# Requirements matrix

Source of truth: `NeuroSciFi_Architecture.pdf` (Engineering edition 0.4, 34 pp.), cited as `p.N`. The editable twin is `Architecture.md`. Every requirement found in the brief is listed here, including those this release cannot satisfy, so none drops out silently.

**Class**
- **M**: mandatory, and feasible on the build host.
- **MB**: mandatory, but blocked by unavailable hardware, accounts or toolchains.
- **P**: labelled *proposed*, *future* or *native target* in the brief. Documented and deferred.
- **R**: research or reproducibility claim.

**Status**
- **VERIFIED**: implemented, its tests pass, and the evidence is recorded in `docs/quality/VERIFICATION_REPORT.md`.
- **IMPLEMENTED**: code exists, but verification is incomplete.
- **PLANNED**: not yet built.
- **BLOCKED**: an external dependency prevents the work.
- **DEFERRED**: out of scope for this release, for the reason given.

Owners refer to `docs/execution/AGENT_OWNERSHIP.md`. Status values are updated as each wave lands.

## INS: the persistent individual

| ID | Requirement | Src | Cl | Implementation | Verification | Status |
|---|---|---|---|---|---|---|
| INS-01 | Each instance has a founding graph, a private sequence of experiences and a history of circuitry changes. A separate file is a separate individual. | p.1 | M | `instance/store.create` refuses an existing file. Evidence journal, `graph_revisions`, `belief_revisions` | test_graphs founding; test_evidence | PLANNED |
| INS-02 | Restarting restores learned state, pending uncertainties and memory permissions. | p.1, p.19 | M | All state is in SQLite; beliefs are recomputed from the journal | test_beliefs restart; acceptance `device_ownership` | PLANNED |
| INS-03 | Birth: publish priors, supplied action semantics, encoder provenance, initial graph and memory inventory; measure baseline with zero episodes. | p.19, p.2 | M | `store.inventory`, `nsf birth`, console Instance screen | test_store birth; acceptance `birth` | PLANNED |
| INS-04 | Track prior exposure of each encoder, every teacher intervention, repeat views and replay updates. | p.2 | M | `counters`: `encoder_exposure:*`, `teacher_interventions`, `repeat_views`, `replay_updates` | test_executive resolve; test_evidence duplicate | PLANNED |
| INS-05 | An event carries sample ID, sensor or speaker identity, upstream calibration root and epoch. Deduplicate copies and group readings by root. | p.12 | M | `evidence` columns; `beliefs.compute` dedupes by (channel, root, sample_id) and groups references | test_beliefs dedupe | PLANNED |
| INS-06 | Replaying an event ID is idempotent; reusing an ID for a different payload is an error. | README; p.24 | M | `evidence.observe` → duplicate / `Conflict` | test_evidence | PLANNED |
| INS-07 | A copy of each action enters the predictor before its measured consequence (efference copy). | p.5 | M | Every observation records the commanded `action`; readings are corrected by it | test_beliefs legacy parity | PLANNED |
| INS-08 | Joint beliefs over body mapping, sensor alignment and partner conventions, with a reserved unknown alternative. Report when a cause is unidentifiable. | p.6, p.12 | M | `beliefs.compute`: factorised exact inference; `unidentifiable` list | test_beliefs identifiability | PLANNED |
| INS-09 | A different speaker can use a different convention. Record who said something separately from what was verified. | p.14 | M | Per-partner convention bit; `actor` and `root` kept per event; references verify | test_beliefs two partners | PLANNED |
| INS-10 | Metacognition estimates whether uncertainty comes from sensing, a partner's convention, the world, or an inadequate hypothesis set, and assigns learning to the affected scope. | p.5 | M | `beliefs.detect_change` scopes plus `explain` uncertainty text | test_beliefs change scopes | PLANNED |
| INS-11 | Grounding: deliberately alter one instrument; the model identifies the affected scope or asks for calibration. Discovering onset during ordinary use is a product requirement. | p.19, p.13 | M | Changepoint test with no announcement needed; executive `ask` (calibrate) | test_beliefs; acceptance `grounding` | PLANNED |
| INS-12 | Executive chooses among act, observe, ask, test a reference and defer, within a goal and interruption budget. | p.5 | M | `executive.decide` 7-rule policy; `ask_budget_per_epoch` | test_executive | PLANNED |
| INS-13 | Ask about a word only if the evidence still leaves two meanings plausible. | p.19 | M | Executive rule 6 | test_executive | PLANNED |
| INS-14 | The person can inspect which experience led to a change and which uncertainty remains. | p.19 | M | `beliefs.explain`; console Beliefs screen | test_beliefs explain; console e2e | PLANNED |
| INS-15 | Owner can refuse a recording or choose memory-only versus learning. | p.14, p.20 | M | `consent` = learn / memory_only, excluded from inference | test_beliefs memory_only | PLANNED |
| INS-16 | Generated or simulated experience is labelled as such. | p.7, p.14 | M | `provenance` on evidence and datasets | tests | PLANNED |
| INS-17 | Regulation tracks activity, backlog, write volume and resource pressure. Storage pressure pauses new writes while leaving approved skills usable. | p.5, p.14 | M | `store.regulation`, `WritesPaused` (507), executive `defer` | test_executive paused; test_store | PLANNED |

