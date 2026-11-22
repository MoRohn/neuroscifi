# Cognitive Voice Bridge (v0.7.0, area 6)

Status: implemented and measured on the reference host (DGX Spark GB10), 2026-11-21. Decision record: ADR-037
(draft `.omc/autopilot/adr/ADR-037-voice.md`). Audit that chose the design: `PERSONAPLEX_INTEGRATION.md`.
Streaming, transcripts, barge-in and ownership details: `STREAMING_AND_TRANSCRIPTS.md`. Measured results:
`VOICE_UAT_RESULTS.md`.

## 1. Principle

NeuroSciFi is authoritative for facts, tools, memory and permissions. A speech engine transcribes the owner and
renders NeuroSciFi's text as audio; it never decides what is asserted. A spoken question goes through **the same
research path as a typed one** (`webcortex.orchestrator.submit`: privacy screen, web switch, budgets, deduplication,
the worker's discovery / reading / claim verification, the inference gateway's evidence-bound answer and its guard,
consolidation into memory). The bridge reads the finished report; it does not ask a model what is true.

```
mic ─▶ AudioWorklet 16 kHz PCM ─▶ VAD ─▶ /api/voice/audio ─▶ Whisper (vLLM, 127.0.0.1:18431) ─▶ transcript (approximate)
                                                                                      │ final
                                     commands.parse (deterministic) ─▶ Bridge ─▶ intent handler (authorised?)
                                                                                      │ question
                                    orchestrator.submit ─▶ worker: search ▸ read ▸ verify ▸ reasoning (Qwen3, guard)
                                                                                      │ report
                         compose_answer: concise speech + detailed text with citations (authoritative segment)
                                                                                      │ sentences
 speaker ◀── WebAudio ◀── /api/voice/speech (WAV per sentence) ◀── Kokoro-82M (127.0.0.1:18432)
```

## 2. Services (deployed and running)

| Service | Container | Bind | Image | Model (read-only mount) |
|---|---|---|---|---|
| ASR | `neuroscifi-voice-asr` | 127.0.0.1:18431 | `neuroscifi-voice-asr:0.7.0` (`sha256:965a968c…`) FROM `vllm/vllm-openai@sha256:48c0a41e…` + soundfile 0.13.1, librosa 0.11.0 | `~/.local/share/neuroscifi/models/whisper-large-v3-turbo` (rev `41f01f3f…`, sha256 of weights `542566a4…`) |
| TTS | `neuroscifi-voice-tts` | 127.0.0.1:18432 | `neuroscifi-voice-tts:0.7.0` (`sha256:67461d62…`) same base + kokoro 0.9.4, misaki 0.9.4 (`--no-deps`), spaCy 3.8.7 + en_core_web_sm 3.8.0, `deploy/voice/tts_server.py` | `~/.local/share/neuroscifi/models/kokoro-82m` (rev `f3ff3571…`, 12 allow-listed voices) |
| LLM | `neuroscifi-vllm` (Lane B) | 127.0.0.1:8811 | — | Qwen3-30B-A3B-Instruct-2507-FP8 |
| Search | `neuroscifi-searxng` (Lane A) | 127.0.0.1:8888 | — | — |

Both voice containers: `--user 1000:1000 --read-only --cap-drop ALL --security-opt no-new-privileges`, pids limit,
`--cpus`/`--memory` ceilings, `HF_HUB_OFFLINE=1`, one generated API key (`~/.config/neuroscifi/voice/api_key`,
0600; ASR via `VLLM_API_KEY`, TTS via `NSF_TTS_API_KEY`, both through 0600 env files). `run.sh` refuses a non-loopback
bind and an image not built on the pinned base digest. The NCCL pin: torch 2.14.0's metadata pins
`nvidia-nccl-cu13==2.30.7` while the image ships 2.31.2, so kokoro/misaki are installed with `--no-deps` and both
Dockerfiles assert torch 2.14.0 and NCCL 2.31.2 after installing (`deploy/voice/constraints.txt` explains).

ASR flags (audit §6.5, required on unified memory): `--gpu-memory-utilization 0.08 --kv-cache-memory-bytes
2147483648 --enforce-eager --max-num-seqs 4`.

