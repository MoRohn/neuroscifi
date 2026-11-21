# Dependency review

Reviewed 2026-11-20 on the DGX Spark build host (aarch64, Python 3.12.16, Node v26.13.0). Every package is
pinned. The core package (`numpy`, `zstandard`) is unchanged; Web Cortex dependencies are optional extras, and
`neuroscifi` imports them lazily, so an install without `[web]` still runs every 0.5 command.

## 1. What already existed (reused, not duplicated)

| Need from the directive | Existing NeuroSciFi equivalent | Decision |
|---|---|---|
| Persistent store, task state, memory | `instance` SQLite file, forward-only migrations, `store.write` | Reused: migration 6 adds the `web_*` tables (ADR-029). |
| Episodic / semantic / procedural memory | `evidence` journal, `belief_revisions`, `graph_revisions` (binary beliefs and graphs only) | Extended with web-specific tables in the same file and the same `memory.retrieve` envelope. |
| Executive approvals | `pending` + `nsf resolve --confirm/--reject` | Reused for knowledge acceptance, monitor and curiosity proposals (scope `web:*`). |
| Forgetting, erasure, checkpoints | `governance.forget_*`, `deletion_log`, `checkpoints.mark stale` | Extended with `forget_research/knowledge/source`. |
| Task queue / scheduler | none (ADR-012: explicit jobs, systemd timers) | Added a SQLite-backed task queue drained by `nsf web work`, and `nsf web tick` on a timer (ADR-027). |
| Vector / graph DB, embeddings, reranker | none (ADR-009) | Not added. BM25 + recency + independence ranking; entity/claim graph in SQLite tables. |
| HTTP client | stdlib only | Stdlib `http.client` with an address-pinning connection (ADR-024). No `requests`/`httpx`. |
| Browser | Playwright 1.64.1 already a `dev` dependency (e2e tests), system Chromium | Reused as the optional `web-render` extra. |
| Search engine access | none | open-webSearch daemon (Node), pinned. |
| MCP | none | Minimal stdlib JSON-RPC stdio server (`nsf web mcp`); no MCP SDK dependency. |

## 2. Added Python packages

`pip install -e '.[web]'` (required for Web Cortex extraction):

| Package | Version | License | Why | Transitive (resolved) |
|---|---|---|---|---|
| scrapling | 0.5.1 | BSD-3-Clause | HTML parsing and selection (`Selector`), parser only | lxml 6.1.3 (BSD-3), cssselect 1.6.0 (BSD-3), orjson 3.13.0 (MPL-2.0 AND (Apache-2.0 OR MIT)), tld 0.13.2 (MPL-1.1 OR GPL-2.0 OR LGPL-2.1+), w3lib 2.6.0 (BSD-3), frostwork 0.2.2 (Apache-2.0), typing_extensions 4.16.0 (PSF-2.0) |
| markdownify | 1.2.4 | MIT | HTML → Markdown for reading views | beautifulsoup4 4.15.0 (MIT), soupsieve 4.1.3 (MIT) |
| pypdf | 7.1.1 | BSD-3-Clause | Bounded PDF text extraction | none on 3.12 |

Optional extras:

| Extra | Package | Version | License | Why | Transitive |
|---|---|---|---|---|---|
| `web-extract` | trafilatura | 2.3.1 | Apache-2.0 | Alternative main-content extractor, measured against the Scrapling heuristic in TEST_RESULTS | courlan 1.5.0, htmldate 1.11.0, justext 3.0.2, charset-normalizer 3.5.2, certifi, urllib3 2.8.0, dateparser 1.5.1, python-dateutil 4.0.0, pytz, regex, tzlocal, tzdata, lxml_html_clean 0.4.6 |
| `web-render` | playwright | 1.64.1 | Apache-2.0 | Sandboxed JavaScript rendering (ADR-030) | greenlet, pyee (already present via `dev`) |

All licenses are compatible with distribution under AGPL-3.0-or-later.

**Vulnerability audit.** OSV `querybatch` for every resolved Python package above plus numpy 2.3.5 and
zstandard 0.25.0, run 2026-11-20: **no known vulnerabilities**. Re-run with `nsf web deps --audit`.

### Deliberately not installed

