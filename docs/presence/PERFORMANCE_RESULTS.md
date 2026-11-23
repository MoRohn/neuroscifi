# Performance results

The measurements were taken on 2026-11-23 by `tests/e2e/test_presence_perf.py`, with the life layer (MOTION_ENGINE.md) on. Raw numbers are in
`perf/measurements.json` and are overwritten on every run.

**Setup**

- Machine: DGX Spark GB10, aarch64, Linux 7.3.
- Browser: Chromium 153.0.8010.36 (`/usr/bin/chromium`, headless, via Playwright).
- CPU throttling: Chromium's DevTools CPU throttling (`Emulation.setCPUThrottlingRate`), at 1× and 4×.
- Viewports: desktop 1280×900 and phone 390×844.

**What runs**

- **Subject**: the gallery's live harness. It is the production `Presence` controller, interpreter, planner,
  motion engine and renderer, drawn 180 px wide (expanded size). The page also holds the static figures of every
  composition in both themes, which are why there are about 2,780 DOM nodes.
- **Continuous motion**: 5 s of the *thinking* composition (orbit swing plus accents) at intensity 0.6.
- **200 transitions**: one synthetic state every 30 ms, cycling through all 20 activities.

**What is measured**

- *Frame pacing*: requestAnimationFrame timestamps.
- *Renderer script*: `performance.now()` around the engine step and the SVG update, per frame.
- *DevTools metrics*: per-frame `ScriptDuration`, `LayoutDuration` and `RecalcStyleDuration` from
  `Performance.getMetrics`. They cover the whole page, including the harness's 1 s stats printer.
- *Heap*: `JSHeapUsedSize` after `HeapProfiler.collectGarbage`.

## Continuous motion (5 s)

| Viewport | CPU | Frames | rAF interval p50 / p95 (ms) | Renderer script per frame p50 / p95 (ms) | DevTools script / layout / style per frame (ms) |
|---|---|---|---|---|---|
| desktop 1280 | 1× | 305 | 16.7 / 16.8 | 1.3 / 1.6 | 1.37 / 0.61 / 0.41 |
| desktop 1280 | 4× | 304 | 16.7 / 16.7 | 0.2 / 0.8 | 0.33 / 0.30 / 0.20 |
| phone 390 | 1× | 303 | 16.7 / 16.8 | 1.6 / 2.0 | 1.56 / 0.69 / 0.47 |
| phone 390 | 4× | 305 | 16.7 / 16.8 | 0.2 / 0.8 | 0.31 / 0.27 / 0.14 |

The animation held 60 fps (p95 16.8 ms) even with 4× CPU throttling. Script cost per frame stays at or below 2 ms at
p95. The 1× rows run first on a fresh page, so they include the JIT warming up for the life layer; the 4× rows,
measured next on the same page, are lower although the CPU is slower. Each configuration ran once.

## 200 transitions

| Viewport | CPU | Wall (s) | Scene commits | Heap before → after GC (KB) | DOM nodes | Listeners | Loops | Paths | Peak point speed |
|---|---|---|---|---|---|---|---|---|---|
| desktop 1280 | 1× | 7.15 | 33 | 3015 → 3110 (+95) | 2776 → 2776 | 75 → 75 | 1 | 14 | 260 |
| desktop 1280 | 4× | 7.07 | 65 | 3130 → 3142 (+12) | 2776 → 2776 | 75 → 75 | 1 | 14 | 260 |
| phone 390 | 1× | 6.46 | 31 | 3014 → 3067 (+53) | 2776 → 2776 | 75 → 75 | 1 | 14 | 260 |
| phone 390 | 4× | 6.95 | 63 | 3088 → 3180 (+92) | 2776 → 2776 | 75 → 75 | 1 | 14 | 260 |

How to read the table:

- **Coalescing**: 200 incoming states produced 31–33 scene commits at 1× and 63–65 at 4×. The commit gap is
  220 ms, and under throttling the timers slip, so more gaps elapse during the same wall time.
- **No accumulation**: DOM nodes, listeners, path count and loop count are unchanged after the run.
- **Listeners**: 75, six more than before the life layer (pointer move and down, pointer out, scroll, focus, blur),
  registered once per presence and unchanged by the run.
- **Heap**: the deltas (12–95 KB) are single measurements. A delta of this size, which does not repeat across
  runs, is within V8 GC noise. No trend was established, because each configuration ran only once.
- **Peak point speed**: 260 units/s is exactly the engine's velocity cap. Fast transitions hit the limit rather
  than exceeding it.

## Console element at rest

On a fresh instance the state is *resting*. Its "z" marks decay 55.6 s after load and the physics settles. The
entity stays alive (the life layer), so over the next 10 s the element drew **300 frames**, all at the 30 fps
ambient cap, at **1.2 ms** of renderer script per frame (p95), and completed **1** presence request: the long-poll
answered "unchanged" after its 20 s window, and no other polling happens. Before the life layer this element drew
0 frames at rest; the owner asked for an entity that is never frozen, and 30 fps at about 1 ms is the cost.

## Other measured behaviour

- In the full browser suite (`tests/e2e`: 127 passed, 1 skipped, 511.8 s), 300 rapid voice events produced no
  growth in listeners or nodes (`test_rapid_transitions_do_not_accumulate`).
- Off-screen pause works. In the first measurement attempt the harness sat below the fold, and the loop correctly
  drew 0 frames until it was scrolled into view. The test now scrolls it into view first.

## Not measured

- Real phones or real mobile GPUs. Throttling the desktop CPU is not a phone.
- Battery-saver mode: `navigator.getBattery` reported a charging battery or no battery.
- Long-duration (hours) memory behaviour.
