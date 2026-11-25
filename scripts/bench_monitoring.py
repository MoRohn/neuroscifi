#!/usr/bin/env python3
"""Lane C evidence (ADR-054): answer monitoring, selective answering, escalation, adaptive depth, the self-critique's
marks and the executive's precision report.

  python scripts/bench_monitoring.py fit        # fit the calibration on the DEVELOPMENT split only and write
                                                # src/neuroscifi/webcortex/data/monitoring_calibration.json
  python scripts/bench_monitoring.py evidence   # every measurement -> docs/quality/evidence/genesis_monitoring.json

Everything is offline (the fictional corpus on loopback, no answer model). The self-critique section reads the
recorded live-model runs of scripts/bench_pipeline.py already in docs/quality/evidence (no model is called here).
The withheld family is never run.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from neuroscifi.acceptance import cognition                               # noqa: E402
from neuroscifi.acceptance.cognition import monitoring as P, scoring     # noqa: E402
from neuroscifi.acceptance.cognition import selective as S               # noqa: E402
from neuroscifi.webcortex import monitoring as M                          # noqa: E402

OUT = ROOT / "docs" / "quality" / "evidence" / "genesis_monitoring.json"
SPLITS = ("development", "held_out")


# ---- calibration -------------------------------------------------------------------------------------------------

def fit() -> dict:
    table = P.fit_tables()
    table = {"version": M.VERSION, "weights": M.WEIGHTS, **table,
             "note": "monotone maps (pool adjacent violators, Beta(1,1) per block) from the monitoring score to a "
                     "probability, fitted by scripts/bench_monitoring.py fit on the development split only; "
                     "fictional offline corpus, not the open web"}
    M.CALIBRATION_FILE.write_text(json.dumps(table, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    M.calibration.cache_clear()
    return table


def debiased_ece(forecasts, outcomes, bins=5) -> dict:
    """Equal-mass bins. plug_in: the usual l1 ECE. debiased_l2: sqrt(max(0, sum_b w_b [(ybar_b - pbar_b)^2 -
    ybar_b (1 - ybar_b) / (n_b - 1)])), the bias-corrected squared calibration error (Kumar, Liang & Ma 2019,
    "Verified uncertainty calibration"); the table: per bin n, mean forecast, observed rate."""
    pairs = sorted(zip(forecasts, outcomes))
    n = len(pairs)
    if not n:
        return {"n": 0}
    size = max(1, n // bins)
    groups = [pairs[i:i + size] for i in range(0, n, size)]
    if len(groups) > bins:
        groups[-2] += groups[-1]
        groups.pop()
    table, l1, l2 = [], 0.0, 0.0
    for g in groups:
        k = len(g)
        p = sum(f for f, _ in g) / k
        y = sum(o for _, o in g) / k
        table.append({"n": k, "mean_forecast": round(p, 4), "observed": round(y, 4)})
        l1 += k / n * abs(y - p)
        l2 += k / n * ((y - p) ** 2 - (y * (1 - y) / (k - 1) if k > 1 else 0.0))
    return {"n": n, "bins": len(groups), "plug_in_l1": round(l1, 4), "debiased_l2": round(max(0.0, l2) ** 0.5, 4),
            "reliability_table": table}


def family(split, mechanism="baseline", ablate=()) -> dict:
    out = cognition.run(["selective"], split=split, mechanism=mechanism, ablate=ablate)
    return out["families"]["selective"]["result"]


def headline(result) -> dict:
    cp = result["claim_probability"]
    return {"aurc": result["selective"]["aurc"], "auroc_answer": result["selective"]["auroc_confidence_for_correctness"],
            "coverage_at_risk_0.05": result["selective"]["coverage_at_risk_0.05"],
            "answer_brier": result["answer_confidence_calibration"]["brier"],
            "answer_ece_10": result["answer_confidence_calibration"]["ece_10_bins"],
            "claim_brier": cp["murphy"]["brier"] if cp["murphy"] else None, "claim_ece_10": cp["ece_10_bins"],
            "claim_auroc": cp["auroc"]}


def detail(result, split) -> dict:
    items = S.corpus_run()["items"]
    rows = result["per_item"]
    conf = [0.5 if r["confidence"] is None else r["confidence"] for r in rows]
    correct = [int(r["correct"]) for r in rows]
    binary = [r for r in rows if items[r["id"]]["category"] == "verification" and r["probability"] is not None
              and items[r["id"]]["gold"] in ("supported", "disputed")]
    forecasts = [r["probability"] for r in binary]
    outcomes = [int(items[r["id"]]["gold"] == "supported") for r in binary]
    return {"answer_confidence": {"murphy": scoring.murphy(conf, correct), **debiased_ece(conf, correct)},
            "claim_probability": {"murphy": scoring.murphy(forecasts, outcomes), **debiased_ece(forecasts, outcomes)},
            "risk_coverage_curve": result["selective"]["curve"]}


def at_threshold(result, cut) -> dict:
    rows = result["per_item"]
    answered = [r for r in rows if r["confidence"] is not None and cut is not None and r["confidence"] >= cut]
    return {"threshold": cut, "coverage": round(len(answered) / len(rows), 4) if rows else None,
            "risk": round(sum(not r["correct"] for r in answered) / len(answered), 4) if answered else None,
            "abstained_items": sorted(r["id"] for r in rows if r not in answered),
            "abstained_wrong": sum(1 for r in rows if r not in answered and not r["correct"])}


def calibration_section() -> dict:
    out = {"baseline": {}, "monitoring": {}, "detail": {}, "ablation": {}}
    for split in SPLITS:
        base, mon = family(split), family(split, "monitoring")
        out["baseline"][split] = headline(base)
        out["monitoring"][split] = headline(mon)
        out["detail"][split] = {"baseline": detail(base, split), "monitoring": detail(mon, split)}
    mech = next(m for m in cognition.mechanisms() if m.family == "selective" and m.name == "monitoring")
    for component in mech.components:
        out["ablation"][component] = {split: headline(family(split, "monitoring", (component,))) for split in SPLITS}
    return out


def selective_section() -> dict:
    """The threshold chosen on development for TARGET_RISK, applied unchanged to held-out; the baseline's own
    confidence with a threshold chosen the same way is the ablation (no monitoring)."""
    out = {"target_risk": M.TARGET_RISK}
    for name in ("monitoring", "baseline"):
        dev = family("development", name)
        cut = M.threshold([0.5 if r["confidence"] is None else r["confidence"] for r in dev["per_item"]],
                          [r["correct"] for r in dev["per_item"]], M.TARGET_RISK)
        out[name] = {split: at_threshold(family(split, name), cut) for split in SPLITS}
    out["shipped_threshold"] = (M.calibration().get("threshold") or {}).get("confidence")
    return out


# ---- the offline research benchmark with each setting --------------------------------------------------------------

def offline_run(config: dict) -> dict:
    from neuroscifi.webcortex import state
    from neuroscifi.webcortex.bench import corpus as C
    from neuroscifi.webcortex.bench import offline

    class _Configured(offline._Bench):
        def _individual(self, path):
            db = super()._individual(path)
            if config:
                state.set_config(db, config, C.T0)
            return db

    web = C.build_web().start()
    web.server.handle_error = lambda request, client_address: None
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="nsf-monitoring-bench-") as directory:
            bench = _Configured(Path(directory), web, False)
            try:
                result = bench.execute()
                records = {r["key"]: r["report"].get("monitoring") for r in bench.records}
                usage = {r["key"]: {k: r["usage"].get(k) for k in ("queries", "requests", "pages")}
                         for r in bench.records}
            finally:
                bench.close()
    finally:
        web.stop()
    return {"deterministic": result["deterministic"], "records": records, "usage": usage,
            "wall_seconds": round(time.perf_counter() - started, 2)}


# sha256 of json.dumps(deterministic metrics, sort_keys=True, default=str) of offline.run_once() on the unchanged code
# (commit 2aee516, the lane's start), measured before any lane C change: the run with every lane C setting off must
# reproduce it exactly.
UNCHANGED_DIGEST = "68731d7235cbae9fca289124ea59bed99c1aa5b578ecaf8784d2e3df54cdf9d5"

KEY_METRICS = ("verification.states.accuracy", "authoritative.answer_correct_top.rate",
               "authoritative.retrieval_success.rate", "contradictions.detector.all.f1",
               "citations.per_cited_source.precision", "time_sensitive.freshness_compliance.rate", "budgets.ok")


def _metric(det, dotted):
    return cognition._lookup(det, dotted)


def offline_section() -> dict:
    from neuroscifi.webcortex.bench import offline
    off = {"answer_monitoring": False, "selective_answering": False, "verification_escalation": False,
           "adaptive_depth": False}
    runs = {"off": off, "off_repeat": off, "defaults": {}, "answer_monitoring": {"answer_monitoring": True},
            "selective_answering": {"answer_monitoring": True, "selective_answering": True},
            "verification_escalation": {"answer_monitoring": True, "verification_escalation": True},
            "escalation_and_selective": {"answer_monitoring": True, "verification_escalation": True,
                                         "selective_answering": True},
            "adaptive_depth": {"answer_monitoring": True, "adaptive_depth": True}}
    done = {name: offline_run(cfg) for name, cfg in runs.items()}
    off = done["off"]
    digest = hashlib.sha256(json.dumps(off["deterministic"], sort_keys=True, default=str).encode()).hexdigest()
    out = {"label": "offline research benchmark (nsf web bench corpus, loopback, no model), in process; "
                    "docs/quality/evidence/webcortex_bench.json is neither read nor written",
           "settings": runs,
           "off_equals_repeat": offline.diff(off["deterministic"], done["off_repeat"]["deterministic"]) == [],
           "off_digest": digest, "unchanged_code_digest": UNCHANGED_DIGEST,
           "off_equals_unchanged_code": digest == UNCHANGED_DIGEST,
           "runs": {}}
    for name, run in done.items():
        if name == "off_repeat":
            continue
        diffs = offline.diff(off["deterministic"], run["deterministic"])
        totals = {k: sum(u[k] or 0 for u in run["usage"].values()) for k in ("queries", "requests", "pages")}
        recs = [r for r in run["records"].values() if r]
        entry = {"key_metrics": {m: _metric(run["deterministic"], m) for m in KEY_METRICS},
                 "differences_from_off": len(diffs), "first_differences": diffs[:25],
                 "usage_totals": totals, "wall_seconds": run["wall_seconds"],
                 "records": len(recs)}
        if recs:
            entry["not_enough_evidence"] = sorted(k for k, r in run["records"].items()
                                                  if r and r["decision"] == "not_enough_evidence")
            entry["escalated"] = {k: (r.get("depth") or {}).get("escalated") for k, r in run["records"].items()
                                  if r and (r.get("depth") or {}).get("escalated")}
            entry["stopped_early"] = {k: (r.get("depth") or {}).get("stopped_early") for k, r in run["records"].items()
                                      if r and (r.get("depth") or {}).get("stopped_early")}
            entry["dependence"] = {k: (r["signals"].get("independence")) for k, r in run["records"].items()
                                   if r and (r["signals"].get("independence") or {}).get("effective_groups", 0)
                                   < (r["signals"].get("independence") or {}).get("groups", 0)}
            entry["depth_records"] = {k: {x: (r.get("depth") or {}).get(x) for x in
                                          ("pages", "verified_after_page", "pages_after_last_gain")}
                                      for k, r in sorted(run["records"].items()) if r}
        out["runs"][name] = entry
    return out


# ---- the self-critique's marks against corpus gold --------------------------------------------------------------

# Values the corpus marks false for a subject (webcortex/bench/corpus.py QUESTIONS, written with the pages before
# any measurement): the authoritative questions' `noise` values and the false claims of the fact checks whose gold
# label is "disputed". (subject pattern, false-value pattern).
FALSE_STATEMENTS = (
    (r"quillstream", r"\b3\.9\b"), (r"varnel", r"\b520\b"), (r"nimbus", r"\b6\.12\b"),
    (r"tessaly", r"\b251,000\b"), (r"lumen-7", r"\b21 september\b"), (r"orbix", r"\bport 9000\b|\b9000\b"),
    (r"corran spire", r"\b(?:is|stands|located) in tessaly\b"), (r"halvorsen", r"\b1995\b"),
    (r"marrow creek", r"\bevery 30 minutes\b"),
)
DISAGREEMENT = re.compile(r"disagree|disputed|contradict|conflict", re.IGNORECASE)


def _label(sentence) -> dict:
    text = sentence["text"].lower()
    false_value = any(re.search(subj, text) and re.search(val, text) for subj, val in FALSE_STATEMENTS)
    cites_disputed = any(c.get("state") == "disputed" for c in sentence.get("cited") or [])
    return {"states_false_value": false_value,
            "states_false_value_unhedged": false_value and not DISAGREEMENT.search(text),
            "cites_disputed_claim": cites_disputed}


def _pr(flags, labels) -> dict:
    tp = sum(1 for f, y in zip(flags, labels) if f and y)
    fp = sum(1 for f, y in zip(flags, labels) if f and not y)
    fn = sum(1 for f, y in zip(flags, labels) if not f and y)
    base = sum(labels) / len(labels) if labels else None
    # one-sided hypergeometric p: flagging sum(flags) of the sentences at random, at least tp of the positives
    # would be among them with this probability (small = the marks carry information about the label)
    from math import comb
    n, k, pos = len(labels), sum(flags), sum(labels)
    p_value = sum(comb(pos, i) * comb(n - pos, k - i) for i in range(tp, min(pos, k) + 1)) / comb(n, k) \
        if n and pos else None
    return {"positives": sum(labels), "flagged": sum(flags), "true_positives": tp,
            "p_value_vs_random_marks": round(p_value, 4) if p_value is not None else None,
            "precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None,
            "base_rate": round(base, 4) if base is not None else None}


def critique_section() -> dict:
    out = {"gold": "a kept sentence is wrong when it states a value the corpus marks false for its subject "
                   "(FALSE_STATEMENTS above, from the corpus gold); also reported: without a disagreement marker, "
                   "and citing a claim the pipeline verified as disputed",
           "source": "recorded live-model runs of scripts/bench_pipeline.py --texts (no model called here)",
           "runs": {}}
    for name in ("accel_baseline_pipeline.json", "accel_after_pipeline.json"):
        path = ROOT / "docs" / "quality" / "evidence" / name
        data = json.loads(path.read_text())
        sentences = [s for t in data.get("tasks") or [] for s in t.get("answer") or [] if "text" in s]
        if not sentences:
            continue
        flags = [bool(s.get("flag")) for s in sentences]
        labels = [_label(s) for s in sentences]
        out["runs"][name] = {"model": data.get("model"), "label": data.get("label"), "sentences": len(sentences),
                             "flags_by_problem": dict(Counter(s["flag"]["problem"] for s in sentences
                                                              if s.get("flag"))),
                             **{key: _pr(flags, [lab[key] for lab in labels]) for key in labels[0]}}
    return out


# ---- the executive's precision report on the staged grounding lifetimes ----------------------------------------------

def precision_section(seeds=(0, 1, 2), lifetimes=40) -> dict:
    from neuroscifi.acceptance import scenarios
    from neuroscifi.instance import executive, store
    out = {"label": "acceptance grounding lifetimes (simulated owner who answers truthfully), product flow arm",
           "seeds": {}}
    for seed in seeds:
        with tempfile.TemporaryDirectory(prefix="nsf-monitoring-precision-") as directory:
            db = store.create(Path(directory) / "p.sqlite", scenarios.T0, note="lane C precision report")
            try:
                owner = Counter()
                for index in range(lifetimes):
                    life = scenarios._grounding_lifetime(seed, index)
                    logged = scenarios._run_detecting(db, life, index, manual=False)
                    owner.update({k: logged["owner"][k] for k in ("confirmed", "rejected")})
                report = executive.precision(db)
            finally:
                db.close()
        alarm = report["by_rule"].get("change_alarm") or {}
        decided = owner["confirmed"] + owner["rejected"]
        out["seeds"][seed] = {"by_rule": report["by_rule"], "epochs": len(report["by_epoch"]),
                              "scenario_log": dict(owner),
                              "scenario_precision": round(owner["confirmed"] / decided, 4) if decided else None,
                              "report_matches_scenario_log":
                                  (alarm.get("confirmed", 0) + (report["by_rule"].get("change_ambiguous") or {})
                                   .get("confirmed", 0)) == owner["confirmed"]
                                  and (alarm.get("rejected", 0) + (report["by_rule"].get("change_ambiguous") or {})
                                       .get("rejected", 0)) == owner["rejected"]}
    return out


def evidence() -> dict:
    started = time.perf_counter()
    out = {"benchmark": "genesis lane C (monitoring)", "adr": "ADR-054", "environment": cognition.environment(),
           "calibration_file": str(M.CALIBRATION_FILE.relative_to(ROOT)),
           "calibration_sha256": cognition.digest(M.CALIBRATION_FILE)}
    out["calibration"] = calibration_section()
    out["selective_answering"] = selective_section()
    out["offline"] = offline_section()
    out["critique_marks"] = critique_section()
    out["precision_report"] = precision_section()
    out["wall_seconds"] = round(time.perf_counter() - started, 1)
    return scoring.rounded(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("fit", "evidence"))
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    if a.command == "fit":
        table = fit()
        print(json.dumps(table, indent=1))
        return 0
    result = evidence()
    Path(a.out).write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("calibration", "selective_answering")}, indent=1)[:6000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