```
deploy/voice/run.sh build                 # both images from the pinned base
deploy/voice/run.sh start all             # or: start asr | start tts
deploy/voice/run.sh wait all 600          # ASR ~110 s, TTS ~15-25 s to ready
deploy/voice/run.sh status
nsf voice health --db INSTANCE            # engine states + real round trip: Kokoro speaks, Whisper transcribes, WER
nsf voice selftest --db INSTANCE --report PATH   # guided mic/speaker/barge-in/echo check on this host (VOICE_VALIDATION §6)
deploy/voice/run.sh stop all              # text keeps working; voice controls show "unavailable"
```

systemd: `deploy/systemd/neuroscifi-voice-asr.service`, `neuroscifi-voice-tts.service` (`run.sh foreground`).

## 3. Speech engine interface (`src/neuroscifi/voice/engines.py`)

`WhisperASR.transcribe(pcm, rate)`, `KokoroTTS.synthesize(text, voice, speed)`, `PersonaPlexEngine` (experimental),
each with `health(probe=)` returning one of `ready | loading | degraded | unavailable | unauthorised |
not_configured | disabled`. Connect timeout 2 s, read timeout 20 s, one retry for a connection error, after 3
consecutive failures a 10 s cool-down that fails fast without a request (then one probe = reconnect), metrics
(calls, failures, p50/p95 latency, last error). Endpoints must be loopback (validated in settings and in the
client). The hub caches health for 5 s.

**PersonaPlex** is behind the same interface but **disabled by default** and never routed a question: its own words
come from a 7B chat model without NeuroSciFi's evidence. Only a loopback URL and an allow-listed voice name are
accepted (the upstream server binds every interface, has no auth, and unpickles a path taken from the request; audit
§2.1). It is not deployed: upstream code is not real-time on GB10 (RTF 1.24–1.84) and the optimised fork is
third-party and unmerged. Enabling it shows its health only.

## 4. Commands and routing (`commands.py`, `bridge.py`)

Parsing is deterministic (regular expressions over the normalised transcript); no model chooses the intent.

| Say | Intent | Effect | Confirmation |
|---|---|---|---|
| any question (≥ 3 words ending with "?" or starting with a question word or an information request: what, how, is, does, tell me, explain, ...), "research X", "look up X", "tell me about X" | research | `orchestrator.submit(origin="voice")`; spoken acknowledgement; the hub follows the task and speaks the answer. At most 8 new tasks per principal from conversation in 10 minutes (then `rate_limited`, with when to ask again) | only if the privacy screen finds private data (owner: "send it anyway?"; Pocket: refused) |
| any other sentence of ≥ 3 words (remarks, thanks, statements) | chatter | nothing is searched; the reply says how to ask | — |
| "show (me) the evidence" | show_evidence | sources of the last task (text), one spoken sentence | — |
| "explain that more simply" | simpler | the local model rewrites from the **same evidence bundle**; every sentence passes `reasoning.guard`; without a model: a shorter extract, labelled | — |
| "remember (these) conclusions" | remember | accept the last task's provisional knowledge items (`knowledge.review`) | **yes** (owner only) |
| "what have you learned since yesterday / this week" | learned_since | knowledge created/updated in the window, from memory (template-labelled) | — (owner only: it reads the whole knowledge base) |
| "what was I researching", "what did I ask earlier", "show my recent research" | history | the conversation's last question and up to 5 of the principal's own recent tasks with their status (owner: tasks a person asked for; Pocket: its family's), from history; nothing is searched (R2-07) | — |
| "continue the previous investigation" | continue_research | `orchestrator.requeue` (partial / cancelled / failed) or a new linked task | — |
| "stop", "stop speaking but keep researching" | stop_speaking | playback stops; research continues | — |
| "go on" | resume_speaking | speak the rest of an interrupted reply | — |
| "cancel the research" | cancel_research | `orchestrator.cancel` (the only voice path that stops a task) | — |
| "switch to text only" / "text replies only" / "speak your answers" | mode | C / B / A | — |
| "forget this conversation" | forget | delete this session's transcript (and retained audio) | **yes** |
| "forget the last research" | forget | `governance.forget_research` | **yes** (owner only) |
| "turn on autonomous research" | enable_autonomy | `webcortex.autonomy.enable(by="owner:voice")` (the switch and its enable record; the reply says the resulting state) | **yes** (owner only) |
| "turn off autonomous research" | disable_autonomy | `webcortex.autonomy.disable(by="owner:voice")`, recorded | — (owner only) |
| "use this device for the microphone" | handoff | moves the capture lease (a Pocket device taking it from another principal: asks the holder, who grants or refuses) | **yes** |
| "confirm" / "no" | confirm / deny | answers the pending confirmation of this session | — |

The address ("NeuroSciFi, …", also as ASR hears it: "New Roe Sci-Fi", "neuro sci fi") is removed before parsing,
and a trailing instruction ("… and summarize their importance", "…, then explain why it matters") is not part of
the research subject: it is kept in the conversation context (`last_instruction`) and shown with the
acknowledgement. It reaches answer synthesis as a presentation instruction: the task's context `presentation`
(`orchestrator.submit(context=...)`, privacy-screened like the objective, kept on resume), which
`reasoning.messages` adds to the system prompt as a style instruction under the same rules. It is never a search
term, and the evidence guard judges every sentence exactly as without it (`reasoning.presentation` records it).
A follow-up question that only points back ("Is that version secure?", "When was it released?") takes its subject
from the conversation's previous question (`last_objective`) through `webcortex.intent.followup`, as the console
does; with no usable earlier question the reply asks which one is meant (intent `clarify`) and nothing is searched.
"Continue the previous investigation", "show the evidence" and "explain more simply" use the conversation's last
task, else the principal's latest research from any device (a Pocket device: its family's; the owner: any
user-started task). "Show the evidence" numbers sources as the answer cites them; uncited sentences carry no "[]".

