# NeuroSciFi operations runbook

Audience: the owner or operator of one NeuroSciFi instance on a Linux host such as the NVIDIA DGX Spark (Arm, DGX OS, Ubuntu-based).

Status of this document: these are procedures. **No service deployment has been performed.** The units, `install.sh` and the switchover steps have not been run as real system services. The software itself was built and tested on a DGX Spark (GB10) development host running a general-purpose distribution, not DGX OS, with no sensors attached. Steps marked *(unverified on a host)* use system tools (`systemd`, `cryptsetup`, `ssh`) that the project's tests do not cover. Commands marked *(available from 0.5.0)* belong to the console, acceptance and doctor tools and are verified by the integrator, not by this document.

Related: [ENVIRONMENT.md](ENVIRONMENT.md) (every setting), [MIGRATIONS.md](MIGRATIONS.md) (upgrades), [TESTING.md](TESTING.md).

## 0. Conventions

All commands run on the host. `nsfs` runs `nsf` as the service user, with the service's database:

```sh
nsfs() { sudo -u neuroscifi env NSF_DB=/var/lib/neuroscifi/instance.sqlite /opt/neuroscifi/venv/bin/nsf "$@"; }
```

Always run `nsf` as `neuroscifi`, never as root. A root-run command creates root-owned files in the state directory that the service then cannot write. Every `nsf` instance command prints JSON on stdout. Instance errors exit with status 2 and a one-line message on stderr.

Paths: code `/opt/neuroscifi/venv`, state `/var/lib/neuroscifi` (0700, owned by `neuroscifi`), settings `/etc/neuroscifi/neuroscifi.env`, TLS files `/etc/neuroscifi/tls`, invitation file `/run/neuroscifi/invitation`.

## 1. Install (no services started)

1. Prepare the encrypted volume and mount it at `/var/lib/neuroscifi` **before** installing (section 11, checklist item 5). The units refuse to start unless that path's mounts are up (`RequiresMountsFor=`), but systemd cannot tell whether the volume is encrypted.
2. From the project folder, review the plan, then install as root:

   ```sh
   bash deploy/install.sh --dry-run
   sudo bash deploy/install.sh
   ```

   `--dry-run` works without root and prints each action. A real run without root refuses and exits 1. The script creates the `neuroscifi` user, `/opt/neuroscifi/venv`, `/etc/neuroscifi`, copies the units and runs `systemctl daemon-reload`. It never enables or starts anything. Run it again after an upgrade of the source tree; existing users, directories and the env file are kept. Options: `--wheel FILE`, `--source DIR`, `--wheelhouse DIR` (offline), `--prefix DIR`, `--python PATH` (see `bash deploy/install.sh --help`).
3. Review `/etc/neuroscifi/neuroscifi.env` (copied from `deploy/neuroscifi.env.example` only if absent).
4. Create the individual. `nsf init` refuses an existing file, so it is safe to repeat.

   ```sh
   nsfs init --note "first instance"
   nsfs birth
   ```

## 2. Start and stop

Nothing starts at boot until you enable it. Start deliberately:

```sh
sudo systemctl start neuroscifi-pocket.service
sudo systemctl status neuroscifi-pocket.service
sudo systemctl stop neuroscifi-pocket.service
sudo systemctl start neuroscifi-console.service        # (available from 0.5.0)
sudo systemctl start neuroscifi-checkpoint.timer       # nightly sweep and cold checkpoint
systemctl list-timers neuroscifi-checkpoint.timer
sudo journalctl -u neuroscifi-pocket.service -n 50
```

Enable at boot only after the rehearsal and switchover below: `sudo systemctl enable neuroscifi-pocket.service`. Disable with `sudo systemctl disable --now neuroscifi-pocket.service`.

The servers keep no access logs by design. The journal holds start, stop and error lines only. The pairing code never appears in it (section 3).

## 3. Pairing and invitations

Pocket issues one invitation per run, valid five minutes, usable once. The service passes `--invitation-file`, so the code goes to a 0600 file in a tmpfs runtime directory, not to the journal.

1. Start or restart Pocket: `sudo systemctl restart neuroscifi-pocket.service`. The journal says `Invitation written to /run/neuroscifi/invitation`.
2. Read the code within five minutes: `sudo cat /run/neuroscifi/invitation`.
3. On the phone, open the HTTPS address and enter the code. The file is deleted the moment the pairing succeeds, when the five minutes expire, and when the service stops. A wrong code does not consume it.
4. For another phone or after expiry, restart the service (a new invitation each time). A paired phone can instead hand off to a tablet with **Link my phone or tablet**, which needs no operator step.

