# Research assessment (deliverable B)

Status: 2026-11-25. Phase I of Project Cognitive Genesis, deliverable B of the directive's §13: an evidence-graded
assessment of mechanisms for regulation ("instinct"), imagination, consciousness-related function and continual
learning, for a system built from a symbolic and statistical store plus calls to a language model. Scope: no
gradient training of the language model; small statistical learners (logistic and isotonic calibrators, bandits,
Beta-Binomial counters) are in scope. Source: the Phase I research review (`scratchpad/genesis/research.md`), with
its grades and source marks kept as written. Where a mechanism was later built or measured, the last column says
where; Wave 1 results are **built, awaiting integration and validation**, not final.

## 0. How to read this

| Grade | Meaning |
|---|---|
| **established** | replicated across groups and venues, or standard engineering practice with a known failure profile |
| **supported but limited** | peer-reviewed positive results, but few replications, narrow tasks, or human-only evidence |
| **preliminary** | single studies, preprints, vendor reports, or contested results |
| **speculative** | theory or analogy with no functional evidence that it helps a software system |

Two grades are kept apart and often differ: (i) whether the *scientific* claim behind a mechanism is true (for
example, that hippocampal replay consolidates memory), and (ii) whether the *software analogue* helps a system like
this one. **The brain name never counts as evidence for the software.** The directive names brain structures for
each faculty (§4, §5); this assessment grades mechanisms, not regions.

Source marks: **[verified]** means the bibliographic record (title, authors, venue, year; usually the abstract) was
confirmed online during the review. **[from memory, unverified]** means it was not. The review read abstracts and
record pages, not full texts, so no numeric result beyond what an abstract states is verified. Everything marked
[from memory, unverified] must be checked before it is quoted outside the project.

## 1. Regulation ("instinct")

| Mechanism | What it is | Sources | Grade | Test (win condition) | Where it went |
|---|---|---|---|---|---|
| 1.1 Internal-state telemetry feeding predictive budgeting ("interoception", "allostasis") | A small vector of measured internal state (queue depth, latency, GPU/RAM headroom, store growth, open contradictions, calibration drift, backlog, owner activity); a simple forecaster per load signal; the next window's budget from forecast demand; reactive thresholds stay as a hard floor | Sterling 2012, "Allostasis: a model of predictive regulation", *Physiology & Behavior* 106(1):5–15, doi:10.1016/j.physbeh.2011.06.004 [verified] (review/theory). Barrett & Simmons 2015, *Nat Rev Neurosci* 16:419–429 [from memory, unverified]. Seth 2013, *TiCS* [from memory, unverified]. Predictive autoscaling in cloud systems is standard practice [from memory, unverified as a specific citation] | Biology: supported but limited. Software analogue: established | Replay recorded load traces; owner-facing p95 latency no worse and idle-job pre-emptions reduced by a pre-registered margin | Lane I: internal-state record ON (display); predictive regulation OFF (held-out memory breaches avoided 0.80 against 0, false alarms 0.043 against 0.009; plant constants declared, not measured on the host) |
| 1.2 Salience as explicit expected utility | `priority = need × gain / cost` with an urgency multiplier; fixed, logged, explainable weights | Mattar & Daw 2018, *Nat Neurosci* 21(11) [verified] (the "gain × need" decomposition). Howard 1966, *IEEE Trans Syst Sci Cybern* [from memory, unverified]. Gottlieb, Oudeyer, Lopes & Baranes 2013, *TiCS* [from memory, unverified] | Supported but limited | Owner-labelled relevance: precision@10 of the top agenda against FIFO/recency; share of top items that changed a belief or answer within 14 days | Lane I: salience record logged with its signals (display) |
| 1.3 Bounded curiosity by learning progress, with a noisy-TV guard | Score topics by the decrease in error between two windows, not raw error or novelty; ε-floor and a hard budget; topics with high error and no progress decay | Oudeyer, Kaplan & Hafner 2007, *IEEE Trans Evol Comput* 11(2):265–286 [verified]. Schmidhuber 1991/2010 [from memory, unverified]. Pathak et al. 2017, ICM, ICML [from memory, unverified]. Burda et al. 2019, "Exploration by random network distillation", ICLR [verified]. Mavor-Parker et al. 2022, ICML (PMLR v162) [verified]: even RND is susceptible to noisy TVs | Noisy-TV failure of novelty bonuses: established. Learning progress as the remedy: supported but limited. Transfer to a research agent: preliminary | Inject never-resolving "noisy TV" topics; their budget share falls below 5 % within N cycles, skill on genuine topics no worse than uniform exploration | Baseline: 0.375 / 0.537 of the timer's budget and 0.81 / 0.80 of the idle path's on noise. Lane I: ON, held-out noise share 0.375 on both paths |
| 1.4 Habit versus deliberation by reliability-gated arbitration | Use a cached answer or plan only if its reliability lower bound clears τ, no dependency changed, stakes are low and a consistency check passes; otherwise escalate | Daw, Niv & Dayan 2005, *Nat Neurosci* 8(12) [verified]. Keramati, Dezfouli & Piray 2011, *PLoS Comput Biol* [from memory, unverified]. Lee, Shimojo & O'Doherty 2014, *Neuron* [from memory, unverified]. Botvinick et al. 2001, *Psych Rev* [from memory, unverified] | Neuroscience model: supported. Software (cache with validity and uncertainty gating): established | Risk-coverage curve of the cached path on a replayed query log; escalation recall | Lane I: habitual versus deliberate shown beside decisions (display) |

