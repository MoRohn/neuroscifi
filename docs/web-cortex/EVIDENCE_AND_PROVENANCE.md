# Web Cortex: evidence and provenance

Status: describes the code committed at `5f09ac3` (`claims.py`, `text.py`, `safety.py`, `synthesis.py`, `state.py`, migration 6). Test names are given where a property is verified. Contracts: [ARCHITECTURE.md](ARCHITECTURE.md) §4.3, §5, §6. Decision: ADR-022 (no model in the loop). Time handling: [TEMPORAL_KNOWLEDGE.md](TEMPORAL_KNOWLEDGE.md). Threats: [SECURITY_MODEL.md](SECURITY_MODEL.md).

There is no language model, no embedding encoder and no reranker. Everything below is lexical, rule-based and deterministic: the same documents and the same clock give the same claims, clusters, confidences and report. That makes the evidence stage testable and auditable, and it sets the limits listed in section 11.

## 1. Pipeline from document to report

```
Document ──▶ quarantine ──▶ extract_claims ──▶ cluster ──▶ score (Beta) ──▶ synthesize ──▶ report
 (untrusted)  (injection)    (sentences)        (subject)    (independent       (quotes and     (cited,
                                                              groups)            templates)      honest)
```

Rank, popularity and search score never enter this pipeline after discovery ordering.

## 2. Claim extraction (`claims.extract_claims`)

A claim is **one sentence of a source**, or a templated statement over a structured API record. Nothing is paraphrased.

1. API records first: `_metadata_statements` builds templates such as `OSV lists advisory GHSA-... for <package>: <summary>; fixed in version ...`, `GitHub lists release <tag> of <repo>, published <date>.`, and `arXiv record: "<title>" (v2), dated <date>, a preprint (not peer reviewed).` A template that carries injection-like text is dropped (`test_metadata_with_injection_is_dropped`).
2. Then every sentence of `doc.text` (for an API record with no text, its abstract) that is 6 to 80 words, not boilerplate or navigation (cookie banners, "subscribe", "all rights reserved", breadcrumb separators), and carries **no injection finding** (`test_extract_claims_skips_injection_and_irrelevant_sentences`).
3. Each candidate is scored with BM25 against the question, plus 0.3 x the title's overlap with the question, then multiplied by 1.15 if it carries values and 1.10 if it carries a dated event. `relevance = raw / (raw + 2.0)`. Structured records have a relevance floor of 0.5. A candidate with relevance 0 is discarded; at most 8 claims per document are kept (`test_extract_claims_respects_max_and_length_bounds`).

| Field | Meaning |
|---|---|
| `id` | `sha256(source canonical URL + "\n" + normalised text)[:24]`. The normalised text masks numbers and versions (section 11) |
| `kind` | `metadata` for API templates. For sentences: `interpretation` if hedged ("suggests", "may", "we propose", "likely"); else `finding` if it reports a result ("we found", "results show", "increased by 12", `n = 40`, `p < 0.05`); else `statement`. Hedging wins over result cues: a hedged result is not a finding (`test_claim_kind_cues`) |
| `polarity` | -1 if the **main clause** is negated ("did not", "no evidence", "false", "debunked", "failed to"), else +1. A leading subordinate clause is skipped and the clause is cut at ", which", ", although", "; ". "not only", "no doubt", "no later than" are exempt (`test_polarity_cues_and_exemptions`) |
| `values` | Canonical strings from `text.values`: versions (`v2.3.5`), dates (`2026-11-17`), years, percents, money (`USD 1.2e+09`), quantities with a unit (`24 participants`), plain numbers |
| `event_at` | First date mentioned in the sentence (`temporal.event_dates`), resolved against the publication date for partial or relative forms |
| `key` | Subject signature: the 4 best content tokens, stemmed and sorted. Question terms score +3, entity-like tokens (capitalised, mixed alphanumeric, upper case) +2, noun-like suffixes +1, others 0.5. Numbers, versions, negations, hedges, direction words (rose, fell), reporting verbs and time words are excluded, so "rose 5 %" and "fell 3 %" about one subject share a key (`test_key_ignores_values_direction_and_hedges`) |
| `flags` | `source_type` (`primary` for structured records fetched for the task, else `secondary`), `retrieval_mode`, `doc_kind`, `observed_at` (retrieval time of a live API record), `subject` (question terms in the key), `truncated` |

