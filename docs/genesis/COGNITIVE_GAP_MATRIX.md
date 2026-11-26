# Cognitive gap matrix (deliverable C)

Status: 2026-11-25. Phase I of Project Cognitive Genesis, deliverable C of the directive's §13: the twelve gap
categories of the directive's §3 ("Cognitive gap taxonomy"), each with what exists, the gap, the measured baseline
where the cognitive benchmark has one, the priority and the lane that addresses it. Code at `76a478e`. Evidence for
"what exists" is in [COGNITIVE_ARCHITECTURE_AUDIT.md](COGNITIVE_ARCHITECTURE_AUDIT.md) (cited as A §n); baseline
numbers are from `docs/quality/evidence/genesis_baseline.json` (development / held-out, mean over the split's seeds;
measured at `19f80e2`, system code unchanged from `07a8728`), described in
[COGNITIVE_BENCHMARK.md](../quality/COGNITIVE_BENCHMARK.md).

How priority is set: by the build plan the owner's session ruled (handoff "Build plan (rulings)"): **Wave 0** is a
prerequisite for everything else; **Wave 1** lanes run in parallel after it; **not planned** means no lane addresses
the gap. The research rank (1–12, [RESEARCH_ASSESSMENT.md](RESEARCH_ASSESSMENT.md) §6) is given in brackets where a
shortlisted mechanism applies. Wave 1 is **integrated, reviewed, fixed and
reconciled**: its rows give the shipped status measured on the reconciled tree ([WAVE1.md](WAVE1.md)); the final
validation and the one look at the withheld family are in its §7 and §8 (row 10: what-if answering 0.12 → 0.827 and
the self-description 12 → 15 of 15 on the withheld worlds; predictive regulation, off, 0.83 of memory breaches
avoided on the simulated plant; `information-v1` and learning progress, both off, show no gain there). Wave 2
(integrated on `genesis-wave2`, `828ca8e`; [WAVE2.md](WAVE2.md)) adds a "Wave 2" line where a row changed (rows 1,
4, 5, 7, 8, 9, 12): no gap closed by a measured gain; the integrated loop equals the baseline on held-out, knowledge
decays over repeated cycles in every configuration, and fault injection found failures now guarded.

The baseline is a set of task scores on synthetic worlds with known answers. It is not a measurement of
"intelligence", of the real host, of the open web or of the answer model.

## The matrix

| # | Category | Status today | Main gap | Baseline (development / held-out) | Priority | Lane |
|---|---|---|---|---|---|---|
| 1 | Reactive intelligence | IT | Gate has no hysteresis; latency and backlog change nothing | breach ticks, all channels 202.7 / 217.7; adversarial 12 of 13 | keep (regression guard) | retention + adversarial families (Z) |
| 2 | Anticipatory intelligence | M (core, runtime); P (web) | No forecast of load; no multi-step rollout | memory breaches avoided 0 of 3 / 0 of 7; lead 4.3 / 5.8 ticks; false alarms 0.000 / 0.009 | Wave 1 [10] | I (regulation), M (forecast) |
| 3 | Causal reasoning | P (core); M (web) | Edges not linked to actions; no interventional or counterfactual engine | counterfactual coverage 0.107 / 0.071, relative error 0.045 | Wave 1 [pilot 2.2] | M |
| 4 | Imagination and simulation | M | No sandbox, no what-if; until Wave 0 no place to keep imagined content apart | counterfactual coverage as above; adversarial `simulated_reading_moves_no_belief` fails | Wave 0 prerequisite done; Wave 1 [1, pilots] | P (done), M |
| 5 | Self-monitoring | P | Five separate status views; no integrated record; under-confident claims; critique marks unmeasured | self coverage 0.80, 4 surfaces; claim ECE 0.304 / 0.341; AUROC 0.685 / undefined | Wave 1 [3, 4] | I, W, C |
| 6 | Temporal continuity | IT (core, per task); P (runtime) | No cross-task working context; voice follow-up lost on restart (R8); console and Pocket separate principals | no family measures it | Wave 1 | W |
| 7 | Adaptive motivation | M (core); H (web) | Fixed rule order; hand weights; noisy-TV trap; two admission rules | core 0.014 / 0.092 bits per investigation (random 0.037 / 0.083); web noise share 0.375 / 0.537 (idle path 0.81 / 0.80) | Wave 0 done (one rule); Wave 1 [9, 11] | S (done), I |
| 8 | Knowledge formation | IT (statements); P (graph); M (abstraction) | No abstraction or concept hierarchy; hypotheses lack assumptions, stored gain and cost; `abandoned` never set | retention 4 of 4 scenarios, 7 offline metrics recorded as reference | Wave 1 [5, 8] | K |
| 9 | Learning-to-learn | M (core); PD (web) | Research-policy weights and per-mode statistics written, read by nothing; depth never adapts | no direct family | Wave 1 [12] | K, C |
| 10 | Generalisation and transfer | P (core); M (web) | No transfer measurement | withheld (once): breach ticks 137.3; web noise share 0.857; counterfactual coverage 0.143; self coverage 0.80 | Wave 1 end: one `--final` run | main session |
| 11 | Novel problem-solving | M | No mechanism | no family | not planned | — |
| 12 | Recursive improvement | P (core: graph gate); guard only (web) | No outcome-driven policy change; no improvement ledger; no reflection | no direct family | Wave 1 [12]; Levels 4–5 not built | K |

