"""Where the time of `learner.fit` goes, what the learned sparse graph is worth beside the packed dense one, and up
to how many populations the dense fit works.

    PYTHONPATH=src python scripts/bench_graph_learner.py time   [--out FILE] [--repeats 7]
    PYTHONPATH=src python scripts/bench_graph_learner.py select [--out FILE] [--worlds 64]
    PYTHONPATH=src python scripts/bench_graph_learner.py sizes  [--out FILE]
    PYTHONPATH=src python scripts/bench_graph_learner.py birth  [--out FILE]

`time`: one stream per size through (a) the dense matrix alone, (b) the loop as the lab wrote it (`learner._learn`
before the exchange was vectorised, kept here verbatim), (c) today's `_learn`, (d) today's `_learn` with the
exchange switched off. (b) and (c) must agree bit for bit; the script says whether they do. Times are wall seconds
on a shared machine: the minimum of the repeats is the figure to read, the median says how noisy the run was.

`select`: for worlds drawn like `lab.graph_bench` (N populations, K true inputs per receiver), what three selection
rules would promote and its error on data neither the fit nor the selection saw: gate-v1 ("rule_today" in the
output: candidates from the dense fit, within the tolerance of the dense FP32 error), the same with the learned
sparse graph added as a candidate, and best-learner-v1 (`pack_reference: best_learner`: within the tolerance of the
better learner).

`sizes`: the product path (datasets.add, graphs.learn, evaluate, promote) at 24 to 1024 populations, on an
individual born under gate-v1 alone and on one born under the birth rules of today, beside the error of a graph with
no edges at all.

`birth`: the acceptance scenarios that learn a graph, with their default parameters, once with every individual born
under gate-v1 alone and once under the birth rules of today, and every leaf of the two reports that differs (timings
and file sizes on disk left out). The first physical test is preregistered under gate-v1 and creates its individuals
that way; for the second run this mode lifts that pin.
"""
import argparse
import json
import os
import platform
import statistics
import time

import numpy as np

from neuroscifi.graph_store import decode, encode
from neuroscifi.instance import learner, store


def world(n, k, rng, low=.17, high=.25):
    """A [N, N] matrix with k non-self inputs per receiver (lab.graph_bench.world for any N and k)."""
    a = np.zeros((n, n))
    for j in range(n):
        idx = rng.choice(np.delete(np.arange(n), j), k, replace=False)
        a[j, idx] = rng.choice([-1., 1.], k) * rng.uniform(low, high, k)
    return a


def stream(a, length, rng):
    """lab.graph_bench.stream for any N."""
    n = len(a)
    prev = np.zeros(n)
    states = np.empty((length, n))
    targets = np.empty_like(states)
    for t in range(length):
        residual = a @ prev + rng.normal(0, .065, n)
        states[t] = prev
        targets[t] = residual
        drive = rng.normal(0, .6, n) * (rng.random(n) < .22)
        prev = .2 * prev + residual + drive
    return states, targets


