"""Research tasks: submit, run, cancel, resume (ARCHITECTURE §2, §4.4, §5, §7).

A task is a row in web_tasks. `run` claims a lease and executes the stages, writing one episodic event per step
and a checkpoint after each stage, all in short transactions (no write transaction is ever held across network
I/O):

  discovering   knowledge.query + intent.assess; when the web is not needed the report is built from fresh
                knowledge that covers the question (decision event says why; provisional items are marked
                'provisional' in the answer and the uncertainties); otherwise intent.plan (references to the
                individual's own system are local context, not queries) -> privacy-screened queries (owner-approved
                disclosure covers the private text of the task's own objective) -> registry.search -> at most one
                intent.refine round
  reading       ranked candidates (<= 3 x budget.max_pages): named URLs, structured API results (their
                metadata/abstract is the document, kind 'api', nothing fetched; for a monitor's task also feed
                items), then web results, deduplicated by canonical URL; read in order until max_pages documents
                SUCCEED (a failed fetch costs requests, not a page; the request/byte/time budgets bound the
                attempts); scientific/deep_research keep ceil(max_pages/3) slots for web pages. Pages are fetched
                through SafeFetcher (purpose 'page'), extracted, quarantined; JavaScript shells escalate to the
                renderer when the budget has browser sessions; deep_research follows <= 2 same-site links of the
                first page when depth and pages remain. Each document is recorded (web_sources, event, checkpoint)
                as soon as it is read; the lease is renewed after every fetch. API records are quarantined like
                pages. An unexpected exception while extracting one document (or reading it back from the cache)
                is that document's failure ('decode_error' or 'internal_error' with the exception type, an
                'error' event), never the task's. A crawl task (`nsf web crawl`, context.crawl) reads its seeds by
                a bounded same-site crawl (crawl.crawl) instead of searching.
                Reading is deliberately serial: cancellation is honoured before every page request (a cancel never
                races several requests already in flight), a crash loses at most the page being read, page
                accounting cannot overshoot max_pages, and SafeFetcher's per-host spacing would serialise most
                same-site candidates anyway; the parallel part of a task is discovery (providers run in
                budget.max_concurrency threads).
  evaluating    claims.extract_claims per document ('extract' event each; one document failing is recorded and
                skipped) -> claims.cluster -> claims.score (config.freshness_ttl as ttl_overrides); clusters persisted
  synthesizing  synthesis.synthesize -> the report (§5, 'synthesized' event) with its context pack
                (synthesis.context_pack within budget.max_context_tokens, charged to usage.tokens); then
                consolidate.run, predictions for new provisional knowledge, mode statistics (procedural memory),
                public-cache retention, and change detection when the task belongs to a monitor

Cancellation (`cancel_requested`, set from any connection) is checked before every network step; a cancelled run
ends 'cancelled' with a partial report. The same check stops a run whose lease another worker took over (its
writes are fenced on lease_holder: state.LostLease; it never consolidates nor writes an outcome) and a run while
instance regulation pauses writes (it ends 'partial' and consolidates nothing; submit refuses with WritesPaused). Time is measured with services.clock against budget.max_seconds. A run
that exhausts its budget or loses a stage ends 'partial' when it still has a report, 'failed' when it has none.
`resume` continues from the checkpoint: completed stages are skipped and documents already read are re-read from
the public cache, so the report then says retrieval_mode 'cache' (live false) for them. `requeue` puts a failed
task back in the queue (same id) or continues a partial/cancelled one as a linked child task.
Autonomous tasks (curiosity, refresh:<id>) get request/byte budgets clamped to the daily allowance left at submit,
and MCP tasks (origin 'mcp') to config.daily_mcp. The owner's own refreshes (origin 'cli'/'console') are not
autonomous.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import math
import os
import socket
import sqlite3
import uuid

from ..instance import store
from ..instance.errors import Conflict, LeaseHeld, ValidationError
from . import state
from .types import (MODES, TERMINAL_STATES, Budget, BudgetExceeded, Claim, ClaimCluster, Document, FetchError,
                    FetchResult, QueryPlan, SearchResult, Usage)

LEASE_SECONDS = 600.0
AUTHORIZED_DISCLOSURE = "owner-approved-disclosure"
MCP_ORIGIN = "mcp"
MAX_OBJECTIVE = 2000
MAX_FOLLOW = 2
MAX_CONTEXT_PACKAGES = 200
CANDIDATE_FACTOR = 3          # reading ranks up to 3 x max_pages candidates; failed fetches do not use a page slot
WEB_RESERVE_MODES = ("scientific", "deep_research")
API_PROVIDERS = ("wikipedia", "arxiv", "openalex", "crossref", "pubmed", "pypi", "npm", "osv", "github")
_PUBLIC = ("id", "objective", "mode", "status", "stage", "origin", "family", "parent_id", "budget", "usage",
           "result", "error", "executed_on", "created_at", "updated_at", "started_at", "finished_at",
           "cancel_requested")


class _Cancelled(Exception):
    pass


class _OutOfTime(Exception):
    pass


class _Paused(Exception):
    """Instance regulation paused writes while the task ran: it stops at the next check and ends 'partial'."""


# Control flow that per-document isolation must never swallow.
_STOP = (_Cancelled, _OutOfTime, _Paused, BudgetExceeded, state.LostLease)


def executed_on() -> dict:
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except OSError:
        load = None
    return {"host": hashlib.sha256(socket.gethostname().encode()).hexdigest()[:12], "cpus": os.cpu_count(),
            "load": load}


# ---- privacy -----------------------------------------------------------------------------------------------------

def private_terms(db, cfg) -> list[str]:
    """Owner-listed private terms plus every camera and partner root of the evidence journal (private names)."""
    roots = [r for (r,) in db.execute("SELECT DISTINCT root FROM evidence WHERE channel IN ('camera','word')")]
    return list(dict.fromkeys([*cfg.private_terms, *roots]))


def screen(db, text, cfg) -> list[dict]:
    from . import safety
    return safety.privacy_screen(text, private_terms=private_terms(db, cfg))


# ---- task API ----------------------------------------------------------------------------------------------------

def _budget(cfg, mode, requested):
    if requested is None:
        return cfg.budget_for(mode)
    if isinstance(requested, Budget):
        requested = requested.to_json()
    if not isinstance(requested, dict):
        raise ValidationError("budget must be an object")
    known = set(Budget().to_json())
    unknown = sorted(set(requested) - known)
    if unknown:
        raise ValidationError(f"unknown budget field(s): {', '.join(unknown)}")
    for key, value in requested.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or value != value:
            raise ValidationError(f"budget.{key} must be a non-negative number")
    return cfg.budget_for(mode, requested)


MAX_CRAWL_SEEDS = 10
MAX_CRAWL_DEPTH = 3


def _crawl_spec(spec):
    """{"seeds": [http(s) URL, ...] (1..10), "max_pages": int >= 1, "max_depth": 0..3} (`nsf web crawl`)."""
    from urllib.parse import urlsplit
    if not isinstance(spec, dict) or set(spec) - {"seeds", "max_pages", "max_depth"}:
        raise ValidationError("context.crawl is {seeds, max_pages, max_depth}")
    seeds = spec.get("seeds")
    if not isinstance(seeds, list) or not 1 <= len(seeds) <= MAX_CRAWL_SEEDS:
        raise ValidationError(f"context.crawl.seeds must list 1 to {MAX_CRAWL_SEEDS} URLs")
    for url in seeds:
        parts = urlsplit(url) if isinstance(url, str) and len(url) <= 2048 else None
        if parts is None or parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValidationError(f"crawl seeds must be http(s) URLs: {str(url)[:200]}")
    pages, depth = spec.get("max_pages", 10), spec.get("max_depth", 1)
    if isinstance(pages, bool) or not isinstance(pages, int) or pages < 1:
        raise ValidationError("context.crawl.max_pages must be a positive integer")
    if isinstance(depth, bool) or not isinstance(depth, int) or not 0 <= depth <= MAX_CRAWL_DEPTH:
        raise ValidationError(f"context.crawl.max_depth must be an integer from 0 to {MAX_CRAWL_DEPTH}")
    return {"seeds": list(dict.fromkeys(seeds)), "max_pages": pages, "max_depth": depth}


def _context(context):
    """Validate the structured task context: {"packages": [{ecosystem, name, version|None}, ...]} (<= 200) and/or
    {"crawl": {seeds, max_pages, max_depth}} (a bounded same-site crawl instead of searching)."""
    if context is None:
        return None
    if not isinstance(context, dict) or set(context) - {"packages", "crawl"}:
        raise ValidationError("context accepts only 'packages' and 'crawl'")
    crawl = _crawl_spec(context["crawl"]) if "crawl" in context else None
    packages = context.get("packages") or []
    if not isinstance(packages, list) or len(packages) > MAX_CONTEXT_PACKAGES:
        raise ValidationError(f"context.packages must be a list of at most {MAX_CONTEXT_PACKAGES} items")
    out = []
    for pkg in packages:
        if not isinstance(pkg, dict) or set(pkg) - {"ecosystem", "name", "version"}:
            raise ValidationError("each package is {ecosystem, name, version}")
        eco, name, version = pkg.get("ecosystem"), pkg.get("name"), pkg.get("version")
        if not (isinstance(eco, str) and 0 < len(eco) <= 32 and isinstance(name, str) and 0 < len(name) <= 214
                and (version is None or isinstance(version, str) and 0 < len(version) <= 64)):
            raise ValidationError("package ecosystem (<= 32), name (<= 214) and version (<= 64 or null) are strings")
        out.append({"ecosystem": eco, "name": name, "version": version})
    validated = {"packages": out} if out or crawl is None else {}
    if crawl is not None:
        validated["crawl"] = crawl
    return validated


def submit(db, objective, now, *, mode=None, budget=None, origin="cli", family=None, authorization=None,
           parent=None, proposed=False, context=None) -> dict:
    """Queue a research task. The objective passes the privacy screen (private terms, evidence roots, e-mail
    addresses, phone numbers, ...) unless authorization == 'owner-approved-disclosure'. `proposed=True` creates a
    proposal the worker skips until the owner authorises it (pending scope web:research:<id>). `context` carries
    structured inputs the objective text cannot (`{"packages": [...]}` for registry and advisory lookups); it is
    kept in the checkpoint and merged into the assessment at discovery. WritesPaused while instance regulation
    pauses writes."""
    prepared = prepare(db, objective, now, mode=mode, budget=budget, origin=origin, family=family,
                       authorization=authorization, parent=parent, proposed=proposed, context=context)
    with store.write(db):
        task_id = insert(db, prepared, now)
        store.bump_state_version(db)
    return get(db, task_id)


def prepare(db, objective, now, *, mode=None, budget=None, origin="cli", family=None, authorization=None,
            parent=None, proposed=False, context=None) -> dict:
    """Validate a submission (submit's checks, no write). Returns what insert() records."""
    cfg = state.require_enabled(db)
    state.require_writable(db)
    context = _context(context)
    if not isinstance(objective, str) or not objective.strip():
        raise ValidationError("objective must be a non-blank string")
    objective = " ".join(objective.split())
    if len(objective) > MAX_OBJECTIVE:
        raise ValidationError(f"objective must be at most {MAX_OBJECTIVE} characters")
    if authorization not in (None, AUTHORIZED_DISCLOSURE):
        raise ValidationError(f"authorization must be omitted or '{AUTHORIZED_DISCLOSURE}'")
    findings = screen(db, objective, cfg)
    if findings and authorization != AUTHORIZED_DISCLOSURE:
        kinds = sorted({f["kind"] for f in findings})
        raise ValidationError(f"objective contains private data ({', '.join(kinds)}); it was not sent anywhere. "
                              f"Remove it, or pass authorization='{AUTHORIZED_DISCLOSURE}' to disclose it")
    if not isinstance(origin, str) or not 0 < len(origin) <= 64:
        raise ValidationError("origin must be a string of 1 to 64 characters")
    if authorization is not None and not _may_authorize(db, origin):
        raise ValidationError(f"authorization '{AUTHORIZED_DISCLOSURE}' is the owner's: it cannot be given to "
                              f"autonomous research or to a monitor the owner did not authorise (origin {origin})")
    if family is not None and (not isinstance(family, str) or not 0 < len(family) <= 64):
        raise ValidationError("family must be a digest string")
    if mode is None:
        from . import intent, knowledge
        known = knowledge.query(db, objective, now)["items"]
        mode = intent.assess(objective, now=now, known=known)["mode"]
    if mode not in MODES:
        raise ValidationError(f"mode must be one of {', '.join(MODES)}")
    chosen = _budget(cfg, mode, budget)
    clamped = None
    if is_autonomous(origin) and not proposed:
        chosen, clamped = _clamp_autonomous(db, cfg, chosen, now)
    elif origin == MCP_ORIGIN:
        chosen, clamped = _clamp_autonomous(db, cfg, chosen, now, mcp=True)
    if parent is not None and db.execute("SELECT 1 FROM web_tasks WHERE id=?", (str(parent),)).fetchone() is None:
        parent = None   # the parent was forgotten; the link is not kept
    initial = {}
    if context:
        initial["context"] = context
    if authorization == AUTHORIZED_DISCLOSURE:
        initial["authorization"] = AUTHORIZED_DISCLOSURE   # applies to this task's own queries (_Run.screened)
    return {"objective": objective, "mode": mode, "proposed": bool(proposed), "origin": origin, "family": family,
            "parent": None if parent is None else str(parent), "budget": chosen, "initial": initial,
            "clamped": clamped, "disclosure_authorized": bool(findings)}


def insert(db, prepared, now) -> str:
    """Record a prepared submission and its 'submitted' event. Caller owns the transaction (and the state bump).
    Returns the task id."""
    task_id = str(uuid.uuid4())
    db.execute("INSERT INTO web_tasks(id,objective,mode,status,stage,origin,family,parent_id,budget,usage,"
               "checkpoint,created_at,updated_at) VALUES(?,?,?,'queued',?,?,?,?,?,?,?,?,?)",
               (task_id, prepared["objective"], prepared["mode"], "proposed" if prepared["proposed"] else None,
                prepared["origin"], prepared["family"], prepared["parent"],
                state.dumps(prepared["budget"].to_json()), state.dumps(Usage().to_json()),
                state.dumps(prepared["initial"]) if prepared["initial"] else None, now, now))
    detail = {"mode": prepared["mode"], "origin": prepared["origin"], "budget": prepared["budget"].to_json(),
              "proposed": prepared["proposed"], "disclosure_authorized": prepared["disclosure_authorized"]}
    if prepared["clamped"]:
        detail["clamped_to_daily_allowance"] = prepared["clamped"]
    state.log_event(db, task_id, "submitted", detail, now)
    return task_id


def is_autonomous(origin) -> bool:
    """Timer-driven research the owner did not ask for: curiosity and prediction refreshes."""
    return origin == "curiosity" or str(origin).startswith("refresh:")


def _may_authorize(db, origin) -> bool:
    """Owner-approved disclosure is never given to autonomous research, and to a monitor's task only when the
    owner authorised that monitor."""
    if is_autonomous(origin):
        return False
    if origin.startswith("monitor:"):
        try:
            mid = int(origin.split(":", 1)[1])
        except ValueError:
            return False
        row = db.execute("SELECT authorized_by FROM web_monitors WHERE id=?", (mid,)).fetchone()
        return row is not None and row[0] == "owner"
    return True


def _clamp_autonomous(db, cfg, chosen, now, *, mcp=False):
    """Clamp an autonomous (mcp=True: an MCP-origin) task's request and byte budgets to what is left of its daily
    allowance (config.daily_autonomous / config.daily_mcp), so the daily cap can be passed by at most the one
    request in flight. Nothing left, or the daily task count reached -> ValidationError (not submitted)."""
    from . import scheduler
    caps, what = (cfg.daily_mcp, "MCP") if mcp else (cfg.daily_autonomous, "autonomous")
    left = scheduler.autonomous_remaining(db, now, cfg, mcp=mcp)
    if mcp and scheduler.autonomous_usage(db, now, mcp=True)["tasks"] >= caps.get("tasks", 0):
        raise ValidationError(f"daily {what} cap reached: tasks {caps.get('tasks', 0)}")
    for key in ("requests", "bytes"):
        if left[key] < 1:
            raise ValidationError(f"daily {what} cap reached: {key} "
                                  f"{caps.get(key, 0) - left[key]} of {caps.get(key, 0)} committed")
    clamped = {}
    if chosen.max_requests > left["requests"]:
        clamped["max_requests"] = left["requests"]
    if chosen.max_bytes > left["bytes"]:
        clamped["max_bytes"] = left["bytes"]
    return (replace(chosen, **clamped) if clamped else chosen), clamped


def _public(task) -> dict:
    out = {k: task[k] for k in _PUBLIC}
    checkpoint = task.get("checkpoint") or {}
    out["stages_done"] = list(checkpoint.get("stages_done", []))
    out["proposed"] = task["stage"] == "proposed"
    return out


def get(db, task_id, *, family=None) -> dict:
    task = state.task(db, task_id, family=family)
    out = _public(task)
    out["progress"] = [{"kind": e["kind"], "stage": e["stage"], "at": e["at"]} for e in state.events(db, task_id)
                       if e["kind"] in ("submitted", "started", "stage", "resumed", "cancel_requested", "completed",
                                        "partial", "failed", "cancelled")]
    return out


def listing(db, *, family=None, status=None, limit=50) -> list[dict]:
    where, params = [], []
    if family is not None:
        where.append("family=?")
        params.append(family)
    if status is not None:
        where.append("status=?")
        params.append(status)
    limit = max(1, min(500, int(limit)))
    sql = f"SELECT id FROM web_tasks{' WHERE ' + ' AND '.join(where) if where else ''} ORDER BY created_at DESC, " \
          "rowid DESC LIMIT ?"
    out = []
    for (tid,) in db.execute(sql, (*params, limit)).fetchall():
        item = _public(state.task(db, tid))
        item.pop("result")
        out.append(item)
    return out


def evidence(db, task_id, *, family=None) -> dict:
    """Show your evidence: sources, claims (untrusted text), links and the episode log of a task."""
    task = state.task(db, task_id, family=family)
    return {"task": {k: task[k] for k in ("id", "objective", "mode", "status")}, "sources": state.sources(db, task_id),
            "claims": [c | {"untrusted": True} for c in state.claims(db, task_id)], "links": state.links(db, task_id),
            "events": state.events(db, task_id)}


def cancel(db, task_id, now, *, family=None) -> dict:
    """Queued -> cancelled at once; running -> cancel_requested (the run stops before its next network step)."""
    with store.write(db):
        task = state.task(db, task_id, family=family)
        if task["status"] in TERMINAL_STATES:
            return _public(task)
        running = task["status"] != "queued" and task["lease_holder"] is not None and \
            (task["lease_expires_at"] or 0) > now
        if running:
            state.update_task(db, task_id, now, cancel_requested=1)
            state.log_event(db, task_id, "cancel_requested", {}, now)
        else:
            state.update_task(db, task_id, now, status="cancelled", cancel_requested=1, finished_at=now,
                              lease_holder=None, lease_expires_at=None,
                              stage=None if task["stage"] == "proposed" else task["stage"])
            state.log_event(db, task_id, "cancelled", {"reason": "cancelled before it ran"}, now)
        store.bump_state_version(db)
    return get(db, task_id, family=family)


# ---- execution ---------------------------------------------------------------------------------------------------

class _Run:
    def __init__(self, db, task, services, resumed):
        self.db, self.services, self.resumed = db, services, resumed
        self.id, self.objective, self.mode = task["id"], task["objective"], task["mode"]
        self.origin = task.get("origin") or ""
        self.budget = Budget.from_json(task["budget"])
        self.checkpoint = task["checkpoint"] or {}
        self.authorized = self.checkpoint.get("authorization") == AUTHORIZED_DISCLOSURE
        self.cfg = state.get_config(db)
        self.usage = Usage.from_json(self.checkpoint.get("usage") or task["usage"])
        self.prior_seconds = float(self.usage.seconds or 0.0)
        self.started = self.clock()
        self.failures = list(self.checkpoint.get("failures", []))
        self.limitations = list(self.checkpoint.get("limitations", []))
        self.documents, self.clusters = [], []
        self.exhausted = None
        self.stage_failed = []
        self.holder = services.holder

    def clock(self):
        return float(self.services.clock())

    def elapsed(self):
        return self.prior_seconds + max(0.0, self.clock() - self.started)

    def event(self, kind, detail, stage=None):
        now = self.clock()
        with store.write(self.db):
            state.log_event(self.db, self.id, kind, detail, now, stage=stage)

    def save(self, **updates):
        """Merge into the checkpoint and persist it with usage and a renewed lease (one short transaction)."""
        self.checkpoint.update(updates)
        self.usage.seconds = round(self.elapsed(), 3)
        self.checkpoint["usage"] = self.usage.to_json()
        self.checkpoint["failures"] = self.failures
        self.checkpoint["limitations"] = self.limitations
        now = self.clock()
        with store.write(self.db):
            state.update_task(self.db, self.id, now, holder=self.holder, checkpoint=self.checkpoint,
                              usage=self.usage.to_json(), lease_expires_at=now + LEASE_SECONDS)

    def renew(self):
        """Renew the worker lease (one short transaction): called after every fetch and search round, so a long
        stage cannot outlive LEASE_SECONDS while the run is still making progress. Fenced: state.LostLease when
        another worker took the task over."""
        now = self.clock()
        with store.write(self.db):
            state.update_task(self.db, self.id, now, holder=self.holder, lease_expires_at=now + LEASE_SECONDS)

    def stage(self, name):
        now = self.clock()
        with store.write(self.db):
            state.update_task(self.db, self.id, now, holder=self.holder, status=name, stage=name,
                              lease_expires_at=now + LEASE_SECONDS)
            state.log_event(self.db, self.id, "stage", {"stage": name}, now, stage=name)

    def done(self, name, **updates):
        stages = list(self.checkpoint.get("stages_done", []))
        if name not in stages:
            stages.append(name)
        self.save(stages_done=stages, **updates)

    def is_done(self, name):
        return name in self.checkpoint.get("stages_done", [])

    def check(self):
        """Before every network step: honour cancel (any connection may set it), the lease (another worker took
        the task over -> state.LostLease), instance regulation (writes paused -> the run stops, 'partial') and the
        time budget."""
        row = self.db.execute("SELECT cancel_requested, lease_holder FROM web_tasks WHERE id=?",
                              (self.id,)).fetchone()
        if row is None or row[0]:
            raise _Cancelled()
        if row[1] != self.holder:
            raise state.LostLease(f"research task {self.id}: the lease is held by another worker")
        allowed, reason = state.write_allowed(self.db, self.services_db_path())
        if not allowed:
            raise _Paused(reason)
        if self.elapsed() > self.budget.max_seconds:
            raise _OutOfTime()

    def services_db_path(self):
        return getattr(self.services, "db_path", None)

    def limit(self, why):
        self.exhausted = self.exhausted or why
        if why not in self.limitations:
            self.limitations.append(why)

    # ---- discovering --------------------------------------------------------------------------------------------
    def discover(self):
        from . import intent, knowledge
        now = self.clock()
        known = knowledge.query(self.db, self.objective, now)["items"]
        assessment = intent.assess(self.objective, now=now, known=known)
        assessment["assessed_mode"], assessment["mode"] = assessment.get("mode"), self.mode
        packages = (self.checkpoint.get("context") or {}).get("packages")
        if packages:
            named = {(p["ecosystem"].lower(), p["name"].lower()) for p in assessment.get("packages") or []}
            assessment["packages"] = list(assessment.get("packages") or []) + [
                p for p in packages if (p["ecosystem"].lower(), p["name"].lower()) not in named]
            assessment["notes"] = [n for n in assessment.get("notes") or [] if not n.startswith("no packages listed")]
        crawl = (self.checkpoint.get("context") or {}).get("crawl")
        if crawl:   # "investigate these sources": the seeds are read by a bounded crawl, nothing is searched
            assessment["needs_web"] = True
            assessment["urls"] = list(crawl["seeds"])
            self.save(assessment=assessment, known=[k["id"] for k in known])
            self.event("decision", {"needs_web": True, "crawl": {"seeds": len(crawl["seeds"]),
                                                                 "max_pages": crawl["max_pages"],
                                                                 "max_depth": crawl["max_depth"]}}, "discovering")
            self.done("discovering", plan=QueryPlan(queries=()).to_json(), results=[], attempts=[])
            return True
        self.save(assessment=assessment, known=[k["id"] for k in known])
        if not assessment.get("needs_web", True):
            self.event("decision", {"needs_web": False, "reasons": assessment.get("reasons", []),
                                    "knowledge": [k["id"] for k in known]}, "discovering")
            return False
        self.event("decision", {"needs_web": True, "reasons": assessment.get("reasons", [])}, "discovering")
        local = self.local_terms()
        plan = self.screened(intent.plan(self.objective, assessment, now=now, local_terms=local))
        results, attempts, failures = [], [], []
        for round_no in range(2):
            for q in plan.queries:
                self.event("query", {"text": q.text, "kind": q.kind, "purpose": q.purpose, "round": round_no},
                           "discovering")
            try:
                self.check()
            except _OutOfTime:
                self.limit("time budget exhausted before searching")
                break
            outcome = self.services.registry.search(plan, budget=self.budget, usage=self.usage)
            self.renew()
            seen = {r["url"] for r in results}
            results += [r.to_json() for r in outcome.results if r.url not in seen]
            attempts += outcome.attempts
            failures += outcome.failures
            self.event("search", {"results": len(outcome.results), "attempts": len(outcome.attempts),
                                  "failures": [f.get("error") for f in outcome.failures][:10], "round": round_no},
                       "discovering")
            if round_no:
                break
            refined = intent.refine(plan, outcome, assessment, now=self.clock(), local_terms=local)
            if refined is None:
                break
            plan = self.screened(refined)
            if not plan.queries:
                break
        self.failures += [{"stage": "discovering", **f} for f in failures]
        if any(str(f.get("error", "")).startswith("budget_exhausted") for f in failures):
            self.limit("query budget exhausted: some planned queries were not sent")
        self.done("discovering", plan=plan.to_json(), results=results, attempts=attempts[:200])
        return True

    def local_terms(self) -> list[str]:
        """Names of the individual's own system that are local context, never search terms (intent.strip_local
        adds the built-in self-references). Owner-listed private terms count, unless the owner authorised
        disclosing them for this task."""
        return [] if self.authorized else list(self.cfg.private_terms)

    def screened(self, plan: QueryPlan) -> QueryPlan:
        """Drop planned queries the privacy screen flags. Under owner-approved disclosure a query is kept when every
        finding in it is private text the objective itself contained (the authorisation covers exactly that), and
        the disclosure is recorded as a decision event (counts and kinds only)."""
        cfg = state.get_config(self.db)
        allowed = set()
        if self.authorized:
            allowed = {_found_text(self.objective, f) for f in screen(self.db, self.objective, cfg)}
        kept, dropped, disclosed = [], [], []
        for q in plan.queries:
            found = screen(self.db, q.text, cfg)
            if found and allowed and all(_found_text(q.text, f) in allowed for f in found):
                disclosed.append((q, found))
                kept.append((q, found))
            else:
                (dropped if found else kept).append((q, found))
        if disclosed:
            self.event("decision", {"disclosed_under_authorization": len(disclosed),
                                    "authorization": AUTHORIZED_DISCLOSURE,
                                    "findings": sorted({f["kind"] for _, fs in disclosed for f in fs})},
                       "discovering")
        if dropped:
            self.event("decision", {"dropped_queries": len(dropped),
                                    "findings": sorted({f["kind"] for _, fs in dropped for f in fs})}, "discovering")
        return replace(plan, queries=tuple(q for q, _ in kept))

    # ---- reading ------------------------------------------------------------------------------------------------
    def select(self, assessment):
        """Ranked reading candidates (at most CANDIDATE_FACTOR x max_pages): URLs named in the question, then
        structured API records (and, for a monitor's task, feed items: cheap and change-friendly), then web
        results, deduplicated by canonical URL. read() works down this list until max_pages documents succeed."""
        from .extract import canonical_url
        results = [SearchResult(**r) for r in self.checkpoint.get("results", [])]
        named = [SearchResult(url=u, title=u, snippet="", provider="question", kind="web")
                 for u in assessment.get("urls") or []]
        monitor = self.origin.startswith("monitor:")
        order = []
        for i, r in enumerate(named + results):
            api = r.provider in API_PROVIDERS and bool(r.metadata)
            early = api or (monitor and r.provider == "feeds")
            order.append((0 if i < len(named) else (1 if early else 2), i, r, api))
        picked, seen = [], set()
        for _, _, r, api in sorted(order, key=lambda x: (x[0], x[1])):
            try:
                key = canonical_url(r.url)
            except Exception:  # an unparsable URL from a provider is skipped, recorded below
                self.failures.append({"stage": "reading", "code": "blocked_policy", "url": r.url,
                                      "detail": "unparsable URL"})
                continue
            if key not in seen:
                seen.add(key)
                picked.append((r, api))
        return picked[:max(0, CANDIDATE_FACTOR * self.budget.max_pages)]

    def web_reserve(self, picked) -> int:
        """Page slots kept for web pages: ceil(max_pages / 3) in scientific and deep_research modes when the
        candidates include web pages, else 0. API records count as pages; they may take a reserved slot only
        after every web candidate was tried (read's second pass)."""
        if self.mode not in WEB_RESERVE_MODES or not any(not api for _, api in picked):
            return 0
        return math.ceil(max(0, self.budget.max_pages) / 3)

    def api_document(self, r: SearchResult, now) -> Document:
        from .extract import canonical_url, registrable_domain
        from urllib.parse import urlsplit
        meta = dict(r.metadata or {})
        abstract = str(meta.get("abstract") or meta.get("summary") or "")
        text = "\n".join(x for x in (r.title, r.snippet, abstract) if x)
        return Document(url=r.url, final_url=r.url, canonical_url=canonical_url(r.url),
                        domain=registrable_domain(urlsplit(r.url).hostname or ""), title=r.title, text=text,
                        retrieved_at=now, retrieval_mode="api", kind="api", content_type="application/json",
                        published_at=r.published_at, metadata=meta | {"provider": r.provider})

    def record(self, doc, *, provider, api):
        now = self.clock()
        with store.write(self.db):
            sid = state.save_source(self.db, self.id, doc, now, provider=provider)
            state.log_event(self.db, self.id, "fetch", {"url": doc.url, "source_id": sid, "kind": doc.kind,
                                                        "retrieval_mode": doc.retrieval_mode,
                                                        "flags": {k: v for k, v in doc.flags.items()
                                                                  if k in ("js_suspected", "hidden_removed",
                                                                           "truncated")},
                                                        "injection": len(doc.flags.get("injection") or [])},
                            now, stage="reading")
        entry = {"source_id": sid, "url": doc.url, "final_url": doc.final_url, "canonical_url": doc.canonical_url,
                 "sha256": doc.content_sha256, "kind": doc.kind, "content_type": doc.content_type,
                 "retrieved_at": doc.retrieved_at, "provider": provider, "api": api,
                 "doc": doc.to_json() if api else None}
        docs = [d for d in self.checkpoint.get("documents", []) if d["canonical_url"] != doc.canonical_url]
        self.save(documents=docs + [entry])
        self.documents.append(doc)
        return sid

    def acquire(self, url, purpose="page"):
        """Fetch -> extract -> quarantine (-> render escalation). Raises FetchError / BudgetExceeded."""
        return self.finish(url, self.fetch(url, purpose))

    def fetch(self, url, purpose="page"):
        """One fetch through SafeFetcher."""
        return self.services.fetcher.fetch(url, purpose=purpose, budget=self.budget, usage=self.usage)

    def finish(self, url, result):
        """Extract -> quarantine (-> render escalation) of a fetched page, on the run's own thread."""
        from . import extract, safety
        doc = safety.quarantine(extract.extract(result))
        renderer = self.services.renderer
        if doc.flags.get("js_suspected") and renderer is not None and \
                self.usage.browser_sessions < self.budget.max_browser_sessions:
            ok, why = renderer.available()
            if ok:
                self.check()
                try:
                    rendered = renderer.render(url, budget=self.budget, usage=self.usage)
                    doc = safety.quarantine(extract.extract(rendered))
                    self.event("render", {"url": url, "ok": True}, "reading")
                except FetchError as exc:
                    self.failures.append({"stage": "reading", **exc.to_json()})
                    self.event("render", {"url": url, "ok": False, "error": exc.to_json()}, "reading")
            else:
                self.event("decision", {"render": False, "url": url, "reason": why}, "reading")
        return doc

    def reread(self, entry):
        """A document read before a crash/resume, re-read from the public cache (retrieval_mode 'cache')."""
        from . import extract, safety
        if entry["api"]:
            doc = safety.quarantine(Document(**entry["doc"]))
            doc.retrieval_mode = "cache"
            return doc
        body = self.services.cache.body(entry["sha256"]) if entry.get("sha256") else None
        if body is None:
            return None
        result = FetchResult(url=entry["url"], final_url=entry["final_url"] or entry["url"], status=200, headers={},
                             content_type=entry["content_type"], body=body, retrieved_at=entry["retrieved_at"],
                             retrieval_mode="cache")
        return safety.quarantine(extract.extract(result))

    def read(self):
        """Work down the ranked candidates until max_pages documents succeed: a failed fetch (blocked, error,
        refused type) costs requests, never a page slot; the request, byte and time budgets bound the attempts.
        In scientific/deep_research modes API records leave web_reserve() slots to web pages; slots no web page
        could fill go to the remaining API records afterwards."""
        from .extract import canonical_url
        assessment = self.checkpoint.get("assessment") or {}
        done = {d["canonical_url"]: d for d in self.checkpoint.get("documents", [])}
        for entry in list(done.values()):
            try:
                doc = self.reread(entry)
            except _STOP:
                raise
            except Exception as exc:   # one bad cached document never costs the task its other evidence
                self.document_failed(entry.get("url"), entry.get("provider"), exc)
                doc = None
            if doc is None:
                done.pop(entry["canonical_url"])
                continue
            self.record(doc, provider=entry.get("provider"), api=entry["api"])
        if self.is_done("reading"):
            return
        crawl = (self.checkpoint.get("context") or {}).get("crawl")
        if crawl:
            self.crawl(crawl, done)
            self.done("reading")
            return
        picked = self.select(assessment)
        self.save(selected=[{"url": r.url, "provider": r.provider, "api": api} for r, api in picked])
        api_cap = max(0, self.budget.max_pages - self.web_reserve(picked))
        deferred = []

        def full():
            return self.usage.pages >= self.budget.max_pages

        def take_api(r):
            from . import safety
            self.check()
            try:
                doc = safety.quarantine(self.api_document(r, self.clock()))
            except _STOP:
                raise
            except Exception as exc:   # a malformed API record is one failed document, not a failed task
                self.document_failed(r.url, r.provider, exc)
                return
            self.usage.charge(self.budget, pages=1)
            self.record(doc, provider=r.provider, api=True)

        try:
            for r, api in picked:
                if canonical_url(r.url) in done:
                    continue
                if full():
                    break
                self.check()
                if api:
                    if sum(1 for d in self.documents if d.kind == "api") >= api_cap:
                        deferred.append(r)
                    else:
                        take_api(r)
                    continue
                try:
                    result = self.fetch(r.url)
                except FetchError as exc:
                    self.failures.append({"stage": "reading", "provider": r.provider, **exc.to_json()})
                    self.event("error", {"stage": "reading", **exc.to_json()}, "reading")
                    self.renew()
                    if exc.code == "budget_exhausted":
                        self.limit(f"budget exhausted while reading: {exc.detail}")
                        break
                    continue
                try:
                    doc = self.finish(r.url, result)
                except _STOP:
                    raise
                except FetchError as exc:   # the render escalation's own fetches
                    self.failures.append({"stage": "reading", "provider": r.provider, **exc.to_json()})
                    self.event("error", {"stage": "reading", **exc.to_json()}, "reading")
                    self.renew()
                    continue
                except Exception as exc:   # one untrusted document never costs the task its other evidence
                    self.document_failed(r.url, r.provider, exc)
                    self.renew()
                    continue
                self.usage.charge(self.budget, pages=1)
                self.record(doc, provider=r.provider, api=False)
            for r in deferred:
                if full() or self.exhausted:
                    break
                take_api(r)
            self.follow()
        except BudgetExceeded as exc:
            self.limit(f"budget exhausted while reading: {exc}")
        except _OutOfTime:
            self.limit("time budget exhausted while reading")
        self.done("reading")

    def document_failed(self, url, provider, exc):
        """Record one document's unexpected failure (decode or internal error) and carry on with the others."""
        code = "decode_error" if isinstance(exc, (UnicodeError, ValueError, LookupError)) else "internal_error"
        failure = {"stage": "reading", "provider": provider, "code": code, "url": url,
                   "detail": f"{type(exc).__name__}: {str(exc)[:200]}"}
        self.failures.append(failure)
        self.event("error", {k: failure[k] for k in ("stage", "code", "url", "detail")}, "reading")

    def crawl(self, spec, done):
        """`nsf web crawl`: a bounded same-site crawl (crawl.crawl: robots, sitemaps, nofollow, per-host throttling,
        the task budget) from the seeds; each page is extracted, quarantined and recorded as a source (provider
        'crawl'). Pages already recorded before a resume are not fetched again."""
        from . import crawl as crawler
        self.check()
        failures = []
        try:
            results = crawler.crawl(self.services.fetcher, spec["seeds"], budget=self.budget, usage=self.usage,
                                    max_pages=spec["max_pages"], max_depth=spec["max_depth"], failures=failures)
        except _OutOfTime:
            results = []
            self.limit("time budget exhausted while crawling")
        for failure in failures:
            self.failures.append({"stage": "reading", "provider": "crawl", **failure})
            if failure.get("code") == "budget_exhausted":
                self.limit(f"budget exhausted while crawling: {failure.get('detail')}")
        if failures:
            self.event("error", {"stage": "reading", "crawl": True, "failures": len(failures),
                                 "codes": sorted({str(f.get("code")) for f in failures})}, "reading")
        self.renew()
        from .extract import canonical_url
        for result in results:
            self.check()
            try:
                if canonical_url(result.final_url) in done:
                    continue
                doc = self.finish(result.url, result)
            except _STOP:
                raise
            except Exception as exc:   # one untrusted page never costs the crawl its other pages
                self.document_failed(result.url, "crawl", exc)
                continue
            self.event("crawl", {"url": result.url}, "reading")
            self.record(doc, provider="crawl", api=False)

    def follow(self):
        """deep_research: follow <= MAX_FOLLOW same-site links of the first page (bounded crawl, depth 1).

        Not crawl.crawl: it re-fetches its seeds (the first page is already read and recorded), returns only after
        the whole walk (no per-document checkpoint, cancel check or lease renewal between fetches) and has no
        render escalation; this loop keeps all four with the same SafeFetcher policy (purpose 'crawl')."""
        if self.mode != "deep_research" or self.budget.max_depth < 1:
            return
        from .extract import canonical_url
        first = next((d for d in self.documents if d.kind not in ("api",) and d.links), None)
        if first is None:
            return
        have = {d.canonical_url for d in self.documents}
        links = []
        for link in first.links:
            try:
                key = canonical_url(link)
            except Exception:  # untrusted link text that is not a URL
                continue
            if key not in have and _same_site(link, first.domain) and link not in links:
                links.append(link)
        for link in links[:MAX_FOLLOW]:
            if self.usage.pages + 1 > self.budget.max_pages:
                break
            self.check()
            try:
                doc = self.finish(link, self.fetch(link, purpose="crawl"))
            except FetchError as exc:
                self.failures.append({"stage": "reading", "provider": "crawl", **exc.to_json()})
                self.event("error", {"stage": "reading", "crawl": True, **exc.to_json()}, "reading")
                self.renew()
                continue
            except _STOP:
                raise
            except Exception as exc:   # one untrusted page never costs the task its other evidence
                self.document_failed(link, "crawl", exc)
                self.renew()
                continue
            self.usage.charge(self.budget, pages=1)
            self.event("crawl", {"from": first.url, "url": link}, "reading")
            self.record(doc, provider="crawl", api=False)

    # ---- evaluating ---------------------------------------------------------------------------------------------
    def evaluate(self):
        from . import claims
        assessment = self.checkpoint.get("assessment") or {}
        freshness = assessment.get("freshness_class") or "stable"
        found, per_doc, failed = [], [], []
        for doc in self.documents:
            try:
                extracted = claims.extract_claims(doc, self.objective)
            except Exception as exc:   # one untrusted document never costs the task its other claims
                code = "decode_error" if isinstance(exc, (UnicodeError, ValueError, LookupError)) else \
                    "internal_error"
                failure = {"stage": "evaluating", "code": code, "url": doc.canonical_url or doc.url,
                           "detail": f"{type(exc).__name__}: {str(exc)[:200]}"}
                self.failures.append(failure)
                failed.append(failure)
                continue
            per_doc.append((doc, len(extracted)))
            found += extracted
        now = self.clock()
        scored = [claims.score(c, now=now, freshness_class=freshness, ttl_overrides=self.ttl_overrides())
                  for c in claims.cluster(found)]
        sources = {}
        for entry in self.checkpoint.get("documents", []):
            for key in ("canonical_url", "url", "final_url"):
                if entry.get(key):
                    sources[entry[key]] = entry["source_id"]
        with store.write(self.db):
            for failure in failed:
                state.log_event(self.db, self.id, "error", failure, now, stage="evaluating")
            for doc, n in per_doc:
                state.log_event(self.db, self.id, "extract", {"url": doc.canonical_url or doc.url, "kind": doc.kind,
                                                              "claims": n}, now, stage="evaluating")
            saved = state.save_clusters(self.db, self.id, scored, now, source_ids=sources)
            state.log_event(self.db, self.id, "claim",
                            {"claims": len(found), "clusters": len(scored),
                             "status": {s: sum(1 for c in scored if c.status == s) for s in
                                        sorted({c.status for c in scored})}}, now, stage="evaluating")
        self.clusters = scored
        self.done("evaluating", clusters=saved)

    # ---- synthesizing -------------------------------------------------------------------------------------------
    def synthesize(self):
        from . import synthesis
        assessment = dict(self.checkpoint.get("assessment") or {})
        self.usage.seconds = round(self.elapsed(), 3)
        report = synthesis.synthesize(self.objective, assessment, self.documents, self.clusters, now=self.clock(),
                                      usage=self.usage, failures=self.failures,
                                      ttl_overrides=self.ttl_overrides())
        report = dict(report)
        report.setdefault("limitations", [])
        for why in self.limitations:
            if why not in report["limitations"]:
                report["limitations"].append(why)
        pack = synthesis.context_pack(report, max_tokens=self.budget.max_context_tokens)
        report["context_pack"] = pack
        self.usage.tokens = int(pack.get("tokens") or 0)   # the working-memory pack this report hands on
        report["budget"] = self.budget.to_json()
        report["usage"] = self.usage.to_json()
        self.event("synthesized", {"answer": len(report.get("answer") or []),
                                   "findings": len(report.get("findings") or []),
                                   "sources": len(report.get("sources") or []), "live": bool(report.get("live"))},
                   "synthesizing")
        return report

    def ttl_overrides(self):
        """The owner's freshness TTL overrides (config.freshness_ttl) for claims.score / synthesis.synthesize."""
        return dict(self.cfg.freshness_ttl) or None


def _found_text(text, finding) -> str:
    return text[finding["offset"]:finding["offset"] + finding["length"]].casefold()


def _same_site(url, domain):
    from urllib.parse import urlsplit
    from .extract import registrable_domain
    try:
        return registrable_domain(urlsplit(url).hostname or "") == domain
    except Exception:  # malformed host in an untrusted link
        return False


def _memory_documents(items):
    """Knowledge items as cached documents and clusters, for a report built without the web."""
    documents, clusters = [], []
    for item in items:
        sources = item["sources"] or [{"url": f"memory:knowledge/{item['id']}", "title": "web knowledge",
                                       "domain": "memory"}]
        members = []
        for n, s in enumerate(sources):
            documents.append(Document(url=s["url"], final_url=s["url"], canonical_url=s["url"],
                                      domain=s.get("domain") or "", title=s.get("title") or "",
                                      text=item["statement"], retrieved_at=item["last_verified_at"],
                                      retrieval_mode="cache", kind="text", published_at=s.get("published_at")))
            members.append(Claim(id=f"knowledge-{item['id']}-{n}", text=item["statement"], source_url=s["url"],
                                 domain=s.get("domain") or "", title=s.get("title") or "",
                                 retrieved_at=item["last_verified_at"], key=item["key"], values=item["values"],
                                 polarity=item["polarity"], published_at=s.get("published_at"), relevance=1.0))
        clusters.append(ClaimCluster(key=item["key"], claims=members, independent_sources=len(sources),
                                     supporting=[c.id for c in members], contradicting=[],
                                     confidence=item["confidence"], uncertainty=item["uncertainty"],
                                     status="corroborated" if len(sources) > 1 else "single_source",
                                     statement=item["statement"]))
    return documents, clusters


def _claim(db, task_id, services, *, resume):
    """Take the task's lease. Returns the task dict, or None when it is already finished."""
    now = float(services.clock())
    with store.write(db):
        task = state.task(db, task_id)
        if task["status"] in TERMINAL_STATES and not (resume and task["status"] == "failed"):
            return None
        if task["stage"] == "proposed":
            raise Conflict(f"Research task {task_id} is a proposal awaiting the owner's authorisation")
        holder = task["lease_holder"]
        if holder and holder != services.holder and (task["lease_expires_at"] or 0) > now:
            raise LeaseHeld(f"Research task {task_id} is being run by another worker")
        changes = {"lease_holder": services.holder, "lease_expires_at": now + LEASE_SECONDS,
                   "executed_on": executed_on(), "started_at": task["started_at"] or now}
        if task["status"] == "failed":
            changes.update(status="queued", finished_at=None, error=None)
        state.update_task(db, task_id, now, **changes)
        state.log_event(db, task_id, "resumed" if resume else "started",
                        {"holder": services.holder.split(":")[0], "stages_done":
                         (task["checkpoint"] or {}).get("stages_done", [])}, now)
    return state.task(db, task_id)


def _execute(db, task_id, services, *, resume):
    state.require_enabled(db)
    task = _claim(db, task_id, services, resume=resume)
    if task is None:
        return get(db, task_id)
    run = _Run(db, task, services, resume)
    report, status, error, consolidation = None, None, None, None
    try:
        needs_web = True
        if not run.is_done("discovering"):
            run.stage("discovering")
            needs_web = run.discover()
        elif not (run.checkpoint.get("assessment") or {}).get("needs_web", True):
            needs_web = False
        if not needs_web:
            from . import knowledge
            assessment = run.checkpoint.get("assessment") or {}
            chosen = set(assessment.get("memory_items") or run.checkpoint.get("known", []))
            items = [i for i in knowledge.query(db, task["objective"], run.clock())["items"]
                     if i["id"] in chosen and not i["stale"] and not i["contested"]]
            run.documents, run.clusters = _memory_documents(items)
            run.stage("synthesizing")
            run.event("extract", {"from_memory": True, "knowledge_ids": [i["id"] for i in items],
                                  "claims": sum(len(c.claims) for c in run.clusters)}, "synthesizing")
            report = run.synthesize()
            report["memory"] = {"answered_from_memory": True, "knowledge_ids": [i["id"] for i in items],
                                "provisional_ids": [i["id"] for i in items if i["status"] == "provisional"],
                                "reasons": (run.checkpoint.get("assessment") or {}).get("reasons", [])}
            report["live"] = False
            _mark_knowledge_status(report, items)
            status = "completed"
        else:
            run.stage("reading")
            run.read()
            run.stage("evaluating")
            if run.is_done("evaluating"):
                run.clusters = state.load_clusters(db, task_id)
            else:
                try:
                    run.evaluate()
                except _STOP:
                    raise
                except Exception as exc:   # a failed stage still allows a partial report
                    run.stage_failed.append("evaluating")
                    run.limit(f"evaluating failed: {type(exc).__name__}")
                    run.event("error", {"stage": "evaluating", "error": type(exc).__name__, "detail": str(exc)[:300]},
                              "evaluating")
            run.stage("synthesizing")
            report = run.synthesize()
            run.done("synthesizing")
            status = "partial" if (run.exhausted or run.stage_failed) else "completed"
    except state.LostLease:
        return _lost_lease(db, task_id, run)
    except _Cancelled:
        status = "cancelled"
        try:
            report = run.synthesize()
            report.setdefault("limitations", []).append("cancelled by request: partial report")
        except Exception as exc:  # a partial report is best effort; the cancellation itself must stand
            error = f"no partial report: {type(exc).__name__}"
    except _Paused as exc:
        status = "partial"
        run.limit(f"writes paused by instance regulation: {exc}")
        run.event("decision", {"stopped": "writes paused", "reason": str(exc)[:300]})
        try:
            report = run.synthesize()
        except Exception as inner:  # best effort; the pause itself must stand
            report, error = None, f"writes paused: {str(exc)[:200]}; no partial report: {type(inner).__name__}"
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:500]}"
        run.event("error", {"stage": run.checkpoint.get("stages_done", [])[-1:] or ["discovering"],
                            "error": type(exc).__name__, "detail": str(exc)[:300]})
        try:
            report = run.synthesize() if run.documents else None
        except Exception:  # the original failure is what gets reported
            report = None
        status = "partial" if report else "failed"
    now = run.clock()
    paused = not state.write_allowed(db, run.services_db_path())[0]
    if paused and report is not None and status in ("completed", "partial"):
        status = "partial"
        if not any(str(why).startswith("writes paused") for why in report.get("limitations", [])):
            report.setdefault("limitations", []).append("writes paused by instance regulation: nothing was "
                                                        "consolidated into knowledge")
    if report is not None and status in ("completed", "partial") and needs_web_report(report) and not paused:
        try:
            from . import consolidate
            consolidation = consolidate.run(db, task_id, now, holder=run.holder)
            report["consolidation"] = {k: consolidation[k] for k in ("provisional", "strengthened", "unchanged",
                                                                      "superseded", "contested", "held", "pending",
                                                                      "questions_deferred", "review_question")}
            _predict(db, consolidation["provisional"], now)
        except state.LostLease:
            return _lost_lease(db, task_id, run)
        except Exception as exc:
            report.setdefault("limitations", []).append(f"consolidation failed: {type(exc).__name__}")
            run.event("error", {"stage": "consolidating", "error": type(exc).__name__, "detail": str(exc)[:300]})
            status = "partial"
    run.usage.seconds = round(run.elapsed(), 3)
    try:
        with store.write(db):
            state.update_task(db, task_id, now, holder=run.holder, status=status, stage=None, result=report,
                              error=error, usage=run.usage.to_json(), finished_at=now, lease_holder=None,
                              lease_expires_at=None, checkpoint=run.checkpoint | {"usage": run.usage.to_json()})
            state.log_event(db, task_id, status, {"live": (report or {}).get("live"),
                                                  "sources": len((report or {}).get("sources") or []),
                                                  "limitations": len((report or {}).get("limitations") or []),
                                                  "error": error}, now)
            state.record_mode_outcome(db, run.mode, status, run.usage.to_json(), now)
            store.bump_state_version(db)
    except state.LostLease:
        return _lost_lease(db, task_id, run)
    services.persist(db, now)
    _prune_cache(db, task_id, services, now)
    _after_run(db, task_id, now)
    return get(db, task_id)


