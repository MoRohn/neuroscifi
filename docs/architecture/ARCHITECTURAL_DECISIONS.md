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

**ADR-030 JavaScript rendering is an escalation, sandboxed and routed through SafeFetcher. R**
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
   the label "Advanced reasoning unavailable — showing extractive summary" and the reason. The extractive answer is
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
