"""Premises and re-check marks (lane K, ADR-055; directive 7.3 "temporal validity", research.md 4.3).

A knowledge item or a hypothesis records, when it is derived, what it rests on (web_premises):
  knowledge   supported_by  the claims that verified it (its `supporting` list)
  hypothesis  supported_by  the claims it states; alternative_to the knowledge item it was opened against;
              revision_of   the hypothesis a failed prediction revised
Older rows are never rewritten: only records derived after lane K's migration have premises.

When a premise changes - a knowledge item is superseded or contradicted, a record is forgotten, or a prediction a
subject rested on fails - every dependent still held is marked "to re-check" (web_rechecks, status 'marked'). A mark
changes nothing by itself: no confidence, status, verification state or answer moves. It queues a bounded
re-verification (recheck.py runs it when the owner switched it on), and the subject changes only through the normal
evidence path (consolidate.run). The marks are what `nsf knowledge rechecks` shows.

Bounds: at most MAX_MARKS_PER_CHANGE dependents are marked per changed premise, one open mark per subject (a second
cause is added to the open mark's cause list, never a second row), at most MAX_OPEN_MARKS open marks in all (the
oldest open ones are dropped first, with the reason).

A mark closes, with the reason recorded in its outcome (settle(), review1 CM3), when its question is answered by any
normal path: the subject was re-verified after it was marked (a due refresh, a task the owner asked for, the idle
re-check, a prediction's settlement: anything that sets the item's last_verified_at), the subject is no longer live
(superseded, rejected, settled; a forgotten one is deleted), or its re-verification task ended without evidence
MAX_ATTEMPTS times (dropped). A hypothesis is marked only while it rests on a live knowledge item (alternative_to):
its re-check is that item's re-verification (recheck.rank counts the mark on the item), and it closes when that item
is re-verified or the hypothesis settles. With idle_recheck off nothing queues a re-verification: a mark is a note the
owner sees (`nsf knowledge rechecks`), and it still closes by those paths. settle() runs in every consolidation
(after_consolidation) and every forget (purge).

Forget: purge() (called in the forget's transaction, governance._web_finish, and after a restore's replay,
governance.replay_web_forgets) marks the dependents of a premise that is gone with reason 'premise_forgotten' (the mark
does not name what was forgotten), then deletes every premise row and mark that names a record no longer held.
Setting: WebConfig.premise_recheck (on: records and marks are display-only).
"""
from __future__ import annotations

from . import state

DEPENDENT_KINDS = ("knowledge", "hypothesis")
PREMISE_KINDS = ("knowledge", "claim", "hypothesis")
RELATIONS = ("supported_by", "alternative_to", "revision_of")
REASONS = ("premise_superseded", "premise_contradicted", "premise_forgotten", "prediction_failed", "idle",
           "reflection")
OPEN = ("marked", "queued")
MAX_MARKS_PER_CHANGE = 50
MAX_OPEN_MARKS = 500
MAX_CLOSED = 2000          # closed marks kept as history (the oldest go first)
MAX_CAUSES = 10
MAX_ATTEMPTS = 3           # re-verification tasks of one mark that may end without consolidating before it is dropped
_TABLES = {"knowledge": "web_knowledge", "hypothesis": "web_hypotheses", "claim": "web_claims"}


def present(db) -> bool:
    """Lane K's migration is present (web_premises and web_rechecks exist)."""
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='web_rechecks'").fetchone() is not None


def enabled(db) -> bool:
    return present(db) and bool(getattr(state.get_config(db), "premise_recheck", True))


# ---- premises ---------------------------------------------------------------------------------------------------------

def record(db, dependent_kind, dependent_id, premises, now) -> int:
    """Record what a newly derived record rests on: `premises` [(premise kind, premise id, relation)]. Duplicates are
    ignored. Caller owns the transaction. Returns the rows written."""
    if dependent_kind not in DEPENDENT_KINDS:
        raise ValueError(f"dependent kind must be one of {DEPENDENT_KINDS}")
    rows = []
    for kind, pid, relation in premises:
        if kind not in PREMISE_KINDS or relation not in RELATIONS or pid is None or str(pid) == "":
            continue
        rows.append((dependent_kind, int(dependent_id), kind, str(pid), relation, now))
    before = db.total_changes
    db.executemany("INSERT OR IGNORE INTO web_premises(dependent_kind,dependent_id,premise_kind,premise_id,relation,"
                   "created_at) VALUES(?,?,?,?,?,?)", rows)
    return db.total_changes - before


