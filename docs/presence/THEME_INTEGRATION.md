# Theme integration

There is one app theme system for the console and Pocket. Before this release neither app had a dark theme, so no
competing setting is involved.

| Part | File | Role |
|---|---|---|
| Preference | localStorage `nsf.theme` = `system` (default) \| `light` \| `dark` | persisted per app origin |
| Boot | `presence/web/theme-boot.js` (classic script in `<head>`, CSP `script-src 'self'`) | sets `<html data-theme>` and `data-theme-pref` before first paint, so the wrong theme never flashes |
| Adapter | `presence/web/theme.mjs` | `setThemePref`, `onThemeChange`, follows `prefers-color-scheme` when on system, syncs other tabs (`storage` event), WCAG contrast helpers |
| Styles | `presence/web/theme.css` | presence tokens (`--pc-*`) for `[data-theme]`; dark overrides for the console's tokens and literal colours (`html[data-app=console]`) and for Pocket (`html[data-app=pocket]`) |
| Control | the presence panel → *Theme* (Follow system / Light / Dark) | the only theme control in either app |

Both `index.html` files load `theme-boot.js` and `theme.css` and carry `data-app`. Nothing else in the apps
changes.

## Why a switch never resets the presence

Every stroke and fill colour is a CSS class (`pc-t-ink`, `pc-t-purple`, `pc-fill`) that resolves to a custom
property. A switch changes only `data-theme` on `<html>`, so no path is re-created, the scene is not re-planned and
the frame loop is not restarted. `tests/e2e/test_presence.py::test_theme_toggle_mid_animation_keeps_state_and_motion`
checks all of this during a real research task in its *reading* stage:

- the body stroke is `rgb(16,16,16)` in light and `rgb(247,247,247)` in dark;
- the page background changes;
- the transition count and `loopStarts` stay the same while frames keep increasing;
- the concept stays *reading*;
- the body path coordinates stay within 6 units across the switch (the motion continues);
- after a reload the page starts dark, with no flash.

## Contrast

In dark, the console's primary colour becomes #25735f (5.7:1 with white text). Links, muted text and warnings use
the lighter tones in `theme.css`. axe-core finds 0 serious or critical issues in either theme on the console (every
view, dark) and on Pocket (`test_presence_axe[light|dark]`).
