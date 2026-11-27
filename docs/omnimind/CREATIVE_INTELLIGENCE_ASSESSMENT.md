# The form planner as measured (deliverable 4: "Creative Intelligence Assessment")

Status: 2026-11-27, written on `omni-docs2` from `omnimind` at `09bfff6`. The directive names this deliverable an
evaluation of "imagination, synthesis, conceptual transfer, and output selection". This page reports what was built
and measured for each, with the figures from committed evidence files. **"Creativity" is not measured by anything
in OMNIMIND, and no claim about it is made here.** What exists is a deterministic function that chooses the form of a
result from the content's shape, the request's words and the context, and says why; the owner called it "Creative
Output Intelligence". It is measured as a chooser of forms against an examiner's labels.

## The first screen

- **By the rule written before its one held-out run, the planner does not beat the references.** It made one
  hard-constraint violation (a form the device or budget could not carry); the rule required none.
- **It was overfit to development**: good on 0.850 of development cases after tuning, 0.659 on held-out (a drop of
  0.19, over the 0.15 the rule allowed).
- **There is no evidence that it reads the request's words.** On held-out, answering every request in sentences
  (today's system) beat it on the cases where the words decide the form (17 against 16 of 34) and on the cases where
  the words and the content conflict (7 of 7 against 4 of 7); the table that reads only the content's shape beat it on
  request pairs (both members right: 8 against 3 of 18).
- **Its estimates of time are 5 to 560 times too high** (median 102) against what making took.
- **No person judged any choice.** The examiner's labels and the "second reader" are two readers of one model
  family, not the owner and not people.
- **Nothing calls it on the answer path.** It decides forms only for the owner's commands (`nsf form plan`, `nsf
  artifact make`, `remake`) and the console's Make button. Every answer the system gives in conversation is still
  sentences.

## What it is

`artifacts/forms.py`, `forms.plan(request, content, context, adjustments=None) -> FormPlan`, deterministic, no
model ([ADR-073](../architecture/ARCHITECTURAL_DECISIONS.md)). It scores registered forms (prose, spoken summary,
table, document, data file, six chart kinds, timeline, node-link and tree diagrams, flow, step-through, explorable
model, animation, sonification, tone, sound view, 3D viewer, surface view, picture, and pairs of a non-text form with
a second form) on seven weighted criteria, after hard constraints (device media, voice only, screen reader, reduced
motion, an available generator, budget, refusals, the cost floors). Its reason is one sentence that names what
decided: the content, the words, the context or an explicit request. It is frozen at `4388f4d`.

## The measurements, in the order they were taken

`scripts/bench_omni_forms.py --family forms`: 133 development and 138 held-out cases written by lane ZF before the
planner existed. A case is "good", "fine" or "bad" (too poor, too much, unfit, or a hard-constraint violation) by
the examiner's labels. Sources: `omnimind_baseline_forms.json` (references), `omnimind_heldout/dev_blind_8d84f29.json`
(blind), `omnimind_heldout/final_c5d3230.json` (held-out), `omnimind_int2_development.json` (frozen, development),
`omnimind_forms_second_reader.json` and `omnimind_heldout/reader2-agreement.json` (second reader).

| step | what was run | good | good or fine | bad | violations |
|---|---|---:|---:|---:|---:|
| references, development | always prose (today's system) / shape-only table | 0.399 / 0.459 | / 0.805 | 0.271 / 0.195 | 0 / 0 |
| 1. blind, development (`8d84f29`, scored by the main session before the planner's author saw any case) | the planner | 65 of 133 (0.489) | 0.805 | 0.195 (too poor 6, too much 15, unfit 5) | 0 |
| 2. tuned, development (`c5d3230`, every rule changed after reading the development cases: optimistic) | the planner | 113 of 133 (0.850) | 133 of 133 | 0 | 0 |
| 3. **the one held-out run** (`c5d3230`) | the planner | **91 of 138 (0.659)** | 0.920 | 0.080 (too poor 4, too much 5, unfit 1, violation 1) | **1** |
| references, held-out | always prose / shape-only table | 0.442 / 0.500 | 0.739 / 0.804 | 0.261 / 0.196 (too much 20) | 0 / 0 |
| 4. the limited round, development (`4388f4d`, frozen) | the planner | 107 of 133 (0.805) | 133 of 133 | 0 | 0 |
| ceiling | a second reader's first choice in the examiner's good set | 0.902 dev, 0.877 held-out | 0.993 (both) | 2 of 271 | |

Blind, the planner scored about what the shape-only table scores: weak where the words decide (10 of 36), on request
pairs (6 of 18) and on twins (0 of 9).

**The held-out verdict, by the rule written before the run** (the main session's log, quoted in the held-out record):

| part of the rule | condition | result |
|---|---|---|
| (1) "beats the references" | good >= 0.600 AND bad <= the shape table's AND too much <= the shape table's AND no hard-constraint violation | **no**: 0.659 >= 0.600, 0.080 <= 0.196, 5 <= 20, but **1 violation** |
| (2) "overfit to development" | development good - held-out good > 0.15 | **yes**: 0.850 - 0.659 = 0.190 |
| (3) "learned from the words" | words slice >= the shape table's + 0.15 AND pairs and twins above the shape table's | **no**: 0.471 against 0.412 (+0.059); pairs 3 of 18 against 8 of 18 |

**Where each subject was good, held-out** (`final_c5d3230.json`, `slices` and `pairs`):

| slice | the planner | always prose | shape-only table |
|---|---:|---:|---:|
| the content decides (81) | **57** | 35 | 49 |
| the words decide (34) | 16 | **17** | 14 |
| the words and the content conflict (7) | 4 | **7** | 1 |
| an explicit form is named (16) | **14** | 2 | 5 |
| request pairs, both good (18) | 3 | 0 | **8** |
| twins, both good (10) | **2** | 0 | 0 |

Read plainly: the planner's gain over both references comes from cases where the content's shape decides and from
explicit requests (it honours a named feasible form 10 of 11 times). Where the request's words alone should change
the form, it does no better than sentences, and on pairs of requests with the same words and different right
answers it does worse than the shape table. Three held-out purpose words were not known to it (passed as none).

**The limited round and the freeze.** After the held-out run the planner's author was given no held-out detail and
was allowed two things: make violations impossible by construction, and accept unknown purposes. Every form now
declares a cost floor and a final gate re-checks every answer against the media shown and the summed floors: a
random search with the benchmark's own rule found 1,140 violating views in 5,000 on `c5d3230` and 0 in 20,000 after.
Rules suspected of fitting the development cases were deleted (topic purpose cues, the "show me" richness wish, the
caption rule, the "full picture" idiom, the process exclusion, the document outline): development fell from 112 to 107
good with 0 bad, pairs 13 of 18 and twins 9 of 9. The measurement review (first pass, N2) found no word added by the round that occurs only in
held-out requests. That development figure is again optimistic. The frozen planner `4388f4d` has not been measured on unseen
cases: it is judged once, on the withheld forms cases, by the rule committed in
[OMNIMIND_BENCHMARK.md](../quality/OMNIMIND_BENCHMARK.md) ("supported on unseen cases" needs 0 violations, good at
least the shape table's + 0.10, bad and too-much no higher than the shape table's; "reads the request's words" needs
the words slice at least the shape table's + 0.15 and at least always-prose's, and pairs above the shape table's).

**The second reader, and what it is.** A second model reader labelled all 271 views (shuffled, ids stripped) without
seeing the examiner's labels. Its first choice falls in the examiner's good set on 0.889 of cases (development 0.902,
held-out 0.877), in good-or-fine on 0.993, in bad on 2 of 271. That says the labels are consistent between two
readers. Both readers are models of the same family; their agreement is not agreement with the owner's taste or with
people. No second reader labelled the withheld cases, so a withheld look has no ceiling to quote.

**P1.** When the context decides, the planner's sentence ends "it is the best form this situation allows"
(`forms.py:2048`). Nothing measures "best". The sentence stays until the withheld forms look (changing it changes the
frozen file), then is reworded (ADR-073, addition of 2026-11-27).

## Divergence and convergence, as implemented

- **Divergence**: `forms.alternatives(plan, n)` returns up to n admitted candidates, the choice first, each at least
  0.4 apart in kind from those already taken (media 0.35, family 0.35, encoding 0.15, interactivity 0.15), so two
  charts never count as two different ideas.
- **Convergence**: `nsf artifact make --alternatives N` (at most 4) builds, makes, checks and repairs each; it keeps
  the one with a passing technical check first, then the most source values read back, then the planner's total,
  then the simpler form. The others stay in the production record as their specification's envelope; `remake --form`
  makes any of them, `--keep` stores those that passed (ADR-076).
- **Measured**: that the candidates differ in kind and that convergence follows that order (lane P's tests). Whether
  an alternative is ever more useful to a person than the first choice is **not measured**.

## Foresight against outcome

Before anything is made, `forms.foresee` predicts bytes, seconds, whether the budget is exceeded, and problems a check
can observe (label overlap, too many series or categories, points per pixel, too many nodes, a sound too long). The
pipeline pairs each prediction with what the made bytes and their check showed.
- **Seconds**: on 183 productions (lane P's corpus, three runs each), predicted / observed is 4.9 to 560, median 102,
  never under (`omnimind_int2.json`). Per form, median: explorable 560, solid viewer 559, node-link 334, data file 250,
  tree 134, table 125, picture 116, document 100, scatter 95, bar 83, small multiples 80, histogram 59, line 39, sound
  view 15, timeline 9, sonification 5. The error can only make the planner more cautious about slow forms; it can
  never let a production run past its budget, which the pipeline enforces on the clock. The planner is frozen, so the
  error is recorded, not corrected.
- **Bytes and budget** (`omnimind_p.json`, `foresight`): on lane P's corpus bytes were right within a factor of two in
  8 of 10 comparisons (1 over, 1 under), "over budget" right in 10 of 10, one false alarm on points per pixel; on the
  development transform tasks bytes right 5 of 6 (1 under), over budget right 6 of 6.
- The pairs are stored in the production record. Since fix lane F3 they go into the imagined store as a SIMULATION
  record only when `imagine_scenarios` is on (off by default).

## The other three words of the directive

- **Imagination.** Nothing here imagines. The pre-production prediction above is arithmetic on counts. No artifact
  is invented without a source: every value a made thing shows comes from a decoded source.
- **Synthesis.** With two or more sources, `nsf artifact make` plans each, places them in one document, and adds a
  section "How the sources relate" (the relater's verdict per pair, both values where they disagree, where each comes
  from, what could not be compared; nothing resolved). Measured: by tests on built pairs and triples (lane int2's
  end-to-end case: a CSV summing to 500 against a report stating 520 is reported as a disagreement). Not measured on a
  benchmark family; no real-world multi-file project was made.
- **Conceptual transfer.** A unit that a document states for a table's column travels to the made chart's axis label
  and the table's header, with where it was read ("degC, from README.md"). Measured on lane R's own 12 cases: 29
  columns, 14 right units, 14 rightly without a unit, 1 absent, 0 wrong (`omnimind_r.json`). Nothing else transfers
  between modalities.

## What this does not establish

That any choice suits the owner; that a made picture is legible or beautiful (no person looked; the benchmark reads
values back); that the planner reads requests (the evidence says it does not, beyond grammar and explicit names);
anything about creativity, imagination or invention. A withheld look can at best establish that the frozen planner
stays within the rule's bounds on cases nobody tuned against.
