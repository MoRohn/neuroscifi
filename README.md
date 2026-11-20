# NeuroSciFi
NeuroSciFi is a hierarchical, multimodal AI architecture centered on one developing artificial connectome. Predictions guide action. Feedback can change a belief, a skill or the wiring itself. Its proposed hybrid memory couples fast episodes and slowly consolidated knowledge with exact, governed digital evidence. The runnable components exercise calibration, graph learning, compact storage and recall on controlled tasks.

The 34-page architecture brief is also included as editable Architecture.md. It contains reanalyses of human brain and behavioral data, controlled simulations, two recorded CPU benchmark runs, TufaZip source evaluation and a first DGX Spark implementation plan, and a mobile-first field runtime for phones and tablets.

Use Python 3.12 with the pinned NumPy and Zstandard packages. The additional research requirements include SciPy. Core experiments use generated data. The human-data reproduction workflow retrieves source files at verified Git hashes. No pretrained model or cloud service is needed to run the cognitive prototype.

```sh
uv venv .venv -p 3.12
uv pip install -e '.[dev,research]'
. .venv/bin/activate
```

The `nsf` command is installed with the package; `nsf --help` lists everything. `bash scripts/check.sh` runs lint, tests, the JavaScript checks and the numerical reproduction.

## Try the phone interface

Run `nsf pocket serve`, open `http://localhost:8765` on that computer, and try a sound or light check. Pair only when ready to share a reviewed observation. For a smartphone, use the private HTTPS setup in [the Pocket guide](mobile/README.md). Open the optional field vault, combine sound or device metrics with a goal, predict an outcome and return later with feedback. The Pocket guide covers native Android/iOS collectors, offline operation, tablet handoff, the synthetic learning benchmark and privacy controls.

## Run a persistent instance

One file is one individual. `nsf init` creates it and refuses to overwrite an existing file. Every command prints JSON. Set `NSF_DB` or pass `--db`.

```sh
export NSF_DB=$PWD/individual.sqlite
nsf init --action-semantics '{"0":"turn left","1":"turn right"}'
nsf birth                                            # priors, action semantics, encoders, memory inventory
nsf epoch start bench
nsf observe bench e01 camera 0 0 cam-a               # event id, channel, commanded bit, observed bit, root
nsf observe bench e02 word 0 0 alice                 # for a word, the root is the partner who spoke
nsf observe bench e03 reference 0 0 standard-1       # an independent mechanical reference
nsf status bench                                     # beliefs: mechanism, each camera root, each partner
nsf decide bench --record                            # act | observe | ask | test_reference | defer, with a reason
nsf explain bench                                    # supporting and counter evidence, remaining uncertainty, revisions
```

**The design question.** A camera is remounted silently while a person keeps saying "left". The individual handles it like this:
1. Camera readings start to disagree. `nsf status` flags `suspected_change` on `cam-a`.
2. `nsf decide --record` asks about scope `camera:cam-a`, and does not act on the stale belief.
3. The owner confirms with `nsf resolve ID "remounted" --confirm`. Only the camera's alignment relearns; the partner's meaning and the mechanism carry over.
4. Later, a new epoch with readings from the original setup is recognised. `nsf epoch match NEW` names the earlier epoch, and the executive asks whether to continue there.

Other commands:
- `nsf pending` lists open questions and `nsf resolve` answers them.
- `nsf memory evidence|belief|graph|…` retrieves records with scope, permissions and state version.
- `nsf forget evidence|epoch|partner|dataset` deletes with a full cascade: derived beliefs, questions and decisions are scrubbed, dependent graphs are invalidated and checkpoints are marked stale.
- `nsf promote` keeps evidence past its default 7-day lifetime.
- `nsf checkpoint create|verify|restore|rebuild|prune` manages verified lossless archives. A restore never resurrects forgotten ids.
- `nsf import-legacy OLD.sqlite` imports a database from the prototype's `live.py`.

**Graphs.** `nsf graph simulate CTX --seed N` generates labelled synthetic trajectories. `learn`, `evaluate` and `promote` run the immutable-rule gate. Also available: `recall DATASET`, `coactivation DATASET`, `lesion REV DATASET` and `rollback CTX REV`.

**Owner console.** `nsf console` serves the same operations as a web UI, on loopback only. It prints a one-time URL; from another machine, reach it through `ssh -L 8766:127.0.0.1:8766`.

**Acceptance.** `nsf acceptance --all` runs the brief's first-release milestones as labelled simulations. `nsf bench` reports host decision latency, and `nsf doctor --db FILE` checks permissions, WAL mode, free space and volume encryption.

Operations, deployment and upgrades: `docs/operations/` (RUNBOOK, ENVIRONMENT, MIGRATIONS, TESTING). Architecture and requirement traceability: `docs/architecture/`.

The prototype's original learner is kept, unchanged in behaviour, as `python -m neuroscifi.legacy_live`. It is the parity oracle for the new inference.

## Run the component suite

