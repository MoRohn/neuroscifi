"""Family 5, selective answering: does the confidence the pipeline attaches to an answer tell right answers from
wrong ones, and is it calibrated?

The offline Web Cortex benchmark (webcortex/bench/offline.py: the real pipeline against the fictional corpus, served
by FixtureWeb on loopback; no internet, no answer model) is run once per process and every report is kept. Items
are the corpus questions with gold labels (webcortex/bench/corpus.py, written with the pages and "never derived from
the system's output"): verification questions (gold: the verification state) and authoritative questions (gold: the
answer text). splits.SELECTIVE says which items develop and which are held out.

Two scorings:
- selective answering: answer only the items whose confidence is at least a threshold. The risk-coverage curve,
  AURC, coverage at 5 % and 10 % risk, and AUROC of confidence for correctness (scoring.risk_coverage, auroc).
  Correct = the verification state equals the gold state; for an authoritative question, the top quote contains the
  gold answer (the offline benchmark's answer_correct_top).
- calibration of the claim probability: on verification items whose gold is "supported" or "disputed", the forecast
  P(claim true) against the outcome (1 = supported). Brier with Murphy's decomposition and the number of items;
  ECE (10 equal-width bins and 5 equal-mass bins) is still computed but is not a headline and backs no claim: with
  about ten items per split, ten bins hold one item each (Wave 1 review B5).
- the selective decision, when a mechanism provides `decision(item)` ("answer" | "not_enough_evidence"): how many it
  would answer, the risk among those, and the wrong and right answers it would withhold.

The reference policy `verdict_word` (VerdictWord, the Wave 1 review's two-line rule on the verdict's state word) is
registered beside the baseline: a calibration claim is read against what two lines of rule already score.

The confidence interface: `confidence(item) -> float | None` (the probability that the answer given is right; None =
none known, scored as 0.5) and optionally `probability(item) -> float | None` (P(claim true) for a verification
item). item: {"id", "category", "question", "predicted", "report"}; never the gold. The baseline reads what the
pipeline exposes today: for a verification item the verdict's confidence c (P(claim supported)), taken as max(c,
1 - c) for the verdict it gives; for an authoritative item the confidence of the top quote (the claim cluster's
posterior). What it cannot show: calibration on the open web (a fixed fictional corpus of ~40 scored items; on so
few items the curve is coarse and the Brier difference between two settings is rarely outside noise).
"""
from __future__ import annotations

from pathlib import Path
import tempfile

from . import scoring

_CACHE: dict = {}


def corpus_run(*, quick: bool = False) -> dict:
    """{"metrics": the offline benchmark's deterministic metrics, "items": {question id: item with gold}}. Cached per
    process and mode (the run is deterministic: offline.diff over repeats is the benchmark's own check)."""
    if quick in _CACHE:
        return _CACHE[quick]
    from ...webcortex.bench import corpus as C
    from ...webcortex.bench import offline

    kept: dict = {}

    class _Keeping(offline._Bench):
        def questions(self):
            recs = super().questions()
            for qid, rec in recs.items():
                kept[qid] = rec["report"]
            return recs

    web = C.build_web().start()
    web.server.handle_error = lambda request, client_address: None      # as offline.run_once does
    try:
        with tempfile.TemporaryDirectory(prefix="nsf-cognition-web-") as directory:
            bench = _Keeping(Path(directory), web, quick)
            try:
                metrics = bench.execute()["deterministic"]
            finally:
                bench.close()
    finally:
        web.stop()
    items = {}
    verification = {i["id"]: i for i in metrics["verification"]["items"]}
    authoritative = {i["id"]: i for i in metrics["authoritative"]["items"]}
    for q in C.QUESTIONS:
        qid = q["id"]
        if qid in verification:
            v = verification[qid]
            items[qid] = {"id": qid, "category": "verification", "question": q["question"],
                          "predicted": v["predicted"], "report": kept.get(qid, {}), "gold": v["gold"],
                          "correct": bool(v["correct"])}
        elif qid in authoritative:
            a = authoritative[qid]
            items[qid] = {"id": qid, "category": "authoritative", "question": q["question"],
                          "predicted": a["top_quote"], "report": kept.get(qid, {}), "gold": q["gold_answer"],
                          "correct": bool(a["answer_correct_top"])}
    _CACHE[quick] = {"metrics": metrics, "items": items}
    return _CACHE[quick]


