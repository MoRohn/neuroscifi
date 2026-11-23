# Visual design system

The reference art in `docs/assets/presence/reference-entity-states.png` and `reference-doodle-grid.png` sets the
style and the complexity ceiling. Snapshots of what the production renderer actually draws are in `snapshots/`
(`gallery-tierA-light.png`, `gallery-tierA-dark.png`, `gallery-tierB-*.png`, `gallery-tierB-32px-*.png`,
`gallery-sizes-*.png`), captured by
`tests/e2e/test_presence.py::test_gallery_renders_every_tier_with_the_production_renderer`.

## Principles

- An organic blob with bold rounded outlines, a few inner marks, sparse accent strokes and lots of empty space.
  The rule is maximum expressiveness through minimum geometry.
- The "eyes" are at most two small marks and follow one rule (below). No state relies on a facial expression.
  Each state differs in body shape, props and motion.
- Props are objects beside or around the entity, never a second face (§No faces).
- No faces, big eyes, 3D, gradients, photorealism, emoji, text glyph fonts or neon.
- A canvas is a 100 × 100 viewBox (drawn with a 6-unit margin). Bold strokes are 4.4 units and fine strokes 2.8
  units at every size, so the stroke proportion stays constant. Line caps and joins are round. Paths are
  midpoint-quadratic curves, which round polyline corners slightly for the hand-drawn look.

## Eye marks: one rule (`canonical.eyeMarks`, `canonical.eyeKindFor`)

The entity's internal marks are always one of three pairs, chosen by activity energy and never by the concept:

| Energy | When | Marks |
|---|---|---|
| attentive | idle/present, curious, reading, asking, error, and any Tier B scene that is not calm or working | two short vertical strokes |
| calm / inward | thinking, resting, disconnected (grey), Tier B with the thinking or resting body or `orbit`/`still` motion | two downward arcs (U-shaped, fine stroke) |
| working | working, Tier B with the working body or `converge` motion | chevrons `> <` facing each other |

Tier A and Tier B draw the marks with the same function, scaled with the body and with floors that keep the two
marks apart at small sizes (open: gap ≥ 6.2 units; closed: gap ≥ 10 units). A Tier B/C spec's `eyes` value can
only switch the marks off (`none`); which marks are drawn follows the rule. `validator.eyeErrors` rejects anything
else on the finished geometry: horizontal dashes, a single dash, touching arcs that read as a squiggle, mixed kinds,
chevrons facing away, uneven heights, or marks outside the body.

## No faces

The spec forbids conventional cartoon faces. Apart from the entity's own eye marks, no scene may contain two small
marks (dots, small circles, short vertical strokes) side by side above a curve or line, or two such marks inside a
round outline. The composer re-lays such a layout out (one mark rises, the "mouth" moves aside), and the validator
rejects it on the spec (`composer.facePattern`, mirrored by `scene_spec.face_pattern` on the server) and on the
finished geometry (`validator.faceErrors`). The orbiting background dot rests at the upper left for the same reason.

## Palette (theme.mjs `TOKENS`, theme.css)

| Token | Light | Dark |
|---|---|---|
| background | #FFFFFF | #050505 |
| ink (stroke) | #101010 | #F7F7F7 |
| secondary | #666666 | #A8A8A8 |
| purple | #3F3F3F | #D6D6D6 |
| blue | #525252 | #BDBDBD |
| pink | #616161 | #B0B0B0 |
| mint | #707070 | #9E9E9E |
| yellow | #808080 | #EDEDED |
| halo (theme.css `--pc-halo`) | #E2DDDC | #2C282A |

The palette is grayscale (owner direction, 2026-11-23): the entity speaks in light, geometry and motion, not hue.
The accent names are identifiers of the scene contract (`contracts.mjs` `TONES`, `scene_spec.py`), kept so model
specs and templates stay valid; each renders as a step of luminance between the ink and the background. "Yellow",
used for sparks and rays, is the lightest step in light mode and the brightest in dark: it reads as light. Every
accent is at least 3:1 against the white background (yellow 3.9:1) and 6:1 against the dark one. The halo is the
entity's light on an interaction stage (MOTION_ENGINE.md, Life layer). Ink and secondary carry meaning
and pass contrast (ink ≥ 4.5:1, secondary ≥ 3:1 in both themes; `validator.paletteContrastErrors()` is checked in
`client_check.mjs`). Accents are decorative and sparse: at most three accent colours per scene, and never the only
carrier of a meaning.

## Tier A compositions (`web/canonical.mjs`)

| Name | Reference | Shapes | Motion |
|---|---|---|---|
| present | Idle/Present | four-lobed blob, two eye strokes, three accent strokes | breath and contour flow of the life layer (≈1 %) |
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
secondary tone, closed arcs, a broken ring). The disconnected and error states are distinct in shape as well as
colour.

## Sizes and detail simplification (`contracts.SIZES`)

| Size | Rendered width | Keeps | Where in the app |
|---|---|---|---|
| indicator | ≤ 28 px | body and eyes; an object doodle without eyes (headphones, gears, bulb) keeps body + its first two marks; the viewBox frames itself on them | Pocket navigation, beside "Research" (22 px) |
| compact | ≤ 64 px | body, eyes, four marks, two accents, ground, overlay slots | console header, Pocket header (40 px) |
| normal | ≤ 140 px | everything | (between compact and the stage while a window is resized) |
| expanded | > 140 px | everything | the interaction stage (`--stage-size`, 132–272 px: console research and voice, Pocket) and the panel (168 px) |

A `ResizeObserver` picks the size class. Changing it re-plans the scene, and the removed slots shrink into a point
instead of disappearing abruptly. Tier B element order is priority, so the compact level keeps the object that
carries the metaphor (`gallery-tierB-32px-*.png` shows every Tier B scene at 32 px). The indicator's framing follows
the moving geometry with smoothing, so a state change never jumps.

## UI placement

The presence is the hero of every interaction surface ([docs/ui/DESIGN_SYSTEM.md](../ui/DESIGN_SYSTEM.md), first
principle).

- **Console.** The presence button sits in the header (`#presence`, compact) on secondary views. While the research
  or voice view is open, `console-boot.mjs` moves the same element (button, panel and the one SVG) onto that view's
  stage (`.presence-stage[data-presence-dock]`): a sticky column beside the dialogue from 1200 px, centred above it
  below that. Nothing is re-created and the frame loop is the same; the size change re-plans the scene and morphs.
- **Pocket.** `pocket-boot.mjs` mounts the element on the stage that heads the first column, above the research
  question (or the field session before pairing). The navigation's "Research" link carries the small indicator
  (`Presence.addIndicator`): a second, decorative (`aria-hidden`) SVG drawn from the same engine by the same frame
  loop, so a page still has one engine, one loop and one live region. Off screen, both pause.
- The button shows the figure and a short word (hidden below 480 px). Activating it opens the panel; from the header the SVG moves into the
  panel (168 px), on a stage it stays where it is, with the status sentence, facts (activity, source, progress, intensity, conditions, composition)
  and the settings: theme, motion and motion intensity, plus in the console an opt-in "model-proposed doodles"
  switch. The presence's controls are marked `data-keep`, so the console's offline mode leaves them usable.
