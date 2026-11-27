# Project OMNIMIND

Status: 2026-11-27. Waves O-0 and O-1, three independent reviews with second passes, and fix lanes F1 to F3 are
merged into the integration branch `omnimind` (`09bfff6`, schema 21); fix lane F4 (`1aa7eb6`) is merged into these
documents' branch and goes into `omnimind` with them. It is a **local branch: not merged into `main`, not pushed,
never run on CI**. A small fix lane F5 is still working; places it will change are marked "to be confirmed after fix
lane F5" in these documents. The main session's final validation and the withheld looks have not been taken.

Project OMNIMIND is the owner's programme to let NeuroSciFi take in information in many forms and make results that
are not only text, on top of the system that exists and without replacing any of it. Every capability is a function
or command with a name that says what it does, sits behind a setting or an explicit owner command, and is measured on
an examination written before it.

## The result, on one screen

NeuroSciFi can now **take in** a file the owner hands over (`nsf inspect`, `nsf artifact add`; the console's upload
when turned on): it identifies it by content, decodes it within limits in a guarded child process (66 readers: tables,
documents, trees, code, notebooks, archives, SQLite, lossless images, uncompressed sound, MIDI, meshes, maps, arrays),
reads only the header or structure of compressed images, compressed audio and video, and keeps it beside the
individual where forget and restore reach it. It can **relate** two artifacts by measuring what one shows against what
the other states (`nsf artifact relate`), **decide** the form of a result before making it and say why (the form
planner), **make** 30 kinds of result (charts, diagrams, documents, tables, data files, sounds, solids, pictures,
explorable models) and **check** each with an independent reader of its bytes before keeping it as "made by the
system, not evidence". On the benchmark's synthetic files it answers 510 of 528 held-out tasks right with 0 wrong and
0 crashes (today's system: 55 right, 1 wrong, 1 crash). It **cannot** see or hear (no such model here), decode JPEG,
MP3 or video frames, or say what a picture depicts; its form planner **does not beat the references** on held-out by
the rule written before the run and shows no evidence of reading the request's words; its learner stays below the
benchmark's own majority rule with simulated people. **Nothing reaches the answer path** (every answer in
conversation is still sentences), Pocket, voice or agents; **nothing is on by default except viewing in the
console**; and **nothing is merged into `main`**. No done-criterion of the directive is met without qualification:
[FINAL_REPORT.md](FINAL_REPORT.md).

## The twelve deliverables (directive §17)

| # | deliverable | file |
|---|---|---|
| 1 | Multimodal capability audit | [CAPABILITY_AUDIT.md](CAPABILITY_AUDIT.md) (after, beside the unchanged baseline) |
| 2 | Universal artifact architecture | [ARTIFACT_ARCHITECTURE.md](ARTIFACT_ARCHITECTURE.md) (as built) |
| 3 | Cognitive gap analysis | [COGNITIVE_GAP_ANALYSIS.md](COGNITIVE_GAP_ANALYSIS.md) |
| 4 | Creative intelligence assessment | [CREATIVE_INTELLIGENCE_ASSESSMENT.md](CREATIVE_INTELLIGENCE_ASSESSMENT.md) (the form planner as measured; creativity is not measured) |
| 5 | Implementation matrix | [IMPLEMENTATION_MATRIX.md](IMPLEMENTATION_MATRIX.md) |
| 6 | Production-ready integrations | [PRODUCTION_INTEGRATIONS.md](PRODUCTION_INTEGRATIONS.md) ("production-ready" is not claimed) |
| 7 | Multimodal quality benchmarks | [BENCHMARKS.md](BENCHMARKS.md), leading to [../quality/OMNIMIND_BENCHMARK.md](../quality/OMNIMIND_BENCHMARK.md) and [../quality/OMNIMIND_BENCHMARK_FORMS.md](../quality/OMNIMIND_BENCHMARK_FORMS.md); the held-out record [../quality/evidence/omnimind_heldout/README.md](../quality/evidence/omnimind_heldout/README.md) |
| 8 | Recursive knowledge validation | [RECURSIVE_KNOWLEDGE_VALIDATION.md](RECURSIVE_KNOWLEDGE_VALIDATION.md) |
| 9 | Complete regression report | [REGRESSION_REPORT.md](REGRESSION_REPORT.md) (the final-commit validation is a placeholder) |
| 10 | Updated architecture documentation | [ARTIFACT_ARCHITECTURE.md](ARTIFACT_ARCHITECTURE.md) and [../architecture/SYSTEM_ARCHITECTURE.md](../architecture/SYSTEM_ARCHITECTURE.md) section 9 |
| 11 | Operational runbook | [RUNBOOK.md](RUNBOOK.md) |
| 12 | Final implementation report | [FINAL_REPORT.md](FINAL_REPORT.md) (the withheld looks are a placeholder) |

Decisions: ADR-070 to ADR-079 in [../architecture/ARCHITECTURAL_DECISIONS.md](../architecture/ARCHITECTURAL_DECISIONS.md)
(ADR-073, 076, 077 and 078 carry dated additions of 2026-11-27).

## The directive in three sentences

The owner's directive (2026-11-26, saved verbatim in the owner's working copy as
`.omc/handoffs/omnimind-directive.md`, which git does not track) asks NeuroSciFi to take in documents, tables, images,
sound, video, code, geometry and data, keep each in a form that does not throw away its structure, and relate different
kinds of content to each other. It asks the system to decide what form a result should take before making it, and to
make that result with checks that do not rely on the system judging its own work; the owner named this "Creative
Output Intelligence" as the most consequential addition. It requires the existing system to be audited first, every
change to be additive, reversible, tested and benchmarked, and anything that cannot be verified to be reported as
unverified or blocked, not as done.

