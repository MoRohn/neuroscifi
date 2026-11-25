# Cognitive state mapping

The source of truth is `src/neuroscifi/presence/aggregator.py`. Unit tests in `tests/presence/test_aggregator.py`
check every row of these tables.

## Research tasks (`web_tasks` + the newest `web_events` row)

| Task row | Newest event | Activity | Composition | Secondary |
|---|---|---|---|---|
| `queued` (no live lease, ≥30 s, no worker) | — | waiting ("no research worker has claimed it yet") | waiting | web_research |
| `queued` | — | waiting | waiting | web_research |
| running, lease expired | — | recovering (+condition `recovering`) | recovering | web_research |
| `cancel_requested=1` | — | waiting ("stopping at the next safe point") | waiting | — |
| `discovering` | any | searching | researching (telescope, scanning) | web_research |
| `reading` | any | reading | reading (book) | web_research |
| `evaluating` | no `contradicts` link | verifying | Tier B *evaluating evidence* (magnifier) | evidence_verification |
| `evaluating` | ≥1 `contradicts` link for the task | comparing | Tier B *comparing* (balance) | evidence_verification |
| `synthesizing` | `stage` | generating | responding (bubble; dots follow intensity) | — |
| `synthesizing` | `extract` with `from_memory` | generating | responding | memory_retrieval |
| `synthesizing` | `synthesized`, model configured with reasoning on | reasoning (confidence 0.8) | thinking (orbit) | — |
| `synthesizing` | `synthesized`, no model | consolidating (0.8) | construction (cubes converge) | knowledge_consolidation |
| `synthesizing` | `decision` with `reasoning`, or `consolidated` | consolidating (0.9) | construction | knowledge_consolidation |
| finished `completed`/`partial` ≤ 8 s ago | — | responding, concept *achievement* | achievement (mountain, flag) | — |
| finished `failed` ≤ 8 s ago | — | waiting, concept *error* (+condition `error` for 60 s) | error ("!" mark) | — |
| proposal (`stage='proposed'`) | — | counted through its pending question | — | — |

The step comes from the stage index (`stage k of 4`). `progress` is always `null` because the backend has no
measured fraction of work done, and the interface never shows a percentage.

Priority: tasks a person asked for are **foreground**: `webcortex.orchestrator.user_initiated(origin)`, the one list
of user origins (`console`, `cli`, `voice`, `pocket`, `pocket-voice`, `owner`, `mcp`, and `refresh-owner:*`); the
voice bridge reads the same list. Everything else (`curiosity`, `refresh:*`, `monitor:*`, `learner`, `bench`) is
**background**, which also adds the interaction `autonomous_research`. A background
primary is shown *subtly*: the calm exploring entity, an orbiting dot and half the motion energy.

## Other sources

| Source | Condition | Activity / interaction |
|---|---|---|
| `pending` unresolved, created ≤ 15 min ago | `awaiting_permission` | waiting, concept *asking*, priority **user_critical**, `tool_awaiting_confirmation` |
| `pending` unresolved, older | `awaiting_permission` | none (a small "?" overlay) |
| monitor `finding` event ≤ 8 s ago | — | learning, concept *discovery*, background |
| `presence.activity` entries (in-process model calls) | — | generating, priority tool, with streamed token count |
| token ledger: calls that ended in the last 30 s | — | feeds intensity (`tokens_30s`) |
| write regulation paused | `resource_constrained` | — |
| measured memory/GPU pressure `elevated` or `critical` (`resources.monitor`, RD-46; `sources.resources`) | `resource_constrained` | — |
| any provider circuit `open` (web enabled) | `network_degraded` (`sources.search = "degraded"`) | — |
| owner voice session active in a voice mode whose speech engines (ASR; plus TTS in `voice_text`) were last measured not `ready` by the hub (`voice.state.engines`, ≤ 60 s old) | `network_degraded` (`sources.voice = "engines_unavailable"`, `sources.voice_engines_down = "asr,tts"`); the text names the engines | — |
| ≥2 running tasks, or running plus queued | `busy` | — |
| nothing running for 10 min (tasks, ledger, questions) | — | resting (else idle) |
| the owner's quiet switch on (instance setting `quiet`, ADR-050; both scopes; `sources.background = "quiet"`) | — | nothing running: resting at once, detail "Quiet: nothing runs unless you ask"; the client's word is "Quiet" while idle or resting and its description says the sentence once whatever is shown. No new activity or condition |

## Voice (Lane V snapshot, owner scope only)

| Snapshot | Result |
|---|---|
| module absent (`ImportError`) | `voice: null`, `sources.voice = "absent"`; no voice dimension is claimed |
| `mode = text_only` | interaction `text_only`; no audio candidates |
| `mic_capturing` | listening (communication); `user_speaking` when reported |
| `speaking` and not `output_muted`, mode `voice_text` | responding (communication), interaction `responding` |
| `output_muted` | interaction `voice_muted`; speech is never shown |
| `interrupted_at` ≤ 3 s ago | interaction `user_interruption` |

In the browser, `nsf:voice` events (which carry a measured level) replace the backend voice candidates. See
`VOICE_INTEGRATION.md`.

## Arbitration

Candidates are sorted by rank, then by recency (`since`). The first one is primary. Up to two others become
accents: an orbiting dot for background work, or a small shape for a condition that is not primary. Conditions are
always shape marks, never colour alone: "?" for awaiting permission, "!" for error, a broken ring for degraded,
dashes for disconnected. When the client cannot reach the server, the concept is *disconnected* and the text says
"activity unknown". Local signals can only lower a claim: typing adds `user_typing`, and recent interaction in the
page turns *resting* into *idle*.

## Intensity

`raw = 0.12·running + events_30s/12 + tokens_30s/600 + 0.5·inflight_calls + 0.05·queued`, then
`intensity = round(1 − e^(−raw), 1)`. The client smooths it (EMA 0.5 per update) and maps it to motion energy
`(0.35 + 0.65·intensity) × preference × (0.5 when subtle) × (0.5 on battery saver)`.

## Family scope (Pocket)

`derive(..., family=digest)` adds `AND family=?` to every task query and filters the ledger to the family's task
ids. It excludes `pending`, the voice snapshot and the in-process registry. Global conditions such as write
regulation still apply. `tests/presence/test_routes.py::test_pocket_presence_is_family_scoped` and
`tests/e2e/test_presence.py::test_pocket_presence_shows_own_family_task` check that nothing of another family
appears.
