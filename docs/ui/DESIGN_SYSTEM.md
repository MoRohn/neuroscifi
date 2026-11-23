# NeuroSciFi design system

One design language for the owner console (desktop and laptop, loopback) and Pocket (phones and tablets over
HTTPS), across every size, input, theme and browser engine. The audit that led here, with before and after
screenshots, is [UI_AUDIT.md](UI_AUDIT.md).

## The first principle: the presence is the hero

When someone interacts with NeuroSciFi (asking, researching, listening, speaking, reviewing an answer), the
primary element on screen is the **Presence Cortex entity**: the minimal, abstract, non-humanoid, physics-driven
figure that shows the agent's presence and cognitive state through small changes in light, geometry and motion
([PRESENCE_CORTEX_ARCHITECTURE.md](../presence/PRESENCE_CORTEX_ARCHITECTURE.md)).

- **Placement.** Every interaction surface opens on a *presence stage* (`.presence-stage[data-presence-dock]`).
  The one presence element (button, panel and its single SVG) moves there; nothing is re-created and the frame loop
  is the same.
  - Console **Research** and **Voice**: on screens 1200 px and wider the stage is a sticky column beside the dialogue
    (the question form, then the report or the voice panel) and takes the free width, so on 1920 and 2560 px screens
    the entity gets room while the text keeps a reading width. Below 1200 px it sits centred above the dialogue.
  - **Pocket**: the stage heads the first column, directly above the research question (or the field session
    before pairing); on phones that is the upper middle of the screen, within one-handed reach of the input below it.
  - Secondary views (overview, teaching, evidence, knowledge lists, graphs, checkpoints, settings) keep the compact
    header button.
- **Size.** `--stage-size`: `clamp(148px, 42vw, 212px)` on phones, 216 px from 600 px, 232 px from 1200 px, 272 px
  from 1800 px, 304 px on the console from 2200 px; 132 px on a phone held sideways. The renderer's `expanded` level (> 140 px) draws every slot, so no
  new size class was needed.
- **Calm surroundings.** No border or card around the stage. The surrounding UI is grayscale, so the entity's
  light, geometry and motion, and its few multicoloured flairs, are the only living, colourful things on the
  surface. The stage adds no motion of its own, and
  busy messages carry no spinner: the entity is the activity indicator.
- **Alive.** On screen the entity is never frozen: the life layer (docs/presence/MOTION_ENGINE.md) keeps it
  breathing and flowing, deepens with real activity, swells with the measured voice level, turns its attention to
  the owner's pointer, carries the inertia of scrolling and answers keystrokes. Its light is a soft halo behind
  the figure whose strength follows the activity; real stage transitions send a wave round its outline.
- **Honesty.** The stage only shows what the presence engine draws from real system state. No faces, avatars,
  chat bubbles from the entity or decorative motion are added. The short word under the figure is the presence's
  own `pc-word` (hidden from assistive technology; the live region and description are unchanged).
- **Panel.** Activating the figure opens its panel (status sentence, facts, theme and motion settings). On a stage
  the figure stays where it is and the panel carries the words; on phones the panel is a bottom sheet above the home
  indicator.
- **Modes.** Reduced motion keeps the static but expressive geometry and turns the life layer off; hidden tabs and
  off-screen stages pause the loop; forced colours drop the halo and keep the strokes in system colours.

## Files

| File | Role |
|---|---|
| `presence/web/ui.css` | **The system**: tokens (light, dark, more contrast, forced colours), base elements and shared components, the presence stage. Loaded first by both apps (`/presence/ui.css`); cached by Pocket's shell. |
| `console/web/console.css` | Console layout only (header, navigation, views, report, autonomy, tables). Tokens only. |
| `pocket/web/style.css` | Pocket layout only (header, chips, cards, research and evidence review). Tokens only. |
| `presence/web/theme.css` | The presence element and panel (`--pc-*` tokens, mapped to the UI tokens), plus the generated wordmark block. |
| `voice/web/voice.css` | The voice panel, on the same tokens. |
| `presence/web/theme-boot.js`, `theme.mjs` | Resolve the theme before first paint, and keep `<meta name="color-scheme">` and `<meta name="theme-color">` in step with it. |

No file outside `ui.css` defines a colour (the presence palette and the wordmark aside).

## Colour

