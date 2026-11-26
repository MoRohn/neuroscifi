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


def environment() -> dict:
    """The commit measured and the interpreter (not the harness's environment(), which hashes the withheld file)."""
    import platform
    import subprocess

    def git(*args):
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
    status = git("status", "--porcelain")
    return {"git_commit": git("rev-parse", "HEAD"), "git_dirty": None if status is None else bool(status),
            "python": platform.python_version(), "platform": platform.platform(), "machine": platform.machine()}


def check() -> dict:
    from neuroscifi.acceptance.cognition import anticipation, shadow_check, splits
    from neuroscifi.resources import forecast, monitor, shadow
    critical = monitor.thresholds(int(anticipation.TOTAL_GIB * GIB))["critical_below_bytes"] / GIB
    forecast_ticks = int(forecast.HORIZON_SECONDS / anticipation.TICK_SECONDS)
    out = {"label": "simulated plant (anticipation.py constants); the product's wiring on a staged individual; "
                    "offline", "environment": environment(), "splits": {}}
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
    from neuroscifi.resources import shadow
    bench_wave1.REGIMES["shadow"] = lambda: bench_wave1.regime({"predictive_regulation": "shadow"}, {})
    real, seen = shadow.observe, {"calls": 0, "recorded": 0}

    def counted(*args, **kwargs):           # what the shadow did during the run (it must have been reached)
        seen["calls"] += 1
        out = real(*args, **kwargs)
        seen["recorded"] += out is not None
        return out
    from neuroscifi.resources import policy
    # the gate reads the setting from the file itself (policy.predictive_mode), not through InstanceConfig.from_json,
    # which the regime patches: switch it there too, as instinct.switched_on does for "on"
    real_mode = policy.predictive_mode
    shadow.observe, policy.predictive_mode = counted, (lambda db_path: "shadow" if db_path else False)
    try:
        value = bench_wave1.offline("shadow")
    finally:
        shadow.observe, policy.predictive_mode = real, real_mode
    return {"regime": "every individual reads predictive_regulation = 'shadow' (the rest as shipped)",
            "environment": environment(), "sha256": value["sha256"], "equals_wave0": value["sha256"] ==
            UNCHANGED_DIGEST, "shadow_gate_calls": seen["calls"], "shadow_verdicts_recorded": seen["recorded"],
            "wall_seconds": value["wall_seconds"]}


