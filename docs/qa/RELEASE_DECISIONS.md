# Release decisions and readiness matrix: 0.6.0 to 0.7.0

This document records the baseline, the owner decisions that scope 0.7.0, the release-readiness matrix and the
release acceptance criteria. Every verification field is `pending`. Nothing in this document claims that a 0.7.0
capability works. The only verified facts are the 0.6.0 baseline figures in section 1.

Sources: `.omc/autopilot/spec-v0.7.0.md`, `docs/qa/PRODUCTION_READINESS.md` (risks R-1 to R-6, section 4),
`docs/qa/DEFECT_RESOLUTION_LOG.md` (open items), `docs/qa/E2E_TEST_RESULTS.md` section 7,
`docs/architecture/REQUIREMENTS_MATRIX.md`, `.omc/autopilot/decisions-pending.md`.

## 1. Baseline

| Field | Value |
|---|---|
| Version | 0.6.0 (Web Cortex) |
| Code commit | `abcd303` |
| Documentation commit | `0be9add` |
| Branch | `build/neuroscifi-v1` |
| Gate (`bash scripts/check.sh`) | 1557 passed, 6 skipped |
| End-to-end (`tests/e2e`) | 107 passed |
| Production-readiness verdict at 0.6.0 | Ready for owner-operated deployment on the DGX Spark with risks R-1 to R-6 accepted; not production-verified for handsets, real sensors or unattended long-running autonomous research |

Measured environment for 0.7.0 work (spec, Wave 1, 2026-11-21): DGX Spark GB10 (Blackwell, sm_121), aarch64,
20 cores, 121 GB unified memory, driver 615.78, CUDA 13.4; Docker with NVIDIA CDI (no `nvidia` runtime registered);
no podman; `vllm/vllm-openai:nightly` arm64 image present; no LLM weights on disk before this release; Kokoro-82M
(TTS, Apache-2.0) in the Hugging Face cache; no Hugging Face token on the host; ports 8000 and 9000 are used by an
unrelated container.

## 2. Owner decisions for 0.7.0

All six decisions were made by the owner's v0.7.0 directive, 2026-11-21.

| ID | Decision | ADR action |
|---|---|---|
| D1 | SearXNG, self-hosted, is the primary general discovery provider. It replaces the earlier recommendation to add a keyed search API. Structured APIs (arXiv, OpenAlex, Crossref, PubMed, PyPI, npm, OSV, GitHub, Wikipedia) stay direct and do not go through SearXNG. | Supersedes ADR-032; new ADR-034 |
| D2 | Claim verification becomes evidence-sensitive: the required evidence depends on the claim category (stable, historical, time-sensitive, scientific, numerical, comparative, registry, interpretive). | Supersedes ADR-026; new ADR-035 |
| D3 | A real local reasoning model is connected through a model-independent inference gateway. Remote endpoints need explicit owner authorisation and a privacy screen. The model never bypasses evidence verification or authorisation. | Extends ADR-022; new ADR-036 |
| D4 | Controlled autonomous research is opt-in. Daily limits: 20 searches, 60 pages, 30 minutes, $1, 2 concurrent tasks. It has a budget separate from the user's. Pocket research is available to authorised devices. | Builds on ADR-027 and ADR-028 |
| D5 | ADR-033 is kept: a package's own registry record may settle a release statement. The documents and answers distinguish published, stable, compatible, secure and recommended. | ADR-033 retained, scope clarified |
| D6 | NVIDIA PersonaPlex is the voice model, connected through a Cognitive Voice Bridge in which NeuroSciFi stays authoritative for facts, tools, memory and permissions. Three modes: A voice and text, B voice in and text out, C text only. | New ADR-037 |

Prior ADRs affected: ADR-021 to ADR-033 are listed in `docs/architecture/ARCHITECTURAL_DECISIONS.md`. ADR-034 to
ADR-037 are assigned here and must exist in that file before release (see RD-48).

### 2.1 Closure of `decisions-pending.md`

