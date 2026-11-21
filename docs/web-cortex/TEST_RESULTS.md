# Web Cortex: measured results

Final measurements for 0.6.0 (Web Cortex plus user-acceptance testing). Every number below is read from
`docs/quality/evidence/*.json` or `docs/qa/*.md`; nothing is estimated.

| Evidence | File | Generated | Code |
|---|---|---|---|
| Offline benchmark, baseline | `docs/quality/evidence/webcortex_bench_baseline.json` | 2026-11-21T02:22:11Z | `git_commit: 5f09ac3`, `git_dirty: true` (committed as `849f7bf`) |
| Offline benchmark, final | `docs/quality/evidence/webcortex_bench.json` | 2026-11-21T09:26:19Z | `git_commit: 4eeaed1`, `git_dirty: true` (uncommitted documentation and one scheduler change) |
| Live scenarios A, B, D, E | `docs/quality/evidence/webcortex_live.json` | see §3 | `git_commit: 4eeaed1`, `git_dirty: true` |
| Scenario C (dependencies) | `docs/quality/evidence/webcortex_scenario_c.json` | see §3 | `nsf web deps` |
| Final regression and UAT | `docs/qa/REGRESSION_REPORT.md`, `E2E_TEST_RESULTS.md`, `DEFECT_RESOLUTION_LOG.md` | | the final commit recorded in `docs/qa/REGRESSION_REPORT.md` |

Host: NVIDIA DGX Spark, aarch64, 20 CPUs, Python 3.12.16, SQLite 3.53.1, scrapling 0.5.1, trafilatura 2.3.1,
lxml 6.1.3, pypdf 7.1.1, Playwright 1.64.1 with `/usr/bin/chromium`.

Reproduce: `nsf web bench` (offline, 3 runs by default), `nsf web bench --quick`, `nsf web bench --live`.
Implementation: `src/neuroscifi/webcortex/bench/`. Test: `tests/webcortex/test_bench.py`, which asserts structure and
safety invariants only and has no accuracy thresholds.

## 1. Method

- **What runs for real.** Intent, the orchestrator (submit, run, cancel, resume), `SafeFetcher` (address and URL
  policy, robots.txt, size, time and type limits, budgets, public cache, ETag revalidation), extraction, quarantine,
  claims, synthesis, consolidation and `knowledge.query`.
- **What is substituted.** DNS and sockets are replaced by `FixtureWeb`, which serves the corpus from a loopback
  HTTP server. Search engines are replaced by `providers.corpus.CorpusProvider` (BM25 over title and snippet).
  Package registries (PyPI, npm) are fixtures in the final corpus.
- **Task clock.** Fixed per task, so `budget.max_seconds` is never reached offline.
- **Gold labels** were written with the corpus in `bench/corpus.py`, not derived from system output. Verdict gold
  rule: count independent source groups that state the claim (support) or an incompatible value or polarity
  (contra). supported = support ≥ 2 and contra = 0; disputed = contra > support; contested = support ≥ 1, contra ≥ 1
  and support ≥ contra; insufficient = anything else.
- **Determinism.** The benchmark runs 3 times in one process; every key under `metrics` is identical in all runs
  (`regression.identical: true`, no differing keys, in both files).

### The two corpora are not the same

The corpus grew between the baseline and the final run because UAT defect classes were added as new pages and
questions. **The baseline and final columns are not a same-corpus comparison.** Metrics that moved may have moved
because the questions changed, not only because the code did.

| | Baseline | Final |
|---|---|---|
| Pages (article + special) | 51 (35 + 16) | 70 (53 + 17) |
| Hosts | 30 | 40 |
| Question tasks | 28 (10 authoritative, 7 time, 11 verification) | 38 (12 authoritative, 2 latest-version, 8 time, 16 verification) |
| Contradiction pairs (conflicts + controls) | 7 + 5 | 7 + 5 |
| Injection pages | 5 | 6 |
| Search results | 47 | 66 |

