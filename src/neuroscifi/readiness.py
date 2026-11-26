"""Readiness of real outcomes: how far each learner that waits for settled real outcomes is from its own minimum, from
the individual's own records (Project Cognitive Genesis, Wave 3 lane H; ADR-058). `nsf readiness`.

Why. Calibration (ADR-054), scenario records (ADR-052), forecast recalibration (ADR-048) and strategy selection
(ADR-055) all learn from outcomes the world settles, and Wave 2 found every one of them starved ("no fitted map has
30 items in 3 blocks"). This report counts what the individual has so far, and when, at the rate observed, each
minimum could be met. It reads only: no fitting, no model call, nothing stored, no text of any record shown.

  predictions   settled web_predictions by kind (evolution._KIND_SQL: knowledge, hypothesis, owner), month of
                settlement and outcome; open ones; settlements a forgotten task took the observation of are counted
                apart (evolution._counted), as recalibration leaves them out
  answers       finished research (completed or partial tasks) and those with a later verdict: a prediction on
                knowledge that task produced, settled confirmed or error. No record pairs an answer with a verdict on
                the answer itself; this is the nearest real outcome, and the candidate items a calibration map of
                real answers would be fitted on
  hypotheses    by status; settled = supported, refuted or revised (abandoned is apart: every prediction was
                inconclusive)
  rechecks      re-verification marks (web_rechecks) that found a change (superseded, contradicted, a prediction in
                error) against none (confirmed), undecided (no evidence, inconclusive), closed by another path,
                dropped, or still open
  learners      each learner with its minimum read from its own module's constants (never restated here), what the
                individual has, what is missing, the rate over the last RATE_DAYS days and the earliest date the
                minimum could be met at that rate ("unknown" when the rate is zero); "no minimum in the code" where
                the code has none

Cost: SQL counts over indexed or bounded tables, one pass each; measured at 10,000 knowledge items and 500,000
events by scripts/bench_shadow.py readiness-cost (docs/quality/evidence/genesis_h.json)."""
from __future__ import annotations

from datetime import datetime, timezone
import time

SCHEMA = "nsf.readiness/1"
RATE_DAYS = 90                # the observed rate is that of the last this many days (or since the first, if later)
DAY = 86400.0
SETTLED_HYPOTHESES = ("supported", "refuted", "revised")       # evolution.metrics' "resolved"
CHANGE = ("superseded by new evidence", "contradicted by new evidence", "prediction error")    # premises.close_for_task
NO_CHANGE = ("confirmed by new evidence", "prediction confirmed")
UNDECIDED = ("no evidence about it", "prediction inconclusive", "no decisive evidence",
             "its knowledge premise was re-verified")


def _has(db, table) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _month(at) -> str:
    if at is None:
        return "unknown"
    try:
        return datetime.fromtimestamp(float(at), timezone.utc).strftime("%Y-%m")
    except (OverflowError, OSError, ValueError):
        return "unknown"


def _date(at) -> str:
    return datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%d")


def _eta(have, minimum, recent, first_at, now) -> dict:
    """{"minimum", "have", "missing", "rate_per_day", "rate_window_days", "earliest"}: the date the minimum could be
    met if outcomes keep settling at the rate of the last RATE_DAYS days (since the first, when that is later)."""
    missing = max(0, minimum - have)
    window = RATE_DAYS
    if first_at is not None:
        window = max(1.0, min(RATE_DAYS, (now - first_at) / DAY))
    rate = recent / window if recent else 0.0
    if not missing:
        earliest = "met"
    elif rate <= 0:
        earliest = "unknown"
    else:
        try:
            earliest = _date(now + missing / rate * DAY)
        except (OverflowError, OSError, ValueError):
            earliest = "unknown"
    return {"minimum": minimum, "have": have, "missing": missing, "rate_per_day": round(rate, 3),
            "rate_window_days": round(window, 1), "earliest": earliest}