## 1. Reactive intelligence

| | |
|---|---|
| What exists | Core: change detection (`beliefs._detect`), write pause, defer (A §3.1, §3.2). Web: question → verified answer with the guard (A §2). Runtime: background gate, engine gate, model slots, vLLM priority, owner-first claim order (A §3.2) |
| Gap | The gate holds background work only once memory is already critical; once drained tasks free memory the level drops below critical and new tasks start again (no hysteresis), which re-breaches slow leaks. Latency and the owner's backlog change nothing (benchmark, "What the baseline says") |
| Baseline | anticipation breach ticks, all channels: 202.7 (sd 2.1) / 217.7 (sd 4.0); adversarial 12 of 13 staged cases pass on both splits |
| Priority, lane | Not a build target: what works is guarded by the retention family (4 acceptance scenarios + 7 offline metrics) and the adversarial family |
| Wave 2 ([REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md)) | Lane U: making each Wave 1 mechanism raise, time out or return garbage broke the owner's question, a status page, the timer's pass or the worker in 44 of 75 cases; 14 guards in 10 files fixed them (75 of 75). Wave 0's offline digest, acceptance 7 of 7 and the guard differential (0 different) unchanged. Ten adversarial cases added (`added_wave2`): 9 of 10 pass; `reflection_bounded_when_everything_asks` fails (21 lines against a limit of 20) |

## 2. Anticipatory intelligence