def premises_of(db, kind, subject_id) -> list[dict]:
    return [{"kind": k, "id": i, "relation": r} for k, i, r in db.execute(
        "SELECT premise_kind, premise_id, relation FROM web_premises WHERE dependent_kind=? AND dependent_id=? "
        "ORDER BY premise_kind, premise_id", (kind, int(subject_id)))]


def dependents(db, premise_kind, premise_id) -> list[tuple[str, int]]:
    """(kind, id) of every record still held that rests on the premise, oldest first."""
    out = []
    for kind, did in db.execute("SELECT dependent_kind, dependent_id FROM web_premises WHERE premise_kind=? AND "
                                "premise_id=? ORDER BY dependent_kind, dependent_id", (premise_kind, str(premise_id))):
        if _held(db, kind, did):
            out.append((kind, did))
    return out


def live_dependents(db, knowledge_id) -> int:
    """How many live records (open hypotheses, live knowledge) rest on a knowledge item: the 'dependents' factor of
    the idle re-check (recheck.rank)."""
    return db.execute(
        "SELECT count(*) FROM web_premises p WHERE p.premise_kind='knowledge' AND p.premise_id=? AND ("
        "(p.dependent_kind='hypothesis' AND EXISTS (SELECT 1 FROM web_hypotheses h WHERE h.id=p.dependent_id AND "
        "h.status='open')) OR (p.dependent_kind='knowledge' AND EXISTS (SELECT 1 FROM web_knowledge k WHERE "
        f"k.id=p.dependent_id AND k.status IN ({','.join('?' * len(state.LIVE_KNOWLEDGE))}))))",
        (str(knowledge_id), *state.LIVE_KNOWLEDGE)).fetchone()[0]


def knowledge_premises(db, hypothesis_id) -> list[int]:
    """The live knowledge items a hypothesis rests on (its premises of kind knowledge)."""
    return [int(i) for (i,) in db.execute(
        "SELECT premise_id FROM web_premises WHERE dependent_kind='hypothesis' AND dependent_id=? AND "
        "premise_kind='knowledge' ORDER BY premise_id", (int(hypothesis_id),))
        if str(i).isdigit() and _held(db, "knowledge", int(i))]


def _held(db, kind, record_id) -> bool:
    """A dependent worth re-checking: a live knowledge item or an open hypothesis."""
    if kind == "knowledge":
        return db.execute(f"SELECT 1 FROM web_knowledge WHERE id=? AND status IN "
                          f"({','.join('?' * len(state.LIVE_KNOWLEDGE))})",
                          (record_id, *state.LIVE_KNOWLEDGE)).fetchone() is not None
    return db.execute("SELECT 1 FROM web_hypotheses WHERE id=? AND status='open'", (record_id,)).fetchone() is not None


# ---- marks ------------------------------------------------------------------------------------------------------------

def open_mark(db, kind, subject_id) -> dict | None:
    row = db.execute("SELECT id, reason, cause, status, task_id FROM web_rechecks WHERE subject_kind=? AND subject_id=? "
                     "AND status IN ('marked','queued') ORDER BY id DESC LIMIT 1", (kind, subject_id)).fetchone()
    if row is None:
        return None
    return {"id": row[0], "reason": row[1], "cause": state.loads(row[2], {}), "status": row[3], "task_id": row[4]}


def mark(db, kind, subject_id, reason, cause, now) -> int | None:
    """Mark one subject "to re-check". One open mark per subject: a further cause is added to the open mark (at most
    MAX_CAUSES kept). Returns the id of a NEW mark, None when an open one was extended or the subject is not held.
    Never changes the subject. Caller owns the transaction."""
    if reason not in REASONS or kind not in DEPENDENT_KINDS or not _held(db, kind, subject_id):
        return None
    if kind == "hypothesis" and not knowledge_premises(db, subject_id):
        return None          # nothing could re-verify it: its re-check is that of the live knowledge it rests on
    cause = {k: v for k, v in dict(cause or {}).items() if k in ("kind", "id", "prediction_id", "task_id")}
    found = open_mark(db, kind, subject_id)
    if found is not None:
        causes = list(found["cause"].get("also") or [])
        if len(causes) < MAX_CAUSES:
            causes.append({"reason": reason} | cause)
            db.execute("UPDATE web_rechecks SET cause=?, updated_at=? WHERE id=?",
                       (state.dumps(found["cause"] | {"also": causes}), now, found["id"]))
        return None
    mid = db.execute("INSERT INTO web_rechecks(subject_kind,subject_id,reason,cause,status,created_at,updated_at) "
                     "VALUES(?,?,?,?, 'marked', ?, ?)", (kind, int(subject_id), reason, state.dumps(cause), now,
                                                         now)).lastrowid
    _bound(db, now)
    return mid


