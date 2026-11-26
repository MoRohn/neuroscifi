"""Voice routes on the real console and Pocket servers (auth, limits, headers) and the speech engine clients."""
from contextlib import closing
import http.client
import importlib.util
import json
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from neuroscifi.console import server as console_server
from neuroscifi.instance import store
from neuroscifi.pocket import server as pocket_server
from neuroscifi.voice import engines as vengines, http as vhttp, state as vstate
from neuroscifi.voice.hub import VoiceHub
from neuroscifi.webcortex import state as wstate

from .conftest import FakeEngines, pcm

TOKEN = "voice-test-token-" + "x" * 30
ROOT = Path(__file__).resolve().parents[2]


def call(port, method, path, body=None, *, token=TOKEN, raw=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    sent = {} if token is None else {"Authorization": f"Bearer {token}"}
    if body is not None:
        raw = json.dumps(body).encode()
        sent["Content-Type"] = "application/json"
    sent.update(headers or {})
    try:
        c.request(method, path, body=raw, headers=sent)
        r = c.getresponse()
        data = r.read()
    finally:
        c.close()
    try:
        return r.status, dict(r.getheaders()), json.loads(data)
    except ValueError:
        return r.status, dict(r.getheaders()), data


@pytest.fixture
def console(instance):
    FakeEngines.instance = None
    vstate.reset()
    srv = console_server.ConsoleServer(instance, 0, token=TOKEN)
    srv.voice_hub = VoiceHub(instance, engines_factory=FakeEngines)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()
    vstate.reset()


def audio_headers(sid, utt="u0001", seq=0, final=False):
    return {"Content-Type": "audio/L16; rate=16000", "X-NSF-Session": sid, "X-NSF-Utterance": utt,
            "X-NSF-Seq": str(seq), "X-NSF-Final": "1" if final else "0"}


def test_console_voice_routes_auth_limits_and_headers(console):
    port = console.server_port
    assert call(port, "GET", "/api/voice/status", token=None)[0] == 403
    assert call(port, "GET", "/api/voice/status", token="wrong")[0] == 403
    st, headers, body = call(port, "GET", "/api/voice/status")
    assert st == 200 and body["available"] == {"voice_in": True, "voice_out": True, "text": True}
    assert "microphone=(self)" in headers["Permissions-Policy"] and "camera=()" in headers["Permissions-Policy"]
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert call(port, "POST", "/api/voice/status", {})[0] == 405
    assert call(port, "GET", "/api/voice/nope")[0] == 404
    st, _, s = call(port, "POST", "/api/voice/session", {"action": "start", "device": "abc12345"})
    assert st == 200 and s["device"] == "console:abc12345"
    sid = s["session"]
    assert call(port, "POST", "/api/voice/session", {"action": "start", "device": "../../etc"})[0] == 400
    assert call(port, "POST", "/api/voice/text", {"session": sid, "text": "x" * 5000})[0] == 413   # 4 KiB JSON cap
    st, _, err = call(port, "POST", "/api/voice/text", raw=b"{}", headers={"Content-Type": "text/plain"})
    assert st == 400
    # audio: dedicated route, own type and 64 KiB cap; refused without consent and lease
    st, _, err = call(port, "POST", "/api/voice/audio", raw=pcm(0.2), headers=audio_headers(sid))
    assert st == 409 and "consent" in err["error"]
    assert call(port, "POST", "/api/voice/consent", {"session": sid, "accept": True})[0] == 200
    assert call(port, "POST", "/api/voice/capture", {"session": sid, "action": "acquire"})[2]["granted"]
    st, _, err = call(port, "POST", "/api/voice/audio", raw=b"\x00" * (64 * 1024 + 2), headers=audio_headers(sid))
    assert st == 413
    st, _, err = call(port, "POST", "/api/voice/audio", raw=pcm(0.2),
                      headers={**audio_headers(sid), "Content-Type": "audio/L16; rate=48000"})
    assert st == 400
    st, _, out = call(port, "POST", "/api/voice/audio", raw=pcm(0.5), headers=audio_headers(sid, final=True))
    assert st == 200 and out["status"] == "final"
    # other console routes keep their limits
    assert call(port, "POST", "/api/epochs", {"name": "e1", "note": "n" * 5000})[0] == 413


def test_console_serves_voice_modules(console):
    port = console.server_port
    for name in ("voice-client.mjs", "capture-worklet.mjs", "voice-ui.mjs", "voice.css"):
        st, headers, body = call(port, "GET", f"/voice/{name}", token=None)
        assert st == 200 and len(body) > 100, name
    st, _, page = call(port, "GET", "/", token=None)
    assert b'data-view="voice"' in page and b"/voice/voice.css" in page


def test_console_events_long_poll_and_speech(console):
    port = console.server_port
    sid = call(port, "POST", "/api/voice/session", {"action": "start", "device": "abc12345"})[2]["session"]
    call(port, "POST", "/api/voice/text", {"session": sid, "text": "help"})
    deadline, reply = time.monotonic() + 5, None
    while reply is None and time.monotonic() < deadline:
        ev = call(port, "GET", f"/api/voice/events?session={sid}&since=0&wait=2")[2]["events"]
        reply = next((e for e in ev if e["type"] == "reply"), None)
    st, headers, wav = call(port, "GET", f"/api/voice/speech?session={sid}&turn={reply['turn']['id']}&n=0")
    assert st == 200 and headers["Content-Type"] == "audio/wav" and wav[:4] == b"RIFF"
    assert call(port, "GET", f"/api/voice/speech?session={sid}&turn={reply['turn']['id']}&n=9")[0] == 404


@pytest.fixture
def pocket(tmp_path, instance):
    FakeEngines.instance = None
    with closing(store.connect(instance)) as db:
        wstate.set_config(db, {"pocket_research": True}, time.time())
    srv = pocket_server.PocketServer(("127.0.0.1", 0), pocket_server.Handler)
    srv.store = pocket_server.Store(tmp_path / "mobile.sqlite")
    srv.pair_lock = threading.Lock()
    srv.invitation, srv.invitation_deadline = "invite", time.monotonic() + 60
    srv.instance_db = str(instance)
    srv.voice_hub = VoiceHub(instance, engines_factory=FakeEngines)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def ppost(srv, route, body=None, key=None, raw=None, headers=None):
    sent = {"Content-Type": "application/json"}
    if key:
        sent["Authorization"] = "Bearer " + key
    sent.update(headers or {})
    return call(srv.server_port, "POST", "/api/" + route, token=None, raw=raw if raw is not None else
                json.dumps(body if body is not None else {}).encode(), headers=sent)


def test_pocket_voice_needs_paired_credential_and_keeps_limits(pocket):
    assert ppost(pocket, "voice_status", {})[0] == 403
    assert ppost(pocket, "voice_status", {}, key="not-paired")[0] == 403
    key = ppost(pocket, "pair", {"code": "invite"})[2]["key"]
    assert ppost(pocket, "voice_status", key=key, raw=b"")[0] == 400   # 2..512 byte JSON bodies, as every route
    assert ppost(pocket, "voice_status", key=key, raw=json.dumps({"q": "x" * 600}).encode())[0] == 400
    st, _, status = ppost(pocket, "voice_status", {"probe": False}, key=key)
    assert st == 200 and status["principal"] == {"owner": False, "research": True,
                                                  "label": status["principal"]["label"]}
    st, _, s = ppost(pocket, "voice_session", {"action": "start", "device": "phone123"}, key=key)
    assert st == 200 and s["device"].startswith("pocket:") and "phone123" not in s["device"]  # the pairing is the id
    sid = s["session"]
    assert ppost(pocket, "voice_text", {"session": sid, "text": "y" * 2000}, key=key)[0] == 400   # 1 KiB cap
    assert ppost(pocket, "voice_settings", {"retain_audio": True}, key=key)[0] == 400              # owner only
    ppost(pocket, "voice_consent", {"session": sid, "accept": True}, key=key)
    assert ppost(pocket, "voice_capture", {"session": sid, "action": "acquire"}, key=key)[2]["granted"]
    st, _, out = ppost(pocket, "voice_audio", key=key, raw=pcm(0.5), headers=audio_headers(sid, final=True))
    assert st == 200 and out["status"] == "final"
    st, _, ev = ppost(pocket, "voice_events", {"session": sid, "since": 0, "wait": 1}, key=key)
    assert st == 200 and any(e["type"] == "transcript" for e in ev["events"])


def test_malformed_voice_fields_are_400_not_500(pocket, console):
    """T5-05: a segment id >= 2**63 overflowed SQLite (500); an object as `session` was a 500 for Pocket; an
    objective with NUL broke the database CHECK (500 / 503, T5-06)."""
    key = ppost(pocket, "pair", {"code": "invite"})[2]["key"]
    sid = ppost(pocket, "voice_session", {"action": "start", "device": "phone123"}, key=key)[2]["session"]
    for route, body in (("voice_status", {"session": {"x": 1}}), ("voice_text", {"session": ["a"], "text": "hi"}),
                        ("voice_events", {"session": {"x": 1}, "since": 0, "wait": 0}),
                        ("voice_correct", {"segment": 2 ** 63, "text": "x"}),
                        ("voice_correct", {"segment": 1, "text": {"x": 1}}),
                        ("voice_transcripts", {"limit": 2 ** 64}),
                        ("voice_events", {"session": sid, "since": 2 ** 70, "wait": 0})):
        st, _, out = ppost(pocket, route, body, key=key)
        assert st == 400 and out["error"], (route, st, out)
    port = console.server_port
    for route, body in (("/api/voice/correct", {"segment": 2 ** 63, "text": "x"}),
                        ("/api/voice/text", {"session": {"x": 1}, "text": "hi"})):
        st, _, out = call(port, "POST", route, body)
        assert st == 400, (route, st, out)


def test_pocket_voice_is_off_unless_pocket_research_is_on(pocket, instance):
    """Pocket voice follows the pocket_research switch (default off), like Pocket research itself (H1)."""
    key = ppost(pocket, "pair", {"code": "invite"})[2]["key"]
    sid = ppost(pocket, "voice_session", {"action": "start", "device": "phone123"}, key=key)[2]["session"]
    with closing(store.connect(instance)) as db:
        wstate.set_config(db, {"pocket_research": False}, time.time())
    assert ppost(pocket, "voice_status", {"probe": False}, key=key)[2] == {"voice": False}
    for route, body in (("voice_session", {"action": "start", "device": "phone123"}),
                        ("voice_text", {"session": sid, "text": "what have you learned since yesterday"}),
                        ("voice_transcripts", {}), ("voice_events", {"session": sid, "since": 0, "wait": 0})):
        assert ppost(pocket, route, body, key=key)[0] == 404, route
    assert ppost(pocket, "voice_status", {"probe": False}, key="unpaired")[0] == 403


def test_pocket_voice_invisible_without_instance(tmp_path):
    srv = pocket_server.PocketServer(("127.0.0.1", 0), pocket_server.Handler)
    srv.store = pocket_server.Store(tmp_path / "m.sqlite")
    srv.pair_lock = threading.Lock()
    srv.invitation, srv.invitation_deadline = "invite", time.monotonic() + 60
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        key = ppost(srv, "pair", {"code": "invite"})[2]["key"]
        assert ppost(srv, "voice_status", {"probe": False}, key=key)[2] == {"voice": False}
        assert ppost(srv, "voice_status", {"probe": False}, key="unpaired")[0] == 403
        assert ppost(srv, "voice_session", {"action": "start", "device": "abcd1234"}, key=key)[0] == 404
        assert call(srv.server_port, "GET", "/voice/voice-client.mjs", token=None)[0] == 200
    finally:
        srv.shutdown()
        srv.server_close()


# ---- engines against small fake servers ----------------------------------------------------------------------------

class _Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, mime="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        mode = self.server.mode
        if self.path == "/health":
            return self._send(200 if mode != "loading" else 503, {"status": "ready"})
        if self.headers.get("Authorization") != "Bearer k":
            return self._send(401, {})
        if self.path == "/v1/models":
            return self._send(200, {"data": [{"id": "whisper-large-v3-turbo"}]})
        return self._send(200, {"voices": ["af_heart"]})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.posts += 1
        try:
            self.server.bodies.append(json.loads(raw))
        except ValueError:
            pass
        if self.headers.get("Authorization") != "Bearer k":
            return self._send(401, {})
        if self.server.mode == "error":
            return self._send(500, {})
        if self.path == "/v1/audio/transcriptions":
            return self._send(200, {"text": "  hello   world "})
        return self._send(200, b"RIFF" + b"\x00" * 40, "audio/wav")


@pytest.fixture
def fake_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
    srv.mode, srv.posts, srv.bodies = "ok", 0, []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def test_engine_states_retry_and_cooldown(fake_server):
    url = f"http://127.0.0.1:{fake_server.server_port}"
    asr = vengines.WhisperASR(url, "k", "whisper-large-v3-turbo")
    assert asr.health()["state"] == "ready"
    assert asr.transcribe(pcm(0.1))["text"] == "hello world"
    assert vengines.WhisperASR(url, "wrong", "whisper-large-v3-turbo").health()["state"] == "unauthorised"
    assert vengines.WhisperASR(url, "k", "other-model").health()["state"] == "degraded"
    assert vengines.WhisperASR("http://10.1.2.3:1", "k", "m").health()["state"] == "not_configured"  # not loopback
    fake_server.mode = "loading"
    assert asr.health()["state"] == "loading"
    fake_server.mode = "error"
    for _ in range(vengines.FAILS_TO_COOL):
        with pytest.raises(vengines.EngineError):
            asr.transcribe(pcm(0.1))
    posts = fake_server.posts
    with pytest.raises(vengines.EngineError, match="cooling down"):
        asr.transcribe(pcm(0.1))
    assert fake_server.posts == posts                      # fail fast while cooling down, no request sent
    assert asr.metrics.public()["failures"] == 3
    tts = vengines.KokoroTTS(url, "k")
    with pytest.raises(vengines.EngineError, match="allow-list"):
        tts.synthesize("hi", "../../etc/passwd")
    fake_server.mode = "ok"
    assert tts.synthesize("hi", "af_heart")["wav"].startswith(b"RIFF")
    dead = vengines.KokoroTTS("http://127.0.0.1:9", "k")
    assert dead.health()["state"] == "unavailable"


def test_personaplex_is_opt_in_loopback_and_allow_listed():
    off = vengines.PersonaPlexEngine("http://127.0.0.1:18998", False, "NATF2")
    assert off.health()["state"] == "disabled" and off.health()["authoritative"] is False
    remote = vengines.PersonaPlexEngine("http://192.168.1.4:8998", True, "NATF2")
    assert remote.health()["state"] == "not_configured"
    url = vengines.PersonaPlexEngine("http://127.0.0.1:18998", True, "NATF2").session_url("x" * 900)
    assert url.startswith("ws://127.0.0.1:18998/api/chat?voice_prompt=NATF2.pt") and len(url) < 1700
    with pytest.raises(vengines.EngineError):
        vengines.PersonaPlexEngine("http://127.0.0.1:18998", True, "../evil").session_url("x")


def test_tts_server_wrapper_rules():
    spec = importlib.util.spec_from_file_location("tts_server", ROOT / "deploy" / "voice" / "tts_server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class Engine:
        device = "cpu"

        def synthesize(self, text, voice, speed=1.0):
            return [0.0, 0.5, -0.5] * 800

    state = mod.State("k", Engine)
    state.load()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
    srv.state = state
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_port
    try:
        assert call(port, "GET", "/health", token=None)[2]["status"] == "ready"
        assert call(port, "GET", "/v1/voices", token=None)[0] == 401
        assert call(port, "POST", "/v1/audio/speech", {"input": "hi"}, token="bad")[0] == 401
        st, headers, wav = call(port, "POST", "/v1/audio/speech", {"input": "hi", "voice": "af_heart"}, token="k")
        assert st == 200 and wav[:4] == b"RIFF" and headers["X-Audio-Seconds"] == "0.100"
        assert call(port, "POST", "/v1/audio/speech", {"input": "hi", "voice": "x/../y"}, token="k")[0] == 400
        assert call(port, "POST", "/v1/audio/speech", {"input": "x" * 1001}, token="k")[0] == 400
        assert call(port, "POST", "/v1/audio/speech", {"input": "hi", "speed": 9}, token="k")[0] == 400
        assert call(port, "POST", "/v1/audio/speech", raw=b"x" * 9000, token="k",
                    headers={"Content-Type": "application/json"})[0] == 400
    finally:
        srv.shutdown()
        srv.server_close()


def test_voice_http_module_lists_pocket_routes():
    assert "voice_audio" in vhttp.POCKET_ROUTES and "voice_events" in vhttp.POCKET_ROUTES
    assert not vhttp.POCKET_ROUTES & (pocket_server.ROUTES | pocket_server.RESEARCH)


def test_speakable_pronunciation_pass():
    """T2-01: domains, URLs and product names reach Kokoro in sayable form; ordinary words are untouched."""
    from neuroscifi.voice.engines import speakable
    assert speakable("According to nature.com: yes.") == "According to nature dot com: yes."
    assert speakable("See https://www.example.org/a/b?c=1 now") == "See example dot org now"
    assert speakable("Ordinary words, 3.5 points.") == "Ordinary words, 3.5 points."
    assert speakable("It uses vLLM.") == "It uses V L L M."


def test_speakable_gives_unknown_names_inline_phonemes():
    """T2-01: names and new terms reach Kokoro as misaki inline phonemes, so a server without the espeak-ng fallback
    still says them (the RC1 container dropped them: "This is NeuroSciFi." -> "This is"); markup in the text itself
    cannot set a pronunciation, and the length cap never cuts a marker."""
    from neuroscifi.voice.engines import PHONEMES, speakable
    assert speakable("This is NeuroSciFi.") == f"This is [NeuroSciFi](/{PHONEMES['neuroscifi']}/)."
    out = speakable("Agentic AI on Kubernetes and PyTorch, says ycombinator.com.")
    for word in ("Agentic", "Kubernetes", "PyTorch", "ycombinator"):
        assert f"[{word}](/{PHONEMES[word.lower()]}/)" in out
    assert out.endswith(" dot com.")
    assert speakable("Say [hello](/hˈɛlO/) and [x](http://e.x) now [1].") == "Say hello and x now  1 ."
    long = speakable("a" * 990 + " agentic")
    assert len(long) <= 1000 and "[" not in long


def test_tts_sends_the_speakable_text(fake_server):
    tts = vengines.KokoroTTS(f"http://127.0.0.1:{fake_server.server_port}", "k")
    fake_server.mode = "ok"
    tts.synthesize("According to nature.com.", "af_heart")
    assert fake_server.bodies[-1]["input"] == "According to nature dot com."


def test_tts_server_refuses_to_run_without_the_espeak_fallback():
    spec = importlib.util.spec_from_file_location("tts_server", ROOT / "deploy" / "voice" / "tts_server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class G2P:
        def __init__(self, fallback):
            self.fallback = fallback

    class Pipe:
        def __init__(self, fallback):
            self.g2p = G2P(fallback)
    mod.require_fallback({"a": Pipe(object()), "b": Pipe(object())})
    with pytest.raises(RuntimeError, match="fallback"):
        mod.require_fallback({"a": Pipe(None), "b": Pipe(object())})


def test_tts_container_tmp_allows_loading_espeak(tmp_path):
    """The TTS container's /tmp must be exec: phonemizer loads libespeak-ng.so from a temporary copy (T2-01)."""
    model = tmp_path / ".local/share/neuroscifi/models/kokoro-82m"
    model.mkdir(parents=True)
    (model / "kokoro-v1_0.pth").write_bytes(b"")
    run = subprocess.run(["bash", str(ROOT / "deploy" / "voice" / "run.sh"), "print", "tts"],
                         env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}, capture_output=True, text=True,
                         timeout=30)
    options = re.search(r"--tmpfs /tmp:(\S+)", run.stdout.replace("\\,", ",")).group(1).split(",")
    assert "exec" in options and "noexec" not in options and {"nosuid", "nodev"} <= set(options), run.stdout


# ---- the gateway's protections for the speech engines (OMNIMIND lane S, audit H2) ---------------------------------

class _Sized(BaseHTTPRequestHandler):
    """A fake engine whose reply is `size` bytes (WAV for speech, JSON for transcription and health)."""
    def log_message(self, *a):
        pass

    def _reply(self, body, mime, length=None):
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body) if length is None else length))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def do_GET(self):
        self._reply(b"{" + b" " * self.server.size + b"}", "application/json")

    def do_POST(self):
        self.server.posts += 1
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path == "/v1/audio/transcriptions":
            return self._reply(b'{"text": "hi"' + b" " * self.server.size + b"}", "application/json")
        self._reply(b"RIFF" + b"\0" * self.server.size, "audio/wav", length=self.server.length)


