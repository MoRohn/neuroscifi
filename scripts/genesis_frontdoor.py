#!/usr/bin/env python3
"""Evidence for Wave 3 lane C, front-door adjustment in the linear causal engine (ADR-059):
docs/quality/evidence/genesis_frontdoor.json.

  python scripts/genesis_frontdoor.py --out docs/quality/evidence/genesis_frontdoor.json [--full-retention]

1. The counterfactual family on both splits (each split's own three seeds): today's answerer (baseline), the `zero`
   reference, the imagination mechanism with the front door on, with it off (the ablation), and the engine of the
   start commit (START: `git show START:src/neuroscifi/causal.py`, through today's answerer). Leaf comparisons: the
   ablation against the start commit's engine (every leaf), and on against off (every leaf outside the front-door
   group, the `scm:confounded` kind and the pooled figures).
2. Every front-door question of both splits answered one by one: truth, answer, relative error, interval score.
3. The property worlds of both criteria (frontdoor_splits.py, committed before the mechanism; backdoor_splits.py,
   committed before the sampling intervals) with exact moments and with moments estimated from 100, 500, 5,000 and
   50,000 draws, in every way a question can state them (REGIMES): answers, declines, interval coverage and FALSE
   ANSWERS per kind, and on the front-door trap kinds what a front-door formula through M would have answered.
4. The adversarial family with the mechanism (front door on, and off), retention with the mechanism, the cost.
Offline, no model; the withheld family is never run, read or imported here.
"""
from __future__ import annotations

import argparse
import functools
import importlib.util
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from neuroscifi import causal                                               # noqa: E402
from neuroscifi.acceptance import cognition                                 # noqa: E402
from neuroscifi.acceptance.cognition import (adversarial, backdoor_splits, counterfactual,  # noqa: E402
                                             frontdoor_splits, imagination, scoring, splits)

START = "9ac8943"
ROOT = Path(__file__).resolve().parents[1]


