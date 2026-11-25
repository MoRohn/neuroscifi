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
| GPU temperature (°C), power draw (W), clock throttle reasons | the same `nvidia-smi --query-gpu` call | a field nvidia-smi cannot read stays null; a driver that refuses these fields is asked for the first three alone |
| Per-process GPU memory | `nvidia-smi --query-compute-apps` | readable on GB10 |
| Host load averages (1, 5, 15 min) and the CPU count | `/proc/loadavg` | reported only; no threshold |
| Inference server state | vLLM's `/metrics` on the configured loopback endpoint | read with the gateway's key file; never read for an endpoint off this host |
| Container memory and CPU | `docker stats --no-stream` (neuroscifi-* only) | cgroup memory: a container's CUDA allocations are **not** in it |
| Container cold start | `docker inspect` StartedAt to the first readiness log line | measures the last start that actually happened |

`sample()` caches the `/proc/meminfo` read for 2 s. It caches the subprocess readings (nvidia-smi, docker) for 15 s.
The gates on the worker's hot path therefore cost at most one small file read. They never start a subprocess. The
`/metrics` scrape is cached for 15 s as well, and only a full reading (`nsf resources`, `GET /api/resources`) makes
it; the hot path never does (`test_the_scrape_is_cached_and_the_hot_path_sample_never_makes_it`).

**What `nsf resources` shows beyond memory.** A full reading (without `--quick`) adds:

- `load`: `avg1`, `avg5`, `avg15` and `cpus`, or `available: false` with the reason.
- `gpu`: `temperature_c`, `power_draw_w` and `throttle` {`thermal`, `sw_thermal_slowdown`, `hw_thermal_slowdown`,
  `sw_power_cap`, `hw_slowdown`, `active_mask`} beside the memory and utilisation fields. `throttle.thermal` is true
  while a software or hardware thermal slowdown holds the clocks down; `active_mask` is the raw bitmask of every
  active clock event reason.
- `inference`: the vLLM server's state. Now: `requests_running`, `requests_waiting`, `kv_cache_usage`, `models`,
  `server_started_at`. Since the server started (`since_start`) and over the time since the previous reading in the
  same process (`interval`, with its `seconds`): `requests_finished`, `mean_seconds` {`queue`, `prefill`, `decode`,
  `first_token`, `request`}, `prompt_tokens`, `prefix_cache_hit_share`, `generation_tokens`, `preemptions` and
  `finished_by_reason`. The interval is there because lifetime means hide a current problem; the first reading of a
  process has none and says so, and a server restart between two readings is reported instead of a negative number.
  An endpoint that is not configured, not on this host, unreachable or not a vLLM server is `available: false` with
  the reason. The idle policy (§3(c)) reads in-flight and served requests with the same parser.

None of these readings changes the pressure level (§2) or a policy decision; they are reported so that a slow answer
can be attributed to a queue, a prefill, a thermal slowdown or a busy host.

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
- Cross-process: every process that hosts a voice hub publishes an audio beacon, the console when it starts and the
  Pocket bridge with its first voice hub (`voice/beacon.py`, `SharedAudioBeacon`). Both write the one file
  `<db>.resources/audio.json` under a lock (`audio.lock`): each process keeps its own entry under `writers`, and the
  top-level `active`, `session` and `at` are the OR of the entries of live processes, so neither hub overwrites the
  other and the readers are unchanged. The audio state comes from every voice session of the process, a Pocket
  device's included, not from the owner-only Presence snapshot. A beacon is written when the state changes and every
  3 s while audio is active or a session is open. A worker in another process (`neuroscifi-webcortex.service`) reads
  the beacon and ignores it once it is older than 10 s; an entry whose process is gone no longer counts. The beacon
  writes nothing before the first voice activity.
- Queue order: `scheduler.claim_next` claims a queued task the owner asked for before a queued autonomous one, oldest
  first within each class. An autonomous task queued more than 10 minutes ago (`AUTONOMOUS_DEFER`) is ordered by age
  with the owner's, so a steady stream of questions delays background work by at most that long and never starves
  it. A waiting worker is woken by a submission from any process (it watches SQLite's `data_version` and the queue
  between polls) and claims the task within about 0.1 s; the 5 s poll remains as the fallback.

**(b) Memory pressure.** At `critical`, background autonomy is refused as in (a), with reason
`memory pressure critical (memory available X GiB of Y GiB is below the critical threshold Z GiB)`. The optional
Chromium renderer is also refused, and the task records a `decision` event `{render: false, reason}`. User research
continues without rendering. At `elevated`, nothing is refused: Presence shows `resource_constrained` and the
reasons appear in `nsf resources`.

**(c) Idle unload: opt-in, per service.** Code: `src/neuroscifi/resources/idle.py`. Every idle timeout is off by
default, so nothing is unloaded until the owner sets one:

```sh
nsf resources idle --set vllm=30m --set asr=15m --set tts=15m   # suggested; searxng too; `=off` turns one off
nsf resources idle --set embed=15m --set rerank=15m             # the two memory models (deploy/memory), if they run
nsf resources tick                                               # one pass; neuroscifi-idle.timer runs it every minute
nsf resources unload vllm      # the owner unloads now (never reloaded on demand: `nsf resources load vllm` brings it back)
nsf resources load vllm        # the owner loads now; prints "Starting the local model, about N s" while it starts
nsf resources                  # policy.idle_unload: timeouts, state, last use, reload estimate, the last 20 events
```

