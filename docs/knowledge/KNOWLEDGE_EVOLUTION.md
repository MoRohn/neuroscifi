# Knowledge evolution

Status: v0.7.0, working tree. This document describes how what the individual knows changes over time and how the system checks its own predictions. Decision: ADR-038 (draft `.omc/autopilot/adr/ADR-038-knowledge-evolution.md`). It builds on ADR-026 (governance of web knowledge), ADR-029 (web memory in the individual's file) and ADR-035 (claim verification, [CLAIM_VERIFICATION.md](CLAIM_VERIFICATION.md)). Code: `webcortex/evolution.py`, `webcortex/consolidate.py`, `webcortex/knowledge.py` (`describe`, `assert_fact`), `webcortex/predictions.py`, migration 8 in `instance/schema.py`, the forget cascade in `instance/governance.py`. Tests: `tests/webcortex/test_evolution.py`, `tests/webcortex/test_consolidate.py`. The separation from procedural learning and model adaptation is in [CONTINUAL_LEARNING.md](CONTINUAL_LEARNING.md).

Everything here is deterministic and reads only stored records. No model weights change. The binary-belief tables (`beliefs`, `belief_revisions`, `graph_revisions`) are never written.

## 1. The loop

| Step | What happens | Where |
|---|---|---|
| DISCOVER | Search, fetch, extract claims | orchestrator (unchanged) |
| UNDERSTAND | Cluster and score claims | `claims.cluster`, `claims.score` (unchanged) |
| VERIFY | The ADR-035 state of each cluster's representative statement | `evolution.cluster_verdict` |
| CONNECT | Typed edges between claims, sources, knowledge, intervals, entities, hypotheses, predictions | `evolution.link`, `record_knowledge_edges`, `record_entity_edges` |
| CONSOLIDATE | `supported` clusters become knowledge candidates for the owner; the rest become research opportunities | `consolidate.run` |
| PREDICT | Every research opportunity gets a testable prediction; every new knowledge item gets a "still holds" prediction | `evolution.open_hypothesis`, `evolution.predict`, `predictions.add` |
| REASSESS | Later verified evidence settles predictions; errors are analysed and revised; calibration is updated | `evolution.observe`, `predictions.check`, `evolution.calibration` |
| EVOLVE | Metacognitive metrics, the change history of a topic, procedural statistics | `evolution.metrics`, `evolution.changes`, `evolution.update_research_policy` |

## 2. Consolidation eligibility (ADR-035 alignment)

Before v0.7.0, consolidation used its own bar: cluster confidence of at least 0.72 from at least two independent sources, with every undated source weighted × 0.75 (ADR-026). The fact-verification verdict moved to per-category rules in ADR-035, so a stable fact backed by two undated sources was `supported` as a verdict but stayed out of knowledge (CLAIM_VERIFICATION §10). Consolidation now uses the same rules.

`evolution.cluster_verdict(cluster, now=, freshness_class=, ttl_overrides=, window=)` applies `claims.CATEGORY_POLICY` to one scored cluster, with the rules of `synthesis._verdict`:

- The statement is the cluster's representative. Its category comes from `temporal.claim_category`.
- Supporting claims are the cluster's `supporting` ids. Contradicting claims are the other side of each contradiction pair. Adversarial claims never count.
- Independent groups come from `claims.independence_groups`, so a copied sentence (3-word shingle Jaccard ≥ 0.8) or a syndicated story counts once. The weight of each group uses the category's undated factor.
- A time-sensitive statement counts only evidence dated inside the window: the question's `recency_window`, else the class TTL. With no timely evidence the result is `temporally_unresolved`.
- A package registry record (`kind == "metadata"`, `flags.record` = package or release) or the subject's official site (`claims.official_source`, not for quantities) supports the statement alone when nothing contradicts it.
- `claims.verification_state` decides. The result is `{state, category, category_reason, basis, confidence, independent_groups, timeframe, min_groups, policy: "ADR-035", verified_at, reason}`.

`consolidate.run` then treats each cluster by state:

| State | Outcome |
|---|---|
| `supported` | Candidate. It passes when the cluster has no contradiction pair and, for basis `corroboration`, when the owner's floors hold: at least `auto_provisional_min_sources` supporting groups and posterior at least `auto_provisional_min_confidence`. The floors can only raise the ADR-035 bar; values below it have no effect. A direct authoritative record (basis `official_source` or `registry_record`) needs no second group |
| `insufficient`, `temporally_unresolved`, `not_evaluated` | Held. Kept as a research opportunity: a hypothesis with a prediction (§5). Never knowledge |
| `conflicting`, `disputed` (cluster status `contested`) | A contradiction is recorded: against live knowledge as before (`_contested_against`), otherwise as a hypothesis (origin `conflicting` or `disputed`) with `claim -contradicts-> hypothesis` edges and the existing `web:contested:` owner question. No consensus is chosen |

Before any of this, claims from the sources the task's answer set aside (`report.set_aside`: another entity, another sense of the name, a record that never names the checked statement's subject) are removed and the cluster is re-scored, as synthesis does; a cluster made only of them is held ("read, not cited"). Unchanged from ADR-026: adversarial claims are removed next; a statement the owner rejected is never proposed again; only a specific, well-formed statement is proposed (R1-02, R2-04), and a guide, roadmap or listicle fragment is not one (R6 D10: a verbless heading before a colon that names a guide (roadmap, guide, checklist, tips, top 10, ...) with two or more undated items, a colon-led undated sequence of three or more steps joined by "then", "next", "finally" or arrows, or a numbered "Step N:"; such a cluster is held as a claim with the reason, while a definition or value after a colon, a dated timeline and a plain list of names keep their shape); a new row is only `provisional`; every new row gets an owner question (`web:knowledge:<id>`, within the question budget) and the owner alone accepts. The question now ends "verified by corroboration" (or the authority).

