# Multimodal capability audit (deliverable 1)

Status: 2026-11-26. The baseline before Project OMNIMIND: what NeuroSciFi takes in, how it holds and reasons over
it, and what it gives back, today. Code: `main` at `8f68beb` (schema 19, package 0.7.0). This document merges the four
Phase I audits (input acquisition and understanding; cognition and memory; output and delivery; runtime and safety).
The audits were written against the Cognitive Genesis Wave 4 branch (`97f7abc`); every file:line below was checked
again on `main`. Where a line moved, the line on `main` is given; facts that exist only on the unmerged Wave 4 branch
(`webcortex/caution.py`, `webcortex/emitted.py`, ADR-064 to ADR-067) are left out or marked "on the unmerged Wave 4
branch". Paths are relative to `src/neuroscifi/` unless they start with `tests/`, `docs/`, `deploy/`, `scripts/` or
`.github/`.

Each pathway has exactly one status word, as directive §4 asks:

| Word | Meaning here |
|---|---|
| Fully operational | does what it is for, on `main`, with a test or a probe that ran |
| Partially implemented | works, but covers only part of what the directive's category asks (for example text but not layout) |
| Unsupported | no code does it |
| Underperforming | works, but below a stated rule or a reasonable standard for its purpose |
| Unverified | code exists, but nothing in Phase I exercised it on this host (a live engine not contacted, a service not running) |

"Probe" means a throwaway script an auditor ran; "test" means a test in `tests/` that an auditor ran or that exists and
covers the line. Live model, speech and search services were not contacted by any audit (the rules forbade it).

## 0. The short answer

- **Nothing takes in a file.** No command, route or tool accepts a local file, an upload, a paste or a drop as a
  document. Documents arrive only from the public web by URL (HTML, the text layer of a PDF, plain text, JSON, XML
  feeds). Speech arrives as live microphone audio and leaves the speech engine as English text. The belief core takes
  one-bit readings and small numeric arrays. Pocket sends one-word labels computed on the phone.
- **Nothing decodes an image, a sound file, a video, an office document, a spreadsheet, a source tree, a mesh or a
  map.** No Python library for it is installed. "Camera" in the belief core is the name of a channel that carries one
  bit, not image data (`instance/schema.py:147`).
- **Every answer is sentences.** The research report is a JSON dict of quoted or templated sentences with citations,
  plus two small tables built by code. The optional model-written answer is at most 8 guarded sentences. Nothing
  chooses the form of an answer: there is no concept of an output form, an attachment or an artifact anywhere in the
  code.
- **One thing already makes a non-text result from a declared, validated specification:** the Presence entity
  (`presence/scene_spec.py`: a typed, bounded, versioned JSON spec; validated on the server and again in the browser;
  a canonical fallback on failure). OMNIMIND's design generalises exactly this pattern
  ([ARTIFACT_ARCHITECTURE.md](ARTIFACT_ARCHITECTURE.md)).
- **The answer model is text-only** (Qwen3-30B-A3B-Instruct-2507 FP8, `Qwen3MoeForCausalLM`). No model on this host
  sees images or hears sound beyond speech-to-text.
- **The machinery around cognition is rigorous**: forget, restore, the quiet switch, provenance, the imagined-store
  quarantine, the sentence guard, size and time bounds on every web input. It is enumerated by hand per table and
  per kind, so every new record type must join about eight hand-kept lists.

## 1. Input acquisition

