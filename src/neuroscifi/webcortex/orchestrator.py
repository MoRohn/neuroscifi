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
                through SafeFetcher (purpose 'page'), extracted, quarantined; JavaScript shells (and, for news /
                time-sensitive questions, scripted pages whose static text is under NEWS_RENDER_MIN_TEXT) escalate
                to the renderer when the budget has browser sessions, with a request allowance that leaves
                RENDER_RESERVE_PER_PAGE requests to each page slot still open (render_budget: a render never
                starves the other pages); deep_research follows <= 2 same-site links of the
                first page when depth and pages remain. Each document is recorded (web_sources, event, checkpoint)
                as soon as it is read; the lease is renewed after every fetch. API records are quarantined like
                pages. An unexpected exception while extracting one document (or reading it back from the cache)
                is that document's failure ('decode_error' or 'internal_error' with the exception type, an
                'error' event), never the task's. A crawl task (`nsf web crawl`, context.crawl) reads its seeds by
                a bounded same-site crawl with crawl.crawl's rules, walked one page at a time (each page recorded
                and checkpointed as it arrives, cancel honoured between pages) instead of searching. A Wikipedia
                hit is read as its article (WikipediaProvider.article, else the page), never as its snippet. A
                rendered page is kept in the public cache; sub-requests the renderer refused are limitations.
                Reading is deliberately serial: cancellation is honoured before every page request (a cancel never
                races several requests already in flight), a crash loses at most the page being read, page
                accounting cannot overshoot max_pages, and SafeFetcher's per-host spacing would serialise most
                same-site candidates anyway; the parallel part of a task is discovery (providers run in
                budget.max_concurrency threads).
  evaluating    claims.extract_claims per document ('extract' event each; one document failing is recorded and
                skipped; an objective with no topic - a crawl - extracts by each page's salience, its title as
                topic) -> claims.cluster -> claims.score (config.freshness_ttl as ttl_overrides); clusters persisted
  synthesizing  synthesis.synthesize -> the report (§5, 'synthesized' event) with its context pack
                (synthesis.context_pack within budget.max_context_tokens, charged to usage.tokens); then, for a
                completed or partial run, reasoning.apply (_reason): a configured model writes an answer from the
                verified evidence only, checked by reasoning.guard (report.reasoning, report.generated_by
                'model:<name>' | 'template'; a 'decision' event); then
                consolidate.run, predictions for new provisional knowledge, mode statistics (procedural memory),
                public-cache retention, and change detection when the task belongs to a monitor

Cancellation (`cancel_requested`, set from any connection) is checked before every network step; a cancelled run
ends 'cancelled' with a partial report. The same check stops a run whose lease another worker took over (its
writes are fenced on lease_holder: state.LostLease; it never consolidates nor writes an outcome) and a run while
instance regulation pauses writes (it ends 'partial' and consolidates nothing; submit refuses with WritesPaused). Time is measured with services.clock against budget.max_seconds. A run
that exhausts its budget or loses a stage ends 'partial' when it still has a report, 'failed' when it has none.
A run that read nothing because every provider or page failed ends 'failed' (W-07; 'partial' when knowledge about
the question exists), its stages cleared so a retry searches again. check() also re-reads the owner's
configuration (at most every CONFIG_RECHECK seconds): web access turned off stops the run 'partial' (WEB_OFF), a
newly denied domain is skipped. A task forgotten while it runs stops at its next fenced write and writes nothing.
Leases name the holder's host digest, pid and process start time: a dead worker on this host does not hold its task
(cancel, resume, requeue and the worker act at once; task JSON carries `worker_alive` and `origin_label`). An
identical submission while one is queued or running returns that task (`deduplicated: true`).
`resume` continues from the checkpoint: completed stages are skipped and documents already read are re-read from
the public cache, so the report then says retrieval_mode 'cache' (live false) for them. `requeue` puts a failed
task back in the queue (same id) or continues a partial/cancelled one as a linked child task.
Autonomous tasks (curiosity, refresh:<id>) get request/byte budgets clamped to the daily allowance left at submit,
and MCP tasks (origin 'mcp') to config.daily_mcp. The owner's own refreshes (origin 'cli'/'console') are not
autonomous.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import math
import os
import re
import socket
import sqlite3
import time
import uuid

from ..instance import store
from ..instance.errors import Conflict, LeaseHeld, ValidationError
from . import state, temporal
from .types import (FETCH_ERROR_CODES, MODES, TERMINAL_STATES, Budget, BudgetExceeded, Claim, ClaimCluster, Document,
                    FetchError, FetchResult, QueryPlan, SearchResult, Usage)

LEASE_SECONDS = 600.0
AUTHORIZED_DISCLOSURE = "owner-approved-disclosure"
PRIVACY_OVERRIDE = f"pass authorization='{AUTHORIZED_DISCLOSURE}'"   # cli.py words it as --allow-private-terms
MCP_ORIGIN = "mcp"
# Origins of tasks a person asked for (console, CLI, voice, Pocket, Pocket voice, an MCP client acting for its user)
# and is waiting on, as opposed to autonomous research (curiosity, refresh:<id>, monitor:<id>, learner, bench).
# The owner's own refreshes are 'refresh-owner:<id>'. Presence and the voice bridge both read user_initiated().
USER_ORIGINS = ("console", "cli", "voice", "pocket", "pocket-voice", "owner", MCP_ORIGIN)
USER_ORIGIN_PREFIXES = ("refresh-owner:",)
OWNER_NOTE_ORIGINS = frozenset({"console", "voice", "pocket", "pocket-voice"})   # reasoning.verdict_note unquoted
MAX_OBJECTIVE = 2000
MAX_FOLLOW = 2
MAX_CONTEXT_PACKAGES = 200
MAX_PRESENTATION = 200
CANDIDATE_FACTOR = 3          # reading ranks up to 3 x max_pages candidates; failed fetches do not use a page slot
_WIKI_ARTICLE = re.compile(r"^https?://([a-z]{2,3})(?:\.m)?\.wikipedia\.org/wiki/([^?#]+)$")


def _as_wikipedia(r: SearchResult) -> SearchResult:
    """A Wikipedia article found by a web search engine, as the encyclopedia's own hit: read through the article
    API (whole or lead, Run.wikipedia) and ranked with the encyclopedia hits, not behind them (RC2 retest T1-07:
    SearXNG's "Python (programming language) - Wikipedia" was cut by the per-host cap behind "Monty Python")."""
    from urllib.parse import unquote
    m = _WIKI_ARTICLE.match(r.url or "")
    if r.provider == "wikipedia" or not m or ":" in unquote(m.group(2)):
        return r
    return replace(r, provider="wikipedia", title=unquote(m.group(2)).replace("_", " "), kind="encyclopedia",
                   metadata={"language": m.group(1), "snippet_only": True, "found_by": r.provider})


# A question about how something began: its encyclopedia article is read beyond the lead (Run.wikipedia)
_ORIGIN_QUESTION = re.compile(r"\b(?:first|originally|initially|earliest)\b|\b(?:founded|invented|created|originated|"
                              r"conceived|history|origins?)\b", re.IGNORECASE)
TIMELY_PER_HOST = 2           # news/time-sensitive questions: at most this many web candidates per host (T1-06);
                              # otherwise max(2, ceil(max_pages / 3)) (T1-07: 8 of 9 candidates on Hacker News)
MAX_UNREAD_HEADLINES = 6      # dated search hits shown when nothing usable could be read (T1-06)
WEB_RESERVE_MODES = ("scientific", "deep_research")
API_PROVIDERS = ("wikipedia", "arxiv", "openalex", "crossref", "pubmed", "pypi", "npm", "osv", "github")
NEWS_RENDER_MIN_TEXT = 500    # news/time-sensitive: a scripted page with less static text than this is rendered
RENDER_RESERVE_PER_PAGE = 2   # requests a render leaves to every other open page slot (robots.txt + the page)
MIN_RENDER_REQUESTS = 4       # a render with fewer sub-requests than this cannot load a page's data: not started
SNIPPET_ONLY = ("wikipedia",)  # search hits whose metadata is a snippet: read the article, never the snippet
CONFIG_RECHECK = 2.0          # seconds between re-reads of the web configuration (enabled, deny_domains) in a run
WEB_OFF = "Web access was turned off during the run"
NO_CLAIM_LEADS = ("No usable claims were extracted", "No source was read")       # synthesis._lead without claims
NO_CLAIM_LIMITATIONS = ("No claim relevant", "No source was read")             # synthesis limitations, likewise
_PUBLIC = ("id", "objective", "mode", "status", "stage", "origin", "family", "parent_id", "budget", "usage",
           "result", "error", "executed_on", "created_at", "updated_at", "started_at", "finished_at",
           "cancel_requested")


class _Cancelled(Exception):
    pass


class _OutOfTime(Exception):
    pass


class _Paused(Exception):
    """Instance regulation paused writes while the task ran: it stops at the next check and ends 'partial'."""


class _Disabled(Exception):
    """The owner turned web access off while the task ran: it stops at the next check and ends 'partial'."""


# Control flow that per-document isolation must never swallow.
_STOP = (_Cancelled, _OutOfTime, _Paused, _Disabled, BudgetExceeded, state.LostLease)


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


