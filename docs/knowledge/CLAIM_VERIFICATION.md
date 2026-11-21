# Claim verification

Status: v0.7.0, working tree. This document describes how the Web Cortex decides whether a checked statement is supported. Decision: ADR-035 (draft `.omc/autopilot/adr/ADR-035-claim-verification.md`), which supersedes the verification rule of ADR-026, and the ADR-033 amendment (`.omc/autopilot/adr/ADR-033-amendment.md`). Code: `webcortex/temporal.py` (`claim_category`), `webcortex/claims.py` (`CATEGORY_POLICY`, `VerificationState`, `verification_state`, `timely`, `official_source`, `origin_keys`, `document_origins`, `independence_groups`), `webcortex/synthesis.py` (`_verdict`, `_registry_verdict`). Tests: `tests/webcortex/test_claim_verification.py`.

Unchanged: everything is lexical and deterministic (ADR-022). The statement is still checked slot by slot (EVIDENCE_AND_PROVENANCE §6). Evidence weights are unchanged: 0.8 for a page, 0.9 for a structured record, 0 for an adversarial source, and stale × 0.5 only in the breaking, volatile and release classes. Consolidation into knowledge is also unchanged: cluster confidence, the 0.72 provisional floor and owner review (MEMORY_INTEGRATION §3). This document changes only the fact-verification **verdict**, plus source independence, which both paths use.

## 1. Why ADR-026's verdict rule was replaced

ADR-026 used one rule for every statement. A statement was `supported` only when the posterior mean was at least 0.72 and at least two independent groups agreed, with every undated source weighted × 0.75. Two credible sources that agreed, one of them undated, gave 0.706 and stayed `insufficient`. Encyclopedias and project "About" pages are usually undated, so true stable facts were rarely confirmed. The v0.6.0 benchmark measured supported recall at 0.4 (PRODUCTION_READINESS §3 R-2). The rule was also blind in the other direction:

- It did not distinguish "is open to visitors **now**" from "was built in 1874". Two dated sources from 2019 could support a current-state claim.
- A syndicated copy that was lightly edited, so that the sentence was no longer 80 % identical, counted as a second independent source.
- A single PyPI record that settled "2.5.5 was published" (ADR-033) was the only record kind that could decide a verdict on its own. Nothing stopped a reader from taking that verdict to mean "stable" or "secure".

The replacement varies the evidence requirement with the **kind of statement**. It keeps the two-group floor wherever the date or the source matters.

## 2. Claim categories

`temporal.claim_category(statement, now=, freshness_class=, registry=, source_kinds=)` returns `(category, reason)`. The rules apply in this order, and the first one that matches wins:

| # | Category | Rule | Freshness class (`CATEGORY_FRESHNESS`) |
|---|---|---|---|
| 1 | `interpretive` | A lower-case judgement word: best, worst, better, should, ought, overrated, recommended, important, good, bad, ... Capitalised names such as "Great Wall" are not matched | stable |
| 2 | `registry` | A package-registry statement (`synthesis.release_statement` parsed it) | release |
| 3 | `historical` | Past tense (`_tense`: the first finite verb is was/were/did/had or a past form), every date in the past, and no "still", "currently", "now", "no longer" or "anymore" | stable |
| 4 | `time_sensitive` | A present-state cue: currently, now, today, still, latest, recent, this week, price, status, outage, "is open/closed/down", "CEO/president/mayor of", ... | volatile |
| 5 | `time_sensitive` | A date in the future (a plan), or the question's class is breaking or volatile | volatile |
| 6 | `historical` | Past tense with no date ("X was created by Y") | stable |
| 7 | `scientific` | A research cue (study, trial, neurons, species, genes, patients, ...), or an academic source was read | scientific |
| 8 | `comparative` | than, versus, outperforms, ranked, or a superlative. "more/less than N" is a bound and does not count | stable |
| 9 | `numerical` | A number, percentage, quantity, amount or version. A four-digit number is a year only after a temporal preposition, so "port 9000" is numerical | stable |
| 10 | `stable` | Everything else: definitions, identities, locations, features | stable |

