"""Registry of in-flight work that leaves no persisted row while it runs.

The inference ledger records a model call only when it ends, so a call that is streaming right now is invisible to
the database. Code that makes such a call (the gateway's streamed calls, Tier C scene proposals in scene_spec.py,
the voice bridge interpreting a request, an allow-listed model tool executing) registers it here and reports streamed
tokens as they arrive; the aggregator reads snapshot(). Nothing is guessed: an entry exists exactly while its
`with track(...)` block runs.

Across processes (ADR-053): a call made in another process (the research worker of `nsf start` or the systemd
worker) is published, when the caller names the instance file (`publish=<db path>`), to
`<db>.resources/inflight-<pid>.json` the way the audio beacon publishes audio state: written atomically when an entry
starts or ends and at most every PUBLISH_EVERY seconds while tokens stream, removed when the process has none left.
remote(db_path) reads the other processes' files: a file of a process that is gone, or older than STALE seconds, is
ignored, so a killed worker never leaves a call on screen. While a process has entries its file is rewritten every
REFRESH seconds, so a call that streams nothing for longer than STALE stays on screen while it runs. The files hold no
prompt or answer text: activity, subsystem, purpose, task id, start time and a token count.

The folder may be one two accounts write (resources/shared.py), so a file is untrusted input (review1 S4, A11): it is
read only when this process's account or the instance database's owner (the service account) owns it, when the pid in
its name is the pid in its content, and when that process's start time (/proc/<pid>/stat) is the one the file
records, so a reused pid or a forged file is not shown; a remote entry ranks at most as "tool" (REMOTE_PRIORITY_CAP).
"""
from __future__ import annotations

from contextlib import contextmanager
import itertools
import json
import math
import os
from pathlib import Path
import threading
import time

_LOCK = threading.Lock()
_ACTIVE: dict[int, dict] = {}
_IDS = itertools.count(1)
_PUBLISHED: dict[str, float] = {}       # db path -> monotonic time of the last write of this process's file
_WRITE = threading.Lock()               # one write of this process's file at a time, each of the newest entries
_TIMERS: dict[str, threading.Timer] = {}   # db path -> the pending rewrite while entries exist (under _WRITE)
MAX_ENTRIES = 64
PUBLISH_EVERY = 1.0                     # seconds between two writes while tokens stream
STALE = 600.0                           # a file not rewritten for this long is ignored (a call's own deadline is less)
REFRESH = STALE / 4                     # a process with entries rewrites its file at least this often
REMOTE_PRIORITY_CAP = "tool"            # another process's entry never ranks above this (Presence's priority)
MAX_FILES = 32
FILE_LIMIT = 64 * 1024
PRIORITIES = ("communication", "foreground", "tool", "background")
_PUBLIC = ("activity", "subsystem", "purpose", "task_id", "since", "tokens", "last_token_at", "priority", "secondary")


@contextmanager
def track(activity, *, subsystem, purpose, task_id=None, clock=time.time, priority="tool", secondary=(),
          publish=None):
    """Register one unit of in-flight work; yields a callable that adds streamed tokens (default 1). `priority`: its
    Presence rank class (default 'tool'); `publish`: the instance file whose resources folder other processes read."""
    entry = {"activity": activity, "subsystem": subsystem, "purpose": purpose, "task_id": task_id,
             "since": clock(), "tokens": 0, "last_token_at": None,
             "priority": priority if priority in PRIORITIES else "tool", "secondary": list(secondary),
             "_publish": str(publish) if publish else None}
    with _LOCK:
        if len(_ACTIVE) >= MAX_ENTRIES:   # a leak would otherwise grow forever; refuse to track more
            ident = None
        else:
            ident = next(_IDS)
            _ACTIVE[ident] = entry
    if ident is not None and entry["_publish"]:
        _publish(entry["_publish"], force=True)

    def tokens(n=1):
        with _LOCK:
            entry["tokens"] += n
            entry["last_token_at"] = clock()
        if ident is not None and entry["_publish"]:
            _publish(entry["_publish"])
    try:
        yield tokens
    finally:
        if ident is not None:
            with _LOCK:
                _ACTIVE.pop(ident, None)
            if entry["_publish"]:
                _publish(entry["_publish"], force=True)


def snapshot() -> list[dict]:
    with _LOCK:
        return [{k: e[k] for k in _PUBLIC} for e in _ACTIVE.values()]


def _folder(db_path) -> Path:
    return Path(f"{db_path}.resources")


