# Web Cortex: search and acquisition

Status: describes the code committed at `5f09ac3` (`src/neuroscifi/webcortex/`). Where a statement rests on a test, the test name is given. Contracts: [ARCHITECTURE.md](ARCHITECTURE.md) §3 and §4.1-4.2. Why these choices: ADR-022 to ADR-025 and ADR-030 in [ARCHITECTURAL_DECISIONS.md](../architecture/ARCHITECTURAL_DECISIONS.md). Dependencies and pins: [DEPENDENCY_REVIEW.md](DEPENDENCY_REVIEW.md). Threats and controls: [SECURITY_MODEL.md](SECURITY_MODEL.md). Operation (`nsf web search`, `fetch`, `health`, `daemon`): see RUNBOOK.

Everything here is off until the owner enables web access (`state.require_enabled`, ADR-021). A fresh individual contacts no network service.

## 1. Two stages, two trust levels

| Stage | Module | Job | Network path |
|---|---|---|---|
| Discovery | `search.py`, `providers/*` | Turn a `QueryPlan` into a ranked list of candidate URLs and records | open-webSearch daemon (loopback) and structured APIs through `SafeFetcher(purpose="api")` |
| Acquisition | `net.py`, `cache.py`, `extract.py`, `browser.py` | Fetch, validate and parse the candidates into `Document`s | `SafeFetcher` only |

Search results (titles, snippets, URLs) and page content are untrusted data at every step.

## 2. Providers and routing

`providers.build(config, fetcher)` instantiates the names in `WebConfig.providers`, in order. Unknown names are ignored (`providers.unknown()` lists them).

| Provider | Serves query kinds | Takes only | Endpoint |
|---|---|---|---|
| `openwebsearch` | `web`, `news` | any text | local daemon `POST /search` |
| `wikipedia` | `encyclopedia`, `web` | any text | `https://<lang>.wikipedia.org/w/api.php` (`lang` from `Query.language`) |
| `arxiv` | `academic` | any text | `https://export.arxiv.org/api/query` |
| `openalex` | `academic` | any text | `https://api.openalex.org/works` |
| `crossref` | `academic` | any text | `https://api.crossref.org/works` |
| `pubmed` | `academic` | any text | NCBI E-utilities `esearch.fcgi` then `esummary.fcgi` |
| `pypi` | `technical` | `pypi:<name>[==ver]` or a bare package name | `https://pypi.org/pypi/<name>/json` |
| `npm` | `technical` | `npm:<name>` forms | `https://registry.npmjs.org/<name>` |
| `osv` | `technical` | `osv:<ecosystem>:<name>[@ver]` | `POST https://api.osv.dev/v1/query` |
| `github` | `technical` | `github:<owner>/<repo>` | `https://api.github.com/repos/<o>/<r>/releases` |

Package, repository and ecosystem names that end up in a URL path are checked against strict patterns first; free text is URL-encoded (`providers/structured.py` docstring). Providers that take only one query form implement `accepts(query)`; the registry does not call them for other text.

`FeedProvider` (RSS/Atom, `purpose="feed"`) and `CorpusProvider` (offline BM25 over a fixed list, for tests and the bench) exist but are **not** in `providers.KNOWN`; `providers.build` never creates them. A feed is therefore reached only when a search result URL is itself a feed (section 4).

### Routing rule (`ProviderRegistry._candidates`, `search`)

1. Every provider whose `kinds` contains `query.kind` and whose `accepts(query)` is true (or absent) runs, in parallel.
2. If none qualifies and the kind is not `web`, the providers serving `web` run instead.
3. If a non-`web` query returns nothing from its specialists, the `web` providers not yet tried run once more for that query (test `test_fallback_to_web_when_specialists_fail`).

Consequence: every general `web` query also reaches Wikipedia, because `wikipedia` declares `web`. Queries are processed one after another; the providers for one query run concurrently on up to `budget.max_concurrency` threads. Each provider returns at most `per_query` results (8, clamped to 1-20).

`intent.plan` chooses the kinds (`assess` sets `academic`, `technical`, `encyclopedia`, `news`, `web`); see RESEARCH_ORCHESTRATION.

