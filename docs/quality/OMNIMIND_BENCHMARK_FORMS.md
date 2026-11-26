# OMNIMIND benchmark: choosing a form, cross-modal questions, learning from outcomes

The examination for three things that **do not exist yet** in NeuroSciFi: a planner that decides the form of a result
(DESIGN section 5, "Creative Output Intelligence"), a function that answers a question needing two artifacts of
different kinds, and a learner that adjusts form choices from the owner's reactions. It was written before any of
them (lane ZF), so that their authors cannot tune the examination to the product. Only the benchmark's own reference
subjects have been run; the "today" rows are the unchanged system.

- Code: `src/neuroscifi/acceptance/omni/` — `forms.py` + `forms_cases.py`, `cross.py` + `cross_cases.py`,
  `learn.py` + `learn_people.py`. Standard library only. Nothing there imports product code, and product code never
  imports from there (`tests/acceptance/test_omni_forms_bench.py` checks the import statements).
- Command: `scripts/bench_omni_forms.py`.
- Frozen tables: `forms_cases.py`, `cross_cases.py`, `learn_people.py`, each committed on its own before its scorer;
  `tests/acceptance/test_omni_forms_bench.py` pins their SHA-256 digests and a digest of every byte the cross
  generators produce.
- Baseline: `docs/quality/evidence/omnimind_baseline_forms.json` (90,330 bytes; summaries only: per split and
  reference, the measures with their denominators and slices; no per-case rows).
- Tests: `tests/acceptance/test_omni_forms_bench.py`, `test_omni_cross_bench.py`, `test_omni_learn_bench.py`.

Everything is offline and deterministic. No model is called. The `learn` family's people are **SIMULATED**.

## How to run

```bash
PY="PYTHONPATH=$PWD/src .venv/bin/python"
$PY scripts/bench_omni_forms.py list                                         # the reference subjects
$PY scripts/bench_omni_forms.py stats                                        # table counts, wording statistics
$PY scripts/bench_omni_forms.py run --family forms --split held_out --subject shape_only
$PY scripts/bench_omni_forms.py run --family cross --split held_out --subject mypkg.drive:relate --out /tmp/x.json
$PY scripts/bench_omni_forms.py run --family learn --split held_out --subject mypkg.drive:make_learner
$PY scripts/bench_omni_forms.py baseline --evidence --out docs/quality/evidence/omnimind_baseline_forms.json
```

`--subject dotted.module:attribute` plugs a product driver in: for `forms` a chooser (`choose(view)` or a callable),
for `cross` a relater (`relate(files, question)` or a callable), for `learn` a zero-argument factory of fresh
learners. `--out` refuses a path inside the checkout unless `--evidence` is given. The driver that maps the product
planner's form names onto this benchmark's vocabulary is the main session's to write (this lane may not import product
code); it must map names only, never re-decide a form.

## Rules for a lane that uses it

1. Tune on `development`; report on `held_out`. Never edit the three tables: add cases in a new file.
2. Report every measure with its denominator, never one blended number. For `forms`: share good, share good or fine,
   share bad split into too poor / too much / unfit / violation, explicit requests honoured, hard constraints
   violated (**must be 0**), and the same for the references.
3. A planner that does not beat **content shape only** on held-out has learned nothing from the words. A learner that
   does not beat the **per-key majority** by more than the visible gain (below) has not shown a better learner.
4. Nothing a subject produces is ground truth: every truth here was written by the examiner or built by a generator
   before any subject ran.

## Family 1: `forms` — is the chosen form right?

### A case

A person's request in their own words, the shapes of the content the system has for it (DESIGN section 2
`ContentShape` written as plain dicts: kinds, n_items, n_series, has_time, has_space, has_parameters, duration_s,
text_chars), the context (device; what it can show; accessibility needs; stated preferences; budget in seconds and
bytes), the form the person named if any (`explicit`), and a purpose where one is known. The examiner's judgement is
five sets, each with a one-line reason: **good** (serves the person well), **fine** (acceptable), **too poor** (three
paragraphs for 365 daily values), **too much** (an interactive simulation for the boiling point of water), **unfit**
(the wrong kind for the content: a node-link diagram for a temperature series). A case names which of these labels
every form it does not list gets (`rest`; default unfit). The subject sees the case without the judgement
(`id, request, explicit, purpose, content, context`).

