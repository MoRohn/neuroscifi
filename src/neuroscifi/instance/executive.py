"""The executive: choose the next step for an epoch, keep open questions and honour the ask budget.

Policy (first match wins):
1 writes paused -> defer
2 change suspected and not ambiguous -> ask (pending kind 'calibrate', scope = changed scope)
3 change ambiguous -> test_reference if the epoch has any reference evidence, else ask
4 no learning evidence yet -> observe
4b the epoch has <= beliefs.MATCH_EVENTS learning events, continues no earlier setup yet, and match_epochs
   names an earlier epoch E (posterior >= beliefs.MATCH_POSTERIOR) -> ask (scope 'epoch:<E>', "This looks like
   E before the camera:x change at #S; continue with that calibration?"), at most once per (epoch, E):
   a question about E that was answered or closed in this epoch is not asked again
5 mechanism not identifiable (|p-.5| < .37) -> test_reference
6 a partner with >= 4 learning words still has |p-.5| < .37 -> ask (the most ambiguous partner that is
  not answered, see below)
7 otherwise -> act; never while a non-ambiguous change suspicion is open (that defers instead)

Answered questions are not re-asked for the same events. `resolve(..., confirm=True)` on an epoch question
links the epoch to the segments of E that matched (epoch_links), so its beliefs start from E's calibration;
confirm=False records that E was declined (the answered question) and only closes it. A change the owner
confirmed or rejected (`resolve(..., confirm=True|False)`) is handled by beliefs.detect_change. Any other owner answer to an
ask/calibrate question of a scope suppresses a new question of the same kind and scope until at least
3 learning events of that scope arrived after the evidence the question was about (its "evidence through
#H" horizon); a suppressed change question defers, a suppressed partner question lets the next rule run.
An ask that needs a new pending row once config.ask_budget_per_epoch ask/calibrate rows were created in
the epoch within the last config.ask_budget_window_seconds (7 days by default; 0 = since the epoch began, so the
budget never refills) becomes defer. The window counts every question asked, answered or not: the budget bounds
how often the owner is interrupted, and an ignored question is still an interruption. Re-deciding the same open
question reuses its pending row.

Working memory decays: decide(record=True) closes ask/test_reference/calibrate questions left open for
more than 7 days ('expired: no answer within 7 days'). Web Cortex questions (scope 'web:...', epoch NULL) are not
epoch working memory: they do not decay and `resolve(..., confirm=True|False)` hands them to
webcortex.knowledge.apply_review inside the same transaction (accept/reject a knowledge candidate, authorise or
decline a monitor or research proposal).
"""
import json
import logging
import math
import re

from . import beliefs, store
from .errors import Conflict, NotFound, ValidationError

log = logging.getLogger(__name__)

MIN_PARTNER_WORDS = 4
QUESTION_TTL_SECONDS = 7 * 86400
EXPIRED = "expired: no answer within 7 days"
_SYSTEM_RESOLUTIONS = ("resolved by evidence:", "expired:")
_PENDING_COLUMNS = ("id", "epoch", "context", "kind", "scope", "question", "created_at", "resolved_at", "resolution")
_SINCE = re.compile(r"since evidence #(\d+)")
_HORIZON = re.compile(r"\(evidence through #(\d+)\)")


def _horizon(db, epoch):
    """The seq of the epoch's latest evidence, raw or consolidated (an expired event keeps its place: ADR-044)."""
    return beliefs.last_seq(db, epoch)


def _new_events(db, epoch, scope, after):
    """Learning events of `scope` with seq > after, whether still raw or consolidated since."""
    if scope == "mechanism":
        return beliefs.learning_since(db, epoch, after)
    prefix, root = scope.split(":", 1)
    return beliefs.learning_since(db, epoch, after, beliefs._CHANNEL[prefix], root)


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
    if not 0 < learning <= beliefs.MATCH_EVENTS or beliefs.link(db, epoch) is not None:
        return None
    match = beliefs.match_epochs(db, epoch, last=beliefs.MATCH_EVENTS)
    best = match["best"]
    if best is None or db.execute("SELECT 1 FROM pending WHERE epoch=? AND scope=? AND resolved_at IS NOT NULL",
                                  (epoch, f"epoch:{best}")).fetchone():
        return None   # once per (epoch, earlier epoch): that question was already answered or closed
    return best, match


