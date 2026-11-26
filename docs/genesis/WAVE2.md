# Wave 2: integration, regression and the learning cycles

Status: 2026-11-25. Wave 2 of Project Cognitive Genesis. Three lanes (U, F, L) were built in parallel from `main` at
`3559069` (Wave 1 merged) and integrated on `genesis-wave2` (`828ca8e`), in the order U, F, L. A follow-up lane (X)
and this documents lane (D) started from `828ca8e`; two independent reviews of the integrated tree followed, and a
measurement fix lane (Y) after them (§4). Sources: the lane reports, the two reviews, ADR-056, the evidence files
named below and the deliverables [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) (H) and
[RECURSIVE_KNOWLEDGE_ASSESSMENT.md](RECURSIVE_KNOWLEDGE_ASSESSMENT.md) (F). Nothing here is a claim of consciousness,
awareness or general intelligence; the names describe functions.

**Wave 2's three main results are negative or neutral.**

1. **The integrated loop equals the baseline on held-out.** With every link between instinct, imagination, the
   workspace, monitoring and knowledge evolution switched on, knowledge correctness is 0.710 [re-measured: lane Y]
   against the baseline's 0.713 [re-measured: lane Y], inside the noise, and a pass costs about 70 % more wall time
   (`genesis_integration.json`). The integrated configuration is not recommended. The benchmark review found that
   most links were not exercised by its worlds, that its paired comparisons were not on identical worlds (with
   separate random streams the reviewer measured +0.004, sd 0.032, where lane L reported −0.003; "no gain" holds),
   and that every arbitration it recorded was false (§3.1).
2. **No knowledge-evolution mechanism shows a held-out gain over repeated cycles; the existing loop beats no loop.**
   Over 14 simulated daily cycles the timer's loop keeps knowledge more correct than no loop (0.574 → 0.631 and
   0.497 → 0.571, 8 of 8 paired seeds). Knowledge still falls over the cycles, partly by the world's design (the
   truth changes 1.75 and 3.3 times per cycle against a budget of 2 and 3 tasks) and partly by the product's: the
   loop recovers about half of what a same-budget blind schedule recovers (§3.2). Only the idle re-check could move
   the metric, and it showed no evidence of gain; the other five lane K mechanisms were not exercised by this world
   (`genesis_learning_cycles.json`).
3. **Fault injection found real failures, now guarded.** Making each Wave 1 mechanism raise, time out or return
   garbage broke the owner's question, a status page, the timer's pass, the worker or a voice session in 44 of 75
   cases and 6 of 6 voice cases; 14 guards in 10 files fixed them (75 of 75 and 6 of 6 pass). 9 of the 10 adversarial
   cases added in Wave 2 pass; the tenth found a measured gap ([REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md)).
   The security review found that one of the new voice guards could deliver an answer after the owner forgot its
   task (§4).

**No default changed.** Wave 2 adds two settings (`arbitration_record` on, a record only; `salience_attention` off),
no schema change, no new background activity and no dependency. At the shipped defaults the offline research
benchmark's digest is still `68731d7235cb…`, Wave 0's (lanes U, F and L each checked it on their branch).

## 1. What each lane built

