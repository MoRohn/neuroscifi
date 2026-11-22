# Resource scheduling (RD-46, directive §14)

NeuroSciFi measures memory and GPU use on the host and turns the readings into one pressure level. It applies three
policies: audio priority, memory pressure and an opt-in idle unload. The policies are deliberately small and every
refusal or unload says why. Code: `src/neuroscifi/resources/` (`monitor.py` measures, `policy.py` decides). Surfaces: `nsf resources`,
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

**(c) Idle unload: opt-in, per service.** Code: `src/neuroscifi/resources/idle.py`. Every idle timeout is off by
default, so nothing is unloaded until the owner sets one:

```sh
nsf resources idle --set vllm=30m --set asr=15m --set tts=15m   # suggested; searxng too; `=off` turns one off
nsf resources tick                                               # one pass; neuroscifi-idle.timer runs it every minute
nsf resources unload vllm      # the owner unloads now (never reloaded on demand: `nsf resources load vllm` brings it back)
nsf resources load vllm        # the owner loads now; prints "Starting the local model, about N s" while it starts
nsf resources                  # policy.idle_unload: timeouts, state, last use, reload estimate, the last 20 events
```

A pass unloads a service only when all of these hold:
1. Its timeout is set and its container runs.
2. No voice session is open and no research task is running. A voice session counts from start to end, whether it
   is capturing or not, the owner's or a Pocket device's. The console that hosts the voice hub publishes open
   sessions in the audio beacon (`session`, §3(a)), so the pass in the timer's process sees them. A running task is a
   `web_tasks` row in a running stage with a live lease.
3. Nothing is in flight. Every model call through the gateway, every SearXNG search and every voice session start
   holds the service's in-use lock (`<db>.resources/idle-inuse-<service>.lock`, shared). The pass takes it exclusively
   and keeps it while the service stops, so a call either finishes first or waits and then reloads the service. For
   vLLM and the Whisper ASR (both vLLM servers) the pass also reads `/metrics`: a running or waiting request there,
   from any client, blocks the unload. When `/metrics` cannot be read, the pass does not unload.
4. Nothing used it within the timeout. The last use is the newest of: the in-use marker (written at the start and
   end of every call), a change in the served-request counter in `/metrics`, the container's start time, and the
   policy's own last load. A voice session's end marks ASR and TTS as used.

At pressure `critical` (§2) the timeout shrinks to a quarter of the configured one, never under 2 minutes: 30 min
becomes 7.5 min. This is the same level at which background autonomy and rendering are refused. A timeout that is
off stays off. The unload event names the level and the measured numbers.

**Reload on demand.** A service the idle policy unloaded starts again on its next use: a model call (gateway), a
SearXNG search, or a voice session that needs ASR (any voice mode) or TTS (`voice_text`). The user is told before the
wait starts: "Starting the local model, about 33 s", "Starting speech recognition, about 18 s". N is the last
measured reload of that service, else the documented cold start (§4). The voice client receives it as a `notice`
event. Presence shows it as the `waiting` activity in every process, because the state file
`<db>.resources/idle.json` carries it. The gateway keeps it in `Gateway.notices`, and `nsf resources load` prints it.
The reload runs the deploy's start command and then its wait command, under a per-service lock so two callers start
it once. The measured start-to-ready time is recorded in the event. A failed reload is recorded with the command's
output; the next use tries again. A service the owner unloaded is not reloaded on demand. While the policy has vLLM
unloaded, the inference health check (`Gateway.health`) reports `unavailable` with `idle_unloaded: true` and the
reason "the local model is unloaded while idle; the next request starts it again (about 33 s)", not an outage. The
voice engine health reports ASR and TTS unavailable until a voice session starts them.

**How stop and start run.** The policy uses the deploy's own commands. vLLM uses `deploy/vllm/run.sh
stop|start|wait`, ASR and TTS use `deploy/voice/run.sh stop|start|wait asr|tts`, and SearXNG uses `nsf web searxng
stop|start`. When the service's systemd unit is active, the policy runs `systemctl --no-ask-password stop|start
<unit>` instead, so it never fights the unit (an earlier reason this policy was manual). The user running the pass
then needs permission to manage that unit; without it the event records the refusal and the service stays loaded.
Install the timer by hand (`deploy/systemd/neuroscifi-idle.{service,timer}`, header comments).

**Events.** Every load, unload and timeout change is one line in `<db>.resources/idle-events.jsonl`: time, service,
action, reason, who (`idle policy`, `on demand`, `owner`), success, seconds, shape (`docker` or `systemd`) and, for
a load, what the user was told. The file is capped at the last 500 to 1000 lines. `nsf resources` and
`GET /api/resources` show the last 20.

**What it saves, and what a reload costs.** These are the §4 figures. vLLM holds 43.1 GiB of GPU memory and 7.4 GiB of
container memory. ASR holds 4.1 + 3.1 GiB and TTS 1.5 + 2.5 GiB; on unified memory the two accountings may overlap.
The last measured start-to-ready times, read from the container logs on 2026-11-21 (`nsf resources --cold-start`),
are vLLM 32.5 s, ASR 17.2 s and TTS 5.8 s. A reload through the policy adds the time to create the container and the
wait command's poll interval (5 s for vLLM, 3 s for voice). The live reload time of vLLM through the idle policy
remains to be measured by the owner: the first reload records it in the event log, and later messages use it. The
lane that built this policy did not restart the shared vLLM, ASR, TTS or SearXNG containers. It verified the stop,
reload and measurement path against a disposable container (§4).

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

**Idle unload against a disposable container** (`nsf-test-idle-*`, `python:3.12-slim-bookworm` running
`python -m http.server`, removed afterwards; the same code path with that container's start, stop and wait commands).
With a 1 min timeout, a pass right after a use kept it (`used 0 s ago`). A pass 2 min later (the policy's clock moved
forward) ran `docker rm -f` and logged `idle 2 min (timeout 1 min)`. The next request through `using()` told
"Starting the test service, about 1 s", started it, waited until it answered (HTTP 200) and recorded the reload as
0.6 s. The offline tests (`tests/test_resources_idle.py`, a fake docker host) cover each unload condition, the
`critical` timeout, the systemd shape, the owner's commands, failed reloads and Presence.

## 5. Limits

- The audio state is what the voice client reported. A client that goes silent for 30 s is no longer counted
  (`voice.state.STALE_SECONDS`).
- The slot cap limits background model calls only. vLLM's own batching (`max-num-seqs 8`) still decides how
  foreground calls interleave.
- Monitors are owner-scheduled and are not deferred.
- The latency measurements use one host, six questions and two runs. They show a direction and a size, not a
  distribution.
- Idle unload sees in-flight work from NeuroSciFi's own calls (the in-use lock) and, for vLLM and ASR, from any client
  (`/metrics`). A client outside NeuroSciFi that calls TTS or SearXNG directly is not seen. Such a request that starts
  in the moment between the pass's check and the stop fails.
- The reload is on demand and blocks the call that needs it for the reload time. Nothing preloads a service ahead of
  use.