PDFs, feeds and plain text pass through the same sentence path.

## 3. Confidence: a Beta posterior over independent groups

**Independence groups** (`independence_groups`): claims from the **same registrable domain**, or whose raw texts have shingle Jaccard of at least 0.8 (3-word shingles; syndication, copying), collapse into one group. Ten reports on one domain count once (`test_same_domain_repetition_does_not_add_confidence`, `test_syndicated_copy_on_second_domain_counts_once`).

**Positions.** Within a cluster, claims are partitioned greedily (most relevant first) into maximal sets of mutually compatible claims. The *representative* position is the one with the most independent groups; ties go to the newer effective date, then higher relevance. In fact verification the claim to be checked is the target and agreement is measured against it instead.

**Update.** Prior Beta(1, 1). For each independent group with `k` members of which `a` agree with the representative position, let `w` be the largest evidence weight among its members:

```
alpha += w * a / k          beta += w * (k - a) / k
confidence  = alpha / (alpha + beta)             (posterior mean)
uncertainty = sqrt(alpha*beta / ((alpha+beta)^2 * (alpha+beta+1)))   (posterior sd)
```

**Evidence weight** `w` (constants in `claims.py`):

| Factor | Value |
|---|---|
| Ordinary page (secondary) | 0.8 |
| Structured API record fetched for the task (primary) | 0.9 |
| Undated source (no `observed_at`, `published_at` or `event_at`) | x 0.75, in every class |
| Dated, but older than the class TTL | x 0.5, **only** for `breaking`, `volatile`, `release` |

Age discounts evidence only where age makes a statement less likely to be true: a 2019 finding is not less true than a 2026 one, and a definition does not rot. For `scientific` and `stable` classes staleness means "re-verify" (reported on the knowledge item, TEMPORAL_KNOWLEDGE §4), not a discount (`test_age_discounts_only_time_sensitive_classes`, `test_stale_and_undated_sources_lower_confidence`).

What **never** counts: search rank, provider score, how many engines returned the URL, citation counts (OpenAlex `cited_by_count` is metadata only), page views, domain reputation. The only source-level prior is the two reliability constants above (`test_rank_popularity_and_search_score_never_change_confidence`, `test_primary_records_weigh_slightly_more`).

Resulting numbers (dated, same subject, agreeing, no contradiction; computed from the constants and verified with `claims.score`):

| Independent agreeing groups | Mean |
|---|---|
| 1 secondary | 0.643 |
| 2 secondary | 0.722 |
| 3 secondary | 0.773 |
| 4 secondary | 0.808 |
| 2 primary | 0.737 |
| 3 primary | 0.787 |
| 3 undated secondary | 0.737 |

**Consequence:** the default consolidation floor is 0.75 (`auto_provisional_min_confidence`), so two ordinary pages never reach it; three do. See MEMORY_INTEGRATION §3.

`test_more_independent_agreement_raises_confidence` and `test_contradiction_lowers_confidence` cover the direction of both effects.

## 4. Clustering (`claims.cluster`)

A claim joins the most similar existing cluster if the key-token Jaccard is at least 0.5 **or** the shingle Jaccard of the normalised texts is at least 0.6; else it starts a new cluster. Claims are visited in relevance order (ties by id), so the result does not depend on input order (`test_cluster_is_deterministic_regardless_of_input_order`). Two claims whose question terms name different subjects (two package records in a comparison) never cluster (`different_subjects`, `test_different_subjects_do_not_cluster_in_comparisons`).

