# Cognitive architecture audit (deliverable A)

Status: 2026-11-25. Phase I of Project Cognitive Genesis, deliverable A of the directive's §13. Read-only discovery,
merged from three audits made at `9bc375a` (the cognitive core; the Web Cortex and inference; the runtime) and
brought to the code at `76a478e` (branch `cognitive-genesis`, Wave 0 integrated). Line numbers are those of
`76a478e`: each audit reference was mapped through the diff from `9bc375a`, and a sample was read in the code (§9).
Paths are relative to `src/neuroscifi/` unless they start with `tests/`, `docs/`, `scripts/` or `deploy/`.

Status key, as the directive asks (§3):

| Code | Meaning |
|---|---|
| **IT** | implemented and tested (a test file in `tests/` exercises it) |
| **P** | partial |
| **H** | heuristic (fixed hand rules or weights where the directive asks for a model) |
| **PD** | present but disconnected (computed or stored, read by nothing that acts) |
| **M** | missing |

"Duplicated" and "uninstrumented" are noted in the row where they apply. **unverified** marks what the audits took
from documents without confirming it in code. The audits did not run the test suites; test counts are `def test_`
greps unless a lane report measured them.

## 1. What the system is

| Part | What it is | Evidence |
|---|---|---|
| Cognitive core | One individual is one SQLite file. Its model is two independent parts: (a) a binary calibration model with exact Bayesian inference over hidden bits (the mechanism `w`, one alignment bit per camera root, one convention bit per word partner, reference roots with an integrated-out fixture polarity); (b) a linear population-dynamics graph `x_{t+1} = A x_t + noise`, learned by a windowed delta rule and promoted through a gate | `instance/store.py:153,404`; `instance/beliefs.py:1-41,579-617`; `instance/learner.py:100-139`; `instance/graphs.py:205-439` |
| Executive | Seven fixed rules turn (a) into one recommendation per call. Nothing is actuated | `instance/executive.py:108-185` |
| Web Cortex | A research pipeline: intent, search and read, claims, verification, synthesis, a model answer held by a deterministic sentence guard, knowledge, hypotheses, predictions, settlement, calibration, autonomy | §2, `webcortex/` |
| Runtime | Inference gateway, resource monitor and policies, idle/wake, voice, Pocket, console, Presence. Processes coordinate through the SQLite file and small files in `<db>.resources/`; there is no bus | §5 |
| Isolation of the core | No outer layer (webcortex, voice, presence) imports beliefs, executive, graphs or recall. They use only `store`, `governance`, `checkpoints` and `errors` from `instance` | audit-core §0 (every `from ..instance` import checked) |

Triggering in the core: nothing runs on its own. The sweep runs lazily on access: before every CLI command except
`sweep`/`config` (`instance/cli.py:132-137`), on every console POST, and at most once a minute on a GET
(`console/server.py:832-862`). `observe` does not call `decide`. Decisions are pulled by CLI, console or acceptance
(`console/server.py:134,139`).

## 2. Stimulus → Perception → Attention → Context → Memory → Prediction → Reasoning → Decision → Action → Feedback → Learning