Measured effect (tests): two undated, independently worded sources for "The Corran Spire is in Harrowgate." give 2.6 / 3.6 = 0.722 and become provisional knowledge (`test_two_undated_sources_of_a_stable_fact_become_provisional_knowledge`). The subject's official site alone supports "Fennick was created by Oda Haraldsen." (`test_the_subjects_official_site_alone_supports_a_narrow_historical_fact`). A single structured record that is not a registry record stays `insufficient` even when the owner lowers `auto_provisional_min_sources` to 1. A PyPI package record supports "latest" on its own (`test_primary_structured_single_source_needs_the_source_threshold`). A "latest" statement from undated sources is `temporally_unresolved` and becomes a hypothesis (`test_unverified_clusters_are_held_as_hypotheses_with_a_testable_prediction`).

Behaviour changes to note:

- **Identical sentences on two hosts are one source.** Consolidation previously trusted the cluster's own source count. It now recounts groups with syndication joining, as the verdict does. The claim scorer already joined them, so this only differs for clusters built outside it, such as test fixtures, which now word their sources independently (`reworded` in `test_consolidate.py`).
- **A stale "latest" statement no longer contradicts knowledge.** An unverified cluster (for example a 30-day-old "latest is 2.2.0" with a 7-day release window) is held as a hypothesis that points at the knowledge item. Only verified (`supported`) or contested evidence records a contradiction or supersedes a version.
- **Stored `confidence` is still the cluster score.** `web_knowledge.confidence` is the scorer's posterior, as before. Owner questions and the console show it. The ADR-035 posterior is in `verification.confidence`.

## 3. Provenance kinds

Every record has one kind, and the kinds are never mixed:

| Kind | Where | How it is created |
|---|---|---|
| `raw_evidence` | `web_sources` (plus public-cache bodies) | Fetch |
| `provisional_claim` | `web_claims` and `web_hypotheses` | Extraction; held clusters; prediction errors; the owner's hypotheses |
| `verified_conclusion` | `web_knowledge.provenance` | Consolidation of a `supported` cluster |
| `user_asserted` | `web_knowledge.provenance` | `knowledge.assert_fact` / `nsf knowledge assert` |

`evolution.PROVENANCE_BY_TABLE` maps the tables. `knowledge.describe` returns `provenance`, `verification` and `times`. `evolution.hypotheses` returns `provenance: "provisional_claim"` on every row.

