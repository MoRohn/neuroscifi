# NVIDIA inference stack: what fits this system, measured

Status: 2026-11-23, branch `nvidia-acceleration-review`. Scope: NVIDIA NIM, Triton Inference Server, Dynamo,
TensorRT-LLM and adjacent software, judged against the system that runs today on one DGX Spark. The rule applied:
a technology is adopted when it closes a measured gap of NeuroSciFi at acceptable cost, not because it is NVIDIA's.

Sources are official documentation and repositories fetched on 2026-11-23 (listed in §7) and measurements on the
reference host (evidence files in `docs/quality/evidence/accel_*.json`). A statement that could not be checked is
marked **unverified**.

## 1. The machine and the workload

| | Measured |
|---|---|
| Host | NVIDIA DGX Spark (DMI `NVIDIA_DGX_Spark`), GB10, aarch64 (Cortex-X925/A725, 20 cores), one Blackwell-class GPU, compute capability 12.1 |
| OS and drivers | Arch-based distribution (Omarchy), kernel 7.3.0; driver 615.78.08, CUDA user-mode driver 13.4; Docker 29.9.1 with NVIDIA Container Toolkit 1.20.1 through CDI. **Not DGX OS**: every NVIDIA support statement below names DGX OS or Ubuntu, so every "runs" carries "operating system not validated by NVIDIA" |
| Memory | 121.9 GiB unified CPU/GPU memory. `nvidia-smi` reports GPU memory as not supported. About 30 GiB available with NeuroSciFi's services and the host's unrelated containers resident. Swap is zram (compressed memory, not a disk) |
| What CUDA sees | `torch.cuda.mem_get_info()` inside the vLLM container reports 3.2 GiB free of 121.9: the page cache counts as used. A new GPU service must size itself with fixed numbers, as the existing ones do, not with a fraction of "free" memory |
| Storage | NVMe, 704 MB/s direct sequential read (a 31 GB model reads cold in about 45 s) |
| Network | Wi-Fi only; all four high-speed ports are down. There is no second GPU node |
| Thermal and power | 43 W at single-stream decode, 93 W peak during a 24k-token prefill, 77 °C peak, no throttling observed |
| Serving today | vLLM 0.33.0rc1 (image pinned by digest): Qwen3-30B-A3B-Instruct-2507 FP8, 44 GiB, 32k context; a second vLLM serves Whisper large-v3-turbo; Kokoro-82M in its own container |
| Workload | One owner, sometimes with one or two background research tasks. A research answer is a 1.1k-token prompt (p95 1.7k) and a 55-token structured reply (p95 218) |

What the baseline says about where time goes (`accel_baseline_inference.json`, `accel_baseline_pipeline.json`):

- **The GPU is compute-bound by one stream.** Decode is 46 to 50 tokens/s at 96 % utilisation. Eight concurrent
  streams give 64 tokens/s in total, twice one stream, at 11 tokens/s each. There is no idle capacity for a batching
  layer to recover.
- **The server's queue is empty.** Mean queue time is 0.03 ms. Prefix caching is on and serves 61 % of all prompt
  tokens since the server started.
- **Orchestration is 5 % of a task.** Of a research task's 3.1 s median, 0.2 s is NeuroSciFi's own code; the rest is
  two model calls.
- **Under load the owner waits behind background prefill.** A short foreground request beside four background
  requests saw its first token after 1.72 s instead of 0.18 s.
- **Memory retrieval is fast and lexical.** 0.6 ms per query; paraphrased questions find the right fact first 27 %
  of the time (`accel_baseline_memory.json`).

## 2. Adoption matrix

