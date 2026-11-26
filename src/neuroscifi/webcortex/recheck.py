"""The idle re-check of what matters most (lane K, ADR-055; the useful part of "replay", research.md 4.2).

Knowledge goes stale while new knowledge arrives. The timer refreshes knowledge whose "still holds" prediction is due,
oldest first (predictions.check_due), and its curiosity looks only at curiosity.rank's ten best candidates, a pool
that contested subjects which are not knowledge can fill (lane S): stale knowledge can then wait for ever. This
activity re-verifies, inside the existing budgets, the knowledge whose loss would cost most:

    value = importance x staleness x (1 + DEPENDENT_WEIGHT x dependents) x futility

  importance   curiosity.IMPORTANCE of the item (accepted 1.0, provisional/stale 0.7; 1.2 for an owner monitor's)
  staleness    TTLs elapsed since it was last verified (age / ttl: the expected number of changes under a constant
               hazard, so the oldest item stays ahead), plus 1 for an item marked stale, one a newer release or
               revision was seen for, or one carrying an open re-check mark (premises.py: a premise changed or a
               prediction it rested on failed). A first version used 1 - P(current) as curiosity.rank reads it; it
               saturates at 0.5 two TTLs after verification and did worse than the due order on the development
               worlds (ADR-055)
  dependents   live records resting on it (premises.live_dependents), weighted by DEPENDENT_WEIGHT (0 by default:
               the development worlds showed no gain from it; the count is shown with every ranked item)
  futility     curiosity's own down-weighting of a subject whose last investigations changed nothing
Without importance and dependents the order is the due order of the "still holds" predictions (measured: identical).

It is not curiosity (curiosity.rank and its scores are unchanged): it only ranks knowledge items the owner already
holds. Every pick is admitted by autonomy.admission(path="recheck"), the one rule of both autonomy paths (recorded
enable, min_gain on the stated forecast, lineage, cooldown, record templates, searchable words); nothing is
loosened. A pick becomes a refresh task (knowledge.refresh, origin 'refresh:<id>': autonomous, charged to the daily
autonomous allowance, claimed after the owner's work by claim_next), its due prediction is linked to it (so
predictions.check_due settles it from that task), and its mark is 'queued' until the task's consolidation closes it
(premises.close_for_task). Bounds: WebConfig.recheck_per_tick picks per timer pass, recheck_daily per rolling 24 h, the
daily autonomous allowance and its concurrency (scheduler._over_cap, autonomy.full). Held by the owner's quiet
(background activity 'tick.knowledge_recheck'). Setting: WebConfig.idle_recheck (off by default).

The order (Wave 3 lane R, ADR-060; setting WebConfig.recheck_order, "staleness" by default = the value above,
unchanged). Another order changes only `value`, the order in which the pool is offered: info_gain, p_current, the
admission rule, the per-pass and daily shares and the allowance are the same, so it can only take, in another order,
items the same rule admits. Its ingredients, read only from the individual's own records (never an imagined record,
never a model), joined with "+":
  rate      the chance the item changed since it was last verified, 1 - exp(-rate x TTLs elapsed), where rate is the
            subject's own verified changes (rows of its key a newer version superseded) per TTL of exposure since it
            was first verified, with a prior of one change per TTL weighing as one TTL of exposure (a new subject is
            neither starved nor favoured); x importance x futility, as above
  seen      that chance is 1 for an item marked to re-check, marked stale, or one a newer version was seen for
  rotation  a fair floor: an item not looked at for one TTL or more (by a refresh, a re-check or a curiosity task of
            its subject, or verified) goes first, the longest-waiting first; alone, the pool is a blind rotation in
            that order (the round-robin the learning-cycles references use)
Each pick's decision event records the order and its inputs (submit).
"""
from __future__ import annotations

import math

from ..instance import store
from . import state

DAY = 86400.0
MAX_POOL = 200           # items offered to the admission rule per pass (the highest value first)
DEPENDENT_WEIGHT = 0.0   # each live dependent adds this share of the value; 0 chosen on the development worlds (ADR-055:
#                          1.0, 0.25 and 0.1 re-checked the same few items and lowered retention there)


ORDER_PARTS = ("rate", "seen", "rotation")      # Wave 3 lane R (ADR-060): recheck_order's ingredients, in this order
DEFAULT_ORDER = "staleness"
LOOK_WINDOW = 365 * DAY  # autonomous decision events read for "last looked at" (older ones count as not looked at)


