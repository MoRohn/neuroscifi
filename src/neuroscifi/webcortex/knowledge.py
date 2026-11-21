"""Web knowledge: versioned, temporal statements in the individual's semantic memory (ADR-026).

A row is a *version* of what is known about one claim key. New values supersede, never overwrite: the older row
keeps its validity interval and points at its successor (`superseded_by`). Every promotion to 'accepted' goes
through the owner: `review()` here, or `nsf resolve ID TEXT --confirm|--reject` on the question with scope
`web:knowledge:<id>` (executive.resolve calls `apply_review` inside its own transaction).

Retrieval is lexical (BM25 over statement and key, ADR-009/022). Staleness is computed on read from the freshness
class (temporal.is_stale); status 'stale' is set only when a forget removed part of an item's support. With the
database at hand staleness also reads later observations: a release-class item is stale once a claim about the
same key (or a near-identical one) carrying a higher version was recorded after the item's last verification
(newer_release_seen); an item resting on an arXiv record is stale once a later read of that record showed a
higher arXiv version (revision_seen, from web_sources.metadata.version).
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


def staleness(item: dict, now, cfg=None, db=None) -> tuple[bool, str]:
    """(stale, reason) for a knowledge row. With `db`, later observations (a newer release, a new arXiv version of
    a source) make it stale before its TTL (module docstring)."""
    if item["status"] == "stale":
        return True, item.get("note") or "marked stale: part of its support was removed"
    from . import temporal
    newer = revised = False
    if db is not None:
        newer = item["freshness_class"] == "release" and newer_release_seen(db, item)
        revised = revision_seen(db, item)
    stale, reason = temporal.is_stale(item["last_verified_at"], item["freshness_class"], now,
                                      ttl_overrides=(cfg.freshness_ttl if cfg is not None else None) or None,
                                      newer_release_seen=newer, revision_seen=revised)
    return bool(stale), reason


def _version(text):
    """Comparable tuple of a version string ('v2.3.5', '2.10', 'v3'), or None."""
    import re
    m = re.match(r"v?(\d+(?:\.\d+)*)", str(text or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def _versions(values):
    from . import text
    out = []
    for v in values or []:
        if isinstance(v, str) and text.value_kind(v)[0] == "version":
            parsed = _version(v)
            if parsed is not None:
                out.append(parsed)
    return out


def newer_release_seen(db, item) -> bool:
    """A claim about the same key (or a near-identical key) with a higher version than every version the item
    states, recorded after the item was last verified."""
    mine = _versions(item["claim_values"])
    if not mine:
        return False
    from .consolidate import SIMILAR_KEY, _similar
    top = max(mine)
    # A range scan of web_claims_created (migration 7): only claims recorded after the last verification are read.
    for key, values in db.execute("SELECT key, claim_values FROM web_claims INDEXED BY web_claims_created "
                                  "WHERE created_at>? AND claim_values LIKE '%v%'", (item["last_verified_at"] or 0.0,)):
        if key != item["key"] and _similar(key, item["key"]) < SIMILAR_KEY:
            continue
        if any(v > top for v in _versions(state.loads(values, []))):
            return True
    return False


def revision_seen(db, item) -> bool:
    """A source of the item is an arXiv record whose version, read after the item was last verified, is higher
    than any version of it read up to then."""
    urls = [s.get("url") for s in item["sources"] if isinstance(s, dict) and s.get("url")]
    if not urls:
        return False
    verified = item["last_verified_at"] or 0.0
    before, after = {}, {}
    rows = db.execute(f"SELECT canonical_url, retrieved_at, json_extract(metadata, '$.version') FROM web_sources "
                      f"WHERE canonical_url IN ({','.join('?' * len(urls))}) AND "
                      f"json_extract(metadata, '$.arxiv_id') IS NOT NULL", urls).fetchall()
    for url, retrieved, version in rows:
        parsed = _version(version)
        if parsed is None:
            continue
        side = before if (retrieved or 0.0) <= verified else after
        side[url] = max(side.get(url, parsed), parsed)
    return any(url in before and v > before[url] for url, v in after.items())


def describe(db, item: dict, now, cfg=None) -> dict:
    """The public view of a knowledge row: status, confidence, freshness, staleness, provenance, versions."""
    stale, reason = staleness(item, now, cfg, db)
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
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValidationError("limit must be a positive integer")
    cfg = state.get_config(db)
    terms = text.content_tokens(topic)
    live = rows(db, f"status IN ({','.join('?' * len(state.LIVE_KNOWLEDGE))})", state.LIVE_KNOWLEDGE)
    items = []
    if terms and live:
        scores = text.BM25([text.content_tokens(r["statement"] + " " + r["key"].replace("<", " ").replace(">", " "))
                            for r in live]).scores(terms)
        ranked = sorted(((s, r) for s, r in zip(scores, live) if s > 0), key=lambda x: (-x[0], -x[1]["id"]))
        for score, item in ranked[:min(100, limit)]:
            items.append(describe(db, item, now, cfg) | {"score": round(score, 6)})
    return {"topic": topic, "as_of": now, "items": items, "matched": len(items),
            "known": sum(1 for i in items if i["status"] == "accepted" and not i["stale"] and not i["contested"])}


FEEDBACK_FIELDS = ("id", "knowledge_id", "key", "effect", "before_confidence", "after_confidence", "info_gain",
                   "task_id", "created_at")


def feedback(db, subject, *, limit=5) -> list[dict]:
    """The newest `limit` (1..100) feedback rows about a subject, newest first: `subject` is a knowledge id (the
    history of its key, across versions) or a claim key. Each row: id, knowledge_id, key, effect (strengthened,
    weakened, unchanged, reversed, new), before/after confidence, info_gain (bits), task_id, created_at."""
    if isinstance(subject, bool) or not isinstance(subject, (int, str)) or (isinstance(subject, str) and
                                                                            not subject.strip()):
        raise ValidationError("subject must be a knowledge id or a non-empty key")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValidationError("limit must be an integer between 1 and 100")
    key = row(db, subject)["key"] if isinstance(subject, int) else subject
    return [{k: (state.finite(v) if k in ("before_confidence", "after_confidence", "info_gain") else v)
             for k, v in zip(FEEDBACK_FIELDS, found)}
            for found in db.execute(f"SELECT {','.join(FEEDBACK_FIELDS)} FROM web_feedback WHERE key=? "
                                    "ORDER BY id DESC LIMIT ?", (key, limit))]


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


def open_web_questions(db) -> int:
    """Open Web Cortex questions (scope web:...), across tasks."""
    return db.execute("SELECT count(*) FROM pending WHERE resolved_at IS NULL AND scope LIKE 'web:%'").fetchone()[0]


def question_room(db, cfg=None) -> bool:
    """Whether a NEW web question fits the owner's interruption budget (config.max_open_web_questions)."""
    cfg = cfg or state.get_config(db)
    return open_web_questions(db) < cfg.max_open_web_questions


