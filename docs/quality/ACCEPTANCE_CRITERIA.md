# Acceptance criteria

The release is accepted only when every **gate** below passes on a fresh run by the integrator. Results go into `VERIFICATION_REPORT.md` with the command and its output.

## G1. Quality gates (automated)

| Gate | Command | Pass condition |
|---|---|---|
| Lint | `ruff check src tests` | zero findings |
| Unit and integration | `pytest -q --ignore=tests/e2e` | 0 failures, 0 skips except the TLS test's documented skip when openssl is absent |
| JavaScript checks | `node tests/js/*.mjs` | all print "Passed" or the benchmark JSON |
| Numerical reproduction | `python scripts/reproduce.py` | "Verified" for all 8 analyses |
| Browser end to end | `bash scripts/e2e.sh` | 0 failures (Pocket and console) |
| Accessibility | inside e2e (axe-core) | 0 serious or critical violations on Pocket and the console, at phone and tablet sizes |
| Acceptance scenarios | `nsf acceptance --all` | every scenario `"pass": true` (7 scenarios, including `first_physical_test` for p.34) |
| Packaging | `uv pip install .` into a clean venv, then `nsf --help` | succeeds |
| Service units | `systemd-analyze verify deploy/systemd/*.service` | no errors |

## G2. Critical user journeys (end to end)

| ID | Journey | Evidence |
|---|---|---|
| J1 | **Teach and detect** (the owner confirms the change with `resolve --confirm`; only the camera relearns and the partner's meaning carries over; the earlier setup is offered back through `epoch match`). Init, start epoch `bench`, teach action and word, add references from three roots. Mechanism becomes identifiable. Camera readings flip without announcement. The scope `camera:<root>` is detected and the executive asks to calibrate. Start epoch `bench-remounted`. After a restart, both epochs keep their beliefs. | acceptance `grounding` + `device_ownership`; CLI test |
| J2 | **Two partners.** Partner B uses the opposite word convention. Each partner's convention resolves, and the first partner's meaning survives in a later session. The executive asks about a word only while it is ambiguous. | acceptance `lifelong_correction` |
| J3 | **Grow and recall.** Learn context A, promote, learn a rewired world in the same context. The candidate fails retention with a "new context" recommendation. Register it under context B. Recall picks the right graph per segment and flags an unrelated world as an unknown context (owner question, no graph created). | acceptance `growth_retention`, `graph_development`; test_recall |
| J4 | **Forget everything derived.** Checkpoint, forget an event and a dataset. Beliefs change, the dependent graph is invalidated and the eligible older one re-activated, the checkpoint goes stale, and a rebuild removes the old archive. Restoring an old checkpoint does not resurrect the id. | test_journeys; test_checkpoints |
| J5 | **Inspect.** In the console: inventory, beliefs with supporting and counter evidence and remaining uncertainty, the decision with its reason, the evidence journal with forget and promote, graphs with the matrix and decisions, checkpoints with verify. | console Playwright journey |
| J6 | **Pocket day.** Sound and light check with no network until pairing, share, learn, forecast, forget. Field forecast, feedback and vault. Evening review: mute, forget the session. Tablet link and cross-device forget. Offline cold reopen. | Pocket Playwright suite |

## G3. Security acceptance

- No secret, token or credential in logs, the repository or frontend bundles (grep plus review).
- Console unreachable from non-loopback addresses; wrong token, Host or Origin → 403. Body limits enforced.
- Pocket contract unchanged: exactly four receipt fields, 512-byte limit, TLS required off loopback.
- Forgotten ids cannot be resurrected by retry (Pocket and instance) or by restore.
- The learner cannot register or edit scoring rules. Snapshot identity is immutable.
- The security reviewer reports no open P0 or P1.

## G4. Brief thresholds (p.19) and how they are scored

| Threshold | Where it is measured | Pass |
|---|---|---|
| ≥ 50% relative reduction in wrong answers above 95% confidence on shifted sensors | `evidence/recalibration.json` (reproduced); acceptance `grounding` reports confident-wrong counts with and without change detection | reported, with the reduction ≥ 50% |
| ≤ 2 points of nominal task loss | recalibration nominal accuracy, refit-and-validate vs frozen | ≤ 2 points |
| ≥ 80% coverage on normal inputs | field benchmark coverage 88.8%; acceptance `grounding` nominal coverage | ≥ 80% |
| Always report coverage and Brier score | acceptance JSON includes both | present |
| Graph revision: transfer beyond training episodes, rare skills within tolerance, locked budget | promotion decision JSON | all three checks recorded |

## G5. Definition of done per requirement

A requirement in `REQUIREMENTS_MATRIX.md` is **VERIFIED** only when:
- its behaviour is implemented and connected;
- the named test or scenario passes in the integrator's run;
- applicable security controls are in place;
- no open P0 or P1 blocks it;
- the evidence is cited in `VERIFICATION_REPORT.md`.

Requirements classed MB or P can never be marked VERIFIED in this release.
