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

---

# 0.7.0

**Code under test:** RC3 `0cd8d8b`; the first acceptance pass `4c3f209` (RC3 plus two test-only commits, 9 fixes from
fix lanes fixr4 and fixr5, and the fixes from the security review of the evidence guard), pushed with CI fixes as
`94ccb09`; and the final tree `5f28e6b` (94 commits after the owner's decisions of 2026-11-22 to `594e244`, then the
final guard round `dd02f16` and its documentation `5f28e6b`; not pushed). Version 0.7.0
on `build/neuroscifi-v1`. Same host as above. The full 0.7.0 regression record, including build, migration and live
evidence, is `FINAL_REGRESSION_REPORT.md`; this section holds the 0.6.0 to 0.7.0 comparison.

## 1. Test-count history

| Point | Commit | Gate (`bash scripts/check.sh`) | e2e (`tests/e2e`, system Chromium) | Source |
|---|---|---|---|---|
| 0.6.0 final | `abcd303` | 1557 passed, 6 skipped | 107 passed | §1 above |
| Lane B (inference gateway) | `06c171c` | 1705 passed, 6 skipped | 107 passed | integrator log |
| Presence and voice UI | `e0bd7df` | 1854 passed, 6 skipped | 128 passed, 1 skipped | integrator log |
| RC1 | `7c64b64` | 1911 passed, 6 skipped | 144 passed, 1 skipped (T3 runs 1 and 3); run 2: 1 failed (T3-03, flaky) | T3 |
| README count commit | `cdb66ba` | 1916 passed | | commit message |
| RC2 | `281e18b` | 2154 passed, 1 failed (G-01), 6 skipped | 164 passed, 1 skipped, twice | integrator |
| RC3 | `0cd8d8b` | 2207 passed, 6 skipped | 166 passed, 1 failed (R2-01-flake), 1 skipped | integrator |
| `373104e` | `373104e` | 2207 passed, 6 skipped | 167 passed, 1 skipped | integrator |
| fixr4 (R5 ran here) | `1e8bf3b` | 2213 passed, 6 skipped | 167 passed, 1 skipped | integrator |
| fixr5 | `090cb7d` | 2259 passed, 6 skipped | 167 passed, 1 skipped | integrator |
| Guard review, round 1 fixes | `bca1ad9` | 2392 passed, 6 skipped | 167 passed, 1 skipped | integrator |
| First acceptance pass | `4c3f209` | 2588 passed, 6 skipped, 0 failed (ruff clean) | 167 passed, 1 skipped, 0 failed | integrator |
| Pushed | `94ccb09` | GitHub Actions CI green | GitHub Actions CI green | CI |
| Owner-decision work | `594e244` | 3267 passed | 184 passed, 1 skipped | integrator |
| Final tree | `5f28e6b` | 3303 passed, 18 skipped (live-service tests), 0 failed | 184 passed, 1 skipped (opt-in degraded-voice test), 0 failed | integrator |

Every gate above had 0 failures except RC2's G-01; every e2e run had 0 failures except RC3's R2-01-flake. From 0.6.0 to
the first acceptance pass the gate grew by 1031 tests (1557 to 2588) and the e2e suite from 107 to 167; to the final tree
by 1746 (to 3303) and to 184. The final tree's 18 skipped tests are opt-in live-service tests. The 6
skipped gate tests are the opt-in live-internet tests (`NSF_LIVE=1`), as in 0.6.0.

## 2. Offline benchmark: 0.6.0 against 0.7.0

Files: `docs/quality/evidence/webcortex_bench.json` (0.6.0, `abcd303`) and `docs/quality/evidence/webcortex_bench_v070.json`
(0.7.0, regenerated on the final tree `4c3f209` on 2026-11-21, `nsf web bench --repeat 3`, offline fictional corpus). Both files record 3
identical runs (`regression.identical: true`).

**The corpus grew**, so this is not a same-corpus comparison: 70 to 78 pages, 53 to 61 articles, 40 to 47 hosts,
16 to 29 verification questions, 79 to 105 tasks. The counts of authoritative questions (12), time-sensitive
questions (8), contradiction pairs (12), memory queries (11) and safety probes (18) are unchanged.

