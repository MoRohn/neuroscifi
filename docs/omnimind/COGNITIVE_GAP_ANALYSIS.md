# Cognitive gap analysis (deliverable 3)

Status: 2026-11-26. The gaps between what the directive's phases II to X ask and what exists on `main` (`8f68beb`,
schema 19). What exists is established in [CAPABILITY_AUDIT.md](CAPABILITY_AUDIT.md) (cited as "audit §n"); the
planned changes are in [IMPLEMENTATION_MATRIX.md](IMPLEMENTATION_MATRIX.md) and the design in
[ARTIFACT_ARCHITECTURE.md](ARTIFACT_ARCHITECTURE.md). Paths are relative to `src/neuroscifi/`.

Every gap is stated the same way:

- **Asks**: what the directive asks.
- **Exists**: what is on `main` today.
- **Root cause**: why it is missing, in the architecture, not "not implemented".
- **Closing it**: what it would take, and which lane or later wave is doing it.
- **Out of reach here**: what cannot be closed on this host without a decision of the owner (a package, a model that
  sees or hears, an external decoder in a sandbox, a change to a security policy), or cannot be closed at all with
  what exists.

Nothing planned is described as done. Wave O-0 is being built and has not been measured; until a lane's work is
merged and its acceptance test has run, every gap below is open.

## The four facts that decide most of what follows

1. **The answer model is text-only** (Qwen3-30B-A3B-Instruct-2507, `Qwen3MoeForCausalLM`; audit §7). No model on
   this host sees an image, hears a sound other than speech-to-text, or embeds anything but text. So "understanding"
   an image or a sound can only mean measurements computed by code from decoded samples (sizes, colours, edges,
   spectra, onsets), never a description of what it depicts or what it sounds like.
2. **No media decoder is available to Python, and nothing may be installed.** The standard library and numpy can
   decode PNG, GIF, BMP, NetPBM, uncompressed TIFF, WAV, AIFF, MIDI, and every text, XML, ZIP and tar based format,
   because those can be written by hand. JPEG, WebP, HEIC/AVIF, MP3, AAC, Opus, Vorbis, FLAC and every video codec
   cannot be decoded without a library or an external program. The host has ffmpeg, ImageMagick, vips, Tesseract and
   LibreOffice as system programs (audit §7), but using a system program on untrusted bytes is a security decision for
   the owner and needs a sandbox; it is not done.
3. **Every unit of meaning in the code is a sentence, a bit or a numeric array.** A claim is one sentence
   (`webcortex/types.py:162`); a knowledge item is a statement string (`instance/schema.py:343`); the model answer is
   `{"sentences": [...]}` (`webcortex/reasoning.py:884` `answer_schema`); only four of 56 tables have a BLOB column
   (audit §3).
4. **The protections are enumerated by hand.** Forget scrubs a listed set of text columns (`instance/governance.py:119`
   `_SCRUB_TABLES`); restore replays a listed set of tombstones (`instance/checkpoints.py:370` `_read_forgets`); the
   imagined-id triggers cover a listed set of tables (`instance/schema.py:35` `_NO_IMAGINED`); settings carried on
   restore are a listed tuple (`instance/checkpoints.py:204`). Every new kind of record must be added to each list by
   hand, and a test catches an omission only if it seeds the new table.

## Phase II: universal input and artifact intelligence (§5)

### II.1 No way in for a file

- **Asks**: accept uploads, local files, streams and connected sources; detect the type; look inside containers;
  process large inputs in parts.
- **Exists**: no route, command or tool takes a file as content (audit §1). The console accepts only JSON objects of
  at most 4 KiB (`console/server.py:34`); Pocket bodies are smaller still; MCP tools take strings. Documents come only
  from the web, gated by the server's declared `Content-Type` (`webcortex/net.py:131-132`).
- **Root cause**: the system was built around one research loop whose input is a question and whose documents are
  fetched pages. Its only document dispatcher, `webcortex/extract.py:966` `extract()`, chooses by declared type and
  decodes everything else as text (`:980-982`); the document kinds a source may have are fixed by a CHECK constraint
  (`web_sources.kind IN ('page','pdf','api','feed','text','json')`, `instance/schema.py:311`). There is no store for
  bytes the owner hands over (the only byte store, the web cache, is disposable by design, `webcortex/cache.py`).