def _segment_phrase(db, other, segments):
    """' before the camera:x change at #S'-style description of the matched segments of `other`."""
    confirmed = beliefs.changes(db, other)["confirmed"]
    parts = []
    for scope, label in sorted(segments.items()):
        seqs = confirmed.get(scope)
        if not seqs:
            continue
        start, end = beliefs._segment_range(confirmed, scope, label)
        if end is not None:
            parts.append(f"before the {scope} change at #{end}" + (f" (since #{start})" if start else ""))
        else:
            parts.append(f"after the {scope} change at #{start}")
    return "".join(" " + part if i == 0 else " and " + part for i, part in enumerate(parts))


def rule_version(db) -> str:
    """The individual's rule version (InstanceConfig.executive_rule; store.RULE_VERSIONS). ADR-051."""
    return store.config(db).executive_rule


def _policy(db, epoch, allowed, why, horizon, rule="rules-v1", seen=None):
    """Return (decision, pending kind or None, scope, reason, question or None) before the ask budget. `rule` is the
    rule version: rules-v1 below; information-v1 (information.py) replaces rules 5-7. `seen`, a dict, receives the
    assessment the policy read."""
    if not allowed:
        return "defer", None, None, f"writes are paused ({why}); defer until regulation allows new evidence", None
    assessed = beliefs._assess(db, epoch, legacy=False)
    result = assessed[0]
    if seen is not None:
        seen["assessed"] = assessed
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
        has_reference = beliefs.has_reference(db, epoch)
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
        where = _segment_phrase(db, best, top["segments"])
        return ("ask", "ask", f"epoch:{best}",
                f"epoch {epoch} has {counts['learning']} learning event(s); {best}{where} explains them with "
                f"posterior {top['posterior']:.3f} >= {beliefs.MATCH_POSTERIOR} against a new setup "
                f"({match['new_setup_posterior']:.3f}) and every other earlier configuration; ask whether this is "
                "the earlier setup",
                f"This looks like {best}{where}; continue with that calibration?{through}")
    if rule == "information-v1":
        from .information import information_policy
        try:            # Wave 2 lane U: a failing opt-in rule decides by rules-v1, said in the decision (rule_error)
            chosen = information_policy(db, epoch, result, assessed, horizon, _answered)
            if not (isinstance(chosen, tuple) and len(chosen) == 5):
                raise TypeError(f"information-v1 returned {type(chosen).__name__}, not a decision")
            return chosen
        except Exception as exc:          # noqa: BLE001 - shown with the decision and logged, never silent
            log.warning("executive rule information-v1 failed (%s); this decision is made by rules-v1",
                        type(exc).__name__)
            if seen is not None:
                seen["rule_error"] = f"information-v1 failed ({type(exc).__name__}); decided by rules-v1"
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


def _ask_window(cfg):
    """(window, warning): the ask budget's rolling window in seconds, 0 when the budget counts the whole epoch. A
    saved value that is no finite number of seconds from 0 up (store.set_config refuses those; a hand-edited or
    damaged settings row can hold one) gives the default window and a warning for the decision to carry, so the
    executive keeps deciding."""
    window = cfg.ask_budget_window_seconds
    if isinstance(window, bool) or not isinstance(window, (int, float)) or not math.isfinite(window) or window < 0:
        default = store.InstanceConfig.ask_budget_window_seconds
        return default, (f"the saved ask_budget_window_seconds ({window!r}) is not a number of seconds from 0 up; "
                         f"the default of {default / 86400:g} days is used (set it with `nsf config "
                         "--ask-window-days`)")
    return window, None


def _window_text(window):
    if not window:
        return ""
    days = window / 86400
    return f" in the last {days:g} day{'' if days == 1 else 's'}" if days >= 1 else f" in the last {window:g} s"


