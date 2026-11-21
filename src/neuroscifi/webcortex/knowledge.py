"""Web knowledge: versioned, temporal statements in the individual's semantic memory (ADR-026).

A row is a *version* of what is known about one claim key. New values supersede, never overwrite: the older row
keeps its validity interval and points at its successor (`superseded_by`). Every promotion to 'accepted' goes
through the owner: `review()` here, or `nsf resolve ID TEXT --confirm|--reject` on the question with scope
`web:knowledge:<id>` (executive.resolve calls `apply_review` inside its own transaction).

Retrieval is lexical (BM25 over statement and key, ADR-009/022). Staleness is computed on read from the freshness
class (temporal.is_stale); status 'stale' is set only when a forget removed part of an item's support.
"""
from __future__ import annotations

from ..instance import store
from ..instance.errors import Conflict, NotFound, ValidationError
from . import state

COLUMNS = ("id", "key", "version", "statement", "status", "confidence", "uncertainty", "freshness_class",
           "first_verified_at", "last_verified_at", "valid_from", "valid_until", "superseded_by", "supporting",
           "contradicting", "sources", "claim_values", "polarity", "note", "pending_id", "task_id", "created_at",
           "updated_at")
_JSON = ("supporting", "contradicting", "sources", "claim_values")
MAX_QUESTION_STATEMENT = 300
REVIEWABLE = ("provisional", "accepted", "stale")


def row(db, knowledge_id) -> dict:
    if isinstance(knowledge_id, bool) or not isinstance(knowledge_id, int):
        raise ValidationError("knowledge id must be an integer")
    found = db.execute(f"SELECT {','.join(COLUMNS)} FROM web_knowledge WHERE id=?", (knowledge_id,)).fetchone()
    if found is None:
        raise NotFound(f"No web knowledge item {knowledge_id}")
    return _decode(found)


def _decode(values) -> dict:
    out = dict(zip(COLUMNS, values))
    for key in _JSON:
        out[key] = state.loads(out[key], [])
    return out


def rows(db, where="1", params=()) -> list[dict]:
    return [_decode(r) for r in db.execute(f"SELECT {','.join(COLUMNS)} FROM web_knowledge WHERE {where} "
                                           "ORDER BY id", params)]


def versions(db, key) -> list[dict]:
    return [{"id": r["id"], "version": r["version"], "statement": r["statement"], "status": r["status"],
             "valid_from": r["valid_from"], "valid_until": r["valid_until"], "superseded_by": r["superseded_by"],
             "confidence": r["confidence"]} for r in rows(db, "key=?", (key,))]


def staleness(item: dict, now, cfg=None) -> tuple[bool, str]:
    """(stale, reason) for a knowledge row."""
    if item["status"] == "stale":
        return True, item.get("note") or "marked stale: part of its support was removed"
    from . import temporal
    stale, reason = temporal.is_stale(item["last_verified_at"], item["freshness_class"], now,
                                      ttl_overrides=(cfg.freshness_ttl if cfg is not None else None) or None)
    return bool(stale), reason


def describe(db, item: dict, now, cfg=None) -> dict:
    """The public view of a knowledge row: status, confidence, freshness, staleness, provenance, versions."""
    stale, reason = staleness(item, now, cfg)
    return {
        "id": item["id"], "key": item["key"], "version": item["version"], "statement": item["statement"],
        "status": item["status"], "confidence": state.finite(item["confidence"], 0.0),
        "uncertainty": state.finite(item["uncertainty"], 0.0), "freshness_class": item["freshness_class"],
        "first_verified_at": item["first_verified_at"], "last_verified_at": item["last_verified_at"],
        "valid_from": item["valid_from"], "valid_until": item["valid_until"], "stale": stale, "stale_reason": reason,
        "provisional": item["status"] == "provisional", "contested": bool(item["contradicting"]),
        "contradicting": item["contradicting"], "supporting": item["supporting"],
        "sources": [{k: s.get(k) for k in ("url", "title", "domain", "published_at", "retrieved_at")}
                    for s in item["sources"]],
        "values": item["claim_values"], "polarity": item["polarity"], "note": item["note"],
        "pending_id": item["pending_id"], "superseded_by": item["superseded_by"],
        "versions": versions(db, item["key"]),
    }


