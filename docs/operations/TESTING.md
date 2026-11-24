# Testing and what it does not cover

Run from the project root with the virtual environment active (`. .venv/bin/activate`). `bash scripts/check.sh` runs layers 1 to 4 and stops at the first failure.

## Layers

| # | Layer | Command | What it checks |
|---|---|---|---|
| 1 | Lint | `ruff check .` | Python syntax errors and undefined names (`E9`, `F`; ADR-004). |
| 2 | Python tests | `pytest -q --ignore=tests/e2e` | Compression and archives, the model and lab code, the Pocket store, the Pocket HTTP server over a real TLS handshake (skipped when OpenSSL is unavailable), the invitation file (`tests/test_pocket_invitation.py`), and the whole instance layer (`tests/instance`). |
| 3 | Node checks | `for f in tests/js/*.mjs; do node "$f"; done` | The shipped sensor controller, service worker, offline behavior and field model with synthetic media and time. |
| 4 | Numerical reproduction | `python scripts/reproduce.py` (or `nsf reproduce`) | Rebuilds the deterministic evidence and compares it with the supplied results to numerical tolerance. |
| 5 | Browser end to end | `bash scripts/e2e.sh` | Chromium (Playwright; first run needs `playwright install chromium`, or set `NSF_CHROMIUM=/usr/bin/chromium`) against an in-process bridge: prototype journey, field session and vault, evening review, offline cold open, phone and tablet layouts, keyboard focus, and axe-core accessibility with no serious or critical findings. `npm ci` installs axe-core the first time. Screenshots go to `test-results/`. |
| 6 | Acceptance | `nsf acceptance --all` *(available from 0.5.0)* | Simulated first-release milestones, labelled as simulated (ADR-014). |
| 7 | Unit syntax and hardening | `systemd-analyze verify --man=no deploy/systemd/*`; `bash -n deploy/install.sh`; `bash deploy/install.sh --dry-run` | The units parse and the installer script parses and prints its plan. Not run in CI. |

Focused runs: `pytest -q tests/test_pocket_invitation.py tests/test_mobile.py tests/test_mobile_http.py` for Pocket; `pytest -q tests/instance` for the instance.

**No thread is left behind.** Layer 2 fails at the end of the session when a thread of NeuroSciFi (a name starting with `nsf-`, or a target defined in the package) is still alive, and names the test file that started it (`tests/thread_check.py`, used by `tests/conftest.py`). A test that starts a server closes it (`server_close()`), a voice hub (`close()`), or a bridge run in a thread (`main(started=...)`, then `server.shutdown()`). Leaked threads had gathered to 35 by the time a test forked six children, and a forked child inherits every lock such a thread holds; that test now starts its processes with the `spawn` method.

**The two-account install** is checked with real accounts by `scripts/two_account_check.py`, which `tests/test_deployment_accounts.py` runs: it needs `unshare --user --map-root-user --map-auto --mount` (unprivileged user namespaces with subordinate uids) and is skipped, with the reason, where the host forbids it.

`systemd-analyze verify` on a host without the venv reports that `/opt/neuroscifi/venv/bin/nsf` is not executable. To check the units cleanly, lay out a root with a stand-in file at that path and run `systemd-analyze verify --root=DIR --man=no /etc/systemd/system/neuroscifi-pocket.service ...`.

## How CI runs

`.github/workflows/ci.yml`, on every push and pull request, Ubuntu with Python 3.12 and Node 22:

1. `check`: installs `.[dev,research]` and runs `bash scripts/check.sh` (layers 1 to 4).
2. `acceptance` (after `check`): `nsf acceptance --all --out acceptance.json`, uploaded as an artifact.
3. `e2e` (after `check`): installs Chromium with Playwright and runs `scripts/e2e.sh`; screenshots uploaded as an artifact.

CI does not run layer 7.

## What is not covered

State this plainly when reporting results.

- **No handset has been tested.** The browser tests use headless Chromium with a fake microphone and camera. Real phone browsers, real sensors, iOS Safari behaviour, install-to-home-screen, and permission prompts are untested. The Android and iOS native collectors were not built or run on physical hardware.
- **Development host.** The release was built and tested on an NVIDIA DGX Spark: aarch64, 20 cores, GB10 GPU, 121 GB unified memory. It runs a general Arch-based distribution, not DGX OS, and has no camera or microphone attached.
- **What was measured on it:**
  - the CPU probe;
  - the GB10 CUDA probe (`nsf lab profile_device --device cuda`, with the `gpu` extra);
  - the compression bake-off;
  - host observe→decide latency, idle and under concurrent learning and checkpointing (acceptance `device_ownership`).

  The results are in `docs/quality/evidence/`.
- **Not measured: sensor-to-action latency with real sensors, dropped inputs from real capture, and energy per skill.** There are no capture devices or power meter (OPS-06 sensor part).
- **No GPU in the regular suites.** The instance, Pocket and console tests do not use the GPU. The CUDA path is a separate probe and is not part of `scripts/check.sh` or CI.
- **The deployment is unexercised.** `deploy/install.sh` has been syntax-checked and dry-run only. The units have passed `systemd-analyze verify` and a user-mode sandbox run of the CLI, but have not been started as system services. Namespace hardening (`ProtectSystem=strict`, `PrivateTmp`) has not run, nor TLS to a real phone, nor an encrypted volume, nor a power-loss recovery.
- **The acceptance scenarios are simulated.** They do not claim physical validation.
- **Not tested at all:** a trusted public certificate chain, VPN or LAN topology, backup tooling exclusions, and concurrent heavy load.
