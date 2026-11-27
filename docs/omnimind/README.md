# Project OMNIMIND

Status: 2026-11-27. Waves O-0 and O-1, three independent reviews with second passes, fix lanes F1 to F5 and these
documents are merged into the integration branch `omnimind` (`ad28fcf`, schema 21; Wave O-2 below). The main session ran the final
validation on `55f89be` (the commit that pins the drivers beside the look rule) and took the withheld looks there, once
each. It is a **local branch: not merged into `main`, not pushed, never run on CI**.

Project OMNIMIND is the owner's programme to let NeuroSciFi take in information in many forms and make results that
are not only text, on top of the system that exists and without replacing any of it. Every capability is a function
or command with a name that says what it does, sits behind a setting or an explicit owner command, and is measured on
an examination written before it.

## The result, on one screen

**Latest: the fix wave (Wave O-2) was examined once on a second withheld split and none of the three fixed mechanisms
earned "supported"; the owner's condition for merging is not met** (section "Wave O-2" below).

NeuroSciFi can now **take in** a file the owner hands over (`nsf inspect`, `nsf artifact add`; the console's upload
when turned on): it identifies it by content, decodes it within limits in a guarded child process (66 readers: tables,
documents, trees, code, notebooks, archives, SQLite, lossless images, uncompressed sound, MIDI, meshes, maps, arrays),
reads only the header or structure of compressed images, compressed audio and video, and keeps it beside the
individual where forget and restore reach it. It can **relate** two artifacts by measuring what one shows against what
the other states (`nsf artifact relate`), **decide** the form of a result before making it and say why (the form
planner), **make** 30 kinds of result (charts, diagrams, documents, tables, data files, sounds, solids, pictures,
explorable models) and **check** each with an independent reader of its bytes before keeping it as "made by the
system, not evidence". **On the withheld split, which no lane tuned against, it gave wrong answers for the first time**: 6 in the seven
families (3 disguised or unknown files answered falsely; 3 hostile files answered instead of refused) and 6 in relating
two artifacts (an image against its caption was answered every time and right on 5 of 10, no better than a coin).
Those three looks are **not supported**; the weaknesses are known now and must be examined afresh on new withheld
cases. Supported on unseen cases: intake, honest, compound, made and transform (629 right, 17 "cannot", 6 wrong, 0
failed of 652 in all; today's system 72 right, 2 wrong, 3 failed), and nothing else: the form planner's and the learner's withheld looks are VOID by the rule (two controls were not run; figures reported as measured, establishing nothing under the rule) (as measured: the
planner's good 0.687 against the shape-only table's 0.480; the learner 0.713 against the planner alone's 0.440 with
simulated people, below the benchmark's own majority rule). The first withheld split is spent for identification,
refusal and the relater; only the second can measure the fix wave. On held-out it had
answered 510 of 528 with 0 wrong. It **cannot** see or hear (no such model here), decode JPEG, MP3 or video frames, or
say what a picture depicts. **Nothing reaches the answer path** (every answer in conversation is still sentences),
Pocket, voice or agents; **nothing is on by default except viewing in the console**; and **nothing is merged into
`main`**. No done-criterion of the directive is met without qualification: [FINAL_REPORT.md](FINAL_REPORT.md).

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
| 9 | Complete regression report | [REGRESSION_REPORT.md](REGRESSION_REPORT.md) (with the final validation at `55f89be`) |
| 10 | Updated architecture documentation | [ARTIFACT_ARCHITECTURE.md](ARTIFACT_ARCHITECTURE.md) and [../architecture/SYSTEM_ARCHITECTURE.md](../architecture/SYSTEM_ARCHITECTURE.md) section 9 |
| 11 | Operational runbook | [RUNBOOK.md](RUNBOOK.md) |
| 12 | Final implementation report | [FINAL_REPORT.md](FINAL_REPORT.md) (with the withheld looks) |

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
| last | F4 the second passes' residues (`1aa7eb6`); docs-2 these documents | F4: merged (`5c29da4`); docs-2: merged |
| after | F5: the make summary after a partial read, `--partial` on a large archive, `make` exit codes, the remaining non-plain words, the `artifact_max_bytes` help (`bf3dac6`) | merged (`20a86ec`) |
| then | driver pins with the look rule (`55f89be`); the main session's full validation there; the withheld looks, once each (`21193c4`) | done; the owner's questions next |

## Wave O-2: fix what the withheld look found

On 2026-11-27 the owner decided that the three weaknesses the first withheld look found (false answers on disguised
or unknown files; hostile files answered instead of refused; the relater answering a pair it cannot measure) be fixed,
and nothing else added: no new format, form, command or setting. Lane FX fixed them by class (ADR-080); lane ZW2 wrote a
second withheld split without reading product code (unknown 171 tasks, hostile 62, cross 98); lane FX2 made the
refusal rule one rule (parser-level attacks refuse the whole file; references outside the file and embedded active
content are named in a note, never followed or run, and never refuse by themselves). The third review passes found the
first withheld split spent for these mechanisms, the forms and learn looks VOID by the rule, and FX's corpora to be
regression evidence only. **The one look at the second withheld split was taken on `ae6120f`, under its rule committed before it
([../quality/OMNIMIND_BENCHMARK.md](../quality/OMNIMIND_BENCHMARK.md)), and none of the three earned "supported"**:
unknown 158 right, **5 wrong** of 171; hostile 51 refused, **11 answered instead of refused** of 62; cross 65 of 98
with 1 wrong of 48 answered and "cannot" on all 18 undecidable cases, but right on only 47 of 80 decidable cases (the
rule needs 60): it stopped guessing and declines too much to count as working. The fixes closed the classes the fix
lane named and did not generalise to new constructions. Both withheld splits are spent for these families; any
further fix needs a third set of unseen cases written first. The owner's condition for merging was examined and is
**not met**; the pull request stays unmerged pending the owner's decision. Details: [FINAL_REPORT.md](FINAL_REPORT.md).

## Where to read next

- What changed for each pathway, and what exists now: [CAPABILITY_AUDIT.md](CAPABILITY_AUDIT.md).
- What failed and every figure: [BENCHMARKS.md](BENCHMARKS.md), [FINAL_REPORT.md](FINAL_REPORT.md).
- How to use, switch off and roll back: [RUNBOOK.md](RUNBOOK.md).
- The programme before this one, whose rules OMNIMIND keeps: [../genesis/README.md](../genesis/README.md).
