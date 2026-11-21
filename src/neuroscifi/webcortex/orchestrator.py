"""Research tasks: submit, run, cancel, resume (ARCHITECTURE §2, §4.4, §5, §7).

A task is a row in web_tasks. `run` claims a lease and executes the stages, writing one episodic event per step
and a checkpoint after each stage, all in short transactions (no write transaction is ever held across network
I/O):

  discovering   knowledge.query + intent.assess; when the web is not needed the report is built from fresh
                knowledge (decision event says why); otherwise intent.plan -> privacy-screened queries ->
                registry.search -> at most one intent.refine round
  reading       top results within budget.max_pages, structured API results first (their metadata/abstract is the
                document, kind 'api', nothing fetched), deduplicated by canonical URL, fetched through SafeFetcher
                (purpose 'page'), extracted, quarantined; JavaScript shells escalate to the renderer when the budget
                has browser sessions; deep_research follows <= 2 same-site links of the first page when depth and
                pages remain. Each document is recorded (web_sources, event, checkpoint) as soon as it is read.
  evaluating    claims.extract_claims per document -> claims.cluster -> claims.score; clusters persisted
  synthesizing  synthesis.synthesize -> the report (§5); then consolidate.run, predictions for new provisional
                knowledge, mode statistics (procedural memory)

Cancellation (`cancel_requested`, set from any connection) is checked before every network step; a cancelled run
ends 'cancelled' with a partial report. Time is measured with services.clock against budget.max_seconds. A run
that exhausts its budget or loses a stage ends 'partial' when it still has a report, 'failed' when it has none.
`resume` continues from the checkpoint: completed stages are skipped and documents already read are re-read from
the public cache, so the report then says retrieval_mode 'cache' (live false) for them.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import os
import socket
import uuid

from ..instance import store
from ..instance.errors import Conflict, LeaseHeld, ValidationError
from . import state
from .types import (MODES, TERMINAL_STATES, Budget, BudgetExceeded, Claim, ClaimCluster, Document, FetchError,
                    FetchResult, QueryPlan, SearchResult, Usage)

LEASE_SECONDS = 600.0
AUTHORIZED_DISCLOSURE = "owner-approved-disclosure"
MAX_OBJECTIVE = 2000
MAX_FOLLOW = 2
MAX_CONTEXT_PACKAGES = 200
API_PROVIDERS = ("wikipedia", "arxiv", "openalex", "crossref", "pubmed", "pypi", "npm", "osv", "github")
_PUBLIC = ("id", "objective", "mode", "status", "stage", "origin", "family", "parent_id", "budget", "usage",
           "result", "error", "executed_on", "created_at", "updated_at", "started_at", "finished_at",
           "cancel_requested")


class _Cancelled(Exception):
    pass


class _OutOfTime(Exception):
    pass


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


def _context(context):
    """Validate the structured task context: {"packages": [{ecosystem, name, version|None}, ...]} (<= 200)."""
    if context is None:
        return None
    if not isinstance(context, dict) or set(context) - {"packages"}:
        raise ValidationError("context accepts only 'packages'")
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
    return {"packages": out}


def submit(db, objective, now, *, mode=None, budget=None, origin="cli", family=None, authorization=None,
           parent=None, proposed=False, context=None) -> dict:
    """Queue a research task. The objective passes the privacy screen (private terms, evidence roots, e-mail
    addresses, phone numbers, ...) unless authorization == 'owner-approved-disclosure'. `proposed=True` creates a
    proposal the worker skips until the owner authorises it (pending scope web:research:<id>). `context` carries
    structured inputs the objective text cannot (`{"packages": [...]}` for registry and advisory lookups); it is
    kept in the checkpoint and merged into the assessment at discovery."""
    cfg = state.require_enabled(db)
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
    if family is not None and (not isinstance(family, str) or not 0 < len(family) <= 64):
        raise ValidationError("family must be a digest string")
    if mode is None:
        from . import intent, knowledge
        known = knowledge.query(db, objective, now)["items"]
        mode = intent.assess(objective, now=now, known=known)["mode"]
    if mode not in MODES:
        raise ValidationError(f"mode must be one of {', '.join(MODES)}")
    chosen = _budget(cfg, mode, budget)
    if parent is not None and db.execute("SELECT 1 FROM web_tasks WHERE id=?", (str(parent),)).fetchone() is None:
        parent = None   # the parent was forgotten; the link is not kept
    task_id = str(uuid.uuid4())
    with store.write(db):
        db.execute("INSERT INTO web_tasks(id,objective,mode,status,stage,origin,family,parent_id,budget,usage,"
                   "checkpoint,created_at,updated_at) VALUES(?,?,?,'queued',?,?,?,?,?,?,?,?,?)",
                   (task_id, objective, mode, "proposed" if proposed else None, origin, family,
                    None if parent is None else str(parent), state.dumps(chosen.to_json()),
                    state.dumps(Usage().to_json()), state.dumps({"context": context}) if context else None,
                    now, now))
        state.log_event(db, task_id, "submitted",
                        {"mode": mode, "origin": origin, "budget": chosen.to_json(), "proposed": bool(proposed),
                         "disclosure_authorized": bool(findings)}, now)
        store.bump_state_version(db)
    return get(db, task_id)


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
        self.budget = Budget.from_json(task["budget"])
        self.checkpoint = task["checkpoint"] or {}
        self.usage = Usage.from_json(self.checkpoint.get("usage") or task["usage"])
        self.prior_seconds = float(self.usage.seconds or 0.0)
        self.started = self.clock()
        self.failures = list(self.checkpoint.get("failures", []))
        self.limitations = list(self.checkpoint.get("limitations", []))
        self.documents, self.clusters = [], []
        self.exhausted = None
        self.stage_failed = []

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
            state.update_task(self.db, self.id, now, checkpoint=self.checkpoint, usage=self.usage.to_json(),
                              lease_expires_at=now + LEASE_SECONDS)

    def stage(self, name):
        now = self.clock()
        with store.write(self.db):
            state.update_task(self.db, self.id, now, status=name, stage=name, lease_expires_at=now + LEASE_SECONDS)
            state.log_event(self.db, self.id, "stage", {"stage": name}, now, stage=name)

    def done(self, name, **updates):
        stages = list(self.checkpoint.get("stages_done", []))
        if name not in stages:
            stages.append(name)
        self.save(stages_done=stages, **updates)

    def is_done(self, name):
        return name in self.checkpoint.get("stages_done", [])

    def check(self):
        """Before every network step: honour cancel (any connection may set it) and the time budget."""
        row = self.db.execute("SELECT cancel_requested FROM web_tasks WHERE id=?", (self.id,)).fetchone()
        if row is None or row[0]:
            raise _Cancelled()
        if self.elapsed() > self.budget.max_seconds:
            raise _OutOfTime()

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
        self.save(assessment=assessment, known=[k["id"] for k in known])
        if not assessment.get("needs_web", True):
            self.event("decision", {"needs_web": False, "reasons": assessment.get("reasons", []),
                                    "knowledge": [k["id"] for k in known]}, "discovering")
            return False
        self.event("decision", {"needs_web": True, "reasons": assessment.get("reasons", [])}, "discovering")
        plan = self.screened(intent.plan(self.objective, assessment, now=now))
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
            seen = {r["url"] for r in results}
            results += [r.to_json() for r in outcome.results if r.url not in seen]
            attempts += outcome.attempts
            failures += outcome.failures
            self.event("search", {"results": len(outcome.results), "attempts": len(outcome.attempts),
                                  "failures": [f.get("error") for f in outcome.failures][:10], "round": round_no},
                       "discovering")
            if round_no:
                break
            refined = intent.refine(plan, outcome, assessment, now=self.clock())
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

    def screened(self, plan: QueryPlan) -> QueryPlan:
        cfg = state.get_config(self.db)
        kept, dropped = [], []
        for q in plan.queries:
            found = screen(self.db, q.text, cfg)
            (dropped if found else kept).append((q, found))
        if dropped:
            self.event("decision", {"dropped_queries": len(dropped),
                                    "findings": sorted({f["kind"] for _, fs in dropped for f in fs})}, "discovering")
        return replace(plan, queries=tuple(q for q, _ in kept))

    # ---- reading ------------------------------------------------------------------------------------------------
    def select(self, assessment):
        from .extract import canonical_url
        results = [SearchResult(**r) for r in self.checkpoint.get("results", [])]
        named = [SearchResult(url=u, title=u, snippet="", provider="question", kind="web")
                 for u in assessment.get("urls") or []]
        order = []
        for i, r in enumerate(named + results):
            api = r.provider in API_PROVIDERS and bool(r.metadata)
            order.append((0 if i < len(named) else (1 if api else 2), i, r, api))
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
        return picked[:max(0, self.budget.max_pages)]

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
        from . import extract, safety
        result = self.services.fetcher.fetch(url, purpose=purpose, budget=self.budget, usage=self.usage)
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
            doc = Document(**entry["doc"])
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
        assessment = self.checkpoint.get("assessment") or {}
        done = {d["canonical_url"]: d for d in self.checkpoint.get("documents", [])}
        for entry in list(done.values()):
            doc = self.reread(entry)
            if doc is None:
                done.pop(entry["canonical_url"])
                continue
            self.record(doc, provider=entry.get("provider"), api=entry["api"])
        if self.is_done("reading"):
            return
        picked = self.select(assessment)
        self.save(selected=[{"url": r.url, "provider": r.provider, "api": api} for r, api in picked])
        try:
            for r, api in picked:
                from .extract import canonical_url
                if canonical_url(r.url) in done:
                    continue
                self.check()
                if self.usage.pages + 1 > self.budget.max_pages:
                    self.limit(f"page budget reached ({self.budget.max_pages} documents)")
                    break
                if api:
                    self.usage.charge(self.budget, pages=1)
                    self.record(self.api_document(r, self.clock()), provider=r.provider, api=True)
                    continue
                try:
                    doc = self.acquire(r.url)
                except FetchError as exc:
                    self.failures.append({"stage": "reading", "provider": r.provider, **exc.to_json()})
                    self.event("error", {"stage": "reading", **exc.to_json()}, "reading")
                    if exc.code == "budget_exhausted":
                        self.limit(f"budget exhausted while reading: {exc.detail}")
                        break
                    continue
                self.usage.charge(self.budget, pages=1)
                self.record(doc, provider=r.provider, api=False)
            self.follow()
        except BudgetExceeded as exc:
            self.limit(f"budget exhausted while reading: {exc}")
        except _OutOfTime:
            self.limit("time budget exhausted while reading")
        self.done("reading")

    def follow(self):
        """deep_research: follow <= MAX_FOLLOW same-site links of the first page (bounded crawl, depth 1)."""
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
                doc = self.acquire(link, purpose="crawl")
            except FetchError as exc:
                self.failures.append({"stage": "reading", "provider": "crawl", **exc.to_json()})
                self.event("error", {"stage": "reading", "crawl": True, **exc.to_json()}, "reading")
                continue
            self.usage.charge(self.budget, pages=1)
            self.event("crawl", {"from": first.url, "url": link}, "reading")
            self.record(doc, provider="crawl", api=False)

    # ---- evaluating ---------------------------------------------------------------------------------------------
    def evaluate(self):
        from . import claims
        assessment = self.checkpoint.get("assessment") or {}
        freshness = assessment.get("freshness_class") or "stable"
        found = []
        for doc in self.documents:
            found += claims.extract_claims(doc, self.objective)
        now = self.clock()
        scored = [claims.score(c, now=now, freshness_class=freshness) for c in claims.cluster(found)]
        sources = {}
        for entry in self.checkpoint.get("documents", []):
            for key in ("canonical_url", "url", "final_url"):
                if entry.get(key):
                    sources[entry[key]] = entry["source_id"]
        with store.write(self.db):
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
                                      usage=self.usage, failures=self.failures)
        report = dict(report)
        report.setdefault("limitations", [])
        for why in self.limitations:
            if why not in report["limitations"]:
                report["limitations"].append(why)
        report["budget"] = self.budget.to_json()
        report["usage"] = self.usage.to_json()
        return report


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
            items = [i for i in knowledge.query(db, task["objective"], run.clock())["items"]
                     if i["id"] in set(run.checkpoint.get("known", [])) and not i["stale"] and not i["contested"]]
            run.documents, run.clusters = _memory_documents(items)
            run.stage("synthesizing")
            report = run.synthesize()
            report["memory"] = {"answered_from_memory": True, "knowledge_ids": [i["id"] for i in items],
                                "reasons": (run.checkpoint.get("assessment") or {}).get("reasons", [])}
            report["live"] = False
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
                except Exception as exc:   # a failed stage still allows a partial report
                    run.stage_failed.append("evaluating")
                    run.limit(f"evaluating failed: {type(exc).__name__}")
                    run.event("error", {"stage": "evaluating", "error": type(exc).__name__, "detail": str(exc)[:300]},
                              "evaluating")
            run.stage("synthesizing")
            report = run.synthesize()
            run.done("synthesizing")
            status = "partial" if (run.exhausted or run.stage_failed) else "completed"
    except _Cancelled:
        status = "cancelled"
        try:
            report = run.synthesize()
            report.setdefault("limitations", []).append("cancelled by request: partial report")
        except Exception as exc:  # a partial report is best effort; the cancellation itself must stand
            error = f"no partial report: {type(exc).__name__}"
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
    if report is not None and status in ("completed", "partial") and needs_web_report(report):
        try:
            from . import consolidate
            consolidation = consolidate.run(db, task_id, now)
            report["consolidation"] = {k: consolidation[k] for k in ("provisional", "strengthened", "unchanged",
                                                                      "superseded", "contested", "held", "pending")}
            _predict(db, consolidation["provisional"], now)
        except Exception as exc:
            report.setdefault("limitations", []).append(f"consolidation failed: {type(exc).__name__}")
            run.event("error", {"stage": "consolidating", "error": type(exc).__name__, "detail": str(exc)[:300]})
            status = "partial"
    run.usage.seconds = round(run.elapsed(), 3)
    with store.write(db):
        state.update_task(db, task_id, now, status=status, stage=None, result=report, error=error,
                          usage=run.usage.to_json(), finished_at=now, lease_holder=None, lease_expires_at=None,
                          checkpoint=run.checkpoint | {"usage": run.usage.to_json()})
        state.log_event(db, task_id, status, {"live": (report or {}).get("live"),
                                              "sources": len((report or {}).get("sources") or []),
                                              "limitations": len((report or {}).get("limitations") or []),
                                              "error": error}, now)
        state.record_mode_outcome(db, run.mode, status, run.usage.to_json(), now)
        store.bump_state_version(db)
    services.persist(db, now)
    return get(db, task_id)


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
    if task["status"] == "queued" and not set(task["checkpoint"] or {}) - {"context"}:
        return run(db, task_id, services)
    return _execute(db, str(task_id), services, resume=True)

