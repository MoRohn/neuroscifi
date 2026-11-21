"""Monitors, the timer tick and the worker loop (ADR-027). Nothing runs without a task, a budget and an origin.

Monitors: an owner monitor starts 'active'; a monitor proposed by a paired Pocket ('pocket:<family digest>')
starts 'proposed' with a pending question (scope web:monitor:<id>) and runs only after the owner confirms it.

tick(db, now, services):
1. due active monitors -> a task each (origin 'monitor:<id>'), run inline unless queue=True. The run is compared
   with the monitor's baseline (canonical URL -> content hash, claim key -> values/polarity/status): a
   *meaningful change* is a new corroborated cluster (or a single-source cluster from a primary structured source)
   or a changed value or polarity of a known key, or a supersession found by consolidation - not merely new URLs.
   A meaningful change is recorded as a 'finding' event ({monitor_id, changes}); the baseline is updated.
2. only when config.autonomous: due predictions (refresh tasks) and the top curiosity candidates, restricted to
   items already in knowledge or owner monitors, within config.daily_autonomous (tasks, requests and bytes of
   origin curiosity/refresh in the last 24 h).
3. a report of what ran, what was skipped and why.

work(db_path): claim queued tasks with a lease (UPDATE ... WHERE status='queued' AND the lease is free) and run
them; a running task whose lease expired belonged to a crashed worker and is resumed from its checkpoint.
"""
from __future__ import annotations

import re
import time

from ..instance import store
from ..instance.errors import Conflict, NotFound, ValidationError
from . import state
from .types import MODES, TERMINAL_STATES

MIN_INTERVAL = 3600.0
DAY = 86400.0
MONITOR_COLUMNS = ("id", "topic", "plan", "mode", "interval_seconds", "budget", "baseline", "authorized_by",
                   "status", "pending_id", "created_at", "updated_at", "last_run_at", "next_run_at", "last_task_id",
                   "runs")
_POCKET = re.compile(r"^pocket:[0-9a-f]{8,64}$")
AUTONOMOUS_ORIGINS = ("curiosity", "refresh:")


def _monitor(db, monitor_id) -> dict:
    found = db.execute(f"SELECT {','.join(MONITOR_COLUMNS)} FROM web_monitors WHERE id=?", (monitor_id,)).fetchone()
    if found is None:
        raise NotFound(f"No monitor {monitor_id}")
    out = dict(zip(MONITOR_COLUMNS, found))
    for key in ("plan", "budget", "baseline"):
        out[key] = state.loads(out[key], {} if key != "plan" else None)
    return out


def monitor_add(db, topic, now, *, interval, mode="continuous", budget=None, authorized_by, baseline=None) -> dict:
    from . import orchestrator
    from .knowledge import open_question, quote
    if not isinstance(topic, str) or not topic.strip() or len(topic) > 2000:
        raise ValidationError("topic must be a non-blank string of at most 2000 characters")
    topic = " ".join(topic.split())
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not interval >= MIN_INTERVAL:
        raise ValidationError(f"interval must be at least {MIN_INTERVAL:.0f} seconds")
    if mode not in MODES:
        raise ValidationError(f"mode must be one of {', '.join(MODES)}")
    if authorized_by != "owner" and not (isinstance(authorized_by, str) and _POCKET.match(authorized_by)):
        raise ValidationError("authorized_by must be 'owner' or 'pocket:<family digest>'")
    if baseline is not None and not isinstance(baseline, dict):
        raise ValidationError("baseline must be an object")
    cfg = state.get_config(db)
    findings = orchestrator.screen(db, topic, cfg)
    if findings:
        raise ValidationError(f"topic contains private data ({', '.join(sorted({f['kind'] for f in findings}))}); "
                              "monitors are built from public text only")
    chosen = orchestrator._budget(cfg, mode, budget)
    owner = authorized_by == "owner"
    with store.write(db):
        mid = db.execute("INSERT INTO web_monitors(topic,mode,interval_seconds,budget,baseline,authorized_by,status,"
                         "created_at,updated_at,next_run_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (topic, mode, float(interval), state.dumps(chosen.to_json()), state.dumps(baseline or {}),
                          authorized_by, "active" if owner else "proposed", now, now,
                          now if owner else None)).lastrowid
        if not owner:
            pid = open_question(db, f"web:monitor:{mid}",
                                f"A paired device proposes monitoring «{quote(topic)}» every "
                                f"{interval / 3600:g} h ({mode}). Allow it?", now)
            db.execute("UPDATE web_monitors SET pending_id=? WHERE id=?", (pid, mid))
        store.bump_state_version(db)
    return _monitor(db, mid)


