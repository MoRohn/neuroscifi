"""Cognitive State Aggregator (docs/presence/COGNITIVE_STATE_MAPPING.md).

derive() reads authoritative state and returns the multidimensional presence state. Every candidate activity names
the row or registry entry it came from; when nothing is running the state is idle (or resting after a long quiet
period), never an invented activity. Progress is a stage step ("2 of 4") only where the task row says so; no
percentages are computed.

Sources (all read-only):
- web_tasks (status, stage, origin, family, lease) and the newest web_events row per running task: sub-phase
  (searching, reading, verifying/comparing, generating, model reasoning, consolidating), contradictions found
- web_tasks finished in the last seconds: result delivered (achievement), failure (error)
- pending (owner scope): questions and authorisation requests awaiting the owner
- the inference ledger `<db stem>.inference.sqlite` (owner scope; family scope: the family's own tasks only)
- presence.activity: in-flight model calls of this process with streamed token counts
- worker health (embedded worker thread, live leases), provider circuit breakers (web_strategies), write regulation
- neuroscifi.voice.state.snapshot() when that module exists (owner scope); absent -> voice "unknown", not false

Family scope (Pocket) sees only its own tasks and global system conditions; never another family's activity,
the owner's questions, the voice session or this process's model calls.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
import time

from . import activity as inflight

SCHEMA = "nsf.presence.state/1"
ACTIVITIES = ("idle", "listening", "interpreting", "reasoning", "planning", "searching", "reading", "analyzing",
              "verifying", "comparing", "learning", "consolidating", "predicting", "simulating", "executing",
              "generating", "responding", "waiting", "recovering", "resting")
SECONDARY = ("web_research", "tool_execution", "audio_streaming", "memory_retrieval", "evidence_verification",
             "knowledge_consolidation", "background_investigation", "device_sync")
CONDITIONS = ("healthy", "busy", "resource_constrained", "network_degraded", "disconnected", "recovering",
              "awaiting_permission", "error")
INTERACTIONS = ("user_speaking", "user_typing", "responding", "user_interruption", "tool_awaiting_confirmation",
                "autonomous_research", "voice_muted", "text_only", "background_task_completed")
# Arbitration (spec): user-critical > active communication > foreground cognition > tool execution > background > idle
RANK = {"user_critical": 5, "communication": 4, "foreground": 3, "tool": 2, "background": 1, "idle": 0}
FOREGROUND_ORIGINS = ("console", "pocket", "cli")
STAGES = ("discovering", "reading", "evaluating", "synthesizing")
TERMINAL = ("completed", "partial", "failed", "cancelled")
RECENT = 30.0              # seconds of events / tokens that feed intensity
FINISHED_WINDOW = 8.0      # a delivered result is shown this long
ERROR_WINDOW = 60.0        # a failed task keeps the 'error' condition this long
FRESH_QUESTION = 900.0     # an open question younger than this is user-critical; older ones are an accent only
RESTING_AFTER = 600.0      # no activity for this long -> resting
QUEUED_HINT = 30.0         # a queued task this old with no live worker is reported as waiting for a worker
INTERRUPT_WINDOW = 3.0
CACHE_SECONDS = 10.0       # regulation and provider health are re-read at most this often
MAX_TASKS = 24
VOICE_KEYS = ("mode", "session_active", "capture_device", "mic_capturing", "output_muted", "speaking",
              "user_speaking", "interrupted_at", "updated_at")
VOICE_MODES = ("voice_text", "voice_in_text_out", "text_only")

_cache: dict = {}
_cache_lock = threading.Lock()


def _cached(key, now, compute):
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and 0 <= now - hit[0] < CACHE_SECONDS:
            return hit[1]
    value = compute()
    with _cache_lock:
        if len(_cache) > 64:
            _cache.clear()
        _cache[key] = (now, value)
    return value


def _short(text, n=80):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _origin_class(origin):
    origin = origin or ""
    if origin in FOREGROUND_ORIGINS:
        return "owner" if origin != "pocket" else "pocket"
    if origin.startswith("monitor:"):
        return "monitor"
    return "autonomous"   # curiosity, refresh:*, learner, mcp, bench: work nobody is waiting on right now


def _running(task, last, contradictions, reasoning_on):
    """(activity, detail, confidence, extra secondary) for a running task from its status and newest event."""
    status, kind = task["status"], (last or {}).get("kind")
    detail = (last or {}).get("detail") or {}
    if task["cancel_requested"]:
        return "waiting", "Stopping at the next safe point (cancel requested)", 1.0, []
    if status == "discovering":
        return "searching", "Searching for sources", 1.0, []
    if status == "reading":
        return "reading", "Reading sources", 1.0, []
    if status == "evaluating":
        if contradictions:
            return "comparing", f"Comparing sources that disagree ({contradictions} contradiction"\
                                f"{'s' if contradictions != 1 else ''})", 1.0, ["evidence_verification"]
        return "verifying", "Checking claims against independent sources", 1.0, ["evidence_verification"]
    # synthesizing: the newest event tells which part of the stage runs (orchestrator._execute order:
    # stage -> synthesized -> [model reasoning -> decision] -> consolidation -> consolidated -> finished)
    if kind == "consolidated" or (kind == "decision" and "reasoning" in detail):
        return "consolidating", "Consolidating verified findings into knowledge", 0.9, ["knowledge_consolidation"]
    if kind == "synthesized":
        if reasoning_on:
            return "reasoning", "The local model is reasoning over the verified evidence", 0.8, []
        return "consolidating", "Consolidating verified findings into knowledge", 0.8, ["knowledge_consolidation"]
    if kind == "extract" and detail.get("from_memory"):
        return "generating", "Writing the answer from memory", 1.0, ["memory_retrieval"]
    return "generating", "Writing the report", 1.0, []


def _loads(text):
    try:
        value = json.loads(text) if text else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _tasks(db, now, family):
    where, args = "", []
    if family is not None:
        where, args = " AND family=?", [family]
    cols = ("id", "objective", "mode", "status", "stage", "origin", "created_at", "updated_at", "started_at",
            "finished_at", "cancel_requested", "lease_holder", "lease_expires_at", "error")
    active = [dict(zip(cols, r)) for r in db.execute(
        f"SELECT {','.join(cols)} FROM web_tasks WHERE status NOT IN ('completed','partial','failed','cancelled')"
        f"{where} ORDER BY created_at DESC LIMIT {MAX_TASKS}", args)]
    recent = [dict(zip(cols, r)) for r in db.execute(
        f"SELECT {','.join(cols)} FROM web_tasks WHERE finished_at>=?{where} ORDER BY finished_at DESC LIMIT 8",
        [now - ERROR_WINDOW, *args])]
    last_change = db.execute(f"SELECT max(updated_at) FROM web_tasks WHERE 1=1{where}", args).fetchone()[0]
    return active, recent, last_change


def _events(db, ids, now):
    if not ids:
        return {}, {}, {}
    marks = ",".join("?" * len(ids))
    last = {}
    for task_id, kind, stage, detail, at in db.execute(
            f"SELECT task_id,kind,stage,detail,created_at FROM web_events WHERE id IN (SELECT max(id) FROM web_events "
            f"WHERE task_id IN ({marks}) GROUP BY task_id)", ids):
        last[task_id] = {"kind": kind, "stage": stage, "detail": _loads(detail), "at": at}
    counts = dict(db.execute(f"SELECT task_id,count(*) FROM web_events WHERE task_id IN ({marks}) AND created_at>=? "
                             "GROUP BY task_id", [*ids, now - RECENT]).fetchall())
    contradictions = dict(db.execute(f"SELECT task_id,count(*) FROM web_claim_links WHERE task_id IN ({marks}) AND "
                                     "kind='contradicts' GROUP BY task_id", ids).fetchall())
    return last, counts, contradictions


def _findings(db, now, family):
    """Monitor findings logged in the last moments (web_events 'finding'), owner scope only."""
    if family is not None:
        return 0
    return db.execute("SELECT count(*) FROM web_events WHERE kind='finding' AND created_at>=?",
                      (now - FINISHED_WINDOW,)).fetchone()[0]


def _ledger(db_path, now, task_ids=None):
    """(calls ended in RECENT s, completion tokens of those, newest end time) from the token ledger; None if absent."""
    if db_path is None:
        return None
    from ..inference.ledger import ledger_path
    path = ledger_path(db_path)
    if not path.exists():
        return None
    where, args = "", [now - RECENT]
    if task_ids is not None:
        if not task_ids:
            return (0, 0, None)
        where = f" AND task_id IN ({','.join('?' * len(task_ids))})"
        args += list(task_ids)
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.5)
        try:
            calls, tokens = con.execute(
                "SELECT count(*), coalesce(sum(completion_tokens),0) FROM calls WHERE "
                f"at + coalesce(latency_ms,0)/1000.0 >= ?{where}", args).fetchone()
            newest = con.execute("SELECT max(at + coalesce(latency_ms,0)/1000.0) FROM calls"
                                 + (f" WHERE 1=1{where}" if where else ""), args[1:]).fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return calls, tokens, newest


def _regulation(db, db_path, now):
    from ..instance import store

    def compute():
        allowed, reason = store.write_allowed(db, str(db_path) if db_path else None)
        return {"writes_allowed": allowed, "reason": reason}
    return _cached(("regulation", str(db_path)), now, compute)


def _providers(db, now):
    """Provider names whose circuit breaker is open (persisted by the last research run); no network call."""
    def compute():
        from ..webcortex import search, state as wstate
        registry = search.ProviderRegistry([], clock=lambda: now)
        registry.load_state(wstate.load_strategies(db))
        return sorted({row.get("provider") or row.get("name") for row in registry.status()
                       if row.get("state") == "open"})
    try:
        return _cached(("providers", _db_file(db)), now, compute)
    except Exception:
        return []


def _db_file(db):
    for _, name, file in db.execute("PRAGMA database_list").fetchall():
        if name == "main":
            return file
    return None


def _web_config(db):
    try:
        from ..webcortex import state as wstate
        return wstate.get_config(db)
    except Exception:
        return None


def _reasoning_on(db):
    try:
        from ..inference import config as iconfig
        cfg = iconfig.get_config(db)
        return cfg.configured and cfg.reasoning, cfg.configured
    except Exception:
        return False, False


def voice_snapshot():
    """(voice dict or None, source state). Lane V's module may be absent: then every voice dimension is unknown."""
    try:
        from ..voice import state as vstate
    except ImportError:
        return None, "absent"
    try:
        snap = vstate.snapshot()
    except Exception:
        return None, "error"
    if not isinstance(snap, dict):
        return None, "error"
    out = {k: snap.get(k) for k in VOICE_KEYS}
    if out["mode"] not in VOICE_MODES:
        out["mode"] = None
    for k in ("session_active", "mic_capturing", "output_muted", "speaking", "user_speaking"):
        out[k] = out[k] if isinstance(out[k], bool) else None
    for k in ("interrupted_at", "updated_at"):
        out[k] = out[k] if isinstance(out[k], (int, float)) and not isinstance(out[k], bool) else None
    out["capture_device"] = _short(out["capture_device"], 60) if out["capture_device"] else None
    return out, "ok"