| Package / feature | Reason |
|---|---|
| `scrapling[fetchers]` (curl_cffi, patchright, browserforge, apify-fingerprint-datapoints) | Default TLS/JA3 browser impersonation, forged headers, Cloudflare/Turnstile solving and fingerprint spoofing: anti-bot circumvention the directive forbids. Redirect safety is a post-hoc IP check without DNS pinning. |
| Scrapling spiders | Need `fetchers`; `robots_txt_obey` defaults to False. Replaced by `crawl.py` (robots always on, bounded). |
| `scrapling[ai]` MCP server | Its tools include browser automation and CAPTCHA-solving hints; not a read-only research surface. |
| `requests`, `httpx`, `aiohttp` | Stdlib `http.client` suffices; one less TLS stack to audit. Async orchestration uses `asyncio.to_thread`. |
| Vector databases, embedding/reranking models | No encoder exists (ADR-009); a placeholder would be dishonest. |

## 3. open-webSearch (Node, separate process)

| Item | Value |
|---|---|
| Package | `open-websearch` 2.2.0 (npm `latest`, released 2026-09-28) |
| License | Apache-2.0 (repository `LICENSE`; the npm manifest has no `license` field) |
| Pin | `deploy/open-websearch/package.json` + `package-lock.json`, installed with `npm ci --ignore-scripts` |
| Size | 152 packages, 82 MB in `node_modules` |
| Runtime deps | @modelcontextprotocol/sdk, @mozilla/readability, axios, cheerio, cors, express 5, https-proxy-agent, ipaddr.js, jsdom, koffi, request-filtering-agent, zod 3 |
| Used surface | `serve --host 127.0.0.1` REST daemon: `GET /health`, `GET /status`, `POST /search` only |
| Not used | `/fetch-*` tools (our SafeFetcher acquires pages), `/mcp` and SSE modes (bind `0.0.0.0`, no auth, DNS-rebinding protection disabled), Playwright mode (not installed) |

**`npm audit` (2026-11-20): 2 high** — both the same advisory: `zod <4.6.6`, GHSA-mrhx-c7xc-m3w5
("can exhaust memory while validating oversized constrained collections"), reached through `open-websearch`.
The only fix npm offers downgrades to open-websearch 1.2.4. **Accepted risk:** the daemon binds 127.0.0.1, its
only client is `providers/openwebsearch.py`, which bounds every request (query ≤ 512 characters, `limit` ≤ 20,
fixed engine list). Revisit when upstream moves to zod ≥ 4.6.6.

**Observed reliability (live, 2026-11-20):** duckduckgo returned results; brave answered 429; startpage
returned 0 results without an error; bing failed with "regional redirect limit". Engines are unofficial scrapers
and fail independently; the daemon reports this in `partialFailures` with HTTP 200 and performs no fallback. Our
client tracks health per engine and falls back across engines and to the structured APIs (ADR-025).

## 4. Structured public APIs (no keys, no accounts)

| Provider | Endpoint | Use | Notes |
|---|---|---|---|
| Wikipedia | `en.wikipedia.org/w/api.php` | stable encyclopedic background | last-edit timestamp, not publication date |
| arXiv | `export.arxiv.org/api/query` (Atom) | preprints, versions | arXiv asks for ≤ 1 request / 3 s; per-host throttle enforces it |
| OpenAlex | `api.openalex.org/works` | scholarly works, abstracts | citation counts shown as metadata only, never as truth |
| Crossref | `api.crossref.org/works` | DOIs, publication dates | |
| PubMed | NCBI E-utilities `esearch`/`esummary` | biomedical literature | ≤ 3 requests / s without a key |
| PyPI | `pypi.org/pypi/<name>/json` | release versions and dates | |
| npm | `registry.npmjs.org/<name>` | release versions and dates | |
| OSV | `api.osv.dev/v1/query` | security advisories | authoritative aggregation of GHSA, PYSEC, etc. |
| GitHub | `api.github.com/repos/<o>/<r>/releases` | release notes | 60 unauthenticated requests / hour |

No contact e-mail is sent in requests (the "polite pool" parameters of OpenAlex and Crossref are not used): the
user agent is a generic product string (`config.USER_AGENT`).

## 5. Upgrade procedure

1. Bump one pin, `uv pip install -e '.[web,web-extract,web-render]'`, `uv pip check`.
2. `nsf web deps --audit` (OSV) and `cd deploy/open-websearch && npm ci --ignore-scripts && npm audit --omit=dev`.
3. `pytest -q tests/webcortex` and `nsf web bench` — extraction accuracy must not regress against TEST_RESULTS.
4. Record the change in this file.