| Pathway | Accepts | Where | Inside, and what is lost | Limits and failure | Dependencies | Status | Evidence |
|---|---|---|---|---|---|---|---|
| Uploads (console) | nothing: no multipart, no file input, no paste or drop handler | `console/server.py:34-36` (JSON bodies ≤ 4 KiB, 8 KiB for two routes) | — | every POST must be a JSON object | stdlib | Unsupported | grep of `console/web/*` for `type="file"`, `FileReader`, `paste`, `drop`: no hit (audit-inputs §1f) |
| Uploads (Pocket) | one screenshot file, reduced in the browser to a 3-valued label; a device-metrics receipt ≤ 1 KiB | `pocket/web/field-ui.mjs:181-200`, `pocket/web/field-model.mjs:48-55` | only the label leaves the phone; the image is resized to 32×32 and zeroed | browser-declared `file.type` trusted (extension-derived), ≤ 8 MB | browser | Partially implemented | code read (audit-inputs §1e) |
| Local filesystem as content | none: no command reads a text, Markdown, PDF, image, audio or CSV file as a document | — | — | — | — | Unsupported | audit-inputs §1a, every CLI handler listed |
| Local files the CLI does read | `.npz` epoch streams, `.npy` matrices, NSFA archives, NSG1 graphs, JSON rule and causal-question files, a prototype SQLite file | `instance/cli_evidence.py:27-43`, `files_cli.py:11-44`, `instance/cli_graphs.py:35`, `instance/cli.py:166`, `instance/legacy.py` | numeric arrays (`.npy` → NSG1 is lossy by design: top entries per row, 4/8-bit); archives byte-exact | `.npz` validated only after numpy has loaded it; three whole-file reads have no size bound; bad files give Python tracebacks (see §9, defects I1, I2, I12) | numpy, zstandard | Partially implemented | probes `a1/` (audit-inputs §1a) |
| Bounded file reader | a regular file up to a limit; no symlink follow, no FIFO or device | `resources/shared.py:113` `read_text` | text | used by `nsf imagine scm`; not used by the whole-file reads above | stdlib | Fully operational | code read |
| Device sensors (server) | none: "camera", "word" and "reference" are names of channels that carry one bit | `instance/evidence.py`, CHECK at `instance/schema.py:147` | one bit per observation | any other channel refused by the CHECK | stdlib sqlite | Fully operational (for bits only; no sensor data) | probe: `channel='image'` → IntegrityError (audit-cognition §1) |
| Cameras (Pocket) | one 320×240 frame drawn into a 1×1 canvas → `dim/midrange/bright` | `pocket/web/mobile.js:176-189` | one 3-valued label; the browser's downscale is not a true mean (inference) | — | browser | Underperforming | code read; crude by design |
| Microphones (console voice) | live speech: AudioWorklet resamples channel 0 to 16 kHz PCM16 by linear interpolation (no anti-alias filter); chunks ≤ 64 KiB | `voice/web/capture-worklet.mjs`, `voice/web/voice-client.mjs`; route `voice/http.py` | PCM in memory; audio dropped after transcription unless the owner turns on `retain_audio` (`voice/config.py:30`, default off) | ≤ 64 KiB per chunk (`voice/hub.py:65`); utterance cut at 30 s **without an error** (`voice/hub.py:66,710-712`) | browser | Partially implemented | `tests/voice/test_http_engines.py` (ran, audit-inputs §1d); aliasing effect unmeasured |
| Microphones (Pocket) | sound level and spread → `soft/moderate/strong`, `steady/variable/wide`; on-device speech recognition limited to four words | `pocket/web/mobile.js:155-271` | only the label leaves the phone | — | browser | Partially implemented | code read |
| Streaming interfaces | voice audio chunks; Presence and voice events by long-poll | `voice/http.py`, `presence/routes.py` | — | no SSE or chunked responses | stdlib | Partially implemented (speech only) | audit-outputs §1E |
| Connected applications (MCP) | JSON-RPC text tools: `web_search`, `web_research`, `web_task`, `web_evidence`, `web_knowledge`, `self_status` | `webcortex/mcp.py` | text strings only; no file, image or audio arguments; results are one text content item | strict schemas, unknown fields refused, rate caps | stdlib | Partially implemented (text only) | code read (audit-inputs §1g) |
| External APIs | Wikipedia, arXiv, OpenAlex, Crossref, PubMed, PyPI, npm, OSV, GitHub releases; SearXNG and others for discovery | `webcortex/providers/` | records turned into text snippets and templated claim sentences | per-provider caps; `purpose='api'` never cached | network services | Unverified (live calls not made) | code read |
| Network resources (web fetch) | `text/html`, XHTML, `text/plain`, JSON, XML, Atom, RSS, `application/pdf` | allow-list `webcortex/net.py:131-132` | bytes, decided by the **server's** `Content-Type` (no sniffing at the gate) | anything else → `unsupported_type` before the body is read (so CSV, Markdown, images, audio, video, DOCX, ZIP are refused); page ≤ 5 MB truncated and flagged (`webcortex/config.py:119`), API ≤ 16 MB; 15 s; SSRF policy, DNS pinning, robots | stdlib | Fully operational (as a gate) | `tests/webcortex/test_net.py` type tests (ran, 5 passed) |
| Structured databases | a prototype `legacy_live.py` SQLite with two fixed tables, read-only | `instance/legacy.py` | rows copied as evidence | sqlite errors → named error | stdlib | Partially implemented (one fixed shape) | code read |
| Datasets | `(states, targets)` arrays | `instance/datasets.py:79` `add` | npz BLOB with sha256 | **no CLI or HTTP caller**: a real dataset cannot be imported | numpy | Unsupported (as an input path; the store itself works) | `grep datasets.add`: acceptance scenarios only (audit-inputs §1a) |
| Existing document and artifact integrations | the public web cache: raw downloaded bodies, content-addressed | `webcortex/cache.py:76` `PublicCache`, `:169` `put`, `:180` `body` | bytes kept; claims keep `doc_sha256` | **disposable**: pruned at 512 MiB or 30 days (`webcortex/config.py:120-121`), recreated empty when corrupt; never in a checkpoint | stdlib sqlite | Partially implemented (a claim can outlive its bytes) | code read |

## 2. Understanding

