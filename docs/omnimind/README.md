# Project OMNIMIND

Status: 2026-11-26. Phase I (four read-only audits of inputs, cognition, outputs and the runtime) is **done**; its
results are merged in [CAPABILITY_AUDIT.md](CAPABILITY_AUDIT.md). The main session has ruled the design
([ARTIFACT_ARCHITECTURE.md](ARTIFACT_ARCHITECTURE.md), first version). **Wave O-0 is in progress**: ten lanes are
building on their own branches from `main` (`8f68beb`). Nothing of OMNIMIND is on `main`, nothing has been merged,
and nothing has been measured yet. Today NeuroSciFi takes in no files, decodes no image, sound or video, and answers
only in sentences; that is the baseline everything here will be measured against.

Project OMNIMIND is the owner's programme to let NeuroSciFi take in information in many forms and make results that
are not only text, on top of the system that exists and without replacing any of it. Every capability is a function
with a name that says what it does, sits behind a setting or an explicit owner command, and is kept only where a
benchmark written before it shows that it works and nothing existing regresses.

## The directive in three sentences

The owner's directive (2026-11-26, saved verbatim in the owner's working copy as `.omc/handoffs/omnimind-directive.md`, which git does not track) asks NeuroSciFi to take
in documents, tables, images, sound, video, code, geometry and data, keep each in a form that does not throw away its
structure, and relate different kinds of content to each other. It asks the system to decide what form a result
should take before making it (a sentence, a table, a chart, a diagram, a sound, an explorable model, a 3-D object or a
combination), and to make that result with checks that do not rely on the system judging its own work; the owner named
this "Creative Output Intelligence" as the most consequential addition. It requires the existing system to be audited
first, every change to be additive, reversible, tested and benchmarked, and anything that cannot be verified to be
reported as unverified or blocked, not as done.

The directive also states a long-term research ambition; it says itself that any such capability must be shown by
evaluation, not assumed. These documents make no claim of consciousness, general or superhuman intelligence.

## Hard rules

| Rule | Where it is enforced |
|---|---|
| Additive and reversible: nothing existing is replaced or removed; every new behaviour is off, display-only or an explicit owner command by default | lane rules; with defaults, the offline research digest must equal `docs/quality/evidence/offline_deterministic_aarch64.json` (`scripts/bench_wave1.py offline`), `scripts/reproduce.py` must pass, and the sentence guard's differential must report 0 different (`scripts/guard_differential.py compare`) |
| The default path imports only the standard library, numpy, zstandard and segno; `pypdf`, `lxml` and Playwright's Chromium are optional and lazy; nothing is installed, downloaded or pulled | `pyproject.toml` core dependencies; lane rules; the CI `acceptance` job installs only `.[dev]` |
| No fabricated understanding: a decoder really decodes, or says exactly what it could and could not establish; an unknown format gets the capability-discovery answer | the `Opaque` and `Unsupported` results ([ARTIFACT_ARCHITECTURE.md](ARTIFACT_ARCHITECTURE.md) §4); `adapters.catalog()` is the only source for "supported"; the benchmark reports every false answer as WRONG, separately (lane Z) |
| Untrusted bytes: bound every allocation before it is made; never extract to disk, follow an input path, execute, import, `eval` or unpickle; refuse XML with a DTD; artifact text passes the injection screen | `Limits` and the shared helpers `artifacts/safe.py` (lane K); the benchmark's `hostile` family; `webcortex.safety.scan_injection` |
| Nothing generated becomes evidence: everything the system makes is labelled as made, never citable, never in an evidence-side table | ADR-049 and `provenance.py`; the imagined-id triggers (`instance/schema.py:35`); ADR-071 and the extended reference-column tests (lane A) |
| Forget, restore, quiet and scopes reach everything stored, including bytes outside the instance file and encoded copies | `instance/governance.py`, `instance/checkpoints.py`, `background.py:217` `allowed`; lane A's seed-and-scan tests; lane S item 4 for imagined payloads |
| No self-grading: validators re-parse the produced bytes with their own code; benchmark answers come from the benchmark's own generators | a test that `artifacts/check/` never imports `artifacts/make/` (lanes G1, G2); `acceptance/omni/` never imported by product code and never importing `neuroscifi.artifacts` (lanes Z, ZF) |
| Honest words: no claim of consciousness, sentience, feelings, awareness, general or superhuman intelligence; anything not run is marked unverified; "detects X and reports its structure" is never written as "supports X" | lane rules; these documents |
| The interface: the Presence entity stays the centre of every interaction view; grayscale with hue only for warning, danger and the entity's flairs; plain words; light and dark; keyboard; axe-clean | `tests/e2e/test_ui_matrix.py`, `tests/e2e/test_a11y_tree.py`, `tests/presence/test_visual_rules.py`; generated pictures follow the palette rule and their checks enforce it (lanes G1, G2) |
| Privacy and the host: bind 127.0.0.1 only; no new daemon or listener; no network requests; logs carry ids, sizes and hashes, never content | `console/server.py:32`; lane rules |
| One migration in Wave O-0, appended, forward-only | `instance/schema.py:131` `MIGRATIONS`; the append-order test in `tests/instance/test_imagined.py`; lane A only |