def _decide(db, epoch, now, record, path):
    beliefs.require_epoch(db, epoch)
    cfg = store.config(db)
    budget, (window, window_warning) = cfg.ask_budget_per_epoch, _ask_window(cfg)
    asks_used = db.execute("SELECT count(*) FROM pending WHERE epoch=? AND kind IN ('ask','calibrate')"
                           + (" AND created_at>?" if window else ""),
                           (epoch, now - window) if window else (epoch,)).fetchone()[0]
    allowed, why = store.write_allowed(db, path)
    rule, seen = rule_version(db), {}
    decision, kind, scope, reason, question = _policy(db, epoch, allowed, why, _horizon(db, epoch), rule, seen)
    if "rule_error" in seen:    # Wave 2 lane X (review2 L3): the stored decision says the fallback decided it
        reason = f"{reason} [{seen['rule_error']}]"
    wanted = (kind, scope)  # the policy's own recommendation, before any budget conversion
    pending_id = _open_pending(db, epoch, kind, scope, now) if kind else None
    if decision == "ask" and pending_id is None and asks_used >= budget:
        reason = (f"ask budget exhausted: {asks_used} of {budget} questions already asked in epoch {epoch}"
                  f"{_window_text(window)}; deferring instead of: {reason}")
        decision, kind = "defer", None
    closed, expired = [], []
    if record and allowed:
        version = store.bump_state_version(db)
        # Working-memory decay: questions nobody answered within 7 days (event time) are closed.
        for (pid,) in db.execute(
                "SELECT id FROM pending WHERE resolved_at IS NULL AND kind IN ('ask','test_reference','calibrate') "
                "AND created_at<? AND scope NOT LIKE 'web:%' ORDER BY id", (now - QUESTION_TTL_SECONDS,)).fetchall():
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
    out = {"decision": decision, "scope": scope, "reason": reason, "pending_id": pending_id,
           "asks_used": asks_used, "ask_budget": budget, "ask_budget_window_seconds": window,
           "closed_pending": closed, "expired_pending": expired}
    if rule != "rules-v1":      # review1 CL9: the decision of the default rule is exactly as before ADR-051
        out["rule"] = rule
    if "rule_error" in seen:
        out["rule_error"] = seen["rule_error"]
    if "assessed" in seen and cfg.decision_information:
        # ADR-051, shown beside the decision and never changing it: the exact expected information gain of each
        # question it could ask, and whether it rests on a matched earlier setup (habitual) or its own evidence
        from . import information
        assessed = seen["assessed"]
        try:            # Wave 2 lane U: a display that fails never takes the decision down
            shown = {}
            if assessed[0]["evidence"]["learning"]:
                shown["information"] = {"readings_per_question": information.READINGS,
                                        "candidates": information.assessed_gains(assessed)}
            shown["processing"] = information.processing(db, epoch, assessed[0].get("change"))
            out |= shown
        except Exception as exc:          # noqa: BLE001 - shown with the decision and logged, never silent
            log.warning("the decision's information display failed (%s)", type(exc).__name__)
            out["information_error"] = f"the information display could not be computed ({type(exc).__name__})"
    if window_warning:      # absent unless the saved window was unusable
        out["warnings"] = [window_warning]
    return out


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


PRECISION_RULES = {
    "change_alarm": "rule 2: the change detector asked to confirm a change (kind 'calibrate')",
    "change_ambiguous": "rule 3: an ambiguous change, asked which scope changed (kind 'ask')",
    "epoch_match": "rule 4b: asked whether this is an earlier setup (scope 'epoch:...')",
    "partner_meaning": "rule 6: asked what a partner means by their words (kind 'ask')",
    "reference_test": "rules 3 and 5: asked for a known reference reading (kind 'test_reference')",
    "web_knowledge": "Web Cortex: asked to accept a knowledge candidate (scope 'web:knowledge:...')",
    "web_other": "Web Cortex: other owner questions (reviews, contested subjects, proposals)",
}
_OUTCOMES = ("confirmed", "rejected", "answered", "withdrawn", "expired", "open")


def _rule_of(kind, scope, question):
    scope = scope or ""
    if scope.startswith("web:knowledge:"):
        return "web_knowledge"
    if scope.startswith("web:"):
        return "web_other"
    if kind == "calibrate":
        return "change_alarm"
    if kind == "test_reference":
        return "reference_test"
    if kind == "ask" and scope.startswith("epoch:"):
        return "epoch_match"
    if kind == "ask" and (question or "").startswith("Something changed"):
        return "change_ambiguous"
    if kind == "ask":
        return "partner_meaning"
    return None


