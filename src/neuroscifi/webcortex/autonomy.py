"""Controlled autonomy (owner decision D4; ADR-039 draft; docs/autonomy/RESEARCH_POLICY.md).

Opt-in. Autonomous research (origins 'curiosity' and 'refresh:<id>') runs only while the owner's `autonomous` switch is
on; the idle investigator below additionally needs an owner enable that was recorded (enable(), the console switch,
`nsf web config --autonomous` or `nsf web autonomy enable`), with who and when.

Two daily budgets, charged to the UTC day of `now` when a task starts (never the wall clock):
  autonomous   searches 20, pages 60, active seconds 1800, external spend $1.00, 2 concurrent investigations
  user         searches 400, pages 1200, active seconds 14400, external spend $5.00 (everything the owner, a
               Pocket device, a node, a monitor or an MCP client asked for)
A *search* is one query dispatched to the provider registry (usage.queries); a *page* one document acquired
(usage.pages); *seconds* the task's active execution time (usage.seconds); *spend* queries x prices_usd.search +
pages x prices_usd.page (both 0.0 by default: SearXNG, the structured APIs and the local model cost nothing; a paid
provider sets its price). SearXNG-facing HTTP requests and the upstream engine requests SearXNG reports are counted
separately (search_counts) and shown in status; they are observations, not limits.

Enforcement (admit, called by orchestrator._claim inside its BEGIN IMMEDIATE transaction, so every worker process
sees the same numbers): used = what tasks of the day actually used (settled ones, and running ones so far);
committed = used + the whole allowance of tasks still running. A task is refused (cancelled with the reason) only when
`used` leaves nothing of searches, pages, seconds or spend (UAT T5-02: allowances held by running tasks are not
spent); when only the allowances of running tasks fill the budget it waits (stays queued; claim_next skips its
scope) until they finish and settle; else its max_queries / max_pages / max_seconds are clamped to what is left and
that allowance is recorded as an open autonomy_ledger row. A queued task holds nothing. Searches and pages are charged before each request (Usage.charge), so they cannot pass the cap;
seconds can pass it by at most the network step in flight. An autonomous task also waits (stays queued) while
`concurrent` autonomous investigations are open; claim_next skips autonomous tasks then. settle() replaces the
allowance with what the task used when it ends. Forgetting a task keeps its ledger row, so it never refunds budget.

Prioritisation (candidates): every candidate comes from stored records - curiosity.rank (contested and stale
knowledge, knowledge predictions due, contested subjects, single-source gaps) and open hypotheses with an open
prediction (evolution, Lane D). priority = info_gain x (1 + sum(WEIGHTS[s] x signal[s])) / cost, signals in [0, 1]:
goal (word overlap with active owner monitors and the owner's research of the last 7 days), gap, hypothesis,
prediction_uncertainty (binary entropy of the forecast, read through what the settled forecasts of its kind show
once 20 of them have settled: evolution.recalibrations, ADR-046), stale, contradiction, source_change (a monitor
reported a meaningful change of that subject in the last 7 days). cost = curiosity.cost(mode) + estimated spend /
spend cap. For an open hypothesis, priority uses the information gain of its forecast as read (info_gain_as_read,
shown when it differs). A candidate whose expected information gain from its stated forecast (info_gain) is below
min_gain bits is refused: no browsing to generate activity. The reading only orders the candidates that are
admitted; it never admits or refuses one (ADR-046, review F3).
A subject investigated autonomously within `cooldown` seconds is skipped. The owner's own records always rank first;
a subject only autonomous research produced is pursued at most one step from the owner's records and only when it
stays on the owner subject it came from (UAT T1-02, T1-03).
Both launch paths, this idle investigator and the timer's curiosity (scheduler.tick), admit through admission(),
the one rule above (ADR-050); and the owner's quiet (background.py) holds both.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import math
import re

from ..instance import store
from ..instance.errors import ValidationError
from . import state
from .types import TERMINAL_STATES

log = logging.getLogger(__name__)

META_KEY = "webcortex.autonomy"
LIMIT_KEYS = ("searches", "pages", "seconds", "spend_usd")
DEFAULTS = {
    "autonomous_daily": {"searches": 20, "pages": 60, "seconds": 1800.0, "spend_usd": 1.0, "concurrent": 2},
    "user_daily": {"searches": 400, "pages": 1200, "seconds": 14400.0, "spend_usd": 5.0},
    "prices_usd": {"search": 0.0, "page": 0.0},
    "min_gain": 0.05,          # bits of expected information gain below which nothing is researched
    "idle_interval": 300.0,    # seconds between two idle-investigation decisions of a worker
    "cooldown": 86400.0,       # seconds before the same subject is investigated autonomously again
}
WEIGHTS = {"goal": 1.0, "contradiction": 1.0, "hypothesis": 0.8, "prediction_uncertainty": 0.8,
           "source_change": 0.7, "stale": 0.6, "gap": 0.5}
GOAL_WINDOW = 7 * 86400.0
CHANGE_WINDOW = 7 * 86400.0
MAX_CANDIDATES = 50
MAX_DERIVATION_DEPTH = 1       # autonomous steps a subject may be away from the owner's records and still be pursued
MIN_RELEVANCE = 1 / 3          # word overlap (share of the smaller set) with the owner subject it was derived from
AUTONOMOUS_SQL = "(origin='curiosity' OR origin LIKE 'refresh:%')"
_WORD = re.compile(r"[^\W_]{3,}")
_STOP = frozenset("the and for with that this from what which when where will would about into does have has was "
                  "are were been than then there their latest current version still".split())


class BudgetRefused(ValidationError):
    """A daily research budget has nothing left (HTTP routes answer 429)."""


# ---- policy and the owner's switch -------------------------------------------------------------------------------

def _meta(db) -> dict:
    row = db.execute("SELECT value FROM meta WHERE key=?", (META_KEY,)).fetchone()
    return state.loads(row[0], {}) if row else {}


def _write_meta(db, value):
    db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (META_KEY, state.dumps(value)))


def policy(db) -> dict:
    """Limits, prices, thresholds (defaults overlaid with the owner's changes) and the enable record."""
    raw = _meta(db)
    out = {k: (dict(v) | dict(raw.get(k) or {})) if isinstance(v, dict) else raw.get(k, v)
           for k, v in DEFAULTS.items()}
    out["record"] = dict(raw.get("record") or {})
    return out


def _number(name, value, *, integer=False, upper=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or \
            (integer and int(value) != value) or (upper is not None and value > upper):
        raise ValidationError(f"{name} must be a non-negative {'integer' if integer else 'number'}"
                              + (f" of at most {upper}" if upper is not None else ""))
    return int(value) if integer else float(value)


def set_policy(db, changes: dict, now) -> dict:
    """Validate and apply owner changes: autonomous_daily / user_daily {searches, pages, seconds, spend_usd
    [, concurrent]}, prices_usd {search, page}, min_gain, idle_interval, cooldown. One decision event."""
    if not isinstance(changes, dict) or not changes or set(changes) - set(DEFAULTS):
        raise ValidationError(f"autonomy policy accepts only {', '.join(DEFAULTS)}")
    clean = {}
    for key, value in changes.items():
        if isinstance(DEFAULTS[key], dict):
            if not isinstance(value, dict) or set(value) - set(DEFAULTS[key]):
                raise ValidationError(f"{key} accepts only {', '.join(DEFAULTS[key])}")
            clean[key] = {k: _number(f"{key}.{k}", v, integer=k in ("searches", "pages", "concurrent"),
                                     upper=16 if k == "concurrent" else None) for k, v in value.items()}
        else:
            clean[key] = _number(key, value, upper=1.0 if key == "min_gain" else None)
    with store.write(db):
        raw = _meta(db)
        for key, value in clean.items():
            raw[key] = (dict(raw.get(key) or {}) | value) if isinstance(value, dict) else value
        _write_meta(db, raw)
        state.log_event(db, None, "decision", {"autonomy": "policy changed", "changes": clean}, now)
        store.bump_state_version(db)
    return policy(db)


def record_switch(db, on: bool, now, *, by="owner") -> dict:
    """Record an owner's change of the `autonomous` switch (who, when) and log it. Caller does not hold a
    transaction. The switch itself is state.set_config's."""
    with store.write(db):
        raw = _meta(db)
        record = dict(raw.get("record") or {})
        if on:
            record.update(enabled_by=str(by)[:64], enabled_at=now, disabled_at=None)
        else:
            record.update(disabled_at=now, disabled_by=str(by)[:64])
        raw["record"] = record
        _write_meta(db, raw)
        state.log_event(db, None, "decision", {"autonomy": "enabled" if on else "disabled", "by": str(by)[:64]}, now)
        store.bump_state_version(db)
    return record


def enable(db, now, *, by="owner") -> dict:
    state.set_config(db, {"autonomous": True}, now)
    return record_switch(db, True, now, by=by)


def disable(db, now, *, by="owner") -> dict:
    state.set_config(db, {"autonomous": False}, now)
    return record_switch(db, False, now, by=by)


def enabled(db) -> bool:
    """The switch is on AND the owner's enable was recorded (and not followed by a disable)."""
    record = policy(db)["record"]
    return bool(state.get_config(db).autonomous and record.get("enabled_at") and not record.get("disabled_at"))


# ---- ledger -------------------------------------------------------------------------------------------------------

def day_of(now) -> str:
    return datetime.fromtimestamp(float(now), timezone.utc).strftime("%Y-%m-%d")


def scope_of(origin) -> str:
    from .orchestrator import is_autonomous
    return "autonomous" if is_autonomous(origin) else "user"


def _limits(pol, scope) -> dict:
    return pol["autonomous_daily" if scope == "autonomous" else "user_daily"]


def spend(usage: dict, prices: dict) -> float:
    return round(int(usage.get("queries", 0) or 0) * float(prices.get("search", 0.0)) +
                 int(usage.get("pages", 0) or 0) * float(prices.get("page", 0.0)), 6)


def _used_of(usage: dict, checkpoint: dict, prices: dict) -> dict:
    counts = (checkpoint or {}).get("search_counts") or {}
    return {"searches": int(usage.get("queries", 0) or 0), "pages": int(usage.get("pages", 0) or 0),
            "seconds": round(float(usage.get("seconds", 0.0) or 0.0), 3), "spend_usd": spend(usage, prices),
            "requests": int(usage.get("requests", 0) or 0), "bytes": int(usage.get("bytes", 0) or 0),
            "searxng_requests": int(counts.get("searxng_requests", 0) or 0),
            "engine_requests": int(counts.get("engine_requests", 0) or 0)}


def _rows(db, scope, day, *, exclude=None):
    """(state, allowed, used now, task status) of the day's ledger rows of a scope (open rows: live usage)."""
    pol = policy(db)
    out = []
    for task_id, row_state, allowed, used, status, usage, checkpoint in db.execute(
            "SELECT l.task_id, l.state, l.allowed, l.used, t.status, t.usage, t.checkpoint FROM autonomy_ledger l "
            "LEFT JOIN web_tasks t ON t.id=l.task_id WHERE l.scope=? AND l.day=?", (scope, day)):
        if task_id == exclude:
            continue
        allowed, used = state.loads(allowed, {}), state.loads(used, {})
        if row_state == "open" and usage is not None:
            used = _used_of(state.loads(usage, {}), state.loads(checkpoint, {}), pol["prices_usd"])
        out.append({"task_id": task_id, "state": row_state, "allowed": allowed, "used": used, "status": status})
    return out


def committed(db, scope, now, *, exclude=None, reserved=True) -> dict:
    """Per limit: used by settled tasks and finished-but-unsettled ones, plus (reserved=True) the allowance of tasks
    still running. reserved=False counts only what running tasks used so far. A task forgotten while open keeps its
    whole allowance either way (its usage is gone, so it never refunds)."""
    out = {k: 0.0 for k in LIMIT_KEYS}
    for row in _rows(db, scope, day_of(now), exclude=exclude):
        is_open = row["state"] == "open" and row["status"] not in TERMINAL_STATES
        gone = is_open and row["status"] is None
        hold = gone or (is_open and reserved)
        source = row["allowed"] if hold else row["used"]
        for key in LIMIT_KEYS:
            value = float(source.get(key, 0) or 0)
            if hold:   # an open task never counts less than it already used
                value = max(value, float(row["used"].get(key, 0) or 0))
            out[key] += value
    return out


def remaining(db, scope, now, *, exclude=None, reserved=True) -> dict:
    limits = _limits(policy(db), scope)
    spent = committed(db, scope, now, exclude=exclude, reserved=reserved)
    return {k: max(0.0, float(limits.get(k, 0)) - spent[k]) for k in LIMIT_KEYS}


def _exhausted(left: dict, prices: dict, limits: dict) -> str | None:
    """Which limit has nothing left, worded as what was used of it ('searches: 5 used of 5')."""
    for key in ("searches", "pages"):
        if left[key] < 1:
            return f"{key}: {int(float(limits[key]) - left[key])} used of {int(limits[key])}"
    if left["seconds"] < 1:
        return f"active time: {float(limits['seconds']) - left['seconds']:g} s used of {float(limits['seconds']):g} s"
    cheapest = min([p for p in (float(prices.get("search", 0)), float(prices.get("page", 0))) if p > 0],
                   default=None)
    if cheapest is not None and left["spend_usd"] < cheapest:
        return (f"external spend: ${float(limits['spend_usd']) - left['spend_usd']:.2f} used of "
                f"${float(limits['spend_usd']):.2f}")
    return None


def refusal(db, scope, now) -> str | None:
    """Why a new task of `scope` cannot start today (what finished and running tasks actually used spends the
    budget), else None. Allowances held by running tasks do not refuse: a task waits for them (waiting())."""
    pol = policy(db)
    reason = _exhausted(remaining(db, scope, now, reserved=False), pol["prices_usd"], _limits(pol, scope))
    return None if reason is None else f"daily {scope} research budget exhausted on {day_of(now)}: {reason}"


def waiting(db, scope, now, *, exclude=None) -> str | None:
    """Why a task of `scope` must wait (stay queued) now: the budget is not spent, but what is left of it is held
    by tasks still running; their allowances return when they finish or are cancelled. Else None."""
    pol = policy(db)
    limits, prices = _limits(pol, scope), pol["prices_usd"]
    if _exhausted(remaining(db, scope, now, exclude=exclude, reserved=False), prices, limits):
        return None
    if _exhausted(remaining(db, scope, now, exclude=exclude), prices, limits) is None:
        return None
    return (f"waiting: the rest of today's {scope} research budget is held by running tasks until they finish")


def require_room(db, origin, now):
    """BudgetRefused when the daily budget of the origin's scope has nothing left (submit-time check)."""
    reason = refusal(db, scope_of(origin), now)
    if reason:
        raise BudgetRefused(reason)


def running(db, now, *, exclude=None) -> int:
    """Autonomous investigations in flight: admitted and not finished, plus queued ones a worker has claimed."""
    terminal = ",".join("?" * len(TERMINAL_STATES))
    admitted = db.execute(f"SELECT count(*) FROM autonomy_ledger l JOIN web_tasks t ON t.id=l.task_id WHERE "
                          f"l.scope='autonomous' AND l.state='open' AND t.status NOT IN ({terminal}) AND l.task_id IS NOT ?",
                          (*TERMINAL_STATES, exclude)).fetchone()[0]
    claimed = db.execute(f"SELECT count(*) FROM web_tasks WHERE {AUTONOMOUS_SQL} AND status='queued' AND "
                         "lease_holder IS NOT NULL AND lease_expires_at>=? AND id IS NOT ? AND id NOT IN "
                         "(SELECT task_id FROM autonomy_ledger WHERE state='open')", (now, exclude)).fetchone()[0]
    return admitted + claimed


def full(db, now, *, exclude=None) -> bool:
    return running(db, now, exclude=exclude) >= int(policy(db)["autonomous_daily"].get("concurrent", 0))


def admit(db, task: dict, now) -> dict:
    """Charge a task that is about to start to its scope's daily budget (orchestrator._claim; caller owns the
    BEGIN IMMEDIATE transaction). Returns {} when the task already holds an open allowance (crash recovery),
    {"wait": reason} for an autonomous task while `concurrent` are open or while resources.policy.background_gate
    refuses (a voice session capturing or speaking, critical memory pressure), {"refused": reason} when the budget is
    spent, else {"budget": clamped budget JSON or None, "allowed": {...}}."""
    from .types import Budget, Usage
    row = db.execute("SELECT state FROM autonomy_ledger WHERE task_id=?", (task["id"],)).fetchone()
    if row is not None and row[0] == "open":
        return {}
    scope = scope_of(task.get("origin"))
    pol = policy(db)
    limits, prices = _limits(pol, scope), pol["prices_usd"]
    if scope == "autonomous" and full(db, now, exclude=task["id"]):
        return {"wait": f"{int(limits.get('concurrent', 0))} autonomous investigations are already running"}
    if scope == "autonomous":         # RD-46: audio priority and critical memory pressure defer a new start
        from ..resources import policy as rpolicy
        gate = rpolicy.background_gate(rpolicy.db_file(db))
        if not gate["allowed"]:
            return {"wait": gate["reason"]}
    reason = _exhausted(remaining(db, scope, now, exclude=task["id"], reserved=False), prices, limits)
    if reason:      # what finished and running tasks actually used spends the budget
        return {"refused": f"daily {scope} research budget exhausted on {day_of(now)}: {reason}"}
    left = remaining(db, scope, now, exclude=task["id"])
    if _exhausted(left, prices, limits):   # the rest is held by running tasks: wait for them, never cancel
        return {"wait": waiting(db, scope, now, exclude=task["id"]) or "waiting for running tasks"}
    budget = Budget.from_json(task["budget"])
    used = Usage.from_json((task.get("checkpoint") or {}).get("usage") or task.get("usage"))
    ps, pp = float(prices.get("search", 0.0)), float(prices.get("page", 0.0))
    money = left["spend_usd"]
    queries = min(budget.max_queries, used.queries + int(left["searches"]))
    if ps > 0:
        queries = min(queries, used.queries + int(math.floor(money / ps + 1e-9)))
    money -= max(0, queries - used.queries) * ps
    pages = min(budget.max_pages, used.pages + int(left["pages"]))
    if pp > 0:
        pages = min(pages, used.pages + int(math.floor(max(0.0, money) / pp + 1e-9)))
    seconds = min(float(budget.max_seconds), float(used.seconds or 0.0) + left["seconds"])
    clamped = {k: v for k, v in (("max_queries", queries), ("max_pages", pages), ("max_seconds", seconds))
               if v < getattr(budget, k)}
    allowed = {"searches": queries, "pages": pages, "seconds": round(seconds, 3),
               "spend_usd": round(queries * ps + pages * pp, 6)}
    db.execute("INSERT INTO autonomy_ledger(task_id,scope,day,state,allowed,used,opened_at) VALUES(?,?,?,'open',?,"
               "'{}',?) ON CONFLICT(task_id) DO UPDATE SET scope=excluded.scope, day=excluded.day, state='open', "
               "allowed=excluded.allowed, opened_at=excluded.opened_at, settled_at=NULL",
               (task["id"], scope, day_of(now), state.dumps(allowed), now))
    out = {"budget": None, "allowed": allowed}
    if clamped:     # the allowance itself is in the ledger; an event only when the budget changed
        state.log_event(db, task["id"], "decision", {"budget_scope": scope, "day": day_of(now), "allowed": allowed,
                                                     "clamped_to_daily_budget": clamped}, now)
        from dataclasses import replace
        out["budget"] = replace(budget, **clamped).to_json()
    return out


def settle(db, task_id, usage: dict, checkpoint: dict, now):
    """Replace a task's open allowance with what it used (it ended). Caller owns the transaction."""
    used = _used_of(usage or {}, checkpoint or {}, policy(db)["prices_usd"])
    db.execute("UPDATE autonomy_ledger SET state='settled', used=?, settled_at=? WHERE task_id=? AND state='open'",
               (state.dumps(used), now, task_id))


def search_counts(attempts) -> dict:
    """SearXNG-facing HTTP requests and upstream engine requests visible in one registry.search's attempts.

    A SearXNG query that reached SearXNG lists each engine it asked (engine set, error not 'circuit_open'); a cache
    hit lists none and is not a request; a transport failure (an error that is not circuit_open/budget/empty, with
    no engine entries) is one request and no engine request."""
    engines, queried, failed = 0, set(), set()
    for a in attempts or []:
        if a.get("provider") != "searxng":
            continue
        if a.get("engine"):
            if a.get("error") != "circuit_open":
                engines += 1
                queried.add(a.get("query"))
        elif a.get("error") and not str(a["error"]).startswith(("circuit_open", "budget_exhausted", "empty")):
            failed.add(a.get("query"))
    return {"searxng_requests": len(queried) + len(failed - queried), "engine_requests": engines}


# ---- status --------------------------------------------------------------------------------------------------------

def _scope_status(db, scope, now, pol) -> dict:
    rows = _rows(db, scope, day_of(now))
    used = {k: 0.0 for k in ("searches", "pages", "seconds", "spend_usd", "searxng_requests", "engine_requests")}
    for row in rows:
        for key in used:
            used[key] += float(row["used"].get(key, 0) or 0)
    limits = _limits(pol, scope)
    spent = committed(db, scope, now)
    out = {"limits": dict(limits), "used": {k: round(v, 6) for k, v in used.items()},
           "committed": {k: round(v, 6) for k, v in spent.items()},
           "remaining": {k: round(max(0.0, float(limits.get(k, 0)) - spent[k]), 6) for k in LIMIT_KEYS},
           "tasks": len(rows), "open": sum(1 for r in rows if r["state"] == "open"),
           "refusal": refusal(db, scope, now)}
    if scope == "autonomous":
        out["running"] = running(db, now)
    return out


def status(db, now) -> dict:
    """What the owner sees: switch and enable record, both budgets for the day of `now`, policy, recent decisions."""
    pol = policy(db)
    cfg = state.get_config(db)
    decisions = [{"at": at, "task_id": tid, **state.loads(detail, {})} for tid, detail, at in db.execute(
        "SELECT task_id, detail, created_at FROM web_events WHERE kind='decision' AND "
        "json_extract(detail,'$.autonomy') IS NOT NULL ORDER BY id DESC LIMIT 10")]
    from .. import background
    return {"day": day_of(now), "switch": cfg.autonomous, "enabled": enabled(db), "record": pol["record"],
            "quiet": background.quiet(db),          # the owner's switch above this one (ADR-050)
            "autonomous": _scope_status(db, "autonomous", now, pol), "user": _scope_status(db, "user", now, pol),
            "prices_usd": pol["prices_usd"], "min_gain": pol["min_gain"], "weights": dict(WEIGHTS),
            "idle_interval": pol["idle_interval"], "cooldown": pol["cooldown"], "decisions": decisions}


# ---- prioritisation ------------------------------------------------------------------------------------------------

def _words(text) -> set:
    return {w for w in _WORD.findall(str(text or "").lower()) if w not in _STOP}


def _overlap(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def goals(db, now) -> list[str]:
    """Active user goals from the records: owner monitors that are active, and the objectives of research the
    owner (console, CLI, Pocket, node, MCP) asked for in the last GOAL_WINDOW seconds."""
    out = [t for (t,) in db.execute("SELECT topic FROM web_monitors WHERE status='active' AND authorized_by='owner'")]
    out += [o for o, origin in db.execute("SELECT objective, origin FROM web_tasks WHERE created_at>=? AND "
                                          f"NOT {AUTONOMOUS_SQL} ORDER BY created_at DESC LIMIT 100",
                                          (now - GOAL_WINDOW,)) if not str(origin).startswith("monitor:")]
    return out


def _changed_keys(db, now) -> set:
    keys = set()
    for (detail,) in db.execute("SELECT detail FROM web_events WHERE kind='finding' AND created_at>=? AND "
                                "json_extract(detail,'$.monitor_id') IS NOT NULL", (now - CHANGE_WINDOW,)):
        changes = (state.loads(detail, {}) or {}).get("changes") or {}
        keys |= {c.get("key") for c in changes.get("changed_values") or [] if isinstance(c, dict)}
        keys |= set(changes.get("new_claims") or [])
    return {k for k in keys if k}


def _recent(db, now, cooldown) -> set:
    return {k for (k,) in db.execute("SELECT json_extract(detail,'$.autonomy.key') FROM web_events WHERE "
                                     "kind='decision' AND created_at>=? AND json_extract(detail,'$.autonomy.key') "
                                     "IS NOT NULL", (now - cooldown,)) if k}


def _lineage(db):
    """depth(key) -> (derivation depth, originating owner subjects) from the records (T1-02).

    A subject's depth is the smallest over its records (claims, hypotheses, knowledge, feedback): 0 for the owner's
    own records (owner hypotheses, owner-asserted or owner-accepted knowledge) and for anything the owner's research
    found; for a record of an autonomous task, 1 + the depth of the subject that task was queued to investigate (its
    decision event; a refresh task investigates its knowledge item; an autonomous task without either counts as one
    step from its own objective). The originating owner subject of a depth-1 subject is the depth-0 subject that
    the autonomous task investigated (its objective and key). Unknown keys are depth 0."""
    tasks = {tid: (str(origin), objective) for tid, origin, objective in
             db.execute("SELECT id, origin, objective FROM web_tasks")}
    investigated = {}
    for tid, key in db.execute("SELECT task_id, json_extract(detail,'$.autonomy.key') FROM web_events WHERE "
                               "kind='decision' AND task_id IS NOT NULL AND json_extract(detail,'$.autonomy.key') "
                               "IS NOT NULL ORDER BY id"):
        investigated.setdefault(tid, key)
    refreshed = {str(kid): key for kid, key in db.execute("SELECT id, key FROM web_knowledge")}
    sources: dict = {}
    for key, tid in db.execute("SELECT DISTINCT key, task_id FROM web_claims UNION SELECT DISTINCT key, task_id "
                               "FROM web_feedback WHERE task_id IS NOT NULL"):
        sources.setdefault(key, set()).add(tid)
    for key, tid, origin in db.execute("SELECT key, task_id, origin FROM web_hypotheses"):
        sources.setdefault(key, set()).add(None if origin == "owner" or tid is None else tid)
    for key, tid, provenance, status in db.execute("SELECT key, task_id, provenance, status FROM web_knowledge"):
        owner = provenance == "user_asserted" or status == "accepted" or tid is None
        sources.setdefault(key, set()).add(None if owner else tid)
    memo: dict = {}

    def task_depth(tid):
        origin, objective = tasks.get(tid, ("owner", ""))
        if tid is None or scope_of(origin) != "autonomous":
            return 0, frozenset()
        key = investigated.get(tid) or (refreshed.get(origin.partition(":")[2]) if origin.startswith("refresh:")
                                        else None)
        if key is None:
            return 1, frozenset({objective or ""})
        d, roots = depth(key)
        return (1, frozenset({f"{objective} {key}"})) if d == 0 else (d + 1, roots)

    def depth(key):
        if key in memo:
            return memo[key]
        memo[key] = (math.inf, frozenset())       # a cycle of autonomous tasks never reaches the owner
        if key not in sources:
            memo[key] = (0, frozenset())
            return memo[key]
        found = [task_depth(t) for t in sources[key]]
        best = min(d for d, _ in found)
        memo[key] = (best, frozenset().union(*(r for d, r in found if d == best)))
        return memo[key]

    return depth


def _overlap_share(a: set, b: set) -> float:
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def candidates(db, now, *, limit=10, offered=()) -> dict:
    """{"chosen": ranked candidates at or above min_gain, "refused": those below it or cooling down}. Each has
    kind, key, statement, objective, mode, info_gain, cost, signals, priority, owner, depth and the reason.

    The owner's own records (open owner hypotheses, owner-asserted knowledge without evidence, contested owner
    assertions) are always considered, never cut by MAX_CANDIDATES, and listed ahead of every derived candidate
    (chosen and refused alike); `total` counts both lists before `limit`, so nothing is dropped silently (T1-03).
    A derived subject that only autonomous research produced is pursued only within MAX_DERIVATION_DEPTH steps of
    an owner record and when it shares MIN_RELEVANCE of its words with the owner subject it came from (T1-02).
    `offered`: further candidates offered by a caller (the knowledge re-check, recheck.rank: {kind, key, statement,
    mode, info_gain, signals, extra}); they go through exactly the same rule as every other candidate."""
    from . import claims, curiosity, evolution, intent
    from .config import MODE_BUDGETS
    pol = policy(db)
    min_gain, cooldown = float(pol["min_gain"]), float(pol["cooldown"])
    cap = float(pol["autonomous_daily"].get("spend_usd", 0) or 0)
    prices = pol["prices_usd"]
    goal_words = [_words(g) for g in goals(db, now)]
    changed = _changed_keys(db, now)
    changed_words = [_words(k) for k in changed]
    recent = _recent(db, now, cooldown)
    lineage = _lineage(db)
    asserted = {kid for (kid,) in db.execute("SELECT id FROM web_knowledge WHERE provenance='user_asserted'")}
    read = evolution.recalibrations(db)      # ADR-046: a forecast as the settled forecasts of its kind read it
    by_key: dict = {}

    def add(kind, key, statement, mode, gain, signals, extra=None, *, gain_as_read=None):
        """`gain` (from the stated forecast) decides which candidate of a subject wins and whether it clears
        min_gain; `gain_as_read` (ADR-046, the forecast read through its kind's settled forecasts) only its
        priority among the admitted candidates."""
        if not key:
            return
        words = _words(statement) | _words(key)
        signals = dict(signals)
        signals["goal"] = round(max((_overlap(words, g) for g in goal_words), default=0.0), 6)
        signals["source_change"] = 1.0 if key in changed or any(_overlap(_words(key), c) >= 0.5
                                                                for c in changed_words) else 0.0
        read_gain = gain if gain_as_read is None else gain_as_read
        found = by_key.get(key)
        if found is not None:     # one subject, one candidate: strongest signals and gain win
            for s, v in signals.items():
                found["signals"][s] = max(found["signals"].get(s, 0.0), v)
            if gain > found["info_gain"]:
                found.update(kind=kind, info_gain=gain, mode=mode, _read_gain=read_gain)
            owner = found.get("owner") or (extra or {}).get("owner")
            found.update(extra or {})
            found["owner"] = bool(owner)
            return
        by_key[key] = {"kind": kind, "key": key, "statement": statement, "mode": mode, "info_gain": gain,
                       "_read_gain": read_gain, "signals": signals} | (extra or {})

    for c in curiosity.rank(db, now, limit=MAX_CANDIDATES):
        if c["excluded"]:
            continue
        target = c["target"]
        signals = {"contradiction": 1.0 if c["kind"] in ("contested", "hypothesis") else 0.0,
                   "stale": 1.0 if c["kind"] == "stale" else 0.0, "gap": 1.0 if c["kind"] == "gap" else 0.0,
                   "hypothesis": 1.0 if c["kind"] == "hypothesis" else 0.0}
        extra = {"knowledge_id": target["knowledge_id"]} if "knowledge_id" in target else {}
        if "learning_progress" in c:     # ADR-051: shown and logged with the candidate
            extra["learning_progress"] = c["learning_progress"]
        if target.get("knowledge_id") in asserted:
            extra["owner"] = True
        if c["kind"] == "prediction":
            row = db.execute("SELECT probability FROM web_predictions WHERE id=?",
                             (target.get("prediction_id"),)).fetchone()
            stated = state.finite(row[0] if row else None, 0.5)
            p = evolution.recalibrate(stated, read["knowledge"])
            signals["prediction_uncertainty"] = round(curiosity.entropy(p), 6)
            extra["prediction_id"] = target.get("prediction_id")
            if p != stated:                  # shown and logged only where the reading changed the number
                extra |= {"forecast": stated, "forecast_as_read": p}
        add(c["kind"], target.get("key"), target.get("statement") or "", c["mode"], float(c["info_gain"]), signals,
            extra)
    # Every open owner hypothesis (with its latest open prediction, if any), then the newest derived ones.
    rows = db.execute("SELECT (SELECT max(p.id) FROM web_predictions p WHERE p.hypothesis_id=h.id AND p.outcome IS "
                      "NULL), h.id FROM web_hypotheses h WHERE h.status='open' AND h.origin='owner' ORDER BY h.id"
                      ).fetchall()
    rows += db.execute("SELECT p.id, p.hypothesis_id FROM web_predictions p JOIN web_hypotheses h ON "
                       "h.id=p.hypothesis_id WHERE p.outcome IS NULL AND h.status='open' AND h.origin!='owner' "
                       "ORDER BY p.id DESC LIMIT ?", (MAX_CANDIDATES,)).fetchall()
    lp_on = curiosity.learning_progress_on(db)
    for pid, hid in rows:
        h = evolution.hypothesis(db, hid)
        found = db.execute("SELECT probability FROM web_predictions WHERE id=?", (pid,)).fetchone() if pid else None
        stated = state.finite(found[0] if found else h["verification"].get("confidence"), 0.5)
        p = evolution.recalibrate(stated, read["owner" if h["origin"] == "owner" else "hypothesis"])
        gain, gain_as_read, lp = curiosity.info_gain(stated), curiosity.info_gain(p), {}
        if lp_on and h["origin"] != "owner":     # ADR-051: learning progress, never on the owner's own records
            try:        # Wave 2 lane U: a failing read leaves the gain as with the setting off, said and logged
                factor, _, record = curiosity.progress(db, h["key"], gain, now)
                factor = float(factor)
            except Exception as exc:      # noqa: BLE001 - recorded with the candidate and logged, never silent
                log.warning("learning progress of hypothesis #%s could not be read (%s)", hid, type(exc).__name__)
                factor, record = 1.0, {"error": f"could not be read ({type(exc).__name__})"}
            gain, gain_as_read, lp = gain * factor, gain_as_read * factor, {"learning_progress": record}
        add("hypothesis_prediction", h["key"], h["statement"], "fact_verification", gain,
            {"hypothesis": 1.0, "prediction_uncertainty": round(curiosity.entropy(p), 6),
             "contradiction": 1.0 if h["origin"] in ("conflicting", "disputed") else 0.0,
             "gap": 1.0 if h["origin"] in ("insufficient", "gap", "not_evaluated") else 0.0},
            {"hypothesis_id": hid, "prediction_id": pid, "forecast": stated} |
            ({"forecast_as_read": p} if p != stated else {}) |
            ({"owner": True} if h["origin"] == "owner" else {}) | lp,
            gain_as_read=gain_as_read)
    # The owner's assertions that no evidence has been examined for yet (ADR-038: not_evaluated, no support).
    for kid, key, statement, confidence in db.execute(
            "SELECT id, key, statement, confidence FROM web_knowledge WHERE provenance='user_asserted' AND "
            f"status IN ({','.join('?' * len(state.LIVE_KNOWLEDGE))}) AND supporting='[]' ORDER BY id",
            state.LIVE_KNOWLEDGE).fetchall():
        p = state.finite(confidence, 0.5)
        add("owner_assertion", key, statement, "fact_verification", curiosity.info_gain(p),
            {"gap": 1.0, "prediction_uncertainty": round(curiosity.entropy(p), 6)}, {"knowledge_id": kid,
                                                                                    "owner": True})
    for item in offered:        # lane K: the knowledge re-check's pool, admitted by this same rule
        add(item["kind"], item["key"], item.get("statement") or "", item["mode"], float(item["info_gain"]),
            item.get("signals") or {}, item.get("extra"))
    chosen, refused = [], []
    from . import salience
    salient = salience.enabled(db)
    for item in by_key.values():
        if salient:             # ADR-051: one explainable score per candidate, recorded; it changes no decision
            try:        # Wave 2 lane U: a record that cannot be made is said in the record and logged
                item["salience"] = salience.score(db, item, now)
            except Exception as exc:      # noqa: BLE001 - recorded with the candidate and logged, never silent
                log.warning("the salience record of a %s candidate (knowledge #%s, hypothesis #%s) could not be "
                            "made (%s)", item.get("kind"), item.get("knowledge_id"), item.get("hypothesis_id"),
                            type(exc).__name__)
                item["salience"] = {"error": f"could not be made ({type(exc).__name__})"}
        mode_cost = curiosity.cost(item["mode"])
        budget = MODE_BUDGETS.get(item["mode"], MODE_BUDGETS["fact_verification"])
        est = budget.max_queries * float(prices.get("search", 0)) + budget.max_pages * float(prices.get("page", 0))
        item["cost"] = round(mode_cost + (est / cap if cap > 0 else 0.0), 6)
        weighted = sum(WEIGHTS[s] * float(v) for s, v in item["signals"].items() if s in WEIGHTS)
        read_gain = item.pop("_read_gain")
        item["priority"] = round(read_gain * (1.0 + weighted) / item["cost"], 6) if item["cost"] else 0.0
        item["info_gain"] = round(item["info_gain"], 6)        # min_gain and every other admission use this one
        if round(read_gain, 6) != item["info_gain"]:
            item["info_gain_as_read"] = round(read_gain, 6)
        statement = item["statement"] or ""
        if item["kind"] == "hypothesis_prediction" and statement:
            # a hypothesis is checked as the statement it makes (fact verification of its claim slots); the
            # orchestrator plans the queries and the privacy screen applies at submit
            item["objective"] = " ".join(statement.split())[:300]
        else:
            item["objective"] = (curiosity.search_terms(statement, item["key"], db) if statement else
                                 curiosity.search_terms(None, item["key"], db))[:300]
        item["owner"] = bool(item.get("owner"))
        depth, roots = (0, frozenset()) if item["owner"] else lineage(item["key"])
        item["depth"] = depth if depth != math.inf else None
        relevance = max((_overlap_share(_words(statement) | _words(item["key"]), _words(r)) for r in roots),
                        default=0.0)
        shape = None if item["owner"] or "knowledge_id" in item else curiosity.claim_shaped(statement)
        if item["info_gain"] < min_gain:
            refused.append(item | {"reason": f"expected information gain {item['info_gain']:.3f} bits < minimum "
                                             f"{min_gain:g}: not researched (no browsing to generate activity)"})
        elif depth > MAX_DERIVATION_DEPTH:
            refused.append(item | {"reason": "derived from autonomous research "
                                             f"{'in a cycle' if depth == math.inf else f'{depth} steps'} away from "
                                             f"the owner's records (at most {MAX_DERIVATION_DEPTH}): not pursued "
                                             "(autonomy does not feed on its own by-products)"})
        elif depth >= 1 and relevance < MIN_RELEVANCE:
            root = min(roots, key=len, default="")
            refused.append(item | {"reason": f"known only from autonomous research and unrelated to the owner subject "
                                             f"it came from («{' '.join(root.split())[:80]}», overlap "
                                             f"{relevance:.2f} < {MIN_RELEVANCE:.2f}): not pursued (autonomy does "
                                             "not feed on its own by-products)"})
        elif item["key"] in recent:
            refused.append(item | {"reason": f"investigated autonomously within the last {cooldown:g} s"})
        elif claims.record_template(statement) is not None:
            refused.append(item | {"reason": "a structured record's template is re-read by its own refresh"})
        elif shape is not None:
            refused.append(item | {"reason": f"{shape}: not researched"})
        elif not item["objective"] or not intent.query_text(item["objective"]):
            refused.append(item | {"reason": "nothing searchable in the statement"})
        else:
            why = ("; the owner's own record: ranked ahead of derived candidates" if item["owner"] else
                   f"; derived 1 step from an owner subject (overlap {relevance:.2f})" if depth else "")
            gain_text = (f"gain as read {read_gain:.3f} (as stated {item['info_gain']:.3f}, which cleared the "
                         f"minimum)" if "info_gain_as_read" in item else f"gain {item['info_gain']:.3f}")
            chosen.append(item | {"reason": f"priority {item['priority']:.4f} = {gain_text} x "
                                            f"(1 + {weighted:.3f}) / cost {item['cost']:.3f}{why}"})
    chosen.sort(key=lambda c: (not c["owner"], -c["priority"], c["key"]))
    refused.sort(key=lambda c: (not c["owner"], -c["info_gain"], c["key"]))
    return {"chosen": chosen[:limit], "refused": refused[:limit], "min_gain": min_gain, "weights": dict(WEIGHTS),
            "total": {"chosen": len(chosen), "refused": len(refused)}}


# ---- the one admission rule (ADR-050) ------------------------------------------------------------------------------

ADMISSION_RULES = ("unified", "legacy")
_TICK_SCOPE = "autonomous research is limited to items already in knowledge and owner monitors; propose it instead"


def admission(db, now, *, path, rule="unified", limit=5, offered=()) -> dict:
    """The one admission rule for autonomous investigations: both launch paths (the worker's idle investigation,
    path 'idle', and the timer's curiosity, path 'tick') ask here. {"rule", "path", "gate": None or why nothing
    may be admitted, "chosen": [...], "refused": [...]}; a chosen item carries objective, mode, key and the
    inputs it was ranked by, and `rule` is recorded with the task that item becomes.

    'unified' (the default; ADR-050): the owner's recorded enable (enabled()), then every candidate through
    candidates(): min_gain on the stated forecast, derivation depth and relevance to the owner's subject, the
    cooldown, record templates, claim shapes and searchable words, ranked by goal-weighted priority. The tick keeps
    its own pool on top, exactly as before (knowledge items among curiosity.rank's ten best, each of the kind the
    ranking gave it; due predictions left to its refreshes), so it can only admit fewer subjects than the legacy
    rule, never others, and none of another kind or mode (a subject a due prediction or a hypothesis took in
    candidates() is refused).
    'legacy' (path 'tick' only, kept one release: `nsf web config --set tick_admission="legacy"`): the tick's rule
    before ADR-050 - curiosity.rank's own floor (curiosity.MIN_GAIN), knowledge items only, no goal weighting, no
    lineage, no cooldown, no recorded enable (the `autonomous` switch suffices), the statement's own words as the
    objective.
    Path 'recheck' (lane K, recheck.py): the pool is `offered`, the knowledge re-check's ranked items, admitted by the
    'unified' rule through candidates(); the admitted ones keep the pool's order (value), each with its
    knowledge_id.
    When strategy selection is in force (strategies.active: promoted through its gate, never by default), the admitted
    candidates are re-ordered by it; it never admits or refuses one."""
    if rule not in ADMISSION_RULES or (rule == "legacy" and path != "tick") or (path == "recheck" and rule != "unified"):
        raise ValidationError(f"admission rule {rule!r} does not apply to path {path!r}")
    out = {"rule": rule, "path": path, "gate": None, "chosen": [], "refused": []}
    if rule == "legacy":
        from . import claims, curiosity, intent
        for c in curiosity.rank(db, now, limit=10):
            target = c["target"]
            statement = target.get("statement") or ""
            why = (c["reason"] if c["excluded"] else _TICK_SCOPE if "knowledge_id" not in target else
                   None if c["kind"] == "prediction" else
                   "a structured record's template is not a statement to research; the record is re-read by its "
                   "own refresh" if claims.record_template(statement) is not None else
                   "nothing searchable in the statement" if not intent.query_text(statement) else "")
            if why is None:
                continue        # a due prediction: handled by predictions.check_due
            if why:
                out["refused"].append({"target": target, "reason": why})
                continue
            out["chosen"].append({"kind": c["kind"], "key": target.get("key"), "mode": c["mode"],
                                  "info_gain": c.get("info_gain"), "knowledge_id": target["knowledge_id"],
                                  "target": target, "objective": intent.query_text(statement)})
        return out
    if not enabled(db):
        out["gate"] = ("autonomy is not enabled by a recorded owner switch (`nsf web autonomy enable` or the "
                       "console switch)")
        return out
    if path == "recheck":     # lane K: only the re-check's own pool, in its order, by the same rule
        order = {item["key"]: (n, item) for n, item in enumerate(offered)}
        ranked = candidates(db, now, limit=10 ** 6, offered=offered)
        out["min_gain"] = ranked["min_gain"]
        out["refused"] = [r for r in ranked["refused"] if r["key"] in order]
        picked = sorted((c for c in ranked["chosen"] if c["key"] in order), key=lambda c: order[c["key"]][0])
        out["chosen"] = [c | {k: order[c["key"]][1][k] for k in ("knowledge_id", "value", "p_current", "dependents",
                                                                 "mark_id")} for c in picked][:limit]
        return out
    pool = None
    if path == "tick":       # the tick's own pool, as before: knowledge items among curiosity.rank's top ten
        from . import curiosity
        pool = {c["target"].get("key"): c["kind"] for c in curiosity.rank(db, now, limit=10)
                if not c["excluded"] and "knowledge_id" in c["target"] and c["kind"] != "prediction"}
    ranked = candidates(db, now, limit=10 ** 6 if path == "tick" else limit)
    out["min_gain"], out["refused"] = ranked["min_gain"], ranked["refused"]
    for item in ranked["chosen"]:
        if pool is not None and item["key"] not in pool:
            out["refused"].append(item | {"reason": _TICK_SCOPE if "knowledge_id" not in item else
                                          "a due prediction is refreshed by predictions.check_due"
                                          if item["kind"] == "prediction" else
                                          "not among the timer's ten best curiosity candidates (curiosity.rank)"})
            continue
        if pool is not None and item["kind"] != pool[item["key"]]:
            # candidates() keeps one item per subject: a due prediction or a hypothesis on a pooled subject may have
            # taken it, in a shape the timer never launched (review0b): the refresh or the idle investigator has it
            out["refused"].append(item | {"reason": "a due prediction is refreshed by predictions.check_due"
                                          if item["kind"] == "prediction" else
                                          f"the timer investigates this subject as {pool[item['key']]}, not as "
                                          f"{item['kind']} (the idle investigator's)"})
            continue
        out["chosen"].append(item)
    from . import salience
    try:                # Wave 2 lane X: an ordering that fails leaves admission's own order, said and logged
        attend = salience.attention(db)
        if not isinstance(attend, bool):
            raise TypeError(f"salience_attention read as {type(attend).__name__}, not true or false")
        if attend:      # ADR-056: salience orders what admission admitted; it admits nothing itself
            ordered = salience.order(out["chosen"])
            if not (isinstance(ordered, tuple) and len(ordered) == 2 and isinstance(ordered[0], list)
                    and isinstance(ordered[1], dict) and sorted(str(c.get("key")) for c in ordered[0])
                    == sorted(str(c.get("key")) for c in out["chosen"])):
                raise TypeError("the salience order is not a reordering of the admitted candidates")
            out["chosen"], out["order"] = ordered
    except Exception as exc:              # noqa: BLE001 - recorded with the admission and logged, never silent
        log.warning("the salience order could not be applied (%s); the admitted order stands", type(exc).__name__)
        out["order"] = {"by": "priority", "error": f"the salience order could not be applied ({type(exc).__name__})"}
    from . import strategies
    try:                # Wave 2 lane U: a gate that cannot be read is closed (the admitted order), said and logged
        active = strategies.active(db)
    except Exception as exc:              # noqa: BLE001 - recorded with the admission and logged, never silent
        log.warning("strategy selection's gate could not be read (%s); the admitted order stands",
                    type(exc).__name__)
        active, out["strategy"] = False, {"error": f"the gate could not be read ({type(exc).__name__})"}
    if not isinstance(active, bool):      # Wave 2 lane X: only `True` opens the gate; anything else is a failure
        log.warning("strategy selection's gate returned %s, not true or false; the admitted order stands",
                    type(active).__name__)
        active, out["strategy"] = False, {"error": f"the gate returned {type(active).__name__}, not a boolean"}
    if active is True:  # lane K: ranks the admitted candidates only, once promoted through its gate
        out["chosen"], out["strategy"] = strategies.rank(db, now, out["chosen"])
    out["chosen"] = out["chosen"][:limit]
    return out


def decision_inputs(pick, admitted) -> dict:
    """What a queued investigation's decision event records: its ranking inputs and the rule that admitted it."""
    inputs = {k: pick[k] for k in ("kind", "key", "mode", "info_gain", "cost", "priority", "signals", "reason")
              if k in pick}
    inputs |= {k: pick[k] for k in ("hypothesis_id", "prediction_id", "knowledge_id", "forecast",
                                    "forecast_as_read", "info_gain_as_read", "owner", "depth", "salience",
                                    "learning_progress", "priority_rank") if k in pick}
    admission = {"rule": admitted["rule"], "path": admitted["path"]}
    if admitted.get("order"):           # ADR-056: taken in salience order (salience.order)
        admission["order"] = admitted["order"]["by"]
    return inputs | {"admission": admission}


# ---- the idle investigator -----------------------------------------------------------------------------------------

MAX_IDLE_WORKERS = 32


def _worker() -> str:
    """The calling worker: its process and thread (scheduler.work runs one worker per thread or process)."""
    import os
    import threading
    return f"{os.getpid()}:{threading.get_ident()}"


def _decide(db, now, detail, task_id=None, *, worker=None):
    with store.write(db):
        state.log_event(db, task_id, "decision", {"autonomy": detail} if isinstance(detail, str) else detail, now)
        raw = _meta(db)
        raw["last_idle_at"] = now
        if worker is not None:
            idle_at = dict(raw.get("idle_at") or {}) | {worker: now}
            raw["idle_at"] = dict(sorted(idle_at.items(), key=lambda kv: -float(kv[1] or 0))[:MAX_IDLE_WORKERS])
        _write_meta(db, raw)


def idle(db, now, *, worker=None) -> dict:
    """One idle-investigation decision of a worker with nothing queued: when autonomy is enabled (recorded), the
    budget has room, fewer than `concurrent` investigations run and the best candidate clears min_gain, queue it
    as an autonomous task (origin 'curiosity') with its ranking inputs logged. At most once per idle_interval per
    worker (default: the calling process and thread), so two workers can run two investigations at once."""
    from ..instance.errors import Conflict, WritesPaused
    from .. import background
    from . import orchestrator
    ok, why = background.allowed(db, "worker.idle_investigation")
    if not ok:              # the owner's quiet (ADR-050): no decision event, nothing is ranked
        return {"action": "quiet", "reason": why}
    if not enabled(db):
        return {"action": "off"}
    pol = policy(db)
    worker = str(worker or _worker())
    last = state.finite((_meta(db).get("idle_at") or {}).get(worker))
    if last is not None and now - last < float(pol["idle_interval"]):
        return {"action": "waiting", "next_at": last + float(pol["idle_interval"])}
    reason = refusal(db, "autonomous", now)
    if reason:
        _decide(db, now, {"autonomy": "refused", "reason": reason}, worker=worker)
        return {"action": "refused", "reason": reason}
    if full(db, now):
        return {"action": "busy", "running": running(db, now)}
    ranked = admission(db, now, path="idle", limit=5)
    if not ranked["chosen"]:
        best = ranked["refused"][0] if ranked["refused"] else None
        why = best["reason"] if best else "no candidate in the records"
        _decide(db, now, {"autonomy": "nothing worth researching", "reason": why,
                          "best_gain": best["info_gain"] if best else None, "min_gain": ranked["min_gain"]},
                worker=worker)
        return {"action": "nothing", "reason": why, "refused": ranked["refused"]}
    for pick in ranked["chosen"]:
        if pick["key"] in _recent(db, now, float(pol["cooldown"])):
            continue            # another worker queued this subject since the ranking was read
        try:
            # a hypothesis' task carries its id, so the prediction settles on that link (evolution.observe)
            context = {"hypothesis": pick["hypothesis_id"]} if pick.get("hypothesis_id") else None
            task = orchestrator.submit(db, pick["objective"], now, mode=pick["mode"], origin="curiosity",
                                       context=context)
        except (ValidationError, Conflict, WritesPaused) as exc:
            if isinstance(exc, BudgetRefused):
                _decide(db, now, {"autonomy": "refused", "reason": str(exc)}, worker=worker)
                return {"action": "refused", "reason": str(exc)}
            continue
        inputs = decision_inputs(pick, ranked)
        _decide(db, now, {"autonomy": inputs | {"weights": dict(WEIGHTS), "min_gain": ranked["min_gain"]}},
                task_id=task["id"], worker=worker)
        return {"action": "queued", "task_id": task["id"], "candidate": inputs}
    _decide(db, now, {"autonomy": "nothing worth researching", "reason": "every candidate was refused at submit"},
            worker=worker)
    return {"action": "nothing", "reason": "every candidate was refused at submit"}


# ---- meaningful findings -> the owner's pending questions ------------------------------------------------------------

SALIENCE_SOURCE = "webcortex/salience.py: the salience record its candidate carried when it was queued"


def _candidate_salience(db, task_id) -> tuple[float | None, dict | None]:
    """(score, inputs) of the salience record (salience.py, ADR-051) that the task's decision event stored when it was
    queued (decision_inputs), for selective broadcast; (None, None) when it has none (salience_record off, the legacy
    tick rule, a task queued before the record existed): broadcast.score's local function applies then."""
    for (detail,) in db.execute("SELECT detail FROM web_events WHERE task_id=? AND kind='decision' ORDER BY id DESC",
                                (task_id,)):
        picked = (state.loads(detail, {}) or {}).get("autonomy")
        record = picked.get("salience") if isinstance(picked, dict) else None
        score = state.finite(record.get("score")) if isinstance(record, dict) else None
        if score is not None:
            return score, {"source": SALIENCE_SOURCE, "explanation": record.get("explanation"),
                           "signals": record.get("signals")}
    return None, None


def after_task(db, task_id, now) -> dict | None:
    """For a finished autonomous task: a notification (the owner's pending question web:autonomy:<task>) only for a
    meaningful finding - a new verified claim (knowledge candidate from a supported
    cluster), a resolved contradiction (a settled prediction of a hypothesis that came from a contradiction) or a
    settled prediction (confirmed or error). Nothing else notifies. The question is opened whatever the owner's web
    question budget (config.max_open_web_questions): reporting a finding is what the owner switched autonomy on for,
    the autonomous budget already bounds how many there can be, and a capped-out notification used to be dropped
    while the log said "notified" (R1-01). "notified" is recorded only with the pending id it was delivered as;
    otherwise "finding not delivered". Returns the findings or None."""
    from . import knowledge
    task = state.task(db, task_id)
    if scope_of(task.get("origin")) != "autonomous" or task["status"] not in TERMINAL_STATES:
        return None
    consolidation = (task.get("result") or {}).get("consolidation") or {}
    settled = [s for s in (task.get("checkpoint") or {}).get("predictions_settled") or []
               if s.get("outcome") in ("confirmed", "error")]
    resolved = []
    for s in settled:
        row = db.execute("SELECT origin FROM web_hypotheses WHERE id=?", (s.get("hypothesis_id"),)).fetchone()
        if row and row[0] in ("conflicting", "disputed"):
            resolved.append(s)
    findings = {"new_verified": list(consolidation.get("provisional") or []), "resolved_contradictions": resolved,
                "settled_predictions": settled}
    if not any(findings.values()):
        with store.write(db):
            state.log_event(db, task_id, "decision", {"autonomy": "no meaningful finding: no notification"}, now)
        return findings
    parts = []
    if findings["new_verified"]:
        parts.append(f"{len(findings['new_verified'])} newly verified statement(s) (knowledge candidates)")
    if resolved:
        parts.append(f"{len(resolved)} contradiction(s) settled by new evidence")
    if settled:
        outcome = ", ".join(f"#{s['prediction_id']} {s['outcome']}" for s in settled[:5])
        parts.append(f"{len(settled)} prediction(s) settled ({outcome})")
    text = (f"Autonomous research «{knowledge.quote(task['objective'])[:120]}» found: {'; '.join(parts)}. "
            "See the console (Research) or `nsf web evidence` for the sources; answer to dismiss.")
    from .. import broadcast
    scope = f"web:autonomy:{task_id}"
    signals = {"new_verified": len(findings["new_verified"]), "resolved_contradictions": len(resolved),
               "settled_predictions": sum(1 for s in settled if s.get("outcome") == "confirmed"),
               "prediction_errors": sum(1 for s in settled if s.get("outcome") == "error")}
    # the salience of what the task found (broadcast.score over the findings) and, when it carried one, its
    # candidate's own pre-task salience record (ADR-051): the higher of the two, so a finding is never judged only
    # by what was expected of it before the task ran (review1 A7); the inputs name both
    salience, salience_inputs = broadcast.score("autonomy.finding", signals)
    candidate, candidate_inputs = _candidate_salience(db, task_id)
    if candidate is not None:
        salience_inputs = {"rule": "max(finding salience, candidate salience)",
                           "finding": {"score": salience, **salience_inputs},
                           "candidate": {"score": candidate, **candidate_inputs}}
        salience = max(salience, candidate)
    with store.write(db):
        # selective broadcast (ADR-053): mode off (the default) delivers exactly as before. The salience is the
        # higher of the findings' own (broadcast.score) and the candidate's salience record.
        offered = broadcast.offer(db, "autonomy.finding", now=now, signals=signals, scope=scope, task_id=task_id,
                                  deliver=lambda: knowledge.open_question(db, scope, text, now),
                                  salience=salience, salience_inputs=salience_inputs)
        findings["pending_id"] = offered["pending_id"]
        delivered = findings["pending_id"] is not None
        kept = offered["outcome"] == "local"
        state.log_event(db, task_id, "decision", {"autonomy": "notified" if delivered else
                                                  f"finding kept local ({offered['reason']})" if kept else
                                                  "finding not delivered",
                                                  "pending_id": findings["pending_id"],
                                                  "findings": {k: len(v) if isinstance(v, list) else v
                                                               for k, v in findings.items()}}, now)
        store.bump_state_version(db)
    return findings


# ---- CLI (`nsf web autonomy ...`) ------------------------------------------------------------------------------------

def cli(db, a, now):
    action = a.autonomy_action
    if a.policy_json and action != "set":
        raise ValidationError("--policy-json applies only to `set`")
    if action == "enable":
        enable(db, now, by="owner:cli")
    elif action == "disable":
        disable(db, now, by="owner:cli")
    elif action == "set":
        if not a.policy_json:
            raise ValidationError("set needs --policy-json")
        import json
        try:
            changes = json.loads(a.policy_json)
        except ValueError as exc:
            raise ValidationError(f"--policy-json is not JSON: {exc}") from None
        set_policy(db, changes, now)
    elif action == "candidates":
        return candidates(db, now, limit=a.limit)
    elif action == "idle":
        return idle(db, now)
    return status(db, now)
