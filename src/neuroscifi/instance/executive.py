"""The executive: choose the next step for an epoch, keep open questions and honour the ask budget.

Policy (first match wins):
1 writes paused -> defer
2 change suspected and not ambiguous -> ask (pending kind 'calibrate', scope = changed scope)
3 change ambiguous -> test_reference if the epoch has any reference evidence, else ask
4 no learning evidence yet -> observe
5 mechanism not identifiable (|p-.5| < .37) -> test_reference
6 a partner with >= 4 learning words still has |p-.5| < .37 -> ask
7 otherwise -> act
An ask that needs a new pending row once config.ask_budget_per_epoch ask/calibrate rows exist in the
epoch becomes defer. Re-deciding the same open question reuses its pending row.
"""
from . import beliefs, store
from .errors import Conflict, NotFound, ValidationError

MIN_PARTNER_WORDS = 4
_PENDING_COLUMNS = ("id", "epoch", "context", "kind", "scope", "question", "created_at", "resolved_at", "resolution")


def _policy(db, epoch, allowed, why):
    """Return (decision, pending kind or None, scope, reason, question or None) before the ask budget."""
    if not allowed:
        return "defer", None, None, f"writes are paused ({why}); defer until regulation allows new evidence", None
    change = beliefs.detect_change(db, epoch)
    if change["suspected"]:
        scope, since, llr = change["scope"], change["since_seq"], change["llr"]
        runner = change["alternatives"][0] if change["alternatives"] else None
        detail = (f"; runner-up {runner['scope']} at {runner['llr']:.2f}" if runner else "")
        if not change["ambiguous"]:
            reason = (f"change suspected in {scope} since evidence #{since}: penalised log-likelihood gain "
                      f"{llr:.2f} nats > log(19)={beliefs.CHANGE_THRESHOLD:.2f}{detail}; ask the owner to "
                      f"confirm and recalibrate {scope}")
            question = (f"The readings for {scope} since evidence #{since} fit a flipped mapping "
                        f"(gain {llr:.2f} nats). Did {scope} change? Please confirm and recalibrate.")
            return "ask", "calibrate", scope, reason, question
        reason = (f"change suspected since evidence #{since} but ambiguous: {scope} at {llr:.2f} nats{detail}, "
                  f"within {beliefs.AMBIGUITY_MARGIN} nat")
        has_reference = db.execute("SELECT 1 FROM evidence WHERE epoch=? AND channel='reference' LIMIT 1",
                                   (epoch,)).fetchone() is not None
        rival = runner["scope"] if runner else "another factor"
        if has_reference:
            return ("test_reference", "test_reference", scope,
                    reason + f"; {beliefs.LEGACY_REFERENCE_TEXT} to separate {scope} from {rival}",
                    f"Run a known mechanical reference to tell whether {scope} or {rival} changed "
                    f"since evidence #{since}.")
        return ("ask", "ask", scope, reason + "; no reference evidence in this epoch, so ask which one changed",
                f"Something changed around evidence #{since}: was it {scope} or {rival}?")
    result = beliefs.compute(db, epoch)
    counts = result["evidence"]
    if counts["learning"] == 0:
        return ("observe", None, None,
                f"epoch {epoch} has no learning evidence yet ({counts['total']} stored, "
                f"{counts['memory_only']} memory-only); act and record camera/word reports", None)
    p = result["mechanism"]["p_inverted"]
    if not result["mechanism"]["identifiable"]:
        return ("test_reference", "test_reference", "mechanism",
                f"mechanism p_inverted={p:.3f} is within {beliefs.IDENTIFIABLE_MARGIN} of 0.5 with "
                f"{result['reference_roots']} reference root(s); {beliefs.LEGACY_REFERENCE_TEXT}",
                f"Please {beliefs.LEGACY_REFERENCE_TEXT} (mechanism p_inverted={p:.3f}).")
    ambiguous = sorted((abs(v["p_inverted"] - .5), root, v) for root, v in result["partners"].items()
                       if v["n"] >= MIN_PARTNER_WORDS and abs(v["p_inverted"] - .5) < beliefs.IDENTIFIABLE_MARGIN)
    if ambiguous:
        _, root, v = ambiguous[0]
        return ("ask", "ask", f"partner:{root}",
                f"partner {root} has {v['n']} learning words but p_inverted={v['p_inverted']:.3f} is within "
                f"{beliefs.IDENTIFIABLE_MARGIN} of 0.5, so the meaning is still ambiguous for that partner; ask",
                f"What does {root} mean by their words? After {v['n']} words the convention is still "
                f"ambiguous (p_inverted={v['p_inverted']:.3f}).")
    return ("act", None, None,
            f"beliefs are resolved (mechanism p_inverted={p:.3f}, {len(result['sensors'])} camera root(s), "
            f"{len(result['partners'])} partner(s)); {beliefs.LEGACY_ACT_TEXT}", None)


def _open_pending(db, epoch, kind, scope):
    row = db.execute("SELECT id FROM pending WHERE epoch=? AND kind=? AND scope=? AND resolved_at IS NULL "
                     "ORDER BY id LIMIT 1", (epoch, kind, scope)).fetchone()
    return row[0] if row else None


def _decide(db, epoch, now, record, path):
    beliefs.require_epoch(db, epoch)
    budget = store.config(db).ask_budget_per_epoch
    asks_used = db.execute("SELECT count(*) FROM pending WHERE epoch=? AND kind IN ('ask','calibrate')",
                           (epoch,)).fetchone()[0]
    allowed, why = store.write_allowed(db, path)
    decision, kind, scope, reason, question = _policy(db, epoch, allowed, why)
    wanted = (kind, scope)  # the policy's own recommendation, before any budget conversion
    pending_id = _open_pending(db, epoch, kind, scope) if kind else None
    if decision == "ask" and pending_id is None and asks_used >= budget:
        reason = (f"ask budget exhausted: {asks_used} of {budget} questions already asked in epoch {epoch}; "
                  f"deferring instead of: {reason}")
        decision, kind = "defer", None
    closed = []
    if record and allowed:
        version = store.bump_state_version(db)
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
            "asks_used": asks_used, "ask_budget": budget, "closed_pending": closed}


def decide(db, epoch, now, *, record=False, path=None):
    """Apply the 7-rule policy and the ask budget. With record=True, write the decision and any new pending row.

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


def resolve(db, pending_id, resolution, now):
    """Close an open question with the owner's answer; bumps teacher_interventions.

    Repeating the same resolution is idempotent; a different answer to a closed question is a Conflict.
    """
    if isinstance(pending_id, bool) or not isinstance(pending_id, int):
        raise ValidationError("pending id must be an integer")
    if not isinstance(resolution, str) or not resolution.strip() or len(resolution) > 1024:
        raise ValidationError("resolution must be a non-blank string of at most 1024 characters")
    select = f"SELECT {','.join(_PENDING_COLUMNS)} FROM pending WHERE id=?"
    with store.write(db):
        row = db.execute(select, (pending_id,)).fetchone()
        if row is None:
            raise NotFound(f"No pending question {pending_id}")
        current = dict(zip(_PENDING_COLUMNS, row))
        if current["resolved_at"] is not None:
            if current["resolution"] == resolution:
                return current
            raise Conflict(f"Pending question {pending_id} was already resolved: {current['resolution']!r}")
        db.execute("UPDATE pending SET resolved_at=?, resolution=? WHERE id=?", (now, resolution, pending_id))
        store.bump(db, "teacher_interventions")
        store.bump_state_version(db)
        return dict(zip(_PENDING_COLUMNS, db.execute(select, (pending_id,)).fetchone()))
