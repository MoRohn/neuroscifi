# Recursive knowledge engine assessment (deliverable F)

Status: 2026-11-25, Wave 2 lane F (dated Wave 3 and Wave 4 sections, §10 and §11, added 2026-11-26), branch `gen2-f` from `main` at `3559069`; integrated on `genesis-wave2`
(`4d8a60c`); revised by Wave 2 lane Y (branch `gen2-y`) after the benchmark-integrity review: references for the
decay (§3), what each mechanism did (§4), the smallest gain the rule can see (§4), the loop's scope (§2, §8), the
commits on the integrated tree (§2) and the cognitive benchmark on that tree (§6). Directive §7 ("recursive knowledge
evolution") and §13 F. Evidence: `docs/quality/evidence/genesis_learning_cycles.json` (regenerated on `gen2-y`: both
splits re-run, 0 of 3,600 held-out and 2,700 development recorded per-seed values differ). Nothing here is a claim of
consciousness, awareness or general intelligence; the names describe functions.

**Result.** In a simulated world of fictional software releases, run through the real extraction, clustering,
consolidation, prediction and timer code for 14 daily cycles, the knowledge loop as it exists (the timer's pass
only: refreshes of due "still holds" predictions and the timer's curiosity, every Wave 1 setting off; the worker's
idle investigation was not run) keeps the individual's knowledge more correct than no loop at all: held-out, the
share of subjects whose newest held item states the current version is 0.574 → 0.631 and 0.497 → 0.571 in the two
worlds, better on 8 of 8 paired seeds, at about twice the pages and wall time. **Knowledge still decays over the
cycles in every configuration** (0.88 → 0.44 and 0.85 → 0.46 from cycle 0 to cycle 14 with the loop; 0.38 and 0.33
without it), so this is slower decay, not persistent improvement. **Part of the decay is the world's design:** the
truth changes 1.75 and 3.3 times per cycle against a budget of 2 and 3 tasks, and 14 % and 17 % of subjects are never
covered by the owner's research in any configuration, so no budget-bound loop can hold its cycle-0 level. For
reference, from the same worlds: a blind round-robin of the same budget that reads each look's majority value holds
0.79 and 0.71 on average (0.67 and 0.54 at cycle 14); an oracle that refreshes exactly the changed subjects holds 0.96
and 0.93; a perfect snapshot never refreshed 0.67 and 0.57. The loop's gain over no loop (+0.057, +0.073) is about
half (0.49 and 0.55) of what the blind schedule gains over the snapshot (+0.115, +0.134). Those references hold every
subject from its birth, while the owner's research covers 86 % and 83 % of subjects in every configuration, so part of
the blind schedule's gain is out of any product loop's reach; allowing for coverage, the check of the fixes
(review2b N6) reads the loop's share as about 0.57 and 0.66 (the reviewer's computation, not in the evidence file).
**The rest of the shortfall is the uncovered subjects and the product's:** contested refreshes that never revise,
and the due order (§5). **None of lane K's six mechanisms shows a
held-out gain beyond the baseline's spread** (the pre-registered rule, §4; with 4 seeds a gain below 0.105 and 0.057
cannot pass it, and "no evidence" does not exclude a gain of that size). Five of them are **not exercised by this
world**: each acted (detail rows, premise rows and marks, abandonments, 14 reflections, 14 strategy rankings per run)
but none ever changed what was investigated or what was held, because nothing here consumes what it wrote or there
was one research mode to rank. Only the idle re-check changed what the loop did (every cycle); it moves the primary
metric −0.019 and −0.004 (better on 2 of 8 paired seeds; paired noise 0.026 and 0.033). In 120 held-out runs nothing
the individual's own outputs asserted and no version only a syndicated wrong story stated became knowledge, no
confidence rose from copies of one source, and nothing learned and still true was lost. **No default and no product
behaviour changed** (the only product file touched is `background.py`, where one entry names the harness as a
benchmark runner of the timer).

## 1. What exists: directive §7 mapped to the code

"Built" means code that runs at the shipped defaults or behind a setting; "partial" names what is missing; "not
built" says why. Levels 4 and 5 of §7.6 are not built by ruling (the owner's authorisation is required and was not
given).