The corpus is fictional (invented entities, hosts, numbers and people) and in English. New in the final corpus:
2 true/false fact pairs, 1 number check, 2 latest-version questions, 20 subjects for cross-subject checks, 1
encyclopedia snippet page, and fixture PyPI and npm registries.

## 2. Offline results: baseline vs final

### 2.1 Headline metrics

| Metric | Baseline | Final | Note |
|---|---|---|---|
| Retrieval success (≥ 1 gold URL read) | 0.90 (9/10) | **1.00** (12/12) | |
| Evidence coverage (gold URLs read / gold URLs) | 0.7619 (16/21) | **0.875** (21/24) | |
| Top answer correct | 0.50 (5/10) | **0.6667** (8/12) | |
| Any quote correct | 0.80 (8/10) | 0.75 (9/12) | lower on the larger corpus |
| Top answer on-subject | not measured | **14/14** | new metric |
| Citation precision per cited source | not measured | **1.00** (148/148 citations) | new metric: every cited source contains the quote |
| Citation support, legacy (any cited source) | 1.00 (111/111) | 1.00 (145/145) | |
| Verification accuracy | 0.4545 (5/11) | **0.75** (12/16) | supported recall 0.4, disputed recall 1.0 |
| True/false claim-pair discrimination | not measured | **0/2** | open, see §2.3 |
| Number check (wrongly supported) | not measured | 1/1 ok, 0 wrongly supported | |
| Contradiction detector F1 | 0.9231 (TP 6, FN 1) | **1.0** (TP 7, FN 0) | |
| Contradiction end to end F1 | 0.60 (TP 3, FN 4) | **0.9231** (TP 6, FN 1) | |
| Cross-subject merged answer items | (23 at an intermediate stage) | **0** of 17 items, 0 of 18 findings | |
| Latest-version correctness | not measured | **2/2** | |
| Code-literal contradictions / snippet records read as documents | not measured | 0 / 0 | |
| Freshness compliance | 7/7 | 8/8 | |
| Publication-date extraction | 35/35 | 53/53 | |
| Research completion | 28 completed | 38 completed | |
| Injection containment | 5/5 flagged, 0 leaks | 6/6 flagged, 0 leaks | |
| SSRF / robots / limits probes | 11/11, 2/2, 5/5 | 11/11, 2/2, 5/5 | |
| Budget violations | 0 of 59 tasks | 0 of 79 tasks | |
| Memory recall@3 / MRR | 1.0 / 1.0 | 1.0 / 1.0 | simulated owner acceptance (§2.5) |
| Determinism (3 runs) | identical | identical | |
| Safety summary (`safety.all_hold`) | true | true | |

### 2.2 Authoritative questions

Gold URLs read and whether the top quoted answer is correct, baseline then final.

| Id | Gold read | Top correct |
|---|---|---|
| A01 Quillstream latest version | 3/3, 3/3 | no, no |
| A02 Varnel River length | 0/2, 1/2 | no, no |
| A03 Tessaly population | 1/2, 1/2 | **yes, no** |
| A04 Halvorsen Bridge opening | 2/2, 2/2 | yes, yes |
| A05 Lumen-7 launch date | 1/2, 1/2 | no, no |
| A06 Orbix default port | 1/2, 2/2 | **no, yes** |
| A07 Kestrel creator | 2/2, 2/2 | yes, yes |
| A08 Brightwater dam height | 3/3, 3/3 | yes, yes |
| A09 Nimbus-OS 5 kernel | 2/2, 2/2 | **no, yes** |
| A10 Orbix encryption at rest | 1/1, 1/1 | yes, yes |
| A11 Brisa language | new | yes |
| A12 Dendritic gating paper | new | yes |

