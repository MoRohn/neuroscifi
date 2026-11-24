# Cognitive gap analysis

Status: 2026-11-24. This page answers one question per faculty: what does NeuroSciFi actually do, where does it fall
short of what it says or needs, and what did this review change. Finding numbers refer to
[ARCHITECTURE_AUDIT.md](ARCHITECTURE_AUDIT.md) §4. A faster system is not counted as a more intelligent one: speed
and quality are kept apart throughout. "Phase 2" marks what changed after the owner delegated the open decisions
([ENGINEERING_PLAN.md](ENGINEERING_PLAN.md) §4); "Phase 3" what changed when the open items were acted on
(2026-11-24, the plan's §5).

## The cycle, traced through the code

| Stage | What the code does | Verdict |
|---|---|---|
| Perception | The core takes bits the owner declares on three channels. Web research reads pages. Voice transcribes speech | The core has no live sensor path on the Spark; the sensory front end is a lab simulation |
| Context | Epochs the owner starts; a task's question; a voice session's last task | Works; context is per surface, not shared (§Distributed cognition) |
| Memory | Evidence journal (7-day lifetime); knowledge the owner accepted; lexical retrieval | Retrieval and the memory-answer decision were the weakest link (W1a, W1b); both improved. Phase 2: what an expired learning event taught is kept (C3) |
| Prediction | A likelihood model of the next observation; predictions attached to knowledge items | The prediction's probability is still the item's confidence when it is stored. Phase 3 (ADR-046): it is read through how the settled forecasts of its kind fared where autonomous research is ranked; the identity below 20 settlements (W7) |
| Reasoning | Exact Bayes in the core; verified evidence, a model-written answer and a deterministic guard in research | Sound and conservative. The guard is lexical by design |
| Decision | Seven fixed rules; the web or memory decision by word overlap | The memory decision answered the wrong property (W1b); fixed |
| Action | A recommendation in text; a research task | Nothing is actuated |
| Feedback | The owner's observations and answers; a task's outcome | Works where the owner is in the loop |
| Learning | Beliefs recomputed from evidence; graphs fitted on request | See below: most of what is called learning is recomputation or storage. Phase 2 made the storage durable; it added no new learning mechanism |

## Does it learn?

The four mechanisms must not be confused, so each is answered separately.

1. **Inference-time adaptation: yes, and it is the main mechanism.** Beliefs are the exact posterior over the
   evidence, recomputed on every read. At `9e4570a` it lasted as long as the evidence, 7 days by default.
   Reproduced there: after the sweep on day 8 the posterior is 0.5 and the executive asks to observe again.
   *Changed in phase 2:* an expired learning event leaves a consolidated reading, and beliefs read events and
   readings as one set, so the posterior on day 8 is the number it was on day 7 (tested with `==`). The mechanism
   is the same recomputation over a set that no longer shrinks.
2. **Memory and retrieval improvement: only through the owner.** Knowledge enters when the owner accepts it.
   Retrieval did not improve with use. *Changed here:* retrieval now ranks by meaning when the memory models are
   configured, which is a better retrieval function, not learning.
3. **Policy and workflow refinement: minimal.** The executive's rules are fixed code; one prior counts the owner's
   answers. The research policy update is recorded for the owner's inspection and read by nothing else, by
   documented decision (W7).
4. **Persistent weight learning: real and disconnected.** Graphs are fitted by a delta rule and promoted through a
   gate, but the only data the CLI and console can feed them is simulated, and nothing outside the core reads a
   promoted graph. *Changed in phase 2:* which graph is promoted. The dense fit that was packed stops fitting
   beyond about 100 populations (C10); new individuals pack whichever of the two learners fits better, and the
   gate refuses a snapshot that is no better than no graph. The learners themselves and the disconnection are
   unchanged.

One closed loop exists: a prediction error above threshold makes the executive ask or defer, and the owner's
confirmation relearns that scope only. Without the owner no belief changes in response to an error.

**What would make it learn from experience** (each changes what the individual remembers, which is the owner's
decision):

- *Consolidation* (C3): **built in phase 2, as retention.** What it is: when a learning event's lifetime ends, a
  consolidated reading stays (its position in the journal, its epoch, channel and root, the corrected reading and
  its sample id), one per expiring copy, and every belief, change test and epoch match reads it. The first sketch
  (counts per epoch, channel and segment) was not built, because a count cannot be cut at a change confirmed
  later, cannot tell which sample it already holds, and cannot give back one event. What it is not: rehearsal,
  replay, or any reweighting of what was learned. Nothing is re-presented to a learner and no weight changes;
  MEM-10 of the requirements matrix stays deferred for that part. Every forget cascades into the readings, and a
  restore keeps the learning without returning anything forgotten (ADR-044). Its costs are growth without a cap
  and a root's name staying in the file until its reading is forgotten ([PERFORMANCE.md](PERFORMANCE.md) §6).
- *Closing the calibration loop* (W7): **closed in phase 3 where it changes no belief.** A forecast is read through
  how the settled forecasts of its kind fared: the identity until 20 have settled, held to the stated forecast by a
  prior, monotone, bounded, computed on read and removed by forgetting (ADR-046). That reading only ranks the
  autonomous investigations the stated forecasts admit; it never admits or refuses one, and stored forecasts,
  confidences and verification states are untouched. On held-out simulated forecasters the mean squared distance to
  the true probability falls from 0.044 to 0.017; for about 50 forecasts after a forecaster's rule reverses it reads
  worse than stated. The evidence is simulated: no individual has settled predictions yet. Examined and declined:
  feeding it into the thresholds of the provisional-knowledge rule, which are policy, and would change what counts
  as verified.
- *Connecting graphs to behaviour* (C6): a real dataset source, and a consumer of the promoted graph.

## Reasoning and metacognition

| Faculty | Finding | Change |
|---|---|---|
| Answer correctness | The served model solves 15 of 16 exact-answer problems and calls the right tool with valid arguments 8 of 8 times (`accel_baseline_inference.json`). The guard keeps 94 sentences and rejects 24 on the benchmark | Unchanged; the model, its precision and the guard are untouched |
| Abstention | When the evidence does not answer the question the model writes no sentence and names the gap. That is the right behaviour, and it was reported as a failure of the checks (W4) | The report now says the model wrote no sentence, with `abstained: true` |
| Self-critique | A second model call that marks sentences. By construction it removes none. On the benchmark it marks about half of the kept sentences, and 42 of its 46 marks are on sentences that are one cited claim character for character with the attribution the code appended (79 of the 90 kept sentences; the first count of 35 used a looser comparison of content words): false alarms about the code's own hedge. It cost 42 % of a task's time (W3) | The owner can skip it for spoken answers. Phase 2 (decision A): it reads only sentences in which the model wrote something of its own, and makes no call when there is none: 9 to 11 calls instead of 44 on 51 tasks, 4 marks instead of 42, none on a restatement. The 4 remaining marks are still a model's unconfirmed judgement; one reader took one of them to name a real shift of meaning |
| Knowing what memory knows | The decision "memory answers this" counted shared word stems (W1b) | The answer model now confirms that a chosen statement states the answer to exactly what is asked. Measured: wrong-property statements accepted 94 of 288 → 1 of 288 |
| Contradiction | Found when the two statements share most of their key words. A contradiction in other words passes (W2) | Phase 3: with semantic memory and an answer model configured, the embedding model proposes a pair, the reranker must agree both ways, the answer model confirms, and the existing value comparison decides. A contradiction is recorded and asked; it never supersedes or weakens knowledge. A same-value pair is reported, not merged: the held-out set had been used once to revise the design. Held out: 31 of 43 found, 0 of 23 false pairs; values in other units and aliases mostly missed |
| Calibration | The core's reading error is a constant; the acceptance check is circular | Not changed; noted |
| Hallucination control | Every sentence is checked against its cited claims; model text never becomes knowledge | Unchanged in phase 1. In phase 2 the guard was changed once, regressed, and was put back: the answer path is the code of `307b907`, held there by a differential test over 1,476 recorded inputs, plus one refusal (a sentence whose own attribution would take further words with it is not shown cut). The reviews also recorded gaps that are older than the branch, among them an inverted exception that passes; they are open (W12). Phase 3: those gaps closed one change at a time, each stricter than `307b907` apart from one proven loosening (a sentence that is exactly one cited claim), over 1,782 recorded inputs with 0 unexplained differences: a claim's exception or condition must survive, words keep the claims' case, characters a reader does not see or that reorder the text keep a claim out of an answer, a passive role swap is refused. Still open: an exception as a negated phrase, a bound of time, a group named as an advisory's author, roles swapped across clauses (W18) |
| Asking the owner | Three questions per epoch, ever: a long-lived epoch stopped asking (C7) | Phase 2 (decision E): the budget counts the last 7 days, answered or not, so interruptions stay bounded and a long-lived epoch asks again |

## Context and memory

| Faculty | Before | After |
|---|---|---|
| Recall of a fact asked in other words | recall@1 0.27 | 0.98 with the embedding model and the reranker (held-out subjects 1.00) |
| Recall when the subject is not named | 6 of 12 found | 11 of 12 first, 12 of 12 with the embedding alone |
| A question memory cannot answer | unrelated memory returned 10 of 10 times | 3 of 10 |
| Long context | A fact planted at three depths in 8k and 24k tokens is recalled 3 of 3 | Unchanged (re-measured) |
| Provenance, timestamps, permissions | Kept on every item | Unchanged: semantic retrieval reorders and filters the same rows and writes nothing |
| KV cache against durable memory | The server's prefix cache reuses computation; memory is the individual's file | Kept apart. The cache is now observable per call (`cached_tokens`), and nothing reads it as memory |
| How retrieval by meaning is run | Two hand-started containers and a typed configuration | Phase 2 (decision C): `nsf setup` switches it on when the two models answer; supervised units exist; the idle policy can release the models, and a lookup that finds them unloaded is answered lexically at once and wakes them. The retrieval function and its measured quality are unchanged (paraphrase recall@1 0.979 on the re-run) |
| What a belief rests on after the evidence lifetime | Nothing: the posterior returned to 0.5 | Phase 2 (decision D): consolidated readings; forgetting by event, position, partner, camera or epoch removes them exactly. Phase 3: under a limit of a million readings, with warnings from 80 % |
| Lookup cost as memory grows | The lexical index was rebuilt on every query: 200 ms at 20,000 statements (W8) | Phase 3: kept between queries and invalidated by value, 31 ms, identical results; every forget clears it |
| How retrieval by meaning is served | Two vLLM servers, 9.33 GiB | Phase 3: one process for both models, 4.26 GiB, the same ranking on the benchmark |

Not improved: temporal indexing, rehearsal or replay of what was consolidated, salience. Write-side matching (W2)
now finds contradictions in other words and asks; it does not merge restatements. Retrieval speed was never the
problem (0.6 ms); the semantic path is slower (about 105 ms a question,
cached within a task) and is justified by quality alone.

## Perception and multimodality

Speech in and out works and is measured (existing evidence: 2.1 % word error rate on clean read speech). There is
no camera or microphone on the Spark and no handset has been tested, so sensor-to-action latency remains
unmeasured. No NVIDIA speech or vision model was adopted: no measured deficit calls for one, and each needs more
memory than the service it would replace. *Changed here:* reply ordering after an interruption, a lost audio chunk,
and the resource policy's blindness to Pocket voice (R2, R6, R7).

## Autonomy

Controlled, opt-in and budgeted; unchanged. Background research now yields to the owner at three levels: the
worker claims the owner's task first, the model server serves the owner's request first, and the audio policy sees
Pocket voice. Phase 2 kept autonomous work out of read-ahead: curiosity, refresh and MCP tasks read one page at a
time, so their daily allowances stay exact, and the owner's stop (a cancel, web access off, a denied site) now
holds before every request of any task; phase 3 extended it to searches, records read through a provider's API and
a render's sub-requests. Phase 3 also ranks autonomous investigations by forecasts as read through their settled
history, without changing which are admitted, and stops an ignored web question from blocking new ones after 30
days.

## Distributed cognition

The device network is a set of clients around one executing node, not a compute cluster. What moves between
devices: family-scoped research tasks, a voice session's last task and preferences, session ownership through a
signed lease, the microphone lease. What does not: goals, working context, a capability registry, an offline
outbox. None of NVIDIA's distributed inference software applies to phones and laptops, and nothing of that kind was
deployed to them. Gaps left open: an offline outbox in Pocket, resuming a spoken follow-up after a restart (R8),
and a per-device sync signal in Presence.
