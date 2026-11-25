"""Selective broadcast (Cognitive Genesis §6.1, ADR-053): one function decides which events the other subsystems see.

A producer offers an event: offer(db, kind, now=..., signals=..., deliver=...). The event has a kind (KINDS) and a
salience value (score(); a producer may pass its own). Only an event at or above the owner's threshold is made
available beyond its producer, and only through the channels that already exist:

  - deliver(): the producer's own delivery, today an owner question (`pending`), which Presence shows as user-critical,
    the console lists under Questions and Pocket pushes for (web:autonomy:*);
  - web_events: the offer is logged as a 'decision' event of the task (task_id NULL allowed), which the console's task
    view and the self-description read;
  - state_version: the producer's transaction bumps it once (both producers always did), the change signal the
    worker's data_version wait and every reader see.

Everything below the threshold stays local: the producer's own records keep it (the task's report, a monitor's
'finding' event) and the log says why it went no further. Nothing else is broadcast: no internal state, no counter, no
model text. Contention: when several broadcast events are live at once, Presence's priority ranking decides which one
is shown first (presence.aggregator.arbitrate: rank, then the newest); the log records which one won and why.

Modes (InstanceConfig.broadcast; `nsf config --broadcast off|log|on`):
  off   (default) the producer delivers exactly as before; nothing is scored or logged. Behaviour is unchanged.
  log   the producer delivers exactly as before; the offer is scored and logged with what "on" would have done
        (display only: a decision event per offer, no other change).
  on    selective: deliver only at or above InstanceConfig.broadcast_threshold and within MAX_PER_HOUR broadcasts;
        otherwise the event stays local and the log says so.
Why off by default: no benchmark shows that holding a finding back helps the owner, and a finding the owner switched
autonomy on for must not be dropped silently (R1-01); with "on" nothing is dropped silently either (it is logged and
listed by the self-description), but the owner is not interrupted for it.

The salience seam: score(kind, signals) is a small local function over the producer's own counts (SIGNAL_WEIGHTS),
1 - exp(-sum of weights). Lane I's salience record replaces it in the integration pass: a producer passes salience=
(the record's value) and salience_inputs=, or score() is pointed at it; offer() and the log do not change.
"""
from __future__ import annotations

import math

KINDS = {
    "autonomy.finding": {"what": "a finished autonomous task's meaningful finding (autonomy.after_task)",
                         "priority": "user_critical"},
    "monitor.finding": {"what": "a monitor run's meaningful change (scheduler._after_monitor_run)",
                        "priority": "user_critical"},
}
MODES = ("off", "log", "on")
DEFAULT_THRESHOLD = 0.5
MAX_PER_HOUR = 12               # broadcasts per rolling hour, every kind together
FRESH = 900.0                   # seconds an open question competes (presence.aggregator.FRESH_QUESTION)
# What each producer signal adds to the salience sum (per item, capped at CAP items per signal).
SIGNAL_WEIGHTS = {
    "resolved_contradictions": 0.6,   # a contradiction settled by new evidence
    "prediction_errors": 0.6,         # a prediction that turned out wrong (surprise)
    "changed_values": 0.5,            # a monitored value changed
    "superseded": 0.35,               # knowledge replaced by a newer version
    "settled_predictions": 0.25,      # a prediction confirmed
    "new_verified": 0.2,              # a newly verified statement (knowledge candidate)
    "new_claims": 0.15,               # a new corroborated subject
}
CAP = 5


def mode(db) -> str:
    from .instance import store
    value = getattr(store.config(db), "broadcast", "off")
    return value if value in MODES else "off"


def threshold(db) -> float:
    from .instance import store
    value = getattr(store.config(db), "broadcast_threshold", DEFAULT_THRESHOLD)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else DEFAULT_THRESHOLD


def score(kind, signals) -> tuple[float, dict]:
    """(salience in [0, 1), the inputs used): 1 - exp(-sum of SIGNAL_WEIGHTS[signal] * min(count, CAP))."""
    if kind not in KINDS:
        raise KeyError(f"unknown broadcast kind {kind!r}")
    used = {}
    total = 0.0
    for name, weight in SIGNAL_WEIGHTS.items():
        n = signals.get(name) or 0
        n = n if isinstance(n, int) and not isinstance(n, bool) else len(n) if isinstance(n, (list, tuple)) else 0
        if n:
            used[name] = n
            total += weight * min(n, CAP)
    return round(1 - math.exp(-total), 4), {"counts": used, "weights": "broadcast.SIGNAL_WEIGHTS", "cap": CAP}