def _without_transient(checkpoint: dict) -> dict:
    """A finished task's checkpoint without the search results its provider allows only transiently
    (metadata.transient: Brave Search API Terms of Use §3(b)(i), ADR-034 amendment 1). The run needed them to
    resume; the documents read, claims, attempts and report stay."""
    results = checkpoint.get("results")
    if not isinstance(results, list):
        return checkpoint
    kept = [r for r in results if not (isinstance(r, dict) and (r.get("metadata") or {}).get("transient"))]
    if len(kept) == len(results):
        return checkpoint
    return {**checkpoint, "results": kept, "transient_results_dropped": len(results) - len(kept)}


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
    {"crawl": {seeds, max_pages, max_depth}} (a bounded same-site crawl instead of searching) and/or
    {"hypothesis": id} (the hypothesis the task investigates: evolution.observe settles its prediction on this
    explicit link, lexical key matching being only the fallback) and/or {"presentation": text} (how the owner
    wants the answer written, e.g. "summarize their importance" from a spoken request: given to answer synthesis
    as a style instruction only, never searched for and never a reason to relax the evidence guard)."""
    if context is None:
        return None
    if not isinstance(context, dict) or set(context) - {"packages", "crawl", "hypothesis", "presentation"}:
        raise ValidationError("context accepts only 'packages', 'crawl', 'hypothesis' and 'presentation'")
    presentation = context.get("presentation")
    if presentation is not None:
        if not isinstance(presentation, str) or not presentation.strip() or len(presentation) > MAX_PRESENTATION \
                or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in presentation):
            raise ValidationError(f"context.presentation must be text of at most {MAX_PRESENTATION} characters "
                                  "without control characters")
        presentation = " ".join(presentation.split())
    crawl = _crawl_spec(context["crawl"]) if "crawl" in context else None
    hypothesis = context.get("hypothesis")
    if hypothesis is not None and (isinstance(hypothesis, bool) or not isinstance(hypothesis, int)
                                   or hypothesis < 1):
        raise ValidationError("context.hypothesis must be a positive hypothesis id")
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
    validated = {"packages": out} if out or (crawl is None and hypothesis is None and presentation is None) else {}
    if presentation is not None:
        validated["presentation"] = presentation
    if crawl is not None:
        validated["crawl"] = crawl
    if hypothesis is not None:
        validated["hypothesis"] = hypothesis
    return validated


def submit(db, objective, now, *, mode=None, budget=None, origin="cli", family=None, authorization=None,
           parent=None, proposed=False, context=None) -> dict:
    """Queue a research task. The objective passes the privacy screen (private terms, evidence roots, e-mail
    addresses, phone numbers, ...) unless authorization == 'owner-approved-disclosure'. `proposed=True` creates a
    proposal the worker skips until the owner authorises it (pending scope web:research:<id>). `context` carries
    structured inputs the objective text cannot (`{"packages": [...]}` for registry and advisory lookups); it is
    kept in the checkpoint and merged into the assessment at discovery. WritesPaused while instance regulation
    pauses writes. An identical submission (objective, mode, origin, family, context) while one is queued or
    running returns that task with `deduplicated: true` (duplicate()); nothing new is queued."""
    prepared = prepare(db, objective, now, mode=mode, budget=budget, origin=origin, family=family,
                       authorization=authorization, parent=parent, proposed=proposed, context=context)
    with store.write(db):
        existing = None if prepared["proposed"] else duplicate(db, prepared)
        if existing is None:
            task_id = insert(db, prepared, now)
            store.bump_state_version(db)
    if existing is not None:
        return get(db, existing) | {"deduplicated": True}
    return get(db, task_id)


def duplicate(db, prepared):
    """The id of a queued or running task (not a proposal) with the same objective, mode, origin, family and
    context as a prepared submission, else None: an identical request while one is in flight is answered by that
    task (submit marks it `deduplicated: true`). Caller owns the transaction."""
    rows = db.execute(f"SELECT id, checkpoint FROM web_tasks WHERE objective=? AND mode=? AND origin=? AND "
                      f"family IS ? AND status NOT IN ({','.join('?' * len(TERMINAL_STATES))}) AND "
                      "coalesce(stage,'')!='proposed' ORDER BY created_at, rowid",
                      (prepared["objective"], prepared["mode"], prepared["origin"], prepared["family"],
                       *TERMINAL_STATES)).fetchall()
    wanted = (prepared["initial"] or {}).get("context")
    for task_id, checkpoint in rows:
        if (state.loads(checkpoint, None) or {}).get("context") == wanted:
            return task_id
    return None


def prepare(db, objective, now, *, mode=None, budget=None, origin="cli", family=None, authorization=None,
            parent=None, proposed=False, context=None) -> dict:
    """Validate a submission (submit's checks, no write). Returns what insert() records."""
    cfg = state.require_enabled(db)
    state.require_writable(db)
    context = _context(context)
    if not isinstance(objective, str) or not objective.strip():
        raise ValidationError("objective must be a non-blank string")
    objective = " ".join(objective.split())
    if any(ord(c) < 32 or 127 <= ord(c) < 160 for c in objective):   # NUL broke the length CHECK -> 500 (T5-06)
        raise ValidationError("objective must not contain control characters")
    if len(objective) > MAX_OBJECTIVE:
        raise ValidationError(f"objective must be at most {MAX_OBJECTIVE} characters")
    if authorization not in (None, AUTHORIZED_DISCLOSURE):
        raise ValidationError(f"authorization must be omitted or '{AUTHORIZED_DISCLOSURE}'")
    findings = screen(db, objective, cfg)
    if context and context.get("presentation"):      # it reaches the model endpoint, so it is screened the same way
        findings = findings + screen(db, context["presentation"], cfg)
    if findings and authorization != AUTHORIZED_DISCLOSURE:
        kinds = sorted({f["kind"] for f in findings})
        raise ValidationError(f"objective contains private data ({', '.join(kinds)}); it was not sent anywhere. "
                              f"Remove it, or {PRIVACY_OVERRIDE} to disclose it")
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
    if not proposed:     # D4: a spent daily budget refuses at once (BudgetRefused); _claim charges it atomically
        from . import autonomy
        autonomy.require_room(db, origin, now)
    clamped = None
    if is_autonomous(origin):      # a proposal is sized to what is left now; confirm and claim check again
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


_ORIGIN_LABELS = {"console": "Owner console", "cli": "Command line", "mcp": "MCP client",
                  "curiosity": "Curiosity (autonomous)"}


def user_initiated(origin) -> bool:
    """Whether a task with this origin was asked for by a person (USER_ORIGINS), not started autonomously."""
    origin = str(origin or "")
    return origin in USER_ORIGINS or origin.startswith(USER_ORIGIN_PREFIXES)


def origin_label(origin, family=None) -> str:
    """Plain label of a task's origin for display: 'refresh-owner:<id>' -> "Refresh of knowledge #<id>",
    'refresh:<id>' -> "Scheduled refresh of knowledge #<id> (autonomous)", 'monitor:<id>' -> "Monitor #<id>",
    a Pocket family -> "Pocket device", cli/console/mcp/curiosity by name, anything else as given."""
    origin = str(origin or "")
    if family or origin.startswith("pocket"):
        return "Pocket device"
    for prefix, label in (("refresh-owner:", "Refresh of knowledge #{}"), ("monitor:", "Monitor #{}"),
                          ("refresh:", "Scheduled refresh of knowledge #{} (autonomous)")):
        if origin.startswith(prefix):
            return label.format(origin[len(prefix):])
    return _ORIGIN_LABELS.get(origin, origin)


def worker_alive(task) -> bool | None:
    """For a task a worker holds (lease_holder set): whether that worker process is still running (same host:
    pid and process start time, services.holder_alive). None when nobody holds it or it cannot be told."""
    from .services import holder_alive
    if not task.get("lease_holder") or task["status"] in TERMINAL_STATES:
        return None
    return holder_alive(task["lease_holder"])


def _holder_running(task, now) -> bool:
    """A worker holds the task right now: a lease that has not expired, held by a process that is not known to be
    dead (a crashed worker on this host is detected at once, without waiting for the lease to expire)."""
    if task["lease_holder"] is None or (task["lease_expires_at"] or 0) <= now:
        return False
    return worker_alive(task) is not False


def _public(task) -> dict:
    out = {k: task[k] for k in _PUBLIC}
    checkpoint = task.get("checkpoint") or {}
    out["stages_done"] = list(checkpoint.get("stages_done", []))
    out["proposed"] = task["stage"] == "proposed"
    out["origin_label"] = origin_label(task.get("origin"), task.get("family"))
    out["worker_alive"] = worker_alive(task)
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
    """Queued -> cancelled at once; running -> cancel_requested (the run stops before its next network step). A
    task whose worker process is dead (same host, services.holder_alive) is cancelled at once."""
    with store.write(db):
        task = state.task(db, task_id, family=family)
        if task["status"] in TERMINAL_STATES:
            return _public(task)
        running = task["status"] != "queued" and _holder_running(task, now)
        if running:
            state.update_task(db, task_id, now, cancel_requested=1)
            state.log_event(db, task_id, "cancel_requested", {}, now)
        else:
            state.update_task(db, task_id, now, status="cancelled", cancel_requested=1, finished_at=now,
                              lease_holder=None, lease_expires_at=None,
                              stage=None if task["stage"] == "proposed" else task["stage"])
            reason = "cancelled before it ran" if task["status"] == "queued" or not task["lease_holder"] else \
                "cancelled: its worker stopped responding"
            state.log_event(db, task_id, "cancelled", {"reason": reason}, now)
            from . import autonomy
            autonomy.settle(db, task_id, task["usage"] or {}, task["checkpoint"] or {}, now)
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
        self.exhausted = self.checkpoint.get("exhausted")   # a limit hit before a resume still makes it partial
        self.stage_failed = []
        self.holder = services.holder
        self.deny_list = tuple(self.cfg.deny_domains)
        self.denied_at_start = frozenset(d.strip(".").lower() for d in self.deny_list)
        self.config_read = time.monotonic()

    def clock(self):
        return float(self.services.clock())

    def elapsed(self):
        return self.prior_seconds + max(0.0, self.clock() - self.started)

    def event(self, kind, detail, stage=None):
        """One episodic event, fenced: LostLease when another worker took the task over or it was forgotten."""
        now = self.clock()
        with store.write(self.db):
            state.require_lease(self.db, self.id, self.holder)
            state.log_event(self.db, self.id, kind, detail, now, stage=stage)

    def save(self, **updates):
        """Merge into the checkpoint and persist it with usage and a renewed lease (one short transaction)."""
        self.checkpoint.update(updates)
        self.usage.seconds = round(self.elapsed(), 3)
        self.checkpoint["usage"] = self.usage.to_json()
        self.checkpoint["failures"] = self.failures
        self.checkpoint["limitations"] = self.limitations
        if self.exhausted:
            self.checkpoint["exhausted"] = self.exhausted
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
        if row is None:
            raise state.LostLease(f"research task {self.id} was forgotten while it ran")
        if row[0]:
            raise _Cancelled()
        if row[1] != self.holder:
            raise state.LostLease(f"research task {self.id}: the lease is held by another worker")
        allowed, reason = state.write_allowed(self.db, self.services_db_path())
        if not allowed:
            raise _Paused(reason)
        self.recheck_config()
        if self.elapsed() > self.budget.max_seconds:
            raise _OutOfTime()

    def recheck_config(self, *, force=False):
        """Re-read the owner's web configuration (one meta row) at most every CONFIG_RECHECK seconds: web access
        turned off -> _Disabled (the run ends 'partial', WEB_OFF); a changed deny list applies from the next
        request (denied() skips those hosts, and the fetcher's policy is updated when it can be)."""
        if not force and time.monotonic() - self.config_read < CONFIG_RECHECK:
            return
        self.config_read = time.monotonic()
        cfg = state.get_config(self.db)
        if not cfg.enabled:
            raise _Disabled(WEB_OFF)
        denied = tuple(cfg.deny_domains)
        if denied == self.deny_list:
            return
        self.deny_list = denied
        fetcher = self.services.fetcher
        policy = getattr(fetcher, "policy", None)
        if policy is not None and hasattr(policy, "deny_domains"):
            try:
                fetcher.policy = replace(policy, deny_domains=tuple(dict.fromkeys((*policy.deny_domains, *denied))))
            except (TypeError, AttributeError):   # a fetcher without a replaceable policy: denied() still applies
                pass
        self.event("decision", {"deny_domains_updated": len(denied)}, None)

    def denied(self, url) -> str | None:
        """The owner's deny-list entry (as last read, recheck_config) that `url`'s host falls under, else None."""
        from urllib.parse import urlsplit
        try:
            host = (urlsplit(url).hostname or "").lower().rstrip(".")
        except ValueError:
            return None
        return next((d.strip(".").lower() for d in self.deny_list if d.strip(".") and
                     (host == d.strip(".").lower() or host.endswith("." + d.strip(".").lower()))), None)

    def skip_denied(self, url, provider) -> bool:
        """Record and skip a candidate whose host the owner denied. A domain denied *during* the run (not on the
        list when it started) makes the run 'partial' and says so (RT-W-07): the report no longer covers what the
        question asked for when it was submitted."""
        domain = self.denied(url)
        if domain is None:
            return False
        failure = {"stage": "reading", "provider": provider, "code": "blocked_policy", "url": url,
                   "detail": "domain is on the deny list"}
        self.failures.append(failure)
        self.event("error", failure, "reading")
        if domain not in self.denied_at_start:
            self.limit(f"the owner denied {domain} during the run: sources on it were not read")
        return True

    def note(self, why):
        """A limitation of the report that does not make the run partial (contrast limit())."""
        if why not in self.limitations:
            self.limitations.append(why)

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
        elif assessment.get("dependency_check"):
            self.dependency_check(assessment)
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
            outcome = self.services.registry.search(plan, budget=self.budget, usage=self.usage, sent=self.save)
            self.renew()
            seen = {r["url"] for r in results}
            results += [r.to_json() for r in outcome.results if r.url not in seen]
            attempts += outcome.attempts
            failures += outcome.failures
            from .autonomy import search_counts
            counted = self.checkpoint.setdefault("search_counts", {"searxng_requests": 0, "engine_requests": 0})
            for key, n in search_counts(outcome.attempts).items():
                counted[key] = int(counted.get(key, 0)) + n
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

    def dependency_check(self, assessment):
        """"Latest releases affecting <this system>" (intent.dependency_question): the `nsf web deps` logic instead
        of a web search for the bare word "releases". The packages in use come from the project inventory
        (deps.inventory: local files and installed versions, no network); deps.check makes its bounded direct pass
        through the PyPI/npm/OSV providers (its own budget, clamped by the owner's ceiling, as `nsf web deps`);
        the packages that need attention (security first, then the largest version gaps) become the plan's
        registry lookups, so their records are read as evidence. The summary is report['dependencies']."""
        from . import deps
        try:
            inv = deps.inventory()
        except Exception as exc:     # an unreadable project file: say so, search nothing
            self.note(f"dependency check: the package inventory could not be read ({type(exc).__name__})")
            return
        packages = [p for p in inv["packages"] if p.get("name")]
        if not packages:
            self.note("dependency check: no package in use was found in the project inventory")
            return
        self.check()
        result = deps.check(self.db, self.services, packages)
        self.renew()
        reports = result["packages"]
        rank = {"major": 0, "minor": 1, "patch": 2}
        attention = sorted((r for r in reports if r.get("recommendation") in ("upgrade (security)",
                                                                              "consider upgrade")),
                           key=lambda r: (r["recommendation"] != "upgrade (security)",
                                          rank.get(r.get("behind_by"), 3), -float(r.get("released_at") or 0)))
        keep = ("ecosystem", "name", "current", "latest", "released_at", "behind_by", "recommendation", "action")
        summary = {"packages": len(reports), "source": "project inventory (deps.inventory) + direct registry pass "
                                                       "(deps.check, as `nsf web deps`)",
                   "totals": deps._totals(reports, False),
                   "attention": [{k: r.get(k) for k in keep} | {"advisories": [a.get("id") for a in r["advisories"]]}
                                 for r in attention][:20],
                   "failures": len(result["failures"]), "usage": result["usage"], "budget": result["budget"]}
        assessment["packages"] = [{"ecosystem": r["ecosystem"], "name": r["name"], "version": r["current"]}
                                  for r in attention]
        self.save(dependencies=summary)
        self.event("decision", {"dependency_check": {"packages": len(reports), "attention": len(attention),
                                                     "failures": len(result["failures"])}}, "discovering")
        if result["failures"]:
            self.note(f"dependency check: {len(result['failures'])} registry/advisory lookup(s) failed "
                      "(see `nsf web deps` for the per-package list)")

    def local_terms(self) -> list[str]:
        """Names of the individual's own system that are local context, never search terms (intent.strip_local
        adds the built-in self-references). Owner-listed private terms count, unless the owner authorised
        disclosing them for this task."""
        return [] if self.authorized else list(self.cfg.private_terms)

    def screened(self, plan: QueryPlan) -> QueryPlan:
        """Drop planned queries the privacy screen flags. Under owner-approved disclosure a query is kept when every
        finding in it is private text the objective itself contained (the authorisation covers exactly that),
        compared in normalised form (_finding_key: NFKC, digits only for phone numbers, case-folded e-mail
        addresses and terms), so a value the planner reformatted ("+1 (415) 555-0100" -> "1 415 555 0100") is
        still the authorised one (RT-B-03). A finding the planner shortened beyond that (a part of exactly one
        authorised value of its kind: "415 555 0100") is replaced in the query by the authorised original instead
        of dropping the query; any other private value still drops it. The disclosure is recorded as a decision
        event (counts and kinds only)."""
        cfg = state.get_config(self.db)
        allowed, originals = set(), {}
        if self.authorized:
            for f in screen(self.db, self.objective, cfg):
                key = _finding_key(self.objective, f)
                allowed.add(key)
                originals.setdefault(f["kind"], {})[key] = self.objective[f["offset"]:f["offset"] + f["length"]]
        kept, dropped, disclosed, restored = [], [], [], 0
        for q in plan.queries:
            found = screen(self.db, q.text, cfg)
            if found and allowed and not all(_finding_key(q.text, f) in allowed for f in found):
                text = q.text
                for f in sorted(found, key=lambda f: -f["offset"]):       # right to left: offsets stay valid
                    key = _finding_key(q.text, f)
                    if key in allowed:
                        continue
                    value = key.split(":", 1)[1]
                    same = [o for k, o in (originals.get(f["kind"]) or {}).items()
                            if value and (k.split(":", 1)[1].endswith(value) or value in k.split(":", 1)[1])]
                    if len(same) == 1:          # the authorised value, shortened by the planner (+1 dropped)
                        text = text[:f["offset"]] + same[0] + text[f["offset"] + f["length"]:]
                if text != q.text:
                    refound = screen(self.db, text, cfg)
                    if all(_finding_key(text, f) in allowed for f in refound):
                        q, found, restored = replace(q, text=text), refound, restored + 1
            if found and allowed and all(_finding_key(q.text, f) in allowed for f in found):
                disclosed.append((q, found))
                kept.append((q, found))
            else:
                (dropped if found else kept).append((q, found))
        if disclosed:
            detail = {"disclosed_under_authorization": len(disclosed), "authorization": AUTHORIZED_DISCLOSURE,
                      "findings": sorted({f["kind"] for _, fs in disclosed for f in fs})}
            if restored:
                detail["authorized_text_restored"] = restored
            self.event("decision", detail, "discovering")
        if dropped:
            self.event("decision", {"dropped_queries": len(dropped),
                                    "findings": sorted({f["kind"] for _, fs in dropped for f in fs})}, "discovering")
        return replace(plan, queries=tuple(q for q, _ in kept))

    # ---- reading ------------------------------------------------------------------------------------------------
    def select(self, assessment):
        """Ranked reading candidates (at most CANDIDATE_FACTOR x max_pages): URLs named in the question, then
        structured API records and encyclopedia hits (and, for a monitor's task, feed items: cheap and
        change-friendly), then web results, deduplicated by canonical URL. read() works down this list until
        max_pages documents succeed. A Wikipedia hit is not an API document: its search snippet is never read as
        the document; read() takes the article (wikipedia())."""
        from .extract import canonical_url
        results = [_as_wikipedia(SearchResult(**r)) for r in self.checkpoint.get("results", [])]
        named = [SearchResult(url=u, title=u, snippet="", provider="question", kind="web")
                 for u in assessment.get("urls") or []]
        monitor = self.origin.startswith("monitor:")
        order = []
        for i, r in enumerate(named + results):
            api = r.provider in API_PROVIDERS and bool(r.metadata) and r.provider not in SNIPPET_ONLY
            early = api or r.provider in SNIPPET_ONLY or (monitor and r.provider == "feeds")
            order.append((0 if i < len(named) else (1 if early else 2), i, r, api))
        # hits crowd onto a few hosts (Hacker News items, one publisher's pages): a few candidates per host, so the
        # candidates a run can afford cover more than one site (T1-06, T1-07); fewer for a news question
        timely = "news" in (assessment.get("kinds") or []) or \
            assessment.get("freshness_class") in ("breaking", "volatile")
        host_cap = TIMELY_PER_HOST if timely else max(TIMELY_PER_HOST, math.ceil(self.budget.max_pages / 3))
        picked, seen, per_host = [], set(), {}
        for _, _, r, api in sorted(order, key=lambda x: (x[0], x[1])):
            try:
                key = canonical_url(r.url)
            except Exception:  # an unparsable URL from a provider is skipped, recorded below
                self.failures.append({"stage": "reading", "code": "blocked_policy", "url": r.url,
                                      "detail": "unparsable URL"})
                continue
            if key in seen:
                continue
            seen.add(key)
            host = _host(r.url).removeprefix("www.")
            if not api and r.provider != "question" and per_host.get(host, 0) >= host_cap:
                continue
            per_host[host] = per_host.get(host, 0) + 1
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
        body = meta.pop("body", None)      # a release's whole body: the document text (the snippet is display only)
        abstract = str(meta.get("abstract") or meta.get("summary") or "")
        text = "\n".join(x for x in (r.title, body if isinstance(body, str) and body else r.snippet, abstract) if x)
        doc = Document(url=r.url, final_url=r.url, canonical_url=canonical_url(r.url),
                       domain=registrable_domain(urlsplit(r.url).hostname or ""), title=r.title, text=text,
                       retrieved_at=now, retrieval_mode="api", kind="api", content_type="application/json",
                       published_at=r.published_at, metadata=meta | {"provider": r.provider})
        temporal.settle_publication_date(doc)       # a future issue date is not a publication date (T1-19)
        return doc

    def wikipedia(self, r: SearchResult):
        """A Wikipedia hit read as the document it points to, never its search snippet: the article's plain-text
        lead through WikipediaProvider.article (an 'api' document) when the registry has that provider, else the
        article page fetched and extracted like any page. Returns (doc, api). Raises FetchError (ProviderError is
        mapped to one) when the article cannot be read."""
        from . import safety
        provider = next((p for p in getattr(self.services.registry, "providers", []) or []
                         if getattr(p, "name", None) == "wikipedia"), None)
        article = getattr(provider, "article", None)
        if article is None:
            return self.finish(r.url, self.fetch(r.url)), False
        from .search import ProviderError
        meta = dict(r.metadata or {})
        # "When was Python first released?": the lead names the 3.0 release, the History section the first one
        # (RC2 retest T1-07), so a question about origins reads the article beyond its lead
        import inspect
        try:
            takes_intro = "intro" in inspect.signature(article).parameters
        except (TypeError, ValueError):
            takes_intro = False
        whole = {"intro": False} if takes_intro and _ORIGIN_QUESTION.search(self.objective or "") else {}
        try:
            found = article(r.title, budget=self.budget, usage=self.usage, lang=meta.get("language") or "en", **whole)
        except ProviderError as exc:
            code = exc.code if exc.code in FETCH_ERROR_CODES else "http_error"
            raise FetchError(code, r.url, f"wikipedia {exc.code}: {str(exc.detail)[:200]}") from None
        text = str((found or {}).get("extract") or "").strip()
        if not text:
            raise FetchError("decode_error", r.url, "the article has no readable text")
        from .search import parse_iso_date
        edited = parse_iso_date(found.get("last_edited"))
        meta.pop("snippet_only", None)      # the hit was a snippet; this document is the article itself (RT-W-01)
        # the latest revision time is when someone last edited the article, not when its facts were published: it
        # is the document's modified_at and never dates it for freshness or verification (T1-05)
        page = SearchResult(url=found.get("url") or r.url, title=found.get("title") or r.title, snippet="",
                            provider=r.provider, rank=r.rank, kind=r.kind, published_at=None,
                            metadata=meta | {"abstract": text, "last_edited": found.get("last_edited") or
                                             meta.get("last_edited"), "source": "article"})
        doc = self.api_document(page, self.clock())
        doc.modified_at = edited or parse_iso_date(meta.get("last_edited"))
        if doc.modified_at is not None:
            doc.metadata["modified_source"] = "wikipedia:revision"
        return safety.quarantine(doc), True

    def record(self, doc, *, provider, api, published_hint=None, search_date=None):
        now = self.clock()
        with store.write(self.db):
            state.require_lease(self.db, self.id, self.holder)   # nothing is written for a task taken or forgotten
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
        if published_hint is not None:
            entry["published_hint"] = published_hint
        if search_date and doc.published_at is None:
            entry["search_date"] = search_date
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

    def finish(self, url, result, published_hint=None):
        """Extract -> quarantine (-> render escalation) of a fetched page, on the run's own thread.

        A render reuses the fetched page (`main`, not fetched twice). Its sub-request outcomes
        (renderer.last_events) are checked: sub-requests refused for lack of budget mean the rendered page may be
        incomplete -> a limitation (the run is 'partial') and the render event says ok false; other refused
        sub-requests are a limitation of the report. The rendered page is kept in the public cache (put_rendered)
        so a resume re-reads it instead of rendering again."""
        from . import safety
        doc = safety.quarantine(_extract(result, published_hint))
        renderer = self.services.renderer
        why_render = "js_shell" if doc.flags.get("js_suspected") else self.thin_news(doc, result)
        if why_render and renderer is not None and self.usage.browser_sessions < self.budget.max_browser_sessions:
            from ..resources import policy as rpolicy
            ok, why = rpolicy.engine_gate("browser rendering")      # RD-46: no Chromium under critical memory
            if ok:
                ok, why = renderer.available()
            budget = self.render_budget() if ok else None
            if ok and budget is None:
                ok, why = False, (f"not rendered: the request budget left would not cover the {self.open_slots()} "
                                  "other page slot(s)")
            if ok:
                self.check()
                try:
                    rendered = self.render(renderer, url, result, budget)
                    doc = safety.quarantine(_extract(rendered, published_hint))
                    self.event("render", self.render_outcome(renderer, url) | {"reason": why_render}, "reading")
                    self.keep_rendered(rendered)
                except BudgetExceeded as exc:
                    self.limit(f"Rendering {_host(url)} ran out of budget ({exc}); the page was read without it")
                    self.event("render", {"url": url, "ok": False, "error": {"code": "budget_exhausted",
                                                                             "detail": str(exc)}}, "reading")
                except FetchError as exc:
                    self.failures.append({"stage": "reading", **exc.to_json()})
                    self.event("render", {"url": url, "ok": False, "error": exc.to_json()}, "reading")
            else:
                self.event("decision", {"render": False, "url": url, "reason": why}, "reading")
        return doc

    def thin_news(self, doc, result):
        """'thin_news_text' when the question is news / time-sensitive (a news query kind, or the breaking /
        volatile freshness class) and a scripted HTML page gave less than NEWS_RENDER_MIN_TEXT characters of
        static text (news sites often fill the article in with JavaScript), else None."""
        assessment = self.checkpoint.get("assessment") or {}
        timely = "news" in (assessment.get("kinds") or []) or \
            assessment.get("freshness_class") in ("breaking", "volatile")
        if not timely or len(doc.text or "") >= NEWS_RENDER_MIN_TEXT or \
                getattr(result, "content_type", None) not in ("text/html", "application/xhtml+xml"):
            return None
        return "thin_news_text" if re.search(rb"<script\b", result.body or b"", re.I) else None

    def open_slots(self) -> int:
        """Page slots still open besides the page being read."""
        return max(0, self.budget.max_pages - self.usage.pages - 1)

    def render_budget(self):
        """The task budget with max_requests lowered so a render leaves RENDER_RESERVE_PER_PAGE requests to every
        other open page slot, or None when that leaves fewer than MIN_RENDER_REQUESTS. Charges still go to the
        task's usage; nothing is added to the budget."""
        allowance = self.budget.max_requests - self.usage.requests - RENDER_RESERVE_PER_PAGE * self.open_slots()
        if allowance < MIN_RENDER_REQUESTS:
            return None
        return replace(self.budget, max_requests=self.usage.requests + allowance)

    def render(self, renderer, url, result, budget=None):
        import inspect
        budget = budget or self.budget
        try:
            takes_main = "main" in inspect.signature(renderer.render).parameters
        except (TypeError, ValueError):
            takes_main = False
        if takes_main:
            return renderer.render(url, budget=budget, usage=self.usage, main=result)
        return renderer.render(url, budget=budget, usage=self.usage)

    def render_outcome(self, renderer, url) -> dict:
        """The render event, from the renderer's sub-request log; records the limitations (W-08)."""
        events = [e for e in (getattr(renderer, "last_events", None) or []) if isinstance(e, dict)]
        exhausted = any(e.get("outcome") == "budget_exhausted" or e.get("code") == "budget_exhausted"
                        for e in events)
        refused = [e for e in events if e.get("outcome") == "refused" and e.get("code") != "budget_exhausted"]
        short = sum(1 for e in events if e.get("code") == "budget_exhausted")
        if exhausted:
            self.limit(f"Rendering {_host(url)} ran out of request budget: {short} sub-request(s) were refused, so "
                       "the rendered page may be incomplete")
        if refused:
            self.note(f"Rendering {_host(url)}: {len(refused)} sub-request(s) were refused by the network policy; "
                      "the rendered page may be incomplete")
        return {"url": url, "ok": not exhausted, "refused": len(refused) + short, "budget_exhausted": exhausted}

    def keep_rendered(self, rendered):
        """Keep the rendered DOM in the public cache under its content hash (W-23); best effort."""
        put = getattr(self.services.cache, "put_rendered", None)
        if put is None or getattr(rendered, "retrieval_mode", None) != "rendered":
            return
        try:
            put(rendered)
        except (sqlite3.Error, OSError) as exc:
            self.event("error", {"stage": "reading", "code": "cache_error", "detail": f"rendered page not cached: "
                                                                                       f"{type(exc).__name__}"},
                       "reading")

    def reread(self, entry):
        """A document read before a crash/resume, re-read from the public cache (retrieval_mode 'cache')."""
        from . import safety
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
        return safety.quarantine(_extract(result, entry.get("published_hint")))

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
            self.record(doc, provider=entry.get("provider"), api=entry["api"], search_date=entry.get("search_date"))
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
        waiting = {}   # host -> candidates skipped: its next polite request slot is beyond the fetch timeout

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
                if self.skip_denied(r.url, r.provider):
                    continue
                if _host(r.url) in waiting:       # the same wait would refuse it again: no request is spent
                    waiting[_host(r.url)] += 1
                    continue
                article = r.provider in SNIPPET_ONLY
                try:
                    if article:
                        doc, as_api = self.wikipedia(r)
                    else:
                        result, as_api = self.fetch(r.url), False
                except FetchError as exc:
                    # a page that fails to load is the page's site, not the search provider that found it (T1-12)
                    self.failures.append({"stage": "reading", **({"provider": r.provider} if article else
                                                                 {"found_by": r.provider}), **exc.to_json()})
                    self.event("error", {"stage": "reading", **exc.to_json()}, "reading")
                    self.renew()
                    if exc.code == "rate_limited" and exc.retry_after is not None and not article:
                        waiting.setdefault(_host(r.url), 0)
                    if exc.code == "budget_exhausted":
                        self.limit(f"budget exhausted while reading: {exc.detail}")
                        break
                    continue
                except _STOP:
                    raise
                except Exception as exc:   # one untrusted article never costs the task its other evidence
                    self.document_failed(r.url, r.provider, exc)
                    self.renew()
                    continue
                hint = r.published_at if r.provider == "feeds" else None   # the feed item's date
                if not article:
                    try:
                        doc = self.finish(r.url, result, hint)
                    except _STOP:
                        raise
                    except FetchError as exc:   # the render escalation's own fetches
                        self.failures.append({"stage": "reading", "found_by": r.provider, **exc.to_json()})
                        self.event("error", {"stage": "reading", **exc.to_json()}, "reading")
                        self.renew()
                        continue
                    except Exception as exc:   # one untrusted document never costs the task its other evidence
                        self.document_failed(r.url, r.provider, exc)
                        self.renew()
                        continue
                self.usage.charge(self.budget, pages=1)
                self.record(doc, provider=r.provider, api=as_api, published_hint=hint,
                            search_date=None if as_api or hint is not None else _search_date(r))
            for r in deferred:
                if full() or self.exhausted:
                    break
                take_api(r)
            for host, n in waiting.items():
                if n:
                    self.note(f"{host}: {n} more candidate(s) not tried: the site's crawl delay puts the next "
                              "request beyond the fetch timeout")
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
        """`nsf web crawl`: a bounded same-site breadth-first crawl from the seeds with crawl.crawl's rules (every
        request through SafeFetcher purpose 'crawl': robots, Crawl-delay, throttling; sitemaps; nofollow; binary
        links skipped; depth <= min(max_depth, budget.max_depth), pages <= min(max_pages, budget.max_pages)),
        walked here one page at a time: each page is extracted, quarantined and recorded as a source (provider
        'crawl') with its checkpoint as soon as it arrives, cancellation and the deny list are honoured before every
        request, and the lease is renewed after each. On resume the pages recorded before are not fetched again
        (their links, re-read from the public cache, continue the walk). Every URL that could not be read is named
        in a limitation."""
        from collections import deque
        from urllib.parse import urlsplit, urlunsplit
        from . import crawl as crawler
        from .extract import _absolute, canonical_url, registrable_domain
        seeds = [u for u in dict.fromkeys(spec["seeds"]) if _absolute(u, u)]
        page_cap = max(0, min(int(spec["max_pages"]), self.budget.max_pages))
        depth_cap = max(0, min(int(spec["max_depth"]), self.budget.max_depth))
        sites = {registrable_domain(urlsplit(u).hostname or "") for u in seeds}
        known = {d.canonical_url: d for d in self.documents}
        queue, seen, fetched, failures = deque(), set(), set(), []

        def enqueue(url, depth):
            try:
                key = canonical_url(url)
                host = urlsplit(url).hostname or ""
            except Exception:  # an untrusted link that is not a URL
                return
            if key in seen or registrable_domain(host) not in sites:
                return
            seen.add(key)
            queue.append((url, depth))

        for seed in seeds:
            enqueue(seed, 0)
        recorded = len(done)
        try:
            self.check()
            if depth_cap >= 1 and page_cap > len(seeds):
                listed = self.checkpoint.get("crawl_sitemap_urls")
                if listed is None:            # read once; a resume re-uses the list from the checkpoint
                    listed = []
                    for origin in dict.fromkeys(urlunsplit((urlsplit(u).scheme, urlsplit(u).netloc, "/", "", ""))
                                                for u in seeds):
                        listed += crawler._sitemap_urls(self.services.fetcher, origin, budget=self.budget,
                                                        usage=self.usage, failures=failures)
                    self.save(crawl_sitemap_urls=listed[:crawler.MAX_SITEMAP_ENTRIES])
                for url in listed:
                    enqueue(url, 1)
            while queue and recorded < page_cap:
                url, depth = queue.popleft()
                key = canonical_url(url)
                if key in done:               # read before a resume: continue the walk from its links
                    fetched.add(key)
                    if depth < depth_cap and key in known:
                        for link in known[key].links or []:
                            enqueue(link, depth + 1)
                    continue
                self.check()
                if self.skip_denied(url, "crawl"):
                    continue
                try:
                    result = self.fetch(url, purpose="crawl")
                except FetchError as exc:
                    failures.append(exc.to_json())
                    self.renew()
                    continue
                final = canonical_url(result.final_url)
                if final in fetched or final in done:
                    self.renew()
                    continue                  # redirected onto a page already collected
                fetched.add(final)
                seen.add(final)
                self.usage.charge(self.budget, pages=1)
                links = crawler._page_links(result.body, result.final_url) \
                    if depth < depth_cap and result.content_type in crawler.HTML_TYPES else []
                try:
                    doc = self.finish(url, result)
                except _STOP:
                    raise
                except FetchError as exc:   # the render escalation's own fetches
                    failures.append(exc.to_json())
                    self.renew()
                    continue
                except Exception as exc:   # one untrusted page never costs the crawl its other pages
                    self.document_failed(url, "crawl", exc)
                    self.renew()
                    continue
                self.event("crawl", {"url": url, "depth": depth}, "reading")
                self.record(doc, provider="crawl", api=False)
                recorded += 1
                for link in links:
                    enqueue(link, depth + 1)
        except BudgetExceeded as exc:
            failures.append({"code": "budget_exhausted", "url": None, "detail": str(exc)})
        except _OutOfTime:
            self.limit("time budget exhausted while crawling")
        for failure in failures:
            self.failures.append({"stage": "reading", "provider": "crawl", **failure})
            if failure.get("code") == "budget_exhausted":
                self.limit(f"budget exhausted while crawling: {failure.get('detail')}")
        named = [f for f in failures if f.get("url") and f.get("code") != "budget_exhausted"]
        for failure in named[:10]:
            self.note(f"{failure['url']} was not read ({failure.get('code')}: "
                      f"{str(failure.get('detail') or '')[:120]})".replace(": )", ")"))
        if len(named) > 10:
            self.note(f"{len(named) - 10} more crawl URL(s) were not read (see the evidence)")
        if failures:
            self.event("error", {"stage": "reading", "crawl": True, "failures": len(failures),
                                 "codes": sorted({str(f.get("code")) for f in failures})}, "reading")
        self.renew()

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
            if self.skip_denied(link, "crawl"):
                continue
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
        salience = self.by_salience()
        if salience:
            self.event("decision", {"extraction": "document salience: the objective names no topic, so each "
                                                  "page's most informative sentences are its notable statements"},
                       "evaluating")
        from . import synthesis
        aside = synthesis.package_ecosystems(self.objective, assessment, self.documents)["aside"]
        if aside:       # RT-W-04: another ecosystem's package of the same name is read, never evidence
            self.event("decision", {"packages_set_aside": sorted(aside)}, "evaluating")
        for doc in self.documents:
            if (doc.canonical_url or doc.final_url or doc.url) in aside:
                per_doc.append((doc, 0))
                continue
            try:
                extracted = self.extract_claims(doc, salience)
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
        origins = claims.stamp_origins(found, self.documents)   # copies of one story are one source (ADR-035)
        now = self.clock()
        scored = [claims.score(c, now=now, freshness_class=freshness, ttl_overrides=self.ttl_overrides(),
                               origins=origins) for c in claims.cluster(found)]
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

    def by_salience(self) -> bool:
        """Claims are extracted by document salience when the objective names no topic once URLs and request
        words are removed ("Investigate these sources: URL", "What has changed on URL since last week?")."""
        from . import intent
        return not intent.query_text(self.objective)

    def extract_claims(self, doc, salience):
        """claims.extract_claims for one document; without a topic, by salience (claims.informativeness): the
        page's notable statements, not answers to a question (W-19)."""
        from . import claims
        if not salience:
            return claims.extract_claims(doc, self.objective)
        return claims.extract_claims(doc, "", salience=True)

    # ---- synthesizing -------------------------------------------------------------------------------------------
    def synthesize(self):
        from . import synthesis
        assessment = dict(self.checkpoint.get("assessment") or {})
        self.usage.seconds = round(self.elapsed(), 3)
        report = synthesis.synthesize(self.objective, assessment, self.documents, self.clusters, now=self.clock(),
                                      usage=self.usage, failures=[f for f in self.failures if not _empty_result(f)],
                                      ttl_overrides=self.ttl_overrides())
        report = dict(report)
        _search_dates(report, self.checkpoint.get("documents") or [])
        _advisory_check(report, self.checkpoint.get("attempts") or [])
        _nothing_usable(report, self.checkpoint, self.failures)
        if self.checkpoint.get("dependencies"):
            _dependencies(report, self.checkpoint["dependencies"])
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


def _search_date(r: SearchResult) -> dict | None:
    """The publication date a search engine reported for a hit (SearXNG publishedDate, Hacker News created_at, ...),
    when it reported one: {at, reported_by}. Never invented: None without a parsed date."""
    if r.published_at is None:
        return None
    engines = (r.metadata or {}).get("engines") or []
    engine = engines[0] if isinstance(engines, list) and engines else getattr(r, "engine", None)
    return {"at": float(r.published_at), "reported_by": f"{r.provider}:{engine}" if engine and engine != r.provider
            else str(r.provider)}


SEARCH_DATE_NOTE = ("date reported by the search engine ({by}), not found on the page: lower trust than an in-page "
                    "date; shown for orientation, not used for freshness or claim verification")


def _search_dates(report, entries):
    """Date the report's undated sources by the search engine's reported date (source['search_published_at'],
    'date_source': 'search_engine', 'date_note'). The in-page date always wins: a source with its own published_at
    is never touched. published_at stays None, so freshness and verification still treat the page as undated
    (lower trust); the uncertainty line about the undated source says what the search engine reported."""
    by_url = {}
    for e in entries:
        if e.get("search_date"):
            for key in ("canonical_url", "url", "final_url"):
                if e.get(key):
                    by_url[e[key]] = e["search_date"]
    if not by_url:
        return
    for source in report.get("sources") or []:
        found = by_url.get(source.get("url"))
        if found is None or source.get("published_at") is not None:
            continue
        source["search_published_at"] = found["at"]
        source["date_source"] = "search_engine"
        source["date_note"] = SEARCH_DATE_NOTE.format(by=found["reported_by"])
        day = datetime.fromtimestamp(found["at"], tz=timezone.utc).strftime("%Y-%m-%d")
        prefix = f"Undated source: [{source.get('n')}] "
        report["uncertainties"] = [
            u + f" The search engine ({found['reported_by']}) reports {day} (lower trust, not counted)."
            if isinstance(u, str) and u.startswith(prefix) else u for u in report.get("uncertainties") or []]


_OSV_QUERY = re.compile(r"^osv:([^:]+):(.+)@([^@\s]+)$", re.I)


def _advisory_check(report, attempts):
    """T1-15: "Is numpy 2.5.5 secure?" said "No OSV record was among the sources" although OSV was queried and
    answered with no advisory. A security verdict left 'insufficient' says what was checked: OSV answered for that
    package and version with no advisory, which still does not establish that it is secure (ADR-033). The verdict
    keeps its state; `summary` (shown on the verdict line) and the first reason say so, `advisory_check` records it."""
    verdict = report.get("verdict") or {}
    if verdict.get("aspect") != "secure" or verdict.get("state") != "insufficient":
        return
    statement = str(verdict.get("statement") or "").lower()
    for a in attempts:
        m = _OSV_QUERY.match(str(a.get("query") or ""))
        if a.get("provider") != "osv" or not a.get("ok") or a.get("engine") or m is None:
            continue
        eco, name, version = m.groups()
        if name.lower() not in statement or version.lower() not in statement:
            continue
        when = datetime.fromtimestamp(float(report.get("as_of") or time.time()), tz=timezone.utc)
        found = int(a.get("n") or 0)
        checked = (f"OSV was checked for {name} {version} ({eco}) during this run ({when:%Y-%m-%d %H:%M} UTC): "
                   + ("no known advisory." if not found else f"{found} record(s), none affecting {version}."))
        verdict["summary"] = f"{checked} That does not establish that it is secure; publication never does either."
        verdict["advisory_check"] = {"source": "osv", "query": a.get("query"), "records": found,
                                     "checked_during_run_at": report.get("as_of")}
        verdict["reasons"] = [verdict["summary"]] + [r for r in verdict.get("reasons") or []
                                                     if not str(r).startswith(("No OSV record", "No OSV advisory"))]
        return


_URL_DATE = re.compile(r"/(20\d{2})[/-](\d{1,2})[/-](\d{1,2})(?=[/-])")


def _nothing_usable(report, checkpoint, failures):
    """T1-06: "No usable claims were extracted from 2 sources read" said nothing about why. The lead says precisely
    what the search engines returned, which pages could not be loaded (by site and reason) and which pages had no
    readable text; for a news / time-sensitive question it adds the dated headlines the search engines found that
    were not read (report['unread_headlines']: search-engine data, never evidence)."""
    lead = next((e for e in report.get("answer") or [] if str(e.get("text") or "").startswith(NO_CLAIM_LEADS[0])),
                None)
    if lead is None:
        return
    attempts = [a for a in checkpoint.get("attempts") or [] if a.get("provider")]
    engines = {}
    with_engines = {a["provider"] for a in attempts if a.get("engine")}
    for a in attempts:
        if not a.get("engine") and a["provider"] in with_engines:
            continue               # the provider's fused total; its engines are listed instead
        name = f"{a['provider']}/{a['engine']}" if a.get("engine") else a["provider"]
        entry = engines.setdefault(name, [0, set()])
        entry[0] += int(a.get("n") or 0)
        if not a.get("ok"):
            entry[1].add(str(a.get("error") or "failed").split(":")[0][:40])
    searched = [f"{name} {n} result(s)" + (f" ({', '.join(sorted(errors))})" if errors else "")
                for name, (n, errors) in engines.items()]
    blocked = {}
    for f in failures:
        if f.get("stage") == "reading" and f.get("url"):
            code = str(f.get("code") or "failed")
            key = (_host(f["url"]).removeprefix("www."), f"HTTP {f['status']}" if f.get("status") else code)
            blocked[key] = blocked.get(key, 0) + 1
    shells = {}
    for src in report.get("sources") or []:
        if (src.get("flags") or {}).get("js_suspected"):
            shells[src.get("domain")] = shells.get(src.get("domain"), 0) + 1
    why = []
    if searched:
        why.append("searched: " + ", ".join(searched))
    if blocked:
        why.append("not loaded: " + ", ".join(f"{host} {code}" + (f" x{n}" if n > 1 else "")
                                              for (host, code), n in blocked.items()))
    if shells:
        why.append("no readable text (JavaScript page; rendering is off by default): " +
                   ", ".join(f"{d}" + (f" x{n}" if n > 1 else "") for d, n in shells.items()))
    assessment = checkpoint.get("assessment") or {}
    timely = "news" in (assessment.get("kinds") or []) or \
        assessment.get("freshness_class") in ("breaking", "volatile")
    headlines = _unread_headlines(report, checkpoint, assessment) if timely else []
    if headlines:
        report["unread_headlines"] = headlines
        why.append("dated headlines found but not read (search-engine data, not evidence): " + "; ".join(
            f"«{_short(h['title'], 80)}» ({h['domain']}, {h['date']}, date from {h['date_source']})"
            for h in headlines[:3]))
    if why:
        lead["text"] = f"{str(lead['text']).rstrip('.')}. Why: " + "; ".join(why) + "."


def _unread_headlines(report, checkpoint, assessment):
    """Search hits not read whose title shares a topical word with the question and that carry a date (the search
    engine's, else one in the URL path) within the recency window, newest first."""
    from . import intent
    words = {w.lower() for w in intent.keywords(str(report.get("question") or ""), limit=12).split()}
    read = {s.get("url") for s in report.get("sources") or []}
    window = assessment.get("recency_window")
    now = float(report.get("as_of") or time.time())
    out, seen = [], set()
    for r in checkpoint.get("results") or []:
        title = str(r.get("title") or "")
        if r.get("url") in read or r.get("url") in seen or not words & {w.lower() for w in re.findall(r"\w+", title)}:
            continue
        at, how = r.get("published_at"), "the search engine"
        if at is None:
            m = _URL_DATE.search(str(r.get("url") or ""))
            try:
                at = datetime(*map(int, m.groups()), tzinfo=timezone.utc).timestamp() if m else None
            except ValueError:
                at = None
            how = "the URL"
        if at is None or (window and now - float(at) > float(window) + 86400.0) or float(at) > now + 86400.0:
            continue
        seen.add(r.get("url"))
        out.append({"title": title[:200], "url": r.get("url"), "domain": _host(r.get("url")).removeprefix("www."),
                    "published_at": float(at), "date": datetime.fromtimestamp(float(at), tz=timezone.utc)
                    .strftime("%Y-%m-%d"), "date_source": how, "found_by": (r.get("metadata") or {}).get("found_by")})
    return sorted(out, key=lambda h: -h["published_at"])[:MAX_UNREAD_HEADLINES]


def _dependencies(report, summary):
    """report['dependencies'] and its totals at the start of the summary line (answer[0], a template, not a quote)
    for a dependency-check question (_Run.dependency_check)."""
    report["dependencies"] = summary
    t = summary.get("totals") or {}
    line = (f"Dependency check of {summary.get('packages', 0)} packages in use: "
            f"{t.get('upgrade_security', 0)} with a security advisory affecting the version in use, "
            f"{t.get('consider_upgrade', 0)} with a newer stable release, {t.get('up_to_date', 0)} up to date, "
            f"{t.get('unknown', 0)} unknown (lookup failed).")
    top = [f"{a['name']} {a['current']} -> {a['latest']}" + (" (security)" if a.get("advisories") else "")
           for a in summary.get("attention") or [] if a.get("latest")][:5]
    if top:
        line += " Needing attention: " + ", ".join(top) + "."
    answer = report.get("answer") or []
    if answer and answer[0].get("status") == "summary":
        answer[0]["text"] = line + " " + str(answer[0].get("text") or "")
    else:
        report["answer"] = [{"text": line, "type": "template", "citations": [], "confidence": None,
                             "status": "summary"}] + answer


def _extract(result, published_hint=None):
    """extract.extract, with the feed item's date as `published_hint` (it wins over the page's own metadata); a
    publication date more than a day after the page was read is set aside (temporal.settle_publication_date)."""
    from . import extract
    if published_hint is None:
        doc = extract.extract(result)
    else:
        doc = extract.extract(result, published_hint=published_hint)
    temporal.settle_publication_date(doc)
    return doc


def _empty_result(failure) -> bool:
    """A search that answered with no results: not a retrieval failure (nothing failed), so the report does not
    count it as one."""
    return isinstance(failure, dict) and failure.get("stage") == "discovering" and not failure.get("code") and \
        str(failure.get("error") or "empty") == "empty"


def _host(url) -> str:
    from urllib.parse import urlsplit
    try:
        return urlsplit(url).hostname or str(url)[:80]
    except ValueError:
        return str(url)[:80]


def _found_text(text, finding) -> str:
    return text[finding["offset"]:finding["offset"] + finding["length"]].casefold()


def _finding_key(text, finding) -> str:
    """A privacy finding's value in comparable form: NFKC and case-folded; a phone number as its digits only; an
    e-mail address or other value without whitespace; a term with separators collapsed (RT-B-03)."""
    import unicodedata
    raw = unicodedata.normalize("NFKC", _found_text(text, finding))
    kind = finding.get("kind")
    if kind == "phone":
        value = re.sub(r"[^0-9]", "", raw)
    elif kind == "private_term":
        value = " ".join(x for x in re.split(r"[\s_.·-]+", raw) if x)
    else:
        value = re.sub(r"\s+", "", raw)
    return f"{kind}:{value}"


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
        if holder and holder != services.holder and _holder_running(task, now):
            raise LeaseHeld(f"Research task {task_id} is being run by another worker")
        if not resume and task["status"] == "queued" and is_autonomous(task["origin"]) and \
                not (task["checkpoint"] or {}).get("stages_done"):
            refused = _autonomous_at_claim(db, task, now)
            if refused == "wait":
                if task["lease_holder"] == services.holder:
                    state.update_task(db, task_id, now, lease_holder=None, lease_expires_at=None)
                return None
            if refused:
                state.update_task(db, task_id, now, status="cancelled", finished_at=now, lease_holder=None,
                                  lease_expires_at=None, error=refused)
                state.log_event(db, task_id, "cancelled", {"reason": refused}, now)
                store.bump_state_version(db)
                return None
        admitted = _admit(db, task, now, services)
        if admitted is not None:
            return None
        task = state.task(db, task_id)
        changes = {"lease_holder": services.holder, "lease_expires_at": now + LEASE_SECONDS,
                   "executed_on": executed_on(), "started_at": task["started_at"] or now}
        if task["status"] == "failed":
            changes.update(status="queued", finished_at=None, error=None)
        state.update_task(db, task_id, now, **changes)
        state.log_event(db, task_id, "resumed" if resume else "started",
                        {"holder": services.holder.split(":")[0], "stages_done":
                         (task["checkpoint"] or {}).get("stages_done", [])}, now)
    return state.task(db, task_id)


def _admit(db, task, now, services):
    """D4 (autonomy.admit) inside _claim's transaction: charge the task to its scope's daily budget and clamp its
    budget to what is left. An autonomous task waits (stays queued, a claim_next lease of this worker released)
    while the concurrency cap is reached; with its budget spent it is cancelled with the reason. Returns None when
    the task may start, else a short outcome."""
    from . import autonomy
    verdict = autonomy.admit(db, task, now)
    if verdict.get("wait"):
        if task["lease_holder"] == services.holder and task["status"] == "queued":
            state.update_task(db, task["id"], now, lease_holder=None, lease_expires_at=None)
        return "wait"
    if verdict.get("refused"):
        reason = verdict["refused"]
        state.update_task(db, task["id"], now, status="cancelled", finished_at=now, lease_holder=None,
                          lease_expires_at=None, error=reason, cancel_requested=1)
        state.log_event(db, task["id"], "cancelled", {"reason": reason}, now)
        store.bump_state_version(db)
        return "refused"
    if verdict.get("budget"):
        state.update_task(db, task["id"], now, budget=verdict["budget"])
    return None


def _autonomous_at_claim(db, task, now):
    """W-12: an autonomous task (curiosity, prediction refresh; a confirmed proposal too) is checked against the
    daily autonomous allowance again when a worker starts it: what other autonomous tasks committed since it was
    queued counts. Its request/byte budget is clamped to what is left; with nothing left the reason is returned
    (the caller cancels it), and "wait" when what is left is only held by running tasks (it stays queued, T5-02).
    Caller owns the transaction."""
    from . import scheduler
    cfg = state.get_config(db)
    actual = scheduler.autonomous_remaining(db, now, cfg, exclude=task["id"], reserved=False)
    for key in ("requests", "bytes"):
        if actual[key] < 1:
            used = cfg.daily_autonomous.get(key, 0) - actual[key]
            return (f"daily autonomous cap reached when it was due to start: {key} {used} used of "
                    f"{cfg.daily_autonomous.get(key, 0)}")
    left = scheduler.autonomous_remaining(db, now, cfg, exclude=task["id"])
    if any(left[key] < 1 for key in ("requests", "bytes")):
        return "wait"
    budget = Budget.from_json(task["budget"])
    clamped = {f"max_{k}": left[k] for k in ("requests", "bytes") if getattr(budget, f"max_{k}") > left[k]}
    if clamped:
        state.update_task(db, task["id"], now, budget=replace(budget, **clamped).to_json())
        state.log_event(db, task["id"], "decision", {"clamped_to_daily_allowance": clamped, "at": "start"}, now)
    return None


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
            _memory_lead(report, items)
            status = "completed"
            _reason(db, run, report)
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
            failed = _retrieval_failed(run)
            if failed:
                status, error = _nothing_read(db, run, report, failed)
                if status == "failed":   # a retry (requeue/resume) searches and reads again
                    run.checkpoint["stages_done"] = []
            elif run.mode == "change_detection":
                _compare_with_previous(db, run, report)
            if status != "failed":
                _reason(db, run, report)
    except state.LostLease:
        return _lost_lease(db, task_id, run)
    except _Cancelled:
        status = "cancelled"
        try:
            report = run.synthesize()
            report.setdefault("limitations", []).append("cancelled by request: partial report")
            if not run.is_done("evaluating"):
                _cancelled_before_extraction(report, len(run.documents))
        except Exception as exc:  # a partial report is best effort; the cancellation itself must stand
            error = f"no partial report: {type(exc).__name__}"
    except (_Paused, _Disabled) as exc:
        status = "partial"
        disabled = isinstance(exc, _Disabled)
        run.limit(WEB_OFF if disabled else f"writes paused by instance regulation: {exc}")
        try:
            run.event("decision", {"stopped": "web access turned off" if disabled else "writes paused",
                                   "reason": str(exc)[:300]})
            report = run.synthesize()
        except state.LostLease:
            return _lost_lease(db, task_id, run)
        except Exception as inner:  # best effort; the stop itself must stand
            report, error = None, f"{'stopped' if disabled else 'writes paused'}: {str(exc)[:200]}; no partial " \
                                  f"report: {type(inner).__name__}"
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:500]}"
        try:
            run.event("error", {"stage": run.checkpoint.get("stages_done", [])[-1:] or ["discovering"],
                                "error": type(exc).__name__, "detail": str(exc)[:300]})
        except state.LostLease:
            return _lost_lease(db, task_id, run)
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
            run.checkpoint["verification"] = list(consolidation.get("verification") or [])[:200]
            run.checkpoint["predictions_settled"] = list(consolidation.get("predictions_settled") or [])[:50]
            report["consolidation"] = {k: consolidation[k] for k in ("provisional", "strengthened", "unchanged",
                                                                      "superseded", "contested", "held", "pending",
                                                                      "questions_deferred", "review_question")}
            _predict(db, consolidation["provisional"], now)
        except state.LostLease:
            return _lost_lease(db, task_id, run)
        except Exception as exc:
            report.setdefault("limitations", []).append(f"consolidation failed: {type(exc).__name__}")
            try:
                run.event("error", {"stage": "consolidating", "error": type(exc).__name__, "detail": str(exc)[:300]})
            except state.LostLease:
                return _lost_lease(db, task_id, run)
            status = "partial"
    run.usage.seconds = round(run.elapsed(), 3)
    try:
        with store.write(db):
            state.update_task(db, task_id, now, holder=run.holder, status=status, stage=None, result=report,
                              error=error, usage=run.usage.to_json(), finished_at=now, lease_holder=None,
                              lease_expires_at=None,
                              checkpoint=_without_transient(run.checkpoint) | {"usage": run.usage.to_json()})
            state.log_event(db, task_id, status, {"live": (report or {}).get("live"),
                                                  "sources": len((report or {}).get("sources") or []),
                                                  "limitations": len((report or {}).get("limitations") or []),
                                                  "error": error}, now)
            state.record_mode_outcome(db, run.mode, status, run.usage.to_json(), now)
            from . import autonomy
            autonomy.settle(db, task_id, run.usage.to_json(), run.checkpoint, now)
            store.bump_state_version(db)
    except state.LostLease:
        return _lost_lease(db, task_id, run)
    services.persist(db, now)
    _prune_cache(db, task_id, services, now)
    _after_run(db, task_id, now)
    return get(db, task_id)


