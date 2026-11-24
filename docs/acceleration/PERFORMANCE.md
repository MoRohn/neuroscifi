# Performance comparison: before and after

Status: 2026-11-23, DGX Spark GB10 (aarch64, 121.9 GiB unified memory). Before = commit `9e4570a` with the 0.7.0
serving flags. After = branch `nvidia-acceleration-review`. Same host, same model and precision
(Qwen3-30B-A3B-Instruct-2507 FP8 on the same pinned vLLM image), same prompts and seeds, server warm in both.
Unrelated containers of the host's other tenants were resident throughout (about 30 GiB of memory available);
they are listed in each evidence file and were not controlled.

§1 to §5 are phase 1 of the review. §7 holds what was measured after the owner's decisions were built (phase 2),
under different conditions that it states; §1 and §6 carry both.

**How to read the numbers.** Latency scenarios run 8 calls each; the pipeline benchmark runs 51 tasks. The shipped
configuration and the adopted one were each measured twice: the two runs of each agree within about 2 % on every
loaded metric (`accel_vllm_flags.json`), so a difference under 5 % on this page is reported as "no change". Quality
counts on the pipeline (sentences kept) move by a few between identical runs because the answer is sampled at
temperature 0.1; they are reported as ranges where that matters. Reproduce with `scripts/bench_inference.py`,
`scripts/bench_pipeline.py` and `scripts/bench_memory.py`; every file below is in `docs/quality/evidence/`.

## 1. Summary

