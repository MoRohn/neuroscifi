#!/usr/bin/env python3
"""ADR-066 (Wave 4 lane S): what source_derivation costs on a large individual. Offline; a temporary individual.

An individual with N completed tasks (default 10,000; model-written answers of about 2 kB) then runs the same looks
through the real modules (cycles.investigate: extraction, stamp_origins, cluster/score, consolidate.run) with the rule
off and on. Recorded per look: seconds in stamp_origins, in consolidate.run, and in the rule's own functions
(derivation_cites, _derivation_groups), and whether any of the rule's functions ran while the database was inside a
write transaction (it reads no row: its work is in memory over one task's pages and claims).

Usage: scripts/cost_source_derivation.py [--tasks N] [--looks K] [--out FILE]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _bulk(db, n, size, rnd, at):
    from neuroscifi.instance import store
    from neuroscifi.webcortex import orchestrator
    task = orchestrator.submit(db, "What is the latest Bulk release?", at, mode="fact_verification", origin="console")
    cols = [c[1] for c in db.execute("PRAGMA table_info(web_tasks)")]
    row = dict(zip(cols, db.execute("SELECT * FROM web_tasks WHERE id=?", (task["id"],)).fetchone()))
    filler = " ".join(f"Item{i} {rnd.randint(100, 99999)} was noted." for i in range(size // 15))
    result = json.dumps({"reasoning": {"generated_by": "model:x", "text": filler[:size]}})
    with store.write(db):
        for i in range(n):
            r = dict(row, id=f"bulk{i:07d}", status="completed", result=result, updated_at=at + i, finished_at=at + i)
            db.execute(f"INSERT INTO web_tasks({','.join(r)}) VALUES({','.join('?' * len(r))})", tuple(r.values()))


def _look_pages(k):
    n, v = f"Quorvane{k}", f"1.{k}.0"
    return n, [("techledger.example", f"At the moment, {n} {v} is the latest {n} release."),
               ("buildnotes.example", f"As things stand, {n} {v} is the latest {n} release."),
               ("feedmill.example", f"Today, {n} {v} is the latest {n} release, Tech Ledger said."),
               ("newsgrab.example", f"As Tech Ledger pointed out, {n} {v} is the latest {n} release."),
               ("weekinreleases.example", f"Weekly releases. {n} {v} is the latest {n} release, Tech Ledger and "
                                          "Build Notes said."),
               ("reuters.example", f"LONDON (Reuters) - {n} 2.{k}.0 is the latest {n} release."),
               ("syndicast.example", f"{n} 2.{k}.0 is the latest {n} release, Reuters observed."),
               ("askthread.example", f"{n} {v} is the latest {n} release, which brings several fixes.")]


def measure(tasks: int, looks: int) -> dict:
    from unittest import mock
    from neuroscifi.acceptance.cognition import cycles
    from neuroscifi.instance import store
    from neuroscifi.webcortex import claims, consolidate, orchestrator, state
    from neuroscifi.webcortex.types import Document
    t0 = 1_800_000_000.0
    out = {}
    with tempfile.TemporaryDirectory(prefix="nsf-genesis-s-cost-") as directory:
        db = store.create(Path(directory) / "c.sqlite", t0 - 40 * 86400.0)
        state.set_config(db, {"enabled": True, "min_domain_interval": 0.0}, t0 - 30 * 86400.0)
        started = time.perf_counter()
        _bulk(db, tasks, 2000, random.Random(66), t0 - 20 * 86400.0)
        out["build_seconds"] = time.perf_counter() - started
        out["tasks"] = db.execute("SELECT count(*) FROM web_tasks").fetchone()[0]
        timers = {"stamp_origins": [], "consolidate.run": [], "derivation_cites": [], "_derivation_groups": []}
        inside = {"derivation_cites": 0, "_derivation_groups": 0}
        current = {}

        def timed(name, fn, check=False):
            def wrapper(*a, **kw):
                if check and db.in_transaction:
                    inside[name] += 1
                s = time.perf_counter()
                try:
                    return fn(*a, **kw)
                finally:
                    current[name] = current.get(name, 0.0) + time.perf_counter() - s
            return wrapper
        with cycles._environment([None]), \
                mock.patch.object(claims, "stamp_origins", timed("stamp_origins", claims.stamp_origins)), \
                mock.patch.object(consolidate, "run", timed("consolidate.run", consolidate.run)), \
                mock.patch.object(claims, "derivation_cites", timed("derivation_cites", claims.derivation_cites, True)), \
                mock.patch.object(claims, "_derivation_groups",
                                  timed("_derivation_groups", claims._derivation_groups, True)):
            for setting in (False, True):
                state.set_config(db, {"source_derivation": setting}, t0)
                rows = {k: [] for k in timers}
                for k in range(looks):
                    at = t0 + (k + (looks if setting else 0)) * 3600.0
                    name, pages = _look_pages(k + (1000 if setting else 0))
                    task = orchestrator.submit(db, f"What is the latest {name} release?", at,
                                               mode="fact_verification", origin="console")
                    docs = [Document(url=f"http://{h}/{name.lower()}", final_url=f"http://{h}/{name.lower()}",
                                     canonical_url=f"http://{h}/{name.lower()}", domain=h, title=name, text=t,
                                     retrieved_at=at, retrieval_mode="live", published_at=at - 3600)
                            for h, t in pages]
                    current.clear()
                    cycles.investigate(db, state.task(db, task["id"]), docs, at)
                    for key in timers:
                        rows[key].append(current.get(key, 0.0))
                out["on" if setting else "off"] = {k: {"median_ms": round(1000 * statistics.median(v), 3),
                                                       "max_ms": round(1000 * max(v), 3)} for k, v in rows.items()}
        out["rule_calls_inside_a_write_transaction"] = inside
        db.close()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks", type=int, default=10_000)
    ap.add_argument("--looks", type=int, default=20)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    result = measure(a.tasks, a.looks)
    text = json.dumps(result, indent=1) + "\n"
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