| | |
|---|---|
| What exists | Web: "still holds at next refresh" predictions and a staleness model (A §2, §3.3). Runtime: the reload estimate is a past measurement shown to the user, not used for planning |
| Gap | Core M: no predictive regulation, no rollout. Runtime: regulation reactive and threshold-based; nothing predicts load; elevated pressure, GPU, queue depth, KV saturation and latency trends protect nothing (A §3.2) |
| Baseline | memory breaches avoided 0 of 3 / 0 of 7; mean lead of the first hold before a memory breach 4.3 / 5.8 ticks; false-alarm share 0.000 / 0.009; background work given up 0.023 / 0.044. The plant's constants (12 GiB, 450 ms, 6 ticks, 2.5 s, 2 GiB) are declared by the benchmark, not measured on the Spark |
| Priority, lane | Wave 1, research rank 10. Lane I: predictive regulation acting only inside existing bounds; lane M: multi-step forecast from the active graph |
| Wave 1 (as shipped; [WAVE1.md](WAVE1.md) §4.1) | Predictive regulation **off** (lane I). Within the simulated plant (constants declared, the forecaster fed directly; the gate's wiring covered by a test): held-out memory breaches avoided 0.80 on the split seeds (was 0), 0.80 ± 0.07 against 0.06 ± 0.11 on 12 further seeds; mainly the per-task memory reserve (without the trend still 0.80: the trend adds lead time, 5.7 → 9.9 ticks, but avoids no further breaches and accounts for all the added false alarms, 0.009 without it); costs: background work given up 0.044 → 0.106, false alarms 0.009 → 0.043. Multi-step graph forecasts (lane M) on as commands. Latency and backlog forecasting not built; nothing measured on the host |

## 3. Causal reasoning

| | |
|---|---|
| What exists | Core: directed lagged graph edges `A[dst,src]`; lesion of one edge with a permuted control; coactivation labelled "not connection" (A §3.6). Both computed and reported, neither used to choose actions |
| Gap | The graph has no action input, so it cannot predict the consequence of an action. Web: no causal relation extraction; entities are co-mention only (A §3.10). No interventional or counterfactual engine |
| Baseline | counterfactual coverage of answerable queries 0.107 / 0.071 (only one-step graph queries are answered); relative error 0.045 where answered, always within 2 sd; correct abstention 1.00 is vacuous (everything else is declined) |
| Priority, lane | Wave 1; research pilot 2.2 (small causal graphs with a deterministic engine). Lane M |
| Wave 1 (as shipped; WAVE1.md §4.3) | Lane M, on as commands: held-out answered within tolerance, answerable questions other than the 4 front-door ones per seed, 0.071 → 1.000 on the split seeds (28 of 28 per seed; 0.065 → 0.985 on 12 further seeds); over all answerable 0.063 → 0.875 (84 of 96, the front-door ones declined; pooled, not a claim). By group: equations handed to the engine 0 → 1.0 (exact arithmetic), belief what-ifs 0 → 1.0 (plumbing), learned-graph forecasts and actions 0.222 → 1.000 within 0.1 × sd (largest error 0.092; 0.778 within 0.05; the tolerance was set in the same commit as the results; 12 seeds 0.204 → 0.954), against an "answer 0" floor of 0.407 at 0.1 and 0.148 at 0.05 on those held-out questions. Front-door questions are declined (no front-door adjustment): the open gap |

## 4. Imagination and simulation

| | |
|---|---|
| What exists | Substrates: `_infer` on hypothetical tallies is exact and pure; iterating `A` is trivial; acceptance's `_fork` copies an individual (A §3.6). Web: 4 templated competing explanations and template error analysis. The guard refuses speculative model sentences. **Wave 0:** five provenance kinds and a quarantined store for inference, simulation and speculation (ADR-049); empty, no mechanism writes to it |
| Gap | No sandbox, no what-if, no scenario, no prospective simulation in either package. A reading labelled `provenance='simulated'` moves a belief like an observation |
| Baseline | counterfactual coverage as in row 3 (multi-step forecasts, action consequences, interventional and counterfactual questions and belief what-ifs: coverage 0); adversarial `simulated_reading_moves_no_belief` fails (0.500 → 0.994 after four readings) |
| Priority, lane | Prerequisite (research rank 1) done in Wave 0 by lane P. Wave 1 lane M: core sandbox on an in-memory copy, exact what-if, multi-step forecast, web scenario records (kind `simulation`, quarantined), the imagine → predict → act → observe → compare loop |
| Wave 1 (as shipped) | Lane M: the sandbox, exact what-ifs, retrospection on (commands; computed on a private copy, never written unless kept, and a kept answer goes when anything it names is forgotten); scenario records **off** (no settled real predictions to measure them on). Adversarial: 13 of the original 13 under the redefinition in ADR-052 §6 (the `simulated` case ruled mislabelled). Open owner decision: mixing simulated-environment and real readings in one epoch (needs a schema change) |
| Wave 2 (ADR-056; `genesis_integration.json`) | Lane L measured the scenario records on its integrated worlds once their predictions settled: three-way Brier 0.712 alone and 0.698 integrated on held-out, **worse than three equal futures (0.667)**; development 0.638 / 0.655. They stay off. A scenario's error feeding a learner was assessed and not built (no information beyond the real settlement) |

## 5. Self-monitoring

| | |
|---|---|
| What exists | Core: belief uncertainty, surprise, familiarity, write regulation, ask budget (A §3.1). Web: verification states, guard, critique marks (display only), calibration (ranking only), circuit health (A §3.8). Runtime: five on-request composites (`resources.status`, `store.regulation`, gateway health, Presence, voice status) |
| Gap | No integrated internal-state record, none persisted or shared between processes; no single self-model (goals, capabilities, limits, authorised tools); claim probabilities under-confident; self-critique precision unmeasured; outcome tracking of the change detector computed by nothing |
| Baseline | self-description: coverage 0.80, accuracy when answered 1.00, 4 separate surfaces; never answered: what it is uncertain about, its last decision, the tools the model may use. Selective: accuracy 0.857 (21 items) / 1.000 (20 items); AURC 0.080 / 0.000; AUROC 0.685 / undefined (no wrong held-out answer); claim probability Brier 0.095 / 0.122, all of it reliability error; ECE 0.304 / 0.341 |
| Priority, lane | Wave 1, research ranks 3 and 4. Lane I (internal-state record), lane W (self-description record on every surface), lane C (confidence, selective answering, escalation, detector precision) |
| Wave 1 (as shipped; WAVE1.md §4.4, §4.5) | Lane I: the internal-state record and its history on (display). Lane W: one self-description record on every surface; held-out 29 of 30 under the original answer key, 30 of 30 under lane W's key (baseline 24 of 30, 4 surfaces → 1); two probes are plumbing checks; held-out states printed once during development. Lane C: the monitoring record on (display); on 10 held-out claims the verification state lowers Brier 0.122 → 0.022, but a two-line rule on the verdict word scores 0.0025, so it is not a calibration gain; **no answer confidence shown** (no fitted map has 30 development items in 3 blocks); on development the record shows no answer confidence, so it ranks nothing (AURC of a constant 0.143 against the baseline's 0.080); no held-out evidence of a gain from selective answering, and turned on it would withhold 12 of 20 right held-out answers (development: 3 of 3 wrong and 12 of 18 right); selective answering, escalation and adaptive depth off; three score terms untested |
| Wave 2 | Lane L: the self-description gains `doing.cycle` (the latest background investigation, its admission, the latest arbitrations, the settled predictions): loop probes 1 → 4 of 4 (**plumbing**); the self-description family unchanged (30 of 30 current key, 29 of 30 original, 1 surface). Lane U: a new adversarial case checks that stale records are reported as stale (pass) |