def _rates(counts):
    decided = counts["confirmed"] + counts["rejected"]
    closed = sum(counts[k] for k in _OUTCOMES if k != "open")
    owner = decided + counts["answered"]
    return {"asked": sum(counts.values()), **counts,
            "precision": round(counts["confirmed"] / decided, 4) if decided else None,
            "owner_answered_share": round(owner / closed, 4) if closed else None}


def precision(db) -> dict:
    """How the executive's questions turned out, from records that already exist (ADR-054; display only, nothing
    reads it back and nothing is written). Per rule (PRECISION_RULES) and per epoch, every question asked: confirmed
    (a change question answered with confirm=True: a changepoint row confirmed=1; an earlier-setup question
    answered "continue there": an epoch_links row; a knowledge candidate the owner accepted), rejected (confirm=False:
    a changepoint row confirmed=0; the earlier setup declined; a candidate rejected), answered (the owner answered
    without confirming or rejecting), withdrawn ("resolved by evidence": the executive no longer asks it), expired
    (no answer within 7 days) or open. precision = confirmed / (confirmed + rejected): for change_alarm, how often
    the change detector's alarm was a change the owner confirmed. An earlier-setup question the owner closed without
    a link counts as rejected (confirm=False and a bare close leave the same record). Questions about forgotten roots are counted with
    their scrubbed scope; a forgotten epoch's questions are gone with it."""
    cps = {}
    if beliefs._has_changepoints(db):
        for pid, confirmed in db.execute("SELECT pending_id, confirmed FROM changepoints WHERE pending_id IS NOT NULL"):
            cps[pid] = bool(confirmed)
    links = set()
    if beliefs._has_table(db, "epoch_links"):
        links = {pid for (pid,) in db.execute("SELECT pending_id FROM epoch_links WHERE pending_id IS NOT NULL")}
    web_status = {}
    if beliefs._has_table(db, "web_knowledge"):
        web_status = dict(db.execute("SELECT 'web:knowledge:' || id, status FROM web_knowledge"))
    by_rule, by_epoch = {}, {}
    for pid, epoch, kind, scope, question, resolved_at, resolution in db.execute(
            "SELECT id, epoch, kind, scope, question, resolved_at, resolution FROM pending ORDER BY id"):
        rule = _rule_of(kind, scope, question)
        if rule is None:
            continue
        resolution = resolution or ""
        if resolved_at is None:
            outcome = "open"
        elif resolution.startswith("expired:"):
            outcome = "expired"
        elif resolution.startswith("resolved by evidence:"):
            outcome = "withdrawn"
        elif pid in cps:
            outcome = "confirmed" if cps[pid] else "rejected"
        elif rule == "epoch_match":
            outcome = "confirmed" if pid in links else "rejected"
        elif rule == "web_knowledge" and web_status.get(scope) in ("accepted", "rejected"):
            outcome = "confirmed" if web_status[scope] == "accepted" else "rejected"
        else:
            outcome = "answered"
        counts = by_rule.setdefault(rule, dict.fromkeys(_OUTCOMES, 0))
        counts[outcome] += 1
        if epoch is not None:
            per = by_epoch.setdefault(epoch, {}).setdefault(rule, dict.fromkeys(_OUTCOMES, 0))
            per[outcome] += 1
    return {"display_only": True, "rules": PRECISION_RULES,
            "by_rule": {rule: _rates(c) for rule, c in sorted(by_rule.items())},
            "by_epoch": {epoch: {rule: _rates(c) for rule, c in sorted(rules.items())}
                         for epoch, rules in sorted(by_epoch.items())}}


def _changepoint(db, pending_id):
    if not beliefs._has_changepoints(db):
        return None
    row = db.execute("SELECT id,epoch,scope,since_seq,created_at,confirmed FROM changepoints WHERE pending_id=? "
                     "ORDER BY id LIMIT 1", (pending_id,)).fetchone()
    if row is None:
        return None
    return dict(zip(("id", "epoch", "scope", "since_seq", "created_at"), row[:5]), confirmed=bool(row[5]))


