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
  checkpoint files until they are pruned (voice forgets are not replayed on restore).
