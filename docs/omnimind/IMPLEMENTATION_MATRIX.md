# Implementation matrix, with outcomes (deliverable 5)

Status: 2026-11-27, written by documentation lane 2 on `omnimind` at `09bfff6` with fix lanes F4 (`1aa7eb6`) and F5 (`bf3dac6`) merged. The first version (2026-11-26, in git
at `abc6be4`) ranked 54 changes by a score of benefit, value, impact, confidence, complexity, risk and cost, with
each row's acceptance criterion; that ranking and its details stay in git history. This version gives every row its
actual outcome, the lane that did it and the evidence, adds the rows the later waves created, and restates the
owner's decisions as they now stand. Nothing here is merged into `main`.

Outcome words: **built** (code and tests exist; no benchmark figure of its own), **built and measured** (a benchmark
or evidence file gives a figure), **dropped** (not done, with the reason), **open** (not done yet, still wanted).

## The first screen

- 51 of the 54 first-version rows are built (L4 and L5 for the console only); 35 of them are built and measured. Not
  done: the answer path's use of the planner (L3), durable jobs (L6), optional engines on the system's own bytes (L9),
  and Pocket's view and upload (the Pocket half of L4 and L5). The withheld looks (L8) were taken once each: three are not supported (unknown, hostile, cross).
- Measured rows are measured on synthetic files and simulated people. The planner (C1) was measured and does **not**
  beat the references on held-out by the rule written before the run.