## 3. open-webSearch daemon

`providers/openwebsearch.py` talks to the pinned open-webSearch 2.2.0 REST daemon (`deploy/open-websearch`, `npm ci --ignore-scripts`).

| Rule | Detail |
|---|---|
| Address | `loopback_base()` accepts only `http://127.0.0.1`, `http://localhost` or `http://[::1]`, a bare origin, no credentials. Anything else raises `NotLoopback` (`test_non_loopback_urls_refused`). `state.set_config` applies the same rule to `openwebsearch_url`. |
| Bypass of SafeFetcher | Deliberate: SafeFetcher refuses loopback. The client uses `urllib` with an empty `ProxyHandler`, so shell proxy variables are ignored. Response cap 4,000,000 bytes. |
| Endpoints used | `GET /health`, `GET /status`, `POST /search` |
| Endpoints not used | `/fetch-*` (no DNS pinning), `/mcp` and SSE (bind 0.0.0.0, no auth). Playwright mode is not installed. |
| Request body | `{"query", "limit", "engines": [one engine], "searchMode": "request"}`. Query is whitespace-collapsed and cut at 512 characters; `Query.site` becomes ` site:<domain>`; `limit` is clamped to 20. |
| Result handling | HTML in titles and snippets is stripped; DuckDuckGo `/l/?uddg=` redirect links are unwrapped; non-http(s) and over-long URLs are dropped; results older than `Query.since` are dropped (undated results stay). |
| Daemon process | `Daemon` binds `127.0.0.1:3210` (refuses a non-loopback host), logs to `<state_dir>/openwebsearch.log`, records a pid file, and `stop()` ends only the process it started or the pid whose command line is this daemon's entry script (`test_daemon_stop_never_kills_unrelated_pid`). |

### Engine health and fallback

The daemon answers HTTP 200 with `partialFailures` when an engine fails and does no fallback itself. So the client calls engines **one at a time** in `WebConfig.engines` order (default `duckduckgo, brave, startpage, bing`), skips an engine whose circuit is open, and stops once `limit` results are gathered. Each engine call costs one `usage.requests`. Per-engine attempts are returned as `last_attempts` and appear in `SearchOutcome.attempts`.

Outcome codes: an engine error or a `partialFailures` entry with no results is a failure for that engine. If every tried engine failed, `ProviderError(code="engines_failed")` is raised. An unreachable daemon raises `daemon_unreachable` at once and does not count against any engine. If every engine circuit is open, `circuit_open` is raised.

### Circuit breakers

One `Breaker` per provider and per engine: 3 consecutive failures open the circuit for 300 s; then one trial call is allowed (`half_open`); success closes it, failure reopens it (`test_breaker_transitions_with_fake_clock`, `test_half_open_failure_reopens_and_allows_one_trial`). Errors that say nothing about provider health never move a breaker: `budget_exhausted`, `disabled`, `blocked_policy`, `blocked_robots`, `circuit_open`, `not_applicable` (`test_non_health_errors_do_not_move_breaker`). State is saved to `web_strategies` (name `registry`) after each task and loaded by `services.build` (`test_status_dump_load_round_trip_with_engines`).

### Empty-result rule (from `search.py`)

An empty list is a soft failure only when all of these hold: the provider declares `general_web` (open-webSearch only), the query kind is `web` or `news`, and another provider answered the same query with results. Inside open-webSearch the same test applies per engine: an engine that returns nothing counts as failed only if a later engine returned results. If nobody found anything, the query is obscure and the empty answer is health-neutral. For specialist providers an empty list means "nothing matches" and counts as a success (`test_empty_answer_rule`, `test_all_empty_is_neutral`).

## 4. Fusion, deduplication, canonical URLs

`search.fuse` merges all ranked lists (queries x providers x engines) with reciprocal rank fusion, `score = sum 1/(60 + rank)`, and groups duplicates by canonical URL (`test_rrf_scores_and_order`, `test_fusion_across_queries_rewards_repeated_hits`).

