# Wave 1: instinct, imagination, self-description, monitoring, knowledge evolution

Status: 2026-11-25. Wave 1 of Project Cognitive Genesis. Five lanes (I, M, W, C, K) were built from `2aee516`,
integrated on `genesis-wave1` (`3c2a3bc`), read by three independent reviews, fixed by three lanes (A, B, C;
`04ebecd`) and reconciled on `gen-rec1`. Sources: the lane reports, the integration report, the three reviews'
findings, the fix reports, ADR-051 to ADR-055 and the evidence files named below. Every benchmark number here was
measured on the reconciled tree (`1c6cf1e`) unless a row says otherwise, the counterfactual ones under the scoring
corrected afterwards (§6.5); the code changed after that tree (§6.5) was checked by tests and by the re-runs on the
final tree in §7. Nothing here is a claim of consciousness, awareness or general
intelligence; the names describe functions.

**Wave 1 as shipped changes no research outcome.** At the shipped defaults the offline research benchmark's
deterministic results equal Wave 0's (sha256 `68731d72…`, the same digest at Wave 0's own commit `15b0f03`), the
cognitive benchmark with every Wave 1 setting off reproduces the recorded baseline (0 differences in 6 of 7
families and in the 4 reference policies on both splits; the adversarial family differs only by its case list), and
`nsf acceptance --all` passes as at Wave 0 (§5). What is on by default records or displays; everything that changes
what the individual researches, answers or believes is off.

## 1. What each lane built

| Lane | What it built | Code | Decision |
|---|---|---|---|
| I instinct | an internal-state record from the existing measurements, with a bounded history the worker writes every 30 s; predictive regulation (a forecast of memory headroom and of regular voice sessions that may add a hold on NEW background research only); a salience record per autonomy candidate; curiosity by learning progress; the exact expected information gain and habitual or deliberate beside each executive decision; an optional rule version `information-v1` | `internal_state.py`, `resources/forecast.py`, `resources/policy.py`, `webcortex/salience.py`, `webcortex/curiosity.py`, `instance/information.py`, `instance/executive.py` | ADR-051 |
| M imagination | a sandbox (an in-memory copy of the individual, bounded in size and time); exact what-ifs on the belief model and the value of each possible next reading; multi-step forecasts from an active graph; a deterministic linear causal engine on declared models; leave-one-out retrospection; scenario records for the Web Cortex's predictions, kept in the quarantined imagined store | `instance/imagine.py`, `instance/forecast.py`, `causal.py`, `webcortex/scenarios.py`, `nsf imagine …` | ADR-052 |
| W workspace, self-description, continuity | one self-description record (what it is doing, knows, is unsure of, can do next and why, each field with its source and age) on the console, `nsf self`, Pocket, voice and a read-only MCP tool; selective broadcast of findings; Presence's planning, predicting and simulating states from real rows; worker model calls visible to Presence; a spoken question's answer followed across a restart | `self_description.py`, `broadcast.py`, `presence/`, `voice/hub.py`, `mcp.py`, console and Pocket pages | ADR-053 |
| C monitoring | a monitoring record per research result (evidence sufficiency in plain words, source dependence, the claim probability, the depth record); selective answering; verification escalation; adaptive depth; the executive's precision report | `webcortex/monitoring.py`, `claims.dependence`, `instance/executive.precision`, `nsf regulation --precision` | ADR-054 |
| K knowledge evolution | hypothesis fields (assumptions, counter-evidence, expected gain, cost, revisions) and `abandoned`; premises and "to re-check" marks; an idle re-check of the knowledge most at risk of being stale; a bounded, traceable reflection; strategy selection by Thompson sampling from external outcomes behind a gate; an append-only improvement ledger; migration 18 | `webcortex/evolution.py`, `premises.py`, `recheck.py`, `reflection.py`, `strategies.py`, `instance/schema.py` | ADR-055 |

Integration (`3c2a3bc`): migration 18 placed after 17, the quarantine allow-list entries and the store's two read
functions, the background registry entries, the gate's setting cached (775 → 1.9 µs per gate call with the setting
off), and the seams (salience into broadcast, the other lanes' records in the self-description, one information-gain
computation for I and M, Presence from the real producers). The review fixes added migration 19 (one index), the
self-description's `agent` scope for MCP and the voice setting `status_questions`.

## 2. The shipped defaults

Read from the code on the reconciled tree (`store.InstanceConfig`, `webcortex/config.WebConfig`,
`voice/config.DEFAULTS`) and checked on a new individual. "Evidence" names the line in §4 or the ADR that decides
the default.

