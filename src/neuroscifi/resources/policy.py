"""Resource scheduling policy (RD-46, directive §14; docs/architecture/RESOURCE_SCHEDULING.md).

Policies (all deterministic; each refusal carries its reason):
(a) audio priority. While a voice session is capturing or speaking (audio_active: voice.state.snapshot() of this
    process, or the audio beacon another process on the host wrote less than BEACON_STALE seconds ago):
    - background_gate() refuses to START new autonomous research (curiosity, prediction refreshes, idle
      investigations): the worker does not claim them, autonomy.admit makes them wait, the timer skips them.
      Tasks the owner asked for (console, CLI, Pocket, monitors) start and run as usual; an autonomous task
      already running continues.
    - background_model_slot() caps background model calls (reasoning of autonomous tasks) at
      BACKGROUND_MODEL_SLOTS_AUDIO at a time across processes (file locks); without audio, BACKGROUND_MODEL_SLOTS.
      A call that gets no slot before its deadline is not made (the report keeps its extractive answer and says
      why). Foreground model calls never take a slot.
(b) memory pressure. At "critical" (monitor.pressure) background_gate() refuses background autonomy and
    engine_gate() refuses optional engines (the headless Chromium renderer); the refusal reason names the measured
    numbers. User-initiated research continues, read without rendering.
(c) idle unload. Opt-in, per service: resources/idle.py (never while a voice session is open or a task runs;
    voice_session_active() below tells it about voice sessions in other processes).

AudioBeacon (run by the console, which hosts the voice hub) writes the in-process audio state (and whether any voice
session is open) to <db>.resources/audio.json when it changes (checked every BEACON_POLL s) and every BEACON_HEARTBEAT s
while either is on, so a worker or the idle policy in another process honours them too."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading
import time

from . import monitor, shared

BACKGROUND_MODEL_SLOTS = 2          # background model calls at a time (= the default concurrent autonomy cap)
BACKGROUND_MODEL_SLOTS_AUDIO = 1    # ... while a voice session is capturing or speaking
SLOT_POLL = 0.25
SLOT_WAIT = 30.0                    # longest wait for a background model slot (bounded further by the deadline)
BEACON_STALE = 10.0
BEACON_HEARTBEAT = 3.0
BEACON_POLL = 1.0
AUDIO_REASON = "audio priority: a voice session is capturing or speaking"


def db_file(db) -> str | None:
    """The main database file of an open sqlite connection (None for an in-memory database)."""
    for _, name, file in db.execute("PRAGMA database_list").fetchall():
        if name == "main":
            return file or None
    return None


def _beacon_path(db_path) -> Path | None:
    if not db_path:
        return None
    return Path(f"{db_path}.resources") / "audio.json"


def _active(snap) -> bool:
    return bool(isinstance(snap, dict) and snap.get("session_active") and
                (snap.get("mic_capturing") or snap.get("speaking") or snap.get("user_speaking")))


def local_audio() -> bool:
    """This process's voice hub has a session that is capturing or speaking (voice.state.snapshot)."""
    try:
        from ..voice import state as vstate
        return _active(vstate.snapshot())
    except Exception:          # the voice module is optional; no snapshot claims no audio
        return False


def local_session() -> bool:
    """This process's voice hub has an open voice session (any principal's)."""
    try:
        from ..voice import state as vstate
        return vstate.any_session()
    except Exception:          # the voice module is optional
        return False


def local_used() -> float | None:
    """When an open voice session of this process was last used (voice.state.last_used), None when none is open."""
    try:
        from ..voice import state as vstate
        return vstate.last_used()
    except Exception:          # the voice module is optional
        return None


def _read_beacon(db_path, now) -> dict | None:
    """The beacon when it is fresh (written less than BEACON_STALE seconds ago), else None."""
    path = _beacon_path(db_path)
    if path is None:
        return None
    try:
        beacon = shared.read_json(path)       # no link, no FIFO, bounded, not too deep (review L4, L4c)
    except (OSError, ValueError):
        return None
    now = time.time() if now is None else now
    at = beacon.get("at") if isinstance(beacon, dict) else None
    if isinstance(at, bool) or not isinstance(at, (int, float)) or not 0 <= now - at <= BEACON_STALE:
        return None
    return beacon


def _beacon(db_path, key, now) -> bool:
    beacon = _read_beacon(db_path, now)
    return bool(beacon is not None and beacon.get(key))


def audio_active(db_path=None, *, now=None) -> bool:
    return local_audio() or _beacon(db_path, "active", now)


def _in_use(used_at, now, quiet) -> bool:
    """An open session counts unless it is known to have been quiet for longer than `quiet` seconds: no bound
    (None), or no time of last use (a beacon written before it was published), counts as in use."""
    if quiet is None or isinstance(used_at, bool) or not isinstance(used_at, (int, float)):
        return True
    return now - used_at <= quiet


