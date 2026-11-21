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
