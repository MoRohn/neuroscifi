"""Wave 3 lane H (ADR-058): predictive regulation in shadow, checked on the anticipation family's traces
(development and held-out; never the withheld family), and the offline research benchmark with shadow on.

  scripts/bench_shadow.py check [--out FILE]     every trace x seed of both splits through the product's wiring
                                                 (acceptance/cognition/shadow_check.py):
      same      the gate's decisions with the setting "shadow" equal those with it off, tick by tick, and so does the
                plant's trace
      product   the report after the sampler's incremental settlement (the critical line, the forecast's horizon of
                180 s) against scoring.breach_report on the same trace with the same line and horizon
      family    the report settled lazily with the family's breach line (BREACH_FLOOR_GIB) and horizon (HORIZON
                ticks) against the family's own scoring.breach_report
      cost      the gate's wall time per call off and in shadow: on each tick (a new sample: the forecast is computed
                and a line appended) and repeated on an unchanged history (the worker's usual case)
  scripts/bench_shadow.py digest                 the offline research benchmark (scripts/bench_wave1.py offline) with
                                                 every individual reading predictive_regulation = "shadow"

Verdict rules, committed with this script before its first run: "same" must hold on every tick of every run;
"product" and "family" must show no mismatch on any run (exact counts; mean lead within 0.05 s); the digest must be
68731d72... (Wave 0's, on this aarch64 host). Simulated plant: the constants are the benchmark's, not the host's.
The pooled counts are pooled, not a claim: they say what the shadow recorded on these traces, not what it would on
the host."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

UNCHANGED_DIGEST = "68731d7235cbae9fca289124ea59bed99c1aa5b578ecaf8784d2e3df54cdf9d5"
GIB = 1024 ** 3


def _cost(path, n=500) -> dict:
    """Seconds per gate call on an unchanged history, shadow as recorded, then the same file read as off."""
    from neuroscifi.resources import policy
    sample = {"level": "normal", "reasons": []}
    out = {}
    for label in ("shadow", "off"):
        if label == "off":
            from neuroscifi.instance import store
            db = store.open_existing(path, graph_migration=False)
            try:
                store.set_config(db, {"predictive_regulation": False}, time.time())
            finally:
                db.close()
        policy._predictive_cache.clear()
        policy.background_gate(path, sample=sample, audio=False)
        times = []
        for _ in range(n):
            started = time.perf_counter()
            policy.background_gate(path, sample=sample, audio=False)
            times.append(time.perf_counter() - started)
        times.sort()
        out[label] = {"mean_us": round(statistics.fmean(times) * 1e6, 1), "p95_us": round(times[int(0.95 * n)] * 1e6,
                                                                                            1)}
    return out


def check() -> dict:
    from neuroscifi.acceptance import cognition
    from neuroscifi.acceptance.cognition import anticipation, shadow_check, splits
    from neuroscifi.resources import forecast, monitor, shadow
    critical = monitor.thresholds(int(anticipation.TOTAL_GIB * GIB))["critical_below_bytes"] / GIB
    forecast_ticks = int(forecast.HORIZON_SECONDS / anticipation.TICK_SECONDS)
    out = {"label": "simulated plant (anticipation.py constants); the product's wiring on a staged individual; "
                    "offline", "environment": cognition.environment(), "splits": {}}
    started = time.perf_counter()
    for split in ("development", "held_out"):
        spec = splits.ANTICIPATION[split]
        rows, pooled = [], {"product": {}, "family": {}}
        gate_off, gate_shadow = [], []
        for seed in spec["seeds"]:
            for trace in spec["traces"]:
                with tempfile.TemporaryDirectory(prefix="nsf-shadow-check-") as directory:
                    off = shadow_check.staged_run(Path(directory) / "off", trace, seed, False)
                    shadowed = shadow_check.staged_run(Path(directory) / "shadow", trace, seed, "shadow")
                    lazy = shadow_check.staged_run(Path(directory) / "lazy", trace, seed, "shadow",
                                                   settle_every=float("inf"))
                    product = shadow.report(shadowed["path"], shadowed["clock"])
                    family = shadow.report(lazy["path"], lazy["clock"],
                                           window=anticipation.HORIZON * anticipation.TICK_SECONDS,
                                           floor_bytes=int(anticipation.BREACH_FLOOR_GIB * GIB))
                    ref_product = shadow_check.reference(shadowed, floor_gib=critical, horizon_ticks=forecast_ticks)
                    ref_family = shadow_check.reference(lazy, floor_gib=anticipation.BREACH_FLOOR_GIB,
                                                        horizon_ticks=anticipation.HORIZON)
                    cost = _cost(shadowed["path"]) if seed == spec["seeds"][0] else None
                gate_off += off["gate_seconds"]
                gate_shadow += shadowed["gate_seconds"]
                same_ticks = sum(1 for a, b in zip(off["ticks"], shadowed["ticks"]) if a["gate"] == b["gate"])
                row = {"trace": trace["name"], "seed": seed, "ticks": len(off["ticks"]),
                       "gate_same_ticks": same_ticks, "trace_same": off["observations"] == shadowed["observations"],
                       "lazy_gate_same_ticks": sum(1 for a, b in zip(off["ticks"], lazy["ticks"])
                                                   if a["gate"] == b["gate"]),
                       "forecast_holds": sum(1 for t in shadowed["ticks"] if t["forecast_hold"]),
                       "gate_refusals": sum(1 for t in off["ticks"] if not t["gate"]["allowed"]),
                       "product": {"report": shadow_check.settled(product), "reference": ref_product,
                                   "mismatches": shadow_check.mismatches(product, ref_product),
                                   "work": product["work"], "problems": product["problems"]},
                       "family": {"report": shadow_check.settled(family), "reference": ref_family,
                                  "mismatches": shadow_check.mismatches(family, ref_family)}}
                if cost is not None:
                    row["cost_unchanged_history"] = cost
                rows.append(row)
                for which in ("product", "family"):
                    for k, v in row[which]["report"].items():
                        if isinstance(v, int):
                            pooled[which][k] = pooled[which].get(k, 0) + v
        n = len(rows)
        out["splits"][split] = {
            "seeds": list(spec["seeds"]), "traces": [t["name"] for t in spec["traces"]], "runs": n,
            "verdict": {"same_every_tick": all(r["gate_same_ticks"] == r["ticks"] and r["trace_same"]
                                               and r["lazy_gate_same_ticks"] == r["ticks"] for r in rows),
                        "product_runs_without_mismatch": sum(1 for r in rows if not r["product"]["mismatches"]),
                        "family_runs_without_mismatch": sum(1 for r in rows if not r["family"]["mismatches"]),
                        "runs": n},
            "did": {"forecast_hold_ticks": sum(r["forecast_holds"] for r in rows),
                    "gate_refusal_ticks": sum(r["gate_refusals"] for r in rows),
                    "decisions_changed_by_shadow": sum(r["ticks"] - r["gate_same_ticks"] for r in rows)},
            "pooled_not_a_claim": pooled,
            "gate_seconds_per_tick": {
                "off": {"mean_us": round(statistics.fmean(gate_off) * 1e6, 1),
                        "p95_us": round(sorted(gate_off)[int(0.95 * len(gate_off))] * 1e6, 1)},
                "shadow": {"mean_us": round(statistics.fmean(gate_shadow) * 1e6, 1),
                           "p95_us": round(sorted(gate_shadow)[int(0.95 * len(gate_shadow))] * 1e6, 1)},
                "note": "each tick brings a new history sample, so in shadow every call computes the forecast and "
                        "appends a line: the worst case; the worker calls the gate many times per 30 s sample"},
            "rows": rows}
    out["wall_seconds"] = round(time.perf_counter() - started, 1)
    return out


def digest() -> dict:
    import bench_wave1
    bench_wave1.REGIMES["shadow"] = lambda: bench_wave1.regime({"predictive_regulation": "shadow"}, {})
    value = bench_wave1.offline("shadow")
    return {"regime": "every individual reads predictive_regulation = 'shadow' (the rest as shipped)",
            "sha256": value["sha256"], "equals_wave0": value["sha256"] == UNCHANGED_DIGEST,
            "wall_seconds": value["wall_seconds"]}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--out")
    d = sub.add_parser("digest")
    d.add_argument("--out")
    args = p.parse_args(argv)
    value = check() if args.cmd == "check" else digest()
    text = json.dumps(value, indent=1, default=str) + "\n"
    if args.out:
        Path(args.out).write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