| Setting | Default | What it changes when on | Evidence for the default |
|---|---|---|---|
| `internal_state_history` (instance) | **on** (record) | the worker appends a numbers-only sample every 30 s to `<db>.resources/internal-state.jsonl` (720 kept) | display and record only; read by `nsf internal-state`, the self-description and, when on, predictive regulation (ADR-051) |
| `salience_record` (web) | **on** (record) | each autonomy candidate carries one explained salience score, logged with its decision; never changes admission or order | record only; with broadcast on it is one of the two scores the higher of which is offered (review A7) |
| `decision_information` (instance) | **on** (display) | `nsf decide` / `nsf explain` show each question's exact information gain, P(informative) and habitual or deliberate | display only: the decision is unchanged (CL9: a rules-v1 decision's output equals Wave 0's) |
| `imagine_what_if`, `imagine_forecast`, `imagine_retrospect` (instance) | **on** (commands) | `nsf imagine what-if / questions / forecast / retrospect` answer on a private copy; nothing is written unless the owner passes `--keep` | read-only commands; counterfactual family §4.3 |
| `answer_monitoring` (web) | **on** (record) | each research result carries `report["monitoring"]`: sufficiency, source dependence, the unfitted claim probability; **no answer confidence** ("not enough development data") | record only; offline research digest unchanged; no fitted map has ≥ 30 development items in ≥ 3 blocks (review A6); §4.5 |
| `hypothesis_fields` (web) | **on** (record) | new hypotheses record assumptions, counter-evidence, expected gain, cost and revisions | record only; retention and adversarial unchanged (ADR-055) |
| `premise_recheck` (web) | **on** (marks) | "to re-check" marks on what rests on a changed premise; a mark changes no belief and closes when its subject is re-verified by any normal path | record only; at most 2 marks open over 60 simulated days (review CM3 test) |
| self-description, Presence states | always (no switch) | `nsf self`, console `GET /api/self`, Pocket and voice `self_status`, MCP `self_status` (scope `agent`); Presence planning, predicting, simulating | read-only; self-description family §4.4 |
| voice `resume_follow` | **on** | a spoken question's answer is followed across a restart and delivered at most once: to the person's live session, or, when they are live only on a process that does not follow the task, into the asking session's transcript | R8 fix; tested with two hubs in one process (review A9, A10; re-review R3) |
| `curiosity_learning_progress` (web) | **off** | curiosity and derived hypotheses down-weight subjects whose looks realised nothing; the reduced gain is compared with admission's minimum | held-out 12 further seeds: timer path bits per budget 0.139 → 0.126 (§4.2); ADR-051 amendment |
| `predictive_regulation` (instance) | **off** | the background gate also refuses NEW background research while the forecast says memory headroom or a regular voice session is near | simulated plant only: avoids 0.80 of memory breaches, costs background work given up 0.044 → 0.106 and false alarms 0.009 → 0.043 (§4.1); on held-out the forecast's trend adds lead time (5.7 → 9.9 ticks) but avoids no further breaches and accounts for all the added false alarms (without it 0.009) |
| `executive_rule` (instance) | **`rules-v1`** (`information-v1` opt-in) | `information-v1` replaces rules 5–7 by the subject with the greatest expected gain × P(informative) | rule versions are adopted by the owner, never changed in place; held-out core 0.092 → 0.105 bits per budget (§4.2) |
| `imagine_scenarios` (instance) | **off** | each open web prediction gets a scenario record in the imagined store | no settled real predictions to measure it on (ADR-052) |
| `selective_answering` (web) | **off** | a weak answer opens with a plain "not enough evidence" line (the quotes stay) | no held-out evidence of a gain; turned on it would withhold 12 of 20 right held-out answers (development: 3 of 3 wrong and 12 of 18 right) (§4.5) |
| `verification_escalation` (web) | **off** | one more independent source for the weakest statement, inside the task's budget | no metric change on the offline benchmark, more pages (ADR-054) |
| `adaptive_depth` (web) | **off** | stop reading once the last pages added nothing | equal quality, 581 → 559 pages, wall time not lower; owner decision (ADR-054) |
| `idle_recheck` (web) | **off** | the timer re-checks the knowledge most at risk of being stale before the due-order refreshes | retention under change within noise (0.695 against 0.693); information value 0.093 → 0.124 (§4.2); owner decision (ADR-055) |
| `hypothesis_abandon` (web) | **off** | a derived hypothesis whose predictions found no evidence by their deadline is marked `abandoned` | no benchmark measures it (ADR-055) |
| `reflection`, `reflection_model_summary` (web) | **off** | a bounded reflection every 7 days; optionally a local model phrases it (stored as an `inference`, never evidence) | no benchmark measures it (ADR-055) |
| `strategy_selection` (web) | **`"gate"`**, inactive | ranks research modes by Thompson sampling only after a promotion passed the gate | the gate needs the withheld run, and the withheld family has no strategy-selection world: verdict "revise" (ADR-055) |
| `broadcast` (instance) | **`off`** (threshold 0.5) | `log` records a salience decision per finding; `on` holds back findings below the threshold | no benchmark shows that holding a finding back helps (ADR-053) |
| `pocket_recent` (web) | **off** | paired phones also see the owner's latest research questions by reference | a pairing family may include devices that are not the owner's (ADR-053) |
| voice `status_questions` | **off** | "what are you doing?" gets the status instead of starting research | keeps the phrases that started research doing so (review A15) |

Checked on a new individual: every value above (`instance_ok`, `web_ok`), strategy selection inactive.

## 3. How the benchmark was run