The freshness classes and TTLs of TEMPORAL_KNOWLEDGE §2 stay the only time limits. A category maps onto a class and adds no new TTLs. Benchmark examples: "the Corran Spire is in Harrowgate" is stable. "the Halvorsen Bridge opened in 1987" is historical. "the brain of the Ostra marsh newt contains 86 million neurons" is scientific. "Tessaly has a population of 251,000" is numerical. "the Marrow Creek ferry currently runs every 30 minutes" is time_sensitive.

## 3. Verification states

`claims.VerificationState` is a `str` enum and is serialised as its value, in `verdict["state"]`:

| State | Meaning | Legacy `verdict["label"]` |
|---|---|---|
| `supported` | Enough independent or authoritative evidence agrees and nothing contradicts | supported |
| `disputed` | Credible evidence contradicts the statement and outweighs the support | disputed |
| `conflicting` | Credible evidence on both sides; neither side clearly outweighs the other | contested |
| `insufficient` | Evidence was examined, but too little of it bears on the statement | insufficient |
| `temporally_unresolved` | The statement is about the current state, and no evidence establishes "now" | insufficient |
| `not_evaluated` | No evidence was examined: a judgement, an unsupported statement shape, or no usable source | insufficient |

`verdict["label"]` keeps the four pre-v0.7.0 values (`claims.STATE_TO_LEGACY`), so readers that only know those labels keep working: Pocket's `VERDICTS`, the console and stored reports. **Normalise on read:** `claims.verdict_state(verdict)` returns `state` when it is present and valid. Otherwise it maps the old label: `contested` → `conflicting`, and a pre-v0.7.0 `insufficient` verdict with `shape: null` counted no source, so it becomes `not_evaluated`. `claims.normalise_state(str)` maps a single string. A fact-verification task that extracted no claims at all still has `verdict: null`, as before; read it as `not_evaluated`.

## 4. Rules and thresholds

Every number below is a named constant in `claims.py`.

**Counting.** For each independent group (§6), with `k` bearing members of which `a` support the statement, the verdict adds `w·a/k` to alpha and `w·(k−a)/k` to beta, from a Beta(1, 1) prior. `w` is the largest member weight. A group counts as supporting when `a ≥ k − a`, and as contradicting otherwise. This is unchanged.

**Per-category policy** (`CATEGORY_POLICY`):

| Category | Undated factor | Groups for `supported` | A direct authoritative record suffices | Timeframe required |
|---|---|---|---|---|
| stable | **1.0** | 2 | the subject's official site | no |
| historical | **1.0** | 2 | the subject's official site | no |
| time_sensitive | undated sources **do not count** | 2 timely groups | the package registry's live record | **yes** |
| scientific | 0.75 | 2 | no | no |
| numerical | 0.75 | 2 | no | no |
| comparative | 0.75 | 2 | no | no |
| registry | 0.75 | 2 | the package registry record (ADR-033) | no |
| interpretive | n/a | n/a | n/a | not evaluated |

**State decision** (`claims.verification_state`), with `sup` and `con` the supporting and contradicting groups and `sw` and `cw` their weights (alpha − 1 and beta − 1):

1. `con = 0`: the state is `supported` when `sup ≥ min_groups` and the posterior mean ≥ **0.72** (`VERIFICATION_MIN_CONFIDENCE`), or when a direct authoritative record supports the statement. Otherwise it is `insufficient`.
2. `sup = 0` and `con ≥ 1`: `disputed`.
3. `con > sup` and `cw ≥ 1.5 × sw` (`DISPUTE_WEIGHT_RATIO`): `disputed`.
4. Otherwise: `conflicting`. No consensus is picked.
5. For time-sensitive statements, an `insufficient` result becomes `temporally_unresolved` when undated or out-of-window evidence bore on the statement.

**Derivations.**