def _predictions(db, now) -> tuple[dict, dict]:
    """(the predictions section, {kind: (decisive with a forecast, of those in the rate window, first at)})."""
    from .webcortex import evolution
    since = now - RATE_DAYS * DAY
    by_kind: dict = {}
    for kind, month, outcome, n in db.execute(
            f"SELECT {evolution._KIND_SQL}, CASE WHEN typeof(p.checked_at) IN ('integer','real') AND p.checked_at "
            "BETWEEN 0 AND 253402300799 THEN strftime('%Y-%m', p.checked_at, 'unixepoch') ELSE 'unknown' END, "
            "p.outcome, count(*) FROM web_predictions p LEFT JOIN web_hypotheses h ON h.id=p.hypothesis_id "
            f"WHERE p.outcome IS NOT NULL AND {evolution._counted('p.')} GROUP BY 1, 2, 3"):
        entry = by_kind.setdefault(kind, {"settled": 0, "confirmed": 0, "error": 0, "inconclusive": 0,
                                          "by_month": {}})
        entry["settled"] += n
        entry[outcome] += n
        entry["by_month"].setdefault(month, {"confirmed": 0, "error": 0, "inconclusive": 0})[outcome] += n
    used = {}
    for kind, have, recent, first in db.execute(
            f"SELECT {evolution._KIND_SQL}, count(*), coalesce(sum(p.checked_at>=?), 0), min(p.checked_at) "
            "FROM web_predictions p LEFT JOIN web_hypotheses h ON h.id=p.hypothesis_id WHERE p.outcome IN "
            f"('confirmed','error') AND p.probability IS NOT NULL AND {evolution._counted('p.')} GROUP BY 1",
            (since,)):
        used[kind] = (have, recent, first)
    open_ = db.execute("SELECT count(*) FROM web_predictions WHERE outcome IS NULL").fetchone()[0]
    uncounted = db.execute("SELECT count(*) FROM web_predictions p WHERE outcome IS NOT NULL AND NOT "
                           f"({evolution._counted('p.')})").fetchone()[0]
    for kind in evolution.RECALIBRATION_KINDS:
        by_kind.setdefault(kind, {"settled": 0, "confirmed": 0, "error": 0, "inconclusive": 0, "by_month": {}})
        by_kind[kind]["by_month"] = dict(sorted(by_kind[kind]["by_month"].items()))
    return {"by_kind": by_kind, "open": open_, "settled_but_forgotten": uncounted}, used


def _answers(db, now) -> tuple[dict, tuple]:
    from .webcortex import evolution
    finished = db.execute("SELECT count(*) FROM web_tasks WHERE status IN ('completed','partial')").fetchone()[0]
    first_verdict = db.execute(
        "SELECT k.task_id, min(p.checked_at) FROM web_predictions p JOIN web_knowledge k ON k.id=p.knowledge_id "
        "JOIN web_tasks t ON t.id=k.task_id AND t.status IN ('completed','partial') "
        f"WHERE p.outcome IN ('confirmed','error') AND {evolution._counted('p.')} GROUP BY k.task_id").fetchall()
    by_month: dict = {}
    for _, at in first_verdict:
        by_month[_month(at)] = by_month.get(_month(at), 0) + 1
    times = [at for _, at in first_verdict if isinstance(at, (int, float))]
    recent = sum(1 for at in times if at >= now - RATE_DAYS * DAY)
    out = {"finished": finished, "with_a_later_verdict": len(first_verdict),
           "by_month_of_first_verdict": dict(sorted(by_month.items())),
           "verdict": "a prediction on knowledge the task produced, settled confirmed or error"}
    return out, (len(first_verdict), recent, min(times) if times else None)


def _hypotheses(db, now) -> tuple[dict, tuple]:
    rows = db.execute("SELECT status, count(*), coalesce(sum(resolved_at>=?), 0), min(resolved_at) FROM "
                      "web_hypotheses GROUP BY status", (now - RATE_DAYS * DAY,)).fetchall()
    status = {s: n for s, n, _, _ in rows}
    settled = sum(n for s, n, _, _ in rows if s in SETTLED_HYPOTHESES)
    recent = sum(r for s, _, r, _ in rows if s in SETTLED_HYPOTHESES)
    firsts = [f for s, _, _, f in rows if s in SETTLED_HYPOTHESES and isinstance(f, (int, float))]
    return ({"by_status": status, "settled": settled, "settled_means": list(SETTLED_HYPOTHESES)},
            (settled, recent, min(firsts) if firsts else None))


def _rechecks(db) -> dict:
    out = {"found_a_change": 0, "found_no_change": 0, "undecided": 0, "closed_by_another_path": 0, "dropped": 0,
           "open": 0}
    for status, outcome, n in db.execute("SELECT status, outcome, count(*) FROM web_rechecks GROUP BY 1, 2"):
        if status in ("marked", "queued"):
            out["open"] += n
        elif status == "dropped":
            out["dropped"] += n
        elif outcome in CHANGE:
            out["found_a_change"] += n
        elif outcome in NO_CHANGE:
            out["found_no_change"] += n
        elif isinstance(outcome, str) and outcome.startswith("closed:"):
            out["closed_by_another_path"] += n
        else:
            out["undecided"] += n
    return out


def _scenarios(db) -> dict:
    from .webcortex import scenarios
    row = db.execute("SELECT count(*), coalesce(sum(outcome IS NOT NULL), 0) FROM imagined WHERE mechanism=?",
                     (scenarios.MECHANISM,)).fetchone()
    return {"records": row[0], "settled": row[1]}


