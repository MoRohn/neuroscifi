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
ignored, so a killed worker never leaves a call on screen. The files hold no prompt or answer text: activity,
subsystem, purpose, task id, start time and a token count.
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
MAX_ENTRIES = 64
PUBLISH_EVERY = 1.0                     # seconds between two writes while tokens stream
STALE = 600.0                           # a file not rewritten for this long is ignored (a call's own deadline is less)
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
    """Write (or remove) this process's file for `db_path`; never raises into the caller's work."""
    now = time.monotonic()
    with _LOCK:
        last = _PUBLISHED.get(db_path)
        if not force and last is not None and now - last < PUBLISH_EVERY:
            return
        _PUBLISHED[db_path] = now
        entries = [{k: e[k] for k in _PUBLIC} for e in _ACTIVE.values() if e["_publish"] == db_path]
    from ..resources import shared
    target = _folder(db_path) / f"inflight-{os.getpid()}.json"
    try:
        if entries:
            shared.ensure_folder(target.parent)
            shared.write_text(target, json.dumps({"pid": os.getpid(), "at": time.time(), "entries": entries},
                                                 allow_nan=False))
        else:
            try:
                os.unlink(target)
            except FileNotFoundError:
                pass
    except (OSError, ValueError):
        pass


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except PermissionError:
        return True             # it exists, under another account
    except (ProcessLookupError, ValueError, TypeError, OverflowError):
        return False
    return True


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


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
    out = []
    for path in sorted(folder.glob("inflight-*.json"))[:MAX_FILES]:
        pid = path.stem.removeprefix("inflight-")
        if not pid.isdigit() or int(pid) == os.getpid() or not _alive(pid):
            continue
        try:
            data = shared.read_json(path, limit=FILE_LIMIT)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not _number(data.get("at")) or not 0 <= now - data["at"] <= STALE:
            continue
        for e in (data.get("entries") or [])[:MAX_ENTRIES]:
            if not isinstance(e, dict) or not isinstance(e.get("activity"), str) or not _number(e.get("since")):
                continue
            tokens = e.get("tokens")
            out.append({"activity": e["activity"][:32], "subsystem": str(e.get("subsystem") or "reasoning")[:32],
                        "purpose": str(e.get("purpose") or "")[:64],
                        "task_id": e.get("task_id") if isinstance(e.get("task_id"), str) else None,
                        "since": e["since"], "tokens": int(tokens) if _number(tokens) and tokens >= 0 else 0,
                        "priority": e.get("priority") if e.get("priority") in PRIORITIES else "tool",
                        "secondary": [s for s in (e.get("secondary") or []) if isinstance(s, str)][:4],
                        "process": int(pid)})
    return out


__all__ = ["remote", "snapshot", "track"]