| §7 | Item | State | Where |
|---|---|---|---|
| 7.1 gap discovery | missing facts, stale information, contradictions, low-confidence predictions | built | `curiosity.rank` (gaps, staleness against the freshness TTL, contested subjects, prediction uncertainty); `autonomy.admission` ranks by expected information gain × the kind's weight (`WEIGHTS`: goal, contradiction, hypothesis, prediction uncertainty, source change, stale, gap) and refuses below `min_gain` (ADR-050) |
| | unsupported assumptions, repeated reasoning failures | partial: recorded, not acted on | hypothesis assumptions (`hypothesis_fields`, on, display); reflection's "failed" section (guard refusals, investigations that changed nothing; `reflection`, off) |
| | weak causal explanations, missing conceptual links, incomplete procedures | not built | no detector exists; the causal engine (ADR-052) answers on declared models only |
| | prioritised by value for the owner's goals | partial | the goal weight and owner monitors (importance 1.2) in the ranking; no model of the owner's goals beyond those |
| 7.2 hypotheses | supporting observations, counter-evidence, assumptions, confidence, testability, expected gain, cost, relevance | built (records) | `evolution.py`: hypotheses from held clusters with a testable prediction and deadline; lane K's fields (`web_hypothesis_details`: assumptions, counter-evidence, expected gain, cost, revisions); relevance through the admission weights |
| | competing explanations | partial | `evolution.compete(key)` (dampened likelihoods, "other" as a member; display only); alternatives after a prediction error are templates (speculation, labelled) |
| | evidence-based revision | built | `evolution.observe`: supported / refuted / revised from settled predictions; `abandoned` with `hypothesis_abandon` (off) |
| 7.3 construction | source attribution, temporal validity, uncertainty, contradiction handling | built | `consolidate.run`: versioned knowledge with sources, supporting claims, validity interval and freshness class, Beta posterior; a newer series value supersedes (`temporal.supersedes`); a conflict is recorded and the owner asked (ADR-026, ADR-038); independence groups join copies and syndication (`claims.independence_groups`) |
| | evidence / inference / prediction / simulation / speculation at the representation level | built | `provenance.py`; the quarantined imagined store (ADR-049) |
| | knowledge graph evolution | partial | typed edges (`web_claim_links`, entities); no graph learning over them |
| | semantic abstraction, concept hierarchies, cross-domain associations, causal extraction, procedural derivation | not built | nothing derives these from knowledge |
| 7.4 learning to learn | strategy selection | built, inactive | `strategies.py`: Thompson sampling over research modes from external outcomes, in force only after its gate (held-out, retention, reward hacking, one withheld run); the gate fails closed (no withheld world for it; WAVE1 §9) |
| | curiosity by learning progress; an information-seeking decision rule | built, off / opt-in | `curiosity_learning_progress` (off); `executive_rule information-v1` (opt-in; showed nothing on the withheld world, WAVE1 §8) |
| | retrieval, decomposition, tool selection, simulation depth, model routing, verification policy, memory prioritisation, planning, learning schedules | not built as learned policies | fixed rules; `verification_escalation` and `adaptive_depth` are fixed switches (off), not learned |
| | transfer to new tasks measured | partial | the withheld family (four interfaces; no knowledge-loop or strategy world) and this experiment's held-out worlds |
| 7.5 consolidation and replay | episodic → semantic | built | a task's clusters → knowledge (web); expiring evidence → consolidated readings (ADR-044, instance) |
| | experience prioritisation, retrieval strengthening, temporal reconciliation | built | the idle re-check ranks by importance × staleness (`recheck.py`, off); a re-read refreshes an item (`_strengthen`); versions and validity intervals |
| | stability / plasticity, interference | partial | old versions are kept as superseded rows; a contested refresh never revises (§5) |
| | adaptive forgetting | not built, by design | forgetting is the owner's act (ADR-048); nothing forgets on its own |
| | generative replay | not built, by ruling | training on self-generated content is on the do-not-build list (model collapse; ROADMAP §6) |
| 7.6 levels | 1 context adaptation | built | retrieval of held knowledge and memory into answers (existing) |
| | 2 memory adaptation | built | consolidation, versioning, refreshes; measured here |
| | 3 procedural adaptation | built, none active by default | strategy selection (gated), rule versions (`information-v1`), learning progress (off) |
| | 4 model adaptation, 5 architecture optimisation | **not built, by ruling** | `evolution.model_adaptation_guard` refuses; the improvement ledger refuses levels 4 and 5 (`strategies.OUT_OF_SCOPE`) |
| 7.7 verification | what improved, evidence, generalisation, regressions, cost, reproducibility, retain / revise / revert | built for strategy selection; this experiment for the loop | `improvement_ledger` (append-only, no free text) and `strategies.gate`; frozen splits, the withheld family and its ledger; adversarial cases against self-confirmation and reward hacking (`no_self_confirmation_without_evidence`, `self_reported_success_earns_no_reward`) |

## 2. The experiment

`scripts/bench_learning_cycles.py` (harness `src/neuroscifi/acceptance/cognition/cycles.py`, worlds
`src/neuroscifi/acceptance/cognition/cycles_splits.py`). Offline, deterministic per seed, no model call, no connection.

**Order (pre-registration).** Commits are named as on `genesis-wave2`, with lane F's branch hash in brackets. The
worlds and their development and held-out seeds were committed first (`22f1add` [`b3de5d2`]); four outlet wordings
that consolidation reads as not self-contained (a pronoun or "this ...") were replaced after one development run
(`4ac4c98` [`7df0136`]), before the harness existed in the history; the harness with its verdict rule (§4) was
committed next (`fd70c27` [`35845f8`]), then the held-out split was run once. No mechanism, setting default or
product file changed at any point. Two harness corrections followed, both disclosed: the no-loop reference had
recorded the owner's autonomy enable, which turns autonomous research on, so it equalled the baseline (fixed in
`79140ac` [`e24f4d0`], test added); and the per-seed rows lacked the counts behind their shares (`840b4ca`
[`8f429ec`]). Both splits were re-run on that commit: 0 of 10,363 held-out values and 0 of 9,073 development values
differ from the earlier runs apart from wall time and file size (an SQLite file's size varies by a page or two
between identical runs). After the review (lane Y, `f15bef1`), the harness gained the references (§3) and the
activity counts (§4); both splits were re-run on that commit, which the evidence records: 0 of 3,600 held-out and
2,700 development recorded per-seed values differ (wall time and file size excepted). This held-out re-run is
disclosed: the harness gained counts and references; the worlds, seeds, rule and product did not change. The numbers
below are that run's.

