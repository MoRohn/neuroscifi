"""The audio beacon of every process that hosts a voice hub: the console and the Pocket bridge (RD-46).

resources.policy.AudioBeacon writes this process's audio state to <db>.resources/audio.json, which the research
worker and the idle unloader of other processes read (policy._beacon: the top-level "active", "session" and "at").
With two hubs on one individual a plain write would clobber: the console, idle, would write "no audio" over the
Pocket bridge's live session until the bridge's next heartbeat, and a background task could start or a speech
service be unloaded in that gap. SharedAudioBeacon keeps one entry per writing process in the same file ("writers",
under an flock on audio.lock) and writes the top level as the OR of the entries of live processes, so readers stay
unchanged and each hub's state counts until that hub says otherwise or its process is gone. It also publishes the
audio of Pocket sessions, which the owner-only Presence snapshot (and so policy.local_audio) leaves out."""
from __future__ import annotations

import fcntl
import json
import math
import os
import time

from ..resources import shared
from ..resources.policy import BEACON_HEARTBEAT, BEACON_STALE, AudioBeacon
from . import state as vstate


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except PermissionError:
        return True             # it exists, under another user
    except (ProcessLookupError, ValueError, TypeError, OverflowError):
        return False
    return True


def _number(value) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:       # an int too large for a float (review1b R1)
        return False


class SharedAudioBeacon(AudioBeacon):
    """AudioBeacon whose writes merge with the other processes' entries instead of replacing them."""

    def __init__(self, db_path, *, clock=time.time, pid=None):
        super().__init__(db_path, clock=clock)
        self.pid = os.getpid() if pid is None else int(pid)

    def tick(self) -> None:
        """AudioBeacon.tick with this process's audio taken from every voice session (state.any_audio), not only
        the owner's (policy.local_audio reads the owner-only Presence snapshot, so a Pocket session's audio would
        be published as none)."""
        flags = (vstate.any_audio(), vstate.any_session())
        now = time.monotonic()
        if self.last is None and not any(flags):
            return
        if self.last is None or flags != self.last[0] or (any(flags) and now - self.last[1] >= BEACON_HEARTBEAT):
            try:
                self.write(*flags, vstate.last_used())
                self.last = (flags, now)
            except OSError:
                pass

    def write(self, active: bool, session: bool = False, used_at: float | None = None) -> None:
        shared.ensure_folder(self.path.parent)       # the folder's own mode decides who may read what is written
        lock = shared.open_file(self.path.parent / "audio.lock", os.O_RDWR)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                # another account's file is not merged in (policy._read_beacon reads it the same way)
                current = shared.read_json(self.path, owners=shared.writers(self.db_path))
            except (OSError, ValueError):
                current = {}
            current = current if isinstance(current, dict) else {}
            writers = current.get("writers")
            if not isinstance(writers, dict):          # a beacon written by a plain AudioBeacon: its one entry
                writers = {str(current["pid"]): current} if "pid" in current else {}
            now = self.clock()
            entries = {pid: {"active": bool(w.get("active")), "session": bool(w.get("session")), "at": w.get("at"),
                             **({"used_at": w["used_at"]} if _number(w.get("used_at")) else {})}
                       for pid, w in writers.items()
                       if pid != str(self.pid) and isinstance(w, dict) and isinstance(w.get("at"), (int, float))
                       and 0 <= now - w["at"] <= BEACON_STALE and _alive(pid)}
            entries[str(self.pid)] = {"active": bool(active), "session": bool(session), "at": now,
                                      **({"used_at": used_at} if session and _number(used_at) else {})}
            record = {"active": any(e["active"] for e in entries.values()),
                      "session": any(e["session"] for e in entries.values()), "at": now, "pid": self.pid,
                      "writers": entries}
            # The newest use of an open session of any process; an open session whose writer did not say when it
            # was used (an older writer) leaves it out, which the readers count as in use (policy._in_use).
            sessions = [e for e in entries.values() if e["session"]]
            if sessions and all("used_at" in e for e in sessions):
                record["used_at"] = max(e["used_at"] for e in sessions)
            shared.write_text(self.path, json.dumps(record), group_write=False)   # the service's alone (R2)
        finally:
            os.close(lock)                             # releases the flock
