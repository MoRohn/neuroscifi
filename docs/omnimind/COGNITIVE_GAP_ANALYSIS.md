# Cognitive gap analysis, after the build (deliverable 3)

Status: 2026-11-27, written by documentation lane 2 on `omnimind` at `09bfff6` with fix lane F4 (`1aa7eb6`) merged. The first version (2026-11-26, written
before anything was built; in git at `abc6be4`) named each gap the directive's phases II to X open in NeuroSciFi. This
version keeps that list and says, for each gap, what closed it, what only narrowed it, and what is still open and
why. "Closed" means a named command or function exists, has tests and a measured figure; it never means the
directive's ambition for that phase is met. Places the small fix lane F5 will change are marked "to be confirmed after fix
lane F5".

## The first screen: what is still open

1. **No model that sees or hears.** The answer model is text-only. Nothing here recognises what a picture shows,
   transcribes speech in a file, or reads text in a picture. Every "cross-modal" relation is a measurement compared
   with a statement. A vision or audio model is an owner decision (a download and a new dependency).
2. **No decoder for compressed images, audio or video.** JPEG, WebP, HEIC, MP3, AAC, FLAC, Ogg and every video codec
   are read for their header or container structure only. A decoder needs a package (Pillow, ffmpeg bindings) or an
   external program on untrusted bytes in a sandbox: owner decisions.
3. **Nothing reaches the answer path.** Every answer in conversation is still sentences. The planner decides forms
   only for owner commands and the console's Make; its held-out verdict does not support connecting it without a new
   examination.
4. **No durable jobs.** A production runs inside the owner's command (60 s by default) and either finishes or leaves
   nothing; nothing resumes, queues or reports progress beyond the console's single in-flight entry.
5. **Pocket, voice and agents see no artifact.**
6. **Real files beyond this host's tools are unmeasured.** The benchmark's files are built from chosen values; the
   integration lane tried a few files made on this host with LibreOffice, ImageMagick and ffmpeg. A camera's JPEG,
   a spreadsheet with merged cells, a PDF with a font map: not tried.
7. **Creative synthesis and knowledge accumulated across modalities are not measured** (two done-criteria stay
   unverified).

## Phase II: taking things in (§5)

| gap (first version) | after | what did it, or why it is open | evidence |
|---|---|---|---|
| II.1 No way in for a file | **closed** for the owner | `nsf inspect`, `nsf artifact add`, `nsf artifact make`; the console upload (off by default) | `tests/artifacts/`, `tests/console/test_console_artifacts.py` |
| II.2 No format detection, no unknown-format answer | **closed** | `artifacts/detect.py` (signatures first) and the capability-discovery answer | unknown family 51 of 51 development, 49 of 49 held-out |
| II.3 Formats decodable with what exists | **closed** for the formats listed | 66 readers within `Limits` and the guard | intake 330 of 337 development, 328 of 339 held-out |
| II.4 Formats that cannot be decoded on this host | **open** | compressed images, compressed audio, video frames, Parquet, HDF5, `.doc`, `.xls`, 7z, RAR: header, structure or identification only; each needs a package or an external decoder (owner decisions) | honest family 52 of 55 (the 3 "cannot" are content questions) |
| II.5 Streaming, resumable and partial processing | **narrowed** | `--partial` uses a file a limit stopped part-way and the made thing says which part (the terminal summary does not yet, and `--partial` on a 5,000-file zip fails: **to be confirmed after fix lane F5**); large sources are made from stated reductions; nothing streams or resumes; a 6-minute WAV is still refused (its samples would need more than 256 MiB) | fix lane F3; `tests/artifacts/test_f3_pipeline.py` |

## Phase III: native representation (§6)