### The vocabulary (the benchmark's own names)

prose, spoken (a summary written for the ear), table, data_file, line_chart, bar_chart, scatter_chart, histogram,
heatmap, timeline, node_link, flow, map, image_view (a picture shown or derived: crop, edge map, rendered view),
waveform_spectrum, step_through (frames the person steps through), animation (frames that play by themselves),
explorable (a model with sliders), sonification, solid_viewer — and combinations of two written `a+b`. It follows
DESIGN section 5's Wave O-0 list plus map, image_view and animation, which real requests need (a map of 85 stations,
a crop of a scan, the directive's "animated" examples).

### Hard constraints (decided from the context, not from the sets)

A chosen form is a **violation** when a part needs something the context cannot show (`shows`: text, picture,
sound, interaction, motion, 3d, file; prose may be spoken, so it needs text *or* sound); when `voice_only` leaves
only sound; when `no_sound` removes sound; when `reduced_motion` removes motion (animation); when a screen-reader user
would get a picture with no text-carrying part (prose, spoken, table, data_file); or when the sum of the parts' cost
floors exceeds the budget. The floors are deliberately low (a chart 0.2 s and 3,072 bytes, a table 200 bytes + 8 per
value, explorable 20,480 bytes ...; `forms.FORMS`): a planner whose own estimate is higher may exclude more, which is
never counted against it. The test checks that no good or fine form of any case is a violation.

### Labels of a combination the examiner did not list

A too-much part makes it too much; all parts good or fine makes it fine; a good or fine part with too-poor parts makes
it fine (the rich part carries the content, the poor one captions it); a good or fine part with an unfit part makes it
too much; otherwise unfit if any part is unfit, else too poor. Unknown names are unfit.

### Explicit requests

A feasible named form is **honoured** when the chosen form is it or contains it. An infeasible one (a chart on a
voice-only speaker, a picture on the terminal, a 3D viewer over budget, an animation for someone with reduced motion)
is **handled** when the chosen form is one the examiner judged good for it (the nearest feasible form, with the plan
saying why); choosing it anyway is a violation.

### The tables

| | development | held-out |
|---|---|---|
| cases | 133 | 138 |
| prose or spoken is good | 64 (48%) | 70 (51%) |
| prose alone is good | 53 (40%) | 61 (44%) |
| explicit requests (infeasible / asking for less) | 15 (5 / 3) | 16 (5 / 3) |
| accessibility (screen reader / voice only / reduced motion / no sound) | 4 / 2 / 4 / 1 | 4 / 2 / 3 / 1 |
| tiny budgets | 4 | 5 |
| devices console / pocket / voice / cli / agent | 85 / 31 / 9 / 4 / 4 | 80 / 43 / 6 / 5 / 4 |
| content kinds | all 18 | all 18 |
| cue pairs (same words, different content, different answer) | 18 | 18 |
| twins (same content and context, different words, different answer) | 9 | 10 |
| directive section 7.2 examples / counter-examples | 7 / 7 | 7 / 6 |

Every case was written by hand, one at a time. Requests are what a person would type or say ("And the cellar?",
"Simple answer please: how did the twelve branches do this quarter?", "Map out my week"), and the right answer is prose
wherever a sentence serves the person best, including when the content is large (one hot day asked of 365 values).

### The wording discipline and its statistics

The planner's author will write rules over request wording, so the requests were not generated from a verb list, and
the tables are built so that words alone cannot decide:

- **Cue pairs: 36** (at least 30 required). Each pair shares cue words ("how warm did it get in the greenhouse",
  "break down", "walk me through", "show me what changed", "where", "map out", "trend", "inside" ...) and has
  disjoint good sets, because the content differs. The test checks the words are in both requests.
