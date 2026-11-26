# Wave 3: what counts as a source, the re-check's order, coverage, the front door, evidence from the host

Status: 2026-11-26. Wave 3 of Project Cognitive Genesis. Step 0 (`gen3-0`) took one look at new withheld
learning-cycle worlds with the unchanged system; six lanes (P, C, H, R, S, V) were then built in parallel and merged
on `genesis-wave3` (`f0a1f09`). Two independent reviews read the merged tree (measurement, security); a fix lane
(`gen3-fix`) acted on their findings, and the reconciliation lane (`gen3-rec`) wrote the record. The fix lane's
final defaults and numbers are written in (its evidence: `genesis_wave3_fix_*.json`); a second and a third fix lane
(`gen3-fix2`, `gen3-fix3`) acted on the checks that followed (review3b, review3c; merged `2c48865`, `0410a22`).
Sources: ADR-058 to ADR-063 in [ARCHITECTURAL_DECISIONS.md](../architecture/ARCHITECTURAL_DECISIONS.md), the evidence
files named beside each number, the lanes' reports and the three review rounds ("Reviews and fixes", below). Nothing
here is a claim of consciousness, awareness or general intelligence; the names describe functions.

## On one screen, in plain words

**The hole the withheld look found is still open.** At Wave 3's step 0 the unchanged system was run once on worlds no
lane had seen. Two protections that held in all 120 earlier held-out runs did not hold there: versions that only the
system itself had stated, and versions that only a wrong, copied story stated, became knowledge; once a confidence
rose on a single source; and some subjects held correctly early were later wrong
(`docs/quality/COGNITIVE_BENCHMARK.md`, "The unchanged system's one look"). Lane S built two rules against this. The
reviews showed that they do not close the hole: they read the exact phrasings lane S's own test worlds were written
with, and miss others; one of them merges honest outlets that credit each other; the other throws away honest pages on
a large individual and treats ordinary sentences about "the AI Act" as model text. **Both rules ship OFF** (fix lane, decisions D1 and D2; the settings stay, the defects the security review found in
them are fixed, and the owner may turn either on).
At Wave 3's shipped defaults the individual treats sources exactly as it did at step 0: a reworded copy of a story can
still count as an independent source, and a page repeating the individual's own answer can still read as
corroboration, whether or not it credits a model (that check is part of `echo_recognition`, off). Reading the wording of a sentence does not decide where a fact came from; what would is a
different kind of rule (which page published a value first and which pages derive from it, or the owner's
confirmation). It is not built. It is the first of the next directions (FINAL_EVALUATION.md §6).
**Also open at the shipped defaults, older than Wave 3:** one page that says a story was first published by one
honest outlet and originally appeared in another joins the two into one source, so a true fact they both report is
not confirmed (review3b N1). A rule that attaches such a page to only one of them was built by the second fix lane
and is **off**: the last check found that it made some copies of one story count as several sources where they had
counted as one (review3c F1), the more dangerous error, and no test world holds such pages, so neither way is
measured. It is the setting `origin_grouping` (default `"transitive"`, the grouping as before; ADR-063).

**What Wave 3 changes at the shipped defaults:**
- `nsf imagine scm` answers causal questions that only front-door adjustment can answer, saying so, where before it
  declined them (lane C; on; its withheld look, taken at the end of Wave 3, kept it on (12 of 12 front-door questions answered exactly; every other figure equal to the run without it; WAVE3.md, "The withheld looks"));
- the worker's idle investigation now stops at the day's task count, as the timer always did (lane V; a budget fix);
  the count is checked again inside the submission's write transaction, so two deciders cannot both take the last
  place (security L6), and the knowledge re-check, when the owner turns it on, checks it inside its own (review3b N5);
- a fetch read ahead that was abandoned is granted no more bytes, so read-ahead stays inside the task's byte budget
  (§3.9);
- a forget marks the answer sentences that stated a forgotten value as sourced (security M3) with its scan made before
  the write lock, which it holds for a fraction of a second (the first fix lane's version held it 7–40 s on a large
  individual, review3b N2), and it removes the forgotten page's sentence from contradiction questions (N6);
- the console overview and Pocket's lists are redrawn only when their content changed, so the keyboard stays where it
  was (lane P);
- the withheld looks' commands refuse, before their ledger line, what would spend a look for nothing (a dirty tree, an
  output that cannot be written, a misspelt mechanism, a blank note, a changed worlds file);
- two things the owner can use and that change nothing by themselves: predictive regulation "in shadow" (opt-in),
  which only writes down what the forecast would have done, and `nsf readiness` (read-only), which counts real
  outcomes (lane H);
- built and off, each the owner's opt-in: another order for the knowledge re-check (`recheck_order`, lane R),
  automatic replacement of an out-of-date fact (`supersession`, lane S), the two source rules
  (`attribution_independence`, `echo_recognition`, lane S), off since the fix lane, and the one-way origin rule
  (`origin_grouping`, off since the third fix lane; ADR-063).

**Negative results, as plainly as the rest:**
- No order of the knowledge re-check helps: every one of 14 orders gets "no evidence" on held-out; the order is not
  what limits the loop (lane R, ADR-060).
- The worker's idle investigation does not close the gap to a blind schedule within the same allowance. Given the
  allowance first it reaches more subjects, but it stays below the blind schedule in every world (lane V, ADR-062).
- Supersession gains less than the spread, and on lane S's held-out worlds 3 of its 23 replacements (about one in
  eight) were wrong; a wrong one stayed wrong for 8 cycles in one world and at least 10 in another, still wrong when
  the run ended (review S-5; re-measured by the fix lane with the censoring recorded, `genesis_wave3_fix_s.json`).
- Lane S's held-out numbers are development evidence: its echo rule was changed twice after held-out runs.

## Why Wave 3 exists

The directive's last order is "repeat only where evidence supports further progress". Wave 2 measured that linking
the mechanisms gives no gain and that knowledge decays over repeated cycles, with a blind schedule of the same budget
doing better than the product's loop ([WAVE2.md](WAVE2.md)). Wave 3 went only where a measurement pointed at a
specific limit: the loop's shortfall (lanes R and V), what counts as a source (lane S), the gap the causal engine
declined (lane C), the missing real outcomes and the untested forecast on the host (lane H), and two things CI showed
(lane P). A measured "no gain" is a valid result. Nothing ships on without a held-out gain beyond the baseline's
spread, an ablation that removes it, retention intact and no adversarial regression; lane S's two rules were turned
on against this and are off since the fix lane (§3.1).

## Step 0: the one look at the unchanged system, safety counts first

Three new withheld worlds for the learning cycles, four seeds each (7341-7344), were committed alone (`26409bc`),
then their guard (`d0b6b02`), then one look on `d0b6b02` (ledger line 4 of `genesis_final_uses.jsonl`). Every number
here is from `docs/quality/COGNITIVE_BENCHMARK.md`, "The unchanged system's one look (Wave 3 step 0)".

