# Web Cortex: evidence and provenance

Status: describes the working tree on top of `64da95b`, which includes the UAT fixes not yet committed (`claims.py`, `text.py`, `extract.py`, `safety.py`, `synthesis.py`, `state.py`, migration 6). Test names are given where a property is verified. Contracts: [ARCHITECTURE.md](ARCHITECTURE.md) §4.3, §5, §6. Decision: ADR-022 (no model in the loop). Time handling: [TEMPORAL_KNOWLEDGE.md](TEMPORAL_KNOWLEDGE.md). Threats: [SECURITY_MODEL.md](SECURITY_MODEL.md).

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

1. API records first: `_metadata_statements` builds templates such as `OSV lists advisory GHSA-... for <package>: <summary>; fixed in version ...`, `GitHub lists release <tag> of <repo>, published <date>.`, and `arXiv record: "<title>" (v2), dated <date>, a preprint (not peer reviewed).` A template that carries injection-like text is dropped (`test_metadata_with_injection_is_dropped`). PyPI and npm records, whose providers store the version under `latest`, produce the same template (`test_pypi_and_npm_records_as_the_providers_store_them_produce_templates`).
2. A document whose metadata says `snippet_only` (a Wikipedia search hit: a highlighted fragment, not the page) yields **no claims**: it is a discovery hint, and the orchestrator reads the article instead (`test_snippet_only_documents_are_discovery_hints`, `test_wikipedia_hits_are_snippet_only_and_article_returns_the_lead`).
3. Then every sentence of `doc.text` (for an API record with no text, its abstract) that is 6 to 80 words, not boilerplate or navigation (cookie banners, "subscribe", "all rights reserved", breadcrumb separators), **not a heading or the page title**, and carries **no injection finding** (`test_extract_claims_skips_injection_and_irrelevant_sentences`). `claims.fragment` also rejects text that is not a self-contained sentence: it starts with closing punctuation or inside a quotation, its double quotes do not balance, or most of its words are ALL CAPS (a heading or a shout) (`test_fragments_and_shouted_lines_are_not_claims`). For HTML pages, feeds and API records every line of `doc.text` is a block boundary, so no sentence spans a line break (`hard_newlines`, `test_hard_newlines_never_glue_a_heading_to_the_next_block`). A sentence boundary is ignored inside a double quotation opened at most 300 characters earlier (`text.QUOTE_SPAN`), so a quote is never split in the middle and a stray unbalanced quote far back does not glue a whole block together (`test_no_sentence_starts_inside_an_open_quotation`, `test_a_stray_quote_far_back_does_not_glue_the_block`).
   - `text.heading_like(line, title)` marks a heading: the line equals the page title (case and whitespace insensitive), or it has no sentence-final punctuation, no finite-verb cue (an auxiliary, a common present-tense verb or an `-ed` form) and is either at most 12 words or in Title Case (`test_headings_and_the_page_title_are_not_claims`, `test_heading_like`).
4. Each candidate is scored with BM25 against the question, plus 0.3 x the title's overlap with the question, then multiplied by 1.15 if it carries values and 1.10 if it carries a dated event. `relevance = raw / (raw + 2.0)`. Structured records have a relevance floor of 0.5. A candidate with relevance 0 is discarded; at most 8 claims per document are kept (`test_extract_claims_respects_max_and_length_bounds`).

