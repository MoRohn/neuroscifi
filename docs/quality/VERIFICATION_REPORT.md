# Verification report — NeuroSciFi 0.5.0

- **Commit verified:** `88f56fd` on `build/neuroscifi-v1`.
- **Host:** NVIDIA DGX Spark. aarch64, 20 cores, GB10 GPU, 121 GB. It runs a general-purpose Arch-based distribution, not DGX OS, and has no camera or microphone attached.
- **Toolchain:** Python 3.12.16, SQLite 3.53.1, Node 26, Chromium (Playwright 1.64.1, revision 1248).

Every result below comes from a run by the integrator. An agent's report is never the evidence.

## 1. Release verdict

| Area | Result |
|---|---|
| Quality gates G1 (lint, unit and integration, JavaScript, reproduction, browser e2e, accessibility, packaging) | **PASS** |
| Acceptance scenarios (`nsf acceptance --all`) | **7 of 7 pass** after the pre-registered D-1 redesign (§5) |
| Critical journeys J1–J6 | **PASS** |
| Security acceptance G3 | **PASS** after remediation. 0 open P0 or P1 (§4). |
| Open P0 / P1 defects | **0 / 0** |
| Deployment | **Not deployed.** Units and installer are verified offline only (§6). |

Under ACCEPTANCE_CRITERIA, every gate and acceptance scenario passes. ACC-08's earlier-setup recognition was resolved through a design that was pre-registered (`3b4e943`) before implementation or measurement. Thresholds were not lowered; a false-offer control was added (§5).

## 2. Quality gates (fresh runs at `88f56fd`)

| Gate | Command | Result |
|---|---|---|
| Lint | `ruff check src tests` | All checks passed |
| Unit and integration | `pytest -q --ignore=tests/e2e` (inside `scripts/check.sh`) | **290 passed**, 31 subtests, 0 skipped, 0 failed |
| JavaScript checks | `node tests/js/*.mjs` | 4 of 4 passed. The field benchmark reports coverage 0.888 and selective accuracy 0.883, matching the brief's 88.8% and 88.3%. |
| Numerical reproduction | `python scripts/reproduce.py` | **8 of 8 Verified**, bit-exact against `evidence/*.json` |
| Browser e2e (Pocket 13 + console 12) | `bash scripts/e2e.sh` | **25 passed**. Run 4 times after remediation; no failures. |
| Accessibility | axe-core inside e2e | 0 serious or critical violations at 390×844 and 1280×900, on every Pocket and console view |
| Keyboard | e2e | Every enabled button is reachable by Tab, with a visible focus ring |
| Packaging | `uv pip install .` into a clean venv | Installed. `nsf --help` works. Both web UIs ship as package data. |
| GPU extra | `uv pip install '.[gpu]'`, then `nsf lab profile_device --device cuda` | Runs on GB10 with numpy 2.3.5 pinned |
| Service units | `systemd-analyze verify deploy/systemd/*` | Only output: the venv binary is absent on this host. Security exposure scores: Pocket 1.3, console 1.1. |
| Orphaned processes | `pgrep` after the suites | 0. One orphan from an earlier interrupted run led to a fixture fix in `88f56fd`. |

## 3. Critical journeys

