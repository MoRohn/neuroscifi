# Resource scheduling (RD-46, directive §14)

NeuroSciFi measures memory and GPU use on the host and turns the readings into one pressure level. It applies three
policies: audio priority, memory pressure and idle unload. The policies are deliberately small and every refusal says
why. Code: `src/neuroscifi/resources/` (`monitor.py` measures, `policy.py` decides). Surfaces: `nsf resources`,
`GET /api/resources` and Presence `resource_constrained`. Evidence: `docs/quality/evidence/resources_live.json`.

## 1. What is measured

| Reading | Source | Note on GB10 (unified memory) |
|---|---|---|
| MemTotal, MemAvailable, swap | `/proc/meminfo` | GPU allocations come out of the same memory, so MemAvailable is the deciding number |
| Memory stall (PSI) `some`/`full` avg10 | `/proc/pressure/memory` | reported only; no threshold |
| GPU memory used/total, utilisation | `nvidia-smi --query-gpu` | memory reads `[N/A]`: recorded as `memory_readable: false`, never guessed |
| Per-process GPU memory | `nvidia-smi --query-compute-apps` | readable on GB10 |
| Container memory and CPU | `docker stats --no-stream` (neuroscifi-* only) | cgroup memory: a container's CUDA allocations are **not** in it |
| Container cold start | `docker inspect` StartedAt to the first readiness log line | measures the last start that actually happened |

`sample()` caches the `/proc/meminfo` read for 2 s. It caches the subprocess readings (nvidia-smi, docker) for 15 s.
The gates on the worker's hot path therefore cost at most one small file read. They never start a subprocess.

## 2. Pressure level

| Level | Condition (either line) |
|---|---|
| critical | MemAvailable < max(6 GiB, 6 % of MemTotal); a readable GPU memory use ≥ 97 % |
| elevated | MemAvailable < max(16 GiB, 15 % of MemTotal); a readable GPU memory use ≥ 90 % |
| ok | otherwise (`unknown` when `/proc/meminfo` cannot be read) |

On this host (MemTotal 121.9 GiB) the thresholds are 18.3 GiB (elevated) and 7.3 GiB (critical).
`NSF_RESOURCE_ELEVATED_GIB` and `NSF_RESOURCE_CRITICAL_GIB` replace the absolute floors. The critical floor leaves
room for the kernel, one Chromium render and a running task. A new model load needs tens of GiB and would push the
host into swap or the OOM killer.

## 3. Policies

**(a) Audio priority.** A voice session counts as audio activity while it is capturing or speaking (`session_active`
and one of `mic_capturing`, `speaking` or `user_speaking` in `voice.state.snapshot()`). While audio is active:
- New autonomous research does not start. This covers curiosity, prediction refreshes and idle investigations.
  `scheduler.claim_next` does not claim these tasks, `autonomy.admit` returns `wait`, and the timer (`tick`) skips
  them with the reason. The worker logs one `decision` event when the deferral starts and one when it ends.
- Research the owner asked for always starts and runs: console, CLI, Pocket, voice, monitors and owner refreshes.
  An autonomous task that was already running also continues.
- Background model calls (the reasoning of autonomous tasks) share capped slots, implemented as file locks in
  `<db>.resources/`. Without audio the cap is 2 at a time; with audio it is 1. A call that finds no free slot before
  its deadline (at most 30 s) is not made. The report keeps its extractive answer and gives the reason. Foreground
  model calls never take a slot.
- Cross-process: the console hosts the voice hub. `AudioBeacon` writes `<db>.resources/audio.json` when the audio
  state changes, and every 3 s while audio is active. A worker in another process (`neuroscifi-webcortex.service`)
  reads the beacon and ignores it once it is older than 10 s. The beacon writes nothing before the first voice
  activity.

**(b) Memory pressure.** At `critical`, background autonomy is refused as in (a), with reason
`memory pressure critical (memory available X GiB of Y GiB is below the critical threshold Z GiB)`. The optional
Chromium renderer is also refused, and the task records a `decision` event `{render: false, reason}`. User research
continues without rendering. At `elevated`, nothing is refused: Presence shows `resource_constrained` and the
reasons appear in `nsf resources`.

**(c) Idle unload: not automatic.** The worker does not stop or start the voice containers, for three reasons:
1. A stopped ASR container makes the next utterance fail until the container is ready again: 17–20 s measured (§4).
   Only the voice hub could restart it on demand, and that code is not part of this lane.
2. Under systemd, `neuroscifi-voice-*.service` runs `run.sh foreground` and restarts the container itself. A worker
   that ran `run.sh stop` would fight the unit.
