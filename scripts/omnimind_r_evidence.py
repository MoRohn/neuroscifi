"""Lane R's evidence (ADR-077): relating artifacts by measurement.

    PYTHONPATH=src python scripts/omnimind_r_evidence.py --out docs/quality/evidence/omnimind_r.json

Writes summaries only (no per-case rows, no artifact text):
- the cross family's DEVELOPMENT split per pair kind, the product relater beside the benchmark's references (never
  held-out, never withheld);
- lane R's own pairs (tests/artifacts/r_pairs.py: kinds the public splits lack; the mechanism was corrected against
  them, so they are development), its fresh pairs (r_pairs_fresh.py: written after commit 677cf22 froze the
  mechanism; the first look is recorded as it was, the current numbers are recomputed), and unrelated pairs whose
  right answer is "cannot";
- own variants built with the cross family's public generators and knobs chosen here (seeds from 20100, inside
  `acceptance.omni.LANE_R_SWEEP_SEEDS`, lane R's own range; not the benchmark's cases): format robustness within the
  six kinds. Until fix lane F2 they used 9100-10729, partly inside the range reserved for the withheld lane
  (review finding B3; the main session found by hashes no generated file or case equal to a withheld one);
- how many quantities and statements each representation yields, by kind of fact;
- the transfer table (units from a document applied to a bare CSV): right, wrong, absent.
"""
from __future__ import annotations

import argparse
import json
import platform
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "artifacts"))

import r_pairs as RP  # noqa: E402
import r_pairs_fresh as RF  # noqa: E402
from neuroscifi.acceptance.omni import cross, subject  # noqa: E402
from neuroscifi.acceptance.omni.cross_cases import P  # noqa: E402
from neuroscifi.artifacts import adapters, relate  # noqa: E402

FRESH_FIRST_LOOK = {"commit": "677cf22", "pairs": 12, "cases": 24, "right": 20, "cannot": 4, "wrong": 0,
                    "unrelated": {"cases": 4, "cannot": 4, "wrong": 0},
                    "cannot_kinds": ["two_logs_order", "doc_rate_wav"],
                    "then": "two_logs_order fixed (a log's format words), doc_rate_wav left as cannot"}


def _run(files, question):
    (na, da), (nb, db) = list(files.items())
    A = relate.side(adapters.decode(da, name=na), name=na, data=da)
    B = relate.side(adapters.decode(db, name=nb), name=nb, data=db)
    return relate.compare(A, B, question)


def _tally(rows):
    out = {}
    for kind, truth, answer in rows:
        t = out.setdefault(kind, {"cases": 0, "right": 0, "cannot": 0, "wrong": 0})
        t["cases"] += 1
        t["right" if answer is truth else "cannot" if answer is None else "wrong"] += 1
    total = {k: sum(v[k] for v in out.values()) for k in ("cases", "right", "cannot", "wrong")}
    return {"by_kind": out, "all": total}


def development() -> dict:
    out = {}
    subjects = {"relate (product)": subject.relate, **{name: name for name in cross.REFERENCES}}
    for label, subj in subjects.items():
        score = cross.score(subj, "development")
        out[label] = {"correct": score["correct"], "answered": score["answered"],
                      "both_twins_correct": score["both_twins_correct"],
                      "by_kind": {k: {"right": v["correct"]["k"], "cannot": v["cases"] - v["answered"]["k"],
                                      "wrong": v["answered"]["k"] - v["correct"]["k"], "cases": v["cases"]}
                                  for k, v in score["by_kind"].items()}}
    return out


def own(module) -> dict:
    rows = []
    for kind, q, m, o in module.pairs():
        rows.append((kind, True, _run(m, q).answer))
        rows.append((kind, False, _run(o, q).answer))
    un = [("unrelated", None, _run(files, q).answer) for q, files in module.unrelated()]
    unrel = {"cases": len(un), "cannot": sum(a is None for _, _, a in un), "wrong": sum(a is not None for *_, a in un)}
    return _tally(rows) | {"unrelated": unrel}