| Pathway | Accepts | Where | Inside, and what is lost | Limits and failure | Dependencies | Status | Evidence |
|---|---|---|---|---|---|---|---|
| File-type detection | — | the fetch gate trusts the server's type; `extract()` sniffs only `%PDF-` and `<html` (`webcortex/extract.py:974`); Pocket trusts the browser's `file.type`; the voice route trusts `audio/l16` | — | no MIME registry, no signature table, no container introspection, no unknown-format classifier | — | Unsupported (as a general mechanism) | audit-inputs §2 end |
| HTML → text | fetched HTML | `webcortex/extract.py:599` `extract_html` | main text, one line per block element; **tables flattened to one cell per line, image `alt` dropped, SVG removed with its text, MathML flattened to tokens**; heading levels kept only in the Markdown view, which is **not persisted** | ≤ 50 000 elements, ≤ 500 000 characters; hostile markup refused before parsing | scrapling, lxml, markdownify, trafilatura (extras, lazy) | Partially implemented | probe output in audit-inputs §1c |
| JavaScript rendering | a page needing scripts | `webcortex/browser.py:163` `render` | rendered DOM → the same text extraction; images, media, fonts, styles, frames blocked; no screenshot | off unless the budget has browser sessions; timeout + SIGKILL watchdog; refused at critical memory (`webcortex/orchestrator.py:1348`) | playwright + Chromium (extras) | Partially implemented (text only) | `tests/webcortex/test_browser.py` (one test ran) |
| PDF | fetched PDF | `webcortex/extract.py:821` `extract_pdf` | text of the first 30 pages joined by blank lines, ≤ 500 000 characters; title, author, dates, DOI; **no layout, columns, tables, figures, equations, images or OCR; a scanned PDF gives empty text** | encrypted → `decode_error`; any pypdf exception → `decode_error`; **no wall-clock bound** on extraction | pypdf (extra `web`) | Partially implemented | `tests/webcortex/test_extract.py -k pdf` (2 passed) |
| JSON, XML | fetched JSON, XML | `webcortex/extract.py:966-1014`; feeds via `webcortex/safety.py` expat | JSON re-serialised as indented text and then treated as prose (types and structure lost); feeds parsed for items; other XML → concatenated text (element names, attributes, hierarchy lost) | any DOCTYPE or entity refused | stdlib | Partially implemented | probe (audit-inputs §1c) |
| Any other bytes given to `extract()` | anything | `webcortex/extract.py:980-982` | decoded as text with replacement characters: a PNG becomes "text" mojibake | only the fetch gate prevents this | — | Underperforming (no refusal of binary content) | probe `a1/probe_extract.py` |
| Classification of requests | a research question | `webcortex/intent.py:523` `assess` | nine research modes, freshness, kinds, entities; English regex cue tables | other scripts kept verbatim | stdlib | Fully operational (research classes only) | code read |
| Classification of voice commands | an utterance | `voice/commands.py:328` `parse` | about 20 command kinds | regex grammar | stdlib | Fully operational | code read |
| Embedding (semantic retrieval) | knowledge statements and the question | `webcortex/semantic.py`, `inference/gateway.py:587` `embed` | vectors held **only in this process**, keyed by statement text; "there is no vector store" (`webcortex/semantic.py:31`) | off until configured; falls back to lexical ranking; strings only | numpy; embed/rerank endpoints (optional) | Unverified (the `neuroscifi-memory` service is not running on this host; code and tests with stubs exist) | `docker ps` in audit-runtime §0 |
| Perception (images) | — | none on the server | — | — | — | Unsupported | audit-inputs §2 |
| Perception (speech) | 16 kHz mono PCM16 | `voice/engines.py:162` `transcribe` → Whisper-compatible server on loopback | one whitespace-collapsed English string; **language fixed to `en`**, no timestamps, word confidences, speakers or language identification | 2 s connect / 20 s read, one retry, cool-down | container `neuroscifi-voice-asr` (Whisper large-v3-turbo) | Partially implemented (speech → English text only; live quality not measured by any audit) | `tests/voice/` with fake servers |
| Perception (research bench) | synthetic 24×24 RGB blobs and 256-sample tones | `lab/sensory.py:15-55` | 16 tile features / 12 spectral bands | never reads a file | numpy | Partially implemented (synthetic arrays only; not an input path) | code read |
| Semantic extraction | a fetched document | `webcortex/claims.py:1037` `extract_claims` | one source sentence per claim (`webcortex/types.py:162` `Claim.text: str`), 6 to 80 words (`webcortex/claims.py:48-49`); numbers with units (`webcortex/text.py`) | lexical, deterministic | stdlib | Fully operational (text only) | code read |
| Prompt-injection screen | text | `webcortex/safety.py:132` `scan_injection`, `:171` `quarantine` | flagged sentences removed; never become claims | English-language regex patterns; semantic or non-English injections pass (inference); images, audio and PDF metadata not covered | stdlib | Partially implemented | code read |
| Spatial reasoning | — | none | — | — | — | Unsupported | grep |
| Temporal interpretation | dates in documents; validity intervals of knowledge; staleness | `webcortex/temporal.py`, `web_knowledge` validity | dates and intervals over statements | text dates only; no time-indexed media | stdlib | Partially implemented (statements only) | code read (audit-cognition §1) |
| Cross-modal alignment | — | none: no code relates two kinds of content | — | — | — | Unsupported | grep: no "modality" or "multimodal" in any identifier or comment (audit-inputs headline) |

## 3. Cognition