| Field | Meaning |
|---|---|
| `id` | `sha256(source canonical URL + "\n" + the sentence lower-cased with whitespace collapsed)[:24]`. Numbers and versions are **kept**, so two sentences on one page that differ only in a value are two claims (`test_claim_ids_keep_numbers_so_value_variants_on_one_page_both_survive`). The subject `key` still masks them |
| `kind` | `metadata` for API templates. For sentences: `interpretation` if hedged ("suggests", "may", "we propose", "likely"); else `finding` if it reports a result ("we found", "results show", "increased by 12", `n = 40`, `p < 0.05`); else `statement`. Hedging wins over result cues: a hedged result is not a finding (`test_claim_kind_cues`) |
| `polarity` | -1 if the **main clause** is negated **in prose** ("did not", "no evidence", "false", "debunked", "failed to"), else +1. A leading subordinate clause is skipped and the clause is cut at ", which", ", although", "; ". "not only", "no doubt", "no later than" are exempt (`test_polarity_cues_and_exemptions`). Code is not prose: inline code, identifiers and calls (`verify=False`, `requests.Session()`) and the literals `False`, `None`, `True`, `null`, `nil`, `NaN`, `undefined` are removed before negation is read (`test_code_literals_are_not_negation`) |
| `values` | Canonical strings from `text.values`: versions (`v2.3.5`), dates (`2026-11-17`), years, percents, money (`USD 1.2e+09`), quantities with a unit (`24 participants`), plain numbers |
| `event_at` | First date mentioned in the sentence (`temporal.event_dates`), resolved against the publication date for partial or relative forms |
| `key` | Subject signature: the 4 best content tokens, stemmed and sorted. Question terms score +3, entity-like tokens (capitalised, mixed alphanumeric, upper case) +2, noun-like suffixes +1, others 0.5. Numbers, versions, negations, hedges, direction words (rose, fell), reporting verbs and time words are excluded, so "rose 5 %" and "fell 3 %" about one subject share a key (`test_key_ignores_values_direction_and_hedges`) |
| `flags` | `primary_subject` (below), `record_id` (structured records, below), `source_type` (`primary` for structured records fetched for the task, else `secondary`), `retrieval_mode`, `doc_kind`, `observed_at` (retrieval time of a live **registry** record, below), `subject` (question terms in the key), `truncated`, `adversarial_source` (section 7), `snippet_only` |

`observed_at` is set only for registry-state records (`claims.registry_record`: PyPI, npm, OSV and GitHub providers, or metadata keys such as `latest`, `tag`, `advisory_id`) that were not served from cache. Wikipedia, OpenAlex, Crossref, PubMed and arXiv records are dated by their own published or last-edited date, however recently they were fetched (`test_only_registry_records_are_observed_at_retrieval`, `test_live_encyclopedia_record_does_not_make_a_this_week_question_fresh`).

PDFs, feeds and plain text pass through the same sentence path.

**Primary subject** (`claims.primary_subject`). Each sentence claim records the first named-entity-like span of its main clause that starts before the main verb: a run of capitalised words with connectors ("Champ de Mars", "Rust for Linux"), a CamelCase or alphanumeric token ("NumPy", "H100", "CVE-2024-35195") or an acronym. Reporting frames ("The agency confirmed that ...", "According to Y, ...") and leading phrases are skipped; a possessor names the owner, not the subject ("ESA's Hera probe" is about Hera); a sentence that starts with a pronoun is about the subject of its document's title ("Mojo (programming language)" gives Mojo, while "Eiffel Tower (Paris, Texas)" keeps its qualifier). A sentence that names no subject before its verb has none (`test_primary_subjects_of_uat_sentences`).

**Record identity** (`claims.record_identity`). A templated record claim is about its record, not a topic: DOI, arXiv id (version stripped), PMID or title for academic records; advisory id plus aliases; repository plus tag; ecosystem plus package name. The ids are stored in `flags["record_id"]`; for claims saved earlier they are parsed back from the template text.

`adversarial_document(doc)` decides the adversarial flag (section 7). An image beacon found **only in the Markdown rendering** does not make a page adversarial: claims come from `text`, which has no images, and quarantine already cut the beacon; otherwise every Wikipedia thumbnail with a tracking parameter would zero the page (`test_a_pages_own_image_thumbnails_do_not_make_it_adversarial`).

## 3. Confidence: a Beta posterior over independent groups

**Independence groups** (`independence_groups`): claims from the **same registrable domain**, or whose raw texts have shingle Jaccard of at least 0.8 (3-word shingles; syndication, copying), collapse into one group. Ten reports on one domain count once (`test_same_domain_repetition_does_not_add_confidence`, `test_syndicated_copy_on_second_domain_counts_once`).

