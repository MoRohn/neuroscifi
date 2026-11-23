# Cognitive gap analysis

Status: 2026-11-23. This page answers one question per faculty: what does NeuroSciFi actually do, where does it fall
short of what it says or needs, and what did this review change. Finding numbers refer to
[ARCHITECTURE_AUDIT.md](ARCHITECTURE_AUDIT.md) §4. A faster system is not counted as a more intelligent one: speed
and quality are kept apart throughout.

## The cycle, traced through the code

| Stage | What the code does | Verdict |
|---|---|---|
| Perception | The core takes bits the owner declares on three channels. Web research reads pages. Voice transcribes speech | The core has no live sensor path on the Spark; the sensory front end is a lab simulation |
| Context | Epochs the owner starts; a task's question; a voice session's last task | Works; context is per surface, not shared (§Distributed cognition) |
| Memory | Evidence journal (7-day lifetime); knowledge the owner accepted; lexical retrieval | Retrieval and the memory-answer decision were the weakest link (W1a, W1b); both improved |
| Prediction | A likelihood model of the next observation; predictions attached to knowledge items | The prediction's probability is the item's confidence, nothing learned (W7) |
| Reasoning | Exact Bayes in the core; verified evidence, a model-written answer and a deterministic guard in research | Sound and conservative. The guard is lexical by design |
| Decision | Seven fixed rules; the web or memory decision by word overlap | The memory decision answered the wrong property (W1b); fixed |
| Action | A recommendation in text; a research task | Nothing is actuated |
| Feedback | The owner's observations and answers; a task's outcome | Works where the owner is in the loop |
| Learning | Beliefs recomputed from evidence; graphs fitted on request | See below: most of what is called learning is recomputation or storage |

## Does it learn?

The four mechanisms must not be confused, so each is answered separately.

1. **Inference-time adaptation: yes, and it is the main mechanism.** Beliefs are the exact posterior over the
   evidence, recomputed on every read. It lasts as long as the evidence, which is 7 days by default. Reproduced:
   after the sweep on day 8 the posterior is 0.5 and the executive asks to observe again.
2. **Memory and retrieval improvement: only through the owner.** Knowledge enters when the owner accepts it.
   Retrieval did not improve with use. *Changed here:* retrieval now ranks by meaning when the memory models are
   configured, which is a better retrieval function, not learning.
3. **Policy and workflow refinement: minimal.** The executive's rules are fixed code; one prior counts the owner's
   answers. The research policy update is written and read by nothing (W7).
4. **Persistent weight learning: real and disconnected.** Graphs are fitted by a delta rule and promoted through a
   gate, but the only data the CLI and console can feed them is simulated, and nothing outside the core reads a
   promoted graph.

One closed loop exists: a prediction error above threshold makes the executive ask or defer, and the owner's
confirmation relearns that scope only. Without the owner no belief changes in response to an error.

**What would make it learn from experience** (not built in this review; each changes what the individual
remembers, which is the owner's decision):

- *Consolidation* (C3): fold expiring evidence into durable counts per epoch, channel and segment, so a belief
  outlives its raw events. The counts become derived memory that every `forget_*` must cascade into, and a schema
  migration. This is the single largest gap between the architecture brief and the running system.
- *Closing the calibration loop* (W7): feed the stored calibration back into prediction probabilities and into the
  thresholds of the provisional-knowledge rule, with the benchmark's verification accuracy as the regression gate.
- *Connecting graphs to behaviour* (C6): a real dataset source, and a consumer of the promoted graph.

## Reasoning and metacognition

| Faculty | Finding | Change |
|---|---|---|
| Answer correctness | The served model solves 15 of 16 exact-answer problems and calls the right tool with valid arguments 8 of 8 times (`accel_baseline_inference.json`). The guard keeps 94 sentences and rejects 24 on the benchmark | Unchanged; the model, its precision and the guard are untouched |
| Abstention | When the evidence does not answer the question the model writes no sentence and names the gap. That is the right behaviour, and it was reported as a failure of the checks (W4) | The report now says the model wrote no sentence, with `abstained: true` |
| Self-critique | A second model call that marks sentences. By construction it removes none. On the benchmark it marks about half of the kept sentences, and three quarters of its marks are on sentences that restate their cited claim word for word with the attribution the code appended: false alarms. It costs 42 % of a task's time (W3) | Measured and documented. The owner can now skip it for spoken answers, where its marks are never presented. Whether to keep it for shown answers is an owner decision ([ENGINEERING_PLAN.md](ENGINEERING_PLAN.md) §4) |
| Knowing what memory knows | The decision "memory answers this" counted shared word stems (W1b) | The answer model now confirms that a chosen statement states the answer to exactly what is asked. Measured: wrong-property statements accepted 94 of 288 → 1 of 288 |
| Contradiction | Found when the two statements share most of their key words. A contradiction in other words passes (W2) | Not changed. The embedding model now available could propose candidates for the existing comparison; the decision must stay with the value comparison. Next priority |
| Calibration | The core's reading error is a constant; the acceptance check is circular | Not changed; noted |
| Hallucination control | Every sentence is checked against its cited claims; model text never becomes knowledge | Unchanged, and re-verified by the regression suite |

## Context and memory

| Faculty | Before | After |
|---|---|---|
| Recall of a fact asked in other words | recall@1 0.27 | 0.98 with the embedding model and the reranker (held-out subjects 1.00) |
| Recall when the subject is not named | 6 of 12 found | 11 of 12 first, 12 of 12 with the embedding alone |
| A question memory cannot answer | unrelated memory returned 10 of 10 times | 3 of 10 |
| Long context | A fact planted at three depths in 8k and 24k tokens is recalled 3 of 3 | Unchanged (re-measured) |
| Provenance, timestamps, permissions | Kept on every item | Unchanged: semantic retrieval reorders and filters the same rows and writes nothing |
| KV cache against durable memory | The server's prefix cache reuses computation; memory is the individual's file | Kept apart. The cache is now observable per call (`cached_tokens`), and nothing reads it as memory |

Not improved: write-side matching (W2), temporal indexing, consolidation scheduling, salience. Retrieval speed was
never the problem (0.6 ms); the semantic path is slower (about 105 ms a question, cached within a task) and is
justified by quality alone.

## Perception and multimodality

Speech in and out works and is measured (existing evidence: 2.1 % word error rate on clean read speech). There is
no camera or microphone on the Spark and no handset has been tested, so sensor-to-action latency remains
unmeasured. No NVIDIA speech or vision model was adopted: no measured deficit calls for one, and each needs more
memory than the service it would replace. *Changed here:* reply ordering after an interruption, a lost audio chunk,
and the resource policy's blindness to Pocket voice (R2, R6, R7).

## Autonomy

Controlled, opt-in and budgeted; unchanged. Background research now yields to the owner at three levels: the
worker claims the owner's task first, the model server serves the owner's request first, and the audio policy sees
Pocket voice.

## Distributed cognition

The device network is a set of clients around one executing node, not a compute cluster. What moves between
devices: family-scoped research tasks, a voice session's last task and preferences, session ownership through a
signed lease, the microphone lease. What does not: goals, working context, a capability registry, an offline
outbox. None of NVIDIA's distributed inference software applies to phones and laptops, and nothing of that kind was
deployed to them. Gaps left open: an offline outbox in Pocket, resuming a spoken follow-up after a restart (R8),
and a per-device sync signal in Presence.
