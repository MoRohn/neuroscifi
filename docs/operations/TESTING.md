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
- **The DGX Spark has not been used.** No test runs on the Spark, on DGX OS, or on a GPU. Nothing has measured p50/p95/p99 sensor-to-action latency, dropped inputs, memory high-water mark or energy per skill (OPS-06 is blocked on hardware). The repository's CPU benchmarks are from other hosts.
- **No GPU or CUDA path is exercised** by the instance, Pocket or console tests.
- **The deployment is unexercised.** `deploy/install.sh` has been syntax-checked and dry-run only. The units have passed `systemd-analyze verify` and a user-mode sandbox run of the CLI, but have not been started as system services. Namespace hardening (`ProtectSystem=strict`, `PrivateTmp`) has not run, nor TLS to a real phone, nor an encrypted volume, nor a power-loss recovery.
- **The acceptance scenarios are simulated.** They do not claim physical validation.
- **Not tested at all:** a trusted public certificate chain, VPN or LAN topology, backup tooling exclusions, and concurrent heavy load.
