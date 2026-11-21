"""In-process voice state for the Presence Cortex (contract with Lane P).

snapshot() -> {mode, session_active, capture_device, mic_capturing, output_muted, speaking, user_speaking,
               interrupted_at, updated_at}       (the owner's sessions only: what the owner's Presence shows)
get(session_id) -> the same fields for one session (a Pocket principal's own status)
`mode` is "voice_text" | "voice_in_text_out" | "text_only". Values are facts reported by the voice hub of this
process: the session and mode from the server, the capture device from the ownership lease, and mic_capturing /
speaking / user_speaking / output_muted as last reported by the client that holds the session (the client reports
every change; the server clears them when the session ends, the mic lease is released or a client goes silent for
STALE_SECONDS). With no voice session in this process the result is text-only and idle.
engines() -> {"asr": state, "tts": state} as the hub last measured them (VoiceHub.health, which every open voice
client polls), or None when nothing was measured for ENGINES_STALE seconds: then nothing is claimed either way.
"""
from __future__ import annotations

import threading
import time

STALE_SECONDS = 30.0
ENGINES_STALE = 60.0
FIELDS = ("mode", "session_active", "capture_device", "mic_capturing", "output_muted", "speaking", "user_speaking",
          "interrupted_at", "updated_at")
_LOCK = threading.Lock()
_SESSIONS: dict[str, dict] = {}
_ENGINES: dict = {}


def _idle(mode="text_only"):
    return {"mode": mode, "session_active": False, "capture_device": None, "mic_capturing": False,
            "output_muted": False, "speaking": False, "user_speaking": False, "interrupted_at": None,
            "updated_at": None}


def update(session_id: str, *, owner: bool = True, **changes) -> dict:
    """Record changes for one session (unknown keys are ignored). Returns the session's state. `owner` False marks
    a non-owner (Pocket) session: it never becomes what snapshot() shows."""
    with _LOCK:
        cur = _SESSIONS.setdefault(session_id, _idle())
        cur["_owner"] = bool(owner)
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


def get(session_id: str) -> dict:
    """One session's state (text-only idle when unknown)."""
    with _LOCK:
        cur = _SESSIONS.get(session_id)
        return _idle() if cur is None else {k: cur[k] for k in FIELDS}


def snapshot() -> dict:
    """The most recently updated active OWNER session of this process, else text-only idle. Non-owner sessions
    never change what the owner's Presence shows."""
    with _LOCK:
        now = time.monotonic()
        live = [s for s in _SESSIONS.values() if s.get("session_active") and s.get("_owner", True)]
        if not live:
            return _idle()
        cur = max(live, key=lambda s: s["updated_at"] or 0)
        out = {k: cur[k] for k in FIELDS}
        if now - cur.get("_seen", now) > STALE_SECONDS:   # the client stopped reporting: claim no live audio
            out.update(mic_capturing=False, speaking=False, user_speaking=False)
        return out


def set_engines(health: dict) -> None:
    """Record the speech engines' measured states (the hub's health result)."""
    if not isinstance(health, dict):
        return
    states = {k: (health.get(k) or {}).get("state") for k in ("asr", "tts")}
    with _LOCK:
        _ENGINES.clear()
        _ENGINES.update(states, _at=time.monotonic())


def engines() -> dict | None:
    """The last measured {"asr", "tts"} states, or None when none were measured in the last ENGINES_STALE s."""
    with _LOCK:
        at = _ENGINES.get("_at")
        if at is None or time.monotonic() - at > ENGINES_STALE:
            return None
        return {k: _ENGINES.get(k) for k in ("asr", "tts")}


def reset() -> None:
    """Tests only."""
    with _LOCK:
        _SESSIONS.clear()
        _ENGINES.clear()