## 6. Temporal continuity

| | |
|---|---|
| What exists | Core: event journal with monotone `seq`, `state_version`, epochs, links, segments, checkpoints and restore (A §3.9). Web: per-task checkpoint, lease and resume. Long-running monitors, predictions, hypotheses, ledger |
| Gap | No cross-task working context; voice sessions, events and the follow of a spoken research answer are lost on restart (R8); console and Pocket are separate principals with no shared conversational context; in-flight model calls are process-local |
| Baseline | none: the self-description family "cannot show continuity over a long-running process" |
| Priority, lane | Wave 1, lane W (voice follow-up survives restart; a "What we were just doing" panel) |
| Wave 1 (as shipped) | Lane W: voice `resume_follow` on, delivered at most once, to the person's live session (or, when they are live only on a process that does not follow it, into the asking transcript) (tested with two hubs in one process; two real processes **unverified**; real microphone and handset **unverified**); worker model calls visible to Presence; `pocket_recent` off. No benchmark measures continuity |

## 7. Adaptive motivation

| | |
|---|---|
| What exists | Core: the executive's fixed first-match rule order; the most uncertain partner asked first (A §3.3). Web: information-gain utility with fixed hand weights, futility down-weighting (the only outcome-learned signal), goal relevance by word overlap, bounded budgets (A §3.4). **Wave 0:** one admission rule for both autonomy paths (ADR-050) |
| Gap | No expected-information-gain computation in the core; the timer's ranking chases a never-resolving subject; the idle ranking is worse (the noisy subject returns as an open hypothesis whose gain the futility rule does not touch) |
| Baseline | core realised bits per investigation 0.014 / 0.092 (oracle 0.035 / 0.119; random 0.037 / 0.083); core noise share 0.00 / 0.10. Web realised bits per unit of budget 0.161 / 0.093 (oracle 0.263 / 0.146); budget on noise 0.375 / 0.537 (idle path 0.81 / 0.80, realised bits per unit of budget −0.040 / −0.016); budget on the stale-but-important subject 0.194 / 0.083 (oracle 0.417 / 0.250) |
| Priority, lane | Wave 0 (lane S) unified admission. Wave 1, research ranks 9 and 11: lane I (curiosity by learning progress, exact information gain beside decisions, optional information-seeking rule) |
| Wave 1 (as shipped; WAVE1.md §4.2) | Lane I, curiosity by learning progress **off**: reworked after the review (a confirming re-verification is progress; the stronger of it and the futility rule; a 7-day fade). Held-out, off → on: timer path 0.093 → 0.134 bits per budget on the split seeds but 0.139 → 0.126 on 12 further seeds (noise 0.412 → 0.394); idle path 0.094 → 0.144 (noise 0.706 → 0.550) on 12 further seeds. Rule `information-v1` **opt-in**, amended: held-out core 0.092 → 0.105 bits per investigation, noise 0.095 → 0.071, and it now passes retention. The salience record and the information display on (records only). Lane K's re-check first, then curiosity: held-out web 0.093 → 0.124 (12 seeds 0.139 → 0.196); the idle re-check is off |
| Wave 2 (ADR-056; `genesis_integration.json`) | Lane L: salience attention (admission takes the admitted candidates in salience order; **off**): held-out knowledge correctness 0.713 alone, the baseline's value; noise share 0.406, the baseline's. An arbitration record (**on**, a record) names the mechanism that lost a subject in a timer pass; results identical with it off. Every background path still goes through `autonomy.admission`; quiet and budgets hold with every link on |