- Canonical URL: lower-case scheme and host, no `www.`, no default port, no userinfo, no fragment, no `utm_*`, `fbclid`, `gclid`; remaining parameters sorted; trailing slash removed. `extract.canonical_url` (used once the parser is installed) additionally drops `dclid`, `gbraid`, `wbraid`, `msclkid`, `yclid`, `igshid`, `ref`, `ref_src`, `_ga`, `_gl`, `mkt_tok`, `spm`, `mc_*`.
- The kept copy is the one with the richest `metadata`; missing fields are filled from the others; `metadata["found_by"]` lists every `provider[:engine]` that returned the URL (`test_dedupe_by_canonical_url_keeps_structured_metadata`).
- Rank, provider score and citation counts only order candidates. They are never evidence (see EVIDENCE_AND_PROVENANCE).

## 5. The acquisition ladder as implemented

`orchestrator._Run.select` and `read` choose, in this order of priority, at most `budget.max_pages` documents:

| Step | When chosen | What happens | Document `retrieval_mode` |
|---|---|---|---|
| 1. URL named in the question | `assess["urls"]` | Fetched directly as a page; not searched | `live` |
| 2. Structured API record | Result from a structured provider with non-empty `metadata` | **Nothing is fetched.** The record (title, snippet, abstract/summary metadata) becomes a `kind="api"` document; one page is charged | `api` |
| 3. HTTP page | Any other result | `SafeFetcher.fetch(purpose="page")`, then `extract.extract` | `live` (or `cache` on a 304) |
| 4. Feed | A result URL that serves RSS, Atom or RDF | Handled by the same fetch; `extract` parses it into a `kind="feed"` document (item titles and summaries) | `live` |
| 5. Extraction | Every fetched body | HTML, PDF, XML or JSON/text to a `Document`, then `safety.quarantine` | as above |
| 6. JavaScript rendering | Document has `flags["js_suspected"]` **and** `usage.browser_sessions < budget.max_browser_sessions` **and** `BrowserRenderer.available()` | Re-load in sandboxed Chromium (section 9); the rendered DOM is extracted and quarantined, replacing the first result. If unavailable, a `decision` event records why | `rendered` |
| 7. Link following | Mode `deep_research` and `budget.max_depth >= 1` | Up to `MAX_FOLLOW = 2` same-registrable-domain links from the first non-API document's main-content links, fetched with `purpose="crawl"`, while pages remain | `live` |

Notes that matter:

- A failed fetch is recorded as a `FetchError` in the task's failures and an `error` event; reading continues with the next candidate. `budget_exhausted` ends reading.
- `crawl.py` (breadth-first walk, sitemaps from `robots.txt` or `/sitemap.xml`, `nofollow` respected, binary suffixes skipped) and `crawl.discover_feeds` are tested modules (`tests/webcortex/test_crawl.py`) that the orchestrator does **not** call. Step 7 is the orchestrator's own two-link `follow()`. ARCHITECTURE §4.1 lists `crawl.crawl` as the link-following mechanism; the running code does not use it.
- Cached bodies are never served in place of a network request. A cache body is used only when the server answers 304 to a conditional request, or when `orchestrator.resume` re-reads a document read before an interruption (then `retrieval_mode` is `cache` and the report says `live: false` if nothing else was live).

## 6. SafeFetcher

`net.SafeFetcher` (stdlib `http.client`; no `requests`, `httpx` or Scrapling fetchers, ADR-024) is the only path to the public web. Each property has a test in `tests/webcortex/test_net.py`.

### URL and address policy