def _recent_broadcasts(db, now) -> int:
    return db.execute("SELECT count(*) FROM web_events WHERE kind='decision' AND created_at>? AND "
                      "json_extract(detail,'$.broadcast.outcome')='broadcast'", (now - 3600.0,)).fetchone()[0]


def _contention(db, now, scope) -> dict:
    """The live broadcast events (open owner questions younger than FRESH) ranked as Presence ranks them."""
    from .presence import aggregator
    rows = db.execute("SELECT id, scope, created_at FROM pending WHERE resolved_at IS NULL AND created_at>=? "
                      "ORDER BY created_at DESC LIMIT 50", (now - FRESH,)).fetchall()
    cands = [{"rank": aggregator.RANK["user_critical"], "since": at, "scope": sc, "pending_id": pid}
             for pid, sc, at in rows]
    if not cands:
        return {"competitors": 0, "winner": None, "rule": None}
    ranked = aggregator.arbitrate(cands)
    winner = ranked[0]
    return {"competitors": len(ranked), "winner": winner["scope"], "winner_pending_id": winner["pending_id"],
            "this_one_won": winner["scope"] == scope,
            "rule": "Presence priority: rank (user_critical), then the newest"}


def offer(db, kind, *, now, signals: dict, deliver, scope=None, task_id=None, salience=None,
          salience_inputs=None) -> dict:
    """Offer one event; returns {"outcome": "broadcast" | "local" | "delivered" (mode off), "pending_id", ...}.

    deliver(): the producer's own delivery (opens or updates its owner question; returns the pending id or None).
    Caller owns the transaction (store.write) and bumps state_version once in it, as both producers already do: the
    delivery, the log row and the bump are one change."""
    from .webcortex import state as wstate
    if kind not in KINDS:
        raise KeyError(f"unknown broadcast kind {kind!r}")
    current = mode(db)
    if current == "off":
        return {"outcome": "delivered", "pending_id": deliver(), "mode": "off"}
    if salience is None:
        salience, salience_inputs = score(kind, signals)
    limit = threshold(db)
    above = salience >= limit
    rate = _recent_broadcasts(db, now)
    would = "broadcast" if above and rate < MAX_PER_HOUR else "local"
    reason = (f"salience {salience:.3f} >= threshold {limit:.3f}" if would == "broadcast" else
              f"salience {salience:.3f} below threshold {limit:.3f}" if not above else
              f"rate limit: {rate} broadcasts in the last hour (at most {MAX_PER_HOUR})")
    pending_id = None
    if current == "log" or would == "broadcast":
        pending_id = deliver()
    outcome = "broadcast" if (current == "on" and would == "broadcast") else (
        "delivered" if current == "log" else "local")
    entry = {"kind": kind, "mode": current, "salience": salience, "inputs": salience_inputs, "threshold": limit,
             "outcome": outcome, "would": would, "reason": reason, "scope": scope, "pending_id": pending_id,
             "channels": (["pending", "web_events", "state_version"] if pending_id is not None else ["web_events"])}
    if pending_id is not None:
        entry["contention"] = _contention(db, now, scope)
    wstate.log_event(db, task_id, "decision", {"broadcast": entry}, now)
    return entry


def log(db, *, limit=20) -> list[dict]:
    """The latest offers, newest first: what was offered, whether it went out, why, and what won contention."""
    from .webcortex import state as wstate
    if not wstate.has_tables(db):
        return []
    out = []
    for task_id, detail, at in db.execute(
            "SELECT task_id, detail, created_at FROM web_events WHERE kind='decision' AND "
            "json_extract(detail,'$.broadcast') IS NOT NULL ORDER BY id DESC LIMIT ?", (int(limit),)):
        entry = (wstate.loads(detail, {}) or {}).get("broadcast") or {}
        out.append({"at": at, "task_id": task_id, **{k: entry.get(k) for k in
                                                     ("kind", "mode", "salience", "threshold", "outcome", "would",
                                                      "reason", "scope", "pending_id", "channels", "contention")}})
    return out


__all__ = ["KINDS", "MAX_PER_HOUR", "MODES", "SIGNAL_WEIGHTS", "log", "mode", "offer", "score", "threshold"]
