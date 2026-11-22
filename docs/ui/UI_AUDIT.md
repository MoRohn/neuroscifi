# UI audit: console and Pocket (v0.7, before and after)

Scope: the owner console (`console/web`), Pocket (`pocket/web`), the shared presence chrome (`presence/web`) and the
voice panel (`voice/web`). Method: both apps run on scratch instances seeded with a finished fact check, a monitor,
taught beliefs, a promoted graph, checkpoints, autonomy budgets and a paired Pocket with a completed research
report (the e2e fixtures), screenshotted at 320, 360, 390, 414, 768, 1024, 1280, 1440, 1920 and 2560 px, light and
dark, coarse (touch) and fine pointers, and measured in the page (horizontal overflow, targets under 44 px on
coarse pointers). Then the same in Firefox and WebKit. The result is the design system in
[DESIGN_SYSTEM.md](DESIGN_SYSTEM.md) and the gallery in [gallery/](gallery/).

## Engines and devices

| Engine | How it ran here (Arch Linux, aarch64) | Coverage |
|---|---|---|
| Chromium 153 (`/usr/bin/chromium`) | real, headless | the full matrix above; touch (`has_touch`, `is_mobile`) emulation for phones and tablets; `forced-colors: active`; the whole e2e suite and the new `test_ui_matrix.py` |
| Firefox 157 (Playwright build) | real, headless | phone (390, touch), tablet (768) and desktop (1280, 1920) layouts of both apps, light and dark |
| WebKit 27.2 (Playwright WPE build, the Safari engine) | real, headless, after supplying its missing Ubuntu libraries (ICU 74, libxml2, flite, libbacktrace) locally; Pocket with service workers blocked, because this build stalls the page while a worker installs (the same with the code before this work; Safari itself supports them) | the same as Firefox |