def ask(db, scope, question, now, cfg=None):
    """open_question within the web question budget: an open question of the same scope is updated (no new
    interruption); a new one is opened only while fewer than config.max_open_web_questions are open. Returns the
    pending id, or None when the budget is spent (the subject stays reviewable in the console / `nsf web
    knowledge`). Caller owns the transaction."""
    found = db.execute("SELECT 1 FROM pending WHERE scope=? AND resolved_at IS NULL LIMIT 1", (scope,)).fetchone()
    if found is None and not question_room(db, cfg):
        return None
    return open_question(db, scope, question, now)


def review_question(db, task_id, deferred, now, cfg=None) -> int | None:
    """The ONE aggregate question of a task whose candidates exceeded the question budget: "N candidates from task
    X await review". When the global budget is spent the newest open aggregate question is updated instead of
    opening another. Caller owns the transaction."""
    if deferred <= 0:
        return None
    text = (f"{deferred} candidate(s) from research task {task_id} await review in the console (Research) or with "
            "`nsf web knowledge --status provisional` / `nsf web review ID --accept|--reject`; no separate question "
            "was opened for each (web question budget).")
    scope = f"web:review:{task_id}"
    if db.execute("SELECT 1 FROM pending WHERE scope=? AND resolved_at IS NULL", (scope,)).fetchone() is None \
            and not question_room(db, cfg):
        latest = db.execute("SELECT scope FROM pending WHERE resolved_at IS NULL AND scope LIKE 'web:review:%' "
                            "ORDER BY id DESC LIMIT 1").fetchone()
        if latest is not None:
            scope = latest[0]
    return open_question(db, scope, text, now)


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
    Other web questions (web:contested:..., web:review:<task> aggregates, web:finding:<monitor>:<task> monitor
    findings) are informational: answer them without confirm.
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


def refresh(db, knowledge_id, now, *, origin="cli") -> dict:
    """Submit (not run) a fact-verification task for the item's statement; returns the queued task.

    `origin` names who asked: the owner's paths pass 'cli' or 'console' (an owner task, never clamped to the
    autonomous allowance); only a due prediction passes 'refresh:<id>', which is autonomous research."""
    from . import orchestrator
    item = row(db, knowledge_id)
    if item["status"] not in REVIEWABLE:
        raise Conflict(f"Knowledge item {knowledge_id} is {item['status']}; refresh the current version instead")
    return orchestrator.submit(db, item["statement"], now, mode="fact_verification", origin=origin,
                               parent=item["task_id"])