## MEM: hybrid memory and governance

| ID | Requirement | Src | Cl | Implementation | Verification | Status |
|---|---|---|---|---|---|---|
| MEM-01 | Working memory: unconfirmed explanations and pending actions. | p.14 | M | `pending` table and `decisions` log | test_executive | PLANNED |
| MEM-02 | Episodic record: consent-scoped, acquisition and arrival times, actor and action, upstream identities. | p.14 | M | `evidence` columns `acquired_at`, `recorded_at`, `actor`, `consent`, `root`, `sample_id` | test_evidence | PLANNED |
| MEM-03 | Semantic memory: scoped beliefs with uncertainty, supporting events, counterevidence and revision history. | p.14 | M | `beliefs.explain`, `belief_revisions` | test_beliefs | PLANNED |
| MEM-04 | Procedural memory: policies with applicability conditions, previous failures, tested interventions and dependency on graph versions. | p.14 | M | `graph_revisions` per context with decisions, rejections, lesion effects, lineage | test_graphs | PLANNED |
| MEM-05 | Digital archive: exact values, time-indexed search, alternate graph snapshots, permission and deletion records. | p.14 | M | `memory.retrieve` time filters; NSG blobs; `deletion_log`; NSFA checkpoints | test_memory | PLANNED |
| MEM-06 | Unified retrieval by exact key and time, and by graph version and context, returning scope, confidence, outcome history, permissions and state version. | p.10 | M | `memory.retrieve` | test_memory | PLANNED |
| MEM-06b | Retrieval by association or embedding. | p.10 | P | No embedding encoder exists in this release (see ADR-009) | — | DEFERRED |
| MEM-07 | A belief can be superseded while its evidence stays auditable. | p.10 | M | Belief revisions are append-only; evidence stays until forgotten | test_beliefs revisions | PLANNED |
| MEM-08 | Deleting an episode applies to derived snapshots and archives, with rebuilding; invalidate dependent beliefs; record a failed rollback and its cause. | p.10, p.14 | M | `governance.forget_*` cascade; `graphs.invalidate_dependents`; `checkpoints.rebuild_stale`; restore re-applies tombstones | test_governance; test_checkpoints | PLANNED |
| MEM-09 | Raw capture has a short default lifetime, with explicit promotion of selected episodes. | p.14 | M | `expires_at` (default 7 days), `governance.promote`, `sweep` | test_governance | PLANNED |
| MEM-10 | Consolidation: rehearse disconfirming cases, rare skills and a uniform control sample; test with episodic lookup disabled; generative replay labelled. | p.7, p.14 | R | Research protocol. Graph recall from packed snapshots alone is the lookup-disabled check in this release | acceptance `growth_retention` | DEFERRED |

