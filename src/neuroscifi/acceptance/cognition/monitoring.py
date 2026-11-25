"""Lane C (ADR-054): the answer-monitoring mechanism registered for the selective family.

The product side is webcortex/monitoring.py: a transparent score over signals the pipeline already produces, mapped
to a probability by a monotone calibration fitted on the DEVELOPMENT split only. This plugin

- runs the offline corpus once more with WebConfig.answer_monitoring on (cached per process), so every report carries
  the record the product writes, including what needs the run's documents and clusters (source dependence) and the
  consolidation (memory agreement); the answers themselves are the baseline run's (checked by
  scripts/bench_monitoring.py: the record changes no answer);
- recomputes the score from the record's signals with any ablated components left out, refits the calibration on
  the development items of splits.SELECTIVE (their gold says whether each answer was right), and answers the
  family's interface: confidence(item) = P(the answer given is right), probability(item) = P(the claim is true).

The held-out items are never used to fit or to choose anything. Components: the score's terms
(monitoring.COMPONENTS), "calibration" (off: the logistic of the raw score, no fitted map) and "stratify" (off: one
pooled answer-confidence map instead of one per answer kind).

Register with `--plugin neuroscifi.acceptance.cognition.monitoring` (or the PLUGINS line in __init__.py).
"""
from __future__ import annotations

from pathlib import Path
import tempfile

from . import register, splits
from . import selective as S

_RUNS: dict = {}


def monitored_run(*, quick: bool = False) -> dict:
    """{question id: report} of the offline corpus with answer_monitoring on (cached per process and mode)."""
    if quick in _RUNS:
        return _RUNS[quick]
    from ...webcortex import state
    from ...webcortex.bench import corpus as C
    from ...webcortex.bench import offline

    kept: dict = {}

    class _Monitored(offline._Bench):
        def _individual(self, path):
            db = super()._individual(path)
            state.set_config(db, {"answer_monitoring": True}, C.T0)
            return db

        def questions(self):
            recs = super().questions()
            for qid, rec in recs.items():
                kept[qid] = rec["report"]
            return recs

    web = C.build_web().start()
    web.server.handle_error = lambda request, client_address: None
    try:
        with tempfile.TemporaryDirectory(prefix="nsf-cognition-monitoring-") as directory:
            bench = _Monitored(Path(directory), web, quick)
            try:
                bench.execute()
            finally:
                bench.close()
    finally:
        web.stop()
    _RUNS[quick] = kept
    return kept


def rows(split: str, *, quick: bool = False) -> list[dict]:
    """The split's items: {"id", "correct", "gold", "category", "record"} (record = report["monitoring"])."""
    base = S.corpus_run(quick=quick)["items"]
    monitored = monitored_run(quick=quick)
    out = []
    for qid in splits.SELECTIVE[split]["questions"]:
        if qid in base and qid in monitored:
            out.append({"id": qid, "correct": base[qid]["correct"], "gold": base[qid]["gold"],
                        "category": base[qid]["category"], "record": monitored[qid].get("monitoring") or {}})
    return out


def _scores(record: dict, drop) -> tuple[float, float, str]:
    from ...webcortex import monitoring as M
    sig = dict(record.get("signals") or {}, statement=record.get("statement"))
    s = M.score(M.components(sig), drop)
    return s, M.answer_score(sig, s), M.stratum(sig)


def fit_tables(drop=frozenset(), *, quick: bool = False, pooled: bool = False) -> dict:
    """Calibration maps fitted on the development items only: "claim" on the fact checks whose gold is supported or
    disputed (outcome: supported), "verdict" and "answer" on the answers (outcome: the answer was right); pooled =
    one answer map for both kinds. Plus the selective threshold for monitoring.TARGET_RISK on development."""
    from ...webcortex import monitoring as M
    dev = rows("development", quick=quick)
    claim_x, claim_y, strata = [], [], {"verdict": ([], []), "answer": ([], [])}
    for r in dev:
        s, a, kind = _scores(r["record"], drop)
        if r["category"] == "verification" and r["gold"] in ("supported", "disputed"):
            claim_x.append(s)
            claim_y.append(r["gold"] == "supported")
        key = "answer" if pooled else kind
        strata[key][0].append(a)
        strata[key][1].append(r["correct"])
    tables = {"claim": M.fit(claim_x, claim_y)}
    for key, (x, y) in strata.items():
        if x:
            tables[key] = M.fit(x, y)
    if pooled:
        tables["verdict"] = tables["answer"]
    confs = [M.calibrate(_scores(r["record"], drop)[1], tables[_scores(r["record"], drop)[2]]) for r in dev]
    cut = M.threshold(confs, [r["correct"] for r in dev], M.TARGET_RISK)
    return {"strata": tables, "threshold": {"target_risk": M.TARGET_RISK, "confidence": cut},
            "fitted_on": "cognitive benchmark, selective family, development split (" +
                         ", ".join(r["id"] for r in dev) + ")"}


class MonitoredConfidence:
    def __init__(self, drop=frozenset(), *, calibrated=True, pooled=False, quick=False):
        self.drop, self.calibrated = frozenset(drop), calibrated
        self.tables = fit_tables(self.drop, quick=quick, pooled=pooled)["strata"] if calibrated else {}
        self.reports = monitored_run(quick=quick)

    def _record(self, item):
        return (self.reports.get(item["id"]) or {}).get("monitoring") or {}

    def probability(self, item):
        if item["category"] != "verification":
            return None
        from ...webcortex import monitoring as M
        s, _, _ = _scores(self._record(item), self.drop)
        return M.calibrate(s, self.tables.get("claim"))

    def confidence(self, item):
        from ...webcortex import monitoring as M
        record = self._record(item)
        if not record or record.get("kind") == "none":
            return None
        _, a, kind = _scores(record, self.drop)
        return M.calibrate(a, self.tables.get(kind))


def _make(ablate):
    from ...webcortex import monitoring as M
    drop = frozenset(a for a in ablate if a in M.COMPONENTS)
    return lambda: MonitoredConfidence(drop, calibrated="calibration" not in ablate, pooled="stratify" in ablate)


def _register():
    from ...webcortex import monitoring as M
    register("selective", "monitoring", _make, components=(*M.COMPONENTS, "calibration", "stratify"),
             description="ADR-054: transparent score over existing signals, monotone calibration fitted on the "
                         "development split (webcortex/monitoring.py)")


_register()
