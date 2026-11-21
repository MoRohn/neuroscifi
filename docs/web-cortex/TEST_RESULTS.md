# Web Cortex — measured results

First measurement; no prior baseline exists. Every number below is copied from
`docs/quality/evidence/webcortex_bench.json` (offline, generated 2026-11-21T02:22:11Z) or
`docs/quality/evidence/webcortex_live.json` (live, two runs 2026-11-21T02:18:59Z–02:21:26Z). Code under test:
commit `5f09ac3` plus the uncommitted working tree of the integration branch at that time (`git_dirty: true` in
both files). Host: the development machine (aarch64, 20 CPUs, Python 3.12.16, SQLite 3.53.1, scrapling 0.5.1,
trafilatura 2.3.1, lxml 6.1.3, pypdf 7.1.1, Playwright 1.64.1 with `/usr/bin/chromium`).

Reproduce: `nsf web bench` (offline, `--repeat 3` by default), `nsf web bench --quick`, `nsf web bench --live`.
Implementation: `src/neuroscifi/webcortex/bench/`. Test: `tests/webcortex/test_bench.py`. That test asserts
structure and safety invariants only. It has no accuracy thresholds.

## 1. Method

### Offline benchmark

- **What runs for real.** Intent, the orchestrator (submit, run, cancel, resume), `SafeFetcher` (address and URL
  policy, robots.txt, size, time and type limits, budgets, public cache, ETag revalidation), extraction, quarantine,
  claims, synthesis, consolidation and `knowledge.query`.
- **What is substituted.** DNS and sockets are replaced by `FixtureWeb`'s resolver and connector, which serve the
  corpus from a loopback HTTP server. Search engines are replaced by `providers.corpus.CorpusProvider`, which ranks
  one result per page with BM25 over title and snippet.
- **Task clock.** The clock is fixed per task: T0 = 2026-11-20T12:00Z, plus 60 s per task. As a result,
  `budget.max_seconds` is never reached offline.
- **Individuals.** All research questions run in one fresh individual with the default configuration.
  `min_domain_interval` is 0 because the server is local.
  - The budget, cancel and crash/resume checks run in a second fresh individual.
  - Memory retrieval runs in a third (see §2.8).
- **Gold labels.** Written with the corpus in `bench/corpus.py`, before any measurement, and not derived from
  system output. Verdict gold rule: count the independent source groups in the corpus that state the claim
  (support) or an incompatible value or polarity (contra).

  | Label | Condition |
  |---|---|
  | supported | support ≥ 2 and contra = 0 |
  | disputed | contra > support |
  | contested | support ≥ 1, contra ≥ 1 and support ≥ contra |
  | insufficient | anything else |

- **Determinism.** The whole benchmark ran 3 times in one process. Every key under `metrics` (quality metrics and
  per-item outcomes) was identical in all 3 runs. Wall-clock timing, RSS and file sizes are reported per run and
  are not compared.

### Corpus

The corpus is fictional: entities, hosts, numbers and people are invented, and all text is English.

- **Size.** 51 pages on 30 hosts: 35 article pages and 16 special pages. Search sees 47 results.
- **Page layouts.** Article pages use five layouts with realistic chrome: news (cookie banner, navigation,
  "most read", newsletter, footer), blog (sidebar, tags, reader comments), docs (table of contents, code block,
  feedback widget), wiki (infobox, "see also", categories, footer) and legacy table layout.
- **Dates.** Dates come from `article:published_time`, JSON-LD, `<time>`, or are absent (undated pages).
- **Questions.** 10 authoritative, 7 time-sensitive and 11 fact-verification questions.
  - Two time-sensitive questions have no source inside their window: T04 (Halvorsen Bridge, this week) and T07
    (Tessaly, recent).
- **Rank noise.** Three questions have a keyword-stuffed, outdated or unreliable page meant to rank first: A01
  Quillstream "3.9", A02 Varnel "520 km", A09 Nimbus-OS "6.12".
- **Contradiction pairs.** 12 labelled sentence pairs.
  - 7 real conflicts: 2 numeric, 2 version, 2 polarity, 1 date.
  - 5 non-conflicting controls: 3 same fact in different wording, 1 time series (2015 vs 2025 census), 1
    syndicated copy of the same story on two domains.
