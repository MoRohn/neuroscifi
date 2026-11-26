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
$PY scripts/bench_cognition.py baseline --out /outside/genesis_baseline.json   # what was recorded (a withheld look:
                                                  # since the third fix lane --out must be outside the checkout)
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
  counted (`front_door_declined`; until Wave 3 the engine did no front-door adjustment; since ADR-059 it answers them,
  0 → 1.000 held-out, `genesis_frontdoor.json`) and an answer is scored for tolerance.
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
journal and the state version unchanged; it guards lane P's Wave 0 mechanism, so it passes in every regime),
`added_wave2` (10), and in Wave 3 `added_wave3_c` (5, lane C's front-door traps) and `added_wave3_s` (16, lane S's
sources, own text and supersession). On the merged Wave 3 tree `f0a1f09`: 54 cases, 49 scored (5 directive items not
staged), 49 of 49 pass on both splits (13 / 4 / 1 / 10 / 5 / 16 by origin; `genesis_wave3_combined_checks.json`).
Lane C's 5 cases were declined by the moments rule before the criterion was reached, so they passed even with the
criterion broken; since the fix lane (`8a8fce7`) they are staged with `moments: population`, a decline for unstated
moments does not count, and a test shows them failing with the separation check removed (the expectation text is
unchanged). Lane S's 16 pass with its two source rules switched on by the cases themselves (`LANE_S_ON`), as their
expectations were written; at the shipped defaults those rules are off since the fix lane (ADR-061). The second fix
lane added `added_wave3_fix2` (3: a page naming two outlets as its origins, a copy naming one origin, a page carrying
two wire credits) and the third fix lane `added_wave3_fix3` (3: copies naming several origins count once); all six
switch `origin_grouping` "one_way" on themselves (ADR-063, off by default). At the shipped default the defect the
first three guard against (review3b N1) is open; the family lists it as a "not staged" line marked KNOWN OPEN
(`known_open_wave3`), never as a pass. On `860db86`: 61 lines, 55 scored cases, 55 of 55 pass on both splits
(13 / 4 / 1 / 10 / 5 / 16 / 3 / 3 by origin), 6 "not staged" lines.
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
  0.954 against 0.204). The engine did no front-door adjustment then: it declined those questions (answered since
  Wave 3, ADR-059). Ablations, held-out,
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

## Wave 3: withheld worlds for the learning cycles

**Purpose.** The learning-cycles experiment (`scripts/bench_learning_cycles.py`; harness
`src/neuroscifi/acceptance/cognition/cycles.py`) had a development and a held-out split, both from one kind of world,
and no withheld world, so a change to the knowledge loop could never be judged on transfer (FINAL_EVALUATION §6,
direction 5). `src/neuroscifi/acceptance/cognition/cycles_withheld.py` holds three such worlds, of kinds that neither
split has, with four seeds each (7341-7344, disjoint from every split and benchmark seed). They were written and
committed alone (`26409bc`), before their guard and before any Wave 3 mechanism. They contain what a supersession rule
and a change-rate-aware refresh order need in order to be judged fairly, without being shaped around either. Their
contents are not described here: no lane may read, import, generate or score them (the file's docstring says what is
forbidden), and nothing may be tuned on them.

