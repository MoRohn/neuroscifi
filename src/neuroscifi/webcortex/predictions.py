"""Predictions about knowledge: "this still holds at the next refresh", checked by a refresh task.

record() stores an expectation with a check time (now + the freshness TTL). check_due() submits a refresh task
(knowledge.refresh) for each due prediction and checks those whose refresh finished. check() compares the
refresh's consolidation with the item: the same values and polarity -> 'confirmed' (and the next prediction is
recorded); a superseding value or a contradiction -> 'error' (a prediction_error event, a question for the owner
within the web question budget, and a curiosity boost for the item, see curiosity.rank); no evidence either way
-> 'inconclusive'. Refreshes are autonomous research: origin 'refresh:<knowledge id>'.
"""
from __future__ import annotations

from ..instance import store
from ..instance.errors import Conflict, NotFound, ValidationError, WritesPaused
from . import state
from .types import TERMINAL_STATES

COLUMNS = ("id", "knowledge_id", "expectation", "check_after", "created_at", "task_id", "checked_at", "outcome",
           "detail")
DEFAULT_EXPECTATION = "still holds at next refresh"
MAX_REFRESHES = 5


def _row(db, prediction_id) -> dict:
    found = db.execute(f"SELECT {','.join(COLUMNS)} FROM web_predictions WHERE id=?", (prediction_id,)).fetchone()
    if found is None:
        raise NotFound(f"No prediction {prediction_id}")
    out = dict(zip(COLUMNS, found))
    out["detail"] = state.loads(out["detail"], {})
    return out


def add(db, knowledge_id, expectation, check_after, now) -> int:
    """Caller owns the transaction."""
    if not isinstance(expectation, str) or not expectation.strip() or len(expectation) > 500:
        raise ValidationError("expectation must be a non-blank string of at most 500 characters")
    if state.finite(check_after) is None:
        raise ValidationError("check_after must be a number")
    if db.execute("SELECT 1 FROM web_knowledge WHERE id=?", (knowledge_id,)).fetchone() is None:
        raise NotFound(f"No web knowledge item {knowledge_id}")
    pid = db.execute("INSERT INTO web_predictions(knowledge_id,expectation,check_after,created_at) VALUES(?,?,?,?)",
                     (knowledge_id, expectation, check_after, now)).lastrowid
    state.log_event(db, None, "prediction", {"prediction_id": pid, "knowledge_id": knowledge_id,
                                             "check_after": check_after}, now)
    return pid


def record(db, knowledge_id, expectation, check_after, now) -> dict:
    with store.write(db):
        pid = add(db, knowledge_id, expectation, check_after, now)
        store.bump_state_version(db)
    return _row(db, pid)


def listing(db, *, open_only=False, limit=100) -> list[dict]:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValidationError("limit must be a positive integer")
    where = "WHERE outcome IS NULL" if open_only else ""
    return [dict(zip(COLUMNS, r)) | {"detail": state.loads(r[-1], {})} for r in db.execute(
        f"SELECT {','.join(COLUMNS)} FROM web_predictions {where} ORDER BY id DESC LIMIT ?", (int(limit),))]


def due(db, now) -> list[dict]:
    """Open predictions whose check time has come."""
    return [dict(zip(COLUMNS, r)) | {"detail": state.loads(r[-1], {})} for r in db.execute(
        f"SELECT {','.join(COLUMNS)} FROM web_predictions WHERE outcome IS NULL AND check_after<=? ORDER BY "
        "check_after, id", (now,))]


def _verdict(kid, item, consolidation):
    """(outcome, reason) from the refresh's consolidation summary and the item's current row."""
    if item is None:
        return "inconclusive", "the knowledge item no longer exists"
    for s in consolidation.get("superseded", []):
        if s.get("old") == kid:
            return "error", f"superseded by item #{s.get('new')}: {s.get('reason')}"
    for c in consolidation.get("contested", []):
        if c.get("knowledge_id") == kid:
            return "error", f"contradicted: {c.get('reason')}"
    if item["status"] == "superseded":
        return "error", f"superseded by item #{item['superseded_by']}"
    if kid in consolidation.get("strengthened", []) or kid in consolidation.get("unchanged", []):
        return "confirmed", "the refresh found the same values and polarity"
    return "inconclusive", "the refresh found no evidence about this item"


