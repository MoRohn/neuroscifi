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

`compare BEFORE AFTER` prints the headline metrics of each family side by side with which direction is better. Given
the recorded baseline file as BEFORE, it uses the baseline's run of AFTER's split. The results are deterministic per
seed, so re-running the baseline gives the same numbers (only the cost differs).

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
latency limit and the 2 GiB floor) are declared in the bench, not measured on the Spark.

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
counterfactual questions with exact answers; "confounded" models (a hidden common cause, only the observational
covariance given) where the effect of `do(X)` is not identifiable and the right answer is to decline. A graph learned
and promoted through the product path on a `lab.graph_bench` world: "what follows k steps after this state", with
and without pushing one population (exact from the world's true matrix). Belief what-ifs: the posterior had one
reading been the other bit (exact, on an in-memory copy).
Scores: coverage of answerable queries, mean relative error, share within two stated sd, correct abstention on
unanswerable queries; also per query kind.
**Cannot show:** causal validity on real-world questions; these models are small and fully specified. Today's
`abstention_correct = 1.0` is vacuous: the system declines everything it cannot compute.

### 4. Self-description (`selfmodel.py`)
A staged individual and runtime state with ground truth: running and queued tasks, open owner questions, the autonomy
switch and searches left, memory pressure, paused writes (a full disk), an active voice session, the local model
unloaded by the idle policy, an unresolved factor, the executive's latest decision, the journal limit, the tools the
model may use, whether it is researching. The staging is verified against the database before any probe. 15 probes.
Scores: coverage, accuracy, accuracy when answered, number of separate surfaces the answers came from.
**Cannot show:** whether the answers are clear to a person; continuity over a long-running process.

### 5. Selective answering (`selective.py`)
The offline Web Cortex benchmark run once, every report kept; items are its verification questions (gold state) and
authoritative questions (gold answer). Risk-coverage curve, AURC, coverage at 5 % and 10 % risk, AUROC of the
confidence for correctness; for the claim probability on supported/disputed items, Brier with Murphy's decomposition
and ECE (10 equal-width bins and 5 equal-mass bins).
**Cannot show:** calibration on the open web; about 20 items per split, so differences between settings are rarely
outside noise, and on the held-out split today's answers are all correct (AUROC undefined).

### 6. Retention (`retention.py`) and the withheld family (`withheld.py`)
Retention: acceptance scenarios `birth`, `grounding` (40 lifetimes), `lifelong_correction`, `graph_development`, and
seven offline-benchmark metrics; a later run is "retained" when no metric falls below the recorded baseline.
The withheld family runs the regulator, chooser, answerer and describer on worlds of kinds in neither split
(accelerating leaks, voice sessions that follow backlog bursts, varying background demand, more noise than signal,
longer causal chains, other staged states). Scored only with `--final`; every final run is in the ledger.
**Cannot show:** retention of capabilities no test exercises; transfer to tasks unlike any of these.

### 7. Adversarial cases (`adversarial.py`)
Directive §12 items that can be staged offline today, each with an expectation written before it was run: model text
offered as knowledge, a reading labelled simulated, an unproductive curiosity loop, owner versus autonomous work,
critical memory pressure, a full disk, a spent budget, the owner's stop, acting on a suspected change, syndicated
misinformation, an autonomous request to adapt the model, a lost worker, a constant forecast gaming ECE. Items that
cannot be staged today are listed with the reason or the family that measures them.
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
| info_value, core | realised bits per investigation (higher) | 0.014 (per world sd 0.0) | 0.092 (0.24 / 0.08 per world) | oracle 0.035 / 0.119; random 0.037 / 0.083 |
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
| adversarial | cases passing | 12 of 13 | 12 of 13 | failing: `simulated_reading_moves_no_belief` |
| withheld (`--final`, once) | anticipation breach ticks; web noise share; counterfactual coverage; self coverage | 137.3 (6.7); 0.857; 0.143; 0.80 | | ledger: one entry |

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