| Metric | 0.6.0 (`abcd303`) | 0.7.0 (`4c3f209`) |
|---|---|---|
| Retrieval success | 12/12 | 12/12 |
| Evidence coverage | 21/24 | 21/24 |
| Top answer correct | 8/12 | 9/12 |
| Correct answer among any quote | 9/12 | 10/12 |
| Top answer on-subject | 14/14 | 14/14 |
| Noise page supplies the top quote (rank-noise questions) | 1 of 3 | 0 of 3 |
| Citation precision per cited source (every cited source contains the quote) | 148/148 (1.00) | 193/194 (0.9948) |
| Answer items whose every citation is supported | 145/145 | 189/190 |
| Citation, legacy (any cited source contains the quote) | 145/145 | 190/190 |
| Verification accuracy, legacy four labels | 12/16 (0.75) | 29/29 (1.00) |
| `supported` recall | 2/5 (0.4) | 10/10 (1.0) |
| Verification states (six ADR-035 states) | not measured | 29/29 |
| Fact-check true/false discrimination | 0/2 | 2/2 |
| Contradiction detector precision / recall | 1.0 / 1.0 | 1.0 / 1.0 |
| Contradiction end-to-end precision / recall (F1) | 1.0 / 0.8571 (0.9231) | 1.0 / 0.8571 (0.9231) |
| Cross-subject merged answer items | 0 (18 findings checked) | 0 (19 findings checked) |
| Latest-version correctness | 2/2 | 2/2 |
| Freshness compliance | 8/8 | 8/8 |
| Date extraction | 53/53 | 61/61 |
| Memory recall@3 / MRR (simulated owner acceptance) | 1.0 / 1.0 | 1.0 / 1.0 |
| Knowledge rows written by consolidation | 2 | 8 |
| Extraction F1, auto / scrapling / trafilatura | 0.838 / 0.853 / 0.885 | 0.834 / 0.849 / 0.887 |
| Safety invariants (SSRF 11/11, robots, limits, injection 6/6 contained, budgets, honest live flag, cancel, crash and resume) | all hold | all hold |

Notes on the comparison:

- **Citation precision.** The one unsupported citation is task F22 ("The Marrow Creek ferry is operated by Ostra Lines
  ...", citation 2, `coastline-wire.example`). F22 is one of the verification questions added in 0.7.0; it does not
  exist in the 0.6.0 corpus. `docs/knowledge/CLAIM_VERIFICATION.md` §8 and §11 quote 206/207 from an earlier 0.7.0
  snapshot with the same F22 citation as the only miss. The final figure is 193/194 because later fixes stopped citing
  off-subject sources, so fewer citations are counted (194 instead of 207). CLAIM_VERIFICATION was not edited.
- **P39 gold relabel.** The 0.7.0 bench uses the relabelled P39 gold (an `og:updated_time` page has no publication date),
  documented with before and after metrics in `docs/knowledge/CLAIM_VERIFICATION.md` §11 and recorded as decision in
  `RELEASE_DECISIONS.md` §5.
- **Knowledge rows.** RC2 wrote 10 knowledge rows; RC3 writes 8, because two on-subject items are now held as
  off-question. The owner then decided to fix it (OD-4): 8 → 9 → 7, below (`RELEASE_DECISIONS.md` §5 DD-13).
- **Environment block.** `environment.packages.neuroscifi` reads `0.5.0` in both files. That is the installed
  distribution metadata in the virtual environment, not the source version (`pyproject.toml` says 0.7.0).
  `git_dirty: true` in the 0.7.0 file reflects uncommitted documentation.
- **Final tree.** The fix lane found all 393 metric keys on `090cb7d` identical to the `373104e` regeneration; the
  integrator's regeneration on `4c3f209` has the same headline metrics.

**After the owner's decisions (2026-11-22).** The bench's knowledge rows changed twice, both recorded in the commit
messages with nothing else moving: `26e16a0` restored the on-subject definition row (8 → 9, provisional 8 → 9); `d88758a`
keeps knowledge from set-aside sources out of consolidation (9 → 7: the Brisa and Tessaly rows came from sources the
answer had set aside as another entity; memory recall@3 and MRR stay 1.0). Bench on the final tree `5f28e6b`:
regenerated into `webcortex_bench_v070.json`, identical across 3 runs, every headline metric as in the table above except
knowledge rows 7 (retrieval 12/12, coverage 21/24, top answer 9/12, citation precision 193/194 with 189/190 items fully
supported, legacy citation support 190/190, freshness 8/8, verdict accuracy and verification states 29/29, contradiction
end-to-end precision/recall 1.0/0.8571, memory recall@3 and MRR 1.0/1.0, fact-check discrimination 2/2, latest version
2/2, top answer on-subject 14/14, 0 cross-subject merges, safety invariants hold).

## 3. Regressions found and fixed in the 0.7.0 cycle

"Regression" here means a behaviour that passed at 0.6.0 or at an earlier 0.7.0 candidate and failed later.

| ID | What regressed | Found by | Fix | Status |
|---|---|---|---|---|
| 0.6.0 critical journeys | None regressed: create, teach, change, resolve, explain, governance, forget, checkpoints, graphs, Pocket pairing, migration, crash safety, CLI | T3 (RC1), R3 (RC2) | none needed | PASS |
| G-01 | A gate test (`test_pathological_page_extraction_is_bounded`) failed under load on RC2; it had passed at RC1 | RC2 gate | `8044251` CPU time instead of wall time | PASS in the RC3 gate |
| R2-01-flake | The new R2-01 e2e test failed once in the RC3 full run (Pocket's service worker let `/api` requests bypass `page.route`) | RC3 e2e | `373104e` (test only) | Fixed (373104e); final e2e PASS |
| T3-03 | `test_voice_wer_and_mic_off_stops_capture` failed once in 3 RC1 runs (fixed 60 s wait under shared load) | T3 | `d315fb7` bounded wait | PASS (R3) |
| T3-04 | The committed 0.7.0 bench file did not match a fresh run (RC1: 28/29 against 29/29; RC2: 8/12 top answers against 9/12) | T3, R3 | regenerated on `373104e` and on the final tree `4c3f209` | Regenerated on `4c3f209` |
| T3-05 | The e2e suite rewrote a tracked document; the README badge lagged the gate count | T3, R3 | `d315fb7`; README count from the final gate | Perf part PASS (R3); README says 2588 (Fixed) |
| Knowledge rows | Offline bench knowledge rows 10 (RC2) to 8 (RC3) | integrator | `26e16a0` (8 → 9), then `d88758a` (9 → 7 by design) | Resolved (owner decision OD-4: fix) |
| Guard round 4 | "No-one has exploited …" lost its negation: `5bfbaac` read "no-one" as one non-negating word, so "Someone has exploited …" passed; `compose()` stripped "No-" | Review round 5 (C2) | `549179c` | Fixed; round 6 confirmed closed |
| Push after signing | Pocket refused the unsigned push status reply after `be2d234`, so push never turned on (5 of 6 push e2e tests failed) | Security review of signing and push (B-0) | `56b62da`, `e2a3378` | Fixed; push live with real FCM, R6 PASS |
| CI on GitHub runners | The pushed tree failed CI: missing Web Cortex extras, Chromium's sandbox on Ubuntu 24.04, the snap shim, the extraction bound on slower runners, chromium-headless-shell killing Pocket's renderer, an autonomy fixture crossing UTC midnight | GitHub Actions | `779f78b`, `13ee248`, `b9cc716`, `65e1602`, `2fca7da`, `94ccb09` | Fixed; CI green on `94ccb09` |
| Guard answer retention | Each stricter guard round cost faithful sentences: replay survival 97 % (round 5) → 94 % (6) → 79 % (7) → 72 % (8) → 90 % (9, negative name oracle) → 88 % (final round F1–F5) | `COGNITIVE_INFERENCE.md` §6 measurements | `9477b3f` recovered sentence-initial names | Measured; the guard fails closed by design |

Intentional behaviour changes from 0.6.0 (not regressions): `nsf llm ask --tools` is refused unless the owner switched tools on (`nsf llm config --tools`, default off); a remark that is
neither a question nor an explicit request no longer starts web research, and each principal starts at most 8 tasks
from conversation per 10 minutes (`0e8c7eb`); version answers distinguish published, stable, compatible, secure and
recommended (ADR-033 amended); an update or modification time is never a publication date (ADR-035, T1-05).
