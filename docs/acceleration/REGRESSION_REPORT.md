# Compatibility and regression report

Status: 2026-11-23, branch `nvidia-acceleration-review` against `9e4570a` (0.7.0). This page says, capability by
capability, how each was checked after the changes and what the check covers. "Verified" means a test or a
measurement ran and passed on this branch; "unverified" means the environment could not check it, and is never
counted as passed.

The branch has two phases: the review itself, and the six decisions the owner then delegated
([ENGINEERING_PLAN.md](ENGINEERING_PLAN.md) §4). Phase 2 changed more of what the system does than phase 1,
including four defaults, so §2 to §4 say for each row which phase touched it.

## 1. Suites

| Layer | Command | Before | After phase 1 | After phase 2 (final code) |
|---|---|---|---|---|
| Lint | `ruff check .` | clean | clean | clean |
| Python tests | `pytest -q --ignore=tests/e2e` | 3337 passed, 18 skipped | 3483 passed, 18 skipped (146 tests added) | 3996 passed, 18 skipped (513 more than after phase 1, 659 more than the baseline) |
| Node checks | `tests/js/*.mjs` (6 files) | all pass | all pass | all pass |
| Numerical reproduction | `scripts/reproduce.py` | 8 of 8 | 8 of 8 | 8 of 8 |
| The gate as a whole | `bash scripts/check.sh` | passed in 468 s | passed in 497 s | passed in 659 s |
| Acceptance scenarios (simulated) | `nsf acceptance --all` | not rerun at baseline (0.7.0 recorded 7 of 7) | exit 0 | exit 0, every scenario passes |
| Browser end to end | `bash scripts/e2e.sh` | not run at baseline; the one test that failed here was run on `9e4570a` (passes) | 194 passed, 1 skipped, 1 failed on the final tree: `test_add_a_phone_from_the_console_to_a_paired_phone` saw Chromium's `net::ERR_NETWORK_CHANGED` when another tenant's container brought up a network interface on this shared host (system log, same second). Its file was rerun three times on the same tree: 3 of 3 pass each time. The full run before the last review fixes: 195 passed, 1 skipped. A second full run on the same tree afterwards: 195 passed, 1 skipped, exit 0 | 198 passed, 1 skipped, exit 0 (the interim run on the tree before the last read-ahead fix gave the same) |
| Offline research benchmark | `nsf web bench` | deterministic, safety invariants hold | every metric identical to before, at read-ahead 1 and 4; safety invariants hold | exit 0; every compared metric identical at read-ahead 1, at 4 and at the default (`accel_decisions_read_ahead.json`); safety invariants hold |

The phase-2 column is one run on commit `5583823`, the last commit that changes product code; the commits after it
change these reports, one sentence of a web-reading page, evidence files, and the clean-up at the end of two
Web Cortex tests (last row of the table below; `tests/webcortex` again afterwards: 1807 passed, 9 skipped). The 18 skips are the same on every side: tests marked live (the real internet, a real remote provider) and
platform-conditional ones.

**Tests changed, not only added.** One: `tests/e2e/test_presence.py::test_voice_events_listening_level_interrupt_and_mute`
failed on this branch (3 of 3 runs) and passed on the baseline (3 of 3). Bisected to the worker wake-up. The test
submitted a research task and then expected the entity to show "responding" while speaking; that held only while
the task sat unclaimed for the old 5 s poll. Claimed at once, the task ends with a review question, which the
documented state mapping ranks above "responding". The research step was moved to the end of the test and every
assertion kept. The page behaves as the mapping says in both orders, and in real voice use an answer is spoken
after its task has finished on either version. No other existing test was modified in phase 1; the read-ahead
lane's three pins on existing orchestrator tests were removed again when its default was set to 1 (phase 2 pinned
them again, below).

**Tests changed in phase 2.** Every one is listed; none lost an assertion without the old behaviour being kept
under its switch or in a twin test.

