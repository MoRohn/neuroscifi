"""Pocket research routes added in 0.7.0 (D4; ADR-028 family scope kept; ADR-039 draft).

research_evidence  {id}                     the task's evidence review: ADR-035 state, basis and category per
                                            subject, with the claim text (untrusted) and the report's sources
research_continue  {id}                     continue a partial, cancelled or failed task (orchestrator.requeue),
                                            counted in the family's 10 submissions per hour
research_session   {id, action[, to]}       session ownership (webcortex/nodes.py): status for any device of the
                                            family; take, handoff (to = a node label), accept and release for
                                            enrolled nodes only
research_cancel    {id}                     as before, refused while another device owns the task's session

Every route resolves the task within the caller's family (another family's task is 404, as before), bodies stay at
512 bytes (pocket/server.py), and errors are short codes, never task text.
"""
from __future__ import annotations

import time

ROUTES = {"research_evidence", "research_continue", "research_session"}
SESSION_ACTIONS = ("status", "take", "handoff", "accept", "release")
MAX_ITEMS = 40


def _text(value, limit):
    return value[:limit] if isinstance(value, str) else ""


def evidence(db, task, family):
    """The review a phone can show: each subject's verification state and basis (ADR-035), never more than
    MAX_ITEMS, with the first claim sentence of that subject (untrusted web text) and the source list."""
    from ..webcortex import state
    claims = {}
    for c in state.claims(db, task["id"]):
        claims.setdefault(c["key"], c)
    checkpoint = task.get("checkpoint") or {}
    rows = checkpoint.get("verification")
    if rows is None:      # a task consolidated before 0.7.0: the cluster status only
        rows = [{"key": c.get("key"), "state": "not_evaluated", "basis": None, "category": None,
                 "cluster": c.get("status")} for c in checkpoint.get("clusters") or []]
    items = []
    for row in rows[:MAX_ITEMS]:
        claim = claims.get(row.get("key")) or {}
        items.append({"statement": _text(claim.get("text"), 300), "state": _text(row.get("state"), 40),
                      "basis": _text(row.get("basis"), 40) or None, "category": _text(row.get("category"), 40) or None,
                      "domain": _text(claim.get("domain"), 255) or None, "untrusted": True})
    from .server import phone_report
    report = phone_report(task.get("result")) or {}
    return {"id": task["id"], "status": task["status"], "evidence": items,
            "sources": report.get("sources", []), "subjects": len(rows)}


def handle(server, db, family, device, route, data, *, token_family):
    """(status, payload) for the routes above and research_cancel. `family` is the instance-salted family digest,
    `device` the salted device digest, `token_family` the Pocket family fingerprint (for the rate limit)."""
    from ..instance.errors import Conflict, NotFound
    from ..webcortex import autonomy, nodes, orchestrator
    from .server import event_id, task_detail
    now = time.time()
    allowed = {"id"} | ({"action", "to"} if route == "research_session" else set())
    if not {"id"} <= set(data) <= allowed:
        raise ValueError()
    ident = event_id(data["id"])
    task = orchestrator.get(db, ident, family=family)     # NotFound for another family's task
    if route == "research_evidence":
        from ..webcortex import state
        return 200, evidence(db, state.task(db, ident, family=family), family)
    if route == "research_session":
        action = data.get("action", "status")
        if action not in SESSION_ACTIONS or ("to" in data) != (action == "handoff"):
            raise ValueError()
        if action == "handoff" and (not isinstance(data["to"], str) or not 0 < len(data["to"]) <= 64):
            raise ValueError()
        if action == "status":
            return 200, nodes.view(db, nodes.session_of(db, ident), device, now)
        try:
            if action == "handoff":
                return 200, nodes.handoff(db, ident, device, family, data["to"], now)
            return 200, getattr(nodes, action)(db, ident, device, family, now)
        except PermissionError:
            return 409, {"error": "not_a_node"}
        except NotFound:
            return 409, {"error": "no_such_node"}
        except Conflict:
            return 409, {"error": "owned", **nodes.view(db, nodes.session_of(db, ident), device, now)}
    if not nodes.may_drive(db, ident, device, now):
        return 409, {"error": "owned", **nodes.view(db, nodes.session_of(db, ident), device, now)}
    if route == "research_cancel":
        orchestrator.cancel(db, ident, now, family=family)
        nodes.renew(db, ident, device, now)
        return 200, task_detail(orchestrator.get(db, ident, family=family))
    # research_continue: a new or resumed task in the same session, under the family's submission limit
    try:
        out = server.store.admit(token_family, lambda: orchestrator.requeue(db, ident, now, origin=task["origin"],
                                                                           family=family))
    except autonomy.BudgetRefused:
        return 429, {"error": "daily_budget"}
    except Conflict:
        return 409, {"error": "not_continuable"}
    if out is None:
        return 429, {}
    nodes.renew(db, ident, device, now)
    return 201, {"id": out["id"], "requeued": out.get("requeued")}
