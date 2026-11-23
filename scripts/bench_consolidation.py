"""Cost of consolidated readings (ADR-044): compute() and decide() on one epoch, all raw against all consolidated.

    python scripts/bench_consolidation.py WORKDIR > result.json
    python scripts/bench_consolidation.py WORKDIR copies [10000 100000 1000000] > copies.json

Builds, per size (200, 10,000 and 100,000 learning events), two temporary individuals in WORKDIR with the same events:
three channels, four roots each, a sample id on a third of them, one confirmed camera change in the middle. In one of
them every event is then expired by a sweep, which consolidates it. Reports the sweep's time, whether the posteriors
are identical before and after it, the time of compute() and decide() on each, and the bytes a consolidated reading
takes with its indexes.

Timing is CPU time (time.process_time), the minimum of 31 warm calls: on a shared, loaded host wall-clock medians move
by a factor of several between runs. The sweep is one call, wall-clock. No model, no network; the files are left in
WORKDIR. Recorded result: docs/quality/evidence/accel_consolidation_cost.json.

`copies`: the cost of the rule that the earliest copy of a sample counts (ADR-044, decision 2), on epochs whose
readings are all consolidated and all carry a sample id. Per size, three files: every sample recorded once, one
sample in ten recorded twice, every sample recorded twice (two devices sending the same frame). Readings are written
straight into evidence_consolidated with their tombstones, 200 roots on two channels and one confirmed camera change,
no ANALYZE. Reported: compute(), decide(), learning_since() for one root, explain(), a forget of one reading and a
sweep that expires 20 memory-only events (each recomputes the epoch for its belief revision). One wall-clock call
each after one warm call of compute().
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


def build_copies(path, n, share):
    """An epoch of n consolidated readings, all with a sample id; `share` of the samples (0, 0.1 or 1) twice."""
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)
    db = store.create(path, T0, config=store.InstanceConfig(max_evidence_rows=10**9))
    evidence.start_epoch(db, "e", T0)
    rows, seq, sample = [], 0, 0
    while seq < n:
        sample += 1
        channel, root, value = ("camera" if sample % 3 else "word"), f"r{sample % 200}", (sample // 7) % 2
        for _ in range(2 if (share == 1 or (share and sample % 10 == 0)) else 1):
            seq += 1
            rows.append((seq, "e", channel, root, value, f"s{sample}", T0))
    with store.write(db):
        db.executemany("INSERT INTO evidence_consolidated(seq,epoch,channel,root,value,sample_id,consolidated_at) "
                       "VALUES(?,?,?,?,?,?,?)", rows)
        db.executemany("INSERT INTO tombstones(id,seq,epoch,deleted_at,reason) VALUES(?,?,?,?,'expired')",
                       [(f"id-{r[0]}", r[0], "e", T0) for r in rows])
        db.execute("INSERT OR REPLACE INTO sqlite_sequence(name,seq) VALUES('evidence',?)", (len(rows),))
        db.execute("INSERT INTO changepoints(epoch,scope,since_seq,pending_id,created_at,confirmed) "
                   "VALUES('e','camera:r1',?,NULL,?,1)", (len(rows) // 2, T0))
        store.bump_state_version(db)
    for k in range(20):
        evidence.observe(db, {"id": f"m{k}", "epoch": "e", "channel": "word", "action": 0, "bit": 1, "root": f"r{k}",
                              "consent": "memory_only"}, T0 + 10 + k)
    return db, len(rows)


def once(fn):
    start = time.perf_counter()
    fn()
    return round((time.perf_counter() - start) * 1000, 1)


def copies(workdir, sizes):
    out = {}
    for n in sizes:
        out[n] = {}
        for label, share in (("each_sample_once", 0), ("one_sample_in_ten_twice", .1), ("each_sample_twice", 1)):
            db, readings = build_copies(f"{workdir}/copies-{n}-{label}.sqlite", n, share)
            beliefs.compute(db, "e")
            later = 9 * 86400
            out[n][label] = {
                "readings": readings,
                "compute_ms": once(lambda: beliefs.compute(db, "e")),
                "decide_ms": once(lambda: executive.decide(db, "e", T0 + later)),
                "learning_since_one_root_ms": once(lambda: beliefs.learning_since(db, "e", 0, "camera", "r1")),
                "explain_ms": once(lambda: beliefs.explain(db, "e")),
                "forget_one_reading_ms": once(lambda: governance.forget_consolidated(db, 23, T0 + later)),
                "sweep_of_20_memory_only_events_ms": once(lambda: governance.sweep(db, T0 + later)),
            }
            db.close()
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "copies":
        copies(sys.argv[1], [int(x) for x in sys.argv[3:]] or [10_000, 100_000, 1_000_000])
    else:
        main(sys.argv[1])
