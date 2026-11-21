# Web Cortex: search provider strategy

Status: v0.7.0 working tree (Lane A). Which provider answers which query, in what order, and what happens when one
fails. Mechanics of each provider: [SEARCH_AND_ACQUISITION.md](SEARCH_AND_ACQUISITION.md) (registry, breakers,
fusion) and [SEARXNG_INTEGRATION.md](SEARXNG_INTEGRATION.md) (SearXNG). Decision: ADR-034 (draft
`.omc/autopilot/adr/ADR-034-searxng.md`), which supersedes ADR-032 and amends ADR-025.

## 1. Rules

1. **Structured sources first, and direct.** Literature (arXiv, OpenAlex, Crossref, PubMed), registries (PyPI, npm,
   OSV, GitHub releases) and Wikipedia are called through their own APIs (`providers/structured.py`,
   SafeFetcher `purpose="api"`). They are never routed through SearXNG, and SearXNG does not enable its engines
   for them. A registry lookup (`pypi:`, `npm:`, `osv:`, `github:`) never reaches a general search provider
   (`search.REGISTRY_QUERY`; `SearXNGProvider.accepts`).
2. **SearXNG is the primary general provider when configured** (`searxng_url`, set by `nsf web searxng start`).
   It is built first in the provider list.
3. **open-webSearch is SearXNG's fallback**, not a peer: with SearXNG configured it carries
   `fallback_for = "searxng"` and runs for a query only when SearXNG's circuit is open (same round) or SearXNG
   failed or found nothing (second round). Its own honest-User-Agent rule (ADR-032) is unchanged.
4. **One category per query, never every engine.** `intent.searxng_route` maps the planned query to one SearXNG
   category (two for a software cue), so a query reaches 1–5 upstream engines, not all of them.
5. **No configuration, no change.** With `searxng_url` empty the provider list, routing and results are exactly
   those of v0.6.0 (`test_build_without_searxng_is_unchanged`).
6. **Privacy screen first.** Every query, to any provider, has passed `orchestrator.screen` / `safety.privacy_screen`
   and the local-context stripping in `intent.plan`. SearXNG forwards query text to third-party engines, so it is
   treated exactly like any external provider.

## 2. Order per intent

`intent.plan` sets the query kinds (`assess`: academic, technical, encyclopedia, news, web). The registry then runs,
per query:

| Query kind (from intent) | Runs first (in parallel) | SearXNG category | Then, if nothing usable |
|---|---|---|---|
| `news` (recency cue, no scientific/technical cue) | SearXNG, feeds | `news` (+ `time_range` day/week/month/year from the recency window) | open-webSearch (fallback) |
| `web` (general) | SearXNG, Wikipedia, feeds | `general` (+ `time_range` when the plan has a window) | open-webSearch (fallback) |
| `web` with a software cue (package, release, docs, API … or a release-notes/changelog expansion) | SearXNG, Wikipedia, feeds | `general,it` | open-webSearch (fallback) |
| `technical`, registry form (`pypi:x`, `npm:x`, `osv:…`, `github:o/r`) | PyPI, npm, OSV, GitHub | — (never) | nothing: a registry miss is a soft miss |
| `technical`, other text | SearXNG and any registry whose `accepts` takes the text (PyPI/npm take a bare name) | `it` (no time_range) | web providers |
| `academic` | arXiv, OpenAlex, Crossref, PubMed **and** SearXNG (supplement) | `science` (no time_range) | open-webSearch, Wikipedia |
| `encyclopedia` | Wikipedia | — | web providers (SearXNG `general`) |

Why no `time_range` for `it` and `science`: SearXNG silently skips engines without time-range support, which would
empty those categories (Stack Exchange, Docker Hub, OpenAIRE, Europe PMC have none). The window is still applied by
NeuroSciFi to dated results (`Query.since`), and undated results are kept and later judged by the evidence rules.

## 3. Failure handling

| Situation | Effect |
|---|---|
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

## 5. Measured coverage (2026-11-21)

See [SEARXNG_INTEGRATION.md §7](SEARXNG_INTEGRATION.md#7-measured-results-2026-11-21) and
`docs/quality/evidence/searxng_live.json`. In short: news, it and science categories return relevant results;
general web relies on mwmbl because duckduckgo answers the honest User-Agent with a CAPTCHA, and the large web
indexes' result pages are disallowed by robots.txt. That gap is recorded as a release blocker in the integration
document.
