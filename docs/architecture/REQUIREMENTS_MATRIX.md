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

Owners refer to `docs/execution/AGENT_OWNERSHIP.md`. Statuses are as of the final verification (see VERIFICATION_REPORT §8). Evidence for each is in `docs/quality/VERIFICATION_REPORT.md`.

## INS: the persistent individual

| ID | Requirement | Src | Cl | Implementation | Verification | Status |
|---|---|---|---|---|---|---|
| INS-01 | Each instance has a founding graph, a private sequence of experiences and a history of circuitry changes. A separate file is a separate individual. | p.1 | M | `instance/store.create` refuses an existing file. Evidence journal, `graph_revisions`, `belief_revisions` | test_graphs founding; test_evidence | VERIFIED (founding graph is owner-supplied; see ADR-016) |
| INS-02 | Restarting restores learned state, pending uncertainties and memory permissions. | p.1, p.19 | M | All state is in SQLite; beliefs are recomputed from the journal | test_beliefs restart; acceptance `device_ownership` | VERIFIED |
| INS-03 | Birth: publish priors, supplied action semantics, encoder provenance, initial graph and memory inventory; measure baseline with zero episodes. | p.19, p.2 | M | `store.inventory`, `nsf birth`, console Instance screen | test_store birth; acceptance `birth` | VERIFIED |
| INS-04 | Track prior exposure of each encoder, every teacher intervention, repeat views and replay updates. | p.2 | M | `counters`: `encoder_exposure:*`, `teacher_interventions`, `repeat_views`, `replay_updates` | test_executive resolve; test_evidence duplicate | VERIFIED (counter semantics in ADR-016) |
| INS-05 | An event carries sample ID, sensor or speaker identity, upstream calibration root and epoch. Deduplicate copies and group readings by root. | p.12 | M | `evidence` columns; `beliefs.compute` dedupes by (channel, root, sample_id) and groups references | test_beliefs dedupe | VERIFIED |
| INS-06 | Replaying an event ID is idempotent; reusing an ID for a different payload is an error. | README; p.24 | M | `evidence.observe` → duplicate / `Conflict` | test_evidence | VERIFIED |
| INS-07 | A copy of each action enters the predictor before its measured consequence (efference copy). | p.5 | M | Every observation records the commanded `action`; readings are corrected by it | test_beliefs legacy parity | VERIFIED |
| INS-08 | Joint beliefs over body mapping, sensor alignment and partner conventions, with a reserved unknown alternative. Report when a cause is unidentifiable. | p.6, p.12 | M | `beliefs.compute`: factorised exact inference; `unidentifiable` list | test_beliefs identifiability | VERIFIED |
| INS-09 | A different speaker can use a different convention. Record who said something separately from what was verified. | p.14 | M | Per-partner convention bit; `actor` and `root` kept per event; references verify | test_beliefs two partners | VERIFIED |
| INS-10 | Metacognition estimates whether uncertainty comes from sensing, a partner's convention, the world, or an inadequate hypothesis set, and assigns learning to the affected scope. | p.5 | M | `beliefs.detect_change` scopes plus `explain` uncertainty text | test_beliefs change scopes | VERIFIED (confirmed change relearns only that scope) |
| INS-11 | Grounding: deliberately alter one instrument; the model identifies the affected scope or asks for calibration. Discovering onset during ordinary use is a product requirement. | p.19, p.13 | M | Changepoint test with no announcement needed; executive `ask` (calibrate) | test_beliefs; acceptance `grounding` | VERIFIED (simulated; scope correct in 92–94% of changed lifetimes) |
| INS-12 | Executive chooses among act, observe, ask, test a reference and defer, within a goal and interruption budget. | p.5 | M | `executive.decide` 7-rule policy; `ask_budget_per_epoch` | test_executive | VERIFIED |
| INS-13 | Ask about a word only if the evidence still leaves two meanings plausible. | p.19 | M | Executive rule 6 | test_executive | VERIFIED |
| INS-14 | The person can inspect which experience led to a change and which uncertainty remains. | p.19 | M | `beliefs.explain`; console Beliefs screen | test_beliefs explain; console e2e | VERIFIED |
| INS-15 | Owner can refuse a recording or choose memory-only versus learning. | p.14, p.20 | M | `consent` = learn / memory_only, excluded from inference | test_beliefs memory_only | VERIFIED |
| INS-16 | Generated or simulated experience is labelled as such. | p.7, p.14 | M | `provenance` on evidence and datasets | tests | VERIFIED |
| INS-17 | Regulation tracks activity, backlog, write volume and resource pressure. Storage pressure pauses new writes while leaving approved skills usable. | p.5, p.14 | M | `store.regulation`, `WritesPaused` (507), executive `defer` | test_executive paused; test_store | VERIFIED (rows, free space, optional thermal) |

