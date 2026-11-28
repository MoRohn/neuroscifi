# OMNIMIND held-out runs: the record

Every held-out run of OMNIMIND, as the main session took it, once each, with the rule it was judged by. The files
are the run outputs as they were written (copied unchanged from the main session's handoff folder
`.omc/handoffs/omnimind/heldout-results/`, where they had been copied from the scratch folder). 14 files,
135,088 bytes in all.

**When the rules were written.** The rules were written in the main session's log
(`.omc/handoffs/omnimind.md`) before each run, and the log orders each rule before its result. That log is not in
the repository and was edited throughout the day with no per-line timestamps, so the order of rule and run **cannot
be verified from git**: what git shows is only that this record was committed after all of the runs (fix lane F2,
review finding B2). The withheld looks are different: their pass rule is to be committed to the repository before
them (no product look at the withheld split has been taken yet), and every look is appended to
`docs/quality/evidence/omnimind_final_uses.jsonl` before it is scored. The quotes
below are the log's text as it stood when this record was written; line numbers are those of that copy.

**Summaries only.** `scripts/omnimind_heldout_record.py check` walks every file and refuses any string that is not
an outcome word, a hash, a commit, a date, a version, the run's own label or note, a held-out task id of the frozen
tables (as a key) or a word the tracked case tables already hold: 0 problems in all 14 files (no file bytes, no
request text). Nothing was stripped. The per-task keys of lane Z's families are task ids
(`family/held_out/case/question`) whose parts all come from `acceptance/omni/cases.py`; the forms result holds
counts and slices only, plus three purpose words that `forms_cases.py` holds.