| gap | after | what did it, or why it is open | evidence |
|---|---|---|---|
| III.1 Everything held as text, bits or arrays | **narrowed** | 12 typed representations keep structure (cells, headings, trees, pixels, samples, vertices, graphs, members); lost: an array's dtype and 3-D shape, a DOCX's embedded pixels, video frames | breakdown: 310 of 326 held-out measurements right |
| III.2 No cross-modal binding | **narrowed** | `nsf artifact relate` and the links it records (`consistent_with`, `contradicts`, `describes`, `same_subject`), labelled as the system's claims; only comparisons the question names (58 / 14 / 0 without it); half of held-out report totals "cannot"; no meaning of an image or sound | cross 72 of 72 development, 66 / 6 / 0 held-out |
| III.3 Memory does not hold artifacts | **narrowed, by design not closed** | the artifact store sits beside the individual with forget, restore and owner scope; nothing decoded enters memory, knowledge or beliefs (on purpose: no knowledge from untrusted files without a reviewed path) | ADR-071 |
| III.4 Large artifacts over a long horizon (the directive's §6.4) | **open** | large files are refused, read in part (said so) or reduced (said so); nothing summarises or indexes a large artifact across sessions | code review M5; fix lane F3 |

## Phase IV: choosing the form of a result (§7)

| gap | after | what did it, or why it is open | evidence |
|---|---|---|---|
| IV.1 Nothing decides the form of an answer | **narrowed** | the form planner, with one sentence of why; frozen; held-out: "does not beat the references", overfit, no evidence it reads the words | [CREATIVE_INTELLIGENCE_ASSESSMENT.md](CREATIVE_INTELLIGENCE_ASSESSMENT.md) |
| IV.2 Non-text-first answers | **narrowed** | made results exist for owner commands and the console; answers in conversation are still sentences; Pocket not covered | ADR-076, ADR-079 |
| IV.3 Facts in a non-text artifact are not checked | **narrowed** | every made thing is read back by an independent check before it is kept; since fix lane F4 the check also reports what a person could not see (`visible_to_a_person`), and the benchmark's reader was made stricter on the two hidings both had passed; no person judged legibility | made 14 of 14 development, 13 of 14 held-out |

## Phase V: making things (§8)

| gap | after | what did it, or why it is open | evidence |
|---|---|---|---|
| V.1 No generator of anything but the entity | **closed** for 30 forms | charts, timeline, diagrams, flow, documents, tables, data files, sounds, solids, raster pictures, explorable models; no video, PDF, DOCX, slides or code | `nsf artifact forms`; lanes G1, G2 |
| V.2 Creativity as mechanism, not randomness | **open** | divergence (`forms.alternatives`: candidates that differ in kind) and convergence (technical pass, carried information, planner total, simplicity) exist; nothing measures creativity, and no claim about it is made | ADR-073, ADR-076 |
| V.3 Multi-stage production with repair | **closed** as a pipeline | `artifacts/produce.py`: eleven named steps, at most three repair rounds, budgets enforced; foresight paired with outcome (time estimates 5 to 560 times too high) | ADR-076; `omnimind_int2.json` |

## Phase VI: imagination and world simulation (§9)

**Open.** Nothing simulates a world from an artifact. The pre-production prediction is arithmetic on counts; an
explorable model evaluates a model file the owner gave (a bounded expression language); the existing imagination
commands (exact what-ifs on a copy of the individual) are unchanged. A production's foresight goes to the imagined
store as a SIMULATION record only when `imagine_scenarios` is on.

## Phase VII: learning from experience (§10)

| gap | after | what did it, or why it is open | evidence |
|---|---|---|---|
| VII.1 Learning from artifacts and outcomes | **narrowed** | the per-key majority of the owner's reactions and check verdicts (`form_learning`, off by default); held-out +0.260 / +0.203 over the planner alone, SIMULATED people, 0.092 below the reference majority rule; no knowledge learned from artifacts; a failed check cannot be an outcome without a migration | [RECURSIVE_KNOWLEDGE_VALIDATION.md](RECURSIVE_KNOWLEDGE_VALIDATION.md) |
| VII.2 Recursive gap detection | **closed** as a report | `nsf artifact gaps --report` (read-only; nothing follows from it) | ADR-078 |

## Phase VIII: quality (§11)

**Narrowed.** Fidelity (values read back within tolerance), safety (allow-listed elements, no scripts or outside
references), determinism and the palette are checked for every made thing. Not checked by anything: legibility,
composition, beauty, usefulness to a person. Tick labels shrink to about half size at 360 px (legible, not
comfortable).

## Phase IX: scale and computation (§12)

**Narrowed.** Every decode is bounded (`Limits`, the guarded child process); the store has caps; a production has
time and byte budgets; large sources are reduced by stated rules. Open: durable or resumable jobs; progress; the GPU is
not used; the guard's cost, as measured by fix lane F4: `add` one decode in the child plus about 0.1 s, `make`,
`facts`, `relate` and the console two decodes plus about 0.1 s (a 5,000-file zip add 2.97 s, a crafted PNG 6.86 s);
a six-minute recording is not read; the planner's
time estimates are 5 to 560 times too high (recorded, not corrected: the planner is frozen).

## Phase X: the person's experience (§13)

**Narrowed.** The console's "Made and kept" view shows, makes and keeps artifacts with the entity on its stage, light
and dark, keyboard reachable, axe-clean in Chromium at 1280 and 360 px. Open: Pocket; voice; real screen readers and
real audio output (unverified); browsers other than Chromium; raw meshes outside a made viewer; pictures other than
PNG.
