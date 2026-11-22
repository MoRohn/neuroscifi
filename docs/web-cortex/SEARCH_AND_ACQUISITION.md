# Web Cortex: search and acquisition

Status: describes the working tree on top of `64da95b`, including the UAT fixes not yet committed (`src/neuroscifi/webcortex/`; the remediation commit changed robots, throttling, caching and feeds, the UAT fixes changed discovery, Wikipedia, feed relevance and daemon state). Where a statement rests on a test, the test name is given. Contracts: [ARCHITECTURE.md](ARCHITECTURE.md) §3 and §4.1-4.2. Why these choices: ADR-022 to ADR-025 and ADR-030 in [ARCHITECTURAL_DECISIONS.md](../architecture/ARCHITECTURAL_DECISIONS.md). Dependencies and pins: [DEPENDENCY_REVIEW.md](DEPENDENCY_REVIEW.md). Threats and controls: [SECURITY_MODEL.md](SECURITY_MODEL.md). Operation (`nsf web search`, `fetch`, `health`, `daemon`, `feeds`, `cache`): see RUNBOOK. Console `/api/web/*`, Pocket research actions and the MCP server are specified in API_CONTRACTS §8; they submit and read tasks and never fetch.

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
| `wikipedia` | `encyclopedia`, `web` | any text | `https://<lang>.wikipedia.org/w/api.php` (`lang` from `Query.language`). Search hits carry `snippet_only: True`: a highlighted fragment is a discovery hint and yields no claims. `WikipediaProvider.article(title)` returns the plain-text lead (`{title, extract, last_edited, url}`, at most 20,000 characters, redirects followed; `not_found` or `bad_response` errors) and is what a task reads for a hit (`test_wikipedia_hits_are_snippet_only_and_article_returns_the_lead`) |
| `arxiv` | `academic` | any text | `https://export.arxiv.org/api/query` |
| `openalex` | `academic` | any text | `https://api.openalex.org/works` |
| `crossref` | `academic` | any text | `https://api.crossref.org/works` |
| `pubmed` | `academic` | any text | NCBI E-utilities `esearch.fcgi` then `esummary.fcgi` |
| `pypi` | `technical` | `pypi:<name>[==ver]` or a bare package name. A plain name in a question with a release cue ("latest release of numpy") is turned into `pypi:` and `npm:` lookups by `intent.plan` (RESEARCH_ORCHESTRATION §2) | `https://pypi.org/pypi/<name>/json` |
| `npm` | `technical` | `npm:<name>` forms | `https://registry.npmjs.org/<name>` |
| `osv` | `technical` | `osv:<ecosystem>:<name>[@ver]` | `POST https://api.osv.dev/v1/query` |
| `github` | `technical` | `github:<owner>/<repo>` | `https://api.github.com/repos/<o>/<r>/releases` |

Package, repository and ecosystem names that end up in a URL path are checked against strict patterns first; free text is URL-encoded (`providers/structured.py` docstring). Providers that take only one query form implement `accepts(query)`; the registry does not call them for other text.

`FeedProvider` (RSS/Atom, `purpose="feed"`) is built from the owner's feed list, not from `WebConfig.providers`: `nsf web feeds add|remove|list` stores up to 50 http(s) URLs under `meta` key `webcortex.feeds` (`state.get_feeds`, `set_feeds`), and `providers.build(config, fetcher, feeds=..., clock=...)` appends a `FeedProvider` when the list is non-empty. `"feeds"` is in `providers.KNOWN`; naming it in `WebConfig.providers` places it at that position instead. A provider instance fetches each feed at most once per 300 s (`MEMO_SECONDS`), not once per planned query. `FeedProvider` serves `news` and `web` queries, so a task with such queries searches the feeds (items are filtered by the query), and monitors read feed items before web results (RESEARCH_ORCHESTRATION §3, §7). Tests: `test_build_adds_the_feed_provider_only_when_feeds_exist`, `test_feed_provider_ranks_filters_and_tolerates_bad_feeds`.

`CorpusProvider` (offline BM25 over a fixed list, for tests and the bench) is still not in `KNOWN`. A search result whose URL is itself a feed is also parsed as a feed (section 5).

