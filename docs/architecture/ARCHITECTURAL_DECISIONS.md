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
Home-screen shortcuts (p.20) need a web app manifest. Decision: precache 8 assets instead of the brief's "six named program assets". Recorded as a deviation from p.29.

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

**ADR-022 No model in the loop: extractive synthesis and lexical ranking. R**
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
erasure. The cache is excluded from checkpoints and pruned by size and age. Consequence: restoring a checkpoint
restores knowledge and provenance but not page bodies; re-verification refetches.

**ADR-024 Our own SSRF-safe fetcher; Scrapling as the parser only. R**
Scrapling 0.5.1's fetchers impersonate browser TLS fingerprints by default and ship Cloudflare/Turnstile solving and
fingerprint spoofing; its redirect check is a post-hoc IP test without DNS pinning, and its spiders ignore robots.txt
by default. Decision: `net.SafeFetcher` (stdlib `http.client`) resolves, validates and pins every address, re-validates
every redirect, honours robots.txt and Retry-After, and limits size, time and type. Bodies are parsed with
`scrapling.Selector(huge_tree=False)` and converted with `markdownify`. `scrapling[fetchers]` is not installed.
Consequence: no anti-bot circumvention exists in the code base; sites that require it are reported as unavailable.

**ADR-025 open-webSearch through its loopback REST daemon, with our own fallback. R**
open-webSearch 2.2.0 provides key-free multi-engine search. Its MCP HTTP mode binds 0.0.0.0 without auth, its
browser fetch path does not pin DNS, and it has no cross-engine fallback or rate limiting (a live run on 2026-11-20:
duckduckgo answered, brave 429, startpage 0 results, bing redirect failure). Decision: run `open-websearch serve
--host 127.0.0.1` pinned by `deploy/open-websearch/package-lock.json`; call only `/search`, `/health`, `/status`;
track health per engine with a circuit breaker; fall back across engines and to structured APIs (Wikipedia, arXiv,
OpenAlex, Crossref, PubMed, PyPI, npm, OSV, GitHub). Consequence: search keeps working, degraded, when engines fail.

**ADR-026 Web knowledge enters semantic memory as candidates through the executive's question path. R**
Web content must never become verified knowledge automatically. Decision: claims → clusters → `web_knowledge` rows
with status `provisional` only when ≥2 independent sources agree, nothing contradicts, and confidence ≥ 0.75
(configurable); everything else, and every promotion to `accepted`, goes through a `pending` question
(scope `web:knowledge:<id>`) answered with `nsf resolve ID TEXT --confirm|--reject`. Versions are never overwritten:
a new value supersedes the old row, which keeps its validity interval. Consequence: raw evidence, extracted claims
and accepted knowledge stay distinguishable, and an owner can audit every step.

**ADR-027 A worker drains explicitly queued tasks; a timer drives monitors and refresh. R** (extends ADR-012)
Pocket- and console-initiated research must run on the Spark without blocking a request. Decision: tasks are rows
in `web_tasks`; `nsf web work` (systemd `neuroscifi-webcortex.service`) claims queued tasks one at a time;
`nsf web tick` (timer) starts due monitors and, only when `autonomous` is on, refreshes and curiosity research
within a daily cap. Nothing crawls without a task, a budget and an origin. Consequence: every background action is a
visible, cancellable task with an episode log.

**ADR-028 Pocket can request research, scoped to its pairing family. R** (amends ADR-013's remote surface note)
Decision: when `pocket_research` is on and the bridge is started with `--instance-db`, paired devices may
submit (≤ 300-character objective), list, read and cancel tasks of their own family, and read the report. They
cannot accept knowledge, change configuration, forget memory or see other families' tasks. Consequence: the LAN
surface grows by four actions under the existing bearer, origin, size and TLS controls.

**ADR-029 Migration 6 adds the web tables to the individual; forget and erasure cover them. H (format)**
Decision: twelve `web_*` tables (ARCHITECTURE §6), owned by `webcortex/state.py`. `nsf forget research|knowledge|source`
cascades to derived claims, links, knowledge versions and pending questions, marks checkpoints stale, and records a
`deletion_log` row. Forgetting a task deletes its row, events, sources and claims; the deletion log keeps only the task id, and a byte-level test checks that the objective text leaves the file and its WAL. Owner questions about forgotten knowledge keep their row with the text replaced by `#forgotten`.

**ADR-030 JavaScript rendering is an escalation, sandboxed and routed through SafeFetcher. R**
Decision: when extraction finds a JavaScript shell and the task has a browser-session budget, `browser.BrowserRenderer`
loads the page in a fresh Chromium context whose every request is intercepted and fulfilled by `SafeFetcher` (or
refused). No cookies persist, downloads and permissions are denied, service workers and WebSockets are blocked.
Off by default (`browser_rendering`). Consequence: SSRF and robots controls apply to every sub-resource.