def voice_session_active(db_path=None, *, now=None, quiet=None) -> bool:
    """A voice session is open in this process or in the process whose beacon is fresh (the idle policy's guard).
    With `quiet` (seconds), a session counts only while it was used within that time: a phone that keeps a voice
    session open without using it does not hold off idle unloading for ever (§6 item 13; Idle.voice_quiet)."""
    now = time.time() if now is None else now
    if local_session() and _in_use(local_used(), now, quiet):
        return True
    beacon = _read_beacon(db_path, now)
    return bool(beacon is not None and beacon.get("session") and _in_use(beacon.get("used_at"), now, quiet))


def background_gate(db_path=None, *, sample=None, audio=None) -> dict:
    """{"allowed", "reason", "why" ("audio" | "memory" | None), "level", "audio"}: may NEW background
    (autonomous) research start now?"""
    s = sample or monitor.sample()
    audio = audio_active(db_path) if audio is None else audio
    reason = why = None
    if audio:
        reason, why = AUDIO_REASON + "; new autonomous research waits until it ends", "audio"
    elif s["level"] == "critical":
        reason, why = ("memory pressure critical (" + "; ".join(s["reasons"]) + "); background autonomy is not "
                       "started"), "memory"
    return {"allowed": reason is None, "reason": reason, "why": why, "level": s["level"], "audio": audio}


def engine_gate(engine: str, *, sample=None) -> tuple[bool, str | None]:
    """May an optional engine (the Chromium renderer) start now? Refused only under critical memory pressure."""
    s = sample or monitor.sample()
    if s["level"] == "critical":
        return False, f"{engine} not started: memory pressure critical (" + "; ".join(s["reasons"]) + ")"
    return True, None


def model_slots(db_path=None) -> int:
    return BACKGROUND_MODEL_SLOTS_AUDIO if audio_active(db_path) else BACKGROUND_MODEL_SLOTS


@contextmanager
def background_model_slot(db_path, *, deadline: float | None = None, sleep=time.sleep):
    """Hold one background model slot (an exclusive flock on <db>.resources/model-slot-<i>.lock, i < the current
    cap) while the block runs; yields the slot number, or None when none was free before
    min(deadline, now + SLOT_WAIT) (time.monotonic). Without a db_path there is nothing to share: slot 0."""
    import fcntl
    if not db_path:
        yield 0
        return
    folder = shared.ensure_folder(f"{db_path}.resources")
    give_up = time.monotonic() + SLOT_WAIT
    if deadline is not None:
        give_up = min(give_up, deadline)
    held = None
    while held is None:
        for i in range(model_slots(db_path)):
            fd = shared.open_file(folder / f"model-slot-{i}.lock", os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                continue
            held = (i, fd)
            break
        if held is None:
            if time.monotonic() >= give_up:
                break
            sleep(SLOT_POLL)
    try:
        yield None if held is None else held[0]
    finally:
        if held is not None:
            fcntl.flock(held[1], fcntl.LOCK_UN)
            os.close(held[1])


class AudioBeacon:
    """Publishes this process's audio state for workers in other processes (see the module doc)."""

    def __init__(self, db_path, *, clock=time.time):
        self.path = _beacon_path(db_path)
        self.clock = clock
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="nsf-audio-beacon", daemon=True)
        self.last = None

    def start(self):
        self.thread.start()
        return self

    def write(self, active: bool, session: bool = False, used_at: float | None = None) -> None:
        shared.ensure_folder(self.path.parent)
        record = {"active": bool(active), "session": bool(session), "at": self.clock(), "pid": os.getpid()}
        if session and used_at is not None:
            record["used_at"] = used_at          # when an open session was last used (voice_session_active's quiet)
        shared.write_text(self.path, json.dumps(record))

    def tick(self) -> None:
        """Write when audio or an open session starts or stops and every BEACON_HEARTBEAT seconds while either is on;
        nothing is written before the first voice activity (an idle console leaves no file behind)."""
        flags = (local_audio(), local_session())
        now = time.monotonic()
        if self.last is None and not any(flags):
            return
        if self.last is None or flags != self.last[0] or (any(flags) and now - self.last[1] >= BEACON_HEARTBEAT):
            try:
                self.write(*flags, local_used())
                self.last = (flags, now)
            except OSError:
                pass

    def _loop(self):
        while not self.stop.wait(BEACON_POLL):
            self.tick()

    def close(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)
        if self.last is not None and any(self.last[0]):
            try:
                self.write(False, False)
            except OSError:
                pass


__all__ = ["AUDIO_REASON", "AudioBeacon", "BACKGROUND_MODEL_SLOTS", "BACKGROUND_MODEL_SLOTS_AUDIO", "audio_active",
           "background_gate", "background_model_slot", "db_file", "engine_gate", "local_audio", "local_session",
           "model_slots", "voice_session_active"]