def _bound(db, now):
    over = db.execute("SELECT count(*) FROM web_rechecks WHERE status IN ('marked','queued')").fetchone()[0] \
        - MAX_OPEN_MARKS
    if over > 0:
        db.execute("UPDATE web_rechecks SET status='dropped', outcome=?, updated_at=? WHERE id IN (SELECT id FROM "
                   "web_rechecks WHERE status='marked' ORDER BY id LIMIT ?)",
                   (f"more than {MAX_OPEN_MARKS} open marks: the oldest are dropped", now, over))
    closed = db.execute("SELECT count(*) FROM web_rechecks WHERE status IN ('done','dropped')").fetchone()[0] \
        - MAX_CLOSED
    if closed > 0:
        db.execute("DELETE FROM web_rechecks WHERE id IN (SELECT id FROM web_rechecks WHERE status IN "
                   "('done','dropped') ORDER BY id LIMIT ?)", (closed,))


def premise_changed(db, premise_kind, premise_id, reason, now, *, cause=None) -> list[int]:
    """Mark every held dependent of a changed premise (at most MAX_MARKS_PER_CHANGE). Caller owns the txn."""
    marked = []
    cause = {"kind": premise_kind, "id": str(premise_id)} | dict(cause or {})
    for kind, did in dependents(db, premise_kind, premise_id)[:MAX_MARKS_PER_CHANGE]:
        mid = mark(db, kind, did, reason, cause, now)
        if mid is not None:
            marked.append(mid)
    return marked


def failed_prediction(db, pred: dict, now) -> list[int]:
    """Directive 7.2/item 4: a failed prediction marks what it rested on for re-check. A prediction about a knowledge
    item rested on that item (marked if still live) and on everything resting on it; a prediction about a
    hypothesis rested on the hypothesis' knowledge premises. Nothing changes a belief here. Caller owns the txn."""
    if not enabled(db):
        return []
    cause = {"prediction_id": pred.get("id")}
    out = []
    if pred.get("knowledge_id") is not None:
        kid = pred["knowledge_id"]
        mid = mark(db, "knowledge", kid, "prediction_failed", cause | {"kind": "knowledge", "id": str(kid)}, now)
        out += [mid] if mid else []
        out += premise_changed(db, "knowledge", kid, "prediction_failed", now, cause=cause)
    elif pred.get("hypothesis_id") is not None:
        for p in premises_of(db, "hypothesis", pred["hypothesis_id"]):
            if p["kind"] == "knowledge" and p["id"].isdigit():
                mid = mark(db, "knowledge", int(p["id"]), "prediction_failed", cause | {"kind": "hypothesis",
                                                                                     "id": str(pred["hypothesis_id"])},
                           now)
                out += [mid] if mid else []
    return out


# ---- consolidation hook --------------------------------------------------------------------------------------------------

