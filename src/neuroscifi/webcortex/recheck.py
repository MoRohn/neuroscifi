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
"""
from __future__ import annotations

import math

from ..instance import store
from . import state

DAY = 86400.0
MAX_POOL = 200           # items offered to the admission rule per pass (the highest value first)
DEPENDENT_WEIGHT = 0.0   # each live dependent adds this share of the value; 0 chosen on the development worlds (ADR-055:
#                          1.0, 0.25 and 0.1 re-checked the same few items and lowered retention there)


def enabled(db) -> bool:
    from . import premises
    cfg = state.get_config(db)
    return bool(getattr(cfg, "idle_recheck", False)) and premises.present(db)


def _staleness(item, now, cfg, db, marked) -> tuple[float, float, str]:
    """(staleness, P(current), why). staleness = TTLs elapsed since the item was last verified (the expected number of
    changes under a constant hazard: it keeps growing, so the oldest item stays ahead of one barely past its TTL),
    plus one TTL for an item marked to re-check, marked stale, or one a newer release or revision was seen for.
    P(current) is curiosity.rank's reading (even odds for those), the stated forecast min_gain reads."""
    from . import curiosity, knowledge
    elapsed = max(0.0, now - (state.finite(item["last_verified_at"]) or now))
    ttl = max(1.0, float(curiosity._ttl(cfg, item["freshness_class"])))
    stale, why = knowledge.staleness(item, now, cfg, db)
    flagged = marked or item["status"] == "stale" or (stale and why.startswith(("stale: a newer",
                                                                                 "stale: a new version")))
    if flagged:
        return elapsed / ttl + 1.0, 0.5, ("marked to re-check" if marked else why)
    return elapsed / ttl, 1.0 - (1.0 - math.exp(-elapsed / ttl)) / 2.0, why


def _hypothesis_marks(db, knowledge_id) -> list[int]:
    """Open ('marked') re-check marks of hypotheses resting on the knowledge item."""
    return [mid for (mid,) in db.execute(
        "SELECT r.id FROM web_rechecks r JOIN web_premises p ON p.dependent_kind='hypothesis' AND "
        "p.dependent_id=r.subject_id AND p.premise_kind='knowledge' AND p.premise_id=? WHERE "
        "r.subject_kind='hypothesis' AND r.status='marked' ORDER BY r.id", (str(knowledge_id),))]


def rank(db, now, *, limit=MAX_POOL) -> list[dict]:
    """Live knowledge items the owner can refresh, highest value first, each as an autonomy candidate (kind 'recheck'):
    {kind, key, statement, mode, info_gain (from P(current), the stated forecast min_gain reads), signals,
    knowledge_id, value, staleness, p_current, importance, dependents, mark_id}. Items of value 0 are left out."""
    from . import curiosity, knowledge, premises
    cfg = state.get_config(db)
    monitor_tasks = curiosity._monitor_keys(db)
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
        staleness, p, why = _staleness(item, now, cfg, db, mark is not None or bool(resting))
        importance = curiosity.IMPORTANCE["monitor"] if item["task_id"] in monitor_tasks else \
            curiosity.IMPORTANCE.get(item["status"], 0.7)
        deps = premises.live_dependents(db, item["id"]) if premises.present(db) else 0
        factor, _ = curiosity._futility(db, item["key"])
        value = importance * staleness * (1 + DEPENDENT_WEIGHT * deps) * factor
        if value <= 0:
            continue
        out.append({"kind": "recheck", "key": item["key"], "statement": item["statement"],
                    "mode": "fact_verification", "info_gain": curiosity.info_gain(p),
                    "signals": {"stale": round(min(1.0, 2.0 * (1.0 - p)), 6)},
                    "extra": {"knowledge_id": item["id"]},
                    "knowledge_id": item["id"], "value": round(value, 6), "staleness": round(staleness, 6),
                    "p_current": round(p, 6), "importance": importance, "dependents": deps, "why": why,
                    "mark_id": mark["id"] if mark else None})
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
    with store.write(db):
        found = premises.open_mark(db, "knowledge", kid)
        if orchestrator.duplicate(db, prepared) is not None or (found is not None and found["status"] == "queued"):
            raise Conflict(f"the re-verification of knowledge #{kid} is already queued")
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
