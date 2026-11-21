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
(c) idle unload. Not automatic: see RESOURCE_SCHEDULING.md (the voice containers are owner-run
    `deploy/voice/run.sh stop|start`; vLLM is never unloaded by NeuroSciFi).

AudioBeacon (run by the console, which hosts the voice hub) writes the in-process audio state to
<db>.resources/audio.json when it changes (checked every BEACON_POLL s) and every BEACON_HEARTBEAT s while active, so a
worker in another process (the neuroscifi-webcortex service) honours audio priority too."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading
import time

from . import monitor

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


def audio_active(db_path=None, *, now=None) -> bool:
    if local_audio():
        return True
    path = _beacon_path(db_path)
    if path is None:
        return False
    try:
        beacon = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    now = time.time() if now is None else now
    at = beacon.get("at") if isinstance(beacon, dict) else None
    return bool(isinstance(at, (int, float)) and 0 <= now - at <= BEACON_STALE and beacon.get("active"))


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
    folder = Path(f"{db_path}.resources")
    folder.mkdir(mode=0o700, exist_ok=True)
    give_up = time.monotonic() + SLOT_WAIT
    if deadline is not None:
        give_up = min(give_up, deadline)
    held = None
    while held is None:
        for i in range(model_slots(db_path)):
            fd = os.open(folder / f"model-slot-{i}.lock", os.O_RDWR | os.O_CREAT, 0o600)
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

    def write(self, active: bool) -> None:
        self.path.parent.mkdir(mode=0o700, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"active": bool(active), "at": self.clock(), "pid": os.getpid()}),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    def tick(self) -> None:
        """Write when audio starts or stops and every BEACON_HEARTBEAT seconds while it runs; nothing is written
        before the first voice activity (an idle console leaves no file behind)."""
        active = local_audio()
        now = time.monotonic()
        if self.last is None and not active:
            return
        if self.last is None or active != self.last[0] or (active and now - self.last[1] >= BEACON_HEARTBEAT):
            try:
                self.write(active)
                self.last = (active, now)
            except OSError:
                pass

    def _loop(self):
        while not self.stop.wait(BEACON_POLL):
            self.tick()

    def close(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)
        if self.last is not None and self.last[0]:
            try:
                self.write(False)
            except OSError:
                pass


__all__ = ["AUDIO_REASON", "AudioBeacon", "BACKGROUND_MODEL_SLOTS", "BACKGROUND_MODEL_SLOTS_AUDIO", "audio_active",
           "background_gate", "background_model_slot", "db_file", "engine_gate", "local_audio", "model_slots"]
