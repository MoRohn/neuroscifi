# Web Cortex: security model

Status: describes the code committed at `5f09ac3`. Controls are mapped to code and to the test that verifies them; where no test exists the entry says so. Interface surfaces (CLI, console, Pocket, MCP) were being written when this was authored; for them this document states the binding design from [ARCHITECTURE.md](ARCHITECTURE.md) §3 and §4.5 and ADR-028, and says what is not in the committed code. Related: [SEARCH_AND_ACQUISITION.md](SEARCH_AND_ACQUISITION.md), [EVIDENCE_AND_PROVENANCE.md](EVIDENCE_AND_PROVENANCE.md), [MEMORY_INTEGRATION.md](MEMORY_INTEGRATION.md), [DEPENDENCY_REVIEW.md](DEPENDENCY_REVIEW.md). Operation: see RUNBOOK.

## 1. Scope and assumptions

Web Cortex gives one individual's NeuroSciFi instance a governed path to the **public** web. Until 0.6 nothing in the project contacted a network service (ADR-021). The security question is what that path adds to the attack surface and what leaves the machine through it.

Assumptions:

- The host is single-owner and runs the service as an unprivileged user. The state directory is on an encrypted volume (ADR-011).
- Everything that arrives from the network is hostile until measured: search results, page text, titles, metadata, feeds, PDFs, API fields, `robots.txt`.
- The package contains no model that could be instructed (ADR-022). Injection harms the pipeline's *data* (poisoned claims, wasted budget, leaked queries), not an agent's actions. A downstream agent that reads reports or MCP output must still treat them as data.
- TLS trust, DNS and the operating system's resolver are those of the host.

Not in scope: private networks, authenticated sites, paywalls, the owner's own files. None of these is fetched.

## 2. Assets and adversaries

| Asset | Why it matters |
|---|---|
| Private memory (evidence journal, beliefs, partner and camera roots, objectives) | Must not leave in a query or log |
| Individual's knowledge base | Must not be poisoned silently |
| Local network and host (loopback services, LAN, metadata endpoints) | Must not be reachable through the fetcher or browser |
| CPU, memory, bandwidth, request quota | Must be bounded per task and per day |
| Third-party sites | Must not be harmed or deceived by the crawler |

| Adversary | Capability |
|---|---|
| Malicious website or search result | Chooses content, redirects, DNS answers, headers, response timing |
| Compromised or noisy search engine or API | Returns false or hostile records |
| Network observer | Sees destination hosts and timing (TLS hides paths and content) |
| Paired Pocket device on the LAN | Holds a bearer token; can reach the Pocket bridge |
| Other local user or process | Can reach loopback ports and read files the OS allows |

## 3. Threat, control, code, test

