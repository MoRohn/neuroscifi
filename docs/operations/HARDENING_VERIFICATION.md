# Hardening and encrypted-storage verification

What was executed on the reference host (DGX Spark GB10, systemd 262, kernel 7.3, no root) on 2026-11-22 UTC, what it showed, and the checks that only the owner can run, because they need root or the system manager.

Related: [RUNBOOK](RUNBOOK.md) (install and operate), [ENVIRONMENT](ENVIRONMENT.md) (settings, TLS paths), [TLS](TLS.md) (trusted chain for Pocket).

## 1. Summary

| Claim | Status | Evidence |
|---|---|---|
| The console, the Web Cortex worker, the checkpoint jobs and Pocket start and work under their units' sandbox | **Verified** as transient user units | Section 4 |
| The docker wrapper units (vLLM, voice, SearXNG) work under the new sandbox | **Verified** with non-disruptive commands | Section 4.4 |
| Capabilities, seccomp, W^X, namespaces, read-only system, private /tmp and devices, memory and task limits are enforced | **Verified** from inside the units (`hardening_probe.py`) | Section 3 |
| `IPAddressDeny=`/`IPAddressAllow=`, `ProtectProc=`/`ProcSubset=`, `IOWeight=`, `User=` and `DynamicUser=` | **Owner-side** (only the system manager enforces them) | Section 5 |
| The instance, checkpoints, model cache, inference keys and docker data are on LUKS2; swap is zram; /tmp is tmpfs | **Verified** on this host | Section 6 |

## 2. Exposure scores (`systemd-analyze security --offline=yes`)