## MEM: hybrid memory and governance

| ID | Requirement | Src | Cl | Implementation | Verification | Status |
|---|---|---|---|---|---|---|
| MEM-01 | Working memory: unconfirmed explanations and pending actions. | p.14 | M | `pending` table and `decisions` log | test_executive | VERIFIED (questions close on evidence or after 7 days) |
| MEM-02 | Episodic record: consent-scoped, acquisition and arrival times, actor and action, upstream identities. | p.14 | M | `evidence` columns `acquired_at`, `recorded_at`, `actor`, `consent`, `root`, `sample_id` | test_evidence | VERIFIED |
| MEM-03 | Semantic memory: scoped beliefs with uncertainty, supporting events, counterevidence and revision history. | p.14 | M | `beliefs.explain`, `belief_revisions` | test_beliefs | VERIFIED |
| MEM-04 | Procedural memory: policies with applicability conditions, previous failures, tested interventions and dependency on graph versions. | p.14 | M | `graph_revisions` per context with decisions, rejections, lesion effects, lineage | test_graphs | VERIFIED |
| MEM-05 | Digital archive: exact values, time-indexed search, alternate graph snapshots, permission and deletion records. | p.14 | M | `memory.retrieve` time filters; NSG blobs; `deletion_log`; NSFA checkpoints | test_memory | VERIFIED |
| MEM-06 | Unified retrieval by exact key and time, and by graph version and context, returning scope, confidence, outcome history, permissions and state version. | p.10 | M | `memory.retrieve` | test_memory | VERIFIED |
| MEM-06b | Retrieval by association or embedding. | p.10 | M (association) / P (embedding) | Association across epochs: `beliefs.match_epochs` (segment-aware). Pocket field episodes: similarity retrieval. No embedding encoder for evidence or episodes exists (ADR-009). Web knowledge only: `knowledge.query` can rank by an optional embedding model and reranker (`webcortex/semantic.py`; off until configured; no vector is stored) | test_scope_learning; test_semantic_memory | PARTIAL: association yes, embedding deferred |
| MEM-07 | A belief can be superseded while its evidence stays auditable. | p.10 | M | Belief revisions are append-only; evidence stays until forgotten | test_beliefs revisions | VERIFIED |
| MEM-08 | Deleting an episode applies to derived snapshots and archives, with rebuilding; invalidate dependent beliefs; record a failed rollback and its cause. | p.10, p.14 | M | `governance.forget_*` cascade; `graphs.invalidate_dependents`; `checkpoints.rebuild_stale`; restore re-applies tombstones and forgotten epochs' derived rows | test_governance; test_checkpoints | VERIFIED (erasure scrub, WAL truncation, restore re-applies) |
| MEM-09 | Raw capture has a short default lifetime, with explicit promotion of selected episodes. | p.14 | M | `expires_at` (default 7 days), `governance.promote`, `sweep` | test_governance | VERIFIED |
| MEM-10 | Consolidation: rehearse disconfirming cases, rare skills and a uniform control sample; test with episodic lookup disabled; generative replay labelled. | p.7, p.14 | R | Research protocol. Graph recall from packed snapshots alone is the lookup-disabled check in this release | acceptance `growth_retention` | DEFERRED |

## GRA: executable connectome

