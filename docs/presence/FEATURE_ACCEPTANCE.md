# Feature acceptance — Presence Cortex

**How these results were produced**

- Date: 2026-11-21.
- Browser: Chromium 153 (`NSF_CHROMIUM=/usr/bin/chromium`), real CSP. axe is the only exception: that test
  bypasses the CSP so it can inject axe-core.
- App: the real console server with its embedded research worker, and the real Pocket bridge.

**Labels used in the table**

- *Offline web*: research runs against FixtureWeb hosts and a CorpusProvider (the pattern of
  `test_console_research.py`), not the internet.
- *Paced*: the evaluating and synthesizing stages each sleep an extra 1.5 s in the test, so a 0.35 s server
  re-derive can observe them. Everything else is the real pipeline.
- *Event-level*: the test dispatches Lane V's `nsf:voice` events in the page. No microphone or speaker was used.

## Results

| # | Scenario | Result | How it was verified |
|---|---|---|---|
| A | idle → listening on mic capture start | **PASS (event-level)** | `test_voice_events_listening_level_interrupt_and_mute`. Capture on gives *listening* within 2 s. Raising the level from 0.05 to 0.95 makes the bars ≥1.8× taller. Capture off returns to idle and the level becomes `null`. |
| B | listening → reasoning after the utterance | **PASS as "listening → the real task" (event-level)** | Same test. The utterance ends and a real research task is queued; the presence shows it (waiting, then the stage). There is no model "reasoning" step before research in today's pipeline, so none is shown. *Reasoning* appears only after a `synthesized` event with a model configured (unit test). |
| C | reasoning → web research during a real investigation | **PASS (waiting → searching/reading; offline web, paced)** | `test_research_task_drives_presence_through_stages`. The recorded sequence contains searching/reading before the later stages. |
| D | research → verification shows inspection/comparison | **PASS (offline web, paced)** | Same test. *Verifying* follows research and is drawn with the Tier B *evaluating evidence* doodle (magnifier). *Comparing* (balance) appears when the task has `contradicts` links (unit test). |
| E | verification → response synced to output | **PASS (offline web, paced)** | Same test. Writing the report (*generating*, the bubble whose dots follow real intensity), then delivered (*achievement*) within 3 s of the task row turning completed. If consolidation raised a review question, the user-critical *asking* state is shown and the delivery stays as a secondary activity. Speech output sync is event-level: dots follow the output level. |
| F | interruption without snapping or stale state | **PASS (event-level)** | Same voice test. A barge-in (speaking off, mic on) gives *listening* plus `user_interruption`. The body moves ≤20 units per 16 ms sample and the peak point speed is ≤260 (the cap). Node test: four scene changes 4–9 frames apart give a per-frame displacement ≤ vmax·dt. |
| G | authorised background research shown subtly | **PASS (offline web)** | `test_background_research_is_subtle`. A curiosity task (autonomous switched on) gives the *exploring* entity, an orbiting dot and half the energy. As a secondary activity beside a foreground task it shows as the orbiting dot only (node test). |
| H | theme toggle mid-animation | **PASS** | `test_theme_toggle_mid_animation_keeps_state_and_motion`, during a real *reading* stage. Computed stroke goes `rgb(16,16,16)` → `rgb(247,247,247)`, the page background changes, transitions and loopStarts are unchanged, frames keep increasing, the body geometry moves ≤6 units across the switch, and the choice persists across a reload. |
| I | novel concept → valid procedural doodle or safe fallback | **PASS** | Gallery test: 19 Tier B concepts including "quantum gardening" and "tidal folklore" (seeded), all paths in bounds. Tier C test with a fake local OpenAI-compatible server: a valid spec is displayed as tier C; markup or an invalid schema gives `unavailable: schema_invalid` and the procedural or canonical scene. Unit tests reject unknown keys, path strings, off-palette colours, out-of-bounds or stacked elements, and a wrong schema version. |
| J | rapid changes: no accumulation, leaks or corruption | **PASS** | `test_rapid_transitions_do_not_accumulate`: 300 voice events 8 ms apart. Listeners and nodes do not grow, 14 paths, ≤1 loop, commits coalesced. Perf test: 200 transitions × 4 configurations, listeners and nodes unchanged. |
| K | small screens | **PASS** | `test_small_screens_console_and_pocket[320/360/390]`, console and Pocket: compact size, figure ≤44 px, no horizontal scroll, panel inside the viewport, Escape returns focus. |
| L | reduced motion | **PASS** | `test_reduced_motion_is_static_and_distinguishable`: emulated `prefers-reduced-motion`. 0 frames, CSS animation `none`, and three states give three distinct static geometries. The in-app Reduced/Full setting overrides the system. |

**Additional checks**

- Pocket family scope: `tests/presence/test_routes.py`, plus e2e `test_pocket_presence_shows_own_family_task`.
  Another family's task text never appears.
- axe: 0 serious/critical on the console (every view in dark) and on Pocket, light and dark.
- Console security for the new routes: token, Host, Origin and query validation (`test_routes.py`). The static
  presence assets carry the console CSP.

## Test counts (this lane)

- `tests/presence`: 27 pytest tests, including one that runs `client_check.mjs` (280 assertions under node).
- `tests/e2e/test_presence.py`: 14 browser tests.
- `tests/e2e/test_presence_perf.py`: 1 measurement test.
- Full browser suite with these changes: 127 passed, 1 skipped.

## Known gaps

- A, B, E (speech) and F were not verified with a real microphone or speaker.
- Live token counts for research-synthesis model calls need a gateway hook. Only presence's own model calls are
  tracked live (`PRESENCE_CORTEX_ARCHITECTURE.md`).
- The presence appears in the app at compact size (header) and expanded size (panel). The normal and indicator
  sizes are rendered only in the gallery, because one live instance per page keeps one frame loop.
- Forced-colours mode, real phones and battery-saver mode are untested.
- The canonical art follows the references' style and complexity but is hand-tuned geometry, not a pixel match.
