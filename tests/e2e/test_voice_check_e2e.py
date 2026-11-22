"""Voice check (owner device self-test) end to end in headless Chromium, on a FAKE DEVICE.

FAKE DEVICE: getUserMedia is replaced (page init script) by a scripted WebAudio stream. The test plays Kokoro speech
of each displayed sentence, and the word "stop", into it when the check asks; there is no physical microphone,
speaker, room or echo path, and playback goes to a null sink, so the speaker and echo steps only prove the flow
(the person's answers are clicked by the test). Everything else is real: the panel, the capture worklet, the VAD
(vad.mjs), uploads, Whisper scoring (127.0.0.1:18431), Kokoro phrases (127.0.0.1:18432), the evidence file and its
contents. Skips when the speech services are down. Measured numbers go to test-results/voice_uat.json.
"""
import base64
import json
from pathlib import Path
import struct

from playwright.sync_api import expect

from test_voice_e2e import Console, engines, need_speech, record

FAKE_MIC = """
(() => {
  const clips = {};
  let ctx = null, dest = null;
  window.__fakeMic = {
    label: "FAKE DEVICE (scripted WebAudio stream)",
    calls: 0,
    load(name, b64) { clips[name] = b64; },
    play(name) {
      const bin = atob(clips[name]);
      const n = bin.length / 2;
      const buf = ctx.createBuffer(1, n, 16000);
      const ch = buf.getChannelData(0);
      for (let i = 0; i < n; i++) {
        let v = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8);
        if (v >= 32768) v -= 65536;
        ch[i] = v / 32768;
      }
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(dest);
      src.start();
      return buf.duration;
    },
  };
  navigator.mediaDevices.getUserMedia = async () => {
    ctx = ctx || new AudioContext({ sampleRate: 48000 });
    await ctx.resume();
    dest = ctx.createMediaStreamDestination();
    const noise = ctx.createBuffer(1, 48000, 48000);
    const d = noise.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * 0.0005;   // a faint floor, about -72 dBFS
    const ns = ctx.createBufferSource();
    ns.buffer = noise;
    ns.loop = true;
    ns.connect(dest);
    ns.start();
    window.__fakeMic.calls++;
    return dest.stream;
  };
})();
"""


def pcm16k(text, voice="am_michael"):
    """Kokoro speech (24 kHz) as 16 kHz PCM16 with 0.3 s of silence around it, base64."""
    raw = engines().tts.synthesize(text, voice)["wav"]
    src = struct.unpack(f"<{(len(raw) - 44) // 2}h", raw[44:])
    out = [src[min(len(src) - 1, int(i * 1.5))] for i in range(int(len(src) / 1.5))]
    pad = [0] * 4800
    return base64.b64encode(struct.pack(f"<{len(out) + 9600}h", *pad, *out, *pad)).decode()


def step(page, sid):
    return page.locator(f".nsfv-vcheck-step[data-step='{sid}']")


def run(page, sid, title):
    page.get_by_role("button", name=f"Run: {title}", exact=True).click()


def answer_yes(page, sid):
    box = step(page, sid).locator(".nsfv-vcheck-ask")
    expect(box).to_be_visible(timeout=60_000)
    box.get_by_role("button", name="Yes").click()


def drive_check(page, folder, surface):
    """Run every step of the Voice check on the fake device; return the evidence file's JSON."""
    from neuroscifi.voice import selftest
    for i, sentence in enumerate(selftest.SENTENCES):
        page.evaluate("([n, b]) => window.__fakeMic.load(n, b)", [f"s{i}", pcm16k(sentence)])
    page.evaluate("([n, b]) => window.__fakeMic.load(n, b)", ["stop", pcm16k("Stop! Please stop.")])
    page.get_by_text("Voice check (microphone, speaker, interruptions)").click()
    page.get_by_role("button", name="Start voice check").click()
    expect(page.locator(".nsfv-vcheck-status")).to_contain_text("Voice check started")

    run(page, "mic", "Microphone")
    answer_yes(page, "mic")
    expect(step(page, "mic")).to_have_attribute("data-state", "passed")
    assert page.evaluate("() => window.__fakeMic.calls") == 1

    run(page, "read", "Read five sentences")
    for i in range(len(selftest.SENTENCES)):
        expect(step(page, "read")).to_have_attribute("data-reading", str(i), timeout=60_000)
        expect(step(page, "read").locator(".nsfv-vcheck-prompt")).to_contain_text(selftest.SENTENCES[i])
        page.wait_for_timeout(400)
        page.evaluate("(n) => window.__fakeMic.play(n)", f"s{i}")
    expect(step(page, "read")).to_have_attribute("data-state", "passed", timeout=90_000)

    run(page, "silence", "Silence")
    expect(step(page, "silence")).to_have_attribute("data-state", "passed", timeout=20_000)

    run(page, "playback", "Speaker")
    answer_yes(page, "playback")
    expect(step(page, "playback")).to_have_attribute("data-state", "passed")

    run(page, "barge", "Interrupt")
    expect(step(page, "barge").locator(".nsfv-vcheck-prompt")).to_contain_text("say “stop”", timeout=30_000)
    page.wait_for_timeout(2500)              # NeuroSciFi is talking now (phrase fetch + decode + a few words)
    page.evaluate("() => window.__fakeMic.play('stop')")
    answer_yes(page, "barge")
    expect(step(page, "barge")).to_have_attribute("data-state", "passed")

    run(page, "echo", "Echo")
    expect(step(page, "echo")).to_have_attribute("data-state", "passed", timeout=90_000)

    run(page, "micoff", "Microphone off")
    answer_yes(page, "micoff")
    expect(step(page, "micoff")).to_have_attribute("data-state", "passed")

    expect(page.locator(".nsfv-vcheck-verdict")).to_contain_text("7 of 7 steps passed", timeout=30_000)
    expect(page.get_by_role("link", name="Download the report")).to_be_visible()
    files = sorted((Path(folder) / "voice-selftest").glob("selftest-*.json"))
    assert len(files) == 1
    ev = json.loads(files[0].read_text())
    s = ev["steps"]
    assert ev["surface"] == surface and ev["audio_retained"] is False and ev["verdict"]["all_passed"]
    assert [r["index"] for r in s["read"]["sentences"]] == list(range(len(selftest.SENTENCES)))
    assert all(r["wer"] <= selftest.WER_PASS for r in s["read"]["sentences"])
    assert s["barge"]["detected"] and s["barge"]["onset_to_stop_ms"] <= selftest.BARGE_PASS_MS
    assert s["echo"]["false_barge_ins"] == 0 and s["micoff"]["capture_stopped"] is True
    assert s["micoff"]["live_tracks_after"] == 0
    assert ev["device"]["user_agent"] and "pcm" not in files[0].read_text()
    return ev