| Stage | Subsystem | What the code does | Status |
|---|---|---|---|
| Stimulus | Core | The owner declares one bit per event: `evidence.observe` (`instance/evidence.py:144`), called from `instance/cli_evidence.py:24` and `console/server.py:149`. Population streams through `evidence.attach_stream` (`instance/evidence.py:117`), datasets through `datasets.add`/`simulate` (`instance/datasets.py:79,119`). Every dataset path in the product is simulated (`graph_bench.world`). No live sensor | P (owner-declared; simulated) |
| | Web | `orchestrator.submit` (`webcortex/orchestrator.py:382`), `prepare` (`webcortex/orchestrator.py:442`); autonomous: `autonomy.idle` (`webcortex/autonomy.py:774`), `scheduler.tick` (`webcortex/scheduler.py:411`), `predictions.check_due` (`webcortex/predictions.py:145`); monitors from the tick. Tasks carry an origin, a mode and a `Budget` | IT |
| | Runtime | Voice sessions (`voice/state.py:36`), Pocket requests, console requests | IT |
| Perception | Core | `evidence.normalize` (`instance/evidence.py:40`) only validates. The encoder is "manual-bit-encoding" (birth record, `instance/store.py:440-444`). The corrected reading is `bit XOR action` (`instance/beliefs.py:941-945`). `lab/sensory.py` (raw sensors → prototypes → state) is imported by nothing outside `lab/` | H; PD (`lab/sensory`) |
| | Web | Intent by rules (ADR-022): `intent.assess` (`webcortex/intent.py:523`), `intent.plan` (`webcortex/intent.py:900`, at most 8 queries), `intent.refine` (`webcortex/intent.py:1069`, one broader round); claims are lexical: one sentence is one claim (`claims.extract_claims`, `webcortex/claims.py:1037`) | IT (rules) |
| Attention | Core | No attention mechanism. Fixed windows: the change test reads the last 64 learning events (`instance/beliefs.py:61,817`), epoch matching the last 12 (`instance/beliefs.py:67,1130`). The executive's rule order is the only prioritisation | M |
| | Web | Scattered salience signals, no unified score (§3.3). Document salience by `claims.informativeness` (`webcortex/claims.py:1025`) when the objective names no topic | P |
| | Runtime | Presence `RANK` is contention resolution over every subsystem's state (`presence/aggregator.py:45`) for display only | PD |
| Context | Core | Epochs (`evidence.start_epoch`, `instance/evidence.py:70`); graph context per epoch (`epoch_contexts`); continuation links (`executive._record_link`, `instance/executive.py:312`); changepoint segments (`beliefs._model`, `instance/beliefs.py:545`) | IT |
| | Web | Memory first: `knowledge.query` (`webcortex/knowledge.py:279`), then `memory_gate.confirm` (`webcortex/memory_gate.py:42`; the model can only send a question to the web, never the reverse); `synthesis.context_pack` (`webcortex/synthesis.py:2115`), a bounded working-memory pack per task | IT |
| | Runtime | Voice "history" is the last 5 research tasks read from the database (`voice/bridge.py:38,411`); no dialogue state across turns | H |
| Memory | Core | Journal `evidence` (TTL 7 days, `instance/store.py:71`); `evidence_consolidated` (ADR-044, `instance/governance.py:349-367`); `belief_revisions` (`instance/beliefs.py:1289`); `pending`, `decisions` (`instance/executive.py:249-256`); datasets; `graph_revisions`. One read envelope over 14 kinds: `memory.retrieve` (`instance/memory.py:16,218`), by id, scope and time, no association (ADR-009) | IT |
| | Web | Versioned `web_knowledge`, typed edges `web_claim_links`, hypotheses, predictions; lexical BM25 (`webcortex/knowledge.py:279`) and optional embedding + reranker (`webcortex/semantic.py:297`) | IT |
| Prediction | Core | Each reading predicts `w XOR s_r` with error 0.10 (`instance/beliefs.py:57,601`). The change test is a marginal-likelihood gain (`_detect`, `instance/beliefs.py:837-906`). The graph's one-step prediction is committed before the outcome is seen (`instance/recall.py:42-51`; `beliefs._graph_dynamics`, `instance/beliefs.py:1068-1085`) | IT (one step only) |
| | Web | Knowledge: one "still holds at next refresh" prediction per new provisional item (`orchestrator._predict`, `webcortex/orchestrator.py:2870`; `predictions.add`, `webcortex/predictions.py:44-62`), probability = the item's confidence. Hypotheses: `evolution.predict` (`webcortex/evolution.py:240`), the posterior clipped to [0.01, 0.99] | IT |
| Reasoning | Core | Exact enumeration over 2^M mechanism-segment assignments, M ≤ 6 (`beliefs._infer`, `instance/beliefs.py:579`); earlier-setup comparison with a multiple-comparison penalty (`instance/beliefs.py:959,1101`); the graph gate's checks (a)–(f) (`graphs._gate`, `instance/graphs.py:287-370`) | IT |
| | Web | Beta posterior per independent source group (`claims.posterior`, `webcortex/claims.py:1801`); one ADR-035 verdict rule (`synthesis.decide`, `webcortex/synthesis.py:1142`); the model writes only from `bundle` and the guard is the authority (`reasoning.apply` `webcortex/reasoning.py:3263`, `guard` `webcortex/reasoning.py:3012`) | IT |
| Decision | Core | `executive._policy` (`instance/executive.py:108-185`): defer / ask / test_reference / observe / act, then the ask budget (`instance/executive.py:215-229`). `decide(record=False)` is a pure read (`instance/executive.py:265-273`) | IT, rule-based (H) |
| | Web | Only `supported` clusters become `provisional` (`consolidate._meets_bar`, `webcortex/consolidate.py:312`); `accepted` only by the owner (`knowledge.decide`, `webcortex/knowledge.py:464`); autonomous choice by `autonomy.candidates` (`webcortex/autonomy.py:506`) | IT |
| Action | Core | A text recommendation plus a `pending` question; `act` means "test a new action and check its observed consequence" (`instance/beliefs.py:72`). The owner carries it out | M (no actuation, by design) |
| | Web | A research task; findings reach the owner only as pending questions (`autonomy.after_task`, `webcortex/autonomy.py:830`) | IT |
| Feedback | Core | Owner answers: `executive.resolve` (`instance/executive.py:363-423`) records a changepoint (`instance/executive.py:336`), an epoch link (`instance/executive.py:312`) or an answer counter, and bumps `teacher_interventions` | IT |
| | Web | Settlement: `predictions.check_due` → refresh task → `predictions.check` (`webcortex/predictions.py:108`); hypotheses `evolution.observe` (`webcortex/evolution.py:395`), `expire` (`webcortex/evolution.py:477`) | IT |
| Learning | Core | Beliefs recomputed on every read (ADR-006); `record_revision` writes an audit row only when the MAP changes (`instance/beliefs.py:1289-1303`); consolidation at sweep (`governance.sweep`, `instance/governance.py:685`); graphs `learn → evaluate → promote` on owner-triggered jobs (ADR-012). The only parameter learned from outcomes is `answer_prior` (`instance/beliefs.py:1088-1096`) | P |
| | Web | Calibration (Brier, ECE) computed on read (`evolution.calibration`, `webcortex/evolution.py:535`); ADR-046 recalibration (`webcortex/evolution.py:667,695`) read only to order autonomous candidates; futility down-weighting (`curiosity._futility`, `webcortex/curiosity.py:85`). Research-policy weights and per-mode statistics are written and read by nothing (§3.12) | P / PD |

## 3. Capability inventory

### 3.1 Internal-state signals (interoception)