- *Undated factor 1.0 for stable and historical.* An undated page is a weaker witness only when *when* matters. A definition, a location or a dated past event does not become less true with age, and an undated source says nothing about whether it was ever true. With factor 1.0, two credible undated secondary pages give (1 + 0.8 + 0.8) / (2 + 1.6) = 2.6/3.6 = **0.722 ≥ 0.72**: exactly the two-source bar, not lower. One source still gives 1.8/2.8 = 0.643 and stays `insufficient`. So the change admits *undated corroboration*, never single sources, apart from the authoritative records below.
- *0.75 kept for scientific, numerical and comparative.* Measurements and comparisons are revised, rounded and re-estimated, and an undated figure may be an old estimate. One dated and one undated source give 0.706, and two undated give 0.688, both below 0.72. These categories need two dated sources or three undated ones (1 + 3 × 0.6 over 2 + 3 × 0.6 = 2.8/3.8 = 0.737).
- *Dispute ratio 1.5.* One dated source against one undated source (0.8 vs 0.6, ratio 1.33) is not decisive: a date alone does not settle a disagreement, so the result is `conflicting`. Two agreeing groups against one at equal weight (ratio 2.0) are decisive. Two undated sources against one dated source (1.2 vs 0.8, exactly 1.5, from more groups) are decisive. `disputed` also needs more contradicting groups than supporting groups, so one source against one source is always `conflicting`. The rule is asymmetric on purpose: `supported` needs **no** counted contradiction, while `disputed` needs a clear majority.
- *Timeframe window.* The window is the question's `recency_window` when it has one. Otherwise it is the TTL of the question's freshness class when that class ages evidence (breaking 3 h, volatile 2 d, release 7 d), and otherwise the volatile TTL (2 days). The owner can override it through `WebConfig.freshness_ttl`. A claim is *timely* (`claims.timely`) when its source's own time falls inside the window: a live registry record's retrieval time, else the publication date, else the sentence's event date. Dates more than a day in the future do not count (`temporal.window_ok`). Only timely claims are counted. The rest are reported in `verdict["timeframe"]` and in the reasons.
- *Official source* (`claims.official_source`). The source is the subject's own site when the registrable label of its host, split at hyphens and stripped of generic words (lang, project, official, docs, dev, app, io, ...), equals the subject's stemmed name tokens. For example, `fennick-project.example` matches Fennick and `nimbus-os.example` matches Nimbus-OS, but `kestrel-birds.example` does not match Kestrel. A structured API record and an adversarial source are never "official". The official-site rule applies only to stable and historical statements whose shape is not `quantity`. A product's own numbers ("120 contributors", "10 million users") are self-reported and still need corroboration. It also applies only when nothing contradicts the statement: an authority never outvotes evidence.

## 5. Package registries: what a record establishes (ADR-033 amended)

`synthesis.release_statement` now reads six aspects, from yes/no questions ("Is corvane 2.5.4 stable?") and from declarative statements. `_registry_verdict` decides the four new ones:

| Aspect | Example | Can be `supported` by | Can be `disputed` by | Otherwise |
|---|---|---|---|---|
| published | "corvane 2.5.5 was released" | The package's own registry record (ADR-033, unchanged) | The record's history not listing the version | insufficient |
| latest | "the latest release of corvane is 2.5.5" | The record's `latest` (unchanged) | Another `latest` | insufficient |
| stable | "corvane 2.5.5 is stable" | A record that lists the version **with an explicit yank flag of false**, and no pre-release marker (PEP 440 a/b/rc/dev, SemVer `-beta`) | A pre-release marker, a yanked release, or a version the record does not list | insufficient. Publication alone never makes a version stable |
| compatible | "... is compatible with Python 3.9" | **Never** (only the negated statement can be supported, by the same refuting metadata) | `requires_python` excluding the target | insufficient: a declaration is not a compatibility test |
| secure | "... is secure" / "is vulnerable" | **Never** for "secure"; "vulnerable" by an OSV advisory covering the version | An OSV advisory whose affected range covers the version | insufficient: no known advisory is not evidence of security |
| recommended | "... is recommended" | **Never** | Never | `not_evaluated`: a judgement |

`verdict["aspect"]` names the aspect, and `basis` is `registry_record` or `version_marker`. Tests: `test_publication_alone_never_supports_stable_compatible_secure_or_recommended`, `test_stable_is_decided_by_pre_release_markers_and_yank_flags`, `test_declared_metadata_and_advisories_can_refute`. A registry statement now gets a verdict in any mode, not only `technical`. R-6 still applies to *publication* and *latest*: a wrong registry entry would be believed for those two aspects. It no longer spreads into stability, security or recommendation.

## 6. Independent sources and syndication

