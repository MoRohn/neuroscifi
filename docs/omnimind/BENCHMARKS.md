# OMNIMIND benchmarks: before and after (deliverable 7)

Status: 2026-11-27, written on `omni-docs2` from `omnimind` at `21193c4` (the withheld looks taken on `55f89be`). Every figure is copied from the committed
evidence file named beside it; nothing was re-run for this page. The examinations themselves, their rules and their
readers are in [../quality/OMNIMIND_BENCHMARK.md](../quality/OMNIMIND_BENCHMARK.md) (taking things in, making things)
and [../quality/OMNIMIND_BENCHMARK_FORMS.md](../quality/OMNIMIND_BENCHMARK_FORMS.md) (choosing a form, relating two
artifacts, learning from outcomes). Every held-out run, its rule quoted verbatim and its verdict:
[../quality/evidence/omnimind_heldout/README.md](../quality/evidence/omnimind_heldout/README.md).

## The first screen: what failed, and what the figures are not

- **The look at the second withheld split (`ae6120f`): none of the three fixed mechanisms supported.** unknown 158
  right, **5 wrong** of 171; hostile **11 answered instead of refused** of 62; cross 1 wrong of 48 answered but right on
  only 47 of 80 decidable cases (needs 60). The owner's merge condition is not met. Section "The look at the second
  withheld split" at the end.