def _candidate(activity, *, subsystem, priority, since=None, task_id=None, category=None, label=None, detail=None,
               step=None, secondary=(), concept=None, confidence=1.0, origin=None):
    return {"activity": activity, "concept": concept, "subsystem": subsystem, "priority": priority,
            "rank": RANK[priority], "since": since, "task_id": task_id, "category": category, "origin": origin,
            "label": label, "detail": detail, "step": step, "progress": None, "secondary": list(secondary),
            "confidence": confidence}


def derive(db, db_path, now, *, family=None, worker_alive=None, voice=True) -> dict:
    """The presence state. family: a Pocket family digest (scope 'family'); worker_alive: the console's embedded
    worker thread state (None when there is none: leases then tell whether any worker is running)."""
    owner = family is None
    cands, conditions, interactions = [], set(), set()
    signals = {"running": 0, "queued": 0, "events_30s": 0, "tokens_30s": 0, "inflight_inference": 0,
               "open_questions": 0, "fresh_questions": 0}
    sources = {"webcortex": "ok", "inference": "unknown", "voice": "unknown" if owner else "not_in_scope"}
    cfg = _web_config(db)
    if cfg is None or not cfg.enabled:
        sources["webcortex"] = "off"
    reasoning_on, inference_configured = _reasoning_on(db)
    sources["inference"] = "configured" if inference_configured else "not_configured"

    active, recent, last_change = _tasks(db, now, family)
    ids = [t["id"] for t in active]
    last, counts, contradictions = _events(db, ids, now)
    live_lease = any(t["lease_expires_at"] and t["lease_expires_at"] >= now for t in active)
    for t in active:
        origin = _origin_class(t["origin"])
        priority = "foreground" if origin in ("owner", "pocket") else "background"
        label = _short(t["objective"])
        signals["events_30s"] += counts.get(t["id"], 0)
        base = dict(subsystem="web_cortex", task_id=t["id"], category=t["mode"], label=label, origin=origin)
        secondary = ["web_research"] + (["background_investigation"] if priority == "background" else [])
        if t["stage"] == "proposed":
            continue   # a proposal waits for the owner; it is counted through its pending question
        if origin != "owner" and origin != "pocket":
            interactions.add("autonomous_research")
        if t["status"] == "queued":
            signals["queued"] += 1
            waited = now - t["created_at"]
            no_worker = not live_lease and worker_alive is not True and waited >= QUEUED_HINT
            detail = ("Queued; no research worker has claimed it yet" if no_worker else "Queued for the research worker")
            cands.append(_candidate("waiting", priority=priority, since=t["created_at"], detail=detail,
                                    secondary=secondary, **base))
            continue
        signals["running"] += 1
        if t["lease_expires_at"] is not None and t["lease_expires_at"] < now:
            conditions.add("recovering")
            cands.append(_candidate("recovering", priority=priority, since=t["lease_expires_at"],
                                    detail="The worker running this task stopped; it resumes from its checkpoint",
                                    secondary=secondary, **base))
            continue
        act, detail, confidence, extra = _running(t, last.get(t["id"]), contradictions.get(t["id"], 0), reasoning_on)
        step = {"index": STAGES.index(t["status"]) + 1, "of": len(STAGES)} if t["status"] in STAGES else None
        cands.append(_candidate(act, priority=priority, since=t["started_at"] or t["created_at"], detail=detail,
                                step=step, secondary=secondary + extra, confidence=confidence, **base))
    if signals["running"] >= 2 or (signals["running"] and signals["queued"]):
        conditions.add("busy")

    for t in recent:
        origin = _origin_class(t["origin"])
        fg = origin in ("owner", "pocket")
        base = dict(subsystem="web_cortex", task_id=t["id"], category=t["mode"], label=_short(t["objective"]),
                    origin=origin, since=t["finished_at"])
        age = now - t["finished_at"]
        if t["status"] == "failed":
            conditions.add("error")
            if age <= FINISHED_WINDOW:
                cands.append(_candidate("waiting", priority="user_critical" if fg else "background", concept="error",
                                        detail="A research task failed: " + _short(t["error"] or "no detail", 120),
                                        **base))
        elif t["status"] in ("completed", "partial") and age <= FINISHED_WINDOW:
            if not fg:
                interactions.add("background_task_completed")
            cands.append(_candidate("responding", priority="communication" if fg else "background",
                                    concept="achievement",
                                    detail="Research finished" + (" with a partial report" if t["status"] == "partial"
                                                                  else ""), **base))

    if _findings(db, now, family):
        cands.append(_candidate("learning", subsystem="web_cortex", priority="background", concept="discovery",
                                since=now, detail="A monitor found a meaningful change"))

    last_question = None
    if owner:
        rows = db.execute("SELECT id, kind, scope, created_at FROM pending WHERE resolved_at IS NULL "
                          "ORDER BY created_at DESC LIMIT 50").fetchall()
        signals["open_questions"] = len(rows)
        if rows:
            conditions.add("awaiting_permission")
            last_question = rows[0][3]
        fresh = [r for r in rows if now - r[3] <= FRESH_QUESTION]
        signals["fresh_questions"] = len(fresh)
        if fresh:
            interactions.add("tool_awaiting_confirmation")
            web = sum(1 for r in fresh if str(r[2]).startswith("web:"))
            cands.append(_candidate("waiting", subsystem="executive", priority="user_critical", concept="asking",
                                    since=fresh[0][3],
                                    detail=f"Waiting for your answer: {len(fresh)} open question"
                                           f"{'s' if len(fresh) != 1 else ''}"
                                           + (f" ({web} about research)" if web else "")))

    ledger = _ledger(db_path, now, None if owner else ids)
    newest_call = None
    if ledger is not None:
        calls, tokens, newest_call = ledger
        signals["tokens_30s"] += int(tokens or 0)
    if owner:
        for entry in inflight.snapshot():
            signals["inflight_inference"] += 1
            signals["tokens_30s"] += entry["tokens"]
            cands.append(_candidate(entry["activity"] if entry["activity"] in ACTIVITIES else "generating",
                                    subsystem=entry["subsystem"], priority="tool", since=entry["since"],
                                    detail=f"Model call in progress ({entry['purpose']}; {entry['tokens']} tokens "
                                           "streamed so far)", task_id=entry["task_id"]))

    reg = _regulation(db, db_path, now)
    if not reg["writes_allowed"]:
        conditions.add("resource_constrained")
    if cfg is not None and cfg.enabled and (owner or signals["running"]):
        if _providers(db, now):
            conditions.add("network_degraded")

    voice_state = None
    if owner and voice:
        voice_state, sources["voice"] = voice_snapshot()
        if voice_state is not None:
            mode = voice_state["mode"]
            if mode == "text_only":
                if voice_state["session_active"]:   # chosen for a session; no session at all claims nothing
                    interactions.add("text_only")
            else:
                if voice_state["output_muted"]:
                    interactions.add("voice_muted")
                if voice_state["mic_capturing"]:
                    if voice_state["user_speaking"]:
                        interactions.add("user_speaking")
                    cands.append(_candidate("listening", subsystem="voice", priority="communication",
                                            since=voice_state["updated_at"], detail="Listening (microphone on)",
                                            secondary=["audio_streaming"]))
                if voice_state["speaking"] and not voice_state["output_muted"] and mode == "voice_text":
                    interactions.add("responding")
                    cands.append(_candidate("responding", subsystem="voice", priority="communication",
                                            since=voice_state["updated_at"], detail="Speaking the answer",
                                            secondary=["audio_streaming"]))
                at = voice_state["interrupted_at"]
                if at is not None and 0 <= now - at <= INTERRUPT_WINDOW:
                    interactions.add("user_interruption")

    cands.sort(key=lambda c: (-c["rank"], -(c["since"] or 0)))
    if cands:
        primary = cands[0]
    else:
        quiet_since = max([x for x in (last_change, newest_call, last_question) if x is not None], default=None)
        resting = quiet_since is None or now - quiet_since >= RESTING_AFTER
        primary = _candidate("resting" if resting else "idle", subsystem="executive", priority="idle",
                             since=quiet_since,
                             detail="Nothing is running; conserving resources" if resting else "Ready; nothing is running")
    raw = (0.12 * signals["running"] + signals["events_30s"] / 12 + signals["tokens_30s"] / 600
           + 0.5 * signals["inflight_inference"] + 0.05 * signals["queued"])
    intensity = round(1 - math.exp(-raw), 1)
    state = {"schema": SCHEMA, "scope": "owner" if owner else "family", "primary": primary, "activities": cands[:8],
             "condition": sorted(conditions) or ["healthy"], "interaction": sorted(interactions),
             "intensity": intensity, "voice": voice_state, "sources": sources}
    state["version"] = version(state)
    state["ts"] = now
    state["signals"] = signals
    return state


