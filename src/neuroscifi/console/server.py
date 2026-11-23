"""Owner console HTTP server: static UI plus a JSON API over one instance file (API_CONTRACTS.md §6).

Security controls (SYSTEM_ARCHITECTURE.md §5):
- binds 127.0.0.1 only; there is no option to choose another address;
- a per-start session token, sent by the page as `Authorization: Bearer`, compared in constant time;
- Host must name the loopback address and port (DNS-rebinding defence), and any Origin must match it;
- POST bodies are JSON objects of at most 4 KiB (8 KiB for research questions and monitor topics, so 2000 CJK
  characters fit); GET carries no body;
- strict CSP, nosniff, no-referrer, no-store on every response, error pages included; no access logs.
Every request opens its own connection, expires due evidence first, and closes it afterwards.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import threading
import time
from uuid import uuid4
from urllib.parse import parse_qs, unquote, urlsplit

from ..instance import (beliefs, checkpoints, datasets, evidence, executive, governance, graphs, memory,
                        recall, store)
from ..instance.errors import InstanceError, ValidationError
from .. import presence
from ..webcortex import state as wstate
from ..webcortex.types import MODES, TASK_STATES, TERMINAL_STATES

BIND = "127.0.0.1"
DEFAULT_PORT = 8766
MAX_BODY = 4096
MAX_TEXT_BODY = 8192          # POST /api/web/tasks and /api/web/monitors: objective or topic of 2000 characters
TEXT_ROUTES = ("/api/web/tasks", "/api/web/monitors")
MAX_ID = 2**63 - 1             # SQLite INTEGER range: a larger id cannot exist
SWEEP_EVERY = 60.0             # seconds between expiry sweeps triggered by GET requests
GET_SWEEP_BUSY_MS = 200        # a GET never waits longer than this for the write lock
POST_BUSY_MS = 2000
WEB = Path(__file__).resolve().parent / "web"
ICON = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" '
        b'fill="#1d191b"/><circle cx="16" cy="16" r="7" fill="none" stroke="#eee9ea" stroke-width="3"/></svg>')
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/console.css": ("console.css", "text/css; charset=utf-8"),
          "/console.js": ("console.js", "text/javascript; charset=utf-8")}
# Presence Cortex browser modules, theme and the dev gallery (absolute paths; docs/presence/).
ASSETS.update(presence.assets(presence.CONSOLE))
ASSETS["/presence/gallery"] = ASSETS.pop("/presence/gallery.html")
# Voice Cortex browser modules (absolute paths; docs/voice/COGNITIVE_VOICE_BRIDGE.md).
from ..voice import assets as voice_assets  # noqa: E402
ASSETS.update(voice_assets())
HEADERS = {
    "Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
    # microphone=(self): the voice panel captures audio only after the owner's consent and an explicit mic-on.
    "Cross-Origin-Resource-Policy": "same-origin", "Permissions-Policy": "camera=(), microphone=(self), geolocation=()",
    "Content-Security-Policy": ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                                "img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"),
}
FORGET = {"evidence": governance.forget_evidence, "epoch": governance.forget_epoch,
          "partner": governance.forget_partner, "dataset": governance.forget_dataset}


# ---- request helpers -------------------------------------------------------------------------------

def _fields(body, required=(), optional=()):
    """Reject unknown or missing keys so a typo never silently falls back to a default."""
    unknown = sorted(set(body) - set(required) - set(optional))
    if unknown:
        raise ValidationError("unknown field(s): " + ", ".join(unknown))
    missing = [key for key in required if key not in body]
    if missing:
        raise ValidationError("missing field(s): " + ", ".join(missing))
    return body


def _int(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{name} must be an integer")
    return value


def _query(query, allowed):
    unknown = sorted(set(query) - set(allowed))
    if unknown:
        raise ValidationError("unknown query parameter(s): " + ", ".join(unknown))
    return {key: values[-1] for key, values in query.items()}


def _number(text, name, kind=float):
    if text is None:
        return None
    try:
        return kind(text)
    except ValueError:
        raise ValidationError(f"{name} must be a number") from None


# ---- route handlers: (ctx, params, query, body) -> JSON value -----------------------------------------

def _inventory(c, p, q, b):
    return {**store.inventory(c.db), "regulation": store.regulation(c.db, c.path)}


def _epochs(c, p, q, b):
    rows = c.db.execute(
        "SELECT e.name, e.created_at, e.note, count(v.seq), coalesce(sum(v.consent='learn'),0), "
        "coalesce(sum(v.consent='memory_only'),0), coalesce(sum(v.retention='promoted'),0), "
        "(SELECT count(*) FROM tombstones t WHERE t.epoch=e.name), "
        "(SELECT context FROM epoch_contexts c WHERE c.epoch=e.name), "
        "(SELECT count(*) FROM epoch_streams s WHERE s.epoch=e.name) "
        "FROM epochs e LEFT JOIN evidence v ON v.epoch=e.name GROUP BY e.name ORDER BY e.created_at, e.name")
    keys = ("name", "created_at", "note", "evidence", "learning", "memory_only", "promoted", "forgotten", "context",
            "streams")
    return [dict(zip(keys, row)) for row in rows]


def _start_epoch(c, p, q, b):
    _fields(b, ("name",), ("note", "context"))
    return evidence.start_epoch(c.db, b["name"], c.now, b.get("note"), b.get("context"))


def _explain(c, p, q, b):
    return beliefs.explain(c.db, p[0])


def _decision(c, p, q, b):
    return executive.decide(c.db, p[0], c.now, record=False, path=c.path)


def _decide(c, p, q, b):
    _fields(b)
    return executive.decide(c.db, p[0], c.now, record=True, path=c.path)


def _evidence(c, p, q, b):
    q = _query(q, ("channel", "root", "limit"))
    return evidence.list_evidence(c.db, p[0], channel=q.get("channel") or None, root=q.get("root") or None,
                                  limit=_number(q.get("limit", "500"), "limit", int))


def _observe(c, p, q, b):
    return evidence.observe(c.db, b, c.now, path=c.path)


def _pending(c, p, q, b):
    q = _query(q, ("epoch", "all"))
    return executive.pending(c.db, epoch=q.get("epoch") or None, include_resolved=q.get("all") in ("1", "true"))


def _resolve(c, p, q, b):
    _fields(b, ("resolution",), ("confirm",))
    confirm = b.get("confirm")
    if "confirm" in b and not isinstance(confirm, bool):
        raise ValidationError("confirm must be true or false")
    return executive.resolve(c.db, int(p[0]), b["resolution"], c.now, confirm=confirm)


def _match(c, p, q, b):
    q = _query(q, ("last",))
    return beliefs.match_epochs(c.db, p[0], last=_number(q.get("last", str(beliefs.MATCH_EVENTS)), "last", int))


def _forget(c, p, q, b):
    _fields(b, ("kind", "target"))
    if b["kind"] not in FORGET:
        raise ValidationError("kind must be one of " + ", ".join(FORGET))
    target = _int(b["target"], "target") if b["kind"] == "dataset" else b["target"]
    if b["kind"] != "dataset" and not isinstance(target, str):
        raise ValidationError("target must be a string")
    return FORGET[b["kind"]](c.db, target, c.now)


def _promote_evidence(c, p, q, b):
    _fields(b, ("id",))
    if not isinstance(b["id"], str):
        raise ValidationError("id must be a string")
    return governance.promote(c.db, b["id"], c.now)


def _graphs(c, p, q, b):
    q = _query(q, ("context",))
    return graphs.listing(c.db, q.get("context") or None)


def _graph(c, p, q, b):
    return graphs.show(c.db, int(p[0]))


def _graph_learn(c, p, q, b):
    _fields(b, ("context", "train", "validation"))
    revision = graphs.learn(c.db, b["context"], c.now, train=_int(b["train"], "train"),
                            validation=_int(b["validation"], "validation"), holder=f"console:{uuid4()}")
    return {"revision_id": revision}


def _graph_evaluate(c, p, q, b):
    _fields(b)
    return graphs.evaluate(c.db, int(p[0]), c.now)


def _graph_promote(c, p, q, b):
    _fields(b)
    return graphs.promote(c.db, int(p[0]), c.now)


def _graph_reject(c, p, q, b):
    _fields(b, ("reason",))
    return graphs.reject(c.db, int(p[0]), c.now, b["reason"])


def _coactivation(c, p, q, b):
    _fields(b, ("dataset_id",), ("lags", "top"))
    lags = b.get("lags", list(graphs.COACTIVATION_LAGS))
    if not isinstance(lags, list) or not lags or not all(isinstance(x, int) and not isinstance(x, bool) for x in lags):
        raise ValidationError("lags must be a non-empty list of integers")
    top = _int(b.get("top", 20), "top")
    return graphs.functional_map(c.db, _int(b["dataset_id"], "dataset_id"), c.now, lags=lags, top=top)


def _datasets(c, p, q, b):
    q = _query(q, ("context",))
    return datasets.listing(c.db, q.get("context") or None)


def _simulate(c, p, q, b):
    _fields(b, ("context", "seed"), ("world",))
    return datasets.simulate(c.db, b["context"], c.now, seed=b["seed"], world=b.get("world", "a"))


def _recall(c, p, q, b):
    _fields(b, ("dataset_id",), ("contexts",))
    contexts = b.get("contexts")
    if contexts is not None and (not isinstance(contexts, list) or not all(isinstance(x, str) for x in contexts)):
        raise ValidationError("contexts must be a list of context names")
    return recall.run(c.db, _int(b["dataset_id"], "dataset_id"), c.now, contexts=contexts or None, record=True)


def _checkpoints(c, p, q, b):
    return checkpoints.listing(c.db)


def _checkpoint_create(c, p, q, b):
    _fields(b, (), ("policy",))
    return checkpoints.create(c.db, c.path, checkpoints.default_dir(c.path), c.now, policy=b.get("policy", "cold"))


def _checkpoint_prune(c, p, q, b):
    _fields(b, ("keep",))
    return checkpoints.prune(c.db, c.now, _int(b["keep"], "keep"), db_path=c.path)


def _checkpoint_rebuild(c, p, q, b):
    _fields(b, (), ("keep", "policy"))
    return checkpoints.rebuild_stale(c.db, c.path, checkpoints.default_dir(c.path), c.now,
                                     policy=b.get("policy", "cold"), keep=_int(b.get("keep", 7), "keep"))


def _checkpoint_verify(c, p, q, b):
    _fields(b)
    return checkpoints.verify(c.db, int(p[0]), c.now)


def _memory(c, p, q, b):
    q = _query(q, ("kind", "id", "epoch", "context", "channel", "root", "since", "until", "limit"))
    if "kind" not in q:
        raise ValidationError("kind is required (one of " + ", ".join(memory.KINDS) + ")")
    filters = {k: q.get(k) or None for k in ("id", "epoch", "context", "channel", "root")}
    return memory.retrieve(c.db, q["kind"], **filters, since=_number(q.get("since"), "since"),
                           until=_number(q.get("until"), "until"), limit=_number(q.get("limit", "100"), "limit", int))


# ---- Web Cortex (ARCHITECTURE.md §4.5): research tasks, knowledge, monitors, switches --------------------------

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
WEB_SWITCHES = ("enabled", "autonomous", "pocket_research", "browser_rendering")
WORKER_POLL = 5.0      # seconds between idle polls of the opt-in embedded worker (as `nsf web work`)
WORKER_RETRY = 5.0     # first back-off after an unexpected worker error; doubles per consecutive error
WORKER_RETRY_MAX = 60.0


def _task_id(text):
    if not UUID.match(text):
        raise ValidationError("task id must be a lowercase UUID")
    return text


def _note(b):
    note = b.get("note", "")
    if not isinstance(note, str) or len(note) > 1024:
        raise ValidationError("note must be a string of at most 1024 characters")
    return note


def _web_config(cfg):
    return {**{k: getattr(cfg, k) for k in WEB_SWITCHES}, "providers": list(cfg.providers),
            "engines": list(cfg.engines), "openwebsearch_url": cfg.openwebsearch_url}


def _provider_health(db, cfg, now):
    """Provider and engine health as last persisted by a run (procedural memory); no network call is made."""
    from ..webcortex import search
    registry = search.ProviderRegistry([], clock=lambda: now)
    registry.load_state(wstate.load_strategies(db))
    seen = {row["provider"] for row in registry.status()}
    rows = [{k: row.get(k) for k in ("name", "provider", "engine", "state", "health", "successes", "failures",
                                     "last_error", "last_ok_at", "last_failure_at", "since_ok", "p50_latency")}
            for row in registry.status()]
    rows += [{"name": name, "provider": name, "engine": None, "state": "unused", "health": "unused", "successes": 0,
              "failures": 0, "last_error": None, "last_ok_at": None, "last_failure_at": None, "since_ok": None,
              "p50_latency": None} for name in cfg.providers if name not in seen]
    # The keyed provider is always disclosed (ADR-034 amendment 1): its key is "configured" or not (never shown),
    # and a rate limit, today's cap or an unusable key file decide its health before the breaker does.
    from ..webcortex.providers import brave, daily_cap
    keyed = brave.status(daily_cap=daily_cap(cfg, "brave"),
                         stored=next((r for r in rows if r["name"] == "brave"), None), clock=lambda: now)
    row = next((r for r in rows if r["name"] == "brave"), None)
    if row is None:
        row = {"name": "brave", "provider": "brave", "engine": None, "state": "unused", "health": "unused",
               "successes": 0, "failures": 0, "last_error": None, "last_ok_at": None, "last_failure_at": None,
               "since_ok": None, "p50_latency": None}
        rows.append(row)
    if keyed["state"] in ("unconfigured", "off", "rate_limited") or keyed["key"] == "insecure":
        row["health"] = keyed["state"]
    row.update({"key": keyed["key"], "daily_cap": keyed["daily_cap"], "used_today": keyed["used_today"],
                "rate_limited_until": keyed["rate_limited_until"], "rate_limit_reason": keyed.get("rate_limit_reason"),
                "last_error": row["last_error"] or keyed["last_error"],
                "last_ok_at": row["last_ok_at"] or keyed["last_ok_at"]})
    return sorted(rows, key=lambda r: r["name"])


def _web_status(c, p, q, b):
    from ..webcortex import curiosity, services
    cfg = wstate.get_config(c.db)
    counts = dict(c.db.execute("SELECT status, count(*) FROM web_tasks GROUP BY status").fetchall())
    cache = None
    path = services.cache_path(c.path)
    if path.exists():
        from ..webcortex.cache import PublicCache
        public = PublicCache(path)
        try:
            cache = {k: v for k, v in public.stats().items() if k != "path"}
        finally:
            public.close()
    worker = c.server.worker if c.server is not None else None
    return {"config": _web_config(cfg), "providers": _provider_health(c.db, cfg, c.now), "cache": cache,
            "tasks": counts, "active": sum(n for s, n in counts.items() if s not in TERMINAL_STATES),
            "curiosity": [x for x in curiosity.rank(c.db, c.now, limit=5) if not x["excluded"]][:5]
            if cfg.enabled else None,
            "worker": _worker_view(c, worker),
            "modes": list(MODES), "memory": wstate.counts(c.db)}


def _worker_view(c, worker) -> dict:
    """The research worker: the console's own (--web-worker) or any other on this database (its heartbeat,
    scheduler.worker_status); `alive` false says plainly that no worker runs (R6 setup h)."""
    from ..webcortex import scheduler
    beat = scheduler.worker_status(c.path)
    embedded = bool(worker and worker.is_alive())
    alive = embedded or beat["running"]
    return {"embedded": worker is not None, "running": embedded, "alive": alive, "beat_at": beat["beat_at"],
            "note": None if alive else beat["note"]}


def _web_config_set(c, p, q, b):
    _fields(b, (), WEB_SWITCHES)
    before = wstate.get_config(c.db).autonomous
    cfg = wstate.set_config(c.db, b, c.now)
    if "autonomous" in b and (b["autonomous"] or before):   # the owner's switch is recorded (D4)
        from ..webcortex import autonomy
        autonomy.record_switch(c.db, bool(b["autonomous"]), c.now, by="owner:console")
    return {"config": _web_config(cfg)}


def _web_autonomy(c, p, q, b):
    """Controlled autonomy (D4): switch and enable record, both daily budgets, policy, recent decisions and the
    current ranking (chosen candidates with their signals, and refused ones with the reason)."""
    from ..webcortex import autonomy
    out = autonomy.status(c.db, c.now)
    out["candidates"] = autonomy.candidates(c.db, c.now, limit=5) if wstate.get_config(c.db).enabled else None
    return out


def _web_autonomy_set(c, p, q, b):
    """{"enabled": bool} and/or policy changes (autonomous_daily, user_daily, prices_usd, min_gain, idle_interval,
    cooldown)."""
    from ..webcortex import autonomy
    _fields(b, (), ("enabled",) + tuple(autonomy.DEFAULTS))
    changes = {k: v for k, v in b.items() if k != "enabled"}
    if changes:
        autonomy.set_policy(c.db, changes, c.now)
    if "enabled" in b:
        if not isinstance(b["enabled"], bool):
            raise ValidationError("enabled must be true or false")
        (autonomy.enable if b["enabled"] else autonomy.disable)(c.db, c.now, by="owner:console")
    return autonomy.status(c.db, c.now)


def _web_nodes(c, p, q, b):
    """Research nodes (ADR-042 draft): enrolled, needing re-enrolment or revoked, each with its key algorithm and
    fingerprint; enrolments attested by a device and waiting for the owner; and this Spark's own key fingerprint
    (the one devices pin at pairing), when the Pocket bridge has created it next to this instance."""
    from ..webcortex import nodes, signing
    key = signing.key_path(c.path)
    try:
        spark = signing.SparkKey.load_or_create(key).pin() if key.is_file() else None
    except (OSError, ValueError):
        spark = None
    return {"nodes": nodes.listing(c.db), "pending": nodes.pending(c.db, c.now), "spark": spark}


def _web_nodes_set(c, p, q, b):
    """{"action": "enrol", "fingerprint", "label"}: the owner compared the fingerprint with the device and enrols
    it under that name; {"action": "reject", "fingerprint"}; {"action": "revoke", "label"} deletes its key."""
    from ..instance.errors import NotFound
    from ..webcortex import nodes
    action = b.get("action")
    _fields(b, ("action",) + {"enrol": ("fingerprint", "label"), "reject": ("fingerprint",),
                              "revoke": ("label",)}.get(action, ()))
    if action == "enrol":
        if not isinstance(b["fingerprint"], str):
            raise ValidationError("fingerprint must be a string")
        nodes.approve(c.db, b["fingerprint"], b["label"], c.now, by="owner:console")
    elif action == "reject":
        if not isinstance(b["fingerprint"], str) or not nodes.reject(c.db, b["fingerprint"], c.now):
            raise NotFound("no pending enrolment has that fingerprint")
    elif action == "revoke":
        if not isinstance(b["label"], str) or not nodes.revoke(c.db, b["label"], c.now):
            raise NotFound("no enrolled node has that name")
    else:
        raise ValidationError("action must be enrol, reject or revoke")
    return _web_nodes(c, p, q, b)


def _web_tasks(c, p, q, b):
    from ..webcortex import orchestrator
    q = _query(q, ("status", "limit"))
    status = q.get("status") or None
    if status is not None and status not in TASK_STATES:
        raise ValidationError("status must be one of " + ", ".join(TASK_STATES))
    limit = _number(q.get("limit", "50"), "limit", int)
    if not 1 <= limit <= 500:
        raise ValidationError("limit must be between 1 and 500")
    return orchestrator.listing(c.db, status=status, limit=limit)


def _web_submit(c, p, q, b):
    """A question from the Research view. Besides queueing a research task it may answer, with `kind`:
    - "history": a question about how the individual's own understanding changed (auto mode) is answered from the
      stored record (evolution.changes); `research` is the body that queues a fresh web run instead (T1-04);
    - "clarify": a follow-up ("Is that version secure?") with no earlier question in this console session to take
      its subject from (`previous`, the session's last question, kept by the page only) (T1-10);
    - "refused": the privacy screen found private data; nothing was sent anywhere (a normal outcome, not an HTTP
      error: T1-20).
    A follow-up resolved from `previous` queues the rewritten question and says so in `resolved_followup`."""
    from ..webcortex import evolution, intent, orchestrator
    _fields(b, ("objective",), ("mode", "authorization", "previous", "route"))
    mode, objective, previous = b.get("mode"), b["objective"], b.get("previous")
    if previous is not None and (not isinstance(previous, str) or len(previous) > 2000):
        raise ValidationError("previous must be a string of at most 2000 characters")
    if b.get("route") not in (None, "research"):
        raise ValidationError("route must be omitted or 'research'")
    resolved = None
    if isinstance(objective, str) and objective.strip():
        topic = intent.history_topic(objective)
        if topic and mode in (None, "auto") and b.get("route") is None:
            return {"kind": "history", "objective": " ".join(objective.split()), "topic": topic,
                    "history": evolution.changes(c.db, topic, c.now),
                    "research": {"objective": " ".join(objective.split()), "route": "research"}}
        follow = intent.followup(objective, previous)
        if follow and "clarify" in follow:
            return {"kind": "clarify", "objective": " ".join(objective.split()), "clarify": follow["clarify"],
                    "anaphor": follow["anaphor"]}
        if follow:
            resolved, objective = follow, follow["question"]
    try:
        task = orchestrator.submit(c.db, objective, c.now, mode=None if mode in (None, "auto") else mode,
                                   origin="console", authorization=b.get("authorization"))
    except ValidationError as error:
        findings = orchestrator.screen(c.db, objective, wstate.get_config(c.db)) \
            if isinstance(objective, str) and b.get("authorization") is None else []
        if not findings:
            raise
        return {"kind": "refused", "refused": "private_data", "kinds": sorted({f["kind"] for f in findings}),
                "error": str(error)}
    if resolved:
        task = task | {"resolved_followup": {k: resolved[k] for k in ("question", "subject", "anaphor",
                                                                      "previous")}}
    return task


def _web_task(c, p, q, b):
    from ..webcortex import orchestrator
    return orchestrator.get(c.db, _task_id(p[0]))


def _web_evidence(c, p, q, b):
    from ..webcortex import orchestrator
    return orchestrator.evidence(c.db, _task_id(p[0]))


def _web_cancel(c, p, q, b):
    from ..webcortex import orchestrator
    _fields(b)
    return orchestrator.cancel(c.db, _task_id(p[0]), c.now)


def _web_resume(c, p, q, b):
    """Queue the task again for the worker (orchestrator.requeue): a failed task keeps its id and resumes from its
    checkpoint; a partial or cancelled task continues as a new task linked by parent_id."""
    from ..webcortex import orchestrator
    _fields(b)
    return orchestrator.requeue(c.db, _task_id(p[0]), c.now, origin="console")


def _web_knowledge(c, p, q, b):
    from ..webcortex import knowledge
    q = _query(q, ("topic", "limit"))
    limit = _number(q.get("limit", "30"), "limit", int)
    if not 1 <= limit <= 100:
        raise ValidationError("limit must be between 1 and 100")
    topic = (q.get("topic") or "").strip()
    if topic:
        out = knowledge.query(c.db, topic, c.now, limit=limit)
    else:
        cfg = wstate.get_config(c.db)
        recent = knowledge.rows(c.db, "id IN (SELECT id FROM web_knowledge ORDER BY updated_at DESC, id DESC "
                                "LIMIT ?)", (limit,))
        items = [knowledge.describe(c.db, item, c.now, cfg) for item in reversed(recent)]
        out = {"topic": None, "as_of": c.now, "items": items, "matched": len(items)}
    for item in out["items"]:
        item["feedback"] = knowledge.feedback(c.db, item["key"], limit=5)
    return out


def _web_review(c, p, q, b):
    from ..webcortex import knowledge
    _fields(b, ("accept",), ("note",))
    return knowledge.review(c.db, int(p[0]), b["accept"], c.now, note=_note(b))


def _web_knowledge_action(action):
    def handler(c, p, q, b):
        from ..webcortex import knowledge
        if action in ("archive", "reject"):
            _fields(b, (), ("note",))
            return getattr(knowledge, action)(c.db, int(p[0]), c.now, note=_note(b))
        _fields(b)
        if action == "refresh":   # the owner's refresh, not autonomous research
            return knowledge.refresh(c.db, int(p[0]), c.now, origin="console")
        return getattr(knowledge, action)(c.db, int(p[0]), c.now)
    return handler


def _web_monitors(c, p, q, b):
    """Monitors, each with its most recent findings (meaningful changes, newest first)."""
    from ..webcortex import scheduler
    return [m | {"findings": scheduler.findings(c.db, m["id"], limit=5)} for m in scheduler.monitor_list(c.db)]


def _web_monitor_add(c, p, q, b):
    from ..webcortex import scheduler
    wstate.require_enabled(c.db)   # API_CONTRACTS §8: 409 while web access is disabled
    _fields(b, ("topic", "interval_hours"), ("mode",))
    hours = b["interval_hours"]
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 1 <= hours <= 24 * 366:
        raise ValidationError("interval_hours must be a number between 1 and 8784")
    return scheduler.monitor_add(c.db, b["topic"], c.now, interval=float(hours) * 3600,
                                 mode=b.get("mode") or "continuous", authorized_by="owner")


def _web_monitor_action(action):
    def handler(c, p, q, b):
        from ..webcortex import scheduler
        wstate.require_enabled(c.db)
        _fields(b)
        return getattr(scheduler, f"monitor_{action}")(c.db, int(p[0]), c.now)
    return handler


def _llm_status(c, p, q, b):
    """Inference gateway health (an explicit state, never a guess), reasoning settings and the last 24 h of token
    accounting. Contacts only the owner-configured endpoint (loopback, or off-host with remote_allowed);
    ?probe=1 also generates a one-word reply."""
    from ..inference import config as iconfig
    from ..inference.gateway import Gateway
    from ..inference.ledger import Ledger, ledger_path
    q = _query(q, ("probe",))
    gw = Gateway.from_db(c.db, c.path)
    path = ledger_path(c.path)
    return {"health": gw.health(probe=q.get("probe") in ("1", "true")), "config": iconfig.public(gw.cfg),
            "usage_24h": Ledger(path).summary(since=c.now - 86400) if path.exists() else None}


def _resources(c, p, q, b):
    """Memory/GPU/container use, the pressure level and what the scheduling policies decide now (RD-46;
    resources.status). ?cold_start=1 also reads each neuroscifi-* container's last start-to-ready time."""
    from .. import resources
    q = _query(q, ("cold_start",))
    return resources.status(c.path, full=True, cold_start=q.get("cold_start") in ("1", "true"))


def _presence(c, p, q, b):
    """Presence state long-poll (presence/routes.py): ?since=<version>&wait=<seconds, at most 20>."""
    from ..presence import routes
    return routes.console_state(c, q)


def _presence_scene(c, p, q, b):
    """A Tier C doodle proposal from the local model, validated; or the nearest canonical as fallback."""
    from ..presence import routes
    return routes.console_scene(c, b)


SEGMENT = r"([^/]+)"
ROUTES = [(method, re.compile(f"^/api/{pattern}$"), handler) for method, pattern, handler in (
    ("GET", "inventory", _inventory),
    ("GET", "epochs", _epochs),
    ("POST", "epochs", _start_epoch),
    ("GET", f"epochs/{SEGMENT}/beliefs", _explain),
    ("GET", f"epochs/{SEGMENT}/explain", _explain),
    ("GET", f"epochs/{SEGMENT}/match", _match),
    ("GET", f"epochs/{SEGMENT}/decision", _decision),
    ("POST", f"epochs/{SEGMENT}/decide", _decide),
    ("GET", f"epochs/{SEGMENT}/evidence", _evidence),
    ("POST", "observe", _observe),
    ("GET", "pending", _pending),
    ("POST", r"pending/(\d+)/resolve", _resolve),
    ("POST", "forget", _forget),
    ("POST", "promote", _promote_evidence),
    ("GET", "graphs", _graphs),
    ("POST", "graphs/learn", _graph_learn),
    ("GET", r"graphs/(\d+)", _graph),
    ("POST", "graphs/coactivation", _coactivation),
    ("POST", r"graphs/(\d+)/evaluate", _graph_evaluate),
    ("POST", r"graphs/(\d+)/promote", _graph_promote),
    ("POST", r"graphs/(\d+)/reject", _graph_reject),
    ("GET", "datasets", _datasets),
    ("POST", "datasets/simulate", _simulate),
    ("POST", "recall", _recall),
    ("GET", "checkpoints", _checkpoints),
    ("POST", "checkpoints", _checkpoint_create),
    ("POST", "checkpoints/prune", _checkpoint_prune),
    ("POST", "checkpoints/rebuild", _checkpoint_rebuild),
    ("POST", r"checkpoints/(\d+)/verify", _checkpoint_verify),
    ("GET", "memory", _memory),
    ("GET", "llm/status", _llm_status),
    ("GET", "resources", _resources),
    ("GET", "presence", _presence),
    ("POST", "presence/scene", _presence_scene),
    ("GET", "web/status", _web_status),
    ("POST", "web/config", _web_config_set),
    ("GET", "web/autonomy", _web_autonomy),
    ("POST", "web/autonomy", _web_autonomy_set),
    ("GET", "web/nodes", _web_nodes),
    ("POST", "web/nodes", _web_nodes_set),
    ("GET", "web/tasks", _web_tasks),
    ("POST", "web/tasks", _web_submit),
    ("GET", f"web/tasks/{SEGMENT}", _web_task),
    ("GET", f"web/tasks/{SEGMENT}/evidence", _web_evidence),
    ("POST", f"web/tasks/{SEGMENT}/cancel", _web_cancel),
    ("POST", f"web/tasks/{SEGMENT}/resume", _web_resume),
    ("GET", "web/knowledge", _web_knowledge),
    ("POST", r"web/knowledge/(\d+)/review", _web_review),
    *(("POST", rf"web/knowledge/(\d+)/{name}", _web_knowledge_action(name))
      for name in ("archive", "reject", "refresh", "forget")),
    ("GET", "web/monitors", _web_monitors),
    ("POST", "web/monitors", _web_monitor_add),
    *(("POST", rf"web/monitors/(\d+)/{name}", _web_monitor_action(name)) for name in ("pause", "resume", "stop")),
)]


class _Context:
    def __init__(self, db, path, now, server=None):
        self.db, self.path, self.now, self.server = db, path, now, server


class Handler(BaseHTTPRequestHandler):
    timeout = 30

    def version_string(self):
        return "nsf-console"

    def log_message(self, *args):
        pass  # No URL, token, body or address in logs.

    def reply(self, code, body=b"", mime="application/json", extra=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, allow_nan=False).encode()
        self.send_response(code)
        for name, value in {"Content-Type": mime, "Content-Length": str(len(body)), **HEADERS,
                            **(extra or {})}.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def fail(self, code, message):
        self.reply(code, {"error": message})

    def send_error(self, code, message=None, explain=None):
        """http.server's own errors (bad request line, 414, 431, 501, ...) as JSON with the security headers;
        the default HTML page would echo request text and miss the CSP."""
        self.close_connection = True
        short = self.responses.get(code, ("error",))[0]
        body = json.dumps({"error": short.lower()}).encode()
        self.send_response(code)
        for name, value in {"Content-Type": "application/json", "Content-Length": str(len(body)), **HEADERS,
                            "Connection": "close"}.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD" and code >= 200 and code not in (204, 304):
            self.wfile.write(body)

    def _trusted_origin(self):
        """Host must name this loopback server; a present Origin must be exactly http://<Host>."""
        port = self.server.server_port
        host = self.headers.get("Host", "")
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin == "http://" + host

    def _authorized(self):
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return False
        return hmac.compare_digest(auth[7:].encode(), self.server.token.encode())

    def handle_method(self):
        if not self._trusted_origin():
            return self.fail(403, "forbidden host or origin")
        url = urlsplit(self.path)
        if not url.path.startswith("/api/"):
            asset = ASSETS.get(url.path)
            if self.command not in ("GET", "HEAD"):
                return self.fail(405 if asset or url.path == "/icon.svg" else 404, "method not allowed")
            if url.path == "/icon.svg":
                return self.reply(200, ICON, "image/svg+xml")
            if not asset:
                return self.fail(404, "not found")
            try:
                body = (WEB / asset[0]).read_bytes()
            except OSError:   # a listed asset missing on disk (incomplete install) is a 404, not a dropped connection
                return self.fail(404, "not found")
            return self.reply(200, body, asset[1])
        if not self._authorized():
            return self.fail(403, "missing or invalid session token; open the URL printed by `nsf console`")
        if url.path.startswith("/api/voice/"):   # own body rules: JSON <= 4 KiB, or one audio chunk <= 64 KiB
            from ..voice import http as voice_http
            return voice_http.console(self, url)
        matches = [(method, m, handler) for method, pattern, handler in ROUTES if (m := pattern.match(url.path))]
        if not matches:
            return self.fail(404, "unknown route")
        chosen = next(((m, h) for method, m, h in matches if method == self.command), None)
        if chosen is None:
            allow = ", ".join(sorted({method for method, _, _ in matches}))
            return self.reply(405, {"error": "method not allowed"}, extra={"Allow": allow})
        match, handler = chosen
        if r"(\d+)" in match.re.pattern and any(int(g) > MAX_ID for g in match.groups()):
            return self.fail(404, "unknown route")
        body = self._body()
        if body is None:
            return
        params = [unquote(g) for g in match.groups()]
        self._dispatch(handler, params, parse_qs(url.query, keep_blank_values=False), body)

    def _body(self):
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.fail(400, "invalid Content-Length")
            return None
        if size < 0:
            self.fail(400, "invalid Content-Length")
            return None
        if self.command == "GET":
            if size or self.headers.get("Transfer-Encoding"):
                self.fail(400, "GET requests carry no body")
                return None
            return {}
        if self.headers.get("Transfer-Encoding"):
            self.fail(400, "chunked bodies are not accepted")
            return None
        limit = MAX_TEXT_BODY if urlsplit(self.path).path in TEXT_ROUTES else MAX_BODY
        if size > limit:
            self.close_connection = True
            self.fail(413, f"body exceeds {limit} bytes")
            return None
        if self.headers.get_content_type() != "application/json":
            self.fail(400, "Content-Type must be application/json")
            return None
        try:
            body = json.loads(self.rfile.read(size)) if size else None
        except (ValueError, UnicodeDecodeError):
            body = None
        if not isinstance(body, dict):
            self.fail(400, "body must be a JSON object")
            return None
        return body

    def _dispatch(self, handler, params, query, body):
        db = None
        try:
            db = store.open_existing(self.server.db_path)
            now = self.server.clock()
            if self.command == "GET":
                self._sweep_if_due(db, now)
            else:
                db.execute(f"PRAGMA busy_timeout={self.server.post_busy_ms}")
                governance.sweep(db, now)
            result = handler(_Context(db, self.server.db_path, now, self.server), params, query, body)
            self.reply(200, result)
        except InstanceError as error:
            self.fail(error.http_status, str(error))
        except sqlite3.OperationalError as error:
            if "locked" in str(error) or "busy" in str(error):
                self.reply(503, {"error": "instance busy; retry"}, extra={"Retry-After": "1"})
            else:
                self.fail(500, "internal error")
        except Exception:
            self.fail(500, "internal error")
        finally:
            if db is not None:
                db.close()

    def _sweep_if_due(self, db, now):
        """GETs stay readable behind a writer: sweep at most once a minute and give up quickly if the lock is busy."""
        server = self.server
        with server.sweep_lock:
            due = time.monotonic() - server.last_sweep >= SWEEP_EVERY
            if due:
                server.last_sweep = time.monotonic()
        if not due:
            return
        db.execute(f"PRAGMA busy_timeout={GET_SWEEP_BUSY_MS}")
        try:
            governance.sweep(db, now)
        except sqlite3.OperationalError as error:
            if "locked" not in str(error) and "busy" not in str(error):
                raise
        db.execute(f"PRAGMA busy_timeout={self.server.post_busy_ms}")

    def do_GET(self):
        self.handle_method()

    def do_HEAD(self):
        self.handle_method()

    def do_POST(self):
        self.handle_method()

    do_PUT = do_DELETE = do_PATCH = do_POST


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, db_path, port=DEFAULT_PORT, *, token=None, clock=time.time, web_worker=False,
                 worker_options=None):
        """web_worker (opt-in, `nsf console --web-worker`): run queued research tasks in a background thread
        (scheduler.work with its own connection; it idles while web access is disabled). Off by default: the
        console service is confined to loopback, so research runs in the neuroscifi-webcortex service (`nsf web
        work`), which has network access; turn it on only for a development console that can reach the internet.
        Task leases keep two workers from running one task. worker_options: extra scheduler.work arguments, e.g.
        {"fixture": FixtureWeb, "providers": [...]} for offline tests."""
        self.db_path = Path(db_path).resolve()
        db = store.open_existing(self.db_path)  # refuse to start without an instance
        try:
            checkpoints.sweep(db, self.db_path)   # working copies a killed checkpoint/restore left (T5-03)
            from ..voice import transcripts
            transcripts.end_orphans(db, "console:", clock())   # voice sessions of a console that died (T5-08)
        finally:
            db.close()
        self.token = token or secrets.token_urlsafe(32)
        self.clock = clock
        self.post_busy_ms = POST_BUSY_MS
        self.sweep_lock, self.last_sweep = threading.Lock(), float("-inf")
        self.worker_stop, self.worker = threading.Event(), None
        super().__init__((BIND, port), Handler)
        self.worker_options = {"clock": clock, **(worker_options or {})}
        from ..resources.policy import AudioBeacon     # the voice hub lives here: publish its audio state (RD-46)
        self.audio_beacon = AudioBeacon(self.db_path).start()
        if web_worker:
            self.worker = threading.Thread(target=self._work, name="nsf-console-web-worker", daemon=True)
            self.worker.start()

    def _work(self):
        """The embedded worker loop: an unexpected error is logged (exception type and a truncated message, never
        a traceback or task content) and the loop restarts after an exponential back-off (5 s doubling to 60 s)."""
        from ..webcortex import scheduler
        delay = WORKER_RETRY
        while not self.worker_stop.is_set():
            started = time.monotonic()
            try:
                scheduler.work(self.db_path, stop=self.worker_stop, poll=WORKER_POLL, sleep=self.worker_stop.wait,
                               **self.worker_options)
            except Exception as exc:
                if time.monotonic() - started > WORKER_RETRY_MAX:
                    delay = WORKER_RETRY   # it ran fine for a while: start the back-off over
                message = " ".join(str(exc).split())[:200]
                print(f"nsf-console: research worker error: {type(exc).__name__}: {message}; retrying in "
                      f"{delay:g} s", file=sys.stderr, flush=True)
                self.worker_stop.wait(delay)
                delay = min(WORKER_RETRY_MAX, delay * 2)

    def server_close(self):
        self.audio_beacon.close()
        self.worker_stop.set()
        if self.worker is not None and self.worker is not threading.current_thread():
            self.worker.join(timeout=30)
        super().server_close()

    def handle_error(self, request, client_address):
        # R6: a tab closed or reloaded while its presence long-poll waited raised BrokenPipeError at the reply,
        # five times in normal navigation; a client that went away is not an error worth reporting
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        print("nsf-console: connection error", file=sys.stderr, flush=True)  # no address, no traceback

    @property
    def url(self):
        return f"http://{BIND}:{self.server_port}/#token={self.token}"