- **Injection pages.** 5:
  - a visible imperative;
  - a CSS-hidden block;
  - an instruction split by zero-width characters;
  - `system:` role marker plus chat-template tokens;
  - tool-call JSON naming `169.254.169.254`.
- **Other special pages:**
  - a 6.3 MB HTML bomb with 20 000 nested `div`s (beyond the 5 MB document limit);
  - broken HTML;
  - HTML served as `image/png`;
  - redirects to `internal.test` (127.0.0.1) and to `169.254.169.254`;
  - a robots.txt-disallowed page, with an allowed control page on the same host;
  - a page delayed 1.2 s, probed with a 0.4 s timeout;
  - a 429 response with `Retry-After: 120`;
  - a JavaScript shell whose text exists only after rendering;
  - a PDF;
  - an RSS feed.
- **Extraction gold.** The paragraph text of all 35 article bodies. Title, chrome and comments are excluded.
- **Memory queries.** 11 topic queries, each with a predicate that marks gold knowledge items.
- **Direct probes.** 18 acquisition probes, each with its expected outcome code.

## 2. Offline results

### 2.1 Headline metrics

| Metric | Definition | Result |
|---|---|---|
| Retrieval success | Authoritative questions with ≥ 1 gold supporting URL among the sources read | **9 / 10** (0.90) |
| Evidence coverage | Gold supporting URLs read / gold supporting URLs | **16 / 21** (0.7619) |
| Answer correct, top quote | Gold answer string in the first quoted answer item | **5 / 10** (0.50) |
| Answer correct, any quote | Gold answer string in any quoted answer item | **8 / 10** (0.80) |
| Citation support | Quote items whose text (whitespace-collapsed) is a substring of ≥ 1 cited source's extracted text, with all citations resolving to report sources | **111 / 111** (1.00); 0 unresolved citations |
| Freshness compliance | `report.freshness.satisfied` equals gold (7 time-sensitive questions) | **7 / 7** |
| Recency window parsed | Reported window equals the window stated in the question | **7 / 7** |
| Nothing-fresh disclosure | "Freshness requirement not met" limitation present when no source is in the window | **2 / 2** (T04, T07) |
| Publication-date extraction | Read corpus pages whose extracted date is within 1 day of gold (undated pages must be undated) | **35 / 35** |
| Verdict accuracy | `report.verdict.label` equals the gold label | **5 / 11** (0.4545) |
| Contradiction detector (sentence pairs) | `claims.contradictions` on the claims extracted from the two gold sentences | P **1.0**, R **0.8571**, F1 **0.9231** (TP 6, FP 0, FN 1; 12/12 pairs evaluable) |
| Contradiction, end to end | The research report of the pair's question lists a contradiction between the two URLs | P **1.0**, R **0.4286**, F1 **0.6** (TP 3, FP 0, FN 4; both URLs read for 7 of 12 pairs) |
| Syndicated copy counted once | Finding citing both copies: independent sources vs cited domains | **yes** (2 independent sources over 3 cited domains) |
| Research completion | Status of the 28 question tasks | **28 completed**, 0 partial, 0 failed |
| Memory retrieval (§2.8) | recall@3 / MRR over queries with ≥ 1 gold item in memory | **1.0 / 1.0** on 6 of 11 queries; 5 queries had no gold item in memory |
| Regression stability | Identical `metrics` across runs | **identical in 3 of 3 runs** (0 differing keys) |

### 2.2 Authoritative questions (per item)

The modes were chosen by intent. Questions classified `quick_lookup` read at most 3 pages.

| Id | Mode | Read | Gold read | Top quoted answer |
|---|---|---|---|---|
| A01 Quillstream latest version | technical | 8 | 3/3 | "…first beta of version 4.3 on 17 November 2026." (4.2.0 appears in a later quote) |
| A02 Varnel River length | quick_lookup | 2 | 0/2 | "The Varnel River is 520 kilometres long…" (the rank-noise page) |
| A03 Tessaly population | quick_lookup | 2 | 1/2 | "…population of 248,000 residents." ✓ |
| A04 Halvorsen Bridge opening | quick_lookup | 3 | 2/2 | "…opened in 1987…" ✓ |
| A05 Lumen-7 launch date | quick_lookup | 3 | 1/2 | "…launched on 21 September 2026…" (the conflicting page) |
| A06 Orbix default port | quick_lookup | 2 | 1/2 | "Orbix is a document database used by many small teams." (7420 in a later quote) |
| A07 Kestrel creator | quick_lookup | 3 | 2/2 | "…created by Ilse Marten in 2019." ✓ |
| A08 Brightwater dam height | quick_lookup | 3 | 3/3 | "Standing 186 metres tall…" ✓ |
| A09 Nimbus-OS 5 kernel | technical | 9 | 2/2 | the Quillstream 4.3 beta sentence (unrelated to the question; 6.14 in a later quote) |
| A10 Orbix encryption at rest | quick_lookup | 2 | 1/1 | "Orbix supports encryption at rest using AES-256…" ✓ |