| Pending item | Closed by | Resolution |
|---|---|---|
| 1. Adopt a local LLM for synthesis | D3 | Option (b), an OpenAI-compatible local endpoint behind the synthesis contract, is adopted. Extractive synthesis remains as the labelled degraded path. |
| 2. Pocket research on the LAN | D4 | Pocket research is allowed for authorised devices. It stays opt-in (`pocket_research=false` by default) and the four-action LAN surface of ADR-028 stays owner-enabled per deployment. |
| 3. Autonomous research | D4 | Opt-in, default off, with the daily caps in D4 and a budget separate from the user's. |
| 4. Search-engine User-Agent (ADR-032) | D1 | Option (c) is replaced: self-hosted SearXNG, not a keyed API. The honest User-Agent rule stays; no browser impersonation and no CAPTCHA circumvention. |
| 5. Undated sources and "supported" (ADR-026) | D2 | Evidence requirements follow claim category. Fixtures are corrected only where dates are legitimately required; dates are never invented. |

## 3. Release-readiness matrix

Classification values: Confirmed defect, Architectural limitation, Missing capability, Integration deficiency,
External dependency, Configuration issue, Operational decision, Non-blocking enhancement.

Lanes: A SearXNG; B Inference and reasoning; C Evidence; D Knowledge evolution; E Voice; F Autonomy, Pocket and
multi-node; G QA and integration.

Verification evidence is `pending` for every row. "Origin" in the root-cause column cites 0.6.0 evidence only as
the source of the problem, not as evidence that the proposed solution works.

### 3.1 Carried from 0.6.0

