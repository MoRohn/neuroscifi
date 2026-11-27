# OMNIMIND benchmark: taking things in and making things

The examination for Project OMNIMIND, written **before** any OMNIMIND mechanism existed (lane Z, from `omnimind` =
main `8f68beb`), the **baseline of the unchanged system**, and, since then, the record of the product on it: the
development runs, every held-out run (once each, "The product on the benchmark" below) and the status of the
withheld split (no product look taken yet). Every later lane (detection and adapters, the store, the form planner,
generators and validators) is measured here; nothing here helps a lane pass except by being right.

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

`abstention_floor` is the number of tasks whose right answer is an abstention: a subject that **refuses**
everything scores exactly that; one that says **"cannot"** to everything scores less, **29** per split, because the
23 hostile tasks need a refusal and count a "cannot" as abstained (both measured with `score_task` on both splits;
the sentence said "cannot" until fix lane F2, review finding L1). **Read `right` against the floor.** The floor is 52
of 524 (development) and 52 of 528 (held-out): the 20 honest content questions, the 9 truly-unknown content
questions and the 23 hostile tasks.

**What "right" is made of.** A total of right answers mixes very different things, so every total below is also
given by kind of answer (`scripts/omnimind_heldout_record.py breakdown`, `evidence/omnimind_right_breakdown.json`;
review finding M2). The kinds: a **format identification** (`detect.family`, `detect.format`); a **measurement read
off decoded content** (a cell, a pixel, a size, a duration, a vertex count, a member list: one value within a
tolerance); a **correct refusal or abstention** (a task whose only right answer is "cannot" or a refusal); a question
where **either a value or an honest "cannot"** is right (two header-fact questions of formats nothing here decodes);
a **made artifact read back** (`made`, `transform`). A subject that identified every format and abstained or refused
everywhere else would score up to 180 of 524. None of this is "understanding": it is decoding fidelity,
identification, refusal and faithful making.

| kind of answer | development: right / tasks | held-out: right / tasks |
|---|---:|---:|
| format identification | 128 / 128 | 128 / 128 |
| measurement read off decoded content | 310 / 322 | 310 / 326 |
| correct refusal or abstention | 50 / 50 | 49 / 50 |
| a value or an honest "cannot" (either right) | 2 / 2 | 2 / 2 |
| made artifact read back | 22 / 22 | 21 / 22 |
| **all** (the `artifacts` subject) | **512 / 524** | **510 / 528** |

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
  package is absent every answer was "cannot: not built" (the package exists now: "The product on the benchmark"). `tests/acceptance/test_omni_driver.py` runs it against a
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
8 TB, not memory used). At the baseline the `artifacts` subject answered "cannot: not built" everywhere: 29 right per split,
exactly the honest and unknown abstention tasks, and 0 of 23 hostile (a "cannot" is not a refusal).

## The product on the benchmark (development and held-out)

The `artifacts` subject is the product package through the driver (`subject.py`). Right / abstained ("cannot" or
refused where an answer was expected) / wrong / failed, of n:

| family | development (`505b55b`) | held-out | held-out commit | verdict by the rule written before the run |
|---|---|---|---|---|
| intake | 330 / 7 / 0 / 0 of 337 | 328 / 11 / 0 / 0 of 339 | `c1f5f2f` | generalizes (0.979 to 0.968) |
| honest | 52 / 3 / 0 / 0 of 55 | 52 / 3 / 0 / 0 of 55 | `c1f5f2f` | generalizes (0.945 to 0.945) |
| unknown | 51 / 0 / 0 / 0 of 51 | 49 / 0 / 0 / 0 of 49 | `c1f5f2f` | generalizes (1.000 to 1.000) |
| compound | 34 / 2 / 0 / 0 of 36 | 38 / 2 / 0 / 0 of 40 | `c1f5f2f` | generalizes (0.944 to 0.950) |
| hostile | 23 / 0 / 0 / 0 of 23 | 22 / 1 / 0 / 0 of 23 | `c1f5f2f` | generalizes (drop 0.043); **"hostile refused" does NOT fully hold**: one attack answered "cannot", not refused |
| made | 14 / 0 / 0 / 0 of 14 | 13 / 1 / 0 / 0 of 14 | `505b55b` | **does not generalize** by the rule (0.929 against 1.000, a drop over 0.05): one held-out making task the product could not do |
| transform | 8 / 0 / 0 / 0 of 8 | 8 / 0 / 0 / 0 of 8 | `505b55b` | generalizes, **with lane P's disclosure**: lane P once saw the held-out transform rows, so this proves less |
| **all** | **512 / 12 / 0 / 0 of 524** | **510 / 18 / 0 / 0 of 528** | | no fabrication, no crash: holds in every family |

