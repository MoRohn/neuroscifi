"""The bounded reflection routine (lane K, ADR-055; directive 6.6, 7.7).

report(db, since, until) is a deterministic reading of the stored records over a period. Every line names the record
ids it was read from ("refs"); there is no model text, no self-assessment and no quoted statement in it:
  learned      knowledge items verified in the period (id, version, task, independent groups, sources) and items a newer
               version superseded
  failed       predictions that failed; answers whose guard refused sentences (counts); investigations that changed
               nothing (autonomous tasks whose evidence left every item as it was)
  assumptions  hypotheses whose status changed (supported / refuted / revised / abandoned) and re-check marks opened
               because a premise changed or a prediction it rested on failed
  missing      open web questions to the owner; predictions past their check time or deadline and still unsettled
  strategies   per research mode: finished tasks and their external outcomes (strategies.outcomes) and the mode's own
               recorded statistics; a mode with at least MIN_MODE_TASKS tasks whose rate of external outcomes is more
               than UNDERPERFORM below the period's overall rate is listed as underperforming
reflect() stores the report (web_reflections) and takes at most MAX_ACTIONS bounded actions, each recorded with the
line it came from:
  re-check     a knowledge item whose prediction failed and that is still live, not already marked (premises.mark,
               reason 'reflection': it queues a re-verification and changes nothing)
  abandon      a derived hypothesis the evidence rule abandons (evolution.abandon_if_unsupported; only while the owner's
               setting hypothesis_abandon is on)
  question     one question to the owner about an underperforming mode, within the web question budget
Nothing it does is a gate: no report line decides anything by itself. An optional summary by a LOCAL model
(reflection_model_summary, or --phrase) is display-only: it is stored as an 'inference' in the imagined store
(instance/imagined.py, never evidence) and the report's lines stay the record.

Scheduled: scheduler.tick runs scheduled() when the owner's setting `reflection` is on, at most once per
reflection_interval_days, held by quiet (background activity 'tick.reflection'). `nsf knowledge reflect` runs it when
the owner asks. Forget: purge() drops every line, and every action, that names a record no longer held (called in each
forget's transaction and after a restore's replay).
"""
from __future__ import annotations

from ..instance import store
from ..instance.errors import ValidationError
from . import state

MAX_LINES = 20            # lines per section
MAX_ACTIONS = 5           # actions per reflection
MIN_MODE_TASKS = 3        # finished tasks of a mode before it can be called underperforming
UNDERPERFORM = 0.2        # its rate of external outcomes this far below the period's overall rate
PAST_DUE_GRACE = 86400.0  # a knowledge prediction this long past its check time counts as unsettled past due
DAY = 86400.0
MECHANISM = "reflection"
MECHANISM_VERSION = "1"
SECTIONS = ("learned", "failed", "assumptions", "missing", "strategies")


def present(db) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='web_reflections'").fetchone() \
        is not None


def _line(kind, text, refs, **detail):
    return {"kind": kind, "text": text, "refs": [[k, str(i)] for k, i in refs]} | detail