- **Closing it**: a separate chain that does not touch the web path: detection by signature (lane K,
  `artifacts/detect.py`), adapters by family (lanes K, B1, B2), a non-disposable store with forget and restore (lane A),
  and an owner command `nsf artifact add FILE` (lane A). An upload route in the console and Pocket is a later wave
  (Wave O-1), because it changes routes and body limits that the security tests pin.
- **Out of reach here**: nothing in this gap needs the owner, except the later upload route's body limits.

### II.2 No format detection, no unknown-format answer

- **Asks**: identify by signature and structure, never trust a declared type alone; when a format cannot be
  understood, run a reasoned capability-discovery workflow (§5.3) rather than fail or invent.
- **Exists**: only `%PDF-` and `<html` are sniffed (`webcortex/extract.py:974`); declared types are trusted
  everywhere else (audit §2). There is no MIME registry, no signature table and no unknown-format classifier.
- **Root cause**: with one input path for web pages, a declared content type was enough; nothing else needed a type.
- **Closing it**: lane K's `detect.detect` (signatures first, then structure, then declared type and name as hints
  that lose to a signature) and `adapters.inspect_unknown` (entropy, printable share, embedded signatures, record
  regularity, text runs; candidates stated as "consistent with", never as fact; and what would be needed). A gap log
  counts formats that ended undecoded (directive §10.5).
- **Out of reach here**: step 4 to 8 of directive §5.3 ("evaluate approved tooling, add a reusable adapter, register")
  can be done by a person writing an adapter. The system cannot install a decoder for itself: installing anything is
  the owner's decision, and an external adapter package is loaded only when the owner names it (DESIGN §3).

### II.3 Formats that can be decoded with what exists

- **Asks**: documents, tables, trees, code, notebooks, archives, databases, images, sound, meshes, maps, arrays.
- **Exists**: none of these as a file (audit §5).
- **Root cause**: no decoder was ever written; the project's dependency rule keeps the default path to the standard
  library, numpy, zstandard and segno.
- **Closing it**: lanes B1 and B2 write decoders by hand within that rule: CSV/TSV, JSON records, XLSX/ODS (cached
  values, formulas not evaluated), Markdown, HTML (standard-library parser), DOCX/ODT/PPTX/ODP/EPUB (XML in ZIP), RTF
  (text and paragraphs only), PDF through optional `pypdf` (text only), XML, TOML, INI, Python through `ast` (never
  imported or run), notebooks (never executed), ZIP and tar, SQLite read-only and sealed; PNG/APNG, GIF, BMP, NetPBM,
  baseline TIFF, ICO, SVG (geometry, no rendering); WAV, AIFF, AU, MIDI; OBJ, STL, PLY, OFF, glTF/GLB; GeoJSON, GPX,
  KML, WKT; `.npy`/`.npz` with the header checked before numpy reads data.
- **Out of reach here**: see II.4.

### II.4 Formats that cannot be decoded on this host

- **Asks**: images, audio, video, scanned documents, OCR, handwriting, layered design files, CAD, Parquet, Arrow,
  Excel, medical and scientific images "where supported".
- **Exists**: nothing.
- **Root cause**: these formats use codecs (DCT, VP8, HEVC, AV1, MPEG audio, AAC, Opus, Vorbis, FLAC, Snappy) or
  recognition models (OCR) that no installed Python package provides, and writing a codec by hand is outside a lane's
  reach and a large new attack surface.
- **Closing it**: within the rules, only header facts: dimensions, duration, sample rate, channels, tracks, codecs,
  tags (lane B2), then `Unsupported` with `needs` naming what is missing. The benchmark (lane Z, family `honest`)
  counts any content answer for these formats as a fabrication.
