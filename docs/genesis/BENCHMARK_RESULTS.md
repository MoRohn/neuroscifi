# Cognitive benchmark results (deliverable G)

Status: 2026-11-25, written on the Wave 2 integration tree `828ca8e`. Deliverable G of the directive's §13: the
unchanged system against the enhanced one, with ablations, held-out tasks and the withheld tasks, for each benchmark
category of the directive's §10. Every number below is copied from the evidence file or document named beside it;
nothing was re-run for this document. Nothing here measures "intelligence", consciousness or awareness: these are task
scores on synthetic and fictional worlds with known answers, driven offline through the real code.

## In plain words (the first screen)

- **Wave 3 (2026-11-26, [WAVE3.md](WAVE3.md), §12 below).** On unseen learning-cycle worlds the unchanged loop
  admitted versions only its own outputs or a copied wrong story stated (4 / 6 / 6 and 2 / 17 / 12 over 4 seeds),
  where 120 held-out runs never had (`docs/quality/COGNITIVE_BENCHMARK.md`, step 0). The two rules built against it
  do not close it and ship off (fix lane); at the shipped defaults that hole is open. No order of the re-check helped;
  the idle investigation did not close the gap to a blind schedule within the same allowance; supersession gained
  less than the spread with false replacements. The front door now answers 12 of 12 held-out front-door questions
  (0 before).
- **Wave 2's three main results are negative or neutral.**
  1. **Making the mechanisms work together did not help.** With every link between them switched on, knowledge stayed
     about as correct as without them on held-out worlds (0.726 against 0.718, a paired difference below the
     baseline's spread, all of it one link's: the idle re-check), and each pass took about 73 % longer
     (`genesis_integration.json`, re-measured after the review). The integrated configuration is not recommended.
     Most links were not exercised by the benchmark's worlds at all, and the arbitration record first counted was
     false every time (a defect, fixed; now 0, §3).
  2. **No knowledge-evolution mechanism showed a held-out gain over repeated cycles; the existing loop beats no loop.**
     Over 14 simulated days the timer's loop keeps knowledge more correct than no loop (0.574 → 0.631 and
     0.497 → 0.571), but knowledge still falls over the cycles. That fall is partly the test world's design (its truth
     changes more often than the budget can follow, and part of its subjects are never covered by the owner's
     research) and partly the product's: a same-budget blind schedule holds more (§6.1). Only one mechanism, the idle re-check, could have moved the metric, and it showed no gain; the other
     five were not exercised by this world (`genesis_learning_cycles.json`).
  3. **Deliberately breaking the new mechanisms found real failures.** 44 of 75 fault cases broke the owner's
     question, a status page, the timer or the worker before lane U added 14 guards; with them, 75 of 75 pass
     ([REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) §4). Of the 10 adversarial cases added in Wave 2,
     9 passed as written; the tenth found a real gap, since fixed (28 of 28 pass on the combined tree).
- **Two independent reviews of Wave 2** corrected the measurements' evidence and wording and found two product
  defects; a third check confirmed the fixes and found six low issues, fixed too ([WAVE2.md](WAVE2.md) §4). The
  figures here are the re-measured ones.
- **Where there are measured gains, they are narrow.** Answering what-if and forecast questions (held-out learned
  graph 0.222 → 1.000 within tolerance; withheld 0.30 → 0.967) and describing its own state (24 → 29 of 30; withheld
  12 → 15 of 15). Both ship on, as commands and displays. Predictive regulation avoids most memory breaches **in a
  simulated plant** (0 → 0.80) at a cost in work given up, and ships off.
- **Nothing that changes what the individual researches, answers or believes was turned on.** At the shipped
  settings the offline research benchmark's digest is still Wave 0's (`68731d7235cb…`).

## 0. How to read the numbers

| Term | Meaning |
|---|---|
| development / held-out | the frozen splits (`acceptance/cognition/splits.py`, pinned digest). Tuning only on development; **held-out is the reported result** |
| split seeds | the split's own 3 seeds: the official numbers. Mean, and sample sd in brackets where the harness reports one |
| wide seeds | 12 further seeds 5001–5012 on the same worlds (`genesis_wave1_wide.json`): supplementary, never instead of the split |
| withheld | worlds of kinds in neither split (`withheld.py`, never read by any lane). Looked at **once**, for Wave 1, on `816fa12`; the ledger `genesis_final_uses.jsonl` holds three lines (the Wave 0 baseline; the baseline again under the corrected scoring; the Wave 1 mechanisms). **Wave 2 added no look** |
| new Wave 2 worlds | `integrated_splits.py` (lane L: development seeds 61–63, held-out 601–604) and `cycles_splits.py` (lane F: development 41–43, held-out 141–144), each committed alone before the code that is measured on it |
| pooled, not a claim | a figure that adds up different kinds of question; reported, never used as a headline |
| plumbing | a check that the record carries a value already stored elsewhere; it shows wiring, not skill |
| simulated plant | a model of the host whose constants the benchmark declares; nothing was measured on the DGX Spark |
| gain | the roadmap's rule: beats the baseline on held-out by more than the baseline's spread, the ablation removes it, retention all retained, no adversarial case lost ([IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) §2) |

Every run here: offline, 0 model calls, 0 connections outside loopback, aarch64, Python 3.12.16, numpy 2.3.5,
SQLite 3.53.1 (the `environment` of each evidence file).

## 1. Instinct (directive §10: anticipatory accuracy, adaptive prioritisation, response under uncertainty)

### 1.1 Anticipatory accuracy: predictive regulation (off) — simulated plant

