# Final evaluation (deliverable J)

Status: 2026-11-26, updated for Wave 3 (`genesis-wave3`, merged at `f0a1f09`, record in [WAVE3.md](WAVE3.md)); the
Wave 2 account below is from `828ca8e`. Deliverable J of the
directive's §13: what improved, what did not, what remains limited, what is unverified, the decisions waiting for the
owner, and the research directions worth the most next. Every number is copied from the evidence file or document
named beside it. Nothing here claims or implies consciousness, sentience, awareness, general intelligence or
superintelligence; the mechanisms are functions with names that say what they do, and the measurements are task
scores on synthetic worlds. Wave 3's defaults and numbers are final after its three fix lanes (`gen3-fix`,
`gen3-fix2`, `gen3-fix3`; merged `0410a22`). Wave 4 (`genesis-wave4`, after its fix lane `3032cf6`; record in
[WAVE4.md](WAVE4.md)) is summarised first; its withheld look and final validation are pending.

## Wave 4 in plain words (read this first)

**What Wave 4 tried.** Wave 3 left one important problem open: NeuroSciFi cannot tell a story's independent reports
from copies of it, or from its own earlier answer coming back to it from the web. Wave 4 went after the cause rather
than the wording. It first built test worlds in which it is known which page copied which, containing every kind of
failure found so far, before any fix was written. On them, as shipped, NeuroSciFi takes as knowledge 127 facts whose
independent support is below its own bar (40 of them false) and leaves 142 of 612 well-supported true facts unheld
(held-out worlds, `genesis_sources_baseline.json`).