## 8. Knowledge formation

| | |
|---|---|
| What exists | Core: beliefs and graphs; abstraction across epochs only through links. Web: versioned statements, supersession, contradictions asked never decided, provisional only from ≥ 2 independent sources, owner acceptance, temporal validity, source attribution (A §3.10) |
| Gap | No abstraction (one sentence = one claim = at most one knowledge row), no concept hierarchy, no causal relation extraction, no uncertainty propagation; hypotheses lack explicit assumptions, stored expected gain and cost; `abandoned` is never set; nothing re-checks dependents when a premise changes |
| Baseline | retention: 4 of 4 scenarios pass; 7 offline-benchmark metrics recorded as the reference values. No family measures abstraction |
| Priority, lane | Wave 1, research ranks 5 and 8: lane K (hypothesis fields, premise-change re-check, idle re-check of important beliefs) |
| Wave 1 (as shipped) | Lane K: hypothesis fields and premise marks on (records; a mark closes when its subject is re-verified by any normal path); idle re-check off (retention under change 0.695 against 0.693, within noise; owner decision); `abandoned` off; migration 18 (five tables; the improvement ledger refuses UPDATE and DELETE) |
| Wave 2 ([RECURSIVE_KNOWLEDGE_ASSESSMENT.md](RECURSIVE_KNOWLEDGE_ASSESSMENT.md); `genesis_learning_cycles.json`) | Lane F, 14 simulated daily cycles, held-out: the existing loop keeps knowledge more correct than no loop (0.574 → 0.631, 0.497 → 0.571; 8 of 8 seeds) but **knowledge decays in every configuration** (0.88 → 0.44, 0.85 → 0.46); each of lane K's six mechanisms "no evidence". Main limit: a contested refresh never revises a stale item without the owner's answer. Nothing lost; nothing self-generated admitted. Abstraction and concept hierarchies remain not built |

## 9. Learning-to-learn

| | |
|---|---|
| What exists | Core: only `answer_prior` learns from outcomes. Web: ADR-046 calibration mapping (orders candidates); futility down-weighting (A §3.12) |
| Gap | Research-policy weights (updated only by `nsf knowledge learning --update`) and per-mode outcome statistics are written and read by nothing; reasoning depth is fixed rules |
| Baseline | no family measures it directly |
| Priority, lane | Wave 1, research rank 12: lane K (Thompson sampling over research modes from the recorded statistics; ranks only; promotion only through the held-out and withheld gate); lane C (depth adapted from outcomes) |
| Wave 1 (as shipped) | Lane K: strategy selection inactive behind its gate — held-out 0.395 against 0.265 verified per investigation; verdict "revise": the gate needs one withheld run, and the withheld family has no strategy-selection world. Lane C: adaptive depth off — equal quality, 581 → 559 pages, wall time not lower (owner decision) |
| Wave 2 | Lane F: strategy selection in force (through a labelled ledger row, not its real gate) changes nothing in the learning-cycles worlds: one research mode, nothing to rank ("no evidence: not measurable here"). The gate still fails closed: no withheld world evaluates it |