def check(db, prediction_id, task_report, now) -> dict:
    """Settle a prediction from the refresh task's report (its result JSON; key "consolidation")."""
    from . import knowledge, temporal
    report = task_report or {}
    with store.write(db):
        pred = _row(db, prediction_id)
        if pred["outcome"] is not None:
            return pred
        kid = pred["knowledge_id"]
        found = db.execute(f"SELECT {','.join(knowledge.COLUMNS)} FROM web_knowledge WHERE id=?", (kid,)).fetchone()
        item = knowledge._decode(found) if found else None
        outcome, reason = _verdict(kid, item, report.get("consolidation") or {})
        detail = {"reason": reason, "task_id": report.get("task_id") or pred["task_id"]}
        if outcome == "error":
            state.log_event(db, pred["task_id"], "prediction_error",
                            {"prediction_id": prediction_id, "knowledge_id": kid, "reason": reason}, now)
            if item is not None and item["status"] in knowledge.REVIEWABLE:   # a superseded item has a successor
                detail["pending_id"] = knowledge.ask(   # within the web question budget (None when spent)
                    db, f"web:knowledge:{kid}",
                    f"Prediction failed: «{knowledge.quote(item['statement'])}» was expected to hold at the next "
                    f"refresh, but {reason}. Keep it as knowledge?", now)
        elif outcome == "confirmed" and item is not None and item["status"] in ("provisional", "accepted"):
            ttl = temporal.ttl(item["freshness_class"], state.get_config(db).freshness_ttl or None)
            detail["next_prediction"] = add(db, kid, pred["expectation"], now + ttl, now)
        db.execute("UPDATE web_predictions SET outcome=?, checked_at=?, detail=? WHERE id=?",
                   (outcome, now, state.dumps(detail), prediction_id))
        state.log_event(db, pred["task_id"], "feedback",
                        {"prediction_id": prediction_id, "knowledge_id": kid, "outcome": outcome}, now)
        store.bump_state_version(db)
    return _row(db, prediction_id)


def check_due(db, now, services=None, *, limit=MAX_REFRESHES) -> list[dict]:
    """Submit refresh tasks for due predictions (at most `limit`) and settle those whose refresh finished.

    Never runs a task: the scheduler or the worker runs what this submits. While web access is disabled no refresh
    is submitted and the prediction stays due (it is not settled 'inconclusive' for want of a refresh)."""
    from . import knowledge
    actions = []
    enabled = state.get_config(db).enabled
    for pred in due(db, now):
        if pred["task_id"] is not None:
            try:
                task = state.task(db, pred["task_id"])
            except NotFound:
                continue
            if task["status"] in TERMINAL_STATES:
                settled = check(db, pred["id"], (task["result"] or {}) | {"task_id": task["id"]}, now)
                actions.append({"prediction_id": pred["id"], "action": "checked", "outcome": settled["outcome"]})
            continue
        if not enabled:
            actions.append({"prediction_id": pred["id"], "action": "skipped",
                            "reason": "web access is disabled; the prediction stays due"})
            continue
        if sum(1 for a in actions if a["action"] == "refresh_submitted") >= limit:
            actions.append({"prediction_id": pred["id"], "action": "skipped", "reason": f"refresh limit {limit}"})
            continue
        try:
            task = knowledge.refresh(db, pred["knowledge_id"], now, origin=f"refresh:{pred['knowledge_id']}")
        except (NotFound, ValidationError, WritesPaused) as exc:
            actions.append({"prediction_id": pred["id"], "action": "skipped", "reason": str(exc)})
            continue
        except Conflict as exc:   # the item is no longer reviewable (superseded/archived/rejected) or web is off
            settled = check(db, pred["id"], {"consolidation": {}}, now)
            actions.append({"prediction_id": pred["id"], "action": "checked", "outcome": settled["outcome"],
                            "reason": str(exc)})
            continue
        with store.write(db):
            db.execute("UPDATE web_predictions SET task_id=? WHERE id=?", (task["id"], pred["id"]))
            store.bump_state_version(db)
        actions.append({"prediction_id": pred["id"], "action": "refresh_submitted", "task_id": task["id"]})
    return actions