def monitor_list(db, *, status=None) -> list[dict]:
    where, params = ("WHERE status=?", (status,)) if status else ("", ())
    return [_monitor(db, mid) for (mid,) in db.execute(f"SELECT id FROM web_monitors {where} ORDER BY id", params)]


def _transition(db, monitor_id, now, allowed, status):
    with store.write(db):
        mon = _monitor(db, monitor_id)
        if mon["status"] == status:
            return mon
        if mon["status"] not in allowed:
            raise Conflict(f"Monitor {monitor_id} is {mon['status']}; it cannot become {status}")
        db.execute("UPDATE web_monitors SET status=?, updated_at=?, next_run_at=? WHERE id=?",
                   (status, now, (mon["next_run_at"] or now) if status == "active" else mon["next_run_at"],
                    monitor_id))
        store.bump_state_version(db)
    return _monitor(db, monitor_id)


def monitor_pause(db, monitor_id, now) -> dict:
    return _transition(db, monitor_id, now, ("active",), "paused")


def monitor_resume(db, monitor_id, now) -> dict:
    return _transition(db, monitor_id, now, ("paused",), "active")


def monitor_stop(db, monitor_id, now) -> dict:
    return _transition(db, monitor_id, now, ("proposed", "active", "paused"), "stopped")


# ---- change detection --------------------------------------------------------------------------------------------

def snapshot(db, task_id) -> dict:
    """What a monitor run saw: canonical URL -> content hash, claim key -> values/polarity/status/primary."""
    sources = {s["canonical_url"]: s["content_sha256"] or s["kind"] for s in state.sources(db, task_id)}
    api = {s["canonical_url"] for s in state.sources(db, task_id) if s["kind"] in ("api", "json")}
    claims = {c["id"]: c for c in state.claims(db, task_id)}
    keys = {}
    for cluster in (state.task(db, task_id)["checkpoint"] or {}).get("clusters", []):
        head = claims.get((cluster.get("supporting") or cluster.get("claims") or [None])[0])
        if head is None:
            continue
        keys[cluster["key"]] = {"values": head["claim_values"], "polarity": head["polarity"],
                                "status": cluster["status"],
                                "primary": any(claims[c]["canonical_url"] in api for c in cluster["claims"]
                                               if c in claims)}
    return {"urls": sources, "claims": keys}


def compare(baseline: dict, current: dict, consolidation=None) -> dict:
    old_urls, old_claims = baseline.get("urls") or {}, baseline.get("claims") or {}
    new_urls = sorted(u for u in current["urls"] if u not in old_urls)
    changed_content = sorted(u for u, h in current["urls"].items() if u in old_urls and old_urls[u] != h)
    new_claims, changed_values = [], []
    for key, info in current["claims"].items():
        solid = info["status"] == "corroborated" or (info["status"] == "single_source" and info["primary"])
        if key not in old_claims:
            if solid:
                new_claims.append(key)
            continue
        before = old_claims[key]
        if before["polarity"] != info["polarity"] or sorted(before["values"]) != sorted(info["values"]):
            if solid:
                changed_values.append({"key": key, "before": before["values"], "after": info["values"],
                                       "polarity": [before["polarity"], info["polarity"]]})
    superseded = list((consolidation or {}).get("superseded") or [])
    meaningful = bool(old_claims or old_urls) and bool(new_claims or changed_values or superseded)
    return {"meaningful": meaningful, "first_run": not (old_claims or old_urls), "new_urls": new_urls,
            "changed_content": changed_content, "new_claims": sorted(new_claims), "changed_values": changed_values,
            "superseded": superseded}


def _after_monitor_run(db, mon, task, now) -> dict:
    current = snapshot(db, task["id"])
    changes = compare(mon["baseline"], current, (task.get("result") or {}).get("consolidation"))
    baseline = {"urls": {**(mon["baseline"].get("urls") or {}), **current["urls"]},
                "claims": {**(mon["baseline"].get("claims") or {}), **current["claims"]}}
    with store.write(db):
        if task["status"] in ("completed", "partial"):
            db.execute("UPDATE web_monitors SET baseline=? WHERE id=?", (state.dumps(baseline), mon["id"]))
        if changes["meaningful"]:
            state.log_event(db, task["id"], "finding", {"monitor_id": mon["id"], "changes": changes}, now)
        store.bump_state_version(db)
    return changes


