# Presence Cortex — architecture

The Presence Cortex is a small vector entity in the console and Pocket headers that shows what the individual is
really doing. It is not a mascot, spinner or avatar. Every visible activity comes from persisted or in-process
state. When nothing is running it shows idle or resting, and when the server cannot be reached it says that the
activity is unknown.

## Pipeline (no new services)

```
subsystems ──► Cognitive State Aggregator ──► channel ──► Semantic Interpreter ──► Visual Metaphor Planner
(web cortex,    presence/aggregator.py         (one per     web/interpreter.mjs       web/planner.mjs
 executive,     derive(): read-only SQL +       page)       arbitration, a11y text    Tier A / B / C + validator
 inference,     ledger + registries                                                    │
 voice, ...)                                                                           ▼
                        console header / Pocket header ◄── Presence Renderer ◄── Physics & Motion Engine
                        presence.mjs (UI, settings)        web/renderer.mjs       web/motion.mjs
```

| Module | File | Contract |
|---|---|---|
| State aggregator (backend) | `src/neuroscifi/presence/aggregator.py` | `derive(db, db_path, now, family=None, worker_alive=None)` → state `nsf.presence.state/1` |
| In-flight registry | `presence/activity.py` | `track(activity, subsystem, purpose)` context; `snapshot()` |
| Endpoints | `presence/routes.py` | console long-poll, Pocket conditional poll, Tier C proposal |
| Tier C schema + validator | `presence/scene_spec.py` | `SCHEMA` (`nsf.presence.scene/1`), `validate_spec`, `propose` |
| Contracts | `web/contracts.mjs` | vocabularies, slots, sizes, budget, preferences |
| State adapter | `web/channel.mjs` | `ConsoleChannel`, `PocketChannel` (one request at a time, paused when hidden) |
| Interpreter | `web/interpreter.mjs` | `interpret(server, local, now)` → intent (activity, concept, priority, text) |
| Scene planner | `web/planner.mjs` | `plan(intent, {size, spec})` → resampled scene, validated |
| Geometry engine | `web/geometry.mjs` | primitives, `resample`, `align`, `toPath` |
| Canonical compositions | `web/canonical.mjs` | Tier A (18 reference compositions + 5 system compositions), overlays |
| Composer | `web/composer.mjs` | Tier B templates → spec → slots; `specToComposition` shared with Tier C |
| Validator | `web/validator.mjs` | `validateSpec`, `validateScene`, palette contrast |
| Motion engine | `web/motion.mjs` | `MotionEngine.setScene`, `step(dt, t)` |
| Renderer | `web/renderer.mjs` | `Renderer.draw(engine)`, `Player` (rAF loop, stats) |
| Theme adapter | `web/theme.mjs`, `theme-boot.js`, `theme.css` | one app theme for both apps |
| Accessibility adapter | `web/a11y.mjs` | `Announcer` (polite/assertive, rate-limited), reduced-motion decision |
| Controller | `web/presence.mjs` | `Presence(host, {channel})`, `expose()` read-only stats for tests |

## Typed contracts

- **Activity / priority** (`aggregator.py`, `contracts.mjs`): one primary activity from 20 values. A candidate also
  has a priority class (`user_critical > communication > foreground > tool > background > idle`), its subsystem,
  `task_id`, `category` (research mode), `since`, `step` (stage *k* of 4, only when the task row says so),
  `progress: null` (never computed), `secondary[]`, `confidence` (1.0 when read directly, 0.8–0.9 when a sub-phase
  is inferred from the newest event) and `detail`.
- **State** `nsf.presence.state/1`: `primary`, `activities[≤8]` sorted by arbitration, `condition[]`,
  `interaction[]`, `intensity` (0–1, rounded to 0.1, from real signals), `voice` (Lane V snapshot or `null`),
  `sources`, `version` (digest of what the client renders), `ts`, `signals` (raw counters).
- **Concept**: a Tier A name (`present`, `thinking`, …), a system composition (`asking`, `error`, `waiting`,
  `recovering`, `disconnected`) or free words for Tier B (`evaluating evidence`, …).
- **Scene geometry**: 14 fixed slots (`ground, body, eyeL, eyeR, m0–m5, a0–a3`). Each slot has a fixed point count
  (body 72, ground 24, others 28), a tone from the palette, a width (bold/fine), an optional fill or dash, and
  animation descriptors.
- **Animation parameters**: `{k: swing|spin|orbit|shift|bob|twinkle|level|dots|converge, amp, period, phase,
  ox, oy, decay, drive}`, bounded by `BUDGET`.
- **Theme tokens**: `TOKENS.light/dark` in `theme.mjs`, mirrored as CSS custom properties in `theme.css`.
- **User preferences**: `nsf.theme` (system | light | dark) and `nsf.presence.prefs` (`motion`: system | reduced
  | full; `intensity`: low | normal | high; `modelDoodles`: false by default), both in localStorage.
- **Render lifecycle**: `commit` (at most one every 220 ms) → `plan` → `engine.setScene` → `Player.start`. The
  life layer (MOTION_ENGINE.md) keeps the entity moving while it is on screen, at 30 fps once the physics has
  settled; the loop pauses while the page is hidden or the element is off screen, and never runs under reduced
  motion. Environment inputs (pointer, scrolling, typing, window focus, the measured audio level, stage
  transitions) feed the life layer directly and never re-plan the scene.

## Channel

- **Console** (loopback, per-start token, Host/Origin checks unchanged): `GET /api/presence?since=<v>&wait=20` is a
  long-poll. The server re-derives every 0.35 s and answers when the version changes, or with
  `{"unchanged": true}` after `wait` seconds. One request is open at a time, and none while the page is hidden.
  Measured: at most 2 completed requests in 10 idle seconds (`PERFORMANCE_RESULTS.md`).
- **Pocket** (pairing token, 512-byte bodies, 10 s request deadline, so no long-poll):
  `POST /api/presence {"since": v}`, sent every 2 s while something runs and every 10 s when idle, and never while
  the page is hidden. The family scope only sees the family's own tasks (see `COGNITIVE_STATE_MAPPING.md`).
- **Tier C**: `POST /api/presence/scene {"concept": "..."}` (console only). The server calls the model through
  `neuroscifi.inference` and validates the result.
- Static modules are served under `/presence/` by both servers with their own CSP. The console also serves the
  gallery (`/presence/gallery`, which renders nothing until its token is accepted). Pocket's service-worker shell
  caches the presence modules. The presence work bumped it to `neuroscifi-pocket-shell-12`; Lane V later bumped it to 13 when it added the voice files.

## What is read, and what is not

The aggregator reads `web_tasks`, the newest `web_events` row per running task, `web_claim_links` (contradictions),
`pending`, `web_strategies` (circuit breakers), write regulation, the token ledger and the in-process registry. It
never reads raw CPU or GPU load as "effort". Regulation and provider health are cached for 10 s.

**Known gap: in-flight token counts for research synthesis.** The ledger records a call only when it ends, and
the gateway (`inference/gateway.py`, owned by another lane) has no in-flight hook. Calls made by the presence
itself (Tier C) are tracked live through `presence.activity.track()`. A research task in its synthesis stage is
shown as *reasoning* once its `synthesized` event exists and a model is configured, and the tokens of finished
calls feed intensity. A two-line hook in `Gateway.chat` (`with activity.track(...) as t:` plus `t()` per delta)
would make the token counts live. It was not added because of lane ownership.
