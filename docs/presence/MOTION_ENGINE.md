# Motion engine

Source: `src/neuroscifi/presence/web/motion.mjs` (engine) and `renderer.mjs` (`Player`, the frame loop).

## Springs

Every coordinate of every slot follows its target on a damped spring: `a = k(target − x) − c·v`, with `k = 140`
and `c = 2·0.8·√k` (slightly underdamped, so there is a soft elastic settle). Integration is semi-implicit Euler,
sub-stepped to ≤1/120 s. Accelerations are clamped to 5200 units/s² and velocities to 260 units/s (`BUDGET`). A
morph across the whole canvas therefore takes at least ~0.4 s, and nothing can jump.

- **Morphing**: shapes with the same point count interpolate point by point. `geometry.align()` re-orders the new
  points to the nearest cyclic start and direction.
- **Appearing or leaving**: a new slot grows out of its own centroid, and a slot that is no longer needed shrinks
  into its centroid while fading. This is an intermediate abstraction instead of a crossfade.
- **Interruption**: `setScene` changes only the targets. Positions and velocities carry over, so an interrupted
  morph continues smoothly from where it is (tested: four scene changes 4–9 frames apart give a per-frame
  displacement ≤ vmax·dt, `client_check.mjs`; in the browser, a voice barge-in moves the body ≤20 units per 16 ms
  sample and the recorded peak point speed stays ≤260).
- **Fills** (flask liquid, flame, flag) switch only once the morph is within 2.5 units, so a filled shape never
  smears.

## Continuous motion (applied to targets, never positions)

| Kind | Used by | Driven by |
|---|---|---|
| swing (rotate ±amp about a pivot) | thinking orbit, telescope scan, leaves, flag | motion energy |
| spin (integrated phase) | gears, clock hand, recovering arrow | motion energy |
| orbit (centroid along a circle) | background-activity dot | motion energy (≥0.4) |
| shift / bob / converge | speed lines, bubbles, cubes organising | motion energy |
| twinkle (scale about own centre) | accent strokes | motion energy |
| level (vertical scale) | listening bars | **measured RMS level only**; without a level the bars stay still |
| dots (bounce) | responding dots | measured output level while speaking, else intensity |
| decay | resting z's | exponential decay (12 s), then stillness |

Motion energy is `(0.35 + 0.65·smoothed intensity) × {low 0.5, normal 1, high 1.4} × (0.5 subtle) × (0.5 battery
saver)`.

## Life layer (motion.mjs `LIFE`)

While it is on screen the entity is never frozen. On top of the spring positions sits a continuous **life field**:
what is drawn is `physics position + life`, computed once per frame (`engine.view(slot)`). The physics keeps its
own guarantees (settling, velocity limits, interruption without snapping), and the life layer never re-plans a scene.
Every input is a real signal; nothing here invents activity.

| Component | What moves | Driven by |
|---|---|---|
| contour flow | the body outline, three slow radial harmonics (2, 3, 5 lobes; periods 9.1, 6.3, 4.3 s) | intensity of the real activity: 1.1 % of the body radius at rest, up to 3 % when busy, + 5.5 % × the measured audio level; the phases speed up with both |
| breath | body and eyes, vertical scale about the ground contact | 1.2 % over 6.4 s at rest, up to 2.2 % over 2.9 s at full intensity |
| attention | eyes (up to 8 % of the body radius) and a slight lean of the whole body (3.5 %) | the direction to the owner's pointer or last touch, from the largest view on screen; back to rest after 6 s without a move (1.6 s after a touch) or when the mouse leaves the window |
| inertia | body, eyes and (at 55 %) props | scrolling anywhere on the page: each scroll step pushes a target that decays, and an underdamped spring follows it; soft limits (tanh) of 5 units up and 1.2 down, so nothing sticks at a bound |
| squash | body and eyes: a quick spring | each keystroke in a text field |
| voice swell | body and eyes by up to 4.5 % (props half) | the measured audio level, in real time (fast attack, slower release) |
| ripple | one wave round the outline (≈0.12 s attack, ≈0.55 s decay) | a real stage transition of the task being shown (its `step i of n` changes; a switch to another task does not count) |
| light | the halo behind the figure on an interaction stage: strength and reach (`engine.glow()`) | intensity, audio level, the ripple; a slow breath at rest; low while dim |
| props | a vertical drift, out of phase per slot | as the breath; the ground never moves |

Vigour scales the whole layer: the owner's *Motion intensity* (low 0.5, normal 1, high 1.4) × 0.5 for background work ×
0.5 on battery saver × 0.55 while the window is in the background. **Dim**: while disconnected, signed out or not
yet connected (nothing is known about the individual's activity) vigour drops to 0.3, the light falls and attention
is off, so the entity never looks lively about a state it cannot know.

Measured with `motion.mjs` under node, Normal intensity, the canonical *present* body (radius 34.8 units): the drawn
outline stays within 2.2 % of the body radius of the physics at rest (0.8 units), 5 % when busy and 7 % at High;
props drift 0.3 units at rest, 0.8 when busy and 1.1 at High. Every input is smoothed or integrated, and inputs are
ignored while the life layer is off and dropped when the loop restarts after a pause (`rest()`), so nothing piles
up and lands later. `client_check.mjs` drives a storm (three scroll events per frame for half a second, a keystroke,
a stage change and a gaze flip) at 60 and 30 fps: the drawn outline moves at most about 22 units/s (checked < 40;
the physics budget is 260). A non-finite input restarts the life layer instead of hiding the figure.

## Frame loop and budgets

- There is one `requestAnimationFrame` chain per presence (`Player`), and `start()` does nothing while one is
  running. The recorded `maxConcurrentLoops` is always 1.
- While the physics moves (a morph, a scene animation) the loop runs at the display rate. While only the life layer
  moves it is capped: at `LIFE.responsiveFps` (60 fps) while it answers an input (pointer, scroll, keystroke, voice
  level, ripple), else at `LIFE.ambientFps` (30 fps), where slow motion stays smooth at half the work. The loop pauses
  when the page is hidden (`visibilitychange`) or every view is off screen (`IntersectionObserver`), and does not run
  at all under reduced motion. `stats().settled` reports whether the physics has settled; `stats().ambientFrames`
  counts the 30 fps frames.
- Battery saver (`navigator.getBattery`, when available, not charging and below 20 %): capped at 30 fps and half
  energy.
- Rapid state changes are coalesced: at most one scene commit every 220 ms, and the latest state wins. A level
  change alone never re-plans the scene; it only updates `engine.level`.
- The DOM is a fixed pool of 14 `<path>` elements per presence, never re-created. Paths that are hidden get the
  `pc-off` class.

## Reduced motion

When `prefers-reduced-motion: reduce` is set (with the preference on "Follow system") or the preference is
"Reduced", scenes are applied instantly, no frame loop runs, and the life layer is off (no breath, flow, attention
or inertia; the halo stays at its resting strength). Every state still
has its own static geometry (tested: listening, responding and idle give three distinct path sets with zero frames).