# ---- tick --------------------------------------------------------------------------------------------------------

def autonomous_usage(db, now) -> dict:
    """Tasks, requests and bytes spent by autonomous research (curiosity/refresh) in the last 24 h."""
    tasks = requests = volume = 0
    for origin, usage in db.execute("SELECT origin, usage FROM web_tasks WHERE created_at>?", (now - DAY,)):
        if origin == "curiosity" or origin.startswith("refresh:"):
            used = state.loads(usage, {})
            tasks += 1
            requests += int(used.get("requests", 0) or 0)
            volume += int(used.get("bytes", 0) or 0)
    return {"tasks": tasks, "requests": requests, "bytes": volume}


def _over_cap(db, now, cfg, *, ignore_tasks=False):
    used = autonomous_usage(db, now)
    caps = cfg.daily_autonomous
    for key in (("requests", "bytes") if ignore_tasks else ("tasks", "requests", "bytes")):
        if used[key] >= caps.get(key, 0):
            return f"daily autonomous cap reached: {key} {used[key]} of {caps.get(key, 0)}"
    return None


def tick(db, now, services, *, max_tasks=None, queue=False) -> dict:
    """One timer pass: due monitors, then (autonomous only) predictions and curiosity. Returns what happened."""
    from . import curiosity, orchestrator, predictions
    report = {"at": now, "ran": [], "queued": [], "skipped": [], "findings": [], "predictions": []}
    cfg = state.get_config(db)
    if not cfg.enabled:
        report["skipped"].append({"what": "everything", "reason": state.DISABLED_MESSAGE})
        return report
    budget_left = max_tasks if max_tasks is not None else 10 ** 6

    def launch(objective, *, mode, origin, budget=None):
        nonlocal budget_left
        task = orchestrator.submit(db, objective, now, mode=mode, origin=origin, budget=budget)
        budget_left -= 1
        if queue:
            report["queued"].append({"task_id": task["id"], "origin": origin})
            return task
        done = orchestrator.run(db, task["id"], services)
        report["ran"].append({"task_id": done["id"], "origin": origin, "status": done["status"]})
        return done

    for (mid,) in db.execute("SELECT id FROM web_monitors WHERE status='active' AND next_run_at<=? "
                             "ORDER BY next_run_at, id", (now,)).fetchall():
        mon = _monitor(db, mid)
        if budget_left <= 0:
            report["skipped"].append({"monitor_id": mid, "reason": f"max_tasks {max_tasks} reached"})
            continue
        try:
            task = launch(mon["topic"], mode=mon["mode"], origin=f"monitor:{mid}", budget=mon["budget"])
        except ValidationError as exc:
            report["skipped"].append({"monitor_id": mid, "reason": str(exc)})
            continue
        with store.write(db):
            db.execute("UPDATE web_monitors SET last_run_at=?, next_run_at=?, last_task_id=?, runs=runs+1, "
                       "updated_at=? WHERE id=?", (now, now + mon["interval_seconds"], task["id"], now, mid))
            store.bump_state_version(db)
        if task["status"] in TERMINAL_STATES:
            changes = _after_monitor_run(db, mon, state.task(db, task["id"]), now)
            if changes["meaningful"]:
                report["findings"].append({"monitor_id": mid, "task_id": task["id"], "changes": changes})
    if not cfg.autonomous:
        report["skipped"].append({"what": "predictions and curiosity",
                                  "reason": "autonomous research is off (nsf web config --autonomous)"})
        return report
    cap = _over_cap(db, now, cfg)
    room = max(0, cfg.daily_autonomous.get("tasks", 0) - autonomous_usage(db, now)["tasks"])
    limit = 0 if cap else max(0, min(room, budget_left, predictions.MAX_REFRESHES))
    report["predictions"] = predictions.check_due(db, now, services, limit=limit)
    for action in report["predictions"]:
        if action["action"] == "skipped" and action["reason"].startswith("refresh limit"):
            action["reason"] = cap or (f"max_tasks {max_tasks} reached" if budget_left <= 0 else
                                       f"daily autonomous cap reached: tasks {cfg.daily_autonomous.get('tasks', 0)}")
            report["skipped"].append({"prediction_id": action["prediction_id"], "reason": action["reason"]})
        if action["action"] != "refresh_submitted":
            continue
        if queue:
            report["queued"].append({"task_id": action["task_id"], "origin": "refresh"})
            continue
        cap = _over_cap(db, now, cfg, ignore_tasks=True)
        if cap:
            report["skipped"].append({"task_id": action["task_id"], "reason": f"{cap}; left queued"})
            continue
        done = orchestrator.run(db, action["task_id"], services)
        budget_left -= 1
        report["ran"].append({"task_id": done["id"], "origin": "refresh", "status": done["status"]})
    for candidate in curiosity.rank(db, now, limit=10):
        if candidate["excluded"]:
            report["skipped"].append({"target": candidate["target"], "reason": candidate["reason"]})
            continue
        if "knowledge_id" not in candidate["target"]:
            report["skipped"].append({"target": candidate["target"], "reason": "autonomous research is limited to "
                                      "items already in knowledge and owner monitors; propose it instead"})
            continue
        if candidate["kind"] == "prediction":
            continue    # handled by predictions.check_due above
        cap = _over_cap(db, now, cfg)
        if cap or budget_left <= 0:
            report["skipped"].append({"target": candidate["target"], "reason": cap or f"max_tasks {max_tasks} reached"})
            continue
        objective = candidate["target"]["statement"]
        try:
            launch(objective, mode=candidate["mode"], origin="curiosity")
        except ValidationError as exc:
            report["skipped"].append({"target": candidate["target"], "reason": str(exc)})
    return report