def _lost_lease(db, task_id, run):
    """Another worker took the task over (this worker's lease expired): stop without writing an outcome or
    consolidating; the new holder finishes the task. One decision event records it."""
    now = run.clock()
    with store.write(db):
        state.log_event(db, task_id, "decision", {"stopped": "lease lost to another worker",
                                                  "holder": run.holder.split(":")[0]}, now)
    return get(db, task_id) | {"lease_lost": True}


def _after_run(db, task_id, now):
    """Change detection for a monitor's task, however it ran (inline tick, worker, resume): the monitor's
    comparison with its baseline (scheduler.after_monitor_task). A failure is recorded; the outcome stands."""
    try:
        origin = db.execute("SELECT origin FROM web_tasks WHERE id=?", (task_id,)).fetchone()
        if origin is None or not str(origin[0]).startswith("monitor:"):
            return
        from . import scheduler
        scheduler.after_monitor_task(db, task_id, now)
    except Exception as exc:   # change detection is best effort; the run's outcome stands
        with store.write(db):
            state.log_event(db, task_id, "error", {"stage": "change_detection", "error": type(exc).__name__,
                                                   "detail": str(exc)[:300]}, now)


def _prune_cache(db, task_id, services, now):
    """Public-cache retention after a run (config.cache_max_bytes / cache_max_age_days, at most hourly). A failure
    is recorded on the task; the run's outcome stands."""
    prune = getattr(services, "prune_cache", None)
    if prune is None:
        return
    try:
        prune(now)
    except (sqlite3.Error, OSError) as exc:
        with store.write(db):
            state.log_event(db, task_id, "error", {"stage": "cache_retention", "error": type(exc).__name__,
                                                   "detail": str(exc)[:300]}, now)