- **Out of reach here**: pixels of JPEG, WebP, HEIC/AVIF, PSD layers and compressed TIFF; samples of MP3, AAC, Opus,
  Vorbis and FLAC; frames of any video; OCR and handwriting; Parquet and Arrow data; legacy binary `.doc`/`.xls`/`.ppt`
  content; CAD (STEP, IGES, DWG); YAML beyond its JSON subset; 7z and RAR members. Each needs **an owner decision**:
  either an optional, pinned Python package (Pillow, soundfile, pyarrow, PyYAML ...), or running a system program that
  is already on the host (ffmpeg, ImageMagick, vips, Tesseract, LibreOffice) on untrusted input inside a sandbox with
  no network and bounded resources. Neither is done.

### II.5 Streaming, resumable and partial processing

- **Asks**: streaming ingestion, partial and resumable processing of large inputs.
- **Exists**: voice audio arrives in chunks; nothing else streams. The durable task machinery (lease, checkpoint,
  resume, cancel) exists for research only.
- **Root cause**: `web_tasks.mode` and `status` are CHECK-bound to research values (`instance/schema.py:287-290`), and
  `orchestrator.run` is the only runner (audit §3, task planning).
- **Closing it**: Wave O-0 decodes in memory within `Limits` (64 MiB input, 256 MiB decoded, 20 s) and marks a
  decode stopped by a limit as `partial`; the store writes bytes in 1 MiB chunks (lane A). Durable, resumable
  production jobs are a later wave (a sibling table to `web_tasks`, or a rebuild migration that widens its CHECKs).
- **Out of reach here**: inputs far beyond the limits on a host with about 24 GiB free memory (audit §7) would need
  disk-backed decoding, which no Wave O-0 adapter does.

## Phase III: native multimodal cognitive representation (§6)

### III.1 Everything held as text, bits or arrays

- **Asks**: keep native representations (tables, graphs, scene graphs, tensors, event sequences, binary artifacts)
  and do not flatten them into text.
- **Exists**: claims are sentences, knowledge is statements, JSON from the web becomes indented text and then prose,
  tables on web pages become one cell per line (audit §2). Datasets, epoch streams and graphs are the only typed
  binary values, and they serve the belief core.
- **Root cause**: the type of a memory item is a string at every layer listed in audit-cognition (b), from
  `Claim.text` to the forget scrubber's `instr()` match; the research pipeline's evidence unit is a sentence with a
  citation, which is what makes the sentence guard possible.
- **Closing it**: DESIGN §2's representation classes (`Table`, `Signal`, `Raster`, `Frames`, `Drawing`, `Mesh`,
  `Features`, `Tree`, `Graph`, `Document`, `Bundle`, `Opaque`), each with `describe()` (structure and measurements,
  bounded to 32 KiB, no invented meaning), `shape()` (what a planner needs) and `text()` (the artifact's own words,
  untrusted). They live beside the research memory, not inside it: nothing existing is retyped.
- **Out of reach here**: latent representations of images and sound (no model to produce them).

### III.2 No cross-modal binding

- **Asks**: connect speech and visual events, objects and sounds, code and execution, data and its picture,
  documents and their datasets, through shared entities, timing, causality and provenance.
- **Exists**: nothing relates two kinds of content (audit §2, cross-modal alignment). The knowledge graph's node
  kinds are fixed by a CHECK (`web_claim_links`, `instance/schema.py:450-458`).
- **Root cause**: there is only one kind of content (sentences from documents), so there was nothing to bind.
- **Closing it**: lane A's `artifact_links` (relations `derived_from`, `contains`, `describes`, `same_subject`,
  `same_time`, `consistent_with`, `contradicts`, each with a machine-readable basis); checks that can be computed
  (a report's stated total against a table's column; a chart against its source data by the reading contract; a
  mesh's bounds against a stated box). Cross-modal binding and reconciliation as a mechanism is a later wave; lane
  ZF's `cross` family is its examination, and today's baseline there is "cannot" on every case.
- **Out of reach here**: binding by what an image shows or what a recording says, other than speech-to-text; any
  binding that needs recognising an object or a speaker.

### III.3 Memory does not hold artifacts

- **Asks**: memory that keeps references to originals, native representations, links, transformation history,
  generated derivatives, confidence and feedback, without storing huge files in conversational memory.
- **Exists**: no unit of memory is an attachment, a media object or a link between artifacts (audit-cognition (a)).
  The imagined store accepts a base64 image inside its JSON and forget does not see it (audit §9, C1).
- **Root cause**: provenance allows model or simulation output only in the imagined store (JSON ≤ 64 KiB, never
  rewritten); bytes inside the instance file would inflate every checkpoint and switch imagination off above 512 MiB
  (`instance/store.py:128`).
- **Closing it**: lane A's sidecar byte store (content-addressed, outside the instance file, joined to forget and
  restore) plus one migration with the index, links, events, tombstones and gap counts; ADR-071 settles where a made
  artifact sits in the provenance vocabulary (never evidence). Lane S closes the imagined-payload hole first.
