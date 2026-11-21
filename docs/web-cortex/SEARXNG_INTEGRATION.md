# Web Cortex: SearXNG integration

Status: v0.7.0 working tree (Lane A), measured live on 2026-11-21. SearXNG is NeuroSciFi's primary general
discovery provider when `searxng_url` is set; open-webSearch is its fallback; structured APIs stay direct
([SEARCH_PROVIDER_STRATEGY.md](SEARCH_PROVIDER_STRATEGY.md)). Decision record: ADR-034 (draft
`.omc/autopilot/adr/ADR-034-searxng.md`; supersedes ADR-032). Code: `src/neuroscifi/webcortex/providers/searxng.py`
(provider + container), `intent.searxng_route`, `providers.build`, `search.ProviderRegistry._deferred`,
`cli._searxng_cmd`. Tests: `tests/webcortex/test_searxng.py` (40, offline). Evidence:
`docs/quality/evidence/searxng_live.json`.

## 1. Deploy

Requirements: docker (rootful; tested with Docker Compose 5.6.0 present but not used — a single container needs no
compose file) and the pinned image already pulled:

| Item | Value |
|---|---|
| Image | `searxng/searxng@sha256:fc1d81d7e9d04d3c8b3e23c7162feddf5c3d6aecd32e67a56eaabcb8d8747f79` (arm64, 2026.11.21-c6ee3e9fd, AGPL-3.0-or-later). Always run by digest, never by tag. |
| Container | `neuroscifi-searxng`, label `neuroscifi.component=searxng` (stop/rm touch only this) |
| Published | `127.0.0.1:8888 -> 8080` only (8000/9000/3000 are taken on this host) |
| Config dir | `~/.config/neuroscifi/searxng/` (`$XDG_CONFIG_HOME` respected), mode 0700 |

```
nsf web config --enable --db DB          # web access is off by default
nsf web searxng start --db DB            # prepares config, runs the container, probes headers, sets searxng_url
nsf web searxng status --db DB           # container + provider view
nsf web searxng health --probe --db DB   # one real query per category; refreshes the 'healthy' evidence
nsf web searxng stop --db DB             # stops and removes the container (config dir and secret stay)
```

What `start` does (`Container.start`):

1. If NeuroSciFi's container already runs the pinned image: reports "already running". Refuses a port held by
   anything else (`port_in_use`).
2. `prepare()`: creates the config dir (0700); creates `secret.env` (`SEARXNG_SECRET=<64 hex>`, 0600) **once**;
   copies `deploy/searxng/settings.yml` (0644; no secret in it); reads the image's pristine
   `searx/network/client.py`, checks its sha256 (`CLIENT_SHA256`), writes the patched copy (§3) and an honest
   `useragents.json`; writes `neuroscifi-honest-ua.json` with the sha256 of each file.
3. `docker run` with the flags below; waits for `/healthz`.
4. Runs the header probe (§3) inside the container; if any request still looks like a browser, stops it and fails
   (`not_honest`).
5. When `searxng_url` is empty, sets it to `http://127.0.0.1:8888` (that makes SearXNG the primary provider).

Container hardening (`Container.run_args`):

| Flag | Why |
|---|---|
| `--publish 127.0.0.1:8888:8080` | loopback only; the bind is the inbound control |
| `--read-only` + `--tmpfs /tmp`, `/var/cache/searxng`, `/etc/searxng` (uid 977), `searx/network/__pycache__` | immutable root; the pycache tmpfs is required for the patch (§3) |
| `--user 977:977`, `--cap-drop ALL`, `--security-opt no-new-privileges`, `FORCE_OWNERSHIP=false` | unprivileged; the image would otherwise run granian as root |
| `--memory 768m --memory-swap 768m --cpus 1.0 --pids-limit 256` | resource limits |
| `--restart unless-stopped` | survives reboots and crashes until `stop` |
| `--health-cmd wget …/healthz` (30 s) | docker health state, shown by `status` |
| `--log-driver json-file --log-opt max-size=10m --log-opt max-file=3` | structured (JSON lines with time and stream), bounded logs: `docker logs neuroscifi-searxng` |
| settings, patched `client.py`, `useragents.json` bind-mounted `:ro` | the container cannot change them |
| `--env-file secret.env` | secret outside git and outside settings.yml |