| Pathway | Accepts / produces | Where | Inside, and what is lost | Limits and failure | Dependencies | Status | Evidence |
|---|---|---|---|---|---|---|---|
| Context construction | the claims of a report → the model's evidence bundle | `webcortex/reasoning.py:792` `bundle` | ≤ 12 claims (`webcortex/reasoning.py:110`), quotes bounded, in an untrusted JSON envelope; quarantined content dropped | text only | stdlib | Fully operational (text) | `tests/webcortex/test_imagined_quarantine.py` (ran) |
| Context pack for agents | a report → `context_pack` | `webcortex/synthesis.py:2115` | top items within a token budget (default 2000); the model-written answer, verdict, contradictions and tables are not in it | — | stdlib | Fully operational | code read |
| Memory (instance store) | rows in 56 tables, 47 triggers | `instance/store.py`, `instance/schema.py:131` `MIGRATIONS` | relational; BLOB columns in exactly four tables (`datasets`, `epoch_streams`, `graph_revisions`, `voice_audio`); everything else TEXT, REAL, INTEGER or JSON-in-TEXT | writes refused under regulation (`instance/store.py:618` `require_write_allowed`); a newer schema refused (`instance/schema.py:948-950`) | stdlib sqlite | Fully operational | probe `a2/probe_schema.py` |
| Memory retrieval | 14 kinds, by id, scope and time | `instance/memory.py:218` `retrieve`, `_TABLES` `:180` | one envelope per record; "association or embedding search is out of scope" (`instance/memory.py:4`) | unknown kind → ValidationError | stdlib | Fully operational | `tests/instance/test_memory.py` (ran) |
| Web knowledge | versioned statements per claim key | `webcortex/knowledge.py:279` `query` | a statement string, values, sources, validity; ranked by BM25 | no non-text content | stdlib | Fully operational (text) | code read |
| Imagined store (quarantine) | simulations, speculations, inferences | `instance/imagined.py:232` `record` | JSON payload ≤ 64 KiB, never rewritten, ≤ 365 days, never evidence; listings labelled "imagined, not evidence" (`provenance.py:53`) | a base64 image inside the JSON is accepted, and forget does not look inside it (defect C1, §9) | stdlib | Fully operational; empty by default | probe; `tests/instance/test_imagined.py` |
| Reasoning (extractive) | a question and its sources → a report | `webcortex/synthesis.py:1757` `synthesize` | answer items quoted or templated; findings; contradictions; a two-item comparison table; a methodology table (scientific mode) | ≤ 5 answer items; no model | stdlib | Fully operational (text with structure) | `tests/webcortex/test_synthesis.py` (exists) |
| Reasoning (model + sentence guard) | the bundle → ≤ 8 sentences | `webcortex/reasoning.py:3263` `apply`, `:3012` `guard`, `:2344` `check_sentence` | sentences with claim ids; every number and unit must appear in a cited claim; content words must be entailed | any failure keeps the extractive answer; **the guard has no notion of a table cell, a chart datum or a diagram edge** | chat model (optional) | Fully operational (text only) | `tests/inference/test_guard_gaps.py` (ran); `scripts/guard_differential.py` |
| Reasoning over beliefs | one-bit readings → posteriors, next step | `instance/beliefs.py`, `instance/executive.py`, `instance/information.py` | binary hidden states | binary domain only | numpy | Fully operational (binary domain) | module docs |
| Predictive modelling | graph forecasts; causal questions on linear-Gaussian models | `instance/forecast.py`, `causal.py` | numbers; answers labelled SIMULATION | refused beyond a horizon or when unstable; only linear-Gaussian models | numpy | Fully operational (narrow) | `tests/test_causal*.py` (exist) |
| Imagination | exact what-ifs on a sandbox copy | `instance/imagine.py:73` `sandbox` | an in-memory copy of the whole file | refused above `imagine_max_bytes` (512 MiB, `instance/store.py:128`) — so bytes stored inside the instance file would switch it off | numpy | Fully operational (binary domain) | code read |
| Task planning | research tasks with lease, checkpoint, resume, cancel, budget | `webcortex/scheduler.py:684` `claim_next`, `:837` `work`; `web_tasks` | JSON budget (network, pages, bytes, seconds, tokens; not CPU, GPU or disk) | `mode` and `status` are CHECK-bound to research (probe: mode `artifact` refused) | stdlib | Fully operational for research; Unsupported for any other job | probe (audit-cognition §1) |
| Strategy selection | Thompson sampling over the nine research modes | `webcortex/strategies.py:107` `sample` | key-value JSON state | off until promoted by a gate | stdlib | Partially implemented (inactive at the defaults) | code read |
| Reflection / gap detection | a period report | `webcortex/reflection.py:249` `scheduled` | ids and numbers | off by default; flags underperforming research modes only | stdlib | Partially implemented (off; research only) | code read |
| Selective broadcast | two event kinds | `broadcast.py:113` `offer` | a salience number and a decision | off by default | stdlib | Partially implemented (display only at the defaults) | code read |
| Tool orchestration | 3 allow-listed model tools (`knowledge_query`, `research_task`, `web_research`) | `inference/tools.py:97` `run_with_tools`, `:146` `TOOLS` | tool output JSON as untrusted data, cut at 6000 characters | ≤ 3 rounds; off by default | stdlib | Fully operational (opt-in) | `tests/inference/` |
| Structured output | a JSON-schema subset | `inference/gateway.py:821` `structured`; `inference/jsonschema.py` | parsed and checked locally | one repair retry | stdlib | Fully operational | used by `presence/scene_spec.py:161` `propose` |
| Creative generation | a concept word → an entity scene | `presence/scene_spec.py:161` `propose` (Tier C) | a closed spec: 6 bodies, 4 eyes, 7 motions, 1–6 elements from 18 primitives | off unless the browser pref is on; console only; asserts no fact | gateway | Partially implemented (opt-in, entity-only) | `tests/presence/test_routes.py` (ran) |
| Answer form selection | — | none: no output-form, modality, media-type or artifact concept in `src/neuroscifi` | — | — | — | Unsupported | grep (audit-cognition §1) |

## 4. Output