def _reason(db, run, report):
    """Model reasoning over the verified evidence (reasoning.apply; COGNITIVE_INFERENCE §3): adds
    report['reasoning'] and report['generated_by'] ('model:<name>' or 'template'); the extractive answer is not
    changed and model text is never consolidated. A model failure only degrades the label. The model calls get
    what is left of the task's time budget (budget.max_seconds - elapsed) as their deadline, and their time is
    counted in the task's usage. One 'decision' event records the outcome when a model endpoint is configured.
    An autonomous task's model calls first take a background model slot (resources.policy; RD-46)."""
    from . import reasoning
    from ..resources import policy as rpolicy
    db_path = getattr(run.services, "db_path", None)

    # F6, N4: the plain note only for what the owner typed or said in the console, by voice or on Pocket. Any other
    # origin (the CLI, which the model's own web_research tool also uses; MCP; refresh-owner; monitors; the bench) may
    # carry a statement the owner did not word: quoted. An autonomous task needs the owner's authorisation.
    asked = "owner" if run.origin in OWNER_NOTE_ORIGINS else "autonomous" if is_autonomous(run.origin) else "confirmed"
    if asked == "autonomous":
        try:
            if any(e["kind"] == "decision" and e["detail"].get("decision") == "proposal authorised by the owner"
                   for e in state.events(db, run.id)):
                asked = "confirmed"
        except sqlite3.Error:
            pass        # unknown: no note (fail closed)

    def apply():
        return reasoning.apply(db, report, run.services, question=run.objective, now=run.clock(), task_id=run.id,
                               time_left=run.budget.max_seconds - run.elapsed(),
                               presentation=(run.checkpoint.get("context") or {}).get("presentation"), asked=asked)
    try:
        if not is_autonomous(run.origin):
            out = apply()
        else:      # RD-46: background model calls share capped slots across processes (one while audio runs)
            deadline = time.monotonic() + max(0.0, run.budget.max_seconds - run.elapsed())
            with rpolicy.background_model_slot(db_path, deadline=deadline) as slot:
                if slot is not None:
                    out = apply()
                else:
                    audio = f"; {rpolicy.AUDIO_REASON}" if rpolicy.audio_active(db_path) else ""
                    out = reasoning._degraded(report, "degraded", "resources: no background model slot was free "
                                              f"({rpolicy.model_slots(db_path)} at a time{audio})", asked=asked)
    except Exception as exc:   # the extractive report must survive any reasoning defect
        out = reasoning._degraded(report, "error", f"reasoning failed: {type(exc).__name__}", asked=asked)
    _sense_note(report, asked)
    run.usage.seconds = round(run.elapsed(), 3)     # model time counts against the task's time budget
    report["usage"] = run.usage.to_json()
    if out["status"] != "not_configured":
        run.event("decision", {"reasoning": out["status"], "generated_by": out["generated_by"],
                               "accepted": len(out.get("answer") or []), "rejected": len(out.get("rejected") or []),
                               "reason": out.get("reason"), "usage": out.get("usage")}, "synthesizing")


