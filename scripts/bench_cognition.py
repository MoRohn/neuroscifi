#!/usr/bin/env python3
"""The cognitive benchmark of Project Cognitive Genesis: anticipation, information value, counterfactual validity,
self-description, selective answering, retention, adversarial cases, and a withheld transfer family.

  python scripts/bench_cognition.py run --split held_out --out held.json             # the baseline, held-out split
  python scripts/bench_cognition.py run --family anticipation --mechanism predictive --ablate trend
  python scripts/bench_cognition.py run --family withheld --final --note "lane I, final" --out /outside/x.json
                                                        # recorded in the ledger; a clean, committed tree only
  python scripts/bench_cognition.py baseline --out docs/quality/evidence/genesis_baseline.json
  python scripts/bench_cognition.py list                                              # families and mechanisms
  python scripts/bench_cognition.py compare before.json after.json                    # headline metrics side by side

Offline and deterministic per seed (standard library and numpy; no answer model; connections outside loopback are
refused while a family runs). src/neuroscifi/acceptance/cognition/__init__.py documents how a mechanism registers;
docs/quality/COGNITIVE_BENCHMARK.md what each family measures and what it cannot show. The withheld family is scored
only with --final, and every --final run is appended to docs/quality/evidence/genesis_final_uses.jsonl.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from neuroscifi.acceptance import cognition                                 # noqa: E402


def _seeds(text):
    return tuple(int(s) for s in text.split(",") if s.strip()) if text else None


def _write(value, out):
    text = json.dumps(value, indent=1, sort_keys=False)
    if out:
        Path(out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {out}")
    else:
        print(text)


def _baseline_output(path):
    p = Path(path) if path else cognition.BASELINE
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="run families for one mechanism on one split")
    r.add_argument("--family", action="append", choices=cognition.FAMILIES,
                   help="repeatable; default: every family but withheld")
    r.add_argument("--split", default="development", choices=("development", "held_out"))
    r.add_argument("--mechanism", default="baseline")
    r.add_argument("--ablate", default="", help="comma-separated components of the mechanism switched off")
    r.add_argument("--seeds", default="", help="comma-separated seeds instead of the split's own")
    r.add_argument("--plugin", action="append", default=[], help="dotted module that registers mechanisms")
    r.add_argument("--quick", action="store_true", help="the offline corpus's quick subset (selective, retention)")
    r.add_argument("--final", action="store_true", help="allow the withheld family; recorded in the ledger")
    r.add_argument("--note", default="", help="recorded with a --final run")
    r.add_argument("--baseline", default="", help="baseline output for retention (default: the recorded one)")
    r.add_argument("--trace-memory", action="store_true",
                   help="also the peak of traced Python allocations per family (tracemalloc: several times slower)")
    r.add_argument("--out")
    b = sub.add_parser("baseline", help="the recorded baseline: every family, both splits, the references, and the "
                                        "withheld family once (--final)")
    b.add_argument("--out")
    b.add_argument("--note", default="baseline on the unchanged system")
    sub.add_parser("list", help="families and registered mechanisms")
    c = sub.add_parser("compare", help="headline metrics of two outputs")
    c.add_argument("before")
    c.add_argument("after")
    a = ap.parse_args(argv)

    if a.command == "list":
        cognition.load_plugins()
        rows = [{"family": m.family, "name": m.name, "components": list(m.components), "reference": m.reference,
                 "needs_model": m.needs_model, "description": m.description} for m in cognition.mechanisms()]
        _write({"families": list(cognition.FAMILIES), "mechanisms": rows}, None)
        return 0
    if a.command == "compare":
        before = json.loads(Path(a.before).read_text(encoding="utf-8"))
        after = json.loads(Path(a.after).read_text(encoding="utf-8"))
        _write(cognition.compare(before, after), None)
        return 0
    if a.command == "baseline":
        out = {"benchmark": "cognition-baseline", "what": "the unchanged system (start commit of the genesis lanes) "
               "on both splits of every family, the reference policies, and the withheld family once"}
        for split in ("development", "held_out"):
            out[split] = cognition.run(split=split)
            out[f"{split}_references"] = {
                name: cognition.run([fam], split=split, mechanism=name)["families"][fam]
                for fam, name in (("info_value", "oracle"), ("info_value", "random"), ("info_value", "autonomy"),
                                  ("anticipation", "open"))}
        out["withheld"] = cognition.run(["withheld"], final=True, note=a.note)
        _write(out, a.out)
        return 0
    if a.final:     # review3 (Q7): a look is written outside the checkout (a new file there would make it dirty)
        root = Path(__file__).resolve().parents[1]
        if not a.out or Path(a.out).resolve().is_relative_to(root.resolve()):
            print("refused: a --final run writes --out to a path OUTSIDE this checkout (copy it in afterwards)",
                  file=sys.stderr)
            return 2
    cognition.load_plugins(a.plugin)
    families = a.family or list(cognition.DEFAULT_FAMILIES)
    try:
        value = cognition.run(families, split=a.split, mechanism=a.mechanism, ablate=a.ablate.split(","),
                              seeds=_seeds(a.seeds), quick=a.quick, final=a.final, note=a.note,
                              baseline_output=_baseline_output(a.baseline), trace_memory=a.trace_memory)
    except cognition.Refused as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    _write(value, a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
