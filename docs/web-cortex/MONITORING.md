# Answer monitoring (ADR-054)

What `webcortex/monitoring.py` adds to a research result, what each setting does, and what was measured. Numbers:
`docs/quality/evidence/genesis_monitoring.json` (`scripts/bench_monitoring.py evidence`).

## The record: `report["monitoring"]` (setting `answer_monitoring`, on by default)

Written after the answer and the model's guarded summary exist, before the result is stored. It changes no
verification state, posterior, guard decision, answer or knowledge item, and calls no model.

| Field | Meaning |
|---|---|
| `kind` | `verdict` (a fact check), `answer` (the top quoted statement) or `none` |
| `confidence`, `confidence_note` | P(the answer given is right) from the stratum's fitted map, **only when that map is applied** (fitted on at least 30 development items in at least 3 blocks); otherwise `None` and the note "not enough development data". No shipped map has that much data today, so the record carries no answer confidence. |
| `claim_probability` | P(the statement is true): the unfitted score (the logistic of the sum below); `claim_probability_basis` says so |
| `score`, `components` | the log-odds terms the probabilities come from (table below) |
| `signals` | the inputs as read: state, posterior, groups for and against, contradictions, staleness, guard, critique, source dependence, memory agreement |
| `sufficiency` | `{"enough": bool, "why": [plain reasons], "shown": [notes]}`: the reasons are about the sources only, such as "only one independent source states it" or "the agreeing sources are one family of sites (docs.x.example, blog.x.example)"; what the model did ("the model found nothing it could say") is a shown note, never a reason |
| `below_threshold`, `selective_basis`, `threshold`, `target_risk` | whether the evidence is weak, and what decided it: `sufficiency` (a fact check, or no applied map: everywhere today) or `threshold` (an applied answer map with the threshold chosen on development data for a 10 % target risk) |
| `decision`, `note` | `answer`, or `not_enough_evidence` with its plain line (only with `selective_answering`) |
| `depth` | pages, queries and requests used; verified statements after each page; gain per page; guard refusals; `stopped_early` (adaptive depth); `escalated` (verification escalation) |

### Score terms (`monitoring.WEIGHTS`, fixed, never fitted)

| Term | Weight (log-odds) |
|---|---|
| evidence | the claims module's posterior as log-odds |
| state | supported +1, disputed −1, other states 0 |
| contradictions | −0.25 for each open contradiction on the subject (at most 2) |
| stale | −0.25 when the question's freshness window is not met |
| independence | −0.5 for each supporting group joined to another by `claims.dependence` |
| guard | 0 × share of the model's sentences the guard refused; 0 if the model abstained (shown only: the development data has no model calls, and with a weight the confidence would move with the model's wording at identical evidence) |
| critique | 0 × share of kept sentences the self-critique marked (shown, but it carries no weight: see below) |
| agreement | +0.25 when memory held the statement and the web strengthened it; −0.25 when a stored item or source contests it |

### Calibration

A monotone map per stratum (`claim`, `verdict`, `answer`): pool adjacent violators over the development outcomes,
each block read with a Beta(1, 1) prior (four agreeing outcomes give 5/6, never 1), linear between block centres.
Fitted on the development split of the cognitive benchmark's selective family **only**
(`scripts/bench_monitoring.py fit` writes `webcortex/data/monitoring_calibration.json`). The corpus is fictional:
on the open web the maps would be a starting point, not a guarantee.

After the Wave 1 review: a map is applied only with at least 30 development items in at least 3 blocks
(`monitoring.applied`; the file records `applied` and `minimum`). The shipped maps have 6 (answer, 3 blocks), 15
(verdict, 1 block: every fact check used to read 0.941) and 8 items (claim, 2 blocks), so none is applied. Claims
never use a map: the fitted claim map scored worse than the unfitted score on development (Brier 0.034 against
0.016, 8 claims), and on held-out as well (0.042 against 0.022, 10 claims). The selective threshold applies only
when both answer maps do.

A monitoring failure is logged as the task's `error` event (stage `monitoring`); it adds nothing to the report.

## Source dependence (`claims.dependence`)

