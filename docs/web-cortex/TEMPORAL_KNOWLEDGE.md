# Web Cortex: temporal knowledge

Status: describes the code committed at `5f09ac3` (`temporal.py`, `knowledge.py`, `consolidate.py`, `predictions.py`, `curiosity.py`, `instance/governance.py`). Test names are given where a property is verified. Contracts: [ARCHITECTURE.md](ARCHITECTURE.md) §4.3-4.4, §6. Decision: ADR-026. Related: [EVIDENCE_AND_PROVENANCE.md](EVIDENCE_AND_PROVENANCE.md), [MEMORY_INTEGRATION.md](MEMORY_INTEGRATION.md).

All times are Unix seconds in UTC. Nothing in `temporal.py` reads the clock: relative expressions ("yesterday", "2 days ago") resolve only against an explicit `now` or reference date. A date without a zone is UTC.

## 1. The time fields

A statement from the web has several different times. The code keeps them apart.

| Field | Where | Meaning | Source |
|---|---|---|---|
| `event_at` | `web_claims` | When the thing the sentence describes happened ("released on 17 November 2026") | First date mentioned in the sentence (`temporal.event_dates`); partial or relative dates resolve against the publication date |
| `published_at` | `web_claims`, `web_sources` | When the source was published | Page metadata, in the priority order of SEARCH_AND_ACQUISITION §7; structured records use the registry's own date |
| `modified_at` | `web_sources` | When the source says it was modified | JSON-LD, meta, PDF; never used as the publication date |
| `retrieved_at` | `web_claims`, `web_sources` | When this system fetched the bytes | The fetcher's clock |
| `verified_at` | `web_claims` | When the claim was last written by an evidence stage | Task clock at save time |
| `first_verified_at`, `last_verified_at` | `web_knowledge` | When the knowledge item was created, and when a task last confirmed it | Consolidation; `last_verified_at` moves forward when a later task finds the same values again |
| `valid_from`, `valid_until` | `web_knowledge` | The interval over which the statement is believed to hold | Section 6 |

Effective time of a claim (`temporal.effective_time`): its `event_at`, else its `published_at`. An undated claim has no effective time; its `retrieved_at` is only an upper bound for when it was written.

Date parsing (`parse_date_precision`) accepts ISO dates and datetimes, `2026/11/03`, month and year forms, RFC 2822, "3 November 2026", "Nov 3, 2026", a bare year, and (only with `now`) relative expressions. Impossible dates ("31 February") return nothing. A bare year is returned only with its precision (`year`); `parse_date` refuses it, because a year is too coarse to stand in for a publication time (`test_parse_date_table`, `test_parse_date_rejects_impossible`, `test_bare_year_only_via_precision_and_relative_needs_now`, `test_event_dates_absolute_partial_and_relative`).

## 2. Freshness classes

Every question and every knowledge item has one class. The TTL is how long a verification stays fresh.

| Class | TTL | Intended for | Note |
|---|---|---|---|
| `breaking` | 3 hours | Developing events, outages, incidents | Age discounts evidence |
| `volatile` | 2 days | Prices, status, current office holders | Age discounts evidence |
| `release` | 7 days | Software versions, releases, advisories | Age discounts evidence |
| `scientific` | 30 days | Research findings | Staleness means "re-verify", no discount |
| `stable` | 365 days | Definitions, history | Staleness means "re-verify", no discount |

`tests/webcortex/test_temporal.py::test_freshness_policy_table` pins the table.

**How a class is chosen.** The orchestrator uses `assessment["freshness_class"]` from `intent.assess` (RESEARCH_ORCHESTRATION §1). `temporal.classify` is the fallback when no assessment exists; it has its own cue table (breaking, then release, then scientific, then volatile, else stable) and prefers a valid class in the assessment (`test_classify_prefers_assessment_and_ignores_invalid`). The two cue tables are written separately and can disagree on a borderline question; the assessment wins whenever it is present.

**Owner override.** `WebConfig.freshness_ttl` maps a class to a TTL in seconds (keys must be class names, values positive). It is applied to knowledge staleness, prediction check times and curiosity (`knowledge.staleness`, `orchestrator._predict`, `predictions.check`, `curiosity.rank`). It is **not** applied to the evidence weights in `claims.score` or to the report's freshness requirement (`synthesis` uses the class default); an override therefore changes when knowledge is called stale, not how a report weighs a source (`test_ttl_overrides_and_unknown_class`).

## 3. Age and the evidence weight

Only for `breaking`, `volatile` and `release`: a dated source older than the class TTL has its weight halved; an undated source is weighted 0.75 in every class. Numbers and rationale: EVIDENCE_AND_PROVENANCE §3. This is a statement about the *evidence*, applied at scoring time.

## 4. Staleness decisions

Staleness of a knowledge item is **computed on read** (`knowledge.staleness`), never stored by a timer:

