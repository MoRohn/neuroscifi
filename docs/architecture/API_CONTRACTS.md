# API contracts

Status: **binding for Wave 2+**. A module may add private helpers. Changing a signature listed here needs an entry in `ARCHITECTURAL_DECISIONS.md` and an update to this file in the same commit.

Requirement IDs refer to `REQUIREMENTS_MATRIX.md`.

## 0. Conventions

- **One individual = one SQLite file.** The schema lives in `src/neuroscifi/instance/schema.py`, migrations are forward-only and tracked in `PRAGMA user_version`. Each table has one owning module (the comment above it in `schema.py`). Only the owner writes it. Other modules read it, or call the owner's function.
- **Connections.** `store.connect(path)` opens in autocommit mode (`isolation_level=None`). Mutations use `with store.write(db):`, which wraps `BEGIN IMMEDIATE … COMMIT`. Each committed logical change calls `store.bump_state_version(db)` exactly once. Public functions that mutate open their own `write()` block, unless the docstring says "caller owns the transaction".
- **Time.** Every function that needs time takes `now: float` (Unix seconds). Nothing calls `time.time()` below the CLI or HTTP layer.
- **Errors.** Raise the types in `instance/errors.py`: `ValidationError` 400, `NotFound` 404, `Conflict` 409, `Gone` 410, `LeaseHeld` 423, `WritesPaused` 507. Never raise bare `ValueError` across a module boundary.
- **Return values** are JSON-serialisable `dict`/`list`, with no NaN or Infinity. They are printed verbatim by the CLI and returned verbatim by the console API.
- **Randomness.** A function that generates data takes `seed: int`. Results are deterministic for a given seed.
- **CLI.** Each workstream module defines `register(sub, *, with_db, run)` (see `instance/cli.py`). `with_db(handler)` wraps `handler(db, args, now)`. Every command takes `--db` via `store.add_db_argument`.

## 1. Store (owner: architect — `store.py`, `schema.py`, `errors.py`, `rules.py`, `cli.py`)

```python
store.create(path, now, *, action_semantics=None, config=None, founding_graph: bytes|None=None, note=None) -> Connection
store.open_existing(path) -> Connection              # migrates; NotFound if absent or not an instance
store.connect(path) -> Connection
store.write(db) -> context manager                  # BEGIN IMMEDIATE / COMMIT / ROLLBACK
store.bump_state_version(db) -> int ; store.state_version(db) -> int
store.bump(db, name, amount=1) ; store.counter(db, name) -> int
store.config(db) -> InstanceConfig(evidence_ttl_seconds, max_evidence_rows, min_free_bytes, ask_budget_per_epoch,
                                   ask_budget_window_seconds, consolidation, max_consolidated_rows,
                                   max_thermal_celsius)
store.set_config(db, changes: dict, now=None) -> {"config": dict, "changed": [names]}   # owner-settable settings only
#   (ask_budget_per_epoch, ask_budget_window_seconds, consolidation, max_consolidated_rows); ValidationError
#   otherwise. `nsf config` is its CLI.
#   Each real change adds a deletion_log row: target_kind "setting", target_id the name, effects {"from", "to",
#   "state_version"} (from a restore: {"from", "to", "carried_by_restore": true}).
#
# Consolidation (ADR-044, schema 14). Additive fields, each absent while zero unless noted:
#   beliefs.compute()["evidence"]["consolidated"]      learning readings that are consolidated (counted in "learning"
#                                                      and "total"). A consolidated reading that is a later copy of a
#                                                      sample is in "total" and "duplicates_ignored", like a journal copy
#   beliefs.explain() factors: "supporting_consolidated", "counterevidence_consolidated" (counted in *_total; the
#                                                      event lists hold raw events only)
#   governance.sweep() -> "consolidated"               readings kept by this sweep (one per expired 'learn' event)
#   governance.sweep() -> "not_consolidated"           'learn' events of this sweep that left nothing because the
#                                                      limit on readings was reached (the oldest by seq are kept)
#   InstanceConfig.max_consolidated_rows               the limit: int >= 0, default 1,000,000, 0 = none. Never deletes
#                                                      a reading. A saved value that is not such an int reads as the
#                                                      default and is listed by store.setting_problems()
#   store.consolidation_room(db) -> int | None         readings the limit still has room for (None: no limit)
#   store.consolidation_warnings(db) -> [str]          at the limit, and from 80 % of it; [] otherwise, without a limit
#                                                      and while consolidation is off. The same sentences appear as
#                                                      "warnings" in: store.regulation(), store.inventory() (top
#                                                      level), `nsf config`, and a sweep that kept or turned away a
#                                                      reading (beside the setting problems); absent while empty
#   store.regulation(measure=True)["largest_epoch"]    {"epoch", "consolidated_readings", "compute_ms" (float | None)}:
#                                                      the epoch with the most readings and what compute() costs
#                                                      there now; absent while nothing is consolidated or unless
#                                                      asked (`nsf regulation`; console GET /api/inventory?measure=1,
#                                                      which the overview sends and the header does not)
#   forget results, effects["consolidated_deleted"]    consolidated readings a forget removed ("deleted" counts events)
#   governance.forget_consolidated(db, seq: int, now)  one reading by the seq it is listed under; deletion_log kind
#                                                      "consolidated", target the seq; 'absent' when no reading has it
#   governance.forget_camera(db, root: str, now)       every camera event and reading of the root, in every epoch, and
#                                                      its changepoints; deletion_log kind "camera", target
#                                                      "camera:#<digest>"; effects["derived_deleted"]["changepoints"]
#   console POST /api/forget {"kind": "consolidated", "target": SEQ (integer)} | {"kind": "camera", "target": ROOT}
#   deletion_log effects of a sweep: "consolidated", "not_consolidated"; its belief revision summary:
#       "consolidated_now", and "consolidation_limit_reached": true when the sweep turned an event away
#   store.inventory()["memory"]["consolidated_readings"], store.regulation()["consolidated_readings"]  (always present)
#   checkpoints.restore() -> "evidence_consolidated", "consolidated_removed", "settings_carried" {name: {"from",
#       "to"}}, "settings_warnings" [str], "scope_changes_removed" int (always present; the settings an owner may
#       change are the live file's in the copy when valid; changepoints of a camera or partner forgotten since go).
#       The live file's readings are read for the seqs of the copy's journal only (checkpoints._forgets(db, now,
#       wanted)); the result is the same as reading all of them
#   store.valid_setting(name, value) -> bool ; store.setting_problems(db) -> [str]  (saved values set_config would
#       refuse; such a consolidation value reads as off). governance.sweep() and `nsf config` add "warnings" then.
#   governance.forget_consolidated / forget_dataset: an id beyond SQLite's integers is a ValidationError
#   governance.forget_partner also deletes the partner's changepoints (effects["derived_deleted"]["changepoints"])
#   executive.decide() -> "warnings": [str]             only when the saved ask-budget window was unusable (the default
#                                                      window is then used)
#   memory.retrieve(db, "consolidated", id=SEQ, epoch=, channel=, root=) -> records with payload
#       {"seq", "channel", "reading", "sample_id"}, permissions.retention "consolidated"
#   governance.held_roots(db) -> camera and word roots held as events or consolidated readings
store.birth(db) -> dict ; store.inventory(db) -> dict ; store.regulation(db, path=None, *, measure=False) -> dict
store.require_write_allowed(db, path=None)          # raises WritesPaused
rules.register(db, definition, now, *, created_by)   # caller owns txn; idempotent on identical content
rules.get(db, version) -> dict ; rules.latest(db) -> str ; rules.listing(db) -> list
```