def order_parts(value) -> tuple[str, ...]:
    """The ingredients of a recheck_order value; () for the default order (lane K's value). Raises ValueError for a
    value that is not 'staleness' or a '+'-joined set of ORDER_PARTS."""
    if value == DEFAULT_ORDER:
        return ()
    parts = value.split("+") if isinstance(value, str) else []
    if not parts or len(set(parts)) != len(parts) or not set(parts) <= set(ORDER_PARTS):
        raise ValueError(f"recheck_order must be {DEFAULT_ORDER!r} or a '+'-joined set of {', '.join(ORDER_PARTS)}")
    return tuple(p for p in ORDER_PARTS if p in parts)


def _histories(db, now) -> tuple[dict, dict, dict]:
    """({key: verified changes seen}, {key: first verified at}, {key: last looked at}) from the individual's own
    knowledge rows, refresh tasks and autonomous decision events (one query each, not one per item)."""
    changes = dict(db.execute("SELECT key, count(*) FROM web_knowledge WHERE superseded_by IS NOT NULL GROUP BY key"))
    first = dict(db.execute("SELECT key, min(first_verified_at) FROM web_knowledge GROUP BY key"))
    keys = dict(db.execute("SELECT id, key FROM web_knowledge"))
    looked: dict = {}
    for origin, at in db.execute("SELECT origin, max(created_at) FROM web_tasks WHERE origin GLOB 'refresh:*' "
                                 "GROUP BY origin"):
        tail = origin.split(":", 1)[1]
        key = keys.get(int(tail)) if tail.isdigit() else None
        if key is not None and at is not None:
            looked[key] = max(looked.get(key, at), at)
    for key, at in db.execute("SELECT json_extract(detail,'$.autonomy.key') AS k, max(created_at) FROM web_events "
                              "WHERE kind='decision' AND created_at>=? AND k IS NOT NULL GROUP BY k",
                              (now - LOOK_WINDOW,)):
        if at is not None:
            looked[key] = max(looked.get(key, at), at)
    return changes, first, looked


def _ordered(item, parts, histories, now, ttl, importance, factor, flagged) -> tuple[float, dict]:
    """(value, the inputs it was computed from) of one item under the order `parts` (see the module docstring)."""
    changes, first, looked = histories
    key = item["key"]
    verified = state.finite(item["last_verified_at"]) or now
    since = max(0.0, now - verified) / ttl
    exposure = max(0.0, now - (state.finite(first.get(key)) or verified)) / ttl
    last = max(verified, state.finite(looked.get(key)) or 0.0)
    waited = max(0.0, now - last) / ttl
    seen = flagged and "seen" in parts
    rate = (changes.get(key, 0) + 1.0) / (exposure + 1.0) if "rate" in parts else 1.0
    p_changed = 1.0 if seen else 1.0 - math.exp(-rate * since)
    overdue = "rotation" in parts and waited >= 1.0
    if overdue:
        value = 2.0 + waited          # ahead of every other item (importance x chance x futility is at most 1.2)
    elif "rate" in parts or "seen" in parts:
        value = importance * p_changed * factor
    else:
        value = waited                # rotation alone: the blind round-robin by last look
    inputs = {"rule": "+".join(parts), "p_changed": round(p_changed, 6), "changes_seen": changes.get(key, 0),
              "rate_per_ttl": round(rate, 6) if "rate" in parts else None, "ttls_since_verified": round(since, 6),
              "ttls_waited": round(waited, 6), "overdue": overdue, "newer_seen": bool(flagged)}
    return value, inputs


def enabled(db) -> bool:
    from . import premises
    cfg = state.get_config(db)
    return bool(getattr(cfg, "idle_recheck", False)) and premises.present(db)


def _staleness(item, now, cfg, db, marked) -> tuple[float, float, str]:
    """(staleness, P(current), why). staleness = TTLs elapsed since the item was last verified (the expected number of
    changes under a constant hazard: it keeps growing, so the oldest item stays ahead of one barely past its TTL),
    plus one TTL for an item marked to re-check, marked stale, or one a newer release or revision was seen for.
    P(current) is curiosity.rank's reading (even odds for those), the stated forecast min_gain reads."""
    staleness, p, why, _flagged = _assess(item, now, cfg, db, marked)
    return staleness, p, why


def _assess(item, now, cfg, db, marked) -> tuple[float, float, str, bool]:
    """_staleness and whether the item is flagged (marked, marked stale, or a newer version seen)."""
    from . import curiosity, knowledge
    elapsed = max(0.0, now - (state.finite(item["last_verified_at"]) or now))
    ttl = max(1.0, float(curiosity._ttl(cfg, item["freshness_class"])))
    stale, why = knowledge.staleness(item, now, cfg, db)
    flagged = marked or item["status"] == "stale" or (stale and why.startswith(("stale: a newer",
                                                                                 "stale: a new version")))
    if flagged:
        return elapsed / ttl + 1.0, 0.5, ("marked to re-check" if marked else why), True
    return elapsed / ttl, 1.0 - (1.0 - math.exp(-elapsed / ttl)) / 2.0, why, False