List and erase pairings:

```sh
sudo -u neuroscifi /opt/neuroscifi/venv/bin/nsf pocket list-pairings --db /var/lib/neuroscifi/pocket.sqlite
sudo -u neuroscifi /opt/neuroscifi/venv/bin/nsf pocket erase-pairing LABEL --db /var/lib/neuroscifi/pocket.sqlite
```

Without the service (foreground, loopback, code on the terminal): `nsf pocket serve --db PATH`. If you pass `--invitation-file PATH` and the file already exists, `serve` exits with an error and leaves it alone; the file must not exist.

## 4. Console access over an SSH tunnel *(available from 0.5.0)*

The console binds loopback only (`--port 8766`) and the unit also denies all non-loopback traffic. Reach it from your own computer:

```sh
sudo systemctl start neuroscifi-console.service
sudo cat /run/neuroscifi/console-url            # the URL the console wrote (--url-file); gone after a stop
ssh -N -L 8766:127.0.0.1:8766 USER@SPARK_HOST   # on your computer; leave running
```

Then open the URL from the file in a browser on your computer, with its host and port unchanged (the tunnel keeps port 8766 on both ends). Close the tunnel when finished. The file lives in the unit's runtime directory (tmpfs), is removed when the console stops, and a restart (`systemctl restart`, a crash, a reboot) writes a new URL with a new token, so read the file again after each start. If you run the console by hand and a crash left a stale URL file behind, the next start replaces it; it refuses any other existing file. Do not change the console to a LAN bind. Run `nsf console --db PATH [--port 8766] [--url-file PATH]` in the foreground instead of the unit for a one-off session.

## 5. Checkpoint, verify, restore

The timer runs `nsf sweep` and `nsf checkpoint rebuild --policy cold` nightly at about 03:15 (random delay up to 15 minutes, `Persistent=true` catches up after downtime). `rebuild` writes one fresh checkpoint and deletes archives made stale by a forget. Archives are in `/var/lib/neuroscifi/checkpoints` (0600 files, 0700 folder).

Manual checkpoint, list and verify (do this before every upgrade or switchover):

```sh
nsfs checkpoint create --policy cold
nsfs checkpoint list
nsfs checkpoint verify ID
```

`verify` decodes the archive and runs SQLite's integrity check. A `status` of `missing` or `stale` in `list` means the file is gone or predates a forget. Run `nsfs checkpoint verify` on the newest id at least weekly and after any disk incident. A checkpoint that has never been verified is not a backup.

Restore never overwrites. It writes a new file, re-applies current tombstones so forgotten ids stay forgotten, and is performed while the services are stopped:

```sh
sudo systemctl stop neuroscifi-pocket.service neuroscifi-console.service neuroscifi-checkpoint.timer
nsfs checkpoint restore ID /var/lib/neuroscifi/restored.sqlite
sudo -u neuroscifi mv /var/lib/neuroscifi/instance.sqlite /var/lib/neuroscifi/instance.sqlite.damaged
sudo -u neuroscifi mv /var/lib/neuroscifi/restored.sqlite /var/lib/neuroscifi/instance.sqlite
sudo -u neuroscifi rm -f /var/lib/neuroscifi/instance.sqlite-wal /var/lib/neuroscifi/instance.sqlite-shm
nsfs birth
```

`restore` reads the checkpoint registry from the live database. If that database is itself unreadable, restore from outside: copy the `.nsfa` file out of the checkpoint folder and unpack with `nsf archive unpack SOURCE DESTINATION` (see `nsf archive --help`; this path does not re-apply tombstones, so forgotten ids may return and must be forgotten again). Delete `instance.sqlite.damaged` only when you no longer need it, since it may hold data you have been asked to erase.

Backups: the checkpoint folder lives on the encrypted volume. Do not copy the Pocket database to any backup or cloud sync (section 10). Copying checkpoint archives off the host is a deliberate decision: they contain everything remembered up to their creation, including data later forgotten if the archive is stale.

## 6. Forgetting and erasure requests

Forgetting removes evidence, leaves a payload-free tombstone, recomputes beliefs, supersedes affected open questions, invalidates derived graphs and marks checkpoints stale.

| Request | Command |
|---|---|
| One observation | `nsfs forget evidence EVENT_ID` |
| A whole calibration epoch | `nsfs forget epoch NAME` |
| Everything tied to one partner | `nsfs forget partner NAME` |
| A stored dataset | `nsfs forget dataset ID` |
| Keep one episode past its lifetime | `nsfs promote ID` |
| Expire everything past its lifetime now | `nsfs sweep` |