**Representative and bearing.** Within a cluster, the *representative* is the claim whose supporters (`claims.agrees`) span the most independent groups; ties go to the newer effective date, then higher relevance. For release and package records it is the **highest version** (`text.version_key`, semver-aware: `2.5.5 > 2.5.4 > 2.5.5rc1 > 2.5.5.dev1`, then the newer date), never the provider order (`test_different_releases_are_different_records_and_the_highest_version_leads`). Every other claim then bears on the representative in one of three ways (`claims.bearing`):

| Bearing | Rule | Counts as evidence |
|---|---|---|
| support | The same claim; a near-duplicate (normalised shingle Jaccard at least 0.6); the same structured record; or a statement about the same subject with at least one equal value and no conflict | Yes, toward alpha |
| contradict | Same subject (similarity above 0) and a conflict (section 5) | Yes, toward beta |
| related | In the cluster, but neither restates nor contradicts | **No**: not cited, no weight |

Mere topical overlap is not agreement (`test_related_claims_are_not_support`). In fact verification the claim to be checked is the target and agreement is measured against it instead (section 6).

**Update.** Prior Beta(1, 1). For each independent group with `k` bearing members (supporting or contradicting; related members are left out) of which `a` support the representative, let `w` be the largest evidence weight among its members:

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
| Claim from an adversarial (injection-flagged) source, section 7 | **0**: shown, never counted |
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

**Consequence:** the two-source confidence floor is 0.72 (`SUPPORTED_MIN_CONFIDENCE` and `WebConfig.auto_provisional_min_confidence`). Derivation with prior Beta(1, 1) and secondary weight 0.8: two dated secondary groups give 2.6 / 3.6 = 0.722 and pass; one dated and one undated (0.8 + 0.6) give 2.4 / 3.4 = 0.706 and do not; two undated (0.6 + 0.6) give 2.2 / 3.2 = 0.688 and do not. The decisive criteria remain at least two agreeing independent groups and no contradiction; the floor keeps weak or undated pairs out. See MEMORY_INTEGRATION §3 (`test_two_independent_sources_rule_is_coherent_across_verdict_and_consolidation`).

`test_more_independent_agreement_raises_confidence` and `test_contradiction_lowers_confidence` cover the direction of both effects.

## 4. Clustering (`claims.cluster`)

A claim joins the most similar existing cluster if its subject is compatible (`claims.subjects_compatible`) **and** the key-token Jaccard is at least 0.5 **or** the shingle Jaccard of the normalised texts is at least 0.6; else it starts a new cluster. Subjects are compatible when: both claims are record claims and their identities intersect (two different papers, advisories or releases never cluster or corroborate: `test_records_of_different_papers_never_corroborate`); both name a primary subject and it is the same (equal token sets, or a one-word name inside the other: "Yuste" and "Rafael Yuste"; "Eiffel Tower" and "Eiffel Tower (Paris, Texas)" differ); one names a subject and the other mentions it; or neither names one and the lexical key decides (`test_statements_about_different_subjects_never_share_a_cluster`). Two record claims with the same identity are always similar (one record, two providers). Claims are visited in relevance order (ties by id), so the result does not depend on input order (`test_cluster_is_deterministic_regardless_of_input_order`). Two claims whose question terms name different subjects (two package records in a comparison) never cluster (`different_subjects`, `test_different_subjects_do_not_cluster_in_comparisons`).

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
2. **Values disagree** within one comparison group (`claims._value_conflict`), compared like for like (`text.values_conflict`): numbers differ by more than 1 % relative, versions differ at all, dates differ by more than 1 day, money only within one currency, quantities only within one physical dimension. Details:
   - **Unit normalisation.** Spellings of a unit map to one canonical unit ("412 kilometres" and "412 km" are both `412 km`; "9,800 square kilometres" is `9800 km²`), and quantities of one dimension (length, area, volume, mass, time, data, frequency, power, voltage) are converted to its base unit, so "1.2 km" and "1200 m" agree. A bare number coordinated with a following quantity takes its unit ("between 400 and 520 km", "400–520 km", "5 to 10 mg"; "and" joins only after "between") (`test_unit_spellings_normalise_so_the_same_quantity_compares`, `test_a_coordinated_plain_number_takes_the_following_unit`).
   - **Magnitude rule for bare numbers.** A plain number on one side stands for a quantity on the other only when it is that side's only plain number of the same order of magnitude (floor log10) as the other side's values of that quantity, the side states no value of that quantity, and no other quantity on the other side has that magnitude ("412" vs "520 km" compare; "24" vs "520 km" and "412, 450" vs "520 km" do not) (`test_plain_number_compares_with_a_quantity_by_magnitude`).
   - **Like-kind pairs.** Every comparison group present on both sides is compared on its own and the first group with no equal pair is the conflict, so a shared incidental number ("Nimbus-OS 5") cannot mask a disagreement in another group (kernel 6.14 vs 6.12). Integers and decimals are separate groups, merged only when the two sides share neither. Two decimals written with the same number of places must be equal ("6.12" vs "6.14" and "42.1%" vs "42.3%" conflict); with different precision the 1 % tolerance absorbs rounding ("42%" vs "42.2%") (`test_a_shared_incidental_number_does_not_mask_a_conflict`, `test_values_conflict`).
