#!/usr/bin/env python3
"""Wave 2 lane L (ADR-056): the integration loop measured as one system, on the frozen worlds of
src/neuroscifi/acceptance/cognition/integrated_splits.py (committed before the runner and before any link).

  run        the "integrated" family (acceptance/cognition/integrated.py) on each split: baseline (every Wave 1 and
             Wave 2 setting off), defaults (as shipped), each link alone, integrated (every link on) and integrated
             minus one link at a time; per world the spread over the split's seeds and, per seed, the paired
             difference of integrated against the baseline and against the best single link. Then the cognitive
             benchmark's retention and adversarial families with every link on (mechanism "integrated") beside their
             baseline, and the self-description family (mechanism self_description: the record gained a field) on the
             same split. Cost: wall seconds per configuration, the process's peak resident set before and after.

The withheld family is never run, imported directly or read here. Usage:
  scripts/bench_wave2_integration.py run [--split development|held_out ...] [--out FILE] [--no-families]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import resource
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

HEADLINE = ("correct_mean", "correct_final", "caught_per_investigation", "noise_share", "wasted_share", "conflicts",
            "arbitrations", "belief_writes", "scenario_brier", "self_probes_correct", "investigations",
            "wall_seconds")
PAIRED = ("correct_mean", "correct_final", "caught_per_investigation", "noise_share")


def _peak_mib() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def paired(result, a, b) -> dict:
    """Per metric: the per-seed differences b - a over every world and seed, their mean and sd, and how many seeds
    b was higher, lower or equal."""
    from neuroscifi.acceptance.cognition import scoring
    out = {}
    ca, cb = result["configurations"][a]["worlds"], result["configurations"][b]["worlds"]
    for metric in PAIRED:
        diffs = []
        for world, rows in ca.items():
            for seed, row in rows["per_seed"].items():
                x, y = row[metric], cb[world]["per_seed"][seed][metric]
                if x is not None and y is not None:
                    diffs.append(y - x)
        s = scoring.spread(diffs)
        out[metric] = {"mean_difference": s["mean"], "sd": s["sd"], "n": s["n"],
                       "higher": sum(d > 1e-12 for d in diffs), "lower": sum(d < -1e-12 for d in diffs),
                       "equal": sum(abs(d) <= 1e-12 for d in diffs)}
    return out


def families(split) -> dict:
    from neuroscifi.acceptance import cognition
    recorded = json.loads(cognition.BASELINE.read_text())
    out = {}
    for family, mechanisms in (("retention", ("baseline", "integrated")), ("adversarial", ("baseline", "integrated")),
                               ("self_model", ("self_description",))):
        for mechanism in mechanisms:
            started = time.perf_counter()
            r = cognition.run([family], split=split, mechanism=mechanism, baseline_output=recorded)
            fam = r["families"][family]
            res = fam["result"]
            entry = {"mechanism": fam["mechanism"], "cost": fam["cost"],
                     "run_seconds": round(time.perf_counter() - started, 2),
                     "headline": {d: cognition.headline_value(res, d) for d, _ in cognition.HEADLINES[family]}}
            if family == "adversarial":
                entry["cases"] = {c["id"]: c["pass"] for c in res.get("cases") or []}
                entry["failed"] = res.get("failed")
            if family == "retention":
                entry["all_retained"] = res.get("all_retained")
                entry["scenarios_pass"] = {k: v.get("pass") for k, v in (res.get("scenarios") or {}).items()
                                           if isinstance(v, dict)}
            out[f"{family}/{mechanism}"] = entry
    return out


def run(splits, *, with_families=True) -> dict:
    from neuroscifi.acceptance import cognition
    from neuroscifi.acceptance.cognition import integrated
    cognition.load_plugins()            # the Wave 1 mechanisms (self_description among them), as bench_wave1.py does
    peak_before = _peak_mib()
    out = {"benchmark": "cognition, Wave 2 lane L: the integration loop", "environment": cognition.environment(),
           "label": cognition.LABEL, "links": {k: {"web": v[0], "instance": v[1]} for k, v in integrated.LINKS.items()},
           "worlds_file": "src/neuroscifi/acceptance/cognition/integrated_splits.py", "splits": {}}
    for split in splits:
        started = time.perf_counter()
        result = integrated.run(split)
        configs = result["configurations"]
        singles = [n for n in configs if n.startswith("alone:")]
        best = max(singles, key=lambda n: configs[n]["mean"]["correct_mean"])
        section = {"seeds": result["seeds"], "reference": result["reference"],
                   "headline": {n: {m: c["mean"][m] for m in HEADLINE} for n, c in configs.items()},
                   "baseline_spread": {w: {m: rows[m] for m in PAIRED}
                                       for w, rows in configs["baseline"]["worlds"].items()},
                   "integrated_vs_baseline": paired(result, "baseline", "integrated"),
                   "best_single_link": best,
                   "integrated_vs_best_single_link": paired(result, best, "integrated"),
                   "full": result, "run_seconds": round(time.perf_counter() - started, 2)}
        if with_families:
            section["families"] = families(split)
        out["splits"][split] = section
    out["peak_rss_mib"] = {"before": round(peak_before, 1), "after": round(_peak_mib(), 1),
                           "note": "the process's peak resident set (ru_maxrss), not one configuration's"}
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--split", action="append", choices=("development", "held_out"))
    r.add_argument("--no-families", action="store_true")
    r.add_argument("--out")
    args = p.parse_args(argv)
    os.environ.setdefault("PYTHONHASHSEED", "0")
    value = run(args.split or ["development", "held_out"], with_families=not args.no_families)
    text = json.dumps(value, indent=1, default=str) + "\n"
    if args.out:
        Path(args.out).write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