def _mark_knowledge_status(report, items):
    """A report answered from memory says which knowledge it rests on, and never presents provisional knowledge
    as accepted: its answer items and findings carry status 'provisional' (knowledge_status keeps the row's
    status) and an uncertainty line says the owner has not accepted it."""
    by_statement = {i["statement"]: i for i in items}
    by_key = {i["key"]: i for i in items}
    for entry in report.get("answer") or []:
        item = by_statement.get(entry.get("text"))
        if item is not None:
            entry["knowledge_id"], entry["knowledge_status"] = item["id"], item["status"]
            if item["status"] == "provisional":
                entry["status"] = "provisional"
    for entry in report.get("findings") or []:
        item = by_statement.get(entry.get("statement")) or by_key.get(entry.get("key"))
        if item is not None:
            entry["knowledge_id"], entry["knowledge_status"] = item["id"], item["status"]
            if item["status"] == "provisional":
                entry["status"] = "provisional"
    lines = report.setdefault("uncertainties", [])
    for item in items:
        if item["status"] == "provisional":
            statement = item["statement"] if len(item["statement"]) <= 90 else item["statement"][:89] + "…"
            lines.append(f"Provisional: \"{statement}\" is provisional knowledge (#{item['id']}, confidence "
                         f"{float(item['confidence'] or 0.0):.2f}) that the owner has not accepted; it is not "
                         "established.")