Today's system on the held-out split: 55 right (52 of them the floor), 1 wrong, 1 crash. Every held-out run was
taken once, by the main session, on a clean tree, after its rule was written in the main session's log; the files,
each rule and result quoted verbatim, the commits and the verdicts are in `evidence/omnimind_heldout/` (README). That
log is not in the repository, so **the order of rule and run cannot be verified from git**; the withheld looks are
different: their rule is to be committed before them. The breakdown of the 510 by kind of answer is in the table
above ("What right is made of").

What the numbers do not say:
- **The driver decides some outcomes** (review finding B4). Until lane F1's driver lands, `subject.py` itself refuses
  a document whose text `webcortex.safety.scan_injection` flags (the 4 hostile "instructions aimed at an AI agent"
  tasks per split credit the driver's refusal, while the product only adds a note), turns a partial decode into
  "refused", and maps a product error to "cannot" or "refused" by its reason string (which is how the one held-out
  hostile task became "cannot"). Lane F1 moves these decisions into the product; the withheld looks refuse a driver
  that is not pinned (see the withheld split below).
- **The made family's readers and the product's checks follow one written contract** (DESIGN §6's reading contract,
  ruled by the main session to be lane Z's reading). That they agree on 1,537 fresh product charts (one product
  finding aside) shows the product meets the contract, not that two independent judges agree on what a person
  sees; the visibility rule (below) makes the benchmark's reader stricter than the contract was.
- No held-out run is repeated: a later version of a mechanism is judged only on the withheld split.

**The withheld split: status.** Written by lane ZW and pinned; **no product look has been taken yet**. The ledger
(`evidence/omnimind_final_uses.jsonl`) holds only lane ZW's machinery check through the reference subjects. Each look
needs its pass rule committed first and the drivers pinned (`evidence/omnimind_driver_pins.json`, null until the main
session pins them after lane F1).

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
- **Visibility** (added by fix lane F2 after review finding M1, before any withheld look of `made` or `transform`):
  only what a person would SEE is read, in every colour scheme the picture defines (light, and dark when it has a
  `prefers-color-scheme: dark` rule). A mark, a series vertex, a tick label, a node or an edge is left out when it or
  an ancestor has `display:none` or sits in `defs`/`clipPath`/`mask`/`pattern`/`symbol`/`marker`; when its
  `visibility` is hidden; when a mask, a CSS filter or a blend mode applies (not established); when it has zero size
  (`r` 0, a bar of zero width across its value axis, a degenerate transform, a polyline whose points coincide,
  `font-size` 0); when its probe point is off the canvas (the root's `viewBox`, else its width and height) or
  outside its clip path; when a later opaque area shape that is neither a data mark nor part of its own drawing
  (an edge's arrowhead) covers it; or when its paint, after every opacity, differs from what is beneath it by less
  than 4 of 255 levels in every channel (a colour the reader cannot resolve to RGB is not seen). A bar of zero
  LENGTH draws nothing and is read as the baseline value where it is placed, as a person reads it. Style sheets are
  evaluated for simple selectors (type, `#id`, `.class`, `:root`, `*`, descendant and child combinators); a rule the
  reader cannot evaluate that sets paint, visibility or geometry, and geometry set by CSS, make the picture
  unreadable (failed). Probe points: a circle's centre; a bar's centre and the midpoints of its four edges (all must
  be seen); each vertex of a series (the seen ones are read); a text's anchor; an edge is seen when any of 19 points
  along it is. Tests: `test_omni_readers.py` (every rule, right and hidden variants), `test_omni_bench.py`
  (`test_a_made_picture_a_person_cannot_see_is_not_right`: the reference maker's six picture forms under eight
  hidings), `test_omni_driver_real.py` (the reviewer's probe on product charts).
  **Re-pin.** `readers.py` was not pinned before; it is pinned now in `test_omni_bench.py` (`READERS_SHA256`): at
  `505b55b` its SHA-256 was `6c6c28a2e91b1cbb77a40d12bbd9cd6091b873120e35353ef2145855f9edc239`, after the rule
  `df21e65899c3830514d2ac05322f3b2ace5738aedb02a17561baf0c985596327`. No case table, task digest or truth changed.
  The product on development after the change: `made` 14/14 right, `transform` 8/8 right, carried 1.0 (unchanged);
  on 1,537 fresh product charts and diagrams (1,600 requests from the development templates, seeds 20000+; 63 bar
  requests the product refused by name) the reader and the product's own check agree on all but one, a histogram where the product puts a value lying exactly on an inner bin edge (17.5)
  in the lower bin against the request's half-open bins: a product finding, and the old reader read it the same way.
  **Re-pin (fix lane F4, measurement review second pass R2-2).** Two hidings still passed the reader: an opaque
  `<rect width="100%" height="100%">` painted after the plot (a rect's percentages were read as user units, so it
  covered a 100 x 100 corner) and a root of zero width or height (the view box alone set the canvas). Now a rect's
  percentages are of the canvas, and a root whose width or height is zero, or whose view box has no area, makes the
  picture unread (ReadError, "the picture has no area"). Both make the examination stricter; neither can raise a
  product figure. Reference tests in `test_omni_readers.py`
  (`test_an_opaque_rectangle_of_the_whole_canvas_painted_after_the_plot_covers_it`,
  `test_a_picture_of_no_area_is_not_read`: the right picture is read, the hidden one is not). `READERS_SHA256`:
  before `df21e65899c3830514d2ac05322f3b2ace5738aedb02a17561baf0c985596327`, after
  `fb51f10c76afeeaa1046e9adb5876d3501175f63eb880ff20eda209a8d3c619e`. No case table, task digest or truth changed.
  The product on development after the change: `made` 14/14 right, `transform` 8/8 right. The product's own check
  applies its own rules for the same hidings and more (`artifacts/check/visible.py`, ADR-074's fix lane F4 section).
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

## The withheld split as written (lane ZW)

Written by lane ZW without reading any product code; counts and rules only here. Nobody else opens a
`withheld_*.py` file or `tests/acceptance/test_omni_withheld_cases.py`.

- `acceptance/omni/withheld_cases.py`: the seven families' withheld tables, 172 cases with seeds from 9100-9599, none
  of whose names, files or parameters repeat development or held-out. Tasks: intake 358, honest 55, unknown 111
  (22 disguised cases, 12 truly unknown), compound 66, hostile 35, made 16, transform 11 (652 in all). Abstention
  floor: honest 20, unknown 21, hostile 35. Of the 35 hostile tasks, 8 are constructions of classes that neither
  development nor held-out has; the rest are new constructions of their classes.
- Truths are known by construction and re-derived in `test_omni_withheld_cases.py` by the independent oracle, the
  header parsers, the reference maker and the reference transforms of the earlier tests; every family's generated
  identity (task count and digest) is pinned there.
- **The look.** `scripts/bench_omni.py final --family F|all --subject S --note TEXT --out FILE [--scratch DIR]` is the
  only way to score it (`acceptance/omni/final.py` is the only code that imports a withheld module, inside its
  functions; `tests/acceptance/test_omni_final.py` checks the import statements of every source, script and test).
  It refuses a missing or blank note, an `--out` inside the checkout or not writable, another tree's source, a dirty
  or uncommitted tree (`git status --porcelain --untracked-files=all`, the ledger excepted) and a withheld file whose
  SHA-256 is not the one pinned in `final.py` (and in the test). Then it appends one line per family to
  `docs/quality/evidence/omnimind_final_uses.jsonl` (time, commit, family, subject, note, script, withheld file and
  its SHA-256) BEFORE it imports or scores anything, so a look that crashes still counts; its output is summaries
  only (no task ids, no case names). `run --split withheld` is refused.
- This supersedes two details of the rules above: the ledger is `omnimind_final_uses.jsonl` (not
  `omnimind_withheld_uses.jsonl`), and `bench_omni.py` now has the `final` subcommand that rule 2 said it lacked.
  The rest stands: one look per mechanism, by the main session, at the end, after a pass rule is committed.
- **The driver is pinned for the looks** (fix lane F2, review finding B4). The drivers translate the product's
  answers into the benchmark's outcomes (`acceptance/omni/subject.py`, `scripts/omni_forms_driver.py`), so
  `final.check` also refuses a look when either driver's SHA-256 differs from its pin in the tracked file
  `docs/quality/evidence/omnimind_driver_pins.json`, or is not pinned there (as committed by lane F2: both null, so
  every look is refused until the main session runs `scripts/bench_omni.py pin-driver`, which prints the file with
  the current hashes and writes nothing, after lane F1's driver rewrite, and commits it). Each ledger line now also
  records the drivers' SHA-256 (`drivers`). `test_omni_bench.py::test_the_drivers_only_translate` checks that a driver
  imports no `make` internal or private product name, never calls `scan_injection`, decodes with no limits of its
  own, chooses no outcome by an error's words or from a partial decode, and names no task or case; today's
  `subject.py` fails it (it calls `scan_injection`, maps reasons and partial decodes), so it is marked
  expected-to-fail ("waits for lane F1's driver") for the main session to unmark at merge.
- The ledger's first lines are lane ZW's machinery check, every one noted "machinery check: references only": the
  unchanged system (`today`) on the seven families and the references of the forms, cross and learn families. No
  product subject has looked.