def report(db, since, until) -> dict:
    """The deterministic report over [since, until] (see the module docstring). Same records, same report."""
    from . import strategies
    since, until = float(since), float(until)
    if until < since:
        raise ValidationError("the period must end after it starts")
    period = (since, until)
    learned = []
    for kid, version, task, verification, sources, superseded in db.execute(
            "SELECT id, version, task_id, verification, sources, superseded_by FROM web_knowledge WHERE "
            "provenance='verified_conclusion' AND created_at BETWEEN ? AND ? ORDER BY id LIMIT ?",
            (*period, MAX_LINES)):
        v = state.loads(verification, {})
        groups = (v.get("independent_groups") or {}).get("supporting")
        learned.append(_line("verified", f"knowledge #{kid} v{version} verified ({v.get('state') or 'n/a'}, "
                                         f"{groups if groups is not None else '?'} independent group(s), "
                                         f"{len(state.loads(sources, []))} source(s))",
                             [("knowledge", kid)] + ([("task", task)] if task else [])))
    for old, new, at in db.execute("SELECT id, superseded_by, valid_until FROM web_knowledge WHERE superseded_by IS "
                                   "NOT NULL AND updated_at BETWEEN ? AND ? ORDER BY id LIMIT ?",
                                   (*period, MAX_LINES)):
        learned.append(_line("superseded", f"knowledge #{old} superseded by #{new}",
                             [("knowledge", old), ("knowledge", new)]))
    failed = []
    for pid, kid, hid in db.execute("SELECT id, knowledge_id, hypothesis_id FROM web_predictions WHERE outcome='error' "
                                    "AND checked_at BETWEEN ? AND ? ORDER BY id LIMIT ?", (*period, MAX_LINES)):
        about = ("knowledge", kid) if kid is not None else ("hypothesis", hid)
        failed.append(_line("prediction_failed", f"prediction #{pid} about {about[0]} #{about[1]} failed",
                            [("prediction", pid), about]))
    for tid, n in db.execute(
            "SELECT id, json_array_length(json_extract(result,'$.reasoning.rejected')) FROM web_tasks WHERE "
            "json_valid(result) AND json_array_length(json_extract(result,'$.reasoning.rejected'))>0 AND "
            "coalesce(finished_at, updated_at) BETWEEN ? AND ? ORDER BY rowid LIMIT ?", (*period, MAX_LINES)):
        failed.append(_line("sentences_refused", f"task {tid}: the guard refused {n} sentence(s) of the answer",
                            [("task", tid)], count=n))
    for tid, mode in db.execute(
            "SELECT t.id, t.mode FROM web_tasks t WHERE (t.origin='curiosity' OR t.origin LIKE 'refresh:%') AND "
            "t.status IN ('completed','partial') AND coalesce(t.finished_at, t.updated_at) BETWEEN ? AND ? AND NOT "
            "EXISTS (SELECT 1 FROM web_feedback f WHERE f.task_id=t.id AND f.effect<>'unchanged') AND NOT EXISTS "
            "(SELECT 1 FROM web_predictions p WHERE p.task_id=t.id AND p.outcome IN ('confirmed','error')) "
            "ORDER BY t.rowid LIMIT ?", (*period, MAX_LINES)):
        failed.append(_line("changed_nothing", f"autonomous task {tid} ({mode}) changed no knowledge and settled no "
                                               "prediction", [("task", tid)]))
    assumptions = []
    for hid, status in db.execute("SELECT id, status FROM web_hypotheses WHERE resolved_at BETWEEN ? AND ? ORDER BY "
                                  "id LIMIT ?", (*period, MAX_LINES)):
        assumptions.append(_line("hypothesis_" + status, f"hypothesis #{hid} {status}", [("hypothesis", hid)]))
    from . import premises
    if premises.present(db):
        for mid, kind, sid, reason in db.execute(
                "SELECT id, subject_kind, subject_id, reason FROM web_rechecks WHERE created_at BETWEEN ? AND ? AND "
                "reason IN ('premise_superseded','premise_contradicted','premise_forgotten','prediction_failed') "
                "ORDER BY id LIMIT ?", (*period, MAX_LINES)):
            assumptions.append(_line("recheck_marked", f"{kind} #{sid} marked to re-check ({reason.replace('_', ' ')})",
                                     [(kind, sid)], recheck_id=mid))
    missing = []
    open_questions = [pid for (pid,) in db.execute(
        "SELECT id FROM pending WHERE scope LIKE 'web:%' AND resolved_at IS NULL AND created_at<=? ORDER BY id",
        (until,))]
    if open_questions:
        missing.append(_line("open_questions", f"{len(open_questions)} question(s) to the owner are open",
                             [("pending", p) for p in open_questions[:MAX_LINES]], count=len(open_questions)))
    for pid, kid, hid in db.execute(
            "SELECT id, knowledge_id, hypothesis_id FROM web_predictions WHERE outcome IS NULL AND ((knowledge_id IS "
            "NOT NULL AND check_after<?) OR (hypothesis_id IS NOT NULL AND deadline IS NOT NULL AND deadline<?)) "
            "ORDER BY id LIMIT ?", (until - PAST_DUE_GRACE, until, MAX_LINES)):
        about = ("knowledge", kid) if kid is not None else ("hypothesis", hid)
        missing.append(_line("unsettled_past_due", f"prediction #{pid} about {about[0]} #{about[1]} is past due "
                                                   "and unsettled", [("prediction", pid), about]))
    modes = []
    rows = db.execute(f"SELECT t.mode, count(*), sum({strategies._REWARD}) FROM web_tasks t WHERE t.status IN "
                      "('completed','partial') AND coalesce(t.finished_at, t.updated_at) BETWEEN ? AND ? GROUP BY "
                      "t.mode ORDER BY t.mode", period).fetchall()
    total = sum(n for _, n, _ in rows)
    overall = (sum(r or 0 for _, _, r in rows) / total) if total else None
    for mode, n, rewarded in rows:
        rate = (rewarded or 0) / n
        under = overall is not None and n >= MIN_MODE_TASKS and rate < overall - UNDERPERFORM
        own = state.load_strategies(db, f"mode:{mode}")
        modes.append(_line("mode_underperformed" if under else "mode", f"{mode}: {rewarded or 0} of {n} finished task(s) "
                           f"gained verified knowledge or settled a prediction (overall {overall:.2f})", [],
                           mode=mode, tasks=n, rewarded=rewarded or 0, rate=round(rate, 6),
                           underperformed=bool(under), self_reported_runs=own.get("runs")))
    return {"period": {"from": since, "to": until}, "learned": learned, "failed": failed,
            "assumptions": assumptions, "missing": missing, "strategies": modes,
            "label": "records only: every line names the record ids it was read from; no model text"}


