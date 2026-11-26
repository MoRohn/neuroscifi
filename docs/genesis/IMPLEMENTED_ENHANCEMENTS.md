# Implemented enhancements (deliverable E)

Status: 2026-11-25, on the Wave 2 integration tree `828ca8e` (branch `genesis-wave2`). Deliverable E of the
directive's §13: an index of what Project Cognitive Genesis actually built in Waves 0, 1 and 2, one row per mechanism,
with where the code is, the setting and the value a new individual gets, the decision record, the tests, the evidence
and how to take it back out. Nothing here is a claim of consciousness, awareness or general intelligence; every name
describes a function.

## In plain words

- **Most of what was built is switched off or only records and displays.** At the shipped settings the individual
  researches, answers and believes exactly what it did before Project Cognitive Genesis: the offline research
  benchmark gives the same result digest as before Wave 1 (`68731d7235cb…`, [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) §6).
- **What is on by default** is: records (the internal-state history, the salience record, the monitoring record,
  hypothesis details, "to re-check" marks, the arbitration record), displays (the information gain beside a decision,
  one self-description record on every surface, Presence's planning / predicting / simulating states), the
  imagination commands an owner types (`nsf imagine …`, computed on a private copy), and the voice follow-up across a
  restart.
- **Everything that would change behaviour ships off**, because no benchmark showed a held-out gain larger than the
  baseline's spread without a cost (see [BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md)).
- **Wave 2 built almost no mechanism.** It added one record (arbitration, on), one ordering rule (salience attention,
  off), one display field, and 14 small guards that keep the product working when a Wave 1 mechanism fails.

How the defaults below were read: from the code on `828ca8e`, not from other documents: `InstanceConfig` in
`src/neuroscifi/instance/store.py`, `WebConfig` in `src/neuroscifi/webcortex/config.py` and `DEFAULTS` in
`src/neuroscifi/voice/config.py`. "Owner command" means the command to change it; the full list is in
`docs/operations/RUNBOOK.md` ("Cognitive mechanisms of Wave 1" and the Wave 2 table after it).

## 1. Wave 0: the instrument and the safety prerequisites

Record: [WAVE0.md](WAVE0.md). No cognitive mechanism; no cognitive gain claimed.

| Mechanism | Module(s) | Setting: shipped default | ADR | Tests | Evidence | How to back it out |
|---|---|---|---|---|---|---|
| Cognitive benchmark: seven families on frozen development / held-out worlds, a withheld family scored once with `--final`, the ledger of final runs | `scripts/bench_cognition.py`, `src/neuroscifi/acceptance/cognition/` | none (measurement only; nothing in the product imports it) | — ([COGNITIVE_BENCHMARK.md](../quality/COGNITIVE_BENCHMARK.md)) | `tests/acceptance/test_cognition_bench.py` | `genesis_baseline.json`, `genesis_final_uses.jsonl` | revert its commits; nothing in the product reads it |
| Five provenance kinds; the quarantined imagined store (inference, simulation and speculation kept apart from evidence; database triggers; an allow-list scan of every module) | `src/neuroscifi/provenance.py`, `src/neuroscifi/instance/imagined.py`, migration 17 in `instance/schema.py` | `max_imagined_rows` **1000**; `imagined_lifetime_seconds` **7 days** (instance) | ADR-049 | `tests/instance/test_imagined.py`, `tests/webcortex/test_imagined_quarantine.py` | `genesis_provenance.json` (42 of 42 safeguards caught) | `nsf config --max-imagined-rows 0`; `nsf imagined purge --all`; schema 17 is forward-only (its empty tables stay) |
| The owner's quiet switch and the registry every background activity must join | `src/neuroscifi/background.py` | `quiet` **off** (instance) | ADR-050 | `tests/webcortex/test_background.py` | `genesis_safety.json` (14 of 14 safeguards caught) | `nsf quiet off` (the default) |
| One admission rule for both autonomous research paths | `webcortex/autonomy.py` (`admission`), `webcortex/scheduler.py` | `tick_admission` **`"unified"`** (web) | ADR-050 | `tests/webcortex/test_autonomy.py`, `test_scheduler.py` | `genesis_safety.json` (`admission`) | `nsf web config --set tick_admission='"legacy"'` restores the old rule exactly |

## 2. Wave 1: the mechanisms (lanes I, M, W, C, K)

Record: [WAVE1.md](WAVE1.md) (the defaults §2, the benchmark §4, the reviews §6, the withheld look §8, backing out
§10). Evidence common to every row: `genesis_wave1.json` (split seeds), `genesis_wave1_wide.json` (12 further seeds),
`genesis_wave1_withheld.json` (the one withheld look).

### 2.1 Instinct: regulation and prioritisation (lane I, ADR-051)

| Mechanism | Module(s) | Setting: shipped default | Tests | Lane evidence | How to back it out |
|---|---|---|---|---|---|
| Internal-state record and its bounded history (numbers only, every 30 s, 720 kept) | `internal_state.py` | `internal_state_history` **on** (record; instance) | `tests/test_internal_state.py`, `tests/test_untrusted_history.py` | `genesis_instinct.json` | `nsf config --no-internal-state-history` |
| Predictive regulation: the background gate also refuses NEW background research while a forecast says memory headroom or a regular voice session is near | `resources/forecast.py`, `resources/policy.py` | `predictive_regulation` **off** (instance) | `tests/test_predictive_wiring.py`, `tests/acceptance/test_cognition_instinct.py` | `genesis_instinct.json` | leave off; `nsf config --no-predictive-regulation` |
| Salience record per autonomy candidate (one explained score, logged with the decision; changes no admission and no order) | `webcortex/salience.py`, `webcortex/autonomy.py` | `salience_record` **on** (record; web) | `tests/webcortex/test_curiosity_progress.py::test_every_candidate_carries_an_explained_salience_that_changes_no_order` | `genesis_instinct.json` | `nsf web config --set salience_record=false` |
| Curiosity by learning progress (down-weights subjects whose looks realised nothing) | `webcortex/curiosity.py` | `curiosity_learning_progress` **off** (web) | `tests/webcortex/test_curiosity_progress.py` | `genesis_fix1b.json` | leave off |
| Exact expected information gain and "habitual or deliberate" beside each executive decision | `instance/information.py`, `instance/executive.py` | `decision_information` **on** (display; instance) | `tests/instance/test_information.py` | `genesis_instinct.json` | `nsf config --no-decision-information` |
| Rule version `information-v1` (replaces rules 5–7 by the question with the greatest expected gain) | `instance/information.py`, `instance/executive.py` | `executive_rule` **`rules-v1`**; `information-v1` opt-in (instance) | `tests/instance/test_information.py` | `genesis_instinct.json` | `nsf config --executive-rule rules-v1` |

### 2.2 Imagination: computation whose answers can be checked (lane M, ADR-052)

| Mechanism | Module(s) | Setting: shipped default | Tests | Lane evidence | How to back it out |
|---|---|---|---|---|---|
| Sandbox: an in-memory copy of the individual, bounded in size and time | `instance/imagine.py` | `imagine_max_bytes` **512 MiB**, `imagine_max_seconds` **10 s** (instance) | `tests/instance/test_imagine.py` | `genesis_imagination.json` | the bounds; the commands below off |
| Exact what-ifs on the belief model; the value of each possible next reading; leave-one-out retrospection | `instance/imagine.py`, `nsf imagine what-if / questions / retrospect` | `imagine_what_if` **on**, `imagine_retrospect` **on** (commands; instance) | `tests/instance/test_imagine.py` | `genesis_imagination.json` | `nsf config --no-imagine-what-if`, `--no-imagine-retrospect`; `nsf imagined purge --all` for kept answers |
| Multi-step forecasts from an active graph | `instance/forecast.py`, `nsf imagine forecast` | `imagine_forecast` **on** (command; instance) | `tests/instance/test_forecast.py` | `genesis_imagination.json` | `nsf config --no-imagine-forecast` |
| Deterministic linear causal engine on declared models | `src/neuroscifi/causal.py`, `nsf imagine scm FILE` | always available as a command (reads only the file it is given) | `tests/test_causal.py`, `tests/instance/test_imagine_scm_file.py` | `genesis_imagination.json` | do not use the command; revert before merge |
| Scenario records: three imagined futures per open web prediction, kept in the imagined store; the outcome slot is filled from the real settlement | `webcortex/scenarios.py` | `imagine_scenarios` **off** (instance) | `tests/webcortex/test_scenarios.py` | `genesis_imagination.json`; Wave 2: `genesis_integration.json` | leave off; `nsf imagined purge` |

### 2.3 Self-description, selective broadcast, continuity (lane W, ADR-053)

| Mechanism | Module(s) | Setting: shipped default | Tests | Lane evidence | How to back it out |
|---|---|---|---|---|---|
| One self-description record (what it is doing, knows, is unsure of, can do next, why; each field with its source and age) on the console, `nsf self`, Pocket, voice and a read-only MCP tool | `self_description.py`, `mcp.py`, console and Pocket pages | always on (display; no switch) | `tests/test_self_description.py`, `test_self_description_robustness.py`, `test_self_description_surfaces.py` | `genesis_workspace.json`; `genesis_fix1a.json` (its cost) | remove `self_status` from the MCP client's allowed tools; revert before merge |
| Presence shows planning, predicting and simulating from real rows; worker model calls visible | `presence/`, `voice/hub.py` | always on (display) | `tests/presence/test_workspace_states.py` | `genesis_workspace.json` | revert before merge |
| Selective broadcast: one function decides which finding reaches the owner's channels | `broadcast.py` | `broadcast` **`off`** (threshold 0.5; instance) | `tests/webcortex/test_broadcast.py` | `genesis_workspace.json` | `nsf config --broadcast off` |
| Paired phones also see the owner's latest research questions | Pocket | `pocket_recent` **off** (web) | `tests/test_self_description.py`, `tests/test_self_description_surfaces.py` | — | leave off |
| A spoken question's answer followed across a restart, delivered at most once | `voice/hub.py` | voice `resume_follow` **on** | `tests/voice/test_follow_restart.py`, `tests/uat/test_uat_voice_follow_faults.py` | — | `nsf voice settings --set '{"resume_follow": false}'` |
| "What are you doing?" answered with the status instead of starting research | `voice/` | voice `status_questions` **off** | `tests/test_self_description.py` | — | leave off |

### 2.4 Monitoring of answers (lane C, ADR-054)

| Mechanism | Module(s) | Setting: shipped default | Tests | Lane evidence | How to back it out |
|---|---|---|---|---|---|
| A monitoring record per research result: evidence sufficiency in plain words, source dependence, the claim probability; **no answer confidence shown** ("not enough development data") | `webcortex/monitoring.py`, `webcortex/orchestrator.py` (`_monitor`) | `answer_monitoring` **on** (record; web) | `tests/webcortex/test_monitoring.py`, `tests/acceptance/test_cognition_monitoring.py` | `genesis_monitoring.json` | `nsf web config --set answer_monitoring=false` |
| Selective answering (a weak answer opens with "not enough evidence") | `webcortex/monitoring.py` | `selective_answering` **off** (web) | `tests/webcortex/test_monitoring.py` | `genesis_monitoring.json` | leave off |
| Verification escalation (one more independent source for the weakest statement) | `webcortex/orchestrator.py` | `verification_escalation` **off** (web) | `tests/webcortex/test_monitoring.py` | `genesis_monitoring.json` | leave off |
| Adaptive depth (stop reading once the last pages added nothing) | `webcortex/orchestrator.py` | `adaptive_depth` **off** (web) | `tests/webcortex/test_monitoring.py` | `genesis_monitoring.json` | leave off |
| The executive's precision report (how its questions turned out) | `instance/executive.py`, `nsf regulation --precision` | always available (read-only command) | `tests/instance/test_precision_report.py` | `genesis_monitoring.json` | do not use the command |

### 2.5 Knowledge evolution (lane K, ADR-055; migrations 18 and 19)

| Mechanism | Module(s) | Setting: shipped default | Tests | Lane evidence | How to back it out |
|---|---|---|---|---|---|
| Hypothesis fields (assumptions, counter-evidence, expected gain, cost, revisions) | `webcortex/evolution.py` | `hypothesis_fields` **on** (record; web) | `tests/webcortex/test_knowledge_evolution.py` | `genesis_knowledge.json` | `nsf web config --set hypothesis_fields=false` |
| `abandoned`: a derived hypothesis whose predictions found no evidence by their deadline | `webcortex/evolution.py` | `hypothesis_abandon` **off** (web) | `tests/webcortex/test_knowledge_evolution.py` | `genesis_knowledge.json` | leave off |
| Premises and "to re-check" marks on what rests on a changed premise (a mark changes no belief) | `webcortex/premises.py` | `premise_recheck` **on** (marks; web) | `tests/webcortex/test_knowledge_evolution.py` | `genesis_knowledge.json` | `nsf web config --set premise_recheck=false` |
| Idle re-check of the knowledge most at risk of being stale | `webcortex/recheck.py` | `idle_recheck` **off**; `recheck_per_tick` 1, `recheck_daily` 4 (web) | `tests/webcortex/test_knowledge_evolution.py` | `genesis_knowledge.json` | leave off |
| A bounded, traceable reflection; optionally phrased by a local model (stored as an `inference`, never evidence) | `webcortex/reflection.py` | `reflection` **off**, `reflection_interval_days` 7, `reflection_model_summary` **off** (web) | `tests/webcortex/test_knowledge_evolution.py` | `genesis_knowledge.json` | leave off; forget covers what was written |
| Strategy selection by Thompson sampling from external outcomes, in force only after a promotion passes its gate; an append-only improvement ledger | `webcortex/strategies.py`, migration 18 | `strategy_selection` **`"gate"`**: inactive (no promotion has passed; the gate fails closed) (web) | `tests/webcortex/test_knowledge_evolution.py` | `genesis_knowledge.json` | `nsf knowledge strategy --revert`; `nsf web config --set strategy_selection='"off"'` |

Schema: migration 18 (five tables, empty at the defaults; the ledger refuses UPDATE and DELETE) and migration 19 (one
index) are forward-only: older software refuses the file, and rollback is a restore of the checkpoint taken before the
upgrade ([WAVE1.md](WAVE1.md) §10).

## 3. Wave 2: the integration loop, the guards, the measurements (lanes L, U, F)

Record: [WAVE2.md](WAVE2.md). No schema change, no default of Wave 1 changed, no new background activity, no new
dependency (lane reports L, U, F).

| Mechanism | Module(s) | Setting: shipped default | ADR | Tests | Evidence | How to back it out |
|---|---|---|---|---|---|---|
| The arbitration record: when the re-check or a due refresh takes the subject the timer's curiosity wanted in the same pass, the winner's task records which mechanism lost and by which rule (never the subject) | `webcortex/scheduler.py` (`_arbitrated`) | `arbitration_record` **on** (a record only; web) | ADR-056 | `tests/webcortex/test_integration_arbitration.py`, `test_integration_loop.py` | `genesis_integration.json` (results identical with it off) | `nsf web config --set arbitration_record=false` |
| Salience attention: of the candidates admission already admitted, the more salient is taken first; it admits and refuses nothing | `webcortex/salience.py` (`attention`, `order`), `webcortex/autonomy.py` (`admission`) | `salience_attention` **off** (web) | ADR-056 | `tests/webcortex/test_integration_arbitration.py` | `genesis_integration.json` (held-out 0.713 alone = the baseline) | leave off |
| The loop in the self-description: `doing.cycle` (the latest background investigation and how it was admitted, the latest arbitrations, the predictions settled so far) and one plain sentence in `short()` | `self_description.py` (`_cycle`, `short`) | always on (display, like the rest of the record) | ADR-056 | `tests/webcortex/test_integration_loop.py` | `genesis_integration.json` (loop probes: plumbing) | revert before merge |
| 14 guards in 10 files: a Wave 1 mechanism that raises, times out or returns garbage no longer breaks the owner's question, a status page, the timer's pass, the worker or a voice session; the failure is shown or logged | `instance/executive.py`, `webcortex/curiosity.py`, `webcortex/autonomy.py`, `webcortex/scheduler.py`, `webcortex/orchestrator.py`, `webcortex/consolidate.py`, `pocket/server.py`, `resources/policy.py`, `internal_state.py`, `voice/hub.py` | always on (no switch: they change behaviour only when something fails) | — (lane U; [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) §4) | `tests/uat/test_uat_fault_injection.py` (75), `tests/uat/test_uat_voice_follow_faults.py` (6) | [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) §4 (no evidence file) | revert lane U's commits (`314ab80`, `9a7796f` on `genesis-wave2`) |
| Rollback and Wave 0 equivalence checks (tests only) | `tests/uat/test_uat_rollback.py` (26), `tests/uat/test_uat_wave0_equivalence.py` (5) | — | — | themselves | [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) §5, §6 | — |
| Ten added adversarial cases (origin `added_wave2`), expectations committed before the cases | `acceptance/cognition/adversarial.py` | — (benchmark) | — | `tests/acceptance/test_cognition_bench.py::test_adversarial_cases_today` | [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) §3 | revert the commits |
| The integrated benchmark family (frozen worlds committed first) | `acceptance/cognition/integrated.py`, `integrated_splits.py`, `scripts/bench_wave2_integration.py` | — (benchmark) | ADR-056 | `tests/acceptance/test_genesis_integration_bench.py` | `genesis_integration.json` | revert the commits |
| The learning-cycles experiment (frozen worlds committed first; pre-registered verdict rule) | `acceptance/cognition/cycles.py`, `cycles_splits.py`, `scripts/bench_learning_cycles.py` | — (benchmark) | — (no mechanism added) | `tests/acceptance/test_learning_cycles_bench.py` (7) | `genesis_learning_cycles.json` | revert the commits |

## 4. What was not built

From [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) §6 and the Wave 2 lanes:

| Not built | Why |
|---|---|
| Any consciousness score, phi or "awareness" module | intractable, contested, informs no decision ([RESEARCH_ASSESSMENT.md](RESEARCH_ASSESSMENT.md) §3.0, §7) |
| A gate or reward that is the system or a model grading itself | self-correction without external feedback is unreliable; self-critique marks precision 0.07 (lane C, cited in ROADMAP §6) |
| Training on the system's own outputs; generative replay | model collapse; on the do-not-build list |
| Model fine-tuning (directive level 4) and architecture self-modification (level 5) | the owner's authorisation is required and was not given; `evolution.model_adaptation_guard` refuses |
| Feeding a scenario's or forecast's error to curiosity, the re-check marks or learning progress | assessed by lane L: the real settlement already reaches every allowed learner; the scenario's error adds no information about the subject (ADR-056 Decision 4) |
| A low-support monitoring verdict asking for a later re-check | assessed by lane L: not measurable on any frozen world (ADR-056 Decision 4) |
| Abstraction, concept hierarchies, causal extraction from text, learned retrieval / routing / verification policies, adaptive forgetting | not built ([RECURSIVE_KNOWLEDGE_ASSESSMENT.md](RECURSIVE_KNOWLEDGE_ASSESSMENT.md) §1); forgetting is the owner's act by design |
| Front-door adjustment in the causal engine | not built; those questions are declined ([WAVE1.md](WAVE1.md) §9) |