def needs_web_report(report) -> bool:
    return not (report.get("memory") or {}).get("answered_from_memory")


def _predict(db, knowledge_ids, now):
    """'still holds at next refresh' for each new provisional item, checked after its freshness TTL."""
    from . import knowledge, predictions, temporal
    if not knowledge_ids:
        return
    cfg = state.get_config(db)
    with store.write(db):
        for kid in knowledge_ids:
            item = knowledge.row(db, kid)
            ttl = temporal.ttl(item["freshness_class"], cfg.freshness_ttl or None)
            predictions.add(db, kid, predictions.DEFAULT_EXPECTATION, now + ttl, now)
        store.bump_state_version(db)


def run(db, task_id, services) -> dict:
    """Execute a queued task to a terminal state (see the module docstring)."""
    return _execute(db, str(task_id), services, resume=False)


def resume(db, task_id, services) -> dict:
    """Continue an interrupted (or failed) task from its last checkpoint."""
    task = state.task(db, str(task_id))
    if task["status"] in ("completed", "partial", "cancelled"):
        raise Conflict(f"Research task {task_id} is {task['status']}; submit a new task instead")
    if task["status"] == "queued" and not set(task["checkpoint"] or {}) - {"context", "authorization"}:
        return run(db, task_id, services)
    return _execute(db, str(task_id), services, resume=True)