- **On the withheld split, which no lane tuned against, the product gave 6 wrong answers in the seven families** (629
  right, 17 cannot, 6 wrong, 0 failed of 652): **3 disguised or unknown files answered falsely** (`unknown`, not
  supported) and **3 hostile files answered instead of refused** (`hostile`, 32 of 35, not supported). **The relater
  gave 6 wrong answers of 78** (cross, not supported); on an image against its caption it answered every time and was
  right on 5 of 10. Nothing of the kind happened on development or held-out. These are known weaknesses now, to be
  fixed and examined on NEW withheld cases, never on these. Supported on unseen cases: intake, honest, compound, made and
  transform. The form planner's and the learner's looks are VOID by the rule (two controls were not run; as measured,
  the planner's good 0.687 against 0.480, the learner 0.713 against 0.440). Section "The withheld looks" at the end.
- **One held-out making task the product could not do.** `made`: 13 of 14 on held-out against 14 of 14 on development.
  By the rule written before the run this is **not a pass** (a drop over 0.05).
- **One hostile held-out file was answered "cannot", not refused** (22 of 23 refused). Nothing wrong was answered,
  nothing was written, nothing crashed; but the rule asked for the named refusal. That outcome came through the
  earlier driver, which mapped the product's error to "cannot" by its words (review finding B4, since fixed).
- **The form planner does not beat the references on held-out** by the rule written before its run: one
  hard-constraint violation (since made impossible by construction), a development-to-held-out drop of 0.19, and no
  evidence that it reads the request's words.
- **The learner is below the benchmark's own majority rule** on held-out (0.710 against 0.802 late), with simulated
  people only (withheld: 0.713 against 0.831).
- **The held-out rules lived in the main session's log, which git does not track**: the order of rule and run
  **cannot be verified from git**. Lane P once saw the held-out `transform` rows, so that family's held-out result
  proves less than the others.
- **Nothing here is about real files, real people, beauty, legibility or understanding.** Every file is built by the
  benchmark's own encoders from values it chose; "right" is exact recovery within a stated tolerance; the people of
  the learn family are simulated.

## Taking things in and making things: today's system against the product

`scripts/bench_omni.py`, right / abstained ("cannot" or refused where an answer was expected) / wrong / failed, of n.
Today's system: `docs/quality/evidence/omnimind_baseline.json` (at `23b5e50`). The product on development:
`omnimind_int2_development.json` (at `f5cc17b`; unchanged at `09bfff6` by the second measurement pass). The product
on held-out: `omnimind_heldout/{intake,honest,unknown,compound,hostile}.json` at `c1f5f2f`, `made.json` and
`transform.json` at `505b55b`.

| family | floor | today, development | product, development | today, held-out | product, held-out | verdict (held-out rule) |
|---|---:|---|---|---|---|---|
| intake | 0 | 25 / 312 / 0 / 0 of 337 | 330 / 7 / 0 / 0 of 337 | 25 / 314 / 0 / 0 of 339 | 328 / 11 / 0 / 0 of 339 | generalizes (0.979 to 0.968) |
| honest | 20 | 20 / 35 / 0 / 0 of 55 | 52 / 3 / 0 / 0 of 55 | 20 / 35 / 0 / 0 of 55 | 52 / 3 / 0 / 0 of 55 | generalizes (0.945 to 0.945) |
| unknown | 9 | 9 / 42 / 0 / 0 of 51 | 51 / 0 / 0 / 0 of 51 | 9 / 40 / 0 / 0 of 49 | 49 / 0 / 0 / 0 of 49 | generalizes (1.000 to 1.000) |
| compound | 0 | 0 / 36 / 0 / 0 of 36 | 34 / 2 / 0 / 0 of 36 | 0 / 40 / 0 / 0 of 40 | 38 / 2 / 0 / 0 of 40 | generalizes (0.944 to 0.950) |
| hostile | 23 | 1 / 20 / **1** / **1** of 23 | 23 / 0 / 0 / 0 of 23 | 1 / 20 / **1** / **1** of 23 | 22 / **1** / 0 / 0 of 23 | generalizes (drop 0.043); **"hostile refused" does not fully hold** |
| made | 0 | 0 / 14 / 0 / 0 of 14 | 14 / 0 / 0 / 0 of 14 | 0 / 14 / 0 / 0 of 14 | 13 / **1** / 0 / 0 of 14 | **does not generalize** (0.929 against 1.000) |
| transform | 0 | 0 / 8 / 0 / 0 of 8 | 8 / 0 / 0 / 0 of 8 | 0 / 8 / 0 / 0 of 8 | 8 / 0 / 0 / 0 of 8 | generalizes, **with lane P's disclosure** |
| **all** | **52** | **55** / 467 / **1** / **1** of 524 | **512** / 12 / 0 / 0 of 524 | **55** / 471 / **1** / **1** of 528 | **510** / 18 / 0 / 0 of 528 | no fabrication, no crash: holds |

**Read "right" against the floor.** 52 of each split's tasks are right only as an abstention: a subject that refuses
everything scores 52; one that says "cannot" to everything scores 29 (the 23 hostile tasks need a refusal). Today's
55 per split are 25 intake answers and 30 abstentions where abstaining is right (20 honest, 9 unknown, 1 hostile
refusal; `omnimind_baseline.json`); it also answered a question about a PDF that is also a ZIP (wrong) and crashed
with a MemoryError on an `.npy` declaring terabytes. (The phrase "55 right (52 of them the floor)", which stood in the
main session's log, the held-out record, ADR-075 and the benchmark document, is wrong: the held-out record and
ADR-075 now carry dated corrections beside the earlier text, and the benchmark document is corrected.)

**What "right" is made of** (`docs/quality/evidence/omnimind_right_breakdown.json`):

| kind of answer | product, development | product, held-out |
|---|---:|---:|
| format identification | 128 / 128 | 128 / 128 |
| measurement read off decoded content | 310 / 322 | 310 / 326 |
| correct refusal or abstention | 50 / 50 | 49 / 50 |
| a value or an honest "cannot" (either is right) | 2 / 2 | 2 / 2 |
| made artifact read back | 22 / 22 | 21 / 22 |
| **all** | **512 / 524** | **510 / 528** |

A subject that identified every format and abstained or refused everywhere else would score up to 180 of 524. What
the product could not read on development (the 12 "cannot"): 7 questions about an array's dtype (arrays are kept as
a table, which does not carry it), an MP3's exact duration (no MP3 decoder; only a labelled estimate), a Parquet
file's rows and columns (no reader), and the size and a pixel of a picture embedded in a DOCX (named, not decoded).

