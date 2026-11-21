"""Monitors, the timer tick and the worker loop (ADR-027). Nothing runs without a task, a budget and an origin.

Monitors: an owner monitor starts 'active'; a monitor proposed by a paired Pocket ('pocket:<family digest>')
starts 'proposed' with a pending question (scope web:monitor:<id>) and runs only after the owner confirms it.

tick(db, now, services):
1. due active monitors -> a task each (origin 'monitor:<id>'), run inline unless queue=True. The run is compared
   with the monitor's baseline (canonical URL -> content hash, claim key -> values/polarity/status/relevance, and
   a bounded list of every claim key the monitor has ever seen). A *meaningful change* is
   (a) a changed value or polarity of a key already in the baseline (reported by a corroborated cluster or a
       primary structured source), or a supersession found by consolidation; or
   (b) a NEW key - not similar to any key the monitor has seen in any earlier run, nor a rewording of a known key
       with the same values - that is corroborated
       (>= 2 independent groups) or comes from a primary structured record with relevance >= PRIMARY_RELEVANCE.
   New URLs alone, or a different mix of search results saying the same things, never count.
   A meaningful change is recorded as a 'finding' event ({monitor_id, changes}); the baseline is updated.
2. only when config.autonomous: due predictions (refresh tasks) and the top curiosity candidates, restricted to
   items already in knowledge or owner monitors, within config.daily_autonomous (tasks, requests and bytes of
   origin curiosity/refresh in the last 24 h). Each autonomous task's request/byte budget is clamped at submit to
   the allowance left (orchestrator.submit), so the cap is passed by at most the request in flight.
3. public-cache retention (services.prune_cache, at most hourly).
4. a report of what ran, what was skipped and why.

work(db_path): claim queued tasks with a lease (UPDATE ... WHERE status='queued' AND the lease is free) and run
them; a running task whose lease expired belonged to a crashed worker and is resumed from its checkpoint (the old
worker, if it is still alive, loses the lease: its fenced writes fail and it stops without consolidating). Nothing
is claimed while instance regulation pauses writes. Change detection runs for every finished monitor task,
whoever ran it (orchestrator -> after_monitor_task); a meaningful change also opens an informational question
web:finding:<monitor>:<task> within the web question budget.
"""
from __future__ import annotations

import re
import sqlite3
import time

from ..instance import store
from ..instance.errors import Conflict, LeaseHeld, NotFound, ValidationError, WritesPaused
from . import state
from .types import MODES, TERMINAL_STATES

MIN_INTERVAL = 3600.0
DAY = 86400.0
MONITOR_COLUMNS = ("id", "topic", "plan", "mode", "interval_seconds", "budget", "baseline", "authorized_by",
                   "status", "pending_id", "created_at", "updated_at", "last_run_at", "next_run_at", "last_task_id",
                   "runs")
