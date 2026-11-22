# Voice validation on recorded human speech (v0.7.0)

This closes the part of the limitation "voice is verified only with synthetic audio" that can be closed without
the owner's hardware, and gives the owner a guided test for the part that cannot. It has two halves:

1. **Corpus validation.** Recorded human speech, noise, music and room impulse responses from public corpora go
   through the product's own ASR client, VAD and command grammar. Measured on the reference host on 2026-11-21. Raw
   numbers: [`docs/quality/evidence/voice_real_speech.json`](../quality/evidence/voice_real_speech.json).
2. **Owner device self-test ("Voice check").** It runs once on the owner's real microphone, speaker and phone, from the
   console Voice panel, from Pocket, or with `nsf voice selftest --report PATH`. It saves an evidence JSON on the
   NeuroSciFi host. See [section 6](#6-owner-device-self-test-voice-check).

**What is still not verified.** The corpus half simulates echo, room and telephone channels. No physical
loudspeaker, microphone, browser echo canceller or handset was involved, and the e2e tests of the Voice check use a
**fake device**. Until the owner has run the Voice check on real hardware and its report is filed, the README
limitation stays open in this narrower form.

## 1. Corpora (downloaded outside the repository)

All corpora are in `~/.local/share/neuroscifi/datasets/`, each in its own directory. They were extracted and decoded
with `tar`/`unzip`/`ffmpeg`, and nothing from them was executed. No audio is in the repository. In total about 6.3 GB
was downloaded, using subsets. The licence of each corpus was checked on its source page on 2026-11-21.

| Corpus | Used for | Licence (source page) | URL |
|---|---|---|---|
| LibriSpeech test-clean, test-other (OpenSLR 12) | read speech, 73 speakers; base for noise, reverberation, far-field and telephone conditions; VAD onsets; double talk | CC BY 4.0 | https://www.openslr.org/12/ |
| Crowdsourced UK and Ireland English Dialect (OpenSLR 83): Irish male, Midlands, Northern, Scottish, Welsh female | dialect WER | CC BY-SA 4.0 | https://www.openslr.org/83/ |
| EdAcc, Edinburgh International Accents of English Corpus: 3 of the 10 test shards (Hugging Face mirror by the authors) | accented, conversational English, 13 first languages | CC BY-SA 4.0. The first DataShare item was withdrawn over a licence correction; the current item (doi:10.7488/ds/7914) ships `CC_BY_SA_license_text.txt`, and the mirror's card states CC BY-SA | https://huggingface.co/datasets/edinburghcstr/edacc · https://doi.org/10.7488/ds/7914 |
| DEMAND (10 environments, 16 kHz, channel 1) | additive noise; noise-only false triggers | CC BY 4.0 (Zenodo record metadata) | https://zenodo.org/records/1227121 |
| MUSAN music, first 63 files of the `music/fma` folder (partial stream of the 11 GB archive) | music-only false triggers | CC BY 4.0 (OpenSLR page). Individual tracks carry their own Creative Commons terms in the corpus | https://www.openslr.org/17/ |
| RIRS_NOISES (OpenSLR 28): REVERB-2014 real RIRs (near and far), AIR, simulated small/medium/large rooms, isotropic room noise | reverberation, far field, echo path | Apache 2.0 | https://www.openslr.org/28/ |
| Speech Commands v0.02 test set | real recordings of "stop", "no", "yes", "go" and 6 other words | CC BY 4.0 (README in the archive) | http://download.tensorflow.org/data/speech_commands_test_set_v0.02.tar.gz |

**No open accented-English corpus was available without an account apart from EdAcc.** SLURP (CC BY-NC audio), L2-ARCTIC
(CC BY-NC) and Speech Accent Archive (CC BY-NC-SA) are not permissive. Common Voice now needs an account.

## 2. Method

**ASR path.** Every transcription went to the live Whisper large-v3-turbo service (vLLM, 127.0.0.1:18431). It used
the product's client, `voice.engines.WhisperASR.transcribe`, with 16 kHz PCM16, `language=en` and
`temperature=0`, exactly what the hub sends.

**Levels.** Active speech level is set to −26 dBFS. The VAD sets vary it between −34 and −20 dBFS, standing in for
different microphones and distances. The browser's AGC and noise suppression are **not** modelled.

**Scoring.** Text is lower-cased, punctuation is removed, numbers are spelled out ("1984" → "nineteen eighty four"),
"mr" → "mister", and fillers (um, uh, …) are dropped from both sides. This is a light normaliser, not Whisper's
English normaliser. Spelling variants such as "wanna" against "want to" (EdAcc) and British against American
spellings still count as errors.

**Corpus WER.** Corpus WER is the sum of errors over the sum of reference words. The 95% interval comes from 1000
bootstrap resamples of whole speakers. A group with one speaker has no speaker variance, so its interval collapses to
the point estimate.

**Rates.** VAD, echo and command rates have Wilson 95% intervals (in the JSON).

**Conditions.**
- **Noise.** DEMAND noise from a random environment and offset, scaled to the stated SNR against active speech.
- **Reverberation.** Convolution with a REVERB-2014 *near* or AIR RIR, or a simulated small or medium room RIR,
  aligned to the direct path.
- **Far field.** A REVERB-2014 *far* RIR or a simulated large room, a level 12 dB lower, and recorded isotropic room
  noise at 15 dB SNR.
- **Telephone.** A 300–3400 Hz band-pass, 8 kHz sampling and G.711 μ-law, then back to 16 kHz, as a headset or call
  bridge delivers it.

**VAD.** The product VAD was extracted into one module, `voice/web/vad.mjs`. The browser client uses it, and the
harness replays it through Node (`scripts/voice_vad_replay.mjs`) over 20 ms frame RMS values, computed as
`capture-worklet.mjs` computes them. The terminal self-test uses the Python mirror `voice/selftest.py: Vad`. It
reads its constants from `vad.mjs`, and a test checks that the two agree frame by frame. Latency is measured from an
oracle onset (the first frame of the clean signal within 20 dB of its active level).

**Echo simulation.** NeuroSciFi's own replies were synthesised by the live Kokoro service with the default voice
`af_heart`: 8 replies, 85 s. The echo path is a mild loudspeaker non-linearity (tanh), a recorded RIR, 20–150 ms of
delay, and the **echo-to-speech ratio (ESR)**. ESR is the echo level at the microphone relative to −26 dBFS user
speech. Room noise sits 25 dB below the speech. Two ESR values stand for working echo cancellation:

- −20 dB: a converged canceller;
- −10 dB: a weak or partly converged one.

0 dB and +6 dB stand for **no** echo cancellation, with a speaker as loud as the user or louder. Double talk adds a
real LibriSpeech sentence, or a real Speech Commands "stop", at a random time during the reply.

**TRAIN/TEST split.** Items are hashed to the two splits by speaker, noise environment, music file and RIR. A
double-talk item is in TEST only when both its echo key and its speaker are TEST. Two grids were scored on TRAIN only,
in order:

1. **Listening:** `SPEECH_K` × `SPEECH_MIN` × `MIN_SPEECH_MS` × `FLOOR_ALPHA`, 96 points.
2. **Barge-in:** `BARGE_K` × `BARGE_MIN` × `BARGE_MS`, 100 points, scored on top of the listening winner.

The selection rule comes first: the constraints, then a cost. The rule is in `listen_objective` and
`barge_objective`.

- **Listening constraint:** false utterances from noise at SNR 20 and 10 dB at most 1/min.
- **Barge-in constraints:** own echo interrupts at most 5% of replies at ESR ≤ −10 dB; noise during playback at SNR
  ≥ 10 dB at most 0.5/min; background music at −36 dBFS at most 1/min.

TEST was replayed once with the old thresholds and once with the winners. `vad.mjs` ships the winners, and the JSON
records `shipped_equals_tuned: true`.

**Procedure deviations, stated plainly.**
- The first tuning pass printed TEST rows next to TRAIN rows. TEST was not used to choose anything, and the grids were
  widened after looking at TRAIN only.
- A first combined grid evaluated barge points on top of whatever listening values `vad.mjs` held at the time. It was
  replaced by the explicit sequential procedure above, and the shipped values come from that.

## 3. Word error rate per condition

Whisper large-v3-turbo through `WhisperASR.transcribe`, with corpus WER and speaker-bootstrap 95% intervals.

| Condition | Utterances | Speakers | Ref. words | WER | 95% CI |
|---|---:|---:|---:|---:|---|
| clean, read speech (LibriSpeech test-clean) | 320 | 40 | 7014 | 2.1% | 1.6–2.6% |
| clean, harder speakers (LibriSpeech test-other) | 264 | 33 | 4966 | 4.2% | 3.1–5.3% |
| noise, SNR 20 dB (DEMAND) | 320 | 40 | 7014 | 2.4% | 1.8–3.1% |
| noise, SNR 10 dB (DEMAND) | 320 | 40 | 7014 | 2.5% | 1.9–3.0% |
| noise, SNR 5 dB (DEMAND) | 320 | 40 | 7014 | 3.0% | 2.3–3.7% |
| reverberation (real + simulated near RIRs) | 320 | 40 | 7014 | 2.6% | 1.9–3.4% |
| far field (far RIR, −12 dB, room noise 15 dB) | 320 | 40 | 7014 | 3.2% | 2.5–4.0% |
| telephone band (8 kHz, G.711 μ-law) | 320 | 40 | 7014 | 2.1% | 1.7–2.7% |
| UK and Irish dialects, read (OpenSLR 83) | 300 | 8 | 4022 | 4.9% | 2.7–8.1% |
| international accents, conversational (EdAcc) | 311 | 26 | 4302 | 18.6% | 15.9–22.6% |

**Dialects (OpenSLR 83):**

| Group | Utterances | Speakers | WER |
|---|---:|---:|---:|
| Irish English, male | 60 | 3 | 10.5% |
| Northern English, female | 60 | 5 | 4.8% |
| Scottish English, female | 60 | 6 | 4.1% |
| Midlands English, female | 60 | 2 | 2.7% |
| Welsh English, female | 60 | 8 | 2.4% |

**EdAcc by first language:**

| First language | Utterances | WER |
|---|---:|---:|
| Vietnamese | 25 | 31.5% |
| Romanian | 3 | 31.8% |
| Jamaican English | 23 | 28.5% |
| Sinhalese | 25 | 25.6% |
| Catalan | 25 | 20.5% |
| Scottish English | 25 | 20.3% |
| Kenyan English | 25 | 19.2% |
| Spanish | 25 | 19.0% |
| Hebrew | 25 | 18.0% |
| Indian English | 7 | 18.0% |
| Mandarin | 22 | 15.5% |
| Mainstream US English | 6 | 13.2% |
| Italian | 25 | 12.5% |
| Bulgarian | 25 | 10.6% |
| Lithuanian | 25 | 6.7% |

Many groups have 1–3 speakers; per-group intervals are in the JSON.

**Reading the WER results.**
- Whisper large-v3-turbo is robust to additive noise down to 5 dB SNR, to reverberation and to the telephone band on
  read speech: every condition is within about 1 point of clean.
- Accents and spontaneous speech are the weak spot. EdAcc's 18.6% mixes three causes:
  - accent;
  - conversational style (disfluencies, overlaps): even its "Mainstream US English" speaker scores 13.2%;
  - reference conventions: the top substitutions are "wanna → to" and "wanna → will".
  
  Several first-language groups have one speaker, so per-group numbers are anecdotal.
- The single Irish-English group (10.5%, 3 speakers) is the worst read-speech result.

## 4. VAD, barge-in and echo on real speech: before and after retuning

| | Thresholds |
|---|---|
| **Before (tuned on synthetic speech)** | listening: voiced above max(3 × floor, 0.012), confirmed after 250 ms; barge-in: max(5 × floor, 0.03) for 300 ms |
| **After (shipped)** | listening: max(4 × floor, 0.006), 160 ms; barge-in: max(2 × floor, 0.03), 250 ms |

The floor adaptation (α 0.05, capped at 0.05), the 700 ms end-of-utterance silence and the 300 ms pre-roll are
unchanged. TRAIN has 747 sequences and TEST 535. Latencies are p50, measured from the oracle onset. Rates are per
segment or per reply; false triggers are per minute.

| Measure | TRAIN before | TRAIN after | TEST n | TEST before | TEST after (shipped) |
|---|---:|---:|---:|---:|---:|
| sentence onset detected, quiet room (SNR 30 dB) | 100% (300 ms) | 100% (160 ms) | 29 | 100% (280 ms) | 100% (140 ms) |
| … SNR 20 dB | 100% (320 ms) | 100% (200 ms) | 34 | 100% (350 ms) | 100% (240 ms) |
| … SNR 10 dB ¹ | 100% | 93% | 36 | 100% | 97% |
| … SNR 5 dB ¹ | 98% | 78% | 29 | 100% | 86% |
| single command word detected (real "stop", "no", "yes", "go") | 60% (260 ms) | 89% (220 ms) | 139 | 60% (260 ms) | **87%** (200 ms) |
| false utterances/min, noise only, SNR 20 dB | 0.17 | 0.33 | 8 | 0.75 | 1.00 |
| … SNR 10 dB | 2.00 | 1.00 | 8 | 2.25 | 1.50 |
| … SNR 5 dB | 1.83 | 2.00 | 8 | 1.75 | 1.75 |
| false utterances/min, background music −36 dBFS | 2.97 | 2.26 | 30 | 2.87 | 2.27 |
| … music −30 dBFS | 2.52 | 2.32 | 30 | 2.47 | 2.47 |
| false barge-ins/min while speaking, noise SNR 20 dB | 0.00 | 0.00 | 8 | 0.00 | 0.00 |
| … SNR 10 dB | 0.00 | 0.50 | 8 | 0.50 | **2.25** |
| … SNR 5 dB | 0.67 | 4.33 | 8 | 0.50 | **3.75** |
| false barge-ins/min while speaking, music −36 dBFS | 0.07 | 0.58 | 30 | 0.07 | 0.53 |
| … music −30 dBFS | 2.26 | 4.52 | 30 | 2.07 | **4.13** |
| replies falsely interrupted by own echo, ESR −20 dB | 0% | 0% | 19 | 0% | 0% |
| … ESR −10 dB | 0% | 0% | 7 | 0% | 0% |
| … ESR 0 dB (no echo cancellation) | 100% | 100% | 16 | 100% | 100% |
| … ESR +6 dB (no echo cancellation) | 100% | 100% | 7 | 100% | 100% |
| user sentence during a reply detected (double talk), ESR −20 dB | 77% (630 ms) | 85% (480 ms) | 11 | 100% (500 ms) | 100% (460 ms) |
| … ESR −10 dB | 26% (860 ms) | 100% (340 ms) | 5 | 40% (310 ms) | **100%** (760 ms) |
| … ESR 0 / +6 dB | 0% | 0% | 8 / 6 | 0% | 0% (falsely interrupted first) |
| real word "stop" during a reply detected, ESR −20 dB | 6% | 19% (500 ms) | 8 | 0% | **0%** |
| … ESR −10 dB | 0% | 43% (680 ms) | 3 | 0% | 67% (620 ms) |
| … ESR 0 / +6 dB | 0% | 0% | 6 / 4 | 0% | 0% |

¹ At SNR 10 and 5 dB the noise had often opened an utterance already, so the speech joined it. Latency is then 0 and
not meaningful.

**What this shows.**
- **The old thresholds failed on real speech.** 40% of real single command words ("no", "stop") never became an
  utterance at all. While NeuroSciFi spoke with a weak echo canceller (ESR −10 dB), only 26–40% of user sentences
  and none of the real "stop" words interrupted it. The 5 × floor barge threshold sits above normal speech level once
  the floor has adapted to the echo.
- **After retuning,** on held-out TEST data:
  - word detection rose to 87%;
  - double talk at ESR −10 dB rose to 5/5 and at −20 dB stayed at 11/11;
  - onset latency fell by about 100 ms;
  - noise-only false utterances at SNR 10 dB fell from 2.25 to 1.5/min.
- **The cost is false barge-ins from loud background sound while NeuroSciFi is speaking.**
  - Noise at SNR 10 dB went from 0.5 to 2.25/min, and at 5 dB from 0.5 to 3.75/min.
  - Music at −30 dBFS went from 2.1 to 4.1/min.
  - On TRAIN, noise at 10 dB met its 0.5/min constraint. TEST, with other environments, did not, so this constraint
    did not generalise.
  - A false barge-in stops playback. "go on" or "continue" resumes it, and both are now understood.
- **Energy VAD cannot separate NeuroSciFi's own voice from the user when echo cancellation is missing.**
  - At ESR 0 dB or louder, every reply interrupts itself, with either set of thresholds. The browser's
    `echoCancellation: true` is therefore load-bearing, and the Voice check's echo step tests it on the owner's
    hardware.
  - Headphones remove the problem.
- **A real, short "stop" during playback is still unreliable.** On TEST it was detected 0/8 at ESR −20 dB and 2/3 at
  −10 dB. Saying "stop talking", "please stop" or "be quiet", or pressing *Mute speech*, works better. The Voice
  check's barge step measures this on the owner's voice.
- **Next step, not done here:** a reference-aware barge detector. It would compare the microphone with the playback
  signal the client already has (a Geigel-style double-talk detector), so that echo does not raise the threshold the
  user's voice must cross.

## 5. Command grammar

The grammar was literal: regular expressions over normalised text. It now tries, in order:

1. the literal grammar;
2. a **canonical** form: lead and tail filler dropped ("um, okay, could you … for a second please"), repeats
   collapsed except "no no";
3. a **synonym** set: "hold on", "hang on", "continue", "where did you get that", …;
4. **frames**: a command verb plus what it acts on ("cut the audio, I'll read it myself", "what are your sources for
   that");
5. **clause splitting** for utterances of at most 12 words, where exactly one clause is a command and no clause asks
   something. A bare "no" inside a longer sentence never counts: "No, I can't remember." is not a refusal;
6. **fuzzy matching**: difflib ratio ≥ 0.84, at least 0.08 ahead of every other command, at most 6 words.

Two safety rules apply on top:

- "confirm" is matched only by its literal words, never fuzzily.
- A fuzzy match of a command with an effect beyond speech (cancel or continue research, mode, disabling autonomy)
  asks first: *I heard "…". Did you mean: …? Say confirm, or press Confirm.* The sensitive commands (forget,
  enable autonomy, remember, hand-off, disclosure) ask for confirmation in every case, as before.

**Paraphrase sets** (`tests/voice/data/`):
- **author**: 120 commands and 50 ordinary sentences, written by the author of the synonyms before writing them. Not
  held out.
- **independent**: 67 commands and 30 ordinary sentences, written by a separate model instance from the intent
  descriptions only. Its misses were seen once and motivated the frames, so it is not held out.
- **fresh**: 56 commands and 26 ordinary sentences in ASR style, written by a third instance **after** the grammar was
  frozen and evaluated once. This is the **held-out** number. A later change, which stopped "no" inside long
  sentences from counting as a refusal, was driven by the real-transcript false-command check below and left this
  score unchanged.

| Intent accuracy | before | after |
|---|---:|---:|
| fresh, held out: text | 21.4% (12/56) | **66.1% (37/56)** |
| fresh, held out: spoken by 2 Kokoro voices, through Whisper, quiet / room noise 10 dB | 18.8% / 18.8% | 61.6% / 64.3% |
| independent: text / spoken (quiet) | 10.4% / 10.4% | 70.1% / 67.2% |
| author: text / spoken (quiet) / spoken (noise 10 dB) | 60.8% / 57.1% / 57.1% | 100% / 95.8% / 97.1% |
| typical ASR errors injected (punctuation, fillers, "quite" for "quiet", mined substitutions), fresh / independent | 11.2% / 3.7% | 50.5% / 48.5% |

The spoken rows use Kokoro voices other than NeuroSciFi's own, transcribed by Whisper. They are synthetic speech; the
real-speech rows follow. Whisper on the shared vLLM server is not bit-for-bit repeatable: the held-out spoken
accuracy was 64.3% / 67.0% in an earlier run of the same grammar.

| Real recordings (Speech Commands, through Whisper) | n | Whisper heard exactly the word | intent correct before / after |
|---|---:|---:|---:|
| "stop" → stop speaking | 150 | 147 | 147 / 147 |
| "no" → deny | 60 | 54 | 54 / 54 |
| "yes", "go", "up", "down", "left", "right", "on", "off" → no command | 220 | — | see JSON (no regression) |

| False commands (must not rise) | before | after |
|---|---:|---:|
| real ASR transcripts of real speech: every WER hypothesis above (LibriSpeech, OpenSLR 83, EdAcc) | 1 / 3115 ("No.") | **1 / 3115** ("No.") |
| ordinary questions and remarks, text, all three sets (106) | 0 | 0 |
| the same, spoken through Whisper | 0 | 0 |

The first version of the clause rule raised real-transcript false commands from 1 to 40, all of them refusals
("No, my little son, she said."). That was caught by this check and fixed before release.

**Still open:**
- The grammar is English only.
- About a third of freely phrased held-out commands are not recognised. They fall through to chatter or research, as
  before, so nothing wrong runs; NeuroSciFi just does not act. Typical misses:
  - long requests with a reason attached;
  - "link me to the studies";
  - "eli5 that";
  - "what topics did I research last week".

## 6. Owner device self-test ("Voice check")

The same check runs on three surfaces:
- the console's **Voice** panel;
- **Pocket**'s Voice tab (paired phone over HTTPS, with Pocket research on);
- the terminal: `nsf voice selftest --report PATH`.

It uses the same capture constraints (`echoCancellation`, `noiseSuppression`, `autoGainControl`), the same worklet,
the same VAD and the same output gain as a voice session. While it runs, the voice session's microphone is off.

| Step | What the owner does | What is measured | Pass |
|---|---|---|---|
| Microphone | allows the microphone, looks for the browser's or OS's capture indicator | permission, live tracks, device label, the echo cancellation / noise suppression / AGC the browser *actually* applied | granted, live, indicator seen |
| Read five sentences | reads each displayed sentence | Whisper WER and speech-to-noise ratio of each reading (uploaded only for scoring) | every WER ≤ 25% and SNR ≥ 15 dB |
| Silence | stays quiet 5 s | noise floor (median and p95 dBFS), VAD utterances started by noise | no utterance |
| Speaker | listens | output peak level, mute/volume state | owner heard it |
| Interrupt | says "stop" while NeuroSciFi talks (~20 s phrase) | barge-in detected; onset-to-stop latency | detected, ≤ 1.5 s, owner confirms it stopped |
| Echo | stays silent while it talks | false barge-ins caused by its own voice; echo level at the mic | none |
| Microphone off | watches the indicator | live and ended track counts; audio frames after stopping (a frame already in flight within 150 ms is tolerated, then 300 ms are watched); indicator out | no track live, at least one ended, no frame in the watched 300 ms, owner confirms; a failure names its reason (tracks still live, no track ended, or frames still arriving) |

Pocket also records the device and browser model: the user agent, `userAgentData` brands, platform, mobile flag,
`model` and `platformVersion` where the browser offers them, screen size, and the AudioContext base and output
latency.

**Privacy.**
- A reading is buffered in memory on the host, transcribed, scored and deleted at once, **whatever the retention
  setting**.
- The page zeroes its copy after upload.
- The report keeps numbers, the transcripts of the five displayed sentences, the device description and the answers.
- Reports are written to `<instance directory>/voice-selftest/selftest-<UTC>-<id>.json`, mode 0600, and offered as a
  download.
- Runs expire after 30 minutes, with at most 4 per principal. A Pocket device sees only its own runs.

### Exact owner steps

1. Start the voice services: `deploy/voice/run.sh start all && deploy/voice/run.sh wait all 600`.
2. **Computer.** Open the console, then **Voice** → **Voice check (microphone, speaker, interruptions)** → **Start
   voice check**. Use the microphone and speakers you normally use, at your normal distance, with headphones *off*
   for the first run.
   1. *Run: Microphone*. Allow the microphone, check the browser's microphone indicator, then answer.
   2. *Run: Read five sentences*. Read each sentence as it appears, at a normal pace.
   3. *Run: Silence*. Stay quiet.
   4. *Run: Speaker*. Answer whether you heard the sentence.
   5. *Run: Interrupt*. When it starts talking, wait 2–3 words, then say **"stop"** once, clearly. Answer whether it
      stopped.
   6. *Run: Echo*. Stay silent until it finishes.
   7. *Run: Microphone off*. Check that the indicator went out, then answer.
   8. Keep the downloaded report, or note the file name shown.
3. **Phone.** Pair Pocket over HTTPS with Pocket research on, open the **Voice** tab and run the same check. Do it
   twice: once with the phone's loudspeaker, holding the phone as for a call, and once with the earpiece or
   headphones if you use them.
4. **Terminal**, optional, on the host's own microphone and speaker:
   `nsf voice selftest --db INSTANCE --report ~/voice-selftest-host.json`. It uses `parecord`/`arecord` and
   `paplay`/`aplay`. This path has **no browser echo canceller**, so expect the echo step to fail on open speakers;
   that is useful as the "AEC off" worst case.
5. **Screen-reader spot checks on the phone.** Use TalkBack on Android (Settings → Accessibility → TalkBack) or
   VoiceOver on iOS (Settings → Accessibility → VoiceOver). During the Pocket Voice check:
   1. Swipe to the *Voice check* summary. It must be announced as a collapsed control; double-tap expands it.
   2. *Start voice check* must be announced as a button. After you activate it, the status line "Voice check started…"
      must be read out: it is an `aria-live` status.
   3. Each step's *Run: …* button must be reachable by swiping, and must be announced as dimmed while it is not the
      current step.
   4. During *Read five sentences*, the sentence to read must be announced when it appears (`aria-live=assertive`).
      Check that the screen reader's own speech does not count as your reading: pause the screen reader, or use
      headphones, while reading.
   5. The *Yes*/*No* question after a step must receive focus and be announced.
   6. After the last step, the verdict and the *Download the report* link must be reachable.
   7. Record what was not announced or not reachable in the report's file name or next to it, for example
      `selftest-…-talkback-notes.txt`.
6. Send the report files, or their names, to the release decision record. The README limitation can then be closed
   for that hardware.

**Interpreting a failure.**
- **Echo step fails:** echo cancellation is off or not converged. Use headphones, or lower the speaker volume.
- **Read step fails on SNR:** move closer to the microphone or reduce background noise.
- **Read step fails on WER only:** that is Whisper on your voice. The transcripts are in the report.
- **Interrupt not detected:** see section 4. Say "stop talking" or use *Mute speech*.

### Automated tests of the check

These are **fake-device** tests.

- `tests/e2e/test_voice_check_e2e.py` runs the whole check in headless Chromium, in the console (1280 px) and in
  Pocket (390 px, HTTPS, paired). `getUserMedia` is replaced by a scripted WebAudio stream, and the test plays Kokoro
  speech of each sentence, and "Stop! Please stop.", into it.
  - **Real:** the panel, the worklet, `vad.mjs`, uploads, Whisper scoring, Kokoro phrases and the evidence file.
  - **Not real:** there is no microphone, speaker, room or echo path. The speaker and echo steps only prove the flow,
    and the test clicks the owner's answers.
  - Last run: 7/7 steps in both. Read WER 0–8.3% ("summarise" was heard as "summarize"), barge onset-to-stop
    438 ms, no false echo barge-ins, fake noise floor −70.8 dBFS.
- `tests/voice/test_selftest.py` covers:
  - the server flow: scoring, audio dropped after scoring, the 0600 evidence file, per-principal isolation and bounds;
  - the verdict rules;
  - the terminal flow on a scripted fake device;
  - `nsf voice selftest` with `--fake-input`;
  - JS/Python VAD parity;
  - the harness's statistics helpers.

## 7. Reproduce

```
# download (see section 1 for URLs) into ~/.local/share/neuroscifi/datasets/{_dl,librispeech,...}, extract, then:
uv venv ~/.local/share/neuroscifi/datasets/_tools/venv && uv pip install --python ~/.local/share/neuroscifi/datasets/_tools/venv/bin/python pyarrow==19.0.1
~/.local/share/neuroscifi/datasets/_tools/venv/bin/python -I scripts/voice_corpus_prepare.py      # 16 kHz WAV subset + manifest
.venv/bin/python scripts/voice_real_speech_eval.py                    # all parts; or --parts wer|vad|commands
.venv/bin/python -m pytest -q tests/voice
NSF_CHROMIUM=/usr/bin/chromium .venv/bin/python -m pytest -q tests/e2e/test_voice_check_e2e.py tests/e2e/test_voice_e2e.py
```

The subset is fixed by seed 20261121. A full run made about 4,600 transcriptions and took about 15 minutes on the
reference host, where Whisper and Kokoro were shared with other work.