Counter names (`store.bump`): `state_version`, `evidence_written`, `repeat_views` (duplicate ingests), `teacher_interventions` (owner resolved a pending question), `replay_updates` (learn/recall/evaluate runs over stored datasets), `encoder_exposure:<channel>`.

## 2. Evidence, beliefs, executive (owner: W2-A)

Files: `evidence.py`, `beliefs.py`, `executive.py`, `legacy.py`, `cli_evidence.py`.

```python
evidence.start_epoch(db, name, now, note=None) -> {"epoch": name, "created": bool}
evidence.observe(db, obs: dict, now, *, path=None) -> {"status": "created"|"duplicate", "seq": int, "state_version": int,
                                                        "belief_revision": int|None}
#   obs keys: id, epoch, channel ('camera'|'word'|'reference'), action (0|1), bit (0|1), root,
#             optional: sample_id, actor='owner', consent='learn'|'memory_only', provenance='observed'|'simulated',
#                       acquired_at (float)
#   Unknown keys -> ValidationError. Unknown epoch -> NotFound. Tombstoned id -> Gone.
#   Same id + identical payload -> "duplicate" (bumps repeat_views, never extends expiry).
#   Same id + different payload -> Conflict. Regulation -> WritesPaused (checked before insert).
#   expires_at = now + config.evidence_ttl_seconds.
#   After insert: recompute beliefs; if MAP state or identifiability changed, insert belief_revisions(cause='evidence:<id>').
evidence.list_evidence(db, epoch, *, channel=None, root=None, limit=500) -> list[dict]

beliefs.compute(db, epoch) -> dict
#   Exact inference over active evidence with consent='learn'. Copies sharing (channel, root, sample_id) count once.
#   Hidden bits: mechanism w; one alignment bit s_r per camera root r; one convention bit t_p per word partner p (root);
#   references grouped by calibration root with lab.source_groups.group_log_likelihood(group, .15, .10).
#   Camera reading predicts (bit XOR action) == w XOR s_r with error .10; word: == w XOR t_p with error .10.
#   Returns {"epoch","state_version","evidence":{"total","learning","memory_only","duplicates_ignored"},
#            "mechanism":{"p_inverted","identifiable"}, "sensors":{root:{"p_inverted","n"}},
#            "partners":{root:{"p_inverted","n"}}, "reference_roots":int,
#            "unidentifiable":[{"scope","why"}], "legacy": {...}|None}
#   "legacy" (only with <=1 camera root and <=1 partner) reproduces legacy_live.status() keys and values
#   (probability_inverted, state_probabilities_in_binary_order, top_state_probability,
#    suggested_next_observation, stored_observations, reference_roots) to 1e-12.
beliefs.detect_change(db, epoch) -> {"suspected": bool, "scope": str|None, "since_seq": int|None,
                                     "llr": float, "alternatives": [{"scope","llr"}], "ambiguous": bool}
#   Single-factor changepoint test over the last 64 learning events. Scopes: "mechanism", "camera:<root>",
#   "partner:<root>". A change flips that hidden bit for events after the changepoint. Score is the
#   log-likelihood gain over no change minus log(number of changepoints tried). Suspected when the best
#   gain > log(19). Ambiguous when the runner-up is within 1.0 nat.
beliefs.explain(db, epoch) -> {"beliefs": compute(), "factors": [{"scope","p_inverted","supporting":[event],
                               "counterevidence":[event],"uncertainty":str}], "revisions":[...last 20...],
                               "change": detect_change()}

executive.decide(db, epoch, now, *, record=False, path=None) -> {"decision": "act"|"observe"|"ask"|"test_reference"|"defer",
                                                 "scope": str|None, "reason": str, "pending_id": int|None,
                                                 "asks_used": int, "ask_budget": int,
                                                 "ask_budget_window_seconds": float}
#   Policy, first match wins:
#   1 writes paused -> defer
#   2 change suspected and not ambiguous -> ask (kind 'calibrate', scope = changed scope)
#   3 change ambiguous -> test_reference if the epoch has any reference evidence, else ask
#   4 the epoch has no learning evidence yet -> observe (act and record camera/word reports)
#   5 mechanism not identifiable (|p-.5| < .37) -> test_reference
#   6 a partner with >= 4 learning words still has |p-.5| < .37 -> ask (meaning still ambiguous for that partner)
#   7 otherwise -> act
#   An 'ask' beyond config.ask_budget_per_epoch becomes 'defer'. asks_used counts the ask/calibrate pending rows
#   created in the epoch within the last config.ask_budget_window_seconds (default 7 days), answered or not; with a
#   window of 0 it counts every such row of the epoch (the rule up to 0.7.0: the budget never refills).
#   With record=True: insert into decisions; for ask/test_reference/calibrate create a pending row unless
#   an identical unresolved one (same epoch, kind, scope) exists.
#   Also with record=True: unresolved ask/test_reference/calibrate rows of the epoch whose (kind, scope) differs from
#   the policy's current recommendation are closed ('resolved by evidence: ...'); ids returned as "closed_pending".
executive.pending(db, *, epoch=None, include_resolved=False) -> list[dict]
executive.resolve(db, pending_id, resolution: str, now) -> dict    # bumps teacher_interventions
legacy.import_legacy(db, legacy_path, now) -> {"epochs": int, "evidence": int}   # prototype live.py DB, provenance kept 'observed'
```

CLI (W2-A): `nsf epoch start NAME`, `nsf observe EPOCH ID CHANNEL ACTION BIT ROOT [--sample-id] [--actor] [--memory-only] [--simulated] [--acquired-at]`, `nsf status EPOCH`, `nsf decide EPOCH [--record]`, `nsf explain EPOCH`, `nsf pending [--epoch] [--all]`, `nsf resolve ID TEXT`, `nsf import-legacy PATH`.