- Every owner decision of the first version is still open except one the main session ruled under the owner's
  standing grayscale rule (the heat map's colours).

## Every row of the first version

| ID | Change | Outcome | Lane, commit | Evidence |
|---|---|---|---|---|
| K1 | the contract: types, errors, `Limits`, registry, spec envelope | built | K, `c7d5aea` / `3d85260` | ADR-072 |
| K2 | `detect`: signatures first | built and measured | K | `omnimind_k.json` (125 files, 99 formats, 0 wrong); unknown family 51/51, 49/49 |
| K3 | the capability-discovery answer and the gap hook | built and measured | K, A | unknown family: 0 wrong; `artifact_gaps` |
| K4 | text, JSON, compressed-stream readers; safety helpers; `nsf inspect` | built and measured | K | intake family |
| B1a | tables: CSV/TSV, JSON records, NDJSON, XLSX, ODS | built and measured | B1, `fb4aa61` / `617e604` | intake family (tables); `omnimind_b1.json` |
| B1b | documents: Markdown, HTML, DOCX, ODT, PPTX/ODP, EPUB, RTF, PDF (optional `pypdf`) | built and measured | B1 | intake family (documents); LibreOffice-made files tried by the integration lane |
| B1c | trees, Python through `ast`, other languages' surface, manifests, notebooks | built and measured | B1 | intake family (code, notebooks, trees) |
| B1d | archives, members in memory; 7z/RAR identified only | built and measured | B1, int (whole-archive refusal) | compound and hostile families |
| B1e | SQLite: schema, counts, a bounded sample | built and measured | B1, F1 (stored columns only) | `test_f1_bounds.py`; security review H1 fixed |
| B2a | lossless rasters to pixels; SVG to shapes | built and measured | B2, `a48bdf0` | intake family (images); `omnimind_b2.json` |
| B2b | images not decoded: header facts | built and measured | B2 | honest family 52/55 (0 wrong) |
| B2c | WAV, AIFF, AU; MIDI; sound measurements | built and measured | B2 | intake family (sound) |
| B2d | compressed audio and video containers: header and structure | built and measured | B2 | honest family |
| B2e | meshes | built and measured | B2 | intake family (meshes) |
| B2f | maps | built and measured | B2 | intake family (geography) |
| B2g | arrays with the header checked first | built and measured | B2 | intake family; hostile (terabyte header refused); the dtype is not kept (7 "cannot") |
| C1 | the form planner | built and measured; **does not beat the references on held-out** | C, frozen `4388f4d` | [CREATIVE_INTELLIGENCE_ASSESSMENT.md](CREATIVE_INTELLIGENCE_ASSESSMENT.md) |
| C2 | `forms.alternatives` | built | C; used by P (`--alternatives`) | lane C and P tests; usefulness not measured |
| C3 | `forms.foresee` | built and measured | C; compared with outcomes by P and int2 | seconds 5-560 times too high (`omnimind_int2.json`); bytes 8 of 10 within a factor of two (`omnimind_p.json`) |
| G1a | charts | built and measured | G1, `7c02595` (G1b follow-up) | made family; `omnimind_g1.json` |
| G1b | timeline, diagrams, flow, HTML table | built and measured | G1 | made and transform families |
| G1c | documents with figures | built and measured | G1 | transform family (document to outline) |
| G1d | data files | built and measured | G1 | made family (CSV) |
| G1e | animated SVG | built | G1 | lane G1's tests |
| G1f | independent checks and bounded repair | built and measured | G1; F3 (histogram check) | 350 fresh seeds: reader and check agree (review M); since F4 (`6c2ed98`) the check reports `visible_to_a_person` (`test_f4_visible.py`, 50 tests) |
| G2a | sound | built and measured | G2, `9b31760` | made family (tone, sonified series); `omnimind_g2.json` |
| G2b | solids and surfaces | built and measured | G2; P adds `solid.mesh` | made and transform families |
| G2c | PNG and APNG | built and measured | G2 | made family (APNG) |
| G2d | explorable models, step-throughs, solid viewer | built | G2 | `tests/e2e/test_g2_explorable_e2e.py` 8 of 8 |
| G2e | checks for WAV, meshes, PNG, explorable | built and measured | G2 | 66 of 66 wrong artifacts caught (lane G2) |
| A1 | the artifact store and schema 20 | built and measured | A, `e2e940f` | `omnimind_a.json` (add about 31 ms; root scan about 11 s for 10,000 artifacts) |
| A2 | forget and restore for artifacts | built | A; F1 (matcher); F4 (the hold, `499a298`) | `test_artifact_forget.py`, `test_f4_forget_hold.py`; security review M2 and M-new-1 fixed |
| Z1 | benchmark: intake, honest, unknown, compound, hostile; baseline | built and measured | Z, `320f068` | `omnimind_baseline.json` |
| Z2 | benchmark: made and transform with independent readers | built and measured | Z; F2 (visibility rule, re-pin) | `OMNIMIND_BENCHMARK.md` |
| ZF1 | benchmark: forms with four references | built and measured | ZF, `542dfd9`; second reader | `omnimind_baseline_forms.json`, `omnimind_forms_second_reader.json` |
| ZF2 | benchmark: cross | built and measured | ZF | as above |
| ZF3 | benchmark: learn (simulated people) | built and measured | ZF | as above |
| S1-S7 | the audit's defects: gateway parts, npz, bounded reads, imagined forget, voice engines, carried settings and launch sites, web setting audit | built (each with a test that failed before) | S, `5445ea0` | ADR-070; `omnimind_s.json` |
| L1 | the production pipeline | built and measured | P, `b87d90f`; F1, F3 | made 14/14, transform 8/8 development; ADR-076 |
| L2 | cross-modal binding over artifact links | built and measured, narrowed to measurement against statement | R, `e0e056a`; F3 | cross 72/72 development, 66/6/0 held-out; ADR-077 |
| L3 | the answer path asks the planner (`output_forms`) | **open** | none | not built; an owner decision, and the planner's held-out verdict would need a new examination |
| L4 | showing artifacts in the console and Pocket | built for the console; **open** for Pocket | U, `841c3a0` | ADR-079; e2e 11 of 11 in the lane's file |
| L5 | an upload route in the console and Pocket | built for the console (off by default); **open** for Pocket | U | `test_console_artifacts.py` |
| L6 | durable production jobs | **open** | none | a production runs in the owner's command |
| L7 | learning form choices from outcomes | built and measured, off by default | L, `2d19a76`; int2 wiring `adae698` | learn held-out 0.710 / 0.656 against 0.450 / 0.454, SIMULATED; ADR-078 |
| L8 | the withheld split and one look per mechanism | built and measured: **looks taken once each on `55f89be`** | ZW, `df5c820`; F2 (driver pins); docs-2 (the rule); the main session (pins, looks) | supported: intake, honest, compound, made, transform, forms, learn; **not supported: unknown, hostile, cross** (`omnimind_looks/summary.json`) |
| L9 | optional engines (ffmpeg, Chromium) on the system's own bytes | **open** | none | an owner decision |

## Rows the later waves added

| change | outcome | lane, commit | evidence |
|---|---|---|---|
| wiring every lane into one registry; real-file reality check | built and measured | int, `c1f5f2f` | development 509/15/0/0 then (later 512/12/0/0) |
| `main` with Cognitive Genesis Wave 4 merged into `omnimind` | built | main, `1a00f50` | 7,726 passed, 0 failed |
| the pipeline records outcomes and plans through the learner (when on), relates its sources, carries units; SVGs scale to 360 px | built and measured | int2, `505b55b` | 11 productions byte-identical with learning off; [REGRESSION_REPORT.md](REGRESSION_REPORT.md) |
| decoding in a limited child process (the guard) | built and measured | F1, `5faab9a` | security review second pass: held against every probe; F4 (`c9b98bd`): `add` decodes once, in the child (5,000-file zip 5.81 to 2.97 s, crafted PNG 13.19 to 6.86 s; `omnimind_f4.json`); N-new-4 refused |
| the product decides outcomes; one outcome table; exit codes | built | F1 `d8bbfd0`, F3 `bdfebab` | `test_the_drivers_only_translate`; review M B4 fixed |
| the benchmark reads only what a person would see; drivers pinned for looks | built | F2, `4a6986e` | `READERS_SHA256`; pins file (null until the main session pins) |
| `--partial`, stated reductions, the SIMULATION record gated, CSV memory, unpack bound, approval by distribution and version | built and measured | F3, `a0c7491` | lane F3's table (30 MP PNG make 1,338 to 338 MiB) |
| the forget hold; the product check's visibility rules; the reader's two blind spots (re-pinned `df21e658...` to `fb51f10c...`); the driver's last decisions (`outcome.member_path`, `FORMS_UNMAPPED`); `add` decoding once; a reader loaded only in the parent refused; console verdict reuse and one decode at a time; the console helpers' import cycle; the MIGRATIONS copy step | built | F4, `1aa7eb6` | lane F4's report; development unchanged (512 / 12 / 0 / 0; forms, cross, learn identical) |
| the make summary after a partial read (N3), `--partial` on a large archive drawn as its folders (N4), `make` and `remake` exit codes through `outcome.of_production` (N2), plain words with no rule names or raw timings, `--why` naming the form made instead, `nsf form plan -r` (L3), the `artifact_max_bytes` help (N-new-2) | built | F5, `bf3dac6` | `tests/artifacts/test_f5_plain.py`; development unchanged (512 / 12 / 0 / 0; forms, cross, learn identical) |

## The owner's decisions, as they now stand

| decision | state | what it allows, and what it costs |
|---|---|---|
| **Merging OMNIMIND into `main`, a push, a pull request and a CI run** | open (the main session will ask after the withheld looks) | the commands become part of the product; schemas 20 and 21 are forward-only (an upgraded file cannot be opened by an older build; the way back is the owner's own copy taken before the upgrade). Wave 4 is already in `main` (`b5c8e2a`) and merged into `omnimind`. The code reviewer asks for one CI run of the check job before merging (the added test time fits only if CI is no more than about 2.2 times slower than this host) |
| **Optional packages** (Pillow, soundfile, pyarrow, PyYAML) | open | JPEG, WebP, MP3, FLAC, Parquet, full YAML; supply-chain exposure, new attack surface |
| **A model that sees or hears** | open | what a picture shows or a sound holds; binding by meaning; memory (the answer model takes about 44 GiB), a provenance and licence review, and the gateway's screen extended first |
| **External decoders on untrusted media in a sandbox** (ffmpeg, ImageMagick, vips, Tesseract, LibreOffice are on the host) | open | compressed images, audio, video frames, OCR, legacy office files; each program parses hostile input |
| **ffmpeg and Chromium on the system's own bytes** | open | video from frames it drew; PDFs of its own documents; the systemd units' `MemoryDenyWriteExecute=yes` would need a drop-in |
| **Anything automatic** (the answer path's use of the planner, turning `form_learning` to `record` or `apply`) | open; all off | answers in a planned form; learning from real reactions; the extractive answer stays the evidence of record |
| **The console view and upload** | the view is **on by default** (`console_artifacts`); upload **off** (`console_artifact_upload`); Pocket not covered | the owner may turn the view off with one command; turning upload on accepts files from the console |
| **The graph heat map's colours** | ruled by the main session under the owner's standing grayscale rule: now a gray ramp (lane U) | the owner may still overrule |