- **Twins: 19.** Same content and context, disjoint good sets, because the words differ ("Was there any day above 35
  degrees last summer?" against "How did the temperature move through the year?" over the same 365 values).
- **Cue-word purity.** For every word in at least 6 of the 271 requests (52 words), the largest share of those
  requests for which one same whole form is good — what the best rule "this word → that form" would score on them:
  the maximum is **0.857** ("has", "was" → prose, against prose's base rate of 0.421); every word with purity of 0.7
  or more points to prose, the commonest right answer. The strongest cue to another form is "read" → spoken (4 of
  its 6 requests, after two counter-cases were added), and among rarer words (3 to 5 requests) "scan" → image_view
  (3 of 4). `forms.wording()` prints the full list.
- **A words-only chooser** (each held-out request takes the first good form of the development request sharing the
  most words) is good on **38 / 138 (28%)** of held-out — below always-prose (44%).
- **How much can be decided without the words?** Three answers, none of them a single number to trust alone:
  1. The examiner's own cue tags: **158 / 271 (58%)** of cases are tagged `content` (the shape and context decide;
     the words give no cue to the form), 70 `words`, 12 `conflict`, 31 `explicit`.
  2. A words-blind **upper bound**: the best single form for each group of cases identical once the words and the
     named form are removed is good on **247 / 271 (91%)** (exact features) or **230 / 271 (85%)** (coarse buckets of
     size, kind and context). It is loose, because most cases have content no other case shares; it is bounded below
     100% by the twins and the explicit requests, where the words are necessary.
  3. The **content shape only** reference (kind → form, no words) is good on 61 / 133 (46%) of development and
     69 / 138 (50%) of held-out.

### Baseline (reference subjects; today's system is "always prose")

`today` always prose (the unchanged system answers every request with sentences); `richest` always the richest form
the context can carry (`forms.RICHNESS`), whatever the request; `random` a uniformly random feasible single form (the
exact expectation, so fractional counts); `shape_only` the content's dominant kind decides through `forms.SHAPE_ONLY`
(eighteen lines, no words).

Held-out (138 cases):

| subject | good | good or fine | bad | too poor | too much | unfit | violations | explicit honoured | infeasible handled | cue pairs both good | twins both good |
|---|---|---|---|---|---|---|---|---|---|---|---|
| today (always prose) | 61 (0.442) | 0.739 | 0.261 | 0.203 | 0.000 | 0.058 | 0 | 2 / 11 | 0 / 5 | 0 / 18 | 0 / 10 |
| richest | 11 (0.080) | 0.116 | 0.884 | 0.000 | 0.725 | 0.159 | 0 | 1 / 11 | 2 / 5 | 0 / 18 | 0 / 10 |
| random | 14.4 (0.104) | 0.222 | 0.778 | 0.036 | 0.358 | 0.384 | 0 | 1.1 / 11 | 1.4 / 5 | 0.06 / 18 | 0.03 / 10 |
| shape_only | 69 (0.500) | 0.804 | 0.196 | 0.007 | 0.145 | 0.043 | 0 | 3 / 11 | 2 / 5 | 8 / 18 | 0 / 10 |

Development (133 cases):

| subject | good | good or fine | bad | too poor | too much | unfit | violations | explicit honoured | infeasible handled | cue pairs both good | twins both good |
|---|---|---|---|---|---|---|---|---|---|---|---|
| today (always prose) | 53 (0.398) | 0.729 | 0.271 | 0.226 | 0.000 | 0.045 | 0 | 3 / 10 | 0 / 5 | 0 / 18 | 0 / 9 |
| richest | 9 (0.068) | 0.090 | 0.910 | 0.023 | 0.714 | 0.173 | 0 | 1 / 10 | 1 / 5 | 0 / 18 | 0 / 9 |
| random | 14.6 (0.110) | 0.239 | 0.761 | 0.041 | 0.326 | 0.394 | 0 | 0.5 / 10 | 1.5 / 5 | 0.04 / 18 | 0.03 / 9 |
| shape_only | 61 (0.459) | 0.805 | 0.196 | 0.008 | 0.128 | 0.060 | 0 | 3 / 10 | 3 / 5 | 6 / 18 | 0 / 9 |

What the references say: always-prose is right where prose is (it is good on 87% of the held-out cases whose good set
holds prose or spoken, and on none of the others); the richest form is almost always too much; content shape alone is
right on half the cases and gets no twin; no reference honours explicit requests, because none reads the words.

### What `forms` cannot show

- That the examiner's sets are right. They are one careful person's judgement, with a reason for each; another
  examiner would move some forms between good and fine. The measures are robust to that only in aggregate.
- Whether the made artifact is good: this family judges the choice of form, not the chart (the artifact-making
  families of lane Z judge that).
- The words-blind statistics are properties of these 271 requests, not of how people talk in general.
- Conflict cases mostly resolve toward the simpler form (always-prose is good on 7 of the 7 held-out conflict cases);
  a planner could exploit that, so read the conflict slice beside the others, never alone.
- The cost floors are the benchmark's, not the host's; a budget violation here means "below any plausible cost",
  not a measured overrun.

## Family 2: `cross` — questions that need two artifacts of different kinds

Six kinds, six pairs per kind per split (72 cases per split), built by `cross.py` with the standard library from the
frozen pair table:

| kind | files | the one value the non-matching twin changes |
|---|---|---|
| report_total | a report (md, txt or docx assembled with zipfile) stating a total; a CSV (comma or semicolon, quoted thousands, decimal commas) | one CSV amount, or the stated total |
| chart_series | an SVG chart following DESIGN section 6's reading contract (line, bars or points; linear or log10 axis, at least three ticks); the CSV | one plotted value, or one CSV value |
| tones_rows | a 16-bit PCM WAV with one equal-length tone per row (pitch linear in the value) and short silences; the table | one row added to the table, or one tone dropped |
| mesh_bbox | a README stating the bounding box (size, min/max or in words); an OBJ mesh (box, wedge, L-bracket, cylinder; in mm, cm or m, stated in a comment) | one stated dimension, or one vertex moved outward |
| code_trace | a Python module; a JSON trace of calls (flat events or a call tree) | one call's callee replaced by a defined function the caller never calls, or by an undefined name |
| timeline_log | a timeline document (numbered or "First / Then / Finally"); a log (ISO, syslog or JSON lines) with unrelated lines interleaved | one event's timestamp, which moves it past the next event |

The truth is known by construction (matching → True). The test re-derives every truth from the bytes with readers of
its own (csv, zipfile and ElementTree, wave, ast, regular expressions), and checks that every pair's twins differ in
exactly one value. A trap is built in: where a chart carries `<metadata>` it holds the unchanged series, so a subject
that reads the metadata instead of the marks is wrong exactly when the marks changed (DESIGN forbids judging a picture
by its metadata).

Interface: `relate(files: {name: bytes}, question: str) -> {"answer": bool | None, "because": [...]}`; None is
"cannot tell". Measures: correct over all cases, answered, correct of answered, matching and non-matching separately,
both twins correct, with reasons, by kind.

| subject (each split: 72 cases, 36 pairs) | correct | answered | matching correct | non-matching correct | both twins |
|---|---|---|---|---|---|
| today (no such function: "cannot") | 0 / 72 | 0 / 72 | 0 | 0 | 0 / 36 |
| always consistent | 36 / 72 (0.5) | 72 / 72 | 1.0 | 0.0 | 0 / 36 |
| always inconsistent | 36 / 72 (0.5) | 72 / 72 | 0.0 | 1.0 | 0 / 36 |

The same on both splits. A subject that reads both files from the bytes scores 72 / 72 (the test's own readers do).
**Cannot show:** understanding of real-world files: these are clean, small, generated files whose formats the
generators chose; a relater can be right here and still fail on a real report. The `because` lines are counted, not
judged.

## Family 3: `learn` — does feedback improve later choices? (SIMULATED)

Six development and six held-out **simulated** people (`learn_people.py`), each a hidden, stable preference over
forms per content kind and device (charts where possible; tables over charts; the simplest thing always; cannot use
sound; charts at the desk and a sentence on the phone; explore anything interactive — and, held-out only, raw data
files; listening to data; charts only for time series and spreads; tables on the phone and interactive things at the
desk; tables at the desk and charts on the phone; an inconsistent person who seldom says what they wanted). No
held-out preference map appears in development (the test compares the full maps). Each person makes 8 cycles of 10
requests per seed, 10 seeds per split; each request is a content kind and a device with its candidate forms; the
person reacts as a real person could: **kept**, **dismissed**, or **replaced by** the form they wanted, with
inconsistency (10%, 25% for one held-out person). The reaction draws come from a per-request stream, the same for
every chooser, so choosers are compared seed by seed.

Interface: `choose(case) -> form` and `feedback(case, form, outcome)`, a fresh learner per person and seed; `case`
holds the kind, device, candidates and a content shape, never the preference. Measure: the share of choices the
person keeps, per cycle (denominator 600 per cycle: 6 people × 10 seeds × 10 requests), in the second half of the
cycles ("late"), and over all cycles ("all", which credits adapting fast), each with its sd over seeds.

| held-out | cycle 1 | cycle 8 | late kept (sd) | all kept (sd) | late gain over never_learns |
|---|---|---|---|---|---|
| today (always prose) | 0.420 | 0.413 | 0.393 (0.030) | 0.396 (0.025) | -0.040 |
| never_learns (the kind's usual form) | 0.418 | 0.447 | 0.433 (0.020) | 0.429 (0.024) | 0 |
| person_majority (one tally per person) | 0.595 | 0.732 | 0.715 (0.030) | 0.695 (0.022) | +0.283 |
| key_majority (tally per kind and device) | 0.500 | 0.818 | 0.802 (0.024) | 0.727 (0.014) | +0.369 |
| oracle (knows the preference) | 0.867 | 0.878 | 0.880 (0.019) | 0.878 (0.008) | +0.448 |

| development | cycle 1 | cycle 8 | late kept (sd) | all kept (sd) | late gain over never_learns |
|---|---|---|---|---|---|
| today | 0.430 | 0.407 | 0.403 (0.022) | 0.409 (0.015) | -0.178 |
| never_learns | 0.565 | 0.585 | 0.581 (0.017) | 0.579 (0.018) | 0 |
| person_majority | 0.672 | 0.727 | 0.757 (0.029) | 0.738 (0.021) | +0.175 |
| key_majority | 0.627 | 0.860 | 0.859 (0.027) | 0.794 (0.025) | +0.278 |
| oracle | 0.893 | 0.892 | 0.907 (0.025) | 0.902 (0.022) | +0.325 |

**The visible gain.** A difference between two choosers is visible when it exceeds 2·√2 × the larger sd over seeds of
the two. On held-out a learner must beat never_learns by more than **0.058** (late) / **0.067** (all) and the
per-key majority by more than **0.068** (late) / **0.067** (all). The per-key majority leaves room below the oracle
(0.727 against 0.878 over all cycles on held-out), because each person spreads ~80 requests over many kind-device keys:
a learner that generalises across keys (that a person who wants tables on one kind wants them on others) can show it.

**Cannot show:** benefit to a real person. This measures adaptation to simulated, stable preferences from simulated,
honest-but-noisy feedback; real preferences drift, depend on the task and are not one form per key, and real people
seldom give feedback at all. The people, their habits and their reaction rates are the examiner's inventions.

## Honest boundaries

- Nothing here was run against a product planner, relater or learner: none exists yet. Every number above is a
  reference subject's.
- The benchmark judges choices and relations against tables a person wrote and files a generator built. It is not a
  measure of intelligence, creativity or understanding.
- `today` in `forms` and `learn` is a stand-in for the unchanged system (it has no form choice: it always answers in
  sentences); in `cross` it is literal (there is no such function).