| ID | Journey | Evidence |
|---|---|---|
| J1 | **Teach, detect a silent remount, confirm, keep meaning.** Run by the integrator through the README CLI commands. | Readings flip, then `status` flags `suspected_change` (llr 8.75), then `decide` returns `ask camera:cam-a`, then `resolve --confirm`. After that, partner p_inverted is unchanged at 0.022, the mechanism stays identifiable, and `decide` returns `act`. Acceptance `grounding`, product-flow arm: confident-wrong 62 → 3 (−95%), coverage 0.85, task loss 0.0 points, Brier 0.73 → 0.059. Partner meaning is preserved in 82% of camera-change lifetimes (manual new epoch: 16%). |
| J2 | **Two partners, a day later** | Acceptance `lifelong_correction`: both meanings correct in session, a day later in a new process, and from semantic memory alone. Exactly 1 meaning question, asked while partner B was ambiguous. 0 open questions at the end. |
| J3 | **Grow and recall** | Acceptance `growth_retention`:<br>• A rewired candidate is rejected for retention only (old-world MSE 0.0131 against a limit of 0.00474) with a "register as a new context" recommendation.<br>• As a new context it is promoted.<br>• Recall: context accuracy 95.5%, selected MSE 0.00465 vs 0.00903 for the latest graph only.<br>• An unrelated world gets `unknown_context` (99.4%) with an owner question and no graph created.<br>• Lookup-disabled recall gives identical results. |
| J4 | **Forget everything derived** | Covered by `tests/instance/test_erasure.py`, `test_governance.py`, `test_checkpoints.py` and `test_journeys.py`. In the integrator's CLI run, after `forget partner alice` the name occurs **0 times** in a full SQL dump. Restore re-applies tombstones, graph invalidation and root scrubs. The WAL is truncated after a forget. |
| J5 | **Inspect in the console** | `tests/e2e/test_console.py`: teach, change badge, "Yes, it changed — relearn only that", act, forget through a dialog (the id disappears from the page), match panel for an earlier setup, graphs gate checklist and heatmap with 72 cells, functional map, checkpoints create, verify, prune and rebuild. |
| J6 | **A Pocket day** | `tests/e2e/test_pocket.py`:<br>• Sensors make no network calls before pairing; then pair, share, learn, forecast and forget.<br>• Field forecast, feedback and vault.<br>• Evening review: mute and forget the session.<br>• Tablet handoff with forget across devices.<br>• Offline cold reopen.<br>• Phone and tablet layouts. |

## 4. Independent reviews and defect disposition

Three read-only reviewers ran in parallel at `d37347c`: security (opus), code (opus) and requirements completeness (opus). Each received the code and the brief, not the team's conclusions.

| Finding | Severity | Fix (commit) | Regression test |
|---|---|---|---|
| Pocket over TLS freezes behind one idle TCP connection | P1 sec | Handshake in the worker thread with a timeout; 32-connection cap (`7e0af91`) | `PocketTLSRobustness.*` |
| Forgotten partner or camera names survive in revisions, questions and decisions | P1 sec + code + audit | Salted-digest scrub in the same transaction; `forgotten_roots`; restore re-applies it (`7e0af91`) | `test_erasure.py` (scans every table plus the raw DB and WAL bytes) |
| Confirmation ignored, re-ask loop, recalibration resets the partner's meaning (the design question) | P1 code + audit | Confirmed changepoints segment only the changed factor; answered questions are not re-asked; `suspected_change` blocks `act` (`7e0af91`) | `test_scope_learning.py`, `test_executive.py` |
| A graph trained on a forgotten dataset could be promoted | P1 code | Commit-time lineage check plus the gate check `lineage_intact` (`7e0af91`) | `test_graphs.py` |
| A checkpoint taken during a forget was registered valid | P1 code | Checks the deletion-log watermark; such a checkpoint is registered stale (`7e0af91`) | `test_deletion_during_pack_registers_the_archive_stale` |
| A founding graph never yielded `unknown_context` | P1 code | A graph without a validation MSE cannot vouch for a step (`7e0af91`) | `test_recall.py` |
| The console unit could not restart | P1 code + sec + audit | Runtime-dir URL file, removed on SIGTERM, stale file replaced atomically (`7e0af91`) | `test_console_restarts_after_sigterm_and_after_a_crash` |
| Forgotten payloads kept in the WAL | P2 sec | `journal_size_limit=0` and `wal_checkpoint(TRUNCATE)` after deletes | `test_wal_is_truncated_*` |
| Restore re-activated graphs built on forgotten data | P2 sec + code | `invalidate_dependents` on the staged copy | `test_restore_invalidates_graphs_*` |
| World-readable archive, graphfile and export outputs | P2 sec | `O_EXCL\|O_NOFOLLOW`, mode 0600 | `tests/test_files_cli.py` |
| Client IPs and tracebacks in the journal | P2 sec | Fixed `handle_error`; SQLite error → 503 | `test_reset_mid_request_leaks_no_address_or_traceback` |
| Verify decoded to the system `/tmp` | P2 sec | Temp directory next to the archive | `test_verify_decodes_next_to_the_archive` |
| Console GETs blocked behind writers, then returned 500 | P2 code | GETs sweep at most every 60 s without blocking; locked → 503 with Retry-After | `test_get_does_not_wait_for_a_writer_*` |
| O(N²) ingest | P2 code | SQL aggregates plus an index. At 50k events: 572 ms → about 20 ms per observe and decide | perf test |
| No checkpoint retention; shared checkpoint folder | P2 code + audit | `prune(keep)`, `<stem>.checkpoints` | `test_prune_*`, `test_default_dir_*` |
| Forget closed every open question | P2 code | Removed. The executive closes questions on evidence. | `test_forget_leaves_open_questions_*` |
| Cascade re-activated a graph the owner had rolled back from | P2 code | Skip revisions replaced by rollback | `test_graphs.py` |
| Belief confidence always null; COMMIT failure left the transaction open; free-space checks skipped on bulk writes; promote held the lock while scoring | P2 code | Fixed (`7e0af91`) | `test_memory.py`, `test_store`, `test_bulk_writes_*`, promote concurrency test |
| Stale confident status after a detected change | P2 audit | `suspected_change` flags | `test_scope_learning.py` |
| Recognising an earlier setup | P1 audit | Segment candidates, exact new-setup alternative, epoch links (`88f56fd`) | `test_scope_learning.py` (brute force to 1e-12). **Automatic recognition stays information-bounded (§5).** |
| GPU and Spark work misclassified as hardware-blocked | audit | Reclassified. CUDA probe run on GB10. | `docs/quality/evidence/profile_device_cuda_gb10.json` |