def _strategy_outcomes(db) -> dict:
    """{mode: {"tasks", "rewarded"}} as strategies.outcomes counts them (its _REWARD over the last WINDOW finished
    tasks of each mode), with the rewarded task ids found in one pass over each table instead of two unindexed
    look-ups per task (strategies.outcomes took 6 s at 10,000 knowledge items and 20,000 predictions;
    tests/test_readiness.py checks both give the same counts)."""
    from .webcortex import strategies
    from .webcortex.types import MODES
    rewarded = {r for (r,) in db.execute(
        "SELECT task_id FROM web_knowledge WHERE provenance='verified_conclusion' AND "
        "json_extract(verification,'$.state')='supported' AND task_id IS NOT NULL UNION SELECT task_id FROM "
        "web_predictions WHERE outcome IN ('confirmed','error') AND task_id IS NOT NULL")}
    out = {}
    for mode in MODES:
        ids = [i for (i,) in db.execute(
            "SELECT id FROM web_tasks WHERE mode=? AND status IN ('completed','partial') ORDER BY "
            "coalesce(finished_at, updated_at) DESC, rowid DESC LIMIT ?", (mode, int(strategies.WINDOW)))]
        if ids:
            out[mode] = {"tasks": len(ids), "rewarded": sum(1 for i in ids if i in rewarded)}
    return out


def report(db, now=None) -> dict:
    """The readiness report (see the module doc). Read only."""
    from .webcortex import evolution, monitoring, strategies
    now = time.time() if now is None else now
    web = _has(db, "web_predictions") and _has(db, "web_tasks") and _has(db, "web_hypotheses")
    out = {"schema": SCHEMA, "at": now, "rate_window_days": RATE_DAYS,
           "note": "counts of the individual's own settled outcomes; nothing is fitted, called or stored"}
    if not web:
        out["note"] += "; this individual has no Web Cortex records"
        return out
    predictions, used = _predictions(db, now)
    answers, answer_eta = _answers(db, now)
    hypotheses, hyp_eta = _hypotheses(db, now)
    out |= {"predictions": predictions, "answers": answers, "hypotheses": hypotheses,
            "rechecks": _rechecks(db) if _has(db, "web_rechecks") else None,
            "scenarios": _scenarios(db) if _has(db, "imagined") else None}
    learners = {}
    for kind in evolution.RECALIBRATION_KINDS:
        have, recent, first = used.get(kind, (0, 0, None))
        learners[f"forecast_recalibration.{kind}"] = {
            "counts": "settled predictions of this kind, confirmed or error, with a forecast",
            "constant": "webcortex.evolution.RECALIBRATION_MINIMUM",
            **_eta(have, evolution.RECALIBRATION_MINIMUM, recent, first, now)}
    shipped = monitoring.calibration().get("strata") or {}
    learners["answer_calibration"] = {
        "counts": "answers with a later verdict (the candidate items of a map fitted on real answers)",
        "constant": "webcortex.monitoring.MIN_ITEMS (and MIN_BLOCKS, which only a fit can show)",
        "minimum_blocks": monitoring.MIN_BLOCKS, **_eta(answer_eta[0], monitoring.MIN_ITEMS, answer_eta[1],
                                                       answer_eta[2], now),
        "shipped_maps": {name: {"items": sum(t.get("n") or ()), "blocks": len(t.get("x") or ()),
                                "applied": monitoring.applied(t)} for name, t in sorted(shipped.items())
                         if isinstance(t, dict)},
        "shipped_maps_fitted_on": "the cognitive benchmark's development split (fictional, offline), not real "
                                  "answers"}
    learners["scenario_records"] = {
        "counts": "scenario records whose prediction settled (imagined, mechanism scenarios.template)",
        "constant": None, "minimum": "no minimum in the code; webcortex.scenarios.errors compares them with three "
                                     "equally likely futures",
        "have": (out["scenarios"] or {}).get("settled", 0)}
    learners["strategy_selection"] = {
        "counts": "finished tasks per mode and those with an external outcome (the last strategies.WINDOW)",
        "constant": "webcortex.strategies.MIN_EFFECT (a held-out benchmark gain, not a count)",
        "minimum": "no count in the code: promotion needs benchmark evidence (strategies.gate), which real outcomes "
                   "alone cannot give", "window": strategies.WINDOW,
        "by_mode": _strategy_outcomes(db)}
    learners["hypothesis_settlement"] = {
        "counts": "hypotheses supported, refuted or revised", "constant": None,
        "minimum": "no minimum in the code", "have": hyp_eta[0]}
    out["learners"] = learners
    return out


def cli(args):
    """`nsf readiness`: the report (JSON), on a read-only connection (mode=ro, query_only: resources.shared
    .read_database), so the file is neither migrated nor written."""
    from .instance import store
    from .instance.errors import NotFound
    from .resources import shared
    path = store.resolve_db(args)
    try:
        with shared.read_database(path) as db:
            return report(db, time.time())
    except FileNotFoundError:
        raise NotFound(f"No instance at {path}; create one with `nsf init --db {path}`") from None


__all__ = ["RATE_DAYS", "SCHEMA", "cli", "report"]
