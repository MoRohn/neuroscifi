"""Lane W evidence (Cognitive Genesis §6.1, §6.2, §6.4, §6.7; ADR-053): measured numbers for the self-description, the
selective broadcast, the Presence states and the voice follow-up across a restart. Writes
docs/quality/evidence/genesis_workspace.json (or --out).

Everything runs offline on temporary individuals through the real modules. The measurements are functional and are
never presented as anything else: "readers" counts which existing subsystems can read a broadcast event right after
it is committed, "how quickly" is the measured read time plus the polling period each reader already has (a code
constant, not a device measurement), and "consistency" is agreement of the record with the existing surfaces at the
same state_version.

    PYTHONPATH=src python scripts/bench_workspace.py [--out PATH] [--repeat N]
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import json
from pathlib import Path
import statistics
import sqlite3
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "quality" / "evidence" / "genesis_workspace.json"


def _family(split, mechanism, ablate=()):
    from neuroscifi.acceptance import cognition
    cognition.load_plugins()
    out = cognition.run(["self_model"], split=split, mechanism=mechanism, ablate=ablate)
    fam = out["families"]["self_model"]
    return {"mechanism": mechanism, "ablate": list(ablate), "summary": fam["result"]["summary"],
            "per_probe": fam["result"]["per_probe"], "staging_problems": fam["result"]["staging_problems"],
            "cost": fam["cost"], "commit": out["environment"]["git_commit"], "dirty": out["environment"]["git_dirty"]}


def self_description_family():
    """Baseline vs the one-record describer, and each component switched off, on both splits."""
    from neuroscifi.acceptance.cognition import selfrecord
    out = {}
    for split in ("development", "held_out"):
        rows = {"baseline": _family(split, "baseline"), "self_description": _family(split, "self_description")}
        for component in selfrecord.COMPONENTS:
            rows[f"without_{component}"] = _family(split, "self_description", (component,))
        out[split] = rows
    return out


def staged_consistency():
    """The record against the existing surfaces on every staged state of both splits and seeds (same state_version)."""
    from neuroscifi.acceptance.cognition import selfmodel, selfrecord, splits
    made = []

    def factory():
        made.append(selfrecord.RecordDescriber())
        return made[-1]
    for split in ("development", "held_out"):
        selfmodel.evaluate(splits.SELF_MODEL[split], factory)
    checks = [c for d in made for c in d.checks]
    return {"records": len(checks), "comparable": sum(c["comparable"] for c in checks),
            "consistent": sum(c["comparable"] and not c["mismatches"] for c in checks),
            "field_checks": sum(c["checks"] for c in checks),
            "mismatches": [m for c in checks for m in c["mismatches"]]}


@contextmanager
def individual():
    from neuroscifi.instance import store
    from neuroscifi.webcortex import state
    with tempfile.TemporaryDirectory(prefix="nsf-workspace-") as folder:
        path = Path(folder) / "w.sqlite"
        with closing(store.create(path, time.time())) as db:
            state.set_config(db, {"enabled": True, "min_domain_interval": 0.0}, time.time())
        db = store.connect(path)
        try:
            yield db, path
        finally:
            db.close()


def consistency_over_time(repeat=3):
    """A scripted day of state changes on one individual; after each, a record is built and checked."""
    from neuroscifi import self_description as sd
    from neuroscifi.acceptance.cognition import selfmodel
    from neuroscifi.instance import evidence, executive, store
    from neuroscifi.webcortex import autonomy, orchestrator
    steps, timings = [], []
    for rep in range(repeat):
        with individual() as (db, path), selfmodel.staged_memory("ok"):
            now = time.time()
            tasks = []

            def run(name, change, level="ok", disk_full=False):
                change()
                with selfmodel.staged_memory(level), selfmodel.staged_disk(disk_full):
                    started = time.perf_counter()
                    record = sd.build(db, path, now)
                    timings.append((time.perf_counter() - started) * 1000)
                    check = sd.consistency(db, path, record, now)
                if rep == 0:
                    steps.append({"step": name, "state_version": record["state_version"],
                                  "comparable": check["comparable"], "checks": check["checks"],
                                  "mismatches": check["mismatches"], "short": sd.short(record)})

            run("fresh individual", lambda: None)
            run("owner asks two questions", lambda: tasks.extend(
                orchestrator.submit(db, f"What changed in release {i} of the workspace package?", now,
                                    origin="console")["id"] for i in range(2)))

            def running():
                with store.write(db):
                    db.execute("UPDATE web_tasks SET status='reading', started_at=?, lease_holder='w', "
                               "lease_expires_at=? WHERE id=?", (now, now + 600, tasks[0]))
            run("the worker reads sources for the first", running)

            def epoch():
                evidence.start_epoch(db, "day", now)
                for i in range(4):
                    evidence.observe(db, {"id": f"c{i}", "epoch": "day", "channel": "camera", "root": "cam",
                                          "action": 0, "bit": 0}, now)
            run("an epoch with camera readings and no reference", epoch)
            run("the executive decides and asks", lambda: executive.decide(db, "day", now, record=True))
            run("autonomy switched on", lambda: autonomy.enable(db, now))
            run("memory pressure rises", lambda: None, level="elevated")
            run("memory nearly full", lambda: None, level="critical")
            run("quiet switched on", lambda: store.set_config(db, {"quiet": True}, now))
            run("storage full", lambda: None, disk_full=True)
            run("cancel requested for the running task (it stops at its next check)", lambda: orchestrator.cancel(db, tasks[0], now))
            run("quiet switched off", lambda: store.set_config(db, {"quiet": False}, now))
    comparable = [s for s in steps if s["comparable"]]
    return {"steps": steps, "consistent_steps": sum(not s["mismatches"] for s in comparable),
            "comparable_steps": len(comparable), "total_steps": len(steps),
            "build_ms": {"median": round(statistics.median(timings), 2), "max": round(max(timings), 2),
                         "n": len(timings)}}


# ---- selective broadcast: who can read an event, and how many reach the owner -------------------------------------

READER_POLL_SECONDS = {   # how often each reader already looks (code constants; the worst-case wait after a commit)
    "presence": ("presence.routes.POLL_STEP (console long-poll re-derive)", 0.35),
    "console_questions": ("console Questions view: on open and on refresh (owner action)", None),
    "pocket_push": ("pocket.push.POLL (push collector)", 5.0),
    "self_description": ("built on request (console overview, nsf self, MCP, voice, Pocket)", None),
    "worker_wake": ("webcortex.scheduler data_version wait", 0.1),
    "state_version": ("console header (every reload)", None),
}
MIX = [   # a fixed mix of findings (prediction outcomes of finished autonomous tasks)
    [(1, "confirmed")], [(2, "confirmed")], [(3, "error")], [(4, "error"), (5, "error")], [(6, "confirmed")],
    [(7, "confirmed"), (8, "confirmed"), (9, "confirmed")], [(10, "error"), (11, "confirmed")], [(12, "confirmed")],
    [(13, "error")], [(14, "error"), (15, "error"), (16, "error")]]


def _finished(db, now, settled, i):
    from neuroscifi.instance import store
    from neuroscifi.webcortex import orchestrator, state
    task = orchestrator.submit(db, f"Zephyr compiler release note {i}", now, mode="fact_verification",
                               origin="curiosity")
    orchestrator.cancel(db, task["id"], now)
    with store.write(db):
        db.execute("UPDATE web_tasks SET checkpoint=? WHERE id=?", (state.dumps({"predictions_settled": [
            {"prediction_id": p, "hypothesis_id": None, "outcome": o} for p, o in settled]}), task["id"]))
    return task["id"]


def _readers(db, path, entry, before):
    """Which subsystems can read this offer right after its commit, and the read time of each (ms)."""
    from neuroscifi import self_description as sd
    from neuroscifi.instance import executive, store
    from neuroscifi.presence import aggregator
    pid = entry.get("pending_id")
    now = time.time()
    out = {}

    def timed(name, fn):
        started = time.perf_counter()
        seen = bool(fn())
        out[name] = {"reads_it": seen, "read_ms": round((time.perf_counter() - started) * 1000, 3)}
    aggregator._cache.clear()
    timed("presence", lambda: pid is not None and aggregator.derive(db, str(path), now)["signals"]["fresh_questions"]
          > before["fresh"])
    timed("console_questions", lambda: pid is not None and any(q["id"] == pid for q in executive.pending(db)))
    timed("pocket_push", lambda: pid is not None and db.execute(
        "SELECT 1 FROM pending WHERE id=? AND scope LIKE 'web:autonomy:%' AND created_at>?",
        (pid, now - 600)).fetchone())
    timed("self_description", lambda: (sd.value(sd.build(db, path, now), "decisions.broadcast") or [{}])[0].get(
        "at") is not None)
    timed("worker_wake", lambda: before["watch"].execute("PRAGMA data_version").fetchone()[0] != before["dv"])
    timed("state_version", lambda: store.state_version(db) > before["sv"])
    return out


def broadcast_access():
    from neuroscifi import broadcast
    from neuroscifi.instance import store
    from neuroscifi.presence import aggregator
    from neuroscifi.webcortex import autonomy
    result = {"reader_poll": {k: {"what": v[0], "period_seconds": v[1]} for k, v in READER_POLL_SECONDS.items()}}
    for mode in ("off", "log", "on"):
        with individual() as (db, path):
            store.set_config(db, {"broadcast": mode}, time.time())
            watch = sqlite3.connect(path)
            rows, offer_ms = [], []
            for i, settled in enumerate(MIX):
                tid = _finished(db, time.time(), settled, i)
                aggregator._cache.clear()
                before = {"fresh": aggregator.derive(db, str(path), time.time())["signals"]["fresh_questions"],
                          "watch": watch, "dv": watch.execute("PRAGMA data_version").fetchone()[0],
                          "sv": store.state_version(db)}
                started = time.perf_counter()
                found = autonomy.after_task(db, tid, time.time())
                offer_ms.append((time.perf_counter() - started) * 1000)
                entry = next(iter(broadcast.log(db, limit=1)), {}) if mode != "off" else {}
                if mode == "off":
                    entry = {"pending_id": found["pending_id"], "outcome": "delivered"}
                readers = _readers(db, path, entry, before)
                rows.append({"finding": i, "salience": entry.get("salience"), "outcome": entry.get("outcome"),
                             "pending_id": entry.get("pending_id"),
                             "readers": sum(r["reads_it"] for r in readers.values()), "detail": readers})
            watch.close()
            result[mode] = {"findings": len(rows), "questions_opened": sum(r["pending_id"] is not None for r in rows),
                            "readers_per_broadcast": [r["readers"] for r in rows if r["pending_id"] is not None],
                            "readers_per_local": [r["readers"] for r in rows if r["pending_id"] is None],
                            "after_task_ms_median": round(statistics.median(offer_ms), 3), "rows": rows}
    return result


def presence_states():
    """Which of the six declared-but-unused activities a scripted session shows (each from a real row)."""
    from neuroscifi.instance import graphs, imagined
    from neuroscifi.presence import activity, aggregator
    from neuroscifi.webcortex import orchestrator, state
    shown = {}
    with individual() as (db, path):
        now = time.time()

        def primary_or_any(name):
            aggregator._cache.clear()
            s = aggregator.derive(db, str(path), now)
            return name in [c["activity"] for c in s["activities"]]
        task = orchestrator.submit(db, "Was the Zephyr compiler version 4.2.0 released?", now - 60, origin="console")
        db.execute("UPDATE web_tasks SET status='discovering', stage='discovering', started_at=?, lease_holder='w', "
                   "lease_expires_at=? WHERE id=?", (now - 5, now + 600, task["id"]))
        state.log_event(db, task["id"], "decision", {"needs_web": True}, now - 1, stage="discovering")
        shown["planning"] = primary_or_any("planning")
        state.log_event(db, None, "prediction", {"prediction_id": 1}, now - 1)
        shown["predicting"] = primary_or_any("predicting")
        imagined.record(db, now=now - 1, **imagined.example(1))
        shown["simulating"] = primary_or_any("simulating")
        with graphs.lease(db, "ctx", "graphs.learn:1:1", now):
            shown["analyzing"] = primary_or_any("analyzing")
        with activity.track("interpreting", subsystem="voice", purpose="voice request", priority="communication"):
            shown["interpreting"] = primary_or_any("interpreting")
        with activity.track("executing", subsystem="reasoning", purpose="model tool x", secondary=("tool_execution",)):
            shown["executing"] = primary_or_any("executing")
        # the cost the remote in-flight read adds to one derive (no files / one file of a live other process)
        timings = {}
        for label in ("no_remote_files", "one_remote_file"):
            if label == "one_remote_file":
                import os
                folder = Path(f"{path}.resources")
                folder.mkdir(exist_ok=True)
                (folder / f"inflight-{os.getppid()}.json").write_text(json.dumps({"at": now, "entries": [
                    {"activity": "generating", "subsystem": "reasoning", "purpose": "x", "since": now, "tokens": 1}]}))
            samples = []
            for _ in range(200):
                aggregator._cache.clear()
                started = time.perf_counter()
                aggregator.derive(db, str(path), now)
                samples.append((time.perf_counter() - started) * 1000)
            timings[label] = round(statistics.median(samples), 3)
    return {"declared_unused_before": 6, "shown_now": sum(shown.values()), "states": shown,
            "derive_ms_median": timings,
            "note": "before this lane the six activities appeared only in the vocabulary (audit-runtime §6)"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--repeat", type=int, default=3)
    a = ap.parse_args(argv)
    from neuroscifi.acceptance.cognition import environment
    started = time.time()
    out = {"benchmark": "genesis-workspace", "version": 1, "lane": "W",
           "label": "offline, temporary individuals through the real modules; functional measurements only, never a "
                    "score of anything but the function named",
           "environment": environment(),
           "self_description_family": self_description_family(),
           "self_description_staged_consistency": staged_consistency(),
           "self_description_over_time": consistency_over_time(a.repeat),
           "broadcast_access": broadcast_access(),
           "presence_states": presence_states(),
           "voice_follow_after_restart": {
               "test": "tests/voice/test_follow_restart.py::test_the_answer_reaches_the_next_session_after_a_restart",
               "with_this_lane": "answer delivered to the next session: 1 of 1 (test passes)",
               "start_commit_2aee516": "answer not delivered: 0 of 1 (the same test fails with the start commit's "
                                       "voice/hub.py)",
               "devices": "unverified: speech engines and microphone are test doubles (FakeEngines)"}}
    out["wall_seconds"] = round(time.time() - started, 1)
    Path(a.out).write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(f"wrote {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