| Signal | Produced at | Consumed by | Status |
|---|---|---|---|
| Belief uncertainty: `p_inverted` per factor, `identifiable` (\|p−.5\| ≥ .37), `unidentifiable[]` with a reason | `instance/beliefs.py:727-749,678,689` | executive rules 5–6 (`instance/executive.py:163-181`); `explain`; `memory._belief_confidence` (`instance/memory.py:61`) | IT |
| Prediction error / surprise: penalised LLR of a flipped suffix, `suspected`, `ambiguous` | `instance/beliefs.py:837-906` | executive rules 2–3 (`instance/executive.py:115-146`) | IT |
| Familiarity: posterior that recent evidence is an earlier epoch | `instance/beliefs.py:1101` | executive rule 4b (`instance/executive.py:80-89,152-162`) | IT |
| Graph prediction error against its validation MSE (novelty of context) | `instance/recall.py:52-54` | a `new_context` pending question (`instance/recall.py:76-91`) | IT (3 tests) |
| Write regulation: journal rows, free bytes, thermal | `store.write_allowed` (`instance/store.py:507-530`) | every writer; executive rule 1 (`instance/executive.py:110-111`); Presence (`presence/aggregator.py:200-210`) | IT |
| Workload / backlog: open questions, evidence rows, consolidated readings, write volume, `state_version`, largest epoch's `compute_ms` | `store.regulation` (`instance/store.py:547-572`) | report only: `nsf regulation`, console overview (`console/server.py:105`), acceptance. Nothing adapts to them | P (sensed, not used) |
| Interruption load (ask budget) | `instance/executive.py:217-229` | the same function (ask → defer) | IT |
| Memory pressure ok/elevated/critical (MemAvailable vs floors, PSI avg10) | `resources/monitor.py:228,551,595` (2 s cache) | `policy.background_gate` (`resources/policy.py:134`), `engine_gate` (`resources/policy.py:148`), idle timeout shrink (`resources/idle.py:597`), Presence (`presence/aggregator.py:214,438`) | IT (`tests/test_resources.py`) |
| GPU memory/util/temperature/power/throttle | `resources/monitor.py:270` (nvidia-smi, 15 s cache) | only `full` samples in `resources.status()` (`resources/__init__.py:55`); gates call `sample()` without `full`, so the GPU never gates (`resources/monitor.py:599`); GB10 memory reads "[N/A]" | P |
| CPU load average | `resources/monitor.py:309` | display only | PD |
| vLLM queue, KV-cache %, TTFT, preemptions | `resources/monitor.py:417` (`/metrics`, 15 s cache) | display; the idle policy reads in-flight/served only | P (never feeds regulation) |
| Model endpoint health | `inference/gateway.py:441` | `/api/llm/status`, `nsf llm status` | IT (`tests/inference/test_inference_gateway.py`) |
| Per-call latency, TTFT, tokens/s, priority | `inference/gateway.py:548-560` → `inference/ledger.py:67` | Presence tokens_30s (`presence/aggregator.py:173`); usage_24h (`inference/ledger.py:88`) | IT (persisted, no text) |
| Context saturation | `inference/gateway.py:494-498` (chars/3.5 estimate, raises `context_overflow`) | the caller only | H |
| In-flight streaming model calls | `presence/activity.py:22` from `inference/gateway.py:528` | Presence (`presence/aggregator.py:420`) | P (process-local: a worker's call is invisible to the console's Presence until its ledger row lands) |
| Voice latency metrics | `voice/hub.py:150` (deque of 200) | `VoiceHub.status`, owner only | P (lost on restart) |
| Worker liveness | heartbeat `<db>.worker` every 10 s (`webcortex/scheduler.py:639`) | console, Presence | IT |
| Change counter `state_version` | `instance/store.py:252` | console header only | PD (a change signal nobody subscribes to) |
| Development counters | `store.bump` at call sites | `inventory()["development"]` only. `replay_updates` is a misnomer: it counts learn, evaluate, promote, lesion, coactivation and recall writes (`instance/graphs.py:261,282,437,684,744`, `instance/recall.py:92`), no replay | P |
| One integrated internal-state record | none: five on-request composites (`resources.status()`, `store.regulation()`, `Gateway.health()` + ledger, `presence.aggregator.derive()`, `VoiceHub.status()`), none persisted, versioned or shared between processes | — | **M** |

### 3.2 Regulation (homeostasis and allostasis)

| Mechanism | Input | Reactive or predictive | Status |
|---|---|---|---|
| Writes pause at journal cap, free-space reserve, thermal limit; reads stay available | `instance/store.py:507` | reactive threshold | IT |
| Ask budget per epoch in a rolling 7-day window | `instance/executive.py:194,226-229` | reactive | IT (`tests/instance/test_executive.py`, 22) |
| Working-memory decay (questions close after 7 days); premise hygiene (MEM-01) | `instance/executive.py:233-248` | reactive | IT |
| Consolidation cap with warnings from 80 % | `instance/governance.py:349-367`, `instance/store.py:337` | reactive | IT (`test_consolidation_limit.py`, 21) |
| Background gate: audio, critical memory | `resources/policy.py:134`, `webcortex/scheduler.py:393,584`, `webcortex/autonomy.py:313` | reactive, binary | IT |
| Engine gate (no Chromium render at critical memory) | `resources/policy.py:148` | reactive | IT |
| Background model slots (1 with audio, else 2) | `resources/policy.py:161` | reactive | IT |
| vLLM priority for background requests | `inference/gateway.py:517`, `inference/config.py:70` | static | IT |
| Owner-first claim order + AUTONOMOUS_DEFER | `webcortex/scheduler.py:566-629` | static rule | IT |
| Worker wake on `PRAGMA data_version` (0.1 s) | `webcortex/scheduler.py:688` | reactive | IT |
| Idle unload / reload on demand | `resources/idle.py:602-718,790,815` | reactive (timeout); not installed by default | IT |
| Read-ahead | `webcortex/config.py:95` (static 4) | static | M (adaptation) |
| Elevated memory pressure; KV cache, queue depth, TTFT drift | shown only | — | M |
| Cost awareness (`compute_ms` of the largest epoch) | `instance/store.py:575` | measured, not acted on | P |
| Anticipation (time to cap, growth rate, predicted load) | — | — | **M** |

Measured at the baseline (`docs/quality/evidence/genesis_baseline.json`): the gate avoided 0 of 3 (development) and
0 of 7 (held-out) memory breaches on the anticipation family's synthetic traces.

### 3.3 Salience and attention

| Signal | Where | Feeds | Status |
|---|---|---|---|
| Core choice | first-match rule list (`instance/executive.py:108-185`); ambiguous partners ranked by \|p−.5\| (`instance/executive.py:169-181`), at least 4 words (`MIN_PARTNER_WORDS`, `instance/executive.py:43`); `test_reference` only with reference evidence | one recommendation | H (no information-gain computation, urgency, consequence or cross-epoch priority) |
| Contradiction | `webcortex/claims.py:1648,1495`; `webcortex/consolidate.py:1185,744` | owner question; hypothesis; curiosity; autonomy weight 1.0 | IT |
| Staleness | `webcortex/knowledge.py:65`, `webcortex/temporal.py:474` | curiosity; memory answers exclude stale items | H |
| Confidence shortfall | `VERIFICATION_MIN_CONFIDENCE=0.72` (`webcortex/claims.py:99`) | hypothesis; curiosity "gap"; guard hedge | IT |
| Novelty / environmental change | a monitor's meaningful change (`webcortex/scheduler.py:207`) | owner question; autonomy `source_change` 0.7 (`webcortex/autonomy.py:432`) | IT |
| Prediction discrepancy | an error in the last 30 days | curiosity importance ×1.5 (`webcortex/curiosity.py:203`) | IT |
| User importance | `curiosity.IMPORTANCE` (`webcortex/curiosity.py:43`); owner records first (`webcortex/autonomy.py:660`) | ranking | H |
| Urgency | freshness class → host cap and TTL (`webcortex/orchestrator.py:1086`) | reading order | M as a general score |
| Memory significance, potential consequence | — | — | M |
| Attention protection | question budget (`webcortex/knowledge.py:405`); most valuable questions first (`webcortex/consolidate.py:1023,1051`) | interruption bound | IT |
| One salience record with its signals | — | — | **M** |