3. The saving is at most about 11 GiB: 4.1 + 1.5 GiB of GPU memory plus 3.1 + 2.5 GiB of container memory (§4;
   the two accountings may overlap on unified memory, so the real saving can be smaller). The host had 41.9 GiB
   available with everything resident.

Idle unload is therefore an owner-run command:

```sh
deploy/voice/run.sh stop all                                         # unload ASR and TTS (Docker shape)
deploy/voice/run.sh start all && deploy/voice/run.sh wait all        # load again; measured 17-20 s (ASR) / 5 s (TTS)
systemctl stop neuroscifi-voice-asr neuroscifi-voice-tts             # systemd shape (start the same way)
nsf resources --cold-start                                           # the last measured start-to-ready times
```

NeuroSciFi never unloads vLLM. Its last measured cold start was 32.5 s, and an earlier start took 35.7 s.

## 4. Measurements (2026-11-21, DGX Spark GB10, all services resident)

**Memory with every NeuroSciFi service resident** (`nsf resources --cold-start`, idle, 15:11 UTC). Twelve unrelated
containers were also running on the host, so MemAvailable is not attributable to NeuroSciFi alone.

| Item | Value |
|---|---|
| MemTotal / MemAvailable | 121.9 GiB / 41.9 GiB (level `ok`) |
| vLLM (Qwen3-30B-A3B FP8) GPU memory, compute-apps | 43.1 GiB (the T2 UAT run measured 44.1 GiB) |
| Whisper ASR (vLLM) GPU memory | 4.06 GiB |
| Kokoro TTS GPU memory | 1.47 GiB (0.97–1.35 GiB in earlier samples) |
| Container cgroup memory: vLLM / ASR / TTS / SearXNG | 7.44 / 3.06 / 2.54 / 0.15 GiB |
| GPU memory total (nvidia-smi) | N/A on GB10 (`memory_readable: false`) |
| Cold start, start to ready (warm caches) | vLLM 32.5 s (35.7 s at an earlier start); ASR 17.2 s (20.1 s); TTS 5.2 s (4.9 s) |

**Research answer latency with and without an active voice session.** Method: six authoritative bench questions run
in sequence. Retrieval used the offline corpus. Reasoning used the live local vLLM: one answer call and one
self-critique call. The voice load was a continuous conversation: Kokoro synthesised a sentence and Whisper
transcribed it back, one round trip at a time, with a voice session flagged in the process. One warm-up pass was not
reported. Run 2 (15:03 UTC; run 1 shows the same direction):

| | Research alone | Research + voice load |
|---|---|---|
| Task wall time, median (min–max) | 4.52 s (3.72–7.73) | 7.82 s (5.95–12.55) |
| Model time, median | 4.30 s | 7.55 s |
| First token, median | 208 ms | 273 ms |
| Answer decode rate, median | 46.8 tokens/s | 29.1 tokens/s |
| Completion tokens, median | 192 | 206.5 |

Run 1 (15:00 UTC): wall time median 5.67 s alone and 7.11 s with voice load; first token median 152 ms and 270 ms.

**Voice latency with and without research running** (same runs, per request):

| | Voice alone | Voice + research |
|---|---|---|
| TTS synthesis, median | 0.119 s (n=101) | 0.252 s (n=68) |
| ASR transcription, median | 0.267 s (n=101) | 0.517 s (n=68) |

These loads share the GPU. Under continuous research reasoning, TTS and ASR latency roughly double but stay under
0.65 s per request in these runs. Under continuous voice traffic, the decode rate drops about 38 %. This is why
policy (a) defers background research during voice. Research the owner asks for is not held back, because the owner
is waiting for it.

**Worker deferral.** Shape: the worker ran in another process from the voice session (the service shape) and read
the audio beacon; poll 0.5 s.

| Observation | Value |
|---|---|
| User task queued during the voice session | completed (claimed and run; 0.7 s after the worker started) |
| Autonomous task while voice stayed active for 10 s | stayed `queued` |
| Voice session end to autonomous task claimed | 1.05 s (beacon poll 1 s + worker poll 0.5 s) |
| Worker decision events | `deferring new autonomous research` (reason: audio priority), then `autonomous research may start again` |

## 5. Limits

- The audio state is what the voice client reported. A client that goes silent for 30 s is no longer counted
  (`voice.state.STALE_SECONDS`).
- The slot cap limits background model calls only. vLLM's own batching (`max-num-seqs 8`) still decides how
  foreground calls interleave.
- Monitors are owner-scheduled and are not deferred.
- The latency measurements use one host, six questions and two runs. They show a direction and a size, not a
  distribution.