## GRA: executable connectome

| ID | Requirement | Src | Cl | Implementation | Verification | Status |
|---|---|---|---|---|---|---|
| GRA-01 | Structural graph: directed edges with source, destination, sign, weight, precision class and version. The runtime executes it. | p.5 | M | NSG1 snapshots in `graph_revisions`; recall executes them | test_graphs | PLANNED |
| GRA-01b | Edge branch type and delay fields. | p.5 | P | NSG1 has no branch or delay fields (ADR-010) | — | DEFERRED |
| GRA-02 | Fixed active-edge budget with edge exchange driven by residual correlations. | p.6, p.8 | M | `learner.fit` (graph_bench loop) | test_learner parity | PLANNED |
| GRA-03 | Over-provision, prune, quantise, and choose the smallest snapshot within 2% validation tolerance. | p.9 | M | `learner.pack` | test_graphs | PLANNED |
| GRA-04 | Preserve an older graph when it still solves a prior context (context-specific checkpoint). | p.6, p.8 | M | Per-context active revisions; the retention gate recommends a new context | test_graphs gate (c) | PLANNED |
| GRA-05 | Select a stored graph from recent prediction errors, committing each prediction before its outcome. | p.10 | M | `recall.run` | test_recall | PLANNED |
| GRA-06 | Unknown-context option; no autonomous graph creation without a test. | p.10 | M | Unknown verdict creates a `pending` new_context row and no graph | test_recall | PLANNED |
| GRA-07 | Promote only if benefit transfers beyond training episodes, older skills stay within tolerance, and resources fit a locked budget. | p.19, p.6 | M | `graphs.promote` gate (a)–(d) | test_graphs gate | PLANNED |
| GRA-08 | Scoring rules are versioned separately; the learner cannot change its own exam. | p.6 | M | `scoring_rules` immutability triggers; rule pinned per candidate; owner-only add | test_store rules; test_graphs (e) | PLANNED |
| GRA-09 | A single writer owns each mutable graph revision; others read a pinned version. | p.17 | M | `graph_leases`; immutable snapshot identity trigger | test_graphs lease | PLANNED |
| GRA-10 | Archive validated older states and revert harmful changes. | p.7 | M | archived status, `graphs.rollback` | test_graphs rollback | PLANNED |
| GRA-11 | Effective influence: silence a route in a fork, repeat episodes, and store the effect with its context. Permutation control. | p.5, p.19 | M | `graphs.lesion` → `graph_effects` | test_graphs lesion | PLANNED |
| GRA-12 | Compare branch-specific plasticity, STDP and eligibility traces; sleep-like replay; dendritic rules. | p.6–7 | R | Research agenda; not a product feature | — | DEFERRED |

## ARC: archives

| ID | Requirement | Src | Cl | Implementation | Verification | Status |
|---|---|---|---|---|---|---|
| ARC-01 | NSFA: independently decodable 64 KiB blocks with per-block codec and checksums plus a footer; fast, cold and experimental policies. | p.15–16 | M | `neuroscifi/archive.py` (prototype, reused) | test_compression | VERIFIED (W1) |
| ARC-02 | Consistent SQLite backup with the writer open. | p.16 | M | `archive.convert('checkpoint')`; `checkpoints.create` | test_checkpoints | PLANNED |
| ARC-03 | A failed restore never publishes a partial file; publication refuses to overwrite. | p.16 | M | `archive.convert` temp-dir plus `os.link` | test_compression; test_checkpoints | PLANNED |
| ARC-04 | Vendored arithmetic coder pinned by blob ID, with license notices carried along. | p.15, p.34 | M | `vendor_tufazip` byte-identical, packaged with LICENSE and NOTICE | git rename 100% | VERIFIED (W1) |
| ARC-05 | Archive access control, encryption as appropriate, and a deletion policy for retained backups. | p.16 | M | Files 0600 in a 0700 directory; stale-archive rebuild and unlink; encrypted volume per runbook (ADR-011) | test_checkpoints; runbook | PLANNED |
| ARC-06 | Neural codec (TufaZip full system). | p.15 | P | Not reproduced; requires B200 GPUs | — | DEFERRED |