| Lane | What it built | Code and documents | Decision |
|---|---|---|---|
| U regression and cognitive UAT (directive §12; deliverable H) | the 20 mandatory test areas mapped to tests; ten adversarial cases with expectations committed first (origin `added_wave2`); fault injection of 25 entry points × 3 failure modes with every Wave 1 setting on, and the 14 guards it proved necessary; the voice follow-up made to fail; rollback of every Wave 1 switch; Wave 0 equivalence | `tests/uat/` (112 cases: 75 + 6 + 26 + 5), `acceptance/cognition/adversarial.py`, guards in `instance/executive.py`, `webcortex/{curiosity,autonomy,scheduler,orchestrator,consolidate}.py`, `pocket/server.py`, `resources/policy.py`, `internal_state.py`, `voice/hub.py`; [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) | no ADR (no mechanism added) |
| F recursive knowledge assessment (directive §7; deliverable F) | directive §7 mapped to the code (built / partial / not built); a learning-cycles experiment: fictional software releases changing over daily cycles, run through the real extraction, clustering, consolidation, prediction and timer code; 15 configurations; a verdict rule committed before the held-out run | `acceptance/cognition/cycles_splits.py`, `cycles.py`, `scripts/bench_learning_cycles.py`, `tests/acceptance/test_learning_cycles_bench.py` (7); [RECURSIVE_KNOWLEDGE_ASSESSMENT.md](RECURSIVE_KNOWLEDGE_ASSESSMENT.md) | no ADR (no mechanism added) |
| L the integration loop (directive §8) | the link map of the eight integration requirements (real / display-only / missing, at the defaults and with everything on); the arbitration record; salience ordering the admitted candidates; the loop in the self-description (`doing.cycle`); a staged end-to-end test; an integrated benchmark family on new frozen worlds | `webcortex/scheduler.py` (`_arbitrated`), `webcortex/autonomy.py`, `webcortex/salience.py`, `self_description.py`, `acceptance/cognition/integrated_splits.py`, `integrated.py`, `scripts/bench_wave2_integration.py`; tests `tests/webcortex/test_integration_loop.py`, `test_integration_arbitration.py`, `tests/acceptance/test_genesis_integration_bench.py` (16 new) | ADR-056 |
| X follow-ups (from `828ca8e`) | brief: lane U's coverage extended to lane L's settings, a test that no setting is missing from the equivalence and rollback lists, the reflection section bound, `strategies.active` required to be `True`, the voice delivery under a failing claim, a load-sensitive voice test made deterministic, lane F's uncapped reflections | in progress when this record was written | see "Reviews and fixes" |
| Y measurement fixes (after the reviews) | the integrated family's separate random streams, the true arbitration count, the vacuous "integrated" retention and adversarial runs, the learning cycles' references | in progress when this record was written; the figures it re-measures are marked beside each number | see "Reviews and fixes" |
| D documents (from `828ca8e`) | deliverables E, G, I, J and this record; the index updates | [IMPLEMENTED_ENHANCEMENTS.md](IMPLEMENTED_ENHANCEMENTS.md), [BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md), [ARCHITECTURE_AFTER_GENESIS.md](ARCHITECTURE_AFTER_GENESIS.md), [FINAL_EVALUATION.md](FINAL_EVALUATION.md) | — |

Commits on `genesis-wave2` (cherry-picked from the lane branches; the lane reports name the lane-branch commits):
U `7ea50a7` (the expectations, before the cases), `b5d2f2f`, `314ab80`, `9a7796f`, `b73b92b`, `fb6667b`, `9d9db40`;
F `22f1add` (the worlds, before the harness), `4ac4c98`, `fd70c27` (the harness and the verdict rule, before any
held-out run), `79140ac`, `840b4ca`, `4d8a60c`; L `72d7a59` (the worlds, before the runner and any link), `a7981e0`,
`f97411e`, `828ca8e`. Two cherry-picks conflicted in `src/neuroscifi/background.py` (lane F's and then lane L's
`RUNNERS` entries against the entries already applied); both sides were kept.

Frozen worlds written before the code they measure: `integrated_splits.py` (development seeds 61–63, held-out
601–604) and `cycles_splits.py` (development 41–43, held-out 141–144; four outlet wordings amended after one
development run and before the harness existed, disclosed by lane F). `splits.py` and `withheld.py` and their pinned
digests are unchanged. **The withheld family was not run in Wave 2**: the ledger `genesis_final_uses.jsonl` still
holds the three lines of Waves 0 and 1.

## 2. The defaults

Read from `WebConfig` (`src/neuroscifi/webcortex/config.py`) on `828ca8e`. Every Wave 1 default is unchanged
([WAVE1.md](WAVE1.md) §2; [IMPLEMENTED_ENHANCEMENTS.md](IMPLEMENTED_ENHANCEMENTS.md)).

