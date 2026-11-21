# Web Cortex architecture

Status: **binding for implementation**. A lane may add private helpers; changing a public signature in §4 needs
an entry in §8 and the same-commit update of this file. Requirement IDs (WEB-xx) are listed in
`docs/architecture/REQUIREMENTS_MATRIX.md` § WEB.

## 1. What Web Cortex is, in this codebase

NeuroSciFi 0.5 is a modular monolith: one Python package, one SQLite file per individual, a loopback owner
console and the Pocket bridge. It contains **no language model, no embedding encoder and no background
scheduler** (ADR-009, ADR-012), and until now it never contacted a network service. Web Cortex adds a governed
path to the public web *inside that shape*:

- it is a new package, `neuroscifi.webcortex`, called by the existing CLI, console and Pocket adapters;
- its accepted outputs live in the **same individual file** (migration 6), so forgetting, checkpoints,
  erasure and inventory cover them;
- raw downloaded content lives in a **separate public cache** (`<db stem>.webcache.sqlite`), never in
  checkpoints, and holds no queries (privacy boundary, ADR-023);
- the executive stays the decision point: knowledge enters semantic memory as *candidates*; the owner (or an
  explicitly configured policy) accepts it through the existing `pending` / `nsf resolve` path.

Honest limits that shape the design (each has an ADR in §8):

| Directive assumes | What exists | Consequence |
|---|---|---|
| LLM reasoning, synthesis | none on the host; no model in the package | Extractive, rule-based, testable synthesis. Every sentence in an answer is a quoted source sentence or a template over measured quantities. |
| Embeddings / vector DB / reranker | none (ADR-009) | BM25 + recency + source-independence ranking; GPU unused for retrieval. |
| Multi-node fabric | Pocket pairing families + the Spark | The Spark executes; Pocket devices and the console are access points. Routing is recorded, not federated. |
| Executive cognition over open text | binary-belief executive with `pending` questions | Web decisions are a separate, auditable policy (`intent.assess`); approvals reuse `pending`. |

## 2. Pipeline

```
INTENT ──▶ DISCOVERY ──▶ ACQUISITION ──▶ EXTRACTION ──▶ VERIFICATION ──▶ SYNTHESIS ──▶ MEMORY ──▶ PREDICTION ──▶ FEEDBACK
intent.py  search.py     net.py          extract.py     claims.py        synthesis.py  state.py    predictions.py  feedback
           providers/*   cache.py        (scrapling)    temporal.py                    consolidate curiosity.py   (state.py)
                         browser.py                     safety.py
                         crawl.py
                  └──────────────── orchestrator.py: tasks, budgets, checkpoints, cancel/resume ────────────────┘
                  scheduler.py (monitors, refresh, curiosity) · worker (queued tasks) · interfaces (CLI, console, Pocket, MCP)
```

Each stage is a pure-ish module with typed inputs/outputs (`types.py`), runnable and testable alone. The
orchestrator writes one episodic event per stage transition (`web_events`), which is what the UI shows as
Discovering → Reading → Evaluating → Synthesizing → Completed. No stage fabricates progress.

## 3. Trust boundaries

| Boundary | Control |
|---|---|
| Instance ↔ public web | `net.SafeFetcher` only. Resolve → validate every address → connect to the validated IP (no second lookup: DNS-rebinding safe) → TLS verified against the hostname → manual redirects, each hop re-validated → size, time, type limits → robots.txt for pages/feeds/crawls → per-host throttle, Retry-After. |
| Instance ↔ open-webSearch daemon | Loopback only, owner-configured URL, plain HTTP to 127.0.0.1. The daemon contacts search engines; its own fetch tools are **not** used. Its `/mcp` mode is not used (binds 0.0.0.0, no auth). |
| Browser rendering | Optional. Fresh Chromium context per page, no profile, no permissions, downloads off, service workers blocked; **every** request the page makes is intercepted and served through `SafeFetcher` (so SSRF and robots apply to sub-resources) or refused. |
| Retrieved content ↔ cognition | Content is data: stored, quoted, scored. `safety.scan_injection` flags and quarantines instruction-like text; flagged sentences never become claims. Nothing in content can start a fetch beyond the task's budgeted link-following, create a monitor, change config, or reach a tool. |
| Private memory ↔ search providers | `safety.privacy_screen` blocks queries containing e-mail addresses, phone numbers, IPs, file paths, secrets, partner/camera roots from the evidence journal, or owner-listed private terms, unless the owner passes explicit authorization. Monitors and curiosity queries are built from public claim text only. |
| Pocket ↔ research | Off unless `pocket_research` is enabled. Tasks are scoped to the submitting pairing family; other families get 404. Pocket can submit, read, cancel and save; it cannot accept knowledge, change config or forget the individual's memory. |