A03 got worse: its top quote is the other side of the conflict (251,000, gold 248,000). A02 is still answered wrongly
(the top quote is the noise page's "520 kilometres ... we paddled every one of them"). The noise page is ranked first
for 2 of 3 noisy questions in both measurements; the top quote came from the noise page for 1 question (A02) in
both, and that quote is correct for 1 of 3 in the final run.

### 2.3 Fact verification

Gold → predicted, final run (supported / disputed / contested / insufficient):

| gold (n) | supported | disputed | contested | insufficient | precision / recall |
|---|---|---|---|---|---|
| supported (5) | 2 | 0 | 0 | 3 | 1.0 / **0.4** |
| disputed (6) | 0 | 6 | 0 | 0 | 0.8571 / **1.0** |
| contested (3) | 0 | 1 | 2 | 0 | 1.0 / 0.6667 |
| insufficient (2) | 0 | 0 | 0 | 2 | 0.4 / 1.0 |

The baseline had no `supported` prediction at all (recall 0.0 on 3 claims) and `disputed` recall 0.5 on 4 claims.

**Open: true/false discrimination is 0/2.** In both new pairs (F12/F13, F14/F15) the false claim is correctly
`disputed`, but the true claim stays `insufficient` although 2 independent source groups agree with it. The cause is
the documented rule that two agreeing sources with one undated stay "insufficient" (ADR-026; OPEN-01 in
`docs/qa/DEFECT_RESOLUTION_LOG.md`, risk R-2 in `docs/qa/PRODUCTION_READINESS.md`). Supported recall (0.4, 5
claims) is the same limit seen from the other side. F16 (100 million vs 86 million neurons, gold contested) is
predicted `disputed`, which the number check accepts.

### 2.4 Contradiction detection, time-sensitive questions, safety

- **Detector** on 12 labelled pairs: all 7 conflicts found, 0 of 5 controls flagged (baseline missed one). **End to
  end** (the report lists the conflict): 6 of 7; one miss (polarity, both pages read). Syndicated copies are counted
  once in both runs (2 independent sources over 3 domains).
- **Time-sensitive**: 8/8 compliant, 8/8 recency windows parsed, 3/3 nothing-fresh disclosures.
- **Dating**: the HTTP Last-Modified header is never used as a publication date (2/2 pages dated correctly).
- **Safety**: SSRF 11/11 `blocked_policy`; robots.txt 2/2; limits 5/5 (type, timeout, 429, back-off with 0 server
  requests, 5 MB HTML bomb truncated); injection 6/6 flagged, 0 leaks into answers, findings, claims or knowledge;
  0 budget violations in 79 tasks; live flag honest; queued and running cancel and crash-then-resume pass
  (0 re-fetches of the checkpointed page). Content probes: PDF, RSS (3 dated entries), broken HTML and a JavaScript
  shell rendered in sandboxed Chromium (1 browser session).
- **Package routing**: a plain package name with a release cue is looked up in the (fixture) registry and the
  registry record is read and cited (1/1).

### 2.5 Consolidation and memory retrieval

Default configuration (≥ 2 independent sources, confidence ≥ 0.72): final run 2 provisional items, 15 contested, 17
pending; baseline 0 provisional, 9 contested, 9 pending. Most claims are held because they come from a single source
that is not a primary structured source (219 in the final run, 152 in the baseline) or from a source with
instruction-like content (21; not present in the baseline).

Memory retrieval runs in a separate individual with a simulated owner acceptance of every provisional item. **This is
a benchmark simulation, labelled as such in the JSON, not an owner decision.** Final: 5 items accepted, 5 of 11
queries have a gold item in memory, recall@3 = 1.0 and MRR = 1.0 over those 5 (baseline 6 items, 6 queries). With so
few items this is a weak test of ranking.

### 2.6 Latency and resources

Wall-clock on the loopback fixture server; **offline, no internet latency, not comparable to live.** Run 1 of 3.

| | Baseline | Final |
|---|---|---|
| All tasks p50 / p95 / max | 0.0243 / 0.0733 / 0.1737 s (28 tasks) | 0.0439 / 0.1513 / 0.1963 s (38 tasks) |
| Whole benchmark run | 3.81 s | 6.364 s |
| Peak RSS | 141 537 280 bytes | 153 284 608 bytes |

The final run has more tasks and more pages per task, and other processes shared the host; the cause of the higher
timings was not separated.

### 2.7 Extraction engines (mean F1 on article pages)

| | Baseline (35 pages) | Final (53 pages) |
|---|---|---|
| scrapling | 0.8701 | 0.8534 |
| trafilatura | 0.9032 | 0.8851 |
| auto | 0.8558 | 0.8384 |

The `auto` rule still scores below both engines.

## 3. Live operational validation (real internet, bounded)

Source: `docs/quality/evidence/webcortex_live.json`. A temporary individual with web access enabled; the open-webSearch
2.2.0 daemon was started by the run on loopback port 3291 and stopped by the same run. 84 requests were charged in
total (75 by tasks, 9 by probes; 20 search queries). Per-scenario request budgets: A 30, B 60, D 30, E 40. No human
judged the relevance of any live result; the rows report what the system returned. One run per scenario gives no
variance.

| Scenario | Result |
|---|---|
| **A** "most important developments in open-source AI inference this week" | Completed, `quick_lookup`, 5 sources, live, `freshness.satisfied = true` (newest source 2026-11-20). 15 requests, 8.05 s. All 5 sources were Wikipedia API records: the key-free search engines returned empty, HTTP 429 or a Startpage token error. "Satisfied" says recent pages exist, not that they describe an inference development. |
| **B** dendritic computation vs the NeuroSciFi learning architecture | Completed, `scientific`, 10 sources (DOI, arXiv and PubMed records), live, freshness satisfied (newest 2026-11-12). 5 queries, 22 requests, 5.81 s. 2 architecture proposals from a keyword mapping (labelled heuristic); nothing was applied. |
| **D** monitor "predictive coding", 1 h interval | Regenerated at `abcd303`: tick 1 set the baseline; tick 2 (clock +3700 s) reported **0 meaningful changes**; monitor state identical after the database was reopened. At `4eeaed1` the same scenario reported 1 false change (search churn: a different paper's date under a shared record-template key); fixed in `abcd303` (FR-03, `test_a_value_appearing_under_a_shared_record_template_key_is_not_a_change`). |
| **E** "Is it true that the human brain contains 100 billion neurons?" | Completed, `fact_verification`, 8 sources, verdict **insufficient** (0.5): 1 independent group agrees, with 3 contradicting sentences in the reasons ("86000000000 neurons, 2025-02-06 instead of 100000000000 neurons"). Freshness requirement reported unmet. 10 requests, 4.97 s. The report surfaces both figures; it did not label the claim `disputed`. |

**Probes.** `http://169.254.169.254/` and `http://localhost/` return `blocked_policy`; `https://www.google.com/search?q=x`
returns `blocked_robots` after 1 request. JavaScript rendering of `https://quotes.toscrape.com/js/`: static fetch is
`js_suspected` (22 characters); rendered text is 1 433 characters (`retrieval_mode: rendered`, 3.34 s, 8 requests).

**Search engines.** With the honest User-Agent (ADR-032) the key-free engines returned nothing during these runs.
At the end of the run the open-webSearch provider and its brave and startpage engines had open circuits; Wikipedia,
arXiv, OpenAlex, Crossref and PubMed answered 8 of 8 calls each. Discovery therefore rested on structured APIs. This
reflects the day and the call pattern, not only the code.

**Scenario C (dependency research, `nsf web deps`).** Evidence: `docs/quality/evidence/webcortex_scenario_c.json`.
49 packages: 39 up to date, 9 "consider upgrade" (6 behind a major version), 1 security advisory (zod
GHSA-mrhx-c7xc-m3w5 in the open-webSearch dependency tree, accepted risk R-5), 0 unknown; 98 requests, 48.6 s,
no failures. Nothing was installed or modified. The research task covered the first 4 of 49 packages by its query
budget; the direct pass covered all 49.

**Scenario F (research from Pocket, multi-device).** Test:
`tests/test_pocket_research.py::test_scenario_f_phone_queues_spark_runs_tablet_reads`, plus browser retests in
`tests/e2e/test_pocket_research_e2e.py` and the UAT retests in `docs/qa/E2E_TEST_RESULTS.md`. It uses in-process devices
and a fixture web, not real handsets and not the real internet.

## 4. Quality gate and user-acceptance testing

Gate (`bash scripts/check.sh`), browser suites and acceptance, as recorded in `docs/qa/REGRESSION_REPORT.md`:

| Check | Result |
|---|---|
| Lint | `ruff` clean |
| Python unit and integration | **1557 passed**, 6 skipped (opt-in live tests), 35 subtests |
| JavaScript checks | 4/4 |
| Numerical reproduction | 8/8 bit-exact |
| Browser end to end (`tests/e2e`) | **107 passed**, twice in a row: test_console 12, test_console_research 6, test_console_uat 26, test_pocket 13, test_pocket_research_e2e 9, test_pocket_uat 41 (test counts from `pytest --collect-only`) |
| Acceptance (`nsf acceptance --all`, installed 0.6.0 wheel) | 7/7 pass (simulated, ADR-014) |

**UAT summary.** Discovery on RC1 ran 6 workstreams (Pocket, console, live research, security, UX; the backend
tester stalled and the integrator re-ran that scope) and found about 90 defects. Fix waves RC2 and RC3, independent
retests R1 to R4 and final fixes followed. Every critical and high defect is fixed and was independently
re-verified, and each fix has a regression test that fails on the commit before it. Details:
`docs/qa/USER_JOURNEY_MATRIX.md`, `E2E_TEST_RESULTS.md`, `DEFECT_RESOLUTION_LOG.md`, `UX_VALIDATION.md`,
`REGRESSION_REPORT.md` and `PRODUCTION_READINESS.md` (open risks in §3).

## 5. What is not covered

- **Relevance of live results** was not judged by a human, and no live ground truth exists. One live run per
  scenario gives no variance; which engines answer depends on the day.
- **Not a same-corpus comparison.** The final corpus has 70 pages and 38 tasks against 51 pages and 28 tasks in the
  baseline, so baseline-to-final changes cannot be attributed to code alone. The corpus is fictional and English,
  and its gold labels were written with the system's design in view, not blind.
- **Small label counts.** There are 5 supported, 6 disputed, 3 contested and 2 insufficient fact claims; one verdict
  flip moves accuracy by 6 points, and per-label rates rest on 2 to 6 items. True/false discrimination rests on 2
  pairs.
- **True claims are often not confirmed** (supported recall 0.4, discrimination 0/2): open item OPEN-01, the
  undated-source rule.
- **Top answers are still wrong for 4 of 12 authoritative questions** (A01, A02, A03, A05), and one end-to-end
  conflict is missed.
- **Offline latency is loopback latency.** It says nothing about internet latency, rate limits or anti-bot behaviour.
- **Memory retrieval** depends on a simulated owner acceptance of 5 items, not on a real, populated memory.
- **Key-free search is effectively unavailable** with the honest User-Agent (OPEN-03); general web discovery relies
  on structured APIs and feeds.
- **Scenario F** used in-process devices and a fixture web. Real handsets, push notifications, screen readers and
  unattended long-running autonomous research were not exercised (`docs/qa/PRODUCTION_READINESS.md` §4).
- **Extraction** engines are scored on 53 synthetic article pages; no real-world extraction benchmark was run.
- **Evidence provenance.** Both benchmark files record `git_dirty: true`: they were generated from a working tree
  with uncommitted changes.
