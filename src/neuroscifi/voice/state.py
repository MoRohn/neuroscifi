"""In-process voice state for the Presence Cortex (contract with Lane P).

snapshot() -> {mode, session_active, capture_device, mic_capturing, output_muted, speaking, user_speaking,
               interrupted_at, updated_at}
`mode` is "voice_text" | "voice_in_text_out" | "text_only". Values are facts reported by the voice hub of this
process: the session and mode from the server, the capture device from the ownership lease, and mic_capturing /
speaking / user_speaking / output_muted as last reported by the client that holds the session (the client reports
every change; the server clears them when the session ends, the mic lease is released or a client goes silent for
STALE_SECONDS). With no voice session in this process the result is text-only and idle.
"""
from __future__ import annotations

import threading
import time

STALE_SECONDS = 30.0
FIELDS = ("mode", "session_active", "capture_device", "mic_capturing", "output_muted", "speaking", "user_speaking",
          "interrupted_at", "updated_at")
_LOCK = threading.Lock()
_SESSIONS: dict[str, dict] = {}


def _idle(mode="text_only"):
    return {"mode": mode, "session_active": False, "capture_device": None, "mic_capturing": False,
            "output_muted": False, "speaking": False, "user_speaking": False, "interrupted_at": None,
            "updated_at": None}


def update(session_id: str, **changes) -> dict:
    """Record changes for one session (unknown keys are ignored). Returns the session's state."""
    with _LOCK:
        cur = _SESSIONS.setdefault(session_id, _idle())
        changed = False
        for key, value in changes.items():
            if key in FIELDS and key != "updated_at" and cur.get(key) != value:
                cur[key] = value
                changed = True
        if changed or cur["updated_at"] is None:
            cur["updated_at"] = time.time()
        cur["_seen"] = time.monotonic()
        return {k: cur[k] for k in FIELDS}


def end(session_id: str) -> None:
    with _LOCK:
        _SESSIONS.pop(session_id, None)


def snapshot() -> dict:
    """The most recently updated active session of this process, else text-only idle."""
    with _LOCK:
        now = time.monotonic()
        live = [s for s in _SESSIONS.values() if s.get("session_active")]
        if not live:
            return _idle()
        cur = max(live, key=lambda s: s["updated_at"] or 0)
        out = {k: cur[k] for k in FIELDS}
        if now - cur.get("_seen", now) > STALE_SECONDS:   # the client stopped reporting: claim no live audio
            out.update(mic_capturing=False, speaking=False, user_speaking=False)
        return out


def reset() -> None:
    """Tests only."""
    with _LOCK:
        _SESSIONS.clear()
