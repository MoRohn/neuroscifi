"""The executive: choose the next step for an epoch, keep open questions and honour the ask budget.

Policy (first match wins):
1 writes paused -> defer
2 change suspected and not ambiguous -> ask (pending kind 'calibrate', scope = changed scope)
3 change ambiguous -> test_reference if the epoch has any reference evidence, else ask
4 no learning evidence yet -> observe
4b the epoch has <= beliefs.MATCH_EVENTS learning events and match_epochs names an earlier epoch -> ask
   (scope 'epoch:<best>', "This looks like <best>; continue there?"), at most once per epoch
5 mechanism not identifiable (|p-.5| < .37) -> test_reference
6 a partner with >= 4 learning words still has |p-.5| < .37 -> ask (the most ambiguous partner that is
  not answered, see below)
7 otherwise -> act; never while a non-ambiguous change suspicion is open (that defers instead)

Answered questions are not re-asked for the same events. A change the owner confirmed or rejected
(`resolve(..., confirm=True|False)`) is handled by beliefs.detect_change. Any other owner answer to an
ask/calibrate question of a scope suppresses a new question of the same kind and scope until at least
3 learning events of that scope arrived after the evidence the question was about (its "evidence through
#H" horizon); a suppressed change question defers, a suppressed partner question lets the next rule run.
An ask that needs a new pending row once config.ask_budget_per_epoch ask/calibrate rows were created in
the epoch becomes defer. Re-deciding the same open question reuses its pending row.

Working memory decays: decide(record=True) closes ask/test_reference/calibrate questions left open for
more than 7 days ('expired: no answer within 7 days').
"""
import re

from . import beliefs, store
from .errors import Conflict, NotFound, ValidationError

MIN_PARTNER_WORDS = 4
QUESTION_TTL_SECONDS = 7 * 86400
EXPIRED = "expired: no answer within 7 days"
_SYSTEM_RESOLUTIONS = ("resolved by evidence:", "expired:")
_PENDING_COLUMNS = ("id", "epoch", "context", "kind", "scope", "question", "created_at", "resolved_at", "resolution")
_SINCE = re.compile(r"since evidence #(\d+)")
_HORIZON = re.compile(r"\(evidence through #(\d+)\)")


def _horizon(db, epoch):
    return db.execute("SELECT coalesce(max(seq), 0) FROM evidence WHERE epoch=?", (epoch,)).fetchone()[0]


def _new_events(db, epoch, scope, after):
    """Learning events of `scope` with seq > after."""
    sql = f"SELECT count(*) FROM evidence WHERE epoch=? AND seq>? AND {beliefs.learning_sql(db)}"
    params = [epoch, after]
    if scope != "mechanism":
        prefix, root = scope.split(":", 1)
        sql += " AND channel=? AND root=?"
        params += [beliefs._CHANNEL[prefix], root]
    return db.execute(sql, params).fetchone()[0]


def _answered(db, epoch, kind, scope):
    """Id of the latest owner-answered (kind, scope) question if fewer than 3 events of the scope followed it."""
    if not scope.startswith(("mechanism", "camera:", "partner:")):
        return None
    system = " AND ".join("resolution NOT LIKE ?" for _ in _SYSTEM_RESOLUTIONS)
    row = db.execute(f"SELECT id, question FROM pending WHERE epoch=? AND kind=? AND scope=? "
                     f"AND resolved_at IS NOT NULL AND {system} ORDER BY id DESC LIMIT 1",
                     (epoch, kind, scope, *(prefix + "%" for prefix in _SYSTEM_RESOLUTIONS))).fetchone()
    if row is None:
        return None
    match = _HORIZON.search(row[1])
    horizon = int(match.group(1)) if match else 0
    return row[0] if _new_events(db, epoch, scope, horizon) < beliefs.MIN_AFTER_CHANGE else None


def _match_rule(db, epoch, learning):
    """Rule 4b: (best epoch, match) or None."""
    if not 0 < learning <= beliefs.MATCH_EVENTS:
        return None
    if db.execute("SELECT 1 FROM pending WHERE epoch=? AND scope LIKE 'epoch:%' AND resolved_at IS NOT NULL",
                  (epoch,)).fetchone():
        return None   # once per epoch: an earlier-epoch question was already answered or closed
    match = beliefs.match_epochs(db, epoch, last=beliefs.MATCH_EVENTS)
    return (match["best"], match) if match["best"] is not None else None


