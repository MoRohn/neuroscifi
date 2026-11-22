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

**Release candidate:** version 0.7.0 (SearXNG discovery and an optional keyed search provider, evidence-sensitive
verification, local reasoning model under an evidence guard, knowledge evolution, controlled autonomy, voice through
the Cognitive Voice Bridge, Presence Cortex, Pocket with signed nodes and Web Push). The first acceptance pass ended at
`4c3f209` and was pushed with CI fixes as `94ccb09`; the final tree is the local `HEAD` `5f28e6b` (94 commits after the
owner's decisions of 2026-11-22 to `594e244`, then the final guard round `dd02f16` and its documentation; not pushed). **Assessed by:** the integrating lead, from independent testers' results
(RC1 T1 to T5, RC2 R1 to R3, R4 on `373104e`, R5 on `1e8bf3b`, R6 on `a9f5e37`), independent reviews (architecture,
security, the evidence guard in rounds 1 to 8, signed nodes and push) and the integrator's own gate runs. No single
agent certified readiness. The criterion-by-criterion judgement is `V0.7.0_RELEASE_ACCEPTANCE.md`.

## Verdict

**Release status: Release candidate, ready for owner acceptance with listed limitations. Not production-ready: owner-side verification remains (real phones and handsets including the Voice check, CA install on phones, push on a real phone, root-only systemd hardening checks, Brave live with the owner's key, a real external inference provider, screen readers with people), plus the open owner decisions OD-7 to OD-11.**

The evidence supports "ready for owner acceptance with listed limitations" for an owner-operated deployment on the
DGX Spark, and no stronger statement:

- the evidence guard's final review: ACCEPT-with-documented-limits (round-8 condition met: F1–F5 in dd02f16; adv11 re-run by the integrator);
- the final run on `5f28e6b`: `bash scripts/check.sh` 3303 passed, 18 skipped (live-service tests), 0 failed; e2e 184 passed, 1 skipped (opt-in degraded-voice test), 0 failed; offline bench identical across 3 runs.

It does not support a production claim while these remain owner-side (BLOCKED here):

- real phones and handsets: Pocket, Web Push (live only through real FCM in headless Chromium) and voice;
- real microphones and speakers (voice is validated on recorded human speech; the owner's Voice check is built);
- root-only hardening directives (`IPAddressDeny=`/`IPAddressAllow=`, `ProtectProc=`, `User=`, `DynamicUser=`);
- Brave Search live with the owner's key;
- a real external inference provider (the remote path ran live against the local vLLM as a stand-in);
- screen readers used by people (the e2e suite checks the accessibility tree);
- days-long wall-clock autonomy (about 4 h of wall-clock soak within the cap, `soak_wallclock_v070.json`, and a 7-day
  accelerated soak ran).

The two High items of the first pass are fixed: T1-07 (word sense; independent live retest R6 PASS) and SEC-G-E1
(entailment coverage; review rounds 4 to 8 answered; final review ACCEPT-with-documented-limits).

## 1. Mandatory acceptance criteria (directive §9, as applied in 0.6.0)

| Criterion | Status | Evidence |
|---|---|---|
| All critical user journeys pass real end-to-end execution | **PASS, most with limitations** | `USER_JOURNEY_MATRIX.md` (0.7.0); R1 to R6. R6: 9 of 11 items PASS; its two failures (Brave key-file check D1, guard sentences D5) are fixed, not retested live |
| Every applicable major feature has observable verification evidence | **PASS** with gaps listed | `E2E_TEST_RESULTS.md` (0.7.0 §9); NOT TESTED, UNVERIFIED and owner-side rows are named there |
| No known critical or high-severity release-blocking defects remain | **PASS** | T1-07 fixed, R6 PASS; SEC-G-E1 fixed, final review ACCEPT-with-documented-limits (round-8 condition met: F1–F5 in dd02f16; adv11 re-run by the integrator); the signing and push review found nothing above Medium, all fixed |
| Authentication, authorization and data-integrity checks pass | **PASS** | Pocket family isolation (R6: a stranger family sees nothing); signed node requests and Spark-signed replies (ADR-042; review A-1 to A-6 fixed); R3 `kill -9`: 0 lost acknowledged writes; forget rewrites checkpoints crash-safe (R6 PASS) |
| Persistent data survives the expected lifecycle | **PASS** | R3: migration 0.6.0 to 0.7.0; migration 13 (retired node keys, nonces); checkpoint restore without resurrection, including revoked node keys |
| Frontend and backend integrations function correctly | **PASS** | e2e on the first pass 167 passed, 0 failed; CI green on `94ccb09`; final tree `5f28e6b`: e2e 184 passed, 1 skipped, 0 failed |
| Supported mobile and desktop experiences are usable | **PASS (emulated)** / **owner-side (handsets)** | axe 0 violations (R2); accessibility-tree checks (`3a4cb14`); R6 multi-device flows in Chromium; no physical phone or tablet |
| Error and recovery scenarios behave predictably | **PASS** | S11 (SearXNG, model and voice services down), hostile endpoints (R3), crash recovery, 7-day soak with injected outages and a crash (all six checks passed) |
| Relevant automated regression suites pass | **PASS on `HEAD`** | `5f28e6b`: gate 3303 passed, 18 skipped, 0 failed; e2e 184 passed, 1 skipped, 0 failed; bench identical across 3 runs. Earlier: `594e244` gate 3267 passed, e2e 184 passed; `4c3f209` gate 2588, e2e 167 |
| The application can be built and started reproducibly | **PASS** | R3: wheel from a clean `git archive`; R6: the documented multi-device setup sequence ran end to end |
| External integrations classified verified / sandbox-tested / blocked | **PASS** | §2 |
| No synthetic, mocked or unexecuted functionality presented as production-verified | **PASS** | fake-device audio, simulated channels, the fake Brave, the stand-in remote provider and the fixture-web bench are labelled as such everywhere |

## 2. External integrations

| Integration | Classification | Evidence |
|---|---|---|
| SearXNG (pinned image, loopback, honest User-Agent) | **Verified live; coverage thin** | R1; `searxng_live.json` |
| Brave Search API (keyed, ADR-034 amendment 1) | **Built; tested offline against a fake Brave; live NOT TESTED** (no key; owner-side) | `SEARCH_PROVIDER_STRATEGY.md` §6; R6 item 7 (key handling, Brave never contacted) |
| Structured APIs (Wikipedia, arXiv, OpenAlex, Crossref, PubMed, PyPI, npm, OSV, GitHub) | **Verified live** (bounded) | R1, R6 |
| Local reasoning model: Qwen3-30B-A3B-Instruct-2507-FP8 on vLLM, loopback | **Verified live** | `inference_live.json`; R6 (all answers model-written) |
| Remote inference provider | **Remote path verified live against the local vLLM as a stand-in** (8 passed, 1 skipped); a real provider owner-side | `COGNITIVE_INFERENCE.md` §7 |
| Whisper ASR and Kokoro TTS containers | **Verified on recorded human speech (public corpora) and fake-device audio** | `VOICE_VALIDATION.md`; R2; R6 item 10 |
| NVIDIA PersonaPlex 7B | **Runs; not real-time upstream; experimental, opt-in; owner: leave as it is** | `PERSONAPLEX_INTEGRATION.md` |
| Chromium rendering of news pages (ADR-041) | **Verified live** | `news_render_live_v2.json`; R6 item 5 |
| Web Push through browser vendors' push services (ADR-040) | **Verified live with real FCM in headless Chromium**; real phones owner-side | `push_live.json`; R6 item 3 |
| Real microphones, speakers, handsets, OS share sheet | **Owner-side** | Voice check and guided phone test built |
| systemd units, encrypted volume, TLS chain | **Units verified as transient user units; root-only directives owner-side; LUKS2 verified on this host; private-CA chain verified in Chromium** | `HARDENING_VERIFICATION.md`; `TLS.md`; R6 items 1 and 11 |

## 3. Release risks (0.6.0 risks re-assessed, and new)

| ID | Risk | 0.7.0 status |
|---|---|---|
| R-1 | Key-free engines return nothing with the honest User-Agent | Replaced by SearXNG (ADR-034); a keyed Brave provider is built for the owner's key (live not tested) |
| R-2 | Undated sources keep true claims "insufficient" | Replaced by evidence-sensitive verification (ADR-035): bench `supported` recall 0.4 to 1.0 and discrimination 0/2 to 2/2 (on a larger corpus) |
| R-3 | Lexical evidence reasoning, no model | A local model writes answers under the evidence guard; entailment coverage checks every cited sentence (SEC-G-E1); the guard is lexical and fails closed, with documented limits (`DEFECT_RESOLUTION_LOG.md` O-12); final review ACCEPT-with-documented-limits (round 8); survival 88 % on the aligned replay |
| R-4 | Pocket and autonomous research enlarge the attack surface | Both opt-in; nodes sign their changes and the Spark signs its replies (ADR-042); push carries no payload (ADR-040) |
| R-5 | zod advisory in the open-webSearch daemon | Not re-assessed in this release; the daemon remains a loopback fallback |
| R-6 | A registry record alone settles release statements | Kept (ADR-033) for "published" only |
| R-7 | PersonaPlex not real-time | Owner decision: leave as it is; ADR-037 hybrid default |
| R-8 | News answers thin under the default render budget | Fixed (ADR-041); rendering on by default for a person's news questions is owner decision OD-10 |
| R-9 | Voice verified only on recorded and fake-device audio | Owner-side Voice check; VAD sensitivity is owner decision OD-9 |
| R-10 | Brave terms of use for headline storage and AI evaluation | Owner decision OD-7 |

## 4. Not verified in this environment

Owner-side: real microphones, speakers and handsets; Pocket and push on real phones; Brave live; a real external
inference provider; root-only hardening directives; screen readers used by people; days-long wall-clock autonomy.
Not tested or unverified: PersonaPlex real-time duplex speech; the $1 autonomy cap (prices are 0); T5-05 Pocket
voice-on path, T5-M06 breaker state and T5-07 multi-worker search charging (UNVERIFIED in R3); an actual idle unload of a
shared service (verified on a throwaway container); the R6 polish fixes without an independent re-run.

## 5. Recommended before a wider rollout

1. Push the final tree when the owner accepts; keep the guard's documented limits in view (O-12).
2. Take the open owner decisions OD-7 to OD-11 (`RELEASE_DECISIONS.md` §6).
3. Run the owner-side checks: the Voice check on the owner's microphone, speaker and phone; the guided phone test for
   Pocket and push (Android Chrome, iOS home-screen app); Brave with the owner's key; the root-only hardening checks of
   `HARDENING_VERIFICATION.md` §5; a screen reader on the console and Pocket flows.
4. Read the wall-clock soak report after a few UTC midnights (`RESEARCH_POLICY.md` §9.2). The snapshot so far
   (`soak_wallclock_v070.json`) shows one "refused" decision every 5 min after the cap, about 12 an hour: bounded, but
   noise in the decision log.
5. Re-run `nsf web bench --repeat 3` and the live evidence after each dependency or policy change.
