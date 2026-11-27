#!/usr/bin/env python3
"""The OMNIMIND benchmark for choosing a form, for cross-modal questions and for learning from outcomes (lane ZF).

  python scripts/bench_omni_forms.py run --family forms --split held_out --subject shape_only
  python scripts/bench_omni_forms.py run --family cross --split development --subject today --out /tmp/x.json
  python scripts/bench_omni_forms.py run --family learn --split held_out --subject mypkg.mod:make_learner
  python scripts/bench_omni_forms.py stats                      # the case tables' counts and wording statistics
  python scripts/bench_omni_forms.py baseline --evidence --out docs/quality/evidence/omnimind_baseline_forms.json
  python scripts/bench_omni_forms.py final --family forms --subject mypkg.mod:choose --note TEXT --out /outside.json

--subject is a reference of the family (`list` shows them) or `dotted.module:attribute`: for forms a chooser (an object
with choose(view) or a callable), for cross a relater (relate(files, question) or a callable), for learn a
zero-argument factory of fresh learners. Output is summaries only. --out refuses a path inside this checkout unless
--evidence is given (the evidence files are the only things this script may write there). Offline; no model;
standard library only. docs/quality/OMNIMIND_BENCHMARK_FORMS.md says what each measure cannot show.

`final` is THE look at the withheld split (acceptance/omni/final.py; one look per mechanism, by the main session, at
the end): it refuses a missing or blank note, an --out inside this checkout or not writable, another tree's source, a
dirty or uncommitted tree and a changed withheld file; then it appends one line per family to
docs/quality/evidence/omnimind_final_uses.jsonl BEFORE it loads or scores anything, and writes summaries only.
`run` refuses the withheld split: only `final` loads it. `final --split withheld2 --family cross` is the second
withheld split's cross family (lane ZW2, acceptance/omni/withheld2_cross.py), with the same refusals and ledger; `run`
refuses it too. `final --split withheld3 --family cross` is the third (lane ZW3, acceptance/omni/withheld3_cross.py);
`run` refuses it. The first two splits are spent for cross (opened by the owner on 2026-11-27): `final` refuses them
and records the refusal in the ledger.
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


def _final(a) -> int:
    from neuroscifi.acceptance.omni import final
    problem = final.out_problem(a.out, ROOT)
    if problem is None and final.REPO.resolve() != ROOT.resolve():
        problem = "run it on this checkout's own source, so the ledger line is written here and names this tree"
    if problem:
        print(f"refused: {problem}", file=sys.stderr)
        return 2
    families = tuple(dict.fromkeys(a.family))
    if a.split in ("withheld2", "withheld3") and families != ("cross",):
        which = "second" if a.split == "withheld2" else "third"
        print(f"refused: the {which} withheld split has only the family cross here (unknown and hostile are in "
              "bench_omni.py)", file=sys.stderr)
        return 2
    if ":" in a.subject and len(families) > 1:
        problem = "a product subject is looked at one family at a time"
    elif ":" not in a.subject and any(a.subject not in FAMILIES[f].REFERENCES for f in families):
        problem = f"{a.subject!r} is not a reference of every family named (or use dotted.module:attribute)"
    if problem:
        print(f"refused: {problem}", file=sys.stderr)
        return 2
    subject = _subject(families[0], a.subject) if ":" in a.subject else a.subject
    try:
        value = final.look(families, subject, a.subject, a.note, script="bench_omni_forms.py final", split=a.split)
    except final.Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    value |= {"benchmark": "omnimind-forms-cross-learn", "python": platform.python_version()}
    _write(value, a.out)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="one family, one split, one subject")
    r.add_argument("--family", required=True, choices=sorted(FAMILIES))
    r.add_argument("--split", default="development", choices=("development", "held_out", "withheld", "withheld2",
                                                              "withheld3"))
    r.add_argument("--subject", default="today")
    r.add_argument("--out")
    r.add_argument("--evidence", action="store_true", help="allow --out inside this checkout")
    b = sub.add_parser("baseline", help="every reference of every family on both splits, with the table statistics")
    b.add_argument("--out")
    b.add_argument("--evidence", action="store_true", help="allow --out inside this checkout")
    sub.add_parser("stats", help="the case tables' counts and the wording statistics")
    f = sub.add_parser("final", help="THE look at the withheld split (recorded in the ledger first)")
    f.add_argument("--family", action="append", required=True, choices=sorted(FAMILIES))
    f.add_argument("--subject", required=True, help="a reference of the family, or dotted.module:attribute")
    f.add_argument("--note", required=True, help="the reason for the look (the ledger line's note)")
    f.add_argument("--out", required=True, help="a path OUTSIDE this checkout")
    f.add_argument("--split", default="withheld", choices=("withheld", "withheld2", "withheld3"),
                   help="which withheld split (withheld2: lane ZW2's cross pairs, spent; withheld3: lane ZW3's)")
    sub.add_parser("list", help="families and their reference subjects")
    a = ap.parse_args(argv)

    if a.command == "list":
        _write({f: {name: entry[0] for name, entry in m.REFERENCES.items()} for f, m in FAMILIES.items()}, None)
        return 0
    if a.command == "stats":
        _write(stats(), None)
        return 0
    if a.command == "final":
        return _final(a)
    if a.command == "run" and a.split in ("withheld", "withheld2", "withheld3"):
        print("refused: the withheld split is scored only by `final`, once per mechanism, and every look is recorded "
              "in the ledger first (docs/quality/OMNIMIND_BENCHMARK_FORMS.md)", file=sys.stderr)
        return 2
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
