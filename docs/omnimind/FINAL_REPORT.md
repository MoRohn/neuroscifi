# OMNIMIND final implementation report (deliverable 12)

Status: 2026-11-27, written by documentation lane 2 on `omnimind` with fix lanes F4 and F5 merged (`20a86ec`),
before the main session's final validation and before any withheld look. Those two places are placeholders; nothing
in them is filled in by anticipation. OMNIMIND lives on the local branch `omnimind` (schema 21); **nothing is merged into
`main`, nothing is pushed, CI has not run.**

## The first screen

**What failed or is not shown.**
- By the rule written before its one held-out run, **the form planner does not beat the references** (one
  hard-constraint violation, since made impossible by construction); it was **overfit** to development (0.850 to
  0.659); and there is **no evidence it reads the request's words** (sentences beat it where the words decide, 17
  against 16 of 34, and where words and content conflict, 7 of 7 against 4 of 7; the shape-only table beat it on
  request pairs, 8 against 3 of 18).
- **One held-out making task could not be done** (made 13 of 14: not a pass by its rule). **One hostile held-out file
  was answered "cannot" instead of refused** (22 of 23).
- **The learner stays below the benchmark's own majority rule** (0.710 against 0.802 late, held-out, simulated people).
- **No done-criterion is met without qualification.** Two are unverified (creative synthesis; knowledge accumulated
  across modalities); two more stay unverified until the final validation runs at the final commit.
- **No withheld look has been taken**; the drivers are not yet pinned.
- **Nothing reaches the answer path**: every answer in conversation is still sentences. Pocket, voice and agents see
  no artifact. No model sees or hears.

**What exists, measured.** The owner can hand NeuroSciFi a file (`nsf inspect`, `nsf artifact add`, the console's
upload when turned on); it is identified by its content, decoded within limits in a guarded child process, and kept
beside the individual with forget and restore reaching it. On the benchmark's synthetic files the product answers
512 of 524 development and 510 of 528 held-out tasks right, with **0 wrong and 0 crashes** (today's system: 55 right,
1 wrong, 1 crash per split). It can make 30 kinds of result (charts, diagrams, documents, tables, data files,
sounds, solids, pictures, explorable models), each read back by an independent check before it is kept and marked
"made by the system, not evidence"; it decides the form before making and says why; it can relate two artifacts by
measurement (held-out 66 right, 6 "cannot", 0 wrong of 72); it can learn which form the owner keeps (off by default).
Only the console's view of all this is on by default.

## Completed work

| wave | lanes | what they built |
|---|---|---|
| Phase I | four audits | the baseline ([CAPABILITY_AUDIT.md](CAPABILITY_AUDIT.md), "Before") |
| O-0 | S, K, A, B1, B2, C, G1 (+ G1b), G2, Z, ZF, ZW, docs-0; integration lane; the merge of `main` with Wave 4 | the audit's defects; detection, readers, the store, the planner, makers and checks, the examinations written first, the withheld split |
| O-1 | P, R, L, U; integration lane 2 | the production pipeline; relating artifacts; learning from outcomes; the console view |
| reviews | security, measurement, code (each with a second pass) | first pass: 4 blockers (measurement), H1-H2 (security), H1 and M1-M4 (code); second pass: all three "offer to the owner: yes", with residues |
| fixes | F1, F2, F3, F4 (`1aa7eb6`), F5 (`bf3dac6`) | the guard; the product decides outcomes; forget's matcher; the benchmark reads only what a person sees; driver pins machinery; `--partial` and reductions; plain words; F4: a forget holds what its scan cannot finish (and the scan's own budget), the product check's `visible_to_a_person`, the reader's two blind spots (re-pinned), the driver's last decisions in the product, `add` decoding once, console verdict reuse; F5: the make summary after a partial read, `--partial` on a large archive (drawn as its folders), `make` exit codes, the remaining non-plain words, the `artifact_max_bytes` help |

