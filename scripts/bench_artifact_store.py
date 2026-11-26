"""The artifact store's cost on a large store (OMNIMIND lane A, ADR-071): 10,000 synthetic artifacts holding 2 GiB.

    python scripts/bench_artifact_store.py --dir DIR [--n 10000] [--total-mib 2048] [--out FILE]

DIR must be on a disk with room for about three times the total (the live sidecar, the restored copy's sidecar and
their WAL while they are written); it is created and left in place (remove it afterwards). Everything is synthetic:
random bytes (seeded), a small share holding a fixed made-up name inside a gzip stream (so the forget of that name
has real work inside the transaction-free scan), no adapter beyond the kernel's (every file ends as measured bytes:
the decode cost here is detection and the entropy of the first MiB, not a real decoder's). Measured, in seconds:
add (each and in all), list (listing of every artifact, and the live reconcile), the forget of a root (the scan
outside the transaction; the removal inside it, i.e. how long the instance's write lock is held; the sidecar sweep
after the commit; and governance.forget_partner end to end for a second name), and a restore of a checkpoint taken
before the forgets (replay plus the restored copy's sidecar). Writes a summary (no per-artifact history) as JSON.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import platform
import statistics
import sys
import time

import numpy as np

T0 = 1_800_000_000.0
NAMES = ("Quillon-bench-01", "Quillon-bench-02")


def _pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--n", type=int, default=10_000)
    parser.add_argument("--total-mib", type=int, default=2048)
    parser.add_argument("--named-every", type=int, default=1000, help="one artifact in this many names a root")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    from neuroscifi.artifacts import store as A
    from neuroscifi.instance import checkpoints, governance, store

    args.dir.mkdir(parents=True, exist_ok=False)
    path = args.dir / "bench.sqlite"
    db = store.create(path, T0)
    store.set_config(db, {"artifact_store_max_bytes": 1 << 40}, T0)
    rng = np.random.default_rng(20261126)
    size = (args.total_mib << 20) // args.n
    adds, named = [], {name: 0 for name in NAMES}
    started = time.perf_counter()
    for i in range(args.n):
        data = rng.integers(0, 256, size, dtype=np.uint8).tobytes()
        if i % args.named_every == 0:
            name = NAMES[(i // args.named_every) % 2]
            inner = gzip.compress(f"a note about {name}".encode() * 4, mtime=0)
            data = data[:size - len(inner)] + inner
            named[name] += 1
        t = time.perf_counter()
        A.add(db, data, name=f"synthetic-{i:05d}.bin", now=T0 + i)
        adds.append(time.perf_counter() - t)
    add_total = time.perf_counter() - started
    side = A.sidecar_path(db)
    stored = {"artifacts": args.n, "bytes_each": size, "bytes_total": size * args.n,
              "sidecar_file_bytes": side.stat().st_size, "instance_file_bytes": path.stat().st_size}

    t = time.perf_counter()
    listed = A.listing(db, limit=args.n)
    list_s = time.perf_counter() - t
    t = time.perf_counter()
    reconciled = A.reconcile(db, T0 + args.n + 10)
    reconcile_s = time.perf_counter() - t

    checkpoint = checkpoints.create(db, path, args.dir / "ck", T0 + args.n + 20)

    first = NAMES[0]
    t = time.perf_counter()
    scan = A.scan_root(db, first)
    scan_s = time.perf_counter() - t
    t = time.perf_counter()
    with store.write(db):
        effects = A.forget_found(db, scan, T0 + args.n + 30, None, "partner:#bench")
    inside_s = time.perf_counter() - t
    t = time.perf_counter()
    A.finish(db, scan, {"status": "forgotten", "effects": effects or {}})
    sweep_s = time.perf_counter() - t
    t = time.perf_counter()
    second = governance.forget_partner(db, NAMES[1], T0 + args.n + 40)
    partner_s = time.perf_counter() - t

    dest = args.dir / "restored.sqlite"
    t = time.perf_counter()
    restored = checkpoints.restore(db, checkpoint["id"], dest, T0 + args.n + 50)
    restore_s = time.perf_counter() - t
    db.close()

    summary = {
        "what": "artifact store cost on a large synthetic store (scripts/bench_artifact_store.py)",
        "host": {"machine": platform.machine(), "python": platform.python_version(),
                 "note": "a shared, loaded host (other lanes ran test suites meanwhile): wall-clock times are "
                         "upper bounds, single runs"},
        "store": stored,
        "add_seconds": {"total": round(add_total, 2), "mean": round(statistics.fmean(adds), 5),
                        "p50": round(_pct(adds, 0.5), 5), "p95": round(_pct(adds, 0.95), 5),
                        "max": round(max(adds), 5)},
        "list_seconds": round(list_s, 3), "listed": len(listed["items"]),
        "reconcile_seconds": round(reconcile_s, 3), "reconcile": reconciled,
        "forget_root": {"named_artifacts": named[first], "scan_outside_transaction_seconds": round(scan_s, 2),
                        "scanned": scan.scanned, "found": len(scan.hits),
                        "removal_inside_transaction_seconds": round(inside_s, 3),
                        "sidecar_sweep_after_commit_seconds": round(sweep_s, 3),
                        "removed": (effects or {}).get("artifacts_removed", 0)},
        "forget_partner_end_to_end": {"named_artifacts": named[NAMES[1]], "seconds": round(partner_s, 2),
                                      "removed": second["effects"].get("artifacts_removed", 0)},
        "restore": {"seconds": round(restore_s, 2), "artifacts_removed_by_replay": restored.get("artifacts_removed", 0),
                    "artifact_bytes_copied": restored.get("artifact_bytes_copied", 0),
                    "artifacts_missing": restored.get("artifacts_missing", 0),
                    "restored_sidecar_bytes": (args.dir / "restored.artifacts.sqlite").stat().st_size},
    }
    text = json.dumps(summary, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