def _start_engine():
    """The causal module of the start commit, loaded from git (no checkout), behind today's keyword."""
    source = subprocess.run(["git", "-C", str(ROOT), "show", f"{START}:src/neuroscifi/causal.py"], check=True,
                            capture_output=True, text=True).stdout
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "causal_start.py"
        path.write_text(source.replace("from . import provenance", "from neuroscifi import provenance"))
        spec = importlib.util.spec_from_file_location("causal_start", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

    class Shim:
        NotIdentified = module.NotIdentified

        @staticmethod
        def answer(query, front_door=None):
            return module.answer(query)
    return Shim


def _leaves(value, path=""):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _leaves(v, f"{path}/{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _leaves(v, f"{path}[{i}]")
    else:
        yield path, value


def _diff(a, b, skip=()):
    la, lb = dict(_leaves(a)), dict(_leaves(b))
    keys = sorted(set(la) | set(lb))
    differ = [k for k in keys if la.get(k, "<absent>") != lb.get(k, "<absent>") and not any(s in k for s in skip)]
    return {"leaves": len(keys), "compared": len([k for k in keys if not any(s in k for s in skip)]),
            "different": len(differ), "first_different": differ[:10]}


def _family(split, mechanism, ablate=()):
    out = cognition.run(["counterfactual"], split=split, mechanism=mechanism, ablate=ablate)
    family = out["families"]["counterfactual"]
    result = family["result"]
    return {"result": {k: result[k] for k in ("summary", "summary_is", "summary_by_group", "per_seed",
                                              "by_query_kind")},
            "mechanism": family["mechanism"], "cost": family["cost"]}


def counterfactual_family():
    out = {}
    original = imagination.causal
    for split in ("development", "held_out"):
        row = {"seeds": list(splits.COUNTERFACTUAL[split]["seeds"])}
        for name, mechanism, ablate in (("baseline", "baseline", ()), ("zero", "zero", ()),
                                        ("front_door_on", "imagination", ()),
                                        ("front_door_off", "imagination", ("front_door",))):
            row[name] = _family(split, mechanism, ablate)
        imagination.causal = _start_engine()
        try:
            row["start_commit_engine"] = _family(split, "imagination")
        finally:
            imagination.causal = original
        row["off_vs_start_commit"] = _diff(row["front_door_off"]["result"], row["start_commit_engine"]["result"])
        row["on_vs_off_outside_the_front_door"] = _diff(
            row["front_door_on"]["result"], row["front_door_off"]["result"],
            skip=("/front_door/", "scm:confounded", "/summary/", "/per_seed["))
        row["on_vs_off_front_door_group"] = {
            k: {"on": row["front_door_on"]["result"]["summary_by_group"]["front_door"][k]["mean"],
                "off": row["front_door_off"]["result"]["summary_by_group"]["front_door"][k]["mean"]}
            for k in ("answered_within_tolerance", "answered_within_strict_tolerance", "max_relative_error",
                      "front_door_declined")}
        out[split] = row
    return out


def front_door_questions():
    out = {}
    for split in ("development", "held_out"):
        rows = []
        for seed in splits.COUNTERFACTUAL[split]["seeds"]:
            for spec in splits.COUNTERFACTUAL[split]["scm"]:
                for q in counterfactual.scm_queries(spec, seed):
                    if q.get("identified_by") != "front_door":
                        continue
                    started = time.perf_counter()
                    got = causal.answer(q["query"] | {"moments": "population"})   # as the family's answerer states
                    seconds = time.perf_counter() - started
                    rows.append({"seed": seed, "nodes": spec["nodes"], "truth": q["truth"], "value": got["value"],
                                 "identified_by": got["identified_by"], "mediators": got.get("mediators"),
                                 "relative_error": (None if got["value"] is None
                                                    else abs(got["value"] - q["truth"]) / q["scale"]),
                                 "interval_score": (None if got["value"] is None else scoring.interval_score(
                                     got["value"], got["sd"], q["truth"], q["scale"])),
                                 "seconds": seconds})
        errors = [r["relative_error"] for r in rows if r["relative_error"] is not None]
        out[split] = {"questions": len(rows), "answered": len(errors),
                      "within_tolerance": sum(e <= scoring.TOLERANCE for e in errors),
                      "within_strict_tolerance": sum(e <= scoring.STRICT_TOLERANCE for e in errors),
                      "max_relative_error": max(errors) if errors else None,
                      "mean_interval_score": statistics.fmean(r["interval_score"] for r in rows if r["value"] is not None)
                      if errors else None,
                      "interval_score_note": "a point answer (sd None): the interval has width 0, so its score is "
                                             "40 x the relative error; it says nothing about calibration",
                      "rows": rows}
    return out


def _formula(query):
    return adversarial._front_door_formula(query)


SAMPLES = (100, 500, 5000, 50000)
# How the question states its moments, per regime (ADR-059 follow-up):
#   exact                 the exact moments, declared "moments": "population"
#   stated_n              estimated moments with "samples": n: an answer carries a 95 % interval; FALSE = the truth
#                         outside it (or any answer to a trap); declined "not enough data" past causal.USEFUL
#   stated_n_unbounded    the same with the usefulness bound lifted (the intervals' own coverage, every world)
#   unstated              estimated moments, nothing said (front door: declined; back door: a point value as before)
#   declared_population   estimated moments wrongly declared exact: a point value; FALSE = error > 0.1 x sd(Y)
REGIMES = ("stated_n", "stated_n_unbounded", "unstated", "declared_population")


def _one(world, query, expect, interval):
    started = time.perf_counter()
    got = causal.answer(query)
    seconds = time.perf_counter() - started
    if got["value"] is None:
        return {"answered": False, "seconds": seconds, "not_enough_data": "not enough data" in (got["why"] or "")}
    error = abs(got["value"] - world["truth"]) / world["scale"]
    inside = got["sd"] is not None and abs(got["value"] - world["truth"]) <= scoring._Z * got["sd"]
    false = expect == "decline" or (not inside if interval else error > frontdoor_splits.TOLERANCE + 1e-12)
    return {"answered": True, "seconds": seconds, "error": error, "inside": inside, "false": false,
            "half_width": None if got["sd"] is None else scoring._Z * got["sd"] / world["scale"]}


def _tally(rows):
    answered = [r for r in rows if r["answered"]]
    errors = [r["error"] for r in answered]
    return {"worlds": len(rows), "answered": len(answered), "declined": len(rows) - len(answered),
            "declined_not_enough_data": sum(r.get("not_enough_data", False) for r in rows),
            "false_answers": sum(r["false"] for r in answered),
            "interval_coverage": (sum(r["inside"] for r in answered) / len(answered)
                                  if answered and all(r["half_width"] is not None for r in answered) else None),
            "within_tolerance": sum(e <= frontdoor_splits.TOLERANCE + 1e-12 for e in errors),
            "within_strict_tolerance": sum(e <= frontdoor_splits.STRICT_TOLERANCE + 1e-12 for e in errors),
            "within_exact": sum(e <= frontdoor_splits.EXACT for e in errors),
            "max_relative_error": max(errors) if errors else None,
            "median_half_width": (statistics.median(r["half_width"] for r in answered)
                                  if answered and answered[0]["half_width"] is not None else None),
            "median_seconds": statistics.median(r["seconds"] for r in rows)}


def _worlds(criterion, split):
    if criterion == "front_door":
        spec = frontdoor_splits.FRONT_DOOR[split]
        return [(frontdoor_splits, frontdoor_splits.generate(k, s, split),
                 "answer" if k in frontdoor_splits.ANSWER_KINDS else "decline")
                for k in spec["kinds"] for s in spec["seeds"]]
    spec = backdoor_splits.BACK_DOOR[split]
    return [(backdoor_splits, backdoor_splits.generate(k, s, split), "answer") for k in spec["kinds"]
            for s in spec["seeds"]]


def property_worlds():
    """Both criteria, both splits, every moment regime: totals, per kind, and (front door, exact moments) whether each
    trap is real."""
    out = {}
    saved = causal.USEFUL
    for criterion in ("front_door", "back_door"):
        for split in ("development", "held_out"):
            worlds = _worlds(criterion, split)
            by = {}
            rows = [(w["kind"], _one(w, w["query"] | {"moments": "population"}, e, False)) for _, w, e in worlds]
            by["exact"] = rows
            for n in SAMPLES:
                for regime in REGIMES:
                    rows = []
                    for module, world, expect in worlds:
                        q = module.sampled(world, n)
                        q = (q | {"samples": n} if regime.startswith("stated_n") else
                             q | {"moments": "population"} if regime == "declared_population" else q)
                        causal.USEFUL = 1e9 if regime == "stated_n_unbounded" else saved
                        try:
                            rows.append((world["kind"], _one(world, q, expect, regime.startswith("stated_n"))))
                        finally:
                            causal.USEFUL = saved
                    by[f"{regime}/{n}"] = rows
            section = {}
            for name, rows in by.items():
                kinds = {}
                for kind, row in rows:
                    kinds.setdefault(kind, []).append(row)
                section[name] = {"total": _tally([r for _, r in rows]),
                                 "answer_worlds": _tally([r for (_, w, e), (_, r) in zip(worlds, rows)
                                                          if e == "answer"]),
                                 "trap_worlds_answered": sum(r["answered"] for (_, w, e), (_, r) in zip(worlds, rows)
                                                             if e == "decline"),
                                 "kinds": {k: _tally(v) for k, v in kinds.items()}}
            if criterion == "front_door":
                traps = {}
                for _, world, expect in worlds:
                    if expect == "decline":
                        naive = _formula(world["query"])
                        if naive is not None:
                            traps.setdefault(world["kind"], []).append(abs(naive - world["truth"]) / world["scale"])
                section["front_door_formula_would_miss"] = {
                    k: {"worlds": len(v), "beyond_tolerance": sum(e > frontdoor_splits.TOLERANCE for e in v),
                        "median_relative_error": statistics.median(v), "max_relative_error": max(v)}
                    for k, v in traps.items()}
            out.setdefault(criterion, {})[split] = section
    out["regimes"] = {"exact": "exact moments declared population", **{r: r for r in REGIMES}}
    out["usefulness_bound"] = {"USEFUL": saved, "meaning": "an answer from estimated moments is given only when its "
                               "95 % half-width is at most USEFUL x sd(outcome); chosen on development only"}
    return out


def others(full_retention):
    """The adversarial family's mechanism has no components (the imagination settings as a new individual has them),
    so the front door is switched off for its second run by binding causal.answer's keyword for the run's duration."""
    rows, original = {}, causal.answer
    for name in ("front_door_on", "front_door_off"):
        if name == "front_door_off":
            causal.answer = functools.partial(original, front_door=False)
        try:
            result = cognition.run(["adversarial"], mechanism="imagination")["families"]["adversarial"]
        finally:
            causal.answer = original
        rows[name] = {k: result["result"][k] for k in ("passed", "scored", "failed", "by_origin")}
        rows[name]["added_wave3_c_cases"] = [{k: c[k] for k in ("id", "pass", "observed")}
                                             for c in result["result"]["cases"]
                                             if c["id"] in adversarial.EXPECTED_WAVE3_C]
    retention = cognition.run(["retention"], mechanism="imagination", quick=not full_retention,
                              baseline_output=json.loads(cognition.BASELINE.read_text(encoding="utf-8")))
    family = retention["families"]["retention"]
    return {"adversarial": rows,
            "retention": {"quick": not full_retention, "mechanism": family["mechanism"],
                          **{k: family["result"].get(k) for k in ("all_scenarios_pass", "all_retained")},
                          "cost": family["cost"]}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path)
    ap.add_argument("--full-retention", action="store_true")
    a = ap.parse_args(argv)
    cognition.load_plugins()
    out = {"what": "Wave 3 lane C, front-door adjustment in the linear causal engine (ADR-059): the counterfactual "
                   "family with the front door on, off and the start commit's engine; every front-door question; "
                   "the property worlds with false answers counted; adversarial, retention and cost",
           "label": cognition.LABEL, "environment": cognition.environment(), "start_commit": START,
           "counterfactual": counterfactual_family(), "front_door_questions": front_door_questions(),
           "property_worlds": property_worlds(), **others(a.full_retention)}
    text = json.dumps(scoring.rounded(out, 12), indent=1) + "\n"
    if a.out:
        a.out.write_text(text, encoding="utf-8")
        print(f"wrote {a.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
