"""Speech engine interface: ASR, TTS and an optional full-duplex engine, each with health/readiness, timeouts,
bounded retry, a reconnect cool-down and metrics. Stdlib HTTP only, loopback endpoints only.

States reported by health() (never guessed):
  ready          the engine answered and is serving (TTS: its /health says ready; ASR: /health and the model listed)
  loading        the process answers but is still loading (TTS /health 'loading', ASR /health 503)
  unauthorised   401/403 with the configured key
  unavailable    connection refused, timeout, 5xx, or in the cool-down after repeated failures
  not_configured no key file / endpoint not loopback
  disabled       an opt-in engine (PersonaPlex) that is switched off
  bad_response   the engine answered with more than it can send (a reply over its size bound, see below)
Reconnect: after FAILS_TO_COOL consecutive failures calls fail fast for COOLDOWN seconds, then the next call tries
again (one probe); a success resets the counter.

The inference gateway's protections, for the voice key and the audio (OMNIMIND lane S, audit H2):
  - Who holds the port: before anything is sent, the owner of the loopback socket that would accept the connection
    is read (resources.monitor.stranger, the check behind the gateway's listener_refusal). Root (Docker's port
    proxy), this account and the key file's owner are the service; a process of any other account gets neither the
    key nor the audio ('unavailable'; NSF_LISTENER_CHECK=0 if that account runs the engine on purpose). Every port
    an engine is configured for is one of this deployment's speech services, so every one is checked.
  - Reply size: at most MAX_REPLY bytes for JSON (transcription, health, lists) and TTS_MAX_REPLY for speech, checked
    against Content-Length before reading and enforced while reading ('bad_response').
"""
from __future__ import annotations

from collections import deque
import http.client
import json
import re
import socket
import struct
import threading
import time
from urllib.parse import urlencode, urlsplit
import uuid

from . import config as vconfig

CONNECT_TIMEOUT = 2.0
FAILS_TO_COOL = 3
COOLDOWN = 10.0
RETRIES = 1                 # one retry for a connection error (not for a timeout, not after bytes were sent back)
RETRY_BACKOFF = 0.25
# A transcription of one utterance (the hub sends at most 30 s) is a few hundred bytes of JSON; Whisper decodes at
# most 448 tokens per 30 s window, so 1 MiB holds several minutes even at 12 bytes of escaped JSON per character of a
# long token. Health and model lists are smaller still.
MAX_REPLY = 1 << 20


class EngineError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def wav(pcm: bytes, rate: int) -> bytes:
    """A 16-bit mono WAV container around little-endian PCM."""
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" + struct.pack("<I", len(pcm)) + pcm)


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = self.failures = self.consecutive = 0
        self.last_error = None
        self.last_ok_at = None
        self.cool_until = 0.0
        self.latency = deque(maxlen=100)

    def ok(self, seconds):
        with self.lock:
            self.calls += 1
            self.consecutive = 0
            self.last_ok_at = time.time()
            self.latency.append(seconds)

    def fail(self, code, message):
        with self.lock:
            self.calls += 1
            self.failures += 1
            self.consecutive += 1
            self.last_error = f"{code}: {message}"[:200]
            if self.consecutive >= FAILS_TO_COOL:
                self.cool_until = time.monotonic() + COOLDOWN

    def cooling(self):
        return time.monotonic() < self.cool_until

    def public(self):
        with self.lock:
            lat = sorted(self.latency)
        pick = (lambda q: round(lat[min(len(lat) - 1, int(q * len(lat)))] * 1000, 1)) if lat else (lambda q: None)
        return {"calls": self.calls, "failures": self.failures, "consecutive_failures": self.consecutive,
                "last_error": self.last_error, "p50_ms": pick(0.5), "p95_ms": pick(0.95),
                "cooling_down": self.cooling()}


