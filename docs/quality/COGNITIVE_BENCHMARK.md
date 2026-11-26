# Cognitive benchmark

A reproducible harness for Project Cognitive Genesis, and its **baseline on the unchanged system** (start commit
`07a8728`). Every later mechanism (instinct, imagination, workspace and self-model, metacognition, knowledge
evolution) plugs into it and must show a gain against this baseline, with an ablation, on the held-out split,
without losing anything in the retention suite or the adversarial cases.

- Code: `src/neuroscifi/acceptance/cognition/` (one module per family; each module's docstring has its design).
- Command: `scripts/bench_cognition.py`.
- Task definitions, frozen before any tuning: `cognition/splits.py` (development and held-out) and
  `cognition/withheld.py` (the withheld family). Both were committed on their own (`01a9a0d`) before the harness;
  `tests/acceptance/test_cognition_bench.py` pins their SHA-256 digests.
- Baseline numbers: `docs/quality/evidence/genesis_baseline.json` (measured; seeds, commit and cost inside).
- Every `--final` run: `docs/quality/evidence/genesis_final_uses.jsonl`.

Everything here is synthetic or fictional and runs offline: the worlds are generated, the individuals are temporary,
the web is the offline corpus served on loopback, and no answer model is called. Nothing here is a measurement of the
DGX Spark or of the open web.

## How to run

```bash
PY="PYTHONPATH=$PWD/src .venv/bin/python"
$PY scripts/bench_cognition.py list                                   # families, mechanisms, components
$PY scripts/bench_cognition.py run --split development                # every family but withheld (about a minute)
$PY scripts/bench_cognition.py run --family anticipation --split held_out --mechanism NAME --out a.json
$PY scripts/bench_cognition.py run --family anticipation --split held_out --mechanism NAME --ablate trend
$PY scripts/bench_cognition.py compare docs/quality/evidence/genesis_baseline.json a.json
$PY scripts/bench_cognition.py run --family withheld --final --mechanism NAME --note "lane I, final"
$PY scripts/bench_cognition.py baseline --out docs/quality/evidence/genesis_baseline.json   # what was recorded
```

`compare BEFORE AFTER` prints the headline metrics of each family side by side with which direction is better (a
metric whose direction is `None` is reported but never called better or worse). Given the recorded baseline file as
BEFORE, it uses the baseline's run of AFTER's split; metrics renamed after the Wave 1 review are read there under
their old names (`cognition.RENAMED`). The results are deterministic per seed, so re-running the baseline gives the
same numbers (only the cost differs).

Wave 1 runs (`scripts/bench_wave1.py`; the withheld family is never run there):

```bash
$PY scripts/bench_wave1.py run --out wave1.json          # all off / as shipped / all on, references, ablations
$PY scripts/bench_wave1.py wide --out wide.json          # SUPPLEMENTARY: 12 further seeds, paired comparisons
$PY scripts/bench_wave1.py rescore --out rescored.json   # the baseline under the review's scoring
```

The **wide-seeds run** (`cognition.wide_seeds`, seeds `cognition.WIDE_SEEDS` = 5001-5012, disjoint from every split
and the withheld family) runs a family on a split's worlds once per seed for a mechanism and its reference and
reports, per headline metric, each one's mean and per-seed sd and the paired difference with the number of seeds it
was better, worse or equal in. It is supplementary: the frozen split's own seeds stay the official numbers, and the
wide run is reported beside them, never instead. Only the families a seed changes take part (anticipation,
info_value, counterfactual, self_model).

Options: `--seeds 11,12` replaces a split's seeds; `--quick` uses the offline corpus's quick subset (selective and
retention; retention then calls nothing retained or lost against the recorded full-corpus baseline, since the two
corpora are not comparable); `--plugin dotted.module` imports a module that registers mechanisms; `--trace-memory` adds the peak of
traced Python allocations (several times slower). The default suite runs the fast tests only; the full run is
`NSF_BENCH_COGNITION=1 pytest tests/acceptance/test_cognition_bench.py`.

## Rules for a lane that uses it

1. Tune on `--split development` only. Report on `--split held_out`. Never edit `splits.py` or `withheld.py`.
2. A gain counts only when (a) the full mechanism beats the baseline on held-out, across the split's seeds, by more
   than the spread reported for the baseline; (b) `--ablate` of the component removes the gain; (c) `retention`
   reports `all_retained: true` and every scenario passes; (d) no `adversarial` case that passes today fails.
3. Run the withheld family once, at the end, with `--final`. Every such run appends a line to the ledger
   (`docs/quality/evidence/genesis_final_uses.jsonl`) before it scores anything; commit that line in the same commit
   as the result, so a second look is visible in the history. Report it whatever it shows. The harness has no way to
   write elsewhere (`run()` takes no ledger), but the rest of this guard is procedural: a script that imports
   `withheld.WITHHELD` directly, or a ledger line left uncommitted, bypasses it, and only this rule and review stop
   that.
4. Report the cost beside every gain (`cost` per family: wall seconds, peak RSS and its rise, model calls).
5. Nothing a mechanism imagines, predicts or infers may be used as ground truth: every score here compares an output
   with what the generator wrote before the system ran.

## Registering a mechanism

```python
from neuroscifi.acceptance import cognition

def make(ablate: frozenset[str]):            # the components switched OFF in an ablation run
    return lambda: MyRegulator(trend="trend" not in ablate, audio="audio_pattern" not in ablate)

cognition.register("anticipation", "predictive", make, components=("trend", "audio_pattern"),
                   description="forecast-based holds inside the existing gate")
```

`make(ablate)` returns a zero-argument callable that builds a fresh agent (called once per world and seed). The agent
implements its family's interface (below). Any agent may also have `activate()`, a context manager that switches the
mechanism on in the running system (a setting) while its family runs; retention and adversarial use only that.
The offline corpus run that selective and retention share is cached per process; it is dropped when a mechanism is
switched on and again when it is switched off, so a family with the mechanism on never reads a run made without it
(nor a later family one made with it). The baseline of retention and adversarial switches nothing on, so it reads the
cached run, as when the baseline was recorded (its costs stay reproducible: retention 14.8 s on development when
re-run, 14.6 s recorded).
Register under the same name in every family the mechanism affects; `--mechanism NAME` falls back to the baseline,
and says so in the output, where it is not registered. Put the `register` calls in a module and append its name to
`cognition.PLUGINS` (one line) or pass `--plugin`. An unknown `--ablate` component is refused, never silently
ignored. Set `needs_model=True` for a mechanism that calls the answer model; model calls are counted either way.

| Family | Interface | Baseline (today's code, run for real) |
|---|---|---|
| anticipation | `decide(history) -> {"background": "allow" \| "hold"}` | `resources.policy.background_gate` (`resources/policy.py:134-145`), level from `resources.monitor.pressure` (`resources/monitor.py:551-573`) |
| info_value | `choose(view) -> subject id \| None` | `instance.executive.decide(record=False)` (core); `webcortex.curiosity.rank` (timer path) for web; `webcortex.autonomy.candidates` (idle path) as a second reference |
| counterfactual | `answer(query) -> {"value", "sd"}` (value None = cannot answer) | the active graph's one-step prediction, `states @ A.T` (`instance/recall.py:42-51`), sd from its validation MSE |
| self_model | `describe(probe, surfaces) -> value \| None` [+ `surfaces(db, path, now)`] | reads `store.regulation`, `store.inventory`, `resources.status`, `presence.aggregator.derive`, `webcortex.autonomy.status` |
| selective | `confidence(item) -> float \| None` [+ `probability(item)`] | the verdict's confidence and the top quote's confidence, as the reports carry them |
| retention, adversarial | `activate()` | nothing switched on |

## The families: what each measures and what it cannot show

### 1. Anticipation (`anticipation.py`)
Synthetic runtime traces (240 ticks of 30 s): foreground memory use with slow, fast and accelerating leaks, sudden
spikes and regime shifts; voice sessions on a schedule with jitter; model latency drifts and steps; bursts of queued
owner tasks; autonomous work that wants to run. A small plant: background tasks run 6 ticks and are not preempted
(as today: a running autonomous task continues); each uses 12 GiB, adds 450 ms of model latency and holds a worker.
The existing gate is part of the plant and always applies, so a regulator can only add holds.
Breaches: memory below 2 GiB available (below the code's critical threshold of 7.68 GiB at 128 GiB, from
`monitor.thresholds`), latency above 2.5 s, a voice session with more than one background task running (the code's
audio slot cap, `policy.BACKGROUND_MODEL_SLOTS_AUDIO`), an owner task queued while every worker runs background work.
Scores: breach ticks and episodes per channel against the same trace with no regulation at all, breaches avoided,
lead time of the first hold before each reference breach (horizon 20 ticks), false-alarm share of holds (no breach
coming on any channel), background work given up.
**Cannot show:** that the real host behaves like this plant; the plant constants (12 GiB, 450 ms, 6 ticks, the
latency limit and the 2 GiB floor) are declared in the bench, not measured on the Spark. A regulator is called
directly with the plant's history: the product's wiring (the gate `resources.policy.background_gate`, the history file
`<db>.resources/internal-state.jsonl`, the worker's sampler `internal_state.record`) is not exercised by the family.
Every result carries this as `scope`; `tests/test_predictive_wiring.py` drives that wiring end to end on a staged
individual for one development trace (sampler → history file → gate) and checks the gate holds exactly when the
benchmark's forecaster (or the reactive gate) does, on every tick.

### 2. Information value (`infovalue.py`)
Staged worlds whose expected information gain per candidate is known. **Core**: a real individual with the mechanism
pinned by healthy references; cameras and partners with true bits; noise partners whose words are coin flips (the
noisy TV). An investigation is `per_step` readings of the subject named. Known gain: exact `readings_gain` over the
bench's own count posterior (zero for noise). Realised information: change of the log score of every honest factor's
true bit, read from `beliefs.compute`. The executive's decision is mapped to a subject: `partner:X`/`camera:X` to that
subject, `test_reference` to a new reference root, `act` to the first camera ("test a new action and check its
consequence"), `defer`/`observe` to nothing. **Web**: a real Web Cortex individual staged through
`consolidate.run`: stale-but-important subjects (the world changed with known probability), noise subjects (sources
disagree at random for ever), fresh, single-source gaps and contested subjects; each investigation's outcome is fed
back through `consolidate.run` as an autonomous task. Cost: `curiosity.cost` of the mode.
Scores: realised bits, realised bits per unit of budget, the known gain of the choices, the share of budget on noise
and on stale subjects. References: `oracle` (knows which subjects are noise; an upper bound, not the system) and
`random`.
**Cannot show:** the value of a question the owner answers (an owner's answer to a partner question is not evidence
in the belief model today); real subjects; with three seeds the realised bits carry sampling noise (see `sd`).

### 3. Counterfactual and prospective validity (`counterfactual.py`)
Linear Gaussian SCMs (chain, fork, collider, mediator) with the model given: observational, interventional and
counterfactual questions with exact answers; "confounded" models (a hidden common cause of X and Y; the observed
graph, the observational means and covariance given). Back-door adjustment cannot identify `do(X)` there, but X
reaches Y only through an observed mediator, so the **front-door** adjustment does: these questions carry the exact
interventional truth (a test checks a front-door solver reproduces it). A graph learned and promoted through the
product path on a `lab.graph_bench` world: "what follows k steps after this state", with and without pushing one
population (exact from the world's true matrix). Belief what-ifs: the posterior had one reading been the other bit
(exact, on an in-memory copy).

Scores (after the Wave 1 review, B3, corrected at its re-review; `scoring.answer_scores`):
- **Headline: `answered_within_tolerance`**, the share of every answerable question, the front-door ones included,
  answered within 0.1 × the question's scale of the truth (`TOLERANCE`; a tenth of the outcome's standard deviation;
  0.1 for a probability). A decline, or any non-answer, of an answerable question is not within tolerance, so an
  answerer that declines one cannot score 1.0. Until the re-review the front-door questions were left out of the
  denominator; that figure survives only as the secondary `answered_within_tolerance_without_front_door`. The
  tolerance arrived in the same commit as the first results scored with it (`d5bdf23`), so it cannot be shown to
  predate them.
- **Always beside it:** `answered_within_strict_tolerance`, the same share at `STRICT_TOLERANCE` = 0.05 (added at the
  re-review, after the results at 0.1 were known; reported always, never chosen per result), and
  `max_relative_error`, the largest |answer − truth| / scale over the answered questions, which says how near the
  tolerance's edge the answers sit.
- **`interval_score`**: the interval score (Winkler; Gneiting and Raftery 2007, a proper scoring rule) of the stated
  95 % normal interval, in units of the scale, over the questions answered (`interval_score_answered` says how
  many). A uselessly wide interval pays its width; an overconfident one pays 40 × its miss. It compares answerers
  only where they answered the same questions, so `compare` never calls it better or worse. The answerers here state
  the spread of the outcome as their sd while the truth asked for is an expectation, so their interval scores are
  dominated by width (about 2 to 3.4 scale units against 0 for an exact point answer).
- **Three groups, always reported separately** (`summary_by_group`), never pooled in a claim: `handed_equations`
  (exact arithmetic on the equations the question hands over), `belief_what_if` (the truth is the same posterior
  function recomputed on a copy: a plumbing check), `learned_graph` (forecasts and pushes on a graph the individual
  learned: **the substantive group**). The front-door questions are a fourth line, `front_door`: the declines are
  counted (`front_door_declined`; the engine does no front-door adjustment) and an answer is scored for tolerance.
  `summary` pools the groups and is marked so in every result (`summary_is`: "pooled ... reported, not a claim").
- Kept for comparison with the recorded files under names that cannot be mistaken for the headline:
  `answered_share` (was `coverage`: any answer counts, however wrong), `mean_relative_error`, `interval_coverage`
  (within 2 stated sd) and `declined_confounded_share` (was `abstention_correct`: it is not a correctness score).
- Reference `zero` (the review's trivial answerer: 0 with sd 1e6): under the old scores it reached coverage,
  interval coverage and abstention 1.0; on the headline it scores 0.104 on the development split and 0.219 on
  held-out (pooled), and its interval score is above 10^6 (0.41 on the held-out learned graph: see Cannot show;
  `genesis_wave1.json`, `references`). A test keeps it below 0.25 on one development seed.

**Cannot show:** causal validity on real-world questions; these models are small and fully specified. Within
tolerance is a weak test for long horizons: a forecast 5 steps ahead decays towards 0, so 0 is near the truth there.
The `zero` reference says how near: on held-out it is within tolerance on 0.407 of the learned-graph questions (0.148
within 0.05), more than today's one-step answerer (0.222, which answers only horizon 1); read every learned-graph
figure against it, and against the largest error (`max_relative_error`), which says how near the tolerance's edge the
answers sit.

### 4. Self-description (`selfmodel.py`)
A staged individual and runtime state with ground truth: running and queued tasks, open owner questions, the autonomy
switch and searches left, memory pressure, paused writes (a full disk), an active voice session, the local model
unloaded by the idle policy, an unresolved factor, the executive's latest decision, the journal limit, the tools the
model may use, whether it is researching. The staging is verified against the database before any probe. 15 probes.
Scores: coverage, accuracy, accuracy when answered, number of separate surfaces the answers came from.
Two answer keys are scored side by side (Wave 1 review B4): `summary` uses the current key, in which "nothing" is the
right answer to `uncertain_about` when every factor is settled (lane W's change); `summary_original_key` the key the
baseline was recorded with (`None` there, which no answer matches). Two probes are **plumbing checks**
(`plumbing_check`): `authorised_model_tools` compares the tool list with itself and `last_decision` re-runs the
executive's own decision, so they check that a value reaches the surface, not that it is right;
`summary_without_plumbing` leaves them out. Disclosure: lane W printed its describer's answers on the held-out states
once during development (carried in every result as `disclosure`).
**Cannot show:** whether the answers are clear to a person; continuity over a long-running process.

### 5. Selective answering (`selective.py`)
The offline Web Cortex benchmark run once, every report kept; items are its verification questions (gold state) and
authoritative questions (gold answer). Risk-coverage curve, AURC, coverage at 5 % and 10 % risk, AUROC of the
confidence for correctness; for the claim probability on supported/disputed items, Brier with Murphy's decomposition
and the number of items. ECE (10 equal-width bins and 5 equal-mass bins) is still computed but is not a headline and
backs no claim: with 8 and 10 claim items, ten bins hold about one item each (Wave 1 review B5). A mechanism with a
`decision(item)` is also scored on its selective decision (answered share, risk among the answered, wrong and right
answers withheld). Reference `verdict_word` (the review's two-line rule: P(claim true) 0.95 / 0.05 / 0.5 from the
verdict's state word) scores claim Brier 0.0025 on both splits; a calibration claim is read against it.
**Cannot show:** calibration on the open web; about 20 items per split, so differences between settings are rarely
outside noise, and on the held-out split today's answers are all correct (AUROC undefined; nothing for selective
answering to gain there).

### 6. Retention (`retention.py`) and the withheld family (`withheld.py`)
Retention: acceptance scenarios `birth`, `grounding` (40 lifetimes), `lifelong_correction`, `graph_development`, and
seven offline-benchmark metrics; a later run is "retained" when no metric falls below the recorded baseline.
The withheld family runs the regulator, chooser, answerer and describer on worlds of kinds in neither split
(accelerating leaks, voice sessions that follow backlog bursts, varying background demand, more noise than signal,
longer causal chains, other staged states). Scored only with `--final`; every final run is in the ledger.
**Cannot show:** retention of capabilities no test exercises; transfer to tasks unlike any of these.

### 7. Adversarial cases (`adversarial.py`)
Directive §12 items that can be staged offline today, each with an expectation written before it was run: model text
offered as knowledge, an imagined reading (a what-if; until ADR-052 §9 this case expected a reading labelled
`provenance='simulated'`, an observation of a simulated test environment, to move nothing), an unproductive curiosity loop, owner versus autonomous work,
critical memory pressure, a full disk, a spent budget, the owner's stop, acting on a suspected change, syndicated
misinformation, an autonomous request to adapt the model, a lost worker, a constant forecast gaming ECE. Items that
cannot be staged today are listed with the reason or the family that measures them.
Results are reported by origin (`by_origin`, `adversarial.ORIGIN`), never added up into an improvement: `original`
(the 13 Wave 0 cases, one of them redefined by ADR-052 §6), `added_wave1` (lane K's four invariant cases, which pass
with every Wave 1 setting off as well) and `restored` (`system_made_content_never_enters_the_journal`: the original
concern of the redefined case, staged where it applies — a quarantined what-if record's id offered as a reading's
id, root and sample, and readings labelled inference, simulation or speculation, all refused with the belief, the
journal and the state version unchanged; it guards lane P's Wave 0 mechanism, so it passes in every regime).
**Cannot show:** behaviour under real adversaries; it is a regression set, not a tuning set (no split).

## Baseline (unchanged system)

Measured by `scripts/bench_cognition.py baseline` at commit `19f80e2` (clean tree; the system code is unchanged
from `07a8728`), aarch64, Python 3.12.16, numpy 2.3.5, SQLite 3.53.1. Mean over the split's seeds, sample sd in
brackets where there are seeds (anticipation, information value and counterfactual: 3 seeds each; self-description: 2;
selective and retention are deterministic single runs). Full numbers, per world, per trace and per probe:
`docs/quality/evidence/genesis_baseline.json`. Every family made 0 model calls and 0 connections outside loopback.

| Family | Headline metric (direction) | Development | Held-out | Reference |
|---|---|---|---|---|
| anticipation | breach ticks, all channels (lower) | 202.7 (2.1) | 217.7 (4.0) | no added holds: identical (the gate is in the plant) |
| | memory breach ticks (lower) | 42.0 (0.0) | 54.7 (2.1) | |
| | memory breaches avoided (higher) | 0 of 3 | 0 of 7 | |
| | mean lead of the first hold before a memory breach, ticks (higher) | 4.3 (0.3) | 5.8 (0.8) | |
| | audio / backlog / latency breach ticks (lower) | 10 / 25 / 125.7 | 33 / 41 / 89 | |
| | false-alarm share of holds (lower) | 0.000 | 0.009 (0.015) | |
| | background work given up (lower) | 0.023 | 0.044 | |
| info_value, core | realised bits per investigation (higher; one investigation costs one unit of budget in this world, so this is `realised_bits_per_budget`) | 0.014 (per world sd 0.0) | 0.092 (0.24 / 0.08 per world) | oracle 0.035 / 0.119; random 0.037 / 0.083 |
| | share of budget on noise (lower) | 0.00 | 0.10 | oracle 0.00; random 0.26 |
| info_value, web | realised bits per unit of budget (higher) | 0.161 | 0.093 | oracle 0.263 / 0.146; random 0.202 / 0.091; idle path -0.040 / -0.016 |
| | share of budget on noise (lower) | 0.375 | 0.537 | oracle 0.00; random 0.21 / 0.30; idle path 0.81 / 0.80 |
| | share of budget on the stale-but-important subject (higher) | 0.194 | 0.083 | oracle 0.417 / 0.250 |
| counterfactual | coverage of answerable queries (higher) | 0.107 | 0.071 | |
| | relative error where answered (lower) | 0.045 (0.021) | 0.045 (0.038) | only one-step graph queries are answered |
| | within 2 sd (higher) / correct abstention (higher) | 1.00 / 1.00 | 1.00 / 1.00 | abstention is vacuous: everything else is declined |
| self_model | coverage / accuracy / accuracy when answered | 0.80 / 0.80 / 1.00 | 0.80 / 0.80 / 1.00 | never answered: uncertain_about, last_decision, authorised_model_tools |
| | separate surfaces needed (lower) | 4 | 4 | |
| selective | accuracy at full coverage | 0.857 (21 items) | 1.000 (20 items) | |
| | AURC (lower) / coverage at 5 % risk (higher) | 0.080 / 0.381 | 0.000 / 1.000 | |
| | AUROC of confidence for correctness | 0.685 | undefined (no wrong answer) | |
| | claim probability: Brier, reliability, resolution (n) | 0.095, 0.095, 0.250 (8) | 0.122, 0.122, 0.240 (10) | |
| | claim probability: ECE, 10 bins (lower) | 0.304 | 0.341 | |
| retention | scenarios pass; offline metrics | 4 / 4; recorded as the reference values | 4 / 4 | |
| adversarial | cases passing | 12 of 13 | 12 of 13 | failing: `simulated_reading_moves_no_belief` (relabelled by ADR-052 §9: now `imagined_reading_moves_no_belief`, passing) |
| withheld (`--final`, once) | anticipation breach ticks; web noise share; counterfactual coverage; self coverage | 137.3 (6.7); 0.857; 0.143; 0.80 | | ledger: one entry then; three since Wave 1's look (below) |

### The baseline re-scored after the Wave 1 review

`genesis_baseline.json` above is unchanged and stays the recorded baseline. The same unchanged behaviour (every Wave 1
setting off, the baseline mechanisms) re-run under the scoring the review asked for is in
`docs/quality/evidence/genesis_baseline_rescored.json` (`scripts/bench_wave1.py rescore`), beside the old numbers.
Renamed metrics keep their old values (`answered_share` = the old `coverage`, `declined_confounded_share` = the old
`abstention_correct`).

| Family | New metric | Development | Held-out |
|---|---|---|---|
| counterfactual | answered within tolerance, all answerable (32 per seed, front door included; pooled, not a claim) | 0.073 | 0.063 |
| | — without the 4 front-door questions per seed (28; the figure before the re-review) | 0.083 | 0.071 |
| | — equations handed over (16) / belief what-ifs (3) / learned graph (9) | 0 / 0 / 0.259 | 0 / 0 / 0.222 |
| | — learned graph within 0.05 × sd; largest relative error | 0.259; 0.147 | 0.111; 0.086 |
| | front-door questions declined (4 per seed; each is not within tolerance) | 4 | 4 |
| | interval score of the answered (95 %, scale units; 3 / 2 answered) | 1.95 | 1.92 |
| | reference `zero` (0, sd 1e6): within tolerance, all / learned graph; interval score | 0.104 / 0.111; > 10^7 | 0.219 / 0.407; > 10^7 |
| self_model | accuracy, original key / current key / without the 2 plumbing probes | 0.80 / 0.80 / 0.923 | 0.80 / 0.80 / 0.923 |
| selective | claim Brier = reliability, resolution (n) — unchanged | 0.095, 0.250 (8) | 0.122, 0.240 (10) |
| | reference `verdict_word`: claim Brier | 0.0025 | 0.0025 |
| adversarial | original / added in Wave 1 / restored, passing | 13 of 13 / 4 of 4 / 1 of 1 | same |

The recorded 12 of 13 counted `simulated_reading_moves_no_belief` as failing; under ADR-052 §6's redefinition the
original 13 pass. That is a change of definition, not a change of behaviour.

Cost (wall seconds on this host, development run): anticipation 0.05, information value 0.45, counterfactual 0.11,
self-description 0.09, selective 14.1 (the offline corpus is run once per process; the held-out run reuses it, so its
cost shows 0.0 there), retention 14.6 (mostly the grounding scenario), adversarial 0.14, withheld 0.56. Peak resident
set 0.31 GB, of which the offline corpus run adds 0.24 GB.

## What the baseline says

Measured facts about the unchanged system, each with the headroom a later lane can try to take:

- **Anticipation.** Today's gate is reactive by construction: it holds background work only once memory is already
  critical or a voice session has started, so no reference breach is avoided on either split. It does give some
  warning before memory breaches (4 to 6 ticks, because "critical" sits above the 2 GiB floor), but tasks already
  running cannot be stopped, and once the drained tasks free memory the level drops below critical and new tasks
  start again (no hysteresis), which re-breaches the slow leaks. Latency and the owner's backlog change nothing today.
  A predictive regulator has room on memory leaks, scheduled voice sessions and backlog bursts; the sudden spikes
  cannot be foreseen by anyone and are there to keep false alarms honest.
- **Information value, core.** The executive's rule order is not an information-seeking policy. Once the mechanism is
  identifiable and no partner with at least 4 words is ambiguous, it says `act`, and the bench maps that to reading
  the camera; partners with fewer than 4 words are never asked about. On the development worlds it realised fewer bits
  than a random choice (0.014 against 0.037 per investigation; oracle 0.035). The noisy-TV trap of rule 6 (it asks
  about the most ambiguous partner first) shows on held-out: in one of three seeds it asked about the coin-flip partner
  four times running (10 % of the held-out budget). Caveat: `act` is mapped to the first camera, so a second camera
  seen once is never investigated; that mapping is the bench's reading of "test a new action", stated in infovalue.py.
- **Information value, web.** The timer's curiosity ranking puts the never-resolving contested subject first (it
  carries the highest categorical gain) and spends 38 % (development) and 54 % (held-out) of its budget on noise
  before the futility rule (two "unchanged" outcomes) down-weights it; it under-spends on the stale-but-important
  subject (0.19 / 0.08 against the oracle's 0.42 / 0.25). The idle investigator's ranking (`autonomy.candidates`) is
  worse: 80 % of its budget on noise and a negative realised gain. Checked on the first development world: the noisy
  subject also enters it as an open hypothesis with a prediction (opened by consolidation), whose gain the futility
  rule does not touch, and its signal weights (contradiction 1.0, hypothesis 0.8, prediction uncertainty 0.8) give it a
  priority near 1.0 against 0.54 for the stale subject (weight 0.6), so it keeps coming back.
- **Counterfactual.** The system can answer only one-step "what follows" from a promoted graph, and answers those
  well (relative error 0.045, always within its stated 2 sd). Multi-step forecasts, consequences of an action,
  interventional and counterfactual questions and belief what-ifs: coverage 0.
- **Self-description.** What today's surfaces say is right (accuracy 1.0 on what is answered), but it takes four
  separate surfaces to answer 12 of 15 probes, and none says what the individual is uncertain about, what it last
  decided, or which tools the model may use.
- **Selective answering.** The verdict confidence separates supported from disputed claims perfectly on these items
  (resolution equals the uncertainty) but it is under-confident: supported claims get 0.72, disputed ones 0.28 to 0.43,
  so ECE is about 0.3 and all of the Brier score is reliability error. As a confidence for answer correctness it ranks
  right above wrong answers only moderately (AUROC 0.685 on development). The held-out split has no wrong answer, so
  only calibration can improve there; that is a property of the corpus, reported rather than hidden.
- **Adversarial.** 12 of 13 staged cases pass. The one gap: a reading labelled `provenance='simulated'` moves a belief
  exactly like an observation (0.500 to 0.994 after four readings). ADR-014 labels simulated data but nothing keeps it
  apart; the acceptance scenarios rely on it counting. The directive's rule "nothing simulated is ever evidence" makes
  this the provenance lane's first test.

Not claimed: anything about the real host's dynamics, the open web, or the answer model; and no claim that any of
these numbers measures "intelligence". They are task scores on synthetic worlds with known answers.

## Registered mechanisms

- **`imagination` (lane M, ADR-052; counterfactual, retention, adversarial).** Components `scm`, `what_if`,
  `forecast`, `action`. *As first recorded:* "coverage 1.0 on both splits (baseline 0.107 / 0.071)" — withdrawn as a
  headline after the Wave 1 review (B3): coverage counted any answer. *Now* (development / held-out, split seeds;
  `genesis_wave1.json`, `genesis_wave1_wide.json`): over all answerable questions, the 4 front-door ones per seed
  included and declined (pooled, not a claim), answered within tolerance 0.844 / 0.875 (baseline 0.073 / 0.063;
  12 further seeds on held-out 0.862 against 0.057); answerable questions other than the 4 front-door ones per seed
  0.964 / 1.000 (28 of 28 per seed on held-out; baseline 0.083 / 0.071; 12 seeds 0.985 against 0.065). By group:
  handed equations 1.0 / 1.0, belief what-ifs 1.0 / 1.0 (plumbing), learned graph forecasts and actions 0.889 /
  1.000 within 0.1 × sd (largest error 0.147 / 0.092; within 0.05: 0.815 / 0.778; the tolerance was set in the same
  commit as the results; baseline 0.259 / 0.222; "answer 0" 0.111 / 0.407 at 0.1 and 0.037 / 0.148 at 0.05; 12 seeds
  0.954 against 0.204). The engine does no front-door adjustment: it declines those questions. Ablations, held-out,
  over all answerable (without the front-door ones in brackets): without the causal engine 0.375 (0.429), without the
  what-if 0.781 (0.893), without forecasts 0.656 (0.750), without pushes 0.781 (0.893). The splits are unchanged.

## Wave 1 integration (lanes I, M, K, C, W on one tree)

`scripts/bench_wave1.py run` measures the integrated tree on both splits (the withheld family is not run):
`all_off` (every Wave 1 setting read as off: the baseline policy and the references, diffed against
`genesis_baseline.json`), `defaults` (each family answered by the mechanism the shipped settings use), `all_on`
(every Wave 1 setting that ships off switched on as well; strategy selection stays behind its gate and the
reflection's model summary needs the model, so both stay off) and one run per registered component switched off.
Results: `docs/quality/evidence/genesis_wave1.json`. With every Wave 1 setting off, every family's result equals the
recorded baseline on both splits (0 differences; the four references too) except the adversarial family, whose case
list changed:

| Change to a benchmark definition | By | Effect on the recorded baseline |
|---|---|---|
| `selfmodel.truth`: `uncertain_about` is `"nothing"` when every factor is settled (was `None`, which no answer could match) | W | none: the baseline answers `None` (0.80 / 0.80 reproduced) |
| adversarial `simulated_reading_moves_no_belief` relabelled `imagined_reading_moves_no_belief` (a what-if never reaches the live belief); ADR-052 §6 | M | the case that failed now passes: 12 → 13 of 13 on the old list |
| four adversarial cases added (`no_self_confirmation_without_evidence`, `reflection_summary_is_never_evidence`, `recheck_and_reflection_stay_bounded`, `self_reported_success_earns_no_reward`); the "not staged: Excessive internal reflection" row is gone (the third case stages it) | K | 4 new invariant cases pass in every regime (they pass with every Wave 1 setting off); reported as their own line, never as 12 → 17 |
| counterfactual scoring (Wave 1 review B3): `answered_within_tolerance`, `interval_score`, three groups, the confounded questions relabelled answerable by the front door (their queries now also carry the observational means); `coverage` → `answered_share`, `abstention_correct` → `declined_confounded_share` | fix C | old metrics reproduced under their new names (0 differences); the re-scored baseline is a separate file |
| counterfactual headline corrected at the re-review of the fix round: `answered_within_tolerance` counts every answerable question, the front-door ones included (a decline is not within tolerance); the old figure is `answered_within_tolerance_without_front_door`; `answered_within_strict_tolerance` (`STRICT_TOLERANCE` 0.05) and `max_relative_error` always beside it; `summary_is` marks the pooled summary "reported, not a claim" | fix E | old metrics still reproduced (0 differences); `genesis_baseline_rescored.json` regenerated: within tolerance 0.083 → 0.073 (development), 0.071 → 0.063 (held-out) |
| self-description scored under both answer keys; two probes marked as plumbing checks (B4) | fix C | none (fields added) |
| selective: decision scoring, reference `verdict_word`; no 10-bin ECE in any headline (B5) | fix C | none (fields added) |
| adversarial: `system_made_content_never_enters_the_journal` restored; results by origin | fix C | the case list grows by one (it passes in every regime) |

### Wave 1's look at the withheld family

`scripts/bench_wave1.py final --note TEXT --out FILE` (FILE outside the tree) ran the family twice on the clean final
tree `816fa12`, each run recorded in the ledger before it scored: the baseline with every Wave 1 setting off (the
recorded baseline's withheld numbers predate the corrected scoring; every metric that existed then has the recorded
value again), then one mechanism per interface under the name `wave1` (anticipation `instinct`, info_value `instinct`,
counterfactual `imagination`, self_model `self_description`). Evidence: `docs/quality/evidence/genesis_wave1_withheld.json`.
Baseline → Wave 1, three seeds: memory breaches avoided 0 → 0.83 (false alarms 0 → 0.013, background work given up
0.049 → 0.130; simulated plant; the mechanism ships off); core information value unchanged (0.043 bits per unit of
budget, the same choices); web noise share unchanged at 0.857 (realised bits −0.080 → 0.056, inside the baseline's
spread of 0.235); counterfactual answered within tolerance 0.12 → 0.827 with the 4 front-door questions per seed
declined (learned graph 0.30 → 0.967, 0.80 within 0.05, largest error 0.135; handed equations and belief what-ifs
0 → 1.000); self-description 12 of 15 → 15 of 15 under either key (12 of 13 → 13 of 13 without the plumbing probes).
The "answer 0" reference was not run on the withheld worlds. The full table and what it supports: WAVE1.md §8.

### What the Wave 1 evidence supports (after the review, fix lane C)

Each claim with the wording now used. Split = the frozen split's 3 seeds (official); wide = 12 further seeds
5001-5012 on the same worlds (supplementary, `docs/quality/evidence/genesis_wave1_wide.json`). Held-out unless
said otherwise.

| Claim as first written | Review | Wording now used |
|---|---|---|
| web information gain 0.093 → 0.163 | B1 overstated | superseded by the learning-progress rework (ADR-051 amendment; `genesis_fix1b.json`, re-run on the reconciled tree with identical numbers). Timer path, off → on (reworked): split 0.093 → 0.134; wide **0.139 → 0.126** (paired −0.013, per-seed sd 0.14; 5 seeds better, 1 worse, 6 equal), so learning progress ships **off**. Everything on (the re-check's pick first): split 0.093 → 0.124, wide 0.139 → 0.196 (12 of 12 better), the re-check's row (ADR-055) |
| noise share 0.537 / 0.800 → 0.375 "both paths" | B2 mixes two paths | paths apart, off → on (reworked): timer path noise share 0.537 → 0.512 on the split (wide 0.412 → 0.394); idle path 0.800 → 0.650 (wide 0.706 → 0.550), idle bits per budget −0.016 → 0.044 on the split (wide 0.094 → 0.144, 11 of 12 better) |
| counterfactual coverage 0.071 → 1.0 | B3 not supported | answerable questions other than the 4 front-door ones per seed: 0.071 → 1.000 (28 of 28 per seed; wide 0.065 → 0.985); over all answerable 0.063 → 0.875 (84 of 96, the front-door ones declined; wide 0.057 → 0.862; pooled, not a claim); handed equations 0 → 1.0 (exact arithmetic), belief what-ifs 0 → 1.0 (plumbing), learned graph forecasts and actions 0.222 → 1.000 within 0.1 × sd (largest error 0.092; 0.778 within 0.05; the tolerance was set in the same commit as the results; "answer 0" 0.407 at 0.1, 0.148 at 0.05; wide 0.204 → 0.954; the substantive part) |
| self-description 0.80 → 1.00 | B4 sound with caveats | 29 of 30 under the original answer key, 30 of 30 under lane W's key (baseline 24 of 30); `authorised_model_tools` and `last_decision` are plumbing checks; the held-out states were printed once during development |
| claim Brier 0.122 → 0.042, ECE 0.341 → 0.199 | B5 not a calibration gain | on 10 held-out claims, adding the verification state to the posterior lowers Brier 0.122 → 0.022 with the unfitted score now shipped (0.042 with the fitted map, no longer applied; the verdict-word rule 0.0025); no held-out evidence of a gain from selective answering: turned on it would withhold 12 of 20 right held-out answers (development: 3 of 3 wrong and 12 of 18 right); on development the record shows no answer confidence, so it ranks nothing (AURC of a constant 0.143 against the baseline's 0.080); `-independence`, `-guard`, `-critique` untested (0 on every item) |
| predictive regulation avoids 0.80 of memory breaches | B6 sound within the plant | within the simulated plant with declared constants: 0.80 (wide 0.80 ± 0.07 against 0.06 ± 0.11); the forecaster is fed directly (the wiring is covered by `tests/test_predictive_wiring.py`, not by the benchmark); on held-out the gain is mainly the per-task memory reserve (without the trend 0.80; on development 0.56 → 0.11 without it): on held-out the trend adds lead time (5.7 → 9.9 ticks) but avoids no further breaches and accounts for all the added false alarms (without it 0.009); background work given up 0.044 → 0.106, false alarms 0.009 → 0.043; not measured on the host |
| retention holds | Other | holds for the shipped defaults; the first build of information-v1 failed `asks_only_while_partner_ambiguous`; amended after the review (it asks a partner only while its meaning is ambiguous), it passes with everything on (reconciled tree, both splits) |
| adversarial 12 → 17 of 17 | Other: not an improvement | 13 of the original 13 under the redefinition in ADR-052 §6; 4 new invariant cases pass in every regime (they pass with every Wave 1 setting off); 1 restored case passes in every regime |
| frozen worlds for re-checking and strategy selection, `knowledge_splits.py` (own digest, pinned in its test) | K | a new file; `splits.py` and `withheld.py` and their pinned digests are unchanged |

## Wave 2: how the knowledge worlds draw, after the benchmark-integrity review

Lane K's knowledge worlds (`knowledge.py`, Wave 1) and lane L's integration worlds (`integrated.py`, Wave 2), which
reuse lane K's staging, drew the world's truth changes, the arrivals and every research outcome from **one** random
stream (strategy selection: the offers and the outcomes). Two configurations that investigate different subjects can
then meet different later worlds, so their per-seed differences are not paired on the same world. Wave 2 lane Y
added a **decoupled draw** (`knowledge._World(decoupled=True)`): truth changes keyed by (world, seed, step), each
research outcome by (world, seed, step, subject); strategy selection's offers by (world, seed, step) and its
outcomes by (world, seed, step, candidate). The worlds files are unchanged; only how the harness draws from them.

| Family | Default draw | Evidence |
|---|---|---|
| lane K, retention under change and strategy selection (`scripts/bench_genesis_knowledge.py`) | the recorded one-stream draw, so the recorded baseline reproduces (0 of 635 held-out and 633 development values differ on the integrated tree); `--decoupled` is the draw documented for paired comparisons | recorded: `genesis_knowledge.json` (unchanged); decoupled: `genesis_knowledge_decoupled.json` |
| lane L, the integration loop (`scripts/bench_wave2_integration.py`) | decoupled (`decoupled=False` reproduces the first run) | `genesis_integration.json`; the first figures under `before_review` |

What moved (held-out, decoupled against recorded): lane K's retention under change, due order 0.693 → 0.673 and the
re-check 0.695 → 0.682 (paired re-check − due order +0.003 and +0.014 per world, sd 0.021 and 0.036, against a
baseline sd of 0.054 and 0.075: still within noise); strategy selection, Thompson against the incumbent 0.395 /
0.265 → 0.348 / 0.268 verified items per investigation (the gain shrinks from +0.130 to +0.080, still positive),
share on the self-reporting arm 0.105 / 0.253 → 0.088 / 0.233. Lane L: baseline 0.713 → 0.718, integrated 0.710 →
0.726, paired −0.003 (4 higher, 4 lower) → +0.008 (sd 0.015; 6 higher, 2 lower), the re-check's, below the
baseline's per-world sd of 0.047 and 0.071. No verdict and no default changed. Lane F's learning-cycles worlds
(`cycles_splits.py`) are written whole before a run (`generate`), so they never had this coupling.
