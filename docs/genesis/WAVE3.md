# Wave 3: what counts as a source, the re-check's order, coverage, the front door, evidence from the host

Status: 2026-11-26. Wave 3 of Project Cognitive Genesis. Step 0 (`gen3-0`) took one look at new withheld
learning-cycle worlds with the unchanged system; six lanes (P, C, H, R, S, V) were then built in parallel and merged
on `genesis-wave3` (`f0a1f09`). Two independent reviews read the merged tree (measurement, security); a fix lane
(`gen3-fix`) acts on their findings, and this reconciliation lane (`gen3-rec`) wrote the record. Where the fix lane will
supply a final number, wording or default, the text carries the marker [fix lane] on that line. Sources: ADR-058 to
ADR-062 in [ARCHITECTURAL_DECISIONS.md](../architecture/ARCHITECTURAL_DECISIONS.md), the evidence files named beside
each number, the lanes' reports and the two reviews. Nothing here is a claim of consciousness, awareness or general
intelligence; the names describe functions.

## On one screen, in plain words

**The hole the withheld look found is still open.** At Wave 3's step 0 the unchanged system was run once on worlds no
lane had seen. Two protections that held in all 120 earlier held-out runs did not hold there: versions that only the
system itself had stated, and versions that only a wrong, copied story stated, became knowledge; once a confidence
rose on a single source; and some subjects held correctly early were later wrong
(`docs/quality/COGNITIVE_BENCHMARK.md`, "The unchanged system's one look"). Lane S built two rules against this. The
reviews showed that they do not close the hole: they read the exact phrasings lane S's own test worlds were written
with, and miss others; one of them merges honest outlets that credit each other; the other throws away honest pages on
a large individual and treats ordinary sentences about "the AI Act" as model text. **Both rules ship OFF** [fix lane].
At Wave 3's shipped defaults the individual treats sources exactly as it did at step 0: a reworded copy of a story can
still count as an independent source, and a page repeating the individual's own answer without crediting a model can
still read as corroboration. Reading the wording of a sentence does not decide where a fact came from; what would is a
different kind of rule (which page published a value first and which pages derive from it, or the owner's
confirmation). It is not built. It is the first of the next directions (FINAL_EVALUATION.md §6).

**What Wave 3 changes at the shipped defaults:**
- `nsf imagine scm` answers causal questions that only front-door adjustment can answer, saying so, where before it
  declined them (lane C; on; its withheld look is pending);
- the worker's idle investigation now stops at the day's task count, as the timer always did (lane V; a budget fix);
- the console overview and Pocket's lists are redrawn only when their content changed, so the keyboard stays where it
  was (lane P);
- two things the owner can use and that change nothing by themselves: predictive regulation "in shadow", which only
  writes down what the forecast would have done, and `nsf readiness`, which counts real outcomes (lane H);
- built and off: another order for the knowledge re-check (lane R), automatic replacement of an out-of-date fact
  (supersession, lane S), and the two source rules (lane S) [fix lane].

**Negative results, as plainly as the rest:**
- No order of the knowledge re-check helps: every one of 14 orders gets "no evidence" on held-out; the order is not
  what limits the loop (lane R, ADR-060).
- The worker's idle investigation does not close the gap to a blind schedule within the same allowance. Given the
  allowance first it reaches more subjects, but it stays below the blind schedule in every world (lane V, ADR-062).
- Supersession gains less than the spread, and on lane S's held-out worlds about one replacement in eleven was wrong;
  a wrong one stayed wrong for 8 cycles in one world and at least 10 in another, still wrong when the run ended
  (review S-5; [fix lane]).
- Lane S's held-out numbers are development evidence: its echo rule was changed twice after held-out runs.

## Why Wave 3 exists

