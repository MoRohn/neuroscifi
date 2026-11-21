# User journey matrix

Personas come from `docs/architecture/PROJECT_BLUEPRINT.md` plus the Web Cortex directive. Each journey maps to the
requirements it exercises (`docs/architecture/REQUIREMENTS_MATRIX.md`) and to the evidence that executed it. Status words:
**PASS** executed and verified · **FAIL** executed and defective · **BLOCKED** cannot be verified here · **NOT TESTED**.

"RC1" = `64da95b`, "RC2" = `cce5134`, "Final" = the commit named in `REGRESSION_REPORT.md`. All UAT runs used real
`nsf` processes (console, Pocket bridge, worker, MCP, CLI) and a real Chromium browser; evidence paths are under the
session scratchpad `uat/` (RC1) and `retest/` (RC2) folders listed in `E2E_TEST_RESULTS.md`.

**There are no user accounts in NeuroSciFi.** Account-style journeys map to the product's real mechanisms:
onboarding = `nsf init`/birth; authentication = the console's per-start session token and Pocket's one-use invitation
pairing; session persistence/logout/recovery = console restart (new token), Pocket unpair/re-pair, vault-held pairing.

## Personas

| ID | Persona | Surfaces |
|---|---|---|
| P1 | Owner-teacher | CLI, console Teach |
| P2 | Inspector ("what did you use to learn that?") | console Beliefs/Evidence/Questions, `nsf explain` |
| P3 | Curator (forget, promote, archives) | console, `nsf forget`, `nsf checkpoint` |
| P4 | Owner on the move | Pocket PWA (phone 390×844, tablet 1024×1366) |
| P5 | Operator | systemd units, `nsf doctor`, `install.sh`, migrations |
| P6 | Researcher (web) | console Research/Knowledge, `nsf web`, Pocket research, MCP |
| P7 | Adversary (LAN client, hostile web page, prompt injection) | Pocket LAN surface, fetched content, MCP inputs |

## Journeys

