# Wave 2: integration, regression and the learning cycles

Status: 2026-11-25. Wave 2 of Project Cognitive Genesis. Three lanes (U, F, L) were built in parallel from `main` at
`3559069` (Wave 1 merged) and integrated on `genesis-wave2` (`828ca8e`), in the order U, F, L. A follow-up lane (X)
and this documents lane (D) started from `828ca8e`. Sources: the lane reports, ADR-056, the evidence files named
below and the deliverables [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) (H) and
[RECURSIVE_KNOWLEDGE_ASSESSMENT.md](RECURSIVE_KNOWLEDGE_ASSESSMENT.md) (F). Nothing here is a claim of consciousness,
awareness or general intelligence; the names describe functions.

**Wave 2's three main results are negative or neutral.**

1. **The integrated loop equals the baseline on held-out.** With every link between instinct, imagination, the
   workspace, monitoring and knowledge evolution switched on, knowledge correctness is 0.710 against the baseline's
   0.713 (paired −0.003, sd 0.028; 4 seeds higher, 4 lower), no single link beats the baseline, and a pass costs about
   70 % more wall time (`genesis_integration.json`). The integrated configuration is not recommended.
2. **No knowledge-evolution mechanism shows a held-out gain over repeated cycles, and knowledge decays in every
   configuration.** Over 14 simulated daily cycles the existing loop keeps knowledge more correct than no loop
   (0.574 → 0.631 and 0.497 → 0.571, 8 of 8 paired seeds), but `correct` still falls from 0.88 to 0.44 and from 0.85
   to 0.46; each of lane K's six mechanisms gets the verdict "no evidence" under a rule committed before the held-out
   run (`genesis_learning_cycles.json`).
