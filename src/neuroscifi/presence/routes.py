"""Presence endpoints: one efficient channel per app (docs/presence/PRESENCE_CORTEX_ARCHITECTURE.md §Channel).

Console (loopback, per-start token): GET /api/presence?since=<version>&wait=<s> is a long-poll. The handler
re-derives every POLL_STEP seconds on the server and answers as soon as the version differs from `since`, or with
{"unchanged": true} after `wait` seconds; the page keeps exactly one such request open while it is visible.

Pocket (paired devices, 512-byte bodies, 10 s request deadline): POST /api/presence {"since": <version>} is a
conditional poll answered at once; the page asks every 2 s while something runs and every 10 s when idle, and not
at all while hidden. Family scope: only the family's own tasks.
"""
from __future__ import annotations

import re
import time

from . import aggregator

MAX_WAIT = 20.0
POLL_STEP = 0.35
VERSION = re.compile(r"^[0-9a-f]{1,64}$")


def _since(value):
    from ..instance.errors import ValidationError
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not VERSION.match(value):
        raise ValidationError("since must be a version string returned by this endpoint")
    return value


def console_state(c, q):
    """Long-poll handler body for the console route (c: console _Context, q: parsed query)."""
    from ..instance.errors import ValidationError
    unknown = sorted(set(q) - {"since", "wait"})
    if unknown:
        raise ValidationError("unknown query parameter(s): " + ", ".join(unknown))
    since = _since((q.get("since") or [None])[-1])
    try:
        wait = float((q.get("wait") or ["0"])[-1])
    except ValueError:
        raise ValidationError("wait must be a number of seconds") from None
    if not 0 <= wait <= MAX_WAIT:
        raise ValidationError(f"wait must be between 0 and {MAX_WAIT:g} seconds")
    server = c.server
    clock = server.clock if server is not None else time.time
    stop = getattr(server, "worker_stop", None)
    worker = getattr(server, "worker", None)
    deadline = time.monotonic() + wait
    now = c.now
    while True:
        state = aggregator.derive(c.db, c.path, now, worker_alive=(worker.is_alive() if worker is not None else None))
        if since is None or state["version"] != since:
            return state
        if time.monotonic() >= deadline:
            return {"unchanged": True, "version": since, "ts": now}
        if stop is not None:
            if stop.wait(POLL_STEP):
                return {"unchanged": True, "version": since, "ts": now}
        else:
            time.sleep(POLL_STEP)
        now = clock()


def console_scene(c, b):
    """POST /api/presence/scene {"concept": "..."}: a Tier C proposal from the local model (or a fallback)."""
    from ..instance.errors import ValidationError
    from . import scene_spec
    if set(b) != {"concept"}:
        raise ValidationError("body must be {\"concept\": \"...\"}")
    concept = b["concept"]
    if not isinstance(concept, str) or not re.match(scene_spec.CONCEPT, concept):
        raise ValidationError("concept must be 1-48 lowercase letters and spaces")
    return scene_spec.propose(c.db, c.path, concept)


def pocket_state(server, token, data):
    """(status, body) for Pocket's POST /api/presence. PermissionError (unknown credential) propagates -> 403."""
    family = server.store.family(token)
    if not set(data) <= {"since"}:
        raise ValueError()
    since = data.get("since")
    if since is not None and (not isinstance(since, str) or not VERSION.match(since)):
        raise ValueError()
    from ..pocket.server import research_db
    db = research_db(server)
    if db is None:
        state = aggregator.unavailable("family", "Research is not shared with linked devices; nothing to show")
    else:
        from ..webcortex import state as wstate
        try:
            digest = wstate.family_digest(db, family)
            state = aggregator.derive(db, server.instance_db, time.time(), family=digest, voice=False)
        finally:
            db.close()
    if since is not None and state["version"] == since:
        return 200, {"unchanged": True, "version": since}
    return 200, state


__all__ = ["console_scene", "console_state", "pocket_state"]