- **Out of reach here**: nothing, apart from the fact that a restore reconciles the sidecar rather than archiving it.

### III.4 Long-horizon understanding of large artifacts

- **Asks**: hierarchical inspection, chunking, temporal segmentation, semantic indexing, lazy loading, progressive
  refinement; summaries never replace the originals.
- **Exists**: the web cache keeps original bytes, but it is disposable (audit §1); semantic vectors exist only in
  process (`webcortex/semantic.py:31`).
- **Root cause**: no persistent index of anything other than statements; no vector store by design.
- **Closing it**: bundles decoded member by member with limits; the store keeps originals; `describe()` and the text
  layer are an index, not a replacement. Semantic indexing of artifact text is not in Wave O-0.
- **Out of reach here**: semantic indexing of images or sound (no model).

## Phase IV: context-driven output intelligence (§7)

### IV.1 Nothing decides the form of an answer

- **Asks**: an output planner that weighs intent, information structure, precision, device, accessibility, editing
  needs and cost, and chooses the medium; explicit requests win; do not ask the person when the context is clear.
- **Exists**: nothing. There is no output-form concept in the code (audit §3, answer form selection).
- **Root cause**: the answer is typed as a list of sentences from the bottom up: the extractive answer items are
  `{text, type: quote|template}` (`webcortex/synthesis.py:1757`), the model's schema is `sentences[]`
  (`webcortex/reasoning.py:884`), and every consumer (console, Pocket's `phone_report`, voice, MCP's `context_pack`)
  reads named text fields through its own allow-list.
- **Closing it**: lane C's `artifacts/forms.py`: a pure, deterministic planner over the request's words (weak
  evidence), the content's shape (strong evidence) and the context (hard constraints), returning every candidate's
  scores, the choice, what decided it and one plain sentence of reason. Wave O-0 calls it only from a command
  (`nsf form plan`); its use in the answer path, behind a setting `output_forms` (`off` / `offer` / `auto`), is a
  later wave. Lane ZF's `forms` family is the examination; the reference it must beat is "content shape only".
- **Out of reach here**: turning on any automatic choice in the answer path is **the owner's decision**.

### IV.2 Non-text-first answers

- **Asks**: answer with minimal prose when another medium serves better.
- **Exists**: only prose reaches a person (audit §4). Clients render text nodes only; the console's CSP has
  `img-src 'self'` and no `media-src` or `frame-src` (`console/server.py:56-57`).
- **Root cause**: as IV.1, plus the clients' rendering rule (text only) and the security policy, both deliberate.
- **Closing it**: generators and validators (lanes G1, G2) make the artifacts; showing them in the console and Pocket
  is a later wave that must not weaken either policy (DESIGN §8: SVG rebuilt through an allow-list importer, a
  first-party runtime module, same-origin images, Web Audio).
- **Out of reach here**: any CSP change is **the owner's decision**.

### IV.3 Facts in a non-text artifact are not checked

- **Asks** (implied by §11 precision and §3 "no unsupported claims"): what a chart or a table states must be as
  correct as a sentence.
- **Exists**: the sentence guard checks numbers, units and entailment per sentence (`webcortex/reasoning.py:3012`);
  it has no notion of a cell, a datum or an edge.
- **Root cause**: the guard's unit is a sentence string with claim ids.
- **Closing it**: in Wave O-0 no model writes data: every made artifact is drawn by code from data the owner gave or
  a validated specification, and the validators recover the data from the bytes and compare. A model-written datum
  would need binding to a claim id and the guard's number and unit rules per datum; that is not planned in Wave O-0.