## Choosing a form (the planner, "Creative Output Intelligence")

`scripts/bench_omni_forms.py --family forms`; 133 development and 138 held-out cases; references from
`omnimind_baseline_forms.json`; the planner's held-out run `omnimind_heldout/final_c5d3230.json`; the blind run
`omnimind_heldout/dev_blind_8d84f29.json`; the frozen planner on development `omnimind_int2_development.json`.
The full assessment: [CREATIVE_INTELLIGENCE_ASSESSMENT.md](CREATIVE_INTELLIGENCE_ASSESSMENT.md).

| subject | development good | development bad | held-out good | held-out bad | held-out violations |
|---|---:|---:|---:|---:|---:|
| today's system (always prose) | 0.399 | 0.271 (30 too poor) | 0.442 | 0.261 (28 too poor) | 0 |
| shape-only table (reference) | 0.459 | 0.195 (17 too much) | 0.500 | 0.196 (20 too much) | 0 |
| richest form (reference) | 0.068 | 0.910 | 0.080 | (not quoted) | 0 |
| random form (reference) | 0.110 | 0.761 | 0.104 | (not quoted) | 0 |
| the planner, blind (`8d84f29`, before its author saw a case) | 0.489 | 0.195 | not run | | |
| the planner tuned (`c5d3230`) | 0.850 (optimistic: tuned on these cases) | 0 | **0.659** | 0.080 | **1** |
| the planner frozen (`4388f4d`, after the limited round) | 0.805 (107 of 133) | 0 | not run; withheld: 0.687 as measured (VOID by the rule; see the end) | 0.073 | 0 |
| a second reader's first choice (a ceiling; a model, not a person) | 0.902 | | 0.877 | | |

Held-out verdict by the rule written before the run: "beats the references" **no** (one violation); "overfit to
development" **yes** (0.850 to 0.659); "learned from the words" **no**. On held-out, always-prose beat the planner on
the slice where the words decide (17 against 16 of 34) and on the conflict slice (7 of 7 against 4 of 7); the
shape-only table beat it on request pairs (8 against 3 of 18).

## Relating two artifacts (the relater) and learning (the learner)

**cross** (`omnimind_r.json`, `omnimind_int2_development.json`, `omnimind_heldout/cross-relate.json`):

| subject | development correct | held-out correct / cannot / wrong | both twins right |
|---|---|---|---|
| today's system | 0 of 72 answered | 0 answered | 0 |
| always consistent, always inconsistent (references) | 36 of 72 | 36 of 72 | 0 |
| the relater (`nsf artifact relate`) | 72 of 72 | 66 / 6 / 0 of 72 | 36 of 36; held-out 33 of 36 |

Held-out verdict: supported. Half of the held-out report-total cases (6 of 12) ended "cannot". Without the question
naming the comparison the relater scores 58 / 14 / 0 on development: it compares what it is told to compare. The
relater was changed after the held-out run (fix lane F3); development stayed 72 / 72.

**learn**, SIMULATED people (`omnimind_l.json`, `omnimind_baseline_forms.json`, `omnimind_heldout/learn-*.json`; late
kept / all kept, mean over seeds):

| subject | development | held-out |
|---|---|---|
| today's system | 0.403 / 0.409 | 0.393 / 0.396 |
| never learns (reference) | 0.581 / 0.579 | 0.433 / 0.429 |
| the planner alone (the product without learning) | 0.587 / 0.584 | 0.450 / 0.454 |
| **the shipped per-key majority through the planner** | **0.816 / 0.762** | **0.710 / 0.656** |
| person_majority (reference) | 0.757 / 0.738 | 0.715 / 0.695 |
| key_majority (reference) | 0.859 / 0.794 | 0.802 / 0.727 |
| oracle (knows the hidden preference) | 0.907 / 0.902 | 0.880 / 0.878 |

