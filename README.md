# NeuroSciFi
NeuroSciFi is a hierarchical, multimodal AI architecture centered on one developing artificial connectome. Predictions guide action. Feedback can change a belief, a skill or the wiring itself. Its proposed hybrid memory couples fast episodes and slowly consolidated knowledge with exact, governed digital evidence. The runnable components exercise calibration, graph learning, compact storage and recall on controlled tasks.

The 34-page architecture brief is also included as editable Architecture.md. It contains reanalyses of human brain and behavioral data, controlled simulations, two recorded CPU benchmark runs, TufaZip source evaluation and a first DGX Spark implementation plan, and a mobile-first field runtime for phones and tablets.

Use Python 3.12 with the pinned NumPy and Zstandard packages. The additional research requirements include SciPy. Core experiments use generated data. The human-data reproduction workflow retrieves source files at verified Git hashes. No pretrained model or cloud service is needed to run the cognitive prototype.

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements.txt
```

## Try the phone interface

Run `python3 mobile/bridge.py`, open `http://localhost:8765` on that computer, and try a sound or light check. Pair only when ready to share a reviewed observation. For a smartphone, use the private HTTPS setup in [the Pocket guide](mobile/README.md). Open the optional field vault, combine sound or device metrics with a goal, predict an outcome and return later with feedback. The Pocket guide covers native Android/iOS collectors, offline operation, tablet handoff, the synthetic learning benchmark and privacy controls.

## Run a persistent instance

From this folder, run the following with Python and NumPy available. Every observation uses an event ID, channel, commanded bit, measured bit and source root. Change the file path to start a separate individual. Each epoch holds a distinct calibration context.

```sh
python3 src/live.py --db example.sqlite start bench
python3 src/live.py --db example.sqlite observe bench e01 camera 0 0 camera-a
python3 src/live.py --db example.sqlite observe bench e02 word 0 0 speaker-a
python3 src/live.py --db example.sqlite observe bench e03 reference 0 0 mechanical-standard-a
python3 src/live.py --db example.sqlite status bench
```

The two directions on a real control can be encoded as bits according to a declared convention. These commands use manually encoded observations. The separate sensory experiment starts from generated RGB and PCM arrays. Replaying the same event ID is idempotent; reusing an ID for a different payload raises an error. For physical references the estimator groups readings by calibration root. Camera and word inputs assume independent reading noise. A transcript or second view of the same sample needs the same evidentiary identity at ingestion. The narrow executable does not implement authentication, raw capture, live voice interpretation, automatic context discovery or background graph updates.

## Run the component suite

```sh
python3 -m unittest discover -s tests -q
python3 reproduce.py
```

The verifier uses temporary output files, invokes all eight deterministic analyses and checks the supplied summaries and per-room results to numerical tolerance. The host timing file is informative and is excluded from deterministic comparison. The complete test suite uses both packages in requirements.txt. Each executable also accepts --help where relevant.

| Script | What it tests |
|---|---|
| src/core.py | Identifiability, common reference faults, confidence on duplicate evidence and equal-budget reference scheduling over changing lifetimes. |
| src/sensory.py | Raw 24 × 24 RGB images, 8 kHz PCM waveforms, class prototypes and a failed familiarity-only acceptance rule. |
| src/recalibrate.py | Announced domain changes. New calibrated rooms, simple supervised refitting and reliability gating under audio aliasing. |
| src/source_groups.py | Independent readout noise versus persistent calibration faults; unknown shared cause is a limitation. |
| src/graph_bench.py | Continuous weight updates and directed edge exchanges under a fixed edge budget in fully observed, perturbed linear worlds. |
| src/human_counts.py | Recalculation of two choice contrasts from published human infant summary counts. |
| src/live.py | Persistent binary model with event IDs, calibration epochs and separate instrument roots. |
| src/profile_device.py | FP32 recurrent rollout probe comparing numerical output with NumPy. CUDA mode optionally uses CuPy. |

## Packed graphs, restored skills and verified archives

```sh
python3 src/graph_compression_bench.py
python3 src/context_recall.py
python3 src/archive.py checkpoint example.sqlite example.nsfa --policy cold
python3 src/archive.py unpack example.nsfa restored.sqlite
python3 src/live.py --db restored.sqlite status bench
python3 src/compression_bench.py --repeats 3
```

For a NumPy matrix, run `python3 src/graph_store.py pack weights.npy weights.nsg --degree 3 --bits 4`. Restore the represented weights with `python3 src/graph_store.py unpack weights.nsg recovered.npy`. Select the degree and precision on separate validation observations before promoting a lossy snapshot. FP32 mode preserves float32 weights. The optional arithmetic codec belongs to the byte-exact archive stage; the quantized graph cannot recover weights discarded during pruning. Format NSG1 stores the graph checksum, row peaks, source indices and signed packed weights. NSFA version 1 stores a codec and checksums for each independently decodable block plus a whole-file footer.

Fast archives choose raw or Zstandard. Cold tries XZ and reversible 16-bit delta plus Zstandard as well. Experimental also tries the vendored TufaZip arithmetic coder with a new deterministic integer predictor. Existing archives identify the codec in each block and can be restored using any policy. Compression results include framing. Size and timing comparisons use a controlled, small corpus. Keep this optional slow path off the interactive loop.

The upstream decoder targets enwik9 on two B200 GPUs. Its verified neural bitstream and trained weights are separate from this implementation. Vendored bytes in src/vendor_tufazip are pinned to Tufalabs/tufazip commit b9d678db3a0f726e117c8cab51927975830395ac. The arithmetic coder blob is bcd7e0f704d5cf8333e6c8fea700b0cfc9f18c11. LICENSE and NOTICE are included unchanged.

## Reproduce the earlier human-data analyses and both CPU comparisons

The research directory contains scripts, input hashes, original numerical results and the new CPU repeat. Follow research/README.md for the public-data workflow and optional compiler requirements. To print each timing report, run these commands from the project root.

```sh
python3 research/scripts/summarize_timings.py research/results
python3 research/scripts/summarize_timings.py research/cpu_repeat
```

## Profile on the target machine

```sh
OPENBLAS_NUM_THREADS=1 python3 src/profile_device.py --device cpu
python3 src/profile_device.py --device cuda
```

GPU mode requires a working CuPy build for the installed NVIDIA driver and runtime. It checks numerical parity, measures synchronized device-resident execution, then includes input and output transfers. Results are per component. Establish sensor-to-action latency, memory competition and energy separately on the full device under concurrent capture and replay. The included CPU sample was collected on x86. No program here changes device services, boot settings or drivers.

## Interpretation

All simulated results specify the hidden variables, source graphs and statistical assumptions in code. The graph benchmark provides population states directly; its candidate search is dense and uses higher precision. Independent standards in the evidence experiment are genuine independent roots only when the environment supplies them. Healthy sensor calibration uses labeled standards. Account for those labels and the 192 initial validation pairs when assessing sample efficiency. The reference model, sensory front end and neural graph benchmark are separate components; their results do not constitute an integrated human-level system.