def _actions(db, rep, now) -> list[dict]:
    from . import evolution, knowledge, premises
    actions = []
    cfg = state.get_config(db)
    for line in rep["failed"]:
        if len(actions) >= MAX_ACTIONS:
            break
        about = [r for r in line["refs"] if r[0] == "knowledge"]
        if line["kind"] == "prediction_failed" and about and premises.enabled(db):
            kid = int(about[0][1])
            mid = premises.mark(db, "knowledge", kid, "reflection", {"kind": "knowledge", "id": str(kid)}, now)
            if mid:
                actions.append({"action": "recheck", "recheck_id": mid, "refs": line["refs"]})
    if getattr(cfg, "hypothesis_abandon", False):
        for (hid,) in db.execute("SELECT h.id FROM web_hypotheses h WHERE h.status='open' AND h.origin<>'owner' AND "
                                 "NOT EXISTS (SELECT 1 FROM web_predictions p WHERE p.hypothesis_id=h.id AND "
                                 "p.outcome IS NULL) ORDER BY h.id").fetchall():
            if len(actions) >= MAX_ACTIONS:
                break
            if evolution.abandon_if_unsupported(db, hid, now):
                actions.append({"action": "abandon", "hypothesis_id": hid, "refs": [["hypothesis", str(hid)]]})
    under = [line for line in rep["strategies"] if line.get("underperformed")]
    if under and len(actions) < MAX_ACTIONS:
        worst = min(under, key=lambda line: (line["rate"], line["mode"]))
        pid = knowledge.ask(db, f"web:reflection:{int(now)}",
                            f"Reflection: {worst['mode']} research gained verified knowledge or settled a prediction in "
                            f"{worst['rewarded']} of {worst['tasks']} task(s) in this period, below the rest. Nothing "
                            "changes unless you decide; `nsf knowledge strategy` shows the numbers. Answer to dismiss.",
                            now)
        if pid is not None:
            actions.append({"action": "question", "pending_id": pid, "mode": worst["mode"], "refs": [["pending",
                                                                                                    str(pid)]]})
    return actions


def reflect(db, now, *, trigger="owner", since=None, phrase=False, gateway=None) -> dict:
    """Build the report over [since, now] (default: since the previous reflection, else the last
    reflection_interval_days), take its bounded actions and store both. Returns the stored reflection."""
    if trigger not in ("owner", "schedule"):
        raise ValidationError("trigger must be owner or schedule")
    cfg = state.get_config(db)
    if since is None:
        row = db.execute("SELECT max(period_to) FROM web_reflections").fetchone()
        since = row[0] if row and row[0] is not None else now - float(cfg.reflection_interval_days) * DAY
    state.require_writable(db)
    with store.write(db):
        rep = report(db, since, now)
        actions = _actions(db, rep, now)
        rid = db.execute("INSERT INTO web_reflections(period_from,period_to,report,actions,trigger,created_at) "
                         "VALUES(?,?,?,?,?,?)", (float(since), float(now), state.dumps(rep), state.dumps(actions),
                                                 trigger, now)).lastrowid
        state.log_event(db, None, "decision", {"reflection": rid, "actions": len(actions)}, now)
        store.bump_state_version(db)
    out = show(db, rid)
    if phrase or getattr(cfg, "reflection_model_summary", False):
        out["summary"] = _summary(db, out, now, gateway)
    return out