**Seeds** (review L5): the development seeds 41 and 42 are numerically equal to two of `splits.py`'s development
seeds; nothing leaks, because every stream here is keyed by the world's name (`cycles:{name}:{seed}`). The held-out
seeds (141-144) are disjoint from `splits.py`, `knowledge_splits.py`, the wide seeds and lane L's seeds.

**The world** (written by `generate(spec, seed)` before the individual runs; nothing the individual produces is ever
truth). Fictional projects each have a "latest release" 1.<minor>.0 that changes with a fixed probability per cycle;
new projects appear and the owner researches them; six independent outlets state the version in their own words;
an outlet may lag (it still shows the previous version, dated when that was true); a wire service sometimes publishes
a wrong version (7.x) that two to four other hosts copy with an attribution line; some looks find only one outlet's
story and its copies (one independent source on several hosts); the individual's own outputs assert versions no
source states (9.x) every cycle: a record in its imagined store, the model-written answer stored with a task, a
reflection summary by a stub local model (with reflection on), and from the next cycle an echo page quoting it. The
owner forgets the wire's site and one honest outlet once (cycle 8 or 9). A simulated owner accepts a seeded share of
the new items it researched and answers nothing else.

| | Development | Held-out |
|---|---|---|
| seeds | 41, 42, 43 | 141, 142, 143, 144 |
| worlds | dev-steady (14 subjects, +1 per cycle, change 0.06, budget 2), dev-churn (10, +2, 0.12, 3); 12 cycles | ho-sparse (18 subjects, +1 per cycle, change 0.08, budget 2, lag 0.2, wire 0.08), ho-busy (12, +2, 0.15, 3, lag 0.15, two outlets per look); 14 cycles |

**One cycle** (one simulated day): the owner's forgets; the individual's own outputs planted; the owner researches
the new subjects; the real timer (`scheduler.tick`: reflection, the idle re-check, due refreshes, curiosity, all
through `autonomy.admission`, inside `daily_autonomous.tasks` = the budget); every queued task read from the world's
pages through `claims.extract_claims`, `claims.stamp_origins`, `claims.cluster`, `claims.score`, `consolidate.run`,
`orchestrator._predict` and the after-run hooks; predictions settle at the next timer pass. **Substituted:** search,
fetching and answer synthesis (the world hands a task its pages), the host's resource gate (held open), the owner.
**Not run:** the worker's idle investigation (`autonomy.idle`), which in the product shares the daily allowance with
the timer: "the loop" in this document is the timer's pass only.

