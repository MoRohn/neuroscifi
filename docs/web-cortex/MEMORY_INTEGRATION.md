# Web Cortex: memory integration

Status: describes the code committed at `5f09ac3` (`state.py`, `consolidate.py`, `knowledge.py`, `curiosity.py`, `scheduler.py`, `instance/{schema,executive,governance,memory,store}.py`). Test names are given where a property is verified. Contracts: [ARCHITECTURE.md](ARCHITECTURE.md) §6. Decisions: ADR-019, ADR-023, ADR-026, ADR-027, ADR-029. Time rules: [TEMPORAL_KNOWLEDGE.md](TEMPORAL_KNOWLEDGE.md). Evidence rules: [EVIDENCE_AND_PROVENANCE.md](EVIDENCE_AND_PROVENANCE.md). Operation (`nsf web knowledge`, `review`, `forget`, `monitor`, `tick`, `curiosity`, `nsf resolve`, `nsf forget`): see RUNBOOK.

**No model weights are modified, ever.** The package contains no model (ADR-022). Web Cortex reads the evidence journal only to build its private-term list; it writes nothing to `evidence`, `beliefs`, `belief_revisions`, `graph_revisions` or any other binary-belief table. The consolidation test asserts that `belief_revisions` is unchanged (`test_two_independent_agreeing_sources_become_provisional_with_a_question`). Its only writes outside the `web_*` tables are the owner's questions (`pending`), the configuration (`meta`), the `deletion_log` and checkpoint status on forgetting, and the shared `teacher_interventions` and `state_version` counters.

## 1. Mapping to the memory systems

Migration 6 adds 12 tables to the individual's file (`PRAGMA user_version` 6). `webcortex/state.py` owns them; no other module creates or alters them. Because they are in the individual's file, checkpoints, forgetting, erasure and inventory cover them (ADR-029). Raw page bodies are not: they live in the separate public cache (section 6).

| Memory | Table | Content | Written by | Read by |
|---|---|---|---|---|
| Working | `web_tasks` | One research task: objective, mode, status, stage, budget, usage, checkpoint, result, error, lease, `executed_on`, origin, family digest | `orchestrator` | `orchestrator`, `scheduler`, interfaces |
| Episodic | `web_events` | One row per step of a task (RESEARCH_ORCHESTRATION §6); `task_id` is null for prediction events | `orchestrator`, `scheduler`, `predictions` | `orchestrator.get`, `evidence` |
| Provenance (raw) | `web_sources` | Each document a task read, with `content_sha256` into the public cache | `orchestrator` | `evidence`, `consolidate`, governance |
| Provenance (raw) | `web_claims` | Extracted sentences or templates; not accepted anything | `state.save_clusters` | `consolidate`, `curiosity`, `memory.retrieve` |
| Semantic and temporal | `web_knowledge` | Versioned statements with status, confidence, uncertainty, freshness class, validity interval, `superseded_by`, supporting and contradicting claim ids, sources, pending question id | `consolidate`; status changes by `knowledge`, governance | everything above it |
| Knowledge graph | `web_claim_links` | Edges between claims: `supports`, `contradicts`, `supersedes`, `duplicates` | `save_clusters`, `consolidate` | `evidence` |
| Knowledge graph | `web_entities`, `web_mentions` | Entities and where they are mentioned (section 4) | `consolidate` | governance |
| Procedural | `web_strategies` | Provider and engine health with circuit-breaker state (`registry`); per-mode outcome counts and request, page, byte totals (`mode:<mode>`) | `services.persist`, `state.record_mode_outcome` | `services.build` |
| Prediction | `web_predictions` | Expectation about a knowledge item, check time, outcome | `orchestrator`, `predictions` | `curiosity`, `scheduler` |
| Metacognition | `web_feedback` | What each investigation did to a key: effect, before and after confidence, information gain in bits | `consolidate` | `curiosity` |
| Continuous investigation | `web_monitors` | Topic, mode, interval, budget, baseline, authorising party, status | `scheduler` | `scheduler` |
| Working memory bundle | (not stored) | `synthesis.context_pack(report, max_tokens)`: highest-value items and findings under a token bound, with citations, an uncertainty line and `untrusted` envelopes | | callers that feed an agent |

## 2. Raw evidence, claims, accepted knowledge

Four stages that stay distinguishable at every point:

| Stage | Where | Trusted? | How it advances |
|---|---|---|---|
| Raw evidence | Public cache body + `web_sources` row | No: untrusted bytes and metadata | Fetch |
| Claim | `web_claims` | No: a quoted sentence or a template, with a cluster status and confidence | Extraction and scoring in the evaluating stage |
| Provisional knowledge | `web_knowledge`, status `provisional` | Candidate only; has an open owner question | Consolidation, if the bar in section 3 is met |
| Accepted knowledge | `web_knowledge`, status `accepted` | Owner-confirmed | `nsf resolve ID TEXT --confirm`, or `nsf web review` |