## 3. Graphs, datasets, recall (owner: W2-B)

Files: `datasets.py`, `graphs.py`, `learner.py`, `recall.py`, `cli_graphs.py`.

```python
datasets.add(db, context, split, states: ndarray[T,N], targets: ndarray[T,N], provenance, now, note=None) -> int
#   float64 finite, 1 <= N <= 1024, 8 <= T <= 200_000; stored as np.savez bytes (allow_pickle=False); sha256 of blob.
#   A shape whose two arrays would not fit one SQLite blob (16 bytes per cell, 1e9 bytes less 4 kB of framing) is
#   refused with ValidationError naming how many time steps fit, before the finite check and the encoding.
datasets.load(db, dataset_id) -> (states, targets)          # Gone if deleted
datasets.simulate(db, context, now, *, seed, world: 'a'|'b'='a', steps=1536) -> {"train": id, "validation": id, "heldout": id}
#   Uses lab.graph_bench.world/stream (24 populations, 3 inputs each), provenance 'simulated'.
#   Validation 256 steps, heldout 512 steps, each from an independent seeded stream of the same world.
datasets.listing(db, context=None) -> list[dict]

learner.fit(states, targets, *, k=3, seed=0) -> {"dense": ndarray, "sparse": ndarray, "support": bool ndarray, "swaps": int}
#   The windowed learner from lab.graph_bench.simulate (window 32, lr .28, score EMA .85/.15, swap check every
#   4 windows with |score| > 1.15|w| + .035), generalised to N populations and one stream. The swap check runs for
#   all receivers at once and gives the lab loop's result bit for bit (tests/instance/test_learner.py); the window
#   update is the lab's, so a fit costs about 2.5 to 4 times a dense-only one (the second matrix and its score).
#   Range: the dense matrix is updated from every input with a step normalised per input, so its error grows with
#   the population count: on the lab's stream it is twice the noise floor at 96 populations, and at 160 the
#   snapshot cut from it is no better than a graph with no edges (far worse beyond). The sparse matrix, on k inputs,
#   stays at the noise floor up to the 1024 populations a dataset may have (scripts/bench_graph_learner.py sizes).
learner.pack(dense, validation, rule, sparse=None) -> {"blob": bytes, "bits", "degree", "byte_size", "validation_mse", "dense_validation_mse", "candidates"}
#   lab.graph_compression_bench selection: FP32 plus degrees x bits from the rule. Keep candidates with
#   val MSE <= (1 + validation_tolerance) * dense val MSE, then take the smallest complete file.
#   Which graph is packed (ADR-045). Under gate-v1 (and any rule without the two optional parts below) only the
#   dense fit is packed; `sparse` is ignored and the result is what it always was. A rule may carry:
#     "pack_candidates": {..., "learned_sparse": true}   the sparse learner's matrix is a candidate too, as FP32
#                                                        and at its own degree with each bit width of the rule
#     "pack_reference": "best_learner"                   the tolerance is measured against the lower validation MSE
#                                                        of the two learners' FP32 graphs (needs learned_sparse)
#   With learned_sparse, files of equal size are separated by the lower validation MSE and the result adds
#   "learner" ('dense'|'sparse'), "reference", "reference_validation_mse" and a "learner" per candidate.
#   store.BEST_LEARNER_V1 ('best-learner-v1') is gate-v1 with both parts, the same thresholds and byte budget.
#   store.BIRTH_RULES = (GATE_V1, BEST_LEARNER_V1): a new individual is born with both, registered in that order,
#   and learns under the last. store.create(..., birth_rules=None) takes another tuple (for example (GATE_V1,) to
#   reproduce an individual born earlier). An individual born earlier keeps the rules it has; its owner adopts the
#   rule with `nsf graph rules add --builtin best-learner-v1 --owner-approved` and goes back with
#   `nsf graph rules add --builtin gate-v1 --as gate-v1-again --owner-approved` (rules are immutable and the newest
#   is used). Candidates keep the rule they were learned under. With 512 or more populations the sparse file is over
#   the 4096-byte budget of both rules, so nothing is promoted there, as before.
#   Measured: docs/quality/evidence/accel_decisions_graph_learner_*.json, accel_decisions_graph_birth_rule.json.

graphs.register_founding(db, nsg_blob, now) -> int           # caller owns txn? No: opens its own write(); status 'active', context 'founding'
graphs.learn(db, context, now, *, train: int, validation: int, holder: str) -> int   # candidate revision id; takes the context lease
#   metrics: validation_mse, dense_validation_mse, pack (the candidates and the choice), sparse (the sparse learner's
#   swaps, edges, validation_mse and how much of its support the snapshot shares), and no_graph_validation_mse with
#   beats_no_graph: the error of a graph with no edges on the same validation data, and whether the snapshot is
#   below it. A snapshot that is not has learned nothing; under gate-v1 that happens with many populations (above),
#   and the gate rejects it (check (f) below).
graphs.evaluate(db, revision_id, now) -> dict                # metrics on held-out sets not in its lineage; stored in metrics
graphs.promote(db, revision_id, now) -> {"promoted": bool, "decision": {...all numbers and the rule version...}}
#   Gate (rule recorded on the candidate; decision["order"] lists the checks applied, graphs.CHECKS all six):
#   (a) >=1 heldout dataset of the context not in its training lineage, else reject
#   (b) incumbent present: candidate heldout MSE < incumbent heldout MSE * (1 - min_heldout_gain)
#   (c) retention: on every older heldout dataset of the context, candidate MSE <= incumbent MSE * (1 + retention_tolerance);
#       failing (c) while passing (b) adds "recommendation": "register as a new context; the incumbent still solves older data"
#   (d) byte_size <= byte_budget
#   (e) lineage_intact: no dataset the candidate was trained or validated on has been deleted
#   (f) beats_no_graph: metrics.validation_mse < metrics.no_graph_validation_mse. Not part of any rule and waived by
#       none: a snapshot no better than a graph with no edges is rejected, with "why" naming both errors. Applied only
#       when learn() recorded metrics.beats_no_graph; a candidate learned before that has checks (a)-(e) and the
#       decision record it always had.
#   Pass: incumbent -> 'archived', candidate -> 'active'. Fail: candidate -> 'rejected'. Decision JSON stored either way.
graphs.reject(db, revision_id, now, reason) -> dict
graphs.rollback(db, context, revision_id, now) -> dict       # re-activate an archived, non-invalidated revision
graphs.invalidate_dependents(db, dataset_id, now) -> {"invalidated": [ids], "reactivated": id|None, "failure": str|None}
#   Caller (governance) owns the transaction. Revisions trained or validated on the dataset -> 'invalidated'.
#   If the active one was invalidated, re-activate the newest archived revision of that context whose lineage has no
#   deleted dataset; if none, failure = "no eligible snapshot for context <c>".
graphs.lesion(db, revision_id, dataset_id, now) -> {"edges":[{"src","dst","delta_mse"}], "permuted_control_mse", "base_mse"}
#   The change in MSE from zeroing each retained edge, stored in graph_effects(kind='lesion'). Zeroing one edge
#   changes only its receiver's residual, so each delta is taken from its closed form (the mean of the per-step
#   change); the full MSE is evaluated twice (base, permuted control), not once per edge. A test compares every
#   edge with the full recomputation.
graphs.listing(db, context=None) -> list[dict] ; graphs.show(db, revision_id) -> dict (includes the decoded matrix as nested lists)
graphs.lease(db, context, holder, now, ttl=600) -> context manager   # LeaseHeld if another holder has an unexpired lease

recall.run(db, dataset_id, now, *, contexts=None, record=True) -> dict
#   Active revisions of all contexts (or a subset) compete through lab.context_recall.GraphRecall
#   (history = rule.recall_history). The prediction for each step is committed before its outcome.
#   A step is 'unknown' when the chosen graph's rolling mean error > rule.unknown_context_ratio * its validation MSE.
#   Returns {"steps","selected_mse","latest_only_mse","choices":[{"context","from","to"}],
#            "unknown_fraction","verdict": "known:<context>"|"unknown_context"}.
#   An 'unknown_context' verdict with record=True creates pending(kind='new_context'). Never creates a graph itself.
```