def after_consolidation(db, task_id, summary, now) -> dict:
    """Called by consolidate.run inside its transaction, after evolution.observe. Records the premises of the
    knowledge items it created; marks the dependents of the items it superseded or that sources contradicted; marks
    what a failed hypothesis prediction rested on; closes the marks this task was the re-verification of, with what
    the evidence did. Returns {"premises": n, "marked": [...], "closed": [...]} (empty while off)."""
    out = {"premises": 0, "marked": [], "closed": []}
    if not enabled(db):
        return out
    for kid in dict.fromkeys(summary.get("provisional") or []):
        row = db.execute("SELECT supporting FROM web_knowledge WHERE id=?", (kid,)).fetchone()
        if row is not None:
            out["premises"] += record(db, "knowledge", kid, [("claim", c, "supported_by")
                                                             for c in state.loads(row[0], [])], now)
    for s in summary.get("superseded") or []:
        out["marked"] += premise_changed(db, "knowledge", s.get("old"), "premise_superseded", now,
                                         cause={"task_id": task_id})
    for c in summary.get("contested") or []:
        if c.get("knowledge_id") is not None:
            out["marked"] += premise_changed(db, "knowledge", c["knowledge_id"], "premise_contradicted", now,
                                             cause={"task_id": task_id})
    for s in summary.get("predictions_settled") or []:
        if s.get("outcome") == "error":
            out["marked"] += failed_prediction(db, {"id": s.get("prediction_id"),
                                                    "hypothesis_id": s.get("hypothesis_id")}, now)
    out["closed"] = close_for_task(db, task_id, summary, now) + settle(db, now)
    return out


def close_for_task(db, task_id, summary, now) -> list[int]:
    """Marks whose re-verification this task was: 'done' with what the evidence did to the subject."""
    closed = []
    superseded = {s.get("old") for s in summary.get("superseded") or []}
    contested = {c.get("knowledge_id") for c in summary.get("contested") or []}
    confirmed = set(summary.get("strengthened") or []) | set(summary.get("unchanged") or [])
    for mid, kind, sid in db.execute("SELECT id, subject_kind, subject_id FROM web_rechecks WHERE task_id=? AND "
                                     "status='queued'", (task_id,)).fetchall():
        if kind == "knowledge":
            outcome = ("superseded by new evidence" if sid in superseded else
                       "contradicted by new evidence" if sid in contested else
                       "confirmed by new evidence" if sid in confirmed else "no evidence about it")
        else:
            settled = [s for s in summary.get("predictions_settled") or [] if s.get("hypothesis_id") == sid]
            premise = [k for k in knowledge_premises(db, sid) if k in confirmed | superseded | contested]
            outcome = (f"prediction {settled[0]['outcome']}" if settled else
                       "its knowledge premise was re-verified" if premise else "no decisive evidence")
        db.execute("UPDATE web_rechecks SET status='done', outcome=?, updated_at=? WHERE id=?", (outcome, now, mid))
        closed.append(mid)
    return closed


def release_stuck(db, now) -> int:
    """Queued marks whose task ended without consolidating (failed, cancelled, partial by quiet) go back to 'marked',
    counting the attempt (cause["attempts"]); at MAX_ATTEMPTS the mark is dropped with that reason (review1 CL6: it
    could be queued again for ever). Caller owns the txn."""
    rows = db.execute("SELECT id, cause FROM web_rechecks WHERE status='queued' AND (task_id IS NULL OR NOT EXISTS "
                      "(SELECT 1 FROM web_tasks t WHERE t.id=web_rechecks.task_id) OR EXISTS (SELECT 1 FROM web_tasks "
                      "t WHERE t.id=web_rechecks.task_id AND t.status IN ('failed','cancelled','completed',"
                      "'partial')))").fetchall()
    for mid, cause in rows:
        value = state.loads(cause, {})
        attempts = int(value.get("attempts") or 0) + 1
        value["attempts"] = attempts
        if attempts >= MAX_ATTEMPTS:
            db.execute("UPDATE web_rechecks SET status='dropped', task_id=NULL, cause=?, outcome=?, updated_at=? "
                       "WHERE id=?", (state.dumps(value), f"its re-verification ended without evidence {attempts} "
                                      "times: dropped", now, mid))
        else:
            db.execute("UPDATE web_rechecks SET status='marked', task_id=NULL, cause=?, updated_at=? WHERE id=?",
                       (state.dumps(value), now, mid))
    return len(rows)


def _answered(db, kind, subject_id, since) -> str | None:
    """Why an open mark made at `since` no longer asks anything, or None while it still does."""
    if kind == "knowledge":
        row = db.execute("SELECT status, last_verified_at FROM web_knowledge WHERE id=?", (subject_id,)).fetchone()
        if row is None:
            return None                     # forgotten: purge deletes it
        if row[0] not in state.LIVE_KNOWLEDGE:
            return f"closed: the item is {row[0]}"
        if state.finite(row[1], 0.0) > since:
            return "closed: re-verified since it was marked"
        return None
    row = db.execute("SELECT status FROM web_hypotheses WHERE id=?", (subject_id,)).fetchone()
    if row is None:
        return None
    if row[0] != "open":
        return f"closed: the hypothesis is {row[0]}"
    held = knowledge_premises(db, subject_id)
    if not held:
        return "closed: no live knowledge premise is left to re-verify"
    verified = db.execute(f"SELECT max(last_verified_at) FROM web_knowledge WHERE id IN ({','.join('?' * len(held))})",
                          held).fetchone()[0]
    if state.finite(verified, 0.0) > since:
        return "closed: its knowledge premise was re-verified since it was marked"
    return None


