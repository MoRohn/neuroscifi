#!/usr/bin/env python3
"""Evidence for lane M, imagination (ADR-052): docs/quality/evidence/genesis_imagination.json.

  python scripts/genesis_imagination.py --out docs/quality/evidence/genesis_imagination.json

1. The counterfactual family on both splits (each split's own three seeds): the recorded baseline, the full
   mechanism, and each component switched off (scm, what_if, forecast, action).
2. The adversarial family with the mechanism, and retention with the mechanism (--quick unless --full-retention).
3. Costs on individuals of growing size: what-if, the comparison of next readings, retrospection (200 readings),
   a 5-step forecast and a push; seconds (median of 5) and the bytes the sandbox copied.
Offline, no model; the withheld family is never run here.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import random
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from neuroscifi.acceptance import cognition                                 # noqa: E402
from neuroscifi.acceptance.cognition import imagination                    # noqa: E402
from neuroscifi.instance import datasets, evidence, forecast, graphs, imagine, store   # noqa: E402

T0 = 1_800_000_000.0


def _headline(out, family):
    result = out["families"][family]["result"]
    if family == "counterfactual":
        return {"summary": {k: v for k, v in result["summary"].items()},
                "by_query_kind": {k: {m: v[m] for m in ("coverage", "mean_relative_error", "interval_coverage",
                                                        "abstention_correct")}
                                  for k, v in result["by_query_kind"].items()},
                "cost": out["families"][family]["cost"]}
    return result


def counterfactual():
    out = {}
    baseline = json.loads(cognition.BASELINE.read_text(encoding="utf-8"))
    for split in ("development", "held_out"):
        row = {"recorded_baseline": {"summary": baseline[split]["families"]["counterfactual"]["result"]["summary"]}}
        rerun = cognition.run(["counterfactual"], split=split, mechanism="baseline")
        row["baseline_rerun"] = _headline(rerun, "counterfactual")
        full = cognition.run(["counterfactual"], split=split, mechanism="imagination")
        row["imagination"] = _headline(full, "counterfactual")
        for component in imagination.COMPONENTS:
            ablated = cognition.run(["counterfactual"], split=split, mechanism="imagination", ablate=(component,))
            row[f"without_{component}"] = {"summary": _headline(ablated, "counterfactual")["summary"]}
        out[split] = row
    return out


def others(full_retention):
    adversarial = cognition.run(["adversarial"], mechanism="imagination")["families"]["adversarial"]
    retention = cognition.run(["retention"], mechanism="imagination", quick=not full_retention,
                              baseline_output=json.loads(cognition.BASELINE.read_text(encoding="utf-8")))
    return {"adversarial": {k: adversarial["result"][k] for k in ("passed", "scored", "failed")} |
            {"cases": [{k: c[k] for k in ("id", "pass", "observed")} for c in adversarial["result"]["cases"]
                       if c["pass"] is not None]},
            "retention": {"quick": not full_retention,
                          **{k: retention["families"]["retention"]["result"].get(k)
                             for k in ("all_scenarios_pass", "all_retained")},
                          "cost": retention["families"]["retention"]["cost"]}}


def _timed(call, repeat=5):
    """(median seconds, last value); a refusal (the sandbox's time limit) is reported as its message."""
    times = []
    for _ in range(repeat):
        started = time.perf_counter()
        try:
            value = call()
        except imagine.Refused as refused:
            return f"refused: {refused}", None
        times.append(time.perf_counter() - started)
    return round(statistics.median(times), 4), value


def costs():
    rows = []
    for readings in (100, 1_000, 10_000, 50_000):
        with tempfile.TemporaryDirectory(prefix="nsf-imagine-cost-") as directory:
            db = store.create(Path(directory) / "c.sqlite", T0)
            with closing(db):
                rng = random.Random(readings)
                evidence.start_epoch(db, "e", T0)
                with store.write(db):
                    for i in range(readings):
                        channel, root = (("reference", f"ref-{i % 3}") if i % 4 == 0 else
                                         ("word", f"p{i % 5}") if i % 4 in (1, 2) else ("camera", f"c{i % 2}"))
                        action = rng.randint(0, 1)
                        db.execute("INSERT INTO evidence(id,epoch,channel,action,bit,root,recorded_at,expires_at) "
                                   "VALUES(?,?,?,?,?,?,?,?)", (f"r{i}", "e", channel, action,
                                                              (rng.random() < 0.1) ^ action, root, T0 + i,
                                                              T0 + 86400 * 7))
                    store.bump_state_version(db)
                what_if, answer = _timed(lambda: imagine.what_if(db, "e", flip=["r1"], add=[
                    {"factor": "partner:p1", "reading": "agree", "count": 4}], now=T0 + 1e6))
                questions, _ = _timed(lambda: imagine.question_values(db, "e", now=T0 + 1e6), repeat=3)
                retro, _ = _timed(lambda: imagine.retrospect(db, "e", "partner:p1", limit=200), repeat=3)
                rows.append({"readings": readings, "copied_bytes": answer["cost"]["copied_bytes"] if answer else None,
                             "what_if_seconds": what_if, "question_values_seconds": questions,
                             "retrospect_200_seconds": retro})
    with tempfile.TemporaryDirectory(prefix="nsf-imagine-cost-") as directory:
        db = store.create(Path(directory) / "g.sqlite", T0)
        with closing(db):
            ids = datasets.simulate(db, "w", T0 + 1, seed=7)
            graphs.promote(db, graphs.learn(db, "w", T0 + 2, train=ids["train"], validation=ids["validation"],
                                            holder="cost"), T0 + 3)
            x = datasets.load(db, ids["heldout"])[0][5].tolist()
            rollout, _ = _timed(lambda: forecast.forecast(db, "w", x, 5))
            push, _ = _timed(lambda: forecast.forecast(db, "w", x, 5, action={"population": 2, "push": 0.6}))
    return {"belief_imagination": rows, "graph_forecast": {"populations": 24, "train_steps": 1536,
                                                           "horizon_5_seconds": rollout,
                                                           "horizon_5_with_push_seconds": push}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path)
    ap.add_argument("--full-retention", action="store_true")
    a = ap.parse_args(argv)
    cognition.load_plugins()
    out = {"what": "lane M, imagination (ADR-052): counterfactual family with ablations, adversarial and retention "
                   "with the mechanism, costs", "label": cognition.LABEL, "environment": cognition.environment(),
           "counterfactual": counterfactual(), **others(a.full_retention), "costs": costs()}
    from neuroscifi.acceptance.cognition.scoring import rounded
    text = json.dumps(rounded(out), indent=1) + "\n"
    if a.out:
        a.out.write_text(text, encoding="utf-8")
        print(f"wrote {a.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