The directive's last order is "repeat only where evidence supports further progress". Wave 2 measured that linking
the mechanisms gives no gain and that knowledge decays over repeated cycles, with a blind schedule of the same budget
doing better than the product's loop ([WAVE2.md](WAVE2.md)). Wave 3 went only where a measurement pointed at a
specific limit: the loop's shortfall (lanes R and V), what counts as a source (lane S), the gap the causal engine
declined (lane C), the missing real outcomes and the untested forecast on the host (lane H), and two things CI showed
(lane P). A measured "no gain" is a valid result. Nothing ships on without a held-out gain beyond the baseline's
spread, an ablation that removes it, retention intact and no adversarial regression; lane S's two rules were turned
on against this and are being turned off (§3.1).

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
| S | `attribution_independence` (a sentence crediting an outlet counts with it), `echo_recognition` (a page repeating the individual's own text is no evidence for it), `supersession` with `supersession_min_sources`, `nsf web replacements` / `nsf web revert` | merged ON / ON / `"ask"`; ship OFF / OFF / `"ask"` [fix lane] | ADR-061 | `genesis_wave3_s.json` |
| V | the worker's idle investigation measured inside the timer's allowance; `autonomy.idle` now honours the day's task count | the fix on; no setting | ADR-062 | `genesis_idle_coverage.json` |

No schema change (version 19). No new background activity beyond benchmark runners registered in
`src/neuroscifi/background.py`. No dependency.

## 2. The defaults, with the evidence for each

| Setting | Default | Why |
|---|---|---|
| `attribution_independence` | **off** [fix lane] | under lane S's own pre-registered rule the verdict was "unsafe" on development and held-out; it costs −0.114 `correct` in one development world against a spread of 0.066; 19 of 27 common credit phrasings give no key or a wrong key; staged with honest outlets crediting each other it merges them and `correct` falls 0.07–0.13, beyond the spread (review S-1, S-3, S-4); one sentence crediting two honest outlets merges them so a true fact is never verified (security H1) |
| `echo_recognition` | **off** [fix lane] | its fall in own versions comes from a phrase list added after held-out runs; echoes that credit no model pass at 67–100 % of the baseline's rate (review S-2, S-8); on a large individual it discards honest pages, a forgotten source blocks corroboration of that subject, its scan can hold the write lock for seconds per claim, and "the AI Act" reads as model text (security M2–M5) |
| `supersession` (`supersession_min_sources` 2) | `"ask"` (off) | gains less than the spread; false replacements; the owner's decision (it changes the rule that contradictions are asked, never decided); with it on, forget does not reach its record and a family or agent task can replace the owner's item (security M1, M6) [fix lane] |
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
the parser does what it was written to do. They do not show that the hole is closed. **Both rules ship OFF** [fix
lane]; each is reported at its withheld look for the owner's information and cannot be switched on by it.

### 3.2 Supersession (ADR-061; off)

Supersession, when the owner turns it on, lets a newer version stated by at least two independent sources replace a
held item without asking. Gain against the fixes, held-out (reference sd in brackets): +0.008 (0.046), +0.007
(0.051), +0.020 (0.078) on lane S's worlds; +0.016 (0.105), +0.008 (0.057) on lane F's. No gain beyond the spread in
any world; retention worse in one (s-wrong-newer 0.50 → 0.75 regressed per run). Rule replacements on lane S
held-out: 3 of 33 false (9 %, pooled, not a claim; per world 1 of 13, 2 of 12, 0 of 8) (`genesis_wave3_s.json`).
The longest a false replacement stayed wrong: 8 cycles in s-mixed; at least 10 cycles in s-wrong-newer, still wrong
when the run ended (review S-5, per-seed maxima; the lane reported the mean of the maxima, 2 and 5) [fix lane].
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
front-door cases currently pass even with the criterion broken, because they are declined by the moments rule first;
the fix lane stages them with population moments [fix lane]. Cost: about 0.1 ms per answer.

### 3.6 Lane H: evidence from the host without changing behaviour (ADR-058; `genesis_h.json`)

In shadow the gate decided exactly as with the setting off on every tick of 48 staged runs (0 decisions changed),
while the forecast would have held on 279 (development) and 508 (held-out) ticks. Cost: 3.7–3.9 µs per gate call
off, 8.7–9.0 µs in shadow (review H-5). Two cautions from the review, for the owner who gathers a week:
- **The host report will look good by construction** until the fix lane changes it: 73 % of the shadow's hits in the
  staged runs fall on ticks where the real gate already refused, and the mean lead equals the 180 s horizon, its cap
  (review H-1). The fix lane makes the report count only what the forecast adds [fix lane].
- "Equals the family's scoring" holds for 3 of 7 quantities (episodes, preceded, lead); hits, false alarms, misses
  and quiet equal a reference counter written for the check (review H-2).

`nsf readiness` at 10,000 knowledge items and 500,000 events: median 36 ms (read-only). The existing
`strategies.outcomes` takes 5.8 s on the same individual (two unindexed look-ups per task; reported, not fixed).

### 3.7 Lane P: what CI showed

**The digest.** CI's x86_64 runner gave `426abca5…` for the offline research benchmark where this host gives
`68731d72…` ([WAVE2.md](WAVE2.md) §5). The difference is one hashed leaf that names the browser's file path
(`probes.content.js_shell.render.availability`), not behaviour: setting only that leaf to CI's Playwright path gives
CI's digest (`tests/test_offline_digest_hosts.py`; the 12-digit prefix comes from a CI log that is not archived,
review P-1). A `portable_sha256` (`d87dbb4f…` here) is now reported beside the unchanged `sha256`. The Wave 0
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
lane re-measures after the default change [fix lane].

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
and return garbage, plus a failure message naming a subject, with every Wave 1 and Wave 3 setting on: 28 cases. **12
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
of 40 were over the bound. The test is right; making it deterministic needs that one-line product change, listed for
the fix lane, not made here [fix lane].

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
- The full non-e2e suite deselects the two tests that read the withheld cycle file's bytes
  (`test_the_withheld_worlds_are_frozen`, `test_the_withheld_seeds_are_disjoint_…`) in the lanes' and the
  reconciliation's runs; the suite's source scans read every file under `src` as text and print only violations.