Details per row: [IMPLEMENTATION_MATRIX.md](IMPLEMENTATION_MATRIX.md); what exists with its limits and rollback:
[PRODUCTION_INTEGRATIONS.md](PRODUCTION_INTEGRATIONS.md); architecture: [ARTIFACT_ARCHITECTURE.md](ARTIFACT_ARCHITECTURE.md).

## Measured results

Every figure is from a committed evidence file ([BENCHMARKS.md](BENCHMARKS.md) names each one).

| measure | reference(s) | the product, development | the product, held-out | verdict by the rule written before the run |
|---|---|---|---|---|
| taking things in and making things (7 families) | today 55 right of 524 / 528; floor 52 (refuse everything); 29 (say "cannot" to everything) | 512 / 12 / 0 / 0 of 524 | 510 / 18 / 0 / 0 of 528 | no fabrication, no crash: holds; generalizes in five families; hostile refused: not fully (22 of 23); made: does not generalize (13 of 14); transform: holds, with lane P's disclosure |
| of which: format identification / measurements / refusals / either / made read back | | 128 / 310 of 322 / 50 / 2 / 22 | 128 / 310 of 326 / 49 of 50 / 2 / 21 of 22 | |
| choosing a form (good share) | always prose 0.399 / 0.442; shape-only 0.459 / 0.500; second reader 0.902 / 0.877 | blind 0.489; tuned 0.850; frozen 0.805 | 0.659 (tuned planner) | does not beat the references; overfit; not shown to read the words |
| relating two artifacts (correct of 72) | always consistent 36; today 0 answered | 72 (58 / 14 / 0 without the question) | 66, 6 "cannot", 0 wrong; report totals 6 of 12 | supported |
| learning (late kept, SIMULATED) | planner alone 0.587 / 0.450; key_majority 0.859 / 0.802 | 0.816 | 0.710 | supported over the planner alone; below key_majority |
| foresight: predicted / observed seconds | | median 102, range 4.9 to 560, never under | | recorded, not corrected (the planner is frozen) |

The held-out rules lived in the main session's untracked log, so their order before the runs **cannot be verified
from git**; the held-out runs of lane Z's families went through the earlier driver, which decided some outcomes
itself; lane P once saw the held-out transform rows.

## The seventeen done-criteria (directive §18)

Status words are the directive's five. Each status is the one the measurement review's second pass said it would
accept now, with the evidence it asked to be cited.

