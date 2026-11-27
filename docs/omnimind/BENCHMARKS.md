# OMNIMIND benchmarks: before and after (deliverable 7)

Status: 2026-11-27, written on `omni-docs2` from `omnimind` at `09bfff6`. Every figure is copied from the committed
evidence file named beside it; nothing was re-run for this page. The examinations themselves, their rules and their
readers are in [../quality/OMNIMIND_BENCHMARK.md](../quality/OMNIMIND_BENCHMARK.md) (taking things in, making things)
and [../quality/OMNIMIND_BENCHMARK_FORMS.md](../quality/OMNIMIND_BENCHMARK_FORMS.md) (choosing a form, relating two
artifacts, learning from outcomes). Every held-out run, its rule quoted verbatim and its verdict:
[../quality/evidence/omnimind_heldout/README.md](../quality/evidence/omnimind_heldout/README.md).

## The first screen: what failed, and what the figures are not

- **One held-out making task the product could not do.** `made`: 13 of 14 on held-out against 14 of 14 on development.
  By the rule written before the run this is **not a pass** (a drop over 0.05).
- **One hostile held-out file was answered "cannot", not refused** (22 of 23 refused). Nothing wrong was answered,
  nothing was written, nothing crashed; but the rule asked for the named refusal. That outcome came through the
  earlier driver, which mapped the product's error to "cannot" by its words (review finding B4, since fixed).
- **The form planner does not beat the references on held-out** by the rule written before its run: one
  hard-constraint violation (since made impossible by construction), a development-to-held-out drop of 0.19, and no
  evidence that it reads the request's words.
- **The learner is below the benchmark's own majority rule** on held-out (0.710 against 0.802 late), with simulated
  people only.
- **No withheld look has been taken.** The rule for them is committed
  ([OMNIMIND_BENCHMARK.md](../quality/OMNIMIND_BENCHMARK.md), "The rule for the withheld looks"); the drivers are not
  yet pinned, so every look is refused today.
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
with a MemoryError on an `.npy` declaring terabytes. (The held-out record's README and the main session's log say
"55 right (52 of them the floor)": the baseline file gives 30 floor tasks right, not 52; the file wins.)

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
| the planner frozen (`4388f4d`, after the limited round) | 0.805 (107 of 133) | 0 | not run (judged next on the withheld cases) | | |
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

## The withheld split

Written by lane ZW without reading product code (652 tasks of the seven families, 150 form cases, 78 cross cases, 6
simulated people), pinned by SHA-256, and scored only by `final`, which appends a ledger line
(`docs/quality/evidence/omnimind_final_uses.jsonl`) before it loads anything. The ledger's 17 lines are lane ZW's
machinery check through the references; **no product look has been taken**. The rule for the looks is committed; the
driver pins are null, so every look is refused until the main session pins them after fix lane F4. The results
belong in [FINAL_REPORT.md](FINAL_REPORT.md), "The withheld looks".