| ID | Item | Classification | Severity | User impact | Root cause | Proposed solution | Dependencies | Owner | Acceptance criteria | Verification evidence |
|---|---|---|---|---|---|---|---|---|---|---|
| RD-01 | R-1 / OPEN-03 / WEB-02: key-free search engines return nothing with the honest User-Agent | Integration deficiency | High | General web discovery depends on structured APIs, Wikipedia and feeds; broad "news this week" questions are thin | open-webSearch scrapers are refused by third-party engines when the User-Agent is honest (ADR-032) | Replace with self-hosted SearXNG as primary general discovery (D1, ADR-034) | RD-24, RD-25 | A | A general query set returns relevant results through SearXNG; the open-webSearch path is either retired or kept only as labelled fallback; ADR-032 is marked superseded | pending |
| RD-02 | R-2 / OPEN-01 / WEB-16: two agreeing independent sources with one undated stay "insufficient" (bench F08, F12, F14; supported recall 0.4; true/false discrimination 0/2) | Architectural limitation | High | True claims are often not confirmed | ADR-026 applies an undated-source discount to every claim class | Evidence-sensitive verification by claim category (D2, ADR-035) | RD-27, RD-28 | C | Benchmark F08, F12, F14 reach "supported" without fabricated dates; false claims remain "disputed"; recall and discrimination are re-measured and recorded | pending |
| RD-03 | R-3 / WEB-42: lexical evidence reasoning, no model | Missing capability | High | Contradictions beyond numbers, versions, dates, places and polarity are missed; synthesis is extractive only | ADR-022: no model in the loop | Local reasoning model behind the inference gateway (D3, ADR-036) | RD-29, RD-30, RD-31 | B | A model-generated answer passes citation and claim validation; template output is never labelled model reasoning; degraded mode is labelled | pending |
| RD-04 | OPEN-02: a contested cluster's representative can be the minority page's value (bench A02) | Confirmed defect | Medium | A contested answer can lead with the weaker value, although the contradiction is listed | Representative chosen by group size, with no source-quality signal other than popularity | Choose the representative by source quality and independence, and show the contested state first | RD-27, RD-28 | C | Bench A02 leads with the better-supported value or presents the conflict without a lead value; regression test fails on the 0.6.0 code | pending |
| RD-05 | R-4: Pocket research and autonomous research enlarge the attack surface | Operational decision | Medium | When enabled, more actions are reachable on the LAN | ADR-027, ADR-028 | Keep both opt-in, family-scoped, rate-limited and budget-capped (D4); re-run security tests with the new voice and gateway surfaces | RD-36, RD-37, RD-43 | F | Both default off; enabling requires an explicit owner action; security tests for the new surfaces pass; no new unauthenticated endpoint | pending |
| RD-06 | R-5: accepted advisory zod < 4.6.6 (GHSA-mrhx-c7xc-m3w5) inside the loopback search daemon | External dependency | Low | None while the daemon is loopback-only | Transitive dependency of open-webSearch 2.2.0 | Retire the daemon with D1, or upgrade it; re-record in DEPENDENCY_REVIEW | RD-01 | A | The advisory is removed from the shipped stack or re-accepted in writing with the loopback constraint | pending |
| RD-07 | R-6 / ADR-033: a registry record alone settles release statements | Operational decision | Medium | A wrong registry entry would be believed | By design (ADR-033) | Keep ADR-033 (D5); distinguish published, stable, compatible, secure and recommended in answers and docs; only "published" is settled by the registry alone | RD-27 | C | An answer to a version question states which of the five statements the evidence supports; "stable", "secure" and "recommended" are not asserted from a registry record alone | pending |
| RD-08 | WEB-16: contradiction detection covers numeric, version, date, polarity and opposite-word conflicts only | Architectural limitation | Medium | Other contradictions are missed | Lexical detector (ADR-022) | Model-assisted contradiction detection whose output is validated against cited evidence | RD-03 | C | Contradictions outside the five lexical types are detected on a benchmark set and each cites the conflicting spans | pending |
| RD-09 | WEB-20: continuous, change-detection and gap-resolution modes are realised through monitors and curiosity, not separate orchestrator paths | Non-blocking enhancement | Low | None observed | Design choice | Keep, or expose as explicit modes once autonomy lands | RD-36 | F | The mapping from each mode to its implementation is documented and tested | pending |
| RD-10 | WEB-31: architecture implications are a keyword mapping | Non-blocking enhancement | Low | Implications are labelled heuristic | No model | Model-drafted implications labelled as proposals, validated against cited findings | RD-03 | B | Implications cite findings; they are labelled proposals, not facts | pending |
| RD-11 | WEB-36: distributed execution across enrolled devices | Missing capability | Low | One executing node only | ADR-031 | Owner scope decision for 0.7.0 multi-node work. The condensed spec lists multi-node in lane F but gives no requirements; record the decision before implementation | Owner | F | Either a written scope with tests, or an explicit deferral recorded in the requirements matrix | pending |
| RD-12 | MEM-06b: no embedding retrieval (association only) | Non-blocking enhancement | Low | Retrieval by meaning is limited | No embedding encoder (ADR-009) | Evaluate an embedding model on the gateway; otherwise keep deferred | RD-29 | D | Decision recorded; if built, recall is measured against association-only retrieval | pending |
| RD-13 | MEM-10: consolidation (rehearsal, lookup-disabled test) deferred | Missing capability | Medium | Knowledge consolidation is not available | Research protocol only in 0.6.0 | Knowledge-evolution consolidation that preserves evidence and history (D2 scope, lane D) | RD-33 | D | Consolidation never deletes a version or its evidence; the lookup-disabled check runs | pending |
| RD-14 | INS-20: no explicit statement-scope field | Non-blocking enhancement | Low | Personal convention versus testable claim is implied, not stored | No free-text statements | Add a scope field to claims in the knowledge-evolution schema | RD-33 | D | A claim records its scope; user-asserted, provisional and verified remain distinct | pending |
| RD-15 | POC-27: owner voice notes, enrolment, attribution, redaction, object and scene notes, private zones | Missing capability | Low | Not available | Needs on-device models; proposed only | Defer unless the voice lane delivers speaker enrolment; record the decision | RD-39 | E | Decision recorded; if built, enrolment, attribution and redaction are tested | pending |
| RD-16 | POC-24, POC-25, POC-26: lock-screen privacy, offline upload queue, everyday companion | Missing capability | Low | Native-only features unavailable | Native target; web build has no queue by design | Keep deferred; native work is blocked on handsets (RD-19) | RD-19 | F | Deferral stays recorded in the requirements matrix | pending |
| RD-17 | GRA-12, ARC-06, RES-05: research agenda items (plasticity rules, neural codec, human-data reanalysis) | Non-blocking enhancement | Low | None for the product | Research scope; ARC-06 needs B200 GPUs | Keep deferred | None | G | Deferral stays recorded | pending |
| RD-18 | OPS-06: real-sensor latency, dropped capture and energy not measured | External dependency | Medium | Host figures only | No capture devices or power meter | Measure when devices are attached | Hardware | G | p50/p95/p99, dropped inputs and joules recorded on real sensors | pending |
| RD-19 | NAT-01, NAT-02, NAT-03: native Android and iOS collectors and release tests | External dependency | Medium | Native clients unverified | No SDK, Xcode or handset on host | Test on handsets when available | Hardware | G | Native release tests pass on at least one Android and one iOS device | pending |
| RD-20 | Not verified (PRODUCTION_READINESS section 4): physical phones and tablets (mobile Safari and Chrome, install to home screen, OS share sheet) | External dependency | Medium | Pocket on real handsets is unverified | No handset attached | Run the Pocket journeys on at least one Android and one iOS device | Hardware | G | Journeys pass on both platforms | pending |
| RD-21 | Not verified: real microphone and camera capture, speech recognition and synthesis engines | External dependency | High | Voice depends on real audio devices | No devices; headless Chromium | Test voice on a real microphone and speaker (see RD-43 to RD-45) | Hardware | E | Capture, playback and transcripts are exercised on real devices | pending |
| RD-22 | Not verified: notifications while Pocket is closed | External dependency | Low | Notifications arrive only while the app is open | No push server (by design) | Keep by design, or add a push design as a separate decision | Owner | F | Behaviour documented in user-facing text | pending |
| RD-50 | Not verified: systemd units under namespace hardening on a real deployment | Configuration issue | Medium | Units may fail to start under hardening | Only `systemd-analyze verify` and `security --offline` were run | Start the units on the Spark with hardening on | Host access | G | Services start, pass health checks and survive restart under hardening | pending |
| RD-51 | Not verified: public TLS certificate chain and LAN/VPN topology | External dependency | Medium | Only self-signed TLS tested | No public certificate | Test with a trusted certificate and the real LAN/VPN path | Certificate | G | Pocket and console work over the trusted chain | pending |
| RD-52 | Not verified: encrypted-volume deployment | Configuration issue | Medium | State on an encrypted volume is untested | Not run | Deploy with the encrypted state volume | Host access | G | Start, write, restart and checkpoint restore succeed on the volume | pending |
| RD-53 | Not verified: screen readers (NVDA, VoiceOver, TalkBack), forced colors, 200 % zoom | Non-blocking enhancement | Medium | Accessibility with assistive tech is unverified (0 axe violations only) | Not tested | Manual test with at least one screen reader per platform; include the new voice controls | Hardware | G | Critical flows operable with a screen reader; forced colors and 200 % zoom render usably | pending |
| RD-54 | Not verified: concurrent heavy load and days-long autonomous operation | Operational decision | Medium | Unattended autonomy is unverified | Not tested | Soak test within the D4 limits | RD-36 | F | A multi-day run stays inside budgets with no leaks or stuck tasks; results recorded | pending |
| RD-55 | Not verified: independent re-execution of backend journeys (concurrent CLI writers, kill -9 during checkpoint or learn, every API error status as a journey) | Non-blocking enhancement | Medium | Covered by security work, automated tests and bounded runs only | Two backend testers stalled (OPEN-04) | Independent re-execution in the 0.7.0 QA pass | None | G | Each journey executed by a tester other than the author, with recorded results | pending |
| RD-56 | UAT-W-14 / W-13: feed relevance and private-feed refusal covered by regression tests only, not live | Non-blocking enhancement | Low | Feed behaviour live is unconfirmed | NOT TESTED live | Run one live feed scenario | None | G | Live run shows unrelated feeds excluded and a private feed URL refused | pending |