**What it found: no fix is good enough to turn on.**
- A rule that groups pages by which other page they name looked promising, but the only thing it reads turned out to
  be a habit of the program that wrote the test worlds (every outlet's name equals its web address), and one page
  could still merge honest sources into one. It also leaves more true facts unconfirmed (142 → 207 of 612). **Not
  supported; do not turn it on** (ADR-066).
- A rule that remembers what NeuroSciFi itself answered, and when, does recognise its own answers coming back (30 of
  31 such pages set aside; own answers taken as knowledge 4 → 1). Two reviews found it could let such an echo replace
  a fact NeuroSciFi had verified, and let a family device or an agent block a fact for you; the first is fixed, the
  second only in part (some of your own answers that merely mention a version are still recorded), and the last check
  found that its 30-day limit can hide a fresh answer behind an old one. It stays **off**: the counts are small, it
  sometimes sets aside honest pages (a value it first stated wrongly that later became true), and parts of it are
  untested (pages without a date) (ADR-067; WAVE4.md, "Reviews and fixes").
- Reserving part of the daily budget for subjects NeuroSciFi knows nothing about yet showed no gain and stays off
  (ADR-064). Tracing why its runs were noisy found two places where the product picked among equal records by a random
  id; now fixed. That weakens one figure of Wave 3's last test: the "run the idle worker first" gain on its second
  unseen world (+0.042) is not clearly larger than its spread (0.039).

**What Wave 4 changes for you:**
- A damaged saved setting can no longer loosen a limit you set: a damaged network limit turns web access off, and a
  damaged setting fixed when the individual was created pauses writes; each says the command that repairs it
  (`nsf web config --set NAME=VALUE`, `nsf config --repair-fixed NAME=VALUE`).
- Re-queuing a failed background task counts against your daily task limit; restoring a checkpoint holds the
  database far more briefly; background runs are now repeatable.
- Where an answer or a fact rests on a single web page, the console, `nsf web`, Pocket and the voice reply say "Only
  one source says this." It is shown only; it changes nothing.

**So the problem is still there at the shipped settings**, and the next step is worlds that contain what really tells
a copy from an honest report (WAVE4.md, "What stays open"). Nothing new needs your decision to stay safe; every new
rule is yours to turn on, and none is recommended.

## Wave 3 in plain words

**What Wave 3 found.** For the first time the knowledge loop was tried once on test worlds that nobody had seen
while building. There, two things that had always held did not: some versions that only NeuroSciFi's own answers had
stated, and some that only a wrong story copied across several sites stated, were taken as knowledge, and some
subjects it had right early on were wrong later (`docs/quality/COGNITIVE_BENCHMARK.md`, Wave 3 step 0). This is the
most important thing Wave 3 learned.

**What was tried, and why it is off.** Two rules were built to stop it: one counts a page that names the outlet it
repeats as that outlet, the other refuses pages that repeat NeuroSciFi's own answer. Two independent reviews showed
that they only recognise the exact wordings the test worlds were written with, that one of them can merge honest
sources and stop a true fact from ever being confirmed, and that the other can throw away honest pages and slow the
system down on a large individual. **Both are off** (the fix lane's decision; you can turn either on, and their defects found in review are fixed). So, today, **the problem is still there**: copies of a
story that are reworded can still count as separate confirmations, and pages repeating NeuroSciFi's own answer can
still count as proof, also when they say an AI wrote it (that check belongs to the echo rule, which is off). Reading the wording of a sentence cannot tell where a fact came
from; a sound rule would look at which page published a value first and which pages copied it, or ask you. That rule
is not built; it is the first next step (§6).
Also still there, older than Wave 3: **one web page that claims a story was first published by two different sites
joins those two sites into one source**, so a true fact they both report is not confirmed. A fix was built and is
**off**: the last check found it made some copies of one story count as several confirmations, which is the worse
mistake, and none of the test worlds contains such pages, so neither way could be measured
([ADR-063](../architecture/ARCHITECTURAL_DECISIONS.md); the setting `origin_grouping`, which you can turn on).

**What Wave 3 did change for you:**
- `nsf imagine scm` now answers a kind of causal question it used to decline, and says how it got the answer
  (checked exactly on 12 of 12 held-out questions, `genesis_frontdoor.json`); a last test on unseen questions is
  pending.
- Background research started by the worker when idle now stops at your daily task limit, as the timer's always did,
  and the limit is checked as each background task is queued, so two of them cannot both take the last place.
- Reading pages ahead now stays inside a task's byte budget; forgetting a source holds the database for a fraction of
  a second and also removes its sentence from open contradiction questions.
- The console overview and Pocket no longer rebuild their buttons when nothing changed, so the keyboard stays put.
- The commands for the last tests on unseen worlds refuse to start when the run would be wasted.
- Two aids that change no decision: predictive regulation "in shadow" (you switch it on) writes down what it would
  have done, without doing it, and `nsf readiness` (read-only) counts how many real outcomes each learning part still
  needs.

**What did not help (measured):** no order of the re-check of known facts did better; letting the idle worker look
up never-covered subjects reached more of them but still did not catch up with a simple round-robin schedule on the
same budget, because the budget, not the list of candidates, is what limits it; automatic replacement of an
out-of-date fact gained less than the normal spread and about one replacement in eight was wrong, some staying wrong
for over a week of simulated days until the test ended ([WAVE3.md](WAVE3.md) §3).

**What you can decide now, and with what evidence:** whether to gather a week of shadow evidence on your machine
(RUNBOOK, "Gathering evidence on your own host"; read its `settled.added` and `guide` first, not `settled.hits`); whether to
turn on automatic replacement (not recommended on this evidence); nothing else needs your decision to stay safe.

### What the final looks showed

**The withheld looks, taken once at the end of Wave 3** (clean tree `0bdc41a`; the rule for them committed first;
ledger lines 5 to 7; WAVE3.md, "The withheld looks"; evidence `genesis_wave3_look_*.json`). On three unseen worlds,
four seeds each, as shipped the share of subjects held correctly is 0.465 / 0.286 / 0.323, exactly as at step 0
(never refreshed 0.455 / 0.385 / 0.395; a blind round-robin with the same allowance 0.636 / 0.541 / 0.537), and the
hole is open in the same counts: 16 versions only the individual itself had asserted and 31 only the wrong story
stated became knowledge. **`echo_recognition` is supported on those worlds** by the rule written beforehand (the 16
own-output versions → 0; wrongly admitted content 48 → 38, lower in every world; correctness +0.017 / +0.013 /
+0.007; no more subjects lost), though wrong-story versions rose 31 → 37; it stays off and is the owner's to turn
on. **`attribution_independence` was not exercised** (every figure identical). **Supersession** gains +0.011 /
+0.042 / +0.007 (every seed) and made 3 false replacements of 54 by the rule; all 5 false replacements in its runs
were still wrong when the runs ended (up to 17 cycles); it stays off. **The re-check's order gives no gain.** **The
idle investigator run before the timer's pass** gives +0.013 / +0.042 / +0.052 (beyond the spread in two worlds)
with fewer subjects lost; the look cannot promote it, and dividing the daily allowance between refreshing and reaching
is the next thing to measure. *Correction, 2026-11-26 (Wave 4 lane A).* The idle-first figures of the look (+0.013 /
+0.042 / +0.052) are single runs of code whose idle runs were not repeatable (a pick among equal records followed
random task ids; fixed in Wave 4, ADR-064). On public worlds a world's mean gain moved by up to 0.015 between repeats,
so "beyond the spread" holds for W3 (margin 0.017) but is not established for W2 (margin 0.003); the held-out figures
of ADR-062 are exactly repeatable. (Note, Wave 4 record: the second measurement pass reads "holds for W3" as stronger
than the evidence: W3's margin exceeds the largest swing measured by 0.002, on only four public worlds that varied;
not shown to fail, not verified on those worlds; WAVE4.md §1.4.) **The front door stays on** (12 of 12 front-door questions exact; every other
figure equal to the run without it).

## One page, in plain words (Wave 2)

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

- **Making the parts work together gave no gain beyond the normal spread** and cost about 73 % more time per pass
  (lane L, re-measured by lane Y).
- **No knowledge mechanism measurably helped knowledge stay correct over repeated days.** The existing loop beats
  having no loop, but knowledge still goes out of date in every configuration tried. Part of that is how the test
  world was built (its facts change more often than the daily budget can check, and some subjects are never looked
  up by your own research), and part is the product's: a simple "check each subject in turn" schedule with the same
  budget keeps more; the loop gets about half of what that schedule gets, or about six tenths once the subjects your
  research never covers are allowed for (lane F; the Wave 2 reviews).
- **The system's own confidence numbers are not better than a two-line rule**, and there is not enough real data to
  show any confidence number at all.
- **Imagined futures for its predictions were not better than guessing "all equally likely"** on held-out worlds.
- **Two independent reviews found two real defects** in Wave 2's own additions: a record of "conflicts" between
  background mechanisms that never happened, and a voice answer that could be delivered after you had told the system
  to forget the question. Both are fixed, and a third check of the fixes found six smaller issues, also fixed (§7).

**What is still unknown.** Nothing was tested on the open web, with the live answer model, on the DGX Spark's real
memory and GPU, with real phones or real speech.

**What is waiting for you (the owner):** seven switches or rules, listed in §5 with the evidence for each. The one with
the most potential is a rule to replace an out-of-date fact automatically when newer, independent sources agree;
today such conflicts are always asked of you and never decided, and that is what most limits how current the
knowledge stays. (Wave 3 built that rule, off; measured, it does not help enough to recommend it: see above.)

## 1. What improved, with evidence

| Improvement | Evidence (held-out unless said) | Ships | Source |
|---|---|---|---|
| Checkable imagination: exact what-ifs, multi-step forecasts, a causal engine on declared models | learned-graph forecasts and actions within 0.1 × sd 0.222 → 1.000 (largest error 0.092; 0.778 within 0.05; "answer 0" 0.407 / 0.148); 12 further seeds 0.204 → 0.954; withheld 0.30 → 0.967 (largest error 0.135) | on (commands) | `genesis_wave1.json`, `genesis_wave1_wide.json`, `genesis_wave1_withheld.json` |
| One self-description record on every surface | 24 → 29 of 30 (original key; 30 of 30 under lane W's key); 4 surfaces → 1; withheld 12 → 15 of 15; two probes are plumbing; held-out states printed once in development | on (display) | `genesis_wave1.json`, `genesis_wave1_withheld.json` |
| Robustness of the new mechanisms | lane U: 75 of 75 fault cases pass; 44 of 75 failed without the 14 guards; voice follow-up 6 of 6 (6 of 6 failed without its 2 guards); rollback 26 of 26. On the combined tree, with lane L's additions and the fixes: 87 of 87 fault cases and 29 of 29 with a failure message naming a subject, voice follow-up 19 of 19, rollback 28 of 28 | on (guards) | [REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) §4–5, §9 |
| Safety controls that are prerequisites, not gains | imagined-store safeguards 42 of 42 caught; quiet and admission safeguards 14 of 14; quiet honoured 1.56 to 4.40 s after the switch in 5 trials | on | `genesis_provenance.json`, `genesis_safety.json` ([WAVE0.md](WAVE0.md) §3) |
| Anticipation in a simulated plant | memory breaches avoided 0 → 0.796 (split), 0.802 ± 0.071 (12 seeds), 0.83 withheld; **cost** work given up 0.044 → 0.106, false alarms 0.009 → 0.043 | **off** | `genesis_wave1.json`, `genesis_wave1_withheld.json` |
| Visibility of the loop | self-description loop probes 1 → 4 of 4 (plumbing). The arbitration record, also on, is **not** an improvement: every arbitration the benchmark first recorded was false (a defect, fixed by lane Y: the record is written only for a real conflict; re-measured 0 in every configuration) | on (display) | `genesis_integration.json`; Wave 2 measurement review H1 |

Nothing lost: the offline research benchmark's digest at the shipped defaults is Wave 0's (`68731d7235cb…`);
`nsf acceptance --all` 7 of 7; the sentence guard differential 0 different; reproduction 8 of 8; files of schema 14,
16, 17 and 18 open and work at 19 ([REGRESSION_VERIFICATION.md](REGRESSION_VERIFICATION.md) §6).

## 2. What did not improve

| Area | Result | Source |
|---|---|---|
| Integration of the four dimensions | knowledge correctness 0.718 → 0.726 on held-out (paired +0.008, sd 0.015; the re-check's; below the baseline's per-world sd 0.047 / 0.071), measured with separate random streams for the world and the outcomes after the review; most links not exercised by the worlds; about +73 % wall time per pass | `genesis_integration.json`, ADR-056, the Wave 2 measurement review |
| Knowledge over repeated cycles | the idle re-check, the one mechanism that could move the metric: no evidence of gain; the other five lane K mechanisms not exercised by the world; a gain below 0.105 / 0.057 could not have been seen. The existing timer loop beats no loop (0.574 → 0.631, 0.497 → 0.571, 8 of 8 seeds), but `correct` falls 0.875 → 0.438 and 0.854 → 0.463 over 14 cycles where the truth changes 1.75 and 3.3 times per cycle against a budget of 2 and 3 tasks; a same-budget blind schedule reading each look's majority holds 0.786 and 0.708, an oracle 0.957 and 0.928 (the harness's references from the world generator alone, `references`); the loop recovers about half of the blind schedule's gain (0.49 and 0.55; about 0.57 and 0.66 allowing for the 14 and 17 % of subjects the owner's research never covers, the check of the fixes' reading) | `genesis_learning_cycles.json`, [RECURSIVE_KNOWLEDGE_ASSESSMENT.md](RECURSIVE_KNOWLEDGE_ASSESSMENT.md), the Wave 2 measurement review |
| Calibration of confidence | Brier 0.122 → 0.022 on 10 held-out claims, but a two-line rule on the verdict word scores 0.0025; no fitted map has 30 development items in 3 blocks, so no answer confidence is shown | `genesis_wave1.json`, `genesis_monitoring.json` |
| Selective answering | no held-out wrong answer to catch; turned on it would withhold 12 of 20 right held-out answers | `genesis_monitoring.json` |
| Imagined futures (scenario records) | not better than "all equally likely" (0.667) on held-out: 0.689 integrated (per seed 0.574–0.824), 0.681 alone (per seed 0.555–0.801); a little better on development (0.630 / 0.640) | `genesis_integration.json` |
| Curiosity by learning progress | timer path 0.139 → 0.126 bits per budget on 12 further held-out seeds; on the withheld world noise share 0.857 → 0.857 | `genesis_wave1_wide.json`, `genesis_wave1_withheld.json` |
| `information-v1` | held-out 0.092 → 0.105, but the withheld core world gave the same choices (0.043 → 0.043) | `genesis_wave1.json`, `genesis_wave1_withheld.json` |
| Salience attention | not exercised: the timer's curiosity pool never held more than one subject; on the idle path, 14 admissions per run had two or more candidates and salience never took another first candidate than the priority order | `genesis_integration.json`; measurement review M1 |
| Efficiency | no mechanism improved quality per unit of cost; adaptive depth reads fewer pages (581 → 559) at equal quality but not faster | [BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md) §8 |
| Wave 3: the re-check's order | every one of 14 orders "no evidence" on held-out; paired gains −0.031 to +0.022 against baseline sds 0.057–0.105 | `genesis_recheck_order.json`, ADR-060, WAVE3.md §3.3 |
| Wave 3: the idle investigation (coverage) | timer first: "no evidence"; allowance first: "revise" (beyond the sd in one world of five), and still below the blind round-robin in every world (gap 0.065–0.120) | `genesis_idle_coverage.json`, ADR-062, WAVE3.md §3.4 |
| Wave 3: the two source rules | "unsafe" under their own pre-registered rule; they read only the phrasings of their worlds; staged honest credits cost 0.07–0.13 `correct` (0.04–0.08 after the fix lane's H1 fix, 30 % credits, new seeds); ship off | `genesis_wave3_s.json`, ADR-061, the Wave 3 reviews, WAVE3.md §3.1 |
| Wave 3: supersession | gain below the spread in every world; 3 of 23 rule replacements false (pooled, not a claim); a false one wrong for 8 and at least 10 cycles (censored), re-measured; development evidence | `genesis_wave3_fix_s.json`, WAVE3.md §3.2 |
| Wave 4: one source by derivation (`source_derivation`) | "trade" under its own rule on both splits; held-out, pooled: wrongly admitted 127 → 99, suppressed 142 → 207 of 612, `correct` −0.064 / −0.054 / −0.101 (after the fix lane); its only signal is the worlds' generator's naming convention; "not supported; do not turn on" | `genesis_wave4_s.json`, `genesis_wave4_fix.json`, ADR-066, WAVE4.md §1.1 |
| Wave 4: own-text provenance (`own_text_provenance`) | "effective" under its own rule, but small: held-out own answers admitted 4 → 1, wrongly admitted 127 → 123, suppressed 142 → 145 of 612, honest pages set aside 14 of 2,670; `correct` inside the spread; off | `genesis_wave4_e.json`, `genesis_wave4_fix.json`, ADR-067, WAVE4.md §1.2 |
| Wave 4: dividing the allowance (`allowance_division`) | no candidate's gain beyond the spread in any held-out world (−0.024 to +0.026); every candidate "unsafe" or "harm"; 0.09–0.26 below the blind round-robin | `genesis_wave4_a_held_out.json`, ADR-064, WAVE4.md §1.4 |
| Wave 4: the caution | display only; only the one-source trigger lines up (development: 3 of 3 wrong answers carried it); held-out not exercised; the other two triggers mislead about three times in ten and are not shown by default | `genesis_wave4_d_caution.json`, ADR-065, WAVE4.md §1.5 |

## 3. What remains limited

- **Knowledge goes stale faster than the loop can refresh it** in the test worlds. Part of this is the worlds'
  design (the truth changes about as often as the budget allows looks); the product's part is that a refresh that
  finds a newer version beside one lagging source is recorded as a contradiction for the owner and never revises the
  stale item (on development, 20 of 79 due refreshes ended contested; RECURSIVE_KNOWLEDGE_ASSESSMENT §5), and the due
  order; the loop recovers about half of what a same-budget blind schedule recovers (0.49 and 0.55; about 0.57 and
  0.66 allowing for the subjects the owner's research never covers, which no loop can refresh). The experiment ran
  the timer's
  pass only, not the worker's idle investigation.
- **Nothing learns from real outcomes yet**: calibration, scenario records and strategy selection have no settled real
  predictions to learn from ([WAVE1.md](WAVE1.md) §9).
- **Strategy selection cannot be promoted**: its gate needs a withheld run that evaluated it, and no withheld world
  has strategy selection; it fails closed.
- **Front-door questions** (4 of 32 answerable per held-out seed) were declined until Wave 3; lane C's front-door
  adjustment answers them (the group 0 → 1.000, 12 of 12; pooled 0.875 → 1.000, `genesis_frontdoor.json`, ADR-059),
  on exact or stated moments only; its withheld look, taken at the end of Wave 3, kept it on (12 of 12 front-door questions answered exactly; every other figure equal to the run without it; WAVE3.md, "The withheld looks").
- **Source independence is decided by wording, not by origin** (Wave 3): the hole the withheld learning-cycle look
  found is open at the shipped defaults ([WAVE3.md](WAVE3.md)). So is an older defect of the same rule: one page
  naming two honest outlets as its origins joins them into one source and their true fact is not verified (review3b
  N1). The one-way rule that closes it on staged pages is built and off (`origin_grouping`, ADR-063): as first shipped
  it made copies naming several origins count as several sources (review3c F1); a page on a host named like an
  outlet can still join two honest outlets (F2), and "Originally published by X" at the start of a sentence is not
  read (F3). No benchmark world holds a page naming several origins. Wave 4 built worlds whose truth is a
  derivation graph and two rules that do not read wording (ADR-066, ADR-067); neither is supported well enough to turn
  on, so the hole is open after Wave 4 too ([WAVE4.md](WAVE4.md)).
- **Everything measured is synthetic**: fictional worlds through the real code, offline; the anticipation plant's
  constants are declared.
- **Open findings** from Wave 2 (the review findings are all fixed, WAVE2.md §4): reflections are kept without a cap
  (bounded in size; no retention bound, since a reflection records the actions taken and forgetting is the owner's); the idle re-check prefers old unchanged items (7 of 72
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
| **Automatic supersession of a stale item** (`supersession`, built in Wave 3, off: a newer version stated by at least two independent sources replaces the older one without asking) | `"ask"`: contradictions are asked, never decided (ADR-026, ADR-038) | it asks fewer questions and catches stale items sooner (lane S) | gain below the spread in every world; 3 of 23 rule replacements false on lane S's held-out; of the 5 false replacements in those runs (3 by the rule, 2 by the path that existed before), 4 still wrong when the run ended, the longest at least 10 cycles (`genesis_wave3_fix_s.json`); development evidence only; forget and revert, and family or agent tasks triggering it, fixed by the fix lane; two colluding pages can still replace a true item | its withheld look (supersession on the shipped defaults, both source rules off: not the configuration measured here; reported, cannot switch it on); a sound source-independence rule first (§6, rank 1) |
| `origin_grouping` (built in Wave 3's second fix lane, off since the third: a page naming its origin attaches to one origin only) | `"transitive"`: the grouping of `3ce4ae3` (for a page's named origins, as before Wave 3); a page naming two honest outlets as its origins joins them (review3b N1, open) | closes N1 on staged pages; with the reviewer's edge choice, copies naming several origins count once on every staged case | as first shipped it counted such copies as 2–3 sources (review3c F1); F2 open for names other than the news wires'; no benchmark world holds a page naming several origins, so neither direction is measured (ADR-063) | worlds with multi-origin copies and pages naming two honest outlets, written by someone other than the rule's author and committed first (§6, rank 1) |
| the two source rules (`attribution_independence`, `echo_recognition`) | off (fix lane) | none that survived review | read only their worlds' phrasings; merge honest outlets; discard honest pages (WAVE3.md §3.1) | a different kind of rule (§6, rank 1) |
| `source_derivation` (Wave 4) | off | fewer badly supported rows (held-out 127 → 99) | its signal is the generator's convention; more true facts not held (142 → 207 of 612); one page can merge honest outlets; **not recommended** (ADR-066) | worlds that hold what separates a copy from an honest credit (§6, rank 1) |
| `own_text_provenance` (Wave 4) | off | own answers admitted 4 → 1 (held-out); "effective" under its rule (at zero margin in one world) | a measured false rejection (honest pages 14 of 2,670); undated pages unmeasured; mentions and some denials still recorded (9 of 18 phrasings, the second security pass); its 30-day expiry hides fresh emissions (ADR-067, WAVE4.md) | the expiry fixed; its withheld look; undated pages in the worlds |
| `allowance_division` (Wave 4) | `"race"` | none on held-out | "unsafe" / "harm"; no gain beyond the spread (ADR-064) | none planned: reaching runs out of candidates |
| `caution_detail` (Wave 4, display) | off | says why pages count together and flags own answers | wrong about three times in ten on the worlds measured (ADR-065) | a measurement where the triggers line up |
| `predictive_regulation` | off; `"shadow"` gathers evidence without acting (Wave 3) | simulated plant: 0.80 of memory breaches avoided held-out, 0.83 withheld | costs work given up (0.044 → 0.106) and false alarms (0.009 → 0.043); the plant's constants are declared; the shadow report counts what the forecast adds separately since the fix lane (staged held-out: 198 added false alarms against 78 added hits) | a week of shadow on the host: `nsf config --predictive-regulation-shadow`, then `nsf internal-state --shadow-report` |
| `recheck_order` | `"staleness"` | none on held-out | every order "no evidence" (ADR-060) | none planned |
| `idle_recheck` | off | Wave 1 information value 0.093 → 0.124 bits per budget (held-out, split) | Wave 2: knowledge correctness −0.019 / −0.004 (lane F), 0.726 against 0.718 (lane L, paired +0.008, below the spread); ~25 % more wall time | none planned (lane R: no order helps, ADR-060) |
| `adaptive_depth` | off | equal quality, 581 → 559 pages | wall time not lower | measurement with the live model, where pages cost model time |
| `executive_rule information-v1` | `rules-v1` (opt-in) | held-out core 0.092 → 0.105 bits per budget; passes retention | the withheld core world gave the same choices | a further withheld world (one look) |
| `curiosity_learning_progress` | off | idle path 0.094 → 0.144 (12 seeds, 11 better) | timer path 0.139 → 0.126 (12 seeds); no gain on the withheld world | none planned: the rule was that neither path may lose |
| `salience_attention` | off | none | not exercised: the timer's curiosity pool never held more than one subject, and where the idle path had several candidates salience never changed the first | a world where the candidates' salience and priority orders disagree |

Also carried from Wave 1: mixing simulated-environment and real readings in one epoch (needs a schema change), and
widening the timer's pool so stale knowledge items are reached (Wave 0, lane S).

## 6. The highest-value next research directions, ranked

Ranked by how directly a measurement here points at a limit, and how cheaply a fair test can be run.

| Rank | Direction | Why (the measurement behind it) | How to test it fairly |
|---|---|---|---|
Updated after Wave 3 ([WAVE3.md](WAVE3.md)). Done in Wave 3, so off this list: front-door adjustment (lane C), a
withheld world for the knowledge loop (step 0), re-checking by observed change (lane R: no gain).
**After Wave 4** ([WAVE4.md](WAVE4.md)): rank 1 was tried (worlds with a derivation graph, written first; a rule by
derivation; a rule by what the individual itself emitted) and is not done: the derivation rule's signal was the
generator's convention, and the own-text rule stays off. What it needs now are worlds that hold what really separates
a copy from an honest report, drawn independently of any rule's author: copy lags, an honest outlet's own specifics,
errors copies inherit, links, honest outlets crediting a wrong first reporter, names that differ from hosts, undated
pages (review4 measurement, Q2). Rank 2 was tried (lane A, ADR-064): no division of the allowance gains; reaching runs
out of admitted candidates, which a share cannot add.

| Rank | Direction | Why (the measurement behind it) | How to test it fairly |
|---|---|---|---|
| 1 | **A sound rule for source independence and for the individual's own text** | the withheld look (step 0) admitted own-text and copied-story versions and raised a confidence on one source, where 120 held-out runs never had; lane S's phrase-parsing rules did not close it: they read only the five credit forms and two model credits their worlds were written with (19 of 27 common credits missed; echoes that credit no model pass at 67–100 %), and merged honest outlets that credit each other (review S-1 to S-4, security H1, M2–M5). What Wave 3 learned: the wording of a sentence does not say where its value came from. Three known failure shapes of today's rule, each staged in a test: reworded copies that name no origin count as independent; a page naming two honest outlets as its origins joins them (review3b N1, open at the defaults); copies naming several origins count as several sources under a one-way rule (review3c F1, `origin_grouping`, off; ADR-063) | decide dependence by derivation, not wording: which page stated a value first (publication order, the individual's own answers by time) and which pages share its text or value afterwards; or ask the owner. New worlds written by someone other than the rule's author, with honest outlets that credit each other, copies in many phrasings, pages naming two honest outlets as their origins, copies carrying a second origin line ("(AP)" plus "first appeared in X") or naming each other, and a host named like a wire or an outlet, committed first; false rejections counted as carefully as false admissions; then one withheld look |
| 2 | **A share of the allowance for never-covered subjects** | coverage is limited by the allowance, not by candidates: with the timer first the idle path gets 0.25–5.75 looks per run against tens of admitted, unreached subject-cycles; taking the allowance first removes 51–83 % of the uncovered part but displaces due refreshes (ADR-062) | choose the share on development; test on worlds no lane has seen |
| 3 | **Gather real settled outcomes**, with the owner's consent | every learner of real outcomes is starved; `nsf readiness` now counts how far each is from its minimum (ADR-058) | weeks of normal use; fit and test only on a split written before the fit |
| 4 | **Measure the one instinct gain on the host** | predictive regulation's 0.80 is from a plant with declared constants | a week of shadow (ADR-058), read by `settled.added` and the `guide` committed before any host data |
| 5 | **Resolve contested refreshes** (the owner's ruling on supersession) | "found, not revised" is 20 % of the loop's shortfall (50 of 70 such looks contested, development); supersession as built gains less than the spread with false replacements | only after rank 1: a replacement rule is only as good as its count of independent sources |
| 6 | **Strategy selection's withheld world** | its gate still cannot pass: no withheld world has strategy selection (step 0, stretch not done) | a harness change in the main session's `final` command, proposed in step 0 |

Not recommended on this evidence: more links between the mechanisms (no gain beyond the spread, +73 % time), routing a scenario's
error to curiosity or the re-check (assessed: no information beyond the real settlement, ADR-056 Decision 4), and
model fine-tuning or architecture self-modification (levels 4 and 5; owner authorisation required and not given).

## 7. The independent reviews of Wave 2

Two independent reviews read the integrated Wave 2 tree (`828ca8e`), read-only, without touching the withheld
family; a third check read the fix lanes' work (review2b). Their findings are corrected in this document and in
[BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md); the fixes themselves, each with its test, are recorded in
[WAVE2.md](WAVE2.md) §4 ("Reviews and fixes"). Every finding of the three is fixed.

| Review | Findings | What they were about |
|---|---|---|
| measurement (lanes L, F, U) | 4 high, 5 medium, 7 low | evidence and wording: lane L's paired comparisons were not on identical worlds; its "integrated" retention and adversarial runs were partly vacuous; mechanisms that never acted were reported as "no evidence"; lane F's decay was given without references; the verdict rules cannot see gains smaller than the baseline's spread; lane-branch commit hashes and pre-integration case counts. One product defect: **the arbitration record recorded conflicts that did not happen** (every one the benchmark counted), fixed by lane Y |
| security (the Wave 2 code) | 1 medium, 6 low | one product defect: **when the voice follow-up's delivery claim fails, the answer was delivered anyway, also after the owner forgot the task**, and two processes could deliver it twice; low: subject text in the process log on a failure, a lenient fallback for a failed learning-progress read, a fallback decision stored without saying so, uncounted re-check submissions after a failure, a test pinning the broken reflection bound, a follower slot that can leak. All fixed by lane X |
| the check of the fixes (lanes X and Y) | 6 low, 3 informational | the voice follow-up's retry could lose an answer when the person moved to another process, and said nothing meanwhile; the queue-mode budget (older than Wave 2); log lines too thin to diagnose; two wordings. All fixed in the reconciliation |

All three negative headlines survived both reviews: no gain from integration; no knowledge-evolution mechanism with
a held-out gain, while the existing loop beats no loop; 9 of the 10 adversarial cases added in Wave 2 passed as
written (the tenth found a gap, fixed: 28 of 28 pass on the combined tree). The figures here are the re-measured
ones.

## 8. Against the directive's success criteria

The table, criterion by criterion, is in [BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md) §10. In short: **measured gains**
for imagination (checkable questions) and self-modelling, and for anticipation **in a simulation only**; **measured
transfer** to withheld worlds for the two mechanisms that ship on; **no measured gain** for integration, knowledge,
learning, autonomy and efficiency; reliability **held** on every offline check. The directive's ultimate objective,
"measurable evidence of each advance", is met for the narrow advances above and not met for learning across cycles.
