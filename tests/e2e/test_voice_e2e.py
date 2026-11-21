"""Voice Cortex end to end in headless Chromium with REAL audio through the real services.

Chromium's fake capture device plays a WAV that Kokoro (the deployed TTS service) spoke, so the microphone path is
real: getUserMedia -> AudioWorklet -> VAD -> uploads -> Whisper (vLLM, 127.0.0.1:18431) -> Cognitive Voice Bridge
-> research path (SearXNG + web + Qwen3 via the inference gateway) -> Kokoro -> WebAudio playback.
Needs: deploy/voice/run.sh start (ASR + TTS), the vLLM model on 127.0.0.1:8811 and SearXNG on 127.0.0.1:8888 for
the research scenario. Each scenario skips with a reason when a service it needs is down. Measured numbers are
written to test-results/voice_uat.json (docs/voice/VOICE_UAT_RESULTS.md quotes them).

No physical microphone, speaker or handset is involved: fake capture has no room acoustics or echo, and headless
playback goes to a null sink (the output level is read from an AnalyserNode on the output path).
"""
from contextlib import closing
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import threading
import time

import pytest
from playwright.sync_api import expect

from neuroscifi.console.server import ConsoleServer
from neuroscifi.instance import store
from neuroscifi.voice import config as vconfig, engines as vengines
from neuroscifi.voice.cli import wer
from neuroscifi.webcortex import state as wstate

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "test-results" / "voice_uat.json"
TOKEN = "voice-e2e-" + "z" * 32
RUN = ROOT / "deploy" / "voice" / "run.sh"
WER_SENTENCES = [
    "Research the latest findings on sleep and memory consolidation.",
    "What is the boiling point of water at the top of Mount Everest?",
    "Show me the evidence for the previous answer.",
    "Explain the difference between weather and climate in simple terms.",
    "How many moons does the planet Jupiter have according to recent sources?",
]


