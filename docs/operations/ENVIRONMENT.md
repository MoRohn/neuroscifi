# NeuroSciFi environment and configuration

Every input that changes how the services and commands behave. Procedures are in [RUNBOOK.md](RUNBOOK.md). Items marked *(available from 0.5.0)* belong to tools still being built and are verified by the integrator.

## 1. Command-line flags

Copied from `--help`.

| Command | Flags |
|---|---|
| `nsf pocket serve` | `--db DB` (default `mobile-private.sqlite`), `--bind BIND` (default `127.0.0.1`), `--port PORT` (default 8765), `--cert CERT`, `--key KEY`, `--allowed-host NAME` (repeatable), `--invitation-file PATH` |
| `nsf pocket list-pairings` | `--db DB`, `--instance-db PATH` (default `$NSF_DB`) |
| `nsf pocket erase-pairing LABEL` | `--db DB`, `--family` (also erase every device of the label's pairing family: linked phones and tablets) |
| `nsf init` | `--db DB`, `--action-semantics JSON`, `--founding-graph FILE`, `--ttl-days N` (default 7), `--ask-budget N` (default 3), `--ask-window-days D` (default 7; 0 = the whole epoch), `--consolidation` / `--no-consolidation` (default on, ADR-044), `--note TEXT` |
| `nsf config` | `--db DB`; without other flags it prints the instance settings. `--ask-budget N`, `--ask-window-days D` change the ask budget and its window; `--consolidation` / `--no-consolidation` says whether an expired learning event leaves a consolidated reading (the other settings are fixed at `nsf init`) |
| `nsf checkpoint create` / `rebuild` | `--db DB`, `--dir DIR` (default: `checkpoints` next to the database), `--policy {cold,experimental,fast}` (default `cold`) |
| `nsf checkpoint list` / `verify ID` / `restore ID DEST` | `--db DB` |
| `nsf memory KIND` | `--db DB`, `--id`, `--epoch`, `--context`, `--channel`, `--root`, `--since`, `--until`, `--limit`. Kinds include `evidence` (episodes in the journal) and `consolidated` (what expired learning events left, ADR-044) |
| Other instance commands (`birth`, `regulation`, `sweep`, `memory`, `forget`, `promote`, `observe`, `status`, `decide`, `explain`, `pending`, `resolve`, `import-legacy`, `epoch start`, `graph ...`) | `--db DB` |
| `nsf console` *(available from 0.5.0)* | `--db PATH`, `--port 8766`, `--url-file PATH` |
| `nsf acceptance` *(available from 0.5.0)* | `--all` |
| `nsf doctor` *(available from 0.5.0)* | `--db PATH` |

`nsf pocket serve` rules:

- The default bind is loopback (`127.0.0.1` or `localhost`). Any other `--bind` requires both `--cert` and `--key`. Giving only one of them, or a non-loopback bind without them, exits with "LAN access requires both --cert and --key for HTTPS."
- `--invitation-file PATH`: the one-use pairing code is written to PATH instead of stdout. The file is created with mode 0600 (symbolic links are not followed) and holds a lock for as long as the bridge runs. The server exits with an error if PATH already exists, except for a **stale invitation** left by an earlier bridge, which it replaces (written to a temporary file, then renamed). A file is stale only when it is a regular file owned by the bridge's user with mode 0600, not a symbolic link, holds exactly one code, and either no running bridge holds its lock or it is older than five minutes. Anything else is never overwritten and the start fails with an error. The file is removed when the invitation is used, after five minutes, and at shutdown by SIGTERM or Ctrl-C. Only "Invitation written to PATH" is printed. A hard kill (SIGKILL, power loss) can leave the file behind; the next start replaces it under the rule above, and the service's runtime directory is on tmpfs and is cleared at stop and reboot. Without the flag, the code is printed on stdout as before.
- `--allowed-host NAME` (repeatable, optional): when given, the request's `Host` header must equal NAME or `NAME:PORT` (case-insensitive), otherwise Pocket answers 421 and does nothing else. Use it on a LAN bind to name the host phones really use, for example `--allowed-host spark.local --allowed-host 192.168.1.20`; it narrows what a DNS-rebinding page could reach. Without the flag any Host is accepted, as before. With the systemd unit, put it in `NSF_POCKET_TLS_ARGS` (for example `--cert ... --key ... --allowed-host spark.local`).
- With TLS on, every response carries `Strict-Transport-Security: max-age=31536000`. Plain HTTP responses do not.
- Connection limits (also on plain HTTP): the TLS handshake runs in the connection's own worker thread, and each connection must deliver its whole request (handshake, head and body) within 10 seconds of being accepted, or it is closed. At most 32 connections are served at once and at most 8 from one address. When a limit is reached, a new connection closes the oldest one still waiting for its request (a pre-handshake one first; one from its own address when that address is at its cap), and is itself dropped only if every candidate is already being answered. An idle or hostile connection therefore cannot freeze the bridge or lock phones out.
- Request bodies are at most 512 bytes (`research_submit`: 1024, so a 300-character question in a non-Latin script fits). Responses, including error pages for malformed requests, carry the same security headers, and the `Server` header is `NeuroSciFi-Pocket` (no Python or library version).
- A port that is already in use ends the start with one line on stderr, `nsf: cannot listen on ADDRESS:PORT: Address already in use`, and exit status 2.
- Per pairing family: at most 6 devices, and at most 10 research submissions per hour. The submission count lives in the Pocket database (family and time only), so restarting the bridge does not reset it.
- `nsf pocket list-pairings` prints one line per device: `LABEL  family=TAG  sponsor=invitation|LABEL  created=UTC time|unknown`. `TAG` is the family tag the console and `nsf web node list` show (8 hex digits of the instance-salted family digest); it needs `--instance-db` or `$NSF_DB`, else it is `-`.
- Connection errors print one fixed line (`nsf-pocket: connection error`) to stderr with no client address and no traceback. A database error while handling a request answers 503 with `{}`.
- The process sets umask 0077 for every file it creates.
- `nsf console --url-file PATH`: the URL with its session token is written atomically to PATH (mode 0600) and removed at shutdown (Ctrl-C or SIGTERM). If PATH exists and is a regular file owned by the same user, not a symbolic link, and starts with `http://127.0.0.1:`, it is a leftover of an earlier console (for example after a crash or SIGKILL) and is replaced; any other existing PATH makes the console exit 2 and leave the file alone.
- Console `GET` requests do not take the database write lock: expired evidence is swept before every `POST`, and on `GET` at most once a minute with a 200 ms lock wait that is skipped when the database is busy. A request that finds the database locked for more than 2 seconds answers 503 `{"error":"instance busy; retry"}` with `Retry-After: 1`.

## 2. Environment variables

| Variable | Read by | Meaning |
|---|---|---|
| `NSF_DB` | every instance command | Instance database path when `--db` is absent. Default `./neuroscifi.sqlite` in the current folder. The `--db` flag wins over it. |
| `NSF_CHROMIUM` | `scripts/e2e.sh` tests only | Path of a system Chromium for the browser tests. |
| `PYTHON` | `scripts/check.sh`, `scripts/e2e.sh` | Interpreter for the checks (default `.venv/bin/python`). |

Pocket does not read `NSF_DB`; it has its own `--db`. The two databases are separate on purpose (ADR-005).

## 3. The systemd environment file

`/etc/neuroscifi/neuroscifi.env`, owner `root`, group `neuroscifi`, mode 0640. Copied from `deploy/neuroscifi.env.example` by `deploy/install.sh` only if absent. Format: one `KEY=VALUE` per line, no shell expansion, no quotes needed. A missing file is not an error (`EnvironmentFile=-`); the defaults below are also set in the units. After editing, `sudo systemctl restart` the affected unit. Changes to a running unit do not apply until then.

| Key | Default | Used by | Notes |
|---|---|---|---|
| `NSF_DB` | `/var/lib/neuroscifi/instance.sqlite` | console, checkpoint job, `nsfs` | Must be on the encrypted volume. |
| `NSF_POCKET_DB` | `/var/lib/neuroscifi/pocket.sqlite` | Pocket | Excluded from backups. |
| `NSF_POCKET_BIND` | `127.0.0.1` | Pocket | Loopback needs no TLS. |
| `NSF_POCKET_PORT` | `8765` | Pocket | Use a port of 1024 or higher: the unit has no capability to bind lower ports. |
| `NSF_POCKET_TLS_ARGS` | empty | Pocket | `--cert PATH --key PATH`, split on spaces, so paths cannot contain spaces. |
| `NSF_CONSOLE_PORT` | `8766` | console | Loopback only regardless of any setting. |

Keys not in this table are ignored by the units. Do not put secrets in this file; it holds none by design.

## 4. TLS certificate and key

Required for any non-loopback bind (OPS-02). Pocket loads them with `ssl.SSLContext.load_cert_chain` and accepts TLS 1.2 or newer.

- The certificate must be valid for the exact hostname the phone types, include the full chain, and be trusted by the phone (a public CA, or a private CA whose root is installed on the phone). A self-signed certificate that the phone does not trust fails in the phone's browser before Pocket is reached.
- `nsf pocket tls init --out /etc/neuroscifi/tls --name HOST ...` issues such a chain from a private CA and `nsf pocket tls export-ca` exports the CA for the phones ([TLS.md](TLS.md), with the Android and iOS steps and the Tailscale / Let's Encrypt alternatives).
- The key must match the certificate and be unencrypted (no passphrase prompt is possible under systemd).
- Location: `/etc/neuroscifi/tls/fullchain.pem` and `/etc/neuroscifi/tls/privkey.pem`. The folder is `root:neuroscifi` 0750 and the files `root:neuroscifi` 0640, so the service reads them through its group and no other account can. The key is not copied into the state directory.
- Renewal: replace both files, then `sudo systemctl restart neuroscifi-pocket.service`. Pocket does not reload certificates while running.
- Pocket needs the certificate readable at start. A wrong path or permission makes `serve` exit; the journal shows the error.

Example LAN lines in the env file:

```
NSF_POCKET_BIND=192.168.1.20
NSF_POCKET_TLS_ARGS=--cert /etc/neuroscifi/tls/fullchain.pem --key /etc/neuroscifi/tls/privkey.pem
```

Use a private LAN or a VPN between the phone and the host. Do not forward the port to the internet. Network, certificates and encryption are not configured by NeuroSciFi.

## 5. Ports

| Port | Service | Bind | Notes |
|---|---|---|---|
| 8765/tcp | Pocket | `NSF_POCKET_BIND` (default loopback) | The only network-facing service. Allow it on the private interface only. |
| 8766/tcp | Console *(available from 0.5.0)* | 127.0.0.1 only | Reached through an SSH tunnel. The unit also sets `IPAddressDeny=any` and `IPAddressAllow=localhost`. |

## 6. Filesystem layout and permissions

| Path | Owner and mode | Purpose |
|---|---|---|
| `/opt/neuroscifi/venv` | root, 0755 | Code. Not writable by the service. |
| `/etc/neuroscifi` | root:neuroscifi, 0750 | Settings. |
| `/etc/neuroscifi/neuroscifi.env` | root:neuroscifi, 0640 | Section 3. |
| `/etc/neuroscifi/tls` | root:neuroscifi, 0750 | Section 4. |
| `/var/lib/neuroscifi` | neuroscifi, 0700 | State directory (`StateDirectory=neuroscifi`). Mount the encrypted volume here. |
| `/var/lib/neuroscifi/instance.sqlite` (+ `-wal`, `-shm`) | neuroscifi, 0600 | The individual. WAL files are part of it while open. |
| `/var/lib/neuroscifi/pocket.sqlite` | neuroscifi, 0600 | Pocket pairings and 24-hour records. |
| `/var/lib/neuroscifi/checkpoints/*.nsfa` | neuroscifi, 0600 in a 0700 folder | Verified archives. |
| `/run/neuroscifi/console-url` | neuroscifi, 0600 in a 0700 tmpfs folder | Console access URL *(available from 0.5.0)*; removed when the console stops. |
| `/run/neuroscifi/invitation` | neuroscifi, 0600 in a 0700 tmpfs folder | One-use pairing code, short-lived. |

The service user has no login shell (`/usr/sbin/nologin`) and no home directory.

## 7. Service hardening (what each unit sets and what was checked)

All three units set: `User=neuroscifi`, `StateDirectory=neuroscifi` (0700), `EnvironmentFile=-/etc/neuroscifi/neuroscifi.env`, an absolute `ExecStart` under `/opt/neuroscifi/venv/bin`, `NoNewPrivileges=yes`, `CapabilityBoundingSet=` (empty), `AmbientCapabilities=` (empty), `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`, `PrivateDevices=yes`, `UMask=0077`, `ReadWritePaths=%S/neuroscifi`, `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX` (the checkpoint job: `AF_UNIX` plus `PrivateNetwork=yes`), `SystemCallFilter=@system-service`, `SystemCallArchitectures=native`, `MemoryDenyWriteExecute=yes`, `ProtectKernelTunables/Modules/Logs`, `ProtectControlGroups`, `ProtectClock`, `ProtectHostname`, `ProtectProc=invisible`, `ProcSubset=pid`, `RestrictNamespaces`, `RestrictRealtime`, `RestrictSUIDSGID`, `LockPersonality`, and `RequiresMountsFor=%S/neuroscifi`. Pocket and the console also set `RemoveIPC=yes`, `PrivateUsers=yes`, `MemoryMax` (1G Pocket, 2G console), `TasksMax=128` and `RuntimeDirectory=neuroscifi` (0700, tmpfs; the console URL and Pocket's invitation live there). `PrivateUsers=yes`, like the namespace options, has **not** been started on a host; if a service fails to start with a namespace error, remove that one line and record why. Both units carry a commented `ConditionPathIsMountPoint=/var/lib/neuroscifi`: uncomment it once the encrypted volume is mounted there, so the unit refuses to start when the volume is absent (`RequiresMountsFor` alone cannot tell an unmounted volume from a plain directory on the root disk). Memory and task ceilings are starting values: raise `MemoryMax` if the console is killed during a large graph evaluation. The console adds `IPAddressDeny=any` and `IPAddressAllow=localhost`. The checkpoint job runs at `Nice=19` with idle I/O so it stays off the interactive loop.

Checks made on the development host (systemd 262, aarch64, Python 3.12, numpy 2.3.5): `MemoryDenyWriteExecute=yes` works. numpy matrix products, zstandard, sqlite3, `nsf init`, `sweep`, `checkpoint create` and `verify`, and `nsf pocket serve` ran under `MemoryDenyWriteExecute=yes`, `SystemCallFilter=@system-service`, `PrivateDevices`, the address-family restriction, an empty capability set and `UMask=0077` in a user-mode transient unit. Adding `SystemCallFilter=~@privileged @resources` made Python die with SIGSYS, so it is not set. The namespace options (`ProtectSystem=strict`, `PrivateTmp`, `ReadWritePaths`) cannot be exercised in a user-mode unit and have **not** been run; they are checked only by `systemd-analyze verify`. Test them on the Spark with `systemd-analyze security neuroscifi-pocket.service` and a real start. If a future dependency needs executable memory, remove `MemoryDenyWriteExecute=yes` and record why in the unit.

## 8. Encrypted volume and backup exclusions

NeuroSciFi does not encrypt data itself (ADR-011). The database is cleartext SQLite, protected by file modes and by the volume.

- Mount a LUKS or fscrypt-protected filesystem at `/var/lib/neuroscifi` before the first start. This holds the instance database, Pocket's database and checkpoints. (The console URL is in `/run/neuroscifi`, on tmpfs.)
- Check: `findmnt /var/lib/neuroscifi` and `lsblk -o NAME,TYPE,FSTYPE,MOUNTPOINT` should show a `crypt` device *(unverified on a host)*. `nsf doctor --db PATH` *(available from 0.5.0)* reports the filesystem but cannot prove encryption.
- Exclude `pocket.sqlite`, `pocket.sqlite-wal` and `pocket.sqlite-shm` from every backup, snapshot sync and cloud sync tool. Do the same for `/run/neuroscifi`. For the instance database and checkpoints, include them in backups only on purpose, to storage with equal protection, knowing that erasure does not reach copies there.
- Unmounted volume: the units will not start (`RequiresMountsFor`). Do not create `/var/lib/neuroscifi` on the root disk to get around it.
