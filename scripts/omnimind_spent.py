"""The SPENT withheld cases of three families, run as development cases (lane FX3, Wave O-3; ADR-081).

The owner decided on 2026-11-27 ("Open spent cases, fix") that the two withheld splits that were already looked at may
be opened FOR THE FAMILIES unknown, hostile and cross ONLY and used as development cases. This script runs the product
on exactly those cases, through the same benchmark functions the looks used (`acceptance/omni/final.py`'s scoring
path, without `final` itself: nothing here writes the ledger, checks pins or looks at anything unseen), and prints
one line per case: split, family, case, class or kind, expected, got, outcome.

    PYTHONPATH=src python scripts/omnimind_spent.py [--split withheld2|withheld|all] [--family unknown|hostile|cross|all]
                                                    [--case TEXT] [--json OUT]

Files opened (and only these):
- `acceptance/omni/withheld2_unknown_hostile.py` (second split: unknown and hostile; nothing else is in it);
- `acceptance/omni/withheld2_cross.py` (second split: cross);
- `acceptance/omni/withheld_cross.py` (first split: cross).
NOT opened: `withheld_cases.py` holds all seven of lane Z's families of the first split (intake, honest, compound,
made and transform beside unknown and hostile; docs/quality/OMNIMIND_BENCHMARK.md, "The withheld split as written"),
so the first split's unknown and hostile cases are not run here (WAVE3.md: a file that also holds another family's
cases is not opened). `withheld_forms.py`, `withheld_learn.py` and every `withheld3*` file are never touched.

How each family is run (the looks' path, `final.score_z` / `final.score_cross2` / `final.score_cross`):
- unknown: `omni.run_tasks` with the product subject `subject.get("artifacts")` (in this process, as the look; this
  process's address space is capped at ADDRESS_SPACE and `run_in_process` stops a task at 60 s);
- hostile: each task in its own child process through `isolate.run_isolated` (address-space cap, traced memory,
  `hostile.SECONDS` timeout, escape check), judged by `hostile.judge`;
- cross: `subject.relate(files, question)`; an answer counts when it is a bool; right when it IS the case's
  `expect` (second split: the truth where the kind is decidable, None on both twins where it is not) or its `truth`
  (first split, which has no undecidable kind).

Hostile columns: each answered hostile case is put in one of (a) a real failure, (b) the case expects a refusal only
because the file refers outside itself or carries active content (the owner's rule 2 of 2026-11-27: read with a plain
note, never refused for that alone; counted here in its own column, its expected answer NOT edited), (c) the
examination's truth is itself wrong (reported, not edited). RULE2 and TRUTH_DOUBT below say which constructions are
(b) and (c) and why; everything else answered is (a).

These are development numbers once a fix has seen the cases: a regression run, never a look.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import resource
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ADDRESS_SPACE = 8 << 30          # soft cap on this process (hostile children set their own, isolate.ADDRESS_SPACE)

# (b): constructions whose ONLY hostile property is an outside reference or embedded active content (owner's rule 2).
# Keyed by the construction's attack name, with the reason; an empty mapping means none of the spent cases is (b).
RULE2: dict[str, str] = {}
# (c): constructions whose expected answer is itself wrong (reported, never edited).
TRUTH_DOUBT: dict[str, str] = {}


def _cap_memory() -> None:
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    if soft == resource.RLIM_INFINITY or soft > ADDRESS_SPACE:
        resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE, hard))


def _short(value, n: int = 90) -> str:
    text = json.dumps(value, default=str) if not isinstance(value, str) else value
    return text if len(text) <= n else text[:n - 3] + "..."


class _Recording:
    """The product subject, recording the answer it gave to each task (run_tasks keeps only the outcome)."""

    def __init__(self, inner):
        self.inner, self.name, self.answers = inner, inner.name, {}

    def answer(self, task):
        answer = self.inner.answer(task)
        self.answers[task.id] = answer
        return answer


# ---- the families --------------------------------------------------------------------------------------------------

def run_unknown(case_filter: str = "") -> list[dict]:
    from neuroscifi.acceptance import omni
    from neuroscifi.acceptance.omni import subject
    from neuroscifi.acceptance.omni import withheld2_unknown_hostile as W
    cases = {c["name"]: c for c in W.DISGUISED + W.TRULY_UNKNOWN}
    tasks = [t for t in W.tasks("unknown") if case_filter in t.id]
    product = _Recording(subject.get("artifacts"))
    rows = []
    for task, result in omni.run_tasks("unknown", W.SPLIT, product, tasks):
        name = task.id.split("/")[2]
        case = cases[name]
        answer = product.answers.get(task.id)
        cls = ("disguised:" + case["gen"] + ":" + case["wrap"]) if case["kind"] == "disguised" \
            else ("truly_unknown:" + case["make"])
        rows.append({"split": "withheld2", "family": "unknown", "case": name, "question": task.question,
                     "class": cls, "kind": task.kind, "expected": task.expect,
                     "got": None if answer is None else answer.value,
                     "status": None if answer is None else answer.status,
                     "reason": "" if answer is None else answer.reason[:160],
                     "outcome": result.outcome, "detail": result.detail.get("failure", "")})
    return rows


def run_hostile(case_filter: str = "", scratch: str | None = None) -> list[dict]:
    from neuroscifi.acceptance.omni import hostile
    from neuroscifi.acceptance.omni import withheld2_unknown_hostile as W
    from neuroscifi.acceptance.omni.isolate import run_isolated
    by_name = {c["name"]: c for c in W.HOSTILE}
    rows = []
    with tempfile.TemporaryDirectory(prefix="omni-spent-hostile-", dir=scratch) as root:
        for task, escapes in W.hostile_tasks():
            if case_filter not in task.id:
                continue
            report = run_isolated(task, "artifacts", Path(root), seconds=hostile.SECONDS, escape_paths=escapes)
            result = hostile.judge(task, report)
            name = task.id.split("/")[2]
            case = by_name[name]
            column = ""
            if result.outcome == "wrong":
                column = "b" if case["attack"] in RULE2 else ("c" if case["attack"] in TRUTH_DOUBT else "a")
            rows.append({"split": "withheld2", "family": "hostile", "case": name, "question": task.question,
                         "class": case["class"], "attack": case["attack"],
                         "public_class": case["class"] in W.PUBLIC_CLASSES, "expected": "refused",
                         "got": report.get("value"), "status": report.get("status") or report.get("failure"),
                         "reason": str(report.get("reason", ""))[:160], "outcome": result.outcome,
                         "detail": result.detail.get("failure", ""), "column": column,
                         "seconds": report.get("seconds"), "peak_traced": report.get("peak_traced")})
    return rows


def _cross_rows(split: str, tasks, decidable=None, case_filter: str = "") -> list[dict]:
    from neuroscifi.acceptance.omni import subject
    rows = []
    for t in tasks:
        if case_filter not in t.id:
            continue
        reply = subject.relate(dict(t.files), t.question) or {}
        answer = reply.get("answer")
        answer = answer if isinstance(answer, bool) else None
        expect = getattr(t, "expect", t.truth)
        if answer is None:
            outcome = "right" if expect is None else "cannot"
        else:
            outcome = "right" if answer is expect else "wrong"
        rows.append({"split": split, "family": "cross", "case": t.id, "kind": t.kind, "question": t.question,
                     "truth": t.truth, "expected": expect,
                     "decidable": True if decidable is None else decidable[t.kind][0],
                     "got": answer, "outcome": outcome,
                     "because": [str(b)[:200] for b in (reply.get("because") or [])][:4]})
    return rows


def run_cross2(case_filter: str = "") -> list[dict]:
    from neuroscifi.acceptance.omni import withheld2_cross as WX
    return _cross_rows("withheld2", WX.tasks(), WX.DECIDABLE, case_filter)


def run_cross1(case_filter: str = "") -> list[dict]:
    from neuroscifi.acceptance.omni import withheld_cross as WX
    return _cross_rows("withheld", WX.tasks(), None, case_filter)


# ---- totals ------------------------------------------------------------------------------------------------------------

def totals(rows: list[dict]) -> dict:
    out = {}
    for key in sorted({(r["split"], r["family"]) for r in rows}):
        sel = [r for r in rows if (r["split"], r["family"]) == key]
        count = Counter(r["outcome"] for r in sel)
        block = {"n": len(sel), "right": count["right"], "cannot": count["cannot"] + count["abstained"],
                 "wrong": count["wrong"], "failed": count["failed"]}
        if key[1] == "hostile":
            cols = Counter(r["column"] for r in sel if r["outcome"] == "wrong")
            block["wrong_by_column"] = {"a": cols["a"], "b": cols["b"], "c": cols["c"]}
        if key[1] == "cross":
            answered = [r for r in sel if r["got"] is not None]
            block["answered"] = len(answered)
            dec = [r for r in sel if r["decidable"]]
            block["decidable"] = {"cases": len(dec), "right": sum(r["outcome"] == "right" for r in dec),
                                  "answered": sum(r["got"] is not None for r in dec)}
            block["undecidable"] = {"cases": len(sel) - len(dec),
                                    "cannot": sum(r["got"] is None for r in sel if not r["decidable"])}
            by_kind = {}
            for r in sel:
                k = by_kind.setdefault(r["kind"], Counter())
                k[r["outcome"]] += 1
            block["by_kind"] = {k: dict(v) for k, v in by_kind.items()}
            pairs = {}
            for r in sel:
                pairs.setdefault(r["case"][:-1], []).append(r["outcome"] == "right")
            block["both_twins_right"] = sum(all(v) for v in pairs.values())
            block["pairs"] = len(pairs)
        out[f"{key[0]}/{key[1]}"] = block
    return out


def _print(rows: list[dict]) -> None:
    for r in rows:
        if r["family"] == "cross":
            print(f"{r['split']:9} cross   {r['case']:7} {r['kind']:19} expected={_short(r['expected']):5} "
                  f"got={_short(r['got']):5} {r['outcome']:7} | {_short('; '.join(r['because']), 140)}")
        elif r["family"] == "hostile":
            print(f"{r['split']:9} hostile {r['case']:42} {r['class']:32} expected=refused got={r['status']} "
                  f"{r['outcome']:9} {r['column']} | {_short(r['reason'], 100)}")
        else:
            print(f"{r['split']:9} unknown {r['case']:38} {r['question']:26} expected={_short(r['expected'], 40)} "
                  f"got={r['status']}:{_short(r['got'], 40)} {r['outcome']} | {_short(r['reason'], 80)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--split", choices=("withheld2", "withheld", "all"), default="all")
    ap.add_argument("--family", choices=("unknown", "hostile", "cross", "all"), default="all")
    ap.add_argument("--case", default="", help="only cases whose id contains this text")
    ap.add_argument("--json", help="write rows and totals here")
    ap.add_argument("--quiet", action="store_true", help="totals only")
    ap.add_argument("--scratch", help="parent directory for the hostile children's working folders")
    args = ap.parse_args(argv)
    _cap_memory()
    fams = ("unknown", "hostile", "cross") if args.family == "all" else (args.family,)
    rows: list[dict] = []
    if args.split in ("withheld2", "all"):
        if "unknown" in fams:
            rows += run_unknown(args.case)
        if "hostile" in fams:
            rows += run_hostile(args.case, args.scratch)
        if "cross" in fams:
            rows += run_cross2(args.case)
    if args.split in ("withheld", "all") and "cross" in fams:
        rows += run_cross1(args.case)
    if not args.quiet:
        _print(rows)
    summary = totals(rows)
    print(json.dumps(summary, indent=1, sort_keys=True))
    if args.json:
        Path(args.json).write_text(json.dumps({"label": "spent withheld cases (development since 2026-11-27)",
                                               "totals": summary, "rows": rows}, indent=1, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