| Setting | Default | What it changes when on | Evidence for the default |
|---|---|---|---|
| `arbitration_record` (web) | **on** (a record) | when the re-check or a due refresh takes the subject the timer's curiosity wanted in the same pass, the winner's task records the losing mechanism and the rule, never the subject; it launches, admits and refuses nothing | a record only: results identical with it off; a task forget removes it (ADR-056 Decision 1). **Defect:** with the re-check on it records conflicts that did not happen (§3.1) [fixed: lane X]; dormant at the shipped defaults (the re-check is off) |
| `salience_attention` (web) | **off** | admission takes the candidates it already admitted in salience order (the owner's first); the same candidates admitted and refused | not exercised by the benchmark's worlds: the timer's curiosity pool never offered more than one candidate, so the order never changed the first (benchmark review M1) |
| self-description `doing.cycle` and one `short()` sentence | always (display) | the latest background investigation and how it was admitted, the latest arbitrations, the predictions settled so far | read-only; ids, numbers and fixed words only |
| lane U's guards | always (no switch) | only when a mechanism fails: the owner's question, status pages, the timer, the worker and voice sessions continue; the failure is logged or shown | 44 of 75 fault cases fail without them |

## 3. Results

### 3.1 The integration loop (lane L; `genesis_integration.json`)

Simulated Web Cortex worlds through the real timer pass and idle investigator; offline; 0 model calls; the host's
background gate read as allowed. Held-out = 2 worlds × 4 seeds; development = 2 × 3. Spread = per-world sd over seeds.

| Measure | Split | Baseline | Integrated |
|---|---|---|---|
| knowledge correctness (mean over steps) | held-out | 0.713 [re-measured: lane Y] | 0.710 [re-measured: lane Y] |
| paired difference, integrated − baseline | held-out | | −0.003 [re-measured: lane Y] (sd 0.028 [re-measured: lane Y]) |
| the same with separate world and outcome streams (the reviewer's probe) | held-out | 0.700 | +0.004 (sd 0.032; 5 seeds higher, 3 lower) |
| knowledge correctness | development | 0.741 [re-measured: lane Y] | 0.738 [re-measured: lane Y] |
| scenario three-way Brier (three equal futures: 0.667) | held-out | — | 0.698 [re-measured: lane Y] (scenarios alone 0.712 [re-measured: lane Y]; per seed 0.60–0.85 [re-measured: lane Y]): **not better than equal futures** |
| | development | — | 0.655 [re-measured: lane Y] (alone 0.638 [re-measured: lane Y]): a little better than the reference |
| conflicts (two tasks on one subject in a pass); belief writes | both | 0; 0 | 0; 0 |
| self-description loop probes (plumbing) | both | 1 of 4 before the change → 4 of 4 | 4 of 4 |
| **cost:** wall s per world run | held-out | 0.319 [re-measured: lane Y] | 0.550 [re-measured: lane Y] (about +70 %) |

**What the links did in these worlds** (benchmark review M1): the idle re-check ran 14 tasks per run and could move
the metric (alone 0.707 [re-measured: lane Y], no gain); salience attention never changed the first candidate (the
timer's curiosity pool never held more than one), and learning progress never changed a launch: both **not exercised
by this world**; premise marks (no reader without the re-check) and scenario records (quarantined by design) **cannot
affect this metric by construction**. With 4 seeds and a per-world spread of 0.033 and 0.042 against a paired sd of
0.028 [re-measured: lane Y], a smaller gain could not have been seen.

**The arbitration counts are not conflicts resolved.** With the re-check on, the record fired 14 [re-measured: lane Y]
(held-out) and 11 [re-measured: lane Y] (development) times per world, each time the cooldown refused a subject the
re-check had taken; in none was that subject among the timer's curiosity candidates and in none was budget left, so
none was a real conflict (a product defect in `scheduler.py` [fixed: lane X]). **The "integrated" retention and
adversarial passes are partly vacuous**: the harness forced every Wave 1 setting off inside each case (benchmark
review H2) [re-measured: lane Y]. Those runs had 18 adversarial cases (13 / 4 / 1), before lane U's ten.

**No gain over the baseline on either split.** Assessed and not built: a scenario's or forecast's settled error
feeding a learner (no information beyond the real settlement), a low-support monitoring verdict asking for a later
re-check (not measurable on any frozen world), broadcast ordering (ADR-056 Decision 4).

The staged end-to-end test (`tests/webcortex/test_integration_loop.py`) follows one stimulus through every hand-over on
stored records; a spy on the imagined store sees no call from curiosity, autonomy or the orchestrator, and deleting
every imagined record leaves curiosity's ranking, the candidates and the knowledge rows identical. Competing
objectives (`test_integration_arbitration.py`): every background activity passes `background.allowed`; all three
paths ask `autonomy.admission`; with lane L's settings on the caps, budgets and policy are unchanged; quiet and a
spent budget stop everything new.

### 3.2 The learning cycles (lane F; `genesis_learning_cycles.json`)

Held-out: 2 worlds × 4 seeds × 14 cycles; primary metric `correct` (share of subjects whose newest held item states
the current version, mean over cycles 1–14), mean ± sd over seeds.

| Configuration | ho-sparse | ho-busy | Cost per run (pages; wall s) |
|---|---|---|---|
| no loop (reference) | 0.574 ± 0.100 | 0.497 ± 0.082 | 106 / 102; 0.20 / 0.17 |
| baseline (today's loop) | 0.631 ± 0.105 | 0.571 ± 0.057 | 208 / 191; 0.42 / 0.39 |
| idle re-check alone | 0.611 ± 0.087 | 0.567 ± 0.088 | 213 / 196; 0.54 / 0.50 |
| all six | 0.625 ± 0.073 | 0.580 ± 0.083 | 213 / 194; 0.57 / 0.54; +0.14 MB |

"The loop" is the timer's pass only; the worker's idle investigation (`autonomy.idle`) was not run (benchmark review
M4). The loop against no loop: +0.057 and +0.073, 8 of 8 seeds.

**The decay and its references.** `correct` falls 0.875 → 0.438 and 0.854 → 0.463 from cycle 0 to 14 with the loop,
to 0.375 and 0.331 without. The world's truth changes 1.75 and 3.3 times per cycle against a budget of 2 and 3 tasks,
and 14 and 17 % of subjects are never covered by the owner's research, so no budget-bound loop holds its cycle-0
level. References from the same worlds, computed by the reviewer (review2/bench/f_oracle.py), to be replaced by the
harness's references: a blind round-robin of the same budget, reading each look's majority value, holds
0.79 [re-measured: lane Y] and 0.72 [re-measured: lane Y] on average (0.67 [re-measured: lane Y] and
0.57 [re-measured: lane Y] at cycle 14); an oracle that refreshes exactly the changed subjects holds
0.96 [re-measured: lane Y] and 0.93 [re-measured: lane Y]. The loop's 0.631 and 0.571 recover about half of the gain
a blind schedule makes over never refreshing; the shortfall is the product's: contested refreshes that never revise a
stale item without the owner's answer, and the due order (RECURSIVE_KNOWLEDGE_ASSESSMENT §5).

Verdicts: `idle_recheck`, the only mechanism that could move the metric: **no evidence of gain** (−0.019 and −0.004
against baseline sds of 0.105 and 0.057). `hypothesis_fields`, `premise_recheck`, `hypothesis_abandon`, `reflection`,
`strategy_selection`: **not exercised by this world** (they acted, but nothing consumed what they wrote, or there was
one research mode to rank). With 4 seeds a gain below 0.105 / 0.057 cannot pass the rule, while the paired noise is
about 0.025–0.031: "no evidence" does not exclude a smaller gain. Nothing lost: 0 lost and 0 regressed of the
retention base in 120 held-out runs; 0 self-generated and 0 syndicated-wrong versions admitted; 0 confidence rises
from one-origin looks. Harness correction disclosed: the no-loop reference first ran the loop (fixed, test added);
both splits re-run, 0 values changed apart from wall time and file size. The reviewer reproduced the held-out run on
the integrated tree.

### 3.3 Regression, fault injection, rollback (lane U; [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md))

| Check | Result on `gen2-u` |
|---|---|
| 20 mandatory test areas | every area has passing tests; gaps closed offline by 112 new cases; 9 areas keep a part unverified offline |
| fault injection (25 entry points × raise / time out / garbage) | 75 of 75; 44 of 75 fail without the guards |
| voice follow-up made to fail | 6 of 6; 6 of 6 fail without its 2 guards |
| rollback of every Wave 1 switch on the next call | 26 of 26; no product change needed |
| adversarial (28 cases on the integrated tree) | original 13 of 13, added in Wave 1 4 of 4, restored 1 of 1; **added in Wave 2: 9 of 10**: `reflection_bounded_when_everything_asks` fails (a reflection's `missing` section held 21 lines against `MAX_LINES` 20) [fixed: lane X]; reported by origin, never added up |
| Wave 0 equivalence | offline digest `68731d7235cb…` at defaults and all off; acceptance 7 of 7; guard 0 different; reproduction 8 of 8; schema 14, 16, 17, 18 files opened and used |
| full non-e2e suite on the lane's branch | 5,736 passed, 22 skipped, 35 subtests, 0 failed |

Observation, not fixed in the lane: `strategies.active` returning a truthy non-boolean would let admission rank by
Thompson sampling without a promotion (the function returns a bool today). Lane U's fault cases cover the Wave 1
settings; lane L's two settings arrived on another branch (lane X's brief).

### 3.4 The cognitive benchmark's other families

Not re-run in Wave 2 beyond the self-description family (lane L: 30 of 30 under the current key, 29 of 30 under the
original, 1 surface), retention and adversarial (lanes L and F). The anticipation, information-value, counterfactual
and selective numbers stand as Wave 1 measured them ([WAVE1.md](WAVE1.md) §4); the full set is in
[BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md).

## 4. Reviews and fixes

Two independent reviews read the integrated tree `828ca8e` (`git diff 3559069..828ca8e`), read-only; neither read,
imported or ran `withheld.py`, and neither passed `--final`.

| Review | Verdict | Findings | What they are about |
|---|---|---|---|
| measurement (lanes L, F, U) | request changes, for the evidence and wording only; all three negative headlines stand | 4 high, 5 medium, 7 low | evidence and wording, plus one product defect: the false arbitration record |
| security (the Wave 2 code) | medium risk | 1 medium, 6 low (and 3 informational) | one product defect: a voice delivery after a forget; the rest low |

What the measurement review found (its high and medium findings):

- **H1, product defect:** every arbitration lane L's benchmark recorded was false: "re-check beat curiosity" was
  inferred from any subject the cooldown refused, although none was among the timer's curiosity candidates and no
  budget was left. The record ships on; it is dormant at the shipped defaults (the re-check is off) [fixed: lane X].
- **H2:** lane L's "integrated" retention and adversarial runs forced every Wave 1 setting off inside each case, so
  their passes are partly vacuous (it also hid lane U's reflection gap).
- **H3:** lane L's paired comparisons were not on identical worlds: one random stream drove the world and the
  research outcomes. With separate streams: +0.004 (sd 0.032; 5 higher, 3 lower) against the reported −0.003; "no
  gain" holds.
- **H4:** lane F's decay was reported without references; the same worlds show it is partly the world's design and
  partly the product's (§3.2).
- **M1:** mechanisms that never acted on the metric were reported as "no evidence"; they are "not exercised by this
  world". **M2:** a failed salience record is ordered as score 0 and recorded "by salience" (only with
  `salience_attention` on). **M3:** the verdict rules cannot see gains smaller than the baseline's spread (0.105 /
  0.057 for lane F, 0.033 / 0.042 for lane L). **M4:** lane F's loop is the timer's pass only. **M5:** the lane
  reports cite lane-branch commit hashes that do not exist on `genesis-wave2`, and "18 of 18" adversarial counts that
  predate lane U's ten cases (28 on this tree).
- Low: lane L stages before applying settings; its "best single link" is chosen on held-out (a tie); the scenario
  claim is stronger than its spread; `salience_never_reorders_admission` holds only with `salience_attention` off;
  seed numbers overlap without leaking; one rounding (+0.074 → +0.073); error persistence mixes censoring with effect.

What the security review found:

- **M1, product defect:** when the voice follow-up's delivery claim fails (for example a forget holding the write
  lock past the 10 s busy timeout), lane U's guard delivers anyway, so a forgotten task's question and outcome go back
  into the transcript and the session context after the forget, and two processes could deliver twice [fixed: lane X].
- Low: subject keys and exception text in the process log, which forget never reaches [fixed: lane X]; a failed
  learning-progress read falls back to a more lenient futility rule; a rules-v1 fallback decision is stored with
  nothing to show it; re-check submissions made before a failure are not counted in the pass; a test pinned the
  broken reflection bound; a follower slot can leak when `_resume_follow` fails part-way.
- Checked and holding: no guard wraps quiet, `background.allowed`, the caps or admission's refusals; no guard can
  swallow a stop; `doing.cycle` carries ids, numbers and fixed words only and nothing of a family's or another
  person's task; the arbitration record goes with a task forget; the imagined-store quarantine and the background
  registry are intact.

The fixes (lane X for code and the security findings, lane Y for the measurements) are recorded here:

<<WAVE2-REVIEWS>>

## 5. Validation

<<WAVE2-VALIDATION>>

## 6. What stays open

| Item | Source |
|---|---|
| **No gain from integration**; the integrated configuration is not recommended (+70 % wall time) | lane L, ADR-056 |
| **Knowledge decays in every configuration**, partly by the world's design; the loop recovers about half of what a same-budget blind schedule recovers; a contested refresh never revises a stale item without the owner's answer; the owner's ruling on automatic supersession under a recency + independence rule is the main route lane F identified | lane F; benchmark review H4 |
| Scenario records are not better than three equal futures on held-out (per seed 0.60–0.85 [re-measured: lane Y] against 0.667); they stay off | lane L; benchmark review L3 |
| The reflection section bound (21 lines against 20) [fixed: lane X] | lane U; security review L5 |
| `strategies.active` must return `True` exactly before admission ranks by it | lane U; lane X's brief |
| A failing delivery claim delivers the voice answer anyway, also after the owner forgot the task, and two processes could deliver it twice [fixed: lane X] | lane U; security review M1 |
| One voice follow-up test failed once under load in a full-suite run (passed 3 of 3 alone) | lane L; lane X's brief |
| Reflections are kept without a cap (~3 KB each; about 156 KB a year at the weekly default) | lane F; lane X's brief |
| Open re-check marks grow with unresolved contradictions (19 and 43 after 60 development cycles; bounded at 500) | lane F |
| The idle re-check prefers old unchanged items (7 of 72 caught a change against 19 of 79 due refreshes, development) | lane F |
| The individual cannot recognise its own text republished on the web; two independent echo pages would read as corroboration (not staged) | lane F |
| A scenario settles one timer pass (15 minutes) after its prediction; the ×1.5 importance is keyed by the failed item's id; core forecasts are never compared with what followed | lane L |
| The arbitration record writes conflicts that did not happen when the re-check is on [fixed: lane X] | benchmark review H1 |
| Subject keys and exception text reach the process log, which forget never reaches [fixed: lane X] | security review L1 |
| Lane U's fault and rollback tests predate lane L's two settings; a failed salience record is ordered as score 0 with `salience_attention` on | benchmark review M2; lane X's brief |
| Lane U's rollback covers switches, not the numeric bounds (`imagine_max_*`, `recheck_*`, `reflection_interval_days`, the broadcast threshold) | lane U |
| **Unverified:** the open web, a live answer model, real hardware (GPU, memory pressure), real speech and phones, two real processes, systemd, a real second account, a real worker running the loop for days | all lanes; [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) §7 |
| Owner decisions pending: automatic supersession; `predictive_regulation`, `idle_recheck`, `adaptive_depth`, `information-v1`, `curiosity_learning_progress`, `salience_attention` | [FINAL_EVALUATION.md](FINAL_EVALUATION.md) §5 |

## 7. Backing each piece out

| Piece | How to back it out | What stays |
|---|---|---|
| The arbitration record | `nsf web config --set arbitration_record=false` | the decision events already written (a task forget removes them) |
| Salience attention | leave off (`nsf web config --set salience_attention=false`) | — |
| `doing.cycle` | revert lane L's commit `a7981e0` before the merge into `main` (the same commit carries the arbitration record and salience attention) | — |
| Lane U's guards | revert `314ab80` and `9a7796f` before the merge (the failures they catch come back) | — |
| The benchmarks and tests (integrated family, learning cycles, UAT, `added_wave2` cases) | revert their commits; nothing in the product reads them | — |
| Any Wave 2 code | revert its commits on `genesis-wave2` before the merge into `main` | no schema change, so nothing in a file |
