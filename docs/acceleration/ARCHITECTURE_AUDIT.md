# Architecture and capability audit

Status: 2026-11-23, of commit `9e4570a` (0.7.0) on the reference DGX Spark. Method: the code was read by subsystem
(three independent read-only audits: the cognitive core, the research pipeline and memory, the runtime), suspicions
were reproduced on throwaway databases, and the running services were measured. Where the documentation and the
code disagreed, the code is what this page records. Findings marked *reproduced* were made to fail by a script or
a test before they were fixed.

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
| Executable graphs: learn (normalised LMS), evaluate, promotion gate, rollback (`graphs.py`, `learner.py`) | Yes | Owner-triggered, offline | Yes | fit 16 s at n = 1024 | The CLI and console can feed it simulated datasets only. Nothing outside the core reads a promoted graph. The sparse learner's result is computed and discarded |
| Recall by context | Yes | Offline replay only | Yes | — | Not connected to live decisions |
| Predictive coding | Partial: a likelihood-ratio change detector; recall commits a prediction before the outcome | Yes | Yes | — | No error-driven weight update on the live path |
| Hebbian or STDP plasticity, neuromodulation | No (documented as deferred) | — | — | — | Absent |
| Pruning | Partial: top-k by magnitude at pack time | Yes | Yes | — | — |
| Consolidation and replay | No (documented as deferred) | — | — | — | Evidence expires after 7 days by default and nothing is folded into a durable form (§4, C3) |
| Episodic memory: evidence journal | Yes | Yes | Yes | — | 7-day lifetime unless the owner promotes an event |
| Working memory: pending questions | Yes | Yes | Yes | — | The ask budget of an epoch never refills (C7) |
| Sensory pathways | Mocked: three owner-declared channels; `lab/sensory.py` is synthetic and imported by nothing | Lab only | Lab | — | No live sensor path on the Spark |
| Checkpoints and restore | Yes | Yes | Yes | 44.6 MB/s cold pack | A restore resurrected a forgotten epoch's derived rows (C1, fixed) |
| Governed forgetting | Yes, extensive | Yes | Yes | — | Three cascade holes (C1, C2, C4, fixed) |

### Research, memory and reasoning (`src/neuroscifi/webcortex/`, `inference/`)

| Capability | Built | Runs | Tested | Measured | Gap |
|---|---|---|---|---|---|
| Intent, discovery, safe fetch, extraction | Yes | Yes | Yes | offline task 0.05 s | Pages are read one at a time; live reading is 7 to 19 s of a task (W5) |
| Claims, verification (8 categories, 6 states), word sense | Yes | Yes | Yes | offline bench, deterministic | A contradiction worded differently is not detected (W2) |
| Extractive synthesis | Yes | Yes | Yes | — | — |
| Model-written answer with the sentence guard | Yes | Yes | Yes, 8 adversarial rounds | 46 of 51 model answers; 94 sentences kept | The guard is lexical (documented) |
| Self-critique pass | Yes | Yes | Yes | 42 % of task time; 0 sentences removed; 41 of 94 marked | It cannot remove a sentence by construction, and about three quarters of its marks sit on verbatim restatements (W3) |
| Memory retrieval (`knowledge.query`) | Yes: BM25 over a statement's words | Yes | Yes (3 scorable queries) | 0.6 ms; paraphrase recall@1 0.27 | Lexical only; unrelated memory returned for any shared word (W1a) |
| Memory answers without the web (`intent.covers`) | Yes: shared 5-letter stems | Yes | Yes | accepts 94 of 288 wrong-property facts | Answers the wrong property (W1b, reproduced) |
| Knowledge evolution: versions, hypotheses, predictions, calibration | Yes | Yes | Yes | — | The loops are open: calibration is written and never read; the research policy update is "advisory only: nothing reads it" (W7) |
| Controlled autonomy, curiosity | Yes, opt-in | Soak run | Yes | — | — |
| Worker queue | Yes | Yes | Yes | submit to claimed 2.7 s mean | Polls every 5 s; first in, first out regardless of who asked (R5a, W6) |
| Inference gateway | Yes | Yes | Yes | first token 180 to 720 ms | No priority; one provider; no cache or queue telemetry (R4) |

### Runtime (`resources/`, `voice/`, `pocket/`, `presence/`, `console/`)

| Capability | Built | Runs | Tested | Measured | Gap |
|---|---|---|---|---|---|
| Resource pressure level, audio priority, background model slots | Yes | Yes | Yes | research and voice latency with and without each other | Pocket voice was invisible to it (R2) |
| Idle unload and reload on demand | Yes | **No**: no unit or timer is installed on this host | Yes (fake host) | disposable container only | A reload ignored the caller's deadline and could block about 20 minutes on a crash loop; no free-memory check (R1) |
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
| C3 | C | Learned state expires: beliefs are computed from evidence with a 7-day lifetime, and nothing consolidates it. After a sweep on day 8 the posterior is 0.5 again and the executive says "observe" | Reproduced. Documented as deferred (MEM-10) |
| C4 | A | `forget_dataset` left the coactivation and lesion statistics derived from the dataset (`graph_effects`), and the table grew without bound | Code: no `DELETE FROM graph_effects` existed |
| C5 | A | Two processes opening the file during an upgrade re-ran a migration ("duplicate column name") and left a connection inside a transaction | Reproduced by interleaving |
| C6 | B | The promoted graph is the dense fit; the edge-exchange sparse pass costs four times the fit (16.4 s against 4.1 s at n = 1024) and its graph is used only in metrics | Measured. The requirements matrix said "verified" for edge exchange |
| C7 | C | The ask budget of an epoch counts every question ever asked there, so a long-lived epoch stops asking | Code and a test that asserts it |
| C8 | B | `lesion` recomputed the full error once per edge (16 s at n = 128) although the closed form was already computed | Measured |
| C9 | A | A dataset shape too large for one SQLite blob surfaced as a raw SQLite error | Arithmetic |

### Research, memory, reasoning

| # | Sev | Finding | Evidence |
|---|---|---|---|
| W1a | C | Memory retrieval is lexical: paraphrase recall@1 0.27, unnamed subject 6 of 12, unrelated memory for 10 of 10 questions memory cannot answer | `accel_baseline_memory.json` |
| W1b | A | Memory answered a question about another property: "encryption in transit?" from "no encryption at rest"; "tallest in the world?" from "tallest in the region" | Reproduced. On the benchmark the lexical test accepts 94 of 288 wrong-property facts |
| W2 | A | Two statements of one fact in different words ("186 metres tall", "a structural height of 192 m") both became knowledge with no contest; a newer version worded differently did not supersede the older | Reproduced |
| W3 | B | The self-critique call is 42 % of a task's time, removes nothing by construction, and most of its marks are false alarms | `accel_baseline_pipeline.json`; `accel_after_pipeline.json` |
| W4 | A | A model that correctly wrote no sentence (the evidence does not answer the question) was reported as "every model sentence failed NeuroSciFi's citation and claim checks" | Reproduced on three benchmark questions |
| W5 | B | Pages are read strictly one at a time; live, reading is most of a task | Existing live evidence: 7 to 19 s for 3 to 12 sources |
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