**Feed relevance.** One shared word is not enough for a feed item to be a result, so an owner's feeds do not pollute unrelated research. Query terms are the question's topical words (stopwords, cue and temporal words and years dropped), compared as 5-letter stems. An item is kept only when it matches at least `min(2, number of terms)` distinct terms, **or** every word of every named entity in the query, **and** its BM25 score over the items of this search reaches `FEED_BM25_FLOOR` (1.0; the score is smoothed with `FEED_BM25_BACKGROUND` = 10 absent documents so a tiny feed does not inflate rare-word weights). Each feed is one entry in `last_attempts` ({`engine`: feed URL, `ok`, `n`, `error`, `latency`}), so a feed that failed while others answered is listed as a failed attempt, never dropped silently (`test_feed_items_need_two_query_terms_or_every_entity`, `test_feed_partial_failures_are_recorded_as_attempts`).

### Routing rule (`ProviderRegistry._candidates`, `search`)

1. Every provider whose `kinds` contains `query.kind` and whose `accepts(query)` is true (or absent) runs, in parallel.
2. If none qualifies and the kind is not `web`, the providers serving `web` run instead.
3. If a non-`web` query returns nothing from its specialists, the `web` providers not yet tried run once more for that query (test `test_fallback_to_web_when_specialists_fail`).

