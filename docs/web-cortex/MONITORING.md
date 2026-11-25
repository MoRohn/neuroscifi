# Answer monitoring (ADR-054)

What `webcortex/monitoring.py` adds to a research result, what each setting does, and what was measured. Numbers:
`docs/quality/evidence/genesis_monitoring.json` (`scripts/bench_monitoring.py evidence`).

## The record: `report["monitoring"]` (setting `answer_monitoring`, on by default)

Written after the answer and the model's guarded summary exist, before the result is stored. It changes no
verification state, posterior, guard decision, answer or knowledge item, and calls no model.

| Field | Meaning |
|---|---|
| `kind` | `verdict` (a fact check), `answer` (the top quoted statement) or `none` |
| `confidence` | P(the answer given is right): for a fact check, that the verification state is right; for a quote, that it answers the question correctly |
| `claim_probability` | P(the statement is true) |
| `score`, `components` | the log-odds terms the probabilities come from (table below) |
| `signals` | the inputs as read: state, posterior, groups for and against, contradictions, staleness, guard, critique, source dependence, memory agreement |
| `sufficiency` | `{"enough": bool, "why": [plain reasons]}` such as "only one independent source states it" or "the agreeing sources are one family of sites (docs.x.example, blog.x.example)" |
| `threshold`, `below_threshold`, `target_risk` | the selective threshold chosen on development data for a 10 % target risk |
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
| guard | −0.5 × share of the model's sentences the guard refused; −0.5 if the model abstained |
| critique | 0 × share of kept sentences the self-critique marked (shown, but it carries no weight: see below) |
| agreement | +0.25 when memory held the statement and the web strengthened it; −0.25 when a stored item or source contests it |

### Calibration

A monotone map per stratum (`claim`, `verdict`, `answer`): pool adjacent violators over the development outcomes,
each block read with a Beta(1, 1) prior (four agreeing outcomes give 5/6, never 1), linear between block centres.
Fitted on the development split of the cognitive benchmark's selective family **only**
(`scripts/bench_monitoring.py fit` writes `webcortex/data/monitoring_calibration.json`). The corpus is fictional:
on the open web the maps are a starting point, not a guarantee.

## Source dependence (`claims.dependence`)

On top of `claims.independence_groups` (which verification keeps using unchanged): two groups are joined when they
share a registrable site label (one family of sites) or when a page of one links to a page or the site of the other
(it cites it). The record says `one_family`, `circular`, `families`, `citations` and the effective number of groups;
`sufficiency.why` says it in plain words.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `answer_monitoring` | on | the record above |
| `selective_answering` | off | below the threshold, the answer opens with "Not enough evidence for a confident answer: <why>. What the sources say is below, as they say it." The quotes stay. |
| `verification_escalation` | off | below the threshold, and only if the task's own budget still has a page, a query, two requests and 15 s: one search for the weakest statement, read the first unread result from a family of sites not read yet, evaluate and synthesize again. Once per task. The owner's stops apply as at every network step. |
| `adaptive_depth` | off | while reading, stop once at least 4 pages were read, a statement has two independent groups, and the last 2 pages added no verified statement and no contradiction |

`nsf web config --set selective_answering=true` (and so on) switches each one.

## Measured (offline corpus, no model; development / held-out)

- Claim probability, held-out: Brier 0.122 → 0.042, ECE (10 bins) 0.341 → 0.199, debiased l2 ECE 0.348 → 0.204,
  AUROC 1.0 → 1.0. Development: Brier 0.095 → 0.034, ECE 0.304 → 0.184.
- Answer confidence, held-out: Brier 0.147 → 0.071, ECE 0.372 → 0.181 (every held-out answer is right, so only
  calibration can move there). Development: AURC 0.080 → 0.027, AUROC 0.685 → 0.926, coverage at 5 % risk 0.38 →
  0.76.
- Selective answering at the development threshold (0.493, target risk 10 %): development coverage 0.86 at risk
  0.056 (baseline confidence with its own development threshold: 0.57 at 0.083); held-out coverage 0.90 at risk 0
  (it sets aside two right answers; held-out has no wrong one to catch). No held-out gain, so it stays off.
- Offline research benchmark: every setting off reproduces the unchanged code's deterministic metrics exactly
  (digest), and so does `answer_monitoring` on (0 differences). Escalation: two tasks escalated, no metric changed,
  +2 queries/pages/requests: off. Adaptive depth: same accuracy, answer, contradiction, citation, freshness and
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