def settle(db, now) -> list[int]:
    """Close every open mark whose question some normal path answered (_answered; the module doc), with the reason
    in its outcome. Caller owns the txn. Returns the ids closed."""
    if not present(db):
        return []
    closed = []
    for mid, kind, sid, created in db.execute("SELECT id, subject_kind, subject_id, created_at FROM web_rechecks "
                                              "WHERE status IN ('marked','queued') ORDER BY id").fetchall():
        why = _answered(db, kind, sid, created)
        if why is not None:
            db.execute("UPDATE web_rechecks SET status='done', outcome=?, updated_at=? WHERE id=?", (why, now, mid))
            closed.append(mid)
    return closed


# ---- forget and restore --------------------------------------------------------------------------------------------------

def _exists(db, kind, record_id) -> bool:
    table = _TABLES[kind]
    if kind != "claim":
        try:
            record_id = int(record_id)
        except (TypeError, ValueError):
            return False
    else:
        record_id = str(record_id)
    return db.execute(f"SELECT 1 FROM {table} WHERE id=? LIMIT 1", (record_id,)).fetchone() is not None


# premise rows whose premise, or whose dependent, is no longer held (indexed lookups, one statement each)
_GONE_PREMISE = ("(p.premise_kind='claim' AND NOT EXISTS (SELECT 1 FROM web_claims c WHERE c.id=p.premise_id)) OR "
                 "(p.premise_kind='knowledge' AND NOT EXISTS (SELECT 1 FROM web_knowledge k WHERE "
                 "k.id=CAST(p.premise_id AS INTEGER))) OR (p.premise_kind='hypothesis' AND NOT EXISTS (SELECT 1 FROM "
                 "web_hypotheses h WHERE h.id=CAST(p.premise_id AS INTEGER)))")
_GONE_DEPENDENT = ("(p.dependent_kind='knowledge' AND NOT EXISTS (SELECT 1 FROM web_knowledge k WHERE "
                   "k.id=p.dependent_id)) OR (p.dependent_kind='hypothesis' AND NOT EXISTS (SELECT 1 FROM "
                   "web_hypotheses h WHERE h.id=p.dependent_id))")


def purge(db, now) -> dict:
    """After records were forgotten (live forget or a restore's replay): the held dependents of a premise that is
    gone are marked 'premise_forgotten' (the mark names no forgotten id); then every premise row and mark naming a
    record no longer held is deleted, and a mark's cause that names one loses that cause. Caller owns the txn.
    Hypothesis details naming a removed record are pruned too. Returns {"premises_removed", "rechecks_removed",
    "marked", "details_removed"} (zeros when lane K's migration is absent)."""
    out = {"premises_removed": 0, "rechecks_removed": 0, "marked": 0, "details_removed": 0, "rechecks_closed": 0}
    if not present(db):
        return out
    gone = db.execute(f"SELECT DISTINCT p.premise_kind, p.premise_id FROM web_premises p WHERE {_GONE_PREMISE}"
                      ).fetchall()
    marking = enabled(db)
    for kind, pid in gone:
        if marking:
            for dkind, did in dependents(db, kind, pid)[:MAX_MARKS_PER_CHANGE]:
                out["marked"] += bool(mark(db, dkind, did, "premise_forgotten", {}, now))
        out["premises_removed"] += db.execute("DELETE FROM web_premises WHERE premise_kind=? AND premise_id=?",
                                              (kind, pid)).rowcount
    out["premises_removed"] += db.execute(f"DELETE FROM web_premises WHERE rowid IN (SELECT p.rowid FROM "
                                          f"web_premises p WHERE {_GONE_DEPENDENT})").rowcount
    for mid, kind, sid, cause in db.execute("SELECT id, subject_kind, subject_id, cause FROM web_rechecks").fetchall():
        if not _exists(db, kind, sid):
            out["rechecks_removed"] += db.execute("DELETE FROM web_rechecks WHERE id=?", (mid,)).rowcount
            continue
        value = state.loads(cause, {})
        kept = _kept_cause(db, value)
        if kept != value:
            db.execute("UPDATE web_rechecks SET cause=? WHERE id=?", (state.dumps(kept), mid))
    db.execute("UPDATE web_rechecks SET task_id=NULL WHERE task_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM web_tasks "
               "t WHERE t.id=web_rechecks.task_id)")
    out["details_removed"] = _purge_details(db)
    out["rechecks_closed"] = len(settle(db, now))
    return out