| run | commit | files | verdict by its rule |
|---|---|---|---|
| forms: the planner, once | `c5d3230` (+ lane ZF's cases) | `final_c5d3230.json`; the blind development run before its author saw a case: `dev_blind_8d84f29.json` | "beats the references": NO (1 hard-constraint violation); "overfit to development": YES (0.850 to 0.659); "learned from the words": NO |
| intake, honest, unknown, compound, hostile | `c1f5f2f` | `intake.json`, `honest.json`, `unknown.json`, `compound.json`, `hostile.json`, `summary.json` | no fabrication, no crash: HOLDS; hostile refused: NOT FULLY (22 of 23; one "cannot"); generalizes: HOLDS |
| cross (the relater) | `7d9038c` | `cross-relate.json` | SUPPORTED (66 right, 6 cannot, 0 wrong of 72) |
| learn (the learner, SIMULATED people) | `7d9038c` | `learn-make_learner.json`, `learn-make_planner_only.json` | SUPPORTED (+0.260 late, +0.203 all over the planner alone); 0.092 below the reference majority rule |
| made, transform | `505b55b` | `made.json`, `transform.json` | no fabrication, no crash: HOLDS; generalizes: transform holds (with lane P's disclosure), made NOT (13 of 14: one making task the product could not do) |
| the forms examiner's labels against a second reader | n/a (labels) | `reader2-agreement.json` | the second reader's first choice is in the examiner's good set on 0.902 (development) and 0.877 (held-out) of cases |

Whole held-out split, the product: **510 right, 18 "cannot", 0 wrong, 0 failed of 528** (intake side at `c1f5f2f`,
made and transform at `505b55b`). Today's system on the same split: 55 right (25 intake answers and 30 right
abstentions; the floor of 52 is what refusing everything scores), 1 wrong, 1 crash (corrected 2026-11-27: this
sentence said "52 of them the floor"). What those 510 are made of (`scripts/omnimind_heldout_record.py breakdown`,
`../omnimind_right_breakdown.json`): 128 format identifications, 310 measurements read off decoded content, 49 correct
refusals or abstentions and 2 questions where a value or an honest "cannot" is right, 21 made artifacts read back.

## The planner (forms family), once, at `c5d3230`

Files: `final_c5d3230.json` (53,593 bytes), `dev_blind_8d84f29.json` (25,343 bytes).

Log lines 158-167, verbatim:

```text
  RULE FOR THE ONE HELD-OUT RUN, WRITTEN BEFORE IT (2026-11-26 16:05 EST; fields of forms.score(…, "held_out")):
  references on held-out: shape_only good 0.500 / good-or-fine 0.804; always-prose good 0.442 / gof 0.739;
  second reader's first choice good 0.877.
  (1) "beats the references" iff planner good.share ≥ 0.600 (shape_only + 0.10) AND bad.share ≤ shape_only's
      bad.share AND bad.too_much.k ≤ shape_only's too_much.k AND hard_constraints_violated.k == 0.
  (2) "overfit to development" iff development good.share − held-out good.share > 0.15; reported whatever (1) says.
  (3) "learned from the words" iff on the slice cue == "words" the planner's good_share exceeds shape_only's by
      ≥ 0.15 AND pairs/twins both_good shares exceed shape_only's.
  One run, by the main session, at c5d3230 + omni-zf's cases; no retuning on held-out after it; a later planner
  version gets a NEW held-out only from the withheld lane.
```

Log lines 168-183, verbatim:

```text
- 16:10: THE ONE HELD-OUT RUN of the planner (c5d3230; result scratch omnimind/blind/final_c5d3230.json; taken in
  a throwaway worktree omni-blind = c5d3230 + omni-zf):
  held-out (138): planner good 91 (0.659), good-or-fine 0.920, bad 0.080 (too poor 4, too much 5, unfit 1,
  violation 1); explicit feasible honoured 10/11, infeasible handled 4/5; cue slices good: content 57/81, words
  16/34, conflict 4/7, explicit 14/16; pairs both good 3/18, twins 2/10.
  shape_only: good 0.500, gof 0.804, bad 0.196 (too much 20), explicit 3/11; words 14/34; pairs 8/18, twins 0/10.
  today (always prose): good 0.442, gof 0.739, bad 0.261 (too poor 28).
  development (133): planner good 0.850, gof 1.000, bad 0.
  VERDICT BY THE RULE WRITTEN BEFORE THE RUN: (1) "beats the references": NO: good ≥ 0.600 yes, bad ≤ reference
  yes, too much ≤ reference yes, BUT hard_constraints_violated = 1 (must be 0). (2) "overfit to development":
  YES (0.850 − 0.659 = 0.190 > 0.15). (3) "learned from the words": NO (words slice 0.471 vs 0.412: +0.059
  < 0.15; request pairs 3/18 against the shape table's 8/18).
  Honest reading: better than always-prose and the shape table on good (+0.22, +0.16) and on wrong choices
  (0.08 vs 0.26 / 0.20; "too much" 5 vs 20), honours explicit requests; but one hard-constraint violation, a
  development-to-held-out gap of 0.19, and no evidence that it reads the request's words (pairs worse than the
  table). Unmapped held-out purposes: 3 names the planner does not know (passed as None).
```

## The intake side (five families), once, at `c1f5f2f`

Files: `intake.json` (24,640 bytes), `honest.json` (5,561 bytes), `unknown.json` (5,110 bytes), `compound.json` (4,704 bytes), `hostile.json` (2,784 bytes), `summary.json` (1,147 bytes).

Log lines 275-285, verbatim:

```text
  RULE FOR THE ONE HELD-OUT RUN OF WAVE O-0's INTAKE SIDE, WRITTEN BEFORE IT (fields of
  `bench_omni.py run --split held_out --subject artifacts` per family: n, right, abstained, wrong, failed):
  families intake, honest, unknown, compound, hostile (made and transform wait for lane P, which changes them).
  (1) "no fabrication, no crash": wrong == 0 and failed == 0 in every family; any wrong or failed is reported
      with its count as the headline, whatever else holds.
  (2) "hostile refused": hostile right == n (every attack refused within the harness's time and memory bounds).
  (3) "generalizes": per family, held-out right/n is not more than 0.05 below development's right/n (intake
      0.979, honest 0.945, unknown 1.000, compound 0.944, hostile 1.000); a larger drop is "overfit to
      development" for that family and is reported.
  One run, by the main session, on omnimind c1f5f2f (clean), outputs outside the tree; lanes get aggregates per
  family only, never a held-out task id; the final claim rests on the withheld look.
```

Log lines 286-295, verbatim:

```text
- THE ONE HELD-OUT RUN of the intake side (omnimind c1f5f2f, clean; outputs scratch omnimind/heldout/*.json,
  summary.json), right/abstained/wrong/failed of n (development share → held-out share):
  intake 328/11/0/0 of 339 (0.979 → 0.968); honest 52/3/0/0 of 55 (0.945 → 0.945); unknown 49/0/0/0 of 49
  (1.000 → 1.000); compound 38/2/0/0 of 40 (0.944 → 0.950); hostile 22/1/0/0 of 23 (1.000 → 0.957).
  TOTAL 489 right, 17 "cannot", 0 wrong, 0 failed of 506. (Today's system on the whole held-out split: 55 right
  of 528, 52 of them the floor, 1 wrong, 1 crash.)
  VERDICT BY THE RULE WRITTEN BEFORE THE RUN: (1) no fabrication, no crash: HOLDS. (2) hostile refused: NOT
  FULLY: 22 of 23 refused; one attack was answered "cannot" instead of refused (safe: nothing wrong, nothing
  written, no crash; but not the named refusal the rule asks). (3) generalizes: HOLDS in every family (largest
  drop 0.043, hostile; intake 0.012).
```

**Correction (2026-11-27, documentation lane 2, the main session's ruling; the quote above is left as the log has it):** "52 of them the floor" is wrong. Today's system scores 55 right per split = 25 real intake answers + 30 right abstentions (20 honest and 9 unknown "cannot", 1 hostile refusal; `../omnimind_baseline.json`). 52 is the abstention floor: what a subject that refuses everything scores; one that says "cannot" to everything scores 29.

**The hostile task answered "cannot" instead of refused.** One of the 23 held-out hostile tasks was answered "cannot"
rather than refused: safe (nothing wrong, nothing written, no crash) but not the named refusal the rule asks for,
so rule (2) does not hold. Review B4 traced how: the benchmark's driver turned a product error into "cannot" or
"refused" by its reason string; lane F1 moves that decision into the product. Which task it was is in
`hostile.json` (an id and an outcome only).

## cross and learn, once each, at `7d9038c`

Files: `cross-relate.json` (3,026 bytes), `learn-make_learner.json` (1,585 bytes), `learn-make_planner_only.json` (1,600 bytes).

Log lines 339-350, verbatim:

```text
- RULES FOR THE HELD-OUT RUNS OF `cross` AND `learn`, WRITTEN BEFORE THEM (the lanes' proposals, adopted; one run
  each, by the main session, on omnimind 7d9038c; int2 does not change relate.compare or learn):
  CROSS (`bench_omni_forms.py run --family cross --split held_out --subject neuroscifi.acceptance.omni.subject:relate`;
  fields correct, answered, correct_of_answered, both_twins_correct, by_kind): "supported" iff wrong (= answered
  − correct) ≤ 2 of 72 AND correct_of_answered ≥ 0.95 AND correct ≥ 54 AND both twins correct ≥ 24 of 36 AND no
  kind with more than 1 wrong. Any wrong is reported as a fabrication count beside the verdict. References:
  always-consistent / always-inconsistent 36 right / 36 wrong; today 0 answered.
  LEARN (`… --family learn --split held_out --subject …:make_learner`, with `…:make_planner_only` as the
  product's never-learns; fields late_kept, all_kept: mean and sd over seeds; SIMULATED people): "supported" iff
  the mean gain of the learner over the planner alone exceeds 0.058 late AND 0.067 all (the family's visibility
  margins on held-out). "Better than a majority rule" is not claimed; the reference key_majority's and the
  oracle's figures are reported beside it. Whatever the result, `form_learning` stays off by default.
```

Log lines 351-362, verbatim:

```text
- HELD-OUT RUNS OF `cross` AND `learn` TAKEN ONCE (omnimind 7d9038c, clean; outputs scratch omnimind/heldout/
  cross-relate.json, learn-make_learner.json, learn-make_planner_only.json):
  CROSS: 66 right / 6 cannot / 0 wrong of 72; correct of answered 66/66; both twins 33/36; per kind right of 12:
  chart_series 12, tones_rows 12, mesh_bbox 12, code_trace 12, timeline_log 12, report_total 6 (6 "cannot").
  VERDICT: SUPPORTED (wrong 0 ≤ 2; 1.00 ≥ 0.95; 66 ≥ 54; twins 33 ≥ 24; no kind with a wrong). Weakness to
  state: half of the held-out report-total cases are answered "cannot" (development: all 12 right) → the
  statement reader does not generalize to those reports' wording; honest "cannot", no fabrication.
  LEARN (SIMULATED people; late / all kept, sd over 10 seeds): shipped majority through the planner 0.710
  (0.032) / 0.656 (0.019); planner alone 0.450 (0.024) / 0.454 (0.025); gain +0.260 / +0.203 > margins
  0.058 / 0.067 → SUPPORTED. Beside it (ZF's held-out references): today 0.393/0.396; never_learns 0.433/0.429;
  person_majority 0.715/0.695; key_majority 0.802/0.727; oracle 0.880/0.878 → the shipped rule is 0.092 below
  the reference majority rule late, at the level of the per-person majority. `form_learning` stays off.
```

The learn family's people are SIMULATED (the benchmark's own inventions: hidden, stable preferences and simulated
feedback); the result says nothing about benefit to a real person.

## made and transform, once, at `505b55b`

Files: `made.json` (2,093 bytes), `transform.json` (1,782 bytes).

Log lines 389-395, verbatim:

```text
  RULE FOR THE ONE HELD-OUT RUN OF `made` AND `transform`, WRITTEN BEFORE IT (omnimind 505b55b; fields n, right,
  abstained, wrong, failed of `bench_omni.py run --split held_out --subject artifacts --family made|transform`):
  (1) no fabrication, no crash: wrong == 0 and failed == 0; (2) generalizes: right/n not more than 0.05 below
  development's (made 14/14, transform 8/8 → at most one miss in each would already exceed 0.05: reported as
  such, with the counts); the benchmark's own readers judge every made artifact. `transform` carries lane P's
  disclosure (held-out rows were once printed to its screen): its result is reported with that caveat and
  proves less than the others.
```

Log lines 396-402, verbatim:

```text
- HELD-OUT `made` AND `transform` TAKEN ONCE (omnimind 505b55b; outputs scratch omnimind/heldout/made.json,
  transform.json): made 13 right / 1 cannot / 0 wrong / 0 failed of 14; transform 8 / 0 / 0 / 0 of 8.
  VERDICT: (1) no fabrication, no crash: HOLDS. (2) generalizes: transform holds (with lane P's disclosure
  caveat); made: one "cannot" of 14 (0.929 against 1.000 on development: a drop of 0.071, over the 0.05 line) →
  reported as "one held-out making task the product could not do", not as a pass.
  WHOLE HELD-OUT SPLIT, product: 510 right / 18 cannot / 0 wrong / 0 failed of 528 (intake side at c1f5f2f; made
  and transform at 505b55b). Today's system: 55 right (52 = the floor), 1 wrong, 1 crash.
```

**Correction (2026-11-27, documentation lane 2, the main session's ruling; the quote above is left as the log has it):** "52 of them the floor" is wrong. Today's system scores 55 right per split = 25 real intake answers + 30 right abstentions (20 honest and 9 unknown "cannot", 1 hostile refusal; `../omnimind_baseline.json`). 52 is the abstention floor: what a subject that refuses everything scores; one that says "cannot" to everything scores 29.

**Lane P's disclosure.** Lane P saw the held-out transform case rows while reading `cases.py` (they were printed to
its screen); the held-out `transform` result carries that caveat and proves less than the others. Log lines 325-326, verbatim:

```text
  DISCLOSURE: lane P saw the HELD-OUT transform case rows while reading cases.py → the held-out result for
  `transform` (8 tasks) carries that caveat; the withheld look is unaffected.
```

## The second reader of the forms labels

File: `reader2-agreement.json` (2,120 bytes) (counts only; the labels themselves, with the views' ids, are in
`../omnimind_forms_second_reader.json`).

Log lines 134-139, verbatim:

```text
- 15:15: SECOND READER done (labels scratch omnimind/reader2/labels.json; agreement omnimind/reader2-agreement.json).
  Reader's FIRST choice under the examiner's sets: good 241/271 = 0.889 (dev 0.902, held-out 0.877), good-or-fine
  0.993, bad 2/271 (0.007). No examiner-good form is called too poor/too much by the reader (335 in best, 52 in
  acceptable, 25 not listed). → labels are consistent between two independent readers; quote 0.88 (held-out) as
  the reference level a careful reader reaches on "good". CAVEAT to state everywhere: both readers are models of
  the same family, not people; agreement between them is not agreement with the owner's taste.
```