| Control | Rule | Test |
|---|---|---|
| Scheme, port | `http` and `https`; ports 80, 443, 8080, 8443 | `test_check_url_refuses` |
| URL form | At most 2048 characters; no whitespace or control characters; no credentials; no backslash in host | `test_check_url_refuses` |
| Host form | IDNA-encodable; `localhost`, `*.localhost`, `*.localdomain`, `*.local`, `*.internal`, `*.home.arpa` refused; non-canonical numeric hosts (`0x7f000001`, `2130706433`, `127.1`) refused | `test_special_hosts_refused_without_connecting`, `test_literal_ip_urls_never_connect` |
| Deny list | `WebConfig.deny_domains` and their subdomains | `test_check_url_normalises_and_denies_domains` |
| Forbidden addresses | Loopback, RFC 1918, link-local (including 169.254.169.254 and `fd00:ec2::254`), CGNAT 100.64.0.0/10, multicast, reserved, unspecified, documentation and benchmark ranges, `0.0.0.0/8`, `240.0.0.0/4`, IPv6 ULA `fc00::/7`, site-local, `::1`; IPv6 forms embedding a refused IPv4 address (IPv4-mapped, IPv4-compatible, 6to4, Teredo, NAT64) | `test_forbidden_addresses`, `test_public_addresses` |
| Mixed DNS answers | If **any** address for a host is forbidden, the host is refused | `test_mixed_answers_refused` |

### Connection

- **DNS pinning.** A host is resolved once per fetch (shared by its `robots.txt` request and by redirect hops back to it). The socket connects to a validated address and is never re-resolved, so a rebinding answer cannot swap the target. TLS (minimum 1.2, default verification) checks the certificate against the original host name; the `Host` header is the original host (`test_dns_rebinding_pins_the_validated_address`, `test_set_cookie_not_kept_and_host_header_original`).
- **Redirects** are followed manually, at most 5. Every hop is re-checked for URL policy, address policy and robots. `https` to `http` downgrades are refused; 303 (and 301/302 after POST) become GET; credential headers are dropped when the origin changes (`test_redirects_to_internal_targets_refused`, `test_redirect_loop_and_limit`, `test_https_to_http_downgrade_refused`).
- **Methods.** GET, HEAD, and POST only for `purpose="api"` (`test_method_and_purpose_restrictions`). Caller headers cannot override `Host`, `User-Agent`, `Accept-Encoding`, validators and similar. `Set-Cookie` is discarded; no cookies are ever sent.

### Limits

| Limit | Default | Behaviour |
|---|---|---|
| Wall-clock per fetch | 15 s (`NetPolicy.timeout`) | `timeout` error (`test_slow_route_times_out`) |
| Body size | `WebConfig.max_document_bytes`, 5,000,000 | Pages and crawls are **truncated** and flagged; API and feed responses raise `too_large` (`test_size_limit_truncates_pages_and_rejects_api`) |
| Content type | Allow-list: `text/html`, `application/xhtml+xml`, `text/plain`, `application/json`, `application/*+json`, `application/xml`, `text/xml`, Atom/RSS, `application/pdf` | Checked before the body is read; else `unsupported_type` (`test_disallowed_content_type`) |
| Decompression | gzip and deflate decoded under the same size cap | A bomb stops at the cap; corrupt data is `decode_error`; other encodings are refused (`test_gzip_bomb_is_capped`) |
| Status | 2xx accepted | Other final statuses raise `http_error` carrying the status |
| Budget | One request charged before every network call, including `robots.txt` and each redirect hop; body bytes charged after reading | `BudgetExceeded` is raised before the limit would be passed (`test_budget_counts_robots_redirects_and_bytes`, `test_byte_budget_never_exceeded`) |

### Politeness

- **robots.txt** is obeyed for `page`, `feed` and `crawl`; `api` and `robots` purposes skip it (documented public APIs). Rules are evaluated with Python's `urllib.robotparser` against the full user agent. Entries are held 24 h in memory and in the public cache; the file is capped at 512 KiB.

  | `robots.txt` answer | Effect |
  |---|---|
  | 2xx | Parsed and used (`test_robots_disallow_blocks_without_requesting`, `test_robots_specific_agent_rules`) |
  | 4xx | Everything allowed; stored as an empty file |
  | 5xx | Everything on that host **disallowed**, in this process only (not written to the cache) |
  | DNS, connect, TLS or policy failure | Everything disallowed, in this process only |
  | Timeout or `rate_limited` | The fetch fails; no verdict is recorded |

  A disallowed URL raises `blocked_robots` without any request to the page (`test_robots_404_allows_and_500_disallows`). **Limit:** `urllib.robotparser` ignores `*` and `$` pattern syntax inside paths. On the project's Python 3.12 environment, `Disallow: /*.pdf$` did not block `/x.pdf`. See SECURITY_MODEL §9.