def _hypothesis_marks(db, knowledge_id) -> list[int]:
    """Open ('marked') re-check marks of hypotheses resting on the knowledge item."""
    return [mid for (mid,) in db.execute(
        "SELECT r.id FROM web_rechecks r JOIN web_premises p ON p.dependent_kind='hypothesis' AND "
        "p.dependent_id=r.subject_id AND p.premise_kind='knowledge' AND p.premise_id=? WHERE "
        "r.subject_kind='hypothesis' AND r.status='marked' ORDER BY r.id", (str(knowledge_id),))]


def rank(db, now, *, limit=MAX_POOL) -> list[dict]:
    """Live knowledge items the owner can refresh, highest value first, each as an autonomy candidate (kind 'recheck'):
    {kind, key, statement, mode, info_gain (from P(current), the stated forecast min_gain reads), signals,
    knowledge_id, value, staleness, p_current, importance, dependents, mark_id}. Items of value 0 are left out.
    Under another recheck_order (ADR-060) the same items are offered (those whose default value is above 0), `value`
    is the order's and each carries `order`, the inputs it was computed from."""
    from . import curiosity, knowledge, premises
    cfg = state.get_config(db)
    monitor_tasks = curiosity._monitor_keys(db)
    parts = order_parts(getattr(cfg, "recheck_order", DEFAULT_ORDER))
    histories = _histories(db, now) if parts else None
    out = []
    for item in knowledge.rows(db, f"status IN ({','.join('?' * len(knowledge.REVIEWABLE))})",
                               tuple(knowledge.REVIEWABLE)):
        if item["status"] not in state.LIVE_KNOWLEDGE:
            continue
        mark = premises.open_mark(db, "knowledge", item["id"]) if premises.present(db) else None
        if mark is not None and mark["status"] == "queued":
            continue                         # its re-verification is already on its way
        # a hypothesis resting on the item is re-checked by re-verifying the item (premises.py, review1 CM3)
        resting = _hypothesis_marks(db, item["id"]) if premises.present(db) else []
        staleness, p, why, flagged = _assess(item, now, cfg, db, mark is not None or bool(resting))
        importance = curiosity.IMPORTANCE["monitor"] if item["task_id"] in monitor_tasks else \
            curiosity.IMPORTANCE.get(item["status"], 0.7)
        deps = premises.live_dependents(db, item["id"]) if premises.present(db) else 0
        factor, _ = curiosity._futility(db, item["key"])
        value = importance * staleness * (1 + DEPENDENT_WEIGHT * deps) * factor
        if value <= 0:
            continue
        candidate = {"kind": "recheck", "key": item["key"], "statement": item["statement"],
                     "mode": "fact_verification", "info_gain": curiosity.info_gain(p),
                     "signals": {"stale": round(min(1.0, 2.0 * (1.0 - p)), 6)},
                     "extra": {"knowledge_id": item["id"]},
                     "knowledge_id": item["id"], "value": round(value, 6), "staleness": round(staleness, 6),
                     "p_current": round(p, 6), "importance": importance, "dependents": deps, "why": why,
                     "mark_id": mark["id"] if mark else None}
        if parts:           # ADR-060: another order of the same pool
            ttl = max(1.0, float(curiosity._ttl(cfg, item["freshness_class"])))
            ordered, candidate["order"] = _ordered(item, parts, histories, now, ttl, importance, factor, flagged)
            candidate["value"] = round(ordered, 6)
        out.append(candidate)
    out.sort(key=lambda c: (-c["value"], c["knowledge_id"]))
    return out[:limit]


def select(db, now, *, limit=1) -> dict:
    """{"admitted": autonomy.admission(path="recheck") over rank(), "picks": the admitted items in value order}."""
    from . import autonomy
    pool = rank(db, now)
    admitted = autonomy.admission(db, now, path="recheck", limit=limit, offered=pool)
    return {"admitted": admitted, "picks": admitted["chosen"], "pool": len(pool)}


def used_today(db, now) -> int:
    """Re-check tasks queued in the last 24 h, a rolling window (the recheck_daily share of the daily allowance)."""
    return db.execute("SELECT count(*) FROM web_events WHERE kind='decision' AND created_at>=? AND "
                      "json_extract(detail,'$.autonomy.recheck') IS NOT NULL", (now - DAY,)).fetchone()[0]