_POCKET = re.compile(r"^pocket:[0-9a-f]{8,64}$")
AUTONOMOUS_ORIGINS = ("curiosity", "refresh:")
PRIMARY_RELEVANCE = 0.5      # a new single-source key counts only from a primary record at least this relevant
RESTATED_KEY = 0.5           # token Jaccard at which a key with the same values restates a known key
MAX_SEEN_KEYS = 5000         # claim keys a monitor remembers across runs (oldest dropped first)
MAX_BASELINE = 2000          # URLs and claim details kept in a monitor's baseline


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
    state.require_writable(db)
    findings = orchestrator.screen(db, topic, cfg)
    if findings:
        raise ValidationError(f"topic contains private data ({', '.join(sorted({f['kind'] for f in findings}))}); "
                              "monitors are built from public text only")
    chosen = orchestrator._budget(cfg, mode, budget)
    owner = authorized_by == "owner"
    from .knowledge import question_room
    if not owner and not question_room(db, cfg):
        raise Conflict(f"the owner already has {cfg.max_open_web_questions} open web questions; a device's "
                       "monitor proposal waits until some are answered")
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
    """What a monitor run saw: canonical URL -> content hash, claim key -> values/polarity/status/primary/groups/
    relevance."""
    sources = {s["canonical_url"]: s["content_sha256"] or s["kind"] for s in state.sources(db, task_id)}
    api = {s["canonical_url"] for s in state.sources(db, task_id) if s["kind"] in ("api", "json")}
    claims = {c["id"]: c for c in state.claims(db, task_id)}
    keys = {}
    for cluster in (state.task(db, task_id)["checkpoint"] or {}).get("clusters", []):
        head = claims.get((cluster.get("supporting") or cluster.get("claims") or [None])[0])
        if head is None:
            continue
        members = [claims[c] for c in cluster["claims"] if c in claims]
        keys[cluster["key"]] = {"values": head["claim_values"], "polarity": head["polarity"],
                                "status": cluster["status"],
                                "primary": any(c["canonical_url"] in api for c in members),
                                "groups": int(cluster.get("independent_sources") or 0),
                                "relevance": max((state.finite(c["relevance"], 0.0) for c in members), default=0.0)}
    return {"urls": sources, "claims": keys}


def _similar_seen(key, seen_tokens) -> bool:
    from .consolidate import SIMILAR_KEY, _key_tokens
    tokens = _key_tokens(key)
    return bool(tokens) and any(len(tokens & t) / len(tokens | t) >= SIMILAR_KEY for t in seen_tokens if t)


def _restated(key, info, old_claims) -> bool:
    """A known statement under a reworded key (another page joined the cluster and its first claim names the key):
    the same non-empty values and polarity as a baseline key sharing at least RESTATED_KEY of its tokens."""
    from .consolidate import _key_tokens
    if not info["values"]:
        return False
    tokens, values = _key_tokens(key), sorted(info["values"])
    for old_key, before in old_claims.items():
        if sorted(before.get("values") or []) != values or before.get("polarity") != info["polarity"]:
            continue
        other = _key_tokens(old_key)
        if tokens and other and len(tokens & other) / len(tokens | other) >= RESTATED_KEY:
            return True
    return False


def compare(baseline: dict, current: dict, consolidation=None) -> dict:
    """Meaningful change between a monitor's baseline and a run's snapshot (module docstring, step 1)."""
    from .consolidate import _key_tokens
    old_urls, old_claims = baseline.get("urls") or {}, baseline.get("claims") or {}
    seen = set(baseline.get("seen_keys") or []) | set(old_claims)
    seen_tokens = [_key_tokens(k) for k in seen]
    new_urls = sorted(u for u in current["urls"] if u not in old_urls)
    changed_content = sorted(u for u, h in current["urls"].items() if u in old_urls and old_urls[u] != h)
    new_claims, changed_values = [], []
    for key, info in current["claims"].items():
        corroborated = info["status"] == "corroborated" and int(info.get("groups", 2)) >= 2
        primary = info["status"] == "single_source" and bool(info["primary"])
        if key in old_claims:
            before = old_claims[key]
            if (before["polarity"] != info["polarity"] or sorted(before["values"]) != sorted(info["values"])) and \
                    (corroborated or primary):
                changed_values.append({"key": key, "before": before["values"], "after": info["values"],
                                       "polarity": [before["polarity"], info["polarity"]]})
            continue
        if key in seen or _similar_seen(key, seen_tokens) or _restated(key, info, old_claims):
            continue        # seen in an earlier run: the search mix changed, the knowledge did not
        if corroborated or (primary and state.finite(info.get("relevance"), 0.0) >= PRIMARY_RELEVANCE):
            new_claims.append(key)
    superseded = list((consolidation or {}).get("superseded") or [])
    meaningful = bool(old_claims or old_urls) and bool(new_claims or changed_values or superseded)
    return {"meaningful": meaningful, "first_run": not (old_claims or old_urls), "new_urls": new_urls,
            "changed_content": changed_content, "new_claims": sorted(new_claims), "changed_values": changed_values,
            "superseded": superseded}