### 3.2 New in 0.7.0

| ID | Item | Classification | Severity | User impact | Root cause | Proposed solution | Dependencies | Owner | Acceptance criteria | Verification evidence |
|---|---|---|---|---|---|---|---|---|---|---|
| RD-24 | SearXNG deployment: pinned image, loopback-only bind, generated secret, JSON format enabled, minimal intent-aware engine set | Missing capability | High | No general discovery without it | Not built | Containerised SearXNG with the settings in the spec; health means real relevant results | Docker; image pin | A | Port reachable only on loopback; secret not in the repository; JSON enabled; health check returns relevant results for a fixed query set | pending |
| RD-25 | SearXNG provider adapter (`webcortex/providers/searxng.py`) with normalisation, circuit breakers, adaptive timeout, bounded retry, cooldown, dedupe, cache, `unresponsive_engines` mapped to engine health | Missing capability | High | Results and failures must be reported honestly | Not built | Adapter through the existing registry; `publishedDate` only when present | RD-24 | A | Tests for each normalised field, failure status, breaker and cache; no fabricated dates; engine failures visible | pending |
| RD-26 | AGPL-3.0 review for SearXNG (unmodified, loopback-only) | Operational decision | Medium | Licence obligations unconfirmed | New dependency | Record the review in DEPENDENCY_REVIEW; keep the image unmodified | RD-24 | A | Written review exists and states the use constraints | pending |
| RD-27 | Claim categories and states: stable, historical, time_sensitive, scientific, numerical, comparative, registry, interpretive; supported, disputed, insufficient, conflicting, temporally_unresolved, not_evaluated | Missing capability | High | Verdicts depend on claim type | Single rule in ADR-026 | Implement ADR-035 | None | C | Each category has a required-evidence rule and tests; a credible undated source supports a stable fact; a current-event claim without timeframe evidence is `temporally_unresolved` | pending |
| RD-28 | Independence of sources: syndicated copies counted as independent; consensus invented | Missing capability | High | False support | Not handled in 0.6.0 | Detect syndicated and duplicated copies; never fabricate consensus | RD-27 | C | Duplicate or syndicated sources count once; a test with copied text does not reach "supported" by count alone | pending |
| RD-29 | Inference gateway: OpenAI-compatible, streaming, model discovery, health, structured output, tool calling, token accounting, timeouts, retries, explicit degradation states | Missing capability | High | No model access | Not built | Implement the gateway (ADR-036), local first | RD-30 | B | Each listed feature has a test against the real server; degradation states are explicit | pending |
| RD-30 | Local reasoning model: Qwen/Qwen3-30B-A3B-Instruct-2507-FP8 (Apache-2.0, ungated) on vLLM nightly arm64 | External dependency | High | No reasoning without weights | Weights not on disk at start of 0.7.0; sm_121 and nightly image compatibility untested | Download, serve and test on the Spark; record limits if the runtime fails | Download; GPU | B | The model answers through the gateway on the Spark; load time, memory and throughput recorded; failure recorded as a blocker | pending |
| RD-31 | Reasoning pipeline: intent, memory, evidence, LLM synthesis, citation and claim validation, response, memory; degraded mode labelled | Missing capability | High | Unvalidated model text could mislead | Not built | Implement with validation that the model cannot bypass | RD-29, RD-27 | B | Uncited or unsupported model claims are rejected or labelled; template output never labelled model reasoning; degraded answer is labelled | pending |
| RD-32 | Remote inference endpoints and privacy | Operational decision | High | Private context could leave the host | New surface | Remote only with explicit owner authorisation and a privacy screen; no silent sending | RD-29 | B | With no authorisation, no request leaves the host; with it, the screen blocks private context; tested | pending |
| RD-33 | Knowledge evolution: claim to source, evidence, contradiction and temporal validity; hypothesis, prediction, observation, error, revision; publication, event, retrieval, verification and effective time; supersession | Missing capability | High | Knowledge cannot be revised with history | Not built | Implement the schema and migration | RD-27 | D | Superseded versions are never destroyed; provisional, verified and user-asserted stay distinct; migration from 0.6.0 preserves data | pending |
| RD-34 | Metacognitive metrics: verified gained, gaps resolved, contradictions clarified, prediction accuracy, information gain | Missing capability | Medium | No view of learning progress | Not built | Compute and show the metrics from stored records | RD-33 | D | Each metric is computed from data and reproducible | pending |
| RD-35 | Separation of knowledge evolution, procedural learning and model adaptation; no autonomous weight changes | Operational decision | High | Weights could change unreviewed | New area | Model adaptation requires authorisation, isolated evaluation and rollback; none runs automatically | RD-29 | D | No code path changes model weights without authorisation; documented in CONTINUAL_LEARNING | pending |
| RD-36 | Controlled autonomous research: opt-in; 20 searches, 60 pages, 30 min, $1, 2 concurrent per day; separate user budget; prioritisation by goals, gaps, stale evidence, contradictions, prediction uncertainty, information gain, cost | Missing capability | High | Unbounded autonomy would be unsafe | 0.6.0 caps exist but were not tested under days-long load | Implement and enforce per D4 | RD-25, RD-31 | F | Off by default; each limit stops work when reached; user budget unaffected; ranking inputs logged | pending |
| RD-37 | Pocket research for authorised devices: initiate, progress, sources, cancel, continue elsewhere | Missing capability | High | Remote research control | Partly built in 0.6.0 (ADR-028) | Extend to the 0.7.0 model and budgets, keeping family scope | RD-36 | F | An authorised device can run each action; an unauthorised or revoked device cannot; no private data crosses family boundaries | pending |
| RD-38 | External dependency: PersonaPlex weights (`nvidia/personaplex-7b-v1`) are gated; licence "other" (NVIDIA) | External dependency | High | No PersonaPlex voice until resolved | Gated repository; the host has no Hugging Face token | The owner accepts the licence and logs in on the host; until then record the blocker. Repo code is public (github.com/NVIDIA/personaplex) | Owner action | E | Owner accepted the licence; token present; weights downloaded; or the blocker is recorded in VOICE_UAT_RESULTS | pending |
| RD-39 | PersonaPlex runtime on the target (aarch64, sm_121) | Integration deficiency | High | Voice may not run on this hardware | Untested on GB10 | Audit, build and run; record exact limits | RD-38 | E | Real-time duplex session runs on the Spark, or the failure is recorded with its cause | pending |
| RD-40 | Cognitive Voice Bridge: NeuroSciFi authoritative for facts, tools, memory, permissions | Missing capability | High | Voice model could state unverified facts | Not built | Implement ADR-037 | RD-39, RD-31 | E | A spoken answer carries the same evidence and permission checks as a text answer | pending |
| RD-41 | Hybrid speech path (e.g. Kokoro TTS) if PersonaPlex cannot speak external text | Missing capability | Medium | Verified answers could not be spoken | Depends on RD-39 findings | Build only if needed; label the path | RD-39 | E | Verified text is spoken through the chosen path, or the audit shows PersonaPlex can do it | pending |
| RD-42 | Modes A (voice and text), B (voice in, text out), C (text only, no PersonaPlex needed) | Missing capability | High | Users need a working mode without voice | Not built | Implement and test switching | RD-40 | E | Mode C works with PersonaPlex stopped; switching modes during a session keeps the conversation | pending |
| RD-43 | Voice controls and privacy: mic, speaker, session, voice, device, volume, transcript, privacy and retention controls; mic off stops capture and transmission; consent and capture indicator; raw audio not stored by default; one capturing device at a time with explicit handoff | Missing capability | Critical | Covert or duplicate capture | Not built | Implement and verify capture actually stops | RD-21 | E | With mic off, no audio frames are captured or sent (checked at the transport); consent precedes capture; indicator visible; no raw audio on disk by default; a second device cannot capture without handoff | pending |
| RD-44 | Transcripts: provisional and final, attributed, timestamped, approximate versus authoritative | Missing capability | Medium | Misleading transcripts | Not built | Implement with documented accuracy limits | RD-40 | E | Each transcript line has status, speaker, time; accuracy measured on a recorded set and limits documented | pending |
| RD-45 | Interruption and recovery: barge-in, session drop, long tasks survive voice session end | Missing capability | High | Lost work on disconnect | Not built | Decouple tasks from the voice session | RD-40, RD-36 | E | Interrupting stops speech; a dropped session reconnects; a running task completes after the session ends | pending |
| RD-46 | Resource management: GPU memory monitoring, residency and idle unload, audio priority, measured co-residency of reasoning and voice models | Missing capability | High | Models may not fit together or may starve audio | Unified 121 GB shared by two large models | Monitor, unload idle models, benchmark | RD-30, RD-39 | B | Co-residency measured and recorded; idle unload tested; audio latency holds under reasoning load | pending |
| RD-47 | Port conflict: an unrelated container uses 8000 and 9000 | Configuration issue | Low | Services fail to bind | Shared host | Use other ports and document them | None | G | No NeuroSciFi service uses 8000 or 9000; documented ports bind | pending |
| RD-48 | ADR-034 to ADR-037 and release documents (SEARXNG_INTEGRATION, SEARCH_PROVIDER_STRATEGY, CLAIM_VERIFICATION, KNOWLEDGE_EVOLUTION, CONTINUAL_LEARNING, RESEARCH_POLICY, COGNITIVE_INFERENCE, PERSONAPLEX_INTEGRATION, COGNITIVE_VOICE_BRIDGE, STREAMING_AND_TRANSCRIPTS, VOICE_UAT_RESULTS, FINAL_REGRESSION_REPORT, V0.7.0_RELEASE_ACCEPTANCE) | Missing capability | Medium | Decisions and limits undocumented | Not written | Write each from measured results | All lanes | G | Every listed file exists; ADRs 026 and 032 marked superseded; no claim without evidence | pending |
| RD-49 | Regression: v0.6.0 critical workflows and security | Operational decision | Critical | Breaking existing behaviour | Large change set | Run the gate, e2e and security checks after each lane lands | All lanes | G | Gate and e2e pass with no fewer tests than the baseline (1557 passed, 107 passed); critical security tests pass | pending |

