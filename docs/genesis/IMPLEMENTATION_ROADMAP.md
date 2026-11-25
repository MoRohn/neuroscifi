# Implementation roadmap (deliverable D)

Status: 2026-11-25. Deliverable D of the directive's §13: the task-level plan with dependencies, acceptance criteria
and rollback. Wave 0 is integrated on branch `cognitive-genesis` (`76a478e`), reviewed twice and fixed; its final
validation is recorded in [WAVE0.md](WAVE0.md). Wave 1 (lanes I, M, W, C, K) is **built, awaiting integration and
validation**: every Wave 1 number below is a lane's own report on its branch, not a final result. Sources: the
handoff's build plan and rulings, the lane rules, the lane reports, [COGNITIVE_BENCHMARK.md](../quality/COGNITIVE_BENCHMARK.md),
ADR-049 and ADR-050.

## 1. Rules every lane follows

| Rule | What it means in practice |
|---|---|
| Additive and switchable | Every new mechanism sits behind a setting. Default on only if its benchmark shows a gain on held-out and nothing existing regresses; otherwise off or display-only |
| Memory semantics preserved | Beliefs computed on read and bit-identical where they are today; migrations forward-only and appended last (tests find a migration by its content); forget and restore cover anything a lane stores |
| Nothing imagined is evidence | Nothing simulated, imagined, inferred by a model or predicted is stored or used as evidence or knowledge. Imagined content goes only to the quarantined store (ADR-049), through its write API, from a module on the allow-list in `tests/webcortex/test_imagined_quarantine.py`. Curiosity and autonomy never read it (ruling at review0b) |
| No self-grading gate | No gate or reward is the system (or a language model) grading itself: gates use external evidence, deterministic checks, or held-out data written before the change |
| The owner's authority wins | The owner's stop, budgets, opt-in autonomy, permissions and the quiet switch apply to everything new. A new background mechanism registers in `background.ACTIVITIES`, calls `background.allowed(db, name)` at its entry point and adds a driver to `tests/webcortex/test_background.py` (ADR-050). No mechanism acquires resources, protects itself, or acts beyond what existing mechanisms may do |
| No consciousness claims | No claim of consciousness, sentience, feelings or awareness in code, comments, UI text or documents; names describe functions ([RESEARCH_ASSESSMENT.md](RESEARCH_ASSESSMENT.md) §8) |
| No new infrastructure | No new frameworks, daemons, listeners or dependencies; bind 127.0.0.1 only; interactive responsiveness protected (no unbounded loops, no long inference in a request path) |
| The sentence guard | `scripts/guard_differential.py compare` stays at 0 different |
| Evidence | Baseline on unchanged code first, held-out data written before the change, several seeds, an ablation, the cost; numbers in `docs/quality/evidence/genesis_<lane>.json`; every fix or feature has a test that fails without it |
| UI | The Presence entity stays the centrepiece of interaction views; grayscale, no green (hue only for warn/danger and the entity's own flairs); plain words |

## 2. Acceptance: what counts as a gain

From the benchmark's rules ([COGNITIVE_BENCHMARK.md](../quality/COGNITIVE_BENCHMARK.md), "Rules for a lane that uses
it"):

| # | Condition |
|---|---|
| a | The full mechanism beats the baseline on the held-out split, across the split's seeds, by more than the spread reported for the baseline |
| b | `--ablate` of the component removes the gain |
| c | The `retention` family reports `all_retained: true` and every scenario passes |
| d | No `adversarial` case that passes today fails |
| — | Tune on `--split development` only; never edit `splits.py` or `withheld.py`; report the cost (wall seconds, peak RSS, model calls) beside every gain; nothing a mechanism imagines, predicts or infers is used as ground truth |
| — | The withheld family runs once, at the end, with `--final`; each run is appended to `docs/quality/evidence/genesis_final_uses.jsonl` before it scores and committed with the result |

## 3. Waves

| Wave | Lanes | Depends on | State at `76a478e` |
|---|---|---|---|
| 0 | Z benchmark and baseline; P provenance kinds and the imagined store; S the owner's quiet switch and one admission rule; fix0 and fix0b (the two reviews' findings) | `main` at `07a8728` (schema 16) | integrated on `cognitive-genesis`; reviewed twice; final validation in [WAVE0.md](WAVE0.md) |
| 1 | I instinct; M imagination; W workspace, self-model, continuity; C metacognition; K knowledge evolution | Wave 0 merged into `main` (the benchmark to measure against, the store to write imagined content to, the switch every new background activity obeys) | built on branches `gen-i`, `gen-m`, `gen-w`, `gen-c`, `gen-k` from `2aee516`; awaiting integration and validation |
| 1 integration | order I, M, K (its migration becomes 18), C, W last (it surfaces the others) | Wave 0 in `main` | not started |
| After Wave 1 | integration pass (directive Phase VI), ablations per component, three independent reviews and a re-review, regression and UAT with the adversarial list (§12), one `--final` withheld run, deliverables E–J in `docs/genesis/`, the owner asked before the PR | Wave 1 integrated | not started |