These documents make no claim of consciousness, understanding, general or superhuman intelligence, or creativity.

## Hard rules, and where each is enforced

| rule | where it is enforced |
|---|---|
| Additive and reversible: nothing existing replaced; every new behaviour an owner command or a setting off by default (one exception, stated: the console's view is on) | with the defaults, the offline digest equals `docs/quality/evidence/offline_deterministic_aarch64.json` in both regimes, `scripts/reproduce.py` passes and `scripts/guard_differential.py compare` reports 0 different (every lane and the code review's second pass; [REGRESSION_REPORT.md](REGRESSION_REPORT.md)) |
| Dependencies: the default path imports only the standard library, numpy, zstandard and segno; `pypdf` and Chromium optional and lazy; nothing installed or downloaded | `pyproject.toml`; the code review's import check (the artifacts package needs only numpy); `scripts/two_account_check.py` 56 of 56 |
| No fabricated understanding: a reader decodes, or says what it could and could not establish; an unknown format gets the capability-discovery answer | `adapters.catalog()` is the only statement of what can be read; the benchmark counts every false answer as wrong: 0 on development and held-out |
| Untrusted bytes: allocation charged before it is made; no extraction to disk, execution, `eval` or unpickling; no DTD; text screened for instructions aimed at an AI | `Limits`, `artifacts/safe*.py`, the guarded child process (`artifacts/guarded.py`); the hostile family (23 of 23, 22 of 23); the security review's second pass |
| Nothing generated becomes evidence | `provenance.MADE` (ADR-071 after ADR-049); schema 20's triggers refuse an artifact id in 15 evidence-side tables; every listing labels made things |
| Forget, restore, quiet and scopes reach everything stored | `store.py` forget and `scan_root`, `checkpoints` replay, owner-only readers; lane A's and fix lane F1's tests; a file the scan cannot finish is held, not deleted (fix lane F4, `test_f4_forget_hold.py`) |
| No self-grading: checks never import makers; benchmark answers come from the benchmark's own generators | an import test over `artifacts/check/`; `acceptance/omni/` never imported by product code; the drivers only translate (`test_the_drivers_only_translate`) and are pinned for the looks |
| Honest words | these documents; the measurement review reads them against the evidence |
| The interface: the Presence entity centred; grayscale; plain words; light and dark; keyboard; axe-clean | `tests/e2e/test_console_artifacts_e2e.py`, `tests/console/test_palette_rule.py` |
| Privacy and the host: 127.0.0.1 only; no new daemon or listener; no network; logs carry ids, sizes and hashes | `console/server.py`; the launch-site classification test (`tests/test_launch_sites.py`) |
| Schema steps appended, forward-only, one per lane that was allowed one | `instance/schema.py` `MIGRATIONS`: 20 (lane A), 21 (lane L); `docs/operations/MIGRATIONS.md` |

## Waves and lanes, as they ran

| step | lanes (last commit) | merged into `omnimind` |
|---|---|---|
| Phase I | four read-only audits | the baseline in [CAPABILITY_AUDIT.md](CAPABILITY_AUDIT.md) |
| O-0 | S audit defects (`5445ea0`); K kernel (`3d85260` + follow-ups); A store, schema 20 (`e2e940f`); B1 documents, tables, code, archives, SQLite (`617e604`); B2 images, sound, video containers, meshes, maps, arrays (`a48bdf0`); C planner (blind `8d84f29`, tuned `c5d3230`, frozen `4388f4d`); G1 pictures, documents, data files and checks (`7c02595`, after the G1b follow-up); G2 sound, solids, rasters, explorable models and checks (`9b31760`); Z examination (`320f068`); ZF forms, cross, learn examination (`542dfd9`); ZW withheld split (`df5c820`); docs-0 (`abc6be4`) | yes |
| O-0 integration | int (`c1f5f2f`); the merge of `main` with Cognitive Genesis Wave 4 (`1a00f50`) | yes |
| O-1 | P production pipeline (`b87d90f`); R relating (`e0e056a`); L learning, schema 21 (`2d19a76`, `d8aaf04`); int2 integration (`505b55b`); U console view (`841c3a0`) | yes |
| reviews | security, measurement, code on `505b55b`; second passes on `09bfff6`: all three "offer to the owner: yes", with residues | (reports, not code) |
| fixes | F2 benchmark (`4a6986e`); F1 product (`b4798aa`, `bb113ab`); F3 the rest (`a0c7491`) | yes (`09bfff6`) |
| last | F4 the second passes' residues (`1aa7eb6`); docs-2 these documents | F4: merged into docs-2 |
| after | F5: the make summary after a partial read, `--partial` on a large archive, `make` exit codes, the remaining non-plain words, the `artifact_max_bytes` help | **in progress** |
| then | pins, full validation, the withheld looks, the owner's questions | not started |

## Where to read next

- What changed for each pathway, and what exists now: [CAPABILITY_AUDIT.md](CAPABILITY_AUDIT.md).
- What failed and every figure: [BENCHMARKS.md](BENCHMARKS.md), [FINAL_REPORT.md](FINAL_REPORT.md).
- How to use, switch off and roll back: [RUNBOOK.md](RUNBOOK.md).
- The programme before this one, whose rules OMNIMIND keeps: [../genesis/README.md](../genesis/README.md).