| # | Threat | Control | Code | Test |
|---|---|---|---|---|
| 1 | SSRF to loopback, RFC 1918, link-local, cloud metadata, CGNAT | Every resolved address must be globally routable; IPv6 forms embedding a refused IPv4 refused; any forbidden answer refuses the host | `net.check_address`, `SafeFetcher._resolve` | `test_forbidden_addresses`, `test_public_addresses`, `test_mixed_answers_refused`, `test_special_hosts_refused_without_connecting` |
| 2 | Literal or obfuscated IP hosts (`0x7f000001`, `2130706433`, `127.1`), `localhost`, `.internal`, `.local` | Refused at URL validation, before any resolution | `net.check_url` | `test_check_url_refuses`, `test_literal_ip_urls_never_connect` |
| 3 | DNS rebinding (public answer, then private) | Resolve once, validate, connect to the validated IP, never re-resolve; TLS verifies the original host name | `SafeFetcher._exchange` (pinned connection) | `test_dns_rebinding_pins_the_validated_address` |
| 4 | Redirect to an internal target, downgrade, loop | Manual redirects, at most 5; every hop re-validated (URL, address, robots); https to http refused | `SafeFetcher._request` | `test_redirects_to_internal_targets_refused`, `test_https_to_http_downgrade_refused`, `test_redirect_loop_and_limit` |
| 5 | Credential or cookie leakage | No cookies sent; `Set-Cookie` discarded; credential headers dropped on origin change; caller cannot override `Host`, `User-Agent` | `net._FORBIDDEN_HEADERS`, `_request` | `test_set_cookie_not_kept_and_host_header_original` |
| 6 | Oversized or endless responses | Body cap (pages truncated, API and feeds rejected), 15 s wall clock, content-type allow-list before reading | `SafeFetcher._read_body` | `test_size_limit_truncates_pages_and_rejects_api`, `test_slow_route_times_out`, `test_disallowed_content_type`, `test_connection_close_response_read_under_deadline` |
| 7 | Decompression bomb | gzip/deflate decoded under the same cap; stops at the cap | `_read_body` | `test_gzip_bomb_is_capped`, `test_gzip_and_deflate_decoded` |
| 8 | XML entity expansion and XXE (feeds, sitemaps, Atom from arXiv) | Any `<!DOCTYPE` or `<!ENTITY` refused; non-ASCII-compatible encodings refused; `xml.etree` only | `extract._safe_xml`, `providers/feeds.parse_feed_minimal`, `structured._safe_xml` | `test_hostile_or_broken_xml_refused`, `test_arxiv_refuses_doctype_and_reports_api_errors`, `test_feed_parser_refuses_doctype_and_all_failing_raises` |
| 9 | Hostile HTML (huge or deeply nested trees) | `Selector(..., huge_tree=False)`; output caps (500,000 characters of text, 200 links) | `extract.extract_html` | Not directly tested. Checked by hand at authoring: 100,000 nested `<div>` returned without error; a 4.8 MB page of 400,000 paragraphs took 6.7 s to extract on the development host (section 9) |
| 10 | Malicious PDF | Page bound (30), character bound, encrypted and unreadable files rejected as `decode_error`; body already capped at 5 MB | `extract.extract_pdf` | `test_pdf_encrypted_or_garbage_is_decode_error`, `test_pdf_text_and_metadata` |
| 11 | Prompt injection in page text, titles, snippets, API fields | Sentences matching 17 agent-directed patterns are removed and never become claims; hidden content (CSS-hidden, `aria-hidden`, templates, zero-width characters) removed before text is taken; findings reported under `uncertainties` | `safety.scan_injection`, `quarantine`, `extract._clean`, `claims.extract_claims` | `test_injection_positives`, `test_injection_negatives`, `test_quarantine_removes_only_flagged_sentences_and_copies`, `test_hidden_injection_removed_and_counted`, `test_extract_claims_skips_injection_and_irrelevant_sentences`, `test_metadata_with_injection_is_dropped` |
| 12 | Injection that acts (starts a fetch, creates a monitor, changes config) | Content is only stored, ranked and quoted. Fetches follow the task's plan and budget; at most 2 same-site links in `deep_research`; no tool or config path exists from content | `orchestrator` (`select`, `follow`) | `test_page_budget_allows_exactly_one_fetch`; structural (no code path), no single test |
| 13 | Data exfiltration through search queries | `privacy_screen` on every objective and every planned query; private terms, evidence-journal roots, e-mail, phone, IP, MAC, file path, key, card and long secret patterns blocked; queries are keyword extracts, not the whole question | `safety.privacy_screen`, `orchestrator.submit`, `_Run.screened`, `scheduler.monitor_add` | `test_privacy_positives`, `test_privacy_negatives`, `test_private_terms_whole_word_case_insensitive_and_masked`, `test_privacy_excerpts_never_echo_the_secret`, `test_privacy_screen_blocks_private_identifiers`, `test_monitor_lifecycle_and_validation` |
| 14 | Exfiltration through URLs in a question | A URL the owner names is fetched as written; nothing else puts private text in a URL. Content-derived URLs are same-site links only | `intent.assess`, `orchestrator.follow` | no single test |
| 15 | Query leakage into logs and error messages | `FetchError.url` drops the query string (`?…`); events store query text only in the owner's own database; engine and provider errors carry codes | `net._redact` | Checked by hand at authoring (`blocked_policy: http://a.example/search?…`); no committed test asserts it |
| 16 | Queries stored in the public cache | API responses (whose URLs embed queries) are never cached; cache keyed by URL and content hash only | `SafeFetcher._fetch`, `cache.PublicCache` | `test_api_responses_are_not_cached`, `test_put_lookup_body_content_addressed` |
| 17 | Browser escape or network access by rendered pages | Fresh context, Chromium sandbox on, no profile, downloads and permissions denied, service workers blocked; every sub-request routed through `SafeFetcher`; Chromium's DNS disabled and a dead proxy set; WebSocket, WebRTC and EventSource constructors throw; off by default | `browser.BrowserRenderer` | `test_render_executes_js_through_safe_fetcher`, `test_render_requires_browser_budget`, `test_render_refuses_internal_targets_before_launch` |
| 18 | Local search daemon abuse | Client accepts only a plain-HTTP loopback origin; calls `/health`, `/status`, `/search` only; ignores proxy variables; query capped at 512 characters, limit at 20; the daemon is started bound to loopback and stopped only if it is ours | `providers/openwebsearch.py`, `state._validate` | `test_non_loopback_urls_refused`, `test_request_body_is_bounded_and_site_applied`, `test_daemon_refuses_non_loopback_host`, `test_daemon_stop_never_kills_unrelated_pid`, `test_config_defaults_validation_and_persistence` |
| 19 | Resource exhaustion by a task or by autonomy | Per-mode budgets clamped to an owner ceiling; one request charged before each network call; byte cap before reading; daily autonomous caps | `types.Budget/Usage`, `config.budget_for`, `scheduler._over_cap` | `test_budget_counts_robots_redirects_and_bytes`, `test_byte_budget_never_exceeded`, `test_budget_exhausted_before_exceeding`, `test_page_budget_allows_exactly_one_fetch`, `test_tick_respects_the_cap_for_curiosity_and_the_master_switch` |
| 20 | Hammering a site; ignoring `robots.txt` | robots always obeyed for pages, feeds and crawls; per-host interval; `Crawl-delay`; `Retry-After` backoff; requests inside a backoff never touch the network | `SafeFetcher._request`, `_throttle`, `_check_backoff` | `test_robots_disallow_blocks_without_requesting`, `test_crawl_delay_and_domain_interval`, `test_retry_after_seconds_sets_backoff`, `test_retry_after_http_date_on_503`, `test_robots_404_allows_and_500_disallows` |
| 21 | Web content silently becoming knowledge | New knowledge is only `provisional`, needs independent agreement, and every acceptance is an owner decision through `pending` | `consolidate.run`, `knowledge.apply_review`, `executive.resolve` | `test_two_independent_agreeing_sources_become_provisional_with_a_question`, `test_weak_evidence_is_held_as_claims_only`, `test_resolve_confirm_accepts_and_reject_rejects_in_one_transaction` |
| 22 | Cached or resumed content presented as current | `retrieval_mode` on every source; `live` false unless a cited source was retrieved in the task; limitations name cache age | `synthesis.synthesize` | `test_cache_only_report_is_not_live`, `test_resume_after_a_crash_at_reading_uses_cached_documents` |
| 23 | Stale private data after deletion | Forget cascades through tasks, claims, knowledge, questions, predictions; checkpoints marked stale; WAL truncated; deletion log holds ids only | `governance.forget_research/knowledge/source` | `test_forget_research_erases_the_task_and_knowledge_resting_on_it` (searches the database file and its WAL for the deleted text), `test_forget_source_by_domain_and_url` |
| 24 | Pocket device reaching other families' research or privileged actions | Off unless `pocket_research`; tasks carry a salted family digest and are read with a family filter (others get `NotFound`); Pocket-proposed monitors need owner confirmation; no accept, config or forget path | `state.task(family=)`, `state.family_digest`, `scheduler.monitor_add` | `test_family_digest_is_salted_and_stable`, `test_pocket_monitor_proposal_is_authorised_through_resolve`. The Pocket HTTP actions are in the interface lane, not in `5f09ac3` |

