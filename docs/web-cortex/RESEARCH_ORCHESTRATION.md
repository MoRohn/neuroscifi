# Web Cortex: research orchestration

Status: describes the working tree on top of `64da95b`, including the UAT fixes not yet committed (`intent.py`, `orchestrator.py`, `config.py`, `scheduler.py`, `state.py`). Test names are given where a property is verified. Contracts: [ARCHITECTURE.md](ARCHITECTURE.md) §2, §4.2, §4.4, §5, §7. Decisions: ADR-022, ADR-026, ADR-027. Operating commands (`nsf web ask`, `tasks`, `show`, `cancel`, `resume`, `requeue`, `work`, `tick`, `feeds`, `cache`): see RUNBOOK. The console (`/api/web/*`, embedded worker, `--no-web-worker`), Pocket research actions (ADR-028) and the MCP server submit and read tasks through the calls described here; routes and bodies are in API_CONTRACTS §8.

A research task is a row in `web_tasks`. Nothing runs without a task, a budget and an origin (ADR-027). Every step writes an episodic event; no stage reports progress it did not make.

## 1. Modes

`submit(db, objective, now, mode=None, ...)` picks the mode with `intent.assess` when none is given. A caller may pass any of the nine modes.

`assess` is deterministic and rule-based (no model, ADR-022). The first rule that matches wins:

| Order | Mode | Cue (case-insensitive regex on the question) | Example cue words |
|---|---|---|---|
| 1 | `continuous` | monitoring requested | monitor, keep an eye, notify me, alert me, subscribe |
| 2 | `change_detection` | change requested | what has changed, changes since, diff |
| 3 | `fact_verification` | verification requested | verify, fact-check, is it true that, claims, debunk |
| 4 | `comparative` | comparison cue | compare, vs, versus, difference between, better than |
| 5 | `deep_research` | depth requested | investigate, in-depth, comprehensive, thorough |
| 6 | `scientific` | scientific cue | study, paper, evidence, arXiv, PubMed, neuron, synapse, cortex |
| 7 | `technical` | technical cue | library, release, version, CVE, npm, PyPI, changelog |
| 8 | `quick_lookup` | none of the above | |

`gap_resolution` is never chosen by `assess`; curiosity proposals use it (see MEMORY_INTEGRATION). Each decision stores a human-readable reason in `assessment["why"]` (`mode`, `recency_window`, `freshness_class`, `kinds`). Cue tables are English only: text in another script is searched as written, tagged with its language and noted in `assessment["notes"]`; nothing is translated.

`assess` also returns:

| Field | Meaning |
|---|---|
| `freshness_class` | `scientific` if a scientific cue and no stronger technical cue; else `release` for a technical cue; else `breaking` for a recency cue with a window of at most 7 days (or the word "breaking"); else `volatile` for any other recency cue or for continuous / change modes; else `stable` (a fact check with no recency, release or scientific cue is `stable`: `test_stable_fact_checks_are_not_volatile`) |
| `recency_window` | The most restrictive explicit window: "today" 1 day, "yesterday" 2, "this week" 7, "this month" 31, "this year" 365, "recently" 30 (90 if scientific), a month or current-year mention up to 366 days; `None` if the cue names no window ("latest") |
| `kinds` | `academic` (scientific), `technical`, `encyclopedia` (short definitional question), `news` (recency cue without science/tech), always `web` |
| `entities`, `urls`, `sites`, `packages`, `comparison`, `claim`, `language` | Extracted deterministically; `packages` come from `name==ver`, `pypi:`, `npm:`, `github:` forms in the text |

### The `needs_web` decision

`needs_web` is false in exactly three cases, each with a stated reason (`test_needs_web_false_explains_itself`); when it is false, `assessment["memory_items"]` lists the knowledge ids that answer:

1. Bare arithmetic and no hard reason.
2. A short definitional question ("what is ...") with no hard reason and at least one fresh, uncontested knowledge item.
3. No hard reason, no soft reason, and at least one fresh, uncontested knowledge item.

*Hard reasons* force a web visit: a URL in the question, any recency cue, a verification request, monitoring, change detection, relevant knowledge that is stale, relevant knowledge that is contested. *Soft reasons*: scientific question, technical question, comparison, an entity that no knowledge statement mentions. Knowledge counts only if it is `accepted` or `provisional` and shares a content word with the question (`test_rejected_or_irrelevant_knowledge_does_not_count`, `test_stale_and_contested_reasons`), and it answers without the web only if it also **covers** the question: `intent.covers(item, question)` requires the statement to state at least 60 % (`MEMORY_COVERAGE`) of the question's topical terms, compared as 5-letter stems, so sharing one name ("Orbix") is not an answer to another property of it (encryption versus port). When related items exist but none covers, the reason says so (`test_related_knowledge_that_does_not_cover_the_question_does_not_answer_it`).

