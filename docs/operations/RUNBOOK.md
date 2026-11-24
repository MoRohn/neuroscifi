# NeuroSciFi operations runbook

Audience: the owner or operator of one NeuroSciFi instance on a Linux host such as the NVIDIA DGX Spark (Arm, DGX OS, Ubuntu-based).

Status of this document: these are procedures. **No system-level service deployment has been performed.** The services were run under their units' sandbox as transient user units, and their encrypted storage was checked ([HARDENING_VERIFICATION.md](HARDENING_VERIFICATION.md); section 5 there lists the checks that need root). The units, `install.sh` and the switchover steps have not been run as real system services. The software itself was built and tested on a DGX Spark (GB10) development host running a general-purpose distribution, not DGX OS, with no sensors attached. Steps marked *(unverified on a host)* use system tools (`systemd`, `cryptsetup`, `ssh`) that the project's tests do not cover. Commands marked *(available from 0.5.0)* belong to the console, acceptance and doctor tools and are verified by the integrator, not by this document.

Related: [ENVIRONMENT.md](ENVIRONMENT.md) (every setting), [MIGRATIONS.md](MIGRATIONS.md) (upgrades), [TESTING.md](TESTING.md).

**Upgrading from 0.7.0?** Read [MIGRATIONS.md](MIGRATIONS.md) section 2 before you start any service. From schema 14 an expired learning event leaves a consolidated reading that is kept until you forget it, up to a limit of a million readings (section 6). To keep the old meaning of expiry, stop the services and run `nsfs config --no-consolidation` as the first command after installing; the console and the Pocket bridge expire due evidence on access, so a service that starts first consolidates first.

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
4. For another phone or after expiry, ask the running service for a new code (no restart; it replaces any unused earlier code and is valid five minutes, once): `sudo -u neuroscifi NSF_POCKET_CONTROL_DIR=/run/neuroscifi /opt/neuroscifi/venv/bin/nsf pocket invite --db /var/lib/neuroscifi/pocket.sqlite`. It prints the code on your terminal only (the request goes over a 0600 socket in `/run/neuroscifi` that only the service user can use). Restarting the service also issues a new invitation. A paired phone can instead hand off to a tablet with **Link my phone or tablet**, which needs no operator step. A device and the devices linked from it form a **pairing family**; a family holds at most 6 devices, and the page refuses a hand-off code when it is full (HTTP 409 `family_full`).

List and erase pairings:

```sh
sudo -u neuroscifi /opt/neuroscifi/venv/bin/nsf pocket list-pairings --db /var/lib/neuroscifi/pocket.sqlite
sudo -u neuroscifi /opt/neuroscifi/venv/bin/nsf pocket erase-pairing LABEL --db /var/lib/neuroscifi/pocket.sqlite
sudo -u neuroscifi /opt/neuroscifi/venv/bin/nsf pocket erase-pairing LABEL --family --db /var/lib/neuroscifi/pocket.sqlite
```

`list-pairings` prints one line per device: `LABEL  family=TAG  sponsor=invitation|LABEL  created=UTC|unknown`. Devices with the same `family=` tag were linked from one another; with `--instance-db` (or `$NSF_DB`) the tag is the one the console and `nsf web node list` show, without it `-`; `sponsor=` names the device that issued a hand-off code, or `invitation` for a device paired with the terminal code. `erase-pairing LABEL` removes one device; with `--family` it removes every device of that label's family. A device can do the same for its own family with **Forget all linked devices** (the `unpair` action with `{"family": true}`). Research submissions are limited to 10 per hour per family, and the count survives a bridge restart.

Without the service (foreground, loopback, code on the terminal): `nsf pocket serve --db PATH`. If you pass `--invitation-file PATH` and the file already exists, `serve` replaces it only when it is a stale invitation of an earlier bridge (a regular 0600 file of your user, not a symlink, holding one code, with no running bridge holding its lock or older than five minutes). Any other existing file makes `serve` exit with an error and stays as it was. A busy port prints `nsf: cannot listen on ADDRESS:PORT: Address already in use` and exits 2.

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