Nothing becomes `accepted` automatically (ADR-026). A claim that does not meet the bar remains a claim; the consolidation summary lists it under `held` with the reasons.

## 3. Consolidation (`consolidate.run`)

One call per finished task (`completed` or `partial`, not memory-answered, not cancelled), in one transaction. For each scored cluster, highest confidence first:

| Step | Rule |
|---|---|
| 1. Candidates | `state.load_clusters`; clusters ordered by confidence |
| 2. Eligibility | `contested` clusters are handled in step 4. Otherwise a cluster must have a claim with relevance above 0, must not be `insufficient`, and, if `single_source`, must come from a primary structured source (an API record or a `metadata` claim). Failing clusters are **held**, not stored |
| 3. Duplication | Look for live knowledge (provisional, accepted, stale) that already cites one of the cluster's claims, else the latest version with the same key, else the most similar key (token Jaccard at least 0.8). If values and polarity match: refresh `last_verified_at`, add the new claim ids and sources, raise confidence to the larger of the two only when new claims arrived, never duplicate; a `stale` item returns to `provisional`. Feedback `strengthened` (new claims) or `unchanged` (`test_duplicate_evidence_strengthens_and_never_duplicates`) |
| 4. Comparison with memory | Series, supersession and contradiction per TEMPORAL_KNOWLEDGE §5. Feedback `reversed` (superseded), `weakened` (new contradiction), `unchanged` (already recorded) |
| 5. Provenance and time | Supporting claim ids, sources `{url, title, domain, published_at, retrieved_at}`, freshness class from the task's assessment, validity interval |
| 6. Commit | A **new** item is written only as `provisional`, and only if the cluster has at least `auto_provisional_min_sources` independent sources (default 2), no contradiction and confidence at least `auto_provisional_min_confidence` (default 0.75). Each new item gets an owner question (scope `web:knowledge:<id>`) and a prediction |

After the loop, entities and mentions are written (section 4). The summary returned and stored on the report holds `provisional`, `strengthened`, `unchanged`, `superseded`, `contested`, `held` and `pending` (question ids), plus the number of feedback rows and entities.

`test_weak_evidence_is_held_as_claims_only` covers single source, low confidence, irrelevance and `insufficient`; `test_primary_structured_single_source_needs_the_source_threshold` shows a one-record API cluster is held at the default threshold and becomes provisional when `auto_provisional_min_sources` is set to 1.

**Practical thresholds.** With the evidence weights of EVIDENCE_AND_PROVENANCE §3, two ordinary agreeing pages give a confidence of 0.722, below the 0.75 floor. At default settings a provisional item needs three independent secondary groups (0.773) or three primary groups; two primary records give 0.737. The consolidation tests hard-code a cluster confidence of 0.85, so they exercise the rule but not this arithmetic. To accept two-source agreement, lower `auto_provisional_min_confidence` (for example to 0.72); this is an owner decision.

**Questions.** Questions live in the existing `pending` table (`kind 'ask'`, `epoch NULL`). `knowledge.open_question` keeps **one open question per subject**: a second finding about the same scope is appended to the open question as an "Update:" line. Scopes:

| Scope | Opened by | `nsf resolve --confirm` / `--reject` |
|---|---|---|
| `web:knowledge:<id>` | New provisional item; a contradiction; a failed prediction | accepted / rejected |
| `web:monitor:<id>` | A monitor proposed by a paired Pocket device | monitor becomes active (due now) / stopped |
| `web:research:<id>` | A curiosity proposal | task queued for the worker / cancelled |
| `web:contested:<task>:<n>` | A contested cluster with no matching knowledge | informational: answer without `--confirm` |

**The executive hook.** `executive.resolve` sees the `web:` prefix and calls `webcortex.knowledge.apply_review` inside its own transaction; the web decision and the closing of the question commit together or not at all (`test_resolve_confirm_accepts_and_reject_rejects_in_one_transaction`). Repeating the same answer is idempotent; the opposite answer to a closed question is a `Conflict`. Web questions do not expire after 7 days like belief questions (`scope NOT LIKE 'web:%'` in the expiry sweep; `test_informational_web_questions_take_no_confirm_and_do_not_decay`). Accepting or rejecting counts as a teacher intervention, like answering any question.

## 4. Entities