| Technology | What NeuroSciFi has instead | The problem it would solve | Runs on this host | Benefit, measured or expected | Complexity and risk | Decision |
|---|---|---|---|---|---|---|
| **vLLM priority scheduling** (`--scheduling-policy priority`, request field `priority`) | First come, first served; a file-lock cap of two background model calls | The owner's first token queues behind background prefill | Yes (in the pinned image) | Foreground first token beside four background requests 1.72 s → 1.13 s; no single-stream cost (`accel_vllm_flags.json`) | One flag, one request field; reversible in the override file | **Integrate now** (done) |
| **vLLM step size** (`--max-num-batched-tokens 4096`; named in NVIDIA's Spark vLLM playbook) | vLLM's default, which behaves like 2048 here | Prompts over 2k tokens prefill in two steps | Yes | Evidence-sized first token 706 → 550 ms; 24k-token prefill −10 %; 8-way throughput +9 % | One flag | **Integrate now** (done) |
| **Embedding and reranking models on the GPU** (vLLM pooling runner; Qwen3-Embedding-0.6B and Qwen3-Reranker-0.6B, Apache-2.0) | Lexical BM25 over memory statements | Paraphrase recall, unnamed subjects, unrelated memory returned for questions memory cannot answer; memory answering a question about another property | Yes (architectures present in the pinned image) | See `docs/acceleration/PERFORMANCE.md` §memory | Two small services behind the existing gateway; off until configured | **Integrate now** (done, optional) |
| **NVIDIA embedding NIM** (`nvcr.io/nim/nvidia/nemotron-embed:2.4.1`; "DGX Spark support starts with version 2.3") | The open models above | The same | Yes with caveat: needs acceptance of the NVIDIA AI Product Agreement; the documentation asks for an NGC account and key; about 3.1 GB of weights | Unmeasured. It speaks the same `/v1/embeddings` contract the integration uses, so it is a configuration change, not a code change | Licence acceptance is the owner's to give | **Integrate conditionally** (owner decision; no code needed) |
| **NVIDIA reranking NIM** (`nemotron-rerank:2.4.0`, Nemotron-3.5-Rerank-8B only) | Qwen3-Reranker-0.6B | The same | Yes, but about 16.8 GB of weights | A larger reranker for a memory of tens to hundreds of statements | Memory the host does not have to spare | **Reject** (size) |
| **LLM NIM** (`nemotron-3.5-lightning-30b-a3b`, others; "each NIM container image embeds one inference engine, vLLM or SGLang") | vLLM 0.33 serving Qwen3-30B-A3B | Packaging | Yes with caveat | None expected: the engine underneath is vLLM. Qwen3-30B-A3B is not offered as a NIM, so an LLM NIM means changing the reasoning model; the model-free NIM (2.1.6) carries vLLM 0.32.0, older than the engine in use | A model change needs the full guard and answer-quality replay; licence acceptance | **Reject** for the current model. Reconsider only together with a deliberate model evaluation |
| **Triton Inference Server** (`tritonserver:26.10-py3`, arm64 images exist; 26.11 lists a newer driver than this host has) | Three purpose-built servers behind loopback ports | Dynamic batching, ensembles, one lifecycle for many models | Yes with caveat (backends inside the arm64 image **unverified**) | None for the answer model: "all requests are placed on the vLLM AsyncEngine as soon as they are received. Inflight batching … is handled by the vLLM engine". Dynamic batching combines concurrent requests; one owner rarely produces any. The TensorRT-LLM backend image is no longer shipped | A 10 GB image and a model repository to maintain, for no measured gain | **Reject** now. **Research prototype** if several small CPU-side models appear that need one lifecycle |
| **Dynamo** (1.6.x; arm64 runtime images exist) | One vLLM worker | Routing, disaggregation and autoscaling across workers | Yes with caveat | None on one GPU with one worker, by its own documentation: KV-aware routing "Use two or more workers"; disaggregated prefill and decode "Requires 2 GPUs"; NIXL moves data between those workers; the planner scales replicas. KV-cache offload runs on one worker, but on unified memory a host tier is the same memory | A discovery plane and a frontend in front of a server that already answers in 0.03 ms of queue time | **Reject** for this topology. Revisit when a second GPU node exists |
| **TensorRT-LLM** (1.3.0; "TensorRT backend removed. PyTorch is the only backend") | vLLM | Decode speed | Yes with caveat (arm64 image; NVIDIA's Spark playbook targets it). Qwen3 MoE is supported; the playbook lists the NVFP4 base model, not Instruct-2507 and not FP8 | **Unverified.** No official comparison with vLLM on Spark exists. It would need the answer model stopped for an A/B on 44 GiB, and probably a different quantisation, which is a model-quality change | An engine swap behind the same OpenAI contract; quality replay required | **Research prototype**, not run in this review (§5) |
| **vLLM speculative decoding** (prompt lookup, `--speculative-config {"method":"ngram",…}`) | Plain decoding | Decode speed where the answer restates the prompt | Yes | **Measured worse**: restating decode +20 %, free-form decode 50 → 25 tokens/s, research task mean 4.4 → 5.6 s, 16-problem reasoning set 3.9 → 7.0 s median | — | **Reject** (measured) |
| **vLLM `--performance-mode interactivity`** | Balanced mode | Single-stream latency | Yes | **Measured none**: decode 50.1 against 50.3 tokens/s; start 83 s against 58 s | — | **Reject** (measured) |
| **Speech NIMs** (Parakeet, Magpie; Spark listed) | Whisper large-v3-turbo (4 GiB) and Kokoro-82M (1.5 GiB), measured WER 2.1 % clean | Speech accuracy or latency | Yes with caveat (NGC key; the TTS NIM asks for ≥ 16 GB) | Unmeasured; ASR is 0.27 s and TTS 0.12 s per request today | More memory than the services they would replace | **Reject** now (no measured speech deficit that they address) |
| **NeMo Guardrails** | A deterministic sentence guard with a closed caveat grammar, tested by eight adversarial rounds | Policy checks around model calls | Yes (Python package) | None identified: it would add model-judged checks beside a guard that is deliberately not model-judged | A second, weaker authority beside the guard | **Reject** |
| **RAPIDS / cuVS** | Brute-force cosine over tens to hundreds of vectors in NumPy | Vector index speed | Unverified on GB10 (a forum thread reports missing kernels) | None at this size: a query costs microseconds on the CPU | — | **Reject** until memory reaches a size where brute force is measurably slow |
| **Nemotron content-safety NIM** | Injection scanning, privacy screen, the guard | Content classification | Yes with caveat | No measured gap for a single-owner system | — | **Reject** now |

## 3. Why the answer model's engine stays

The directive asked for a comparison with simpler alternatives before introducing distributed components. The
comparison is one-sided on this hardware:

1. One stream saturates the GPU (96 % utilisation at 46 to 50 tokens/s). Batching layers (Triton dynamic batching,
   Dynamo routing) raise throughput only when requests wait for each other. Here the queue is empty.
2. The serving engine inside NVIDIA's own LLM NIMs on Spark is vLLM. A NIM would package an older vLLM around a
   different model.
3. Every disaggregation feature of Dynamo is defined across at least two workers. The Spark has one GPU, and its
   only network link is Wi-Fi.
4. The two engine settings that do help this workload are flags of the engine already installed.

What would change this: a second GPU node on the high-speed ports (Dynamo's routing and disaggregation become
applicable), or a measured TensorRT-LLM result on this model (§5).

## 4. What was integrated

- **Scheduling.** `deploy/vllm/vllm.env`: `NSF_VLLM_SCHEDULING=priority`, `NSF_VLLM_BATCHED_TOKENS=4096`. The
  gateway sends `priority` only for background model calls and only when `background_priority` is set, so an
  endpoint that does not know the field never sees it (`docs/architecture/COGNITIVE_INFERENCE.md` §7).
- **Memory models.** `deploy/memory/`: the same pinned vLLM image serves an embedding model and a reranker on
  loopback ports. The gateway gained `embed()` and `rerank()` under the same local-first guard, key rule and ledger
  as the answer model, with their own settings rows. Any endpoint that speaks `/v1/embeddings` or `/v1/rerank` can
  stand in, NVIDIA's embedding NIM included. After the owner's decision they are run as a managed service
  (`nsf setup` switches them on, supervised units, release when idle); an endpoint the owner points elsewhere is
  left alone by the idle policy ([ENGINEERING_PLAN.md](ENGINEERING_PLAN.md) §4 C). Since phase 3 one process
  inside the same pinned image serves both models by default (4.26 GiB against 9.33 GiB for two vLLM servers, the
  same benchmark result); the two vLLM servers remain selectable (`NSF_MEMORY_SERVER=vllm`).
- **Telemetry.** The ledger records the provider, the priority and, when the server reports them, the prompt tokens
  served from the prefix cache; `nsf resources` reports the server's queue, prefill and decode times.

## 5. Not run, and what would settle it

| Open item | Why it was not done | The check |
|---|---|---|
| TensorRT-LLM against vLLM on this model | Needs the answer model stopped, a second copy of the weights in a quantisation TensorRT-LLM documents for it (NVFP4), and a full answer-quality replay; a multi-hour experiment with a model-quality change inside it | `scripts/bench_inference.py` and `scripts/bench_pipeline.py` against a `trtllm-serve` endpoint on another port, same prompts, then the guard survival replay |
| NVIDIA embedding NIM against Qwen3-Embedding | Licence acceptance and an NGC key are the owner's | `nsf web memory config --embed-url …` at the NIM's port, then `scripts/bench_memory.py` |
| A lighter server for the two memory models (4.6 GiB each under vLLM for 1.1 GiB of weights; no setting of the pinned image lowers it) | **Done in phase 3** without a new image: one process in the pinned image serves both (`deploy/memory/server.py`; [ENGINEERING_PLAN.md](ENGINEERING_PLAN.md) §5.1), so the candidates below were not needed. Before that: outside the decision that was delegated. `text-embeddings-inference` publishes CUDA images for amd64 only; `ghcr.io/ggml-org/llama.cpp:server-cuda` lists a linux/arm64 manifest (MIT) that was **not pulled or run**: whether its CUDA build runs on GB10, and whether GGUF builds of the two models reproduce the benchmark with the reranker's prompt, is unverified | Pin the image by digest, serve both models, then `scripts/bench_memory.py` against the numbers of [PERFORMANCE.md](PERFORMANCE.md) §4 |
| Triton backends in the arm64 image | No use for the answer; not pulled (10 GB) | `docker run --rm nvcr.io/nvidia/tritonserver:26.10-py3 ls /opt/tritonserver/backends` |
| NVIDIA's validation of this operating system | NVIDIA validates DGX OS and Ubuntu | None available; the containers that run here are evidence for those containers only |

## 6. Licences touched by this review

| Artifact | Licence | Action taken |
|---|---|---|
| `Qwen/Qwen3-Embedding-0.6B`, `Qwen/Qwen3-Reranker-0.6B` | Apache-2.0, not gated | Downloaded at pinned revisions to `~/.local/share/neuroscifi/models/` |
| NVIDIA NIM containers | NVIDIA AI Product Agreement | Not pulled, not accepted |
| `nvidia/llama-nemotron-embed-1b-v2`, `nvidia/Nemotron-3.5-Embed-1B` | NVIDIA Open Model License, OpenMDW-1.1 | Not downloaded |

## 7. Sources

Fetched 2026-11-23.

- NIM for Spark: <https://build.nvidia.com/spark/nim-llm/overview.md>; support matrix
  <https://docs.nvidia.com/nim/large-language-models/latest/reference/support-matrix.html>; prerequisites
  <https://docs.nvidia.com/nim/large-language-models/latest/get-started/prerequisites.html>; release notes
  <https://docs.nvidia.com/nim/large-language-models/latest/about-nim-llm/release-notes.html>; licence
  <https://docs.nvidia.com/nim/large-language-models/latest/resources/legal.html>
- Embedding and reranking NIMs: <https://docs.nvidia.com/nim/nemo-retriever/embedding/latest/support-matrix.html>,
  <https://docs.nvidia.com/nim/nemo-retriever/reranking/latest/support-matrix.html>
- Speech NIMs: <https://docs.nvidia.com/nim/speech/latest/reference/support-matrix/asr.html>,
  <https://docs.nvidia.com/nim/speech/latest/reference/support-matrix/tts.html>
- Triton: release notes <https://docs.nvidia.com/deeplearning/triton-inference-server/release-notes/rel-26-11.html>;
  compatibility <https://github.com/triton-inference-server/server/blob/main/docs/introduction/compatibility.md>;
  vLLM backend <https://github.com/triton-inference-server/vllm_backend>; batcher
  <https://github.com/triton-inference-server/server/blob/main/docs/user_guide/batcher.md>
- Dynamo: <https://docs.nvidia.com/dynamo/latest/reference/compatibility>; installation
  <https://docs.nvidia.com/dynamo/v1.6.1/cli/installation/install-dynamo>; KV-aware routing
  <https://docs.nvidia.com/dynamo/v1.6.1/cli/kv-aware-routing/overview>; disaggregated serving
  <https://docs.nvidia.com/dynamo/v1.6.1/cli/disaggregated-serving/overview>; KV-cache offloading
  <https://docs.nvidia.com/dynamo/v1.6.1/cli/kv-cache-offloading/overview>
- TensorRT-LLM: release <https://github.com/NVIDIA/TensorRT-LLM/releases/tag/v1.3.0>; supported models
  <https://github.com/NVIDIA/TensorRT-LLM/blob/main/docs/source/models/supported-models.md>; Spark playbook
  <https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/trt-llm/README.md>
- vLLM: documentation at tag v0.33.0rc3 <https://github.com/vllm-project/vllm/tree/v0.33.0rc3/docs>; NVIDIA's Spark
  vLLM playbook <https://build.nvidia.com/spark/vllm/advanced.md>; the flags were also read from the pinned image's
  own `vllm serve --help=all`
- Registry statements ("an arm64 manifest exists") come from anonymous manifest reads of `nvcr.io`; no image was
  pulled.