`claims.independence_groups(claims, origins=)` joins two claims into one group when any of these holds:

| Signal | Rule | Constant |
|---|---|---|
| Same host | Equal host, as before | n/a |
| Copied sentence | 3-word shingle Jaccard of the raw sentences ≥ 0.8 | `DUPLICATE_SHINGLE` |
| Canonical URL | The document's canonical URL is on another host: origin `site:<host>` | n/a |
| Attribution | "first appeared on / originally published by / republished from / syndicated from / reprinted from / provided by `Name`": origin `name:<name>`. This matches the other host's registrable label with punctuation removed (`Coastline Wire` = `coastline-wire.example`) | n/a |
| Wire credit | "(Reuters)", "(AP)", "(AFP)", "by Associated Press", ...: origin `name:<wire>`, so two outlets carrying one wire story are one group | n/a |
| Copied document | Both texts have at least 40 words and their 5-word shingle Jaccard is ≥ 0.6: origin `doc:<first copy>` (`document_origins`, computed in `synthesize`) | `DOC_DUPLICATE_SHINGLE`, `DOC_SHINGLE_K`, `DOC_DUPLICATE_MIN_WORDS` |

Origins from the document itself are stored on each claim as `flags["origin"]`. Document-level duplicates are passed as `origins` to `claims.score` and to the verdict. Short texts are compared only sentence by sentence, because two independent one-line reports of a fact are often worded alike (`test_canonical_url_and_near_identical_documents_join_origins`). The verdict reports "N sentence(s) are copies of another source ... and count once".

## 7. Examples (benchmark cases, fictional corpus)

| Case | Statement | Evidence | State | Basis |
|---|---|---|---|---|
| F17 stable, undated | the Tolvane Lighthouse is in Varnmouth | wiki (undated) + heritage page (undated) | supported (0.722) | corroboration |
| F18 historical, undated | the Tolvane Lighthouse was built in 1874 | the same two undated pages | supported | corroboration |
| F19 current, undated | the Tolvane Lighthouse is currently open to visitors | the same two undated pages | temporally_unresolved | n/a |
| F20 official source | Fennick was created by Oda Haraldsen | fennick-project.example/about only (undated) | supported | official_source |
| F21 corroboration | Fennick is written in OCaml | official site (undated) + independent dated blog | supported | corroboration |
| F22 syndication | the Marrow Creek ferry is operated by Ostra Lines | a wire story + an edited, attributed copy on another host | insufficient (1 group) | n/a |
| F23 superseded | the Marrow Creek ferry currently runs every 30 minutes | timetable from 2025 (out of window) vs a report dated yesterday saying 45 minutes | disputed | contradiction |
| F24 contradiction | the Tolvane Lighthouse is 41 metres tall | 41 m (undated) vs 38 m (undated) | conflicting | contradiction |
| F25 missing | the Tolvane Lighthouse was designed by Hanne Sollie | nothing names a designer | insufficient | n/a |
| F26–F29 registry | stable / secure / published / recommended | fixture PyPI record for corvane | see §8 | n/a |

## 8. Benchmark: measured before and after

`nsf web bench` (offline, fictional corpus, no network), run on this host on 2026-11-21. "Before" is the working tree before this change: `docs/quality/evidence/webcortex_bench.json` at v0.6.0 shows the same verification numbers. "After" was run with `--repeat 3`, and the metrics were identical across the 3 runs.

| Metric | Before (16 cases) | After, same 16 cases | After, all 29 cases |
|---|---|---|---|
| Verdict accuracy, legacy labels | 12/16 = 0.75 | 16/16 = 1.0 | 28/29 = 0.966 |
| Verification-state accuracy | n/a | 16/16 | 28/29 = 0.966 |
| `supported` recall | 2/5 = 0.4 | 5/5 = 1.0 | 10/10 = 1.0 |
| `supported` precision | 1.0 | 1.0 | 1.0 |
| `disputed` precision / recall | 0.857 / 1.0 | 1.0 / 1.0 | 1.0 / 0.875 |
| Fact-check discrimination (true/false pairs) | 0/2 | 2/2 | 2/2 |
| Contradiction end-to-end P/R | 1.0 / 0.857 | n/a | 1.0 / 0.857 (unchanged) |
| Citation precision (per cited source) | 148/148 | n/a | 209/210 (see below) |
| Safety invariants | hold | n/a | hold |