When the web is not needed the task builds its report from the knowledge items (stage `synthesizing` only). The report says `live: false`, carries `memory: {answered_from_memory: true, knowledge_ids, provisional_ids, reasons}`, and nothing is consolidated (`test_fresh_accepted_knowledge_answers_without_the_web`). Provisional knowledge is flagged, never presented as accepted: its answer items and findings carry `status: "provisional"` (and `knowledge_id`, `knowledge_status`), and each provisional item adds an `uncertainties` line saying the owner has not accepted it. The run emits an `extract` event (`from_memory`, `knowledge_ids`) and a `synthesized` event (`test_memory_answer_marks_provisional_knowledge_and_logs_extract_and_synthesis`).

## 2. Planning, expansion, refinement

`intent.plan` turns the assessment into at most `min(8, MODE_BUDGETS[mode].max_queries)` queries, ordered by priority and deduplicated (`test_plan_is_deduped_capped_and_deterministic`):

| Priority | Queries |
|---|---|
| 0 | Registry and advisory lookups for each package: `pypi:<name>`, `osv:PyPI:<name>@<ver>`, `npm:<name>`, `github:<o>/<r>`. A plainly named package (see *Plain package names* below) gets `pypi:` and `npm:` lookups |
| 1 | Comparison (joint query and one per side), the claim under verification, each sub-question (keywords), plus a scholarly and an encyclopedia variant where the kinds ask for them |
| 2 | One query per site the user named, with `site=` restriction (the domain is removed from the keywords) |
| 3 | Expansions: topic plus the current year when there is a recency window, `... review` (academic), `... release notes` (technical), `... fact check` (claims) |
| 4 | `... study`, `... changelog` |

**Plain package names.** In a `technical`, `comparative` or `quick_lookup` question with a release cue ("latest release", "latest stable version", "release notes", "security advisory", "vulnerability"), lower-case identifiers become registry candidates without any `pypi:` marker: names in backticks, names before "package", "library" or "module", and plain lower-case words ("numpy") when the cue is strong and the question has at most 14 words (`MAX_PLAIN_QUESTION_WORDS`). Stopwords, cue and temporal words, common English words and language or platform names are skipped, at most 3 names are taken, and a name that is one of the owner's private terms stays local context. Both registries are asked unless the question names one ("python", "pip" or "pypi" gives PyPI; "npm", "node" or "javascript" gives npm); the one that does not know the name answers 404, which is a **soft miss**, not a failure (SEARCH_AND_ACQUISITION §3). Advisories (`osv:`) are added for a plain name only when a version or an advisory cue is present. A `quick_lookup` that infers a package becomes `technical` (`test_plain_package_name_with_a_release_cue_queries_the_registries`, `test_package_inference_keeps_common_words_and_local_context_out`).

**Comparison sides.** The question's predicate is not part of a side: "numpy vs pandas latest release dates" and "the latest release dates of numpy and pandas" both give the sides `numpy` and `pandas` (`test_comparison_sides_do_not_swallow_the_predicate`).

**Planning hygiene.** A query made only of stopwords, cue words, temporal words ("last", "past week", "ago") and years searches for nothing and is never sent (`intent.searchable_text`; registry lookups always pass) (`test_no_query_is_only_stopwords_or_temporal_words`). `intent.query_text(statement)` returns the topical words of a stored statement in their original form and order, never claim-key stems; curiosity and refresh tasks search with it (`test_query_text_uses_the_statement_words_not_stems`, `test_curiosity_queries_use_the_statements_words_not_key_stems`).

**Decomposition** splits on `;`, `?` and sentence ends, and on " and " only when the right side begins with a verb from a fixed list. A clause with no searchable topic (an instruction only) is dropped with a note (`test_plan_monitor_drops_instruction_clause`). Every query carries a `purpose` string that appears in the episode log.