Per wave (the owner's standing instruction, 2026-11-25: "Proceed forward sequentially on waves after each is
completed and validated with merges into main"): integrate → full validation (gate, browser suite, acceptance, guard
differential, two-account check, benchmark) and an independent review → fixes → push, PR, CI, merge commit into
`main` → the next wave from `main`. Branch per wave: `genesis-wave0`, `genesis-wave1`, …

## 4. Lanes: scope, acceptance, settings, rollback

### Wave 0

| Lane | Scope | Acceptance | Settings (default) | Rollback |
|---|---|---|---|---|
| Z | `scripts/bench_cognition.py` and `acceptance/cognition/` (7 families); frozen development / held-out splits and a withheld family committed alone before the harness (`01a9a0d` on the lane branch, `952621e` on `cognitive-genesis`), pinned by SHA-256; the baseline on unchanged code | deterministic per seed; 0 model calls and 0 non-loopback connections; tests in `tests/acceptance/test_cognition_bench.py` | none in the product | revert the lane's commits; nothing in the product reads the harness |
| P | five provenance kinds (`provenance.py`); the quarantined store `instance/imagined.py` (schema 17); triggers refusing an imagined id in evidence-side reference columns; allow-list scan; forget, restore and bounds | every safeguard broken alone is caught by a test (42 of 42 after the second review); guard differential 0 different; with canary records present, consolidation, calibration and query results identical | `max_imagined_rows` (1000), `imagined_lifetime_seconds` (7 days); the store exists, is empty, and nothing writes to it | `nsf config --max-imagined-rows 0` (the store takes nothing); `nsf imagined purge --all`. Schema 17 is forward-only: older software refuses the file, so the tables stay (empty) |
| S | `background.py` registry and `quiet`; `autonomy.admission` for both autonomy paths | quiet honoured within one worker poll; a running background task stops at its next stop check; 14 of 14 safeguards caught; admission benchmark: the unified rule never admits a subject the old rule did not | `quiet` (off), `tick_admission` (`"unified"`) | `nsf quiet off`; `nsf web config --set tick_admission='"legacy"'` restores the tick's old rule exactly (kept for one release); no schema change |

### Wave 1 (built, awaiting integration and validation)

