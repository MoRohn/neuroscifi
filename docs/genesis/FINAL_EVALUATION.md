# Final evaluation (deliverable J)

Status: 2026-11-25, on the Wave 2 integration tree `828ca8e` (branch `genesis-wave2`). Deliverable J of the
directive's §13: what improved, what did not, what remains limited, what is unverified, the decisions waiting for the
owner, and the research directions worth the most next. Every number is copied from the evidence file or document
named beside it. Nothing here claims or implies consciousness, sentience, awareness, general intelligence or
superintelligence; the mechanisms are functions with names that say what they do, and the measurements are task
scores on synthetic worlds.

## One page, in plain words

**What Project Cognitive Genesis set out to do.** Add ways for NeuroSciFi to anticipate, imagine, describe and check
itself, and keep its knowledge up to date, without replacing anything that worked, and keep each one only if a fair
test showed it helps.

**What it delivered.** 35 new pieces across three waves (counted in
[IMPLEMENTED_ENHANCEMENTS.md](IMPLEMENTED_ENHANCEMENTS.md), benchmarks not included), most behind their own switch, plus a test bench with
held-back test worlds, a store that keeps imagined content apart from real evidence, and one owner switch ("quiet")
that pauses everything the system does unasked.

**What got better, measurably:**

- **Answering "what if" and "what happens next" questions about what it has learned.** On held-out test worlds, from
  0.222 to 1.000 of the learned-graph questions answered within tolerance (a trivial "answer 0" scores 0.407), and
  0.967 on worlds held back until the end (`genesis_wave1.json`, `genesis_wave1_withheld.json`). These are commands
  the owner types; they write nothing unless the owner keeps the answer.
- **Saying what it is doing and why, in one place.** 29 of 30 questions about its own state answered correctly
  instead of 24, from one record instead of four separate screens; 15 of 15 on the held-back worlds.
- **Staying usable when a new part breaks.** Breaking each new part on purpose broke the owner's question, a status
  page or the timer in 44 of 75 cases; small guards fixed all of them (lane U).
- **Avoiding memory overloads, in a simulation only.** A forecast that holds back new background work avoided 0.80
  of memory breaches in a simulated machine, but gave up more background work. It is off; it was not tested on the
  real machine.

**What did not get better:**

- **Making the parts work together gave no gain** and cost about 70 % more time per pass (lane L).
- **No knowledge mechanism helped knowledge stay correct over repeated days, and knowledge goes out of date in every
  configuration tried.** The existing loop slows that decay; nothing added by this project slowed it further (lane F).
- **The system's own confidence numbers are not better than a two-line rule**, and there is not enough real data to
  show any confidence number at all.
- **Imagined futures for its predictions were worse than guessing "all equally likely"** on held-out worlds.

**What is still unknown.** Nothing was tested on the open web, with the live answer model, on the DGX Spark's real
memory and GPU, with real phones or real speech.

**What is waiting for you (the owner):** seven switches or rules, listed in §5 with the evidence for each. The one with
the most potential is a rule to replace an out-of-date fact automatically when newer, independent sources agree;
today such conflicts are always asked of you and never decided, and that is what most limits how current the
knowledge stays.

## 1. What improved, with evidence

