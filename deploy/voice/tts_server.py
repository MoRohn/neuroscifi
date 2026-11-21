"""Kokoro-82M text-to-speech over loopback HTTP (NeuroSciFi Voice Cortex, deploy/voice).

Runs inside the neuroscifi-voice-tts container; the host publishes it on 127.0.0.1 only.

  GET  /health            {"status": "ready"|"loading"|"failed", ...}   no auth, no text
  GET  /v1/voices         allow-listed voices                            Bearer key
  POST /v1/audio/speech   {"input", "voice", "speed", "response_format": "wav"|"pcm"} -> 24 kHz mono audio

Security: a Bearer key (NSF_TTS_API_KEY) is required for every route but /health; JSON bodies of at most 8 KiB and
input of at most 1000 characters; the voice must be on the allow-list (a file name is never taken from a request);
nothing is logged except start-up and failures (no text, no audio). Synthesis is serialised with a lock.
Weights are read from NSF_TTS_MODEL_DIR (mounted read-only); Hugging Face access is off.
"""
import hmac
import json
import os
from pathlib import Path
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SAMPLE_RATE = 24000
MAX_BODY = 8192
MAX_INPUT = 1000
# American and British English voices shipped with Kokoro-82M v1.0 (VOICES.md). Other languages need other G2P.
VOICES = ("af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "am_adam", "am_michael", "am_onyx",
          "bf_emma", "bf_isabella", "bm_george", "bm_lewis")


