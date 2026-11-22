# Web Cortex: search provider strategy

Status: v0.7.0 working tree (Lane A). Which provider answers which query, in what order, and what happens when one
fails. Mechanics of each provider: [SEARCH_AND_ACQUISITION.md](SEARCH_AND_ACQUISITION.md) (registry, breakers,
fusion) and [SEARXNG_INTEGRATION.md](SEARXNG_INTEGRATION.md) (SearXNG); the keyed Brave Search API is §6. Decision:
ADR-034 (draft `.omc/autopilot/adr/ADR-034-searxng.md`), which supersedes ADR-032 and amends ADR-025, and its
amendment 1 (a keyed engine is the primary general discovery provider when configured).

## 1. Rules

1. **Structured sources first, and direct.** Literature (arXiv, OpenAlex, Crossref, PubMed), registries (PyPI, npm,
   OSV, GitHub releases) and Wikipedia are called through their own APIs (`providers/structured.py`,
   SafeFetcher `purpose="api"`). They are never routed through SearXNG, and SearXNG does not enable its engines
   for them. A registry lookup (`pypi:`, `npm:`, `osv:`, `github:`) never reaches a general search provider
   (`search.REGISTRY_QUERY`; `SearXNGProvider.accepts`).
2. **A keyed engine is primary when its key is configured** (ADR-034 amendment 1). With a Brave Search API key
   (`nsf web provider set-key brave`, §6) Brave is built first and answers `web` and `news` queries; SearXNG then
   carries `fallback_for = "brave"`. Without a key Brave is not built and everything below is unchanged.
3. **SearXNG is the primary key-free general provider** (`searxng_url`, set by `nsf web searxng start`). It is
   built first when no key is configured, else right after Brave.