def query(db, topic, now, *, limit=10) -> dict:
    """What is known about `topic`: live items (provisional, accepted, stale) ranked by BM25 over statement and key."""
    from . import text
    if not isinstance(topic, str) or not topic.strip() or len(topic) > 2000:
        raise ValidationError("topic must be a non-blank string of at most 2000 characters")
    cfg = state.get_config(db)
    terms = text.content_tokens(topic)
    live = rows(db, f"status IN ({','.join('?' * len(state.LIVE_KNOWLEDGE))})", state.LIVE_KNOWLEDGE)
    items = []
    if terms and live:
        scores = text.BM25([text.content_tokens(r["statement"] + " " + r["key"].replace("<", " ").replace(">", " "))
                            for r in live]).scores(terms)
        ranked = sorted(((s, r) for s, r in zip(scores, live) if s > 0), key=lambda x: (-x[0], -x[1]["id"]))
        for score, item in ranked[:max(1, min(100, int(limit)))]:
            items.append(describe(db, item, now, cfg) | {"score": round(score, 6)})
    return {"topic": topic, "as_of": now, "items": items, "matched": len(items),
            "known": sum(1 for i in items if i["status"] == "accepted" and not i["stale"] and not i["contested"])}


# ---- questions -----------------------------------------------------------------------------------------------

MAX_QUESTION = 2000


def open_question(db, scope, question, now) -> int:
    """Open the web question of `scope`, or add `question` as an update to the one already open (one open question
    per subject). Caller owns the transaction."""
    found = db.execute("SELECT id, question FROM pending WHERE scope=? AND resolved_at IS NULL ORDER BY id LIMIT 1",
                       (scope,)).fetchone()
    if found:
        if question not in found[1] and len(found[1]) + len(question) + 9 <= MAX_QUESTION:
            db.execute("UPDATE pending SET question=? WHERE id=?", (f"{found[1]}\nUpdate: {question}", found[0]))
        return found[0]
    return db.execute("INSERT INTO pending(epoch,kind,scope,question,created_at) VALUES(NULL,'ask',?,?,?)",
                      (scope, question, now)).lastrowid


def quote(statement) -> str:
    statement = " ".join(str(statement).split())
    return statement if len(statement) <= MAX_QUESTION_STATEMENT else statement[:MAX_QUESTION_STATEMENT - 1] + "…"


def close_questions(db, scope, resolution, now) -> list[int]:
    """Close the open questions of `scope` with a system resolution. Caller owns the transaction."""
    return _close_questions(db, scope, resolution, now)


def _close_questions(db, scope, resolution, now) -> list[int]:
    ids = [r[0] for r in db.execute("SELECT id FROM pending WHERE scope=? AND resolved_at IS NULL", (scope,))]
    for pid in ids:
        db.execute("UPDATE pending SET resolved_at=?, resolution=? WHERE id=?", (now, resolution, pid))
    return ids


# ---- review --------------------------------------------------------------------------------------------------

def _set_status(db, item, status, now, note=None):
    if item["status"] == status:
        return
    if item["status"] not in REVIEWABLE:
        raise Conflict(f"Knowledge item {item['id']} is {item['status']}; only provisional, accepted or stale items "
                       "can be reviewed")
    db.execute("UPDATE web_knowledge SET status=?, note=coalesce(?, note), updated_at=? WHERE id=?",
               (status, note, now, item["id"]))


def _parse_scope(scope):
    parts = scope.split(":", 2)
    if len(parts) != 3 or parts[0] != "web":
        raise ValidationError(f"not a web question scope: {scope}")
    return parts[1], parts[2]


def apply_review(db, current: dict, confirm: bool, now) -> dict:
    """The owner's confirm/reject of a web question. Caller (executive.resolve) owns the transaction.

    web:knowledge:<id>  confirm=True -> accepted, False -> rejected
    web:monitor:<id>    confirm=True -> the proposed monitor becomes active (due now), False -> stopped
    web:research:<id>   confirm=True -> the proposed task is queued for the worker, False -> cancelled
    Other web questions (e.g. web:contested:...) are informational: answer them without confirm.
    """
    kind, ident = _parse_scope(current["scope"])
    if kind == "knowledge":
        item = row(db, _int(ident))
        before = item["status"]
        _set_status(db, item, "accepted" if confirm else "rejected", now,
                    f"owner {'accepted' if confirm else 'rejected'} (question #{current['id']})")
        return {"kind": "knowledge", "id": item["id"], "previous_status": before,
                "status": "accepted" if confirm else "rejected"}
    if kind == "monitor":
        mid = _int(ident)
        found = db.execute("SELECT status FROM web_monitors WHERE id=?", (mid,)).fetchone()
        if found is None:
            raise NotFound(f"No monitor {mid}")
        if found[0] != "proposed":
            raise Conflict(f"Monitor {mid} is {found[0]}, not a proposal")
        status = "active" if confirm else "stopped"
        db.execute("UPDATE web_monitors SET status=?, next_run_at=?, updated_at=? WHERE id=?",
                   (status, now if confirm else None, now, mid))
        return {"kind": "monitor", "id": mid, "status": status}
    if kind == "research":
        found = db.execute("SELECT status, stage FROM web_tasks WHERE id=?", (ident,)).fetchone()
        if found is None:
            raise NotFound(f"No research task {ident}")
        if found != ("queued", "proposed"):
            raise Conflict(f"Research task {ident} is not an open proposal")
        if confirm:
            state.update_task(db, ident, now, stage=None)
            state.log_event(db, ident, "decision", {"decision": "proposal authorised by the owner"}, now)
            return {"kind": "research", "id": ident, "status": "queued"}
        state.update_task(db, ident, now, status="cancelled", stage=None, finished_at=now,
                          error="proposal declined by the owner")
        state.log_event(db, ident, "cancelled", {"reason": "proposal declined by the owner"}, now)
        return {"kind": "research", "id": ident, "status": "cancelled"}
    raise ValidationError(f"Question {current['id']} ({current['scope']}) is informational; answer it without "
                          "confirm")


