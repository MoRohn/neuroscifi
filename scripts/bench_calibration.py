#!/usr/bin/env python3
"""Does mapping a forecast through its own settled history help? (ADR-046, evolution.fit_recalibration)

  python scripts/bench_calibration.py report --out calibration.json     # the shipped settings, both sets of streams
  python scripts/bench_calibration.py select --out selection.json       # every setting of the grid, development only
  python scripts/bench_calibration.py change --out change.json          # a change of habits (ADR-048): select, gate

Deterministic (seeded, standard library only), no model, no network, no individual: the simulation is
src/neuroscifi/webcortex/bench/calibration.py and its docstring says what a stream is and how it is scored.
`report` is what docs/quality/evidence/open_items_calibration.json records; `select` is how the settings in
evolution.py were chosen (about two minutes).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from neuroscifi.webcortex.bench import calibration                     # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", choices=("report", "select", "change"))
    ap.add_argument("--out", help="write the JSON here (default: print it)")
    a = ap.parse_args()
    if a.mode == "report":
        value = {"benchmark": "calibration", "what": "prequential: every forecast is scored before its outcome is "
                 "used; settings chosen on the development streams only", "scores": {
                     "brier": "mean (forecast - outcome)^2", "distance": "mean (forecast - true probability)^2"},
                 "prefixes": "the first 25, 50 and 100 predictions of a stream, and all of it"} | calibration.report()
    elif a.mode == "change":
        rows = calibration.select_change()
        value = {"benchmark": "calibration, a change of habits (ADR-048)", "grid": calibration.CHANGE_GRID,
                 "rules": calibration.CHANGE_RULES, "shipped": calibration.CHOSEN, "selected": rows[0]["options"],
                 "gate": calibration.change_gate(rows[0]["options"]), "selection": rows}
    else:
        rows = calibration.select()
        value = {"benchmark": "calibration, selection", "grid": {k: list(v) for k, v in calibration.GRID.items()},
                 "rule": "lowest mean distance among the settings whose worst change to any development stream at "
                         "any prefix is at most +0.002; decided before the grid was run",
                 "shipped": calibration.CHOSEN,
                 "note": "the first row of this order has a minimum of 10 settlements; the shipped setting is the "
                         "second row, the same with a minimum of 20 (mean distance 0.0263 against 0.0253). The "
                         "larger minimum was kept because one refresh can settle several predictions about one "
                         "source, so real settlements count for fewer than their number; on the burst streams of "
                         "`report` the two minimums score alike",
                 "rows": rows}
    text = json.dumps(value, indent=1)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {a.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