def _link(db, pending_id):
    if not beliefs._has_table(db, "epoch_links"):
        return None
    row = db.execute("SELECT epoch,source_epoch,segments,pending_id,created_at FROM epoch_links WHERE pending_id=?",
                     (pending_id,)).fetchone()
    if row is None:
        return None
    return {"epoch": row[0], "source_epoch": row[1], "segments": json.loads(row[2]), "pending_id": row[3],
            "created_at": row[4]}


def _record_link(db, current, confirm, now):
    """confirm=True on an 'epoch:<E>' question: link the epoch to E's matching segments. Caller owns the txn.

    The segments are those of E's entry in match_epochs at answer time. confirm=False records nothing beyond
    the answered question itself, which keeps rule 4b from asking about E again in this epoch.
    """
    epoch, source = current["epoch"], current["scope"].split(":", 1)[1]
    if not confirm:
        return None
    beliefs.require_epoch(db, source)
    existing = beliefs.link(db, epoch)
    if existing is not None:
        raise Conflict(f"Epoch {epoch} already continues epoch {existing['source_epoch']}")
    match = beliefs.match_epochs(db, epoch, last=beliefs.MATCH_EVENTS)
    entry = next((r for r in match["ranking"] if r["epoch"] == source), None)
    if entry is None:
        raise ValidationError(f"Epoch {source} has no learning evidence to continue from")
    db.execute("INSERT INTO epoch_links(epoch,source_epoch,segments,pending_id,created_at) VALUES(?,?,?,?,?)",
               (epoch, source, json.dumps(entry["segments"], sort_keys=True, separators=(",", ":")),
                current["id"], now))
    return {"epoch": epoch, "source_epoch": source, "segments": entry["segments"], "pending_id": current["id"],
            "created_at": now}


def _record_answer(db, current, confirm, now):
    """Insert the changepoint row for a confirm=True/False answer to a change question. Caller owns the txn."""
    epoch, scope, kind = current["epoch"], current["scope"], current["kind"]
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
    keeps pooling and is not reported again until 3 of its events arrive. On an 'epoch:<E>' question,
    confirm=True ("continue there") links the epoch to E's matching segments (see _record_link) and
    confirm=False ("this is new") declines E; either answer is counted for beliefs.answer_prior (the empirical
    P(new setup) of match_epochs). confirm=None only closes the question. Returns the pending row
    plus "changepoint" and "link" (the recorded rows or None).
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
        about_epoch = current["scope"].startswith("epoch:")
        about_web = current["scope"].startswith("web:")
        if about_web:
            from ..webcortex import knowledge as web_knowledge  # lazy: the executive does not need the web
        if current["resolved_at"] is not None:
            recorded = _changepoint(db, pending_id)
            linked = _link(db, pending_id)
            if about_web:
                previous = web_knowledge.review_outcome(db, current)
            else:
                previous = (linked is not None) if about_epoch else (recorded["confirmed"] if recorded else None)
            if current["resolution"] == resolution and (confirm is None or confirm == previous):
                return {**current, "changepoint": recorded, "link": linked}
            raise Conflict(f"Pending question {pending_id} was already resolved: {current['resolution']!r}")
        changepoint = linked = web = None
        if confirm is None and about_web:     # an informational web question: "412 km holds" decides a dispute
            web = web_knowledge.answer_contested(db, current, resolution, now)
        if confirm is not None:
            if about_web:
                web = web_knowledge.apply_review(db, current, confirm, now)
            elif about_epoch:
                linked = _record_link(db, current, confirm, now)
                store.bump(db, beliefs.ANSWER_COUNTERS[confirm])   # the empirical prior of beliefs.match_epochs
            else:
                changepoint = _record_answer(db, current, confirm, now)
        db.execute("UPDATE pending SET resolved_at=?, resolution=? WHERE id=?", (now, resolution, pending_id))
        store.bump(db, "teacher_interventions")
        store.bump_state_version(db)
        if changepoint is not None or linked is not None:
            beliefs.record_revision(db, current["epoch"], now, f"resolve:{pending_id}")
        out = {**dict(zip(_PENDING_COLUMNS, db.execute(select, (pending_id,)).fetchone())),
               "changepoint": changepoint, "link": linked}
        if about_web:
            out["web"] = web
        return out