**Rank noise.** In the provider ranking, the noise page was first for 2 of 3 questions; for A02 it was second.

- It became the top quoted answer once (A02).
- None of the 3 noisy questions had a correct top quote.
- In A02 the two gold pages were not read. The page selection keeps `max_pages` = 3 candidates: a redirect to
  `169.254.169.254` (ranked first, blocked), the noise page and the CSS-hidden injection page. Only 2 pages were
  read.

### 2.3 Fact verification

Confusion matrix (rows = gold, columns = predicted):

| gold \ predicted | supported | disputed | contested | insufficient |
|---|---|---|---|---|
| supported (3) | 0 | 0 | 0 | 3 |
| disputed (4) | 0 | 2 | 1 | 1 |
| contested (2) | 0 | 0 | 1 | 1 |
| insufficient (2) | 0 | 0 | 0 | 2 |

Per label:

| Label | Precision | Recall |
|---|---|---|
| supported | — (never predicted) | 0.0 |
| disputed | 1.0 | 0.5 |
| contested | 0.5 | 0.5 |
| insufficient | 0.2857 | 1.0 |

The verdict was "supported" for none of the 11 claims. The reasons in the reports show why:

- **The confidence bar.** F08, the Kestrel creator claim, has "2 independent source group(s) agree…; 0 contradict"
  and a posterior of 0.688, below the 0.75 bar. With the secondary-page weight 0.8, two agreeing dated pages give
  a posterior of 2.6/3.6 = 0.722, and undated pages weigh less. Two agreeing pages therefore cannot reach
  "supported".
- **Unit mismatch.** F02 ("…520 km long") was labelled insufficient with "2 groups agree, 0 contradict". The
  412 km pages were counted as agreeing with "520 km" (§5, item 2).

### 2.4 Contradiction detection (pairs)

The detector missed one conflict: C4, Nimbus-OS 5 "kernel 6.14" vs "kernel 6.12". The shared number "5" in the
product name makes the two value lists compatible (§5, item 3). Every other conflict was detected:

- numeric 412 vs 520;
- numeric 248 000 vs 251 000;
- version v4.2.0 vs v3.9;
- both polarity pairs;
- date 2026-09-14 vs 2026-09-21.

No control was flagged as a conflict. This includes the time series (2015 vs 2025 census) and the syndicated
copy, which formed a single independence group.

End to end, 4 conflicts were missed:

- C1, C2 and C6: the question's task did not read both pages.
- C4: the detector miss above.

### 2.5 Time-sensitive questions

All 7 reports set `freshness.satisfied` as the gold says.

- Each of the 5 questions with an in-window page read and cited that page.
- Both questions without one (T04, T07) carried the "Freshness requirement not met" limitation, and their lead
  sentence says the statements may be out of date.

### 2.6 Safety and limits