## 4. Module contracts (public API)

All functions take `now` (or an injected `clock`) explicitly. Errors crossing into the instance layer are
`instance.errors` types; acquisition failures are `types.FetchError` and are recorded, never swallowed.

### 4.1 Acquisition (`net.py`, `cache.py`, `extract.py`, `browser.py`, `crawl.py`)

```python
net.NetPolicy(allowed_schemes=("http","https"), allowed_ports=(80,443,8080,8443), max_redirects=5,
              max_bytes=5_000_000, timeout=15.0, user_agent=config.USER_AGENT,
              content_types=(...), deny_domains=(), min_domain_interval=1.0)
net.check_address(ip: str) -> str | None             # reason it is forbidden, else None
net.check_url(url: str, policy) -> urllib.parse.SplitResult   # raises FetchError('blocked_policy')
net.SafeFetcher(policy, *, cache: PublicCache|None=None, resolver=None, connector=None,
                clock=time.time, sleep=time.sleep)
    .fetch(url, *, method="GET", body=None, headers=None, purpose="page"|"api"|"feed"|"crawl"|"robots",
           conditional=True, budget: Budget|None=None, usage: Usage|None=None) -> FetchResult
    .robots_allowed(url, *, budget=None, usage=None) -> bool
    .stats() -> dict                                  # per-host counts, errors, backoffs, bytes
    async .afetch(...)                                # asyncio.to_thread with a concurrency semaphore
# resolver(host, port) -> list[str]; connector(ip, port, timeout) -> socket.socket   (tests: fixtureweb)
# purpose 'api' skips robots (documented public APIs); everything else obeys robots.txt (cached 24 h).
# Identical concurrent GETs are coalesced. 429/503 Retry-After sets a host backoff; a request inside a
# backoff raises FetchError('rate_limited', retry_after=...). Usage is charged one request per network call.

cache.PublicCache(path)          # separate SQLite file, mode 0600, no queries stored
    .lookup(url) -> dict|None    # {final_url, status, content_type, sha256, etag, last_modified, fetched_at}
    .put(result: FetchResult) -> str (sha256)  ; .body(sha256) -> bytes|None
    .robots(host) -> (text, fetched_at)|None ; .put_robots(host, text, fetched_at)
    .prune(now, *, max_bytes, max_age_seconds) -> dict ; .forget_url(url) ; .forget_domain(domain) -> int
    .stats() -> dict

extract.extract(result: FetchResult, *, engine="auto"|"scrapling"|"trafilatura") -> Document
extract.extract_html(body: bytes, url: str, *, retrieved_at, retrieval_mode, engine="auto") -> Document
extract.extract_pdf(body: bytes, url: str, *, retrieved_at, max_pages=30) -> Document
extract.parse_feed(body: bytes, url: str) -> list[dict]      # {title, url, published_at, summary}
extract.parse_sitemap(body: bytes, url: str) -> list[dict]   # {url, lastmod}
extract.registrable_domain(host) -> str ; extract.canonical_url(url) -> str
# HTML parsed with scrapling.Selector(huge_tree=False). Hidden content (display:none, hidden, aria-hidden,
# <template>, zero-width chars) is removed and counted in flags["hidden_removed"]. JS-shell pages set
# flags["js_suspected"]. Dates from JSON-LD, meta (article:published_time, dc.date, citation_*), <time>,
# then HTTP Last-Modified (marked metadata["date_source"]).

browser.BrowserRenderer(fetcher: SafeFetcher, *, executable=None, timeout=20.0)
    .available() -> (bool, str)
    .render(url, *, budget, usage) -> FetchResult    # retrieval_mode='rendered'; charges browser_sessions=1

crawl.crawl(fetcher, seeds, *, budget, usage, max_pages, max_depth, same_site=True, sitemaps=True) -> list[FetchResult]
crawl.discover_feeds(html: bytes, url) -> list[str]
```

### 4.2 Discovery (`search.py`, `providers/*`, `intent.py`)

