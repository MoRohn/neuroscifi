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