**User-asserted facts.** `assert_fact` stores the owner's statement as `accepted`, with confidence 0.5 (no web evidence either way) and verification `{state: "not_evaluated", basis: "owner_assertion"}`. It matches live knowledge by key, and by the key of each row's statement. When a live row states the same values, nothing is written ("already known"). When a live row states other values, the assertion becomes a **new version**: the old row becomes `superseded` with `valid_until` set, its questions close, and a `knowledge -supersedes-> knowledge` edge and a `reversed` feedback row are written. Web evidence never supersedes a `user_asserted` row. A conflicting verified cluster is recorded as a contradiction and the owner is asked, and the row's confidence is not lowered (`test_web_evidence_never_supersedes_a_user_asserted_fact`, `test_an_owner_assertion_supersedes_web_knowledge_with_a_new_version`).

## 4. Time and versions

| Time | Field |
|---|---|
| Publication | `web_knowledge.published_at`: the newest publication date of the supporting claims |
| Event | `web_knowledge.event_at`: the newest event date stated in the supporting sentences |
| Retrieval | `web_knowledge.retrieved_at`: the newest retrieval time |
| Verification | `first_verified_at`, `last_verified_at`, and `verification.verified_at` |
| Effective | `valid_from` (`temporal.validity`; never in the future), `valid_until` when superseded |

Strengthening moves `published_at` and `retrieved_at` forward and replaces `verification` with the newer `supported` verdict. Rows written before migration 8 get `published_at` and `retrieved_at` from their own `sources` JSON. Their `event_at` stays NULL: it was never recorded, and a date is never invented. Their verification is `{"policy": "ADR-026"}`.

**Versions are never destroyed.** A new value takes `consolidate.next_version` (`MAX(version) + 1` over every row of the key). The old row keeps its interval and points at its successor. Supersession writes `claim -supersedes-> claim` (as before) and `knowledge -supersedes-> knowledge`. Only forgetting removes versions (§8).

## 5. Hypotheses and predictions

**Hypotheses** (`web_hypotheses`, migration 8): `key, statement, origin, status, verification, claims, claim_values, polarity, alternatives, knowledge_id, parent_id, task_id, note, created_at, updated_at, resolved_at`.

- Origins: `insufficient`, `conflicting`, `temporally_unresolved`, `disputed`, `not_evaluated`, `gap`, `prediction_error`, `owner`.
- States: `open`, `supported`, `refuted`, `revised`, `abandoned`.
- An open hypothesis with the same key, values and polarity is refreshed (claims merged, latest verification), never duplicated.
- At most 20 hypotheses are recorded per consolidation (`MAX_HYPOTHESES_PER_TASK`).

**Predictions** (`web_predictions`, rebuilt in migration 8): a row is about a knowledge item **or** a hypothesis. It records `probability` (the forecast that the statement holds), `criteria` (JSON resolution criteria), `check_after` and `deadline`.

| Prediction | Made by | Probability | Settled |
|---|---|---|---|
| "still holds at next refresh" about knowledge | `orchestrator._predict` → `predictions.add` | The item's confidence | `predictions.check` after a refresh task: confirmed, error or inconclusive (unchanged rules) |
| "«statement» will be confirmed by independent evidence by DATE" about a hypothesis | `evolution.open_hypothesis` → `predict` | The ADR-035 posterior, clipped to [0.01, 0.99] | `evolution.observe` at the end of every consolidation; `expire` at the deadline |

`predict` validates the probability (0..1) and the deadline (future). The default deadline is the class TTL, at most 30 days (`MAX_HORIZON`). The default criteria name the key, values and polarity, and the confirmed, error and inconclusive conditions.

**Comparison when new evidence arrives** (`evolution.observe`). For each open prediction of an open hypothesis formed by another task, one cluster of this task is compared. A task created for a hypothesis (autonomous research records the hypothesis id in the task context, `{"hypothesis": id}`) matches that hypothesis by this explicit link: its `supported` or `disputed` cluster whose key names more than half of the hypothesis key's tokens (most first, then key similarity). Otherwise, as the fallback, the first cluster with an equal or near-identical key (token Jaccard ≥ 0.8) is used. The research question shifts which tokens a cluster key keeps, so the two keys can differ while naming the same subject. The observation records which rule matched (`match`: `linked` or `lexical`) and the cluster key:

| Observation | Relation to the hypothesis | Outcome |
|---|---|---|
| `supported` | same values and polarity | `confirmed`; the hypothesis becomes `supported`, linked to the new knowledge id |
| `supported` | conflict, or series (another reference period) | `error`; the hypothesis becomes `revised` |
| `disputed` | same values | `error`; the hypothesis becomes `refuted` |
| anything else | n/a | Tracked in `detail.observations` (last 10); the prediction stays open |

The deadline passing first gives `inconclusive`, and the hypothesis stays open.

**Error analysis and revision.** An `error` stores `detail.error_analysis`: `reason`, the expected and observed values and polarity, the forecast probability, and **alternative explanations**. The alternatives are template sentences chosen from the records: the hypothesis was wrong; the subject changed (conflict or series); the statements describe different periods (series); the observation rests on fewer than two groups; the original sources already disagreed. They are labelled "template analysis from the stored records". A `supported` contradiction creates a **revised hypothesis** stating what was observed (origin `prediction_error`, `parent_id` = the old one), with its own prediction. A failed knowledge prediction (`predictions.check`) is linked to its revision: the superseding version, or a hypothesis stating the contradicting claim (`evolution.knowledge_prediction_error`).

**Calibration** (`evolution.calibration`). The calibration covers every settled prediction with a probability, where `confirmed` = 1 and `error` = 0. `inconclusive` is counted but excluded, because nothing was observed. The output fields:

| Field | Meaning |
|---|---|
| `brier` | mean of (p − y)² |
| `base_rate` | share of `confirmed` outcomes |
| `brier_reference` | base·(1 − base) |
| `skill` | 1 − brier / reference |
| `bins` | 10 equal reliability bins: `{lower, upper, n, mean_probability, observed_frequency}` |
| `ece` | Bin-weighted \|mean forecast − observed frequency\| |

Every settlement writes a snapshot to `web_metrics` (kind `calibration`). `nsf knowledge calibration` shows the current calibration and its history.

