#!/usr/bin/env python3
"""Wave 4 fix lane: the summary evidence of the source-independence family after the reviews' fixes.

  python scripts/summarise_wave4_fix.py --dev dev.json --held held.json --out docs/quality/evidence/genesis_wave4_fix.json

`dev.json` / `held.json`: `scripts/bench_sources.py run --split development|held_out --config shipped --config
own_text_provenance --config source_derivation --config wave4:both --config echo_recognition`. Summaries only: per
configuration the totals over worlds and seeds, per world the totals over seeds and mean_correct, per shape the sums,
and the verdicts committed before the held-out runs (sources.own_verdict, sources.verdict_derivation), applied with
the same rules; no per-look history.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from neuroscifi.acceptance.cognition import scoring, sources        # noqa: E402

FIELDS = ("looks", "pages_with_claims", "eligible", "admitted", "wrongly_admitted", "wrongly_admitted_false",
          "own_admitted", "suppressed", "suppressed_contested", "dependent_rises", "discarded_echo_pages",
          "discarded_honest_pages", "tp", "fp", "fn")


def _split(out) -> dict:
    configs = out["configurations"]
    summary = {"commit": out.get("commit"), "dirty": out.get("dirty"), "seeds": out["seeds"], "configurations": {}}
    for name, entry in configs.items():
        worlds = entry["worlds"]
        totals = {k: sum(w["totals_sum"].get(k, 0) for w in worlds.values()) for k in FIELDS}
        tp, fp, fn = totals["tp"], totals["fp"], totals["fn"]
        shapes = sorted({s for w in worlds.values() for s in w["by_shape_sum"]})
        summary["configurations"][name] = {
            "read_back": entry.get("settings"),
            "totals": totals,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None,
            "worlds": {w: {"totals_sum": {k: e["totals_sum"].get(k, 0) for k in FIELDS},
                           "mean_correct": e["mean_correct"],
                           "per_seed_mean_correct": [s["mean_correct"] for s in e["per_seed"]]}
                       for w, e in worlds.items()},
            "by_shape_sum": {s: {k: sum(w["by_shape_sum"].get(s, {}).get(k, 0) for w in worlds.values())
                                 for k in FIELDS} for s in shapes}}
    have = set(configs)
    verdicts = {}
    for name in ("own_text_provenance", "echo_recognition", "wave4:both"):
        if {name, "shipped"} <= have:
            verdicts[f"own_verdict:{name}"] = sources.own_verdict(out, config=name)
    for name in ("source_derivation", "wave4:both"):
        if {name, "shipped"} <= have:
            v = sources.verdict_derivation(out, rule=name)
            verdicts[f"verdict_derivation:{name}"] = {k: v[k] for k in ("verdict", "A_admissions", "B_suppression",
                                                                        "C_correct", "D_grouping", "totals",
                                                                        "precision_recall", "paired_correct")}
    summary["verdicts"] = verdicts
    summary["world_digests"] = out.get("world_digests")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dev", required=True)
    ap.add_argument("--held", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    value = {"label": "Wave 4 fix lane (gen4-fix): the source-independence family after the reviews' fixes "
                      "(review4 security and measurement); simulated worlds of fictional software releases read "
                      "through the real modules, offline, no model; summaries per configuration, world and shape",
             "development": _split(json.loads(Path(a.dev).read_text())),
             "held_out": _split(json.loads(Path(a.held).read_text()))}
    Path(a.out).write_text(json.dumps(scoring.rounded(value), indent=1) + "\n", encoding="utf-8")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