class HttpEngine:
    """Shared plumbing: one HTTP request with timeouts, a retry for connection errors and the cool-down."""
    kind = "engine"

    def __init__(self, url, key, *, read_timeout=30.0, key_file=None):
        self.url, self.key, self.read_timeout = url.rstrip("/"), key, read_timeout
        self.key_file = key_file     # its owner is trusted to run the engine (stranger)
        self.listeners = None        # port -> [(address, uid)] of its listening sockets (resources.monitor.listeners)
        self.metrics = Metrics()
        parts = urlsplit(self.url)
        self.host, self.port, self.https = parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), \
            parts.scheme == "https"
        self.configured = bool(key) and vconfig.loopback_url(self.url)

    def _conn(self, timeout):
        cls = http.client.HTTPSConnection if self.https else http.client.HTTPConnection
        return cls(self.host, self.port, timeout=timeout)

    def stranger(self) -> str | None:
        """Why nothing may be sent to this engine's port, or None (resources.monitor.stranger: a process of another
        account holds the loopback socket the connection would reach). Never a refusal when the check itself fails."""
        from ..inference import config as iconfig
        from ..resources import monitor
        try:
            where, _, addresses = iconfig.resolve(self.url)
            if where != "loopback":
                return None
            return monitor.stranger(addresses, self.port, key_file=self.key_file, table=self.listeners,
                                    what=f"the {self.name} service")
        except Exception:                      # the check must never be the reason a call fails
            return None

    def request(self, method, path, body=None, headers=None, *, timeout=None, auth=True, record=True,
                limit=MAX_REPLY):
        if not self.configured:
            raise EngineError("not_configured", "no key file or endpoint is not loopback")
        if self.metrics.cooling():
            raise EngineError("unavailable", f"cooling down after repeated failures ({self.metrics.last_error})")
        refused = self.stranger()
        if refused:
            self.metrics.fail("unavailable", refused) if record else None
            raise EngineError("unavailable", refused)
        sent = dict(headers or {})
        if auth:
            sent["Authorization"] = f"Bearer {self.key}"
        started = time.perf_counter()
        for attempt in range(RETRIES + 1):
            conn = self._conn(CONNECT_TIMEOUT)
            try:
                conn.connect()
                conn.sock.settimeout(timeout or self.read_timeout)
                conn.request(method, path, body=body, headers=sent)
                resp = conn.getresponse()
                declared = resp.getheader("Content-Length")
                if declared is not None and declared.strip().isdigit() and int(declared) > limit:
                    data = None
                else:
                    data = resp.read(limit + 1)
                status, rheaders = resp.status, dict(resp.getheaders())
                break
            except (ConnectionError, socket.gaierror) as exc:
                if attempt < RETRIES:
                    time.sleep(RETRY_BACKOFF * (2 ** attempt))
                    continue
                self.metrics.fail("unavailable", str(exc)) if record else None
                raise EngineError("unavailable", f"cannot connect: {exc}") from None
            except (socket.timeout, TimeoutError):
                self.metrics.fail("timeout", "no answer in time") if record else None
                raise EngineError("timeout", "no answer in time") from None
            except (OSError, http.client.HTTPException) as exc:
                self.metrics.fail("unavailable", str(exc)) if record else None
                raise EngineError("unavailable", str(exc)) from None
            finally:
                conn.close()
        if data is None or len(data) > limit:
            self.metrics.fail("bad_response", f"reply larger than {limit} bytes") if record else None
            raise EngineError("bad_response", f"the engine's reply is larger than {limit} bytes; it was not read")
        if status in (401, 403):
            self.metrics.fail("unauthorised", str(status)) if record else None
            raise EngineError("unauthorised", f"HTTP {status}: wrong or missing key")
        if status >= 500 or status == 429:
            self.metrics.fail("unavailable", f"HTTP {status}") if record else None
            raise EngineError("unavailable", f"HTTP {status}")
        if status >= 400:
            self.metrics.fail("bad_request", f"HTTP {status}") if record else None
            raise EngineError("bad_request", f"HTTP {status}: {data[:200].decode('utf-8', 'replace')}")
        if record:
            self.metrics.ok(time.perf_counter() - started)
        return status, rheaders, data

    def _health(self, state, reason=None, latency=None, **extra):
        return {"engine": self.name, "kind": self.kind, "url": self.url, "state": state, "reason": reason,
                "latency_ms": None if latency is None else round(latency * 1000, 1), "metrics": self.metrics.public(),
                **extra}


