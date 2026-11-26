# OMNIMIND benchmark: taking things in and making things

The examination for Project OMNIMIND, written **before** any OMNIMIND mechanism exists (lane Z, from `omnimind` =
main `8f68beb`), and the **baseline of the unchanged system**. Every later lane (detection and adapters, the store,
the form planner, generators and validators) is measured here; nothing here helps a lane pass except by being right.

- Code: `src/neuroscifi/acceptance/omni/` (one module per family; each module's docstring has its design).
- Command: `scripts/bench_omni.py`.
- Case tables, frozen: `acceptance/omni/cases.py`; `tests/acceptance/test_omni_bench.py` pins its SHA-256 and every
  family's task digest per split.
- Baseline numbers: `docs/quality/evidence/omnimind_baseline.json` (89,222 bytes; measured at `23b5e50`).
- Decision record: ADR-075 in `docs/architecture/ARCHITECTURAL_DECISIONS.md` (lane Z's section).

Everything is synthetic and offline: every file is assembled by the benchmark's own encoders from values its
generator chose; no model is called; nothing is fetched. Nothing here measures real-world files (a camera's JPEG,
a phone's recording, a CAD export): it measures whether the system reads and makes files whose content is known
exactly.

## How to run

```bash
PY="PYTHONPATH=$PWD/src .venv/bin/python"
$PY scripts/bench_omni.py list                                          # families and the measurement vocabulary
$PY scripts/bench_omni.py run --split development --subject artifacts --out /tmp/omni.json
$PY scripts/bench_omni.py run --split held_out --subject artifacts --family intake --out /tmp/intake.json
$PY scripts/bench_omni.py run --split both --subject today --out docs/quality/evidence/omnimind_baseline.json \
    --evidence --scratch /some/scratch/dir                              # how the baseline was recorded
$PY scripts/bench_omni.py hash                                          # the frozen hashes
```

`run` refuses to write inside the repository unless `--evidence` is given. A full run of both splits takes under a minute
on the DGX Spark (the hostile family's process per task is most of it). The tests:
`tests/acceptance/test_omni_bench.py`, `test_omni_readers.py`, `test_omni_families.py`, `test_omni_driver.py`
(about 10 seconds together).

## The families

| Family | What it asks | Development | Held-out |
|---|---|---:|---:|
| `intake` | one modality at a time: tables (CSV with quoting, `;` and decimal commas, missing cells; TSV; JSON records; NDJSON; XLSX with shared strings, numbers, dates, two sheets), documents (Markdown, HTML, DOCX, ODT, EPUB, a hand-built PDF), images (PNG gray/RGB/RGBA/palette+tRNS at 2/4/8/16 bit, interlaced; PGM/PPM binary and ASCII; BMP 24/32; GIF with frames; SVG), sound (WAV 8/16/24 bit, mono/stereo, extensible header), meshes (OBJ, ASCII/binary STL, ASCII/binary PLY: cube, tetrahedron, open box), geography (GeoJSON, GPX), trees (JSON, XML), code (a module, a package in a zip), notebooks, arrays (npy in four layouts, npz), archives (zip, tar, tar.gz, a gzipped CSV) | 337 | 339 |
| `honest` | formats nothing here decodes by design (JPEG, WebP, TIFF, MP3, FLAC, Ogg Vorbis, MP4, WebM, Parquet, HDF5, .doc, .xls): identification and header facts are answerable; content is not | 55 | 55 |
| `unknown` | decodable files in disguise (wrong or no extension, a lying declared type, BOM, UTF-16, gzip, zstd, both, unusual valid variants) and truly unknown bytes (random, invented record formats, an invented sealed container, zeros, a buried signature, the first bytes of a real format) | 51 | 49 |
| `compound` | the question needs the right member: a zip of CSV + PNG + a report referring to both; a DOCX with an embedded image and a table; XLSX with 3-4 sheets; an archive in an archive; a DOCX, notebook or WAV inside a zip | 36 | 40 |
| `hostile` | bombs, lies, traversal, links, truncation, depth, a 50-60 MB field, a PDF that is a ZIP, instructions aimed at an AI agent; each task in its own process under bounds | 23 | 23 |
| `made` | make something from a request: line, bar (with warned values), scatter, histogram, timeline, node-link diagram, tone, sonified series, STL solid, OBJ surface, APNG animation, CSV data file | 14 | 14 |
| `transform` | several steps: table -> chart -> values; table -> sound -> order; OBJ/PLY -> STL -> volume; document -> outline diagram -> headings in order; archive -> index -> every member | 8 | 8 |
| **all** | | **524** | **528** |

A task is one question about one set of files. Questions are MEASUREMENTS from a fixed vocabulary
(`subject.MEASURES`, printed by `bench_omni.py list`): `detect.family`, `detect.format`, `table.n_rows`,
`table.column_mean:<name>`, `table.cell:<r>,<c>`, `doc.headings`, `doc.table_cell:<t>,<r>,<c>`, `image.size`,
`image.pixel:<x>,<y>`, `image.mean_gray`, `audio.duration_s`, `audio.dominant_hz`, `audio.n_onsets`,
`mesh.volume`, `mesh.watertight`, `geo.bbox`, `tree.value:<path>`, `code.calls:<f>`, `bundle.members`, ... A
question may name a member first: `data/values.csv::table.n_rows`, nested as deep as needed. Scoring is exact
comparison within a stated tolerance (`omni.same`), never a judgment.

## The three outcomes, and the floor

Per task, reported with its denominator in every summary:

- **right**: the expected value within tolerance, or the expected abstention.
- **abstained**: an honest "cannot" or "refused" where an answer was expected. No credit, no harm.
- **wrong**: a false answer: a value outside tolerance, or any answer where only an abstention is right (content of
  a format nothing here decodes, a hostile input, the format of random bytes), or a made artifact that misstates its
  source. **This is fabrication, the worst outcome; it is reported on its own (`fabrication`) and never averaged
  away.**
- **failed** (neither of the three): a crash (an unnamed exception: a traceback to the owner), a hang, memory past
  the bound, a file or link written outside the task's directory; for made artifacts also unreadable by the reading
  contract, unsafe, not deterministic, or the palette broken.

`abstention_floor` is the number of tasks whose right answer is an abstention: a subject that says "cannot" to
everything scores exactly that. **Read `right` against the floor.** Today the floor is 52 of 524 (development) and
52 of 528 (held-out): the 20 honest content questions, the 9 truly-unknown content questions and the 23 hostile
tasks (where only a refusal is right, and "cannot" counts as abstained, not right).

Family-specific measures beside the outcomes: `unknown` reports `missed_decodable` (disguised files answered
"cannot") and `false_identification` (a format named for bytes that are not it); `hostile` reports `safe` (right
plus abstained) and the peak traced memory; `made` and `transform` report `carried_mean` (the share of source values
recovered, the central measure of a made artifact), how many were deterministic, and how many had problems;
`honest` splits header facts from content.

## The subjects

- **`today`**: what exists on `main`. A file reaches today's system only as a web response, so a document is handed
  to `webcortex.extract.extract` as a server would send it with the task's declared type, then through
  `safety.quarantine` as the orchestrator does; a type the fetcher does not accept (`webcortex.net.CONTENT_TYPES`:
  no Markdown, no CSV, no office documents) is "cannot", as its `unsupported_type` would be. Arrays reach it through
  `nsf graphfile pack` (an `.npy`, `np.load(allow_pickle=False)`, uncaught errors) and `nsf epoch stream` (an `.npz`
  holding exactly `states` and `targets`, errors named). Everything else is "cannot": no detection, no file intake,
  nothing that makes an artifact.
- **`artifacts`**: written against DESIGN.md §2/§3/§6 (`adapters.decode`, `make.make`), imported lazily; while the
  package is absent every answer is "cannot: not built". `tests/acceptance/test_omni_driver.py` runs it against a
  stand-in package written from DESIGN §2 to prove that a correct package is scored right through it.

The driver reads each measurement off the returned representation with its own definition (e.g. `image.mean_gray`
is its own Rec. 709 luminance over the returned pixels divided by `2**bits - 1`). Where DESIGN leaves a
representation open, the driver accepts every reading DESIGN allows and says "cannot" otherwise: an array is a
`numpy.ndarray` or an object with an `array`/`values`/`data` array (DESIGN §2 names no array class); JSON maps to
`Node`s with children named by key or index; an XLSX may be a `Bundle` of sheet `Table`s, one `Table` with a title,
or a `Document` of table blocks; a JSON array of objects may be a `Table` or a `Tree` of records; a `.gz`/`.zst`
`Bundle` of one member stands for that member.

## The baseline (today's system, measured at `23b5e50`)

| Split | Family | n | right | floor | abstained | wrong | failed |
|---|---|---:|---:|---:|---:|---:|---:|
| development | intake | 337 | 25 | 0 | 312 | 0 | 0 |
| development | honest | 55 | 20 | 20 | 35 | 0 | 0 |
| development | unknown | 51 | 9 | 9 | 42 | 0 | 0 |
| development | compound | 36 | 0 | 0 | 36 | 0 | 0 |
| development | hostile | 23 | 1 | 23 | 20 | 1 | 1 |
| development | made | 14 | 0 | 0 | 14 | 0 | 0 |
| development | transform | 8 | 0 | 0 | 8 | 0 | 0 |
| development | **all** | **524** | **55** | **52** | **467** | **1** | **1** |
| held-out | intake | 339 | 25 | 0 | 314 | 0 | 0 |
| held-out | honest | 55 | 20 | 20 | 35 | 0 | 0 |
| held-out | unknown | 49 | 9 | 9 | 40 | 0 | 0 |
| held-out | compound | 40 | 0 | 0 | 40 | 0 | 0 |
| held-out | hostile | 23 | 1 | 23 | 20 | 1 | 1 |
| held-out | made | 14 | 0 | 0 | 14 | 0 | 0 |
| held-out | transform | 8 | 0 | 0 | 8 | 0 | 0 |
| held-out | **all** | **528** | **55** | **52** | **471** | **1** | **1** |

Read plainly: today's system takes in almost nothing. Its 25 intake answers per split are an HTML page's title,
headings and a sentence, a hand-built PDF's pages, title and sentences, and `.npy`/`.npz` arrays through the two
commands that load them; every other intake question (tables, office documents, images, sound, meshes,
geography, trees, code, notebooks, archives) is "cannot". It identifies nothing (`missed_decodable` 33 of 33 and
31 of 31). It makes nothing. Its 20 + 9 honest/unknown "right" are the floor (abstentions), not capability. On
hostile inputs it refuses the instruction-carrying HTML (development) and PDF (held-out) through `quarantine`,
abstains on 20, **answers a question about a PDF that is also a ZIP** (the one fabrication-class outcome: it does
not notice the second identity), and **crashes with a MemoryError on an `.npy` header declaring terabytes**
(`nsf graphfile pack` does not catch it; the traced peak recorded beside that task is the attempted allocation,
8 TB, not memory used). The `artifacts` subject today answers "cannot: not built" everywhere: 29 right per split,
exactly the honest and unknown abstention tasks, and 0 of 23 hostile (a "cannot" is not a refusal).

## The readers of made artifacts (and what DESIGN.md does not fix)

`acceptance/omni/readers.py` recovers content from bytes the way a person reads it, following DESIGN §6's reading
contract, and never reads `<metadata>`:

- **SVG charts**: one `<g class="plot">`; `<g class="axis x">`/`<g class="axis y">` with `<g class="tick"
  transform="translate(X,Y)">` and a `<text>` value (decimal number, ISO 8601 date, or category label). Scales are
  fitted from the ticks (linear or log10, residual under 0.1 % of the span, or categories by position); transforms
  are composed (translate, scale, matrix, rotate). Marks: `circle.mark`, `rect.mark` (a bar's value is its edge away
  from zero, or from the axis minimum when zero is outside it; histograms keep both x edges), `polyline.series`
  inside `<g class="series" data-name="...">`. Tolerance: 1 % of the data's range (a 10 % scale error is caught).
- **Diagrams** (DESIGN fixes no contract for them; this is the benchmark's reading, to be ruled on): nodes are
  `<g class="node">` with their label as text and their position at the first circle/ellipse/rect inside (else the
  group's origin); edges are elements of class `edge` (`line`, `polyline`, or a `path` of M/L/H/V/C/Q commands)
  whose two ends attach to the nearest nodes. Reading order of an outline: depth-first from the node no edge points
  to, siblings by position across the direction the tree grows.
- **Timelines**: read as a chart: an x axis of ISO dates, one `circle.mark` per event.
- **Warnings and the palette**: hue is allowed only on elements carrying (or inside) a class `warn` or `danger`, on
  at most as many marks as the request has warned values, and in style rules whose selector names warn or danger;
  `var(...)` colours are judged where defined. Grays: `#rgb`/`#rrggbb` with equal channels, `rgb()` with equal
  channels, `hsl()` with zero saturation, gray named colours, `currentColor`, `none`.
- **Safety**: allowed elements only (no `script`, `foreignObject`, `image`, `a`, ...), no `on*` attribute, no
  `href` or `url()` that does not start with `#`, no `javascript:`, no `@import`, no DTD.
- **Sound**: RIFF parsed by hand (integer PCM 8/16/24/32, extensible); a sonified series is trimmed of leading and
  trailing silence, cut into as many equal tones as the request has values, and each tone's pitch (Hann window,
  FFT, interpolated peak over the central 60 %) is mapped back linearly between the request's `low_hz`/`high_hz`
  and its `domain`; tolerance 2 % of the domain. A tone: 1 % on pitch, 1 % (at least 10 ms) on duration.
- **Meshes**: binary STL recognised by its size (84 + 50 n bytes, whatever its header says), ASCII STL, OBJ (fan-
  triangulated polygons, negative indices), ASCII PLY; vertices welded by exact coordinates; signed volume,
  area, bounds; watertight = every undirected edge shared by exactly two triangles; oriented = every directed edge
  used once; STL normals must agree with the winding; volume must be positive (outward).
- **PNG/APNG**: chunks with CRCs, all five filters, Adam7, bit depths 1-16, palettes and tRNS, `acTL`/`fcTL`/`fdAT`
  with dispose and blend; pixels exact; delays within 1 ms; all frames gray unless warned.
- **CSV**: header and every cell (numbers within 1e-9 relative, strings exact).

The benchmark's **requests** (`request.json` of a made task) are its own neutral format (form and plain lists).
`subject._spec` turns one into DESIGN §6's envelope (`spec`, `version`, `title`, `description`, `data`, `options`,
`sources`, `made_by`) with form names from `subject.FORM_NAMES`; that mapping, and the transform chaining in
`ArtifactsSubject._transform`, are the only places a later lane adjusts to the schemas lanes G1/G2 settle on. The
requests, the truths and the readers stay frozen.

## Hostile bounds

Each hostile task runs in its own process (`isolate.py`) with an address-space cap of 6 GiB (a net), working
directory, HOME and TMPDIR inside its own scratch folder, and is killed after 60 s (a hang). It is `failed` when
its peak traced Python allocation (tracemalloc, numpy included) passes 384 MiB (DESIGN's `max_decoded_bytes` is
256 MiB), its own work passes 25 s (DESIGN's `max_seconds` is 20), it crashes, or a file or link appears outside its
working directory (the traversal targets each task names, anything new in the scratch root, any symbolic link).
Probe subjects (`probe:hang`, `probe:crash`, `probe:allocate`, `probe:escape`) prove each detection in the tests.
Only a refusal is right; "cannot" is safe but abstained; any answer is wrong.

## Freezing and the splits

- `development` (tune on it) and `held_out` (report on it, never choose with it) are separately written case tables
  with disjoint seed ranges (3100-3599 and 3600-4099), other file names, sizes, parameters and variants.
- `test_task_definitions_are_frozen` pins `cases.py`'s SHA-256 and, per family and split, the number of tasks and a
  SHA-256 over every task's digest (id, question, truth, tolerance, kind, declared types, each file's SHA-256). A
  changed generator changes the bytes and fails the test. New tasks go in a new file, never in place.
- The encoders write their own deflate, zlib, gzip, zip and zstd framing, so the bytes do not depend on the host's
  zlib. They do depend on numpy's `default_rng` streams; a numpy upgrade that changed them would fail the freeze
  test, and that must be reported, not re-pinned silently.

## The withheld split: rules for the lane that writes it and for everyone else

1. A separate lane writes `src/neuroscifi/acceptance/omni/withheld_*.py`: its own case tables and seeds from
   9100-9599 (`omni.WITHHELD_SEEDS`), new file names and variants, written without reading any product lane's code
   and committed before any mechanism is final. Its SHA-256 is pinned in the same commit.
2. Nobody else opens, prints, imports or runs a file whose name contains `withheld` (COMMON.md); nothing in this
   package imports it, and `bench_omni.py` has no subcommand that runs it.
3. One look per mechanism, at the end, by the main session, after a rule is committed BEFORE the look that says
   what passes (for example: right on withheld within 5 points of held-out, fabrication not above held-out's,
   no new failed task). Each look is appended to a ledger (`docs/quality/evidence/omnimind_withheld_uses.jsonl`)
   with the commit, the mechanism and the result; a second look at the same mechanism is reported as such.

## What this benchmark cannot show

- Real-world files: every file is built here from chosen values. A product that reads these files can still fail
  on a camera's JPEG, a spreadsheet with merged cells or a PDF with a CMap. Passing here is necessary, not
  sufficient.
- "Understanding": it measures recovered structure and values (cells, pixels, pitches, volumes, headings), never
  what a picture depicts or what a text means, and no model judges anything.
- Output selection (whether a chart was the right form) and cross-modal reasoning beyond following a member path
  are not measured here; they belong to the form planner's and later lanes' tests.
- The made and transform families measure fidelity, safety, determinism and palette; not beauty, legibility or
  composition.
- Header facts of the honest formats come from hand-built headers checked by parsers in the tests; a decoder that
  computes a duration differently (encoder delay, Xing frames) could disagree by more than the tolerance on real
  files; these files have no such parts.
- The today driver's mapping of a file to a web response is a modelling choice: today's system has no file intake
  at all, so its numbers say what its existing extractors would do if handed these bytes.
