# OMNIMIND final implementation report (deliverable 12)

Status: 2026-11-27, written by documentation lane 2 on `omnimind` at `21193c4`: fix lanes F4 and F5 merged, the
driver pins committed with the look rule in `55f89be`, the main session's final validation run on `55f89be`, and the
withheld looks taken once each on `55f89be` (evidence `docs/quality/evidence/omnimind_looks/`, the main session's
reading `summary.json`; ledger `omnimind_final_uses.jsonl`, 42 lines). OMNIMIND lives on the local branch `omnimind`
(schema 21); **nothing is merged into `main`, nothing is pushed, CI has not run.**

## The first screen

**On unseen inputs the system fabricated or failed to refuse.** In the withheld looks, which no lane tuned against,
the product gave **6 wrong answers** on 652 tasks of the seven families: **3 disguised or unknown files were answered
falsely** (`unknown`, not supported) and **3 hostile files were answered instead of refused** (`hostile`, 32 of 35:
"hostile inputs refused" does not hold). The relater gave **6 wrong answers** on 78 cross cases (not supported): on a
kind of pair no earlier split had, an image against its caption, it answered all 10 and was right on 5, no better
than a coin. None of this happened on development or held-out. These are the most important results of the
programme; by the rule they are never re-examined on these cases: they are known weaknesses, to be fixed and examined
afresh on NEW withheld cases. Also not shown: the planner's one held-out run did not beat the references (one
violation, overfit 0.850 to 0.659); one held-out making task could not be done (13 of 14); the learner stays below the
benchmark's own majority rule (withheld 0.713 against 0.831 late, simulated people). **No done-criterion is met
without qualification**; two are unverified (creative synthesis; knowledge accumulated across modalities). **Nothing
reaches the answer path**: every answer in conversation is still sentences; Pocket, voice and agents see no artifact;
no model sees or hears.

**What was supported on unseen cases, by the rule committed before the looks.** intake 347 of 358, honest 52 of 55,
compound 64 of 66, made 16 of 16, transform 11 of 11 (each 0 wrong, 0 failed). The form planner's and the
learner's looks are VOID by the rule (two controls were not run; figures reported as measured, establishing nothing under the rule): as measured, the planner's good was 0.687 against the shape-only table's 0.480 and
always-prose's 0.440, word-decided cases 35 of 52 against 18 and 27; the learner 0.713 against the planner alone's
0.440 late (simulated people). The first withheld split is now spent for identification, refusal and the relater. Seven families in all: 629 right, 17 "cannot", 6 wrong, 0 failed of 652 (today's
system: 72 right, 2 wrong, 3 failed). The main session's final validation at `55f89be` passed every check run
(8,472 passed in the non-browser suite, 235 in the browser suite, 0 different in the sentence guard, the offline digest
unchanged, acceptance 7 of 7, two accounts 56 of 56). What a look cannot establish: real files, real people, visual
quality or usability, any later version, formats absent from the tables, generality from small n.

**What exists.** The owner can hand NeuroSciFi a file (`nsf inspect`, `nsf artifact add`, the console's upload when
turned on); it is identified by its content, decoded within limits in a guarded child process, and kept beside the
individual with forget and restore reaching it. It can make 30 kinds of result, each read back by an independent
check before it is kept and marked "made by the system, not evidence"; it decides the form before making and says
why; it can relate two artifacts by measurement; it can learn which form the owner keeps (off by default). Only the
console's view of all this is on by default.

## Completed work

