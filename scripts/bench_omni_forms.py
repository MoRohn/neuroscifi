#!/usr/bin/env python3
"""The OMNIMIND benchmark for choosing a form, for cross-modal questions and for learning from outcomes (lane ZF).

  python scripts/bench_omni_forms.py run --family forms --split held_out --subject shape_only
  python scripts/bench_omni_forms.py run --family cross --split development --subject today --out /tmp/x.json
  python scripts/bench_omni_forms.py run --family learn --split held_out --subject mypkg.mod:make_learner
  python scripts/bench_omni_forms.py stats                      # the case tables' counts and wording statistics
  python scripts/bench_omni_forms.py baseline --evidence --out docs/quality/evidence/omnimind_baseline_forms.json

--subject is a reference of the family (`list` shows them) or `dotted.module:attribute`: for forms a chooser (an object
with choose(view) or a callable), for cross a relater (relate(files, question) or a callable), for learn a
zero-argument factory of fresh learners. Output is summaries only. --out refuses a path inside this checkout unless
--evidence is given (the evidence files are the only things this script may write there). Offline; no model;
standard library only. docs/quality/OMNIMIND_BENCHMARK_FORMS.md says what each measure cannot show.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from neuroscifi.acceptance.omni import cross, forms, learn           # noqa: E402

FAMILIES = {"forms": forms, "cross": cross, "learn": learn}


def _subject(family: str, name: str):
    module = FAMILIES[family]
    if name in module.REFERENCES or (family == "forms" and name == "first_good"):
        return name
    if ":" not in name:
        raise SystemExit(f"unknown subject {name!r} for {family} (references: {', '.join(module.REFERENCES)}; "
                         "or dotted.module:attribute)")
    mod, _, attr = name.partition(":")
    return getattr(importlib.import_module(mod), attr)


def _environment() -> dict:
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5)
        commit = head.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        commit = None
    return {"git_commit": commit, "python": platform.python_version(),
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def _refused(out: str | None, evidence: bool) -> str | None:
    if out and Path(out).resolve().is_relative_to(ROOT.resolve()) and not evidence:
        return "--out is inside this checkout: pass --evidence to write an evidence file there"
    return None


def _write(value, out):
    text = json.dumps(value, indent=1, ensure_ascii=False) + "\n"
    if out:
        Path(out).write_text(text, encoding="utf-8")
        print(f"wrote {out} ({len(text.encode('utf-8'))} bytes)")
    else:
        print(text, end="")


def stats() -> dict:
    return {"forms": {"tables": forms.table_stats(), "wording": forms.wording()},
            "cross": {split: {"pairs": len(cross.tasks(split)) // 2, "cases": len(cross.tasks(split)),
                              "generated_sha256": cross.digest(split)} for split in cross.SPLITS},
            "learn": {split: {"people": [p["id"] for p in learn.learn_people.ALL[split]],
                              "seeds": list(learn.learn_people.SEEDS[split]), "cycles": learn.learn_people.CYCLES,
                              "requests_per_cycle": learn.learn_people.PER_CYCLE} for split in learn.SPLITS}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="one family, one split, one subject")
    r.add_argument("--family", required=True, choices=sorted(FAMILIES))
    r.add_argument("--split", default="development", choices=("development", "held_out"))
    r.add_argument("--subject", default="today")
    r.add_argument("--out")
    r.add_argument("--evidence", action="store_true", help="allow --out inside this checkout")
    b = sub.add_parser("baseline", help="every reference of every family on both splits, with the table statistics")
    b.add_argument("--out")
    b.add_argument("--evidence", action="store_true", help="allow --out inside this checkout")
    sub.add_parser("stats", help="the case tables' counts and the wording statistics")
    sub.add_parser("list", help="families and their reference subjects")
    a = ap.parse_args(argv)

    if a.command == "list":
        _write({f: {name: entry[0] for name, entry in m.REFERENCES.items()} for f, m in FAMILIES.items()}, None)
        return 0
    if a.command == "stats":
        _write(stats(), None)
        return 0
    problem = _refused(a.out, a.evidence)
    if problem:
        print(f"refused: {problem}", file=sys.stderr)
        return 2
    if a.command == "baseline":
        value = {"benchmark": "omnimind-forms-cross-learn", "version": 1, "environment": _environment(),
                 "what": "the reference subjects (today's system among them) on both splits of the forms, cross and "
                         "learn families, with the case tables' statistics; summaries only",
                 "labels": {"forms": forms.LABEL, "cross": cross.LABEL, "learn": learn.LABEL},
                 "stats": stats(), "forms": forms.baseline(), "cross": cross.baseline(), "learn": learn.baseline()}
        _write(value, a.out)
        return 0
    module = FAMILIES[a.family]
    subject = _subject(a.family, a.subject)
    value = {"benchmark": "omnimind-forms-cross-learn", "family": a.family, "subject": a.subject,
             "environment": _environment(), "result": module.score(subject, a.split)}
    _write(value, a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
