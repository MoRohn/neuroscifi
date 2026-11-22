#!/usr/bin/env python3
"""Summarise an autonomy soak: budgets per UTC day, tasks, leases, decisions, file sizes and the worker process.

    scripts/soak_report.py --db PATH [--unit nsf-soak] [--samples FILE] [--append] [--json]

Reads the instance read-only (sqlite `mode=ro`), so it is safe while the worker runs. With --unit it also asks the
user manager for the worker's state, memory and restarts (`systemctl --user show`). --append adds one sample line
to --samples (JSON lines; a timer can call it), and the report shows the trend of every sampled number: first,
last, min, max. Checks, each "ok" or a reason:
  budget       no UTC day's settled autonomous usage passes the daily limits (searches, pages)
  leases       no task in a running state whose lease expired more than 15 minutes ago (a stuck lease)
  ledger       every open autonomy_ledger row belongs to a task that is still running
  decisions    every autonomous task that started has a ledger row on the UTC day it started
Exit status 0, or 1 when a check fails.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

DEFAULT_LIMITS = {"searches": 20, "pages": 60, "seconds": 1800.0}
RUNNING = ("discovering", "reading", "evaluating", "synthesizing")
STUCK_AFTER = 900.0
UNIT_PROPS = ("ActiveState", "SubState", "MainPID", "NRestarts", "MemoryCurrent", "MemoryPeak", "CPUUsageNSec",
              "TasksCurrent", "ExecMainStartTimestamp", "Result")


def day(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _loads(text, default):
    try:
        value = json.loads(text) if text else default
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


def _autonomous(origin: str) -> bool:
    return origin == "curiosity" or origin.startswith("refresh:")


def unit_state(unit: str) -> dict:
    try:
        out = subprocess.run(["systemctl", "--user", "show", unit, "-p", ",".join(UNIT_PROPS)],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": str(exc)}
    props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    for key in ("MainPID", "NRestarts", "MemoryCurrent", "MemoryPeak", "CPUUsageNSec", "TasksCurrent"):
        try:
            props[key] = int(props[key])
        except (KeyError, ValueError):
            props[key] = None
    pid = props.get("MainPID")
    if pid:
        try:
            status = Path(f"/proc/{pid}/status").read_text()
            props["VmRSS_kB"] = int(next(line.split()[1] for line in status.splitlines() if line.startswith("VmRSS")))
        except (OSError, StopIteration, ValueError):
            props["VmRSS_kB"] = None
    return props


def files(db_path: Path) -> dict:
    out = {}
    for path in sorted(db_path.parent.glob(db_path.stem + "*")):
        if path.is_file():
            out[path.name] = path.stat().st_size
    return out


def report(db_path: Path, now: float, unit: str | None = None) -> dict:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    try:
        policy = _loads((db.execute("SELECT value FROM meta WHERE key='webcortex.autonomy'").fetchone() or [None])[0],
                        {})
        limits = DEFAULT_LIMITS | {k: v for k, v in (policy.get("autonomous_daily") or {}).items()
                                   if k in DEFAULT_LIMITS}
        tasks = db.execute("SELECT id, origin, status, created_at, started_at, finished_at, lease_holder, "
                           "lease_expires_at FROM web_tasks").fetchall()
        ledger = db.execute("SELECT task_id, scope, day, state, allowed, used FROM autonomy_ledger").fetchall()
        decisions = db.execute("SELECT detail, created_at FROM web_events WHERE kind='decision' AND task_id IS NULL "
                               "ORDER BY id").fetchall()
        events = db.execute("SELECT count(*) FROM web_events").fetchone()[0]
        knowledge = {name: db.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                     for name in ("web_claims", "web_sources", "web_knowledge")
                     if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()}
    finally:
        db.close()

    by_task = {row[0]: row for row in tasks}
    per_day = defaultdict(lambda: {"tasks": Counter(), "autonomous_used": Counter(), "user_used": Counter(),
                                   "autonomous_allowed_open": Counter()})
    for task_id, origin, status, created, started, finished, holder, expires in tasks:
        per_day[day(started or created)]["tasks"][("autonomous" if _autonomous(origin) else "user") + ":" + status] += 1
    for task_id, scope, d, st, allowed, used in ledger:
        bucket = per_day[d]
        for key, value in _loads(used, {}).items():
            if isinstance(value, (int, float)):
                bucket[f"{scope}_used"][key] += value
        if st == "open" and scope == "autonomous":
            for key, value in _loads(allowed, {}).items():
                if isinstance(value, (int, float)):
                    bucket["autonomous_allowed_open"][key] += value

    checks = {}
    over = [f"{d}: {k} {v:g} > {limits[k]:g}" for d, b in per_day.items() for k, v in b["autonomous_used"].items()
            if k in ("searches", "pages") and v > limits[k]]
    checks["budget"] = "ok" if not over else "; ".join(over)
    stuck = [t[0] for t in tasks if t[2] in RUNNING and t[7] is not None and t[7] < now - STUCK_AFTER]
    checks["leases"] = "ok" if not stuck else f"{len(stuck)} stuck: {', '.join(stuck[:5])}"
    orphan = [r[0] for r in ledger if r[3] == "open" and (r[0] not in by_task or by_task[r[0]][2] not in
                                                          ("queued", *RUNNING))]
    checks["ledger"] = "ok" if not orphan else f"{len(orphan)} open rows of ended tasks: {', '.join(orphan[:5])}"
    ledger_days = {r[0]: r[2] for r in ledger}
    missing = [t[0] for t in tasks if _autonomous(t[1]) and t[4] is not None and t[2] != "cancelled"
               and ledger_days.get(t[0]) != day(t[4])]
    checks["decisions"] = "ok" if not missing else f"{len(missing)} started autonomous tasks without a same-day " \
                                                   f"ledger row: {', '.join(missing[:5])}"

    kinds = Counter()
    last = []
    for detail, created in decisions:
        d = _loads(detail, {})
        label = d.get("autonomy") or d.get("worker") or "other"
        kinds[label if isinstance(label, str) else "other"] += 1
        last.append({"at": datetime.fromtimestamp(created, timezone.utc).isoformat(timespec="seconds"),
                     **{k: v for k, v in d.items() if k in ("autonomy", "worker", "reason", "key")}})
    out = {
        "at": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
        "db": str(db_path),
        "limits": limits,
        "days": {d: {"tasks": dict(b["tasks"]), "autonomous_used": dict(b["autonomous_used"]),
                     "user_used": dict(b["user_used"]),
                     "autonomous_open_allowance": dict(b["autonomous_allowed_open"])}
                 for d, b in sorted(per_day.items())},
        "tasks": {"total": len(tasks), "by_status": dict(Counter(t[2] for t in tasks)),
                  "by_origin": dict(Counter(t[1].split(":")[0] for t in tasks)),
                  "running": [t[0] for t in tasks if t[2] in RUNNING]},
        "ledger_rows": {"total": len(ledger), "open": sum(1 for r in ledger if r[3] == "open")},
        "events": events,
        "knowledge": knowledge,
        "decisions": {"by_kind": dict(kinds), "last": last[-8:]},
        "files": files(db_path),
        "checks": checks,
    }
    if unit:
        out["unit"] = {unit: unit_state(unit)}
    return out


def sample(rep: dict, unit: str | None) -> dict:
    row = {"at": rep["at"], "tasks": rep["tasks"]["total"], "events": rep["events"],
           "db_bytes": sum(v for k, v in rep["files"].items() if k.endswith((".sqlite", ".sqlite-wal"))),
           "all_bytes": sum(rep["files"].values()), "open_ledger": rep["ledger_rows"]["open"]}
    if unit:
        u = rep["unit"][unit]
        row |= {"rss_kB": u.get("VmRSS_kB"), "memory_current": u.get("MemoryCurrent"),
                "restarts": u.get("NRestarts"), "active": u.get("ActiveState")}
    return row


def trend(path: Path) -> dict:
    rows = []
    for line in path.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    out = {"samples": len(rows)}
    if rows:
        out["span"] = [rows[0]["at"], rows[-1]["at"]]
        for key in ("tasks", "events", "db_bytes", "all_bytes", "rss_kB", "memory_current", "open_ledger",
                    "restarts"):
            values = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
            if values:
                out[key] = {"first": values[0], "last": values[-1], "min": min(values), "max": max(values)}
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--unit", help="the worker's user unit, e.g. nsf-soak")
    p.add_argument("--samples", type=Path, help="JSON-lines file of samples")
    p.add_argument("--append", action="store_true", help="append one sample to --samples and print nothing else")
    p.add_argument("--json", action="store_true", help="print the full report as JSON")
    a = p.parse_args(argv)
    rep = report(a.db, time.time(), a.unit)
    if a.append:
        if not a.samples:
            p.error("--append needs --samples")
        fd = os.open(a.samples, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write(json.dumps(sample(rep, a.unit), sort_keys=True) + "\n")
        return 0
    if a.samples and a.samples.exists():
        rep["trend"] = trend(a.samples)
    if a.json:
        print(json.dumps(rep, indent=2, sort_keys=True))
    else:
        print(f"soak report {rep['at']}  db {rep['db']}")
        if a.unit:
            u = rep["unit"][a.unit]
            print(f"unit {a.unit}: {u.get('ActiveState')}/{u.get('SubState')} pid {u.get('MainPID')} restarts "
                  f"{u.get('NRestarts')} rss {u.get('VmRSS_kB')} kB since {u.get('ExecMainStartTimestamp')}")
        print(f"limits (autonomous, per UTC day): {rep['limits']}")
        for d, b in rep["days"].items():
            print(f"  {d}: tasks {b['tasks']}  autonomous used {b['autonomous_used']}  user used {b['user_used']}")
        print(f"tasks {rep['tasks']['by_status']}  origins {rep['tasks']['by_origin']}  running "
              f"{rep['tasks']['running']}")
        print(f"ledger {rep['ledger_rows']}  events {rep['events']}  knowledge {rep['knowledge']}")
        print(f"decisions {rep['decisions']['by_kind']}")
        for d in rep["decisions"]["last"]:
            print(f"  {d}")
        print(f"files {rep['files']}")
        if "trend" in rep:
            print(f"trend {json.dumps(rep['trend'], sort_keys=True)}")
        print(f"checks {rep['checks']}")
    return 0 if all(v == "ok" for v in rep["checks"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