Grayscale (owner direction, 2026-11-23): warm graphite neutrals with a trace of rose ("gray matter", in step with
the wordmark), graphite actions, and hue only for the two signals that must never be missed, warning and danger,
and for the Presence entity's own minor multicoloured flairs (docs/presence/VISUAL_DESIGN_SYSTEM.md).
Primary actions are the ink itself: a graphite button on light, a light button on graphite in dark. Values are tokens on `:root` / `[data-theme]`; `tests/e2e/test_ui_matrix.py` reads them from
the running page and checks every pair below.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#F6F4F3` | `#121011` | page; also the browser's theme-color |
| `--surface` | `#FFFFFF` | `#1A1719` | cards, header, fields |
| `--surface-muted` | `#F0EDEC` | `#221F21` | table heads, quotes, hover |
| `--surface-sunken` | `#E9E5E4` | `#0C0A0B` | meter tracks |
| `--line` / `--line-strong` | `#E0DAD9` / `#8A8084` | `#302A2D` / `#7A7075` | hairlines / control borders (>= 3:1) |
| `--ink` / `--ink-muted` | `#1D191B` / `#595155` | `#EEE9EA` / `#B5ABAF` | text (15.9:1) / secondary text (>= 6.1:1) |
| `--primary` (+ `--on-primary`, `--primary-hover`) | `#1D191B` (white text) | `#EEE9EA` (`#141213` text) | primary buttons, current place (>= 12:1; hover `#3A3437` / `#D6D0D2`, text stays >= 4.5:1) |
| `--primary-soft` / `--primary-line` | `#ECE8E7` / `#BDB5B7` | `#2A2628` / `#554D51` | current navigation item, quotes' rule, sensing card |
| `--link` | `#1D191B` | `#EEE9EA` | links and citations: the ink, always underlined (citations with a quieter `--line-strong` underline that turns to ink on hover) |
| `--accent` | `#595155` | `#C9C0C3` | counts (e.g. "2 open") |
| `--focus` | `#1D191B` | `#EEE9EA` | the 3 px focus ring, set off from the control (2 px on buttons, 1 px on fields; inset on navigation), >= 3:1 on every surface |
| `--ok`, `--info`, `--neutral` | graphite tones on `*-soft` grounds with `*-line` borders | light tones on deep grounds | done (ink with a stronger border), informational and neutral (muted) states; the word always carries the meaning (all >= 6.6:1) |
| `--warn`, `--danger` | `#8A3A14`, `#A3361A` on warm grounds | `#F2A88A`, `#F59C84` on deep grounds | the only hues: warnings, errors and destructive actions |

Rules:

- **Never colour alone.** A state is a word; badges carry a border; claim states in Pocket's evidence review add a
  glyph and a border pattern (solid, double, dotted, dashed); report stages are written out with a mark; the
  current navigation item has a bar (underline on narrow screens, left edge on wide ones); disabled buttons have a
  dashed border.
- **More contrast** (`prefers-contrast: more`): hairlines become real borders, secondary text darkens, links get a
  2 px underline.
- **Forced colours** (Windows High Contrast and others): system colours win; selects get their native arrow back;
  badges, cards, dialogs and toasts keep a `CanvasText` border; the current navigation item and report stage get a
  `Highlight` outline; budget meters keep their `Highlight` fill (`test_t4_ux`).

## Type

System faces only: the CSP has no `font-src`, and a downloaded face would differ per device anyway.

| Token | Stack | Use |
|---|---|---|
| `--font-sans` | `system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI Variable Text", "Segoe UI", Roboto, "Noto Sans", Ubuntu, Cantarell, …` | the interface: SF on Apple, Segoe UI Variable on Windows 11, Roboto on Android, the desktop's face on Linux |
| `--font-serif` | `"Iowan Old Style", Charter, "Sitka Text", Cambria, "Noto Serif", Georgia, serif` | what the sources and the individual say: the research question, quoted evidence, verdicts, knowledge statements, NeuroSciFi's voice replies |
| `--font-mono` | `ui-monospace, "SF Mono", "Cascadia Code", Menlo, Consolas, "Roboto Mono", …` | ids, fingerprints, code |