Find ids with `nsfs memory evidence --epoch NAME`, `nsfs memory dataset`, and `nsfs memory deletion` (the deletion log).

An erasure is complete only when old copies are gone. After every forget:

1. `nsfs checkpoint rebuild --policy cold` (the nightly timer also does this). This writes a clean archive and deletes stale ones.
2. `nsfs checkpoint list` and confirm no `stale` rows remain.
3. Check `nsfs memory deletion` for the entry and for a `failure` field.
4. Pocket data: the person's own phone records expire after 24 hours; unpairing deletes a device's records at once (section 8).

Out of scope for the erasure guarantee, which you must state to the requester: copies already exported or backed up elsewhere, the damaged-file copy you chose to keep, and storage-device remnants. SQLite secure delete is on in Pocket.

## 7. Regulation and paused writes

`nsfs regulation` reports whether writes are allowed and why not.

```sh
nsfs regulation
```

Writes pause when the evidence journal reaches `max_evidence_rows` (default 1,000,000) or free space on the database's filesystem falls below `min_free_bytes` (default 64 MiB). Reads, `nsfs memory`, `nsfs explain` and checkpoint restore keep working. A paused write returns exit 2 with a "writes paused" message and HTTP 507 where applicable.

Procedure:

1. Read `reason` in `nsfs regulation`.
2. Free space: delete old files you own outside the state directory; see section 9 for a full disk.
3. Reduce rows: `nsfs sweep` removes expired evidence. Forget what is no longer wanted (section 6).
4. Run `nsfs regulation` again. `writes_allowed` must be `true`. Nothing needs restarting.

The two limits are set when the instance is created and have no command-line setting in this release. Do not edit the database by hand to change them.

## 8. Incident: lost or stolen phone

Erase that pairing at once. It deletes the device credential and everything that device originated on the Spark, including field episodes it shared.

```sh
sudo -u neuroscifi /opt/neuroscifi/venv/bin/nsf pocket list-pairings --db /var/lib/neuroscifi/pocket.sqlite
sudo -u neuroscifi /opt/neuroscifi/venv/bin/nsf pocket erase-pairing LABEL --db /var/lib/neuroscifi/pocket.sqlite
```

`list-pairings` shows random labels only, not owners. If you cannot tell which label belongs to the lost phone, erase every pairing and re-pair the phones you still have (section 3). Then:

1. Linked tablets from a hand-off are separate pairings; erase them too if they could be affected.
2. Records the phone shared to the instance journal through you (not through Pocket) are ordinary evidence: forget them if wanted (section 6).
3. If the phone had a field vault open, its local data is protected only by the person's passphrase. Advise changing it where reused. The server cannot clear a phone remotely.
4. If the lost phone also held TLS private-key material or you suspect the key leaked, replace the certificate and key (ENVIRONMENT.md) and restart.

## 9. Incident: full disk and corrupted archive

**Full disk.** The state directory is on the encrypted volume.

1. `df -h /var/lib/neuroscifi` and `du -sh /var/lib/neuroscifi/*`.
2. Writes will already be paused (section 7), which protects the database. Do not delete `instance.sqlite*` files.
3. `nsfs sweep`, then `nsfs checkpoint rebuild --policy cold` (it deletes stale archives). If space remains short, move old verified checkpoints to other storage you control, or enlarge the volume. Keep the newest verified checkpoint on the volume.
4. When `nsfs regulation` shows writes allowed, restart nothing; confirm with `nsfs birth`.

**Corrupted checkpoint.** `nsfs checkpoint verify ID` exits 2 with "fails verification". Do not restore from it. Verify the next older id; create a fresh checkpoint from the live database with `nsfs checkpoint create --policy cold` and verify it. Investigate the volume (`dmesg`, SMART data) before trusting the disk.

**Corrupted live database.** Stop services, keep the damaged file, restore the newest verified checkpoint (section 5), then re-forget anything erased after that checkpoint was taken (`nsfs memory deletion` on the restored copy shows what it knows; `restore` re-applies the tombstones it knows about). Record the incident.

**Pocket database damaged.** It holds only 24-hour records and pairings. Move it aside and start the service. A new file is created; every phone must pair again.

## 10. Reversible switchover and rollback

The brief asks for a restorable snapshot, stopped conflicting services, verification, and a reversible selection. The project's own part is the checkpoint and the service selection. Host-specific parts (what else uses the camera, microphone or actions; boot entries) are yours to record first.

**Before switchover**