| Improvement | Evidence (held-out unless said) | Ships | Source |
|---|---|---|---|
| Checkable imagination: exact what-ifs, multi-step forecasts, a causal engine on declared models | learned-graph forecasts and actions within 0.1 × sd 0.222 → 1.000 (largest error 0.092; 0.778 within 0.05; "answer 0" 0.407 / 0.148); 12 further seeds 0.204 → 0.954; withheld 0.30 → 0.967 (largest error 0.135) | on (commands) | `genesis_wave1.json`, `genesis_wave1_wide.json`, `genesis_wave1_withheld.json` |
| One self-description record on every surface | 24 → 29 of 30 (original key; 30 of 30 under lane W's key); 4 surfaces → 1; withheld 12 → 15 of 15; two probes are plumbing; held-out states printed once in development | on (display) | `genesis_wave1.json`, `genesis_wave1_withheld.json` |
| Robustness of the new mechanisms | 75 of 75 fault cases pass; 44 of 75 failed without the 14 guards; voice follow-up 6 of 6 (6 of 6 failed without its 2 guards); rollback 26 of 26 | on (guards) | [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) §4–5 |
| Safety controls that are prerequisites, not gains | imagined-store safeguards 42 of 42 caught; quiet and admission safeguards 14 of 14; quiet honoured 1.56 to 4.40 s after the switch in 5 trials | on | `genesis_provenance.json`, `genesis_safety.json` ([WAVE0.md](WAVE0.md) §3) |
| Anticipation in a simulated plant | memory breaches avoided 0 → 0.796 (split), 0.802 ± 0.071 (12 seeds), 0.83 withheld; **cost** work given up 0.044 → 0.106, false alarms 0.009 → 0.043 | **off** | `genesis_wave1.json`, `genesis_wave1_withheld.json` |
| Visibility of arbitration and of the loop | arbitration recorded (14 per held-out world with the re-check on); self-description loop probes 1 → 4 of 4 (plumbing) | on (record, display) | `genesis_integration.json` |

Nothing lost: the offline research benchmark's digest at the shipped defaults is Wave 0's (`68731d7235cb…`);
`nsf acceptance --all` 7 of 7; the sentence guard differential 0 different; reproduction 8 of 8; files of schema 14,
16, 17 and 18 open and work at 19 ([REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) §6).

## 2. What did not improve

| Area | Result | Source |
|---|---|---|
| Integration of the four dimensions | knowledge correctness 0.713 → 0.710 (paired −0.003, sd 0.028; 4 seeds higher, 4 lower); no single link beats the baseline; +70 % wall time per pass | `genesis_integration.json`, ADR-056 |
| Knowledge over repeated cycles | each of six lane K mechanisms "no evidence" under a rule committed before the held-out run; the existing loop beats no loop (0.574 → 0.631, 0.497 → 0.571, 8 of 8 seeds) but `correct` falls 0.875 → 0.438 and 0.854 → 0.463 over 14 cycles | `genesis_learning_cycles.json`, [RECURSIVE_KNOWLEDGE_ASSESSMENT.md](RECURSIVE_KNOWLEDGE_ASSESSMENT.md) |
| Calibration of confidence | Brier 0.122 → 0.022 on 10 held-out claims, but a two-line rule on the verdict word scores 0.0025; no fitted map has 30 development items in 3 blocks, so no answer confidence is shown | `genesis_wave1.json`, `genesis_monitoring.json` |
| Selective answering | no held-out wrong answer to catch; turned on it would withhold 12 of 20 right held-out answers | `genesis_monitoring.json` |
| Imagined futures (scenario records) | three-way Brier 0.698–0.712 on held-out against 0.667 for "all equally likely" | `genesis_integration.json` |
| Curiosity by learning progress | timer path 0.139 → 0.126 bits per budget on 12 further held-out seeds; on the withheld world noise share 0.857 → 0.857 | `genesis_wave1_wide.json`, `genesis_wave1_withheld.json` |
| `information-v1` | held-out 0.092 → 0.105, but the withheld core world gave the same choices (0.043 → 0.043) | `genesis_wave1.json`, `genesis_wave1_withheld.json` |
| Salience attention | 0.713 alone, equal to the baseline | `genesis_integration.json` |
| Efficiency | no mechanism improved quality per unit of cost; adaptive depth reads fewer pages (581 → 559) at equal quality but not faster | [BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md) §8 |

## 3. What remains limited

- **Knowledge goes stale faster than the loop can refresh it** in the test worlds, mostly because a refresh that
  finds a newer version beside one lagging source is recorded as a contradiction for the owner and never revises the
  stale item (on development, 20 of 79 due refreshes ended contested; RECURSIVE_KNOWLEDGE_ASSESSMENT §5).
- **Nothing learns from real outcomes yet**: calibration, scenario records and strategy selection have no settled real
  predictions to learn from ([WAVE1.md](WAVE1.md) §9).
- **Strategy selection cannot be promoted**: its gate needs a withheld run that evaluated it, and no withheld world
  has strategy selection; it fails closed.
- **The causal engine declines front-door questions** (4 of 32 answerable per held-out seed), so the pooled figure is
  0.875, not 1.000.
- **Everything measured is synthetic**: fictional worlds through the real code, offline; the anticipation plant's
  constants are declared.
- **Open findings** from Wave 2: one adversarial case (`reflection_bounded_when_everything_asks`) failed on the
  integrated tree (a reflection section held 21 lines against its limit of 20; Wave 2's follow-up lane is fixing it,
  [WAVE2.md](WAVE2.md)); reflections are kept without a cap; the idle re-check prefers old unchanged items (7 of 72
  re-checks caught a change against 19 of 79 due refreshes, development); the individual cannot recognise its own
  text republished on the web; a scenario settles one timer pass after its prediction.

## 4. Unverified on real hardware, a live model, the open web

Each with the command an owner would run (from [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) §7):

| What | How to verify it |
|---|---|
| a live answer model: how it uses the context, its tool choice, its latency | `nsf llm status`; a console question; `scripts/bench_inference.py` |
| the predictive forecast on this host's memory | `nsf config --predictive-regulation`; `nsf internal-state` over a week |
| GPU utilisation, TensorRT-LLM | `nsf resources status`; `scripts/trtllm_compare.py` on the DGX Spark |
| real speech, microphones, speakers | `scripts/voice_real_speech_eval.py` with the voice services running |
| real phones, Pocket across devices | `nsf setup`, `nsf add-phone`; ask on one device, read on another |
| two real processes for the voice follow-up | two `nsf console` processes on one database, a spoken research question, one stopped |
| systemd timers and services; a real second account | `deploy/install.sh`; the two-account install in `deploy/` |
| the open web: real search, real outlets' lag and syndication, a real owner | not testable offline; every knowledge result here is from simulated worlds |
| the memory model services | `nsf setup`; `nsf web memory status` |

## 5. Decisions waiting for the owner

| Decision | Today | Evidence for | Evidence against | What would settle it |
|---|---|---|---|---|
| **Automatic supersession of a stale item** under a recency + independence rule (a newer version stated by at least two independent sources replaces the older one without asking) | contradictions are asked, never decided (ADR-026, ADR-038) | lane F names the contested refresh as the main limit of the loop (§3 above) | it changes a standing rule about what the individual believes; **no measurement exists of what it would gain or of its false replacements** | the owner's ruling; then new held-out cycle seeds committed before the change, and the learning-cycles experiment with and without it |
| `predictive_regulation` | off | simulated plant: 0.80 of memory breaches avoided held-out, 0.83 withheld | costs work given up (0.044 → 0.106) and false alarms (0.009 → 0.043); the plant's constants are declared | a week of `nsf internal-state` history on the host with it on |
| `idle_recheck` | off | Wave 1 information value 0.093 → 0.124 bits per budget (held-out, split) | Wave 2: knowledge correctness −0.019 / −0.004 (lane F), 0.707 against 0.713 (lane L); ~25 % more wall time | a re-check ranked by observed change rather than age (§6, direction 4) |
| `adaptive_depth` | off | equal quality, 581 → 559 pages | wall time not lower | measurement with the live model, where pages cost model time |
| `executive_rule information-v1` | `rules-v1` (opt-in) | held-out core 0.092 → 0.105 bits per budget; passes retention | the withheld core world gave the same choices | a further withheld world (one look) |
| `curiosity_learning_progress` | off | idle path 0.094 → 0.144 (12 seeds, 11 better) | timer path 0.139 → 0.126 (12 seeds); no gain on the withheld world | none planned: the rule was that neither path may lose |
| `salience_attention` | off | none (held-out equal to the baseline) | — | nothing to decide unless a measured gain appears |

Also carried from Wave 1: mixing simulated-environment and real readings in one epoch (needs a schema change), and
widening the timer's pool so stale knowledge items are reached (Wave 0, lane S).

## 6. The highest-value next research directions, ranked

Ranked by how directly a measurement here points at a limit, and how cheaply a fair test can be run.

| Rank | Direction | Why (the measurement behind it) | How to test it fairly |
|---|---|---|---|
| 1 | **Resolve contested refreshes** (with the owner's ruling on supersession) | the only knowledge result with an effect is the existing loop (+0.057 / +0.074 against no loop), and its main measured limit is that contested refreshes never revise (20 of 79 due refreshes, development) | new held-out cycle worlds and seeds committed before the change; the pre-registered verdict rule; count false replacements as well as gains |
| 2 | **Gather real settled outcomes**, with the owner's consent, before more imagination or calibration work | every learner of real outcomes is starved: no fitted map has 30 items in 3 blocks; scenario records lose to "equally likely"; strategy selection has nothing to promote | count settled real predictions over weeks of normal use; only then fit and test on a split written before the fit |
| 3 | **Measure the one instinct gain on the host** | predictive regulation's 0.80 is from a plant with declared constants; its costs are real | `nsf internal-state` history over a week with the forecast on, false holds counted against real memory |
| 4 | **Re-check what is likely to have changed, not what is oldest** | the idle re-check caught a change in 7 of 72 looks against 19 of 79 for the due order, and took budget from it (development) | rank by an observed change rate per subject; lane F's held-out cycles with new seeds |
| 5 | **A withheld world for the knowledge loop and strategy selection** | knowledge transfer is not measured at all; strategy selection's gate cannot pass without one | write the world and commit it alone before any further mechanism, then one look |
| 6 | **Front-door adjustment in the causal engine** | 4 of 32 answerable held-out questions per seed are declined; a well-defined, exactly checkable gap | the existing counterfactual family; but its worlds are synthetic, so the value to the owner is indirect |
| 7 | **Recognise the individual's own text republished on the web** | not staged: two independent echo pages would read as corroboration (lane F §6) | an adversarial case with its expectation committed first |

Not recommended on this evidence: more links between the mechanisms (no gain, +70 % time), routing a scenario's
error to curiosity or the re-check (assessed: no information beyond the real settlement, ADR-056 Decision 4), and
model fine-tuning or architecture self-modification (levels 4 and 5; owner authorisation required and not given).

## 7. Against the directive's success criteria

The table, criterion by criterion, is in [BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md) §10. In short: **measured gains**
for imagination (checkable questions) and self-modelling, and for anticipation **in a simulation only**; **measured
transfer** to withheld worlds for the two mechanisms that ship on; **no measured gain** for integration, knowledge,
learning, autonomy and efficiency; reliability **held** on every offline check. The directive's ultimate objective,
"measurable evidence of each advance", is met for the narrow advances above and not met for learning across cycles.
