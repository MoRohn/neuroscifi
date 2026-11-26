#!/usr/bin/env python3
"""Wave 3 lane S (ADR-061): what counts as an independent source, the individual's own text, and supersession of a
stale item, measured on the learning-cycle worlds.

  python scripts/bench_wave3_s.py run --out docs/quality/evidence/genesis_wave3_s.json     # every split and family
  python scripts/bench_wave3_s.py run --family lane-s --split development --config fixes   # a quick look

Offline and deterministic per seed (apart from wall seconds); no model is called and no connection is made. Worlds:
src/neuroscifi/acceptance/cognition/cycles_splits_s.py (lane S, committed alone before any mechanism) and
cycles_splits.py (lane F; its held-out split was looked at in Wave 2: disclosed, never a claim alone). The withheld
learning-cycles worlds are never loaded here; that look is the main session's (cycles.final), once.
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

from neuroscifi.acceptance.cognition import cycles_s, scoring        # noqa: E402


def _commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _dirty():
    try:
        return bool(subprocess.run(["git", "status", "--porcelain", "--", "src", "scripts"], cwd=ROOT,
                                   capture_output=True, text=True, check=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def run(families, splits, configs) -> dict:
    started = time.perf_counter()
    out = {"generated_by": "scripts/bench_wave3_s.py run", "commit": _commit(), "dirty": _dirty(),
           "machine": platform.machine(), "python": platform.python_version(), "results": {}, "census": {},
           "verdicts": {}}
    for family in families:
        for split in splits:
            name = f"{family}:{split}"
            out["census"][name] = cycles_s.census(family, split)
            result = cycles_s.run_split(family, split, configs=configs)
            out["results"][name] = result
            have = result["configurations"]
            verdicts = {}
            for config in have:
                if config.startswith("fix") or config == "shipped":
                    verdicts[config] = cycles_s.verdict(result, config, "baseline") if "baseline" in have else None
                    if "baseline" in have:      # Wave 3 fix lane: the roadmap's rule beside the lane's own
                        verdicts[config + ":roadmap"] = cycles_s.roadmap_verdict(result, config, "baseline")
                elif config.startswith("supersede"):
                    verdicts[config] = cycles_s.verdict(result, config, "fixes") if "fixes" in have else None
            out["verdicts"][name] = verdicts
    out["wall_seconds"] = time.perf_counter() - started
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--family", action="append", choices=tuple(cycles_s.FAMILIES))
    r.add_argument("--split", action="append", choices=("development", "held_out"))
    r.add_argument("--config", action="append", choices=tuple(cycles_s.CONFIGS))
    r.add_argument("--out")
    a = ap.parse_args(argv)
    result = run(a.family or ["lane-s", "lane-f"], a.split or ["development", "held_out"], a.config)
    text = json.dumps(scoring.rounded(result), indent=1)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {a.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
