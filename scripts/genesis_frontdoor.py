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
3. The property worlds (frontdoor_splits.py, committed before the mechanism) with exact moments and with moments
   estimated from 500, 5,000 and 50,000 draws: answers, declines and FALSE ANSWERS per kind, and on the trap kinds what
   a front-door formula through M would have answered (whether each trap is real).
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
from neuroscifi.acceptance.cognition import (adversarial, counterfactual, frontdoor_splits, imagination,  # noqa: E402
                                             scoring, splits)

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
                    got = causal.answer(q["query"])
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


def property_worlds():
    out = {}
    for split, spec in frontdoor_splits.FRONT_DOOR.items():
        by_moments = {}
        for moments in ("exact", *spec["samples"]):
            kinds = {}
            for kind in spec["kinds"]:
                answered = declined = false = within = strict = exact = 0
                errors, trap_errors, seconds = [], [], []
                for seed in spec["seeds"]:
                    world = frontdoor_splits.generate(kind, seed, split)
                    query = world["query"] if moments == "exact" else frontdoor_splits.sampled(world, moments)
                    started = time.perf_counter()
                    got = causal.answer(query)
                    seconds.append(time.perf_counter() - started)
                    if got["value"] is None:
                        declined += 1
                        if world["expect"] == "decline":
                            naive = _formula(query)
                            if naive is not None:
                                trap_errors.append(abs(naive - world["truth"]) / world["scale"])
                        continue
                    answered += 1
                    error = abs(got["value"] - world["truth"]) / world["scale"]
                    errors.append(error)
                    within += error <= frontdoor_splits.TOLERANCE + 1e-12
                    strict += error <= frontdoor_splits.STRICT_TOLERANCE + 1e-12
                    exact += error <= frontdoor_splits.EXACT
                    false += world["expect"] == "decline" or error > frontdoor_splits.TOLERANCE + 1e-12
                kinds[kind] = {"expect": "answer" if kind in frontdoor_splits.ANSWER_KINDS else "decline",
                               "worlds": len(spec["seeds"]), "answered": answered, "declined": declined,
                               "false_answers": false, "within_tolerance": within, "within_strict_tolerance": strict,
                               "within_exact": exact, "max_relative_error": max(errors) if errors else None,
                               "median_seconds": statistics.median(seconds)}
                if trap_errors:
                    kinds[kind]["front_door_formula_would_miss"] = {
                        "worlds": len(trap_errors), "beyond_tolerance": sum(e > frontdoor_splits.TOLERANCE
                                                                            for e in trap_errors),
                        "median_relative_error": statistics.median(trap_errors), "max_relative_error": max(trap_errors)}
            total = {k: sum(v[k] for v in kinds.values()) for k in ("worlds", "answered", "declined", "false_answers")}
            answer_kinds = [kinds[k] for k in frontdoor_splits.ANSWER_KINDS]
            total["answer_worlds"] = sum(v["worlds"] for v in answer_kinds)
            total["answer_worlds_within_tolerance"] = sum(v["within_tolerance"] for v in answer_kinds)
            total["answer_worlds_within_strict_tolerance"] = sum(v["within_strict_tolerance"] for v in answer_kinds)
            total["answer_worlds_within_exact"] = sum(v["within_exact"] for v in answer_kinds)
            total["decline_worlds_answered"] = sum(kinds[k]["answered"] for k in frontdoor_splits.DECLINE_KINDS)
            by_moments[str(moments)] = {"total": total, "kinds": kinds}
        out[split] = {"seeds": [spec["seeds"][0], spec["seeds"][-1]], "coefficients": spec["coefficients"],
                      "by_moments": by_moments}
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
