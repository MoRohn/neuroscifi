# Master task ledger

Each task has one accountable owner, a completion check and a risk class (L, M or H). Status is one of TODO, RUNNING, DONE or BLOCKED, and DONE requires the check to pass, with output re-run by the integrator.

| ID | Task | Owner | Depends | Completion check | Risk | Status |
|---|---|---|---|---|---|---|
| T0.1 | Safe extraction, baseline test run, plan decomposition | architect | — | 20 py + 4 node + reproduce green on the host | L | DONE |
| T0.2 | Requirements matrix, audit, architecture, ADRs, contracts | architect | T0.1 | docs/architecture/* present; every requirement classed | M | DONE |
| T1.1 | Package layout, `nsf` CLI, pyproject, check.sh, CI workflow | W1 executor | T0.1 | check.sh green; reproduce 8/8 (re-run by architect) | M | DONE (`392ea0f`) |
| T1.2 | Instance schema, store, errors, rules, CLI seam, test fixture | architect | T1.1 | `nsf init/birth/regulation` smoke; rule immutability trigger | M | DONE (`ef95e48`) |
| T2.A | Evidence journal, factorised beliefs, change detection, explain, executive, legacy import | W2-A (opus) | T1.2 | tests/instance/test_{evidence,beliefs,executive,legacy}.py | H | DONE (f35ece5) |
| T2.B | Datasets, learner extraction, packing, gate, rollback, invalidation, lesion, recall | W2-B (opus) | T1.2 | tests/instance/test_{datasets,learner,graphs,recall}.py | H | DONE (f35ece5) |
| T2.C | Retrieval, forget cascade, expiry, promotion, checkpoints with no-resurrection restore | W2-C (sonnet) | T1.2 (+T2.B for the dataset cascade test) | tests/instance/test_{memory,governance,checkpoints}.py | M | DONE (f35ece5, a5f4832) |
| T2.D | Pocket control audit, evening review, mute/unmute, manifest shortcuts, Playwright e2e, axe a11y | W2-D (sonnet) | T1.1 | pytest tests/e2e; node checks; test_mobile | M | DONE (83f394e) |
| T3.1 | (Folded into T3.2: console routes call module functions directly; no separate facade) Service facade for console and acceptance (thin, no logic duplication) | W3 integrator | T2.A–C | console tests | M | DONE (n/a) |
| T3.2 | Console server (loopback, token, Host/Origin) and web UI in Pocket's style | W3 executor | T3.1 | console API tests + Playwright journey + axe | M | DONE (d37347c) |
| T3.3 | `nsf acceptance` milestone scenarios (birth, grounding, lifelong correction, graph development, growth and retention, device ownership) | W3 executor | T2.A–C | scenario JSON with thresholds; pytest wrapper | H | DONE (3a93acc) |
| T3.4 | `nsf bench` (observe→decide latency p50/p95/p99), `nsf doctor` | W3 executor | T2.A | bench output recorded | L | DONE (3a93acc) |
| T3.5 | Cross-module journey tests: forget cascades through graphs and checkpoints with real modules | integrator | T2.B, T2.C | tests/instance/test_journeys.py | M | DONE (f35ece5) |
| T4.1 | UX and accessibility pass on console and Pocket; error and empty states | designer / W3 | T3.2, T2.D | axe 0 serious; keyboard e2e | M | DONE (axe/keyboard in e2e, d37347c) |
| T5.1 | Independent reviews: architect (completeness), security-reviewer, code-reviewer | reviewers | T3.*, T4.1 | Findings classified P0–P3 | M | DONE (3 independent reviews at d37347c) |
| T5.2 | Remediate P0 and P1, re-test | owners | T5.1 | Re-review clean | M | DONE (7e0af91, 88f56fd); 1 simulated acceptance check open, D-1 |
| T6.1 | systemd units, runbook, environment guide, migration procedure, testing guide | writer | T3.* | `systemd-analyze verify`; docs match `--help` | L | DONE (ce28573, 7e0af91) |
| T6.2 | Final verification report and completion report | architect | all | VERIFICATION_REPORT complete | L | DONE (VERIFICATION_REPORT) |

| T5.3 | Remediation lanes R1 (governance and privacy), R2 (learning loop), R3 (servers and ops) | executors (opus, opus, sonnet) | T5.1 | regression tests fail on the old code | H | DONE (7e0af91) |
| T5.4 | Integration lanes I1 (migrations, acceptance p.34, concurrency), I2 (console catch-up) | executors | T5.3 | gate + e2e | M | DONE (7e0af91) |
| T5.5 | R4 earlier-setup recognition and epoch links | executor (opus) | T5.4 | brute-force tests; acceptance | H | DONE (88f56fd) |
| T5.6 | D-1 analysis → pre-registration → dynamics-based recognition with negative controls | architect + executor (opus) | T5.5 | pre-registered thresholds (3b4e943) | H | DONE |
