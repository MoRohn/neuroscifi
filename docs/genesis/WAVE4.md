# Wave 4: where a value came from — derivation, the individual's own answers, the day's allowance, a caution for the owner

Status: 2026-11-26. Wave 4 of Project Cognitive Genesis. Step 0 (`gen4-0`) wrote worlds for source independence and
took one look at them with the unchanged system; lanes D, E and S were merged on `genesis-wave4` (`0b3e35e`,
`97f7abc`, `23243b1`), wired together by the integration lane (`gen4-int`, `d4931c0`); two independent reviews read
that tree (security, measurement); the fix lane (`gen4-fix`) acted on their findings under the main session's rulings
and merged lane A (`9ba3263`); this record is written on `3032cf6`; the second fix round (`gen4-fix2`) acted on the
reviews' second passes ("Reviews and fixes"). Sources: ADR-064 to ADR-067 in
[ARCHITECTURAL_DECISIONS.md](../architecture/ARCHITECTURAL_DECISIONS.md), the Wave 4 sections of
[COGNITIVE_BENCHMARK.md](../quality/COGNITIVE_BENCHMARK.md), the evidence files named beside each number, the lanes'
reports and the two reviews ("Reviews and fixes", below). Nothing here is a claim of consciousness, awareness or
general intelligence; the names describe functions.

## On one screen, in plain words

**The hole Wave 3 left open is still open at the shipped defaults.** The individual cannot tell independent sources
from copies, or from its own words coming back: a reworded copy of a story counts as an independent source, a page
repeating the individual's own answer reads as corroboration, and a page naming two honest outlets as its origins
joins them into one. Wave 4 went after the cause instead of the wording. Step 0 first wrote worlds whose ground truth
is which page copied which (every known failure shape, thirteen in all), before any rule, and measured the unchanged
system on them: on held-out worlds it admits 127 knowledge rows whose independent support is below its own bar (40 of
them false) and joins only a fifth of the page pairs that truly share a source (`genesis_sources_baseline.json`); on
worlds no lane may read, 110 such rows and 129 of 356 well-supported true values not held (step 0's one look,
COGNITIVE_BENCHMARK.md).

**Result: no rule is supported, and none ships on.**
- **The derivation rule** (`source_derivation`, lane S, ADR-066). Its only signal turned out to be a convention of
  the test worlds' generator: a capitalised name that equals a host's site label (31 of the 34 named parties and 10 of
  the 10 outlets are written that way). And a single page could merge honest outlets into one source; the cheap
  hardening the fix lane added still leaves a page that every honest outlet links or names able to do it. Measured,
  it admits fewer badly supported rows (held-out 127 → 99) and pays with more true values not held (142 → 207 of 612)
  and a lower `correct` in every world (−0.064 / −0.054 / −0.101). **Recorded verdict: "not supported; do not turn
  on".**
- **The own-text rule** (`own_text_provenance`, lane E, ADR-067) recognises the individual's own answers coming back,
  by a record of what it said and when, not by wording: own answers admitted 4 → 1 on held-out, 30 of 31 echoes of
  unsourced answers set aside. Two reviews found that it **let such echoes replace a verified value** (an echo that
  disagreed with a held item was kept whole and counted for its value) and **let any scope poison a subject** (a
  family device's or an agent's answer that only mentioned or denied a version stopped the owner ever learning it).
  The first is fixed: an echo is no evidence for or against, and the disagreement is asked. The second is partly
  fixed: only the owner's answers are recorded, only asserting sentences, for 30 days; but the security review's second
  pass found the assertion reader still records 9 of another 18 owner phrasings, denials in other words among them
  (the fix lane's own probe: 3 of 18), and that the 30-day expiry opened a new defect, an expired first emission of a
  value hiding a fresh one (fixed in the second fix round). It **stays off**, with limits that remain: its value
  normaliser undoes exactly the reformats the worlds' generator writes, and every world page is dated, so the false
  rejection of undated honest pages is not measured at all; its "effective" verdict holds at zero margin in one
  held-out world.
- **Dividing the day's allowance** between refreshing and reaching (`allowance_division`, lane A, ADR-064) shows no
  gain beyond the spread in any held-out world and is "unsafe" or "harm" under the rule written before the run; it
  stays off. Tracing why its runs were noisy found **two tie-breaks in the product that followed random task ids**,
  now fixed. That weakens one of Wave 3's three `idle-first` figures on the withheld worlds: **W2's +0.042 exceeds its
  spread (0.039) by only 0.003, less than the 0.015 a world's mean moved between repeats of that code, so "beyond the
  spread in W2" is not established**; W3 (+0.052 against 0.035, margin 0.017, only 0.002 more than that swing) is
  not shown to fail and not established (not verifiable without the withheld worlds); W1 (+0.013) was within its
  spread either way. Wave 3's "beyond the spread in two worlds" becomes "in one at most, W3, unverified".

**What ships and changes behaviour at the shipped defaults:**
- **Damaged saved settings fail closed and say how to repair them.** Every saved web, instance and voice setting is
  checked when read (lane D). A damaged network limit (blocked domains, private terms, the per-domain interval, the
  budget ceiling, the document size) turns web access off and the refusal names `nsf web config --set NAME=VALUE`;
  a damaged setting fixed at `nsf init` (evidence lifetime, row cap, free-space reserve, thermal limit) pauses writes
  until `nsf config --repair-fixed NAME=VALUE` (the fix lane: lane D's first version read these as their loosest
  defaults, a regression from `main` the security review found); other damaged values read as their cautious value
  or default, logged once by name and listed by `nsf config`, `nsf web config` and the console.
- **`requeue` honours the day's autonomous task count**, checked inside its write transaction (the owner's own tasks
  are never counted).
- **A checkpoint restore scans the answers before its write transaction** (0.14–0.15 s inside it at 10,000 answers of
  20 kB, against 1.64–1.82 s).
