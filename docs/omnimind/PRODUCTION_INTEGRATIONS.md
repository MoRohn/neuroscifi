# What exists, with its limits and its rollback (deliverable 6: "Production-Ready Integrations")

Status: 2026-11-27, brought up to date by documentation lane 3 from `omnimind` at `4f66291` (Wave O-3: the relater's
verdicts shown only, never stored; its three new comparisons withdrawn; the relater not supported). Earlier: written by documentation lane 2 on `omnimind` at `09bfff6` with fix lanes F4 (`1aa7eb6`) and F5 (`bf3dac6`) merged, from the catalogs the system prints
(`nsf artifact adapters`, `nsf artifact forms`, `nsf artifact --help`, as recorded by the integration lane 2) and the
code. "Production-ready" in the directive's sense is not claimed: this is a local branch, not merged into `main`,
never run on CI, measured on synthetic files and simulated people, and with three mechanisms (identification of unknown files, refusal of hostile ones, the relater) not
supported on either withheld split.
Fix lane F5 (`bf3dac6`) is merged too.

## The first screen

- **Everything here is reached by an owner's explicit command or the owner's console view.** Nothing runs unasked;
  nothing is on the answer path; Pocket, voice, agents and family scopes reach none of it.
- **One thing is on by default**: the console's "Made and kept" view (`console_artifacts`). Upload from the console
  and form learning are off.
- **Rollback of the code** is a revert of the lanes' commits; **rollback of the individual's file is not possible by
  restore**: schemas 20 and 21 are forward-only, and the way back is the owner's own copy taken before the upgrade
  ([RUNBOOK.md](RUNBOOK.md)).

## Commands

| command | what it does | limits |
|---|---|---|
| `nsf inspect FILE` | says what a file is and what can be read from it; stores nothing | `Limits`; the guard |
| `nsf artifact add FILE...` | keeps files beside the individual with what could be read | `artifact_max_bytes` per file, `artifact_store_max_bytes` in all; a safety refusal stores nothing; a size-bound refusal keeps the file unread |
| `nsf artifact list` | what is kept, newest first; made ones say so | owner only |
| `nsf artifact show ID [--why]` | what was detected and read, links, what was done; `--why` the production's reasons in plain words |  |
| `nsf artifact facts ID` | measurements from what was read, each with how |  |
| `nsf artifact export ID PATH` | writes the bytes to a new file (never overwrites; mode 0600); the explorable type gets `.html` |  |
| `nsf artifact forget ID` | the artifact, everything made from or contained in it, its bytes and outcomes; later checkpoints rewritten |  |
| `nsf artifact gaps [--report]` | formats nothing read, and how often; `--report` adds failed checks, recurring repairs, words where another form carried more, contradicting links (since Wave O-3 only links an earlier version stored: none is stored now) | read-only |
| `nsf artifact feedback ID kept\|dismissed\|replaced:FORM`, `--reset` | records the owner's reaction to a made form; `--reset` deletes every row | only with `form_learning` `record` or `apply` |
| `nsf artifact relate A B [--question "..."]` | measures both and shows the verdict with the line "not recorded: the relater's answers are shown only, never stored as links, until a relater passes an examination (ADR-081)"; since Wave O-3 **no `consistent_with` / `contradicts` link is stored** on any path; `describes` / `same_subject` are recorded as before, as the system's claim | compares what the question names; archive members against a listing, drawn links against a text and sound cue times against a cue sheet are "cannot" (those comparisons were withdrawn: they could not be made reliable); the relater is not supported. Verdict links an earlier version stored: `PYTHONPATH=src python scripts/omnimind_fx3/links_decided_by.py --db PATH --by any` lists them (dry run), `--remove` removes them and writes no history entry |
| `nsf artifact adapters` | the readers here and the approved external ones |  |
| `nsf artifact make FILE\|ID... -r "..." [--form F] [--alternatives N] [--keep] [--partial] [--device D] [--seconds S] [--bytes B]` | decides the form, makes, checks independently, repairs, keeps with its "why"; several files are related | 60 s and 16 MiB by default; at most 4 alternatives; large sources reduced by stated rules |
| `nsf artifact remake ID [--form F] [--option K=V]` | makes again from the kept specification or sources | as `make` |
| `nsf artifact forms [--json]` | what can be made here and which check reads it back | reads no individual |
| `nsf form plan -r "..." [--content FILE... \| --shape JSON] [--device D] [--accessibility NEED]` (`-r` since fix lane F5; `--request` as before) | prints the form a result would take, and why; nothing is made |  |
| `nsf form learned` | what was learned per key, and what it changes |  |
| `nsf artifact release ID` | ends a forget's hold on an artifact (the owner's statement that it does not name what was forgotten) | added by fix lane F4; writes a log row; a restore replays it |