## RES: reproducibility

| ID | Requirement | Src | Cl | Implementation | Verification | Status |
|---|---|---|---|---|---|---|
| RES-01 | Eight deterministic analyses reproduce their published JSON. | p.31, p.33 | R | `scripts/reproduce.py` | check.sh | VERIFIED (W1) |
| RES-02 | Lossless archive bake-off (180 round trips). | p.16 | R | `nsf lab compression_bench` | run in Wave 5 | PLANNED |
| RES-03 | CPU timing reports for both runs. | p.18 | R | `research/scripts/summarize_timings.py` | Wave 5 | PLANNED |
| RES-04 | Device probe: CPU parity and latency. | p.17 | R | `nsf lab profile_device --device cpu` | Wave 5 | PLANNED |
| RES-04b | CUDA probe. | p.17 | MB | Needs an NVIDIA GPU and CuPy; none on host | — | BLOCKED |
| RES-05 | Human-data reanalyses from hash-verified public inputs. | p.4 | R | `research/scripts/*` (network, SciPy) | optional | DEFERRED |
| RES-06 | Field learner benchmark: 3,072 forecasts, about 88.8% coverage and 88.3% selective accuracy. | p.31 | R | `tests/js/field_benchmark.mjs` | check.sh | VERIFIED (W1) |

## POC: Pocket (phone/tablet web runtime and bridge)