- **Out of reach here**: nothing, as long as no model writes data.

## Phase V: generative creativity and artifact production (§8)

### V.1 No generator of anything but the entity

- **Asks**: an extensible production framework for visual, audio, video, document, software, spatial, scientific and
  hybrid artifacts.
- **Exists**: the Presence entity (SVG from a closed spec), the graph heatmap, QR codes, speech (audit §6).
- **Root cause**: nothing in the system had to produce a file; the one non-text renderer was built for the entity
  only (14 fixed path slots, a closed vocabulary; `presence/scene_spec.py`).
- **Closing it**: the entity's pattern, generalised: a typed specification validated before use, a fixed generator
  that turns it into bytes deterministically, an independent validator that re-reads the bytes (lanes K, G1, G2).
  Wave O-0's forms: charts (line, bar, scatter, histogram, heatmap, small multiples), timeline, node-link and tree
  diagrams, steps, tables, report documents (HTML, Markdown), data files (CSV, JSON), declaratively animated SVG,
  tones, sequences and sonified series (WAV), solids and surfaces (STL, OBJ), raster images and animated PNG from
  numeric grids, explorable models and step-throughs (one self-contained HTML document with a fixed, hashed runtime).
- **Out of reach here**: **video** (no encoder is used by the product; ffmpeg on the host could encode frames the
  system itself made, a later wave and an owner decision); **pictures of things** (illustrations, photographs,
  "cinematic scenes": no image-generation model is on the host); **original music and sound design** beyond tones and
  sonification (no audio-generation model); **speech files** (the voice engines exist, but Wave O-0 does not call
  them); **mobile prototypes, generated applications and APIs** (no model writes code that the system would run, by
  rule: generated code is never executed); **CAD-compatible assets** (STEP, IGES: no writer). Each of the model-based
  items needs **a model the owner chooses to install**, with its memory cost on a host that has about 24 GiB free.

### V.2 Creativity as mechanism, not randomness

- **Asks**: conceptual blending, analogy, divergent exploration with convergence, aesthetic and functional evaluation;
  avoid near-identical variants.
- **Exists**: nothing for artifacts. The answer model could propose ideas but only as text, and Wave O-0 uses no model.
- **Root cause**: the system's one generative component (the answer model) is fenced by the sentence guard to restate
  evidence; nothing else generates.
- **Closing it**: lane C's `forms.alternatives(plan, n)`: candidates that differ in kind (a measure of difference
  between forms, tested), ranked; convergence by the planner's stated criteria and the validators' verdicts. This is
  divergence over forms, not over ideas.
- **Out of reach here**: conceptual blending, analogy and novel hypotheses would need a model to propose them; a model
  may only suggest and code decides (DESIGN §5). Aesthetic evaluation can only be measurable rules (overlap, contrast,
  palette, density); taste is not measurable here, and a model judging its own output is forbidden as a gate.

### V.3 Multi-stage production with repair

- **Asks**: interpret → conceptualise → explore → choose → plan → generate → render → evaluate → repair → finalise,
  keeping intermediate states and editable sources.
- **Exists**: nothing.
- **Root cause**: as V.1.
- **Closing it**: Wave O-0 gives the pieces: the spec is the editable source and is stored with the artifact; repair
  is bounded and rule-based (at most three rounds, recorded in the spec's history, never changing the data; lanes G1,
  G2). The pipeline that runs them in order, with pre-production checks, is a later wave.
- **Out of reach here**: "render" for HTML documents in a browser is optional (Chromium is an optional extra).

## Phase VI: multimodal imagination and world simulation (§9)

- **Asks**: build internal models before making artifacts: simulate camera movement, UI interaction, physics,
  synchronisation and spatial relationships; keep simulation apart from verified reality.
- **Exists**: exact what-ifs over the binary belief model (`instance/imagine.py:73`), linear-Gaussian causal questions
  (`causal.py`), graph forecasts; all labelled and quarantined (audit §3). Nothing simulates a picture, a scene, a
  sound or an interface.
- **Root cause**: the imagination built in Cognitive Genesis operates on the belief core's bits and the causal
  engine's linear models; there are no models of space, motion or appearance to run.