| baseline (today's loop), summed over 4 seeds | W1 | W2 | W3 |
|---|---|---|---|
| versions only the individual's own outputs stated, admitted as knowledge | 4 | 6 | 6 |
| versions only a wrong story stated, admitted as knowledge | 2 | 17 | 12 |
| a confidence raised after a look with one origin | 0 | 0 | 1 |
| retention: subjects lost / regressed (of the base) | 0 / 1 (16) | 0 / 3 (9) | 0 / 3 (15) |

On the 120 held-out runs before, each of these counts was 0. This is Wave 3's most important finding.

| `correct`, mean ± sd over 4 seeds | W1 | W2 | W3 |
|---|---|---|---|
| no loop | 0.423 ± 0.051 | 0.311 ± 0.061 | 0.311 ± 0.065 |
| baseline (today's loop) | 0.465 ± 0.064 | 0.286 ± 0.039 | 0.323 ± 0.035 |
| baseline − no loop, paired (seeds better) | +0.042 (4 of 4) | −0.025 (1 of 4) | +0.012 (3 of 4) |
| reference: perfect snapshot / blind round-robin, majority read / oracle | 0.455 / 0.636 / 0.797 | 0.385 / 0.541 / 0.809 | 0.395 / 0.537 / 0.820 |

The loop does not reliably beat no loop on unseen worlds, and it sits 0.17 to 0.26 below the blind round-robin of the
same allowance. The smallest gain the pre-registered rule can see there is the baseline's sd: 0.064, 0.039, 0.035.
The shipped defaults of step 0's tree were identical to the baseline on every metric and seed.

## 1. What each lane built

| Lane | Built | Default | ADR | Evidence |
|---|---|---|---|---|
| 0 (step 0) | three withheld learning-cycle worlds, their guard (`cycles.final()` only, ledger line before scoring, pinned digest), the unchanged system's look | no mechanism | — | `genesis_cycles_withheld_baseline.json` (only the main session reads it) |
| P | why the offline digest differs between hosts (`--deterministic-out`, `portable_sha256`, `scripts/diff_deterministic.py`, a CI artifact step); the console overview's panels and Pocket's lists redrawn only when changed, focus kept | on (a fix) | — | `offline_deterministic_aarch64.json`, `tests/test_offline_digest_hosts.py`, `tests/e2e/test_focus_kept.py` |
| C | front-door adjustment in the linear causal engine; a stated origin of the moments (`population` or `samples: n`), an interval from the sample size, a usefulness bound | on in `nsf imagine scm` (`--no-front-door`) | ADR-059 | `genesis_frontdoor.json` |
| H | `predictive_regulation: "shadow"` (verdicts recorded, the gate decides as off), `nsf internal-state --shadow-report`, `nsf readiness` and its owner-scope field in the self-description | shadow: opt-in; readiness: read-only | ADR-058 | `genesis_h.json` |
| R | `recheck_order`: the re-check's pool ordered by observed change rate, a newer version seen, and rotation; the decomposition of the loop's shortfall | `"staleness"` (unchanged) | ADR-060 | `genesis_recheck_order.json` |
| S | `attribution_independence` (a sentence crediting an outlet counts with it), `echo_recognition` (a page repeating the individual's own text is no evidence for it), `supersession` with `supersession_min_sources`, `nsf web replacements` / `nsf web revert` | merged ON / ON / `"ask"`; ship OFF / OFF / `"ask"` (fix lane) | ADR-061 | `genesis_wave3_s.json` |
| V | the worker's idle investigation measured inside the timer's allowance; `autonomy.idle` now honours the day's task count | the fix on; no setting | ADR-062 | `genesis_idle_coverage.json` |

No schema change (version 19). No new background activity beyond benchmark runners registered in
`src/neuroscifi/background.py`. No dependency.

## 2. The defaults, with the evidence for each

| Setting | Default | Why |
|---|---|---|
| `attribution_independence` | **off** (fix lane) | under lane S's own pre-registered rule the verdict was "unsafe" on development and held-out; it costs −0.114 `correct` in one development world against a spread of 0.066; 19 of 27 common credit phrasings give no key or a wrong key; staged with honest outlets crediting each other it merges them and `correct` falls 0.07–0.13, beyond the spread (review S-1, S-3, S-4); one sentence crediting two honest outlets merges them so a true fact is never verified (security H1) |
| `echo_recognition` | **off** (fix lane) | its fall in own versions comes from a phrase list added after held-out runs; echoes that credit no model pass at 67–100 % of the baseline's rate (review S-2, S-8); on a large individual it discards honest pages, a forgotten source blocks corroboration of that subject, its scan can hold the write lock for seconds per claim, and "the AI Act" reads as model text (security M2–M5) |
| `supersession` (`supersession_min_sources` 2) | `"ask"` (off) | gains less than the spread; false replacements; the owner's decision (it changes the rule that contradictions are asked, never decided); with it on, forget does not reach its record and a family or agent task could replace the owner's item (security M1, M6: fixed by the fix lane; two colluding pages can still replace a true item in the owner's research or a refresh) |
| `origin_grouping` | `"transitive"` (third fix lane) | the grouping of `3ce4ae3`, unchanged (0 of 614,988 world inputs and 0 of 8,437 offline-benchmark groupings differ). `"one_way"` closes N1 (a page naming two honest outlets as its origins joins them) on staged pages, but as first shipped it made copies of one story count as 2–3 sources (review3c F1); with the reviewer's edge choice it counts them once on every staged case, yet no benchmark world holds a page naming two origins, so neither direction is measured (ADR-063) |
| `recheck_order` | `"staleness"` (lane K's order) | no held-out gain: every order "no evidence" (ADR-060) |
| `predictive_regulation` | `false` (the third value `"shadow"` is the owner's opt-in) | the forecast has no host evidence yet; shadow lets the owner gather it without any change in behaviour (ADR-058) |
| front door in `nsf imagine scm` | on | its pre-registered rule held: the front-door group 0 → 1.000 held-out (12 of 12), every other leaf unchanged (ADR-059); its withheld look is pending |
| `autonomy.idle` and the day's task count | always (no setting) | a budget correction: the timer honoured the count, the idle path did not (ADR-062) |
| Pocket / console redraw | always (no setting) | a fix: nothing redrawn whose content did not change |

## 3. Results

Every table carries its references and spread; "pooled" figures are pooled, not a claim. All worlds are simulated:
fictional software releases or declared causal models, driven through the real modules offline, no model called.

### 3.1 Lane S: what counts as a source, and the individual's own text (ADR-061; `genesis_wave3_s.json`)

**What the lane reported.** On its own held-out worlds (s-mixed / s-wrong-newer / s-echo, 4 seeds), with both rules
on: own versions admitted 1.25 / 0.50 / 3.75 → 0.25 / 0 / 0.25 per run; syndicated-only versions 1.50 / 2.25 / 1.00 →
0; `correct` 0.577 ± 0.084 → 0.546 ± 0.046, 0.394 ± 0.067 → 0.375 ± 0.051, 0.573 ± 0.079 → 0.520 ± 0.078 (cost
−0.031 / −0.019 / −0.053, inside the spread); on lane F's worlds every paired difference 0. The measurement review
recomputed every one of these from the per-seed values and they hold as arithmetic. What they mean does not:

- **Unsafe under the lane's own rule (S-1).** The verdict function committed before the held-out runs
  (`cycles_s.verdict`) returns "unsafe" for the fixes on development and held-out: own versions are still admitted in
  2 of 4 held-out seeds of two worlds and in 2 of 3 development seeds of sd-wrong-newer, and on development
  sd-wrong-newer loses 0.114 against a spread of 0.066. The lane shipped them on the addendum's condition instead.
- **The own-text figure rests on a phrase list (S-2).** In these worlds the individual's own values live only in its
  imagined store, which the echo rule may not read. The fall comes from the rule that a sentence crediting "an
  assistant", "a chatbot" or "an AI model" is model text, added after the second held-out run, and matching 2 of the
  generator's 5 echo shapes. Echoes that credit no model are still admitted at 100 % / 100 % / 67 % of the baseline's
  rate; with the number restated in another format, the pages recognised as echoes fall from 12.5 to 2.0 per run.
- **The attribution figure is a property of the worlds (S-3).** The worlds' copies use exactly the five credit forms
  the parser reads. 19 of 27 common credit phrasings give no key or a wrong key; with them the rule admits the same
  syndicated-only versions as the baseline (1.75 / 1.75 / 0.75 per run).
- **False rejections were not measurable on these worlds (S-4).** No honest page there credits another outlet. Staged
  with 30 % of honest pages crediting the outlet that reported first (the reviewer's staging; the real web's share is
  unknown), the rule merges 11.75 / 5.0 / 10.75 → 65.25 / 52.0 / 61.0 honest outlets per run and `correct` falls by
  0.117 / 0.067 / 0.133, beyond every world's spread.
- **Not only removals (S-6).** Item by item, summed over seeds, the rules make the newest held version go from true
  to wrong in 25 / 6 / 21 subject-cycles (held-out) and 8 / 14 (development): a stale version kept where the baseline
  held the true newer one, because the newer one was refused on one origin. "The change is exactly the removal of
  wrongly admitted content" is false.
- **Lane F's "unchanged" is "not tested" (S-7).** On lane F's worlds both rules act only where earlier rules already
  acted; with the idle investigation on, echo recognition does change runs (§3.8).
- **Contaminated held-out (S-8).** The echo rule was changed twice after held-out runs; lane S's held-out was seen
  three times. Every lane S held-out figure above, and every supersession figure below, is development evidence.

The security review added, for the same two rules: one sentence crediting two honest outlets merges them into one
source, so a true fact is never verified, and a round-up page does it with no attacker (H1); the echo scan samples
200 rows with no subject filter, so on a long-lived individual honest pages are discarded as echoes (M2); forgetting
the source an answer rested on blocks corroboration of that subject (M3); the scan holds the write lock, 9.8 s for
one claim with 78 numbers at 10,000 tasks (M4); "the AI Act" and "the AI Safety Institute" match the model-credit
pattern (M5).

**What remains true.** The census of reworded wire copies that credit their origin in a phrasing the parser reads
(407 of 449 split before, 50 after) and the tests of the 16 adversarial cases (`added_wave3_s`, 16 of 16 pass) show
the parser does what it was written to do. They do not show that the hole is closed. **Both rules ship OFF** (fix
lane, D1 and D2); each is reported at its withheld look for the owner's information and cannot be switched on by it.

### 3.2 Supersession (ADR-061; off)

Supersession, when the owner turns it on, lets a newer version stated by at least two independent sources replace a
held item without asking. Gain against the fixes (both rules on), held-out (reference sd in brackets), as the fix lane
re-measured it (`genesis_wave3_fix_s.json`): +0.001 (0.046), −0.002 (0.051), +0.016 (0.078) on lane S's worlds;
+0.016 (0.105), +0.005 (0.057) on lane F's. No gain beyond the spread in any world; retention worse in one
(s-wrong-newer 0.50 → 0.75 regressed per run). Rule replacements on lane S held-out: 3 of 23 false (13 %, about one in
eight, pooled, not a claim; per world 1 of 11, 2 of 7, 0 of 5); 0 of 11 on lane F's. (Lane S's own figures,
`genesis_wave3_s.json`, before the fix lane's changes to echo recognition, were +0.008 / +0.007 / +0.020 and 3 of 33.)
The withheld look's `lane-s:supersede-K2` is supersession on the shipped defaults, with both rules off: not this
configuration (ADR-061, Consequences, has the second measurement review's development rehearsal of it).
The longest a false replacement stayed wrong: 8 cycles in s-mixed; at least 10 cycles in s-wrong-newer, still wrong
when the run ended (review S-5, per-seed maxima; the lane reported the mean of the maxima, 2 and 5; the fix lane's re-measurement records
per seed s-mixed 0, 0, 0, 8 and s-wrong-newer 10 (3 censored), 0, 10 (1 censored), 0).
`fixes` itself, with supersession "ask", made false replacements on s-wrong-newer through the path that existed
before Wave 3 (per seed 1, 0, 1, 0, persisting 9 and 10 cycles; review S-5). All of these are development evidence
(S-8). The owner's commands: `nsf web replacements`, `nsf web revert ID`
([RUNBOOK.md](../operations/RUNBOOK.md) §12).

### 3.3 Lane R: the order of the re-check (ADR-060; `genesis_recheck_order.json`)

**The decomposition** (development, mean of 3 seeds, the loop against the blind round-robin): pooled over the five
worlds, uncovered 64 %, found but not revised 20 %, not looked 22 %, unreadable 6 %, minus 13 % where the loop is
ahead, wrong source 0 (pooled, not a claim; the parts sum to the gap; review LOW 5). "Found, not revised" is mostly
contested refreshes: on development 50 of 70 such looks were contested, the rest held, mostly one-origin looks
(review LOW 3). The uncovered part is outside what an order can reach; an order can reach only "not looked",
0.03–0.05.

**Held-out** (4 seeds; paired gain against the baseline, seeds better of 4):

| | ho-rate-mixed | ho-rate-uniform | ho-rate-shift | ho-sparse (F) | ho-busy (F) |
|---|---|---|---|---|---|
| baseline sd (smallest visible gain) | 0.089 | 0.071 | 0.080 | 0.105 | 0.057 |
| snapshot / blind round-robin / oracle | 0.651 / 0.724 / 0.873 | 0.573 / 0.724 / 0.949 | 0.712 / 0.797 / 0.940 | 0.670 / 0.786 / 0.957 | 0.574 / 0.708 / 0.928 |
| no loop / baseline | 0.539 / 0.587 | 0.503 / 0.617 | 0.614 / 0.646 | 0.574 / 0.631 | 0.497 / 0.571 |
| combination `rate+seen+rotation/all` | −0.003 (1) | −0.004 (1) | +0.004 (2) | −0.011 (1) | +0.005 (3) |
| blind rotation `rotation/all` | −0.006 (0) | −0.007 (1) | +0.001 (2) | −0.007 (1) | +0.022 (3) |

Every one of the 14 order configurations: "no evidence", in both held-out families. Every paired gain lies between
−0.031 and +0.022 (review LOW 4; the ADR says −0.029). The order is exercised (it changed the launch in 62 of 64, 46
of 56 and 61 of 64 seed-cycles against lane K's order), coverage is identical in every configuration, and the safety
counts are 0 in every run, but lane R's worlds do not reproduce the withheld holes, so those zeros say nothing about
them. Cost: wall time +20 to +33 %. **No order of the re-check helps; the default stays.**

### 3.4 Lane V: coverage and the worker's idle investigation (ADR-062; `genesis_idle_coverage.json`)

The product runs the timer and the worker's idle investigation all day from one allowance; lane V measured both
extremes of how it can be split. Held-out, 4 seeds, paired gain against the same configuration without idle:

| | ho-rate-mixed | ho-rate-uniform | ho-rate-shift | ho-sparse (F) | ho-busy (F) |
|---|---|---|---|---|---|
| `idle` = `shipped+idle` (timer first) | +0.000 (0/0) | +0.046 (4/0) | +0.049 (4/0) | +0.017 (1/0) | +0.015 (2/1) |
| `idle-first` (idle takes the allowance first) | +0.057 (3/1) | +0.012 (3/1) | +0.086 (4/0) | +0.035 (3/1) | +0.018 (3/1) |
| gap to blind round-robin: base / idle / idle-first | 0.137 / 0.137 / 0.081 | 0.108 / 0.062 / 0.096 | 0.151 / 0.102 / 0.065 | 0.155 / 0.138 / 0.120 | 0.138 / 0.123 / 0.120 |

Verdicts: `idle` and `shipped+idle` "no evidence"; `idle-first` "revise" (beyond the sd only in ho-rate-shift,
+0.086 against 0.080). Taking the allowance first it removes 51–83 % of the uncovered part; the timer's own due
refreshes then fall to 0–1 per run and the idle path re-takes many of those items as its own candidates (7–17 of
22–31 idle tasks per development run), so the rise of "not looked" (+0.043 to +0.058) is a net figure (review
MEDIUM 2). **Even with the allowance first, the loop stays below the blind round-robin in every world: the idle
investigator does not close the gap within the same allowance.** Coverage is limited by the allowance, not by
candidates: with the timer first the idle path gets 0.25–5.75 looks per run against tens of admitted, unreached
subject-cycles. Safety counts 0 in all 210 runs (worlds that do not reproduce the withheld holes). The product fix:
`autonomy.idle` refuses at `daily_autonomous.tasks`; its test fails without it.

### 3.5 Lane C: front-door adjustment (ADR-059; `genesis_frontdoor.json`)

Counterfactual family, held-out, within 0.1 × scale: the front-door group 0 → 1.000 (12 of 12); all answerable
questions 0.875 → 1.000 (pooled, not a claim); every leaf outside the front-door group unchanged (0 of 257 and 0 of
276 differ, the two splits). Property
worlds with exact moments: 0 false answers, 0 traps answered. With moments estimated from n draws and n stated, the
answer carries an interval (held-out coverage 0.930–0.945 with the usefulness bound lifted); an unstated origin is
declined. Back-door answers keep their old behaviour with a caveat; under the usefulness bound their coverage at
n = 100 is 0.916 on a reviewer's re-run, against 0.953 without the bound (review, lane C LOW). The 5 adversarial
front-door cases passed even with the criterion broken, because they were declined by the moments rule first;
since the fix lane (`8a8fce7`) they are staged with population moments and a test shows them failing with the
separation check removed. Cost: about 0.1 ms per answer.

### 3.6 Lane H: evidence from the host without changing behaviour (ADR-058; `genesis_h.json`)

In shadow the gate decided exactly as with the setting off on every tick of 48 staged runs (0 decisions changed),
while the forecast would have held on 279 (development) and 508 (held-out) ticks. Cost: 3.7–3.9 µs per gate call
off, 8.7–9.0 µs in shadow (review H-5). Two cautions from the review, for the owner who gathers a week:
- **The host report will look good by construction** until the fix lane changes it: 73 % of the shadow's hits in the
  staged runs fall on ticks where the real gate already refused, and the mean lead equals the 180 s horizon, its cap
  (review H-1). Since the fix lane the report counts what the forecast adds (`settled.added`), separates holds the
  gate already made, reports the lead as a distribution and states, before any host data, what would support turning
  it on (`guide`); re-run on the 48 staged runs: development 62 added hits against 60 added false alarms, held-out 78
  against 198 (ADR-058).
- "Equals the family's scoring" holds for 3 of 7 quantities (episodes, preceded, lead); hits, false alarms, misses
  and quiet equal a reference counter written for the check (review H-2).

`nsf readiness` at 10,000 knowledge items and 500,000 events: median 36 ms (read-only). The existing
`strategies.outcomes` took 5.8 s on the same individual (two unindexed look-ups per task); the fix lane rewrote it
over one pass per table without an index (same counts; 0.035 s against 42–55 s on a staged individual of that size).

### 3.7 Lane P: what CI showed

**The digest.** CI's x86_64 runner gave `426abca5…` for the offline research benchmark where this host gives
`68731d72…` ([WAVE2.md](WAVE2.md) §5). The difference is one hashed leaf that names the browser's file path
(`probes.content.js_shell.render.availability`), not behaviour: setting only that leaf to CI's Playwright path gives
CI's digest (`tests/test_offline_digest_hosts.py`). The whole digest CI printed, read by the fix lane from the Wave 2
pull request's run 45759723830 with read-only `gh`, is
`426abca5d803349df0953045bd233495d2c23363a3107aea16de66151de46f6c`, and the one-leaf substitution gives exactly it
(the test now pins all 64 characters; review P-1). A `portable_sha256` (`d87dbb4f…` here) is now reported beside the unchanged `sha256`. The Wave 0
equivalence test still compares `sha256` on aarch64 only, and depends on `/usr/bin/chromium` and on `NSF_CHROMIUM`
being unset.

**The keyboard test.** CI's second run on the Wave 2 pull request failed
`tests/e2e/test_console.py::test_keyboard_reaches_every_enabled_button[overview]` once. Lane P found no control
replaced while the overview is idle; the overview loads twice when its link is clicked at start, and the test's
"settled" check could pass between the two loads (4 of 160 runs under CPU load before the fix, lane P's report). The
fix redraws a panel only when its markup changed and keeps focus and open details. Pocket's main view had a real idle
version of the defect (controls rebuilt every 3, 10 and 60 s with unchanged data: 36 replaced in 70 s before, 0
after, lane P's report). `tests/e2e/test_focus_kept.py`: 3 tests, which fail on the previous product code.

### 3.8 The lanes measured together (reconciliation, before the fix lane)

Lanes R and V measured without lane S's rules, and lane S without lane V's budget fix and lane R's order. The
reconciliation re-ran each lane's command on the merged tree `f0a1f09`, where lane S's rules were still on, and wrote
the results to new files; the lanes' own files are unchanged. **These figures are before the fix lane**: the fix
lane re-measured after the default change (ADR-061 "Fix lane", ADR-060, ADR-062; `genesis_wave3_fix_*.json`): with
both rules off and `baseline` explicit, every `correct` value the merge had moved in lanes R and V returns to the lane's
own evidence, no verdict changes, lane F's `correct` is identical in every configuration, world and seed, and lane S's
per-fix numbers equal lane S's evidence.

| Lane, command | Values compared | Values that differ | `correct` values that differ | Verdicts changed | File |
|---|---|---|---|---|---|
| S, `scripts/bench_wave3_s.py run` | 21,472 | 0 | 0 | 0 of 36 | `genesis_wave3_combined_s.json` |
| V, `scripts/bench_learning_cycles.py idle` | 10,080 | 157 | 9 | 0 of 12 | `genesis_wave3_combined_idle.json` |
| R, `bench_learning_cycles.py run --orders`, four splits | 23,520 | 105 | 3 | 0 of 56 | `genesis_wave3_combined_orders.json` |
| F, `bench_learning_cycles.py run`, both splits | 7,770 | 92 (outside the evidence's 4-decimal rounding) | 0 | 0 of 12 | `genesis_wave3_combined_cycles.json` |

The `correct` values that moved, per seed (lane's evidence → merged tree):
- lane V, development dev-churn seed 41: `idle` and `shipped+idle` 0.558673 → 0.569482, `idle-first` 0.59315 →
  0.583467; held-out ho-rate-mixed seed 2701: `baseline`, `shipped`, `idle`, `shipped+idle` 0.505633 → 0.527024,
  `idle-first` 0.6215 → 0.625738; seed 2704 `idle-first` 0.695562 → 0.717287;
- lane R, development dev-churn seed 41: `recheck:rate` 0.566029 → 0.583515, `recheck:rotation` 0.522118 →
  0.544659; held-out ho-rate-mixed seed 2701 `baseline` 0.505633 → 0.527024.

The other differing values are activity counts, Brier scores, pages and marks. The review traced seed 2701 and
dev-churn 41 to echo recognition (with it off, 2701 returns to the evidence's value); seed 2704 was not traced
(**unverified**). On ho-rate-mixed the baseline's mean moves 0.587 → 0.592 and `idle-first`'s paired gain +0.057 →
+0.058; no verdict of any lane changed, and no wrong-source content was admitted in either direction. Lane F's
`correct` is unchanged on every seed.

Also on `f0a1f09` (`genesis_wave3_combined_checks.json`): the cognitive benchmark, development and held-out:
retention all retained, all scenarios pass; adversarial 49 of 49 scored cases pass on both (54 cases; by origin: 13
original, 4 added in Wave 1, 1 restored, 10 added in Wave 2, 5 added by lane C, 16 added by lane S; 5 directive items
not staged). The offline research digest at the shipped defaults and with every setting off: `68731d7235cb…` on this
host (Wave 0's), portable `d87dbb4fb77f…`; `docs/quality/evidence/webcortex_bench.json` unchanged. The front-door
harness: 6 of 12,995 leaves differ from lane C's evidence, all expected (the adversarial counts 33 → 49 with lane S's
cases; retention run in its quick form here).

### 3.9 Regression: the new settings in the regression suite; the read-ahead test

**Coverage** (`tests/uat/`, commit `ce5a432`). Every setting Wave 3 added is now in the setting-lists test, the
rollback test (two new: `"shadow"`, and the front door off / on / off) and the fault-injection test: 7 Wave 3 entry
points (`recheck._ordered`, `claims.attribution_keys`, `consolidate._without_echoes`,
`consolidate._rule_replacement`, `shadow._verdict`, `readiness.report`, `causal._frontdoor`) made to raise, time out
and return garbage, plus a failure message naming a subject, with every Wave 1 and Wave 3 setting on: 28 cases (32
since the third fix lane added `origin_grouping` and its entry point `claims._one_way`). **12
of 28 failed on `f0a1f09`**: a failing attribution read or echo check left the owner's question "partial"; a failing
supersession rule failed the refresh's consolidation; garbage credits were taken silently. Three guards in lane U's
pattern fix them (the claim counts as with the setting off, the cluster stays as with echo recognition off, the item
is asked about as with supersession "ask"; the failure is a WARNING or the task's "error" event, its type only): 28 of
28 pass. The guards matter even with the rules off, because the owner can turn them on.

**The load-sensitive read-ahead test.**
`tests/webcortex/test_read_ahead.py::test_at_every_budget_edge_read_ahead_ends_as_one_at_a_time_and_stays_inside_the_budget[max_bytes=200]`
failed once in three lanes' full runs (lane H: unused 427 bytes against a bound of 203). The cause is in the product,
not the test: `net._Speculation.take()` grants a whole chunk to a fetch ahead that has already been abandoned ("its
connection is shut, the read ends itself"), but `abandon()` counts and only then shuts the connection. When the
abandon lands between two reads of the fetch's thread, the rest of a small page already in the response buffer is
read and counted as unused, beyond the task's byte budget. A probe (reconciliation, not committed) recorded every
`take()` made after an abandon: every run over the bound had such takes (2 of 40 with a 0.3 s delay before the
shutdown; 1 of 10 without any delay on the loaded host), and with `take()` granting 0 bytes to an abandoned fetch, 0
of 40 were over the bound. The test is right; making it deterministic needs that one-line product change, made by the fix lane
(`82b7fd9`): `take()` grants 0 bytes to an abandoned fetch, and a deterministic test forces the interleaving.

## 4. Disclosures

- **Withheld lines printed.** Lane C's early `grep -rn` printed two lines of `withheld.py` (a docstring line and the
  start of its causal-question spec: a chain and a confounded structure of 5 nodes); the main session had printed the
  head of that file before Wave 1's look. Step 0's seed grep printed `withheld.py`'s seeds line (`9001, 9002, 9003`,
  already in the documents).
- **Line counts seen.** Lane V's `wc -l` over `cycles*.py` gave away `cycles_withheld.py`'s line count; the
  reconciliation's `git diff --stat 9ac8943 f0a1f09` showed it too (no content in either).
- **A digest printed.** Lane H's first `bench_shadow.py check` printed the sha256 of `withheld.py` (through
  `cognition.environment()`), no content.
- **Indirect import.** Importing `neuroscifi.acceptance.cognition`, as every harness and the cognitive benchmark do,
  imports `withheld.py`; nothing of it is used.
- **Categories given.** Lanes R, S and V were told the categories in which the unchanged system failed on the
  learning-cycle withheld worlds (not the worlds).
- **Held-out seen again.** Lane S's echo rule was changed twice after its own held-out runs (lane F's held-out changed
  under the first version; the imagined-store read failed the quarantine test), so its held-out was seen three times;
  lane F's held-out split had been looked at by several lanes and reviews before lanes R, S and V.
- **Rule changed after the fact.** Lane S shipped its rules on against the verdict its own rule returned ("unsafe",
  §3.1).
- **Seeds line read.** The fix lane, the second fix lane and the round-2 measurement reviewer read the line of
  `tests/acceptance/test_learning_cycles_withheld.py` that states the withheld cycle seeds (7341–7344; the benchmark
  document states them too), not the worlds. The second fix lane copied both withheld files byte for byte inside a
  `git archive` of `3ce4ae3` for one isolated re-run of lane S's harness, never opened.
- The full non-e2e suite deselects the two tests that read the withheld cycle file's bytes
  (`test_the_withheld_worlds_are_frozen`, `test_the_withheld_seeds_are_disjoint_…`) in the lanes' and the
  reconciliation's runs; the suite's source scans read every file under `src` as text and print only violations.

## 5. What stays open

- **Source independence and the individual's own text** (the hole of step 0): open at shipped defaults. A rule that
  decides where a value came from by publication order and derivation, or by the owner's confirmation, is not built.
- Supersession's soundness when the owner turns it on: forget and revert (security M1), which tasks may trigger it
  (M6), revert not reopening closed questions (L1): fixed by the fix lane; the false-replacement risk from colluding
  pages stays.
- The withheld looks of Wave 3 (below): the front door, each source rule for the record, the re-check orders and the
  idle investigation for the record.
- The read-ahead accounting race (§3.9): fixed by the fix lane.
- The shadow report's definitions (H-1, H-2): changed by the fix lane; a host week is still to be run by the owner.
- Lane C's adversarial front-door cases and the sampled-trap test: staged with stated moments by the fix lane.
- **One page naming two honest outlets as its origins joins them into one source** at the shipped defaults, so their
  true fact is not confirmed (review3b N1, since `10339b8`, older than Wave 3): OPEN. The second fix lane closed it by
  changing the shipped grouping; the final check (review3c) found that change made copies of one story count as
  several sources (F1: copies naming the origin and each other, copies of an unread origin each adding a different
  second origin, "first appeared in the Regional Times. (AP)" beside a plain copy), so the third fix lane
  (`gen3-fix3`) restored the grouping of `3ce4ae3` as the default and put the one-way rule behind `origin_grouping`
  (off; ADR-063). Also open: a page on a host named like an outlet that an honest copy credits, naming another
  outlet, joins two honest outlets (F2; with the setting on only for names other than the news wires'); with the
  setting on, a page copying one honest outlet's sentence word for word and naming another outlet joins the two (as
  under the second fix lane; the default keeps them apart there); "Originally
  published by X" at the start of a sentence gives no origin (F3, the pattern is case-sensitive; not changed, it
  would change the shipped grouping); no benchmark world holds a page naming several origins, so a sound rule needs
  new worlds first (ADR-063 lists them).
- The security check of the fix round (review3b) found ten more items; the second fix lane (`gen3-fix2`) fixed nine
  with a test (N1 above is undone at the defaults): a
  forget no longer scans every answer inside its write transaction (N2: 0.15–0.25 s held at 10,000 answers of 20 kB,
  against 6.9–39.6 s) and marks only answers about the forgotten subject (N9); the re-check checks the day's task
  count inside its own transaction (N5); with `origin_grouping` "one_way", attribution's credits attach one way and
  in an order-free way (N3, N10; at the default they are as at `3ce4ae3`, open with `attribution_independence` on); echo
  recognition shares its value cap per claim and names and logs every claim it did not check (N4); a forget replaces
  the forgotten sentence in contradiction questions (N6); verdicts the shadow record could not take are counted and
  reported (N7); a damaged bar of an opt-in rule turns the rule off instead of loosening it, logged once, and the
  console lists damaged settings (N8). Still open: on a very large individual one page can still use up the echo
  checks' 2 s budget (now visible in `echo_skipped` and the log); a checkpoint restore still scans the answers once
  inside its own transaction (1.5–1.7 s at 10,000 answers of 20 kB); saved settings outside the checked Wave 3 web
  ones (`config.CHECKED_ON_LOAD`: five, six since the third fix lane added `origin_grouping`) are not checked when
  read (older); `requeue`, an owner surface, inserts without the day's task count (older).
- Coverage is limited by the allowance: a share of the allowance for reaching uncovered subjects would have to be
  chosen on development and tested on worlds no lane has seen. Not built.
- Everything above is offline and simulated: nothing here was measured on the open web, with a live answer model, or
  on the host's real memory over a week.

## 6. Backing each piece out

| Piece | How | Data |
|---|---|---|
| the two source rules (already off) | `nsf web config --db DB --set attribution_independence=false --set echo_recognition=false` | nothing stored by them |
| supersession (off) | `nsf web config --db DB --set 'supersession="ask"'`; each replacement: `nsf web revert ID` | its replacements stay listed |
| `recheck_order` (default) | `nsf web config --db DB --set 'recheck_order="staleness"'` | none |
| `origin_grouping` (default) | `nsf web config --db DB --set 'origin_grouping="transitive"'` (applies to the next task) | claims read while it was `"one_way"` keep a mark in their flags: a grouping made only of such claims (an older stored cluster on its own) still uses the one-way rule; one claim read after the switch makes it the default |
| shadow | `nsf config --no-predictive-regulation --db DB`; delete `<db>.resources/forecast-shadow*` to discard | numbers only |
| the front door | `nsf imagine scm FILE --no-front-door`; revert lane C's merge `5701bbe` | nothing stored |
| the idle task-count fix | revert `41198b3` (lane V) | none |
| Pocket / console redraw | revert `81edd00` (lane P) | none |
| the reconciliation's guards | revert `ce5a432` (the 12 fault cases fail again) | none |
| whole lanes | revert the merges: P `0b264c6`, C `5701bbe`, H `7dafc33`, R `f56f9cb`, S `60f062e`, V `f0a1f09`; step 0 `a0717c1` | none (no schema change) |

## The rule for the withheld looks (written before them)

Wave 3 ends with one look per changed mechanism at worlds no lane could see. What each look may change is fixed
here, before any is taken; the commit that adds this section precedes the looks' ledger lines. A first version of
this rule was replaced before any look, after an independent reviewer showed that it named no output fields, decided
two rules jointly and implied a command that could not produce one of its comparisons; a second reviewer then
checked this version against the commands' outputs on development and held-out worlds and supplied the exact field
names below.

**Decided before the looks, from the reviews, and not open to them:** `attribution_independence` and
`echo_recognition` ship OFF, and `supersession` ships OFF. The looks report each for the owner's decision. No look
can switch any of them on. Predictive regulation in shadow (ADR-058) has no withheld look: it changes no decision
(48 of 48 runs).

### Learning-cycle withheld worlds (`cycles_withheld.py`; the unchanged system's look is ledger line 4)

One command, on a clean committed tree, run in the background with at least 10 minutes allowed, its output
directory created beforehand and outside the tree:
`scripts/bench_learning_cycles.py final --look wave3 --mechanism wave3 --note "<note>" --out <outside>/cycles.json`.
It runs, on the same worlds and seeds, the twelve configurations of `cycles.LOOKS["wave3"]`: `reference:no-loop`,
`baseline`, `shipped`, `lane-s:fixes-off`, `lane-s:echo-only`, `lane-s:attribution-only`, `lane-s:both`,
`lane-s:supersede-K2`, `recheck:rate+seen+rotation/all`, `recheck:rotation/all`, `shipped+idle`, `idle-first`.

- **Read-back.** Each run reads its settings back (`config_read_back`), and `config_same_every_seed` must be true in
  every world. Against its reference in `cycles.LOOK_PAIRS`, a configuration may differ only as follows:
  `lane-s:echo-only`: `echo_recognition` true; `lane-s:attribution-only`: `attribution_independence` true;
  `lane-s:both`: both true; `shipped`: nothing (it equals `lane-s:fixes-off`); `lane-s:supersede-K2`: `supersession`
  "independent" with a minimum of 2 sources; `shipped+idle`: the idle investigation after the pass; `idle-first`:
  before the pass; the two `recheck:…/all`: `idle_recheck` true, the named order, and the per-pass and daily share
  set to 100; `lane-s:fixes-off` against `baseline`: `salience_record`, `hypothesis_fields`, `premise_recheck` and
  `answer_monitoring` true and `strategy_selection` "gate"; `reference:no-loop` against `baseline`: `autonomous` and
  the autonomy switch false. A configuration whose read-back differs otherwise is void, and the record says so.
- **Same worlds, same system as Step 0?** The look's `world_digests` must equal Step 0's. Then every per-seed figure
  of `reference:no-loop`, `baseline` and `shipped` that Step 0's file also has (`wall_seconds` excepted) is compared
  with Step 0's run of the same name, world and seed (worlds in file order, W1–W3). Wave 3 changed things under them
  (the idle investigator's daily count, the read-ahead fix, the re-check's refactoring): every difference is listed,
  and the comparisons below are made against the NEW runs, never against Step 0's file.
- **Each source rule, reported for the owner** (`lane-s:echo-only`, `lane-s:attribution-only`, `lane-s:both`, each
  against `lane-s:fixes-off`, same world and seed, read from `look.<configuration>.<world>[i]`): (a)
  `self_admitted + wire_admitted + one_origin_rises`, summed over the three worlds and four seeds, and per world;
  (b) per world, the mean over the four seeds of the paired difference (configuration minus `lane-s:fixes-off`) of
  `mean.correct`, beside the sd of `mean.correct` in Step 0's `baseline` run (`COGNITIVE_BENCHMARK.md`: W1 0.064,
  W2 0.039, W3 0.035, in file order); (c) `retention.lost + retention.regressed` per world and seed. The record
  calls a rule "supported on unseen worlds" only if (a) is lower in total and in no world higher, (b) is in no world
  below minus that world's sd, and (c) is in no world and seed higher; otherwise "not supported". The
  admitted-content counters see only the version ranges the harness marks as own-output and wrong-story versions
  (the output's `counters`); `admitted.wrong`, `admitted.own_only` and `admitted.wire_only` are reported beside them
  and decide nothing. Whatever the verdict, both rules stay OFF.
- **Supersession** (`lane-s:supersede-K2`). In the look it runs on the shipped defaults, with both source rules OFF,
  which is not the configuration ADR-061 measured. Reported: its paired gain per world against `shipped`;
  `replacements.true`, `.false`, `.other`, `.by_rule_true`, `.by_rule_false`; for each false replacement the subject,
  the cycle, `cycles` (split into `wrong` and `none`) and `censored` (the run ended while it was still wrong); and
  the same counts for `shipped`. Whatever it shows, it stays OFF.
- **The re-check's order and the idle investigator** (`recheck:…`, `shipped+idle`, `idle-first`): reported against
  their references in `cycles.LOOK_PAIRS`. Neither showed a held-out gain; the look cannot promote them.
- Every configuration is reported beside the references computed from the worlds alone (`references.worlds.<w>`:
  `snapshot`, `round_robin`, `round_robin_perfect`, `oracle`).

### Counterfactual withheld interface (`withheld.py`; Wave 1's look is ledger line 3)

Two runs, on a clean committed tree, outputs outside the tree in a directory created beforehand, both at the same
commit, nothing committed between them, the commands copied exactly:
`scripts/bench_cognition.py run --family withheld --mechanism imagination --final --note "<note>: front door on" --out <outside>/fd-on.json`
and
`scripts/bench_cognition.py run --family withheld --mechanism imagination --ablate front_door --final --note "<note>: front door off" --out <outside>/fd-off.json`.
After both runs, before the rule is applied, read back from each output: `ablate` is `[]` and `["front_door"]`, and
`families.withheld.mechanisms.counterfactual.used == "imagination"` with `fell_back_to_baseline == false`; otherwise
the look is void and reported as such. `scripts/bench_wave1.py final` is not used for this look: its "baseline" run
is not the ablation.

- **The front door stays ON** if and only if (a) in the on-run,
  `families.withheld.result.counterfactual.summary_by_group.front_door.max_relative_error.max` is ≤ 0.1, or is None
  with `…max_relative_error.n` equal to 0 (a missing `summary_by_group.front_door` voids the look); and (b) every
  leaf of `families.withheld.result` is equal in the two runs, except: in `counterfactual`, everything under
  `summary_by_group.front_door` and `by_query_kind["scm:confounded"]`, and the fields `answered_within_tolerance`,
  `answered_within_strict_tolerance`, `max_relative_error`, `front_door_declined`, `front_door_within_tolerance` and
  `declined_confounded_share` under `summary` and under each `per_seed[i]`. Run metadata (`generated_at`,
  `environment`, `final_runs`, `ablate`, `note`, `families.withheld.cost`) is not compared. The comparison is made
  by `scripts/compare_front_door_look.py`, which was first run on a held-out pair (no `--final`): 47 leaves differed,
  none outside the exemptions, and deliberate breaks were flagged. If (a) or (b) fails, the front door ships OFF in
  a commit made after the look.
- Reported whatever it shows: `front_door`, `front_door_declined` and `front_door_within_tolerance` per seed, and the
  group's `answered_within_tolerance`. If every front-door question was declined, the record says "not exercised by
  these worlds", never "no harm shown".

### Disclosed before the looks

- Lane C's search printed two lines of `withheld.py` (a docstring line, and the start of its causal-question spec: a
  chain and a confounded structure of 5 nodes); the main session had printed the head of that file before Wave 1's
  look. The front door is exact arithmetic checked on 6,000 random graphs and was not tuned on either, but the
  counterfactual look is therefore weaker evidence of transfer than a look at unseen worlds.
- Lanes R, S and V and the fix lanes were told the CATEGORIES in which the unchanged system failed on the
  learning-cycle withheld worlds, not the worlds. Lane V's `wc -l` showed that file's line count. The fix lane and
  the second reviewer read a test file that states the withheld cycle seeds (7341–7344); the benchmark document
  states them too.
- Lane S's echo rule was changed twice after its own held-out runs, and both source rules were changed again by
  the fix lane after the reviews, so none of their held-out numbers is clean; the withheld look is their only clean
  test, and it cannot switch either on.
- A crash after a look's ledger line is written still spends that look; the record would say so.

Nothing is tuned after a look. Whatever a look shows is reported in full.

## Reviews and fixes

Three review rounds read Wave 3, each read-only and each independent of the lane whose work it read.
**Round 1** read the merged tree `f0a1f09` (`9ac8943..f0a1f09`): a measurement review (9 high: lane S's evidence,
S-1 to S-6, the look rule, two, and the front-door look's command; 11 medium; 20 low) and a security review (0
critical, 1 high H1, 6 medium M1–M6, 8 low L1–L8). The fix lane `gen3-fix` acted on both, with the reconciliation
lane's three guards merged into it (merged `3ce4ae3`). **Round 2** read the fix lane's work (`f0a1f09..3ce4ae3`): a
measurement check of the fixes and of the looks' machinery (GO for both looks with preconditions; 1 high, 7 medium,
13 low, most of them about the documents) and a security check (every round-1 finding fixed, documented or partly
fixed; new N1–N10: 1 high, N1, older than Wave 3; 3 medium, N2 a regression at the shipped defaults; 6 low). The
second fix lane `gen3-fix2` acted on both (merged `2c48865`). **Round 3** was one security check of that lane
(`3ce4ae3..2c48865`): N2–N10 and the look guards verified; N1's change to the shipped grouping opened F1 (high by the
brief's rule) and, with attribution on, F2 (medium); F3–F6 low. The third fix lane `gen3-fix3` answered it (merged
`0410a22`). No reviewer read, printed or scored a withheld worlds file, passed `--final` or ran a `final`
subcommand; the round-1 measurement reviewer took only the sha256 of `cycles_withheld.py`'s bytes, to check its pin,
and the probes that import `neuroscifi.acceptance.cognition` import `withheld.py` indirectly, as every lane's do (§4).
**Disclosed:** the round-2 measurement reviewer, the fix lane and the second fix lane read the line of
`tests/acceptance/test_learning_cycles_withheld.py` that states the withheld cycle seeds (7341–7344), not the worlds;
the second fix lane copied both withheld files byte for byte inside a `git archive` of `3ce4ae3` for one isolated
re-run of lane S's harness, never opened. Every test named below was checked to exist on this branch
(`grep -rn "def <name>" tests`); that each fails without its fix is the fix lanes' report (each restored the old file
and ran the test), not re-checked here. Their findings that change this record are in §2, §3 and §5.

**The main session's decisions, with their reasons:**
- **Both lane S source rules ship OFF** (after round 1; the fix lane's D1, D2). Under lane S's own pre-registered
  rule the verdict was "unsafe" (S-1); the own-text figure rests on a phrase list added after held-out (S-2); the
  attribution figure is a property of the worlds' five phrasings (S-3); staged honest credits cost 0.07–0.13 `correct`
  (S-4); one sentence could stop a true fact from being verified (H1). The settings stay, their defects are fixed, the
  owner may turn either on; the withheld look reports them and cannot switch them on.
- **`supersession` stays OFF** (`"ask"`; D3): gain below the spread, 3 of 23 rule replacements false, some still
  wrong when the run ended, and it changes the standing rule that contradictions are asked, never decided.
- **The look rule was rewritten twice before any look**: after round 1 (it named no output fields, decided the two
  source rules jointly, lacked retention and one-origin terms, and implied a command that could not produce the
  front-door pair) and again with round 2's exact field names, read-back conditions and preconditions (the section
  "The rule for the withheld looks", committed before them).
- **Wave 3 does not change the shipped grouping** (after round 3). Counting copies of one story as independent
  sources (F1) is the more dangerous error; failing to verify a true fact (N1) is the safer one; and no benchmark world
  contains a page naming two origins, so neither direction is measured. The shipped grouping is `3ce4ae3`'s again
  (`origin_grouping: "transitive"`), and the one-way rule is a setting, off. **So N1, older than Wave 3, stays OPEN
  at the shipped defaults.** The main session re-ran review3c's grouping probe on the merged tree (`0410a22`): 19 of 19
  comparisons equal to the old code (the reviewer's saved output of the same probe on `2c48865`, before the third fix
  lane, when it had 16 comparisons: 11 equal, 5 different).

**Round 1, security review** (review3, `f0a1f09`; fixed by `gen3-fix`):

| Finding | Severity | What was done | Test that fails without the fix |
|---|---|---|---|
| H1: one sentence crediting two honest outlets merged them through the attribution rule, so their true fact was never verified (a round-up page does it with no attacker) | high | fixed (`8bd1673`, refined `df238c8`): credits kept in `flags["credited"]`, groups formed without them, a crediting group joins one outlet it credits and never connects two others; the rule also ships OFF (D1). Round 2: partly fixed, the same class open as N3 and, at the defaults, through document origins (N1) | `tests/acceptance/test_wave3_fix.py::test_one_sentence_crediting_two_honest_outlets_does_not_merge_them`, `::test_a_hostile_host_crediting_each_honest_outlet_merges_nothing`; control `::test_copies_still_count_with_the_outlet_they_credit` |
| M1: forget did not reach supersession's record; `nsf web revert` wrote a forgotten source back | medium | fixed (`a9e2137`): the record keeps only changed scalar columns; every web forget cuts older records down; forgetting the replaced version blanks the rule's question | `::test_a_revert_after_forgetting_a_source_of_the_replaced_item_does_not_write_it_back`, `::test_a_record_written_before_the_fix_is_cut_down_by_a_forget_and_never_written_back`, `::test_forgetting_the_replaced_items_research_leaves_no_trace_in_the_rules_records` |
| M2: the echo scan's `LIMIT 200` with no subject filter discarded honest pages on a large individual | medium | fixed (`98109d7`): subject names filtered in SQL, existence tested oldest first | `::test_a_sourced_answer_stays_sourced_when_many_other_subjects_state_the_value`; control `::test_an_unsourced_answer_is_still_echoed` |
| M3: forgetting the source an answer rested on blocked corroboration of that subject | medium | fixed (`98109d7`): before claims are forgotten, the answer sentences that stated their values are marked sourced (indexes only), also on checkpoint replay. This fix opened N2 (round 2) | `::test_after_forgetting_the_answers_source_later_honest_pages_still_count`, `::test_after_forgetting_the_research_the_answer_rested_on_later_honest_pages_still_count`, `::test_a_checkpoint_restored_after_the_forget_replays_the_mark` |
| M4: the echo scan held the write lock (9.8 s for one claim at 10,000 tasks) | medium | fixed (`98109d7`): checks built once before the write transaction, asserted values only (8 per claim, 64 per consolidation), a 2 s budget; an unchecked claim is not an echo and is listed in `echo_skipped`. Round 2: the cap could be filled by one page (N4) | `::test_the_echo_checks_run_before_the_write_transaction`, `::test_a_claim_not_checked_in_time_is_not_an_echo_and_is_named`, `::test_the_echo_checks_take_one_scan_at_ten_thousand_tasks` |
| M5: "the AI Act", "the AI Safety Institute" matched the model-credit pattern | medium | fixed (`98109d7`, the reviewer's narrowed pattern; ADR-061 calls the phrase rule a stop-gap) | `::test_ordinary_sentences_about_ai_are_not_model_text`, `::test_honest_pages_crediting_an_ai_institute_are_not_discarded_as_echoes`; control `::test_a_sentence_crediting_a_model_speaker_is_still_model_text` |
| M6: with supersession on, a family or agent task could replace the owner's item; two colluding pages replace a true item | medium | fixed for the scope (`a9e2137`: only `console`, `cli`, `refresh:`, `refresh-owner:` let the rule decide); the colluding-pages risk documented (ADR-061), not fixed | `::test_a_family_or_agent_task_never_lets_the_rule_replace_the_owners_item`; control `::test_refreshes_let_the_rule_decide` |
| L1: revert did not reopen the questions the rule closed, nor mark its links | low | fixed (`a9e2137`) | `::test_a_revert_reopens_the_questions_the_rule_closed_and_marks_its_links` |
| L2, L3: the attribution rule is wording-deep; `attribution_keys` quadratic in one claim's credits | low | documented (`claims.py`, ADR-061), not changed; the possessive credit bug fixed with S-3 | `::test_a_possessive_credit_names_the_outlet` (the possessive only) |
| L4: the shadow settlement blocked the worker's heartbeat on a lock another group member can hold | low | fixed (`7969153`): `LOCK_NB`, a held lock skips the pass. Round 2: dropped verdicts were untraced (N7) | `tests/test_forecast_shadow.py::test_a_held_shadow_lock_skips_the_settlement_without_waiting` |
| L5: the shadow files appended without an owner check; forged future days and leads taken | low | fixed (`7969153`) | `tests/test_forecast_shadow.py::test_a_record_file_of_another_account_is_not_appended_to`, `::test_forged_future_days_and_absurd_lead_times_are_not_taken` |
| L6: the idle task count was check-then-act (3 of a cap of 2) | low | fixed on the submit path (`7969153`: the autonomous count checked inside submit's write transaction, `BudgetRefused`); round 2: the re-check path still open (N5) | `tests/acceptance/test_wave3_fix.py::test_two_deciders_that_both_saw_room_cannot_both_take_the_last_place`, `::test_concurrent_autonomous_submissions_never_pass_the_cap` |
| L7: `nsf imagine scm`: a nan/inf `do` value gave a traceback; duplicate parents quadratic | low | fixed (`7969153`) | `::test_a_do_value_that_is_not_finite_is_a_one_line_error`, `::test_the_owners_command_prints_one_line_for_a_value_that_is_not_finite`, `::test_duplicate_parent_entries_do_not_make_the_front_door_quadratic` |
| L8: saved web settings not validated on load; CI actions tag-pinned | low | fixed for the Wave 3 web settings (`7969153`: `config.CHECKED_ON_LOAD`, damaged → default, logged, `nsf web config` lists `problems`); other settings not checked (older; open); tag pins documented, left (as everywhere in the repository) | `::test_a_damaged_saved_wave3_setting_reads_as_its_default` |

**Round 1, found while reconciling the lanes** (`gen3-rec`, §3.9; the read-ahead fix by `gen3-fix`):

| Finding | Severity | What was done | Test that fails without the fix |
|---|---|---|---|
| 12 of 28 fault cases failed: a failing attribution read or echo check left the owner's question "partial", a failing supersession rule failed the refresh, garbage credits taken silently | (not rated) | fixed (`ce5a432`): three guards; the fix lane gave the echo pre-check the same guard (`5c1b37f`) | `tests/uat/test_uat_fault_injection.py::test_a_failing_wave3_mechanism_leaves_the_owner_in_control`, `::test_a_wave3_failure_message_naming_a_subject_reaches_no_log_and_no_stored_record` |
| the read-ahead race: a fetch ahead already abandoned was granted a whole chunk, so unused bytes passed the task's byte budget (the load-sensitive `[max_bytes=200]` failure) | (not rated) | fixed (`82b7fd9`, test made load-proof `102e72b`): `take()` grants 0 bytes once abandoned | `tests/webcortex/test_read_ahead.py::test_a_fetch_ahead_abandoned_between_two_of_its_reads_reads_one_byte_more_not_the_rest_of_the_body` |

**Round 1, measurement review** (review3, `f0a1f09`; fixed by `gen3-fix`):

| Finding | Severity | What was done | Test that fails without the fix |
|---|---|---|---|
| S-1: the fixes shipped ON against lane S's own pre-registered rule ("unsafe" on development and held-out) | high | decided otherwise than the reviewer's wording (ship ON, let the look decide): both rules OFF (D1, D2, `a78a216`); ADR-061 opens with the "unsafe" verdict and "the hole is NOT closed" | `tests/acceptance/test_wave3_s.py::test_supersession_ships_off_and_its_values_are_checked` (asserts both rules default off) |
| S-2: the own-text fall rests on a phrase list added after held-out; echoes crediting no model still pass | high | measured (variant worlds committed alone, `1aaa75b`: 92–100 % still admitted) and stated; not fixed: the rule is off, a rule without a phrase list is not built | — (evidence `genesis_wave3_fix_s_variants.json`) |
| S-3: the attribution result is a tautology of the worlds' five phrasings; 19 of 27 others pass | high | measured (off-parser credits: 0 caught) and stated; the possessive bug fixed | `tests/acceptance/test_wave3_fix.py::test_a_possessive_credit_names_the_outlet` (the bug only) |
| S-4: honest outlets crediting each other were never staged; staged, `correct` falls 0.07–0.13 | high | measured at 0/10/30/50 % honest credits, before and after H1 (30 %: −0.125/−0.069/−0.102 before, −0.081/−0.041/−0.081 after); rule OFF | — (evidence `genesis_wave3_fix_s_variants.json`) |
| S-5: "longest false replacement 2 / 5 cycles" was a mean of per-seed maxima; really 8 and at least 10 (censored) | high | fixed: the harness records persistence, nothing-held cycles and censoring per replacement (`e4bbe32`; for the look `965416b`); figures corrected | `tests/acceptance/test_wave3_fix.py::test_false_persistence_counts_nothing_held_apart_and_censors_a_run_that_ends_wrong` |
| S-6: "exactly the removal of wrongly admitted content" is false item by item (true → wrong 25 / 6 / 21) | high | fixed in the evidence (`item_changes_vs_baseline`, `e4bbe32`) and the wording | — (evidence) |
| Q7-1, Q7-2: the look rule named no fields and decided the two rules jointly | high | the machinery (`965416b`): `lane-s:echo-only`, `-attribution-only`, `-both`, `LOOK_PAIRS`, `COUNTERS`, an explicit Step 0 `baseline`; the rule rewritten | `::test_the_look_configurations_read_back_as_named`, `::test_the_new_counters_count_on_development_worlds`, `::test_the_look_command_passes_the_twelve_configurations_to_final` |
| the front-door look's command (`bench_wave1.py final`) could not produce the ablated pair | high | fixed (`965416b`): the pair is two `bench_cognition.py run --final` runs; `scripts/compare_front_door_look.py` applies condition (b); `--final` refuses a dirty tree and an `--out` inside the checkout | `::test_the_front_door_comparison_holds_on_a_held_out_pair_and_catches_a_change`, `::test_the_counterfactual_look_refuses_a_dirty_tree_and_an_output_inside_the_checkout` |
| S-7: lane F's "unchanged" is "not tested"; S-8: lane S's held-out is development evidence | medium | wording (ADR-061, §3.1) | — (wording) |
| Q7-3, Q7-4, Q7-5, Q7-8: one-origin and retention terms; (b)'s arithmetic; other Wave 3 changes reach `shipped`; `baseline` carried the fixes | medium | in the machinery (`965416b`: `baseline` forces both rules off) and the rule (Step 0's figures compared against the new runs) | as Q7-1 |
| lane C: the 5 adversarial front-door cases and the sampled-trap test were declined by the moments rule, so a broken criterion passed | medium | fixed (`8a8fce7`): staged with population moments | `tests/test_causal_front_door.py::test_the_trap_cases_and_the_sampled_test_fail_when_the_separation_check_is_removed` |
| R/V MEDIUM 1: lane S's echo recognition moved lane R's and V's numbers on the merged tree | medium | after D1, lanes R and V re-measured: every moved `correct` value returns to the lane's evidence (`genesis_wave3_fix_orders.json`, `…_idle.json`) | — (evidence) |
| R/V MEDIUM 2: `idle-first`'s rise of "not looked" is a net figure | medium | wording (ADR-062, §3.4) | — (wording) |
| H-1: a shadow "hit" counted holds the gate already made; the lead equals its cap | medium | fixed (`8f72f27`): `settled.added`, holds the gate refused apart, the lead as a distribution, `shadow.GUIDE` committed before host data | `tests/test_forecast_shadow.py::test_the_report_separates_what_the_forecast_adds_from_holds_the_gate_already_made` |
| H-2: four of seven quantities equal a reference counter, not `breach_report` | medium | wording, and `breach_report`'s false alarms compared (`8f72f27`) | `tests/test_forecast_shadow.py::test_the_staged_check_compares_breach_reports_false_alarms_too` |
| 0-1: the guard's dirty check failed open when `git status` failed | low (low-medium) | fixed (`965416b`): a failing, timed-out or raising `git status` is dirty | `tests/acceptance/test_wave3_fix.py::test_a_dirty_check_that_fails_is_not_clean_and_the_look_is_refused` |
| 0-2: the worlds file's digest pinned by a test only | low | fixed for the cycles look (`965416b`: `cycles.WITHHELD_SHA256` checked before the ledger line; round 2's L-6 for the counterfactual look) | `::test_a_changed_worlds_file_is_refused_before_the_ledger_and_before_loading` |
| P-1: the CI digest prefix longer than the record | low | fixed (`3e2be80`): CI's whole digest read with read-only `gh`; the test pins all 64 characters | `tests/test_offline_digest_hosts.py::test_the_committed_metrics_are_the_recorded_digest_and_ci_differs_only_by_the_browser_path` |
| the other lows: S-9; Q7-6, Q7-7; lane C's six (bound coverage, the answerer's moments mark, "any regime", leaf identity, document drift, the rule's condition); R/V LOW 3–7; H-3, H-4, H-5 | low | wording in ADR-058 to ADR-062 and here (S-9 partly, completed in round 2); LOW 6: lane V's command records the commit per split (`ad6fc8a`); LOW 7: the product count enforced at submit (L6) | — (wording, evidence) |

**Round 2, measurement check** (review3b, `3ce4ae3`; fixed by `gen3-fix2`). Verdict on round 1: every measurement
finding fixed, S-9 partly. Both looks GO with preconditions (time allowed, an existing `--out` directory, the commands
copied exactly, nothing committed between the front-door runs); documents: request changes.

| Finding | Severity | What was done | Test that fails without the fix |
|---|---|---|---|
| M-1: `--out` checked for its place, not for being writable: a failure after the ledger line spends the look | medium | fixed (`94de412`): both look commands refuse before the ledger line | `tests/acceptance/test_wave3_fix.py::test_both_looks_refuse_an_output_that_cannot_be_written_before_the_ledger` |
| M-2: `--final` checked the mechanism and ablation after the ledger line | medium | fixed (`94de412`) | `::test_the_counterfactual_look_refuses_a_misspelt_mechanism_an_unknown_ablation_and_no_note` |
| M-3: the front-door condition (a) failed open | medium | fixed (`94de412`): (a) needs `max` ≤ 0.1, or None with `n` 0; a missing group is VOID | `::test_the_front_door_condition_a_does_not_fail_open` |
| L-2: `run --final` accepted an empty note | low | fixed (`94de412`) | as M-2 |
| L-6: the counterfactual look did not check `withheld.py`'s digest | low | fixed (`94de412`): `cognition.WITHHELD_SHA256` before the ledger line | `::test_the_counterfactual_look_refuses_a_changed_withheld_file_before_the_ledger` |
| L-7: the `sealed` fixture did not replace `cognition._withheld` | low | fixed (`94de412`): it raises if reached | — (the fixture; shown by removing the dirty guard: four guard tests fail on the stand-in) |
| L-12: a comment overclaimed what `baseline` turns off | low | fixed (`cycles.py` comment, `COGNITIVE_BENCHMARK.md`) | — (wording) |
| D-1: the documents said the shipped defaults block AI-credited echoes; they do not (that check is part of `echo_recognition`, off) | high | fixed (`34d6ebc`) in WAVE3.md, FINAL_EVALUATION.md, RECURSIVE_KNOWLEDGE_ASSESSMENT.md, RUNBOOK | — (wording) |
| D-2, D-3, D-4: supersession figures were lane S's pre-fix ones; a header said "fixes (shipped)"; the look's K2 is not the configuration measured | medium | fixed (`34d6ebc`): re-measured figures (3 of 23 false, about one in eight, gains +0.001 / −0.002 / +0.016) in six documents; the look's K2 stated as on the shipped defaults | — (wording) |
| D-5 to D-12, S-9, L-1: a leftover marker, wording, the order of `df238c8` after the variant worlds | low | fixed (`34d6ebc`) | — (wording) |
| the look's runtime on the withheld worlds; page kinds the generator may name | medium; low (open questions) | not knowable without the worlds: the rule asks for 10 minutes or more in the background; the admitted-content counters decide nothing | — |

**Round 2, security check** (review3b, `3ce4ae3`; fixed by `gen3-fix2`). Verdict on round 1: M1–M3, M5, M6 (scope),
L1, L4, L5, L7 and the read-ahead race fixed; M4 fixed for the lock; H1 and L6 partly (N3, N5); L2, L3 and the CI
pins documented; L8 fixed for the Wave 3 settings.

| Finding | Severity | What was done | Test that fails without the fix |
|---|---|---|---|
| N1: one page naming two honest outlets as its origins ("first published by X … originally appeared in Y") joins them, so their true fact is never verified; at the shipped defaults, since `10339b8`, older than Wave 3 | high | fixed by `gen3-fix2` as a change of the shipped grouping (`fe82ac1`); **decided otherwise after round 3**: the shipped grouping is `3ce4ae3`'s again and the one-way rule is a setting, off (`c91dc64`, ADR-063). **OPEN at the shipped defaults**; a KNOWN OPEN line in the adversarial family, never counted as a pass | `tests/acceptance/test_wave3_fix.py::test_a_page_naming_two_outlets_as_its_origins_merges_neither_with_one_way_and_both_at_the_default` (asserts both: kept apart with `"one_way"`, joined at the default); adversarial origin `added_wave3_fix2` |
| N2: since M3's fix every web forget scanned all task results inside its write transaction (6.9–39.6 s at 10,000 answers of 20 kB): a regression at the shipped defaults | medium | fixed (`de82c7b`): the scan before the lock; the transaction held 0.15–0.25 s; checkpoint replay still one pass inside the restore (open) | `::test_a_forget_reads_no_task_result_by_scanning_inside_its_write_transaction`, `::test_an_answer_written_between_the_scan_and_the_transaction_is_still_marked` |
| N3: with attribution on, a page crediting no one but carrying two wires' keys absorbs two honest crediting outlets | medium | fixed under `"one_way"` (`fe82ac1`; the reviewer's sketch not used: each crediting group takes one edge chosen by content); at the default as at `3ce4ae3`, open with attribution on; a word-for-word variant open with `"one_way"` too; the third fix lane re-staged the case (an expectation note committed first, `147cfe9`) | `::test_a_page_carrying_two_wire_credits_absorbs_no_honest_outlet_that_credits_one` |
| N4: with echo recognition on, one hostile page used up the echo check's value cap or budget, silently | medium | fixed (`0fc74fd`): the cap shared per claim, every unchecked claim named and logged once, the subject looked up past the row bound; one page can still use up the 2 s budget on a very large individual (open, now visible) | `::test_one_page_with_many_values_cannot_take_the_whole_value_cap_and_what_is_unchecked_is_named`, `::test_answers_about_other_subjects_cannot_crowd_out_the_answer_an_echo_repeats` |
| N5: the re-check checked the day's task count outside its transaction (3 of a cap of 2) | low | fixed (`4fb6e86`) | `::test_a_recheck_that_saw_room_cannot_take_the_last_place_after_the_idle_investigation` |
| N6: a forgotten source's sentence stayed quoted in contradiction questions (older than Wave 3) | low | fixed (`de82c7b`): quotes replaced by «#forgotten», live and on replay | `::test_forgetting_a_source_removes_its_sentence_from_contradiction_questions` |
| N7: shadow verdicts dropped while another account held the lock left no trace | low | fixed (`b3607bb`): counted, carried and reported (`not_recorded_busy`) | `tests/test_forecast_shadow.py::test_verdicts_dropped_while_the_record_is_busy_are_counted_and_reported` |
| N8: a damaged saved bar loosened an owner-tightened rule; logged on every read; not in the console | low | fixed (`4f3ca3a`): a damaged `supersession_min_sources` turns supersession to "ask" (the reviewer proposed falling back to 10), logged once, shown in the console; a damaged source-rule switch reads as off (documented); settings outside the checked ones still unchecked (older, open) | `tests/acceptance/test_wave3_fix.py::test_a_damaged_bar_of_a_rule_that_is_on_turns_the_rule_off_and_is_logged_once`, `tests/console/test_console_web.py::test_the_web_status_lists_damaged_saved_settings` |
| N9: M3's mark matched digit tokens across subjects | low | fixed (`de82c7b`): a sentence is marked only when it names a subject the forgotten claim named. This moved 36 values of lane S's family with echo recognition on (the known false rejection, no longer hidden) | `::test_forgetting_a_number_heavy_source_marks_no_answer_about_another_subject` |
| N10: the credit grouping depended on the order of the claims | low | fixed under `"one_way"`; at the default as at `3ce4ae3` (order-dependent with attribution on; not reproduced end to end) | `::test_the_groups_do_not_depend_on_the_order_of_the_claims` |

**Round 3, security check** (review3c, `2c48865`; answered by `gen3-fix3`). Verified: N2–N10, the look guards
(M-1, M-2, M-3, L-2, L-6, L-7), no modified test loosened.

| Finding | Severity | What was done | Test that fails without the fix |
|---|---|---|---|
| F1: with N1's change shipped, copies naming several origins (copies naming the origin and each other; copies of an unread origin each adding a different second origin; "first appeared in the Regional Times. (AP)" beside a plain copy) counted as 2–3 sources where they had counted as one | high by the brief's rule (medium on exploitability) | decided: not shipped. `origin_grouping` default `"transitive"` = `3ce4ae3`'s grouping, shown equal on 614,988 world inputs, 8,437 offline-benchmark groupings and 496 staged orders (0 differ); under `"one_way"` the edge is chosen by content (the reviewer's prototype) and every F1 shape counts once (`c91dc64`) | `tests/webcortex/test_origin_grouping.py::test_the_default_groups_exactly_as_3ce4ae3_in_every_order`, `::test_one_way_groups_as_recorded_in_every_order`, `::test_what_the_setting_decides_on_the_known_shapes`; adversarial origin `added_wave3_fix3` (3 cases, each fails with `2c48865`'s rule) |
| F2: a page on a host named like an outlet that an honest copy credits, naming another outlet, joins two honest outlets (a regression with attribution on; at the defaults the dateline form is the N1 class, open before and after) | medium | under `"one_way"` a wire's name is owned only by the wire's own site (`_WIRE_SITES`, a list not checked against the real web); open at the default and, for names other than the news wires', with `"one_way"` (reasoned, not staged); "roots only" rejected because it splits copy-of-copy chains | `::test_what_the_setting_decides_on_the_known_shapes` (the F2 pages) |
| F3: attribution lines are read case-sensitively ("Originally published by X" at a sentence start gives no origin) | low (older) | not changed (it would change the shipped grouping); documented open | — |
| F4: `bench_cognition.py baseline` spends a withheld look with none of the `--out` guards | low (older) | fixed (`860db86`) | `tests/acceptance/test_wave3_fix.py::test_the_baseline_command_refuses_an_output_a_look_may_not_write_before_running_anything` |
| F5: `cycles.final` accepted a note of only spaces | low | fixed (`860db86`) | `::test_the_cycles_look_refuses_a_note_of_only_spaces_before_the_ledger` |
| F6: accepted residuals: one page can use the 2 s echo budget; a checkpoint replay scans inside its transaction; the forget's pre-pass sees a change only through `(status, updated_at)` | low | documented open (no code path rewrites a result without `updated_at` today) | — |

**What the reviews changed in this wave's conclusions.** Lane S's two rules do not close the hole Step 0's look
found: they read the phrasings lane S's worlds were written with (S-2, S-3), one merges honest outlets (S-4, H1), the
other discarded honest pages and held the write lock (M2–M5); both ship OFF, and at the shipped defaults sources are
treated as at step 0, so the hole is open. Supersession ships OFF ("ask"), and so does the one-way grouping; the
shipped grouping is unchanged, so N1, older than Wave 3, stays open by decision. What changes at the defaults is
small: the front door (kept on by its look), the task count honoured by the idle investigation and checked inside the
submission, the read-ahead byte bound, a forget that holds the lock briefly and reaches contradiction questions, the
redraw fix and the look guards. The figures the reviews corrected: lane S's held-out numbers are development evidence
(S-8); supersession's false replacements are 3 of 23 (about one in eight; lane S reported 3 of 33) and the longest
stayed wrong 8 cycles and at least 10 (censored; lane S reported the means of the maxima, 2 and 5); the gains are
+0.001 / −0.002 / +0.016 (lane S: +0.008 / +0.007 / +0.020); lane R's paired-gain range reaches −0.031 (not −0.029);
"found, not revised" is mostly contested refreshes (50 of 70), not all; 73 % of the shadow's staged hits fell where
the gate already refused, and what the forecast adds is 62 hits against 60 false alarms (development) and 78 against
198 (held-out); lane C's five adversarial front-door cases tested the moments rule, not the criterion, until fixed;
back-door coverage under the usefulness bound at n = 100 is 0.916 on a reviewer's re-run; the documents had said the
defaults catch echoes that credit a model, which they do not (D-1). The negative results stand: no order of the
re-check helps; the idle investigator does not close the gap to a blind schedule.

**What stays open from the reviews** (each with its source):
- The hole of Step 0, at the shipped defaults: reworded copies of a story can count as independent sources, and
  pages repeating the individual's own answer count as corroboration, crediting a model or not (S-2, S-3, D-1; the
  source rules off). A rule by publication order and derivation, or the owner's confirmation, is not built.
- With `echo_recognition` on: echoes that credit no model still pass at 92–100 % of the baseline's rate (S-2, the
  fix lane's variant worlds); with `attribution_independence` on: 19 of 27 common credit phrasings are not read
  (S-3, L2), honest outlets crediting each other are merged (S-4: −0.081 / −0.041 / −0.081 at 30 % after H1), and
  N3 and N10 are as at `3ce4ae3` (review3b; the third fix lane).
- N1, a page naming two honest outlets as its origins joins them, at the shipped defaults (review3b N1; decision
  after review3c); F2's dateline form at the defaults (review3c F2).
- With `origin_grouping` "one_way": F2 for names other than the news wires' (review3c F2; reasoned, not staged); a
  page copying one honest outlet's sentence word for word and naming another outlet joins the two (the third fix
  lane); `_WIRE_SITES` not checked against the real web; a stored cluster read while `"one_way"` was on keeps its
  mark after the owner sets it back, until one newly read claim joins it (the third fix lane; §6).
- F3: "Originally published by X" at the start of a sentence gives no origin (review3c F3; not changed).
- No benchmark world holds a page naming several origins, so neither grouping is measured on realistic data
  (review3c 1e; ADR-063 lists the worlds needed).
- Supersession, when the owner turns it on: two colluding pages can still replace a true item in the owner's research
  or a refresh (security M6; review3b); revert never writes back a legacy record, which is cut down only at the next
  web forget (review3b M1).
- One page can use up the echo check's 2 s budget on a large individual (review3b N4, review3c F6; named in
  `echo_skipped` and the log); an own-values side table would need a schema change, not built.
- A checkpoint restore still scans the answers once inside its own transaction (1.5–1.7 s at 10,000 answers of
  20 kB; the second fix lane, review3c F6); the forget's pre-pass sees a changed answer only through
  `(status, updated_at)` (review3c F6).
- Saved settings other than the Wave 3 web ones (`config.CHECKED_ON_LOAD`) are not validated on load (security L8,
  review3b N8; older); a damaged source-rule switch reads as off (visible, by design).
- `requeue`, an owner surface, inserts without the day's task count (review3b (f); older); the owner's own console,
  CLI and voice research is not counted against it (by design).
- The shadow report's evidence for turning predictive regulation on needs a week on the host (H-4; `shadow.GUIDE`
  committed before any host data); a genuinely different account's lock (L4, L5, N7) was not tested.
- The Wave 0 equivalence test depends on `/usr/bin/chromium` and on `NSF_CHROMIUM` being unset (review3 P-1); CI's
  digest was read from the CI log by the fix lane, not re-fetched by a reviewer.
- `nsf imagine scm --help` does not name the `moments` / `samples` fields (review3 lane C LOW; the decline reason
  names them); a misdeclared graph is never tested (lane C).
- Seed 2704's move on the merged tree was not traced (§3.8); the outcome of a split of the allowance between the two
  measured extremes is not measured (R/V MEDIUM 2).
- The real web (syndication and credit wording, multi-origin pages, hosts named like wires), a live answer model and
  the host's own week: nothing in these reviews was measured there.

## The withheld looks

Taken once each, on 2026-11-26, on the clean tree `0bdc41a`, after the rule above was committed (`49bf81a`) and after
the validation below. Nothing was committed between the runs and nothing was tuned afterwards. The ledger
`docs/quality/evidence/genesis_final_uses.jsonl` gained three lines (5: the cycles look; 6 and 7: the front-door pair),
all `"dirty": false` at `0bdc41a`. Evidence: `docs/quality/evidence/genesis_wave3_look_cycles.json`,
`genesis_wave3_look_fd_on.json`, `genesis_wave3_look_fd_off.json`, `genesis_wave3_look_fd_compare.txt`.

### The front door stays on

The read-back holds (`ablate` `[]` and `["front_door"]`; `imagination` used, no fall-back), so the look is not void.

- **(a) holds.** `summary_by_group.front_door.max_relative_error.max` is 0.0 with `n` = 3: all 12 front-door
  questions (4 per seed) were answered, each exactly.
- **(b) holds.** `scripts/compare_front_door_look.py`: 47 leaves differ between the two runs, 0 outside the
  exemptions.
- Reported: `front_door_declined` 0, 0, 0 (with the front door off: 4, 4, 4); `front_door_within_tolerance` 1.0 in
  every seed; pooled (not a claim) `answered_within_tolerance` 0.827 → 0.987 and within the stricter 0.05,
  0.76 → 0.92.

By the rule, the front door ships ON. What this look is worth is limited as disclosed above: two lines of that
file's question spec had been printed by a lane's search, and the mechanism is exact arithmetic on a declared graph.

### The learning cycles: three worlds of kinds no split has, four seeds each

The look's `world_digests` equal Step 0's. Of the 5,217 per-seed figures Step 0 recorded for `reference:no-loop`,
`baseline` and `shipped`, the look reproduces all but 102, and those 102 are database file sizes
(`file_bytes_end`, `file_bytes_growth_per_cycle`): what the individual did on these worlds is what it did at Step 0.
Every read-back is as the rule allows and `config_same_every_seed` is true in every world. `shipped` equals
`lane-s:fixes-off` and `baseline` in every reported figure.

Share of subjects whose newest held item is true now (`mean.correct`), mean over 4 seeds (sd); worlds in file order.

| | W1 | W2 | W3 |
|---|---|---|---|
| no loop | 0.423 (0.051) | 0.311 (0.061) | 0.311 (0.065) |
| **as shipped** (= `baseline`, `lane-s:fixes-off`) | **0.465 (0.064)** | **0.286 (0.039)** | **0.323 (0.035)** |
| *reference:* never refreshed | 0.455 | 0.385 | 0.395 |
| *reference:* blind round-robin, same allowance | 0.636 | 0.541 | 0.537 |
| *reference:* oracle, same allowance | 0.797 | 0.809 | 0.820 |

Each rule or mechanism against its reference configuration, same world and seed: the mean paired difference of
`mean.correct` (seeds better of 4), then the counts the rule names, summed over the four seeds per world.

| Configuration (against) | W1 | W2 | W3 | own-output / wrong-story versions admitted, one-origin rises (W1; W2; W3) | subjects lost or regressed (W1, W2, W3) |
|---|---|---|---|---|---|
| as shipped | | | | 4 / 2 / 0; 6 / 17 / 0; 6 / 12 / 1 (48 in all) | 1, 3, 3 |
| `lane-s:echo-only` (`lane-s:fixes-off`) | +0.017 (1) | +0.013 (3) | +0.007 (1) | 0 / 4 / 0; 0 / 18 / 0; 0 / 15 / 1 (38) | 0, 2, 2 |
| `lane-s:attribution-only` (`lane-s:fixes-off`) | 0 (0) | 0 (0) | 0 (0) | identical to as shipped (48) | 1, 3, 3 |
| `lane-s:both` (`lane-s:fixes-off`) | +0.017 (1) | +0.013 (3) | +0.007 (1) | as `lane-s:echo-only` (38) | 0, 2, 2 |
| `lane-s:supersede-K2` (`shipped`) | +0.011 (4) | +0.042 (4) | +0.007 (4) | 3 / 7 / 0; 6 / 21 / 0; 7 / 13 / 1 (58) | 1, 4, 4 |
| `recheck:rate+seen+rotation/all` (`baseline`) | −0.005 (2) | −0.007 (1) | −0.006 (2) | 51 | 2, 4, 4 |
| `recheck:rotation/all` (`baseline`) | +0.008 (3) | −0.002 (1) | −0.015 (2) | 47 | 0, 4, 3 |
| `shipped+idle` (`shipped`) | +0.006 (3) | +0.035 (3) | +0.024 (3) | 50 | 1, 2, 3 |
| `idle-first` (`baseline`) | +0.013 (3) | +0.042 (3) | +0.052 (4) | 2 / 1 / 0; 7 / 13 / 0; 7 / 13 / 0 (43) | 1, 1, 1 |

The admitted-content counters see only the version ranges the worlds mark as the individual's own outputs and as
the wrong story (the output's `counters`). Beside them, deciding nothing: `admitted.wrong` 49 as shipped, 38 with
the echo rule, 66 with supersession; `admitted.own_only` 16 as shipped, 0 with the echo rule.

What the look says, by the rule written before it:

- **`echo_recognition`: supported on unseen worlds; it stays OFF.** (a) 48 → 38 in total and lower in every world
  (6 → 4, 23 → 18, 19 → 16); (b) no world below minus its spread (all three paired differences are positive, each
  smaller than the spread); (c) in no world and seed more subjects lost or regressed. Versions only the individual
  itself had asserted that became knowledge: 16 → 0. Versions only the wrong story stated: 31 → 37, six MORE: with
  the own-output versions gone, the wrong story's took some of their places. This is the rule's only clean test,
  it is one look at three worlds, and the rule's known limits stand (it catches what credits a model or repeats a
  stored answer's value and wording; one page can exhaust its time budget on a large individual). Whether to turn
  it on is the owner's decision: `nsf web config --set 'echo_recognition=true'`.
- **`attribution_independence`: not exercised by these worlds; "not supported"; it stays OFF.** Every figure equals
  the run without it: no copy in these worlds credits its origin in a phrasing the rule reads. The look says
  nothing for or against it beyond that.
- **The hole Step 0 found is open at the shipped defaults, on the same worlds, in the same counts:** 16 own-output
  and 31 wrong-story versions became knowledge, one confidence rose from a single origin, 7 subjects held correctly
  went wrong.
- **`supersession` (K = 2, both source rules off): it stays OFF.** Its paired gain is positive in every seed of
  every world and beyond the spread in one (W2 +0.042 against 0.039). It made 54 replacements by the rule, 51 to the
  true newer version and 3 to a wrong one; with the 2 made by the path that exists without it, 5 false replacements,
  and every one was still wrong when its run ended (3, 9, 15, 3 and 17 cycles: lower bounds). As shipped, the same
  worlds have 1 (9 cycles, still wrong at the end). It admits more wrong-story versions (31 → 41) and loses or
  regresses more subjects (7 → 9). The trade is now measured on unseen worlds: more subjects correct on average,
  and wrong beliefs that nothing corrected before the run ended.
- **The re-check's order: no gain** (paired differences from −0.015 to +0.008 per world, never beyond the spread),
  as on held-out.
- **The idle investigator: the one mechanism here that helped on unseen worlds, and the look cannot promote it.**
  Run after the timer's pass (`shipped+idle`) it adds +0.006, +0.035, +0.024; run before it (`idle-first`) +0.013,
  +0.042, +0.052, beyond the spread in W2 and W3, with coverage 0.883 / 0.872 / 0.815 → 0.972 / 0.938 / 0.941,
  fewer subjects lost or regressed (7 → 3) and no more wrongly admitted content (48 → 43). On held-out the same
  configuration was "revise" (beyond the spread in one world of five). How the daily allowance is divided between
  refreshing what is known and reaching what is not is the next thing to measure properly, on worlds written for it.
  *Correction, 2026-11-26 (Wave 4 lane A, ADR-064 "Follow-up: repeatability").* Runs that use the idle investigation
  were not repeatable at the code of this look: two choices among equal records followed the random task ids
  (`autonomy._lineage` walked a set of task ids; `curiosity.search_terms` and `curiosity.rank` broke ties among claims
  stored at one instant by task id). Measured afterwards on 18 public worlds, 10 repeats each, of this code:
  `baseline` and `shipped+idle` never varied; `idle-first` varied in 4 of 18 worlds, and a world's mean paired gain
  moved by up to 0.015 between repeats (0.007 and under 0.001 in the other three). The look's figures are single runs
  on worlds that cannot be re-run here, so each carries that uncertainty: `idle-first` W1 +0.013 is within its spread
  (0.064) either way; **W2 +0.042 exceeds its spread (0.039) by only 0.003, less than the largest repeat-to-repeat
  swing measured, so "beyond the spread in W2" is not established by this single run**; W3 +0.052 exceeds its spread
  (0.035) by 0.017, more than every swing measured, so it likely stands (not verified on those worlds). `shipped+idle`
  (+0.006 / +0.035 / +0.024) did not vary in any measured world. The held-out figures of §3.4 (ADR-062) reproduce
  exactly in 10 of 10 repeats. Since Wave 4 lane A both choices use a stable order (creation order) and repeated runs
  are identical. The seed 2704 move of §3.8 is not explained by this (that run did not vary in 10 repeats); it stays
  untraced.
  *Correction, 2026-11-26 (Wave 4 record lane, after Wave 4's withheld look; the correction above is kept as it was).*
  Wave 4 took one more look at these worlds (ledger line 10, on `e812a8e`) with `baseline`, `idle-first`, `shipped`
  and `shipped+idle` on the fixed, repeatable code. The references' means (0.465 / 0.286 / 0.323) and all six gains
  above equal this look's figures to three decimals (`idle-first` +0.013 / +0.042 / +0.052, `shipped+idle` +0.006 /
  +0.035 / +0.024), so on these worlds the ties that followed random ids did not move the result and these figures
  stand. `idle-first` is **reproduced on a repeatable run; beyond the spread by 0.003 in W2 and 0.017 in W3 on four
  seeds; a small margin in W2; it cannot be promoted and remains below the blind schedule** (0.478 / 0.328 / 0.376
  against 0.636 / 0.541 / 0.537). `shipped+idle` is within the spread in all three. Lane A's "not established" for W2
  and the later "not shown to fail, not established" for W3 are superseded by this look; "likely stands" for W3 is
  borne out by it. [WAVE4.md](WAVE4.md), "The withheld looks"; `genesis_wave4_look_analysis.json`.
- **Every configuration stays far below a blind round-robin with the same allowance** (0.636 / 0.541 / 0.537): the
  best here reaches 0.482, 0.328 and 0.375.

## Validation

The reconciliation's checks (branch `gen3-rec`) are in its report; the final validation is recorded here.

Run on `49bf81a` (the commit that adds the rule for the looks), 2026-11-26, on this host (shared with other work;
nothing below is a measurement of the DGX Spark, the open web or a live answer model). The commits after it changed
documents only.

| Check | Result |
|---|---|
| Quality gate (`bash scripts/check.sh`: ruff, the non-e2e suite, the JS checks, the numerical reproduction) | 6,195 passed, 22 skipped, 35 subtests; ALL CHECKS PASSED |
| Browser suite (`scripts/e2e.sh`) | 212 passed, 1 skipped |
| `nsf acceptance --all` | pass |
| Sentence guard against `307b907` (`scripts/guard_differential.py compare`) | 1,937 compared: 1,688 identical, 232 stricter, 17 loosened, 0 different |
| Two accounts (`scripts/two_account_check.py`, real uids in a user namespace) | 55 of 55 |
| Offline research benchmark, every setting off, and as shipped | sha256 `68731d7235cb…` both, as at Wave 0 on this host; the portable digest `d87dbb4fb77f…` both |
| Cognitive benchmark, development and held-out (`scripts/bench_cognition.py run`) | retention: all retained on both; adversarial: 55 of 55 scored cases pass on both (13 original, 4 added in Wave 1, 1 restored, 10 added in Wave 2, 27 added in Wave 3: 5 lane C, 16 lane S, 3 and 3 by the fix lanes) |
| Dry run of the front-door look on held-out, no `--final` (`scripts/compare_front_door_look.py`) | 47 leaves differ, 0 outside the exemptions |
| Dry run of the cycles look's twelve configurations on development, no `--final` | all twelve ran |

The same checks were green on the two earlier trees of the fix rounds (`3ce4ae3`: gate 6,093 passed, browser suite
212; `2c48865`: gate 6,117 passed, browser suite 212). The two tests that failed once under load inside single lanes
(a voice follow-up timing test and the read-ahead byte-bound test, the second a product race fixed in this wave)
failed in none of the three runs.

Not run here: anything on the open web, a live answer model, real hardware, a real second account, two real
processes for the voice follow-up, systemd, or a week of the shadow record on the host (the owner's to run).

**Note added 2026-11-26 (Wave 4 lane D), from CI after the merge.** The Wave 3 pull request (#7, head `c967604`,
merged as `8f68beb`) passed all six of its checks on the first run: three jobs (lint, tests, JS checks and the
numerical reproduction; the browser suite with axe-core; the simulated first-release milestones) in each of its two
workflow runs (`pull_request` 45835871829 and `push` 45835782417, attempt 1, every job "success"; read with read-only
`gh`). The pull-request run's artifact `offline-deterministic` (x86_64) has the portable digest `d87dbb4fb77f…`, the
same as the development host's at the shipped defaults; the full digests (CI `426abca5d803…`, the host
`68731d7235cb…`) differ in exactly one leaf, the browser's file path (`probes.content.js_shell.render.availability`:
`chromium at /usr/bin/chromium` here, Playwright's own Chromium under `/home/runner/.cache` on CI), by
`scripts/diff_deterministic.py` against a run of `scripts/bench_wave1.py offline --regime defaults` on `8f68beb`.