def review_outcome(db, current: dict):
    """True/False when the question's subject shows the owner's confirm/reject, else None (for idempotent retries)."""
    try:
        kind, ident = _parse_scope(current["scope"])
    except ValidationError:
        return None
    if kind == "knowledge":
        found = db.execute("SELECT status FROM web_knowledge WHERE id=?", (_maybe_int(ident),)).fetchone()
        return {"accepted": True, "rejected": False}.get(found[0]) if found else None
    if kind == "monitor":
        found = db.execute("SELECT status FROM web_monitors WHERE id=?", (_maybe_int(ident),)).fetchone()
        return {"active": True, "paused": True, "stopped": False}.get(found[0]) if found else None
    if kind == "research":
        found = db.execute("SELECT status, stage FROM web_tasks WHERE id=?", (ident,)).fetchone()
        if found is None or found[1] == "proposed":
            return None
        return found[0] != "cancelled"
    return None


def _int(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        raise ValidationError(f"invalid id in web question scope: {text}") from None


def _maybe_int(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def review(db, knowledge_id, accept: bool, now, *, note="") -> dict:
    """Accept (provisional/stale -> accepted) or reject an item; closes its open question in the same transaction
    and counts as a teacher intervention, exactly like answering it with `nsf resolve`."""
    if not isinstance(accept, bool):
        raise ValidationError("accept must be true or false")
    if not isinstance(note, str) or len(note) > 1024:
        raise ValidationError("note must be a string of at most 1024 characters")
    with store.write(db):
        item = row(db, knowledge_id)
        before = item["status"]
        verdict = "accepted" if accept else "rejected"
        _set_status(db, item, verdict, now, f"owner {verdict}" + (f": {note}" if note else ""))
        closed = _close_questions(db, f"web:knowledge:{item['id']}", note or verdict, now)
        store.bump(db, "teacher_interventions")
        version = store.bump_state_version(db)
    return {"id": item["id"], "previous_status": before, "status": verdict, "pending_closed": closed,
            "state_version": version}


def _owner_status(db, knowledge_id, status, now, note) -> dict:
    with store.write(db):
        item = row(db, knowledge_id)
        before = item["status"]
        if before not in REVIEWABLE and before != status:
            raise Conflict(f"Knowledge item {item['id']} is {before}; it cannot become {status}")
        db.execute("UPDATE web_knowledge SET status=?, note=?, updated_at=? WHERE id=?",
                   (status, f"owner {status}" + (f": {note}" if note else ""), now, item["id"]))
        closed = _close_questions(db, f"web:knowledge:{item['id']}", f"{status} by the owner", now)
        version = store.bump_state_version(db)
    return {"id": item["id"], "previous_status": before, "status": status, "pending_closed": closed,
            "state_version": version}


def archive(db, knowledge_id, now, *, note="") -> dict:
    """Keep the item for history but stop treating it as current knowledge."""
    return _owner_status(db, knowledge_id, "archived", now, note)


def reject(db, knowledge_id, now, *, note="") -> dict:
    return _owner_status(db, knowledge_id, "rejected", now, note)


def forget(db, knowledge_id, now) -> dict:
    """Forget the item and every version in its chain (governance.forget_knowledge)."""
    from ..instance import governance
    return governance.forget_knowledge(db, knowledge_id, now)


def refresh(db, knowledge_id, now, *, origin=None) -> dict:
    """Submit (not run) a fact-verification task for the item's statement; returns the queued task."""
    from . import orchestrator
    item = row(db, knowledge_id)
    if item["status"] not in REVIEWABLE:
        raise Conflict(f"Knowledge item {knowledge_id} is {item['status']}; refresh the current version instead")
    return orchestrator.submit(db, item["statement"], now, mode="fact_verification",
                               origin=origin or f"refresh:{item['id']}", parent=item["task_id"])