**The guard.** Only `cycles.final()` loads the worlds; `scripts/bench_learning_cycles.py final --note TEXT --out FILE`
is its command line. `run --split` does not accept them, and `cycles.run_split` / `run_references` refuse them. A final
run refuses a missing note and a tree that is dirty or not committed (the ledger of final runs is excluded from the
dirty check, as `cognition.environment()` does); since the fix lane a dirty check that fails or times out counts as
dirty, and `final()` compares the worlds file's bytes with the SHA-256 pinned in `cycles.WITHHELD_SHA256` before it
records or loads anything (the digest goes into the ledger line). It appends its line to
`docs/quality/evidence/genesis_final_uses.jsonl` (commit, clean flag, mechanism, `"ablate": []`, the family
`learning_cycles_withheld`, the configurations, the note) **before** it loads or scores anything, so a crash still
counts as a look. The script also refuses another tree's source, an `--out` inside the checkout and (since the second
fix lane) an `--out` whose directory is missing or not writable. The file's SHA-256
is pinned in `cycles.py` (checked by `final()`) and by `tests/acceptance/test_learning_cycles_withheld.py`; the tests
check each refusal without loading the worlds. A `--final` run of `scripts/bench_cognition.py` (the counterfactual
withheld interface) likewise refuses a dirty or uncommitted tree and an `--out` inside the checkout (fix lane); since
the second fix lane it also refuses, before its ledger line, an `--out` it cannot write, an empty note, a mechanism
that no selected family has (every family would fall back to the baseline), an unknown ablation, and a `withheld.py`
whose bytes do not hash to the SHA-256 pinned in `cognition.WITHHELD_SHA256`. Since the third fix lane
`bench_cognition.py baseline`, which also takes a withheld look, refuses the same `--out` paths before it runs any
family (review3c F4), and `cycles.final` refuses a note of only spaces as `cognition.run` does (F5).
`baseline` is Step 0's behaviour made explicit since the fix lane (the Wave 1 web settings of `WAVE1_OFF_WEB` off,
lane S's two rules off, supersession "ask", lane K's re-check order, no idle investigation; every other setting at
its default, as at Step 0, among them `calibrated_forecasts`, `tick_admission` "unified" and `arbitration_record`,
which are on); the Wave 3 look's configurations, their reference
pairings and report fields are `cycles.LOOKS["wave3"]`, `LOOK_PAIRS`, `LOOK_FIELDS` and `COUNTERS` (what each
admitted-content counter can see: `self_admitted` and `wire_admitted` see only 9.x and 7.x versions;
`admitted.wrong`, `admitted.own_only` and `admitted.wire_only` need no version convention).
Each result records every world's digest per seed (`world_digests`). Two looks met the same worlds when their digests
are equal. The settings each individual held are read back in every run (`config_read_back`).

**Who looks.** Wave 3 step 0 took the unchanged system's one look (below). After that, only the main session looks,
once, at the end of Wave 3, with the mechanisms under test beside `baseline`. The evidence file of a look names the
worlds and records their per-cycle allowance and series, so it is not development material either.

### The unchanged system's one look (Wave 3 step 0)

Run once, on commit `d0b6b02` (clean), ledger line 4 of `genesis_final_uses.jsonl`, written before scoring; evidence
`docs/quality/evidence/genesis_cycles_withheld_baseline.json` (written outside the tree, then copied in; 25 s). There
are 4 seeds; mean ± sd of `correct` (the primary: share of subjects whose newest held item states the current
version, mean over all cycles). Worlds are numbered in file order. Nothing was tuned before or after.

