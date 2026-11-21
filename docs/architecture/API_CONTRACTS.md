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
store.config(db) -> InstanceConfig(evidence_ttl_seconds, max_evidence_rows, min_free_bytes, ask_budget_per_epoch)
store.birth(db) -> dict ; store.inventory(db) -> dict ; store.regulation(db, path=None) -> dict
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
                                                 "asks_used": int, "ask_budget": int}
#   Policy, first match wins:
#   1 writes paused -> defer
#   2 change suspected and not ambiguous -> ask (kind 'calibrate', scope = changed scope)
#   3 change ambiguous -> test_reference if the epoch has any reference evidence, else ask
#   4 the epoch has no learning evidence yet -> observe (act and record camera/word reports)
#   5 mechanism not identifiable (|p-.5| < .37) -> test_reference
#   6 a partner with >= 4 learning words still has |p-.5| < .37 -> ask (meaning still ambiguous for that partner)
#   7 otherwise -> act
#   An 'ask' beyond config.ask_budget_per_epoch (count of ask/calibrate pending rows created in the epoch) becomes
#   'defer'. With record=True: insert into decisions; for ask/test_reference/calibrate create a pending row unless
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
datasets.load(db, dataset_id) -> (states, targets)          # Gone if deleted
datasets.simulate(db, context, now, *, seed, world: 'a'|'b'='a', steps=1536) -> {"train": id, "validation": id, "heldout": id}
#   Uses lab.graph_bench.world/stream (24 populations, 3 inputs each), provenance 'simulated'.
#   Validation 256 steps, heldout 512 steps, each from an independent seeded stream of the same world.
datasets.listing(db, context=None) -> list[dict]

learner.fit(states, targets, *, k=3, seed=0) -> {"dense": ndarray, "sparse": ndarray, "support": bool ndarray, "swaps": int}
#   The windowed learner from lab.graph_bench.simulate (window 32, lr .28, score EMA .85/.15, swap check every
#   4 windows with |score| > 1.15|w| + .035), generalised to N populations and one stream.
learner.pack(dense, validation, rule) -> {"blob": bytes, "bits", "degree", "byte_size", "validation_mse", "dense_validation_mse"}
#   lab.graph_compression_bench selection: FP32 plus degrees x bits from the rule. Keep candidates with
#   val MSE <= (1 + validation_tolerance) * dense val MSE, then take the smallest complete file.

graphs.register_founding(db, nsg_blob, now) -> int           # caller owns txn? No: opens its own write(); status 'active', context 'founding'
graphs.learn(db, context, now, *, train: int, validation: int, holder: str) -> int   # candidate revision id; takes the context lease
graphs.evaluate(db, revision_id, now) -> dict                # metrics on held-out sets not in its lineage; stored in metrics
graphs.promote(db, revision_id, now) -> {"promoted": bool, "decision": {...all numbers and the rule version...}}
#   Gate (rule recorded on the candidate):
#   (a) >=1 heldout dataset of the context not in its training lineage, else reject
#   (b) incumbent present: candidate heldout MSE < incumbent heldout MSE * (1 - min_heldout_gain)
#   (c) retention: on every older heldout dataset of the context, candidate MSE <= incumbent MSE * (1 + retention_tolerance);
#       failing (c) while passing (b) adds "recommendation": "register as a new context; the incumbent still solves older data"
#   (d) byte_size <= byte_budget
#   Pass: incumbent -> 'archived', candidate -> 'active'. Fail: candidate -> 'rejected'. Decision JSON stored either way.
graphs.reject(db, revision_id, now, reason) -> dict
graphs.rollback(db, context, revision_id, now) -> dict       # re-activate an archived, non-invalidated revision
graphs.invalidate_dependents(db, dataset_id, now) -> {"invalidated": [ids], "reactivated": id|None, "failure": str|None}
#   Caller (governance) owns the transaction. Revisions trained or validated on the dataset -> 'invalidated'.
#   If the active one was invalidated, re-activate the newest archived revision of that context whose lineage has no
#   deleted dataset; if none, failure = "no eligible snapshot for context <c>".
graphs.lesion(db, revision_id, dataset_id, now) -> {"edges":[{"src","dst","delta_mse"}], "permuted_control_mse", "base_mse"}
#   Zero each retained edge in a copy, re-score, store rows in graph_effects(kind='lesion').
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

