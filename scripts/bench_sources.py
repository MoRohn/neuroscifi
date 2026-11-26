#!/usr/bin/env python3
"""The source-independence family (Wave 4 step 0): which pages count as independent sources, measured against a
derivation graph written before any rule.

  python scripts/bench_sources.py run --split development                      # choose and debug here
  python scripts/bench_sources.py run --split held_out --out held.json          # report here, once per rule
  python scripts/bench_sources.py run --split development --config shipped      # one configuration
  python scripts/bench_sources.py final --note TEXT --out /outside/the/tree.json  # THE look (below)

Configurations (sources.CONFIGS): `shipped` (every setting as a new individual has it) and each opt-in source rule
alone on it (`attribution_independence`, `echo_recognition`, `origin_grouping:one_way`). The references
(sources.REFERENCES) are computed beside every run. Offline and deterministic (apart from wall seconds); no model is
called and no connection is made. Metrics and references: src/neuroscifi/acceptance/cognition/sources.py.

`final` scores the WITHHELD source-independence worlds (sources_withheld.py), and nothing else does: one look per run.
It refuses, before its ledger line, another tree's source, an --out inside this checkout or one it cannot write
(missing or unwritable directory, a directory), a blank note, an unknown configuration, a dirty or unverifiable tree
and a changed worlds file; then it appends its line to docs/quality/evidence/genesis_final_uses.jsonl before it loads
or scores anything. Default configuration: `shipped` (and the references).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from neuroscifi.acceptance.cognition import scoring, sources        # noqa: E402


def _commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _dirty():
    """Whether the tree has changes not committed (review4 L4); None when git cannot say."""
    try:
        return bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True,
                                   check=True, timeout=60).stdout.strip())
    except (OSError, subprocess.SubprocessError):
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
    r.add_argument("--split", choices=sources.SPLITS, required=True)
    r.add_argument("--config", action="append", help=f"repeatable; default every one of {', '.join(sources.CONFIGS)}")
    r.add_argument("--out")
    f = sub.add_parser("final")
    f.add_argument("--note", required=True)
    f.add_argument("--out", required=True, help="a path OUTSIDE this checkout; copy it in afterwards")
    f.add_argument("--config", action="append", help=f"repeatable; default {', '.join(sources.FINAL_CONFIGS)}")
    f.add_argument("--mechanism", default="baseline", help="the name the ledger line records")
    a = ap.parse_args(argv)
    unknown = sorted(set(a.config or ()) - set(sources.CONFIGS))
    if unknown:
        raise SystemExit(f"unknown configuration(s): {', '.join(unknown)}; known: {', '.join(sources.CONFIGS)}")
    if a.command == "final":
        return final(a)
    started = time.perf_counter()
    out = {"label": "Wave 4 step 0, source independence: simulated worlds of fictional software releases with a "
                    "derivation graph, read through the real modules; offline; no model; search, fetching and the "
                    "owner substituted",
           "commit": _commit(), "dirty": _dirty(),
           "environment": {"python": platform.python_version(), "machine": platform.machine()}}
    out |= sources.run_split(a.split, configs=a.config)
    out["wall_seconds"] = time.perf_counter() - started
    _write(out, a.out)
    return 0


def final(a) -> int:
    """The look at the withheld worlds (sources.final), refused here before anything is recorded when it would not
    name this tree or would write into it."""
    from neuroscifi.acceptance import cognition
    if cognition.REPO.resolve() != ROOT.resolve():
        raise SystemExit("final: run it on this checkout's own source (not an installed copy), so the ledger line is "
                         "written here and names this tree")
    if Path(a.out).resolve().is_relative_to(ROOT.resolve()):
        raise SystemExit("final: write --out outside this checkout and copy it in afterwards: a new file in the tree "
                         "would make the tree dirty")
    out = Path(a.out)
    if not out.parent.is_dir() or not os.access(out.parent, os.W_OK) or out.is_dir() or (
            out.exists() and not os.access(out, os.W_OK)):
        raise SystemExit(f"final: --out {a.out} cannot be written (its directory must exist and be writable)")
    started = time.perf_counter()
    try:
        value = sources.final(a.note, configs=a.config, mechanism=a.mechanism)
    except cognition.Refused as exc:
        raise SystemExit(str(exc)) from None
    result = {"label": "the withheld source-independence worlds (Wave 4 step 0): simulated worlds of fictional "
                       "software releases with a derivation graph, read through the real modules; offline; no model",
              "commit": _commit(), "environment_short": {"python": platform.python_version(),
                                                         "machine": platform.machine()}}
    result |= value
    result["wall_seconds"] = time.perf_counter() - started
    _write(result, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
