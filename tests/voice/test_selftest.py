"""Voice check (owner device self-test): the server flow, the terminal flow on a FAKE device and the Python mirror of
the product VAD. No speech service is needed (scripted engines)."""
import json
import math
from pathlib import Path
import random
import shutil
import stat
import struct
import subprocess

import pytest

from neuroscifi.instance.errors import NotFound, ValidationError
from neuroscifi.voice import http as vhttp, selftest
from neuroscifi.voice.engines import wav

from .conftest import OWNER, FakeEngines, pocket_principal

ROOT = Path(__file__).resolve().parents[2]
VAD_JS = ROOT / "src" / "neuroscifi" / "voice" / "web" / "vad.mjs"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def tone(seconds, amplitude=0.1, freq=220, rate=16000):
    return b"".join(struct.pack("<h", int(32767 * amplitude * math.sin(2 * math.pi * freq * i / rate)))
                    for i in range(int(seconds * rate)))


def silence(seconds, rate=16000):
    return b"\x00\x00" * int(seconds * rate)


# ---- VAD: one source of truth, two implementations that agree ------------------------------------------------------

@needs_node
def test_python_vad_constants_are_read_from_vad_mjs():
    out = subprocess.run(["node", "--input-type=module", "-e",
                          f"import {{ VAD }} from {json.dumps(VAD_JS.as_uri())}; console.log(JSON.stringify(VAD));"],
                         capture_output=True, text=True, timeout=60, check=True)
    assert json.loads(out.stdout) == selftest.VAD