def summary(ev):
    s = ev["steps"]
    return {"device": "FAKE DEVICE (scripted WebAudio stream); no microphone, speaker, room or echo path",
            "wer": [r["wer"] for r in s["read"]["sentences"]],
            "transcripts": [r["transcript"] for r in s["read"]["sentences"]],
            "barge_onset_to_stop_ms": s["barge"]["onset_to_stop_ms"], "echo_false_barge_ins": s["echo"]["false_barge_ins"],
            "noise_floor_dbfs": s["silence"]["noise_dbfs"], "verdict": ev["verdict"], "browser": ev["device"].get("browser"),
            "screen": ev["device"].get("screen")}


def test_voice_check_flow_on_a_fake_device(browser, tmp_path):
    need_speech()
    console = Console(tmp_path)
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, permissions=["microphone"])
    ctx.add_init_script(FAKE_MIC)
    page = ctx.new_page()
    problems = []
    page.on("pageerror", lambda e: problems.append(str(e)))
    uploads = []
    page.on("request", lambda r: uploads.append(r.url) if r.url.endswith("/api/voice/selftest_audio") else None)
    try:
        page.goto(console.url)
        page.locator("#nav").get_by_role("link", name="Voice").click()
        expect(page.locator(".nsfv-health")).to_contain_text("Speech recognition: ready", timeout=15_000)
        ev = drive_check(page, tmp_path, "console")
        assert len(uploads) >= 5
        assert not problems, problems
        record("voice_check_fake_device", summary(ev))
    finally:
        ctx.close()
        console.close()


def test_voice_check_in_pocket_on_a_fake_device(browser, tmp_path):
    """The same check in Pocket (paired phone, HTTPS, phone-sized viewport): the report is saved on the host with
    surface 'pocket' and the browser model; Pocket keeps nothing in browser storage."""
    from contextlib import closing
    import ssl
    import subprocess
    import threading
    import time
    from neuroscifi.instance import store
    from neuroscifi.pocket import server as pocket
    from neuroscifi.webcortex import state as wstate
    need_speech()
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-keyout", str(key),
                    "-out", str(cert), "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost"],
                   check=True, capture_output=True)
    console = Console(tmp_path)
    with closing(store.connect(console.path)) as db:
        wstate.set_config(db, {"enabled": True, "pocket_research": True}, time.time())
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    srv = pocket.PocketServer(("127.0.0.1", 0), pocket.Handler, tls=tls)
    srv.store = pocket.Store(tmp_path / "mobile.sqlite")
    srv.pair_lock = threading.Lock()
    srv.invitation, srv.invitation_deadline = "invite-code", time.monotonic() + 120
    srv.instance_db = str(console.path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, permissions=["microphone"],
                              ignore_https_errors=True)
    ctx.add_init_script(FAKE_MIC)
    page = ctx.new_page()
    problems = []
    page.on("pageerror", lambda e: problems.append(str(e)))
    try:
        page.goto(f"https://localhost:{srv.server_port}/")
        page.locator("#code").fill("invite-code")
        page.locator("#pairform button").click()
        expect(page.locator("#navvoice")).to_be_visible(timeout=15_000)
        page.locator("#navvoice").click()
        expect(page.locator("#voicemount .nsfv-health")).to_contain_text("Speech recognition: ready", timeout=15_000)
        ev = drive_check(page, tmp_path, "pocket")
        assert ev["principal"].startswith("Pocket device") and ev["device"]["screen"].startswith("390x")
        assert page.evaluate("() => localStorage.length") == 0
        assert not problems, problems
        record("voice_check_fake_device_pocket", summary(ev))
    finally:
        ctx.close()
        srv.shutdown()
        srv.server_close()
        console.close()
