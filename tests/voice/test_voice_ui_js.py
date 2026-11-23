"""Voice panel source links go through safeUrl (http/https only), run under node without a browser (L7)."""
from pathlib import Path
import json
import shutil
import subprocess

import pytest

UI = Path(__file__).resolve().parents[2] / "src" / "neuroscifi" / "voice" / "web" / "voice-ui.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_voice_ui_source_links_are_safe_urls():
    script = (f"import {{ safeUrl }} from {json.dumps(UI.as_uri())};\n"
              "const cases = ['javascript:alert(1)', 'data:text/html,x', 'vbscript:x', ' javascript:x', 'not a url',"
              " 'https://example.org/a?b=1', 'http://example.com/'];\n"
              "console.log(JSON.stringify(cases.map((u) => safeUrl(u))));")
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [None, None, None, None, None, "https://example.org/a?b=1", "http://example.com/"]
    text = UI.read_text()
    assert "href: s.url" not in text and "href: safeUrl(s.url)" in text


CLIENT = UI.with_name("voice-client.mjs")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_refused_handoff_stops_waiting_at_once():
    """Low (R2): after the holder refused a hand-off, the requester kept waiting 65 s and then said "did not hand
    over". The server's refusal notice (handoff: refused) now ends the wait; nothing else is said."""
    script = (f"import {{ VoiceClient }} from {json.dumps(CLIENT.as_uri())};\n"
              "const t = { post: async (op) => op === 'capture' ? { requested: { id: 'r1', expires_at: 0 } } : {},\n"
              "            get: async () => ({ capture: { held: true, this_device: false } }) };\n"
              "const c = new VoiceClient(t, { deviceId: 'dev1' }); c.session = 's1';\n"
              "const notices = []; c.addEventListener('notice', (e) => notices.push(e.detail));\n"
              "const started = Date.now();\n"
              "await c.requestHandoff();\n"
              "c._event({ seq: 1, type: 'notice', handoff: 'refused', text: 'refused' });\n"
              "const wait = c._awaitHandoff();\n"
              "c._event({ seq: 2, type: 'notice', handoff: 'refused', text: 'refused' });\n"
              "await wait;\n"
              "console.log(JSON.stringify({ ms: Date.now() - started, notices }));\n"
              "process.exit(0);")
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=90)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["ms"] < 10_000
    assert not any("did not hand over" in n for n in got["notices"])


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_an_audio_chunk_whose_upload_failed_is_sent_once_more_with_the_same_sequence_number():
    """Defect 6: one lost response (a dropped connection, a 5xx) killed the utterance, because the client never
    retried and the next chunk was refused as a gap. It now sends the failed chunk once more (same X-NSF-Seq; the
    hub acknowledges a repeat without adding it twice); a refusal (4xx) is not retried, and a second failure is."""
    script = (f"import {{ VoiceClient }} from {json.dumps(CLIENT.as_uri())};\n"
              "const sent = []; const fails = { 1: [new Error('network down')], 3: [\n"
              "  Object.assign(new Error('HTTP 502'), { status: 502 }), Object.assign(new Error('HTTP 502'), { status: 502 })],\n"
              "  4: [Object.assign(new Error('expected chunk 9, got 4'), { status: 409 })] };\n"
              "const t = { audio: async (h) => { sent.push(h['X-NSF-Seq']); const f = (fails[h['X-NSF-Seq']] || []).shift();\n"
              "                                  if (f) throw f; return { status: 'buffered' }; } };\n"
              "const c = new VoiceClient(t, { deviceId: 'dev1' }); c.session = 's1'; c.micOn = true;\n"
              "const notices = []; c.addEventListener('notice', (e) => notices.push(e.detail));\n"
              "c.vad.utt = 'u1'; c.vad.seq = 0; c.vad.sent = 0; c.vad.frames = [];\n"
              "for (let i = 0; i < 6; i++) { c.vad.frames.push(new Int16Array(160)); c._send(false); }\n"
              "await c.queue;\n"
              "console.log(JSON.stringify({ sent, notices, retries: c.stats.audioRetries }));\n"
              "process.exit(0);")
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=90)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["sent"] == ["0", "1", "1", "2", "3", "3", "4", "5"]
    assert got["retries"] == 2 and len(got["notices"]) == 2        # chunk 3 failed twice, chunk 4 was refused
