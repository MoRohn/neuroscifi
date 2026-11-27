# Learning from outcomes, as measured (deliverable 8: "Recursive Knowledge Validation")

Status: 2026-11-27, brought up to date by documentation lane 3 from `omnimind` at `4f66291` (Wave O-3 changed nothing
in the learner; the relater's verdicts are no longer stored). Earlier: written on `omni-docs2` from `omnimind` at `21193c4` (the withheld look taken on `55f89be`). The directive asks for "evidence that
experience and feedback improve future multimodal performance". This page says what "learning" means in OMNIMIND,
what was measured, against which references, and what is not learned at all. Figures come from
`docs/quality/evidence/omnimind_l.json` (development), `omnimind_baseline_forms.json` (references) and
`omnimind_heldout/learn-make_learner.json`, `learn-make_planner_only.json` (held-out). Decision record: ADR-078 and
its addition of 2026-11-27 in [../architecture/ARCHITECTURAL_DECISIONS.md](../architecture/ARCHITECTURAL_DECISIONS.md).

## The first screen

- **What is learned is one thing: which form of result a person tends to keep**, per (content kind, purpose,
  device). It is a per-key majority of the owner's reactions and of check verdicts. No model weight, no knowledge and
  no fact is learned.
- **It is off by default** (`form_learning` = `off`). Nobody has turned it on; no real person's reactions have been
  recorded or measured.
- **Every figure is of SIMULATED people** with hidden, stable preferences and simulated feedback. None measures
  benefit to a real person.
- **On the withheld simulated people (one look, on `55f89be`) it is VOID by the rule (two controls were not run; figures reported as measured, establishing nothing under the rule)**; as measured: late / all kept 0.713 /
  0.656 against the planner alone 0.440 / 0.448 (gains 0.273 and 0.208 against the rule's thresholds 0.088 and 0.039),
  and **0.118 below the benchmark's own majority rule** late (key_majority 0.831 / 0.756). It stays off.
- **On held-out it beat the planner alone** by more than the margins written before the run (+0.260 late, +0.203 all)
  **and stayed below the benchmark's own per-key majority** (0.710 against 0.802 late: 0.092 below). By construction
  no learner that goes through the frozen planner can clear the margin over that reference (the development bound).
- **Two done-criteria stay unverified**: "knowledge can be accumulated across different modalities" (nothing
  measures accumulation) and "creative synthesis has demonstrably improved" (no measure exists).
- **A failed check cannot be recorded as an outcome** without a migration: a production whose check fails stores
  nothing, and `form_outcomes` needs a held made artifact (pinned by a test, fix lane F3).

## What "learning" is here

`artifacts/learn.py`, schema 21's table `form_outcomes`, setting `form_learning` (`off` | `record` | `apply`).
- **Signals, from outside the system only**: the owner's reaction to a made artifact (`nsf artifact feedback ID
  kept|dismissed|replaced:FORM`, or the console's buttons) and the independent check's verdict at make time. The
  planner's scores are never read as a reward.
- **The rule**: per key a tally (+1 kept, -1 dismissed, -1 replaced and +1 the form named, -1 a failed check); a key
  needs evidence of at least 2 before anything changes. A form with a positive tally becomes a preference in (0, 1]
  handed to the planner through `Context.preferences`. It never removes a form, acts only among the forms the planner
  admits, and yields to the owner's stated preferences.
- **Who calls it**: since the Wave O-1 integration (`adae698`), `nsf artifact make` records each made artifact's check
  verdict with `record`, and also plans through `learn.plan` with `apply`. With `off`, the planner is called exactly as
  before and no row is written (11 productions compared byte for byte by lane int2). When a learned habit changed the
  choice, the "why" says so in one sentence ("...: that habit, not the content, decided this form (from the content
  alone it would be a line chart).").
- **Undo**: `nsf config --form-learning off` stops it at the next call; `nsf artifact feedback --reset` deletes every
  row and is replayed onto older copies. Rows go with their artifact on forget and restore; owner scope only.

## The measurements (SIMULATED people)

`scripts/bench_omni_forms.py --family learn`: simulated people (6 on held-out), 10 seeds, 8 cycles of 10 requests; "kept" is the
share of made forms the simulated person kept; "late" is cycles 5 to 8. Mean (sd over seeds).

| subject | development late | development all | held-out late | held-out all |
|---|---:|---:|---:|---:|
| today's system (always prose) | 0.403 (0.022) | 0.409 (0.015) | 0.393 | 0.396 |
| never learns (reference) | 0.581 (0.017) | 0.579 (0.018) | 0.433 | 0.429 |
| the planner alone (the product, learning off) | 0.587 (0.026) | 0.584 (0.016) | 0.450 (0.024) | 0.454 (0.025) |
| **shipped: per-key majority through the planner** | **0.816 (0.024)** | **0.762 (0.018)** | **0.710 (0.032)** | **0.656 (0.019)** |
| person_majority (reference) | 0.757 (0.029) | 0.738 (0.021) | 0.715 | 0.695 |
| key_majority (reference, chooses directly) | 0.859 (0.027) | 0.794 (0.025) | 0.802 | 0.727 |
| oracle (knows the hidden preference; a bound) | 0.907 (0.025) | 0.902 (0.022) | 0.880 | 0.878 |
| not shipped: the planner's weights learned through `adjustments` | 0.618 (0.022) | 0.601 (0.019) | not run | |
| not shipped: per-key majority backed off to the person | 0.826 (0.029) | 0.797 (0.020) | not run | |
| bound: the hidden preference handed to the planner | 0.855 (0.022) | 0.854 (0.020) | not run | |

- **Held-out verdict**, by the rule written before the run (the main session's log, quoted in the held-out record):
  "supported" iff the learner's mean gain over the planner alone exceeds 0.058 late and 0.067 all. Gains +0.260 and
  +0.203: supported. "Better than a majority rule" was not claimed, and is not true: key_majority is 0.092 higher late
  and 0.071 higher overall; the shipped rule is at the level of person_majority.
- **Why the majority rule ships**: learning the planner's criterion weights moved almost nothing (+0.036 late over
  never learns, below the 0.063 margin); the one attempt to generalise across keys (back-off to the person) gained
  +0.010 late over the shipped rule, below its margin. Through the frozen planner the bound is 0.855 late, below the
  reference plus the margin (0.937): the planner keeps its hard constraints and proportion rules, which the reference,
  choosing a form directly, ignores.
- **Development to held-out**: the shipped rule fell from 0.816 to 0.710 late; the planner alone fell from 0.587 to
  0.450. The gain over the planner alone held (+0.229 development, +0.260 held-out).
- **The withheld look** (taken once, on `55f89be`; `docs/quality/evidence/omnimind_looks/summary.json`): the rule was
  "supported on unseen cases (simulated people)" iff both gains exceed 2 times the square root of 2 times the larger
  sd over seeds. Result: shipped rule 0.713 / 0.656, planner alone 0.440 / 0.448, gains 0.273 / 0.208 against
  thresholds 0.088 / 0.039: **VOID by the rule (two controls were not run; figures reported as measured, establishing nothing under the rule)** (the forms oracle and the identical-rerun control were not run). Beside it: never learns 0.444 / 0.454,
  key_majority 0.831 / 0.756, oracle 0.891 / 0.894 (the control order oracle >= key_majority >= never learns held).
  The shipped rule is 0.118 below key_majority late: "better than a majority rule" is not claimed. `form_learning`
  stays off. Nothing here is a real person.

## What is NOT learned

- **No weights.** No model is trained or called; no weight of any model changes. The planner's own weights are not
  changed either (`adjustments` stays None).
- **No knowledge from artifacts enters memory.** What the adapters read from a file stays in the artifact store,
  beside the individual, labelled with its source. Nothing decoded from an artifact is written to the individual's
  knowledge, claims, beliefs or any evidence-side table; schema 20's triggers refuse an artifact id in 15
  evidence-side tables. Made artifacts are labelled "made by the system, not evidence" everywhere.
- **Cross-modal transfer is measured only as units travelling from a document to a table.** A unit a README states
  for a CSV column reaches the made chart's axis label ("degC, from README.md"); on lane R's own 12 cases, 29 columns:
  14 right units, 14 rightly without a unit, 1 absent, 0 wrong. The relater's verdicts (`consistent_with`,
  `contradicts`) were recorded as the system's claims, not evidence; since Wave O-3 (the owner's decision of 2026-11-27,
  ADR-081) they are shown only and never stored, the relater is not supported, and nothing learns from them.
- **No skill accumulates across modalities.** The tally is per content kind; nothing carries a preference learned on
  tables to sounds, and no measure of accumulation across modalities exists.

## The gap report

`nsf artifact gaps --report` (read-only; nothing follows from it): formats nothing here decodes and how often they
were given; made artifacts whose independent check failed, by generator and rule; repairs that recur; answers given
in words where the content was of a kind another form carries fully; artifacts linked as contradicting each other.
Productions that ended "could not make this well" are not recorded anywhere, and the report says so. It is a list for
the owner, not a learning signal.

## The directive's done-criteria this page bears on

| criterion (directive §18) | status | evidence |
|---|---|---|
| Knowledge can be accumulated across different modalities | **Unverified** | nothing measures accumulation; nothing decoded enters memory by design |
| Recursive improvement produces measurable benefits | **Partially implemented** | learn held-out +0.260 / +0.203 over the planner alone, and withheld +0.273 / +0.208 as measured (VOID by the rule), SIMULATED; below key_majority (withheld 0.118 late); `form_learning` off by default |
| Creative synthesis has demonstrably improved | **Unverified** | no measure exists |