systemd: `deploy/systemd/neuroscifi-searxng.service` (installed disabled) is a oneshot wrapper around
`nsf web searxng start|stop`. `sudo bash deploy/install.sh --searxng` (dry run: `--dry-run --searxng`) installs it
and copies `deploy/searxng/settings.yml` to `PREFIX/searxng/settings.yml`, the path in `NSF_SEARXNG_SETTINGS`; then
set `User=` (a docker user) and `NSF_DB` with `systemctl edit neuroscifi-searxng.service`. Docker's restart policy keeps the container up between boots either way.

## 2. Configuration

`deploy/searxng/settings.yml` (rendered verbatim; `use_default_settings` with `engines.keep_only`):

| Setting | Value | Reason |
|---|---|---|
| `search.formats` | `[html, json]` | the adapter uses JSON |
| `server.limiter` | `false` | the limiter protects public instances from anonymous clients and needs valkey; this one has one client on the same host, loopback-only, so no valkey container is run |
| `server.public_instance` | `false` | |
| `server.image_proxy` | `false` | nothing fetches images |
| `search.autocomplete` | `""` | no query leaves for autocompletion |
| `search.default_lang` | `all` | NeuroSciFi passes `language` when the question has one |
| `outgoing.request_timeout` / `max_request_timeout` | 4 s / 8 s | the adapter waits 6–20 s (adaptive) |
| `outgoing.useragent_suffix` | `NeuroSciFi-WebCortex (self-hosted research agent; loopback-only instance)` | appended to SearXNG's own User-Agent |
| engines | 10, see §4 | explicit minimal set per category |

NeuroSciFi configuration (`nsf web config --set KEY=JSON`): `searxng_url` (empty = not configured; must be a bare
`http://` loopback origin, `state._validate`) and `searxng_engines` (optional `{category: [engine, …]}` pins for
general/news/it/science). Environment: `NSF_SEARXNG_SETTINGS` (template path for installed wheels).

**Authenticated backend access.** SearXNG has no client authentication, so a shared header or token would be
checked by nothing; none is sent. Access control is the loopback-only publish: only processes on this host reach
it (the same trust boundary as the open-webSearch daemon, ADR-025). Residual risk, accepted: any local user can
query it; it holds no secrets or private data, and it logs no queries (debug off, no access log). The adapter
accepts only `http://127.0.0.1|localhost|[::1]` origins and bypasses SafeFetcher for exactly that URL; SafeFetcher's
SSRF policy (loopback refused) is unchanged.

**Secrets.** `secret.env` signs SearXNG's cookies/preferences only. Rotate: `nsf web searxng stop`, delete
`~/.config/neuroscifi/searxng/secret.env`, `start` (a new one is generated). It is never logged or committed.

## 3. Honest requests (the patch)

Unmodified SearXNG 2026.11.21 impersonates a browser on every engine request: `network/client.py` defaults to
curl_cffi `impersonate="chrome"` (Chrome TLS fingerprint plus Chrome `User-Agent`, `sec-ch-ua`, `Sec-Fetch-*`
headers), engines may ask for `firefox`, and `utils.gen_useragent()` returns a random Firefox User-Agent. Measured
inside the image before the patch: a plain request carried `Mozilla/5.0 … Chrome/152` and `sec-ch-ua`. That
contradicts the ADR-032 rule (honest User-Agent, no anti-bot circumvention), so NeuroSciFi mounts:

- a patched `client.py`, produced by `providers/searxng.honest_client` from the pinned file (sha256 verified):
  `use_impersonate = False` and the client's default headers `{"User-Agent": searxng_useragent()}`;
- `useragents.json` whose "random browser" string is SearXNG's honest User-Agent;
- an empty tmpfs over `searx/network/__pycache__`: the image ships unchecked hash-based `.pyc` files, so Python
  would otherwise keep running the original module and ignore the mounted source (found during deployment).

Every engine request now says
`SearXNG/2026.11.21+c6ee3e9fd NeuroSciFi-WebCortex (self-hosted research agent; loopback-only instance)`.
`Container.probe_headers()` verifies it at every start by running SearXNG's own network layer inside the container
against a throwaway loopback listener (default request, Chrome-requested, Firefox-requested, `gen_useragent()`):
all four carry only the honest User-Agent and no Client-Hint or `Sec-Fetch` headers (evidence JSON,
`instance.header_probe`). Engines may still add their own headers: the duckduckgo module adds browser-style
`Sec-Fetch-*` and `Referer` headers to its form POST; the User-Agent stays honest.

## 4. Engines and compliance