@needs_node
def test_python_vad_matches_vad_mjs_frame_by_frame():
    rng = random.Random(7)
    seqs = []
    for k in range(40):
        rms, playing, level = [], [], rng.choice([0.002, 0.01, 0.03])
        for i in range(600):
            burst = (i // rng.randint(20, 60)) % 2 == 0
            rms.append(round(abs(rng.gauss(level * (8 if burst else 1), level)), 6))
            playing.append(1 if (k % 3 == 0 and 100 < i < 400) else 0)
        seqs.append({"id": str(k), "rms": rms, "playing": playing})
    out = subprocess.run(["node", str(ROOT / "scripts" / "voice_vad_replay.mjs")],
                         input=json.dumps({"sequences": seqs}), capture_output=True, text=True, timeout=120, check=True)
    js = json.loads(out.stdout)["results"][0]
    for seq, res in zip(seqs, js):
        v, events = selftest.Vad(), []
        for i, (r, p) in enumerate(zip(seq["rms"], seq["playing"])):
            ev = v.step(r, bool(p), i * 20)
            events += [[i, k, False] for k in ("start", "barge", "confirm") if ev[k]]
            if ev["end"]:
                events.append([i, "end", ev["confirmed"]])
        assert events == res["events"], seq["id"]


# ---- server flow ------------------------------------------------------------------------------------------------

def test_voice_check_server_flow_scores_and_drops_audio(hub, instance):
    asr = hub.fake.asr
    run = hub.selftest(OWNER, "start", {"surface": "console"})
    assert run["sentences"] == list(selftest.SENTENCES) and run["thresholds"] == selftest.VAD
    rid = run["run"]
    hub.selftest(OWNER, "result", {"run": rid, "step": "mic", "data": {"granted": True, "live_tracks": 1,
                                                                        "indicator_seen": True, "label": "Mic"}})
    hub.selftest(OWNER, "result", {"run": rid, "step": "silence", "data": {"noise_rms": 0.001, "noise_dbfs": -60,
                                                                            "false_utterances": 0}})
    for i, sentence in enumerate(selftest.SENTENCES):
        asr.script = [sentence]
        speech = silence(0.3) + tone(1.6, 0.1) + silence(0.3)
        half = len(speech) // 2 // 2 * 2
        assert hub.selftest_audio(OWNER, rid, i, 0, False, speech[:half])["status"] == "buffered"
        scored = hub.selftest_audio(OWNER, rid, i, 1, True, speech[half:])
        assert scored["status"] == "scored" and scored["wer"] == 0.0 and scored["snr_db"] > 30
        assert hub.selftests.runs[rid]["audio"] == {}               # the reading left memory once scored
    for step, data in (("playback", {"heard": True}), ("barge", {"detected": True, "onset_to_stop_ms": 420,
                                                                "stopped_confirmed": True}),
                       ("echo", {"false_barge_ins": 0}),
                       ("micoff", {"live_tracks_after": 0, "capture_stopped": True, "indicator_off": True})):
        assert hub.selftest(OWNER, "result", {"run": rid, "step": step, "data": data})["passed"] is True
    done = hub.selftest(OWNER, "finish", {"run": rid, "data": {"surface": "console", "user_agent": "UA"}})
    ev = done["evidence"]
    assert ev["verdict"]["all_passed"] and ev["audio_retained"] is False and ev["schema"] == selftest.SCHEMA
    path = Path(instance).parent / "voice-selftest" / done["file"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    saved = json.loads(path.read_text())
    assert saved["steps"]["read"]["sentences"][4]["transcript"] == selftest.SENTENCES[4]
    assert "pcm" not in path.read_text() and rid not in hub.selftests.runs
    with pytest.raises(NotFound):
        hub.selftest(OWNER, "result", {"run": rid, "step": "echo", "data": {}})


def test_voice_check_runs_are_per_principal_and_bounded(hub):
    rid = hub.selftest(OWNER, "start", {})["run"]
    pocket = pocket_principal()
    with pytest.raises(NotFound):
        hub.selftest(pocket, "result", {"run": rid, "step": "mic", "data": {}})
    with pytest.raises(NotFound):
        hub.selftest_audio(pocket, rid, 0, 0, True, silence(0.5))
    assert hub.selftest(pocket, "start", {})["run"] != rid
    for _ in range(selftest.MAX_RUNS + 2):
        hub.selftest(OWNER, "start", {})
    assert sum(1 for r in hub.selftests.runs.values() if r["principal"] == "owner") == selftest.MAX_RUNS
    with pytest.raises(ValidationError):
        hub.selftest(OWNER, "start", {"surface": "radio"})
    rid = hub.selftest(OWNER, "start", {})["run"]
    with pytest.raises(ValidationError):
        hub.selftest(OWNER, "result", {"run": rid, "step": "read", "data": {}})        # readings come from audio
    with pytest.raises(ValidationError):
        hub.selftest(OWNER, "result", {"run": rid, "step": "mic", "data": {"x": {"y": {"z": {"w": 1}}}}})
    with pytest.raises(ValidationError):
        hub.selftest_audio(OWNER, rid, 0, 0, False, silence(selftest.MAX_READ_SECONDS + 1))


def test_voice_check_routes_and_speech(hub):
    status, body, _ = vhttp.run(hub, OWNER, "selftest", {"action": "start"})
    rid = body["run"]
    status, data, mime = vhttp.run(hub, OWNER, "selftest_speech", {"run": rid, "n": 1})
    assert status == 200 and mime == "audio/wav" and data[:4] == b"RIFF"
    assert hub.fake.tts.spoken[-1] == selftest.PHRASES[1]
    hub.fake.asr.script = [selftest.SENTENCES[0]]
    status, out, _ = vhttp.run(hub, OWNER, "selftest_audio", {}, audio=tone(1.0),
                               headers={"X-NSF-Selftest": rid, "X-NSF-Index": "0", "X-NSF-Seq": "0",
                                        "X-NSF-Final": "1"})
    assert out["status"] == "scored"
    with pytest.raises(ValidationError):
        vhttp.run(hub, OWNER, "selftest", {"action": "start", "surface": {"x": 1}})
    assert {"selftest", "selftest_audio", "selftest_speech"} <= vhttp.POCKET_OPS
    assert "selftest_speech" in vhttp.CONSOLE_GET and {"selftest", "selftest_audio"} <= vhttp.CONSOLE_POST


def test_verdict_rules():
    good = {"mic": {"granted": True, "live_tracks": 1, "indicator_seen": True},
            "read": {"sentences": [{"wer": 0.1, "snr_db": 30}] * len(selftest.SENTENCES)},
            "silence": {"false_utterances": 0}, "playback": {"heard": True},
            "barge": {"detected": True, "onset_to_stop_ms": 500, "stopped_confirmed": True},
            "echo": {"false_barge_ins": 0}, "micoff": {"live_tracks_after": 0, "capture_stopped": True}}
    assert selftest.verdict(good)["all_passed"]
    for step, bad in (("read", {"sentences": [{"wer": 0.1, "snr_db": 30}] * 4}),
                      ("read", {"sentences": [{"wer": 0.5, "snr_db": 30}] * 5}),
                      ("read", {"sentences": [{"wer": 0.0, "snr_db": 8}] * 5}),
                      ("barge", {"detected": True, "onset_to_stop_ms": 2500, "stopped_confirmed": True}),
                      ("echo", {"false_barge_ins": 1}), ("micoff", {"live_tracks_after": 1, "capture_stopped": False}),
                      ("mic", {"granted": True, "live_tracks": 1, "indicator_seen": False})):
        v = selftest.verdict({**good, step: bad})
        assert v["steps"][step] is False and not v["all_passed"]
    assert selftest.verdict({"mic": good["mic"]})["ran"] == 1


# ---- the terminal flow on a FAKE device ------------------------------------------------------------------------

def test_terminal_self_test_on_a_fake_device(tmp_path):
    FakeEngines.instance = None
    eng = FakeEngines({})
    eng.tts.synthesize = lambda text, voice, speed=1.0: {"wav": wav(silence(3.0), 16000), "latency": 0.01}
    readings = iter(selftest.SENTENCES)
    eng.asr.transcribe = lambda pcm, rate=16000: {"text": next(readings), "latency": 0.02}
    script = {f"read{i}": silence(0.6) + tone(1.5, 0.1) + silence(1.2) for i in range(len(selftest.SENTENCES))}
    script.update(mic=silence(1.5), silence=silence(0.2) + tone(0.0) + bytes(320 * 2 * 5),
                  barge=silence(1.0) + tone(0.6, 0.2) + silence(1.0), echo=silence(4.0), playback=silence(4.0))
    asked = []
    ev = selftest.run_terminal(eng, selftest.ScriptedAudio(script), lambda q: asked.append(q) or True,
                               lambda text: None)
    v = ev["verdict"]
    assert v["all_passed"], ev["steps"]
    assert ev["device"]["fake"] is True and ev["audio_retained"] is False
    assert ev["steps"]["barge"]["detected"] and ev["steps"]["barge"]["onset_to_stop_ms"] is not None
    assert ev["steps"]["echo"]["false_barge_ins"] == 0 and ev["steps"]["micoff"]["frames_after_close"] == 0
    assert len(asked) == 4 and all(r["wer"] == 0.0 for r in ev["steps"]["read"]["sentences"])


def test_cli_self_test_writes_the_report_with_a_fake_device(tmp_path, monkeypatch, instance, capsys):
    from neuroscifi.voice import cli, engines as vengines
    FakeEngines.instance = None
    fake = FakeEngines({})
    fake.tts.synthesize = lambda text, voice, speed=1.0: {"wav": wav(silence(1.0), 16000), "latency": 0.01}
    monkeypatch.setattr(vengines, "Engines", lambda cfg: fake)
    src = tmp_path / "fake.wav"
    src.write_bytes(wav(silence(0.5) + tone(1.2, 0.1) + silence(1.5), 16000))
    report = tmp_path / "report.json"

    class Args:
        db = str(instance)
        fake_input = str(src)
        yes = True
    Args.report = str(report)
    out = cli._selftest(Args)
    data = json.loads(report.read_text())
    assert out["fake_device"] is True and data["surface"] == "cli" and data["device"]["fake"] is True
    assert (Path(instance).parent / "voice-selftest" / out["saved"]).exists()
    assert set(data["steps"]) == set(selftest.STEPS)
