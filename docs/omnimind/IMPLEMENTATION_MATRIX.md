# Implementation matrix (deliverable 5)

Status: 2026-11-26. Every planned change of Project OMNIMIND, prioritised: the seven fixes of lane S (item 6 has two
parts), every Wave O-0 lane's work as DESIGN §1 assigns it, and the later waves DESIGN §1 lists. Nothing in this
matrix is built yet on `main` (`8f68beb`); Wave O-0 is being built and has not been measured. The gaps named in the
second column are those of [COGNITIVE_GAP_ANALYSIS.md](COGNITIVE_GAP_ANALYSIS.md) (II.1 and so on) and the defects
of [CAPABILITY_AUDIT.md](CAPABILITY_AUDIT.md) §9 (H1, I1, C1 and so on). Benchmark families are those of lanes Z
(`intake`, `honest`, `unknown`, `compound`, `hostile`, `made`, `transform`) and ZF (`forms`, `cross`, `learn`).

## How the score is computed

The directive's formula (§15): **Cognitive Benefit × Creative Value × User Impact × Implementation Confidence ÷
(Complexity + Risk + Resource Cost)**. Each factor is a judgment from 1 to 5, made by this document and shown so that
it can be disputed:

| Factor | 1 | 5 |
|---|---|---|
| CB, cognitive benefit | changes no understanding, memory or reasoning | a whole phase of the directive depends on it |
| CV, creative value | makes no new kind of result | directly makes new kinds of result or choices between them |
| UI, user impact | the owner never notices | the owner meets it in daily use |
| IC, implementation confidence | uncertain whether it can be done well here | standard library, a precedent in this repository, a clear test |
| Cx, complexity | a few dozen lines | several thousand lines across modules |
| R, risk | touches nothing existing or security-sensitive | touches the security policy, shared storage, or the answer path |
| RC, resource cost | negligible CPU, memory and time | heavy CPU, memory or disk on a host with about 24 GiB free |

The score ranges from 0.07 (1 × 1 × 1 × 1 ÷ 15) to 208 (5 × 5 × 5 × 5 ÷ 3). It measures value per effort and risk;
it does not measure order of need, and it rewards creative value, so security fixes and benchmarks score low by
construction.

## Ranked by score