def _summary(db, reflection, now, gateway=None) -> dict:
    """A local model's phrasing of the report, display-only, stored as an 'inference' in the imagined store; the
    report's lines stay the record. None of it is evidence."""
    from ..instance import imagined
    # The summary phrases the report's lines, so it derives from every record they name: a forget of any removes it
    # (imagined.purge_orphans; review1 CL1). Beyond what one record may name it is not made (never truncated).
    refs = list(dict.fromkeys((k, str(i)) for s in SECTIONS for line in reflection["report"][s]
                              for k, i in line.get("refs", []) if k in imagined.REF_KINDS))
    if len(refs) > imagined.MAX_REFS:
        return {"generated_by": None, "note": f"the report names {len(refs)} records; a stored summary may derive "
                                              f"from at most {imagined.MAX_REFS}, so none is made"}
    try:
        if gateway is None:
            from ..inference.gateway import Gateway
            gateway = Gateway.from_db(db)
        if gateway.guard() == "remote":
            return {"generated_by": None, "note": "the configured model endpoint is remote; memory is not sent to it"}
        lines = [{"section": s, "text": line["text"]} for s in SECTIONS for line in reflection["report"][s]]
        reply = gateway.chat([
            {"role": "system", "content": "Summarise these reflection lines in at most four plain sentences. Use only "
                                          "these lines; add nothing; do not judge."},
            {"role": "user", "content": state.dumps(lines)[:8000]}], purpose="knowledge_reflection", max_tokens=300)
        rid = imagined.record(db, "inference", payload={"text": reply.text.strip()[:4000],
                                                        "reflection_id": reflection["id"]},
                              assumptions=["the model read only the report lines"],
                              uncertainty="a model's phrasing; the report lines are the record",
                              mechanism=MECHANISM, mechanism_version=MECHANISM_VERSION,
                              question=f"summary of reflection #{reflection['id']}", now=now, model=reply.model,
                              derived_from=refs)
        return {"text": reply.text.strip(), "generated_by": f"model:{reply.model}", "imagined_id": rid,
                "label": "display only: a model's phrasing, stored as an inference, never evidence"}
    except Exception as exc:          # degraded: the report stands
        return {"generated_by": None, "note": f"model summary unavailable: {type(exc).__name__}"}


def scheduled(db, now) -> dict | None:
    """The timer's reflection: None when off or not due; {"skipped": reason} while quiet holds it; else the stored
    reflection. Asks background.allowed('tick.reflection')."""
    from .. import background
    cfg = state.get_config(db)
    if not getattr(cfg, "reflection", False) or not present(db):
        return None
    ok, why = background.allowed(db, "tick.reflection")
    if not ok:
        return {"skipped": why}
    last = db.execute("SELECT max(created_at) FROM web_reflections WHERE trigger='schedule'").fetchone()[0]
    if last is not None and now - last < float(cfg.reflection_interval_days) * DAY:
        return None
    from ..instance.errors import InstanceError
    try:
        return reflect(db, now, trigger="schedule")
    except InstanceError as exc:         # writes paused, a busy file: the tick goes on (review1 CL2)
        return {"skipped": f"reflection not written: {exc}"[:300]}


def show(db, reflection_id) -> dict:
    from ..instance.errors import NotFound
    row = db.execute("SELECT id, period_from, period_to, report, actions, trigger, created_at FROM web_reflections "
                     "WHERE id=?", (reflection_id,)).fetchone()
    if row is None:
        raise NotFound(f"No reflection {reflection_id}")
    return {"id": row[0], "period": {"from": row[1], "to": row[2]}, "report": state.loads(row[3], {}),
            "actions": state.loads(row[4], []), "trigger": row[5], "created_at": row[6]}


def listing(db, *, limit=20) -> list[dict]:
    if not present(db):
        return []
    return [{"id": i, "period": {"from": a, "to": b}, "trigger": t, "created_at": c,
             "lines": sum(len(v) for k, v in state.loads(r, {}).items() if k in SECTIONS),
             "actions": len(state.loads(x, []))}
            for i, a, b, r, x, t, c in db.execute("SELECT id, period_from, period_to, report, actions, trigger, "
                                                  "created_at FROM web_reflections ORDER BY id DESC LIMIT ?",
                                                  (int(limit),))]


_REF_TABLES = {"knowledge": "web_knowledge", "hypothesis": "web_hypotheses", "prediction": "web_predictions",
               "task": "web_tasks", "pending": "pending"}


def _held(db, kind, ref_id) -> bool:
    table = _REF_TABLES.get(kind)
    if table is None:
        return True
    if table == "pending":
        return db.execute("SELECT 1 FROM pending WHERE id=? AND question<>'#forgotten'", (int(ref_id),)).fetchone() \
            is not None
    value = ref_id if kind == "task" else int(ref_id)
    return db.execute(f"SELECT 1 FROM {table} WHERE id=?", (value,)).fetchone() is not None