3. **Cue pair**: one side uses only one pole of increase/decrease, supports/refutes, approved/rejected, safe/unsafe, effective/ineffective, true/false, the other only the opposite pole.

   - **Plain numbers count the same thing.** A plain number is compared with a plain number only when the word after it agrees ("55 countries" never against "1 winner") or one has no noun and both share an order of magnitude, unless the two sentences are the same template with different numbers (`text.value_nouns`, `test_plain_numbers_compare_only_when_they_count_the_same_thing`). A range shares its magnitude and unit ("14-16 billion") (`test_ranges_share_magnitude_and_unit`).
   - **Years alone.** A disagreement that is only about years needs a key-token Jaccard of at least 0.75 or a near-identical text: different years usually date different events ("started in 2020" vs "1.0 in 2015") (`test_value_conflicts_compare_like_with_like_and_read_plainly`).
   - **Records.** Record claims of different identities never conflict: two releases are not two answers to one question.

**Series, not conflict.** When both the dates/years *and* the other values differ ("revenue 5 in 2024" vs "revenue 6 in 2025"), the statements describe different reference periods, so they do not conflict (`text.values_conflict`). A later value for the same subject may supersede (TEMPORAL_KNOWLEDGE §5).

Pairs from the same independence group, and pairs of claims that are not directly similar, are not listed; at most 50 pairs per cluster. Each pair carries a `description` from `claims.describe_conflict`, a plain sentence for owner questions and UIs: "Sources disagree about NumPy: one says 2.3, another says 2.4.", or "Sources disagree: one states it, another denies it ('not')." Claims about different subjects are never compared. `test_conflicting_claims_are_contested_with_reason`, `test_contradictions_require_same_subject`.

## 6. Verdicts (fact verification only)

`synthesis._verdict` checks the statement (`assessment["claim"]`, else the question) **slot by slot**, not by word overlap. `synthesis.parse_statement` reads the statement into a shape, a subject, an object and a polarity; every reportable claim of the task (a claim from an adversarial source, a claim with an injection finding and a snippet-only hint are not reportable) is then classified against it as supporting, contradicting or irrelevant. Word overlap alone is never agreement.

| Shape | Statement | Supported by | Contradicted by |
|---|---|---|---|
| `location` | "X is in / located in Y" (Y capitalised) | A sentence about X placing it in Y | A sentence about X placing it elsewhere, or denying Y |
| `capital` | "X is the capital of Y" | "X is the capital of Y" | "Z is the capital of Y" with Z not X |
| `quantity` | "X has / is / measures N unit" | Equal value (1 % tolerance; 10 % when either side says about, roughly, nearly) | Another value of the same kind |
| `date` | "X was released / launched / founded / born in DATE" | Equal year or day, in a sentence with a release cue | A different date |
| `is` | "X is Y" | Every name in Y and at least 60 % of its other words after X | Same words with opposite polarity |
| `event` | "X verb ... [DATE]" | Same verb stem, same object words or equal value | Different value or opposite polarity |

