# Production readiness assessment

**Release candidate:** `abcd303`, version 0.6.0 (NeuroSciFi with Web Cortex), branch `build/neuroscifi-v1`.
**Assessed by:** the integrating lead, from evidence produced by independent testers and the integrator's own runs.
No single agent certified readiness; every fix was re-verified by a different tester or by a deterministic regression
test that fails on the pre-fix commit.

## Verdict

**Ready for an owner-operated deployment on the DGX Spark, with the release risks in §3 accepted by the owner.
Not production-verified for handsets, real sensors, or unattended long-running autonomous research.**

Everything that can run on this host was executed as a user would run it and passes. What remains is either an
explicit owner decision (§3) or blocked by hardware and infrastructure that this environment does not have (§4).

## 1. Mandatory acceptance criteria (directive §9)

| Criterion | Status | Evidence |
|---|---|---|
| All critical user journeys pass real end-to-end execution | **PASS** | `USER_JOURNEY_MATRIX.md`; real processes + Chromium; RC2 and final retests |
| Every applicable major feature has observable verification evidence | **PASS** with gaps listed | `E2E_TEST_RESULTS.md`; NOT TESTED rows are named there |
| No known critical or high-severity release-blocking defects remain | **PASS** | `DEFECT_RESOLUTION_LOG.md`: every critical/high fixed and independently re-verified; open items are medium or lower and are policy decisions (§3) |
| Authentication, authorization and data-integrity checks pass | **PASS** | console token/Host/Origin/CSRF, Pocket pairing/family isolation/revocation, kill -9 integrity (0 lost acknowledged writes), forget + checkpoint restore without resurrection |
| Persistent data survives the expected lifecycle | **PASS** | restarts, console restart mid-task, worker crash recovery, migration 0.5.0 → 0.6.0, checkpoint restore |
| Frontend and backend integrations function correctly | **PASS** | console ↔ API ↔ worker ↔ instance; Pocket ↔ bridge ↔ instance; MCP ↔ orchestrator; 107 e2e (twice in a row) |
| Supported mobile and desktop experiences are usable | **PASS (emulated)** / **BLOCKED (handsets)** | Chromium at 320–1920 px, 0 axe violations; no physical phone or tablet |
| Error and recovery scenarios behave predictably | **PASS** | offline/online, server stopped, writes paused (507), web disabled mid-run, corrupt cache, dead worker, network loss |
| Relevant automated regression suites pass | **PASS** | gate 1557 passed; e2e 107 passed (three full runs across `4eeaed1` and `abcd303`); reproduction 8/8 bit-exact; acceptance 7/7 |
| The application can be built and started reproducibly | **PASS** | wheel from a clean `git archive`, fresh venv, every `--help`, README journey |
| External integrations classified verified / sandbox-tested / blocked | **PASS** | §2 |
| No synthetic, mocked or unexecuted functionality presented as production-verified | **PASS** | simulated acceptance and fixture-web results are labelled everywhere; NOT TESTED and BLOCKED items listed |

## 2. External integrations

| Integration | Classification | Evidence |
|---|---|---|
| Wikipedia, arXiv, OpenAlex, Crossref, PubMed, PyPI, npm, OSV, GitHub APIs | **Verified live** (bounded) | live scenarios A/B/C/E, retests R2/R4; rate limits (Wikipedia 429) observed and handled |
| open-webSearch 2.2.0 daemon (loopback) | **Verified live: integration works; engines return nothing with the honest User-Agent** | daemon start/status/stop, health, port checks; engine results depend on third parties (ADR-032) |
| Chromium rendering of JavaScript pages | **Verified live** | quotes.toscrape.com/js rendered through SafeFetcher |
| Pocket on real handsets, OS share sheet, push | **Blocked** | no handset; no push server (notifications only while the app is open) |
| systemd services, encrypted volume, public TLS chain | **Sandbox-tested only** | `systemd-analyze verify`/`security --offline`, `install.sh --dry-run`, self-signed TLS |
| LLM-based synthesis | **Not present by design** | ADR-022; extractive synthesis only |

## 3. Unresolved release risks (owner decisions)

| ID | Risk | Effect today | Decision record |
|---|---|---|---|
| R-1 | Honest User-Agent: key-free search engines return nothing | General web discovery relies on structured APIs, Wikipedia and feeds; broad "news this week" questions are thin | ADR-032; decisions-pending item 4 (recommended: add a keyed, terms-compliant search API) |
| R-2 | Undated sources: two independent agreeing sources with one undated stay "insufficient" | True claims are often not confirmed (benchmark supported recall 0.4); false claims are reliably disputed | ADR-026; decisions-pending item 5 |
| R-3 | Lexical evidence reasoning (no model) | Contradictions beyond numbers, versions, dates, places and polarity are missed; contested clusters can lead with a minority value (benchmark A02); ranking can still surface weaker sentences | ADR-022; decisions-pending item 1 |
| R-4 | Pocket research and autonomous research enlarge the attack surface | Both are off by default; when enabled: family-scoped, rate-limited, budget-capped | ADR-027, ADR-028 |
| R-5 | Accepted dependency advisory | zod < 4.6.6 (GHSA-mrhx-c7xc-m3w5) inside the loopback search daemon | DEPENDENCY_REVIEW §3 |
| R-6 | A registry record alone settles release statements | A wrong registry entry would be believed | ADR-033 |