def purge(db) -> int:
    """Drop every report line and action naming a record no longer held. Caller owns the txn. Returns lines and
    actions removed."""
    if not present(db):
        return 0
    removed = 0
    for rid, rep, actions in db.execute("SELECT id, report, actions FROM web_reflections").fetchall():
        value, acts = state.loads(rep, {}), state.loads(actions, [])
        changed = False
        for section in SECTIONS:
            kept = [line for line in value.get(section) or [] if all(_held(db, k, i) for k, i in line.get("refs", []))]
            if len(kept) != len(value.get(section) or []):
                removed += len(value[section]) - len(kept)
                value[section], changed = kept, True
        kept_acts = [a for a in acts if all(_held(db, k, i) for k, i in a.get("refs", []))]
        if len(kept_acts) != len(acts):
            removed += len(acts) - len(kept_acts)
            changed = True
        if changed:
            db.execute("UPDATE web_reflections SET report=?, actions=? WHERE id=?",
                       (state.dumps(value), state.dumps(kept_acts), rid))
    return removed


# ---- CLI: nsf knowledge rechecks | reflect | reflections | strategy | ledger | compete (lane K) -----------------------

def _cli_rechecks(db, a, now):
    from . import premises, recheck
    out = premises.status(db) | {"list": premises.listing(db, status=a.status, limit=a.limit)}
    if a.rank:
        out["rank"] = recheck.rank(db, now, limit=a.limit)
    return out


def _cli_reflect(db, a, now):
    return reflect(db, now, trigger="owner", since=(now - a.since_days * DAY) if a.since_days else None,
                   phrase=a.phrase)


def _cli_reflections(db, a, now):
    return show(db, a.id) if a.id is not None else {"reflections": listing(db, limit=a.limit)}


def _cli_strategy(db, a, now):
    import json
    from . import strategies
    if a.promote:
        with open(a.promote, encoding="utf-8") as fh:
            evidence = json.load(fh)
        if not isinstance(evidence, dict):
            raise ValidationError("the evidence file must hold a JSON object")
        return strategies.promote(db, evidence, now)
    if a.revert:
        return strategies.revert(db, now, reason=a.reason)
    return strategies.status(db, now)


def _cli_ledger(db, a, now):
    from . import strategies
    return {"ledger": strategies.ledger(db, capability=a.capability, limit=a.limit),
            "out_of_scope": strategies.OUT_OF_SCOPE}


def _cli_compete(db, a, now):
    from . import evolution
    return evolution.compete(db, a.key)


def register_cli(cmd):
    """Add lane K's commands to `nsf knowledge` (evolution.register passes its command helper)."""
    from ..instance.cli import LIMIT, days
    from .strategies import REVERT_REASONS
    p = cmd("rechecks", _cli_rechecks, "re-check marks: what rests on a changed premise or a failed prediction")
    p.add_argument("--status", choices=("marked", "queued", "done", "dropped"))
    p.add_argument("--limit", type=LIMIT, default=50)
    p.add_argument("--rank", action="store_true", help="also the idle re-check's ranking (importance x staleness x "
                                                       "dependents)")
    p = cmd("reflect", _cli_reflect, "a bounded, traceable reflection over the records of a period")
    p.add_argument("--since-days", type=days)
    p.add_argument("--phrase", action="store_true", help="let a configured local model summarise it (display only)")
    p = cmd("reflections", _cli_reflections, "stored reflections")
    p.add_argument("--id", type=int)
    p.add_argument("--limit", type=LIMIT, default=20)
    p = cmd("strategy", _cli_strategy, "strategy selection from external outcomes: status, gate, revert")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--promote", metavar="EVIDENCE_JSON", help="run the promotion gate over benchmark evidence")
    group.add_argument("--revert", action="store_true")
    p.add_argument("--reason", choices=REVERT_REASONS,
                   help="why the owner reverts it, as a code (the ledger is permanent and keeps no free text)")
    p = cmd("ledger", _cli_ledger, "the improvement ledger: adopted and reverted strategy and rule changes")
    p.add_argument("--capability")
    p.add_argument("--limit", type=LIMIT, default=50)
    p = cmd("compete", _cli_compete, "the open hypotheses of one claim key, compared with dampened likelihoods")
    p.add_argument("key")
