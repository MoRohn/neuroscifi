# OMNIMIND benchmark: taking things in and making things

The examination for Project OMNIMIND, written **before** any OMNIMIND mechanism existed (lane Z, from `omnimind` =
main `8f68beb`), the **baseline of the unchanged system**, and, since then, the record of the product on it: the
development runs, every held-out run (once each, "The product on the benchmark" below) and the status of the
withheld split (looked at once per mechanism, on `55f89be`). Every later lane (detection and adapters, the store, the form planner,
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

Today's system on the held-out split: 55 right (25 intake answers and 30 abstentions where abstaining is right; the floor is 52), 1 wrong, 1 crash. Every held-out run was
taken once, by the main session, on a clean tree, after its rule was written in the main session's log; the files,
each rule and result quoted verbatim, the commits and the verdicts are in `evidence/omnimind_heldout/` (README). That
log is not in the repository, so **the order of rule and run cannot be verified from git**; the withheld looks are
different: their rule is to be committed before them. The breakdown of the 510 by kind of answer is in the table
above ("What right is made of").

What the numbers do not say:
- **Every held-out figure above was taken through the earlier driver** (review finding B4). At `c1f5f2f` and
  `505b55b`, `subject.py` itself refused a document whose text `webcortex.safety.scan_injection` flagged (the 4
  hostile "instructions aimed at an AI agent" tasks per split credited the driver's refusal, while the product only
  added a note), turned a partial decode into "refused", and mapped a product error to "cannot" or "refused" by its
  reason string (which is how the one held-out hostile task became "cannot"). Since then the product decides: fix
  lane F1 (`d8bbfd0`) added `Limits.refuse_flagged` (on by default; the store turns it off and marks the file
  instead), `Decoded.status` and one outcome table, `artifacts/outcome.py` (`of_error`, `of_decoded`), which both the
  command line's exit codes and the driver read; fix lane F3 (`bdfebab`) moved the last two member decisions into it
  (`of_member`). Development is unchanged by the move (512 / 12 / 0 / 0). The held-out figures were not re-taken (no
  held-out run is repeated); the withheld looks are taken through the new driver, pinned.
- **The driver's last two decisions moved into the product** (second measurement pass, R2-3 and R2-4; fix lane F4,
  `c579be6`). Until `09bfff6`, `choose_form` re-planned without the explicit form when the planner's `Request` refused
  it, dropped explicit, preferred and avoided form names it could not map, and decided itself that a climbing or
  absolute member name "names no member". Now a refused explicit form gives no form (the product's answer, scored by
  the examiner) and is recorded as `("explicit-refused", name)`; unmapped names are passed to the planner unchanged and
  recorded in `FORMS_UNMAPPED`, which every forms look prints; and `artifacts/outcome.member_path` (the product's
  table) decides the member question. The dead `_Refuse` class is deleted. `test_the_drivers_only_translate` passes
  with no mark; `tests/acceptance/test_f4_driver.py`. Development is unchanged (forms identical to the reviewer's
  run; `FORMS_UNMAPPED == []`).
