#!/usr/bin/env python3
"""Wave 4 lane D (ADR-065): does the caution shown to the owner (webcortex/caution.py, display only) line up with wrong
answers? Measured on the cognitive benchmark's selective family (splits.SELECTIVE, development and held-out).

  python scripts/bench_caution.py --out docs/quality/evidence/genesis_wave4_d_caution.json

The offline Web Cortex benchmark (the real pipeline against the fictional corpus on loopback; no internet, no answer
model) is run once at the shipped defaults; for every question its report's caution is computed as the console would
show it (for a fact check: the verdict's supporting claims; for any other question: the top quoted answer's), and the
item's correctness is the selective family's (selective.corpus_run: the verification state equals the gold state; the
top quote contains the gold answer).

Written before it was run (the rule, unchanged afterwards): per split, the 2x2 counts (caution shown or not, answer
right or wrong), the wrong share with and without the caution, and the share of wrong answers that carried one. The
caution "lines up" on a split when the wrong share with it is higher than without it; with about twenty items per
split this is a description of these items, not a claim. Beside it, the reference that needs no new code: a caution
whenever fewer than two independent sources support the answer (the report's own count). The own-answer flag is not
exercised here: no answer model runs, so the individual has no answers of its own to repeat. Nothing here changes an
answer, a state or a default: the caution is display only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def run() -> dict:
    from neuroscifi.acceptance.cognition import splits
    from neuroscifi.webcortex import caution
    from neuroscifi.webcortex.bench import corpus as C
    from neuroscifi.webcortex.bench import offline

    shown: dict = {}

    class _Cautioned(offline._Bench):
        def questions(self):
            recs = super().questions()
            for qid, rec in recs.items():
                report = rec["report"] or {}
                view = caution.for_report(rec["db"], rec["task_id"], report)
                if report.get("verdict", {}).get("state"):
                    c = view["verdict"]
                    groups = (report["verdict"].get("independent_groups") or {}).get("supporting")
                else:
                    quotes = [i for i, a in enumerate(report.get("answer") or []) if a.get("type") == "quote"]
                    c = view["answer"][quotes[0]] if quotes else None
                    groups = report["answer"][quotes[0]].get("independent_sources") if quotes else None
                shown[qid] = {"caution": c, "supporting_groups": groups}
            return recs

    web = C.build_web().start()
    web.server.handle_error = lambda request, client_address: None
    try:
        with tempfile.TemporaryDirectory(prefix="nsf-caution-") as directory:
            bench = _Cautioned(Path(directory), web, False)
            try:
                metrics = bench.execute()["deterministic"]
            finally:
                bench.close()
    finally:
        web.stop()
    correct = {i["id"]: bool(i["correct"]) for i in metrics["verification"]["items"]}
    correct |= {i["id"]: bool(i["answer_correct_top"]) for i in metrics["authoritative"]["items"]}
    out = {"label": "Wave 4 lane D: the owner's caution (display only) against wrong answers; the offline research "
                    "corpus (fictional, loopback, no model), the selective family's items", "commit": _commit(),
           "environment": {"python": platform.python_version(), "machine": platform.machine()},
           "rule": "lines up on a split when the wrong share with a caution is higher than without one (written before "
                   "the run; about twenty items per split: a description, not a claim)",
           "splits": {}}
    for split, spec in splits.SELECTIVE.items():
        rows = []
        for qid in spec["questions"]:
            if qid not in correct or qid not in shown:
                rows.append({"id": qid, "scored": False})
                continue
            c = shown[qid]["caution"]
            groups = shown[qid]["supporting_groups"]
            rows.append({"id": qid, "scored": True, "wrong": not correct[qid], "caution": c is not None,
                         "facts": None if c is None else {k: c[k] for k in ("pages", "groups", "joined",
                                                                           "own_answer")},
                         "reference_fewer_than_two_sources": not (isinstance(groups, int) and groups >= 2)})
        out["splits"][split] = _table(rows)
    return out


def _table(rows) -> dict:
    scored = [r for r in rows if r["scored"]]

    def cell(key, value):
        sel = [r for r in scored if r[key] is value]
        wrong = sum(1 for r in sel if r["wrong"])
        return {"n": len(sel), "wrong": wrong, "wrong_share": round(wrong / len(sel), 4) if sel else None}
    out = {"items": len(rows), "scored": len(scored), "wrong": sum(1 for r in scored if r["wrong"])}
    for key, name in (("caution", "caution"), ("reference_fewer_than_two_sources", "reference")):
        on, off = cell(key, True), cell(key, False)
        out[name] = {"shown": on, "not_shown": off,
                     "wrong_answers_with_it": f"{on['wrong']} of {out['wrong']}",
                     "lines_up": None if on["wrong_share"] is None or off["wrong_share"] is None
                     else on["wrong_share"] > off["wrong_share"]}
    out["rows"] = rows
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    started = time.perf_counter()
    value = run()
    value["wall_seconds"] = round(time.perf_counter() - started, 1)
    text = json.dumps(value, indent=1)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {a.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
