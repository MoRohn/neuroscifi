#!/usr/bin/env python3
"""Accelerated autonomy soak: the real scheduler, worker, orchestrator and SQLite over simulated days.

    scripts/soak_sim.py [--days 7] [--out report.json] [--db-dir DIR]

Everything is the production code path (scheduler.work with its idle investigator, scheduler.tick, orchestrator,
autonomy admission and settlement, knowledge consolidation) except the outside world and the clock:
  - clock      an injected clock; the worker's poll sleep advances it, so a week runs in minutes. Each search
               advances it 2 s, so task durations and the seconds budget are exercised.
  - web        FixtureWeb serves 12 topics x 3 dated pages; a CorpusProvider stands in for the search engines.
  - owner      8 hypotheses with predictions and 2 monitors (6 h) at the start; 3 owner questions a day.
  - faults     provider outages (day 2 06:00-12:00 UTC: every search fails; day 5 00:00-03:00: one in two),
               page outages (day 4 09:00-15:00: two hosts answer 503), a worker restart every day at 12:00 UTC,
               and a crash in the middle of a task on day 3 (the process "dies" after reading; the lease
               expires and the next worker resumes it from its checkpoint).
Default D4 budgets (autonomous 20 searches, 60 pages, 1800 s a UTC day; user 400/1200). Each simulated hour it
samples DB and cache file sizes, Python heap (tracemalloc) and RSS. Checks at the end:
  budget       no UTC day's autonomous use passes a limit; the user budget is separate
  reset        autonomous research resumes on every day after the first (the budget resets at 00:00 UTC)
  drift        96 ticks a simulated day; idle decisions at least idle_interval apart; every ledger row on the UTC
               day its task started
  growth       DB size and heap grow at most linearly: the last day's growth <= 2 x the median daily growth (+1 MB)
  leases       no running task with an expired lease, no open ledger row of an ended task, no queued autonomous
               task older than a day
  decisions    every autonomous task that ran has its autonomy decision event (inputs) and a settled ledger row
Prints the report as JSON; exit 1 when a check fails.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import threading
import time
import tracemalloc

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from neuroscifi.instance import store  # noqa: E402
from neuroscifi.webcortex import autonomy, claims, evolution, orchestrator, scheduler, state, text  # noqa: E402
from neuroscifi.webcortex import services as service_builder  # noqa: E402
from neuroscifi.webcortex.fixtureweb import FixtureWeb  # noqa: E402
from neuroscifi.webcortex.providers.corpus import CorpusProvider  # noqa: E402
from neuroscifi.webcortex.search import ProviderError  # noqa: E402

DAY, HOUR = 86400.0, 3600.0
START = datetime(2026, 11, 23, 6, 0, tzinfo=timezone.utc).timestamp()   # a Monday, 06:00 UTC
TICK = 900.0
POLL = 120.0
PROJECTS = ["Zephyr compiler", "Aurora database", "Basalt web framework", "Cobalt shell", "Delta message broker",
            "Ember game engine", "Fjord file system", "Garnet tracing tool", "Helix build system",
            "Iris image codec", "Juniper package manager", "Kestrel proxy server"]
HOSTS = ["news-one.example", "tech-two.example", "blog-three.example"]


def topic_pages():
    pages = {}
    for n, project in enumerate(PROJECTS):
        slug = project.lower().replace(" ", "-")
        version = f"{n % 5 + 2}.{n % 7}.0"
        for h, host in enumerate(HOSTS):
            title = f"{project} {version} released"
            paragraphs = [f"The {project} version {version} was released on {10 + n} November 2026 by its team.",
                          f"Release notes for {project} {version} list faster startup and {h + 2} new features.",
                          f"Users of the {project} report that version {version} is stable in production."]
            pages[(host, f"/{slug}")] = (title, f"2026-11-{10 + n:02d}T09:00:00Z", paragraphs)
    return pages


def html(title, date, paragraphs):
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (f"<html><head><title>{title}</title><meta property='article:published_time' content='{date}'></head>"
            f"<body><article><h1>{title}</h1>{body}</article></body></html>")


class Clock:
    def __init__(self, now):
        self.now = float(now)
        self.lock = threading.Lock()

    def __call__(self):
        return self.now

    def advance(self, seconds):
        with self.lock:
            self.now += float(seconds)


class FlakyCorpus(CorpusProvider):
    """The search engines: fail every search during an outage, every other one during a partial outage."""

    def __init__(self, results, clock, outages):
        super().__init__(results)
        self.clock, self.outages, self.calls, self.failures = clock, outages, 0, 0

    def search(self, query, limit, *, budget=None, usage=None):
        self.calls += 1
        self.clock.advance(2.0)
        for start, end, every in self.outages:
            if start <= self.clock() < end and self.calls % every == 0:
                self.failures += 1
                raise ProviderError(self.name, "simulated outage", code="unavailable")
        return super().search(query, limit, budget=budget, usage=usage)


class Crash(BaseException):
    """The worker process dying in the middle of a task (not an Exception: nothing in the run catches it)."""


def day(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def rss_kb():
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS"):
                return int(line.split()[1])
    except OSError:
        return None
    return None


def file_bytes(path):
    return sum(p.stat().st_size for p in path.parent.glob(path.stem + "*") if p.is_file())


def seed(db, now):
    for n, project in enumerate(PROJECTS[:8]):
        version = f"{n % 5 + 2}.{n % 7}.0"
        statement = f"The {project} version {version} was released in November 2026"
        with store.write(db):
            hid, _ = evolution.open_hypothesis(
                db, key=claims.claim_key(statement), statement=statement, origin="owner",
                verification={"policy": "ADR-035", "state": "not_evaluated", "basis": "owner_hypothesis",
                              "confidence": 0.5}, claims=[], values=text.values(statement),
                polarity=claims.polarity(statement), now=now, with_prediction=False)
            evolution.predict(db, hid, now, deadline=now + 30 * DAY, probability=0.35 + 0.05 * n)
            store.bump_state_version(db)
    for project in PROJECTS[8:10]:
        scheduler.monitor_add(db, f"latest {project} release", now, interval=6 * HOUR, mode="change_detection",
                              authorized_by="owner")


def run(days=7, db_dir=None):
    tracemalloc.start()
    workdir = Path(db_dir or tempfile.mkdtemp(prefix="nsf-soak-sim-"))
    workdir.mkdir(parents=True, exist_ok=True)
    path = workdir / "individual.sqlite"
    clock = Clock(START)
    end = START + days * DAY
    pages = topic_pages()
    web = FixtureWeb().start()
    for (host, route), (title, date, paragraphs) in pages.items():
        web.add(host, route, html(title, date, paragraphs))
    outages = [(START + 1 * DAY, START + 1 * DAY + 6 * HOUR, 1),          # day 2, 06:00-12:00 UTC
               (START + 4 * DAY - 6 * HOUR, START + 4 * DAY - 3 * HOUR, 2)]   # day 5, 00:00-03:00 UTC
    provider = FlakyCorpus([{"url": f"http://{h}{r}", "title": t, "snippet": " ".join(p)[:160]}
                            for (h, r), (t, _, p) in pages.items()], clock, outages)
    page_outage = (START + 3 * DAY + 3 * HOUR, START + 3 * DAY + 9 * HOUR)  # day 4, 09:00-15:00 UTC
    down_hosts = HOSTS[:2]
    crash_at = START + 2 * DAY + 4 * HOUR                                 # day 3, 10:00 UTC

    db = store.create(path, START)
    state.set_config(db, {"enabled": True, "autonomous": True, "min_domain_interval": 0.0}, START)
    autonomy.enable(db, START, by="owner:soak")
    seed(db, START)

    samples, ticks, owner_asked, faults = [], Counter(), 0, Counter()
    marks = {"tick": START, "sample": START, "owner": START}
    services = service_builder.build(db, path, fixture=web, providers=[provider], clock=clock)
    stop = threading.Event()
    page_down = [False]

    def set_pages(down):
        if down == page_down[0]:
            return
        page_down[0] = down
        for (host, route), (title, date, paragraphs) in pages.items():
            if host in down_hosts:
                web.add(host, route, b"unavailable" if down else html(title, date, paragraphs),
                        status=503 if down else 200)
        faults["page_outage_" + ("start" if down else "end")] += 1

    def sim_sleep(seconds):
        nonlocal owner_asked
        clock.advance(seconds)
        now = clock()
        set_pages(page_outage[0] <= now < page_outage[1])
        while marks["tick"] + TICK <= now:
            marks["tick"] += TICK
            scheduler.tick(db, marks["tick"], services, queue=True)
            ticks[day(marks["tick"])] += 1
        while marks["owner"] + 8 * HOUR <= now:     # three owner questions a day
            marks["owner"] += 8 * HOUR
            project = PROJECTS[owner_asked % len(PROJECTS)]
            orchestrator.submit(db, f"What is the latest version of the {project}?", marks["owner"],
                                mode="quick_lookup", origin="console")
            owner_asked += 1
        while marks["sample"] + HOUR <= now:
            marks["sample"] += HOUR
            current, peak = tracemalloc.get_traced_memory()
            samples.append({"at": marks["sample"], "db_bytes": file_bytes(path), "heap": current, "heap_peak": peak,
                            "rss_kB": rss_kb()})
        if now >= segment_end[0]:
            stop.set()

    real_done = orchestrator._Run.done
    crashed = []

    def crash_once(self, name, **updates):
        if name == "reading" and not crashed and clock() >= crash_at:
            crashed.append({"task_id": self.id, "at": datetime.fromtimestamp(clock(), timezone.utc).isoformat()})
            raise Crash()
        return real_done(self, name, **updates)

    orchestrator._Run.done = crash_once
    segment_end = [START + 6 * HOUR]      # the first restart at 12:00 UTC, then every 24 h
    restarts = 0
    wall = time.monotonic()
    try:
        while clock() < end:
            stop.clear()
            try:
                scheduler.work(path, once=False, stop=stop, poll=POLL, sleep=sim_sleep, clock=clock, fixture=web,
                               providers=[provider])
            except Crash:
                faults["worker_crash"] += 1
                clock.advance(60)
            restarts += 1
            if stop.is_set():
                segment_end[0] = min(end, segment_end[0] + DAY)
    finally:
        orchestrator._Run.done = real_done
        services.close()
        web.stop()
    elapsed = time.monotonic() - wall
    faults["provider_search_failures"] = provider.failures
    return analyse(db, path, samples, ticks, restarts, faults, crashed, owner_asked, days, elapsed, clock())


def analyse(db, path, samples, ticks, restarts, faults, crashed, owner_asked, days, elapsed, now):
    limits = autonomy.policy(db)["autonomous_daily"]
    idle_interval = autonomy.policy(db)["idle_interval"]
    rows = db.execute("SELECT task_id, scope, day, state, used FROM autonomy_ledger").fetchall()
    tasks = {r[0]: r for r in db.execute("SELECT id, origin, status, started_at, lease_expires_at, created_at "
                                         "FROM web_tasks").fetchall()}
    per_day = defaultdict(lambda: {"autonomous": Counter(), "user": Counter(), "autonomous_tasks": 0,
                                   "user_tasks": 0})
    for task_id, scope, d, st, used in rows:
        for key, value in state.loads(used, {}).items():
            if isinstance(value, (int, float)) and key in ("searches", "pages", "seconds"):
                per_day[d][scope][key] += value
        per_day[d][f"{scope}_tasks"] += 1
    decisions = db.execute("SELECT detail, created_at FROM web_events WHERE kind='decision'").fetchall()
    kinds, idle_times = Counter(), []
    for detail, created in decisions:
        d = state.loads(detail, {})
        label = d.get("autonomy")
        if isinstance(label, str):
            kinds[label] += 1
            if label in ("queued", "nothing worth researching", "refused"):
                idle_times.append(created)
        elif isinstance(label, dict):
            kinds["inputs"] += 1
    checks = {}
    over = [f"{d} {k} {v:g} > {limits[k]:g}" for d, b in per_day.items() for k, v in b["autonomous"].items()
            if v > limits[k] + (1e-6 if k != "seconds" else 60.0)]
    checks["budget"] = "ok" if not over else "; ".join(over)
    sim_days = sorted({day(START + n * DAY) for n in range(days)})
    quiet = [d for d in sim_days[1:] if per_day[d]["autonomous"]["searches"] == 0]
    checks["reset"] = "ok" if not quiet else f"no autonomous research on {', '.join(quiet)}"
    full_days = [d for d in sim_days[1:-1]]
    bad_ticks = {d: ticks[d] for d in full_days if ticks[d] != 96}
    gaps = [b - a for a, b in zip(sorted(idle_times), sorted(idle_times)[1:])]
    wrong_day = [t for t, scope, d, *_ in rows if t in tasks and tasks[t][3] and day(tasks[t][3]) != d]
    drift = []
    if bad_ticks:
        drift.append(f"ticks per day {bad_ticks}")
    if gaps and min(gaps) < idle_interval - 1e-6:
        drift.append(f"idle decisions {min(gaps):.0f} s apart < {idle_interval:.0f} s")
    if wrong_day:
        drift.append(f"{len(wrong_day)} ledger rows on another day than their start")
    checks["drift"] = "ok" if not drift else "; ".join(drift)
    by_day = defaultdict(list)
    for s in samples:
        by_day[day(s["at"] - 1)].append(s)
    daily = [(d, v[-1]["db_bytes"], v[-1]["heap"]) for d, v in sorted(by_day.items())]
    db_growth = [b[1] - a[1] for a, b in zip(daily, daily[1:])]
    heap_growth = [b[2] - a[2] for a, b in zip(daily, daily[1:])]
    growth = []
    for name, series in (("db", db_growth), ("heap", heap_growth)):
        if len(series) >= 3 and series[-1] > 2 * max(statistics.median(series), 0) + 1_000_000:
            growth.append(f"{name} grew {series[-1]} bytes on the last day (median {statistics.median(series):.0f})")
    checks["growth"] = "ok" if not growth else "; ".join(growth)
    running = ("discovering", "reading", "evaluating", "synthesizing")
    stuck = [t for t, r in tasks.items() if r[2] in running and r[4] is not None and r[4] < now]
    open_ended = [t for t, scope, d, st, _ in rows if st == "open" and tasks.get(t, (0, 0, "gone"))[2] not in
                  ("queued", *running)]
    stale_queue = [t for t, r in tasks.items() if r[2] == "queued" and r[5] < now - DAY
                   and (r[1] == "curiosity" or r[1].startswith("refresh:"))]
    lease = []
    if stuck:
        lease.append(f"{len(stuck)} running tasks with an expired lease")
    if open_ended:
        lease.append(f"{len(open_ended)} open ledger rows of ended tasks")
    if stale_queue:
        lease.append(f"{len(stale_queue)} autonomous tasks queued for more than a day")
    checks["leases"] = "ok" if not lease else "; ".join(lease)
    autonomous_ran = [t for t, r in tasks.items() if (r[1] == "curiosity" or r[1].startswith("refresh:")) and r[3]]
    with_inputs = {task_id for task_id, detail in db.execute(
        "SELECT task_id, detail FROM web_events WHERE kind='decision' AND task_id IS NOT NULL")
        if isinstance(state.loads(detail, {}).get("autonomy"), dict)}
    settled = {t for t, scope, d, st, _ in rows if st == "settled"}
    missing = [t for t in autonomous_ran if t not in with_inputs or (tasks[t][2] not in running and t not in settled)]
    checks["decisions"] = "ok" if not missing else f"{len(missing)} autonomous tasks without inputs or settlement"
    report = {
        "simulated": {"days": days, "start": datetime.fromtimestamp(START, timezone.utc).isoformat(),
                      "end": datetime.fromtimestamp(now, timezone.utc).isoformat(), "wall_seconds": round(elapsed, 1)},
        "limits": limits,
        "days": {d: {"autonomous": dict(b["autonomous"]), "autonomous_tasks": b["autonomous_tasks"],
                     "user": dict(b["user"]), "user_tasks": b["user_tasks"], "ticks": ticks.get(d, 0)}
                 for d, b in sorted(per_day.items())},
        "tasks": {"total": len(tasks), "by_status": dict(Counter(r[2] for r in tasks.values())),
                  "by_origin": dict(Counter(r[1].split(":")[0] for r in tasks.values()))},
        "owner_questions": owner_asked,
        "decisions": dict(kinds),
        "faults": dict(faults) | {"crashed_task": crashed},
        "worker_starts": restarts,
        "growth": {"db_bytes_end_of_day": {d: b for d, b, _ in daily}, "heap_end_of_day": {d: h for d, _, h in daily},
                   "db_growth_per_day": db_growth, "heap_growth_per_day": heap_growth,
                   "rss_kB": {"first": samples[0]["rss_kB"], "last": samples[-1]["rss_kB"],
                              "max": max(s["rss_kB"] or 0 for s in samples)} if samples else {}},
        "events": db.execute("SELECT count(*) FROM web_events").fetchone()[0],
        "checks": checks,
    }
    db.close()
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--out", type=Path)
    p.add_argument("--db-dir", type=Path)
    a = p.parse_args(argv)
    report = run(a.days, a.db_dir)
    text_out = json.dumps(report, indent=2, sort_keys=True)
    if a.out:
        a.out.write_text(text_out + "\n")
    print(text_out)
    return 0 if all(v == "ok" for v in report["checks"].values()) else 1


if __name__ == "__main__":
    os.environ.setdefault("NSF_SOAK_SIM", "1")
    sys.exit(main())