CLI (W2-B): `nsf graph simulate CONTEXT --seed N [--world a|b]`, `nsf graph datasets [--context]`, `nsf graph learn CONTEXT --train ID --validation ID`, `nsf graph evaluate REV`, `nsf graph promote REV`, `nsf graph reject REV --reason`, `nsf graph rollback CONTEXT REV`, `nsf graph list [--context]`, `nsf graph show REV`, `nsf graph lesion REV DATASET`, `nsf graph recall DATASET [--context ...]`, `nsf graph export REV OUT.nsg`, `nsf graph rules list`, `nsf graph rules add FILE.json --owner-approved` (the flag is required), `nsf graph rules add --builtin gate-v1|best-learner-v1 [--as VERSION] --owner-approved` (a rule that ships with the software, no file; `--as` registers it under another version name, needed when it is registered already and a newer rule is in use).

## 4. Memory, governance, checkpoints (owner: W2-C)

Files: `memory.py`, `governance.py`, `checkpoints.py`, `cli_memory.py`.

```python
memory.retrieve(db, kind, *, id=None, epoch=None, context=None, channel=None, root=None,
                since=None, until=None, limit=100) -> {"state_version": int, "items": [record]}
#   kind in evidence|belief|graph|dataset|checkpoint|deletion|decision|pending.
#   Every record has: kind, id, scope {epoch|context, root}, time, permissions {consent, retention, expires_at}
#   (None where not applicable), confidence (evidence: null; belief: top state probability; graph: heldout MSE),
#   outcome_history (graph: decision list; others: []), payload.
#   Association/embedding retrieval is out of scope (see REQUIREMENTS_MATRIX MEM-06).

governance.forget_evidence(db, event_id, now, *, reason='forgotten') -> {"status": "forgotten"|"absent", "effects": {...}}
#   Delete the row, tombstone (id, seq, epoch, deleted_at, reason) with no payload, mark checkpoints whose
#   max_evidence_seq >= seq as 'stale', insert belief_revisions(cause='forget:<id>'), close pending rows of that
#   epoch whose premise changed (resolution 'superseded by deletion'), write deletion_log. Idempotent.
governance.forget_epoch(db, epoch, now) -> dict           # every evidence row in the epoch (reason 'epoch_forgotten')
#   Also deletes the epoch's derived rows (governance.drop_epoch_rows: belief_revisions, decisions, pending,
#   changepoints, epoch links from or to it, its graph context and streams) and clears the owner's note; the epochs
#   row stays. An epoch holding only a note is forgotten and logged too. Every checkpoint is marked stale whenever
#   derived rows were removed, also when the epoch's evidence had already expired.
governance.forget_partner(db, root, now) -> dict          # every word evidence row from that partner
governance.forget_dataset(db, dataset_id, now) -> dict    # blob NULL, deleted_at set, graphs.invalidate_dependents, stale checkpoints
#   Also deletes the graph_effects rows measured on the dataset (lesion, lesion_control, coactivation):
#   effects["graph_effects_deleted"]. Effects measured on other datasets stay.
governance.promote(db, event_id, now) -> dict             # retention 'promoted', expires_at NULL
governance.sweep(db, now) -> {"expired": int}             # expire evidence with expires_at <= now (reason 'expired')

checkpoints.create(db, db_path, out_dir, now, *, policy='cold') -> dict    # archive.convert('checkpoint'); file 0600; registry row
checkpoints.verify(db, checkpoint_id, now) -> dict        # full decode to a temp file, footer hash; never publishes output
checkpoints.restore(db, checkpoint_id, dest, now, *, apply_tombstones=True) -> dict
#   archive.convert('unpack'); refuses an existing dest; then deletes from the restored copy every evidence id that is
#   tombstoned in the current instance, so forgotten ids are never resurrected by a restore.
#   The replay also applies forgotten epochs (drop_epoch_rows, read from deletion_log with the state version each
#   forget committed at, and only to copies older than that forget, so an epoch name used again keeps its new rows)
#   and forgotten datasets with the graph effects measured on them, and rewrites revision causes naming a
#   tombstoned event id to 'evidence:#forgotten'. Result adds epoch_rows_removed and graph_effects_removed.
checkpoints.rebuild_stale(db, db_path, out_dir, now) -> dict   # new checkpoint, then unlink stale files ('deleted'); record failures
checkpoints.listing(db) -> list
```

CLI (W2-C): `nsf memory KIND [filters]`, `nsf forget evidence ID | epoch NAME | partner ROOT | dataset ID`, `nsf promote ID`, `nsf sweep`, `nsf checkpoint create [--dir] [--policy] | list | verify ID | restore ID DEST | rebuild [--dir]`.

## 5. Pocket bridge HTTP API (owner: W2-D; prototype contract preserved)

All routes are `POST /api/<route>`. Requests must be `Content-Type: application/json`, between 2 and 512 bytes (`research_submit`: up to 1024, so 300 non-Latin characters fit), and carry the same `Origin` if any. Everything except `pair` needs `Authorization: Bearer <credential>`. Responses are `no-store` and carry the CSP shown in `pocket/server.py`.

