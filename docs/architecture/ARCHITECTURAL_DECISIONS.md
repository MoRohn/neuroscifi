# Architectural decisions

Format: context → decision → consequences. Reversibility: **R** means cheap to reverse; **H** means hard.

**ADR-001 Keep the prototype stack (Python 3.12/NumPy, vanilla JS PWA). R**
The prototype is a working, security-conscious Python kit with a strict-CSP, no-build PWA, and the brief's measurements come from that code. Rewriting it in another stack would discard verified numerics and the reviewed privacy surface. Decision: keep the stack and restructure it into an installable package (`neuroscifi`) with an `nsf` CLI. Consequence: `scripts/reproduce.py` is the tripwire for numerical drift.

**ADR-002 Modular monolith, not services. R**
The brief calls for one writer per mutable graph revision and a single individual per device. Separate services would add network boundaries without isolating anything useful. Decision: one package; the two HTTP servers are thin adapters over the same modules.

**ADR-003 One SQLite file per individual, WAL, forward-only migrations in `PRAGMA user_version`. H (data format)**
The prototype already used SQLite. The brief needs restartable state, exact records and consistent online backups (`sqlite3.backup`). Decision: a single file is the individual, and `store.create` refuses to overwrite an existing one. Consequence: per-table ownership by module (API_CONTRACTS §0) replaces process isolation.

**ADR-004 Lint with ruff `E9,F` only. R**
The prototype uses a dense style. Reformatting it would bury real changes and risk numeric edits. Decision: lint for real errors only. Consequence: style stays heterogeneous between prototype and new code.

**ADR-005 Keep the Pocket database separate from the individual. H (privacy boundary)**
The brief says Pocket receipts expire after 24 hours, are isolated per pairing, are excluded from backups, and "do not silently update" the binary calibration circuits (p.24, README). Merging them into the individual would put them in checkpoints and lineage. Decision: two databases. Consequence: there is no automatic consolidation of field episodes into the individual. The brief lists that as the "next consolidation experiment" (p.30), not a release feature.

**ADR-006 Beliefs are computed on read from active evidence, not cached. R**
Deletion must invalidate dependent beliefs immediately (p.14). At this scale exact factorised inference is microseconds per event. Decision: no belief cache; `belief_revisions` is an audit trail, not a source of truth. Consequence: forgetting needs no cache invalidation, and a restart reproduces beliefs exactly.

**ADR-007 Generalise the 8-state learner to per-camera-root and per-partner bits, with exact factorised inference. R**
The brief requires partner-specific conventions (p.14, p.19) and per-instrument calibration roots (p.12). Decision: hidden bits are w, s_r per camera root and t_p per partner. Inference factorises given w. Consequence: with one camera root and one partner the results equal `legacy_live` to 1e-12, and a parity test enforces this. Prototype behaviour that differs: two camera roots now get independent alignment bits, where the prototype shared one.