| # | criterion | status | evidence |
|---|---|---|---|
| 1 | Existing functionality inventoried and preserved | **Unverified** until the main session's full validation at the final commit; **Partially implemented** with it | [CAPABILITY_AUDIT.md](CAPABILITY_AUDIT.md); [REGRESSION_REPORT.md](REGRESSION_REPORT.md) (per-lane suites, guard 0 different, digest unchanged in both regimes, two-account 56 of 56 at earlier commits; the final-commit run is a placeholder) |
| 2 | Ingestion extensible beyond a fixed list | Partially implemented | the reader registry; external readers approved by entry point, distribution and version (F3 `4597e73`); unknown family 51 of 51 development, 49 of 49 held-out; "identified" is not "decoded" |
| 3 | Native information retained where important | Partially implemented | measurements 310 of 326 right on held-out (310 of 322 development); lost: an array's dtype and 3-D shape, a DOCX's embedded pixels, video frames |
| 4 | Cross-modal relationships represented and reasoned over | Partially implemented | cross 72 of 72 development, 66 / 6 / 0 held-out; only comparisons the question names; report totals 6 of 12 "cannot" |
| 5 | Context-driven output selection operational | Partially implemented | the planner's held-out verdict "does not beat the references" (one violation); the limited round; the withheld forms look not yet taken; owner commands and the console only, not the answer path |
| 6 | Non-text responses where useful and feasible | Partially implemented | 30 makers (`nsf artifact forms`); the console's "Made and kept" (lane U, ADR-079); Pocket not covered; answers are sentences |
| 7 | Artifact generation gives usable, validated results | Partially implemented | made 14 of 14 development, 13 of 14 held-out; the withheld made look not taken; the benchmark reader's visibility rules; the product check's `visible_to_a_person` and the stricter reader (fix lane F4, after R2-1 and R2-2); no person judged usability |
| 8 | Creative synthesis demonstrably improved | **Unverified** | no measure exists |
| 9 | Complex multi-file artifacts through reliable workflows | Partially implemented | the several-source document with "How the sources relate"; transform 8 of 8 development and held-out; no multi-file project measured |
| 10 | Large workloads managed by resource-aware execution | Partially implemented | `Limits`; the store's caps; decoding in a limited child (F1); bounded reductions and unpack (F3); planner time estimates 5 to 560 times high; no durable jobs |
| 11 | Knowledge accumulated across modalities | **Unverified** | nothing measures accumulation |
| 12 | Recursive improvement gives measurable benefits | Partially implemented | learn held-out +0.260 / +0.203 over the planner alone, SIMULATED; below key_majority; `form_learning` off |
| 13 | New capabilities integrate with existing systems | Partially implemented | forget, restore, quiet and governance tests over artifacts; not on the answer path, the workspace or memory |
| 14 | Original memory, inference, autonomy, device and interface features operational | **Unverified** until the full validation at the final commit; **Partially implemented** with it | the same run as item 1 |
| 15 | Security and resource boundaries enforced | Partially implemented | hostile 23 of 23 development, 22 of 23 held-out; the security review's second pass ("offer: yes", with M-new-1 to fix or state: fixed by fix lane F4, a forget holds what it cannot check); F1, F3 and F4's fixes (N-new-4, L-new-1) |
| 16 | End-to-end tests and benchmarks support the claims | Partially implemented | the committed held-out record (`omnimind_heldout/`); development evidence that reproduces; complete only after the withheld looks, under the rule committed before them |
| 17 | Rollback and recovery for significant changes | Partially implemented | console viewing (`console_artifacts`) is ON by default; everything that lets bytes in or changes a choice (upload, form learning, external readers) is off; schemas 20-21 forward-only, rolled back by the owner's own copy, not by restore (MIGRATIONS.md section 2, F1 `480b009`); [RUNBOOK.md](RUNBOOK.md) |

## Remaining gaps

[COGNITIVE_GAP_ANALYSIS.md](COGNITIVE_GAP_ANALYSIS.md), first screen: no model that sees or hears; no decoder for
compressed images, audio or video; nothing on the answer path; no durable jobs; Pocket, voice, agents; real files
beyond this host's tools; creativity and accumulation unmeasured.

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
1. Merge these documents, pin the drivers in a commit that has the look rule as an ancestor, run the full validation there,
   take each withheld look once under the committed rule, and record the results below as they come, failures first.
2. Run the CI check job once (it needs a push: the owner's decision) before any merge into `main`.
3. Only then ask the owner whether to merge into `main` (schema 21 is one-way for an individual's file).
4. A planner revision with its own new examination: the time estimates (5 to 560 times high), reading the request's
   words (no evidence today), and the P1 sentence after the forms look.
5. A failed check counted as an outcome (needs a migration), so the learner and the gap report see failures.
6. Owner decisions that would open the largest closed doors: decoders for compressed images and audio (packages, or
   external programs in a sandbox), a model that sees or hears, a Pocket view, durable jobs.
7. A corpus of real files made by other producers, and a person's judgment of usability.

## The withheld looks (placeholder)

**Not yet taken.** To be filled by the main session after the looks, each beside its rule
([../quality/OMNIMIND_BENCHMARK.md](../quality/OMNIMIND_BENCHMARK.md), "The rule for the withheld looks"), its
references and its ledger line; a VOID look reported as void; failures first.

| family | subject | commit | n | result | references | word (supported on unseen cases / not supported / not exercised / VOID) |
|---|---|---|---:|---|---|---|
| intake, honest, unknown, compound, hostile, made, transform | `artifacts` | not yet | | not yet taken | | |
| forms | `choose_form` (planner `4388f4d`) | not yet | | not yet taken | | |
| cross | `relate` | not yet | | not yet taken | | |
| learn | `make_learner` against `make_planner_only` | not yet | | not yet taken | | |