- **Closing it**: lane C's `forms.foresee`: cheap arithmetic before anything is made (more series than line styles,
  labels that will overlap, points per pixel, a sound longer than an attention bound), labelled as estimates; a later
  wave compares them with what validators find. Lane G2's explorable models run a declared difference equation in a
  tiny, checked expression language, which is a simulation of a model someone wrote down, not a learned world model.
- **Out of reach here**: generative world models, physical simulation beyond declared equations, camera and scene
  simulation (no such model on this host; training one is not allowed without the owner).

## Phase VII: recursive multimodal knowledge evolution (§10)

### VII.1 Learning from artifacts and outcomes

- **Asks**: learn facts, procedures and design patterns from documents, images, video, audio, code, data and
  outcomes; transfer across modalities; Create → Inspect → Critique → Test → Compare → Improve → Validate → Learn.
- **Exists**: a learning mechanism for one choice: Thompson sampling over the nine research modes, promoted only by a
  gate on held-out data, off by default (`webcortex/strategies.py:107`); an append-only improvement ledger. Cognitive
  Genesis measured the knowledge loop and found no held-out gain from its evolution mechanisms
  ([../genesis/FINAL_EVALUATION.md](../genesis/FINAL_EVALUATION.md)).
- **Root cause**: the strategy arms are hard-wired to the research modes; the only outcome signals are research
  outcomes; there is nothing else to learn about because nothing else is produced.
- **Closing it**: lane C leaves a seam (`forms.plan(..., adjustments=)`, bounded, `None` by default). Learning form
  choices from outcomes that come from outside the system (the owner kept, dismissed or replaced a form; a validator
  passed or failed) is a later wave, stored as procedural memory, off by default. Lane ZF's `learn` family measures
  adaptation to **simulated** preferences against a per-key majority rule and an oracle; it does not measure benefit to
  a real person.
- **Out of reach here**: model-weight updates (no training without the owner, and no memory for it on this host);
  learning from images or sound beyond their measurements; any evidence that knowledge "transfers across modalities"
  until two modalities feed one learned choice and a benchmark shows a gain on held-out cases.

### VII.2 Recursive gap detection

- **Asks**: notice frequently unsupported artifacts, repeated generation failures, excessive reliance on text.
- **Exists**: reflection flags underperforming research modes only, and is off (`webcortex/reflection.py:249`).
- **Root cause**: as VII.1.
- **Closing it**: the gap log (lane K's hook `adapters.on_gap`, lane A's counts per format, `nsf artifact gaps`) is
  the evidence for "which formats are missing most". It records; it decides nothing.
- **Out of reach here**: acting on a gap by installing a decoder: the owner's decision.

## Phase VIII: high-fidelity artifact quality (§11)

- **Asks**: modality-specific validation (image, layout, video, audio, code, application, data, document, 3-D,
  numerics, accessibility, format integrity) using rendered previews, execution and structural inspection; not an LLM
  judging its own work.
- **Exists**: the sentence guard (prose) and the entity's two validators (audit §4, validation).
- **Root cause**: there were no artifacts to validate.
- **Closing it**: validators written from the format, never from the generator (a test enforces that `check` never
  imports `make`): SVG safety and structure, carried information recovered from geometry and tick labels, layout and
  contrast in both themes, the palette rule; document structure and placeholders; data round-trips; WAV pitch,
  clipping, clicks; STL/OBJ volume, watertightness and orientation; PNG pixels back to values; the explorable
  document's exact script and policy hashes (lanes G1, G2). Deliberately wrong artifacts in the tests must be caught.
  Benchmark readers written independently again (lane Z).
- **Out of reach here**: perceptual quality of images and sound (no model that sees or hears); temporal consistency
  of video (no video); "an LLM judging its own generation" is excluded by rule, not by lack of means. Browser-level
  checks (console errors, keyboard reachability, axe) only where Chromium is present, and they never decide `ok` alone.

## Phase IX: output scale, performance and computational orchestration (§12)

- **Asks**: chunked and streaming production, resumable tasks, checkpointing, resource-aware scheduling, parallel
  rendering, content-addressable storage, artifact dependency graphs; use the DGX Spark well; match effort to need.