**ADR-008 Change detection is a penalised single-factor changepoint test. R**
"Discovering the onset during ordinary use remains a product requirement" (p.13). Decision: a bounded hypothesis set (mechanism, each camera, each partner) with a log(#τ) penalty and a log(19) threshold, plus an explicit "ambiguous" outcome when the runner-up is within 1 nat. That outcome is the brief's reserved unknown alternative. Consequence: simultaneous multi-factor changes are reported as ambiguous or attributed to the mechanism, and the executive then asks for a reference.

**ADR-009 No embedding or associative retrieval in this release. R**
There is no trained encoder (the brief lists "production neural encoders" as pending, p.19), and a fake embedding would be a placeholder. Decision: retrieval by exact key, time, epoch, context and graph version only. MEM-06b is deferred.

**ADR-010 NSG1 stays as the snapshot format; no branch or delay fields. H (format)**
Changing the format would invalidate the verified packing results. Decision: keep NSG1, with branch type and delay deferred to a future NSG2 (GRA-01b).

**ADR-011 Encryption at rest is delegated to the volume (LUKS or fscrypt). R**
The brief: "Use an encrypted volume and exclude the DB from backups", and the prototype "stores SQLite in cleartext within that volume". Application-level encryption would add a key-management design the brief does not specify. Decision: files are 0600 in a 0700 state directory, and the runbook makes the encrypted volume a deployment precondition. Consequence: a host without an encrypted volume is out of policy. `nsf doctor` reports the filesystem type but cannot prove encryption.

**ADR-012 Learning, recall over stored data, and checkpointing are explicit jobs. R**
The brief: "Decouple ingestion from training so incoming events do not contend with replies", and "Keep this optional slow path off the interactive loop" (p.25, p.16). Decision: `observe`/`decide` touch only the journal and inference. Graph learning and archiving run from CLI or console POSTs that the owner triggers. Consequence: there is no background scheduler in v1. Scheduling is a systemd timer, if wanted (runbook).

**ADR-013 A console for owner inspection, loopback only. R**
"The person can inspect which experience led to the change and which uncertainty remains" (p.19), and "Publish … memory inventory" (p.19). Decision: a small loopback web console reusing Pocket's design tokens. It is never exposed on the LAN. Consequence: remote inspection of the individual is not supported, which keeps the remote attack surface at Pocket's 4-field contract.

**ADR-014 Simulated acceptance scenarios stand in for physical milestones, and are labelled as simulated. R**
The brief's milestones (p.19, p.34) assume a Spark, cameras and people. Decision: `nsf acceptance` runs each milestone against simulated evidence with `provenance='simulated'` and reports its thresholds. Consequence: nothing in this release claims physical validation.

**ADR-015 The service worker precache list grows to include the manifest and icon. R**
Home-screen shortcuts (p.20) need a web app manifest. Decision: precache 8 assets instead of the brief's "six named program assets". Recorded as a deviation from p.29. **0.6:** grows to 9 with `research-ui.mjs` (cache `neuroscifi-pocket-shell-8`) so research findings saved in the encrypted vault reopen offline (ADR-028); API traffic is still never cached.

**ADR-016 Founding graph and development counters. R**
INS-01 says each instance has a founding scaffold, but the brief does not specify its contents. Inventing weights would be a placeholder. Decision: the founding graph is owner-supplied (`nsf init --founding-graph FILE.nsg`). Without one, birth records `initial_graph: null`. `replay_updates` counts graph operations over stored datasets (learn, evaluate, promote, lesion, recall). `teacher_interventions` counts owner answers to questions.

**ADR-017 A confirmed change splits only the affected factor (changepoints). R**
"Assigns learning to the affected scope" (p.5). Decision: `resolve(..., confirm=True)` records a changepoint (migration 3). The changed camera or partner bit gets a new segment from `since_seq` onward; a mechanism change makes the mechanism piecewise, with exact enumeration capped at 6 segments. Every other factor keeps pooling. With no changepoints, results are identical to ADR-007. Consequence: when the owner confirms the wrong scope, the error is carried forward. The detector named the correct scope in 92–94% of simulated changes.

**ADR-018 Earlier-setup recognition is Bayesian against a fresh-setup alternative; continuing links evidence. R**
Decision:
- `match_epochs` scores earlier epochs and their segments against the exact marginal likelihood of a new setup.
- It asks only at posterior ≥ 0.75. That threshold was declared before measurement.
- "Continue there" writes `epoch_links` (migration 4), and inference pools the linked source segments.

Consequence: with camera and word readings alone, recognition is bounded at a posterior of 2/3 (see VERIFICATION_REPORT §5, D-1). That is an owner decision, not a tuning knob.

**ADR-019 Erasure reaches derived memory through salted digests. H (privacy)**
When a root has no remaining evidence, every derived mention of it is rewritten to `camera:#d` or `partner:#d`, where d = sha256(instance_id:root)[:16]. This covers revision summaries, questions, decisions, changepoints and links. Forgotten event ids in causes become `#forgotten`, and the digests are recorded in `forgotten_roots` (migration 2) so a restore can re-apply them. After every destructive operation the WAL is truncated. Consequence: the semantic history keeps its shape but loses names. Tombstones keep event ids, which they need in order to block resurrection.

**ADR-020 The GPU is an optional extra. R**
The CUDA probe needs CuPy, which is large and specific to the CUDA driver. Decision: `pip install 'neuroscifi[gpu]'` installs `cupy-cuda13x[ctk]==14.2.0`, verified on GB10 with numpy 2.3.5. The core package and CI stay CPU-only.

## Web Cortex (0.6)

**ADR-021 Web access is an owner-enabled capability of one individual, off by default. R**
Until 0.6 nothing contacted a network service. The Web Cortex directive asks for internet perception; the project's
privacy posture asks that no network path exist unless the owner wants it. Decision: `meta.webcortex.enabled`
defaults to false and every network entry point calls `state.require_enabled`. Autonomous research, Pocket
research and JavaScript rendering are three further switches, also off. Respecting robots.txt and the private-address
block are constants, not settings. Consequence: a fresh individual behaves exactly as 0.5.

**ADR-022 No model in the loop: extractive synthesis and lexical ranking. R** (extended by ADR-036)
There is no language model, embedding encoder or reranker on the host or in the package (ADR-009 still holds).
Inventing one would be a placeholder. Decision: query planning, claim extraction, contradiction detection and
synthesis are deterministic and rule-based; ranking is BM25 plus recency and source independence. Every answer
sentence is a quoted source sentence or a template over measured quantities, with citations. Consequence: answers
are less fluent and contradiction detection is limited to numeric, version, date and polarity conflicts — measured
and reported in TEST_RESULTS. The GPU is not used for retrieval. A model can later implement the same contracts.

**ADR-023 Raw web content lives in a separate public cache; queries never do. H (privacy boundary)**
The directive asks to separate public research caches from private memory. Decision: downloaded bodies, robots
files and HTTP validators go to `<db stem>.webcache.sqlite` (0600), keyed by URL and content hash only. Task
objectives, queries, claims and knowledge live in the individual and are governed by forget, checkpoints and
erasure. The cache is excluded from checkpoints and pruned by size and age: `Services.prune_cache` applies
`cache_max_bytes` and `cache_max_age_days` through `PublicCache.maybe_prune` (at most hourly) after every run and tick,
and `nsf web cache prune` runs it at once. The cache also holds Retry-After backoffs (so every fetcher on it honours
them); forgetting a URL or domain deletes bodies no other row references. Consequence: restoring a checkpoint
restores knowledge and provenance but not page bodies; re-verification refetches.

**ADR-024 Our own SSRF-safe fetcher; Scrapling as the parser only. R**
Scrapling 0.5.1's fetchers impersonate browser TLS fingerprints by default and ship Cloudflare/Turnstile solving and
fingerprint spoofing; its redirect check is a post-hoc IP test without DNS pinning, and its spiders ignore robots.txt
by default. Decision: `net.SafeFetcher` (stdlib `http.client`) resolves, validates and pins every address, re-validates
every redirect, honours robots.txt (evaluated by its own RFC 9309 matcher, `net.RobotsRules`, because the stdlib parser
ignores `*` and `$`) and Retry-After, throttles per host (the largest of the default interval, a per-host interval such as
arXiv 3 s and Crawl-delay), and limits size, time and type; browser sub-requests use their own `subresource` purpose.
Bodies are parsed with
`scrapling.Selector(huge_tree=False)` and converted with `markdownify`. `scrapling[fetchers]` is not installed.
Consequence: no anti-bot circumvention exists in the code base; sites that require it are reported as unavailable.

**ADR-025 open-webSearch through its loopback REST daemon, with our own fallback. R**
open-webSearch 2.2.0 provides key-free multi-engine search. Its MCP HTTP mode binds 0.0.0.0 without auth, its
browser fetch path does not pin DNS, and it has no cross-engine fallback or rate limiting (a live run on 2026-11-20:
duckduckgo answered, brave 429, startpage 0 results, bing redirect failure). Decision: run `open-websearch serve
--host 127.0.0.1` pinned by `deploy/open-websearch/package-lock.json`; call only `/search`, `/health`, `/status`;
track health per engine with a circuit breaker; fall back across engines and to structured APIs (Wikipedia, arXiv,
OpenAlex, Crossref, PubMed, PyPI, npm, OSV, GitHub). The owner's RSS/Atom feeds (`nsf web feeds`) join as a `FeedProvider`.
Consequence: search keeps working, degraded, when engines fail.

**ADR-026 Web knowledge enters semantic memory as candidates through the executive's question path. R** (verification thresholds superseded by ADR-035)
Web content must never become verified knowledge automatically. Decision: claims → clusters → `web_knowledge` rows
with status `provisional` only when ≥2 independent sources agree, nothing contradicts, and confidence ≥ 0.72
(`auto_provisional_min_confidence`, configurable). The floor is derived, not chosen: with prior Beta(1, 1) and
secondary-source weight 0.8, two dated secondary sources give 2.6/3.6 = 0.722 and pass, one dated plus one undated
give 2.4/3.4 = 0.706 and two undated 0.688 and do not; the same constant is the fact-verification `supported`
threshold, so a verdict and a promotion agree. At 0.75 no two-source claim could pass (two primary
records give 0.737). Claims from adversarial (injection-flagged) sources are held and never count; everything else, and every promotion to `accepted`, goes through a `pending` question
(scope `web:knowledge:<id>`) answered with `nsf resolve ID TEXT --confirm|--reject`. Versions are never overwritten:
a new value supersedes the old row, which keeps its validity interval. Consequence: raw evidence, extracted claims
and accepted knowledge stay distinguishable, and an owner can audit every step.

**ADR-027 A worker drains explicitly queued tasks; a timer drives monitors and refresh. R** (extends ADR-012)
Pocket- and console-initiated research must run on the Spark without blocking a request. Decision: tasks are rows
in `web_tasks`; `nsf web work` (systemd `neuroscifi-webcortex.service`) claims queued tasks one at a time;
`nsf web tick` (timer) starts due monitors and, only when `autonomous` is on, refreshes and curiosity research
within a daily cap that is also clamped into each autonomous task's budget at submit. A monitor reports a change only
when it is meaningful (a changed value of a known key, or a new corroborated or primary key not seen in any earlier run),
not when the search results merely differ. The console runs an embedded worker (leases prevent double execution;
`--no-web-worker` leaves tasks to the service). Nothing crawls without a task, a budget and an origin. Consequence:
every background action is a visible, cancellable task with an episode log.

**ADR-028 Pocket can request research, scoped to its pairing family. R** (amends ADR-013's remote surface note)
Decision: when `pocket_research` is on and the bridge is started with `--instance-db`, paired devices may
submit (≤ 300-character objective; the `research_submit` body may be up to 1024 bytes so 300 non-Latin characters fit, every other route stays at 512), list, read and cancel tasks of their own family, and read the report. Submissions are limited to 10 per hour per pairing family, persisted across restarts; a family holds at most 6 devices, and `nsf pocket erase-pairing LABEL --family` (or "Forget all linked devices" on a device) revokes a whole family (UAT S-02). They
cannot accept knowledge, change configuration, forget memory or see other families' tasks. A device may submit 10
tasks per hour, with a privacy-screened objective and no budget field. Consequence: the LAN surface grows by four
actions under the existing bearer, origin, size and TLS controls (tests: `tests/test_pocket_research.py`).

**ADR-029 Migration 6 adds the web tables to the individual; forget and erasure cover them. H (format)**
Decision: twelve `web_*` tables (ARCHITECTURE §6), owned by `webcortex/state.py`. `nsf forget research|knowledge|source`
cascades to derived claims, links, knowledge versions and pending questions, marks checkpoints stale, and records a
`deletion_log` row. Forgetting a task deletes its row, events, sources and claims; the deletion log keeps only the task id, and a byte-level test checks that the objective text leaves the file and its WAL. Owner questions about forgotten knowledge keep their row with the text replaced by `#forgotten`.

**ADR-030 JavaScript rendering is an escalation, sandboxed and routed through SafeFetcher. R** (default and budget amended by ADR-041)
Decision: when extraction finds a JavaScript shell and the task has a browser-session budget, `browser.BrowserRenderer`
loads the page in a fresh Chromium context whose every request is intercepted and fulfilled by `SafeFetcher` (or
refused; fetched with `purpose="subresource"`, which admits scripts and stylesheets, is never cached or extracted and
uses a short same-host interval). No cookies persist, downloads and permissions are denied, service workers and
WebSockets are blocked. Off by default (`browser_rendering`). Consequence: SSRF and robots controls apply to every
sub-resource.

**ADR-031 One executing node; devices are access points. R**
The directive describes distributed research across enrolled devices with compute-aware routing. The only device
relationship that exists is Pocket pairing families, and the only machine that holds the individual is the Spark.
Federating workers would need device enrolment, remote attestation and result signing, none of which exist.
Decision: the process that runs `nsf web work` (or `ask`/`tick`) executes; Pocket families and the console submit,
watch, cancel and read; each task records `executed_on` (host digest, CPUs, load). Linked devices share tasks through
the family scope (ADR-028). Consequence: no crawler stack on phones, no credentials or browser sessions on devices,
and no cross-device routing decisions to make. Deferred: worker federation (WEB-36).

**ADR-032 The search daemon sends an honest User-Agent unless the owner opts in. R** (amends ADR-025; superseded by ADR-034, honest-UA rule retained)
The security review found that open-webSearch 2.2.0's engine scrapers send forged Chrome User-Agent strings, which
contradicts "no anti-bot circumvention". Decision: `deploy/open-websearch/apply-honest-ua.mjs` (`npm run patch-ua`,
run by `install.sh`) replaces them with `config.USER_AGENT`; `Daemon.start` refuses an unpatched build unless the owner
sets `search_browser_user_agent` (default false). Consequence, measured live on 2026-11-20: with the honest
User-Agent all four key-free engines returned 0 results (duckduckgo 0, brave 429, startpage token failure, bing 0), so
general web discovery currently relies on the structured providers (Wikipedia, arXiv, OpenAlex, Crossref, PubMed,
PyPI, npm, OSV, GitHub) and on feeds. Whether to accept browser impersonation for search engines is an owner decision
(`.omc/autopilot/decisions-pending.md` item 4).

**ADR-033 A package's own registry record may settle a release statement. R** (narrows ADR-026)
UAT RT4-03: "Was numpy 2.5.5 released?" stayed "insufficient" because the only source was PyPI. For statements about a
package's own releases (which version is latest, whether a version was released) the registry record is the primary
authority, not a second-hand report. Decision: such a statement is "supported" (or contradicted) by the package's own
record when nothing contradicts it; the verdict says that a single registry record decided it. Every other statement
still needs two independent agreeing groups. Consequence: release checks answer from the authoritative record; a
compromised or wrong registry entry would be believed (mitigated by OSV advisories and the owner's review of knowledge).

**ADR-034 Self-hosted SearXNG is the primary general discovery provider, honest and robots-compliant. R** (supersedes ADR-032, amends ADR-025)

Context. ADR-032 kept the honest User-Agent for the open-webSearch scrapers; measured on 2026-11-20 all four
key-free engines then returned nothing, so general web discovery rested on structured APIs and feeds. The v0.7.0
directive makes a self-hosted SearXNG the primary general provider. Inspection of the pinned image
(`searxng/searxng@sha256:fc1d81d7…`, 2026.11.21-c6ee3e9fd) found that unmodified SearXNG impersonates a browser by
default: every engine request goes through curl_cffi with `impersonate="chrome"` (Chrome TLS fingerprint, Chrome
User-Agent and Client-Hint headers) unless the engine opts out, some engines ask for Firefox, and `gen_useragent()`
returns a random Firefox User-Agent. Several engines also solve CAPTCHAs or proof-of-work challenges (mojeek
ALTCHA, startpage Anubis) or replay anti-bot cookies with browser header order (qwant DataDome). Most search-result
pages are disallowed by their site's robots.txt (brave /search, bing /search, google news, yahoo, mojeek,
startpage, duckduckgo.com/news.js).

Decision.
1. Run the pinned image by digest under docker, published on 127.0.0.1 only (`nsf web searxng start`,
   `providers/searxng.Container`): read-only root, `--cap-drop ALL`, no-new-privileges, user 977, memory/CPU/pid
   limits, healthcheck, bounded json-file logs, `--restart unless-stopped`. The secret is generated once into
   `~/.config/neuroscifi/searxng/secret.env` (0600, outside git) and passed as SEARXNG_SECRET. JSON output on,
   limiter off (one loopback client; public_instance false; no valkey), no image proxy.
2. Honest requests, as ADR-032 required of open-webSearch: NeuroSciFi mounts a patched `searx/network/client.py`
   (two edits: impersonation off, SearXNG's own User-Agent as the client default; the pristine file is verified by
   sha256 before patching) and an honest `useragents.json`, plus an empty tmpfs over `network/__pycache__` (the
   image's unchecked hash-based .pyc would otherwise ignore the patch). `start` runs a header probe inside the
   container and stops it if any request still looks like a browser. Engines see
   `SearXNG/2026.11.21+c6ee3e9fd NeuroSciFi-WebCortex (self-hosted research agent; loopback-only instance)`.
3. Engines: only those whose requested URL robots.txt allows, or that call a documented public API, and whose
   module neither solves challenges nor replays anti-bot state. Enabled: general duckduckgo (html.duckduckgo.com
   allows /html/), mwmbl; news bing news (/news/infinitescrollajax allowed), hackernews (Algolia API);
   it stackoverflow, askubuntu, superuser, docker hub, hackernews; science openairepublications, europepmc.
   The structured APIs NeuroSciFi calls directly (arXiv, OpenAlex, Crossref, PubMed, PyPI, npm, OSV, GitHub,
   Wikipedia) are not enabled in SearXNG; they stay primary.
4. Routing (`intent.searxng_route`): one category per query, never all engines — news → news (+time_range for a
   recency window), software cue → general,it, academic → science (supplement), else general.
5. Registry: SearXNG is built only when `searxng_url` is set (`start` sets it) and is then first; open-webSearch
   gets `fallback_for="searxng"` and runs only when SearXNG's circuit is open or SearXNG failed or found nothing.
   Per-engine breakers from `unresponsive_engines` (captcha/403/429/timeout/error); an empty engine is neutral.
   "Healthy" requires a non-empty real result within 6 h, not an HTTP 200.
6. AGPL-3.0: the image is AGPL-3.0-or-later. NeuroSciFi does not link or import SearXNG code; it talks HTTP to a
   separate process. Because of decision 2 the deployed SearXNG is a *modified* version. AGPL §13 obliges offering
   the corresponding source to users who interact with the modified program over a network; the only user is the
   owner on loopback, and the complete modification (two-line patch, generated from the pinned upstream file,
   `providers/searxng.honest_client`) plus the upstream revision (c6ee3e9fd) are in this repository. If the
   instance were ever exposed to other users, a source offer must be added first.

Consequences. Measured 2026-11-21 (docs/quality/evidence/searxng_live.json): duckduckgo answered the honest
User-Agent with a CAPTCHA on 4 of 9 calls, so general web discovery rests largely on mwmbl (small open index, no
dates); wikinews returned nothing and was dropped;
news (bing news, hackernews), it and science categories return relevant results. A full general web index through
an honest, robots-compliant engine is not available key-free: release blocker for "SearXNG as primary general
provider" in its full sense; owner options are a keyed API engine (e.g. Brave Search API) or accepting the
limitation. Disabling the patch (browser impersonation) is not offered.
Supersedes ADR-032 (the honest-User-Agent rule now covers both search backends; the open-webSearch opt-in
`search_browser_user_agent` stays for that daemon only).

**ADR-034 (amended) A keyed engine, the Brave Search API, is the primary general discovery provider when its key is
configured. R** (draft amendment 1, 2026-11-21; amends ADR-034)

Context. ADR-034 measured that no honest, robots-compliant key-free engine gives a full general web index
(DuckDuckGo answered the honest User-Agent with a CAPTCHA on 4 of 9 calls; Mwmbl is small and undated) and named a
keyed API engine as the owner's option. The owner chose one: the Brave Search API, a documented API (ADR-034
decision 3 admits engines that call one), with web and news endpoints and a `page_age` date on results.

Decision.
1. Routing: with a key, Brave (`providers/brave.py`) is built first and answers `web` and `news` queries; SearXNG
   gets `fallback_for="brave"`, open-webSearch stays SearXNG's fallback (Brave's when SearXNG is not configured),
   and the registry runs fallbacks as a chain, one tier after the other (a fallback waits while any provider up
   its chain is live). Technical and academic queries keep
   SearXNG's it/science engines and the structured APIs. Without a key, or with the cap at 0, nothing changes.
2. The key is the owner's secret: `~/.config/neuroscifi/search/brave.key` (NSF_SEARCH_KEY_DIR), 0600 in a 0700
   directory, outside git, written only by `nsf web provider set-key brave --from-file PATH | --stdin` (never argv);
   a symlinked, foreign-owned or group/other-accessible file, or a directory others can write to, is refused. It is read per request and never logged,
   printed ("configured" only), stored in the instance, an event, an error or an evidence file; API error text is
   scrubbed of it; SafeFetcher drops its header on a cross-origin redirect.
3. Cost: `daily_provider_queries.brave` (default 30 per UTC day; 0 = off) is counted per key, across instances,
   processes and origins (autonomy included), before a request is sent, and refunded only when Brave cannot have
   billed it (refused before sending, or an error status; Brave bills only successful requests); a damaged ledger
   counts as a used-up day. X-RateLimit-* headers are honoured (a used-up window longer than one
   second blocks until its reset), a 429 sets the host backoff (Retry-After, else 60 s), and requests to the API
   host are at least one second apart. Rate limits and the cap are health-neutral and hand the query to SearXNG.
4. Policy unchanged: every query has passed the privacy screen and the task and daily budgets before the registry
   sends it; SafeFetcher `purpose="api"` with the honest User-Agent; nothing is cached on disk.
5. Terms (Search API Terms of Use, 1 Sep 2026): results are kept only transiently (120 s in memory, marked
   `metadata.transient`, dropped from a task's checkpoint when it ends) and are never used for benchmarks
   (`nsf web bench --live` sets the Brave cap to 0).
6. Marginalia Search is not added: its shared `public` key is offered for experimentation and often rate-limited;
   ongoing use needs a personal key requested by e-mail, and its results are undated.

Consequences. With a key, general and news discovery rest on a full, dated web index instead of DuckDuckGo and
Mwmbl; each such query costs one Brave request ($5 per 1,000 on the Search plan, which includes a $5 monthly credit;
the default cap of 30 a day stays inside it). The ADR-034 release blocker "general web discovery is thin" is
resolved for an owner who configures a key and stays for one who does not. Open: the report's unread-headline list
can quote a few Brave titles and URLs and is kept with the task (an owner question under §3(b)(i)). Live
validation waits for the owner's key (`tests/webcortex/test_live_providers.py`). Details:
docs/web-cortex/SEARCH_PROVIDER_STRATEGY.md §6.

**ADR-035 Claim verification depends on the kind of statement: categories, six states, syndication-aware
independence. R** (draft; supersedes ADR-026's verification rule; amends ADR-033, see ADR-033-amendment.md)

Context. ADR-026 used one bar for every checked statement: posterior mean >= 0.72 from >= 2 independent groups,
with every undated source weighted x 0.75. Two credible agreeing sources stayed "insufficient" when one was undated
(0.706), which is the usual case for encyclopedias and project pages. The v0.6.0 offline benchmark measured
supported recall at 0.4 (PRODUCTION_READINESS R-2). The rule also could not tell a current-state claim from a
historical one, counted a lightly edited syndicated copy as a second source, and had only four labels, so
"conflicting evidence", "no evidence about now" and "not checkable" all read as "insufficient" or "contested".

Decision.
1. Each checked statement gets one category (`temporal.claim_category`, deterministic cue rules, mapped onto the
   existing freshness classes): stable, historical, time_sensitive, scientific, numerical, comparative, registry,
   interpretive.
2. The verdict carries a state (`claims.VerificationState`): supported, disputed, insufficient, conflicting,
   temporally_unresolved, not_evaluated. `verdict["label"]` keeps the four legacy labels (conflicting -> contested,
   the two new non-decisions -> insufficient); stored reports are normalised on read (`claims.verdict_state`).
3. Per-category policy (`claims.CATEGORY_POLICY`): undated sources are not discounted for stable and historical
   statements (two credible undated sources give 2.6/3.6 = 0.722, exactly the two-source bar), keep x 0.75 for
   scientific/numerical/comparative, and do not count at all for time-sensitive statements, which are decided
   only by evidence dated inside the question's window and are otherwise temporally_unresolved. The 0.72 floor
   and the two-group requirement are unchanged. A direct authoritative record suffices alone, only when nothing
   contradicts: the subject's own official site for narrow stable/historical facts (never quantities), the
   package registry for publication and latest version (ADR-033).
4. Disputed requires more contradicting groups and >= 1.5 x the supporting weight; otherwise credible evidence on
   both sides is conflicting, and no consensus is chosen. Supported still requires that no group contradicts.
5. Syndicated copies are one source: canonical URL on another host, attribution lines, wire credits and
   near-identical documents (>= 40 words, 5-shingle Jaccard >= 0.6) join independence groups, in verdicts and in
   cluster scoring.
6. Interpretive statements, unsupported shapes and verdicts with no usable source are not_evaluated.

Kept from ADR-026: web knowledge enters memory only as candidates through the owner's question path; the
consolidation floor (`auto_provisional_min_confidence` 0.72 over cluster confidence with the 0.75 undated factor)
and versioning are unchanged.

Consequences. On the offline benchmark the original 16 cases go from 12/16 to 16/16 and supported recall from 0.4
to 1.0, with supported precision 1.0. 13 new cases (undated stable/historical facts, current undated claims,
official sources, corroboration, syndication, supersession, contradiction, missing evidence, registry aspects)
score 12/13. The miss is a routing gap: intent does not query the registry for "Is X V stable?". A stable fact can
now be supported by two undated sources yet not become provisional knowledge, because consolidation keeps the
ADR-026 bar (an owner decision). Category cues and official-site detection are lexical. A look-alike domain naming
the subject would be believed for a narrow fact when nothing contradicts it. Details and every number:
docs/knowledge/CLAIM_VERIFICATION.md.

**ADR-033 (amended) A package's own registry record settles publication and latest version, nothing more. R**
(draft amendment; ADR-035)

Context. ADR-033 let a package's registry record alone decide "was V released?" and "is V the latest?" (UAT
RT4-03). Nothing prevented the same record from being read as evidence that a version is stable, compatible,
secure or advisable, although a record of publication says nothing about these.

Decision. `synthesis.release_statement` distinguishes six aspects, and `_registry_verdict` applies:
- published, latest: unchanged. The record supports or contradicts the statement, `basis: registry_record`.
- stable: supported only when the record lists the version with an explicit yank flag of false and the version
  has no pre-release marker (PEP 440 a/b/rc/dev, SemVer "-pre"). Disputed by a marker, a yank or an unlisted
  version. Otherwise insufficient.
- compatible: never supported. Disputed when declared metadata (`requires_python`) excludes the target.
- secure: never supported. Disputed when an OSV advisory's affected range covers the version. No advisory found
  stays insufficient. "is vulnerable" is supported by such an advisory.
- recommended: not_evaluated (a judgement).
A registry statement gets a verdict in any mode, not only `technical`.

Consequences. R-6 still holds for publication and latest version (a wrong registry entry is believed for those),
but it no longer spreads to stability, security or recommendation. Tests:
tests/webcortex/test_claim_verification.py (section 4). Details: docs/knowledge/CLAIM_VERIFICATION.md §5.

# ADR-036 (draft) Local inference gateway; the model writes answers only from verified evidence. R

Status: draft for docs/architecture/ARCHITECTURAL_DECISIONS.md (Lane B, v0.7.0). Extends ADR-022 (which stays true
for retrieval, verification, ranking and the degraded path) and supersedes its sentence "There is no language model
... on the host or in the package" for answer writing only.

Context. v0.7.0 puts an LLM on the DGX Spark (Qwen3-30B-A3B-Instruct-2507-FP8, Apache-2.0, served by vLLM in
Docker). ADR-022 made every answer sentence a quote or a template so that nothing unverified is ever presented as
fact. A model can write a more direct answer, but it can also invent facts, numbers, sources and certainty, and it
reads web text that may carry instructions.

Decision.
1. `neuroscifi.inference` is a model-independent client for OpenAI-compatible endpoints (stdlib `http.client`, no
   new dependency): discovery, explicit health states (ready / degraded / unavailable / unauthorised /
   not_configured), chat with SSE streaming, structured output validated locally with one bounded repair, tool calls
   decided locally, timeouts, bounded retries with backoff, a context budget and a per-call token ledger
   (`<db stem>.inference.sqlite`, no text).
2. Local-first. The endpoint is per individual (`meta` key `inference`, off by default). It must resolve to loopback
   or private addresses; anything else needs the owner's `remote_allowed`, https, and passes the web privacy screen;
   messages marked private need `remote_private_memory` as well. The API key lives in a file outside git (0600).
3. The model writes the research answer after verification, from a bundle of verified claims only (id, state,
   confidence, quoted text, source ids, dates), wrapped as untrusted data. `webcortex.reasoning.guard` is the
   authority: per-sentence source and claim citations must exist and match; non-supported claims must be hedged in
   the sentence's own words; no "verified/confirmed" for non-supported claims; no number absent from the cited
   evidence; no instruction-like text. A bounded self-critique may only remove sentences. The model never changes a
   claim's state and its text never becomes knowledge (consolidation reads claims only).
4. `report.generated_by` is `model:<served name>` only when at least one sentence passed; otherwise `template`, with
   the label "Model-written summary unavailable — showing the extractive summary quoted from the sources" and the reason. The extractive answer is
   always kept unchanged.
5. Tool calls: the model suggests; NeuroSciFi checks the allow-list, the granted permission and the argument schema,
   and executes through existing functions (knowledge.query, orchestrator.get, orchestrator.submit with its web
   switch, privacy screen and budgets). No tool changes configuration, forgets, accepts knowledge or resolves
   questions.

Rejected alternatives. (a) Let the model write freely and show its citations: unverifiable sentences would look
verified. (b) Trust the model's own `hedged` flag or a self-check alone: the authority must be local code.
(c) An SDK (openai, httpx): a new dependency for ~400 lines of stdlib. (d) Ollama/llama.cpp: vLLM is the measured,
working server on GB10 with tool parsing and guided decoding. (e) Storing model answers as knowledge: would let
model text bypass ADR-026.

Consequences. Answers read better and say what is missing (knowledge gaps), at a cost of one or two model calls
(measured: COGNITIVE_INFERENCE.md §4). The guard is lexical: hedge detection can be fooled by a hedge word in an
otherwise assertive sentence, and number grounding does not check units or which claim a number belongs to. A model
outage, a key problem or a guard rejection only changes the label; research keeps working.

# ADR-038 (draft) Knowledge evolution: verified-only consolidation, hypotheses with calibrated predictions, and three separated kinds of learning. R

Status: draft for docs/architecture/ARCHITECTURAL_DECISIONS.md (Lane D, v0.7.0). Amends ADR-026 (its consolidation
thresholds only; its governance stands) and builds on ADR-029 (web memory lives in the individual's file) and
ADR-035 (claim verification). Details and every rule: docs/knowledge/KNOWLEDGE_EVOLUTION.md and
docs/knowledge/CONTINUAL_LEARNING.md.

Context. After ADR-035 the fact-verification verdict used per-category rules (an undated stable pair is supported,
a "current" claim needs evidence from the time window, syndicated copies count once), but consolidation into
knowledge still used ADR-026's single bar over cluster confidence. A statement could be "supported" yet never become
knowledge, and an old "latest" page could still contradict current knowledge. Statements that failed the bar were
dropped from view, so nothing tracked them as open questions. The system made "still holds" predictions but had no
forecast probability, calibration, error analysis or revision. There was no record of how understanding of a topic
had changed, no metric of learning other than stored volume, and no written boundary between updating memory,
tuning research behaviour and changing model weights.

Decision.
1. Consolidation eligibility is the ADR-035 verification state of each scored cluster (evolution.cluster_verdict:
   category policy, syndication-aware groups, timeframe, authoritative records). Only "supported" clusters become
   provisional candidates. The owner question path of ADR-026 is unchanged, and nothing is accepted without the
   owner. The owner's floors (auto_provisional_min_sources, auto_provisional_min_confidence) can only raise the bar.
   "insufficient", "temporally_unresolved" and "not_evaluated" clusters are held as research opportunities.
   "conflicting" and "disputed" clusters record a contradiction (against knowledge, or as a hypothesis) and choose
   no consensus.
2. Every record has one provenance kind: raw_evidence (web_sources), provisional_claim (web_claims, web_hypotheses),
   verified_conclusion and user_asserted (web_knowledge.provenance). An owner assertion is a new accepted version
   with state not_evaluated. Web evidence never supersedes it; a conflict is recorded and the owner is asked.
3. Each knowledge version stores its verification record and its publication, event, retrieval, verification and
   effective times. Versions are never destroyed, and only forgetting removes them.
4. Typed knowledge-graph edges live in the existing web_claim_links table, rebuilt with node kinds: claim -> source,
   evidence, contradiction and validity interval; entity -> entity; knowledge -> knowledge (supersedes);
   hypothesis -> prediction -> observation -> error -> revised hypothesis or version.
5. Each research opportunity is a hypothesis with a testable prediction: resolution criteria, a deadline (class TTL,
   at most 30 days) and a forecast probability (the ADR-035 posterior). Later verified evidence settles it
   (confirmed, or error with an error analysis, alternative explanations and a revised hypothesis). An expired
   deadline gives inconclusive. Calibration (Brier score, skill, ECE, 10 reliability bins) is persisted after every
   settlement.
6. Metacognitive metrics are computed from records only. Stored volume is not a metric. "What has changed in your
   understanding of X" is a structured, dated answer. A local model may rephrase it from the returned records,
   labelled; a remote endpoint never receives it.
7. Learning comes in three kinds, kept separate. Knowledge evolution (above). Procedural learning: advisory
   provider weights from verified yield, at most 0.1 per step, in [0.5, 1.5], logged as snapshots, and reversible.
   Model adaptation: not implemented. A guard refuses any weight change without owner authorisation, an isolated
   evaluation plan and a rollback plan, always refuses autonomous origins, and logs every decision.
8. Migration 8 is forward-only and atomic. It adds the knowledge columns, web_hypotheses and web_metrics, and
   rebuilds web_predictions and web_claim_links, copying every row and id. Forgetting cascades through the new
   records, and checkpoint restore replays the cascade.

Consequences. True stable and historical facts with undated sources now reach the owner as candidates. Stale
current-state claims no longer contradict knowledge; they become hypotheses. Consolidation recounts independent
groups, so a cluster built outside the scorer that counted a copied sentence twice is now one source; test fixtures
were reworded accordingly. web_knowledge.confidence remains the scorer's cluster posterior, and the ADR-035
posterior is in verification.confidence. Prediction quality is now measurable but noisy until enough forecasts
settle. Procedural weights are advisory: wiring them into provider selection is a later owner decision. Matching is
still lexical (keys, values), so a rephrased hypothesis can be missed. Reversal: drop the eligibility call to
restore ADR-026 thresholds. The new tables and columns can stay, because they are additive and readers ignore them.

# ADR-037 (draft): Voice Cortex — hybrid speech path behind a NeuroSciFi-authoritative bridge

Status: proposed (Lane V, v0.7.0), 2026-11-21. Evidence: docs/voice/PERSONAPLEX_INTEGRATION.md (audit),
docs/voice/VOICE_UAT_RESULTS.md (measured), test-results/voice_uat.json.

## Context

Spec area 6 asks for voice with NeuroSciFi authoritative for facts, tools, memory and permissions; modes A/B/C;
real mic/speaker/session/privacy controls; one capturing device at a time; transcripts that separate approximate
from authoritative text; long tasks that survive the voice session. The audit measured PersonaPlex on this host:
upstream not real-time (RTF 1.24–1.84), cannot speak external text through its server, unauthenticated server
binding all interfaces; Whisper large-v3-turbo (vLLM) and Kokoro-82M measured fast and exact on synthetic speech.

## Decision

1. **Default path = hybrid**: Whisper large-v3-turbo via vLLM transcription (127.0.0.1:18431) + Kokoro-82M behind
   a small authenticated wrapper (127.0.0.1:18432), both Docker containers built FROM the digest-pinned vLLM image,
   loopback-published, read-only, no capabilities, one generated 0600 API key. NCCL is protected by installing
   torch-dependent packages with `--no-deps` and asserting torch/NCCL versions in the build.
2. **Speech engine interface** (`voice/engines.py`): ASR, TTS and an optional full-duplex engine with explicit
   health states, timeouts, one retry, a fail-fast cool-down and metrics. PersonaPlex is an opt-in experimental
   engine behind it (loopback URL and voice allow-list enforced), **never routed a question**, not deployed.
3. **Cognitive Voice Bridge**: deterministic command grammar; questions go to `orchestrator.submit` exactly like
   typed ones (privacy screen, web switch, budgets, worker, inference gateway + guard, memory). Replies are composed
   from the task report only: concise speech, detailed text with citations, honest model/extractive/template
   labels. "Explain more simply" re-asks the local model from the same evidence bundle and re-applies the guard.
4. **Authorisation and confirmation**: console = owner; Pocket = its pairing family with Pocket's existing rights.
   Forget/delete, enabling autonomy, privacy-term disclosure, accepting conclusions and microphone hand-off require
   a same-session confirmation within 60 s (spoken "confirm" or the button).
5. **Transport**: plain HTTP on the existing servers (no WebSocket): 16 kHz PCM16 chunks ≤ 64 KiB on a dedicated
   authenticated route, long-poll events, per-sentence WAV. Every other route keeps its limits; console
   `Permissions-Policy` becomes `microphone=(self)`.
6. **Client-side VAD and barge-in** (energy + duration thresholds), playback stopped locally before telling the
   server; server cancels the rest of the turn and records `interrupted` with the sentence reached.
7. **Persistence**: forward-only schema v9 (`voice_sessions`, `voice_segments`, `voice_capture`, `voice_audio`);
   raw audio not stored unless the owner enables retention (TTL 1–168 h, swept); one capture lease per individual
   shared by console and Pocket.
8. **Presence contract**: `voice.state.snapshot()` and the `nsf:voice` browser event with real RMS levels.

## Alternatives rejected

- PersonaPlex as the default engine: not real-time upstream on GB10, no external-text channel, unauthenticated
  server; its words are not NeuroSciFi's evidence.
- WebSocket transport: would need a new server stack in both the console and Pocket; HTTP chunks measured fast
  enough (EOU → final transcript 0.16–0.45 s).
- Server-side VAD: needs continuous upload; client VAD uploads nothing outside confirmed utterances and lets mic
  off mean no transmission at all.
- An LLM intent classifier: a model must not decide whether a command (forget, autonomy) was given.

## Consequences

- Speech adds ≈ 5 GiB of GPU memory (ASR engine 4.2 GiB, TTS 0.9 GiB measured) beside Qwen3 (44 GiB).
- End-of-utterance costs the 700 ms VAD hang-over plus ASR (~0.2 s); research answers take tens of seconds and are
  acknowledged at once.
- Thresholds, echo cancellation and accents are unmeasured with real hardware (no microphone/speaker here).
- Shared files touched (small edits): console server/index/js, Pocket server/index/sw.js, instance schema, the top
  CLI module list, tests/js/offline_check.mjs (shell count).

# ADR-039 (draft) Controlled autonomy: two day-bounded budgets enforced at claim, research chosen from records, and explicit session ownership for research nodes. R

Status: draft for docs/architecture/ARCHITECTURAL_DECISIONS.md (Lane F, v0.7.0). Implements owner decision D4.
Builds on ADR-027 (the worker drains queued tasks), ADR-028 (Pocket family scope) and ADR-031 (one executing node).
Records the RD-11 scope. All rules are in docs/autonomy/RESEARCH_POLICY.md.

## Context

In 0.6, autonomous research was bounded only by a rolling 24-hour cap on tasks, requests and bytes, applied when a
task was submitted and again when it started. That cap had several gaps:

- No search, page, time or spend limit matched the owner's terms.
- User research had no budget of its own.
- There was no concurrency bound across worker processes.
- Forgetting a task removed its usage from the cap.
- Nothing scheduled research to settle Lane D's hypotheses and predictions.

Pocket could submit, list, read and cancel research. It could not continue a task, review its evidence or hand work
between devices. RD-11 asked for a multi-node scope decision before any implementation.

## Decision

1. **Two daily budgets, charged per UTC day of an explicit `now`.**
   - Autonomous research (origins `curiosity` and `refresh:`) gets 20 searches, 60 pages, 1800 s of active time,
     $1 of spend and 2 concurrent investigations.
   - User research (every other origin) gets 400 searches, 1200 pages, 14400 s and $5.
   - The owner can configure both, through the `webcortex.autonomy` meta key.
   - Spend is a price model: searches x price plus pages x price, with both prices 0.0 by default.
   - SearXNG requests and upstream engine requests are counted separately, as observations.
2. **Charging and recording happen at claim.**
   - `autonomy.admit` runs inside `orchestrator._claim`'s `BEGIN IMMEDIATE` transaction.
   - It refuses the task when any limit is spent; the task is cancelled and the reason recorded.
   - Otherwise it clamps `max_queries`, `max_pages` and `max_seconds` to what is left and records the allowance in
     the new `autonomy_ledger` table (migration 10).
   - An autonomous task waits while 2 are open, and `claim_next` skips it meanwhile.
   - `settle` records what the task actually used.
   - Ledger rows outlive forgetting and hold only numbers and the task id.
   - A submission against a spent budget is refused with `BudgetRefused`; Pocket answers 429 `daily_budget`.
3. **Opt-in with a record.**
   - The idle investigator needs the `autonomous` switch and a recorded owner enable (CLI, console or API).
   - The 0.6 timer behaviour stays keyed on the switch.
4. **What to research comes from the records.**
   - Candidates come from `curiosity.rank` and from open hypotheses with open predictions.
   - Priority is info_gain x (1 + sum of weight x signal) / cost.
   - The signals are goal, contradiction, hypothesis, prediction uncertainty, source change, stale and gap. The
     weights are documented.
   - A candidate is refused when its expected gain is below `min_gain` (0.05 bits), when its subject is within the
     24-hour cooldown, or when it is known only from autonomous research and unrelated to an owner goal.
   - The ranking inputs are logged on each task.
5. **Notifications only for meaningful findings.** A pending question `web:autonomy:<task>` is raised for a newly
   verified statement, a resolved contradiction or a settled prediction, and for nothing else.
6. **Pocket.** Three routes are added:
   - `research_evidence`: ADR-035 state and basis per subject;
   - `research_continue`: continue the task, counted in the hourly limit;
   - `research_session`: session ownership.

   `research_get` gains `spent` and `continued_from`. Family scope, the 512-byte bodies, the rate limits and
   invisibility while off are unchanged.
7. **RD-11 minimal multi-node.**
   - A node is a paired Pocket device that the owner enrolled with the `node` capability (`nsf web node enrol`).
     It is stored as salted digests.
   - Nodes submit, continue, read and own sessions. They never execute, which keeps ADR-031.
   - A session (a chain of continued tasks) has at most one owner, enforced by the primary key, under a 15-minute
     lease.
   - Ownership moves only on expiry or through an explicit offer and accept: one conditional `UPDATE` within 5
     minutes.
   - Other devices cannot cancel or continue an owned session.

## Alternatives rejected

- Keeping the rolling 24-hour window: it does not match "per day", it refunds budget on forget, and it cannot
  bound concurrency.
- Charging at submit only: a queued task can wait for days, and several processes would race on the last
  allowance.
- Counting each SearXNG call as one search: it hides the upstream engine load. Counting engine requests as searches
  would make one query cost 7.
- Letting autonomy research every single-source claim it creates: the live run showed this would spend the budget
  on boilerplate sentences.
- Worker federation across devices: it needs enrolment, attestation and result signing, none of which exist.
  RD-11 stays deferred beyond the minimal scope above.

## Consequences

- Every background action is bounded per day and visible (`nsf web autonomy status`, `GET /api/web/autonomy`).
  User research can no longer be starved by autonomy.
- Searches and pages cannot pass a cap. Active time can pass it by at most the step in flight.
- Settling a hypothesis's prediction depends on Lane D's key matching. Live, a verified registry statement reached
  the owner as a knowledge candidate but did not settle the prediction, because the keys differed.
- Tests: `tests/webcortex/test_autonomy.py`, `tests/test_pocket_autonomy.py`, `tests/console/test_console_autonomy.py`,
  `tests/e2e/test_pocket_autonomy_e2e.py`. Live: `docs/quality/evidence/autonomy_live.json`.

# ADR-040 (draft) Pocket Web Push: an empty push per opted-in device, through the browser vendor's push service. R

Status: draft (lane "push", v0.7.0), owner decision: Web Push with a minimal payload. Amends ADR-028 (Pocket research,
family scope) and the "notifications only from the open page" behaviour (mobile/README.md). Keeps POC-07: the pairing
credential stays in tab memory only. Code: `src/neuroscifi/pocket/push.py`; tests: `tests/test_pocket_push.py`,
`tests/e2e/test_pocket_push_e2e.py`, `tests/js/offline_check.mjs`.

## Context

Pocket notified only while its page was open and visible: a hidden or closed Pocket checked nothing, and RD-22 recorded
"notifications while Pocket is closed" as not available. Delivering to a closed page needs a push service, and the
only one a browser accepts is its vendor's: FCM for Chrome and Android, Mozilla autopush for Firefox, Apple web push
for Safari and home-screen apps on iOS, WNS for Edge. That service is a third party. The service worker that receives
a push holds no pairing credential (POC-07), so it cannot ask the Spark what happened.

## Decision

1. **Opt-in per device.** Push exists only when the bridge is started with `--push-key PATH` (and `--instance-db`, with
   Pocket research on). The key is the Spark's VAPID key: an EC P-256 key generated once, on first start, as a new
   0600 file (refused later when it is a symlink, not owned, or not 0600; keep it outside git). A paired device turns
   push on with its own switch: the page asks for notification permission, subscribes with
   `PushManager.subscribe({userVisibleOnly: true, applicationServerKey})`, and sends only the endpoint URL to the Spark
   over the authenticated pairing channel (`push` route). The p256dh and auth keys are never sent or stored, because
   nothing is encrypted for the device. The table `push_subs` holds one endpoint per device. It references `devices`
   with `ON DELETE CASCADE`, so unsubscribing, unpairing, revoking a family and `erase-pairing` (with or without
   `--family`) delete it. One browser holds one subscription per origin: the device that registers it last receives
   its pushes. Every reply of the `push` route is signed by the Spark key like a research reply (ADR-042 §4), and
   Pocket refuses an unverified success. Before this was so, push never turned on, because the unsigned status reply
   was refused (security review B-0).
2. **No payload.** The Spark sends an empty POST (`Content-Length: 0`, no `Content-Encoding`), so nothing about a
   question leaves the host. The service worker shows the same text for every push ("NeuroSciFi Pocket: Your Spark
   finished a research task.", tag `pocket-push`) and tells open pages to refresh. Pocket loads the details from the
   Spark when it is opened. Because the worker holds no credential, generic text is the only option, and it is the
   point of the design.
3. **Sending.** VAPID per RFC 8292: an ES256 JWT (`aud` = the endpoint's origin, `exp` = 12 hours, `sub` = a contact URI,
   default `mailto:pocket@neuroscifi.invalid`, or `--push-contact`), signed with `cryptography` and sent as
   `Authorization: vapid t=…, k=…`. The request also carries `TTL: 43200` and `Urgency: normal`. Each push goes to
   the stored endpoint URL exactly as the browser's push service returned it. SSRF rules:
   - The endpoint must be `https` on port 443, with a plain DNS name and no credentials, fragment, backslash,
     whitespace or non-ASCII character, and at most 1000 characters.
   - Its host must be on the allow-list: `fcm.googleapis.com`, `jmt17.google.com` (the FCM host that Chromium builds
     without Google's keys return, seen live), `updates.push.services.mozilla.com`, `*.push.apple.com` or
     `*.notify.windows.com`. Anything else is refused at subscription (`400 push_service`) and again before every
     send.
   - The name is resolved once, in at most 5 s (`DNS_TIMEOUT`; a slower lookup counts as unreachable, so a slow
     resolver for one service does not hold up everyone's pushes). If any resolved address is not public
     (`webcortex.net.check_address`), nothing is contacted. Otherwise the connection is pinned to that address, with
     SNI and certificate verification for the name.
   - `http.client` is used directly: no proxy settings are read and redirects are not followed (a 3xx drops that
     push). Every socket operation has a 10-second timeout.
4. **Responses.**
   - 2xx sends it.
   - 404 or 410 deletes the subscription. Exception: within 120 s of the endpoint being first stored, it is retried
     instead (after 15 s, and no sooner than the one-a-minute interval), because live, FCM answered 410 to a
     registration 3 and 8 seconds old and 201 at 19 seconds.
   - 429 and 5xx keep the push pending until `Retry-After` (seconds or an HTTP date, at most a day) or a default
     backoff of 10 min for 429 and 2 min for 5xx. A timeout or connection error retries after 1 min.
   - Any other status drops that push and keeps the subscription. The log line names the service host and status,
     never the endpoint path.
5. **Trigger and rate limit.** A dispatcher thread in the bridge checks every 5 s, using the individual's database:
   - a research task of a subscribed family reached a terminal state (completed, partial, failed or cancelled, as the
     open-page notification counts them). It is announced to that family's subscribed devices only, once per finish
     (`push_seen`, ids only, kept two days);
   - an autonomy finding reached the owner (a new `web:autonomy:<task>` pending question, ADR-039). It goes to every
     subscribed device, because every pairing is made by the owner;
   - only finishes after the device subscribed and within the last 6 hours count.

   A device gets at most one push a minute, and every attempt counts, whatever the push service answered. Events in
   between collapse into the next push, which says no more anyway. **Send a test push** queues one push under the
   same limit.

   A device can also make the Spark contact the push service by storing a new endpoint and asking for test pushes.
   So that it cannot make the Spark ignore a `Retry-After` (security review B-1):
   - a rotated endpoint keeps the subscription's backoff (`not_before`) and its 404/410 grace clock (`stored`);
   - storing a new endpoint (a first subscription or a rotation) and asking for a test push are each limited to 5 an
     hour per device. Beyond that the route answers `429 push_limit` with `retry_after` in seconds, and Pocket says
     when to try again. Sending the stored endpoint again is free, and unsubscribing does not reset the count. The
     count is kept in the bridge's memory, so a bridge restart clears it.
6. **UI.** In the Research panel, Pocket shows:
   - the switch "Notify me even when Pocket is closed (push)", off by default, with the text "Uses your browser vendor's
     push service; no question text is sent";
   - the browser permission state and whether the Spark holds a subscription;
   - the **Send a test push** button.

   The Spark says in `state` whether it offers push, so the page never probes a 404. Without `PushManager` the switch
   is hidden and a hint is shown. On iPhone and iPad outside an installed home-screen app, the hint says to add Pocket
   to the Home Screen (iOS 16.4 or later). Everything else works as before. With push on, the open-page notification
   stays silent to avoid duplicates.

## Alternatives rejected

- **An encrypted payload with the question or status (RFC 8291).** It would still pass ciphertext and its size through
  the push service, and it would need the device's p256dh and auth keys on the Spark. The owner chose a minimal
  payload, and the generic text says enough.
- **Giving the service worker the credential so it can fetch details.** That would break POC-07, which keeps the
  credential in tab memory only (DD-2).
- **A self-hosted push server or a long-lived connection.** Browsers deliver background pushes only through their
  vendor's service. A closed page holds no connection.
- **A per-request `Topic` header to collapse pending pushes.** The per-device rate limit already collapses them, and
  not every service documents the header.

## Consequences

- The push service learns the device's endpoint (a random URL that identifies this browser's subscription to it), the
  Spark's public IP address and VAPID public key, the contact URI, and when each push is sent. It never learns the
  question, the answer, the task id or which family it belongs to. Timing can reveal when research finishes.
- Owners turn push off per device (the switch, which unsubscribes on the Spark and in the browser), per family
  (unpair), or entirely (start the bridge without `--push-key`; the routes then answer 404). Deleting the key file
  invalidates every subscription.
- New dependency `cryptography==50.0.2` as the `crypto` extra, shared with node signing. Without it `--push-key` is
  refused with an install hint.
- The `push` route takes 1 KiB bodies like `research_submit`, because WNS endpoints run to several hundred bytes. All
  other routes stay at 512 bytes.
- Verified live (`docs/quality/evidence/push_live.json`): headless Chromium 153 (Linux, persistent profile)
  subscribed through FCM. An empty push from `Push.request` was answered 201 and reached the service worker 0.43 s
  later, which showed the generic notification. Through the Pocket UI and the bridge's dispatcher, the test push, a
  finished question and a test push sent after the Pocket tab was closed were all answered 201 and shown. FCM also
  answered 201 to a push sent right after the browser unsubscribed, so a dropped subscription is cleaned up on a
  later 404/410, not at once.
  Playwright's default contexts are incognito, where Chromium offers no Push API, so the hermetic e2e stubs
  `PushManager` and simulates delivery with CDP `ServiceWorker.deliverPushMessage`. Real phones (Android Chrome, iOS
  home-screen app) are covered by the owner's guided test in mobile/README.md.
- Shared files touched: Pocket server, CLI, index, research-ui, mobile.js, sw.js (cache `neuroscifi-pocket-shell-19`,
  now 21), `tests/js/offline_check.mjs`.

# ADR-041 (draft) News rendering: a bounded per-render allowance, static-first reading, and rendering on for a person's news questions. R

Status: draft, v0.7.0 news lane. Settles owner decision OD-3 and open item O-05. Amends ADR-030 (rendering is no
longer off for every question by default). Rules and the budget table: docs/web-cortex/SEARCH_AND_ACQUISITION.md §9.
Evidence: `docs/quality/evidence/news_render_live_v2.json`.

## Context

- Bing News, the main dated-news engine behind SearXNG, returns no `publishedDate`, and most of its hits are msn.com
  articles whose static HTML has no text and no date.
- A complete MSN render needs 70 to 80 sub-requests. The renderer charged them to the task's request budget, which
  is 20 for a quick lookup, so the render was cut short. "What happened in AI this week?" then ended "freshness
  requirement not met" (`news_render_live.json`).
- Rendering was off by default (ADR-030), so a default individual never rendered at all.
- Many news sites need no render: Yahoo, Fox Business, Newsweek, TechCrunch, The Motley Fool and wccftech carry text
  and a JSON-LD `datePublished` in their static HTML. In the live runs, Wikipedia articles and a 2020-dated home page
  took the page slots ahead of them.

## Decision

1. **Accounting.** A render counts as one page read against `max_pages`. The page itself is fetched once and
   charged to the task's requests. The render's sub-requests, including robots.txt for sub-resource hosts, are
   charged to their own allowance, `budget.max_render_requests`, and never to `max_requests`.
   | Limit | Default | Ceiling or switch |
   |---|---|---|
   | Sub-requests per render | 100 | ceiling 150 |
   | Renders per task | `max_browser_sessions` 1 | ceiling 2 |
   | Renders per UTC day | `daily_renders` 40 | 0 to 1000 |
   | Render time | 20 s, cut to the task's time left minus 2 s | not started with less than 10 s left |
   | Sub-resource bytes | count against the task's `max_bytes` | |
   The day's count is a meta counter, so forgetting a task does not give its renders back. When the allowance runs
   out, the remaining sub-requests are refused and the run is `partial` with a limitation.
2. **Blocking.** The renderer never fetches anything a text extractor does not need:
   - images, media, fonts, stylesheets, beacons, and other resources such as favicons and prefetches;
   - sub-frame documents (iframes);
   - known advertising, analytics and beacon hosts (`browser.BLOCKED_DOMAINS`).
   Everything else still goes through SafeFetcher, with robots.txt, Retry-After, the address policy and the honest
   User-Agent.
3. **Static first for news and time-sensitive questions.**
   - Dated static pages inside the question's freshness window are read first. The date comes from JSON-LD,
     `article:published_time` and the other meta dates, `<time>`, or the feed item.
   - These wait until the dated pages have been read: scripted pages with no text or no date, encyclopedia articles,
     pages dated before the window, and hits the search engine dates before the window (these are not fetched yet).
   - Open slots go to pages with no text first, which are rendered when allowed. The other waiting candidates follow
     in rank order. A page with no text that could not be rendered takes a slot last.
   - SearXNG news results are interleaved engine by engine before the list is cut to its limit.
   - Search-engine dates order candidates only. They never date a source or satisfy freshness.
4. **Default.**
   - `news_rendering` is **on** by default and `nsf web config --no-news-render` turns it off. It permits a render
     only for a news or time-sensitive question that a person asked (`user_initiated`), never for autonomous
     research.
   - `browser_rendering`, which allows rendering for every question, stays off by default.
   - Nothing renders until the owner enables web access.
   - Resource basis: measured peaks for one render are 427 to 534 MiB summed PSS (1.2 to 1.4 GiB summed RSS) and 3 to
     5 s of CPU over 10 to 20 s. On the GB10's 121 GiB of unified memory that is under 0.5 % of memory. It also fits
     the service units' `MemoryMax=2G` next to the worker.
   - At most one render runs per task, and at most 40 a day. That bounds the daily cost to about 7 GiB-minutes of
     memory (40 x 0.53 GiB x 20 s) and about 3.5 CPU-minutes.
   - The resource scheduler refuses the render at `critical` pressure (less than 6 GiB, or 6 %, available, or GPU
     memory at 97 % or more). The task then reads the static page and records why.

## Measured

Sub-requests per render, before and after blocking:

| Site | Before | After | Note |
|---|---|---|---|
| msn.com | 76 to 80 | 69 to 81 | About 60 first-party script chunks build the article, so blocking removes only the ad, analytics and login-frame requests |
| foxbusiness.com | 76 | 34 to 36 | |
| wccftech.com | 26 | 8 | |
| finance.yahoo.com | 246 | 236 | Over the allowance, but its static HTML is dated, so it is not rendered |

Live, with default settings and web access enabled, "What happened in AI this week?" (three times) and "What is the
latest news about NVIDIA?" (once) all met the freshness requirement:

- Each run read one rendered msn.com article (25 to 75 of its 100 sub-requests) and one or two statically dated
  pages.
- Each task spent 9 to 16 of its 20 requests.
- Wall time was 36 to 42 s.

## Alternatives rejected

- Raising the quick-lookup request budget to 150. That gives every quick lookup the cost of the worst page, and a
  heavy render still starves the other pages.
- Using search-engine dates for freshness. They are not the publisher's date, and Bing News sends none.
- Site-specific article APIs, such as MSN's content JSON. They are site scraping outside the page's own rules and
  break silently.
- Turning on `browser_rendering` for every question. It costs more and widens what Chromium runs, with no gain for
  questions that are not about the news.
- Blocking first-party script chunks. That breaks the page the render is for.

## Consequences

- Current-event questions can now return dated, cited answers by default.
- With web access enabled, Chromium runs untrusted news-site JavaScript by default. It stays inside the ADR-030
  sandbox, at most once per task and 40 times a day.
- A page that needs more than 100 sub-requests renders incomplete, and the report says so.
- `usage.render_requests`, the render event fields (`subrequests`, `allowance`, `blocked`) and `nsf web status`
  (`renders.today`) make every render visible.
- Tests: `tests/webcortex/test_browser.py` (allowance, blocking, robots on sub-resources) and
  `tests/webcortex/test_orchestrator.py` (accounting, static-first ordering, switches, daily cap, critical pressure,
  stale hits). Also `tests/webcortex/test_searxng.py` (interleaving) and `tests/webcortex/test_cli.py` (defaults and
  switch).

# ADR-042 (draft) Signed node identities, signed requests and Spark-signed results; attested enrolment. R

Status: draft (lane "sign", v0.7.0). Owner decision: signed node identities, signed results and attested enrolment
for multi-node; no worker federation. Amends ADR-039 §7 (enrolment and the node requests it lists) and keeps ADR-031
(one executing node) and POC-07 (the pairing credential lives in tab memory only). Rules: docs/autonomy/RESEARCH_POLICY.md
§6. Code: `webcortex/signing.py`, `webcortex/nodes.py`, `pocket/research_routes.py`, `pocket/web/node-key.mjs`.

## Context

ADR-031 and ADR-039 deferred federation because "it needs enrolment, attestation and result signing, none of which
exist". ADR-039's minimal nodes were enrolled from the CLI by pairing label and then trusted on their bearer
credential alone: a stolen credential could take, hand off or cancel research as the node, and nothing a phone showed
as "from your Spark" was authenticated beyond TLS.

## Decision

1. **Node identity.** Each node holds its own key pair, generated in the browser with WebCrypto: Ed25519 where
   `generateKey` supports it, otherwise ECDSA P-256 with SHA-256 (detected at run time; the algorithm is recorded at
   enrolment and never read from a request). The private key is generated non-extractable and stored as a
   `CryptoKey` in IndexedDB (`nsf-node`), so it survives reloads without script ever reading its material. The
   pairing credential stays in tab memory (POC-07); a reload still unpairs, and re-pairing makes a new device that
   must enrol again with the same key.
2. **Attested enrolment.** A paired device asks for a challenge (`node_enrol` challenge). The Spark issues 32 random
   bytes bound to the pairing family (`f-` + 16 hex of the salted family digest), the node id (`n-` + 16 hex of the
   salted device digest) and an expiry (5 minutes), one pending challenge per device. The device signs
   `NSF-ENROL-1\nchallenge\nfamily\nnode\nexpires` and sends its raw public key (attest). The Spark verifies, keeps the
   key as pending (1 hour) and records nothing as a node. An Ed25519 key of small order (libsodium's blocklist, in
   every encoding, including -0 and the non-canonical y = p and y = p + 1) is refused as `bad_key`: under such a key
   the signature (R = identity, S = 0) verifies for every message, so it proves no private key. `signing.verify` never
   accepts a signature by one either. The device shows the fingerprint it computed itself
   (SHA-256 of `NSF-KEY-1\nalg\nkey`, 128 bits as eight groups of four hex digits). The console lists pending keys
   with the same fingerprint, and the owner enrols the device only after comparing the two
   (`POST /api/web/nodes {"action": "enrol", "fingerprint", "label"}` or
   `nsf web node enrol NAME --fingerprint FP`). The Spark stores the public key, algorithm and fingerprint. A key
   lives on one node row: approving it revokes any other live row that holds it (the old row of a browser that was
   re-paired and enrolled again with the key it kept).
3. **Signed requests.** Every state-changing request from a node enrolled with a key (`research_submit`,
   `research_continue`, `research_cancel`, `research_session` except `status`) carries `X-NSF-Timestamp`,
   `X-NSF-Nonce` and `X-NSF-Signature` over
   `NSF-REQ-2\nfamily\nnode\nPOST\npath\nsha256(body)\ntimestamp\nnonce`, where `family` and `node` are the family
   tag and node id of the challenge, which `node_enrol` status also returns. The signature thus verifies for that
   device row only. `NSF-REQ-1` (without them) let a captured request signed for one row be replayed with another
   row's credential when both held the same key; it is no longer accepted. A node's key does not change, so a node
   enrolled under `NSF-REQ-1` needs no re-enrolment: the current Pocket signs `NSF-REQ-2` with the same key. The Spark refuses
   it with 401 and a short code unless the signature verifies with the recorded key and algorithm
   (`signature_invalid`), the timestamp is within ±120 s (`stale_request`), the nonce is new for that device
   (`replayed_request`; kept 240 s) and the node belongs to the caller's family (`wrong_family`). It also refuses
   unsigned changes from a signed node (`signature_required`), a revoked node (`node_revoked`), and a device with no
   key (`unknown_node`). A signature that is present is verified whoever sends it. Pocket has no evidence-review
   decision route today; one added later belongs in the signed set.

   Pocket voice follows the same rules (`voice/bridge.py`). A typed or spoken command that would submit, cancel or
   continue research goes through `research_routes.gate` for an unsigned request and, for a task, `nodes.may_drive`.
   A voice command carries no node signature, so a device enrolled as a signed node changes research only with the
   Research panel's signed controls. By voice it can still read, show and explain research, and voice tells it to
   use the panel for changes. Any device is refused, by voice as over HTTP, when it tries to cancel or continue a
   session another device owns. Continuing by voice counts in the family's hourly limit, like `research_continue`. A
   Pocket voice session belongs to the device that started it. Signing voice commands was rejected: spoken commands
   are recognised on the Spark from many audio chunks, and a signature over the audio would not show which command
   the Spark heard. The owner console's voice is unchanged.
4. **Spark-signed results.** The Spark holds its own Ed25519 key, generated once with mode 0600 next to the instance
   (`INSTANCE.spark-key`, `*.spark-key` is git-ignored; `--spark-key PATH` overrides). The bridge refuses a key file
   that is not a regular 0600 file owned by the user. The `pair` reply includes the Spark's public key and
   fingerprint, and the device pins them with the credential (tab memory, or the vault when the owner keeps the
   pairing there). Every reply of a research route and of the `push` route carries `X-NSF-Spark-Signature` over
   `NSF-RES-1\nstatus\npath\nrequest nonce\nsha256(body)`. This covers task results, lists, evidence, session views
   and hand-off grants, node status and push status. It also covers every refusal and error: the routes' own 401,
   409 and 429 answers, and the bridge's 400, 403 (no or unknown credential, wrong `Origin`), 404 (research or push
   off), 500 and 503. Pocket checks the signature against the pinned key before showing anything as from the Spark. An
   unverified success is refused ("could not confirm"), and an unverified error is reduced to its status. Browsers
   without WebCrypto Ed25519 verify with a small RFC 8032 verifier in `node-key.mjs`.

   These replies stay unsigned:
   - `421` for a `Host` the bridge does not serve, and `404` for an unknown route. Both are answered before any
     route is known.
   - Malformed HTTP that the server library refuses before the handler runs (`400`, `414`, `431`, `501`).
   - The field, presence and voice routes, and `pair` (its reply carries the pin).

   Pocket forgets a pairing on any `403` (`mobile.js` `revoked()`), signed or not, including the unsigned `403` of a
   field route. The `403` of a research route is signed, but anyone who can reach the Spark gets a genuine signed
   `403` for any nonce by sending the request without a credential. So a rogue bridge or a TLS interceptor can make
   Pocket forget its pairing and pair again. That is a denial of service only: it reveals nothing and changes
   nothing on the Spark.
5. **Revocation and rotation.** Revoking a node deletes its key, nonces and pending enrolment and releases its
   sessions. A later signed request fails with `node_revoked`; unsigned, it is an ordinary paired device again.
   Key rotation is re-enrolment: a new challenge, attestation and owner approval replace the key. Revoking a key and
   replacing it both record its fingerprint in `node_keys_retired` (migration 13), unless another live row still
   holds it. Approving the same key again removes it from that table. A checkpoint restore or rewrite replays both
   revocations and retired keys: any restored row that holds a retired key is revoked. So neither a revocation nor
   a rotation is undone by restoring a copy made before it. The replay also copies the live `node_nonces` of the
   last 240 s, so a signed request accepted after the checkpoint stays a replay in the copy. All of this happens in
   the replay's single transaction, before the crash-safe publish.
6. **Back-compatibility.** Migration 12 adds `alg`, `public_key`, `key_fingerprint` and `signed` to
   `autonomy_nodes`, plus `node_enrolments` and `node_nonces`. Nodes enrolled before it have `signed=0` and no key.
   They are not nodes until they re-enrol: session actions answer 409 `reenrol`, Pocket and the console say so, and
   they cannot be hand-off targets. A node marked signed never has an unsigned change accepted. Migration 13
   releases any session such a node owned when version 12 arrived, and any offer made to it. From then on
   `nodes.may_drive`, `view`, `take` and `renew` ignore a lease held by anything but a live signed node, so a
   leftover lease never locks the family out. Pairings made before
   this change have no pinned Spark key, so Pocket asks the user to pair again before showing research.

## Alternatives rejected

- **Signing with the bearer credential (HMAC).** The credential is a shared secret that the Spark also holds, so it
  cannot attest a device. It is also in tab memory only, so it would not survive a reload.
- **Extractable keys or keys stored in the vault.** These would expose key material to script. Non-extractable
  `CryptoKey` objects in IndexedDB give persistence without exposure.
- **Approval without fingerprint comparison (trust on first attestation).** A stolen credential could enrol an
  attacker's key. Comparing the device's own fingerprint with the console's closes that.
- **Spark P-256 so every browser verifies natively.** The owner chose Ed25519 for the Spark. The fallback verifier
  is a few dozen lines over public data and is checked against the Spark's own vectors.
- **Worker federation.** Still out of scope: nodes submit, continue, read and own sessions, and only the Spark
  executes (ADR-031).

## Consequences

- A stolen pairing credential no longer lets an attacker drive a node's sessions, over HTTP or by voice. A stolen
  credential of an ordinary family device still reads and submits as before (ADR-028); a stolen node credential
  reads only, because every change of a node needs its key.
- A substituted or tampered research reply (rogue bridge, TLS interception with an accepted certificate) is refused,
  provided the pin was not substituted at pairing. Pinning is trust on first use. The owner can compare the Spark
  fingerprint that the console and Pocket both show.
- Not covered: signatures on field and voice replies, the console (loopback with its own token), and the pairing
  exchange itself. Voice commands follow the node and session rules above.
- Clock skew over 2 minutes refuses a node's changes. Pocket says to correct the clock.
- Tests: `tests/webcortex/test_signing.py`, `tests/test_pocket_signing.py`, `tests/test_pocket_autonomy.py`,
  `tests/test_pocket_voice_nodes.py`, `tests/console/test_console_autonomy.py`, `tests/instance/test_checkpoints.py`
  and `tests/instance/test_migrations.py` (retired keys, nonces, migration 13), `tests/js/signing_check.mjs` (with
  `tests/js/spark_vectors.json`), `tests/e2e/test_pocket_signing_e2e.py`, `tests/e2e/test_pocket_handoff_e2e.py`.
- A security review of signed nodes and push found A-1 (voice bypassed node signatures and session ownership) and
  A-2 to A-6, B-0 and B-1. Each is fixed as described here and in ADR-040, and each has a regression test that
  fails before its fix.

# ADR-043 (draft) Guided phone setup: QR codes, a plain-HTTP page that offers the CA, and `nsf start`. R

## Context

The owner found the Quick start's phone step unusable for people who are not technical (2026-11-23). It asked them to
find a certificate file by its path, compare a long fingerprint, choose a URL and type a 32-character pairing code;
starting NeuroSciFi also took three terminals with long commands. A phone must trust the Spark's private CA, because
Pocket needs a secure context for its service worker, WebCrypto keys and the microphone. Only an outside service (a
publicly trusted certificate through a relay, Tailscale or a domain the owner controls) removes the certificate
install. The owner chose to stay fully local, with the install guided step by step.

## Decision

1. **One command to run it.** `nsf setup` records its choices in `~/.config/neuroscifi/setup.json` (0600; rewritten
   only when they change). `nsf start` runs the research worker, the owner console and the Pocket bridge from that
   record in one terminal, prefixing each line with the service's name. It opens the console through a 0600
   forwarding page next to the console URL file, so the browser's command line holds a file path, never the
   session token (other local users can read command lines). Ctrl-C, SIGTERM and SIGHUP stop every service; when
   one service stops, the others are stopped and the exit code is 1. In this terminal the bridge's start-up code
   line is replaced by a pointer to Add a phone.
2. **Add a phone.** The console's *Add a phone* view, and `nsf add-phone` in a terminal, ask the running bridge for a
   new invitation over its owner-only control socket (0600, peer uid checked, unchanged). An invitation now carries
   an id, the setup page URL and the pairing URL. The console page draws two QR codes from module matrices
   (segno), so no image or markup crosses from the bridge into the page. It asks `status <id>` every 2 s (`used`,
   `pending`, `expired`, `unknown`), says when the phone has paired, and renews expired codes while the view is open,
   at most 6 times without a click.
3. **Code 1: the setup page.** `nsf pocket serve --setup-port` (`nsf setup` uses the Pocket port minus one) serves
   plain HTTP on the LAN: a few static files, `setup.json` and the CA certificate in DER. The certificate is offered
   for 15 minutes after the owner asks for codes (**Add a phone**, `nsf add-phone`); bridge start-up and the
   console's automatic renewals do not open it. The page never serves the app, the API, a pairing code or a
   fingerprint. Its CSP allows scripts and styles from itself only, and `connect-src` adds exactly the Spark's
   HTTPS origin, which is used for a trust probe: a `no-cors` request for `/icon.svg` succeeds only once the phone
   trusts the CA. The page then says so by itself. The listener is a `PocketServer` (the bridge's per-address and
   total connection caps and request deadline), and http.server's own error pages carry the same headers. The
   steps shown depend on the phone: iPhone and iPad, Android, or other.
4. **Code 2: pairing.** `https://<LAN address>:<port>/#pair=<code>`. A browser never sends the fragment to any
   server. Pocket reads it, removes any `#pair=` fragment from the address bar and the tab's history at once, fills
   the pairing form, brings the card into view and waits for a tap on **Pair**. It then sends
   `"via": "invitation"`, so the bridge accepts only its own invitation from a link, never a linked device's handoff
   code: a link cannot put a device into someone else's family. A refused invitation is cleared from the form.
   Typed codes may contain spaces (the console shows them in groups of four).
5. **Status.** `status <id>` answers `used`, `pending`, `expired`, `replaced` (a newer code was issued, for example
   by another console tab or `nsf add-phone`) or `unknown`. It is read under the pairing lock, and "used" is recorded
   before the code is cleared, so the console never sees a just-used code as expired. The console renews only
   expired codes, and it stops on `replaced` and says why.
6. **IP addresses in the codes**, not `.local` names. Android resolves `.local` unreliably, and browsers never apply
   HSTS to an IP address, so the plain-HTTP page stays reachable after the HTTPS app has set HSTS for the host.
   `nsf setup` passes `--allowed-host` for every name and address on the certificate, so the bridge and its setup
   page answer 421 to any other Host. This closes DNS rebinding from a web page opened on another LAN device.
7. **Unpaired Pocket** shows where pairing starts, at the top of the page.

## Security analysis

- **What the plain-HTTP port gives away:** a public certificate and static instructions. Nothing on it changes
  state.
- **A substituted certificate (the main risk).** An active attacker on the LAN who answers for the Spark's address
  during setup (ARP or IP spoofing) can serve their own page and certificate. A phone that installs that
  certificate trusts a root CA without name limits; on iOS with full trust, the attacker can then intercept all of
  that phone's HTTPS, not only Pocket's. Plain HTTP cannot prevent this, and the 15-minute window limits only when
  the real Spark offers the file, not what an impostor serves.

  What the design does about it:
  - The page shows no fingerprint, because an impostor would show its own.
  - The owner's console, which is trusted (loopback with a session token), shows the CA's full SHA-256 next to
    code 1, with the exact place on the phone where the installed certificate's fingerprint appears.
  - The page, the console and the README say to do this on a home network you trust.
  - For shared networks the manual path stays: copy the certificate over a channel you trust, compare the
    fingerprint, then scan only code 2 (TLS.md §2).

  Before this change the owner chose the channel and could choose a trusted one; now unauthenticated HTTP is the
  default path, so this risk is new and is the price of the simpler setup. The owner accepted it for a home
  network, with the manual path documented.
- **The pairing code.** Code 2's link is recorded in the browser's own history before any script runs (and may be
  synced by the browser vendor). Pocket clears only the tab's history. The code works once, for 5 minutes, so a
  recorded link is useless once the phone has paired. Anyone who sees the owner's screen within those 5 minutes
  could pair, as with the printed code before. The console says when the code was used.
- **Denial of service on the setup port:** the bridge's connection caps apply.
- **`nsf start`:**
  - It starts services with `python -P`, so nothing is imported from the current folder.
  - It accepts a setup record only when it belongs to this user and no one else can write it.
  - It opens the console through a 0600 file with a random name in a new 0700 folder.
  - It reads the console URL only from a file of this user written after the start.
  - Its services receive SIGTERM when the supervisor dies, even by SIGKILL (Linux `PR_SET_PDEATHSIG`).
- **Console API** (`POST/GET /api/phones/invite`): session token required, loopback only, `no-store`. The status
  request returns a single state word.

## Alternatives rejected

- **Publicly trusted certificates** (relay, Tailscale, a domain): these need an outside service; the owner chose
  fully local. TLS.md keeps documenting them for owners who want them.
- **The CA from the HTTPS port:** the phone would show a certificate warning first, which teaches people to click
  through warnings.
- **The pairing code on the setup page:** it would cross plain HTTP.
- **A `.mobileconfig` profile:** it shows "Unsigned" unless signed, and a plain certificate installs the same way.
- **Short numeric codes:** weaker, and the QR code removes the typing anyway.
- **Pairing without a tap:** the tap confirms intent.
- **A hand-written QR encoder:** instead, one small pinned dependency, `segno==1.6.6` (BSD-3, pure Python, no
  dependencies of its own).

## Consequences

- Setup becomes: `nsf setup` once; `nsf start`; **Add a phone**; scan code 1 and follow the phone's steps (once per
  phone); scan code 2 and tap **Pair**.
- New options:
  - the bridge: `--setup-port`, `--ca-file`;
  - the console: `--pocket-db`;
  - `nsf setup`: `--setup-port`.
- New commands: `nsf start` and `nsf add-phone`.
- Each phone added from the console is its own family. A tablet joins the phone's family through **Link my phone or
  tablet**, as before.
- Not verified on real hardware (owner checklist):
  - the exact Settings wording on current iOS and on several Android makers;
  - camera scanning of a laptop screen.
- Tests: `tests/test_phone_setup.py`, `tests/e2e/test_phone_setup_e2e.py` (iPhone and Android user agents; trust
  emulated with `ignore_https_errors`), and `tests/test_setup_cli.py`.

# ADR-044 (draft) Consolidation of expiring evidence: what a learning event taught outlives the event. R

## Context

Beliefs are computed on read from the evidence journal (ADR-006), and an unpromoted event lives 7 days (MEM-09). So
on day 8 an individual that nobody taught in the meantime had forgotten everything: the posterior was 0.5 and the
executive said "observe" (audit C3, reproduced). The only way to keep what was learned was to promote each event by
hand. The brief's consolidation requirement (MEM-10) was documented as deferred. The owner decided (2026-11-23) that
what is learned must persist.

The design must keep four things that the journal gives today:

- **Exact segments.** A change the owner confirms sets a boundary at an evidence seq, and can be confirmed after
  the events around it expired (a question lives 7 days; a promoted event or a changed lifetime makes expiry out of
  seq order).
- **Samples counted once.** Copies of one `(channel, root, sample_id)` are one reading. A copy can arrive after the
  first one expired.
- **Exact forgetting.** One event, one partner or one epoch can be forgotten, and beliefs are then those of an
  individual that never had them. A restore never brings them back.
- **Cost independent of Python work per event** (ADR-006).

## Decision

1. **A consolidated reading per expired event with consent `learn`** (schema 14, `evidence_consolidated`). When
   `governance.sweep` expires such an event it keeps one row: the event's own `seq`, its epoch, channel and root,
   the corrected reading (`bit XOR action`) and its sample id. The event row is deleted and tombstoned as before.
   **Not kept:** the event id (the tombstone has it, with the seq), the actor, the action and the bit themselves,
   provenance, consent, `acquired_at`, `recorded_at`. Memory-only events expire to nothing, as before. **Every copy
   of a sample leaves its own reading**; which copy counts is decided when beliefs are read (decision 2).
   Consolidated readings never expire.
2. **Beliefs read both, and the earliest copy of a sample counts.** Every belief and likelihood reads the journal's
   events and the consolidated readings as one set ordered by seq: the tallies (`beliefs._aggregate`), a linked
   epoch's pool, the change window, epoch matching, the legacy status and the executive's "new events since".
   Copies of one `(channel, root, sample_id)` count once, and the copy that counts is the one with the lowest seq
   among the journal's learning copies and the consolidated readings together: the journal's own rule, extended
   over both tables. It is applied in SQL, never by listing readings in Python: one pass over the copies index
   finds the samples with several readings; a few are looked up by key, many are counted in one grouped pass
   (per sample, all its readings less its first; `beliefs._not_counted`), and the window and the "new events
   since" count test each reading they touch (`_EARLIEST`). Consequences, tested: a posterior is the same number
   before and after its events expire (`==`, not approximately), with confirmed changes on any scope, sampled
   events and a linked epoch; and when the owner forgets the first copy of a sample, the next copy counts, whether
   or not anything had expired, so beliefs after a forget are those of an individual that never had the event
   (a twin whose events never expire, same operations, in both orders of sweep and forget).
3. **Listings stay honest.** The journal (`list_evidence`, `nsf memory evidence`, the event lists of `explain`)
   shows episodes only. Counts say how many consolidated readings stand beside them, and
   `nsf memory consolidated` lists the readings themselves.
4. **Forgetting, with a handle for everything that is kept.** `forget_epoch` and `forget_partner` delete the
   consolidated readings they cover. `forget_evidence ID` still works after the event expired: the tombstone maps
   the id to its seq (looked up by key, then the one reading by key). Because no listing shows an expired event's
   id, a reading can also be forgotten by the seq it is listed under: `governance.forget_consolidated`,
   `nsf forget consolidated SEQ`, and a button beside each reading in the console's memory view. A camera root is
   a private name like a partner's and no longer leaves on its own, so it has its own forget:
   `governance.forget_camera`, `nsf forget camera NAME`, the console's "Forget a camera" (every epoch, events and
   readings, the changes confirmed for it; the log keeps a digest label). Each forget changes the tombstone's
   reason from `expired` (to `forgotten`, `partner_forgotten` or `epoch_forgotten`; a camera's say `forgotten`,
   the tombstone table's reasons being fixed since schema 1); that is the durable record that a reading which once
   existed must not exist (schema 14 indexes the tombstones by epoch and seq for this). A root is a forgotten root
   only when neither an event nor a consolidated reading of it is left. `forget_camera` and `forget_partner` also
   delete the confirmed and rejected changes of their scope in every epoch, and a restore replays that by the
   digest label of the deletion-log row (`governance.replay_scope_forgets`, for copies older than the forget, and
   the copy takes the record over): without it a camera forgotten while a partner of the same name kept the root
   held stayed in a restored copy as an empty sensor with its old segment. All of these go through the routes the
   other forgets use (`POST /api/forget` behind the console token; the Pocket bridge exposes no instance forget).
5. **Restore and rewrite** (`checkpoints._replay`). For each tombstone of the live file: an event in the copy
   whose tombstone says `expired`, and whose seq the live file holds as a consolidated reading, becomes that
   reading in the copy (the live row is copied); otherwise it is deleted as before. A consolidated reading in the
   copy whose tombstone no longer says `expired` is deleted. The copy takes over the stronger tombstone reason, so
   an older archive restored from the copy ends the same way. The copy's evidence sequence is raised to the live
   file's highest seq: a consolidated reading is identified by its seq, so no seq may be given to a second event.
   The live file is read in one snapshot, once the archive's copy is open: its consolidated readings are read
   for the seqs of the copy's own journal only (500 per statement, by key), because an archive can turn into
   readings only the events it holds. What a restore keeps in memory of the readings is therefore bounded by the
   archive's journal, not by everything the individual ever kept; the tombstones are still read whole, since the
   copy takes every one. **The owner's settings travel with the live file:** the replay writes
   the settings an owner may change (`consolidation`, `max_consolidated_rows`, `ask_budget_per_epoch`,
   `ask_budget_window_seconds`) into
   the copy before anything else, so an archive taken before `--no-consolidation` does not turn consolidation back
   on, nor one taken after it turn it off against a later choice. The restore result lists them
   (`settings_carried`) and the copy's deletion log records each. The settings fixed at `nsf init` (lifetime,
   journal limit, free-space reserve, temperature limit) stay as the archive has them: they cannot differ between
   an individual and its own archives. A restore without replay (`apply_tombstones=False`) is a plain copy, with
   the archive's settings: consolidation can be on in it after the owner turned it off. Restore is the recovery
   tool, so a damaged settings row does not stop it: a live row that cannot be read carries nothing, and a value
   `set_config` would refuse is not carried as it stands (consolidation goes over as what the live file acts on,
   off; a bad ask budget or window leaves the archive's); the result lists both under `settings_warnings`.
6. **A switch.** `InstanceConfig.consolidation` (default on; `nsf config --no-consolidation`, `nsf init
   --no-consolidation`). Off gives the sweep of schema 13 exactly. Turning it off stops further consolidation;
   readings already kept keep counting until they are forgotten. Settings saved by 0.7.0 have no such field and
   read the default. **Never on by truthiness:** a saved value that is not a JSON boolean (a hand-edited `"no"`, a
   `1`) reads as off, the reading that cannot keep what an owner opted out of; every sweep and `nsf config` report
   it until `nsf config` sets a valid value. `nsf config` migrates the file and changes the setting without sweeping first, so on an
   upgrade it takes effect before any event is consolidated (tested on a version-13 file; the services must be
   stopped, because the console and the Pocket bridge sweep on access). Every change of a setting leaves a row
   in the deletion log (kind `setting`), so the log shows from when to when expired events left nothing.
7. **The executive's horizon is monotone.** "Evidence through #N" is the highest seq the epoch ever held (journal,
   consolidated readings, tombstones). It no longer falls when a memory-only event expires or the latest event is
   forgotten; no learning reading lies between the old value and the new one, so the count of new events since a
   question is unchanged.
8. **A limit, as a guard rail.** `InstanceConfig.max_consolidated_rows` (default 1,000,000; 0 = no limit;
   `nsf config --max-consolidated-rows N`, `nsf init --max-consolidated-rows N`; carried through a restore like
   `consolidation`). Every reading counts towards it, a later copy of a sample too: it bounds rows of the file.
   At the limit the sweep stops adding readings: an expiring learning event leaves nothing, as with consolidation
   off, and the individual stops outliving new evidence until the owner makes room. A sweep that would cross the
   limit partway keeps the oldest of its learning events, by their place in the journal, until the limit is
   reached; the rest expire, and the result says how many of each (`consolidated`, `not_consolidated`; the
   deletion log and the belief revision record it too). **The limit never deletes a reading**: lowered below what
   is kept, it only stops further consolidation. The owner is told in plain words, with what to do (raise the
   limit, or forget an epoch, a partner or a camera), by the sweep that met the limit, `nsf regulation`,
   `nsf birth`, `nsf config` and the console's Regulation card; from 80 % of the limit the same places warn. A
   saved value that is not a whole number from 0 up is never read as "no limit": the default applies and the same
   places say so. The count for the limit is made only by a sweep that meets a learning event.
9. **The cost is shown before it is felt.** `nsf regulation` and the console's Regulation card name the epoch with
   the most consolidated readings, how many it holds, and how long its beliefs take to compute now
   (`largest_epoch`: one grouped pass over the readings and one `compute()`; absent while nothing is consolidated).

## Measured

`docs/quality/evidence/accel_consolidation_cost.json` (`scripts/bench_consolidation.py`), on the reference host (CPU
time, best of 31 calls; the sweep is one wall-clock call). A third of the events carry a sample id, each sample once.

| Learning readings in one epoch | `compute()`, all raw events | `compute()`, all consolidated | The sweep that consolidates them | Bytes per consolidated reading, with indexes |
|---|---|---|---|---|
| 200 | 2.06 ms | 1.92 ms | 4 ms | n/a (one page) |
| 10,000 | 6.72 ms | 4.05 ms | 85 ms | 93 |
| 100,000 | 55.17 ms | 23.99 ms | 930 ms | 96 |

The copy rule, where it costs most: every reading has a sample id (`bench_consolidation.py WORKDIR copies`; one
wall-clock call each, milliseconds, before → after the second review pass, which replaced one query per copied
sample by passes over the index):

| Readings in one epoch, all sampled | Each sample recorded | `compute()` | `learning_since()`, one root | `explain()` | Forget one reading |
|---|---|---|---|---|---|
| 10,000 | once | 19 → 19 | 3 → 0 | 38 → 42 | 19 → 19 |
| 10,000 | one in ten twice | 21 → 21 | 7 → 0 | 50 → 53 | 22 → 23 |
| 10,000 | twice | 39 → 26 | 21 → 0 | 98 → 60 | 38 → 27 |
| 100,000 | once | 57 → 57 | 34 → 1 | 156 → 121 | 58 → 57 |
| 100,000 | one in ten twice | 105 → 86 | 73 → 1 | 287 → 178 | 100 → 87 |
| 100,000 | twice | 308 → 97 | 235 → 0 | 818 → 200 | 271 → 95 |
| 1,000,000 | once | 502 → 454 | 381 → 5 | 1484 → 922 | 538 → 456 |
| 1,000,000 | one in ten twice | 1477 → 744 | 1124 → 5 | 3927 → 1496 | 1381 → 744 |
| 1,000,000 | twice | 5370 → 791 | 4238 → 4 | 14595 → 1587 | 5148 → 793 |

- In every row of the first table the posteriors after the sweep equal those before it.
- An epoch with nothing consolidated pays 2 to 3 % more for `compute()` than before schema 14
  (1.955 to 2.0 ms at 200 events, 5.044 to 5.193 ms at 10,000).
- Half of the consolidated time in the first table is the pass that finds samples with more than one reading
  (decision 2): about 0.3 microseconds per reading that has a sample id, nothing for an epoch whose readings have
  none. With the first version of this decision (only the first copy kept, no such pass) the 100,000 row read
  11.8 ms; that version made forgetting inexact (see "Alternatives rejected").
- With every sample recorded twice the grouped pass costs about as much again. At a million readings that all
  carry a sample id, `compute()` takes 0.45 s when each sample was recorded once and 0.79 s when each was recorded
  twice; with one query per copied sample, as first built, the second case took 5.4 s (second table).
- At 1,000,000 readings of 200 roots: a sweep that expires one event per root takes 0.08 s (5.3 s before the index
  by root), listing the held roots for a web query 0.2 ms (51 ms), a forget of an unknown id 0.0 ms (195 ms).

Per reading the consolidated path is cheaper than the raw path it replaces (ordered passes over covering indexes,
no sort). What changes is the bound: raw events were limited to one lifetime's worth, consolidated readings
accumulate for the life of the epoch, up to the limit of decision 8.

The limit and its reporting (`scripts/bench_consolidation.py WORKDIR limit`, wall-clock, one call each, on a file
of readings that all carry a sample id; `docs/quality/evidence/open_items_consolidation.json`):

| | 100,000 readings | 1,000,000 readings |
|---|---|---|
| The count a sweep makes for the limit (only when it meets a learning event) | 0.4 ms | 3.6 ms |
| A sweep of 1,000 expiring learning events, room for all | 76.3 ms | 477.7 ms |
| The same with room for 100 (100 kept, 900 expire to nothing) | 70.2 ms | 475.8 ms |
| The same at the limit (none kept) | 68.1 ms | 473.1 ms |
| `nsf regulation`, of which the largest epoch's `compute()` | 60.6 ms, 57.4 ms | 491.4 ms, 459.1 ms |
| Python memory while a restore reads the live file: every reading, as before | 56.1 MiB | 548.7 MiB |
| The same, the readings of an archive with 1,000 events in its journal | 23.9 MiB | 231.9 MiB |

Each sweep in the table also recomputes the epoch's beliefs once for its revision, which is most of its time at a
million readings. What a restore still holds in the last row is the tombstones, which it reads whole as before:
one row of five short fields per event that ever expired or was forgotten, about 230 MiB of Python objects per
million. Reading those in bounded pieces needs the live file's snapshot held open through the replay; not done.

## Alternatives rejected

- **Counts per epoch, channel and segment** (the audit's sketch). They cannot be cut at a boundary confirmed
  later, cannot tell which sample they already hold, and cannot give back one event. A wrong count is a wrong
  belief with no way to notice.
- **Keep only the first copy of a sample** (the first version of this decision). Later copies expired to nothing,
  so forgetting the first copy after a sweep left the sample uncounted, where forgetting it before the sweep, or
  with nothing expired, let the next copy count (review F1: n = 3 against n = 4). Since every command sweeps
  first, the wrong order was the normal one.
- **Promote every learning event.** It keeps the whole episode: who, when, the raw action and bit. The lifetime
  exists so that episodes do not persist.
- **A hash of the sample id.** The dedupe runs in SQL; a salted hash needs a function registered on every
  connection that reads beliefs, and a connection without it would count copies twice. The sample id is kept in
  clear, as the journal holds it, and goes with every forget.
- **A maintained summary table** in place of the rows, or a cache of computed beliefs beside them. Either is a
  second source of truth for the individual's beliefs, to be invalidated by every forget, every confirmed change,
  every sweep and every restore; a write path that forgot to do so would give a wrong belief with nothing to
  notice it. Below about a million readings in one epoch it is not needed (table above), and the limit keeps a
  file there by default. Not built; the rows stay the truth.
- **Compacting old readings into counts at the limit**, so that nothing is ever turned away. A count cannot give
  one reading back: the tombstone of an expired event maps its id to a position in the journal, not to a
  channel, root and value, so forgetting by id and by position would stop working for everything compacted, and
  a change confirmed later could not cut the count at its boundary. The limit therefore stops consolidation and
  says so, and the owner decides what to give up.
- **A maintained list of copied samples**, to spare an epoch whose readings all carry sample ids the pass that
  looks for samples recorded more than once (about 0.3 s per million readings on every read). The pass is itself
  the existence test: "no sample has two readings" cannot be read off an index without visiting each sample, so
  skipping it needs state kept in step with every sweep, forget and restore. Left as it is.
- **Counting consolidated readings against `max_evidence_rows`.** A full journal empties itself as events expire;
  with consolidated readings counted it never would, and a small limit would pause an individual for good.

## Consequences

- An individual keeps what it was taught. Day 8, and day 400, answer as day 1 did.
- **What "expired" means changed.** After its lifetime an event with consent `learn` leaves: its position in the
  journal, its epoch, channel and root, the corrected reading and its sample id. The root's name and the sample
  id therefore stay in the file until the reading is forgotten, where 0.7.0 scrubbed a root at expiry. This is
  the owner's decision; the switch restores the old meaning, and each kept datum has a forget the owner can
  reach: the reading by its seq, the camera or partner by its name, the epoch.
- A suspected change that nobody answers no longer disappears with its events: it stays suspected, and the
  executive may ask again (within the ask budget's window).
- The change window and epoch matching reach back past the lifetime: an epoch that was quiet for a month is
  matched and checked against what it knew.
- Growth: about 95 bytes per consolidated reading with its four indexes, one reading per expired learning copy,
  up to `max_consolidated_rows` (a million by default: about 95 MB). `nsf regulation` and `nsf birth` report the
  count and warn from 80 % of the limit. `max_evidence_rows` still bounds the journal only.
- **At the limit the individual stops keeping what new events teach.** That is the 0.7.0 behaviour for those
  events, announced instead of silent; what was kept before stays and keeps counting. The owner raises the limit
  or forgets what is no longer needed. Which events are kept when a sweep crosses the limit is decided by their
  place in the journal, not by what they teach.
- A camera forgotten while a partner of the same name is still held (or the reverse): its readings, events and
  changes go, in the live file and in every restore. Its name as that kind goes too (ADR-048, schema 15): until
  then it stayed in the text of belief revisions, questions and decisions, because scrubbing worked per root name.
  A bare mention of the name outside a question or decision about the forgotten scope stays, since it may be the
  other one; it goes when the last evidence of that name does.
- Version 14 was amended twice before any release. A file that reached version 14 under an earlier build of this
  branch gets the current indexes when it is next opened (`schema.ensure_indexes`); the sweeps it ran under the
  first build kept only the first copy of a sample, and that history is not repaired.
- `compute()` is linear in the readings of an epoch, as it was in its events. (Amended by schema 16, below: the
  pass that grouped every reading by sample to find the copied samples is gone when few samples are copied; the
  tallies' pass over the readings stays.)
- The prototype's `legacy` status reads the same readings, but without event ids to order them by its sums can
  differ from `legacy_live`'s in the last bits once readings are consolidated.
- Two files that both continue after a restore (the restored copy and the file it was restored from) issue seqs
  independently from then on. Restoring an archive of one into the other can then delete a consolidated reading of
  the same seq and epoch if the other file forgot an event there. It cannot bring anything back. Use one of the
  two files.
- Tombstones keep event ids with their seqs, as before (the audit's open item on ids in clear is unchanged, and
  consolidation now depends on those rows: if tombstones are ever pruned, the id-to-seq map and the forget record
  must move first).
- An older binary refuses a schema-14 file ("Instance schema 14 is newer than this software (13)") and changes
  nothing; rollback is a restore of the pre-upgrade checkpoint (MIGRATIONS.md).
- Tests: `tests/instance/test_consolidation.py` (92 cases, each run on both paths of the copy rule),
  `tests/instance/test_consolidation_limit.py` (the limit at, below and across; the warning; the setting; what a
  restore reads), `tests/console/test_console_api.py` (the readings and the two forgets over HTTP), and the expiry
  tests of `test_erasure.py` and `test_journeys.py`, which keep their old assertions under `consolidation=False`.

## Amendment (schema 16): finding the copied samples without a pass over every reading

Beliefs count the earliest copy of a sample. To know which samples have more than one reading, every belief
computation grouped all of the epoch's readings by sample (about 0.1 to 0.3 s per million readings that carry a
sample id), even when no sample was ever recorded twice. A belief cache, or a maintained list of copied samples, was
rejected (phase 3): a second source of truth that every forget, sweep, confirmed change and restore must invalidate.

Decision: a column `copied` on each consolidated reading and three triggers that keep one rule: **of any two
readings of one sample (epoch, channel, root, sample id), at least one has `copied=1`**. The reading that arrives
second, by an insert or by an update of those columns, finds the first and is flagged; a flag is never cleared (a
trigger refuses it). Why this is not the rejected design:

- It is a one-sided hint, not a fact. A flag can be stale (the other reading was forgotten since); beliefs then look
  up one sample by key, find one reading, and drop it. A missing flag is impossible by the rule, so no sample with
  several readings is ever missed. Nothing reads `copied` as a statement about a sample.
- No code path maintains it: SQLite does, in the same statement as the write, whichever path writes (a sweep, a
  restore's replay, a forget). Nothing needs to invalidate it.
- Beliefs are computed exactly as before from the same rows. `beliefs._copied_samples` returns the same samples as
  the grouped pass: up to 256 flagged samples are counted by key; beyond that, or on a connection that has not
  migrated, the grouped pass runs as before.

Measured on files of a million readings each with its own sample id: see
`docs/quality/evidence/remaining_consolidation.json`. Tests: `tests/instance/test_consolidation_copied.py` (the rule
under random inserts, deletes and updates; the migration of a version-14 file; no grouped pass without copies;
beliefs identical on the three paths), and every test of `test_consolidation.py`, which now runs on three paths:
flags, the grouped pass, and no flags.

**A restore streams the tombstones.** The live file's tombstones (about 230 MiB per million as Python rows) are no
longer read whole: the replay applies them in batches of 2,000 while the snapshot of the live file stays open
(`checkpoints._snapshot`), so a forget committing meanwhile is still wholly in the replay or not at all. The cost:
the read transaction on the live file lasts as long as the replay, so its WAL cannot be checkpointed past that
snapshot until the replay ends (writers are not blocked).

# ADR-045 (draft) Which graph is promoted: the better learner's, and never one worse than no graph. R

## Context

`learner.fit` trains two matrices on one stream: a dense one, and a sparse one with k inputs per receiver whose
support is rewired by edge exchange (GRA-02, the brief's fixed active-edge budget). Until this decision the snapshot
of a candidate was always cut from the dense matrix (`learner.pack`: the smallest file within 2 % of the dense FP32
error), and the sparse matrix was only measured (audit C6).

Measuring whether the sparse graph should be promoted found a defect instead. The dense update takes a step from
every input, each normalised by that input alone, so its error grows with the number of populations. On the lab's
stream (`scripts/bench_graph_learner.py sizes`, validation MSE; the noise floor is 0.0042, a graph with no edges
0.018):

| Populations | Dense fit | Snapshot gate-v1 cuts from it | Sparse learner |
|---|---|---|---|
| 24 | 0.0049 | 0.0043 | 0.0043 |
| 96 | 0.0097 | 0.0085 | 0.0043 |
| 160 | 0.115 | 0.0192 | 0.0044 |
| 256 | 3628 | 255 | 0.0043 |

A cut of a failed fit is always "within tolerance" of that fit, and with no incumbent every check of the gate
passed, so the first candidate of a context with 160 or more populations was promoted although it predicted worse
than no graph. `datasets.add` accepts 1024 populations; the built-in source (`datasets.simulate`) has 24, where
the two learners agree to within 0.3 %.

## Decision

1. **The gate has a sixth check that no rule can waive: `beats_no_graph`.** `graphs.learn` records the error of a
   graph with no edges on the candidate's validation data (`no_graph_validation_mse`) and whether the snapshot is
   below it (`beats_no_graph`). `graphs.promote` rejects a candidate that is not, with a reason naming both errors,
   under every rule version. A candidate without the record (learned by earlier software) has the five checks it
   always had, and its decision record is unchanged.
2. **A scoring rule may pack the sparse learner's graph.** Two optional parts of a rule definition:
   `pack_candidates.learned_sparse` (the sparse matrix is a candidate, as FP32 and at its own degree with each bit
   width) and `pack_reference: "best_learner"` (the tolerance is measured against the lower validation error of
   the two learners' FP32 graphs). Selection is unchanged in kind: the smallest file within the tolerance; with
   `learned_sparse`, files of equal size are separated by the lower validation error. A rule without the two parts
   keeps its meaning, its sha256 and its result, byte for byte.
3. **New individuals are born with two rules, `gate-v1` and then `best-learner-v1`** (`store.BIRTH_RULES`):
   gate-v1 with both parts, the same thresholds and the same 4096-byte budget. Graphs are learned under the last.
4. **Existing individuals keep exactly the rules they have** (superseded by ADR-047: an individual still on
   gate-v1 adopts best-learner-v1 at its next open, and an active graph worse than no graph is archived; both are
   recorded and reversible). Their owner adopts the rule with
   `nsf graph rules add --builtin best-learner-v1 --owner-approved`, and goes back with
   `nsf graph rules add --builtin gate-v1 --as gate-v1-again --owner-approved` (rules are immutable and the newest
   is used, so going back is a new version with gate-v1's content). Candidates keep the rule they were learned
   under either way.

## Measured

`scripts/bench_graph_learner.py select`, 64 worlds per configuration (16 at 160 and 256 populations), held-out
error on 1024 steps neither the fit nor the selection saw
(`docs/quality/evidence/accel_decisions_graph_learner_select.json`):

| Configuration | gate-v1 | best-learner-v1 |
|---|---|---|
| Lab world (24 populations, 3 inputs) | dense degree 3, 4 bit, 247 bytes; 0.004321 | the same size in every world; the sparse learner's weights in 64 %; 0.004313 |
| Lab world, 512 steps | 0.004327 | the same file in every world |
| 6 true inputs, edge budget 3 | dense degree 6; 0.004462 | the same file in every world |
| 2 true inputs, edge budget 3 | dense degree 2; 0.004291 | the same file in every world |
| 96 populations | dense degree 2, 715 bytes; 0.008368 | sparse degree 3, 859 bytes; 0.004321 |
| 160 populations | 0.017967; worse than no graph in 8 of 16 worlds | 0.004319 |
| 256 populations | 322.99; worse than no graph in 16 of 16 | 0.004317 |

The acceptance scenarios that learn a graph, run once born under gate-v1 alone and once under the birth rules
(`accel_decisions_graph_birth_rule.json`): all pass both ways and no check changes; the leaves of their reports
that differ are listed there.

## Alternatives rejected

- **Adding the sparse graph as a candidate under the dense reference only.** Chosen in 64 % of lab worlds for an
  error 0.3 % lower and never where it matters, because the reference is the failed fit.
- **Refusing in `learn`.** A candidate that learned nothing is still worth showing with its numbers; the place
  where a graph becomes the one in use is the gate.
- **Making the no-graph check part of a rule version.** It would leave every individual on gate-v1 exposed, and a
  graph worse than none is wrong under any exam.
- **Repairing the dense learner.** Its arithmetic is the lab's, value for value, and the published reproduction
  depends on it. The sparse learner is the one the brief specifies.
- **Raising the byte budget.** At 512 populations the sparse 4-bit file is about 5.9 kB, over the 4096 bytes both
  birth rules allow, so nothing is promoted there, as before. A larger budget is a resource decision for the owner
  (a rule file with `byte_budget`).

## Consequences

- A new individual's promoted snapshot carries the sparse learner's weights in about two lab-size worlds of three:
  the same size, degree and support, an error within 0.3 %. Its bytes and sha256 differ from what gate-v1 would
  have stored.
- The first physical test (D1) is preregistered as "learned and promoted under gate-v1"; its scenario creates its
  individuals under gate-v1 explicitly and reproduces its recorded numbers. It passes with the same rates under the
  birth rules.
- `docs/quality/evidence/acceptance.json` was recorded under gate-v1 and is not regenerated. Run today,
  `growth_retention` differs from it in the leaves listed in `accel_decisions_graph_birth_rule.json`, and its
  `thresholds` name `best-learner-v1`.
- A decision record of a candidate learned from now on lists six checks. Readers of `decision.order` see the
  sixth; nothing else in the record moved.
- Older software reading a file whose latest rule is `best-learner-v1` ignores the two parts and packs as gate-v1
  while the candidate records `best-learner-v1`. The schema did not change, so nothing refuses such a downgrade:
  go back with the command above first.
- Tests: `tests/instance/test_graphs.py` (the gate check, the birth rules, the built-in command, a candidate
  without the record), `tests/instance/test_learner.py` (packing).

# ADR-046 (draft) A forecast is read through what the settled forecasts of its kind show. R

## Context

ADR-038 gave every prediction a forecast probability and scores the settled ones (`evolution.calibration`: Brier
score, reliability bins, a snapshot at every settlement). The audit found that the score went nowhere (W7): a
prediction's probability is the confidence of the item or hypothesis it is about, and no number the individual
uses changes when its forecasts turn out to be systematically too high or too low.

Reading the code for this decision gave the map of what is written and what is read:

| Written | By | Read by |
|---|---|---|
| `web_predictions.probability` of a "still holds" prediction (the item's confidence) | `predictions.add` | `calibration`; `autonomy.candidates` (the signal `prediction_uncertainty` of a due prediction); the error analysis text |
| `web_predictions.probability` of a hypothesis prediction (its posterior, or the owner's estimate) | `evolution.predict` | `calibration`; `autonomy.candidates` (`prediction_uncertainty` and the information gain of the hypothesis: both decide which autonomous investigation runs next) |
| Outcomes `confirmed`, `error`, `inconclusive` | `predictions.check`, `evolution.observe`, `expire` | `calibration`, `metrics`; `curiosity.rank` (a recent error raises an item's importance) |
| Calibration snapshots (`web_metrics`, kind `calibration`) | `record_calibration` at every settlement | `nsf knowledge calibration` only: nothing computes with them |
| Research-policy weights (`web_metrics`, kind `procedural`) | `update_research_policy`, only on `nsf knowledge learning --update` | `nsf knowledge learning` only. CONTINUAL_LEARNING §2 already says so: advisory, and connecting them to provider selection is the owner's decision |

So one loop was open where closing it changes no belief: forecasts feed the order of autonomous research, and
nothing told that order how forecasts of the same kind had fared. The thresholds of provisional knowledge are not
part of it: MEMORY_INTEGRATION and EVIDENCE_AND_PROVENANCE state that they are policy and that the posterior is a
weighting rule, not a calibrated probability; moving them would change what the individual holds as verified.

## Decision

1. **One mapping per kind of forecaster**, because their base rates have nothing to do with each other:
   `knowledge` (an item's confidence that it still holds), `hypothesis` (a derived hypothesis' posterior) and
   `owner` (the owner's own estimate, `nsf knowledge hypothesize`).
2. **The mapping** is `logit q = logit p + shift + tilt × (logit p − centre)`, with `centre` the mean logit of the
   settled forecasts. `shift = tilt = 0` is the forecast as stated. The pair is the maximum of the likelihood of
   the settled outcomes under a Gaussian prior around 0 (precision 16 on each), so it starts at the stated
   forecast and leaves it only as outcomes insist. `tilt` is kept in [−1, 1]: the slope stays in [0, 2] and an
   order of forecasts is never reversed; a forecaster that has it backwards is flattened to its base rate. The
   result is kept in [0.01, 0.99]. `evolution.fit_recalibration`, `recalibrate`.
3. **Identity until 20 forecasts of the kind have settled** (`confirmed` or `error`; `inconclusive` observed
   nothing and does not count). Only the last 100 settlements count.
4. **Computed on read, stored nowhere.** `evolution.recalibrations(db)` reads the settled rows of
   `web_predictions`, the rows `calibration` scores. There is no parameter to migrate, restore or forget: a
   prediction removed by a forget (`forget_knowledge`, `forget_research` for the hypotheses a task formed) stops
   counting with the delete, and a restored copy reads from the rows it holds after the replay.
5. **One reader, and only for order.** `autonomy.candidates` reads a due knowledge prediction's forecast and an
   open hypothesis' forecast through the mapping of its kind, and uses the reading for the candidate's priority
   only: the signal `prediction_uncertainty`, and for a hypothesis the information gain that multiplies the
   priority (`info_gain_as_read`). Whether a candidate is researched at all is decided from the stated forecast:
   `info_gain`, which `min_gain` and every other admission test compare, is the stated forecast's gain, and the
   candidate a subject keeps when several records name it is chosen by that gain too. So the mapping changes the
   rank of the admitted candidates and never the set (review F3; before that fix a stated 0.9 read as 0.965 fell
   below `min_gain` and was refused). The candidate shows the stated `forecast`, and `forecast_as_read` and
   `info_gain_as_read` beside it when they differ; the `decision` event of a queued investigation records them.
6. **Not changed, by construction:** the stored forecast of any prediction, old or new; the confidence of a
   knowledge item; verification states, verdicts, the thresholds of provisional knowledge, answers; `calibration`
   and its snapshots, which go on scoring the forecasts as stated. Tests compare the tables before and after.
7. **A setting:** `calibrated_forecasts` (web settings, default on); `nsf web config --set
   calibrated_forecasts=false` makes every mapping the identity. `nsf knowledge calibration` shows, per kind, the
   mapping in force, what it makes of five stated forecasts, and how it would have fared on the individual's own
   settled predictions had it been in use all along (each scored before its outcome is used).

## Why these numbers

The individual has no history to learn them from, so they come from a simulation
(`src/neuroscifi/webcortex/bench/calibration.py`, `scripts/bench_calibration.py`): streams of forecasts whose true
probability follows a rule the forecaster does not know (right; too sure; not sure enough; off by a constant; one
forecast every time; knows nothing; backwards; a rule that changes halfway). An outcome becomes known some
predictions later, and every forecast is scored before its outcome is used. 108 settings (minimum 10, 20, 40;
window 100, 200, all; prior precision 1, 4, 16 on the shift; shift alone or a tilt with precision 4, 16, 64) were
run on eleven development streams. The rule, fixed before the run: the lowest mean squared distance to the true
probability among the settings that make no stream worse by more than 0.002 at any prefix (the first 25, 50, 100
predictions, or all). 13 settings pass; the first has a minimum of 10, and the second, the same with a minimum of
20, was shipped (0.0263 against 0.0253): one refresh can settle several predictions about one source, so real
settlements count for fewer than their number, and on streams whose outcomes arrive in bursts of five the two
minimums score alike.

Held-out streams (thirteen, with other rules, other forecast values, a longer delay and other seeds;
`docs/quality/evidence/open_items_calibration.json`):

| | Stated forecast | Read through the mapping |
|---|---|---|
| Mean squared distance to the true probability | 0.0436 | 0.0165 |
| Brier score | 0.2273 | 0.1999 |
| Worst change to any stream at any prefix | — | +0.0011 (a forecaster that was right) |
| First 25 predictions of every stream | — | identical: below the minimum |

Where it does not help, measured: a forecaster that is already right pays about 0.001 (0.002 when its outcomes
come in bursts), which is estimation noise; and in the 50 predictions after a forecaster's rule reverses, the
mapping still describes the old rule and is worse than the stated forecast (0.095 against 0.043 on the held-out
reversal, 0.106 against 0.047 on the development one) until the window turns over. Over such a stream as a whole
it is still better (0.046 against 0.053).

## Alternatives considered

- **Store the reading as the prediction's probability.** Rejected: the next fit would then learn from forecasts
  it had already moved, and `calibration` would score the mapping instead of the forecaster.
- **Reliability bins with isotonic smoothing.** Rejected for this size: with a few dozen settlements and a
  handful of distinct forecast values the bins are nearly empty; two parameters with a prior use every outcome.
- **A shift alone.** In the grid: it never passes the 0.002 rule on the development streams (a forecaster that is
  too sure needs a slope).
- **Move the thresholds of provisional knowledge with the calibration.** Not done and not proposed: it would
  change what counts as verified from a statistic of a different question ("did it still hold at the next
  refresh").
- **Give the research-policy weights a reader.** Not done: CONTINUAL_LEARNING §2 reserves that for the owner, and
  nothing in the docs defines what the reader would do.

## Consequences

- An individual with fewer than 20 settled forecasts of a kind behaves exactly as before; the offline benchmark
  (`nsf web bench`) is identical with the mapping in force and with it made inert (every metric, the safety
  summary and the regression block).
- Once a mapping acts, the order of autonomous research changes, and only the order: a hypothesis whose reading
  moves its forecast towards certainty carries less expected information as read and ranks lower, one whose
  reading moves it towards even odds ranks higher; knowledge whose "still holds" forecasts kept failing is read as
  less certain and ranks higher among due predictions. The set of admitted candidates is the one the stated
  forecasts give. With `limit` (the idle investigator asks for five) a candidate can fall out of the returned
  list by rank, as any lower-ranked candidate always could.
  This is the first value the individual learns from its own record that changes what it does next without the
  owner asking (CONTINUAL_LEARNING, kind 2b): bounded as above, shown, logged with each decision, and one setting
  away from off.
- A prediction settled by a task that was later forgotten keeps its outcome (ADR-029: the observation is scrubbed,
  the outcome stays). Since ADR-048 it no longer counts, here or in `calibration`.
- Settlements are treated as independent draws. They are not (one source change can fail several predictions);
  the minimum and the prior are the only allowance for that.
- Tests: `tests/webcortex/test_recalibration.py` (24; the fit against the objective written out and against a
  one-value solution by bisection, monotonicity over 200 random histories, the kinds, the reader, the decision
  record, forgetting, restore, the setting, the command, and the recorded evidence recomputed).


# ADR-047 (draft) Individuals made by older software adopt today's graph rules once, recorded and reversible. R

## Context

ADR-045 left existing individuals with exactly the rules they had: one born under gate-v1 kept packing a cut of the
dense fit, which at 96 and 128 populations has twice the sparse learner's error, until its owner added
best-learner-v1; and an active graph promoted before check (f) existed stayed active although it predicts worse
than a graph with no edges (from about 160 populations, ADR-045's table). On 2026-11-24 the owner ruled: migrate
automatically.

## Decision

`instance/graph_migration.py`, run by `store.open_existing` once per file (a meta row `graph_rule_migration`
records that it ran and what it did):

1. **Birth rule.** If the newest rule is the built-in `gate-v1` and `best-learner-v1` is not registered, register
   `best-learner-v1` (`created_by: migration`). An owner who chose otherwise keeps their choice: a rule of their own
   newest, or best-learner-v1 registered and gate-v1 added again after it. Nothing is removed or edited; revisions
   keep the rule they were decided under.
2. **Active graphs.** Each active graph is judged by check (f): from what `learn` recorded, or, for one learned
   before that was recorded, by computing its snapshot's error and the no-graph error on its own validation dataset
   (`graphs.no_graph_check`). One that fails is archived with a decision `{"action": "demote", "by": "migration",
   "check", "prior"}`; snapshot, metrics and lineage are kept. A graph that cannot be judged (founding, imported,
   validation data gone or damaged) stays active and is listed as `not_judged`. The deletion cascade never
   re-activates a demoted graph on its own (`graphs.NOT_REVIVED`); the owner can, with `nsf graph rollback`.
3. **Not applied** to an individual born by this software (`store.create` records `settled_at_birth`: its rules
   are the ones chosen at birth, which keeps the first physical test's gate-v1 individuals as preregistered), while
   the owner's setting `graph_rule_migration` is false, to a connection that cannot write (a read-only file, or
   `query_only`: tried again at the next writable open), and while a context's lease is held (a graph job runs).
   All of these are read before the write lock is asked for, and the lock is waited for at most 200 ms
   (`store.optional_write`; a busy file is tried again at the next open): an open must never queue behind another
   writer for a step it can postpone, since the console opens the file for each request (review of the round).
   `nsf config` and `nsf graph migration` open the file without it, so an owner can see the plan
   (`nsf graph migration status`) and opt out (`nsf config --no-graph-rule-migration`) before anything runs it.
4. **Reversal.** `nsf graph migration undo --owner-approved`: gate-v1's content is registered again as the newest
   rule (`gate-v1-restored`) if best-learner-v1 added by the migration is still the newest; each demoted graph is
   re-activated if it is still archived as demoted, its lineage is intact and its context has no active graph; the
   setting turns off (logged as a setting change), so it never runs again, also in a copy restored from an older
   archive (the setting is carried by a restore, like consolidation's). What cannot be reversed is listed with the
   reason and the command that does it by hand. The migration runs at most once: turning the setting on again after
   an undo does nothing, and `nsf config` says so. An undo is not carried into a copy restored from an archive taken
   after the migration and before the undo: the copy holds the migration's own record and changes, as the archive
   did, and only the setting (off) comes from the live file; the owner runs `nsf graph migration undo` in the copy
   if it is to become the individual.
5. Shown in `nsf birth` and the console's overview (`graph_rule_migration`), and in each demoted revision's
   decision (the console's graph detail says why it was archived).

No schema change: the record is a meta row and the setting a field of the settings JSON.

## Measured

`scripts/bench_graph_learner.py migration` (`docs/quality/evidence/remaining_graphs.json`), 3 worlds per size,
individuals as older software left them:

| Populations | Old active graph, validation MSE against no graph | First open | Graph learned afterwards (heldout MSE, old → new) |
|---|---|---|---|
| 24 | 0.0042-0.0044 < 0.018-0.020: kept | 1.0-1.1 ms | 0.00433 → 0.00430, promoted |
| 96 | 0.0083-0.0085 < 0.017-0.018: kept | 2.8-24 ms | 0.00835 → 0.00433, promoted |
| 128 | 0.0084-0.0085 < 0.017-0.018: kept | 6.2-15 ms | 0.00859 → 0.00433, promoted |
| 160 | 0.019-0.020 > 0.0177-0.0182: archived | 7.7-31 ms | 0.0193 → 0.00432, promoted |
| 256 | 228-468 > 0.018: archived | 18-24 ms | 273 → 0.00432, promoted |

A later open costs 0.4-1.0 ms in all (one meta read decides that nothing is due).

## Alternatives rejected

- **Relearning the graphs at migration.** A graph job needs the lease, takes seconds and writes candidates; it is
  the owner's or the scheduler's to run. At 96 and 128 populations the old graph still beats no graph, so it stays
  active until a candidate learned under best-learner-v1 beats it at the gate, which it did in every world.
- **Re-activating an older archived graph in place of a demoted one.** Its quality was never compared with the
  demoted one's on today's data; a context with no active graph is not used by recall, which is safer than one
  that misleads. The owner can roll back.
- **Repairing the dense learner.** As in ADR-045: its arithmetic is the lab's and the reproduction depends on it.

## Consequences

- An individual made by older software changes at its first open by this software, unless its owner opts out
  first. The change is graph state of the failing revisions and one added rule; no revision, snapshot, metric or
  dataset is removed.
- A preregistered individual born by older software under gate-v1 alone is migrated too unless its owner opts out
  first; its recorded decisions keep gate-v1.
- Tests: `tests/instance/test_graph_migration.py` (kept and archived graphs, a founding graph not judged, a graph
  learned afterwards under best-learner-v1, the cascade, undo and what it leaves, opt-out before the open, a damaged
  setting, a read-only file and a `query_only` connection, a held lease, a new individual, an owner's own rule
  choice, restores before and after an undo; an open beside a writer while the migration is pending, a read-only
  file not judged, `nsf config` after an undo, the record without the scores of an erased graph).

# ADR-048 (draft) Forgetting reaches what was left behind: one kind of a name, a forgotten task's settlements, a forgotten dataset's graphs. R

## Context

ENGINEERING_PLAN.md §6 listed what a forget still left behind after phase 3: the name of a camera forgotten while a
partner of the same name is held (or the reverse) in belief revisions, questions and decisions; the settled
predictions of a forgotten task, which still counted in the calibration of ADR-046; and the weights and metrics of a
graph revision invalidated by `forget_dataset`, with the dataset's digest. Forgetting is the owner's act, so
erasing what it names is correct; nothing else may go, and a restore must end the same way.

## Decision

1. **One kind of a name (schema 15, `forgotten_scopes`).** When the last evidence of a root in one channel goes
   (by `forget_camera`, `forget_partner`, the forget of its last event or a sweep) while the other channel still
   holds the name, that kind is scrubbed (`governance.scrub_roots(..., scoped=True)`): `camera:<root>` (or
   `partner:<root>` and the executive's "partner <root>") everywhere, the root's key in the summaries' `sensors`
   (or `partners`) map, its changepoints and the epoch links with a segment of it, and every mention of the name
   in a question or decision whose scope is that very scope. A bare mention anywhere else may be the other one and
   stays until the last evidence of the name goes. The digest is recorded with its kind (`forgotten_scopes`, as
   `forgotten_roots` records whole roots), and a restore scrubs the same kind in its copy unless the copy holds
   evidence of that kind (a newer camera or partner of the name). `effects.scopes_forgotten` counts them.
2. **A forgotten task's settlements stop counting.** A task the owner forgets takes its observation with it
   (`governance._scrub_observations` leaves `{"forgotten": true}` on the prediction it settled). The outcome stays
   on the prediction (ADR-029: the knowledge and hypotheses revised by it are not rolled back), but the settlement
   no longer counts in `calibration`, in the mapping of ADR-046 or in the accuracy of `metrics`, which reports such
   settlements as `settled_by_forgotten_tasks`. Computed on read, so it holds for forgets made before this change
   and in a restored copy. Reopening the prediction instead was rejected: it would be checked again and could
   start a second error analysis for the same failure.
3. **What graphs learned from a forgotten dataset goes with it (schema 15).** `forget_dataset` already invalidated
   the revisions trained or validated on the dataset. Now `graphs.erase_learned_from` also empties their blob (the
   weights are an image of the data they were fitted to) and replaces their metrics with
   `{"erased": {"dataset", "at"}}`, deletes the graph effects measured on them, and the dataset's digest becomes
   empty (with it a guessed dataset could be checked against the file). A revision invalidated earlier by another
   dataset of its lineage is erased too. Nothing kept may confirm a guess of the data either (security review of the
   round, L3: `learn` is deterministic, so a guessed dataset could be refitted and compared): the digest of the
   weights becomes `erased`, and every score computed with the weights is removed (set to null) from the revision's
   own decisions, from the incumbent's side of other revisions' gate decisions where it was the incumbent (its
   held-out error, and the threshold and limits computed from it; the other candidate's own scores stay), and from
   the graph-rule migration's record (ADR-047). The revision stays listed with its id, status, shape (bits, degree,
   byte size: fixed by the shape and the rule, not by the data), lineage and decisions, the audit of what was
   promoted and why; `graphs.show` returns it without a matrix and says why, `export`, `lesion` and the other readers
   of the weights raise Gone. Migration 15 replaces the identity trigger: it allows exactly this update of an
   invalidated revision (the blob to empty and the digest to `erased`, nothing else of its identity) and refuses
   every other as before; a second trigger keeps an erased revision invalidated, and a third refuses deleting a
   revision, which also closes INSERT OR REPLACE (its implicit delete fires it; review L2). A restore replays it.
   A revision that only scored the dataset as held-out data keeps its weights: it did not learn from it.
   **Forgets made by an earlier version** kept all of this. The first writable open of the file finishes them once
   (`governance.finish_earlier_forgets`, logged as a dataset deletion with `by: upgrade`, valid checkpoints marked
   stale), so the live file holds what every restored copy holds, and `forget_dataset` on such a dataset finishes
   it too (status `completed`; `absent` once nothing is left). The owner already named the data; nothing not
   learned from it is touched (review CM2). Like the graph-rule migration it waits for a writable open and never
   queues for the lock.
   **The WAL.** A restore or rewrite keeps one read snapshot of the live file for its whole replay, so a forget
   committed meanwhile cannot truncate the WAL; when the file changed during the snapshot (`PRAGMA data_version`),
   the WAL is truncated as soon as it ends (review L4).
4. **Calibration after a change of habits: tried, not shipped.** After a forecaster's rule reverses, the mapping of
   ADR-046 reads the next 50 forecasts worse than stated (held out: 0.095 against 0.043). `fit_recalibration` can
   now split its window where fitting the two parts apart gains most and drop the older part when twice that gain
   exceeds a threshold (`change_step`, `change_threshold`). Both rules were written before they were run
   (`bench.calibration.CHANGE_RULES`): selected on the development streams (step 10, threshold 4.0; the first grid
   was best at its edge and was extended downwards on the development streams only), gated on the held-out ones.
   It made both held-out reversals better (0.095 to 0.065, 0.045 to 0.043) and the mean distance lower, but one
   held-out stream without a change got worse than the shipped mapping by 0.002021 in Brier, over the 0.002
   allowed, and the burst streams by up to 0.006 (false alarms on correlated outcomes). So
   `RECALIBRATION_CHANGE_THRESHOLD` stays None and the mapping is exactly ADR-046's. No other setting was tried on
   the held-out streams. `python scripts/bench_calibration.py change` reproduces all of it.

## Measured

`docs/quality/evidence/remaining_forgetting.json`, the code of c565c20 against this change on the same scenarios:
a camera forgotten while a partner of its name is held left `camera:<name>` 15 times and the name in 13 summaries'
sensors, live and in a restored older archive; now 0 and 0 (the partner's 15 mentions stay). The reverse: 15, 10,
and the question "What does <name> mean" once; now 0. Thirty predictions settled by one task the owner then forgot
counted 30 times in the calibration and in the mapping; now 0, the outcomes kept. A revision trained on a forgotten
dataset kept its 247-byte blob, 2,407 bytes of metrics, 73 effects and the dataset's digest; now 0 bytes, 46 bytes
(the erasure note), 0 and no digest.

## Consequences

- Schema 15. An older binary refuses the file, as for every schema. The migration adds a table and replaces or
  adds triggers; no row changes (tested on a version-14 file with graphs, datasets and evidence). The completion of
  earlier forgets is not part of the migration: it runs at the first writable open after it.
- An erased revision cannot be exported, lesioned or rolled back to; `graphs.show` lists it without a matrix.
- The calibration history snapshots in `web_metrics` recorded before a task was forgotten keep the numbers they
  were computed with, by decision: they are a past record of aggregate scores (a Brier score over many forecasts),
  not the task's content, and the calibration read now leaves the settlement out (review question Q1).
- Still open: a bare mention of a name whose other kind is held (it may be that other one); tombstones and the
  deletion log keep epoch names and event ids in clear; settlements are still treated as independent draws.
  A connection that does not turn recursive_triggers on (a raw sqlite3 connection, not `store.connect`) does not
  fire the delete trigger on INSERT OR REPLACE.
- Tests: `tests/instance/test_forget_one_kind.py`, `tests/instance/test_forget_dataset_erasure.py`,
  `tests/webcortex/test_recalibration.py` (the settlement of a forgotten task; the change detection, off).

# ADR-049 (draft) Provenance kinds and the imagined store: nothing imagined is ever evidence. R (H for the schema)

## Context

Cognitive Genesis §5.5 asks for an imagination sandbox whose results "must never silently become factual memory", and
§7.3 asks that evidence, inference, prediction, simulation and speculation be distinguished at the representation
level. Before this change nothing stored simulated or model-inferred content apart from evidence, because no mechanism
produced any: the only model text kept is the labelled answer in a task's report, never consolidated. The project's
research review ranked provenance typing with a quarantine for imagined content as the largest single risk reduction
(self-generated data erodes a store; poisoning agent memory works at very low rates) and as the prerequisite for every
imagination mechanism. It must exist, with its guarantees enforced by the database and by tests, before any mechanism
stores such content.

## Decision

1. **Five kinds, one vocabulary (`neuroscifi/provenance.py`).** `evidence` (observed or reported from outside, and
   what the evidence pipeline's fixed rules derive from it), `inference` (a conclusion beyond the evidence that no
   deterministic rule checked, such as model-written text), `prediction` (an expectation about a later observation,
   with what settles it), `simulation` (a model run forward under stated assumptions) and `speculation` (a possibility
   with neither evidence nor a model behind it). `inference`, `simulation` and `speculation` are quarantined.
   `provenance.mapping()` maps today's record types to the kinds, read-only; no row is retagged. Claims, sources, the
   evidence journal, consolidated readings, epoch streams and datasets are evidence; knowledge items are evidence in the
   sense "what verified evidence supports"; hypotheses and predictions are prediction. Evidence and datasets with
   provenance `simulated` are observations of a simulated environment (ADR-014), labelled since schema 1: evidence with
   that label, never simulation. Two existing fields are model or template text and are named as such (the
   model-written answer in a task's report: inference; a hypothesis's template alternatives: speculation); both were
   already labelled and never consolidated, and they stay where they are.
2. **One quarantined store (schema 17, `instance/imagined.py`).** Table `imagined` holds the kind, a structured JSON
   payload, the assumptions, an uncertainty statement, the mechanism and its version, the model id when a model wrote
   text, the question or goal served, the creation and expiry times, and an outcome slot for what was later observed.
   `imagined_refs` holds what a record was derived from (kind and id; 14 kinds, from a task to a graph to another
   imagined record) and `imagined_forgotten` holds payload-free tombstones. The kind CHECK admits only the three
   quarantined kinds. Triggers make a record immutable except its outcome slot and its expiry: an UPDATE of anything
   else and an INSERT OR REPLACE of a held id (whose implicit delete would rewrite the record and drop its lineage
   with no tombstone) are refused, and the expiry may only be brought forward (a lower lifetime, decision 6). The
   outcome slot is filled once, while writes are allowed, with an observation that names held evidence and never
   another imagined record (imagine, observe, compare: the comparison is the mechanism's; the store keeps both,
   apart). The evidence it names joins the record's references (`imagined_refs.observed` = 1), so forgetting any of it
   removes the record, and the observation itself carries no text: a number (an integer within ±2^53, so no text fits
   in its digits, or a finite float), true/false or null, or an object of the record's own payload keys to such
   values. Nothing a forget removes can survive in an outcome.
3. **Nothing on the evidence side can name a record.** An id is `imagined:` followed by 32 hex digits. Triggers on
   insert and update refuse such an id in every column that holds a REFERENCE on the evidence side
   (`schema._NO_IMAGINED`): the id columns of evidence, consolidated readings, tasks (parent), events, web sources,
   claims (and their source), claim links, mentions, hypotheses (task, knowledge, parent), predictions (task,
   knowledge, hypothesis), feedback, voice segments and monitors (last task), compared lower-cased and trimmed of white
   space; a belief revision's cause that contains one; and the elements of the JSON id lists (knowledge supporting,
   the `claim`, `claim_id` and `task` of each contradicting entry, a hypothesis's claims), read with `json_each`, so a
   JSON escape hides nothing. An event id, root or sample id, or an epoch name, holding such an id in any case is
   refused before the database sees it (`ValidationError`; the revision cause would have refused it as a raw
   integrity error, at the epoch's forget for a name). Free text is never tested: a
   page title copied into a knowledge item's sources, a quote in `contradicting`, a detail, a verification record or
   a metric may read like an id (a web page titled `imagined:<32 hex>` used to abort its task's consolidation). Text
   copied from a record is kept out by the code instead: an AST scan of every module under `src/neuroscifi` allows the
   store only to an allow-list (`tests/webcortex/test_imagined_quarantine.py`): the store and its lifecycle (schema,
   bounds, forgets and sweep, restore, the owner's commands); *writers*, which may call only its write API (`record`,
   `observe`); and *displays*, which may list records labelled "imagined, not evidence" (`listing`, `show`), each
   entry with its reason. No evidence path (knowledge, claims, consolidation, synthesis, evolution and calibration,
   predictions, semantic memory, the memory gate, reasoning, web state, temporal reasoning, beliefs, evidence, the
   executive, memory, datasets, graphs, the learner, recall) may be put on any list, nor may the orchestrator (it
   builds the report the answer model is sent), curiosity or autonomy (they choose what is investigated): a
   scenario's outcome reaches those only as a settled prediction error, through the ordinary prediction records. A
   lane adds its module to WRITERS or DISPLAYS with a one-line reason; the scan checks the names it uses.
4. **The answer model never sees it as evidence.** `reasoning.bundle` drops findings, answer items and sources of a
   quarantined kind. `reasoning.check_sentence`, the guard's only citation resolver, treats a quarantined claim or
   source as unknown. `messages()` sends only the bundle's evidence keys, so a separate part built with
   `imagined.section()` (labelled "imagined, not evidence", items without ids) reaches the model only if a later
   change adds it on purpose, and it can never be cited. `guard_differential.py compare`: 0 different.
5. **Retrieval never returns it.** `knowledge.query` (lexical and semantic), the memory gate and `memory.retrieve`
   read other tables; `memory.KINDS` has no imagined kind.
6. **Bounded and forgotten.** `InstanceConfig.max_imagined_rows` (default 1000; the oldest is evicted first; 0 = the
   store takes nothing) and `imagined_lifetime_seconds` (default 7 days, from one minute to 365 days; a writer may only
   shorten it) are owner-settable (`nsf config --max-imagined-rows`, `--imagined-lifetime-days`) and carried into a
   restored copy. A lower limit applies to the records already held, in the same transaction as the setting
   (`imagined.bound`: expiries brought forward, the oldest beyond the limit evicted), and a restore applies the
   carried limits to its copy. `governance.sweep` expires records and, whatever expired, removes any record one of
   whose sources is gone by any path. Every forget (evidence, epoch, partner, camera, consolidated
   reading, dataset with its erased graphs, research task, knowledge item, source, voice session) removes in its own
   transaction every record one of whose sources no longer resolves, to a fixed point (`imagined.purge_orphans`;
   `effects.imagined_removed`, absent while zero). An epoch row outlives its forget, so an epoch counts as gone for a
   record made before the epoch's deletion-log row, and a forgotten epoch is never a new record's source. Every
   removal (forget, purge, expiry, eviction) leaves a tombstone, kept for 365 days (the longest lifetime a record can
   have: by then the record has expired in every archive too, and a restore expires it there). Purge, expiry and
   eviction write a deletion-log row of kind `imagined` and make stale the checkpoints written since the oldest
   removed record.
7. **Checkpoints carry it; a restore never brings a removed record back.** An archive is a copy of the file, so the
   store travels with its kinds. `checkpoints._replay` (restore and rewrite) runs `imagined.replay` last: the live
   tombstones, expiry, and the orphans every other replay left. `imagined` is a rewritable kind (`REWRITABLE_KINDS`),
   so the archives a purge made stale are rewritten in place.
8. **Owner surface.** `nsf imagined list | show | purge | kinds`. Every listing carries each record's kind and
   "imagined, not evidence"; `purge` takes `--id`, `--kind` or `--all` and rewrites the archives it made stale. The
   console is not changed: its memory browser is driven by `memory.KINDS`, which must not list imagined records, and a
   separate labelled panel needs a design pass. It is left to the workspace lane.
9. **Default.** The store exists and is empty. No mechanism writes to it. `imagined.example(seed)` is a deterministic
   generator for tests and measurements and writes nothing itself. With no record held, a forget costs one indexed
   read more and the answer path is unchanged.

## What the triggers and the scans cannot see

Only the database triggers (decision 3 and the store's own) and, for ADR-050, the runtime quiet check enforce anything
while the system runs. The AST scans are tests: they read the source as written, and a lane can defeat them without
meaning to. Known gaps, measured by the second Wave 0 review (`review0b`):

- **Forms the reference triggers let through.** A JSON id list that SQLite's `json_valid` rejects but Python's
  `json.loads` reads (a `NaN` or `Infinity` element beside an id: the trigger reads no element of an invalid list);
  an id padded with characters `trim` does not remove (a no-break space, a zero-width character); an id inside a
  nested list or an object element of `supporting` (only text elements are tested), or under a `contradicting` key
  other than `claim`, `claim_id` and `task`.
- **Reference columns not covered.** `web_predictions.detail`'s `task_id` and its `observation.task_id` (JSON inside
  a free-text detail), `graph_lineage.dataset_id`. `web_monitors.last_task_id`, `web_claims.source_id` and
  `contradicting[].claim_id` were added by the second fix round.
- **A damaged settings row** in a restored or rewritten copy reads the imagined limits as the defaults (1000 records,
  7 days), so the copy's store may shrink to them on restore (fail-closed: it can only lose records, never keep more).
- **Tombstones are kept by age, by the wall clock**: a clock set forward by more than 365 days drops them early (the
  records they name are then past their expiry by the same clock, and a replay expires them, unless the clock is set
  back again before the restore). They are bounded by age, not by count: a store churned at its row limit leaves one
  tombstone per removal for 365 days.
- **Shapes the store scan cannot see** (`tests/webcortex/test_imagined_quarantine.py`): a module imported by
  `importlib`, `getattr` or `sys.modules`; SQL built from strings, quoted (`"imagined"`), schema-qualified
  (`main.imagined`), behind a comma join or a comment, or a table name read from `sqlite_master`; a call to an allowed
  store module (the owner's `cli_imagined` functions). A star import and a dictionary of the store's functions are
  seen.
- **Shapes the launch scan cannot see** (`tests/webcortex/test_background.py`, ADR-050): `getattr`, `importlib`,
  `__dict__` and `operator.attrgetter` access to an entry point, an entry module rebound to another name, a star
  import, `**{"origin": ...}`, an f-string origin without a fixed background prefix (`f"curiosity{x}"`), a raw
  `INSERT INTO web_tasks`, `orchestrator.prepare` and `insert` with the origin changed in between, and a subprocess
  running `nsf web tick`. A forwarder (`background.FORWARDERS`) is exempt only for the origin it passes on: a
  background origin it writes out, or an entry point it refers to, must be registered as a launch site.

## Measured

`docs/quality/evidence/genesis_provenance.json` (`python scripts/genesis_provenance.py all`, 5 runs each, medians):
20 safeguards, each broken alone in a copy of the source, were caught by a test (20 of 20; the unchanged copy passes).
Costs: the triggers add 0.9 µs per evidence insert (20,000 rows: 0.077 to 0.095 s) and 3.8 µs per claim insert
(5,000 rows: 0.029 to 0.048 s). With the store empty, a forget took 1.05 ms against 0.91 ms on the start commit and a
sweep with nothing due 0.084 ms against 0.053 ms (15 runs each, measured separately). With 1,000 records derived from
the forgotten event, the forget took 9.0 ms and removed all of them; writing a record 76 µs; listing 100 of 1,000
1.2 ms; a restore replaying a purge of 1,000 records 11 ms. The empty store takes 6 pages.

After the Wave 0 review's fixes (`genesis_provenance.json`, `review0`): 34 of 34 safeguards caught, 14 of them new
(references only, case and padding, the added columns, free text never tested, no INSERT OR REPLACE, the outcome's
evidence and text, writes paused, lower limits applied, orphans swept, forgotten epochs, tombstone age, the first
build's repair, the allow-list scan; the 2aee516 opt-in scan misses the last one). Measured in one session against the
start commit's own script and source: 20,000 evidence inserts 0.116 s against 0.094 s (+1.1 µs a row), 5,000 claim
inserts 0.090 s against 0.086 s (the start commit itself measures 11.5 µs a row for the triggers here, not the 3.8 µs
recorded above); a sweep with nothing due 0.085 ms against 0.078 ms with the store empty and 0.44 ms against 0.09 ms
with 1,000 records held (it now looks for orphans whatever expired); a forget on an empty store unchanged (about
1.0 ms); the empty store takes 7 pages (the tombstone age index).

After the re-review's fixes (`genesis_provenance.json`, `review0b` and `mutations`): 42 of 42 safeguards caught
(control 222 passed), 8 of them new: two processes repairing a first-build file together, the first build's
observations removed by the repair, observed integers bounded, only retired triggers dropped, the monitor's last task
and the claim's source checked, event ids and epoch names refused up front. The allow-list's evidence paths are a
test's own list, so that break is shown by hand: with the orchestrator listed as a display and reading the store, the
03cbece tests pass and these fail. Costs were not re-measured.

## Alternatives rejected

- A provenance column on the existing tables: every evidence path would have to filter by it, and one forgotten filter
  would let imagined content through. Separate tables need no filter.
- Retagging existing rows (for example knowledge as inference): it changes no behaviour and rewrites the owner's data;
  the mapping documents the kinds instead.
- Refusing writes at the limit instead of evicting: imagined content is short-lived, and a full store must not stop a
  mechanism from recording its newest result. Evictions are logged, tombstoned and replayed.

## Consequences

- Schema 17. Older software refuses the file. The migration only adds empty tables, indexes and triggers (IF NOT
  EXISTS, so a file a test put back to an earlier version by hand migrates again); no row changes. It was amended
  before release, twice (the Wave 0 review and its re-review): a file migrated by either earlier build (2aee516's or
  03cbece's) is brought to the current triggers, `imagined_refs.observed` and the tombstone index at its next open
  (`schema.ensure_triggers`, as for version 15), which reads the catalogue again once it holds the write lock, so
  two processes opening such a file together both open it. It drops only the reference triggers it retired by name
  (`schema._RETIRED_17`). The same one-time repair removes, with a tombstone each and one deletion-log row, every
  record observed under the first build (its outcomes kept text, and evidence no forget reached) and any outcome
  today's `observe` could not have written; tested on files built from each commit's own `schema.py` (`git show`; the
  test skips in a clone without that history).
- An evidence event id, root or sample id, or an epoch name, that holds `imagined:` + 32 hex digits (in any case) is
  refused with a `ValidationError`: the form is reserved in reference columns, never in free text.
- Every forget runs one more query, and a loop to a fixed point when records derive from what it removed.
- Changed at the defaults by the Wave 0 fixes, beyond the store's own rules: every sweep looks for orphaned records
  whatever expired (with records held it can remove one and mark checkpoints stale); `store.set_config` returns
  `imagined_removed` when a limit change removed records; raising either imagined limit, not only lowering it, runs
  a full expire pass over the store (`imagined.bound`).
- Tests: `tests/instance/test_imagined.py`, `tests/webcortex/test_imagined_quarantine.py`; mutations:
  `scripts/genesis_provenance.py mutations`.

# ADR-050 (draft) One owner switch for everything done unasked, and one admission rule for autonomous investigations. R

## Context

Cognitive Genesis (owner directive 2026-11-25) adds new autonomous mechanisms only after two safety prerequisites
(directive §2 items 8 and 9, §4.4, §12 "Unauthorized autonomous actions" and "Unproductive curiosity loops"):

1. Stopping everything the individual does unasked took several commands, each with its own scope: `nsf web
   autonomy disable` (the idle investigator and the tick's refreshes and curiosity), monitor pause for each monitor,
   and disabling the 15-minute timer. Nothing said which background mechanisms exist, so a new one could start work
   without any of these covering it.
2. Two paths launched autonomous investigations under different rules. `scheduler.tick` took curiosity.rank's top
   ten with curiosity's own floor (`curiosity.MIN_GAIN` 0.02 bits) and needed only the `autonomous` switch.
   `autonomy.idle` needed the owner's recorded enable and admitted through `autonomy.candidates`: `min_gain` 0.05
   bits on the stated forecast, at most `MAX_DERIVATION_DEPTH` (1) step from the owner's records with
   `MIN_RELEVANCE` (1/3) to the owner subject it came from, `cooldown` (24 h) per subject, record templates and claim
   shapes refused, goal-weighted priority. Verified in code: the tick had no goal signal, no lineage check, no
   cooldown, no claim-shape check and no recorded-enable check, and it logged no subject, so its launches fed
   neither the cooldown nor the lineage. Both paths share the daily budgets, the concurrency limit and the
   background gate at claim time (`autonomy.admit`, `scheduler._over_cap`); those were already the same.

## Decision

1. **`quiet`, an instance setting (no schema change).** `InstanceConfig.quiet`, default false, beside
   `graph_rule_migration`: owner-settable (`store._SETTABLE`), every change a deletion-log `setting` row, carried by
   restore (`checkpoints.CARRIED_SETTINGS`); a saved value that is not a JSON boolean reads as **on**, the reading that
   cannot run work the owner may have stopped, and `store.setting_problems` lists it. Set only on the owner's command
   line: `nsf quiet on|off|status`, or `nsf config --quiet|--no-quiet`. No console, Pocket, voice or MCP route sets it
   (the console's four web switches, Pocket's fixed routes and MCP's five read-mostly tools are unchanged). It sits
   above the other switches and never writes them: `autonomous`, the enable record, monitors and timers keep their
   stored values.
2. **One check and a registry (`neuroscifi/background.py`).** `background.allowed(db, activity) -> (bool, reason)`
   for an activity named in `ACTIVITIES` (KeyError otherwise). Kinds: *cognition* (held while quiet: the worker's
   claims and crash resumes of background tasks, its idle investigation, the tick's monitors, refreshes and
   curiosity, a background task already running); *maintenance* (keeps running: evidence expiry and its consolidated
   readings, the nightly checkpoint and the rewrite after a forget, cache retention, unloading idle model services,
   reloading one a request waits for, the worker's heartbeat, the voice and Pocket sweeps); *delivery* (keeps
   running: a finished autonomous task's finding question, a Pocket push); *owner* (a curiosity proposal waits for
   the owner's yes). Background origins are `curiosity`, `refresh:*` and `monitor:*`, matched with case everywhere
   (`ORIGIN_SQL` uses GLOB, as `held_origin` does); a proposal the owner confirmed (`knowledge.apply_review`'s decision
   event) and a background task the owner resumed or queued again (`nsf web resume`, `requeue` from the CLI, console,
   Pocket or voice: `background.asked_by_owner` records a decision event with it) are owner-asked work and never
   held. `RUNNERS` and `UNITS` name the callers and the systemd timers and path units that run the entry points;
   `FORWARDERS` the functions that pass on an origin they did not write out. The source scan flags every call that
   creates a task of a background origin, every `origin=` that is not a literal, and every reference to an entry
   point (called, handed to a thread or `functools.partial`, or imported under another name).
3. **A background task already running stops at its next stop check**, the owner's cancel's place: `_Run.check`
   before every network step (unthrottled, one meta row) and the run's `_Lookout` for its other threads, which also
   cuts requests on the wire; and quiet is asked again before the answer model is called and before consolidation, so
   quiet turned on while the task synthesizes or reasons stops it there. It ends `partial` with the limitation
   "stopped by quiet" and **consolidates nothing**,
   so no memory or model call runs for it after the stop (web-off still consolidates what was read; quiet is a
   stronger stop by design). Queued background tasks stay queued; after `nsf quiet off` they run as before. A
   prediction whose refresh quiet stopped is not settled by it (it used to be settled "inconclusive: the refresh
   found no evidence", and calibration counted that): `predictions.check_due` lets the stopped refresh go and the
   prediction stays due. A background task the owner resumed or queued again is labelled owner-confirmed in its
   answer note, as a confirmed proposal is (it kept the "autonomous" label, which shows no note).
4. **One admission rule (`autonomy.admission`).** Both paths call it. Rule `unified` (the default): the recorded
   enable, then `autonomy.candidates` (everything listed in Context for the idle path). The tick keeps its own pool,
   unchanged: knowledge items among curiosity.rank's ten best, due predictions left to the refreshes. So it admits a
   subset of what the old rule admitted, never another subject; and a pooled subject only in the kind the ranking
   gave it (`candidates` keeps one item per subject, so a due prediction or a hypothesis on the same key could take
   it, and the tick would have launched a `prediction` or a fact-verified hypothesis it never launched before: such
   an item is refused, review0b; the offline benchmark's admission is unchanged by this). Each tick launch records
   `{"autonomy": {...,
   "admission": {"rule", "path"}}}` with its subject, which feeds the cooldown and the lineage as the idle
   investigator's does; the idle investigator's event gains `admission` too. Rule `legacy` (path tick only), kept for
   one release: `nsf web config --set tick_admission='"legacy"'` restores the tick's old rule exactly, and its launches
   record `{"autonomy": "admitted by the legacy tick rule", "key", "admission"}`, which feeds no cooldown, as before.
   Prediction refreshes (`predictions.check_due`) are not investigations chosen by ranking and keep their rule; under
   the unified rule the tick launches no curiosity task on a subject (knowledge id or key) whose refresh it queued in
   the same tick. A restore carries the live `tick_admission` into the copy, as it carries quiet: an archive taken
   under `legacy` never brings the older rule back.

## Measured

`docs/quality/evidence/genesis_safety.json` (`scripts/bench_genesis_safety.py`, at bcc7d48, aarch64):

- **Honouring quiet, worker as `nsf web work` (5 s poll), 5 trials, quiet turned on at a random moment from another
  connection:** the worker recorded the hold after 1.56, 1.75, 2.44, 2.55 and 4.40 s (all within one poll; a claim
  reads the setting at that moment, so nothing background could start after the commit in any case). A curiosity
  task queued meanwhile stayed queued; after `quiet off` it was claimed within 0.25 to 4.49 s. A background task
  reading a slow site (1 s per page) when quiet was turned on stopped 0.25 s after the switch in all 5 trials, with
  0 page requests sent after it (the run's gate cut the request on the wire), and ended `partial`.
- **Cost:** `background.allowed` 6.4 µs (median); `claim_next` 0.200 ms with quiet off, 0.222 ms with it on.
- **The offline benchmark's world** (51 questions, 7 knowledge items), eight clock offsets from 1 to 730 days, both
  rules, one tick and eight ticks 15 minutes apart (queued tasks marked finished between ticks; nothing fetched):
  as researched, **0 launches under either rule** at every offset (the aged items are the refresh path's: each
  carries an open prediction). Staged without those predictions, still 0 under either rule: the tick's pool
  (curiosity.rank's ten best) is filled by contested subjects that are not knowledge. Staged also without those
  subjects (written after the first two showed this): one tick admits the same 5 subjects under both rules at 365,
  400 and 730 days (15 in all); over eight ticks the legacy rule launched 36 times, relaunching all 5 subjects at
  each offset until the daily task cap (12) stopped it, the unified rule 15 times, once per subject (cooldown).
  Nothing the legacy rule launched in one tick was refused; the change on this world is the end of the relaunch
  loop. Ablation, not shipped: the unified rule over every ranked knowledge item instead of the tick's pool would
  have admitted 5 subjects at each of 365, 400 and 730 days in the second staged world, where both shipped rules
  admit none.
- **After the Wave 0 review's fixes** (`genesis_safety.json`, `review0` and `mutations`): 10 of 10 safeguards broken
  one at a time fail `tests/webcortex/test_background.py` (quiet before the model call and before consolidation, an
  owner's resume and requeue, owner-asked work honoured, origins matched with case, the same-tick dedupe, tick_admission
  carried by restore, an entry point handed to a thread, an origin that is not a literal; the 2aee516 scan misses the
  last two). The admission benchmark re-run gives the same launches, subjects and refusals at every offset under both
  rules.
- **After the re-review's fixes** (`genesis_safety.json`, `review0b` and `mutations`): 14 of 14 (control 43 passed),
  4 new: a refresh quiet stopped leaves its prediction due, a forwarder that also launches work is a launch site (the
  03cbece scan misses it), an owner-asked background task reads as owner-confirmed, the tick's kind check. The
  admission benchmark run at 03cbece and with these fixes in one session: identical launches, subjects and refusals
  at every offset, variant and rule.
- **The autonomy tests** (`tests/webcortex/test_background.py`) show the launches the legacy rule makes and the
  unified rule refuses: a subject at 0.03 bits (above the old floor, below `min_gain`), a subject investigated
  autonomously an hour earlier (cooldown), every subject while autonomy was switched on without the recorded enable;
  and a record template under both.

## Alternatives rejected

- A second switch per mechanism, or quiet as `autonomous=false` plus monitor pauses: it would change stored values
  the owner set, and a new mechanism would still need its own switch.
- A Presence activity or condition for quiet: none fits as a state of activity, and adding vocabulary means the
  shared contract and the visual-rules tests; the owner's setting is shown through `sources.background` and the
  existing `resting`.
- Holding upkeep too: stopping evidence expiry would let expired evidence keep counting (MEM-09), and holding the
  idle unload would keep memory the owner chose to release; neither starts research or calls a model.
- Widening the tick's pool to every ranked knowledge item (measured as an ablation): it would admit subjects the old
  tick never reached, a loosening; left to the owner.
- A schema change (a table of owner switches): the settings row already gives logging and restore.

## Consequences

- Default off: nothing changes until the owner turns quiet on. With quiet on, a background task already running
  ends `partial` and loses what it read (it is not consolidated); a background task the owner resumes or queues again
  by hand is owner-asked work and runs.
- The tick under the unified rule needs the recorded enable (`--set autonomous=true` alone no longer runs its
  curiosity), refuses subjects below 0.05 bits, beyond one step from the owner's records or within the cooldown, and
  researches the subject's search terms (`curiosity.search_terms`, as the idle investigator does) instead of the
  statement's own words. `test_curiosity_researches_a_statements_own_words_never_a_record_template` now pins the
  legacy rule.
- `autonomy.status` and `store.regulation()["config"]` show `quiet`; the console overview (Regulation, "Background
  work") and the Presence entity on the console and Pocket say it in plain words.
- Changed at the defaults by the Wave 0 fixes, beyond quiet and the admission rule themselves: the unified tick
  skips a subject whose prediction refresh it queued in the same tick (`scheduler.REFRESHED_THIS_TICK`);
  `predictions.check_due`'s `refresh_submitted` actions carry `knowledge_id`; an owner's requeue or resume of a
  background-origin task records a decision event with it (`background.asked_by_owner`) even while quiet is off.
- What the launch scan cannot see is listed in ADR-049 ("What the triggers and the scans cannot see"): only the
  runtime check (`background.allowed`, `held_task` at claim, the run's stop check) enforces quiet while the system
  runs.
- Tests: `tests/webcortex/test_background.py` (each cognition entry point driven with quiet on, a driver for every
  registered one, an AST scan of the source for unregistered launch sites with a self-test of the scanner, the
  systemd units, the owner-only surfaces including MCP, a damaged value, restore, the other switches unchanged and
  owner work running, Presence in both scopes, a running worker within one poll, the unified rule against the legacy
  one), `tests/presence/client_check.mjs`, `tests/e2e/test_console.py` and `tests/e2e/test_presence.py`.

# ADR-051 (draft) Instinct-like regulation as measurements: an internal-state record, predictive regulation, salience, curiosity by learning progress, information gain beside decisions

## Context

Cognitive Genesis §4 (lane I). Verified in code at the start commit (2aee516): the individual measured much about its
own condition, but only `store.write_allowed` and the ask budget acted on it, and the measurements lived in five
separate views (`resources.status`, `store.regulation`, the gateway's health and token ledger, Presence, the voice
hub), none persisted. Regulation was reactive only: `policy.background_gate` refuses new background research at
"critical" memory pressure or while audio is active, and a background task already running continues, so a task
started just before a slow leak reaches the critical line still adds its memory on top. The executive chose by a
fixed rule order with no information-gain computation. Curiosity's only outcome signal was the futility streak
(`curiosity._futility`). The cognitive benchmark's baseline (`genesis_baseline.json`) measured 0 of 3 (development)
and 0 of 7 (held-out) memory breaches avoided, 0.014 bits per core investigation (random 0.037), and 38 % / 54 % of
the timer's curiosity budget (idle investigator about 80 %) spent on a subject that never resolves.

## Decision

Each piece has its own setting. Nothing here is a feeling or awareness; the names describe what is measured.

1. **Internal-state record (`neuroscifi/internal_state.py`), display, on.** `snapshot()` assembles one typed,
   versioned record (`nsf.internal_state/1`) from what already exists: memory and pressure, the background gate,
   audio, background model slots in use and model services in use (read from `/proc/locks`, never by taking a
   lock), the idle policy's unloaded services, write regulation and backlog, the research queue (owner and
   background), the worker's heartbeat, the token ledger's last five minutes (read only; a missing ledger is not
   created), the endpoint's health on request (`--gateway`), knowledge staleness, prediction errors and open change
   questions, open contradictions, and `quiet`. Every field carries `value`, `source`, `at` and `age_seconds`.
   `nsf internal-state [--gateway] [--history N]`. The research worker's heartbeat thread appends one compact sample
   (numbers and flags only) every 30 s to `<db>.resources/internal-state.jsonl` (the audio beacon's folder and file
   rules: no link followed, bounded reads, the folder's file mode, so the idle account can read it); 720 samples are
   kept (6 h). No new daemon, timer or listener; the heartbeat stays a maintenance activity (`background.py`). Like
   `idle-events.jsonl` and the token ledger it is telemetry: no text, not in checkpoints, nothing `forget` must
   reach. `nsf config --no-internal-state-history` stops it.
2. **Predictive regulation (`resources/forecast.py`), off.** `assess(history)`: available memory as it would be with
   no background task running (available + footprint x running, the footprint being the median memory change per
   change in running background tasks), Holt's linear smoothing (level 0.5, trend 0.3) over the last 40 samples, a
   declared horizon of 180 s; hold when the forecast less the existing cap's worth of background tasks
   (`BACKGROUND_MODEL_SLOTS`) falls under the code's critical line (`monitor.thresholds`). A regular pattern of voice
   sessions (at least two starts, intervals within 25 % of their median) is anticipated the same way. With
   `nsf config --predictive-regulation` on, `policy.background_gate` adds the refusal `why="forecast"` after the
   audio and critical checks: it can only add a hold on NEW background research, never lift one, never touch owner
   work, a running task, idle unloading or any other lever, and never keeps anything alive. Too little or stale
   history (fewer than 4 samples, newest older than 300 s) gives no forecast.
3. **Salience record (`webcortex/salience.py`), display and record, on.** One score per autonomy candidate,
   `sum(weight x signal)` over novelty, contradiction, staleness, confidence shortfall, owner importance, potential
   consequence and prediction discrepancy (each in [0, 1], declared weights summing to 1), with every signal's value
   and a one-line explanation, stored with the decision event of a queued investigation (`autonomy.decision_inputs`).
   It changes nothing that is admitted or its order. `nsf web config --set salience_record=false` hides it.
4. **Curiosity by learning progress (`curiosity.progress`), on.** Per subject, its last six looks in `web_feedback`:
   a look's realised gain is the entropy it removed, and a look that changed what is held (new, reversed,
   strengthened, weakened; not an unresolved disagreement) is credited with the gain expected of it; a look made
   progress when it realised at least half the expected gain. With n looks without progress since the last that made
   some, P(resolvable) = p (1 - c)^n / (p (1 - c)^n + 1 - p) with p = 0.5 and c = 0.8, and the expected gain is
   multiplied by P(resolvable) / p (only ever down). Applied in `curiosity.rank` (both launch paths read it) and to
   derived hypotheses in `autonomy.candidates`; never to the owner's own records. Hard caps, `min_gain`,
   `autonomy.admission`, `background.allowed` and `quiet` are unchanged.
   `nsf web config --set curiosity_learning_progress=false` turns it off.
5. **Information gain beside decisions (`instance/information.py`), display, on; rule information-v1, opt-in.**
   For a new reference root, each camera root and each partner: the exact mutual information between the hidden bits
   and the next 3 readings, H(Y) from the ratio of marginal likelihoods with and without each hypothetical sequence
   (`beliefs._infer`, as the change detector rescores hypothetical counts) minus the reading noise; beside it
   `p_informative`, a Bayes factor of the subject's own readings between the reporter the model assumes and coin
   flips (prior 1/2). `nsf decide` and `nsf explain` show it; the decision is unchanged (`decision_information`).
   Rule versions are immutable: `rules-v1` is the existing order, unchanged; `information-v1` keeps rules 1-4b and
   replaces 5-7 by the subject with the greatest EIG x p_informative if it reaches 0.01 bits, else act. Existing and
   new individuals keep `rules-v1`; the owner adopts the other with `nsf config --executive-rule information-v1`
   (logged, carried by restore). The ask budget and the "answered, wait for 3 events" rule apply to both.
6. **Habitual or deliberate (`information.processing`, `information.report`), display, on.** A decision in an epoch
   that continues a matched earlier setup (an epoch link) is habitual, otherwise deliberate; a change suspected in a
   linked epoch says the habit may no longer apply. `report()` gives the rates from the recorded decisions, links
   and changepoints. No new automation.

Settings: `InstanceConfig.internal_state_history` (true), `predictive_regulation` (false), `executive_rule`
("rules-v1"), `decision_information` (true); `WebConfig.curiosity_learning_progress` (true), `salience_record`
(true). A damaged saved value reads as the behaviour before the setting (no history, no forecast holds, rules-v1, no
display) and `store.setting_problems` names it. No schema change, no migration.

## Measured

`docs/quality/evidence/genesis_instinct.json` (the cognitive benchmark, `--mechanism instinct` / `instinct-idle`,
each component ablated; constants chosen on development, held-out run once; the withheld family not run). Held-out:

- **Predictive regulation (anticipation, 8 traces x 3 seeds).** Memory breaches avoided 0.80 of reference episodes
  (baseline 0.00); memory breach ticks 18.0 (54.7); lead before a memory breach 9.9 ticks (5.8); voice-session
  breaches avoided 0.53 (0.00); breach ticks over all channels 160.3 (217.7). Cost: false-alarm share 0.043 (0.009)
  and background work given up 0.106 (0.044). Ablations: without the footprint, memory avoided 0.00; without the
  voice pattern, audio avoided 0.00; without the trend, memory avoided stays 0.80 on held-out (on development 0.11
  against 0.56). Latency and backlog channels are not addressed (the product has no latency target to hold to).
- **Learning progress (information value, web).** Timer path: budget on the noise subjects 0.375 (baseline 0.537),
  on the stale-but-important subject 0.167 (0.083), realised bits per budget 0.163 (0.093). Idle path: noise 0.375
  (0.800), stale 0.167 (0.000), realised bits per budget -0.017 (-0.016). Without learning progress both paths
  return exactly to their baselines.
- **information-v1 (information value, core).** Realised bits per investigation 0.118 (rules-v1 0.092; oracle
  0.119, random 0.083); share of investigations on noise partners 0.31 (0.10).
- **Retention.** With predictive regulation and learning progress on: 4/4 scenarios, 7/7 offline metrics retained,
  adversarial 12 (baseline 12). With information-v1 on as well: `lifelong_correction` fails its check
  `asks_only_while_partner_ambiguous`, a property of rules-v1's order (information-v1 asks about whatever is most
  informative), so information-v1 stays the owner's choice.
- **Cost on this host (aarch64, medians).** `decide` 1.31 ms without the display, 1.48 ms with it, 1.64 ms under
  information-v1 (a 120-reading epoch); `snapshot` 0.69 ms; one history sample 0.60 ms; 720 samples 181 KB.
  `background_gate` 0.47 ms with the setting off (it now reads the setting with a read-only connection) and 2.05 ms
  with it on over a full history. Every benchmark family ran with 0 model calls and 0 refused connections.

## Defaults and why

- On: the record and its history, the salience record, the information display, learning progress (held-out: a
  clear drop in budget on noise on both paths, the stale subject kept and reached more often, retention and the
  offline metrics unchanged).
- Off: predictive regulation. It shows a clear held-out gain, but it gives up 2.4 times the background work and
  raises the false-alarm share fivefold on the benchmark's plant, whose constants are declared, not measured on the
  host, and its footprint estimate on the real host is unmeasured. `nsf config --predictive-regulation` turns it on.
- Opt-in: information-v1 (it changes a retention scenario's rules-v1 property; existing individuals keep their rule).

## Alternatives rejected

- A learned forecaster or learned salience weights: not transparent, and nothing external to train them against.
- Taking each lock to see whether it is held: it would block a slot taker or the idle pass for a moment.
- Writing the history from the console or the CLI too: the worker is the process that runs continuously and may
  write there; a second writer would add a second cadence.
- Pure EIG under the belief model as the rule: a partner whose words are coin flips keeps its belief near 1/2 and so
  can keep the greatest EIG (the noisy TV); the check of its readings against coin flips is what removes it.
- Changing rules-v1 in place: rule versions are immutable; the owner adopts a new one.

## Consequences

- `nsf decide` gains `rule`, `information` and `processing`; `nsf explain` gains `information`. Autonomy candidates
  and their decision events gain `salience` and, where it applied, `learning_progress`.
- The worker writes one small file in `<db>.resources/`; lane W surfaces the record in the console.
- Tests: `tests/test_internal_state.py`, `tests/instance/test_information.py`,
  `tests/webcortex/test_curiosity_progress.py`, `tests/acceptance/test_cognition_instinct.py`.

# ADR-052 (draft) Imagination is computation whose answers can be checked: exact what-ifs on a sandbox, graph forecasts, a causal engine, and scenario records kept apart. R

## Context

Cognitive Genesis §5 asks for imagination that is "an active cognitive mechanism, not merely text generation":
counterfactuals with explicit assumptions and uncertainty (§5.2), prospective simulation with bounded horizons (§5.3),
an isolated sandbox (§5.5) and an imagine → predict → observe → compare loop (§5.6). The core audit found the substrate
there and unused: the belief model's exact inference can score hypothetical readings, a promoted graph can be
iterated, and the acceptance code already copies an individual into memory; the graph has no action input (audit-core
Q5). The research review graded deterministic engines on declared models as established, and model-written scenario
narration as preliminary (research.md 2.2, 2.3, 2.6). The cognitive benchmark measured today's counterfactual coverage
at 0.107 (development) and 0.071 (held-out): one-step graph predictions only. ADR-049's imagined store exists for
whatever such a mechanism keeps.

## Decision

1. **Sandbox (`instance/imagine.py: sandbox`).** A private in-memory copy of the individual (sqlite backup; the live
   connection is only read). It is an in-memory database by construction and checked (`store.database_path(copy) is
   None`), so nothing done to it reaches the file. Bounded: a file above `imagine_max_bytes` is not copied, and work on
   the copy is interrupted after `imagine_max_seconds`. Tests: after writes into the sandbox the live file's bytes
   (file and WAL), state version and journal are unchanged; a mutation that hands the live connection to the sandbox
   fails both isolation tests.
2. **Exact what-ifs on the beliefs (`what_if`, `nsf imagine what-if`).** Flip, drop or add readings (an evidence id,
   `#SEQ` for a consolidated reading, or `FACTOR=READING[xN]`, where `agree`/`disagree` means the factor's most
   probable next reading) on the copy, and read the belief model's own exact posterior. The answer carries the model's
   assumptions, the stated changes, before and after, and the uncertainty "exact under the model". Tests check
   equality with a hand-made copy and with actually observing the added readings.
3. **Comparing next readings (`question_values`, `nsf imagine questions`).** For each reading the individual could take
   next: the exact predictive probability of each outcome (a ratio of marginal likelihoods), the posterior after each,
   the expected change and the mutual information with each hidden bit. It decides nothing; it is the function an
   information-gain score may read (lane I). No executive rule changed.
4. **Retrospection (`retrospect`, `nsf imagine retrospect`).** Exact leave-one-out on the copy over the latest readings
   (journal and consolidated) and the owner's answers that shape the beliefs (confirmed and rejected changes, the link
   to an earlier setup); interchangeable readings are grouped.
5. **Forecasts from an active graph (`instance/forecast.py`, `nsf imagine forecast`).** How a state carries over is
   learned from the graph's own training trajectory (x' = c x + g y + input, per population), giving k-step means
   A M^(k-1) x with M = diag(c) + diag(g) A, and variances propagated from the residual covariance plus the graph's
   validation error. Stated limit: 12 steps, and nothing beyond one step when the carry-over grows (spectral radius
   >= 1: the error has no bound). Parameters are taken as estimated (the interval covers process noise only).
6. **Consequences of an action, only where the data identifies them.** The belief model's recorded actions do not:
   an action enters only as the declared inversion of a reading (bit XOR action), and nothing recorded says what an
   action did to the world, so such questions are declined. On a graph, a push is answered only when the training
   trajectory holds inputs that move a population by itself (at least 5 % of its next-state variance), those inputs do
   not follow the state or themselves (|correlation| below 4/sqrt(T)), and the carry-over holds on the validation
   trajectory (error at most 1.5 times the training one); otherwise it is declined with the reason. Tested on the
   benchmark's worlds (answered, within the stated spread) and on worlds without inputs and with feedback (declined).
7. **A linear-Gaussian causal engine (`neuroscifi/causal.py`, `nsf imagine scm FILE`).** Rungs 1-3 in closed form on a
   declared model (conditioning; the mutilated model; abduction, action and prediction, with partial observation). A
   question with only a graph and an observational covariance is answered by back-door adjustment, and declined when a
   hidden common cause touches the cause. Front-door and other identification formulas are not implemented, so a
   decline means "not identified by this engine" (see the benchmark note).
8. **Scenario records for predictions (`webcortex/scenarios.py`).** For each open prediction one deterministic record of
   kind `simulation` in the imagined store, derived from the prediction and its hypothesis or knowledge item: three
   futures (decisive and right `d q`, decisive and wrong `d (1-q)`, nothing decisive `1-d`), q the forecast as the
   ADR-046 mapping reads it, d the Laplace share of earlier settled predictions of the kind that were decisive; the
   assumptions are written with it. When the prediction settles the outcome slot is filled once (the prediction named
   as the evidence) with the three-way Brier and log score and the plain forecast's Brier; `errors()` reports them per
   mechanism on read, and the next template's d already includes the settled prediction. The timer runs it
   (`scheduler.tick`, activity `tick.scenarios`, held by quiet). Settlement is pulled from the stored outcomes, so
   `evolution.py` is not touched and a failure here can never roll back a settlement. No model writes scenarios: a
   model-written pilot was not built, since no offline ablation can show a gain (research.md: preliminary). No blending
   generator.
9. **The adversarial case about `provenance='simulated'` was mislabelled, and is corrected.** Evidence: the label is
   written by the acceptance harness and the cognitive benchmark into individuals made for the test (every reading of
   the case's individual carries it, the reference readings that pin the mechanism too), by `datasets.simulate`, and
   by the owner's explicit `nsf observe --simulated` / `epoch stream --simulated`. ADR-014 and ADR-049
   (`provenance.mapping`) define it as an observation of a simulated test environment, and the acceptance scenarios
   learn from exactly such readings: excluding them from beliefs would break that suite and contradict both ADRs. What
   the directive forbids is the system's own imagined content counting as an observation, and that cannot happen:
   imagined readings exist only on a sandbox copy, and the evidence tables refuse imagined ids (ADR-049). The case is
   now `imagined_reading_moves_no_belief`: a what-if of the same four readings must leave the live belief, the journal
   and the state version unchanged (the what-if says 0.994 on the copy; the live belief stays 0.500); the old
   observation is still reported (four observed environment readings move it to 0.994). Mixing simulated-environment
   and real readings in one epoch is not refused: no product path does it unasked, and consolidated readings do not
   keep their provenance, so an epoch-level rule needs a schema change. It is an open question for the owner. Existing
   individuals see no change.

## Settings (owner's, `nsf config`; carried into a restored copy)

| Setting | Default | What it does |
|---|---|---|
| `imagine_what_if` | on | `what_if` and `question_values` (read-only; stored only with `--keep`) |
| `imagine_forecast` | on | graph forecasts and pushes (read-only) |
| `imagine_retrospect` | on | leave-one-out retrospection (read-only) |
| `imagine_scenarios` | off | scenario records for predictions, written to the imagined store, compared once settled |
| `imagine_max_seconds` | 10 | the longest a question may run on its copy (0.1 to 600) |
| `imagine_max_bytes` | 512 MiB | the largest file a sandbox may copy (`--imagine-max-mib`) |

A damaged switch reads as off and a damaged bound as its default (`setting_problems` says so). The three read-only
switches are on: the benchmark shows the gain below, and they are reached only from `nsf imagine` and the library, so
nothing existing changes. The scenario records write, and have no measurement on a real individual (no individual has
settled predictions yet), so they stay off.

## Evidence (`docs/quality/evidence/genesis_imagination.json`, `scripts/genesis_imagination.py`)

Counterfactual family, each split's three seeds, mean (baseline rerun equals the recorded baseline):

| | coverage dev | coverage held-out | rel. error dev | rel. error held-out |
|---|---|---|---|---|
| baseline | 0.107 | 0.071 | 0.045 | 0.045 |
| imagination | 1.000 | 1.000 | 0.011 | 0.010 |
| without the causal engine | 0.429 | 0.429 | 0.026 | 0.024 |
| without the what-if | 0.893 | 0.893 | 0.012 | 0.011 |
| without forecasts (and pushes) | 0.786 | 0.750 | 0.006 | 0.004 |
| without pushes | 0.893 | 0.893 | 0.008 | 0.006 |

Interval coverage (within 2 stated sd) 1.0 and correct abstention on the confounded questions 1.0 in every row.
Adversarial 13 of 13 with the corrected case (the recorded 12 of 13 differs by the relabelled expectation, not by a
mechanism). Retention (full, against the recorded baseline): every scenario passes, every metric retained. Cost: a what-if 5 ms at 1,000 readings and 24 ms at 50,000 (the copy is
the whole file, 8 MiB there); retrospection over 200 readings 0.8 s at 50,000; a 5-step forecast on 24 populations
2 ms. No model call, no network.

## Benchmark note

The family's "confounded" structures (hidden U → X0 and U → Y around the chain X0 → X1 → … → Y) are identifiable by the
front-door criterion through X1, so "decline" is right only for an engine without front-door adjustment, as this one
is. The frozen split is not changed. The causal engine's share of the coverage is exact arithmetic on declared models
and says nothing about real-world causal questions; the core mechanisms' share is in the what-if, forecast and push
rows. Interval coverage is a weak test here: the stated sd includes each population's own noise while the truth is
the conditional mean.

## Consequences

- New owner commands `nsf imagine what-if | questions | retrospect | forecast | scm | scenarios`; every answer is
  labelled "imagined, not evidence". `--keep` stores an answer in the imagined store derived from the epoch and the
  readings it names, so forgetting those readings removes it.
- No schema change, no migration, no new dependency, listener or daemon. `background.ACTIVITIES` gains
  `tick.scenarios` (cognition: held by quiet), and the tick timer's unit lists it.
- The cognitive benchmark registers the mechanism `imagination` (`acceptance/cognition/imagination.py`, in
  `PLUGINS`) for the counterfactual, retention and adversarial families; the adversarial case is renamed as above.
- Not done: model-written scenarios, conceptual blending, front-door identification, actions on the belief model,
  parameter uncertainty in forecast intervals, a refusal of mixed simulated and real readings (owner question).

# ADR-054 (draft) Answer monitoring: a calibrated confidence from existing signals, selective answering, source dependence and the executive's precision report. R

## Context

Cognitive Genesis §6.3 asks for metacognitive monitoring with task-level evidence. Verified in code at 2aee516:
the verdict's confidence (a Beta posterior per independent group) ranks supported against disputed claims
perfectly on the benchmark but is under-confident (held-out ECE 0.34, Brier 0.12); nothing turns the signals the
pipeline already produces (verification state, groups for and against, guard refusals, the self-critique's marks,
contradictions, staleness) into one probability; `claims.independence_groups` counts two subdomains of one site, or
a page and the page it cites, as two independent groups; reading depth follows fixed per-mode budgets; the records
that say how often the change detector's alarms were confirmed exist (`pending`, `changepoints`, `epoch_links`)
but nothing computes it; the self-critique's marks were never checked against gold. The research (Huang et al.
2024; Panickssery et al. 2024) rules out a model judging its own output as a gate.

## Decision

1. **`webcortex/monitoring.py`, a record per research result (`report["monitoring"]`, setting `answer_monitoring`,
   on).** A transparent sum of named log-odds terms (`WEIGHTS`, fixed, never fitted) over existing signals, mapped to
   probabilities by monotone maps (isotonic, Beta(1, 1) per block, per stratum) fitted on the cognitive benchmark's
   development split only and shipped as `webcortex/data/monitoring_calibration.json`. It changes no verification
   state, posterior, guard decision, answer or knowledge, and calls no model. The self-critique's marks are one
   named input with weight 0 (measured: they do not predict wrong sentences), never a gate.
2. **Selective answering (`selective_answering`, off).** Below the threshold chosen on development data for a 10 %
   target risk, the answer opens with a plain "Not enough evidence for a confident answer: <why>" line; the quotes
   stay.
3. **Verification escalation (`verification_escalation`, off).** Below the threshold, and only inside the task's own
   budget (a page, a query, two requests, 15 s left), one search for the weakest statement and one more page from a
   family of sites not read yet, then evaluation and synthesis again; once per task; the owner's stops apply.
4. **Adaptive depth (`adaptive_depth`, off).** Reading stops once 4 pages were read, a statement is verified and
   the last 2 pages added no verified statement and no contradiction. Deepening is item 3.
5. **`claims.dependence`**: groups joined by a shared site label or a citation link, reported in the record and in
   plain words; `independence_groups` and every verdict are unchanged.
6. **`executive.precision` and `nsf regulation --precision`**: per rule and per epoch, how the executive's
   questions ended and the precision of the change detector's alarms; display only, computed only when asked.

No schema change, no migration, no new dependency, endpoint or process. The record lives in `web_tasks.result`, so
forgetting a task removes it.

## Measured

`docs/quality/evidence/genesis_monitoring.json` (`scripts/bench_monitoring.py evidence`; offline, no model):
held-out claim probability Brier 0.122 → 0.042, ECE 0.341 → 0.199 (debiased 0.348 → 0.204), AUROC 1.0 → 1.0;
held-out answer confidence ECE 0.372 → 0.181; development AURC 0.080 → 0.027. Selective answering: no held-out gain
(no held-out answer is wrong), so off. Escalation: no metric changed at +2 pages: off. Adaptive depth: equal rates
at 22 fewer pages of 581, wall time not lower: off (an owner decision). Offline benchmark with the settings off:
identical to the unchanged code (digest); with `answer_monitoring` on: 0 differences. Guard differential: 0
different. Ablations of every term are in the evidence file.

## Alternatives rejected

- Fitting the weights on the 21 development items: too few to fit eight weights without overfitting; the weights
  stay declared and only the monotone map is fitted.
- Retuning after seeing ablations: the ablations show `stale` and the posterior term slightly hurt development
  ranking on this corpus; changing them now would tune against held-out numbers already seen.
- Calibrating from live settled forecasts (ADR-046) instead: the right next step for the open web once enough
  research answers have settled outcomes; not available offline.
- Self-critique as a gate or a strong input: measured not predictive; and a model grading its own output is not a
  gate under the Genesis rules.

## Consequences

- Every research result is about 2 KB larger. Console, Pocket and Presence do not show the record yet (lane W or
  the integration pass).
- The calibration is fitted on a fictional corpus; on the open web it is a starting point.
- Tests: `tests/webcortex/test_monitoring.py`, `tests/instance/test_precision_report.py`,
  `tests/acceptance/test_cognition_monitoring.py` (the corpus runs under `NSF_BENCH_COGNITION=1`).

# ADR-055 (draft) Knowledge that knows what it rests on: premises, re-checks, reflection and gated strategy selection. R (H for the schema)

## Context

Cognitive Genesis directive §7 (recursive knowledge evolution) and §6.6 (reflective introspection). Verified in code at
`2aee516`: a hypothesis had no assumptions, expected information gain or cost, and `abandoned` was never set
(`evolution.HYPOTHESIS_STATES` only); a failed prediction changed no belief and marked nothing; the research-policy
weights and the per-mode outcome statistics (`state.record_mode_outcome`) were written and read by nothing;
knowledge was flat (no record said what a conclusion rests on, so nothing re-checked it when a premise changed); no
reflection routine ran; transfer was never measured. Lane S measured that the timer's ten-candidate curiosity pool is
filled by contested subjects that are not knowledge, so stale knowledge is never refreshed through it.

## Decision

Schema (one additive migration, appended last; tests find it by its content `CREATE TABLE IF NOT EXISTS
web_premises`; IF NOT EXISTS throughout, so it runs again on a file that holds its objects):
`web_hypothesis_details` beside `web_hypotheses` (`assumptions`, `counter_evidence`, `expected_info_gain`, `cost`,
`revisions`; one row per hypothesis recorded after it, none invented for older ones; a table rather than columns
because `ALTER TABLE ADD COLUMN` cannot run twice), `web_premises` (refuses an `imagined:` id by CHECK), `web_rechecks`,
`web_reflections`, `improvement_ledger` (append-only by trigger). Seven settings, each its own switch:

1. **Hypothesis fields** (`hypothesis_fields`, on: display-only records). A new hypothesis records template
   assumptions read from its own records, counter-evidence (the supporting claims of the item it is an alternative
   to, then every claim linked to it by a `contradicts` edge), the expected information gain of its forecast and the
   cost of one fact-verification investigation (the numbers autonomy ranks it by), and every status change with the
   evidence that made it (`supported`, `revised`, `refuted`: the prediction and task; `abandoned`: the rule).
   `evolution.compete(key)` compares the open hypotheses of one key with "other" as a member, likelihood ratio
   0.8/0.2 per independent group raised to 0.5 (dampened, research.md 4.5); display-only.
   **`abandoned`** (`hypothesis_abandon`, off: no benchmark measures it): a derived hypothesis whose every prediction
   expired with no tracked observation and none open. The owner's hypotheses are never abandoned automatically.
2. **Premises and re-check marks** (`premise_recheck`, on: display-only). New knowledge rests on its supporting
   claims; a hypothesis on its claims, the item it is an alternative to and the hypothesis it revises. A superseded
   or contradicted premise, a forgotten one, and a failed prediction (item 4 of the brief: what it rested on) mark
   every held dependent "to re-check", at most 50 per change, one open mark per subject, 500 open in all. A mark
   changes nothing: no confidence, status, verification or answer moves (test: the item is identical with the
   setting on and off). A subject changes only through consolidation of new evidence, which closes its mark with
   what the evidence did. Forget: `premises.purge` in the forget's transaction and after a restore's replay marks the
   dependents of a premise that is gone (the mark names no forgotten id) and deletes every row naming it.
3. **The idle re-check** (`idle_recheck`, **off**; `recheck_per_tick` 1, `recheck_daily` 4). Registered background
   activity `tick.knowledge_recheck` (cognition: held by quiet). Before the due-order refreshes, the timer ranks live
   knowledge by importance x staleness x (1 + DEPENDENT_WEIGHT x dependents) x curiosity's futility factor, staleness
   = TTLs elapsed (+1 when marked, marked stale or a newer release was seen), and admits picks through
   `autonomy.admission(path="recheck")`: the unified rule unchanged (recorded enable, min_gain on the stated forecast,
   lineage, cooldown, templates, searchable words), only the pool differs (the re-check's ranked knowledge instead of
   curiosity's ten best). A pick is a refresh task (`refresh:<id>`, autonomous budget, claimed after owner work), its
   due prediction linked to it. `curiosity.rank` is not changed.
4. **Reflection** (`reflection`, off; `reflection_interval_days` 7; `nsf knowledge reflect` on request). Registered
   activity `tick.reflection` (cognition). A deterministic report over a period (learned, failed, assumptions,
   missing, strategies) whose every line names its record ids and quotes no statement; at most 5 actions: re-check
   marks, abandoned hypotheses (only with `hypothesis_abandon`), one owner question within the web question budget.
   No line is a gate. An optional local-model summary (`reflection_model_summary`, off) is stored as an `inference`
   in the imagined store and is never evidence. Forget drops every line and action naming a removed record.
5. **Strategy selection** (`strategy_selection` "gate"). Thompson sampling over research modes, Beta(1 + rewarded,
   1 + not) over the last 100 finished tasks per mode; reward = the task produced verified knowledge or settled a
   prediction decisively, read from the records. Self-reported mode statistics are never rewarded. It only re-orders
   the candidates `autonomy.admission` admitted. In force only after `strategies.promote` passes its gate (held-out
   gain of at least 0.02 verified items per investigation, no larger share on the self-reporting arm, retention not
   worse, one withheld evaluation by the main session) and while the owner has not reverted it or set "off".
6. **Improvement ledger** (`improvement_ledger`, `nsf knowledge ledger`): capability, level (1 to 3), change,
   evidence (run ids), generalisation, regressions, cost, reproducibility, decision (retain/revise/revert), decided by
   (owner/gate). Levels 4 (weights) and 5 (architecture) are refused: they need the owner's authorisation
   (`evolution.model_adaptation_guard`).

## Measured

`docs/quality/evidence/genesis_knowledge.json` (`scripts/bench_genesis_knowledge.py`, worlds frozen at `8b8a9c0`
before any mechanism; `scripts/bench_cognition.py` with the plugin `neuroscifi.acceptance.cognition.knowledge`).
Simulated worlds driven through the real modules, offline.

- **Retention under change, held-out** (2 worlds x 4 seeds, run once): share of subjects whose live knowledge states
  the world's current truth, mean over the steps: due order (today) 0.693, re-check 0.695 (random 0.663, oracle
  0.803); old subjects weighted by owner importance 0.629 vs 0.638; final step 0.581 vs 0.575; stale facts caught per
  investigation 0.461 vs 0.432. Per world: sparse budget 0.633 (sd 0.097) vs 0.624 (0.104), busy 0.753 (0.040) vs
  0.766 (0.036): **within noise of the due order.** 0 subjects lost, 0 belief writes in every run. Ablation: without
  importance the order is the due order (identical numbers on development, 0.6925 on held-out); marks change nothing
  here (these worlds raise no mark on knowledge); weighting dependents 1.0 gave 0.629 old-weighted.
  Development choices (stated before the held-out run): staleness as 1 - P(current) gave 0.682 against the due
  order's 0.716 (it saturates two TTLs after verification), so staleness is TTLs elapsed; DEPENDENT_WEIGHT 0 (1.0,
  0.25 and 0.1 gave 0.695, 0.685 and 0.696 old-weighted against 0.713 for 0).
- **Information value (cognitive benchmark), the re-check's pick then the timer's curiosity:** development 0.222 bits
  per unit of budget against the recorded baseline's 0.161 (oracle 0.263, random 0.202), share of budget on noise
  0.000 against 0.375; held-out 0.124 against 0.093 (oracle 0.146, random 0.091), noise 0.000 against 0.538. The core
  world is unchanged. The withheld (transfer) family is not run by this lane.
- **Strategy selection, held-out** (2 worlds x 4 seeds): verified items per investigation 0.395 (Thompson) against
  0.265 (incumbent order); share on the self-reporting arm 0.105 against 0.253. Gate: held-out gain and reward-hacking
  checks pass, retention passes, the withheld check is missing, so the decision is **revise: off**.
- **Retention family**: 4/4 scenarios, 7/7 offline metrics retained, with the lane's mechanisms on and off.
  **Adversarial**: 16/17 on and off (the recorded baseline 12/13; the 4 lane-K cases added pass; the one failure is
  `simulated_reading_moves_no_belief`, outside this lane).
- **Cost** (aarch64, while other lanes' suites ran): consolidation 1.35 -> 1.38 ms median per one-cluster task
  with premises; re-check rank 82 ms and admission 168 ms per pass over 200 items; a reflection report 2 ms; a
  Thompson ranking 1.5 ms. 0 model calls in every benchmark.

## Alternatives rejected

- Widening the timer's curiosity pool (ADR-050 ablation): a loosening of the admission rule. The re-check keeps the
  rule and changes only the pool, to knowledge the owner already holds.
- Letting a mark lower confidence or set `stale`: a belief would move without evidence.
- A reflection written by a model, or a model judging strategies: self-grading (research.md 4.8).
- Rewarding the mode statistics tasks write about themselves: gameable by construction (tested).

## Consequences

- Defaults: hypothesis fields and premises on (display-only records; retention and adversarial unchanged); the idle
  re-check, abandonment and reflection off; strategy selection off until its gate passes. The idle re-check showed a
  gain on information value and none on retention under change: turning it on is the owner's call.
- `evolution.py` changed in `_decode_h`, `hypothesis` and `hypotheses` (read the details with a LEFT JOIN: new
  constants `K_COLUMNS`, `_K_JSON`, `_H_SELECT`), `open_hypothesis` (one call to `_describe`), `observe` (two
  `record_revision` calls), `expire` (the abandonment rule) and `register` (lane K's commands); new functions
  `fields_enabled`, `_describe`, `record_revision`, `refresh_counter_evidence`, `abandon_if_unsupported`, `compete`.
- Tests changed beside the new ones: `tests/instance/test_migrations.py` (19 web tables), `tests/instance/
  test_imagined.py` (version 17 is no longer the last migration), `tests/webcortex/test_state.py` (table list).
- New commands under `nsf knowledge`: `rechecks [--rank]`, `reflect`, `reflections`, `strategy [--promote FILE |
  --revert]`, `ledger`, `compete KEY` (JSON, for the console and Pocket lanes).
- Tests: `tests/webcortex/test_knowledge_evolution.py`, `tests/acceptance/test_genesis_knowledge_bench.py`, two new
  drivers in `tests/webcortex/test_background.py`, the table list in `tests/webcortex/test_state.py`.