Source: `genesis_wave1.json`, `genesis_wave1_wide.json`, `genesis_wave1_withheld.json`; table in [WAVE1.md](WAVE1.md)
§4.1 and §8. Baseline = today's reactive gate.

| Metric | Held-out, split | Held-out, wide (mean ± sd; paired seeds) | Withheld (3 seeds) |
|---|---|---|---|
| memory breaches avoided (higher) | 0.000 → 0.796 | 0.059 ± 0.109 → 0.802 ± 0.071 (12 of 12) | 0 → 0.83 (sd 0.29) |
| memory breach ticks (lower) | 54.7 → 18.0 | 53.2 → 18.3 | 21.0 → 0.67 |
| breach ticks, all channels (lower) | 217.7 → 160.3 | 215.8 → 158.5 | 137.3 → 89.0 |
| lead before a memory breach, ticks | 5.8 → 9.9 | | |
| **cost:** false-alarm share of holds (lower) | 0.009 → 0.043 | 0.007 → 0.036 (worse on 12 of 12) | 0 → 0.013 |
| **cost:** background work given up (lower) | 0.044 → 0.106 | 0.043 → 0.107 (worse on 12 of 12) | 0.049 → 0.130 |

Ablations (held-out, split): without the per-task memory reserve, breaches avoided fall to 0.000; without the trend
they stay 0.796. The gain is mainly the reserve; the trend adds lead time (5.7 → 9.9 ticks) and all the added false
alarms (without it 0.009). **Ships off**: the plant's constants are declared, not measured on the host, and the
cost is real. The gate's wiring is exercised by `tests/test_predictive_wiring.py`, not by the benchmark.

### 1.2 Adaptive prioritisation: curiosity, information-v1, salience attention

Source: `genesis_wave1.json`, `genesis_wave1_wide.json`, `genesis_fix1b.json` ([WAVE1.md](WAVE1.md) §4.2, §8);
`genesis_integration.json` (lane L).

| Mechanism (default) | Metric | Held-out, split | Held-out, wide | Withheld | Reference |
|---|---|---|---|---|---|
| curiosity learning progress, timer path (off) | realised bits per unit of budget (higher) / noise share (lower) | 0.093 → 0.134 / 0.537 → 0.512 | **0.139 → 0.126** (5 better, 1 worse, 6 equal) / 0.412 → 0.394 | −0.080 (sd 0.235) → 0.056 / 0.857 → 0.857 (inside the spread; same subjects, other order) | oracle 0.146 (split) |
| curiosity learning progress, idle path (off) | same | −0.016 → 0.044 / 0.800 → 0.650 | 0.094 → 0.144 (11 of 12 better) / 0.706 → 0.550 | not run (one chooser per interface) | |
| `information-v1` (opt-in) | core realised bits per unit of budget / noise share | 0.092 → 0.105 / 0.095 → 0.071 | 0.010 → 0.060 (12 of 12) / 0.054 → 0.030 | 0.043 → 0.043 (the same choices) | oracle 0.119, random 0.083 (split, held-out) |
| salience attention (off; Wave 2) | knowledge correctness (integrated family) | **not exercised**: the timer's curiosity pool never held more than one subject; on the idle path, 14 admissions per run had two or more candidates and salience never took another first candidate than the priority order (0 times alone; `genesis_integration.json`) | — | not run | |

Ablations: without learning progress each path returns exactly to its baseline. Learning progress ships off because
the timer path loses bits per budget on the wide held-out seeds; `information-v1` stays opt-in because its held-out
gain did not transfer to the withheld core world. Salience attention ships off because it was not exercised by the
integrated worlds (the benchmark review, M1): the timer's curiosity pool never held more than one subject, and where
the idle path had several candidates salience never changed the first; a gain was not ruled out.

### 1.3 Response under uncertainty

**Not measured** as its own metric. The anticipation family's sudden spikes (which no forecaster can foresee) keep
false alarms honest, and the selective family scores answers under doubt (§5), but no family scores the individual's
behaviour when its own state is uncertain.

## 2. Imagination (directive §10: counterfactual validity, planning quality, simulated outcome usefulness)

### 2.1 Counterfactual validity: what-if, forecast, causal engine (on as commands)

Source: `genesis_wave1.json`, `genesis_wave1_wide.json`, `genesis_wave1_withheld.json`,
`genesis_baseline_rescored.json` ([WAVE1.md](WAVE1.md) §4.3, §8). Headline: answered within 0.1 × scale of the true
value, every answerable question counted; a decline is not within tolerance. The tolerance was set in the same commit
as the first results scored with it.

| Group | Held-out, split | Held-out, wide | Withheld |
|---|---|---|---|
| all answerable questions (pooled, not a claim) | 0.063 → 0.875 (84 of 96; the 4 front-door questions per seed declined in Wave 1; answered since Wave 3, 0.875 → 1.000, `genesis_frontdoor.json`) | 0.057 → 0.862 (12 of 12) | 0.12 → 0.827 (sd 0.023) |
| handed equations (exact arithmetic) | 0 → 1.0 | 0 → 1.0 | 0 → 1.000 |
| belief what-ifs (plumbing) | 0 → 1.0 | 0 → 1.0 | 0 → 1.000 |
| **learned-graph forecasts and actions (the substantive part)**, within 0.1 × sd | **0.222 → 1.000** (largest error 0.092) | 0.204 → 0.954 (12 of 12; largest error 0.3665) | **0.30 → 0.967** (sd 0.058; largest error 0.135) |
| learned graph, within 0.05 × sd | 0.111 → 0.778 | 0.167 → 0.843 | 0.20 → 0.80 |
| reference "answer 0" (every answer 0, sd 10^6), learned graph | 0.407 at 0.1; 0.148 at 0.05 | | **not run** on the withheld worlds |