## 4. Privacy

**What may leave the machine.** Only what the owner's objective yields after screening: keyword queries to the loopback daemon (which forwards them to the search engines) and to the structured APIs, and the URLs fetched. Destination hosts and timing are visible to the network and, for DNS, to the host's resolver. Engines and APIs see the query text and the host's IP address; the project cannot prevent that. A *public* term in a query still reveals interest in that topic.

**Screen** (`safety.privacy_screen`). Detects: e-mail addresses; phone numbers (9-15 digits); IPv4 and IPv6 literals (an IPv4 right after "version", "release" or "v" is a version, not an address); MAC addresses; absolute file paths (`/home/...`, `~/...`, `C:\...`); API-key prefixes (`sk-`, `ghp_`, `AKIA`, `xox*-`, `AIza`, ...); Luhn-valid card numbers; long mixed-case or base64-like secrets of at least 24 characters; and **private terms** (whole word, case-insensitive). Findings carry the kind and a masked excerpt, so they can be logged safely (`test_privacy_excerpts_never_echo_the_secret`). An empty list is the only "safe to send" signal.

**Private terms** are the owner's `private_terms` plus every root of the evidence journal on the `camera` and `word` (partner) channels, read at screening time (`orchestrator.private_terms`). Erased roots leave the journal and so leave the list; erasure rewrites their mentions to salted digests (ADR-019).