| ID | Persona | Journey | Requirements | RC1 | RC2 retest | Evidence |
|---|---|---|---|---|---|---|
| J-ON-1 | P1 | Create an individual, birth record, empty-state UI | INS-01, INS-03 | PASS | PASS | T1 J2, T5 empty states, R3 |
| J-AU-1 | P1 | Console lock: no token, wrong token, token after restart (session expiry), reload | ADR-013, OPS | PASS | PASS (clearer wording) | T1 J1/J1b, T4 1.1–1.3, R1 U1 |
| J-AU-2 | P4 | Pocket pairing: valid, wrong, reused, expired invitation; handoff link | POC-07, POC-16 | PASS | PASS | Pocket J05/J10/J19, R1 |
| J-AU-3 | P4 | Logout/recovery: unpair, re-pair, vault-held pairing, Spark-side revocation | POC-07, POC-14 | FAIL (P-02, P-03) | PASS | Pocket J11, R1 P-02/P-03 |
| J-TE-1 | P1 | README design question: change suspected → ask → confirm relearns one scope → earlier setup recognised → continue there | INS-10..12, INS-18 | PASS | PASS | T1 J3 (UI only), R3 (CLI) |
| J-IN-1 | P2 | Explain beliefs, supporting/counter evidence, revision history | INS-14, MEM-03 | PASS | PASS | T1 J3 |
| J-CU-1 | P3 | Forget evidence/epoch/partner/dataset with cascade; promote; sweep | MEM-08, MEM-09 | PASS | PASS | T1 J4, R3 |
| J-CU-2 | P3 | Checkpoint create/verify/restore (no resurrection)/rebuild/prune | ARC-02..05, MEM-08 | PASS | PASS | T1 J6, R3 |
| J-GR-1 | P1 | Graphs: simulate, learn, evaluate, promote/reject, recall, rollback, coactivation, lesion | GRA-02..11 | PASS | PASS | T1 J5, R3 |
| J-PK-1 | P4 | First open (sensors off), check sound/light, review → share/learn/discard/forget | POC-01..06 | PASS | PASS | Pocket J01/J02 |
| J-PK-2 | P4 | Field session: forecast, why, outcome feedback (incl. double tap) | POC-11..13 | FAIL (P-01 high, P-06) | PASS | Pocket J07/J13/J25, R1 P-01 (3 viewports × 3 outcomes) |
| J-PK-3 | P4 | Vault: create, lock on hide, unlock, wrong passphrase, erase | POC-14 | PASS | PASS | Pocket J08, R1 |
| J-PK-4 | P4 | Offline cold reopen and recovery | POC-15 | PASS | PASS | Pocket J09 |
| J-PK-5 | P4 | Keyboard-only use, focus after actions | WCAG 2.4.3 | FAIL (P-04) | PASS | Pocket J16/J23, R1 |
| J-RS-1 | P6 | Enable web, submit research in console, truthful stages, open report, citations | WEB-19, WEB-32 | PASS (mechanics), FAIL (answer quality C-02/C-03/C-09) | PASS mechanics; answer quality see J-RS-3 | T1 J8, R1 |
| J-RS-2 | P6 | Cancel, resume/requeue, refresh mid-task, console restart mid-task, duplicate submissions | WEB-19 | PASS | PASS (dedupe, idempotent requeue) | T1 J10/J11, T4 3.x, R2 |
| J-RS-3 | P6 | Grounded answers: every quote in every cited source; no cross-subject merges; latest versions; dates | WEB-14..18 | FAIL (W-01..05, C-02) | Mostly PASS; FAIL RT-W-01 (Wikipedia regression), RT-W-02 (location containment) → fixed in Final, see REGRESSION_REPORT | T3, R2 |
| J-RS-4 | P6 | Fact verification true vs false claims | WEB-16 | FAIL (W-04) | PARTIAL (false → disputed; true stays "insufficient" under ADR-026 with undated sources) | T3, R2, bench |
| J-RS-5 | P6 | Knowledge review: accept/reject/archive/refresh/forget; questions close; versions | WEB-23, WEB-25 | FAIL (C-01 IntegrityError) | PASS | T1 J9, R1 C-01, R2 W-22 |
| J-RS-6 | P6 | Monitors: add/pause/resume/stop, ticks, findings to owner | WEB-29 | PASS (with C-05) | PASS | T1 J9, T3 5.x, R1 C-05 |
| J-RS-7 | P6 | Dependency report (Scenario C): actual versions, advisories, installs nothing | WEB-30 | PASS | PASS | T3 7, R2 |
| J-RS-8 | P6 | MCP client session: tools, rate limits, malformed input, untrusted envelopes | WEB-37 | PASS | PASS | T3 6, R2 |
| J-RS-9 | P6 | Pocket research: submit, progress, report, cancel, offline save, share target, read aloud | WEB-33 | PASS | PASS | Pocket research tests, T5, R1 |
| J-RS-10 | P6 | Multi-device continuity (Scenario F): phone submits, tablet continues, owner reads; other family excluded | WEB-34 | PASS | PASS | tests/test_pocket_research.py, T4 2.5, R1 |
| J-RS-11 | P6 | Privacy: private terms/e-mail/phone refused, nothing sent; owner override with consent | WEB-12 | PASS (refusal); FAIL C-04 false positives, C-06 no override | PASS refusal and override UI; FAIL RT-B-03 authorised value dropped → fixed in Final | T4 6.1 (0 bytes left), R1 |
| J-RS-12 | P6 | Degraded/offline operation: engines down, daemon down, total network loss, web disabled mid-run | WEB-02, WEB-21 | FAIL (W-06, W-07) | PASS | T3 8.x, R2 |
| J-RS-13 | P6 | Crash recovery: kill -9 worker, corrupt cache | WEB-19, WEB-13 | FAIL (S-05, S-06 slow) | PASS | T4 4.x, R2 |
| J-OP-1 | P5 | Reproducible build and start from a clean environment; every `--help` | OPS-01, RES | NOT TESTED (T2 stalled) | see R3 / REGRESSION_REPORT | R3 |
| J-OP-2 | P5 | Migrations: 0.5.0 instance opened by 0.6; newer schema refused | ADR-003 | NOT TESTED (T2 stalled) | see R3 | R3 |
| J-OP-3 | P5 | Regulation (writes paused) across CLI, console, Pocket, web | INS-17 | FAIL (S-03, S-04) | PASS | T4 4.5/4.6, R1 S-03/S-04 |
| J-SE-1 | P7 | Console: Host spoofing, CSRF, CORS, traversal, oversized input, slowloris | SYSTEM_ARCH §5 | PASS | PASS | T4 1.x |
| J-SE-2 | P7 | Pocket LAN: brute force, replay after unpair, cross-family access, connection exhaustion, sibling minting | POC-07/08, ADR-028 | FAIL (S-01, S-02) | PASS | T4 2.x, R1 S-01/S-02 |
| J-SE-3 | P7 | Hostile web content: XSS, prompt injection, huge/slow pages rendered in console and Pocket | WEB-09, WEB-11 | PASS (W-09 variant leaked) | PASS | T4 5.x, R2 W-09 |
| J-UX-1 | all | Responsive 320–1920, keyboard, screen-reader semantics, axe | — | PASS a11y (0 serious); FAIL usability (U-01…U-31) | PASS a11y (0 violations); see UX_VALIDATION | T5, R1 |

Not executable in this environment (BLOCKED): real handsets and mobile browsers, real microphones/cameras, push
notifications while the app is closed (no push server), a trusted public TLS chain, systemd service start under
namespace hardening, a real screen reader. See `PRODUCTION_READINESS.md`.