Cluster `status`:

| Status | Rule |
|---|---|
| `insufficient` | No claims, so no representative |
| `contested` | A contradicting pair exists, or a group mostly contradicts the representative |
| `corroborated` | At least 2 independent groups support the representative, and none contradicts |
| `single_source` | Otherwise |

## 5. Contradiction rules (`claims.conflict`)

Two claims **about the same subject** (similar by section 4) conflict when any of these holds:

1. **Opposite polarity**, one main clause negated.
2. **Values disagree** within one comparison group (`text.values_conflict`): numbers differ by more than 1 % relative, versions differ at all, dates differ by more than 1 day, money only within one currency, quantities only with one unit.
3. **Cue pair**: one side uses only one pole of increase/decrease, supports/refutes, approved/rejected, safe/unsafe, effective/ineffective, true/false, the other only the opposite pole.

**Series, not conflict.** When both the dates/years *and* the other values differ ("revenue 5 in 2024" vs "revenue 6 in 2025"), the statements describe different reference periods, so they do not conflict (`text.values_conflict`). A later value for the same subject may supersede (TEMPORAL_KNOWLEDGE §5).

Pairs from the same independence group are not listed; at most 50 pairs per cluster. Claims about different subjects are never compared. `test_conflicting_claims_are_contested_with_reason`, `test_contradictions_require_same_subject`.

## 6. Verdicts (fact verification only)

`synthesis._verdict` takes `assessment["claim"]` (or the question), finds the best-matching cluster (similarity, else word-stem overlap of at least 0.25), recomputes the posterior against the claim, and labels:

| Label | Rule |
|---|---|
| `supported` | Mean at least 0.75, at least 2 independent groups agree, none contradicts, no contradicting pair |
| `disputed` | More weight on the contradicting side than on the agreeing side |
| `contested` | At least one group agrees and at least one contradicts, but not `disputed` |
| `insufficient` | Anything else, including no matching cluster (confidence reported as 0.5) |

`reasons` lists the group counts, the posterior mean and the first conflicting sources with the rule that fired. A negated claim flips support to `disputed` (`test_verdict_labels`, `test_verdict_for_negated_claim_flips_support`, `test_verdict_insufficient_when_nothing_matches`). Because of the 0.75 floor, `supported` needs three ordinary independent sources or three primary records.

## 7. Prompt-injection quarantine (`safety.py`)

Retrieved text is data (ARCHITECTURE §3). `scan_injection` runs 17 regex patterns for text addressed to an AI agent: ignore or override instructions, role reassignment, "act as", system-prompt requests, developer-mode, chat-template tokens (`<|im_start|>`, `[INST]`), role markers, concealment from the user, exfiltration and "send data to ...", shell commands (`rm -`, `curl | sh`), tool-call JSON, direct address to an agent, base64 blobs over 200 characters, hidden characters, tracking-beacon images. Patterns target imperative, agent-directed phrasing so that "researchers ignored previous results" is not flagged (`test_injection_positives`, `test_injection_negatives`).

- `quarantine(doc)` returns a copy with each flagged sentence removed from `text` and `markdown`. `flags["injection"]` lists findings (`pattern`, an excerpt of at most 120 control-stripped characters, `offset`, `field`); `flags["quarantined_sentences"]` counts removals (`test_quarantine_removes_only_flagged_sentences_and_copies`).
- `acquire()` and `reread()` quarantine every fetched or re-read document. The claim extractor screens every sentence and every API template again, so API records are covered.
- A flagged sentence **never becomes a claim**. The report's `uncertainties` names the source, the pattern names and the number of sentences removed.
- Nothing in content can start a fetch outside the budgeted plan, create a monitor, change configuration or reach a tool. Output to agents and MCP is wrapped in `safety.untrusted()` envelopes (`test_untrusted_envelope`, `test_context_pack_respects_token_bound_and_wraps_untrusted`).