**Self-references are local context.** References to the individual's own system are answered from local documents and state and never become web queries. `intent.strip_local(text, local_terms)` removes the names in `intent.SELF_NAMES` (`NeuroSciFi`, `Neuro SciFi`, `Neuro-SciFi`), possessive and demonstrative phrases ("our", "my", "this individual", "this instance", "this system"), "the libraries we use"-style phrases, and the caller's `local_terms` (the owner's `private_terms`, unless the owner authorised disclosure for this task). A possessive reference is removed with the noun phrase it owns (up to 4 non-function words: "NeuroSciFi's learning architecture"); a bare name is removed alone. `intent.plan(..., local_terms=)` and `intent.refine(..., local_terms=)` apply it to the question, claim, comparison sides and entities, and a `notes` entry says how many references were not searched; a comparison whose side is purely local searches only the outside side (`test_plan_and_refine_never_search_the_individuals_own_system`, `test_strip_local_keeps_the_outside_topic`, `test_self_references_are_local_context_not_web_queries`).

**Privacy.** Every planned query passes `safety.privacy_screen` with the owner's private terms and the camera and partner roots of the evidence journal. A query with a finding is dropped and a `decision` event counts the drops and names the finding kinds, never the text (`test_privacy_screen_blocks_private_identifiers` covers submission). **Owner authorisation** (`authorization="owner-approved-disclosure"` at submit) applies to **this task's own queries only**: a query is kept when every finding in it is private text that the task's objective itself contained, and a `decision` event `disclosed_under_authorization` records the count and finding kinds (never the text). Curiosity and refresh tasks cannot carry it, and a monitor's task carries it only when the owner authorised that monitor; otherwise `submit` raises `ValidationError` (`test_owner_authorisation_applies_to_the_tasks_own_queries`). `requeue` and `resume` keep the authorisation.

**Refinement.** After the first search, `intent.refine` allows one broader round when fewer than 3 results arrived or every provider failed: no date or site limit, text cut to the key entities or first three keywords, at most 4 queries (`MAX_REFINE_QUERIES`), original queries not repeated, and **never more than the queries the run may still send**: callers pass `remaining = budget.max_queries - usage.queries`, and by default the mode's `max_queries` minus the planned ones; with none left, `refine` returns `None` (`test_refine_broadens_thin_results`, `test_refine_returns_none_when_nothing_broader_exists`, `test_refine_never_overshoots_the_mode_query_budget`).

## 3. Task lifecycle

```
queued ──▶ discovering ──▶ reading ──▶ evaluating ──▶ synthesizing ──▶ completed | partial | failed | cancelled
```

`status` takes one of the nine values above; terminal states are `completed`, `partial`, `failed`, `cancelled`. A *proposed* task is `queued` with `stage = "proposed"`; the worker skips it until the owner confirms the question `web:research:<id>`.

| Stage | Work | Checkpoint written |
|---|---|---|
| discovering | `knowledge.query`, `intent.assess`, `decision` event, plan (self-references removed), privacy screen, `registry.search`, at most one `refine` round. The lease is renewed after every search round | `assessment`, `plan`, `results`, `attempts` |
| reading | Rank candidates, read until `max_pages` documents succeed (see *Selecting documents* below), extract, quarantine, optional render, optional two-link follow (SEARCH_AND_ACQUISITION §5). Each document is saved as a `web_sources` row and checkpointed as soon as it is read; the lease is renewed after every fetch (`test_lease_is_renewed_after_every_fetch`) | `documents`, `selected` |
| evaluating | `claims.extract_claims` per document (one `extract` event each: `test_web_run_logs_extract_per_document`), `cluster`, `score` with the owner's `freshness_ttl` as `ttl_overrides`; clusters and claims persisted | `clusters` |
| synthesizing | `synthesis.synthesize` builds the report (`synthesized` event); then `consolidate.run`, one prediction per new provisional item, mode statistics, and public-cache retention (`Services.prune_cache`, at most hourly; a failure is logged as an `error` event with stage `cache_retention` and does not change the outcome) | `result` |

**Selecting documents** (`_Run.select`, `read`). `select` ranks at most `3 x max_pages` candidates: URLs named in the question first, then structured API records (and, for a monitor's task, feed items), then web results, deduplicated by canonical URL. `read` takes them in order until `max_pages` documents have **succeeded**. A failed fetch (blocked by policy or robots, error, refused type) costs requests, bytes and time, never a page slot, so one dead link does not shrink the evidence; the request, byte and time budgets bound the attempts (`test_failed_fetches_do_not_use_page_slots`). In `scientific` and `deep_research` modes `ceil(max_pages / 3)` slots are reserved for web pages (`WEB_RESERVE_MODES`): API records fill only the other slots, and take a reserved one only after every web candidate was tried (`test_scientific_mode_reserves_page_slots_for_web_pages`). A Wikipedia search hit is a snippet, so reading takes its **article lead** (`WikipediaProvider.article`, else the page) and never the snippet (`SNIPPET_ONLY`, `test_wikipedia_hits_are_read_as_articles_never_as_snippets`). A page reached from a feed item takes the item's date as its publication date (`test_a_page_reached_from_a_feed_item_takes_the_items_date`). A page rendered by the browser is kept in the public cache under `<url>#nsf-rendered`, so a resume re-reads it instead of rendering again; an exhausted render allowance is a limitation (`test_render_budget_shortfall_is_a_limitation_and_rendered_pages_are_cached`). For a news / time-sensitive question reading is static-first (ADR-040): several candidates wait while the other candidates are read. They are a scripted page whose static HTML gave no text or no date, an encyclopedia article, a page dated before the required freshness window (the recency window, else the freshness class TTL), and a hit the search engine dates before that window, which is not fetched yet. Slots still open then go to them in `LATER_ORDER`: pages without text first, rendered when allowed; then the rest in rank order; a page without text that could not be rendered takes a slot last (`test_news_questions_read_dated_static_pages_before_rendering`, `test_news_questions_read_encyclopedia_and_out_of_window_pages_after_dated_ones`, `test_news_pages_without_text_render_before_undated_ones`, `test_news_hits_the_search_engine_dates_before_the_window_are_fetched_last`). A render is one page read plus its own allowance of `budget.max_render_requests` sub-requests (100), never the task's `max_requests` (`test_a_render_spends_its_own_allowance_not_the_task_requests`); the owner's switches, the day's render cap and critical memory pressure can refuse it (`test_news_rendering_follows_the_owner_switches`, `test_the_daily_render_cap_counts_across_tasks_and_days`, `test_news_rendering_is_refused_under_critical_memory_pressure`); SEARCH_AND_ACQUISITION §9 has the budget table. Feeds: SEARCH_AND_ACQUISITION §2; monitors read feed items before web results (`test_monitor_tasks_read_feed_items_first_and_services_build_the_feed_provider`).

**Link following.** `deep_research` follows at most 2 same-site links of the first page through the orchestrator's own `follow()`, not `crawl.crawl`: the crawler re-fetches its seeds, returns only after the whole walk (no per-document checkpoint, cancel check or lease renewal) and has no render escalation. `follow()` keeps all four with the same `SafeFetcher` policy.

**Crawl tasks** (`nsf web crawl`, `context.crawl`) walk their seeds page by page with `crawl.crawl`'s rules: each page is recorded and checkpointed as it arrives, and cancel is honoured between pages, so a crawl keeps the same guarantees as a search task (`test_crawl_records_each_page_as_it_arrives_and_honours_cancel_between_pages`). An objective that names no topic extracts claims **by document salience**, with the page's title as topic, and names URLs the crawl was blocked from (`test_crawl_without_topic_words_extracts_by_salience_and_names_blocked_urls`).

Final status:

- `completed`: every stage ran and no budget limit was hit.
- `partial`: a budget was exhausted (`limit()` records why), the evaluating stage raised, consolidation failed, or an exception occurred after at least one document was read (a report is built from what exists).
- `failed`: an exception with nothing read, or **every provider or page failed** so nothing could be read (network down, all circuits open). The limitation names the failures and the stages are cleared, so `requeue` searches again. An empty search that nobody failed is not a failure (`test_when_every_provider_fails_and_nothing_is_read_the_task_fails`, `test_an_empty_search_is_not_a_retrieval_failure`). When knowledge about the question exists the run ends `partial` instead.
- `forgotten`: a task forgotten while it runs stops at its next fenced write and writes nothing; the run returns `{id, status: "forgotten", lease_lost: true}` (`test_a_task_forgotten_while_it_runs_stops_and_leaves_nothing_behind`). This is a run result, not a stored status.
- Configuration is re-read while a run is going (see *Configuration re-check* in section 5).
- `cancelled`: see section 6.

`test_completed_report_cites_live_sources_and_consolidates` asserts the stage order `discovering, reading, evaluating, synthesizing`.

## 4. Budgets

`config.MODE_BUDGETS` (queries, requests, pages, seconds, depth; bytes 20,000,000, concurrency 4, context tokens 2000 and browser sessions 0 unless the caller changes them):

| Mode | max_queries | max_requests | max_pages | max_seconds | max_depth |
|---|---|---|---|---|---|
| `quick_lookup` | 3 | 20 | 3 | 45 | 0 |
| `fact_verification` | 6 | 40 | 8 | 120 | 0 |
| `deep_research` | 10 | 90 | 15 | 300 | 1 |
| `comparative` | 10 | 80 | 12 | 240 | 0 |
| `scientific` | 8 | 80 | 12 | 300 | 0 |
| `technical` | 12 | 120 | 10 | 240 | 0 |
| `change_detection` | 2 | 30 | 10 | 120 | 0 |
| `continuous` | 4 | 40 | 6 | 120 | 0 |
| `gap_resolution` | 4 | 30 | 5 | 90 | 0 |

**Autonomous tasks** (origin `curiosity` or `refresh:*`; the owner's own `refresh-owner:<id>` tasks are not autonomous and carry the label "Refresh of knowledge #<id>" in `origin_label`) have their request and byte budgets clamped at submit to the daily allowance left (`scheduler.autonomous_remaining`); nothing left means a `ValidationError` and no task. The `submitted` event records `clamped_to_daily_allowance` (MEMORY_INTEGRATION §8).

**Requested budgets and the ceiling.** A caller may pass a partial budget object. `submit` rejects unknown fields and non-numeric or negative values (`test_page_budget_allows_exactly_one_fetch` covers the unknown-field error). `WebConfig.budget_for` applies the request over the mode default, sets `max_browser_sessions` to 0 when both rendering switches (`browser_rendering`, `news_rendering`) are off and to at least 1 when either is on, and then clamps **every field** to `WebConfig.budget_ceiling` (default queries 16, requests 200, pages 30, bytes 60,000,000, seconds 600, depth 2, browser sessions 2, render requests 150, concurrency 6, context tokens 6000). A task can ask for less, never more.

**How each resource is charged and enforced**

| Resource | Charged | Enforced |
|---|---|---|
| `max_queries` | One per planned query, by the registry before sending | Extra planned queries are not sent; the failure `budget_exhausted: N planned queries not sent` is recorded |
| `max_requests` | One per network call: page, robots.txt, each redirect hop, each structured API call, each open-webSearch engine call | `BudgetExceeded` before the limit would be passed; reading stops |
| `max_pages` | One per document recorded (API records included); failed fetches are not charged | Reading stops when `max_pages` documents succeeded; limitation `page budget reached (N documents)` when candidates remained |
| `max_bytes` | Wire bytes after each read | The read stops before the cap is exceeded |
| `max_seconds` | Not a counter: `elapsed = seconds used before a resume + (clock - run start)` | Checked before each network step and between queries; a step already in flight can overrun by up to one fetch timeout (15 s) |
| `max_depth` | | Link following only when mode is `deep_research` and depth is at least 1 |
| `max_browser_sessions` | One per render | Render skipped when none left |
| `max_concurrency` | | Threads for provider fan-out and `afetch` |
| `max_context_tokens` | | Size of `synthesis.context_pack` (working-memory bundle: highest-value items, citations, an uncertainty line) |

`Usage.charge` checks and adds atomically under one module lock, so concurrent fetches cannot overshoot; a search started with the budget already used up sends nothing (`test_budget_exhausted_by_earlier_usage_stops_before_sending`). An exhausted budget never raises to the caller: it becomes a `limitations` entry in the report and the task ends `partial`.

## 5. Checkpoints, resume, cancellation, leases

**Checkpoints.** `web_tasks.checkpoint` (JSON) holds `stages_done`, `usage`, `failures`, `limitations` and the stage outputs in section 3. It is rewritten in one short transaction after each stage and after each document; no write transaction is held across network I/O.

**Resume** (`orchestrator.resume`, worker recovery):

- Allowed for a `queued`-with-checkpoint, running (lease expired) or `failed` task. `completed`, `partial` and `cancelled` tasks raise `Conflict`; submit a new task instead.
- Stages in `stages_done` are skipped. Discovery output is reused, not re-searched.
- Documents already read are **re-read from the public cache** by content hash and extracted again; API records are restored from the checkpoint. They carry `retrieval_mode: "cache"`, so a resumed task whose documents were all read before the interruption reports `live: false`, and every source says `cache` (`test_resume_after_a_crash_at_reading_uses_cached_documents`, which also asserts that no page is fetched again). If a body has been pruned, that document is dropped and may be fetched again when reading re-selects it.
- Time already spent counts against `max_seconds`.

**Cancellation** (`orchestrator.cancel`, any connection):

- A queued task, or one whose lease is not live, becomes `cancelled` at once (`test_cancel_of_a_queued_task_is_immediate`).
- A running task gets `cancel_requested = 1` and a `cancel_requested` event. The run checks the flag before every network step, stops, builds a **partial report** from what it has (limitation `cancelled by request: partial report`), and ends `cancelled`. No consolidation happens, so nothing reaches knowledge (`test_cancel_from_another_connection_stops_before_the_next_fetch`).

**Partial results.** Reports from `partial` and `cancelled` tasks have the same shape as completed ones. Their `limitations` list says what was cut.

**Leases.** `LEASE_SECONDS = 600`. `_claim` sets `lease_holder` (`<host digest>:<pid>:<thread>:<process start time>`) and `lease_expires_at`; each stage change and checkpoint renews it, and so does every fetch and search round (`_Run.renew`), so a long stage cannot outlive the lease while it is still making progress. A second worker raises `LeaseHeld` while the lease is live. `scheduler.claim_next` takes a queued task (skipping proposals and live leases), else a running task whose lease expired, and resumes it (`test_worker_drains_the_queue_and_recovers_expired_leases`).

**Dead workers.** `services.holder_alive` tells whether a holder on this host is still running: the pid exists with the same start time, so a reused pid is not mistaken for the crashed worker. A holder on another host, or in the older three-part form, is unknown. A task whose holder is dead on this host is treated as not leased: `cancel`, `resume` and `requeue` act at once and `scheduler.claim_next` takes it from its checkpoint without waiting for the 600 s lease. Task JSON carries `worker_alive` (`true`, `false`, or `null` when nobody holds it or the holder is on another host) and `origin_label` (`test_a_dead_workers_task_is_cancelled_resumed_or_requeued_at_once`, `test_the_worker_recovers_a_dead_workers_task_without_waiting_for_the_lease`).

**Configuration re-check.** `_Run.check()` re-reads the owner's web configuration (one `meta` row) at most every `CONFIG_RECHECK = 2.0` seconds. Turning web access off stops the run `partial` with "Web access was turned off during the run" (`WEB_OFF`); a domain added to `deny_domains` is skipped from then on (`test_turning_web_access_off_mid_run_stops_the_task_partial`, `test_a_domain_denied_mid_run_is_never_fetched`).

**Duplicates and requeue.** An identical submission (same objective, mode and origin) while one is queued or running returns that task with `deduplicated: true` and queues nothing. `requeue` of a partial or cancelled task returns the child already queued rather than creating another (`test_requeue_is_idempotent_and_identical_submissions_are_deduplicated`).

**`executed_on`.** Recorded at claim time: `{host: sha256(hostname)[:12], cpus, load}`. The host name itself is not stored. On a deployment this identifies the Spark; Pocket devices and the console only submit and read (ARCHITECTURE §7).

## 6. Episodic events (`web_events`)

One row per event, in order. Kinds the committed code writes:

| Kind | When |
|---|---|
| `submitted`, `started`, `resumed` | Task creation; lease taken; lease taken on resume |
| `stage` | Entering a stage (`detail.stage`) |
| `decision` | `needs_web` and reasons; dropped queries; `disclosed_under_authorization` (count and finding kinds); render skipped (with reason); worker `claimed`; proposal authorised |
| `query` | One per planned query, with `kind`, `purpose`, `round` |
| `search` | Result count, attempt count, first failure reasons per round |
| `fetch` | Each document recorded: `source_id`, kind, retrieval mode, flags, injection count |
| `render`, `crawl` | A rendered page; a followed link |
| `error` | A `FetchError` or stage failure |
| `extract` | Per document: URL, kind, claim count; for a memory-answered task `from_memory` with the knowledge ids |
| `claim` | Counts of claims, clusters and cluster statuses |
| `synthesized` | Answer items, findings, sources and `live` of the report |
| `consolidated` | Consolidation summary counts |
| `cancel_requested`, `cancelled`, `completed`, `partial`, `failed` | Terminal and control events; the terminal event carries `live`, source count, limitation count, error |
| `finding` | A monitor run found a meaningful change (scheduler) |
| `prediction`, `prediction_error`, `feedback` | Prediction lifecycle (task id may be null) |

The UI stage line (Discovering, Reading, Evaluating, Synthesizing, Completed) is derived from `stage` events.

## 7. A real report

Produced offline at `df00137` (before the UAT fixes added `related`, `copies` and `description` to findings and contradictions; the numbers and wording below are otherwise unchanged) with `FixtureWeb` (three fake public hosts), `CorpusProvider` and `services.build`, exactly as `tests/webcortex/test_orchestrator.py` does, with question *"What is the latest version of the Zephyr compiler?"* at `T0 = 1795200000` (the fixture clock). Trimmed; `contradictions` and `verdict` were empty; the event list is written in compact `kind:stage` notation.

```json
{
 "task": {"mode": "technical", "status": "completed",
          "stages_done": ["discovering", "reading", "evaluating", "synthesizing"],
          "usage": {"queries": 3, "requests": 6, "pages": 3, "bytes": 1105, "browser_sessions": 0, "seconds": 0.0}},
 "events": ["submitted","started","stage:discovering","decision","query","query","query","search",
            "stage:reading","fetch","fetch","fetch","stage:evaluating","extract","extract","extract","claim",
            "stage:synthesizing","synthesized","consolidated","completed"],
 "assessment": {"needs_web": true, "mode": "technical", "freshness_class": "release",
   "reasons": ["recency requested: latest",
               "technical question (version): registries and advisories change",
               "unfamiliar entity: Zephyr",
               "no fresh, uncontested knowledge answers this"]},
 "report": {
  "question": "What is the latest version of the Zephyr compiler?", "mode": "technical", "live": true,
  "answer": [
   {"type": "template", "status": "summary", "citations": [1, 2, 3],
    "text": "3 sources read; 3 statement clusters had usable evidence; the top statement is corroborated (3 agreeing independent source groups of 3). Newest cited source: 9 hours ago."},
   {"type": "quote", "status": "corroborated", "citations": [1, 2, 3], "confidence": 0.772727,
    "uncertainty": 0.180339, "independent_sources": 3,
    "text": "The Zephyr compiler version 4.2.0 was released on 17 November 2026 by the Zephyr project team."},
   {"type": "quote", "status": "single_source", "citations": [3], "confidence": 0.642857,
    "text": "Developers report that the compiler now supports more targets."},
   {"type": "quote", "status": "single_source", "citations": [1], "confidence": 0.642857,
    "text": "The release adds faster incremental builds for large embedded projects."}
  ],
  "uncertainties": ["Single source: \"Developers report that the compiler now supports more targets.\" is reported only by tech-two.example [3].",
                    "Single source: \"The release adds faster incremental builds for large embedded projects.\" is reported only by news-one.example [1]."],
  "limitations": ["Extractive synthesis: statements are quoted from sources, not generated; contradiction detection covers numbers, versions, dates, polarity and explicit opposites only (ADR-022)"],
  "freshness": {"class": "release", "window_seconds": null, "ttl_seconds": 604800.0,
                "required_seconds": 604800.0, "satisfied": true, "newest_source_at": 1795165200.0},
  "sources": [
   {"n": 1, "title": "Zephyr compiler 4.2 released", "url": "http://news-one.example/zephyr", "retrieval_mode": "live", "kind": "page"},
   {"n": 2, "title": "Hands on with the Zephyr compiler", "url": "http://blog-three.example/zephyr-review", "retrieval_mode": "live", "kind": "page"},
   {"n": 3, "title": "Zephyr 4.2 is out", "url": "http://tech-two.example/news/zephyr-42", "retrieval_mode": "live", "kind": "page"}
  ],
  "consolidation": {"provisional": [1], "pending": [1], "strengthened": [], "unchanged": [], "superseded": [], "contested": [],
    "held": [{"statement": "The release adds faster incremental builds for large embedded projects.",
              "reasons": ["single source that is not a primary structured source"]},
             {"statement": "Developers report that the compiler now supports more targets.",
              "reasons": ["single source that is not a primary structured source"]}]},
  "budget": {"max_queries": 12, "max_requests": 120, "max_pages": 10, "max_seconds": 240, "max_depth": 0, "max_browser_sessions": 0}
 }
}
```

What the example shows: three independent hosts agree, so the top cluster is `corroborated` with posterior mean 0.7727 (section 3 of EVIDENCE_AND_PROVENANCE); the two other statements are single-source; page headings and titles are not claims (`text.heading_like`), so only body sentences appear; the one corroborated cluster became provisional knowledge #1 with an owner question, and the single-source clusters were held as claims only. Report shape: ARCHITECTURE §5.

## 8. Recovery procedures

Commands are named in ARCHITECTURE §4.5; flags and service units are in RUNBOOK.

| Situation | Check | Action |
|---|---|---|
| Task stuck in `discovering`, `reading`, `evaluating` or `synthesizing` | `nsf web show` for the task: `lease_expires_at` and last event time | If the lease has expired (up to 600 s after the last checkpoint), the worker resumes it by itself, or run `nsf web resume <id>`. While the lease is live, resume raises `LeaseHeld`: wait, or `nsf web cancel <id>` (sets `cancel_requested`; the holder stops at its next network step) |
| Worker crashed mid-task | Events end without a terminal kind; `worker_alive: false` in the task JSON | On the same host `cancel`, `resume` and `requeue` act at once and the worker service takes the task over without waiting for the lease. Otherwise as above; the checkpoint keeps documents, so no page is re-fetched while the cache still holds the body |
| Task `failed` | `error` field and the `error` events from `nsf web evidence <id>` | `nsf web resume <id>` continues a failed task from its checkpoint here; `nsf web requeue <id>` queues it again for the worker (same id). If the cause is permanent, `nsf forget research <id>` and submit again |
| Task `failed` because every provider or page failed | `limitations` names the failures | `nsf web requeue <id>` once the network is back; it searches again |
| Web access turned off while a task runs | Task ends `partial`, "Web access was turned off during the run" | Re-enable with `nsf web config`, then `nsf web requeue <id>` |
| Task `partial` or `cancelled` | `limitations` in the result | Not resumable. `nsf web requeue <id>` (console: resume) queues a linked child task (`parent_id` = the task) with the same objective, mode, context and owner authorisation; documents read before are re-requested conditionally. Or raise the budget (up to the ceiling) and submit a new task, or accept the partial report |
| Provider or engine outage | `nsf web health`; `registry.status()` shows `open` circuits and last errors | Nothing to do for a short outage: breakers open after 3 failures, retry after 300 s, and other engines or providers answer meanwhile. For a long one, `nsf web daemon status` / `start`, or edit `engines` / `providers` with `nsf web config`. Reports from degraded runs list each failure under `limitations` |
| Web access switched off while tasks are queued | `Conflict: Web access is disabled ...` | Tasks stay queued; re-enable with `nsf web config` |
| Proposal waiting | `nsf resolve` question with scope `web:research:<id>` | `--confirm` queues it; `--reject` cancels it |

`orchestrator.requeue(db, task_id, now, *, origin=None, family=None)` accepts `failed` (same id, status `queued`, the worker resumes from the checkpoint), `partial` and `cancelled` (new child task); any other status raises `Conflict`. The result carries `resumed_from` and `requeued` (`same` or `child`) (`test_requeue_semantics_and_feedback_reader`, `test_feeds_cache_and_requeue_commands`).

Autonomy (monitors, `tick`, daily caps) is in MEMORY_INTEGRATION §8.

## 9. How to verify

```sh
pytest -q tests/webcortex/test_orchestrator.py tests/webcortex/test_intent.py tests/webcortex/test_state.py
pytest -q tests/webcortex/test_scheduler.py -k "worker or monitor"
pytest -q tests/webcortex -k "resume or cancel or budget or lease or requeue"
```

## 10. Limits stated plainly

- Mode and intent rules are lexical and English-only; a question the cues miss becomes a `quick_lookup` with a 3-query budget.
- `max_seconds` bounds when the next step may start, not the step itself.
- `resume` after the public cache is pruned re-fetches live; the task then mixes `cache` and `live` sources.
- **Recorded deviations from API_CONTRACTS §0 (Time).** The contract says nothing below the CLI or HTTP layer reads the wall clock. Two reads remain, both outside research results: `orchestrator._Run` throttles its configuration re-check with `time.monotonic()` (a duration, not a date, so it cannot affect a report or a test clock), and `PublicCache` names a quarantined file `<name>.corrupt-<unix time>` with `time.time()` (a file name, never stored data). Each other date is passed in as `now` (`services.clock`).
- Self-reference stripping is lexical: a local project referred to by a name the owner did not list in `private_terms` (and that is not `NeuroSciFi`) is still searched.
- `intent.covers` compares 5-letter stems; a paraphrase that shares fewer than 60 % of the question's topical stems sends the question to the web even if memory holds the answer.
- A resumed or memory-answered report is `live: false` by construction, even if the knowledge it used was verified minutes before.