| Dimension | Before | After | Verdict |
|---|---|---|---|
| Reasoning correctness (16 exact-answer problems) | 15 | 15 | No change, as intended: same model |
| Tool selection and arguments (8 requests) | 8 | 8 | No change |
| Long-context recall (a fact at 3 depths, 8k and 24k tokens) | 3/3, 3/3 | 3/3, 3/3 | No change |
| Single-stream decode | 49.8 tokens/s | 49.4 tokens/s | No change: the GPU is saturated by one stream |
| First token, evidence-sized prompt (3.8k tokens) | 719 ms | 604 ms | **−16 %** (550 ms in the two flag runs) |
| First token, 24k-token prompt | 7.63 s | 6.87 s | **−10 %** |
| Throughput, 8 concurrent | 63.6 tokens/s | 71.6 tokens/s | **+13 %** |
| Owner's first token beside 4 background requests | 1.72 s | 1.12 s | **−35 %** |
| Owner's whole short reply beside 4 background requests | 2.86 s | 2.26 s | **−21 %** |
| Queued question: submit to worker claims it | 2.73 s mean, 4.8 s worst | 0.05 s mean, 0.1 s worst | **−98 %** |
| Research task, median (model calls included), settings of phase 1 | 3.10 s | 3.27 s | No change (§3) |
| Research task, median, self-critique off (owner's choice) | — | 1.37 s | −56 % against the default; see the caveat in §3 |
| Research task after phase 2: the critique reads only the model's own sentences | mean 4.16 s, median 2.91 s (every sentence judged, same session) | mean 2.65 and 2.70 s, median 1.87 and 1.81 s | **−35 %** on the mean; final code, quiet host (§7.1) |
| Self-critique marks on a sentence that is a cited claim word for word | 45 of 49 | 0 of 5 and 0 of 4 | **Removed** (§7.1) |
| Reading pages, 300 ms per response, task p50 | 2.05 s | 0.96 s | **−53 %**, outcomes identical; final code, quiet host (§7.2) |
| Requests reaching sites after a cancel / web access off / a deny | 1 / 11 / 11 | 0 / 0 / 0 | **Fixed** (§7.2) |
| Belief after the evidence lifetime | back to the prior (0.5) | the posterior of the day before, exactly | **Fixed** (§7.3) |
| First graph of a context with 160 to 256 populations | a snapshot no better than no graph, promoted | rejected under `gate-v1`; a working graph for new individuals | **Fixed** (§7.4) |
| Memory: paraphrased question finds its fact first | 27 % | 98 % | **+71 points** (with the memory models) |
| Memory: unrelated memory returned for a question memory cannot answer | 10 of 10 | 3 of 10 | **Improved** |
| Memory answers from a wrong-property fact (decision test, 288 cases) | 94 | 1 | **Fixed** (model check; it also refuses 10 of 96 right facts, which then go to the web) |
| Memory lookup time | 0.7 ms | 107 ms with the memory models | **Slower** (§4): quality bought with latency |
| Unified memory | — | unchanged by default; +8.7 GiB when the optional memory models run (releasable when idle since phase 2, §7.5) | **Cost** when enabled |
| Regression suite | 3337 passed | see [REGRESSION_REPORT.md](REGRESSION_REPORT.md) | — |

Regressions and costs are listed in §6. Nothing on this page claims a gain in the model's own reasoning: the model
and its precision are unchanged, and the guard is the code of 0.7.0 with one added refusal (§7.1). The quality
gains are in memory retrieval, in the decision of when memory may answer, in what survives the evidence lifetime,
and in honest labelling.

## 2. Inference endpoint

`accel_baseline_inference.json` → `accel_after_inference.json`; flag experiments in `accel_vllm_flags.json`.

| Scenario | Metric | Before | After |
|---|---|---|---|
| Short reply | first token p50 | 180 ms | 141 ms (the first scenario of a run has a warm-up effect: the baseline's repeat measured 142 ms) |
| | decode | 49.8 tok/s | 49.4 tok/s |
| Evidence prompt, fresh | first token p50 / total p50 | 719 ms / 3.12 s | 604 ms / 3.10 s |
| Same prompt again (prefix cache) | first token p50 | 202 ms | 250 ms |
| Structured (JSON schema) | total p50; valid | 4.78 s; 8/8 | 4.67 s; 8/8 |
| 8k-token context | first token p50 | 1.77 s | 1.53 s |
| 24k-token context | first token p50 | 7.63 s | 6.87 s |
| Concurrency 1 / 4 / 8 | aggregate tok/s | 31.3 / 47.9 / 63.6 | 33.1 / 50.3 / 71.6 |
| Concurrency 8 | first token p95 | 5.15 s | 4.10 s |
| Foreground alone | first token p50 | 177 ms | 184 ms |
| Foreground beside 4 background | first token p50 / p95 | 1.72 s / 1.74 s | 1.12 s / 1.15 s |
| | total p50 | 2.86 s | 2.26 s |
| GPU | utilisation p50; power p50 in the mixed scenario; peak temperature | 96 %; 46 W; 77 °C | 96 %; 47 W; 80 °C |

What produced the change: `--scheduling-policy priority` with background requests carrying `priority: 10`, and
`--max-num-batched-tokens 4096`. What was tried and measured as no better or worse, and not adopted:
`--performance-mode interactivity` (no change, slower start), a step of 8192 (no better than 4096), and
prompt-lookup speculative decoding (restating decode +20 %, free-form decode halved, research task mean 4.4 → 5.6 s;
`accel_rejected_speculative_*.json`).

The peak temperature in the long-context scenario was 3 °C higher in the after run; no throttling was reported in
either. A bigger step draws more power while it runs (93 W peak in both).

## 3. Research task with the live model

`accel_baseline_pipeline.json` → `accel_after_pipeline.json`, `accel_after_pipeline_no_critique.json`. The offline
corpus through the real orchestrator; the reasoning step calls the model.

| | Before | After (defaults) | After, self-critique off |
|---|---|---|---|
| Tasks completed | 51 of 51 | 51 of 51 | 51 of 51 |
| Model-written answers / template | 46 / 5 | 46 / 5 | 44 / 7 |
| Sentences kept / rejected by the guard | 94 / 24 | 90 / 26 | 86 / 22 |
| Task wall time p50 / p95 / mean | 3.10 / 8.41 / 4.41 s | 3.27 / 8.63 / 4.45 s | 1.37 / 4.90 / 2.36 s |
| Share of wall time: answer / critique / NeuroSciFi's own code | 53 % / 42 % / 5 % | 53 % / 43 % / 4 % | 100 % answer |
| Self-critique: sentences removed / marked | 0 / 41 | 0 / 46 | — |
| Marks on a sentence that restates its cited claims | not recorded | 35 of 46 by the content-word test of phase 1; 42 of 46 by the exact test of phase 2 (§7.1) | — |
| Abstentions labelled as such | 0 (3 mislabelled) | 2 | 4 |

- **The default task is not faster.** A typical answer prompt is 1.1k tokens and runs alone, so neither engine flag
  touches it. The gains of §2 are for long prompts, concurrent tasks and the owner under background load.
- **The self-critique pass is 42 % of a task and removes nothing.** It cannot: a mark removes a sentence only if the
  deterministic check that already accepted the sentence now rejects it. Of its 46 marks, 35 sit on sentences whose
  every content word is in the claim they cite and whose attribution was appended by code. At the end of phase 1
  it stayed on by default as the owner's feature, with switches to turn it off (`nsf llm config
  --no-self-critique`) or off for spoken answers only (`--no-critique-voice`). Phase 2 narrowed what it reads
  instead (§7.1), and its exact comparison counts 42 of the 46.
- **Caveat on the critique-off run.** It ran directly after the default run on the same questions, so its answer
  prompts were almost entirely in the server's prefix cache (97 % against 54 %): its first token was 168 ms against
  281 ms. About 0.1 s of its 1.9 s advantage is that cache. The 42 % share measured in the baseline is the better
  estimate of what turning the critique off saves.
- The kept-sentence counts (94, 90, 86) and the two extra template answers of the last run are sampling variation of
  the answer call at temperature 0.1, with the guard unchanged; the critique removed nothing in any run.
- **Prefix cache, now visible per call:** the answer call takes 54 % of its prompt tokens from the cache (its
  constant system prompt); the critique call 8 %, because it has another system prompt.

## 4. Memory

`accel_after_memory_lexical.json`, `accel_after_memory_embedding.json`, `accel_after_memory_semantic.json`: 130
queries over 48 facts through `knowledge.query`. The lexical row is the 0.7.0 behaviour, which is still the
default.

| | Lexical (default) | Embedding | Embedding + reranker |
|---|---|---|---|
| Fact's own words (`near`), recall@1 | 0.92 | 0.98 | 1.00 |
| Other words (`para`), recall@1 | 0.27 | 0.94 | 0.98 |
| — held-out subjects only | 0.25 | 0.92 | 1.00 |
| Subject not named (`noname`), recall@1 | 0.50 | 1.00 | 0.92 |
| All answerable, recall@3 | 0.82 | 1.00 | 0.99 |
| Subject's name alone: all four facts on top | 1.00 | 0.92 | 1.00 |
| Nothing in memory: something returned anyway | 10 of 10 | 10 of 10 | 3 of 10 |
| Lookup p50 | 0.7 ms | 41 ms | 107 ms |
| First lookup of a process (embeds 48 statements) | 28 ms | 125 ms | 318 ms |

- The reranker's floor (0.02) drops one unnamed-subject answer that the embedding alone finds, and removes seven of
  ten unrelated results. The three that remain are near-misses in meaning ("How does Bazel cache build outputs?"
  against a fact about another build tool's cache).
- The slower lookup is paid once per question inside a task (the result is cached in the process); a task asks
  memory up to three times.
- Fusing the lexical and semantic orders (reciprocal rank fusion) measured **worse** than the semantic order alone
  (paraphrase recall@1 0.48) and is not done. The best lexical hits are only added to the reranker's candidates.
- The reranker needs its own prompt template: without it recall@1 was 0.25 to 0.79 (`accel_after_runtime.json`).

**The decision that memory may answer** (`accel_after_memory_answer_check.json`): each of 96 questions against the
four facts of its subject.

| | Right fact accepted | Wrong-property fact accepted |
|---|---|---|
| Lexical test (0.7.0) | 64 of 96 | 94 of 288 |
| Answer model's check, one call per statement | 86 of 96 | 1 of 288 |
| — an earlier design, all statements in one prompt (replaced after review) | 94 of 96 | 1 of 288 |
| 0.6B reranker at threshold 0.9 | 93 of 96 | 11 of 288, and both reproduced wrong answers |

In use a statement must pass both the lexical test and the model's check, so the check can only send a question to
the web. It costs about 0.3 s per statement checked (1.1 s for four) on the path where memory would otherwise have
answered alone. The one-prompt design accepted more right facts, but one statement's text could sway the verdict on
the others; each statement is now judged in a call of its own, and the ten right facts it refuses go to web research.

## 5. Responsiveness outside the model

`accel_after_runtime.json`, `accel_after_read_ahead_latency.json`.

| | Before | After |
|---|---|---|
| Queued question to claimed by the worker (12 submissions) | mean 2.73 s, max 4.81 s | mean 0.05 s, max 0.10 s |
| Worker idle cost | 1 ms of CPU per 5 s | 10 ms per 5 s |
| Voice: first check of a followed research task | 1 s | 0.1 s, backing off to 1 s |
| Reading pages, 300 ms per response, task p50 / p95 (read-ahead off by default at the end of phase 1) | 2.10 / 3.80 s | 2.10 / 3.80 s at 1; 0.99 / 1.83 s with `read_ahead=4`, which phase 2 made the default (§7.2) |
| `lesion` on a 128-node graph | about 16 s | closed form (the same values to 1e-15) |

Read-ahead reads, charges and reports exactly what one-at-a-time reading does (every benchmark metric identical),
and sent 4 unused requests over 51 tasks. Phase 1 left it off because of what it changed at the edges; phase 2
removed those objections and turned it on ([ENGINEERING_PLAN.md](ENGINEERING_PLAN.md) §4 B; §7.2 for the stops,
the budget and the one stated exception).

## 6. Regressions and costs

| Item | Size | Why accepted |
|---|---|---|
| Memory lookup with the memory models | 0.7 ms → 107 ms | Optional; cached within a task; buys the recall of §4 |
| Unified memory with the memory models | +8.7 GiB as measured in phase 1 (4.3 GiB each); phase 2 measured 4.6 GiB per service loaded and used (1.9 device, 2.6 process). Not reduced by any setting tried | Optional; the host keeps about 31 GiB available, above the 18.3 GiB "elevated" threshold. Each can be run alone: the embedding alone gives the recall without the floor. With an idle timeout (off by default) the memory is released when unused: 7.9 to 9.4 GiB returned, and a lookup brings the models back in 20 to 29 s without waiting for them |
| One model call per chosen statement before memory answers alone | +0.3 s per statement on that path | It replaces a wrong answer with web research; `nsf web config --no-memory-check` restores 0.7.0 |
| Worker idle CPU | 1 → 10 ms per 5 s | The price of the wake-up check |
| Same prompt repeated: first token | 202 → 250 ms | Within the noise of an 8-call scenario; the cached path does one decode step either way. Listed because it moved in the wrong direction |
| Background research under priority scheduling | Waits behind the owner by design | The owner is waiting; background work is not. No preemption was observed |
| vLLM start | 45 to 58 s to healthy in these runs (0.7.0 measured 32 to 52 s) | Varies with what else the host is doing; not attributable to the new flags |
| **Phase 2.** Storage of consolidated readings | About 96 bytes per expired learning copy, with no cap (0.7.0: nothing kept). The tombstone an expired event leaves either way is the larger part | The price of beliefs that outlive their events; `nsf config --no-consolidation` restores 0.7.0 |
| Belief computation grows with an epoch's age | 24 ms per 100,000 readings with a third of them sampled; about 0.3 s per million readings that carry a sample id even when none was recorded twice; 0.79 s at a million readings each recorded twice (0.7.0: bounded by one lifetime of events) | Per reading it is cheaper than the raw path; the bound is what changed. A cap or a maintained list of copied samples is next work |
| Privacy of an expired event | A root's name and a sample id stay in the file until the reading is forgotten (0.7.0 scrubbed a root when its last event expired). An individual upgraded from 0.7.0 gets this unless the owner opts out first | The owner's decision D; every reading can be forgotten by position, partner, camera or epoch, at the byte level |
| Requests a task sends and does not use | 4 beside 350 on the benchmark; at most the task's request budget again in the worst case (22 to 23 under a budget of 20 without owner action, 32 with a deny mid-run) | The price of reading four pages at once; charged requests stay within the budget; tasks under a daily allowance do not read ahead |
| One stated difference from one-at-a-time reading | A site that answers "slow down" to a page fetched ahead is left alone at once; pages that lead onto it are missing from the report, which names the site and the reason | Politeness to the site ranks above identical reports |
| Sentences refused that 0.7.0 showed | A sentence ending in its own "according to <source>" followed by more words. 0 on the benchmark and in the live runs; 17 of 1,476 recorded inputs, all from adversarial corpora. A faithful "… according to a report by Reuters" is refused too | 0.7.0 showed such a sentence without the words after the attribution, which could drop an exception. Code cannot tell the two cases apart |
| The critic reads fewer sentences | 75 to 81 restated sentences of a run are no longer shown to it, also not as context for the gaps it names | Every mark it put on such a sentence concerned the code's own attribution; `--critique-restatements` restores the full reading |
| A gateway call to a local service | One read of the listening sockets per second at most (4.2 ms), 0.006 ms per call | It keeps a key and prompts from another account's process on a freed port |
| New individuals' graphs | At 24 populations the same 247 bytes, the sparse learner's weights in 64 % of worlds. A fit still costs 2.6 to 3.9 times a dense-only one | The better learner is packed; existing individuals are unchanged |

Not measured: energy per task (no meter; GPU power is sampled and unchanged), sensor-to-action latency (no capture
device), anything on a handset, and the live internet (no test contacted it). Of phase 2's times, the research
pipeline, the read-ahead latency and the memory benchmark were measured again on the final code on a quiet host
(§7.1, §7.2, §7.5); the others were taken under load and are marked so.

## 7. Phase 2: the owner's decisions, measured

The six decisions of [ENGINEERING_PLAN.md](ENGINEERING_PLAN.md) §4 were built after the sections above were
written. **Conditions:** these runs were taken while the other lanes' test and benchmark jobs shared the host
(load average 20 to 40 at times, and at times a second model on the GPU). Counts (calls, sentences, requests,
bytes, rows) do not depend on that; times do, and are indicative. Where a script reports CPU time or a minimum
over repeats instead of a wall-clock median, the table says so. Evidence files are named
`accel_decisions_*.json` and `accel_consolidation_cost.json`. Three measurements were repeated at the end, on the
final code with nothing else running (load average under 4 for the model runs, under 1 for the others):
`accel_decisions_pipeline_final.json`, `accel_decisions_final_read_ahead_latency.json` and
`accel_decisions_final_memory.json`; their rows say "quiet host".

### 7.1 What the self-critique reads (decision A)

`accel_decisions_critique_scope.json`: the pipeline benchmark of §3, 51 tasks, the live model.

| Run | Critique calls | Sentences judged / kept | Marks | Marks on a restatement | Removed | Task mean / median |
|---|---|---|---|---|---|---|
| Every sentence judged (the 0.7.0 scope, now `critique_restatements` on), two runs | 44, 44 | 86 / 86 | 42, 42 | 38, 38 | 0 | 4.13 / 3.03 s, 4.16 / 2.96 s |
| Default scope, first version of the test, two runs | 9, 11 | 10 / 91, 12 / 89 | 4, 4 | 0 | 0 | 2.80 / 1.77 s, 2.79 / 1.81 s |
| Default scope, character-for-character test, two runs | 10, 10 | 11 / 86 | 4 | 0 | 0 | mean 2.64 s |
| Default scope, final rule (a restatement is one claim), one run | 9 | 10 / 85 | 4 | 0 | 0 | mean 2.59 s |
| Self-critique off | 0 | — | — | — | — | 2.33 / 1.38 s |
| **Final code, quiet host:** default scope, two runs | 10, 10 | 11 / 86 | 5, 4 | 0 | 0 | 2.70 / 1.81 s, 2.65 / 1.87 s |
| **Final code, quiet host:** every sentence judged | 45 | 90 / 90 | 49 | 45 | 0 | 4.16 / 2.91 s |
| **Final code, quiet host:** self-critique off | 0 | — | — | — | — | 2.41 / 1.32 s |

- The critique's share of task time fell from 42.6 % to 10.3 %, its tokens from 40,626 to 6,425 a run (quiet
  host, final code: 42.1 % to 11.1 %). The answer call is the same request in every setting (59,755 prompt tokens
  in every run, the quiet-host ones included).
- **Correction to §3.** The stored default run of phase 1 (`accel_after_pipeline.json`), re-read with the final
  rule: 79 of its 90 kept sentences are one cited claim word for word with the code's attribution, and 42 of its
  46 marks sit on them. The "35 of 46" in §3 came from a looser test that compared content words; it missed one
  attribution wording and accepted shortened claims.
- Model-written answers were 44 to 46 of 51 across these runs (44 and 45 on the quiet host). The difference
  follows the run, not the setting:
  the answer is sampled at temperature 0.1, the critique runs after it and removed nothing in any run.
- What is still judged: sentences the model shortened, reordered or built from more than one claim. Of the 4
  marks, one reader took one to name a real shift of meaning (a company's own statement restated as fact) and
  three to be false alarms on shortened sentences. That is a reading of four marks, not a measured precision.
- The answer path is unchanged by this: a differential over 1,476 inputs recorded from `307b907` shows 1,459
  identical results and 17 that differ only by a sentence now refused ([REGRESSION_REPORT.md](REGRESSION_REPORT.md)
  §3); none of the 248 benchmark sentences among them changed, and the live runs refused none.

### 7.2 Reading pages (decision B)

`accel_decisions_final_read_ahead_latency.json` (final code, quiet host; the lane's own runs under load, in
`accel_decisions_read_ahead.json`, gave 1.93 to 2.08 s at 1 and 0.96 to 0.97 s at 4). Task time on the offline
corpus with every fixture response delayed 0.3 s (not comparable to live reading); outcomes of all 51 tasks
identical across windows:

| `read_ahead` | Task p50 | Task p95 | Requests used | Sent ahead and not used |
|---|---|---|---|---|
| 1 (0.7.0) | 2.05 s | 3.62 s | 350 | 0 |
| 2 | 1.31 s | 2.40 s | 350 | 2 |
| 3 | 1.08 s | 2.09 s | 350 | 4 |
| 4 (the default) | 0.96 s | 1.85 s | 350 | 4 |

Requests that reach the sites after the owner's stop was committed (every fetch held at its robots.txt answer,
the stop made on another connection, the answers then released):

| | Cancel | Web access off | Sites denied |
|---|---|---|---|
| `read_ahead` 1, before | 1 | 11 | 11 |
| `read_ahead` 4, before | 4 | 8 | 8 |
| Either, after | 0 | 0 | 0 |

The 11 is the one-at-a-time path of 0.7.0: the web settings were re-read at most every 2 s. A cancelled run now
ends about 0.25 s after the cancel (measured 0.01 to 0.24 s); before, it ended when its slowest request did (4.0 s
with a 4 s answer; up to the 15 s timeout).

Traffic against the task's budget, from the independent reviews' scenarios:

| Scenario | One page at a time | Read-ahead 4, first version | Read-ahead 4, now |
|---|---|---|---|
| Three fetches ahead, `max_requests` 4: requests reaching sites | 4 | 12 | 4 |
| Six redirecting candidates, `max_requests` 10 | 10 | 21 (11 unused) | 14 |
| Worst case found without owner action, `max_requests` 20 | 20 | 34 | 22 to 23 |
| The same with an unrelated site denied mid-run | 20 | 38 | 32 |
| Unused bytes under a byte budget of 1,281,313 | 0 | 2,562,673 | 427,061 |

Charged requests never exceed the budget, and neither do the unused ones; both together can reach exactly twice
the budget in a constructed case (24 requests under a budget of 12: the reader's first site is in back-off, the
fetches ahead take the whole budget, and a deny makes all of them unusable). On the benchmark the unused part is
4 requests beside 350.

### 7.3 Consolidated readings (decision D)

`accel_consolidation_cost.json`, `scripts/bench_consolidation.py`. One epoch, three channels, four roots each, a
sample id on a third of the events, one confirmed change. `compute()` is CPU time, minimum of 31 warm calls; the
sweep is one wall-clock call. Posteriors are identical before and after the sweep in every row.

| Learning readings in one epoch | `compute()`, all raw | `compute()`, all consolidated | Sweep that consolidates them | Bytes per reading, with indexes |
|---|---|---|---|---|
| 200 | 2.06 ms | 1.92 ms | 4.5 ms | (one page) |
| 10,000 | 6.72 ms | 4.05 ms | 85 ms | 93 |
| 100,000 | 55.17 ms | 23.99 ms | 930 ms | 96.5 |

- An epoch with nothing consolidated pays 2 to 3 % more than at `307b907` (1.427 → 1.455 ms at 50 events, 5.044 →
  5.193 ms at 10,000).
- Per reading the consolidated path is cheaper than the raw one it replaces. What changed is the bound: the
  journal held one lifetime of events, readings accumulate for the life of the epoch.
- The first version kept one reading per sample (12 ms and 77 bytes at 100,000). It made forgetting inexact and
  was replaced; the difference is the price of exact forgetting.

Epochs whose readings all carry a sample id (`copies` mode; one wall-clock call, milliseconds; before → after the
second review pass replaced one query per copied sample by passes over one index):

| Readings, all sampled | Each sample recorded | `compute()` | Readings of one root since a question | `explain()` | Forget one reading |
|---|---|---|---|---|---|
| 100,000 | once | 57 → 57 | 34 → 1 | 156 → 121 | 58 → 57 |
| 100,000 | twice | 308 → 97 | 235 → 0 | 818 → 200 | 271 → 95 |
| 1,000,000 | once | 502 → 454 | 381 → 5 | 1484 → 922 | 538 → 456 |
| 1,000,000 | one in ten twice | 1477 → 744 | 1124 → 5 | 3927 → 1496 | 1381 → 744 |
| 1,000,000 | twice | 5370 → 791 | 4238 → 4 | 14595 → 1587 | 5148 → 793 |

The independent review's own cases at a million readings and 200 roots: a sweep that expires one event per root
5.31 s → 0.08 s, the list of held roots that every web query reads 51 ms → 0.2 ms, a forget of an unknown id
195 ms → 0 ms (an index by root, a keyed tombstone lookup).

### 7.4 The graph learner and what is promoted (decision F)

`accel_decisions_graph_learner_time.json`: fit time, minimum of 9, 1536 steps unless noted. All sizes are
bit-identical to the lab's loop.

| Populations | Before | Now | Speed-up | The exchange's share of a fit, before → now | Now, as a multiple of a dense-only fit |
|---|---|---|---|---|---|
| 24 | 2.0 ms | 0.9 ms | 2.4× | 66 % → 19 % | 2.6 |
| 256 | 36.8 ms | 21.2 ms | 1.7× | 49 % → 12 % | 2.6 |
| 1024 | 0.97 s | 0.83 s | 1.2× | 20 % → 6 % | 3.9 |
| 1024, 8192 steps | 2.55 s | 1.72 s | 1.5× | 29 % → about 0 | 2.6 |

The 24 and 256 rows are steady (median within 6 % of the minimum). The 1024 rows are not (medians two to eight
times the minimum under the load named above): **indicative only**.

`accel_decisions_graph_learner_sizes.json`: the product path (`datasets.add`, `graphs.learn`, `evaluate`,
`promote` on a new individual), validation error of the candidate and what the gate did, three worlds per size
(one at 512 and 1024). A graph with no edges scores about 0.018 at every size.

| Populations | Born under `gate-v1` alone | Born under the birth rules (`best-learner-v1`) |
|---|---|---|
| 24 | 0.0043, promoted | 0.0043, promoted (247 bytes either way) |
| 48, 64 | 0.0043, 0.0044, promoted | 0.0043, sparse learner's graph, promoted |
| 96, 128 | 0.0085, promoted | 0.0044, 0.0043, promoted |
| 160 | 0.0192, rejected: no better than no graph (before phase 2: promoted) | 0.0044, promoted |
| 192 | 0.27, rejected (before: promoted) | 0.0043, promoted |
| 256 | 255, rejected (before: promoted) | 0.0043, promoted |
| 512, 1024 | rejected: byte budget and no better than no graph | 0.0043, rejected: byte budget only |

Selection on the lab's own worlds (`accel_decisions_graph_learner_select.json`, 64 worlds): at 24 populations the
birth rule chooses the sparse learner's weights in 64 % of worlds at the same 247 bytes, held-out error 0.004313
against 0.004321. Acceptance scenarios under both births: all pass, no check changes
(`accel_decisions_graph_birth_rule.json`).

### 7.5 The memory models as a service (decision C)

`accel_decisions_memory_service.json`: the real containers and a temporary individual on this host; ranges are
across four runs of the same script.

| What | Result |
|---|---|
| `nsf setup` on a new individual, both services up | step `memory` done; a second run reports "already done" and probes nothing |
| A paraphrased question | semantic, the right fact first; 111 to 175 ms |
| Retrieval benchmark with the committed settings (130 queries) | paraphrase recall@1 0.979, lookup p50 111 ms: equal to §4. Again on the final code, quiet host (`accel_decisions_final_memory.json`): every class equal to §4 (paraphrase 0.979, unnamed subject 0.917, 3 of 10 unanswerable return something), lookup p50 113 ms, p95 168 ms; without the services, the lexical figures of §4 (paraphrase 0.271, 10 of 10) |
| The reranker stopped by hand | the next lookup is lexical in 30 to 40 ms with the reason; the one after it 0.9 to 1.0 ms, nothing tried for 30 s |
| First request to a model after a start | 1539 ms (embedding), 1051 ms (reranker) when the wait checked health only; 32 and 39 ms now that the wait sends one real request |
| Start to ready | 18 to 31 s with warm files |
| Idle pass after a 1-minute timeout | both unloaded in 1.0 to 1.8 s each; available memory 30.0 to 30.4 GiB → 38.3 to 39.3 GiB |
| A lookup while both are unloaded | lexical with `waking`, 1.9 to 3.3 ms; a one-shot command 0.15 to 0.18 s in all |
| Both models answering again | 20.2 s after that lookup (16.4 to 28.7 s per reload across runs) |
| First lookup after the reload | semantic, 65 to 74 ms (1862 ms before the warm-up request) |
| A reload asked for by a wake request (transient user units) | the lookup returns in 0.4 ms; the model answers again after 36.0 s |
| Reloading through a unit: the wait | before, exit after 130 ms with "container is gone"; now "ready after 35s" |
| A stuck entry in the request folder | before, 29 starts and the unit's start limit; now 1 start, the entry set aside, the next request served |
| Footprint per service, loaded and used | 4.55 to 4.57 GiB: 2.6 GiB of process memory and 1.93 GiB of device memory (weights 1.12). **Not reduced**: no setting of the pinned image lowers it |

Unverified: the units as installed system units, the installer run as root, the idle timer, a second account.

### 7.6 The listener check

Reading who owns a listening port costs one parse of `/proc/net/tcp` and `tcp6`: 4.2 ms on this host (421 socket
lines). The first version parsed on every request, about four times per memory lookup. Now one parse serves a
second of checks: 0.006 ms per check.