Exit codes follow one table (`artifacts/outcome.py`, fix lane F1): 0 done; 1 could not be done (an id not held,
a make that failed or could not, a feedback not recorded); 2 refused (a rule or a limit, or only part of a file read
without `--partial`) or misuse. `make` and `remake` exit through the product's
outcome table too (fix lane F5): a part used without `--partial`, a limit or a held file gives 2; a format nothing here
reads gives 1. After a partial read or a reduction the summary and the "why" say
so in the made thing's own words; a very large archive is drawn as its folders with file counts (fix lane F5).

## Settings

| setting | default | command | what it does | rollback |
|---|---|---|---|---|
| `artifact_max_bytes` | 64 MiB (1 KiB to 1 GiB) | `nsf config --artifact-max-mib N` | the largest file kept or read; memory in the guard's child can reach 256 MiB + 2 x the input + 2 x 256 MiB (896 MiB at the default, about 2.8 GiB at 1 GiB), and the second decode of `make`, `facts` and `relate` in the command can use as much; the help says so, with 344 MiB measured to make a chart from a 60 MiB CSV (fix lane F5) | set it back |
| `artifact_store_max_bytes` | 2 GiB (0 to 1 TiB) | `nsf config --artifact-store-max-mib N` | the sidecar's total | set it back |
| `artifact_adapters` | none | `nsf config --artifact-adapters NAME,...` | approves external readers by entry point, distribution and version (`NAME=DIST==VERSION`); another distribution or version is listed "approve it again" and not loaded | `nsf config --artifact-adapters ""` |
| `form_learning` | `off` | `nsf config --form-learning off\|record\|apply` | record: check verdicts and reactions are kept; apply: plans go through `learn.plan` and the "why" says when a habit decided | `off`; `nsf artifact feedback --reset` removes what was learned |
| `console_artifacts` | **on** | `nsf config --console-artifacts on\|off` | the console's "Made and kept" view and its routes; off: every route answers 409 naming the command | `off` (next call) |
| `console_artifact_upload` | off | `nsf config --console-artifact-upload on\|off` | the console accepts files (chunked, capped, through `store.add`) | `off` (next call) |
| `imagine_scenarios` (existing) | off | existing | when on (and `max_imagined_rows` above 0), a production's foresight is also written to the imagined store as a SIMULATION record | off |

A damaged value of any of the six reads as its default. All six are carried by a restore. Constants that are not
settings (revert their commit to change them): `archive.MAX_UNPACK_BYTES` 16 GiB, `produce.MAX_RASTER_PIXELS` 4 MP,
`produce_builders.LINE_WINDOWS` 2,000, `MAX_SCATTER_POINTS` 5,000, `MAX_SIBLINGS` 24, `produce.MAX_RELATE_PAIRS` 6,
`MAX_RELATE_SECONDS` 10 s, `Limits` (below).

## Readers (adapters)

66 built-in readers (`nsf artifact adapters`; the list is the only statement of what can be read). Limits for all
(`artifacts/types.py`, `Limits`): input 64 MiB, 256 MiB decoded in all, 4,096 members, nesting depth 4, ratio 200
per stream, 5,000,000 elements, 20 s per decode; each decode first in the guarded child process (fail closed).

| reads the content | reads only the header, or only the structure | identifies only, or refuses |
|---|---|---|
| plain text; JSON, GeoJSON, ipynb; gzip, zstd, bzip2, xz streams; CSV, TSV; NDJSON; XLSX, XLSM; ODS; Markdown; HTML, XHTML; RTF; DOCX; ODT; PPTX; ODP; EPUB; PDF (text layer, optional `pypdf`); XML, RSS, Atom (no DTD); plist; TOML; INI; Python; JavaScript, TypeScript, C, C++, shell (surface); pyproject, requirements, package.json; notebooks; ZIP, JAR, APK; TAR; SQLite (stored columns); PNG; NetPBM; farbfeld; QOI; BMP; ICO; GIF; SVG; WAV; AIFF; AU; MIDI; OBJ; STL; PLY; OFF; glTF, GLB; GeoJSON, GPX, KML, KMZ, WKT; NPY; NPZ; the system's own model file (`nsf_model`) | header only: JPEG, WebP, HEIC/AVIF, PSD, MP3, AAC, FLAC, Ogg/Opus/Vorbis, M4A; content of some variants and header of others: YAML, TIFF, DICOM, FITS; container structure and tracks, no frame or sample: MP4/MOV/3GP, Matroska/WebM, AVI | identified only: 7z, RAR (and formats detected with no reader, such as Parquet, HDF5, `.doc`, `.xls`: the capability-discovery answer); refused: Python pickle (never loaded) |