- **Exists**: durable research tasks; a background gate, two model slots and an engine gate that refuses only at
  critical memory (`resources/policy.py:256`); budgets in network, pages, bytes, seconds and tokens, not CPU, GPU or
  disk; no response size cap on the console or Pocket (audit §9, H5, O5).
- **Root cause**: the runtime was sized for one research loop and a model server. GPU memory is not readable on GB10,
  so the monitor sees only available system memory; the installed systemd units cap services at 1–4 GiB with
  `MemoryDenyWriteExecute=yes`, which would kill Chromium, node or ffmpeg.
- **Closing it**: Wave O-0 bounds every decode and make by `Limits` and the spec; the store is content-addressed
  with a total cap (2 GiB by default) and derivation links (lane A). Durable production jobs with progress, cancel
  and resume, and admission control for heavy work, are later waves.
- **Out of reach here**: rendering video or large 3-D scenes; running a second large model (a vision or audio model)
  beside the 44 GiB answer model on a host with about 24 GiB free; "multi-node offloading" (Pocket devices are not
  compute nodes). Directive §12.2 itself says a single Spark cannot run every model; that is the case here.

## Phase X: multimodal user experience (§13)

- **Asks**: artifacts shown in the existing minimalist interface; the entity stays central; adaptive presentation
  (canvas, timeline, 3-D viewer, notebook, dashboard, media player, app preview, workspace); light and dark;
  device-specific; occasional unasked non-text presentation when it clearly helps.
- **Exists**: no view shows any artifact. Pocket drops even the report's tables (`pocket/server.py:497`
  `phone_report`); voice speaks at most two sentences (audit §4).
- **Root cause**: the clients' rule that untrusted content is text nodes only; the CSPs; bearer-header
  authentication, which an `<img>`, `<audio>` or `<iframe src>` request cannot carry (audit-outputs §4).
- **Closing it**: a later wave (O-1): the explorable runtime mounted as a first-party module, SVG rebuilt through an
  allow-list importer, raster images from a short-lived same-origin capability address, sound through Web Audio;
  Presence as the hero; the e2e matrix (`tests/e2e/test_ui_matrix.py`, `tests/e2e/test_a11y_tree.py`) extended.
  "Creative spontaneity" is the planner's `offer` mode, off by default.
- **Out of reach here**: anything that needs a weaker CSP or an iframe that runs generated script on the app's
  origin; both are **the owner's decision** and not planned.

## Directive §18: where each item of the definition of done stands

| Item | Today |
|---|---|
| Existing functionality inventoried and preserved | inventoried ([CAPABILITY_AUDIT.md](CAPABILITY_AUDIT.md)); preservation is checked per lane (offline digest, reproduction, guard differential, full suite) |
| Ingestion extensible beyond a fixed list | open: being built (lanes K, B1, B2); the external-adapter rule is designed |
| Native information retained | open: being built (DESIGN §2 representations; lane A) |
| Cross-modal relationships represented and reasoned over | open: links are built in Wave O-0 (lane A); reasoning over them is a later wave |
| Context-driven output selection operational | open: the planner is built in Wave O-0 (lane C) but is not used by the answer path |
| Non-text responses where useful | open: generators being built; showing them is a later wave |
| Artifact generation produces usable, validated results | open: being built (lanes G1, G2) |
| Creative synthesis demonstrably improved | open; only form diversity and validator results can be measured here |
| Complex multi-file artifacts through reliable workflows | open: later wave |
| Large workloads managed through resource-aware execution | open: limits in Wave O-0; durable jobs later |
| Knowledge accumulated across modalities | open: later wave |
| Recursive improvement produces measurable benefits | open: later wave; measured on simulated preferences only |
| New capabilities integrate with existing cognitive systems | open |
| Original features remain operational | checked per lane as above |
| Security and resource boundaries enforced | lane S is fixing nine of the audit's defects first (not yet merged); every lane works under the hard rules |
| End-to-end tests and benchmarks support claims | the benchmarks (lanes Z, ZF) are being written before the mechanisms |
| Rollback and recovery for significant changes | every Wave O-0 feature is off or command-only; the one migration cannot be undone by an older build (see the implementation matrix) |
