# Prototype audit

Input: `NeuroSciFi_Prototype.zip`, 94 entries. It was extracted with path-traversal and symlink checks (none found). The untouched archive stays at the repository root and is imported unchanged as commit `e9fd953`.

Baseline on the build host (aarch64, Python 3.12.16, Node 26): 20 Python tests OK; `sensor_check`, `field_check`, `offline_check` and `field_benchmark` all pass; `reproduce.py` verifies 8 of 8.

## 1. Component inventory and classification

| Prototype path | What it is | Classification | Destination |
|---|---|---|---|
| src/core.py, sensory.py, recalibrate.py, source_groups.py, graph_bench.py, human_counts.py, graph_compression_bench.py, context_recall.py, compression_bench.py, profile_device.py | Deterministic experiments behind the brief's numbers | **Reuse directly** (import lines only) | `neuroscifi/lab/` |
| src/graph_store.py | NSG1 codec | **Reuse directly** | `neuroscifi/graph_store.py` |
| src/archive.py, vendor_tufazip/ | NSFA archive with the pinned coder | **Reuse directly** (vendor bytes identical) | `neuroscifi/archive.py` |
| src/live.py | Persistent 8-state learner, one epoch at a time | **Refactor and extend**. Kept as a parity oracle; generalised in `instance/beliefs.py` | `neuroscifi/legacy_live.py`, `instance/*` |
| graph_bench learner loop | Evolving sparse learner inside an experiment | **Reimplement while preserving intent**. Extracted as `instance/learner.fit` with a parity test | `instance/learner.py` |
| context_recall.GraphRecall | History-based selection | **Reuse plus extend** (unknown-context option) | `instance/recall.py` |
| mobile/bridge.py | Pocket HTTP bridge | **Refactor and extend** (mute and unmute, manifest) | `neuroscifi/pocket/server.py` |
| mobile/web/* | PWA: index.html, style.css, mobile.js, field-ui.mjs, field-model.mjs, sw.js | **Reuse directly**, extended with an evening review, manifest and a11y fixes | `neuroscifi/pocket/web/` |
| mobile/*.mjs | Node checks of the shipped JavaScript | **Reuse directly** | `tests/js/` |
| mobile/browser_check.py | Playwright check, never run (Chromium blocked on the authors' host) | **Reimplement** as a pytest e2e suite | `tests/e2e/` |
| mobile/native/android, ios | Collector sources, never built | **Keep as is**. Build blocked on this host | `mobile/native/` |
| tests/*.py | 20 unittest tests | **Reuse directly** (import mechanics only) | `tests/` |
| reproduce.py | Evidence verifier | **Reuse** (runs `-m` modules) | `scripts/reproduce.py` |
| evidence/, figures/, research/ | Published outputs, data scripts, CPU runs | **Keep as is** | same |
| Architecture.md | Editable twin of the brief | **Keep as is** | root |

## 2. Screens, routes and navigation (Pocket)

One page, `/`, with anchor navigation: Field session (`#workspace`), Sense (`#sensor`), Memory (`#localmem`), Spark (`#spark`). The "Mark an activity" section gains `#mark` in Wave 2 so the manifest shortcut can target it. Server routes are `GET /`, `/style.css`, `/mobile.js`, `/field-ui.mjs`, `/field-model.mjs`, `/sw.js`, and `POST /api/{pair,save,forget,state,unpair,episode,episodes,forget_episode,link}`.

## 3. Visual language (preserve)

- **Palette.** Ink `#183338`, page `#f4f7f5`, rule `#c6d2cb`, sensor panel `#e7f1ea` with border `#abc8ba`, primary `#184e42`, accent `#166147`, live dot `#bd4d28`, focus `#0876a5`.
- **Typography.** Body "DM Sans", system-ui; h1 Georgia 400 at clamp(3rem, 11vw, 4.6rem).
- **Shape.** 7px radius on buttons, 14px on the sensor panel. Touch targets of at least 44px.
- **Layout.** A single 620px column. At 800px and wider, a two-column grid (1.15fr / 1fr) at a maximum width of 1140px.
- **Status.** `role=status` and `aria-live` regions for drafts, forecasts and messages.

The console reuses these tokens (ADR-013).

## 4. State management

- `mobile.js`: module-level `key` (pairing credential, tab memory), `draft`, `selected` stream, `generation` counter for stale results.
- `field-ui.mjs`: in-memory rows, an optional vault (PBKDF2 → AES-GCM), the pending prediction, and an epoch counter to invalidate work after a lock.
- No framework and no global store. Visibility and pagehide handlers enforce the private-moment rules.

## 5. Findings: dead controls, mocks and placeholders

| Finding | Severity | Disposition |
|---|---|---|
| No mock data or fake services. All UI controls call real handlers or the bridge. | — | Confirmed by the handler inventory (mobile.js 20 bindings, field-ui.mjs 16 bindings). Full control-by-control table from W2-D appended below. |
| Evening review controls ("forget this session", "stop learning this pattern", "last sensor active") are absent. | P1 (explicit requirement p.21) | W2-D implements them |
| Home-screen shortcuts absent. | P2 | W2-D adds a manifest |
| `browser_check.py` was never executed. | P1 (verification gap) | W2-D pytest e2e |
| `live.py` orders records by lexical id, so insertion order is lost. | P3 | The new journal uses `seq`. Legacy kept only as an oracle |
| `live.py --db` default depends on which files exist in the CWD. | P3 | `NSF_DB` env or `--db` in the new CLI |
| Native collectors never built. | MB | Documented as blocked |
| No integrated individual: learner, graphs, recall, archive and governance are separate scripts. | P1 (core of the brief) | `neuroscifi.instance` (Wave 2) |

## 6. Prototype → requirement map

The `Implementation` column of `REQUIREMENTS_MATRIX.md` names the prototype file behind each reused capability (POC-*, ARC-*, RES-*). New code is listed under INS, MEM and GRA.

## 7. Pocket control audit (W2-D)

_Appended when W2-D reports._