| Check | Result |
|---|---|
| SSRF probes: redirect to `internal.test`, redirect to `169.254.169.254`, the metadata IP, `metadata.test`, `internal.test`, `localhost`, `[::1]`, IPv4-mapped loopback, `2130706433`, `10.0.0.5`, metadata host via purpose `api` | **11 / 11 `blocked_policy`**. No direct probe reached the server; the two redirect probes reached only the public redirecting page. |
| robots.txt | Disallowed path → `blocked_robots`; allowed path on the same host → fetched (2 / 2) |
| Limits | Wrong content type → `unsupported_type`; slow page → `timeout` (0.4 s); 429 → `rate_limited` with `retry_after` 120; immediate second request → `rate_limited` **without a network request** (0 requests reached the server); HTML bomb → read up to exactly 5 000 000 bytes, flagged truncated, extraction finished without error (0 characters of text; 0.106–0.160 s) |
| Injection containment | 5 / 5 injection pages were read by research tasks and 5 / 5 were flagged: pattern findings, or `hidden_removed` for the CSS block. **0** canary strings in any answer, finding, contradiction side, stored claim or knowledge statement, in either the main or the memory individual. The injected text does appear 91 times in `sources[].flags.injection[].excerpt`, where the report quotes the flagged text as evidence of the flag. |
| Budgets | 0 violations over all tasks. Tight budget `{max_queries 1, max_requests 3, max_pages 1}` → `partial`, used 1 query, 2 requests, 1 page, with the "budget exhausted" limitations. `{max_bytes 15000}` → used 5 585 bytes. |
| Live flag | 0 reports where `live` disagrees with the cited sources' retrieval modes (59 reports with citations). The repeat of A05 was served entirely by ETag revalidation (`cache` ×3), and its report says `live: false`. |
| Cancel a queued task | `cancelled`, 0 requests |
| Cancel from another connection mid-run | `cancelled` after exactly 1 page fetch; partial report with a "cancelled" limitation; 0 knowledge rows |
| Crash after the first document, then resume with new services | Crash left the task in `reading` with 1 checkpointed document. Resume → `completed`, 0 re-fetches of the checkpointed page, which the report lists as `cache`. |
| Errors recorded in task episodes | `blocked_policy` 6, `blocked_robots` 6, `rate_limited` 8, `unsupported_type` 10. Nothing was swallowed: each is an `error` event. |
| Content probes | PDF read (title and text found; date from the PDF creation date). RSS: 3 entries, all dated, URLs in order. Broken HTML: text found. JavaScript shell: `js_suspected` with 0 static characters; rendered through `SafeFetcher` in sandboxed Chromium, the script-produced sentence is present (`retrieval_mode: rendered`, 1 browser session, 1.15–1.17 s). |

### 2.7 Extraction engines

Token F1 (bag of lower-cased words) against the gold paragraphs of the 35 article pages, `extract_html(...,
engine=…)`:

| Engine | Precision | Recall | F1 |
|---|---|---|---|
| scrapling | 0.783 | 1.0 | **0.8701** |
| trafilatura | 0.8294 | 1.0 | **0.9032** |
| auto (system default) | 0.7573 | 1.0 | **0.8558** (used scrapling on 30 pages, trafilatura on 5) |

By layout (F1, scrapling / trafilatura / auto):

| Layout | Pages | scrapling | trafilatura | auto |
|---|---|---|---|---|
| blog | 8 | 1.0 | 0.9213 | 0.9378 |
| docs | 11 | 0.882 | 0.9185 | 0.882 |
| legacy | 2 | 0.9829 | 0.7419 | 0.9829 |
| news | 10 | 0.7634 | 0.9484 | 0.7634 |
| wiki | 4 | 0.7876 | 0.7925 | 0.7876 |

Per page, trafilatura scored higher on 22 pages and scrapling on 13.

Extraction time per page, run 1 (ms):

| Engine | P50 | P95 |
|---|---|---|
| scrapling | 0.53 | 0.84 |
| trafilatura | 1.23 | 2.75 |

Across the three runs, trafilatura's P50 was 2.3–2.7× scrapling's.

On this corpus trafilatura has the higher mean F1 (+0.033). The `auto` rule, which switches to trafilatura only
when scrapling's text is short, scored below both engines.

### 2.8 Memory retrieval

**Default configuration.** With the default owner configuration (≥ 2 independent sources and confidence ≥ 0.75),
consolidation of the 28 main tasks produced **0 provisional items**. It opened 9 contested-knowledge questions and
held the rest:

- 152 held as "single source that is not a primary structured source";
- 12 held as "confidence below the bar".

**Measurement individual.** Retrieval was therefore measured in a separate individual. It runs the same questions
with one documented change: `auto_provisional_min_confidence = 0.0`. The ≥ 2-source and no-contradiction bars stay.

- It produced 6 provisional items.
- All 6 were accepted programmatically. This is a **simulated owner acceptance**, labelled as such in the JSON. It
  is not an owner decision.

**Results.**

- 6 of 11 queries had a gold item in memory, and for those `knowledge.query` returned it at rank 1: recall@3 =
  1.0, MRR = 1.0.
- The other 5 queries (Varnel length, Tessaly population, Lumen-7 launch date, Orbix encryption, Kestrel 2.0
  release) had no gold item, because consolidation produced none for them.