CLI (W2-B): `nsf graph simulate CONTEXT --seed N [--world a|b]`, `nsf graph datasets [--context]`, `nsf graph learn CONTEXT --train ID --validation ID`, `nsf graph evaluate REV`, `nsf graph promote REV`, `nsf graph reject REV --reason`, `nsf graph rollback CONTEXT REV`, `nsf graph list [--context]`, `nsf graph show REV`, `nsf graph lesion REV DATASET`, `nsf graph recall DATASET [--context ...]`, `nsf graph export REV OUT.nsg`, `nsf graph rules list`, `nsf graph rules add FILE.json --owner-approved` (the flag is required).

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
governance.forget_partner(db, root, now) -> dict          # every word evidence row from that partner
governance.forget_dataset(db, dataset_id, now) -> dict    # blob NULL, deleted_at set, graphs.invalidate_dependents, stale checkpoints
governance.promote(db, event_id, now) -> dict             # retention 'promoted', expires_at NULL
governance.sweep(db, now) -> {"expired": int}             # expire evidence with expires_at <= now (reason 'expired')

checkpoints.create(db, db_path, out_dir, now, *, policy='cold') -> dict    # archive.convert('checkpoint'); file 0600; registry row
checkpoints.verify(db, checkpoint_id, now) -> dict        # full decode to a temp file, footer hash; never publishes output
checkpoints.restore(db, checkpoint_id, dest, now, *, apply_tombstones=True) -> dict
#   archive.convert('unpack'); refuses an existing dest; then deletes from the restored copy every evidence id that is
#   tombstoned in the current instance, so forgotten ids are never resurrected by a restore.
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
| POST | /api/forget | governance.forget_* `{"kind":"evidence"\|"epoch"\|"partner"\|"dataset","target"}` |
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
| GET | /api/web/knowledge?topic=&limit= | knowledge.query (no topic: recent items) with knowledge.feedback |
| POST | /api/web/knowledge/{id}/review `{accept, note?}` ; /archive ; /reject ; /refresh ; /forget | knowledge.review / archive / reject / refresh / forget |
| GET / POST | /api/web/monitors ; /api/web/monitors/{id}/pause, /resume, /stop | scheduler.monitor_* |

The console does not run research by default: queued tasks are executed by `neuroscifi-webcortex.service`
(`nsf web work`). `nsf console --web-worker` starts an embedded worker for a development console that has network
access (the console's systemd unit allows loopback only). Leases prevent double execution.

**Pocket bridge research actions** (ADR-028). Present only when the bridge runs with `--instance-db` and the
individual has `enabled` and `pocket_research` on; otherwise 404 like any unknown route. Existing bearer, Origin,
JSON content type and 512-byte body rules apply.

| Action | Body | Result |
|---|---|---|
| research_submit | `{objective: 1–300 chars, mode?: quick_lookup\|deep_research\|fact_verification\|comparative\|scientific\|technical}` | 201 `{id}`; 400 `private_data` on the privacy screen; 429 after 10 per pairing family per hour (persisted); 507 `writes_paused` |
| research_list | `{}` | the caller's pairing family's tasks, newest first, ≤ 50 |
| research_get | `{id}` | status, stage and a phone-sized report; 404 for unknown ids and other families |
| research_cancel | `{id}` | 200; 404 as above |

`state` gains `"research": true` only when the feature is on. A pairing family holds at most 6 devices (`link` → 409 `family_full`); `unpair {"family": true}` revokes the whole family.

**MCP (`nsf web mcp`, stdio JSON-RPC 2.0, protocol 2025-06-18).** Tools: `web_search`, `web_research`, `web_task`,
`web_evidence`, `web_knowledge`. Retrieved content is wrapped in `safety.untrusted` envelopes. There are no forget,
review or configuration tools.
