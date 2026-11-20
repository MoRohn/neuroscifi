"""Host-local latency of the capture-to-action loop (observe, then decide) on a temporary instance.

This measures the machine it runs on. It is not a DGX Spark measurement (OPS-06 stays blocked on hardware).
"""
import os
from pathlib import Path
import platform
import sqlite3
import sys
import tempfile
import time

import numpy as np

from ..instance import evidence, executive, store

T0 = 1_800_000_000.0
HOST_LABEL = "host-local measurement on this machine, not Spark"


def percentiles(seconds):
    """p50/p95/p99/mean/max of a list of durations in seconds, reported in milliseconds."""
    ms = np.asarray(seconds, dtype=np.float64) * 1e3
    if not len(ms):
        return {"n": 0, "p50": None, "p95": None, "p99": None, "mean": None, "max": None}
    p50, p95, p99 = np.percentile(ms, [50, 95, 99])
    return {"n": int(len(ms)), "p50": float(p50), "p95": float(p95), "p99": float(p99),
            "mean": float(ms.mean()), "max": float(ms.max())}


def host_info():
    return {"platform": platform.platform(), "machine": platform.machine(), "processor": platform.processor() or None,
            "cpu_count": os.cpu_count(), "python": sys.version.split()[0], "numpy": np.__version__,
            "sqlite": sqlite3.sqlite_version}


def teaching_stream(rng, events):
    """Simulated teaching events: camera and word readings with 10% noise, every third event a healthy reference.

    Hidden bits (mechanism, camera alignment, partner convention) are drawn from `rng`. Reference roots
    change every six reference readings. Returns (truth, [observation dicts without id/epoch]).
    """
    truth = {"mechanism": int(rng.integers(2)), "camera": int(rng.integers(2)), "partner": int(rng.integers(2))}
    out = []
    for i in range(events):
        action = int(rng.integers(2))
        kind = i % 3
        if kind == 0:
            channel, root, relation = "camera", "camera-1", truth["mechanism"] ^ truth["camera"]
        elif kind == 1:
            channel, root, relation = "word", "partner-1", truth["mechanism"] ^ truth["partner"]
        else:
            channel, root, relation = "reference", f"reference-{i // 18}", truth["mechanism"]
        reading = relation ^ int(rng.random() < .10)
        out.append({"channel": channel, "root": root, "action": action, "bit": reading ^ action,
                    "provenance": "simulated", "actor": "simulator"})
    return truth, out


def run(events=500, seed=0):
    """Time observe and decide(record=True) separately for `events` simulated events on a fresh instance."""
    if isinstance(events, bool) or not isinstance(events, int) or events < 1:
        raise ValueError("events must be a positive integer")
    _, stream = teaching_stream(np.random.default_rng([seed, 9]), events)
    observe_s, decide_s, loop_s, decisions = [], [], [], {}
    with tempfile.TemporaryDirectory(prefix="nsf-bench-") as directory:
        path = Path(directory) / "bench.sqlite"
        db = store.create(path, T0, note="nsf bench (simulated events)")
        try:
            evidence.start_epoch(db, "bench", T0)
            for i, obs in enumerate(stream):
                now = T0 + 1 + i
                start = time.perf_counter()
                evidence.observe(db, {**obs, "id": f"bench-{i}", "epoch": "bench"}, now, path=path)
                mid = time.perf_counter()
                decision = executive.decide(db, "bench", now, record=True, path=path)
                end = time.perf_counter()
                observe_s.append(mid - start)
                decide_s.append(end - mid)
                loop_s.append(end - start)
                decisions[decision["decision"]] = decisions.get(decision["decision"], 0) + 1
        finally:
            db.close()
    return {"simulated": True, "label": HOST_LABEL, "events": events, "seed": seed,
            "observe_ms": percentiles(observe_s), "decide_ms": percentiles(decide_s),
            "loop_ms": percentiles(loop_s), "decisions": decisions, "host": host_info()}
