# Final integrated regression report

**Commit verified:** `abcd303` (version 0.6.0) on `build/neuroscifi-v1`. Code at this commit is what every result below
measured; later commits add documentation and regenerated evidence only.
**Host:** NVIDIA DGX Spark (aarch64, 20 cores, GB10), Arch-based distribution, Python 3.12.16, Node v26.13.0,
Chromium 153 (system, `/usr/bin/chromium`). No camera, microphone or handset attached.
Every number was produced by the integrator's own run, not taken from an agent's report.

## 1. Automated suites

| Suite | Command | Result |
|---|---|---|
| Lint | `ruff check .` (inside `scripts/check.sh`) | All checks passed |
| Python unit + integration | `pytest -q --ignore=tests/e2e` (inside `scripts/check.sh`) | **1557 passed**, 6 skipped, 35 subtests passed, 0 failed |
| JavaScript checks | `node tests/js/*.mjs` | 4 of 4 passed (field benchmark coverage 0.888 / selective accuracy 0.883, unchanged) |
| Numerical reproduction | `python scripts/reproduce.py` | **8 of 8 Verified** (reference, sensory, recalibration, lineage, graph, human_counts, graph_packing, recall), bit-exact |
| Browser end-to-end | `NSF_CHROMIUM=/usr/bin/chromium pytest -q tests/e2e` | **107 passed** in each of the last three full runs (two consecutive at `4eeaed1`, one at `abcd303`), 0 failed (test_console 12, test_console_research 6, test_console_uat 26, test_pocket 13, test_pocket_research_e2e 9, test_pocket_uat 41); axe-core 0 violations of any impact on every view at 390/1024/1280 |
| Acceptance scenarios | `nsf acceptance --all` | **7 of 7 pass**: 124 s from the installed 0.6.0 wheel at `8054a7f`; 125 s at `abcd303` (simulated, ADR-014) |

The 6 skipped tests are the opt-in live-internet tests (`NSF_LIVE=1`); their live equivalents are §4.

Growth over the run: the pre-Web-Cortex gate had 303 tests (0.5.0); RC1 1333; RC2 1458; RC3 1524; Final 1557. Every defect fix
in `DEFECT_RESOLUTION_LOG.md` added a regression test that fails on the commit before the fix.

## 2. Reproducible build and start

| Check | Result |
|---|---|
| `uv build --wheel` from a clean `git archive` export (no `build/` written into the repository) | `neuroscifi-0.6.0` wheel built |
| Install the wheel with `[web]` into a fresh Python 3.12 venv | succeeded |
| `--help` of every command and 15 nested subcommands (42 invocations) | 0 failures |
| Package data present (console and Pocket web assets, benchmark data) | yes |
| README journey with the installed package (init, epoch, observe ×3, status, decide, explain, doctor) | all succeeded; `nsf doctor` ran 15 checks |
| Migration: instance created by the 0.5.0 code (commit `7179c3f`, schema 5) opened by 0.6.0 | migrated to schema 7, evidence intact, web inventory present |
| Newer schema (user_version 99) | refused: "Instance schema 99 is newer than this software (7)" |

## 3. Re-executed user journeys

| Round | Commit | Executed by | Outcome |
|---|---|---|---|
| Discovery | RC1 `64da95b` | 6 parallel workstreams (Pocket core, console, live research, security, UX; backend stalled) | ~90 defects (6 Pocket, 16 console, 23 research, 14 security, 31 UX) |
| Retest | RC2 `cce5134` | 3 independent retesters (browser, live research, backend) | Nearly all RC1 defects PASS; 2 new high (Wikipedia regression, location containment) and 8 low–medium found |
| Final retest | `8054a7f` | independent retester R4 | every RC3 fix PASS; 6 low–medium new defects (RT4-01…06) fixed in `4eeaed1` |
| Final regression | `4eeaed1`, `abcd303` | integrator | two Pocket timing defects (FR-01, FR-02) and one monitor false positive in the live evidence (FR-03) found and fixed at the root |