def readiness_cost(knowledge=10_000, events=500_000, tasks=10_000, predictions=20_000, hypotheses=3_000,
                   rechecks=2_000, scenario_records=1_000) -> dict:
    """The readiness report's wall time on a large staged individual (rows written directly; numbers only)."""
    import random
    from contextlib import closing
    from neuroscifi import readiness
    from neuroscifi.instance import store
    from neuroscifi.resources import shared
    from neuroscifi.webcortex import scenarios, state, strategies
    from neuroscifi.webcortex.types import MODES
    rng = random.Random(58)
    t0 = 1_800_000_000.0
    now = t0 + 400 * 86400
    with tempfile.TemporaryDirectory(prefix="nsf-readiness-cost-") as directory:
        path = Path(directory) / "individual.sqlite"
        with closing(store.create(path, t0)) as db:
            state.set_config(db, {"enabled": True}, t0)
            with store.write(db):
                db.executemany("INSERT INTO web_tasks(id, objective, mode, status, origin, created_at, updated_at, "
                               "finished_at) VALUES(?, 'q', ?, ?, 'console', ?, ?, ?)",
                               [(f"t{i}", MODES[i % len(MODES)], rng.choice(("completed", "partial", "failed")),
                                 t0 + i * 3000, t0 + i * 3000, t0 + i * 3000) for i in range(tasks)])
                db.executemany("INSERT INTO web_knowledge(id, key, version, statement, status, confidence, "
                               "uncertainty, freshness_class, first_verified_at, last_verified_at, task_id, "
                               "created_at, updated_at) VALUES(?, ?, 1, 's', 'accepted', 0.8, 0.2, 'stable', ?, ?, ?, "
                               "?, ?)", [(i + 1, f"k{i}", t0, t0, f"t{rng.randrange(tasks)}", t0, t0)
                                         for i in range(knowledge)])
                db.executemany("INSERT INTO web_events(task_id, kind, detail, created_at) VALUES(?, ?, '{}', ?)",
                               [(f"t{rng.randrange(tasks)}", rng.choice(("stage", "fetch", "claim", "decision")),
                                 t0 + i * 60) for i in range(events)])
                db.executemany("INSERT INTO web_hypotheses(id, key, statement, origin, status, created_at, "
                               "updated_at, resolved_at) VALUES(?, ?, 'h', ?, ?, ?, ?, ?)",
                               [(i + 1, f"h{i}", rng.choice(("gap", "owner")),
                                 rng.choice(("open", "supported", "refuted", "revised", "abandoned")), t0, t0,
                                 t0 + rng.random() * 400 * 86400) for i in range(hypotheses)])
                rows = []
                for i in range(predictions):
                    on_knowledge = rng.random() < 0.7
                    settled = rng.random() < 0.5
                    rows.append((rng.randrange(1, knowledge + 1) if on_knowledge else None,
                                 None if on_knowledge else rng.randrange(1, hypotheses + 1), rng.random(), t0, t0,
                                 t0 + rng.random() * 400 * 86400 if settled else None,
                                 rng.choice(("confirmed", "error", "inconclusive")) if settled else None))
                db.executemany("INSERT INTO web_predictions(knowledge_id, hypothesis_id, expectation, probability, "
                               "check_after, created_at, checked_at, outcome) VALUES(?, ?, 'e', ?, ?, ?, ?, ?)", rows)
                db.executemany("INSERT INTO web_rechecks(subject_kind, subject_id, reason, status, outcome, "
                               "created_at, updated_at) VALUES('knowledge', 1, 'idle', ?, ?, ?, ?)",
                               [(s, o, t0, t0) for s, o in (rng.choice((
                                   ("done", "superseded by new evidence"), ("done", "confirmed by new evidence"),
                                   ("done", "no evidence about it"), ("marked", None), ("dropped", "x")))
                                   for _ in range(rechecks))])
                # the payload scenarios.compare reads (three futures and the forecast used), as scenarios writes it
                payload = json.dumps({"prediction_id": 1, "futures": {"confirmed": {"p": 0.5}, "error": {"p": 0.3},
                                                                      "inconclusive": {"p": 0.2}},
                                      "forecast": {"used": 0.6}})
                db.executemany("INSERT INTO imagined(id, kind, payload, assumptions, uncertainty, mechanism, "
                               "mechanism_version, question, created_at, expires_at, outcome, observed_at) "
                               "VALUES(?, 'simulation', ?, '[]', 'u', ?, '1', 'q', ?, ?, ?, ?)",
                               [(f"imagined:{i:032x}", payload, scenarios.MECHANISM, t0, t0 + 10 ** 9,
                                 '{"observed": {"futures": 0}, "evidence": []}' if i % 2 else None,
                                 t0 + 1 if i % 2 else None) for i in range(scenario_records)])
        times, strategy_times = [], []
        with shared.read_database(path) as db:
            for _ in range(5):
                started = time.perf_counter()
                out = readiness.report(db, now)
                times.append(time.perf_counter() - started)
                started = time.perf_counter()
                theirs = strategies.outcomes(db)
                strategy_times.append(time.perf_counter() - started)
            same = out["learners"]["strategy_selection"]["by_mode"] == {
                m: {"tasks": o["tasks"], "rewarded": o["rewarded"]} for m, o in theirs.items() if o["tasks"]}
    return {"environment": environment(),
            "rows": {"web_knowledge": knowledge, "web_events": events, "web_tasks": tasks,
                     "web_predictions": predictions, "web_hypotheses": hypotheses, "web_rechecks": rechecks,
                     "imagined (scenario records)": scenario_records},
            "report_seconds": {"median": round(statistics.median(times), 4), "max": round(max(times), 4), "runs": 5},
            "for_comparison_strategies_outcomes_seconds": {
                "median": round(statistics.median(strategy_times), 4),
                "note": "the existing webcortex.strategies.outcomes on the same individual (two unindexed look-ups "
                        "per task); the report counts the same with one pass per table",
                "same_counts_as_the_report": same},
            "answers_with_a_later_verdict": out["answers"]["with_a_later_verdict"],
            "scenario_records_settled": out["scenarios"]["settled"]}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--out")
    d = sub.add_parser("digest")
    d.add_argument("--out")
    r = sub.add_parser("readiness-cost")
    r.add_argument("--out")
    args = p.parse_args(argv)
    value = {"check": check, "digest": digest, "readiness-cost": readiness_cost}[args.cmd]()
    text = json.dumps(value, indent=1, default=str) + "\n"
    if args.out:
        Path(args.out).write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
