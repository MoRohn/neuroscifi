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
- neuroscifi.voice.state.snapshot() when that module exists (owner scope); absent -> voice "unknown", not false;
  an active voice session whose speech engines were last measured not ready (voice.state.engines) -> condition
  network_degraded, sources.voice "engines_unavailable" (R2-08)

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
    """Foreground when a person asked for it (orchestrator.user_initiated: console, CLI, voice, Pocket and Pocket
    voice, MCP, the owner's refreshes; T2-02, R2-02); a Pocket device's own question is 'pocket'."""
    from ..webcortex.orchestrator import user_initiated
    origin = origin or ""
    if user_initiated(origin):
        return "pocket" if origin.startswith("pocket") else "owner"
    if origin.startswith("monitor:"):
        return "monitor"
    return "autonomous"   # curiosity, refresh:*, learner, bench: work nobody is waiting on right now


def _running(task, last, contradictions, reasoning_on):
    """(activity, detail, confidence, extra secondary) for a running task from its status and newest event."""
    status, kind = task["status"], (last or {}).get("kind")
    detail = (last or {}).get("detail") or {}
    if task["cancel_requested"]:
        return "waiting", "Stopping at the next safe point (cancel requested)", 1.0, []
    if status == "discovering":
        if kind == "decision" and detail.get("needs_web") is True:
            # orchestrator: the decision that the web is needed is followed by the query plan (intent.plan)
            return "planning", "Planning the web searches", 1.0, []
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
            return "reasoning", "The local model is writing an answer from the collected evidence", 0.8, []
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


def _predictions_made(db, now):
    """(count, newest time) of predictions recorded in the last moments (web_events 'prediction': a forecast with
    its check date, predictions.record / evolution.predict), owner scope only."""
    return db.execute("SELECT count(*), max(created_at) FROM web_events WHERE kind='prediction' AND created_at>=?",
                      (now - FINISHED_WINDOW,)).fetchone()


IMAGINED_WORDS = {"simulation": ("simulation", "simulations"), "speculation": ("possibility", "possibilities"),
                  "inference": ("inference", "inferences")}


def _imagined_made(db, now):
    """(count, newest time, counts by kind) of imagined records written in the last moments (instance/imagined.py:
    simulation, speculation, inference; never evidence), owner scope only; (0, None, {}) before schema 17."""
    from ..instance import imagined
    try:
        made = imagined.counts(db, since=now - FINISHED_WINDOW)
    except sqlite3.OperationalError:
        return 0, None, {}
    return made["total"], made["newest"], {k: n for k, n in made["counts"].items() if n}


def _imagined_detail(by_kind) -> str:
    """What was written, in the past tense and by kind (review1 A14): "Recorded 1 simulation and 2 inferences"."""
    parts = []
    for kind, n in by_kind.items():
        one, many = IMAGINED_WORDS.get(kind, (kind, kind + "s"))
        parts.append(f"{n} {one if n == 1 else many}")
    listed = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"Recorded {listed}; imagined, not evidence"


def _graph_jobs(db, now):
    """Unexpired graph leases (graphs.lease): a graph is being learned, promoted or rolled back for that context."""
    try:
        return db.execute("SELECT context, holder, expires_at FROM graph_leases WHERE expires_at>? "
                          "ORDER BY context LIMIT 4", (now,)).fetchall()
    except sqlite3.OperationalError:
        return []


_INFLIGHT_DETAIL = {"interpreting": "Interpreting your request", "executing": "Running an allow-listed tool ({purpose})"}


def arbitrate(cands):
    """Contention between candidates (and between broadcast events, broadcast.py): highest rank first, then the
    most recent. The one ordering Presence and the selective broadcast share."""
    return sorted(cands, key=lambda c: (-c["rank"], -(c["since"] or 0)))


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
        from .. import background
        try:
            quiet = background.quiet(db)        # the owner's switch for everything done unasked (ADR-050)
        except (sqlite3.Error, ValueError):
            quiet = False                       # shown only: never a reason to invent a state
        return {"writes_allowed": allowed, "reason": reason, "quiet": quiet}
    return _cached(("regulation", str(db_path)), now, compute)


def _resource_level():
    """The host's memory pressure level (resources.monitor.sample: one cached /proc/meminfo read), or "unknown"."""
    try:
        from ..resources import monitor
        return monitor.sample()["level"]
    except Exception:
        return "unknown"


_IDLE_SUBSYSTEM = {"vllm": "reasoning", "asr": "voice", "tts": "voice", "searxng": "web_cortex"}


def _starting(db_path):
    """Services the idle policy is loading again right now, with what the user is told (resources.idle)."""
    if not db_path:
        return []
    try:
        from ..resources import idle
        return idle.Idle(db_path).loading()
    except Exception:
        return []


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