```python
search.SearchProvider (Protocol): name: str; kinds: frozenset[str]; search(query: Query, limit: int, *, budget, usage) -> list[SearchResult]
search.ProviderError(provider, detail, *, retryable=True)
search.ProviderRegistry(providers: list, *, clock=time.time, failure_threshold=3, cooldown=300.0)
    .search(plan: QueryPlan, *, budget, usage, per_query=8) -> SearchOutcome   # parallel per query, fallback, RRF fusion, dedupe by canonical URL
    .record(name, ok: bool, latency: float, error: str|None=None)
    .status() -> list[dict]       # per provider/engine: state (closed|open|half_open), successes, failures, last_error, p50 latency
    .load_state(dict) / .dump_state() -> dict   # persisted by state.py (procedural memory)
providers.openwebsearch.OpenWebSearchProvider(base_url, engines, *, timeout=20.0)   # per-engine health and fallback, strips HTML
    .health() -> dict             # GET /health and /status
providers.openwebsearch.Daemon(node="node", package_dir=deploy/open-websearch, host="127.0.0.1", port=3210)  # start/stop/status
providers.structured: WikipediaProvider, ArxivProvider, OpenAlexProvider, CrossrefProvider, PubMedProvider,
    PyPIProvider, NpmProvider, OSVProvider, GitHubReleasesProvider   # all via SafeFetcher(purpose="api")
providers.feeds.FeedProvider(feed_urls)      ; providers.corpus.CorpusProvider(results)   # offline/bench
providers.build(config: WebConfig, fetcher: SafeFetcher) -> list[SearchProvider]

intent.assess(question: str, *, now, known: list[dict] = ()) -> dict
#  {"needs_web": bool, "reasons": [str], "mode": MODES, "freshness_class": FRESHNESS_CLASSES,
#   "recency_window": seconds|None, "kinds": [QUERY_KINDS], "entities": [str], "urls": [str],
#   "comparison": [str, str]|None, "claim": str|None, "packages": [..]}
#  `known` = matching accepted knowledge with freshness; fresh, uncontested knowledge -> needs_web False.
intent.plan(question: str, assessment: dict, *, now) -> QueryPlan   # decomposition, expansion, temporal/site/kind routing
intent.refine(plan, outcome: SearchOutcome, assessment, *, now) -> QueryPlan|None   # one refinement round when results are thin
```

### 4.3 Evidence and time (`text.py`, `claims.py`, `temporal.py`, `safety.py`, `synthesis.py`)

```python
text.tokens(s) -> list[str] ; text.sentences(s) -> list[str] ; text.BM25(docs: list[list[str]]).scores(q) -> list[float]
text.shingles(s, k=5) -> set ; text.jaccard(a, b) -> float ; text.values(s) -> list[str]   # numbers, versions, percents

safety.scan_injection(text) -> list[dict]    # {pattern, excerpt, offset}
safety.quarantine(doc: Document) -> Document # removes flagged sentences; doc.flags["injection"]
safety.privacy_screen(query: str, *, private_terms=()) -> list[dict]   # empty list = safe to send
safety.untrusted(text, source) -> dict        # {"untrusted": True, "source": url, "text": ...} envelope for agents

claims.extract_claims(doc: Document, question: str, *, max_claims=8) -> list[Claim]
claims.cluster(claims: list[Claim]) -> list[ClaimCluster]
claims.independence_groups(claims) -> list[list[str]]   # same registrable domain or near-duplicate text => one group
claims.contradictions(a: Claim, b: Claim) -> str|None   # reason or None
claims.score(cluster, *, now, freshness_class) -> ClaimCluster   # Beta posterior; rank/popularity never counted as truth

temporal.FRESHNESS: dict[class -> {"ttl": seconds, "description": str}]
temporal.classify(question: str, assessment: dict|None=None) -> str
temporal.parse_date(text: str, *, now=None) -> float|None ; temporal.event_dates(sentence, *, reference) -> list[float]
temporal.is_stale(last_verified_at, freshness_class, now, *, ttl_overrides=None, newer_release_seen=False) -> (bool, str)
temporal.window_ok(published_at, window, now) -> bool|None   # None = undated
temporal.supersedes(new: dict, old: dict) -> bool  ; temporal.describe_age(ts, now) -> str

synthesis.synthesize(question, assessment, documents, clusters, *, now, usage, failures) -> dict  # the Report, §5
synthesis.context_pack(report: dict, *, max_tokens) -> dict  # bounded working-memory bundle with citations + uncertainty
```

### 4.4 Memory, orchestration, autonomy (`state.py`, `orchestrator.py`, `consolidate.py`, `curiosity.py`, `predictions.py`, `scheduler.py`, `services.py`)