def version(state) -> str:
    """A short digest of everything the client renders (not timestamps of the reply or raw counters), so an
    unchanged state answers a long-poll with 'unchanged' instead of a new body."""
    stable = {k: state[k] for k in ("scope", "primary", "activities", "condition", "interaction", "intensity",
                                    "sources")}
    voice_state = state.get("voice")
    stable["voice"] = {k: v for k, v in voice_state.items() if k != "updated_at"} if voice_state else None
    return hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()[:16]


def unavailable(scope, reason) -> dict:
    """The state when no individual can be read (e.g. Pocket without --instance-db or research switched off)."""
    primary = _candidate("idle", subsystem="executive", priority="idle", detail=reason)
    state = {"schema": SCHEMA, "scope": scope, "primary": primary, "activities": [], "condition": ["healthy"],
             "interaction": [], "intensity": 0.0, "voice": None,
             "sources": {"webcortex": "off", "inference": "not_in_scope", "voice": "not_in_scope"}}
    state["version"] = version(state)
    state["ts"] = time.time()
    state["signals"] = {}
    return state


__all__ = ["ACTIVITIES", "CONDITIONS", "INTERACTIONS", "RANK", "SCHEMA", "SECONDARY", "derive", "unavailable",
           "version", "voice_snapshot"]