## 8. Claim-to-source traceability

Every accepted statement can be walked back to bytes:

| Step | Where | Key |
|---|---|---|
| Knowledge item | `web_knowledge` | integer `id`, `version`, `key`; `supporting` and `contradicting` hold claim ids; `sources` holds `{url, title, domain, published_at, retrieved_at}`; `task_id` is the task that created it |
| Claim | `web_claims` | `(task_id, id)`; `id` is the 24-hex claim id; `source_id` -> `web_sources.id`; `source_url`, `canonical_url`, `doc_sha256`, `sentence_index`, `cluster_status`, `confidence`, `flags` |
| Relation between claims | `web_claim_links` | `src`, `dst` claim ids; kind `supports`, `contradicts`, `supersedes`, `duplicates`; `detail` holds the reason or knowledge id |
| Source | `web_sources` | integer `id`, unique per `(task_id, canonical_url)`; `url`, `final_url`, `kind`, `published_at`, `retrieved_at`, `retrieval_mode`, `content_sha256`, `provider`, `flags` (injection findings, hidden-removed count, truncation) |
| Bytes | public cache `documents` | `sha256 = content_sha256`; may have been pruned, in which case the row and hash remain but the body does not |
| Episode | `web_events` | `task_id`, kind, stage, detail, time |

`orchestrator.evidence(db, task_id)` ("show your evidence") returns sources, claims (each marked `"untrusted": true`), links and the event log of a task. `memory.retrieve` exposes the same rows as the kinds `claim`, `source`, `knowledge`, `research` (MEMORY_INTEGRATION §5). In a report, `sources[].n` is a number local to that report (in order of first citation); `findings[].claims` lists the claim ids. The same claim id can appear in several tasks (it hashes the source and sentence), which is why `web_claims` is keyed by task as well.

## 9. What a report may and may not say (`synthesize`)