## 4. Not verified in this environment

Physical phones and tablets (mobile Safari/Chrome, install-to-home-screen, share sheet, real microphones and cameras,
speech engines); notifications while Pocket is closed; systemd services started under namespace hardening on a real
deployment; a public TLS certificate chain and LAN/VPN topology; encrypted-volume deployment; screen readers, forced
colors and 200 % zoom; concurrent heavy load and days-long autonomous operation; independent re-execution of some
backend journeys (concurrent CLI writers, kill -9 during checkpoint/learn, every API error status as a user journey):
two backend testers stalled, and those items are covered by the security workstream, automated tests and the
integrator's bounded runs rather than an independent journey (`E2E_TEST_RESULTS.md` §5).

## 5. Recommended before a wider rollout

1. Decide R-1 and R-2 (they most affect answer usefulness).
2. Run the systemd units on the Spark with the encrypted state volume and a trusted certificate; repeat the Pocket
   journeys on at least one Android and one iOS device.
3. Re-run `nsf web bench` and the live scenarios after each dependency or policy change; compare with
   `docs/quality/evidence/webcortex_bench.json`.

---

# 0.7.0

**Release candidate:** RC3 `0cd8d8b`, version 0.7.0 (SearXNG discovery, evidence-sensitive verification, local
reasoning model, knowledge evolution, controlled autonomy, voice through the Cognitive Voice Bridge, Presence Cortex);
final tree `HEAD` `4c3f209` (RC3 plus test-only commits, the fixr4 and fixr5 fixes and the guard security-review fixes). **Assessed by:** the integrating lead, from independent testers' results (RC1
T1 to T5, RC2 R1 to R3, R4 on `373104e`, R5 on `1e8bf3b`) and the integrator's own gate runs. No single agent certified readiness. The criterion-by-
criterion judgement is `V0.7.0_RELEASE_ACCEPTANCE.md`.

## Verdict

**Not production-ready. Release status: release candidate; not accepted. Open High: T1-07 (ambiguous-subject sense) and SEC-G-E1 (no entailment check for supported-cited sentences, pre-existing); owner decisions OD-5, OD-6.**

- Open High: T1-07, "When was Rust first released?" answered about the video game (no word-sense handling). Design
  proposed in `RELEASE_DECISIONS.md` §7; owner decision required.
- Open High (security, pre-existing since ADR-036): SEC-G-E1, a sentence citing any supported claim is not checked for
  entailment; exploiting it needs a prompt-injected or misbehaving model. Owner decision OD-6.
- The caveat guard was rebuilt as a closed grammar after an independent adversarial security review (10 findings fixed,
  round-3 re-review ACCEPT-with-documented-limits); it was run live by the fix lane only.
- Final gate on `HEAD` `4c3f209`: 2588 passed, 6 skipped (the `NSF_LIVE=1` live-internet tests), 0 failed, 35 subtests (345 s); e2e 167 passed, 1 skipped, 0 failed (790 s); bench regenerated (3 identical runs).

The release is fit to be put before the owner for a decision with these items and the limitations in §3 and §4. It is not
verified for real microphones, speakers or handsets, for PersonaPlex as a real-time voice, or for unattended days-long
autonomous research.

## 1. Mandatory acceptance criteria (directive §9, as applied in 0.6.0)

| Criterion | Status | Evidence |
|---|---|---|
| All critical user journeys pass real end-to-end execution | **PASS, most with limitations; the Rust first-release question (T1-07) FAILS** | `USER_JOURNEY_MATRIX.md` (0.7.0); R1, R2, R3 retests |
| Every applicable major feature has observable verification evidence | **PASS** with gaps listed | `E2E_TEST_RESULTS.md` (0.7.0); NOT TESTED and UNVERIFIED rows are named there |
| No known critical or high-severity release-blocking defects remain | **FAIL** | T1-07 (Rust answered about the video game) is open and rated High by R4; SEC-G-E1 (no entailment check for supported-cited sentences, pre-existing) is open and rated High for the release decision. R1-01 (High) PASS in R4; T1-01 range case PASS; all High and Critical findings of the guard security review fixed |
| Authentication, authorization and data-integrity checks pass | **PASS** | SEC-H1, M2, M3 PASS (R2); Pocket family isolation and holder-confirmed hand-off (S10); R3 `kill -9`: 0 lost acknowledged writes |
| Persistent data survives the expected lifecycle | **PASS** | R3: migration 0.6.0 to 0.7.0 (schema 7 to 11), checkpoint restore without resurrection, crash recovery |
| Frontend and backend integrations function correctly | **PASS** | console, Pocket, voice hub, MCP and autonomy all go through `orchestrator.submit` (architecture review); e2e 166 passed on RC3 with 1 test flake (fixed) |
| Supported mobile and desktop experiences are usable | **PASS (emulated)** / **BLOCKED (handsets)** | axe 0 violations in 203 runs, console and Pocket, light and dark (R2); 320 to 1920 px; no physical phone or tablet |
| Error and recovery scenarios behave predictably | **PASS** | S11 (SearXNG, model and voice services down), hostile model and SearXNG endpoints (R3), crash recovery |
| Relevant automated regression suites pass | **PASS on `HEAD`** | `4c3f209`: ruff clean; gate 2588 passed, 6 skipped, 0 failed; e2e 167 passed, 1 skipped, 0 failed |
| The application can be built and started reproducibly | **PASS** | R3: wheel from a clean `git archive`, fresh virtual environment, all web assets present |
| External integrations classified verified / sandbox-tested / blocked | **PASS** | §2 |
| No synthetic, mocked or unexecuted functionality presented as production-verified | **PASS** | fake-device audio, simulated acceptance and the fixture-web bench are labelled as such everywhere |