| ID | Requirement | Src | Cl | Implementation | Verification | Status |
|---|---|---|---|---|---|---|
| GRA-01 | Structural graph: directed edges with source, destination, sign, weight, precision class and version. The runtime executes it. | p.5 | M | NSG1 snapshots in `graph_revisions`; recall executes them | test_graphs | VERIFIED |
| GRA-01b | Edge branch type and delay fields. | p.5 | M (reclassified by audit) | NSG1 has no branch or delay fields; changing the format would invalidate verified packing results (ADR-010) | — | NOT IMPLEMENTED: open gap, planned NSG2 |
| GRA-02 | Fixed active-edge budget with edge exchange driven by residual correlations. | p.6, p.8 | M | `learner.fit` (graph_bench loop; the exchange for all receivers at once) | test_learner: every bit against the lab loop at 2 to 1024 populations and against `graph_bench.simulate` | PARTIAL: verified, the learner exists and equals the lab's (max diff 0.0). Not the default: under the birth rule gate-v1 the promoted snapshot is cut from the dense fit and the edge-exchange graph is only measured (`metrics.sparse`). On the lab world (24 populations) the two are the same size and within 0.3 % of each other, so nothing is lost there; from about 96 populations the dense fit degrades and from 160 its snapshot is worse than no graph, while the edge-exchange graph stays at the noise floor. A rule the owner adds (`pack_candidates.learned_sparse`, `pack_reference: best_learner`) promotes it; none is registered by the software (`accel_decisions_graph_learner_*.json`) |
| GRA-03 | Over-provision, prune, quantise, and choose the smallest snapshot within 2% validation tolerance. | p.9 | M | `learner.pack` | test_graphs, test_learner (gate-v1's choice unchanged byte for byte) | VERIFIED (4-bit k=3, 247 B). The tolerance is against the dense fit, so it is only as good as that fit: see GRA-02 |
| GRA-04 | Preserve an older graph when it still solves a prior context (context-specific checkpoint). | p.6, p.8 | M | Per-context active revisions; the retention gate recommends a new context | test_graphs gate (c) | VERIFIED |
| GRA-05 | Select a stored graph from recent prediction errors, committing each prediction before its outcome. | p.10 | M | `recall.run` | test_recall | VERIFIED (context accuracy 95.5%) |
| GRA-06 | Unknown-context option; no autonomous graph creation without a test. | p.10 | M | Unknown verdict creates a `pending` new_context row and no graph | test_recall | VERIFIED (incl. founding graph) |
| GRA-07 | Promote only if benefit transfers beyond training episodes, older skills stay within tolerance, and resources fit a locked budget. | p.19, p.6 | M | `graphs.promote` gate (a)–(d) | test_graphs gate | VERIFIED (+ lineage_intact) |
| GRA-08 | Scoring rules are versioned separately; the learner cannot change its own exam. | p.6 | M | `scoring_rules` immutability triggers; rule pinned per candidate; owner-only add | test_store rules; test_graphs (e) | VERIFIED |
| GRA-09 | A single writer owns each mutable graph revision; others read a pinned version. | p.17 | M | `graph_leases`; immutable snapshot identity trigger | test_graphs lease | VERIFIED |
| GRA-10 | Archive validated older states and revert harmful changes. | p.7 | M | archived status, `graphs.rollback` | test_graphs rollback | VERIFIED |
| GRA-11 | Effective influence: silence a route in a fork, repeat episodes, and store the effect with its context. Permutation control. | p.5, p.19 | M | `graphs.lesion` → `graph_effects` | test_graphs lesion | VERIFIED (with block standard errors) |
| GRA-12 | Compare branch-specific plasticity, STDP and eligibility traces; sleep-like replay; dendritic rules. | p.6–7 | R | Research agenda; not a product feature | — | DEFERRED |

## ARC: archives

| ID | Requirement | Src | Cl | Implementation | Verification | Status |
|---|---|---|---|---|---|---|
| ARC-01 | NSFA: independently decodable 64 KiB blocks with per-block codec and checksums plus a footer; fast, cold and experimental policies. | p.15–16 | M | `neuroscifi/archive.py` (prototype, reused) | test_compression | VERIFIED |
| ARC-02 | Consistent SQLite backup with the writer open. | p.16 | M | `archive.convert('checkpoint')`; `checkpoints.create` | test_checkpoints | VERIFIED |
| ARC-03 | A failed restore never publishes a partial file; publication refuses to overwrite. | p.16 | M | `archive.convert` temp-dir plus `os.link` | test_compression; test_checkpoints | VERIFIED |
| ARC-04 | Vendored arithmetic coder pinned by blob ID, with license notices carried along. | p.15, p.34 | M | `vendor_tufazip` byte-identical, packaged with LICENSE and NOTICE | git rename 100% | VERIFIED |
| ARC-05 | Archive access control, encryption as appropriate, and a deletion policy for retained backups. | p.16 | M | Files 0600 in a 0700 directory; stale-archive rebuild and unlink; encrypted volume per runbook (ADR-011) | test_checkpoints; runbook | VERIFIED (0600 files, prune keep-N, stale rebuild) |
| ARC-06 | Neural codec (TufaZip full system). | p.15 | P | Not reproduced; requires B200 GPUs | — | DEFERRED |

## RES: reproducibility

| ID | Requirement | Src | Cl | Implementation | Verification | Status |
|---|---|---|---|---|---|---|
| RES-01 | Eight deterministic analyses reproduce their published JSON. | p.31, p.33 | R | `scripts/reproduce.py` | check.sh | VERIFIED |
| RES-02 | Lossless archive bake-off (180 round trips). | p.16 | R | `nsf lab compression_bench` | run in Wave 5 | VERIFIED (aarch64 rerun; JSON 6.27× fast, as in the brief) |
| RES-03 | CPU timing reports for both runs. | p.18 | R | `research/scripts/summarize_timings.py` | Wave 5 | VERIFIED |
| RES-04 | Device probe: CPU parity and latency. | p.17 | R | `nsf lab profile_device --device cpu` | Wave 5 | VERIFIED |
| RES-04b | CUDA probe. | p.17 | M | `nsf lab profile_device --device cuda` with the `gpu` extra (CuPy 14.2.0) | GB10 run: parity max error 1.0e-6; batch-64 p50 0.22 ms vs CPU 1.05 ms (`docs/quality/evidence/profile_device_cuda_gb10.json`) | VERIFIED (reclassified: the host is a DGX Spark) |
| RES-05 | Human-data reanalyses from hash-verified public inputs. | p.4 | R | `research/scripts/*` (network, SciPy) | optional | DEFERRED |
| RES-06 | Field learner benchmark: 3,072 forecasts, about 88.8% coverage and 88.3% selective accuracy. | p.31 | R | `tests/js/field_benchmark.mjs` | check.sh | VERIFIED |

## POC: Pocket (phone/tablet web runtime and bridge)

| ID | Requirement | Src | Cl | Implementation | Verification | Status |
|---|---|---|---|---|---|---|
| POC-01 | Sensors off by default. Check-once sound and light produce one coarse value awaiting review. | p.21 | M | `pocket/web/mobile.js` | sensor_check; e2e | VERIFIED (W1 node) |
| POC-02 | Listen for up to 5 minutes in the foreground; opt-in one band per minute; the switch clears on stop. | p.21 | M | mobile.js | sensor_check; e2e | VERIFIED |
| POC-03 | Private moment: Stop releases tracks; Discard clears the draft; a hidden page clears drafts. | p.21, p.20 | M | mobile.js | sensor_check; e2e | VERIFIED |
| POC-04 | Spoken activity: on-device US English only, one whole label, discard on extra words or names, review required. | p.22 | M | mobile.js speech gate | sensor_check | VERIFIED (W1 node) |
| POC-05 | Light check reduces a frame to one pixel and releases the track; only dim, midrange or bright leave. | p.23 | M | mobile.js | sensor_check | VERIFIED (W1 node) |
| POC-06 | Review: inspect the exact outgoing value; share once, allow learning, discard or forget. | p.20 | M | mobile.js; bridge `save` / `forget` | test_mobile; e2e | VERIFIED |
| POC-07 | Transport: one-use 5-minute invitation; per-pairing revocable credential in tab memory; loopback by default; LAN requires TLS. | p.24 | M | pocket/server.py | test_mobile_http (real TLS) | VERIFIED (W1) |
| POC-08 | Receiver: exactly four fields, channel vocabularies, size limit, auth, per-pairing isolation, conflict on changed retry, no resurrection of expired or forgotten IDs. | p.24 | M | server.py | test_mobile | VERIFIED (W1) |
| POC-09 | Estimator: next value per channel from retained counts plus one prior count, recomputed after deletion. | p.24 | M | server.py `state` | test_mobile | VERIFIED (W1) |
| POC-10 | Storage: 24 h expiry; sweep every minute, before authenticated reads and writes, and at startup; tombstones until unpairing. | p.24 | M | server.py | test_mobile | VERIFIED (W1) |
| POC-11 | Field episode: categorical features with sources; ≥1 match and ≥2/3 agreement; ≤12 neighbours; one vote each; abstain unless support ≥3 and posterior ≥55%. | p.30 | M | field-model.mjs `forecast` | field_check; field_benchmark | VERIFIED (W1 node) |
| POC-12 | Freeze features and estimate before asking what happened; reopening restores the estimate. | p.27 | M | field-ui.mjs plus vault | field_check; e2e | VERIFIED |
| POC-13 | "Why this estimate?" shows three probabilities and up to three contributing episodes. | p.29 | M | field-ui.mjs | e2e | VERIFIED |
| POC-14 | Vault: AES-GCM with a fresh IV per write and a PBKDF2 key; Web Locks; expired rows excluded; 64 entries; 24 h by default or 7 days on request. | p.29–30 | M | field-model.mjs, field-ui.mjs | field_check; e2e | VERIFIED (W1 node) |
| POC-15 | Offline: the service worker precaches named assets and excludes API traffic; cold reopen works without the Spark. | p.27, p.29 | M | sw.js | offline_check; e2e offline | VERIFIED (offline cold reopen in Chromium) |
| POC-16 | Cross-device: a five-minute one-use handoff code; the second device gets its own credential; linked devices view and forget shared episodes; legacy receipts stay private. | p.29 | M | server.py `link` | test_field; e2e handoff | VERIFIED |
| POC-17 | Tablet layout at wide viewports: field session and memory in one column, sensing and Spark alongside. | p.29 | M | style.css ≥800px grid | e2e layout | VERIFIED |
| POC-18 | Shared devices: sensors off, session-only memory, lock on hide. | p.29 | M | field-ui.mjs lock on hide | field_check | VERIFIED (W1 node) |
| POC-19 | Evening review: observations that changed a forecast, anything awaiting approval, last time a sensor was active; forget this, forget this session, stop learning this pattern. | p.21 | M | W2-D: review panel, `mute` / `unmute` routes | test_mobile; e2e | VERIFIED |
| POC-20 | Home and lock-screen shortcuts open Ask, Mark an activity, or a capture session. | p.20 | M (web part) | W2-D: web manifest shortcuts | e2e | VERIFIED (web manifest shortcuts) |
| POC-21 | Imported device metrics (pocket-device-1) and screenshot detail band; app identity stays out of the episode. | p.28 | M | field-model.mjs `deviceReceipt`, `texture` | field_check | VERIFIED (W1 node) |
| POC-22 | Interface never launches or blocks another app because of its guess. | p.30 | M | No such capability exists in the code | audit | VERIFIED (by construction) |
| POC-23 | Accessibility: keyboard reachable, visible focus, screen-reader status, contrast. | p.25 | M | W2-D audit and fixes | e2e axe and keyboard | VERIFIED (axe 0 serious/critical; keyboard) |
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
| OPS-01 | Fresh service identity, environment, state directory and explicit device permissions. | p.17 | M | `deploy/systemd/*.service` (DynamicUser or a dedicated user, StateDirectory, hardening) | `systemd-analyze verify` | IMPLEMENTED (units verified offline; not run as services) |
| OPS-02 | Default binding is loopback; LAN binding requires TLS. | p.24 | M | server.py argument check | test_mobile_http | VERIFIED (W1) |
| OPS-03 | Rehearse against synthetic input before taking exclusive ownership of devices. | p.17 | M | `nsf acceptance` runs the synthetic rehearsal | acceptance suite | VERIFIED |
| OPS-04 | Preserve a restorable snapshot before switchover, keep a reversible service selection, and verify startup, loss of input, restart and power recovery. | p.17 | M (local) / MB (Spark) | Runbook procedure, `nsf checkpoint`, restart test | test restart; runbook | IMPLEMENTED (procedure + restart/restore tests; switchover not run) |
| OPS-05 | Use an encrypted volume and exclude the bridge DB from backups. | p.24 | M (doc) | Runbook and environment guide | review | IMPLEMENTED (documented; doctor detects dm-crypt) |
| OPS-06 | DGX Spark commissioning: p50/p95/p99 sensor-to-action latency, dropped inputs, memory high-water, joules per skill. | p.17–18 | M (host part) / MB (sensors, energy) | `nsf bench`; acceptance `device_ownership` idle and under concurrent learning plus checkpointing | Spark host: p50 2.10 ms, p99 2.22 ms idle; p99 3.4 ms under consolidation; 0 failed writes | PARTIAL: host measured; real-sensor latency, dropped capture and energy blocked (no capture devices or power meter) |
| OPS-07 | Consolidation and archiving as independently interruptible jobs, off the interactive loop. | p.18, p.16 | M | Checkpoints and graph learning are separate CLI or console jobs; bridge and console requests never run them inline (ADR-012) | review | VERIFIED |
| OPS-08 | No access logs of observations, addresses, URLs or tokens. | p.24 | M | `log_message` suppressed in both servers | test | VERIFIED (no addresses in errors either) |

## ACC: first-release acceptance tests (simulated where the brief requires hardware)

| ID | Milestone | Src | Cl | Implementation | Status |
|---|---|---|---|---|---|
| ACC-01 | Birth | p.19 | M | `nsf acceptance` scenario `birth` | VERIFIED |
| ACC-02 | Grounding (instrument altered without announcement → scope identified or calibration asked) | p.19 | M | scenario `grounding` | VERIFIED |
| ACC-03 | Lifelong correction (second partner with opposite mapping; first partner's environment restored a day later; both meanings scored) | p.19 | M | scenario `lifelong_correction` | VERIFIED |
| ACC-04 | Graph development (forked instances on different streams, cross-tested, permuted and lesioned, compared with fixed and dense) | p.19 | M | scenario `graph_development` | VERIFIED (simulated; equal-cost recurrent alternative only in ACC-08) |
| ACC-05 | Growth and retention (gate) | p.19 | M | scenario `growth_retention` | VERIFIED |
| ACC-06 | Device ownership (capture-to-action loop, restart with intact beliefs and evidence) | p.19 | M (software) / MB (physical) | scenario `device_ownership` | VERIFIED (simulated; host latency idle and under consolidation) |
| ACC-07 | Decision thresholds: 50% fewer confident-wrong answers on shifted sensors, at most 2 points of task loss, at least 80% coverage, with Brier score and coverage reported | p.19 | R | Recalibration evidence (`evidence/recalibration.json`) via reproduce | VERIFIED (W1) |
| ACC-08 | First physical acceptance test (teach an action and a word, switch a mapping, restart, return) | p.34 | M (simulated) / MB (physical) | `nsf acceptance first_physical_test` against baselines (fixed wiring, episodic lookup, recurrent learner with replay) | VERIFIED (simulated; pre-registered D-1: recognition 1.00, false offers 0.00, original segment 0.89; attribution 0.92, retention 0.975, confident-wrong 0 vs 78) |

## ADD: requirements added by the independent audit

| ID | Requirement | Src | Cl | Implementation | Status |
|---|---|---|---|---|---|
| GRA-13 | Functional map: log coactivation with context and lag; propose routes | p.5 | M | `graphs.functional_map`, `nsf graph coactivation`, console | VERIFIED (≥ 6 of 8 pruned true edges recovered at lag 1) |
| INS-18 | Recognise and resume an earlier setup ("on the earlier panel it recalls and executes the earlier skill") | p.19, p.1 | M | `beliefs.match_epochs` (segment candidates, exact new-setup alternative) + `epoch_links` pooling on "continue there" | VERIFIED (simulated): dynamics + calibration evidence, 1.00 recognised / 0.00 false offers incl. rewired near-duplicates; needs an observation stream (`nsf epoch stream`) |
| INS-19 | Developmental milestones reported with interactions and compute per capability | p.6 | M | Acceptance JSON reports owner answers, references and CPU seconds per lifetime | VERIFIED (simulated) |
| INS-20 | Statement scope: personal convention vs testable claim | p.14 | M | Partner-scoped conventions (word channel) vs reference-verified mechanism; `actor` and `root` recorded | PARTIAL: no free-text statements, so no explicit scope field |
| INS-21 | Request a demonstration | p.2 | M | Executive `ask` / `observe` (the brief's executive set has no separate verb) | VERIFIED (mapped) |
| MEM-11 | Measure recall after rebuilding | p.14 | M | Checkpoint restore gives identical beliefs; recall from packed graphs after dataset loss | VERIFIED |
| OPS-09 | Thermal throttling can pause writes | p.14 | M | `InstanceConfig.max_thermal_celsius` | VERIFIED (unit test with fake thermal zone) |
| OPS-10 | Device-specific latency budget | p.19 | M | Latency reported, not judged: no device budget has been declared by the owner | OPEN: needs the owner's budget |

## WEB: Web Cortex (0.6 directive, "Autonomous Internet Intelligence")

Source: the owner's Web Cortex directive (2026-11-20), cited by section `§N`. Design: `docs/web-cortex/ARCHITECTURE.md`; decisions ADR-021…030. Evidence keys refer to `docs/quality/evidence/webcortex_bench.json` (offline), `webcortex_live.json` (live) and `webcortex_scenario_c.json`. Results: `docs/web-cortex/TEST_RESULTS.md`.

| ID | Requirement | Src | Cl | Implementation | Verification | Status |
|---|---|---|---|---|---|---|
| WEB-01 | Network access only when the owner enables it; conservative defaults | §10 | M | `state.require_enabled`; `WebConfig` defaults off (ADR-021) | test_state, test_cli (refusals while disabled) | VERIFIED |
| WEB-02 | Multi-engine key-free discovery with health checks and fallback | §1A, §2B | M | open-webSearch 2.2.0 loopback daemon; per-engine breaker; fallback across engines and structured APIs (ADR-025) | test_openwebsearch, test_search; live run (engine failures reported, not hidden) | VERIFIED |
| WEB-03 | Structured APIs before scraping (arXiv, OpenAlex, Crossref, PubMed, PyPI, npm, OSV, GitHub, Wikipedia) and feeds | §1, §2C | M | `providers/structured.py`, `providers/feeds.py` | test_providers; live probes | VERIFIED |
| WEB-04 | Query decomposition, expansion, temporal/site/kind routing, refinement | §2B | M | `intent.plan`, `intent.refine` | test_intent | VERIFIED (rule-based; no translation: multilingual queries are sent as written) |
| WEB-05 | Decide when web access is necessary; avoid searching stable, well-supported knowledge | §2A | M | `intent.assess` (`needs_web`, `covers`, freshness of known items) | test_intent; test_orchestrator memory path | VERIFIED |
| WEB-06 | Cheapest reliable acquisition ladder; bounded crawling only when needed | §2C | M | API record → feed → HTTP page → extraction → JS rendering escalation → bounded same-site following; `nsf web crawl` for named sources | test_orchestrator; test_crawl | VERIFIED |
| WEB-07 | Scrapling-based structured extraction; Markdown; PDFs; article extraction measured | §1B | M | `extract.py` (Selector parser only), markdownify, pypdf; trafilatura optional | test_extract; bench extraction F1 (scrapling vs trafilatura) | VERIFIED |
| WEB-08 | Sandboxed JavaScript rendering, every request through the safe fetcher | §1B, §10 | M | `browser.py` (ADR-030) | test_browser (real Chromium); live render check | VERIFIED |
| WEB-09 | SSRF/DNS-rebinding protection, redirect re-validation, protocol/port/size/time/type limits | §10 | M | `net.SafeFetcher` | test_net; bench `ssrf` 11/11; security review probes | VERIFIED |
| WEB-10 | robots.txt, rate limits, Retry-After, no anti-bot circumvention, no auth/paywall bypass | §10 | M | RFC 9309 matcher; per-host intervals; shared backoff; no `scrapling[fetchers]` (ADR-024) | test_net robots tables; live robots probe | VERIFIED (search daemon's User-Agent: see DEPENDENCY_REVIEW §3) |
| WEB-11 | Retrieved content is untrusted; prompt injection detected and contained; never elevated | §2E, §10 | M | `safety.scan_injection/quarantine`; adversarial sources weight 0; MCP envelopes | test_safety, test_claims adversarial, bench injection 6/6, e2e console injection | VERIFIED (regex detection: limits in SECURITY_MODEL) |
| WEB-12 | No private memory/credentials in queries; minimised disclosure; self-references local | §10 | M | `safety.privacy_screen`; evidence roots and private terms; `intent.strip_local`; per-task owner authorisation | test_safety, test_orchestrator privacy, test_intent local context | VERIFIED |
| WEB-13 | Public research cache separate from private memory; retention | §10 | M | `<stem>.webcache.sqlite`, no queries, prune by size and age (ADR-023) | test_cache, test_scheduler retention | VERIFIED |
| WEB-14 | Claim extraction, provenance, claim-to-source traceability | §2D, §4 | M | `claims.extract_claims`; `web_sources`, `web_claims`, `web_claim_links` | bench citation support (verbatim); test_claims | VERIFIED |
| WEB-15 | Corroboration with source independence; rank/popularity never establishes truth | §2D | M | independence groups (domain, near-duplicates); Beta posterior; rank excluded | test_claims (rank has no effect) | VERIFIED |
| WEB-16 | Contradiction detection and calibrated conclusions preserving uncertainty | §2D, §12E | M | `claims.contradictions`; verdicts; contested status | bench contradiction P/R (detector F1 1.0, end to end 0.92); verification accuracy 0.75; live Scenario E | PARTIAL: numeric, version, date, polarity and opposite-word conflicts only (ADR-022). Open: true/false discrimination 0/2 and supported recall 0.4 under the undated-source rule (ADR-026; PRODUCTION_READINESS R-2) |
| WEB-17 | Temporal model: event/published/retrieved/verified/valid-from/valid-until; supersession | §4 | M | `temporal.py`; `web_knowledge` versions | test_temporal, test_knowledge | VERIFIED |
| WEB-18 | Freshness policy classes; staleness; disclose unmet freshness; never call cache live | §4 | M | `temporal.FRESHNESS`, overrides, release/revision invalidation; report `live`, `freshness` | bench freshness 8/8, live flag | VERIFIED |
| WEB-19 | Common research orchestration: objective, budget, state, evidence, completion, cancel, checkpoint, resume, partial results | §3 | M | `orchestrator.py` | test_orchestrator; bench cancel/resume | VERIFIED |
| WEB-20 | Research modes (quick, deep, continuous, comparative, scientific, technical, change detection, verification, gap resolution) | §3 | M | modes in `intent`/`orchestrator`; continuous via monitors, gap resolution via curiosity | test_intent, test_synthesis per mode | PARTIAL: continuous, change detection and gap resolution are realised through monitors and curiosity, not separate orchestrator paths |
| WEB-21 | Budgets: requests, pages, tokens, time, concurrency, storage, bandwidth, browser sessions, depth | §7 | M | `types.Budget`/`Usage`; ceiling clamp; daily autonomous caps; cache size cap; token-bounded context packs | test_types/orchestrator budgets; bench budget violations 0 | VERIFIED |
| WEB-22 | Integration with existing memory: working, episodic, semantic, procedural, temporal, knowledge graph | §5 | M | migration 6 tables; `memory.retrieve` kinds; context packs | test_instance_integration, test_knowledge | VERIFIED |
| WEB-23 | Controlled consolidation; raw evidence vs claims vs accepted knowledge; owner approval through the executive | §5 | M | `consolidate.run`; `pending` `web:*`; `nsf resolve` (ADR-026) | test_consolidate, test_instance_integration | VERIFIED |
| WEB-24 | No model-weight changes from web content | §5 | M | Web Cortex writes no belief, graph or weight table | test_consolidate (belief_revisions unchanged) | VERIFIED |
| WEB-25 | Forget, archive, refresh, reject externally acquired knowledge; erasure reaches web memory and checkpoints | §9, §10 | M | `governance.forget_research/knowledge/source`; web tombstones replayed on restore | test_governance web cases, restore no-resurrection test | VERIFIED |
| WEB-26 | Prediction verification and prediction-error detection | §6 | M | `predictions.py` | test_predictions via scheduler/knowledge tests | VERIFIED |
| WEB-27 | Curiosity: gaps, hypotheses, information-gain ranking, metacognitive feedback; bounded | §6 | M | `curiosity.rank/propose` (expected information gain, noisy-subject damping), `web_feedback` | test_curiosity | VERIFIED |
| WEB-28 | Autonomous scheduler integrated with executive control; observable, interruptible | §7 | M | `scheduler.tick/work`; proposals need owner confirmation; regulation honoured (ADR-027) | test_scheduler | VERIFIED |
| WEB-29 | Continuous monitoring with meaningful-change detection | §12D | M | monitors, feeds, seen-keys rule, findings as owner questions | test_scheduler; live Scenario D | VERIFIED |
| WEB-30 | Software intelligence: actual dependency versions, releases, advisories; never installs | §12C | M | `deps.py`, `nsf web deps` | test_deps; live Scenario C | VERIFIED |
| WEB-31 | Scientific investigation: literature, findings vs interpretations, methodology, architecture implications as proposals | §12B | M | scientific mode; `bench/implications.py` | test_synthesis; live Scenario B | PARTIAL: implications are a keyword mapping to the documented architecture, labelled heuristic |
| WEB-32 | Console research UX: stages, sources, dates, conclusions, contradictions, confidence, cancel, history; knowledge awareness | §9 | M | console Research and Knowledge views | tests/console/test_console_web.py; e2e test_console_research | VERIFIED |
| WEB-33 | Pocket: initiate research, live progress, citations, cancel, saved findings offline, dictation, shared URLs, notifications, read aloud | §8 | M | `pocket/server.py` research actions; `research-ui.mjs` (ADR-028) | tests/test_pocket_research.py; e2e test_pocket_research_e2e | VERIFIED (headless Chromium and UAT retests; no handset; notifications only while the app is open: no push server) |
| WEB-34 | Multi-device continuity: stable task identity, permission-aware sync, no private session leakage | §8, §12F | M | family-scoped tasks; owner sees all | Scenario F end-to-end test | VERIFIED (in-process devices, fixture web) |
| WEB-35 | Spark executes; devices are access points; execution placement recorded | §8, §11 | M | worker service; `executed_on` | test_orchestrator | VERIFIED |
| WEB-36 | Distributed execution across enrolled devices, compute-aware routing | §8 | P | Not built: there is one executing node (ARCHITECTURE §7) | — | DEFERRED (ADR-031) |
| WEB-37 | MCP tool contract, read-mostly | §1, §10 | M | `nsf web mcp` | test_mcp (subprocess) | VERIFIED |
| WEB-38 | Async I/O, concurrency, per-domain throttling, coalescing, conditional GET, caching | §11 | M | thread-parallel search and prefetch; per-host intervals; coalescing; ETag/Last-Modified | test_net, test_search | VERIFIED |
| WEB-39 | GPU not used for ordinary retrieval | §11 | M | no model stages (ADR-022) | review | VERIFIED |
| WEB-40 | Benchmark corpus with known answers, time-sensitive, conflicting and malicious content; measured baselines | §14 | M | `webcortex.bench`, `nsf web bench` | evidence JSON; TEST_RESULTS | VERIFIED (fictional corpus; baseline and final measurements on different corpus sizes: not a same-corpus comparison) |
| WEB-41 | Deployment configuration, health checks, recovery procedures | §15 | M | systemd units, `install.sh --web`, `nsf web status/health`, RUNBOOK web section | systemd-analyze verify; dry-run | IMPLEMENTED (not run as services) |
| WEB-42 | LLM-based synthesis and reasoning over open text | §2, §3 | P | No model on the host (ADR-022); contracts allow one later | — | DEFERRED (decisions-pending) |

## DEF: explicitly pending in the brief (not built, by design)

Production neural encoders, general language, open-world reasoning, autonomous lifelong graph revision, physical Spark commissioning (p.19). Free-form dialogue and replies that combine memories with the world model (p.20, "proposed"). MediaProjection and screen assistance, iOS broadcast (p.32). Screen Time / FamilyActivityData (p.32). Field trial (p.32). The brief itself lists these as pending milestones. They are recorded here so they cannot disappear.