1. Status `stale` is set only by forgetting: when part of an item's supporting claims is removed (section 8). Reason: "marked stale: part of its support was removed".
2. Otherwise `temporal.is_stale(last_verified_at, freshness_class, now)`:
   - never verified: stale;
   - `now - last_verified_at` greater than the class TTL (override applied): stale, with a reason such as "stale: last verified 9 days ago; the release class allows 7 days";
   - otherwise fresh, with the age and limit stated. A verification time more than a minute in the future is fresh with a clock-skew warning.

`is_stale` also takes `newer_release_seen` and `revision_seen` flags that force "stale" with a stated reason (`test_is_stale_overrides_and_events`). **At `5f09ac3` no production code passes them.** A newer release or a new paper revision is therefore noticed indirectly: a refresh finds the newer value, and consolidation supersedes the old row (section 5). Until that refresh runs, the old row is fresh until its TTL.

`knowledge.query` returns each item with `stale`, `stale_reason`, `last_verified_at`, `valid_from`, `valid_until`, `provisional`, `contested` and its version list, so a caller never has to guess (`test_query_ranks_matches_and_reports_freshness_provenance_and_status`). `intent.assess` treats stale or contested knowledge as a hard reason to go back to the web.

**Windows.** `temporal.window_ok(published_at, window, now)` is true when the source falls inside the window, `None` when undated, and false when older, or dated more than one day in the future (`FUTURE_SKEW`): a misparsed or scheduled date is not evidence of freshness (`test_window_ok`). Reports use it for the freshness requirement (EVIDENCE_AND_PROVENANCE §9).

## 5. Supersession versus contradiction

Two claims about the same subject that disagree are one of three things. The rules are in `claims.conflict` and `temporal.supersedes`; consolidation applies them to knowledge.

| Relation | Condition | Result |
|---|---|---|
| **Same** | Same values and polarity | Duplication: the existing item is refreshed (`last_verified_at`, supporting claims, sources), never duplicated (`test_duplicate_evidence_strengthens_and_never_duplicates`) |
| **Series** | The dates/years *and* the other values both differ (revenue in 2024 vs 2025) | Not a contradiction. If the new statement's effective time is later, it updates the item as a new version; an older one is held, with the reason "older statement of a series than knowledge #N" (`test_later_statement_of_a_series_updates_and_an_older_one_is_held`) |
| **Conflict** | Values differ in one group, or polarity differs | `temporal.supersedes(new, old)` decides |

`supersedes(new, old)` is true only when: same key; the new claim has an effective time (an undated new claim never supersedes); that time is **strictly later** than the old one's (an undated old claim is bounded by its retrieval time); and the values or polarity conflict (`test_supersedes_newer_conflicting_value`, `test_supersedes_event_time_wins_over_publication`, `test_supersedes_requires_conflict_same_key_and_strictly_later`, `test_supersedes_never_from_undated_new_and_bounds_undated_old_by_retrieval`).

In consolidation:

- **Newer and meets the provisional bar** (independent sources, confidence, no contradiction: MEMORY_INTEGRATION §3): a new version row is inserted as `provisional`; the old row becomes `superseded`; the old row's `valid_until` is set to the new row's `valid_from` and `superseded_by` points at the new row; the old row's open question is closed; a `supersedes` link and a `reversed` feedback row are written (`test_newer_conflicting_value_supersedes_with_a_new_version`).
- **Newer but below the bar**, or **not newer / undated**: the old row stays. The disagreement is recorded as a contradiction (the new claim is appended to the item's `contradicting` list, a `contradicts` link is stored, the item's confidence is multiplied by `1 - cluster_confidence / 2`, an owner question is opened or extended, feedback `weakened`) (`test_older_conflicting_value_is_recorded_as_a_contradiction`).
- **A contested cluster with no matching knowledge** becomes an informational question `web:contested:<task>:<n>`; nothing is stored as knowledge (`test_contested_cluster_becomes_a_question_not_knowledge`).

In reports (`synthesize`), a contradiction between two sources is called a *leaning* toward the later one only when the reason is a changed value and one side's effective date is later; the text states that basis and adds "Not independently confirmed". Otherwise it says the evidence does not establish which is correct. Opposite polarity and cue-pair conflicts are never resolved by date.

## 6. Validity intervals

`temporal.validity(claim, now)` returns `{valid_from, valid_until, basis}`. For a new knowledge item, `consolidate._validity` applies it to the newest event, publication and retrieval times among the supporting claims:

| Basis | `valid_from` |
|---|---|
| The sources state an event date | The latest event date |
| Else a publication date | The latest publication date |
| Else (undated) | The retrieval time. The true start is earlier; the basis text says so |

`valid_until` is open (`NULL`) until the row is superseded. A start date more than a day in the future is noted in the basis. `test_validity` covers each case.

## 7. Versioned knowledge rows

`web_knowledge` has `UNIQUE(key, version)`. A new value for a key is a new row with `version + 1`; the older row keeps its text, confidence and validity interval and gains `superseded_by`. **Rows are never overwritten.** Status values:

| Status | Meaning | Reviewable by the owner |
|---|---|---|
| `provisional` | Created by consolidation; waiting for the owner | yes |
| `accepted` | The owner confirmed it (`nsf resolve --confirm`, or `nsf web review`) | yes |
| `stale` | Part of its support was forgotten; needs re-verification | yes |
| `superseded` | A newer version exists (`superseded_by`) | no |
| `archived`, `rejected` | Owner decisions; kept for history | no |

`knowledge.versions(db, key)` lists the chain with each version's validity interval. When a stale item is re-verified by a later task (duplication path), it returns to `provisional`. `refresh` on a superseded item is refused: refresh the current version instead (`test_reject_and_superseded_items_cannot_be_reviewed`, `test_refresh_submits_a_fact_verification_task_without_running_it`).

**Forgetting the chain.** `governance.forget_knowledge` deletes every version connected by `superseded_by`. `forget_research` / `forget_source` may delete only the newest version; then the previous version is restored: `superseded_by` and `valid_until` cleared, status `stale`, note "its successor version was forgotten; needs re-verification" (`test_forget_knowledge_removes_the_whole_version_chain`, `test_forget_research_keeps_knowledge_with_other_support_as_stale`).

## 8. Never describe cache as live

The system distinguishes *when the bytes were fetched* from *whether this task fetched them*.

- Every source carries `retrieval_mode`: `live`, `api`, `feed`, `rendered` (fetched during this task) or `cache`.
- A conditional request answered 304 is reported as `cache`, although the server just confirmed the page. This errs on the cautious side.
- `orchestrator.resume` re-reads earlier documents from the public cache and marks them `cache`; a fully resumed task reports `live: false` (`test_resume_after_a_crash_at_reading_uses_cached_documents`).
- A report built from memory carries `live: false` and `memory.answered_from_memory: true`.
- A report whose cited sources are all `cache` says so three times: `"live": false`, a lead sentence ("All cited sources were served from cache."), and a limitation naming the age of the oldest ("Served from cache retrieved 2 days ago; not re-verified live") (`test_cache_only_report_is_not_live`).
- Knowledge returned to a caller always carries `last_verified_at` and the stale decision with its reason; it is never presented as a current fact without them.

## 9. Predictions

A prediction is a falsifiable expectation about a knowledge item, checked by a later refresh (`predictions.py`, table `web_predictions`).

1. **Record.** After consolidation the orchestrator records, for each new provisional item, the expectation "still holds at next refresh" with `check_after = now + TTL(class)` (override applied). `predictions.record` does the same for a chosen item and time (`web_events` kind `prediction`).
2. **Check.** `check_due` (called by `tick` only when `autonomous` is on, within the daily cap and at most 5 refreshes per pass) submits a `fact_verification` task for each due prediction (origin `refresh:<knowledge id>`). Nothing runs inside `check_due`; the worker or the tick runs the task. When the task is terminal, `check` reads its consolidation summary.
3. **Outcome.**

| Outcome | When | Effect |
|---|---|---|
| `confirmed` | The refresh strengthened or left unchanged the item (same values, same polarity) | The next prediction is recorded at `now + TTL` |
| `error` | The refresh superseded the item or contradicted it | `prediction_error` event; an owner question "Prediction failed ... Keep it as knowledge?" (scope `web:knowledge:<id>`) if the item is still reviewable |
| `inconclusive` | The refresh found nothing about the item, or the item no longer exists | Prediction closed; nothing else |

A prediction check is idempotent: a settled prediction is returned unchanged (`test_prediction_check_outcomes`, `test_tick_autonomous_refreshes_due_predictions_within_the_daily_cap`).

**How errors raise curiosity.** In `curiosity.rank`, an item with a prediction outcome `error` in the last 30 days has its importance multiplied by 1.5, and the candidate's reason says so. A due prediction is itself a candidate of kind `prediction`. The item then ranks higher for information gain per cost (MEMORY_INTEGRATION §7). `feedback` rows (`strengthened`, `weakened`, `unchanged`, `reversed`, `new`) record before and after confidence and the information gain in bits for every consolidation, which is also what the futility damping reads.

## 10. How to verify

```sh
pytest -q tests/webcortex/test_temporal.py tests/webcortex/test_knowledge.py
pytest -q tests/webcortex/test_consolidate.py tests/webcortex/test_curiosity.py
pytest -q tests/webcortex/test_scheduler.py -k prediction
pytest -q tests/webcortex/test_instance_integration.py -k forget
```

## 11. Limits stated plainly

- Staleness is time-based only. A fact that changed upstream an hour ago is fresh until its TTL unless a refresh finds the change.
- TTLs are policy defaults, not measurements of how fast a given topic changes. The owner can override them.
- `newer_release_seen` and `revision_seen` are not wired in the committed code (section 4).
- `freshness_ttl` overrides do not reach evidence weighting or report freshness (section 2).
- An undated source can support a statement but starts its validity at the retrieval time, which is later than the truth.
- Series detection is by comparing value groups; a sentence with unrelated extra numbers can be misread as a series or a conflict.
- Event dates come from regular expressions over English month names and relative phrases.