An optional "is it true that", "fact-check" or "verify" prefix and "not" or "never" in the statement are allowed. Source sentences must be *about* X (its primary subject, section 2), not mention it in passing. A number in the subject ("Quillstream 4.2.0") must not differ in the sentence, since another release is another subject. A document titled "X (Qualifier)" whose qualifier the statement does not share ("Eiffel Tower (Paris, Texas)") is about something else (`test_fact_check_discriminates_place_claims`, `test_verdict_shapes_quantity_capital_and_date`).

Independent groups then weigh in as in `claims.evaluate`: prior Beta(1, 1), each group adds its evidence weight split between supporting and contradicting members. A claim that a listed contradiction pits against a supporting claim counts as contradicting too, so a verdict never says "0 contradict" next to a contradiction of the checked statement (`test_verdict_is_consistent_with_listed_contradictions`).

| Label | Rule |
|---|---|
| `supported` | Mean at least 0.72, at least 2 independent groups support, no group contradicts. Adversarial claims count on neither side |
| `disputed` | More weight on the contradicting side than on the supporting side |
| `contested` | At least one group supports and at least one contradicts, but not `disputed` |
| `insufficient` | Anything else, including no claim naming the subject with the checked relation (confidence 0.5) |

The verdict object carries `label`, `confidence`, `statement`, `shape` (null for an unsupported statement), `citations`, `supporting` and `contradicting` (claim ids) and `reasons` (group counts, the posterior mean and up to four contradicting sources with what each says instead). A statement of any other shape gets `insufficient`, `shape: null` and a reason that lists the shapes the lexical checker supports; no source is counted. A fact-verification task with no extracted claims at all has `verdict: null` (`test_unsupported_shapes_and_no_claims`). Because of the 0.72 floor, `supported` needs two dated independent sources; one dated plus one undated, or two undated, stay `insufficient`. A verdict's `ttl_overrides` (owner `freshness_ttl`) come from `synthesize` (`test_synthesize_threads_ttl_overrides`, `test_score_threads_ttl_overrides`). Older tests of the labels: `test_verdict_labels`, `test_verdict_for_negated_claim_flips_support`, `test_verdict_insufficient_when_nothing_matches`.

## 7. Prompt-injection quarantine (`safety.py`)

Retrieved text is data (ARCHITECTURE §3). `scan_injection` runs 21 regex patterns for text addressed to an AI agent: ignore or override instructions, role reassignment, "act as", system-prompt requests, developer-mode, chat-template tokens (`<|im_start|>`, `[INST]`), role markers, concealment from the user, exfiltration and "send data to ...", shell commands (`rm -`, `curl | sh`), tool-call JSON, direct address to an agent, base64 blobs over 200 characters, hidden characters, tracking-beacon images. Variants added after UAT: "ignore the directions given above" (an instruction noun followed by above, before, earlier or previously), "translate this sentence as ...", "say exactly: ..." and "repeat after me", and "new instructions:" or "from now on, you will ..." (`test_injection_variants_from_uat`). Patterns target imperative, agent-directed phrasing so that "researchers ignored previous results" is not flagged (`test_injection_positives`, `test_injection_negatives`, `test_injection_variants_keep_benign_prose`).