SWEEP = 20000                     # LANE_R_SWEEP_SEEDS.start: kind k uses SWEEP + 1000 k + 100 seed + i


def sweep(seeds=(1, 2, 3)) -> dict:
    rows = []
    for seed in seeds:
        rng = random.Random(seed)
        pairs = []
        for k in range(30):
            fmt = rng.choice(["md", "txt", "docx"])
            pairs.append(P(f"s{k}", "report_total", SWEEP + 100 * seed + k, "Does the report's total match the CSV?",
                           rng.choice(["csv", "stated"]), report="r." + fmt, fmt=fmt, csv="d.csv",
                           item=rng.choice(["Day", "Date", "Line", "Supplier"]),
                           amount=rng.choice(["Amount", "Cost (EUR)", "Total (USD)", "Value", "Price (GBP)"]),
                           n=rng.randint(3, 30), style=rng.choice(["grouped", "plain", "currency", "euro"]),
                           decimals=rng.choice([0, 2]),
                           sentence=rng.choice(["The total came to {total}.", "Altogether we spent {total}.",
                                                "Sum of all entries: {total}", "In all, {total} was paid."])))
        for k in range(20):
            pairs.append(P(f"c{k}", "chart_series", SWEEP + 1000 + 100 * seed + k, "Does the chart show the CSV's values?",
                           rng.choice(["svg", "csv"]), svg="c.svg", csv="c.csv",
                           series=rng.choice(["Sales", "Rainfall (mm)", "Users", "Score"]),
                           x=rng.choice(["year", "month", "day"]), n=rng.choice([5, 8, 12]),
                           mark=rng.choice(["line", "bars", "points"]), scale=rng.choice(["linear", "log"]),
                           metadata=rng.choice([True, False])))
        for k in range(15):
            f_lo = rng.choice([200, 300, 440])
            pairs.append(P(f"t{k}", "tones_rows", SWEEP + 2000 + 100 * seed + k, "Is there one tone per row?",
                           rng.choice(["table", "wav"]), wav="s.wav", csv="s.csv", column="Value",
                           n=rng.randint(3, 20), tone_s=rng.choice([0.1, 0.15, 0.3]),
                           gap_s=rng.choice([0.03, 0.05, 0.1]), f_lo=f_lo, f_hi=f_lo * rng.choice([2, 3])))
        for k in range(15):
            pairs.append(P(f"m{k}", "mesh_bbox", SWEEP + 3000 + 100 * seed + k, "Does the mesh have the README's size?",
                           rng.choice(["vertex", "readme"]), readme="README.md", obj="p.obj",
                           shape=rng.choice(["box", "wedge", "l_bracket", "cylinder"]),
                           size=(rng.choice([10, 25.5, 80, 120]), rng.choice([5, 30, 44.25]), rng.choice([3, 12, 60])),
                           corner=(rng.choice([0, -5.0, 2.5]), 0.0, rng.choice([0, -1.0])),
                           obj_unit=rng.choice(["mm", "cm", "m"]), style=rng.choice(["x", "minmax", "prose"]),
                           axis=rng.randrange(3), delta=rng.choice([1.0, 2.0, 5.0])))
        for k in range(10):
            pairs.append(P(f"k{k}", "code_trace", SWEEP + 4000 + 100 * seed + k, "Is the trace consistent with the code?",
                           rng.choice(["other_fn", "undefined"]), py="m.py", trace="t.json",
                           fmt=rng.choice(["flat", "tree"]), entry="main",
                           graph=rng.choice(["main>a,b; a>c*2,d; b>e", "main>load,run*2; run>step,log; step>log"]),
                           unused="zzz", event=rng.randint(1, 4), undefined="nope"))
        for k in range(15):
            ev = rng.choice([("valve opened", "flow started", "pressure rose", "valve closed"),
                             ("job queued", "job started", "job failed", "retry scheduled", "job finished"),
                             ("sensor 1 offline", "sensor 2 offline", "sensor 1 online", "sensor 2 online")])
            fmt = rng.choice(["md", "txt"])
            logfmt = rng.choice(["iso", "syslog", "json"])
            pairs.append(P(f"l{k}", "timeline_log", SWEEP + 5000 + 100 * seed + k, "Do they agree on the order?", "swap",
                           doc="t." + fmt, log="a.jsonl" if logfmt == "json" else "a.log", fmt=fmt, logfmt=logfmt,
                           noise=rng.randint(0, 10), swap=rng.randrange(len(ev) - 1), events=ev))
        for pair in pairs:
            for matching in (True, False):
                rows.append((pair["kind"], matching, _run(cross.build(pair, matching), pair["question"]).answer))
    return _tally(rows) | {"seeds": list(seeds), "note": "own pairs from the public generators with knobs drawn "
                                                         "here; not the benchmark's cases"}