| ID | Requirement | Src | Cl | Implementation | Verification | Status |
|---|---|---|---|---|---|---|
| POC-01 | Sensors off by default. Check-once sound and light produce one coarse value awaiting review. | p.21 | M | `pocket/web/mobile.js` | sensor_check; e2e | VERIFIED (W1 node) |
| POC-02 | Listen for up to 5 minutes in the foreground; opt-in one band per minute; the switch clears on stop. | p.21 | M | mobile.js | sensor_check; e2e | IMPLEMENTED |
| POC-03 | Private moment: Stop releases tracks; Discard clears the draft; a hidden page clears drafts. | p.21, p.20 | M | mobile.js | sensor_check; e2e | IMPLEMENTED |
| POC-04 | Spoken activity: on-device US English only, one whole label, discard on extra words or names, review required. | p.22 | M | mobile.js speech gate | sensor_check | VERIFIED (W1 node) |
| POC-05 | Light check reduces a frame to one pixel and releases the track; only dim, midrange or bright leave. | p.23 | M | mobile.js | sensor_check | VERIFIED (W1 node) |
| POC-06 | Review: inspect the exact outgoing value; share once, allow learning, discard or forget. | p.20 | M | mobile.js; bridge `save` / `forget` | test_mobile; e2e | IMPLEMENTED |
| POC-07 | Transport: one-use 5-minute invitation; per-pairing revocable credential in tab memory; loopback by default; LAN requires TLS. | p.24 | M | pocket/server.py | test_mobile_http (real TLS) | VERIFIED (W1) |
| POC-08 | Receiver: exactly four fields, channel vocabularies, size limit, auth, per-pairing isolation, conflict on changed retry, no resurrection of expired or forgotten IDs. | p.24 | M | server.py | test_mobile | VERIFIED (W1) |
| POC-09 | Estimator: next value per channel from retained counts plus one prior count, recomputed after deletion. | p.24 | M | server.py `state` | test_mobile | VERIFIED (W1) |
| POC-10 | Storage: 24 h expiry; sweep every minute, before authenticated reads and writes, and at startup; tombstones until unpairing. | p.24 | M | server.py | test_mobile | VERIFIED (W1) |
| POC-11 | Field episode: categorical features with sources; ≥1 match and ≥2/3 agreement; ≤12 neighbours; one vote each; abstain unless support ≥3 and posterior ≥55%. | p.30 | M | field-model.mjs `forecast` | field_check; field_benchmark | VERIFIED (W1 node) |
| POC-12 | Freeze features and estimate before asking what happened; reopening restores the estimate. | p.27 | M | field-ui.mjs plus vault | field_check; e2e | IMPLEMENTED |
| POC-13 | "Why this estimate?" shows three probabilities and up to three contributing episodes. | p.29 | M | field-ui.mjs | e2e | IMPLEMENTED |
| POC-14 | Vault: AES-GCM with a fresh IV per write and a PBKDF2 key; Web Locks; expired rows excluded; 64 entries; 24 h by default or 7 days on request. | p.29–30 | M | field-model.mjs, field-ui.mjs | field_check; e2e | VERIFIED (W1 node) |
| POC-15 | Offline: the service worker precaches named assets and excludes API traffic; cold reopen works without the Spark. | p.27, p.29 | M | sw.js | offline_check; e2e offline | IMPLEMENTED |
| POC-16 | Cross-device: a five-minute one-use handoff code; the second device gets its own credential; linked devices view and forget shared episodes; legacy receipts stay private. | p.29 | M | server.py `link` | test_field; e2e handoff | IMPLEMENTED |
| POC-17 | Tablet layout at wide viewports: field session and memory in one column, sensing and Spark alongside. | p.29 | M | style.css ≥800px grid | e2e layout | IMPLEMENTED |
| POC-18 | Shared devices: sensors off, session-only memory, lock on hide. | p.29 | M | field-ui.mjs lock on hide | field_check | VERIFIED (W1 node) |
| POC-19 | Evening review: observations that changed a forecast, anything awaiting approval, last time a sensor was active; forget this, forget this session, stop learning this pattern. | p.21 | M | W2-D: review panel, `mute` / `unmute` routes | test_mobile; e2e | PLANNED |
| POC-20 | Home and lock-screen shortcuts open Ask, Mark an activity, or a capture session. | p.20 | M (web part) | W2-D: web manifest shortcuts | e2e | PLANNED |
| POC-21 | Imported device metrics (pocket-device-1) and screenshot detail band; app identity stays out of the episode. | p.28 | M | field-model.mjs `deviceReceipt`, `texture` | field_check | VERIFIED (W1 node) |
| POC-22 | Interface never launches or blocks another app because of its guess. | p.30 | M | No such capability exists in the code | audit | VERIFIED (by construction) |
| POC-23 | Accessibility: keyboard reachable, visible focus, screen-reader status, contrast. | p.25 | M | W2-D audit and fixes | e2e axe and keyboard | PLANNED |
| POC-24 | Hide personal answers on a locked screen; native lock-screen stop and expiry; haptic cues. | p.20 | P | Native target | — | DEFERRED |
| POC-25 | Encrypted, explicitly enabled offline upload queue to the Spark. | p.20, p.24 | P | Native evolution; the web build has no automatic queue by design | — | DEFERRED |
| POC-26 | Everyday companion (renewable native sessions with the screen locked). | p.21 | P | Native target | — | DEFERRED |
| POC-27 | Owner voice notes, enrollment, attribution and redaction; object and scene notes with detectors; private zones. | p.22–23 | P | Proposed; needs on-device models | — | DEFERRED |

## NAT: native collectors

| ID | Requirement | Src | Cl | Implementation | Verification | Status |
|---|---|---|---|---|---|---|
| NAT-01 | Android collector: usage and screen bands, power and thermal; no internet permission; export pocket-device-1 JSON. | p.28 | MB | `mobile/native/android` source (prototype) | Schema contract test only; no SDK or handset on host | BLOCKED |
| NAT-02 | iOS Pocket Metrics: Shortcut marks, power and thermal; export JSON. | p.28 | MB | `mobile/native/ios/PocketMetrics.swift` | No Xcode or iPhone on host | BLOCKED |
| NAT-03 | Native release tests: lock, interruption, network loss, permission revoke, sleep, thermal, reboot; zero post-stop restarts; leakage and battery measures. | p.26 | MB | Commissioning plan in runbook | Needs handsets | BLOCKED |