def _sense_note(report, asked):
    """T1-07 (R4): when the sources hold several senses of the question's subject and the question named none, the
    reader is told which one was answered ("NeuroSciFi note: this answer is about Rust, the programming language.
    Rust also names a video game; ..."), beside any verdict note. Built from the question's name and fixed sense
    labels only (sense.separate); not for an autonomous task no owner saw (as verdict_note)."""
    from . import sense
    note = (report.get("sense") or {}).get("note")
    reasoning = report.get("reasoning")
    if not note or asked == "autonomous" or not isinstance(reasoning, dict):
        return
    prior = reasoning.get("note") if isinstance(reasoning.get("note"), dict) else None
    body = note[len(sense.NOTE_LEAD):]
    text = f"{prior['text']} {body[:1].upper()}{body[1:]}" if prior and prior.get("text") else note
    reasoning["note"] = {"text": text, "by": "rules"}


def _lost_lease(db, task_id, run):
    """Another worker took the task over (this worker's lease expired): stop without writing an outcome or
    consolidating; the new holder finishes the task. One decision event records it. A task forgotten while it ran
    (governance.forget_research / forget_knowledge of a refresh) leaves nothing behind: no event is written and
    {"id", "status": "forgotten"} is returned."""
    now = run.clock()
    with store.write(db):
        gone = db.execute("SELECT 1 FROM web_tasks WHERE id=?", (task_id,)).fetchone() is None
        if not gone:
            state.log_event(db, task_id, "decision", {"stopped": "lease lost to another worker",
                                                      "holder": run.holder.split(":")[0]}, now)
    if gone:
        return {"id": task_id, "status": "forgotten", "lease_lost": True}
    return get(db, task_id) | {"lease_lost": True}