The cases that changed among the original 16: F08 (Kestrel's creator: the official site plus a wiki page, both undated), F12 (Corran Spire location: undated wiki plus a dated blog) and F14 (Halvorsen Bridge opening year: undated wiki plus dated news) went from `insufficient` to `supported`. F16 (newt neurons: 86 million in an undated wiki page vs 100 million in dated news, one group each) went from `disputed` to `conflicting`, which matches its gold label `contested`.

**Old policy on the new corpus (control).** The v0.6.0 code from `git archive HEAD`, run on the extended corpus, gets 17/29 legacy labels right. It calls F22 (the syndicated copy) `supported`, a false support that the origin detection removes. It calls F17, F18, F20 and F21 `insufficient`, and gives no verdict at all for F26, F27 and F29.

**The remaining miss, F26** ("Is corvane 2.5.4 stable?", gold `disputed`, measured `insufficient`). Intent routes this question as `quick_lookup`, so the PyPI provider is never queried. Without the record, `_registry_verdict` correctly declines to decide stability from web pages. The fix belongs to intent routing (Lane A), which should send registry-aspect questions to the package registries. The same policy with the record read is covered by `test_stable_is_decided_by_pre_release_markers_and_yank_flags`. The gold label was not changed.

**Citation precision 209/210.** One citation is unsupported: the new F22 answer quotes the edited copy's sentence and also cites the wire original, whose sentence differs by one clause. This is the existing `_cite_cluster` behaviour of citing near-duplicate restatements (shingle Jaccard ≥ 0.6), which the new fixture exposed. It is reported here and was not changed in this release.

**Fixture changes.** No existing page, question or gold label was changed. F08, F12 and F14 pass because of the policy, not because of edited dates. Their sources are legitimately undated encyclopedia and project pages, and the statements are stable or historical, so no date is required. Added: pages P54–P61 (Tolvane Lighthouse, Fennick, Marrow Creek ferry) and questions F17–F29. Their gold labels were written with the pages, before any measurement. The old gold label `contested` (F06, F07, F16) is read through `normalise_state` as `conflicting`. `offline.verification` now reports `states` (the six-state confusion) next to the legacy `confusion`.

## 9. Verdict fields

`report["verdict"]` keeps `label`, `confidence`, `statement`, `shape`, `citations`, `supporting`, `contradicting` and `reasons`, and adds these fields:

| Field | Values |
|---|---|
| `state` | One of `claims.VERIFICATION_STATES` |
| `category` | One of `temporal.CLAIM_CATEGORIES` |
| `category_reason` | Why the category was chosen, in words |
| `basis` | `corroboration`, `official_source`, `registry_record`, `version_marker`, `contradiction`, or null |
| `aspect` | For registry statements: `published`, `latest`, `stable`, `compatible`, `secure`, `recommended`; else null |
| `timeframe` | Null, or for time-sensitive statements `{required, window_seconds, class, timely, undated, outside_window}` |
| `independent_groups` | `{supporting, contradicting}`: counted groups after syndication joining |

## 10. Limitations

- The category rules are lexical cue tables. A present-tense statement with no cue ("the museum admits visitors") is `stable`, not `time_sensitive`. `_tense` is a heuristic, and its errors fall on the safe side: a past statement read as present loses only the `historical` category.
- Official-site detection relies on the host name. A project whose domain does not name it (for example `getfoo.io` for Foo) is not recognised, which is the conservative failure. A look-alike domain that names the subject would be believed for a narrow stable or historical fact, but only when no source contradicts it.
- Attribution detection covers English cue phrases and a fixed list of wire services.
- Compatibility checking reads `requires_python` specifiers only (`==`, `!=`, `>=`, `<=`, `>`, `<`, `~=`, `.*`). Other markers are unknown, and an unknown marker is never read as support.
- Consolidation into knowledge still uses the ADR-026 cluster confidence. A stable fact supported by two undated sources gets a `supported` verdict but does not become provisional knowledge on its own (MEMORY_INTEGRATION §3). Aligning the two is a separate owner decision.
