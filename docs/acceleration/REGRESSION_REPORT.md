# Compatibility and regression report

Status: 2026-11-23, branch `nvidia-acceleration-review` against `9e4570a` (0.7.0). This page says, capability by
capability, how each was checked after the changes and what the check covers. "Verified" means a test or a
measurement ran and passed on this branch; "unverified" means the environment could not check it, and is never
counted as passed.

## 1. Suites

| Layer | Command | Before | After |
|---|---|---|---|
| Lint | `ruff check .` | clean | clean |
| Python tests | `pytest -q --ignore=tests/e2e` | 3337 passed, 18 skipped | **GATE_PYTEST** |
| Node checks | `tests/js/*.mjs` (6 files) | all pass | all pass |
| Numerical reproduction | `scripts/reproduce.py` | 8 of 8 | 8 of 8 |
| The gate as a whole | `bash scripts/check.sh` | passed in 468 s | **GATE_ALL** |
| Acceptance scenarios (simulated) | `nsf acceptance --all` | not rerun at baseline (0.7.0 recorded 7 of 7) | exit 0 |
| Browser end to end | `bash scripts/e2e.sh` | not run at baseline; the one test that failed here was run on `9e4570a` (passes) | **GATE_E2E** |
| Offline research benchmark | `nsf web bench` | deterministic, safety invariants hold | every metric identical to before, at read-ahead 1 and 4; safety invariants hold |

The 18 skips are the same on both sides: tests marked live (the real internet, a real remote provider) and
platform-conditional ones.

**Tests changed, not only added.** One: `tests/e2e/test_presence.py::test_voice_events_listening_level_interrupt_and_mute`
failed on this branch (3 of 3 runs) and passed on the baseline (3 of 3). Bisected to the worker wake-up. The test
submitted a research task and then expected the entity to show "responding" while speaking; that held only while
the task sat unclaimed for the old 5 s poll. Claimed at once, the task ends with a review question, which the
documented state mapping ranks above "responding". The research step was moved to the end of the test and every
assertion kept. The page behaves as the mapping says in both orders, and in real voice use an answer is spoken
after its task has finished on either version. No other existing test was modified; the read-ahead lane's three
pins on existing orchestrator tests were removed again when its default was set to 1.

## 2. Capability matrix

| Capability | Touched by this branch | How it was checked | Result |
|---|---|---|---|
| Executive decisions, beliefs, change detection, scoped relearning | No | `tests/instance` (unchanged tests) | Verified |
| Graph learning, evaluation, promotion gate, rollback, recall | `lesion` only (closed form) | `tests/instance`; equality with the old computation to 1e-15; reproduction 8 of 8 | Verified |
| Evidence journal, lifetimes, promotion | No | `tests/instance` | Verified |
| Checkpoints and restore | Yes: a restore replays more forgets | `tests/instance/test_checkpoints.py` including the new cases; archives byte-exact (`tests` for archives, reproduction) | Verified |
| Governed forgetting | Yes: three cascades widened | `tests/instance/test_erasure.py`, `test_governance.py`; new tests failed on the old code first | Verified; stricter than before |
| Schema and migrations | Locking only; **no schema change**, no migration added | `tests/instance/test_migrations.py` with a two-connection interleaving | Verified |
| Saved instance data | Not rewritten. An existing ledger gains three columns and an index on open; older code ignores them | Test with a 0.7.0-shaped ledger file | Verified |
| Intent, discovery, safe fetch, extraction | Reading stage and fetcher (read-ahead, default off) | Offline benchmark identical; `tests/webcortex`; the three interruption tests unchanged | Verified at the default; verified equivalent at 4 on the benchmark |
| SSRF, robots.txt, limits, backoff, budgets, injection containment | Fetcher | Benchmark safety summary: all hold | Verified offline |
| Claims, verification states, word sense, synthesis | No | `tests/webcortex`; benchmark identical | Verified |
| Model-written answers and the guard | The guard: no. `apply`: two arguments and the abstention label | `tests/inference/test_inference_reasoning.py` (all prior tests unchanged and passing); pipeline benchmark: 46 model answers before and after, critique removed 0 before and after | Verified |
| Guard survival and attack tests (eight review rounds) | No | The same tests | Verified |
| Self-critique | Default unchanged; optional skip for spoken answers | Tests for both settings | Verified |
| Memory retrieval | Default (lexical) path restructured, same result; optional semantic path | Offline benchmark memory metrics identical; `accel_after_memory_lexical.json` equals the baseline class by class; new tests | Verified |
| Memory answers without the web | Yes: stricter when a model is configured | Existing test of the memory answer passes unchanged (no model: identical events); new tests | Verified; a behaviour change by design (§3) |
| Knowledge evolution, hypotheses, predictions, consolidation | No | `tests/webcortex` | Verified |
| Autonomy, budgets, curiosity | Queue order only | `tests/webcortex/test_autonomy.py`, scheduler tests | Verified |
| Worker, leases, crash resume, cancel | Wake-up and claim order | Scheduler tests; benchmark checks `cancel_queued`, `cancel_running`, `crash_resume` hold | Verified |
| Inference gateway: health states, streaming, retries, timeouts, structured output, tools, local-first guard | Additive | `tests/inference` (prior tests unchanged) | Verified |
| Supported model and provider | Unchanged: the same model, precision and image digest | `accel_after_inference.json`: reasoning 15 of 16, tools 8 of 8, as before | Verified live |
| Resource pressure level and thresholds | Not changed (reporting added) | `tests/test_resources.py` | Verified |
| Audio priority, background model slots | Pocket now counted | Tests | Verified; stricter |
| Idle unload and reload | Reload bounded; memory refusal | `tests/test_resources_idle.py`, `tests/inference/test_inference_idle.py` | Verified against the fake host. **Unverified live**: idle unload is not installed on this host, as before |
| Voice: ASR, TTS, bridge, modes, barge-in, transcripts, commands | Hub ordering, chunk repeat, follow interval | `tests/voice`; Node check of the voice client; browser suite | Verified with synthetic audio. **Unverified**: real microphone and speaker (as in 0.7.0) |
| Pocket: pairing, families, HTTPS, signed replies, push, nodes | Audio beacon; the voice client file (offline cache version 26 → 27) | Pocket tests over a real TLS handshake; `tests/js/offline_check.mjs`; browser suite | Verified. **Unverified**: a physical handset (as in 0.7.0) |
| Console: API, token, Host and Origin checks | `/api/resources` fields added | `tests/console`; browser suite | Verified |
| Presence entity: states, motion, light and dark, accessibility (axe) | No product change | Browser suite (`tests/e2e/test_presence.py` 18 of 18; the full suite) | Verified |
| CLI contracts | Added: `nsf web memory`, switches on `nsf web config` and `nsf llm config`. Removed: none | CLI tests; `nsf setup` tests | Verified |
| Deploy scripts | vLLM flags; `wait`; new `deploy/memory` | `bash -n`; the vLLM container restarted through `run.sh` twelve times in this review and returned to healthy each time; the memory containers started and served | Verified on this host. `deploy/install.sh` and the systemd units: **unverified** (not installed here, as in 0.7.0) |
| Services not started by this review | SearXNG, ASR, TTS containers | Left running untouched; health checks green before and after | Verified |