def _labels(failures, limit=6) -> str:
    out = []
    for f in failures:
        who = f.get("provider") or ""
        if f.get("engine"):
            who = f"{who}/{f['engine']}" if who else str(f["engine"])
        if not who or who == "crawl":
            who = _host(f["url"]) if f.get("url") else (who or "retrieval")
        code = str(f.get("code") or f.get("error") or "failed").split(":")[0][:60]
        label = f"{who}: {code}"
        if label not in out:
            out.append(label)
    more = f" and {len(out) - limit} more" if len(out) > limit else ""
    return ", ".join(out[:limit]) + more


def _retrieval_failed(run):
    """Why nothing could be read (W-07), or None: no document was read, and either every candidate page (or crawl
    seed) failed to load, or no search provider answered at all (every one failed: network down, circuits open).
    An empty search result is not a failure, and running out of budget is reported as a budget limit."""
    if run.documents:
        return None
    real = [f for f in run.failures if not _empty_result(f)
            and not str(f.get("code") or f.get("error") or "").startswith("budget_exhausted")]
    reading = [f for f in real if f.get("stage") == "reading"]
    if reading:
        return f"Nothing could be read: every source tried failed ({_labels(reading)})"
    searching = [f for f in real if f.get("stage") == "discovering"]
    attempts = run.checkpoint.get("attempts") or []
    if not searching or run.checkpoint.get("results") or any(a.get("n") for a in attempts):
        return None
    # An empty answer counts as an answer only from a specialist ("no such package": the registry knows); a general
    # web provider whose engines came back empty while others failed found nothing, which is not an answer
    # (W-21/RT-W-08: brave 429, startpage error, duckduckgo/bing empty and wikipedia circuit_open read nothing).
    general = {getattr(p, "name", None) for p in getattr(run.services.registry, "providers", None) or []
               if getattr(p, "general_web", False)}
    if any(a.get("ok") and not a.get("engine") and a.get("provider") not in general for a in attempts):
        return None
    empty = any(a.get("ok") for a in attempts)
    return f"Nothing could be read: every search provider failed{' or found nothing' if empty else ''} " \
           f"({_labels(searching)})"