`consolidate._entities` scans every claim text of the task (up to 12 per claim) with fixed patterns: identifiers (`CVE-...`, `GHSA-...`, DOIs), capitalised multi-word names, CamelCase names, kebab or snake case package names, upper-case acronyms of 2-8 characters. **Resolution** is by `normalised = casefold(name)`, unique in `web_entities`; a repeat updates `last_seen` and adds a mention. A `web_mentions` row links entity, claim, knowledge item (when the claim's key produced one) and task.

Limits: there is no disambiguation (two things with one name are one entity), no alias merging, and no coreference. The graph supports "where was this name mentioned"; it is not an ontology.

## 5. Retrieval, inventory

`memory.retrieve(db, kind, ...)` is read-only and uses the common envelope (`kind, id, scope, time, permissions, confidence, outcome_history, payload`). Web kinds:

| Kind | Table | Filters beyond `id`, `since`, `until`, `limit` (max 1000) | Permissions |
|---|---|---|---|
| `research` | `web_tasks` | `status` | consent `web`, retention `until_forgotten` |
| `claim` | `web_claims` | `status` (the cluster status), `task`; payload has `"untrusted": true` | same; `confidence` is the cluster confidence |
| `knowledge` | `web_knowledge` | `status` | retention `versioned` |
| `source` | `web_sources` | `task` | `until_forgotten` |
| `monitor` | `web_monitors` | `status` | `until_stopped` |

Events, predictions, feedback, entities and strategies have no `retrieve` kind. `store.inventory` adds a `web` section (tasks, open tasks, sources, claims, knowledge, accepted and provisional knowledge, entities, monitors, open predictions, feedback) when migration 6 is present (`test_memory_retrieves_web_kinds_with_filters`, `test_inventory_counts_web_memory`).

## 6. Forgetting and erasure

Governance (`instance/governance.py`) removes web memory completely and records only ids.

| Call | Removes | Cascade |
|---|---|---|
| `forget_research(task_id)` | The task row, its events, sources, claims, links, mentions, feedback | Knowledge whose supporting claims are all gone is deleted with its predictions, feedback, mentions and open questions. Knowledge with other support loses the references, becomes `stale` and notes "needs re-verification". A version whose successor was deleted becomes current again, `stale`. Parent, monitor and knowledge references to the task are nulled. Claim-less entities are deleted |
| `forget_knowledge(id)` | The item and every version in its `superseded_by` chain | Its predictions, feedback, mentions and questions. Claims stay with their tasks |
| `forget_source(url or domain)` | Matching `web_sources` and the claims taken from them (a domain matches subdomains too) | The same knowledge cascade. Returns `effects["cache"]` = `{urls, domain, sha256}`, the keys the caller must drop from the public cache |

Common to all three: one transaction; every valid checkpoint is marked `stale`; `state_version` is bumped; one `deletion_log` row is written naming the target kind and id (never the objective or any text); the WAL is truncated. Questions about removed knowledge keep their row, with the text replaced by `#forgotten` and a system resolution. The same salted-digest idea as ADR-019 applies: the history keeps its shape and loses its words.

Verified: `test_forget_research_erases_the_task_and_knowledge_resting_on_it` plants a unique objective, forgets the task, and checks that the string is absent from the database file **and its WAL**, that every `web_*` table is empty, that checkpoints are `stale`, and that the deletion log holds only the task id. `test_forget_research_keeps_knowledge_with_other_support_as_stale`, `test_forget_knowledge_removes_the_whole_version_chain`, `test_forget_source_by_domain_and_url`.

**Checkpoints.** A restored checkpoint restores web knowledge and provenance but not page bodies; re-verification refetches (ADR-023). Forgetting marks existing checkpoints stale because they still contain what was removed.

**Public cache separation.** The cache holds public bytes keyed by URL and content hash, plus validators and `robots.txt`; it holds no queries, objectives or claims. Consequently `forget_research` does not touch it (the same public page may serve other tasks), and `forget_source` only *names* the cache keys to drop: the governance functions never open the cache file. In the committed code nothing calls `PublicCache.forget_url`, `forget_domain` or `prune` on the owner's behalf, so an interface lane must do so (SEARCH_AND_ACQUISITION §6). Size and age bounds: `cache_max_bytes` (512 MiB) and `cache_max_age_days` (30), applied by `prune`.

## 7. Curiosity (`curiosity.rank`; never runs research)

```
utility = expected information gain (bits) x importance x futility factor / cost
```

- **Information gain.** One more investigation is modelled as a report of reliability `r = 0.8` about a binary variable with probability `p`. `IG(p) = H(p*r + (1-p)(1-r)) - H(r)` (mutual information; `H` is binary entropy in bits). It is zero for a noise-only source however uncertain the item is: curiosity chases *resolvable* uncertainty, not irreducible noise (`test_information_gain_is_mutual_information_not_entropy`).
- **Uncontested knowledge.** `p = 1 - (1 - exp(-age/ttl)) / 2`: freshly verified is near 1 (gain near 0), at one TTL 0.68, tending to 0.5. A `stale`-status item uses 0.5. Only items **past their TTL** with gain at least 0.02 bits are candidates (kind `stale`); everything else is listed as excluded with the reason ("not due", or "expected gain ... < 0.02") to avoid unnecessary searches (`test_stale_contested_beats_stale_corroborated_and_fresh_is_excluded`).
- **Contested knowledge.** Four hypotheses (A holds, B holds, both held at different times, different scope) with a prior from the support on each side; the gain is the mutual information of a symmetric four-way report. Each candidate lists a distinguishing query per hypothesis. Contested subjects that never became knowledge (open `web:contested:` questions) are candidates of kind `hypothesis`.
- **Other candidates.** `prediction` (due and unsettled; uses the item's own `p`) and `gap` (a single-source claim from the last 7 days whose key no knowledge row covers; `p` is its confidence).
- **Importance.** accepted 1.0, provisional 0.7, stale 0.7, held 0.4, knowledge produced by an owner monitor 1.2; x1.5 if a prediction about the item failed in the last 30 days.
- **Cost.** `max_requests` of the mode budget the investigation would use, relative to `fact_verification` (40): 1.0 for refreshes, 0.75 for `gap_resolution`.
- **Noisy-subject damping.** If the last 2 `web_feedback` rows for a key are `unchanged` with gain under 0.01 bits, the expected gain is multiplied by 0.25, and by 0.25 again for each further futile run: a subject whose disagreement persists however often it is read stops dominating (`test_irreducibly_noisy_subject_stops_dominating`).
- **Exclusion threshold.** 0.02 bits. Output is sorted by utility, then the excluded candidates with `excluded: true` and the reason.

`curiosity.propose(candidate)` creates a *proposal*: a task queued with stage `proposed` and an owner question `web:research:<id>`. The worker skips it until the owner confirms (`test_propose_creates_a_proposal_the_owner_authorises`).

## 8. Scheduler, monitors, autonomy caps

| Control | Rule |
|---|---|
| Master switches | `enabled` (all network), `autonomous` (refresh and curiosity), both default off. `tick` with web access off does nothing and says why |
| Monitors | Interval at least 3600 s. Topic is privacy-screened and must be public text. Owner monitors start `active`; a monitor proposed by a paired Pocket device (`pocket:<family digest>`) starts `proposed` with a `web:monitor:<id>` question and runs only after confirmation (`test_pocket_monitor_proposal_is_authorised_through_resolve`) |
| Tick | Runs due active monitors (origin `monitor:<id>`); then, only if `autonomous`: due predictions (refresh tasks) and the top curiosity candidates. Optional `max_tasks`; `queue=True` queues instead of running inline |
| Autonomy scope | Autonomous research is limited to items **already in knowledge** and owner monitors. Hypotheses and gaps are reported as skipped ("propose it instead") |
| Daily cap | `daily_autonomous` over the last 24 h for origins `curiosity` and `refresh:*`: tasks 12, requests 400, bytes 80,000,000. A cap hit is reported as skipped with the figures (`test_tick_respects_the_cap_for_curiosity_and_the_master_switch`). Monitor runs are owner-authorised and not counted |
| Per-task bounds | Every task has a mode budget clamped by the ceiling (RESEARCH_ORCHESTRATION §4) |
| Worker | `work` claims queued tasks one at a time with a 600 s lease and resumes tasks whose lease expired; proposals are skipped |

**Meaningful change rule** (`scheduler.compare`). A monitor keeps a baseline from its previous run: canonical URL to content hash, and claim key to values, polarity and status. A run is a *meaningful change* only if a baseline exists and at least one of these holds: a new key appears as a `corroborated` cluster (or a `single_source` cluster from a primary structured source); a known key's values or polarity changed in such a cluster; consolidation superseded a knowledge item. New URLs and changed page bytes alone are not meaningful. A meaningful change writes a `finding` event on the task and the report of the tick; the baseline is updated after completed or partial runs (`test_tick_runs_due_monitors_and_reports_meaningful_change`). The first run only sets the baseline.

## 9. How to verify

```sh
pytest -q tests/webcortex/test_consolidate.py tests/webcortex/test_knowledge.py tests/webcortex/test_state.py
pytest -q tests/webcortex/test_instance_integration.py          # executive hook, forgetting, retrieve, inventory
pytest -q tests/webcortex/test_curiosity.py tests/webcortex/test_scheduler.py
pytest -q tests/instance -k "erasure or memory"                  # the binary-belief side still passes
```

## 10. Limits stated plainly

- Thresholds are policy, not calibration; the 0.75 floor needs three ordinary sources (section 3).
- Entity resolution is by name only.
- Curiosity ranks; it does not decide. The owner or a cap-limited autonomous tick decides what runs.
- Forgetting does not purge the public cache by itself (section 6).
- A restored checkpoint has knowledge but not the page bodies behind it.