A confirmation is held server-side per session for 60 s; it is executed only by the same session ("confirm" spoken,
or the Confirm button). "Yes" alone is not accepted.

**Authorisation.** The console principal is the owner. Pocket voice exists only while `pocket_research` is on
(default off, the same switch as Pocket research; otherwise `voice_status` answers `{"voice": false}` and every other
voice route 404). A Pocket principal is its pairing family: research with Pocket's per-family hourly limit, only its
family's tasks and transcripts, no owner settings (retention, endpoints), no knowledge-base reads ("what have you
learned"), no sensitive owner commands ("needs the owner console").

**Replies.** `compose_answer(task)` uses the model answer when `report.reasoning.status == "model"` (sentences that
passed the guard; label "Advanced reasoning by …"), otherwise the extractive answer, spoken as "According to
<domain>: <quote>" and labelled "Extractive summary (quoted evidence); advanced reasoning unavailable (reason)".
Speech is concise (≤ 2 sentences, ≤ 70 words, citation markers removed, plus "The details and citations are on
screen."); the transcript keeps the full text with `[n]` citations and the source list. Template replies (help,
confirmations, refusals) are labelled "NeuroSciFi template reply (no model involved)".

**Long tasks.** The acknowledgement is spoken at once; a follower thread reads the task every second, emits
truthful stage progress ("Research: reading sources") and speaks "Still working: …" at most every 30 s when idle.
The task belongs to the research system: ending the voice session, switching mode or barge-in never cancels it; its
answer is still written to the transcript when the session has ended.

**Durable context.** The session row keeps `last_task_id` / `last_objective`; a new session of the same principal
inherits them, so "show the evidence" or "continue the previous investigation" work after a restart. Nothing
depends on a speech engine's context window.

## 5. Modes and controls

| Mode | Mic | Speech out | Needs |
|---|---|---|---|
| A `voice_text` | yes | yes | ASR + TTS |
| B `voice_in_text_out` | yes | no (server refuses `/speech`; no synthesis) | ASR |
| C `text_only` | no (server refuses audio and releases the lease; the client stops its tracks) | no | nothing (works with every voice service down) |

Switching keeps the session and its context. Controls in the console "Voice" view and the Pocket "Voice" section:
session start/end, microphone on/off (off = `MediaStreamTrack.stop()` on every track and lease release), speaker
mute (no playback, no synthesis, `speaking` never reported while muted), volume (gain node), voice (Kokoro
allow-list), input device (`enumerateDevices`, after permission), transcript visibility, typed input, transcript
search, correction of recognised text, owner-only raw-audio retention with a 1–168 h TTL, a consent prompt before
the first capture on a device, and a capture indicator ("● Microphone on — capturing").