| Pathway | Produces | Where | Inside, and what is lost | Limits and failure | Dependencies | Status | Evidence |
|---|---|---|---|---|---|---|---|
| Content generation (research report) | one JSON dict per task, stored as `web_tasks.result`; no streaming | `webcortex/synthesis.py:1757`; called by `webcortex/orchestrator.py` | quoted or templated sentences, citations, two code-built tables; "Extractive synthesis: statements are quoted from sources, not generated" appended (`webcortex/synthesis.py:1920`) | no chart, image or numeric-series field exists | stdlib | Fully operational | consumed by every client |
| Content generation (model) | ≤ 8 guarded sentences | `webcortex/reasoning.py:3263` `apply` | prose only | the extractive answer is never changed | model (optional) | Fully operational (with a configured model) | `tests/inference/` |
| Content generation (`nsf llm ask`) | free model text, labelled "Model output, not checked against evidence" | `inference/cli.py` | not guarded | tools allow-list | model | Fully operational (as labelled) | `tests/inference/test_inference_tools.py` (exists) |
| Rendering (console) | DOM built from text nodes; two tables; lists | `console/web/console.js` (`h()`/`s()`; the one `innerHTML` at `:69` reads, never writes) | no chart, image, audio element or canvas in the answer view | bearer token | none | Fully operational | e2e `tests/e2e/test_console_research.py` (exists) |
| Rendering (Pocket) | `phone_report` JSON rendered with `textContent` | `pocket/server.py:497` `phone_report` | **comparison, methodology, findings dropped**; text cut at 1000 characters | small bodies, rate limits | none | Partially implemented (drops the tables) | `tests/test_pocket_research.py` (ran) |
| Rendering (voice) | ≤ 2 sentences / 70 words spoken, the rest on screen | `voice/bridge.py:148` `compose_answer`, `:108` `spoken` | citations, sources, tables, contradictions not spoken | — | — | Fully operational | `tests/voice/test_commands_bridge.py` (ran) |
| Rendering (Presence entity) | one inline SVG of 14 morphing path slots | `presence/web/planner.mjs`, `renderer.mjs`; spec `presence/scene_spec.py` | data only (enums and bounded numbers); any failure → the nearest canonical scene | motion budget; reduced motion | none | Fully operational | `node tests/presence/visual_rules_check.mjs` (693 checks), `client_check.mjs` (320), 3 e2e tests (ran) |
| Rendering (graph heatmap) | inline SVG with title, legend, caption and edge list | `console/web/console.js:1191-1212` | — | **white background and blue/rust hues hard-coded**: ignores the dark theme and the owner's grayscale rule | none | Underperforming | code read; dark appearance not rendered (Unverified visually) |
| Speech output | WAV per sentence, sentence-streamed, played through Web Audio | `voice/engines.py:256` (Kokoro), `voice/hub.py` | text ≤ 1000 characters per sentence; URLs reduced to host names | engine error → "the reply is shown as text" | container `neuroscifi-voice-tts` | Unverified (live engine not contacted; scripted engines pass) | `tests/voice/test_http_engines.py` (ran) |
| File production | QR codes (SVG / terminal); TLS certificates (PEM, DER); NSFA archives; NSG1 and `.npy` graph exports; a voice self-test report (JSON); acceptance evidence JSON | `console/phones.py`, `pocket/tls.py`, `archive.py`, `instance/graphs.py`, `voice/selftest.py` | exact | never overwrite; 0600 where private | segno, numpy, zstandard | Fully operational (for these files); Unsupported for PDF, DOCX, slides, images, audio files, video, 3-D, notebooks | audit-outputs §1D |
| Interactive components | none for answers (sliders, explorable models, viewers) | — | — | — | — | Unsupported | grep |
| Executable artifacts | none (no code, app preview, notebook) | — | — | — | — | Unsupported | audit-outputs §2 |
| Validation | the sentence guard (prose); Presence's two spec validators; QR tests | `webcortex/reasoning.py:3012`; `presence/scene_spec.py:117` + `presence/web/validator.mjs` | — | nothing validates a non-text artifact against its data | — | Partially implemented (prose and the entity only) | as above |
| Persistence | `web_tasks.result`; the imagined store for simulations; checkpoints of the whole instance file | `instance/checkpoints.py:77` `create` | the whole file is archived; a sidecar file is not | — | stdlib | Fully operational | `tests/instance/test_checkpoints.py` (exists) |
| Export and delivery (HTTP) | JSON and fixed static files; WAV for speech | `console/server.py` (bind `127.0.0.1` at `:32`), `pocket/server.py` | exact-match static asset tables; no route serves a file named by the request | no response size cap; no streaming beyond long-poll; CSP `img-src 'self'`, no `media-src` or `frame-src` on the console (`console/server.py:56-57`) | stdlib | Fully operational (for text) | audit-outputs §1E, §4 (a CSP probe in Chromium) |
| Export and delivery (agents) | one text content item (untrusted header + JSON) | `webcortex/mcp.py` | no structured content, image, audio or resource content types | rate caps | stdlib | Partially implemented (text only) | code read |

## 5. Directive §5.2 input categories, today

| Category | Status | What exists |
|---|---|---|
| Natural language and documents | Partially implemented | short typed text (console, voice, Pocket, MCP, CLI); web HTML (main text) and PDF (text layer, ≤ 30 pages) by URL only. Markdown, DOCX, EPUB, presentations, rich text: Unsupported (refused by the fetch gate; no library). No local document of any kind |
| Scanned documents, OCR, handwriting | Unsupported | no OCR code. (The host has Tesseract 5.5.3 with English data as a system program; nothing uses it, see §7) |
| Images and visual information | Unsupported (server); Partially implemented on Pocket only as 3-valued labels | the server never decodes an image; the fetcher refuses `image/*`; rendering blocks images |
| Audio and speech | Partially implemented | live speech → English text; no audio file, music, environmental sound or multichannel analysis |
| Video and motion | Unsupported | no video decode, no frame sequence, no motion sensors |
| Structured and semi-structured data | Partially implemented | web JSON (pretty-printed into text), XML feeds; `.npz`/`.npy` arrays for the core; JSON rule and causal files. CSV, Excel, Parquet, Arrow, generic SQL, logs: Unsupported |
| Source code and executable systems | Unsupported | `<pre>` code on web pages becomes text lines; package and vulnerability metadata from registries become facts |
| Spatial and geometric information | Unsupported | nothing |
| Scientific and mathematical information | Partially implemented | 2-D float arrays for the belief and graph core; values with units extracted from claim sentences; scholarly metadata providers. MathML flattened; no equations, tensors beyond 2-D, or uncertainty on arrays |
| Interactive and compound artifacts | Unsupported | Chromium renders a page only to extract its text; no zip, tar or compound-document inspection |

## 6. Directive §8.3 output families, today

| Family | Status | What exists |
|---|---|---|
| Visual | Partially implemented | the Presence entity (SVG from a validated spec); the graph heatmap (SVG, hard-coded colours); QR codes. No chart, diagram, illustration or visualisation of an answer |
| Audio | Partially implemented | speech (Kokoro WAV per sentence; on-device read-aloud on Pocket). No narration file, music, sound design or sonification |
| Video | Unsupported | nothing in the product (a developer script encodes an animated WebP of the entity with ffmpeg, `scripts/ui/readme_screenshots.py`) |
| Documents | Partially implemented | the structured research report as JSON and its console page; self-description and evidence JSON. No PDF, DOCX, slides or Markdown export |
| Software | Unsupported | no code, app, notebook or executable output |
| Spatial | Unsupported | no 3-D, CAD or scene |
| Scientific | Partially implemented | numeric results as JSON (what-ifs, forecasts, causal answers, graph evaluation); the heatmap is the only picture. No plots, notebooks or reproducible analysis packages |
| Hybrid | Partially implemented | the voice view (entity + speech + transcript + sources) and the research view (entity on stage + report) |