def _port_open(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def engines():
    return vengines.Engines(dict(vconfig.DEFAULTS))


def need_speech():
    h = engines().health()
    if h["asr"]["state"] != "ready" or h["tts"]["state"] != "ready":
        pytest.skip(f"voice services not ready (asr {h['asr']['state']}, tts {h['tts']['state']}); "
                    "run deploy/voice/run.sh start")


def record(key, value):
    RESULTS.parent.mkdir(exist_ok=True)
    data = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}
    data[key] = value
    data["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    RESULTS.write_text(json.dumps(data, indent=2))


def fake_capture_wav(path, parts, rate=48000):
    """parts: list of seconds (silence) or sentences (spoken by the deployed Kokoro service). 16-bit mono WAV."""
    tts = engines().tts
    out = bytearray()
    for part in parts:
        if isinstance(part, (int, float)):
            out += b"\x00\x00" * int(part * rate)
            continue
        if isinstance(part, tuple):                          # ("burst", seconds): a short voice-level sound
            import math
            out += b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 180 * i / rate)))
                            for i in range(int(part[1] * rate)))
            continue
        wav = tts.synthesize(part, "am_michael")["wav"]
        src = struct.unpack(f"<{(len(wav) - 44) // 2}h", wav[44:])
        n = int(len(src) * rate / 24000)
        for i in range(n):                                   # 24 kHz -> 48 kHz, linear interpolation
            x = i * 24000 / rate
            j = int(x)
            a, b = src[min(j, len(src) - 1)], src[min(j + 1, len(src) - 1)]
            out += struct.pack("<h", int(a + (b - a) * (x - j)))
    header = (b"RIFF" + struct.pack("<I", 36 + len(out)) + b"WAVEfmt " +
              struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" + struct.pack("<I", len(out)))
    Path(path).write_bytes(header + bytes(out))
    return path


class Console:
    def __init__(self, folder, *, web=False, llm=False):
        self.path = Path(folder) / "owner.sqlite"
        with closing(store.create(self.path, time.time())) as db:
            changes = {"min_domain_interval": 1.0}
            if web:
                changes.update(enabled=True, searxng_url="http://127.0.0.1:8888")
            wstate.set_config(db, changes, time.time())
            if llm:
                from neuroscifi.inference import config as iconfig
                iconfig.set_config(db, {"base_url": "http://127.0.0.1:8811/v1",
                                        "model": "qwen3-30b-a3b-instruct-2507-fp8"}, time.time())
        self.server = ConsoleServer(self.path, 0, token=TOKEN, web_worker=web)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = self.server.url

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def launch(browser, tmp_path):
    """launch(wav) -> page on a fresh Chromium whose fake microphone plays `wav`."""
    opened = []

    def make(wav, console):
        b = browser.browser_type.launch(executable_path=os.environ.get("NSF_CHROMIUM") or None, args=[
            "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
            f"--use-file-for-fake-audio-capture={wav}", "--autoplay-policy=no-user-gesture-required"])
        opened.append(b)
        ctx = b.new_context(viewport={"width": 1280, "height": 900}, permissions=["microphone"])
        page = ctx.new_page()
        page.problems = []
        page.on("pageerror", lambda e: page.problems.append(str(e)))
        page.audio_posts = []
        page.on("request", lambda r: page.audio_posts.append(time.monotonic()) if r.url.endswith("/api/voice/audio")
                else None)
        page.goto(console.url)
        page.locator("#nav").get_by_role("link", name="Voice").click()
        expect(page.locator("#voice .nsfv")).to_be_visible()
        expect(page.locator(".nsfv-health")).to_contain_text("Speech recognition: ready", timeout=15_000)
        return page

    yield make
    for b in opened:
        b.close()


def start_session(page, mode="voice_text", mic=True):
    page.get_by_role("button", name="Start voice session").click()
    expect(page.get_by_role("button", name="End voice session")).to_be_visible()
    page.locator(f".nsfv-modes input[value='{mode}']").check()
    expect(page.locator(f"[data-mode='{mode}']")).to_have_count(1)
    if mic:
        page.get_by_role("button", name="Turn microphone on").click()
        expect(page.locator(".nsfv-consent")).to_be_visible()
        page.get_by_role("button", name="Allow microphone").click()
        expect(page.locator(".nsfv-indicator")).to_contain_text("Microphone on")


def metrics(page):
    return page.evaluate("() => JSON.parse(JSON.stringify(window.__nsfVoice.client.metrics))")


def finals(page):
    return page.evaluate("() => [...document.querySelectorAll('.nsfv-user.nsfv-final .nsfv-text')]"
                         ".map(e => e.textContent)")


def test_voice_wer_and_mic_off_stops_capture(launch, tmp_path):
    """Five spoken sentences through real capture + Whisper in mode B (text out), then mic off: the track stops and
    no audio request is sent while the fake device keeps 'speaking'."""
    need_speech()
    wav = fake_capture_wav(tmp_path / "wer.wav", [1.0, *[x for s in WER_SENTENCES for x in (s, 2.2)], 1.0])
    console = Console(tmp_path)          # web access off: questions get a fast "not available" reply
    try:
        page = launch(wav, console)
        start_session(page, "voice_in_text_out")
        expect(page.locator(".nsfv-user.nsfv-final")).to_have_count(len(WER_SENTENCES), timeout=60_000)
        heard = finals(page)[:len(WER_SENTENCES)]
        rows = [{"ref": r, "hyp": h, "wer": round(wer(r, h), 4)} for r, h in zip(WER_SENTENCES, heard)]
        ref_words = sum(len(r.split()) for r in WER_SENTENCES)
        errors = sum(wer(r, h) * len(r.split()) for r, h in zip(WER_SENTENCES, heard))
        corpus = errors / ref_words
        m = metrics(page)
        eou_to_final = [round(t["final_ack"] - t["eou"]) for t in m["turns"] if t.get("final_ack")]
        # mode B: no speech output at all
        assert page.evaluate("() => window.__nsfVoice.client.metrics.replies.every(r => !r.first_audio)")
        # mic off in the middle of an utterance: the file loops, so the device keeps 'speaking' afterwards
        mark = len(page.audio_posts)
        deadline = time.monotonic() + 30
        while len(page.audio_posts) <= mark and time.monotonic() < deadline:
            page.wait_for_timeout(50)
        assert len(page.audio_posts) > mark, "the looping file should start a new utterance"
        before = len(page.audio_posts)
        page.get_by_role("button", name="Turn microphone off").click()
        expect(page.locator(".nsfv-indicator")).to_have_text("Microphone off")
        live = page.evaluate("() => window.__nsfVoice.client.tracksLive()")
        stream = page.evaluate("() => window.__nsfVoice.client.stream === null")
        time.sleep(1.0)
        settled = len(page.audio_posts)
        time.sleep(8.0)
        after = len(page.audio_posts)
        record("wer", {"sentences": rows, "corpus_wer": round(corpus, 4), "eou_to_final_transcript_ms": eou_to_final,
                       "voice": "am_michael (Kokoro) through Chromium fake capture, 48 kHz file"})
        record("mic_off", {"uploads_before_off": before, "uploads_within_1s_after_off": settled - before,
                           "uploads_in_next_8s": after - settled, "live_tracks_after_off": live,
                           "stream_released": stream,
                           "note": "mic turned off while an utterance was being uploaded; the fake device kept "
                                   "playing speech for the next 8 s"})
        assert live == 0 and stream and after == settled
        assert corpus <= 0.1, rows
        assert not page.problems, page.problems
    finally:
        console.close()


def test_voice_research_question_real_asr_llm_tts_latency(launch, tmp_path):
    """A spoken question -> real research (SearXNG + web + Qwen3 reasoning + guard) -> spoken answer.
    Measures end-of-utterance -> first audio (acknowledgement) and -> answer audio. Then switches A/B/C repeatedly
    and checks the conversation context survives ('show the evidence' still refers to the same task)."""
    need_speech()
    if not (_port_open(8811) and _port_open(8888)):
        pytest.skip("needs vLLM (127.0.0.1:8811) and SearXNG (127.0.0.1:8888)")
    question = "When was the Python programming language first released?"
    wav = fake_capture_wav(tmp_path / "ask.wav", [1.0, question, 120.0])
    console = Console(tmp_path, web=True, llm=True)
    try:
        page = launch(wav, console)
        start_session(page, "voice_text")
        expect(page.locator(".nsfv-user.nsfv-final")).to_have_count(1, timeout=30_000)
        page.get_by_role("button", name="Turn microphone off").click()      # one question only (the file loops)
        heard = finals(page)[0]
        expect(page.locator(".nsfv-neuroscifi").first).to_contain_text("researching", timeout=15_000)
        page.wait_for_function("() => window.__nsfVoice.client.metrics.replies.some(r => r.intent === "
                               "'research_result' && r.first_audio)", timeout=300_000)
        m = metrics(page)
        turn = m["turns"][0]
        ack = next(r for r in m["replies"] if r["intent"] == "research")
        answer = next(r for r in m["replies"] if r["intent"] == "research_result")
        answer_text = page.locator(".nsfv-neuroscifi").last.inner_text()
        label = page.locator(".nsfv-neuroscifi").last.locator(".nsfv-badge").first.inner_text()
        record("research_turn", {
            "question": question, "heard": heard, "wer": round(wer(question, heard), 4),
            "eou_to_final_transcript_ms": round(turn["final_ack"] - turn["eou"]),
            "eou_to_ack_first_audio_ms": round(ack["first_audio"] - turn["eou"]),
            "eou_to_answer_first_audio_ms": round(answer["first_audio"] - turn["eou"]),
            "answer_source": answer["source"], "answer_label": label, "answer_text": answer_text[:600],
            "note": "eou = client VAD end-of-utterance (700 ms after the last voiced frame)"})
        assert ack["first_audio"] - turn["eou"] < 5000
        # mode switching preserves context
        for mode in ("voice_in_text_out", "text_only", "voice_text", "text_only", "voice_in_text_out", "voice_text"):
            page.locator(f".nsfv-modes input[value='{mode}']").check()
            expect(page.locator(f"[data-mode='{mode}']")).to_have_count(1)
            if mode == "text_only":
                expect(page.get_by_role("button", name="Turn microphone on")).to_be_disabled()
        page.locator("#nsfvText").fill("show the evidence")
        page.get_by_role("button", name="Send").click()
        expect(page.locator(".nsfv-neuroscifi").last).to_contain_text("Evidence for: " + heard[:20], timeout=15_000)
        assert not page.problems, page.problems
    finally:
        console.close()


def test_voice_barge_in_stops_playback(launch, tmp_path):
    """While NeuroSciFi speaks a long reply, a 150 ms burst (backchannel-length overlap) does not interrupt; then the
    user says 'Please stop.': playback stops on the client, the server marks the reply interrupted, and the
    utterance itself is handled as the stop command."""
    need_speech()
    wav = fake_capture_wav(tmp_path / "barge.wav", [1.0, "What can you do?", 2.6, ("burst", 0.15), 2.4,
                                                    "Please stop.", 40.0])
    console = Console(tmp_path)
    try:
        page = launch(wav, console)
        start_session(page, "voice_text")
        page.wait_for_function("() => window.__nsfVoice.client.metrics.bargeIns.length > 0", timeout=40_000)
        page.wait_for_function("() => document.querySelector('.nsfv-neuroscifi.nsfv-interrupted')", timeout=10_000)
        expect(page.locator(".nsfv-user.nsfv-final")).to_have_count(2, timeout=20_000)
        page.get_by_role("button", name="Turn microphone off").click()
        m = metrics(page)
        b = m["bargeIns"][0]
        out_level = page.evaluate("() => { const c = window.__nsfVoice.client; return c.speaking; }")
        status = page.evaluate("async () => (await window.__nsfVoice.client.status()).metrics")
        heard = finals(page)
        overlaps = page.evaluate("() => window.__nsfVoice.client.stats.overlaps")
        record("barge_in", {"onset_to_stop_ms": round(b["stop_ms"], 1), "brief_overlaps_ignored": overlaps,
                            "detect_to_stop_ms": round(b["after_detect_ms"], 2),
                            "hold_before_detect_ms": 300, "server": status, "heard": heard,
                            "note": "onset = first frame above the barge threshold; includes the 300 ms hold"})
        assert out_level is False and b["stop_ms"] < 1000
        assert overlaps == 1 and status["barge_ins"] == 1          # the 150 ms burst did not interrupt
        assert any(re.search(r"stop", h, re.I) for h in heard[1:])
        assert not page.problems, page.problems
    finally:
        console.close()


@pytest.mark.skipif(os.environ.get("NSF_VOICE_DEGRADED") != "1",
                    reason="stops and restarts the deployed speech containers (~2 min); set NSF_VOICE_DEGRADED=1")
def test_voice_degraded_text_only_still_works(launch, tmp_path):
    need_speech()
    wav = fake_capture_wav(tmp_path / "quiet.wav", [5.0])
    console = Console(tmp_path)
    try:
        page = launch(wav, console)
        subprocess.run(["bash", str(RUN), "stop", "all"], check=True, capture_output=True)
        page.evaluate("() => window.__nsfVoice.refreshStatus()")
        expect(page.locator(".nsfv-health")).to_contain_text("Speech recognition: unavailable", timeout=20_000)
        expect(page.locator(".nsfv-health")).to_contain_text("Speech output: unavailable")
        start_session(page, "text_only", mic=False)
        expect(page.get_by_role("button", name="Turn microphone on")).to_be_disabled()
        page.locator("#nsfvText").fill("what can you do")
        page.get_by_role("button", name="Send").click()
        expect(page.locator(".nsfv-neuroscifi").last).to_contain_text("You can ask a question", timeout=10_000)
        page.locator(".nsfv-modes input[value='voice_text']").check()
        page.locator("#nsfvText").fill("help")
        page.get_by_role("button", name="Send").click()
        expect(page.locator(".nsfv-neuroscifi")).to_have_count(2, timeout=10_000)
        expect(page.locator(".nsfv-notice")).to_contain_text("Speech output is unavailable", timeout=10_000)
        status = page.evaluate("async () => (await window.__nsfVoice.client.status()).engines")
        record("degraded", {"asr": status["asr"]["state"], "tts": status["tts"]["state"],
                            "text_reply_ok": True})
        assert not [p for p in page.problems if "speech" not in p.lower()], page.problems
    finally:
        console.close()
        subprocess.run(["bash", str(RUN), "start", "all"], check=True, capture_output=True)
        subprocess.run(["bash", str(RUN), "wait", "all", "600"], check=True, capture_output=True)


def test_voice_pocket_https_capture_and_one_device_at_a_time(launch, browser, tmp_path):
    """A paired phone (Pocket over HTTPS) captures real audio through its paired session; while it holds the
    microphone the owner console's mic-on is refused and offers an explicit hand-off; after the hand-off the phone
    learns it lost the microphone and stops its track."""
    import ssl
    from neuroscifi.pocket import server as pocket
    need_speech()
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-keyout", str(key),
                    "-out", str(cert), "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost"],
                   check=True, capture_output=True)
    console = Console(tmp_path)
    with closing(store.connect(console.path)) as db:
        wstate.set_config(db, {"enabled": True, "pocket_research": True}, time.time())
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    srv = pocket.PocketServer(("127.0.0.1", 0), pocket.Handler, tls=ctx)
    srv.store = pocket.Store(tmp_path / "mobile.sqlite")
    srv.pair_lock = threading.Lock()
    srv.invitation, srv.invitation_deadline = "invite-code", time.monotonic() + 120
    srv.instance_db = str(console.path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    wav = fake_capture_wav(tmp_path / "phone.wav", [1.0, "Show me the evidence please.", 30.0])
    b = browser.browser_type.launch(executable_path=os.environ.get("NSF_CHROMIUM") or None, args=[
        "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--ignore-certificate-errors",
        f"--use-file-for-fake-audio-capture={wav}", "--autoplay-policy=no-user-gesture-required"])
    try:
        phone = b.new_context(viewport={"width": 390, "height": 844}, permissions=["microphone"],
                              ignore_https_errors=True).new_page()
        phone.goto(f"https://localhost:{srv.server_port}/")
        assert phone.evaluate("() => window.isSecureContext")
        phone.locator("#code").fill("invite-code")
        phone.locator("#pairform button").click()
        expect(phone.locator("#navvoice")).to_be_visible(timeout=15_000)
        phone.locator("#navvoice").click()
        root = phone.locator("#voicemount")
        root.get_by_role("button", name="Start voice session").click()
        root.locator(".nsfv-modes input[value='voice_in_text_out']").check()
        root.get_by_role("button", name="Turn microphone on").click()
        root.get_by_role("button", name="Allow microphone").click()
        expect(root.locator(".nsfv-indicator")).to_contain_text("Microphone on")
        expect(root.locator(".nsfv-user.nsfv-final")).to_have_count(1, timeout=30_000)
        heard = root.locator(".nsfv-user.nsfv-final .nsfv-text").inner_text()
        assert phone.evaluate("() => localStorage.length") == 0          # Pocket keeps nothing in browser storage
        # the owner console tries to capture while the phone holds the microphone
        page = launch(wav, console)
        start_session(page, "voice_text", mic=False)
        page.get_by_role("button", name="Turn microphone on").click()
        page.get_by_role("button", name="Allow microphone").click()
        expect(page.locator(".nsfv-handoff")).to_contain_text("in use on Pocket device")
        assert page.evaluate("() => window.__nsfVoice.client.tracksLive()") == 0
        page.get_by_role("button", name="Move the microphone here").click()
        expect(page.locator(".nsfv-confirm")).to_contain_text("Move the microphone to this device?")
        page.locator(".nsfv-confirm").get_by_role("button", name="Confirm").click()
        expect(page.locator(".nsfv-indicator")).to_contain_text("Microphone on")
        expect(root.locator(".nsfv-indicator")).to_have_text("Microphone off", timeout=12_000)   # lease renewal
        assert phone.evaluate("() => window.__nsfVoice.client.tracksLive()") == 0
        expect(phone.locator(".nsfv-notice")).to_contain_text("moved to another device")   # holder stays anonymous
        record("multi_device", {"pocket_https": True, "pocket_heard": heard, "console_refused_while_phone_held": True,
                                "handoff_needed_confirmation": True, "phone_stopped_after_handoff": True})
    finally:
        b.close()
        srv.shutdown()
        srv.server_close()
        console.close()
