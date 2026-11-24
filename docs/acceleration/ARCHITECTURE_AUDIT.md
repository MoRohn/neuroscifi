# Architecture and capability audit

Status: 2026-11-23, of commit `9e4570a` (0.7.0) on the reference DGX Spark. Method: the code was read by subsystem
(three independent read-only audits: the cognitive core, the research pipeline and memory, the runtime), suspicions
were reproduced on throwaway databases, and the running services were measured. Where the documentation and the
code disagreed, the code is what this page records. Findings marked *reproduced* were made to fail by a script or
a test before they were fixed.

The inventory and §4 describe `9e4570a`. Where the branch has since changed a row, the row says so ("Phase 2: …"
for the work that followed the owner's decisions), and §4 ends with the findings that phase 2 itself turned up.
Status and rollback of every change are in [ENGINEERING_PLAN.md](ENGINEERING_PLAN.md).

Columns of the inventory: **Built** (code exists) · **Runs** (exercised on this host outside tests) · **Tested** ·
**Measured** (a performance or quality number exists) · **Gap**.

## 1. What runs on the host

```
 clients                         the DGX Spark (one host)                               loopback services (Docker)
 ───────                         ────────────────────────                               ──────────────────────────
 owner console (web) ──┐   ┌──────────────────────────────────────────┐        ┌──▶ vLLM  :8811  Qwen3-30B-A3B FP8   44 GiB
 Pocket (PWA, HTTPS) ──┤   │ orchestrator (webcortex/orchestrator.py) │        ├──▶ vLLM  :18431 Whisper large-v3     4 GiB
 voice (hub in console │──▶│  intent ▸ memory ▸ discovery ▸ reading   │──gateway──▶ Kokoro :18432 TTS                 1.5 GiB
   and in Pocket)      │   │  ▸ claims ▸ verification ▸ synthesis     │        ├──▶ vLLM  :18441 embeddings  (new, optional)
 CLI, MCP ─────────────┤   │  ▸ reasoning (model) ▸ guard ▸ report    │        ├──▶ vLLM  :18442 reranker    (new, optional)
 timer (tick) ─────────┘   │  ▸ consolidation (owner accepts)         │        └──▶ SearXNG :8888 discovery
                           └───────────────┬──────────────────────────┘
                                           │                              the public web: screened queries, page fetches
                     one SQLite file per individual (+ sidecars: web cache, inference ledger, `<db>.resources/`)
```

Processes: the research worker (`nsf web work`), the console, Pocket, and the timer pass are separate processes that
share the individual's SQLite file and the small state files in `<db>.resources/`. No message bus, no second
database, no cloud dependency. At audit time no owner instance was running on this host: the model, speech and
search containers were up, and a multi-day soak of the worker ran on a scratch individual with no model configured.

## 2. Capability inventory

### Cognitive core (`src/neuroscifi/instance/`, `lab/`, `acceptance/`)

| Capability | Built | Runs | Tested | Measured | Gap |
|---|---|---|---|---|---|
| Executive decision: act, observe, ask, test a reference, defer (`executive.py`) | Yes: seven fixed rules, first match | Yes | Yes | observe and decide 2.2 ms at 10k events | Flat, not hierarchical. "act" is a recommendation in text: nothing is actuated |
| Beliefs: exact Bayesian posterior over hidden bits, recomputed from evidence on every read (`beliefs.py`) | Yes | Yes | Yes | — | The reading error is fixed at 0.10 and never estimated; the acceptance calibration generates its data with the same 0.10 |
| Change detection and scoped relearning | Yes | Yes | Yes | — | Relearning needs the owner's confirmation, by design |
| Executable graphs: learn (normalised LMS), evaluate, promotion gate, rollback (`graphs.py`, `learner.py`) | Yes | Owner-triggered, offline | Yes | fit 16 s at n = 1024 | The CLI and console can feed it simulated datasets only. Nothing outside the core reads a promoted graph. The sparse learner's result was computed and discarded (C6), and the dense fit that was promoted fails beyond about 100 populations (C10). Phase 2: new individuals pack the better learner's graph, and the gate refuses a snapshot no better than no graph |
| Recall by context | Yes | Offline replay only | Yes | — | Not connected to live decisions |
| Predictive coding | Partial: a likelihood-ratio change detector; recall commits a prediction before the outcome | Yes | Yes | — | No error-driven weight update on the live path |
| Hebbian or STDP plasticity, neuromodulation | No (documented as deferred) | — | — | — | Absent |
| Pruning | Partial: top-k by magnitude at pack time | Yes | Yes | — | — |
| Consolidation and replay | No at audit time (documented as deferred). Phase 2: retention yes, replay no | Phase 2: yes | Phase 2: yes | `compute()` 24 ms at 100,000 consolidated readings | Evidence expired after 7 days by default and nothing was folded into a durable form (§4, C3). Phase 2: what an expired learning event taught stays as a consolidated reading (schema 14). Rehearsal and replay remain absent |
| Episodic memory: evidence journal | Yes | Yes | Yes | — | 7-day lifetime unless the owner promotes an event. Phase 2: the episode's detail still goes after 7 days; its reading stays until forgotten |
| Working memory: pending questions | Yes | Yes | Yes | — | The ask budget of an epoch never refilled (C7). Phase 2: it counts a rolling 7 days |
| Sensory pathways | Mocked: three owner-declared channels; `lab/sensory.py` is synthetic and imported by nothing | Lab only | Lab | — | No live sensor path on the Spark |
| Checkpoints and restore | Yes | Yes | Yes | 44.6 MB/s cold pack | A restore resurrected a forgotten epoch's derived rows (C1, fixed). A restored copy reused evidence positions and reverted the owner's settings (C11, fixed in phase 2) |
| Governed forgetting | Yes, extensive | Yes | Yes | — | Three cascade holes (C1, C2, C4, fixed). Phase 2 adds forgets for one consolidated reading and for a camera |

### Research, memory and reasoning (`src/neuroscifi/webcortex/`, `inference/`)

| Capability | Built | Runs | Tested | Measured | Gap |
|---|---|---|---|---|---|
| Intent, discovery, safe fetch, extraction | Yes | Yes | Yes | offline task 0.05 s | Pages were read one at a time; live reading is 7 to 19 s of a task (W5). A stop took effect up to 2 s late (W9). Phase 2: four pages at once by default, and no request after a stop |
| Claims, verification (8 categories, 6 states), word sense | Yes | Yes | Yes | offline bench, deterministic | A contradiction worded differently is not detected (W2) |
| Extractive synthesis | Yes | Yes | Yes | — | — |
| Model-written answer with the sentence guard | Yes | Yes | Yes, 8 adversarial rounds | 46 of 51 model answers; 94 sentences kept | The guard is lexical (documented). Phase 2 found gaps in it that are older than the branch (W11 fixed by a refusal, W12 open) |
| Self-critique pass | Yes | Yes | Yes | 42 % of task time; 0 sentences removed; 41 of 94 marked | It cannot remove a sentence by construction, and most of its marks sit on verbatim restatements (W3; 42 of 46 by the exact test of phase 2). Phase 2: it reads only the other sentences |
| Memory retrieval (`knowledge.query`) | Yes: BM25 over a statement's words | Yes | Yes (3 scorable queries) | 0.6 ms; paraphrase recall@1 0.27 | Lexical only; unrelated memory returned for any shared word (W1a). Semantic retrieval is optional; phase 2 made it a managed service that `nsf setup` switches on |
| Memory answers without the web (`intent.covers`) | Yes: shared 5-letter stems | Yes | Yes | accepts 94 of 288 wrong-property facts | Answers the wrong property (W1b, reproduced) |
| Knowledge evolution: versions, hypotheses, predictions, calibration | Yes | Yes | Yes | — | The loops are open: calibration is written and never read; the research policy update is "advisory only: nothing reads it" (W7) |
| Controlled autonomy, curiosity | Yes, opt-in | Soak run | Yes | — | — |
| Worker queue | Yes | Yes | Yes | submit to claimed 2.7 s mean | Polls every 5 s; first in, first out regardless of who asked (R5a, W6) |
| Inference gateway | Yes | Yes | Yes | first token 180 to 720 ms | No priority; one provider; no cache or queue telemetry (R4) |

### Runtime (`resources/`, `voice/`, `pocket/`, `presence/`, `console/`)

| Capability | Built | Runs | Tested | Measured | Gap |
|---|---|---|---|---|---|
| Resource pressure level, audio priority, background model slots | Yes | Yes | Yes | research and voice latency with and without each other | Pocket voice was invisible to it (R2) |
| Idle unload and reload on demand | Yes | **No**: no unit or timer is installed on this host | Yes (fake host) | disposable container only | A reload ignored the caller's deadline and could block about 20 minutes on a crash loop; no free-memory check (R1). Phase 2 found four more defects of the reload path (R10 to R13) |
| Voice: Whisper ASR, Kokoro TTS, bridge, barge-in | Yes | Containers healthy | Yes | ASR 0.27 s, TTS 0.12 s | Barge-in did not order replies; one lost chunk killed an utterance; a restart loses a followed answer (R6, R7, R8) |
| Pocket: pairing, families, signed replies, push, signed nodes | Yes | Not on real handsets | Yes | — | No offline outbox; no capability registry beyond node enrolment. The Spark is the only executing node (documented) |
| Presence entity state feed | Yes | Yes | Yes | renderer performance | `device_sync` is declared and never emitted |
| Telemetry | Ledger, resource sample on demand, idle events, task events | Yes | Yes | — | No queue, prefill, decode, cache, temperature or power reading (R-telemetry) |

## 3. Dependency map

| Component | Depends on | Degrades to |
|---|---|---|
| Orchestrator | the instance file; providers; the safe fetcher | a partial or failed report with the reason |
| Reasoning | the gateway and the answer model | the extractive report, labelled `template` |
| Guard | nothing but the bundle (deterministic) | — |
| Memory retrieval | the instance file; optionally the two memory models | the lexical ranking, with the reason |
| Memory-answer check | the answer model | the lexical decision |
| Voice | ASR and TTS containers; the console or Pocket process | typed questions |
| Resource policy | `/proc`, `nvidia-smi`, `docker`, the server's `/metrics` | "not readable", never a guess |
| Pocket | the private CA; the instance file | "Spark unreachable" |

## 4. Deficiencies found

Severity: **A** a wrong result or a broken promise to the owner · **B** a measured cost · **C** a gap in capability.
Status as of this branch is in [ENGINEERING_PLAN.md](ENGINEERING_PLAN.md).

### Core

| # | Sev | Finding | Evidence |
|---|---|---|---|
| C1 | A | A checkpoint restore brought back a forgotten epoch's belief revisions, decisions, pending questions, links and context. `_replay` deleted evidence by tombstone only; the live `forget_epoch` deletes six more tables. With all of an epoch's evidence already expired, no archive was marked stale at all. Contradicts "a restore never resurrects forgotten records" | Reproduced: 8 observations, 3 decisions, checkpoint, forget, restore: 1 revision, 3 decisions, 1 question, 1 context row came back |
| C2 | A | `forget_epoch` kept the `epochs` row with the owner's free-text note | Reproduced |
| C3 | C | Learned state expires: beliefs are computed from evidence with a 7-day lifetime, and nothing consolidates it. After a sweep on day 8 the posterior is 0.5 again and the executive says "observe" | Reproduced. Documented as deferred (MEM-10). **Fixed in phase 2** for the calibration beliefs: a consolidated reading per expired learning copy (ADR-044); rehearsal and replay stay deferred |
| C4 | A | `forget_dataset` left the coactivation and lesion statistics derived from the dataset (`graph_effects`), and the table grew without bound | Code: no `DELETE FROM graph_effects` existed |
| C5 | A | Two processes opening the file during an upgrade re-ran a migration ("duplicate column name") and left a connection inside a transaction | Reproduced by interleaving |
| C6 | B | The promoted graph is the dense fit; a fit with the sparse matrix costs about four times a dense-only fit (16.4 s against 4.1 s at n = 1024) and the sparse graph is used only in metrics. *Corrected in phase 2:* the first reading blamed the edge-exchange loop for that factor; measured, the loop was 66 % of a fit at 24 populations and 20 to 29 % at 1024, the rest being the sparse matrix's own window update | Measured (`accel_decisions_graph_learner_time.json`). The requirements matrix said "verified" for edge exchange. **Addressed in phase 2**: the loop runs for all receivers at once, bit for bit (2.4 times faster at 24 populations, 1.7 at 256; a fit still costs 2.6 to 3.9 times a dense-only one), and new individuals pack the better learner's graph (ADR-045) |
| C7 | C | The ask budget of an epoch counts every question ever asked there, so a long-lived epoch stops asking | Code and a test that asserts it. **Fixed in phase 2**: a rolling window of 7 days; 0 restores the old rule |
| C8 | B | `lesion` recomputed the full error once per edge (16 s at n = 128) although the closed form was already computed | Measured |
| C9 | A | A dataset shape too large for one SQLite blob surfaced as a raw SQLite error | Arithmetic |

### Research, memory, reasoning

| # | Sev | Finding | Evidence |
|---|---|---|---|
| W1a | C | Memory retrieval is lexical: paraphrase recall@1 0.27, unnamed subject 6 of 12, unrelated memory for 10 of 10 questions memory cannot answer | `accel_baseline_memory.json` |
| W1b | A | Memory answered a question about another property: "encryption in transit?" from "no encryption at rest"; "tallest in the world?" from "tallest in the region" | Reproduced. On the benchmark the lexical test accepts 94 of 288 wrong-property facts |
| W2 | A | Two statements of one fact in different words ("186 metres tall", "a structural height of 192 m") both became knowledge with no contest; a newer version worded differently did not supersede the older | Reproduced |
| W3 | B | The self-critique call is 42 % of a task's time, removes nothing by construction, and most of its marks are false alarms | `accel_baseline_pipeline.json`; `accel_after_pipeline.json`. **Narrowed in phase 2**: it reads only sentences that are not a cited claim word for word |
| W4 | A | A model that correctly wrote no sentence (the evidence does not answer the question) was reported as "every model sentence failed NeuroSciFi's citation and claim checks" | Reproduced on three benchmark questions |
| W5 | B | Pages are read strictly one at a time; live, reading is most of a task | Existing live evidence: 7 to 19 s for 3 to 12 sources. **Fixed in phase 2**: read-ahead is the default |
| W6 | B | The worker's queue is first in, first out, and an autonomous task waiting for a model slot blocks the single worker | Code |
| W7 | C | The feedback loops of knowledge evolution are open: calibration, policy updates and resolved hypotheses change nothing downstream | Code |
| W8 | B | `knowledge.query` rebuilds its index on every call: 24 ms at 1k statements, 393 ms at 20k, two to three times per task | Measured |

### Runtime

| # | Sev | Finding | Evidence |
|---|---|---|---|
| R1 | A | An idle reload ignored the caller's deadline (up to about 20 minutes on a crash-looping container) and did not check free memory first. Latent: idle unload is not installed here | Reproduced with a fake runner |
| R2 | A | A Pocket voice session did not defer background research and did not protect the speech services from the idle unloader; the documentation said it did | Code; worse than first read (the audio check looked at the owner's sessions only) |
| R4 | B | No priority at the model server: the owner's first token waited behind background prefill, 1.72 s against 0.18 s | `accel_baseline_inference.json` |
| R5a | B | A queued question waited for the worker's 5 s poll: 2.7 s on average | Measured: 12 submissions, mean 2738 ms, max 4818 ms |
| R6 | A | An older, slower reply could be spoken after a newer one or after an interruption | Code; reproduced in a test |
| R7 | A | One lost audio chunk silently ended the utterance | Code; reproduced in a test |
| R8 | C | A restart loses the follow-up of a research answer asked by voice | Code |
| R9 | B | The ledger had no index and is read on every Presence refresh | Code |

### Found in phase 2

Turned up while the owner's decisions were built and reviewed. Every row describes behaviour of `9e4570a`; R10
to R13 were latent there because idle unloading is not installed on this host.

| # | Sev | Finding | Evidence | Status |
|---|---|---|---|---|
| C10 | A | The dense learner stops fitting beyond about 100 populations, while `datasets.add` accepts 1024. The snapshot cut from its fit is no better than a graph with no edges at 160 populations and about 14,000 times worse at 256; the sparse learner stays at the noise floor (0.0043) up to 1024. Under `gate-v1` such a snapshot was promoted as the first graph of a context. No shipped data source exceeds 24 populations | Reproduced through the product path (`accel_decisions_graph_learner_sizes.json`). The cause is a reading of the update rule, not a proof | Fixed for candidates learned from now on: the gate refuses a snapshot no better than no graph, and new individuals pack the better learner's graph. Open for existing individuals on `gate-v1` at 96 to 128 populations, and an active graph is not demoted |
| C11 | A | A restored copy continued its own evidence sequence, so positions were issued twice across a restore. Harmless while rows were found by id; consolidated readings are identified by position | Code; test | Fixed |
| C12 | C | No instance setting could be changed after `nsf init` | Code | Fixed: `nsf config` |
| C13 | A | A scoring rule with `"bits": [4.0]` or `"degrees": [true]` validated and then crashed or bent packing | Reproduced by an independent review | Fixed |
| W9 | A | On the one-at-a-time path the web settings were re-read at most every 2 s and a cancel was seen at the next page: a run that finishes inside 2 s read every page after web access was turned off or its sites denied (11 requests), although the security model said a denial takes effect at once | Reproduced (`accel_decisions_read_ahead.json`, `stops`) | Fixed: the gate is asked before every network step |
| W10 | A | A crawl's robots.txt and sitemap reads ignored a stop (three sitemaps requested after a cancel) | Reproduced by an independent review | Fixed |
| W11 | A | A sentence that ended in its own "according to <source>" lost up to 40 characters after it when the code replaced the attribution: a claim's exception ("… except in versions 2 and 3") could vanish from the shown sentence | Reproduced by two independent reviews | Fixed by refusing such a sentence |
| W12 | A | Further gaps of the lexical guard: a one-version inversion of an exception is accepted ("except", "other than", "apart from" are not read as negation); a sentence the model shortens so that an exception is gone passes; words are compared without case ("Co" for "CO"); a clean sentence is accepted for a claim that holds a direction-override character; a supported claim ending in a bare number after "and" is rejected when copied word for word | Reproduced by the security review and recorded in the differential reference | **Open.** An attempted fix of the last one regressed the guard and was withdrawn |
| R10 | C | A service unloaded by the idle policy could not be reloaded on demand from the hardened console and worker units: their account has no Docker access and may not start units, and the spawned reload died with the console | Code and the units' sandbox | Built: wake requests served by a path unit. **Unverified as installed system units**; a two-account install needs access the installer does not grant |
| R11 | A | A systemd unit stopped by the idle policy was reloaded with `run.sh start`: a second, unsupervised container beside the unit. The test's fake systemd had kept a stopped unit "active" | Code; reproduced with the corrected fake | Fixed |
| R12 | A | A running container counted as loaded whether or not it answered, and a failed start was left running, so the next lookup called a model that did not answer | Reproduced with a fake runner. Unverified against a real container that never answers | Fixed for services with a metrics address; TTS and SearXNG keep the old rule |
| R13 | A | A loopback port belongs to whoever binds it first. While a model server's container is down (unloaded while idle, stopped, crashed), a process of another local account could take its port and receive the key and the prompts or memory texts | Reproduced with a stand-in listener for the memory key | Mitigated: the gateway checks who owns the listening socket on this deployment's own service ports. Inside the hardened units other accounts cannot be told apart, so there the check refuses nobody |

## 5. Checked and found sound

Recorded so that they are not audited again without cause.

- Interactive paths of the core are fast at scale (observe and decide 2.2 ms at 10k events; the write gate 4 ms at
  1M rows). No core path uses or needs the GPU: the workloads are count arithmetic and 24 × 24 matrices.
- `store.write` rolls back on any failure, a failed COMMIT included. Graph leases, promotion re-checks and rollback
  are sound. Checkpoint publication uses hard links and never overwrites; the rewrite journal is crash-safe.
- Task leases are fenced (`LostLease`); submit deduplication runs inside the write transaction; a double settle of a
  prediction is harmless; the 600 s lease covers the model call.
- The gateway makes no health probe and no `/models` call per request; the deadline holds across dripping reads; a
  stream is never retried after its first byte. Keep-alive would save under 1 ms on loopback.
- The console binds loopback with Host and Origin checks and a constant-time token comparison. Pocket caps
  connections and bodies, sends empty pushes, and creates keys 0600. A world-readable key file is refused. The
  ledger holds no prompt text.
- vLLM's prefix cache is on and serves 61 % of prompt tokens; the answer prompt's layout (constant system prompt
  first) suits it. The guided-decoding schema varies only in its claim-id list, so compiled grammars are reused.