### 3.4 Curiosity

| Aspect | Evidence | Status |
|---|---|---|
| Generators: contested, stale or aging, due predictions, contested never stored, single-source gaps; plus open hypotheses and owner assertions | `webcortex/curiosity.py:186-261`; `webcortex/autonomy.py:577-604` | IT |
| Score: information gain (mutual information, report reliability 0.8; 4 hypotheses for contested); utility = IG × importance / cost; autonomy priority with fixed WEIGHTS | `webcortex/curiosity.py:55,63,177`; `webcortex/autonomy.py:66,611-613` | H (fixed hand weights) |
| Outcome feedback: two "unchanged" investigations below 0.01 bit realised → gain × 0.25^k | `webcortex/curiosity.py:85`; `tests/webcortex/test_curiosity.py:69` | IT (the only learned-from-outcome signal in curiosity) |
| Bounds: `min_gain` 0.05 bits, cooldown 24 h, derivation depth 1, relevance 1/3, daily budgets 20 searches / 60 pages / 1800 s / $1 / 2 concurrent | `webcortex/autonomy.py:58-72,296`; `tests/webcortex/test_autonomy_ranking.py:54,81` | IT |
| Proposals: a candidate becomes a research proposal that needs the owner's yes, within the question budget | `webcortex/curiosity.py:279`; `tests/webcortex/test_curiosity.py:90,120` | IT |
| Goal relevance by word-set Jaccard | `webcortex/autonomy.py:418,422` | H |
| Two admission paths with different rules (timer: `curiosity.MIN_GAIN` 0.02, no goal, lineage or cooldown; idle: the full rule) | at `9bc375a`: `scheduler.tick` vs `autonomy.idle` | **Duplicated at the audit; unified in Wave 0** (ADR-050: `autonomy.admission`, `webcortex/autonomy.py:672`, called from `webcortex/scheduler.py:506`) |
| Measured | web curiosity spent 0.375 (development) and 0.537 (held-out) of its budget on a never-resolving subject; the idle path 0.81 / 0.80 with negative realised gain | `genesis_baseline.json` (`info_value.web_mean`, references `autonomy`) |

### 3.5 Habit and procedural knowledge