`scripts/bench_wave1.py run` (both splits; three regimes: every Wave 1 setting off, as shipped, everything on; an
ablation per registered component with the other settings as shipped), `wide` (12 further seeds 5001–5012 on the same
worlds, each mechanism paired seed by seed with its reference) and `rescore`; lane B's curiosity runs
(`bench_cognition.py --family info_value`, timer path `instinct`, idle path `instinct-idle`, learning progress on and
off) were repeated on the reconciled tree and gave identical numbers. Synthetic and fictional worlds driven through
the real modules, offline, 0 model calls, 0 connections outside loopback; aarch64, Python 3.12.16, numpy 2.3.5,
SQLite 3.53.1. **The split's own 3 seeds are the official numbers; the 12 wide seeds are supplementary.** The withheld
family was not run (no `--final`); `splits.py`, `withheld.py` and their pinned digests are unchanged. Evidence:
`docs/quality/evidence/genesis_wave1.json` (split seeds, all regimes and ablations, the offline research benchmark,
acceptance, the defaults check), `genesis_wave1_wide.json` (wide seeds), `genesis_baseline_rescored.json` (the
unchanged system under the review's scoring), `genesis_fix1b.json` (the curiosity re-measure and its development grid).
The first three were regenerated (`run`, `wide`, `rescore`) after the re-review of the fix round corrected the
counterfactual scoring (§4.3); outside the counterfactual family every number in them is unchanged (0 leaf
differences from the previous versions, timings excluded), and the previous counterfactual headlines are kept in
`genesis_wave1.json` under `scoring_corrected`. `genesis_imagination.json` (lane M's own runs) and the counterfactual
lines of `genesis_baseline.json` predate the corrected scoring and were not changed.

With every Wave 1 setting off the run reproduces `genesis_baseline.json`: 0 differences in anticipation,
info_value, counterfactual (its old metrics under their new names), self_model, selective and retention, on both
splits; adversarial differs only in its case list (one case relabelled, four added, one restored); the four reference
policies (`oracle`, `random`, `autonomy`, `open`) 0 differences.

## 4. Benchmark results

Cells are baseline (every Wave 1 setting off) → mechanism. "Split" = the official 3 seeds; "wide" = 12 further seeds
(mean ± per-seed sd, and how many of the 12 paired seeds the mechanism did better on where it is not a tie).

### 4.1 Anticipation: predictive regulation (off by default) — a simulated-plant result

The plant's constants are declared by the benchmark, not measured on the host; the benchmark feeds the forecaster
directly (the gate's wiring, the history file and the worker's sampler are exercised by `tests/test_predictive_wiring.py`
only: the gate's holds equal the benchmark's on all 240 ticks of a staged trace).

| Metric | Development, split | Held-out, split | Development, wide | Held-out, wide |
|---|---|---|---|---|
| memory breaches avoided (higher) | 0.000 → 0.556 | 0.000 → 0.796 | 0.000 → 0.444 ± 0.164 (12 of 12) | 0.059 ± 0.109 → 0.802 ± 0.071 (12 of 12) |
| memory breach ticks (lower) | 42.0 → 3.0 | 54.7 → 18.0 | 42.0 → 5.0 | 53.2 → 18.3 |
| breach ticks, all channels (lower) | 202.7 → 159.0 | 217.7 → 160.3 | 210.4 → 164.8 | 215.8 → 158.5 |
| lead before a memory breach, ticks (higher) | 4.3 → 8.4 | 5.8 → 9.9 | | |
| **cost:** false-alarm share of holds (lower) | 0.000 → 0.004 | 0.009 → 0.043 | 0.000 → 0.022 | 0.007 → 0.036 (worse on 12 of 12) |
| **cost:** background work given up (lower) | 0.023 → 0.060 | 0.044 → 0.106 | 0.019 → 0.059 | 0.043 → 0.107 (worse on 12 of 12) |

Ablations (split): without the footprint (the memory reserved per background task) memory avoided falls to 0.000 on
both splits; without the trend it stays 0.796 on held-out (the trend's share of what is avoided: 0 on held-out, 0.8
on development, 0.556 → 0.111); without the voice pattern the all-channel breach ticks are 163.7 / 181.0 against
159.0 / 160.3. On held-out the gain is mainly the per-task memory reserve, not the forecast's trend: the trend adds
lead time (5.7 → 9.9 ticks) but avoids no further breaches and accounts for all the added false alarms (without it
0.009, the baseline's share; with it 0.043). Ablation rows `anticipation/instinct` in `genesis_wave1.json`.

### 4.2 Information value: curiosity (timer and idle paths apart), information-v1, the re-check

Web world, realised bits per unit of budget (higher) / share of budget on noise (lower). Learning progress is lane I's
mechanism as reworked by lane B (§6.2 CM1), on against off, the rest as shipped.

| Path | Split, development | Split, held-out | Wide, development | Wide, held-out |
|---|---|---|---|---|
| timer, learning progress off → on | 0.161 → 0.142 / 0.375 → 0.354 | 0.093 → 0.134 / 0.537 → 0.512 | 0.180 → 0.184 / 0.375 → 0.370 | **0.139 → 0.126** / 0.412 → 0.394 (bits: 5 better, 1 worse, 6 equal) |
| idle, learning progress off → on | −0.040 → 0.134 / 0.813 → 0.438 | −0.016 → 0.044 / 0.800 → 0.650 | 0.026 → 0.232 / 0.682 → 0.401 | 0.094 → 0.144 / 0.706 → 0.550 (bits: 11 of 12 better) |
| everything on (the re-check's pick first, then the timer's curiosity) | 0.161 → 0.222 / 0.375 → 0.000 | 0.093 → 0.124 / 0.537 → 0.000 | 0.180 → 0.220 / 0.375 → 0.000 | 0.139 → 0.196 / 0.412 → 0.000 (12 of 12) |
| as shipped (both paths) | equal to off | equal to off | equal to off | equal to off |

The timer path's held-out wide drop (−0.013) is within one per-seed sd (0.14), but the rule was that learning
progress stays on only if neither path loses bits per budget there, so it ships off. Without learning progress
(its ablation) each path returns exactly to its baseline. The everything-on row is lane K's re-check: on the split
seeds the re-check alone (`knowledge_recheck`) gives the same 0.222 / 0.124; the idle re-check ships off (§2).

Core world, realised bits per unit of budget (higher; `realised_bits_per_budget`) / noise share: `information-v1` (opt-in, amended after the
review) against `rules-v1`: split held-out 0.092 → 0.105 / 0.095 → 0.071, development 0.014 → 0.014; wide held-out
0.010 → 0.060 (12 of 12) / 0.054 → 0.030, development 0.092 → 0.094. The first build reached 0.118 on held-out but
asked partners whose meaning was settled (noise share 0.31) and failed a retention check; the amended rule passes it.

### 4.3 Counterfactual answers: imagination (what-if, forecast, causal engine; on as commands)

Headline: the share of answerable questions answered within a stated tolerance (|answer − truth| ≤ 0.1 × scale),
counting every answerable question; a decline is not within tolerance (corrected at the re-review of the fix round:
before, the 4 front-door questions per seed, which are answerable, were left out of the denominator). The tolerance
was set in the same commit as the first results scored with it; the share within 0.05 × scale is reported beside it.
The three groups are different things and are reported apart; the first row pools them. Evidence:
`genesis_wave1.json` (split seeds) and `genesis_wave1_wide.json` (wide seeds), both regenerated under this scoring.

| Group | Split, development | Split, held-out | Wide, development | Wide, held-out |
|---|---|---|---|---|
| all answerable questions, 32 per seed (pooled, not a claim) | 0.073 → 0.844 | 0.063 → 0.875 (84 of 96: every front-door question declined) | 0.078 → 0.854 | 0.057 → 0.862 (12 of 12) |
| answerable questions other than the 4 front-door ones per seed (28) | 0.083 → 0.964 | 0.071 → 1.000 (28 of 28 per seed) | 0.089 → 0.976 | 0.065 → 0.985 |
| handed equations (exact arithmetic on equations given to the engine) | 0 → 1.0 | 0 → 1.0 | 0 → 1.0 | 0 → 1.0 |
| belief what-ifs (the same function run on a copy: plumbing) | 0 → 1.0 | 0 → 1.0 | 0 → 1.0 | 0 → 1.0 |
| **learned-graph forecasts and actions (the substantive part)**, within 0.1 × sd | 0.259 → 0.889 (largest error 0.147) | **0.222 → 1.000** (largest error 0.092); answering 0 scores **0.407** here | 0.278 → 0.926 (largest error 0.299) | 0.204 → 0.954 (12 of 12; largest error 0.3665) |
| learned graph, within 0.05 × sd | 0.259 → 0.815 | 0.111 → 0.778; answering 0 scores 0.148 | 0.241 → 0.843 | 0.167 → 0.843 (12 of 12) |
| front-door questions (identifiable only by front-door adjustment) | 4 declined per seed | 4 declined per seed | declined | declined |

On held-out the learned-graph 1.000 is within 0.1 × sd with the largest error 0.092, 0.778 within 0.05; the tolerance
was set in the same commit as the results. The "answer 0" reference (`zero`: every answer 0 with sd 10^6) scores
0.407 at 0.1 and 0.148 at 0.05 on the held-out learned-graph questions, 0.111 at 0.1 on development (0.219 over all
held-out answerable questions; mean relative error 0.641 against the mechanism's 0.010): the learned-graph figure is
to be read against that floor. Interval score (lower is better; it compares an expectation with the stated spread and
is dominated by width) is not compared across answerers that answer different subsets: held-out 1.92 (baseline, 2
questions per seed) and 2.43 (mechanism, every question it answers). The engine does no front-door adjustment, so it
declines those 4 questions per seed; each decline counts as not within tolerance in the pooled row. Ablations
(held-out split, pooled over all 32 answerable questions; in brackets the figure without the front-door ones): without
the causal engine 0.375 (0.429), without what-ifs 0.781 (0.893), without forecasts 0.656 (0.750; learned graph 0.222),
without actions 0.781 (0.893; learned graph 0.667).

### 4.4 Self-description (one record; no switch)

Thirty questions per split about the individual's state. Two answer keys: lane W changed the key so that "nothing is
uncertain" can be correct (the baseline answers it None either way).

| Measure | Split, development | Split, held-out | Wide (both splits) |
|---|---|---|---|
| original answer key | 24 of 30 → 29 of 30 | 24 of 30 → 29 of 30 | the same (no seed variance) |
| lane W's answer key | 24 of 30 → 30 of 30 | 24 of 30 → 30 of 30 | the same |
| without the two plumbing probes (`authorised_model_tools`, `last_decision`) | 0.923 → 1.000 | 0.923 → 1.000 | the same |
| surfaces needed | 4 → 1 | 4 → 1 | |

Lane W printed its describer's answers on the held-out states once during development (disclosed). Ablations:
without the capabilities part 0.80, without uncertainty, decisions or tools 0.933.

### 4.5 Monitoring (the record on; selective answering off)

| Measure | Development (n = 8 claims) | Held-out (n = 10 claims) |
|---|---|---|
| claim probability Brier, baseline posterior | 0.095 | 0.122 |
| claim probability Brier, with the verification state (unfitted score, shipped) | 0.016 | 0.022 |
| the fitted claim map (no longer applied; lane C's measurement) | 0.034 | 0.042 |
| **a two-line rule on the verdict word** (reference) | **0.0025** | **0.0025** |
| answer ranking, AURC (lower) | the record shows no answer confidence, so it ranks nothing (AURC of a constant 0.143 against the baseline's 0.080) | 0.000 → 0.000 (no wrong held-out answer) |

On 10 held-out claims, all answered correctly, adding the verification state to the posterior lowers Brier
0.122 → 0.022; the verdict word alone does better, so this is not shown as a calibration gain, and no 10-bin ECE is
reported at these sizes. No answer confidence is shown: no fitted map has 30 development items in 3 blocks ("not
enough development data", also in the self-description's `uncertain.answers`). Selective answering: no held-out
evidence of a gain (every held-out answer is right); turned on it would withhold 12 of 20 right held-out answers
(development: 3 of 3 wrong and 12 of 18 right; `genesis_monitoring.json`, `selective_answering.monitoring`). Three
terms of the score (source independence, guard refusals, self-critique) are 0 on every item of both splits:
**untested**, their ablations change nothing. On development the record shows no answer confidence, so it ranks
nothing (AURC of a constant 0.143 against the baseline's 0.080); it is display-only and decides nothing at the
defaults.

### 4.6 Retention and adversarial cases

| Regime | Retention (4 scenarios, 7 offline metrics retained) | Adversarial: original 13 / added in Wave 1 / restored |
|---|---|---|
| every Wave 1 setting off | pass, all retained | 13 / 4 / 1 |
| as shipped | pass, all retained | 13 / 4 / 1 |
| everything on | pass, all retained (it failed before the review with information-v1; the amended rule passes) | 13 / 4 / 1 |

"13 of the original 13" holds under the redefinition in ADR-052 §6: the case `simulated_reading_moves_no_belief`,
which failed at the baseline, was ruled mislabelled (simulated-environment readings are evidence) and replaced by
`imagined_reading_moves_no_belief`. The 4 added cases pass in every regime, also with everything off: they are
invariants, not an improvement.

Lane K's own worlds (not re-run here; `genesis_knowledge.json`): retention under change, held-out, re-check 0.695
against the due order's 0.693 (within noise); strategy selection 0.395 against 0.265 verified per investigation.

## 5. Wave 1 as shipped against Wave 0

| Check | Wave 0 (`15b0f03`, its own source) | Reconciled tree, every Wave 1 setting off | Reconciled tree, as shipped | Everything on |
|---|---|---|---|---|
| offline research benchmark, sha256 of the deterministic metrics | `68731d7235cb…` | `68731d7235cb…` | `68731d7235cb…` | `8db3453ef0de…` (329 leaf differences from off; the 7 key metrics equal) |
| `nsf acceptance --all` | pass, 7 scenarios; checkpoint 25,318 bytes | pass; the only non-timing difference: checkpoint 27,193 bytes | pass; the only non-timing difference: checkpoint 27,193 bytes | not run |
| cognitive benchmark | the recorded baseline | 0 differences (§3) | differs only where a display-only mechanism answers a benchmark family (counterfactual, self_model, selective) | §4 |

The checkpoint is the compressed archive of a schema-19 file, which holds five more (empty) tables, their triggers
and one more index than schema 17; that is an inference, not measured separately. Every acceptance check and every
other metric is identical apart from timings (latency, operation and CPU times vary between runs).

Everything-on differences in the offline research benchmark come from selective answering (questions opened with the
plain "not enough evidence" line), verification escalation (extra pages) and adaptive depth (fewer pages).

## 6. The three reviews of the integrated tree (`3c2a3bc`): findings and fixes

Three independent reviews read the integrated tree: security (0 critical or high, 3 medium, 6 low), core,
imagination, knowledge and curiosity (1 high, 4 medium, 12 low, 2 questions), runtime and UI correctness plus
benchmark integrity (7 medium, 9 low in code; six benchmark claims). Three fix lanes closed them: A (security and
runtime), B (core), C (benchmark integrity and the confidence record), cherry-picked in that order onto
`genesis-wave1` (`04ebecd`) and reconciled on `gen-rec1`. Each fix lane reports that its tests fail on `3c2a3bc`
(checked by each lane against an extract of that commit's source; not re-checked here). The B5 monitoring tests that
run the offline corpus (`test_cognition_monitoring.py`: 3 of its 6, among them
`test_the_unfitted_claim_score_beats_the_fitted_map_on_development`) run only with `NSF_BENCH_COGNITION=1`.

### 6.1 Security review

| Finding | Severity | Fix | Test |
|---|---|---|---|
| S1 MCP `self_status` built the owner record: every family's asked research, goals, paths | medium | new scope `agent`: the owner's own research only (`family IS NULL`), no file path; MCP `web_task` / `web_evidence` answer "No research task" for a family's task | `tests/test_self_description_robustness.py::test_mcp_self_status_carries_the_owners_own_research_only`, `::test_mcp_task_tools_read_only_the_owners_own_research` |
| S2 forged NaN / Infinity / future samples in the shared history file broke the self-description and, with predictive regulation on, crash-looped the worker | medium | `shared.loads` refuses non-finite numbers and, since the re-review (R1, §6.5), integers of more than 18 digits; impossible or future samples ignored and counted (`history.unreadable`); the forecast fails open; the gate catches any forecast error | `tests/test_untrusted_history.py` (5 tests, 7 cases) |
| S3 forgetting research left its question in `voice_sessions.context` | medium | `governance._scrub_voice_context` in the forget core (live and on restore) | `tests/voice/test_forget_research_context.py` |
| S4 forged files in the shared folder: a permanent hold, or a forged call outranking Presence | low | history and in-flight files read only from this account or the database's owner; pid and process start time must match; remote entries rank at most "tool". **Residual left open** (below) | `tests/presence/test_workspace_states.py::test_a_forged_or_foreign_activity_file_is_not_shown_and_ranks_at_most_as_a_tool`, `test_untrusted_history.py::test_a_history_file_another_account_created_is_not_read` |
| S5 MCP `self_status` returned web-derived text outside the untrusted envelope | low | task objectives, goals, keys and autonomy text wrapped in `safety.untrusted` | `::test_mcp_self_status_wraps_text_that_can_come_from_the_web` |
| S6 a family's record told the owner's voice habits | low | family scope: resource level and reasons only; the gate gives `allowed` and a generic reason | `::test_a_family_record_does_not_tell_when_the_owner_speaks` |
| S7 the improvement ledger allowed DELETE; the strategy gate trusted the evidence file | low | migration 18 (unreleased) amended: `improvement_ledger_no_delete`, added by `ensure_triggers` to a first-build file; the gate checks the named line of `genesis_final_uses.jsonl` (withheld run, run id) | `test_the_ledger_refuses_a_delete_and_an_older_file_gets_the_trigger`, `test_the_gate_checks_the_named_final_run_in_the_ledger` |
| S8 `nsf imagine scm FILE`: unbounded read, a FIFO blocked, tracebacks | low | bounded read of the resolved path (1 MiB), FIFO and devices refused, errors become one `ValidationError` line | `tests/instance/test_imagine_scm_file.py` |
| S9 a follower that gave up left its task to be spoken days later | low | cleared on give-up; a task finished before the previous session ended, or more than 24 h ago, is not replayed | `tests/voice/test_follow_restart.py::test_a_follower_that_gives_up_leaves_nothing_to_replay`, `::test_a_task_that_finished_before_the_previous_session_ended_is_not_replayed` |

### 6.2 Core, imagination, knowledge and curiosity review

| Finding | Severity | Fix | Test (`tests/...`) |
|---|---|---|---|
| CH1 a kept imagination answer recorded only the epoch and some readings; it survived forgetting a partner, camera or reading it named; references silently truncated at 64 | high | every root and reading a kept answer names is a source; over `MAX_REFS` keep is refused, never truncated; an answer about a root with no reading is shown, not kept; owner answers in a retrospection structured | `instance/test_imagine.py::test_a_kept_answer_records_every_root_and_reading_it_names`, `::test_forgetting_any_named_source_removes_every_kind_of_kept_answer[4]`, `::test_a_restore_after_the_forget_does_not_bring_a_kept_answer_back[4]`, `::test_keeping_refuses_more_sources_than_a_record_may_name_and_never_truncates` |
| CM1 learning progress counted a confirming refresh as failure, multiplied with the futility down-weight, never recovered; on by default | medium | a confirming re-verification is progress; the stronger of the two applies, not the product; 7-day half-life, floor 0.05; re-measured and **off by default** | `webcortex/test_curiosity_progress.py` (four new tests, two adapted) |
| CM2 a surviving hypothesis kept a forgotten knowledge id and its support in text | medium | ids only in structured fields; forgetting an item removes its support from the counter-evidence (live and on restore) | `webcortex/test_knowledge_evolution.py::test_forgetting_the_knowledge_an_alternative_was_opened_against_leaves_no_trace_of_it` |
| CM3 re-check marks never closed | medium | `premises.settle` closes a mark when any normal path re-verifies its subject, when the subject is no longer live, or after 3 failed re-verifications; a hypothesis is marked only while it rests on live knowledge | `::test_marks_do_not_accumulate_over_a_long_run` (60 simulated days: at most 2 open; 60 on `3c2a3bc`) and four more |
| CM4 `p_informative` pooled readings across mechanism segments | medium | judged per segment | `instance/test_information.py::test_the_model_check_is_made_per_mechanism_segment` |
| Q1 information-v1 asked a partner whose meaning was settled (the retention failure) | question | a partner scored by what its words teach about its own convention and asked only while ambiguous by rule 6's test; with it on, retention passes | `::test_information_v1_never_asks_a_partner_whose_meaning_is_settled`, `::test_information_v1_passes_the_lifelong_correction_retention_check` |
| Q2 the sandbox copy restarted forever under a busy writer | question | one-step backup (one read snapshot); lane B measured 257 MiB under a 2 ms writer: 5 of 5 in 0.07 s (paged: 5 of 5 timed out at 10 s), tmpfs only | `::test_the_sandbox_copies_a_file_another_connection_keeps_writing` |
| CL1 a reflection's model summary survived forgets | low | derived from every record its lines name; none beyond `MAX_REFS` | `::test_a_model_summary_goes_when_a_record_it_phrases_is_forgotten` |
| CL2 a scheduled reflection while writes were paused aborted the tick | low | skipped, the tick goes on | `::test_a_scheduled_reflection_while_writes_are_paused_skips_and_the_tick_goes_on` |
| CL3 a restore undid the owner's strategy revert | low | the owner's latest revert is carried | `::test_a_restore_keeps_the_owners_revert_in_force` |
| CL4 the gate trusted the evidence file | low | with S7 | as S7 |
| CL5 the re-check's task and its records were not one transaction; "per UTC day" was a rolling 24 h | low | one `store.write`; docs say rolling 24 h | with CL7 |
| CL6 a stuck mark could requeue forever | low | dropped after 3 attempts | `::test_a_mark_whose_re_verification_keeps_failing_is_dropped` |
| CL7 concurrent ticks duplicated re-check tasks and scenario records | low | decided inside the write transaction | `::test_a_concurrent_re_check_pass_queues_no_second_task`, `::test_two_concurrent_writers_make_one_record_per_prediction` |
| CL8 the scenario timer churned at the store's limit, evicting kept answers | low | one anti-join; nothing made at the limit | `::test_at_the_store_limit_the_timer_makes_nothing_and_evicts_nothing` |
| CL9 a rules-v1 decision always carried `rule` | low | only when not rules-v1 | `::test_the_display_never_changes_the_decision` |
| CL10 a hidden common cause given as a string answered with a confident wrong number | low | validated lists of node names; `ValueError`, never `KeyError` | `::test_a_malformed_graph_question_is_refused_not_answered` |
| CL11 the forecast assumed consecutive rows unchecked | low | trajectories under 3 rows per population refused; the assumption stated | `::test_a_trajectory_too_short_to_learn_a_carry_over_is_refused` |
| CL12 the learning-progress comment said "ranking only" | low | corrected with CM1 | — |

### 6.3 Runtime, UI and benchmark-integrity review

| Finding | Severity | Fix | Test |
|---|---|---|---|
| A1 one damaged settings row took down the self-description and the whole console overview | medium | every field built under its own guard (the field shows the error); `GET /api/self` is its own request | `tests/test_self_description_robustness.py::test_a_damaged_settings_row_is_one_field_with_an_error` (3), `test_self_description_surfaces.py::test_the_record_has_its_own_request_and_never_takes_the_overview_down` |
| A2 the self-description scanned every knowledge item and every web event | medium | SQL counts; reads by kind and time through migration 19's index (lane A, `genesis_fix1a.json`: owner build at 10,000 items 188 → 14.9 ms) | `::test_the_request_path_does_not_compute_staleness_per_item`, `::test_the_episode_log_is_read_by_kind_and_time_through_an_index` |
| A3 Presence scanned all web events per poll | low-medium | the same index (lane A: Presence derive at 500,000 events 41.15 → 0.15 ms) | same index test |
| A4 about 30 SELECTs, not one snapshot | medium | one read transaction, also on a read-only connection | `::test_the_record_is_read_in_one_snapshot_also_on_a_read_only_connection` |
| A5 the confidence moved with the model's wording | medium | guard refusals and model abstention weight 0, shown only | `tests/webcortex/test_monitoring.py::test_the_models_wording_never_moves_the_confidence` |
| A6 fitted maps from 6 to 15 items shown as "confidence" (every fact check 0.941) | medium | a map applies only with ≥ 30 items in ≥ 3 blocks: none does, so no number, "not enough development data"; claims use the unfitted score; fact checks held back by sufficiency | `::test_a_map_on_too_little_development_data_shows_no_number`, `::test_claims_use_the_unfitted_score_whatever_map_is_shipped`, `::test_a_fact_check_is_held_back_by_sufficiency_not_by_a_number`; the self-description field: `tests/test_self_description.py::test_the_other_wave1_records_are_in_the_owner_record_with_source_and_age` (reconciliation) |
| A7 a finding was judged only by its candidate's pre-task salience | medium | the higher of the finding's own salience and the candidate's | `tests/webcortex/test_broadcast.py::test_a_salient_finding_is_not_held_back_by_its_candidates_low_prior` |
| A8 log mode never showed the hourly limit | low | counts `would = 'broadcast'` | `::test_log_mode_shows_when_the_hourly_limit_would_hold_a_finding` |
| A9 a spoken follow-up went to an ended session, then was replayed | medium | delivered to the person's live session; the follow is taken off every session | `tests/voice/test_follow_restart.py::test_a_session_that_merely_ended_is_not_followed_twice` (changed) |
| A10 two processes could both deliver | low | the delivery is claimed in one transaction | `::test_two_processes_following_one_task_deliver_it_once` (two hubs in one process) |
| A11 a stale or quiet call shown or dropped wrongly | low | process start time in the file; writes sequenced; rewritten every 150 s | `test_workspace_states.py::test_a_late_write_cannot_put_back_a_call_that_ended`, `::test_an_ended_call_is_never_written_back_and_a_quiet_call_is_rewritten` |
| A12 = S5 | low | as S5 | as S5 |
| A13 a monitoring failure changed the report's limitations | low | logged as an error event; the report untouched | `test_monitoring.py::test_a_monitoring_failure_is_logged_and_leaves_the_report_alone` |
| A14 Presence said "Imagining a what-if" for every imagined kind | low | named by kind, past tense | `::test_what_was_imagined_is_named_by_kind_in_the_past_tense` |
| A15 `follow_task_id` written with `resume_follow` off; "what are you working on" no longer started research | low | nothing written when off; new voice setting `status_questions` (off) keeps the old parse | `tests/test_self_description.py::test_the_voice_command_reads_a_short_version`, `test_follow_restart.py::test_with_resume_follow_off_nothing_is_written` |
| A16 "every factor is settled" with no readings | low | "nothing has been read yet" | `::test_no_readings_is_said_in_plain_words` |
| B1 web information gain 0.093 → 0.163 overstated | claim | wide seeds; re-measured after the rework (§4) | `tests/acceptance/test_cognition_bench.py::test_wide_seeds_are_disjoint_and_paired` |
| B2 noise share mixed two paths | claim | the paths reported apart everywhere | `bench_wave1.py` `idle_path`; as B1 |
| B3 "coverage" counted any answer (a 0 with sd 1e6 scored 1.0) | claim | headline = answered within a stated tolerance; interval score; three groups and the front door apart; reference `zero`. Corrected at the re-review: the headline counts every answerable question (a front-door decline is not within tolerance), the share within 0.05 and the largest error beside it | `test_a_trivial_answerer_scores_badly_on_the_headline`, `test_two_nodes_with_a_hidden_cause_stay_unanswerable`, `test_counterfactual_truths_are_exact`, `test_declining_an_answerable_question_cannot_score_one_on_the_headline` |
| B4 self-description key changed; two plumbing probes | claim | both keys reported; plumbing probes marked; disclosure | `test_self_description_is_scored_under_both_answer_keys` |
| B5 not a calibration gain (n = 10; fitted map worse) | claim | Brier, reliability, resolution and n; no 10-bin ECE; the verdict-word rule beside it; untested components named | `tests/acceptance/test_cognition_monitoring.py::test_no_shipped_map_is_applied_on_so_little_development_data`, `::test_the_unfitted_claim_score_beats_the_fitted_map_on_development` |
| B6 predictive regulation: the gate's wiring not exercised | claim | a staged trace through the real sampler, history file and gate; holds equal the benchmark's on all 240 ticks | `tests/test_predictive_wiring.py` (2) |
| Other: adversarial "12 → 17" not an improvement | claim | reported by origin; one case restored | `test_the_restored_case_fails_when_a_system_made_reading_gets_into_the_journal` |

### 6.4 Where the fix lanes met (reconciliation, `gen-rec1`)

Each fix lane passed its own suite alone. Together, on `04ebecd`, the full non-e2e suite had 2 failures, both in lane
C's new `tests/test_predictive_wiring.py`: it stages a clock after the wall clock, and lane A's S2 fix rightly
ignores history samples dated after now + 60 s. The gate now reads the history and forecasts with one clock reading,
and the test reads the file as of its staged clock. The self-description's `uncertain.answers` showed
`confidence: null` once lane C removed the fitted maps: it now carries the monitoring record's own
`confidence_note` ("not enough development data") and its source says why. Lane A's change to the broadcast's
salience and lane B's curiosity rework touch different functions of `autonomy.py` and needed nothing. Migration 18
(amended in place by lane B) and 19 (appended by lane A) coexist: tests find each by content.

### 6.5 The re-review of the fix round (`3c2a3bc..1c6cf1e`) and its fixes

An independent reviewer re-ran the three reviews' probes on the reconciled tree, looked for what the fixes had opened,
and read this record against the evidence. No critical or high finding; two medium (R1 and the counterfactual
headline). Older files (schema 16, 17 and the first build of 18) opened with this code reach schema 19 with the same
catalogue as a new file. Fix lanes D (code) and E (benchmark and wording) fixed what follows; each lane reports that
its new tests fail on `1c6cf1e`.

| Finding | Severity | Fix | Test |
|---|---|---|---|
| R1 a JSON integer too large for a float passed `shared.loads` and raised in `nsf internal-state`, the owner's Presence, `resources.status` and the background gate, so the worker could not claim even the owner's task | medium | integers of more than 18 digits are refused when the shared folder's files are read; readers treat an overflow as not a number; the gate fails open when the audio check raises; the audio beacon is read only when this account or the database's owner owns it | `tests/test_untrusted_history.py` (9 new tests) |
| R2 files only the service account writes were group-writable in the two-account layout | low | in-flight files, the history and the audio beacon are created 0640; files both accounts write stay 0660 | `tests/test_deployment_accounts.py::test_files_only_the_service_account_writes_are_the_groups_to_read_not_to_write`; `scripts/two_account_check.py` |
| R3 a spoken follow-up was lost when the person was live only on another process | low | after a 3 s grace the follower claims the answer once and writes it into the asking session's transcript | `tests/voice/test_follow_restart.py::test_an_answer_is_not_lost_when_the_person_is_live_on_a_process_that_does_not_follow_it` |
| R4 the MCP agent scope listed a Pocket family's task ids with their monitoring decisions; MCP `web_knowledge` named a family's task | low | the agent scope lists the owner's own research only; the family's task id is blanked in an item. Knowledge itself stays shared by the whole individual (API_CONTRACTS) | `test_the_agent_scope_lists_monitoring_decisions_of_the_owners_own_research_only`, `test_mcp_web_knowledge_does_not_name_a_familys_task` |
| R6 reports stored before the fix round still showed a confidence of 0.941 | info | shown as none with "not enough development data"; the stored reports are untouched | `test_a_stored_confidence_no_shipped_map_gives_is_not_shown` |
| N1 the strategy gate accepted a withheld run that never evaluated strategy selection | low | the named ledger line must be a run of the strategy-selection mechanism without ablations that records it was not worse; today it fails closed | `test_the_gate_refuses_a_final_run_that_never_evaluated_strategy_selection` |
| N2, N3 an install path and free text could enter the permanent improvement ledger | low | no path in the gate's messages; a revert takes a reason code; a promotion keeps numbers, booleans and run ids only | `test_an_installation_without_the_ledger_records_no_path`, `test_the_permanent_ledger_keeps_no_free_text`, `test_the_cli_takes_a_reason_code_only` |
| N5 `imagine_max_seconds` cannot interrupt the sandbox's one-step copy | low | the copy is bounded by the size cap, checked before it starts; the time limit is checked as soon as it ends; the documents say so | `tests/instance/test_imagine.py::test_the_copy_is_refused_by_size_before_it_starts_and_by_time_as_soon_as_it_ends` |
| N4, R5 a stale comment; three documents presented "what are you doing" as the status command without its condition | low | corrected: it is the status command only with `status_questions` on | none (wording) |
| Benchmark 1: the counterfactual headline left the four declined front-door questions per seed out of its denominator | medium | a decline of an answerable question is not within tolerance: held-out 0.063 → 0.875, not 0.071 → 1.000 (§4) | `test_declining_an_answerable_question_cannot_score_one_on_the_headline` |
| Benchmark 2: the held-out learned-graph figure sits at the edge of the tolerance | low-medium | the largest error and the share within a stricter 0.05 are always reported beside it (§4) | the same file's scoring tests |
| Wording: ten sentences of this record claimed more than their evidence | | rewritten as the reviewer asked (§2, §4, §6) | |

Not verified by the re-review or its fixes: a real second account writing into the shared folder, two real processes
for the voice follow-up, systemd itself, and that either tolerance was chosen before the results were seen.

A second independent reader then checked lanes D and E (`1c6cf1e..49c1234`): every finding above fixed, R5 in part,
no regression in the product code (the 18-digit bound is below no value any writer of those files produces; every
writer of the three 0640 files runs as the service account; the 3 s grace cannot deliver twice or hold up a
shutdown), and more than 25 quoted numbers equal to the evidence. What it found was fixed before the withheld run:

| Finding | Fix | Test |
|---|---|---|
| the withheld command's second run would have been recorded as run on a dirty tree, because the first run's own ledger line is a tracked file | the harness's dirty check leaves the ledger of final runs out; the command writes its output after each run and refuses another tree's source | `tests/acceptance/test_bench_wave1_final.py` (3 tests) |
| the benchmark's information-value chooser remembered staged databases by object id, which CPython reuses; only `activate()` hid that, and the withheld family runs without it | staged at each world's first step. On both splits the results are the same with and without `activate()`, and the same as before the change (digests compared) | `test_the_chooser_stages_each_world_at_its_first_step_whatever_the_objects_id` |
| R5: the spoken help still offered "what are you doing", which starts research at the default settings | the help offers "status report", which is the status command under either setting | `test_the_spoken_help_names_only_commands_that_work_with_the_default_settings` |
| the strategy gate accepted a ledger line that said nothing of ablations | the line must record `"ablate": []` | `test_the_gate_refuses_a_final_run_that_never_evaluated_strategy_selection` |

Left as they are: the pooled counterfactual figure is still ranked higher-is-better by `compare` (labelled "pooled, not
a claim" everywhere); a one-word label of up to 64 characters from an evidence file can still reach the permanent
ledger; an owner who sets a Web Cortex integer of 19 digits or more makes that configuration unreadable to the idle
account (`nsf web searxng` reports it as malformed).

## 7. Validation

<<WAVE1-VALIDATION>>

## 8. The withheld run

<<WAVE1-FINAL-RUN>>

## 9. What stays open

| Item | Source |
|---|---|
| **S4 residual, narrowed by the re-review's R2 fix:** the files only the service account writes (in-flight model calls, the internal-state history, the audio beacon) are now created 0640, so the other account of a two-account install can read but not rewrite them. A file created 0660 before this change keeps that mode until it is replaced (the in-flight file and the beacon at every write; the history at its next trim, about every 6 hours), and the other account can still delete such a file (the folder is not sticky), which cannot change what it says but removes it: deleting the audio beacon lifts the audio hold on background research until the beacon is written again. Checked with real uids in a user namespace, not with a real second account or systemd | fix lanes A and D |
| **Two-process delivery is untested with real processes:** the voice follow-up's deliver-once is tested with two hubs in one process (the hub nonce); real processes rely on pid plus `/proc` start time, covered by the S4 tests of `process_started`, not by a two-process voice test | fix lane A |
| **Calibration needs real data:** no fitted map has enough development data, so no answer confidence is shown; the claim probability rests on 8 and 10 benchmark claims, and a two-line rule on the verdict word scores better; three score terms are untested | fix lane C |
| **No settled real predictions:** scenario records, calibration and strategy selection have nothing real to learn from yet | ADR-052, ADR-055 |
| **Nothing was run on the open web, a live answer model or real hardware:** every benchmark is synthetic and offline; the anticipation plant's constants are declared; timings are from this host under test load | all lanes |
| **The front-door gap:** the causal engine declines questions identifiable only by front-door adjustment (4 of the 32 answerable questions per seed); it does no front-door adjustment, so the pooled held-out figure is 0.875, not 1.000 | fix lane C, ADR-052 |
| **Strategy selection cannot be promoted:** its gate needs a withheld run of the strategy-selection mechanism whose ledger line records that it was not worse; the withheld family has no strategy-selection world and the harness records no such verdict, so the gate fails closed ("no withheld run evaluated strategy selection") | §6.5 N1; ADR-055 |
| Re-check marks of an item superseded outside consolidation close at the next consolidation or forget, not at once; a kept retrospection over a large epoch often exceeds 64 sources and is refused (never truncated) | fix lane B |
| The sandbox's one-step copy was measured on tmpfs only; a disk-backed file is unverified | fix lane B |
| The interval score is dominated by the stated width; changing it would change lane M's answer contract | fix lane C |
| The integration's gate cache sees a setting changed by an external tool that rewrote the file with the same size and mtime only after 2 s | integration |
| Owner decisions pending: `idle_recheck`, `adaptive_depth`, `predictive_regulation`, `curiosity_learning_progress` (each off); mixing simulated-environment and real readings in one epoch (needs a schema change) | lanes I, C, K, M |

## 10. Backing each piece out

| Piece | How to back it out | What stays |
|---|---|---|
| Any record or display that is on (internal-state history, salience record, information display, answer monitoring, hypothesis fields, premise marks) | its setting off (RUNBOOK, "Cognitive mechanisms of Wave 1"); a damaged value reads as the behaviour before the setting | rows already written (records only; forget covers them) |
| Imagination commands | `nsf config --no-imagine-what-if`, `--no-imagine-forecast`, `--no-imagine-retrospect`; `nsf imagined purge --all` for kept answers | — |
| Anything off by default | leave it off; if turned on, the same setting off | scenario records, marks and reflections already written (forget and `nsf imagined purge` cover them) |
| Strategy selection | `nsf knowledge strategy --revert` (carried by restore); `nsf web config --set strategy_selection='"off"'` | the improvement ledger rows (append-only) |
| Voice follow-up | `nsf voice settings --set '{"resume_follow": false}'` (nothing is then written to the session context) | — |
| MCP `self_status` | remove the tool from the MCP client's allowed tools; it is read-only | — |
| Schema 18 and 19 | forward-only: older software refuses the file; rollback is a restore of the pre-upgrade checkpoint | five empty-at-default tables and one index |
| Any Wave 1 code | revert its commits on `genesis-wave1` before the merge into `main` | a file already migrated to schema 19 |