def _bounded(old: dict, new: dict, limit: int) -> dict:
    """old updated with new, the most recently seen `limit` entries kept."""
    merged = {k: v for k, v in old.items() if k not in new}
    merged.update(new)
    return dict(list(merged.items())[-limit:])


def merge_baseline(baseline: dict, current: dict) -> dict:
    seen = list(dict.fromkeys([*(baseline.get("seen_keys") or []), *(baseline.get("claims") or {}),
                               *current["claims"]]))
    return {"urls": _bounded(baseline.get("urls") or {}, current["urls"], MAX_BASELINE),
            "claims": _bounded(baseline.get("claims") or {}, current["claims"], MAX_BASELINE),
            "seen_keys": seen[-MAX_SEEN_KEYS:]}


def _after_monitor_run(db, mon, task, now) -> dict:
    """Compare a finished monitor task with the monitor's baseline (once per task). A meaningful change is a
    'finding' event and an informational question for the owner (scope web:finding:<monitor>:<task>, within the
    web question budget); a completed or partial run updates the baseline."""
    from .knowledge import ask
    if mon["baseline"].get("compared_task") == task["id"]:
        return None
    current = snapshot(db, task["id"])
    changes = compare(mon["baseline"], current, (task.get("result") or {}).get("consolidation"))
    baseline = merge_baseline(mon["baseline"], current) if task["status"] in ("completed", "partial") \
        else dict(mon["baseline"])
    baseline["compared_task"] = task["id"]
    with store.write(db):
        db.execute("UPDATE web_monitors SET baseline=? WHERE id=?", (state.dumps(baseline), mon["id"]))
        if changes["meaningful"]:
            changes["pending_id"] = ask(db, f"web:finding:{mon['id']}:{task['id']}", _finding_text(mon, changes),
                                        now)
            state.log_event(db, task["id"], "finding", {"monitor_id": mon["id"], "changes": changes}, now)
        store.bump_state_version(db)
    return changes


def _finding_text(mon, changes) -> str:
    from .knowledge import quote
    parts = []
    for item in changes.get("changed_values", [])[:3]:
        parts.append(f"«{quote(item['key'])[:80]}» changed from {', '.join(map(str, item['before'])) or '-'} to "
                     f"{', '.join(map(str, item['after'])) or '-'}")
    if changes.get("new_claims"):
        parts.append(f"{len(changes['new_claims'])} new corroborated subject(s), e.g. "
                     f"«{quote(changes['new_claims'][0])[:80]}»")
    if changes.get("superseded"):
        parts.append(f"{len(changes['superseded'])} knowledge item(s) superseded by newer versions")
    return (f"Monitor #{mon['id']} «{quote(mon['topic'])[:120]}» found a meaningful change: {'; '.join(parts)}. "
            "See the console (Research, Monitors) or `nsf web evidence` for the task; answer to dismiss.")


def after_monitor_task(db, task_id, now):
    """Change detection for a finished task of origin 'monitor:<id>' however it ran (tick, worker, resume).
    Returns the changes, or None when the task is not a finished monitor task or was already compared."""
    task = state.task(db, task_id)
    origin = str(task.get("origin") or "")
    if not origin.startswith("monitor:") or task["status"] not in TERMINAL_STATES:
        return None
    try:
        mon = _monitor(db, int(origin.split(":", 1)[1]))
    except (ValueError, NotFound):
        return None
    return _after_monitor_run(db, mon, task, now)


def findings(db, monitor_id, *, limit=5) -> list[dict]:
    """The monitor's most recent findings (newest first): task, time, changes and the owner question."""
    out = []
    for task_id, detail, at in db.execute(
            "SELECT task_id, detail, created_at FROM web_events WHERE kind='finding' AND "
            "json_extract(detail,'$.monitor_id')=? ORDER BY id DESC LIMIT ?", (monitor_id, int(limit))):
        changes = (state.loads(detail, {}) or {}).get("changes") or {}
        out.append({"task_id": task_id, "at": at, "pending_id": changes.get("pending_id"),
                    "changed_values": changes.get("changed_values", [])[:10],
                    "new_claims": changes.get("new_claims", [])[:10], "superseded": changes.get("superseded", [])[:10]})
    return out