## 6. HTTP surface

Console (loopback, session token, Host/Origin checks unchanged): `GET /api/voice/{status,events,speech,transcripts}`,
`POST /api/voice/{session,consent,mode,settings,capture,text,confirm,interrupt,client,correct,forget}` (JSON ≤ 4 KiB)
and `POST /api/voice/audio` (`audio/L16; rate=16000`, ≤ 64 KiB per chunk, headers `X-NSF-Session`,
`X-NSF-Utterance`, `X-NSF-Seq`, `X-NSF-Final`). Every other console limit is unchanged. `Permissions-Policy` now
allows `microphone=(self)` (camera and geolocation stay off).

Pocket (paired credential, HTTPS on the LAN): `POST /api/voice_<op>` with JSON ≤ 512 B (`voice_text`,
`voice_correct` ≤ 1 KiB), `voice_audio` ≤ 64 KiB. A bridge without `--instance-db`, or with `pocket_research` off,
answers `voice_status` with `{"voice": false}` to paired devices; unpaired callers get 403. The Pocket device id is an instance-salted pseudonym
of its pairing; Pocket stores nothing in browser storage for voice.

## 7. Operations

| Symptom | Check | Recovery |
|---|---|---|
| "Speech recognition: unavailable" | `deploy/voice/run.sh status`, `docker logs neuroscifi-voice-asr` | `run.sh start asr` (ready after ~110 s); text keeps working |
| "unauthorised" | `ls -l ~/.config/neuroscifi/voice/api_key` (must be 0600) | `run.sh start` re-writes the env files from the key |
| ASR container exits at start | log shows `Free memory … less than desired` or a profiler assertion | keep `--gpu-memory-utilization 0.08` **and** `--kv-cache-memory-bytes` (`voice.env`) |
| Names, new terms or domains missing from speech ("This is NeuroSciFi" spoken as "This is") | `curl 127.0.0.1:18432/health` says `failed: espeak-ng fallback unavailable` | the TTS container's `/tmp` must be `exec` (phonemizer loads libespeak-ng from a temporary copy): `run.sh print tts` shows `--tmpfs /tmp:rw,exec,...`; `run.sh start tts`. Before synthesis `engines.speakable` reads URLs/domains as "techcrunch dot com" and gives the names in `engines.PHONEMES` (NeuroSciFi, agentic, Kubernetes, PyTorch, ...) to misaki as inline phonemes, so they are spoken even without the fallback |
| TTS `loading` | `curl 127.0.0.1:18432/health` | first start loads spaCy; on CPU set `NSF_VOICE_TTS_DEVICE=cpu` (RTF 0.42) |
| Console service cannot read the key | `ProtectHome=yes` in the unit | `nsf voice settings --set '{"key_file": "/etc/neuroscifi/voice.key"}'` (root:neuroscifi 0640 is refused: key files must be 0600 and owned by the service user) |

## 8. Limitations (honest)

- End-of-utterance detection is energy-based client VAD (adaptive floor, 700 ms hang-over); the 700 ms is part of
  every measured response time from the owner's last word. No streaming ASR route exists for Whisper in vLLM; the
  provisional transcript is a re-transcription of the open utterance at most once a second.
- Echo cancellation is the browser's (`echoCancellation: true`). It was simulated on recorded speech, not measured
  with real speakers and a real microphone. Without it, NeuroSciFi's own voice interrupts it. The Voice check's echo
  step measures this on the owner's hardware ([VOICE_VALIDATION](VOICE_VALIDATION.md)).
- Thresholds live in `web/vad.mjs`. They were retuned in 0.7.0 on recorded human speech: listening is max(4×floor,
  0.006) for 160 ms, barge-in is max(2×floor, 0.03) for 250 ms. Results before and after, on a held-out split, are
  in VOICE_VALIDATION §4.
- The command grammar is English. Beyond the literal phrases it applies canonical forms, synonyms, frames, clause
  splitting and fuzzy matching. A fuzzy match of a command with effects asks for confirmation first. About two thirds
  of freely phrased held-out commands are recognised (VOICE_VALIDATION §5); the rest fall through to chatter or
  research.
- PersonaPlex: interface and health only; not deployed, not real-time upstream (audit).