Measured: [BENCHMARKS.md](BENCHMARKS.md). Known limits: an array's dtype and 3-D shape are not kept; a DOCX's embedded
pictures are named, not decoded; XLSX reads about 150,000 cells per sheet at the default limits; text formats are
charged their measured worst-case cost per structural mark, so honest large files are refused with a reason (above
about 131,000 INI lines, 335,000 JavaScript or requirements lines, 1.5 million Markdown lines); a 6-minute WAV is
refused (its samples would need more than 256 MiB). Rollback: revert lanes K, B1, B2 and their fixes; the readers are
new modules nothing existing calls.

**What refuses a whole file, and what does not** (ADR-080 decision 4; `artifacts/outcome.py`, rules 1 to 3; lane
FX2):
1. **Parser-level attacks refuse the whole file**, even when the attack sits in one part beside honest parts: entities,
   document type definitions, XInclude, decompression bombs, lying sizes, overlapping members, unsafe member names and
   links, damage, nesting past the limit, and text addressed to an AI agent (injection).
2. **A reference to something outside the file is never followed and never refuses by itself.** It is named in a note;
   whatever needs it is "cannot": a glTF whose geometry sits in an outside buffer is read as its header only (counts,
   buffers by index); an SVG linking an outside image is read, with its note. OOXML relationships to outside targets
   are counted by kind, never by address.
3. **Embedded active content is never run and never refuses by itself**: a macro project, a PDF script, auto-run or
   launch action, an embedded file are named in a note.
Stated limits: a spreadsheet cell with no cached value is "cannot" only in the note (in the table it is an empty cell);
a PDF action inside a compressed stream other than the catalog is not found (without `pypdf` a PDF is header facts
only); a text note that quotes the ZIP signature bytes is left unknown; ODF outside links are not counted. Lane FX's own
refusal corpus now reads 165 refused, 3 answered of 168: the 3 are the SVG-with-an-outside-image class, answered with
its note as ruled (its truth file still says "refused").

## Makers (generators) and checks (validators)

| maker | media type | checked by |
|---|---|---|
| `chart.line`, `chart.bar`, `chart.scatter`, `chart.histogram`, `chart.heatmap`, `chart.small_multiples`, `timeline`, `graph.nodelink`, `graph.tree`, `flow.steps`, `animation.svg` | `image/svg+xml` | `g1.svg` |
| `table.html`, `document.html` | `text/html` | `g1.html` |
| `document.md` | `text/markdown` | `g1.markdown` |
| `data.csv` | `text/csv` | `g1.csv` |
| `data.json` | `application/json` | `g1.json` |
| `sound.tone`, `sound.sonify`, `sound.sequence` | `audio/wav` | `g2.wav` |
| `solid.box`, `solid.extrude`, `solid.revolve`, `solid.mesh`, `solid.points`, `surface.grid` | `model/stl`, `model/obj` | `g2.mesh` |
| `image.raster`, `image.apng` | `image/png`, `image/apng` | `g2.png` |
| `explorable.model`, `explorable.steps`, `solid.view` | `application/vnd.neuroscifi.explorable+html` | `g2.explorable` |

Every maker is deterministic, grayscale (hue only on warned values), and every SVG scales to its container. The checks
never import the makers (one shared module: `expr.py`, for the explorable expression language). Since fix lane F4 the
SVG check reports `visible_to_a_person` (`check/visible.py`): until then it passed six hidings the benchmark's reader
rejects, and both passed an opaque covering rectangle and a zero-width root; the reader was made stricter on those two
in a separate commit and re-pinned. Repairs: at most three rounds of the makers' own named repairs. Rollback:
revert lanes G1, G2 and their fixes.

## The pipeline, the planner, the relater, the learner