def _voice_engines_down(mode):
    """The speech engines a voice session in `mode` needs that were last measured not ready ("asr", "tts"); empty
    when they are ready or nothing was measured recently (voice.state.engines; no network call here)."""
    try:
        from ..voice import state as vstate
        measured = vstate.engines()
    except (ImportError, AttributeError):
        return []
    if not isinstance(measured, dict):
        return []
    needed = ("asr", "tts") if mode == "voice_text" else ("asr",)
    return [k for k in needed if measured.get(k) not in (None, "ready")]


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
    if owner:
        # Real rows only (ADR-053): a prediction recorded, an imagined record written, a graph job under its lease.
        made, at = _predictions_made(db, now)
        if made:
            cands.append(_candidate("predicting", subsystem="web_cortex", priority="background", since=at,
                                    detail=f"Recorded {made} prediction{'s' if made != 1 else ''} to check later"))
        made, at, by_kind = _imagined_made(db, now)
        if made:
            cands.append(_candidate("simulating", subsystem="imagination", priority="background", since=at,
                                    detail=_imagined_detail(by_kind)))
        for context, holder, expires in _graph_jobs(db, now):
            job = str(holder or "").split(":", 1)[0]
            job = job.removeprefix("graphs.") if job.startswith("graphs.") else "graph job"
            # every graph lease is taken for graphs.lease's default 600 s, so its start is expires - 600
            cands.append(_candidate("analyzing", subsystem="graphs", priority="foreground", since=expires - 600,
                                    detail=f"Fitting and scoring a graph for context {_short(context, 40)} ({job})"))

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
        # this process's in-flight work, then what other processes published (the research worker; ADR-053)
        try:
            published = inflight.remote(db_path, now)
        except Exception:          # noqa: BLE001 - another process's file must not take the owner's Presence down
            published = []         # (review1b R1)
        for entry in inflight.snapshot() + published:
            act = entry["activity"] if entry["activity"] in ACTIVITIES else "generating"
            if act in _INFLIGHT_DETAIL:
                detail = _INFLIGHT_DETAIL[act].format(purpose=_short(entry["purpose"], 40))
            else:
                signals["inflight_inference"] += 1
                signals["tokens_30s"] += entry["tokens"]
                detail = (f"Model call in progress ({entry['purpose']}; {entry['tokens']} tokens streamed so far)"
                          + (" in the research worker" if entry.get("process") else ""))
            cands.append(_candidate(act, subsystem=entry["subsystem"], priority=entry.get("priority") or "tool",
                                    since=entry["since"], detail=detail, task_id=entry["task_id"],
                                    secondary=[s for s in entry.get("secondary") or () if s in SECONDARY]))

        for entry in _starting(db_path):     # a service the idle policy unloaded is loading again (RD-46)
            cands.append(_candidate("waiting", subsystem=_IDLE_SUBSYSTEM.get(entry["service"], "reasoning"),
                                    priority="foreground", since=entry["since"], detail=entry["told"]))

    reg = _regulation(db, db_path, now)
    if not reg["writes_allowed"]:
        conditions.add("resource_constrained")
        sources["writes"] = "paused"       # the client names the cause: storage, not memory (ADR-053)
    if reg.get("quiet"):
        sources["background"] = "quiet"     # the owner holds background work: shown in plain words by the client
    level = _resource_level()
    sources["resources"] = level
    if level in ("elevated", "critical"):      # measured memory/GPU pressure (resources.monitor; RD-46)
        conditions.add("resource_constrained")
    if cfg is not None and cfg.enabled and (owner or signals["running"]):
        if _providers(db, now):
            conditions.add("network_degraded")
            sources["search"] = "degraded"

    voice_state = None
    if owner and voice:
        voice_state, sources["voice"] = voice_snapshot()
        if voice_state is not None:
            mode = voice_state["mode"]
            if mode == "text_only":
                if voice_state["session_active"]:   # chosen for a session; no session at all claims nothing
                    interactions.add("text_only")
            else:
                down = _voice_engines_down(mode) if voice_state["session_active"] else []
                if down:   # the session needs speech services that do not answer: not healthy (R2-08)
                    conditions.add("network_degraded")
                    sources["voice"] = "engines_unavailable"
                    sources["voice_engines_down"] = ",".join(down)
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

    cands = arbitrate(cands)
    if cands:
        primary = cands[0]
    else:
        quiet_since = max([x for x in (last_change, newest_call, last_question) if x is not None], default=None)
        held = sources.get("background") == "quiet"   # nothing will start unasked: resting at once
        resting = held or quiet_since is None or now - quiet_since >= RESTING_AFTER
        primary = _candidate("resting" if resting else "idle", subsystem="executive", priority="idle",
                             since=quiet_since,
                             detail="Quiet: nothing runs unless you ask" if held else
                             "Nothing is running; conserving resources" if resting else "Ready; nothing is running")
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


__all__ = ["ACTIVITIES", "CONDITIONS", "INTERACTIONS", "RANK", "SCHEMA", "SECONDARY", "arbitrate", "derive",
           "unavailable", "version", "voice_snapshot"]
