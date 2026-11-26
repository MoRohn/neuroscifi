# Project Cognitive Genesis

Status: 2026-11-25. Phase I (discovery and research) is done and Wave 0 is integrated on branch `cognitive-genesis`
(`76a478e`), reviewed twice and fixed; its final validation is in [WAVE0.md](WAVE0.md). Wave 1 is **integrated
(`3c2a3bc`), reviewed three times, fixed and reconciled** (`gen-rec1`): its record, the shipped defaults and the
benchmark on the reconciled tree are in [WAVE1.md](WAVE1.md). As shipped it changes no research outcome: what is on
records or displays, everything that changes behaviour is off. A re-review of the fixes, two more fix lanes and a
second check followed; the final validation (`816fa12`) and the one look at the withheld family are recorded there
(§7, §8). **Wave 2 is integrated on `genesis-wave2` (`828ca8e`), with its fix lanes and reconciliation**; its
record is [WAVE2.md](WAVE2.md). Its three main results are negative or neutral: the integrated loop shows no gain
beyond the baseline's spread on held-out; no knowledge-evolution mechanism shows a held-out gain over repeated cycles
(the existing loop beats no loop; knowledge decays in every configuration, partly by the test world's design); fault
injection found real failures, now guarded. Two independent reviews corrected the evidence and found two product
defects in Wave 2's additions (the false arbitration record, a voice delivery after a forget); both are fixed, and a
third check of the fixes found six low issues, also fixed (WAVE2.md §4). No default changed; one older behaviour
changed (a timer pass with a task limit now counts a queued refresh, WAVE2.md). Wave 2 took no look at the withheld
family. All ten deliverables (A–J) are written; the final account for the owner is
[FINAL_EVALUATION.md](FINAL_EVALUATION.md).

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
| 1 | I instinct; M imagination; W workspace, self-model and continuity; C metacognition; K knowledge evolution | integrated on `genesis-wave1` (`3c2a3bc`); three reviews; fix lanes A, B, C; reconciled on `gen-rec1`; record in [WAVE1.md](WAVE1.md) |
| 1, closing | re-review of the fix round, fix lanes D and E, a second check, final validation, the one withheld look | done: [WAVE1.md](WAVE1.md) §6.5, §7, §8; then PR into `main` |
| 2 | U regression and cognitive UAT; F the recursive knowledge assessment; L the integration loop; X follow-ups; D deliverables E, G, I, J | U, F, L integrated on `genesis-wave2` (`828ca8e`); X and D from it; record in [WAVE2.md](WAVE2.md) |

## Documents

| Document | Deliverable | What it holds |
|---|---|---|
| [COGNITIVE_ARCHITECTURE_AUDIT.md](COGNITIVE_ARCHITECTURE_AUDIT.md) | A | The stimulus-to-learning cycle as the code runs it, the capability inventory with a status per item, dependencies, limitations, invariants, the references checked |
| [RESEARCH_ASSESSMENT.md](RESEARCH_ASSESSMENT.md) | B | Mechanisms graded established / supported but limited / preliminary / speculative, with sources and their verification marks; the shortlist; what not to build; the claims never to make |
| [COGNITIVE_GAP_MATRIX.md](COGNITIVE_GAP_MATRIX.md) | C | The directive's twelve gap categories: what exists, the gap, the measured baseline, priority, the lane; the shipped status after Waves 1 and 2 |
| [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) | D | Waves and lanes with scope, dependencies, acceptance criteria, settings and defaults, rollback; what is not built and why |
| [IMPLEMENTED_ENHANCEMENTS.md](IMPLEMENTED_ENHANCEMENTS.md) | E | Every mechanism built in Waves 0–2: module, setting and shipped default (read from the code), ADR, tests, evidence, how to back it out |
| [RECURSIVE_KNOWLEDGE_ASSESSMENT.md](RECURSIVE_KNOWLEDGE_ASSESSMENT.md) | F | Directive §7 mapped to the code (built / partial / not built); the learning-cycles experiment (14 daily cycles of a simulated world through the real modules), per-mechanism verdicts, forgetting and overconfidence, what is unverified |
| [BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md) | G | Baseline against the enhanced system for each category of directive §10, with ablations, held-out and withheld results, references and costs; what is not measured; the §14 success criteria one by one |
| [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) | H | The 20 mandatory test areas, the adversarial items, fault injection and its guards, rollback, Wave 0 equivalence, what is unverified and how an owner would verify it |
| [ARCHITECTURE_AFTER_GENESIS.md](ARCHITECTURE_AFTER_GENESIS.md) | I | The subsystems and information flows after Waves 0–2: which record each part writes, who reads it, which setting gates it, what is real, display-only or absent at the shipped defaults; one diagram |
| [FINAL_EVALUATION.md](FINAL_EVALUATION.md) | J | One page for the owner, then what improved and what did not, what remains limited and unverified, the decisions pending, and the next research directions ranked |
| [WAVE0.md](WAVE0.md) | record | What Wave 0 delivered, the baseline, both reviews' findings and fixes, validation, what stays open, how to back each piece out |
| [WAVE1.md](WAVE1.md) | record | What each Wave 1 lane built, the shipped defaults, the benchmark (split and wide seeds), the three reviews' findings and fixes, what stays open, how to back each piece out |
| [WAVE2.md](WAVE2.md) | record | What each Wave 2 lane built, the two new settings, the integration, learning-cycles and regression results, what stays open, how to back each piece out |
| [COGNITIVE_BENCHMARK.md](../quality/COGNITIVE_BENCHMARK.md) | instrument | The cognitive benchmark: families, rules for a lane, how to register a mechanism, the baseline |
| [ARCHITECTURAL_DECISIONS.md](../architecture/ARCHITECTURAL_DECISIONS.md) | decisions | ADR-049 (provenance kinds and the imagined store), ADR-050 (the quiet switch and one admission rule), ADR-051 to ADR-055 (Wave 1), ADR-056 (the integration loop, Wave 2) |

## Evidence

| File | What it holds |
|---|---|
| `docs/quality/evidence/genesis_baseline.json` | the benchmark on the unchanged system: development, held-out, reference policies (`oracle`, `random`, `autonomy` (the idle path), `open`), and the withheld family once |
| `docs/quality/evidence/genesis_final_uses.jsonl` | the ledger of `--final` runs of the withheld family (three entries: the baseline's, and Wave 1's look: the baseline re-scored, then the Wave 1 mechanisms) |
| `docs/quality/evidence/genesis_wave1_withheld.json` | Wave 1's one look at the withheld family: both runs, headline metrics and full results |
| `docs/quality/evidence/genesis_provenance.json` | the imagined store: safeguards broken one at a time (42 of 42 caught), costs, both review rounds |
| `docs/quality/evidence/genesis_safety.json` | quiet and admission: honour times, costs, the admission benchmark, safeguards (14 of 14 caught), both review rounds |
| `docs/quality/evidence/genesis_wave1.json` | Wave 1 on the reconciled tree: every setting off (= the recorded baseline), as shipped, everything on, an ablation per component, both splits; the offline research benchmark and acceptance against Wave 0; regenerated after the re-review of the fix round corrected the counterfactual headline (every answerable question counted) |
| `docs/quality/evidence/genesis_wave1_wide.json` | the same comparisons on 12 further seeds (5001–5012), supplementary |
| `docs/quality/evidence/genesis_{instinct,imagination,workspace,monitoring,knowledge}.json`, `genesis_fix1a.json`, `genesis_fix1b.json`, `genesis_baseline_rescored.json` | the lanes' own measurements, the fix lanes' re-measures, the baseline under the review's scoring |
| `docs/quality/evidence/genesis_learning_cycles.json` | Wave 2 lane F: the learning-cycles experiment, both splits, every configuration, the verdicts, development probes, the cognitive benchmark's retention and adversarial families with lane K on (`scripts/bench_learning_cycles.py`) |
| `docs/quality/evidence/genesis_integration.json` | Wave 2 lane L: the integrated family on both splits (baseline, defaults, each link alone, integrated, integrated minus one link), the baseline's spread, the paired comparisons, retention, adversarial and self-description, and the same runner before any link (`scripts/bench_wave2_integration.py`) |
| [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) | Wave 2 lane U: no evidence file; the counts of the fault-injection, rollback and equivalence tests and the `added_wave2` adversarial results are in the document, the tests in `tests/uat/` |

The ledger was not extended in Wave 2: no withheld run.

Reproduce with `scripts/bench_cognition.py`, `scripts/bench_wave1.py`, `scripts/bench_wave2_integration.py`,
`scripts/bench_learning_cycles.py`, `scripts/genesis_provenance.py` and `scripts/bench_genesis_safety.py`;
each script's docstring gives the command.