Restore never overwrites. It writes a new file and re-applies what the live database has forgotten since the archive was written, and is performed while the services are stopped. The replay covers tombstones (forgotten ids stay forgotten), forgotten epochs (their belief revisions, decisions, open questions, changepoints, links, graph context and the owner's note, in copies written before the forget; an epoch name used again afterwards keeps its new rows) and forgotten datasets (the blob and the lesion and coactivation effects measured on it):

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

Forgetting removes evidence, leaves a payload-free tombstone, recomputes beliefs, supersedes affected open questions, invalidates derived graphs and marks checkpoints stale. Voice and web forgets then rewrite those checkpoints in place (below).

| Request | Command |
|---|---|
| One observation | `nsfs forget evidence EVENT_ID` |
| A whole calibration epoch | `nsfs forget epoch NAME` |
| Everything tied to one partner | `nsfs forget partner NAME` |
| Everything one camera taught | `nsfs forget camera NAME` |
| One consolidated reading (what an expired event left) | `nsfs forget consolidated SEQ` |
| A stored dataset | `nsfs forget dataset ID` |
| Keep one episode past its lifetime | `nsfs promote ID` |
| Expire everything past its lifetime now | `nsfs sweep` |

Find ids with `nsfs memory evidence --epoch NAME`, `nsfs memory dataset`, and `nsfs memory deletion` (the deletion log).

**What expiry leaves (consolidation, ADR-044).** An event that reaches its lifetime is deleted and tombstoned. If it was a learning event, what it taught stays as a *consolidated reading*: its position in the journal (seq), its epoch, channel and root, the corrected reading and its sample id. Beliefs keep counting it, so the individual still knows on day 8 what it learned on day 1. The event's id, actor, action and bit, provenance and timestamps are gone. Memory-only events and repeated copies of a sample leave nothing.

- See them: `nsfs memory consolidated --epoch NAME` (the record's id is the seq). `nsfs birth` and `nsfs regulation` count them; `nsfs sweep` says how many it kept (`consolidated`).
- Forget them: `nsfs forget consolidated SEQ` forgets one reading by the seq the listing shows (the console's Memory log has a Forget button beside each reading). `nsfs forget evidence EVENT_ID` also still works after the event expired, if you kept the id. `nsfs forget partner NAME`, `nsfs forget camera NAME` and `nsfs forget epoch NAME` remove the consolidated readings they cover, in every epoch for a partner or a camera. A root's name stays in the file while any evidence of that name is held. When the first copy of a sample is forgotten, its next copy counts.
- Turn it off: `nsfs config --no-consolidation` (back on: `--consolidation`). From then on an expired event leaves nothing, as in 0.7.0. Readings already kept stay until you forget them. The change is recorded in the deletion log (`nsfs memory deletion`, kind `setting`). **On an upgrade, do this before starting any service** (MIGRATIONS.md section 2): the console and the Pocket bridge expire due evidence on access.
- A restored checkpoint takes this setting, the limit on readings, and the ask budget and its window, from the instance it is restored from, not from the archive; the restore result lists what it changed (`settings_carried`) and anything it could not carry (`settings_warnings`). A plain restore (`apply_tombstones=False`, or `nsf archive unpack`) is the archive as it was, settings included: consolidation can be on in that copy although you turned it off since, so run `nsfs config --no-consolidation` on the copy before anything else if you opted out.
- A settings value that was edited by hand into something other than `true` or `false` counts as off, and `nsfs sweep` and `nsfs config` say so until you set it. A limit edited into something that is not a whole number from 0 up is never taken as "no limit": the default of a million applies until you set it.
- They never expire and are not counted in `max_evidence_rows`. Each takes about 95 bytes.
- **There is a limit**: `max_consolidated_rows`, a million readings unless you change it (`nsfs config --max-consolidated-rows N`; `0` = no limit). Every reading counts, a repeated copy of a sample too. From 80 % of the limit `nsfs regulation`, `nsfs birth`, `nsfs config`, the console's Regulation card and any sweep that keeps a reading warn you. **At the limit nothing is deleted**, but a learning event that reaches the end of its lifetime now leaves nothing, as in 0.7.0, so the individual stops keeping what new events teach; a sweep that crosses the limit keeps the oldest of its events and reports the rest as `not_consolidated`. To make room: raise the limit, or forget what is no longer needed (`nsfs forget epoch NAME`, `nsfs forget partner NAME`, `nsfs forget camera NAME`). Lowering the limit below what is kept removes nothing; it only stops further consolidation.
- **What a large epoch costs**: belief computation grows with an epoch's readings. `nsfs regulation` (`largest_epoch`) and the Regulation card name the epoch with the most readings and how long its beliefs take to compute now, so you see it before decisions feel slow.

Forgetting an epoch also clears the free-text note the owner gave it; the epoch's row stays, because tombstones, the deletion log and evidence refer to its name. An epoch that holds only a note is forgotten and logged too. Forgetting a dataset also deletes the lesion and coactivation effects measured on it (`graph_effects_deleted` in the result); effects measured on other datasets stay.

A voice session: `nsfs forget voice SESSION` (or `nsfs voice forget SESSION`); a research task, knowledge item or source: `nsfs forget research|knowledge|source TARGET`.

**Checkpoints are rewritten in place.** After a voice, research, knowledge or source forget, every checkpoint archive that the forget made stale, and that only such forgets made stale, is rewritten without the forgotten rows. The rewrite applies the same replay as a restore and vacuums the copy into a new file. It checks the copy (`PRAGMA integrity_check`), packs it with the archive's own policy, decodes the result again and compares hashes, and only then replaces the file. A journal (`<archive>.rewrite`) and an atomic rename make it crash-safe: a kill at any moment leaves the old archive whole (still `stale`) or the new one whole (`valid`). The next `verify`, `restore`, `rewrite` or `prune` of it, or the next console start, settles the registry. The forget result lists `checkpoints_rewritten` (id, path, old and new size, seconds); `checkpoints_not_rewritten` gives each remaining archive's reason, and `next_steps` appears only for archives still stale. `nsfs memory deletion` holds one `checkpoint` row per rewrite (`status: rewritten`, old and new sha256, the counts removed; no forgotten text). Evidence, epoch, partner, dataset and expiry forgets are not rewritten, because their derived rows are not fully replayed; those archives wait for `rebuild`. `nsfs checkpoint rewrite [--id N]` retries a rewrite, for example after a console forget or a failed one. Measured on the GB10 host with a synthetic instance of 420–432 MiB (transcript text plus about 45 % incompressible retained audio), archived to 235–247 MiB, in two runs; the second ran beside the test suite. A `cold` archive rewrote in 36.0–41.9 s, about as long as creating it (33.1–40.8 s). A `fast` archive rewrote in 3.9–5.4 s. `verify` took 2.8–3.2 s (`cold`) and 1.4–1.6 s (`fast`). The command waits that long; the voice hub rewrites in the background. A rewrite needs free space for one decoded copy and one new archive next to the old one; without it the archive stays stale and the reason is reported.

An erasure is complete only when old copies are gone. After every forget:

1. If the forget result lists `checkpoints_stale` (or `next_steps`): `nsfs checkpoint rebuild --policy cold` (the nightly timer also does this). This writes a clean archive and deletes stale ones.
2. `nsfs checkpoint list` and confirm no `stale` rows remain.
3. Check `nsfs memory deletion` for the entry and for a `failure` field.
4. Pocket data: the person's own phone records expire after 24 hours; unpairing deletes a device's records at once (section 8).

Out of scope for the erasure guarantee, which you must state to the requester: copies already exported or backed up elsewhere, the damaged-file copy you chose to keep, and storage-device remnants. SQLite secure delete is on in Pocket.

## 7. Regulation and paused writes

`nsfs regulation` reports whether writes are allowed and why not.

```sh
nsfs regulation
```

Writes pause when the evidence journal reaches `max_evidence_rows` (default 1,000,000) or free space on the database's filesystem falls below `min_free_bytes` (default 64 MiB). The free-space check always uses the instance's own file, so console writes (simulate, learn, start an epoch) pause exactly like the command line. Reads, `nsfs memory`, `nsfs explain`, forgetting and checkpoint restore keep working. A paused write returns exit 2 with a "writes paused" message and HTTP 507 where applicable.

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

1. Linked tablets from a hand-off are separate pairings in the same family. If they could be affected, erase the whole family with `nsf pocket erase-pairing LABEL --family --db /var/lib/neuroscifi/pocket.sqlite`; `list-pairings` shows which devices share a `family=` tag.
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

**Corrupted public web cache.** `<db stem>.webcache.sqlite` holds only downloaded public pages and validators (ADR-023) and is disposable. A corrupt file is renamed `<name>.corrupt-<unix time>` and an empty cache replaces it the next time the worker, `nsfs web status` or the console opens it; research continues (pages are fetched again) and an `error` event with stage `public_cache` records the quarantine. `nsfs doctor --db PATH` reports a corrupt cache as `public_cache: warn`. Delete the `.corrupt-*` files once you have kept what you need for the incident record.

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
- Turning web access off (`nsfs web config --disable`), adding a domain to `deny_domains` or cancelling a task takes effect in a running task at once while it reads pages: no request is sent afterwards (every page fetch asks before each lookup, each connection and each request) and requests already on the wire are cut within about a quarter of a second. While it searches it takes effect at the next step, within about 2 s. The task stops as `partial` ("Web access was turned off during the run"), ends `cancelled`, or skips the denied hosts and says so ([Search and acquisition](../web-cortex/SEARCH_AND_ACQUISITION.md), "Stops").
- A task that could read nothing because every provider or page failed (network down, all circuits open) ends `failed` with a limitation naming the failures. `nsfs web requeue TASK_ID` searches again once the network is back.
- An identical submission (same objective, mode and origin) while one is queued or running returns that task (`deduplicated: true`). `requeue` of a partial or cancelled task returns the child already queued instead of creating another.
- Curiosity proposals are sized to the daily autonomous allowance left when they are made, and are checked again when you confirm them and when the worker starts them.

### Crashed worker

Each lease records the worker's host digest, pid and process start time. On the same host a dead worker is detected at once: `cancel`, `resume` and `requeue` act immediately and the worker service picks the task up from its checkpoint without waiting for the 600 s lease to expire. Task JSON carries `worker_alive` (`true`, `false`, or `null` when no worker holds it or it runs on another host).

### Forgetting web memory

| Request | Command |
|---|---|
| A research task, its evidence and the knowledge resting only on it (also its pages in the public cache that no other task read) | `nsfs forget research TASK_ID` |
| A knowledge item and every version of it | `nsfs forget knowledge ID` (or `nsfs web forget ID`) |
| A source URL (as requested, original or final) or every source of a domain, also from the public cache | `nsfs forget source URL_OR_DOMAIN` |

Forgetting a knowledge item also forgets its queued or running refresh tasks (their objective is the forgotten statement); the result lists them in `refresh_tasks_forgotten`. Every forget result whose checkpoints became stale carries `next_steps` naming `nsf checkpoint rebuild`.

Each web forget records a payload-free tombstone (`web_forgotten`, an instance-salted digest), marks every checkpoint stale and logs only a digest label for a URL or domain in `deletion_log`. `checkpoint restore` replays those tombstones on the restored copy, so a forgotten task, knowledge version or source never comes back. Then follow the erasure steps of section 6 (`checkpoint rebuild`). Forgetting a partner (`nsfs forget partner NAME`) also replaces the name in research objectives, reports, events, claims and knowledge.

### Memory models (semantic retrieval)

Optional. Without them memory is ranked by its words (the lexical ranking of 0.7.0) and nothing below is needed. They are two small models served by the same digest-pinned vLLM image as the answer model, on loopback only: an embedding model on port 18441 (`neuroscifi-memory-embed`) and a reranker on port 18442 (`neuroscifi-memory-rerank`). What they do and do not do: [MEMORY_INTEGRATION §5.1](../web-cortex/MEMORY_INTEGRATION.md#51-semantic-retrieval-semanticpy-optional).

**Install.** Place the weights in `~/.local/share/neuroscifi/models/qwen3-embedding-0.6b` and `~/.local/share/neuroscifi/models/qwen3-reranker-0.6b` (the script refuses to start a model whose `config.json` is missing). Settings: `deploy/memory/memory.env`; override any line in `~/.config/neuroscifi/memory/memory.env`. The script runs as the user who owns the weights and Docker access, like `deploy/vllm/run.sh`. It generates one API key for both services on first use (`~/.config/neuroscifi/memory/api_key`, 0600), separate from the answer model's key, and refuses a non-loopback bind and an image without a digest.

**Start, and tell the individual.** Two commands; the second is the same `nsf setup` that set up everything else, and it can be run again at any time:

```sh
deploy/memory/run.sh start && deploy/memory/run.sh wait   # both models; `wait` ends when each answered a real request
nsf setup --db PATH                                        # step "memory": done
```

`nsf setup` looks for the key file and sends each model one request with a fixed, meaningless text. When both answer it configures them for the individual and switches memory by meaning on. With the embedding model alone it switches on the ranking without the cut and says what is missing; a later run adds the reranker. Without the key file it contacts nothing and says in one line how to add the models. A memory setting you made by hand (`nsf web memory config`) is never changed: the step then reports "kept as you set it". `--no-memory` skips the step; `--memory-embed-url`, `--memory-rerank-url` and `--memory-key-file` name other places. By hand, the same result:

```sh
nsf web memory config --enable --embed-url http://127.0.0.1:18441/v1 --rerank-url http://127.0.0.1:18442/v1 \
    --key-file ~/.config/neuroscifi/memory/api_key
```

`--key-file` takes an absolute path. A worker running as the `neuroscifi` service user cannot read a key in your home folder; give it a copy it can read, as for the answer model's key ([COGNITIVE_INFERENCE §5](../architecture/COGNITIVE_INFERENCE.md#5-run-configure-recover)).

**As system services.** `sudo bash deploy/install.sh --memory --memory-user USER` installs `neuroscifi-memory-embed.service` and `neuroscifi-memory-rerank.service`, disabled like every other unit (USER is in the docker group and owns the weights and the key; the default is `--llm-user`, then the invoking user). Each runs `deploy/memory/run.sh foreground embed|rerank` under the wrapper sandbox of the speech units, and systemd restarts it on failure. Enable them once the weights are in place: `sudo systemctl enable --now neuroscifi-memory-embed.service neuroscifi-memory-rerank.service`. Do not also start the same service with `run.sh start`: the two would replace each other's container.

**Status.**

```sh
nsf web memory                        # switch, both endpoints with state and reason, the effect in words
nsf web knowledge "a question"        # result.retrieval says which ranking produced the items
nsf resources                         # the containers' memory; policy.idle_unload.services.embed / .rerank
deploy/memory/run.sh status           # container state and health
deploy/memory/run.sh print embed      # the docker command, key redacted; changes nothing
```

In `nsf web memory` a model's `state` is `ready`, `degraded`, `unavailable` or `unauthorised` (each with its reason), or `asleep`: unloaded by the idle policy, not an outage.

**Release the memory while idle (off by default).** The two models hold about 8.7 GiB together (below). The idle policy can release each after a time without use, like the answer model and the speech services ([RESOURCE_SCHEDULING §3(c)](../architecture/RESOURCE_SCHEDULING.md)):

```sh
nsf resources idle --set embed=15m --set rerank=15m     # `off` turns one off again; needs the idle timer (below)
nsf resources unload embed                              # by hand, now; `nsf resources load embed` brings it back
```

A memory lookup never waits for a model. A lookup that finds one unloaded is ranked by its words at once (`retrieval.reason` says "… is unloaded while idle and is starting again", `retrieval.waking` names the models) and starts the reload in the background; a lookup after that is ranked by meaning again. Measured on the reference host: unloading takes 1 to 2 s per model, both answer again 20 to 29 s after the lookup that found them unloaded, and that lookup took 2 to 4 ms (0.15 s as the whole command `nsf web knowledge TOPIC`, which may exit at once: the reload is a process of its own). A reload that would leave less free memory than the critical threshold is refused and recorded, and tried again by a lookup 30 s later at the earliest. The pass that unloads is `nsf resources tick`, run every minute by `neuroscifi-idle.timer` (installed by hand: the header of `deploy/systemd/neuroscifi-idle.service`); it stops and starts an active systemd unit through `systemctl` and a hand-started container through `run.sh`. Only this host's own two services are managed: a memory endpoint you configured elsewhere is never stopped or started.

**Footprint.** Measured per model with both loaded and used (`docs/quality/evidence/accel_decisions_memory_service.json`): 1.9 GiB of device memory (1.1 GiB of weights, the 0.25 GiB key-value cache that vLLM requires for a 2,048-token input, the rest the CUDA context) and 2.6 GiB of process memory in two processes (1.0 GiB the API server, 1.5 GiB the engine, mostly imported libraries). No setting of the pinned image lowers it: a smaller key-value cache is refused at this input length, and limiting the allocator's arenas changed nothing. The cost is two full vLLM processes for two small models; a lighter server is listed under next priorities in `docs/acceleration/ENGINEERING_PLAN.md`. To hold half of it, run the embedding model alone (`deploy/memory/run.sh start embed`): the recall gain stays, the cut of unrelated statements goes.

**Where the console and the worker may not start services** (the units of `deploy/install.sh`: user `neuroscifi`, no Docker access). A lookup or a model call cannot reload a service there. `sudo bash deploy/install.sh --idle --idle-user <the docker account>` installs the idle timer and the wake units (disabled) and creates the request folder `/var/lib/neuroscifi-wake` (owner: that account, group `neuroscifi`, mode 2770); its closing message lists the three steps left: `NSF_DB` in a drop-in for `neuroscifi-idle.service` and `neuroscifi-wake.service`, `NSF_WAKE_DIR=/var/lib/neuroscifi-wake` in `/etc/neuroscifi/neuroscifi.env`, and `systemctl enable --now neuroscifi-idle.timer neuroscifi-wake.path`. The unprivileged process then leaves an empty request file `embed.wake` (or `vllm.wake`, …) and `neuroscifi-wake.service` does the reload as the Docker account. The console and the worker need nothing but that folder: they leave the request when a lookup finds the service unloaded or, where they cannot read the idle policy's files, when a connection to the service is refused (the call's error then ends "its start was asked for (wake request)"). The Docker account decides whether a reload is due. **Not arranged by the installer, and not verified:** the Docker account must read the instance database and write the folder beside it that ends in `.resources` (the idle policy's own files); with the installed state folder (`neuroscifi` alone, 0700) that needs a group or an ACL you grant. Until then the idle policy unloads nothing there.

Check: `systemctl status neuroscifi-wake.path`; `nsf resources wake --dir /var/lib/neuroscifi-wake` by hand prints what it served. A lookup whose request nobody served within 60 s says so in `retrieval.reason`. An entry in the folder that is not a plain file is removed or renamed to `.stuck-…` (delete those at leisure); if `journalctl -u neuroscifi-wake.service` shows the service starting every 30 s with "not a file: stuck", the folder is read-only or sticky: fix its mode (`chmod 2770`), nothing else is needed.

**Who gets the key.** The memory key is sent only to the memory service. `run.sh wait` refuses a port that answers while Docker does not report the container publishing it ("another process holds the port. The key was not sent"), and `nsf setup` and every lookup refuse one of the memory models' ports when the socket that would take the connection belongs to another account (`nsf web memory`: state `refused`). If you see either, find what holds the port: `ss -ltnp 'sport = :18441'`. The answer model's endpoint is checked the same way before every request: a model call that fails with "127.0.0.1:8811 on this computer is held by a process of another account (uid N)" sent nothing; find the holder with `ss -ltnp 'sport = :8811'`. The check looks only at the ports of the services NeuroSciFi deploys itself and only at the socket that would accept the connection, so a model server you run yourself on its own port and account (Ollama, for instance) is used as before, and a listener on another address does not matter. If you run one of *these* services under another account on purpose, set `NSF_LISTENER_CHECK=0` in the environment of the processes that call it. Limits: inside the hardened units (`PrivateUsers=yes`) other accounts cannot be told apart, so the check refuses nobody there; and a process of your own account or of root is never refused (it can read the key file anyway).

**Stop and roll back.**

```sh
deploy/memory/run.sh stop             # both; or: stop embed | stop rerank   (systemd: systemctl disable --now …)
nsf web memory config --disable       # the individual: back to the lexical ranking
```

Stopping the containers does not need a configuration change: the next query falls back to the lexical ranking, `retrieval.reason` says why, and the models are left alone for 30 s before the next lookup tries them again.

### Reading several pages at once (read-ahead, on by default)

```sh
nsf web config                        # shows read_ahead (default 4)
nsf web config --set read_ahead=1     # one page at a time for every task, as 0.7.0
nsf web config --set read_ahead=4     # back to the default
```

A task's report, events and charged usage are the same at any value; only the time spent waiting for sites changes
(about half at 4). One exception: when a site answers 429 to a page fetched ahead, it is left alone from that moment,
and a source an earlier page would have redirected to there is not read; the report says so. Curiosity, refresh and
MCP tasks always read one page at a time, so the daily allowances are exact. After a cancel, a denial or switching
web access off no request is sent and the ones on the wire are cut. Pages fetched ahead share the task's budget, and
what a task sent ahead and did not use is in its usage (`prefetch_unused_requests`, `prefetch_unused_bytes`).
Details: [Search and acquisition](../web-cortex/SEARCH_AND_ACQUISITION.md), "Stops" and "Read-ahead".

### Rolling back the inference and memory defaults

Each default below can be reverted on its own. None needs a database change beyond the setting itself.

| Default | Roll back | Check |
|---|---|---|
| vLLM serves requests by `priority` (`NSF_VLLM_SCHEDULING=priority`) | Put `NSF_VLLM_SCHEDULING=fcfs` in `~/.config/neuroscifi/inference/vllm.env`, then `deploy/vllm/run.sh start` (it replaces the running container; answers fall back to the extractive summary until `deploy/vllm/run.sh wait` returns). Where `neuroscifi-vllm.service` runs the model, use `sudo systemctl restart neuroscifi-vllm.service` instead | `deploy/vllm/run.sh print` shows `--scheduling-policy fcfs` |
| vLLM processes up to 4096 tokens per step (`NSF_VLLM_BATCHED_TOKENS=4096`) | Put `NSF_VLLM_BATCHED_TOKENS=` (empty: vLLM's default) in the same file, then `deploy/vllm/run.sh start` | `deploy/vllm/run.sh print` no longer shows `--max-num-batched-tokens` |
| Background research is sent with `priority` 10 (set by `nsf setup` for a vLLM endpoint) | `nsf llm config --set background_priority=0` | `nsf llm status`: `reasoning.background_priority` is null |
| The answer model confirms a memory answer before it is given (`memory_check`) | `nsf web config --no-memory-check` | `nsf web config`: `config.memory_check` is false |
| The self-critique reads only sentences that say more than a word-for-word restatement of their cited claims, and makes no call when there is none | `nsf llm config --critique-restatements` (every accepted sentence is judged again, as in 0.7.0) | `nsf llm status`: `reasoning.critique_restatements` is true |
| Semantic memory retrieval, once the owner or `nsf setup` enabled it | `nsf web memory config --disable` | `nsf web memory`: `effect` starts with "off" |
| The memory model containers, once started | `deploy/memory/run.sh stop` | `deploy/memory/run.sh status`: both "not running" |
| The memory step of `nsf setup` | `nsf setup --no-memory` skips it; what it set is undone with `nsf web memory config --disable` | `nsf web memory`: `effect` starts with "off"; a later `nsf setup` reports "kept as you set it" |
| Idle unloading of the memory models, once the owner set a timeout | `nsf resources idle --set embed=off --set rerank=off` | `nsf resources idle`: both `timeout_s` null |
| The memory units, once installed | `sudo systemctl disable --now neuroscifi-memory-embed.service neuroscifi-memory-rerank.service` | `systemctl is-active` says inactive |

Measurements behind the two vLLM settings and what each change costs: [COGNITIVE_INFERENCE §4 and §7](../architecture/COGNITIVE_INFERENCE.md#4-deployment-on-the-dgx-spark-measured-2026-11-21).

| Symptom | Check | Recovery |
|---|---|---|
| `nsf web knowledge TOPIC` shows `retrieval: {"mode": "lexical", "reason": "…"}` | The reason. "no embedding endpoint is set": the switch is on without an endpoint. "semantic retrieval failed (unavailable)": a container is down or still starting (`deploy/memory/run.sh status`). "(unauthorised)": the key file does not match the running containers (`nsf web memory` shows `key_present`). "(timeout)": the call took longer than the `timeout` setting. "semantic retrieval unusable": the key file is readable by every user or otherwise unusable | Set the endpoint (`nsf web memory config --embed-url URL`), start the containers (`deploy/memory/run.sh start && deploy/memory/run.sh wait`), point `--key-file` at `~/.config/neuroscifi/memory/api_key`, or raise the timeout (`nsf web memory config --set timeout=5`). Until then answers use the lexical ranking; nothing is lost |
| `retrieval` is `{"mode": "lexical"}` without a reason | Semantic retrieval is off for this individual (`nsf web memory`: `enabled` false) | `nsf web memory config --enable` |
| `retrieval` is lexical with `waking: ["embed", "rerank"]` | `nsf resources`: `policy.idle_unload.services.embed.state` is `unloaded` by the `idle policy`; the last events show the unload and the `load` that is running | Nothing to do: the idle policy released the models and this lookup started them again; a lookup about half a minute later is ranked by meaning. To keep them loaded: `nsf resources idle --set embed=off --set rerank=off` |
| `retrieval.reason` says "not enough memory to load memory search (…)" or "… did not start" | `nsf resources`: MemAvailable, and the last `load` event of `embed` or `rerank` (`refused: true`, or `error` with the script's output); `docker logs neuroscifi-memory-embed` | Free memory, or fix what the log names; the next lookup after 30 s tries again by itself, or `nsf resources load embed` now |
| A question that memory used to answer now goes to the web | The task's `decision` event: `memory_check.refused` lists the statements the answer model did not accept | Expected when the statement is about another property or scope. To restore the earlier behaviour: `nsf web config --no-memory-check` |
| A report's `reasoning.rejected` holds "ends in its own attribution followed by more words ('…'): replacing the attribution would drop them" | The sentence and the words named. The model ended a sentence with "according to <source> …" and more words after the source; 0.7.0 showed such a sentence without those words (an exception such as "except on Sundays" could vanish) | Expected: the sentence is refused, the other sentences and the extractive answer stand. Nothing to configure |
| A research answer shows no "self-check flagged" mark and its report has `reasoning.critique.status: "skipped"` | The `reason`. "nothing to judge: all N sentence(s) restate their cited claims word for word…": every sentence is a cited claim's own words with the attribution the code chose, so no critique call was made (`restatements: N`). "spoken answer…": `critique_voice` is off. "time_budget…": too little of the task's time was left | Expected. To have the critic read restatements too: `nsf llm config --critique-restatements` |
| `deploy/vllm/run.sh wait` ends with "container is restarting" or "container restarted N time(s) while starting" | `docker logs neuroscifi-vllm` | The container is crash-looping, usually for lack of memory; `wait` no longer sits out its limit. Free memory or lower `NSF_VLLM_GPU_UTIL`, then start again |
| A model call or `nsf resources load vllm` ends with "not enough memory to load the local model: …" | `nsf resources`: MemAvailable and the last `load` event with `refused: true` | The idle policy refuses a reload that would leave less than the critical threshold free. Free memory (stop another workload), then repeat the call or the command |

### MCP

`nsf web mcp` allows at most `mcp_research_per_hour` (default 6) `web_research` calls per hour per server process, and MCP-origin tasks share the daily allowance `daily_mcp` (tasks, requests, bytes). Results carry the report's context pack (bounded by the task's `max_context_tokens`); `full_report: true` returns the whole report.

## 13. Graph scoring rules: which rule an individual learns under

A graph candidate is learned under the newest scoring rule of its individual and judged by that rule for good (`nsf graph rules list --db DB`; the last entry is the one in use). Rules are immutable: they are added, never edited, removed or reordered (ADR-045).

| Individual | Rules it has | What `nsf graph learn` packs |
|---|---|---|
| Created by this version or later | `gate-v1`, then `best-learner-v1` (both `created_by: birth`) | The smallest file within 2 % of the better learner: a cut of the dense fit, or the sparse learner's graph. Same thresholds and byte budget as gate-v1 |
| Created earlier | `gate-v1`, plus whatever its owner added | A cut of the dense fit, as before. Nothing changes until its owner adds a rule |

**Adopt the rule on an older individual** (no file needed; services may stay up, the command takes one short write):

```
nsf graph rules add --builtin best-learner-v1 --owner-approved --db DB
```

Graphs learned afterwards use it. Graphs already learned, active or archived keep their rule and their bytes.

**Go back.** `gate-v1` is registered already and the newest rule is the one in use, so going back is a new version with gate-v1's content:

```
nsf graph rules add --builtin gate-v1 --as gate-v1-again --owner-approved --db DB
```

The same form returns to best-learner later (`--builtin best-learner-v1 --as best-learner-v1-again`). A candidate already learned under the other rule is not relearned; learn it again if you want it judged under the new one.

**What every individual gets, whatever its rules.** The promotion gate rejects a snapshot that predicts no better than a graph with no edges (check `beats_no_graph`; the console shows it as "Better than a graph with no edges", and the revision's detail has the line "Against no graph"). It applies to candidates learned by this version or later; a candidate learned earlier is judged by its five checks as before.

| Symptom | Check | Recovery |
|---|---|---|
| `nsf graph promote` rejects with `failed: ["beats_no_graph"]` | `nsf graph show REV`: `metrics.validation_mse` against `metrics.no_graph_validation_mse`, and `metrics.sparse.validation_mse` | The dense learner stops fitting with many populations (about 100 and up). If the sparse learner's error is low, adopt `best-learner-v1` (above) and learn again. If both are near the no-graph error the data holds nothing to learn, or the stream is too short |
| A candidate with 512 or more populations is rejected for `byte_budget` only | `byte_size` against 4096 | Expected under both built-in rules. To allow it, add your own rule: copy `best-learner-v1` from `nsf graph rules list`, give it a new `version` and a larger `byte_budget`, and `nsf graph rules add FILE.json --owner-approved` |
| `Rule gate-v1 is already registered and a newer rule is in use` | — | Use `--as NEW-NAME` as above |
| The software is to be downgraded below this version | `nsf graph rules list`: is the last rule one with `pack_reference`? | Go back to gate-v1 first (above). Older software ignores the two new parts of a rule and would pack as gate-v1 under best-learner-v1's name |