@pytest.fixture
def sized_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Sized)
    srv.size, srv.length, srv.posts = 100, None, 0
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def test_a_reply_larger_than_the_engine_can_send_is_refused_by_name(sized_server):
    url = f"http://127.0.0.1:{sized_server.server_port}"
    tts, asr = vengines.KokoroTTS(url, "k"), vengines.WhisperASR(url, "k", "m")
    assert len(tts.synthesize("hi", "af_heart")["wav"]) == 104           # an ordinary reply is read whole
    assert asr.transcribe(pcm(0.1))["text"] == "hi"
    sized_server.size = vengines.TTS_MAX_REPLY                           # 4 bytes over the bound with "RIFF"
    with pytest.raises(vengines.EngineError) as err:
        tts.synthesize("hi", "af_heart")
    assert err.value.code == "bad_response" and str(vengines.TTS_MAX_REPLY) in str(err.value)
    sized_server.size, sized_server.length = 100, vengines.TTS_MAX_REPLY + 1  # declared too large: not read at all
    with pytest.raises(vengines.EngineError, match="larger than"):
        tts.synthesize("hi", "af_heart")
    sized_server.size, sized_server.length = vengines.MAX_REPLY, None
    with pytest.raises(vengines.EngineError, match="larger than"):
        asr.transcribe(pcm(0.1))
    assert asr.health()["state"] == "bad_response"