An engine is enabled only if (1) the site's robots.txt allows the URL the engine requests, or the engine calls a
documented public API (the same rule as SafeFetcher `purpose="api"`, ADR-024), (2) its module does not solve
CAPTCHAs or proof-of-work challenges, and (3) it does not replay anti-bot state or mimic a browser's header order.
robots.txt was evaluated with NeuroSciFi's own RFC 9309 matcher (`net.RobotsRules`) on the URL each engine module
builds (evidence JSON, `engine_compliance`).

| Category | Enabled | Access |
|---|---|---|
| general | duckduckgo (html.duckduckgo.com/html/: robots allows), mwmbl (api.mwmbl.org) | html / API |
| news | bing news (/news/infinitescrollajax: robots allows), hackernews (hn.algolia.com API) | html / API |
| it | stackoverflow, askubuntu, superuser (Stack Exchange API), docker hub (search API: robots allows), hackernews | API |
| science | openairepublications (OpenAIRE API), europepmc (Europe PMC REST API) | API |

Not enabled, and never queried by NeuroSciFi:

| Engine | Reason |
|---|---|
| brave, bing (web), google news, yahoo news, arch linux wiki | robots.txt disallows the result page |
| mojeek | robots.txt disallows `/search`; the module solves Mojeek's ALTCHA CAPTCHA |
| startpage | robots.txt disallows; the module solves an Anubis proof-of-work challenge |
| qwant | replays DataDome cookies with Chrome header order (bot-detection evasion) |
| duckduckgo news | duckduckgo.com robots.txt disallows `/*?`; the module asks for Firefox impersonation |
| reuters, semantic scholar, mdn | internal website APIs disallowed by robots.txt |
| wikinews | measured: 0 results in 6 calls and one 429; dropped after the measurement (MediaWiki API, compliant) |
| google, google scholar | robots.txt disallows; google sends a hard-coded Samsung browser User-Agent |
| arxiv, pubmed, crossref, openalex, pypi, npm, github, wikipedia, wikidata | NeuroSciFi calls these directly (structured APIs stay primary); wikipedia/wikidata would also need a contact URL |

## 5. Adapter behaviour

`SearXNGProvider` (`name="searxng"`, kinds web/news/academic/technical, `general_web=True`):

- `GET /search?q=&format=json&categories=&pageno=1&safesearch=0[&language=][&time_range=][&engines=]`; category
  and time range from `intent.searxng_route`; `engines=` only to leave out engines whose circuit is open (or the
  configured pins). Query collapsed and cut at 512 characters; `Query.site` becomes ` site:<domain>`.
- Normalised `SearchResult`: title/snippet (`content`) HTML-stripped; `url` (non-http(s) and duplicates dropped);
  `engine` (first engine) and `metadata.engines` (all); `metadata.category`; `metadata.searxng_score`;
  `metadata.retrieved_at`; `metadata.canonical_url`; `rank`; `kind` `academic` for science else `web`;
  `published_at` **only** from a parseable `publishedDate` — missing, null or unparseable stays `None`
  (`test_results_are_normalised_and_dates_are_never_invented`). Dated results older than `Query.since` are dropped;
  undated ones stay. A page read from a dated hit that has no date of its own is shown with that date as
  search-engine-reported, lower trust, never as its publication date (SEARCH_AND_ACQUISITION §7).
- Engine health: `unresponsive_engines` → per-engine Breaker failure classified captcha / forbidden (403) /
  rate_limited (429) / timeout / error, plus counters (`engine_stats`); contributing engines → success; silent
  engines → neutral ("empty"). Exposed as `last_attempts` (registry `attempts`, `searxng:<engine>` in status and
  in the persisted `web_strategies` health).
- Breakers: the registry's `Breaker` (3 consecutive failures → open 300 s → one trial). Provider-level failures
  (`searxng_unreachable`, `timeout`, `searxng_error`, `engines_failed`, `bad_response`, `http_error`) move the
  provider breaker; `rate_limited` with Retry-After and `circuit_open` do not.
- Adaptive timeout: 2 × p90 of recent call latency + 2 s within 6–20 s (12 s until 3 calls). One retry after
  0.5 s on 5xx or a reset connection; none on timeout or 429.
- Cache: identical requests within 120 s are answered from memory (no upstream request, no budget charge,
  `metadata.cache=true`, original `retrieved_at`), at most 128 entries.
- Budget: one `usage.requests` per upstream engine queried (true upstream load).
- `health()`: `/healthz`, `/config` (version, enabled engines per category), engine snapshots, adaptive timeout,
  `verdict` (SEARCH_PROVIDER_STRATEGY §4: healthy needs a non-empty real result within 6 h).