def requeue(db, task_id, now, *, origin=None, family=None) -> dict:
    """Queue a finished-but-unsuccessful task for the worker again.

    failed               -> the same task (same id) is queued again; the worker resumes it from its checkpoint
                            (completed stages are skipped, documents already read come from the public cache).
    partial / cancelled  -> a new child task (parent_id = task_id) with the same objective, mode, context and
                            owner authorisation; `origin` defaults to the parent's. Documents read before are
                            served conditionally from the public cache.
    anything else        -> Conflict.
    Returns the queued task with `resumed_from` (the given id) and `requeued` ('same' or 'child')."""
    task_id = str(task_id)
    with store.write(db):
        task = state.task(db, task_id, family=family)
        if task["status"] == "failed":
            state.update_task(db, task_id, now, status="queued", stage=None, finished_at=None, error=None,
                              lease_holder=None, lease_expires_at=None, cancel_requested=0)
            state.log_event(db, task_id, "resumed", {"requeued": True, "stages_done":
                                                     (task["checkpoint"] or {}).get("stages_done", [])}, now)
            store.bump_state_version(db)
    if task["status"] == "failed":
        return get(db, task_id, family=family) | {"resumed_from": task_id, "requeued": "same"}
    if task["status"] not in ("partial", "cancelled"):
        raise Conflict(f"Research task {task_id} is {task['status']}; only failed, partial or cancelled tasks can be "
                       "queued again")
    checkpoint = task["checkpoint"] or {}
    extra = {"context": checkpoint["context"]} if checkpoint.get("context") else {}
    if checkpoint.get("authorization") == AUTHORIZED_DISCLOSURE:
        extra["authorization"] = AUTHORIZED_DISCLOSURE
    child = submit(db, task["objective"], now, mode=task["mode"], budget=task["budget"],
                   origin=origin or task["origin"], family=task["family"], parent=task_id, **extra)
    return child | {"resumed_from": task_id, "requeued": "child"}