def learn_as_the_lab_wrote_it(states, targets, use):
    """`learner._learn` as it was before this change (commit 307b907), verbatim: the reference for the timing."""
    n = states.shape[1]
    dense = np.zeros((n, n))
    growing = dense.copy()
    score = dense.copy()
    swaps = 0
    for left in range(0, len(states), learner.WINDOW):
        xx = states[left:left + learner.WINDOW]
        yy = targets[left:left + learner.WINDOW]
        denom = (xx * xx).sum(axis=0) + learner.RIDGE
        for mat, mask in ((dense, None), (growing, use)):
            residual = yy - xx @ mat.T
            grad = (residual.T @ xx) / denom
            np.fill_diagonal(grad, 0)
            mat += learner.LEARNING_RATE * grad if mask is None else learner.LEARNING_RATE * grad * mask
            if mat is growing:
                score = learner.SCORE_KEEP * score + learner.SCORE_NEW * grad
        if (left // learner.WINDOW + 1) % learner.SWAP_EVERY == 0:
            for j in range(n):
                inside = np.where(use[j])[0]
                outside = np.where((~use[j]) & (np.arange(n) != j))[0]
                if not len(inside) or not len(outside):
                    continue
                drop = inside[np.argmin(abs(growing[j, inside]))]
                add = outside[np.argmax(abs(score[j, outside]))]
                if abs(score[j, add]) > learner.SWAP_RATIO * abs(growing[j, drop]) + learner.SWAP_MARGIN:
                    growing[j, drop] = 0
                    use[j, drop] = False
                    use[j, add] = True
                    growing[j, add] = 0
                    swaps += 1
    return {"dense": dense, "sparse": growing, "support": use, "swaps": swaps}


def dense_only(states, targets):
    n = states.shape[1]
    dense = np.zeros((n, n))
    for left in range(0, len(states), learner.WINDOW):
        xx = states[left:left + learner.WINDOW]
        yy = targets[left:left + learner.WINDOW]
        denom = (xx * xx).sum(axis=0) + learner.RIDGE
        grad = ((yy - xx @ dense.T).T @ xx) / denom
        np.fill_diagonal(grad, 0)
        dense += learner.LEARNING_RATE * grad
    return dense


def _same(a, b):
    return bool(a["dense"].tobytes() == b["dense"].tobytes() and a["sparse"].tobytes() == b["sparse"].tobytes()
                and np.array_equal(a["support"], b["support"]) and a["swaps"] == b["swaps"])


def run_time(repeats):
    variants = {
        "lab_loop_before": lambda x, y, use: learn_as_the_lab_wrote_it(x, y, use),
        "learn": lambda x, y, use: learner._learn(x, y, use),
        "learn_without_exchange": lambda x, y, use: learner._learn(x, y, use, exchange=lambda *a: 0),
    }
    out = []
    for n, steps in ((24, 1536), (256, 1536), (1024, 1536), (1024, 8192)):
        rng = np.random.default_rng(n + steps)
        x, y = stream(world(n, 3, rng), steps, rng)
        seconds = {name: [] for name in (*variants, "dense_matrix_only")}
        results = {}
        for _ in range(repeats):                      # variants interleaved, so a busy minute hits all of them
            for name, fn in variants.items():
                use = learner._layout(n, 3, np.random.default_rng(0))
                started = time.perf_counter()
                results[name] = fn(x, y, use)
                seconds[name].append(time.perf_counter() - started)
            started = time.perf_counter()
            dense = dense_only(x, y)
            seconds["dense_matrix_only"].append(time.perf_counter() - started)
        row = {"n": n, "steps": steps, "swaps": results["learn"]["swaps"],
               "identical_to_lab_loop": _same(results["lab_loop_before"], results["learn"])
               and dense.tobytes() == results["learn"]["dense"].tobytes(),
               "seconds": {name: {"min": round(min(v), 4), "median": round(statistics.median(v), 4)}
                           for name, v in seconds.items()}}
        best = {name: min(v) for name, v in seconds.items()}
        row["ratio_to_dense_matrix_only"] = {name: round(best[name] / best["dense_matrix_only"], 2) for name in variants}
        row["speedup_over_lab_loop"] = round(best["lab_loop_before"] / best["learn"], 2)
        row["exchange_share_of_fit"] = {
            "before": round((best["lab_loop_before"] - best["learn_without_exchange"]) / best["lab_loop_before"], 3),
            "now": round(max(0.0, best["learn"] - best["learn_without_exchange"]) / best["learn"], 3)}
        out.append(row)
        print(json.dumps(row), flush=True)
    return {"repeats": repeats, "sizes": out}


SELECTIONS = {
    "rule_today": "gate-v1: candidates cut from the dense fit; within the tolerance of the dense FP32 error",
    "sparse_added": "pack_candidates.learned_sparse: the learned sparse graph is a candidate too; the same reference",
    "best_learner": "learned_sparse and pack_reference best_learner: within the tolerance of the better learner",
}


def _rules(rule):
    sparse_too = dict(rule, pack_candidates=dict(rule["pack_candidates"], learned_sparse=True))
    return {"rule_today": rule, "sparse_added": sparse_too, "best_learner": dict(sparse_too, pack_reference="best_learner")}


def run_select(worlds, rule):
    """Per configuration: what each selection would promote, and its error on data neither fit nor selection saw."""
    tolerance = rule["validation_tolerance"]
    configs = [  # n, true inputs per receiver, learner k, steps, label
        (24, 3, 3, 1536, "lab world: 24 populations, 3 true inputs, k = 3 (lab.graph_bench; datasets.simulate)"),
        (24, 3, 3, 512, "the lab world on a short stream: 512 steps"),
        (24, 3, 3, 6144, "the lab world on a long stream: 6144 steps"),
        (24, 6, 3, 1536, "6 true inputs per receiver, k = 3 (the edge budget is too small)"),
        (24, 2, 3, 1536, "2 true inputs per receiver, k = 3 (the edge budget is too large)"),
        (96, 3, 3, 1536, "96 populations, 3 true inputs, k = 3"),
        (96, 3, 3, 6144, "96 populations on a long stream: 6144 steps"),
        (160, 3, 3, 1536, "160 populations, 3 true inputs, k = 3"),
        (256, 3, 3, 1536, "256 populations, 3 true inputs, k = 3"),
    ]
    out = []
    for n, true_k, k, steps, label in configs:
        rows = []
        for w in range(worlds if n < 160 else min(worlds, 16)):
            rng = np.random.default_rng(813240 + w)
            a = world(n, true_k, rng)
            x, y = stream(a, steps, rng)
            val = stream(a, 256, np.random.default_rng(10_000 + w))
            held = stream(a, 1024, np.random.default_rng(20_000 + w))
            fitted = learner.fit(x, y, k=k, seed=w)
            chosen = {}
            for name, definition in _rules(rule).items():
                packed = learner.pack(fitted["dense"], val, definition, sparse=fitted["sparse"])
                chosen[name] = {"source": packed.get("learner", "dense"), "degree": packed["degree"],
                                "bits": packed["bits"], "byte_size": packed["byte_size"],
                                "validation_mse": packed["validation_mse"],
                                "heldout_mse": learner.mse(decode(packed["blob"]), *held)}
            dense_fp32 = {"validation_mse": packed["dense_validation_mse"],
                          "heldout_mse": learner.mse(fitted["dense"].astype("f4"), *held)}
            sparse = {}
            for bits in rule["pack_candidates"]["bits"]:
                graph = decode(encode(fitted["sparse"].astype("f4"), k, bits))
                sparse[bits] = {"validation_mse": learner.mse(graph, *val), "heldout_mse": learner.mse(graph, *held)}
            rows.append({"dense": dense_fp32, "chosen": chosen, "sparse": sparse,
                         "no_graph_heldout_mse": float(np.mean(held[1] ** 2)),
                         "sparse_eligible": {bits: c["validation_mse"] <= (1 + tolerance) * dense_fp32["validation_mse"]
                                             for bits, c in sparse.items()},
                         "true_edges_in_sparse_support": float((fitted["support"] & (a != 0)).sum()
                                                               / max(1, fitted["support"].sum()))})

        def share(pred):
            return round(sum(1 for r in rows if pred(r)) / len(rows), 3)

        def med(values):
            return round(statistics.median(list(values)), 6)

        summary = {
            "label": label, "n": n, "true_inputs": true_k, "k": k, "steps": steps, "worlds": len(rows),
            "median_validation_mse": {"dense_fp32": med(r["dense"]["validation_mse"] for r in rows),
                                      **{f"sparse_{bits}_bit": med(r["sparse"][bits]["validation_mse"] for r in rows)
                                         for bits in rule["pack_candidates"]["bits"]}},
            "median_heldout_mse": {"dense_fp32": med(r["dense"]["heldout_mse"] for r in rows),
                                   **{f"sparse_{bits}_bit": med(r["sparse"][bits]["heldout_mse"] for r in rows)
                                      for bits in rule["pack_candidates"]["bits"]}},
            "sparse_within_tolerance_of_dense_share": {str(bits): share(lambda r, bits=bits: r["sparse_eligible"][bits])
                                                       for bits in rule["pack_candidates"]["bits"]},
            "median_true_edges_in_sparse_support": med(r["true_edges_in_sparse_support"] for r in rows),
            "median_no_graph_heldout_mse": med(r["no_graph_heldout_mse"] for r in rows),
            "selection": {},
        }
        for name in SELECTIONS:
            picks = [r["chosen"][name] for r in rows]
            kinds = sorted({(c["source"], c["degree"], c["bits"]) for c in picks})
            summary["selection"][name] = {
                "chosen": {f"{source} degree {degree}, {bits} bit":
                           round(sum(1 for c in picks if (c["source"], c["degree"], c["bits"]) == (source, degree, bits))
                                 / len(picks), 3) for source, degree, bits in kinds},
                "sparse_chosen_share": round(sum(1 for c in picks if c["source"] == "sparse") / len(picks), 3),
                "median_byte_size": med(c["byte_size"] for c in picks),
                "median_heldout_mse": med(c["heldout_mse"] for c in picks),
                "median_heldout_mse_over_rule_today": med(c["heldout_mse"] / r["chosen"]["rule_today"]["heldout_mse"]
                                                          for c, r in zip(picks, rows)),
                "heldout_better_than_rule_today_share": round(
                    sum(1 for c, r in zip(picks, rows) if c["heldout_mse"] < r["chosen"]["rule_today"]["heldout_mse"])
                    / len(picks), 3),
                "heldout_worse_than_rule_today_share": round(
                    sum(1 for c, r in zip(picks, rows) if c["heldout_mse"] > r["chosen"]["rule_today"]["heldout_mse"])
                    / len(picks), 3),
                "worse_than_no_graph_share": round(
                    sum(1 for c, r in zip(picks, rows) if c["heldout_mse"] >= r["no_graph_heldout_mse"]) / len(picks), 3),
            }
        out.append(summary)
        print(json.dumps(summary), flush=True)
    return {"rule": rule, "selections": SELECTIONS, "noise_floor_mse": round(.065 ** 2, 6), "configurations": out}


def run_sizes():
    """datasets.add -> graphs.learn -> evaluate -> promote on a new individual, per population count and birth rules."""
    import tempfile
    from pathlib import Path

    from neuroscifi.instance import datasets, graphs

    now = 1_800_000_000.0
    births = {"born_under_gate_v1": (store.GATE_V1,), "born_under_birth_rules": store.BIRTH_RULES}
    out = []
    for n in (24, 48, 64, 96, 128, 160, 192, 224, 256, 512, 1024):
        rows = {name: [] for name in births}
        for seed in range(3 if n <= 256 else 1):
            rng = np.random.default_rng(1000 * n + seed)
            a = world(n, 3, rng)
            data = {"train": stream(a, 1536, rng), "validation": stream(a, 256, rng), "heldout": stream(a, 512, rng)}
            for name, birth_rules in births.items():
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "individual.sqlite"
                    db = store.create(path, now, birth_rules=birth_rules)
                    ids = {split: datasets.add(db, "panel", split, *pair, "simulated", now)
                           for split, pair in data.items()}
                    revision = graphs.learn(db, "panel", now + 1, train=ids["train"], validation=ids["validation"],
                                            holder="bench")
                    shown = graphs.show(db, revision)
                    heldout = graphs.evaluate(db, revision, now + 2)["heldout_mse"]
                    decision = graphs.promote(db, revision, now + 3)
                    db.close()
                metrics = shown["metrics"]
                rows[name].append({
                    "rule": shown["rule_version"], "learner": metrics["pack"].get("learner", "dense"),
                    "no_graph_validation_mse": metrics["no_graph_validation_mse"],
                    "dense_validation_mse": metrics["dense_validation_mse"],
                    "candidate_validation_mse": metrics["validation_mse"], "candidate_heldout_mse": heldout,
                    "learned_sparse_validation_mse": metrics["sparse"]["validation_mse"],
                    "candidate": [shown["degree"], shown["bits"], shown["byte_size"]],
                    "beats_no_graph": metrics["beats_no_graph"],
                    "promoted": decision["promoted"], "failed_checks": decision["decision"]["failed"]})
        summary = {"n": n, "worlds": len(rows["born_under_gate_v1"])}
        for name, items in rows.items():
            summary[name] = {
                "rule": items[0]["rule"], "learner": sorted({r["learner"] for r in items}),
                **{key: float(np.median([r[key] for r in items])) for key in (
                    "no_graph_validation_mse", "dense_validation_mse", "candidate_validation_mse",
                    "candidate_heldout_mse", "learned_sparse_validation_mse")},
                "candidate_degree_bits_bytes": items[0]["candidate"],
                "beats_no_graph": [r["beats_no_graph"] for r in items],
                "promoted": [r["promoted"] for r in items], "failed_checks": [r["failed_checks"] for r in items]}
        out.append(summary)
        print(json.dumps(summary), flush=True)
    return {"stream": "lab.graph_bench.stream for N populations, 3 true inputs per receiver, 1536 training steps",
            "noise_floor_mse": round(.065 ** 2, 6), "sizes": out}


NOT_COMPARED = ("seconds", "latency", "_ms.", "_ms[", "p50", "p95", "p99", "cpu", "duration", "elapsed", "wall",
                "bytes", "path", "high_water", "throughput", "per_second", "mean_us", "max_us", "gpu")
GRAPH_SCENARIOS = ("birth", "graph_development", "growth_retention", "device_ownership", "first_physical_test")


def _leaves(value, prefix=""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(item, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _leaves(item, f"{prefix}[{index}]")
    else:
        yield prefix, value


def run_birth():
    """The acceptance scenarios that learn a graph, born under gate-v1 alone and under the birth rules of today."""
    from neuroscifi.acceptance import scenarios

    def run(birth_rules):
        saved = store.BIRTH_RULES, scenarios.FIRST_TEST_BIRTH_RULES
        store.BIRTH_RULES = scenarios.FIRST_TEST_BIRTH_RULES = birth_rules
        try:
            return scenarios.run(list(GRAPH_SCENARIOS))
        finally:
            store.BIRTH_RULES, scenarios.FIRST_TEST_BIRTH_RULES = saved

    today = store.BIRTH_RULES
    gate, best = run((store.GATE_V1,)), run(today)
    out = {"parameters": best["parameters"], "seed": best["seed"],
           "birth_rules": {"gate-v1": ["gate-v1"], "today": [rule["version"] for rule in today]},
           "not_compared": "leaves whose name marks a timing or a size on disk: " + ", ".join(NOT_COMPARED),
           "scenarios": {}}
    for name in GRAPH_SCENARIOS:
        a, b = dict(_leaves(gate["scenarios"][name])), dict(_leaves(best["scenarios"][name]))
        differing = [key for key in sorted(set(a) | set(b)) if a.get(key) != b.get(key)]
        changed = {key: {"gate-v1": a.get(key), "today": b.get(key)} for key in differing
                   if not any(word in key.lower() + "." for word in NOT_COMPARED)}
        out["scenarios"][name] = {
            "pass": {"gate-v1": gate["scenarios"][name]["pass"], "today": best["scenarios"][name]["pass"]},
            "checks": len(best["scenarios"][name]["checks"]),
            "checks_changed": sorted(key for key in changed if key.startswith("checks.")),
            "leaves": len(set(a) | set(b)), "leaves_changed": len(changed),
            "timing_or_size_leaves_differing": len(differing) - len(changed), "changed": changed}
        print(json.dumps({name: {k: v for k, v in out["scenarios"][name].items() if k != "changed"}}), flush=True)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=("time", "select", "sizes", "birth"))
    ap.add_argument("--out")
    ap.add_argument("--repeats", type=int, default=7)
    ap.add_argument("--worlds", type=int, default=64)
    args = ap.parse_args()
    result = {"benchmark": f"graph_learner_{args.mode}", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "environment": {"machine": platform.machine(), "python": platform.python_version(),
                              "numpy": np.__version__, "load_average_at_start": list(os.getloadavg())}}
    result |= (run_time(args.repeats) if args.mode == "time" else run_sizes() if args.mode == "sizes"
               else run_birth() if args.mode == "birth" else run_select(args.worlds, store.GATE_V1))
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(result, fh, indent=1)
            fh.write("\n")


if __name__ == "__main__":
    main()