# ---- tick --------------------------------------------------------------------------------------------------------

def _counted(origin, mcp):
    return origin == "mcp" if mcp else (origin == "curiosity" or origin.startswith("refresh:"))


_WINDOW = (f"(created_at>? OR started_at>? OR (status NOT IN ({','.join('?' * len(TERMINAL_STATES))}) AND "
           "coalesce(stage,'')!='proposed'))")


def _window(now):
    """Tasks of the daily window: created or started in the last 24 h, or still open (not a waiting proposal).
    A proposal confirmed days after it was made counts from its confirmation (it is open) and its start."""
    return _WINDOW, (now - DAY, now - DAY, *TERMINAL_STATES)


def autonomous_usage(db, now, *, mcp=False) -> dict:
    """Tasks, requests and bytes spent by autonomous research (curiosity/refresh; mcp=True: MCP-origin tasks) in
    the last 24 h (_window)."""
    tasks = requests = volume = 0
    where, params = _window(now)
    for origin, usage in db.execute(f"SELECT origin, usage FROM web_tasks WHERE {where}", params):
        if _counted(origin, mcp):
            used = state.loads(usage, {})
            tasks += 1
            requests += int(used.get("requests", 0) or 0)
            volume += int(used.get("bytes", 0) or 0)
    return {"tasks": tasks, "requests": requests, "bytes": volume}


def autonomous_remaining(db, now, cfg, *, mcp=False, exclude=None) -> dict:
    """Requests and bytes of the daily autonomous allowance (mcp=True: config.daily_mcp for MCP-origin tasks) not
    yet committed: caps minus what finished tasks of the window (_window) spent and what open ones may still spend
    (their whole budget). Proposals waiting for the owner are not counted until the owner authorises them; a
    confirmed one counts like any queued task. `exclude`: a task id left out (the one being checked)."""
    committed = {"requests": 0, "bytes": 0}
    caps = cfg.daily_mcp if mcp else cfg.daily_autonomous
    where, params = _window(now)
    for task_id, origin, status, stage, usage, budget in db.execute(
            f"SELECT id, origin, status, stage, usage, budget FROM web_tasks WHERE {where}", params):
        if not _counted(origin, mcp) or stage == "proposed" or task_id == exclude:
            continue
        used, limits = state.loads(usage, {}), state.loads(budget, {})
        for key in committed:
            spent = int(used.get(key, 0) or 0)
            if status not in TERMINAL_STATES:
                spent = max(spent, int(limits.get(f"max_{key}", 0) or 0))
            committed[key] += spent
    return {key: max(0, int(caps.get(key, 0)) - committed[key]) for key in committed}


def _over_cap(db, now, cfg, *, ignore_tasks=False):
    used = autonomous_usage(db, now)
    caps = cfg.daily_autonomous
    for key in (("requests", "bytes") if ignore_tasks else ("tasks", "requests", "bytes")):
        if used[key] >= caps.get(key, 0):
            return f"daily autonomous cap reached: {key} {used[key]} of {caps.get(key, 0)}"
    return None