3. **Fault injection found real failures, now guarded.** Making each Wave 1 mechanism raise, time out or return
   garbage broke the owner's question, a status page, the timer's pass, the worker or a voice session in 44 of 75
   cases and 6 of 6 voice cases; 14 guards in 10 files fixed them (75 of 75 and 6 of 6 pass). One added adversarial
   case found a measured gap that was not fixed in the lane ([REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md)).

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
| `arbitration_record` (web) | **on** (a record) | when the re-check or a due refresh takes the subject the timer's curiosity wanted in the same pass, the winner's task records the losing mechanism and the rule, never the subject; it launches, admits and refuses nothing | display-only: results identical with it off (leaf-equal, both splits); a task forget removes it (ADR-056 Decision 1) |
| `salience_attention` (web) | **off** | admission takes the candidates it already admitted in salience order (the owner's first); the same candidates admitted and refused | held-out 0.713 alone, the baseline's value; development +0.003, within noise (ADR-056 Decision 2) |
| self-description `doing.cycle` and one `short()` sentence | always (display) | the latest background investigation and how it was admitted, the latest arbitrations, the predictions settled so far | read-only; ids, numbers and fixed words only |
| lane U's guards | always (no switch) | only when a mechanism fails: the owner's question, status pages, the timer, the worker and voice sessions continue; the failure is logged or shown | 44 of 75 fault cases fail without them |

## 3. Results

### 3.1 The integration loop (lane L; `genesis_integration.json`)

Simulated Web Cortex worlds through the real timer pass and idle investigator; offline; 0 model calls; the host's
background gate read as allowed. Held-out = 2 worlds × 4 seeds; development = 2 × 3. Spread = per-world sd over seeds.

| Measure | Split | Baseline | Integrated | Best single link |
|---|---|---|---|---|
| knowledge correctness (mean over steps) | held-out | 0.713 (sd 0.033 / 0.042) | 0.710; paired −0.003 (sd 0.028; 4 higher, 4 lower) | 0.713 (every link alone except the re-check, 0.707) |
| | development | 0.741 (sd 0.094 / 0.022) | 0.738; paired −0.003 (sd 0.012; 2 higher, 4 lower) | salience attention 0.744 |
| stale caught per investigation | held-out | 0.263 | 0.263 | re-check 0.266 |
| noise share | held-out / development | 0.406 / 0.421 | 0.408 / 0.413 | salience 0.406 / 0.402 |
| scenario three-way Brier (equal futures 0.667) | held-out / development | — | 0.698 / 0.655 | scenarios alone 0.712 / 0.638: **worse than equal futures on held-out** |
| conflicts; wasted looks; belief writes | both | 0; 0; 0 | 0; 0; 0 | every configuration |
| self-description loop probes (plumbing) | both | 1 of 4 before the change → 4 of 4 | 4 of 4 | |
| retention; adversarial (13 / 4 / 1, before lane U's cases) | both | all retained; 18 pass | all retained; 18 pass | |
| **cost:** wall s per world run | held-out / development | 0.319 / 0.216 | 0.550 / 0.361 (about +70 %) | re-check 0.458; arbitration 0.330 |

Ablations (held-out correctness): without the re-check 0.714, without learning progress or salience attention 0.707,
without marks, scenarios or the arbitration record 0.710. **No gain over the baseline or the best single link on
either split.** Assessed and not built: a scenario's or forecast's settled error feeding a learner (no information
beyond the real settlement), a low-support monitoring verdict asking for a later re-check (not measurable on any
frozen world), broadcast ordering (ADR-056 Decision 4).

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

Verdicts: `hypothesis_fields`, `premise_recheck`, `idle_recheck`, `hypothesis_abandon`, `reflection`,
`strategy_selection`: **no evidence** each (five are identical to the baseline on every knowledge metric; the idle
re-check moves it −0.019 and −0.004 against sds of 0.105 and 0.057). Nothing lost: 0 lost and 0 regressed of the
retention base in 120 held-out runs; 0 self-generated and 0 syndicated-wrong versions admitted; 0 confidence rises
from one-origin looks. Main limit found: a contested refresh never revises a stale item without the owner's answer
(RECURSIVE_KNOWLEDGE_ASSESSMENT §5). Harness corrections disclosed: the no-loop reference first ran the loop (fixed,
test added); both splits re-run, 0 values changed apart from wall time and file size.

### 3.3 Regression, fault injection, rollback (lane U; [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md))

| Check | Result on `gen2-u` |
|---|---|
| 20 mandatory test areas | every area has passing tests; gaps closed offline by 112 new cases; 9 areas keep a part unverified offline |
| fault injection (25 entry points × raise / time out / garbage) | 75 of 75; 44 of 75 fail without the guards |
| voice follow-up made to fail | 6 of 6; 6 of 6 fail without its 2 guards |
| rollback of every Wave 1 switch on the next call | 26 of 26; no product change needed |
| adversarial `added_wave2` | **9 of 10**: `reflection_bounded_when_everything_asks` fails (a reflection's `missing` section held 21 lines against `MAX_LINES` 20); reported apart from the original 13, the 4 of Wave 1 and the 1 restored, all passing |
| Wave 0 equivalence | offline digest `68731d7235cb…` at defaults and all off; acceptance 7 of 7; guard 0 different; reproduction 8 of 8; schema 14, 16, 17, 18 files opened and used |
| full non-e2e suite at `80718b2` | 5,736 passed, 22 skipped, 35 subtests, 0 failed |

Observation, not fixed in the lane: `strategies.active` returning a truthy non-boolean would let admission rank by
Thompson sampling without a promotion (the function returns a bool today). Lane U's fault cases cover the Wave 1
settings; lane L's two settings arrived on another branch (lane X's brief).

### 3.4 The cognitive benchmark's other families

Not re-run in Wave 2 beyond the self-description family (lane L: 30 of 30 under the current key, 29 of 30 under the
original, 1 surface), retention and adversarial (lanes L and F). The anticipation, information-value, counterfactual
and selective numbers stand as Wave 1 measured them ([WAVE1.md](WAVE1.md) §4); the full set is in
[BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md).

## 4. Reviews and fixes

<<WAVE2-REVIEWS>>

## 5. Validation

<<WAVE2-VALIDATION>>

## 6. What stays open

| Item | Source |
|---|---|
| **No gain from integration**; the integrated configuration is not recommended (+70 % wall time) | lane L, ADR-056 |
| **Knowledge decays in every configuration**; a contested refresh never revises a stale item without the owner's answer; the owner's ruling on automatic supersession under a recency + independence rule is the main route lane F identified | lane F |
| Scenario records score worse than equal futures on held-out (0.698–0.712 against 0.667); they stay off | lane L |
| The reflection section bound (21 lines against 20) | lane U; lane X's brief |
| `strategies.active` must return `True` exactly before admission ranks by it | lane U; lane X's brief |
| A failing delivery claim delivers the voice answer from this follower (a second process could then deliver it twice) | lane U; lane X's brief |
| One voice follow-up test failed once under load in a full-suite run (passed 3 of 3 alone) | lane L; lane X's brief |
| Reflections are kept without a cap (~3 KB each; about 156 KB a year at the weekly default) | lane F; lane X's brief |
| Open re-check marks grow with unresolved contradictions (19 and 43 after 60 development cycles; bounded at 500) | lane F |
| The idle re-check prefers old unchanged items (7 of 72 caught a change against 19 of 79 due refreshes, development) | lane F |
| The individual cannot recognise its own text republished on the web; two independent echo pages would read as corroboration (not staged) | lane F |
| A scenario settles one timer pass (15 minutes) after its prediction; the ×1.5 importance is keyed by the failed item's id; core forecasts are never compared with what followed | lane L |
| Lane U's rollback covers switches, not the numeric bounds (`imagine_max_*`, `recheck_*`, `reflection_interval_days`, the broadcast threshold) | lane U |
| **Unverified:** the open web, a live answer model, real hardware (GPU, memory pressure), real speech and phones, two real processes, systemd, a real second account, a real worker running the loop for days | all lanes; [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) §7 |
| Owner decisions pending: automatic supersession; `predictive_regulation`, `idle_recheck`, `adaptive_depth`, `information-v1`, `curiosity_learning_progress`, `salience_attention` | [FINAL_EVALUATION.md](FINAL_EVALUATION.md) §5 |

## 7. Backing each piece out

| Piece | How to back it out | What stays |
|---|---|---|
| The arbitration record | `nsf web config --set arbitration_record=false` | the decision events already written (a task forget removes them) |
| Salience attention | leave off (`nsf web config --set salience_attention=false`) | — |
| `doing.cycle` | revert lane L's commit `a7981e0` before the merge into `main` | — |
| Lane U's guards | revert `314ab80` and `9a7796f` before the merge (the failures they catch come back) | — |
| The benchmarks and tests (integrated family, learning cycles, UAT, `added_wave2` cases) | revert their commits; nothing in the product reads them | — |
| Any Wave 2 code | revert its commits on `genesis-wave2` before the merge into `main` | no schema change, so nothing in a file |