**A forecast as read** (ADR-046; `evolution.recalibrations`, `recalibrate`). The calibration above scores forecasts; this is what is done with the score. Forecasts are of three kinds with unrelated base rates: `knowledge` (an item's confidence that it still holds at the next refresh), `hypothesis` (a derived hypothesis' posterior) and `owner` (the owner's own estimate). For each kind, the settled forecasts (the last 100; `inconclusive` excluded) give a mapping `logit q = logit p + shift + tilt × (logit p − centre)`, fitted with a prior that holds it to the stated forecast; with fewer than 20 settled forecasts of the kind it is the identity. The slope stays in [0, 2], so an order of forecasts is never reversed, and the result stays in [0.01, 0.99].

| What | Behaviour |
|---|---|
| Stored | Nothing. The mapping is computed from `web_predictions` when it is needed, so a prediction removed by a forget stops counting with the delete, and a restored copy reads from its own rows |
| Read by | `autonomy.candidates` only, and only for priority: the uncertainty signal of a due knowledge prediction, and the uncertainty signal and the information gain that ranks an open hypothesis. Whether a candidate is researched (`min_gain` and the other admission tests) is decided from the stated forecast (RESEARCH_POLICY) |
| Not changed | The stored forecast of any prediction, an item's confidence, verification states, the thresholds of provisional knowledge, answers, and `calibration` itself, which goes on scoring the forecasts as stated |
| Shown | `nsf knowledge calibration`, key `recalibration`: per kind the mapping, what it makes of 0.1 … 0.9, and how it would have fared on the individual's own settled predictions (each scored before its outcome is used) |
| Off | `nsf web config --set calibrated_forecasts=false` |

Example: 20 forecasts of 0.6 that all failed read the next 0.6 as 0.46, and 100 of them as 0.24; the plain frequency would say 0. The numbers (20, 100, the prior) were chosen on simulated forecasters and are reported on held-out ones: mean squared distance to the true probability 0.044 as stated, 0.017 as read; a forecaster that is already right pays 0.001; after a forecaster's rule reverses, the next 50 forecasts are read worse than stated until the window turns over (`docs/quality/evidence/open_items_calibration.json`, `scripts/bench_calibration.py`); dropping the settlements before a detected change was tried and not shipped, because it failed its held-out gate (ADR-048, `docs/quality/evidence/remaining_forgetting.json`, `scripts/bench_calibration.py change`). A settlement whose task the owner forgot no longer counts (ADR-048). Tests: `tests/webcortex/test_recalibration.py`. Test: `test_prediction_lifecycle_confirm_error_revision_expiry_and_calibration` checks the Brier score against an independently computed value.

## 6. Knowledge graph

Edges live in the existing `web_claim_links` table, which migration 8 rebuilds with typed nodes: `src_kind`, `dst_kind` ∈ `state.NODE_KINDS` = claim, source, knowledge, interval, entity, hypothesis, prediction, observation, error. Every pre-existing row is copied as claim → claim.

| Edge | Kind | Written by |
|---|---|---|
| claim → source (URL) | `derived_from` | New knowledge; new supporting claims |
| claim → knowledge (the claim is evidence for the conclusion) | `evidence_for` | Same |
| claim → hypothesis | `evidence_for` | `open_hypothesis` |
| claim → knowledge / hypothesis (contradiction) | `contradicts` | `_contradict`; contested clusters |
| claim → interval (the version's validity, with its publication, event, retrieval, verification and effective times) | `valid_during` | New knowledge (head claim) |
| entity → entity (named in the same claim; at most 10 per claim) | `related_to` | `_entities` |
| knowledge → knowledge | `supersedes` | Supersession; owner assertion |
| hypothesis → prediction | `predicts` | `predict` |
| prediction → observation (the observing task) | `observed` | `observe`, `knowledge_prediction_error` |
| observation → error (the prediction id) | `error_of` | Same |
| error → hypothesis / knowledge | `revised_to` | Same |

`state.links(db, task)` (the evidence view, `nsf web evidence`) still lists only claim → claim links. `evolution.graph(db, kind, id, depth=1..3)` walks typed edges in both directions, up to 500 edges (`nsf knowledge graph knowledge:12`).

Limit: `related_to` records co-mention, not a typed relation. There is still no entity disambiguation (MEMORY_INTEGRATION §4).

## 7. Metacognitive metrics and the change history

`evolution.metrics(db, now, since=None, record=False)` (`nsf knowledge evolution [--since-days N] [--record]`) is computed only from records:

| Metric | Computed from |
|---|---|
| `verified_knowledge.gained` | `verified_conclusion` rows with verification state `supported` created in the window (also: owner-accepted, strengthened, pre-ADR-035 versions) |
| `gaps.opened / resolved / open` | `web_hypotheses` created, and resolved as supported, refuted or revised |
| `contradictions.recorded / clarified` | Contested feedback rows; resolved `web:contested:` questions plus supersessions plus resolved conflicting or disputed hypotheses |
| `predictions` | Outcome counts, open count, `accuracy` = confirmed / (confirmed + error), and calibration |
| `retrieval` | Share of finished tasks answered from memory (`result.memory.answered_from_memory`). `measurable: false` when no task finished |
| `research_efficiency` | Network requests of finished tasks (`usage.requests`) per verified claim (gained + strengthened). Null when nothing was verified |
| `information_gain_bits` | Sum of `web_feedback.info_gain` |

Stored-row counts are deliberately absent (`not_metrics`): more stored data is not more understanding. `record` writes a snapshot (`web_metrics` kind `evolution`).

**"What has changed in your understanding of X?"** `evolution.changes(db, topic, now, since=None, limit=5, phrase=False)` (`nsf knowledge changes TOPIC [--since-days N] [--phrase]`) ranks claim keys by BM25 over every version and hypothesis, in any status. For each key it returns:

- `versions`: statement, status, provenance, verification, and every time listed in §4.
- `current`: the live versions.
- `timeline`: dated events, sorted:
  - `version`: a version was recorded.
  - `superseded`.
  - `evidence`: each feedback row (new, strengthened, unchanged, weakened, reversed), with confidence before and after and bits.
  - `owner_decision`: resolved questions.
  - `hypothesis` and `hypothesis_<status>`.
  - `prediction_<outcome>`.
- `predictions`.
- `changed`.

`summary` is template text built from these records and labelled `generated_by: "template"`. With `phrase=True` and a configured, **local** inference gateway, the model is given only the returned timeline and asked to rephrase it. The result is labelled `generated_by: "model:<name>"` with "the records, not this text, are the evidence". A remote endpoint is never used for this, because memory is private. Any failure keeps the template, labelled as degraded (`test_model_phrasing_is_labelled_uses_only_the_records_and_degrades`).

**Console.** No console file changed. The knowledge view already receives `knowledge.describe` output, which now includes `provenance`, `verification` (state, category, basis, independent groups) and `times`. The suggested UI change: show a provenance badge (verified / owner-asserted), the verification state and basis next to confidence, and the five times in the detail pane. A "What changed" tab could call `evolution.changes`, and a "Metacognition" card `evolution.metrics`. Both are plain-JSON functions.

## 8. Forgetting and checkpoints

Forgetting still wins (ADR-029). The cascades in `instance/governance.py`:

| Forget | Also removes |
|---|---|
| `forget_research(task)` | The task's hypotheses, with their predictions and edges; typed edges of the task (`task_id`); `observation:<task>` edges. Predictions the task settled keep their outcome, but their observation and error analysis are replaced by `{"forgotten": true}` and their tracked observations from that task are dropped |
| Claims removed (research or source forget) | Hypotheses citing a removed claim; `related_to` edges citing it; every edge from or to it |
| `forget_knowledge(id)` / dropped versions | Edges touching `knowledge:<id>` and `interval:<id>`; the item's predictions with their `prediction` and `error` nodes. Hypotheses that pointed at it keep their own evidence and lose `knowledge_id` |

`checkpoints.restore` migrates the restored copy first, then replays the tombstones through the same functions, so a checkpoint taken before a forget never brings hypotheses, edges or observations back (`test_forgetting_knowledge_removes_its_edges_and_predictions_and_restore_replays_it`, `test_forgetting_a_task_removes_its_hypotheses_predictions_edges_and_observations`, which also checks that the forgotten statement's bytes are absent from the database file and its WAL). `web_metrics` holds numbers and provider names only, never statements, objectives or URLs, so forgetting leaves it in place. Procedural statistics are recomputed from the remaining records at the next update.

## 9. Migration 8

Migration 8 is forward-only and runs in one transaction with its `user_version` bump (`schema.migrate`). It does the following:

1. Adds `provenance` (CHECK verified_conclusion | user_asserted, default verified_conclusion), `verification` (JSON), `published_at`, `event_at` and `retrieved_at` to `web_knowledge`, and backfills them as described in §4.
2. Creates `web_hypotheses`.
3. Rebuilds `web_predictions`: `knowledge_id` becomes nullable; `hypothesis_id`, `criteria`, `probability`, `deadline` and `CHECK(knowledge_id IS NOT NULL OR hypothesis_id IS NOT NULL)` are added; every row and id is copied.
4. Rebuilds `web_claim_links` with typed nodes and the new edge kinds; every row and id is copied.
5. Creates `web_metrics`.

An interrupted run leaves the file at version 7, with nothing changed, and the next open replays the migration (`test_migration_8_is_atomic_and_replays_after_an_interruption`). A schema-7 file with knowledge, a prediction and links migrates with its data intact and keeps evolving: the next superseding version is v2 (`test_a_schema_7_file_migrates_forward_with_its_data_intact`). The voice migration (Lane V, ADR-037) follows as version 9.

## 10. Limits

- Lexical throughout: the category cues, keys and value comparison of ADR-035 and TEMPORAL_KNOWLEDGE §5. A hypothesis is matched to new evidence by key similarity, so a rephrasing with a different key is not recognised.
- A hypothesis prediction is settled only by a later research task about the same subject. Nothing schedules that research automatically. Curiosity and the autonomy lane decide what runs.
- Calibration is only as good as the number of settled forecasts. With few outcomes the Brier score and bins are noisy, and `n` is always reported.
- Document-level syndication (`claims.document_origins`) is computed during synthesis and not stored. Consolidation recounts groups from the claims' own origin flags and their sentences.
- Entity relationships are co-mentions only.
