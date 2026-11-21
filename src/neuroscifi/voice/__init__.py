"""Voice Cortex (v0.7.0, ADR-037 draft): speech engines, the Cognitive Voice Bridge, transcripts and capture
ownership. NeuroSciFi stays authoritative for facts, tools, memory and permissions; speech engines only transcribe
and render. See docs/voice/COGNITIVE_VOICE_BRIDGE.md and docs/voice/STREAMING_AND_TRANSCRIPTS.md."""

from pathlib import Path as _Path

WEB = _Path(__file__).resolve().parent / "web"
WEB_MODULES = ("voice-client.mjs", "capture-worklet.mjs", "voice-ui.mjs", "voice.css", "pocket-voice.mjs")


def assets() -> dict:
    """Browser files served by the console and Pocket at /voice/<name>: {url path: (absolute file, MIME type)}."""
    return {f"/voice/{name}": (str(WEB / name), "text/css; charset=utf-8" if name.endswith(".css")
                               else "text/javascript; charset=utf-8") for name in WEB_MODULES}