## Waves

| Wave | Lanes | Status |
|---|---|---|
| Phase I | four read-only audits: inputs and understanding (A1), cognition and memory (A2), outputs and delivery (A3), runtime, models and safety (A4) | done; merged in [CAPABILITY_AUDIT.md](CAPABILITY_AUDIT.md) |
| O-0 | **S** the audit's defects closed first; **K** the kernel (types, detection, adapter registry, spec envelope); **Z** the benchmark for taking things in and making things; **ZF** the benchmark for choosing a form, cross-modal questions and learning; **A** the artifact store; **B1** adapters for documents, tables, trees, code, notebooks, archives, databases; **B2** adapters for images, sound, video containers, meshes, maps, arrays; **C** the form planner; **G1** pictures, documents and data files, with their checks; **G2** sound, solids, raster images and explorable models, with their checks; **DOCS-0** these documents | being built, not yet measured |
| later | the production pipeline with bounded repair and pre-production checks; cross-modal binding and reconciliation; the answer path's use of the planner (`output_forms` off / offer / auto); showing artifacts in the console and Pocket; an upload route; durable production jobs; learning form choices from outcomes; the withheld split; optional engines on the system's own bytes | not started; see [IMPLEMENTATION_MATRIX.md](IMPLEMENTATION_MATRIX.md) |

## The twelve deliverables (directive §17)

| # | Deliverable | File | State |
|---|---|---|---|
| 1 | Multimodal capability audit | [CAPABILITY_AUDIT.md](CAPABILITY_AUDIT.md) | written: the baseline before OMNIMIND |
| 2 | Universal artifact architecture | [ARTIFACT_ARCHITECTURE.md](ARTIFACT_ARCHITECTURE.md); later the ADRs 071 to 075 in `docs/architecture/ARCHITECTURAL_DECISIONS.md` | first version: as designed, being built |
| 3 | Cognitive gap analysis | [COGNITIVE_GAP_ANALYSIS.md](COGNITIVE_GAP_ANALYSIS.md) | written |
| 4 | Creative intelligence assessment | `docs/omnimind/CREATIVE_INTELLIGENCE_ASSESSMENT.md` (planned) | not written: needs the planner (lane C) measured on the `forms` benchmark (lane ZF) |
| 5 | Implementation matrix | [IMPLEMENTATION_MATRIX.md](IMPLEMENTATION_MATRIX.md) | written |
| 6 | Production-ready integrations | the code under `src/neuroscifi/artifacts/` and its tests; a listing in `docs/omnimind/INTEGRATIONS.md` (planned) | being built on the lanes' branches |
| 7 | Multimodal quality benchmarks | `docs/quality/OMNIMIND_BENCHMARK.md`, `docs/quality/OMNIMIND_BENCHMARK_FORMS.md`, the baselines `docs/quality/evidence/omnimind_baseline.json` and `omnimind_baseline_forms.json` (lanes Z, ZF); results in `docs/omnimind/BENCHMARK_RESULTS.md` (planned) | being written |
| 8 | Recursive knowledge validation | `docs/omnimind/RECURSIVE_VALIDATION.md` (planned) | not started: learning from outcomes is a later wave; the `learn` family measures simulated preferences only |
| 9 | Complete regression report | `docs/omnimind/REGRESSION_REPORT.md` (planned) | not started: written when Wave O-0 is integrated |
| 10 | Updated architecture documentation | `docs/omnimind/ARCHITECTURE_AFTER_OMNIMIND.md` (planned) | not started: describes what is deployed, so it follows a merge |
| 11 | Operational runbook | an OMNIMIND section of `docs/operations/RUNBOOK.md` (planned) | not started |
| 12 | Final implementation report | `docs/omnimind/FINAL_REPORT.md` (planned) | not started |

"Planned" files do not exist yet; they are named here so that the next lanes write them in one place.

## Where to read next

- What the system can and cannot do with each kind of input and output today: [CAPABILITY_AUDIT.md](CAPABILITY_AUDIT.md).
- Why each gap exists in the architecture, and what is out of reach without the owner: [COGNITIVE_GAP_ANALYSIS.md](COGNITIVE_GAP_ANALYSIS.md).
- What is planned, in what order, and the decisions waiting for the owner: [IMPLEMENTATION_MATRIX.md](IMPLEMENTATION_MATRIX.md).
- How the pieces fit: [ARTIFACT_ARCHITECTURE.md](ARTIFACT_ARCHITECTURE.md).
- The programme before this one, whose rules OMNIMIND keeps: [../genesis/README.md](../genesis/README.md).
