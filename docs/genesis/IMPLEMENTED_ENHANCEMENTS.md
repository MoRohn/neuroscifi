# Implemented enhancements (deliverable E)

Status: 2026-11-26; Waves 0–2 on `828ca8e`, Wave 3 (§5) on `0410a22` (branch `genesis-wave3`, after the three fix lanes), Wave 4 (§6) on `3032cf6` (branch `genesis-wave4`, after its fix lane). Deliverable E of the
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
- **Wave 3** (§5) adds the front door in `nsf imagine scm` (on), the idle investigation's daily task count (a
  budget fix, also checked inside the submission's transaction), the read-ahead byte bound, a forget that holds the
  write lock briefly and reaches contradiction questions, Pocket and console panels redrawn only on change, predictive
  regulation "in shadow" and `nsf readiness` (opt-in / read-only), and five settings built off: `recheck_order`,
  `supersession`, the two source rules (`attribution_independence`, `echo_recognition`, merged on at `f0a1f09`,
  shipping off since the fix lane) and `origin_grouping` (`"transitive"`; the one-way rule is the opt-in).
  At Wave 3's shipped defaults the individual treats sources exactly as before Wave 3.
- **Wave 4** (§6) adds settings checked on load (damaged network limits and settings fixed at creation fail closed,
  each naming its repair command), `requeue` within the day's task count, a restore that scans before its write
  transaction, two tie-breaks by creation order, and a display-only "Only one source says this." caution; and four
  settings built off: `source_derivation` (not supported; not recommended), `own_text_provenance`,
  `allowance_division` (`"race"`) and `caution_detail`. At Wave 4's shipped defaults sources are still treated as
  before Wave 3.
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
| The arbitration record: when the re-check or a due refresh takes a subject the timer's curiosity could have launched in the same pass (in its pool, as the kind it launches, with budget left), the winner's task records which mechanism lost and by which rule (never the subject) | `webcortex/scheduler.py` (`_arbitrated`) | `arbitration_record` **on** (a record only; web) | ADR-056 | `tests/webcortex/test_integration_arbitration.py`, `test_integration_loop.py` | `genesis_integration.json` (results identical with it off). **Defect, fixed** (Wave 2 measurement review H1): as first built, with the re-check on it recorded conflicts that did not happen; fixed by lane Y (`fb19aae`, `9b53aa0`), re-measured 0 in every configuration | `nsf web config --set arbitration_record=false` |
| Salience attention: of the candidates admission already admitted, the more salient is taken first; it admits and refuses nothing | `webcortex/salience.py` (`attention`, `order`), `webcortex/autonomy.py` (`admission`) | `salience_attention` **off** (web) | ADR-056 | `tests/webcortex/test_integration_arbitration.py` | `genesis_integration.json` (not exercised: the timer's curiosity pool never held more than one subject; where the idle path had several candidates salience never changed the first) | leave off |
| The loop in the self-description: `doing.cycle` (the latest background investigation and how it was admitted, the latest arbitrations, the predictions settled so far) and one plain sentence in `short()` | `self_description.py` (`_cycle`, `short`) | always on (display, like the rest of the record) | ADR-056 | `tests/webcortex/test_integration_loop.py` | `genesis_integration.json` (loop probes: plumbing) | revert before merge |
| 14 guards in 10 files: a Wave 1 mechanism that raises, times out or returns garbage no longer breaks the owner's question, a status page, the timer's pass, the worker or a voice session; the failure is shown or logged | `instance/executive.py`, `webcortex/curiosity.py`, `webcortex/autonomy.py`, `webcortex/scheduler.py`, `webcortex/orchestrator.py`, `webcortex/consolidate.py`, `pocket/server.py`, `resources/policy.py`, `internal_state.py`, `voice/hub.py` | always on (no switch: they change behaviour only when something fails) | — (lane U; [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) §4) | `tests/uat/test_uat_fault_injection.py` (75; 116 on the combined tree), `tests/uat/test_uat_voice_follow_faults.py` (6; 19 on the combined tree) | [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) §4 (no evidence file). **Defects** found by the Wave 2 security review, fixed by lane X: the voice guard delivered an answer when its delivery claim failed, also after a forget (now: no claim, no delivery; the claim is asked again); several guards wrote subject text to the process log (now: the type, SQLite's error name and ids only). Lane X added two guards for lane L's entry points (16 in 10 files) | revert lane U's commits (`314ab80`, `9a7796f` on `genesis-wave2`) |
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
| Front-door adjustment in the causal engine | built in Wave 3 (§5 below, ADR-059); until then those questions were declined ([WAVE1.md](WAVE1.md) §9) |
| A source-independence rule that decides by origin (publication order, derivation) or by the owner's confirmation | not built; Wave 3's phrase-reading rules did not close the hole the withheld look found and ship off ([WAVE3.md](WAVE3.md)) |

## 5. Wave 3: sources, the re-check's order, coverage, the front door, host evidence (lanes P, C, H, R, S, V)

Record: [WAVE3.md](WAVE3.md). Defaults read again from the code on `49bf81a` (after the three fix lanes): `WebConfig`
in `src/neuroscifi/webcortex/config.py` (`recheck_order` "staleness", `attribution_independence` false,
`echo_recognition` false, `supersession` "ask", `supersession_min_sources` 2, `origin_grouping` "transitive"),
`InstanceConfig` in
`src/neuroscifi/instance/store.py` (`predictive_regulation` false), `FRONT_DOOR` True and `USEFUL` 0.1 in
`src/neuroscifi/causal.py`. Where the fix lane changed a default the row says so. No schema change.

| Mechanism | Module(s) | Setting: default | ADR | Tests | Evidence | How to back it out |
|---|---|---|---|---|---|---|
| Front-door adjustment; a stated origin of the moments (`moments: population` or `samples: n`), an interval from n, a usefulness bound (`USEFUL` 0.1) | `causal.py`, `instance/cli_imagine.py` | `FRONT_DOOR = True`; `nsf imagine scm --no-front-door` (a flag of an owner's command, nothing stored) | ADR-059 | `tests/test_causal_front_door.py`, `tests/uat/test_uat_rollback.py::test_front_door` | `genesis_frontdoor.json` | `--no-front-door`; revert `5701bbe` |
| Predictive regulation in shadow: the gate decides as off, the forecast's verdict is recorded and settled against the host's memory; `nsf internal-state --shadow-report` | `resources/shadow.py`, `resources/policy.py` | `predictive_regulation` `false`; `"shadow"` is opt-in (instance) | ADR-058 | `tests/test_forecast_shadow.py`, `tests/uat/test_uat_shadow_faults.py`, `tests/uat/test_uat_rollback.py::test_predictive_regulation_shadow` | `genesis_h.json` | `nsf config --no-predictive-regulation`; delete `<db>.resources/forecast-shadow*` |
| `nsf readiness`: each learner of real outcomes against its minimum; `uncertain.outcomes_readiness` (owner scope) | `readiness.py`, `self_description.py` | always (read-only) | ADR-058 | `tests/test_readiness.py` | `genesis_h.json` | revert `4807813` |
| The re-check's order by change rate, a newer version seen, rotation | `webcortex/recheck.py` | `recheck_order` `"staleness"` (web) | ADR-060 | `tests/webcortex/test_recheck_order.py`, `tests/uat/test_uat_rollback.py::test_recheck_order` | `genesis_recheck_order.json` | leave at `"staleness"` |
| A sentence crediting an outlet counts with that outlet | `webcortex/claims.py` (`attribution_keys`) | `attribution_independence`: `true` at `f0a1f09`, ships `false` (fix lane) (web) | ADR-061 | `tests/acceptance/test_wave3_s.py`, rollback and fault-injection tests | `genesis_wave3_s.json`; review S-1, S-3, S-4, security H1 | `nsf web config --set attribution_independence=false` |
| A page repeating the individual's own text, or crediting a model, is no evidence for it | `webcortex/consolidate.py` (`_Echoes`, `_without_echoes`) | `echo_recognition`: `true` at `f0a1f09`, ships `false` (fix lane) (web) | ADR-061 | as above | `genesis_wave3_s.json`; review S-2, S-8, security M2–M5 | `nsf web config --set echo_recognition=false` |
| Supersession of a stale item by enough independent sources; `nsf web replacements`, `nsf web revert ID` | `webcortex/consolidate.py`, `webcortex/knowledge.py` | `supersession` `"ask"` (off), `supersession_min_sources` 2 (web) | ADR-061 | `tests/acceptance/test_wave3_s.py`, `tests/uat/test_uat_rollback.py::test_supersession_and_its_records` | `genesis_wave3_s.json` | leave at `"ask"`; revert each replacement |
| `autonomy.idle` honours the day's autonomous task count | `webcortex/autonomy.py` (`_tasks_spent`) | always (a budget fix) | ADR-062 | `tests/webcortex/test_autonomy.py::test_idle_shares_the_days_autonomous_task_count_with_the_timer` | `genesis_idle_coverage.json` | revert `41198b3` |
| The day's autonomous task count checked again inside submit's write transaction, so the timer and the idle investigation together never queue past the owner's cap (fix lane, security review L6; a behaviour change at the defaults: the second of two deciders that both saw room is refused, `BudgetRefused`) | `webcortex/orchestrator.py` (`_within_task_count`) | always (a budget fix) | ADR-062 | `tests/acceptance/test_wave3_fix.py::test_two_deciders_that_both_saw_room_cannot_both_take_the_last_place`, `::test_concurrent_autonomous_submissions_never_pass_the_cap`; the knowledge re-check (when on) checks it inside its own transaction (review3b N5): `::test_a_recheck_that_saw_room_cannot_take_the_last_place_after_the_idle_investigation` | the fix lane's report (8 threads, cap 3: exactly 3 queued, 5 refused) | revert `7969153` (with the fix lane's other L2–L8 fixes); N5: revert `4fb6e86` |
| Read-ahead stays inside the task's byte budget: a fetch ahead that was abandoned is granted no more bytes (the reconciliation lane's race, fix lane) | `webcortex/net.py` (`_Speculation.take`) | always (a fix) | — | `tests/webcortex/test_read_ahead.py::test_a_fetch_ahead_abandoned_between_two_of_its_reads_reads_one_byte_more_not_the_rest_of_the_body` | `genesis_wave3_fix_checks.json` (the read-ahead file 50 of 50 under CPU load) | revert `82b7fd9`, `102e72b` |
| Forget: the answer sentences that stated a forgotten value are marked sourced (security M3), the scan made before the write lock (review3b N2), only sentences naming the forgotten claim's subject (N9); quoted sentences removed from contradiction questions (N6) | `instance/governance.py` (`prepare_sourced_marks`, `_mark_sourced_answers`, `_forget_quoted`) | always (a fix) | ADR-061 | `tests/acceptance/test_wave3_fix.py::test_after_forgetting_the_answers_source_later_honest_pages_still_count`, `::test_a_forget_reads_no_task_result_by_scanning_inside_its_write_transaction`, `::test_forgetting_a_number_heavy_source_marks_no_answer_about_another_subject`, `::test_forgetting_a_source_removes_its_sentence_from_contradiction_questions` | the second fix lane's report (the write lock held 0.15–0.25 s at 10,000 answers of 20 kB, against 6.9–39.6 s) | revert `de82c7b` (and `98109d7` for M3) |
| Damaged saved Wave 3 web settings read as their default (a damaged `supersession_min_sources` turns `supersession` to "ask"), logged once, listed by `nsf web config` (`problems`) and the console | `webcortex/config.py` (`CHECKED_ON_LOAD`, `FAILS_CLOSED`), `webcortex/state.py` (`setting_problems`) | always (a fix) | ADR-061 | `tests/acceptance/test_wave3_fix.py::test_a_damaged_saved_wave3_setting_reads_as_its_default`, `::test_a_damaged_bar_of_a_rule_that_is_on_turns_the_rule_off_and_is_logged_once`, `tests/console/test_console_web.py::test_the_web_status_lists_damaged_saved_settings` | — | revert `4f3ca3a`, `7969153` |
| A page's named origin (attribution line, wire credit, canonical URL elsewhere) attaches it to ONE origin ("one_way": closes review3b N1, N3, N10 on staged pages; the edge chosen by content, review3c F1; a wire's name owned only by the wire's own site, F2). Built by the second fix lane as a change of the shipped grouping; since the third fix lane a setting, off: at the default the grouping is `3ce4ae3`'s, unchanged (N1 open) | `webcortex/claims.py` (`independence_groups`, `_one_way_groups`, `_one_way`, `stamp_origins(grouping=)`) | `origin_grouping` `"transitive"` (web); `"one_way"` is the owner's opt-in | ADR-063 | `tests/webcortex/test_origin_grouping.py` (the default against `3ce4ae3`'s groups: 32 scenarios, 496 orders), `tests/acceptance/test_wave3_fix.py` (N1, N3, N10), adversarial origins `added_wave3_fix2`, `added_wave3_fix3`, rollback and fault-injection tests | `scripts/compare_origin_grouping.py`: 0 of 614,988 world inputs and 0 of 8,437 offline-benchmark groupings differ from `3ce4ae3`; offline digest `68731d72…` (ADR-063) | leave at `"transitive"`; `nsf web config --set 'origin_grouping="transitive"'` |
| Panels redrawn only on change, focus kept (console overview, Pocket lists) | `console/web/console.js`, `pocket/web/field-ui.mjs`, `research-ui.mjs` | always (a fix) | — | `tests/e2e/test_focus_kept.py` | lane P's report | revert `81edd00` |
| The offline digest across hosts: `portable_sha256`, `--deterministic-out`, `scripts/diff_deterministic.py`, a CI artifact | `scripts/bench_wave1.py`, `.github/workflows/ci.yml` | — (benchmark) | — | `tests/test_offline_digest_hosts.py` | `offline_deterministic_aarch64.json` | revert `abb493f` |
| Three guards (reconciliation): a failing attribution read, echo check or supersession rule leaves the claim, the cluster or the question as with the setting off; the failure is a WARNING or the task's "error" event, its type only | `webcortex/claims.py`, `webcortex/consolidate.py` | always (act only on failure) | — | `tests/uat/test_uat_fault_injection.py` (28 Wave 3 cases when written, 12 fail without the guards; 32 since the third fix lane added `claims._one_way`) | WAVE3.md §3.9 | revert `ce5a432` |
| Withheld learning-cycle worlds and their guard (step 0); lanes R, S, V's worlds and harnesses | `acceptance/cognition/cycles.py`, `cycles_rate_splits.py`, `cycles_splits_s.py`, `cycles_s.py`, `scripts/bench_wave3_s.py` | — (benchmark) | — | `tests/acceptance/test_learning_cycles_*.py` | the evidence files above | revert the commits |


## 6. Wave 4: source independence by derivation and by what the individual said; the allowance; settings; the caution (lanes 0, S, E, A, D, the fix lane)

Record: [WAVE4.md](WAVE4.md). Defaults read from the code on `3032cf6` (after the fix lane): `WebConfig` in
`src/neuroscifi/webcortex/config.py` (`own_text_provenance` false, `source_derivation` false, `allowance_division`
"race", `reach_share` 0.25, `caution_detail` false), `EMISSION_LIFETIME` 30 days and `MAX_FINGERPRINTS` 200,000 in
`src/neuroscifi/webcortex/emitted.py`. No schema change (19). Every rule that would change what counts as a source
ships off, and none is recommended to be turned on.

| Mechanism | Module(s) | Setting: default | Verdict | ADR | Tests | Evidence | How to back it out |
|---|---|---|---|---|---|---|---|
| One source by evidence of derivation: a page naming (a capitalised name equal to a site label) or linking another page read in the task joins that page's group, one edge per group, on top of today's grouping; hardened by the fix lane (no flagged or claimless page named; no subject name or calendar word as a label) | `webcortex/claims.py` (`derivation_cites`, `_derivation_edges`, `stamp_origins(derivation=, echo=)`) | `source_derivation`: **off** (web) | "trade" under its own rule on both splits; **not supported; do not turn on** (its signal is the worlds' generator's convention; one page can still merge honest outlets) | ADR-066 | `tests/webcortex/test_source_derivation.py`, `tests/acceptance/test_wave4_fix.py`, adversarial origin `added_wave4_s`, UAT rollback, fault injection, setting lists | `genesis_wave4_s.json`, `genesis_wave4_s_assess_development.json`, `genesis_wave4_fix.json`; default path 2,052,099 comparisons, 0 different | `nsf web config --set source_derivation=false` (its stored marks are not read when off) |
| Own-text provenance: the owner's answers' asserted (subject, value) pairs recorded at emission with time and source status (salted digests in `web_strategies`, 30 days, at most 200,000 rows, about 44 MB at the cap); a page repeating an unsourced one is no evidence for or against, and a disagreement it raises is asked | `webcortex/emitted.py` (new), `webcortex/consolidate.py` (`_echo_disagreement`), `webcortex/orchestrator.py` (emission), `instance/governance.py` (forget, replay) | `own_text_provenance`: **off** (web) | "effective" under its own rule on both splits; off: small counts, a measured false rejection, undated pages unmeasured, the owner's decision | ADR-067 | `tests/acceptance/test_own_text_provenance.py`, `tests/acceptance/test_wave4_fix.py`, `tests/webcortex/test_derivation_echo_wiring.py`, UAT rollback, fault injection, setting lists, Wave 0 equivalence | `genesis_wave4_e.json`, `genesis_wave4_fix.json` | `--set own_text_provenance=false`; `nsf web config --forget-own-text` removes every fingerprint |
| Division of the day's autonomous allowance between refreshing and reaching (`"reserve"`, `"yield"`) | `webcortex/autonomy.py` (division, `idle`), `webcortex/scheduler.py` (`_over_cap(held=)`, `_division`) | `allowance_division`: `"race"` (as before), `reach_share` 0.25 (web) | every candidate "unsafe" or "harm"; no gain beyond the spread | ADR-064 | `tests/webcortex/test_allowance_division.py`, `tests/acceptance/test_learning_cycles_division.py`, UAT rollback, fault injection (4 cases) | `genesis_wave4_a_{dev,held_out,default_identity,cost}.json` | `--set 'allowance_division="race"'` |
| Two tie-breaks follow creation order, not random task ids (`autonomy._lineage`; `curiosity.search_terms` and `rank`): runs with the idle investigation are repeatable | `webcortex/autonomy.py`, `webcortex/curiosity.py` | always (a fix) | timer-only runs unchanged (594 runs, only file sizes differ); offline digest unchanged | ADR-064 "Follow-up" | `tests/acceptance/test_learning_cycles_division.py::test_the_same_history_gives_the_same_run_whatever_the_task_ids` | `genesis_wave4_a_repeatability.json` | revert `805fa0e` |
| Every saved web, instance and voice setting checked on load: damaged → cautious value or default, logged once by name, listed; network limits fail closed (web off; the refusal names `nsf web config --set NAME=VALUE`); settings fixed at `nsf init` pause writes until `nsf config --repair-fixed NAME=VALUE` (the fix lane, restoring `main`'s closed behaviour) | `webcortex/config.py` (`CHECKED_ON_LOAD`, `CAUTIOUS`, `FAILS_CLOSED`), `webcortex/state.py`, `instance/store.py`, `instance/cli.py`, `voice/config.py` | always (a fix) | — | ADR-065 (context); WAVE4.md §1.5 | `tests/acceptance/test_wave4_d.py`, `tests/acceptance/test_wave4_fix.py`, `tests/console/test_console_setting_problems.py` | the lanes' reports (`WebConfig.from_json` 30 → 83 µs) | revert `d2f23f0`, `03bf1ac`, `268c433`, `e36d4ee`, `3d99b91` |
| `requeue` honours the day's autonomous task count inside its write transaction (the owner's tasks never counted) | `webcortex/orchestrator.py` (`requeue`) | always (a budget fix) | — | — | `tests/acceptance/test_wave4_d.py` | lane D's report | revert `ec33fe0` |
| A checkpoint restore scans the answers before its write transaction (0.14–0.15 s inside it, against 1.64–1.82 s, at 10,000 answers of 20 kB) | `instance/governance.py` (`prepare_replay_marks`), `instance/checkpoints.py` | always (a fix) | — | — | `tests/acceptance/test_wave4_d.py` | lane D's report | revert `b905024` |
| The caution: "Only one source says this." where a statement rests on a single page (console, `nsf web show` / `ask` / `knowledge`, Pocket, the voice reply); the copy trigger and the own-answer flag computed and shown only with `caution_detail` | `webcortex/caution.py` (new), the views | shown (display only); `caution_detail`: **off** (web) | the one-source trigger lines up on development (3 of 3 wrong answers); held-out not exercised; the other two triggers mislead about three times in ten | ADR-065 | `tests/acceptance/test_wave4_d.py`, `tests/console/test_console_caution.py`, `tests/e2e/test_caution_e2e.py`, `tests/e2e/test_pocket_caution_e2e.py`, `tests/uat/test_uat_rollback.py::test_caution_detail` | `genesis_wave4_d_caution.json` | `--set caution_detail=false`; revert `cfc1511`, `29c69a1`, `5341de6` |
| Learning-cycle harnesses write summaries by default (`--full-history` for everything) | `acceptance/cognition/scoring.py`, `scripts/bench_learning_cycles.py`, `scripts/bench_wave3_s.py` | — (benchmark) | same verdicts with and without histories | — | the harnesses' tests | lane D's report | revert `da23d5e` |
| The source-independence worlds (development, held-out, withheld), harness, metrics, references, guard (step 0); the reach worlds (lane A) | `acceptance/cognition/sources.py`, `sources_splits_dev.py`, `sources_splits_heldout.py`, `cycles_reach_splits.py`, `scripts/bench_sources.py` | — (benchmark) | — | — | `tests/acceptance/test_sources_bench.py` | `genesis_sources_baseline.json` | revert the commits |