**P3 items accepted as known limitations:**
- Pocket bearer keys do not expire server-side until unpair.
- The `retired` tables grow per device.
- Vault records use no AAD, and one key protects both rows and the remembered pairing.
- Crafted NSFA archives using the experimental `adaptive` codec can cost a lot of CPU to decode. Archives are the instance's own files.
- Old SQLite rollback-journal pages are unlinked, not wiped. The encrypted-volume requirement covers this.

## 5. Acceptance scenarios

**Run:** `nsf acceptance --all`, seed 20260519, 200 lifetimes, 100 test lifetimes. All runs are simulated, and the outputs are labelled so. Evidence: `docs/quality/evidence/acceptance.json`.

| Scenario | Pass | Key measurements |
|---|---|---|
| birth | yes | Priors, semantics, encoders and founding graph (247 B) published. 0 personal episodes. Baseline decision `observe`. |
| grounding | yes | See J1. The thresholds (≥ 50% fewer confident-wrong answers, ≥ 80% coverage, ≤ 2 points task loss) were met. |
| lifelong_correction | yes | See J2 |
| graph_development | yes | Each fork is about 7× better on its own world than the other fork. Learned graph 0.00462 vs dense least-squares 0.00435 vs fixed random sparse 0.0149. Lesion: true-edge Δ 2.0e-4 vs weak-edge Δ about 0. |
| growth_retention | yes | See J3 |
| device_ownership | yes | Idle observe→decide p50 2.10 ms, p99 2.22 ms. Under concurrent learning and checkpointing, p99 3.4 ms with 0 failed writes. Restart, checkpoint restore and write pause all behave correctly. |
| first_physical_test (p.34) | **no (3/4)** | Attribution 0.92 ✓. Older-skill retention 0.975 ✓. Confident-wrong 0 vs 78 for fixed wiring ✓. **Earlier-setup recognition 0.00 ✗** |

**D-1: earlier-setup recognition, resolved by a pre-registered design.**

