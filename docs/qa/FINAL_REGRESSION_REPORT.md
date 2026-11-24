# Final regression report: 0.7.0

**Code under test:** final tree `5f28e6b` (local branch, not pushed): the first acceptance pass `4c3f209`, the CI fixes
pushed as `94ccb09`, 94 commits after the owner's decisions of 2026-11-22 (to `594e244`), and the final guard round
(`dd02f16`, `5f28e6b`). The first pass, `4c3f209`, version 0.7.0, branch `build/neuroscifi-v1`: RC3 `0cd8d8b`, two test-only
commits (`373104e`, `a9228b7`), the fixr4 fixes (`c7ab1e1`, `4aae409`, `55b8aed`, `f41879e`, `1271b3e`, `1e8bf3b`) and the
fixr5 fixes (`f510512`, `224a843`, `090cb7d`) and 11 commits from the security review of the evidence guard (`78e2576`
to `4c3f209`).
**Host:** NVIDIA DGX Spark (GB10, Blackwell sm_121), aarch64, 20 cores, about 121 GB unified memory, driver 615.78,
CUDA 13.4; Python 3.12.16; system Chromium 153 (`/usr/bin/chromium`). No microphone, camera, speaker or handset attached.
**Services used by live tests:** SearXNG (loopback 127.0.0.1:8888, pinned image), vLLM serving
Qwen3-30B-A3B-Instruct-2507-FP8 (loopback 127.0.0.1:8811), Whisper ASR (18431) and Kokoro TTS (18432) containers.