| Test | Change | Why |
|---|---|---|
| `tests/webcortex/test_orchestrator.py`: `test_cancel_from_another_connection_stops_before_the_next_fetch`, `test_turning_web_access_off_mid_run_stops_the_task_partial`, `test_a_domain_denied_mid_run_is_never_fetched` | One setup line each sets `read_ahead: 1`; assertions untouched | They assert that one page was ever requested when the task is stopped after its first page. With four pages fetched at once, the next pages were requested before the stop existed. What holds at 4 (nothing after the stop) is asserted by `test_read_ahead_stops.py`; an independent review confirmed that every other assertion of the three passes at the default, 30 runs of 30 |
| `tests/e2e`: the fixtures of `test_pocket_research_e2e.py` (also used by `test_a11y_tree.py`), `test_presence.py`, `test_console_research.py` | `conftest.paced` scales the fixture's per-page delay by the pages fetched at once; assertions untouched; the individuals keep the default `read_ahead` | Four tests need the reading stage to last long enough to be observed (a visible "Reading" stage, a theme switch during it, a cancel during it); with the pages fetched together the stage lasted one delay. A non-browser test now checks that each source read is its own progress event at the default |
| `tests/e2e/test_reasoning_console.py`, fixture | Sets `critique_restatements` on | Its fake model copies the claim word for word and its fake critic marks that sentence; the test is about rendering a mark. A new test beside it covers the default scope |
| `tests/instance/test_executive.py::test_open_questions_expire_after_seven_days` | `asks_used == 2` → `== 1` | The expired question is outside the window. The old assertion lives on in `test_an_expired_question_still_counts_when_the_budget_has_no_window` (window 0) |
| `tests/instance/test_executive.py::test_ask_budget_turns_the_fourth_new_question_into_defer` | Two assertions added | The reason names the window |
| `tests/instance/test_erasure.py::test_sweep_expiry_scrubs_a_root_whose_last_evidence_expired`, `tests/instance/test_journeys.py::test_cli_sweeps_expired_evidence_before_reading` | Run with consolidation off; assertions unchanged | They assert the 0.7.0 meaning of expiry. Each has a new twin for the default |
| `tests/instance/test_checkpoints.py`, `test_migrations.py`: the helpers that rebuild an older schema | Also drop the schema-14 objects | Each new migration has needed this |
| `tests/instance/test_graphs.py`: `test_simulate_learn_evaluate_promote`, `test_gate_d_owner_rule_budget_and_rule_version_is_frozen_at_learn`, `test_gate_e_rule_version_and_blob_are_immutable`, `test_cascade_picks_the_newest_eligible_snapshot_past_a_rolled_back_one` | The individual is born under `gate-v1` alone (a fixture); bodies unchanged | They assert `gate-v1` behaviour. The same journey under the birth rules is a new test |
| `tests/instance/test_graphs.py::test_cli_graph_lifecycle` | The rule list is `gate-v1`, `best-learner-v1` | What `nsf init` now registers |
| `tests/console/test_console_api.py::test_graph_journey_over_http`, `tests/e2e/test_console.py::test_owner_journey` | Six gate checks, not five | The no-graph check |
| `tests/e2e/test_console_uat.py` | Memory kinds 13 → 14 | The kind `consolidated` |
| `tests/test_setup_cli.py::test_a_fresh_run_does_every_step_and_names_every_lan_address`, `tests/test_resources_idle.py::test_resources_idle_shows_loaded_as_resources_does` | Exact dictionaries gain the step `memory` and the services `embed`, `rerank` | New step, new services |
| `tests/test_resources_idle.py::test_an_active_systemd_unit_is_stopped_and_started_with_systemctl` | One assertion added; its fake host reports a stopped unit as inactive, as systemd does | With the corrected fake the old code fails the test: that is finding R11 |
| `tests/webcortex/test_autonomy.py::test_two_concurrent_autonomous_investigations_at_most_across_processes`, `tests/webcortex/test_browser.py::test_hung_render_kills_driver_and_chromium` | Each now kills a process that outlived it (the first also asserts that none did); no assertion removed | On one of two CI runs of the same commit both failed on a slow runner, and the processes they left held the log pipe until the job's 40-minute limit: the hang `main` already had a timeout for. The first forks from a test process that holds 34 threads of earlier tests on `9e4570a` and 35 here; neither failed in 12 local runs on one busy CPU on either commit |

Tests that the phase-2 lanes themselves added and later rewrote during review (the consolidation file, the
critique-scope file, the listener and wake tests) are not listed: they did not exist at `9e4570a`.
`tests/inference/test_inference_reasoning.py`, the guard's own suite, is byte for byte the file of `307b907`.