```sh
pytest -q --ignore=tests/e2e
python scripts/reproduce.py
```

The verifier uses temporary output files, invokes all eight deterministic analyses and checks the supplied summaries and per-room results to numerical tolerance. The host timing file is informative and is excluded from deterministic comparison. The complete test suite uses both packages in requirements.txt. Each executable also accepts --help where relevant.

| Script | What it tests |
|---|---|
| neuroscifi.lab.core | Identifiability, common reference faults, confidence on duplicate evidence and equal-budget reference scheduling over changing lifetimes. |
| neuroscifi.lab.sensory | Raw 24 × 24 RGB images, 8 kHz PCM waveforms, class prototypes and a failed familiarity-only acceptance rule. |
| neuroscifi.lab.recalibrate | Announced domain changes. New calibrated rooms, simple supervised refitting and reliability gating under audio aliasing. |
| neuroscifi.lab.source_groups | Independent readout noise versus persistent calibration faults; unknown shared cause is a limitation. |
| neuroscifi.lab.graph_bench | Continuous weight updates and directed edge exchanges under a fixed edge budget in fully observed, perturbed linear worlds. |
| neuroscifi.lab.human_counts | Recalculation of two choice contrasts from published human infant summary counts. |
| neuroscifi.legacy_live | Persistent binary model with event IDs, calibration epochs and separate instrument roots. |
| neuroscifi.lab.profile_device | FP32 recurrent rollout probe comparing numerical output with NumPy. CUDA mode optionally uses CuPy. |

## Packed graphs, restored skills and verified archives

```sh
python -m neuroscifi.lab.graph_compression_bench
python -m neuroscifi.lab.context_recall
nsf archive checkpoint example.sqlite example.nsfa --policy cold
nsf archive unpack example.nsfa restored.sqlite
python -m neuroscifi.legacy_live --db restored.sqlite status bench
python -m neuroscifi.lab.compression_bench --repeats 3
```

For a NumPy matrix, run `nsf graphfile pack weights.npy weights.nsg --degree 3 --bits 4`. Restore the represented weights with `nsf graphfile unpack weights.nsg recovered.npy`. Select the degree and precision on separate validation observations before promoting a lossy snapshot. FP32 mode preserves float32 weights. The optional arithmetic codec belongs to the byte-exact archive stage; the quantized graph cannot recover weights discarded during pruning. Format NSG1 stores the graph checksum, row peaks, source indices and signed packed weights. NSFA version 1 stores a codec and checksums for each independently decodable block plus a whole-file footer.

Fast archives choose raw or Zstandard. Cold tries XZ and reversible 16-bit delta plus Zstandard as well. Experimental also tries the vendored TufaZip arithmetic coder with a new deterministic integer predictor. Existing archives identify the codec in each block and can be restored using any policy. Compression results include framing. Size and timing comparisons use a controlled, small corpus. Keep this optional slow path off the interactive loop.

The upstream decoder targets enwik9 on two B200 GPUs. Its verified neural bitstream and trained weights are separate from this implementation. Vendored bytes in src/neuroscifi/vendor_tufazip are pinned to Tufalabs/tufazip commit b9d678db3a0f726e117c8cab51927975830395ac. The arithmetic coder blob is bcd7e0f704d5cf8333e6c8fea700b0cfc9f18c11. LICENSE and NOTICE are included unchanged.

## Reproduce the earlier human-data analyses and both CPU comparisons

The research directory contains scripts, input hashes, original numerical results and the new CPU repeat. Follow research/README.md for the public-data workflow and optional compiler requirements. To print each timing report, run these commands from the project root.

```sh
python3 research/scripts/summarize_timings.py research/results
python3 research/scripts/summarize_timings.py research/cpu_repeat
```

## Profile on the target machine

```sh
OPENBLAS_NUM_THREADS=1 python -m neuroscifi.lab.profile_device --device cpu
python -m neuroscifi.lab.profile_device --device cuda
```

GPU mode requires a working CuPy build for the installed NVIDIA driver and runtime: `uv pip install -e '.[gpu]'` installs CuPy 14.2.0 for CUDA 13 (verified on a DGX Spark GB10; results in `docs/quality/evidence/profile_device_cuda_gb10.json`). It checks numerical parity, measures synchronized device-resident execution, then includes input and output transfers. Results are per component. Establish sensor-to-action latency, memory competition and energy separately on the full device under concurrent capture and replay. The original CPU sample was collected on x86; a DGX Spark (aarch64) rerun is in `docs/quality/evidence/`. No program here changes device services, boot settings or drivers.

## Interpretation

All simulated results specify the hidden variables, source graphs and statistical assumptions in code. The graph benchmark provides population states directly; its candidate search is dense and uses higher precision. Independent standards in the evidence experiment are genuine independent roots only when the environment supplies them. Healthy sensor calibration uses labeled standards. Account for those labels and the 192 initial validation pairs when assessing sample efficiency. The reference model, sensory front end and neural graph benchmark are separate components; their results do not constitute an integrated human-level system.