def coverage() -> dict:
    """Facts per representation kind over every file of lane R's own pairs and the development pairs."""
    out: dict = {}
    files = []
    for kind, q, m, o in RP.pairs() + RF.pairs():
        files += list(m.items()) + list(o.items())
    for t in cross.tasks("development"):
        files += list(t.files.items())
    seen = set()
    for name, data in files:
        key = (name, hash(data))
        if key in seen:
            continue
        seen.add(key)
        try:
            s = relate.side(adapters.decode(data, name=name), name=name, data=data)
        except Exception as error:                        # recorded, never hidden
            out.setdefault("errors", []).append(f"{name}: {type(error).__name__}")
            continue
        row = out.setdefault(s.kind, {"files": 0, "quantities": {}, "statements": {}})
        row["files"] += 1
        for f in s.facts:
            side = "quantities" if isinstance(f, relate.Quantity) else "statements"
            row[side][f.kind] = row[side].get(f.kind, 0) + 1
    return out


def transfer() -> dict:
    cases, totals = [], {"columns": 0, "right_unit": 0, "right_absent": 0, "wrong": 0, "absent": 0,
                         "conflicts_reported": 0}
    for i, (doc, header, truth) in enumerate(RP.TRANSFER):
        table = adapters.decode(RP.csv(header, [[1.5] * len(header)]), name="data.csv").representation
        tr = relate.transfer_units(table, adapters.decode(doc.encode(), name="README.md").representation,
                                   source="README.md", data=doc.encode())
        row = {"case": i, "columns": 0, "right_unit": 0, "right_absent": 0, "wrong": 0, "absent": 0,
               "conflicts_reported": len(tr.conflicts)}
        for c in tr.table.columns:
            expected = truth.get(c.name, next((v for k, v in truth.items() if k.startswith(c.name + " ")), None))
            row["columns"] += 1
            if c.unit == expected:
                row["right_unit" if expected else "right_absent"] += 1
            elif c.unit is None:
                row["absent"] += 1
            else:
                row["wrong"] += 1
        cases.append(row)
        for k in totals:
            totals[k] += row[k]
    return {"cases": cases, "all": totals,
            "note": "lane R's own cases; three reader defects were fixed against them (development numbers)"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                                timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = None
    value = {"lane": "R", "adr": "ADR-077", "commit": commit, "python": platform.python_version(),
             "what": "relating artifacts by measurement: development split of the cross family (never held-out or "
                     "withheld), own pairs, fresh pairs, own generator variants, coverage of facts, transfer",
             "development": development(), "own_pairs": own(RP),
             "fresh_pairs": {"first_look": FRESH_FIRST_LOOK, "now": own(RF)},
             "own_generator_variants": sweep(), "coverage": coverage(), "transfer": transfer()}
    text = json.dumps(value, indent=1, sort_keys=True) + "\n"
    Path(a.out).write_text(text, encoding="utf-8")
    print(f"wrote {a.out} ({len(text.encode())} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