## 10. Generalisation and transfer

| | |
|---|---|
| What exists | Core: epoch continuation; the graph gate's retention check on older held-out data (A §3.12) |
| Gap | No transfer metric anywhere in the product |
| Baseline | the withheld family, run once with `--final` on the unchanged system (ledger: one entry): anticipation breach ticks 137.3 (sd 6.7); web noise share 0.857; counterfactual coverage 0.143; self coverage 0.80 |
| Priority, lane | No lane tunes on it. The main session runs `--final` once, after Wave 1 integration |

## 11. Novel problem-solving

| | |
|---|---|
| What exists | Nothing in either package (both audits: M) |
| Gap | No mechanism. The research review grades the nearest candidates (conceptual blending: speculative, not built; analogy as a creative generator: preliminary, pilot only) |
| Baseline | no family |
| Priority, lane | Not planned. Novel combinations, if any mechanism produces them, are ordinary hypotheses routed into verification (RESEARCH_ASSESSMENT §7) |

## 12. Recursive improvement

| | |
|---|---|
| What exists | Core: versioned, immutable scoring rules the learner cannot change; the graph gate (held-out gain, retention, byte budget, lineage, "beats no graph"); rollback; recorded reversible graph-rule migration (A §3.12). Web: `model_adaptation_guard` refuses unless owner authorisation, isolated evaluation and rollback are all present; no implementation ships |
| Gap | No policy learned from outcomes beyond `answer_prior`; no improvement ledger (retain, revise, revert); no scheduled reflection; nothing turns an error analysis into a changed procedure |
| Baseline | no direct family; the benchmark's rules (held-out gain beyond the baseline's spread, ablation removes it, retention all retained, no adversarial case lost) are the gate |
| Priority, lane | Wave 1, lane K (bounded reflection with traceable outputs, improvement ledger). Directive Levels 4 (model adaptation) and 5 (architecture optimisation) are not built: they need the owner's authorisation ([IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) §6) |
| Wave 1 (as shipped) | Lane K: reflection off (a scheduled reflection while writes are paused is skipped); its optional model summary is an `inference` in the imagined store, derived from the records it phrases, never evidence. The improvement ledger is append-only. Levels 4–5 not built |
| Wave 2 | Lane F: no reproducible improvement across repeated cycles from any mechanism (reflection: identical to the baseline, 14 reports per run, ~3 KB each, kept without a cap). Lane L: the integrated loop 0.710 against the baseline's 0.713 on held-out, +70 % wall time. Levels 4–5 still not built (no owner authorisation) |

## Other findings the directive's §3 asks to classify

| Kind | Finding | Evidence | Where handled |
|---|---|---|---|
| Duplicated | Two autonomy admission paths with different rules | A §3.4 | Wave 0, ADR-050 |
| Duplicated | Five internal-state composites with overlapping schemas | A §3.1 | lane I |
| Uninstrumented | Self-critique precision; the change detector's precision and false alarms | A §3.8 | lane C (`nsf regulation --precision`: alarm precision 0.91 / 0.67 / 0.68 on staged seeds) |
| Underperforming | The idle investigator's ranking (80 % of budget on noise, negative realised gain) | baseline, info_value web | lane I |
| Present but disconnected | Promoted graphs (no consumer outside the core); research-policy weights; mode statistics; Presence's predicting, simulating and planning states; `/api/resources` not rendered | A §3.6, §3.12, §3.13, §3.7 | lanes M, K, W |
| Wrong text | Presence says "writes paused to protect storage" when the cause is memory pressure | `presence/web/interpreter.mjs:27` | lane W |