# ---- worker ------------------------------------------------------------------------------------------------------

def claim_next(db, holder, now, *, lease=None):
    """Claim one runnable task: a queued task, else a running task whose lease expired (its worker crashed).
    Returns (task_id, resume) or None."""
    from . import orchestrator
    lease = orchestrator.LEASE_SECONDS if lease is None else lease
    with store.write(db):
        row = db.execute("SELECT id FROM web_tasks WHERE status='queued' AND coalesce(stage,'')!='proposed' "
                         "AND (lease_holder IS NULL OR lease_expires_at<?) ORDER BY created_at, rowid LIMIT 1",
                         (now,)).fetchone()
        resume = False
        if row is None:
            row = db.execute("SELECT id FROM web_tasks WHERE status IN ('discovering','reading','evaluating',"
                             "'synthesizing') AND lease_expires_at IS NOT NULL AND lease_expires_at<? "
                             "ORDER BY lease_expires_at LIMIT 1", (now,)).fetchone()
            resume = True
        if row is None:
            return None
        claimed = db.execute("UPDATE web_tasks SET lease_holder=?, lease_expires_at=?, updated_at=? WHERE id=? AND "
                             "(lease_holder IS NULL OR lease_expires_at<?)",
                             (holder, now + lease, now, row[0], now)).rowcount
        if not claimed:
            return None
        state.log_event(db, row[0], "decision", {"claimed": True, "recovered_lease": resume}, now)
    return row[0], resume


def work(db_path, *, once=False, stop=None, poll=5.0, sleep=time.sleep, clock=time.time, fixture=None,
         providers=None, max_tasks=None) -> dict:
    """Drain queued tasks with its own connection. once=True returns when nothing is runnable; otherwise polls
    every `poll` seconds until `stop` (a threading.Event) is set."""
    from . import orchestrator, services as service_builder
    db = store.open_existing(db_path)
    done = []
    try:
        while not (stop is not None and stop.is_set()):
            if max_tasks is not None and len(done) >= max_tasks:
                break
            if not state.get_config(db).enabled:
                if once:
                    break
                sleep(poll)
                continue
            svc = service_builder.build(db, db_path, fixture=fixture, providers=providers, clock=clock)
            try:
                claimed = claim_next(db, svc.holder, float(clock()))
                if claimed is None:
                    if once:
                        break
                    sleep(poll)
                    continue
                task_id, resume = claimed
                try:
                    out = orchestrator.resume(db, task_id, svc) if resume else orchestrator.run(db, task_id, svc)
                except Conflict as exc:
                    out = {"id": task_id, "status": "skipped", "error": str(exc)}
                done.append({"task_id": task_id, "status": out["status"], "resumed": resume})
            finally:
                svc.close()
    finally:
        db.close()
    return {"processed": done}