```python
state.get_config(db) -> WebConfig ; state.set_config(db, changes: dict, now) -> WebConfig ; state.require_enabled(db)
services.build(db, db_path, *, fixture=None) -> Services   # fetcher, cache, registry, renderer, clock
orchestrator.submit(db, objective, now, *, mode=None, budget=None, origin="cli", family=None,
                    authorization=None, parent=None) -> dict      # task row; 'queued'
orchestrator.run(db, task_id, services) -> dict    # executes stages, checkpoints after each, honours cancel
orchestrator.cancel(db, task_id, now, *, family=None) -> dict
orchestrator.resume(db, task_id, services) -> dict # continues from the last checkpoint; cached documents reused
orchestrator.get(db, task_id, *, family=None) -> dict ; orchestrator.listing(db, *, family=None, status=None, limit=50)
orchestrator.evidence(db, task_id, *, family=None) -> dict    # sources, claims, links ("show your evidence")
consolidate.run(db, task_id, now) -> dict          # candidates -> knowledge (provisional|pending owner) with versions
knowledge.query(db, topic, now) -> dict            # what is known, from where, verified when, stale, provisional, contested
knowledge.review(db, knowledge_id, accept: bool, now, *, note="") -> dict   # also reachable via nsf resolve
knowledge.archive / reject / forget(db, knowledge_id, now) ; knowledge.refresh(db, knowledge_id, now) -> task
curiosity.rank(db, now, *, limit=10) -> list[dict]  # {target, kind, info_gain, cost, utility, reason}
predictions.record(db, knowledge_id, expectation, check_after, now) ; predictions.check_due(db, now, services) -> list
scheduler.monitor_add(db, topic, now, *, interval, mode="continuous", budget=None, authorized_by) -> dict
scheduler.tick(db, now, services, *, max_tasks=None) -> dict   # due monitors, refreshes, curiosity (if autonomous)
scheduler.work(db_path, *, once=False, stop=None) -> None      # drains queued tasks (worker service)
```

### 4.5 Interfaces

- CLI `nsf web …` (`webcortex/cli.py`): `config`, `status`, `health`, `daemon start|stop|status`, `search`, `fetch`,
  `ask`, `tasks`, `show`, `evidence`, `cancel`, `resume`, `knowledge`, `review`, `refresh`, `archive`, `reject`,
  `forget`, `monitor add|list|pause|resume|stop`, `tick`, `work`, `curiosity`, `predictions`, `deps`, `bench`, `mcp`.
- Console (loopback): `/api/web/*` routes and a Research view.
- Pocket (opt-in): `research_submit`, `research_list`, `research_get`, `research_cancel` actions under the
  existing bearer/pairing model; family-scoped.
- MCP stdio server (`nsf web mcp`): read-mostly tools `web_search`, `web_research`, `web_task`, `web_evidence`,
  `web_knowledge`. Outputs wrap content in `safety.untrusted` envelopes. No forget/config tools.

## 5. The research report (task result)

```json
{"question": "...", "mode": "deep_research", "as_of": 1795200000.0, "live": true,   // illustrative shape
 "answer": [{"text": "quoted or templated sentence", "citations": [1, 3], "confidence": 0.82, "status": "corroborated"}],
 "findings": [...], "contradictions": [{"a": {...}, "b": {...}, "reason": "different values: 2.3 vs 2.4", "assessment": "..."}],
 "uncertainties": ["..."], "limitations": ["no source dated within the 7-day window", "brave: rate_limited"],
 "freshness": {"class": "breaking", "window_seconds": 604800, "satisfied": false, "newest_source_at": ...},
 "sources": [{"n": 1, "title": "...", "url": "...", "domain": "...", "published_at": ..., "retrieved_at": ...,
              "retrieval_mode": "live", "kind": "page", "flags": {...}}],
 "verdict": null | {"label": "supported|disputed|insufficient|contested", "confidence": 0.7, "reasons": [...]},
 "comparison": null | {"items": [...], "rows": [...]},
 "methodology": null | [{"source": 2, "design": "in vitro", "sample": "n = 24", "kind": "primary|review|preprint"}],
 "usage": {...}, "budget": {...}}
```

`live` is true only if at least one cited source was retrieved live during this task; otherwise the report
says `"live": false` and every source carries `retrieval_mode: "cache"`.

## 6. Data (migration 6, owner `webcortex/state.py`)