class WhisperASR(HttpEngine):
    """openai/whisper-large-v3-turbo through vLLM's /v1/audio/transcriptions (deploy/voice, port 18431)."""
    kind = "asr"
    name = "whisper-large-v3-turbo (vLLM)"

    def __init__(self, url, key, model, *, read_timeout=20.0, key_file=None):
        super().__init__(url, key, read_timeout=read_timeout, key_file=key_file)
        self.model = model

    def transcribe(self, pcm: bytes, rate=16000, *, language="en") -> dict:
        boundary = uuid.uuid4().hex
        parts = [f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
                 for k, v in (("model", self.model), ("language", language), ("temperature", "0"),
                              ("response_format", "json"))]
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"u.wav\"\r\n"
                     f"Content-Type: audio/wav\r\n\r\n".encode() + wav(pcm, rate) + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        started = time.perf_counter()
        _, _, data = self.request("POST", "/v1/audio/transcriptions", b"".join(parts),
                                  {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            text = json.loads(data)["text"]
        except (ValueError, KeyError, TypeError):
            raise EngineError("bad_response", "transcription reply is not JSON with text") from None
        return {"text": " ".join(str(text).split()), "latency": time.perf_counter() - started,
                "audio_seconds": len(pcm) / 2 / rate}

    def health(self, probe=False) -> dict:
        if not self.configured:
            return self._health("not_configured", "no voice key file, or the ASR URL is not loopback")
        started = time.perf_counter()
        try:
            status, _, _ = self.request("GET", "/health", auth=False, timeout=3, record=False)
            _, _, data = self.request("GET", "/v1/models", timeout=3, record=False)
            served = [m.get("id") for m in json.loads(data).get("data", [])]
        except EngineError as err:
            return self._health("loading" if "HTTP 503" in str(err) else err.code, str(err))
        except ValueError:
            return self._health("unavailable", "unparsable /v1/models")
        if self.model not in served:
            return self._health("degraded", f"model {self.model} not served (serving {served})")
        out = self._health("ready", None, time.perf_counter() - started, model=self.model)
        if probe:   # a real transcription of 0.5 s of silence: proves the audio path, not the words
            try:
                out["probe_latency_ms"] = round(self.transcribe(b"\x00\x00" * 8000)["latency"] * 1000, 1)
            except EngineError as err:
                out.update(state="degraded", reason=f"probe transcription failed: {err}")
        return out


# ---- pronunciation pass before TTS ---------------------------------------------------------------------------------
# Kokoro's G2P (misaki) knows dictionary words and spells capital acronyms (LLM, GPU); the espeak-ng fallback guesses
# the rest (deploy/voice/run.sh keeps it loadable). This pass fixes what neither gets right: URLs and domains (read as
# "techcrunch dot com", not dropped or run together) and the names and terms in PHONEMES, which are given to misaki
# as inline phonemes ("[agentic](/Aʤˈɛntɪk/)"), so they are spoken even by a server whose fallback failed to load
# (UAT T2-01: the RC1 container dropped every unknown word: "This is NeuroSciFi." -> "This is"). Phonemes are
# misaki's American set; each was checked by a Kokoro -> Whisper round trip. Markup already in the text is removed
# first, so only this table can set a pronunciation.
LEXICON = {"vllm": "V L L M"}
PHONEMES = {"neuroscifi": "nˈʊɹOsˌIfˌI", "agentic": "Aʤˈɛntɪk", "kubernetes": "kˌubəɹnˈɛtiz",
            "pytorch": "pˈI tˈɔɹʧ", "searxng": "sˈɜɹʧ ˌɛn ʤˈi", "qwen": "ʧwˈɛn", "ycombinator": "wˈI kˈɑmbənˌAɾəɹ",
            "techcrunch": "tˈɛkkɹˌʌnʧ", "github": "ɡˈɪthˌʌb", "arxiv": "ˈɑɹkˌIv"}
_URL = re.compile(r"\bhttps?://([^/\s]+)\S*", re.IGNORECASE)
_DOMAIN = re.compile(r"\b((?:[a-z0-9-]+\.)+(?:com|org|net|io|ai|dev|edu|gov|co|uk|de|fr|eu|info|app))\b", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_MARKUP = re.compile(r"\[([^\]]*)\]\([^)]*\)")
MAX_TTS_INPUT = 1000
# The longest WAV a 1,000-character input can honestly be: at most one second of speech per input character at the
# slowest speed the owner may choose (0.5; ordinary speech is about 0.07 s a character at speed 1), 24 kHz 16-bit mono
# (deploy/voice/tts_server.py SAMPLE_RATE), plus the 44-byte header: 48,000,044 bytes.
TTS_SAMPLE_RATE = 24000
TTS_MAX_REPLY = MAX_TTS_INPUT * 1 * TTS_SAMPLE_RATE * 2 + 44


def _say_word(m):
    word = m.group(0)
    key = word.lower()
    if key in PHONEMES:
        return f"[{word}](/{PHONEMES[key]}/)"
    return LEXICON.get(key, word)


def speakable(text: str) -> str:
    """The text as it should be pronounced: URLs -> their host, domains -> "techcrunch dot com", LEXICON terms
    respelled, PHONEMES terms as inline phonemes. Only what is spoken changes; the transcript keeps the original text.
    At most MAX_TTS_INPUT characters, never cut inside a phoneme marker."""
    text = _MARKUP.sub(lambda m: m.group(1), text or "").replace("[", " ").replace("]", " ")
    text = _URL.sub(lambda m: m.group(1), text)
    text = _DOMAIN.sub(lambda m: " dot ".join(m.group(1).lower().removeprefix("www.").split(".")), text)
    out, n = [], 0
    for part in re.split(r"(\[[^\]]*\]\(/[^)]*/\))", _WORD.sub(_say_word, text)):
        if n + len(part) > MAX_TTS_INPUT:
            if not part.startswith("["):
                out.append(part[:MAX_TTS_INPUT - n])
            break
        out.append(part)
        n += len(part)
    return "".join(out)


class KokoroTTS(HttpEngine):
    """Kokoro-82M behind deploy/voice/tts_server.py (port 18432)."""
    kind = "tts"
    name = "Kokoro-82M"

    def __init__(self, url, key, *, read_timeout=20.0, key_file=None):
        super().__init__(url, key, read_timeout=read_timeout, key_file=key_file)

    def synthesize(self, text: str, voice: str, speed: float = 1.0) -> dict:
        if voice not in vconfig.VOICES:
            raise EngineError("bad_request", "voice not on the allow-list")
        body = json.dumps({"input": speakable(text), "voice": voice, "speed": speed,
                           "response_format": "wav"}).encode()
        started = time.perf_counter()
        _, headers, data = self.request("POST", "/v1/audio/speech", body, {"Content-Type": "application/json"},
                                        limit=TTS_MAX_REPLY)
        if not data.startswith(b"RIFF"):
            raise EngineError("bad_response", "speech reply is not WAV audio")
        lower = {k.lower(): v for k, v in headers.items()}
        return {"wav": data, "latency": time.perf_counter() - started,
                "audio_seconds": float(lower.get("x-audio-seconds") or 0), "voice": voice}

    def health(self, probe=False) -> dict:
        if not self.configured:
            return self._health("not_configured", "no voice key file, or the TTS URL is not loopback")
        started = time.perf_counter()
        try:
            _, _, data = self.request("GET", "/health", auth=False, timeout=3, record=False)
            info = json.loads(data)
            self.request("GET", "/v1/voices", timeout=3, record=False)
        except EngineError as err:
            return self._health("loading" if "HTTP 503" in str(err) else err.code, str(err))
        except ValueError:
            return self._health("unavailable", "unparsable /health")
        out = self._health("ready" if info.get("status") == "ready" else "loading", info.get("error"),
                           time.perf_counter() - started, device=info.get("device"), rtf=info.get("rtf"))
        if probe and out["state"] == "ready":
            try:
                out["probe_latency_ms"] = round(self.synthesize("Ready.", vconfig.VOICES[0])["latency"] * 1000, 1)
            except EngineError as err:
                out.update(state="degraded", reason=f"probe synthesis failed: {err}")
        return out


class PersonaPlexEngine(HttpEngine):
    """EXPERIMENTAL, opt-in full-duplex engine (NVIDIA PersonaPlex, audit docs/voice/PERSONAPLEX_INTEGRATION.md).

    Never authoritative: its own words are a 7B chat model's, without NeuroSciFi's evidence, memory or tools, so the
    bridge never routes a question to it. Only a loopback-published container may be configured (upstream binds every
    interface and has no auth); the voice prompt is chosen from an allow-list, never a file name from a request.
    Upstream main is not real-time on GB10 (RTF 1.24-1.84, audit §6.1); there is no streaming client here."""
    kind = "duplex"
    name = "PersonaPlex-7B (experimental)"

    def __init__(self, url, enabled, voice):
        super().__init__(url, "unused", read_timeout=5.0)
        self.enabled, self.voice = enabled, voice

    def session_url(self, persona: str) -> str:
        if self.voice not in vconfig.PERSONAPLEX_VOICES:
            raise EngineError("bad_request", "voice not on the allow-list")
        query = urlencode({"voice_prompt": f"{self.voice}.pt", "text_prompt": persona[:500]})
        return f"{self.url.replace('http', 'ws', 1)}/api/chat?{query}"

    def health(self, probe=False) -> dict:
        if not self.enabled:
            return self._health("disabled", "experimental engine; off unless the owner enables it", authoritative=False)
        if not self.configured:
            return self._health("not_configured", "PersonaPlex URL must be loopback", authoritative=False)
        started = time.perf_counter()
        try:
            self.request("GET", "/", auth=False, timeout=3, record=False)
        except EngineError as err:
            return self._health(err.code, str(err), authoritative=False)
        return self._health("ready", "reachable; not real-time on upstream code (audit §6.1)",
                            time.perf_counter() - started, authoritative=False)


class Engines:
    """The engines for one individual's settings; rebuilt when the settings or key change."""

    def __init__(self, cfg: dict):
        try:
            key = vconfig.read_key(cfg)
        except Exception:      # a world-readable key file: treat as not configured (health says why)
            key = None
        self.cfg = cfg
        key_file = vconfig.key_path(cfg)
        self.asr = WhisperASR(cfg["asr_url"], key, cfg["asr_model"], key_file=key_file)
        self.tts = KokoroTTS(cfg["tts_url"], key, key_file=key_file)
        self.duplex = PersonaPlexEngine(cfg["personaplex_url"], cfg["personaplex"], cfg["personaplex_voice"])
        self.signature = (cfg["asr_url"], cfg["asr_model"], cfg["tts_url"], cfg["personaplex"],
                          cfg["personaplex_url"], cfg["personaplex_voice"], key)

    def health(self, probe=False) -> dict:
        return {"asr": self.asr.health(probe), "tts": self.tts.health(probe), "duplex": self.duplex.health(probe)}
