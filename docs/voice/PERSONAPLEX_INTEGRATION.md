# PersonaPlex integration audit (v0.7.0, area 6)

Status: **audit + feasibility evidence, 2026-11-21.** No NeuroSciFi code depends on this yet. Every number below
is labelled **measured** (with the command that produced it), **third-party** (someone else's report, not
reproduced here) or **unmeasured**. Audited upstream commit: `NVIDIA/personaplex@16419a2` (2026-11-05). Paths
like `server.py:501` are relative to `moshi/moshi/` in that repository unless they start with another top-level
folder.

## 1. Verdict

- **PersonaPlex runs on this host, but the upstream code is not real-time here.** With the owner's licence
  acceptance the weights were downloaded and run for real (aarch64, GB10 sm_121, torch 2.14.0+cu130 from the
  present vLLM image). Upstream `main` takes **134.5-151.5 ms per 80 ms frame** (RTF 1.68-1.84, every frame over
  budget); with system-prompt pinning switched off it takes 99.4-99.8 ms (RTF 1.24) — still too slow. Live, the
  audio would lag further behind every second (the symptom reported in upstream issues #3 and #96).
- **A third-party, unmerged optimisation series (PR #102/#103, fork `jethac/personaplex@pgx-plan`) is real-time
  here**: `--fast` (weight-only 8-bit LM + depformer early exit + single Mimi + fp16 Mimi) measured **47.25 ms p50,
  50.76 ms p99, 0/375 frames over budget**, with Lane B's Qwen loaded. Same harness, stock bf16: 104.07 ms p50,
  375/375 over budget. Output quality of `--fast` was not evaluated here.
- **It cannot speak NeuroSciFi's text through its shipped server or protocol** (audio-only input; text only as
  the persona prompt at connect). **Through a library-level hack it can**: teacher-forcing the text stream
  rendered 3/3 test sentences with 0 % word error (Whisper check) at 5 frames per word, and failed completely
  (100 % WER, unrelated words) at 4 frames per word. Fragile, unsupported upstream, needs a custom server.
- **Recommendation**: the v0.7.0 voice pathway is the **hybrid path** — Whisper large-v3-turbo (vLLM, GPU) for
  ASR + NeuroSciFi cognition + Kokoro-82M for TTS — measured end-to-end on this host with 0 % round-trip WER on 3
  sentences, TTS RTF 0.013 (GPU) / 0.42 (CPU), ASR RTF ≤ 0.042. PersonaPlex stays an **experimental, opt-in**
  component (forced-text voice renderer or non-factual conversational filler), never an authority, and only on
  the optimised runtime after a co-residency test under concurrent LLM load. vLLM-Omni's PersonaPlex support is
  currently marked unavailable and is not an option.

## 2. What PersonaPlex is

| Question | Finding | Evidence |
|---|---|---|
| Architecture | Moshi fine-tune (Kyutai Moshiko weights): Mimi neural codec (SEANet conv + 8-layer transformer, 8 of 32 codebooks used) + 32-layer 4096-d "temporal" transformer (Helium backbone) + 6-layer 1024-d depth transformer over 16 codebooks. Full duplex: every 80 ms step consumes one user frame and emits one agent audio frame plus one agent text token. | `models/loaders.py:39-122`, `models/lm.py:649`, HF card "Network Architecture: Moshi", base model `kyutai/moshiko-pytorch-bf16` |
| Parameters / precision / size | `model.safetensors` 16,742,874,000 B; loads as bf16, **8.371 B parameters** (measured: `lm_params_b`), Mimi checkpoint 384,644,900 B. sha256 of the downloaded weights matched the HF LFS pointer (measured). | HF API `siblings`; `loaders.py:170` (`dtype=torch.bfloat16`); §6 run |
| Audio format | 24 kHz mono, 12.5 frames/s, **1920 samples = 80 ms per frame**. Browser client sends Opus (20 ms Opus frames, 24 kHz encoder, `maxFramesPerPage: 2`). | `loaders.py:39-40`, `server.py:108`, `client/src/pages/Conversation/hooks/useUserAudio.ts:67-79` |
| Server entrypoint | `python -m moshi.server` (aiohttp), one route `GET /api/chat` (WebSocket), static web UI from `dist.tgz`; default port 8998; `--ssl DIR` for https. Docker: `Dockerfile` (CUDA 12.4 base, `uv sync`), `docker-compose.yaml` (`NO_TORCH_COMPILE=1`). | `server.py:363-501`, `Dockerfile:1-25`, `docker-compose.yaml` |
| Wire protocol | Binary WebSocket frames, first byte = kind. Client→server: only `0x01` + Opus bytes is handled; any other kind is logged "unknown message kind" and dropped. Server→client: `0x00` handshake (sent after the system prompt is processed), `0x01` + Opus audio, `0x02` + UTF-8 text piece. The client's `0x03` control (`start/endTurn/pause/restart`), `0x04` metadata, `0x06` ping are **not implemented server-side**. | `server.py:200-205, 245-257, 294`; `client/src/protocol/encoder.ts:10-31` |
| Session parameters | Query string: `text_prompt` (wrapped as `<system> … <system>`), `voice_prompt` (file name inside the voices dir; `.pt` embeddings or audio), `seed`. Sampling knobs sent by the client (`text_temperature`, `audio_topk`, …) are ignored (commented out). `seed` is read from `request["seed"]`, not `request.query` — a bug (open PR #111). | `server.py:149-177`; `client/src/pages/Conversation/Conversation.tsx:56-72` |
| Session lifecycle | One global `asyncio.Lock`: **one conversation at a time per process**; further connections queue. On connect: reset streaming state → prefill voice prompt + 0.5 s silence + text prompt + 0.5 s silence → send handshake → three tasks (recv, model loop, send) until any ends → close. A client that vanishes mid-stream can leave the lock held forever (open PR #104). | `server.py:264-315`, `lm.py:1153-1176` |
| Persona / voice conditioning | Text prompt = role/persona text forced into the text stream at session start; voice prompt = audio (or pre-computed `.pt` embedding) forced into the agent audio stream. 18 bundled voices (`NATF0-3`, `NATM0-3`, `VARF0-4`, `VARM0-4`). Training covered a fixed "teacher" assistant role, customer-service roles, and Fisher casual-conversation prompts. | `lm.py:996-1176`; `README.md:110-165` |
| Context | 2048-frame attention context (`loaders.py:104`) ≈ 164 s. The system prompt is pinned (kept) and the rest slides: measured log `Pinned 89 system prompt frames (… sliding window 1959 frames)` ≈ 157 s of conversation memory. README's example "sliding window 2824 frames" does not match the code's 2048 (doc drift); vLLM-Omni's port uses 3000. | `README.md:10, 68-78`; `lm.py:1187-1197`; `modules/transformer.py:220-262` |
| Text channel | Yes: one "inner monologue" text token per frame; non-padding tokens are streamed to the client as `0x02` pieces. It is the model's own generated text, roughly time-aligned with its speech, not an ASR transcript of either side. The **user's** speech is never transcribed. | `server.py:241-246`, `offline.py:292-301` |
| Barge-in / turn-taking | Native and implicit: the model always hears the user stream and decides itself when to stop/speak; there is no explicit turn, stop or "cancel speech" message. Card (third-party, A100): smooth turn-taking latency 0.170 s, user-interruption latency 0.240 s, interruption TOR 0.950. vLLM-Omni lists it as `supports_client_commit=false, supports_external_turn_signal=false`. | HF README "FullDuplexBench" table; `vllm-omni/recipes/NVIDIA/PersonaPlex.md` |
| Licence | Code: MIT (`LICENSE-MIT`; `moshi/LICENSE.moshi`, `moshi/LICENSE.audiocraft`). Weights: **NVIDIA Open Model License** (version dated 2025-10-24) + CC-BY-4.0 attribution for the Moshiko base. Relevant to owner-local use: commercial and personal use permitted; derivatives allowed; NVIDIA claims no ownership of outputs; rights **terminate automatically if a safety guardrail is bypassed or disabled** without a substantially similar replacement, and on patent/copyright litigation; redistribution needs the licence + "Licensed by NVIDIA Corporation under the NVIDIA Open Model License" notice. (Licence text read via a web summary, not verbatim; the linked Trustworthy-AI terms were not reviewed.) The model card says "ready for commercial use"; bias card: no bias mitigation measured; privacy card: trained with consented voice data, user interactions not used for training. | `README.md:174-176`; HF `README.md:71-72`, `bias.md`, `privacy.md`, `safety.md` (read after the owner's licence acceptance) |
| Supported hardware (vendor) | Card lists Ampere A100 and Hopper H100 only; test hardware A100 80 GB. Blackwell needs a cu130 torch (README). aarch64 is not mentioned. | HF README "Supported Hardware"; `README.md:38-41` |

### 2.1 Security and robustness findings in the upstream server

These matter because NeuroSciFi must not expose an unauthenticated audio endpoint.

1. **Binds every interface regardless of `--host`.** `--host` is only used to print the URL (`server.py:496`);
   `web.run_app(app, port=…)` (`server.py:501`) gets no host, so aiohttp listens on `0.0.0.0`. Mitigation: run it
   in a container and publish only `127.0.0.1:<port>`.
2. **No authentication**, and `--gradio-tunnel` can publish the endpoint to the internet (`server.py:367-370, 433-500`).
3. **Path traversal into `torch.load`** via `voice_prompt` (`server.py:158-175`, `lm.py:1013-1015`): an attacker
   can make the server unpickle any reachable `.pt` file (open PRs #98/#101). NeuroSciFi must pass only an
   allow-listed voice name.
4. `tar.extractall` without a filter on HF-downloaded archives (`server.py:339, 355`).
5. Session lock can leak on abrupt disconnect (PR #104); `seed` bug (PR #111); `--cpu-offload` crashes at warm-up
   (issue #105).
6. The server always runs a second Mimi (`other_mimi`) whose output is discarded (`server.py:122, 129, 231, 238`).

## 3. Can PersonaPlex speak NeuroSciFi's authoritative text?

**Not through the shipped server or protocol.** The only client→server input is user audio (`server.py:200-205`);
text arrives only once, as the persona prompt at connect time (`server.py:176`). Changing the prompt mid-session
is not supported (a "live prompt injection" PR #69 was closed unmerged). The model generates its own words from
its 7B Helium backbone; there is no tool, retrieval, or "say this" channel.

**Inside the library there is a mechanism**: `LMGen.step(text_token=…)` teacher-forces the agent text stream for
that frame (`lm.py:812-815`) and the depth transformer then generates audio conditioned on the forced token
(`lm.py:928-933`); the same mechanism is how the system prompt is injected (`lm.py:1132-1140`). The model was not
trained as a TTS (Kyutai's TTS is a separately trained delayed-streams model), so whether forcing produces
faithful speech had to be measured — see §6.3.

Measured result (§6.3): forcing works for short sentences when the schedule gives each word enough frames
(5 frames = 400 ms per word: 3/3 sentences, 0 % WER), and fails badly when it does not (4 frames per word: the
model spoke "The easiest way is to tap the stebrine, but two studies suggest this claim is a soup."). The text
channel then echoes the forced text regardless of what the audio says, so **the text channel cannot be used to
verify forced speech** — an ASR check of the rendered audio is required. Untested: numbers, abbreviations, long
paragraphs, other voices, forcing while the owner is talking, and forcing on the optimised (`--fast`) runtime.

## 4. Platform compatibility (aarch64, Blackwell sm_121, CUDA 13)

| Item | Finding |
|---|---|
| Upstream pins | `torch >=2.2,<2.5`, `numpy <2.2`, `huggingface-hub <0.25`, `safetensors <0.5`, `sphn <0.2` (`moshi/pyproject.toml:5-15`). torch <2.5 has no sm_120/121 kernels; upstream's own README says install the cu130 wheel for Blackwell (`README.md:38-41`). Docker base is CUDA 12.4 x86-oriented (`Dockerfile:1-2`). Installing as documented on this host would fail; do not run it on the host. |
| What actually ran here | The present `vllm/vllm-openai:nightly` image (torch 2.14.0+cu130, aarch64, arch list sm_80…sm_120, which runs on sm_121) loaded and ran PersonaPlex **unmodified** by putting `moshi/` on `sys.path` (no `pip install`), after pip-installing only `soundfile librosa jiwer` in a throw-away container. Newer numpy/safetensors/huggingface_hub/einops in the image did not break loading or inference. |
| `sphn` (Opus + resampling) | **No aarch64 wheel**; `pip install "sphn>=0.1.4,<0.2"` and latest `sphn` both failed to build in the image (Rust needed). The benchmark replaced it with a stub (only used for Opus streaming and `.wav` voice prompts), so **Opus encode/decode was not exercised** here (third-party profile: ~0.5 ms/frame, issue #3). A real server needs sphn built from source with a Rust toolchain in the image, or an alternative Opus path. |
| Known GB10 status upstream | Issue #3 (58 comments, open) and #96 (open): DGX Spark/GB10 runs but audio is choppy and latency grows, because each 80 ms frame takes longer than 80 ms. Third-party profile on GB10 (issue #3, 2026-07-24): stock bf16 101.7 ms/frame (475/475 frames over budget); with unmerged PR #103 flags (w8a16 weight-only quantisation + depformer early exit + skip second Mimi + fp16 Mimi) 46.5 ms p50. These are not merged upstream (`#102`, `#103` open). |
| vLLM-Omni | `vllm-project/vllm-omni` had a native PersonaPlex full-duplex integration (`/v1/realtime?duplex=1`), but its recipe (main @ `dbd2566`, 2026-11-21) is marked **"Unavailable with vLLM 0.34 / V2-only execution"** — historical reference only. Verified on Hopper; GB10/aarch64 not listed. vLLM-Omni is not in the present image. Not a viable path for v0.7.0. |

## 5. What was blocked, and how it was unblocked

Until 2026-11-21 the weights were gated (`gated: auto`, licence "other"); without a token every file except
`README.md` returned HTTP 401 (`config.json`, `privacy.md`, `safety.md`, `bias.md` included). The owner then:

1. signed in to Hugging Face and accepted the NVIDIA Open Model License on
   `https://huggingface.co/nvidia/personaplex-7b-v1`;
2. logged this host in (`hf auth login`, token stored by the HF CLI, never in the repo);
3. downloaded with `uvx --from huggingface_hub hf download nvidia/personaplex-7b-v1 --local-dir
   ~/.local/share/neuroscifi/models/personaplex-7b-v1` (16.7 GB weights + 385 MB Mimi + tokenizer + `voices.tgz` +
   `dist.tgz`; sha256 of `model.safetensors` verified against the HF LFS pointer).

The weights must stay outside the git tree and outside checkpoints; `voices.tgz`/`dist.tgz` are extracted into a
container-private directory with a safe tar filter.

## 6. Measured on this host

Host: DGX Spark GB10 (sm_121), aarch64, 121.9 GiB unified memory, driver 615.78, CUDA 13.4. Lane B's
`neuroscifi-vllm` container (Qwen3-30B-A3B-Instruct-2507-FP8, `--gpu-memory-utilization 0.45`) was **loaded and
idle** during every run below unless stated; its concurrent decode load was not exercised (**unmeasured**).

### 6.1 PersonaPlex, upstream code (real inference)

Harness `pp_bench.py` imports upstream `moshi` unchanged and runs the same per-frame loop as `server.py:224-246`
(Mimi encode ×2, `LMGen.step`, Mimi decode ×2, device→host copy), timing each 80 ms frame with
`torch.cuda.synchronize()`. Opus is not included (stubbed `sphn`, see §4). Voice `NATF2.pt`, persona = the
README's teacher prompt (89 system-prompt frames). Input: upstream `assets/test/input_assistant.wav` (40 s user
question + 3 s silence), or silence. First 25 frames excluded from percentiles.

| Run (UTC) | Config | Frame p50 / p90 / p99 ms | Over 80 ms | RTF | Lane B state |
|---|---|---|---|---|---|
| A (≈11:13-11:18) converse | upstream defaults (pin + prefill on) | 136.52 / 140.33 / 145.14 | 512/512 | 1.71 | Qwen loaded, idle |
| B0 (ended 11:20:10) free 6 s | defaults | 134.94 / 136.60 / 137.52 | 50/50 | 1.69 | Qwen loaded, idle |
| B1-B3 (ended 11:20:37-11:21:20) forced text | defaults | 135.86-283.12 / 282-288 / 290-305 | all | 2.17-3.54 | coincided with Lane B tearing its vLLM down (≈47 GiB released by 11:21:33; container restarted 11:24:09) |
| B4 (ended 11:21:33) forced, 4 frames/word | defaults | 135.57 / 137.74 / 139.06 | 64/64 | 1.70 | Qwen absent |
| B5 (ended 11:22:52) converse | defaults | 151.52 / 155.76 / 162.22 | 512/512 | 1.84 | Qwen absent |
| C0-C2 | `pin_system_prompt=False, prefill_system_prompt=False` | 99.40-99.80 / 101.2-101.5 / 101.9-103.7 | all | 1.24-1.25 | Qwen loaded, idle |
| D0-D2 | defaults (control for C) | 134.52-135.77 / 135.7-137.8 / 137.0-140.5 | all | 1.68-1.70 | Qwen loaded, idle |

Other measured values (upstream defaults):

- **Load** (Mimi ×2 + tokenizer + 16.7 GB LM from local disk): 119.0-133.4 s; warm-up incl. CUDA-graph capture
  and `torch.compile`: 29.4 s first time, 3.2-10.1 s later (inductor cache). Total cold start ≈ 2.5 min.
- **Session open** (reset + system prompt, before the handshake would be sent): 0.17-2.92 s with prefill; 8.05 s
  with prefill off (frame-by-frame).
- **First audio frame**: 0.098-0.147 s after the first input frame (one model step). Because RTF > 1, live
  latency then grows by 0.7-0.8 s per second of conversation (A: 43 s of input took ≈ 73 s).
- **Memory**: `torch.cuda.max_memory_allocated` 17.62 GiB, reserved 18.05 GiB (17.50 GiB without pinning);
  container cgroup 15.9 GiB sampled during a run. `mem_get_info` free before load ranged 7.95-24.95 GiB with Lane
  B resident; nothing failed for lack of memory.
- **Pinning cost**: the November "endless conversation" change (system prompt pinned in the KV cache, re-encoded
  every step, cost scaling with `--max-pinned-frames 512`) adds ≈ 35 ms per frame on this GPU (C vs D). Turning
  it off brings back issue #99: the persona prompt slides out of context after ≈ 2.7 min (2048 frames).
- **Behaviour**: unprompted, it opens with "Hey, let me know if you have any questions." On the test question
  (rice; Whisper transcript of the input: "How do I prevent rice from becoming sticky … what quick garnish could add
  color …") it answered fluently from its own knowledge, different content per run (sampling). Text channel vs
  Whisper transcript of its own audio: **0.97 % WER** over 103 words (run A) — the text channel is a good
  approximate transcript of what it says when free-running.

### 6.2 PersonaPlex, third-party optimised fork (`jethac/personaplex@a0e170b`, PR #102/#103)

Run with the fork's own `bench/bench.py` inside the same container (`PYTHONPATH=/fork/moshi`, `sphn` stubbed,
`--no-dmon --pin-cores none`, 400 frames, 375 summarised). The fork predates upstream's pinning change.

| Config | total ms p50 / p90 / p99 / max | step ms p50 | Over 80 ms | Cold start |
|---|---|---|---|---|
| `--fast` (w8a16 + `--dep-q-exit 8` + `--skip-other-mimi` + `--mimi-fp16`) | **47.25 / 48.66 / 50.76 / 51.56** | 43.54 | **0/375** | 122.9 s (incl. 2.7 s quantisation, 38.9 s warm-up) |
| stock bf16 (fork) | 104.07 / 107.48 / 113.36 / 118.37 | 94.09 | 375/375 | 147.1 s |

Lane B's new Qwen container (started 11:24:09) was loaded during both runs; its log showed two short request
bursts in the window. These numbers match the fork author's published GB10 numbers (46.5 / 101.7 ms). Audio
quality, forced-text behaviour and memory of `--fast` were **not measured** here.

### 6.3 PersonaPlex speaking external text (forced text stream)

`pp_bench.py force`: user stream = digital silence; agent text stream forced with
`[PAD]×4` lead-in, then per word `[EPAD, word pieces…]` padded with `PAD` to *N* frames, then `[PAD]×25`. Output
transcribed by Whisper large-v3-turbo (vLLM) and scored as in §6.5.

| Sentence | Frames/word | Whisper transcript of PersonaPlex audio | WER |
|---|---|---|---|
| "The evidence for this claim is disputed, because two independent sources disagree about the date." | 5 | identical (comma dropped) | **0 %** |
| "I found three peer reviewed studies on sleep and memory consolidation, and all of them are recent." | 5 | identical ("peer-reviewed") | **0 %** |
| "Microphone capture is now off, and no raw audio will be stored on this device." | 5 | identical | **0 %** |
| first sentence again | 4 | "The easiest way is to tap the stebrine, but two studies suggest this claim is a soup." | **100 %** |

The text channel showed the forced sentence verbatim in all four cases, including the failed one. Free-running
on silence it said "Hey, let me know if you have any questions." (Whisper agrees).

### 6.4 Hybrid path: Kokoro-82M TTS

Container: `vllm/vllm-openai:nightly` + `pip install kokoro==0.9.4 soundfile librosa jiwer` (pulls misaki 0.9.4,
spaCy 3.8 + `en_core_web_sm` downloaded on first use, `espeakng-loader` with bundled espeak-ng, phonemizer-fork;
it kept torch 2.14.0 but **downgraded `nvidia-nccl-cu13` 2.31.2→2.30.7** — pin it with a constraints file in a
real image). Weights from the existing HF cache mounted read-only (`HF_HUB_CACHE=…/hub HF_HUB_OFFLINE=1`), voice
`af_heart`, 24 kHz output. Script: `kokoro_rtf.py` (scratchpad, §9).

| Device | Load | Synthesis of 3 sentences (17.6 s audio) | RTF | Peak allocation |
|---|---|---|---|---|
| GPU (3 runs, 2 with Qwen idle co-resident) | 1.23-1.25 s (7.0 s first run incl. spaCy model download) | 0.059-0.095 s per sentence (whole sentence = first chunk) | **0.013** | 690.5 MiB |
| CPU, 8 threads | 0.89 s | 2.13-2.69 s per sentence | **0.42** | — |
| CPU, 4 threads | 0.90 s | 2.96-3.37 s per sentence | **0.54** | — |

Kokoro-82M is Apache-2.0 (weights) and the `kokoro` package Apache-2.0; espeak-ng (fallback G2P) is GPL-3.0,
loaded as a separate shared library at runtime — note it in the licence inventory.

### 6.5 Hybrid path: ASR and round-trip WER

The three Kokoro GPU WAVs were transcribed and scored against the source sentences (lower-cased, punctuation and
hyphens normalised; `jiwer`). Script: `asr_roundtrip.py`, `fw_cpu.py`.

| ASR | Where | Latency per 5-6 s clip | RTF | Round-trip WER (3 sentences) |
|---|---|---|---|---|
| `openai/whisper-large-v3-turbo` via vLLM `/v1/audio/transcriptions` (`--enforce-eager --kv-cache-memory-bytes 2147483648 --gpu-memory-utilization 0.08 --max-num-seqs 4`) | GPU | 0.16-0.25 s warm (2.52 s first request); 0.18-0.33 s with Qwen co-resident idle | 0.026-0.042 | **0.0 %** (3/3 exact) |
| faster-whisper 1.2.1 `small.en`, int8, 8 threads | CPU | 1.95-2.64 s | 0.38-0.42 | **0.0 %** |
| faster-whisper `distil-large-v3`, int8, 8 threads | CPU | 7.05-7.28 s | 1.12-1.39 (too slow) | **0.0 %** |

Whisper server facts (measured): weights 1.51 GiB; start-up to "Application startup complete" ≈ 85 s (66.9 s
engine init incl. warm-up); container cgroup memory 12.9 GiB idle (includes CPU-side processes, not only GPU).
It exposes `/v1/audio/transcriptions` and `/v1/audio/translations` only (no streaming/realtime route for Whisper).

Two start-up failures on unified memory, both fixed by flags (the implementation lane must use them):

- default `--gpu-memory-utilization 0.92` → `ValueError: Free memory on device … (97.05/121.93 GiB) … less than
  desired GPU memory utilization (0.92, 112.17 GiB)`: always pass a small `--gpu-memory-utilization` (0.08 worked);
- `--gpu-memory-utilization 0.08` alone → `AssertionError: Error in memory profiling. Initial free memory 93.47
  GiB, current free memory 95.87 GiB` (free memory moves under the profiler on unified memory): add
  `--kv-cache-memory-bytes`, which skips the profiling assertion.

faster-whisper 1.2.1 + PyAV 19 fails on file input (`TypeError: open() got an unexpected keyword argument
'metadata_errors'`); pass a 16 kHz float32 numpy array (as `fw_cpu.py` does) or pin PyAV.

Caveat: synthetic, clean TTS speech is the easiest possible ASR input. These WERs say the plumbing is right;
they say nothing about the owner's microphone, accent, noise or far-field audio (**unmeasured**).

Other ASR options (unmeasured): the image's vLLM also registers `Qwen3ASRForConditionalGeneration`,
`Qwen3ASRRealtimeGeneration`, `VoxtralRealtimeGeneration`, `CanaryForConditionalGeneration` and
`GraniteSpeechForConditionalGeneration`; a realtime/streaming ASR is worth evaluating later for lower
end-of-utterance latency. NVIDIA Parakeet needs NeMo (not in the image; heavy) — not evaluated.

## 7. Recommended Voice Cortex architecture

Principle (spec area 6): **NeuroSciFi is authoritative** for facts, tools, memory and permissions. A speech model
may shape *how* something is said and *when*; it never decides *what* is asserted. PersonaPlex's free-running
output is a 7B chat model's own words, with no access to NeuroSciFi's evidence, memory or tools, so it must not
answer the owner's questions on its own.

### 7.1 Default pathway for v0.7.0: hybrid ASR → cognition → TTS (measured, works on this host)

```
mic (client, AEC on) ──PCM 16 kHz──▶ VAD/endpointing ──▶ ASR (Whisper, vLLM, loopback) ──▶ provisional/final transcript
                                                                                     │
                                                         NeuroSciFi cognition (intent → memory → evidence → LLM → validation)
                                                                                     │ authoritative text (sentences)
speaker (client) ◀──PCM 24 kHz── TTS (Kokoro-82M, sentence by sentence, loopback) ◀──┘
```

- **Mode A (voice + text)**: everything above; text shown alongside audio.
- **Mode B (voice in, text out)**: no TTS service needed.
- **Mode C (text only)**: no voice service runs; nothing here is required (works without PersonaPlex).
- Transcripts: ASR text is labelled *approximate (ASR)*, provisional until end-of-utterance, then final, attributed
  to the owner and timestamped; NeuroSciFi's answer text is *authoritative*; spoken audio is a rendering of it.
- Latency budget (measured parts only): ASR 0.16-0.33 s per 5-6 s utterance + Kokoro first sentence 0.06-0.10 s
  on GPU. End-of-utterance detection and the LLM's time-to-first-sentence are **unmeasured** and will dominate.
- Stream TTS per sentence as the LLM produces validated sentences; never speak text that has not passed claim
  validation.

### 7.2 Turn arbitration and barge-in (hybrid path)

- A server-side **turn manager** owns the state machine `idle → listening → thinking → speaking → (interrupted |
  idle)`; exactly one capturing device holds the microphone lease (explicit hand-off).
- Barge-in: while `speaking`, client-side VAD on the AEC'd mic stream that stays voiced for a short hold time
  (threshold **unmeasured**; start at ~250 ms) → the client stops playback at once, the server cancels the queued
  TTS sentences, and the transcript records the answer as *interrupted at sentence n*. The underlying long task is
  not cancelled (spec: long tasks survive voice session end); the owner can say "stop" to cancel it explicitly.
- Echo cancellation (`getUserMedia({echoCancellation: true})`) is mandatory, otherwise the agent's own TTS
  triggers barge-in. Not measured here.
- Mic off = the client stops the `MediaStreamTrack` and closes the upload socket; the server drops any partial
  buffer. Raw audio is processed in memory only and never written (no `--output-wav`-style dumps in production).

### 7.3 Where PersonaPlex fits (experimental, off by default)

Two possible roles, both gated behind an owner setting and only on the optimised runtime (§6.2):

1. **Voice renderer with full-duplex listening ("forced-text mode")** — NeuroSciFi's authoritative sentence is
   tokenised and teacher-forced into PersonaPlex's text stream (`LMGen.step(text_token=…)`), while the owner's mic
   streams in. Measured: 3/3 sentences rendered exactly at 5 frames/word; 4 frames/word produced unrelated speech.
   So it needs a per-word duration model (syllables, numbers, abbreviations) and a verifier (ASR of the rendered
   audio, or at least the text channel) before it can be trusted. Barge-in is not automatic here: forced text
   keeps speaking, so the turn manager must stop forcing when VAD fires. This is **not an upstream feature**; it
   uses library internals and needs a custom server.
2. **Conversational filler only** (backchannels, "let me check that") — PersonaPlex free-running with a persona
   prompt that forbids factual claims. Its text channel must be labelled *conversational model output, not
   verified*. Measured behaviour: it greets on its own at session start ("Hey, let me know if you have any
   questions.") and answered a cooking question at length from its own knowledge — exactly what must not happen
   for factual queries.

Recommendation: ship 7.1 as the voice pathway; build 7.3(1) as an experiment only after the owner accepts that it
uses a third-party, unmerged optimisation series and after a co-residency test under concurrent LLM decode passes.

## 8. Resource plan

Unified memory: 121.9 GiB shared by CPU and GPU; memory bandwidth (~273 GB/s, third-party figure) is the
shared bottleneck for every batch-1 decoder on this box.

| Component | Memory (measured unless noted) | Compute per real-time second | Notes |
|---|---|---|---|
| Qwen3-30B-A3B FP8 (Lane B) | `--gpu-memory-utilization 0.45` + `--kv-cache-memory-bytes 13958643712` (≈ 55 GiB budget); container cgroup 16.2 GiB sampled | — | port 127.0.0.1:8811 |
| PersonaPlex, upstream bf16 | peak allocated 17.62 GiB, reserved 18.05 GiB; container 15.9 GiB sampled | 1.24-1.84 s (RTF 1.24 without pinning, 1.68-1.84 with): **not real-time** | §6.1 |
| PersonaPlex, fork `--fast` | **unmeasured** (8-bit weights; expect less than bf16) | 0.59 s (47 ms per 80 ms frame) | §6.2; one session per process |
| Whisper large-v3-turbo (vLLM) | weights 1.51 GiB + 2 GiB KV; container 12.9 GiB cgroup | ≈ 0.03-0.04 s | §6.5 |
| Kokoro-82M | peak allocated 0.69 GiB (GPU) | 0.013 s GPU / 0.42 s CPU (8 threads) | §6.4 |
| faster-whisper small.en (CPU fallback) | **unmeasured** (small) | 0.38-0.42 s on 8 cores | §6.5 |

Plan:

- **Hybrid path co-resides comfortably** with Lane B (≈ 55 + 4 + 1 GiB of GPU budget, both voice models at
  RTF ≤ 0.04). Keep Kokoro and the ASR resident while voice is enabled; unload after an idle timeout.
- **Audio priority**: ASR and TTS requests are small; run them with their own vLLM/process so they never queue
  behind LLM requests. If GPU memory is short, Kokoro on CPU (RTF 0.42) and faster-whisper small.en on CPU (RTF
  0.4) are measured fallbacks that keep Mode A working without any GPU audio model.
- **PersonaPlex co-residency is a bandwidth problem, not a capacity one**: with Lane B's model idle, frame time
  was unchanged; while Lane B's container was being torn down/restarted, PersonaPlex frames rose from ~136 ms to
  283-300 ms (§6.1). Concurrent LLM decode is **unmeasured** and must be tested (fork `--fast` has ~33 ms/frame
  headroom) before PersonaPlex is enabled; the policy should be "PersonaPlex session active ⇒ background
  autonomy LLM work paused".
- Do not set vLLM `--gpu-memory-utilization` high for any audio service on unified memory (see §6.5 failures).

## 9. Reproduction

All scripts live in the session scratchpad (`…/scratchpad/v07/voice/scripts/`): `kokoro_rtf.py`,
`asr_roundtrip.py`, `asr_files.py`, `fw_cpu.py`, `pp_bench.py`, `stubs/sphn.py`, task lists `tasks1.json`,
`tasks2.json`. Upstream clone: `src/personaplex` (`16419a2`); fork clone: `src/personaplex-jethac` (`a0e170b`,
branch `pgx-plan`). They are evidence for this audit, not product code; the implementation lane should copy what
it needs into `src/`/`tests/` with tests. Raw outputs (logs, JSON, WAVs) are under `…/v07/voice/out` and
`…/v07/voice/pp_out`; WAVs are synthetic or model speech, no owner audio was recorded.

```bash
V=<scratchpad>/v07/voice
# Kokoro + Whisper container (removed afterwards)
docker run -d --name nsf-voice-smoke --device nvidia.com/gpu=all -p 127.0.0.1:18431:8000 \
  -v $HOME/.cache/huggingface:/kokoro-hf:ro -v $V/hfcache:/root/.cache/huggingface \
  -v $V/scripts:/scripts:ro -v $V/out:/out --entrypoint sleep vllm/vllm-openai:nightly infinity
docker exec nsf-voice-smoke pip install "kokoro==0.9.4" soundfile librosa jiwer
docker exec -e HF_HUB_CACHE=/kokoro-hf/hub -e HF_HUB_OFFLINE=1 nsf-voice-smoke python3 /scripts/kokoro_rtf.py cuda /out
docker exec -e HF_HUB_CACHE=/kokoro-hf/hub -e HF_HUB_OFFLINE=1 -e CUDA_VISIBLE_DEVICES= nsf-voice-smoke \
  python3 /scripts/kokoro_rtf.py cpu /out 8
docker exec -d nsf-voice-smoke bash -c 'vllm serve openai/whisper-large-v3-turbo --host 0.0.0.0 --port 8000 \
  --gpu-memory-utilization 0.08 --kv-cache-memory-bytes 2147483648 --enforce-eager --max-num-seqs 4'
docker exec nsf-voice-smoke python3 -I /scripts/asr_roundtrip.py http://127.0.0.1:8000 /out/kokoro_cuda_ 3
docker exec nsf-voice-smoke pip install faster-whisper
docker exec nsf-voice-smoke python3 -I /scripts/fw_cpu.py small.en /out/kokoro_cuda_ 8
docker rm -f nsf-voice-smoke

# PersonaPlex (weights at ~/.local/share/neuroscifi/models/personaplex-7b-v1, mounted read-only)
docker run -d --name nsf-pp-smoke --device nvidia.com/gpu=all -p 127.0.0.1:18431:8000 \
  -v $HOME/.local/share/neuroscifi/models/personaplex-7b-v1:/model:ro -v $V/src/personaplex:/repo:ro \
  -v $V/src/personaplex-jethac:/fork:ro -v $V/scripts:/scripts:ro -v $V/pp_out:/out \
  -v $V/hfcache:/root/.cache/huggingface --entrypoint sleep vllm/vllm-openai:nightly infinity
docker exec nsf-pp-smoke pip install soundfile librosa jiwer
docker exec nsf-pp-smoke python3 -I /scripts/pp_bench.py converse /model /repo/moshi /out/converse \
  /repo/assets/test/input_assistant.wav NATF2.pt
docker exec nsf-pp-smoke bash -c 'python3 -I /scripts/pp_bench.py multi /model /repo/moshi /out/multi1 "$(cat /scripts/tasks1.json)"'
docker exec -e PP_PIN=0 nsf-pp-smoke bash -c 'python3 -I /scripts/pp_bench.py multi /model /repo/moshi /out/pin0 "$(cat /scripts/tasks2.json)"'
docker exec -e PYTHONPATH=/fork/moshi:/scripts/stubs nsf-pp-smoke python3 /fork/bench/bench.py --fast --frames 400 \
  --no-dmon --pin-cores none --moshi-weight /model/model.safetensors \
  --mimi-weight /model/tokenizer-e351c8d8-checkpoint125.safetensors --tokenizer /model/tokenizer_spm_32k_3.model \
  --voice-prompt NATF2.pt --voice-prompt-dir /out/converse/voices --out /out/fork_fast   # and without --fast
docker rm -f nsf-pp-smoke
```

### 9.1 Concrete services for the implementation lane

Ports below are free loopback ports on this host (8000/9000/3000/8811/8888 and the other published ports are
taken). Every service binds `127.0.0.1` only.

1. **ASR** — derived image `FROM vllm/vllm-openai:nightly` + `pip install soundfile librosa` (the base image has
   neither, and vLLM's transcription endpoint needs them), with a constraints file pinning `torch==2.14.0` and
   `nvidia-nccl-cu13==2.31.2`. Run `serve openai/whisper-large-v3-turbo --gpu-memory-utilization 0.08
   --kv-cache-memory-bytes 2147483648 --enforce-eager --max-num-seqs 4`, published `127.0.0.1:18431:8000`.
   Health = a transcription of a fixed WAV, not `/v1/models`. Start-up ≈ 85 s.
2. **TTS** — derived image with `kokoro==0.9.4 soundfile`, same constraints, `en_core_web_sm` installed at build
   time, Kokoro weights mounted read-only from the HF cache, `HF_HUB_OFFLINE=1`; a ~50-line HTTP wrapper (to be
   written with tests) on `127.0.0.1:18432` returning 24 kHz PCM/WAV per sentence. CPU fallback with
   `CUDA_VISIBLE_DEVICES=`.
3. **PersonaPlex (experimental)** — only if the owner opts in: derived image with a Rust toolchain to build `sphn`
   (Opus), the upstream code plus PR #102/#103 changes (reviewed, pinned by commit), the `--host` bind fix, an
   allow-list for `voice_prompt`, weights mounted read-only, published `127.0.0.1:18998`. NeuroSciFi talks to it
   through its own bridge, never exposing `/api/chat` to Pocket devices directly.