## OPS: Spark operation and deployment

| ID | Requirement | Src | Cl | Implementation | Verification | Status |
|---|---|---|---|---|---|---|
| OPS-01 | Fresh service identity, environment, state directory and explicit device permissions. | p.17 | M | `deploy/systemd/*.service` (DynamicUser or a dedicated user, StateDirectory, hardening) | `systemd-analyze verify` | PLANNED |
| OPS-02 | Default binding is loopback; LAN binding requires TLS. | p.24 | M | server.py argument check | test_mobile_http | VERIFIED (W1) |
| OPS-03 | Rehearse against synthetic input before taking exclusive ownership of devices. | p.17 | M | `nsf acceptance` runs the synthetic rehearsal | acceptance suite | PLANNED |
| OPS-04 | Preserve a restorable snapshot before switchover, keep a reversible service selection, and verify startup, loss of input, restart and power recovery. | p.17 | M (local) / MB (Spark) | Runbook procedure, `nsf checkpoint`, restart test | test restart; runbook | PLANNED |
| OPS-05 | Use an encrypted volume and exclude the bridge DB from backups. | p.24 | M (doc) | Runbook and environment guide | review | PLANNED |
| OPS-06 | Physical DGX Spark commissioning: p50/p95/p99 sensor-to-action latency, dropped inputs, memory high-water, joules per skill. | p.17–18 | MB | `nsf bench` measures host-local decision latency; Spark run blocked | bench output | BLOCKED (Spark) |
| OPS-07 | Consolidation and archiving as independently interruptible jobs, off the interactive loop. | p.18, p.16 | M | Checkpoints and graph learning are separate CLI or console jobs; bridge and console requests never run them inline (ADR-012) | review | PLANNED |
| OPS-08 | No access logs of observations, addresses, URLs or tokens. | p.24 | M | `log_message` suppressed in both servers | test | IMPLEMENTED |

## ACC: first-release acceptance tests (simulated where the brief requires hardware)

| ID | Milestone | Src | Cl | Implementation | Status |
|---|---|---|---|---|---|
| ACC-01 | Birth | p.19 | M | `nsf acceptance` scenario `birth` | PLANNED |
| ACC-02 | Grounding (instrument altered without announcement → scope identified or calibration asked) | p.19 | M | scenario `grounding` | PLANNED |
| ACC-03 | Lifelong correction (second partner with opposite mapping; first partner's environment restored a day later; both meanings scored) | p.19 | M | scenario `lifelong_correction` | PLANNED |
| ACC-04 | Graph development (forked instances on different streams, cross-tested, permuted and lesioned, compared with fixed and dense) | p.19 | M | scenario `graph_development` | PLANNED |
| ACC-05 | Growth and retention (gate) | p.19 | M | scenario `growth_retention` | PLANNED |
| ACC-06 | Device ownership (capture-to-action loop, restart with intact beliefs and evidence) | p.19 | M (software) / MB (physical) | scenario `device_ownership` | PLANNED |
| ACC-07 | Decision thresholds: 50% fewer confident-wrong answers on shifted sensors, at most 2 points of task loss, at least 80% coverage, with Brier score and coverage reported | p.19 | R | Recalibration evidence (`evidence/recalibration.json`) via reproduce | VERIFIED (W1) |
| ACC-08 | First physical acceptance test (teach an action and a word, switch a mapping, restart, return) | p.34 | MB (physical) | Simulated equivalent in `nsf acceptance` | PLANNED |

## DEF: explicitly pending in the brief (not built, by design)

Production neural encoders, general language, open-world reasoning, autonomous lifelong graph revision, physical Spark commissioning (p.19). Free-form dialogue and replies that combine memories with the world model (p.20, "proposed"). MediaProjection and screen assistance, iOS broadcast (p.32). Screen Time / FamilyActivityData (p.32). Field trial (p.32). The brief itself lists these as pending milestones. They are recorded here so they cannot disappear.
