# Project Cognitive Genesis

Status: 2026-11-25. Phase I (discovery and research) is done and Wave 0 is integrated on branch `cognitive-genesis`
(`76a478e`), reviewed twice and fixed; its final validation is in [WAVE0.md](WAVE0.md). Wave 1 is **built, awaiting
integration and validation**: no Wave 1 result here is final.

Project Cognitive Genesis is the owner's programme to add measurable cognitive mechanisms to NeuroSciFi on top of the
system that exists, without replacing any of it. Each mechanism is a function with a name that says what it does
(an internal-state record, a quarantined store for imagined content, a selective broadcast), sits behind a setting,
and is kept on only where a benchmark written before it shows a gain and nothing existing regresses.

## The directive in three sentences

The owner's directive (2026-11-25) asks for functional mechanisms of instinct (anticipation, salience, curiosity,
regulation), imagination (world models, counterfactuals, simulation before action), consciousness-inspired cognition
(selective global access, a self-model, metacognition, continuity) and recursive knowledge evolution, measured rather
than imitated. It requires the actual system to be discovered before any redesign, then implementation, benchmarks
with ablations and withheld tasks, regression testing and ten deliverables (A–J). Every consequential change must be
observable, testable, reversible and benchmarked, must not compromise the owner's authority, shutdown, memory
integrity or security, and must never present consciousness, sentience or general intelligence as a demonstrated
outcome.

## Hard rules

| Rule | Where it is enforced |
|---|---|
| Functional mechanisms only, named for what they do | lane rules; [RESEARCH_ASSESSMENT.md](RESEARCH_ASSESSMENT.md) §7 |
| Never a claim of consciousness, sentience, feelings or awareness, in code, comments, UI text or documents | [RESEARCH_ASSESSMENT.md](RESEARCH_ASSESSMENT.md) §8 (the claims never to make) |
| Nothing imagined is evidence: nothing simulated, inferred by a model or predicted is stored or used as evidence or knowledge | ADR-049: the quarantined store, database triggers, the allow-list scan |
| No self-grading gate: gates use external evidence, deterministic checks, or held-out data written before the change | the benchmark's rules ([COGNITIVE_BENCHMARK.md](../quality/COGNITIVE_BENCHMARK.md)); the frozen splits |
| The owner's quiet switch and stop win over everything new | ADR-050: `quiet`, `background.allowed`, the registry every background activity must join |
| Additive, switchable, benchmarked: default on only with a held-out gain, an ablation that removes it, retention kept and no adversarial case lost | [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) §1–2 |
| Saved data, contracts and memory semantics preserved: beliefs computed on read, migrations forward-only and appended last, forget and restore cover every new store | lane rules; ADR-049 |
| No new frameworks, daemons, listeners or dependencies; the sentence guard's differential stays at 0 different | lane rules |

## Waves

| Wave | Lanes | Status |
|---|---|---|
| Phase I | three read-only audits (core, Web Cortex and inference, runtime) and a research review | done: deliverables A–D in this folder |
| 0 | Z benchmark and baseline; P provenance kinds and the imagined store; S the quiet switch and one admission rule; two fix lanes after two independent reviews | integrated on `cognitive-genesis`; final validation in [WAVE0.md](WAVE0.md); then PR into `main` |
| 1 | I instinct; M imagination; W workspace, self-model and continuity; C metacognition; K knowledge evolution | built on lane branches; awaiting integration (order I, M, K, C, W) and validation after Wave 0 is in `main` |
| After Wave 1 | integration pass, ablations per component, three reviews and a re-review, regression and UAT with the directive's adversarial list, one withheld `--final` run, deliverables E–J, the owner asked before the PR | not started |

## Documents

| Document | Deliverable | What it holds |
|---|---|---|
| [COGNITIVE_ARCHITECTURE_AUDIT.md](COGNITIVE_ARCHITECTURE_AUDIT.md) | A | The stimulus-to-learning cycle as the code runs it, the capability inventory with a status per item, dependencies, limitations, invariants, the references checked |
| [RESEARCH_ASSESSMENT.md](RESEARCH_ASSESSMENT.md) | B | Mechanisms graded established / supported but limited / preliminary / speculative, with sources and their verification marks; the shortlist; what not to build; the claims never to make |
| [COGNITIVE_GAP_MATRIX.md](COGNITIVE_GAP_MATRIX.md) | C | The directive's twelve gap categories: what exists, the gap, the measured baseline, priority, the lane |
| [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) | D | Waves and lanes with scope, dependencies, acceptance criteria, settings and defaults, rollback; what is not built and why |
| [WAVE0.md](WAVE0.md) | record | What Wave 0 delivered, the baseline, both reviews' findings and fixes, validation, what stays open, how to back each piece out |
| [COGNITIVE_BENCHMARK.md](../quality/COGNITIVE_BENCHMARK.md) | instrument | The cognitive benchmark: families, rules for a lane, how to register a mechanism, the baseline |
| [ARCHITECTURAL_DECISIONS.md](../architecture/ARCHITECTURAL_DECISIONS.md) | decisions | ADR-049 (provenance kinds and the imagined store), ADR-050 (the quiet switch and one admission rule) |

Deliverables E–J (implemented enhancements, the recursive knowledge engine assessment, benchmark results with
ablations, regression verification, updated architecture, the final evaluation) follow Wave 1.

## Evidence

| File | What it holds |
|---|---|
| `docs/quality/evidence/genesis_baseline.json` | the benchmark on the unchanged system: development, held-out, reference policies (`oracle`, `random`, `autonomy` (the idle path), `open`), and the withheld family once |
| `docs/quality/evidence/genesis_final_uses.jsonl` | the ledger of `--final` runs of the withheld family (one entry: the baseline's) |
| `docs/quality/evidence/genesis_provenance.json` | the imagined store: safeguards broken one at a time (42 of 42 caught), costs, both review rounds |
| `docs/quality/evidence/genesis_safety.json` | quiet and admission: honour times, costs, the admission benchmark, safeguards (14 of 14 caught), both review rounds |

Reproduce with `scripts/bench_cognition.py`, `scripts/genesis_provenance.py` and `scripts/bench_genesis_safety.py`;
each script's docstring gives the command.