- `quarantine(doc)` returns a copy with each flagged sentence removed from `text` and `markdown`. `flags["injection"]` lists findings (`pattern`, an excerpt of at most 120 control-stripped characters, `offset`, `field`); `flags["quarantined_sentences"]` counts removals (`test_quarantine_removes_only_flagged_sentences_and_copies`). The stored source keeps these findings for the evidence view; reports do not (below).
- `acquire()` and `reread()` quarantine every fetched or re-read document. The claim extractor screens every sentence and every API template again, so API records are covered.
- A flagged sentence **never becomes a claim**. The report's `uncertainties` names the source, the pattern names and the number of sentences removed.
- **Adversarial sources.** A document with any injection finding is adversarial, except one whose only finding is a `markdown_beacon` in the Markdown field (`claims.adversarial_document`). Its remaining statements are still extracted and shown, with `flags["adversarial_source"] = True` and evidence weight 0 (`claims.is_adversarial`). They are in no independence group, position, agreeing set or contradiction pair, so they never change a cluster's representative, the posterior, the independent-source count, a verdict or the list of contradictions (`claims.evaluate`; `test_adversarial_sources_are_shown_but_carry_no_weight`). Reports cite only non-adversarial claims and add the uncertainty "N source(s) excluded as adversarial: they carry instruction-like content, so their remaining statements are shown but carry no evidence weight." (`test_adversarial_sources_are_excluded_from_evidence_and_named`). Consolidation holds such claims and never promotes them (MEMORY_INTEGRATION §3).
- **Reports never echo injected text.** `sources[].flags.injection` in a report is `[{pattern, count}]` only; the report (and so anything built from it: console report view, Pocket report, MCP `web_task`) carries no excerpt of injected text (`test_report_never_echoes_injected_text`). More strongly, **no report field carries text from an adversarial source or text that `safety.scan_injection` flags**. Every producer of report text (answer quotes, findings, contradictions, the verdict, `consolidation.held`, the supporting list) goes through `claims.is_reportable` and `synthesis.report_safe_text`, which also exclude snippet-only hints; a finding whose representative is not reportable is dropped and a contradiction with an unreportable side is not listed (`test_is_reportable_excludes_adversarial_injected_and_snippet_text`, `test_injection_variants_never_reach_any_report_field`). The stored source row, shown by `orchestrator.evidence`, keeps the excerpts.
- Nothing in content can start a fetch outside the budgeted plan, create a monitor, change configuration or reach a tool. Output to agents and MCP is wrapped in `safety.untrusted()` envelopes (`test_untrusted_envelope`, `test_context_pack_respects_token_bound_and_wraps_untrusted`).

## 8. Claim-to-source traceability

Every accepted statement can be walked back to bytes:

| Step | Where | Key |
|---|---|---|
| Knowledge item | `web_knowledge` | integer `id`, `version`, `key`; `supporting` and `contradicting` hold claim ids; `sources` holds `{url, title, domain, published_at, retrieved_at}`; `task_id` is the task that created it |
| Claim | `web_claims` | `(task_id, id)`; `id` is the 24-hex claim id; `source_id` -> `web_sources.id`; `source_url`, `canonical_url`, `doc_sha256`, `sentence_index`, `cluster_status`, `confidence`, `flags` |
| Relation between claims | `web_claim_links` | `src`, `dst` claim ids; kind `supports`, `contradicts`, `supersedes`, `duplicates`; `detail` holds the reason or knowledge id |
| Source | `web_sources` | integer `id`, unique per `(task_id, canonical_url)`; `url`, `final_url`, `kind`, `published_at`, `retrieved_at`, `retrieval_mode`, `content_sha256`, `provider`, `flags` (injection findings with excerpts, hidden-removed count, truncation, `truncated_extraction`); `metadata` may carry `snippet_only`, `date_source`, `modified_source` |
| Bytes | public cache `documents` | `sha256 = content_sha256`; may have been pruned, in which case the row and hash remain but the body does not |
| Episode | `web_events` | `task_id`, kind, stage, detail, time |

`orchestrator.evidence(db, task_id)` ("show your evidence") returns sources, claims (each marked `"untrusted": true`), links and the event log of a task. `memory.retrieve` exposes the same rows as the kinds `claim`, `source`, `knowledge`, `research` (MEMORY_INTEGRATION §5). In a report, `sources[].n` is a number local to that report (in order of first citation); `findings[].claims` lists the claim ids. The same claim id can appear in several tasks (it hashes the source and sentence), which is why `web_claims` is keyed by task as well. Extraction is bounded (50,000 page elements, Markdown only for main content of at most 10,000 elements, 500,000 characters); a cut sets `flags["truncated_extraction"]` on the source (SEARCH_AND_ACQUISITION §7). Report `uncertainties` name only the byte-size `truncated` flag; `truncated_extraction` is visible in the report's `sources[].flags`.

## 9. What a report may and may not say (`synthesize`)