- 2 of those 5 queries returned one non-gold item each.

With only 6 items in memory this is a weak test of ranking.

### 2.9 Latency and resources

Measured offline with wall-clock time, on the loopback fixture server. **These numbers are not comparable to live
latency.**

| Mode (tasks) | P50 s | P95 s |
|---|---|---|
| quick_lookup (14) | 0.0139 | 0.0694 |
| fact_verification (11) | 0.0326 | 0.0599 |
| technical (3) | 0.042 | 0.1605 |
| all (28) | 0.0243 (runs 2–3: 0.0223, 0.0230) | 0.0733 |

Per task usage (28 tasks), P50 / P95 / max:

| Resource | P50 | P95 | Max |
|---|---|---|---|
| Queries | 2 | 4 | 5 |
| Requests | 5 | 10.9 | 16 |
| Pages | 3 | 8 | 9 |
| Bytes | 5 905 | 5 003 403 | 5 005 192 (the HTML bomb) |

Logical SQLite growth per task, P50 / P95 / max:

| Store | P50 | P95 | Max |
|---|---|---|---|
| Individual | 26 624 B | 73 728 B | 81 920 B |
| Public cache | 4 096 B | 13 517 B | 20 480 B |

After 29 tasks the main individual was 1.34 MB and its cache 147 KB.

Peak RSS of the benchmark process was 141.5 / 151.2 / 156.8 MB after runs 1 / 2 / 3. This is process-wide: it
includes the in-process fixture server and the earlier runs.

## 3. Live operational validation (real internet)

**Setup.**

- A temporary individual, deleted afterwards, with web access enabled.
- The open-webSearch 2.2.0 daemon, started on loopback port 3291 and stopped again by the same run. The engines
  were duckduckgo, brave, startpage and bing, plus the structured providers.
- Two consecutive runs: run 1 at 02:18:59–02:19:30Z, run 2 at 02:20:44–02:21:26Z.
- The only harness change between the runs was that run 2 also records each task's planned queries and search
  rounds.

**Request totals.** Requests charged by `SafeFetcher` and the search provider:

| Run | Requests |
|---|---|
| 1 | 82 (75 by tasks, 7 by probes) |
| 2 | 92 (85 by tasks, 7 by probes) |
| Both | 174 |

About 5 further upstream calls were made in a manual DuckDuckGo check (below). Per-scenario budgets: A 30, B 60,
D 30 per run, E 40 requests.

**Relevance.** No human judged the relevance of any live result. The statements below report what the system
returned.