def _nothing_read(db, run, report, why):
    """A run that read nothing because retrieval failed is 'failed', not 'completed' (or 'partial' when the
    individual already knows something about the question: the report points at that knowledge). Returns
    (status, error)."""
    limitations = [x for x in report.get("limitations") or [] if not str(x).startswith(NO_CLAIM_LIMITATIONS)]
    report["limitations"] = [why] + limitations
    _reword_lead(report, NO_CLAIM_LEADS, f"{why}.")
    known = []
    try:
        from . import knowledge
        known = [i for i in knowledge.query(db, run.objective, run.clock())["items"]
                 if i["id"] in set(run.checkpoint.get("known") or []) and not i["contested"]]
    except Exception:   # pointing at prior knowledge is best effort; the failure stands
        known = []
    if known:
        report.setdefault("uncertainties", []).append(
            "Known before this run (not re-verified now): " + "; ".join(
                f"knowledge #{i['id']} ({i['status']}): «{_short(i['statement'])}»" for i in known[:3]))
        report["memory"] = {"answered_from_memory": False, "knowledge_ids": [i["id"] for i in known[:3]]}
        return "partial", None
    return "failed", why


def _compare_with_previous(db, run, report):
    """change_detection (W-20): compare the pages read with the previous finished change_detection run that read
    the same page (its snapshot: content hash and extracted statements, as stored with its sources and claims).
    report["change"] = {"baseline_task", "since", "unchanged", "pages": [{url, content_changed, added, removed}]}
    and the lead says "Unchanged since <date>" or what changed; without an earlier run this run is the baseline."""
    pages = {d.canonical_url: d for d in run.documents if d.kind != "api" and d.canonical_url}
    if not pages:
        return
    marks = ",".join("?" * len(pages))
    row = db.execute(f"SELECT t.id, t.finished_at FROM web_tasks t JOIN web_sources s ON s.task_id = t.id "
                     f"WHERE t.mode = 'change_detection' AND t.status IN ('completed', 'partial') AND t.id != ? "
                     f"AND s.canonical_url IN ({marks}) ORDER BY t.finished_at DESC, t.rowid DESC LIMIT 1",
                     (run.id, *pages)).fetchone()
    lead = (report.get("answer") or [{}])[0]
    if row is None:
        report["change"] = {"baseline_task": None, "since": None, "unchanged": None, "pages": []}
        lead["text"] = (f"First check of {', '.join(sorted(pages))[:300]}: no earlier snapshot to compare with; "
                        f"this run is the baseline. {lead.get('text') or ''}").strip()
        return
    base_id, since = row
    before = {s["canonical_url"]: s["content_sha256"] for s in state.sources(db, base_id)}
    old_claims, new_claims = {}, {}
    for c in state.claims(db, base_id):
        old_claims.setdefault(c["canonical_url"], set()).add(" ".join(c["text"].split()))
    for c in state.claims(db, run.id):
        new_claims.setdefault(c["canonical_url"], set()).add(" ".join(c["text"].split()))
    rows = []
    for url, doc in sorted(pages.items()):
        if url not in before:
            continue
        old, new = old_claims.get(url, set()), new_claims.get(url, set())
        rows.append({"url": url, "content_changed": bool(doc.content_sha256 and before[url] and
                                                         doc.content_sha256 != before[url]),
                     "added": sorted(new - old), "removed": sorted(old - new)})
    unchanged = all(not r["content_changed"] and not r["added"] and not r["removed"] for r in rows)
    day = datetime.fromtimestamp(float(since), timezone.utc).strftime("%Y-%m-%d")
    report["change"] = {"baseline_task": base_id, "since": since, "unchanged": unchanged, "pages": rows}
    if unchanged:
        text = f"Unchanged since {day}: the same content as the previous check (task {base_id[:8]})."
    else:
        added = sum(len(r["added"]) for r in rows)
        removed = sum(len(r["removed"]) for r in rows)
        changed = sum(1 for r in rows if r["content_changed"])
        text = (f"Changed since {day} (previous check: task {base_id[:8]}): {changed} page(s) with different "
                f"content; {added} statement(s) new, {removed} no longer present (listed under change).")
        if not added and not removed:
            text = (f"Changed since {day} (previous check: task {base_id[:8]}): the content differs, but the "
                    "extracted statements are the same.")
    lead["text"] = f"{text} {lead.get('text') or ''}".strip()