## 6. Operations and recovery

| Symptom | Check | Fix |
|---|---|---|
| `verdict: down` | `nsf web searxng status` (`answers`, `container.running`, `health`) | `nsf web searxng start`; `docker logs neuroscifi-searxng` |
| `start` → `port_in_use` | `ss -ltnp \| grep 8888` | free the port or `nsf web config --set searxng_url='"http://127.0.0.1:PORT"'` then `start` |
| `start` → `not_honest` or "not the pinned file" | the image or its digest changed | do not run it; re-pin (below) |
| `container exited` | `docker logs` tail in the error | usually settings.yml syntax; `stop`, fix the template, `start` |
| `verdict: degraded` | `health --probe`; `engines` in `status` | an engine CAPTCHA/403/429s: its breaker opens and recovers by itself; SearXNG also suspends it (CAPTCHA 1 h, 429 3 min, 403 3 min by default) |
| every engine of a category open | `searxng:<engine>` `last_error` | wait the cooldown; persistent → disable the engine in settings.yml (document why) |
| results but no dates | `dated_share` in the evidence | expected for mwmbl, bing news, Stack Exchange; never filled in |

Re-pinning a new image: pull by digest, read the new `client.py`, re-derive the two patch edits, update
`IMAGE_DIGEST`, `IMAGE_VERSION`, `VERSION_TAG`, `CLIENT_SHA256` (tests pin them), re-run the robots/compliance check
for every enabled engine, `start` (the probe must pass), re-measure and update the evidence.

Rollback: `nsf web searxng stop` and `nsf web config --set 'searxng_url=""'` restore v0.6.0 behaviour exactly
(open-webSearch primary again; nothing else changes).

## 7. Measured results (2026-11-21)

Source: `docs/quality/evidence/searxng_live.json` (generated 11:19 UTC). Container running, published on
`127.0.0.1:8888` only, read-only root, `CapDrop [ALL]`, 768 MiB, 1 CPU, 256 pids, user 977, no-new-privileges,
`unless-stopped`; header probe honest (4/4 requests with the SearXNG User-Agent, no browser headers).

