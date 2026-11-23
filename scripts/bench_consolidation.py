"""Cost of consolidated readings (ADR-044): compute() and decide() on one epoch, all raw against all consolidated.

    python scripts/bench_consolidation.py WORKDIR > result.json

Builds, per size (200, 10,000 and 100,000 learning events), two temporary individuals in WORKDIR with the same events:
three channels, four roots each, a sample id on a third of them, one confirmed camera change in the middle. In one of
them every event is then expired by a sweep, which consolidates it. Reports the sweep's time, whether the posteriors
are identical before and after it, the time of compute() and decide() on each, and the bytes a consolidated reading
takes with its indexes.

Timing is CPU time (time.process_time), the minimum of 31 warm calls: on a shared, loaded host wall-clock medians move
by a factor of several between runs. The sweep is one call, wall-clock. No model, no network; the files are left in
WORKDIR. Recorded result: docs/quality/evidence/accel_consolidation_cost.json.
"""
import json
import os
import sys
import time

from neuroscifi.instance import beliefs, evidence, executive, governance, store

T0 = 1_800_000_000.0


def build(path, n, ttl):
    db = store.create(path, T0, config=store.InstanceConfig(evidence_ttl_seconds=ttl, max_evidence_rows=10**9))
    evidence.start_epoch(db, "e", T0)
    rows = []
    for i in range(n):
        channel = ("camera", "word", "reference")[i % 3]
        root = f"{channel[0]}{i % 4}"
        sample = f"s{i}" if i % 3 == 0 else None
        rows.append((f"id-{i}", "e", channel, i % 2, (i % 2) ^ (1 if i % 11 == 0 else 0), root, sample, "owner",
                     "learn", "observed", None, T0 + i * 0.001, "standard", T0 + i * 0.001 + ttl))
    with store.write(db):
        db.executemany("INSERT INTO evidence(id,epoch,channel,action,bit,root,sample_id,actor,consent,provenance,"
                       "acquired_at,recorded_at,retention,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        db.execute("INSERT INTO changepoints(epoch,scope,since_seq,pending_id,created_at,confirmed) "
                   "VALUES('e','camera:c0',?,NULL,?,1)", (n // 2, T0))
        store.bump_state_version(db)
    return db


def timed(fn, repeat=31):
    fn()
    samples = []
    for _ in range(repeat):
        start = time.process_time()
        fn()
        samples.append((time.process_time() - start) * 1000)
    return round(min(samples), 2)


def size(path):
    return sum(os.path.getsize(p) for p in (path, path + "-wal") if os.path.exists(p))


def main(workdir):
    out = {}
    for n in (200, 10_000, 100_000):
        raw_path, folded_path = f"{workdir}/raw-{n}.sqlite", f"{workdir}/folded-{n}.sqlite"
        for p in (raw_path, folded_path):
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(p + suffix):
                    os.remove(p + suffix)
        raw = build(raw_path, n, 10**9)
        folded = build(folded_path, n, 7 * 86400)
        before = beliefs.compute(folded, "e")
        store.truncate_wal(folded)
        size_raw = size(folded_path)
        start = time.perf_counter()
        swept = governance.sweep(folded, T0 + 30 * 86400)
        sweep_ms = round((time.perf_counter() - start) * 1000, 1)
        folded.execute("VACUUM")
        store.truncate_wal(folded)
        after = beliefs.compute(folded, "e")
        same = all(before[k] == after[k] for k in ("mechanism", "sensors", "partners", "segments", "change"))
        out[n] = {
            "sweep": {"expired": swept["expired"], "consolidated": swept.get("consolidated"), "ms": sweep_ms},
            "posteriors_identical": same,
            "compute_ms": {"raw": timed(lambda: beliefs.compute(raw, "e")),
                           "consolidated": timed(lambda: beliefs.compute(folded, "e"))},
            "decide_ms": {"raw": timed(lambda: executive.decide(raw, "e", T0)),
                          "consolidated": timed(lambda: executive.decide(folded, "e", T0))},
            "file_bytes": {"raw_events": size_raw, "after_consolidation_and_vacuum": size(folded_path)},
            "bytes_per_consolidated_reading": round(
                folded.execute("SELECT sum(pgsize) FROM dbstat WHERE name LIKE 'evidence_consolidated%'"
                               ).fetchone()[0] / max(1, swept.get("consolidated") or 1), 1),
        }
        raw.close()
        folded.close()
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
