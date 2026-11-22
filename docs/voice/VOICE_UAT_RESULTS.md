# Voice UAT results (v0.7.0, area 6)

Measured 2026-11-21 on the reference host (DGX Spark GB10, aarch64, 121.9 GiB unified memory) with the deployed
services: `neuroscifi-voice-asr` (Whisper large-v3-turbo, vLLM), `neuroscifi-voice-tts` (Kokoro-82M),
`neuroscifi-vllm` (Qwen3-30B-A3B-Instruct-2507-FP8), `neuroscifi-searxng`. Browser: headless Chromium
(`NSF_CHROMIUM=/usr/bin/chromium`) with `--use-fake-device-for-media-stream
--use-file-for-fake-audio-capture=<WAV spoken by the deployed Kokoro, voice am_michael, 48 kHz>`, so real audio
goes through getUserMedia → AudioWorklet → VAD → upload → Whisper → bridge → research/LLM → Kokoro → WebAudio.
Raw numbers: `test-results/voice_uat.json` (written by `tests/e2e/test_voice_e2e.py`; two full runs, values below
list both, plus the first development run where marked).

**Not testable here (no hardware):** a physical microphone, speakers, room acoustics, echo cancellation against
real loudspeaker output, the owner's voice/accent/noise, far-field audio, real handsets (iOS/Android background
behaviour, Bluetooth headsets), HTTPS with a real LAN certificate on a phone. Fake capture feeds clean synthetic
speech: these WERs prove the plumbing, not recognition quality on the owner's speech.

## 1. Commands

```
deploy/voice/run.sh start all && deploy/voice/run.sh wait all 600
nsf voice health --db INSTANCE
.venv/bin/python -m pytest -q tests/voice tests/console
NSF_CHROMIUM=/usr/bin/chromium .venv/bin/python -m pytest -q tests/e2e/test_voice_e2e.py
NSF_VOICE_DEGRADED=1 NSF_CHROMIUM=/usr/bin/chromium .venv/bin/python -m pytest -q tests/e2e/test_voice_e2e.py -k degraded
```

## 2. Results

| Measure | Result | How |
|---|---|---|
| WER, 5 spoken sentences through real capture (mode B) | **0.0 %** corpus WER (5/5 exact), both runs | `test_voice_wer_and_mic_off_stops_capture` |
| WER, spoken research question | 0.0 % (3 runs) | `test_voice_research_question_…` |
| End-of-utterance (VAD) → final transcript in the page | 175–406 ms (run 1), 167–372 ms (run 2); 163–214 ms for the research question | client timestamps |
| End-of-utterance → first audio of the reply (acknowledgement "I'm researching that now…") | **295 / 332 ms** (runs 1/2), 338 ms (development run) | VAD end → scheduled start of the first WebAudio source |
| End-of-utterance → first audio of the **research answer** (SearXNG + web pages + Qwen3 answer + guard + Kokoro) | **28.5 s / 27.7 s** (runs 1/2), 37.8 s (development run) | same; answer source `model` (passed the evidence guard) every time |
| Barge-in: onset of the user's speech → playback stopped | **363 ms** (4 runs: 362.3, 363.0, 363.2, 363.2) = 300 ms hold + frame granularity | `metrics.bargeIns` |
| Barge-in: detection → all sources stopped | 0.0–0.1 ms | same |
| Brief overlap (150 ms burst while speaking) | ignored (overlap counted, playback continued), every run | `stats.overlaps == 1`, `barge_ins == 1` |
| Interrupted reply recorded | segment `interrupted`, `spoken_until` set; the interrupting "Please stop." transcribed and handled as the stop command | DOM + server metrics |
| Mic off mid-utterance | **0** live tracks, stream released, **0** audio requests in the next 9 s while the fake device kept speaking (43 uploads before) | Playwright request counter |
| Server (hub) timings, barge scenario | ASR 150–186 ms per final; final audio received → reply ready 164–199 ms; → first sentence synthesised 367–435 ms (one long help sentence) | `GET /api/voice/status` metrics |
| `nsf voice health` round trip | Kokoro 89 ms + Whisper 204 ms for a 5.1 s sentence, WER 0.0 | CLI |
| Mode switching A→B→C→A→C→B→A | context kept: "show the evidence" afterwards cites the same task; mic button disabled in C | e2e + unit |
| Degraded: both speech containers stopped | status shows ASR `unavailable`, TTS `unavailable`; typed questions answered in text-only and in mode A (with "Speech output is unavailable") | `test_voice_degraded_text_only_still_works`, containers restarted afterwards |
| Pocket over HTTPS (self-signed cert, `isSecureContext` true) | paired phone captured and transcribed "Show me the evidence, please." through its paired session; nothing in localStorage | `test_voice_pocket_https_…` |
| One capturing device | console mic-on refused while the phone held the lease ("in use on Pocket device"); hand-off only after Confirm; the phone stopped its track within one renewal (≤ 5 s) and said "moved to Owner console" | same |
| Service start-up | ASR 113 s cold (first ever), ~30 s warm; TTS 13–25 s | `run.sh wait` |
| GPU memory | ASR engine 4 156 MiB, TTS 876 MiB (`nvidia-smi --query-compute-apps`); containers 3.7 GiB / 2.0 GiB cgroup; Qwen3 44 096 MiB alongside | measured idle after tests |