**Where the screen runs.** On the objective at `submit`: a finding rejects the task, and the error names the kinds, not the text. On every planned query: a finding drops that query and logs the count and kinds (`decision` event). On monitor topics. Monitors and curiosity build queries from public claim text only.

**Authorization behaves more strictly than its name.** `submit(..., authorization="owner-approved-disclosure")` lets an objective that contains private data be *stored*. Planned queries are screened without that exception, so queries that contain the private term are still dropped. Checked by running it: three queries dropped, none sent. A private term therefore never leaves in a query. This differs from ARCHITECTURE §3, which says the screen can be passed by explicit authorization.

**Storage.** Objectives, queries (as events), claims and knowledge live in the individual's file under its protections (mode 0600, volume encryption). The public cache holds public bytes keyed by URL and content hash, no queries, no objectives, no claims, and is excluded from checkpoints (ADR-023). `purpose="api"` responses are never cached.

**Not stored.** Cookies; response bodies in logs; query strings in error messages; the host's name (only a 12-character SHA-256 digest in `executed_on`).

## 5. Responsible acquisition

| Practice | How it is enforced |
|---|---|
| `robots.txt` always on | Constant, not a setting (`config.RESPECT_ROBOTS`). Applies to `page`, `feed`, `crawl`, browser sub-requests; documented public APIs (`purpose="api"`) are exempt. Failure semantics in SEARCH_AND_ACQUISITION §6: a 5xx or an unreachable `robots.txt` blocks the host |
| Identifiable agent | `NeuroSciFi-WebCortex/0.6 (research agent; honours robots.txt)`. No browser impersonation, no header forging. No contact address is sent, which protects the owner's identity but gives site operators no way to reach the owner |
| Rate limits | Default 1 request per second per host (up to 3600 s configurable), `Crawl-delay` up to 60 s, `Retry-After` honoured up to 6 h, 429 backs the host off. arXiv asks for one request per 3 s; no per-host rule exists, so the default 1 s interval does not meet it unless the owner raises `min_domain_interval` (DEPENDENCY_REVIEW §4 says the throttle enforces it; the code does not) |
| No anti-bot circumvention | No Cloudflare or CAPTCHA solving, no TLS fingerprint spoofing, no proxy rotation. `scrapling[fetchers]` is deliberately not installed (ADR-024, DEPENDENCY_REVIEW §2). Blocked pages are reported unavailable |
| No authentication or paywall bypass | No credentials are sent; no login flow exists; `Authorization` is never set by this code |
| Bounded use | Task budgets, daily autonomous caps, page and byte limits; the crawler follows at most 2 same-site links per task |
| Attribution | Every claim keeps its source URL, domain, dates and content hash; reports cite every statement; quotes are single sentences, not whole pages |