| Rank | ID | Change | Lane, wave | CB | CV | UI | IC | Cx | R | RC | Score |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | B1a | Tables: CSV/TSV, JSON records, NDJSON, XLSX and ODS (cached values, formulas not evaluated) | B1, O-0 | 5 | 4 | 5 | 4 | 3 | 2 | 1 | 66.7 |
| 2 | G1a | Charts (line, bar, scatter, histogram, heatmap, small multiples) as SVG under the reading contract; grayscale; deterministic | G1, O-0 | 4 | 5 | 5 | 4 | 4 | 2 | 1 | 57.1 |
| 3 | C1 | The form planner: `forms.plan`, the registry of forms, stated criteria, hard constraints, `decided_by`, a reason in plain words, `nsf form plan`, a bounded adjustments seam | C, O-0 | 5 | 5 | 5 | 3 | 4 | 2 | 1 | 53.6 |
| 4 | G1b | Timeline, node-link and tree diagrams, flow of steps, HTML table | G1, O-0 | 4 | 5 | 4 | 4 | 4 | 2 | 1 | 45.7 |
| 5 | G2a | Sound: tones, sequences, sonified series (16-bit PCM WAV), with a plain description of how to hear it | G2, O-0 | 3 | 5 | 3 | 4 | 2 | 1 | 1 | 45.0 |
| 6 | G1c | Report documents (HTML, Markdown) with inline figures, contents, sources and the made-by-the-system label; no script | G1, O-0 | 3 | 4 | 5 | 4 | 3 | 2 | 1 | 40.0 |
| 7 | B1b | Documents: Markdown, HTML, DOCX, ODT, PPTX/ODP, EPUB, RTF (text only), PDF through optional `pypdf` (text only) | B1, O-0 | 5 | 3 | 5 | 4 | 4 | 2 | 2 | 37.5 |
| 8 | ZF1 | Benchmark: `forms` (is the chosen form right?) with four reference choosers | ZF, O-0 | 5 | 4 | 3 | 3 | 3 | 1 | 1 | 36.0 |
| 9 | C2 | `forms.alternatives`: candidates that differ in kind | C, O-0 | 3 | 5 | 3 | 3 | 2 | 1 | 1 | 33.8 |
| 10 | B1c | Trees (XML, TOML, INI, plist), Python code through `ast` (never imported or run), other languages' surface reading, manifests, notebooks (never executed) | B1, O-0 | 4 | 3 | 4 | 4 | 3 | 2 | 1 | 32.0 |
| 11 | B2a | Rasters decoded to pixels: PNG/APNG, GIF, BMP, NetPBM, baseline TIFF, ICO; image measurements; SVG → shapes (no rendering) | B2, O-0 | 4 | 4 | 4 | 4 | 4 | 2 | 2 | 32.0 |
| 12 | B2c | Sound decoded: WAV, AIFF, AU; MIDI to a table of notes; sound measurements | B2, O-0 | 4 | 4 | 3 | 4 | 3 | 2 | 1 | 32.0 |
| 13 | K1 | The contract: representation types, errors, `Limits`, the adapter registry, the spec envelope, empty `make` and `check` registries | K, O-0 | 5 | 4 | 2 | 4 | 3 | 1 | 1 | 32.0 |
| 14 | G1f | Independent checks (SVG safety, carried information, layout, documents, data) and bounded rule-based repair | G1, O-0 | 5 | 3 | 3 | 4 | 4 | 1 | 1 | 30.0 |
| 15 | G2c | Raster PNG and animated PNG from numeric grids (own writer) | G2, O-0 | 2 | 4 | 3 | 5 | 2 | 1 | 1 | 30.0 |
| 16 | C3 | `forms.foresee`: predicted problems and cost before anything is made, labelled as estimates | C, O-0 | 4 | 3 | 3 | 3 | 2 | 1 | 1 | 27.0 |
| 17 | G1d | Data files: CSV and JSON, exact and round-trippable | G1, O-0 | 2 | 2 | 4 | 5 | 1 | 1 | 1 | 26.7 |
| 18 | L1 | Production pipeline: plan → foresee → make → check → bounded repair, keeping the spec as the editable source | later wave (O-1) | 4 | 5 | 4 | 3 | 4 | 2 | 3 | 26.7 |
| 19 | G2e | Independent checks for WAV, STL/OBJ, PNG/APNG and the explorable document | G2, O-0 | 5 | 3 | 3 | 4 | 4 | 1 | 2 | 25.7 |
| 20 | L3 | The answer path asks the planner (`output_forms` = off / offer / auto) | later wave | 5 | 5 | 5 | 2 | 4 | 4 | 2 | 25.0 |
| 21 | K2 | `detect`: signatures first, then structure, then hints | K, O-0 | 4 | 2 | 3 | 5 | 3 | 1 | 1 | 24.0 |
| 22 | G2d | Explorable models, step-throughs and a solid viewer: one self-contained HTML document with one fixed, hashed runtime and a JSON spec; an expression language parsed, never `eval` | G2, O-0 | 5 | 5 | 5 | 2 | 5 | 4 | 2 | 22.7 |
| 23 | K4 | Text, JSON and single-stream gzip/zstd/bzip2/xz adapters; shared safety helpers; `nsf inspect` | K, O-0 | 4 | 2 | 4 | 4 | 3 | 2 | 1 | 21.3 |
| 24 | G2b | Solids and surfaces (box, extrude, revolve, grid, points) as binary STL and OBJ | G2, O-0 | 3 | 4 | 2 | 4 | 3 | 1 | 1 | 19.2 |
| 25 | Z2 | Benchmark: made and transform families with independent readers | Z, O-0 | 4 | 4 | 2 | 4 | 4 | 1 | 2 | 18.3 |
| 26 | B2f | Maps: GeoJSON, GPX, KML/KMZ, WKT; counts, bounding box, track length, time span | B2, O-0 | 3 | 3 | 2 | 4 | 2 | 1 | 1 | 18.0 |
| 27 | B1d | Archives: ZIP and TAR (also inside gzip/xz/bzip2/zstd), members decoded in memory; 7z/RAR identified only | B1, O-0 | 4 | 2 | 4 | 4 | 3 | 3 | 2 | 16.0 |
| 28 | B2e | Meshes: OBJ, STL, PLY, OFF, glTF/GLB (embedded buffers only); mesh measurements | B2, O-0 | 3 | 4 | 2 | 4 | 3 | 2 | 1 | 16.0 |
| 29 | L7 | Learning form choices from outcomes from outside the system | later wave | 4 | 3 | 4 | 2 | 3 | 2 | 1 | 16.0 |
| 30 | A1 | Artifact store: sidecar bytes, the one migration (index, links, events, tombstones, gap counts), made artifacts never evidence (ADR-071), links, `nsf artifact`, three settings | A, O-0 | 5 | 3 | 4 | 3 | 5 | 4 | 3 | 15.0 |
| 31 | L4 | Showing artifacts in the console and Pocket under today's security policies | later wave | 3 | 4 | 5 | 3 | 5 | 5 | 2 | 15.0 |
| 32 | ZF2 | Benchmark: `cross` (questions needing two artifacts) | ZF, O-0 | 4 | 2 | 2 | 4 | 3 | 1 | 1 | 12.8 |
| 33 | B2g | Arrays: `.npy`/`.npz` with the header checked before numpy reads data | B2, O-0 | 3 | 2 | 2 | 5 | 2 | 2 | 1 | 12.0 |
| 34 | G1e | Declaratively animated SVG; reduced motion gives a static last frame | G1, O-0 | 2 | 4 | 3 | 3 | 3 | 2 | 1 | 12.0 |
| 35 | K3 | `inspect_unknown` and the gap hook | K, O-0 | 4 | 1 | 3 | 4 | 2 | 1 | 1 | 12.0 |
| 36 | L5 | An upload route in the console and Pocket | later wave | 4 | 2 | 5 | 3 | 3 | 5 | 2 | 12.0 |
| 37 | Z1 | Benchmark: intake, honest, unknown, compound and hostile families; frozen splits; the baseline | Z, O-0 | 5 | 2 | 2 | 4 | 4 | 1 | 2 | 11.4 |
| 38 | L2 | Cross-modal binding and reconciliation over artifact links | later wave | 5 | 3 | 3 | 2 | 5 | 2 | 2 | 10.0 |
| 39 | L9 | Optional engines on the system's own bytes: ffmpeg to encode frames it made, Chromium to render its own documents | later wave | 2 | 5 | 4 | 3 | 4 | 4 | 4 | 10.0 |
| 40 | ZF3 | Benchmark: `learn` (simulated people's feedback) | ZF, O-0 | 4 | 2 | 2 | 3 | 3 | 1 | 1 | 9.6 |
| 41 | B2b | Images not decoded (JPEG, WebP, HEIC/AVIF, PSD, compressed TIFF, DICOM, FITS): header facts, then `Unsupported` | B2, O-0 | 3 | 1 | 3 | 4 | 2 | 2 | 1 | 7.2 |
| 42 | B1e | SQLite: header facts, schema, counts and a bounded sample through a sealed read-only connection (or header only) | B1, O-0 | 3 | 2 | 3 | 3 | 3 | 4 | 2 | 6.0 |
| 43 | B2d | Compressed audio and video containers (MP3, AAC/M4A, FLAC, Ogg, MP4/MOV, Matroska/WebM, AVI): header facts, then `Unsupported` | B2, O-0 | 3 | 1 | 3 | 4 | 3 | 2 | 1 | 6.0 |
| 44 | L6 | Durable production jobs (progress, cancel, resume, budget) | later wave | 3 | 2 | 3 | 3 | 4 | 3 | 2 | 6.0 |
| 45 | S1 | Gateway refuses any message part it cannot both count and screen | S, O-0 | 2 | 1 | 2 | 5 | 1 | 2 | 1 | 5.0 |
| 46 | L8 | The withheld split (Zw) and one look per mechanism | later wave | 4 | 1 | 1 | 4 | 2 | 1 | 1 | 4.0 |
| 47 | A2 | Forget and restore for artifacts: by artifact (transitively through what was made from it) and by text; sidecar reconciliation | A, O-0 | 4 | 1 | 3 | 3 | 4 | 4 | 2 | 3.6 |
| 48 | S6b | A scan of every thread, timer, process and loop start in product code, each classified in one list | S, O-0 | 3 | 1 | 2 | 3 | 3 | 1 | 1 | 3.6 |
| 49 | S4 | Forget removes imagined records whose payload names the forgotten text, also inside base64 and zlib/gzip values; restore replays it | S, O-0 | 3 | 1 | 3 | 3 | 3 | 3 | 2 | 3.4 |
| 50 | S6a | A test that `CARRIED_SETTINGS` and the settable settings agree, or every difference is named | S, O-0 | 2 | 1 | 1 | 5 | 1 | 1 | 1 | 3.3 |
| 51 | S2 | `.npz` input checked from the zip directory and npy headers before numpy loads it | S, O-0 | 2 | 1 | 2 | 4 | 2 | 2 | 1 | 3.2 |
| 52 | S3 | Bounded reads of regular files only; malformed files give the command's named error | S, O-0 | 1 | 1 | 3 | 5 | 2 | 2 | 1 | 3.0 |
| 53 | S7 | A web setting change writes an audit row (who, when, which key; never a value) | S, O-0 | 1 | 1 | 2 | 5 | 1 | 2 | 1 | 2.5 |
| 54 | S5 | Voice engines get a response size cap and the gateway's port-owner check | S, O-0 | 1 | 1 | 2 | 4 | 2 | 2 | 1 | 1.6 |

### Why the build order differs from the score order

The build order is: **lane S first; the kernel (K) and both benchmarks (Z, ZF) next; the store (A) and every adapter,
planner and generator lane (B1, B2, C, G1, G2) in parallel after lane K's first commit; later waves only after Wave
O-0 is merged and measured.**

- **Lane S** scores lowest (1.6 to 5.0) and goes first. Its defects are in code that exists today, and two of them
  (H1, C1) become exploitable exactly when richer content starts to flow: a non-text message part would bypass the
  privacy screen, and a forgotten name inside an encoded payload would survive forget. Directive §16 says not to
  remove security controls for capability; closing known holes before adding surface is the same rule.
- **The benchmarks (Z, ZF)** score in the middle and come before the mechanisms they measure, because everything is
  measured by them: a benchmark written after the mechanism tends to be written to it. The rule comes from Cognitive
  Genesis ([../genesis/README.md](../genesis/README.md), "No self-grading gate").
- **The kernel (K1)** ranks 13th and comes first among the product lanes, because every other lane is built on its
  types, registry and spec envelope; a mistake there costs every lane.
- **The store (A1, A2)** ranks 30th and 47th but is needed before anything is kept: without it there is no forget,
  restore or provenance for artifacts, and the hard rules forbid storing what cannot be forgotten.
- **The answer path's use of the planner (L3)** ranks 20th and is deliberately last: it is the only change that could
  alter what the owner is told, it is the owner's decision, and it needs the planner measured first.

## Details

| ID | Gap it closes | Depends on | Risks | Acceptance criterion (it can fail) | Setting (default) | Rollback |
|---|---|---|---|---|---|---|
| S1 | audit H1 (= I6): non-text parts skip the token estimate and the privacy screen | — | refusing a text-only list by mistake | a test sends a message with an `image_url` part: a named error before anything is sent or written to the ledger; every existing `tests/inference` test passes unchanged | none | revert the lane's commits; honest inputs behave as before either way |
| S2 | audit I1, C7 | — | refusing an honest file | a crafted header and a compressed bomb are refused with a named error and no MemoryError; honest `.npz` files give byte-identical results | none | revert the lane's commits; honest inputs behave as before either way |
| S3 | audit I2, I12 | — | a size bound below an honest file | a FIFO, a device and an oversized file are refused; a truncated archive gives a one-line error, no traceback; honest outputs byte-identical | none | revert the lane's commits; honest inputs behave as before either way |
| S4 | audit C1 | — | cost of the scan inside the write transaction; over-deletion | erasure canaries seed the imagined store with plain, base64 and compressed copies: none survives forget, nor a restore of an archive taken before it; cost measured at 10 000 records | none | revert the lane's commits; honest inputs behave as before either way |
| S5 | audit H2 | — | a cap below a real reply | a fake server that sends too much, and a stranger holding the port, are both refused with a named error before the key or audio is sent | none | revert the lane's commits; honest inputs behave as before either way |
| S6a | audit C6 | — | none | the test fails when a settable setting is added without a carried entry or a stated reason | none | revert the lane's commits; honest inputs behave as before either way |
| S6b | audit C5 (= H10) | — | false positives that train people to add entries blindly | a planted unclassified launch site makes the test fail; every existing site is classified with a reason | none | revert the lane's commits; honest inputs behave as before either way |
| S7 | audit C9 | — | a value that could be secret leaking into the row | a setting change writes one row in the same transaction; no row contains the value; readers of the log unchanged | none | revert the lane's commits; honest inputs behave as before either way |
| K1 | II.1, III.1 (no types for anything but sentences) | — | a contract that is wrong for the adapter lanes; every lane builds on it | for every class, `describe()` stays ≤ 32 KiB serialised and JSON-able on adversarial content, `shape()` is defined; `catalog()` lists exactly what is registered | none | revert the merge; the package is new, nothing existing imports it, no data is written |
| K2 | II.2 | K1 | a false identification | every table entry tested with hand-written bytes; conflicting hints resolve to the signature; false identifications on random and adversarial prefixes = 0 (`omnimind_k.json`); truncations to 1, 4, 16 bytes never raise | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| K3 | II.2 (the capability-discovery answer) | K1 | a candidate read as a fact | benchmark `unknown`: a truly unknown input never gets a format named as fact (WRONG = 0) | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| K4 | II.1, II.3 | K1 | a decompression bomb | ratio and size refused before allocation; benchmark `hostile` bombs refused within the harness's time and memory bound; `nsf inspect` on a FIFO refused | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| Z1 | §14 (no measure of input understanding exists) | — (codes against DESIGN, imports no product code) | a benchmark that shares code with the product and grades it kindly | a test fails if a frozen task changes; every generator checked against expectations written in the test; baseline `omnimind_baseline.json` committed | none | remove the benchmark package; product code never imports it |
| Z2 | §14, §11 (no measure of made artifacts) | — | a reader that echoes embedded data | each reader catches the deliberately wrong references (scale off by 10 %, wrong pitch, inverted face) and passes the right ones | none | remove the benchmark package; product code never imports it |
| ZF1 | §7, §14 (no measure of form choice) | — | cases decidable from cue words alone | ≥ 240 cases; ≥ 30 same-words pairs with different answers; a subject returning only `bad` forms scores 0, one returning `good` everywhere scores 1 | none | remove the benchmark package; product code never imports it |
| ZF2 | III.2 | — | twins that differ in more than one value | the always-"consistent" subject scores exactly 0.5; today's baseline is "cannot" on every case | none | remove the benchmark package; product code never imports it |
| ZF3 | VII.1 | — | reading simulated adaptation as benefit to a person | the reference learner and the oracle are separated by more than the spread over seeds; every number labelled SIMULATED | none | remove the benchmark package; product code never imports it |
| A1 | II.1, III.3 | K1 | the migration cannot be undone by an older build; a made artifact reaching an evidence-side table; bytes kept after forget | the reference-column scan refuses an artifact id in every evidence-side table; caps refused with no partial write; hash verified on every read; costs at 10 000 artifacts and 2 GiB in `omnimind_a.json` | `artifact_max_bytes` 64 MiB; `artifact_store_max_bytes` 2 GiB; `artifact_adapters` [] (none approved) | forward-only: an older build refuses a schema-20 file (`instance/schema.py:948-950`). Keep the new build with the tables unused, or restore a checkpoint taken before the upgrade; `nsf artifact forget` empties the sidecar |
| A2 | III.3; contracts C5, C7 | A1 | a forgotten name surviving in a compressed member; a long scan holding the write lock | seed-and-scan tests: a root named only inside a compressed member of a stored archive is found and removed; a restore of an older archive replays the forget; the scan runs outside the transaction | as A1 | as A1 |
| B1a | II.3 | K1, K4 | a guessed delimiter or decimal comma producing wrong numbers | benchmark `intake` (tables): WRONG = 0 and every task of a format listed in `catalog()` answered right on held-out | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| B1b | II.3; §5.2 documents | K1, K4 | a slow or hostile PDF that cannot be stopped | benchmark `intake` (documents) as B1a; a hostile PDF bounded by input size or deadline as the lane states; without `pypdf`, `Unsupported` with `needs` | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| B1c | II.3; §5.2 code | K1, K4 | a parser that executes something | benchmark `intake` (code, notebooks, trees) as B1a; a test proves no module was imported and no cell run | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| B1d | II.3; §5.1 recursive inspection | K1, K4 | path traversal; bombs; encrypted members | benchmark `hostile`: `../`, absolute and link members refused as members to decode, bombs refused, nothing written outside the scratch directory; `compound` questions answered from the right member | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| B1e | II.3; §5.2 databases | K1, K4 | a crafted database that runs code or hangs | a database with triggers, views and a hostile schema yields facts and runs nothing; the deadline stops a slow query | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| B2a | II.3; §5.2 images (decodable part) | K1, K4 | allocation from declared dimensions | benchmark `intake` (images) as B1a; a PNG declaring 100 000 × 100 000 refused before allocation | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| B2b | II.4 | K1 | a pixel value invented | benchmark `honest`: header facts right; every content question "cannot"; any content answer counts as WRONG and must be 0 | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| B2c | II.3; §5.2 audio (decodable part) | K1, K4 | a `data` size that lies | benchmark `intake` (sound) as B1a: duration, dominant frequency and onset count within tolerance | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| B2d | II.4 | K1 | a duration guessed rather than read | benchmark `honest` as B2b | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| B2e | II.3; §5.2 spatial | K1, K4 | an external URI fetched | benchmark `intake` (meshes) as B1a: face count, volume, watertightness right | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| B2f | II.3; §5.2 geospatial | K1, K4 | a coordinate order mistake | benchmark `intake` (geographic) as B1a: bounding boxes and counts right | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| B2g | II.3; §5.2 scientific | K1, K4 | an object dtype or terabyte header | benchmark `hostile` terabyte header refused; `intake` arrays right | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| C1 | IV.1 | K1 (`ContentShape`), `make.catalog()` | a planner that is a list of cue words; too much media for a simple question | benchmark `forms` held-out: hard-constraint violations 0; explicit feasible requests honoured in every case; share `good` above the "content shape only" reference; too-much share below the "richest feasible" reference | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| C2 | V.2 (divergence and convergence) | C1 | near-identical variants | a test: two charts of one family never count as different; alternatives are ranked and each is feasible | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| C3 | VI | C1 | estimates read as facts | for the benchmark's `made` specs, each predicted problem is later found or not found by the validators; the share is recorded (Wave O-1 compares) | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| G1a | V.1; §8.3 visual | K1 (spec envelope), `inference/jsonschema.py` | a chart that misleads (truncated axis, overlapping labels) | benchmark `made`: the independent reader recovers every source value within tolerance on held-out specs within the form's stated capacity; two makes give the same bytes; no hue outside warning marks | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| G1b | V.1 | K1 | layouts that hide nodes or edges | benchmark `made`: node and edge sets, event order and table cells recovered exactly | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| G1c | V.1; §8.3 documents | G1a, G1b | a placeholder or broken reference shipped | G1's document check: heading levels, references, no placeholder, label present; benchmark `transform` document → outline | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| G1d | V.1 | K1 | precision lost | parse back and compare with the source exactly; benchmark `made` data file | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| G1e | V.1 (animation without video) | G1a | motion for its own sake | the check finds no script; reduced-motion rule present; frames recoverable | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| G1f | VIII | K1 | a check that shares code with the generator | a test fails if `check` imports `make`; every deliberately wrong artifact caught (scale off by 10 %, swapped series, clipped label, script, green fill, missing title) and right ones pass | none | revert the merge; the package is new, nothing existing imports it, no data is written |
| G2a | V.1; §8.3 audio | K1 | clicks, clipping, a pitch scale that misleads | benchmark `made`: FFT peak per segment back to the source values within tolerance; G2's check catches a wrong pitch, a clipped file and a truncated `data` chunk | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| G2b | V.1; §8.3 spatial | K1 | a non-watertight solid | benchmark `made` and `transform`: volume, bounds and watertightness recovered; an inverted face, a missing face and a scaled copy caught | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| G2c | V.1 | K1 | a gray ramp that loses values | pixels map back through the stated ramp to the grid within tolerance; frame count and delays right | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| G2d | V.1, VI; §8.3 interactive | K1 | script in an artifact (the largest attack surface of the wave); disagreement between the two evaluators | the static check finds exactly the known script and style hashes and the exact CSP meta, no URL; the Python and ECMAScript evaluators agree on a few thousand generated expressions (under node); where Chromium exists, no console error and keyboard-reachable sliders | none (owner command only; nothing calls it automatically) | revert the merge; the package is new, nothing existing imports it, no data is written |
| G2e | VIII | K1 | a browser check deciding alone | every deliberately wrong artifact caught; without Chromium the verdict says "not run in a browser" and the static checks decide | none | revert the merge; the package is new, nothing existing imports it, no data is written |
| L1 | V.3 | Wave O-0 merged and measured | loops that do not end; repairs that change data | on benchmark `made`/`transform` held-out, repaired artifacts pass checks no less often than unrepaired; repair rounds ≤ 3; data never changed | proposed: off | turn the setting off |
| L2 | III.2 | A1, B1, B2 | a link asserted without a basis | benchmark `cross` held-out above 0.5 with both twins of a pair right; WRONG reported separately | proposed: off | turn the setting off |
| L3 | IV.1, IV.2 | C1, L4 | elaborate media where a sentence serves; the evidence of record changing | with `off`, every existing output byte-identical (offline digest, guard differential 0 different); `offer` and `auto` measured on `forms` held-out before any default changes | `output_forms` off | set `off` |
| L4 | X | G1, G2, A1 | an injection surface; CSP weakened; another family's artifact shown | the existing header tests unchanged; e2e: Presence stays the hero, axe-clean, light and dark, keyboard; a family never receives another's artifact | proposed: off | turn the setting off |
| L5 | II.1 | A1 | body limits and parsing on an authenticated route | size and type refused before reading the body; header tests unchanged; forget and family scope proven | proposed: off | turn the setting off |
| L6 | II.5, IX | L1 | a job running while quiet is on | registered in `background.ACTIVITIES`; quiet holds it; a killed job resumes from its checkpoint | proposed: off | turn the setting off |
| L7 | VII.1 | C1 seam, L3, ZF3 | learning from the system's own opinion | benchmark `learn` held-out: gain above the spread over seeds and above the per-key majority reference; labelled SIMULATED | proposed: off | turn off; adjustments are bounded and reversible |
| L8 | §14 (withheld evaluation sets) | Z1, Z2 | a look taken twice | the rule is committed before the look; the look is taken once | none | remove the benchmark package; product code never imports it |
| L9 | V.1 (video), VIII (rendered previews) | G1, G2 | a sandbox escape; memory pressure on the host | fixed argument lists, no shell, no network; degrades with a named reason when absent; never on untrusted input | proposed: off | turn the setting off |

Acceptance criteria are written so that they can fail. Where a lane's brief sets a test, the criterion is that test;
where it sets no number, the number here (for example "WRONG = 0 and every task of a format listed in `catalog()`
answered right on held-out") is this document's proposal, to be confirmed when the benchmarks exist.

Settings: in Wave O-0 nothing runs unless the owner types a command (`nsf inspect`, `nsf artifact …`,
`nsf form plan`); the only new settings are lane A's three limits. Every later-wave change is proposed behind a
setting that is off by default. With every setting at its default, every existing output must stay byte-identical:
`scripts/bench_wave1.py offline` must reproduce the digest in `docs/quality/evidence/offline_deterministic_aarch64.json`,
`scripts/reproduce.py` must pass, and `scripts/guard_differential.py compare` must report 0 different.

## Decisions that are the owner's

| Decision | What it would allow | What it costs or risks |
|---|---|---|
| **Merging OMNIMIND waves into `main`** | the commands and modules of Wave O-0 become part of the product | lane A's migration takes an individual's file to schema 20, and an older build refuses a newer file (`instance/schema.py:948-950`): going back means restoring a checkpoint taken before the upgrade. The Cognitive Genesis Wave 4 branch is also unmerged; the order of the two merges is the owner's |
| **Optional packages** (for example Pillow, soundfile, pyarrow, PyYAML) | decoding JPEG, WebP, MP3, FLAC, Parquet, YAML; each would be a new, pinned, optional extra | supply-chain exposure; CI installs; every decoder is new attack surface. Nothing may be installed without the owner |
| **A model that sees or hears** (a vision-language or audio model) | describing what an image shows or a sound contains; binding by meaning across kinds | memory: the answer model already takes about 44 GiB and about 24 GiB is free; the gateway refuses non-text parts (lane S) until its token estimate and privacy screen are extended; a new model's provenance and licence |
| **Decoding untrusted media through an external decoder** (ffmpeg, ImageMagick, vips, Tesseract, LibreOffice, already on the host) | pixels of compressed images, samples of compressed audio, video frames, OCR, legacy office files | each program parses hostile input; it needs a sandbox with no network, bounded memory and time, fixed argument lists and no shell. Not done until the owner says so (DESIGN §8) |
| **Using ffmpeg and Chromium on the system's own bytes** (L9) | video made from frames the system drew; rendered previews and PDFs of its own documents | lower risk than the decision above (trusted input), but the installed systemd units would need a drop-in (`MemoryDenyWriteExecute=yes` kills both) |
| **Turning on anything automatic in the answer path** (L3 `offer`/`auto`, L7 learning, unasked "spontaneous" forms) | answers in the form the planner chooses | elaborate media where a sentence serves; the extractive answer must stay the evidence of record; measured on held-out `forms` cases first |
| **Showing artifacts and accepting uploads** (L4, L5) | the console and Pocket display charts, sounds and explorable models; files dropped in directly | any change to a Content-Security-Policy or to body limits is a security change; DESIGN §8 plans to need none, and the e2e and header tests must stay green |
| **The graph heatmap's colours** (audit §9, O7) | — | white, blue and rust are hard-coded: against the grayscale rule and the dark theme. Whether that is acceptable data encoding is the owner's call |