- **Throttle.** A request slot per host at least `min_domain_interval` seconds (default 1.0) after the previous one. `Crawl-delay` raises the interval, capped at 60 s (`test_crawl_delay_and_domain_interval`). The interval is one value for every host: there is no per-host rule, so arXiv's request for one call per 3 s is met only if the owner raises `min_domain_interval`. State is per process: two processes (for example the worker and a manual fetch) do not coordinate.
- **Retry-After.** HTTP 429 sets a host backoff from `Retry-After` (seconds or HTTP date, capped at 6 h; 60 s if absent). HTTP 503 does so only when it carries `Retry-After`. Any request to the host inside the backoff raises `rate_limited` with `retry_after` and touches no network (`test_retry_after_seconds_sets_backoff`, `test_retry_after_http_date_on_503`).
- **Conditional GET.** For `page`, `feed` and `crawl` GETs, if the cache holds an ETag or Last-Modified and the body, the request sends `If-None-Match` / `If-Modified-Since`. A 304 returns the cached body with `retrieval_mode="cache"` (`test_conditional_get_returns_cached_body`).
- **Coalescing.** Identical concurrent GETs (same URL, purpose, conditional flag, extra headers) share one network call; followers receive a copy of the leader's result or error. Only the leader's budget is charged (`test_identical_concurrent_gets_are_coalesced`).
- **User agent.** `NeuroSciFi-WebCortex/0.6 (research agent; honours robots.txt)`. No contact address, no browser impersonation, no proxy rotation.

### What is cached

| Cached in `<db stem>.webcache.sqlite` | Not cached |
|---|---|
| 2xx GET bodies for `page`, `feed`, `crawl` that were not truncated (keyed by URL; bodies by SHA-256) | `purpose="api"` responses (their URLs embed queries; `test_api_responses_are_not_cached`) |
| `robots.txt` text and fetch time | Truncated bodies, HEAD and POST responses |
| ETag and Last-Modified validators | Any query, task objective or claim |

`PublicCache`: mode 0600, WAL, `secure_delete` on. `prune(now, max_bytes, max_age_seconds)` removes rows older than the age, then least recently fetched bodies until under the size; `forget_url` and `forget_domain` delete rows and orphaned bodies and truncate the WAL (`tests/webcortex/test_cache.py`). The cache is excluded from checkpoints (ADR-023). `WebConfig.cache_max_bytes` and `cache_max_age_days` are validated and stored, but no committed code calls `prune`; only the interface lanes can.

## 7. Extraction (`extract.py`)

| Aspect | Behaviour | Test |
|---|---|---|
| Parser | `scrapling.parser.Selector(..., huge_tree=False)` (bounded tree, no network, no script execution); Markdown by `markdownify` | `test_article_metadata_and_main_text` |
| Hidden content | Removed **before** text is taken and counted in `flags["hidden_removed"]`: `hidden`, `aria-hidden="true"`, inline `display:none` / `visibility:hidden` / `opacity:0` / `font-size:0` / far off-screen positions, hiding class names (`hidden`, `sr-only`, `d-none`, ...), `<template>`, `<input type=hidden>`, zero-width and bidi-control characters | `test_hidden_injection_removed_and_counted` |
| Boilerplate | `script, style, noscript, svg, nav, header, footer, aside, form, iframe, ...` removed, counted in `flags["boilerplate_removed"]` | |
| Main content | `<article>`, `<main>` or `role=main` if it holds at least 30 % of body text or 500 characters; else the parent with the most paragraph text (grandparent counts half), accepted if it holds at least 20 % of the body; else the body | `test_density_heuristic_without_article` |
| Trafilatura | Optional extra. `engine="trafilatura"` always uses it; `"auto"` (default) uses it only when the heuristic text is under 300 characters and trafilatura's is longer. It sees the already-cleaned HTML. Absent package: silent fallback | `test_trafilatura_engine_or_fallback` |
| JS shell | `flags["js_suspected"]` when visible text is under 200 characters and there are scripts, an empty `#root`/`#app`/`#__next`-style mount, or a `<noscript>` asking for JavaScript | `test_js_shell_detection` |
| Canonical link | Honoured only if absolute http(s) and on the same registrable domain; an off-site canonical is recorded (`canonical_offsite`) and ignored | `test_offsite_canonical_ignored` |
| PDF | `pypdf`, at most 30 pages and 500,000 characters; encrypted or unreadable files raise `decode_error` | `test_pdf_text_and_metadata`, `test_pdf_encrypted_or_garbage_is_decode_error` |
| Feeds, sitemaps | `xml.etree` after refusing any `<!DOCTYPE` / `<!ENTITY` and non-ASCII-compatible encodings (entity expansion and XXE cannot happen); 500 feed items, 50,000 sitemap URLs at most | `test_hostile_or_broken_xml_refused`, `test_parse_rss_atom_rdf`, `test_parse_sitemap_and_index` |
| Other types | JSON is pretty-printed; plain text keeps zero-width characters removed | `test_extract_dispatch_json_text_and_feed` |
| Caps | text 500,000 characters, Markdown 500,000, 200 links | |