def _publish(db_path, *, force=False):
    """Write (or remove) this process's file for `db_path`; never raises into the caller's work. The entries are
    read and written under one lock, so a late rate-limited write can never put back an entry that ended meanwhile
    (review1 A11); while entries remain, a rewrite is scheduled REFRESH seconds on."""
    with _LOCK:
        last = _PUBLISHED.get(db_path)
        if not force and last is not None and time.monotonic() - last < PUBLISH_EVERY:
            return
    from ..resources import shared
    target = _folder(db_path) / f"inflight-{os.getpid()}.json"
    with _WRITE:
        with _LOCK:
            _PUBLISHED[db_path] = time.monotonic()
            entries = [{k: e[k] for k in _PUBLIC} for e in _ACTIVE.values() if e["_publish"] == db_path]
        try:
            if entries:
                shared.ensure_folder(target.parent)
                shared.write_text(target, json.dumps({"pid": os.getpid(), "started": process_started(os.getpid()),
                                                      "at": time.time(), "entries": entries}, allow_nan=False))
            else:
                try:
                    os.unlink(target)
                except FileNotFoundError:
                    pass
        except (OSError, ValueError):
            pass
        timer = _TIMERS.pop(db_path, None)
        if timer is not None:
            timer.cancel()
        if entries:
            timer = threading.Timer(REFRESH, _publish, args=(db_path,), kwargs={"force": True})
            timer.daemon = True
            timer.name = "nsf-activity-refresh"
            _TIMERS[db_path] = timer
            timer.start()


def process_started(pid) -> int | None:
    """The process's start time in clock ticks after boot (field 22 of /proc/<pid>/stat), or None when it cannot be
    read (no such process, no /proc). With the pid it names one process: a reused pid has another start time."""
    try:
        with open(f"/proc/{int(pid)}/stat", "rb") as fh:
            data = fh.read(4096)
        return int(data[data.rindex(b")") + 2:].split()[19])
    except (OSError, ValueError, IndexError, TypeError, OverflowError):
        return None


def _alive(pid, started=None) -> bool:
    """The process `pid` exists and is the one that started at `started` (process_started; None where /proc cannot
    say)."""
    try:
        os.kill(int(pid), 0)
    except PermissionError:
        pass                    # it exists, under another account
    except (ProcessLookupError, ValueError, TypeError, OverflowError):
        return False
    return started == process_started(pid)


def _number(value) -> bool:
    """A finite int or float; an int too large for a float (review1b R1) is not a number here."""
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _capped(priority) -> str:
    """A remote entry's priority, at most REMOTE_PRIORITY_CAP (PRIORITIES runs from the highest rank down)."""
    floor = PRIORITIES.index(REMOTE_PRIORITY_CAP)
    return priority if priority in PRIORITIES[floor:] else REMOTE_PRIORITY_CAP


def remote(db_path, now=None) -> list[dict]:
    """In-flight work other live processes published for this instance (see the module docstring); [] without a
    path or folder. Malformed files and entries are skipped."""
    if not db_path:
        return []
    folder = _folder(db_path)
    if not folder.is_dir():
        return []
    from ..resources import shared
    now = time.time() if now is None else now
    owners = shared.writers(db_path)
    out = []
    for path in sorted(folder.glob("inflight-*.json"))[:MAX_FILES]:
        pid = path.stem.removeprefix("inflight-")
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        try:
            data = shared.read_json(path, limit=FILE_LIMIT, owners=owners)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not _number(data.get("at")) or not 0 <= now - data["at"] <= STALE:
            continue
        started = data.get("started")
        if data.get("pid") != int(pid) or not (started is None or _number(started)) or not _alive(pid, started):
            continue
        for e in (data.get("entries") or [])[:MAX_ENTRIES]:
            if not isinstance(e, dict) or not isinstance(e.get("activity"), str) or not _number(e.get("since")):
                continue
            tokens = e.get("tokens")
            out.append({"activity": e["activity"][:32], "subsystem": str(e.get("subsystem") or "reasoning")[:32],
                        "purpose": str(e.get("purpose") or "")[:64],
                        "task_id": e.get("task_id") if isinstance(e.get("task_id"), str) else None,
                        "since": e["since"], "tokens": int(tokens) if _number(tokens) and tokens >= 0 else 0,
                        "priority": _capped(e.get("priority")),
                        "secondary": [s for s in (e.get("secondary") or []) if isinstance(s, str)][:4],
                        "process": int(pid)})
    return out


__all__ = ["process_started", "remote", "snapshot", "track"]