def _short(text, n=90):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def _reword_lead(report, prefix, replacement):
    for entry in report.get("answer") or []:
        if str(entry.get("text") or "").startswith(prefix):
            entry["text"] = replacement
            return True
    return False


def _cancelled_before_extraction(report, n_docs):
    """W-21: a run cancelled before evaluating never extracted claims; the report says so instead of 'no relevant
    claim could be extracted'."""
    report["limitations"] = [x for x in report.get("limitations") or []
                             if not str(x).startswith(NO_CLAIM_LIMITATIONS)]
    report["limitations"].append("Cancelled before claims were extracted: no statement was evaluated")
    _reword_lead(report, NO_CLAIM_LEADS,
                 f"Cancelled before claims were extracted from the {n_docs} source{'s' if n_docs != 1 else ''} read.")


def _memory_lead(report, items):
    """W-21: an answer from memory read no source now; its lead says so instead of "N sources read"."""
    import re
    n = len(items)
    text = f"Answered from memory: {n} knowledge item{'s' if n != 1 else ''}; no source was read for this answer"
    for entry in report.get("answer") or []:
        lead = str(entry.get("text") or "")
        if re.match(r"^\d+ sources? read;", lead):
            entry["text"] = re.sub(r"^\d+ sources? read", text, lead, count=1)
            return
        if lead.startswith(NO_CLAIM_LEADS):
            entry["text"] = f"{text}."
            return