## 2. External integrations

| Integration | Classification | Evidence |
|---|---|---|
| SearXNG (pinned image, loopback, honest User-Agent) | **Verified live; coverage thin** | R1 (115 queries); `searxng_live.json`: some engines answer with a CAPTCHA or rate limits |
| Structured APIs (Wikipedia, arXiv, OpenAlex, Crossref, PubMed, PyPI, npm, OSV, GitHub) | **Verified live** (bounded) | R1 S3 and S5; quotes re-fetched; rate limits reported |
| Local reasoning model: Qwen3-30B-A3B-Instruct-2507-FP8 on vLLM (nightly arm64 image), loopback | **Verified live** | `inference_live.json`; R1 model answers; R3 hostile-endpoint cases |
| Remote inference endpoint | **Not tested live** | regression tests only (loopback-only without `remote_allowed`, no key over http off-host) |
| Whisper ASR and Kokoro TTS containers | **Verified with fake-device audio** | R2 S7 to S11; `VOICE_UAT_RESULTS.md` |
| NVIDIA PersonaPlex 7B | **Runs; not real-time upstream; experimental, opt-in** | `PERSONAPLEX_INTEGRATION.md` |
| Chromium rendering of JavaScript pages (news) | **Verified live; limited by the default request budget** | `news_render_live.json` |
| Real microphones, speakers, handsets, OS share sheet, push | **Blocked** | no devices; no push server |
| systemd units, encrypted volume, public TLS chain | **Sandbox-tested only** | as in 0.6.0 |

## 3. Release risks (0.6.0 risks re-assessed, and new)

| ID | Risk | 0.7.0 status |
|---|---|---|
| R-1 | Key-free engines return nothing with the honest User-Agent | Replaced by SearXNG (ADR-034). General coverage is still thin (O-08) |
| R-2 | Undated sources keep true claims "insufficient" | Replaced by evidence-sensitive verification (ADR-035): bench `supported` recall 0.4 to 1.0 and discrimination 0/2 to 2/2 (on a larger corpus) |
| R-3 | Lexical evidence reasoning, no model | A local model writes answers under the evidence guard; the model cannot raise a verification state. The caveat guard is a closed grammar (`128c339`) plus a rules-labelled note (`224a843`), accepted with documented limits by an independent adversarial review; open: no entailment check for supported-cited sentences (SEC-G-E1) |
| R-4 | Pocket and autonomous research enlarge the attack surface | Both still opt-in; voice adds a LAN surface; security findings H1, M2, M3 PASS on RC2 |
| R-5 | zod advisory in the open-webSearch daemon | Not re-assessed in this release; the daemon remains the loopback fallback behind SearXNG (`search_reasoning_live.json`) |
| R-6 | A registry record alone settles release statements | Kept (ADR-033) for "published" only; stable, compatible, secure and recommended are answered separately |
| R-7 | PersonaPlex not real-time; hybrid default not approved | Owner decision (ADR-037) |
| R-8 | News answers thin under the default render budget | Owner decision |
| R-9 | Fake-device audio only | BLOCKED until real devices are attached |

## 4. Not verified in this environment

Real microphones, speakers and handsets; PersonaPlex real-time duplex speech; the $1 autonomy cap (prices are 0) and
days-long autonomy; the remote inference endpoint; T5-05 Pocket voice-on path, T5-M06 breaker state and T5-07
multi-worker search charging (UNVERIFIED in R3); real screen readers and 200 % zoom; systemd services under hardening;
a public TLS chain; an encrypted state volume.

## 5. Recommended before a wider rollout

1. Decide OD-6 (SEC-G-E1) and on T1-07 (fix with the proposed word-sense design, or accept the release with it open); record the final
   gate and the security review of the guard change.
2. Take the four open owner decisions (`V0.7.0_RELEASE_ACCEPTANCE.md` §5).
3. Run the voice journeys (S7 to S11, Presence A, B, F) with a real microphone and speaker, and the Pocket journeys on
   at least one Android and one iOS device.
4. Re-run `nsf web bench --repeat 3` and the live evidence after each dependency or policy change; compare with
   `docs/quality/evidence/webcortex_bench_v070.json`.