def tick(db, now, services, *, max_tasks=None, queue=False) -> dict:
    """One timer pass: due monitors, then (autonomous only) predictions and curiosity. Returns what happened."""
    from . import claims, curiosity, intent, orchestrator, predictions
    report = {"at": now, "ran": [], "queued": [], "skipped": [], "findings": [], "predictions": []}
    cfg = state.get_config(db)
    if not cfg.enabled:
        report["skipped"].append({"what": "everything", "reason": state.DISABLED_MESSAGE})
        return report
    budget_left = max_tasks if max_tasks is not None else 10 ** 6

    def launch(objective, *, mode, origin, budget=None, before_run=None):
        nonlocal budget_left
        task = orchestrator.submit(db, objective, now, mode=mode, origin=origin, budget=budget)
        budget_left -= 1
        if before_run is not None:
            before_run(task)
        if queue:
            report["queued"].append({"task_id": task["id"], "origin": origin})
            return task
        try:
            done = orchestrator.run(db, task["id"], services)
        except (LeaseHeld, Conflict) as exc:   # a worker claimed the queued task first: it runs it there
            report["queued"].append({"task_id": task["id"], "origin": origin, "note": str(exc)[:200]})
            return task
        report["ran"].append({"task_id": done["id"], "origin": origin, "status": done["status"]})
        return done

    for (mid,) in db.execute("SELECT id FROM web_monitors WHERE status='active' AND next_run_at<=? "
                             "ORDER BY next_run_at, id", (now,)).fetchall():
        mon = _monitor(db, mid)
        if budget_left <= 0:
            report["skipped"].append({"monitor_id": mid, "reason": f"max_tasks {max_tasks} reached"})
            continue

        def advance(task, mon=mon, mid=mid):
            # The schedule moves on as soon as the task exists, whoever runs it and however that ends.
            with store.write(db):
                db.execute("UPDATE web_monitors SET last_run_at=?, next_run_at=?, last_task_id=?, runs=runs+1, "
                           "updated_at=? WHERE id=?", (now, now + mon["interval_seconds"], task["id"], now, mid))
                store.bump_state_version(db)

        try:
            task = launch(mon["topic"], mode=mon["mode"], origin=f"monitor:{mid}", budget=mon["budget"],
                          before_run=advance)
        except (ValidationError, WritesPaused) as exc:
            report["skipped"].append({"monitor_id": mid, "reason": str(exc)})
            continue
        if task["status"] in TERMINAL_STATES:
            for (detail,) in db.execute("SELECT detail FROM web_events WHERE task_id=? AND kind='finding'",
                                        (task["id"],)):
                changes = (state.loads(detail, {}) or {}).get("changes") or {}
                report["findings"].append({"monitor_id": mid, "task_id": task["id"], "changes": changes})
    if not cfg.autonomous:
        report["skipped"].append({"what": "predictions and curiosity",
                                  "reason": "autonomous research is off (nsf web config --autonomous)"})
        return _retention(report, services, now)
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
        try:
            done = orchestrator.run(db, action["task_id"], services)
        except (LeaseHeld, Conflict) as exc:
            report["queued"].append({"task_id": action["task_id"], "origin": "refresh", "note": str(exc)[:200]})
            continue
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
        statement = candidate["target"].get("statement") or ""
        if claims.record_template(statement) is not None:
            report["skipped"].append({"target": candidate["target"], "reason": "a structured record's template is "
                                      "not a statement to research; the record is re-read by its own refresh"})
            continue
        objective = intent.query_text(statement)      # the statement's own words (W-20), never a template frame
        if not objective:
            report["skipped"].append({"target": candidate["target"], "reason": "nothing searchable in the statement"})
            continue
        try:
            launch(objective, mode=candidate["mode"], origin="curiosity")
        except (ValidationError, WritesPaused) as exc:
            report["skipped"].append({"target": candidate["target"], "reason": str(exc)})
    return _retention(report, services, now)


def _retention(report, services, now):
    """Public-cache retention once per tick (PublicCache.maybe_prune runs at most hourly)."""
    prune = getattr(services, "prune_cache", None)
    if prune is not None:
        try:
            report["cache"] = prune(now)
        except (sqlite3.Error, OSError) as exc:
            report["skipped"].append({"what": "cache retention", "reason": f"{type(exc).__name__}: {exc}"[:300]})
    return report


# ---- worker ------------------------------------------------------------------------------------------------------

