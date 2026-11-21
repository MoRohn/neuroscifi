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
saver)`. Idle-like compositions (present, asking, waiting) breathe with a CSS compositor animation (`pc-breathe`,
about 1 % over 6.5 s), so they need no script per frame.

## Frame loop and budgets

- There is one `requestAnimationFrame` chain per presence (`Player`), and `start()` does nothing while one is
  running. The recorded `maxConcurrentLoops` is always 1.
- The loop stops when every slot is settled and no continuous motion with real amplitude remains, when the page
  is hidden (`visibilitychange`), or when the element is off screen (`IntersectionObserver`). It restarts on the
  next commit.
- Battery saver (`navigator.getBattery`, when available, not charging and below 20 %): capped at 30 fps and half
  energy.
- Rapid state changes are coalesced: at most one scene commit every 220 ms, and the latest state wins. A level
  change alone never re-plans the scene; it only updates `engine.level`.
- The DOM is a fixed pool of 14 `<path>` elements per presence, never re-created. Paths that are hidden get the
  `pc-off` class.

## Reduced motion

When `prefers-reduced-motion: reduce` is set (with the preference on "Follow system") or the preference is
"Reduced", scenes are applied instantly, no frame loop runs, and the CSS breathing is disabled. Every state still
has its own static geometry (tested: listening, responding and idle give three distinct path sets with zero frames).