A pass unloads a service only when all of these hold:
1. Its timeout is set and its container runs.
2. No voice session is in use and no research task is running. A voice session counts from its start for as long as
   it is used, whether it is capturing or not, the owner's or a Pocket device's: a use is audio (or a client
   reporting capture, speech or the user speaking), a typed turn, a capture or mode change, a reply fetched, an
   interruption or a confirmation; polling for events and status reports that nothing plays are not. A session left
   open and unused for longer than `voice` (default 30 min, `nsf resources idle --set voice=45m`) stops holding off
   unloading, so a phone that keeps a session open in a pocket no longer does so for ever; the pass says so in its
   `notes`. `--set voice=off` is the rule of 0.7.0: an open session counts until it ends. The console and the Pocket
   bridge each publish their open sessions in the audio beacon (`session`, and `used_at`, the newest use; a writer
   that does not say when counts as in use; §3(a)), so the pass in the timer's process sees them. A running task is
   a `web_tasks` row in a running stage with a live lease.
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
output; the next use tries again. A service the owner unloaded is not reloaded on demand.

**The memory models are never waited for.** The embedding model and the reranker (`embed`, `rerank`;
`deploy/memory`) are services of this policy like the others, with one difference in how they come back. A model
call or a voice session waits for its service; a memory lookup does not, because it has an answer without the
model: the lexical ranking. So a lookup takes the in-use lock without blocking (`idle.using_now`), and when it finds
a model unloaded it is ranked by its words at once, with `retrieval.reason` "memory search (the embedding model) is
unloaded while idle and is starting again (about N s)" and `retrieval.waking` naming the models, while the reload
runs in the background. A lookup after the reload is ranked by meaning again.

The reload is a process of its own, `nsf resources load SERVICE --on-demand "memory lookup"`, started in its own
session with no pipe back to the caller. A thread would not do: a one-shot command such as `nsf web knowledge TOPIC`
ends right after its lookup, a reload in one of its threads would die with it, and the model would never start,
whatever the lookup had said. The process is started once per service: a lookup that finds this process's reload
still running, or another process holding the service's load lock, starts nothing
(`test_the_background_reload_is_a_process_of_its_own_that_outlives_the_lookup`,
`test_a_reload_another_process_is_running_is_not_started_twice`). The same headroom check, wait command,
measurement and event as every other reload apply, because it is the same `Idle.load`.

**Wake requests: when the process may not start services.** With `NSF_WAKE_DIR` set, no process of NeuroSciFi
starts a service itself. A call or a lookup that finds its service unloaded leaves an empty file `<service>.wake`
in that folder (created atomically, mode 0600; the name is the whole message). `neuroscifi-wake.path`
(`PathExistsGlob=<folder>/*.wake`) starts `neuroscifi-wake.service`, which runs `nsf resources wake` as the user
that owns the services. Each request is removed first, never opened, and its name is looked up in the policy's own
service list: any other name is dropped and no command is run
(`test_a_wake_request_is_only_a_service_name_anything_else_is_dropped_and_nothing_is_run`). The reload is
`Idle.load`: the same free-memory check, wait, measurement and event. A memory lookup still returns at once; a
model call waits until the state says "loaded", no longer than its own timeout, and then ends `unavailable` with
"… it keeps starting in the background" as before; a voice session's start only asks. A request that nobody picked
up within 60 s is reported: "its wake request was not picked up within 60 s (is neuroscifi-wake.path active?)".
Install: `deploy/install.sh --idle [--idle-user USER]` installs the idle timer and the wake units (disabled) and
creates the request folder.

