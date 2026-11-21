# End-to-end test results

Executed test cases, how they were run, and their outcomes. Status words: **PASS** executed and verified · **FAIL**
executed and defective · **BLOCKED** cannot be verified here · **NOT TESTED** · **UNVERIFIED** executed but the check
could not complete (e.g. a third-party 429 while re-fetching a cited page).

## 1. Environment and method

- Host: NVIDIA DGX Spark (aarch64), Arch-based distribution, Python 3.12.16, Node v26.13.0, system Chromium 153.
  Playwright's bundled Chromium crashes the renderer on the Pocket page on this host; all browser runs used
  `/usr/bin/chromium` (`NSF_CHROMIUM`).
- Every journey ran against **real processes**: `nsf console` (loopback, per-start token; `--web-worker` or a separate
  `nsf web work`), `nsf pocket serve` (HTTP and TLS with a self-signed certificate, with and without `--instance-db`),
  `nsf web work`, `nsf web mcp` driven by a JSON-RPC stdio client, the CLI, and the open-webSearch daemon.
- Research used the **live internet** with bounded budgets where stated; otherwise an offline fixture web
  (`FixtureWeb`) served through the real acquisition stack (same SSRF policy, robots, extraction) — always labelled.
- Each tester observed console errors, failed requests, HTTP ≥ 400 responses and database state (sqlite3) before and
  after actions. Evidence (scripts, JSONL logs, screenshots, reports) lives in the session scratchpad:
  `uat/{pocket,t1,t3,t4,t5}/` (RC1), `retest/{r1,r2,r4}/` (RC2 and final), `final/` (build, migration, acceptance).
- Rounds: RC1 `64da95b` discovery → RC2 `cce5134` retest → final `8054a7f` retest (R4) → final fixes `4eeaed1`, `abcd303`. Testers never edited the
  repository; fixes came from separate lanes and were re-verified by a different tester.

## 2. Discovery on RC1 (6 workstreams)

| Workstream | Executed | Result |
|---|---|---|
| Pocket core (real bridge, 3 viewports) | 25 journeys | 21 PASS, 4 FAIL (P-01 high double-tap deletion, P-02, P-03, P-04/P-06); 0 axe violations |
| Console journeys (real console + worker) | 15 journeys, 3 viewports | Teach, governance, graphs, checkpoints, sessions, resilience, double submissions, inert hostile input PASS; research/knowledge FAIL (C-01 high, C-02 high, C-03…C-16) |
| Live agentic research (CLI, worker, MCP, ~400 requests) | 35 cases | Gating, privacy, worker lifecycle, forgetting + restore, MCP, deps PASS; grounding/citation/contradiction/dating FAIL (W-01…W-05 high) |
| Security and reliability | 40+ cases | No critical/high; 14 medium/low (S-01 Pocket slowloris, S-02 sibling minting, S-03 regulation bypass, S-05 corrupt cache …); privacy screen: 18 obfuscated variants refused, 0 bytes reached a recording listener |
| UX, accessibility, responsive (5 viewports, 179 screenshots, ~135 axe scans) | all views and states | 0 serious/critical axe; 31 UX findings (3 high) |
| Backend/CLI/persistence | — | **NOT TESTED**: the tester stalled on a hung command; scope re-run by the integrator (§5) |

## 3. Retest on RC2 (3 independent retesters)

| Retester | Result |
|---|---|
| R1 browser journeys | Every Pocket defect PASS (P-01 at 3 viewports × 3 outcomes); console U-01 (phone), U-03…U-07, U-09…U-11, C-01, C-05, C-08, C-11…C-13, S-01 (40 IPs + drippers: legitimate requests ≤ 0.51 s), S-02, S-04, S-08, S-12, S-13 PASS; U-02 FAIL; U-13, U-14 PARTIAL; 8 new (RT-B-01…08). axe 0 violations everywhere |
| R2 live research (~335 requests) | Registry answers correct (numpy 2.5.5, pandas 3.1.1 checked against PyPI); W-02, W-03, W-05…W-09, W-11, W-12, W-17, W-18, W-22, W-23, C-04, S-05, S-07, S-14, MCP PASS; **RT-W-01 high regression** (Wikipedia articles produced no claims), **RT-W-02 high** (location containment), W-01/W-04/W-19/W-20/C-02/C-09/C-16/S-09 FAIL; W-13…W-16 NOT TESTED |
| R3 backend retest | **NOT TESTED**: no report received (stalled); scope re-run by the integrator (§5) |

