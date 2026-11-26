#!/usr/bin/env python3
"""Lane K's benchmark (recursive knowledge evolution, ADR-055): retention of knowledge under change with and without
the knowledge re-check (and its ablations), and strategy selection from external outcomes against the incumbent.

  python scripts/bench_genesis_knowledge.py --split development                 # choose settings here
  python scripts/bench_genesis_knowledge.py --split held_out --out held.json   # report here, once
  python scripts/bench_genesis_knowledge.py --split held_out --part strategy
  python scripts/bench_genesis_knowledge.py --split held_out --decoupled --out d.json   # paired comparisons

Offline and deterministic per seed (apart from wall seconds). The worlds are frozen in
src/neuroscifi/acceptance/cognition/knowledge_splits.py; what each measures is in
src/neuroscifi/acceptance/cognition/knowledge.py. The withheld family is never run here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from neuroscifi.acceptance.cognition import knowledge, scoring           # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--split", choices=("development", "held_out"), required=True)
    p.add_argument("--part", choices=("recheck", "strategy", "both"), default="both")
    p.add_argument("--decoupled", action="store_true",
                   help="the decoupled draw (Wave 2 lane Y): the world's changes and the research outcomes from "
                        "separate keyed streams, for paired comparisons; the default is the recorded draw")
    p.add_argument("--out")
    a = p.parse_args(argv)
    started = time.perf_counter()
    out = {"label": "lane K benchmark: simulated worlds driven through the real modules; offline; no answer model",
           "split": a.split}
    if a.part in ("recheck", "both"):
        out["recheck"] = knowledge.run_recheck(a.split, decoupled=a.decoupled)
    if a.part in ("strategy", "both"):
        out["strategy"] = knowledge.run_strategy(a.split, decoupled=a.decoupled)
    out["wall_seconds"] = time.perf_counter() - started
    text = json.dumps(scoring.rounded(out), indent=1)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {a.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
