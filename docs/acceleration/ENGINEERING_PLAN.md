# Engineering plan: what was changed, what was not, and what is the owner's to decide

Status: 2026-11-23, branch `nvidia-acceleration-review` (based on `9e4570a`, 0.7.0). Finding numbers refer to
[ARCHITECTURE_AUDIT.md](ARCHITECTURE_AUDIT.md) §4; numbers to [PERFORMANCE.md](PERFORMANCE.md).

Order of priority applied: impact × confidence × feasibility ÷ (complexity + risk + maintenance), under the
directive's hierarchy: correctness and identity first, then reasoning quality, memory, reliability and safety,
then latency. Two consequences run through this page: a change that could alter what the individual remembers or
contacts was either made stricter-only or left off by default, and no default was changed for speed alone when it
removes something the owner currently sees.

## 1. Done, on by default

Each row: the smallest change that fixes the finding, how it was accepted, how to undo it.

| # | Finding | Change | Accepted by | Rollback |
|---|---|---|---|---|
| 1 | C1 restore resurrects a forgotten epoch | A restore replays epoch forgets recorded in the deletion log, on copies older than the forget; the forget-event path no longer restores revision causes naming a forgotten event; a forget that removes derived rows marks archives stale even with no live evidence | Tests that failed first, for every derived table (`tests/instance/test_checkpoints.py`, `test_erasure.py`) | Revert the commit; no schema change |
| 2 | C2 the note survives `forget_epoch` | The note is cleared, also in a restored copy (the row stays: evidence and tombstones refer to the name) | Test | Revert |
| 3 | C4 `graph_effects` survive `forget_dataset`; the dataset's free-text note survived too (found while fixing C2) | Both deleted in the cascade and in a restore | Tests that failed first | Revert |
| 4 | C5 concurrent migration | Write lock first, then re-read the version, rollback on error | Deterministic two-connection test | Revert |
| 5 | C8, C9 | Closed-form lesion (equal to 1e-15 on a test graph); an oversized dataset is refused with a clear message | Tests | Revert |
| 6 | W4 an abstaining model reported as rejected | Truthful reason and `abstained: true`; the status value is unchanged | Test; 2 to 4 abstentions labelled on the benchmark | Revert |
| 7 | W1b memory answers the wrong property | Before memory answers without the web, the answer model confirms the chosen statements (`memory_gate.py`). It can only send a question to the web. Nothing is asked or recorded when no model is configured | 94 of 96 right, 1 of 288 wrong accepted; orchestrator tests with the reproduced wrong answer | `nsf web config --no-memory-check` |
| 8 | R4 the owner waits behind background prefill | vLLM priority scheduling and a 4096-token step; the gateway sends `priority` for autonomous tasks when `background_priority` is set (`nsf setup` sets 10 for a vLLM endpoint) | Two runs each: first token under load −35 %, no single-stream change | `NSF_VLLM_SCHEDULING=fcfs`, `NSF_VLLM_BATCHED_TOKENS=` in `~/.config/neuroscifi/inference/vllm.env`, restart; `nsf llm config --set background_priority=0` |
| 9 | R5a a queued question waits for a poll | The worker wakes when another connection commits a queue change; the 5 s poll stays as the fallback | 2.73 s → 0.05 s mean | Revert |
| 10 | W6 queue order | Owner-asked tasks are claimed first, oldest first within a class; an autonomous task queued over 10 minutes competes by age again | Tests | Revert |
| 11 | R2 Pocket voice invisible to the resource policy | Pocket publishes the audio beacon; console and Pocket merge their entries in one file; the audio check covers every session | Tests | Revert |
| 12 | R6, R7 voice ordering and a lost chunk | A reply generation per turn: an older reply is shown, not spoken. The hub accepts a repeat of the last chunk once; the client retries once | Tests; the Node checks | Revert |
| 13 | R1 reload ignores the deadline; no memory check | A reload waits no longer than the caller may and continues in the background; a reload that would leave less than the critical threshold free is refused and recorded; `run.sh wait` fails at once on a crash loop | Tests that failed first | Revert |
| 14 | Missing telemetry | `nsf resources` and `GET /api/resources`: GPU temperature, power, throttling, load, the model server's queue, prefill, decode, first-token and prefix-cache state (lifetime and since the last reading). The ledger: provider, priority, cached tokens; an index. `nsf llm status`: cache share and calls by purpose | Tests; read against the live server | Revert; the ledger's added columns are ignored by older code |

## 2. Done, off until the owner turns it on

| # | Finding | Change | Why off | Turn on |
|---|---|---|---|---|
| 15 | W1a lexical memory retrieval | Semantic retrieval: an embedding model's candidates, a reranker's order and floor (`semantic.py`; `deploy/memory/`) | It needs two services and 8.7 GiB of memory, and makes a lookup 107 ms. Paraphrase recall@1 0.27 → 0.98 | `deploy/memory/run.sh start`, then `nsf web memory config --enable --embed-url http://127.0.0.1:18441/v1 --embed-model qwen3-embedding-0.6b --rerank-url http://127.0.0.1:18442/v1 --rerank-model qwen3-reranker-0.6b --key-file ~/.config/neuroscifi/memory/api_key` |
| 16 | W5 pages read one at a time | Read-ahead: the next pages are fetched ahead, one per site, and read, charged and reported in the original order | §4, decision B | `nsf web config --set read_ahead=4` |
| 17 | W3 the critique's cost for spoken answers | `critique_voice`: the critique pass can be skipped for spoken answers | It removes marks from a stored report | `nsf llm config --no-critique-voice` |

