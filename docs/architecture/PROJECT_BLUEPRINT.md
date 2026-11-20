# Project blueprint

## Product

NeuroSciFi is a developmental AI built around one individual artificial connectome. This release, **0.5.0**, turns the edition 0.4 research kit into one operable system. A single persistent individual records governed evidence and holds explainable beliefs. It decides what to do next, grows and recalls versioned graphs under an immutable exam, forgets on request with a full cascade, and checkpoints itself losslessly. Pocket, the private phone and tablet interface, and an owner console sit on top.

## Users and journeys

| Actor | Journey | Surfaces |
|---|---|---|
| Owner (teacher) | Create the individual, teach an action and a word, notice a remounted camera, answer one question, return to yesterday's setup | `nsf` CLI, console |
| Owner (inspector) | "What did you use to learn that?" Inspect the evidence, counterevidence, remaining uncertainty and revision history | console Beliefs and Evidence, `nsf explain` |
| Owner (curator) | Forget an event, a session or a partner. Promote evidence to keep it. Verify and rebuild archives | console, `nsf forget`, `nsf checkpoint` |
| Owner on the move | Check sound or light, mark an activity, run a field forecast offline, give one-tap feedback, run an evening review, link a tablet | Pocket PWA |
| Operator | Install as a service, rehearse with synthetic input, back up and restore, take regulation readings | systemd units, runbook, `nsf acceptance`, `nsf doctor` |
| Researcher | Reproduce the brief's numbers and run milestone scenarios | `scripts/reproduce.py`, `nsf lab`, `nsf acceptance` |

## Objectives → release scope

1. **One individual, end to end.** INS and MEM requirements in `REQUIREMENTS_MATRIX.md`.
2. **Graph lifecycle with a gate the learner cannot game.** GRA.
3. **Governed memory.** Deletion that reaches snapshots and archives, no resurrection, short default lifetime.
4. **Pocket stays private and usable.** Evening review, shortcuts, accessibility, real browser end-to-end tests.
5. **Operable.** CLI, console, systemd units, runbook, checkpoint and restore, executable acceptance scenarios.
6. **Reproducible.** All 8 deterministic analyses still verify bit-for-bit.

## Explicit non-goals for 0.5.0

These come from the brief's own pending list (p.19) or are blocked: physical Spark commissioning, native handset builds, neural encoders, language and dialogue, embedding retrieval, autonomous graph creation, and background consolidation. See `REQUIREMENTS_MATRIX.md` (classes P and MB).

## Waves

| Wave | Content | Exit gate |
|---|---|---|
| 0 | Discovery, requirements, prototype audit, architecture, contracts | Every requirement has a path, or is classed P or MB with a reason |
| 1 | Package layout, CLI, test harness, CI, schema and contracts | `scripts/check.sh` green; reproduce bit-exact |
| 2 | Parallel: A evidence/beliefs/executive · B graphs/recall · C memory/governance/checkpoints · D Pocket review, e2e, a11y | Each workstream's behavioural tests green in the full suite |
| 3 | Integration: console (API and UI), acceptance scenarios, bench, doctor, cross-module journeys | Critical journeys pass end to end (CLI, console browser e2e) |
| 4 | Refinement: UX consistency, accessibility, error states, performance | axe zero serious or critical; keyboard checks |
| 5 | Independent verification: architect, security and code reviewers; remediation | No open P0 or P1 |
| 6 | Delivery: runbook, environment guide, migration procedure, final report | Documents match the code; verification report complete |
