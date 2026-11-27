#!/usr/bin/env python3
"""The OMNIMIND benchmark (lane Z): run a subject on a split, print the frozen-task hashes, list what is measured.

    bench_omni.py run --split development|held_out|both [--subject today|artifacts] [--family F ...] --out FILE
                      [--evidence] [--scratch DIR]
    bench_omni.py hash [--split development|held_out]
    bench_omni.py list
    bench_omni.py final --family F [--family F ...] --subject today|artifacts --note TEXT --out FILE [--scratch DIR]
                        [--split withheld|withheld2]
    bench_omni.py pin-driver

`run` writes a summary (per family: counts with denominators, the fabrication count on its own, per-task outcomes,
cost) and refuses to write inside the repository unless --evidence is given (the recorded baseline is
docs/quality/evidence/omnimind_baseline.json). Hostile tasks run each in their own process under a scratch
directory (--scratch, default the system's temporary directory). docs/quality/OMNIMIND_BENCHMARK.md explains the
families, the measures, the outcomes and the rules for the withheld split.

`final` is THE look at the withheld split (acceptance/omni/final.py; one look per mechanism, by the main session, at
the end): it refuses a missing or blank note, an --out inside this checkout or not writable, another tree's source, a
dirty or uncommitted tree, a changed withheld file and a driver (acceptance/omni/subject.py,
scripts/omni_forms_driver.py) that differs from its pin in docs/quality/evidence/omnimind_driver_pins.json; then it
appends one line per family to
docs/quality/evidence/omnimind_final_uses.jsonl BEFORE it loads or scores anything, and writes summaries only.
`run` refuses the withheld split: only `final` loads it. `final --split withheld2` is the second withheld split (lane
ZW2: new cases of the families `unknown` and `hostile`; acceptance/omni/withheld2_unknown_hostile.py), with the same
refusals and ledger; `run` refuses it too.

`pin-driver` prints that pin file's content with the drivers' current SHA-256 and the commit; it writes nothing. The
main session redirects it into docs/quality/evidence/omnimind_driver_pins.json and commits it deliberately, once the
driver is final, before a look.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from neuroscifi.acceptance import omni  # noqa: E402
from neuroscifi.acceptance.omni import cases, subject  # noqa: E402


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _totals(families: dict) -> dict:
    keys = ("n", "right", "abstained", "wrong", "failed", "abstention_floor")
    out = {k: sum(f[k] for f in families.values()) for k in keys}
    out["score"] = round(out["right"] / out["n"], 4) if out["n"] else None
    out["fabrication"] = out["wrong"]
    return out


def cmd_run(args) -> int:
    if args.split in ("withheld", "withheld2"):
        print("refused: the withheld split is scored only by `final`, once per mechanism, and every look is recorded "
              "in the ledger first (docs/quality/OMNIMIND_BENCHMARK.md)", file=sys.stderr)
        return 2
    out_path = Path(args.out).resolve()
    if REPO in out_path.parents and not args.evidence:
        print(f"refused: {out_path} is inside the repository; pass --evidence to record it there", file=sys.stderr)
        return 2
    families = tuple(args.family) if args.family else omni.FAMILIES
    for name in families:
        omni.family(name)
    splits = omni.SPLITS if args.split == "both" else (args.split,)
    subj = subject.get(args.subject)
    available = subj.available() if hasattr(subj, "available") else (True, "")
    report = {"label": omni.LABEL, "subject": subj.name, "subject_available": list(available),
              "commit": _commit(), "python": platform.python_version(), "machine": platform.machine(),
              "recorded": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
              "cases_sha256": hashlib.sha256(Path(cases.__file__).read_bytes()).hexdigest(), "splits": {}}
    for split in splits:
        start = time.perf_counter()
        results = {}
        for name in families:
            fam = omni.family(name)
            results[name] = fam.score(subj, split, scratch=args.scratch) if name == "hostile" else \
                fam.score(subj, split)
            r = results[name]
            print(f"{split:12s} {name:10s} n={r['n']:4d} right={r['right']:4d} abstained={r['abstained']:4d} "
                  f"wrong={r['wrong']:3d} failed={r['failed']:3d} ({r['wall_s']:.1f}s)", flush=True)
        report["splits"][split] = {"families": results, "totals": _totals(results),
                                   "frozen": omni.digests(split, families),
                                   "wall_s": round(time.perf_counter() - start, 1)}
    text = json.dumps(report, indent=1, sort_keys=True) + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text)
    print(f"wrote {out_path} ({len(text.encode())} bytes)")
    return 0


def cmd_hash(args) -> int:
    print(f"cases.py sha256 {hashlib.sha256(Path(cases.__file__).read_bytes()).hexdigest()}")
    for split in (omni.SPLITS if args.split is None else (args.split,)):
        for name, d in omni.digests(split).items():
            print(f"{split:12s} {name:10s} n={d['n']:4d} {d['sha256']}")
    return 0


def cmd_list(args) -> int:
    for name in omni.FAMILIES:
        doc = (omni.family(name).__doc__ or "").strip().splitlines()[0]
        print(f"{name:10s} {doc}")
    print()
    for measure, meaning in subject.MEASURES.items():
        print(f"  {measure:30s} {meaning}")
    return 0


def cmd_final(args) -> int:
    from neuroscifi.acceptance.omni import final
    problem = final.out_problem(args.out, REPO)
    if problem is None and final.REPO.resolve() != REPO.resolve():
        problem = "run it on this checkout's own source, so the ledger line is written here and names this tree"
    if problem:
        print(f"refused: {problem}", file=sys.stderr)
        return 2
    second = ("unknown", "hostile")
    if args.split == "withheld2":
        families = second if "all" in args.family else tuple(dict.fromkeys(args.family))
        if any(f not in second for f in families):
            print("refused: the second withheld split has only the families unknown and hostile here (cross is in "
                  "bench_omni_forms.py)", file=sys.stderr)
            return 2
    else:
        families = omni.FAMILIES if "all" in args.family else tuple(dict.fromkeys(args.family))
    try:
        value = final.look(families, subject.get(args.subject), args.subject, args.note, script="bench_omni.py final",
                           scratch=args.scratch, split=args.split)
    except final.Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    value |= {"python": platform.python_version(), "machine": platform.machine()}
    text = json.dumps(value, indent=1, sort_keys=True) + "\n"
    Path(args.out).write_text(text)
    for name, r in value["families"].items():
        print(f"{args.split} {name:10s} n={r['n']:4d} right={r['right']:4d} abstained={r['abstained']:4d} "
              f"wrong={r['wrong']:3d} failed={r['failed']:3d}", flush=True)
    print(f"wrote {args.out} ({len(text.encode())} bytes); ledger lines now {value['ledger_lines']}")
    return 0


def cmd_pin_driver(args) -> int:
    from neuroscifi.acceptance.omni import final
    print(json.dumps(final.pin_document(REPO, _commit()), indent=1, sort_keys=True))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--split", choices=(*omni.SPLITS, "both", "withheld", "withheld2"), required=True)
    run.add_argument("--subject", default="today", choices=tuple(subject.SUBJECTS))
    run.add_argument("--family", action="append", choices=omni.FAMILIES)
    run.add_argument("--out", required=True)
    run.add_argument("--evidence", action="store_true", help="allow writing inside the repository")
    run.add_argument("--scratch", default=None, help="directory for the hostile family's per-task folders")
    run.set_defaults(func=cmd_run)
    h = sub.add_parser("hash")
    h.add_argument("--split", choices=omni.SPLITS)
    h.set_defaults(func=cmd_hash)
    lst = sub.add_parser("list")
    lst.set_defaults(func=cmd_list)
    fin = sub.add_parser("final", help="THE look at the withheld split (recorded in the ledger first)")
    fin.add_argument("--family", action="append", required=True, choices=(*omni.FAMILIES, "all"))
    fin.add_argument("--subject", required=True, choices=tuple(subject.SUBJECTS))
    fin.add_argument("--note", required=True, help="the reason for the look (the ledger line's note)")
    fin.add_argument("--out", required=True, help="a path OUTSIDE this checkout")
    fin.add_argument("--scratch", default=None, help="directory for the hostile family's per-task folders")
    fin.add_argument("--split", default="withheld", choices=("withheld", "withheld2"),
                     help="which withheld split (withheld2: lane ZW2's unknown and hostile)")
    fin.set_defaults(func=cmd_final)
    pin = sub.add_parser("pin-driver", help="print the drivers' pin file with their current SHA-256 (writes nothing)")
    pin.set_defaults(func=cmd_pin_driver)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