## 2. Capability matrix

| Capability | Touched by this branch | How it was checked | Result |
|---|---|---|---|
| Executive decisions, beliefs, change detection, scoped relearning | Phase 1: no. Phase 2: beliefs, the change window and epoch matching read consolidated readings beside events; the ask budget counts a window | `tests/instance`; 80 randomised lives against a twin whose events never expire (decisions, reasons and tallies equal); the consolidation fields are absent while zero, so the recorded acceptance evidence and the exact-equality tests of an individual with nothing consolidated are untouched | Verified |
| Graph learning, evaluation, promotion gate, rollback, recall | Phase 1: `lesion` only (closed form). Phase 2: the edge exchange for all receivers at once; a sixth gate check; `best-learner-v1` for new individuals | `tests/instance`; equality with the old computation to 1e-15; the exchange bit-identical to the lab loop (parity tests, 43,000 random cases by an independent review); `pack` under a rule without the new parts byte-identical to before (400 random cases); a candidate without the new metric judged by its five checks as before; reproduction 8 of 8 | Verified. A behaviour change for new individuals and for a snapshot no better than no graph (§3) |
| Evidence journal, lifetimes, promotion | Phase 2: an expiring learning event leaves a consolidated reading; the event itself expires as before | `tests/instance/test_consolidation.py`; with consolidation off the expiry tests keep their 0.7.0 assertions | Verified; a behaviour change by design (§3) |
| Checkpoints and restore | Yes: a restore replays more forgets. Phase 2: it also turns expired events of an older archive into readings, removes readings forgotten since, replays camera and partner forgets, continues the live file's evidence sequence and carries the owner's settings | `tests/instance/test_checkpoints.py`, `test_consolidation.py` through a second generation; two independent restore fuzzes (12 and 15 random lives, first and second generation): nothing forgotten returned; archives byte-exact | Verified |
| Governed forgetting | Yes: three cascades widened. Phase 2: every forget cascades into consolidated readings; new forgets by journal position and by camera; `forget_partner` also removes the partner's change markers | `tests/instance/test_erasure.py`, `test_governance.py`, `test_consolidation.py`; new tests failed on the old code first; the security review checked the file, the WAL and free pages at the byte level after each forget | Verified; stricter than before |
| Schema and migrations | Phase 1: locking only. Phase 2: **schema 14** (one table, `evidence_consolidated`, and indexes; nothing existing altered) | `tests/instance/test_migrations.py` with a two-connection interleaving; a version-13 file keeps every row (`integrity_check`, `foreign_key_check`); the opt-out on a version-13 file is in place before anything is swept; older software refuses a version-14 file | Verified |
| Saved instance data | Not rewritten. An existing ledger gains three columns and an index on open; older code ignores them. Phase 2: settings saved by 0.7.0 read the new defaults (consolidation on, a 7-day ask window, `read_ahead` 4, the narrowed critique) | Tests with 0.7.0-shaped ledger, settings and web-configuration rows | Verified. The new defaults are behaviour changes (§3) |
| Intent, discovery, safe fetch, extraction | Reading stage and fetcher. Phase 2: read-ahead on by default (4); every page fetch gated per request | Offline benchmark identical at 1, at 4 and at the default; `tests/webcortex`; 400 and 600 random small webs under tight budgets with 0 differences in the second review pass. The third pass found one case that differed (a redirected robots.txt inside a continued fetch); it was fixed (`989a384`) and the fourth pass found no difference in the reproduction, the replayed scenarios or 600 further random webs | Verified on the benchmark and the fuzz named |
| SSRF, robots.txt, limits, backoff, budgets, injection containment | Fetcher. Phase 2: the same rules on the fetch-ahead threads; a shared budget for the pages fetched ahead | Benchmark safety summary: all ten hold at 1, 4 and the default; an independent review tried redirects to loopback, link-local, metadata, hex-IP and rebinding addresses on the fetch-ahead path (all refused), and found no second connection to one site after the fix | Verified offline. Traffic can exceed the request budget by the unused read-ahead (§3) |
| Claims, verification states, word sense, synthesis | No | `tests/webcortex`; benchmark identical | Verified |
| Model-written answers and the guard | Phase 1: the guard no; `apply`: two arguments and the abstention label. Phase 2: one refusal added to `compose` and `guard`; the critique block of `apply` | The guard suite of `307b907` (827 tests) passes unmodified; a committed differential replays 1,476 inputs recorded from `307b907`'s own code: 1,459 identical, 17 stricter only, 0 different; pipeline benchmark: 44 to 46 model answers in every run, the critique removed 0 | Verified. An attempted change to the guard was withdrawn after review (§3) |
| Guard survival and attack tests (eight review rounds) | No | The same tests, unmodified; their 1,137 inputs are part of the differential and all identical | Verified |
| Self-critique | Phase 1: default unchanged; optional skip for spoken answers. Phase 2: it reads only sentences that are not one cited claim word for word | Tests for each setting; `critique_restatements` on gives the 0.7.0 request and record byte for byte; live counts in [PERFORMANCE.md](PERFORMANCE.md) §7.1 | Verified; a behaviour change by design (§3) |
| Memory retrieval | Default (lexical) path restructured, same result; optional semantic path. Phase 2: a model the idle policy unloaded gives a lexical answer at once and is woken; `nsf setup` can switch the semantic path on | Offline benchmark memory metrics identical; `accel_after_memory_lexical.json` equals the baseline class by class; the memory benchmark re-run with the committed settings (paraphrase recall@1 0.979, as before); new tests; a live check with the real containers | Verified |
| Memory answers without the web | Yes: stricter when a model is configured | Existing test of the memory answer passes unchanged (no model: identical events); new tests | Verified; a behaviour change by design (§3) |
| Knowledge evolution, hypotheses, predictions, consolidation | No | `tests/webcortex` | Verified |
| Autonomy, budgets, curiosity | Queue order. Phase 2: tasks under a daily allowance never read ahead, and the allowances count unused traffic | `tests/webcortex/test_autonomy.py`, scheduler tests, cap-boundary tests (399 of 400 leaves one, 400 of 400 refuses) | Verified |
| Worker, leases, crash resume, cancel | Wake-up and claim order. Phase 2: a cancel cuts the requests on the wire | Scheduler tests; benchmark checks `cancel_queued`, `cancel_running`, `crash_resume` hold; a cancelled run ends about 0.25 s after the cancel with no thread, busy mark or budget hold left (independent review) | Verified |
| Inference gateway: health states, streaming, retries, timeouts, structured output, tools, local-first guard | Additive. Phase 2: before a request to a loopback port of the deployment's own services, a check of who owns the listening socket; a refused connection to such a port can leave a wake request | `tests/inference` (prior tests unchanged); `tests/test_listener_check.py` with kernel-shaped tables and real sockets; an endpoint elsewhere on this computer is not inspected | Verified. **Unverified**: a listener of a real second account |
| Supported model and provider | Unchanged: the same model, precision and image digest | `accel_after_inference.json`: reasoning 15 of 16, tools 8 of 8, as before | Verified live |
| Resource pressure level and thresholds | Not changed (reporting added) | `tests/test_resources.py` | Verified |
| Audio priority, background model slots | Pocket now counted | Tests | Verified; stricter |
| Idle unload and reload | Reload bounded; memory refusal. Phase 2: the memory models as services of the policy; a stopped unit restarted as that unit; loaded only once it answers; one reload and one retry limit across processes; wake requests | `tests/test_resources_idle.py`, `tests/test_resources_wake.py`, `tests/inference/test_inference_idle.py`; live for the two memory containers (unload, lexical lookup, wake, 20 s back); the wake path and the unit sandbox with transient **user** units | Verified against the fake host and, for the memory models, live. **Unverified**: the units installed as system units, the idle timer, the installer as root, a two-account install, a container that never answers |
| Voice: ASR, TTS, bridge, modes, barge-in, transcripts, commands | Hub ordering, chunk repeat, follow interval | `tests/voice`; Node check of the voice client; browser suite | Verified with synthetic audio. **Unverified**: real microphone and speaker (as in 0.7.0) |
| Pocket: pairing, families, HTTPS, signed replies, push, nodes | Audio beacon; the voice client file (offline cache version 26 → 27) | Pocket tests over a real TLS handshake; `tests/js/offline_check.mjs`; browser suite | Verified. **Unverified**: a physical handset (as in 0.7.0) |
| Console: API, token, Host and Origin checks | `/api/resources` fields added. Phase 2: memory kind `consolidated`, forget kinds `consolidated` and `camera` on the existing `/api/forget`, the sixth gate check and "Against no graph", the ask window | `tests/console` (403 without or with a wrong token and with a foreign Origin; 400 for a bad position); the console browser files, with an accessibility scan of the new controls | Verified |
| Presence entity: states, motion, light and dark, accessibility (axe) | No product change | Browser suite (`tests/e2e/test_presence.py` 18 of 18; the full suite) | Verified |
| CLI contracts | Added: `nsf web memory`, switches on `nsf web config` and `nsf llm config`. Phase 2 added: `nsf config`, `nsf forget consolidated`, `nsf forget camera`, `nsf memory consolidated`, `nsf graph rules add --builtin`, `nsf resources wake`, `nsf resources load --on-demand`, `nsf setup --no-memory`, `nsf llm config --critique-restatements`, `nsf init --ask-window-days / --no-consolidation`. Removed: none | CLI tests; `nsf setup` tests | Verified |
| Deploy scripts | vLLM flags; `wait`; new `deploy/memory`. Phase 2: the wait's grace for a container that does not exist yet (three scripts), the memory script's warm-up request and port check, four new units, `deploy/install.sh --memory` and `--idle` | `bash -n`; the vLLM container restarted through `run.sh` twelve times in phase 1 and returned to healthy each time (not restarted in phase 2); the memory containers started, served and were stopped repeatedly (about ten times in the first round of phase 2 alone); unit tests of the scripts against a fake Docker; `systemd-analyze security --offline` 1.6 for the memory units, as the ASR unit | Verified on this host for the memory script. `deploy/install.sh` beyond `--dry-run` and every systemd unit as an installed system unit: **unverified** (not installed here, as in 0.7.0). The wait's grace for the answer-model and speech scripts: unit-tested only |
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