**Direct queries** (`GET /search?format=json`, 3 per category, 1 s apart; latency is the whole SearXNG call —
SearXNG's JSON does not give per-engine latency):

| Engine | Calls | With results | Empty | CAPTCHA | 403 | 429 | Timeout | Results | Dated | Median age (dated) | Call latency median |
|---|---|---|---|---|---|---|---|---|---|---|---|
| duckduckgo | 3 | 0 | 0 | 3 | 0 | 0 | 0 | 0 | — | — | 0.57 s |
| mwmbl | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 147 | 0 % | — | 0.57 s |
| bing news | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 26 | 0 % | — | 0.28 s |
| hackernews (news + it) | 6 | 5 | 1 | 0 | 0 | 0 | 0 | 95 | 100 % | 248 d | 0.30 s |
| wikinews (since dropped) | 3 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | — | — | 0.28 s |
| stackoverflow | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 21 | 0 % | — | 0.39 s |
| askubuntu | 3 | 2 | 1 | 0 | 0 | 0 | 0 | 12 | 0 % | — | 0.39 s |
| superuser | 3 | 1 | 2 | 0 | 0 | 0 | 0 | 5 | 0 % | — | 0.39 s |
| docker hub | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 30 | 100 % | 30 d | 0.39 s |
| europepmc | 3 | 1 | 0 | 0 | 0 | 0 | 2 | 20 | 100 % | 51 d | 4.27 s |
| openairepublications | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 30 | 0 % | — | 4.27 s |

Per category: general 34–65 results per query, all from mwmbl, none dated; news (time_range=week) 39 / 13 / 7
results with 30 / 3 / 0 dated (the dated ones are Hacker News items; Bing News sends no `publishedDate`); it 32–57
results; science 10–30 results, Europe PMC timing out at SearXNG's 4 s on two of three queries.

**Acceptance queries** through `nsf web ask` on a fresh individual (open-webSearch daemon not running):

| Question | Mode | SearXNG categories used | Candidates | Read | Outcome |
|---|---|---|---|---|---|
| What happened in AI this week? | quick_lookup | news (week) | 12, all SearXNG (bing news, hackernews) | 3 msn.com pages | discovery worked; 0 claims extracted (JavaScript pages), no dated source: freshness not met |
| Latest developments in open-source LLM inference | quick_lookup | news | 8 SearXNG, 5 dated | 2 pages | llm-d 1.0 release (2026-10-20/21) quoted, single source; freshness window (2 days) not met |
| Recent research on predictive coding | scientific | general + science, plus direct arXiv/OpenAlex/Crossref/PubMed | 80 (SearXNG 29, OpenAlex 16, arXiv 16, PubMed 14, Crossref 5), 59 dated | 12 records/pages | newest cited source 1 day old; structured APIs carried the answer, SearXNG supplemented |
| Current DGX Spark software updates | quick_lookup | news | 8 SearXNG, 3 dated | 3 | relevant msn/Hacker News hits; newest dated source 2026-06-30: freshness not met |
| Latest releases affecting NeuroSciFi | technical | general,it | 23 SearXNG + 20 Wikipedia | 9 | "NeuroSciFi" is local context and is stripped, so the plan searched "releases": irrelevant results (see §8) |
| (dependency interpretation) `nsf web deps` | technical | none (PyPI/npm/OSV direct) | 49 packages | 4 PyPI records | e.g. numpy 2.5.5 (2026-11-15), scipy 1.18.1, cupy-cuda13x 14.2.0, zstandard 0.25.0 |

Engine attempts inside those runs: duckduckgo 6 attempts, 5 with results (50) and 1 CAPTCHA — so its CAPTCHA is
intermittent, not permanent; mwmbl 6 (3 with results, 3 empty); bing news 4/4; hackernews 7/7; docker hub,
stackoverflow, askubuntu, superuser 3/3 each; europepmc 2/2; openairepublications 2/2; wikinews 3 empty + 1
`too many requests`. No 403 anywhere; one 429 (wikinews); CAPTCHA only from duckduckgo (4 of 9 calls overall).

## 8. Limitations and release blockers

1. **Blocker — general web discovery is thin.** The only honest, robots-compliant general web engines are
   duckduckgo (CAPTCHA on 4 of 9 calls) and mwmbl (small independent index, never dated). Brave, Bing, Google,
   Mojeek, Startpage and Yahoo either disallow their result pages in robots.txt or need challenge solving/browser
   mimicry; they stay off. "SearXNG as primary general provider" therefore works for news, software and science
   discovery but gives weak general web coverage. Owner options: a keyed API engine (for example Brave Search API
   or a Google Programmable Search key, both supported by SearXNG), or accepting the limitation. Re-enabling
   browser impersonation is not an option under the project rules.
2. **Freshness of news.** Bing News results carry no `publishedDate`; dated news comes from Hacker News. Pages read
   from msn.com are JavaScript shells (0 claims). Current-event questions therefore often end with "freshness
   requirement not met" — honestly, but not usefully.
3. ~~**"Latest releases affecting NeuroSciFi"** is planned as a web search for "releases"~~ — fixed (Lane G):
   `intent.dependency_question` recognises "releases / advisories affecting <this system>" and the task runs the
   `nsf web deps` logic (project inventory + direct PyPI/npm/OSV pass, `report.dependencies`); no general search is
   sent. Live result: `docs/quality/evidence/search_reasoning_live.json`.
4. Europe PMC often exceeds SearXNG's 4 s engine timeout (2 of 3 calls).
5. The adapter cannot report per-engine latency (SearXNG's JSON has none); the evidence records call latency.

## 9. AGPL-3.0 review

SearXNG is AGPL-3.0-or-later. NeuroSciFi does not import, link or vendor SearXNG code: it runs the upstream image
as a separate process and talks HTTP to it, so NeuroSciFi's own licence is unaffected. The deployed instance is
**modified**: the honest-UA patch (§3) changes one upstream file and one data file at run time. AGPL-3.0 §13
requires offering the Corresponding Source of a modified version to every user who interacts with it remotely over
a network. Here the only user is the owner, through NeuroSciFi on the same host (loopback publish; not reachable
from the LAN). The complete modification is the code that generates it (`providers/searxng.honest_client`,
`honest_useragents`) together with the pinned upstream revision `c6ee3e9fd` (github.com/searxng/searxng), both
available to that user. No patched SearXNG file is committed to this repository (the patch is applied to the
file read from the pinned image at start). Obligation that follows: if the instance is ever exposed to anyone
else (LAN, public), a source offer must be added first (SearXNG shows a source link in its UI footer; the
modification would have to be published alongside). Using it unmodified would not change this analysis, but
unmodified use would mean browser impersonation, which the project's rules forbid.