- **The two tie-breaks** (`autonomy._lineage`, `curiosity`'s two claim queries) follow creation order: runs with the
  idle investigation are now repeatable. Timer-only runs did not move (594 runs, only database file sizes differ).
- **A display-only caution, "Only one source says this."**, where an answer, a verdict or a knowledge item rests on a
  single page (console, `nsf web show` / `ask` / `knowledge`, Pocket, the voice reply). It decides nothing.
- New owner commands: `nsf config --repair-fixed NAME=VALUE`, `nsf web config --forget-own-text`. Benchmark
  harnesses write summaries by default (`--full-history` for everything).
- Since the second fix round: an agent (MCP) whose call is refused because a setting is damaged is told only that the
  individual is paused for repair by its owner (the owner's surfaces keep the full sentence); `--repair-fixed` says
  that the value chosen at creation is recorded nowhere and logs the repair; `nsf web config` says the recorded
  verdict when `source_derivation` or `own_text_provenance` is set on (and still applies it); `--forget-own-text` is
  recorded so that a restore of an older checkpoint replays it.
- Built and off, each the owner's opt-in: `source_derivation` (not recommended), `own_text_provenance`,
  `allowance_division` (`"race"`), and `caution_detail` (shows two more caution triggers that the measurement found
  misleading about three times in ten).

**The hole stays open at the shipped defaults.** No default that decides what counts as a source changed in Wave 4.

## Why Wave 4 exists

Wave 3's withheld look found that on unseen worlds versions only the individual itself had stated (16) and versions
only a copied wrong story stated (31) became knowledge, and that its two phrase-reading rules did not close this: a
rule that reads the wording of a credit sees only the wordings its author wrote ([WAVE3.md](WAVE3.md)). Wave 4 went
after the cause: which page published a value first and which pages derive from it, and what the individual itself
said and when. Its rules (COMMON, all from Wave 3's review findings): the worlds come first and must contain every
known failure shape; a world's generator and the rule it tests share no phrase list, pattern or convention; both
directions are measured for every rule and shape (wrongly admitted content and true facts not held); a change to what
counts as a source ships off and its default path must be shown identical to today's; the verdict rules are committed
before held-out. One lane (A) followed Wave 3's other open direction, the share of the allowance for subjects the
loop never reaches; one (D) closed housekeeping the Wave 3 reviews left open and gave the owner a caution.

## Step 0: worlds for source independence, the baseline, one look

Committed alone and in order (`gen4-0`, merged `42fd136`): development worlds `sources_splits_dev.py` (`cda7271`;
seeds 8301–8303, two worlds), held-out `sources_splits_heldout.py` (`6480b12`; 8401–8404, three worlds), withheld
`sources_withheld.py` (`5ce7868`; 8501–8504, read by no lane), then the harness `acceptance/cognition/sources.py`,
`scripts/bench_sources.py`, the guard and the look rule (`cd99288`). Fictional projects, outlets, wires,
aggregators, round-ups, forums and release pages under `.example`; each look stages one of thirteen shapes (honest
independent corroboration, release-page citation, honest outlets that checked the value and still credit another,
syndicated copies with and without a credit, two origins, an extra contributor, hosts named like wires, round-ups,
mirrors, corrections, stale pages, echoes of the individual's own answer). Credit and echo wording comes from
documented style guides and credit lines, not from any parser (COGNITIVE_BENCHMARK.md, "Wave 4: the
source-independence family"). Metrics fixed before any rule: `wrongly_admitted` (a row whose value's independent
first-hand support in the look is below the bar of 2 roots), `own_admitted`, `suppressed` of `eligible` (a
well-supported true value not held), `dependent_rises`, grouping precision and recall of "same source", `correct`;
references from the graph alone (`oracle`, `every_page`, `same_value`, `todays_rule`).

**The baseline** (`genesis_sources_baseline.json`, 463 kB, measured on `cd99288`; recomputed for this record from the
JSON): held-out, 3 worlds × 4 seeds, 864 looks; development, 2 worlds × 3 seeds, 360 looks.

| held-out, summed | wrongly admitted (false) | own answers admitted | suppressed of eligible | precision, recall |
|---|---|---|---|---|
| shipped | 127 (40) | 4 | 142 of 612 | 0.81, 0.20 |
| `attribution_independence` (Wave 3, off) | 118 (37) | 4 | 153 | 0.81, 0.27 |
| `echo_recognition` (Wave 3, off) | 123 (37) | 1 | 145 | 0.81, 0.20 |
| `origin_grouping` "one_way" (Wave 3, off) | 127 (40) | 4 | 142 | identical: not exercised |
| oracle / todays_rule / every_page | 0 / 232 / 287 | 0 / 13 / 14 | 4 / 82 / 70 | |

Development: shipped 64 (21) / 4 / 39 of 233; `correct` shipped held-out 0.601 ± 0.064, 0.627 ± 0.064, 0.656 ± 0.073
(oracle 0.713 / 0.718 / 0.709). Every wrongly admitted row comes from four shapes (credited copies 61, uncredited
copies 36, copies with an extra contributor 26, echoes 4); the shipped grouping joins 6 % of truly same-source pairs
among credited copies and none among round-ups or release-page citations, and wrongly joins honest outlets that wrote
the same short sentence (53 false pairs). Suppression in wire-like hosts, corrections, stale pages and mirrors comes
with almost no false merge: a source rule cannot remove it.

**The one look** (ledger line 8 of `genesis_final_uses.jsonl`, clean tree `d10927f`, `shipped` and the four
references only; evidence `genesis_sources_withheld_baseline.json`, 234 kB, which only the main session reads; the
figures here are COGNITIVE_BENCHMARK.md's and were not recomputed, since this lane may not open that file): 110 rows
admitted below the bar (26 false now), 2 own answers, 129 of 356 well-supported true values not held (66 of those
looks contested), grouping precision 0.75 / 0.84 and recall 0.19 / 0.25, `correct` 0.535 ± 0.042 and 0.489 ± 0.062
against the oracle's 0.610 and 0.657, dependent confidence rises 0. It decided nothing.

## 1. What each lane built

| Lane | Built | Setting: default | ADR | Evidence |
|---|---|---|---|---|
| 0 (step 0) | the source-independence worlds (development, held-out, withheld), harness, metrics, references, guard, baseline, one look | no mechanism | — | `genesis_sources_baseline.json` (463 kB); the look's file (main session only) |
| S | one source by evidence of derivation: a page naming or linking another page read with it joins that page's group | `source_derivation`: off | ADR-066 | `genesis_wave4_s.json` (296 kB), `genesis_wave4_s_assess_development.json` (18 kB) |
| E | own-text provenance: a record of the owner's answers' (subject, value) pairs at emission; a page repeating an unsourced one is no evidence | `own_text_provenance`: off | ADR-067 | `genesis_wave4_e.json` (234 kB) |
| D | settings checked on load; `requeue` and the day's task count; the restore's scan before its transaction; harness summaries; the caution | always (fixes); caution display-only, `caution_detail`: off | ADR-065 | `genesis_wave4_d_caution.json` (10 kB) |
| A | a division of the day's autonomous allowance between refreshing and reaching; the tie-breaks | `allowance_division`: `"race"`, `reach_share` 0.25; the tie-breaks always | ADR-064 | `genesis_wave4_a_{dev,held_out,default_identity,cost,repeatability}.json` (384, 437, 6, 3, 49 kB) |
| int | lane E's check wired to lane S's echo hook when both are on; the configuration `wave4:both` | — | — | the lane's report; re-measured in `genesis_wave4_fix.json` |
| fix | the reviews' findings under the main session's rulings; lane A merged | `caution_detail` (new, off) | amends ADR-064 to ADR-067 | `genesis_wave4_fix.json` (92 kB) |

No schema change (version 19), no migration, no new background activity, no dependency. New evidence: about 2.0 MB
in the eleven files above, plus the look's 234 kB; the harnesses now write summaries, so Wave 3's 45 MB did not
repeat.

### 1.1 Lane S: `source_derivation` (ADR-066)

**What was looked for.** Evidence of derivation that does not depend on wording, scored on development against the
graph (`scripts/assess_derivation_evidence.py`, committed with its summary before the rule): the same stated
publication time (604 of 627 same-source pairs flagged, but 294 independent pairs too: copies and honest outlets carry
the same times here); order-preserving text overlap (AUC 0.33, below chance); a shared rare word pair; a page naming
another read page's outlet or linking to it (101 same-source pairs, 22 independent, every one an honest outlet that
checked the value and credits who reported first). Only the last was kept: the rule's only evidence.

**The rule.** Today's grouping, then only joins: a group moves when every page in it names another page read in the
task, by ONE edge chosen from content (order-free), never to a group that itself names another, never to a page
stated as published later. Stored: normalised names only (`flags["derivation"]["cites"]`, at most 16 per page; never
a host, URL or text). The bar counts a joined group once and is never lowered. Since the fix lane: a page the
injection screen flagged, or that yields no claim, is never named or linked; the subject's own name and calendar
words are no site label; stored marks count only while the setting is on.

**Both directions** (held-out, 3 worlds × 4 seeds; lane S's run on `8e46b98`, then the fix lane's re-run on `842ef8b`
in brackets; `genesis_wave4_s.json`, `genesis_wave4_fix.json`, both recomputed for this record):

| | shipped | `source_derivation` |
|---|---|---|
| wrongly admitted (false now) | 127 (40) | 97 (30) [99 (31)] |
| suppressed of eligible | 142 of 612 | 207 of 612 [207] |
| own answers admitted | 4 | 4 [4] |
| grouping precision, recall | 0.807, 0.203 | 0.778, 0.353 [0.777, 0.350] |
| `correct`, paired against shipped (seeds worse of 4) | 0.601 ± 0.064 / 0.627 ± 0.064 / 0.656 ± 0.073 | −0.066 (4) / −0.054 (3) / −0.101 (4) [−0.064 (4) / −0.054 (3) / −0.101 (4)] |

Per shape (lane S's run): credited copies wrongly admitted 61 → 39, copies with an extra contributor 26 → 18, copies
crediting nobody 36 → 36 and echoes 4 → 4 (unseen); suppressed in `verified_credit` (honest outlets that credit
another) 4 → 42 of 55 and in round-ups 5 → 22 of 53. Development: 64 (21) → 54 (17) [55 (17)], suppressed 39 → 65 of
233, `correct` −0.083 / −0.093 (every seed worse). Verdict under `sources.verdict_derivation` (committed before
held-out): **"trade"** on both splits, before and after the fix (A fewer false values admitted: holds; B suppression
not higher, C `correct` within the spread, D precision within 0.02: fail).

**What it cannot see** (held-out rows still admitted over rows admitted at shipped, a ratio of counts, not of the same
rows; lane S's run): credited copies 64 % (67 % after the fix lane's hardening, 41 of 61), extra contributor 69 %,
uncredited copies 100 %, echoes 100 %. The name census's "seen"
shares (credited copies 32 %) are an upper bound set by the generator.

**The record after the reviews: not supported; do not turn on.** (1) The signal is the generator's convention
(31 of 34 cast names, 10 of 10 outlets equal their host's label; the rule's own comment used the generator's example,
since removed); (2) 16 of its 17 extra round-up suppressions are looks with exactly one first-hand page read, where
the frozen metric credits the round-up with roots never read, so part of the "suppression" is the rule declining one
page's word about unread outlets; the 38 extra in `verified_credit` are real joins of honest outlets; (3) before the
fix any page whose host label equalled the subject's name or a month, stating nothing or flagged by the injection
screen, merged every honest outlet (security H1); after it, a claim-bearing page every honest outlet links or names
still does.

**Default path.** `scripts/compare_source_derivation.py` runs `42fd136`'s `claims.py` beside this tree's on every
input of the source, F, S, S-variant and R worlds, the reviewers' scenarios and the offline benchmark: 2,052,099
comparisons, 0 different, on lane S's final code and again on the fix lane's tree. These are highly correlated
comparisons of `claims.py` only, at default arguments outside the offline part (review4 measurement L3); the check can
fail (an injected regression: 619 different on one world; kept as a test). Cost: +0.19 ms per look outside any
transaction; grouping 0.36 ms per look inside consolidation's transaction, reading no row.

### 1.2 Lane E: `own_text_provenance` (ADR-067)

**What is recorded.** When a model-written answer of an owner's task is stored (console, CLI, voice, the owner's
refresh; since the fix lane, no family device, agent or autonomous task), each asserting part of each sentence (no
question, negation, hedge or condition, since the fix lane) is read for (subject name, value) pairs, normalised by the
product's own value parser; for each pair, its time and whether a claim read by then states it (sourced / not / not
known). Salted digests in `web_strategies` rows (no migration; primary-key reads only; never the text or a source).
At most 200,000 fingerprint rows, about 44 MB at the cap, in every checkpoint (review4 L2).

**The rule.** A page's claim is an echo when the earliest emission of one of its pairs made in the 30 days before the
claim was read was unsourced, and the page states no earlier publication date (since the second fix round an expired
emission never hides a newer one). **An echo is no
evidence, for or against** (since the fix lane): it leaves the supporting side, supersession never counts it, a held
item it disagrees with is neither superseded nor weakened, and the disagreement is asked as a contradiction question.
Point lookups before the write transaction (at most 512 per consolidation).

**Both directions** (`genesis_wave4_e.json` on `04a64b5`, then the fix lane's re-run `genesis_wave4_fix.json` on
`842ef8b`; pooled over worlds and seeds, not a claim; recomputed for this record):

| | development (360 looks) shipped → rule [after fix] | held-out (864 looks) shipped → rule [after fix] |
|---|---|---|
| own answers admitted | 4 → 2 [2] | 4 → 1 [1] |
| wrongly admitted (false) | 64 (21) → 60 (20) [60 (20)] | 127 (40) → 123 (37) [123 (37)] |
| suppressed of eligible | 39 → 39 of 233 [39] | 142 → 144 of 612 [145] |
| echo pages set aside, of echo pages read | 18 of 29 [18] | 30 of 41 [30] |
| honest pages set aside, of honest pages read | 7 of 1,091 [8] | 9 of 2,670 [14] |
| grouping | unchanged | unchanged |
| `correct`, paired | 0 / −0.011 | −0.003 / +0.002 / 0 [−0.004 / +0.002 / 0], every change inside the spread |

Of the echo pages: those repeating an UNSOURCED answer, 18 of 18 and 30 of 31 set aside; those repeating a sourced
answer, 0 of 11 and 0 of 10 (by design; they are all of the own answers still admitted). The 5 more honest pages on
held-out after the fix are pages that had escaped through the defect the reviews found (review4 measurement H1): "9 of
2,670" undercounted. **The false rejection:** every honest page set aside repeats a value the individual had stated
wrong and unsourced that later became true and was then reported; after the fix lane, 14 pages on 6 looks on
held-out cost 3 eligible looks (suppressed 142 → 145: +2 in src-ho-mixed, +1 in src-ho-copies; the 2 looks added by
the fix, Quororra and Wexeth, diagnosed by the second measurement pass), 8 pages and no look on development; lane E's
run had 9 pages and 2 looks. Verdict under `sources.own_verdict` (committed before held-out): **"effective"** on both splits, before
and after the fix. Wave 3's `echo_recognition` under the same rule: also "effective", with 13 honest pages set aside
on held-out. **What "effective" means after the fix** (review4 measurement, second pass): S1 allows each world 2 more
suppressed looks, and src-ho-mixed is now 56 → 58, exactly at the limit (it was +1): one more false rejection there
would make it a "trade". The verdict does not count questions: the new disagreement path asks 1 question on
development and 2 on held-out, and every one is raised by honest pages (the false rejections Quororra and Wexeth), with
a wording ("repeats a value that only this system's own earlier answer had stated") that is false for them; no real
echo page reached that path in the worlds, so the fixed behaviour is shown only by staged cases. And the worlds do not
exercise the fix lane's restrictions: every answer there is an owner's plain assertion made 1 to 11 days before its
echoes. Since the fix, Wave 3's `echo_recognition` also sends a disagreeing echo to that path (held-out
`suppressed_contested` 71 → 73); a note in ADR-061 now says so.

**What it cannot see:** echoes of an answer that had a source; an echo of a value only imagined (Wave 3 lane S's
worlds plant own values there, so they barely exercise it: own-only versions 5 / 2 / 13 → 5 / 2 / 11 on its held-out);
backdated pages; another system's output; anything emitted while off; answers of a family device, an agent or an
autonomous task (since the fix lane); mentions and denials in other words are read as assertions (the fix lane's
probe: 3 of 18 staged forms; the security review's second pass, re-run on the second fix round's tree: 9 of another 18
owner phrasings, six mentions such as "is rumoured for next month" and "The roadmap lists X 9.4.0" and three denials,
"Some blogs claim X 9.4.0 is out, but no release exists", "Nobody has seen X 9.4.0", "is fake news"; an owner answer
"X 9.4.0 is rumoured for next month" followed by the release 20 or 29 days later left 9.4.0 unheld, with no question
when nothing was held); an emission older than 30 days (before the second fix round an expired first emission of a
pair also hid a fresh one, failing open; fixed); an adversary's small edit (a lower-case name,
"Velmora-9.4.0", a soft hyphen, full-width dots: it catches accidental echoes only); `nsf ask` replies and free voice
conversation. Two conventions of the generator: the echo shape's reformatted numbers are exactly the forms the
normaliser undoes (lane E read the development generator before writing it), and every world page is dated while the
rule treats an undated page as an echo, so **the false rejection of undated honest pages is not measured** ("0.34 %
of honest pages" holds only for dated ones).

**Why off.** Small counts (4 own answers at shipped per split), a measured false rejection, and changing what counts
as a source is the owner's decision. Cost (10,000 tasks, 200,000 claims): the emission's read 0.39 s median before
the write transaction; the consolidation check of 64 claims 0.9 ms; nothing scans inside a transaction.

### 1.3 The two together: `wave4:both` (integration lane)

When both settings are on, lane E's check marks echo pages at extraction through lane S's hook. Measured
(`genesis_wave4_fix.json`, after the fix; recomputed): held-out 96 (29) wrongly admitted, 1 own answer, 209 of 612
suppressed, echo pages set aside 30, honest pages 16, precision 0.778, recall 0.348, `correct` −0.067 / −0.056 /
−0.101 (seeds worse 4 / 3 / 4); development 51 (16) / 2 / 64 of 233 / 18 / 8. No verdict rule was written for the
pair: under lane S's rule it is a "trade", under lane E's "unsafe" (the grouping and its costs are lane S's); **nothing
is claimed for it.** The integration lane's counts (echo 24 / 15, honest 12 / 7) missed the pages marked at
extraction (review4 measurement M1); the fixed harness counts them. With both on, an echo that disagreed with a held
item opened no question and a stale held value could no longer be challenged (security H4); fixed: it is asked.
Retention and adversarial families (60 of 60) passed in all four configurations on `d4931c0` (the integration lane;
not re-run after the fix lane). No verdict rule was pre-registered for the pair; it inherits "not supported; do not
turn on" from the derivation rule and is not looked at on the withheld worlds (ADR-067).

### 1.4 Lane A: dividing the day's allowance (ADR-064)

**Worlds.** `cycles_reach_splits.py` (committed alone, `f802ba4`; development 6401–6403, held-out 6501–6504): lane S's
Wave 3 generator plus subjects whose owner research finds one origin only, so they hold nothing; kinds `reach`,
`covered` (a split favouring reaching should lose), `mixed`, `shift`. They do reproduce the holes: as shipped with the
idle investigation, held-out admits 46 wrong rows (6 own-output, 19 wrong-story).

**Diagnosis** (development): the timer's pass used the whole allowance in 8.0–9.7 of 12 cycles; per look reaching
made more subjects correct than refreshing, but it runs out of admitted candidates after the first cycles, so shares
0.5 and 0.75 behave identically in three of four worlds.

**The mechanism.** `allowance_division` `"reserve"` holds round(tasks × `reach_share`) places for reaching while the
admission rule admits a reaching candidate (released at once otherwise); `"yield"` sets the share from each path's
7-day rate of tasks that wrote knowledge. No new budget; the cap is checked as before.

**Held-out** (4 seeds, run once on `78540a0`; paired gain against `shipped+idle`, seeds better of 4; recomputed from
`genesis_wave4_a_held_out.json`):

| | rh-reach | rh-covered | rh-mixed | rh-shift |
|---|---|---|---|---|
| `shipped+idle` (sd = smallest visible gain) | 0.612 (0.054) | 0.461 (0.051) | 0.497 (0.037) | 0.560 (0.034) |
| reference: never refreshed / blind round-robin / oracle | 0.671 / 0.776 / 0.980 | 0.430 / 0.555 / 0.729 | 0.586 / 0.757 / 0.971 | 0.628 / 0.763 / 0.972 |
| `reserve-0.25` | +0.003 (2) | −0.019 (1) | +0.010 (2) | +0.004 (2) |
| `reserve-0.5` | −0.001 (3) | −0.019 (1) | +0.005 (2) | +0.026 (4) |
| `reserve-0.75` | −0.001 (3) | −0.024 (1) | +0.005 (2) | +0.026 (4) |
| `yield` | −0.011 (1) | −0.016 (1) | +0.023 (3) | +0.024 (4) |
| `shipped+idle-first` (the other extreme) | +0.006 (2) | −0.022 (1) | −0.023 (1) | +0.044 (4) |

Verdicts (rule committed in `ea83927` before held-out): `reserve-0.25` **harm** (retention), the others **unsafe**
(rh-mixed `admitted.wrong` 14 / 14 / 15 against 12). No gain beyond the spread in any world; development chose
nothing. The other direction, pooled over 16 runs (own-output / wrong-story / all wrong rows): `shipped+idle` 6 / 19 /
46, `reserve-0.25` 3 / 11 / 38, `reserve-0.5` 6 / 9 / 35, `reserve-0.75` 6 / 7 / 32, `yield` 6 / 14 / 41: fewer
pooled, more in rh-mixed, which is what the rule reads. The reference and every candidate stay 0.09–0.26 below the
blind round-robin (each world's mean; `shipped+idle-first` is up to 0.28 below, the product without the idle
investigation up to 0.32; `genesis_wave4_a_held_out.json` `gap_to`). Cost on a large individual: `"reserve"` 0.40 s per call, `"yield"` 1.5 s, an idle decision 0.45 →
0.83 s, all outside write transactions; `"race"` reads nothing.

**Repeatability** (`genesis_wave4_a_repeatability.json`; ADR-064 "Follow-up"). The default-path comparison found 235
differing values in 8 `idle-first` runs that varied on the UNCHANGED tree. Cause, in the product: `autonomy._lineage`
walked a set of task ids (uuid4), and `curiosity.search_terms` / `rank` broke ties among claims stored at one instant
by task id. Fixed by creation order (`805fa0e`); test
`tests/acceptance/test_learning_cycles_division.py::test_the_same_history_gives_the_same_run_whatever_the_task_ids`.
Before the fix, 10 repeats per world of 18 public worlds: `baseline` and `shipped+idle` never varied; `idle-first`
varied in 4 of 18 worlds, a world's mean paired gain by up to 0.015 (rh-mixed, +0.0366 to +0.0513; recomputed) and
0.007 in rd-covered; lane V's held-out figures (ADR-062) reproduce exactly. After it, 0 variation. Re-run after the
fix: every candidate "unsafe", nothing chosen. **For Wave 3's withheld look** (single runs on worlds that cannot be
re-run here): `idle-first` W2 +0.042 against its spread 0.039, a margin of 0.003, below the largest swing measured:
"beyond the spread in W2" is not established; W3 +0.052 against 0.035 (margin 0.017) is 0.002 above the largest
swing, which comes from only the 4 public worlds that varied at all (a single seed swung by 0.056): not shown to fail,
not established, unverified on those worlds (review4 measurement, second pass; WAVE3.md says "likely stands", stronger
than this; FINAL_EVALUATION.md's merged correction said "holds" and was corrected in the second fix round); `shipped+idle` did not vary anywhere. The
correction is written into WAVE3.md, FINAL_EVALUATION.md and COGNITIVE_BENCHMARK.md. It does not explain Wave 3's
untraced seed-2704 move. Not compared field by field after the fix: the views and the family that read
`curiosity.rank` and `search_terms` at the defaults (the console overview's curiosity list, `nsf web curiosity`, the
cognitive benchmark's `infovalue` family; covered only by `nsf acceptance --all`'s pass); where claims tie on their
time, they now show the claim stored last.

### 1.5 Lane D: settings, budgets, the restore, evidence size, the caution (ADR-065)

- **Settings checked on load** (`d2f23f0`, `03bf1ac`, `268c433`; fail-closed fixed settings by the fix lane,
  `e36d4ee`, `3d99b91`). Every `WebConfig` field is in `CHECKED_ON_LOAD`; a damaged value reads as its cautious value
  (`config.CAUTIOUS`: allowances of unasked work 0, nothing provisional by itself, …) or its default; `FAILS_CLOSED`
  network limits turn web access off and the refusal names the repair. Instance: `ask_budget_per_epoch` damaged → 0;
  the four settings fixed at `nsf init` damaged → writes (and checkpoints, for `min_free_bytes`) paused with a
  sentence naming `nsf config --repair-fixed`. Voice: damaged consent `{}`, audio lifetime 1 h, a non-owner's damaged
  preferences ignored. A web row that is not a JSON object reads as every setting damaged. Cost: `WebConfig.from_json`
  30 → 83 µs. Tests: `tests/acceptance/test_wave4_d.py` (every setting × 19 junk values), `test_wave4_fix.py`.
- **`requeue`** (`ec33fe0`): a failed autonomous task queued again, or a child keeping an autonomous origin, is checked
  against the day's count inside the transaction that queues it.
- **The checkpoint restore** (`b905024`): the mark's scan of every answer runs before the write transaction; inside it,
  0.146 / 0.138 s against 1.64 / 1.82 s at 10,000 answers of 20 kB (the whole restore 1.96–2.13 s either way).
- **`strategies.outcomes`**: already fast since Wave 3's fix lane (median 0.0091 s at lane H's scale); no index needed.
- **Evidence size** (`da23d5e`): the learning-cycle harnesses write summaries by default; Wave 3's 21 files (44.90 MB)
  are kept (recommended; compressing or moving them shrinks the working tree, never the clone).
- **The caution** (`cfc1511`, `dd516c3`; amended by the fix lane `29c69a1`, `5341de6`). Computed when a view is drawn,
  never stored, imported only by the views (a test scans `src`): how many pages support a statement, how many sources
  they count as under today's rule, why pages were counted together, and whether a page repeats an earlier answer of
  the individual's own (owner views only). Measured on the selective family (`genesis_wave4_d_caution.json`, rule
  committed before the run; about twenty items per split, a description, not a claim): development 21 items, 3 wrong;
  the caution as first built was shown on 8 and carried all 3 wrong answers (3 of 8 against 0 of 13 without it);
  held-out 20 items, none wrong: **not exercised** (shown on 11 right answers). All 3 wrong answers carried the
  one-source trigger; the copy trigger fired 3 times, each on a right answer, and the own-answer flag never fired. On
  the source-independence worlds the copy trigger sat on truly independent pages in 79 of 264 cases (held-out) and the
  own-answer flag had no echo page in 7 of 22 (review4 M4). **So by default only "Only one source says this." (a
  single page) is shown**; the other two are computed and shown only with `caution_detail` (off). Under that default,
  recomputed from the evidence's rows (not re-run): development shown on 7, 3 of them wrong, against 0 of 14 without;
  held-out shown on 9 right answers.

## 2. The defaults, with the evidence for each

| Setting | Default | Why |
|---|---|---|
| `source_derivation` | **off** | "trade" under its own rule on both splits; the signal is the generator's naming convention; a single page can still merge honest outlets: "not supported; do not turn on" (ADR-066) |
| `own_text_provenance` | **off** | "effective" under its own rule on both splits, but small counts, a measured false rejection (true values stated wrongly first), limits unmeasured (undated pages), and changing what counts as a source is the owner's decision (ADR-067) |
| `allowance_division`, `reach_share` | `"race"`, 0.25 | no candidate's gain beyond the spread; every candidate "unsafe" or "harm" (ADR-064) |
| `caution_detail` | off | the copy trigger and the own-answer flag are wrong about three times in ten on the worlds measured (ADR-065) |
| the caution "Only one source says this." | shown (display only) | the one trigger the measurement supports, on development only |
| settings checked on load; fixed settings and network limits fail closed | always | fixes (the fixed settings restore `main`'s closed behaviour) |
| `requeue` and the day's count; the restore's scan; the tie-breaks | always | fixes |
| Wave 3's settings (`attribution_independence`, `echo_recognition`, `supersession`, `origin_grouping`, `recheck_order`) | unchanged (off / `"ask"` / `"transitive"` / `"staleness"`) | WAVE3.md §2 |

## 3. Disclosures

- **Indirect import.** Importing `neuroscifi.acceptance.cognition`, as every harness does, imports `withheld.py`;
  nothing of it is used. No lane, reviewer or this documentation lane opened, printed or ran a withheld file or passed
  `--final` / ran a `final` subcommand, except step 0's one look (ledger line 8).
- **Bytes hashed.** `tests/acceptance/test_sources_bench.py`'s frozen-files test hashes `sources_withheld.py`'s bytes;
  lane S ran that file whole three times, the integration lane once (nothing read or printed).
- **Collected.** Step 0's first full-suite run started without deselecting the two withheld-byte tests and was
  stopped within seconds (whether they ran is not known); the fix lane's first full-suite run collected
  `tests/acceptance/test_learning_cycles_withheld.py` (pytest imported it) before it was stopped.
- **Archives.** Lane A (`f802ba4`), the integration lane (`97f7abc`) and the fix lane (`main`, `d4931c0`) extracted
  `git archive` trees that contain the withheld files byte for byte; none was opened.
- **What the rule authors knew.** Step 0's author read `config.py`'s comment listing credit forms after the worlds
  were committed, and ADR-063's `_WIRE_SITES` and ADR-061's quoted phrasings before (the required reading). Step 0
  published held-out per-shape baselines before lanes S and E designed their rules: the held-out failure profile was
  known to them (review4 measurement, notes). Lane E read the development generator before writing its normaliser.
  Held-out was written by the same author with the same generator as development: weakly independent.
- **Runs not in the evidence.** Lane E's first held-out run (on `bf35870`, after its verdict rule) is not recorded;
  the committed run on `04a64b5` gives the same numbers. Lane S's held-out run is recorded as `8e46b98` but had two
  uncommitted edits to the learning-cycles harness, which the source family does not use (runs now record `dirty`).
  Lane A ran held-out a second time (repeatability, not committed) and a third after the tie-break fix (verdicts in
  the repeatability file).
- **This record's reads.** The documentation lane read the ledger's `commit`, `dirty` and `at` fields only, and listed
  the evidence directory with withheld names filtered out; the step-0 look's figures above are COGNITIVE_BENCHMARK.md's.

## Reviews and fixes

Two independent reviews read the integrated tree `d4931c0` (`main..genesis-wave4`), read-only, each with probes that
reproduce its findings (`review4/s/`, `review4/m/`): **security** (rev4-s: 4 high H1–H4, 1 medium M1, 3 low L1–L3, a
note N1; risk MEDIUM at the defaults, HIGH for anyone turning a Wave 4 rule on; "may merge after the listed fixes")
and **measurement** (rev4-m: 1 high H1, 4 medium M1–M4, 4 low L1–L4; "may merge after the fixes"; every lane figure
recomputed and reproduced exactly; the default-path proof has power). The fix lane (`gen4-fix`, `e36d4ee` to
`3032cf6`) acted on both under the main session's rulings: the one default-path defect restores `main`'s behaviour;
an echo is never evidence; only owner assertions are recorded; the derivation rule's record is "not supported; do not
turn on" with cheap hardening only; the counters see the hook's marks; only the measured caution trigger is shown.
Every test named below was checked to exist on this branch (`grep -rn "def <name>" tests`); that each fails before
its fix is the fix lane's report (it restored the touched source files and ran the test), not re-checked here.

**Security review** (review4 s, `d4931c0`):

| Finding | Severity | What was done | Test / where |
|---|---|---|---|
| H1: derivation rule: a page whose host label equals a word the honest pages capitalise (the subject's name, a month), or a hub they link, merges every honest outlet; the page may state nothing or be flagged by the injection screen | high | fixed in part (`7765425`): a flagged or claimless page is never named or linked; the subject's name and calendar words are no site label. **Open:** a claim-bearing page every honest outlet links or names still merges them ("do not rescue"). Reworded: ADR-066 "not supported; do not turn on" | `tests/acceptance/test_wave4_fix.py::test_no_page_merges_honest_outlets_through_the_subjects_name_a_month_or_saying_nothing` (4 cases); control `::test_a_credit_to_another_read_outlet_still_joins_after_the_fix` |
| H2: own-text rule: an echo disagreeing with a HELD value was kept whole, counted for its value and superseded the verified one; turning the rule on removed `echo_recognition`'s protection there | high | fixed (`ce63748`): every echo either check marks leaves the cluster; the held item is neither superseded nor weakened; the disagreement is asked. ADR-067's earlier sentence called false; the expectation renamed `disagreeing_echo_is_asked_never_evidence` | `tests/acceptance/test_own_text_provenance.py::test_disagreeing_echo_is_asked_never_evidence` (3 cases); Wave 3's `tests/acceptance/test_wave3_s.py::test_an_echo_that_only_disagrees_is_still_asked_never_decided` updated (`3032cf6`) |
| H3: any origin (a Pocket family, an MCP agent) recorded emissions, denials counted, nothing expired: a family's question could stop the owner ever learning a version | high | fixed (`eae9f7d`): owner origins only; asserting parts only (no question, negation, hedge, condition); 30 days. **Open:** 3 of 18 staged mention forms still record (second pass: 9 of another 18 phrasings, denials in other words among them: "partly fixed"); the "record only sourced" fallback would make the rule never fire (the main session's decision) | `test_wave4_fix.py::test_only_the_owners_answers_are_recorded`, `::test_a_denial_a_question_a_condition_or_a_hedge_records_no_value`, `::test_the_owner_denying_a_value_does_not_stop_it_being_learned_later`, `::test_an_emission_counts_for_thirty_days` |
| H4: both rules on: a stale held value could no longer be challenged, no question asked | high | fixed (`ce63748`, with H2; H3 removes the denial that caused it): with both on the disagreement is asked | as H2 (the `source_derivation` case) |
| M1: settings fixed at `nsf init` failed OPEN when damaged (thermal limit off, loosest lifetime, cap and reserve), logged as "cautious": a regression from `main`, on the default path | medium | fixed (`e36d4ee`, `3b42189`): writes pause with a sentence naming `nsf config --repair-fixed NAME=VALUE`; checkpoint refused while `min_free_bytes` is damaged; the log never says "cautious" for them (a damaged `max_evidence_rows` of `5000.0` now pauses writes, stricter than `main`) | `tests/acceptance/test_wave4_d.py::test_a_damaged_setting_fixed_at_creation_pauses_writes_until_the_owner_repairs_it`, `::test_the_owner_repairs_a_damaged_fixed_setting_with_the_named_command` |
| L1: the refusal for a damaged network limit said "enable with `--enable`", which has no effect | low | fixed (`3d99b91`), on both paths (`require_enabled`, the timer's pass, the read-only SearXNG start) | `test_wave4_fix.py::test_the_refusal_for_a_damaged_limit_names_the_repair_not_enable`, `::test_web_access_switched_off_by_the_owner_says_enable` |
| L2: the open-questions view had one own-answer budget and one unindexed scan per question | low | fixed (`29c69a1`): one query, one budget, at most 50 statements | `test_wave4_fix.py::test_the_open_questions_view_reads_its_records_once_with_one_own_answer_budget` |
| L3: fingerprints never expire; off left them active; small edits escape | low | fixed in part (`eae9f7d`): 30 days; off means off (not read; claim marks dropped on load); `nsf web config --forget-own-text`; reworded "accidental echoes only". **Open:** NFKC / format-character normalisation; a full store records nothing more; second pass: the expiry hides fresh emissions (new, medium) and `--forget-own-text` is undone by an older checkpoint's restore (new, low) | `test_wave4_fix.py::test_turned_off_the_fingerprints_are_not_read_and_the_owner_can_remove_them`, `::test_stored_derivation_and_echo_marks_are_dropped_when_their_settings_are_off` |
| N1: the "salt" is the instance id, which is not secret | note | reworded (`emitted.py`, ADR-067) | — |

**Measurement review** (review4 m, `d4931c0`):

| Finding | Severity | What was done | Test / where |
|---|---|---|---|
| H1: echoes of the individual's own unsourced answer replace a held value when they disagree (= security H2); honest pages set aside undercounted | high | fixed (`ce63748`); held-out honest pages set aside now 14 of 2,670 (was 9), `genesis_wave4_fix.json` | as security H2 |
| M1: `wave4:both` opened no question on a disagreeing echo; its counters were blind to the hook's marks | medium-high | fixed (`ce63748`, `8beec6e`): asked; counters count marked ∪ set aside: echo 30 / 18, honest 16 / 8 (held-out / development), as the reviewer computed | `test_wave4_fix.py::test_the_discarded_counters_count_pages_the_hook_marked_at_extraction` |
| M2: the derivation rule's only signal is the generator's convention (31 of 34, 10 of 10 outlets) | medium | reworded (ADR-066, COGNITIVE_BENCHMARK.md; the comment's example removed, `7765425`, `9954c59`) | — |
| M3: 16 of 17 extra round-up suppressions are the metric crediting unread roots | medium | reworded (ADR-066); a second bar counting only read roots not computed (open) | — |
| M4: the caution misleads in measured ways; only the one-source trigger is supported | medium | fixed and reworded (`29c69a1`, `5341de6`, `9954c59`): only "Only one source says this." shown; the other two behind `caution_detail` (off); "more selectively" withdrawn. **Open:** "Only one source" also shows on the project's own site | `test_wave4_fix.py::test_the_caution_shows_only_one_source_by_default`, `tests/uat/test_uat_rollback.py::test_caution_detail` |
| L1: lane E's coverage rests on the generator's reformats and on every page being dated | low | reworded (ADR-067 "what it cannot see") | — |
| L2: the store is about 44 MB at the cap, not 28 MB | low | reworded (ADR-067, `emitted.py`); task rows not capped, with the reason | — |
| L3: the scope of the default-path proof | low | reworded (ADR-066) and kept as a test of the comparison's power | `test_wave4_fix.py::test_the_default_path_comparison_catches_an_injected_regression_and_the_rule_turned_on` |
| L4: no `dirty` field; `derivation_joins` had no caller; lane E's section lacked denominators; "unseen share" unlabelled | low | fixed (`8beec6e`, `9954c59`) | `test_wave4_fix.py::test_the_bench_records_whether_the_tree_was_dirty` |
| notes: the integration's "+4–8 kB" file size is run-to-run variation; held-out baselines were published before the rules | — | recorded (§3) | — |

**Second passes** (the same two reviewers, read-only, on `3032cf6`; every first-pass probe re-run, new probes in
`review4/s/r2/` and `review4/m/r2/`; targeted tests 130 and 150 passed; no withheld file opened, no `final`).

Security, per finding: H1 **fixed for the probed paths, reworded accurately**, open by design (a claim-bearing page
linked or named by every honest outlet; reachable by anyone through a free subdomain such as `attacker.github.io`,
whose site label "github" honest outlets write mid-sentence); H2 **fixed**; H3 **partly fixed** (a family's denial
records nothing now, but 9 of 18 owner phrasings still record, denials in other words among them); H4 **fixed**; M1
**fixed** (only owner paths write the row; the refusal names the setting and the command; reads and forgets still
work); L1 **fixed**; L2 **fixed** (`for_pending` 45 ms at 50 questions and 77 ms at 500, against 953 ms at 50
before); L3 **partly fixed** (expiry, off means off and the removal command fixed; the escapes as the reworded ADR
says); N1 **reworded accurately**. New: the expiry hides fresh emissions behind an expired first one (**medium**,
fails open; the rule ships off); `nsf web config --forget-own-text` leaves no trace a restore replays, so restoring an
older checkpoint brings the fingerprints back (**low**). Notes: `nsf config --repair-fixed` accepts any in-range value
and the creation value is recorded nowhere (so the only number offered is the loosest default; not an escalation, it
needs write access to the file); `caution_detail` is not carried across a restore; an agent's refused call names the
damaged setting (the pattern predates Wave 4); ADR-066's title said "a TRADE, and OFF" (retitled in this
documentation pass) and setting `source_derivation` true gives no warning. **Verdict: may merge after two wording
corrections in ADR-067** (the measured miss of the assertion reader; the restore caveat of `--forget-own-text`; both
made in this documentation pass, `cf36847`), **no code change needed**; the
expiry defect and the mention forms block any recommendation to turn the own-text rule on and any "effective" claim
at the withheld look.

Measurement, per finding: H1 **fixed**; M1 **fixed** (the counters report 18 / 8 and 30 / 16); M2, M3 **reworded
accurately**; M4 **fixed, residual stated** (the caution still shows on the project's own site); L1, L2 **reworded
accurately**; L3 **fixed and reworded** (the power check now 0 / 619 / 640 of 1,768: ADR-066 still says 656); L4
**fixed**. The fix evidence reproduces exactly (150 leaves, 10 verdicts); `shipped` is identical to `d4931c0`'s field
by field; the honest pages set aside going 9 → 14 are the two looks the first pass found kept whole (Quororra,
Wexeth), both now asked; both verdict functions are byte-identical since their commits. What the record did not say,
now in §1.2 and §1.4: "effective" holds at zero margin in src-ho-mixed; the disagreement path is exercised only by
honest pages, with a wording false for them; the worlds do not exercise the H3 restrictions; `echo_recognition`'s
behaviour changed (71 → 73 contested); the curiosity tie-break's views and the `infovalue` family were not compared
field by field; FINAL_EVALUATION.md's "holds for W3" overstates (WAVE3.md's "likely" is at the edge). Wording left in
ADR-066 and ADR-067 at pre-fix values without a label ("9, on 4 looks", "2 suppressed looks", "0.64 % → 0.34 %",
ADR-066's per-shape table, "656", "tested with a stand-in only", "on the next task"); `wave4:both` had no record in any
ADR. **Verdict: every finding fixed or reworded accurately in its main point; Wave 4 may merge**; the wording defects do
not block the merge but block publishing the withheld look's interpretation until corrected. This documentation pass
labelled or corrected each of them in ADR-061, ADR-064, ADR-066 and ADR-067 (`cf36847`), added a note beside
FINAL_EVALUATION.md's merged W3 sentence (kept as merged) and recorded `wave4:both` in ADR-067; a reviewer has not
re-read those edits. Its conditions for the
look are summarised under "The rule for the withheld look".

**Second fix round** (`gen4-fix2`, from `71e5672`; what the second passes found, under the main session's brief).
Every code change has a test in `tests/acceptance/test_wave4_fix2.py` that fails on `71e5672` and passes after it;
every Wave 4 rule stays off; no migration; no held-out tuning.

| Item | What was done | Test | Commit |
|---|---|---|---|
| The expiry masks a fresh emission (security 2b(ii), medium) | `Echoes` judges the earliest emission within 30 days of the claim's read time; `record()` drops expired emissions (and unlists their tasks) before cutting the list to 3 | `::test_a_fresh_emission_counts_though_the_pairs_first_emission_has_expired`, `::test_expired_emissions_are_dropped_before_the_list_is_cut_to_its_maximum` | `c8024c6` |
| `--forget-own-text` undone by a restore (security 2b(iii), low) | recorded as a forget: a `deletion_log` row (kind `own_text`, its state version), valid checkpoints marked stale and rewritten (`after_forget`); every restore or rewrite of an older archive removes the rows from the copy and records it there (`emitted.replay`) | `::test_forget_own_text_survives_a_restore_of_an_archive_taken_before_it` | `d12e9c7` |
| `--repair-fixed` (security 2a) | accepts only what the setting's own check accepts (it already did; the refusal now says what that is); the refusal, the help and the repair's result say the value chosen at creation is recorded nowhere in the file (an archive taken before the damage holds the value then in force); the repair's `deletion_log` row records `creation_value_recorded: false` | `::test_the_repair_says_the_creation_value_is_recorded_nowhere_and_leaves_an_audit_row` | `9c4c19d` |
| Refusal text names damaged settings to agents (security 2a) | a refusal caused by a damaged fixed or web setting is marked; the MCP server returns "the individual is paused for repair by its owner", and the agent scope of the self-description replaces the sentence; the owner's surfaces keep it | `::test_an_agents_refused_call_does_not_name_the_damaged_setting_the_owner_still_sees_it` | `615b26f` |
| No warning when an unsupported rule is set on (security 2d) | `nsf web config` prints and returns the recorded verdict for `source_derivation` (ADR-066) and the stated limits of `own_text_provenance` (ADR-067), and applies the setting; the console cannot set either | `::test_turning_an_unsupported_rule_on_says_its_recorded_verdict_and_still_obeys` | `7cb316d` |
| `caution_detail` and restore (security 2c) | stated in ADR-065 and the setting's comment (web settings but `tick_admission` are not carried; the rule unchanged) | — (wording) | `c921e9e` |

Words. The documentation lane's ADR commit `cf36847` was read line by line against both second passes and the
evidence. Kept as written: ADR-061's note (71 → 73 contested, recomputed), ADR-064's sentence on the views not
compared, ADR-066's hook and free-subdomain sentences, its power figures, its post-hardening syndicated_credit 41 (16)
of 61 (recomputed), ADR-067's post-fix totals. Corrected: ADR-067's "misses about half of the denials and mentions"
(restated with the 18 forms, re-run on this tree: six mentions and three denials in other words record; the nine left
out are negated, hedged or questioned); its verdict line still gave the pre-fix "+1, +1, 0" (now +2, +1, 0 after the
fix lane, labelled); "The false rejection" and "Why OFF" led with lane E's pre-fix figures (now the post-fix cost
first, 14 honest pages and 3 looks on held-out, labelled, with the zero margin in src-ho-mixed and what one more false
rejection does); the expiry defect and the restore caveat (now fixed, and what a restore does is said); `wave4:both`
(no rule pre-registered; it inherits "do not turn on" and is not looked at); ADR-066's title (it led with "a TRADE"),
its "Consequences" (which still said "on the next task" beside "at once") and the default-path proof (re-run here).
Also corrected: FINAL_EVALUATION.md's "holds for W3" (now "not shown to fail, not established", with the margin and
the swing); ADR-064's "0.09–0.26 below the round-robin", which holds for the reference and the four candidates only
(the other configurations are further below), here, in FINAL_EVALUATION.md, BENCHMARK_RESULTS.md and
RECURSIVE_KNOWLEDGE_ASSESSMENT.md; `webcortex/config.py`'s comment on `own_text_provenance` (it still said answers
shown on Pocket or returned over MCP are recorded).

Evidence: `bench_sources.py run` for `shipped`, `own_text_provenance` and `echo_recognition` on development and
held-out (`c921e9e`, clean) is identical field by field to the fix lane's `842ef8b` runs (development 1,416 / 1,423 /
1,413 leaves, held-out 2,607 / 2,615 / 2,614, references and world digests: 0 differ; wall seconds and file sizes
left out), so no evidence file moves. Items 1 and 2 cannot move these worlds: their cycles are 24 hours apart and a
world runs 10 (development) or 12 (held-out), so no emission is ever 30 days old when an echo is read (the lifetime and the list's cut never act),
and no world forgets or restores. The default path on `c921e9e`: `scripts/compare_source_derivation.py` 2,052,099
comparisons (worlds and scenarios 2,038,936, offline 13,163), 0 different; the offline research digest
`68731d7235cb…` (portable `d87dbb4fb77f…`) at the defaults and with every setting off; the comparison's power probe
0 / 619 / 640 of 1,768; the guard differential 0 different (1,937 compared).

**What the reviews changed in this wave's conclusions.** The derivation rule's measured recall and its share of
copies among flagged pairs are an upper bound set by the generator, and its suppression is partly the metric's; it is
recorded as not supported. The own-text rule's "effective" says nothing about the case it exists for when the
individual already holds a value: there its echoes used to win; fixed, and its false rejection was undercounted (9 →
14 of 2,670). The pair's counts were blind. The caution's measurement supports one trigger of three. The one defect on
the default path (fixed settings failing open) was introduced by Wave 4 itself and is fixed. The second passes added:
the own-text fix for who may record is partial (mention forms) and its expiry opened a defect of its own; its
"effective" holds at zero margin and its new question path was reached only by honest pages; Wave 3's `idle-first` W3
figure is not established either, only not shown to fail. Neither second pass found a defect on the default path.

## The rule for the withheld look

Written by the main session and committed before either look (2026-11-26, on `385e3a9`; the conditions are the
second measurement pass's, `review4/findings-measure-2.md`, adopted here). Two looks are taken, each once, on a clean
committed tree, with the output outside the tree and one ledger line each in
`docs/quality/evidence/genesis_final_uses.jsonl`. **Whatever either look shows, no setting changes: every Wave 4
rule stays OFF, and turning one on is the owner's decision.**

**Look 1: source independence** (`scripts/bench_sources.py final --config shipped --config own_text_provenance
--config echo_recognition`).

- *Control.* `shipped` must reproduce step 0's withheld baseline (the look of ledger line 8) field by field in
  `configurations.shipped.worlds.<w>.totals_sum` and `.mean_correct`, apart from timings and file sizes. If it does
  not, the look is void and says so; nothing else in it is read. `shipped` itself establishes nothing new.
- *The own-text rule* (`own_text_provenance`, against `shipped`), judged by `sources.own_verdict`, whose text and
  body are unchanged since `34494cc`, before its held-out run: A1 (`own_admitted` lower in total and in no world
  higher), A2 (`wrongly_admitted` in no world higher), S1 (`suppressed` rises by at most half the number of seeds
  in every world), C1 (`mean_correct` in no world below `shipped` by more than `shipped`'s sd), G (`tp`, `fp`,
  `fn` of the grouping unchanged). The record states the verdict word the function returns and, beside it, per
  world: `discarded_honest_pages`, `discarded_echo_pages`, and the number of contradiction questions an echo opened
  (`contested` with `echo: true`), because the fix changed what the rule does with a disagreeing echo and the
  verdict function does not see that.
- *What an "effective" here would and would not mean.* It would mean: on worlds with proportions and surface forms
  the rule was not built on, fewer of the individual's own answers came back as knowledge, without more true facts
  left unheld. It would NOT mean the rule is safe to turn on. Not exercised by these worlds, and so not established
  by any verdict: answers that only mention or deny a value (9 of 18 further owner phrasings still record),
  undated pages, a page author who edits the echo to escape or to suppress, answers given to a family device or
  an agent, and the 30-day lifetime (a cycle is 24 hours). On held-out S1 held at zero margin in one world: one
  more true fact left unheld there would have read "trade". The record repeats these limits next to the verdict.
- *The older echo rule* (`echo_recognition`, against `shipped`) is run beside it for comparison under the same
  function, as on held-out.
- *Not looked at:* `source_derivation` (its record already says "not supported; do not turn on", which no look can
  change; the worlds are kept fresh for a rule worth judging) and `wave4:both` (no verdict rule was written for the
  pair before any run; it inherits "do not turn on").

**Look 2: learning cycles, for the record of Wave 3** (`scripts/bench_learning_cycles.py final --look wave4-a`:
`reference:no-loop`, `shipped`, `shipped+idle`, `shipped+idle-first`). It names no allowance division: nothing was
chosen on development (ADR-064), so nothing can be promoted.

- *Why it is taken.* Wave 3's three `idle-first` figures (+0.013 / +0.042 / +0.052) are single runs made while two
  tie-breaks followed random task ids; they cannot be repeated. With the tie-breaks fixed the same worlds now give
  the same result on every run, so one new look replaces them.
- *What is read* (from `look.<configuration>.<world>[i]`, same world and seed): per world, the mean over the four
  seeds of the paired difference of `mean.correct`, for `shipped+idle-first` minus `shipped` and for `shipped+idle`
  minus `shipped`, beside the sd of `mean.correct` in step 0's `baseline` run on that world (W1 0.064, W2 0.039,
  W3 0.035, in file order) and beside the references computed from the worlds alone
  (`references.worlds.<w>.round_robin`). A gain is called "beyond the spread" for a world only if it exceeds that
  world's sd; otherwise "within the spread".
- *Precondition, reported whatever it shows.* `shipped` here is not Wave 3's `baseline` run: the defaults changed
  (the two tie-breaks, the requeue's daily count). Every difference between this look's `shipped` and Wave 3's
  figures for the unchanged loop is listed, and the gains above are computed against the NEW `shipped`, never
  against Wave 3's file.
- *What it settles.* Whether running the idle investigation before the timer's pass gains anything on unseen
  worlds once the result is repeatable. It still cannot make `idle-first` a default: no held-out gain was shown for
  it (ADR-062), and the look rule of Wave 3 already said the look cannot promote it.

## Validation

*Placeholder: the main session's final runs on the final tree (quality gate, browser suite, acceptance, sentence
guard, two accounts, offline digest in both regimes, the default-path comparison, the cognitive benchmark) are
recorded here.*

For reference only, not the final validation: the fix lane's runs on its tree (lane-fix report): `ruff check .`
clean; `tests/instance` 905 passed; `tests/webcortex` 2,481 passed, 9 skipped; the full non-e2e suite (withheld
cycle file ignored) on `9954c59` 6,381 passed, 22 skipped, 1 failed (Wave 3's echo case, updated in `3032cf6`, its
file then 26 passed; the whole suite was not re-run); `node tests/js/*.mjs` 7 of 7; `scripts/reproduce.py` 8 of 8;
guard differential 1,937 compared, 0 different; offline digest `68731d7235cb…` (portable `d87dbb4fb77f…`) at the
defaults and all off; the default-path comparison 2,052,099, 0 different; the caution's e2e files 4 passed.

## What stays open

- **Source independence and the individual's own text** (Wave 3's hole): open at the shipped defaults. On unseen
  source worlds the unchanged system admits 110 rows below the bar and does not hold 129 of 356 well-supported true
  values (step 0's look). N1 (a page naming two honest outlets as its origins joins them) is unchanged.
- **The derivation rule**: these worlds lack the evidence that would separate a copy from an honest credit. Worlds
  that could need, drawn independently of any rule's author (review4 measurement Q2): copy lags, an honest outlet's
  own specifics, errors inherited by copies, links (to origins and to primary sources), honest outlets crediting a
  wrong first reporter, names that differ from hosts, undated pages. Links and the publication-order condition are
  not exercised; a claim-bearing page linked by every honest outlet still merges them; a second bar for round-ups
  (only read roots) is not computed.
- **The own-text rule**: mentions and denials in other words still record (3 of 18 staged forms in the fix lane's
  probe, 9 of another 18 in the second pass's; the expiry defect and the restore of `--forget-own-text` are fixed in
  the second fix round); a restore still brings back the archive's web settings, this one included; the normaliser undoes exactly the
  generator's reformats; undated honest pages unmeasured; adversarial escapes (NFKC, format characters); a full store
  records nothing more; echoes of sourced answers; the false rejection of a value stated wrong first; backdated
  pages; `nsf ask` and voice conversation not recorded; its emission scan is linear in claims (at a few million it
  would hit its 1 s budget and the rule would silently do nothing; extrapolated by the security review, not measured);
  the real rate of unsourced answers is unknown.
- **`wave4:both`**: no verdict rule written for the pair; not claimed.
- **Settings**: `nsf config --repair-fixed` accepts any value the setting's own check accepts; the value chosen at
  creation is recorded nowhere once its row is damaged (the refusal and the repair now say so, and point to an archive
  taken before the damage; the creation values are still not recorded); `caution_detail`, like every web setting but
  `tick_admission`, is not carried across a restore (stated in ADR-065; the rule unchanged); an agent's refusal no
  longer names the damaged setting, but a research task that a damage pauses mid-run still writes the reason into its
  report's limitations, which an agent asking for the full report reads (not changed).
- **The caution**: "Only one source" is also shown on the project's own site (F07, F20), which the product treats as
  sufficient; the copy trigger and own-answer flag stay hidden; nothing measured on the open web or with a live model;
  the open-questions view's time at 50 questions with real answers is not measured.
- **Coverage**: reaching runs out of admitted candidates; a share cannot add candidates. The real interleaving of the
  timer and the worker over a day is not simulated. Wave 3's `idle-first` W3 margin is inferred from public worlds.
- **Settings**: an unreadable web row reads as all cautious, and if the owner then sets one setting the others'
  cautious values are saved; an unreadable instance row still raises; the cautious values are lane D's choices.
- Seed 2704's move (WAVE3.md §3.8) stays untraced. Whether `forget_partner` / `forget_camera` should reach
  fingerprints of answers naming a forgotten person was not traced (security review).
- Everything here is offline and simulated: nothing was measured on the open web, with a live answer model, on real
  hardware or over a week on the host.

## The owner's decisions

**Nothing is recommended to be turned on.** Each is a setting the owner may change (`nsf web config --db DB --set
NAME=VALUE`):

| Decision | Today | Evidence for | Evidence against |
|---|---|---|---|
| `source_derivation` | off | fewer badly supported rows (127 → 99, held-out) | the signal is the generator's convention; more true values not held (142 → 207 of 612); `correct` −0.054 to −0.101; one page can merge honest outlets: **do not turn on** |
| `own_text_provenance` | off | own answers admitted 4 → 1, 30 of 31 echoes of unsourced answers set aside; "effective" under its rule on both splits (at zero margin in one world) | a measured false rejection (14 of 2,670 honest pages set aside; suppressed 142 → 145 of 612); "effective" at zero margin in src-ho-mixed (one more false rejection makes it "trade"); mentions and denials still recorded in 9 of 18 phrasings; undated pages unmeasured; owner's answers only. The security second pass: no recommendation to turn it on while these stand |
| `echo_recognition` (Wave 3) | off | "effective" under lane E's rule; supported on Wave 3's withheld look | phrase-based; 13 honest pages set aside on held-out; WAVE3.md |
| `allowance_division` | `"race"` | per look reaching makes more subjects correct | no gain beyond the spread; "unsafe" / "harm" |
| `caution_detail` | off | shows why pages were counted together and the own-answer flag | wrong about three times in ten on the worlds measured |
| Wave 3's settings | unchanged | WAVE3.md, FINAL_EVALUATION.md §5 | |

For the main session: whether the assertion reader's remaining mention forms (3 of 18; 9 of another 18) are acceptable, or the
"record only sourced assertions" fallback (which would make the rule never fire); which configurations the withheld
look runs; the evidence-files proposal (keep, lane D).

## Backing each piece out

| Piece | How | Data |
|---|---|---|
| `source_derivation` (off) | `nsf web config --db DB --set source_derivation=false` | names in claim flags, inert when off; forget removes them |
| `own_text_provenance` (off) | `--set own_text_provenance=false`; `nsf web config --forget-own-text` removes every fingerprint (recorded in `deletion_log`, replayed by every restore of an older archive) | `web_strategies` rows (`emitted:`, `emitted-task:`), inert when off |
| `allowance_division` (default) | `--set 'allowance_division="race"'` | the decision events' `division` field |
| `caution_detail` (off) | `--set caution_detail=false`; revert `29c69a1`, `5341de6`, `cfc1511` for the caution itself | nothing stored |
| the tie-breaks | revert `805fa0e` (runs with the idle investigation become unrepeatable again) | none |
| settings checked on load; fixed settings closed | revert `d2f23f0`, `03bf1ac`, `268c433`, `e36d4ee`, `3d99b91` | none |
| `requeue`'s count; the restore's scan | revert `ec33fe0`; `b905024` | none |
| whole lanes | revert the merges: step 0 `42fd136`, D `0b3e35e`, E `97f7abc`, S `23243b1`, A `9ba3263`; the integration `0fd8fe6`, `d4931c0` | none (no schema change) |