| Scenario | Run 1 | Run 2 |
|---|---|---|
| **A** "most important developments in open-source AI inference this week" (`quick_lookup`, breaking, 7-day window) | 5 sources, **all Wikipedia API records**. The general engines gave no usable results: duckduckgo empty, brave HTTP 429, startpage empty, bing "regional redirect limit". `freshness.satisfied = true`, because a structured record fetched live counts as fresh at retrieval time. Answer: list and definition sentences, no dated development. 6 requests, 3.05 s. | 5 **live web pages** found through duckduckgo: devflokers.com (2026-06-03), huggingface.co blog (2025-11-13), aimultiple.com (2026-03-04), awesomeagents.ai (2026-04-17), Wikipedia list page (2025-07-20). `freshness.satisfied = false`; limitation "newest source is from 2026-06-03 (required: within 7 days)". Top statement corroborated by 3 groups: "Open-Source AI June 2026: New Models, Agents & Papers". 12 requests, 11.35 s. |
| **B** dendritic computation, `scientific` | 10 sources, all structured records (OpenAlex/Crossref DOIs, Wikipedia, PubMed). Relevant: Neural Computation 2026 "A Dendritic Voltage Surrogate-Based Synaptic Learning Framework…", Nature Communications 2026 "Dendritic computation bridges neuroscience and foundation models", Wikipedia "Dendrite". Off-topic: "Artificial consciousness", "Recurrent neural network", two immunology PubMed records, a memristor review, songbird reviewer reports. 26 requests, 6.08 s. | Same pattern; added "Neuronal computation on complex dendritic morphologies" (doi 10.82444/warw.33629662). 26 requests, 6.28 s. |
| B → architecture implications (heuristic) | **1 proposal**, dendritic-nonlinearity: 10 findings share vocabulary with `REQUIREMENTS_MATRIX.md:74` (GRA-12, deferred "dendritic rules"), `Architecture.md:96` (functional plasticity) and `:600`/`:602` (the architecture's own references to Wright et al. 2025 and Gidon et al. 2020). The other 4 themes had no matching findings: compartment-specific plasticity, eligibility traces, STDP, neuron as network. Proposal only: nothing was changed. | Same single proposal (16 matching findings). |
| **D** owner monitor "predictive coding", 1 h interval | Tick 1: 1 task, 14 requests, 5 sources, baseline 5 URLs and 11 claim keys. DB reopened: monitor state identical. Tick 2 (clock +3700 s): 1 task, 13 requests. **1 "meaningful change" reported**: 2 new URLs (arXiv 1910.12607 from 2019, an unrelated PubMed record) and 5 new claim keys. Wikipedia answered 429 in that tick and the source mix changed. This is search-result churn 1.5 minutes after the baseline, not a change in the world. Runs = 2. | Tick 1: 14 requests; tick 2: 14 requests; **0 meaningful changes**; state preserved across the reopen; runs = 2. |
| **E** "Is it true that the human brain contains 100 billion neurons?" (`fact_verification`) | 8 structured sources, including von Bartheld et al. 2016 ("The search for true numbers…": "for half a century, the human brain was believed to contain about 100 billion neurons"), Herculano-Houzel 2009 "The human brain in numbers", Goriely 2024 "Eighty-six billion and counting", the 2025 reply "Yes, the human brain has around 86 billion neurons", and Wikipedia "Human brain" and "Brain". **Real disagreement found and listed**: 100 billion vs 86 billion (`different values: 100000000000 neurons vs 86000000000 neurons`). Verdict **insufficient** (posterior 0.559); the reasons say "2 groups agree, 0 contradict" and also list the 86-billion and 14–16-billion conflicts. 10 contradiction entries; several are spurious (the "10, 1 vs 14" glia:neuron ratio vs the cortical neuron count, and cerebral-cortex count vs whole-brain count). 16 requests, 5.26 s. | Same 8 sources, same verdict (insufficient, 0.559), same contradictions. 19 requests, 6.40 s. |

What scenario E established:

- The report surfaces both figures with their sources, and it marks the cluster contested.
- It did not establish which figure is correct.
- It did not label the claim disputed, although the newer sources it cites state ~86 billion.
- The Wikipedia sentence that mentions 100 billion as the historical belief was treated as supporting the claim.

**Probes (both runs).**

| Probe | Result |
|---|---|
| `http://169.254.169.254/` | `blocked_policy` (link-local / metadata) |
| `http://localhost/` | `blocked_policy` (reserved local host name) |
| `https://www.google.com/search?q=x` | `blocked_robots`, after 1 robots.txt request |
| JavaScript rendering (`https://quotes.toscrape.com/js/`, with rendering switched on for the temporary individual) | Static extraction flagged `js_suspected` (22 characters). The sandboxed render completed in 4.47 s / 4.96 s and also returned 22 characters ("Quotes to Scrape / Login"). The renderer refused `bootstrap.min.css`, `main.css` and `jquery.js` with `unsupported_type` (§5, item 5). |

**Provider status after the runs.** All circuits closed. Successes and failures:

| Provider | Run 1 | Run 2 |
|---|---|---|
| duckduckgo | 12 / 0 | 13 / 0 |
| brave | 0 / 1 (HTTP 429) | 0 / 1 (HTTP 429) |
| startpage | 0 / 0 | 0 / 1 ("0 results while another engine answered") |
| bing | 0 / 1 ("regional redirect limit") | 0 / 1 ("regional redirect limit") |
| wikipedia | 10 / 2 (run 1: `rate_limited`) | 11 / 0 |
| arxiv, openalex, crossref, pubmed | 10 / 0 each | 10 / 0 each |
| pypi, npm, osv, github | 0 / 0 (not used by these questions) | 0 / 0 |

**Manual DuckDuckGo check.** In run 1, duckduckgo returned empty answers for scenario A's queries. Afterwards, a
manual check through a second daemon on port 3292 returned 8 results for each of 4 sequential queries, including
the two queries planned for scenario A. The empty answers were not reproduced, and their cause was not established.

**Scenarios C and F** are run by Lane E1 (`nsf web deps`, evidence `docs/quality/evidence/webcortex_scenario_c.json`)
and Lane E3 (Pocket). Their results are not repeated here.

## 4. Baseline

This is the first measurement of Web Cortex. No earlier result exists to compare against, and nothing here is
an improvement claim.

## 5. Behaviour the measurements exposed

Each item is a measured fact with its evidence key.

1. **Two agreeing secondary sources cannot reach the provisional or "supported" bars.** The posterior for two
   dated pages is 0.722, below the 0.75 bar, and undated pages are weighed lower. Evidence: `consolidation`
   (0 provisional items), verdicts F08 and F09.
2. **A unit in the claim hides a numeric conflict.** `text.values("the Varnel River is 520 km long")` →
   `['520 km']`, but `text.values("The Varnel River is 412 kilometres long…")` → `['412', '9800']`, and
   `text.values_conflict(['412', '9800'], ['520 km'])` → `None`. Result: F02 lists "2 groups agree, 0 contradict".
3. **A shared incidental number masks a differing value.** `text.values_conflict(['5', '6.14'], ['5', '6.12'])` →
   `None`. The product name "Nimbus-OS 5" supplies the shared 5. Result: C4 missed and F07 labelled insufficient.
4. **Page selection truncates before fetching.** `_Run.select` truncates the candidate list to `max_pages` before
   fetching, so blocked or failed candidates use up page slots (A02: 2 pages read of 3 allowed, gold pages unread).
   In scientific mode, structured API results are selected first and filled all 10 slots live, so no web page was
   read in scenario B.
5. **The renderer refuses external scripts and styles.** Every sub-resource goes through
   `SafeFetcher.fetch(purpose="page")`, and `net.CONTENT_TYPES` has no `application/javascript`, `text/javascript`
   or `text/css`, so they are refused with `unsupported_type`. Offline, a page whose content comes from an
   *inline* script rendered correctly. Live, the external-script page rendered no more text than the static fetch.
6. **Monitor change detection counts search churn as change.** It treats new single-source records from structured
   APIs as "solid" new claims, so a change in which records the search returns is reported as a meaningful change
   (live D, run 1).
7. **Retrieval time makes API records "fresh".** Freshness for a "this week" question was satisfied by Wikipedia
   records fetched live, because an API record counts as fresh at its retrieval time (live A, run 1).
8. **Planning sends the self-referential clause to search engines.** In scenario B, 4 of 8 planned queries were
   "NeuroSciFi learning architecture", sent to the search engines and academic APIs; all returned nothing.
9. **Report excerpts carry the flagged text.** Reports carry the excerpts of flagged injection text in
   `sources[].flags.injection[].excerpt` (91 occurrences offline). Answers, findings, claims and knowledge never
   carry them.

## 6. What is not covered

- **No human relevance judgments** on any live result. Live "relevant" and "off-topic" above are the author's
  reading of titles, not a scored judgment. There is no live gold set, so there are no live precision or recall
  numbers.
- **Small, fictional, English-only offline corpus.** 51 pages, 28 questions and 12 contradiction pairs, written by
  the same lane that wrote the harness, so the pages may not reflect real-web difficulty: real chrome, ads,
  paywalls, consent walls, non-English text and adversarial SEO at scale. Every rate above has a small
  denominator.
- **No real search engine offline.** `CorpusProvider` ranks one result per page by BM25. Live search quality
  appears only in the two live runs.
- **Offline latency is loopback latency.** It says nothing about network latency. Live wall times come from two
  runs of each scenario on one host at one time of day.
- **Memory retrieval.** Measured over 6 knowledge items, with a lowered confidence bar and simulated owner
  acceptance.
- **Not exercised:** the `nsf web work` service; Pocket and console paths (other lanes); MCP; feeds and crawls
  inside research tasks; sitemaps; `deep_research`, `comparative` and `change_detection` modes offline (intent
  chose `quick_lookup`, `technical` and `fact_verification` for this corpus); TLS; DNS rebinding (covered by
  `tests/webcortex/test_net.py`, not here); `budget.max_seconds` (the offline clock is fixed); concurrent tasks;
  long-running monitors; the autonomous scheduler path.
- **Live budgets were small:** ≤ 60 requests per scenario and 174 requests in total. Engine behaviour (429s,
  redirect limits, empty answers) changed between runs two minutes apart. Two runs do not characterise its
  variance.
- **GPU unused.** No stage uses a model (ADR-022).