1. Write down what currently owns the devices and which services conflict: `systemctl list-units --type=service --state=running` *(unverified on a host)*. Name them in your notes as `OLD_SERVICE`.
2. Take and verify a checkpoint of NeuroSciFi (section 5) and a snapshot of whatever the old system keeps. Preserve its restorable snapshot by the means that system provides.
3. Keep the previous boot or service selection available: do not remove the old units, packages or boot entries.
4. Run the rehearsal against synthetic input (section 11, item 3).

**Switchover**

```sh
sudo systemctl disable --now OLD_SERVICE            # stop and unselect the old owner (site specific)
sudo systemctl enable --now neuroscifi-pocket.service
sudo systemctl enable --now neuroscifi-checkpoint.timer
```

Verify startup, loss of input, restart and power recovery (checklist items 6 to 8).

**Rollback** (reverses the selection; data stays)

```sh
sudo systemctl disable --now neuroscifi-pocket.service neuroscifi-console.service neuroscifi-checkpoint.timer
sudo systemctl enable --now OLD_SERVICE
```

NeuroSciFi's state stays in `/var/lib/neuroscifi` and is unaffected. To roll back to NeuroSciFi's own earlier state or software, restore the pre-upgrade checkpoint (MIGRATIONS.md). Removing everything: stop and disable the units, delete the unit files and `/opt/neuroscifi`, and (only on request, because it destroys the individual) the state directory.

## 11. Commissioning checklist mapped to the brief

Brief reference: Architecture.md, "Operating as the primary system on the Spark", Commissioning. The project requirement ids are in `docs/architecture/REQUIREMENTS_MATRIX.md`.

| # | Check | How | Requirement | Done here? |
|---|---|---|---|---|
| 1 | Fresh service identity, environment, state directory | `bash deploy/install.sh`; `id neuroscifi`; `stat -c '%U %a' /var/lib/neuroscifi` shows `neuroscifi 700` | OPS-01 | Procedure only |
| 2 | Explicit device permissions | Units set `PrivateDevices=yes`, no capabilities and no extra groups. Pocket needs no host devices because sensing happens on the phone. Adding any device (a camera, a GPU) is a conscious unit edit; see ENVIRONMENT.md | OPS-01 | Procedure only |
| 3 | Rehearse against synthetic input before taking exclusive ownership | `nsf acceptance --all` *(available from 0.5.0)*; `bash scripts/check.sh`; synthetic observations written with `nsfs observe --simulated` (provenance `simulated`) into a scratch instance, never the real one | OPS-03 | Simulated only |
| 4 | Restorable snapshot preserved | Section 5: `checkpoint create`, then `checkpoint verify`, then a trial `checkpoint restore` to a scratch file | OPS-04 | Procedure only |
| 5 | Encrypted volume; bridge DB excluded from backups | `lsblk -o NAME,TYPE,FSTYPE,MOUNTPOINT` shows a `crypt` device mounted at `/var/lib/neuroscifi` *(unverified on a host)*; `nsf doctor --db PATH` *(available from 0.5.0)* reports the filesystem but cannot prove encryption; check your backup tool's exclusions for `pocket.sqlite*` | OPS-05 | Procedure only |
| 6 | Verify startup | `systemctl start`, journal shows no errors, `nsfs birth` answers, Pocket serves over HTTPS | OPS-04 | Procedure only |
| 7 | Verify loss of input | Stop the phone or close its tab: Pocket keeps running and records nothing. A stopped input never creates evidence | OPS-04 | Procedure only |
| 8 | Verify restart and power recovery | `systemctl restart`, then a hard power cycle in a maintenance window; afterwards `nsfs birth`, `nsfs checkpoint verify ID`, `nsfs regulation` | OPS-04 | Procedure only |
| 9 | Stop conflicting services during switchover | Section 10 | OPS-04 | Procedure only |
| 10 | Reversible boot or service selection | Section 10 rollback; keep the old boot entry | OPS-04 | Procedure only |
| 11 | Private LAN or VPN, trusted HTTPS, loopback by default | Env file defaults; LAN needs `--cert` and `--key` or `serve` refuses to start | OPS-02 | Verified by tests; deployment procedure only |
| 12 | No access logs of observations, addresses, URLs or tokens | `journalctl -u neuroscifi-pocket.service` shows no request lines | OPS-08 | Implemented, tested |
| 13 | p50/p95/p99 sensor-to-action latency, dropped inputs, memory high-water, joules per skill | Host decision latency, idle and under concurrent learning plus checkpointing, was measured on the development Spark (acceptance `device_ownership`; `docs/quality/evidence/acceptance.json`). Real sensor-to-action latency, dropped real inputs and joules per skill need capture devices and a power meter | OPS-06 | **Partial:** host latency measured; sensor and energy parts blocked |