**Verdict: Release candidate, ready for owner acceptance with listed limitations. Not production-ready: owner-side verification remains (real phones and handsets including the Voice check, CA install on phones, push on a real phone, root-only systemd hardening checks, Brave live with the owner's key, a real external inference provider, screen readers with people), plus the open owner decisions OD-7 to OD-11.**

The first pass ended "not accepted" (open High T1-07 and SEC-G-E1). After the owner's decisions both are fixed: T1-07
passed the independent live retest R6; SEC-G-E1's final review is ACCEPT-with-documented-limits (round-8 condition met: F1–F5 in dd02f16; adv11 re-run by the integrator). The final run on `5f28e6b` is green
(§1). Owner-side items are in §7.

## Summary

| Area | Result | Detail |
|---|---|---|
| Gate on RC3 | 2207 passed, 6 skipped, 0 failed | §1 |
| e2e on RC3 | 166 passed, 1 failed (test flake, fixed in `373104e`), 1 skipped | §1 |
| Gate and e2e on `HEAD` `4c3f209` | gate 2588 passed, 6 skipped, 0 failed; e2e 167 passed, 1 skipped, 0 failed | §1 |
| 0.6.0 critical workflows (S12) | PASS on RC1 (T3) and RC2 (R3) | §2 |
| Build, clean install, migration 0.6.0 to 0.7.0, crash safety | PASS on RC2 (R3) | §2 |
| Independent retests on RC2 | R1, R2, R3 ran; 15 new defects (R1 5, R2 10, R3 0), each with a fix after RC2 | §3 |
| Live retest of the post-RC2 fixes (R4) | 13 items PASS; T1-07 Rust (rated High by R4), the T1-09 residual (R4-N1) and R2-05 open; 4 new findings | §3 |
| Live retest of the fixr4 fixes (R5) | R2-05, R4-N2, R4-N3, O-09 PASS; the R4-N1 guard fix FAILED (D-R5-1 to 3; fixed again by fixr5, then rebuilt as a closed caveat grammar after the guard security review); 2 new Low findings open (R5-O1, R5-O2) | §3 |
| SEC-G-E1 (High, pre-existing) | Fixed: entailment coverage plus review rounds 4 to 8; final review ACCEPT-with-documented-limits (round-8 condition met: F1–F5 in dd02f16; adv11 re-run by the integrator) | §6 |
| T1-07 (High) | Fixed: one sense per answer; R6 PASS (2 of 2 language runs; the game when named) | §6 |
| CI at the pushed `94ccb09` | GitHub Actions green after six CI fixes (extras, Chromium sandbox sysctl, snap shim, full Chromium in e2e, extraction bound, midnight fixture) | §1 |
| Independent live retest R6 (`a9f5e37`) | 9 PASS, 2 FAIL (Brave key-file check D1, guard sentences D5); 11 defects and 8 setup items fixed with tests, not retested live | §3 |
| Security review of signed nodes and push | 1 Medium, 6 Low, 1 functional blocker; all fixed | §3 |
| Gate, e2e and bench on the final tree `5f28e6b` | gate 3303 passed, 18 skipped, 0 failed; e2e 184 passed, 1 skipped, 0 failed; bench identical across 3 runs | §1 |
| Offline bench | 3 identical runs on a larger corpus than 0.6.0. Lower than 0.6.0: citation precision 193/194 (the miss is on a task new in 0.7.0) and extraction F1 (auto 0.838 to 0.834); every other quality measure equal or higher | §5 |
| Real audio devices and handsets | BLOCKED here; voice validated on recorded human speech; the owner's Voice check is built | §7 |

## 1. Automated suites

| Suite | Command | RC3 `0cd8d8b` | First pass `4c3f209` | Final tree `5f28e6b` |
|---|---|---|---|---|
| Lint | `ruff check .` (in `scripts/check.sh`) | All checks passed | All checks passed | All checks passed (in the gate) |
| Python unit and integration, JavaScript checks, reproduction | `bash scripts/check.sh` | 2207 passed, 6 skipped (the `NSF_LIVE=1` live-internet tests), 0 failed | ALL CHECKS PASSED: 2588 passed, 6 skipped, 0 failed, 35 subtests (345 s) | ALL CHECKS PASSED: 3303 passed, 18 skipped (live-service tests), 0 failed |
| Browser end-to-end | `NSF_CHROMIUM=/usr/bin/chromium .venv/bin/python -m pytest -q tests/e2e` | 166 passed, 1 failed, 1 skipped | 167 passed, 1 skipped, 0 failed (790 s) | 184 passed, 1 skipped (opt-in degraded-voice test), 0 failed |

On `4c3f209` the same test passed in the e2e run and in 4 earlier consecutive full runs (`373104e`, `1e8bf3b`,
`090cb7d`, `bca1ad9`: 167 passed, 0 failed each). The RC3 e2e failure was `tests/e2e/test_pocket_uat.py::test_restart_right_after_pairing_recovers_without_a_reload`, the
new test for R2-01. It passed 3 of 3 alone and its file 3 times (42 of 42). Root cause: once Pocket's service worker
controlled the page, Chromium sometimes sent `/api` requests past Playwright's `page.route`, so the simulated bridge
restart did not happen (3 of 150 requests unseen in the reproduction). `373104e` blocks service workers in that test;
the fixed test passed 120 of 120 under CPU load in the fix lane. Product code is not involved (`DEFECT_RESOLUTION_LOG.md`
R2-01-flake).

On `594e244` (before the final guard round) an integrated run was also green: gate 3267 passed, e2e 184 passed,
1 skipped. The final tree `5f28e6b` adds `dd02f16` (guard) and `5f28e6b` (documentation). CI (GitHub Actions) was green on the pushed `94ccb09`
after these fixes: `779f78b` Web Cortex extras in the gate and e2e jobs, `13ee248` Chromium's sandbox on Ubuntu 24.04
runners, `b9cc716` Playwright's Chromium rather than the runner's snap shim, `65e1602` the pathological-extraction bound
on slower runners, `2fca7da` full Chromium in e2e (chromium-headless-shell killed Pocket's renderer), `94ccb09` the
autonomy fixtures across a UTC midnight.

Test-count history from 0.6.0 (1557 gate, 107 e2e) to RC3 (2207, 167 executed) is in `REGRESSION_REPORT.md` (0.7.0 §1).

## 2. Build, install, migration and the 0.6.0 workflows (S12)

S12 reruns the 0.6.0 critical workflows on 0.7.0. T3 ran it on RC1; R3 repeated the build, migration, crash-safety and
CLI parts on RC2. Evidence: UAT evidence (not in repo) `uat/t3/REPORT.md`, `retest/r3/REPORT.md`.

| Check | RC1 (T3) | RC2 (R3) |
|---|---|---|
| Wheel from a clean `git archive`, fresh virtual environment with `[web]` | 0.7.0; README journey and `--help` of 174 commands PASS; voice web assets missing (T3-01) | 0.7.0; voice, presence, console and Pocket assets present; installed console and Pocket serve `/voice` assets 200 and a missing asset as 404 JSON; `nsf --version` prints 0.7.0 |
| Migration of a copy of real 0.6.0 data | schema 7 to 11; table counts and ids unchanged; forgets effective; restoring a pre-forget checkpoint keeps the forgotten rows removed; 0.6.0 refuses schema 11 | schema 7 to 11; integrity ok; ids unchanged; new tables empty; forgets stay; a pre-forget checkpoint restore re-applies the forgets |
| `kill -9` crash safety | integrity ok; 1,378 acknowledged writes, 0 lost (T5) | console killed during tasks and voice, 5 rounds: integrity ok, 1,138 acknowledged tasks and 462 acknowledged segments, 0 lost, no duplicate ids; killed during a checkpoint of a 420 MB database: integrity ok, `.nsf-partial` swept at the next start; worker killed 6 times over 8 tasks, all terminal |
| CLI | teach, change, resolve, governance, graphs, checkpoints, knowledge PASS; several failure paths printed tracebacks (T5-C01 to C05) | 138 CLI cases, 0 tracebacks; overflow rejected; forgetting a voice session forgets its research task; restoring a pre-revoke checkpoint keeps the node revoked |
| Pocket pairing | 403 / 201 / 403 / 200 / 201 / 200 / 403 | exercised in S10 by R2; not repeated as a sequence |
| `nsf acceptance --all` (simulated, ADR-014); `scripts/reproduce.py` | 7 of 7; 8 of 8 bit-exact | reproduce runs inside every gate; acceptance not repeated |

Verdict for S12: **PASS**. The README test count (T3-05) now says 2588, from the final gate; the committed bench file
was stale on RC2 (T3-04) and was regenerated on `373104e` and again on the final tree `4c3f209`.

## 3. Re-executed user journeys

| Round | Commit | Executed by | Outcome |
|---|---|---|---|
| Discovery | RC1 `7c64b64` | T1 research and autonomy (live), T2 voice and Presence (fake-device audio), T3 regression and build, T4 UX and accessibility, T5 adversarial and reliability | 64 defects (T1 20, T2 11, T3 5, T4 8, T5 20 including one design issue) |
| Reviews | RC1 | security review, architecture review | SEC: 0 critical, 1 high, 2 medium, 5 low; ARCH: 9 gaps |
| Fix wave | RC1 to RC2 | fix lanes (not the testers) | 57 commits |
| Retest | RC2 `281e18b` | R1 (T1 live), R2 (T2, T4, SEC), R3 (T3, T5) | 15 of 20 T1 items, all 11 T2 items, 7 of 8 T4 items, 3 SEC items, 17 of 20 T5 items and 3 of 5 T3 items PASS; residuals in T1-01, 07, 09, 16, 18, T4-02, T3-04, T3-05; UNVERIFIED parts in T5-05, T5-07, T5-M06; 15 new defects (R1-01 to 05, R2-01 to 10); R3 found none |
| Fix wave | RC2 to RC3 | fix lanes | `8044251` and 19 commits |
| Live retest of the RC3 fixes | `373104e` (RC3 product code) | R4 | PASS: R1-01 to R1-03, R2-01 to R2-04, R2-06 to R2-08, R2-10, T1-18, T4-02; tested in part: T1-01, T1-16, R1-05, R2-09; open: T1-07 / R1-04, T1-09 (R4-N1), R2-05, new R4-N1 to N3 and O-09 (handled by fixr4, below). Report: UAT evidence (not in repo) `retest/r4/REPORT.md`; detail `E2E_TEST_RESULTS.md` (0.7.0 §8.1) |
| Fix wave | after R4 | fix lane fixr4 | 6 commits (`c7ab1e1`, `4aae409`, `55b8aed`, `f41879e`, `1271b3e`, `1e8bf3b`) |
| Live retest of the fixr4 fixes | `1e8bf3b` | R5 | PASS: R2-05, R4-N2, R4-N3, O-09, Python first release; FAIL: the R4-N1 guard fix (D-R5-1 to 3); new D-R5-4 (Low) and findings R5-O1, R5-O2 (Low, open); Rust not fixed. Report: UAT evidence (not in repo) `retest/r5/REPORT.md`; detail `E2E_TEST_RESULTS.md` (0.7.0 §8.2) |
| Fix wave | after R5 | fix lane fixr5 | `f510512`, `224a843`, `090cb7d`; fix-lane live runs only (`fixr5_live/`), no independent live retest |
| Security review of the evidence guard | fixr5 code, then `bca1ad9`, then the final guard | independent adversarial reviewer, 3 rounds | Round 1: SEC-G-F1 (Critical) to F6; round 2: SEC-G-N1 to N4; all fixed (`78e2576` to `c317ea9`, `4c3f209`). Round 3: 0 of 73 original and 0 of 65 round-2 sentences pass; about 50 new grammar attacks found no bypass. Verdict ACCEPT-with-documented-limits. Open: SEC-G-E1 (High for the release decision, pre-existing: no entailment check for supported-cited sentences), SEC-G-R1 (Low); SEC-G-R2 (Low) accepted. Evidence (not in repo): `SECREVIEW_GUARD.md`, `SECREVIEW2_GUARD.md` |
| Owner's decisions | 2026-11-22 | owner | fix T1-07, SEC-G-E1, news rendering, bench rows; leave PersonaPlex; resolve the README limitations |
| Fix waves after the push | `94ccb09..5f28e6b` | lanes (not the reviewers or retesters) | 94 commits to `594e244` (grouped in `DEFECT_RESOLUTION_LOG.md`), then `dd02f16` and `5f28e6b` |
| Evidence-guard review, rounds 4 to 7 | `f898fc2` to `0de65b9`, then the name oracles `c79b627`, `9477b3f` | independent adversarial reviewer | Each round BLOCK; 17 findings (High 11), each fixed; earlier corpora re-run every round. Evidence (not in repo): `SECREVIEW4_ENTAIL.md` to `SECREVIEW7_GUARD.md` |
| Evidence-guard review, round 8 (final) | the tree with the name oracles | independent adversarial reviewer | "ACCEPT-with-documented-limits once F1–F3 land; no further review round needed; only a re-run of adv11 and the survival replay". F1–F5 landed in `dd02f16`; the integrator re-ran adv11 (4 accepted: 2 documented limits, 2 controls), adv4 (0 of 47), adv8 (0), adv9 (3: documented -s heads and a control), adv10 (2 controls), adv7 (7, faithful or documented); replay survival 88 %, 0 unfaithful. Verdict ACCEPT-with-documented-limits (round-8 condition met: F1–F5 in dd02f16; adv11 re-run by the integrator) |
| Security review of signed nodes and push | `c722315` | independent security reviewer | Risk MEDIUM; A-1 (Medium), A-2 to A-6 and B-1 (Low), B-0 (functional); all fixed (`9ae2ade` to `e2a3378`); no re-review recorded. Evidence (not in repo): `SECREVIEW_SIGNPUSH.md` |
| Independent live retest R6 | `a9f5e37` | R6 | Multi-device Pocket with a trusted TLS chain, node enrolment and signing, Web Push (real FCM), word sense, news, Voice check, forget and checkpoints, `nsf doctor` PASS; answers and guard (D5) and the Brave key UX (D1) FAIL; idle unloading PASS in part. Fixed by 21 polish commits, not retested live. Evidence (not in repo): `retest/r6/REPORT.md` |

Per-defect detail: `DEFECT_RESOLUTION_LOG.md` (0.7.0). Per-scenario detail: `USER_JOURNEY_MATRIX.md` (0.7.0) and
`E2E_TEST_RESULTS.md` (0.7.0 §7).

## 4. Live operational validation (real internet and real local models, bounded)

These files are developer evidence produced by the lanes that built each part; the independent live runs are R1 and R2
above.

| Evidence file | What it shows |
|---|---|
| `docs/quality/evidence/searxng_live.json` | Pinned SearXNG image, loopback bind, honest User-Agent patch verified. 5 acceptance questions completed through `nsf web ask`. Engine results vary: Hacker News 7 of 7 attempts with results, Bing News 4 of 4, Mwmbl 3 of 6; DuckDuckGo answered 3 of 3 direct calls with a CAPTCHA; Wikinews returned nothing (1 rate-limited). `nsf web deps` exit 0 in 53.2 s |
| `docs/quality/evidence/inference_live.json` | Qwen3-30B-A3B-Instruct-2507-FP8 on vLLM (nightly arm64 image, default flags) at 127.0.0.1:8811: cold start 175 s to healthy; warm restart with a fixed KV cache 52 s; uncontended stream 50.4 tokens/s, first token 222 ms; cited, hedged model answers through the CLI and the console; a live guard rejection recorded |
| `docs/quality/evidence/search_reasoning_live.json` | SearXNG down: the task ends `partial`, "Nothing could be read", every provider failure named. Model down: the answer is the extractive summary, labelled "Advanced reasoning unavailable"; vLLM healthy again 36 s after restart |
| `docs/quality/evidence/autonomy_live.json` | Opt-in autonomy with owner-raised limits: the owner's hypothesis picked first, derivation depth at most 1. Some derived objectives are page fragments (for example a heading ending in `#`) |
| `docs/quality/evidence/news_render_live.json` | Rendering MSN articles needs about 70 to 100 sub-requests; the quick-lookup budget allows 20, so the default answer stays thin. With an owner-raised budget (150 requests, 2 browser sessions) 2 rendered articles were read and the 7-day freshness requirement was met. Robots refusals and HTTP 403 were respected |
| `docs/voice/VOICE_UAT_RESULTS.md` | TTS round-trip WER over four test sentences 30.8 % (RC1 container) to 7.7 % (RC2); WER 0.0 % on 5 sentences through fake-device capture. These prove the plumbing, not recognition of the owner's speech |
| `docs/voice/PERSONAPLEX_INTEGRATION.md` | PersonaPlex runs on this host but upstream is not real-time: 134.5 to 151.5 ms per 80 ms frame, 99.4 to 99.8 ms without system-prompt pinning. An unmerged third-party fork measured 47.25 ms p50 (output quality not evaluated) |
| `docs/quality/evidence/news_render_live_v2.json` | ADR-041 defaults: 4 of 4 current-event questions met freshness; one msn.com render per run within its own 100-sub-request allowance (25 to 75 used); 9 to 16 of the task's 20 requests; robots and 403 respected. R6 repeated it: freshness met on every run |
| `docs/quality/evidence/push_live.json` | Web Push through real FCM in headless Chromium 153: the test push, a finished question and a push after the Pocket tab was closed were answered 201 and shown. Real phones are owner-side |
| `docs/quality/evidence/voice_real_speech.json`, `docs/voice/VOICE_VALIDATION.md` | Recorded human speech from public corpora through the product's ASR client, VAD and grammar: WER 2.1 % clean read speech to 18.6 % accented conversational; VAD word detection 60 % → 87 % with more false barge-ins in loud noise; command grammar 21 % → 66 % held-out |
| `docs/operations/HARDENING_VERIFICATION.md` | Units run as transient user units under their sandbox; exposure of the docker wrapper units 9.0–9.2 → 1.6–1.9; root-only directives owner-side; LUKS2 for instance, checkpoints, models and docker data; swap zram |
| `docs/autonomy/RESEARCH_POLICY.md` §9 | 7-day accelerated soak on the real scheduler (all six checks passed); wall-clock `nsf-soak`: 20 of 20 searches on its first day, then waiting for the UTC reset |
| `docs/quality/evidence/soak_wallclock_v070.json` | The wall-clock soak (`soak_wallclock_v070.json`): about 4 h from 01:23 to 05:18 UTC on 2026-11-22, 0 restarts, RSS 138–141 MB, exactly 20 of 20 autonomous searches that UTC day, then one "refused" decision every 5 min (about 12 an hour); budget, leases, ledger and decisions checks ok. Frozen code at `94ccb09`, live SearXNG, default budgets, units under the services' sandbox |
| `docs/architecture/COGNITIVE_INFERENCE.md` §6, §8 | Measured cost of each guard round on the model's real answers; the remote-provider path live against the local vLLM as a stand-in (a real external provider is owner-side) |
| `docs/architecture/RESOURCE_SCHEDULING.md` | Opt-in idle unloading, verified against a throwaway container; R6 could not prove a shared service idle, so nothing was unloaded there |

## 5. Offline benchmark

`docs/quality/evidence/webcortex_bench_v070.json`, regenerated on the final tree `4c3f209` (offline fictional corpus, `nsf web bench
--repeat 3`, 3 identical runs): retrieval 12/12; evidence coverage 21/24; top answer 9/12; citation precision 193/194
cited sources contain the quote (189/190 answer items fully supported); legacy citation support 190/190; freshness 8/8;
verdict accuracy 29/29; verification states 29/29; contradiction end-to-end precision/recall 1.0/0.8571; memory
recall@3 and MRR 1.0/1.0; fact-check discrimination 2/2; latest version 2/2; top answer on-subject 14/14; 0
cross-subject merges; all safety invariants hold.

The comparison with 0.6.0, the corpus growth, the citation-precision note (206/207 in `CLAIM_VERIFICATION.md` §11 is
an earlier snapshot), the P39 relabel and the knowledge-row trade-off (10 to 8) are in `REGRESSION_REPORT.md` (0.7.0 §2).

After the owner's decisions the knowledge rows went 8 → 9 (`26e16a0`) and 9 → 7 by design (`d88758a`); the commits
report nothing else moved. Bench on the final tree `5f28e6b`: regenerated, identical across 3 runs, headline metrics as
above (knowledge rows 7).

## 6. Regressions and the High items

Listed in `REGRESSION_REPORT.md` (0.7.0 §3). None of the 0.6.0 critical workflows regressed. Test-only regressions
(G-01, R2-01-flake, T3-03) were fixed.

**T1-07 (fixed).** "When was Rust first released?" was answered about the video game (R4). `4807d7c` gives each source
that names the subject a sense (title qualifier, defining sentence, vocabulary), answers in the sense the question
supports, reads other senses without citing them and says so in a rules note; `d88758a` keeps set-aside sources out of
consolidation. R6 live: the language in 2 of 2 runs, the game when named, Python February 1991.

**SEC-G-E1 (fixed; review ACCEPT-with-documented-limits).** `f898fc2` checks that a model sentence says no more than the claims it
cites. Review rounds 4 to 7 each found bypasses (negation flips, relayed instructions, version reversal, symbols and
reported speech that hid imperatives, attacker-chosen names); each was fixed, and two name oracles cut the cost. The
round-4 change regressed "No-one has exploited …" (found in round 5, fixed). Round 8, the final review, accepted with
documented limits once F1 to F3 landed; F1 to F5 landed in `dd02f16`. Verdict: ACCEPT-with-documented-limits (round-8 condition met: F1–F5 in dd02f16; adv11 re-run by the integrator). Survival on the
aligned 44-answer replay 88 %, template fallback 8 of 44 (all empty model answers), 0 unfaithful in the hand checks.

Bench on the first pass: the fix lane compared `090cb7d` with the regenerated `webcortex_bench_v070.json` and found all
393 metric keys identical; the integrator regenerated it on `4c3f209` (3 identical runs, headline metrics unchanged).

## 7. What this regression does not cover

Owner-side (BLOCKED here; no production claim while these remain):
- Real microphones, speakers and handsets. Voice is validated on recorded human speech with simulated channels; the
  owner's Voice check is built and tested with a fake device.
- Pocket and Web Push on real phones (Android Chrome, iOS home-screen app): push ran live through real FCM in headless
  Chromium only.
- Root-only hardening (`IPAddressDeny=`/`IPAddressAllow=`, `ProtectProc=`, `User=`, `DynamicUser=`).
- Brave Search live with the owner's key.
- A real external inference provider.
- Screen readers used by people (the e2e suite checks the accessibility tree).
- Days-long wall-clock autonomy (about 4 h ran, `soak_wallclock_v070.json`).

Not tested or unverified here:
- PersonaPlex as a real-time voice: not real-time upstream; the owner left it as it is (ADR-037 hybrid default).
- UNVERIFIED in R3: T5-05 Pocket voice-on path, T5-M06 breaker state, T5-07 multi-worker search charging.
- NOT TESTED live: the T1-01 subgroup and T1-16 "for decades" cases, the R1-05 conflicting case, R2-09 cross-family
  eviction, an actual idle unload of a shared service, the $1 autonomy cap (prices are 0).
- The R6 polish fixes (D1 to D11, setup items) were not re-run by an independent tester.
- The evidence guard is lexical; its documented limits are in `COGNITIVE_INFERENCE.md` §6.