Held-out verdict: supported over the planner alone (+0.260 / +0.203, margins 0.058 / 0.067); **0.092 below the
reference majority rule** late. `form_learning` is off by default. See
[RECURSIVE_KNOWLEDGE_VALIDATION.md](RECURSIVE_KNOWLEDGE_VALIDATION.md).

## Caveats that travel with every held-out figure

- The rules were written in the main session's untracked log before each run; git shows only that the record was
  committed after all runs.
- Every held-out run of lane Z's families went through the earlier driver, which decided some outcomes itself
  (an injection refusal, a partial decode as "refused", an error's words as "cannot"). The product decides those now;
  the held-out figures were not re-taken.
- `transform`: lane P saw its held-out rows. `made` and `transform` were judged by the benchmark's reader before fix
  lane F2 made it read only what a person would see.
- The relater and the pipeline changed after their held-out runs (fix lanes F1, F3); the planner did not (frozen at
  `4388f4d`, judged once more only on the withheld cases).

## The withheld looks (taken once each, on `55f89be`)

Taken by the main session on 2026-11-27, once per mechanism, on the clean commit `55f89be`, which holds the rule
([../quality/OMNIMIND_BENCHMARK.md](../quality/OMNIMIND_BENCHMARK.md), "The rule for the withheld looks") and the
driver pins. Ledger: `docs/quality/evidence/omnimind_final_uses.jsonl` (42 lines). Evidence: every look and control in
`docs/quality/evidence/omnimind_looks/`; the main session's reading of each by the committed rule is `summary.json`,
whose figures and verdict words are copied here. A "not supported" is never retried on these cases.

**Preconditions: held.** The tree was clean; development reproduced exactly at `55f89be` (artifacts 512 / 12 / 0 / 0
of 524; forms 107 good of 133, 0 bad, 0 violations; cross 72 of 72; learn 0.8158 / 0.7617 against the planner alone
0.5871 / 0.5842).

**Controls: held for the seven families and cross; two were not run for forms and learn.** Today's system on the seven families: 0 failed outside hostile and
nothing made (made 0 of 16, transform 0 of 11). Always-consistent on cross: 39 of 78, 0 of 39 twins. Learn: oracle
0.891 >= key_majority 0.831 >= never-learns 0.444. Not done: the forms oracle `first_good` could not be run through
`final` (`scripts/bench_omni_forms.py` refuses it as a `final` subject) and "reference reruns identical" was not
checked. The refuse-everything control of the seven families was dropped before the looks by the main session's ruling
(it holds by construction).

**Correction (2026-11-27, the main session's, after the measurement review's third pass, finding 2).** By the letter
of the committed rule a control that was not run voids that family's look. Two controls of the forms and learn
looks were not run: the forms oracle `first_good` (`scripts/bench_omni_forms.py` refuses it as a `final` subject,
although the committed rule said it could run) and "every reference run twice gives identical figures". The
statement that the oracle's absence was "stated before the product looks" cannot be verified: a refused `final`
writes no ledger line. So the forms and learn looks are **VOID by the rule (two controls were not run); the figures are reported as measured and establish nothing under the rule**. The seven-family and cross verdicts stand: their
controls ran.

**The seven families** (`bench_omni.py final --subject artifacts`; right / "cannot" / wrong / failed of n):

| family | n | result | verdict by the rule |
|---|---:|---|---|
| intake | 358 | 347 / 11 / 0 / 0 | supported on unseen cases |
| honest | 55 | 52 / 3 / 0 / 0 | supported on unseen cases |
| **unknown** | 111 | 107 / 1 / **3** / 0 | **not supported: 3 wrong answers** (disguised or unknown files answered falsely) |
| compound | 66 | 64 / 2 / 0 / 0 | supported on unseen cases |
| **hostile** | 35 | 32 / 0 / **3** / 0 | **not supported: 3 hostile files answered instead of refused; "hostile inputs refused" does not hold** (8 of the withheld constructions belong to 7 classes no earlier split has) |
| made | 16 | 16 / 0 / 0 / 0 | supported on unseen cases |
| transform | 11 | 11 / 0 / 0 / 0 | supported on unseen cases |
| **all** | **652** | **629 / 17 / 6 / 0** | today's system: 72 right, 2 wrong, 3 failed |

**These six wrong answers are the most important result of the looks**: on inputs nobody tuned against, the system
fabricated (the unknown family) or failed to refuse (the hostile family), which it never did on development or
held-out.

**forms** (the frozen planner `4388f4d` through `choose_form`, learning off; 150 cases):

| subject | good | good or fine | bad | words slice good | request pairs both good | twins both good |
|---|---:|---:|---|---|---|---|
| the planner | **0.687** | 0.927 | 0.073 (too poor 3, too much 4, unfit 4); 0 violations | **35 of 52** | **20 of 40** | **11 of 25** |
| shape-only table | 0.480 | 0.773 | 0.227 (too much 22) | 18 of 52 | 14 of 40 | 0 of 25 |
| always prose (today) | 0.440 | 0.820 | 0.180 (too poor 21) | 27 of 52 | | |

Verdict: **VOID by the rule (two controls were not run); the figures are reported as measured and establish nothing under the rule** (correction of 2026-11-27, above). As measured, the figures would have met the
"supported" thresholds (good 0.687 >= 0.480 + 0.10; 0 violations; bad and too-much below the shape-only table's) and the
"reads the words" thresholds (words slice 0.673 against 0.346 and 0.519; pairs 20 against 14 of 40); explicit forms
honoured 6 of 7; no unmapped name. They establish nothing under the rule. Caveats: the labels are one model-written examiner's; no second reader labelled these cases, so
there is no ceiling to quote; about 30 of the 150 are close calls by the examiner's own count.

**cross** (the relater; 78 cases):

| kind | result |
|---|---|
| chart_series, mesh_bbox, code_trace, timeline_log | 8 of 8 each |
| tones_rows | 6 right, 2 cannot of 8 |
| report_total | 5 right, **1 wrong**, 2 cannot of 8 |
| image_caption (a kind only this split has) | **answered all 10, right on 5: no better than a coin** |
| route_stops, archive_manifest (kinds only this split has) | 0 answered of 10 each: not exercised |
| **all** | **48 right, 24 cannot, 6 wrong**; correct of answered 0.889; both twins 21 of 39 |

Verdict: **not supported** (6 wrong, more than 2; 0.889 below 0.95; 48 below 58.5; twins 21 below 26; one kind with 5
wrong). The image-against-caption answers are exactly the fabrication the relater's design warned of: a "consistent"
or "inconsistent" given where nothing could be measured.

**learn** (SIMULATED people; late kept / all kept):

| subject | late | all |
|---|---:|---:|
| the shipped per-key majority through the planner | **0.713** | **0.656** |
| the planner alone | 0.440 | 0.448 |
| never learns (reference) | 0.444 | 0.454 |
| key_majority (reference) | 0.831 | 0.756 |
| oracle (reference) | 0.891 | 0.894 |

Verdict: **VOID by the rule (two controls were not run); the figures are reported as measured and establish nothing under the rule** (correction of 2026-11-27, above). As measured: gains 0.273 and 0.208 against
thresholds 0.088 and 0.039; **0.118 below the reference majority rule** late; `form_learning` stays off.

**What these looks cannot establish** (the rule's part C): anything about real files, real people, the owner's taste,
visual quality or usability; any version after `55f89be`; formats or kinds absent from the tables; generality from
small n (made 16, transform 11); and a second look at the same cases, which would not be a fresh measure. **The
unknown, hostile and cross findings are known weaknesses now: they are to be fixed and examined afresh on NEW withheld
cases, never on these.**

**What the first withheld split can still measure.** It is spent for identification, refusal and the relater: the
fix wave (lane FX) read the look summary and WAVE2.md, written by the main session, which named the cross kind
answered at coin level, so re-scoring these cases would measure the fixes against the cases that motivated them;
only the second withheld split can measure them (measurement review, third pass, finding 5). Lane FX's own corpus
figures (wrong answers 344 to 0 of 3,669 disguises, 132 to 0 of 168 hostile parts inside honest files, 24 to 0 of
144 relater twins) are regression evidence for the classes it named, written by the same lane that wrote the fixes,
not evidence of generality (finding 6).

## The look at the second withheld split (taken once, on `ae6120f`)

Evidence: `docs/quality/evidence/omnimind_looks2/`; rule: [../quality/OMNIMIND_BENCHMARK.md](../quality/OMNIMIND_BENCHMARK.md).

**Preconditions and controls: held.** The rule was committed in `ae6120f` before the look; the driver pins matched; the
tree was clean; development reproduced exactly (512 / 12 / 0 / 0; forms 107 good, 0 bad, 0 violations; cross 72 of
72; learn 0.8158 / 0.7617 against 0.5871 / 0.5842). Every control was run twice; the two runs' result figures are
identical (they differ only in times, notes, ledger line counts and one traced peak-memory figure) and equal the rule's
table: unknown today 25 / 145 / 0 / 1 of 171; hostile today 4 / 56 / 1 / 1 of 62; cross today 18 of 98, 0 answered;
always-consistent and always-inconsistent 40 of 98, twins 0 of 49. The full validation on `ae6120f` was green except
one browser test (`tests/e2e/test_ui_matrix.py::test_pocket_matrix[dark]`, a known flake on the base commit too),
which passed alone twice.

| family | n | result | verdict by the rule |
|---|---:|---|---|
| **unknown** | 171 | 158 right, 8 "cannot", **5 wrong**, 0 failed | **not supported** (the rule requires 0 wrong) |
| **hostile** | 62 | 51 refused, **11 answered instead of refused**, 0 failed | **not supported**; "hostile inputs refused" does not hold |
| **cross** | 98 | 65 right; 48 answered, **1 wrong** (correct of answered 0.979); "cannot" on all 18 undecidable cases; decidable: 47 right of 80; both twins right on 23 of 40 decidable pairs | **not supported**: it no longer guesses, but it is right on only 47 of the 80 decidable cases (the rule needs 60) and on both twins of 23 decidable pairs (needs 27) |

Cross by kind: report_total 9 right, 1 wrong of 10; chart_series 4 right, 4 cannot of 8; tones_rows 6 of 6; mesh_bbox,
code_trace, timeline_log 8 of 8 each; image_size_caption (new) 4 right, 4 cannot of 8; image_depicts and translation
(new, undecidable) "cannot" on 10 of 10 and 8 of 8, as expected; diagram_text, archive_checksums and audio_cues (new)
0 answered of 8 each: **not exercised**. In plain words: the relater stopped guessing, and it declines too much to
count as working.

The rule asked for any hostile class with a wrong answer to be named by class: **the look's output carries no
per-class breakdown, so the classes cannot be named.**

**The honest reading.** The fix wave closed the classes it named (its own corpora), and those fixes did not
generalise to new constructions. On unseen inputs the wrong answers went from 3 to 5 for unknown files and from 3 to
11 for hostile files; this is **not a like-for-like comparison** (different splits and sizes: 111 and 35 cases then,
171 and 62 now). The main session's ruling in lane FX2 (an outside reference or embedded active content is named,
never followed or run, and does not refuse a file by itself) was made on principle before the look and may account
for part of the hostile count; this is not known, because no task is opened. **Both withheld splits are now spent for
these families**: any further fix needs a third set of unseen cases, written first. **The owner's condition for
merging (fix what the look found and re-examine on new withheld cases) was examined and is NOT met**; the pull request
stays unmerged pending the owner's decision.

What this look cannot establish (the rule's part C): real files or real people; kinds and classes not in the split;
versions after `ae6120f`; generality from 98 or 171 cases; anything about forms, learn, made or transform.
