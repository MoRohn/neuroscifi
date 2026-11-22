# UX, accessibility and responsive validation

Method: real `nsf console` and `nsf pocket serve` processes against seeded instances (CLI-seeded evidence, graphs,
checkpoints; research states produced through the real orchestrator with an offline fixture web), Chromium at
320, 390×844, 768×1024, 1024×1366, 1280×900 and 1920×1080, axe-core 4.10.2 on every view and state, keyboard-only
passes, DOM/landmark inspection. RC1 review: 179 screenshots, ~135 axe scans. RC2 retest: every finding re-checked at
390/1024/1280. A real screen reader was **not** used (NOT TESTED).

## Accessibility

| Check | RC1 | RC2 / Final |
|---|---|---|
| axe serious/critical | 0 | 0 |
| axe moderate | 2 (`landmark-unique` #checkpoints, `region` toast outside landmarks) | 0 |
| Contrast (computed by hand where axe was incomplete) | all text AA (muted 6.07:1, warn 6.75:1, focus ring 4.7:1) | unchanged |
| Keyboard: visible focus, skip link, dialog | focus visible; Tab escaped modal dialogs; focus lost after Pocket actions (P-04) and Knowledge actions | focus trapped in dialogs; focus restored after actions (P-04 PASS; RT-B-04 fixed in Final) |
| Reflow at 320 px | no page-level horizontal scroll | unchanged |
| Touch targets | Pocket nav 24 px, checkboxes 18 px, citations 20×17 px | nav 44 px, checkboxes 24 px, citations 44 px on coarse pointers |
| Live regions | Pocket report recreated `role=status` each poll | one persistent node |
| Landmarks | Pocket header inside main, no skip link, unnamed Sense section | fixed |

## Usability findings and resolution

Severity as assessed by the reviewer. Status after the RC2 retest (browser, real processes) and Final.

| ID | Sev | Finding | Resolution | Status |
|---|---|---|---|---|
| U-01 | high | Research history/monitor row actions off-screen on phones | Stacked rows with labels below 640 px; tablets covered in Final (RT-B-05) | PASS (390); RT-B-05 fixed in Final |
| U-02 | high | Verdict contradicted its own report ("insufficient 81%, 0 contradict" beside a contradiction) | Verdict derived from the same supporting/contradicting sets; question-form objectives get no verdict unless they become a checkable statement (RT-B-06, Final) | see REGRESSION_REPORT |
| U-03 | high | Privacy refusal told the owner to set an API parameter | Plain refusal naming the detected kinds, focus + `aria-invalid`, explicit "Send anyway" with a confirm dialog | PASS |
| U-04 | med | Queued task with no worker waited silently | Worker liveness shown; hint after 30 s naming `nsf web work` | PASS |
| U-05 | med | Server stopped: misleading empty states, live buttons | One banner, mutating controls disabled | PASS |
| U-06/U-07 | med | 409/507 messages pointed to the CLI and left stale header state | Plain guidance, header refresh, focus to the enable switch | PASS |
| U-09 | med | Pocket message bar never cleared and covered controls | Dismiss + auto-clear for transient messages; state-change messages persist (RT-B-02, Final) | PASS / fixed in Final |
| U-12 | med | Phone header + nav took ~40 % of the first screen | One horizontally scrolling nav row below 1000 px | PASS |
| U-13 | med | Report → accept knowledge took 4 clicks; free-text answers for yes/no questions | Link from the report, status filter, Accept/Reject on questions; per-value buttons for contested questions (RT-B-07, Final) | PARTIAL at RC2 → Final |
| U-14 | med | Jargon (posterior, nats, Beta prior, stemmed keys, raw scopes) | Plain sentence first, numbers under "Technical detail"; remaining Questions-view jargon moved in Final | PARTIAL at RC2 → Final |
| U-15/U-16/U-17 | med | Frozen relative dates, contradictory provenance badges, raw failure text | Absolute dates, badge from sources, plain failure text with Resume | PASS |
| U-18/U-21 | low | "single source" next to three citations; three names for the same mode | "3 copies, 1 independent source"; one label map across console and Pocket ("Check a claim", "Kind of research") | PASS |
| U-19/U-20 | low | Stale "Paired with Spark"; one message for all pairing failures | Reachability in the header; distinct messages | PASS |
| U-22..U-31 | low | Heatmap label size, spacing, unloaded font, backticks, stale counts, unexplained disabled buttons, Pocket busy-port traceback | All addressed | PASS |
| Pocket UX 1–12 | low–med | Generic errors, vague passphrase message, wrong control named, double dialogs, small checkboxes, tablet label layout | All addressed | PASS |

## What works well (reviewer)

- Consistent tokens; AA contrast everywhere; meaning never carried by colour alone.
- Fast: console first load 109–261 ms, view switch 33–130 ms, submit acknowledgement 63–97 ms.
- Progress is honest: the stage shown is the one the worker recorded.
- Hostile source content is shown as inert text with an "injection quarantined" badge.
- Pocket's plain-language errors became the model for the console.

## Not covered

Real screen readers (NVDA, VoiceOver, TalkBack); forced-colors mode; 200 % browser zoom (320 px reflow only);
installed PWA behaviour and OS share sheet on a handset; real speech synthesis/recognition engines.


---

# 0.7.0

UX, accessibility and responsive validation of the 0.7.0 release candidates. **RC1 = `7c64b64`** (T4 and T2 found
the issues below); **RC2 = `281e18b`** (R2 retested them); **RC3 = `0cd8d8b`** (fixes for what R2 found); `HEAD` =
`4c3f209` (final tree). **R4** retested the RC3 fixes live on `373104e`; **R5** retested the fixr4 fixes on `1e8bf3b`.
UAT evidence (not in repo): `retest/r4/`, `retest/r5/`.

## Method

- **T4** (UX, accessibility, responsive): real `nsf console` and `nsf pocket serve`, system Chromium via Playwright,
  axe-core on console 6 views and states x 6 widths x 2 themes and Pocket 6 x 6 x 2, plus the empty instance and dialogs;
  keyboard-only, screen-reader semantics (names, roles, live regions; not a real screen reader), reduced motion, touch
  targets. UAT evidence (not in repo): `uat/t4/`: `shots/`, `sweep_console.json`, `sweep_pocket.json`.
- **T2** (voice and Presence): the same real processes with fake-device audio only (WAV files fed through Chromium's fake
  capture device); no real microphone or speaker. UAT evidence (not in repo): `uat/t2/ev/`.
- **R2** (RC2 retest of T2, T4 and the security findings): same method; axe on console and Pocket in light and dark.
  UAT evidence (not in repo): `retest/r2/t4/`, `retest/r2/ev/`.

## Accessibility results on RC1 (T4)

| Check | Result |
|---|---|
| axe, console (6 views and states x 6 widths x 2 themes) | PASS: 0 violations of any impact |
| axe, Pocket (6 x 6 x 2), empty instance, dialogs | PASS: 0 violations of any impact |
| Unexpected console or request errors | PASS: none |
| Console reflow at all widths | PASS |
| Pocket reflow | FAIL: at 320 px in both themes after pairing (T4-01) |
| Console keyboard: no traps, visible focus | PASS |
| Screen-reader names, roles, live regions | PASS |
| Reduced motion | PASS |
| Forced colours | FAIL: Autonomy budget bars drew as empty outlines (T4-06) |
| Touch targets | PASS with one exception (T4-07) |
| Empty, loading and long-operation feedback is honest | PASS except T4-08 |
| Pocket hand-off and continue | NOT TESTED beyond partial: the browser was not enrolled as a node; the enrolled path is covered by the repository e2e suite |
| Real screen reader (NVDA, VoiceOver, TalkBack), 200 % zoom | NOT TESTED |

## Accessibility results on RC2 (R2)

| Check | Result |
|---|---|
| axe, console and Pocket, light and dark | PASS: 0 violations in 203 runs. Dark theme confirmed (`data-theme=dark`, `nsf.theme=dark`, background rgb(7,9,8)) |
| Pocket reflow at 320 px (T4-01) | PASS |
| Forced colours (T4-06) | PASS |
| Copy honesty | T4-03 PASS; T4-02 FAIL (Voice panel "single-source statements are marked" with no marks; Presence "reasoning over the verified evidence"), fixed in `e23f55f`; axe on the changed views in light and dark added in `203ff5c`; **PASS** in R4 (label and real marks shown; Presence never says "verified evidence") |

## Usability findings (T4)

Severity as assigned by T4. "Fix" is the commit that fixed it for RC2 (`ae5b838` unless noted); "Status" is the RC2
retest outcome. Regression tests are named in `DEFECT_RESOLUTION_LOG.md`.

| ID | Sev | Finding | Evidence (`t4/`) | Fix | Status |
|---|---|---|---|---|---|
| T4-01 | Medium | Pocket at 320 px, both themes, after pairing: the `#researchmode` select is 361 px wide and the page scrolls horizontally (WCAG 1.4.10) | `shots/p-research-320-light.png` | Pocket 320 px reflow | PASS on RC2 (R2) |
| T4-02 | Medium (copy) | Console Research and Voice label model text "Model reasoning ... written only from the verified evidence" although it includes "Single source" sentences; "reasoning" and "verified" overstate a single prompt | `shots/x-research-done.png` | `ae5b838`, `6893205` (accurate reasoning labels), `a108950` (model name from `generated_by`). The degraded label now reads "Model-written summary unavailable - showing the extractive summary quoted from the sources" | FAIL on RC2 (R2); Fixed (e23f55f); live retest R4 PASS |
| T4-03 | Low (copy) | A provisional, unreviewed Knowledge item shows "Last verified" and "First verified"; the basis is source agreement, not a human check | `shots/x-knowledge.png` | Honest provisional wording | PASS on RC2 (R2) |
| T4-04 | Low | Pocket 390 px: after Completed the message still reads "Queued on your Spark..."; the submit button is disabled with no hint; the evidence review shows "basis: not stated" for insufficient items | `t4/REPORT.md` | Message cleared, submit hint, basis wording | PASS on RC2 (R2) |
| T4-05 | Low-Medium | Pocket personal-data refusal drops focus to the body and sets no `aria-invalid` (the console does both); "Read aloud" is offered when no voice exists | `t4/REPORT.md` | Refusal focus, `aria-invalid` | PASS on RC2 (R2) |
| T4-06 | Low | Console forced colours: Autonomy budget bars render as empty outlines; the numbers are still present | `t4/REPORT.md` | Bars readable in forced colours | PASS on RC2 (R2) |
| T4-07 | Low | Voice checkbox label hit area 23 px and input 22 px; Pocket volume slider 16 px high on a coarse pointer (under 24 px) | `t4/REPORT.md` | Touch targets enlarged | PASS on RC2 (R2) |
| T4-08 | Low-Medium | Knowledge empty state has no next step; the microphone consent panel appears without focus or announcement and Escape does not close it; the voice indicator flips between "Microphone off" and "Speaking" and loses the microphone state; Autonomy candidates 2 and 3 duplicate their statement; Presence chips such as "Open hypothesis 1.00" are unexplained | `shots/c-autonomy-1280-light.png`, `shots/c-voice-consent.png` | Next step in the empty state; consent dialog; stable voice indicator; candidate de-duplication; chip descriptions | PASS on RC2 (R2) |

The 0.6.0 findings above (U-01 to U-31, Pocket UX 1 to 12) are unchanged. T4's report does not say whether it re-checked them one by one.

## Presence and voice observations (T2, fake-device audio)

These are what a person would see or hear, as measured in the T2 run. Results for Presence A to L are in
`USER_JOURNEY_MATRIX.md` (0.7.0).

| Area | Observation | Status |
|---|---|---|
| Presence stages | During one real research run the entity moved through waiting, discovering, searching, reading, evaluating, verifying, synthesizing, generating and completed, matching the worker's recorded stages (timings in seconds after submit: 0.06, 2.91, 3.26, 5.36, 64.89, 65.76, 65.96, 74.27) | PASS |
| Progress | `progress` was always null: the entity shows activity without a percentage, as the spec requires | PASS |
| Resting and disconnected | At rest: resting, intensity 0. Without a token: "Not connected... activity unknown" instead of a made-up state | PASS |
| Priority | While the microphone is on, listening outranks a running task | PASS (note) |
| False interruption | After every normally finished reply with the microphone on, Presence showed a user interruption that did not happen (T2-05) | FAIL on RC1; fixed (`cfdbf1a`, `47b925c`); PASS on RC2 (R2: Presence F from the S7 and S8 traces) |
| Voice question shown as background work | A user's own spoken question looked like autonomous research (T2-02) | FAIL on RC1; PASS on RC2 for console voice (R2); a question spoken on Pocket still showed as autonomous (R2-02), fixed in `47fe37b`; PASS in R4 |
| Typing state | Clicking the mode radios set "user typing" (T2-11) | FAIL (low) on RC1; fixed (`cfdbf1a`); PASS on RC2 (R2) |
| Theme | Toggling theme mid-animation changed background 244,247,245 to 7,9,8 with continuous frames and persisted; caveat: toggled while "asking" | PASS |
| Small screens | 320, 360 and 390 px: no overflow, 40 x 40 px targets | PASS |
| Reduced motion | 0 animation frames; 3 distinct static geometries | PASS |
| Stability | 60 mode switches and 20 microphone clicks in 8.5 s: nodes 862 to 862, heap 7.8 to 7.4 MB, 0 errors | PASS |
| Microphone and mode controls | In mode C (text only) the microphone button is disabled; in mode B a speech request gets 409; microphone-off stops all uploads (0 live tracks) over 3 cycles | PASS |
| Microphone with recognition down | With speech recognition stopped and the microphone on, capture continued (14 uploads in 15 s) and the off button was disabled: a privacy defect (T2-08) | FAIL on RC1; fixed (`7d0484a`); PASS on RC2 (R2: microphone off 7.6 s after the outage, 0 uploads, user told) |
| Text-only and TTS down | Status accurate; typed research answered in 14.6 s; with speech synthesis down, mode A showed a text notice | PASS |
| Recovery | After restart (20 to 22 s) health was ready; WER 0.0; Kokoro 106 ms, Whisper 177 ms | PASS |
| Spoken replies | Words the speech engine does not know were dropped ("This is NeuroSciFi." spoken as "This is"); the product spoke "According to .com" (T2-01) | FAIL on RC1; fixed (`dd23058`, `7c9c560`); PASS on RC2 (R2). TTS round-trip WER over four test sentences 30.8 % to 7.7 % |
| Spoken answer quality | Honest "the evidence does not contain..." was rejected, so an irrelevant extractive quote was spoken (T2-03); citation [1] in the answer differed from [1] in "show the evidence" (T2-09); a literal "[]" appeared when the citation list was empty (T2-10) | FAIL on RC1; fixed (`722d7d4`, `e70410c`, `c0afd2b`); PASS on RC2 (R2) on the exact repro; a guard variant failed (R2-03), fixed in `9f14000`; PASS in R4 |
| Voice latency | RC1: end of utterance to final transcript 288 / 753 ms; to acknowledgement audio 401 / 957 ms; to answer audio 9.4 / 17.1 s unloaded (two runs). RC2 (R2, S7): 231 ms, 355 ms, 18.5 s | measured |
| Presence during a speech-engine outage | RC2: Presence said "healthy" and voice "ok" while ASR and TTS were unreachable (R2-08) | FAIL on RC2; fixed in `7e3e05c`; PASS in R4 (`network_degraded`, ASR and TTS named, "type instead") |
| Pocket after a bridge restart right after pairing | RC2: the tab stayed on "Spark unreachable" with Research and Voice hidden for over 60 s (R2-01) | FAIL on RC2; fixed in `167252f`; PASS in R4 (recovered 0.53 s after the bridge came back, 0 reloads) |
| Pocket refusal and evidence wording | RC2: "Nothing was sent" after a refusal although the question reached the Spark (R2-05); "no supporting evidence was found" for quoted statements (R2-06) | FAIL on RC2; fixed in `e7c99cf`, `bf9fcce`. R4: R2-06 PASS ("none yet: stated by one source only ..."); R2-05: R4 found the message truthful but the question still sent to the Spark; fixed in `f41879e`; R5 PASS (e-mail and obfuscated e-mail refused on the device with "Nothing was sent ..." and `aria-invalid`, 0 POSTs; a phone number refused by the Spark; a clean question submitted) |
| Voice history question | RC2: "what was I researching" started a web search (R2-07) | FAIL on RC2; fixed in `b5955c3`; PASS in R4 (history reply, no web task) |
| Cross-device voice | A phone-started research could not be reached by voice (T2-06); another family could see and take the microphone (T2-04) | FAIL on RC1; fixed (`e70410c`, `df12353`); PASS on RC2 (R2) |

## After the owner's decisions (R6 and lanes, 2026-11-22)

What a person sees changed in these places; R6 (UAT evidence (not in repo) `retest/r6/`) found the issues, the polish
lane fixed them with tests, and they were **not retested live**.

| Area | Finding | Fix | Status |
|---|---|---|---|
| Pocket setup | The bridge printed no URL; a second family needed a restart; the `tls init` hint was incomplete; no warning when the LAN address was not on the CA; `--pocket-research` silently needed `--enable`; questions stayed "Queued" with no worker and no message | `7675292`, `32a675e`, `d95160d`, `cd3563b` | Fixed; not retested live |
| Pocket Research | The question list did not refresh itself; Refresh history sat in another card | `93afb4e` (refreshes while visible, its own Refresh) | Fixed; not retested live |
| Pocket report | The model-written answer was never shown though Limitations mentioned it (R6 D8) | `212fe3a` (answer with attribution and rules note) | Fixed; not retested live |
| Family tags | Two tags for one family across console and CLI (D4) | `8aea6d5` | Fixed; not retested live |
| Push opt-out | Turning push off needed the vault or a fresh pairing | `bf470cc` | Fixed; not retested live |
| Voice check | "Microphone off" failed 1 of 5 runs with a contradictory reason (D3); `--fake-input` named the default microphone | `1e1d7c4`, `d79ba31` | Fixed; not retested live |
| Answer lead | "The cited registry records were read live." when no registry was cited (D2) | `05d39d4` | Fixed; not retested live |
| Brave status | `key: configured` with `state: unconfigured` at cap 0 (D9) | `966c9c0` ("off (cap 0)") | Fixed; not retested live |
| Dark mode | Pocket research notes used colours outside the theme's info colours | `594e244` | Fixed; no test in the commit |
| Accessibility tree | Checks of the console and Pocket flows added; empty live regions kept in the tree | `3a4cb14` | In the e2e suite |
| Rules note | Answers about a name with two senses say "this answer is about Rust, the programming language. Rust also names a video game; those sources were read, not cited." | `4807d7c` | R6 PASS |
| Voice | Detects more barge-ins (60 % → 87 % single command words), stops more often by mistake in loud noise (owner decision OD-9); understands more freely phrased commands (21 % → 66 % held-out); a guided Voice check for the owner's hardware | `b6032af`, `8959833` | Measured on recorded speech (`VOICE_VALIDATION.md`) |

Not covered: real phones, a real microphone and speaker, and screen readers used by people (owner-side).

## Not covered

- Real microphone, speaker and speech engines in a real browser session: BLOCKED. Everything above is fake-device.
  Presence A, B and F with real speech output are NOT TESTED.
- Real handsets and mobile browsers; installed PWA behaviour; the OS share sheet: BLOCKED.
- Real screen readers and 200 % zoom: NOT TESTED.
- Pocket hand-off and continue as an enrolled node: NOT TESTED by T4.
- After R4 and R5 no UX defect remains open; R2-05 passed in R5. A voice question about ethanol's boiling point was answered
  "not provided": search missed the Ethanol article and the answer was honest (known limitation, `DEFECT_RESOLUTION_LOG.md` O-10).