| Capability | Evidence | Status |
|---|---|---|
| Recognise a recurring setup | `instance/beliefs.py:1101,959`, executive rule 4b | IT (`test_executive.py`, `test_panel_dynamics.py` 8, `test_near_duplicate_panels.py` 1) |
| Reuse the routine (continue an epoch's calibration) | `epoch_links` + pooling (`instance/beliefs.py:390,712-724`) | IT |
| Detect that a habit no longer applies | `_detect`; graph recall `unknown_context` | IT |
| Scoped relearning (only the changed factor) | `instance/executive.py:336` → `instance/beliefs.py:545` (ADR-017) | IT (`test_scope_learning.py`, 15) |
| Empirical prior over "new versus continue" | `instance/beliefs.py:1088` | IT |
| Reusable routines, skill library, strategy selection; habitual versus deliberate split | — | **M** |

### 3.6 World model, prediction and imagination

| Capability | Evidence | Status |
|---|---|---|
| Bit model: latent binary causes with exact posteriors; no state transitions (time only through changepoint segments) | `instance/beliefs.py:1-41` | IT |
| Graph: linear transition model over anonymous populations, no action input, so no consequence of an action | `lab/graph_bench.py:22-40`, `instance/learner.py:129` | IT (one step) |
| Causal direction: `A[dst,src]` lagged edges; lesion of one edge with a permuted control; coactivation labelled "not connection" | `instance/graphs.py:640-688,691-750` | PD (reported, never used to choose) |
| Multi-step rollout | none in `instance/` (grep "rollout") | M |
| Belief what-if | `_infer` on hypothetical tallies is exact and pure (`instance/beliefs.py:855-886`) | M as a feature; substrate exists |
| Consumers of a promoted graph | `recall.run`, `_graph_dynamics`, `functional_map`, console and CLI. Nothing outside the core | PD |
| Web counterfactual, what-if, scenario | none (grep `counterfactual\|what[- ]if\|scenario\|simulat\|imagin\|speculat\|sandbox`); nearest: 4 templated competing explanations (`webcortex/curiosity.py:157`) and template error analysis (`webcortex/evolution.py:325`) | M |
| Speculative model sentences | refused by the guard (`webcortex/reasoning.py:1566-1568`): filtered, not represented | IT (as a refusal) |
| Provenance kinds for inference, simulation, speculation | none at the audit (`webcortex/evolution.py:35`, `webcortex/state.py:30`). **Wave 0** added five kinds (`provenance.py:49-53`) and a quarantined store (`instance/imagined.py`, schema 17, ADR-049); empty, no mechanism writes to it | IT (store); no writer |
| Simulated readings | `provenance='simulated'` labels data since schema 1 (ADR-014, `instance/evidence.py:15`) but a labelled reading moves a belief like an observation (0.500 → 0.994, adversarial case `simulated_reading_moves_no_belief`, failing at the baseline) | P (open; see [WAVE0.md](WAVE0.md)) |
| Measured | counterfactual coverage 0.107 (development) / 0.071 (held-out): only one-step graph queries are answered, relative error 0.045, always within 2 sd | `genesis_baseline.json` |

### 3.7 Self-model and self-description

| Surface | Reports | Missing | Status |
|---|---|---|---|
| `nsf birth` / `GET /api/inventory` | birth record, memory counts, development counters, warnings, regulation | models, resources, tools, goals | IT (`instance/store.py:592-625`) |
| `GET /api/resources` / `nsf resources` | pressure, GPU, load, containers, vLLM metrics, gate decisions, idle state | not rendered by the console page | PD |
| `GET /api/llm/status`, `/api/web/status`, `/api/web/autonomy` | gateway, web config and health, autonomy switch, budgets, decisions | — | IT |
| `status` / `explain` / `decide` / `pending` | beliefs, evidence for and against, why uncertain, next step, open questions | — | IT (`instance/beliefs.py:800,1206`; `instance/executive.py:265,276`) |
| `GET /api/presence` | primary activity, candidates, condition | goals, capabilities, limits | IT |
| MCP | five tools (`webcortex/mcp.py:51-80`); `SERVER_INFO.version` hard-coded "0.7.0" (`webcortex/mcp.py:34`) | no status or self tool | P |
| Tools the model may use | `inference/tools.py:141-166` | not exposed on any status surface | PD |
| One structured self-model (goals, capabilities, limits, authorised tools) | data scattered over birth, config rows, `autonomy.goals()` (`webcortex/autonomy.py:422`), the tools allow-list and gate reasons | — | **M** |
| Measured | 12 of 15 probes answered (coverage 0.80, accuracy 1.00 when answered) from 4 separate surfaces; never answered: what it is uncertain about, its last decision, the tools the model may use | `genesis_baseline.json` |

### 3.8 Metacognition

| Faculty | Evidence | Status |
|---|---|---|
| Confidence (core) | posteriors; margin .37; `memory._belief_confidence` | IT |
| Calibration (core) | reading errors are constants (`instance/beliefs.py:57-59`, `instance/store.py:60`); nothing learns them | **M** |
| Calibration (web) | Brier, ECE (`webcortex/evolution.py:535`); ADR-046 mapping read only to order candidates (`webcortex/autonomy.py:527,569,587`); change-point detection implemented but off (`RECALIBRATION_CHANGE_THRESHOLD=None`, failed its held-out gate, ADR-048) | IT (simulated evidence only: "no individual has settled predictions yet") |
| Error detection | change test; ambiguity margin of 1 nat (`instance/beliefs.py:63`); recall unknown-context; gate check (f) "worse than no graph" (`instance/graphs.py:342-353`) | IT |
| Verification states | the ADR-035 six-state enum (`webcortex/claims.py:120-132`), one rule (`webcortex/synthesis.py:1142`); the guard never upgrades a state (`webcortex/reasoning.py:712`) | IT (`tests/webcortex/test_shared_verdict.py`) |
| Evidence sufficiency, abstention | `min_groups=2`; `insufficient`; the guard's `insufficiency` (`webcortex/reasoning.py:695`); abstention recorded (`webcortex/reasoning.py:3376`) | IT |
| Self-critique | `webcortex/reasoning.py:3251`, used at `webcortex/reasoning.py:3326-3365`; a flag removes a sentence only when `check_sentence` confirms; Pocket shows it (`pocket/server.py:490`), the console **unverified**; nothing else reads it | IT, effect display-only |
| Source independence / circularity | `webcortex/claims.py:1277,1319` (syndicated copies count once, ADR-035); `autonomy._lineage` (`webcortex/autonomy.py:448`). Every DOI resolves through doi.org into one group (known gap) | IT |
| Decision records with reasons | core `decisions` reason is free text; inputs and alternatives not stored (`instance/executive.py:255`); graph gate decisions store every check (`instance/graphs.py:356-369`) | P (bits) / IT (graphs) |
| Outcome tracking of its own judgements | data exists (a `calibrate` question → changepoint confirmed or rejected); nothing computes it | PD |
| Reasoning-budget adaptation | fixed rules (`webcortex/config.py:31`; `webcortex/reasoning.py:116`; `webcortex/orchestrator.py:2598-2606`) | P (nothing adapts depth from outcomes) |
| Error detection over time | `evolution.metrics` (`webcortex/evolution.py:796`) on request; `--record` snapshots nothing reads back | PD |
| Measured | selective answering: AUROC 0.685 (development), held-out has no wrong answer; claim probability ECE 0.304 / 0.341, Brier 0.095 / 0.122, all of it reliability error (under-confident) | `genesis_baseline.json` |

### 3.9 Temporal continuity

| Mechanism | Evidence | Status |
|---|---|---|
| Event journal with monotone `seq`; beliefs a projection (ADR-006); a seq never reissued after a restore | `instance/evidence.py:166`, `instance/governance.py:352-355` | IT |
| `state_version`, one bump per logical change | `instance/store.py:252` | IT |
| Checkpoints: online backup, verify, restore to a new file, replay of forgets | `instance/checkpoints.py:77,130,160-201,403` | IT (`test_checkpoints.py` 37, `test_checkpoint_rewrite.py` 9) |
| Restart of a research task: lease expiry or dead pid resumes from the stage checkpoint | `webcortex/scheduler.py:566-618`, `webcortex/orchestrator.py:98,2889` | IT |
| Restart of voice: transcripts survive; sessions, events and the follow of a spoken research answer are lost (R8) | `voice/transcripts.py:1-12` | P |
| Device change: mic hand-off between a principal's devices; "continue on another device"; Pocket outbox | `voice/hub.py:15-19`, `pocket/web/outbox.mjs:1-30` | P (console and Pocket are separate principals; no shared conversational context) |
| Long-running: monitors, predictions, hypotheses, autonomy ledger | `webcortex/scheduler.py:53-64` | IT |
| Cross-task working context | — | M |

### 3.10 Knowledge construction

| Aspect | Evidence | Status |
|---|---|---|
| Versions, supersession, contradiction recorded and asked, never decided | `webcortex/consolidate.py:1459-1498,744`; `webcortex/temporal.py:536` | IT |
| Provisional at ≥ 2 independent sources, confidence ≥ 0.72, no contradiction; accepted only by the owner | `webcortex/consolidate.py:312`; `webcortex/knowledge.py:464,583` | IT |
| Temporal validity, source attribution | `webcortex/knowledge.py:27`; `webcortex/evolution.py:135` | IT |
| Graph: typed provenance edges, entities by co-mention only (`webcortex/evolution.py:148`: "not an ontology relation"); read only by `nsf knowledge graph` and `changes` | `webcortex/state.py:30`, `webcortex/evolution.py:163` | P |
| Abstraction (episodic → semantic), concept hierarchy, causal relation extraction | one sentence = one claim = at most one knowledge row | **M** |
| Uncertainty propagation across related items | — | M |

### 3.11 Learning, consolidation, replay (core)

| Item | Evidence | Status |
|---|---|---|
| Evidence lifetime 7 days unless the owner promotes it | `instance/store.py:71`, `instance/governance.py:667` | IT |
| Consolidation (ADR-044): the posterior after expiry equals the one before (exact) | `instance/governance.py:349-367`; `tests/instance/test_consolidation.py:103` | IT (47 tests) |
| Forgetting with scrubbing and tombstones | `instance/governance.py:454-607`, `instance/evidence.py:155` | IT |
| Adaptive forgetting; replay or rehearsal | none in the individual; a "conventional recurrent learner with replay" is only an acceptance baseline (`acceptance/scenarios.py:1213,1482-1519`) | M |
| Graph learning (delta rule, owner-triggered) | `instance/graphs.py:205` | IT |

### 3.12 Learning to learn and recursive improvement

| Mechanism | Evidence | Status |
|---|---|---|
| Research-policy weights: per-provider weight toward 0.5 + yield, ±0.1 per step, revertible; triggered only by `nsf knowledge learning --update`; read by nothing ("Advisory only") | `webcortex/evolution.py:1068,1072,1096,1208`; `tests/webcortex/test_evolution.py:306` | PD |
| Per-mode outcome statistics (`web_strategies 'mode:*'`) | `webcortex/state.py:576`; no reader | PD |
| Futility down-weighting; ADR-046 calibration mapping | `webcortex/curiosity.py:85`; `webcortex/evolution.py:667` | IT |
| Versioned, immutable scoring rules; the learner cannot change its own exam (SQLite triggers) | `instance/rules.py:1-6,68-96`; `instance/schema.py:177-179,235` | IT |
| Graph gate: held-out gain, retention, byte budget, lineage, the unwaivable "beats no graph"; rollback; best-learner selection; recorded reversible migration | `instance/graphs.py:222-258,287-370,399,458`; `instance/learner.py:146-201`; `instance/graph_migration.py:111,161` | IT (`test_graphs.py` 32, `test_graph_migration.py` 17) |
| A policy learned from outcomes | only `answer_prior`; rules and thresholds fixed, "declared before measurement" (`instance/beliefs.py:68-70`) | M |
| Model adaptation | `evolution.model_adaptation_guard` refuses unless owner authorisation, isolated evaluation and rollback are all present; never autonomous; no implementation ships | `webcortex/evolution.py:1116` | guard only |
| Hypothesis fields the directive asks for (§7.2) | supporting yes; counterevidence partial; assumptions **M** (`webcortex/evolution.py:50`); expected information gain, cost and goal relevance computed at ranking, not stored; `abandoned` never set in code (only in the enum) | P |
| Scheduled reflection | nothing runs `metrics`, `changes` or `update_research_policy` on a timer (`webcortex/scheduler.py:411`) | M |
| Transfer measurement | only held-out splits for retrieval, contradiction and calibration settings | M |

### 3.13 Global workspace and event flow

| Channel | Writer → reader | Push or poll |
|---|---|---|
| Instance SQLite (tasks, events, pending, strategies, ledger, voice tables) | every process | readers poll; the worker gets near-push through `data_version` (`webcortex/scheduler.py:688`) |
| `web_events` append log | orchestrator and worker → Presence, console, voice follow | poll (`presence/aggregator.py:146`) |
| `pending` owner questions | autonomy and monitor findings → Presence, console, Pocket push | poll, budgeted |
| `<db>.resources/audio.json` beacon | voice hubs → worker, autonomy, idle | file under flock (`voice/beacon.py:60`) |
| idle and model-slot files | idle/wake ↔ gateway, worker, console | files + flock (`resources/shared.py:1-16`) |
| `*.wake` files | service account → ops account | push (systemd path unit, `resources/idle.py:1216-1276`) |
| Presence | aggregator → console (long poll, re-derive every 0.35 s) and Pocket (poll) | `presence/routes.py:31-98` |

Verdict: a workspace as a mechanism is **M**. As parts it is **PD**: `pending` + `after_task` is already a selective,
salience-gated, budgeted broadcast ("meaningful finding" only, `webcortex/autonomy.py:830-872`), and Presence `RANK`
is contention resolution (`presence/aggregator.py:45`). Presence declares the activities interpreting, planning,
analyzing, predicting, simulating and executing, and the secondaries tool_execution and device_sync, and never emits
them (`presence/aggregator.py:35-39`).

### 3.14 Safety boundaries

| Boundary | Evidence | Status |
|---|---|---|
| Nothing runs without a task, a budget and an origin | `webcortex/scheduler.py:1`, `webcortex/autonomy.py:296` | IT |
| Autonomy opt-in with a recorded owner switch (who, when) | `webcortex/autonomy.py:1-5,136-168` | IT |
| Findings reach the owner only as questions; memory needs owner acceptance | `webcortex/autonomy.py:830`, `voice/bridge.py:15-19` | IT |
| MCP: no forget, config, review or monitor tool; rate limit; daily allowance | `webcortex/mcp.py:7-18` | IT |
| Sensitive voice commands need a confirmation within 60 s | `voice/bridge.py:15-21,30` | IT |
| Remote endpoints and private memory gated | `inference/config.py:58-59`, `inference/gateway.py:224-285` | IT |
| Two accounts; the ops account reads the database read-only | `resources/shared.py:1-16,168-213` | IT (`test_deployment_accounts.py`, `test_idle_hardening.py`) |
| One owner switch for all background cognition | at the audit: missing (it took `web autonomy disable` + `--no-autonomous` + monitor pauses + disabling timers). **Wave 0** added `quiet` (`background.py:164-183`, ADR-050) | IT since Wave 0 |

### 3.15 Background scheduling (what runs when nobody asks)

| Runner | Trigger | Does | Bounded by | Owner stops it by |
|---|---|---|---|---|
| `nsf web work` / `neuroscifi-webcortex.service` / `console --web-worker` | loop; `data_version` wake; 5 s poll | claims queued tasks; resumes dead leases; when empty, `autonomy.idle` queues one investigation per `idle_interval` | write pause, background gate, the daily autonomous budget, `min_gain`, cooldown, one step from owner records | `nsf web autonomy disable`; `nsf web config --disable`; task cancel; stopping the unit; since Wave 0 `nsf quiet on` |
| `neuroscifi-webcortex-tick.timer` (every 15 min) → `nsf web tick` | timer | due monitors; if `autonomous`: due prediction refreshes, then curiosity on items already in knowledge; cache retention | `_over_cap` per launch, `max_tasks`, MemoryMax=2G, Nice 15 | monitor pause/stop; `--no-autonomous`; disabling the timer; since Wave 0 `nsf quiet on` (retention keeps running) |
| `neuroscifi-idle.timer` (1 min) → `nsf resources tick` | timer, installed disabled | unloads idle services | opt-in timeouts; voice, task and in-flight blockers | `nsf resources idle --set X=off`; disabling the timer |
| `neuroscifi-wake.path` | a `*.wake` file | reloads a service | headroom check, StartLimitBurst 30 per 10 min | disabling the path unit |
| `neuroscifi-checkpoint.timer` 03:15 | nightly | sweep and cold checkpoint | PrivateNetwork, MemoryMax 4G, Nice 19 | disabling the timer |
| Voice hub sweep, Pocket push | on requests every 60 s; a thread every 5 s | expire retained audio, end idle sessions; empty pushes, one per minute per device | — | no `--push-key` (push) |
| `nsf start` (`start_cli.py:22`) | foreground | runs the worker, console and Pocket only: no tick, no idle timer | Ctrl-C stops all | Ctrl-C |

Evidence: the tick's knowledge-only scope (since Wave 0 inside `autonomy.admission`, `webcortex/autonomy.py:697,724`), `webcortex/autonomy.py:31-37`, `deploy/systemd/` units (`tests/test_deploy_units.py`).

### 3.16 Evaluation coverage (benchmarks that existed before Wave 0)

| Benchmark | Measures | Held-out discipline |
|---|---|---|
| `webcortex/bench/offline.py` + `corpus.py` (`nsf web bench`; never run without restoring its evidence file) | the full pipeline on a 61-page fictional web: extraction, dates, verification, contradictions, citations, memory answers, consolidation, budgets, injection, determinism | gold labels written with the pages, "never derived from the system's output"; determinism across repeats |
| `webcortex/bench/calibration.py`, `scripts/bench_calibration.py` | prequential Brier and distance to the true p of simulated forecasters | settings chosen on development streams, reported on held-out streams |
| `scripts/bench_memory.py` | recall@1/3, MRR, none-share on 48 facts × 4 query classes | development and held-out subject halves |
| `scripts/bench_knowledge.py` | index cost; contradictions in other words | held-out set "used once" |
| `scripts/bench_pipeline.py` | the live model on the offline corpus: guard kept or rejected, critique marks, time split | none (fictional corpus) |
| `scripts/bench_inference.py` | the endpoint: latency, prefix cache, 16 reasoning problems, 8 tool calls, long context | fixed workload |
| `scripts/guard_differential.py` + `tests/inference/test_guard_differential.py` | every verdict against `307b907` over about 1,937 inputs | reference recorded at a fixed ref |
| Acceptance scenarios (`acceptance/scenarios.py:1945-1953`) | `birth`, `grounding`, `lifelong_correction`, `graph_development`, `growth_retention`, `device_ownership`, `first_physical_test`; deterministic per seed | `tests/acceptance/test_acceptance.py:20-236` |

Missing before Wave 0, for the directive: curiosity value, hypothesis-revision accuracy, ablations of salience and
recalibration on real outcomes, transfer, self-model accuracy. Wave 0's cognitive benchmark (lane Z) covers
curiosity value, counterfactual validity, self-description and transfer on synthetic worlds; real outcomes remain
unmeasured.

## 4. Dependencies

| Dependency | What depends on it |
|---|---|
| The instance file (`instance/store.py`) | every process: core, worker, console, Pocket, voice; the ops account read-only |
| `store.write_allowed` | every writer in the core and the web pipeline (`state.require_writable`, `webcortex/consolidate.py:1315`) |
| `resources.policy` gates | worker claims, autonomy admission, idle pass |
| The answer model (vLLM behind `inference/gateway.py`) | the model answer and self-critique; the guard and extractive answer do not need it |
| The offline corpus and benchmarks | `nsf web bench` (never run without restoring its evidence), the cognitive benchmark's selective and retention families |
| `background.allowed` (Wave 0) | every registered cognition activity (worker claims, idle investigation, tick monitors, refreshes, curiosity, a running background task) |

## 5. Operational limitations

- The core has no live sensor on the Spark: bits are declared by the owner, datasets are simulated.
- No outer layer uses the core's beliefs, executive or graphs.
- Regulation is reactive only; elevated pressure, GPU, queue depth, KV saturation and latency trends protect nothing.
- `nsf start` runs the worker, console and Pocket only: no tick, no idle timer (`start_cli.py:22`).
- The Presence client describes `resource_constrained` as "writes paused to protect storage"
  (`presence/web/interpreter.mjs:27`), which is wrong when the cause is memory pressure.
- Voice follow-up of a spoken research answer is lost on restart (R8).
- Calibration's evidence is simulated: no individual has settled predictions yet.

## 6. Invariants any change must keep

| Invariant | Where enforced or tested |
|---|---|
| Beliefs are computed on read; no belief cache; `belief_revisions` is an audit trail | ADR-006; `instance/beliefs.py:25-27` |
| Legacy equality: with ≤ 1 camera root, ≤ 1 partner, no change and no link, `compute()["legacy"]` equals the prototype (test tolerance 1e-12) | `instance/beliefs.py:15-16,636-641`; `tests/instance/test_beliefs.py:48-69` |
| Posterior, segments and decision reason identical (`==`) before and after expiry and consolidation | `tests/instance/test_consolidation.py:99-115` |
| Exact inference, M ≤ 6 segments | `instance/beliefs.py:66`, `instance/executive.py:351-354` |
| Every writer uses `with store.write(db)` and bumps `state_version` once per logical change; time passed in as `now` | `instance/store.py:1-10,187-201` |
| `decide(record=False)` writes nothing | `instance/executive.py:231,265-273` |
| Append-only migrations; never edit a released migration; tests locate a migration by its content | `instance/schema.py:1-7`; `test_migrations.py` |
| Forgetting cascades and scrubs; restore never overwrites and re-applies every forget | ADR-019, ADR-048; `instance/governance.py:200-607`; `instance/checkpoints.py:1-21,160-207` |
| The answer path equals `307b907` except stricter or the one proven loosening: 0 "different" | `scripts/guard_differential.py`, `tests/inference/test_guard_differential.py` |
| Model text never becomes knowledge; the model never sets or upgrades a verification state | `webcortex/consolidate.py:1325-1335`, `webcortex/reasoning.py:712` |
| The owner's stop holds before every network step | `webcortex/orchestrator.py:221,749`; `test_stops_elsewhere.py` |
| Autonomy opt-in, `min_gain` on the stated forecast, depth ≤ 1, relevance ≥ 1/3, owner records first; ADR-046 only orders | `webcortex/autonomy.py:631-660`; `tests/webcortex/test_recalibration.py:307,337` |
| Two accounts, no new listeners (console 127.0.0.1:8766), bounded reads | `resources/shared.py`, `console/server.py:4,32` |
| Interactive latency: hot-path gates cost at most one cached `/proc/meminfo` read | `resources/monitor.py:32-34`, `voice/hub.py:25-27` |
| Presence never invents activity; every candidate names its source | `presence/aggregator.py:1-6`, `tests/presence/test_visual_rules.py` |
| Nothing imagined is evidence (Wave 0): triggers refuse an imagined id in every reference column; only an allow-list of modules touches the store | ADR-049; `tests/instance/test_imagined.py`, `tests/webcortex/test_imagined_quarantine.py` |

## 7. Extension points the audits found (low risk, no change to memory semantics)

| # | Extension | Attach at | Addressed by |
|---|---|---|---|
| 1 | Read-only internal-state snapshot | beside `store.regulation` (`instance/store.py:547`); `resources.status()` (`resources/__init__.py:55`); a state file in `<db>.resources/` | lane I |
| 2 | Exact expected information gain per candidate question, in shadow mode | `executive._policy` / `_decide`, using the `factors(delta)` pattern (`instance/beliefs.py:855-886`) | lane I |
| 3 | Imagination sandbox on an in-memory copy | the acceptance `_fork` pattern (`acceptance/scenarios.py:668`) | lane M |
| 4 | k-step graph forecast with an error band from `validation_mse` | beside `graphs.show` (`instance/graphs.py:762`) | lane M |
| 5 | Self-calibration report of the change detector from existing records | read-only over `pending`, `changepoints`, `decisions` | lane C |
| 6 | One admission rule for both autonomy paths | the tick's curiosity loop (lines 473-499 of `webcortex/scheduler.py` at `9bc375a`) → `webcortex/autonomy.py:506` | done in Wave 0 (lane S) |
| 7 | Provenance kinds and an isolated table for imagined content | `webcortex/evolution.py:35`, `webcortex/state.py:30` | done in Wave 0 (lane P) |
| 8 | Hypothesis fields: assumptions, stored expected gain and cost, `abandoned` | `webcortex/evolution.py:276,477` | lane K |
| 9 | Scheduled bounded reflection producing records and questions | after retention in `scheduler.tick` | lane K |
| 10 | Unified salience record logged with the decision | `webcortex/autonomy.py:530-553` (`signals`) | lane I |
| 11 | One self-model view; read-only MCP `self_status` tool | `/api/inventory`; `webcortex/mcp.py` `_tools()` | lane W |
| 12 | Emit Presence's unused activities from real rows; fix the memory-pressure text; render `/api/resources` | `presence/aggregator.py`; `presence/web/interpreter.mjs:27` | lane W |
| 13 | A staged cognitive benchmark with development and held-out worlds | `webcortex/bench/offline.py`, `webcortex/bench/calibration.py` pattern | done in Wave 0 (lane Z) |

Higher risk (they change memory semantics; owner decision and ADR needed): learned reading-error rates (they change
every posterior and break legacy equality); replay that reweights evidence; acting on graph forecasts automatically;
an event bus inside `store.write`.

## 8. Not verified in code

- The ADR-044 cost numbers and the earlier gap analysis's measured numbers (taken from documents by the core audit).
- Whether the console shows the self-critique flag.
- The grayscale rule in CSS (only the tone list in `presence/scene_spec.py` was read).
- Pocket outbox behaviour on real handsets.
- The exact console line of `_llm_status`.
- Test counts are `def test_` greps; no audit ran the suites.

## 9. References checked

The three audits were written at `9bc375a`. Between that commit and `76a478e`, 37 source files changed (Wave 0 and
the end of the remaining-items round). Every `file:line` above was mapped mechanically through the diff of its file
(`difflib` over the two versions; a line inside an unchanged block keeps its text and moves with it). 76 references
were then read at both commits against the symbol the audit named:

| Result | Count | Detail |
|---|---|---|
| The named symbol is on the mapped line at `76a478e` | 72 | e.g. `executive.decide` 265, `beliefs._infer` 579, `store.regulation` 510 → 547, `autonomy.idle` 685 → 774, `orchestrator._reason` 2520 → 2565, `reasoning.guard` 3008 → 3012, `scheduler.claim_next` 522 → 566, `evolution.model_adaptation_guard` 1113 → 1116 |
| Line right, wording of the claim checked by reading | 3 | `instance/recall.py:47` is the prediction committed before the outcome (the `states @ A.T` product is inside `GraphRecall`); `webcortex/scheduler.py:688` is `wait_for_queue`, the `data_version` wait; `resources/__init__.py:56` is the docstring of `status`, defined on line 55 (cited as 55 here) |
| Corrected | 1 | the memory-pressure text is on `presence/web/interpreter.mjs:27`, not 28 |
| Changed by Wave 0 | 1 | the tick's curiosity loop (line 473 of `webcortex/scheduler.py` at `9bc375a`) is now the call of `autonomy.admission` (`webcortex/scheduler.py:506`); cited as such |

Also confirmed in the code at `76a478e`: `abandoned` appears in `webcortex/evolution.py` only in the state enum;
`research_policy` is called only inside `webcortex/evolution.py`; the MCP server lists exactly five tools; Presence's
predicting, simulating and planning appear only in its vocabulary tuple.
