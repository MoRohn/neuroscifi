#!/usr/bin/env python3
"""The learning-cycles experiment (Wave 2 lane F; directive §7, deliverable F): does the recursive knowledge loop
improve knowledge over repeated cycles, and what does each of lane K's mechanisms add?

  python scripts/bench_learning_cycles.py run --split development                  # choose and debug here
  python scripts/bench_learning_cycles.py run --split held_out --out held.json      # report here, once
  python scripts/bench_learning_cycles.py verdict held.json                         # the pre-registered rule
  python scripts/bench_learning_cycles.py run --split development --cycles 3 --config baseline   # a quick look

Offline and deterministic per seed (apart from wall seconds); no model is called and no connection is made. The worlds
are frozen in src/neuroscifi/acceptance/cognition/cycles_splits.py (committed before this harness); what is measured,
and the verdict rule, are in src/neuroscifi/acceptance/cognition/cycles.py. The withheld family is never run here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from neuroscifi.acceptance.cognition import cycles, scoring        # noqa: E402


def _commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _write(value, out):
    text = json.dumps(scoring.rounded(value), indent=1)
    if out:
        Path(out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {out}")
    else:
        print(text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--split", choices=("development", "held_out"), required=True)
    r.add_argument("--config", action="append", help="repeatable; default every configuration")
    r.add_argument("--cycles", type=int, help="shorten every world to this many cycles")
    r.add_argument("--out")
    v = sub.add_parser("verdict")
    v.add_argument("result")
    v.add_argument("--out")
    a = ap.parse_args(argv)
    if a.command == "verdict":
        _write(cycles.verdicts(json.loads(Path(a.result).read_text(encoding="utf-8"))), a.out)
        return 0
    configs = cycles.configurations()
    if a.config:
        unknown = sorted(set(a.config) - set(configs))
        if unknown:
            ap.error(f"unknown configuration(s): {', '.join(unknown)}; known: {', '.join(configs)}")
        configs = {k: configs[k] for k in a.config}
    started = time.perf_counter()
    out = {"label": "lane F learning cycles: a simulated world of fictional software releases driven through the "
                    "real modules (extraction, clustering, consolidation, predictions, the timer); offline; no "
                    "model; search, fetching and the owner substituted",
           "commit": _commit(), "environment": {"python": platform.python_version(), "machine": platform.machine()}}
    out |= cycles.run_split(a.split, configs=configs, cycles=a.cycles)
    out["wall_seconds"] = time.perf_counter() - started
    _write(out, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