*Who needs what* (second review, S2). The process that asks needs nothing of the idle policy's state. Where the
two accounts share that state (§3(d): what `deploy/install.sh --idle` arranges) the console and the worker read
it and ask before they call. Where they cannot read it (an install made before that, or by hand) they see "policy
off" and make their call. A **refused connection** to one of the deployment's own service ports is the signal: the
inference gateway leaves the request then (`idle.asked_on_refusal`; at most one per service every 30 s from one
process) and the call ends `unavailable` with "its start was asked for (wake request)"; a memory lookup is ranked
by its words meanwhile. Where the state can be read, the lookup or the call leaves the request before trying, as
above; in neither case does the asking process write anything in the policy's folder. The serving side decides
from its own state: unloaded by the policy (an owner's unload stands), enough free memory, and not within 30 s of
its last failed or refused reload (`skipped`, with the reason).

*The request folder* is the serving account's. It is created by `deploy/install.sh --idle`
(`install -d -m 3770 -o <idle user> -g neuroscifi /var/lib/neuroscifi-wake`: the owner removes requests, the
service user's group may create them, setgid, nobody else enters; sticky, so a request is removed by whoever left
it or by the folder's owner and by nobody else) or, when missing, by `nsf resources wake` (mode 3770). A process
that asks never creates it and never changes its mode; a missing folder is an error it reports. A request is an
empty file whose name is the whole message, so its own mode does not matter. The installer also adds the folder
to `ReadWritePaths=` of the console, worker and Pocket units (they run under `ProtectSystem=strict`).

*What is not a request* (S3). `PathExistsGlob=` starts the service again each time it ends while a matching entry
exists; that is what serves a request left during a reload, and it would loop on an entry that never goes away.
`nsf resources wake` therefore serves regular files only (a symbolic link is removed, not followed), removes an
empty folder or a special file, and renames anything else to a hidden `.stuck-…` name that no pattern matches. If
an entry can be neither removed nor renamed (a read-only folder), the command stays for 30 s, serving the requests
that arrive, before it ends.

*How often the unit starts is the command's to decide, not the asker's.* The path unit starts the service once
per request that finds it stopped. Thirty requests in ten minutes, from anyone who may write the folder or simply
from three services asking for six models every 30 s, therefore reached the unit's start limit
(`StartLimitBurst=30` in `StartLimitIntervalSec=10min`): systemd then refuses the service, the path unit fails
with it, and no request is served until `systemctl reset-failed`. The unit now runs `nsf resources wake --linger
25`: the command stays 25 s whenever it starts and serves within a second what arrives meanwhile. That is 24
starts in ten minutes at most, whatever is written into the folder (`idle.WAKE_LINGER`;
`test_the_wake_unit_cannot_reach_its_start_limit_whatever_is_written_into_the_folder` keeps the two numbers
apart). The limit itself stays, as the last stop against a command that fails at once. Measured with a transient
user unit and a request every 2 s: §4.

**(d) Two accounts: what they share.** `deploy/install.sh` runs the console, the worker and Pocket as the
service account `neuroscifi` (sandboxed, no Docker access) and the idle pass and the wake requests as the account
that runs the model services (the *idle account*: `--idle-user`). `deploy/install.sh --idle` arranges what the
two share, and `--idle-access` does only that (run it again after `nsf init`: the database does not exist
before). The model, once:

| Path | Owner : group, mode | Who does what |
|---|---|---|
| `/var/lib/neuroscifi` (the state folder) | `neuroscifi:neuroscifi` 0710 (was 0700) | The group may pass through; only the owner may list or create. Everything else inside keeps its mode (0600, 0700) |
| The instance database, with its `-wal` and `-shm` | `neuroscifi:neuroscifi` 0640 (was 0600) | The idle account reads it for one question: does a research task run (`Idle.running_tasks`). It cannot write it. SQLite gives the write-ahead log's files the database's mode when it creates them |
| `<db>.resources/` | `neuroscifi:neuroscifi` 2770 (was 0700); the files in it 0660, those only the service account writes 0640 | Both read and write: the service account its audio beacon, use markers, in-use and slot locks; the idle account the policy's settings, state, events and locks |
| `/var/lib/neuroscifi-wake` | idle account `:neuroscifi` 3770 | The service account leaves requests; the idle account removes them; sticky, so nobody removes another's |
| `/etc/polkit-1/rules.d/49-neuroscifi-idle.rules` | `root` 0644 | The idle account may `start` and `stop` the model services' units, and nothing else (`systemctl --no-ask-password`, which the pass and the wake unit run without a terminal) |

- *One group, the service account's own.* The idle account is added to the group `neuroscifi`. The service
  units run with `PrivateUsers=yes`, where only the unit's own user and group are certain to keep their meaning;
  so what the idle account writes for them carries that group (the folder's setgid bit does it). The price: the
  idle account can read `/etc/neuroscifi` too (`root:neuroscifi` 0750). It is in the docker group, which already
  equals root on the host; the installer says so when it adds it.
- *The folder's mode decides the files' mode* (`resources/shared.py`). In a folder that is setgid and
  group-writable a file this code creates is made 0660 on the open descriptor, because the units' `UMask=0077`
  would remove the group's bits. A file only the service account writes (the audio beacon `audio.json`, the
  in-flight files `inflight-<pid>.json`, the internal-state history) is made 0640 instead: the idle account reads
  it and cannot rewrite what the service account wrote, which the readers' ownership check alone cannot tell
  (review1b R2; a file created 0660 before this change keeps its mode until it is next replaced). Anywhere else
  (one account: `nsf start`, a checkout) the folder is created 0700 and its files 0600 as before. The code never changes the mode or owner of a folder, or of a file it did not
  create in the same call. A use marker the other account created is set to "now" (a chosen time needs the
  file's owner).
- *A database this account may not read is not "no task".* `Idle.running_tasks` gives None then, and the pass
  unloads nothing and says why ("whether a research task runs cannot be told … `deploy/install.sh
  --idle-access` grants it"). Before, an unreadable database read as zero tasks. While a process has the database
  open its write-ahead log's files exist and the read-only open works; when nobody has it open they do not, the
  idle account cannot create them in a folder it may not write, and the file is read as it stands
  (`immutable=1`). The file is the service account's, so the idle account opens it only when it is a plain file
  (not a link, a FIFO or a device), read-only, with `PRAGMA trusted_schema=OFF` and `query_only=ON`, and counts
  only when `web_tasks` is a table (a view written by that account gives "cannot tell"). A swap between the look
  and the open is not excluded; its worst is a pass that hangs until its unit's `TimeoutStartSec` and unloads
  nothing.
- *Root never acts inside the service account's folder* (review F1, security H1). The service account owns
  `/var/lib/neuroscifi` and can put anything there; root's `chgrp`/`chmod` follow links, so a link from the
  database's name to `/etc/shadow` or from `.resources` to `/etc` made that account root, and `find … -exec`
  raced. Every step inside the folder now runs **as the service account** (`setpriv --reuid neuroscifi --regid
  neuroscifi --init-groups`, `runuser` without setpriv): each is something that account could do anyway, and the
  kernel refuses the rest, so no check has to win a race. A link is still refused and named before, so the owner
  learns of it. Root acts only on root's own paths: the state folder's own name in `/var/lib`, the request
  folder in `/var/lib`, `/etc/group` (gpasswd) and the polkit rule under `/etc`.
- *A plain re-run keeps it* (review F7). When the request folder or the polkit rule exists, `deploy/install.sh`
  without `--idle` (an upgrade) keeps the state folder at 0710 and the request folder writable in the console,
  worker and Pocket units, and says so. RUNBOOK section 1 says how to take the arrangement away.
- *What the idle account reads in the shared folder* (security L4). `idle.json`, the events and the audio
  beacon are read without following a link and without blocking (a FIFO), plain files only, at most 4 MiB (the
  events from their tail), and a state file of the wrong shape is reported (`state_problems` in `nsf resources
  idle`) and read as empty instead of stopping the pass or the wake command. Files it creates or touches there
  are opened the same way and their times set through the descriptor.
- *The polkit rule* is evaluated by a test with its own JavaScript over every combination of action, unit
  detail (the listed units, other units, a transient unit, none), verb and caller: YES for `manage-units`,
  the listed units, `start`/`stop` and the idle account only. **Not verified:** that a running polkitd and
  systemd pass exactly these details (`pkcheck` needs the rule installed under `/etc` and polkitd running).
- *Verified with real accounts.* `scripts/two_account_check.py` (run by
  `tests/test_deployment_accounts.py`, skipped with the reason where the host forbids it) enters a user
  namespace in which it is root over further uids and its own mounts, creates the accounts with the installer's
  commands, runs the real `deploy/install.sh --idle-access` and then acts as each account with this code: 46
  checks, §4, among them the service account's attacks on the installer (links for the database, its `-wal` and
  `.resources` to root-only targets, links inside the shared folder, what a race could do at worst) and a plain
  re-run. **Not shown by it:** systemd itself (the units, `PrivateUsers=yes`, the path unit as a system unit),
  polkit acting on the rule, and the real root account.

Rules of every reload, found in review and fixed on 2026-11-23:

- *A container that does not exist yet is not gone.* `systemctl start` of a `Type=simple` unit returns before the
  wrapper has created the container; the wait that followed died in 130 ms with "container is gone" and the reload
  of every stopped unit was recorded as failed while the model came up. The three `run.sh wait` loops now wait for
  a missing container for 60 s (`NSF_WAIT_GONE_GRACE`) before calling it gone.
- *Running is not answering.* A container found running counts as loaded only once it answers `/metrics`, else the
  deploy's wait is run first. A start, or a found container, that does not become ready is stopped with its shape's
  own command and the event says `stopped: true`: the state "unloaded" must stay true for the free-memory check,
  and a crash-looping container holds memory. Speech synthesis and SearXNG have no `/metrics` and had kept
  "running means loaded"; since 2026-11-24 they are asked at their readiness address (`/health`, `/healthz`) on
  the port Docker publishes for the container (`docker port`, so a port the owner changed is still the right
  one). Speech synthesis then runs its wait command; SearXNG, which has none, is asked again every 2 s for up to
  60 s, the limit of its own start. A service whose published port cannot be told counts as before.
- *One reload, one retry limit, for all processes.* The 30 s limit after a failure is the failure time in
  `idle.json`, and the decision to start, the start and its marker happen under one short lock. That lock is never
  waited for: a lookup that finds it held answers "loading" (whoever holds it is starting the reload), so a
  process stopped while holding it cannot block memory lookups. Where reloads go through wake requests no lock is
  taken at all; the request file, created exclusively, is the "one at a time".
- *The key goes to the service only.* `deploy/memory/run.sh wait` sends its warm-up request, which carries the key,
  only when Docker reports that container running and publishing that port on the loopback address; a port that
  answers without that fails the wait. In Python there is one check, made by the inference gateway before every
  request of every provider (`Gateway.stranger` → `idle.listener_refusal` → `monitor.stranger`); `nsf web memory`
  and `nsf setup` ask the same function before they try. It reads who owns the socket that would **accept this
  connection** (`/proc/net/tcp`, `tcp6`): a socket bound to the address the gateway connects to, else the
  wildcard of that family, else for IPv4 the IPv6 wildcard (a dual-stack socket). Root (Docker's port proxy), the
  same user and the owner of the key file are the service; a process of any other account gets neither the key
  nor a request, the call ends `unavailable` with the address, port and uid, and a memory lookup is lexical with
  that reason. The tables are parsed once a second at most (4.0 ms a parse on the reference host, 0.006 ms a
  check from the parse; before, 4.0 ms on every request and four times per memory lookup).

  What the check deliberately does not do (second review, S1; each was a wrong refusal before):
  - It applies only to the ports of this deployment's own services (the answer model, speech recognition, the two
    memory models: `idle.managed_service`). An endpoint the owner points elsewhere on this computer, such as
    Ollama or any server under its own account and port, is not inspected, as before the check existed.
  - A listener on another address of this computer does not count (a resolver on `127.0.0.53:53` does not hold
    `127.0.0.1:53`; binding the model's port on a bridge address does not turn the model off).
  - Uid 65534 is an account that cannot be identified, not a stranger: it is never refused, and a key file that
    reads as owned by 65534 trusts nobody. This is what every other account looks like under `PrivateUsers=yes`,
    **so in the hardened console, worker and Pocket units the check refuses nobody**; it protects the processes
    that see real uids (`nsf start` under one account, the CLI, the idle and wake units).
  - `NSF_LISTENER_CHECK=0` turns it off, for an owner who runs one of the services under another account on its
    usual port.
  - Never a refusal: nothing listening, unreadable tables, any surprise while reading them.
  - Not covered by any check: a process of the same account or of root, which can read the key file itself.

Three consequences, each tested (`tests/test_resources_idle.py`, "the memory models"):

- Nothing announces this reload. Presence shows `waiting` only for a service somebody is waiting for
  (`Service.background`; `test_a_lookup_never_waits_for_an_unloaded_memory_model_it_starts_the_reload_in_the_background`,
  and `test_a_model_somebody_waits_for_is_still_shown_as_starting` for the others).
- With one of the two models unloaded the lookup is lexical, not ranked by the embedding alone: a result must not
  change its meaning (with or without the cut of unrelated statements) from one lookup to the next because of the
  idle policy.
- A reload that was refused for lack of memory, or that failed, is not started again by every lookup: the next
  attempt starts at least 30 s after the last one did (`BACKGROUND_RETRY`; the lookups in between are lexical and
  say why: "not enough memory to load memory search (…): …" or "… did not start: …").

The policy manages only this host's own two containers, recognised by their loopback ports (18441 and 18442, or
`NSF_MEMORY_EMBED_PORT` / `NSF_MEMORY_RERANK_PORT`). A memory endpoint the owner configured elsewhere is called as it
is and never stopped or started (`test_a_memory_endpoint_elsewhere_is_never_touched_by_the_idle_policy`).
`nsf web memory` reports an unloaded model as `asleep`, not `unavailable`.

Two bounds apply to every load, on demand or by `nsf resources load`:

- **Free memory first.** Before a load the policy compares MemAvailable with the service's footprint: what its last
  load took or its last unload freed (MemAvailable before and after, so GPU allocations count on unified memory),
  else the documented figure of §4 (vLLM 44.1 GiB, ASR 4.1 GiB, TTS 1.5 GiB; SearXNG has none and is checked only
  once a load or unload has measured it).
  A load that would leave less than the critical threshold (§2) free is refused: no container is started, the
  refusal is a `load` event with `ok: false`, `refused: true` and the three numbers, and the call or
  `nsf resources load` (exit 1) ends with the reason, for example "not enough memory to load the local model: …".
  On a shared host another workload may have taken the memory the unload freed, and a model loaded into too little
  memory crash-loops. When `/proc/meminfo` cannot be read the check is skipped and says so.
- **The caller's deadline.** A call waits for a reload no longer than it may take itself. The gateway passes its
  call's timeout, capped by `read_timeout`; the reload runs in a background thread that the callers of the process
  share. A call whose time runs out ends `unavailable` at once with "Starting the local model, about N s: not ready
  within this call's T s; it keeps starting in the background", and the next call finds the model loaded or still
  loading. Before this bound a 0.5 s call waited out the whole wait command (up to 1200 s for vLLM).

The wait commands fail early instead of sitting out their limit. `deploy/vllm/run.sh wait` ends on the first poll
that finds the container `restarting`, `exited` or `dead`, or with a restart count higher than when the wait began;
with `--restart unless-stopped` a crash-looping container keeps existing, so its presence alone proves nothing.
Tested with a fake docker (`test_run_sh_wait_fails_at_once_when_the_container_is_restarting`,
`test_run_sh_wait_fails_when_the_restart_count_grows_between_two_polls`,
`test_a_reload_that_would_not_fit_in_free_memory_is_refused_recorded_and_starts_nothing`,
`test_callers_that_run_out_of_time_share_one_background_reload_and_each_is_told_once`).

While the policy has vLLM
unloaded, the inference health check (`Gateway.health`) reports `unavailable` with `idle_unloaded: true` and the
reason "the local model is unloaded while idle; the next request starts it again (about 33 s)", not an outage. The
voice engine health reports ASR and TTS unavailable until a voice session starts them.

**How stop and start run.** The policy uses the deploy's own commands. vLLM uses `deploy/vllm/run.sh
stop|start|wait`, ASR and TTS use `deploy/voice/run.sh stop|start|wait asr|tts`, the memory models use
`deploy/memory/run.sh stop|start|wait embed|rerank` (with the single server either name acts on its one
container), and SearXNG uses `nsf web searxng stop|start`. When the
service's systemd unit is active, the policy runs `systemctl --no-ask-password stop|start <unit>` instead, so it
never fights the unit (an earlier reason this policy was manual). SearXNG's unit is treated like the others since
2026-11-24: its own commands write the instance database, which the idle account of an installed deployment may
only read. The user running the pass then needs permission to manage that unit: `deploy/install.sh --idle`
installs a polkit rule that allows the idle account `start` and `stop` on exactly these units (§3(d)); without
it the event records the refusal and the service stays loaded.
A unit the policy stopped is no longer active, so the unload records its shape and the reload starts that unit
again. Before 2026-11-23 the reload looked only at whether the unit was active, found it stopped and ran
`run.sh start`: a second, unsupervised container beside the unit
(`test_an_active_systemd_unit_is_stopped_and_started_with_systemctl`, whose fake systemd now reports a stopped unit
as inactive).
Install the timer by hand (`deploy/systemd/neuroscifi-idle.{service,timer}`, header comments).

**Events.** Every load, unload and timeout change is one line in `<db>.resources/idle-events.jsonl`: time, service,
action, reason, who (`idle policy`, `on demand`, `owner`), success, seconds, shape (`docker` or `systemd`) and, for
a load, what the user was told, the wording of the free-memory check (`headroom`) and the memory the load took
(`took_bytes`, when MemAvailable fell); an unload records what it freed (`freed_bytes`), and a refused load carries
`refused: true` with `available_bytes`, `need_bytes` and `floor_bytes`. The file is capped at the last 500 to 1000
lines. `nsf resources` and `GET /api/resources` show the last 20.

**What it saves, and what a reload costs.** These are the §4 figures. vLLM holds 43.1 GiB of GPU memory and 7.4 GiB of
container memory. ASR holds 4.1 + 3.1 GiB and TTS 1.5 + 2.5 GiB; on unified memory the two accountings may overlap.
The last measured start-to-ready times, read from the container logs on 2026-11-21 (`nsf resources --cold-start`),
are vLLM 32.5 s, ASR 17.2 s and TTS 5.8 s. A reload through the policy adds the time to create the container and the
wait command's poll interval (5 s for vLLM, 3 s for voice). Restarts of the shared vLLM through `deploy/vllm/run.sh`
for the flag measurements of 2026-11-23 took 30 to 83 s to healthy (`docs/quality/evidence/accel_vllm_flags.json`,
`restart_seconds_to_healthy`). The live reload time of vLLM through the idle policy
remains to be measured by the owner: the first reload records it in the event log, and later messages use it. The
lane that built this policy did not restart the shared vLLM, ASR, TTS or SearXNG containers. It verified the stop,
reload and measurement path against a disposable container (§4).

The two memory models hold 4.26 GiB together in the single server of `deploy/memory/server.py`, the default (1.67
GiB of process memory, 2.59 GiB of device memory; `docs/quality/evidence/open_items_memory_server.json`). Served by two vLLM servers
(`NSF_MEMORY_SERVER=vllm`) they hold 4.6 GiB each: 1.9 GiB of device memory (1.1 GiB of weights, the 0.25 GiB
key-value cache vLLM requires for a 2,048-token input, the rest the CUDA context) and 2.6 GiB in the container's two
processes, most of it libraries a full vLLM server imports. With the single server `embed` and `rerank` are two
names of one container and one unit, and the policy treats the container as one unit (`Idle.group`):

- its idle timeout is the most conservative of its names: a name set to off keeps it for good, otherwise the longest
  timeout counts (`nsf resources` shows `shared_with` and `effective_timeout_s` for each name);
- calls under every name take their in-use lock and mark their use as soon as the policy is on for any name, so a
  rerank lookup keeps the container even when only `embed` has a timeout;
- an unload, the owner's `nsf resources unload embed` included, takes the in-use locks of every name in one fixed
  order and waits until no call under either name is in flight; it stops the container once and both names say
  "unloaded" (the result's `also` names the other one);
- one decision per container per pass (the second name reads "one container with embed: decided there"); one load
  lock per container, so reloads under both names start it once; a container found running marks both names
  loaded.

The figures below were measured with two vLLM servers. An unload frees 3.6 to 4.7 GiB by its
own measurement, and both together returned 7.9 to 9.4 GiB of MemAvailable. They were unloaded and reloaded live
through this policy (§4): 1 to 2 s to unload each, 20 to 29 s until both answer again.

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

**Idle unload of the memory models, live** (2026-11-23; the real `neuroscifi-memory-embed` and
`neuroscifi-memory-rerank` containers, a temporary individual set up by `nsf setup`, the policy run by direct
calls with a 1 min timeout, no timer installed; `docs/quality/evidence/accel_decisions_memory_service.json`,
four runs, the last two with the reload as a process of its own):

| Observation | Value |
|---|---|
| A pass right after a lookup | both kept: `used 0 s ago (unloads after 1 min idle)` |
| A pass 66 s later | both unloaded (`idle 1 min (timeout 1 min)`), 1.0 to 1.8 s each; `/metrics` of both read for requests in flight |
| Memory freed, by each unload's own measurement | 3.6 to 3.8 GiB (embedding) and 4.6 to 4.7 GiB (reranker); MemAvailable 30.0 to 30.4 GiB with both loaded, 38.3 to 39.3 GiB without |
| `nsf web memory` meanwhile | both `asleep`: "unloaded while idle; the next memory lookup is ranked by its words and starts it again" |
| A lookup that finds both unloaded | lexical in 2 to 4 ms, `waking: ["embed", "rerank"]`; as the one-shot command `nsf web knowledge TOPIC`: 0.15 to 0.18 s for the whole command, which then exits |
| Both models answering again | 20 to 29 s after that lookup (each reload records 16.4 to 28.7 s), although the command that asked had long exited; a lookup by another process 0.2 s after it started no second reload |
| The first lookup after the reload | semantic in 65 to 74 ms. Before `run.sh wait` sent a warm-up request it took 1.86 s: a model's first request after a start is 1.0 to 1.5 s, the next ones about 20 ms |
| Presence during the reload | no `waiting` entry (`loading()` empty) |
| Footprint recorded by the two reloads | 7.7 to 8.8 GiB each, because they ran at the same time; above the ceiling of 1.5 times the documented 4.4 GiB, so not believed, and the next free-memory check used each unload's figure |

**The review fixes, live** (2026-11-23, transient user units, the real embedding container, one account):

| Observation | Value |
|---|---|
| The embedding model started as a unit (`run.sh foreground embed` under the unit's sandbox), then `run.sh wait` at once, before the fix | exit 1 after 130 ms: "embed container is gone" |
| The same wait after the fix | "embed ready after 35s", exit 0; `docker port` reports `127.0.0.1:18441` |
| Unloaded by the policy, then a lookup with `NSF_WAKE_DIR` set (starting anything itself forbidden) | "loading" in 0.4 ms; `embed.wake` left in the folder |
| A transient path unit (`PathExistsGlob=…/*.wake`) running `nsf resources wake` | started by the request; the model answered again 36.0 s after the lookup; event `on demand: wake request`, 35.5 s; the request file gone; unit result `success` |
| The same unit (start limit 30 in 10 min) with a non-empty folder named `rerank.wake` in the request folder, 12 s | before the fix: 29 starts, then `start-limit-hit`, the watch failed and a later request stayed unserved. Now: 1 start, the folder renamed to `.stuck-rerank.wake.…`, the watch active, the next request served by 1 start |
| The same, with an entry that can be neither removed nor renamed (the folder read-only), 70 s | 3 starts (one, then one each 30 s), the watch active throughout |
| The listener check, per call (421 socket lines on the host) | 4.0 ms before (every request parsed both tables); now one parse a second (4.2 ms) and 0.006 ms a check from it |
| Another process answering `/health` on the freed port 18441 | `run.sh wait` exits 1: "another process holds the port. The key was not sent"; that process received no request carrying the key |

One model stopped by hand (no idle policy involved): the lookup was lexical in 30 to 40 ms with "semantic retrieval
failed (unavailable): cannot connect: Connection refused", the next lookup inside the 30 s back-off cost 0.9 ms
and tried nothing, and the first lookup after the back-off with the model running again was semantic.

**The two accounts, with real uids** (2026-11-24; `scripts/two_account_check.py` in a user namespace: root of the
namespace, the accounts `neuroscifi`, an idle account and a third one, the real `deploy/install.sh --idle-access`,
a fake Docker for stop and start; evidence `docs/quality/evidence/open_items_deployment.json`):

| Observation | Result |
|---|---|
| Before the grant: the idle account asks whether a research task runs | cannot tell (None); the pass would unload nothing |
| After `--idle-access` | state folder `710`, database `640`, `.resources` `2770` with the file that was in it now `660`, request folder `3770` owned by the idle account; another file of the service account still `600`; a second run changes nothing |
| The idle account: list the state folder, read another file in it, write the database, create a file beside it | refused, each (PermissionError; the write fails) |
| A third account: enter the state folder or the request folder | refused |
| The idle account counts tasks while the service account has the database open with a task running | 1; and the pass unloads nothing ("1 research task(s) running") |
| The same with nobody holding the database open (no write-ahead log beside it) | 0, read as the file stands |
| Files each account creates in `.resources` under `UMask=0077` | `660`, group `neuroscifi`, readable and writable by the other |
| A service unused for longer than its timeout | unloaded by the idle account's pass; the service account sees it and leaves `vllm.wake` |
| The service account removes an entry the idle account left in the request folder | refused (sticky) |
| The idle account serves the request | the service starts again, the request is gone, the service account reads "loaded" |
| A real listener on a service's port, asked about by the service account | refused when a third account owns it, and when the idle account owns it without being the key file's owner; accepted for the service account itself, root, and the key file's owner |
| The code imported by a uid with no account and no HOME | imports, reads its settings; no key file found, no error |
| The units' own command lines as the idle account: `nsf resources tick --db …` and `nsf resources wake --db … --dir …` | both exit 0; the pass can tell whether a task runs; the wake command clears a request the service account left |
| The service account plants links for a database (`planted.sqlite`, its `-wal`, its `.resources`) to root-only targets, and a link inside the shared folder; root then runs `install.sh --idle-access --db planted.sqlite` and the plain `--idle-access` (review round, 2026-11-24) | the first refuses and names the link, exit 1; the second passes over the inner link; every target keeps `root:root 600` / `755`. On 76f32d2 the targets became `root:neuroscifi 640` and `2770` |
| What a race could make the installer do at worst (chgrp/chmod on such a link, as the service account) | refused by the kernel, each |
| A plain `install.sh --dry-run` (no `--idle`) after the arrangement | "a two-account arrangement is installed … it is kept"; the state folder at 0710; the three units keep the request folder writable (on 76f32d2: 0700, and the units without it) |

46 of 46 checks; the same checks fail where they should on the code before (the installer had no such step,
`Path.home()` raised at import; on 76f32d2 five of the 46 fail: the four on the links and the plain re-run).

**The wake unit's start limit, live** (2026-11-24; a transient user path unit, `StartLimitBurst=5` in 60 s
standing in for 30 in 10 minutes, a request every 2 s for 70 s; no container touched: the requests name no
service):

| Command of the unit | Starts | End state | Requests left unserved |
|---|---|---|---|
| `nsf resources wake` (before) | 5, then refused | service `start-limit-hit`, path unit failed (`unit-start-limit-hit`) | 30 of 35 |
| `nsf resources wake --linger 15` (a quarter of the window, as 25 s is to 10 minutes) | 4 | service `success`, path unit active | 0 |

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
- The reload is on demand. Nothing preloads a service ahead of use. A call waits for the reload at most as long as
  its own timeout and then ends `unavailable` (§3(c)). The reload and the request share one allowance since
  2026-11-24: what the reload took comes off the request's time, and a reload that took all of it ends the call
  `timeout` ("the local model was started again for this call and that took all of the call's N s; it is loaded
  now"). Before, such a call could take up to twice its timeout. A call that meets an unload in progress (the
  pass holds the in-use lock while the service stops, up to 120 s) waits for it no longer than its own time either,
  and ends `unavailable` ("… is being unloaded while idle and this call could not wait N s for it") without
  claiming a reload (review F5: before, it waited for the whole stop).
- The free-memory check uses one reading of MemAvailable and one footprint. Another workload that changes memory
  at the same moment skews both the check and the next measured footprint.
- The memory models are released while idle only when the owner sets their timeouts, like every other service. The
  first lookups after an unload are lexical: about half a minute on the reference host, until both models answer
  again. The two reloads then run at the same time, so each one's measured footprint includes the other's and is
  discarded by the ceiling rule (a reading above 1.5 times the documented 4.4 GiB is not believed); the footprint
  used for the next free-memory check is then the one its last unload measured, else the documented one.
- Where one user runs everything (`nsf start`), a reload on demand is started by the process that needed the
  service. Under the units of `deploy/install.sh` the worker and the console run as `neuroscifi`, which has no
  Docker access and may not start units: there reloads go through **wake requests** (§3(c)), which need
  `neuroscifi-wake.path` and `neuroscifi-wake.service` installed and `NSF_WAKE_DIR` set for the console and the
  worker. Without them a reload on demand fails there and says so; `nsf resources load SERVICE` by the Docker user
  brings the service back. `deploy/install.sh --idle` installs the idle timer and the wake units, creates the
  request folder and arranges what the two accounts share (§3(d)); the database must exist for the last step, so
  after `nsf init` it is run again as `--idle-access`, and until then the pass unloads nothing and says why.
  Exercised with real accounts in a user namespace (the installer's own commands, both accounts and a third
  acting with this code: §3(d), §4) and with transient user units (§4). **Not exercised**: the units installed as
  system units and started by systemd, `PrivateUsers=yes` as systemd sets it up, polkit acting on the installed
  rule, and the real root account.
- The idle account reads the whole instance database to answer one question (does a research task run). It is in
  the docker group, which already equals root on the host, so this grants nothing that account could not take;
  an account without that power would need the worker to publish the fact in `<db>.resources/` instead (the way
  the audio beacon does), which is not built.
- SearXNG's unit (`deploy/install.sh --searxng`) runs `nsf web searxng start` as a Docker user. For an account that
  may read the instance database but not write it, `nsf web searxng start|stop|status` reads the Web Cortex
  configuration (read only, through the idle pass's opener: no link or FIFO, nothing of the schema trusted) and
  writes nothing; a first start says which command the database's owner runs once to set `searxng_url`, and
  `health` stays with that owner because it records what it finds. With `--idle --searxng` the installer writes
  the unit's drop-in (`User=` the idle account, `NSF_DB`), so the unit and the idle pass start and stop SearXNG as
  the same account, with no right added to it (two-account check, §3(d)). Not exercised: systemd running that unit.
- A lookup's use of a memory model is seen by the idle policy only when it goes through `knowledge.query` in a
  process that knows the individual's file. A result served from the per-process cache uses no model and marks no
  use.
- Queue order is by who asked, not by how long a task takes. One worker runs one task at a time, so a question
  queued while a task is running waits for that task to finish; and an autonomous task queued more than 10 minutes
  ago is claimed in age order with the owner's questions (§3(a)).
- The inference server's state is read for a vLLM server on this host only. Its interval figures are per process:
  two processes reading the same server each keep their own previous reading.