On top of `claims.independence_groups` (which verification keeps using unchanged): two groups are joined when they
share a registrable site label (one family of sites) or when a page of one links to a page or the site of the other
(it cites it). The record says `one_family`, `circular`, `families`, `citations` and the effective number of groups;
`sufficiency.why` says it in plain words.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `answer_monitoring` | on | the record above |
| `selective_answering` | off | when the evidence is weak (`below_threshold`: by `sufficiency` for a fact check and wherever no map is applied), the answer opens with "Not enough evidence for a confident answer: <why>. What the sources say is below, as they say it." The quotes stay. |
| `verification_escalation` | off | when the evidence is weak (as above), and only if the task's own budget still has a page, a query, two requests and 15 s: one search for the weakest statement, read the first unread result from a family of sites not read yet, evaluate and synthesize again. Once per task. The owner's stops apply as at every network step. |
| `adaptive_depth` | off | while reading, stop once at least 4 pages were read, a statement has two independent groups, and the last 2 pages added no verified statement and no contradiction |

`nsf web config --set selective_answering=true` (and so on) switches each one.

## Measured (offline corpus, no model; development / held-out)

- **The claim the evidence supports** (Wave 1 review B5): on 10 held-out claims, adding the verification state to
  the posterior lowers Brier 0.122 → 0.022 with the unfitted score that ships (0.042 with the fitted map, no longer
  shipped); development, 8 claims, 0.095 → 0.016. Every claim was already ranked right (AUROC 1.0 before and after;
  resolution = uncertainty), so the whole Brier is reliability error. The two-line rule on the verdict's state word
  (0.95 / 0.05 / 0.5) scores 0.0025 on both splits. ECE with 10 bins is not reported as a claim (n = 8 and 10).
- **No held-out evidence about selective answering:** every held-out answer is right. The sufficiency decision
  would answer 8 of 20 held-out items (withholding 12 right answers) and 6 of 21 on development (withholding the 3
  wrong and 12 of the 18 right). It stays off.
- Answer confidence: none is shown (not enough development data). As first recorded by the lane, with the maps
  applied: held-out answer Brier 0.147 → 0.071, development AURC 0.080 → 0.027; those maps rest on 6 and 15 items.
- The score's terms `independence`, `guard` and `critique` are 0 on every corpus item (no dependent sources, no
  model): their ablations change nothing because nothing here exercises them. They are untested.
- Offline research benchmark: every setting off reproduces the unchanged code's deterministic metrics exactly
  (digest), and so does `answer_monitoring` on (0 differences). With the sufficiency basis (after the Wave 1 review):
  selective answering opens 36 of the corpus questions with the "not enough evidence" line, the seven key metrics
  unchanged; escalation escalates 14 tasks for +6 pages, +28 queries and +12 requests, no key metric changed
  (citation precision 0.9948 → 0.9949): off. (Before the review, with the threshold: two tasks escalated, +2.) Adaptive depth: same accuracy, answer, contradiction, citation, freshness and
  budget rates with 22 fewer pages (581 → 559) and 24 fewer requests; fewer items held for research and wall time
  not lower: off, offered to the owner as a candidate.
- Self-critique marks (two recorded live-model runs, 94 and 90 kept sentences): marks on 41 and 46 sentences; 3
  sentences per run state a value the corpus marks false, all 3 marked (recall 1.0, precision 0.07); random marks
  of the same number would catch all 3 with probability 0.08 and 0.13. The marks do not usefully predict wrong
  sentences: they stay display-only and weigh 0 in the score.

## Precision report of the belief core (`nsf regulation --precision`)

Display only, computed when asked from `pending`, `changepoints` and `epoch_links`: per rule (change alarm, ambiguous
change, earlier setup, partner meaning, reference test, web knowledge, other web questions) and per epoch, every
question asked and how it ended (confirmed, rejected, answered, withdrawn, expired, open), with precision =
confirmed / (confirmed + rejected) and the share the owner answered. On the staged grounding lifetimes (40 per
seed, a simulated owner who answers truthfully) it matches the scenario's own count of answers, and it shows the
change detector's alarm precision at 0.91, 0.67 and 0.68 for seeds 0, 1 and 2 (22, 30 and 28 alarms).
