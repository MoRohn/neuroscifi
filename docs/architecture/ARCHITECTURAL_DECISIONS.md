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
   `observe`, and `written_by` to read back their own mechanism's records); and *displays*, which may list or count
   records labelled "imagined, not evidence" (`listing`, `show`, `counts`), each entry with its reason. At the Wave 1
   integration the writers are scenario records and kept imagination answers (lane M) and a reflection's model
   summary (lane K); the displays are the console panel, the self-description and Presence's "simulating" (lane W). No evidence path (knowledge, claims, consolidation, synthesis, evolution and calibration,
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
4. **Curiosity by learning progress (`curiosity.progress`), off** (on in the first build; reworked and turned off
   before release, see the amendment below). Per subject, its last six looks in `web_feedback`:
   a look's realised gain is the entropy it removed, and a look that changed what is held (new, reversed,
   strengthened, weakened; not an unresolved disagreement) is credited with the gain expected of it; a look made
   progress when it realised at least half the expected gain. With n looks without progress since the last that made
   some, P(resolvable) = p (1 - c)^n / (p (1 - c)^n + 1 - p) with p = 0.5 and c = 0.8, and the expected gain is
   multiplied by P(resolvable) / p (only ever down). Applied in `curiosity.rank` (both launch paths read it) and to
   derived hypotheses in `autonomy.candidates`; never to the owner's own records. Hard caps, `min_gain`,
   `autonomy.admission`, `background.allowed` and `quiet` are unchanged.
   `nsf web config --set curiosity_learning_progress=true` turns it on.
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
("rules-v1"), `decision_information` (true); `WebConfig.curiosity_learning_progress` (false since the amendment), `salience_record`
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
- **information-v1 (information value, core).** Realised bits per unit of budget 0.118 (rules-v1 0.092; oracle
  0.119, random 0.083); share of investigations on noise partners 0.31 (0.10).
- **Retention.** With predictive regulation and learning progress on: 4/4 scenarios, 7/7 offline metrics retained,
  adversarial 12 (baseline 12). With information-v1 on as well: `lifelong_correction` fails its check
  `asks_only_while_partner_ambiguous`, a property of rules-v1's order (information-v1 asks about whatever is most
  informative), so information-v1 stays the owner's choice.
- **Cost on this host (aarch64, medians).** `decide` 1.31 ms without the display, 1.48 ms with it, 1.64 ms under
  information-v1 (a 120-reading epoch); `snapshot` 0.69 ms; one history sample 0.60 ms; 720 samples 181 KB.
  `background_gate` 0.47 ms with the setting off (it now reads the setting with a read-only connection) and 2.05 ms
  with it on over a full history. Every benchmark family ran with 0 model calls and 0 refused connections.

## After the Wave 1 review (fix lane C): the wording the evidence supports