| Route | Body | Success | Errors |
|---|---|---|---|
| pair | `{"code"}` | 201 `{"key"}` | 403 |
| save | `{"id","channel","value","learn"}` | 201 new, 200 identical retry | 400, 403, 409 changed retry, 410 forgotten or expired, 429 > 128 |
| forget | `{"id"}` | 200 | 400, 403 |
| state | `{}` | 200 `{"records","prediction"}` | 403 |
| episode | `{"id","features","outcome","learn"}` | 201, 200 | 400, 403, 409, 410, 429 > 64 |
| episodes | `{}` | 200 `{"episodes"}` (≤ 256, group) | 403 |
| forget_episode | `{"id"}` | 200 | 400, 403 |
| link | `{}` | 201 `{"code"}` (5 min, one use) | 403 |
| unpair | `{}` | 200 | 403 |

Wave 2 additions (W2-D, evening review):

| Route | Body | Success | Errors |
|---|---|---|---|
| mute | `{"channel"}` | 200 `{"unlearned": n}`. Existing events in that channel get learn=false, and new `learn:true` saves on it return 423. | 400, 403 |
| unmute | `{"channel"}` | 200 | 400, 403 |
| state (extended) | `{}` | adds `"muted": [channel…]` | |

## 6. Console HTTP API (owner: W3-E)

Loopback only (`127.0.0.1`), started with `nsf console --db PATH [--port 8766]`. Startup prints a one-time URL with the session token in the fragment (`http://127.0.0.1:8766/#token=…`). The page keeps the token in memory and sends it as `Authorization: Bearer`. Requests must also pass a `Host` check (loopback names only) and an `Origin` check. GET routes are read-only. POST routes mutate. Bodies are JSON of at most 4 KiB.

| Method | Route | Service call |
|---|---|---|
| GET | /api/inventory | store.inventory + store.regulation |
| GET | /api/epochs | epochs plus evidence counts |
| GET | /api/epochs/{e}/beliefs | beliefs.explain |
| GET | /api/epochs/{e}/decision | executive.decide(record=False) |
| GET | /api/epochs/{e}/evidence | evidence.list_evidence |
| GET | /api/pending | executive.pending |
| GET | /api/graphs | graphs.listing ; /api/graphs/{id} → graphs.show |
| GET | /api/datasets | datasets.listing |
| GET | /api/checkpoints | checkpoints.listing |
| GET | /api/memory?kind=… | memory.retrieve |
| POST | /api/epochs | evidence.start_epoch `{"name","note"?}` |
| POST | /api/observe | evidence.observe (body = obs) |
| POST | /api/epochs/{e}/decide | executive.decide(record=True) |
| POST | /api/pending/{id}/resolve | executive.resolve `{"resolution"}` |
| POST | /api/forget | governance.forget_* `{"kind":"evidence"\|"consolidated"\|"epoch"\|"partner"\|"camera"\|"dataset","target"}` (`target` is an integer for `dataset` and `consolidated`, a string otherwise) |
| POST | /api/promote | governance.promote `{"id"}` |
| POST | /api/graphs/{id}/promote, /reject | graphs.promote / graphs.reject |
| POST | /api/checkpoints | checkpoints.create `{"policy"}` ; /api/checkpoints/{id}/verify |

Errors return `{"error": message}` with the status from `errors.py`.

**Additional routes (W3-E):**
- `POST /api/datasets/simulate {"context","seed","world"?}` → datasets.simulate.
- `POST /api/graphs/learn {"context","train","validation"}` → graphs.learn(holder="console"), returns `{"revision_id"}`.
- `POST /api/graphs/{id}/evaluate {}` → graphs.evaluate.
- `POST /api/recall {"dataset_id","contexts"?}` → recall.run(record=True).
- `GET /api/epochs/{e}/explain` is an alias of `/beliefs`.

**Response shapes:**
- `GET /api/inventory` returns store.inventory plus a `regulation` key.
- `GET /api/epochs` returns `[{name, created_at, note, evidence, learning, memory_only, promoted, forgotten}]`.

**Request rules:**
- Unknown body or query keys → 400.
- Body over 4096 bytes → 413.
- Wrong method → 405 with an `Allow` header.
- Bad Host, Origin or token → 403.

**Startup and storage:**
- `nsf console --url-file PATH` writes the URL to a 0600 file instead of stdout (atomically replacing a stale console URL file it owns) and removes it at shutdown. GET routes are read-only except for a non-blocking expiry sweep at most once per 60 s; a locked instance returns 503 with Retry-After.
- Checkpoints are written to `checkpoints.default_dir(db)` = `<db parent>/<stem>.checkpoints`.

**CLI-only operations (not in the console):** graph rollback, lesion and export; rule management; checkpoint restore and rebuild; legacy import.


## 7. Additions from Wave 5 remediation (binding)

**Schema.** Migrations 2–4 add:
- `forgotten_roots`;
- an insert trigger on `scoring_rules`;
- `changepoints`;
- the `evidence_tally` index;
- `epoch_links`.

Connections set `journal_size_limit=0` and `recursive_triggers=ON`.

**store**
- `truncate_wal(db) -> "ok"|"busy"`
- `InstanceConfig.max_thermal_celsius` (default None)

**evidence and beliefs**
- `compute()` adds:
  - `segments`, `confirmed_changes`, `change` and `linked_from`;
  - per factor: `suspected_change`, `llr`, `suspected_since_seq` and `ambiguous`.
- `detect_change()` adds `suppressed`.
- `beliefs.match_epochs(db, epoch, *, last=12)` → `{ranking:[{epoch, segments, log_likelihood, posterior, events}], best, best_posterior, new_setup_posterior}`.

**executive**
- `resolve(db, pending_id, resolution, now, *, confirm=None)`. `confirm` is valid on `calibrate` questions (records a changepoint) and on `epoch:<E>` questions (records or declines a link).
- `decide()` returns `closed_pending` and `expired_pending`.
- Rule 4b: ask "This looks like <E> …; continue …?" once per (epoch, candidate) when the posterior is ≥ 0.75.
- A question the owner answered is not re-asked until 3 new events arrive in its scope.
- `act` is withheld while an unanswered, non-ambiguous change suspicion is open.

**graphs**
- The gate gains a 5th check, `lineage_intact`.
- `functional_map(db, dataset_id, now, *, lags=(1,2,3), top=20)`.
- Lesion edges carry `se`; the output carries `task`.
- `export` creates its file with 0600 and `O_EXCL`.
- `invalidate_dependents` skips revisions replaced by an owner rollback.

**governance**
- Forgetting no longer closes pending rows; the executive closes them when evidence removes their premise.
- When a root's last evidence goes, it is scrubbed to a salted digest (ADR-019).
- Effects add `roots_forgotten`, `derived_rows_scrubbed`, `wal_checkpoint` and `linked_epochs_revised`.

