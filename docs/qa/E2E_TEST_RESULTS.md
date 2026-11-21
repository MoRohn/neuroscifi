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


---

# 0.7.0

Executed test cases for the 0.7.0 release candidates. **RC1 = `7c64b64`** (discovery, §2), **RC2 = `281e18b`**
(independent retests R1 to R3, §7), **RC3 = `0cd8d8b`** (fixes for what R1 and R2 found, §8), `HEAD` = `4c3f209` (final tree: RC3, two test-only commits, 9 fixes from fix lanes fixr4 and fixr5 to `090cb7d`, and 11 commits from the security review of the evidence guard). R4
retested the RC3 fixes live (§8.1); R5 retested the fixr4 fixes (§8.2). The final gate on `HEAD` passed (§8). The guard
security review is in §8.3. Status words:
PASS, FAIL, BLOCKED, NOT TESTED, UNVERIFIED. Where a tester wrote PARTIAL, this section records FAIL or PASS with the
defects named.

## 1. Environment and method

### 1.1 Host and services

| Item | Value |
|---|---|
| Host | NVIDIA DGX Spark (GB10, Blackwell sm_121), aarch64, 20 cores, about 121 GB unified memory (124.9 GB total in T2's `free`), driver 615.78, CUDA 13.4 |
| Docker | NVIDIA CDI (`--device nvidia.com/gpu=all`); no `nvidia` runtime registered; no podman |
| Browser | Playwright driving system `/usr/bin/chromium` (Playwright's bundled Chromium crashes on the Pocket page on this host) |
| Reasoning model | `qwen3-30b-a3b-instruct-2507-fp8` (Qwen3-30B-A3B-Instruct-2507-FP8) on vLLM at `127.0.0.1:8811/v1`; `nsf llm status --probe` `ready`, reply "ready" in 121 ms (T1) |
| Search | SearXNG at `127.0.0.1:8888` (Docker, loopback, `honest_user_agent: true`); open-webSearch daemon; structured APIs direct |
| Speech recognition | Whisper in the `neuroscifi-voice-asr` container, port 18431 |
| Speech synthesis | Kokoro (`am_michael`) in the voice TTS container, port 18432 |
| Console | `nsf console --port 0` or a fixed port (T1: 8796), `--url-file`, `--web-worker` |
| Pocket bridge | `nsf pocket serve --bind 127.0.0.1 --port 8443`, self-signed certificate (CN=localhost), `--instance-db owner.sqlite --invitation-file invite.txt` (T2) |
| Not used | Ports 8000 and 9000 (an unrelated container uses them) |
| Instances | Every tester used its own scratch instances (`nsf init`): T1 `t1.sqlite` and `t1b.sqlite`; T2 `owner.sqlite`; T3 a copy of real 0.6.0 data and a fresh virtual environment; T4 seeded instances; T5 its own. **The owner's instance was never touched** (stated by T1) |
| Shared host effects | The testers shared one host and one set of services. T1 restarted SearXNG (about 09:49 in T2's log) and vLLM (09:51); two T2 benchmark runs were discarded (`circuit_open`) and rerun. T2's console died about 09:30 without a traceback when T1 started its console and was restarted. `neuroscifi-voice-asr` had disappeared by 14:53 UTC in T1's log; T1 never stopped it. The repository perf test rewrote `docs/presence/perf/measurements.json` during the run (T3-05). None of these is counted as a product defect except as listed in the defect log |

### 1.2 Fake-device audio method (T2)

No real audio device was used. Every voice and Presence result is **fake-device**.

- Chromium flags: `--use-fake-ui-for-media-stream --use-fake-device-for-media-stream
  --use-file-for-fake-audio-capture=<wav> --autoplay-policy=no-user-gesture-required --ignore-certificate-errors`.
- Input WAVs were made by `bin/mkwav.py` from Kokoro speech (`am_michael`, 24 kHz resampled to 48 kHz mono 16-bit), with
  silence gaps, 180 Hz tone bursts and a long trailing silence.
- Every spoken reply WAV was transcribed back with the running Whisper to check what was said.
- Instrumentation: a `getUserMedia` wrapper counting live tracks; `nsf:voice` and presence intent logs; upload request
  counts; a server-side long-poll logger (`ev/presence_server.jsonl`).
- Kokoro cannot say some product words (T2-01), so S7 was run twice: once with "NeuroSciFi" and "agentic", once with
  sayable wording.

### 1.3 Other methods

- T1 ran a loopback logging proxy (`127.0.0.1:8889` to 8888) so every query that left NeuroSciFi was recorded (130
  lines). Private terms were set with `nsf web config --set 'private_terms=[...]'`. About 15 Playwright sessions logged
  0 page errors and 0 JavaScript errors.
- T1 checked cited quotes by re-fetching the cited pages (two requests 2 s apart, honest User-Agent), and checked
  version and release facts against PyPI and OSV directly.
- T3 built the wheel from `git archive`, installed it in a fresh virtual environment with `[web]`, and migrated a copy of
  real 0.6.0 data.
- T4 used axe-core and Chromium at 6 widths and 2 themes (`sweep_console.json`, `sweep_pocket.json`).
- T5 used `kill -9`, hostile model and SearXNG stand-ins that drip bytes or return odd shapes, and request-level probes.
- Testers did not edit the repository; fixes came from separate lanes.

Report and evidence locations: UAT evidence (not in repo), relative to the session's `v07/` folder: `uat/t1/` to
`uat/t5/` (each `REPORT.md` plus raw files; T5 evidence in `a123/`, `a45/`, `a6/`; T2 evidence in `ev/`; T4 in
`shots/`), `uat/reviews/REVIEWS.md`, probe scripts in `sec/`, and the RC2 retests in `retest/r1/`, `retest/r2/`,
`retest/r3/`. The reports of T2 to T5 and R1 to R3 were saved by the integrator from each tester's final message.

## 2. Executed cases by tester (RC1)

### 2.1 T1: research quality, knowledge, autonomy

| Case | Result | Evidence (`t1/`) |
|---|---|---|
| S1 default mode (CLI x1, console x2) | FAIL (no answer, 3 of 3) | `s1_cli.json` |
| S1 deep mode (CLI 36 s, console 21 s) | FAIL on dates (T1-08) and guard (T1-09); 15 of 15 quotes present | `s1b_cli.json`, `s1_ui.json` |
| S2 (CLI 29 s, console 41 s) | FAIL: off-topic sources, guard errors; 6 of 6 Crossref quotes present | `s2_cli.json`, `s2_ui.json` |
| S3 five numpy questions plus console | PASS for released, secure, stable, recommended; FAIL for the follow-up | `s3_*.json` |
| S4 (CLI, console, plain question) | FAIL | `s4a.json`, `s4c.json`, `s4_ui.json` |
| S5 (3 research runs, owner assertions, changes, `--phrase`, console, Knowledge view) | CLI PASS; console FAIL | `s5_*.json` |
| S6 autonomy (14 steps, two instances) | Mechanism PASS; prioritisation FAIL | `s6_*.json` |
| S11 research part (SearXNG stop and start, vLLM stop and start, CLI and console) | PASS with defects (T1-05, T1-11) | `s11_*.json` |
| Privacy (CLI and console) | PASS | `priv1.json`, `priv1.err`, `shots/privacy_ui.png` |

Not tested by T1: two concurrent autonomous tasks (one worker ran tasks in sequence), the $1 spend cap (prices are 0),
`nsf web tick` curiosity (it reported that autonomous research is limited to knowledge and owner monitors), Pocket
research and nodes, voice, days-long operation, the remote inference endpoint.

Timings (wall): S1 quick 5.5 s; S1 deep 36 s CLI and 21 s console; S2 29 s and 41 s; S3 23 to 31 s; S4 16 s; S5 15 to 24
s; autonomous tasks 6.6 to 22.2 s each (10 tasks, 174.6 s active); vLLM warm restart 36 s; SearXNG restart 2 s. Model
answer calls about 4 to 10 s, first token about 320 ms, 42 to 48 tokens/s.

### 2.2 T2: voice and Presence (fake-device)

| Case | Result | Evidence (`t2/ev/`) |
|---|---|---|
| S7 voice research (2 spoken runs) | FAIL (T2-01, T2-03) | `s7_run1.json`, `s7b.json` |
| S8 barge-in | PASS (273 ms stop) | `s8.json` |
| S9 mode switching (3 cycles) | PASS | `s9.json` |
| S10 multi-device (phone, tablet, stranger family) | FAIL (T2-04, T2-06) | `s10*.json` |
| S11 voice part (`deploy/voice/run.sh stop all`, 21.8 s) | FAIL (T2-08); text-only and recovery PASS | `s11*.json` |
| Presence A to L | A, C to E, G to L PASS; B PASS (note); F FAIL (T2-05); honesty FAIL (T2-02, T2-05) | `presence2.json`, `presence3.json` |
| Co-residency benchmark (2 runs) | measured | `bench_run3.json`, `bench_run4.json` |
| Security probes | PASS except T2-04 | `pocket_limits.json` |
| Robustness (repeat session starts) | FAIL (T2-07) | repro in `t2/REPORT.md` |

Timings: end of utterance to final transcript 288 / 753 ms (two runs); to acknowledgement audio 401 / 957 ms; to answer
audio 9.4 / 17.1 s unloaded. Under benchmark load (deep research started 3 s earlier): final transcript 325 / 951 ms,
acknowledgement audio 425 / 1113 ms, answer audio 23.7 / 45.5 s. Hub averages: ASR 194 ms, reply 119 ms, first sentence
254 ms. Memory: 80.9 to 81.8 GB used, minimum available 43.0 of 124.9 GB. Container and GPU use during the benchmark:
vLLM about 7.4 GiB cgroup (EngineCore 44.1 GiB on the GPU), ASR 3.06 GiB (4.16 GiB GPU), TTS 2.02 GiB (0.97 to 1.2 GiB
GPU), SearXNG 145 MiB.

### 2.3 T3: regression, build, migration

| # | Case | Result |
|---|---|---|
| 1 | `scripts/check.sh` | PASS: ruff clean, 1911 passed, 6 skipped, 4 node scripts OK, reproduce 8 of 8 |
| 2 | e2e, 3 runs | Runs 1 and 3: 144 passed, 1 skipped. Run 2: 1 failed (`test_voice_wer_and_mic_off_stops_capture`, T3-03; it passed 2 of 2 alone). Console owner journey never failed |
| 3 | `nsf acceptance --all`; reproduce | PASS: 7 of 7 simulated; 8 of 8 bit-exact |
| 4 | Offline bench, `--repeat 3` | PASS, deterministic: 29 of 29 verdicts, citation 206 of 207, contradiction precision and recall 1.0 and 0.857, safety holds. The committed `webcortex_bench_v070.json` was stale (28 of 29, T3-04) |
| 5 | Wheel from `git archive`, fresh virtual environment with `[web]` | PASS for version 0.7.0, README journey and `--help` (174 commands); FAIL for voice web assets missing from the wheel (T3-01) |
| 6 | Migration 0.6.0 to 0.7.0 on real data | PASS: schema 7 to 11; table counts and ids unchanged; forgets effective; restoring a pre-forget checkpoint kept 4 forgotten evidence rows removed; 0.6.0 refuses schema 11 |
| 7 | 0.6.0 critical journeys | PASS: CLI teach, change, resolve; governance; graphs; checkpoints; knowledge; console via e2e and a walk of the migrated database; Pocket pairing 403 / 201 / 403 / 200 / 201 / 200 / 403 |

Other T3 findings: T3-02 (no `nsf --version`), T3-05 (e2e rewrites a tracked file; README badge said 1557 tests).

### 2.4 T4: UX, accessibility, responsive

| Case | Result |
|---|---|
| axe: console 6 views and states x 6 widths x 2 themes; Pocket 6 x 6 x 2; empty instance; dialogs | PASS: 0 violations of any impact |
| Console and request errors | PASS: none unexpected |
| Console reflow at all widths | PASS |
| Console keyboard (no traps, visible focus) | PASS |
| Screen-reader names, roles, live regions (semantics, not a real screen reader) | PASS |
| Reduced motion | PASS |
| Touch targets | PASS with T4-07 |
| Empty, loading, long-operation feedback | PASS except T4-08 |
| Pocket reflow | FAIL (T4-01 at 320 px) |
| Forced colours | FAIL (T4-06) |
| Copy honesty | FAIL (T4-02, T4-03) |
| Pocket hand-off and continue | NOT TESTED beyond partial: the browser was not enrolled as a node; the enrolled path is covered by the repository e2e suite |

### 2.5 T5: adversarial, reliability, backend

| Area | Result | Counts |
|---|---|---|
| 1 Routes: auth, methods, malformed input | FAIL (minor) | 138 token, Origin and Host probes returned 403; methods 405 or 501; oversize 413; 500s in T5-04, T5-05, T5-06 |
| 2 Concurrency | FAIL (T5-01, T5-02) | Search budget never overspent; autonomy cap 2 held; 60 node take and hand-off races never produced two owners; Pocket family limit held (1 of 1,933); duplicates de-duplicated |
| 3 `kill -9` recovery | PASS with T5-03, T5-07, T5-08 | Integrity ok; 1,378 acknowledged writes, 0 lost; resumed tasks not duplicated or double charged |
| 4 Model failures | FAIL (T5-M01, T5-M02) | Guard, permissions and degraded label held |
| 5 SearXNG failures | FAIL (T5-M05, T5-M06) | |
| 6 CLI, migration, lifecycle | FAIL (T5-C01 to C05) | Migration 7 to 11, kill during migration and tombstones after restore PASS |
| Design | Not rated | A non-command sentence became a web research query (385 tasks queued in about 5 s) |

### 2.6 Reviews

| Review | Result |
|---|---|
| Security (opus security-reviewer) | Risk MEDIUM: 0 critical, 1 high, 2 medium, 5 low. Clean: SearXNG loopback, secrets and User-Agent patch; tool allow-list; the model cannot raise verification states; Presence Tier C numeric only; atomic node hand-off; console CSP, Origin and token |
| Architecture (opus architect) | One integrated architecture confirmed; migrations 8 to 11 sound; 9 gaps (8 fixed on `HEAD`, 1 open owner decision). Criteria verdicts at RC1: met 1, 3, 5, 7, 9, 11, 12; partial 2, 4, 6, 8, 10, 13, 15; not met 14 |

## 3. Counts

| Measure | Value | Source |
|---|---|---|
| Defects filed on RC1 | T1 20, T2 11, T3 5, T4 8, T5 20 (incl. 1 unrated design item), SEC 8, ARCH 9 | `DEFECT_RESOLUTION_LOG.md` |
| Unit and integration gate on RC1 | 1911 passed, 6 skipped | T3 item 1 |
| e2e on RC1 | 144 passed and 1 skipped (2 of 3 runs); 1 failure in run 2 | T3 item 2 |
| Defects filed on RC2 | R1 5, R2 10, R3 0 | `DEFECT_RESOLUTION_LOG.md` |
| Unit and integration gate on RC2 `281e18b` | 2154 passed, 1 failed (G-01, a wall-time bound under load), 6 skipped | integrator, §7 |
| e2e on RC2 | 164 passed, 1 skipped, in each of 2 runs | integrator, §7 |
| Unit and integration gate on RC3 `0cd8d8b` | 2207 passed, 6 skipped, 0 failed | integrator, §8 |
| e2e on RC3 | 166 passed, 1 failed (R2-01-flake), 1 skipped | integrator, §8 |
| Gate and e2e on `HEAD` `4c3f209` | gate 2588 passed, 6 skipped (the `NSF_LIVE=1` live-internet tests), 0 failed, 35 subtests (345 s); e2e 167 passed, 1 skipped, 0 failed (790 s) | integrator, §8 |

## 4. Not executed or blocked

| Item | Status | Reason |
|---|---|---|
| Real microphone and speaker; real phone or tablet; mobile browsers | BLOCKED | No devices. All voice and Presence audio (RC1 and RC2) is fake-device |
| PersonaPlex real-time duplex speech | BLOCKED | Not real-time upstream on this host (`DEFECT_RESOLUTION_LOG.md` O-02) |
| $1 spend cap, tight caps with several workers, days-long autonomy | NOT TESTED | Zero prices; R3 ran 3 workers without reaching a tight cap; no soak |
| T5-05 Pocket voice-on path, T5-M06 breaker state, T5-07 multi-worker search charging | UNVERIFIED (R3) | Pocket bridge had voice off; breaker state not read; the fake SearXNG expired mid-run |
| Remote inference endpoint | NOT TESTED | None configured |
| Real screen reader, 200 % zoom | NOT TESTED | Unchanged from 0.6.0 (forced colours was exercised by T4) |
| Notifications while Pocket is closed; public TLS chain; systemd under namespace hardening | BLOCKED / NOT TESTED | Unchanged from 0.6.0 |
| Pocket hand-off and continue as an enrolled node | NOT TESTED by T4 | Covered by the repository e2e suite only |

## 5. Environment observations that are not product defects

The shared-host events in §1.1 (a console that died when another tester started theirs, services restarted by another
lane, a removed ASR container, a tracked file rewritten by the repository's own perf test) affected timing and forced
reruns. T3-03 shows that the last one can also cause a flaky e2e failure; it is filed and fixed (`d315fb7`).

## 6. Fix waves

- RC1 to RC2: 57 commits, `7c64b64..281e18b`, listed with subjects in `DEFECT_RESOLUTION_LOG.md` (0.7.0). Each defect row
  names its regression test, or states that it has none. The developer evidence commit `676120e` (live console re-run
  of S1, S3 follow-up, S5) is not an independent retest.
- RC2 to RC3: `8044251` (G-01) and 19 commits for the R1 and R2 findings, `8044251..0cd8d8b`.
- After RC3: `373104e` (R2-01-flake) and `a9228b7`, both test-only; fixr4 (`c7ab1e1`, `4aae409`, `55b8aed`, `f41879e`,
  `1271b3e`, `1e8bf3b`) after R4; fixr5 (`f510512`, `224a843`, `090cb7d`) after R5.

## 7. RC2 retest (`281e18b`)

Three independent retesters; none wrote the fixes. Reports: UAT evidence (not in repo) `retest/r1/REPORT.md`,
`retest/r2/REPORT.md`, `retest/r3/REPORT.md`. Per-defect outcomes are in `DEFECT_RESOLUTION_LOG.md`.

### 7.1 R1: T1 live (research, knowledge, autonomy)

Real SearXNG, vLLM (Qwen3-30B-A3B-Instruct-2507-FP8) and the live internet; scratch instance `r1.sqlite`. Quotes were
re-fetched (CNN, Us Weekly, Snyk, numpy 2.5.5 notes, PMC) and found with matching dates. 0 private terms in 115 SearXNG
queries.

| Case | Result | Evidence (`retest/r1/`) |
|---|---|---|
| S1 default and deep, CLI and console | PASS: cited model answers | `s1_quick.json`, `s1_deep.json`, `s1_ui*.json` |
| S2 | FAIL (tester: PARTIAL): subject kept; off-topic sources still cited (T1-07) | `s2_cli.json`, `s2_ui.json` |
| S3 | PASS except T1-09; the follow-up without an earlier question asks which version is meant | `s3_*.json`, `s3_that.err` |
| S4 | FAIL (tester: PARTIAL): `conflicting`, no recency leaning; false sides remain (T1-01, T1-16); console "how many" answer off-topic (T1-07); R1-05 | `s4a.json`, `s4c.json`, `s4_ui.json` (CLI 20.7 s and 40.0 s) |
| S5 | PASS: v1 (1998) superseded by v2 (1999) | `s5_*.json` |
| S6 | FAIL (tester: PARTIAL): opt-in, owner first, 20 of 20 searches, 60 of 60 pages, depth <= 1 hold; R1-01, R1-02 | `s6_*.json` |
| T1-02 to T1-06, T1-10 to T1-12, T1-14, T1-15, T1-17, T1-20 | PASS | per scenario |
| T1-08, T1-13, T1-19 | PASS (direct call on the RC2 code) | |
| T1-01, T1-07, T1-09, T1-16, T1-18 | FAIL in part (residuals described in the defect log) | |
| Python and Rust "first released" | FAIL: unanswered (T1-07, R1-04); 8.4 s and 6.6 s | `py_first.json`, `rust_first.json` |

### 7.2 R2: T2, T4 and SEC (voice, devices, UX, security; fake-device audio)

| Case | Result | Evidence (`retest/r2/`) |
|---|---|---|
| S7 voice research | PASS: wake word and instruction stripped; on-topic; spoken equals text; final transcript 231 ms, acknowledgement 355 ms, answer 18.5 s | `ev/s7.json`, `ev/s7.out` |
| S8 barge-in | PASS: stop 365 ms; 150 ms overlap ignored; "Go on" resumed | `ev/s8.json` |
| S9 mode switching | PASS: 3 cycles; 0 tracks in text-only; 409 forced uploads; contiguous sequence | `ev/s9.json` |
| S10 multi-device | PASS except R2-01, R2-02 | `ev/s10r2*`, `ev/tablet_race_*.out` |
| S11 voice services down | PASS: scratch voice configuration pointed at closed ports 18497 and 18498; microphone off 7.6 s after the outage, 0 uploads, user told, status accurate, recovery | `ev/s11r2*`, `s11/` |
| Presence A to L | A to E, G, H, J to L PASS; F PASS from the S7 and S8 traces; I PASS on the safe fallback (both Tier C concepts invalid) | `ev/presr2*.json`, `ev/presence_server.jsonl` |
| T2-01, T2-02 (console voice), T2-04 to T2-11 | PASS; T2-06 on a same-family tablet; T2-07 including idle expiry and the startup sweep | `ev/t201.json`, `ev/t207*.out` |
| T2-03 | PASS on the exact repro; a variant fails (R2-03) | `ev/t203.json` |
| T4-01, T4-03 to T4-08 | PASS | `t4/` |
| T4-02 | FAIL: Voice panel "single-source statements are marked" with no marks; Presence "reasoning over the verified evidence" | `t4/shots/` |
| axe | 0 violations in 203 runs, console and Pocket, light and dark (dark confirmed: `data-theme=dark`, `nsf.theme=dark`, background rgb(7,9,8)) | `t4/sweep_r2_console_pocket.json` |
| SEC-H1, SEC-M2, SEC-M3 | PASS: forged, bogus and self hand-offs 409; holder-only grant; microphone moved in 2.8 s with 0 uploads after; owner sessions never evicted | `ev/sech1.json`, `sec/` |

### 7.3 R3: T3 and T5 (reliability, build, migration, crash safety)

| Case | Result | Evidence (`retest/r3/`) |
|---|---|---|
| T5-01 | PASS: 20 concurrent voice starts in 1.1 s, all 200 | `a123/` |
| T5-02 | PASS: 19 queued tasks, 3 workers, none cancelled for budget (tight cap not exercised) | `a123/` |
| T5-03, T5-04, T5-06, T5-08 | PASS | `a123/` |
| T5-05 | console PASS (segment >= 2^63 gives 400); Pocket half UNVERIFIED (bridge had voice off: `{voice:false}` / 404, no 500) | `a123/` |
| T5-07 | UNVERIFIED: the fake SearXNG expired mid multi-worker run; single worker 1 sent = 1 charged | `a123/` |
| T5-M01, M02, M04 | PASS: every stream and shape mode exits 1 without a traceback; drip_once and stall end at 10.1 s against 10 s | `a45/` |
| T5-M03 | PASS: `--no-tools` honoured | `a6/` |
| T5-M05, M06 | PASS: 16 SearXNG modes without a traceback; slow and drip about 12 s; `health --probe` 2 to 4 s; breaker state UNVERIFIED | `a45/` |
| T5-C01 to C05 | PASS: 138 CLI cases, 0 tracebacks; overflow rejected; forgetting a voice session forgets its research task; pre-revoke checkpoint restore keeps the node revoked | `a6/` |
| T5-D1 | PASS: 300 sentences in 1.5 s created 8 voice tasks | `a123/` |
| T3-01 | PASS: installed console and Pocket serve `/voice` assets 200; missing asset 404 JSON | `inst/` |
| T3-02 | PASS: `nsf --version` prints 0.7.0 | `inst/` |
| T3-03 | PASS: 2 of 2 alone | |
| T3-05 | e2e part PASS (perf document written only with `NSF_UPDATE_PERF_DOCS=1`); README badge FAIL (1916 against 2207) | |
| T3-04 | FAIL: `webcortex_bench_v070.json` stale (8/12 top answers against 9/12 measured; 41 metrics differ; for any `PYTHONHASHSEED`) | `bench/` |
| Wheel | PASS: clean `git archive`, fresh virtual environment with `[web]`; 0.7.0; voice, presence, console and Pocket assets present | `dist/`, `vi/` |
| Migration 0.6.0 to RC2 | PASS: schema 7 to 11, integrity ok, ids unchanged, new tables empty, forgets stay, pre-forget checkpoint restore re-applies forgets | `mig/` |
| `kill -9` | PASS: console killed during tasks and voice, 5 rounds: integrity ok, 1,138 acknowledged tasks and 462 acknowledged segments, 0 lost, no duplicate ids, old voice sessions ended at the next start; killed during a checkpoint of a 420 MB database: integrity ok, `.nsf-partial` swept at the next start; worker killed 6 times over 8 tasks, all terminal | `a123/` |
| New defects | none | |

### 7.4 Integrator gate on RC2

`bash scripts/check.sh`: 2154 passed, 1 failed (G-01: `test_pathological_page_extraction_is_bounded`, a wall-time bound
under load; fixed in `8044251`), 6 skipped. e2e (`NSF_CHROMIUM=/usr/bin/chromium .venv/bin/python -m pytest -q
tests/e2e`): 164 passed, 1 skipped, in each of 2 runs.

## 8. RC3 and `HEAD`

| Run | Commit | Result |
|---|---|---|
| Gate (`bash scripts/check.sh`) | RC3 `0cd8d8b` | 2207 passed, 6 skipped, 0 failed; ruff clean |
| e2e, full run | RC3 `0cd8d8b` | 166 passed, 1 failed, 1 skipped. The failure was the new R2-01 test under load (R2-01-flake); it passed 3 of 3 alone and its file 3 times (42 of 42) |
| R2-01 test after `373104e` | `373104e` | 120 of 120 under CPU load (fix lane, not independent) |
| Offline bench, `--repeat 3` | `373104e` | 3 identical runs; see `REGRESSION_REPORT.md` (0.7.0) |
| Gate on `HEAD` (`ruff check` and `bash scripts/check.sh`) | `4c3f209` | ruff: all checks passed; ALL CHECKS PASSED: 2588 passed, 6 skipped (the `NSF_LIVE=1` live-internet tests), 0 failed, 35 subtests (345 s) |
| e2e on `HEAD` | `4c3f209` | 167 passed, 1 skipped, 0 failed (790 s); includes the R2-01 restart test. The same suite also passed 167 of 167 in full runs on `373104e`, `1e8bf3b`, `090cb7d` and `bca1ad9` |
| Offline bench, fix-lane comparison | `090cb7d` | identical to the regenerated `webcortex_bench_v070.json` on all 393 metric keys |
| Offline bench regeneration by the integrator, `--repeat 3` | `4c3f209` | 3 identical runs: retrieval 12/12, top answer 9/12, citation precision 193/194 (189/190 items fully supported), verdict accuracy 29/29, verification states 29/29, safety invariants hold; headline metrics unchanged from the `373104e` regeneration |
| Live retest of the RC3 fixes (R4) | `373104e` (RC3 product code) | 13 items PASS; R2-05, T1-07 / R1-04, T1-09 residual open; 4 new findings; see §8.1 |
| Live retest of the fixr4 fixes (R5) | `1e8bf3b` | R2-05, R4-N2, R4-N3, O-09 PASS; the guard fix for R4-N1 FAILED (D-R5-1 to 3); Rust still wrong; see §8.2 |

### 8.1 R4: live retest of the RC3 fixes

Independent retester R4 on `373104e` (RC3 product code), scratch instances, Console and Pocket bridge, fake-device audio;
the voice outage was simulated by pointing voice settings at closed ports 18497 and 18498. Report: UAT evidence (not in
repo) `retest/r4/REPORT.md`; evidence `retest/r4/ev/`.

| Item | Result | Evidence (`retest/r4/ev/`) |
|---|---|---|
| R1-01 | PASS: with the owner's question cap lowered from 10 to 3 and full, the autonomy finding became pending question #5; the decision records "notified" with that id; the console names question #5 | `r101_*`, `c_views.json` |
| R1-02, R2-04 | PASS: 15 asks, 4 Accept-as-knowledge questions, all specific and on-topic (the voice-path Python repro was not re-run) | `r102_pending_all.txt`, `r102_provisional.txt` |
| R2-03 | PASS | `makemake*.json` |
| T1-09 | FAIL in part: caveats repeating "2.5.5" rejected by the number check (R4-N1) | `np_secure.json`, `numpy_rec2.json` |
| R1-05 | the non-conflicting case PASS; conflicting case NOT TESTED | |
| T1-01, T1-16 | 86 bn against a 61-99 bn range not contested (PASS); the subgroup and "for decades" cases NOT TESTED live (not in the live sources); see R4-N2 | `neurons.json`, `neurons2.json` |
| T1-07, R1-04 | FAIL: Python PASS (February 1991); Rust answered about the video game in 2 of 2 runs; S2 cites on-topic [1, 2] but off-topic PubMed records are flagged cited | `python_first.json`, `rust_first*.json`, `s2_pc.json` |
| R1-03 | PASS: "Newest cited source: published 2026-11-15 (5 days ago)" for numpy; see R4-N3 | `c_research.json` |
| T1-18 | PASS: "Asserted by you on 2026-11-21 20:29:18 UTC"; 0 "Last verified" | `t118_*`, `c_views.json` |
| R2-02 | PASS: a Pocket voice task is foreground in the owner's Presence, never autonomous | `p_multi.json` |
| R2-08 | PASS: `network_degraded`, `engines_unavailable` ASR and TTS, "speech recognition and speech output unavailable; type instead" | `v_console.json` |
| T4-02 | PASS: no marked-label claim before the answer; label and a real mark after; Presence never "verified evidence" | `v_console.json` |
| R2-07 | PASS: history intent, template reply, web tasks unchanged (2) | `v_console.json` |
| R2-09, R2-10 (`be0477f`) | Refused hand-off told in about 0.2 s (PASS); cross-family eviction NOT TESTED live (needs ≥4 families), in-process run consistent | `p_multi.json`, `be0477f_inproc.out` |
| R2-01 | PASS: restart 0.76 s after pairing; recovered 0.53 s after the bridge came back; 0 reloads | `p_multi.json` |
| R2-05 | message truthful ("Nothing was stored or searched"), 0 tasks; the question is still POSTed (400), not refused client-side (fixed in `f41879e`; R5 PASS) | `p_r205_r206.json` |
| R2-06 | PASS: "basis: none yet: stated by one source only, not confirmed by an independent source" | `p_r205_r206.json` |
| New | R4-N1 (Medium), R4-N2 (Medium), R4-N3 (Low-Medium), O-09 User-Agent (Low), fixed by fix lane fixr4 and retested in R5 (§8.2) | `DEFECT_RESOLUTION_LOG.md` (0.7.0, R4) |
| Observation | A voice question about ethanol's boiling point answered "not provided": search missed the Ethanol article; the answer was honest (known limitation O-10) | `v_console.json` |

### 8.2 R5: live retest of the fixr4 fixes, and the fixr5 fix-lane runs

Independent retester R5 on `1e8bf3b`: model `qwen3-30b-a3b-instruct-2507-fp8`, SearXNG live, Pocket through system
Chromium. Report: UAT evidence (not in repo) `retest/r5/REPORT.md`; evidence `retest/r5/ev/`.

| Item | Result | Evidence (`retest/r5/ev/`) |
|---|---|---|
| R4-N1 guard (`c7ab1e1`, `1271b3e`) | FAIL: "secure" runs 1 and 2 kept the caveat; run 3 rejected "cannot be confirmed" (verified/confirmed and hedging rules) and fell back to the template; "recommended" 3 of 3 runs kept no caveat (number check, "cites no source", a compound caveat). Filed as D-R5-1, D-R5-2, D-R5-3 | `n1_rec_*.json`, `n1_secure_*.json` |
| R4-N2 (`55b8aed`) | PASS: 2 runs cite only brain and neuron records; Milky Way and climate-finance records read with `cited: false`. Run 2 kept "Another source states ..." citing an unrelated Crossref record (R5-O1, Low) | `n2_1.json`, `n2_2.json` |
| R4-N3 (`1e8bf3b`) | PASS: `newest_source_at` from the answer-cited source [1] (2026-01-07), freshness not satisfied, out-of-date warning shown. Low-ranked off-topic findings still `cited: true` (R5-O2, Low) | `n3_1.json` |
| R2-05 (`f41879e`) | PASS: e-mail and obfuscated e-mail give 0 `research_submit` POSTs and "Nothing was sent ..." with `aria-invalid`; a phone number is refused by the Spark; a clean question is queued | `p_r205.json` |
| O-09 User-Agent (`4aae409`) | PASS: httpbin echoes "NeuroSciFi-WebCortex/0.7 (research agent; honours robots.txt)" | `ua_full.txt` |
| Python first release (regression) | PASS: "first released Python in 1991 as Python 0.9.0", citing the Wikipedia article on the programming language. A 7-day freshness requirement was applied (D-R5-4) | `reg_python.json` |
| Rust first release (T1-07) | Not fixed by fixr4; open High | |

fixr5 (`f510512`, `224a843`, `090cb7d`) was checked by the fix lane only (evidence not in repo: `fixr5_live/`): the
labelled "NeuroSciFi note" appeared in 6 of 6 numpy secure and recommended runs; caveats were accepted in some runs;
D-R5-2's exact sentence is covered by replay and unit tests; "When was Python first released?" got the stable class with
no freshness requirement or warning, while "latest release" keeps 7 days. **No independent live retest of fixr5 was done.**
The guard was then reviewed and rebuilt (§8.3).

### 8.3 Independent adversarial security review of the evidence guard

Three rounds by an independent reviewer, running crafted sentences (cited and uncited) through `reasoning.guard` against
the numpy 2.5.5 bundle. Evidence (not in repo): `SECREVIEW_GUARD.md`, `SECREVIEW2_GUARD.md`; round 3 reported to the
integrator.

| Round | Code | Result |
|---|---|---|
| 1 | fixr5 (`f510512`, `224a843`) | 6 findings: SEC-G-F1 Critical, F2 and F3 High, F4 and F5 Medium, F6 Low |
| 2 | `bca1ad9` (after the F1 to F6 fixes) | 4 findings: SEC-G-N1 and N2 High, N3 Medium, N4 Low |
| 3 | final guard (`128c339`, `c317ea9`, `4c3f209`) | 0 of 73 original and 0 of 65 round-2 sentences pass; about 50 new grammar attacks found no bypass. Verdict ACCEPT-with-documented-limits. Open: SEC-G-E1 (no entailment check for supported-cited sentences, pre-existing, High for the release decision), SEC-G-R1 (Low); SEC-G-R2 (Low) accepted |

Fix-lane live run of the rebuilt guard: the rules note appeared in 2 of 2 runs, and sentences were kept or rejected as
designed. No independent live retest of the rebuilt guard was done.