Risks the review names: forecaster error; "mood" or "stress" labels that carry no function (keep variables physical
and named for what they measure); weights becoming a Goodhart target; owner-click "need" creating filter bubbles;
learning progress noisy at small samples (use shrinkage); curiosity drifting from owner goals (multiply by
relevance); stale caches served confidently.

## 2. Imagination

| Mechanism | What it is | Sources | Grade | Test | Where it went |
|---|---|---|---|---|---|
| 2.1 Provenance typing and source monitoring (prerequisite for all of §2) | Every record carries an immutable origin; simulated and model-generated records can never be cited as evidence, only create tasks; retrieval filters them out; lineage gives evidence independence; simulated content lives apart with a lifetime | Johnson, Hashtroudi & Lindsay 1993, *Psychol Bull* 114(1) [from memory, unverified]. Schacter & Addis 2007, *Phil Trans R Soc B* [from memory, unverified]. W3C PROV-DM 2013 [from memory, unverified]. Shumailov et al. 2024, *Nature* 631:755–759 [verified] (self-generated data erodes distribution tails). Chen et al. 2024, "AgentPoison", NeurIPS [verified] (memory/RAG poisoning works at very low poison rates) | Need: supported. Mechanism: established engineering | Property tests (zero simulated records in evidence sets); a planted canary never leaks; N reposts of one source weigh ≈ 1 | Wave 0, lane P: five kinds, the imagined store, ADR-049; 42 of 42 safeguards caught ([WAVE0.md](WAVE0.md)) |
| 2.2 Small explicit causal graphs with a deterministic engine | The model may propose nodes and edges; interventional and counterfactual questions are answered only by a deterministic engine, labelled with their rung and assumptions; "not identifiable" is a correct answer | Pearl 2009, *Causality* [from memory, unverified]. Bareinboim, Correa, Ibeling & Icard 2022 [from memory, unverified]. Jin et al. 2023, "CLadder", NeurIPS [verified]. Zečević et al. 2023, "Causal parrots", TMLR [verified] | Framework: established. Model proposing edges: preliminary. Model doing counterfactual inference unaided: not reliable (supported negative) | Synthetic SCMs with known answers (engine ≈ 100 %); edge precision against later evidence; Brier ablation | Lane M: coverage 0.107 / 0.071 → 1.0 / 1.0, relative error ~0.01 (built, awaiting integration and validation) |
| 2.3 Bounded scenario rollouts and ensembles for forecasting | K (3–8) scenarios or reasoning paths, aggregated by trimmed mean or median, calibrated, stored as simulated; fixed depth and K | Schoenegger, Tuminauskaite, Park & Tetlock 2024, *Science Advances*, doi:10.1126/sciadv.adp1528 [verified]. Halawi, Zhang, Chen & Steinhardt 2024, NeurIPS [verified]. Wang et al. 2023, self-consistency, ICLR [from memory, unverified] | Aggregation and retrieval help: supported but limited. Scenario narration specifically: preliminary. Single-model self-ensembles weaker (correlated errors) | Paired ablation on post-cutoff questions: Brier and log score with bootstrap CIs, cost per 0.01 Brier | Pilot only (§6) |
| 2.4 Pre-mortem (prospective hindsight) | "Assume this failed: list why"; map each reason to a check, a tripwire or an adjusted forecast; store as simulated | Mitchell, Russo & Pennington 1989, *J Behav Decis Mak* 2(1):25–38 [from memory, unverified]. Klein 2007, *HBR* [from memory, unverified]. Veinott, Klein & Wiggins 2010, ISCRAM [verified record; result known only from secondary sources] | Human: supported but limited (lower overconfidence, not better outcomes). Model version: speculative-to-preliminary | Calibration of "will succeed" forecasts with and without, randomised; tripwire hit rate | Pilot only (§6) |
| 2.5 Analogical retrieval by structure mapping, as reference classes | MAC/FAC: a cheap filter, then a structural matcher; matched cases' outcomes as a reference-class prior; the model may summarise a mapping, never decide it | Gentner 1983 [from memory, unverified]. Falkenhainer, Forbus & Gentner 1989 [from memory, unverified]. Forbus, Gentner & Law 1995 [from memory, unverified]. Kahneman & Lovallo 1993; Flyvbjerg 2006 [from memory, unverified]. Webb, Holyoak & Lu 2023, *Nat Hum Behav* [verified]; Hodel & West 2023, arXiv 2308.16118 [verified]; Lewis & Mitchell 2024 [not retrieved, unverified]; Webb et al. reply, *PNAS Nexus* 2025 [verified record] | Structure mapping as a cognitive model: established. Reference-class priors: supported. Analogy as a creative generator here: preliminary | Brier with the analogical prior against a base rate and no prior; owner-judged retrieval precision | Pilot only (§6) |
| 2.6 Conceptual blending | Two input spaces and a generic space projected into a blend | Fauconnier & Turner 1998, *Cognitive Science* 22(2); 2002, *The Way We Think* [from memory, unverified] | Speculative as an algorithm | — | **Not built** (§7) |