| | W1 | W2 | W3 |
|---|---|---|---|
| no loop (autonomous research off) | 0.423 ± 0.051 | 0.311 ± 0.061 | 0.311 ± 0.065 |
| **baseline** (today's loop, every Wave 1 setting off) | **0.465 ± 0.064** | **0.286 ± 0.039** | **0.323 ± 0.035** |
| shipped defaults | identical to baseline on every metric and seed | identical | identical |
| baseline − no loop, paired (seeds better) | +0.042 (4 of 4) | −0.025 (1 of 4) | +0.012 (3 of 4) |
| *reference:* perfect snapshot, never refreshed | 0.455 | 0.385 | 0.395 |
| *reference:* blind round-robin, same allowance, majority read | 0.636 | 0.541 | 0.537 |
| *reference:* blind round-robin, perfect reads | 0.693 | 0.656 | 0.649 |
| *reference:* oracle, same allowance | 0.797 | 0.809 | 0.820 |
| coverage by the owner's research | 0.883 | 0.872 | 0.815 |
| Brier, baseline (constant reference) | 0.250 (0.248) | 0.323 (0.218) | 0.282 (0.239) |
| baseline, over the 4 seeds: own-output versions admitted / wrong-story versions admitted / one-origin rises | 4 / 2 / 0 | 6 / 17 / 0 | 6 / 12 / 1 |
| baseline, retention: lost / regressed (of the subject-runs in the base) | 0 / 1 (16) | 0 / 3 (9) | 0 / 3 (15) |

Reading. On the held-out worlds the loop beat no loop on every seed; here it does so on every seed in one world of
three. In the other two the paired difference is within the baseline's spread or negative. In every world the loop
sits below the blind round-robin of the same allowance (by 0.17 to 0.26) and near or below the never-refreshed
snapshot (coverage explains part of that, as on held-out). The adversarial counts are not zero here, while they
were 0 in all 120 held-out runs: versions asserted only by the individual's own outputs and versions stated only by
a wrong story were admitted as knowledge, one confidence rose after a look with one origin, and subjects held
correctly at cycle 3 regressed. The shipped-on Wave 1 and Wave 2 records (`hypothesis_fields`, `premise_recheck`,
`salience_record`, `arbitration_record`, `answer_monitoring`; read back in every run) changed no launch and no held
item (0 changed cycles on every seed). The smallest gain the pre-registered rule can see with these seeds is the
baseline's sd: 0.064, 0.039 and 0.035.

### The looks at the end of Wave 3

**The withheld looks, taken once at the end of Wave 3** (clean tree `0bdc41a`; the rule for them committed first;
ledger lines 5 to 7; WAVE3.md, "The withheld looks"; evidence `genesis_wave3_look_*.json`). On three unseen worlds,
four seeds each, as shipped the share of subjects held correctly is 0.465 / 0.286 / 0.323, exactly as at step 0
(never refreshed 0.455 / 0.385 / 0.395; a blind round-robin with the same allowance 0.636 / 0.541 / 0.537), and the
hole is open in the same counts: 16 versions only the individual itself had asserted and 31 only the wrong story
stated became knowledge. **`echo_recognition` is supported on those worlds** by the rule written beforehand (the 16
own-output versions → 0; wrongly admitted content 48 → 38, lower in every world; correctness +0.017 / +0.013 /
+0.007; no more subjects lost), though wrong-story versions rose 31 → 37; it stays off and is the owner's to turn
on. **`attribution_independence` was not exercised** (every figure identical). **Supersession** gains +0.011 /
+0.042 / +0.007 (every seed) and made 3 false replacements of 54 by the rule; all 5 false replacements in its runs
were still wrong when the runs ended (up to 17 cycles); it stays off. **The re-check's order gives no gain.** **The
idle investigator run before the timer's pass** gives +0.013 / +0.042 / +0.052 (beyond the spread in two worlds)
with fewer subjects lost; the look cannot promote it, and dividing the daily allowance between refreshing and
reaching is the next thing to measure. **The front door stays on** (12 of 12 front-door questions exact; every
other figure equal to the run without it).

## Wave 4: the source-independence family

**Purpose.** At the shipped defaults the individual cannot tell independent sources from copies beyond near-identical
text and single named origins (WAVE3.md). The learning-cycles worlds measure that only through version-number
counters and contain few of the shapes in which it fails. This family measures it directly: worlds whose ground truth
is a DERIVATION GRAPH (for every page, whether it reports first-hand, derives from which other page(s), or repeats the
individual's own earlier answer), written before any source rule, on which every later source rule is judged
(`scripts/bench_sources.py`; harness `src/neuroscifi/acceptance/cognition/sources.py`).

**The worlds.** `sources_splits_dev.py` (development, seeds 8301-8303, two worlds) and `sources_splits_heldout.py`
(held-out, seeds 8401-8404, three worlds), each committed alone (`cda7271`, `6480b12`) before the withheld worlds,
the harness and any rule; seeds disjoint from every seed in the repository outside the withheld files. Fictional
projects whose latest release changes (and is sometimes pulled), outlets, wires, aggregators, round-up sites, forums
and the projects' own release pages, all under `.example`. Each look the world schedules (births, then a blind
rotation of revisits: the same looks in the same order in every configuration) stages one of thirteen shapes:
honest independent corroboration (the commonest, so a rule is judged on not breaking it), outlets restating the
project's release page, honest outlets that checked the value and still credit another outlet, reworded syndicated
copies crediting their origin, copies crediting nobody, a page naming two origins, copies naming their origin and an
irrelevant contributor, hosts named like wire services (independent outlets and copiers), round-ups citing several
outlets, mirrors with later dates (some with a canonical link), corrections and corrections of corrections, stale
pages (dated when the old value was true, or re-dated), and echoes of the individual's own earlier answer (verbatim,
paraphrased, number reformatted, crediting a model or not; its value right or wrong). Wrong stories are neighbouring
values or far ones; no value carries a mark of where it came from. The credit and echo wording comes from documented
sources, listed in the development file's docstring (Wikipedia's MOS:SAID verbs, the AP and Reuters datelines,
ProPublica's and The Conversation's republication lines, the AP and Reuters credit lines, the Curator's Code, the NYT
and Reuters correction forms, the APA model citation), not from any parser's pattern list, and no page was run
through the product before the worlds were committed. The proportions are stated in the files with their reasons;
the real web's shares are not measured anywhere in this project. What the worlds cannot show: the open web, real
syndication rates, a real answer model.

