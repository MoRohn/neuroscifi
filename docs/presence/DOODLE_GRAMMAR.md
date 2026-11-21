# Doodle grammar

Doodles are built from primitives in three tiers. Every tier produces the same structure: 14 slots, resampled
point sets and palette tones. That shared structure is what lets any scene morph into any other.

## Primitives (`web/geometry.mjs`, `web/composer.mjs`)

curve, line, arc, loop, circle, dot, blob, ribbon, wave, spiral, ring, rays, orbit, branch, node, polygon,
container, connector. The canonical compositions also use the helpers `smoothClosed` (Catmull-Rom through key
points), `polar`, `flower`, `gear`, `sparkle`, `roundRect`, `bezier`, `quad`, `questionHook` and `zee` (glyph
strokes drawn as curves, never text).

Resampling: few-vertex polylines keep their corners, because points are spread along each segment in proportion
to its length. Dense curves are resampled uniformly by arc length. A closed shape repeats its first point, so every
slot renders as one path. `align()` picks the cyclic start and direction of the next shape that minimises travel
from the current one, so morphs never twist.

## Tier A — curated canonical compositions

There are 18 compositions from the reference images and 5 system compositions (`VISUAL_DESIGN_SYSTEM.md`). The
interpreter maps activities to them (`interpreter.ACTIVITY_CONCEPT`).

## Tier B — procedural composition

`composeSpec(concept)` looks for a semantic template. An exact template name matches first, then keywords. The
template returns a **spec** (the same data type as Tier C), and `specToComposition(spec)` turns it into slots:
the Tier A entity body itself (present, thinking, working, responding, exploring or resting) scaled to about 36
units and seated on the ground at the left, its eye marks (chosen by the energy rule, `VISUAL_DESIGN_SYSTEM.md`
§Eye marks), up to six elements in the prop area, a ground line and a motion kind.

Each template draws one recognisable object beside the entity, within the reference complexity ceiling (≤ 8
strokes, ≤ 2 accent colours): a magnifier over a page (evaluating evidence), overlapping views with a spark
(resolving contradictions), a balance (comparing), a fork with two destinations (exploring alternatives), a
three-node graph (mapping knowledge), two linked ideas (connecting ideas), stacked bricks (constructing), a beaker
with bubbles (experiment), a magnifier over a signal (anomalies), a phone under a magnifier (device), a thought
bubble with a card (memory), a learning curve on axes (feedback), a monitor with a live signal (monitoring), a wave
over spectrum bars (audio), a phone and a screen with a wireless link (coordinating), a page of text
(documentation) and a ticked box (finishing).

Element order is priority: the compact size keeps the first four ink marks and the first two accents, so the
object that carries the metaphor comes first. Motion: `drift` bobs every element; `scan` swings the first two
elements (e.g. magnifier and handle) about the first; `orbit` swings the accents about the first element; `pulse`
twinkles the accents; `converge` gathers the fragments. `rays` draws the reference accent fan (three short strokes
30° apart, radiating from a source behind the element along `rotation`).

Templates: researching documentation, evaluating evidence, resolving contradictions, comparing, exploring
alternatives, mapping knowledge, connecting ideas, constructing a solution, running an experiment, investigating
anomalies, inspecting a device, retrieving memory, learning from feedback, monitoring an event, analysing audio,
coordinating devices, finishing a task.

A concept that no template knows gets a deterministic **seeded constellation**: an FNV-1a hash of the words seeds
two or three primitives plus one accent dot in fixed spots. It is still validated, and a failure falls back to the
nearest canonical.

Composer constraint: every template and seeded spec passes `deface()`, which re-lays out a face-like arrangement
(two small marks side by side above a curve, or inside an outline) by raising one mark and moving the "mouth"
aside. A Tier C spec is not re-laid out: the validator rejects it and the procedural or canonical scene stays. *Evaluating evidence* and *comparing* are used in the live flow for verifying and comparing.

## Tier C — model-proposed spec (optional, off by default)

- Schema `nsf.presence.scene/1` (`presence/scene_spec.py SCHEMA`), typed and bounded:
  - `schema` (const), `concept` (`^[a-z][a-z ]{0,47}$`), `body` (6 enums), `eyes` (4: `open`, `closed`,
    `focused` = working chevrons, `none`; only `none` changes the drawing, the energy rule picks the marks),
    `motion` (7).
  - `elements[1..6]`, each `{primitive (18 enums), x 40–96, y 8–92, size 4–36, rotation −180–180,
    tone (7 palette names), count? 1–5, sides? 3–8}`.
  - `additionalProperties: false` everywhere.
- The request is made on the server by `propose()`: `Gateway.structured()` with `response_format: json_schema`
  (local-first, ledgered, privacy-screened for remote endpoints, one bounded repair), tracked in
  `presence.activity`.
- The server validates the schema plus geometric rules. The client validates again (`validator.validateSpec`)
  before any display.
- The model never supplies markup, path strings, colours, code or commands. The rendered concept text is always
  the requester's own words, not the model's. A failure (not configured, busy, unavailable, schema invalid,
  geometry invalid) returns `fallback`, the nearest canonical, and the client keeps its Tier B or canonical scene.
- Accepted specs are cached per concept on the server (≤64) and in the page. At most one proposal runs at a time.
- In the console the presence asks only when the owner enabled "model-proposed doodles" and the planner fell back
  to a seeded constellation. The gallery's Tier C form shows the full round trip.

## Validator (`web/validator.mjs`, `scene_spec.validate_spec`)

| Rule | Spec | Scene |
|---|---|---|
| Vocabulary | known keys, primitives, bodies, eyes, motions, tones | known slots, palette tones |
| Bounds | element boxes inside the prop area | every point finite and within −4…104 |
| Accents | ≤3 accent-coloured elements, ≤2 accent colours | ≤7 accent slots, ≤3 accent colours |
| Readability | no two elements stacked (same centre ±3, size ratio 0.8–1.25) | every visible slot ≥1.5 units across |
| No faces | no two small elements (dot, node, small circle/shape, short vertical line) side by side (Δy ≤ 5, Δx 4–34) above a flat curve/line within Δx+14, or inside a ring/outline | the same pattern on the finished slots (`faceErrors`), entity eyes excluded |
| Eye marks | — | none, or one sanctioned pair: two vertical strokes, two downward arcs or `> <` chevrons; same kind, level, clearly apart, inside the body (`eyeErrors`) |
| Complexity | ≤10 strokes | ≤72 + 13×28 points (fixed pool) |
| Animation budget | motion kind only | amplitudes ≤14 units, rotation ≤1.2 rad/s, ≤10 animated slots |
| Contrast | — | ink ≥4.5:1, secondary ≥3:1 against both backgrounds |

On failure the planner shows the nearest canonical (`planner.NEAREST`, mirrored by `scene_spec.NEAREST`) and records
`fallbackReason` (visible in the gallery captions and in `__nsfPresence.scene()`).