def _policy(db, epoch, allowed, why, horizon):
    """Return (decision, pending kind or None, scope, reason, question or None) before the ask budget."""
    if not allowed:
        return "defer", None, None, f"writes are paused ({why}); defer until regulation allows new evidence", None
    result = beliefs._assess(db, epoch, legacy=False)[0]
    change = result["change"]
    through = f" (evidence through #{horizon})"
    if change["suspected"]:
        scope, since, llr = change["scope"], change["since_seq"], change["llr"]
        runner = change["alternatives"][0] if change["alternatives"] else None
        detail = (f"; runner-up {runner['scope']} at {runner['llr']:.2f}" if runner else "")
        if not change["ambiguous"]:
            reason = (f"change suspected in {scope} since evidence #{since}: penalised log-likelihood gain "
                      f"{llr:.2f} nats > log(19)={beliefs.CHANGE_THRESHOLD:.2f}{detail}")
            answered = _answered(db, epoch, "calibrate", scope)
            if answered is not None:
                return ("defer", None, scope,
                        reason + f"; question #{answered} about {scope} was answered without confirming or "
                        f"rejecting the change; waiting for {beliefs.MIN_AFTER_CHANGE} new {scope} events before "
                        "asking again (resolve with confirm to relearn only this scope)", None)
            question = (f"The readings for {scope} since evidence #{since} fit a flipped mapping "
                        f"(gain {llr:.2f} nats). Did {scope} change? Please confirm and recalibrate.{through}")
            return ("ask", "calibrate", scope, reason + f"; ask the owner to confirm and recalibrate {scope}",
                    question)
        reason = (f"change suspected since evidence #{since} but ambiguous: {scope} at {llr:.2f} nats{detail}, "
                  f"within {beliefs.AMBIGUITY_MARGIN} nat")
        has_reference = db.execute("SELECT 1 FROM evidence WHERE epoch=? AND channel='reference' LIMIT 1",
                                   (epoch,)).fetchone() is not None
        rival = runner["scope"] if runner else "another factor"
        if has_reference:
            return ("test_reference", "test_reference", scope,
                    reason + f"; {beliefs.LEGACY_REFERENCE_TEXT} to separate {scope} from {rival}",
                    f"Run a known mechanical reference to tell whether {scope} or {rival} changed "
                    f"since evidence #{since}.{through}")
        answered = _answered(db, epoch, "ask", scope)
        if answered is not None:
            return ("defer", None, scope, reason + f"; question #{answered} about {scope} was already answered; "
                    f"waiting for {beliefs.MIN_AFTER_CHANGE} new {scope} events before asking again", None)
        return ("ask", "ask", scope, reason + "; no reference evidence in this epoch, so ask which one changed",
                f"Something changed around evidence #{since}: was it {scope} or {rival}?{through}")
    counts = result["evidence"]
    if counts["learning"] == 0:
        return ("observe", None, None,
                f"epoch {epoch} has no learning evidence yet ({counts['total']} stored, "
                f"{counts['memory_only']} memory-only); act and record camera/word reports", None)
    matched = _match_rule(db, epoch, counts["learning"])
    if matched is not None:
        best, match = matched
        top = match["ranking"][0]
        return ("ask", "ask", f"epoch:{best}",
                f"epoch {epoch} has {counts['learning']} learning event(s); under {best}'s beliefs they score "
                f"{top['log_likelihood']:.2f} nats, at least log(19) above every other epoch and unknown "
                f"({match['unknown_log_likelihood']:.2f}); ask whether this is the earlier setup",
                f"This looks like {best}; continue there?{through}")
    p = result["mechanism"]["p_inverted"]
    if not result["mechanism"]["identifiable"]:
        return ("test_reference", "test_reference", "mechanism",
                f"mechanism p_inverted={p:.3f} is within {beliefs.IDENTIFIABLE_MARGIN} of 0.5 with "
                f"{result['reference_roots']} reference root(s); {beliefs.LEGACY_REFERENCE_TEXT}",
                f"Please {beliefs.LEGACY_REFERENCE_TEXT} (mechanism p_inverted={p:.3f}).{through}")
    ambiguous = sorted((abs(v["p_inverted"] - .5), root, v) for root, v in result["partners"].items()
                       if v["n"] >= MIN_PARTNER_WORDS and abs(v["p_inverted"] - .5) < beliefs.IDENTIFIABLE_MARGIN)
    skipped = []
    for _, root, v in ambiguous:
        answered = _answered(db, epoch, "ask", f"partner:{root}")
        if answered is not None:
            skipped.append(f"partner {root} (question #{answered} answered)")
            continue
        return ("ask", "ask", f"partner:{root}",
                f"partner {root} has {v['n']} learning words but p_inverted={v['p_inverted']:.3f} is within "
                f"{beliefs.IDENTIFIABLE_MARGIN} of 0.5, so the meaning is still ambiguous for that partner; ask",
                f"What does {root} mean by their words? After {v['n']} words the convention is still "
                f"ambiguous (p_inverted={v['p_inverted']:.3f}).{through}")
    note = f"; still ambiguous but already answered: {', '.join(skipped)}" if skipped else ""
    return ("act", None, None,
            f"beliefs are resolved (mechanism p_inverted={p:.3f}, {len(result['sensors'])} camera root(s), "
            f"{len(result['partners'])} partner(s)); {beliefs.LEGACY_ACT_TEXT}{note}", None)