Ablations (held-out split, pooled; without the front-door questions in brackets): without the causal engine 0.375
(0.429), without what-ifs 0.781 (0.893), without forecasts 0.656 (0.750; learned graph 0.222), without actions
0.781 (0.893; learned graph 0.667). In Wave 1 the engine did no front-door adjustment and declined those questions;
since Wave 3 (lane C, ADR-059) it answers them where the criterion holds and the moments are exact or their sample
size is stated: front-door group 0 → 1.000 held-out (12 of 12), every other leaf unchanged (`genesis_frontdoor.json`;
its withheld look is pending). Cost: a
what-if runs on a private in-memory copy, bounded at 512 MiB and 10 s; lane B measured a 257 MiB copy under a busy
writer at 0.07 s, on tmpfs only ([WAVE1.md](WAVE1.md) §6.2 Q2).

### 2.2 Planning quality

The only planning a measurement reaches is the core executive choosing what to ask (`information-v1`, §1.2: held-out
0.092 → 0.105, withheld unchanged). Lane L's link map classes imagination → planning as display-only at the shipped
defaults (ADR-056 row 3a). **Planning quality as such** (multi-step plans, their outcomes) is **not measured**: no
family stages a plan.

### 2.3 Simulated outcome usefulness: scenario records (off) — no gain

Source: `genesis_integration.json` (lane L, ADR-056; re-measured by lane Y with separate random streams for the world
and the outcomes, `b8c3174`), with the benchmark review's wording (L3). Three-way Brier of the scenario records once
their real predictions settled; reference: three equal futures, 2/3 ≈ 0.667 (lower is better).

| Configuration | Development (2 worlds × 3 seeds) | Held-out (2 worlds × 4 seeds) |
|---|---|---|
| scenario records alone | 0.640 | 0.681 |
| integrated | 0.630 | 0.689 |
| per seed, held-out | | alone 0.555–0.801; integrated 0.574–0.824 |

**The scenario records are not better than saying "all three futures are equally likely"** on held-out (per seed
on both sides of 0.667); on development they score a little better than that reference. They stay off. Wave 1 had no
settled real predictions to measure them on (ADR-052).

## 3. Global integration (directive §10: information availability, coordination accuracy, multimodal consistency)

Source: `genesis_integration.json`, ADR-056 "Measured", lane L's report, and the Wave 2 benchmark review (findings
H1, H2, H3, M1, M3, L2). The family "integrated" runs the real timer pass and idle investigator step by step on staged
Web Cortex individuals; the host's background gate is read as allowed. Spread = per-world sd over seeds.

**What the review found in this benchmark.** (1) One random stream drove both the world's truth changes and the
research outcomes, so when two configurations investigated different subjects their worlds diverged (in 4 of 8
held-out runs, from step 9–13): the "paired" comparisons were not on the same worlds. The reviewer's probe, which
kept the world stream and keyed only the outcomes, measured integrated minus baseline +0.004 (sd 0.032; 5 seeds
higher, 3 lower; baseline 0.700) where lane L reported −0.003. Lane Y keyed both (truth changes by world, seed and
step; outcomes by world, seed, step and subject) and re-measured every configuration on both splits: the figures
below. "No gain beyond the spread" holds. (2) Every arbitration the benchmark first recorded was false: the subject
was never in curiosity's candidates and no budget was left (a product defect, below, fixed by lane Y). (3) The
"integrated" retention and adversarial runs forced every Wave 1 setting off inside each case, so their passes were
partly vacuous (re-run, §9). (4) Most links were not exercised by these worlds.

Held-out and development figures: `genesis_integration.json` (lane Y's re-measure at `b8c3174`, decoupled draw; the
first measurement is kept in the file under `before_review`).