4. **open-webSearch is SearXNG's fallback**, not a peer: with SearXNG configured it carries
   `fallback_for = "searxng"` (Brave's when only Brave is configured) and runs for a query only when SearXNG's
   circuit is open (same round) or SearXNG failed or found nothing (next round). Fallbacks chain one tier at a
   time (brave → searxng → openwebsearch): a fallback waits while any provider up its chain is live (so with
   SearXNG's circuit open, open-webSearch still waits for Brave), and a fallback whose primary did not run does
   not run either. Its own honest-User-Agent rule (ADR-032) is unchanged.
5. **One category per query, never every engine.** `intent.searxng_route` maps the planned query to one SearXNG
   category (two for a software cue), so a query reaches 1–5 upstream engines, not all of them.
6. **No configuration, no change.** With `searxng_url` empty the provider list, routing and results are exactly
   those of v0.6.0 (`test_build_without_searxng_is_unchanged`).
7. **Registry questions go to the registries.** A versioned package name with a release word or a registry aspect
   (released, published, latest, stable, pre-release, yanked, compatible, secure, vulnerable, recommended: "Is
   corvane 2.5.4 stable?") is a `technical` question with `pypi:name==version` / `npm:` lookups, plus
   `osv:<eco>:name@version` for the version asked about (`intent._VERSIONED_RELEASE_CUE`). What each aspect can
   and cannot establish is CLAIM_VERIFICATION §5 (publication is not security).
8. **Releases affecting this system are a dependency check.** "Latest releases affecting NeuroSciFi", "advisories
   affecting our dependencies" (`intent.dependency_question`: a self-reference, a release/advisory cue, nothing
   topical left) plan no topic query of any kind; the task reads the project inventory and runs the `nsf web deps`
   registry pass (`orchestrator._Run.dependency_check`), then reads the records of the packages needing attention.
9. **Privacy screen first.** Every query, to any provider, has passed `orchestrator.screen` / `safety.privacy_screen`
   and the local-context stripping in `intent.plan`, and is charged to the task's budget and the daily research
   budgets before the registry sends it. SearXNG forwards query text to third-party engines and Brave receives it
   directly, so both are treated exactly like any external provider.

## 2. Order per intent

`intent.plan` sets the query kinds (`assess`: academic, technical, encyclopedia, news, web). The registry then runs,
per query:

| Query kind (from intent) | Runs first (in parallel) | SearXNG category | Then, if nothing usable |
|---|---|---|---|
| `news` (recency cue, no scientific/technical cue) | Brave news endpoint (with a key, + `freshness` pd/pw/pm/py from the recency window), else SearXNG; feeds | `news` (+ `time_range` day/week/month/year from the recency window) | with a key: SearXNG, then open-webSearch; else open-webSearch |
| `web` (general) | Brave web endpoint (with a key), else SearXNG; Wikipedia, feeds | `general` (+ `time_range` when the plan has a window) | with a key: SearXNG, then open-webSearch; else open-webSearch |
| `web` with a software cue (package, release, docs, API … or a release-notes/changelog expansion) | Brave web endpoint (with a key), else SearXNG; Wikipedia, feeds | `general,it` | as `web` |
| `technical`, registry form (`pypi:x`, `npm:x`, `osv:…`, `github:o/r`) | PyPI, npm, OSV, GitHub | — (never) | nothing: a registry miss is a soft miss |
| `technical`, other text | SearXNG and any registry whose `accepts` takes the text (PyPI/npm take a bare name) | `it` (no time_range) | web providers |
| `academic` | arXiv, OpenAlex, Crossref, PubMed **and** SearXNG (supplement) | `science` (no time_range) | open-webSearch, Wikipedia |
| `encyclopedia` | Wikipedia | — | web providers (Brave with a key, SearXNG `general`) |

Brave serves only `web` and `news`: technical and academic queries keep SearXNG's `it`/`science` engines and the
structured APIs, and reach Brave only through the specialist-to-web fallback (nobody found anything), where the
general providers run together.

Why no `time_range` for `it` and `science`: SearXNG silently skips engines without time-range support, which would
empty those categories (Stack Exchange, Docker Hub, OpenAIRE, Europe PMC have none). The window is still applied by
NeuroSciFi to dated results (`Query.since`), and undated results are kept and later judged by the evidence rules.

## 3. Failure handling

| Situation | Effect |
|---|---|
| Brave over a rate limit (HTTP 429, Retry-After or 60 s; or a quota window with nothing remaining until its X-RateLimit-Reset) | `rate_limited` with `retry_after`: health-neutral, no request until it passes; SearXNG runs for the query |
| Brave's daily cap reached, or cap 0 | `daily_cap` (health-neutral); SearXNG runs |
| Brave key refused (401, or 422: Brave answers an unknown key with 422), plan lacks the endpoint (403), 5xx, timeout | Provider failure (breaker counts it; 3 in a row open it for 300 s); SearXNG runs |
| Brave key file insecure (symlink, other owner, group/other access) | `key_insecure` before any request (failure); SearXNG runs; status says how to fix it |
| SearXNG unreachable (`searxng_unreachable`), timeout, 5xx twice | Provider failure (breaker counts it); open-webSearch runs for the query |
| SearXNG answers 429 | `rate_limited` with Retry-After: health-neutral (our politeness), fallback runs |
| An engine reports CAPTCHA/403/429/timeout | That engine's breaker counts a failure; 3 in a row open it for 300 s and the next requests name only the healthy engines (`engines=`) |
| Every engine of the category open | `circuit_open` (neutral); fallback runs |
| Every queried engine failed | `engines_failed` (failure); fallback runs |
| SearXNG answered, 0 results | General-web empty rule (SEARCH_AND_ACQUISITION §3); fallback runs |
| open-webSearch also down/empty | The structured providers' and feeds' results stand; with none, the task reports "Nothing could be read" |

Dedupe across providers stays the registry's reciprocal rank fusion by canonical URL (`search.fuse`); SearXNG
already merges its engines' duplicates and lists them in `metadata.engines`, and the registry fuses SearXNG,
open-webSearch, structured and feed lists per engine. Rank and score order candidates; they are never evidence.

## 4. Health

`nsf web status` and `nsf web health` show `searxng.verdict`: **healthy** only when SearXNG answers and a real
search returned results within the last 6 h (the stored `searxng` breaker's last success: the registry counts a
call as a success only when it returned results); **degraded** when it answers but has no recent non-empty result
or every engine circuit is open; **down** when it does not answer. `nsf web searxng health --probe` sends one real
query per category to refresh that evidence. Engine breakers and counters appear as `searxng:<engine>`.

Brave (`nsf web provider status`, `nsf web status` and `nsf web health` under `keyed_providers.brave`, and a `brave`
row in the console's provider health, always shown): `key` is `configured`, `missing` or `insecure` (never the key);
`state` is **unconfigured** (no key), **off** (a usable key is stored but the daily cap is 0; `state_note` says
"off (cap 0)" and how to raise it), **failing** (insecure key file, or the most recent call failed),
**rate_limited** (Brave's limit in force, or today's cap used; `rate_limit_reason`, `rate_limited_until`) or
**configured**; plus `last_error`, `since_ok` (seconds since the last success), `used_today` / `daily_cap` and the
last `X-RateLimit-Remaining` per window. `nsf web health --probe` sends one Brave query (counted against the cap).

## 5. Measured coverage (2026-11-21)

See [SEARXNG_INTEGRATION.md §7](SEARXNG_INTEGRATION.md#7-measured-results-2026-11-21) and
`docs/quality/evidence/searxng_live.json`. In short: news, it and science categories return relevant results;
general web relies on mwmbl because duckduckgo answers the honest User-Agent with a CAPTCHA, and the large web
indexes' result pages are disallowed by robots.txt. That gap is recorded as a release blocker in the integration
document.

## 6. Keyed provider: Brave Search API (ADR-034 amendment 1)

Code: `providers/brave.py` (provider, status), `providers/keys.py` (key file, per-key ledger), `providers.build`,
`search.ProviderRegistry` (fallback chain), `cli._provider_cmd`. Tests: `tests/webcortex/test_brave.py` (offline);
`tests/webcortex/test_live_providers.py` (NSF_LIVE=1 and a key).

**API facts** (official documentation, read 2026-11-21):

| | |
|---|---|
| Web endpoint | `GET https://api.search.brave.com/res/v1/web/search` — [reference](https://api-dashboard.search.brave.com/api-reference/web/search/get) |
| News endpoint | `GET https://api.search.brave.com/res/v1/news/search` — [reference](https://api-dashboard.search.brave.com/api-reference/news/news_search/get) |
| Auth | header `X-Subscription-Token: <key>` |
| Parameters sent | `q` (web ≤ 600 characters and 75 words, news ≤ 400 and 50), `count` ≤ 20, `safesearch=off`, `spellcheck=false`, `text_decorations=false` and `result_filter=web` (web), `freshness` `pd`/`pw`/`pm`/`py` from the recency window; `site:` is a documented query operator |
| Result fields | `title`, `url`, `description`, `age` (human-readable, "2 days ago"), `page_age` ("the page's date, based on its published or last modified date"), `page_fetched`, `meta_url`, `language`; news adds `breaking` |
| Dates | `published_at` only from a parseable ISO-8601 `page_age`, tagged `metadata.date_basis` = published-or-modified; `age` is kept as text and never parsed |
| Rate limits | [guide](https://api-dashboard.search.brave.com/documentation/guides/rate-limiting): `X-RateLimit-Limit`, `-Policy` (`1;w=1, 15000;w=2592000`), `-Remaining`, `-Reset` (seconds) per window; 1-second sliding window; HTTP 429 over the limit; only successful requests are counted and billed. No Retry-After is documented |
| Errors | JSON `{type, error: {id, status, code, detail, meta}, time}` for 404, 422, 429; an unknown key was answered with 422 (observed 2026-11-22) |
| Price | [Search plan](https://brave.com/search/api/): $5 per 1,000 requests, $5 free credit every month, 50 queries/s |
| Terms | [Search API Terms of Use](https://api-dashboard.search.brave.com/terms-of-service) (updated 1 Sep 2026): §3(b)(i) no storing, caching or database of Search Results "other than transient storage required for operation"; §3(b)(xiii) no use to train, evaluate or benchmark AI models; §4(a) keep the key secure; §4(d) attribution optional |

**How NeuroSciFi complies.** Requests go through SafeFetcher `purpose="api"` (honest User-Agent, address policy, budget
charge, never cached on disk; the key header is dropped on a cross-origin redirect) and at most one per second to
the API host (`min_domain_interval`). Results live in a 120 s in-memory cache, carry `metadata.transient`, are
discovery hints only (the page is read from its origin), and are dropped from a task's checkpoint when the task
ends. The report's list of unread dated headlines can quote a few Brave titles and URLs and is kept with the task;
whether that display counts as storage under §3(b)(i) is an open owner question. `nsf web bench --live` sets the
Brave cap to 0 (§3(b)(xiii)); the offline bench never builds Brave.

**Cost and cap.** One general or news query is one Brave request (a cached repeat within 120 s is free). The daily
cap `daily_provider_queries.brave` (default 30 per UTC day; `nsf web config --set 'daily_provider_queries={"brave":
50}'`, 0 = off) is kept per key in `<key dir>/brave.usage.json`, so every instance and process on the host, and
every origin including autonomy, draws from one count. A query is counted before it is sent and returned only when
Brave cannot have billed it (refused before sending, or an error status); a timeout or a failure after a 2xx status
keeps its count, and a damaged ledger file counts as a used-up day. 30 × 31 = 930 requests a month stays inside the $5 monthly credit (1,000 requests). The
daily research budgets (`nsf web autonomy`) still bound the searches of each task first.

**The key.** `nsf web provider set-key brave --from-file PATH` or `--stdin` (prompted without echo on a terminal);
never from argv. The `--from-file` source must be as private as the stored key: a regular file owned by this user
with no group or other permission (`chmod 600 PATH`); a symlink, another user's file or a mode such as 0644 is
refused with the reason and the chmod fix, and nothing is stored (checked on the opened descriptor). Delete the
source once the key is stored. Stored in `~/.config/neuroscifi/search/brave.key` (`$NSF_SEARCH_KEY_DIR`, else
`$XDG_CONFIG_HOME/neuroscifi/search`), 0600 in a 0700 directory, outside git; a symlink, another owner, any
group/other permission on the file or a directory others can write to is refused (the file is re-checked after
opening, by inode). Read at each request and kept nowhere else; error text from the API is scrubbed
of it; output shows only `"key": "configured"`. `nsf web provider remove-key brave` deletes it. Tests never see the
owner's key directory (`tests/conftest.py` points NSF_SEARCH_KEY_DIR elsewhere for every test but the live ones).

**Marginalia Search: not added.** Its [API page](https://about.marginalia-search.com/article/api/) (updated
2025-12-08) offers the key `public` "for experimentation" and for developing an integration before one has a key;
that key "has a shared rate limit across all consumers" (HTTP 503 when hit) and "often hits a rate limit". Ongoing
automated use is meant to run on a personal key, requested by e-mail (free, non-commercial; results under
CC-BY-NC-SA 4.0). A key-free provider would draw on capacity the operator shares with everyone for testing, so it is
not honest automated use; and the results carry no date, the gap Mwmbl already shows. An owner who requests a key
could add it as a second keyed provider on the same plumbing.