Latency budget of the 330 ms acknowledgement (from the page's end-of-utterance event): final chunk upload +
Whisper ≈ 160–210 ms, bridge (command parse + `orchestrator.submit`) a few ms, Kokoro first sentence ≈ 60–110 ms,
WAV fetch + decode + scheduling the rest. From the owner's **last word** add the 700 ms VAD hang-over.

## 3. Test counts

- `tests/voice` (unit/integration, scripted engines; one test runs the real research worker offline): **71 passed**.
- `tests/voice tests/console`: **127 passed**.
- `tests/e2e/test_voice_e2e.py`: **4 passed + 1 opt-in** (degraded; passed when run with `NSF_VOICE_DEGRADED=1`).
- Unchanged suites re-run after the shared edits: Pocket/console e2e (`test_console.py`, `test_pocket*.py`) pass;
  `tests/instance` 200 passed with schema v9; `tests/js/offline_check.mjs` passes (shell count updated to 30).

## 4. Known limits seen during UAT

- Thresholds were tuned on synthetic speech; real-room barge-in false positives/negatives are unmeasured.
- The answer latency is dominated by research (search + page reads + 2 model calls); it is acknowledged at once and
  the transcript shows stage progress.
- Whisper occasionally returns "Thank you." / "you" for noise; such finals are dropped (`_noise`).
- Checkpoints copy the instance database, so a voice session forgotten after a checkpoint still exists in older
  checkpoint files until they are pruned (voice forgets are not replayed on restore). Resolved since: migration 11
  replays voice tombstones on restore, and from 0.7.0 a voice forget rewrites every archive it made stale without
  the session (`docs/operations/RUNBOOK.md` §6).

## RC2 voice follow-ups (2026-11-21, fake-device audio)

All audio below is **fake-device**: Chromium `--use-fake-device-for-media-stream
--use-file-for-fake-audio-capture=<WAV>` playing speech the deployed Kokoro (am_michael) synthesised, so the
recognition numbers prove the plumbing, not recognition of the owner's own voice in a room. Services were live on
loopback: Whisper (ASR :18431), Kokoro (TTS :18432), Qwen3-30B-A3B (vLLM :8811), SearXNG (:8888).

### T2-01: TTS round trip of the reported sentences

Kokoro speaks each sentence and Whisper transcribes the reply WAV back (24 kHz to 16 kHz). Corpus WER is over
the four sentences ("This is NeuroSciFi.", "The word agentic is new, and NeuroSciFi can say it.", "Kubernetes and
PyTorch are tools.", "According to ycombinator.com, agentic tools are spreading.").

| TTS server | Pronunciation pass | Corpus WER | What Whisper heard |
|---|---|---|---|
| RC1 container (image built 07:26, `/tmp` noexec, no espeak-ng fallback) | RC1 (`Neuro Sci-Fi` respelling) | **30.8 %** | "This is" / "The word is new and can say it." / "and torch our tools." / "According to .com, tools are spreading."; the words NeuroSciFi, agentic and Kubernetes alone gave **0 s** of audio |
| RC1 container | RC2 (inline phonemes) | 7.7 % | every word spoken; the only mismatch is the spelling "Y Combinator.com" |
| rebuilt image, `/tmp` exec (fallback loaded) | RC1 | 11.5 % | "Accombinator.com" for ycombinator.com; the rest exact |
| rebuilt image | RC2 | **7.7 %** | "This is NeuroSciFi." / "... and NeurosciFi can say it." / "Kubernetes and PyTorch are tools." / "According to Y Combinator.com, agentic tools are spreading." |

Root cause: the running container was still the RC1 deployment. Its `/tmp` was mounted noexec and its
`tts_server.py` predated 976946f, so misaki had no out-of-vocabulary fallback. The image was rebuilt from
`deploy/voice` and only the TTS container was restarted with `run.sh` (`/tmp` exec). That took 28 s of TTS
downtime, and `nsf voice health` was ready afterwards (round-trip WER 0.0, Kokoro 65 ms, Whisper 234 ms).
`engines.speakable` now sends the names in `engines.PHONEMES` as misaki inline phonemes. Each phoneme string is
what misaki's own espeak fallback gives for that word, and each was checked by round trip. The names are therefore
spoken even by a server whose fallback failed (defence in depth). Domains are still read as "x dot com".

### S7: the spoken research request, end to end (two runs)

"NeuroSciFi, research the latest developments in agentic AI and summarize their importance." Kokoro now says it
in full, so the original wording was used.

| | Run 1 | Run 2 |
|---|---|---|
| Heard (Whisper) | "NeurosciFi. Research the latest developments in agentic AI and summarize their importance." (WER 0) | same (WER 0) |
| Task objective / context | `the latest developments in agentic AI` / `{"presentation": "summarize their importance"}` | same |
| End of utterance → final transcript | 392 ms | 404 ms |
| End of utterance → acknowledgement audio | 480 ms | 562 ms |
| End of utterance → answer audio | 17.7 s | 24.1 s |
| Model answer and guard | `model`: 1 sentence passed the guard, 3 rejected ("states C… (insufficient) as fact without hedging") | `model`: 2 passed, 3 rejected |
| Spoken text | "Some analysts believe Anthropic may outlast OpenAI amid increasing price competition, though growth is expected to slow." | "Anthropic's recent IPO saw its stock open 33% above the $83 price, …" + "According to one source, Anthropic is seen as more likely than OpenAI to survive the commoditization of agentic AI …" |
| Spoken reply transcribed back | identical (WER 0.0) | WER 1.7 % ("Anthropik's") |

The instruction reached the answer prompt as a style instruction and was not searched for. The guard ran
unchanged, and in both runs it removed unhedged sentences built on insufficient claims. RC1 spoke an unrelated
extractive quote in both of its runs. In RC2 both runs spoke model text that passed the guard. When no sentence
passes, voice now speaks `reasoning.unanswered` ("The sources read do not establish an answer to: …") and puts the
quotes below it in the text (unit test). The answer's relevance is limited by what SearXNG returned (IPO and
pricing news about one vendor), which is a research-quality limit and not a voice defect. Raw records:
`scratchpad/v07/fv2work/s7_run{1,2}.json`.

### S8: barge-in and Presence F (`test_voice_barge_in_stops_playback`, two runs)

| | Run 1 (alone) | Run 2 (full e2e suite) |
|---|---|---|
| User speech onset → playback stopped (includes the 300 ms hold) | 363.4 ms | 287.8 ms |
| Detection → all sources stopped | 0 ms | 0.1 ms |
| 150 ms burst while speaking | ignored (overlaps 1, barge-ins 1) | same |
| Heard back | "What can you do?", "Please stop." (handled as the stop command) | same |
| Client stop → `nsf:voice` event with `interrupted: true` | 0.5 ms, exactly 1 event | 0.1 ms, 1 event |
| Client stop → Presence intent shows `user_interruption` | 176.6 ms | 26.9 ms |
| `user_interruption` before the barge-in | none | none |
| `user_interruption` shown for | 3.4 s (the client's 1.5 s, then the server's 3 s window) | 3.4 s |
| Server voice state `interrupted_at` set | yes | yes |
| Hub averages | ASR 135 ms, reply 151 ms, first sentence 389 ms | ASR 194 ms, reply 194 ms, first sentence 428 ms |

The client and the server now agree on interruptions. A barge-in or stop that cuts a playing reply is marked
`interrupted: true` on the page's `nsf:voice` event at the same moment `/api/voice/interrupt` is called. A reply
that simply finishes is not marked (`test_presence.py`). The new assertions fail on the RC1 client: it sent no
`interrupted` flag, so 0 events were recorded.

### Other RC2 voice fixes (unit-tested)

- A follow-up question that only points back ("Is that version secure?") takes its subject from the conversation's
  previous question, as the console does. With no earlier question, the reply asks which one is meant and no search
  starts.
- An instruction such as "… and summarize their importance" is task context `presentation`, not part of the search
  terms. It is privacy-screened like the objective, kept on resume, given to the answer prompt as a style
  instruction only, and recorded in `report.reasoning.presentation`. The guard is unchanged.

### Test counts (RC2 voice lane)

- `pytest -q` (everything but e2e): **2155 passed**, 6 skipped.
- `tests/e2e/test_voice_e2e.py tests/e2e/test_presence.py tests/e2e/test_console*.py tests/e2e/test_pocket*.py`:
  **146 passed**, 1 skipped (the opt-in degraded scenario).
- `ruff check .` clean; `node tests/js/offline_check.mjs` passed.