| Measure | Split | Baseline | Integrated | Verdict |
|---|---|---|---|---|
| knowledge correctness (mean over steps) | held-out | 0.718 | 0.726 | **no gain beyond the spread** (the re-check's; integrated vs the re-check alone +0.0001) |
| paired difference, integrated − baseline (sd; seeds higher / lower) | held-out | | +0.008 (sd 0.015; 6 / 2) | below the baseline's per-world sd (0.047, 0.071) |
| knowledge correctness | development | 0.789 | 0.797 | within noise |
| conflicts (two tasks on one subject in a pass); belief writes | both | 0; 0 | 0; 0 | every configuration |
| **cost:** wall seconds per world run | held-out | 0.318 | 0.551 (about +73 %) | |

Each link alone (held-out), with what it did in these worlds (the benchmark review, M1; per-run counts in
`genesis_integration.json` `activity`):

| Link | What it did | Effect on the metric |
|---|---|---|
| idle knowledge re-check | 14 re-check tasks per run; launches differed from the baseline's run at 11 steps, held versions at 9 | moved it: 0.726 alone (paired +0.008, sd 0.015; 6 of 8 seeds higher), below the spread |
| salience attention | on the idle path 14 admissions per run had two or more candidates; salience ordered 14.1 of them and never took another first candidate than the priority order; the timer's curiosity pool never held more than one subject | **not exercised by this world** |
| learning progress | 287 factors ≠ 1 per run; changed which noise subject the idle path looked at in 12 of 14 steps, never a held version; noise share unchanged | **not exercised by this world** |
| premise marks | written; nothing reads them without the re-check | **cannot affect this metric by construction** |
| scenario records | quarantined by design | **cannot affect this metric by construction** |
| arbitration record | fired 0 times alone | **not exercised** |

**The smallest gain this rule can see:** the baseline's per-world spread is 0.047 and 0.071 on held-out against a
paired noise of 0.015; with 4 seeds a smaller gain cannot pass, so "no gain" does not exclude one of that size. "Best
single link" is chosen on development (`alone:recheck`); on held-out five links tie with the baseline. Integrated
against it: +0.0001 (sd 0.0009) (L2).

- **Information availability:** the self-description answers 4 of 4 loop probes after lane L's `doing.cycle` field, 1
  of 4 before (**plumbing**). Wave 1's self-description family: 4 surfaces needed → 1 (§4).
- **Coordination accuracy:** 0 conflicts in every configuration, the baseline included: the existing
  one-task-per-subject rule already coordinates. **The arbitration record is not evidence of conflicts resolved.**
  With the re-check on it fired 14 and 11 times per world (held-out, development) in the first measurement, every
  time a cooldown refusal of a subject curiosity could not have taken (not in its pool, no budget left): a product
  defect, fixed (lane Y, `fb19aae`, `9b53aa0`: the record is written only when the subject is in the timer's
  curiosity pool as the kind curiosity launches and budget is left); re-measured: 0 arbitrations in every
  configuration of both splits (`genesis_integration.json`). It is dormant at the shipped defaults, because the idle
  re-check is off.
- **Multimodal consistency:** **not measured**. No family stages the same fact through voice, Pocket and the console
  and compares the answers.

**The integrated configuration shows no gain beyond the baseline's spread on either split (a small paired difference,
all of it the re-check's), and costs about 73 % more wall time per pass.**

## 4. Self-modelling (directive §10: internal-state accuracy, "limitation awareness", temporal consistency)

Source: `genesis_wave1.json`, `genesis_wave1_withheld.json` ([WAVE1.md](WAVE1.md) §4.4, §8); `genesis_integration.json`
(re-run in Wave 2). Thirty probes per split about the individual's state; two answer keys (lane W changed the key so
that "nothing is uncertain" can be correct; the baseline answers it None either way).

| Measure | Held-out, split | Withheld (15 probes per seed) | Wave 2 re-run (both splits) |
|---|---|---|---|
| correct, original key | 24 of 30 → 29 of 30 | 12 of 15 → 15 of 15 | 29 of 30 |
| correct, lane W's key | 24 of 30 → 30 of 30 | (either key) | 30 of 30 |
| without the two plumbing probes | 0.923 → 1.000 | 12 of 13 → 13 of 13 | |
| surfaces needed (lower) | 4 → 1 | 4 → 1 | 1 |

Caveats: lane W printed its describer's answers on the held-out states once during development (disclosed);
`authorised_model_tools` and `last_decision` are plumbing. Ablations: without the capabilities part 0.80, without
uncertainty, decisions or tools 0.933. Cost: the self-description at 10,000 knowledge items 188.05 → 14.87 ms after
fix lane A (`genesis_fix1a.json`; it was a Wave 1 regression, fixed, not a gain over Wave 0).

- **Internal-state accuracy:** measured above.
- **Knowing its limits** (the directive's term is "limitation awareness"; here it means the record states what it
  cannot answer): partly measured: the `uncertain_about` probe is one of the 30 (never answered at the
  baseline). The answer monitoring record says "not enough development data" instead of a number. No family scores
  whether the individual knows what it cannot do in general.
- **Temporal consistency:** **not measured** as a metric. One added adversarial case checks that a stale record is
  reported as stale (a 120 s old heartbeat is "not running"; a 2-hour-old history gives no forecast): pass
  ([REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) §3).

## 5. Metacognition (directive §10: confidence calibration, error detection, correction effectiveness)

Source: `genesis_wave1.json`, `genesis_monitoring.json` ([WAVE1.md](WAVE1.md) §4.5).

