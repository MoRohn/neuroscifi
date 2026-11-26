# Wave 2: integration, regression and the learning cycles

Status: 2026-11-25. Wave 2 of Project Cognitive Genesis. Three lanes (U, F, L) were built in parallel from `main` at
`3559069` (Wave 1 merged) and integrated on `genesis-wave2` (`828ca8e`), in the order U, F, L. A follow-up lane (X)
and this documents lane (D) started from `828ca8e`; two independent reviews of the integrated tree followed, then a
measurement fix lane (Y); lanes Y and X were merged (`735388e`, `484555a`) and a reconciliation lane made the
combined tree consistent and these documents final (§4). Sources: the lane reports, the two reviews, ADR-056, the evidence files
named below and the deliverables [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) (H) and
[RECURSIVE_KNOWLEDGE_ASSESSMENT.md](RECURSIVE_KNOWLEDGE_ASSESSMENT.md) (F). Nothing here is a claim of consciousness,
awareness or general intelligence; the names describe functions.

**Wave 2's three main results are negative or neutral.**

1. **The integrated loop shows no gain beyond the baseline's spread on held-out.** With every link between
   instinct, imagination, the workspace, monitoring and knowledge evolution switched on, knowledge correctness is
   0.726 against the baseline's 0.718 (paired +0.008, sd 0.015; 6 of 8 seeds higher, all of it the re-check's),
   below the baseline's spread, and a pass costs about 73 % more wall time (`genesis_integration.json`, lane Y's
   re-measure with separate random streams for the world and the outcomes). The integrated configuration is not
   recommended. The benchmark review found that most links were not exercised by its worlds, that the first paired
   comparisons were not on identical worlds, and that every arbitration first recorded was false, a product defect
   fixed by lane Y (§3.1).
2. **No knowledge-evolution mechanism shows a held-out gain over repeated cycles; the existing loop beats no loop.**
   Over 14 simulated daily cycles the timer's loop keeps knowledge more correct than no loop (0.574 → 0.631 and
   0.497 → 0.571, 8 of 8 paired seeds). Knowledge still falls over the cycles, partly by the world's design (the
   truth changes 1.75 and 3.3 times per cycle against a budget of 2 and 3 tasks) and partly by the product's: the
   loop recovers about half (0.49 and 0.55) of what a same-budget blind schedule recovers, a schedule whose
   references hold every subject while the owner's research covers 86 and 83 % of them (allowing for that, the check
   of the fixes reads about 0.57 and 0.66; §3.2). Only the idle
   re-check could move the metric, and it showed no evidence of gain; the other five lane K mechanisms were not
   exercised by this world (`genesis_learning_cycles.json`).
3. **Fault injection found real failures, now guarded.** Making each Wave 1 mechanism raise, time out or return
   garbage broke the owner's question, a status page, the timer's pass, the worker or a voice session in 44 of 75
   cases and 6 of 6 voice cases; 14 guards in 10 files fixed them (75 of 75 and 6 of 6 pass). 9 of the 10 adversarial
   cases added in Wave 2 passed as written; the tenth found a measured gap, a reflection section of 21 lines against
   its limit of 20, fixed by lane X (28 of 28 adversarial cases pass on the combined tree;
   [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md)). The security review found that one of the new voice
   guards could deliver an answer after the owner forgot its task; lane X replaced it (§4).

**No default changed.** Wave 2 adds two settings (`arbitration_record` on, a record only; `salience_attention` off),
no schema change, no new background activity and no dependency. At the shipped defaults the offline research
benchmark's digest is still `68731d7235cb…`, Wave 0's (lanes U, F, L, X and Y each checked it on their branch, and the
reconciliation on the combined tree after its fixes).