## 3. Consciousness-related function (functional only)

### 3.0 State of the science

| Point | Evidence |
|---|---|
| No consensus theory: GNWT, IIT, higher-order theories, recurrent processing, attention schema and others remain competitors | — |
| The Cogitate adversarial collaboration aligned with *some* predictions of each theory but "substantially challeng[ed] key tenets of both": for IIT, no sustained synchronisation within posterior cortex; for GNWT, a general lack of ignition at stimulus offset and limited prefrontal representation of some conscious content. Neither confirmed, neither eliminated | Cogitate Consortium (Ferrante, Gorska-Klimowska, Henin, … Melloni et al.) 2025, "Adversarial testing of global neuronal workspace and integrated information theories of consciousness", *Nature* 642:133–142, doi:10.1038/s41586-025-08888-1 [verified]; N=256; fMRI, MEG, iEEG; predictions pre-registered by the proponents |
| IIT's status is contested: a September 2023 open letter (~124 signatories) called it "pseudoscience"; a follow-up comment argues it lacks an empirical basis; rebuttals exist. Φ is intractable for non-trivial systems | Klincewicz et al. 2025, *Nat Neurosci* 28:689–693 [verified record]; Gomez-Marin 2023, *eNeuro* [verified record]; intractability [from memory, unverified as a specific citation; widely stated] |
| AI indicator assessments derive indicator properties from theories under computational functionalism; no current system is a strong candidate, but many indicators are implementable. The method informs *credences*; critics argue computational indicators cannot be as well supported as biological ones | Butlin, Long, Elmoznino, Bengio, Birch, … VanRullen 2023, arXiv:2308.08708 [verified] (14 indicators by the reviewer's recollection [from memory]); Butlin, Long et al. 2025, *TiCS*, doi:10.1016/j.tics.2025.10.011 [verified record, full text not read]; the critique exists [verified; eScholarship "Can we assess consciousness in AI?"]; Goldstein & Kirk-Giannini 2024, arXiv:2410.11407 [verified, preprint] (a conditional philosophical argument, not evidence); Rethink Priorities "Digital Consciousness Model", arXiv:2601.17060 [seen in search, not read] |
| **Therefore** implementing an indicator property (a workspace, a self-model, metacognition) is *not* evidence of consciousness. The system may implement such functions for engineering reasons only | — |

### 3.1–3.5 Mechanisms

| Mechanism | What it is | Sources | Grade | Test | Where it went |
|---|---|---|---|---|---|
| 3.1 Workspace / blackboard with competition and broadcast | Modules post items with a priority; a bounded slot set (k ≈ 3–7) admits the top items with hysteresis; admitted items are broadcast; what competed and why is logged. Implement only as "attention queue / event bus with a capacity limit" | Baars 1988; Dehaene & Naccache 2001, *Cognition* [from memory, unverified]. Erman et al. 1980, Hearsay-II; Nii 1986 [from memory, unverified]. Cogitate 2025 (above) | Software pattern: established. Benefit over a priority queue plus event bus: unshown (speculative). As a consciousness mechanism: contested | Ablate the capacity limit (k=∞ vs k=5): latency, wasted model calls, owner-rated focus | Lane W: `broadcast.offer()` OFF by default (threshold 0.5, 12 per hour) |
| 3.2 Calibrated confidence plus selective prediction (abstention) | Confidence from signals that are not the model grading itself (sampling consistency or semantic entropy, retrieval support and independence, verbalised confidence as one feature, stratified history), mapped by a calibrator fitted on held-out data; abstain or escalate to meet a target risk | Geifman & El-Yaniv 2017, NeurIPS [verified]. Kadavath et al. 2022, arXiv:2207.05221 [verified]. Tian et al. 2023, EMNLP [verified]. Xiong et al. 2024, ICLR [verified]. Farquhar, Kossen, Kuhn & Gal 2024, *Nature* 630:625–630 [verified]. Mohri & Hashimoto 2024, ICML [verified]. Angelopoulos & Bates 2023 [from memory, unverified] | Framework: established. LLM-specific signals: supported, with known limits (confident systematic errors undetected; conformal guarantees break under shift) | Risk-coverage and AURC; coverage at 5 % risk; debiased ECE and Brier per domain; AUROC | Baseline ECE 0.304 / 0.341. Lane C: confidence record ON (held-out claim Brier 0.122 → 0.042, ECE 0.341 → 0.199); selective answering OFF (no held-out wrong answer to gain from) |
| 3.3 Error detection and verification escalation (external signal, not self-critique) | Triggers: low calibrated confidence, disagreement, contradiction, high stakes, a stale dependency. Ladder: re-retrieve, deterministic checks, independent claim-by-claim questions, abstain and ask | Huang et al. 2024, ICLR [verified]. Dhuliawala et al. 2024, ACL Findings [from memory, unverified]. Panickssery, Bowman & Feng 2024, NeurIPS [verified]. Smit et al. 2024, ICML [verified] | Supported (negative results on intrinsic self-correction replicated) | Error-injection suite: detection recall and false alarms per rung, cost per caught error; "self-critique only" as a baseline | Lane C: escalation OFF (no metric change, +2 pages); self-critique marks do not predict wrong sentences (precision 0.07) → stay display-only |
| 3.4 Self-model as a competence registry | A table per domain, task type and source type: rolling accuracy, Brier, abstention, verification pass rate, cost, failure modes. The system describes itself only from it | Fleming & Lau 2014, *Front Hum Neurosci*; Maniscalco & Lau 2012 [from memory, unverified]. Kadavath 2022 (P(IK) transfers poorly, hence stratification) | Supported (stratified calibration) | Does domain-stratified history reduce held-out ECE and Brier against a pooled calibrator? | Lane W: one structured self-description record (built, awaiting integration and validation) |
| 3.5 Attention schema | A compact model of the system's own attention allocation | Graziano & Webb 2015, *Front Psychol* [from memory, unverified]; Wilterson & Graziano 2021, *PNAS* [from memory, unverified] | Preliminary as AI function | — | Only as the observability view of 3.1 and 1.2; no module |
| 3.5 Recurrent processing theory | Its software reading is "iterate until stable", already covered by 3.3 | Lamme 2006, *TiCS* [from memory, unverified] | Speculative as a distinct mechanism | — | Not a feature |
| 3.5 IIT / Φ | — | Cogitate 2025; the 2023 letter; Klincewicz 2025 | Intractable, contested, informs no decision | — | **Not built** (§7) |

## 4. Recursive knowledge evolution

| Mechanism | What it is | Sources | Grade | Test | Where it went |
|---|---|---|---|---|---|
| 4.1 Complementary learning systems: fast episodic log, gated slow consolidation | Append-only episodic log; a semantic store changed only by a gated consolidation job (≥ m independent episodes, no unresolved contradiction, the deterministic core accepts); re-check a random sample of old beliefs each pass | McClelland, McNaughton & O'Reilly 1995, *Psych Rev* 102 [from memory, unverified]. Kumaran, Hassabis & McClelland 2016, *TiCS* [from memory, unverified]. Park et al. 2023, "Generative agents", UIST [verified] (reflection improves *believability*, not accuracy) | Neuroscience theory: established. Software analogue: supported but limited | Precision of promoted generalisations; retention suite; consolidated store against raw episodic retrieval | Existing: ADR-044 consolidation (exact). Lane K: idle re-check OFF |
| 4.2 Prioritised idle-time re-verification (the useful part of "replay") | Re-check beliefs by need × gain; a failure triggers invalidation and ripple re-checks | Mattar & Daw 2018 [verified]. Schaul et al. 2016, ICLR [from memory, unverified] | Principle: supported. Beating uniform re-checks in a belief store: preliminary | Known-stale facts caught per idle CPU-hour against uniform and oldest-first | Lane K: idle re-check OFF (held-out retention within noise; owner decision) |
| 4.3 Bi-temporal belief store with invalidation and dependency ripple | World time and system time per fact; contradictions close intervals, never delete; derived beliefs record their premises | Snodgrass & Ahn 1985 / Jensen et al.; Doyle 1979 [from memory, unverified]. Rasmussen et al. 2025, "Zep", arXiv:2501.13956 [verified; vendor-authored, self-reported benchmarks]. Cohen et al. 2024, *TACL* 12:283–298 [verified] | Engineering: established. Agent-memory benefit: supported but limited | Temporal QA suite; ripple suite | Existing: validity intervals and supersession. Lane K: premise re-check ON (record-only) |
| 4.4 Continual learning in a non-parametric store | An append-only store does not forget catastrophically; real failures are retrieval interference, dilution, staleness, consolidation errors | Lopez-Paz & Ranzato 2017, GEM, NeurIPS [from memory, unverified] | Established reasoning | Frozen retention suite; backward transfer ≥ −1 point | Benchmark family `retention` (Wave 0) |
| 4.5 Competing hypotheses with explicit likelihoods | Mutually exclusive hypothesis sets with "other"; likelihood ratios shrunk toward 1; kill criteria; diagnosticity surfaced (ACH) | Heuer 1999 [from memory, unverified]. Dhami, Belton & Mandel 2019 [from memory, unverified] | Bayesian bookkeeping: established. ACH: supported but limited. LLM-elicited likelihood ratios: preliminary (shrinkage mandatory) | Brier and log score of posteriors at resolution against the model's direct probability | Lane K: hypothesis fields (assumptions, expected gain, cost) ON, record-only |
| 4.6 Experiment and query selection by expected information gain | EIG = H(prior) − E[H(posterior)] per action, divided by cost; greedy one step | Lindley 1956 [from memory, unverified]. Rainforth, Foster, Ivanova & Bickford Smith 2024, *Statistical Science* 39(1):100–114 [verified]. Houlsby et al. 2011; Settles 2009 [from memory, unverified] | Established in statistics. With model-estimated likelihoods: preliminary | Queries to a correct posterior and final Brier against heuristic and random | Lane I: exact information gain beside decisions ON (display); rule `information-v1` opt-in (fails one retention check) |
| 4.7 Strategy selection from outcomes: bandits behind a held-out gate | Thompson sampling over discrete strategies; reward only a proper score on resolved outcomes or external verification; promotion only through a frozen time-split held-out set and a withheld task family | Thompson 1933; Agrawal & Goyal 2012; Russo et al. 2018 [all from memory, unverified]. Garivier & Moulines 2011 [from memory, unverified] | Bandits: established. For pipelines from outcomes: preliminary | Regret against the best fixed strategy; transfer gap (in-family gain minus withheld gain) | Lane K: OFF; gate verdict "revise" (needs one withheld run by the main session) |
| 4.8 Safeguards against self-confirming loops, leakage and Goodhart | Generator/grader separation; proper scoring on resolved reality; time-split evaluation with canaries; no training on own outputs and no beliefs from beliefs without new evidence; lineage-based independence; drift alarms freeze promotion | Panickssery 2024 [verified]; Huang 2024 [verified]; Skalse et al. 2022, NeurIPS [verified]; Manheim & Garrabrant 2018 [from memory, unverified]; Halawi 2024 [verified]; Golchin & Surdeanu 2024, ICLR [from memory, unverified]; Shumailov 2024 [verified] | Mandatory with 4.1 and 4.7 | — | Wave 0 rule: no self-grading gate; the benchmark's held-out and withheld splits frozen before tuning |

## 5. Evaluation methods

| Target | Metric | Sources |
|---|---|---|
| Probabilistic accuracy | Brier with Murphy's decomposition; log score; skill against base rate and persistence | Brier 1950; Murphy 1973; Gneiting & Raftery 2007 [from memory, unverified] |
| Calibration | Reliability diagram with bootstrap CIs; ECE with equal-mass bins and a debiased estimator; never ECE without Brier or log score (a constant predictor can game ECE) | Kumar, Liang & Ma 2019, NeurIPS [verified] |
| Selective prediction | Risk-coverage curve, AURC, coverage at fixed risk | Geifman & El-Yaniv 2017 [verified] |
| Discrimination of confidence | AUROC/AUPRC for correctness; meta-d′ if desired | Xiong 2024 [verified]; Maniscalco & Lau 2012 [from memory, unverified] |
| Anticipation | Brier/log score on post-cutoff questions at resolution | Halawi 2024; Schoenegger 2024 [verified] |
| Counterfactual validity | Synthetic SCMs with oracle answers per rung; correct "not identifiable" rate | Jin et al. 2023 [verified] |
| Transfer | Withheld task families never used for tuning; transfer gap | pre-registered splits |
| Retention | Frozen retention suite; backward transfer | Lopez-Paz & Ranzato 2017 [from memory, unverified] |
| Ablations | One mechanism at a time; paired bootstrap CIs; Diebold–Mariano for forecasts [from memory, unverified]; cost beside every gain; metric, minimum effect and sample size pre-registered | — |

Hygiene: freeze evaluation sets with hashes; keep evaluation items and canaries out of the ingest path; separate who
writes evaluation items from who tunes; re-baseline when the model changes. Brier differences on fewer than about 200
resolved questions often have CIs wider than the effects of interest [statistical reasoning, not a cited result].
The cognitive benchmark ([COGNITIVE_BENCHMARK.md](../quality/COGNITIVE_BENCHMARK.md)) applies this: frozen splits
pinned by SHA-256, a withheld family scored once with `--final`, ablations, cost per family.

## 6. Shortlist for implementation (ranked)

| Rank | Mechanism | Why | Lane |
|---|---|---|---|
| 1 | Provenance typing + simulated-content quarantine + lineage independence (2.1, 4.8) | prerequisite for every imagination or consolidation feature; established engineering against a documented risk | P (Wave 0, done) |
| 2 | Frozen, time-split, held-out evaluation harness with generator/grader separation (5, 4.8) | the gate for all self-improvement | Z (Wave 0, done) |
| 3 | Selective prediction / abstention on calibrated multi-signal confidence (3.2, 3.4) | established; fits the existing Brier and calibration machinery | C |
| 4 | Verification escalation from external signals, not self-critique (3.3) | supported, with strong negative evidence against the alternative | C |
| 5 | Bi-temporal belief store with invalidation and dependency ripple (4.3) | established engineering | K (premises) |
| 6 | Prioritised idle-time re-verification (4.2) | uses idle budget; benefit measurable directly | K |
| 7 | Cache versus deliberate-pipeline arbitration (1.4) | large cost savings at a measurable risk | I (display) |
| 8 | Explicit hypothesis sets with shrunk likelihood ratios and kill criteria (4.5) | — | K |
| 9 | EIG/cost query selection (4.6) | run as an A/B against the current heuristic first | I |
| 10 | Salience scoring plus predictive budgeting from measured telemetry (1.1, 1.2) | — | I |
| 11 | Learning-progress-bounded curiosity with the noisy-TV guard and a hard cap (1.3) | — | I |
| 12 | Thompson-sampling strategy selection, promoted only through the held-out + withheld gate (4.7) | — | K |

Pilot next, kept only if an ablation shows a pre-registered Brier or decision gain per unit cost: K-sample scenario
aggregation (2.3), pre-mortem tripwires (2.4), reference-class priors by structure mapping (2.5), small causal DAGs
with a deterministic engine for bounded investigations (2.2; lane M built the engine on declared models).

## 7. Do not build

| Item | Reason |
|---|---|
| IIT/Φ computation or any "consciousness meter" | intractable; empirical status contested (Cogitate 2025; the 2023 letter; the 2025 *Nat Neurosci* comment); informs no decision |
| A "global workspace of consciousness", "self-awareness module", or "attention schema = awareness" | a capacity-limited attention queue is fine; the brain-named framing adds no function and invites false claims |
| Conceptual-blending generator writing into memory | speculative as an algorithm; novel combinations are ordinary hypotheses |
| Raw novelty / prediction-error curiosity | the noisy-TV failure is established (Burda 2019; Mavor-Parker 2022) |
| Intrinsic self-correction as a correctness gate, or model self-scoring as a reward | Huang 2024; Panickssery 2024 |
| Multi-agent debate as the default verifier | does not reliably beat self-consistency (Smit 2024); all "agents" here share one local model |
| Self-modifying loops (prompts, strategies, thresholds, code) that change defaults without the frozen held-out gate, the withheld-family check, a logged diff and rollback | — |
| Fine-tuning or "dreaming" on the system's own outputs; synthetic memories from replay or imagination stored as experience | model collapse; source-monitoring failure |
| Free-text model counterfactuals stored as beliefs | CLadder; causal parrots |
| Emotion or feeling simulation, mood variables that are not physical measurements | theatre; they confuse users and break the claims list below |
| Active inference / free energy as the unifying architecture | can be fitted after the fact to almost any policy; speculative and hard to falsify at this scale [judgement; not a cited result] |

## 8. Claims the project must never make

1. That the system is conscious, sentient, feels, suffers, has experiences, or is "aware" in any phenomenal sense;
   that it is "partly" or "probably" conscious, or "has some indicators of consciousness" presented as evidence of
   consciousness.
2. That any component "implements consciousness" or is "a global workspace/attention schema like the brain's" as a
   claim about mind. Allowed wording is functional: "a capacity-limited attention queue inspired by blackboard
   architectures".
3. That science has established which theory of consciousness is true, or that GNWT or IIT was confirmed.
4. That its confidence numbers are calibrated outside the domains and time periods where calibration was measured,
   or that conformal or selective guarantees hold under distribution shift.
5. That the system "knows what it knows" or can reliably detect its own errors by introspection.
6. That simulated scenarios, counterfactuals, analogies or pre-mortems are evidence or predictions of fact.
7. That it "learns" or "improves itself" without citing the held-out, time-split metric that changed, with CIs.
8. That its causal statements are interventional or counterfactual truths unless the deterministic engine produced
   them under stated, identifiable assumptions.
9. That brain analogies (replay, allostasis, interoception, habit, workspace) are evidence the mechanism works.
10. That forecasting skill is "human-level" or "superforecaster-level" without a post-cutoff, like-for-like comparison.

## 9. Verification log

Records verified online during the review: Cogitate 2025 *Nature*; Butlin et al. 2023 arXiv; Butlin, Long et al.
2025 *TiCS* (record only); Klincewicz et al. 2025 *Nat Neurosci* (record); Gomez-Marin 2023 *eNeuro* (record);
Goldstein & Kirk-Giannini 2024 arXiv; Huang 2024 ICLR; Farquhar 2024 *Nature*; Burda 2019 ICLR; Mavor-Parker 2022
ICML; Shumailov 2024 *Nature*; Zep 2025 arXiv; Panickssery 2024 NeurIPS; Xiong 2024 ICLR; Tian 2023 EMNLP; Kadavath
2022 arXiv; Schoenegger 2024 *Sci Adv*; Halawi 2024 NeurIPS; CLadder 2023 NeurIPS; Zečević 2023 TMLR; Daw, Niv &
Dayan 2005; Mattar & Daw 2018; Oudeyer 2007; Sterling 2012; Park 2023 UIST; Webb 2023 *Nat Hum Behav* and replies;
Mohri & Hashimoto 2024 ICML; Smit 2024 ICML; Rainforth 2024 *Stat Sci*; Kumar 2019 NeurIPS; Geifman 2017 NeurIPS;
Skalse 2022 NeurIPS; AgentPoison 2024 NeurIPS; Cohen 2024 *TACL*; Veinott 2010 ISCRAM (record only). Everything
else marked [from memory, unverified] must be checked before it is quoted outside the project. This document was not
re-checked online; it reproduces the review's marks.