**checkpoints**
- New: `default_dir(db_path)` and `prune(db, now, keep=7)`.
- `rebuild_stale(..., keep=7)` returns `pruned`.
- `create` may raise `WritesPaused`, and may register as `stale` with a `warning` if a deletion committed during packing.
- `restore` returns `graphs_invalidated` and `derived_rows_scrubbed`.
- 0.7: `rewrite(db, id, now)` rewrites one stale archive in place without what was forgotten since. It applies the
  replay `restore` applies, plus VACUUM INTO, integrity check, re-pack with the archive's policy and a decode check.
  A journal and an atomic rename make it crash-safe; `_recover` settles a killed run at the next verify, restore or
  sweep. It returns {id, path, old_sha256, new_sha256, old_bytes, bytes, removed, seconds}. It raises `Conflict`
  for a hash mismatch, or when `rewritable()` refuses: only archives made stale solely by `voice_session`,
  `research`, `knowledge` or `source` forgets qualify. `rewrite_stale(db, now, ids=None)` returns {rewritten,
  not_rewritten: [{id, reason}]}. `after_forget(db, now)` returns {checkpoints_rewritten, checkpoints_not_rewritten?,
  checkpoints_stale}.
- 0.7: `transcripts.forget_session(..., rewrite=True)` and `nsf forget evidence|epoch|partner|dataset|research|
  knowledge|source|voice` merge `after_forget` into their result. `next_steps` stays only while archives are still
  stale. `VoiceHub.forget` rewrites in the background and returns `checkpoints_rewriting` [ids].
  `nsf checkpoint rewrite [--id N ...]` prints `rewrite_stale`.

**datasets and legacy** gain `path=` for free-space regulation.

**CLI.** New commands and flags:
- `nsf epoch match`
- `nsf resolve ID TEXT [--confirm|--reject]`
- `nsf graph coactivation DATASET`
- `nsf checkpoint prune [--keep]` and `rebuild --keep`
- `nsf init --max-evidence-rows --min-free-mib`

**Pocket**
- `--allowed-host` (Host mismatch → 421).
- HSTS when TLS is on.
- A 32-connection cap, with the TLS handshake run in the worker thread.
- A not-yet-stored id that is forgotten is retired, so a later save gets 410.
- After unmute, an identical retry of an event stored while muted returns 200.
- A SQLite error returns 503.

**Console routes**
- `POST /api/pending/{id}/resolve {"resolution","confirm"?}`
- `GET /api/epochs/{e}/match?last=N`
- `POST /api/graphs/coactivation {"dataset_id","lags"?,"top"?}`
- `POST /api/checkpoints/prune {"keep"}`
- `POST /api/checkpoints/rebuild {"keep"?,"policy"?}`

## 8. Web Cortex (0.6, binding)

Module contracts: `docs/web-cortex/ARCHITECTURE.md` §4. Every route below returns 409 with a clear message while web
access is disabled for the individual (ADR-021), except reads of stored memory.

**Console `/api/web/*`** (same token, Host, Origin and 4 KiB rules as §6; task ids are lowercase UUIDs, otherwise 400):

| Method | Route | Service call |
|---|---|---|
| GET | /api/web/status | config switches, provider health (stored, no probing), cache stats, task counts, curiosity top 5, worker state |
| POST | /api/web/config | state.set_config — only `enabled`, `autonomous`, `pocket_research`, `browser_rendering` |
| GET | /api/web/tasks?status=&limit= | orchestrator.listing |
| POST | /api/web/tasks `{objective, mode?, authorization?}` | orchestrator.submit (queued; the embedded worker runs it) |
| GET | /api/web/tasks/{id} ; /evidence | orchestrator.get ; orchestrator.evidence |
| POST | /api/web/tasks/{id}/cancel ; /resume | orchestrator.cancel ; orchestrator.requeue |
| GET | /api/web/knowledge?topic=&limit= | knowledge.query (no topic: recent items) with knowledge.feedback. With a topic the result carries `retrieval` (below) |
| POST | /api/web/knowledge/{id}/review `{accept, note?}` ; /archive ; /reject ; /refresh ; /forget | knowledge.review / archive / reject / refresh / forget |
| GET / POST | /api/web/monitors ; /api/web/monitors/{id}/pause, /resume, /stop | scheduler.monitor_* |