def test_the_tts_bound_holds_the_longest_speech_the_input_limit_allows():
    """1,000 characters at most (MAX_TTS_INPUT), at most one second of speech each at the slowest speed, 24 kHz 16-bit
    mono (deploy/voice/tts_server.py SAMPLE_RATE), plus the WAV header."""
    assert vengines.TTS_MAX_REPLY == vengines.MAX_TTS_INPUT * 1 * 24000 * 2 + 44


def test_nothing_is_sent_to_a_speech_port_held_by_another_accounts_process(sized_server):
    """Whoever binds the engine's loopback port while its container is down would get the voice key and the audio."""
    url = f"http://127.0.0.1:{sized_server.server_port}"
    stranger = os.getuid() + 4242
    for engine, call in ((vengines.KokoroTTS(url, "k"), lambda e: e.synthesize("hi", "af_heart")),
                         (vengines.WhisperASR(url, "k", "m"), lambda e: e.transcribe(pcm(0.1)))):
        engine.listeners = lambda port: [("127.0.0.1", stranger)]
        with pytest.raises(vengines.EngineError) as err:
            call(engine)
        assert err.value.code == "unavailable" and f"uid {stranger}" in str(err.value)
        assert "NSF_LISTENER_CHECK=0" in str(err.value)
        assert engine.health()["state"] == "unavailable"
        engine.listeners = lambda port: [("127.0.0.1", os.getuid())]     # this account's own server: used as before
        call(engine)
    assert sized_server.posts == 2                                       # only the two trusted calls arrived