## 4. Release acceptance criteria for 0.7.0

| # | Criterion | Related rows | Status |
|---|---|---|---|
| 1 | SearXNG general search is verified, or upstream limits are recorded as blockers | RD-01, RD-24, RD-25, RD-26 | pending |
| 2 | Authoritative integrations (structured APIs, feeds) still work | RD-01, RD-49 | pending |
| 3 | A real reasoning model is connected and tested | RD-29, RD-30, RD-31 | pending |
| 4 | Claim verification is correct for stable, current and authoritative claims | RD-02, RD-07, RD-27, RD-28 | pending |
| 5 | Consolidation preserves evidence and history | RD-13, RD-33 | pending |
| 6 | Autonomy respects opt-in and budgets | RD-36 | pending |
| 7 | Pocket research keeps permission boundaries | RD-05, RD-37 | pending |
| 8 | PersonaPlex is validated on the target runtime | RD-38, RD-39 | pending |
| 9 | Speech and cognition are integrated | RD-40, RD-41 | pending |
| 10 | Transcripts are accurate within documented limits | RD-44 | pending |
| 11 | Mode switching works | RD-42 | pending |
| 12 | Interruption and recovery are tested | RD-45 | pending |
| 13 | Critical security checks pass | RD-05, RD-32, RD-43, RD-49 | pending |
| 14 | No unresolved high-severity release blockers | all High and Critical rows | pending |
| 15 | v0.6.0 critical workflows pass | RD-49 | pending |

A criterion moves from `pending` only when its linked rows hold verification evidence recorded by someone other than
the author of the change.