| function | entry | limits | rollback |
|---|---|---|---|
| `artifacts.produce.produce` | `nsf artifact make`, `remake`, the console's Make | 60 s, 16 MiB, at most 4 alternatives, at most 6 source pairs related (none started after 10 s); any stop leaves nothing stored | revert lane P, int2, F1 D, F3 item 3 |
| `artifacts.forms.plan`, `alternatives`, `foresee` | inside `produce`; `nsf form plan` | frozen at `4388f4d`; time estimates 5 to 560 times too high (recorded) | revert lane C (nothing else imports it but the pipeline and the console) |
| `artifacts.relate.compare`, `transfer_units` | `nsf artifact relate`; inside `produce` for two or more sources (its relations are shown in the made document and its record, never stored as verdict links) | comparisons the question names, the older ones only (Wave O-3 withdrew three new ones); answers shown only; plural folding only; no meaning of a picture or sound; with no question no word selects an aggregate, so a stated total beside a column shows "Cannot tell" | revert lane R and F3 item 2; reverting lane FX5's last commits would bring back the withdrawn comparisons and verdict recording (not advised: ADR-081); earlier verdict links: `links_decided_by.py --by any --remove` |
| `artifacts.learn.plan`, `record_outcome` | inside `produce` when `form_learning` is on; `nsf artifact feedback`; `nsf form learned` | per-key tally, minimum evidence 2, never removes a form, owner's stated preferences win | `form_learning off`; revert lane L (schema 21 stays) |

## Console routes (lane U, ADR-079)

All behind the console's owner bearer token and its unchanged Host and Origin checks; security headers byte-equal
to `main`'s; no route takes a path; ids are `artifact-` and 32 hex digits.

| route | what it does |
|---|---|
| `GET /api/artifacts?page=N` | a page of 20 kept artifacts, newest first, in plain words |
| `GET /api/artifacts/<id>` | one artifact: what it is, how to show it, facts, why, relations (the guard's success verdict reused per content hash; one detail decode at a time, a second waits up to 60 s then gets a plain "busy" answer; a held artifact shown without a decode) |
| `GET /api/artifacts/<id>/content` | its bytes, `application/octet-stream`, streamed in bounded chunks |
| `POST /api/artifacts/<id>/image`, then `GET /artifact-image/<token>` | a 256-bit, 120-second, session-bound address for a PNG the store holds (an `<img>` cannot send the bearer header) |
| `POST /api/artifacts/<id>/feedback` | kept / dismissed / replaced, only while `form_learning` records |
| `POST /api/artifacts/<id>/forget` | as `nsf artifact forget` |
| `POST /api/artifacts/make`, `GET /api/artifacts/jobs/<id>` | one production at a time, off the request thread, polled by the page |
| `POST /api/artifacts/uploads`, `.../<id>/chunk?offset=N`, `.../<id>/finish` | upload, only when `console_artifact_upload` is on: chunks of at most 1 MiB, in order, never past the announced size, at most two unfinished, 600 s expiry, then `store.add` |

Rollback: `nsf config --console-artifacts off`, or revert lane U's commits; nothing stored changes shape.

## The guard

`artifacts/guarded.py` (fix lane F1): always on for owner-started decodes; only tests can switch it off. The child
gets `RLIMIT_AS` = 256 MiB + 2 x the input + 2 x `max_decoded_bytes`, CPU = `max_seconds` + 5 s, no file writes, no
core file, a clean environment and an empty private directory; its verdict is a JSON line (for `add`, since fix lane
F4, a summary of at most 4 MiB that the parent validates field by field; a malformed one means the file is kept unread);
a child that cannot start means the file is kept but not read; an approved external reader that loads in the parent
but not in the child is refused (`external_unguarded`); a success verdict is reused per content hash, name, limits and
approved readers (at most 256 entries; refusals are never reused). Where `RLIMIT_AS` cannot be set (macOS,
Windows: **unverified**, no such host was available) only the wall-clock kill bounds it. Its cost (fix lane F4,
`docs/quality/evidence/omnimind_f4.json`): the child starts in about 0.09 s; `add` decodes once, in the child (a
5,000-file zip 5.81 to 2.97 s, a crafted 135 KiB PNG 13.19 to 6.86 s, a 30 MP PNG 1.31 to 1.02 s); `make`, `facts`,
`relate` and the console's view decode twice plus about 0.1 s. Before that change the code reviewer measured from
about 1.0 (a WAV) to 7.7 (the zip) times the unguarded time.
Rollback: revert `5faab9a` and its call sites (this removes a security bound; not advised).