def submit(db, pick, admitted, now) -> dict:
    """Queue one admitted pick: a refresh task of the item (origin 'refresh:<id>'), its due prediction linked to the
    task, its mark 'queued' (a new mark with reason 'idle' when it had none) and the marks of hypotheses resting on
    it, and the decision event the cooldown and lineage read, all in one transaction (review1 CL5). Conflict, and
    nothing written, when the item's re-verification is already queued or running (a concurrent pass; CL7).
    Returns the task."""
    from ..instance.errors import Conflict
    from . import autonomy, knowledge, orchestrator, premises
    kid = pick["knowledge_id"]
    item = knowledge.row(db, kid)
    if item["status"] not in knowledge.REVIEWABLE:
        raise Conflict(f"Knowledge item {kid} is {item['status']}; refresh the current version instead")
    prepared = orchestrator.prepare(db, item["statement"], now, mode="fact_verification", origin=f"refresh:{kid}",
                                    parent=item["task_id"])
    inputs = autonomy.decision_inputs(pick, admitted) | {"recheck": {"value": pick.get("value"),
                                                                     "p_current": pick.get("p_current"),
                                                                     "dependents": pick.get("dependents")}}
    if pick.get("order"):       # ADR-060: why this pick, under the order the owner set
        inputs["recheck"]["order"] = pick["order"]
    with store.write(db):
        found = premises.open_mark(db, "knowledge", kid)
        if orchestrator.duplicate(db, prepared) is not None or (found is not None and found["status"] == "queued"):
            raise Conflict(f"the re-verification of knowledge #{kid} is already queued")
        # review3b N5 (completes security review L6): the day's autonomous task count is checked here, inside the
        # transaction that inserts, so a re-check and the idle investigation cannot both take the last place
        orchestrator._within_task_count(db, now)
        task_id = orchestrator.insert(db, prepared, now)
        state.log_event(db, task_id, "decision", {"autonomy": inputs}, now)
        db.execute("UPDATE web_predictions SET task_id=? WHERE knowledge_id=? AND outcome IS NULL AND task_id IS NULL "
                   "AND check_after<=?", (task_id, kid, now))
        mid = found["id"] if found is not None else \
            premises.mark(db, "knowledge", kid, "idle", {"kind": "knowledge", "id": str(kid)}, now)
        for queued in ([mid] if mid is not None else []) + _hypothesis_marks(db, kid):
            db.execute("UPDATE web_rechecks SET status='queued', task_id=?, updated_at=? WHERE id=?",
                       (task_id, now, queued))
        store.bump_state_version(db)
    return orchestrator.get(db, task_id)


def run(db, now, *, limit=None, over_cap=None) -> dict:
    """One timer pass (scheduler.tick, before the due-order refreshes): {"submitted": [{task_id, knowledge_id}],
    "skipped": [{what, reason}], "pool": n}. Asks background.allowed first; submits nothing while the setting is
    off, a cap is reached or the per-tick / per-day share is spent. `over_cap` (scheduler._over_cap bound to the
    tick) is asked before every submission."""
    from .. import background
    from ..instance.errors import Conflict, NotFound, ValidationError, WritesPaused
    from . import autonomy, premises
    out = {"submitted": [], "skipped": [], "pool": 0}
    ok, why = background.allowed(db, "tick.knowledge_recheck")
    if not ok:
        out["skipped"].append({"what": "knowledge re-check", "reason": why})
        return out
    if not enabled(db):
        return out
    cfg = state.get_config(db)
    with store.write(db):
        premises.release_stuck(db, now)
    room = max(0, int(cfg.recheck_daily) - used_today(db, now))
    limit = min(int(cfg.recheck_per_tick if limit is None else limit), room)
    if limit <= 0:
        out["skipped"].append({"what": "knowledge re-check", "reason": f"re-check share spent: {cfg.recheck_daily} "
                                                                      "per day, or 0 per pass"})
        return out
    if autonomy.full(db, now):
        out["skipped"].append({"what": "knowledge re-check", "reason": "autonomous investigations at their "
                                                                      "concurrency limit"})
        return out
    chosen = select(db, now, limit=limit)
    out["pool"] = chosen["pool"]
    if chosen["admitted"]["gate"]:
        out["skipped"].append({"what": "knowledge re-check", "reason": chosen["admitted"]["gate"]})
    cooling = autonomy._recent(db, now, float(autonomy.policy(db)["cooldown"]))
    for pick in chosen["picks"]:
        cap = over_cap() if over_cap is not None else None
        if cap:
            out["skipped"].append({"knowledge_id": pick["knowledge_id"], "reason": cap})
            break
        if pick["key"] in cooling:
            out["skipped"].append({"knowledge_id": pick["knowledge_id"], "reason": "investigated autonomously since "
                                                                                   "the ranking was read (cooldown)"})
            continue
        try:
            task = submit(db, pick, chosen["admitted"], now)
        except (Conflict, NotFound, ValidationError, WritesPaused) as exc:
            out["skipped"].append({"knowledge_id": pick["knowledge_id"], "reason": str(exc)[:300]})
            continue
        out["submitted"].append({"task_id": task["id"], "knowledge_id": pick["knowledge_id"]})
    return out