Record the date, operator, host, software version (`/opt/neuroscifi/venv/bin/pip show neuroscifi`) and the result of each item. An item with no recorded result is not passed.

## 12. Web Cortex (governed web research)

Web access is off until the owner turns it on: `nsfs web config --enable`. Nothing below contacts the internet while it is off.

### Which process runs research

| Unit | Role | Network |
|---|---|---|
| `neuroscifi-webcortex.service` | `nsf web work`: runs queued research tasks (console, Pocket, `nsf web ask --queue`, the tick timer) | Public web and loopback |
| `neuroscifi-webcortex-tick.timer` | `nsf web tick`: runs due monitors, and refresh/curiosity when `--autonomous` is on | Public web and loopback |
| `neuroscifi-websearch.service` | the loopback open-webSearch daemon | Search engines and loopback |
| `neuroscifi-console.service` | submits, shows and reviews research | **Loopback only** |

The console does **not** run research by default (changed in this release). Its unit is confined to loopback (`IPAddressDeny=any`), so an embedded worker there could not reach the internet and would mark providers unhealthy for everyone. Start `neuroscifi-webcortex.service` to run queued tasks:

```sh
sudo systemctl start neuroscifi-webcortex.service neuroscifi-websearch.service
sudo systemctl start neuroscifi-webcortex-tick.timer          # monitors (and autonomous research if enabled)
nsfs web status                                                # switches, provider health, task counts
```

`nsf console --web-worker` also runs queued tasks inside the console process. Use it only for a development console started by hand on a host with network access, never in the console unit. `--no-web-worker` is still accepted and is the default. An embedded worker that fails logs the exception type and a short message and retries after 5 s, doubling to at most 60 s.

### Search daemon and the honest User-Agent (ADR-032)

`install.sh --websearch` runs `npm ci --ignore-scripts`, then `apply-honest-ua.mjs`, which replaces the scrapers' forged browser User-Agents with the Web Cortex's own. `nsf web daemon start` and the unit's `ExecStartPre` refuse an unpatched build. After a manual `npm ci`, run `npm run patch-ua` in `/opt/neuroscifi/open-websearch`. Some engines return nothing to an honest User-Agent; research then falls back to the structured providers (Wikipedia, arXiv, OpenAlex, Crossref, PubMed, PyPI, npm, OSV, GitHub). An owner who accepts impersonation can opt in with `nsfs web config --search-browser-ua` (off by default; `nsfs web status` and `nsfs web health` show the switch and `honest_user_agent`).

### Questions, findings and paused writes

- Web questions have their own budget: at most `max_open_web_questions` (default 10) open, and at most `max_web_questions_per_task` (default 3) new per task. Beyond that, candidates stay provisional and one aggregate question per task (scope `web:review:<task>`) says how many await review. Review them in the console (Research) or with `nsfs web knowledge --status provisional` and `nsfs web review ID --accept|--reject`. `nsfs regulation` reports `open_web_questions` separately.
- A monitor's meaningful change is a `finding` event and an informational question `web:finding:<monitor>:<task>`; the console's monitors table lists recent findings.
- While regulation pauses writes (section 7), nothing web-related is written: submissions, consolidation, monitors and proposals return "writes paused", the worker claims nothing (one decision event), and a running task stops at its next step as `partial` without consolidating.

### Forgetting web memory

| Request | Command |
|---|---|
| A research task, its evidence and the knowledge resting only on it (also its pages in the public cache that no other task read) | `nsfs forget research TASK_ID` |
| A knowledge item and every version of it | `nsfs forget knowledge ID` (or `nsfs web forget ID`) |
| A source URL (as requested, original or final) or every source of a domain, also from the public cache | `nsfs forget source URL_OR_DOMAIN` |

Each web forget records a payload-free tombstone (`web_forgotten`, an instance-salted digest), marks every checkpoint stale and logs only a digest label for a URL or domain in `deletion_log`. `checkpoint restore` replays those tombstones on the restored copy, so a forgotten task, knowledge version or source never comes back. Then follow the erasure steps of section 6 (`checkpoint rebuild`). Forgetting a partner (`nsfs forget partner NAME`) also replaces the name in research objectives, reports, events, claims and knowledge.

### MCP

`nsf web mcp` allows at most `mcp_research_per_hour` (default 6) `web_research` calls per hour per server process, and MCP-origin tasks share the daily allowance `daily_mcp` (tasks, requests, bytes). Results carry the report's context pack (bounded by the task's `max_context_tokens`); `full_report: true` returns the whole report.