def claim_next(db, holder, now, *, lease=None):
    """Claim one runnable task: a queued task, else a running task whose lease expired or whose worker process is
    dead (same host: services.holder_alive, so a crash is recovered without waiting for the lease to expire).
    Returns (task_id, resume) or None."""
    from . import orchestrator
    from .services import holder_alive
    lease = orchestrator.LEASE_SECONDS if lease is None else lease
    with store.write(db):
        row = db.execute("SELECT id, lease_holder FROM web_tasks WHERE status='queued' AND "
                         "coalesce(stage,'')!='proposed' AND (lease_holder IS NULL OR lease_expires_at<?) "
                         "ORDER BY created_at, rowid LIMIT 1", (now,)).fetchone()
        resume = False
        if row is None:
            row = db.execute("SELECT id, lease_holder FROM web_tasks WHERE status IN ('discovering','reading',"
                             "'evaluating','synthesizing') AND lease_expires_at IS NOT NULL AND lease_expires_at<? "
                             "ORDER BY lease_expires_at LIMIT 1", (now,)).fetchone()
            resume = True
        if row is None:
            row = next((r for r in db.execute(
                "SELECT id, lease_holder FROM web_tasks WHERE status IN ('queued','discovering','reading',"
                "'evaluating','synthesizing') AND coalesce(stage,'')!='proposed' AND lease_holder IS NOT NULL "
                "AND lease_expires_at>=? ORDER BY lease_expires_at LIMIT 50", (now,)).fetchall()
                if r[1] != holder and holder_alive(r[1]) is False), None)
            if row is not None:
                resume = db.execute("SELECT status FROM web_tasks WHERE id=?", (row[0],)).fetchone()[0] != "queued"
        if row is None:
            return None
        claimed = db.execute("UPDATE web_tasks SET lease_holder=?, lease_expires_at=?, updated_at=? WHERE id=? AND "
                             "(lease_holder IS NULL OR lease_expires_at<? OR lease_holder=?)",
                             (holder, now + lease, now, row[0], now, row[1])).rowcount
        if not claimed:
            return None
        state.log_event(db, row[0], "decision", {"claimed": True, "recovered_lease": resume}, now)
    return row[0], resume


def work(db_path, *, once=False, stop=None, poll=5.0, sleep=time.sleep, clock=time.time, fixture=None,
         providers=None, max_tasks=None) -> dict:
    """Drain queued tasks with its own connection. once=True returns when nothing is runnable; otherwise polls
    every `poll` seconds until `stop` (a threading.Event) is set.

    The services (fetcher, public cache, provider registry) are built once and reused across tasks, so per-host
    request spacing and Retry-After backoffs carry over from one task to the next; they are rebuilt when the
    configuration or the feed list changes. While instance regulation pauses writes nothing is claimed (one
    decision event per pause, result["paused"] says why)."""
    from . import orchestrator, services as service_builder
    db = store.open_existing(db_path)
    done, svc, built_for, paused = [], None, None, None
    try:
        while not (stop is not None and stop.is_set()):
            if max_tasks is not None and len(done) >= max_tasks:
                break
            cfg = state.get_config(db)
            if not cfg.enabled:
                if once:
                    break
                sleep(poll)
                continue
            allowed, reason = state.write_allowed(db, db_path)
            if not allowed:
                if paused is None:
                    with store.write(db):
                        state.log_event(db, None, "decision", {"worker": "not claiming: writes paused",
                                                               "reason": str(reason)[:300]}, float(clock()))
                paused = reason
                if once:
                    break
                sleep(poll)
                continue
            paused = None
            wanted = (state.dumps(cfg.to_json()), state.dumps(state.get_feeds(db)))
            if svc is None or built_for != wanted:
                if svc is not None:
                    svc.close()
                svc, built_for = service_builder.build(db, db_path, fixture=fixture, providers=providers,
                                                       clock=clock), wanted
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
        if svc is not None:
            svc.close()
        db.close()
    out = {"processed": done}
    if paused is not None:
        out["paused"] = paused
    return out
