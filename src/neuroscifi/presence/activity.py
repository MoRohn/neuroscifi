"""Process-local registry of in-flight work that leaves no persisted row while it runs.

The inference ledger records a model call only when it ends, so a call that is streaming right now is invisible to
the database. Code that makes such a call in this process (today: Tier C scene proposals in scene_spec.py) registers
it here and reports streamed tokens as they arrive; the aggregator reads snapshot(). Nothing here is persisted and
nothing is guessed: an entry exists exactly while its `with track(...)` block runs.
"""
from __future__ import annotations

from contextlib import contextmanager
import itertools
import threading
import time

_LOCK = threading.Lock()
_ACTIVE: dict[int, dict] = {}
_IDS = itertools.count(1)
MAX_ENTRIES = 64


@contextmanager
def track(activity, *, subsystem, purpose, task_id=None, clock=time.time):
    """Register one unit of in-flight work; yields a callable that adds streamed tokens (default 1)."""
    entry = {"activity": activity, "subsystem": subsystem, "purpose": purpose, "task_id": task_id,
             "since": clock(), "tokens": 0, "last_token_at": None}
    with _LOCK:
        if len(_ACTIVE) >= MAX_ENTRIES:   # a leak would otherwise grow forever; refuse to track more
            ident = None
        else:
            ident = next(_IDS)
            _ACTIVE[ident] = entry

    def tokens(n=1):
        with _LOCK:
            entry["tokens"] += n
            entry["last_token_at"] = clock()
    try:
        yield tokens
    finally:
        if ident is not None:
            with _LOCK:
                _ACTIVE.pop(ident, None)


def snapshot() -> list[dict]:
    with _LOCK:
        return [dict(e) for e in _ACTIVE.values()]


__all__ = ["snapshot", "track"]