**One behaviour change older than Wave 2** (review2b N3, lane Y's open item): a timer pass run with a task limit
(`max_tasks`, as the timer's service runs it in queue mode) used to leave a queued due refresh, or one a worker had
claimed first, out of that limit, so curiosity could queue up to the limit again in the same pass. It now counts, so
such a pass can queue fewer tasks than before. The offline research digest at the shipped defaults is unchanged.
Lane L's evidence (`genesis_integration.json`) predates this fix: re-run as a check only and not rewritten, its
held-out knowledge correctness is unchanged on every seed; in one world and seed (ho-crowded, 604) the eight
configurations without the re-check investigate 28 times instead of 29 (caught per investigation 0.103 → 0.107, noise
share 0.483 → 0.500; the baseline's held-out means 0.2272 → 0.2277 and 0.4145 → 0.4167). Development: nothing moved.
Lane F's harness runs the pass without a task limit, so the fix cannot move it.

**Wave 2 took no look at the withheld family.** No lane changed a mechanism behind its four interfaces (the
regulator: predictive regulation; the chooser: `information-v1` and learning progress; the answerer: what-if and
forecast; the describer: the self-description record). Wave 2 added guards that act only when a mechanism fails, two
lane L settings outside those interfaces, benchmarks, and one display field in the self-description
(`doing.cycle`), whose family lane L re-ran with Wave 1's scores (30 of 30 under the current key, 29 of 30 under the
original; ADR-056 "Measured", `genesis_integration.json`). A look would have measured Wave 1's mechanisms again and spent one of the
family's uses for nothing. `withheld.py` was not read, imported directly or run, and no `--final` was passed (the
ledger `genesis_final_uses.jsonl` still holds the three lines of Waves 0 and 1).

## 1. What each lane built

| Lane | What it built | Code and documents | Decision |
|---|---|---|---|
| U regression and cognitive UAT (directive §12; deliverable H) | the 20 mandatory test areas mapped to tests; ten adversarial cases with expectations committed first (origin `added_wave2`); fault injection of 25 entry points × 3 failure modes with every Wave 1 setting on, and the 14 guards it proved necessary; the voice follow-up made to fail; rollback of every Wave 1 switch; Wave 0 equivalence | `tests/uat/` (112 cases: 75 + 6 + 26 + 5), `acceptance/cognition/adversarial.py`, guards in `instance/executive.py`, `webcortex/{curiosity,autonomy,scheduler,orchestrator,consolidate}.py`, `pocket/server.py`, `resources/policy.py`, `internal_state.py`, `voice/hub.py`; [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) | no ADR (no mechanism added) |
| F recursive knowledge assessment (directive §7; deliverable F) | directive §7 mapped to the code (built / partial / not built); a learning-cycles experiment: fictional software releases changing over daily cycles, run through the real extraction, clustering, consolidation, prediction and timer code; 15 configurations; a verdict rule committed before the held-out run | `acceptance/cognition/cycles_splits.py`, `cycles.py`, `scripts/bench_learning_cycles.py`, `tests/acceptance/test_learning_cycles_bench.py` (7); [RECURSIVE_KNOWLEDGE_ASSESSMENT.md](RECURSIVE_KNOWLEDGE_ASSESSMENT.md) | no ADR (no mechanism added) |
| L the integration loop (directive §8) | the link map of the eight integration requirements (real / display-only / missing, at the defaults and with everything on); the arbitration record; salience ordering the admitted candidates; the loop in the self-description (`doing.cycle`); a staged end-to-end test; an integrated benchmark family on new frozen worlds | `webcortex/scheduler.py` (`_arbitrated`), `webcortex/autonomy.py`, `webcortex/salience.py`, `self_description.py`, `acceptance/cognition/integrated_splits.py`, `integrated.py`, `scripts/bench_wave2_integration.py`; tests `tests/webcortex/test_integration_loop.py`, `test_integration_arbitration.py`, `tests/acceptance/test_genesis_integration_bench.py` (16 new) | ADR-056 |
| X follow-ups (from `828ca8e`) | lane U's coverage extended to lane L's settings (4 entry points, two guards proved necessary), a test that no setting is missing from the equivalence and rollback lists, the reflection section bound, `strategies.active` required to be `True`, the voice delivery under a failing claim, a load-sensitive voice test made deterministic; the security review's findings; lane F's uncapped reflections kept (no retention bound, reasoned) | `webcortex/{autonomy,scheduler,reflection,curiosity,consolidate,orchestrator}.py`, `instance/executive.py`, `resources/policy.py`, `internal_state.py`, `self_description.py`, `voice/hub.py`; `tests/uat/` (166 cases on the combined tree), `tests/webcortex/test_wave2_review_fixes.py`; merged by `484555a` | no ADR (no mechanism added) |
| Y measurement fixes (after the reviews) | the arbitration record for a real conflict only (a product fix), the salience order's fallback, lane L's harness (only the links forced, separate random streams for the world and the outcomes, settings before staging, activity counts), lane K's opt-in decoupled draw, lane F's references and counts; the evidence regenerated | `webcortex/{scheduler,salience,autonomy}.py`, `acceptance/cognition/{integrated,knowledge,cycles}.py`; `genesis_integration.json`, `genesis_learning_cycles.json`, `genesis_knowledge_decoupled.json`; merged by `735388e` | ADR-056 rewritten where measured |
| REC reconciliation (from `484555a`) | the fault-injection rig stages a real arbitration conflict; tests where lanes X and Y meet; lane Y's open item (a refresh queued by the timer now counts against `max_tasks`); the check of the fixes' N1–N6 (the voice follow-up's retry, subject-free log detail, wordings); these documents final | `webcortex/scheduler.py`, `voice/hub.py`, `resources/policy.py`, `webcortex/curiosity.py`, `instance/errors.py`; tests in `tests/uat/`, `tests/voice/test_follow_restart.py`, `tests/webcortex/test_integration_arbitration.py`, `test_scheduler.py`, `test_wave2_review_fixes.py`; branch `gen2-rec` | no ADR (no mechanism added) |
| D documents (from `828ca8e`) | deliverables E, G, I, J and this record; the index updates | [IMPLEMENTED_ENHANCEMENTS.md](IMPLEMENTED_ENHANCEMENTS.md), [BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md), [ARCHITECTURE_AFTER_GENESIS.md](ARCHITECTURE_AFTER_GENESIS.md), [FINAL_EVALUATION.md](FINAL_EVALUATION.md) | — |

Commits on `genesis-wave2` (cherry-picked from the lane branches; the lane reports name the lane-branch commits):
U `7ea50a7` (the expectations, before the cases), `b5d2f2f`, `314ab80`, `9a7796f`, `b73b92b`, `fb6667b`, `9d9db40`;
F `22f1add` (the worlds, before the harness), `4ac4c98`, `fd70c27` (the harness and the verdict rule, before any
held-out run), `79140ac`, `840b4ca`, `4d8a60c`; L `72d7a59` (the worlds, before the runner and any link), `a7981e0`,
`f97411e`, `828ca8e`. Two cherry-picks conflicted in `src/neuroscifi/background.py` (lane F's and then lane L's
`RUNNERS` entries against the entries already applied); both sides were kept. Lane D's documents: `ad74201`,
`ecac79c`, `186c13e`. Lanes Y and X were merged with their own commits: Y `fb19aae`, `521ddd9`, `b8c3174`, `f15bef1`,
`d8dd7d7`, `9b53aa0`, `e47cffe` (merge `735388e`); X `8204012`, `0186b57`, `cf623f3`, `aebe096`, `23bc7a0`,
`d85715b`, `8a220a6`, `d7df379` (merge `484555a`).

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
| `arbitration_record` (web) | **on** (a record) | when the re-check or a due refresh takes a subject the timer's curiosity could have launched in the same pass (in its pool, as the kind it launches, with budget left), the winner's task records the losing mechanism and the rule, never the subject; it launches, admits and refuses nothing | a record only: results identical with it off; a task forget removes it (ADR-056 Decision 1). **Defect, fixed:** as first built, with the re-check on it recorded conflicts that did not happen (§3.1); fixed by lane Y (`fb19aae`, `9b53aa0`), re-measured 0 in every configuration; dormant at the shipped defaults (the re-check is off) |
| `salience_attention` (web) | **off** | admission takes the candidates it already admitted in salience order (the owner's first); the same candidates admitted and refused; the priority order stands unless every candidate carries a finite score (lane Y) | not exercised by the benchmark's worlds: the timer's curiosity pool never held more than one subject; on the idle path, 14 admissions per run had two or more candidates and salience never took another first candidate than the priority order (benchmark review M1) |
| self-description `doing.cycle` and one `short()` sentence | always (display) | the latest background investigation and how it was admitted, the latest arbitrations, the predictions settled so far | read-only; ids, numbers and fixed words only |
| lane U's and lane X's guards | always (no switch) | only when a mechanism fails: the owner's question, status pages, the timer, the worker and voice sessions continue; the failure is logged (its type and ids only) or shown | 44 of 75 of lane U's fault cases fail without lane U's 14; 9 of lane X's 12 without its two |

## 3. Results

### 3.1 The integration loop (lane L; `genesis_integration.json`)

Simulated Web Cortex worlds through the real timer pass and idle investigator; offline; 0 model calls; the host's
background gate read as allowed. Held-out = 2 worlds × 4 seeds; development = 2 × 3. Spread = per-world sd over seeds.
Figures: lane Y's re-measure after the benchmark review, with separate random streams for the world's truth changes
and the research outcomes, so every configuration meets the same world (`genesis_integration.json`, recorded at
`b8c3174`; lane L's first measurement is kept in the file under `before_review`).

| Measure | Split | Baseline | Integrated |
|---|---|---|---|
| knowledge correctness (mean over steps) | held-out | 0.718 | 0.726 |
| paired difference, integrated − baseline | held-out | | +0.008 (sd 0.015; 6 higher, 2 lower) |
| knowledge correctness | development | 0.789 | 0.797 |
| scenario three-way Brier (three equal futures: 0.667) | held-out | — | 0.689 (scenarios alone 0.681; per seed 0.574–0.824, alone 0.555–0.801): **not better than equal futures** |
| | development | — | 0.630 (alone 0.640): a little better than the reference |
| conflicts (two tasks on one subject in a pass); belief writes | both | 0; 0 | 0; 0 |
| self-description loop probes (plumbing) | both | 1 of 4 before the change → 4 of 4 | 4 of 4 |
| **cost:** wall s per world run | held-out | 0.318 | 0.551 (about +73 %) |

**What the links did in these worlds** (benchmark review M1; per-run counts in the evidence's `activity`): the idle
re-check ran 14 tasks per run and moved the metric (alone 0.726, paired +0.008, below the spread; integrated against
it +0.0001); salience attention never changed the first candidate (the timer's curiosity pool never held more than
one subject; on the idle path, 14 admissions per run had two or more candidates and salience never took another
first candidate than the priority order), and learning progress changed which noise subject the idle path looked at
but never a held version: both **not exercised by this world**; premise marks (no reader without the re-check) and
scenario records (quarantined by design) **cannot affect this metric by construction**. With 4 seeds and the
baseline's per-world sd of 0.047 and 0.071 against a paired noise of 0.015, a smaller gain could not have been seen.

**The first arbitration counts were not conflicts resolved.** With the re-check on, the record fired 14 (held-out)
and 11 (development) times per world, each time the cooldown refused a subject the re-check had taken; in none was
that subject among the timer's curiosity candidates, so none was a real conflict (a product defect in `scheduler.py`,
fixed by lane Y: `fb19aae`, `9b53aa0`; re-measured: 0 in every configuration). **The first "integrated" retention and
adversarial passes were partly vacuous**: the harness forced every Wave 1 setting off inside each case (benchmark
review H2); re-run with only the links forced: 27 of 28 adversarial cases pass like the plain run on that tree (the
one failure was lane U's measured gap, since fixed by lane X), retention all retained. The first runs had 18
adversarial cases (13 / 4 / 1), before lane U's ten.

**No gain beyond the baseline's spread on either split.** Assessed and not built: a scenario's or forecast's settled error
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
level. References from the same worlds, computed by the harness from the world generator alone with the run's budget
(`cycles.references`; `genesis_learning_cycles.json` `references`, held-out): a perfect snapshot never refreshed holds
0.670 and 0.574 on average; a blind round-robin of the same budget, reading each look's majority value (a tie teaches
nothing), holds 0.786 and 0.708 (0.67 and 0.54 at cycle 14); an oracle that refreshes exactly the changed subjects
holds 0.957 and 0.928. The loop's 0.631 and 0.571 recover about half (0.49 and 0.55) of the gain a blind schedule
makes over never refreshing. The references hold every subject from its birth, while the owner's research covers
0.861 and 0.831 of them in every configuration, so part of that gain is out of any product loop's reach; allowing
for coverage the check of the fixes (review2b N6) reads the loop's share as about 0.57 and 0.66 (the reviewer's
computation, not in the evidence file). The shortfall: the 14 and 17 % of subjects the owner's research never covers,
and the product's own limits, contested refreshes that never revise a stale item without the owner's answer and the
due order (RECURSIVE_KNOWLEDGE_ASSESSMENT §5).

Verdicts: `idle_recheck`, the only mechanism that could move the metric: **no evidence of gain** (−0.019 and −0.004
against baseline sds of 0.105 and 0.057). `hypothesis_fields`, `premise_recheck`, `hypothesis_abandon`, `reflection`,
`strategy_selection`: **not exercised by this world** (they acted, but nothing consumed what they wrote, or there was
one research mode to rank). With 4 seeds a gain below 0.105 / 0.057 cannot pass the rule, while the paired noise is
0.026 and 0.033 (`verdict_resolution`): "no evidence" does not exclude a smaller gain. Nothing lost: 0 lost and 0 regressed of the
retention base in 120 held-out runs; 0 self-generated and 0 syndicated-wrong versions admitted; 0 confidence rises
from one-origin looks. Harness correction disclosed: the no-loop reference first ran the loop (fixed, test added);
both splits re-run, 0 values changed apart from wall time and file size. The reviewer reproduced the held-out run on
the integrated tree.

### 3.3 Regression, fault injection, rollback (lane U; [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md))

| Check | Result on `gen2-u` | On the combined tree (lanes X and Y merged, reconciliation) |
|---|---|---|
| 20 mandatory test areas | every area has passing tests; gaps closed offline by 112 new cases; 9 areas keep a part unverified offline | 166 cases in `tests/uat/` (lane X added lane L's entry points, the voice claim's replacement, the setting-list test) |
| fault injection (raise / time out / garbage) | 25 entry points: 75 of 75; 44 of 75 fail without the guards | 29 entry points (lane L's four added): 87 of 87, and 29 of 29 with a failure message naming a subject (none reaches a log or a stored record) |
| voice follow-up made to fail | 6 of 6; 6 of 6 fail without its 2 guards | the claim's guard replaced by lane X (it delivered on a failed claim) and the reconciliation's fixes of review2b N1, N2, N4: 19 of 19 |
| rollback of every switch on the next call | 26 of 26; no product change needed | 28 of 28 (lane L's two settings added) |
| adversarial, reported by origin, never added up | 28 cases: original 13 of 13, added in Wave 1 4 of 4, restored 1 of 1; **added in Wave 2: 9 of 10**: `reflection_bounded_when_everything_asks` failed (a reflection's `missing` section held 21 lines against `MAX_LINES` 20) | **28 of 28** on both splits (`scripts/bench_cognition.py run --family adversarial`): the section bound fixed by lane X (`0186b57`) |
| Wave 0 equivalence | offline digest `68731d7235cb…` at defaults and all off; acceptance 7 of 7; guard 0 different; reproduction 8 of 8; schema 14, 16, 17, 18 files opened and used | §5 |
| full non-e2e suite | 5,736 passed, 22 skipped, 35 subtests, 0 failed (the lane's branch) | §5 |

Observation in lane U, fixed by lane X (`8204012`): `strategies.active` returning a truthy non-boolean would have let
admission rank by Thompson sampling without a promotion; only `True` opens the gate now.

### 3.4 The cognitive benchmark's other families

Not re-run in Wave 2 beyond the self-description family (lane L: 30 of 30 under the current key, 29 of 30 under the
original, 1 surface), retention and adversarial (lanes L, F, Y, and on the combined tree: retention all scenarios
pass and all retained, adversarial 28 of 28, both splits). The anticipation, information-value, counterfactual
and selective numbers stand as Wave 1 measured them ([WAVE1.md](WAVE1.md) §4); the full set is in
[BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md).

## 4. Reviews and fixes

Two independent reviews read the integrated tree `828ca8e` (`git diff 3559069..828ca8e`), read-only; a third
independent check read the fix lanes' work (`git diff 828ca8e..484555a`, review2b). None read, imported or ran
`withheld.py`, and none passed `--final`.

| Review | Verdict | Findings | What they are about |
|---|---|---|---|
| measurement (lanes L, F, U) | request changes, for the evidence and wording only; all three negative headlines stand | 4 high, 5 medium, 7 low | evidence and wording, plus one product defect: the false arbitration record |
| security (the Wave 2 code) | medium risk | 1 medium, 6 low (and 3 informational) | one product defect: a voice delivery after a forget; the rest low |
| the check of the fixes (lanes X and Y) | comment (approve with follow-ups): every finding of both reviews fixed; no new high or medium issue | 6 low (N1–N6), 3 informational (N7–N9) | the voice follow-up's retry, the queue-mode budget (lane Y's open item), log detail, two wordings |

**Summary: every finding of both reviews is fixed**, in code or in evidence and wording, and every low finding of the
check of the fixes (N1–N6) is fixed in the reconciliation (`gen2-rec`).

What the measurement review found (its high and medium findings):

- **H1, product defect:** every arbitration lane L's benchmark recorded was false: "re-check beat curiosity" was
  inferred from any subject the cooldown refused, although none was among the timer's curiosity candidates and no
  budget was left. The record ships on; it is dormant at the shipped defaults (the re-check is off). Fixed by lane Y.
- **H2:** lane L's "integrated" retention and adversarial runs forced every Wave 1 setting off inside each case, so
  their passes were partly vacuous (it also hid lane U's reflection gap).
- **H3:** lane L's paired comparisons were not on identical worlds: one random stream drove the world and the
  research outcomes. The reviewer's probe (outcomes keyed, the world stream kept) measured +0.004 (sd 0.032; 5
  higher, 3 lower) against the reported −0.003; lane Y's re-measure with both keyed: +0.008 (sd 0.015; 6 / 2).
  "No gain beyond the spread" holds.
- **H4:** lane F's decay was reported without references; the same worlds show it is partly the world's design and
  partly the product's (§3.2).
- **M1:** mechanisms that never acted on the metric were reported as "no evidence"; they are "not exercised by this
  world". **M2:** a failed salience record was ordered as score 0 and recorded "by salience" (only with
  `salience_attention` on). **M3:** the verdict rules cannot see gains smaller than the baseline's spread (0.105 /
  0.057 for lane F; 0.047 / 0.071 for lane L after the re-measure). **M4:** lane F's loop is the timer's pass only.
  **M5:** the lane reports cite lane-branch commit hashes that do not exist on `genesis-wave2`, and "18 of 18"
  adversarial counts that predate lane U's ten cases (28 on this tree).
- Low: lane L staged before applying settings; its "best single link" was chosen on held-out (a tie); the scenario
  claim was stronger than its spread; `salience_never_reorders_admission` holds only with `salience_attention` off;
  seed numbers overlap without leaking; one rounding (+0.074 → +0.073); error persistence mixes censoring with effect.

What the security review found:

- **M1, product defect:** when the voice follow-up's delivery claim fails (for example a forget holding the write
  lock past the 10 s busy timeout), lane U's guard delivered anyway, so a forgotten task's question and outcome went
  back into the transcript and the session context after the forget, and two processes could deliver twice. Fixed by
  lane X.
- Low: subject keys and exception text in the process log, which forget never reaches; a failed learning-progress
  read fell back to a more lenient futility rule; a rules-v1 fallback decision was stored with nothing to show it;
  re-check submissions made before a failure were not counted in the pass; a test pinned the broken reflection bound;
  a follower slot could leak when `_resume_follow` failed part-way. All fixed by lane X.
- Checked and holding: no guard wraps quiet, `background.allowed`, the caps or admission's refusals; no guard can
  swallow a stop; `doing.cycle` carries ids, numbers and fixed words only and nothing of a family's or another
  person's task; the arbitration record goes with a task forget; the imagined-store quarantine and the background
  registry are intact.

The fixes: lane X (code, the security findings; merged `484555a`), lane Y (the measurements and the arbitration
record; merged `735388e`), the reconciliation (`gen2-rec`: the check of the fixes, where the lanes met). Every test
named below exists on the combined tree and passes there (§5).

**The security review** (review2, the Wave 2 code):

| Finding | Severity | Fix | Test |
|---|---|---|---|
| M1: a failed voice delivery claim delivered anyway, also after a forget, and could deliver twice | medium | a claim that raises, times out or returns a non-count is not a claim: nothing is delivered, the follow stays, the claim is asked again after each pause of `CLAIM_WAITS` until `FOLLOW_MAX`; delivery only on a real count above 0, after reading the task again (lane X, `aebe096`) | `tests/uat/test_uat_voice_follow_faults.py::test_a_claim_that_cannot_be_made_delivers_nothing_and_keeps_the_follow`, `::test_two_hubs_with_a_failing_claim_deliver_the_answer_once`, `::test_a_forget_holding_the_lock_past_the_wait_leaves_nothing_of_the_task` |
| L1: subject keys and exception text in the process log and in tasks' `error` events | low | every Wave 2 guard logs and stores the exception's type and ids only (lane X, `23bc7a0`) | `tests/uat/test_uat_fault_injection.py::test_a_failure_message_naming_a_subject_reaches_no_log_and_no_stored_record` (29 entry points) |
| L2: a failing learning-progress read used the more lenient futility rule | low | falls back to the rule of the setting off (lane X, `23bc7a0`) | `tests/webcortex/test_wave2_review_fixes.py::test_a_failing_learning_progress_read_falls_back_to_the_strict_futility_rule` |
| L3: a rules-v1 fallback decision was stored with nothing to show it | low | the stored reason ends `[information-v1 failed (<Type>); decided by rules-v1]` (lane X, `23bc7a0`) | `tests/webcortex/test_wave2_review_fixes.py::test_a_decision_made_by_the_rules_v1_fallback_says_so_where_it_is_stored` |
| L4: re-check tasks queued before `recheck.run` failed were not counted in the pass | low | read back from their decision events, counted against the budget and for arbitration (lane X, `23bc7a0`) | `tests/webcortex/test_wave2_review_fixes.py::test_re_checks_queued_before_the_re_check_failed_still_count_in_the_pass`; with lane Y's condition: `tests/webcortex/test_integration_arbitration.py::test_a_recheck_that_failed_after_queuing_a_subject_in_curiositys_pool_still_records_the_arbitration` |
| L5: a test pinned the broken reflection bound (21 lines against 20) | low | every section cut at `MAX_LINES` and the cut counted; refs per line and text per line bounded; the test restored to `failed == []` (lane X, `0186b57`) | `tests/webcortex/test_knowledge_evolution.py::test_every_section_line_and_text_of_a_reflection_holds_its_documented_bound`, `tests/acceptance/test_cognition_bench.py::test_adversarial_cases_today` |
| L6: a follower slot or count could leak when `_resume_follow` failed part-way | low | try/finally gives both back (lane X, `aebe096`) | `tests/uat/test_uat_voice_follow_faults.py::test_a_pick_up_failing_part_way_gives_back_its_count_and_slot` |
| Info 1: a malformed forecast verdict reads as "no hold" | info | none: the documented fail-open design of the opt-in forecast (ADR-051: it may only add a hold) | — |
| Info 2: an `{"error": …}` salience record was ordered as score 0 | info | the same as the measurement review's M2 (lane Y, `521ddd9`) | see M2 below |
| Info 3: the arbitration record opened its own write; a failure cost the rest of the timer's pass | info | `scheduler._arbitrate`: a failure is a report line and a log warning, the pass goes on (lane X, `23bc7a0`, `d85715b`) | `tests/webcortex/test_wave2_review_fixes.py::test_an_arbitration_record_that_cannot_be_written_never_costs_the_rest_of_the_pass` |

**The measurement review** (review2, lanes L, F and U):

| Finding | Severity | Fix | Test |
|---|---|---|---|
| H1: every recorded arbitration was spurious | high | recorded only when the refused subject is in the timer's curiosity pool as the kind it launches and budget is left, at both arbitration points (lane Y, `fb19aae`, `9b53aa0`); re-measured: 0 in every configuration | `tests/webcortex/test_integration_arbitration.py::test_a_subject_outside_the_timers_curiosity_pool_records_no_arbitration`, `::test_no_budget_left_for_curiosity_records_no_arbitration`, `::test_a_refresh_that_takes_a_subject_when_no_budget_is_left_records_no_arbitration`, `::test_a_real_conflict_between_the_recheck_and_curiosity_records_exactly_one_arbitration` (positive control) |
| H2: "integrated" forced every Wave 1 setting off inside the families' cases | high | only the links' settings and the records are forced (lane Y, `b8c3174`); re-run: retention all retained, adversarial like the plain run | `tests/acceptance/test_genesis_integration_bench.py::test_the_integrated_mechanism_forces_only_its_links_and_keeps_what_each_case_staged` |
| H3: one random stream for the world and the outcomes | high | truth changes keyed by world, seed and step, outcomes by world, seed, step and subject; lane K's opt-in `--decoupled` draw (lane Y, `b8c3174`); lane L re-measured (`genesis_integration.json`), lane K re-run (`genesis_knowledge_decoupled.json`) | `tests/acceptance/test_genesis_integration_bench.py::test_every_configuration_meets_the_same_world_under_the_decoupled_draw`, `tests/acceptance/test_genesis_knowledge_bench.py::test_the_decoupled_draw_keys_each_outcome_by_step_and_subject_and_leaves_the_recorded_draw_alone`, `::test_the_decoupled_strategy_draw_offers_both_policies_the_same_candidates` |
| H4: lane F's decay without references | high | snapshot, blind round-robin (majority; perfect reads) and oracle from the world generator alone (lane Y, `f15bef1`) | `tests/acceptance/test_learning_cycles_bench.py::test_the_references_come_from_the_world_generator_alone`, `::test_a_look_teaches_the_blind_reference_its_majority_and_a_tie_teaches_nothing` |
| M1: "no evidence" for mechanisms that never acted | medium | per-run activity counts in both harnesses; the wording "not exercised by this world" / "cannot affect this metric by construction" (lane Y, `b8c3174`, `f15bef1`) | `tests/acceptance/test_genesis_integration_bench.py::test_each_run_counts_what_the_links_did`, `tests/acceptance/test_learning_cycles_bench.py::test_each_run_counts_what_the_mechanisms_did_and_whether_it_changed_a_launch_or_a_held_item` |
| M2: a failed salience record ordered "by salience" | medium | the priority order stands unless every score is a finite number (lane Y, `521ddd9`) | `tests/webcortex/test_integration_arbitration.py::test_salience_order_falls_back_to_priority_unless_every_score_is_finite`; with lane U's and lane X's guards: `::test_a_failed_salience_record_or_order_leaves_the_priority_order_with_attention_on` |
| M3: the smallest gain each rule can see was not stated | medium | `verdict_resolution` in both evidence files; stated beside every verdict (lane Y) | — (evidence and wording) |
| M4: lane F's loop is the timer's pass only | medium | said in `cycles.py`, the assessment and the evidence's `not_run` (lane Y, `f15bef1`) | — (wording) |
| M5: stale hashes and counts after the cherry-picks | medium | a lane → `genesis-wave2` commit map in both evidence files, ADR-056 and the assessment (lane Y, `d8dd7d7`); every hash the evidence records checked to be in this history (reconciliation) | — (evidence) |
| L1: staging ran before the configuration | low | settings applied before staging (lane Y, `b8c3174`) | `tests/acceptance/test_genesis_integration_bench.py::test_the_configuration_is_applied_before_the_staging` |
| L2: "best single link" picked on held-out | low | chosen on development (`alone:recheck`); held-out ties listed (lane Y, `b8c3174`) | — (evidence) |
| L3: the scenario claim stronger than its spread | low | "not better than three equal futures" with each per-seed range named (alone 0.555–0.801, integrated 0.574–0.824) | — (wording) |
| L4: `salience_never_reorders_admission` against ADR-056 | low | docstring and documents qualified: with `salience_attention` off (lane Y, reconciliation) | — (wording) |
| L5: seed numbers overlap | low | said in the assessment §2 (no leak: streams keyed by world name) | — (wording) |
| L6: +0.074 | low | +0.073 | — (wording) |
| L7: persistence mixes censoring with effect | low | episodes open at the end beside persistence (lane Y, `f15bef1`) | — (evidence) |

**The check of the fixes** (review2b, lanes X and Y; fixed in the reconciliation, `gen2-rec`):

| Finding | Severity | Fix | Test |
|---|---|---|---|
| N1: a new session on another hub cleared a follow whose follower was still retrying its claim; the answer was then never given | low | the pick-up leaves a follow whose follower hub lives to it for `min(FOLLOW_MAX, RESUME_MAX_AGE)` after the task finished (`a0d9c0c`) | `tests/uat/test_uat_voice_follow_faults.py::test_a_new_session_on_another_hub_never_takes_a_follow_whose_follower_still_claims` (both orders); `tests/voice/test_follow_restart.py::test_a_task_that_finished_before_the_previous_session_ended_is_not_replayed` (updated: the mark is left to the living follower, cleared after `FOLLOW_MAX`, never replayed) |
| N2: the person saw nothing while the claim was retried | low | one plain notice in the asking session on the first failed attempt, never per retry (`a0d9c0c`) | `tests/uat/test_uat_voice_follow_faults.py::test_the_person_hears_once_that_the_answer_is_coming_while_the_claim_is_retried` |
| N3: in queue mode a queued refresh did not count against `max_tasks` (lane Y's open item; it predates Wave 2) | low | a refresh the timer queues, or one a worker claimed first, counts against the pass's budget (`d4c6dc0`, `a1f60d1`) | `tests/webcortex/test_scheduler.py::test_a_refresh_queued_by_the_timer_counts_against_the_pass_budget`, `::test_a_refresh_a_worker_claimed_first_counts_against_the_pass_budget`, `tests/webcortex/test_integration_arbitration.py::test_a_queued_refresh_leaves_no_budget_so_the_recheck_takes_no_arbitration` |
| N4: since the follow is written before the reply, a database error hid the acknowledgement; a failure before the follower started leaked its slot; a reply that follows and forgets lost the follow | low | the write's failure is logged and the reply shown; try/finally gives back slot and count; the follow is written again to the new session unless the forget took the task (`a0d9c0c`) | `tests/uat/test_uat_voice_follow_faults.py::test_a_failing_follow_write_still_shows_the_acknowledgement`, `::test_a_failure_before_the_follower_starts_gives_back_its_slot_and_count`, `::test_a_reply_that_follows_a_task_and_forgets_the_conversation` |
| N5: "type only" logs lost diagnosis; the forecast gate was silent after the first failure of a type | low | `errors.failure_kind`: the type with SQLite's error name or the errno name, never the message; the forecast gate logs per kind and again at 10, 100, … repeats; the curiosity warning names the knowledge id (`d95a604`) | `tests/webcortex/test_wave2_review_fixes.py::test_a_forecast_failure_is_logged_by_its_kind_with_its_repeats_and_no_subject`, `::test_a_failing_learning_progress_read_names_the_knowledge_item_never_its_subject`; the marker case of `test_uat_fault_injection.py` still passes |
| N6: the loop's share of the blind schedule's gain ignored coverage; the scenario per-seed range did not say whose it was | low | the coverage-adjusted reading (the reviewer's, about 0.57 and 0.66) beside 0.49 and 0.55, and coverage among the reasons; each range named (documents, this commit) | — (wording) |
| N7–N9 | info | not changed: a line's text cut at 300 characters has no marker (ids lead every line and are in its refs); two investigations of one subject in one step draw the same decoupled outcome (rare under the cooldown); the L1 test reads log messages, not tracebacks | — |

**Where the fix lanes met.** Lane X extended lane U's fault injection to lane L's arbitration record and staged an
item the re-check and curiosity both wanted; lane Y then made the record fire only for a real conflict (the subject
in the timer's curiosity pool as the kind curiosity launches, with budget left). The staged item's "still holds"
prediction was due, so curiosity left it to the due refresh (kind `prediction`, never in that pool): four fault cases
found `_arbitrated` never reached. The reconciliation stages that prediction as not yet due, so curiosity ranks the
subject as stale knowledge and the fault is reached; no assertion changed (`f5753e8`). Two more places where the
lanes meet got tests: a re-check that fails after queuing (lane X's read-back) with lane Y's condition, and a failed
salience record or order with `salience_attention` on (lane U's and lane X's guards with lane Y's fallback). Lane Y's
open item (queue mode) was real and older than Wave 2 (N3, above).

**What stays open from the reviews:** two real processes for the voice follow-up (a second hub in one process stood
in); the retention and permissions of log lines in journald (not checked); the voice retry is not ended by the owner's
stop of a session (by design: the answer goes to the transcript; closing the hub ends it); `nsf web bench` not run.

## 5. Validation

Run on `18a48ae`, 2026-11-26, on this host (shared with other work; nothing below is a measurement of the DGX Spark,
the open web or a live answer model).

| Check | Result |
|---|---|
| Quality gate (`bash scripts/check.sh`: ruff, the non-e2e suite, the JS checks, the numerical reproduction) | 5,861 passed, 22 skipped, 35 subtests; ALL CHECKS PASSED |
| Browser suite (`scripts/e2e.sh`) | 209 passed, 1 skipped |
| `nsf acceptance --all` | pass |
| Sentence guard against `307b907` (`scripts/guard_differential.py compare`) | 1,937 compared: 1,688 identical, 232 stricter, 17 loosened, 0 different |
| Two accounts (`scripts/two_account_check.py`, real uids in a user namespace) | 55 of 55 |
| Offline research benchmark, every Wave 1 and Wave 2 setting off, and as shipped | sha256 `68731d7235cb…` both: the digest of Wave 0 and Wave 1 |
| Cognitive benchmark, development and held-out (`scripts/bench_cognition.py run`) | retention: all retained on both; adversarial: 28 of 28 scored cases pass on both (13 original, 4 added in Wave 1, 1 restored, 10 added in Wave 2), 5 directive items listed as not staged |

A last independent reader then checked the reconciliation's fixes (`484555a..18a48ae`): all verified by fresh runs,
probes and two mutation checks; one low finding and one note were fixed in the commit after this validation, which
changed two product lines and their tests and nothing else in the code:

| Finding | Fix | Test |
|---|---|---|
| R1: the notice said once while a delivery is retried read "Your research answer is ready" for a task that was cancelled or failed | the notice promises an answer only for a completed or partial task; otherwise it says the research has ended | `tests/test_failure_kind.py::test_the_retry_notice_promises_an_answer_only_where_there_is_one`; `tests/uat/test_uat_voice_follow_faults.py::test_the_person_hears_once_that_the_answer_is_coming_while_the_claim_is_retried` |
| D3: the helper that names a failure for a log line could itself raise for an exception whose attribute raises | it falls back to the type name | `tests/test_failure_kind.py::test_it_cannot_raise_inside_a_handler_that_must_not_fail` |

Noted and left: a hub closed inside a process that still runs reads as alive to another process, so its pending
answer is cleared only after the 3-hour limit (nothing is lost compared with before); when the person's next
session starts on another process first, the answer is written into the asking session's transcript, not spoken
live there.

**The recorded digest is this host's.** CI's first run on the Wave 2 pull request failed one test,
`tests/uat/test_uat_wave0_equivalence.py::test_the_offline_research_benchmark_equals_wave0_at_the_shipped_defaults_and_with_every_setting_off`:
on CI's x86_64 runner the offline research benchmark's digest is `426abca5…`, not the `68731d72…` recorded on the
development host (aarch64). The test had pinned the recorded value, which no earlier CI run had checked. It now
asserts, in any environment, that the shipped defaults give the same digest as every setting off, and compares
the recorded value only on the architecture it was recorded on. Why the two environments differ was **not
investigated**; every "same digest as Wave 0" in these documents is a statement about runs on the development
host.

**Added in Wave 3.** CI's second run on the Wave 2 pull request failed
`tests/e2e/test_console.py::test_keyboard_reaches_every_enabled_button[overview]` once of two runs: the overview
loads twice in quick succession and the test's settled check could pass in between; the test was hardened in
`08cced0` and the cause was found in Wave 3 by lane P ([WAVE3.md](WAVE3.md) §3.7). The digest difference between the
two hosts is the browser's file path inside the hashed metrics (lane P), not a difference in behaviour.

**Wave 2 took no look at the withheld family:** no lane changed a mechanism behind its four interfaces. The ledger
still holds three lines.

Not run here: anything on the open web, a live answer model, real hardware, a real second account, two real
processes for the voice follow-up, or systemd.

## 6. What stays open

| Item | Source |
|---|---|
| **No gain from integration beyond the spread**; the integrated configuration is not recommended (+73 % wall time) | lane L, lane Y, ADR-056 |
| **Knowledge decays in every configuration**, partly by the world's design; the loop recovers about half of what a same-budget blind schedule recovers (0.49 and 0.55; about 0.57 and 0.66 by the check of the fixes' reading, which allows for the 86 and 83 % of subjects the owner's research covers); a contested refresh never revises a stale item without the owner's answer; the owner's ruling on automatic supersession under a recency + independence rule is the main route lane F identified | lane F; benchmark review H4; review2b N6 |
| Scenario records are not better than three equal futures on held-out (0.681 alone, per seed 0.555–0.801; 0.689 integrated, per seed 0.574–0.824; against 0.667); they stay off | lane L, lane Y; benchmark review L3 |
| Reflections are kept without a cap (~3 KB each; about 156 KB a year at the weekly default): no retention bound added, because a reflection is also the record of the actions the system took and forgetting is the owner's; its size is bounded now | lane F; lane X |
| Open re-check marks grow with unresolved contradictions (19 and 43 after 60 development cycles; bounded at 500) | lane F |
| The idle re-check prefers old unchanged items (7 of 72 caught a change against 19 of 79 due refreshes, development) | lane F |
| The individual cannot recognise its own text republished on the web; two independent echo pages would read as corroboration (not staged) | lane F |
| A scenario settles one timer pass (15 minutes) after its prediction; the ×1.5 importance is keyed by the failed item's id; core forecasts are never compared with what followed | lane L |
| `salience_never_reorders_admission` under "integrated" compares the record on against the record on (the links force it); its observation is unchanged | lane Y (H2's residual) |
| Lane U's rollback covers switches, not the numeric bounds (`imagine_max_*`, `recheck_*`, `reflection_interval_days`, the broadcast threshold) | lane U |
| The voice follow-up across **two real processes** was not run (a second hub in one process stood in); the retry is not ended by the owner's stop of a session (closing the hub ends it) | lane X; review2b |
| The retention and permissions of log lines in journald were not checked (the lines carry types, error names and ids only) | security review L1; review2b N5 |
| **Unverified:** the open web, a live answer model, real hardware (GPU, memory pressure), real speech and phones, two real processes, systemd, a real second account, a real worker running the loop for days | all lanes; [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) §7 |
| Owner decisions pending: automatic supersession; `predictive_regulation`, `idle_recheck`, `adaptive_depth`, `information-v1`, `curiosity_learning_progress`, `salience_attention` | [FINAL_EVALUATION.md](FINAL_EVALUATION.md) §5 |

Fixed and no longer open (§4): the false arbitration record, the reflection section bound, `strategies.active`, the
voice delivery under a failing claim and its retry, subject text in logs, lane L's settings in lane U's fault and
rollback tests, the failed salience record ordered as score 0, the load-sensitive voice test, the queue-mode budget.

## 7. Backing each piece out

| Piece | How to back it out | What stays |
|---|---|---|
| The arbitration record | `nsf web config --set arbitration_record=false` | the decision events already written (a task forget removes them) |
| Salience attention | leave off (`nsf web config --set salience_attention=false`) | — |
| `doing.cycle` | revert lane L's commit `a7981e0` before the merge into `main` (the same commit carries the arbitration record and salience attention) | — |
| Lane U's guards | revert `314ab80` and `9a7796f` before the merge (the failures they catch come back) | — |
| The benchmarks and tests (integrated family, learning cycles, UAT, `added_wave2` cases) | revert their commits; nothing in the product reads them | — |
| Any Wave 2 code | revert its commits on `genesis-wave2` before the merge into `main` | no schema change, so nothing in a file |