## 4. Live operational validation (real internet, bounded)

Regenerated at `abcd303` (`docs/quality/evidence/webcortex_live.json`, 81 requests; `git_dirty` reflects uncommitted documentation only):

| Scenario | Outcome |
|---|---|
| A — latest open-source AI inference developments this week | completed, quick_lookup, 5 sources, live, freshness satisfied, 15 requests, 8.2 s |
| B — dendritic computation vs NeuroSciFi's learning architecture | completed, scientific, 10 sources, live, 22 requests, 5.8 s, 2 architecture proposals (not applied) |
| D — monitor "predictive coding" | two ticks ran, 0 meaningful changes, state preserved across reopen (at `4eeaed1` the same run reported 1 false change: FR-03, fixed in `abcd303`) |
| E — conflicting sources (neuron count) | completed, fact_verification, 8 sources, 4.8 s, verdict "insufficient" (0.5), freshness requirement reported unmet |
| C — dependency releases and advisories (`docs/quality/evidence/webcortex_scenario_c.json`) | 49 packages, 1 security advisory (zod GHSA-mrhx-c7xc-m3w5, accepted risk), 9 newer releases, 0 unknown; nothing installed |
| F — multi-device continuity | in-process devices and fixture web: `tests/test_pocket_research.py::test_scenario_f_phone_queues_spark_runs_tablet_reads` pass; real browser family isolation re-verified by retesters |

Search engines: with the honest User-Agent (ADR-032) the key-free engines returned no results during these runs;
discovery used Wikipedia, arXiv, OpenAlex, Crossref, PubMed, PyPI, npm, OSV and GitHub.

## 5. Offline benchmark (deterministic, `docs/quality/evidence/webcortex_bench.json`)

Three identical runs at `abcd303` (metrics unchanged from `8054a7f` and `4eeaed1`). The corpus grew after the baseline (UAT defect classes were added: 70 pages of which 53
articles, 38 tasks, vs 51 pages and 28 tasks), so the baseline column is the first measurement on the smaller corpus, not a same-corpus
comparison.

| Metric | Baseline (849f7bf) | Final (8054a7f) |
|---|---|---|
| Retrieval success | 0.90 | 1.00 (12/12) |
| Evidence coverage | 0.76 | 0.875 (21/24) |
| Top answer correct | 0.50 | 0.67 (8/12) |
| Top answer on-subject | — | 14/14 |
| Citation precision per cited source (every cited source contains the quote) | not measured | **1.00** |
| Citation (legacy, any cited source) | 1.00 | 1.00 |
| Verification accuracy | 0.45 | 0.75 (12/16); supported recall 0.4, disputed recall 1.0 |
| Fact-check true/false discrimination | — | 0/2 (false claims disputed; true claims "insufficient", OPEN-01) |
| Contradiction detector F1 / end-to-end F1 | 0.92 / 0.60 | 1.00 / 0.92 |
| Cross-subject merged answer items | (23 at an intermediate stage) | 0 |
| Latest-version correctness | — | 2/2 |
| Freshness compliance | 1.00 | 1.00 |
| Date extraction | 1.00 (35 pages) | 1.00 (53 pages) |
| Memory recall@3 / MRR (simulated owner acceptance) | 1.0 / 1.0 | 1.0 / 1.0 |
| Safety invariants (SSRF 11/11, robots, limits, injection 6/6, budgets, honest live flag, cancel, crash/resume) | all hold | all hold |
| Extraction F1 scrapling / trafilatura / auto | 0.87 / 0.90 / 0.86 | 0.85 / 0.89 / 0.84 |
| Offline P50 latency per task (loopback) | 0.024 s | 0.022–0.048 s by mode |
| Peak RSS | ~141–157 MB | 155 MB |

## 6. What this regression does not cover

See `PRODUCTION_READINESS.md` §4. In short: real handsets and sensors, push notifications, systemd services started
under hardening, a public TLS chain, screen readers, concurrent heavy load, and long-running autonomous operation.