| Unit | Before | After | Change |
|---|---|---|---|
| neuroscifi-checkpoint | 0.8 | **0.4** | `MemoryMax=4G`, `TasksMax=32`, `PrivateUsers=yes`, `RemoveIPC=yes`, `IPAddressDeny=any` |
| neuroscifi-console | 1.1 | 1.1 | unchanged |
| neuroscifi-pocket | 1.3 | 1.3 | unchanged |
| neuroscifi-webcortex | 1.3 | 1.3 | unchanged |
| neuroscifi-webcortex-tick | 1.3 | 1.3 | unchanged |
| neuroscifi-websearch | 1.3 | 1.3 | unchanged (`MemoryDenyWriteExecute=no`: V8's JIT) |
| neuroscifi-vllm | 9.0 | **1.6** | the wrapper is now sandboxed (below) |
| neuroscifi-voice-asr | 9.0 | **1.6** | same |
| neuroscifi-voice-tts | 9.0 | **1.6** | same |
| neuroscifi-searxng | 9.2 | **1.9** | same |

The four docker wrapper units ran the docker CLI and `run.sh` with no sandbox at all. The container keeps its own hardening (read-only root filesystem, no capabilities, loopback port). The wrapper now has `NoNewPrivileges`, an empty `CapabilityBoundingSet`, `ProtectSystem=full`, `PrivateTmp`, `PrivateDevices`, the `ProtectKernel*`/`ProtectControlGroups`/`ProtectClock`/`ProtectHostname` set, `ProtectProc=invisible`, `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`, `IPAddressDeny=any` with `IPAddressAllow=localhost`, `RestrictNamespaces`, `RestrictRealtime`, `RestrictSUIDSGID`, `LockPersonality`, `MemoryDenyWriteExecute=yes`, `SystemCallArchitectures=native` and `SystemCallFilter=@system-service`.

Two things were deliberately left out, and the unit says why:

- `ProtectSystem=strict` and `ProtectHome`. The wrapper writes its key and cache under the service user's home, and the template cannot know that path.
- `PrivateUsers`. The wrapper must keep its docker group to reach `/run/docker.sock`. The docker group is root-equivalent, so keep it small, as the units already say.

`MemoryDenyWriteExecute=yes` stays off only for the two JIT runtimes: Node (`neuroscifi-websearch`) and Chromium (browser rendering in `neuroscifi-webcortex`, through a drop-in only when the owner turns rendering on).

## 3. What the kernel enforces: the probe

`deploy/systemd/hardening_probe.py` runs inside a unit and reports what is really enforced: capability sets, `NoNewPrivs`, seccomp mode, writes to `/usr`, `/etc` and the state folder, visible devices and homes, a W^X mapping, `unshare(CLONE_NEWUSER)`, `clock_settime`, `sched_setscheduler`, socket families, TCP connections and the cgroup limits.

`scripts/hardened_run.py UNIT --name NAME [--user] ... -- COMMAND` passes the `[Service]` sandbox and resource directives of `deploy/systemd/UNIT.service` to `systemd-run` as `-p` properties. In user mode only the paths change:

- `User=`/`Group=` are dropped.
- `ProtectHome=yes` becomes `ProtectHome=tmpfs`, and the virtualenv and its interpreter are bind-mounted read-only into the empty `/home`.
- The scratch state folder is bound read-write.

Results under the user manager (systemd 262), the same for console, pocket, webcortex and checkpoint unless noted:

| Directive | Observed inside the unit |
|---|---|
| `CapabilityBoundingSet=`, `AmbientCapabilities=` | `CapBnd`, `CapEff`, `CapAmb` all `0000000000000000` |
| `NoNewPrivileges=yes` | `NoNewPrivs: 1` |
| `SystemCallFilter=@system-service`, `SystemCallArchitectures=native` | `Seccomp: 2` (22 filters); `clock_settime` → **killed (SIGSYS)** |
| `MemoryDenyWriteExecute=yes` | `mmap(PROT_WRITE|PROT_EXEC)` → refused (EACCES) |
| `RestrictNamespaces=yes` | `unshare(CLONE_NEWUSER)` → refused (EPERM) |
| `RestrictRealtime=yes` | `sched_setscheduler(SCHED_FIFO)` → refused (EPERM) |
| `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX` | `AF_NETLINK`, `AF_PACKET` → refused (EAFNOSUPPORT) |
| checkpoint: `PrivateNetwork=yes`, `RestrictAddressFamilies=AF_UNIX` | every TCP connection, loopback included → refused |
| `ProtectSystem=strict`, `ReadWritePaths=` | `/usr`, `/etc` → EROFS; the state folder writable |
| `ProtectKernelTunables=yes` | write to `/proc/sys/kernel/domainname` → EROFS |
| `PrivateTmp=yes` | `/tmp` holds only the bound state folder (1 entry) |
| `PrivateDevices=yes`, `ProtectKernelLogs=yes` | `/dev/nvme0n1`, `/dev/kmsg` absent |
| `ProtectHome=` (tmpfs in user mode) | `/home` shows only the read-only code binds; `/root` empty |
| `MemoryMax=`, `TasksMax=` | `memory.max` 2147483648 (console, webcortex) / 1073741824 (pocket); `pids.max` 128 |
| `CPUWeight=` | webcortex `cpu.weight` 50, checkpoint 20 |
| **`IPAddressDeny=`/`IPAddressAllow=`** | **not enforced**: 1.1.1.1:443 and a LAN router address reachable from the console and webcortex sandboxes. The journal says "unit configures an IP firewall, but not running as root" |
| **`ProtectProc=invisible`, `ProcSubset=pid`** | **not enforced**: pid 1 and about 700 other processes visible |
| **`IOWeight=`, `IOSchedulingClass=`** | the io controller is not delegated to the user manager (`io.weight` absent) |

## 4. Services really run under the sandbox

Each service ran as a transient user unit (`nsf-test-*`) with its unit's sandbox, against scratch instances in `lane_deploy_live/`, from the source tree at 94ccb09 plus this lane. All transient units were stopped and collected afterwards. The long-running soak ([RESEARCH_POLICY §9.2](../autonomy/RESEARCH_POLICY.md#92-wall-clock-the-nsf-soak-session-started-2026-11-22-0123-utc-still-running)) also runs the worker under this sandbox.

### 4.1 Console (`neuroscifi-console` sandbox)

- It started and wrote its URL file (0600). `GET /api/inventory` and `GET /api/web/status` returned 200, and the same call without the token returned 403.
- `POST /api/web/tasks` queued "What is the latest stable version of SQLite?". `POST /api/checkpoints` created checkpoint 1 (cold, 25 255 bytes, `valid`).
- `systemctl --user restart` rotated the session token: the old token got 403 and the new one 200.

### 4.2 Web Cortex worker (`neuroscifi-webcortex` sandbox)

- The worker claimed the console's task and ran it against the live SearXNG at 127.0.0.1:8888, ending `partial`: 3 queries, 2 pages read from Wikipedia, and the report says the source is single and not fresh.
- SIGTERM (`systemctl --user stop`) ended it cleanly with `Result=success`. Peak memory was 63 MB.

### 4.3 Checkpoint jobs (`neuroscifi-checkpoint` sandbox, no network)

`nsf sweep`, `nsf checkpoint rebuild --policy cold` (created checkpoint 2), `nsf checkpoint list` and `nsf checkpoint verify 2` (`ok: true`) all exited 0, after the tightening in section 2.

### 4.4 Pocket (`neuroscifi-pocket` sandbox, HTTPS)

- Pocket served the certificate issued by `nsf pocket tls init` ([TLS](TLS.md)).
- Pairing over the trusted chain: `POST /api/pair` with the code from the 0600 invitation file returned 201. The invitation file was deleted and `list-pairings` showed the device.
- It still served after a restart, and `openssl s_client` verified the chain again.

### 4.5 Docker wrappers (new sandbox; the shared containers were not restarted)

| Wrapper | Command | Result |
|---|---|---|
| neuroscifi-vllm | `deploy/vllm/run.sh status` | container healthy; `/v1/models` read with the key file |
| neuroscifi-voice-asr/tts | `deploy/voice/run.sh status` | both containers healthy |
| neuroscifi-searxng | `nsf web searxng start` (already running: no change), `nsf web searxng health` | `already running`, engine health listed |
| any | `docker run --rm --network none --entrypoint cat <pinned searxng image> /etc/os-release` | ran and removed a fresh container |

## 5. Owner-side checks (system manager, root)

The user manager cannot attach BPF programs or mount `/proc` with `hidepid`, and it does not switch users. Run these once on the deployed host after `sudo bash deploy/install.sh`. The probe ships in the source tree.

```sh
# 1. Scores of the installed units (online, as the system sees them)
for u in checkpoint console pocket webcortex webcortex-tick websearch vllm voice-asr voice-tts searxng; do
  systemd-analyze security neuroscifi-$u.service | tail -1; done

# 2. The IP firewall is attached (BPF): expect the cgroup to carry ingress/egress filters
sudo systemctl start neuroscifi-console.service
sudo bpftool cgroup show /sys/fs/cgroup/system.slice/neuroscifi-console.service      # cgroup_skb ingress + egress
systemctl show neuroscifi-console.service -p IPAddressDeny -p IPAddressAllow -p IPEgressBytes

# 3. The firewall works: the probe under the console's sandbox must reach only loopback. The probe must sit where
#    the sandbox can read it (ProtectHome=yes hides /home, PrivateTmp hides /tmp):
sudo install -m 0644 deploy/systemd/hardening_probe.py /opt/neuroscifi/hardening_probe.py
sudo python3 scripts/hardened_run.py neuroscifi-console --name nsf-probe-console --wait -p User=neuroscifi \
  -- /usr/bin/python3 /opt/neuroscifi/hardening_probe.py \
  --allow 127.0.0.1:8766 --deny 1.1.1.1:443 --deny 192.168.1.1:80
#    expect connect 1.1.1.1:443 and 192.168.1.1:80 "refused (EPERM)"; proc_pids_visible small and pid1_visible false
#    (ProtectProc=invisible + ProcSubset=pid); status Uid = the neuroscifi uid

# 4. The worker reaches public addresses but not the LAN or the metadata address
sudo python3 scripts/hardened_run.py neuroscifi-webcortex --name nsf-probe-webcortex --wait -p User=neuroscifi \
  -- /usr/bin/python3 /opt/neuroscifi/hardening_probe.py \
  --allow 1.1.1.1:443 --deny 192.168.1.1:80 --deny 169.254.169.254:80 --deny 10.0.0.1:80

# 5. DynamicUser websearch: a throwaway uid, no access to the individual's state
systemctl show neuroscifi-websearch.service -p DynamicUser -p MainPID; ps -o user=,pid= -p "$(systemctl show -p MainPID --value neuroscifi-websearch.service)"
```

Replace `192.168.1.1` with your router's address. `hardened_run.py` without `--user` uses the system manager and applies the unit's sandbox with `ProtectHome=yes`. `User=` is not taken from the unit; `-p User=neuroscifi` adds it. The probe changes nothing: it writes and deletes one file only under `--rw` paths. Remove `/opt/neuroscifi/hardening_probe.py` afterwards.

## 6. Encrypted storage

The host root is LUKS2: `nvme0n1p2` (crypto_LUKS) → `/dev/mapper/root` (dm uuid `CRYPT-LUKS2-…`) → btrfs with subvolumes `/@` and `/@home`.

| What | Path | Backing |
|---|---|---|
| Deployed instance, checkpoints, Pocket DB (units) | `/var/lib/neuroscifi` (`/var/lib`) | `/dev/mapper/root[/@]` btrfs: LUKS2 |
| Model weights | `~/.local/share/neuroscifi/models` | `/dev/mapper/root[/@home]`: LUKS2 |
| vLLM and voice caches | `~/.cache/neuroscifi/{vllm,voice}` | `/dev/mapper/root[/@home]`: LUKS2 |
| Inference key, SearXNG config and secret | `~/.config/neuroscifi` | `/dev/mapper/root[/@home]`: LUKS2 |
| Docker data root and its 22 volumes | `/var/lib/docker` (overlayfs) | `/dev/mapper/root[/@]`: LUKS2; no separate mounts below it |
| NeuroSciFi containers | bind mounts only (models read-only, caches, SearXNG config) and `--tmpfs` | LUKS2 and RAM |
| Swap | `/dev/zram0` (121.9 GiB, compressed RAM) | RAM: no page reaches a disk |
| `/tmp` | tmpfs | RAM |

Evidence commands: `lsblk -o NAME,TYPE,FSTYPE,MOUNTPOINTS`, `findmnt -T PATH`, `docker info -f '{{.DockerRootDir}}'`, `docker volume inspect -f '{{.Mountpoint}}'`, `docker inspect -f '{{range .Mounts}}…{{end}}'`, `/proc/swaps`.

A full instance cycle ran on the encrypted btrfs (a scratch folder under `~/.cache`, removed afterwards):

1. `nsf init`.
2. A hypothesis write.
3. A live research task through SearXNG (3 queries, 2 pages).
4. `nsf checkpoint create` (files 0600, folder 0700), then `nsf checkpoint verify` (ok).
5. `nsf checkpoint restore` into a new file.

`nsf doctor` on both files reported `encryption ok` (dm-crypt LUKS2) and `integrity ok`.

`nsf doctor --db PATH [--path PATH ...]` now reports this itself:

- `encryption` is `ok` only when the database's filesystem is backed by dm-crypt. The verdict comes from the kernel (`/sys/dev/block/MAJ:MIN/dm/uuid` starting `CRYPT-`, or a device below it, so LVM on LUKS counts). `lsblk -s` only adds the device chain to the detail.
- tmpfs is reported as "memory-backed" (warn: nothing on disk, lost at reboot).
- Anything without kernel evidence (fscrypt, ZFS native encryption, network filesystems, missing `/proc`) is "unknown" (warn), never ok.
- `swap` is ok only when every swap area is zram or on dm-crypt.
- `--path` checks other places, such as `~/.local/share/neuroscifi`, `/var/lib/docker` and an archive folder.

Owner-side: the units' `RequiresMountsFor=` only waits for a mount. On a host whose state volume is a separate LUKS volume (RUNBOOK §1), uncomment `ConditionPathIsMountPoint=/var/lib/neuroscifi`. Then run `nsf doctor --db /var/lib/neuroscifi/instance.sqlite --path /var/lib/docker --path /home/USER/.local/share/neuroscifi` as the service user, and confirm `cryptsetup status` for the volume.