The bullets above are the lane's measurement on the split's 3 seeds. The review (B1, B2, B6) found three of them
worded more strongly than the evidence; these are the claims now made (`genesis_wave1.json`, wide-seeds run
`genesis_wave1_wide.json`: 12 further seeds 5001-5012 on the held-out worlds, supplementary to the split's seeds):

- **Predictive regulation**, sound within the simulated plant: held-out memory breaches avoided 0.80 (split seeds);
  12 further seeds 0.80 ± 0.07 against 0.06 ± 0.11 for the reactive gate, better in 12 of 12. The plant's constants
  are declared, not measured on the host; the benchmark feeds the forecaster directly, so the gate's wiring, the
  history file and the worker's sampler are not exercised by it (`tests/test_predictive_wiring.py` now drives them
  end to end on a staged individual: the gate holds exactly when the benchmark's forecaster does). The gain is
  mainly the memory reserved per background task: without the trend the held-out share stays 0.80 (on development
  the trend carries more of it: 0.56 → 0.11 without it). On held-out the trend adds lead time (5.7 → 9.9 ticks) but
  avoids no further breaches and accounts for all the added false alarms (without it 0.009). Costs:
  background work given up 0.044 → 0.106, false alarms 0.009 → 0.043.
- **Learning progress, web information gain** (B1, B2). The first build's numbers (held-out timer path 0.093 →
  0.163 bits per budget on the split's 3 seeds, 0.139 → 0.185 on 12 further seeds) were overstated as written and
  are superseded by the rework (amendment below). Re-measured, held-out, off → on (reworked), the two paths apart
  (`genesis_fix1b.json`; re-run on the reconciled tree with identical numbers, `genesis_wave1.json`,
  `genesis_wave1_wide.json`):
  - timer path, split seeds: noise share 0.537 → 0.512, bits per budget 0.093 → 0.134; 12 further seeds: noise
    0.412 → 0.394, bits per budget **0.139 → 0.126** (paired difference −0.013, per-seed sd 0.14: within one sd;
    5 seeds better, 1 worse, 6 equal);
  - idle path, split seeds: noise share 0.800 → 0.650, bits per budget −0.016 → 0.044; 12 further seeds: noise
    0.706 → 0.550, bits per budget 0.094 → 0.144 (11 of 12 seeds better).
  The timer path loses bits per budget on the wider seeds, so learning progress ships off. With every Wave 1
  chooser on (the knowledge re-check's pick first) the held-out timer path is 0.093 → 0.124 on the split seeds and
  0.139 → 0.196 (12 of 12 seeds better, noise 0.412 → 0) on 12 further seeds; that row is the re-check's (ADR-055),
  whose numbers the rework did not move.
- **Retention** held for the shipped defaults only: the first build of information-v1 failed
  `asks_only_while_partner_ambiguous` (as stated above). With information-v1 amended (below), retention passes with
  every Wave 1 setting on, on both splits (reconciled tree, `genesis_wave1.json`).

## Defaults and why

- On: the record and its history, the salience record, the information display (display and record only).
- Off since the amendment: learning progress (the first build was on; reworked, it lowers the timer path's bits per
  budget on the held-out wider seeds, 0.139 → 0.126).
- Off: predictive regulation. It shows a clear held-out gain, but it gives up 2.4 times the background work and
  raises the false-alarm share fivefold on the benchmark's plant, whose constants are declared, not measured on the
  host, and its footprint estimate on the real host is unmeasured. `nsf config --predictive-regulation` turns it on.
- Opt-in: information-v1 (rule versions are the owner's to adopt; existing individuals keep their rule).

## Amendment (review1, before release)

- **Learning progress reworked and now off by default.** A confirming re-verification (a refresh that found the item
  still current) counts as progress on what it was asked to settle; learning progress and the futility down-weight
  read the same `web_feedback` rows, so the stronger of the two applies, never their product; the down-weight halves
  every 7 days after the subject's newest look and never goes below 0.05 (constants chosen on development only).
  Re-measured on the held-out worlds with the split's seeds and 12 further ones (`genesis_fix1b.json`): on the wider
  seeds the timer path's noise share fell (0.412 to 0.394) but its realised bits per budget fell too (0.139 to
  0.126); the idle path gained (0.094 to 0.144, noise 0.706 to 0.550). The rule was to keep it on only if bits per
  budget did not fall on both paths, so `curiosity_learning_progress` defaults to false. The reduced gain is what
  admission compares with its minimum (`curiosity.MIN_GAIN`, `min_gain`): "ranking only" was wrong.
- **information-v1 amended in place (unreleased).** A partner is scored by what its next words teach about its own
  convention (`own_bits`), not the mechanism, and is asked only while its meaning is ambiguous by rule 6's test (at
  least 4 words, |p - .5| < .37); `p_informative` is judged per mechanism segment (pooled, a reporter whose readings
  flip exactly when the mechanism changed looked like a coin). With it on, `lifelong_correction` passes
  `asks_only_while_partner_ambiguous`. Held-out (split seeds): 0.105 bits per budget (rules-v1 0.092, the first
  build 0.118), noise share 0.071 (0.095, 0.307). It stays opt-in. A rules-v1 decision no longer carries `rule`.

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
   None`), so nothing done to it reaches the file. Bounded: a file above `imagine_max_bytes` is not copied. The copy is
   one step (so writers are not held back, review1 Q2), which cannot be interrupted: its time is bounded by
   `imagine_max_bytes`, and `imagine_max_seconds` is checked once it ends (a copy that alone took longer is refused
   then, before any work) and interrupts the work on the copy (review1b N5). Tests: after writes into the sandbox the live file's bytes
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
   as the evidence) with which future happened (its index: an outcome holds no text, ADR-049 as amended in Wave 0);
   the three-way Brier and log score and the plain forecast's Brier are computed from it on read, and `errors()`
   reports them per mechanism; the next template's d already includes the settled prediction. The timer runs it
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

## After the Wave 1 review (fix lane C): what the evidence supports

The table above scores "coverage", which counts any answer: the review (B3) showed a trivial answerer (0 with sd
1e6) reaches coverage, interval coverage and abstention 1.0. "Coverage 0.071 → 1.0" is **not** a supported headline.
The family is now scored on the share of answerable questions answered within 0.1 × scale of the truth, with the
interval score of the stated 95 % interval, in three groups never pooled in a claim, and the confounded questions
relabelled answerable by the front door (a decline there is not within tolerance, and the declines are also counted
on their own line; corrected at the re-review of the fix round; COGNITIVE_BENCHMARK.md §3). Held-out, split seeds (12 further seeds in brackets), baseline → imagination:

| Group (questions per seed) | Within tolerance | What it measures |
|---|---|---|
| all answerable (32, front door included; pooled, not a claim) | 0.063 → 0.875 (84 of 96; 0.057 → 0.862) | the front-door questions declined |
| answerable other than the 4 front-door ones (28) | 0.071 → 1.000 (28 of 28 per seed; 0.065 → 0.985) | |
| equations handed to the engine (16) | 0 → 1.000 | exact arithmetic on the given model |
| belief what-ifs (3) | 0 → 1.000 | plumbing: the truth is the same posterior function on a copy |
| learned graph forecasts and actions (9) | 0.222 → 1.000 within 0.1 × sd (largest error 0.092; 0.778 within 0.05; the tolerance was set in the same commit as the results; 0.204 → 0.954) | **the substantive part**: relative error 0.024 / 0.008 / 0.043 at horizon 2 / 5 / pushes |
| front door (4) | declined (each not within tolerance) | identifiable; this engine has no front-door adjustment |

The `zero` reference (the review's trivial answerer) is within tolerance on 0.111 (development) and 0.407 (held-out)
of the learned-graph questions (0.037 and 0.148 within 0.05), because a 5-step forecast decays towards 0; its interval score is above 10^7.
Imagination's interval score is about 2.4 (the stated sd is the outcome's spread, so the width dominates). Adversarial: 13 of the original 13 under the redefinition in §6 (it is a different test: a
reading labelled provenance='simulated' still moves the belief, as an observation of a simulated environment should);
the original concern is now staged as `system_made_content_never_enters_the_journal`, which passes in every regime.

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

# ADR-053 (draft) Self-description record, selective broadcast, Presence from real activity, and continuity. R

## Context

Cognitive Genesis §6.1, §6.2, §6.4, §6.5, §6.7 and §8 ask for a mechanism that makes important information
available across subsystems without broadcasting everything, a structured functional self-model that answers "what
am I doing, what do I know, what am I uncertain about, what can I do next, what evidence supports that decision",
continuity across sessions and devices, and measurements of access and consistency. The runtime audit found five
partial status views and no record; `pending` plus `autonomy.after_task` already a salience-gated, budgeted
broadcast for findings, Presence's RANK already a contention rule; six Presence activities declared and never shown;
worker model calls invisible to the console's Presence; the memory-pressure condition described as paused writes;
MCP's version hard-coded; no console view of `GET /api/resources` or of the imagined store; and a spoken question's
answer lost when the console restarts (R8). The baseline self-description benchmark answered 12 of 15 probes from four
surfaces (never: what it is uncertain about, its last decision, which tools the model may use).

None of this is a claim about experience. The names are functional ("self-description", "selective broadcast"); the
literature (research.md §3) gives no support for reading a workspace or a self-model as evidence of consciousness,
and the measurements below are not scores of anything but the function they name.

## Decision

1. **One self-description record (`self_description.py`, schema `nsf.self-description/1`).** Built on request from
   the existing stores, read-only. Every field is `{value, source, at, age_seconds}`; nothing in it is text a model
   wrote (objectives are the owner's words, reasons are the executive's own sentences, knowledge keys come from
   sources and are shown as data). Sections: doing, knows, uncertain, can, decisions, resources, provenance, recent,
   and `answers` mapping the five questions to field paths. "Uncertain" is the current epoch's least certain factor
   by the exact posterior (mechanism first while it is not identifiable), open contradictions, the lowest-confidence
   knowledge in use and unsettled predictions; "decisions" carries the executive's decision for the most recently
   started epoch computed now (not recorded) with its reason, and the recorded ones with their state_version.
   Served by `GET /api/inventory?self=1` (the overview's plain-words panel; the header's reload does not pay for it),
   `nsf self [--short]`, the Pocket research route `self_status` (signed like every research reply, family scope:
   its own tasks and the global conditions only), the voice intent `self_status` ("status report"; "what are you
   doing" only when the owner turns the voice setting `status_questions` on, off by default) and the
   read-only MCP tool `self_status`. MCP's `serverInfo.version` is now `neuroscifi.__version__`.
2. **Selective broadcast (`broadcast.py`), one function.** `offer(db, kind, now, signals, deliver, ...)`: the two
   existing producers (autonomous findings, monitor findings) call it with their own delivery. Salience is a small
   local function of the producer's counts (`score`, 1 − e^(−Σ weight·min(n, 5))). Mode `off` (default) delivers
   exactly as before and logs nothing; `log` delivers as before and logs the salience decision; `on` delivers only at
   or above `broadcast_threshold` (0.5) and within 12 broadcasts an hour, the rest stays local with its reason. Only
   existing channels: the owner question (`pending`: Presence, console Questions, Pocket push), `web_events` (the
   log), `state_version` (the producer's own bump). Contention: `presence.aggregator.arbitrate` (rank, then newest),
   the ordering Presence itself uses, records which live question is shown first. Seam for lane I: `offer(...,
   salience=, salience_inputs=)` takes the salience record's value; or `score()` is pointed at it.
3. **Presence from real activity.** planning (a discovery stage's `decision` with `needs_web`, before the query
   plan), predicting (a `prediction` event in the last 8 s), simulating (an imagined record in the last 8 s),
   analyzing (an unexpired graph lease), interpreting (the voice hub while the bridge handles a request; rank
   communication), executing (an allow-listed model tool while it runs; secondary `tool_execution`). The research
   worker's streamed model calls are published the way the audio beacon publishes audio: `presence.activity.track(
   ..., publish=<db>)` writes `<db>.resources/inflight-<pid>.json` (no text: activity, purpose, task id, start, token
   count) on start and end and at most once a second while tokens stream; the console reads the other live
   processes' files and ignores dead or stale (600 s) ones. Paused writes now set `sources.writes = "paused"`, and
   the client names each cause of `resource_constrained` in its own words. The console overview shows the
   self-description, "What we were just doing", Resources (`GET /api/resources`, which had no view) and a read-only
   "Imagined, not evidence" list (`GET /api/imagined`).
4. **Continuity.** (a) R8: the voice session context keeps `follow_task_id` while an answer is awaited; the
   principal's next session (after a restart) follows it again and delivers the answer once; owner voice setting
   `resume_follow`, on by default (it fixes a recorded finding and stores one task id). (b) "What we were just
   doing": `self_description.recent` lists the latest person-asked tasks by reference; the owner sees all devices,
   a Pocket family its own, and the owner's own questions only with the web setting `pocket_recent` (off by default:
   a pairing family may include devices that are not the owner's; even then a phone cannot open the owner's report).
   (c) Instrumentation (`scripts/bench_workspace.py`): which subsystems can read an event right after its commit and
   their polling periods, and the record's agreement with the existing surfaces over a scripted sequence.

## Measured

`docs/quality/evidence/genesis_workspace.json` (`scripts/bench_workspace.py`), offline, temporary individuals:

- **Self-description family** (cognitive benchmark, 15 probes, 2 states × 2 seeds per split): baseline coverage and
  accuracy 0.80 from 4 surfaces on development and held-out; the record 1.00 and 1.00 from 1 surface on both.
  Ablations (held-out): without the capabilities/resources sections 0.80; without uncertainty, decisions or tools
  0.933 each. Cost about 0.1–0.2 s per family run, 0 model calls.
  *After the Wave 1 review (B4), the wording the evidence supports:* sound with caveats. Held-out, per seed: 29 of 30
  under the original answer key and 30 of 30 under this lane's changed key (`uncertain_about` = "nothing" when every
  factor is settled; the baseline scores 24 of 30 under both). Two probes are plumbing checks, not accuracy:
  `authorised_model_tools` compares the tool list with itself and `last_decision` re-runs the executive's own
  decision; without them the record answers 26 of 26 and the baseline 24 of 26. The lane printed the record's
  answers on the held-out states once during development. Both keys are scored side by side
  (`summary_original_key`, `summary_without_plumbing`).
- **Consistency:** 8 of 8 staged records (96 field checks) and 12 of 12 steps of a scripted day agree with
  store.regulation, store.inventory, autonomy.status, resources.status and presence.derive at the same
  state_version; build 1.3 ms median on these small individuals (it computes one epoch's beliefs: linear in its
  readings, as the overview's largest-epoch measurement already is).
- **Broadcast**, a fixed mix of 10 findings: off 10 owner questions; log 10 (each with its salience logged); on 4 at
  threshold 0.5. A broadcast event is readable by 6 subsystems at once (Presence, console Questions, Pocket push's
  query, the self-description, the worker's data_version wake, state_version); one kept local by 3 (the
  self-description's log, and the change signals of the producer's own record), never by Presence, Questions or
  push. Readers then see it within their existing polling periods: Presence 0.35 s, worker 0.1 s, push 5 s.
- **Presence:** 6 of the 6 declared-but-unused activities now shown from real rows; one derive 0.13 ms median, 0.15
  ms with one other process's in-flight file.
- **R8:** with the start commit's voice hub the answer is not delivered after a restart (the test fails); with this
  change it is, once. Real microphone and handset: unverified.

## Amendment (review1, before release)

- **Scopes.** MCP `self_status` builds scope `agent`: the owner's own research only (`family IS NULL`), no file
  path, web-derived text inside `safety.untrusted` envelopes; MCP `web_task` / `web_evidence` refuse a family's task.
  The family scope carries the resource level and reasons only and a generic background-gate reason (S1, S5, S6).
- **Robustness.** Every field is built under its own guard (a part that cannot be read is that field with `value:
  null` and `error`); the record is read in one read transaction; the console asks `GET /api/self` in its own
  request; the internal-state copy counts knowledge in SQL and decisions are read through migration 19's index
  (A1–A4; owner build at 10,000 knowledge items 188 → 14.9 ms, `genesis_fix1a.json`).
- **Answer monitoring in the record.** `uncertain.answers` gives each latest result's `confidence` and the monitoring
  record's own `confidence_note`: while no fitted map has 30 development items in 3 blocks (none of the shipped maps
  has), the confidence is null and the note says "not enough development data"; the field's source says why
  (reconciliation of review A6 with this record).
- **Voice.** The follow-up is delivered at most once to the person's live session, claimed in the database (when
  the person is live only on another process that does not follow it, into the asking transcript: review1b R3); with
  `resume_follow` off nothing is written; a new owner voice setting `status_questions` (default off) keeps "what are
  you doing?" starting research as before; "status report" and "status" are the status command (A9, A10, A15, S9).
- **Broadcast.** With `log` or `on`, a finding is offered with the higher of its own salience and its candidate's;
  log mode counts what would have been broadcast, so it shows the hourly limit (A7, A8).

## Alternatives rejected

- A bus, a listener or a daemon for broadcast: the existing pending/web_events/state_version channels and polling
  readers already carry it; a new mechanism would be a second source of truth.
- Broadcast on by default: no benchmark shows that holding a finding back helps the owner.
- A persisted self-model table: the record is computed from the stores, so it cannot drift from them, and forget
  and restore need no new rule.
- A model-written self-summary: model text never becomes a statement about the system.
- Showing the owner's Pocket tasks' reports on another family's phone: references only, and only with the switch.

## Consequences

- New settings: InstanceConfig `broadcast` ("off") and `broadcast_threshold` (0.5), carried by restore; web
  setting `pocket_recent` (false); voice setting `resume_follow` (true). No schema change, no migration.
- The benchmark's self-description truth for `uncertain_about` is "nothing" when every factor is settled (it was
  `None`, which no answer could match); the baseline's numbers do not change.
- Tests: `tests/test_self_description.py`, `tests/test_self_description_surfaces.py`,
  `tests/webcortex/test_broadcast.py`, `tests/presence/test_workspace_states.py`, `tests/voice/test_follow_restart.py`,
  `tests/presence/client_check.mjs`, `tests/e2e/test_console.py`.

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
   **Changed after the Wave 1 review (A5, A6, A13, B5):** the claim probability is the *unfitted* score (the fitted
   claim map was worse on development: Brier 0.034 against 0.016 on 8 items; held-out agrees, 0.042 against 0.022 on
   10); a fitted map is applied only with at least 30 development items in at least 3 blocks, and none of the shipped
   maps has that (6 to 15 items, 1 to 3 blocks), so the record shows no answer confidence and says "not enough
   development data" (before, every fact check showed the same 0.941, an "insufficient" one too); the guard's
   refusals and the model abstaining have weight 0 and are shown only (the development data has no model calls); a
   monitoring failure is logged as the task's error event and adds nothing to the owner's report.
2. **Selective answering (`selective_answering`, off).** When the evidence is weak the answer opens with a plain
   "Not enough evidence for a confident answer: <why>" line; the quotes stay. Weak means: for a fact check, and
   wherever no map is applied (everywhere today), `monitoring.sufficiency` says the sources are not enough (fewer
   than two independent groups, one family of sites, one source citing another, stale, or a state that is not
   verified); only an applied map would use the threshold chosen on development data for a 10 % target risk.
3. **Verification escalation (`verification_escalation`, off).** When the evidence is weak (as in 2), and only inside the task's own
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

`docs/quality/evidence/genesis_monitoring.json` (`scripts/bench_monitoring.py evidence`; offline, no model).
*As first recorded by the lane (superseded; kept in the file under `before_wave1_review`):* held-out claim
probability Brier 0.122 → 0.042, ECE 0.341 → 0.199, AUROC 1.0 → 1.0; held-out answer confidence ECE 0.372 → 0.181;
development AURC 0.080 → 0.027. The review (B5) found this is not a calibration gain: 10 held-out claims, all
answered correctly (AUROC 1.0 before and after); 10-bin ECE is meaningless at n = 10; the fitted map was worse than
the unfitted score; the two-line rule on the verdict word scores Brier 0.0025.
*The wording the evidence supports, as shipped now:* on 10 held-out claims, adding the verification state to the
posterior lowers Brier 0.122 → 0.022 with the unfitted score (0.042 with the fitted map, no longer shipped; the
verdict-word rule 0.0025; Brier is all reliability error in every row, resolution 0.24 = the uncertainty); development
8 claims 0.095 → 0.016. **No held-out evidence about selective answering:** no held-out answer is wrong, and the
sufficiency decision would withhold 12 of 20 right held-out answers (development: it withholds 3 of the 3 wrong and
12 of the 18 right). The terms `independence`, `guard` and `critique` are 0 on every item of the corpus, so their
ablations change nothing: they are **untested** here. Selective answering: off (on the offline corpus the
sufficiency basis opens 36 questions with the plain line, key metrics unchanged). Escalation: no key metric changed
(+2 pages with the old threshold; +6 pages, +28 queries for 14 tasks with the sufficiency basis): off. Adaptive depth: equal rates
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
   worse, one withheld evaluation by the main session) and while the owner has not reverted it or set "off". The
   withheld evaluation is a line of `docs/quality/evidence/genesis_final_uses.jsonl`: a `--final` run of the
   `strategy_selection` mechanism without ablations whose line records `"not_worse": true`; the evidence file cannot
   say "not worse" in its place (review1b N1). The harness records a final run before it starts and does not write
   that result, so until such a line exists the gate fails closed ("no withheld run evaluated strategy selection").
   Installed without the docs folder, it fails closed with "the final-runs ledger is not available in this
   installation", naming no path (review1b N2).
6. **Improvement ledger** (`improvement_ledger`, `nsf knowledge ledger`): capability, level (1 to 3), change,
   evidence (run ids), generalisation, regressions, cost, reproducibility, decision (retain/revise/revert), decided by
   (owner/gate). Levels 4 (weights) and 5 (architecture) are refused: they need the owner's authorisation
   (`evolution.model_adaptation_guard`). **The ledger is permanent**: no forget reaches it and triggers refuse every
   update and delete. So it holds no free text (review1b N3): the owner's revert takes a reason code
   (`--reason not_wanted|worse_results|unexpected_behaviour|testing`, or none), and of a promotion's evidence file
   only finite numbers, booleans and run ids matching `^[A-Za-z0-9][A-Za-z0-9:._-]{0,63}$` are kept (anything else is
   stored as null); the gate's own details are fixed sentences with numbers.

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
  `simulated_reading_moves_no_belief`, outside this lane). *Wording after the Wave 1 review:* the four cases are
  invariants that pass in every regime, with every Wave 1 setting off too; they are reported as their own line
  ("4 new invariant cases pass in every regime"), never added to the original 13 as an improvement.
- *Information value, after the Wave 1 review:* the 0.124 held-out figure is the re-check's pick then the **timer's**
  curiosity, on the split's 3 seeds; the idle investigator's path is reported apart (`genesis_wave1.json`
  `idle_path`). With every Wave 1 chooser on, 12 further seeds on the held-out worlds give 0.139 → 0.196 (paired,
  better in 12 of 12), noise share 0.41 → 0. Re-measured on the reconciled tree after the learning-progress rework
  (learning progress on in that regime, reworked): the same 0.139 → 0.196 and 0.412 → 0, 12 of 12; the
  numbers did not move with the rework (on the split's seeds the re-check alone gives the everything-on numbers;
  `genesis_wave1.json`, `genesis_wave1_wide.json`). The idle re-check stays off: no gain on
  retention under change (above).
- *The draw, after the Wave 2 benchmark-integrity review (lane Y, H3):* this harness draws the truth changes, the
  arrivals and every research outcome from one random stream (strategy selection: the offers and the outcomes), so
  two policies that investigate different subjects can meet different worlds and different outcomes. The numbers
  above are kept as recorded (`genesis_knowledge.json` unchanged; its default draw re-run on the integrated tree
  reproduces it: 0 of 635 held-out and 633 development values differ). The decoupled draw (`--decoupled`: truth
  changes keyed by world, seed and step, outcomes by world, seed, step and subject; strategy offers by step and
  outcomes by candidate) is the one documented for paired comparisons; its held-out run, beside the recorded one in
  `genesis_knowledge_decoupled.json`: **retention under change** due order 0.673, re-check 0.682 (random 0.665,
  oracle 0.765); per world sparse budget 0.609 vs 0.612 (paired +0.003, sd 0.021; 3 seeds higher, 1 lower), busy
  0.738 vs 0.752 (paired +0.014, sd 0.036; 2 higher, 2 lower), against a baseline sd of 0.054 and 0.075: **still
  within noise of the due order**, so the idle re-check stays off. **Strategy selection** Thompson 0.348 against the
  incumbent's 0.268 verified items per investigation (paired per world +0.135, sd 0.105, 3 higher / 1 lower; and
  +0.025, sd 0.076, 2 higher / 1 lower), share on the self-reporting arm 0.088 against 0.233 (lower on 8 of 8
  paired seeds): the gain shrinks from +0.130 to +0.080 and stays positive; the gate's decision is unchanged
  (revise: off; the withheld check is missing). What moved is the size of the strategy gain and both levels (the
  decoupled draw is another sample of the same worlds: the due order 0.693 → 0.673, the oracle 0.803 → 0.765); no
  verdict and no default.
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

- After the Wave 1 review (fix lane B, before release): a mark closes, with its reason, when any normal path
  re-verifies its subject, when the subject is no longer live, or after 3 failed re-verifications, and a hypothesis
  is marked only while it rests on live knowledge (60 simulated days: at most 2 marks open); migration 18 was amended
  in place so the improvement ledger also refuses a DELETE (a file of 18's first build gets the trigger at its next
  open); the strategy gate checks the named line of `genesis_final_uses.jsonl`; a restore carries the owner's revert.
  No default changed.
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

# ADR-056 (draft) The integration loop: how instinct, imagination, the workspace, monitoring and knowledge evolution hand over to each other, one arbitration record, and salience ordering the admitted. R

## Context

Cognitive Genesis directive §8 (Phase VI): "the system must not become four unrelated modules"; eight special
integration requirements; "prevent any subsystem from creating uncontrolled competing objectives". Wave 1 built the
pieces (ADR-051 to ADR-055) and shipped most of them off or display-only. Wave 2 lane L read the code at `3559069`
(Wave 1 merged) for each requirement, at the shipped defaults and with every Wave 1 setting on, and classified each
link: **REAL** (a consumer changes behaviour on it), **DISPLAY-ONLY** (recorded or shown, no consumer acts on it) or
**MISSING**.

## The link map (code at `3559069`; line numbers after this ADR's changes)

| # | Requirement | Producer → consumer (file:line) | Record that carries it | Setting that gates it | Shipped defaults | Everything on |
|---|---|---|---|---|---|---|
| 1 | curiosity → knowledge discovery | `curiosity.rank` (`webcortex/curiosity.py:252`) → `autonomy.candidates` (`webcortex/autonomy.py:557`) → `autonomy.admission` (`:688`) → the timer's launch (`webcortex/scheduler.py:557-597`) or the idle investigator's `orchestrator.submit` (`autonomy.py:852`) → research → `consolidate.run` → `web_knowledge` | `web_tasks` (origin `curiosity`), decision event `$.autonomy`, `web_feedback` | `autonomous` and the recorded enable (the owner's opt-in), `quiet`, the daily budgets; `curiosity_learning_progress` (off) damps the gain (`curiosity.py:265-277`) | **REAL** once the owner opts in (off on a new individual) | REAL |
| 2 | salience → attention | `salience.score` (`webcortex/salience.py:71`) attached to every candidate (`autonomy.py:622`); consumers: selective broadcast (`autonomy.after_task`, `autonomy.py:927-943` → `broadcast.offer`, `broadcast.py:113`); the order of the admitted candidates (**new**, `autonomy.py:769`, `salience.order`) | decision event `$.autonomy.salience`; `$.broadcast` | `salience_record` (on), `broadcast` (`off`), `salience_attention` (**new, off**) | **DISPLAY-ONLY** (recorded with every decision; changes nothing) | REAL: what reaches the owner (broadcast `on` holds back findings below the threshold) and, with `salience_attention`, which admitted candidate is taken first; before this ADR the candidate order was **MISSING** |
| 3a | imagination → planning | `instance/information.py` (the exact expected gain the what-ifs share, `:121`, `:161`) → `executive.decide` rule `information-v1` (`instance/executive.py:173-175`); display beside rules-v1 (`:276-284`) | `decisions` | `executive_rule` (`rules-v1`; `information-v1` opt-in), `decision_information` (on) | **DISPLAY-ONLY** | REAL in the core world (the rule chooses by expected gain); no web-side planning reads a scenario (by ruling: a scenario reaches curiosity, autonomy and the orchestrator only as a settled prediction error) |
| 3b | imagination → learning | `scenarios.tick` (`webcortex/scenarios.py:215`) imagines three futures per open prediction; `settle` (`:170`) fills the outcome slot from the real settlement; `errors` (`:188`) computes the error on read → consumer: `nsf imagine scenarios` only (`instance/cli_imagine.py:100-102`). The template itself learns from the **real** settlements (`decisive_rate`, `:56`; the forecast as `evolution.recalibrations` reads it) | `imagined` (kind simulation), its outcome slot | `imagine_scenarios` (off) | **DISPLAY-ONLY** (off) | DISPLAY-ONLY: the scenario's error reaches no learner. **MISSING by design**, assessed and not built (Decision 4) |
| 4 | the workspace → coordinated access | `broadcast.offer` (`broadcast.py:113`): the one function that decides which event reaches the owner's channels; `self_description.build` (`self_description.py:661`): one record for console, CLI, Pocket, voice and MCP; `presence.aggregator.derive` (`presence/aggregator.py:373`) | `$.broadcast` decision events; the record is built on read | `broadcast` (`off`: deliver as before; `log`: record; `on`: hold back) | **DISPLAY-ONLY** | REAL for delivery to the owner (`on`); no subsystem reads the self-description to decide (by design: it must not become a control path). Subsystems coordinate through the stored records and the one admission rule (row 8) |
| 5 | metacognition → reasoning quality | `monitoring.assess` (`webcortex/monitoring.py:313`) in `orchestrator._monitor` (`webcortex/orchestrator.py:2724`); `selective_answering` (`apply_note`, `:2729`), `verification_escalation` (`:2542`, `:1967-1977`), `adaptive_depth` (`:1630`) | `report["monitoring"]` | `answer_monitoring` (on), the other three off | **DISPLAY-ONLY** | REAL inside the task (escalation reads one more source; selective answering opens with a plain line; depth stops early). From a low-support verdict to a later re-check: **MISSING**, assessed and not built (Decision 4) |
| 6 | memory → continuity | premises and marks (`webcortex/premises.py:207`, called by `consolidate.py:1534`); the re-check ranks a marked item higher (`webcortex/recheck.py:92`); voice `resume_follow` (`voice/hub.py:307-328`); `self_description.recent` | `web_premises`, `web_rechecks`; `voice_sessions.context` | `premise_recheck` (on, records), `idle_recheck` (off), voice `resume_follow` (on) | **REAL** (the follow-up across a restart; every step reads the stored knowledge, hypotheses and predictions); marks DISPLAY-ONLY | REAL (marks order the re-check) |
| 7 | verified outcomes → future improvements | `predictions.check` (`webcortex/predictions.py:108`) → re-check marks (`premises.failed_prediction`, `:127`; `premises.py:182`), the error analysis's link chain (`:130`, `evolution.py:639`), the calibration record (`:142`) read by `autonomy.candidates` through `evolution.recalibrations` (`autonomy.py:529`); `curiosity.rank` raises the importance of an item whose prediction failed (`curiosity.py:260`, `:283`); salience's prediction discrepancy; `web_feedback` → futility (`curiosity.py:95`); strategy selection (`strategies.rank`, `webcortex/strategies.py:106`) behind its gate | `web_predictions`, `web_rechecks`, `web_claim_links`, `web_feedback`, `improvement_ledger` | `calibrated_forecasts` (on), `premise_recheck` (on), `strategy_selection` (`"gate"`: inactive) | **REAL** (futility; the calibration reading orders admitted candidates; the ×1.5 importance); marks DISPLAY-ONLY; strategy selection inactive | REAL (marks order the re-check; learning progress). Finding: the ×1.5 importance is keyed by the failed item's id, so it reaches the subject only when the item stayed live (contradicted); a superseded item's successor is freshly verified and not a candidate anyway, so nothing measurable is lost |
| 8 | executive control → arbitration of conflicts and permissions | `background.allowed` (`background.py:200`) at every entry point; `autonomy.admission` for the idle, timer and re-check paths; the daily caps and the resource gate (`scheduler._over_cap`, `:398`); owner work claimed first (`scheduler.claim_next`, `:626`); the timer's fixed order (re-check `:505-521`, due refreshes `:522-549`, curiosity `:550-597`) with the cooldown (`autonomy._recent`, `:442`) and one task per subject (`REFRESHED_THIS_TICK`) | the tick's returned report only (`skipped`); **new**: a decision event `$.arbitration` on the winner's task (`scheduler._arbitrated`, `:419`), for a real conflict only (Wave 2 lane Y: the subject was in the timer's curiosity pool and budget was left) | `quiet`, budgets, `tick_admission`; `arbitration_record` (**new, on: a record**) | **REAL** (the decision); the loser was recorded nowhere that persists (**MISSING** record before this ADR) | REAL, with the record |

## Decision

1. **The arbitration record** (`arbitration_record`, web, **on**: a record only). When the knowledge re-check or a due
   refresh takes a subject the timer's curiosity could have launched in the same pass, the tick's existing order
   decides, as before (one task per subject), and `scheduler._arbitrated` writes a decision event on the **winner's**
   task. "Could have launched" (corrected by Wave 2 lane Y after the benchmark-integrity review, H1): for the
   re-check, the subject is in that pass's curiosity pool (`autonomy.admission` returns it as `pool`), as the kind
   curiosity launches, and budget is left (`budget_left > 0`, no cap reached); for a due refresh, curiosity admitted
   the subject. As first built, the re-check record fired on any subject curiosity's cooldown refused after the
   re-check queued it, which in lane L's benchmark was never a subject curiosity could take (every one of the 14 and
   11 recorded arbitrations per world was of that kind); the decision event:
   `{"arbitration": {"slot": "one investigation per subject per timer pass", "winner": "recheck"|"refresh",
   "loser": "curiosity", "rule": ...}}`. It names mechanisms and the rule, never the subject (the subject is the
   winner task's own), so forgetting the task removes it (`governance`: a task's events go with it) and forgetting the
   subject leaves nothing in it; it never feeds the cooldown or the lineage (they read `$.autonomy.key`). It
   launches, admits and refuses nothing.
2. **Salience orders the attention admission already gave** (`salience_attention`, web, **off**). With it on (and
   the salience record on), `autonomy.admission` on the idle and timer paths takes the admitted candidates in
   salience order (`salience.order`: the owner's records first as before, then the higher salience, then the higher
   priority). The same candidates are admitted and refused with the same reasons; caps, budgets, cooldown, quiet and
   min_gain are read as before; the weights are the fixed ones of ADR-051 (nothing learned or rewarded). Each taken
   candidate records `priority_rank`, its place in admission's own order, and the decision's `admission.order`: when
   salience and priority disagree (§12 "conflicting cognitive subsystem outputs") one rule decides and the record
   keeps both, the losing candidate named by its place, never by its words. The re-check path keeps its own order.
   The priority order stands, recorded as `"priority"`, unless every admitted candidate carries a finite numeric
   salience score (lane Y, M2: a record that could not be made, `{"error": ...}`, was ordered as score 0 and recorded
   "by salience").
3. **The loop in the self-description** (display, no switch, like the rest of the record). `doing.cycle`: the latest
   background investigation (task id, origin, status, how it was admitted: path, rule, order, the salience score it
   carried, its priority rank), the latest arbitrations, and the predictions settled so far by outcome. Ids, numbers
   and fixed words only. `short()` adds one plain sentence ("My last background look was a re-check of something
   that may be out of date (completed); 12 predictions checked so far, 3 turned out wrong."). It answers "what am I
   doing" and "what evidence supports that decision".
4. **Assessed and not built**:
   - *(a) a scenario's or forecast's settled error feeding learning progress or the re-check marks.* The settled
     **real** outcome already reaches every learner the ruling allows: the marks (`predictions.py:127`), curiosity's
     importance (`curiosity.py:283`), salience (prediction discrepancy), the calibration reading (`autonomy.py:529`)
     and learning progress through the refresh's own feedback (`curiosity.py:137-165`). A scenario record's error is
     a function of that outcome, the prediction's own stated probability and the base rate of decisive settlements
     (`scenarios.py:83-86`), so routing it on would add no information about the subject and would make curiosity
     depend on the imagined store being switched on. Core forecasts (`nsf imagine forecast --keep`) are never
     compared with what followed (nothing calls `imagined.observe` for them): that compare step is missing, and no
     consumer would act on it (the graph gate already learns from held-out real readings).
   - *(c) a low-support monitoring verdict requesting verification through the re-check path.* The in-task path
     exists (`verification_escalation`, off: no metric change, +2 pages, ADR-054). A later re-check cannot be
     measured on any frozen world: the selective family's held-out split has no wrong answer, and no family stages a
     weak answer followed by a later re-verification.
   - *Broadcast ordering (the second half of b)*: owner questions are listed by each surface in its own order and
     Presence ranks live events (`presence.aggregator.arbitrate`); with broadcast `on`, salience already decides what
     is held back. Nothing further was built.
5. **No new background activity, no schema change.** Both links run inside existing registered entry points
   (`tick.*`, `worker.idle_investigation`); the benchmark runner is registered in `background.RUNNERS` and, as a
   display (`imagined.counts` only), in the quarantine allow-list.

## Measured

`docs/quality/evidence/genesis_integration.json` (`scripts/bench_wave2_integration.py run`). The family "integrated"
(`acceptance/cognition/integrated.py`) runs the real timer pass and idle investigator step by step on staged Web
Cortex individuals, worlds frozen in `integrated_splits.py` (committed alone first: `a625b96` on lane L's branch,
`72d7a59` on `genesis-wave2`; development seeds 61-63, held-out 601-604; the withheld family is not used).
Simulated worlds, offline, 0 model calls; the host's background gate is read as allowed in every configuration.
Configurations: baseline (every Wave 1 and 2 setting off), defaults, each link alone, integrated (re-check, marks,
learning progress, scenario records, salience attention, the arbitration record), integrated minus one link. Nothing
was chosen on held-out (salience attention has no parameter).

**Re-measured after the benchmark-integrity review (Wave 2 lane Y, branch `gen2-y`; the numbers below).** The first
measurement had three harness defects and one product defect: one random stream drew the world's truth changes and
the research outcomes, so configurations that investigated other subjects met other worlds (H3; the draw is now
decoupled: truth changes keyed by world, seed and step, outcomes by world, seed, step and subject; the worlds file is
unchanged); the "integrated" mechanism of the retention and adversarial families forced every Wave 1 setting off
inside each case (H2; it now forces only the links' settings); the staging ran at the shipped defaults (L1); and the
arbitration record fired on subjects curiosity could not have taken (H1, Decision 1). Held-out was re-run because the
harness changed; the worlds and seeds did not change; no default changed. Check: with the old one-stream draw and the
L1 and H1 fixes in place, every held-out per-seed value of baseline, defaults, the re-check alone and integrated
equals the first measurement except the arbitration count (14 → 0), so every move below comes from the draw. The
first figures are kept in the evidence file under `before_review`.

- **Knowledge correctness under change** (share of item subjects whose live knowledge states the world's truth, mean
  over steps). Held-out (2 worlds x 4 seeds): baseline 0.718 (per world sd 0.047 and 0.071), integrated 0.726; paired
  difference +0.008 (sd 0.015; 6 seeds higher, 2 lower). The re-check alone 0.726 (paired +0.008, sd 0.015; 6
  higher, 2 lower); every other link alone equals the baseline on every seed. Against the best single link, chosen
  on development (the re-check): +0.0001 (sd 0.0009; 1 higher, 1 lower, 6 equal). Ablations: without the re-check
  0.719; without any other one link 0.726. Final step 0.634 against 0.632; stale facts caught per investigation 0.227
  against 0.232. Development (2 x 3): baseline 0.789 (sd 0.028 and 0.068), integrated 0.797 (paired +0.008, sd 0.011;
  5 higher, 1 lower), the re-check alone 0.796, salience attention alone 0.791 (+0.002; 1 higher, 5 equal). **The
  integrated configuration shows no gain beyond the baseline's spread and none over the re-check alone; the small
  positive paired difference is the re-check's.** With these seeds the rule (a gain larger than the baseline's
  per-world sd) cannot see a held-out gain below 0.047 / 0.071; "no gain beyond the spread" does not exclude a
  smaller one (the paired noise is 0.015). Before the review: baseline 0.713, integrated 0.710, paired −0.003 (sd
  0.028; 4 higher, 4 lower), not paired on the same worlds.
- **What each link did** (held-out, per run; `splits.held_out.activity`). A link that acted but never changed what
  was investigated or held is **not exercised by this world**, not "no evidence of gain":
  - `salience_attention`: the timer's curiosity pool held at most one subject in every run (so the timer had nothing
    to order); on the idle path 14 admissions per run had two or more candidates, salience ordered them, and it
    never took another first candidate than the priority order (0 times; launches identical to the baseline's).
    Not exercised by this world. With the other links on it took another first candidate 8.5 times per run, and
    removing it moves correctness by 0.0001.
  - `learning_progress`: 287 factors other than 1 per run; it changed which noise subject the idle path looked at in
    12 of 14 steps but never what was held and not the noise share. Not exercised for correctness by this world.
  - marks (`premise_recheck` alone): no mark opened and nothing reads one without the re-check (no consumer here).
  - scenario records: 76.5 per run, quarantined by design: no consumer; no launch changed.
  - the arbitration record: 0 real conflicts, so 0 records, in every configuration of both splits.
  - the re-check: changed what was investigated in 11 of 14 steps and what was held in 9.
- **Information value** (share of investigations on noise subjects that never settle; the idle path's noisy-TV
  trap): held-out 0.415 baseline, 0.408 integrated, 0.415 salience attention alone; development 0.420, 0.410, 0.401.
- **Anticipation / prospective validity** (the scenario records' three-way Brier once settled; reference: three
  equal futures, 2/3): **not better than three equal futures**: held-out 0.681 (scenarios alone; per seed
  0.555-0.801) and 0.689 (integrated; per seed 0.574-0.824); development 0.640 and 0.630. Scenario records stay off.
- **Arbitration and conflicts**: 0 timer passes with two tasks on one subject and 0 investigations of a subject
  already covered in the same step, in every configuration; 0 arbitrations recorded (the first measurement's 11 and
  14 per world were all spurious, H1). 0 belief writes in every run.
- **Self-description** (four probes on the loop against the world's bookkeeping, **plumbing checks**): 1 of 4 before
  this ADR, 4 of 4 after, in every configuration. The self-description family: 30 of 30 under the current key, 29 of
  30 under the original, 1 surface.
- **Retention**: 4 of 4 scenarios, every offline metric retained, baseline and integrated, both splits; no value
  differs between the plain run, the first forcing and the corrected one. **Adversarial** (on `genesis-wave2`: 28
  scored cases, original 13, Wave 1 4, restored 1, Wave 2 lane U 10): 27 pass, baseline and integrated alike, both
  splits; the one failure is lane U's measured gap (`reflection_bounded_when_everything_asks`: 21 lines against
  MAX_LINES 20). Under the first forcing "integrated" scored 28 of 28, but two passes were vacuous: with reflection
  forced off, `recheck_and_reflection_stay_bounded` ran 0 reflections instead of 2 and
  `reflection_bounded_when_everything_asks` ran none, which masked the gap. The first record's "18 cases pass" was
  lane L's branch, before lane U's cases.
- **The shipped defaults**: the offline research benchmark's digest is `68731d72…` (unchanged); the "defaults"
  configuration equals the baseline on every knowledge metric (it writes premise rows, `premise_recheck` being on).
- **Cost**: wall seconds per world run, held-out: baseline 0.318, integrated 0.551 (+73 %; the re-check alone 0.463;
  the arbitration record alone 0.333); development 0.219 and 0.357. The whole command's peak resident set 369.4 MiB
  (the retention family's offline corpus; 48.0 MiB before the first run). 0 model calls, no connection outside
  loopback.

## Alternatives rejected

- Feeding a scenario record's error, or its content, to curiosity, autonomy or the orchestrator (Decision 4): no
  information the real settlement does not carry, and the ruling allows only the settled error.
- Ordering by salience inside admission (letting salience admit or refuse): a new objective with its own priority.
- Recording the losing subject's key or statement in the arbitration event: a forgotten subject would stay in
  another task's record.
- A new "integration" background activity or daemon: every hand-over already runs inside a registered entry point.

## Consequences

- Defaults: `arbitration_record` on (a record, now written only for a real conflict); `salience_attention` off: it
  is not exercised by this world (the timer's curiosity pool never held more than one subject, and on the idle path
  it never took another first candidate than the priority order, so it changed nothing investigated); every Wave 1
  default unchanged. The integrated configuration is not recommended: it shows no gain beyond the spread and none
  over the re-check alone, and costs about 73 % more wall time per pass, mostly the re-check's.
- Changed code: `scheduler.tick` (the two arbitration points, `_arbitrated`), `autonomy.admission` and
  `decision_inputs` (the salience order and `priority_rank`; after the review also the tick's `pool` in the
  admission result), `salience.attention`/`order` (after the review: the priority order unless every score is
  finite), `self_description._cycle` and `short`, `WebConfig` and its validation (two booleans). No schema change,
  no new background activity.
- Tests: `tests/webcortex/test_integration_loop.py` (the staged end-to-end cycle on one individual, each hand-over
  and the forbidden paths), `tests/webcortex/test_integration_arbitration.py` (salience order, conflicting priorities
  and outputs, competing objectives, quiet and a spent budget; after the review: a subject outside the pool, no
  budget left and a real conflict, for both arbitration points; the salience order's fallback and permutation
  stability), `tests/acceptance/test_genesis_integration_bench.py` (after the review: the decoupled draw, the
  settings before the staging, the forcing of the links only, the activity counts); the quarantine allow-list gains
  the benchmark as a display; `background.RUNNERS` gains the benchmark runner.
- Commits: lane L's branch hashes and their `genesis-wave2` commits: `a625b96` → `72d7a59` (worlds), `440c705` →
  `a7981e0` (links), `5463dfc` → `f97411e` (family and script), `7218e0f` → `828ca8e` (this ADR, evidence).
- Not verified: anything on the open web, a live model, a real worker process running the loop for days, or the
  real host's background gate during the loop.

# ADR-058 (draft) Host evidence without behaviour change: predictive regulation in shadow, and the readiness of real outcomes. R

## Context

Wave 3 lane H (FINAL_EVALUATION §6, directions 2 and 3). ADR-051's forecast avoided most memory breaches on a
SIMULATED plant with declared constants and ships off; turning it on is the owner's decision, with no host evidence.
Calibration (ADR-054), scenario records (ADR-052), forecast recalibration (ADR-048) and strategy selection (ADR-055)
all wait for settled real outcomes ("no fitted map has 30 items in 3 blocks"), and nothing told the owner how far
each is. Both additions here record or read; neither changes what the individual does.

## Decision

1. **`predictive_regulation = "shadow"`** (a third value of the existing setting; default stays `false`;
   `nsf config --predictive-regulation-shadow`). `policy.background_gate` makes its decision exactly as with the
   setting off and only then, when called on a decision path (`record=True`; `internal_state.snapshot` and
   `resources.status` pass `False`), calls `resources/shadow.observe`, whose result is not read and which never
   raises. It computes the forecast as the gate would with the setting on (the same history read and `forecast.assess`
   call), at most once per history sample per process (the history file's state is the key: on an unchanged history
   the added cost is one stat call), and appends one line to `<db>.resources/forecast-shadow.jsonl`: the time, the
   sample's time, would hold / would allow / no forecast, the reason word (`memory`, `voice`), the horizon, the
   margin, and what the real gate did (`allowed`, `audio`, `memory`); a failure is a `{"failed": kind}` line and a
   WARNING naming the kind only. Numbers and fixed words only; 0640 in a shared folder; a link, FIFO or another
   account's file is refused; a file over 1 MiB takes no line; the gate never waits for the lock.
2. **Settlement inside the existing sampler.** `internal_state.record` (the worker's heartbeat, every 30 s) calls
   `shadow.settle_quietly`, which at most every 300 s settles each verdict whose horizon has passed against the
   history samples: a breach is a sample under the critical line (`monitor.thresholds`); a would-hold with a breach
   in `[t, t + horizon]` is a hit, without one a false alarm; a would-allow with one is a miss; a breach episode was
   preceded when a would-hold at `t` had `t <= start <= t + horizon`, by `start - earliest t`; background starts after
   a would-hold the real gate allowed are the work holding would have deferred (a proxy: net increases of running
   tasks between samples). Counts go to a daily roll-up, `forecast-shadow-days.json` (120 days); the verdict file
   keeps only what is unsettled. A pass that did not finish is not counted twice (`settled_until`; one verdict per
   sample, the first). No new background activity: `background.py` is unchanged.
3. **`nsf internal-state --shadow-report`**: the roll-up plus the verdicts due but not yet rolled up (settled in
   memory; nothing written), with the days and breach episodes the numbers rest on, and "Not enough data" below
   7 days or 5 episodes (`shadow.MIN_DAYS`, `MIN_EPISODES`).
4. **`nsf readiness`** (`neuroscifi/readiness.py`), read-only (`mode=ro`, `query_only`): settled predictions by
   kind (`evolution._KIND_SQL`) and month, open, and settled-but-forgotten; answers (completed or partial tasks)
   with a later verdict (a prediction on knowledge the task produced, settled confirmed or error: no record pairs an
   answer with a verdict on the answer itself); hypotheses by status; re-checks that found a change, none,
   undecided, closed by another path, dropped, open; scenario records settled (through `scenarios.errors`, the
   mechanism's own report: this module never touches the imagined store). Each learner with its minimum read
   from its module at run time (`evolution.RECALIBRATION_MINIMUM` per kind, `monitoring.MIN_ITEMS` and
   `MIN_BLOCKS` with the shipped maps' items and blocks), what is missing, the rate over the last 90 days (or since
   the first) and the earliest date at that rate ("unknown" at rate zero, "met"); scenario records, hypothesis
   settlement and strategy selection have no count minimum in the code and say so. The self-description's owner
   scope (not agent, not family) carries the per-learner summary as `uncertain.outcomes_readiness`.

## Evidence (`docs/quality/evidence/genesis_h.json`; simulated plant for the check, real code throughout)

- **Shadow == off.** The anticipation family's development and held-out traces (8 + 8 traces x 3 seeds = 48 runs of
  240 ticks; never the withheld family) through the product's wiring (`acceptance/cognition/shadow_check.py`: the
  sampler, the history file, the real gate): the gate's decisions with shadow equal those with the setting off on
  every tick of every run, and so does the plant's trace; 0 decisions changed, while the forecast would have held on
  279 (development) and 508 (held-out) ticks. The offline research benchmark with every individual reading
  `"shadow"` gives `68731d72…`, Wave 0's digest, but that benchmark never calls the background gate (0 calls,
  counted), so it shows only that the setting's value changes nothing else; the staged traces are the proof.
- **Settlement against known truth.** On every run the report's numbers equal `scoring.breach_report` (the family's
  own scoring) on the same trace: (a) incremental (the sampler settling every 300 s of the staged clock, the
  critical line, 180 s horizon) and (b) lazy with the family's breach line (2 GiB) and horizon (20 ticks): hits,
  false alarms, misses, quiet allows, episodes, preceded episodes, mean lead; 48 of 48 runs without a mismatch for
  each. Pooled, not a claim, (a): development 219 hits, 60 false alarms, 21 misses, 38 of 38 episodes preceded;
  held-out 306, 198, 19, 37 of 37. These say the arithmetic and the wiring are right; they say nothing of the host.
- **Cost** (aarch64 host, commit `87a3a7a`). The gate on an unchanged history (the worker's usual case: many calls
  per 30 s sample): 3.7 µs off, 8.7–9.0 µs in shadow (means of 500 calls, 16 staged individuals). On a call right
  after a new sample (the forecast computed, a line appended; at most once per sample per process): mean 1,041 →
  1,751 µs (development) and 795 → 1,320 µs (held-out), p95 up to 3.3 ms; the "off" figure includes re-reading the
  setting because the staged file changes every tick. `nsf readiness` at 10,000 knowledge items, 500,000 events,
  20,000 predictions, 10,000 tasks, 1,000 scenario records: median 36 ms (5 runs, commit `a8de711`).
- **Fault injection** (`tests/uat/test_uat_shadow_faults.py`): a forecast that raises, times out or returns garbage,
  a failing history read, a FIFO in place of the record, a roll-up that cannot be written: the gate's decisions equal
  those with the setting off on every tick, a WARNING names the kind (never the message), the report counts the
  failed verdicts.

## Consequences

- Defaults unchanged: `predictive_regulation` stays `false`; the readiness report is a command. A week of shadow on
  the host is the owner's to run (`docs/operations/RUNBOOK.md`, "Gathering evidence on your own host").
- The shadow files are operational telemetry like the internal-state history (ADR-051): numbers only, not in
  checkpoints, not something `forget` must reach; deleting them loses only the evidence. Nothing reads them but the
  report: no learner, no gate, no evidence or knowledge.
- Found on the way, not changed: `webcortex.strategies.outcomes` makes two unindexed look-ups per task; at 10,000
  knowledge items and 20,000 predictions it takes seconds (the readiness report counts the same with one pass per
  table, and a test checks both agree). An index would need a migration; left to the owner of ADR-055.
- No schema change, no new dependency, no new background activity.

# ADR-059 (draft) Front-door adjustment in the linear causal engine: a decline becomes a labelled answer only where the criterion holds. R

## Context

ADR-052's engine (`neuroscifi/causal.py`, `nsf imagine scm`) answers a graph question (a graph, the observational
means and covariance, the nodes a hidden common cause touches) by back-door adjustment, and declines it when a
hidden common cause touches the cause. The counterfactual family's "confounded" questions (hidden U -> X0 and U -> Y
around the chain X0 -> X1 -> ... -> Y) are identified by the front-door criterion through X1, so 4 of the 32
answerable questions per held-out seed were declined: the pooled held-out figure was 0.875 (FINAL_EVALUATION §6,
direction 6). What the engine is handed for them: the observed graph `{X0: [], X1: [X0], ...}`,
`hidden_common_cause: [X0, Y]`, `observational_mean` and `observational_covariance` over the observed nodes (exact,
from the equations), `do: {X0: x}` and `ask: Y`. The truth is E[Y | do(X0 = x)] from the structural equations.

## Decision

1. **Where it is tried.** Only where the back-door decline is "a hidden common cause acts on the cause". Every other
   path of the engine is unchanged.
2. **The criterion (Pearl 1995), checked, never assumed.** The mediators M are the cause's children that are
   ancestors of the outcome, so M intercepts every directed path from X to Y unless X -> Y is an edge (declined:
   "bypasses every mediator"). With one hidden node acting on every node named in `hidden_common_cause` (several
   named causes read as one shared cause, which can only add paths, so only add declines), the two other conditions
   are d-separations in the moral ancestral graph: X and M separated once the edges out of X are removed (no
   unblocked back-door path from X to M), and M and Y separated given X once the edges out of M are removed (X blocks
   every back-door path from M to Y). Declined, each with its reason: a mediator without data (unobserved), no
   directed path, a cycle, cause = outcome, more than 64 nodes, a singular covariance of M and X. No general
   do-calculus, no conditional front door, no instrumental variables.
3. **The estimate.** Linear: E[Y | do(X = x)] = E[Y] + b(Y ~ M | X) . b(M ~ X) (x - E[X]), from the given moments
   (`means`, or `observational_mean` as the family names them; both and different is an error). Its uncertainty
   depends on where the moments come from (Decision 6).
4. **Every answer says which criterion identified it**: `identified_by` is `declared_model`, `back_door` or
   `front_door` (with `mediators`), None on a decline. An answer is labelled "imagined, not evidence", kind
   `simulation`, as before; nothing is stored. Engine version 2.
5. **Setting.** `causal.answer(query, front_door=True)`; `nsf imagine scm FILE --no-front-door` restores the old
   decline (with a reason that says it is switched off). Not a stored setting: `nsf imagine scm` reads no individual
   (ADR-052's other imagine commands are per-individual switches; this one has no individual to hold it). The
   benchmark's imagination answerer gains the ablation component `front_door`.
6. **Where the moments come from (the review follow-up).** What the product path receives: the engine is reached only
   by `nsf imagine scm FILE` (a JSON question the owner writes) and the library; `instance/imagine.py` does not call
   it. ADR-052's format for a graph question names a graph, the means and covariance and the hidden common cause;
   nothing in it said whether those moments are a declared population's or estimates from data, and no field carried
   a sample size, so the engine could not know (the counterfactual family's are exact: computed from its equations).
   A graph question may now say: `"moments": "population"` (exact) or `"samples": n` (estimated from n independent
   draws, covariance with the n - 1 divisor). Then:
   - **population**: a point value, exact for those moments (as before).
   - **samples n**: the answer carries the standard error of the estimate (delta method under the engine's
     linear-Gaussian assumption: Var(mean) = S/n, Cov(S_ij, S_kl) = (S_ik S_jl + S_il S_jk)/(n - 1), gradient by
     central differences; validated by coverage below and against repeated sampling in the tests) and states its
     95 % interval. It is declined "not enough data to answer" when the interval's half-width exceeds
     `USEFUL` = 0.1 x the outcome's sd (the benchmark's tolerance, so a given answer's interval stays within it),
     when n <= variables + 1, or with more than 16 variables. `USEFUL` was chosen on the development worlds and
     committed (`021a3d7`) before any held-out run of the intervals; the held-out point errors from 500 draws had
     been seen before (first evidence run), the intervals had not.
   - **not stated, front door**: declined ("does not say whether the means and covariance are the population's ...").
     By the standard that a confident wrong answer is worse than a decline: in the first evidence run, estimates
     taken as exact gave 20 false answers in 200 held-out worlds at 500 draws, and the engine cannot tell estimates
     from population values; stating the origin is one field. The family's answerer states `population` for graph
     questions, as its interface computes them exactly (`acceptance/cognition/imagination.py`).
   - **not stated, back door**: answered as before (value and sd unchanged; it is the engine's existing behaviour),
     with `moments: {origin: unstated}` and an uncertainty sentence saying the moments were taken as exact. The
     measurements below show the same limit as the front door's; whether to decline these too changes existing
     answers and is left to the main session.

**Default: on.** The rule written before the mechanism (`frontdoor_splits.py`, committed alone first) held on every
point: 0 false answers and every answer world exact on the property worlds with exact moments, both splits; on the
held-out counterfactual split every front-door question within tolerance and every other leaf identical with the
front door on and off; adversarial not regressing; retention unchanged. With estimated moments (the follow-up): a
front-door answer is given only with its sample size, its interval covers the truth at 0.93 to 0.95 on held-out
worlds (near the stated 0.95), too little data is declined, and an unstated origin is declined; no trap world is
answered in any regime.

## Evidence (`docs/quality/evidence/genesis_frontdoor.json`, `scripts/genesis_frontdoor.py`, measured at `14d8d55`)

Counterfactual family, each split's three seeds (development 31-33, held-out 1031-1033), answered within 0.1 x scale:

| | front-door group (4 per seed) | pooled, not a claim | handed equations | belief what-if | learned graph |
|---|---|---|---|---|---|
| held-out, today's answerer (baseline) | 0 | 0.063 | 0 | 0 | 0.222 |
| held-out, `zero` reference | 0 (declines) | 0.219 | 0.083 | 0.667 | 0.407 |
| held-out, front door off (= start commit) | 0 (4 declined per seed) | 0.875 | 1.000 | 1.000 | 1.000 |
| held-out, front door on | **1.000** (12 of 12; 1.000 at 0.05) | 1.000 | 1.000 | 1.000 | 1.000 |
| development, front door off | 0 | 0.844 | 1.000 | 1.000 | 0.889 |
| development, front door on | 1.000 (12 of 12) | 0.969 | 1.000 | 1.000 | 0.889 |

- Largest relative error on the front-door questions: below 1e-12 (the moments are exact). Interval score: 0 to
  that precision, because a point answer's interval has width 0 and its score is 40 x the error; it says nothing
  about calibration. The family has no spread here (deterministic per seed; held-out sd 0).
- Leaf identity: the ablation (front door off) against the start commit's engine (`git show 9ac8943:...causal.py`
  through today's answerer): 0 of 419 (development) and 0 of 438 (held-out) leaves differ. Front door on against off,
  outside the front-door group, the `scm:confounded` kind and the pooled figures: 0 of 257 and 0 of 276 differ.
- Property worlds (12 kinds, 5 where the criterion holds, 7 traps; development 20 seeds, held-out 40):

  Exact moments (`moments: population`): development 100 of 100 answer worlds within 1e-9, 0 of 140 trap worlds
  answered, **0 false answers**; held-out 200 of 200 within 1e-9, 0 of 280 traps answered, **0 false answers**. The
  traps are real: a front-door formula through M would have missed by more than 0.1 x sd(Y) on 20 to 33 of the 40
  held-out worlds of each trap kind with data for M (median error 0.10 to 0.32 x sd(Y), up to 2.6).

  Estimated moments, **held-out** answer worlds (200 front door, 160 back door; development in the evidence file).
  "Coverage" is the share of given answers whose stated 95 % interval holds the truth; in this regime a FALSE ANSWER
  is the truth outside the stated interval (a calibrated 95 % interval has about 5 % of them by construction), and
  "> 0.1 sd" counts given answers farther than 0.1 x sd(Y) from the truth. No trap world was answered in any regime.

  | criterion, draws n | with `samples: n`: answered / declined "not enough data" | coverage (false answers) | > 0.1 sd | coverage with the bound lifted (every world) | not stated | estimates declared `population` (> 0.1 sd) |
  |---|---|---|---|---|---|---|
  | front door, 100 | 11 / 189 | 1.000 (0) | 0 | 0.930 | 0 of 200 answered | 84 of 200 |
  | front door, 500 | 104 / 96 | 0.933 (7) | 1 (0.103) | 0.935 | 0 answered | 20 of 200 |
  | front door, 5,000 | 199 / 1 | 0.945 (11) | 0 | 0.945 | 0 answered | 0 |
  | front door, 50,000 | 200 / 0 | 0.940 (12) | 0 | 0.940 | 0 answered | 0 |
  | back door, 100 | 8 / 152 | 0.750 (2) | 1 (0.102) | 0.956 | 160 answered, **49 > 0.1 sd** | 49 of 160 |
  | back door, 500 | 106 / 54 | 0.934 (7) | 2 (up to 0.157) | 0.938 | 160 answered, **16 > 0.1 sd** | 16 of 160 |
  | back door, 5,000 | 160 / 0 | 0.944 (9) | 0 | 0.944 | 160 answered, 0 | 0 |
  | back door, 50,000 | 160 / 0 | 0.950 (8) | 0 | 0.950 | 160 answered, 0 | 0 |

  Development, with the bound lifted, coverage 0.94 to 0.98 (front door) and 0.95 to 1.00 (back door). The
  back-door "not stated" column is a **pre-existing limit** of ADR-052's engine: a point answer from estimated
  moments taken as exact misses by more than 0.1 x sd(Y) in 49 of 160 held-out worlds at 100 draws and 16 of 160 at
  500 (33 and 5 of 80 on development). Its value is unchanged here; the caveat is added.
- Random graphs (`tests/test_causal_front_door.py`): 6,000 random DAGs of 3 to 7 observed nodes with a hidden cause
  on X, Y and at random others, some nodes without data: 2,234 answered, 0 false answers (exact moments); the same
  graphs produce false answers when the separation checks or the direct-edge check are removed (the test's power).
- Adversarial: 33 of 33 with the front door on and off (28 before, plus 5 lane C cases whose expectations were
  committed before the cases and the cases before the mechanism; on the start commit's engine they passed 25 of 25
  worlds by the back-door decline). Retention (full, against the recorded baseline): every scenario passes, every
  metric retained.
- Cost: a front-door answer about 0.1 ms (median; largest 0.23 ms); the family's wall time 0.42 s against 0.43 s off
  (held-out). No model call, no network, no storage.

## What this does not show

The front-door group is exact arithmetic on a declared graph and given population moments, like the handed-equations
group: it says nothing about real-world causal questions, about a graph declared wrongly (a hidden cause not named is
an unchecked assumption, stated in every answer), or about data that are not independent draws of a multivariate
normal (the interval's assumption). The interval is slightly narrow on the front door at small n (held-out 0.930 at
100 draws, 0.935 at 500, bound lifted). The withheld counterfactual family has front-door questions; it has not been
run (the main session's single look).

**Disclosure.** Early in the lane, a broad `grep -rn` over `src` for "causal" matched
`src/neuroscifi/acceptance/cognition/withheld.py` and printed two of its lines: a docstring line (line 6: "noise than signal,
causal structures with longer paths and actions on hidden routes, and staged states of other") and line 54, the start of the withheld
counterfactual `scm` spec: `"scm": ({"structure": "chain", "nodes": 5, "queries": 8}, {"structure": "confounded",
"nodes": 5,` (cut off there). Nothing was tuned on it: the mechanism is a general criterion check with no parameter
for the number of nodes, the worlds, `USEFUL` and the verdict rule were written from the development and held-out
splits and the brief, and every later search excluded `*withheld*`.

## Rejected

- A stored per-individual setting: `nsf imagine scm` reads no individual.
- Answering a front-door question with unstated moments under a caveat: the engine cannot tell estimates from
  population values, and estimates taken as exact were confidently wrong (above); a decline costs one field.
- Declining back-door questions with unstated moments: it changes existing answers; left to the main session.
- A bootstrap for the interval: the delta method is closed-form, cheap and validated by coverage.
- Searching every subset of observed nodes for a front-door set, conditional front doors, general do-calculus:
  larger than the smallest sound extension; a decline there is "not identified by this engine".

## Consequences

- Changed: `causal.py` (version 2, `answer(front_door=)`, `identified_by`), `cli_imagine.py` (`--front-door`),
  `acceptance/cognition/imagination.py` (component `front_door`), `acceptance/cognition/adversarial.py` (5 cases,
  origin `added_wave3_c`). New: `acceptance/cognition/frontdoor_splits.py`, `acceptance/cognition/backdoor_splits.py`
  (committed alone before the intervals), `scripts/genesis_frontdoor.py`, `tests/test_causal_front_door.py`. The
  question format gains `moments` and `samples`; answers to graph questions gain `moments` (origin, samples). No
  schema change, no migration, no background activity, no dependency.
- The counterfactual family's recorded imagination figures move only in the front-door group and the pooled figures
  (held-out 0.875 -> 1.000, development 0.844 -> 0.969); earlier evidence files are not rewritten.

# ADR-060 The order of the timer's knowledge re-check: what the loop's shortfall is made of, and an order by observed change that ships off

Status: Wave 3 lane R, branch `gen3-r` from `a0717c1`. Measured on `320ae8e`. Evidence
`docs/quality/evidence/genesis_recheck_order.json`. Simulated worlds through the real modules, offline, no model.

## Context

Wave 2 measured that the timer's loop recovers about half of what a blind same-budget round-robin recovers over
never refreshing (RECURSIVE_KNOWLEDGE_ASSESSMENT §3), and that the idle re-check, which takes the oldest items, caught
a change in 7 of 72 looks against 19 of 79 for the due order. Step 0 of Wave 3 confirmed the loop falling behind the
blind schedule on the withheld worlds. FINAL_EVALUATION §6 direction 4: re-check what is likely to have changed, not
what is oldest. The question: with the same budget, can the order in which the timer refreshes and re-checks be made
at least as good as a blind schedule, and better where change rates differ?

## Diagnosis (development only; `cycles.decompose`)

The harness now records every autonomous look (what the newest held item was, what the look's pages said by
majority, what it changed). `cycles.decompose` splits the gap between a reference and a run, in the units of the
primary metric (`correct`, mean over cycles), into the subject-cycles where the reference holds the true version
and the run does not: (a) **uncovered**, the run holds nothing (the owner's research gave the subject no knowledge);
**wrong source**, the newest item is a wire-story or own-output version; (d) **found, not revised**, a look since the
item went stale read the true version and the item stayed stale (contested; lane S's subject); **looked,
unreadable**, looks since then whose pages did not show the true version; (c) **not looked**, no look since it went
stale; minus where the run is ahead. The parts sum to the gap (tested). (b), looks spent on subjects that had not
changed, is counted per look.

Development, today's loop (`baseline`) against the blind round-robin (majority read), mean of 3 seeds:

| World | Gap | (a) uncovered | (d) found, not revised | unreadable | (c) not looked | loop ahead | Looks used / allowance | (b) looks on unchanged subjects: loop / blind |
|---|---|---|---|---|---|---|---|---|
| dev-steady (lane F) | 0.194 | 0.114 | 0.035 | 0.006 | 0.050 | −0.011 | 21.0 / 24 | 71 % / 69 % |
| dev-churn (lane F) | 0.132 | 0.074 | 0.032 | 0.008 | 0.033 | −0.015 | 27.7 / 36 | 46 % / 56 % |
| dev-rate-mixed | 0.166 | 0.119 | 0.031 | 0.003 | 0.030 | −0.017 | 25.7 / 28 | 66 % / 67 % |
| dev-rate-uniform | 0.185 | 0.097 | 0.061 | 0.012 | 0.045 | −0.030 | 25.7 / 28 | 42 % / 48 % |
| dev-rate-shift | 0.158 | 0.133 | 0.010 | 0.019 | 0.029 | −0.033 | 23.7 / 29 | 49 % / 62 % |

Pooled over the five worlds (pooled, not a claim): uncovered 64 % of the gap, found-not-revised 20 %, not looked 22 %,
unreadable 6 %, the loop ahead −13 %; wrong source 0. Against the oracle the "not looked" part is larger (0.116 and
0.151 of gaps 0.321 and 0.338 in lane F's worlds), because the oracle looks only at subjects that changed.

What it decides. (a) dominates and is out of any order's reach: its subjects (an owner look that found one origin
only, the wrong story alone, or a contested page set) are not knowledge, and the timer admits only knowledge items
(`autonomy.admission`, "propose it instead"); the owner or the worker's idle investigation (not run here) could
reach them. (d) is lane S's. The loop's own looks are not more wasteful than the blind schedule's ((b): 42–71 % on
unchanged subjects against 48–69 %); what an order can reach is (c), 0.03–0.05 against the blind schedule, partly
the 2–8 looks of the allowance the loop leaves unused per run. The rule cannot see a gain below the baseline's sd
(0.03–0.10 on these seeds), so the expected effect of any order is at or below what the rule can see: build little.

## Decision

- **WebConfig.recheck_order**, default `"staleness"`: lane K's value (importance × staleness × futility),
  unchanged. Otherwise a `+`-joined set of: `rate` (the chance the item changed since it was last verified,
  1 − exp(−rate × TTLs elapsed), with the rate from its subject's own verified changes, the rows of its key a newer
  version superseded, per TTL of exposure, and a prior of one change per TTL weighing as one TTL); `seen` (that chance
  is 1 for an item marked to re-check, marked stale, or one a newer version was seen for); `rotation` (an item not
  looked at for one TTL or more, by a refresh, a re-check, a curiosity task of its subject or a verification, goes
  first, the longest-waiting first; alone, a blind rotation by last look).
- It only re-orders the re-check's pool (`recheck.rank`'s `value`): the same items (those whose default value is
  above 0), the same `info_gain`, `p_current` and signals, the same admission (`autonomy.admission(path="recheck")`),
  the same per-pass and daily shares (`recheck_per_tick`, `recheck_daily`) and the daily autonomous allowance. It
  reads only knowledge rows, refresh tasks and autonomous decision events; never an imagined record, never a model.
  No new background activity, no schema change. It needs `idle_recheck` on to act.
- Each pick's decision event records the order and its inputs (`autonomy.recheck.order`: rule, chance, changes seen,
  rate, TTLs since verified and waited, overdue, newer seen); the self-description's "to re-check" names the order
  when it is not the default.
- **Default: unchanged (`staleness`; `idle_recheck` stays off).** No order showed a held-out gain (below).

## Measured (held-out, once, on `320ae8e`)

Worlds: `cycles_rate_splits.py` (committed alone first, `3f75ed8`; seeds 2701–2704: subjects with learnable rate
classes, one rate for all, classes redrawn at one cycle with a per-cycle allowance) and lane F's held-out (disclosed:
looked at by three lanes and two reviews before). `/all`: `recheck_per_tick` and `recheck_daily` 100 (the owner's
bounds), so the re-check may take the whole daily allowance, which still caps every pass. Mean `correct` over 4
seeds; paired gain against `baseline` (seeds better of 4); the rule's smallest visible gain is the baseline's sd.

| | ho-rate-mixed | ho-rate-uniform | ho-rate-shift | ho-sparse (lane F) | ho-busy (lane F) |
|---|---|---|---|---|---|
| baseline sd (the smallest visible gain) | 0.089 | 0.071 | 0.080 | 0.105 | 0.057 |
| reference: snapshot / blind round-robin / oracle | 0.651 / 0.724 / 0.873 | 0.573 / 0.724 / 0.949 | 0.712 / 0.797 / 0.940 | 0.670 / 0.786 / 0.957 | 0.574 / 0.708 / 0.928 |
| no loop | 0.539 | 0.503 | 0.614 | 0.574 | 0.497 |
| **baseline** (today's loop) | **0.587** | **0.617** | **0.646** | **0.631** | **0.571** |
| lane K's order (`recheck:staleness`) | −0.023 (0) | +0.007 (2) | −0.002 (1) | −0.019 (1) | −0.004 (1) |
| blind rotation, whole allowance (`recheck:rotation/all`) | −0.006 (0) | −0.007 (1) | +0.001 (2) | −0.007 (1) | +0.022 (3) |
| **the combination** (`recheck:rate+seen+rotation/all`) | −0.003 (1) | −0.004 (1) | +0.004 (2) | −0.011 (1) | +0.005 (3) |
| without rate (`seen+rotation/all`) | −0.003 (1) | −0.002 (2) | +0.000 (2) | −0.011 (1) | +0.001 (3) |
| without seen (`rate+rotation/all`) | −0.007 (0) | +0.009 (2) | +0.007 (3) | −0.011 (1) | +0.014 (3) |
| without rotation (`rate+seen/all`) | −0.013 (2) | −0.002 (2) | −0.002 (2) | −0.023 (1) | −0.001 (2) |
| without the whole allowance (`rate+seen+rotation`) | −0.008 (1) | −0.009 (2) | +0.001 (2) | −0.002 (1) | +0.004 (2) |
| the combination against the blind rotation (same share) | +0.003 | +0.003 | +0.004 | −0.004 | −0.016 |

- **Verdict (the rule committed before the run, `cycles.order_verdicts`): "no evidence" for every one of the 14
  order configurations in both held-out families**: no paired gain beats the baseline's sd in any world (the largest
  is +0.022, against 0.057); retention (c) and safety (d) hold everywhere. None is "not exercised": every order
  changed what was investigated in nearly every cycle (the combination, against lane K's order at the same share:
  62 of 64, 46 of 56 and 61 of 64 seed-cycles in lane R's worlds).
- The paired noise is small (sd of the combination's paired differences 0.008–0.021), so the result is not only the
  rule's blindness: every measured paired gain lies between −0.029 and +0.022.
- **At least as good as a blind schedule:** inside the product, the combination is within ±0.004 of the blind
  rotation on lane R's worlds and 0.004 and 0.016 below it on lane F's. **Better where change rates differ: no**
  (+0.003 against the blind rotation in the learnable world, 1 seed better, 1 worse). Why: with more items than one
  TTL's allowance, the rotation floor decides most picks (30.8 of 32 per run in ho-rate-mixed), and the rate is
  learned from verified changes, which are rare when 6.0 of the 13.8 looks per run at a changed subject find the change
  and do not revise (baseline, ho-rate-mixed); the
  combination without the rate gives identical results in two of the five held-out worlds.
- **A fair rotation as the default** (brief item 5) is not supported either: `recheck:rotation/all` gains −0.006,
  −0.007, +0.001, −0.007 and +0.022 over today's loop.
- What the order did (held-out, per run, the combination): every autonomous look a re-check pick (32, 42 and 33 in
  lane R's worlds; 28 and 42 in lane F's), of which overdue under the floor 30.8, 20.2, 28.0, 27.0, 21.2, and with a
  newer version seen 16.2, 21.5, 14.5, 11.8, 21.8; coverage identical to the baseline in every world (no order can
  reach the uncovered subjects).
- Safety and retention, every run of every configuration (192 + 128 held-out, 144 + 96 development): 0 own-output
  versions admitted, 0 wrong-story versions admitted, 0 one-origin confidence rises, 0 subjects lost or regressed.
  These worlds do not reproduce the withheld worlds' holes (Step 0), so they do not show the order safe where those
  holes exist; lane S closes them.
- Cost (held-out, per run, baseline → the combination): pages 222 → 226, 175 → 200, 210 → 217, 208 → 208,
  191 → 209 (the whole allowance used); wall time +20 to +33 % (0.40–0.47 s → 0.53–0.58 s).
- Cognitive benchmark with every individual reading `idle_recheck` on, the whole allowance and the combination
  (scripts/bench_wave1.py's regime), development and held-out: retention all retained; adversarial 28 of 28 scored
  cases pass (5 directive items not staged), as at the defaults and with lane K's order. Offline research digest at
  the shipped defaults: `68731d72…` (the Wave 0 equivalence test, aarch64).

## Alternatives rejected

- Covering the never-covered subjects from the timer (the largest part): it would widen what the timer admits (non-
  knowledge subjects), which the admission rule forbids ("propose it instead"); the owner or the idle investigator
  decides those.
- Counting contested sightings of a newer version as changes in the rate: they are not verified values; `seen` uses
  them only to order, never as a change of what is known.
- Re-ordering the due refreshes (`predictions.check_due`): the due set is gated by the 7-day TTL, so a fast-changing
  subject cannot be reached before it; the re-check's pool holds every live item.
- Raising the re-check's share by itself: the shares are the owner's settings; `/all` is measured as an owner choice.

## Consequences

- Defaults unchanged: `recheck_order` "staleness", `idle_recheck` off. Nothing here argues for turning either on.
- Changed code: `webcortex/recheck.py` (`order_parts`, `_histories`, `_ordered`, `_assess`; `rank` and `submit`),
  `autonomy.admission` (carries a pick's `order` on the re-check path), `WebConfig.recheck_order` and its validation,
  `self_description._to_recheck`. Harness: `cycles.py` (looks, `decompose`, `rate:` splits, `order_configurations`,
  `order_verdicts`), `scripts/bench_learning_cycles.py` (`--orders`).
- Tests: `tests/webcortex/test_recheck_order.py`, `tests/acceptance/test_learning_cycles_orders.py`, the rollback and
  Wave 0 equivalence lists (`tests/uat/`).
- Withheld look (main session only): `scripts/bench_learning_cycles.py final --note ... --out <outside> --mechanism
  recheck_order --config reference:no-loop --config baseline --config recheck:rate+seen+rotation/all --config
  recheck:rotation/all`. Not needed to keep the default.
- Not verified: the open web, real change rates, a live model, the worker's idle investigation (which shares the
  allowance), an owner who answers, timer passes more often than once a simulated day.