- **Quote or template.** Every `answer` item (after the lead) is a sentence quoted verbatim from a cited source (`type: "quote"`) or a template over measured quantities (`type: "template"`: counts, statuses, dates, a structured record's fields). Citations list every source that supports it. `test_answer_has_only_quotes_and_templates_and_limit` and the invariant check in `test_report_keys_and_invariants` assert that each quote occurs in a cited document.
- **At most 5 quoted statements**, ranked by `confidence x relevance x freshness factor` (1.0 inside the required window, 0.75 undated, 0.5 outside). Freshness changes ranking here, not the stored confidence.
- **`live`** is true only if at least one *cited* source was retrieved live in this task (`retrieval_mode` of `live`, `api`, `feed` or `rendered`). Otherwise the report says `"live": false`, the lead sentence says "All cited sources were served from cache", and `limitations` says how old the cache is (`test_cache_only_report_is_not_live`).
- **Freshness.** The required window is the question's recency window, else the class TTL. If no cited source is dated inside it, `limitations` says so, with the newest date, and the lead says the statements may be out of date. Undated sources never satisfy freshness and are named under `uncertainties` (`test_freshness_unsatisfied_path`, `test_undated_sources_never_satisfy_freshness_and_are_named`).
- **Contradictions** are listed with both sides, publication and event dates, source types, and the count of independent groups on each side. The assessment says "Unresolved: the available evidence does not establish which is correct." unless the disagreement is a changed value with a later effective date, in which case it names the later source as the *leaning* with its basis and adds "Not independently confirmed." (`test_contradictions_listed_with_both_sides_and_unresolved_basis`, `test_value_supersession_states_its_basis`).
- **Failures are surfaced, never hidden**: each provider, engine or fetch failure becomes a `limitations` line, and the number of failures that may have hidden evidence appears under `uncertainties` (`test_failures_are_surfaced_never_hidden`).
- **Always present** in `limitations`: the extractive-synthesis caveat (statements are quoted, not generated; contradiction detection covers numbers, versions, dates, polarity and explicit opposites only).
- **Also surfaced**: single-source and contested statements, undated sources, injection quarantine, truncated documents (`test_uncertainties_name_injection_truncation_and_single_source`).
- **Mode sections**: `verdict` (fact verification), `comparison` rows (comparative), `methodology` and findings/interpretations split (scientific; design, sample size and preprint status read from cue words), findings grouped by package (technical).

## 10. Persistence

Clusters and their claims are written in the evaluating stage in one transaction (`state.save_clusters`): claims as `web_claims` rows, `supports` links from each supporting claim to the first, `contradicts` links for each pair, and the cluster list (claim ids, posterior, status) in the task checkpoint. Raw ≠ accepted: a claim row is raw extracted text; only consolidation can turn clusters into `web_knowledge`, and only the owner can accept (MEMORY_INTEGRATION).

## 11. Known weaknesses

Stated plainly; each is a property of the current rules, not a hidden failure.

1. **Lexical only.** Two sentences that say the same thing in different words fall below the key (0.5) and shingle (0.6) thresholds and form separate single-source clusters. Contradiction detection finds numbers, versions, dates, polarity and six cue pairs; it does not find other disagreements (ADR-022).
2. **Claim ids ignore numbers.** `normalise` masks numbers and versions, so two sentences on one URL that differ only in a value ("was 2.3.4 ... " and "is 2.4.0 ...") get the same id; only the more relevant one is kept. Verified at `5f09ac3`.
3. **Independence is a heuristic.** Different domains count as independent even if one quotes the other in different words, or both belong to one publisher. Same-domain checks use the registrable domain, so `a.example.com` and `b.example.com` are one source (correct for blogs on one site, wrong for hosting platforms).
4. **Group count beats reliability.** The representative position is chosen by the number of independent groups; a coordinated set of cheap domains outvotes one authoritative record. The reliability constants (0.8, 0.9, 0.75, 0.5) are fixed policy, not learned.
5. **"Primary" means "a structured API we called", not "authoritative".** Wikipedia, OpenAlex and Crossref records are weighted 0.9; a Wikipedia hit contributes only its title and a snippet of at most 400 characters.
6. **PyPI and npm records do not produce the templated statement.** `_metadata_statements` reads `version` or `latest_version`; the providers store `latest`. The claim then comes from the provider's snippet sentence ("Latest release 2.3.5 uploaded ...") as a `statement`, still marked primary, and the report labels it a quote, not a template. OSV advisories, GitHub releases and academic records do produce templates.
7. **Headings can become claims.** A heading or title of six words or more passes the sentence filter and may appear as a "statement" (visible in the example in RESEARCH_ORCHESTRATION §7).
8. **Negation and hedging are regexes.** Sarcasm, double negatives and negation outside the main clause are missed or mis-read; hedges are English only.
9. **Injection detection is pattern-based.** Novel phrasing, other languages and instructions split across sentences can pass; stylesheet-hidden text that the cleaner does not recognise (external CSS) is kept (SECURITY_MODEL §9).
10. **Dates.** Without a publication date a source is "undated" (0.75 weight); a date taken from the HTTP `Last-Modified` header may reflect a template change rather than the content.
11. **Posterior is not calibrated.** The Beta update is a transparent weighting rule; 0.77 does not mean "77 % of such statements are true". The committed tests check its direction and bounds, not its calibration against ground truth.
12. **Dead check.** `consolidate._eligibility` tests `claim.flags["injection"]`, which `extract_claims` never sets (flagged sentences are removed before claims exist). The check cannot fire; the protection comes from the removal.

## 12. How to verify

```sh
pytest -q tests/webcortex/test_claims.py tests/webcortex/test_text.py tests/webcortex/test_safety.py
pytest -q tests/webcortex/test_synthesis.py tests/webcortex/test_state.py
pytest -q tests/webcortex -k "independent or syndicated or contradict or verdict or injection"
```