The search daemon scrapes public engine result pages. Whether that is acceptable under an engine's terms is the owner's decision; the daemon's own behaviour is outside this project's control (DEPENDENCY_REVIEW §3).

## 6. Read-only defaults and separation of privileged tools

- **Default state is off.** `enabled`, `autonomous`, `pocket_research` and `browser_rendering` are all false (`config.WebConfig`); every network entry point calls `state.require_enabled`. A fresh individual behaves exactly as 0.5.
- **The web path is read-only toward the world.** Only GET and HEAD, plus POST for documented read APIs (OSV). Nothing posts forms, submits data or acts on a site.
- **Web content never reaches privileged operations.** Configuration (`state.set_config`), forgetting (`governance.*`), acceptance (`knowledge.apply_review` through `nsf resolve`) and monitor authorisation are owner actions on separate code paths. No function in `webcortex` takes content as a command.
- **MCP.** The design (ARCHITECTURE §4.5) is a stdio server with read-mostly tools `web_search`, `web_research`, `web_task`, `web_evidence`, `web_knowledge`, all output wrapped in `safety.untrusted()` envelopes, and **no forget or config tool**. It is not part of `5f09ac3`; its own tests must assert the tool list. `safety.untrusted` and `synthesis.context_pack` (envelopes) are tested (`test_untrusted_envelope`, `test_context_pack_respects_token_bound_and_wraps_untrusted`).
- **Autonomy is capped and scoped.** Refresh and curiosity research only touch items already in knowledge or owner monitors, within `daily_autonomous`; new topics are proposals the owner must confirm (MEMORY_INTEGRATION §7-8).

## 7. Surfaces

| Surface | Exposure | Control |
|---|---|---|
| Console | Loopback only, bearer session token (ADR-013) | Owner-only; may change configuration. Console routes are in the interface lane |
| Pocket bridge | LAN or VPN, TLS required off loopback, bearer and pairing (ADR-028) | Research off by default; four actions (submit up to 300 characters, list, read, cancel) scoped to the device's family; cannot accept knowledge, change configuration or forget; monitors it proposes wait for the owner |
| open-webSearch daemon | `127.0.0.1:3210`, no auth | Reachable by any local process; its only client here is `providers/openwebsearch.py`. Any local user can run searches through it. Acceptable on a single-owner host; do not bind it elsewhere |
| Public cache file | Same directory as the individual file | Mode 0600, under the 0700 state directory |

## 8. Encryption at rest and retention

- **Encryption at rest** is delegated to the volume (ADR-011): files are 0600 in a 0700 state directory and an encrypted volume is a deployment precondition. Web Cortex adds no application-level encryption; the public cache is likewise on that volume. `nsf doctor` reports the filesystem type but cannot prove encryption.
- **Public cache retention.** Bounded by size (`cache_max_bytes`, 512 MiB) and age (`cache_max_age_days`, 30) through `PublicCache.prune` (oldest first); `secure_delete` is on and the WAL is truncated after pruning and forgetting (`test_prune_by_age_and_size_lru`, `test_forget_url_and_domain`). No committed code calls `prune` on a schedule; see section 9.
- **Task, claim, knowledge retention** is "until forgotten" (visible in `memory.retrieve` permissions). There is no automatic expiry; the owner forgets by task, source or knowledge item (MEMORY_INTEGRATION §6).
- **Checkpoints** exclude the public cache; forgetting marks existing checkpoints stale.

## 9. Accepted and residual risks

Accepted, with reasons:

| Risk | Why accepted |
|---|---|
| `zod < 4.6.6` advisory (GHSA-mrhx-c7xc-m3w5) reachable through open-webSearch | The daemon binds 127.0.0.1; its only client sends bounded requests (query 512 characters, limit 20, fixed engines). Revisit when upstream moves to a fixed `zod`. DEPENDENCY_REVIEW §3 |
| Regex-based injection detection | It is a filter, not a proof. English, imperative, agent-directed phrasing only; paraphrase, other languages and instructions split across sentences can pass. The defence in depth is that content has no path to act (control 12) and flagged or unflagged claims still need independent agreement and owner acceptance |
| Text hidden by stylesheet rules is kept | The cleaner understands inline styles, `hidden`, `aria-hidden`, templates and common hiding class names. Text hidden by an external or `<style>` rule with arbitrary class names looks visible to it. The rendering path does not change that |
| Coalesced requests share one result | Identical concurrent GETs get the leader's response; only the leader's budget is charged, so a follower task is not charged for the request it used, and it inherits the leader's failure |
| Per-process throttle, backoff and robots state | Two processes (worker and a manual fetch) can together exceed the per-host interval. State is not shared |
| Stdlib `robots.txt` parser | `urllib.robotparser` does not implement `*` and `$` in rule paths (RFC 9309). At authoring, `Disallow: /*.pdf$` did not block `/x.pdf` on the project's Python 3.12. A site relying on wildcard rules can be fetched where it asked not to be. Plain prefix rules, `Allow`, `Crawl-delay` and `Sitemap` work |
| `robots.txt` 401/403 treated as "no file" | Any 4xx allows the host. This follows the common reading of RFC 9309 for unavailable files but is permissive |
| Unofficial engine scrapers | Reliability and terms-of-service exposure are the daemon's (DEPENDENCY_REVIEW §3) |

Residual, not mitigated by code:

- **Extraction CPU is not bounded by the fetch timeout.** The 15 s limit covers the network read. A 4.8 MB, 400,000-paragraph page took 6.7 s to extract on the development host; a crafted PDF can cost more. Budgets bound the number of documents, not the work per document.
- **Chromium vulnerabilities.** The sandbox is on and rendering is off by default, but a renderer exploit would run as the service user. Systemd hardening of the service units is in the deployment lane, not in `5f09ac3`.
- **Traffic analysis.** Hosts contacted, timing and query text to engines are visible to those parties.
- **Cache not purged by forgetting.** `forget_research` leaves public page bodies in the cache; `forget_source` names the cache keys to drop but does not touch the file, and nothing in the committed code calls `PublicCache.forget_url`, `forget_domain` or `prune`. An interface that exposes forgetting must drop the keys; until it does, a forgotten source's public bytes can remain on the volume. They hold no queries or objectives.
- **Third-party text is stored** (sentence-level quotes in claims, up to 5 MB bodies in the cache). Copyright and retention of that text are the owner's responsibility; forgetting removes it.
- **Pocket compromise** lets an attacker spend the owner's request budget and read that family's reports, not other families' tasks or the private memory.
- **Local processes** can query the loopback daemon and, if the OS allows, read files in the state directory.

## 10. How to verify

```sh
pytest -q tests/webcortex/test_net.py tests/webcortex/test_safety.py tests/webcortex/test_extract.py
pytest -q tests/webcortex/test_openwebsearch.py tests/webcortex/test_browser.py tests/webcortex/test_cache.py
pytest -q tests/webcortex/test_instance_integration.py tests/webcortex/test_orchestrator.py
pytest -q tests/webcortex -k "privacy or forget or robots or injection or rebinding or budget"
pytest -q tests/instance -k erasure        # the individual's own erasure tests still pass
NSF_LIVE=1 pytest -q -k live tests/webcortex   # optional: real TLS and provider smoke tests
```

At `5f09ac3`, `pytest -q tests/webcortex` reported 609 passed and 6 skipped (the `live` tests). A reviewer who wants to challenge a row of section 3 should read the named test and try to break it; rows marked "not directly tested" or "no single test" are the weakest.