def _after_run(db, task_id, now):
    """Change detection for a monitor's task, however it ran (inline tick, worker, resume): the monitor's
    comparison with its baseline (scheduler.after_monitor_task); for an autonomous task, the owner is notified of
    meaningful findings only (autonomy.after_task). A failure is recorded; the outcome stands."""
    try:
        origin = db.execute("SELECT origin FROM web_tasks WHERE id=?", (task_id,)).fetchone()
        if origin is not None and is_autonomous(origin[0]):
            from . import autonomy
            autonomy.after_task(db, task_id, now)
            return
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
    from . import knowledge, predictions
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
    if task["status"] in ("partial", "cancelled"):
        raise Conflict(f"Research task {task_id} is {task['status']}; it cannot be resumed. Requeue it to continue "
                       f"as a linked task that reuses what was read (`nsf web requeue {task_id}`, or Requeue in the "
                       "console)")
    if task["status"] == "completed":
        raise Conflict(f"Research task {task_id} is completed; submit a new task (or refresh its knowledge) instead")
    if task["status"] == "queued" and not set(task["checkpoint"] or {}) - {"context", "authorization"}:
        return run(db, task_id, services)
    return _execute(db, str(task_id), services, resume=True)


def requeue(db, task_id, now, *, origin=None, family=None) -> dict:
    """Queue a finished-but-unsuccessful task for the worker again.

    failed               -> the same task (same id) is queued again; the worker resumes it from its checkpoint
                            (completed stages are skipped, documents already read come from the public cache).
    running, worker dead -> the same (a crashed worker on this host is detected at once: services.holder_alive).
    partial / cancelled  -> a new child task (parent_id = task_id) with the same objective, mode, context and
                            owner authorisation; `origin` defaults to the parent's. Documents read before are
                            served conditionally from the public cache. Idempotent: while a child of this task is
                            queued or running it is returned (requeued 'existing') instead of creating another; the
                            check and the insert are one transaction.
    anything else        -> Conflict.
    Returns the queued task with `resumed_from` (the given id) and `requeued` ('same', 'child' or 'existing')."""
    task_id = str(task_id)
    with store.write(db):
        task = state.task(db, task_id, family=family)
        crashed = task["status"] not in TERMINAL_STATES and task["status"] != "queued" and \
            task["lease_holder"] is not None and worker_alive(task) is False
        if task["status"] == "failed" or crashed:
            state.update_task(db, task_id, now, status="queued", stage=None, finished_at=None, error=None,
                              lease_holder=None, lease_expires_at=None, cancel_requested=0)
            state.log_event(db, task_id, "resumed", {"requeued": True, "worker_stopped": crashed, "stages_done":
                                                     (task["checkpoint"] or {}).get("stages_done", [])}, now)
            store.bump_state_version(db)
    if task["status"] == "failed" or crashed:
        return get(db, task_id, family=family) | {"resumed_from": task_id, "requeued": "same"}
    if task["status"] not in ("partial", "cancelled"):
        raise Conflict(f"Research task {task_id} is {task['status']}; only failed, partial or cancelled tasks (or one "
                       "whose worker stopped) can be queued again")
    existing = _open_child(db, task_id)
    if existing is not None:
        return get(db, existing, family=family) | {"resumed_from": task_id, "requeued": "existing"}
    checkpoint = task["checkpoint"] or {}
    extra = {"context": checkpoint["context"]} if checkpoint.get("context") else {}
    if checkpoint.get("authorization") == AUTHORIZED_DISCLOSURE:
        extra["authorization"] = AUTHORIZED_DISCLOSURE
    prepared = prepare(db, task["objective"], now, mode=task["mode"], budget=task["budget"],
                       origin=origin or task["origin"], family=task["family"], parent=task_id, **extra)
    with store.write(db):
        existing = _open_child(db, task_id)
        if existing is None:
            child = insert(db, prepared, now)
            store.bump_state_version(db)
    if existing is not None:
        return get(db, existing, family=family) | {"resumed_from": task_id, "requeued": "existing"}
    return get(db, child, family=family) | {"resumed_from": task_id, "requeued": "child"}


def _open_child(db, parent_id):
    """A queued or running child task of `parent_id` (newest first), else None."""
    found = db.execute(f"SELECT id FROM web_tasks WHERE parent_id=? AND status NOT IN "
                       f"({','.join('?' * len(TERMINAL_STATES))}) AND coalesce(stage,'')!='proposed' "
                       "ORDER BY created_at DESC, rowid DESC LIMIT 1", (parent_id, *TERMINAL_STATES)).fetchone()
    return found[0] if found else None
