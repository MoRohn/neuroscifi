# Visual design system

The reference art in `docs/assets/presence/reference-entity-states.png` and `reference-doodle-grid.png` sets the
style and the complexity ceiling. Snapshots of what the production renderer actually draws are in `snapshots/`
(`gallery-tierA-light.png`, `gallery-tierA-dark.png`, `gallery-tierB-*.png`, `gallery-sizes-*.png`), captured by
`tests/e2e/test_presence.py::test_gallery_renders_every_tier_with_the_production_renderer`.

## Principles

- An organic blob with bold rounded outlines, a few inner marks, sparse accent strokes and lots of empty space.
  The rule is maximum expressiveness through minimum geometry.
- The "eyes" are at most two small strokes: open (vertical), closed (arcs), focused (short dashes) or `> <` for
  working. No state relies on a facial expression. Each state differs in body shape, props and motion.
- No faces, big eyes, 3D, gradients, photorealism, emoji, text glyph fonts or neon.
- A canvas is a 100 × 100 viewBox (drawn with a 6-unit margin). Bold strokes are 4.4 units and fine strokes 2.8
  units at every size, so the stroke proportion stays constant. Line caps and joins are round. Paths are
  midpoint-quadratic curves, which round polyline corners slightly for the hand-drawn look.

## Palette (theme.mjs `TOKENS`, theme.css)

| Token | Light | Dark |
|---|---|---|
| background | #FFFFFF | #050505 |
| ink (stroke) | #101010 | #F7F7F7 |
| secondary | #666666 | #A8A8A8 |
| purple | #B45CFF | #C27DFF |
| blue | #4A9CFF | #6BAFFF |
| mint | #3DDDB2 | #52E3BE |
| yellow | #FFC928 | #FFD24A |
| pink | #F45CA7 | #F77DB9 |

In dark mode the accents are slightly lighter (contrast-adjusted), with no glow. Ink and secondary carry meaning
and pass contrast (ink ≥ 4.5:1, secondary ≥ 3:1 in both themes; `validator.paletteContrastErrors()` is checked in
`client_check.mjs`). Accents are decorative and sparse: at most three accent colours per scene, and never the only
carrier of a meaning.

## Tier A compositions (`web/canonical.mjs`)

| Name | Reference | Shapes | Motion |
|---|---|---|---|
| present | Idle/Present | four-lobed blob, two eye strokes, three purple strokes | compositor breathing (≈1 %) |
| thinking | Thinking | circle, tilted orbit ring, closed eyes, blue strokes | orbit swings ±9° over 7.5 s |
| working | Working | elongated blob with tail, `> <`, mint speed lines | speed lines shift, body bobs |
| responding | Responding | speech bubble with tail, three dots, yellow strokes | dots bounce with output level, else intensity |
| exploring | Curious/Exploring | scalloped body, eyes, pink "?" | gentle tilt, "?" twinkle |
| resting | Resting | low dome, closed eyes, purple "z z" | z's bob and decay to stillness (≈55 s) |
| reading | book | small body, open book, ground, purple strokes | page sways |
| computing | laptop | body, laptop, screen dot, mint strokes | dot blinks |
| researching | telescope | body, tube, legs, yellow sparkle | tube scans ±7° |
| discovery | rocket | hull, window, fins, flame, cloud ground | bob, flame flicker |
| ideation | bulb | bulb, filament, base lines, five yellow rays | rays twinkle |
| experiment | flask | flask, rim, purple liquid, bubbles | bubbles rise |
| exploration | map | folded map, folds, dashed route, x, purple pin | pin bobs |
| construction | cubes | three isometric cubes, mint strokes | cubes converge (fragments organise) |
| execution | gears | two gears, hubs, blue motion arcs | gears counter-rotate |
| listening | headphones | band, cups, five mint level bars | bars scale with the **measured** mic level only |
| growth | seedling | mound, stem, two leaves, mint arcs | leaves sway |
| achievement | mountain | peak, snow line, pole, purple flag, clouds | flag waves |

The system compositions use the same grammar: *asking* (present body with a pink "?"), *error* (deflated body with
"!"), *waiting* (present body with a small clock), *recovering* (a circular arrow) and *disconnected* (a dome in
secondary tone, flat eyes, a broken ring). The disconnected and error states are distinct in shape as well as
colour.

## Sizes and detail simplification (`contracts.SIZES`)

| Size | Rendered width | Keeps |
|---|---|---|
| indicator | ≤ 28 px | body, eyes |
| compact (headers, phones) | ≤ 64 px | body, eyes, three marks, two accents, ground, overlay slots |
| normal | ≤ 140 px | everything |
| expanded (panel, voice focus) | > 140 px | everything |

A `ResizeObserver` picks the size class. Changing it re-plans the scene, and the removed slots shrink into a point
instead of disappearing abruptly.

## UI placement

The presence button sits in the console header (`#presence`, between the brand and the instance status) and in
Pocket's header. The button shows the compact figure and a short word, which is hidden below 480 px. Activating
it moves the same SVG into an expanded panel (168 px). The panel shows the status sentence, facts (activity,
source, progress, intensity, conditions, composition) and the settings: theme, motion and motion intensity, plus
in the console an opt-in "model-proposed doodles" switch.