def public(item: dict) -> dict:
    """What a confidence function may see: everything but the gold and the correctness."""
    return {k: item[k] for k in ("id", "category", "question", "predicted", "report")}


class TodayConfidence:
    """Baseline: the confidence the pipeline already attaches to its answer."""

    def probability(self, item):
        verdict = item["report"].get("verdict") or {}
        c = verdict.get("confidence")
        return None if c is None else float(c)

    def confidence(self, item):
        if item["category"] == "verification":
            c = self.probability(item)
            return None if c is None else max(c, 1.0 - c)
        quotes = [a for a in item["report"].get("answer") or [] if a.get("type") == "quote"]
        c = quotes[0].get("confidence") if quotes else None
        return None if c is None else float(c)


def evaluate(split_spec: dict, make_confidence, *, quick: bool = False) -> dict:
    run = corpus_run(quick=quick)
    wanted = [q for q in split_spec["questions"] if q in run["items"]]
    fn = make_confidence()
    rows = []
    for qid in wanted:
        item = run["items"][qid]
        conf = fn.confidence(public(item))
        prob = fn.probability(public(item)) if hasattr(fn, "probability") else None
        decided = fn.decision(public(item)) if hasattr(fn, "decision") else None
        rows.append({"id": qid, "category": item["category"], "correct": item["correct"], "gold": item["gold"],
                     "confidence": conf, "probability": prob, "decision": decided})
    confs = [0.5 if r["confidence"] is None else r["confidence"] for r in rows]
    correct = [r["correct"] for r in rows]
    binary = [r for r in rows if r["category"] == "verification" and r["gold"] in ("supported", "disputed")
              and r["probability"] is not None]
    forecasts = [r["probability"] for r in binary]
    outcomes = [1 if r["gold"] == "supported" else 0 for r in binary]
    curve = scoring.risk_coverage(confs, correct)
    decided = [r for r in rows if r["decision"] is not None]
    answered = [r for r in decided if r["decision"] == "answer"]
    decision = None if not decided else {
        "items": len(decided), "answered": len(answered),
        "coverage": len(answered) / len(decided),
        "risk": sum(not r["correct"] for r in answered) / len(answered) if answered else None,
        "wrong_withheld": sum(1 for r in decided if r["decision"] != "answer" and not r["correct"]),
        "right_withheld": sum(1 for r in decided if r["decision"] != "answer" and r["correct"]),
        "wrong_items": sum(not r["correct"] for r in decided)}
    return {"items": len(rows), "missing_items": sorted(set(split_spec["questions"]) - set(wanted)),
            "decision": decision,
            "confidence_exposed": sum(r["confidence"] is not None for r in rows) / len(rows) if rows else None,
            "accuracy": sum(correct) / len(rows) if rows else None,
            "selective": {"aurc": curve["aurc"], "coverage_at_risk_0.05": curve.get("coverage_at_risk_0.05"),
                          "coverage_at_risk_0.10": curve.get("coverage_at_risk_0.10"),
                          "full_coverage_risk": curve.get("full_coverage_risk"),
                          "auroc_confidence_for_correctness": scoring.auroc(confs, correct), "curve": curve["points"]},
            "answer_confidence_calibration": {"brier": scoring.brier(confs, [int(c) for c in correct]),
                                              "ece_10_bins": scoring.ece(confs, [int(c) for c in correct])},
            "claim_probability": {"items": len(binary), "murphy": scoring.murphy(forecasts, outcomes),
                                  "ece_10_bins": scoring.ece(forecasts, outcomes),
                                  "ece_5_equal_mass": scoring.ece(forecasts, outcomes, bins=5, mass=True),
                                  "auroc": scoring.auroc(forecasts, outcomes)},
            "per_item": [{k: r[k] for k in ("id", "category", "correct", "confidence", "probability")} for r in rows]}


class VerdictWord:
    """Reference policy (Wave 1 review B5), not a mechanism: P(claim true) from the verdict's state word alone, 0.95
    for "supported", 0.05 for "disputed", 0.5 otherwise, and a constant answer confidence (no ranking). Two lines of
    rule: a calibration claim for any mechanism is read against it."""

    def probability(self, item):
        if item["category"] != "verification":
            return None
        word = (item["report"].get("verdict") or {}).get("state") or ""
        return 0.95 if word == "supported" else 0.05 if word == "disputed" else 0.5

    def confidence(self, item):
        return 0.99


def run(split_spec: dict, make_confidence, *, quick: bool = False) -> dict:
    return evaluate(split_spec, make_confidence, quick=quick)
