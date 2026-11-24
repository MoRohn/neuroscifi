# Web Cortex: memory integration

Status: describes the working tree on top of `64da95b`, including the UAT fixes not yet committed (`state.py`, `consolidate.py`, `knowledge.py`, `curiosity.py`, `scheduler.py`, `instance/{schema,executive,governance,memory,store}.py`). Test names are given where a property is verified. Contracts: [ARCHITECTURE.md](ARCHITECTURE.md) §6. Decisions: ADR-019, ADR-023, ADR-026, ADR-027, ADR-029. Time rules: [TEMPORAL_KNOWLEDGE.md](TEMPORAL_KNOWLEDGE.md). Evidence rules: [EVIDENCE_AND_PROVENANCE.md](EVIDENCE_AND_PROVENANCE.md). Operation (`nsf web knowledge`, `review`, `forget`, `monitor`, `feeds`, `cache`, `tick`, `curiosity`, `nsf resolve`, `nsf forget`): see RUNBOOK. Console, Pocket and MCP views of this memory: API_CONTRACTS §8.

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
| Metacognition | `web_feedback` | What each investigation did to a key: effect, before and after confidence, information gain in bits | `consolidate` | `curiosity`, `knowledge.feedback` (console knowledge view) |
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
| 2. Eligibility | **Adversarial claims are removed first** (claims flagged `adversarial_source`, or claims of this task whose source's quarantine found instruction-like content): they never support, strengthen or contest knowledge; independent sources are recounted over the rest, contradictions involving a removed claim are dropped, and a cluster made only of them is **held**, never knowledge (`test_claims_from_adversarial_sources_are_held_never_provisional`, `test_claims_from_injection_flagged_sources_are_held`). Then `contested` clusters are handled in step 4. Otherwise a cluster must have a claim with relevance above 0, must not be `insufficient`, and, if `single_source`, must come from a primary structured source (an API record or a `metadata` claim). Failing clusters are **held**, not stored |
| 3. Duplication | Look for live knowledge (provisional, accepted, stale) that already cites one of the cluster's claims, else the latest version with the same key, else the most similar key (token Jaccard at least 0.8). If values and polarity match: refresh `last_verified_at`, add the new claim ids and sources, raise confidence to the larger of the two only when new claims arrived, never duplicate; a `stale` item returns to `provisional`. Feedback `strengthened` (new claims) or `unchanged` (`test_duplicate_evidence_strengthens_and_never_duplicates`) |
| 4. Comparison with memory | Series, supersession and contradiction per TEMPORAL_KNOWLEDGE §5. Feedback `reversed` (superseded), `weakened` (new contradiction), `unchanged` (already recorded) |
| 5. Provenance and time | Supporting claim ids, sources `{url, title, domain, published_at, retrieved_at}`, freshness class from the task's assessment, validity interval |
| 5b. Owner rejection stands | A statement with the same values and polarity as a row the owner **rejected** under the same or a near-identical key is **held** ("owner rejected this before (knowledge #N)"), never proposed again; a different value may become a new version (`test_new_knowledge_after_reject_or_archive_takes_the_next_version_and_rejection_stands`) |
| 6. Commit | A **new** item is written only as `provisional`, and only if the cluster's ADR-035 verification state is `supported` (`evolution.cluster_verdict`; v0.7.0, ADR-038), it has no contradiction, and, for basis `corroboration`, the owner's floors hold (`auto_provisional_min_sources`, default 2; `auto_provisional_min_confidence`, default 0.72, over the category posterior). Each new item records its provenance kind, verification and times, and gets an owner question (scope `web:knowledge:<id>`) and a prediction. Clusters that are not `supported` are kept as research opportunities (hypotheses): see [KNOWLEDGE_EVOLUTION.md](../knowledge/KNOWLEDGE_EVOLUTION.md) §2 |

**Versions.** A new row takes `consolidate.next_version(db, key)`: `MAX(version) + 1` over **every** row of the key (live, archived, rejected, superseded), so the `UNIQUE(key, version)` constraint cannot collide after an owner rejected or archived an earlier version.

**Report-safe text.** Statements in `held[]`, in contested questions and in contradiction records go through `consolidate.safe_text` (`claims.is_reportable`, `synthesis.report_safe_text`): text from an adversarial source or with instruction-like content is replaced by `(withheld: the statement carries instruction-like content)` (`test_held_statements_never_carry_instruction_like_text`). A page whose only finding is a Markdown image beacon is not adversarial (`test_a_markdown_image_beacon_alone_does_not_make_a_source_adversarial`). A contested question names the disagreement in plain words from the conflict `description`, for example "Sources disagree about NumPy: one says 2.3, another says 2.4." (`test_contested_questions_use_the_plain_conflict_description`).

After the loop, entities and mentions are written (section 4). The summary returned and stored on the report holds `provisional`, `strengthened`, `unchanged`, `superseded`, `contested`, `held` and `pending` (question ids), plus the number of feedback rows and entities.

`test_weak_evidence_is_held_as_claims_only` covers single source, low confidence, irrelevance and `insufficient`; `test_primary_structured_single_source_needs_the_source_threshold` shows a one-record API cluster is held at the default threshold and becomes provisional when `auto_provisional_min_sources` is set to 1.

**Practical thresholds** (before v0.7.0; since ADR-038 the bar is the ADR-035 state, so an undated stable pair at 0.722 passes and a time-sensitive statement needs evidence dated inside its window: KNOWLEDGE_EVOLUTION §2). The decisive criteria are at least two agreeing independent sources and no contradiction; the confidence floor of 0.72 keeps weak pairs out. With the evidence weights of EVIDENCE_AND_PROVENANCE §3: two dated secondary groups give 0.722 and pass; one dated plus one undated give 0.706 and two undated 0.688, and do not; two primary records give 0.737 and pass. The floor is the same constant as the fact-verification threshold (`synthesis.SUPPORTED_MIN_CONFIDENCE`), so a verdict of `supported` and a provisional item agree (`test_two_independent_sources_rule_is_coherent_across_verdict_and_consolidation`). The consolidation tests hard-code a cluster confidence of 0.85, so they exercise the rule but not this arithmetic. Raising or lowering `auto_provisional_min_confidence` is an owner decision.

**Questions.** Questions live in the existing `pending` table (`kind 'ask'`, `epoch NULL`). `knowledge.open_question` keeps **one open question per subject**: a second finding about the same scope is appended to the open question as an "Update:" line. Scopes:

| Scope | Opened by | `nsf resolve --confirm` / `--reject` |
|---|---|---|
| `web:knowledge:<id>` | New provisional item; a contradiction; a failed prediction | accepted / rejected |
| `web:monitor:<id>` | A monitor proposed by a paired Pocket device | monitor becomes active (due now) / stopped |
| `web:research:<id>` | A curiosity proposal | task queued for the worker / cancelled |
| `web:contested:<task>:<n>` | A contested cluster with no matching knowledge | informational: answer without `--confirm` |

**One owner decision, one transition** (`knowledge.decide`). `review()` and `nsf resolve` (`apply_review`) share it. A provisional, stale, accepted or rejected item may be accepted or rejected; archived and superseded rows raise `Conflict`. An owner decision may be **revised**: rejecting an accepted item, or re-accepting a rejected one, writes the revision into the item's note ("owner re-accepted it after an earlier rejection" / "owner rejected it after an earlier acceptance") and appends a dated line to the resolution of every already-resolved question of scope `web:knowledge:<id>` (at most 4000 characters), so the question's record never contradicts the item. Open questions of that scope are closed. The result carries `previous_status`, `status`, `pending_closed` and `decisions_revised` (`test_review_after_resolve_revises_the_decision_on_the_question_with_history`).

**Refresh tasks.** An owner's refresh of an item (`knowledge.refresh`, from the CLI or console) is recorded with origin `refresh-owner:<id>` (`origin_label`: "Refresh of knowledge #<id>"). It is not autonomous and is never clamped to the daily allowance. A due prediction uses `refresh:<id>` and is autonomous.

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

`memory.retrieve` reads records by kind, id and time. Retrieval by topic is `knowledge.query(db, topic, now, limit=10)`: the live statements (provisional, accepted, stale) that match a question, used by `nsf web knowledge TOPIC`, the console knowledge view, the MCP `web_knowledge` tool and, in every research task, by `intent.assess` to decide whether memory answers without the web. By default it ranks by BM25 over each statement's own words and key. The two subsections below describe the optional ranking by meaning and the check that runs before memory answers.

### 5.1 Semantic retrieval (`semantic.py`, optional)

**Why.** The lexical ranking finds a fact when the question uses its words. Measured on 118 queries over 48 fictional facts written through the real write path (`scripts/bench_memory.py`; `docs/quality/evidence/accel_baseline_memory.json`): the right fact is first for 0.92 of 48 questions in the fact's own vocabulary, for 0.27 of 48 paraphrases, and for 6 of 12 descriptions that do not name the subject (the other 6 do not find it at all); all 10 questions that nothing in memory answers still return an unrelated fact. A query takes 0.6 ms (p50).

**What it does.** Semantic retrieval is off until the owner configures and enables it. When it is on, `knowledge.query` asks two small models behind the inference gateway (providers `embed` and `rerank`, [COGNITIVE_INFERENCE §2](../architecture/COGNITIVE_INFERENCE.md#2-inference-gateway-srcneuroscifiinference)), each optional:

| Model | Role |
|---|---|
| `embed` | One vector per live statement and one for the question. The nearest statements are the candidates (`candidates`, default 10, or the query's limit when that is larger). The 5 best lexical hits are always added, so a rare exact word is never lost (`test_the_best_lexical_hits_are_always_among_the_candidates`) |
| `rerank` | A cross-encoder reads the question with each candidate and scores it. The candidates are returned in that order, and one scoring under `floor` (default 0.02) is not returned at all: nothing in memory is about this (`test_a_question_about_nothing_in_memory_returns_nothing`) |

With only `embed` configured the candidates are returned by cosine similarity and nothing is cut: similarity alone does not separate "about this" from "about something similar", so no floor is applied without the reranker (`test_without_a_reranker_the_order_is_by_similarity_and_nothing_is_cut`). An item's `score` is then the cosine; with the reranker it is the reranker's score (0 to 1). The reranker receives the question and each statement in Qwen3-Reranker's own prompt form (`rerank_template: qwen3`; `plain` sends the bare texts for another model).

**The `retrieval` field.** Every `knowledge.query` result says which ranking produced its items:

| Value | Meaning |
|---|---|
| `{"mode": "lexical"}` | Semantic retrieval is off. The result is exactly the 0.7.0 ranking and no model is asked (`test_off_by_default_the_lexical_ranking_is_unchanged_and_no_model_is_asked`) |
| `{"mode": "lexical", "reason": "…"}` | It is on but could not be used: no embedding endpoint is set (the reason names `nsf web memory config --embed-url URL`), the key file is unusable, or a memory-model call failed or timed out (the reason carries the error code, e.g. `unavailable`). The items are exactly the lexical result (`test_a_failing_memory_model_falls_back_to_the_lexical_ranking_and_says_why`) |
| `{"mode": "lexical", "reason": "…", "waking": ["embed", "rerank"]}` | A local memory model is unloaded while idle (below). The lookup is not held up: the items are the lexical result, and the models named in `waking` are starting again in the background (`test_a_memory_lookup_is_lexical_at_once_while_its_models_sleep_and_semantic_again_once_they_are_back`) |
| `{"mode": "semantic", "ranked_by": "rerank", "candidates", "dropped_below_floor", "floor", "models": {"embed", "rerank"}}` | Ranked by the reranker; `dropped_below_floor` statements were scored and not returned |
| `{"mode": "semantic", "ranked_by": "embedding", "candidates", "models": {"embed"}}` | Ranked by vector similarity, nothing cut |

`"cached": true` is added when the same question over the same statements and settings was already ranked in this process: one research task asks memory the same question up to three times, and the repeats cost no model call (`test_the_same_question_over_the_same_memory_asks_the_models_once`).

**Lexical fallback.** Any failure is the lexical ranking, never an error and never an empty answer caused by the models: off, not configured, unreachable, a malformed reply, or a call past `timeout` (default 2 s per memory-model call; embedding new statements gets at least 10 s). The owner sees the reason in `retrieval` and in `nsf web memory`. A failed call is not retried, and the models are then left alone for 30 s (`retrieval.retry_in_seconds`): with the reranker's port closed, the first version spent 1.5 s of connection retries on every lookup.

**A service, not a dependency.** The two models are installed, supervised and released like the other model services, and memory never depends on them being up:

- *Setup.* `nsf setup` configures them for the individual when their key file exists and each answers one real request, and switches semantic retrieval on; with the embedding model alone it switches on the ranking without the cut and says what is missing. It contacts nothing without the key file, sends no text of the individual, and never changes a memory setting the owner made by hand: it records what it wrote (`meta` key `semantic_setup`) and acts again only while the settings still equal that record (`tests/test_setup_cli.py`, "step 7").
- *Supervision.* `deploy/systemd/neuroscifi-memory-embed.service` and `neuroscifi-memory-rerank.service` (installed disabled by `deploy/install.sh --memory`), or `deploy/memory/run.sh start` with Docker's own restart policy. `run.sh wait` counts a model as ready only after it answered a real request: the first request after a start took 1.0 to 1.5 s, the following ones about 20 ms, and a lookup allows each call 2 s.
- *Idle.* With an idle timeout set by the owner (`nsf resources idle --set embed=15m --set rerank=15m`; off by default) the policy of [RESOURCE_SCHEDULING §3(c)](../architecture/RESOURCE_SCHEDULING.md) stops a model nobody used and a lookup brings it back. A lookup never waits for that: it is ranked by its words at once and says so (`retrieval.waking`), and a later one is ranked by meaning again. With one of the two models unloaded the lookup is lexical, not half semantic. A model that cannot be started (not enough free memory, a failing container) is tried again 30 s later at the earliest. Only this host's own services are managed; an endpoint configured elsewhere is called as it is.

**What it is not.** It is not a second memory, and there is no vector store. Vectors live only in the process that computed them, keyed by the statement's text, per individual and per embedding model. On every query the set is trimmed to the live statements before anything else happens, so a forgotten, superseded or archived statement leaves no vector behind, is never shown to a model again, and nothing derived from memory is written to disk (`test_a_forgotten_statement_leaves_no_vector_and_is_never_shown_to_a_model_again`). A new statement is embedded once; the others are not embedded again. Semantic retrieval ranks and filters what `knowledge.query` returns. It does not decide whether memory answers a question (`intent.assess` and §5.2 do), it does not merge or compare knowledge, and it never changes a statement, a confidence or a verification state. Checkpoints, forgetting and erasure (section 6) need no step for it.

**Privacy.** Statements and questions reach the two endpoints only through `Gateway.embed` and `Gateway.rerank`, which mark every text private. An endpoint off this host is refused unless the owner authorised that provider for remote use and, separately, for private memory (`test_memory_text_is_never_sent_off_this_host_without_the_private_memory_authorisation`). `nsf web memory config` sets neither authorisation, so memory models set up through the CLI are on this host. The token ledger records each call's purpose (`memory_embed`, `memory_query`, `memory_rerank`), tokens and latency, and no text.

**Settings and commands.** One settings row per individual (`meta` key `semantic`): `enabled` (default false), `candidates` (10; 1 to 100), `floor` (0.02; 0 to 1, 0 returns every candidate), `timeout` (2.0 s; 0.1 to 60) and `rerank_template` (`qwen3` or `plain`). Unknown keys and out-of-range values are refused (`test_settings_are_validated_and_stored_per_individual`).

```sh
nsf web memory config --enable --embed-url http://127.0.0.1:18441/v1 --rerank-url http://127.0.0.1:18442/v1 \
    --key-file ~/.config/neuroscifi/memory/api_key     # the key file path must be absolute
nsf web memory                                         # the switch, both endpoints with their health, the effect
nsf web memory config --set floor=0.05                 # one retrieval setting
nsf web memory config --disable                        # back to the lexical ranking (0.7.0)
```

`nsf web memory` prints `enabled`, `settings`, `providers` {`embed`, `rerank`: `configured`, `endpoint`, `model`, `state`, `reason`, `key_present`}, `vectors_held_by_this_process` and `effect` in plain words, never a key. The models themselves are served by `deploy/memory/run.sh` (Qwen3-Embedding-0.6B and Qwen3-Reranker-0.6B, both Apache-2.0, from the digest-pinned vLLM image of the answer model, on loopback ports 18441 and 18442); any server that speaks `/v1/embeddings` or `/v1/rerank` can take their place without a code change. A provider's `state` is `asleep` while the idle policy has its service unloaded. Installing, starting, the idle timeouts, the footprint and rollback: [RUNBOOK](../operations/RUNBOOK.md), "Memory models".

**Measured with the models on** (`scripts/bench_memory.py`, 130 queries over the same 48 facts: the 118 above and 12 topic queries; Qwen3-Embedding-0.6B and Qwen3-Reranker-0.6B, default settings):

| | Lexical (`accel_after_memory_lexical.json`) | Embedding only (`accel_after_memory_embedding.json`) | Embedding and reranker (`accel_after_memory_semantic.json`) |
|---|---|---|---|
| Fact's own vocabulary, right fact first (48) | 0.92 | 0.98 | 1.00 |
| Paraphrase, right fact first (48) | 0.27 | 0.94 | 0.98 |
| Subject not named, right fact first (12) | 0.50 | 1.00 | 0.92 |
| Nothing in memory answers it: something returned (10) | 10 of 10 | 10 of 10 | 3 of 10 |
| Topic query: statements of other subjects returned, mean (12) | 0.0 | 6.0 | 0.25 |
| `knowledge.query` time, p50 / p95 | 0.7 ms / 0.7 ms | 41 ms / 49 ms | 107 ms / 185 ms |

The files are in `docs/quality/evidence/`. The benchmark reports the two halves of the subjects separately, so that a threshold chosen on the development half is checked on the held-out half: there the reranked run finds the right fact first for 24 of 24 paraphrases and still returns something for 2 of 5 unanswerable questions (`by_half.held_out`). The embedding alone ranks well and cuts nothing, which is why the floor needs the reranker. The price is time: a query costs about 0.1 s instead of under a millisecond, and the first query of a process embeds every live statement (318 ms for 48). The benchmark is 48 fictional facts; it says nothing about a memory of thousands of statements.

### 5.2 The memory-answer check (`memory_gate.py`)

**The defect it closes.** `intent.assess` lets memory answer a question without the web when a stored statement covers at least 60 % of the question's topical stems (`intent.covers`). Shared stems cannot tell a property from its neighbour: "Does the Orbix server support encryption in transit?" was answered from "… does not support encryption at rest", and "the tallest structure in the world?" from "… the tallest structure in the Ostra region" (`test_the_lexical_test_alone_answers_the_wrong_property_from_memory` pins the first with the check off).

**What it does.** Before memory answers without the web, `_Run.memory_check` (orchestrator, discovering stage) asks the answer model whether each chosen statement states the answer to exactly what is asked: the same subject, the same property and the same scope. It makes one short structured call per statement (`memory_gate.confirm`, purpose `memory_gate` in the ledger; at most 8 statements, any beyond are refused unchecked): each reply is one boolean, and no statement appears in another's prompt, so the text of one cannot change the verdict on another, validated against a schema of exactly that length; no other text is generated. At most 8 statements are sent, each cut to 600 characters; a chosen statement beyond the eighth counts as not confirmed. The call has 8 s (`TIMEOUT`) or what is left of the task's time budget, whichever is less, and carries the background priority for an autonomous task ([COGNITIVE_INFERENCE §7](../architecture/COGNITIVE_INFERENCE.md#7-adaptive-inference-policy)).

| Outcome | Effect | `memory_check` in the task's `decision` event |
|---|---|---|
| The model confirms at least one statement | Memory answers as before, from the confirmed statements only (`test_a_statement_the_model_confirms_is_answered_from_memory_as_before`) | `{"status": "checked", "confirmed": [ids], "refused": [ids]}` |
| The model confirms none | The question goes to the web: `needs_web` becomes true with the reason "N knowledge item(s) share the question's words but do not state its answer (checked by the answer model)" (`test_a_statement_the_model_refuses_sends_the_question_to_the_web_instead`) | `{"status": "checked", "confirmed": [], "refused": [ids]}` |
| The check cannot run: the call failed or timed out, a reply was not true or false, or less than 1 s of budget is left | The lexical decision stands; nothing is guessed (`test_when_the_check_cannot_run_the_lexical_decision_stands_and_the_event_says_so`) | `{"status": "skipped", "reason": "…"}` |
| No answer model is configured, or model reasoning is off | Nothing is attempted and the event is exactly the 0.7.0 one (`test_without_any_model_the_decision_event_is_exactly_the_0_7_0_one`) | absent |

It is a stricter filter, not a new source of answers: it only removes statements from the set the lexical test chose, so it can send a question to the web that memory would have answered and never the reverse. The statements are data inside the prompt; the system prompt says to ignore instructions inside them, and the single-boolean schema, in a call of its own, leaves an instruction nothing to change but possibly its own boolean. The check does not run when the task already needs the web, and not for a crawl of given sources.

**Measured** (`scripts/bench_memory.py --gate --llm-url URL`; `docs/quality/evidence/accel_after_memory_answer_check.json`; 96 questions, each with the 4 stored facts of its subject, Qwen3-30B-A3B): the answer model accepts 86 of 96 statements that answer the question and 1 of 288 statements about another property of the same subject, at about 0.3 s per statement. (A first design put all statements in one prompt and accepted 94 of 96; an independent review showed that one statement's text could then sway the verdict on the others, so it was replaced, at the price of eight more right statements refused. A refusal sends the question to the web.) The lexical test alone accepts 64 of the 96 and 94 of the 288. In use a statement answers from memory only when both accept it. One check takes 0.45 s (p50; p95 0.50 s). The three mistakes of the model are listed in the file (`model_check_mistakes`). The module docstring of `memory_gate.py` records an earlier run of the same benchmark (95 of 96, 0 of 288) and that a 0.6B reranker accepts 11 to 60 of the 288 depending on its threshold and both wrong examples above, which is why the reranker of §5.1 is not used for this decision.

**Privacy.** The statements are the owner's memory: the message that carries each one is marked private, as `Gateway.embed` and `Gateway.rerank` mark them. With the answer model on this host (the default) they do not leave it. With an answer model the owner authorised off this host they are sent only under the separate private-memory authorisation (`remote_private_memory`); without it the check is skipped (`private_blocked`) and the lexical decision stands (`test_memory_statements_are_never_sent_to_a_model_off_this_host_without_the_authorisation`).

**Default and rollback.** On by default (`memory_check`, a web setting); without a configured answer model it does nothing. `nsf web config --no-memory-check` restores the 0.7.0 behaviour (memory answers whenever its statements share enough of the question's words); `nsf web config --memory-check` turns it back on. Cost: one extra model call for a question that memory would answer.

## 6. Forgetting and erasure

Governance (`instance/governance.py`) removes web memory completely and records only ids.

| Call | Removes | Cascade |
|---|---|---|
| `forget_research(task_id)` | The task row, its events, sources, claims, links, mentions, feedback | Knowledge whose supporting claims are all gone is deleted with its predictions, feedback, mentions and open questions. Knowledge with other support loses the references, becomes `stale` and notes "needs re-verification". A version whose successor was deleted becomes current again, `stale`. Parent, monitor and knowledge references to the task are nulled. Claim-less entities are deleted |
| `forget_knowledge(id)` | The item and every version in its `superseded_by` chain | Its predictions, feedback, mentions and questions. Claims stay with their tasks. The **unfinished refresh tasks** of the chain (`refresh-owner:<id>`, `refresh:<id>`, and owner refreshes recorded before those origins existed) are forgotten too, because the forgotten statement is their objective; a worker running one stops at its next fenced write. The effects list them as `refresh_tasks_forgotten` and name their unshared public-cache keys under `cache` (`test_forget_knowledge_forgets_its_unfinished_refresh_tasks`) |
| `forget_source(url or domain)` | Matching `web_sources` and the claims taken from them (a domain matches subdomains too) | The same knowledge cascade. Returns `effects["cache"]` = `{urls, domain, sha256}`, the keys the caller must drop from the public cache |

When checkpoints became stale, the result carries `next_steps` naming `nsf checkpoint rebuild`: a stale archive keeps the forgotten payload until it is rewritten, rebuilt or pruned. `nsf forget research|knowledge|source` rewrites, before it returns, every archive that only web and voice forgets made stale (`checkpoints.after_forget`; the replay is the live cascade, `replay_web_forgets`). It lists them under `checkpoints_rewritten` and keeps `next_steps` only for archives still stale. The console's forget routes mark archives stale as before; `nsf checkpoint rewrite` rewrites them later. Common to all three: one transaction; every valid checkpoint is marked `stale`; `state_version` is bumped; one `deletion_log` row is written naming the target kind and id (never the objective or any text); the WAL is truncated. Questions about removed knowledge keep their row, with the text replaced by `#forgotten` and a system resolution. A forgotten task's own questions (its proposal, review aggregate, contested subjects, monitor findings and autonomy finding, whose text quoted the objective) are closed the same way and their scope becomes `web:forgotten`, and its daily-budget row in `autonomy_ledger` keeps only its numbers under a random id (R6 D11, [RESEARCH_POLICY.md](../autonomy/RESEARCH_POLICY.md) §2); after a research forget, `deletion_log` is the only row that names the task id. The same salted-digest idea as ADR-019 applies: the history keeps its shape and loses its words.

Verified: `test_forget_research_erases_the_task_and_knowledge_resting_on_it` plants a unique objective, forgets the task, and checks that the string is absent from the database file **and its WAL**, that every `web_*` table is empty, that checkpoints are `stale`, and that the deletion log holds only the task id. `test_forget_research_keeps_knowledge_with_other_support_as_stale`, `test_forget_knowledge_removes_the_whole_version_chain`, `test_forget_source_by_domain_and_url`.

**Checkpoints.** A restored checkpoint restores web knowledge and provenance but not page bodies; re-verification refetches (ADR-023). Forgetting marks existing checkpoints stale because they still contain what was removed.

**Public cache separation.** The cache holds public bytes keyed by URL and content hash, plus validators and `robots.txt`; it holds no queries, objectives or claims. Consequently `forget_research` does not touch it (the same public page may serve other tasks), and `forget_source` only *names* the cache keys to drop: the governance functions never open the cache file. The interface lane drops the cache entries: `nsf forget source` calls `governance.forget_source`, then `PublicCache.forget_url` or `forget_domain`, which delete the rows and every body no remaining row references (`test_nsf_forget_research_knowledge_and_source`, `test_forget_deletes_only_orphaned_bodies`). Retention by size and age (`cache_max_bytes` 512 MiB, `cache_max_age_days` 30) is applied by `Services.prune_cache` after each run and tick, and by `nsf web cache prune` (SEARCH_AND_ACQUISITION §6).

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

Curiosity searches with the statement's own words (`intent.query_text`), never claim-key stems (`curiosity.search_terms`). `curiosity.propose(candidate)` creates a *proposal*: a task queued with stage `proposed` and an owner question `web:research:<id>`. The worker skips it until the owner confirms (`test_propose_creates_a_proposal_the_owner_authorises`). Proposals count against the daily autonomous allowance: the proposal's request and byte budget is sized to what is left when it is made (a `ValidationError` and no proposal when nothing is left), and the allowance is checked again when the owner confirms (`knowledge._within_allowance`: clamped, recorded as `clamped_to_daily_allowance`, or a `Conflict` that leaves the proposal open) and when the worker starts it (`test_curiosity_proposals_count_against_the_daily_autonomous_cap`). A proposal confirmed days later counts from its confirmation, because the daily window covers tasks created or started in the last 24 h and tasks still open (`scheduler._window`).

## 8. Scheduler, monitors, autonomy caps

| Control | Rule |
|---|---|
| Master switches | `enabled` (all network), `autonomous` (refresh and curiosity), both default off. `tick` with web access off does nothing and says why |
| Monitors | Interval at least 3600 s. Topic is privacy-screened and must be public text. A monitor's task reads the owner's feed items before web results (RESEARCH_ORCHESTRATION §3). Owner monitors start `active`; a monitor proposed with `authorized_by=pocket:<family digest>` (the scheduler API; no Pocket route calls it at `df00137`) starts `proposed` with a `web:monitor:<id>` question and runs only after confirmation (`test_pocket_monitor_proposal_is_authorised_through_resolve`) |
| Tick | Runs due active monitors (origin `monitor:<id>`); then, only if `autonomous`: due predictions (refresh tasks) and the top curiosity candidates. Optional `max_tasks`; `queue=True` queues instead of running inline |
| Autonomy scope | Autonomous research is limited to items **already in knowledge** and owner monitors. Hypotheses and gaps are reported as skipped ("propose it instead") |
| Daily cap | `daily_autonomous` over the last 24 h for origins `curiosity` and `refresh:*`: tasks 12, requests 400, bytes 80,000,000. A cap hit is reported as skipped with the figures (`test_tick_respects_the_cap_for_curiosity_and_the_master_switch`). The request and byte budgets are also **clamped at submit** to what `scheduler.autonomous_remaining` says is left (caps minus what finished autonomous tasks spent and what open ones may still spend; proposals waiting for the owner are not counted), so the cap is passed by at most the request in flight; nothing left means the task is not submitted (`test_autonomous_budgets_are_clamped_to_the_daily_allowance`). Monitor runs are owner-authorised and not counted |
| Per-task bounds | Every task has a mode budget clamped by the ceiling (RESEARCH_ORCHESTRATION §4) |
| Worker | `work` claims queued tasks one at a time with a 600 s lease, renewed after every fetch and search round, and resumes tasks whose lease expired **or whose worker process is dead** on this host (`services.holder_alive`; RESEARCH_ORCHESTRATION §5); proposals are skipped. Queued tasks the owner asked for are claimed before autonomous ones (`curiosity`, `refresh:*`), oldest first within each class; an autonomous task queued more than 10 minutes ago (`AUTONOMOUS_DEFER`) competes by age again, so it is never starved. A waiting worker is woken by a submission from any process (SQLite's `data_version` and the queue are watched between polls) and claims it within about 0.1 s; the 5 s poll stays as the fallback |
| Cache retention | Every tick and every finished run calls `Services.prune_cache` (at most hourly through `PublicCache.maybe_prune`); the tick report carries `cache` (`test_tick_and_runs_apply_cache_retention`) |

**Meaningful change rule** (`scheduler.compare`). A monitor keeps a baseline from its previous run: canonical URL to content hash, and claim key to values, polarity, status, independent groups and relevance, plus `seen_keys`, every claim key the monitor has seen (bounded at 5000, oldest dropped; URLs and claim details are bounded at 2000). A run is a *meaningful change* only if a baseline exists and at least one of these holds:

- a known key's values or polarity changed, reported by a corroborated cluster (at least 2 independent groups) or a primary structured record;
- a **new** key appears that is corroborated, or comes from a primary structured record whose relevance is at least `PRIMARY_RELEVANCE` (0.5). A key counts as new only if it is not in `seen_keys`, not similar to any seen key (token Jaccard), and not a rewording of a known key with the same values and polarity;
- consolidation superseded a knowledge item.

New URLs, changed page bytes and a different mix of search results saying the same things are never meaningful (`test_compare_ignores_search_churn_and_reports_value_changes`, `test_two_ticks_over_equivalent_result_mixes_report_no_change`). A meaningful change writes a `finding` event on the task and the report of the tick; the baseline is merged after completed or partial runs (`test_tick_runs_due_monitors_and_reports_meaningful_change`). The first run only sets the baseline.

## 9. How to verify

```sh
pytest -q tests/webcortex/test_consolidate.py tests/webcortex/test_knowledge.py tests/webcortex/test_state.py
pytest -q tests/webcortex/test_instance_integration.py          # executive hook, forgetting, retrieve, inventory
pytest -q tests/webcortex/test_curiosity.py tests/webcortex/test_scheduler.py
pytest -q tests/webcortex/test_semantic_memory.py tests/webcortex/test_memory_gate.py   # §5.1, §5.2 (fake models)
python scripts/bench_memory.py --label baseline --out memory.json   # the lexical baseline of §5.1, no model needed
pytest -q tests/instance -k "erasure or memory"                  # the binary-belief side still passes
```

## 10. Limits stated plainly

- Thresholds are policy, not calibration; the 0.72 floor passes exactly two dated secondary sources and rejects undated pairs (section 3).
- Entity resolution is by name only.
- Curiosity ranks; it does not decide. The owner or a cap-limited autonomous tick decides what runs.
- Forgetting does not purge the public cache by itself (section 6).
- A restored checkpoint has knowledge but not the page bodies behind it.
- Without the memory models, retrieval by topic is lexical: a paraphrase finds the right fact first 27 % of the time and a question nothing in memory answers still returns unrelated facts (section 5.1, `accel_baseline_memory.json`).
- Semantic retrieval holds its vectors per process. A new process embeds every live statement again on its first query, and two processes (console and worker) each hold their own set. Every query compares the question with every live statement, and the measurement covers 48 statements only. With the reranker on, 3 of 10 questions that nothing in memory answers still return a statement.
- The memory-answer check is a model's judgement, bounded to a yes or no per statement. In the measurement of section 5.2 it refused 10 of 96 right statements and accepted 1 of 288 wrong ones. A wrong refusal costs a web search; a wrong acceptance still needs the lexical test to have chosen the statement. It checks at most 8 statements.