def _open_pending(db, epoch, kind, scope, now):
    row = db.execute("SELECT id FROM pending WHERE epoch=? AND kind=? AND scope=? AND resolved_at IS NULL "
                     "AND created_at>=? ORDER BY id LIMIT 1", (epoch, kind, scope, now - QUESTION_TTL_SECONDS)).fetchone()
    return row[0] if row else None


def _decide(db, epoch, now, record, path):
    beliefs.require_epoch(db, epoch)
    budget = store.config(db).ask_budget_per_epoch
    asks_used = db.execute("SELECT count(*) FROM pending WHERE epoch=? AND kind IN ('ask','calibrate')",
                           (epoch,)).fetchone()[0]
    allowed, why = store.write_allowed(db, path)
    decision, kind, scope, reason, question = _policy(db, epoch, allowed, why, _horizon(db, epoch))
    wanted = (kind, scope)  # the policy's own recommendation, before any budget conversion
    pending_id = _open_pending(db, epoch, kind, scope, now) if kind else None
    if decision == "ask" and pending_id is None and asks_used >= budget:
        reason = (f"ask budget exhausted: {asks_used} of {budget} questions already asked in epoch {epoch}; "
                  f"deferring instead of: {reason}")
        decision, kind = "defer", None
    closed, expired = [], []
    if record and allowed:
        version = store.bump_state_version(db)
        # Working-memory decay: questions nobody answered within 7 days (event time) are closed.
        for (pid,) in db.execute(
                "SELECT id FROM pending WHERE resolved_at IS NULL AND kind IN ('ask','test_reference','calibrate') "
                "AND created_at<? ORDER BY id", (now - QUESTION_TTL_SECONDS,)).fetchall():
            db.execute("UPDATE pending SET resolved_at=?, resolution=? WHERE id=?", (now, EXPIRED, pid))
            expired.append(pid)
        # Working memory hygiene (MEM-01): an open question whose premise the evidence has since removed is
        # closed with an auditable resolution. new_context questions belong to recall and are left alone.
        for pid, pkind, pscope in db.execute(
                "SELECT id,kind,scope FROM pending WHERE epoch=? AND resolved_at IS NULL "
                "AND kind IN ('ask','test_reference','calibrate') ORDER BY id", (epoch,)).fetchall():
            if (pkind, pscope) != wanted:
                db.execute("UPDATE pending SET resolved_at=?, resolution=? WHERE id=?",
                           (now, f"resolved by evidence: the executive now recommends {decision}"
                                 + (f" for {scope}" if scope else ""), pid))
                closed.append(pid)
        if kind and pending_id is None:
            pending_id = db.execute(
                "INSERT INTO pending(epoch,kind,scope,question,created_at) VALUES(?,?,?,?,?)",
                (epoch, kind, scope, question, now)).lastrowid
            if kind in ("ask", "calibrate"):
                asks_used += 1
        db.execute("INSERT INTO decisions(epoch,state_version,decision,scope,reason,created_at) VALUES(?,?,?,?,?,?)",
                   (epoch, version, decision, scope, reason, now))
    return {"decision": decision, "scope": scope, "reason": reason, "pending_id": pending_id,
            "asks_used": asks_used, "ask_budget": budget, "closed_pending": closed, "expired_pending": expired}


def decide(db, epoch, now, *, record=False, path=None):
    """Apply the policy and the ask budget. With record=True, write the decision and any new pending row.

    While writes are paused the decision is defer and nothing is written, even with record=True.
    """
    if not record:
        return _decide(db, epoch, now, False, path)
    with store.write(db):
        return _decide(db, epoch, now, True, path)


def pending(db, *, epoch=None, include_resolved=False):
    """Open questions (or all with include_resolved=True), oldest first, optionally for one epoch."""
    where, params = [], []
    if epoch is not None:
        beliefs.require_epoch(db, epoch)
        where.append("epoch=?")
        params.append(epoch)
    if not include_resolved:
        where.append("resolved_at IS NULL")
    sql = f"SELECT {','.join(_PENDING_COLUMNS)} FROM pending"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return [dict(zip(_PENDING_COLUMNS, row)) for row in db.execute(sql + " ORDER BY id", params)]