At `88f56fd`, recognition was 0.00. The analysis behind the fix:
- The scenario had **no negative control**, so a recognition rate alone was meaningless.
- **Calibration bits alone can never pass.** A new setup reproduces K binary relations by chance with probability 2^-K, so 80% recall forces at least 20% false offers when K = 2.
- **The missing evidence is the panel's own dynamics.** This is the brief's p.10 mechanism, and the camera remount does not change it.

The design and thresholds were committed in `docs/quality/PREREGISTRATION_D1.md` (`3b4e943`) before any code or measurement. The only later edit is an appended "Implementation notes" section; 0 original lines changed. The design:
- epoch ↔ graph context;
- a 12-step observation stream for the current epoch;
- a Gaussian dynamics likelihood against a zero-knowledge "new panel" alternative;
- calibration bits that pick the camera segment;
- an empirical prior learned from the owner's earlier answers;
- the 3:1 ask threshold, unchanged.

| Judged check (100 true returns, 100 new-panel returns) | Threshold | Result |
|---|---|---|
| Earlier setup recognised | ≥ 0.80 | **1.00** |
| False offers on new panels (same device names) | ≤ 0.10 | **0.00** |
| Original camera segment offered | ≥ 0.80 | **0.89** |
| Attribution, retention, confident-wrong vs fixed wiring | unchanged | 0.92, 0.975, 0 vs 78 |

**Bits-only arm (reported, not judged):** recognition 0.00, with the maximum posterior exactly the predicted 2/3 bound.

**Post-hoc probe (not pre-registered; kept as `tests/instance/test_near_duplicate_panels.py`).** The hardest negative control is the brief's rewired panel: one of three inputs per population changed, with identical calibration bits and device names.

| Return | False offers | Dynamics margin vs new setup |
|---|---|---|
| Rewired near-duplicate | 0/30 | −79 nats on average |
| Fresh panel | 0/30 | −597 nats |
| Same panel | 30/30 offered (correct) | +184 nats |

The old graph beats the zero predictor on the rewired panel (MSE 0.013 vs 0.018), but not its own calibrated error (0.0043). The likelihood penalises that overconfidence.

**What this does not show:**
- Panels whose dynamics differ by less than one rewired input per population would be offered as the same setup.
- Simulated panels have clean dynamics. Real sensor streams need their own calibration of the residual variance.
- The 0.11 of returns offered the remounted camera segment happen because recognition fires on the first camera reading. You correct it by answering "No".

## 6. Reproducibility and device evidence

| Item | Result | File |
|---|---|---|
| CPU probe (aarch64) | Parity max error 0.0. Batch 1 p50 0.042 ms; batch 64 p50 1.05 ms. | `evidence/profile_device_cpu.json` |
| CUDA probe (GB10) | Parity max error 1.0e-6. Batch 1 p50 0.196 ms; batch 64 p50 0.218 ms. The CPU wins at batch 1 and the GPU at batch 64. | `evidence/profile_device_cuda_gb10.json` |
| Compression bake-off | JSON 6.27× fast, matching the brief | `evidence/compression_bench_aarch64.json` |
| CPU repeat summary | At 25% active: shuffled 30.81 ms, bitmap 8.51 ms, preordered 7.02 ms, matching the brief's repeat column | `evidence/cpu_repeat_summary.txt` |

## 7. What is not verified

- **No deployment.** The systemd units and `install.sh` have not been run as real services. `PrivateUsers=yes`, `ProtectSystem=strict` and the reversible switchover are unexercised.
- **No real sensors.** There is no camera or microphone, so there is no real sensor-to-action latency, no dropped-capture measurement and no energy per skill (OPS-06 sensor part).
- **No native handset builds or tests** (NAT-01..03). No Android SDK, Xcode or handsets are available.
- **Human-data reanalyses** (RES-05) were not re-fetched.
- **Real panel dynamics.** Earlier-setup recognition depends on attaching an observation stream (`nsf epoch stream`), and it has only been validated on simulated dynamics.
- **GRA-01b** (edge branch and delay fields) is not implemented (ADR-010). **OPS-10** (device latency budget) needs the owner's budget.