| wave | lanes | what they built |
|---|---|---|
| Phase I | four audits | the baseline ([CAPABILITY_AUDIT.md](CAPABILITY_AUDIT.md), "Before") |
| O-0 | S, K, A, B1, B2, C, G1 (+ G1b), G2, Z, ZF, ZW, docs-0; integration lane; the merge of `main` with Wave 4 | the audit's defects; detection, readers, the store, the planner, makers and checks, the examinations written first, the withheld split |
| O-1 | P, R, L, U; integration lane 2 | the production pipeline; relating artifacts; learning from outcomes; the console view |
| reviews | security, measurement, code (each with a second pass) | first pass: 4 blockers (measurement), H1-H2 (security), H1 and M1-M4 (code); second pass: all three "offer to the owner: yes", with residues |
| fixes | F1, F2, F3, F4 (`1aa7eb6`), F5 (`bf3dac6`) | the guard; the product decides outcomes; forget's matcher; the benchmark reads only what a person sees; driver pins machinery; `--partial` and reductions; plain words; F4: a forget holds what its scan cannot finish (and the scan's own budget), the product check's `visible_to_a_person`, the reader's two blind spots (re-pinned), the driver's last decisions in the product, `add` decoding once, console verdict reuse; F5: the make summary after a partial read, `--partial` on a large archive (drawn as its folders), `make` exit codes, the remaining non-plain words, the `artifact_max_bytes` help |
| Wave O-2 (fix what the withheld look found; the owner's decision of 2026-11-27) | FX, ZW2, FX2; third review passes | FX: a name or declared type never identifies a file alone; a hostile part refuses the whole file; the relater answers only quantities it can measure (ADR-080); its own corpora 344 to 0 wrong of 3,669 disguises, 132 to 0 of 168 hostile parts, 24 to 0 of 144 relater twins (regression evidence by the same lane, not generality). ZW2: a second withheld split (unknown 171 tasks, hostile 62, cross 98). FX2: one refusal rule (parser-level attacks refuse; outside references and active content are named, never followed or run); FX's refusal corpus 165 refused, 3 answered of 168 (the SVG-with-an-outside-image class, as ruled). **The look at the second withheld split is not yet taken** |

Details per row: [IMPLEMENTATION_MATRIX.md](IMPLEMENTATION_MATRIX.md); what exists with its limits and rollback:
[PRODUCTION_INTEGRATIONS.md](PRODUCTION_INTEGRATIONS.md); architecture: [ARTIFACT_ARCHITECTURE.md](ARTIFACT_ARCHITECTURE.md).

## Measured results

Every figure is from a committed evidence file ([BENCHMARKS.md](BENCHMARKS.md) names each one).

| measure | reference(s) | the product, development | the product, held-out | verdict by the rule written before the held-out run | withheld look (`55f89be`) |
|---|---|---|---|---|---|
| taking things in and making things (7 families) | today 55 right of 524 / 528; floor 52 (refuse everything); 29 (say "cannot" to everything) | 512 / 12 / 0 / 0 of 524 | 510 / 18 / 0 / 0 of 528 | no fabrication, no crash: holds; generalizes in five families; hostile refused: not fully (22 of 23); made: does not generalize (13 of 14); transform: holds, with lane P's disclosure | 629 / 17 / **6 wrong** / 0 of 652; supported: intake, honest, compound, made, transform; **not supported: unknown (3 wrong), hostile (3 answered instead of refused)** |
| of which: format identification / measurements / refusals / either / made read back | | 128 / 310 of 322 / 50 / 2 / 22 | 128 / 310 of 326 / 49 of 50 / 2 / 21 of 22 | | |
| choosing a form (good share) | always prose 0.399 / 0.442; shape-only 0.459 / 0.500; second reader 0.902 / 0.877 | blind 0.489; tuned 0.850; frozen 0.805 | 0.659 (tuned planner) | does not beat the references; overfit; not shown to read the words | 0.687 (shape-only 0.480, always prose 0.440); VOID by the rule (two controls not run) |
| relating two artifacts (correct of 72) | always consistent 36; today 0 answered | 72 (58 / 14 / 0 without the question) | 66, 6 "cannot", 0 wrong; report totals 6 of 12 | supported | 48 right, 24 cannot, **6 wrong** of 78; **not supported** |
| learning (late kept, SIMULATED) | planner alone 0.587 / 0.450; key_majority 0.859 / 0.802 | 0.816 | 0.710 | supported over the planner alone; below key_majority | 0.713 (planner alone 0.440; key_majority 0.831); VOID by the rule (two controls not run) |
| foresight: predicted / observed seconds | | median 102, range 4.9 to 560, never under | | recorded, not corrected (the planner is frozen) | |

The held-out rules lived in the main session's untracked log, so their order before the runs **cannot be verified
from git**; the held-out runs of lane Z's families went through the earlier driver, which decided some outcomes
itself; lane P once saw the held-out transform rows.

## The seventeen done-criteria (directive §18)

Status words are the directive's five. Each status follows the measurement review's second pass, now with the final
validation and the withheld looks cited; the failures of the looks are named in the items they bear on.

| # | criterion | status | evidence |
|---|---|---|---|
| 1 | Existing functionality inventoried and preserved | Partially implemented | [CAPABILITY_AUDIT.md](CAPABILITY_AUDIT.md); the main session's full validation at `55f89be` ([REGRESSION_REPORT.md](REGRESSION_REPORT.md)): `scripts/check.sh` all checks passed (8,472 passed, 22 skipped), browser suite 235 passed, guard 0 different, offline digest equal in both regimes, two-account 56 of 56, acceptance 7 of 7; not run: CI, x86_64, a real model, real phones, a screen reader |
| 2 | Ingestion extensible beyond a fixed list | Partially implemented | the reader registry; external readers approved by entry point, distribution and version (F3 `4597e73`); unknown family 51 of 51 development, 49 of 49 held-out, but **on the withheld split 107 of 111 with 3 wrong answers (disguised or unknown files answered falsely): not supported**; "identified" is not "decoded" |
| 3 | Native information retained where important | Partially implemented | measurements 310 of 326 right on held-out (310 of 322 development); intake supported on the withheld split (347 of 358, 0 wrong); lost: an array's dtype and 3-D shape, a DOCX's embedded pixels, video frames |
| 4 | Cross-modal relationships represented and reasoned over | Partially implemented | cross 72 of 72 development, 66 / 6 / 0 held-out, and **on the withheld split not supported: 48 right, 24 cannot, 6 wrong of 78; an image against its caption answered every time and right on 5 of 10**; only comparisons the question names |
| 5 | Context-driven output selection operational | Partially implemented | the planner's held-out verdict "does not beat the references" (one violation, since impossible by construction); the frozen planner's withheld look **VOID by the rule** (two controls not run; as measured good 0.687 against 0.480, 0 violations); owner commands and the console only: **nothing reaches the answer path** |
| 6 | Non-text responses where useful and feasible | Partially implemented | 30 makers; the console's "Made and kept" (ADR-079); the planner's withheld look (VOID by the rule: two controls not run; figures 0.687 against 0.480 as measured); Pocket not covered; answers in conversation are sentences |
| 7 | Artifact generation gives usable, validated results | Partially implemented | made 14 of 14 development, 13 of 14 held-out, **16 of 16 withheld (supported)**; transform 11 of 11 withheld; the product check's `visible_to_a_person` and the stricter reader (F4); no person judged usability |
| 8 | Creative synthesis demonstrably improved | **Unverified** | no measure exists |
| 9 | Complex multi-file artifacts through reliable workflows | Partially implemented | the several-source document with "How the sources relate"; transform 8 of 8 development and held-out, 11 of 11 withheld; no multi-file project measured |
| 10 | Large workloads managed by resource-aware execution | Partially implemented | `Limits`; the store's caps; decoding in a limited child (F1, F4); bounded reductions (F3, F5); planner time estimates 5 to 560 times high; no durable jobs |
| 11 | Knowledge accumulated across modalities | **Unverified** | nothing measures accumulation |
| 12 | Recursive improvement gives measurable benefits | Partially implemented | learn held-out +0.260 / +0.203 over the planner alone, and withheld +0.273 / +0.208 as measured (that look VOID by the rule: two controls not run), SIMULATED people; 0.118 below key_majority late on the withheld split; `form_learning` off |
| 13 | New capabilities integrate with existing systems | Partially implemented | forget, restore, quiet and governance tests over artifacts; not on the answer path, the workspace or memory |
| 14 | Original memory, inference, autonomy, device and interface features operational | Partially implemented | the same run as item 1 at `55f89be`; not run: CI, x86_64, a real model, real phones, a screen reader |
| 15 | Security and resource boundaries enforced | Partially implemented | hostile 23 of 23 development, 22 of 23 held-out, and **on the withheld split 32 of 35 with 3 hostile files answered instead of refused: not supported** (8 withheld constructions belong to 7 classes no earlier split had); the security review's second pass ("offer: yes"); F1, F3 and F4's fixes |
| 16 | End-to-end tests and benchmarks support the claims | Partially implemented | the committed held-out record; development reproduced exactly at `55f89be`; the withheld looks taken once each under the rule committed before them: supported for intake, honest, compound, made and transform; VOID by the rule for forms and learn (two controls not run); **not supported for unknown, hostile and cross** |
| 17 | Rollback and recovery for significant changes | Partially implemented | console viewing (`console_artifacts`) is ON by default; everything that lets bytes in or changes a choice (upload, form learning, external readers) is off; schemas 20-21 forward-only, rolled back by the owner's own copy, not by restore (MIGRATIONS.md section 2, F1 `480b009`); [RUNBOOK.md](RUNBOOK.md) |

## Remaining gaps

[COGNITIVE_GAP_ANALYSIS.md](COGNITIVE_GAP_ANALYSIS.md), first screen: no model that sees or hears; no decoder for
compressed images, audio or video; nothing on the answer path; no durable jobs; Pocket, voice, agents; real files
beyond this host's tools; creativity and accumulation unmeasured.

Unchecked by any reviewer: whether text planted inside an artifact can steer the relater; and
`scripts/bench_omni_forms.py` refuses the forms oracle `first_good` as a look subject (so a forms look cannot run that
control as it stands). Stated limits of the refusal rule (ADR-080 decision 4): a cell with no cached value is "cannot"
only in the note; a PDF action inside a compressed stream other than the catalog is not found; a text note quoting the
ZIP signature bytes is left unknown.

Reported as open, not to be fixed in this programme's remaining lanes:
- a failed check cannot count as a learning outcome without a migration (`form_outcomes` needs a held made artifact;
  pinned by a test);
- the plan explanation does not name the simplest-form margin in the planner's own sentence (the planner is frozen);
- duplicate conventions left from parallel lanes: two zip-entry classes, two XML memory rules, two encoded-name
  searches (`instance/imagined.py` and `artifacts/store.py`);
- two functions of about 200 lines (`forms._score_run`, `produce.produce`);
- a restore-ordering concern for the sidecar (code review L10), not reproduced;
- a six-minute recording is not read (its samples would need more than 256 MiB);
- CI's 40-minute cap is not shown to fit: one CI run is needed, which needs the owner's go-ahead to push.

Fixed by fix lane F5 (`bf3dac6`), no longer open: the make summary's wording after a partial read (N3), `--partial`
on a large archive (N4), `make` and `remake` exit codes (N2), the remaining non-plain words (L3), the
`artifact_max_bytes` help (N-new-2).

## Limitations of the evidence

- Synthetic files with known content; simulated people; two model readers of one family as the only judges of form
  labels; no person judged any made thing.
- One host (aarch64, shared and loaded); no x86_64, macOS, Windows or CI run.
- Small held-out families (made 14, transform 8) and one held-out run per mechanism, with its rule in an untracked log.
- What "made right" and "checked" do not establish: [../quality/OMNIMIND_BENCHMARK.md](../quality/OMNIMIND_BENCHMARK.md).

## Recommended next priorities

These are recommendations for the owner and the main session, not decisions.
1. Fix the three weaknesses the withheld looks found (false answers on disguised or unknown files; hostile files
   answered instead of refused; the relater answering a kind of pair it cannot measure, an image against its
   caption), and examine each fix on NEW withheld cases written by a lane that saw no product code: never on these.
2. Run the CI check job once (it needs a push: the owner's decision) before any merge into `main`.
3. Only then ask the owner whether to merge into `main` (schema 21 is one-way for an individual's file).
4. A planner revision with its own new examination: the time estimates (5 to 560 times high); the P1 sentence may be
   reworded now that the forms look is taken (a commit that changes no choice).
5. A failed check counted as an outcome (needs a migration), so the learner and the gap report see failures.
6. Owner decisions that would open the largest closed doors: decoders for compressed images and audio (packages, or
   external programs in a sandbox), a model that sees or hears, a Pocket view, durable jobs.
7. A corpus of real files made by other producers, and a person's judgment of usability.

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

## The look at the second withheld split (placeholder)

**Not yet taken.** To be filled by the main session after the one look at `final --split withheld2` (unknown,
hostile, cross), beside its rule ([../quality/OMNIMIND_BENCHMARK.md](../quality/OMNIMIND_BENCHMARK.md), "The rule for
the look at the second withheld split"), its controls and its ledger lines; a VOID look reported as void; failures
first.