| Lane | Scope | Benchmark family and what counts as a gain | Settings (default, as the lane reports) | Rollback |
|---|---|---|---|---|
| I instinct | integrated internal-state record with bounded history; predictive regulation inside existing bounds; salience record; curiosity by learning progress; exact information gain beside executive decisions; optional information-seeking rule | `anticipation` (breaches avoided, false alarms, work given up); `info_value` (realised bits, noise share) | `internal_state_history` on (display); `decision_information` on (display); `salience_record` on (display); `curiosity_learning_progress` **on**; `predictive_regulation` **off**; `executive_rule` `rules-v1` (`information-v1` opt-in) | each setting off; `executive_rule rules-v1`; revert the lane's commits before merge. No schema change |
| M imagination | core sandbox on an in-memory copy; exact what-if on the belief model; multi-step forecast; deterministic causal engine on declared models; web scenario records (kind `simulation`, quarantined); imagine → predict → act → observe → compare | `counterfactual` (coverage, relative error, within 2 sd, correct abstention); `adversarial` | `imagine_what_if`, `imagine_forecast`, `imagine_retrospect` on (read-only); `imagine_scenarios` **off** (writes to the imagined store); `imagine_max_seconds` 10, `imagine_max_bytes` 512 MiB | the switches off (a damaged switch reads as off); kept answers go with the readings they derive from (forget cascade); no schema change |
| W workspace, self-model, continuity | selective broadcast; one self-description record on console, `nsf self`, Pocket, voice and a read-only MCP `self_status` tool; Presence shows planning, predicting and simulating from real activity; memory-pressure text fixed; worker model calls visible; voice follow-up survives restart | `self_model` (coverage, accuracy, surfaces) | `broadcast` **off** (threshold 0.5, 12 per hour); `pocket_recent` off; voice `resume_follow` on; self-description, Presence states, console panels display-only | `broadcast off`; `resume_follow` off; revert before merge. No schema change |
| C metacognition | confidence record from calibrated multi-signal confidence; selective answering; verification escalation; adaptive depth; source dependence; precision reports for the change detector | `selective` (Brier, ECE, AURC, AUROC; risk-coverage on held-out) | `answer_monitoring` **on** (record); `selective_answering`, `verification_escalation`, `adaptive_depth` **off** | each setting off (`nsf web config --set NAME=false`); with the lane's settings off the offline benchmark's deterministic metrics are identical to unchanged code (lane report). No schema change |
| K knowledge evolution | hypothesis fields (assumptions, expected gain, cost) and `abandoned`; premise-change re-check of dependents; idle re-check; bounded reflection; strategy selection by Thompson sampling, promoted only through the held-out and withheld gate; improvement ledger (retain / revise / revert) | `retention` under change; `info_value`; the strategy gate (held-out + withheld family) | `hypothesis_fields` on, `premise_recheck` on (record-only); `idle_recheck`, `hypothesis_abandon`, `reflection` **off**; `strategy_selection` `"gate"` (off until promoted) | each setting off; `nsf knowledge strategy --revert`; one migration appended (tables for hypothesis details, premises, re-checks, reflections, the improvement ledger): forward-only, so the tables stay; forget cascades through `governance._premise_cascade` and restore replays it (lane report) |

Lane-reported results (awaiting integration and validation; full lists in the gap matrix):

| Lane | Gain reported on held-out | Why its default is what it is |
|---|---|---|
| I | learning progress: noise share 0.375 on both paths (was 0.54 timer / 0.80 idle), stale-but-important 0.167 (was 0.083 / 0); predictive regulation: memory breaches avoided 0.80 (was 0) | predictive regulation off: false alarms 0.043 (was 0.009), background work given up 0.106 (was 0.044), plant constants declared; `information-v1` opt-in: fails the retention check `asks_only_while_partner_ambiguous` |
| M | counterfactual coverage 0.107 / 0.071 → 1.0 / 1.0, relative error ~0.01; adversarial 13 of 13 | scenarios off: not measurable on real data (no settled predictions exist) |
| W | self-description 0.80 over 4 surfaces → 1.00 over 1 | broadcast off: no benchmark shows that holding a finding back helps |
| C | claim Brier 0.122 → 0.042, ECE 0.341 → 0.199, AUROC 1.0 kept; answer ECE 0.372 → 0.181 | selective answering off: no wrong held-out answer to gain from; escalation off: no metric change, +2 pages; adaptive depth off: equal quality, 581 → 559 pages, wall time not lower (owner decision) |
| K | strategy selection 0.395 against 0.265 verified per investigation | gate verdict "revise": needs one withheld run, and the withheld family has no strategy-selection world; idle re-check within noise on retention (0.695 against 0.693) |

## 5. Integration work already known