- **Quote or template.** Every `answer` item (after the lead) is a sentence quoted verbatim from a cited source (`type: "quote"`) or a template over measured quantities (`type: "template"`: counts, statuses, dates, a structured record's fields). `citations` list **exactly the sources whose own claim restates it** (`claims.agrees`); other members of the cluster are `related`, listed separately under `related` (source numbers), never cited and never counted. Each finding also reports `copies` (cited documents) and `independent_sources` (independent groups among them), so a UI can say "3 copies, 1 independent source" (`test_each_answer_item_cites_only_sources_that_support_it`, `test_copies_and_independent_sources_are_reported`). `test_answer_has_only_quotes_and_templates_and_limit` and the invariant check in `test_report_keys_and_invariants` assert that each quote occurs in a cited document.
- **Latest version wins.** Among release and package records of one package or repository, the newest version (semver order, then date) leads; older ones follow and carry `superseded: true` (`test_latest_release_question_leads_with_the_newest_release`).
- **At most 5 quoted statements**, ranked by `confidence x relevance x freshness factor` (1.0 inside the required window, 0.75 undated, 0.5 outside). Freshness changes ranking here, not the stored confidence.
- **`live`** is true only if at least one *cited* source was retrieved live in this task (`retrieval_mode` of `live`, `api`, `feed` or `rendered`; a 304 revalidation is `live`). Otherwise the report says `"live": false`, the lead sentence says "All cited sources were served from cache", and `limitations` says how old the cache is (`test_cache_only_report_is_not_live`).
- **Freshness.** The required window is the question's recency window, else the class TTL. If no cited source is dated inside it, `limitations` says so, with the newest date, and the lead says the statements may be out of date. Only registry-state records (PyPI, npm, OSV, GitHub) fetched live count as current at retrieval time; other undated sources never satisfy freshness and are named under `uncertainties` (`test_freshness_unsatisfied_path`, `test_undated_sources_never_satisfy_freshness_and_are_named`).
- **Contradictions** are listed with both sides, a plain-language `description` (`claims.describe_conflict`), publication and event dates, source types, and the count of independent groups on each side. The assessment says "Unresolved: the available evidence does not establish which is correct." unless the disagreement is a changed value with a later effective date, in which case it names the later source as the *leaning* with its basis and adds "Not independently confirmed." (`test_contradictions_listed_with_both_sides_and_unresolved_basis`, `test_value_supersession_states_its_basis`).
- **Failures are surfaced, never hidden**: each provider, engine or fetch failure becomes a `limitations` line, and the number of failures that may have hidden evidence appears under `uncertainties` (`test_failures_are_surfaced_never_hidden`).
- **Always present** in `limitations`: the extractive-synthesis caveat (statements are quoted, not generated; contradiction detection covers numbers, versions, dates, polarity and explicit opposites only).
- **Also surfaced**: single-source and contested statements, undated sources, injection quarantine, truncated documents (`test_uncertainties_name_injection_truncation_and_single_source`).
- **Mode sections**: `verdict` (fact verification), `comparison` rows (comparative), `methodology` and findings/interpretations split (scientific; design, sample size and preprint status read from cue words), findings grouped by package (technical).

## 10. Persistence

Clusters and their claims are written in the evaluating stage in one transaction (`state.save_clusters`): claims as `web_claims` rows, `supports` links from each supporting claim to the first, `contradicts` links for each pair, and the cluster list (claim ids, posterior, status) in the task checkpoint. Raw ≠ accepted: a claim row is raw extracted text; only consolidation can turn clusters into `web_knowledge`, and only the owner can accept (MEMORY_INTEGRATION).

## 11. Known weaknesses

Stated plainly; each is a property of the current rules, not a hidden failure. The UAT fixes removed these earlier entries: related claims cited as support, topical overlap treated as fact-check support, different papers or releases sharing a cluster, code literals read as negation, adversarial text in report fields, Last-Modified used as a publication date, phones detected from bare digit runs, and a heading filter that was the only guard against fragments (`claims.fragment` now also rejects split quotations and shouted lines).

1. **Lexical only.** Two sentences that say the same thing in different words fall below the key (0.5) and shingle (0.6) thresholds and form separate single-source clusters. Contradiction detection finds numbers, versions, dates, polarity and six cue pairs; it does not find other disagreements (ADR-022).
2. **The fact checker is a lexical slot checker.** It handles six statement shapes (section 6). It does not understand paraphrase, negation scope across clauses or world knowledge; any other statement is `insufficient` with no source counted.
3. **Myth-reporting sentences.** A sentence that reports a false claim in order to debunk it ("Many believe the Eiffel Tower is in Berlin") can be read as support for that claim: the checker sees the subject, the relation and the object, not the stance of the writer.
4. **Capital-of is only checked in one reverse case.** "X is the capital of Y" is contradicted by "Z is the capital of Y"; the reverse question ("what is the capital of Y?") and a country with several capitals are not modelled.
5. **Capitalised first words.** A sentence-initial capitalised word is a name only by position rules (`claims._initial_is_entity`: part of a multi-word name, in apposition, or followed by a verb). An ordinary word at the start can be mistaken for a subject, and a one-word name followed by a non-verb can be missed.
6. **Claim ids and keys differ in what they mask.** Ids keep numbers (sentences that differ only in a value are separate claims). The subject `key` masks numbers, so such claims share a key, cluster together and can contradict each other as intended.
7. **Independence is a heuristic.** Different domains count as independent even if one quotes the other in different words, or both belong to one publisher. Same-domain checks use the registrable domain, so `a.example.com` and `b.example.com` are one source (correct for blogs on one site, wrong for hosting platforms).
8. **Group count beats reliability.** The representative is chosen by the number of independent groups; a coordinated set of cheap domains outvotes one authoritative record (for release records the highest version leads regardless). The reliability constants (0.8, 0.9, 0.75, 0.5) are fixed policy, not learned.
9. **"Primary" means "a structured API we called", not "authoritative".** Wikipedia, OpenAlex and Crossref records are weighted 0.9; a Wikipedia search hit is a snippet-only hint and yields no claim, the article lead (at most 20,000 characters) does.
10. **The heading filter is heuristic.** `text.heading_like` is lexical (no finite-verb cue, short or Title Case). A heading with a verb-like word, or a verbless sentence fragment that is a real claim, can be mis-classified in either direction.
11. **Negation and hedging are regexes.** Sarcasm, double negatives and negation outside the main clause are missed or mis-read; hedges are English only.
12. **Injection detection is pattern-based.** Novel phrasing, other languages and instructions split across sentences can pass; stylesheet-hidden text that the cleaner does not recognise (external CSS) is kept (SECURITY_MODEL §9).
13. **Dates.** Without a publication date a source is "undated" (0.75 weight). The HTTP `Last-Modified` header is no longer a publication date: it fills `modified_at` only (`metadata["modified_source"] = "http-last-modified"`), so a header-only page stays undated. A page reached from a feed takes the item's date (`published_hint`, `date_source` "feed"), and Unix-epoch meta values (`og:updated_time="1663021219"`, seconds or milliseconds) are parsed (`test_last_modified_header_is_never_a_publication_date`, `test_unix_epoch_meta_dates_are_parsed`, `test_feed_item_date_wins_as_published_hint`).
14. **Posterior is not calibrated.** The Beta update is a transparent weighting rule; 0.77 does not mean "77 % of such statements are true". The committed tests check its direction and bounds, not its calibration against ground truth.
15. **Adversarial exclusion is per document.** One injection finding (other than a Markdown-only image beacon) makes the whole document's remaining statements weightless; a benign page that quotes an attack string loses its evidence weight (conservative by design). The unit table covers common physical units; an unlisted unit compares only with itself.

## 12. How to verify

```sh
pytest -q tests/webcortex/test_claims.py tests/webcortex/test_text.py tests/webcortex/test_safety.py
pytest -q tests/webcortex/test_synthesis.py tests/webcortex/test_state.py
pytest -q tests/webcortex -k "independent or syndicated or contradict or verdict or injection"
```