**The withheld worlds** (`sources_withheld.py`, seeds 8501-8504, committed alone `5ce7868`): worlds no rule is written
or tuned on, with different proportions and with shapes and surface forms the development and held-out worlds do not
have. Their contents are not described here; no lane reads, prints, imports or runs them.

**Metrics** (fixed before any rule; per world, seed and shape; `sources.py` docstring): `wrongly_admitted` (knowledge
rows written whose value's independent first-hand support in the look is below the bar: fewer than 2 roots and not
the project's own release page), `own_admitted` (rows whose value only echoes of the individual's own answer state),
`suppressed` of `eligible` (looks whose true value meets the bar after which the subject's newest held item does not
state it; `suppressed_contested`: the look opened a contradiction question), `dependent_rises` (a held item's
confidence rose in a look that stated its value with no root not already behind it), grouping precision and recall
of "same source" over pairs of read pages that yielded a claim (the pipeline's `claims.independence_groups` over each
cluster against "their roots intersect"), `correct` over time (share of subjects whose newest held item states the
value true now; `mean_correct` over cycles 1..N), and cost (wall seconds, seconds inside `consolidate.run`, pages,
rows, file bytes). Values are compared as numbers ("2.4" = "v2.4.0"); no metric reads a version-number convention.
Each configuration reads its settings back (`config_read_back`, `config_same_every_seed`) and records what it did
(`looks_changed_vs_shipped`: looks whose grouping, admitted values or questions differ from `shipped`'s).

**References** (same looks, from the graph alone): `oracle` (the true roots), `every_page`, `same_value` (every page
stating one value is one source) and `todays_rule` (the product's grouping at the defaults, and no other product
code); each admits the value whose count reaches the bar and is above every other, and is scored by the same metrics.
They bound what a rule can gain in each direction; they model no other rule of the product, so their `correct` is not
the product's ceiling.

**The guard.** Only `sources.final()` loads the withheld worlds; `scripts/bench_sources.py final --note TEXT --out
FILE [--config NAME]` is its command. Refused before the ledger line: another tree's source, an `--out` inside the
checkout, in a missing or unwritable directory or that is a directory, a blank note, an unknown configuration, a tree
that is dirty, uncommitted or whose state git cannot give, and a withheld file or development file (whose generator
the withheld worlds use) whose bytes do not hash to the SHA-256 pinned in `sources.WITHHELD_SHA256` /
`sources.DEV_SHA256` (also pinned by `tests/acceptance/test_sources_bench.py`, which tests each refusal without
loading the worlds). The line goes to `docs/quality/evidence/genesis_final_uses.jsonl` (family `sources_withheld`)
before anything is loaded or scored. `run --split` accepts only `development` and `held_out`.

**The look rule (written before the held-out runs and the look).** Wave 4 step 0 takes one look, on its final clean
tree: `final` with the default configuration (`shipped`) and the four references, nothing else (the opt-in rules get
their look with Wave 4's mechanisms at the end). It decides nothing: no setting changes with it. Reported from the
output, per world and summed over the four seeds: `totals_sum` and `by_shape_sum` (`wrongly_admitted`,
`wrongly_admitted_false`, `own_admitted`, `eligible`, `suppressed`, `suppressed_contested`, `dependent_rises`,
`precision`, `recall`), `mean_correct` (mean and sd over seeds), `config_read_back` and `config_same_every_seed` (a
read-back other than the shipped defaults with `enabled` true voids the look), `world_digests`, and the same fields
for each reference. The evidence file is a summary per world, seed and shape. It names shapes the other splits do
not have, so only the main session reads it.

### The unchanged system and the three opt-in rules, development and held-out (Wave 4 step 0)

Measured on `cd99288` (clean), `scripts/bench_sources.py run --split development` and `--split held_out`; evidence
`docs/quality/evidence/genesis_sources_baseline.json` (463 kB: both splits per world, seed and shape, the
references, and the form census below). About 0.4 s per run (0.1 s of it inside `consolidate.run`); every
configuration read its settings back as named in every world (`config_same_every_seed` true).

Held-out, summed over 3 worlds × 4 seeds (12 cycles, 72 looks per run). Each cell: wrongly admitted / own answers
admitted / suppressed of eligible / grouping precision, recall ("–": no pair of that kind). The references' cells
omit precision and recall (the oracle's are 1, 1 by definition).

| shape (looks) | shipped | `attribution_independence` | `echo_recognition` | `origin_grouping` one_way | oracle | todays_rule | every_page |
|---|---|---|---|---|---|---|---|
| independent (183) | 0 / 0 / 16 of 183 / 0.00, – | 0 / 0 / 16 of 183 / 0.00, – | 0 / 0 / 17 of 183 / 0.00, – | 0 / 0 / 16 of 183 / 0.00, – | 0 / 0 / 0 of 183 | 0 / 0 / 12 of 183 | 0 / 0 / 0 of 183 |
| syndicated_credit (109) | 61 / 0 / 23 of 30 / 0.92, 0.06 | 56 / 0 / 23 of 30 / 0.98, 0.24 | 61 / 0 / 23 of 30 / 0.92, 0.06 | 61 / 0 / 23 of 30 / 0.92, 0.06 | 0 / 0 / 0 of 30 | 101 / 0 / 28 of 30 | 104 / 0 / 29 of 30 |
| primary_cited (103) | 0 / 0 / 4 of 103 / –, 0.00 | 0 / 0 / 3 of 103 / –, 0.00 | 0 / 0 / 5 of 103 / –, 0.00 | 0 / 0 / 4 of 103 / –, 0.00 | 0 / 0 / 0 of 103 | 0 / 0 / 0 of 103 | 0 / 0 / 0 of 103 |
| syndicated_nocredit (72) | 36 / 0 / 5 of 13 / 0.96, 0.17 | 36 / 0 / 5 of 13 / 0.96, 0.17 | 36 / 0 / 5 of 13 / 0.96, 0.17 | 36 / 0 / 5 of 13 / 0.96, 0.17 | 0 / 0 / 0 of 13 | 66 / 0 / 13 of 13 | 69 / 0 / 12 of 13 |
| wire_like_host (61) | 0 / 0 / 33 of 61 / 1.00, 0.88 | 0 / 0 / 33 of 61 / 1.00, 0.88 | 0 / 0 / 33 of 61 / 1.00, 0.88 | 0 / 0 / 33 of 61 / 1.00, 0.88 | 0 / 0 / 0 of 61 | 0 / 0 / 3 of 61 | 0 / 0 / 0 of 61 |
| extra_origin (58) | 26 / 0 / 6 of 12 / 0.92, 0.12 | 22 / 0 / 6 of 12 / 0.96, 0.25 | 25 / 0 / 6 of 12 / 0.92, 0.12 | 26 / 0 / 6 of 12 / 0.92, 0.12 | 0 / 0 / 0 of 12 | 52 / 0 / 10 of 12 | 54 / 0 / 10 of 12 |
| mirror_dates (58) | 0 / 0 / 12 of 18 / 0.98, 1.00 | 0 / 0 / 12 of 18 / 0.98, 1.00 | 0 / 0 / 12 of 18 / 0.98, 1.00 | 0 / 0 / 12 of 18 / 0.98, 1.00 | 0 / 0 / 0 of 18 | 0 / 0 / 2 of 18 | 46 / 0 / 14 of 18 |
| verified_credit (55) | 0 / 0 / 4 of 55 / 0.00, – | 0 / 0 / 13 of 55 / 0.00, – | 0 / 0 / 5 of 55 / 0.00, – | 0 / 0 / 4 of 55 / 0.00, – | 0 / 0 / 0 of 55 | 0 / 0 / 1 of 55 | 0 / 0 / 0 of 55 |
| roundup (53) | 0 / 0 / 5 of 53 / 0.00, 0.00 | 0 / 0 / 8 of 53 / 0.62, 0.10 | 0 / 0 / 5 of 53 / 0.00, 0.00 | 0 / 0 / 5 of 53 / 0.00, 0.00 | 0 / 0 / 0 of 53 | 0 / 0 / 0 of 53 | 0 / 0 / 0 of 53 |
| two_origins (43) | 0 / 0 / 7 of 43 / 0.81, 0.41 | 0 / 0 / 7 of 43 / 0.81, 0.41 | 0 / 0 / 7 of 43 / 0.81, 0.41 | 0 / 0 / 7 of 43 / 0.81, 0.41 | 0 / 0 / 0 of 43 | 0 / 0 / 6 of 43 | 0 / 0 / 0 of 43 |
| correction (32) | 0 / 0 / 20 of 32 / 0.00, – | 0 / 0 / 20 of 32 / 0.00, – | 0 / 0 / 20 of 32 / 0.00, – | 0 / 0 / 20 of 32 / 0.00, – | 0 / 0 / 0 of 32 | 0 / 0 / 0 of 32 | 0 / 0 / 0 of 32 |
| echo (21) | 4 / 4 / 1 of 3 / 0.75, 0.12 | 4 / 4 / 1 of 3 / 0.75, 0.12 | 1 / 1 / 1 of 3 / 0.75, 0.12 | 4 / 4 / 1 of 3 / 0.75, 0.12 | 0 / 0 / 0 of 3 | 13 / 13 / 2 of 3 | 14 / 14 / 1 of 3 |
| stale (16) | 0 / 0 / 6 of 6 / 0.00, – | 0 / 0 / 6 of 6 / 0.00, – | 0 / 0 / 6 of 6 / 0.00, – | 0 / 0 / 6 of 6 / 0.00, – | 0 / 0 / 4 of 6 | 0 / 0 / 5 of 6 | 0 / 0 / 4 of 6 |
| **all** | **127 / 4 / 142 of 612 / 0.81, 0.20** | 118 / 4 / 153 of 612 / 0.81, 0.27 | 123 / 1 / 145 of 612 / 0.81, 0.20 | 127 / 4 / 142 of 612 / 0.81, 0.20 | 0 / 0 / 4 of 612 | 232 / 13 / 82 of 612 | 287 / 14 / 70 of 612 |

`correct` (mean over cycles 1..12, mean ± sd over 4 seeds; for a rule: the paired difference with `shipped`, seeds
better / worse, and the looks it changed against `shipped`):

| world | shipped | `attribution_independence` | `echo_recognition` | one_way | oracle | todays_rule | every_page | same_value |
|---|---|---|---|---|---|---|---|---|
| src-ho-mixed | 0.601 ± 0.064 | −0.008 (0 / 3); 26 looks | −0.004 (0 / 3); 4 | 0 (0 / 0); 0 | 0.713 | 0.683 | 0.714 | 0.093 |
| src-ho-copies | 0.627 ± 0.064 | −0.009 (0 / 2); 35 | +0.002 (1 / 1); 7 | 0 (0 / 0); 0 | 0.718 | 0.615 | 0.618 | 0.118 |
| src-ho-origins | 0.656 ± 0.073 | −0.027 (0 / 3); 30 | 0 (0 / 0); 1 | 0 (0 / 0); 0 | 0.709 | 0.720 | 0.731 | 0.113 |

Development (2 worlds × 3 seeds, all shapes summed): shipped 64 / 4 / 39 of 233 / 0.87, 0.20;
`attribution_independence` 56 / 3 / 49 of 233 / 0.86, 0.27; `echo_recognition` 58 / 0 / 39 of 233 / 0.87, 0.20;
one_way identical to shipped (0 looks changed); oracle 0 / 0 / 2 of 233; todays_rule 114 / 9 / 25 of 233; every_page
137 / 11 / 22 of 233. `correct` src-dev-mixed 0.704 ± 0.064, src-dev-copies 0.579 ± 0.050; attribution −0.023 and
−0.058 (every changed seed worse), echo 0 and −0.011. Wrongly admitted values that are also false: shipped 21 (dev) and
40 (held-out); `dependent_rises` 1 on each split in every configuration (the metric is barely exercised: held items'
confidence rarely rises in these worlds). Full per-shape tables are in the evidence file.

Reading (held-out; pooled over worlds, not a claim):
- **Where the shipped system fails.** Every wrongly admitted row comes from four shapes: syndicated copies crediting
  their origin (61), copies crediting nobody (36), copies naming an irrelevant contributor (26) and echoes (4). The
  shipped grouping joins only 6 % of truly same-source page pairs among credited copies, 17 % among uncredited ones,
  12 % with an extra contributor, none among outlets restating the release page or among round-ups and the outlets
  they cite (it does join mirrors and wire datelines). It also joins honest outlets that wrote the same short sentence
  (precision 0.00 within `independent`: 53 false same-source pairs, 16 of 183 true values not held).
- **Suppression that is not grouping.** `wire_like_host` (33 of 61), `correction` (20 of 32), `stale` and
  `mirror_dates` lose true values with almost no false merge (precision 0.98-1.00): the cause is elsewhere (a
  correction note names the wrong value, so the look is contested; dateline forms; earlier held values). A source rule
  cannot remove these, and a reader of `suppressed` must not credit or blame one for them.
- **What each opt-in rule cannot see.** `attribution_independence` raises recall among credited copies only from 0.06
  to 0.24 (76 % of same-source pairs still unseen; wrongly admitted 61 → 56) and among copies with an extra
  contributor 0.12 → 0.25, sees nothing of uncredited copies, release-page citation or echoes, and merges honest
  outlets that credit another (suppressed in `verified_credit` 4 → 13, in `roundup` 5 → 8); `correct` falls in 8 of 12
  seeds. `echo_recognition` removes 3 of the 4 own answers admitted (dev 4 → 0) and nothing else; none of the
  model-crediting echo forms used here (APA "(OpenAI, 2023)", "according to ChatGPT", "ChatGPT said") matches its
  model-credit pattern (0 of 28 pages), so what it catches it catches by value and wording. `origin_grouping`
  "one_way" changed no look in any world or seed: not exercised by these worlds (the multi-origin pages here carry at
  most one origin the reader recognises; the "contributed to this report" lines are not read).
- **References.** The oracle admits no wrongly supported value and misses 4 of 612 eligible true values (ties with a
  stale value). `todays_rule` (today's grouping under the reference admission) admits 232 wrongly supported values:
  the product admits fewer (127) because its other rules (contradiction questions, recency, relevance) refuse many,
  not because of its grouping. `correct` of the shipped system is 0.06-0.11 below the oracle's in every held-out
  world.

**The form census** (development and held-out pages, computed from the rules' code after the worlds were committed;
`form_census` in the evidence file). Of the derived pages: the shipped origin reader (`claims.origin_keys`) gives a
key to the ProPublica line "This story was originally published by X." (29 of 29), to the AP and Reuters datelines
(97 of 135: not the fictional wire's), to canonical-linked mirrors and to the two-origin form that opens with that
line (on its first name only); to none of the MOS:SAID verb forms, "Read the full article on X", "X contributed to
this report", "HT: X", "via X", round-ups or reworded copies. The attribution reader (`claims.attribution_keys`) reads
"according to X", "X wrote / stated / confirmed that", "(via X)", "..., X said." and round-ups' "According to X and
Y", not "claimed", "commented", "noted", "observed", "explained", "pointed out", "remarked", "Read the full article
on", "contributed", "HT", "said" lists or datelines, and no credit to the project itself ("According to the X
release notes", "the X project said": 80 of 80 such pages of the development worlds give no key, so release-page citation is unseen); it reads the model credits "According to ChatGPT" and "ChatGPT
said" as an outlet named ChatGPT. Every page of every form yielded a claim through the real extractor.

### The unchanged system's one look (Wave 4 step 0)

Taken once, on the clean tree `d10927f`, after the look rule above (`cd99288`), by `scripts/bench_sources.py final`
with `shipped` and the four references; ledger line 8 of `genesis_final_uses.jsonl` (family `sources_withheld`),
written before anything was loaded; 4.7 s. Evidence `docs/quality/evidence/genesis_sources_withheld_baseline.json`
(234 kB; per world, seed and shape; for the main session only, as it names the withheld shapes). The read-back is the
shipped defaults with `enabled` true in both worlds and every seed (`config_same_every_seed` true): the look is not
void. Nothing was tuned before or after; the look decides nothing.

Summed over 4 seeds per world (72 looks per run); shipped / oracle / todays_rule / every_page / same_value:

| | wh-a | wh-b |
|---|---|---|
| wrongly admitted (of them false now) | 54 (12) / 0 / 90 (41) / 105 (51) / 0 | 56 (14) / 0 / 119 (66) / 140 (78) / 0 |
| own answers admitted | 0 / 0 / 1 / 4 / 0 | 2 / 0 / 5 / 6 / 0 |
| suppressed of eligible (shipped: of them contested) | 56 of 192 (25) / 5 / 30 / 19 / 175 | 73 of 164 (41) / 1 / 53 / 32 / 154 |
| grouping precision, recall (shipped) | 0.75, 0.19 | 0.84, 0.25 |
| `correct`: shipped mean ± sd / references | 0.535 ± 0.042 / 0.610 / 0.605 / 0.640 / 0.052 | 0.489 ± 0.062 / 0.657 / 0.519 / 0.600 / 0.052 |
| confidence raised by dependent pages (shipped) | 0 | 0 |

Reading: on worlds no rule was written for, the shipped system admits 110 knowledge rows whose independent first-hand
support was below the bar (26 of them false now), 2 of them own answers, and does not hold 129 of 356 true values that
had enough support (66 of those looks opened a contradiction question); it joins about a fifth of truly same-source
page pairs. Its `correct` is 0.08 and 0.17 below the oracle's. Wrongly admitted content is of the held-out order (127
over three worlds there); suppression is higher (29 % and 45 % of eligible looks, against 23 % on held-out).