**Measured per cycle against the world's truth:** accuracy (share of held items true now), coverage, correct (share
of subjects whose newest held item states the current version; the primary metric, averaged over cycles 1..N),
changes caught and the cycles it took, error persistence (cycles a wrong newest item survives), retention (subjects
held correctly at cycle 3 whose truth never changed after it: still held correctly at the end?), calibration (Brier of
each held item's stated confidence against "true now", pooled over items and cycles, with the constant reference),
self-generated and syndicated-wrong versions ever admitted as knowledge, confidence rises after a look that held one
origin only, cost (tasks, pages, wall time, file size).

**Configurations:** `reference:no-loop` (autonomous research off: knowledge only from the owner's research),
`baseline` (every Wave 1 setting off: today's loop), each lane K mechanism alone (`hypothesis_fields`,
`premise_recheck`, `idle_recheck`, `hypothesis_abandon`, `reflection` with its model summary and the shortest interval
the owner may set, 1 day, `strategy_selection` in force without passing its gate), all six, and all six with one off.

**What it cannot show.** That real projects change, lag, syndicate or err at these rates; anything about the open
web, real search, a real answer model or a real owner (this owner never answers a contradiction question, which is
what blocks most revisions, §5); horizons beyond 14 held-out cycles (60-cycle runs are development only); strategy
selection (every task here is a fact verification: one research mode, nothing to choose between; lane K's own
strategy world measures it); one page per outlet per look, one sentence per page, one kind of fact.

## 3. Held-out results

Mean ± sd over the 4 seeds; cycles 1–14. Five mechanisms alone (`hypothesis_fields`, `premise_recheck`,
`hypothesis_abandon`, `reflection`, `strategy_selection`) give values identical to the baseline on every knowledge
metric of every seed; they differ only in their own records and cost (below).

| ho-sparse | no loop | baseline (today's loop) | idle re-check alone | all six |
|---|---|---|---|---|
| **correct** (primary) | 0.574 ± 0.100 | **0.631 ± 0.105** | 0.611 ± 0.087 | 0.625 ± 0.073 |
| accuracy of held items | 0.660 ± 0.054 | 0.727 ± 0.059 | 0.706 ± 0.035 | 0.723 ± 0.026 |
| coverage | 0.861 | 0.861 | 0.861 | 0.861 |
| changes caught (of 98 over the seeds) | 0 | 18 (0.191 ± 0.051) | 21 (0.218 ± 0.113) | 18 (0.188 ± 0.101) |
| error persistence, cycles (censored at cycle 14) | 7.56 ± 1.07 | 5.72 ± 0.97 | 6.44 ± 0.92 | 6.12 ± 1.02 |
| error episodes still open at cycle 14, of all episodes | 15.0 of 15.0 | 13.0 of 16.0 | 12.25 of 15.25 | 12.25 of 15.25 |
| Brier (constant reference) | 0.242 (0.224) | 0.186 (0.199) | 0.204 (0.209) | 0.184 (0.203) |
| pages / wall s / file MB at the end | 106 / 0.20 / 1.01 | 208 / 0.42 / 1.38 | 213 / 0.54 / 1.39 | 213 / 0.57 / 1.53 |

| ho-busy | no loop | baseline | idle re-check alone | all six |
|---|---|---|---|---|
| **correct** | 0.497 ± 0.082 | **0.571 ± 0.057** | 0.567 ± 0.088 | 0.580 ± 0.083 |
| accuracy of held items | 0.593 ± 0.080 | 0.683 ± 0.050 | 0.677 ± 0.087 | 0.693 ± 0.081 |
| coverage | 0.831 | 0.831 | 0.831 | 0.831 |
| changes caught (of 185) | 0 | 56 (0.295 ± 0.145) | 49 (0.262 ± 0.100) | 55 (0.294 ± 0.088) |
| error persistence, cycles (censored at cycle 14) | 7.10 ± 1.07 | 4.60 ± 0.42 | 4.85 ± 1.04 | 4.52 ± 0.75 |
| error episodes still open at cycle 14, of all episodes | 19.5 of 19.5 | 14.25 of 23.0 | 15.75 of 22.5 | 15.5 of 23.0 |
| Brier (constant reference) | 0.269 (0.240) | 0.208 (0.219) | 0.213 (0.219) | 0.208 (0.215) |
| pages / wall s / file MB | 102 / 0.17 / 1.01 | 191 / 0.39 / 1.40 | 196 / 0.50 / 1.41 | 194 / 0.54 / 1.54 |

Error persistence is the mean length of an episode in which the newest held item is wrong, cut off at cycle 14.
Without the loop every episode is still open at cycle 14, so the difference to the loop (−1.8 and −2.5 cycles) mixes
the censoring with the effect; the count of episodes still open at the end (row below it) is the plainer measure.

**The loop against no loop** (paired, held-out): correct +0.057 and +0.073, accuracy +0.067 and +0.090, error
persistence −1.8 and −2.5 cycles (censored, above), episodes open at the end −2.0 and −5.25, Brier −0.056 and −0.061,
correct, accuracy and Brier on 4 of 4 seeds in both worlds; cost +102 and +90 pages, +0.22 s and +0.37 / +0.38 MB per
run. Development agrees (+0.048, +0.086; 3 of 3 seeds each). Without the loop the stated confidence is worse than a
constant (Brier above its reference); with it, slightly better.

**References from the same worlds** (review H4; `cycles.references`, computed from `cycles_splits.generate` alone
with the run's budget, no product code; held-out, mean over the 4 seeds). Each reference holds one value per subject
and starts from what the owner's research would give it at birth; scored like `correct`:

| Held-out | ho-sparse: mean over cycles 1-14 (at cycle 14) | ho-busy: mean (at cycle 14) |
|---|---|---|
| the world: truth changes per cycle, against the budget | 1.75 against 2 tasks | 3.30 against 3 tasks |
| subjects the owner's research never covers (every configuration) | 14 % (coverage 0.861) | 17 % (coverage 0.831) |
| perfect snapshot at birth, never refreshed | 0.670 (0.44) | 0.574 (0.38) |
| blind round-robin, same budget, each look's majority value (a tie teaches nothing) | 0.786 (0.67) | 0.708 (0.54) |
| blind round-robin, same budget, perfect reads | 0.801 (0.71) | 0.754 (0.62) |
| oracle: refreshes, perfectly, up to the budget of the subjects that changed | 0.957 (0.91) | 0.928 (0.80) |
| **product, no loop** (measured) | 0.574 (0.38) | 0.497 (0.33) |
| **product, the loop** (measured) | 0.631 (0.44) | 0.571 (0.46) |

Reading (the review's): even a blind schedule with perfect reads decays, so the decay is partly the world's design.
The loop gains +0.057 and +0.073 over no loop; the blind round-robin gains +0.115 and +0.134 over the snapshot; so
the loop recovers about half (0.49 and 0.55) of what a blind schedule of the same budget recovers. Coverage (the
check of the fixes, review2b N6): the references hold every subject, while the owner's research covers 0.861 and
0.831 of them, so part of the blind schedule's gain is out of any product loop's reach; scaled by coverage the loop's
share is about 0.57 and 0.66 (the reviewer's computation). The rest of the shortfall is the 14 % and 17 % of subjects
never covered and the product's: contested refreshes that never revise without the owner's answer, and the due order
(§5). The product's
no-loop level is below the snapshot because 14 % and 17 % of subjects are never covered and the owner's research is
read through the real extraction, not perfectly. The reviewer's own probe gave 0.724 for the majority-reading
round-robin in ho-busy, taking the first value of a tie; here a tie (one honest outlet against one lagging one, the
common case with two outlets per look) teaches nothing, which gives 0.708. Development: snapshot 0.738 and 0.611,
majority round-robin 0.866 and 0.777, oracle 0.993 and 0.982 (dev-steady, dev-churn; evidence `references`).

**Over the cycles** (correct, mean over seeds, at cycles 0 / 1 / 4 / 7 / 10 / 14):

| | ho-sparse | ho-busy |
|---|---|---|
| no loop | 0.875 / 0.868 / 0.705 / 0.550 / 0.455 / 0.375 | 0.854 / 0.732 / 0.588 / 0.510 / 0.391 / 0.331 |
| baseline | 0.875 / 0.882 / 0.727 / 0.610 / 0.554 / 0.438 | 0.854 / 0.732 / 0.625 / 0.587 / 0.508 / 0.463 |
| all six | 0.875 / 0.882 / 0.727 / 0.600 / 0.536 / 0.461 | 0.854 / 0.750 / 0.662 / 0.625 / 0.484 / 0.431 |
| *reference:* snapshot, never refreshed | 1.000 / 0.987 / 0.841 / 0.640 / 0.518 / 0.438 | 1.000 / 0.857 / 0.650 / 0.577 / 0.484 / 0.381 |
| *reference:* blind round-robin, majority | 1.000 / 0.987 / 0.864 / 0.760 / 0.679 / 0.672 | 1.000 / 0.875 / 0.750 / 0.731 / 0.656 / 0.544 |
| *reference:* oracle, same budget | 1.000 / 1.000 / 0.977 / 0.930 / 0.929 / 0.906 | 1.000 / 1.000 / 0.938 / 0.981 / 0.898 / 0.800 |

The loop does not improve knowledge cycle over cycle; it slows its decay, and so does every reference except the
oracle, which also decays when more subjects change in a cycle than its budget covers. On 60-cycle development runs
(one seed per world) correct falls to 0.26 and 0.12 with the loop, 0.18 and 0.09 without it (no reference was
computed for those runs).

**Cost of the mechanisms** (held-out, both worlds): the idle re-check adds ~0.11 s per run and turns 14 of the
~28–36 autonomous tasks per run from due refreshes and curiosity into re-checks; all six add ~0.16 s and 0.14 MB per
14 cycles (reflection alone ~0.07–0.08 MB: 14 reports of ~3 KB and their records; premises and marks ~0.03 MB). Open
re-check marks at the end: 14.75 (ho-sparse) and 7.75 (ho-busy) with `premise_recheck` alone, the shipped default; 2.75
and 3.25 with the idle re-check working them off.

## 4. Verdicts

The rule, committed with the harness before the held-out run (`cycles.verdicts`; the roadmap's acceptance rule on
paired seeds): **retain** = (a) alone, the gain over the baseline beats the baseline's sd in every held-out world, (b)
`all` beats the baseline and `all` without the mechanism does not in some world, (c) no subject lost or regressed
beyond the baseline's, (d) no self-generated or syndicated-wrong version admitted and no one-origin confidence rise;
**revise** = (c) and (d) with a positive gain on at least 3 of 4 paired seeds in every world, or (a) in some world;
else **no evidence**.

The rule's output is "no evidence" for all six. That word is right only for a mechanism that changed what the loop
did; a mechanism that acted but never changed what was investigated or what was held is **not exercised by this
world** (review M1). Each run therefore counts what the mechanism did (its own records; strategy selection's
rankings) and the cycles in which what was investigated, or what was held, differs from the baseline's run of the
same world and seed (the world is written whole before the run, so both met the same pages). Held-out, per run, in
ho-sparse and ho-busy:

| Mechanism (default) | Held-out gain in correct (baseline sd), paired seeds better / worse | (a) | (b) | (c) | (d) | What it did; cycles in which it changed what was investigated / held | Reading |
|---|---|---|---|---|---|---|---|
| `hypothesis_fields` (on, record) | 0, 0 (identical) | no | n.a. | yes | yes | 18.25 and 20 detail rows; 0 / 0 | **not exercised by this world** (no consumer of the rows) |
| `premise_recheck` (on, record) | 0, 0 (identical); in `all` its marks add +0.013, +0.014 to the re-check (better on 2, worse on 1 of 4 seeds in ho-sparse; better on 4 of 4 in ho-busy; within the sd) | no | n.a. | yes | yes | 149 and 154 premise rows, 16.25 and 13 marks; 0 / 0 alone | **not exercised alone** (nothing reads a mark without the idle re-check); with it, within the sd |
| `idle_recheck` (off) | −0.019 (0.105), −0.004 (0.057); 1/3 and 1/3 | no | n.a. | yes | yes | 14 re-check tasks; 14 / 9.25 and 14 / 8.75 | **no evidence of a gain** (it changed what the loop did, every cycle) |
| `hypothesis_abandon` (off) | 0, 0 (identical) | no | n.a. | yes | yes | 4.5 and 4.25 hypotheses abandoned; 0 / 0 | **not exercised by this world** (no knowledge consumer) |
| `reflection` (off) | 0, 0 (identical) | no | n.a. | yes | yes | 14 reports; 0 / 0 | **not exercised by this world** (no consumer) |
| `strategy_selection` ("gate", inactive) | 0, 0 (identical) | no | n.a. | yes | yes | 14 rankings, 0 of which changed the order; 0 / 0 | **cannot affect this metric by construction** (one research mode, nothing to rank) |
| all six | −0.006 (0.105), +0.010 (0.057); 1/3 and 2/2 | — | — | yes | yes | 14 / 10 and 14 / 10.5 | — |

(b) is "n.a." because `all` itself does not beat the baseline. On development the idle re-check gave +0.022 (3 of 3
seeds) and −0.009: the development gain did not transfer to held-out.

**What the rule can see** (review M3). Rule (a) needs a paired gain larger than the baseline's between-seed sd in
every world: 0.105 (ho-sparse) and 0.057 (ho-busy). With 4 seeds a gain below that cannot pass it; "no evidence"
does not exclude a gain of that size. The paired noise is much smaller: the sd of the idle re-check's paired
differences is 0.026 and 0.033, of the loop's against no loop 0.014 and 0.026 (evidence `verdict_resolution`). The
rule was committed before the held-out run (`fd70c27`) and is applied as written.

What the owner could turn on, and the evidence: nothing here argues for turning anything on for knowledge quality.
`idle_recheck` costs ~25 % more wall time per cycle and spends half the autonomous budget on re-checks for no measured
gain in these worlds (lane K's own information-value gain, 0.093 → 0.124 bits per budget on held-out, is a different
measure); `reflection` costs storage and records what happened, which an owner may want to read; it moved no knowledge
here. Keeping `hypothesis_fields` and `premise_recheck` on (the shipped defaults) costs 0.03 MB per 14 cycles and
changes no knowledge.

## 5. Why the loop does not improve more (development diagnosis)

On development with the idle re-check on (3 seeds × 2 worlds), of 79 due refreshes 19 superseded the stale item, 20
ended contested, 23 were held and 17 confirmed the item; of 72 idle re-checks 7 superseded, 20 ended contested, 22 were
held and 23 confirmed an unchanged item.

- **A refresh that finds the newer version beside one lagging outlet does not revise.** The cluster is contested, so
  consolidation records a contradiction against the held item and asks the owner (ADR-026, ADR-038); it never
  supersedes it, even two independent outlets to one. With an owner who does not answer, the stale item stays. This
  is the design (the owner decides contradictions), and it is what most limits the loop here; changing it would
  change what the individual believes and is outside this lane.
- **Old pages cannot re-verify a time-sensitive fact.** A release statement whose pages were all published long ago is
  "temporally unresolved" and is held, so even a correct item is not re-verified.
- **The idle re-check prefers the oldest items**, which in these worlds are as often unchanged as changed: it confirms
  more and catches fewer changes per task (7 of 72) than the due order (19 of 79), and it takes budget from it.
- **Marks are a backlog, not a leak.** A failed "still holds" prediction marks the item; the mark closes when the item
  is re-verified, superseded or forgotten. A contested refresh does none of these, so with nobody answering, open marks
  grow with the unresolved contradictions: 19 and 43 after 60 development cycles with `premise_recheck` alone, bounded
  at 500 by design (`premises.MAX_OPEN_MARKS`). They say something true about the items.
- **Storage grows with the work.** About 38–57 KB per cycle at these budgets, in every configuration (the no-loop
  reference 11–19 KB); reflection adds one report of ~3 KB per interval and keeps all of them (no cap: about 156 KB a
  year at the default weekly interval, 1.1 MB at the daily minimum); every other lane K store is bounded.

## 6. Forgetting and overconfidence (brief §4)

- **Nothing learned and still true is lost.** Held-out, over every configuration and seed (120 runs), every subject
  held correctly at cycle 3 whose truth never changed after it was still held correctly at cycle 14: the retention
  base is 22 subject-runs in ho-sparse (23 in two configurations) and 14 in ho-busy per configuration, over the 4
  seeds (the base is small: a subject needs a right item at cycle 3 and no change for 11 cycles; this bounds what
  "nothing lost" can show), 0 lost, 0 regressed, 0 removed by the owner's forget of one honest outlet and the wire's site
  (the items rested on other outlets). Re-checks, marks, reflections and abandonment changed none of them. Test:
  `tests/acceptance/test_learning_cycles_bench.py::test_nothing_learned_and_still_true_is_lost_over_the_cycles` (8
  cycles through the forget, baseline and all six on).
- **Confidence never rises from the individual's own outputs or from copies of one source.** 0 versions asserted only
  by its own outputs (9.x) and 0 versions stated only by the syndicated wrong story (7.x) became knowledge in any run;
  after 3 to 15 one-origin looks per held-out run, 0 rises above both the item's previous confidence and what that one
  origin earns alone. The mechanism: a re-read takes the larger of the old confidence and the new cluster's
  (`consolidate._strengthen`), and copies count as one independent group (`claims.independence_groups`). Test:
  `::test_confidence_never_rises_from_own_outputs_or_from_copies_of_one_source`: ten rounds of stored model answers
  and imagined inferences, ten rounds of one outlet plus three copies on new hosts, ten rounds of the wrong wire story
  with four copies; it fails when copies are counted as independent (checked by patching the grouping).
- **The cognitive benchmark with lane K's mechanisms on** (held-out; re-run on `gen2-y` from `genesis-wave2`):
  retention family all scenarios pass, all retained; adversarial **27 of 28** scored cases pass (the 13 original, 4
  added in Wave 1, 1 restored, 10 added by Wave 2 lane U; 0 model calls). The one failure,
  `reflection_bounded_when_everything_asks` (a reflection section of 21 lines against `MAX_LINES` 20), is lane U's
  measured gap and fails with lane K's mechanisms off as well. Lane F's first record, "18 of 18", was lane F's
  branch, before lane U's cases were on the tree.
- **Not covered:** the individual cannot recognise its own earlier text republished on the web; one echo page is one
  source and is never enough alone, but two independent echo pages would be read as corroboration (not built; not
  staged here).

## 7. Defects found and fixed

- In the product: none fixed. The cycles exposed no defect in lane K's mechanisms against their stated contracts; the
  limits in §5 are design choices or open items.
- In this lane's harness: the no-loop reference ran the loop (§2; fixed with a test). After the benchmark-integrity
  review (lane Y): no defect in what was measured (every recorded per-seed value reproduces); added the references
  for the decay (§3), the activity counts (§4) and the statement of the loop's scope (§2), each with a test.
- Open, for the owner or a later wave (not changed here): a contested refresh never revises a stale item without the
  owner's answer (§5); reflections are kept without a cap (§5); the individual cannot recognise its own text
  republished on the web (§6).

## 8. What is unverified or not run

- **Unverified:** everything outside these simulated worlds: the open web, real search and fetching, answer synthesis,
  a real answer model, real outlets' lag and syndication rates, a real owner, real hardware. The stub reflection model
  stands for a model; nothing called one.
- **Not run:** the worker's idle investigation (`autonomy.idle`; the loop measured here is the timer's pass only, and
  in the product the idle investigator shares the daily allowance with it); the withheld family (it has no
  knowledge-loop world); held-out horizons beyond 14 cycles; an owner who answers contradiction questions; strategy
  selection with more than one research mode in this world; two independent echo pages; the browser suite (no page
  touched); references for the 60-cycle development runs.
- Development settings: none chosen; the reflection interval (1 day) and the strategy ledger row were set before any
  run, as stated in `cycles.py`.

## 9. Reproduce

```
PYTHONPATH=src python scripts/bench_learning_cycles.py run --split held_out --out held.json    # ~1 min
PYTHONPATH=src python scripts/bench_learning_cycles.py verdict held.json
PYTHONPATH=src python scripts/bench_learning_cycles.py run --split development --out dev.json
```
Deterministic per seed apart from wall time and file size; a full run also writes the references (`references`,
from the world generator alone) and, per configuration, the activity counts. `tests/acceptance/test_learning_cycles_bench.py`
(10 tests, ~5 s) runs in the normal suite; the full run is opt-in.

## 10. Wave 3 (2026-11-26): the withheld baseline, the decomposition, the fixes

Record: [WAVE3.md](WAVE3.md); the fix lane's re-measurement in ADR-061 (section "Fix lane").

**The withheld baseline (step 0).** Three learning-cycle worlds of kinds neither split has, four seeds each, were
committed alone and looked at once with the unchanged system (`d0b6b02`, ledger line 4;
`docs/quality/COGNITIVE_BENCHMARK.md`, step 0). The loop beats no loop on every seed in one world of three (+0.042,
4 of 4), not in the others (−0.025, 1 of 4; +0.012, 3 of 4), and sits 0.17 to 0.26 below the blind round-robin of the
same allowance. Unlike §6's 120 held-out runs, where each was 0, versions only the individual's own outputs stated
were admitted (4 / 6 / 6 over 4 seeds), versions only a wrong story stated were admitted (2 / 17 / 12), one confidence
rose after a look with one origin, and subjects held correctly were later regressed (1 / 3 / 3). §6's "nothing
self-generated admitted" holds on the split worlds only.

**The decomposition** (lane R, ADR-060, development, `genesis_recheck_order.json`). The loop's gap to the blind
round-robin, pooled over five worlds (pooled, not a claim): subjects never covered by the owner's research 64 %, a look
that read the true version without revising the item 20 % (50 of 70 such looks contested, the rest held, mostly
one-origin looks; the Wave 3 measurement review), subjects not looked at 22 %, unreadable 6 %, minus 13 % where the
loop is ahead; wrong sources 0. An order of the re-check can reach only the "not looked" part (0.03–0.05); every one of
14 orders got "no evidence" on held-out. Lane V (ADR-062) measured the worker's idle investigation inside the same
allowance: it reaches the uncovered subjects only when it gets a share of the allowance (allowance first: 51–83 % of
the uncovered part removed), and even then the loop stays below the blind round-robin in every world. **Coverage is
limited by the allowance, not by candidates.**

**The fixes.**
- The idle investigation now honours the day's task count (`autonomy._tasks_spent`), a budget correction.
- Lane S's two source rules (`attribution_independence`, `echo_recognition`) removed the step-0 categories on lane
  S's own worlds, but only for the phrasings those worlds were written with; they returned "unsafe" under their own
  pre-registered rule, merged honest outlets that credit each other, and discarded honest pages; their held-out
  figures are development evidence (WAVE3.md §3.1). They ship off (fix lane). **At the shipped defaults the loop
  admits own-text echoes (crediting a model or not) and reworded syndicated copies exactly as at step 0.** A rule that
  decides dependence by publication order and derivation, or by the owner's confirmation, is not built.
- Supersession (off): gains below the spread; 3 of 23 rule replacements false on lane S's held-out (pooled, not a
  claim; re-measured, `genesis_wave3_fix_s.json`); a false one stayed wrong 8 and at least 10 cycles, still wrong when the run ended (censored; WAVE3.md §3.2).

**Unverified**, as in §8: the open web, a live model, an owner who answers, real change rates. The withheld looks of
Wave 3's mechanisms are recorded in WAVE3.md, "The withheld looks".

## 11. Wave 4 (2026-11-26): nothing that improves knowledge across cycles; the runs were not repeatable

Record: [WAVE4.md](WAVE4.md); ADR-064 to ADR-067.

**Nothing in Wave 4 improves knowledge across cycles at the shipped defaults.** Its two source rules ship off (the
derivation rule "not supported; do not turn on", ADR-066; own-text provenance off, ADR-067), and the one mechanism
aimed at the loop itself, a division of the day's allowance between refreshing held items and reaching subjects that
hold nothing (ADR-064, lane A), gains nothing: on held-out reach worlds every candidate's paired gain against the
shipped loop with the idle investigation lies between −0.024 and +0.026, below the spread (0.034–0.054) in every
world, and every candidate is "unsafe" (more wrong content in one world) or "harm" (retention) under the rule written
before the run (`genesis_wave4_a_held_out.json`). The loop with or without a division stays 0.09–0.26 below a blind
round-robin of the same allowance. The diagnosis (development): per look, reaching made more subjects correct than
refreshing, but reaching runs out of admitted candidates after the first cycles (gaps inside their 7-day window, open
hypotheses), so a share cannot add what the admission rule does not offer. Wave 3's "coverage is limited by the
allowance" becomes: by the allowance and, once divided, by the candidates.

**On the learning-cycle worlds** (Wave 3's lane S worlds, `cycles_s`): own-text provenance barely acts there, because
those worlds plant the individual's own values only in its imagined store, which the rule may not read (own-only
versions on its held-out 5 / 2 / 13 → 5 / 2 / 11; Wave 3's `echo_recognition` 1 / 0 / 2); the derivation rule removes
the syndicated-only versions (6 / 9 / 4 → 0) at a cost in `correct` (−0.039 / −0.019 / −0.073). Lane F's worlds:
neither rule changes anything (not exercised). Retention: no subject lost in any of these runs; regressed subjects
equal or fewer with own-text provenance, and with the derivation rule one more in s-echo (2 → 3, summed over seeds;
`genesis_wave4_s.json`, `cycles_wave3_lane_s`, recomputed).

**The repeatability finding.** The learning-cycle runs that use the worker's idle investigation were not repeatable:
two choices among equal records followed the random task ids (`autonomy._lineage` walked a set of ids;
`curiosity.search_terms` and `rank` broke ties among claims stored at one instant by id). Over 10 repeats of 18 public
worlds on Wave 3's final code, `baseline` and `shipped+idle` never varied; `idle-first` varied in 4 worlds, a world's
mean paired gain by up to 0.015 (`genesis_wave4_a_repeatability.json`). Both choices now follow creation order and the
runs are identical; the timer-only configurations, which every verdict of §4 and §10 rests on, did not vary before
and do not move with the fix (594 runs, only database file sizes differ). Consequences for the record: ADR-062's
held-out figures reproduce exactly; Wave 3's withheld `idle-first` gain in W2 (+0.042 against a spread of 0.039) is
not established beyond the spread by a single run of that code (WAVE3.md, the correction under "The withheld
looks"); W3's (+0.052 against 0.035) likely stands. Seed 2704's move (WAVE3.md §3.8) is not explained by this.

**Unverified**, as in §8: the open web, a live model, an owner who answers, real change rates, the real interleaving
of the timer and the worker over a day.
