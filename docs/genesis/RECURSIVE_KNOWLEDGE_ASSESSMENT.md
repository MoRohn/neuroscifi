# Recursive knowledge engine assessment (deliverable F)

Status: 2026-11-25, Wave 2 lane F, branch `gen2-f` from `main` at `3559069`. Directive §7 ("recursive knowledge
evolution") and §13 F. Evidence: `docs/quality/evidence/genesis_learning_cycles.json`. Nothing here is a claim of
consciousness, awareness or general intelligence; the names describe functions.

**Result.** In a simulated world of fictional software releases, run through the real extraction, clustering,
consolidation, prediction and timer code for 14 daily cycles, the knowledge loop as it exists (refreshes of due
"still holds" predictions and the timer's curiosity, every Wave 1 setting off) keeps the individual's knowledge more
correct than no loop at all: held-out, the share of subjects whose newest held item states the current version is
0.574 → 0.631 and 0.497 → 0.571 in the two worlds, better on 8 of 8 paired seeds, at about twice the pages and wall
time. **But knowledge still decays over the cycles in every configuration** (0.88 → 0.44 and 0.85 → 0.46 from
cycle 0 to cycle 14 with the loop; 0.38 and 0.33 without it), so this is slower decay, not persistent improvement.
**None of lane K's six mechanisms shows a held-out gain beyond the baseline's spread: the verdict is "no evidence"
for each** (the pre-registered rule, §4). Five change nothing on any knowledge metric; the idle re-check moves the
primary metric −0.019 and −0.004 (spread 0.105 and 0.057; better on 2 of 8 paired seeds). In 120 held-out runs nothing
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

**Order (pre-registration).** The worlds and their development and held-out seeds were committed first (`b3de5d2`);
four outlet wordings that consolidation reads as not self-contained (a pronoun or "this ...") were replaced after one
development run (`7df0136`), before the harness existed in the history; the harness with its verdict rule (§4) was
committed next (`35845f8`), then the held-out split was run once. No mechanism, setting default or product file
changed at any point. Two harness corrections followed, both disclosed: the no-loop reference had recorded the owner's
autonomy enable, which turns autonomous research on, so it equalled the baseline (fixed in `e24f4d0`, test added);
and the per-seed rows lacked the counts behind their shares (`8f429ec`). Both splits were re-run on `8f429ec`: 0 of
10,363 held-out values and 0 of 9,073 development values differ from the earlier runs apart from wall time and file
size (an SQLite file's size varies by a page or two between identical runs). Those re-runs are the numbers below.

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
| error persistence, cycles | 7.56 ± 1.07 | 5.72 ± 0.97 | 6.44 ± 0.92 | 6.12 ± 1.02 |
| Brier (constant reference) | 0.242 (0.224) | 0.186 (0.199) | 0.204 (0.209) | 0.184 (0.203) |
| pages / wall s / file MB at the end | 106 / 0.20 / 1.01 | 208 / 0.42 / 1.38 | 213 / 0.54 / 1.39 | 213 / 0.57 / 1.53 |

| ho-busy | no loop | baseline | idle re-check alone | all six |
|---|---|---|---|---|
| **correct** | 0.497 ± 0.082 | **0.571 ± 0.057** | 0.567 ± 0.088 | 0.580 ± 0.083 |
| accuracy of held items | 0.593 ± 0.080 | 0.683 ± 0.050 | 0.677 ± 0.087 | 0.693 ± 0.081 |
| coverage | 0.831 | 0.831 | 0.831 | 0.831 |
| changes caught (of 185) | 0 | 56 (0.295 ± 0.145) | 49 (0.262 ± 0.100) | 55 (0.294 ± 0.088) |
| error persistence, cycles | 7.10 ± 1.07 | 4.60 ± 0.42 | 4.85 ± 1.04 | 4.52 ± 0.75 |
| Brier (constant reference) | 0.269 (0.240) | 0.208 (0.219) | 0.213 (0.219) | 0.208 (0.215) |
| pages / wall s / file MB | 102 / 0.17 / 1.01 | 191 / 0.39 / 1.40 | 196 / 0.50 / 1.41 | 194 / 0.54 / 1.54 |

**The loop against no loop** (paired, held-out): correct +0.057 and +0.074, accuracy +0.067 and +0.090, error
persistence −1.8 and −2.5 cycles, Brier −0.056 and −0.061, every one on 4 of 4 seeds in both worlds; cost +102 and
+90 pages, +0.22 s and +0.37 / +0.38 MB per run. Development agrees (+0.048, +0.086; 3 of 3 seeds each). Without the loop the
stated confidence is worse than a constant (Brier above its reference); with it, slightly better.

**Over the cycles** (correct, mean over seeds, at cycles 0 / 1 / 4 / 7 / 10 / 14):

| | ho-sparse | ho-busy |
|---|---|---|
| no loop | 0.875 / 0.868 / 0.705 / 0.550 / 0.455 / 0.375 | 0.854 / 0.732 / 0.588 / 0.510 / 0.391 / 0.331 |
| baseline | 0.875 / 0.882 / 0.727 / 0.610 / 0.554 / 0.438 | 0.854 / 0.732 / 0.625 / 0.587 / 0.508 / 0.463 |
| all six | 0.875 / 0.882 / 0.727 / 0.600 / 0.536 / 0.461 | 0.854 / 0.750 / 0.662 / 0.625 / 0.484 / 0.431 |

The loop does not improve knowledge cycle over cycle; it slows its decay. On 60-cycle development runs (one seed per
world) correct falls to 0.26 and 0.12 with the loop, 0.18 and 0.09 without it.

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

| Mechanism (default) | Held-out gain in correct (baseline sd), paired seeds better / worse | (a) | (b) | (c) | (d) | Verdict |
|---|---|---|---|---|---|---|
| `hypothesis_fields` (on, record) | 0, 0 (identical) | no | n.a. | yes | yes | **no evidence** (display-only) |
| `premise_recheck` (on, record) | 0, 0 (identical); in `all` its marks add +0.013, +0.014 to the re-check (better on 2, worse on 1 of 4 seeds in ho-sparse; better on 4 of 4 in ho-busy; within the sd) | no | n.a. | yes | yes | **no evidence** |
| `idle_recheck` (off) | −0.019 (0.105), −0.004 (0.057); 1/3 and 1/3 | no | n.a. | yes | yes | **no evidence** |
| `hypothesis_abandon` (off) | 0, 0 (identical); 4.5 and 4.25 hypotheses abandoned per run | no | n.a. | yes | yes | **no evidence** |
| `reflection` (off) | 0, 0 (identical); 14 reflections per run | no | n.a. | yes | yes | **no evidence** |
| `strategy_selection` ("gate", inactive) | 0, 0 (identical): one research mode here, nothing to rank | no | n.a. | yes | yes | **no evidence** (not measurable in this world) |
| all six | −0.006 (0.105), +0.010 (0.057); 1/3 and 2/2 | — | — | yes | yes | — |

(b) is "n.a." because `all` itself does not beat the baseline. On development the idle re-check gave +0.022 (3 of 3
seeds) and −0.009: the development gain did not transfer to held-out.

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
  held correctly at cycle 3 whose truth never changed after it was still held correctly at cycle 14: 22–23
  subject-runs in ho-sparse and 14 in ho-busy per configuration (the base is small: a subject needs a right item at cycle 3 and no
  change for 11 cycles), 0 lost, 0 regressed, 0 removed by the owner's forget of one honest outlet and the wire's site
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
- **The cognitive benchmark with lane K's mechanisms on** (held-out, this tree): retention family all scenarios pass,
  all retained; adversarial 18 of 18 scored cases pass (the 13 original, 4 added in Wave 1, 1 restored; 0 model calls).
- **Not covered:** the individual cannot recognise its own earlier text republished on the web; one echo page is one
  source and is never enough alone, but two independent echo pages would be read as corroboration (not built; not
  staged here).

## 7. Defects found and fixed

- In the product: none fixed. The cycles exposed no defect in lane K's mechanisms against their stated contracts; the
  limits in §5 are design choices or open items.
- In this lane's harness: the no-loop reference ran the loop (§2; fixed with a test).
- Open, for the owner or a later wave (not changed here): a contested refresh never revises a stale item without the
  owner's answer (§5); reflections are kept without a cap (§5); the individual cannot recognise its own text
  republished on the web (§6).

## 8. What is unverified or not run

- **Unverified:** everything outside these simulated worlds: the open web, real search and fetching, answer synthesis,
  a real answer model, real outlets' lag and syndication rates, a real owner, real hardware. The stub reflection model
  stands for a model; nothing called one.
- **Not run:** the withheld family (it has no knowledge-loop world); held-out horizons beyond 14 cycles; an owner who
  answers contradiction questions; strategy selection with more than one research mode in this world; two independent
  echo pages; the browser suite (no page touched).
- Development settings: none chosen; the reflection interval (1 day) and the strategy ledger row were set before any
  run, as stated in `cycles.py`.

## 9. Reproduce

```
PYTHONPATH=src python scripts/bench_learning_cycles.py run --split held_out --out held.json    # ~1 min
PYTHONPATH=src python scripts/bench_learning_cycles.py verdict held.json
PYTHONPATH=src python scripts/bench_learning_cycles.py run --split development --out dev.json
```
Deterministic per seed apart from wall time and file size; `tests/acceptance/test_learning_cycles_bench.py` (7 tests,
~4 s) runs in the normal suite; the full run is opt-in.