The console does not run research by default: queued tasks are executed by `neuroscifi-webcortex.service`
(`nsf web work`). `nsf console --web-worker` starts an embedded worker for a development console that has network
access (the console's systemd unit allows loopback only). Leases prevent double execution.

**Knowledge query result** (`knowledge.query`; `nsf web knowledge TOPIC`, the route above and the MCP
`web_knowledge` tool): `{topic, as_of, items, matched, known, retrieval}`. `retrieval` says which ranking produced
`items` (`docs/web-cortex/MEMORY_INTEGRATION.md` §5.1):

| `retrieval` | When |
|---|---|
| `{"mode": "lexical"}` | semantic retrieval is off: the BM25 ranking, as in 0.7.0 |
| `{"mode": "lexical", "reason": str}` | semantic retrieval is on and could not be used (no embedding endpoint, an unusable key file, a failed or timed-out memory-model call); `items` are the lexical result |
| `{"mode": "lexical", "reason": str, "retry_in_seconds": float}` | a memory model failed, or could not be started again, less than 30 s ago and is left alone until then |
| `{"mode": "lexical", "reason": str, "waking": ["embed" \| "rerank", ...]}` | a local memory model is unloaded while idle (or is being unloaded): this lookup is not held up, and the models named in `waking` are starting again in the background |
| `{"mode": "semantic", "ranked_by": "rerank", "candidates": int, "dropped_below_floor": int, "floor": float, "models": {"embed", "rerank"}, "cached"?: true}` | ranked by the reranker; an item's `score` is the reranker's (0 to 1); statements under the floor are not returned |
| `{"mode": "semantic", "ranked_by": "embedding", "candidates": int, "models": {"embed"}, "cached"?: true}` | no reranker configured: ranked by cosine similarity (the item's `score`), nothing cut |

`nsf web memory [status|config|clear]` prints `{enabled, settings {enabled, candidates, floor, timeout,
rerank_template}, providers {embed, rerank: {configured, endpoint?, model?, state?, reason?, key_present?}},
vectors_held_by_this_process, effect, error?}`. A provider's `state` is the gateway's health state, or `"asleep"`
when the idle policy unloaded the local service (not an outage: the next lookup starts it). There is no console
route for it. `nsf setup --json` reports a `memory` step (`done`, `already done`, `skipped` or `would do`) with
`models {embed?, rerank?}` when it configured them.

**Task events and report.** Two optional fields were added after 0.7.0; a consumer that ignores unknown keys is
unaffected.

- The `decision` event of the discovering stage (`{needs_web, reasons, knowledge?}`) may carry `memory_check`:
  `{"status": "checked", "confirmed": [knowledge ids], "refused": [knowledge ids]}` or `{"status": "skipped",
  "reason": str}`. It is absent when no check was attempted: the task already needed the web, `memory_check` is off,
  no answer model is configured or model reasoning is off. When the model confirms no statement, the same event has
  `needs_web: true` (MEMORY_INTEGRATION §5.2).
- `result.reasoning` (`docs/architecture/COGNITIVE_INFERENCE.md` §3) may carry `abstained: true`, only together
  with `status: "rejected"`: the model wrote no sentence, so none was checked or rejected, and `reason` says so.
  `rejected` stays the status of every answer in which no model sentence is shown. `result.reasoning.critique` is
  `null`, or `{status: "ok", removed, flagged}`, `{status: "skipped", reason}` or `{status: "failed", reason}`.
  When a sentence was left out of the critique because it restates its cited claims word for word, the record also
  carries `judged` and `restatements` (integers: accepted sentences shown to the critic, and those left out). When
  none was left out, for the voice and time-budget skips and when `critique_restatements` is on, the record is the
  0.7.0 one. When every accepted
  sentence is a restatement the record is `{status: "skipped", reason: "nothing to judge: …", judged: 0,
  restatements: N}` and no critique call was made.

`POST /api/web/config` accepts only the four switches listed above; `memory_check` is set with
`nsf web config --memory-check|--no-memory-check`.

**Forecasts as read (ADR-046).** Additive, nothing renamed or removed:
- Web setting `calibrated_forecasts` (boolean, default true; `nsf web config --set calibrated_forecasts=false`).
- `evolution.fit_recalibration(pairs, *, minimum, window, prior_shift, prior_tilt) -> {n, used, minimum, applied,
  shift, tilt, centre}`; `recalibrate(p, fit) -> float | None` (`p` itself while `applied` is false);
  `settled_forecasts(db) -> {knowledge|hypothesis|owner: [(forecast, 1.0|0.0), …]}`; `recalibrations(db) ->
  {kind: fit}` (every fit the identity, with `off: true`, when the setting is off); `prediction_kind(db, id)`;
  `prequential(pairs) -> {n, adjusted, brier_stated, brier_mapped}`; `recalibration_report(db)`.
- `nsf knowledge calibration` gains the key `recalibration`: `{enabled, minimum, window, kinds: {kind: {settled,
  mapping, reads: {"0.1" … "0.9"}, had_it_been_used}}, used_by, off}`. `current` (`evolution.calibration`) and
  `history` are unchanged and score the forecasts as stated.
- An `autonomy.candidates` entry of kind `hypothesis_prediction` keeps `forecast` (the stored forecast) and gains
  `forecast_as_read` when the mapping of its kind changes it; an entry of kind `prediction` gains `forecast` and
  `forecast_as_read` only in that case. `signals.prediction_uncertainty` and a hypothesis' `info_gain` are
  computed from the forecast as read. The `decision` event of a queued investigation carries both numbers.
- No table, column or stored value is added or changed.

**Pocket bridge research actions** (ADR-028). Present only when the bridge runs with `--instance-db` and the
individual has `enabled` and `pocket_research` on; otherwise 404 like any unknown route. Existing bearer, Origin,
JSON content type and 512-byte body rules apply.

| Action | Body | Result |
|---|---|---|
| research_submit | `{objective: 1–300 chars, mode?: quick_lookup\|deep_research\|fact_verification\|comparative\|scientific\|technical}` | 201 `{id}`; 400 `private_data` on the privacy screen; 429 after 10 per pairing family per hour (persisted); 507 `writes_paused` |
| research_list | `{}` | the caller's pairing family's tasks, newest first, ≤ 50 |
| research_get | `{id}` | status, stage and a phone-sized report; 404 for unknown ids and other families |
| research_cancel | `{id}` | 200; 404 as above |

`state` gains `"research": true` only when the feature is on, and `"push": true` when the bridge also runs with `--push-key`. A pairing family holds at most 6 devices (`link` → 409 `family_full`); `unpair {"family": true}` revokes the whole family.

**Pocket Web Push** (ADR-040, `pocket/push.py`). One route, `push`, gated like the research actions, and 404 also when the bridge runs without `--push-key`. Bodies may take up to 1024 bytes, because push endpoints are long.

| Body | Result |
|---|---|
| `{action: "status"}` | 200 `{key, subscribed}`: the Spark's VAPID public key (base64url, uncompressed P-256) and whether this device has a subscription |
| `{action: "subscribe", endpoint}` | 201 `{subscribed: true}`, or 200 when unchanged. The endpoint must be https on 443 of `fcm.googleapis.com`, `jmt17.google.com`, `updates.push.services.mozilla.com`, `*.push.apple.com` or `*.notify.windows.com`, else 400 `push_service`. The last device to register an endpoint holds it. A new endpoint (first or rotated) is accepted at most 5 times an hour per device, else 429 `{error: "push_limit", retry_after}` (seconds); a rotation keeps the subscription's backoff. |
| `{action: "unsubscribe"}` | 200 `{subscribed: false}` |
| `{action: "test"}` | 202 `{after}`: seconds until the push may leave (at most one a minute per device); 409 `not_subscribed`; at most 5 an hour per device, else 429 `{error: "push_limit", retry_after}` |

Every push is an empty POST to the stored endpoint, with `TTL: 43200`, `Urgency: normal` and `Authorization: vapid t=<ES256 JWT>, k=<key>`. 404 or 410 deletes the subscription (after a 120 s grace for new registrations). 429 and 5xx are retried after `Retry-After`, and every attempt counts for the one-a-minute interval. Every reply of the `push` route, like every research reply, carries `X-NSF-Spark-Signature` (ADR-042 §4).

**MCP (`nsf web mcp`, stdio JSON-RPC 2.0, protocol 2025-06-18).** Tools: `web_search`, `web_research`, `web_task`,
`web_evidence`, `web_knowledge`. Retrieved content is wrapped in `safety.untrusted` envelopes. There are no forget,
review or configuration tools.

## 9. Resource scheduling (0.7, RD-46)

`docs/architecture/RESOURCE_SCHEDULING.md`. GET /api/resources is read-only. The `nsf resources` actions
`tick`, `unload` and `load` stop and start services (opt-in idle unload, §3(c) there).

| Surface | Result |
|---|---|
| GET /api/resources[?cold_start=1] (console token rules as §6) | `resources.status`: `level` (`ok` \| `elevated` \| `critical` \| `unknown`), `reasons`, `thresholds`, `measured_at`, `memory` (/proc/meminfo bytes, PSI), `load` {`available`, `avg1`, `avg5`, `avg15`, `cpus`} (/proc/loadavg; `{available: false, reason}` when unreadable), `gpu` (nvidia-smi: `available`, `memory_used_bytes`, `memory_total_bytes`, `utilization_percent`, `memory_readable` false where it reports N/A, `temperature_c`, `power_draw_w`, `throttle` {`thermal`, `sw_thermal_slowdown`, `hw_thermal_slowdown`, `sw_power_cap`, `hw_slowdown`, `active_mask`} or null on a driver without these fields, `compute_apps` [{pid, name, memory_bytes}]), `containers` (docker stats of neuroscifi-*), `inference` (the vLLM server of the configured loopback endpoint, from its `/metrics`, cached 15 s: `{available: true, url, models, requests_running, requests_waiting, kv_cache_usage, server_started_at, since_start, interval}`; `since_start` and an available `interval` are `{requests_finished, mean_seconds {queue, prefill, decode, first_token, request}, prompt_tokens, prefix_cache_hit_share, generation_tokens, preemptions, finished_by_reason}`, `interval` also has `available` and `seconds` and is `{available: false, reason}` for the first reading of a process or after a server restart; the whole object is `{available: false, reason, url?}` when the endpoint is not configured, not on this host, unreachable or not a vLLM server), `audio_active`, `policy` {`background_research`, `browser_rendering`: {allowed, reason}, `background_model_slots`, `idle_unload` {`automatic` (some timeout set), `services` {vllm, asr, tts, searxng: {`timeout_s` (null: off), `suggested_minutes`, `loaded`, `state`, `state_by`, `last_used_at`, `idle_s`, `reload_estimate_s`}}, `critical_share`, `events` (last 20: {at, service, action, reason, by, ok, seconds, shape, told?, error?, headroom?, took_bytes?, freed_bytes?, refused?, available_bytes?, need_bytes?, floor_bytes?}), `how`}}; with `cold_start=1` also `cold_starts` [{name, started_at, ready_at, seconds}] |
| `nsf resources [--db] [--quick] [--cold-start]` | the same JSON on stdout (`--quick`: memory only, no subprocesses; `load`, `gpu`, `containers` and `inference` are then null) |
| `nsf resources idle [--set SERVICE=DURATION ...]` | show or set idle timeouts (`30m`, `900s`, `1h`, `off`; at least 1 min); prints `idle_unload` |
| `nsf resources tick` | one idle-policy pass: {`enabled`, `level`, `blockers`, `decisions` {service: {action `none` \| `unload` \| `failed`, why, idle_s?, event?}}} |
| `nsf resources unload\|load SERVICE` | the owner's manual command: {action, why, event; load: seconds, told}; exit 1 when the command failed or the load was refused (`action: "refused"`: it would leave less than the critical threshold of memory free) |

## 10. Inference gateway (after 0.7.0)

`docs/architecture/COGNITIVE_INFERENCE.md` §2 and §7. Settings are per individual and per provider (`chat`, `embed`,
`rerank`); `nsf llm …` and `GET /api/llm/status` act on `chat`, `nsf web memory config` on the other two.

| Surface | Result |
|---|---|
| `nsf llm config [flags]` | the `chat` settings without the key: the 0.7.0 fields plus `background_priority` (integer 0–100, default 0: no `priority` is sent) and `critique_voice` (boolean, default true) and `critique_restatements` (boolean, default false), `key_file_effective`, `key_present`, and `locality` when an endpoint is set. Flags `--critique-voice\|--no-critique-voice`, `--critique-restatements\|--no-critique-restatements`; `--set background_priority=N` |
| `nsf llm status [--probe]` | `{health, usage_24h, reasoning {enabled, self_critique, critique_voice, critique_restatements, background_priority (null when 0), used_for, when_unavailable}}` |
| `GET /api/llm/status[?probe=1]` (console token rules as §6) | `{health, config (as `nsf llm config`), usage_24h}` |
| `nsf llm usage [--limit N]` | `{summary, calls}`; `summary` is `null` and `calls` empty while no ledger exists |

`usage_24h` and `summary`: `{calls, ok, prompt_tokens, completion_tokens, avg_latency_ms, avg_first_token_ms,
avg_tokens_per_second, cached_prompt_tokens, cached_share, by_purpose {purpose: {calls, avg_latency_ms}}}`.
`cached_prompt_tokens` and `cached_share` are taken only over the calls whose server reported its prefix cache;
both are `null` when none did.

A ledger call row (`calls[]`, newest first): `{id, at, purpose, model, locality, stream, status, error,
prompt_tokens, completion_tokens, latency_ms, first_token_ms, attempts, tokens_per_second, task_id, provider,
priority, cached_tokens}`. `provider` is `chat`, `embed` or `rerank`; `priority` is the value sent with the request
(`null`: none); `cached_tokens` is the prompt tokens the server took from its prefix cache (`null`: not reported).
The three last fields are `null` in rows written by 0.7.0. Purposes added: `memory_gate` (the memory-answer check),
`memory_embed`, `memory_query` and `memory_rerank` (semantic retrieval). No row holds prompt, answer or memory text.

Gateway calls: `Gateway.chat(..., priority=None)` sends `priority` only for a positive integer.
`Gateway.embed(texts, *, purpose, input_type=None, timeout=None) -> list[list[float]]` (POST `/embeddings`) and
`Gateway.rerank(query, documents, *, purpose, timeout=None) -> list[float]` (POST `/rerank`) raise `InferenceError`
with the gateway's codes (`bad_response` for a malformed answer, `private_blocked` for memory text and an endpoint
off this host without `remote_private_memory`) and `ValueError` for empty input.
`reasoning.apply(..., background=False, spoken=False)`: `background` sends the configured priority with the task's
model calls; `spoken` skips the critique pass when `critique_voice` is off.
`reasoning.restates(item, bundle) -> bool`: whether an accepted sentence is one cited claim character for character
(white space and one final full stop aside) with the attribution the code appended; the critique judges only the
sentences for which it is false. It is always false for a sentence that cites more than one claim, for a claim that
holds its own "according to <source>", and for text with characters a reader does not see.
`reasoning.compose()` and `reasoning.guard()` are those of 0.7.0, with one addition that only refuses: a sentence
whose own trailing "according to <source> …" would take further words with it is composed and judged as before
(the item carries `cut: [text as written, the words]`) and, where the guard would accept it, is rejected with
`reasoning.CUT_WORDS` instead (COGNITIVE_INFERENCE §3).
