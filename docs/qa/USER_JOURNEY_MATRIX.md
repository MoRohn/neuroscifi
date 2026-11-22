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

## Presence Cortex (0.7.0, Lane P)

Evidence: `tests/e2e/test_presence.py` (real Chromium, real CSP, console with its embedded worker on an offline
FixtureWeb, Pocket bridge), `tests/e2e/test_presence_perf.py`, `tests/presence/`. Details in
`docs/presence/FEATURE_ACCEPTANCE.md`. "Event-level" means the test dispatches Lane V's `nsf:voice` events in the
page; no real microphone was used.

| ID | Persona | Journey | Requirements | Status | Evidence |
|---|---|---|---|---|---|
| J-PR-A | P1 | Idle → listening when capture starts; bars follow the mic level; mic off stops listening motion | Presence A | PASS (event-level) | test_voice_events_listening_level_interrupt_and_mute |
| J-PR-B | P1 | Listening → the real task after the utterance (no invented "reasoning") | Presence B | PASS (event-level voice + real task) | same |
| J-PR-C | P6 | Waiting → web research (searching/reading) during a real investigation | Presence C | PASS | test_research_task_drives_presence_through_stages |
| J-PR-D | P6 | Research → verification shown as inspection (Tier B *evaluating evidence*) | Presence D | PASS | same |
| J-PR-E | P6 | Verification → writing → delivered (achievement), or the owner's review question when consolidation raised one | Presence E | PASS | same |
| J-PR-F | P1 | Voice barge-in → listening without snapping or stale state | Presence F | PASS (event-level) | test_voice_events_… |
| J-PR-G | P6 | Authorised background (curiosity) research shown subtly | Presence G | PASS | test_background_research_is_subtle |
| J-PR-H | all | Theme toggle mid-animation: colours change, no reset or restart, geometry preserved, persisted | Presence H | PASS | test_theme_toggle_mid_animation_keeps_state_and_motion |
| J-PR-I | P1 | Novel concept → valid procedural doodle; Tier C model spec validated, a hostile spec falls back | Presence I | PASS | test_gallery_…, test_tier_c_model_spec_validated_in_browser |
| J-PR-J | all | 300 rapid transitions: no listener, node or loop accumulation | Presence J | PASS | test_rapid_transitions_do_not_accumulate, perf |
| J-PR-K | P4 | 320/360/390 px console and Pocket: compact, no overflow, panel fits, Escape returns focus | Presence K | PASS | test_small_screens_console_and_pocket |
| J-PR-L | all | Reduced motion (system and in-app): static and distinguishable states | Presence L | PASS | test_reduced_motion_is_static_and_distinguishable |
| J-PR-P | P4/P7 | Pocket presence shows only the family's own task | ADR-028 | PASS | test_pocket_presence_shows_own_family_task, tests/presence/test_routes.py |
| J-PR-X | all | axe on console (every view, dark) and Pocket, light and dark | — | PASS (0 serious/critical) | test_presence_axe |
| J-PR-V | P1 | A, B and F with a real microphone and real speech output | Presence A/B/F | NOT TESTED | needs Lane V's live voice session and a speaker |


---

# 0.7.0

