# Performance comparison: before and after

Status: 2026-11-23, DGX Spark GB10 (aarch64, 121.9 GiB unified memory). Before = commit `9e4570a` with the 0.7.0
serving flags. After = branch `nvidia-acceleration-review`. Same host, same model and precision
(Qwen3-30B-A3B-Instruct-2507 FP8 on the same pinned vLLM image), same prompts and seeds, server warm in both.
Unrelated containers of the host's other tenants were resident throughout (about 30 GiB of memory available);
they are listed in each evidence file and were not controlled.

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
| Research task, median (model calls included), default settings | 3.10 s | 3.27 s | No change (§3) |
| Research task, median, self-critique off (owner's choice) | — | 1.37 s | −56 % against the default; see the caveat in §3 |
| Memory: paraphrased question finds its fact first | 27 % | 98 % | **+71 points** (with the memory models) |
| Memory: unrelated memory returned for a question memory cannot answer | 10 of 10 | 3 of 10 | **Improved** |
| Memory answers from a wrong-property fact (decision test, 288 cases) | 94 | 1 | **Fixed** (model check; it also refuses 10 of 96 right facts, which then go to the web) |
| Memory lookup time | 0.7 ms | 107 ms with the memory models | **Slower** (§4): quality bought with latency |
| Unified memory | — | unchanged by default; +8.7 GiB when the optional memory models run | **Cost** when enabled |
| Regression suite | 3337 passed | see [REGRESSION_REPORT.md](REGRESSION_REPORT.md) | — |

Regressions and costs are listed in §6. Nothing on this page claims a gain in the model's own reasoning: the model,
its precision and the guard are unchanged. The quality gains are in memory retrieval, in the decision of when
memory may answer, and in honest labelling.

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
| Marks on a sentence that restates its cited claims word for word | not recorded | 35 of 46 | — |
| Abstentions labelled as such | 0 (3 mislabelled) | 2 | 4 |

- **The default task is not faster.** A typical answer prompt is 1.1k tokens and runs alone, so neither engine flag
  touches it. The gains of §2 are for long prompts, concurrent tasks and the owner under background load.
- **The self-critique pass is 42 % of a task and removes nothing.** It cannot: a mark removes a sentence only if the
  deterministic check that already accepted the sentence now rejects it. Of its 46 marks, 35 sit on sentences whose
  every content word is in the claim they cite and whose attribution was appended by code. It stays on by default
  because it is the owner's feature; the owner can turn it off (`nsf llm config --no-self-critique`) or off for
  spoken answers only (`--no-critique-voice`), where its marks were never presented.
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
| Reading pages, 300 ms per response, task p50 / p95 (read-ahead **off by default**) | 2.10 / 3.80 s | 2.10 / 3.80 s at the default; 0.99 / 1.83 s with `read_ahead=4` |
| `lesion` on a 128-node graph | about 16 s | closed form (the same values to 1e-15) |

Read-ahead reads, charges and reports exactly what one-at-a-time reading does (every benchmark metric identical),
and sent 4 unused requests over 51 tasks. It is off by default because of what it changes at the edges
([ENGINEERING_PLAN.md](ENGINEERING_PLAN.md) §4).

## 6. Regressions and costs

| Item | Size | Why accepted |
|---|---|---|
| Memory lookup with the memory models | 0.7 ms → 107 ms | Optional; cached within a task; buys the recall of §4 |
| Unified memory with the memory models | +8.7 GiB (4.3 GiB each) | Optional; the host keeps about 31 GiB available, above the 18.3 GiB "elevated" threshold. Each can be run alone: the embedding alone gives the recall without the floor |
| One model call per chosen statement before memory answers alone | +0.3 s per statement on that path | It replaces a wrong answer with web research; `nsf web config --no-memory-check` restores 0.7.0 |
| Worker idle CPU | 1 → 10 ms per 5 s | The price of the wake-up check |
| Same prompt repeated: first token | 202 → 250 ms | Within the noise of an 8-call scenario; the cached path does one decode step either way. Listed because it moved in the wrong direction |
| Background research under priority scheduling | Waits behind the owner by design | The owner is waiting; background work is not. No preemption was observed |
| vLLM start | 45 to 58 s to healthy in these runs (0.7.0 measured 32 to 52 s) | Varies with what else the host is doing; not attributable to the new flags |

Not measured: energy per task (no meter; GPU power is sampled and unchanged), sensor-to-action latency (no capture
device), anything on a handset, and the live internet (no test contacted it).