## 3. Behaviour that changed on purpose

Everything else on this page is "the same result as before". These are not:

1. **Memory may decline to answer** a question it used to answer without the web, when an answer model is
   configured and does not confirm the statements. The question goes to web research instead. Off:
   `nsf web config --no-memory-check`.
2. **A forgotten epoch, its note, a forgotten dataset's note and effects no longer return** from a restored
   checkpoint. There is no switch: the old behaviour contradicted the forgetting promise.
3. **Queue order**: the owner's task is claimed before older autonomous tasks.
4. **A reply that arrives late is shown and not spoken** when a newer turn or an interruption has happened since.
5. **Background model calls carry a priority** when `background_priority` is set, and the shipped vLLM flags
   schedule by priority.
6. **A model's abstention has its own reason text** and `abstained: true`.
7. **An idle reload can refuse** for lack of free memory, and gives up the caller's wait at the caller's deadline.

Additive contract changes (new optional fields, no removed or renamed field): `retrieval` in the knowledge query
result; `memory_check` in the needs-web decision event; `abstained` in `reasoning`; new readings in
`GET /api/resources`; `provider`, `priority`, `cached_tokens` in ledger rows; `prefetch_unused_*` in task usage
(absent while zero).

## 4. Edge and recovery scenarios

| Scenario | Checked by | Result |
|---|---|---|
| A 24k-token context | `bench_inference.py` context scenario | Recall 3 of 3 before and after |
| Eight concurrent model calls; the owner beside four background calls | `bench_inference.py` | No error; no preemption |
| Abrupt process end mid-task, then resume | Benchmark `crash_resume`; read-ahead crash test | Holds |
| Cancel, web switched off, a domain denied mid-run | Orchestrator tests (unchanged) at the default; read-ahead versions at 4 | Hold at the default. At 4: the documented exposure (requests already sent) |
| A memory model down, slow or answering garbage | `test_semantic_memory.py`, `test_inference_policy.py` | Lexical ranking with the reason; a malformed answer is recorded as `bad_response` |
| The answer model down during the memory check | `test_memory_gate.py` | The lexical decision stands; the event says the check was skipped |
| A model server that crash-loops | `run.sh wait` test with a restarting container | Fails at once instead of waiting out the limit |
| Too little memory for a reload | Idle tests | Refused and recorded |
| Two processes upgrading one file | Migration test | One migrates, the other waits and sees the new version |
| A corrupted or stale checkpoint | Existing checkpoint tests | Unchanged |
| An endpoint off this host for memory text | `test_inference_policy.py` | Refused without both authorisations |
| A lost audio chunk; an out-of-order reply | Voice tests | The utterance survives one repeat; the stale reply is not spoken |
| GPU memory exhaustion; a failed image upgrade and rollback; days of wall-clock autonomy | — | **Unverified** in this review (the first two were not provoked on a shared host; the third is the owner's running soak, left untouched) |
