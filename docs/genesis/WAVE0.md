# Wave 0: the benchmark, the imagined store, the quiet switch

Status: 2026-11-25. Wave 0 of Project Cognitive Genesis, integrated on branch `cognitive-genesis` from `main` at
`07a8728` (schema 16) to `76a478e`. Three lanes built it (Z, P, S); two independent reviews read it; two fix lanes
closed their findings. Wave 0 adds safety prerequisites and a measuring instrument. It adds no cognitive mechanism,
so it claims no cognitive gain. Sources: the lane reports, the two reviews' findings, the handoff,
[COGNITIVE_BENCHMARK.md](../quality/COGNITIVE_BENCHMARK.md), ADR-049, ADR-050 and the evidence files named below.

## 1. What Wave 0 delivered

| Piece | What it is | Code | Decision | Evidence |
|---|---|---|---|---|
| Benchmark and baseline (lane Z) | A reproducible harness of seven families (anticipation, information value, counterfactual validity, self-description, selective answering, retention, adversarial cases) plus a withheld transfer family scored only with `--final`. Synthetic and fictional worlds driven through the real modules, offline, no answer model. Development, held-out and withheld task definitions committed alone before the harness and pinned by SHA-256 | `scripts/bench_cognition.py`, `src/neuroscifi/acceptance/cognition/` | — | `docs/quality/evidence/genesis_baseline.json`, `genesis_final_uses.jsonl`, [COGNITIVE_BENCHMARK.md](../quality/COGNITIVE_BENCHMARK.md) |
| Provenance kinds and the imagined store (lane P) | Five kinds (`evidence`, `inference`, `prediction`, `simulation`, `speculation`); the last three are quarantined in one store (schema 17) that nothing on the evidence side can name, that no evidence path may import, that the answer model never sees as evidence and retrieval never returns, bounded in rows and lifetime, covered by every forget and by restore | `src/neuroscifi/provenance.py`, `src/neuroscifi/instance/imagined.py`, `instance/schema.py` (migration 17), `nsf imagined list\|show\|purge\|kinds` | ADR-049 | `docs/quality/evidence/genesis_provenance.json` |
| The quiet switch (lane S) | One owner setting, `quiet`, that holds everything the individual does unasked (worker claims and resumes of background tasks, the idle investigation, the tick's monitors, refreshes and curiosity, a running background task) while upkeep and delivery keep running; a registry of every background activity that new mechanisms must join | `src/neuroscifi/background.py`; `nsf quiet on\|off\|status` | ADR-050 | `docs/quality/evidence/genesis_safety.json` |
| One admission rule (lane S) | `autonomy.admission` for both autonomous paths: the tick now applies the idle investigator's rule (recorded enable, 0.05-bit floor, goal weighting, lineage, 24 h cooldown, claim shapes) over its old pool | `webcortex/autonomy.py`, `webcortex/scheduler.py` | ADR-050 | `genesis_safety.json` (`admission`) |

Commits on `cognitive-genesis`: P `bd6995c`, `b1000e3`; S `a00d77f`, `14a60b2`; Z `952621e` (the frozen task
definitions alone; `01a9a0d` on the lane branch), `9af3a19`, `1757a7e`, `24da33c`; integration `2aee516`; first fix
round `c16521a`, `5a36908`, `2a37166`, `962226d`, `03cbece`; second fix round `993bade`, `1697e88`, `8776e87`,
`76a478e`.

### Settings added

| Setting | Default | What it does | Why that default |
|---|---|---|---|
| `max_imagined_rows` (instance) | 1000 (0 to 100,000) | most imagined records kept; the oldest is evicted first; 0 = the store accepts nothing | a bound, not a mechanism; nothing writes to the store yet |
| `imagined_lifetime_seconds` (instance) | 7 days (1 minute to 365 days) | how long a record is kept; a writer may only shorten it | as above |
| `quiet` (instance) | off | holds everything done unasked | today's behaviour is unchanged until the owner turns it on; a safety control, not a mechanism with a gain |
| `tick_admission` (web) | `"unified"` | `"legacy"` restores the tick's old rule exactly, for one release | the stricter rule; on the benchmark it only ends a relaunch loop and never admits a subject the old rule did not |

All four are owner-settable from the command line only and carried into a restored copy. A damaged `quiet` value
reads as on; a damaged imagined limit reads as its default.

## 2. The baseline

Measured on the unchanged system (`scripts/bench_cognition.py baseline` at `19f80e2`, system code unchanged from
`07a8728`; aarch64, Python 3.12.16, numpy 2.3.5, SQLite 3.53.1). Mean over the split's seeds, sample sd in brackets.
Every family made 0 model calls and 0 connections outside loopback.

| Family | Headline metric (direction) | Development | Held-out | Reference |
|---|---|---|---|---|
| anticipation | breach ticks, all channels (lower) | 202.7 (2.1) | 217.7 (4.0) | |
| | memory breaches avoided (higher) | 0 of 3 | 0 of 7 | |
| | mean lead of the first hold before a memory breach, ticks (higher) | 4.3 (0.3) | 5.8 (0.8) | |
| | false-alarm share of holds (lower) | 0.000 | 0.009 (0.015) | |
| | background work given up (lower) | 0.023 | 0.044 | |
| info_value, core | realised bits per investigation (higher) | 0.014 | 0.092 | oracle 0.035 / 0.119; random 0.037 / 0.083 |
| info_value, web | realised bits per unit of budget (higher) | 0.161 | 0.093 | oracle 0.263 / 0.146; idle path −0.040 / −0.016 |
| | share of budget on noise (lower) | 0.375 | 0.537 | oracle 0.00; idle path 0.81 / 0.80 |
| counterfactual | coverage of answerable queries (higher) | 0.107 | 0.071 | only one-step graph queries are answered |
| | relative error where answered (lower) | 0.045 (0.021) | 0.045 (0.038) | |
| self_model | coverage / accuracy when answered | 0.80 / 1.00 | 0.80 / 1.00 | 4 surfaces; never answered: uncertain_about, last_decision, authorised_model_tools |
| selective | accuracy at full coverage | 0.857 (21 items) | 1.000 (20 items) | |
| | AURC (lower) / AUROC | 0.080 / 0.685 | 0.000 / undefined | no wrong held-out answer |
| | claim probability Brier / ECE, 10 bins (lower) | 0.095 / 0.304 | 0.122 / 0.341 | all of the Brier score is reliability error |
| retention | scenarios pass | 4 of 4 | 4 of 4 | 7 offline metrics recorded as the reference values |
| adversarial | cases passing | 12 of 13 | 12 of 13 | failing: `simulated_reading_moves_no_belief` |
| withheld (`--final`, once) | breach ticks; web noise share; counterfactual coverage; self coverage | 137.3 (6.7); 0.857; 0.143; 0.80 | | ledger: one entry |

The baseline was re-run after each fix round (development, held-out and the four reference policies; the withheld
family not run): 0 differences against `genesis_baseline.json` both times (cost, environment and timestamps
excluded). The file was not changed.

Not claimed: anything about the real host's dynamics, the open web or the answer model, and no claim that any of these
numbers measures "intelligence". The anticipation plant's constants are declared by the benchmark, not measured on
the Spark.

## 3. Measured for the safeguards

| What | Lane build | After the first review | After the second review |
|---|---|---|---|
| Imagined-store safeguards, each broken alone in a copy of the source, caught by a test | 20 of 20 | 34 of 34 (control 203 passed) | 42 of 42 (control 222 passed) |
| Quiet and admission safeguards caught | — | 10 of 10 (control 34 passed) | 14 of 14 (control 43 passed) |
| Guard differential (`scripts/guard_differential.py compare`) | 1937 compared, 0 different | 0 different | 0 different |
| Full non-e2e suite on the lane's branch | P 5136 passed; S 5076 passed; Z 5073 passed, 1 timing failure under load (passed 3 of 3 alone) | 5314 passed, 19 skipped | 5342 passed, 19 skipped, 0 failed |

Costs (`genesis_provenance.json`, `genesis_safety.json`, medians on this host):

| What | Before | With Wave 0 |
|---|---|---|
| 20,000 evidence inserts (trigger overhead) | 0.094 s | 0.116 s (+1.1 µs a row; first fix round, same session) |
| 5,000 claim inserts | 0.086 s | 0.090 s (first fix round, same session; the start commit itself measured 11.5 µs a row there, not the 3.8 µs first recorded) |
| A sweep with nothing due, store empty | 0.078 ms | 0.085 ms |
| A sweep with 1,000 imagined records held | 0.09 ms | 0.44 ms (it now looks for orphans whatever expired) |
| A forget, store empty | about 1.0 ms | about 1.0 ms |
| `background.allowed` | — | 6.4 µs |
| `claim_next` | 0.200 ms (quiet off) | 0.222 ms (quiet on) |
| Quiet honoured by a worker polling every 5 s | — | hold recorded after 1.56 to 4.40 s in 5 trials; a running background task stopped 0.25 s after the switch with 0 page requests after it |

The second fix round's added triggers (one column per claim insert, two on monitors) were not re-measured.

## 4. The first independent review (`2aee516`): findings and what was fixed

Verdict: medium overall; 0 critical, 0 high, 3 medium, 8 low. Fixed by lane fix0; each fix has a test that fails on
`2aee516`.

| Finding | Severity | What it was | Fix |
|---|---|---|---|
| M1 | medium | `observe()` wrote an outcome's evidence references and free-text observation into the outcome only, so forgetting what an outcome named left the record | the observed evidence joins the record's references (`imagined_refs.observed`), so forgetting it removes the record; an observation carries no text (a bounded number, true/false, null, or an object of payload keys to such) |
| M2 | medium | the trigger matched any quoted string in free-text JSON, so a web page titled `imagined:<32 hex>` aborted consolidation of its whole task (remote, repeatable) | only whole-id columns and JSON id lists (`json_each`) are checked; free text is never tested; migration 17 amended in place with a repair for files of the first build |
| M3 | medium | the benchmark's retention read the baseline's cached corpus run, so retention equalled baseline by construction with a mechanism on; a `--quick` run was compared with the full baseline | the cache is dropped when a mechanism is switched on and off; retention gives no verdict across corpora |
| L1 | low | INSERT OR REPLACE could rewrite a record and drop its lineage without a tombstone | BEFORE INSERT triggers on `imagined` and `imagined_refs` |
| L2 | low | quiet turned on while a background task synthesised or reasoned still called the answer model and consolidated | quiet asked again before both model calls and before consolidation |
| L3 | low | lowering the limits did not apply to held records; the sweep missed orphans; a record could derive from a forgotten epoch | `imagined.bound` applies a lower limit in the same transaction; every sweep removes orphans; a forgotten epoch is never a source |
| L4 | low | the triggers accepted upper case, padding and JSON escapes, and missed several reference columns | lower-cased and trimmed comparison; `json_each`; columns added: `web_tasks.parent_id`, `web_events.task_id`, `web_mentions` ids, `web_hypotheses.knowledge_id`/`parent_id`, `web_predictions.knowledge_id`/`hypothesis_id`, `web_feedback.knowledge_id`, `voice_segments.task_id` (free-text columns such as `pending`, `belief_revisions.summary` and `web_events.detail` deliberately not) |
| L5 | low | the quarantine scan was an opt-in list of 22 modules | an allow-list over every module (STORE, WRITERS, DISPLAYS, each with a reason); evidence paths may never be listed |
| L6 | low | with quiet on, the owner's own `nsf web resume` or `requeue` of a background task was held | `background.asked_by_owner` records a decision event; owner-asked work is never held |
| L7 | low | the withheld guard could be given another ledger | `run()` takes no ledger; the rest of the guard is stated as procedural |
| L8 | low | the launch scan missed threads, aliased imports, `functools.partial` and non-literal origins | references to entry points and every non-literal `origin=` are flagged; `background.FORWARDERS` |
| Notes | — | `tick_admission` not carried by restore; the unified tick could relaunch a subject its refresh just queued; LIKE against case-sensitive matching; `observe()` skipped the write-pause check; tombstones unbounded | all fixed (live value carried; same-tick dedupe; GLOB everywhere; write check; tombstones kept 365 days) |
| Note | — | the legacy decision event's `autonomy` field is a string | not changed: display only, matches the existing convention, and a dict would feed the cooldown and change the legacy rule |

## 5. The second independent review (`03cbece`): findings and what was fixed

Verdict: low; all first-review findings fixed or mostly fixed. Fixed by lane fix0b; each fix has a test that fails
on `03cbece`.

| Finding | Severity | What it was | Fix |
|---|---|---|---|
| N1 | low-medium | the quarantine scan's forbidden list had lost the orchestrator, autonomy and curiosity | back in the forbidden list; ruling recorded: curiosity and autonomy may not read imagined records (scenario outcomes reach them only as settled prediction errors) |
| N2 | low | two processes opening a first-build file together: the second failed (duplicate column) | the repair re-reads the catalogue under `BEGIN IMMEDIATE` |
| N3 | low | the repair kept observations written by the first build (text outcome, evidence not referenced) | the one-time repair removes them, with tombstones and one deletion-log row |
| N4 | low | an observed integer could be any size (text encodable in its digits; `10**400` raised OverflowError) | integers within ±2^53, finite floats, otherwise `ValidationError` |
| N5 | low | a refresh stopped by quiet settled its prediction "inconclusive", and calibration counted it | the prediction stays due and the next check resubmits |
| N6 | low | the repair dropped any trigger named `*_no_imagined_*` not in today's list | it drops only `_RETIRED_17`, by name |
| N7 | low | a function in `FORWARDERS` was exempt from the launch scan as a whole | a forwarder that also launches work or refers to an entry point must be registered |
| N8 | — (document) | trigger forms and columns still let through | three cheap columns added (`contradicting[].claim_id`, `web_monitors.last_task_id`, `web_claims.source_id`); the rest documented in ADR-049 ("What the triggers and the scans cannot see") |
| N9 | info | the inert baseline cleared the corpus cache, so recorded costs were not reproducible | the inert baseline keeps the cache; baseline re-run: 0 differences |
| N10 | info | an evidence id or epoch name like an imagined id failed with a raw IntegrityError | refused up front with `ValidationError` |
| N11 | — (document) | a damaged settings row in an archive shrinks its imagined store on rewrite (fail-closed) | documented in ADR-049 |
| N12 | info | an owner-asked background task kept the "autonomous" label in its answer | labelled owner-confirmed |
| Tick dedupe | partial | the unified pool could launch a subject as another kind (a prediction, a fact-verified hypothesis) | a pooled subject is admitted only in the kind the timer ranked |

Defaults changed by the fixes beyond the findings (ADR-049 and ADR-050, "Consequences"): the unified tick skips a
subject whose refresh it queued in the same tick; `check_due` actions carry `knowledge_id`; every sweep removes
orphans and can mark checkpoints stale; an owner's requeue of a background-origin task records a decision event even
with quiet off; `store.set_config` returns `imagined_removed`; raising an imagined limit runs a full expire pass.

## 6. Validation

| Run | Result |
|---|---|
| Gate on the integrated head before `2aee516` | 5179 passed, 19 skipped, 1 failed (lane S's registry scan flagged the benchmark's 5 runner functions) → fixed in `2aee516` |
| Validation on `2aee516` | gate 5181 passed, 19 skipped; acceptance 0; guard differential 0 different; two-account check 55 of 55; e2e 207 passed + 1 timing flake (Pocket "enrol this device", 30 s; the file passed 6 of 6 on 5 reruns); benchmark development run equal to the recorded baseline |
| Validation on `03cbece` | gate 5314 passed, 19 skipped; e2e 208 passed, 1 skipped; acceptance 0; guard differential 0 different; two-account check 55 of 55; benchmark development and held-out ran |
| Lane fix0b on its branch | full non-e2e suite 5342 passed, 19 skipped, 0 failed; `ruff` clean; `tests/js` 7 of 7; `scripts/reproduce.py` 8 verified; e2e not run (no UI touched) |
| Final validation on `76a478e` | <<WAVE0-FINAL>> |

## 7. What stays open

| Item | Source |
|---|---|
| A reading labelled `provenance='simulated'` still moves a belief (adversarial case failing at the baseline). Lane M (Wave 1) ruled the case mislabelled: simulated-environment readings are evidence (ADR-014). Preventing simulated-environment and real readings from mixing in one epoch is an owner decision and needs a schema change | handoff; lane M |
| The triggers catch an imagined *id*, not imagined *text* copied into a claim; text is kept out by code isolation (the allow-list scan) and canary tests, not by the database | ADR-049 |
| The forms and columns the triggers and both scans cannot see (N8 remainder, N11, tombstones kept by wall-clock age and bounded by age, not count) | ADR-049, "What the triggers and the scans cannot see" |
| The `git show` test of both earlier builds of migration 17 skips in a clone without that history (CI's shallow checkout); the synthetic first-build test still runs | lane fix0b |
| A big-integer observation written by the `03cbece` build is not purged by the repair (SQL cannot tell an over-2^53 integer from a float); only new ones are refused | lane fix0b |
| An epoch already named like an imagined id in an older file still makes its forget fail (refused at creation only) | lane fix0b |
| The tick's pool (curiosity's ten best) is crowded by contested subjects that are not knowledge, so stale knowledge items are never reached by either rule; widening it is a loosening left to the owner | lane S; ADR-050 |
| The legacy decision event's `autonomy` field is a string (display only) | lane fix0 |
| No console control for quiet: command line only, by design | lane S |
| **Unverified:** real systemd units with quiet on; Pocket on real handsets; behaviour on a real owner's file (only temporary individuals, and files built from each commit's own schema, were used); cost on the DGX Spark under load | lane reports |

## 8. Backing each piece out

| Piece | How to back it out | What stays |
|---|---|---|
| The imagined store | `nsf config --max-imagined-rows 0` (the store accepts nothing); `nsf imagined purge --all` (rewrites the archives it made stale) | schema 17 is forward-only: older software refuses the file, so its empty tables and triggers stay |
| An evidence id, root, sample id or epoch name of the form `imagined:` + 32 hex digits | refused by design (a contract narrowing with no realistic collision) | — |
| Quiet | `nsf quiet off` (the default) | the registry and checks, which cost 6.4 µs per check |
| The unified admission rule | `nsf web config --set tick_admission='"legacy"'` restores the tick's old rule exactly (kept for one release) | — |
| The benchmark | nothing in the product reads it (`2aee516` registers it as measurement only and forbids product imports); revert its commits | — |
| Any Wave 0 code | revert its commits on the wave branch before the merge into `main` | a file already migrated to schema 17 |
