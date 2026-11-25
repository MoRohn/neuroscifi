#!/usr/bin/env python3
"""Lane S of Cognitive Genesis (ADR-050): the owner's quiet switch and the one admission rule, measured.

  PYTHONPATH=src python scripts/bench_genesis_safety.py [--trials 5] [--poll 5.0] [--out FILE]

admission  The offline benchmark's fictional web (webcortex/bench/corpus.py) is researched once, as `nsf web bench`
           does, into a temporary individual (no evidence file is written). On copies of that individual, with
           autonomy enabled by a recorded owner switch, the timer's curiosity is admitted by the tick's rule before
           ADR-050 ('legacy') and by the unified rule, at several clock offsets after the research (the knowledge
           ages, so different items become worth refreshing). Reported per offset and rule: the subjects one tick
           admits, and the launches of eight ticks 15 minutes apart where every queued task is marked finished
           before the next tick (nothing is fetched: only admission is measured, never an outcome); every subject
           the unified rule no longer launches is listed with the reason it was refused.
honour     A research worker runs as `nsf web work` does (its own thread and connection, the default 5 s poll).
           Another connection turns quiet on at a random moment: the time until the worker records the hold, and
           until a background task queued meanwhile would have been claimed after quiet is turned off. A background
           task reading a slow site (1 s per page) is running when quiet is turned on: the time until it stops and
           the page requests sent after the switch.
cost       background.allowed and claim_next with quiet off and on (median of repeated calls).
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import platform
import random
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from neuroscifi import __version__, background
from neuroscifi.instance import store
from neuroscifi.webcortex import autonomy, orchestrator, scheduler, state
from neuroscifi.webcortex.bench import corpus as C, offline
from neuroscifi.webcortex.fixtureweb import FixtureWeb
from neuroscifi.webcortex.providers.corpus import CorpusProvider

DAY = 86400.0
TICKS, TICK_EVERY = 8, 900.0


def _copy(src: Path, dest: Path) -> sqlite3.Connection:
    with sqlite3.connect(src) as a, sqlite3.connect(dest) as b:
        a.backup(b)
    return store.open_existing(dest)


def _category(reason: str) -> str:
    for key, words in (("min_gain", "minimum"), ("cooldown", "within the last"), ("lineage", "own by-products"),
                       ("scope", "limited to items already in knowledge"), ("prediction", "due prediction"),
                       ("pool", "ten best curiosity candidates"),
                       ("template", "template"), ("claim_shape", "not researched"),
                       ("not_searchable", "nothing searchable"), ("not_enabled", "recorded owner switch")):
        if words in reason:
            return key
    return "other"


def _ticks(db, start, rule):
    """Eight ticks 15 minutes apart; queued autonomous tasks are marked finished between ticks (not run)."""
    state.set_config(db, {"tick_admission": rule}, start)
    launches = Counter()
    for n in range(TICKS):
        now = start + n * TICK_EVERY
        report = scheduler.tick(db, now, None, queue=True)
        for q in report["queued"]:
            if q["origin"] == "curiosity":
                launches[state.task(db, q["task_id"])["objective"]] += 1
        with store.write(db):
            db.execute("UPDATE web_tasks SET status='completed', finished_at=?, updated_at=? WHERE status='queued' "
                       "AND origin='curiosity'", (now + 1, now + 1))
    return launches


def admission(root: Path) -> dict:
    web = C.build_web().start()
    web.server.handle_error = lambda request, client_address: None
    try:
        bench = offline._Bench(root, web, False)
        started = time.perf_counter()
        bench.questions()
        research_s = time.perf_counter() - started
        end = bench.clock.t
        bench.close()
    finally:
        web.stop()
    base = root / "bench.sqlite"
    db = store.open_existing(base)
    knowledge = db.execute("SELECT count(*) FROM web_knowledge").fetchone()[0]
    autonomy.enable(db, end)       # the owner's recorded enable; the ticks below only queue (nothing is fetched)
    db.close()
    out = {"research_seconds": round(research_s, 1), "knowledge_items": knowledge, "questions": len(C.QUESTIONS)}
    for variant in VARIANTS:
        rows = _offsets(root, base, end, variant)
        out[variant] = {"rows": rows, "totals": {
            rule: {"one_tick": sum(len(r[f"{rule}_one_tick"]) for r in rows),
                   f"launches_{TICKS}_ticks": sum(r[f"{rule}_launches_{TICKS}_ticks"] for r in rows),
                   "relaunched_subjects": sum(r[f"{rule}_relaunched_subjects"] for r in rows)}
            for rule in ("legacy", "unified")}}
    return out


# as_researched: the individual exactly as the offline benchmark leaves it. Its seven knowledge items each carry an
# open "still holds" prediction, so an aged item is the refresh path's (predictions.check_due), not curiosity's.
# no_open_knowledge_predictions (staged, written before it was run): the same copy without those open predictions,
# as for knowledge stored before predictions existed, so aged items fall to the timer's curiosity.
# ...and_no_contested_subjects (staged, written after the first two showed the tick's pool, curiosity.rank's ten
# best, filled by contested subjects that are not knowledge): also without the contested-subject feedback rows, so
# the knowledge items reach the tick's pool.
VARIANTS = ("as_researched", "no_open_knowledge_predictions", "no_open_knowledge_predictions_and_no_contested_subjects")
OFFSETS_DAYS = (1, 7, 30, 90, 180, 365, 400, 730)


def _staged(copy, variant):
    with store.write(copy):
        if variant.startswith("no_open_knowledge_predictions"):
            copy.execute("DELETE FROM web_predictions WHERE knowledge_id IS NOT NULL AND outcome IS NULL")
        if variant.endswith("no_contested_subjects"):
            copy.execute("DELETE FROM web_feedback WHERE knowledge_id IS NULL AND "
                         "json_extract(detail,'$.contested')=1")


def _offsets(root, base, end, variant):
    rows = []
    for days in OFFSETS_DAYS:
        now = end + days * DAY
        row = {"offset_days": days}
        for rule in ("legacy", "unified"):
            copy = _copy(base, root / f"{variant}-{rule}-{days}.sqlite")
            _staged(copy, variant)
            try:
                t0 = time.perf_counter()
                admitted = autonomy.admission(copy, now, path="tick", rule=rule, limit=10)
                row[f"{rule}_admission_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                row[f"{rule}_one_tick"] = [{"key": c["key"], "info_gain": c.get("info_gain")}
                                           for c in admitted["chosen"]]
                if rule == "unified":
                    row["unified_refused"] = dict(Counter(_category(r["reason"]) for r in admitted["refused"]))
                    # ablation, not shipped: the same rule over every ranked knowledge item instead of the tick's pool
                    row["unified_if_pool_widened_one_tick"] = len(
                        [c for c in autonomy.candidates(copy, now, limit=10 ** 6)["chosen"]
                         if "knowledge_id" in c and c["kind"] != "prediction"][:10])
                launches = _ticks(copy, now, rule)
                row[f"{rule}_launches_{TICKS}_ticks"] = sum(launches.values())
                row[f"{rule}_relaunched_subjects"] = sum(1 for v in launches.values() if v > 1)
            finally:
                copy.close()
        legacy_keys = {c["key"] for c in row["legacy_one_tick"]}
        unified_keys = {c["key"] for c in row["unified_one_tick"]}
        copy = _copy(base, root / f"{variant}-why-{days}.sqlite")
        _staged(copy, variant)
        try:
            refused = {r["key"]: _category(r["reason"]) for r in
                       autonomy.admission(copy, now, path="tick", limit=10)["refused"] if "key" in r}
        finally:
            copy.close()
        row["no_longer_launched"] = sorted(({"key": k, "why": refused.get(k, "ranked below the ten admitted")}
                                            for k in legacy_keys - unified_keys), key=lambda x: x["key"])
        row["newly_launched"] = sorted(unified_keys - legacy_keys)
        rows.append(row)
    return rows


PAGES = {("slow-one.example", f"/p{n}"): f"Zephyr board {n} ships with the Zephyr compiler version 4.2.0 released "
                                         f"on 17 November 2026 for embedded targets." for n in range(6)}


def _html(text):
    return (f"<html><head><title>Zephyr</title><meta property='article:published_time' "
            f"content='2026-11-18T10:00:00Z'></head><body><article><p>{text}</p><p>{text}</p></article></body></html>")


def _individual(root: Path, name: str, now: float):
    path = root / f"{name}.sqlite"
    db = store.create(path, now)
    state.set_config(db, {"enabled": True, "min_domain_interval": 0.0, "read_ahead": 1}, now)
    return db, path


def _wait(predicate, limit):
    started = time.monotonic()
    while not predicate():
        if time.monotonic() - started > limit:
            return None
        time.sleep(0.01)
    return time.monotonic() - started


def honour(root: Path, trials: int, poll: float) -> dict:
    web = FixtureWeb().start()
    for (host, path), text in PAGES.items():
        web.add(host, path, _html(text), delay=1.0)
    provider = CorpusProvider([{"url": f"http://{h}{p}", "title": "Zephyr", "snippet": t[:120]}
                               for (h, p), t in PAGES.items()])
    rng = random.Random(50)
    hold, release, stop_s, after = [], [], [], []
    try:
        for n in range(trials):
            now = time.time()
            db, path = _individual(root, f"honour-{n}", now)
            stop = threading.Event()
            worker = threading.Thread(target=scheduler.work, args=(path,), daemon=True,
                                      kwargs={"stop": stop, "poll": poll, "sleep": stop.wait, "fixture": web,
                                              "providers": [provider], "idle": False})
            worker.start()
            try:
                time.sleep(rng.uniform(0.2, poll))                  # a random moment of the worker's poll
                other = store.connect(path)
                store.set_config(other, {"quiet": True}, time.time())
                hold.append(_wait(lambda: db.execute(
                    "SELECT 1 FROM web_events WHERE json_extract(detail,'$.worker')=?",
                    ("holding background work: quiet is on",)).fetchone(), 3 * poll))
                task = orchestrator.submit(db, "Zephyr compiler boards", time.time(), mode="quick_lookup",
                                           origin="curiosity")
                time.sleep(rng.uniform(0.2, poll))
                assert state.task(db, task["id"])["status"] == "queued", "a held task was claimed"
                store.set_config(other, {"quiet": False}, time.time())
                release.append(_wait(lambda: state.task(db, task["id"])["started_at"] is not None, 3 * poll))
                # a running background task: quiet on after its first page request reached the site
                _wait(lambda: state.task(db, task["id"])["status"] in ("completed", "partial", "failed"), 120)
                web.log.clear()
                running = orchestrator.submit(db, "Zephyr compiler version 4.2.0 boards", time.time(),
                                              mode="fact_verification", origin="curiosity")
                _wait(lambda: any(r["path"].startswith("/p") for r in list(web.log)), 60)
                switched = time.monotonic()
                store.set_config(other, {"quiet": True}, time.time())
                sent_at_switch = sum(1 for r in list(web.log) if r["path"].startswith("/p"))
                _wait(lambda: state.task(db, running["id"])["status"] in ("completed", "partial", "failed",
                                                                         "cancelled"), 60)
                stop_s.append(round(time.monotonic() - switched, 3))
                after.append(sum(1 for r in list(web.log) if r["path"].startswith("/p")) - sent_at_switch)
                done = state.task(db, running["id"])
                assert done["status"] == "partial" and background.STOPPED in (done["result"] or {}).get(
                    "limitations", []), done["status"]
                other.close()
            finally:
                stop.set()
                worker.join(30)
                db.close()
    finally:
        web.stop()
    clean = [x for x in hold if x is not None]
    freed = [x for x in release if x is not None]
    return {"poll_seconds": poll, "trials": trials,
            "hold_recorded_s": [round(x, 3) for x in hold], "hold_max_s": round(max(clean), 3) if clean else None,
            "within_one_poll": all(x is not None and x <= poll + 0.5 for x in hold),
            "released_claimed_s": [round(x, 3) if x is not None else None for x in release],
            "release_max_s": round(max(freed), 3) if freed else None,
            "running_task_stop_s": stop_s, "page_requests_sent_after_switch": after,
            "page_delay_s": 1.0}


def cost(root: Path, reps: int = 2000) -> dict:
    db, _ = _individual(root, "cost", time.time())
    for n in range(20):
        orchestrator.submit(db, f"Zephyr board {n}", time.time(), mode="quick_lookup",
                            origin="curiosity" if n % 2 else "cli")
    out = {}
    for on in (False, True):
        store.set_config(db, {"quiet": on}, time.time())
        samples = []
        for _ in range(reps):
            t0 = time.perf_counter()
            background.allowed(db, "worker.claim_background")
            samples.append(time.perf_counter() - t0)
        out[f"allowed_us_quiet_{'on' if on else 'off'}"] = round(statistics.median(samples) * 1e6, 1)
        samples = []
        for _ in range(200):
            t0 = time.perf_counter()
            scheduler.claim_next(db, "bench", time.time())     # the claim is released again below
            samples.append(time.perf_counter() - t0)
            with store.write(db):
                db.execute("UPDATE web_tasks SET lease_holder=NULL, lease_expires_at=NULL")
        out[f"claim_next_ms_quiet_{'on' if on else 'off'}"] = round(statistics.median(samples) * 1e3, 3)
    db.close()
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--trials", type=int, default=5)
    p.add_argument("--poll", type=float, default=5.0, help="the worker's poll (as `nsf web work`; default 5)")
    p.add_argument("--out", type=Path)
    p.add_argument("--only", choices=("admission", "honour", "cost"))
    a = p.parse_args(argv)
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                cwd=Path(__file__).resolve().parents[1]).stdout.strip() or None
    except OSError:
        commit = None
    out = {"schema": "nsf.genesis.safety/1", "software": __version__, "commit": commit,
           "python": platform.python_version(), "machine": platform.machine(), "measured_at": time.time()}
    with tempfile.TemporaryDirectory(prefix="nsf-genesis-s-") as tmp:
        root = Path(tmp)
        if a.only in (None, "cost"):
            out["cost"] = cost(root)
        if a.only in (None, "honour"):
            out["honour"] = honour(root, a.trials, a.poll)
        if a.only in (None, "admission"):
            (root / "adm").mkdir()
            out["admission"] = admission(root / "adm")
    text = json.dumps(out, indent=2, sort_keys=True)
    if a.out:
        a.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