**Publication date**, in priority order: JSON-LD `datePublished`; a meta tag (`article:published_time`, `citation_publication_date`, `citation_date`, Dublin Core `dc.date*` / `dcterms.*`, `datepublished`, `pubdate`, ...); `<time datetime>` (prefers `itemprop=datePublished` and `pubdate`); HTTP `Last-Modified` as a last resort. `metadata["date_source"]` names the one used (`json-ld`, `meta:<name>`, `time-element`, `http-last-modified`, `pdf-creation-date`). JSON-LD and meta modification dates are stored in `modified_at` and never stand in for publication; only the HTTP `Last-Modified` header is used, and only when nothing else dated the page (`test_last_modified_header_is_the_last_resort`).

Plain-text and non-HTML documents get only zero-width removal; they are screened by `safety.quarantine` and again by the claim extractor.

## 8. Health checks and degraded operation

| Question | Where answered |
|---|---|
| Is the daemon up, which engines are allowed? | `OpenWebSearchProvider.health()`: `GET /health`, `GET /status`, 5 s limit each; returns `reachable`, `version`, `engines_allowed`, `search_mode`, per-engine breaker snapshot |
| Which providers and engines are open or half-open? | `ProviderRegistry.status()`: state, successes, failures, last error, p50 latency; also lists persisted state for providers no longer configured |
| Per-host request, error, byte, backoff, robots-block, coalesced and cache-hit counts | `SafeFetcher.stats()` |
| Is rendering available? | `BrowserRenderer.available()` returns `(bool, reason)`: Playwright installed, Chromium found (`NSF_CHROMIUM`, `/usr/bin/chromium`, PATH, or Playwright's bundle) |

Degraded behaviour, all recorded rather than hidden:

- An engine fails: the next engine is tried; its failure appears in `attempts` and in the report's `limitations` (for example `brave: rate_limited`).
- The daemon is down: open-webSearch fails; structured providers still answer for `academic`, `technical` and `encyclopedia` queries. Plain `web` queries then reach only Wikipedia. `intent.refine` makes one broader attempt when fewer than 3 results come back or every provider failed.
- A page fails (policy, robots, timeout, size, type): it is skipped and listed under `limitations`; the task can end `partial`.
- Rendering is unavailable: the task continues with the unrendered text and records the reason.

Live behaviour of the engines on 2026-11-20 (duckduckgo answered, brave 429, startpage empty without error, bing redirect failure) is in DEPENDENCY_REVIEW §3.

## 9. Browser rendering sandbox (`browser.py`, ADR-030)

Off by default (`browser_rendering`); needs the `web-render` extra and a Chromium binary. A task may render at most `budget.max_browser_sessions` pages (clamped to the ceiling; 0 when rendering is off; at least 1 when it is on and the caller did not choose a number).

- The page body is first fetched with `SafeFetcher(purpose="page")`, so URL policy and robots apply before Chromium starts (`test_render_refuses_internal_targets_before_launch`).
- Fresh context per render, closed afterwards: no profile, `accept_downloads=False`, `permissions=[]`, `service_workers="block"`, Chromium sandbox on (`chromium_sandbox=True`), no extensions, background networking off.
- `context.route("**/*")` intercepts **every** request. Only GET and HEAD over http(s) are served, and only through `SafeFetcher(purpose="page")`, so SSRF, DNS pinning, robots, throttle, size, type and budget rules cover each sub-resource. Images, media, fonts, manifests and pings are aborted (`test_render_executes_js_through_safe_fetcher`).
- Defence in depth for traffic that bypasses routing: Chromium resolves every name to NOTFOUND (`--host-resolver-rules=MAP * ~NOTFOUND`), a dead proxy is configured, WebRTC may not use non-proxied UDP. `WebSocket`, `RTCPeerConnection` and `EventSource` constructors throw inside every frame.
- One wall-clock timeout per render (20 s); rendered body capped at the fetcher's `max_bytes`; `last_events` keeps each sub-request outcome (URL without query) for audit.

## 10. Configuration knobs (`config.WebConfig`)

Stored as JSON in `meta` key `webcortex`; validated by `state.set_config`. Constants, not settings: robots.txt compliance, the address policy, read-only default.

| Field | Default | Meaning |
|---|---|---|
| `enabled` | false | Master switch for every network path |
| `autonomous` | false | Scheduler may start refresh and curiosity research |
| `pocket_research` | false | Paired Pocket devices may submit and read tasks |
| `browser_rendering` | false | Permit sandboxed JavaScript rendering |
| `openwebsearch_url` | `http://127.0.0.1:3210` | Loopback daemon; validated as plain-HTTP loopback |
| `engines` | duckduckgo, brave, startpage, bing | Engine order, tried one at a time |
| `providers` | openwebsearch, wikipedia, arxiv, openalex, crossref, pubmed, pypi, npm, osv, github | Providers built, in order |
| `budget_ceiling` | queries 16, requests 200, pages 30, bytes 60,000,000, seconds 600, depth 2, browser sessions 2, concurrency 6, context tokens 6000 | Upper bound for any task budget |
| `daily_autonomous` | tasks 12, requests 400, bytes 80,000,000 | 24 h cap for curiosity and refresh tasks |
| `min_domain_interval` | 1.0 s (0-3600) | Minimum gap between requests to one host |
| `max_document_bytes` | 5,000,000 (min 1024) | Body cap per fetch |
| `cache_max_bytes` | 512 MiB | Public-cache size bound for `prune` |
| `cache_max_age_days` | 30 | Public-cache age bound for `prune` |
| `deny_domains` | none | Never contacted (subdomains too) |
| `private_terms` | none | Extra strings that may never leave in a query |
| `auto_provisional_min_sources` | 2 (1-50) | Independent sources before a claim may become provisional |
| `auto_provisional_min_confidence` | 0.75 | Confidence floor for the same |
| `freshness_ttl` | none | Per-class TTL overrides in seconds |

## 11. How to verify

```sh
pytest -q tests/webcortex/test_net.py tests/webcortex/test_extract.py tests/webcortex/test_cache.py
pytest -q tests/webcortex/test_search.py tests/webcortex/test_openwebsearch.py tests/webcortex/test_providers.py
pytest -q tests/webcortex/test_crawl.py tests/webcortex/test_browser.py
NSF_LIVE=1 pytest -q -k live tests/webcortex     # real internet; optional
```

At `5f09ac3`, `pytest -q tests/webcortex` reported 609 passed and 6 skipped (the six are the `live` tests).

## 12. Limits stated plainly

- Search quality depends on unofficial engine scrapers that fail independently.
- Rendering depends on a local Chromium and is off by default.
- Per-host throttle, backoff and robots state live in one process.
- Robots wildcard rules are not honoured (stdlib parser).
- Sites that require anti-bot circumvention, logins or payment are reported unavailable; none is attempted.