Not available here: real iOS/Android devices, Windows High Contrast with real system palettes, macOS font
rendering. Those conventions are implemented to their specifications (safe areas, theme-color,
forced colours, font smoothing) and checked by emulation (Chromium `forced_colors`, WebKit's Safari user agent), not
on hardware.

## Findings (before) and what changed

Severity: **High** breaks the product's intent or a WCAG AA rule; **Medium** hurts a common task or a platform;
**Low** is polish. Screenshots are in [gallery/](gallery/) (`before-*` / `after-*`).

| # | Severity | Surface | Finding (before) | Fix (after) | Gallery |
|---|---|---|---|---|---|
| U-1 | High | both | The presence entity, the product's way of showing the agent's state, was a 40 px header icon, and a 72 px figure squeezed into the research and voice title bars; on Pocket a 40 px header icon. The form, not the agent, was the focal point while asking and waiting. | A presence stage on every interaction surface: a sticky column beside the dialogue on wide screens, centred above the question on phones and tablets, 148–272 px, calm surroundings; the figure stays when its panel opens; bottom-sheet panel on phones. | `01`, `02`, `03`, `05`, `07` |
| U-2 | High | both | Two style systems: `console.css` "copied from Pocket", Pocket's minified literal colours; dark mode bolted on by about 30 high-specificity overrides in `theme.css`, so every new component needed its own dark patch; the voice panel guessed tokens through fallbacks. | One token set in `presence/web/ui.css` (light, dark, more contrast, forced colours) and shared components; both app stylesheets are layout-only and use tokens; the overrides are gone. | all |
| U-3 | Medium | Pocket | "DM Sans" first in Pocket's font stack but never bundled (the CSP has no `font-src`): Pocket rendered in DM Sans only on devices that had it installed, the console in the system face, so the two apps differed on the same device. | System stacks per platform for both apps, a serif stack for claims and quoted evidence, a monospace stack for ids. | `05`, `06` |
| U-4 | Medium | Pocket | Section navigation as underlined links wrapping onto three lines at 390 px; the Voice link 42 px wide. | A sticky row of 44 px chips that scrolls sideways with a trailing fade; Research and Voice first. | `05` |
| U-5 | High | Pocket | Targets under 44 px on touch screens (measured): disclosure summaries 41 px, file pickers 21 px, checkboxes 24 px whose labels did not enlarge them, the Voice link. | Shared 44 px summaries and file buttons; a label around a checkbox is a 44 px flex target; measured 0 small targets at every width (`test_ui_matrix`). | `05` |
| U-6 | Medium | Pocket | On a phone the research question sat about 1,000 px down the page, under the field session and device memory; a 4.6 rem serif "Pocket" title filled the first screen. | Research and Voice first in the first column, under the stage: on a 390 × 844 phone the stage sits in the upper middle and the question field starts within the first screen. Sections are cards. | `05`, `08` |
| U-7 | Medium | console | The header ignored the content grid (brand at the window edge, content centred at 1320 px); on phones it wrapped into three loose lines. | Header aligned to the shell; the wordmark slot and "Owner console" on the wordmark's baseline; instance status as a compact line under it on phones. | `01`, `03`, `09` |
| U-8 | Medium | console | On phones the view tabs scrolled sideways with no affordance (a cut-off "uestions") and scrolled away with the page; the current tab was a heavy filled pill. | A sticky, translucent tab row with an underline marker and a trailing fade; on wide screens a sidebar with a quiet left bar. | `03` |
| U-9 | Medium | console | The research report read as a flat list: section labels (Answer, Verdict, Sources…) were small bold headings, quotes italic on a green tint, lines up to about 120 characters at 1440 px and wider. | A document: the question in serif, sections under small uppercase labels with hairlines, quotes in the serif on a quiet ground with an evergreen rule, a larger verdict line, a 68ch measure. | `01`, `04` |
| U-10 | Low | console | Wide screens (1920, 2560) kept a 1320 px column with empty margins. | The shell grows to 1600 px from 1700 px and 1840 px from 2200 px; the stage takes the free width beside a reading column; three-column card grids. | `04` |
| U-11 | Medium | both | `<meta name="color-scheme" content="light">` and a green `theme-color` in both themes: in dark, native scrollbars and controls stayed light until CSS loaded and Android's address bar stayed green. | Both metas follow the resolved theme (before first paint and on every change); the address bar matches the page. | — |
| U-12 | Low | Pocket | The manifest's `background_color` and `theme_color` belonged to the old palette (green splash bar on a pale page). | Page-coloured manifest colours. A maskable icon was tried and dropped: Chrome downloads manifest icons outside the service worker, so an offline reopen logged a failed fetch, and the brand icon is hash-locked, so it cannot become full-bleed itself (see What remains). | — |
| U-13 | Medium | both | No safe-area handling: in landscape on notched phones, and above the home indicator, the sticky message bar and toast could sit under system UI. | `viewport-fit=cover` with `env(safe-area-inset-*)` on headers, page edges, the message bar, the toast and the presence sheet. | — |
| U-14 | Low | both | Disabled primary buttons were pale green at 42 % opacity: they read as a lighter primary, not as unavailable, with low-contrast text. | Disabled is a muted surface with a dashed border and full-contrast muted text, on every button. | `05` |
| U-15 | Low | console | Summaries laid out as flex lost their disclosure triangle in Chromium and Firefox ("Technical detail", "Change web settings"). | One CSS chevron that rotates on open, on every engine. | `01` |
| U-16 | Low | voice | The volume slider was 28 px tall on touch screens; ranges and radios used the engine's default blue, also in dark. | 44 px on coarse pointers; accent colour from the theme; NeuroSciFi's replies in the claim serif. | `07` |
| U-17 | Medium | both | Dark mode was a green-black (#070908) with green-tinted greys: cards barely separated from the page, and it clashed with the new gray-matter wordmark. | A warm graphite dark set with distinct page, card and muted levels, deeper shadows with a faint inner highlight; every text pair >= 6:1. | `02`, `04`, `06` |
| U-18 | Low | both | Selects drew engine-specific arrows (Linux Chromium, Firefox and WebKit all differ). | One CSP-safe gradient chevron; forced colours get the native arrow back. | `01` |
| U-20 | Low | both | On touch screens the 44 px citation links ([1], [2]) stretched the line they sat on, so sentences and quotes had uneven spacing. | Same 44 px targets with negative block margins: the line keeps its spacing. | — |
| U-19 | Low | both | Busy states showed only text, while the presence (which already shows activity) was out of sight. | No spinner by design: the entity on the stage is the activity indicator. | `01` |

## Gallery

Pairs are the same state, size and theme before and after (`docs/ui/gallery/before-NN-*.png`, `after-NN-*.png`);
10–14 are after only.

| # | State |
|---|---|
| 01 | Console, Research, 1440 px, light: a finished fact check open, the entity on its stage beside the question |
| 02 | Console, Voice, 1280 px, dark |
| 03 | Console, Research, 390 px phone, light: header, sticky tabs, the stage above the question |
| 04 | Console, Research, 2560 px, dark: the stage takes the free width beside a reading column |
| 05 | Pocket, 390 px phone, light, paired: chips, the stage, the research question |
| 06 | Pocket, research report, 390 px phone, dark |
| 07 | Console, Voice, 390 px phone, dark |
| 08 | Pocket, 768 px tablet, light: two columns |
| 09 | Console, Overview, 1280 px, light: a secondary view keeps the compact header presence |
| 10 | Console, Research, 1280 px, `forced-colors: active` |
| 11, 12 | Firefox 157: Pocket 390 px light; console Research 1280 px dark |
| 13, 14 | WebKit 27.2: Pocket 390 px dark; console Research 1280 px light |

## Measured after

- No horizontal overflow at any of the ten widths, light or dark, console views and Pocket (`test_ui_matrix`).
- 0 targets under 44 px on coarse pointers (before: 6–8 per Pocket state; the console's voice slider).
- Every colour-token text pair >= 4.5:1 and every control and focus pair >= 3:1 in both themes, read from the
  running page; the wordmark's colours >= 4.5:1 on the new backgrounds (`test_brand_wordmark`).
- axe: 0 moderate, serious or critical violations on every console view at 390, 1024 and 1280 px and on Pocket, in
  light and dark (the existing sweeps).

## What remains

- Hardware checks on iOS Safari (standalone and in-browser), Android Chrome (installed PWA, adaptive icon), and
  Windows High Contrast with real system palettes.
- A maskable (full-bleed) Android icon that Chrome can fetch offline, for example a PNG shipped with the
  manifest and pre-cached through a page reference; the brand icon itself is hash-locked.
- iOS ignores SVG home-screen icons: a PNG `apple-touch-icon` needs a binary asset, a server route and a shell
  entry; not added in this pass.
- Phones: after a question is sent, nothing next to the stage names the question being read (the entity shows the
  state; the question is in the list further down). A one-line caption under the stage would close the gap.
- The presence ranks a fresh open question above a running research task, so the console can say "Waiting for your
  answer" while research reads. That is the presence engine's rule (user-critical first), unchanged here.
- Pocket once showed "Your Spark has no research worker running" while its list already said "Reading · step 2 of
  4" (seen with the e2e one-shot worker during the README captures, not with a persistent worker); unconfirmed,
  possibly the 10 s list refresh lagging.
- Pocket's pairing card stays at the end of the page; a first-run layout that leads with pairing would help new
  owners.
- RTL: layouts use `start` alignment and logical gaps where cheap, but no RTL locale exists to verify against.