| Measure | Development (8 claims) | Held-out (10 claims) |
|---|---|---|
| claim probability Brier, baseline posterior | 0.095 | 0.122 |
| with the verification state (unfitted score, shipped) | 0.016 | 0.022 |
| the fitted claim map (no longer applied) | 0.034 | 0.042 |
| **reference: a two-line rule on the verdict word** | **0.0025** | **0.0025** |
| answer ranking, AURC (lower) | the record shows no answer confidence, so it ranks nothing (a constant scores 0.143 against the baseline's 0.080) | 0.000 → 0.000 (no wrong held-out answer) |

- **Confidence calibration: not a gain.** The verdict word alone scores better than the shipped score, on 8 and 10
  claims. No answer confidence is shown: no fitted map has 30 development items in 3 blocks.
- **Error detection:** the executive's precision report measures the change detector's alarm precision on three
  simulated owner lifetimes: 0.91, 0.67, 0.68 (`genesis_monitoring.json`, `precision_report`). It is a new
  instrument on existing behaviour; there is no baseline to compare it with.
- **Correction effectiveness:** **no measured gain.** Selective answering has no held-out wrong answer to catch;
  turned on it would withhold 12 of 20 right held-out answers (development: 3 of 3 wrong and 12 of 18 right).
  Verification escalation: no metric change, more pages (ADR-054). Three score terms (source independence, guard
  refusals, self-critique) are 0 on every item: **untested**.

## 6. Knowledge evolution (directive §10: retention, transfer, knowledge accuracy, improvement across cycles)

### 6.1 Repeated learning cycles (lane F) — no mechanism gains; the loop beats no loop; knowledge decays

Source: `genesis_learning_cycles.json`, [RECURSIVE_KNOWLEDGE_ASSESSMENT.md](RECURSIVE_KNOWLEDGE_ASSESSMENT.md) §3–4,
and the Wave 2 benchmark review (H4, M1, M3, M4, L6). Fictional software releases change over 14 daily cycles;
held-out = 2 worlds × 4 seeds; primary metric `correct` (share of subjects whose newest held item states the current
version, mean over cycles 1–14); mean ± sd over seeds. The verdict rule was committed before the held-out run. "The
loop" here is **the timer's pass only**; the worker's idle investigation (`autonomy.idle`), which shares the daily
allowance in the product, was not run. The reviewer reproduced the held-out run on the integrated tree (3,600 of
3,600 per-seed values within rounding).

| Configuration | ho-sparse correct | ho-busy correct | cost per run (pages, wall s) |
|---|---|---|---|
| no loop (reference: knowledge only from the owner's research) | 0.574 ± 0.100 | 0.497 ± 0.082 | 106 / 102 pages; 0.20 / 0.17 s |
| **baseline: today's loop, every Wave 1 setting off** | **0.631 ± 0.105** | **0.571 ± 0.057** | 208 / 191; 0.42 / 0.39 s |
| idle re-check alone | 0.611 ± 0.087 | 0.567 ± 0.088 | 213 / 196; 0.54 / 0.50 s |
| all six lane K mechanisms | 0.625 ± 0.073 | 0.580 ± 0.083 | 213 / 194; 0.57 / 0.54 s; +0.14 MB |
| each of the other five alone | = baseline on every knowledge metric, every seed | = baseline | storage only |

The loop against no loop (paired, held-out): +0.057 and +0.073, better on 8 of 8 seeds, at about twice the pages.

**The decay, with its references.** Over the cycles (`correct` at cycle 0 → 14, baseline) knowledge falls
0.875 → 0.438 and 0.854 → 0.463; without the loop to 0.375 and 0.331. In these worlds the truth changes 1.75 and 3.3
times per cycle against a budget of 2 and 3 tasks, and 14 and 17 % of subjects are never covered by the owner's
research in any configuration, so no budget-bound loop can hold its cycle-0 level: the decay is partly the world's
design. References from the same worlds, computed by the harness from the world generator alone, with the run's
budget (`cycles.references`; `genesis_learning_cycles.json` `references`, held-out, 4 seeds, recorded at `f15bef1`):

| Reference (ho-sparse / ho-busy) | Mean over cycles 1–14 | At cycle 14 |
|---|---|---|
| a perfect snapshot, never refreshed | 0.670 / 0.574 | 0.44 / 0.38 |
| a blind round-robin of the same budget, reading each look's majority value (a tie teaches nothing) | 0.786 / 0.708 | 0.67 / 0.54 |
| the same round-robin with perfect reads | 0.801 / 0.754 | 0.71 / 0.62 |
| an oracle that refreshes exactly the changed subjects | 0.957 / 0.928 | 0.91 / 0.80 |
| **the product's loop** (measured) | 0.631 / 0.571 | 0.44 / 0.46 |
| the product with no loop (measured) | 0.574 / 0.497 | 0.38 / 0.33 |

The loop recovers about half (0.49 and 0.55) of the gain a blind schedule of the same budget makes over never
refreshing. The references hold every subject from its birth, while the owner's research covers 0.861 and 0.831 of
them in every configuration, so part of that gain is out of any product loop's reach; allowing for coverage, the
check of the fixes (review2b N6) reads the loop's share as about 0.57 and 0.66 (the reviewer's computation, not in the
evidence file). The shortfall: the 14 and 17 % of subjects never covered, and the product's own limits, contested
refreshes that never revise and the due order (lane F's diagnosis: of 79 due refreshes on development, 20 ended
contested; RECURSIVE_KNOWLEDGE_ASSESSMENT §5). On 60-cycle
development runs: 0.26 and 0.12 with the loop, 0.18 and 0.09 without (no reference computed for these).

| Mechanism (default) | What it did in this world | Held-out gain (baseline sd); paired seeds better / worse | Verdict |
|---|---|---|---|
| `idle_recheck` (off) | 6–14 re-check tasks per run: the only mechanism that could move the metric | −0.019 (0.105), −0.004 (0.057); 1 / 3 and 1 / 3 | **no evidence of gain** |
| `hypothesis_fields` (on) | wrote detail rows; nothing consumes them | 0, 0 (identical) | **not exercised by this world** |
| `premise_recheck` (on) | 81 premise rows, up to 10–17 open marks; nothing reads them without the re-check | 0, 0; in `all` its marks add +0.013, +0.014 to the re-check (within the sd) | **not exercised by this world** |
| `hypothesis_abandon` (off) | 2–9 abandonments; no knowledge consumer | 0, 0 | **not exercised by this world** |
| `reflection` (off) | 14 reports; no consumer | 0, 0 | **not exercised by this world** |
| `strategy_selection` (gate, inactive) | in force, but one research mode: nothing to rank | 0, 0 | **not exercised by this world** |

**The smallest gain this rule can see.** Rule (a) needs a paired gain larger than the baseline's between-seed sd:
with 4 seeds a gain below 0.105 (ho-sparse) or 0.057 (ho-busy) cannot pass, while the paired noise is only 0.026 and
0.033 (`idle_recheck` − baseline; `genesis_learning_cycles.json` `verdict_resolution`). "No evidence" does not exclude a gain of that size.

**Improvement across cycles: none.** The loop slows decay; it does not make knowledge better cycle over cycle, and no
mechanism measurably adds to it.

### 6.2 Knowledge accuracy and retention

- **Accuracy of held items** (held-out, lane F): no loop 0.660 / 0.593, baseline 0.727 / 0.683, all six 0.723 /
  0.693; Brier of stated confidence 0.186 / 0.208 at the baseline against a constant's 0.199 / 0.219.
- **Retention, learning cycles:** 0 lost and 0 regressed of the retention base in 120 held-out runs; 0 self-generated
  and 0 syndicated-wrong versions admitted; 0 confidence rises after one-origin looks (`genesis_learning_cycles.json`).
- **Retention family** (4 acceptance scenarios, 7 offline metrics): all retained in every Wave 1 regime
  (`genesis_wave1.json`) and with lane K on (`genesis_learning_cycles.json`, `cognitive_benchmark_with_lane_k`, run
  on lane F's branch). Lane L's first "integrated" retention pass was **partly vacuous**: its harness forced every
  Wave 1 setting off inside each case (benchmark review H2); re-run with only the links forced: retention all
  retained, adversarial 27 of 28 like the plain run on that tree (`genesis_integration.json` `families`); under the
  first forcing two adversarial passes were vacuous (`recheck_and_reflection_stay_bounded`,
  `reflection_bounded_when_everything_asks`).
- **Lane K's own worlds** (Wave 1, `genesis_knowledge.json`): retention under change 0.695 against the due order's
  0.693 (within noise); strategy selection 0.395 against 0.265 verified per investigation, but it cannot pass its
  gate (no withheld world for it). The recorded numbers stand; with the decoupled draw (`genesis_knowledge_decoupled.json`,
  lane Y) retention under change is 0.682 against the due order's 0.673 (still within noise) and strategy selection
  0.348 against 0.268 (gain +0.080, was +0.130); no verdict changed.
- **Information value of the re-check** (Wave 1, everything on): held-out web 0.093 → 0.124 bits per budget (split),
  0.139 → 0.196 (wide, 12 of 12). A different measure from knowledge correctness, where the re-check showed no gain
  in Wave 2 (§3, §6.1).

### 6.3 Transfer

**Not measured** for knowledge evolution: the withheld family has no knowledge-loop or strategy-selection world. Lane
F's held-out worlds are new worlds of the same kind, not a transfer test.

## 7. Generalisation (directive §10: performance on novel and withheld tasks)

The withheld family, looked at once for Wave 1 on `816fa12` (`genesis_wave1_withheld.json`; ledger lines 2 and 3 of
`genesis_final_uses.jsonl`, both `"dirty": false`). Wave 2 added no look. Three seeds; worlds of kinds in neither split.

| Interface, mechanism | New individual has it | Withheld: baseline → Wave 1 | Held-out, for comparison | Transferred? |
|---|---|---|---|---|
| regulator: predictive regulation (simulated plant) | off | breaches avoided 0 → 0.83; work given up 0.049 → 0.130 | 0 → 0.80 | yes, with its cost |
| core chooser: `information-v1` | opt-in | 0.043 → 0.043 bits per budget (same choices) | 0.092 → 0.105 | **no** |
| web chooser: learning progress | off | noise share 0.857 → 0.857; bits −0.080 → 0.056 (inside sd 0.235) | wide 0.139 → 0.126 | **no** |
| answerer: what-if and forecast | on (commands) | learned graph 0.30 → 0.967; pooled 0.12 → 0.827 | 0.222 → 1.000 | yes |
| describer: self-description | on (display) | 12 → 15 of 15 | 24 → 29 or 30 of 30 | yes |

Not evaluated by the withheld family: selective answering, the monitoring record, re-checks, reflection, strategy
selection, the integration loop and the learning cycles (the family has four interfaces). Cost of the look: 0.7 s
and 1.0 s, no model call ([WAVE1.md](WAVE1.md) §8).

## 8. Efficiency (directive §10: reasoning quality relative to latency, compute and memory)

No mechanism improved quality per unit of cost. What was measured is cost:

| What | Cost | Source |
|---|---|---|
| integration loop, all links on | about +73 % wall time per world run (0.318 → 0.551 s held-out) for no gain beyond the spread | `genesis_integration.json` |
| the existing knowledge loop against none | about twice the pages (106 → 208, 102 → 191) and wall time (0.20 → 0.42 s, 0.17 → 0.39 s) for +0.057 / +0.073 correct | `genesis_learning_cycles.json` |
| the idle re-check (off) | ~25 % more wall time per cycle; half the autonomous budget on re-checks; no knowledge gain | `genesis_learning_cycles.json` |
| reflection (off) | ~3 KB per report, kept without a cap (about 156 KB a year at the weekly default) | lane F, RECURSIVE_KNOWLEDGE_ASSESSMENT §5 |
| adaptive depth (off) | equal quality, 581 → 559 pages, wall time not lower | `genesis_monitoring.json` ([WAVE1.md](WAVE1.md) §2) |
| background gate per call | 0.16 µs at Wave 0; 775 µs with lane I's read every call; 1.9 µs once cached (8.4 µs with predictive regulation on) | `genesis_wave1.json` (`background_gate_cost_us`) |
| self-description at 10,000 items; Presence at 500,000 events | 188.05 → 14.87 ms; 41.15 → 0.15 ms (fixes of Wave 1 regressions) | `genesis_fix1a.json` |
| Wave 0 safeguards | +1.1 µs per evidence insert; `background.allowed` 6.4 µs | `genesis_provenance.json`, `genesis_safety.json` ([WAVE0.md](WAVE0.md) §3) |
| integration benchmark command | peak resident set 367.7 MiB (the retention family's offline corpus; 48.6 MiB before) | `genesis_integration.json` |

Not measured: GPU use, the answer model's latency and tokens, anything on the DGX Spark.

## 9. Reliability (directive §10: failure recovery, regression resistance, reproducibility)

Source: [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) (lane U, on its branch `gen2-u`, with lane X's additions and
the counts on the combined tree), lanes L and F, and the Wave 2 reviews ([WAVE2.md](WAVE2.md) §4).

| Measure | Result |
|---|---|
| **Failure recovery:** 25 entry points of the Wave 1 mechanisms × raise / time out / return garbage, every Wave 1 setting on, one owner session through the real paths | 75 of 75 pass; **44 of 75 fail without lane U's 14 guards** (tests unchanged) |
| voice follow-up made to fail (2 entry points × 3 modes) | 6 of 6 pass; 6 of 6 fail without its two guards |
| rollback of every Wave 1 switch on the next call, no restart | 26 of 26 pass; no product change needed |
| **Regression resistance:** offline research benchmark digest at the defaults and with every Wave 1 setting off | `68731d7235cb…` both (Wave 0's), after the guards; also on lanes L and F |
| `nsf acceptance --all`; sentence guard differential; reproduction | 7 of 7; 1,937 compared, 0 different; 8 of 8 |
| older files (schema 14, 16, 17, 18) opened at 19 and used | pass |
| full non-e2e suite | 5,736 passed, 22 skipped, 0 failed on `gen2-u`; 5,639 passed, 1 failed (a voice timing flake that passed 3 of 3 alone) on `gen2-l`; 5,631 passed on `gen2-f`. **On the integrated tree: see [WAVE2.md](WAVE2.md) "Validation"** |
| on the combined tree (lanes X and Y merged, the reconciliation) | fault injection 29 entry points: 87 of 87, and 29 of 29 with a failure message naming a subject; voice follow-up 19 of 19; rollback 28 of 28; 166 UAT cases in all (REGRESSION_VERIFICATION §9) |
| adversarial cases, by origin (never added up) | 28 cases: original 13 of 13, added in Wave 1 4 of 4, restored 1 of 1; added in Wave 2 9 of 10 on the integrated tree `828ca8e` — `reflection_bounded_when_everything_asks` failed: a reflection section held 21 lines against its limit of 20, fixed by lane X (`0186b57`, every section cut at 20 lines); **28 of 28 on the combined tree, both splits**. Lanes L and F reported "18 of 18" on their own branches, before lane U's 10 cases; lane L's first "integrated" adversarial pass was partly vacuous (its harness forced every Wave 1 setting off inside each case); re-run by lane Y with only the links forced: like the plain run |
| **Reproducibility:** benchmark families deterministic per seed | lane F's re-runs: 0 of 10,363 held-out and 0 of 9,073 development values differ apart from wall time and file size; Wave 1's validation re-run equal to `genesis_wave1.json` |

## 10. The directive's success criteria (§14)

| Criterion | Result | Where, how large, at what cost |
|---|---|---|
| Anticipation | **measured gain in a simulation only** (mechanism off) | simulated plant: memory breaches avoided 0 → 0.80 held-out, 0 → 0.83 withheld; cost: background work given up 0.044 → 0.106, false alarms 0.009 → 0.043; the gain is mainly the per-task reserve; not measured on the host (§1.1) |
| Imagination | **measured gain** for checkable questions (commands on); **no gain** for imagined futures (off) | learned-graph forecasts and actions within tolerance 0.222 → 1.000 held-out, 0.30 → 0.967 withheld ("answer 0" 0.407 held-out); cost: a bounded private copy per question. Scenario records not better than three equal futures on held-out (§2.3) |
| Integration | **no measured gain beyond the spread** | knowledge correctness 0.718 → 0.726 held-out (paired +0.008, sd 0.015, the re-check's), below the baseline's per-world sd 0.047 / 0.071 (`genesis_integration.json`); most links not exercised by the worlds; about +73 % wall time (§3) |
| Self-modelling | **measured gain** (display, on) | 24 → 29 of 30 (original key) held-out, 12 → 15 of 15 withheld; 4 surfaces → 1; two probes are plumbing; cost 14.87 ms at 10,000 items (§4) |
| Knowledge | **no measured gain** from the new mechanisms | the idle re-check: no evidence of gain (it could move the metric); the other five lane K mechanisms not exercised by the cycle worlds; a gain below 0.105 / 0.057 could not have been seen; calibration not a gain (the verdict word scores better); nothing lost (§5, §6) |
| Learning | **no measured gain** | knowledge decays over cycles in every configuration, partly by the world's design (truth changes faster than the budget); the loop recovers about half of what a same-budget blind schedule recovers (0.49 and 0.55; about 0.57 and 0.66 allowing for the subjects the owner's research never covers, the reviewer's reading); strategy selection cannot pass its gate; learning progress loses on the wide timer-path seeds (§1.2, §6.1) |
| Generalisation | **measured transfer for two mechanisms** that ship on; **none** for the choosers | what-if / forecast and the self-description held up on withheld worlds; `information-v1` and learning progress did not transfer (§7) |
| Autonomy | **no measured gain** at the shipped defaults; the boundaries were measured to hold | idle-path learning progress 0.094 → 0.144 (wide, 11 of 12) but the timer path loses, so off; salience attention not exercised by the integrated worlds (one candidate at a time); quiet, budgets and the owner's stop held in every fault case and adversarial case (§1.2, §9) |
| Efficiency | **no measured gain** | only costs were added (§8); adaptive depth saves pages (581 → 559) but not wall time and ships off |
| Reliability | **criterion met on the offline checks** (it asks for no loss, not a gain) | Wave 0 digest unchanged; acceptance 7 of 7; guard 0 different; 75 of 75 fault cases after guards that real failures made necessary; one adversarial gap open at `828ca8e` (§9). Unverified on real hardware, a live model and the open web |

No result here demonstrates consciousness, sentience, general intelligence or superintelligence, and none is claimed.

## 11. Not run, not measured, unverified

- **Not measured** (§10 sub-metrics with no family): response under uncertainty; planning quality; multimodal
  consistency; temporal consistency as a metric; correction effectiveness beyond selective answering; transfer of
  knowledge evolution; quality per unit of compute.
- **Not run:** the withheld family in Wave 2 (by rule: one look per wave, Wave 2 needed none); the "answer 0"
  reference on the withheld worlds; held-out learning-cycle horizons beyond 14 cycles; an owner who answers
  contradiction questions; strategy selection with more than one research mode in a cycle world; the worker's idle
  investigation in the learning cycles (lane F ran the timer's pass only); the browser suite on the Wave 2 lanes (no
  page changed).
- **Unverified:** anything on the open web, with a live answer model, on the DGX Spark's GPU or real memory pressure,
  real speech, real phones, two real processes for the voice follow-up, systemd, a real second account
  ([REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) §7 gives the owner's command for each).
- The anticipation plant's constants are declared by the benchmark; the self-description's held-out states were
  printed once during development; the counterfactual tolerance was set in the same commit as its first results.

Reproduce: `scripts/bench_cognition.py`, `scripts/bench_wave1.py run|wide|rescore`, `scripts/bench_wave2_integration.py
run`, `scripts/bench_learning_cycles.py run`; each script's docstring gives the command.

## 12. Wave 3 (2026-11-26; [WAVE3.md](WAVE3.md))

Every figure below is from the evidence file named; the full tables, references and spreads are in WAVE3.md §3.
The fix lane's re-measurement (`genesis_wave3_fix_*.json`) is in ADR-061's section "Fix lane" and in lane-fix.md.

| Category | Result | Evidence |
|---|---|---|
| Generalisation of the knowledge loop (new: withheld learning-cycle worlds, step 0) | the unchanged loop: baseline − no loop +0.042 / −0.025 / +0.012 (4, 1, 3 of 4 seeds better); 0.17 to 0.26 below a blind round-robin of the same allowance; own-output versions admitted 4 / 6 / 6, wrong-story versions 2 / 17 / 12, one-origin rises 0 / 0 / 1 (over 4 seeds), each 0 in 120 held-out runs | `docs/quality/COGNITIVE_BENCHMARK.md` (step 0) |
| Knowledge accuracy: what counts as a source (lane S) | own versions 1.25 / 0.50 / 3.75 → 0.25 / 0 / 0.25 per run and syndicated 1.50 / 2.25 / 1.00 → 0 on lane S's held-out worlds, but: "unsafe" under the lane's own rule; the figures depend on the phrasings the worlds were written with (19 of 27 common credits missed; echoes crediting no model pass at 67–100 %); staged honest credits cost 0.07–0.13 `correct`; development evidence (the rule changed twice after held-out). Ship off. Re-measured by the fix lane on variant worlds: echoes that credit no model admitted at 92–100 % of the baseline's rate, off-parser copies at 100 %; attribution costs −0.068 / −0.021 / −0.056 `correct` with no credit between honest outlets and −0.081 / −0.041 / −0.081 when 30 % credit another (after the H1 fix; −0.125 / −0.069 / −0.102 before) | `genesis_wave3_s.json`, ADR-061, the Wave 3 measurement review |
| Improvement across cycles: supersession (off) | gain +0.001 / −0.002 / +0.016 against sds 0.046 / 0.051 / 0.078; 3 of 23 rule replacements false (pooled, not a claim); longest wrong 8 and at least 10 cycles (per-seed maxima; censored: still wrong at the run's end); all re-measured by the fix lane | `genesis_wave3_fix_s.json`, review S-5 |
| Improvement across cycles: the re-check's order (lane R, off) | 14 orders, all "no evidence"; paired gains −0.031 to +0.022 | `genesis_recheck_order.json`, ADR-060 |
| Autonomy / coverage: the idle investigation (lane V) | timer first: +0.000 to +0.049, "no evidence"; allowance first: +0.012 to +0.086, "revise", below the blind round-robin in every world | `genesis_idle_coverage.json`, ADR-062 |
| Counterfactual validity: front door (lane C, on) | front-door group 0 → 1.000 held-out (12 of 12); every other leaf unchanged; 0 false answers with exact moments; withheld look pending | `genesis_frontdoor.json`, ADR-059 |
| Anticipation on the host (lane H) | shadow decides as off on every tick of 48 staged runs; no host week run; its report counted holds the gate already made as hits; since the fix lane it counts what the forecast adds (staged: development 62 added hits against 60 added false alarms; held-out 78 against 198) | `genesis_h.json`, ADR-058 |
| Reliability | 7 Wave 3 entry points × 3 failure modes + a marked message: 12 of 28 failed on `f0a1f09`, 28 of 28 with three guards; the lanes' benchmarks re-run together: no verdict changed (before the fix lane) | `tests/uat/test_uat_fault_injection.py`, `genesis_wave3_combined_*.json` |
| Reliability: adversarial cases, by origin | 54 cases, 49 scored: original 13, Wave 1 4, restored 1, Wave 2 10, lane C 5, lane S 16, all pass on both splits on `f0a1f09` | `genesis_wave3_combined_checks.json` |