"DM Sans" (named in Pocket's old stylesheet) was never bundled: it only applied on devices that happened to have
it installed. It is gone.

Scale (rem, so text-only zoom scales it): `--fs-xs` .78, `--fs-sm` .875, `--fs-md` 1, `--fs-lg` 1.125,
`--fs-xl` 1.375, `--fs-2xl` clamp(1.625–2.125) for view titles, `--fs-3xl` clamp(2–2.75). Body line height 1.55;
headings 1.2 with `text-wrap: balance`; paragraphs `text-wrap: pretty`; numbers in tables, meters and metadata
use tabular figures. Report section labels and table heads are small caps (`font-variant-caps: all-small-caps`, never
`text-transform: uppercase`, so screen readers and `innerText` keep the real words) over a hairline. Prose and answers keep
`--measure` (68ch).

## Space, shape, elevation, motion

- Spacing: a 4 px base, `--sp-1` 4 … `--sp-10` 72. Page gutters `--gutter`: 16 px, 24 px from 600 px, 28 px from
  1200 px, plus the safe-area insets.
- Radii: `--r-xs` 6, `--r-sm` 8 (controls), `--r-md` 12, `--r-lg` 16 (cards), `--r-xl` 24, `--r-pill`.
- Elevation: `--shadow-1` (cards at rest), `--shadow-2` (message bar, toast), `--shadow-3` (dialogs, the presence
  panel). Dark shadows are deeper with a faint inner highlight.
- Motion: `--dur-1` 120 ms, `--dur-2` 200 ms, `--dur-3` 320 ms, `--ease-out`. Only state changes animate (hover,
  press, a switch, a meter's fill). `prefers-reduced-motion: reduce` sets every duration to zero; the presence
  keeps its own reduced-motion rules.

## Components

| Component | Markup | Notes |
|---|---|---|
| Button | `<button>`, `.primary`, `.danger` | 44 px tall on every pointer; 1 px press; dashed border when disabled; no hover colour on touch screens; colours never transition (a re-enabled button is never caught with its old ground) |
| Field | `input`, `select`, `textarea` | 44 px; 1 px `--line-strong` border; focus ring plus a focus-coloured border; `aria-invalid` gets a 2 px danger border; one gradient chevron for `select` on every engine |
| Checkbox, radio, range | native | `accent-color` from the theme; a label around a checkbox is the whole 44 px target |
| File | `input[type=file]` | the picker button is a real 44 px button |
| Disclosure | `details > summary` | 44 px; one rotating chevron on every engine |
| Card | `.card`, Pocket `section` | surface, hairline, 16 px radius, `--shadow-1`; nested sections are muted |
| Badge | `.badge.ok / .warn / .info / .neutral` | word + tone + border |
| Status message | `.msg.ok / .error / .busy` | empty messages leave the layout but stay in the accessibility tree |
| Table | `table`, `.tablewrap`, `table.stack` | uppercase small heads; below 880 px of container width, stacked labelled rows |
| Navigation | console `#nav`, Pocket `nav` | console: sticky tab row with an underline marker under 1000 px, sidebar with a left bar above; Pocket: sticky chips; both scroll sideways with a fade on the trailing edge |
| Dialog, toast | `dialog`, `.toast` | `--shadow-3` and a scrim; the toast sits above the home indicator |
| Presence stage | `.presence-stage[data-presence-dock]` | see the first principle |

## Platform conventions

- **iOS / iPadOS:** `viewport-fit=cover` with `env(safe-area-inset-*)` padding on headers, page edges, the message
  bar, the toast and the presence bottom sheet; `apple-mobile-web-app-title`; no tap highlight;
  `touch-action: manipulation` on controls (no double-tap zoom delay); `overscroll-behavior` on the page and on
  horizontal scrollers.
- **Android:** `theme-color` follows the resolved theme (the address bar matches the page); the manifest's
  `background_color` and `theme_color` are the page colour, so the splash screen and task switcher match.
- **Windows:** Segoe UI Variable; forced-colours fallbacks as above.
- **macOS:** antialiased font smoothing (no halo on dark), SF via `system-ui`.
- **Linux:** `system-ui` resolves to the desktop's face (Cantarell, Noto Sans, Ubuntu).
- **All:** `<meta name="color-scheme">` follows the resolved theme, so scrollbars and native controls match it
  before and after the stylesheets load; text-size-adjust is pinned so rotating a phone does not inflate text;
  layouts are logical-property friendly where cheap (`text-align: start`).

## Checks

- `tests/e2e/test_ui_matrix.py`: the console (overview, research, autonomy, knowledge, voice) and Pocket at 320,
  360, 390, 414, 768, 1024, 1280, 1440, 1920 and 2560 px in light and dark, coarse pointers up to 1024 px: no
  horizontal overflow, every target >= 44 px on coarse pointers, the entity on the stage of the interaction views
  (>= 140 px, inside the viewport, above Pocket's question); every token pair above >= 4.5:1 (text) or >= 3:1 (UI);
  the browser chrome metas follow the theme; forced colours keep the current item, selects, badges and cards
  legible.
- `tests/e2e/test_presence.py`: the hero placement and the panel from the stage; everything the presence already
  guaranteed (one SVG, one loop, theme switch without re-planning).
- The axe sweeps of every view (console 390, 1024, 1280 px; Pocket; light and dark) and the accessibility-tree
  test.