Phase 2, the owner's decisions and what followed from them:

8. **An expired learning event leaves a consolidated reading** (ADR-044). Beliefs no longer return to the prior
   after the evidence lifetime; the change window and epoch matching reach past it; a root's name and a sample id
   stay in the file until the reading is forgotten. Existing individuals get this on upgrade. Off:
   `nsf config --no-consolidation`, which on a version-13 file takes effect before anything is swept.
9. **The ask budget refills**: questions older than 7 days no longer count. Off: `nsf config --ask-window-days 0`.
10. **A restored copy continues the evidence sequence of the live file and takes its settings** (consolidation,
    ask budget and window). A plain copy (`apply_tombstones=False`) keeps the archive's. A saved `consolidation`
    that is not true or false reads as off, with a warning until it is set.
11. **`nsf config` is the one instance command besides `nsf sweep` that does not expire due evidence first**, so
    an opt-out can come before the first sweep.
12. **The owner's research tasks read up to four pages at once**, one per site. Reports, charged usage and events
    are those of one-at-a-time reading; usage may also carry `prefetch_unused_requests` and
    `prefetch_unused_bytes`, at most the task's budget again. Curiosity, refresh and MCP tasks are unchanged.
    Off: `nsf web config --set read_ahead=1`.
13. **A cancel, a denied site and switching web access off take effect before the next request of a page fetch,
    and cut the requests on the wire.** A cancelled task ends within about a quarter of a second. No switch: the
    old timing contradicted the stated property.