| Table | Memory role | Notes |
|---|---|---|
| `web_tasks` | working memory / executive state | id (uuid), objective, mode, status, stage, origin, family digest, budget, usage, checkpoint, result, error, timestamps |
| `web_events` | **episodic** | one row per stage event: started, query, search, fetch, extract, claim, error, decision, finding, completed, cancelled |
| `web_sources` | provenance | url, canonical, domain, title, dates, retrieval mode, content sha (→ public cache), flags; per task |
| `web_claims` | extracted claims (raw ≠ accepted) | text, key, kind, polarity, values, published/event/retrieved/verified, source, task |
| `web_claim_links` | knowledge graph edges | supports / contradicts / supersedes / duplicates |
| `web_knowledge` | **semantic + temporal** | versioned statements: provisional / accepted / stale / archived / rejected, validity interval, superseded_by, supporting & contradicting claim ids, freshness class, pending id |
| `web_entities`, `web_mentions` | knowledge graph | entity resolution by normalised name |
| `web_strategies` | **procedural** | provider/mode outcome statistics, circuit-breaker state |
| `web_monitors` | continuous investigation | topic, plan, interval, budget, baseline, authorised by, status |
| `web_predictions` | prediction | expectation about a knowledge item, check time, outcome (confirmed / error / inconclusive) |
| `web_feedback` | metacognition | before/after confidence, effect (strengthened / weakened / unchanged / reversed / new), information gain |

Public cache (`<stem>.webcache.sqlite`): `documents(sha256, body, content_type, bytes, first_seen)`,
`urls(url, final_url, status, sha256, etag, last_modified, fetched_at)`, `robots(host, text, fetched_at)`.

## 7. Execution placement

The process that runs `orchestrator.run` (CLI `nsf web ask`, `nsf web work` service, or `nsf web tick` timer) is
the executing node; on a deployment that is the DGX Spark. Pocket devices and the console submit and read;
they never fetch. GPU is not used: no stage has a model to run (ADR-022). The worker records `executed_on`
(hostname digest, cpu count, load) per task.

## 8. Decisions and recorded contract changes

Decisions: `docs/architecture/ARCHITECTURAL_DECISIONS.md` ADR-021 … ADR-030.

Additive changes made during implementation (all backwards compatible with §4):

| Module | Change |
|---|---|
| types | `JsonMixin.to_json` serialises nested contract objects; `Usage.charge` is atomic under one module lock. |
| net | `NetPolicy.max_concurrency`; `SafeFetcher.sitemaps(url)`; `FetchError.url` omits the query string (queries stay out of logs); API responses are never cached (they embed queries); any forbidden DNS answer refuses the host; `localhost`, `.local`, `.internal`, `home.arpa` and non-canonical numeric hosts refused; robots.txt capped at 512 KiB, Crawl-delay at 60 s. |
| extract | `extract_html(..., headers=None, final_url=None)`, `extract_pdf(..., retrieval_mode, final_url)`; off-site `rel=canonical` recorded, not used. |
| crawl / browser | `crawl(..., failures=list)` collects failures; `BrowserRenderer.last_events`; Chromium's own DNS and proxy are disabled so it cannot open sockets itself; WebSocket, RTCPeerConnection and EventSource constructors throw. |
| search / providers | `ProviderError(code=)`; empty-result health rule documented in `search.py`; `OpenWebSearchProvider(clock, failure_threshold, cooldown)`; `Daemon(state_dir)` stops only the process it started; `FeedProvider(feed_urls, fetcher=)`; arXiv over https. |
| intent | `assess` adds `sites`, `language`, `notes`, `why`; bare arithmetic needs no web. |
| claims / synthesis | `evaluate`, `posterior`, `info_gain` (bits); age discounts evidence only for breaking, volatile and release classes; report answer items carry `type: quote|template`; scientific findings carry `section`, technical findings `package`. |
| orchestration | `submit(..., proposed=False)` for curiosity proposals awaiting `web:research:<id>`; `services.build(..., providers, clock, sleep)`; `scheduler.tick(..., queue)`; `scheduler.work` returns `{"processed": [...]}`; contested clusters without knowledge become `web:contested:<task>:<n>` informational questions; one open question per knowledge item (updates appended). |
| schema | 12 `web_*` tables; `web_claims` keyed `(task_id, id)`. |
| interfaces | `orchestrator.submit(..., context={"packages": [...]})` for technical mode; `cli.SERVICES_OPTIONS` test hook; console `web_worker`, `worker_options`; Pocket `state.research` flag, precache 9 assets (ADR-015); HTTP routes in API_CONTRACTS §8. |