A **registry lookup** (`pypi:`, `npm:`, `osv:`, `github:`; `search.REGISTRY_QUERY`) is never re-sent to general web search when nobody answers: "npm:numpy" is not a web search.

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
| Port in use | `start()` refuses a port some other process already holds, whether or not it speaks HTTP: `ProviderError` code `port_in_use` naming the port (a listening socket in `/proc/net/tcp*`, else a TCP connection being accepted). A daemon NeuroSciFi started that is alive and owns the port but is not answering yet returns "already running" instead (`test_port_held_by_a_non_daemon_process_is_a_clear_port_in_use_error`). |
| Ownership | `status()` judges ownership the same way from any process: `started_here` (this object started it) or `owned_by_pid_file` (the pid file's pid, its `/proc` start time and the entry script on its command line all match); `owned` is either; `alive` says the process exists; `port_answering` says something answers `/status`; `port_in_use` says something accepts connections. |
| State files | The state directory is mode 0700; the pid and log files are 0600 whatever the umask or the file's earlier mode, because the log can contain queries (`test_daemon_state_files_are_private`). |

### Engine health and fallback

The daemon answers HTTP 200 with `partialFailures` when an engine fails and does no fallback itself. So the client calls engines **one at a time** in `WebConfig.engines` order (default `duckduckgo, brave, startpage, bing`), skips an engine whose circuit is open, and stops once `limit` results are gathered. Each engine call costs one `usage.requests`. Per-engine attempts are returned as `last_attempts` and appear in `SearchOutcome.attempts`.

Outcome codes: an engine error or a `partialFailures` entry with no results is a failure for that engine. If every tried engine failed, `ProviderError(code="engines_failed")` is raised. An unreachable daemon raises `daemon_unreachable` at once and does not count against any engine. If every engine circuit is open, `circuit_open` is raised.

### Circuit breakers

One `Breaker` per provider and per engine: 3 consecutive failures open the circuit for 300 s; then one trial call is allowed (`half_open`); success closes it, failure reopens it (`test_breaker_transitions_with_fake_clock`, `test_half_open_failure_reopens_and_allows_one_trial`). Errors that say nothing about provider health never move a breaker: `budget_exhausted`, `disabled`, `blocked_policy`, `blocked_robots`, `circuit_open`, `not_applicable` (`test_non_health_errors_do_not_move_breaker`). State is saved to `web_strategies` (name `registry`) after each task and loaded by `services.build` (`test_status_dump_load_round_trip_with_engines`).

### Empty-result rule (from `search.py`)

An empty list is a soft failure only when all of these hold: the provider declares `general_web` (open-webSearch only), the query kind is `web` or `news`, and another provider answered the same query with results. Inside open-webSearch the same test applies per engine: an engine that returns nothing counts as failed only if a later engine returned results. If nobody found anything, the query is obscure and the empty answer is health-neutral. For specialist providers an empty list means "nothing matches" and counts as a success (`test_empty_answer_rule`, `test_all_empty_is_neutral`). A registry's 404 for a name it does not know (PyPI and npm are both asked for a plainly named package) is the same soft miss: it is not a failure, it does not open a breaker and it is not listed in `failures`; only a general provider's empty answer is (`test_registry_404_is_a_soft_miss_and_never_falls_back_to_web_search`).

## 4. Fusion, deduplication, canonical URLs

`search.fuse` merges all ranked lists (queries x providers x engines) with reciprocal rank fusion, `score = sum 1/(60 + rank)`, and groups duplicates by canonical URL (`test_rrf_scores_and_order`, `test_fusion_across_queries_rewards_repeated_hits`).

- Canonical URL: lower-case scheme and host, no `www.`, no default port, no userinfo, no fragment, no `utm_*`, `fbclid`, `gclid`; remaining parameters sorted; trailing slash removed. `extract.canonical_url` (used once the parser is installed) additionally drops `dclid`, `gbraid`, `wbraid`, `msclkid`, `yclid`, `igshid`, `ref`, `ref_src`, `_ga`, `_gl`, `mkt_tok`, `spm`, `mc_*`.
- The kept copy is the one with the richest `metadata`; missing fields are filled from the others; `metadata["found_by"]` lists every `provider[:engine]` that returned the URL (`test_dedupe_by_canonical_url_keeps_structured_metadata`).
- Rank, provider score and citation counts only order candidates. They are never evidence (see EVIDENCE_AND_PROVENANCE).

## 5. The acquisition ladder as implemented

`orchestrator._Run.select` ranks up to `3 x budget.max_pages` candidates in the order below; `read` works down that list until `budget.max_pages` documents have **succeeded**:

| Step | When chosen | What happens | Document `retrieval_mode` |
|---|---|---|---|
| 1. URL named in the question | `assess["urls"]` | Fetched directly as a page; not searched | `live` |
| 2. Structured API record | Result from a structured provider with non-empty `metadata` | **Nothing is fetched.** The record (title, snippet, abstract/summary metadata) becomes a `kind="api"` document; one page is charged | `api` |
| 3. HTTP page | Any other result | `SafeFetcher.fetch(purpose="page")`, then `extract.extract` | `live` (also on a 304: the origin just confirmed the cached body, header `x-nsf-revalidated: 304`) |
| 4. Feed | A result URL that serves RSS, Atom or RDF | Handled by the same fetch; `extract` parses it into a `kind="feed"` document (item titles and summaries) | `live` |
| 5. Extraction | Every fetched body | HTML, PDF, XML or JSON/text to a `Document`, then `safety.quarantine` | as above |
| 6. JavaScript rendering | Document has `flags["js_suspected"]`, or (news / time-sensitive question: a `news` query kind or the breaking / volatile freshness class) a scripted HTML page gave under `NEWS_RENDER_MIN_TEXT` (500) characters of static text (`thin_news_text`) or no publication date (`undated_news`); **and** `usage.browser_sessions < budget.max_browser_sessions`, the resource scheduler is not at critical memory pressure, the owner's switches allow it (`browser_rendering`; or `news_rendering`, on by default, for a news question a person asked), `BrowserRenderer.available()`, at least `RENDER_MIN_SECONDS` (10 s) of the time budget left and the day's render cap (`daily_renders`, 40) not reached. For news questions these pages are read **after** the dated, in-window static pages (static-first, ADR-040) | Re-load in sandboxed Chromium (section 9) with its own sub-request allowance (`budget.max_render_requests`, 100), never the task's requests; open shadow roots are inlined into the rendered DOM; the result is extracted and quarantined, replacing the first result. The `render` event names the reason (`js_shell` / `thin_news_text` / `undated_news`), `subrequests`, `allowance` and `blocked`. If not rendered, a `decision` event records why | `rendered` |
| 7. Link following | Mode `deep_research` and `budget.max_depth >= 1` | Up to `MAX_FOLLOW = 2` same-registrable-domain links from the first non-API document's main-content links, fetched with `purpose="crawl"`, while pages remain | `live` |

Notes that matter:

- A failed fetch (policy, robots, error, refused type) is recorded as a `FetchError` in the task's failures and an `error` event, and reading continues with the next candidate. It costs requests and bytes, not a page slot: the request, byte and time budgets bound the attempts. `budget_exhausted` ends reading. In `scientific` and `deep_research` modes `ceil(max_pages / 3)` slots stay reserved for web pages; API records take a reserved slot only after every web candidate was tried (`web_reserve`). For a monitor's task, feed items rank with the structured records (step 2 priority). Details: RESEARCH_ORCHESTRATION §3. The task lease is renewed after every fetch.
- `crawl.py` (breadth-first walk, sitemaps from `robots.txt` or `/sitemap.xml`, `nofollow` respected, binary suffixes skipped) and `crawl.discover_feeds` are tested modules (`tests/webcortex/test_crawl.py`) that the orchestrator does **not** call. Step 7 is the orchestrator's own two-link `follow()`, kept on purpose: `crawl.crawl` re-fetches its seeds (the first page is already read and recorded), returns only after the whole walk (no per-document checkpoint, cancel check or lease renewal between fetches) and has no render escalation. `follow()` keeps all four with the same `SafeFetcher` policy (`purpose="crawl"`). `crawl.crawl` honours robots wildcards (`test_crawl_honours_robots_wildcards`).
- Cached bodies are never served in place of a network request. A cache body is used only when the server answers 304 to a conditional request (a live observation, `retrieval_mode` `live`), or when `orchestrator.resume` re-reads a document read before an interruption (then `retrieval_mode` is `cache` and the report says `live: false` if nothing else was live).

## 6. SafeFetcher

`net.SafeFetcher` (stdlib `http.client`; no `requests`, `httpx` or Scrapling fetchers, ADR-024) is the only path to the public web. Each property has a test in `tests/webcortex/test_net.py`. Purposes: `page`, `feed`, `crawl` (robots, cached), `api` (no robots, never cached, own size cap, POST allowed), `robots`, and `subresource` (section 9).

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
| Body size | `WebConfig.max_document_bytes`, 5,000,000; `NetPolicy.max_api_bytes`, 16,000,000 for `purpose="api"` (large JSON metadata) | Pages and crawls are **truncated** and flagged; API, feed and sub-resource responses raise `too_large` (`test_size_limit_truncates_pages_and_rejects_api`, `test_api_responses_have_their_own_size_cap`) |
| Content type | Allow-list: `text/html`, `application/xhtml+xml`, `text/plain`, `application/json`, `application/*+json`, `application/xml`, `text/xml`, Atom/RSS, `application/pdf`; `purpose="subresource"` adds JavaScript and `text/css` types (`net.SUBRESOURCE_TYPES`) | Checked before the body is read; else `unsupported_type` (`test_disallowed_content_type`, `test_subresource_purpose_admits_scripts_and_styles`) |
| Decompression | gzip and deflate decoded under the same size cap | A bomb stops at the cap; corrupt data is `decode_error`; other encodings are refused (`test_gzip_bomb_is_capped`) |
| Status | 2xx accepted | Other final statuses raise `http_error` carrying the status |
| Budget | One request charged before every network call, including `robots.txt` and each redirect hop; body bytes charged after reading | `BudgetExceeded` is raised before the limit would be passed (`test_budget_counts_robots_redirects_and_bytes`, `test_byte_budget_never_exceeded`) |

### Politeness

- **robots.txt** is obeyed for `page`, `feed`, `crawl` and `subresource`; `api` and `robots` purposes skip it (documented public APIs). Rules are evaluated by `net.RobotsRules`, an RFC 9309 matcher (not `urllib.robotparser`). Entries are held 24 h in memory and in the public cache; the fetched file is capped at 512 KiB and only the first 500 KiB are parsed (cut at the last full line).

  | Matcher rule (`RobotsRules`) | Behaviour |
  |---|---|
  | Group selection | Groups whose `user-agent` token is the product token (`NeuroSciFi-WebCortex`, case-insensitive; a whole `-`-separated prefix such as `NeuroSciFi` also matches) are used, the longest token winning and equal tokens combined; otherwise the combined `*` groups; otherwise no rule applies |
  | Pattern language | `*` matches any run of characters; a final `$` anchors the end; otherwise a prefix match |
  | Decision | The matching rule with the longest normalised pattern wins; **Allow wins ties** |
  | Normalisation | Patterns and the URL's `path?query` are compared after percent-encoding normalisation (RFC 9309 section 2.2.2) |
  | Other | `/robots.txt` is always allowed; empty `Allow`/`Disallow` values are no rule; malformed lines are ignored; `Crawl-delay` (non-standard) is the largest value of the chosen groups; `Sitemap` lines are global (at most 100) |

  Tests: `test_robots_rfc9309_simple_example`, `test_robots_rfc9309_longest_match_and_ties`, `test_robots_rfc9309_groups_combine_and_select`, `test_robots_pattern_matching`, `test_robots_parsing_tolerates_malformed_input`, `test_robots_crawl_delay_sitemaps_and_parse_cap`, and `test_robots_wildcards_enforced_by_fetcher` (`Disallow: /*.pdf$` blocks `/x.pdf` through the fetcher).

  | `robots.txt` answer | Effect |
  |---|---|
  | 2xx | Parsed and used (`test_robots_disallow_blocks_without_requesting`, `test_robots_specific_agent_rules`) |
  | 4xx | Everything allowed; stored as an empty file |
  | 5xx | Everything on that host **disallowed**, in this process only (not written to the cache) |
  | DNS, connect, TLS or policy failure | Everything disallowed, in this process only |
  | Timeout or `rate_limited` | The fetch fails; no verdict is recorded |

  A disallowed URL raises `blocked_robots` without any request to the page (`test_robots_404_allows_and_500_disallows`).
- **Throttle.** A request slot per host at least the largest of three values after the previous one: `NetPolicy.min_domain_interval` (default 1.0 s), the host's entry in `NetPolicy.host_intervals`, and robots `Crawl-delay` (capped at 60 s). `host_intervals` defaults to `export.arxiv.org` 3.0 s (arXiv API terms) and `eutils.ncbi.nlm.nih.gov` 0.34 s (NCBI, three requests per second without a key); the longest matching domain suffix applies. Tests: `test_crawl_delay_and_domain_interval`, `test_host_intervals_with_fake_clock`. `purpose="subresource"` (browser sub-requests, section 9) uses `subresource_interval` (0.1 s) and the host interval only: `Crawl-delay` is not applied to one page view's sub-resources, robots allow/disallow still is (`test_subresource_obeys_robots_and_uses_short_interval`). Slot state is per process; the Retry-After backoff is shared (next item).
- **Retry-After.** HTTP 429 sets a host backoff from `Retry-After` (seconds or HTTP date, capped at 6 h; 60 s if absent). HTTP 503 does so only when it carries `Retry-After`. Any request to the host inside the backoff raises `rate_limited` with `retry_after` and touches no network (`test_retry_after_seconds_sets_backoff`, `test_retry_after_http_date_on_503`). The backoff is also written to the public cache (`backoff` table, a later `until` never shortens an earlier one), so a second fetcher or process sharing the cache honours it (`test_retry_after_shared_by_fetchers_on_one_cache`, `test_backoff_rows_keep_the_longest_and_expire`).
- **Conditional GET.** For `page`, `feed` and `crawl` GETs, if the cache holds an ETag or Last-Modified and the body, the request sends `If-None-Match` / `If-Modified-Since`. A 304 returns the cached body with `retrieval_mode="live"`, because the origin was just contacted and confirmed it current; the result header `x-nsf-revalidated: 304` (`net.REVALIDATED_HEADER`) marks that the body came from the cache (`test_conditional_get_returns_cached_body`).
- **Coalescing.** Identical concurrent GETs (same URL, purpose, conditional flag, extra headers) share one network call; followers receive a copy of the leader's result or error. Only the leader's budget is charged (`test_identical_concurrent_gets_are_coalesced`).
- **User agent.** `NeuroSciFi-WebCortex/0.7 (research agent; honours robots.txt)`. No contact address, no browser impersonation, no proxy rotation.

### What is cached

| Cached in `<db stem>.webcache.sqlite` | Not cached |
|---|---|
| 2xx GET bodies for `page`, `feed`, `crawl` that were not truncated (keyed by URL; bodies by SHA-256) | `purpose="api"` responses (their URLs embed queries; `test_api_responses_are_not_cached`) and `subresource` responses |
| `robots.txt` text and fetch time | Truncated bodies, HEAD and POST responses |
| ETag and Last-Modified validators | Any query, task objective or claim |
| Retry-After backoff per host (`backoff` table) | |
| `meta` rows (`last_prune`) | |

`PublicCache`: mode 0600, WAL, `secure_delete` on. `prune(now, max_bytes, max_age_seconds)` removes rows older than the age and expired backoffs, then least recently fetched bodies until under the size (`test_prune_by_age_and_size_lru`). `maybe_prune(now, ..., every=3600)` runs `prune` only when the `last_prune` value in `meta` is at least an hour old (or in the future) and returns the report or `None` (`test_maybe_prune_runs_at_most_once_per_interval`). `forget_url` and `forget_domain` delete rows and every body no remaining `urls` row references, and truncate the WAL; `forget_domain` also drops the domain's robots and backoff rows (`test_forget_url_and_domain`, `test_forget_deletes_only_orphaned_bodies`). The cache is excluded from checkpoints (ADR-023). A page rendered by the browser is stored under its content hash with a URL row `<url>#nsf-rendered`, which is never a fetchable URL, so a later fetch of the page is never served the rendered DOM; a resume re-reads it by hash (`test_rendered_pages_are_kept_by_hash_and_never_served_for_the_page_url`). The cache is disposable: a corrupt file ("file is not a database", "database disk image is malformed", on open or on any later use) is renamed `<name>.corrupt-<unix time>` with its `-wal` and `-shm` files, an empty cache is created in its place and the operation retried once. Each quarantine is recorded as an `error` event with stage `public_cache` and no task, and `nsf doctor` reports a corrupt file as `public_cache: warn` (`test_a_corrupt_cache_is_quarantined_and_rebuilt_on_open_and_on_use`, `test_doctor_checks_the_public_cache_file`, `test_privacy_refusal_names_the_cli_flag_and_status_survives_a_corrupt_cache`). Busy or locked errors are not corruption and propagate.

Retention is wired. `Services.prune_cache()` applies `WebConfig.cache_max_bytes` and `cache_max_age_days` through `maybe_prune`; it runs at the end of every research run and in every scheduler tick, and a failure is recorded without changing the run's outcome. `nsf web cache stats` reports the cache; `nsf web cache prune` prunes at once, outside the hourly schedule.

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
| Bounded work | At most `MAX_ELEMENTS` = 50,000 elements of a parsed page are kept (document order); Markdown is converted only when the main content has at most `MAX_MARKDOWN_ELEMENTS` = 10,000 elements (else it is the plain text); text is cut at 500,000 characters. Any cut sets `flags["truncated_extraction"]` | `test_pathological_page_extraction_is_bounded` |
| Line structure | `doc.text` has one line per block element (`h1`-`h6`, `p`, `li`, `td`, `blockquote`, `div`, `br`); whitespace inside a block, including source line breaks, is collapsed, so a line break in an HTML document's text is always a block boundary (claims split there) | `test_text_has_one_line_per_block_and_no_source_line_breaks` |

**Publication date**, in priority order: a `published_hint` from the caller (the date of the feed item the page was reached from; `date_source` "feed"); JSON-LD `datePublished`; a meta tag (`article:published_time`, `citation_publication_date`, `citation_date`, Dublin Core `dc.date*` / `dcterms.*`, `datepublished`, `pubdate`, ...); `<time datetime>` (prefers `itemprop=datePublished` and `pubdate`). `metadata["date_source"]` names the one used (`feed`, `json-ld`, `meta:<name>`, `time-element`, `pdf-creation-date`). Unix epochs in seconds or milliseconds are accepted as meta values (`og:updated_time="1663021219"`). JSON-LD and meta modification dates are stored in `modified_at` and never stand in for publication. The HTTP `Last-Modified` header is **never** a publication date, since servers stamp it on every deploy: it only fills `modified_at`, with `metadata["modified_source"] = "http-last-modified"` (`test_last_modified_header_is_never_a_publication_date`, `test_unix_epoch_meta_dates_are_parsed`, `test_feed_item_date_wins_as_published_hint`).

**Search-engine-reported dates** (v0.7.0). A search hit may carry a date the engine reported (SearXNG `publishedDate`, for example from Hacker News; `SearchResult.published_at`, never invented). When the page read from that hit has **no date of its own**, `orchestrator._search_date` keeps the reported date in the task checkpoint and `_search_dates` adds it to the report's source entry as `search_published_at`, with `date_source: "search_engine"` and a `date_note` naming the engine. The undated-source uncertainty line says what the engine reported. It is lower trust than an in-page date: `published_at` stays null, so freshness, claim weighting and verification still treat the page as undated, and an in-page date always wins (the reported date is then ignored). The model sees it as `search_engine_reported_date`, labelled. Feed items keep their stronger rule (`date_source` "feed"). Test: `test_a_search_engine_date_dates_an_undated_page_with_lower_trust`.

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

## 9. Browser rendering sandbox (`browser.py`, ADR-030, ADR-040)

Two owner switches: `browser_rendering` (off by default) permits every render escalation; `news_rendering` (on by default since 0.7.0, ADR-040) permits renders only for a news / time-sensitive question a person asked (CLI, console, voice, Pocket, MCP), never for autonomous research. Turn it off with `nsf web config --no-news-render`. Rendering needs the `web-render` extra and a Chromium binary; without them the task reads the static page and records why. `WebConfig.budget_for` gives a task `max_browser_sessions` 1 when either switch is on (0 when both are off; the ceiling is 2).

**Budget (ADR-040).** A render is accounted as:

| Limit | Default | Where |
|---|---|---|
| Page read | the render replaces the page's static read: one page of `max_pages`; the page itself is fetched once (`main`) and charged to the task's requests | `orchestrator._Run.escalate` |
| Renders per task | `max_browser_sessions` 1 (ceiling 2) | task budget |
| Sub-requests per render | `max_render_requests` 100 (ceiling 150), robots.txt of sub-resource hosts included; tallied in `usage.render_requests`, **not** charged to `max_requests` | task budget, `BrowserRenderer.render` |
| Bytes | sub-resource bytes count against the task's `max_bytes` | task budget |
| Time | one render at most 20 s (`BrowserRenderer.timeout`), shortened to the task's time left minus 2 s; not started with under 10 s left | `RENDER_MIN_SECONDS` |
| Renders per day | `daily_renders` 40 per UTC day, all tasks and processes; a meta counter (`webcortex.renders`), so forgetting a task does not refund it | `state.take_render` |
| Memory pressure | refused at `critical` (RD-46 `engine_gate`) | `resources.policy` |

When the allowance runs out the remaining sub-requests are refused (`budget_exhausted`), the report says the rendered page may be incomplete and the run is `partial`. `nsf web status` shows `renders.today` and `renders.daily_cap`.

**What a render fetches.** Measured on 2026-11-21 (`docs/quality/evidence/news_render_live_v2.json`):

| Site | Sub-requests before blocking | After | Note |
|---|---|---|---|
| msn.com | 76 to 80 | 69 to 81 | About 60 first-party script chunks build the article; blocking cannot remove them |
| foxbusiness.com | 76 | 34 to 36 | |
| wccftech.com | 26 | 8 | |
| finance.yahoo.com | 246 | 236 | Over the allowance; its static HTML already carries text and a JSON-LD date, so it is never rendered |

A render's peak is 427 to 534 MiB of summed PSS for the Playwright driver and Chromium (1.2 to 1.4 GiB of summed RSS)
and 3 to 5 s of CPU.

- **Static first for news questions.** Before any render, the static HTML is read for text and a date: JSON-LD
  `datePublished`, `article:published_time` and the other meta dates, `<time datetime>`, or the feed item's date
  when the page came from a feed.
  - Dated pages inside the required freshness window are read at once. The window is the question's recency
    window, else the TTL of its freshness class.
  - Five kinds of candidate wait: pages without text, undated scripted pages, encyclopedia articles, pages dated
    before the window, and hits the search engine dates before the window (not fetched yet).
  - Open slots then go to them in `LATER_ORDER`: pages without text first (rendered when allowed), then the rest
    in rank order.
  - A page without text that could not be rendered takes a slot last.
  - SearXNG news results are interleaved engine by engine before the cut to the limit.
  - Search-engine dates (SearXNG `publishedDate`) only order candidates (`orchestrator._search_dates`,
    EVIDENCE_AND_PROVENANCE). They never date a source and never satisfy a freshness requirement.
- The page body is first fetched with `SafeFetcher(purpose="page")`, so URL policy and robots apply before Chromium starts (`test_render_refuses_internal_targets_before_launch`).
- Fresh context per render, closed afterwards: no profile, `accept_downloads=False`, `permissions=[]`, `service_workers="block"`, Chromium sandbox on (`chromium_sandbox=True`), no extensions, background networking off. The honest User-Agent (`config.USER_AGENT`) is the context's User-Agent.
- `context.route("**/*")` intercepts **every** request. Only GET and HEAD over http(s) are served, and only through `SafeFetcher(purpose="subresource")`, so SSRF, DNS pinning, robots, Retry-After backoff, throttle, size and type rules cover each sub-resource. That purpose admits scripts and stylesheets in addition to the page types, uses the 0.1 s same-host interval instead of `Crawl-delay`, is never cached and never becomes a document (`test_render_executes_js_through_safe_fetcher`, `test_robots_rules_apply_to_every_sub_resource`).
- Never fetched, because text does not need them (`aborted` with `why`): images, media, fonts, stylesheets, manifests, pings and `other` (favicons, prefetches) → `type`; sub-frame documents (ads, consent and login iframes) → `frame`; hosts in `browser.BLOCKED_DOMAINS` (advertising, analytics and beacon domains such as doubleclick.net, googletagmanager.com, scorecardresearch.com, clarity.ms) and their subdomains → `tracker`. A blocked request costs nothing, not even robots.txt (`test_images_styles_frames_and_trackers_are_blocked_without_a_request`, `test_render_runs_external_scripts_and_blocks_styles`).
- Defence in depth for traffic that bypasses routing: Chromium resolves every name to NOTFOUND (`--host-resolver-rules=MAP * ~NOTFOUND`), a dead proxy is configured, WebRTC may not use non-proxied UDP. `WebSocket`, `RTCPeerConnection` and `EventSource` constructors throw inside every frame.
- One wall-clock timeout per render (20 s, see above); rendered body capped at the fetcher's `max_bytes`; `last_events` keeps each sub-request outcome (URL without query) and `last_usage` the requests and bytes spent, for audit.

## 10. Configuration knobs (`config.WebConfig`)

Stored as JSON in `meta` key `webcortex`; validated by `state.set_config`. Constants, not settings: robots.txt compliance, the address policy, read-only default.

| Field | Default | Meaning |
|---|---|---|
| `enabled` | false | Master switch for every network path |
| `autonomous` | false | Scheduler may start refresh and curiosity research |
| `pocket_research` | false | Paired Pocket devices may submit and read tasks |
| `browser_rendering` | false | Permit sandboxed JavaScript rendering for every escalation |
| `news_rendering` | true | Permit rendering for a news / time-sensitive question a person asked, when the static page gave no text or no date (ADR-040) |
| `daily_renders` | 40 (0-1000) | Renders per UTC day across all tasks (ADR-040) |
| `openwebsearch_url` | `http://127.0.0.1:3210` | Loopback daemon; validated as plain-HTTP loopback |
| `engines` | duckduckgo, brave, startpage, bing | Engine order, tried one at a time |
| `providers` | openwebsearch, wikipedia, arxiv, openalex, crossref, pubmed, pypi, npm, osv, github | Providers built, in order |
| `budget_ceiling` | queries 16, requests 200, pages 30, bytes 60,000,000, seconds 600, depth 2, browser sessions 2, render requests 150, concurrency 6, context tokens 6000 | Upper bound for any task budget |
| `daily_autonomous` | tasks 12, requests 400, bytes 80,000,000 | 24 h cap for curiosity and refresh tasks |
| `min_domain_interval` | 1.0 s (0-3600) | Minimum gap between requests to one host; the effective gap is the largest of this, the host's `NetPolicy.host_intervals` entry (arXiv 3.0 s, NCBI 0.34 s) and `Crawl-delay` |
| `max_document_bytes` | 5,000,000 (min 1024) | Body cap per fetch |
| `cache_max_bytes` | 512 MiB | Public-cache size bound, applied by `Services.prune_cache` and `nsf web cache prune` |
| `cache_max_age_days` | 30 | Public-cache age bound, same |
| `deny_domains` | none | Never contacted (subdomains too) |
| `private_terms` | none | Extra strings that may never leave in a query |
| `auto_provisional_min_sources` | 2 (1-50) | Independent sources before a claim may become provisional |
| `auto_provisional_min_confidence` | 0.72 | Confidence floor for the same; admits exactly two dated secondary sources (0.722), not dated + undated (0.706) |
| `freshness_ttl` | none | Per-class TTL overrides in seconds; threaded into `claims.score`, `synthesis.synthesize` and `knowledge.staleness` |

## 11. How to verify

```sh
pytest -q tests/webcortex/test_net.py tests/webcortex/test_extract.py tests/webcortex/test_cache.py
pytest -q tests/webcortex/test_search.py tests/webcortex/test_openwebsearch.py tests/webcortex/test_providers.py
pytest -q tests/webcortex/test_crawl.py tests/webcortex/test_browser.py
NSF_LIVE=1 pytest -q -k live tests/webcortex     # real internet; optional
```

The test counts for the current code are in TEST_RESULTS.

## 12. Limits stated plainly

- Search quality depends on unofficial engine scrapers that fail independently.
- Rendering depends on a local Chromium and is off by default.
- The per-host request slot (throttle) is per process: two processes (for example the worker and a manual fetch) do not coordinate their gaps. Retry-After backoff is shared through the public cache; robots entries are cached there too.
- Sites that require anti-bot circumvention, logins or payment are reported unavailable; none is attempted.