def _changepoint(db, pending_id):
    if not beliefs._has_changepoints(db):
        return None
    row = db.execute("SELECT id,epoch,scope,since_seq,created_at,confirmed FROM changepoints WHERE pending_id=? "
                     "ORDER BY id LIMIT 1", (pending_id,)).fetchone()
    if row is None:
        return None
    return dict(zip(("id", "epoch", "scope", "since_seq", "created_at"), row[:5]), confirmed=bool(row[5]))


def _record_answer(db, current, confirm, now):
    """Insert the changepoint row for a confirm=True/False answer to a change question. Caller owns the txn."""
    epoch, scope, kind = current["epoch"], current["scope"], current["kind"]
    if scope.startswith("epoch:"):
        return None    # the owner's answer to "continue there?" is recorded in the resolution text
    if kind not in ("calibrate", "ask") or not scope.startswith(("mechanism", "camera:", "partner:")):
        raise ValidationError(f"Question {current['id']} ({kind} about {scope}) is not about a change; "
                              "answer it without confirm")
    match = _SINCE.search(current["question"])
    since = int(match.group(1)) if match else None
    if since is None:
        change = beliefs.detect_change(db, epoch)
        if not change["suspected"] or change["scope"] != scope:
            raise ValidationError(f"Question {current['id']} names no change of {scope} and none is suspected now")
        since = change["since_seq"]
    if confirm:
        known = beliefs.changes(db, epoch)["confirmed"]
        if scope == "mechanism" and since not in known.get("mechanism", []) \
                and len(known.get("mechanism", [])) + 1 >= beliefs.MAX_MECHANISM_SEGMENTS:
            raise ValidationError(f"Epoch {epoch} already has {beliefs.MAX_MECHANISM_SEGMENTS} mechanism segments; "
                                  "start a new epoch instead")
        seq = since
    else:
        seq = _horizon(db, epoch)   # a rejection covers the evidence the owner has seen
    cid = db.execute("INSERT INTO changepoints(epoch,scope,since_seq,pending_id,created_at,confirmed) "
                     "VALUES(?,?,?,?,?,?)", (epoch, scope, seq, current["id"], now, int(bool(confirm)))).lastrowid
    return {"id": cid, "epoch": epoch, "scope": scope, "since_seq": seq, "created_at": now, "confirmed": bool(confirm)}


def resolve(db, pending_id, resolution, now, *, confirm=None):
    """Close an open question with the owner's answer; bumps teacher_interventions.

    confirm=True on a change question (kind 'calibrate', or an 'ask' about a change) records a confirmed
    changepoint (scope, since_seq from the question, else from detect_change): that scope alone is relearned
    from a new hidden bit, everything else keeps its evidence. confirm=False records a rejection: the scope
    keeps pooling and is not reported again until 3 of its events arrive. confirm=None only closes the
    question. Returns the pending row plus "changepoint" (the recorded row or None).
    Repeating the same answer is idempotent; a different answer to a closed question is a Conflict.
    """
    if isinstance(pending_id, bool) or not isinstance(pending_id, int):
        raise ValidationError("pending id must be an integer")
    if not isinstance(resolution, str) or not resolution.strip() or len(resolution) > 1024:
        raise ValidationError("resolution must be a non-blank string of at most 1024 characters")
    if confirm not in (None, True, False):
        raise ValidationError("confirm must be true, false or omitted")
    select = f"SELECT {','.join(_PENDING_COLUMNS)} FROM pending WHERE id=?"
    with store.write(db):
        beliefs.ensure_tables(db)
        row = db.execute(select, (pending_id,)).fetchone()
        if row is None:
            raise NotFound(f"No pending question {pending_id}")
        current = dict(zip(_PENDING_COLUMNS, row))
        if current["resolved_at"] is not None:
            recorded = _changepoint(db, pending_id)
            previous = recorded["confirmed"] if recorded else None
            if current["resolution"] == resolution and (confirm is None or confirm == previous
                                                        or current["scope"].startswith("epoch:")):
                return {**current, "changepoint": recorded}
            raise Conflict(f"Pending question {pending_id} was already resolved: {current['resolution']!r}")
        changepoint = _record_answer(db, current, confirm, now) if confirm is not None else None
        db.execute("UPDATE pending SET resolved_at=?, resolution=? WHERE id=?", (now, resolution, pending_id))
        store.bump(db, "teacher_interventions")
        store.bump_state_version(db)
        if changepoint is not None:
            beliefs.record_revision(db, current["epoch"], now, f"resolve:{pending_id}")
        return {**dict(zip(_PENDING_COLUMNS, db.execute(select, (pending_id,)).fetchone())),
                "changepoint": changepoint}
