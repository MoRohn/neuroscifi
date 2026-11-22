"""Guided voice self-test on the owner's real hardware ("Voice check": console Voice panel, Pocket, and
`nsf voice selftest`). docs/voice/VOICE_VALIDATION.md, "Owner device self-test".

Steps (ids are fixed; each records measured numbers and, where a person must judge, what they answered):
  mic       microphone permission, the capture indicator, the capture settings the browser actually applied
  read      five displayed sentences read aloud: Whisper word error rate and the speech-to-noise ratio of each
  silence   five seconds of silence: the noise floor, and whether the VAD started an utterance anyway
  playback  NeuroSciFi speaks a phrase through the normal output path; the person confirms they heard it
  barge     the person says "stop" while NeuroSciFi talks: detection, onset-to-stop latency, confirmation it stopped
  echo      the person stays silent while NeuroSciFi talks: its own echo must not trigger a barge-in
  micoff    the microphone is turned off: every capture track must have ended (and the indicator gone out)
Audio is never retained: a reading is held in memory only until it is scored and is then dropped, whatever the
retention setting; the evidence JSON keeps numbers and transcripts of the displayed sentences only. Evidence files
are written under <instance directory>/voice-selftest/ (mode 0600).
The VAD constants are read from web/vad.mjs (one source of truth); Vad mirrors vadStep for the terminal self-test
and tests/voice/test_selftest.py checks the two agree frame by frame.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import secrets
import struct
import threading
import time

from ..instance.errors import Conflict, NotFound, ValidationError

SCHEMA = "neuroscifi.voice.selftest/1"
STEPS = ("mic", "read", "silence", "playback", "barge", "echo", "micoff")
SENTENCES = (
    "Research the history of the printing press and summarise it for me.",
    "Show me the evidence behind your last answer.",
    "What was I researching yesterday afternoon?",
    "Stop talking, but keep looking for better sources.",
    "The quick brown fox jumps over the lazy dog near the river bank.",
)
PHRASES = (   # what NeuroSciFi says in the playback (0) and barge-in / echo (1) steps
    "This is NeuroSciFi. If you can hear this sentence clearly, answer yes.",
    "I will keep talking for a little while so that you can test interruptions. Researchers have studied sleep and "
    "memory for more than a century, and most agree that deep sleep helps the brain keep what it learned during the "
    "day. Some studies even suggest that a short nap can help. When you are asked to, say stop, and I should stop "
    "at once. If you were asked to stay silent, just listen until I finish this long sentence.",
)
RATE = 16000
MAX_READ_SECONDS = 15
MAX_RUNS = 4
RUN_TTL = 1800.0
MAX_FIELDS = 24
WER_PASS = 0.25          # a reading passes at or below this word error rate
SNR_PASS_DB = 15.0       # ... and at or above this speech-to-noise ratio
BARGE_PASS_MS = 1500     # onset of "stop" to playback stopped


def _vad_constants() -> dict:
    text = (Path(__file__).resolve().parent / "web" / "vad.mjs").read_text()
    block = text[text.index("export const VAD = Object.freeze({"):]
    block = block[:block.index("});")]
    return {k: float(v) if "." in v else int(v) for k, v in re.findall(r"^\s+([A-Z_]+): ([0-9.]+),", block, re.M)}


VAD = _vad_constants()


class Vad:
    """vad.mjs vadStep in Python (same arithmetic, same order), for the terminal self-test."""

    def __init__(self, params=None):
        self.p = dict(params or VAD)
        self.floor = self.p["FLOOR_INIT"]
        self.voiced = self.silent = 0
        self.active = self.confirmed = self.barge = False
        self.onset = 0.0

    def step(self, rms: float, playing: bool, now: float) -> dict:
        p = self.p
        threshold = max(self.floor * p["SPEECH_K"], p["SPEECH_MIN"])
        barge_threshold = max(self.floor * p["BARGE_K"], p["BARGE_MIN"])
        voiced = rms > (barge_threshold if playing else threshold)
        if not self.active and not voiced:
            self.floor = min(p["FLOOR_MAX"], self.floor * (1 - p["FLOOR_ALPHA"]) + rms * p["FLOOR_ALPHA"])
        out = {"voiced": voiced, "start": False, "barge": False, "confirm": False, "end": False, "confirmed": False,
               "overlap": False}
        if not self.active:
            if voiced:
                self.active, self.confirmed, self.voiced, self.silent = True, False, p["FRAME_MS"], 0
                self.onset, self.barge = now, False
                out["start"] = True
            return out
        if voiced:
            self.voiced += p["FRAME_MS"]
            self.silent = 0
        else:
            self.silent += p["FRAME_MS"]
        if playing and not self.barge and self.voiced >= p["BARGE_MS"]:
            self.barge = out["barge"] = True
        if not self.confirmed and self.voiced >= (p["BARGE_MS"] if playing else p["MIN_SPEECH_MS"]):
            self.confirmed = out["confirm"] = True
        if self.silent >= p["END_SILENCE_MS"]:
            out.update(end=True, confirmed=self.confirmed, overlap=not self.confirmed and self.voiced > 0)
            self.active = self.confirmed = False
            self.voiced = self.silent = 0
        return out


def frames_rms(pcm: bytes) -> list[float]:
    """RMS of each 20 ms frame of 16 kHz PCM16 (as capture-worklet.mjs measures it)."""
    n = len(pcm) // 640
    vals = struct.unpack(f"<{n * 320}h", pcm[:n * 640])
    out = []
    for i in range(n):
        s = sum((v / 32768) ** 2 for v in vals[i * 320:(i + 1) * 320])
        out.append(math.sqrt(s / 320))
    return out


def dbfs(x: float) -> float:
    return round(20 * math.log10(max(x, 1e-6)), 1)


def snr_of(rms: list[float], floor: float | None = None) -> dict:
    """Speech level (95th percentile frame) against the noise floor (the silence step's, else the 10th percentile)."""
    if not rms:
        return {"speech_dbfs": None, "noise_dbfs": None, "snr_db": None}
    srt = sorted(rms)
    speech = srt[min(len(srt) - 1, int(0.95 * len(srt)))]
    noise = floor if floor else srt[int(0.10 * len(srt))]
    return {"speech_dbfs": dbfs(speech), "noise_dbfs": dbfs(noise), "snr_db": round(dbfs(speech) - dbfs(noise), 1)}


def _clean(data, depth=0):
    """Client-reported results: small JSON of numbers, booleans and short strings only."""
    if depth > 2:
        raise ValidationError("result too deep")
    if isinstance(data, dict):
        if len(data) > MAX_FIELDS:
            raise ValidationError("too many result fields")
        return {str(k)[:40]: _clean(v, depth + 1) for k, v in data.items()}
    if isinstance(data, list):
        return [_clean(v, depth + 1) for v in data[:MAX_FIELDS]]
    if data is None or isinstance(data, bool):
        return data
    if isinstance(data, (int, float)):
        return round(float(data), 3) if math.isfinite(data) else None
    if isinstance(data, str):
        return data[:200]
    raise ValidationError("unsupported result value")


def verdict(steps: dict) -> dict:
    """Pass/fail per step from the measured numbers and the person's answers (None = not run)."""
    def read_ok(r):
        rows = r.get("sentences") or []
        return len(rows) == len(SENTENCES) and all(x.get("wer") is not None and x["wer"] <= WER_PASS for x in rows) and \
            all((x.get("snr_db") or 0) >= SNR_PASS_DB for x in rows)
    rules = {
        "mic": lambda r: bool(r.get("granted")) and r.get("live_tracks", 0) > 0 and r.get("indicator_seen") is True,
        "read": read_ok,
        "silence": lambda r: r.get("false_utterances", 1) == 0,
        "playback": lambda r: r.get("heard") is True,
        "barge": lambda r: bool(r.get("detected")) and r.get("onset_to_stop_ms") is not None and
        r["onset_to_stop_ms"] <= BARGE_PASS_MS and
        r.get("stopped_confirmed") is True,
        "echo": lambda r: r.get("false_barge_ins", 1) == 0,
        "micoff": lambda r: r.get("live_tracks_after", 1) == 0 and r.get("capture_stopped") is True and
        r.get("indicator_off") is not False,
    }
    out = {s: (None if s not in steps else bool(rules[s](steps[s]))) for s in STEPS}
    ran = [v for v in out.values() if v is not None]
    return {"steps": out, "passed": sum(ran), "ran": len(ran), "all_passed": len(ran) == len(STEPS) and all(ran)}


def write_evidence(folder: Path, evidence: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    name = time.strftime("selftest-%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{secrets.token_hex(3)}.json"
    path = folder / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(evidence, fh, indent=1)
    return path


class SelfTests:
    """The self-test runs of one hub: in memory, bounded, per principal; audio only until it is scored."""

    def __init__(self, folder: Path, clock=time.time):
        self.folder, self.clock = Path(folder), clock
        self.runs: dict[str, dict] = {}
        self.lock = threading.Lock()

    def _expire(self):
        now = self.clock()
        for rid in [r for r, v in self.runs.items() if now - v["started"] > RUN_TTL]:
            self.runs.pop(rid, None)

    def run(self, principal, rid) -> dict:
        with self.lock:
            self._expire()
            r = self.runs.get(rid) if isinstance(rid, str) else None
        if r is None or r["principal"] != principal.name:
            raise NotFound("no such voice check (it expires after 30 minutes)")
        return r

    def start(self, principal, surface) -> dict:
        if surface not in ("console", "pocket", "cli"):
            raise ValidationError("surface must be console, pocket or cli")
        with self.lock:
            self._expire()
            mine = sorted((v["started"], k) for k, v in self.runs.items() if v["principal"] == principal.name)
            for _, k in mine[:max(0, len(mine) - MAX_RUNS + 1)]:
                self.runs.pop(k, None)
            rid = "vc" + secrets.token_hex(8)
            self.runs[rid] = {"principal": principal.name, "surface": surface, "started": self.clock(), "steps": {},
                              "audio": {}, "floor": None}
        return {"run": rid, "steps": list(STEPS), "sentences": list(SENTENCES), "phrases": len(PHRASES),
                "thresholds": VAD, "pass": {"wer_max": WER_PASS, "snr_min_db": SNR_PASS_DB,
                                            "barge_max_ms": BARGE_PASS_MS}}

    def result(self, principal, rid, step, data) -> dict:
        r = self.run(principal, rid)
        if step not in STEPS or step == "read":
            raise ValidationError("step must be one of " + ", ".join(s for s in STEPS if s != "read"))
        if not isinstance(data, dict):
            raise ValidationError("data must be an object")
        clean = _clean(data)
        with self.lock:
            r["steps"][step] = {**r["steps"].get(step, {}), **clean}
            if step == "silence" and isinstance(clean.get("noise_rms"), float):
                r["floor"] = clean["noise_rms"] or None
        return {"step": step, "passed": verdict(r["steps"])["steps"][step]}

    def audio(self, principal, rid, index, seq, final, pcm: bytes, asr) -> dict:
        """One chunk of reading `index` (0..4); on the final chunk it is transcribed, scored and dropped."""
        r = self.run(principal, rid)
        if not isinstance(index, int) or not 0 <= index < len(SENTENCES):
            raise ValidationError(f"sentence index must be 0..{len(SENTENCES) - 1}")
        if len(pcm) % 2:
            raise ValidationError("audio must be 16-bit PCM")
        with self.lock:
            buf = r["audio"].setdefault(index, {"pcm": bytearray(), "seq": -1})
            if seq != buf["seq"] + 1:
                r["audio"].pop(index, None)
                raise Conflict(f"expected chunk {buf['seq'] + 1}, got {seq}")
            if len(buf["pcm"]) + len(pcm) > MAX_READ_SECONDS * RATE * 2:
                r["audio"].pop(index, None)
                raise ValidationError(f"a reading is at most {MAX_READ_SECONDS} s")
            buf["pcm"] += pcm
            buf["seq"] = seq
            if not final:
                return {"status": "buffered", "seconds": round(len(buf["pcm"]) / RATE / 2, 2)}
            audio = bytes(r["audio"].pop(index)["pcm"])      # the only copy leaves the run here
        try:
            from .cli import wer
            heard = asr.transcribe(audio, RATE)
            row = {"index": index, "sentence": SENTENCES[index], "transcript": heard["text"],
                   "wer": round(wer(SENTENCES[index], heard["text"]), 4), "asr_ms": round(heard["latency"] * 1000, 1),
                   "seconds": round(len(audio) / RATE / 2, 2), **snr_of(frames_rms(audio), r["floor"])}
        finally:
            del audio
        with self.lock:
            rows = [x for x in r["steps"].get("read", {}).get("sentences", []) if x["index"] != index]
            r["steps"]["read"] = {"sentences": sorted(rows + [row], key=lambda x: x["index"])}
        return {"status": "scored", **row}

    def finish(self, principal, rid, device: dict | None) -> dict:
        r = self.run(principal, rid)
        with self.lock:
            self.runs.pop(rid, None)
        evidence = build_evidence(r["surface"], r["steps"], device or {}, principal_label=principal.label)
        path = write_evidence(self.folder, evidence)
        return {"evidence": evidence, "file": path.name}


def build_evidence(surface, steps, device, *, principal_label=None) -> dict:
    return {"schema": SCHEMA, "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "surface": surface,
            "principal": principal_label, "device": _clean(device) if device else {}, "thresholds": VAD,
            "pass_rules": {"wer_max": WER_PASS, "snr_min_db": SNR_PASS_DB, "barge_max_ms": BARGE_PASS_MS},
            "steps": steps, "verdict": verdict(steps), "audio_retained": False,
            "note": "Raw audio was scored in memory and deleted; only these numbers and transcripts of the displayed "
                    "sentences are kept."}


# ---- the terminal self-test (`nsf voice selftest --report PATH`) -------------------------------------------------

def _wav_pcm16(wav: bytes) -> tuple[bytes, int]:
    import io
    import wave
    with wave.open(io.BytesIO(wav)) as w:
        return w.readframes(w.getnframes()), w.getframerate()


class HostAudio:
    """The host's default microphone and speaker through PipeWire/PulseAudio or ALSA command-line tools
    (parecord/arecord to capture 16 kHz mono PCM16, paplay/aplay to play a WAV). Nothing is written to disk."""
    fake = False

    def __init__(self):
        import shutil
        self.rec_cmd = next(([*c] for c in (
            ["parecord", "--raw", "--format=s16le", "--rate=16000", "--channels=1", "--latency-msec=20"],
            ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "raw", "--buffer-time=40000"])
            if shutil.which(c[0])), None)
        self.play_cmd = next(([*c] for c in (["paplay"], ["aplay", "-q", "-"]) if shutil.which(c[0])), None)
        if not self.rec_cmd or not self.play_cmd:
            raise ValidationError("the terminal self-test needs parecord or arecord, and paplay or aplay")
        self.proc = None
        self.label = f"{self.rec_cmd[0]} default source / {self.play_cmd[0]} default sink"

    def open(self):
        import subprocess
        self.proc = subprocess.Popen(self.rec_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)

    def frame(self) -> bytes | None:
        """The next 20 ms (640 bytes), blocking; None once capture has stopped."""
        if self.proc is None or self.proc.stdout is None:
            return None
        buf = b""
        while len(buf) < 640:
            part = self.proc.stdout.read(640 - len(buf))
            if not part:
                return None
            buf += part
        return buf

    def play(self, wav: bytes):
        import subprocess
        p = subprocess.Popen(self.play_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        def feed():
            try:
                p.stdin.write(wav)
                p.stdin.close()
            except OSError:          # the player was stopped by a barge-in before it read everything
                pass
        threading.Thread(target=feed, daemon=True).start()
        return p

    def close(self) -> dict:
        p, self.proc = self.proc, None
        if p is None:
            return {"capture_stopped": True, "live_tracks_after": 0}
        p.terminate()
        try:
            p.wait(timeout=3)
        except Exception:
            p.kill()
            p.wait(timeout=3)
        return {"capture_stopped": p.poll() is not None, "live_tracks_after": 0 if p.poll() is not None else 1,
                "recorder_exit": p.returncode}


class ScriptedAudio:
    """A FAKE device for tests and dry runs: each capture step reads its own scripted PCM (16 kHz mono PCM16) as
    fast as it is consumed; playback is not audible and ends after the phrase's duration in frames."""
    fake = True
    label = "fake device (scripted audio, no microphone or speaker)"

    def __init__(self, script: dict[str, bytes]):
        self.script, self.step, self.pos, self.open_ = script, None, 0, False
        self.playing_frames = 0

    def open(self):
        self.open_ = True

    def select(self, step):
        self.step, self.pos = step, 0

    def frame(self):
        if not self.open_:
            return None
        data = self.script.get(self.step, b"")
        chunk = data[self.pos:self.pos + 640]
        self.pos += 640
        if self.playing_frames:
            self.playing_frames -= 1
        return chunk if len(chunk) == 640 else b"\x00" * 640

    def play(self, wav: bytes):
        pcm, rate = _wav_pcm16(wav)
        self.playing_frames = max(1, int(len(pcm) / 2 / rate * 50))
        outer = self

        class Handle:
            def poll(self):
                return None if outer.playing_frames > 0 else 0

            def terminate(self):
                outer.playing_frames = 0

            kill = terminate
        return Handle()

    def close(self):
        self.open_ = False
        return {"capture_stopped": True, "live_tracks_after": 0}


def run_terminal(engines, audio, ask, say, *, voice="af_heart", speed=1.0) -> dict:
    """The guided steps in a terminal; returns the evidence (also its verdict). ask(question) -> bool."""
    steps, vad, floor = {}, Vad(), None
    ticks = [0]          # a fake device delivers frames faster than real time: its clock counts frames
    clock = (lambda: ticks[0] * 0.02) if audio.fake else time.monotonic
    select = getattr(audio, "select", lambda step: None)

    def frames(seconds, playing=lambda: False, until=None):
        t0 = clock()
        n = 0
        while n < seconds * 50:
            pcm = audio.frame()
            if pcm is None:
                return
            ticks[0] += 1
            rms = frames_rms(pcm)[0]
            ev = vad.step(rms, playing(), clock() * 1000)
            n += 1
            yield pcm, rms, ev
            if until and until():
                return
            if not audio.fake and clock() - t0 > seconds + 2:
                return

    say("Voice check on this computer's default microphone and speaker. Nothing is recorded to disk; readings are "
        "scored by the local speech recognition and dropped.")
    select("mic")
    audio.open()
    first = audio.frame()
    steps["mic"] = {"granted": first is not None, "live_tracks": 1 if first is not None else 0,
                    "label": audio.label, "fake": audio.fake}
    steps["mic"]["indicator_seen"] = ask("Microphone on. Do you see your desktop's microphone indicator?")
    for _ in frames(1.0):     # settle the noise floor
        pass
    rows = []
    for i, sentence in enumerate(SENTENCES):
        select(f"read{i}")
        say(f"Read aloud ({i + 1}/{len(SENTENCES)}): {sentence}")
        pre, utt = [], None
        for pcm, rms, ev in frames(MAX_READ_SECONDS):
            if utt is None:
                pre = (pre + [pcm])[-(VAD["PREROLL_MS"] // VAD["FRAME_MS"]):]
                if ev["start"]:
                    utt = list(pre)
                continue
            utt.append(pcm)
            if ev["end"]:
                if ev["confirmed"]:
                    break
                utt, pre = None, []
        if not utt:
            rows.append({"index": i, "sentence": sentence, "transcript": None, "wer": None, "status": "nothing_heard"})
            say("  nothing heard")
            continue
        audio_bytes = b"".join(utt)
        from .cli import wer
        heard = engines.asr.transcribe(audio_bytes, RATE)
        row = {"index": i, "sentence": sentence, "transcript": heard["text"],
               "wer": round(wer(sentence, heard["text"]), 4), "seconds": round(len(audio_bytes) / RATE / 2, 2),
               **snr_of(frames_rms(audio_bytes), floor)}
        del audio_bytes, utt
        rows.append(row)
        say(f"  heard: {row['transcript']!r}  WER {row['wer']:.0%}  SNR {row['snr_db']} dB")
    steps["read"] = {"sentences": rows}
    select("silence")
    say("Stay silent for five seconds…")
    vals, false_utt = [], 0
    for _, rms, ev in frames(5.0):
        vals.append(rms)
        false_utt += ev["confirm"]
    srt = sorted(vals) or [0.0]
    floor = srt[len(srt) // 2]
    steps["silence"] = {"noise_dbfs": dbfs(floor), "noise_p95_dbfs": dbfs(srt[int(0.95 * (len(srt) - 1))]),
                        "false_utterances": false_utt, "seconds": round(len(vals) / 50, 1)}
    say(f"  noise floor {dbfs(floor)} dBFS, {false_utt} false utterance(s)")
    tts = engines.tts
    phrase0 = tts.synthesize(PHRASES[0], voice, speed)["wav"]
    phrase1 = tts.synthesize(PHRASES[1], voice, speed)["wav"]
    select("playback")
    say("Playing a sentence through the speaker…")
    p = audio.play(phrase0)
    for _ in frames(30, until=lambda: p.poll() is not None):
        pass
    steps["playback"] = {"heard": ask("Did you hear the sentence clearly?")}

    def talk(step, prompt, stop_on_barge):
        select(step)
        say(prompt)
        handle = audio.play(phrase1)
        alive = lambda: handle.poll() is None        # noqa: E731
        barges, detected, onset, stopped, levels = 0, None, None, None, []
        for _, rms, ev in frames(40, playing=alive, until=lambda: not alive()):
            levels.append(rms)
            if ev["barge"]:
                barges += 1
                if stop_on_barge and detected is None:
                    detected, onset = clock() * 1000, vad.onset
                    handle.terminate()
                    stopped = clock() * 1000
        if alive():
            handle.terminate()
        levels.sort()
        return barges, detected, onset, stopped, levels
    b, det, on, st, _ = talk("barge", "NeuroSciFi will talk. After a few words, say \"stop\".", True)
    steps["barge"] = {"detected": det is not None, "onset_to_stop_ms": round(st - on) if det else None}
    say("  interruption detected, stopped after %s ms" % steps["barge"]["onset_to_stop_ms"] if det else
        "  no interruption detected")
    steps["barge"]["stopped_confirmed"] = ask("Did it stop talking when you said stop?")
    b, _, _, _, lv = talk("echo", "NeuroSciFi will talk again. Stay silent until it finishes.", False)
    steps["echo"] = {"false_barge_ins": b, "echo_median_dbfs": dbfs(lv[len(lv) // 2] if lv else 0),
                     "echo_peak_dbfs": dbfs(lv[-1] if lv else 0)}
    say(f"  {b} false interruption(s) from its own voice")
    select("micoff")
    off = audio.close()
    steps["micoff"] = {**off, "frames_after_close": 0 if audio.frame() is None else 1}
    steps["micoff"]["indicator_off"] = ask("Microphone off. Has the microphone indicator gone out?")
    device = {"surface": "cli", "audio": audio.label, "fake": audio.fake}
    try:
        import platform
        device.update(platform=platform.platform()[:80], machine=platform.machine())
    except Exception:
        pass
    return build_evidence("cli", steps, device, principal_label="Owner (terminal)")