def _purge_details(db) -> int:
    """Hypothesis details (evolution._describe) of a hypothesis no longer held go; counter-evidence loses claims no
    longer held; a revision's evidence loses a task no longer held. Caller owns the txn."""
    removed = db.execute("DELETE FROM web_hypothesis_details WHERE NOT EXISTS (SELECT 1 FROM web_hypotheses h WHERE "
                         "h.id=web_hypothesis_details.hypothesis_id)").rowcount
    for hid, counter, revisions in db.execute("SELECT hypothesis_id, counter_evidence, revisions FROM "
                                              "web_hypothesis_details").fetchall():
        kept = [c for c in state.loads(counter, []) if _exists(db, "claim", c)]
        revs = state.loads(revisions, [])
        for r in revs:
            evidence = r.get("evidence") or {}
            if evidence.get("task_id") is not None and not db.execute("SELECT 1 FROM web_tasks WHERE id=?",
                                                                      (evidence["task_id"],)).fetchone():
                evidence["task_id"] = None
                evidence["forgotten"] = True
            if evidence.get("claims"):
                evidence["claims"] = [c for c in evidence["claims"] if _exists(db, "claim", c)]
        if kept != state.loads(counter, []) or revs != state.loads(revisions, []):
            db.execute("UPDATE web_hypothesis_details SET counter_evidence=?, revisions=? WHERE hypothesis_id=?",
                       (state.dumps(kept), state.dumps(revs), hid))
    return removed


def _kept_cause(db, cause: dict) -> dict:
    def alive(c):
        if c.get("kind") in _TABLES and c.get("id") is not None and not _exists(db, c["kind"], c["id"]):
            return False
        if c.get("prediction_id") is not None and db.execute("SELECT 1 FROM web_predictions WHERE id=?",
                                                             (c["prediction_id"],)).fetchone() is None:
            return False
        if c.get("task_id") is not None and db.execute("SELECT 1 FROM web_tasks WHERE id=?",
                                                       (c["task_id"],)).fetchone() is None:
            return False
        return True
    out = {k: v for k, v in cause.items() if k != "also"} if alive(cause) else \
        {"forgotten": True} | ({"attempts": cause["attempts"]} if "attempts" in cause else {})
    also = [c for c in cause.get("also") or [] if alive(c)]
    if also:
        out["also"] = also
    return out


# ---- reading ----------------------------------------------------------------------------------------------------------------

def listing(db, *, status=None, limit=50) -> list[dict]:
    if not present(db):
        return []
    where, params = ("WHERE status=?", (status,)) if status else ("", ())
    return [{"id": i, "subject": {"kind": k, "id": s}, "reason": r, "cause": state.loads(c, {}), "status": st,
             "task_id": t, "outcome": o, "created_at": ca, "updated_at": ua}
            for i, k, s, r, c, st, t, o, ca, ua in db.execute(
                "SELECT id, subject_kind, subject_id, reason, cause, status, task_id, outcome, created_at, updated_at "
                f"FROM web_rechecks {where} ORDER BY id DESC LIMIT ?", (*params, int(limit)))]


def status(db) -> dict:
    if not present(db):
        return {"present": False}
    counts = dict(db.execute("SELECT status, count(*) FROM web_rechecks GROUP BY status").fetchall())
    return {"present": True, "enabled": enabled(db), "marks": {s: counts.get(s, 0) for s in
                                                                ("marked", "queued", "done", "dropped")},
            "premises": db.execute("SELECT count(*) FROM web_premises").fetchone()[0],
            "note": "A mark changes nothing by itself; the subject changes only through new evidence."}