Journeys for the 0.7.0 release candidates. **RC1 = `7c64b64`** (discovery testers T1 to T5); **RC2 = `281e18b`**
(independent retesters R1, R2, R3); **RC3 = `0cd8d8b`** (RC2 plus fixes for what R1 and R2 found); first acceptance pass = `4c3f209` (RC3, two test-only commits, 9 fixes from fix lanes fixr4 and fixr5 to `090cb7d`, and 11 commits from the security review of the evidence guard), pushed as `94ccb09`; final tree = `5f28e6b` (after the owner's decisions of 2026-11-22 and the final guard round; R6 below).
**R4** retested the RC3 fixes live on `373104e` (UAT evidence (not in repo) `retest/r4/REPORT.md`); **R5** retested the
fixr4 fixes live on `1e8bf3b` (`retest/r5/REPORT.md`). The fixr5 fixes (`f510512`, `224a843`, `090cb7d`) were run live
by the fix lane only; the guard was then rebuilt after an independent adversarial security review (`DEFECT_RESOLUTION_LOG.md`,
"Security review of the evidence guard"). Defects and their fixes are in `DEFECT_RESOLUTION_LOG.md` (0.7.0).

Status words: **PASS**, **FAIL**, **BLOCKED**, **NOT TESTED**, **UNVERIFIED** (a check could not complete). Where a
tester wrote PARTIAL, this matrix records FAIL and names the defects, or PASS and names what was not covered.

Testers (independent, none built the product). RC1: **T1** research quality, knowledge and autonomy (S1 to S6, S11
research part); **T2** voice and Presence (S7 to S11, Presence A to L, security, benchmark); **T3** regression, build,
migration and the 0.6.0 critical journeys (S12); **T4** UX, accessibility and responsive; **T5** adversarial,
reliability and backend. RC2: **R1** retested T1 live; **R2** retested T2, T4 and the security findings; **R3** retested
T3 and T5. Reviews on RC1: security (SEC-*) and architecture (ARCH-*). Evidence is UAT evidence (not in repo), relative
to the session's `v07/` folder: `uat/t1/` to `uat/t5/` (RC1) and `retest/r1/` to `retest/r3/` (RC2).

**All voice audio results are fake-device results.** Chromium captured WAV files through
`--use-fake-device-for-media-stream`; no real microphone, speaker or handset was used (BLOCKED, RD-21).

## Acceptance scenarios (S1 to S12)

Requirement IDs (RD-nn) are the rows of `RELEASE_DECISIONS.md` §3. "Criteria" are its §4 release criteria. The final
judgement per scenario is in `V0.7.0_RELEASE_ACCEPTANCE.md`.

| ID | Scenario | Requirements | Criteria | RC1 result | RC2 result | After RC2: R4, R5 and fix-lane runs | Evidence |
|---|---|---|---|---|---|---|---|
| S1 | "What happened in AI this week?" (default and deep mode, CLI and console) | RD-01, RD-24, RD-25, RD-31 | 1, 3, 4 | FAIL: no answer in default mode (3 of 3 runs); deep mode wrong event dates and guard errors (T1-05, 06, 08, 09, 12, 13). 15 of 15 quotes present | **PASS** (R1): default and deep mode give cited model answers in the CLI and the console; re-fetched quotes present with matching dates | none | `uat/t1/s1_*.json`; `retest/r1/s1_quick.json`, `s1_deep.json`, `s1_ui*.json` |
| S2 | "Research recent advances in dendritic computation and evaluate their implications for NeuroSciFi." | RD-27, RD-29, RD-31 | 3, 4 | FAIL: off-topic sources cited; honest caveat rejected (T1-07, 09, 14). States not over-claimed; "NeuroSciFi" stripped from queries | **FAIL** (R1: PARTIAL): the subject is kept, but off-topic sources are still cited (a dendritic-cell neoplasm paper, Fe2O3 decision letters), T1-07 | R4: FAIL in part (off-topic PubMed records flagged cited; newest-source date from an uncited record, R4-N3). R5: **PASS-with-limitation**: newest-source date from the sources the answer cites, out-of-date warning shown (R4-N3 fixed, `1e8bf3b`); low-ranked off-topic findings can still be flagged `cited: true` (R5-O2, Low, open) (`retest/r5/ev/n3_1.json`) | `uat/t1/s2_*.json`; `retest/r1/s2_cli.json`, `s2_ui.json` |
| S3 | numpy: released, secure, stable, recommended, and a follow-up ("that version") | RD-07 (ADR-033), RD-27 | 4 | PASS for the four ADR-033 questions; FAIL for the follow-up (researched Go 1), T1-10 | **PASS except T1-09** (R1): the four questions answered with the ADR-033 distinctions; the follow-up with no earlier question asks "Which version do you mean?" and searches nothing; honest caveats still rejected (T1-09) | R4: R1-03 PASS; caveats repeating "2.5.5" rejected (R4-N1). fixr4 failed in R5 (D-R5-1 to 3). fixr5 (`f510512`, `224a843`): the labelled "NeuroSciFi note" in 6 of 6 fix-lane runs. Then guard rebuilt as a closed caveat grammar (`128c339`) after an independent adversarial security review; the rules note (`224a843`) carries "not established"; fix-lane live: note in 2/2 and kept/rejected as designed; independent re-review round 3 PASS (ACCEPT-with-documented-limits). No independent live retest of the guard. **PASS-with-limitation** | `uat/t1/s3_*.json`; `retest/r1/s3_*.json`, `s3_that.err`, `s3_ui_secure.json` |
| S4 | A contested number ("The human brain contains 100 billion neurons.") | RD-08, RD-27, RD-28 | 4 | FAIL: wrong contradiction pairs, a debunked belief counted as support, irrelevant console sources (T1-01, 07, 16, 19) | **FAIL** (R1: PARTIAL): verdict `conflicting` with no recency leaning; still false sides (86 bn against the "73-99 bn" range, a female subgroup against the whole brain, "For decades, scientists said ..." as a side: T1-01, T1-16); the console "how many" answer was off-topic with no count (T1-07); "Sources disagree" rejected by the guard (R1-05) | R4: FAIL (off-topic "100 billion" sources cited, R4-N2; range case PASS). R5: **PASS-with-limitation**: 2 runs cite only brain and neuron records (R4-N2 fixed, `55b8aed`); in one run a kept "Another source states ..." cites an unrelated Crossref record (R5-O1, Low, open). The subgroup and "for decades" cases NOT TESTED live (`retest/r5/ev/n2_*.json`) | `uat/t1/s4*.json`; `retest/r1/s4a.json`, `s4c.json`, `s4_ui.json` |
| S5 | "What has changed in your understanding of predictive coding?" | RD-13, RD-33, RD-34 | 5 | FAIL in the console (T1-04, 17, 18); CLI PASS | **PASS** (R1): history v1 (1998) superseded by v2 (1999) in CLI and console. The Knowledge view still labelled owner assertions "Last verified" (T1-18 residual) | **PASS** (R4): owner assertions labelled "Asserted by you on ...", 0 "Last verified" (`retest/r4/ev/t118_*`) | `uat/t1/s5_*.json`; `retest/r1/s5_*.json` |
| S6 | Autonomous research: opt-in, budgets, prioritisation, notification | RD-36 | 6 | Mechanism PASS (off by default; caps 20 of 20 and 60 of 60 held); FAIL prioritisation (T1-02, 03, 17, T5-02) | **FAIL** (R1: PARTIAL): opt-in, owner record first, 20 of 20 searches, 60 of 60 pages, derivation depth <= 1 all hold; a finding was never delivered when the owner's question queue was full while the log said "notified" (R1-01, High); trivial knowledge prompts filled the queue (R1-02) | **PASS** (R4): with the owner's question cap lowered to 3 and full, the finding became pending question #5 and the console named it (R1-01); 4 Accept-as-knowledge questions in 15 asks, all specific and on-topic (R1-02) (`retest/r4/ev/r101_*`, `r102_*`) | `uat/t1/s6_*.json`; `retest/r1/s6_*.json` |
| S7 | Voice research ("research the latest developments in agentic AI and summarize their importance") | RD-40, RD-41, RD-44 | 9, 10 | FAIL: pipeline works, answers wrong (T2-01, 03, 09, 10) | **PASS** (R2, fake-device): wake word and instruction stripped; answer on-topic; spoken text equals written text; final transcript 231 ms, acknowledgement 355 ms, answer 18.5 s after end of speech | **PASS** (R4): the label follows the answer and real marks are shown; "what was I researching" answers from history (R2-07). Observation: an ethanol question was answered "not provided" because search missed the Ethanol article (known limitation O-10) (`retest/r4/ev/v_console.json`) | `uat/t2/ev/s7*`; `retest/r2/ev/s7.json`, `s7.out`, `s7_speech_*.wav` |
| S8 | Barge-in | RD-45 | 12 | PASS (273 ms stop) | **PASS** (R2, fake-device): playback stopped 365 ms after speech onset; a 150 ms overlap ignored; "Go on" resumed | none | `retest/r2/ev/s8.json`, `s8.out` |
| S9 | Mode switching A (voice and text), B (voice in, text out), C (text only) | RD-42, RD-43 | 11 | PASS (3 cycles) | **PASS** (R2, fake-device): 3 cycles; 0 live tracks in text-only; 409 for forced uploads; segment sequence contiguous | none | `retest/r2/ev/s9.json`, `s9.out` |
| S10 | Multi-device: phone and tablet (one family), a stranger family, hand-off | RD-37, RD-43, RD-05 | 7, 9 | FAIL (T2-04, T2-06); isolation and one capturing device PASS | **PASS except R2-01, R2-02** (R2): isolation, hand-off and cross-device continuation hold; a bridge restart right after pairing left the tab stuck (R2-01); Pocket voice shown as autonomous in the owner's Presence (R2-02) | **PASS** (R4): restart 0.76 s after pairing, recovered 0.53 s after the bridge came back, 0 reloads (R2-01); Pocket voice shown as foreground (R2-02); refused hand-off told in about 0.2 s (R2-10). Cross-family eviction (R2-09) NOT TESTED live (needs ≥4 families) (`retest/r4/ev/p_multi.json`) | `retest/r2/ev/s10r2*`, `tablet_race_*.out` |
| S11 | Degraded operation and recovery (SearXNG down, model down, voice services down) | RD-29, RD-45 | 3, 12 | Research part PASS with T1-05, T1-11; voice part FAIL (T2-08: microphone stayed on with recognition down) | **PASS** (R1 for T1-05 and T1-11; R2 voice part): with the voice services' ports closed the microphone went off 7.6 s after the outage, 0 uploads, the user was told, status accurate, recovery worked. Presence still said "healthy" during the outage (R2-08) | **PASS** (R4): during the speech-engine outage Presence showed `network_degraded`, engines ASR and TTS unavailable, "type instead" (R2-08) (`retest/r4/ev/v_console.json`) | `retest/r1/s11_*.json`; `retest/r2/ev/s11r2*`, `retest/r2/s11/` |
| S12 | Rerun of the 0.6.0 critical workflows on 0.7.0: clean install from a wheel, migration of real 0.6.0 data, crash safety, CLI | RD-49 | 15 | **PASS** (T3): gate, e2e, acceptance 7 of 7 (simulated), reproduce 8 of 8, wheel build and README journey, migration 0.6.0 to 0.7.0, the 0.6.0 journeys (details below). Defect T3-01 (voice assets missing from the wheel) | **PASS** (R3): wheel from a clean `git archive` in a fresh virtual environment (0.7.0, voice, presence, console and Pocket assets present); migration 0.6.0 to RC2 (schema 7 to 11, integrity ok, ids unchanged, forgets stay, pre-forget checkpoint restore re-applies forgets); `kill -9` safety (0 lost acknowledged writes); 138 CLI cases with 0 tracebacks. Bench file stale (T3-04) and README badge (T3-05) | Bench regenerated on `4c3f209`; README says 2588 (final gate on `4c3f209`: 2588 passed, e2e 167 passed, 0 failed) | `uat/t3/REPORT.md`; `retest/r3/REPORT.md`, `retest/r3/mig/`, `a123/`, `a6/` |

The scenario text of S12 is not in `.omc/autopilot/spec-v0.7.0.md` (it says scenarios 1 to 12 are in the owner's
directive). The integrator's record (`.omc/handoffs/v0.7.0-kickoff.md`) defines S12 as the rerun of the 0.6.0
regression; it maps to release criterion 15.

## Presence A to L

Requirements: `.omc/autopilot/spec-presence.md` acceptance A to L; product documents in `docs/presence/`. RC1: T2;
RC2: R2. Real console and Pocket bridge, fake-device audio.

| ID | Check | RC1 result | RC2 result (R2) | Notes and evidence |
|---|---|---|---|---|
| A | Idle to listening on real capture start | PASS | PASS | Capture is fake-device; `retest/r2/ev/presr2.json` |
| B | Listening to reasoning after an utterance | PASS (note: listening outranks the task while the microphone is on) | PASS | `presr2.json` |
| C | Reasoning to web research | PASS | PASS | `presence_server.jsonl` |
| D | Research to verification | PASS | PASS | |
| E | Verification to response | PASS | PASS | Presence said "reasoning over the verified evidence" (T4-02); fixed in `e23f55f`; R4 PASS: never "verified evidence" |
| F | Interruption without snapping or stale state | FAIL (T2-05: invented interruptions) | PASS (from the S7 and S8 traces) | `retest/r2/ev/s7.out`, `s8.out` |
| G | Authorised background research shown subtly | PASS (a voice question looked the same, T2-02) | PASS (console voice); Pocket voice shown as autonomous (R2-02) | R2-02 fixed in `47fe37b`; R4 PASS: a Pocket voice task shows as foreground, never autonomous |
| H | Theme toggle mid-animation | PASS | PASS | Dark confirmed: `data-theme=dark`, `nsf.theme=dark`, background rgb(7,9,8) |
| I | Novel concept to a valid doodle or fallback | PASS | PASS on the safe fallback: both Tier C concepts were invalid, so the fallback was shown; a valid Tier C spec was not displayed in this run | `presr2b.json` |
| J | Rapid changes, no accumulation | PASS | PASS | |
| K | Small screens | PASS | PASS | 320, 360, 390 px console and Pocket: `presr2_console_320.png` to `presr2_pocket_390.png` |
| L | Reduced motion | PASS | PASS | |
| Honesty (never invents state) | | FAIL (T2-02, T2-05) | FAIL in part: R2-02 and R2-08 ("healthy" while speech engines unreachable) | Both fixed (`47fe37b`, `7e3e05c`); **PASS** in R4 |
| A, B, F with a real microphone and real speech output | | BLOCKED | BLOCKED | No real microphone or speaker |

The repository's own Presence tests (`tests/e2e/test_presence.py`, `tests/presence/`) are listed under "Presence Cortex
(0.7.0, Lane P)" above; this section records only the independent runs.

## Cross-cutting 0.7.0 journeys

| ID | Journey | Requirements | RC1 result | RC2 result | Evidence |
|---|---|---|---|---|---|
| J7-SE | Security: tokens, Origin, Host, pairing, size limits, retention | RD-05, RD-32, RD-43 | PASS except T2-04. 138 token, Origin and Host probes 403; oversize 413; 500s in T5-04 to T5-06 | PASS (R2 T2-04; R3 T5-04 to T5-06 give 400; T5-05 Pocket half UNVERIFIED) | `uat/t2/ev/pocket_limits.json`, `uat/t5/a123/`; `retest/r3/a123/` |
| J7-SEC-REV | Security review findings | RD-32 | Risk MEDIUM: 0 critical, 1 high (SEC-H1), 2 medium, 5 low | SEC-H1, SEC-M2, SEC-M3 PASS (R2); SEC-L4 to L8 regression tests only | `uat/reviews/REVIEWS.md`; `retest/r2/sec/`, `retest/r2/ev/sech1.json` |
| J7-PRIV | Privacy: private terms refused, nothing sent | RD-32 | PASS | PASS (R1: 0 private terms in 115 SearXNG queries). Pocket's refusal message overstated "Nothing was sent" (R2-05). R4: message truthful but the question still reached the Spark; R5 PASS after `f41879e`: e-mail and obfuscated e-mail refused on the device (0 POSTs), a phone number refused by the Spark | `retest/r1/priv*.json`, `searxng_queries.jsonl` |
| J7-CONC | Concurrency: budgets, autonomy cap, node hand-off | RD-36 | FAIL (T5-01, T5-02) | PASS (R3): 20 concurrent voice starts in 1.1 s; 19 queued tasks on 3 workers, none cancelled for budget. Tight cap not exercised; T5-07 multi-worker charging UNVERIFIED | `retest/r3/a123/` |
| J7-KILL | Crash recovery (`kill -9`) | RD-45 | PASS with T5-03, 07, 08 | PASS (R3): console killed during tasks and voice 5 rounds, integrity ok, 1,138 acknowledged tasks and 462 acknowledged segments with 0 lost, no duplicate ids; killed during a checkpoint of a 420 MB database, integrity ok and `.nsf-partial` swept; worker killed 6 times over 8 tasks, all terminal | `retest/r3/a123/` |
| J7-MODEL | Model failures (hostile endpoint) | RD-29, RD-31 | FAIL (T5-M01, M02) | PASS (R3): all stream and shape modes exit 1 without a traceback; drip and stall end at 10.1 s against 10 s; `--no-tools` honoured | `retest/r3/a45/` |
| J7-SX | SearXNG failures | RD-24, RD-25 | FAIL (T5-M05, M06) | PASS (R3): 16 modes without a traceback; slow and drip about 12 s; `health --probe` 2 to 4 s. Breaker state UNVERIFIED | `retest/r3/a45/` |
| J7-CLI | CLI, migration, lifecycle | RD-49 | FAIL (T5-C01 to C05) | PASS (R3): 138 CLI cases, 0 tracebacks; forget cascades; restore keeps a node revoked | `retest/r3/a6/` |
| J7-RES | Resource scheduling (GPU and memory pressure, audio priority) | RD-46 | FAIL (ARCH-2: missing) | Not retested live; regression tests only (`df1e20a`); developer evidence `docs/quality/evidence/resources_live.json` | |
| J7-BENCH | Co-residency benchmark (vLLM, ASR, TTS, SearXNG resident, deep research during a spoken question) | RD-46 | Measured: voice answer audio 23.7 / 45.5 s under load; minimum available memory 43.0 of 124.9 GB | Not re-measured | `uat/t2/ev/bench_run3.json`, `bench_run4.json` |
| J7-UX | Accessibility and responsive | | axe 0 violations; FAIL T4-01, T4-02, T4-06 and lower findings | axe 0 violations in 203 runs, console and Pocket, light and dark; T4-01, T4-03 to T4-08 PASS; T4-02 FAIL on RC2, fixed in `e23f55f`, PASS in R4. See `UX_VALIDATION.md` | `retest/r2/t4/sweep_r2_console_pocket.json`, `retest/r2/t4/shots/` |

## 0.6.0 critical journeys on 0.7.0 (S12, criterion 15)

T3 ran these on RC1 on a copy of real 0.6.0 data and from a wheel built from `git archive`; R3 repeated the build,
migration, crash-safety and CLI parts on RC2. Journey IDs are the 0.6.0 IDs above.

| 0.6.0 journey | Requirements | RC1 result (T3) | RC2 result (R3) | Evidence |
|---|---|---|---|---|
| J-ON-1, J-TE-1, J-IN-1 (create, teach, change, resolve, explain) | INS-01, INS-10..12, INS-14 | PASS (CLI teach, change, resolve) | PASS within the 138 CLI cases | `uat/t3/REPORT.md` item 7; `retest/r3/a6/` |
| J-CU-1, J-CU-2 (governance, forget, checkpoints) | MEM-08, MEM-09, ARC-02..05 | PASS. Pre-forget checkpoint restore kept the 4 forgotten evidence rows removed | PASS: forgets stay after migration; a pre-forget checkpoint restore re-applies the forgets | `uat/t3/mig/`; `retest/r3/mig/` |
| J-GR-1 (graphs) | GRA-02..11 | PASS | PASS within the CLI cases (nonexistent nodes are errors) | `uat/t3/REPORT.md`; `retest/r3/a6/` |
| J-AU-2 (Pocket pairing) | POC-07, POC-16 | PASS: 403 / 201 / 403 / 200 / 201 / 200 / 403 | Pairing exercised by R2 (S10); not repeated as a sequence | `uat/t3/REPORT.md` item 7 |
| J-RS-5 (knowledge) and console owner journey | WEB-23, WEB-25 | PASS via e2e (144 passed, 1 skipped in runs 1 and 3) and a walk of the migrated database | e2e on RC2 by the integrator: 164 passed, 1 skipped, twice | `uat/t3/e2e/` |
| J-OP-1 (clean build and start) | OPS-01 | PASS with T3-01 (voice assets missing) | PASS: 0.7.0 wheel with voice, presence, console and Pocket assets; installed console and Pocket serve `/voice` assets, missing asset 404 JSON | `retest/r3/dist/`, `inst/` |
| J-OP-2 (migration) | ADR-003 | PASS: schema 7 to 11, counts and ids unchanged; 0.6.0 refuses schema 11 | PASS: schema 7 to 11, integrity ok, ids unchanged, new tables empty | `retest/r3/mig/` |
| Crash safety | ADR-003, RD-45 | PASS (T5 on RC1) | PASS (see J7-KILL) | `retest/r3/a123/` |
| Acceptance harness and reproduction | | PASS: `nsf acceptance --all` 7 of 7 (simulated, ADR-014); reproduce 8 of 8 bit-exact | Not repeated by R3; reproduce runs in every gate (`scripts/check.sh`) | `uat/t3/acc/` |
| J-PK-1..4, J-RS-6..10, J-OP-3, J-SE-3 | | NOT TESTED as independent 0.7.0 journeys; covered by the repository e2e suite | Same | |

## Journeys after the owner's decisions (R6, 2026-11-22)

After the owner decided to fix T1-07, SEC-G-E1, news rendering and the bench rows, and to resolve the README limitations
(Brave keyed search, public speech corpora plus a guided device test, empty-payload Web Push, node signing), the
independent retester **R6** ran these journeys live at `a9f5e37` (UAT evidence (not in repo) `retest/r6/REPORT.md`,
E1 to E11). Defects R6 found were fixed by the polish lane with tests and **not retested live**
(`DEFECT_RESOLUTION_LOG.md`, R6 table). The final tree is `5f28e6b`.

| ID | Persona | Journey | Scenario | R6 result | After R6 | Evidence |
|---|---|---|---|---|---|---|
| J7-TLS | P5 | Issue a private-CA chain (`nsf pocket tls init`, `export-ca`, `status`); the phone trusts it; untrusted it fails | S10 | PASS (TLS 1.3, issuer "NeuroSciFi Pocket CA"; untrusted `ERR_CERT_AUTHORITY_INVALID`) | Serve hint completed; warning when no LAN address is on the CA (`32a675e`) | E1 |
| J7-FAM | P4 | Phone pairs with the terminal code; tablet pairs with the phone's link code into one family; the tablet follows and continues the phone's question; a stranger family sees nothing | S10 | PASS (Queued to Completed in 24 s on the tablet; stranger 404 on all three family tasks) | `nsf pocket invite` issues a code without a restart, the bridge prints its URLs (`7675292`); the list refreshes itself (`93afb4e`); one family tag everywhere (`8aea6d5`) | E1 |
| J7-NODE | P4 | Enrol a device as a research node by comparing fingerprints; it owns a session; other devices and voice are refused; hand-off; revoke | S10 | PASS | — | E1 (screenshots 09–17) |
| J7-PUSH | P4 | Turn push on, test push, a push with Pocket closed, opt out | S10 | PASS (real FCM; notification 13 s after a question; no push after opt-out over 70 s) | Opt-out without the vault or a pairing (`bf470cc`) | E1 (screenshots 20–22); `push_live.json` |
| J7-SENSE | P6 | "When was Rust first released?"; the Rust video game; Python | S1-type question | PASS (the language 2 of 2 runs with a rules note; the game when named; Python February 1991) | Game guide fragments are not knowledge (`c98bd29`) | E4 |
| J7-NEWS | P6 | "What happened in AI this week?", NVIDIA news; rendering off with `--no-news-render` | S1 | PASS (freshness met on every run; one render per news query) | — | E5; `news_render_live_v2.json` |
| J7-GUARD | P6 | Model answers for numpy secure and recommended, Jupiter, the neurons claim; hand check | S3, S4 | FAIL (minor): 1 of 5 neurons sentences unfaithful (D5); claim check `insufficient` (D6) | D5 fixed by the round-6 guard (`2a0747b`; final guard review ACCEPT-with-documented-limits (round 8)); D6 now disputed (`6aa5381`) | E6 |
| J7-BRAVE | P5 | Store, show, refuse an insecure key, cap 0, remove (fake key; Brave never contacted) | — | FAIL: a 0644 source file was accepted (D1) | Refused now (`ee32d21`); cap 0 reads "off (cap 0)" (`966c9c0`). Brave live NOT TESTED (no key) | E7 |
| J7-IDLE | P5 | Idle-unload configuration, refusals, tick | — | PASS in part; an actual unload NOT TESTED (shared services) | `nsf resources idle` shows `loaded` (`abeac2b`) | E8 |
| J7-FORGET | P3 | Forget a research task; both checkpoint archives rewritten; forgotten text absent; archives verify | — | PASS | Budget row and questions unlinked (`17bcf6f`) | E9 |
| J7-VCHECK | P1 | Console Voice check and `nsf voice selftest` with a fake device | S7 to S9 | PASS (mic, read 5 of 5 at 2 % WER, speaker, interrupt; "Microphone off" failed 1 of 5 runs, D3) | D3 fixed (`1e1d7c4`); `--fake-input` names the fake device (`d79ba31`). The real microphone is owner-side | E10 |
| J7-DOCTOR | P5 | `nsf doctor` on this host, with `--path` | — | PASS (tmpfs memory-backed, zram swap, LUKS2 under the model directory) | — | E11 |

The voice journeys on recorded human speech (public corpora) are developer evidence: `docs/voice/VOICE_VALIDATION.md`.
The owner's Voice check and the guided phone test are the owner-side journeys for real hardware.

## Release criteria

The judgement per criterion is in `V0.7.0_RELEASE_ACCEPTANCE.md`; statuses are mirrored in `RELEASE_DECISIONS.md` §4.
The architecture review's RC1 snapshot (met 1, 3, 5, 7, 9, 11, 12; partial 2, 4, 6, 8, 10, 13, 15; not met 14) was a
review, not a retest.

## Not executable here (BLOCKED)

Owner-side: real microphone, speaker and handset (RD-21; the owner's Voice check and guided phone test are built);
Pocket and Web Push on real phones; Brave live with the owner's key; root-only hardening; a real external inference
provider; a screen reader used by a person. PersonaPlex real-time duplex speech is not real-time upstream on this host
(`DEFECT_RESOLUTION_LOG.md` O-02; the owner left it as it is).
