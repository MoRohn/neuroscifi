# Accessibility

| Need | Implementation | Verified by |
|---|---|---|
| Text equivalent | The presence button's accessible name is "Presence: <activity>. Show details". It is described by a polite live region with the full sentence (activity, step *k of 4* when known, the task question, detail, other activities, conditions, voice muted / text only). No percentage is ever shown unless one is known, and none is known today. | e2e `test_background_research_is_subtle`, `test_pocket_presence_shows_own_family_task` |
| Live announcements | `a11y.Announcer`: polite for ordinary changes, **assertive only** for user-critical states (questions waiting) and errors. Coalesced: the latest wins, at most one announcement per 1.5 s, and duplicates are skipped. | unit (`interpret().assertive`), e2e live-region count |
| Keyboard | Native `<button aria-expanded aria-controls>` toggles the panel. Escape closes it and returns focus to the button. The panel controls are labelled `<select>`s and a checkbox, all with 44 px targets. | e2e `test_small_screens_console_and_pocket` (Escape → focus) |
| Reduced motion | `prefers-reduced-motion: reduce` with "Follow system", or the in-app *Motion: Reduced (static)*: no frame loop, no life layer (breath, contour flow, attention to the pointer, scroll inertia), scenes applied instantly. *Full* overrides the system. | e2e `test_reduced_motion_is_static_and_distinguishable` |
| Motion intensity | *Motion intensity*: Low (×0.5), Normal, High (×1.4), persisted in `nsf.presence.prefs`. | unit through `applyEnergy` |
| States distinguishable without motion | Every state has its own static geometry (body shape, props), not only a motion pattern. | e2e L: three states, three distinct static path sets |
| Not colour alone | Conditions are shapes ("?", "!", a broken ring, dashed eyes); disconnected differs from resting in shape and tone. Accents are decorative. | `VISUAL_DESIGN_SYSTEM.md`; gallery snapshots |
| Contrast | Ink ≥4.5:1 and secondary ≥3:1 in both themes (validator check). The app dark theme passes axe. | `client_check.mjs`; e2e `test_presence_axe[light/dark]` (0 serious/critical on the console — every view in dark — and on Pocket) |
| High contrast / forced colours | Strokes use `currentColor`-free CSS classes. In forced-colours mode the browser substitutes system colours for the strokes; not separately tested. | — (gap) |
| Static fallback | Without JavaScript or modules nothing is rendered and the rest of the app works unchanged. When the server cannot be reached, the presence says "Connection lost; activity unknown". | e2e locked/disconnected paths in the theme test |

The SVG itself is `aria-hidden`. The text is the accessible content, and the figure is an illustration of it.

## Accessibility tree checks (console and Pocket, 2026-11-22)

`tests/e2e/test_a11y_tree.py` (run by `scripts/e2e.sh`) reads Chromium's computed accessibility tree over CDP (`Accessibility.getFullAXTree` and `getPartialAXTree`), the tree that NVDA, Orca, TalkBack and VoiceOver consume. It complements the axe scans, which apply rules to the DOM.

| Check | Flows | How |
|---|---|---|
| Every control has a role and a name | Pocket unpaired, paired, research form, research report; console overview, Research, Voice | no button, link, textbox, combobox, checkbox, disclosure or other control with an empty name in the tree |
| Research progress is announced | Pocket `#researchstage` (role status); console `#researchLive` and `#askStatus` | the region is in the tree with `live: polite`; a MutationObserver records each text it takes ("Queued on your Spark…", "Reading…", "Completed"; "Research queued", the stage lines) |
| Voice state is announced | console Voice view: `.nsfv-indicator` (role status, "Microphone off"), `.nsfv-notice`, the "Transcript" list | `live: polite` and in the tree |
| No silent live regions | every `aria-live`/status/alert/log region that text would make visible | it must already be in the tree while empty, because a region created at the moment its text arrives is not announced |
| Focus order | Pocket landing (full cycle: skip link → presence → sections → field controls → pairing code → Pair, then back to the page; no trap); console header and nav; the Research form (question → kind → Start research) | Tab, reading the focused node's role and name each time; every stop is named |

**Fixed by these checks.** Three live regions were `display:none` while empty, so they were out of the tree and their first message would not be announced:

- Pocket `#message` ("Paired.", errors), through `#messagebar:has(#message:empty)`;
- the console's 23 `.msg` status lines (`.msg:empty`);
- the voice `.nsfv-notice` (`:empty`).

They are now visually hidden while empty (clipped, out of the layout) but stay in the tree. The empty message bar's dismiss button is `visibility:hidden`, so it is not focusable. With the old CSS the three tests fail.

## Manual screen-reader protocol (owner-side)

Not performed: no screen reader is installed on the reference host and no handset is attached. Allow about 20 minutes per platform. Record pass or fail, with the words heard, for each step.

**Desktop: NVDA 2025 + Firefox or Chrome on Windows, or Orca + Firefox on Linux. The console, through the SSH tunnel (RUNBOOK §4).**

1. Load the console URL. Expect the page title and "NeuroSciFi · Owner console". Tab once: "Skip to content, link".
2. Press H or Insert+F7 (NVDA elements list). Each view has one level-1 heading.
3. Research: Tab to "Question or objective, edit, multi-line", then "Kind of research, combo box", then "Start research, button". Submit a question. Expect to hear "Research queued" and then the stage changes (Reading…, Completed) **without moving focus**.
4. Open the report. Citations read as "[1], link". Sources are a list.
5. Voice: "Microphone off, status". Turning the microphone on is announced. A notice ("Speech output is unavailable") is spoken when it appears.
6. Error path: submit an empty question. The error is announced, and focus stays on or returns to the field.
7. Presence button: "Presence: <activity>. Show details, button, collapsed". Enter expands it, Escape collapses it and focus returns.

**Android: TalkBack + Chrome (or the installed Pocket).**

1. Open Pocket over HTTPS (CA installed, [TLS](../operations/TLS.md)). Swipe right through the page. The order should match the visual order: Skip to content, Presence, Sections, Field session…
2. Pairing: "Pairing code, edit box, password". Enter the code, then double-tap "Pair". Expect "Paired." and "Paired with Spark".
3. Research: type a question, double-tap the submit button. Expect "Queued on your Spark", then the stages and "Completed" spoken while the focus stays put. Swipe into the report: the verdict, the quoted answer, sources as links.
4. Voice (if enabled): the microphone state is spoken when it changes. A one-device-at-a-time refusal is spoken.
5. Rotate the phone and set the font size to largest (Settings → Display). Nothing should be cut off, and every control stays reachable.

**iOS: VoiceOver + Safari (or the home-screen Pocket).**

1. The same steps 1–4 as TalkBack, swiping right and double-tapping. Use the rotor (Headings, Links, Form Controls): each lists the expected items with names.
2. Live regions: VoiceOver should speak "Queued on your Spark" and the stage changes without the cursor moving. If it does not, record the iOS version: this is the platform most likely to differ.
3. Larger Text (Settings → Accessibility → Display & Text Size → Larger Text, maximum) and Smart Invert: the content stays readable.

Forced colours (Windows High Contrast) and 200 % zoom remain open (RD-53).