def wav_bytes(samples, rate=SAMPLE_RATE):
    """16-bit PCM mono WAV from float samples in [-1, 1] (a list, array or numpy array)."""
    pcm = pcm16(samples)
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack(
        "<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" + struct.pack("<I", len(pcm))
    return header + pcm


def pcm16(samples):
    try:
        import numpy as np
        arr = np.clip(np.asarray(samples, dtype="float32"), -1.0, 1.0)
        return (arr * 32767.0).astype("<i2").tobytes()
    except ImportError:
        return b"".join(struct.pack("<h", int(max(-1.0, min(1.0, x)) * 32767)) for x in samples)


class Engine:
    """Kokoro pipelines per language code ('a' American, 'b' British), loaded once."""

    def __init__(self, model_dir, device):
        import torch
        from kokoro import KModel, KPipeline
        self.device = device
        self.dir = Path(model_dir)
        model = KModel(config=str(self.dir / "config.json"), model=str(self.dir / "kokoro-v1_0.pth")).to(device).eval()
        self.pipes = {code: KPipeline(lang_code=code, model=model, device=device) for code in ("a", "b")}
        self.torch = torch

    def synthesize(self, text, voice, speed=1.0):
        pipe = self.pipes["b" if voice.startswith("b") else "a"]
        chunks = []
        with self.torch.inference_mode():
            for result in pipe(text, voice=str(self.dir / "voices" / f"{voice}.pt"), speed=speed):
                if result.audio is not None:
                    chunks.append(result.audio.detach().cpu().numpy())
        if not chunks:
            return []
        import numpy as np
        return np.concatenate(chunks)


class State:
    def __init__(self, key, engine_factory):
        self.key, self.factory = key, engine_factory
        self.engine, self.status, self.error = None, "loading", None
        self.lock = threading.Lock()
        self.started = time.time()
        self.requests = self.failures = 0
        self.synth_seconds = self.audio_seconds = 0.0

    def load(self):
        try:
            engine = self.factory()
            engine.synthesize("Ready.", VOICES[0])   # warm-up: the first call loads spaCy and compiles kernels
            self.engine, self.status = engine, "ready"
            print(f"tts: ready on {getattr(engine, 'device', '?')} after {time.time() - self.started:.1f}s",
                  file=sys.stderr, flush=True)
        except Exception as exc:   # reported by /health; the process stays up so the failure is visible
            self.status, self.error = "failed", f"{type(exc).__name__}: {' '.join(str(exc).split())[:200]}"
            print(f"tts: load failed: {self.error}", file=sys.stderr, flush=True)


class Handler(BaseHTTPRequestHandler):
    timeout = 30
    server_version = "nsf-tts"
    sys_version = ""

    def log_message(self, *args):
        pass

    def reply(self, code, body, mime="application/json", extra=None):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        for name, value in {"Content-Type": mime, "Content-Length": str(len(body)), "Cache-Control": "no-store",
                            "X-Content-Type-Options": "nosniff", **(extra or {})}.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        auth = self.headers.get("Authorization", "")
        key = self.server.state.key
        return bool(key) and auth.startswith("Bearer ") and hmac.compare_digest(auth[7:].encode(), key.encode())

    def do_GET(self):
        st = self.server.state
        if self.path == "/health":
            engine = st.engine
            return self.reply(200 if st.status == "ready" else 503, {
                "status": st.status, "error": st.error, "device": getattr(engine, "device", None),
                "sample_rate": SAMPLE_RATE, "requests": st.requests, "failures": st.failures,
                "rtf": round(st.synth_seconds / st.audio_seconds, 4) if st.audio_seconds else None})
        if not self.authorized():
            return self.reply(401, {"error": "unauthorised"})
        if self.path == "/v1/voices":
            return self.reply(200, {"voices": list(VOICES), "default": VOICES[0]})
        return self.reply(404, {"error": "not found"})

    def do_POST(self):
        st = self.server.state
        if self.path != "/v1/audio/speech":
            return self.reply(404, {"error": "not found"})
        if not self.authorized():
            return self.reply(401, {"error": "unauthorised"})
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            size = -1
        if not 2 <= size <= MAX_BODY or self.headers.get_content_type() != "application/json":
            self.close_connection = True
            return self.reply(400, {"error": f"JSON body of at most {MAX_BODY} bytes required"})
        try:
            body = json.loads(self.rfile.read(size))
        except (ValueError, UnicodeDecodeError):
            body = None
        if not isinstance(body, dict) or set(body) - {"input", "voice", "speed", "response_format", "model"}:
            return self.reply(400, {"error": "invalid body"})
        text, voice = body.get("input"), body.get("voice", VOICES[0])
        speed, fmt = body.get("speed", 1.0), body.get("response_format", "wav")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_INPUT:
            return self.reply(400, {"error": f"input must be 1..{MAX_INPUT} characters"})
        if voice not in VOICES:
            return self.reply(400, {"error": "voice not allowed"})
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not 0.5 <= speed <= 2.0:
            return self.reply(400, {"error": "speed must be between 0.5 and 2.0"})
        if fmt not in ("wav", "pcm"):
            return self.reply(400, {"error": "response_format must be wav or pcm"})
        if st.status != "ready":
            return self.reply(503, {"error": f"engine {st.status}"}, extra={"Retry-After": "5"})
        started = time.perf_counter()
        try:
            with st.lock:
                audio = st.engine.synthesize(text, voice, float(speed))
        except Exception as exc:
            st.failures += 1
            print(f"tts: synthesis failed: {type(exc).__name__}", file=sys.stderr, flush=True)
            return self.reply(500, {"error": "synthesis failed"})
        took = time.perf_counter() - started
        seconds = len(audio) / SAMPLE_RATE
        st.requests += 1
        st.synth_seconds += took
        st.audio_seconds += seconds
        data = wav_bytes(audio) if fmt == "wav" else pcm16(audio)
        self.reply(200, data, "audio/wav" if fmt == "wav" else "audio/L16;rate=24000;channels=1",
                   {"X-Synthesis-Seconds": f"{took:.4f}", "X-Audio-Seconds": f"{seconds:.3f}"})


def main():
    key_file = os.environ.get("NSF_TTS_API_KEY_FILE")
    key = os.environ.get("NSF_TTS_API_KEY") or (Path(key_file).read_text().strip() if key_file else "")
    if not key:
        print("tts: NSF_TTS_API_KEY is required", file=sys.stderr)
        raise SystemExit(2)
    model_dir = os.environ.get("NSF_TTS_MODEL_DIR", "/models/kokoro-82m")
    device = os.environ.get("NSF_TTS_DEVICE", "cuda")
    threads = os.environ.get("NSF_TTS_THREADS")
    if threads:
        import torch
        torch.set_num_threads(int(threads))
    state = State(key, lambda: Engine(model_dir, device))
    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("NSF_TTS_PORT", "8000"))), Handler)
    server.daemon_threads = True
    server.state = state
    threading.Thread(target=state.load, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