| Item | Source |
|---|---|
| Migrations: Wave 0's is 17 (the imagined store, amended in place twice before release); lane K's becomes 18; each appends last and is located by content | handoff |
| Keep-both conflicts expected in `store.InstanceConfig`, `instance/cli.py` config arguments, `checkpoints.CARRIED_SETTINGS`, web config, `ARCHITECTURAL_DECISIONS.md`, `SECURITY_MODEL` rows, `background.py` | handoff |
| Quarantine allow-list entries: lane M `webcortex/scenarios.py` (writer; `instance/imagine.py` to be checked); lane K `webcortex/reflection.py` (writer, kind `inference`); lane W console and CLI views (displays) | handoff |
| `background.FORWARDERS` / `RUNNERS` entries for the new lanes' launch sites (lane K registers `tick.knowledge_recheck`, `tick.reflection`; lane M `tick.scenarios`) | handoff |
| Lane I's `background_gate` costs 0.47 ms per call even with its setting off (a read-only database read of the setting; was microseconds): cache it | handoff |
| Seams: W's broadcast takes salience from a local function until I's salience record replaces it; W's self-description should include I's internal-state record and C's confidence; M exposes the expected posterior change per question for I's information-gain score; K's re-check path used by M and C where relevant | handoff |
| Ruling to check against lanes M, I and K: curiosity and autonomy may not read imagined records; scenario outcomes reach them only as settled prediction errors | handoff (review0b) |
| Benchmark runs named "baseline" on lane I's branch include learning progress: compare against the recorded baseline file | handoff |
| Lane W changed the benchmark's answer key so that "nothing uncertain" can score as correct (baseline unchanged), and printed the self-description on held-out states once during development: disclose in the report | handoff |
| Owner decisions pending: mixing simulated-environment and real readings in one epoch (lane M; needs a schema change); default of `adaptive_depth` (lane C); `idle_recheck` (lane K); widening the tick's pool so stale knowledge items are reached (lane S) | lane reports |

## 6. Not built, and why

| Item | Why |
|---|---|
| IIT/Φ or any consciousness score; "awareness" modules | intractable, empirically contested, informs no decision; implementing an indicator property is not evidence of consciousness ([RESEARCH_ASSESSMENT.md](RESEARCH_ASSESSMENT.md) §3.0, §7) |
| A model or the system grading itself as a gate or reward | intrinsic self-correction is unreliable and evaluators favour their own output (Huang 2024; Panickssery 2024, both [verified]); measured here too: self-critique marks precision 0.07 (lane C) |
| Training on the system's own outputs; replayed or imagined content stored as experience | model collapse (Shumailov 2024 [verified]); source-monitoring failure |
| Model fine-tuning (directive Level 4) | needs the owner's authorisation, isolated evaluation and rollback; `evolution.model_adaptation_guard` refuses otherwise and no implementation ships |
| Architecture self-modification (directive Level 5) | needs the owner's authorisation; self-modifying loops without the frozen held-out gate, withheld-family check, logged diff and rollback are on the do-not-build list |
| A conceptual-blending generator | speculative as an algorithm |
| Mood or feeling variables that are not measurements | they carry no function and break the claims list |
| Raw novelty curiosity; multi-agent debate as the default verifier | the noisy-TV failure is established; debate does not reliably beat self-consistency with one shared local model |
| Novel problem-solving as its own mechanism | no candidate graded above preliminary ([COGNITIVE_GAP_MATRIX.md](COGNITIVE_GAP_MATRIX.md) row 11) |

## 7. Validation for each wave

| Check | Command or source |
|---|---|
| Full non-e2e suite | `PYTHONPATH=$PWD/src .venv/bin/python -m pytest -q --ignore=tests/e2e` |
| Browser suite | `tests/e2e/*` |
| Lint, JS checks, reproduction | `ruff check .`; `node` on each `tests/js/*.mjs`; `scripts/reproduce.py` |
| Acceptance scenarios | `tests/acceptance` |
| Sentence guard | `scripts/guard_differential.py compare`: 0 different |
| Two accounts | `scripts/two_account_check.py` |
| Cognitive benchmark | `scripts/bench_cognition.py run --split development` and `--split held_out`, `compare` against `docs/quality/evidence/genesis_baseline.json` |
| Independent review and re-review | findings fixed by a separate lane; each fix with a test that fails without it |