## 3. Rejected on measurement

Tried, measured, not adopted. Recorded so that they are not tried again without a new reason.

| Idea | Result | Evidence |
|---|---|---|
| `--performance-mode interactivity` | No change in decode or loaded latency; 25 s slower to start | `accel_vllm_flags.json` |
| A step of 8192 tokens | No better than 4096; doubles the longest step a new request waits behind | same |
| Speculative decoding by prompt lookup | Restating decode +20 %, free-form decode halved, research task mean 4.4 → 5.6 s | `accel_rejected_speculative_*.json` |
| Fusing lexical and semantic ranks | Paraphrase recall@1 0.48 against 0.94 for the semantic order alone | `accel_after_runtime.json` |
| A reranker as the judge of "memory answers this" | Accepts 11 to 60 of 288 wrong-property facts and both reproduced wrong answers | same |
| Requiring the model to write at least one sentence (suggested by the audit for W4) | Not built: the empty answers are correct abstentions on questions the evidence does not answer | `accel_baseline_pipeline.json`, tasks T04, T08, F25 |
| NIM, Triton, Dynamo, TensorRT-LLM for the answer model | [NVIDIA_ASSESSMENT.md](NVIDIA_ASSESSMENT.md) | — |

## 4. Decisions for the owner

Each is ready to act on; none is needed for the system to work.

**A. Turn the self-critique pass off?** It is 42 % of a research task's time. By construction it removes no
sentence. On the benchmark it marks about half of the kept sentences, and 35 of 46 marks sit on sentences that
restate their cited claim word for word with the attribution the code appended. Recommendation: off
(`nsf llm config --no-self-critique`), or at least off for voice (`--no-critique-voice`). Not done for you because
the marks and the model-named gaps are something the console shows today. Reversible at any time.

**B. Turn read-ahead on?** Reading is most of a live task; with 300 ms per response it halves task time
(2.10 → 0.99 s) and every benchmark outcome is identical. Two things change at the edges: after a cancel, after
web access is turned off or after a domain is denied, up to `read_ahead − 1` requests that were already sent still
reach their sites (nothing from them is kept); and unused requests (4 in 51 tasks) are not counted in the daily
caps. Recommendation: on at 4 for an owner who accepts that; the default stays 1 because "denied takes effect at
once" is a stated property of the security model.

**C. Run the memory models?** Recommendation: yes if 8.7 GiB can be spared; the embedding model alone (4.3 GiB)
gives the recall gain without the floor that drops unrelated results. An NVIDIA embedding NIM can be used in their
place by pointing the same setting at it; it needs your acceptance of NVIDIA's licence terms, which this review did
not give on your behalf.

**D. Consolidation of expiring evidence (C3).** The largest gap between the design and the running system: what the
individual learned is gone after 7 days unless each event is promoted by hand. A design is sketched in
[COGNITIVE_GAP_ANALYSIS.md](COGNITIVE_GAP_ANALYSIS.md). It changes what is remembered and how forgetting cascades,
and it needs a schema migration, so it was not built without you.

**E. The ask budget (C7)** never refills in a long-lived epoch; a test asserts the current behaviour. Counting only
open or owner-answered questions, or a time window, is a small change once you choose the rule.

**F. The sparse graph (C6).** The promoted graph is the dense fit and the sparse learner's result is discarded at
four times the fit cost. Either promote the sparse graph or make the pass optional; the requirements matrix now
says which it is.

## 5. Next engineering priorities

In order, with the reason each was not done in this pass.

1. **Contradictions in other words (W2).** Use the embedding model to propose candidates to
   `consolidate._existing`; keep the decision in the existing value comparison. Needs its own benchmark of
   paraphrased contradictions and a held-out set before any threshold is trusted: a false merge would inflate the
   count of independent sources.
2. **Close the calibration loop (W7)**, with the offline benchmark's verification accuracy as the gate.
3. **Idle management of the memory models** (they are not in the idle policy's service list), and a lighter server
   for them: two vLLM processes for two 0.6B models is where the 8.7 GiB goes.
4. **Count unused read-ahead requests in the daily caps** (the sum lives in the scheduler), then reconsider its
   default.
5. **A TensorRT-LLM comparison** on this model with the harness of this review
   ([NVIDIA_ASSESSMENT.md](NVIDIA_ASSESSMENT.md) §5).
6. **Voice continuity across a restart (R8)** and an **offline outbox in Pocket**.
7. **`knowledge.query` at scale (W8):** it rebuilds its lexical index per call (393 ms at 20k statements).
8. **Tombstones and the deletion log keep epoch names and event ids in clear**; a restore needs them to find what
   to delete, so storing digests means a migration. The restore's replay of epoch forgets relies on those rows: if
   the deletion log is ever pruned, the replay must move to a table that is not.
9. **A call that triggers an idle reload** can take up to twice its timeout (never the old 20 minutes).