## 4. Final retest on `8054a7f` (independent retester R4)

| ID | Case | Result |
|---|---|---|
| RT-W-01 | "What is the capital of Australia?" (live) | PASS — Canberra sentence leads; 5/5 quotes found in their cited source (live re-fetch) |
| RT-W-02 | "Is it true that the Eiffel Tower is in Paris?" / "…in Berlin?" (live) | PASS — Paris `insufficient` (not disputed; re-fetch UNVERIFIED, 429); Berlin `disputed`, contradiction listed, counts match |
| RT-B-06 | yes/no vs wh-questions | PASS (with RT4-03 found) |
| RT-W-03 / RT-W-04 / C-02 | latest numpy; `github:numpy/numpy` | PASS — PyPI 2.5.5 leads (confirmed against PyPI JSON); v2.5.5 leads; release notes not contested (RT4-04 found) |
| W-19 | crawl quotes.toscrape.com, 3 pages | PASS — 5 notable statements, all found on the live pages |
| RT-B-03 | authorised disclosure of a phone number | PASS — refused without the flag (listener: 0 requests); with `--allow-private-terms` the listener received the number and the event was recorded |
| RT-B-05, RT-B-04, RT-B-07, U-14 | tablet tables, focus after review, contested value buttons, plain questions | PASS (RT4-01, RT4-02 found) |
| RT-B-01, RT-B-02, RT-B-08, Pocket copies | Pocket guards and messages | PASS |
| axe-core | console research/knowledge/questions/teach and Pocket views at 390/1024 | PASS — 0 violations of any impact |
| Smoke | console lock/token, teach decide/resolve, Pocket pair + share | PASS; only expected 403s |

New in R4 (all fixed afterwards, see `DEFECT_RESOLUTION_LOG.md`): RT4-01 focus after answering a question (M),
RT4-02 stemmed key in Curiosity (L), RT4-03 plain release questions not routed to registries (L–M), RT4-04 truncated
GitHub release quotes (L), RT4-05 re-asked disagreements (L), RT4-06 console URL file left on SIGHUP (L).

## 5. Backend, build and lifecycle (integrator, `8054a7f`)

Scope of the two stalled backend testers, run by the integrator with every step under a timeout
(`final/bin/backend.sh`, log `final/backend.log`):

| Case | Result |
|---|---|
| Wheel built from a clean `git archive`; installed into a fresh venv with `[web]` | PASS (`neuroscifi-0.6.0`) |
| `--help` of 42 commands/subcommands | PASS (0 failures) |
| Package data (console, Pocket, benchmark) | PASS |
| README journey with the installed package | PASS |
| Migration 0.5.0 (schema 5) → 0.6.0 (schema 7), data intact; newer schema refused | PASS |
| `nsf acceptance --all` from the installed package | PASS — 7/7 (simulated), 124 s |
| Concurrent CLI writers, kill -9 during checkpoint/learn, every API route's error statuses, import-legacy, graphfile/archive round trips | Partly covered elsewhere: concurrency and kill -9 on console/Pocket/worker by the security workstream (T4 4.1–4.3 PASS, integrity_check ok, no lost acknowledged writes); API error statuses by T4/T1 and `tests/console`, `tests/test_mobile*`; archive/graphfile/legacy by `tests/test_compression.py`, `tests/test_files_cli.py`, `tests/instance/test_legacy.py`. **Not re-run independently as user journeys: NOT TESTED** |

## 6. Live operational scenarios

See `REGRESSION_REPORT.md` §4 (A, B, C, D, E at `abcd303`; F by automated end-to-end test and browser retests).

## 7. Blocked or not tested

| Item | Status | Reason |
|---|---|---|
| Real phones/tablets, mobile Safari/Chrome, install to home screen, OS share sheet | BLOCKED | No handset attached |
| Real microphone/camera capture, speech recognition/synthesis engines | BLOCKED | No devices; headless Chromium |
| Notifications while Pocket is closed | BLOCKED (by design) | No push server |
| systemd units started under namespace hardening; real deployment | NOT TESTED | Units verified offline (`systemd-analyze verify`, `security --offline`) only |
| Public TLS certificate chain, LAN/VPN topology | BLOCKED | Self-signed certificate only |
| Screen readers (NVDA, VoiceOver, TalkBack), forced colors, 200 % zoom | NOT TESTED | — |
| Key-free search engines with an honest User-Agent | executed, returned nothing | Third-party behaviour; ADR-032 |
| Long-running autonomous operation (days), concurrent heavy load | NOT TESTED | — |
