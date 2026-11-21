# Accessibility

| Need | Implementation | Verified by |
|---|---|---|
| Text equivalent | The presence button's accessible name is "Presence: <activity>. Show details". It is described by a polite live region with the full sentence (activity, step *k of 4* when known, the task question, detail, other activities, conditions, voice muted / text only). No percentage is ever shown unless one is known, and none is known today. | e2e `test_background_research_is_subtle`, `test_pocket_presence_shows_own_family_task` |
| Live announcements | `a11y.Announcer`: polite for ordinary changes, **assertive only** for user-critical states (questions waiting) and errors. Coalesced: the latest wins, at most one announcement per 1.5 s, and duplicates are skipped. | unit (`interpret().assertive`), e2e live-region count |
| Keyboard | Native `<button aria-expanded aria-controls>` toggles the panel. Escape closes it and returns focus to the button. The panel controls are labelled `<select>`s and a checkbox, all with 44 px targets. | e2e `test_small_screens_console_and_pocket` (Escape → focus) |
| Reduced motion | `prefers-reduced-motion: reduce` with "Follow system", or the in-app *Motion: Reduced (static)*: no frame loop, no CSS breathing, scenes applied instantly. *Full* overrides the system. | e2e `test_reduced_motion_is_static_and_distinguishable` |
| Motion intensity | *Motion intensity*: Low (×0.5), Normal, High (×1.4), persisted in `nsf.presence.prefs`. | unit through `applyEnergy` |
| States distinguishable without motion | Every state has its own static geometry (body shape, props), not only a motion pattern. | e2e L: three states, three distinct static path sets |
| Not colour alone | Conditions are shapes ("?", "!", a broken ring, dashed eyes); disconnected differs from resting in shape and tone. Accents are decorative. | `VISUAL_DESIGN_SYSTEM.md`; gallery snapshots |
| Contrast | Ink ≥4.5:1 and secondary ≥3:1 in both themes (validator check). The app dark theme passes axe. | `client_check.mjs`; e2e `test_presence_axe[light/dark]` (0 serious/critical on the console — every view in dark — and on Pocket) |
| High contrast / forced colours | Strokes use `currentColor`-free CSS classes. In forced-colours mode the browser substitutes system colours for the strokes; not separately tested. | — (gap) |
| Static fallback | Without JavaScript or modules nothing is rendered and the rest of the app works unchanged. When the server cannot be reached, the presence says "Connection lost; activity unknown". | e2e locked/disconnected paths in the theme test |

The SVG itself is `aria-hidden`. The text is the accessible content, and the figure is an illustration of it.