14. **A site that answers "slow down" (429) to a page fetched ahead is left alone at once.** Missing from the
    report are the pages that lead onto that site; the report names the site and the reason. This is the one
    stated case in which a report differs from one-at-a-time reading. A report now names a site that asked for
    fewer requests at either setting.
15. **A request cut for a stop that was then withdrawn ends as `interrupted`**, a new fetch error that says
    nothing about the site.
16. **The self-critique reads fewer sentences.** A sentence that is one cited claim word for word, with the
    attribution the code chose, is not shown to the critic and can carry no "self-check flagged" mark; an answer
    made only of such sentences makes one model call and its report says why. Off:
    `nsf llm config --critique-restatements`.
17. **A sentence that ends in its own attribution followed by more words is rejected** ("ends in its own
    attribution followed by more words …") where 0.7.0 showed it without those words. No switch: it only refuses.
18. **A graph that is no better than no graph is not promoted**, under any rule, for candidates learned from this
    version on. **New individuals learn graphs under `best-learner-v1`**; existing individuals are not changed.
    `growth_retention` in a regenerated `acceptance.json` would differ in nine leaves, listed in
    `accel_decisions_graph_birth_rule.json`; the recorded file was not regenerated, and the first physical test
    stays on `gate-v1` as preregistered.
19. **`nsf setup` has an eighth step, `memory`**: with the memory models answering and no memory setting made by
    hand, it switches semantic retrieval on. Without their key file it contacts nothing. Off: `--no-memory`.
20. **A systemd unit the idle policy stopped is started again as that unit**; a container counts as loaded only
    once it answers; a start that never becomes ready is stopped.
21. **Nothing is sent to a loopback port of the deployment's own services when a process of another account holds
    it**; the call fails as `unavailable`. Off: `NSF_LISTENER_CHECK=0`.
22. **A forgotten partner's change markers are deleted with it**, as a forgotten camera's are.

Additive contract changes (new optional fields, no removed or renamed field): `retrieval` in the knowledge query
result; `memory_check` in the needs-web decision event; `abstained` in `reasoning`; new readings in
`GET /api/resources`; `provider`, `priority`, `cached_tokens` in ledger rows; `prefetch_unused_*` in task usage
(absent while zero).

Added in phase 2, also additive: `judged` and `restatements` in `reasoning.critique` (only when a sentence was left
out) and the setting `critique_restatements`; the refusal reason of item 17; `interrupted` among the fetch error
codes and the "asked for fewer requests" wording of a limitation; `read_ahead` in `nsf web status`;
`retrieval.waking`, the provider states `asleep` and `refused` in `nsf web memory`, the step `memory` in
`nsf setup --json`, the services `embed` and `rerank` in `policy.idle_unload.services`; the consolidation fields
of beliefs, sweeps, forgets, inventory and restores (`consolidated`, `consolidated_readings`, `settings_carried`,
`settings_warnings`, `scope_changes_removed`; the counts inside results are absent while zero), the memory kind
`consolidated`, the forget kinds `consolidated` and `camera`; `ask_budget_window_seconds` in a decision;
`beats_no_graph` and `no_graph_validation_mse` in a candidate's metrics, the sixth entry of a gate decision's
checks, and the optional rule parts `pack_candidates.learned_sparse` and `pack_reference`. They are listed with
their shapes in `docs/architecture/API_CONTRACTS.md`.

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
| Phase 2: a stop that lands while a connection is being opened, during a TLS handshake, or while the reader waits for a page fetched ahead | Stop tests; the independent review's scripts | Nothing sent, nothing charged, the fetch ahead cut |
| A site that stalls, redirects onto the site being read, or answers 429 to a page fetched ahead | Stop and read-ahead tests; review scripts | One connection per site; the stated 429 exception |
| The request or byte budget running out with pages in flight | 15 budget edges compared at 1 and 4; random webs | Identical outcomes, including the case of a redirected robots.txt inside a fetch ahead that the third review pass found and the fourth confirmed fixed |
| A watch that cannot read the file while a request is on the wire | `test_a_watch_that_cannot_be_read_does_not_cut_a_healthy_request` | Nothing is cut; no false robots.txt verdict |
| Events expiring in any order relative to forgets, promotions, confirmed changes and restores | Randomised lives against a twin that never expires; restore fuzz | Beliefs equal; nothing forgotten returns, through a second generation |
| A damaged settings row; a setting that is not a boolean; a position beyond SQLite's integers | Restore and settings tests | The restore goes on and reports it; consolidation reads as off; a validation error |
| A file migrated by the first build of schema 14 | A test that builds that shape and opens it | Its indexes converge at open |
| A memory model unloaded, starting, or refusing to start while lookups arrive from several processes | Idle tests; live with the real containers | A lexical answer at once, one reload, one retry per 30 s |
| A request folder holding a directory, a symlink or an entry that cannot be removed | Wake tests; a transient user unit | Removed or set aside; the unit does not loop |
| A scoring rule with non-integer or non-list bit widths | Rule tests | Refused at validation |
| GPU memory exhaustion; a failed image upgrade and rollback; days of wall-clock autonomy | — | **Unverified** in this review (the first two were not provoked on a shared host; the third is the owner's running soak, left untouched) |