## 5. What stays open

- **Source independence and the individual's own text** (the hole of step 0): open at shipped defaults. A rule that
  decides where a value came from by publication order and derivation, or by the owner's confirmation, is not built.
- Supersession's soundness when the owner turns it on: forget and revert (security M1), which tasks may trigger it
  (M6), revert not reopening closed questions (L1) [fix lane].
- The withheld looks of Wave 3 (below): the front door, each source rule for the record, the re-check orders and the
  idle investigation for the record.
- The read-ahead accounting race (§3.9) [fix lane].
- The shadow report's definitions (H-1, H-2) before an owner decides on predictive regulation [fix lane].
- Lane C's adversarial front-door cases and the sampled-trap test staged with stated moments [fix lane].
- `strategies.outcomes` is slow at scale (5.8 s, two unindexed look-ups per task; an index is a migration).
- Coverage is limited by the allowance: a share of the allowance for reaching uncovered subjects would have to be
  chosen on development and tested on worlds no lane has seen. Not built.
- Everything above is offline and simulated: nothing here was measured on the open web, with a live answer model, or
  on the host's real memory over a week.

## 6. Backing each piece out

| Piece | How | Data |
|---|---|---|
| the two source rules (already off [fix lane]) | `nsf web config --db DB --set attribution_independence=false --set echo_recognition=false` | nothing stored by them |
| supersession (off) | `nsf web config --db DB --set 'supersession="ask"'`; each replacement: `nsf web revert ID` | its replacements stay listed |
| `recheck_order` (default) | `nsf web config --db DB --set 'recheck_order="staleness"'` | none |
| shadow | `nsf config --no-predictive-regulation --db DB`; delete `<db>.resources/forecast-shadow*` to discard | numbers only |
| the front door | `nsf imagine scm FILE --no-front-door`; revert lane C's merge `5701bbe` | nothing stored |
| the idle task-count fix | revert `41198b3` (lane V) | none |
| Pocket / console redraw | revert `81edd00` (lane P) | none |
| the reconciliation's guards | revert `ce5a432` (the 12 fault cases fail again) | none |
| whole lanes | revert the merges: P `0b264c6`, C `5701bbe`, H `7dafc33`, R `f56f9cb`, S `60f062e`, V `f0a1f09`; step 0 `a0717c1` | none (no schema change) |

## The rule for the withheld looks (written before them)

<<WAVE3-LOOK-RULE>>

## Reviews and fixes

Two independent reviews read the merged tree `f0a1f09` read-only, without loading any withheld file. The measurement
review: 9 high (lane S's evidence, S-1 to S-6; the look rule, two; the front-door look's command), 11 medium, 20 low.
The security review: 0 critical, 1 high (H1), 6 medium (M1–M6), 8 low. Their findings that change this record are in
§2, §3 and §5. The main session's decisions: both source rules ship off; the look rule is rewritten; the fix lane
(`gen3-fix`) implements the findings.

<<WAVE3-REVIEWS>>

## The withheld looks

<<WAVE3-LOOKS>>

## Validation

The reconciliation's checks (branch `gen3-rec`) are in its report; the final validation is recorded here.

<<WAVE3-VALIDATION>>
