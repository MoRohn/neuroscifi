#!/usr/bin/env python3
"""Wave 4 lane E (ADR-067): the summary evidence of own-text provenance, written from full runs kept outside the tree.

  python scripts/summarise_wave4_e.py --dev dev.json --held held.json --cycles cycles.json \
      --extra extra.json --out docs/quality/evidence/genesis_wave4_e.json

`dev.json` / `held.json`: `scripts/bench_sources.py run --split development|held_out` (every configuration);
`cycles.json`: `scripts/bench_wave3_s.py run ... --config baseline --config fix:echo --config fix:own`; `extra.json`:
the default-path comparison, the offline digests, the adversarial family and the cost (written by hand from the
commands named in it). Summaries only: per world and seed totals, per shape sums, the verdicts; no per-look history.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from neuroscifi.acceptance.cognition import scoring, sources        # noqa: E402

CONFIGS = ("shipped", "echo_recognition", "own_text_provenance")
FIELDS = ("looks", "eligible", "admitted", "wrongly_admitted", "wrongly_admitted_false", "own_admitted", "suppressed",
          "suppressed_contested", "dependent_rises", "discarded_echo_pages", "discarded_honest_pages", "tp", "fp", "fn")
CYCLE_FIELDS = ("self_admitted", "admitted.own_only", "admitted.wrong", "admitted.rows", "mean.correct",
                "retention.lost", "retention.regressed")


def _rate(row):
    tp, fp, fn = row.get("tp", 0), row.get("fp", 0), row.get("fn", 0)
    return {"precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None}


def _split(result) -> dict:
    out = {"seeds": result["seeds"], "commit": result.get("commit"), "configurations": {}, "references": {},
           "verdicts": {c: sources.own_verdict(result, c) for c in ("own_text_provenance", "echo_recognition")}}
    for cfg in CONFIGS:
        worlds = result["configurations"][cfg]["worlds"]
        out["configurations"][cfg] = {
            w: {"totals": {k: e["totals_sum"].get(k, 0) for k in FIELDS} | _rate(e["totals_sum"]),
                "per_seed": [{k: s["totals"].get(k, 0) for k in ("own_admitted", "wrongly_admitted", "suppressed",
                                                                 "discarded_echo_pages", "discarded_honest_pages")}
                             | {"mean_correct": s["mean_correct"]} for s in e["per_seed"]],
                "by_shape": {s: {k: v.get(k, 0) for k in FIELDS} for s, v in e["by_shape_sum"].items()},
                "mean_correct": e["mean_correct"], "looks_changed_vs_shipped": e.get("looks_changed_vs_shipped"),
                "config_read_back": e.get("config_read_back")}
            for w, e in worlds.items()}
    for w, refs in result["references"].items():
        out["references"][w] = {r: {"totals": {k: v["totals_sum"].get(k, 0) for k in FIELDS} | _rate(v["totals_sum"]),
                                    "mean_correct": v["mean_correct"]} for r, v in refs.items()}
    return out


def _by_shape_pooled(split, cfg) -> dict:
    """Per shape, summed over the split's worlds and seeds (pooled, not a claim): both directions."""
    pooled = {}
    for w in split["configurations"][cfg].values():
        for s, v in w["by_shape"].items():
            row = pooled.setdefault(s, dict.fromkeys(FIELDS, 0))
            for k in FIELDS:
                row[k] += v[k]
    return pooled


def _drop(dev, held) -> dict:
    """dev -> held-out, per looks of the shape (pooled over worlds): own answers admitted and honest pages discarded
    for the rule and for shipped, so the drop is visible beside the counts."""
    out = {}
    for cfg in CONFIGS:
        d, h = _by_shape_pooled(dev, cfg), _by_shape_pooled(held, cfg)
        out[cfg] = {}
        for s in sorted(set(d) | set(h)):
            row = {}
            for split, data in (("development", d.get(s)), ("held_out", h.get(s))):
                if not data or not data["looks"]:
                    row[split] = None
                    continue
                row[split] = {k: round(data[k] / data["looks"], 4) for k in
                              ("own_admitted", "wrongly_admitted", "suppressed", "discarded_echo_pages",
                               "discarded_honest_pages")} | {"looks": data["looks"]}
            out[cfg][s] = row
    return out


def _cycles(result) -> dict:
    out = {"commit": result.get("commit"), "results": {}, "verdicts": result.get("verdicts")}
    for name, res in result["results"].items():
        out["results"][name] = {
            cfg: {w: {k: round(sum(s.get(k) or 0 for s in e["per_seed"]), 4) for k in CYCLE_FIELDS}
                  | {"seeds": len(e["per_seed"]), "own_text_provenance_read_back":
                     (e.get("config_read_back") or {}).get("own_text_provenance")}
                  for w, e in c["worlds"].items()}
            for cfg, c in res["configurations"].items()}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dev", required=True)
    ap.add_argument("--held", required=True)
    ap.add_argument("--cycles", required=True)
    ap.add_argument("--extra", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    dev, held = (_split(json.loads(Path(p).read_text())) for p in (a.dev, a.held))
    value = {"label": "Wave 4 lane E (ADR-067): own-text provenance on the source-independence family (development, "
                      "then held-out after the verdict rule was committed), Wave 3's lane S and lane F learning-cycle "
                      "worlds; simulated worlds read through the real modules, offline, no model",
             "development": dev, "held_out": held, "dev_to_held_out_per_look": _drop(dev, held),
             "cycles": _cycles(json.loads(Path(a.cycles).read_text())),
             "extra": json.loads(Path(a.extra).read_text())}
    Path(a.out).write_text(json.dumps(scoring.rounded(value), indent=1) + "\n", encoding="utf-8")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