## 7. The host and its limits

Measured on 2026-11-26 (this lane re-ran the tool and package checks at 13:01; model and container facts are the
runtime audit's, same day).

| Resource | Value |
|---|---|
| Machine | one NVIDIA GB10 (DGX Spark class), aarch64, 20 cores |
| Memory | 121 GiB unified CPU/GPU memory; 24 GiB available when checked; swap in use. The pressure monitor's lines: elevated below about 18.3 GiB, critical below about 7.3 GiB (`resources/monitor.py:538-544`). GPU memory is not readable on GB10 (`memory_readable: false`), so only available system memory counts |
| Answer model | vLLM serving Qwen3-30B-A3B-Instruct-2507 FP8 on `127.0.0.1:8811` (`deploy/vllm/vllm.env:10-14`), 32 768-token context, about 44 GiB. **Text only**: `Qwen3MoeForCausalLM`, no vision or audio configuration, no multimodal flag |
| Other models present | Whisper large-v3-turbo (speech to text), Kokoro-82M (text to speech), Qwen3 embedding and reranker 0.6B (not running), PersonaPlex 7B (experimental, off). **No model that sees images or hears sound beyond speech** |
| Other tenants | 12 unrelated containers on the same machine |

Dependencies, where each is true:

| Item | On this host | Declared in `pyproject.toml` | Installed in CI | May the default path rely on it? |
|---|---|---|---|---|
| Python stdlib, numpy 2.3.5, zstandard 0.25.0, segno 1.6.6 | yes | core | every job | yes |
| pypdf 7.1.1 | yes | extra `web` | `check`, `e2e` | optional, lazy, with a named reason when missing |
| lxml 6.1.3 | yes | not declared (pulled in by web extras) | with the web extras | optional, lazy |
| Playwright 1.64.1 + Chromium | yes | extras `dev`, `web-render` | browser in `check`, `e2e` (not `acceptance`) | optional checks only |
| scipy, cryptography | yes | extras `research`, `crypto` | `check` | optional |
| Pillow, soundfile, av, OpenCV, openpyxl, pandas, pyarrow, python-docx, matplotlib, cupy, torch, PyYAML, third-party jsonschema | **absent** | no | no | no (nothing may be installed) |
| ffmpeg n9.0.2 / ffprobe | yes (`/usr/bin`) | no | not installed by the workflow (whether the runner image has it: Unverified) | no; product code never calls it |
| ImageMagick 7.1.2, rsvg-convert 2.63.3, Graphviz 16.1.0, vips 8.18.7, LibreOffice 26.8.1 | yes | no | no | no |
| Tesseract 5.5.3 (languages `eng`, `osd`), Ghostscript, qpdf, pdftotext | yes | no | no | no |
| node v26.13.0 | yes | dev-only (`axe-core`) | node 22 in `check`, `e2e` | tests only |
| pandoc, Inkscape, sox, Blender, exiftool, espeak-ng, piper, LaTeX, typst, gnuplot, OpenSCAD | absent | — | — | — |

The CI workflow (`.github/workflows/ci.yml`): job `check` installs `.[dev,research,web,web-extract,crypto]` and
Playwright's Chromium and runs `scripts/check.sh`; job `acceptance` installs only `.[dev]`; job `e2e` installs
`.[dev,web,web-extract,crypto]` and Chromium. No GPU, model server, voice container or systemd unit exists in CI.

## 8. Safety controls that must not be weakened

| Control | Where |
|---|---|
| SafeFetcher: address policy, DNS pinning, ports, robots, content-type allow-list, size and time caps | `webcortex/net.py` |
| XML with a DOCTYPE or entity refused | `webcortex/safety.py` (expat) |
| Hostile-markup refusal before HTML parsing | `webcortex/extract.py` |
| Prompt-injection quarantine: flagged sentences never become claims or reach the model | `webcortex/safety.py:132,171`; `webcortex/reasoning.py` |
| Privacy screen on every message to a non-loopback model endpoint | `inference/gateway.py:243` `screen` |
| Loopback-first model endpoints, address pinning, port-squatter refusal | `inference/gateway.py:261` `stranger`; `inference/config.py` |
| The sentence guard and its differential (0 different allowed) | `webcortex/reasoning.py:3012`; `scripts/guard_differential.py`; `tests/inference/test_guard_differential.py` |
| Provenance: nothing simulated, inferred or speculated is evidence; database triggers refuse imagined ids in evidence-side tables | `provenance.py`; `instance/schema.py:35` `_NO_IMAGINED`; ADR-049 |
| Forget cascades and tombstones; restore replays every forget | `instance/governance.py`; `instance/checkpoints.py:370` `_read_forgets`, `:423` `_replay` |
| The quiet switch: every unasked activity is registered and asks first | `background.py:50` `ACTIVITIES`, `:217` `allowed`; ADR-050 |
| Write regulation (free-space reserve, thermal) | `instance/store.py:618` `require_write_allowed` |
| Console: `127.0.0.1` only, per-start bearer token, Host and Origin checks, strict CSP | `console/server.py:32,52-57` |
| Pocket: family-scoped credentials, signed research replies, small bodies, strict CSP | `pocket/server.py:726` |
| Untrusted text rendered as text nodes only; links http(s) only | `console/web/console.js`, `pocket/web/research-ui.mjs`, `voice/web/voice-ui.mjs` |
| MCP: read-mostly scope, results in untrusted envelopes, no forget or configuration tools | `webcortex/mcp.py` |
| Chromium for fetched pages: sandbox on, all requests through SafeFetcher, no DNS, watchdog | `webcortex/browser.py` |
| `allow_pickle=False` on every numpy load | `files_cli.py`, `graph_store.py`, `instance/cli_evidence.py:30`, `instance/datasets.py` |
| No pickle, YAML load, tar or zip extraction, shell, or builtin `eval` anywhere in product code | audit-runtime §6(b) grep |

## 9. Existing defects the audits found

Hazard numbers are the audits' own: I = audit-inputs §5, C = audit-cognition §4, H = audit-runtime §4, O =
audit-outputs §7. "Lane S" means the defect is being fixed in lane S of Wave O-0 (not yet merged, not yet verified).

| # | Defect | Where (on `main`) | Being fixed |
|---|---|---|---|
| H1 = I6 | Non-text message parts skip the gateway's token estimate and privacy screen and would be forwarded as given (probe: a 400 kB base64 image counted as 9 tokens; screened text "describe "). Latent: no caller sends them | `inference/gateway.py:97` `estimate_messages`, `:106` `_content_text`, `:243` `screen` | in lane S (item 1: refuse any part that cannot be both counted and screened) |
| I1 | `.npz` is validated only after numpy has loaded it: a crafted header gives an uncaught MemoryError; a compressed bomb is fully decompressed | `instance/cli_evidence.py:30` | in lane S (item 2) |
| I2 | Whole-file reads with no size bound; FIFOs and symlinks followed | `files_cli.py:42`, `instance/cli.py:166`, `instance/cli_graphs.py:35` | in lane S (item 3) |
| I12 | `nsf archive` and `nsf graphfile` report bad files as Python tracebacks | `files_cli.py` | in lane S (item 3) |
| C1 | Forget does not look inside imagined payloads: a forgotten name inside a base64 or compressed value survives until lineage or expiry removes the record; the erasure canaries skip bytes | `instance/governance.py:119` `_SCRUB_TABLES` (no `imagined` entry), `:144` `scrub_roots` | in lane S (item 4) |
| H2 | Voice engines read responses with no size cap and do not check who holds the port before sending the key and the audio | `voice/engines.py` | in lane S (item 5) |
| C6 | `CARRIED_SETTINGS` equals the settable instance settings only by hand; no test enforces it | `instance/checkpoints.py:204` | in lane S (item 6a) |
| C5 = H10 | The background-work scanner recognises only the research entry points; a new loop that calls none of them is not detected | `tests/webcortex/test_background.py:416` `ENTRY_ANYWHERE` and the scan after it | in lane S (item 6b) |
| C9 | A web setting change writes no audit row (instance settings do) | `webcortex/state.py:268` `set_config` (instance: `instance/store.py:472`) | in lane S (item 7) |
| I3 | `extract()` accepts any bytes and turns binary into "text"; only the fetch gate keeps binaries out | `webcortex/extract.py:980-982` | not fixed; OMNIMIND's adapters never call `extract()` |
| I4 | Type decisions trust declared types (server, browser, request); only `%PDF-` and `<html` are sniffed | `webcortex/extract.py:974` | not changed for the web path; the new `detect` (lane K) decides by signature first |
| I5 | PDF extraction has no wall-clock bound and runs on the research worker thread | `webcortex/extract.py:821`; `webcortex/orchestrator.py:1324` | not fixed |
| I7 | The injection screen is English regex only; text from OCR, speech, captions or metadata would need more | `webcortex/safety.py` | not fixed; every new artifact text must pass it (a lane rule) |
| I8 | The unpersisted Markdown view carries absolute image URLs; rendering it with auto-loading images would make requests | `webcortex/extract.py:764-776` | not fixed (nothing renders it) |
| I9 | Speech over 30 s is cut without an error | `voice/hub.py:710-712` | not fixed |
| I10 | Original bytes live in a disposable cache; a claim's `doc_sha256` can outlive them | `webcortex/cache.py` | not fixed; OMNIMIND's own store is not disposable (lane A) |
| C2 | Forget-by-text is a hand list of text columns; a new table holding captions, file names or OCR text keeps a forgotten name unless added | `instance/governance.py:119` | lane A must add its own text layer to forget (its brief, item 6) |
| C3 = H8 | A durable generated artifact has no home under today's provenance rule (model or simulation output only in the imagined store) | `provenance.py`; `instance/imagined.py` | lane A settles it in ADR-071 |
| C4 = H9 | Bytes stored inside the instance file would inflate every checkpoint and switch imagination off above 512 MiB | `instance/imagine.py:73`, `instance/store.py:128` | design: bytes in a sidecar file (lane A) |
| C7 | A crafted compressed npz in a dataset blob is bounded only by the 1 GB blob limit (not probed) | `instance/datasets.py` | lane S checks the decode paths (item 2) |
| C8 | No untrusted envelope exists for text derived from artifacts (OCR, transcripts of media, code comments) | `webcortex/reasoning.py:792` | not in Wave O-0 (no model path) |
| H3 | No append-only audit log: `deletion_log` and `web_events` have no immutability trigger | `instance/schema.py` | not fixed; lane A's `artifact_events` is append-only for its own rows |
| H4 | Installed systemd units (`MemoryMax=2G`, `MemoryDenyWriteExecute=yes`) would kill Chromium, node or ffmpeg; `nsf start` has no limits at all | `deploy/systemd/` | not fixed |
| H5 | The host runs near its elevated memory line; `engine_gate` refuses only at critical and reserves nothing | `resources/policy.py:256` | not fixed |
| H6 | The only renderer routes sub-requests to the real web; it is built for reading pages, not for rendering artifacts | `webcortex/browser.py` | not fixed; Wave O-0 renders nothing in a browser on the default path |
| H7 | Console CSP has no `media-src` or `frame-src` and `img-src 'self'` only; showing artifacts needs a deliberate, reviewed change | `console/server.py:56-57`, `pocket/server.py:726` | not in Wave O-0 (DESIGN §8 sets the rule for Wave O-1: no weakening) |
| O1 | The first view that inserts generated SVG or HTML with `innerHTML` would open an injection surface; no Trusted Types policy exists | `console/web/console.js` | not in Wave O-0 |
| O2 | The sentence guard checks sentences only; a model-written chart or table would carry unchecked numbers | `webcortex/reasoning.py:3012` | not in Wave O-0 (no model writes data) |
| O5 | Console and Pocket have no response size cap and send whole files in one write | `console/server.py`, `pocket/server.py` | not in Wave O-0 |
| O7 | The heatmap's hard-coded colours (white, blue, rust) break the dark theme and the grayscale rule; no test enforces "no hue" | `console/web/console.js:1191-1212` | open question for the owner |
| O8 | `tests/e2e/test_presence.py` rewrites tracked PNGs in `docs/presence/snapshots/` when it runs | `tests/e2e/test_presence.py` | not fixed |

## 10. Where the audits disagree, and which is right

| Topic | What each says | Which is right |
|---|---|---|
| Media decoders | audit-inputs: "nothing in CI or the venv can decode raster images, audio files, video"; audit-outputs and audit-runtime: ffmpeg 9, ImageMagick, rsvg-convert, Graphviz, LibreOffice and vips are on the host | Both are true about different things. No **Python package** that decodes media is installed or declared, and none is in CI. The **host** has system programs that can decode most media; none is a declared dependency, none is installed by the CI workflow, and no product code calls any of them. This lane also found Tesseract 5.5.3 (OCR, English data only), Ghostscript, qpdf and pdftotext on the host, which no audit listed. Using any of them on untrusted input is a security decision for the owner (it needs a sandbox) |
| Web extraction | audit-runtime #23: Fully operational; audit-inputs §1c: Partially implemented | Both, measured against different things. It does what it is for (text for claims) reliably; against directive §5.2 (preserve layout, tables, figures, hierarchy) it is partial. This document uses Partially implemented |
| Speech to text | audit-runtime #9: Fully operational (container healthy); audit-inputs §1d: Partially implemented, end to end unverified | The route and engine plumbing are tested with fake servers; the live engine's quality was never measured by any audit; it gives English text only. Partially implemented, with live quality Unverified |
| Embeddings | audit-cognition: Partially implemented (text, optional); audit-runtime #5: Unverified (service not running) | The service is not running on this host, so nothing here can show it works: Unverified |
| Text to speech | audit-runtime #10: Fully operational; audit-outputs B1: live engine Unverified | Container health is not a test of output. Unverified for the live engine; the code path is tested |
| Background-scanner test | audit-cognition cites `test_background.py:415-424`; audit-runtime cites `tests/webcortex/test_background.py:499-584` | The same file, `tests/webcortex/test_background.py`; the entry-point set is at `:416` and the scan follows it. Both ranges are parts of it |
| CI's `check` job | `docs/operations/TESTING.md` says `.[dev,research]`; the workflow installs `.[dev,research,web,web-extract,crypto]` plus Chromium | The workflow is right; TESTING.md is stale (audit-runtime §5) |

References corrected against `main` (the audits quoted the Wave 4 branch): `instance/store.py` settable settings at
`:326` (not `:340`), `set_config` at `:441` and its audit row at `:472` (not `:471`, `:500-503`),
`require_write_allowed` at `:618` (not `:648`); `webcortex/state.py` `set_config` at `:268` (not `:277`);
`webcortex/config.py` document and cache caps at `:119-121` (not `:135-137`); the Pocket CSP at
`pocket/server.py:726` (not `:735`); `instance/memory.py` `retrieve` at `:218` and `_TABLES` at `:180` (not `:159`,
`:179`); claim length bounds at `webcortex/claims.py:48-49` (not `:45-46`). Three references were off on both branches: `instance/datasets.py` `add` is at `:79` (not `:74`), the branch of `extract()` that decodes any other bytes as text is `webcortex/extract.py:980-982` (not `:986-988`), and Pocket's texture function is `pocket/web/field-model.mjs:48-55` (not `:47-54`). The "caution" lines in Pocket's and the
voice reply, the echo fingerprinting of `emitted.py` and the test `tests/acceptance/test_wave4_d.py` exist only on
the unmerged Wave 4 branch and are not part of this baseline.

## 11. What remains unverified

- The live answer model, speech-to-text and text-to-speech engines: no audit sent them a request. Speech quality,
  latency and the deployed ASR's handling of `language="en"` are not measured here; the "2.1 % word error rate" in
  `docs/acceleration/COGNITIVE_GAP_ANALYSIS.md:108` was not re-measured.
- Which container owns each of the two smaller GPU processes (by size: speech engines).
- Live structured providers and search back-ends (no network was allowed).
- The CPU and memory cost of a hostile PDF of up to 5 MB, and whether the task deadline interrupts a long extraction.
- The aliasing effect of the voice capture's linear resampler on recognition accuracy.
- What the browser's 1×1 and 32×32 downscales on Pocket compute (true mean or sampled pixels), per browser.
- Pocket read-aloud on real phones; any handset test of voice capture.
- Typical and maximum report size in practice (no response cap exists).
- The heatmap's appearance in dark mode (read from code, not rendered).
- Forget and restore coverage of `web_reflections`, `web_metrics` and `web_rechecks` (traced only in part).
- Whether `voice_audio` PCM is size-bounded per segment (only its expiry was seen).
- Whether the CI runner image ships ffmpeg; whether the systemd units work with Chromium rendering enabled.
- Torch checkpoint loading semantics inside the speech and memory containers.
- The stale installed metadata (`neuroscifi 0.5.0` in the virtual environment's dist-info against 0.7.0).