- **The driver measures some quantities with its own definitions**, not the product's: a sound's pitch and its
  number of onsets (with the benchmark's own readers), an image's mean gray (Rec. 709 luminance over the returned
  pixels), a map's bounding box. A product that computed them differently would be scored by the driver's
  definition.
- **The forms subject sees the case's content shape, not the content** (review finding L4). The forms driver builds
  the planner's `ContentShape` from the case's own fields: the planner never sees content there. The learn driver
  passes each case's candidate forms to the learner as its set of generators. The name mapping is many-to-one: several
  product forms count as one benchmark form (document as prose, small multiples as a line chart, a tree diagram as a
  node-link diagram, a tone as a sonification, a surface view as a solid viewer, an image sequence as an animation),
  which can turn a choice the examiner did not list into a listed one. A forms figure measures choosing a form from a
  described shape, not from real files.
- **One module is shared by a maker and its check** (review finding N1): `artifacts/expr.py` evaluates expressions for
  both the explorable generator and the explorable check, so that check is not independent of the maker on expression
  meaning (mitigated by the tests against the page's own script runtime).
- **The made family's readers and the product's checks follow one written contract** (DESIGN §6's reading contract,
  ruled by the main session to be lane Z's reading). That they agree on 1,537 fresh product charts shows the product
  meets the contract, not that two independent judges agree on what a person sees. The one product finding of that
  comparison (a histogram value lying exactly on an inner bin edge drawn in the lower bin) was fixed by fix lane F1
  (`bb0fedf`, the generator) and fix lane F3 (the check: with a stated bin width every bin is half-open,
  `test_f3_histogram.py`). The visibility rule (below) made the benchmark's reader stricter than the contract was;
  since fix lane F4 the product's own check applies visibility rules of its own (next section).
- No held-out run is repeated: a later version of a mechanism is judged only on the withheld split.

**What "made right" and "checked" establish, and what they do not** (second measurement pass, R2-1 and R2-2).
- "Made right" means: the benchmark's reader recovered the source values from the bytes within tolerance, read only
  what its visibility rule counts as seen, found the palette and safety rules kept, and the bytes were deterministic.
  It does not mean a person looked at the picture, found it legible or found it useful: no person judged any made
  artifact.
- "Checked" (what the store records for a made artifact) is the product's own check's verdict
  (`artifacts/check/`, code that never imports the generators). At `09bfff6` it was weaker than the benchmark's reader:
  on product-made line, bar and scatter charts it said ok for six hidings the reader rejects (`visibility="hidden"`
  on the plot; a style rule `display:none` on the marks or series; a style rule `opacity:0` on the plot; the plot
  clipped to a 0 x 0 `clipPath`; tick labels at `font-size:0`; on a scatter, circles with no radius), and two
  hidings passed both the reader and the check (an opaque rectangle the size of the picture painted after the plot,
  and a root `<svg width="0">`).
- **Since fix lane F4** the product's check has its own finding `visible_to_a_person` (`artifacts/check/visible.py`,
  `6c2ed98`; written from the SVG and CSS rules, not from the reader): hidden by `display`, `visibility` or opacity in
  attributes, `style` attributes or `<style>` rules (the dark scheme's `@media` rules counted for that scheme); a clip
  path of zero area; zero font size; a circle with no radius; paint only in the background colour or none; a mark off
  the canvas; a mark covered by a later opaque shape; a zero-size root; a hiding rule the check cannot read. Tests:
  `tests/artifacts/test_f4_visible.py` (50: 16 hidings on line, bar and scatter charts and no radius on a scatter, all
  failing without the change; untouched charts and harmless later shapes pass). And the reader (`8523136`, one
  separate commit) reads a rectangle's percentages as percentages of the canvas, so the covering rectangle covers
  the plot, and refuses a picture with no area; re-pinned (`READERS_SHA256` `df21e658...` to `fb51f10c...`, the
  paragraph "Re-pin (fix lane F4 ...)" below). The product on development after both: `made` 14/14, `transform` 8/8.
- None of these could raise a product figure: the product's generators never emit them (the reviewer's fuzz of 350
  fresh seeds: 0 disagreements between the reader and the check). What remains a limit of the words: both judges
  read only what their rules name; a way of hiding that neither rule set names is still possible, and no person
  looked at any made picture.

**The withheld split: status. The looks were taken, once each, on `55f89be`** (2026-11-27, by the main session; the
rule below and the driver pins committed in that commit; controls first). Ledger `evidence/omnimind_final_uses.jsonl`
(42 lines); evidence and the main session's reading by the rule in `evidence/omnimind_looks/` (`summary.json`).
Seven families (subject `artifacts`), right / "cannot" / wrong / failed: intake 347 / 11 / 0 / 0 of 358, honest 52 / 3 /
0 / 0 of 55, compound 64 / 2 / 0 / 0 of 66, made 16 / 0 / 0 / 0 of 16, transform 11 / 0 / 0 / 0 of 11: **supported on
unseen cases**; **unknown 107 / 1 / 3 / 0 of 111: not supported, 3 wrong answers** (disguised or unknown files
answered falsely); **hostile 32 / 0 / 3 / 0 of 35: not supported, 3 hostile files answered instead of refused**
("hostile inputs refused" does not hold; 8 withheld constructions belong to 7 classes no earlier split has). All:
629 / 17 / 6 / 0 of 652 (today's system: 72 right, 2 wrong, 3 failed). These six wrong answers are the first
fabrications and failures to refuse seen on any split. Controls: held, except that "reference reruns identical" was
not checked (and the refuse-everything control was dropped before the looks, by ruling). By the rule, none of these
is retried on these cases: the unknown and hostile weaknesses are to be fixed and examined on NEW withheld cases. The
forms, cross and learn looks: [OMNIMIND_BENCHMARK_FORMS.md](OMNIMIND_BENCHMARK_FORMS.md), "The withheld split:
status"; all of it: [../omnimind/BENCHMARKS.md](../omnimind/BENCHMARKS.md), "The withheld looks".

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
   no new failed task). Each look is appended to a ledger (`docs/quality/evidence/omnimind_final_uses.jsonl`; this
   line named it `omnimind_withheld_uses.jsonl` until the second measurement pass, R2-5, found the stale name) with the
   commit, the mechanism and the result; a second look at the same mechanism is reported as such.

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
  own, chooses no outcome by an error's words or from a partial decode, and names no task or case. It failed on the
  driver of `505b55b` and was marked expected-to-fail; since fix lane F1's driver (`d8bbfd0`, `10dd174`) it passes
  without the mark, and fix lane F3 (`bdfebab`) made it stricter (a choice made from a note's words is caught too,
  `test_driver_rules_catch_each_way_of_deciding`). It is a tripwire over the driver's syntax, not a proof (R2-6: the
  re-planning of R2-3 passes it); the controls are the pins, the clean tree and the commit each ledger line names.
- The ledger's first lines are lane ZW's machinery check, every one noted "machinery check: references only": the
  unchanged system (`today`) on the seven families and the references of the forms, cross and learn families. No
  product subject has looked.

## The rule for the withheld looks (written before them)

This section was committed by documentation lane 2 (2026-11-27) **before any product look at the withheld split**;
every look must be taken at a commit that has this section as an ancestor, with the driver pins of
`evidence/omnimind_driver_pins.json` non-null and committed in the same commit or later. Nothing below was chosen
after seeing a withheld figure: none exists (the ledger holds only lane ZW's machinery check).

**Where it comes from.** The second measurement pass (the reviewer's section "The
rule I would hold the withheld looks to") (its report is in the main session's scratch folder and in `.omc/handoffs/omnimind/heldout-results/review-measure-2.md`, neither tracked by git) proposed it; the main session adopted it and wrote it in its log
(`.omc/handoffs/omnimind.md`, which git does not track). The log's text, exactly as it stands there:

```text
  LOOK RULE (ADOPTED from the reviewer; to be written into the docs and committed with the pins BEFORE the
  looks): A. VOID unless: rule committed in an ancestor of the look commit; pins non-null committed with the
  rule or later; tree clean; development reproduces exactly at the look commit (512/12/0/0; forms 107 good / 0
  bad / 0 violations; cross 72/72; learn 0.8158/0.7617 vs 0.5871/0.5842); controls run first as reference-only
  ledger lines: refuse-everything = the withheld floor with 0 wrong; first_good oracle = 1.000;
  always_consistent correct on exactly n/2 with 0 twins; oracle ≥ key_majority ≥ never_learns; reference reruns
  identical. B. Words: "supported on unseen cases" / "not supported" / "not exercised" / VOID. Seven families:
  supported iff 0 wrong AND 0 failed AND right/n ≥ held-out share − 0.05 (intake 0.968, honest 0.945, unknown
  1.000, compound 0.950, hostile 0.957, made 0.929, transform 1.000); any wrong is the headline; "hostile
  refused" only if right == n; breakdown and right − floor beside it. forms: supported iff 0 violations AND good
  ≥ shape_only + 0.10 AND bad ≤ shape_only's AND too_much ≤ shape_only's; "reads the words" only if words slice
  ≥ shape_only + 0.15 AND ≥ always-prose's AND pairs > shape_only's; print FORMS_UNMAPPED; no second-reader
  ceiling on withheld. cross: supported iff wrong ≤ 2 AND correct_of_answered ≥ 0.95 AND correct ≥ 0.75n AND
  twins ≥ 2/3 of pairs AND no kind with > 1 wrong; a new kind answered only "cannot" = "not exercised". learn
  (simulated): supported iff gains late and all each exceed 2√2 × the larger sd over seeds; majority references
  beside; form_learning stays off. C. A look cannot establish real files, real people, visual quality or
  usability, later versions, absent formats, or generality from n of 14 or 8; a second look is not fresh; a
  "not supported" is never retried.
```

**The rule in full.** The log's text is a summary of the reviewer's; the log says the rule was adopted from the
reviewer. The reviewer's text, exactly as written (A, B, C):

**A. Preconditions (a look taken without them is VOID, reported as void, not as "not supported").**
1. The rule is committed in an ancestor of the look commit. The driver pins in `omnimind_driver_pins.json` are non-null
   and committed in the same commit as the rule or later. The tree is clean. `final`'s refusals all pass: withheld SHA
   pins, driver pins, note.
2. Development reproduction at the look commit (development is allowed and costs nothing). It must reproduce exactly:
   artifacts 512/12/0/0; forms 107 good / 0 bad / 0 violations; cross 72/72 (twins 36); learn late 0.8158, all 0.7617
   (planner-only 0.5871, 0.5842). Any difference means the product under the look is not the one measured: void (or
   re-baseline in a commit first).
3. Controls on the withheld split, run with the same `final` machinery as reference-only ledger lines, BEFORE the product
   looks. Each holds by construction, so no withheld figure is needed in advance:
   - Lane Z families: a refuse-everything subject scores right == abstention_floor and wrong == 0. The today subject
     scores 0 failed outside hostile and makes nothing.
   - forms: the `first_good` oracle scores good share 1.000. `shape_only` run twice gives identical figures.
   - cross: always_consistent is correct on exactly n/2, with 0 both-twins.
   - learn: oracle ≥ key_majority ≥ never_learns on late_kept, and planner-only run twice gives identical per-seed
     figures.
   
   A failed control voids that family's look. If `final` cannot run a control subject, the rule says which ones it can
   run before the looks are taken.
4. One product look per family, at one commit. A second look at the same mechanism is reported as a second look and is
   never "supported on unseen cases".

**B. Fields and outcome words, per family.** Allowed words: **"supported on unseen cases"**, **"not supported"**, **"not
exercised"** (the family or kind has no task the subject could attempt, e.g. its subject is unavailable), and VOID from A.
Every look reports n, the counts and the references beside them, whatever the word.

- **intake, honest, unknown, compound, hostile, made, transform** (`bench_omni.py final --subject artifacts`).
  - Fields: n, right, abstained, wrong, failed, abstention_floor, fabrication, right by kind (the breakdown's five
    kinds), and carried_mean for made and transform.
  - "Supported on unseen cases" iff wrong == 0 AND failed == 0 AND right/n ≥ the held-out share − 0.05 (OMNIMIND_BENCHMARK.md
    §withheld, item 3). The held-out shares: intake 0.968, honest 0.945, unknown 1.000, compound 0.950, hostile 0.957,
    made 0.929, transform 1.000.
  - Otherwise "not supported". Any wrong is the headline, as a fabrication count, whatever else holds.
  - The words "hostile inputs refused" only if hostile right == n.
  - Report right − abstention_floor beside right.
- **forms** (frozen planner 4388f4d through `choose_form`).
  - Fields: good, good_or_fine, bad (too_poor, too_much, unfit, violation), hard_constraints_violated, explicit
    (feasible_honoured, infeasible_handled), the slices cue words / content / conflict, pairs and twins both_good,
    `FORMS_UNMAPPED`, and the unknown purpose words.
  - References from the same run: shape_only, always-prose (today), richest, random.
  - "Supported on unseen cases" iff violations == 0 AND good ≥ shape_only good + 0.10 AND bad ≤ shape_only bad AND
    too_much.k ≤ shape_only's.
  - "Reads the request's words" only if the words slice ≥ shape_only + 0.15 AND ≥ always-prose's words slice AND pairs
    both_good > shape_only's.
  - State that no second reader labelled the withheld cases, so there is no ceiling to quote. Any unmapped explicit form
    is listed, and counts as not honoured.
- **cross** (the relater).
  - Fields: correct, answered, correct_of_answered, both_twins_correct, by_kind (the three new kinds listed apart).
  - "Supported on unseen cases" iff wrong (answered − correct) ≤ 2 AND correct_of_answered ≥ 0.95 AND correct ≥ 0.75 n
    AND both twins ≥ 2/3 of pairs AND no kind with more than 1 wrong.
  - A new kind answered only "cannot": "not exercised" for that kind (honest), never counted toward "supported".
  - always_consistent and always_inconsistent go beside the result.
- **learn** (the shipped rule against planner-only, the same driver, SIMULATED people).
  - Fields: late_kept and all_kept, mean and sd over the 10 withheld seeds, per seed.
  - "Supported on unseen cases (simulated people)" iff the mean gain late > 2√2 × the larger sd AND the mean gain all >
    2√2 × the larger sd (the family's visibility rule).
  - key_majority, person_majority, never_learns and the oracle go beside it. "Better than a majority rule" only if the
    gain over key_majority clears the same margin.
  - `form_learning` stays off whatever the result.

**C. What a look can establish, and what it cannot.**
- It can establish: on cases written without reading the product, by a lane that saw no product code, the frozen product
  at the look commit stays within these bounds, and fabricates nothing on those cases.
- It cannot establish:
  - anything about real files, real people, the owner's taste, visual quality or usability (R2-1, R2-2);
  - any version after the look commit;
  - formats or kinds absent from the tables;
  - generality from small n (made 14, transform 8);
  - a second look, which is not a fresh measure.
- A "not supported" is reported as a result, never retried on the same split.

**Where the two texts differ, and which applies** (the main session's ruling of 2026-11-27, before any look: **in all
four places the reviewer's stricter text applies**):
- The reviewer's A.3 adds a control for the seven families that the log's summary leaves out: "The today subject
  scores 0 failed outside hostile and makes nothing."
- The reviewer's A.3 says what happens when a control cannot be run: "If `final` cannot run a control subject, the rule
  says which ones it can run before the looks are taken." The log's summary does not.
- The reviewer's A.4 ("One product look per family, at one commit") is in the log only as "a second look is not
  fresh".
- The reviewer's B adds, for the seven families, "The words 'hostile inputs refused' only if hostile right == n", which
  the log writes as "'hostile refused' only if right == n"; and, for forms, "Any unmapped explicit form is listed, and
  counts as not honoured".
So the rule that applies is the reviewer's text above, with the one change below for the refuse-everything control.
Concretely: the `today` control must score 0 failed outside hostile and make nothing; one product look per family,
at one commit; "hostile inputs refused" is written only if hostile right == n; an unmapped explicit form is listed
and counts as not honoured.

**Which controls `final` can run** (read from the code at `09bfff6`, unchanged by fix lane F4; nothing was run).
- forms, cross, learn (`scripts/bench_omni_forms.py final`): the subject may be any of the family's references
  (`acceptance/omni/forms.py`: `today`, `richest`, `random`, `shape_only`, and `first_good` for forms;
  `cross.py`: `today`, `always_consistent`, `always_inconsistent`; `learn.py`: `today`, `never_learns`,
  `key_majority`, `person_majority`, `oracle`) or a dotted `module:attribute` such as the product's
  `make_planner_only`. Every control the rule names for these three families can be run.
- the seven families of lane Z (`scripts/bench_omni.py final`): `--subject` accepts only `today` and `artifacts`
  (`subject.SUBJECTS`). The `today` control can be run. **A refuse-everything subject cannot be run through `final`
  as it stands**: the harness's `probe:refuse` subject exists for the tests and is not a `final` choice. By the
  reviewer's A.3 the main session ruled before the looks (2026-11-27): **the refuse-everything control is DROPPED for
  the withheld split**, because `final` cannot run it and its result holds by construction (a subject that refuses
  everything scores exactly the floor). **The `today` control IS run first**, as a reference-only ledger line, and
  every control named for forms, cross and learn is run.

