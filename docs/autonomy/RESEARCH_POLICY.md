# Controlled autonomous research: policy

Owner decision D4 (docs/qa/RELEASE_DECISIONS.md), ADR-039 draft (`.omc/autopilot/adr/ADR-039-autonomy.md`).
Code: `src/neuroscifi/webcortex/autonomy.py` (budgets, ranking, idle investigator, notifications),
`webcortex/nodes.py` (nodes and session ownership), `pocket/research_routes.py` (Pocket routes),
migration 10 (`instance/schema.py`). Tests: `tests/webcortex/test_autonomy.py`, `tests/test_pocket_autonomy.py`,
`tests/console/test_console_autonomy.py`, `tests/e2e/test_pocket_autonomy_e2e.py`. Live evidence:
`docs/quality/evidence/autonomy_live.json`.

## 1. Policy

- Autonomous research is off by default. It needs the `autonomous` switch and a recorded owner enable (who and
  when, plus a `decision` event). The recording paths are `nsf web autonomy enable`, `nsf web config --autonomous`,
  the console switch (`POST /api/web/config` or `POST /api/web/autonomy {"enabled": true}`) and `autonomy.enable()`.
  If code sets the switch without a record, the timer keeps its 0.6 behaviour, but the idle investigator stays off.
- Autonomous research means the tasks whose origin is `curiosity` or `refresh:<id>`. Every other origin counts as
  user research: console, CLI, Pocket, node, owner monitor and MCP.
- Research runs only when the stored records contain a reason for it (section 3). It also needs room in the budget
  and an expected information gain of at least the minimum. Nothing browses just to generate activity.
- Autonomy does not feed on its own by-products. A subject that only autonomous research has seen is not pursued
  unless it overlaps an active owner goal. The owner's own records are always eligible: hypotheses, monitors and the
  owner's research.
- Results are never promoted automatically. A verified statement becomes a knowledge candidate through the existing
  ADR-026/035 path. The owner is notified only of meaningful findings (section 5).

## 2. Daily limits

Each day is the UTC calendar day of the task's start time `now`. Every function takes an explicit `now`, and no
code reads the wall clock for this.

| Limit | Autonomous (default) | User (default) | Counted as |
|---|---|---|---|
| Searches | 20 | 400 | queries dispatched to the provider registry (`usage.queries`); charged before sending |
| Pages | 60 | 1200 | documents acquired (`usage.pages`); charged before fetching |
| Active execution | 1800 s | 14400 s | the task's measured run time (`usage.seconds`) |
| External spend | $1.00 | $5.00 | searches x `prices_usd.search` + pages x `prices_usd.page` |
| Concurrent investigations | 2 | not limited | autonomous tasks that have started and not finished, plus queued ones a worker has claimed |

The two budgets are separate. When autonomy exhausts its budget, the user's research is not affected, and the
reverse also holds (tested in `test_user_research_has_its_own_budget` and live in the evidence file).

Both prices default to 0.0. SearXNG, the structured APIs (PyPI, npm, OSV, Wikipedia, arXiv, ...) and the local
vLLM model have no per-call charge. A paid provider would set its price, and the $ limit then applies through the
same arithmetic (tested with `prices_usd.search = 0.10`).

These are observed, not limited. Each task records two numbers, `search_counts.searxng_requests` and
`search_counts.engine_requests`: the HTTP requests sent to SearXNG, and the upstream engine requests SearXNG reports
for them. Cache hits count as neither. A circuit-open engine is not counted. A transport failure counts as one
SearXNG request and no engine request. The totals appear in `nsf web autonomy status`.

Configuration, applied by the owner only:

    nsf web autonomy set --policy-json '{"autonomous_daily": {"searches": 10, "concurrent": 1},
                                         "user_daily": {"pages": 600}, "prices_usd": {"search": 0.0},
                                         "min_gain": 0.05, "idle_interval": 300, "cooldown": 86400}'

The console equivalent is `POST /api/web/autonomy` with the same keys.

### Enforcement

- A task's budget is charged when a worker starts the task. `autonomy.admit` runs inside `orchestrator._claim`'s
  `BEGIN IMMEDIATE` transaction, so every worker process sees the same totals and two processes cannot both take the
  last allowance. Committed spend for the day is the usage of settled tasks plus the whole allowance of open ones.
- If searches, pages, active time or spend has nothing left, the task is cancelled. Its error reads `daily
  <scope> research budget exhausted on <day>: <limit>`.
- Otherwise the task's `max_queries`, `max_pages` and `max_seconds` are clamped to what is left, and an open
  `autonomy_ledger` row records the allowance. A clamp also writes a `decision` event.
- A submission against a spent budget is refused at once with `autonomy.BudgetRefused`. Pocket answers
  `429 {"error": "daily_budget"}` and nothing is queued.
- Searches and pages are charged before each request, so they cannot pass the cap. Active time can pass it by at
  most the network step in flight, because the run checks its time budget before every step.
- Concurrency: `claim_next` holds autonomous tasks back while 2 are open, and `admit` checks again under the same
  lock. A task refused only for concurrency stays queued, and the worker releases its claim.
- When a task ends, `settle` replaces its allowance with what it actually used. A task cancelled before it
  started has no ledger row and costs nothing.
- Forgetting a task (`nsf forget research`) keeps its ledger row, which holds the task id and numbers only, so a
  budget cannot be refunded by forgetting.
- The 0.6 rolling 24-hour caps (`daily_autonomous`: tasks, requests and bytes) still apply in addition.

## 3. Prioritisation

Every candidate comes from stored records:

- `curiosity.rank`: contested knowledge, stale knowledge, knowledge predictions that are due, contested subjects
  that never became knowledge, and single-source gaps.
- Open hypotheses with an open prediction, from Lane D (`web_hypotheses`, `web_predictions`). Before this lane,
  nothing scheduled research to settle them.

Each subject appears once, with its strongest signals.

    priority = info_gain x (1 + sum(weight[s] x signal[s])) / cost

| Signal (value in [0, 1]) | Weight | Source record |
|---|---|---|
| goal | 1.0 | word overlap (Jaccard) with active owner monitors and the owner's research of the last 7 days |
| contradiction | 1.0 | contested knowledge or subject, or a hypothesis from a `conflicting`/`disputed` cluster |
| hypothesis | 0.8 | an open hypothesis awaiting evidence |
| prediction_uncertainty | 0.8 | binary entropy of the prediction's forecast probability |
| source_change | 0.7 | a monitor reported a meaningful change of the subject in the last 7 days (`finding` events) |
| stale | 0.6 | knowledge whose freshness window passed |
| gap | 0.5 | a single-source claim, or a hypothesis from `insufficient`/`not_evaluated` evidence |

- `info_gain` is the expected information gain in bits, computed with curiosity's model: a report of reliability
  0.8 about a binary variable, or 4-way for contested subjects.
- `cost` is the mode's request budget relative to `fact_verification`, plus the estimated spend divided by the
  spend cap.
- A candidate is refused, and the refusal is shown with its reason, when any of these holds:
  - its expected gain is below `min_gain`, 0.05 bits by default (for example, a forecast of 0.995 gives 0.012 bits);
  - the subject was investigated autonomously within `cooldown` (24 h);
  - its statement is a structured record's template;
  - it has nothing searchable;
  - it is known only from autonomous research and has no goal overlap.

Hypotheses are checked as the statements they make (`fact_verification`). Other kinds research the statement's own
words.

Every autonomous task carries a `decision` event with its ranking inputs: kind, key, signals, weights, info_gain,
cost, priority, and hypothesis or prediction ids. `GET /api/web/autonomy` and `nsf web autonomy candidates` show the
current ranking.

Modes map to implementations as follows (RD-09):

- Continuous research and change detection are monitors (`nsf web monitor`).
- Gap resolution covers single-source gaps and hypotheses.
- Stale refresh covers knowledge predictions that are due, and stale knowledge.

## 4. Controls

| Control | Command / route |
|---|---|
| Status (both budgets, refusal reason, running count, recent decisions) | `nsf web autonomy status`, `GET /api/web/autonomy` |
| Enable / disable (recorded) | `nsf web autonomy enable|disable`, `POST /api/web/autonomy {"enabled": ...}` |
| Limits, prices, threshold, cooldown | `nsf web autonomy set --policy-json ...`, `POST /api/web/autonomy` |
| Current ranking | `nsf web autonomy candidates` |
| One idle decision now | `nsf web autonomy idle` |
| Cancel any task | `nsf web cancel`, console, Pocket `research_cancel` |
| Research nodes | `nsf web node enrol LABEL --pocket-db PATH`, `nsf web node revoke LABEL`, `nsf web node list` |

## 5. Scheduling, recovery and notifications

- Idle investigations: when a worker (`nsf web work`, or the console's embedded worker) finds nothing queued, it
  asks `autonomy.idle`, at most once per `idle_interval` (300 s). If autonomy is enabled, the budget has room,
  fewer than 2 investigations are open and the best candidate clears the threshold, that candidate is queued as an
  autonomous task. Otherwise a `decision` event states why: budget refused, nothing worth researching (with the
  best gain), or busy.
- Scheduled research is unchanged:
  - monitors are owner-scheduled and charged to the user budget;
  - the timer (`nsf web tick`) runs predictions and curiosity, charged to the autonomous budget;
  - checkpoints after each stage, cancellation before every network step, and the history in `web_events` are as
    in RESEARCH_ORCHESTRATION.md.
- Crash recovery: a crashed task keeps its open allowance and its concurrency slot. Detection works by lease expiry,
  or at once when the holder process is dead on the same host. `claim_next` resumes the task from its checkpoint
  without a second allowance (`test_a_crashed_autonomous_task_resumes_without_a_second_allowance`).
- Notifications use the existing pending-question mechanism, `web:autonomy:<task>`, within the web question
  budget. A notification is sent only when the task produced at least one of these:
  - a newly verified statement: a knowledge candidate from a `supported` cluster;
  - a resolved contradiction: a settled prediction of a hypothesis that came from a contradiction;
  - a settled prediction: `confirmed` or `error`.

  Anything else records "no meaningful finding: no notification".

## 6. Pocket research and nodes

Authorised devices keep the ADR-028 rules: family scope, 10 submissions per hour per family, 512-byte bodies
(1 KiB for submit), bearer and origin checks, and invisibility while the feature is off.

| Route | Who | What |
|---|---|---|
| `research_submit`, `research_list`, `research_get` | any device of the family | as before; `research_get` adds `spent` (searches, pages so far) and `continued_from` |
| `research_evidence {id}` | any device of the family | ADR-035 state, basis and category per subject, with the claim text (marked untrusted) and the sources |
| `research_continue {id}` | any device of the family, unless another device owns the session | continue a cancelled, partial or failed task (`orchestrator.requeue`), counted in the hourly limit |
| `research_cancel {id}` | any device of the family, unless another device owns the session | as before |
| `research_session {id, action[, to]}` | `status`: any device; `take`, `handoff`, `accept`, `release`: enrolled nodes | session ownership |

Multi-node, minimal scope (RD-11 assumption, ADR-031 kept):

- A node is a paired Pocket device that the owner enrolled with the `node` capability. It is stored as
  instance-salted digests of its device and family fingerprints, never the credential. Nodes submit, continue,
  read and own sessions. They never execute: the Spark that runs `nsf web work` is the only executing node.
- A session is a chain of continued tasks, named by its root task id. A session has one owner at a time; the
  primary key of `autonomy_sessions` guarantees it. Ownership is a 15-minute lease renewed by the owner's actions.
- Another node can get ownership in one of two ways:
  - `take`: only when the session is unowned or the lease has expired;
  - explicit hand-off: the owner offers the session to a named node of the same family, and that node accepts
    within 5 minutes. The transfer is one conditional `UPDATE`.
- While a session is owned, no other device of the family can cancel or continue it (`409 owned`). Reading stays
  open to the family.
- Revoking a node releases its sessions.
- Tests cover a race of 8 concurrent `take` calls (exactly one owner), hand-off and accept, revocation, and family
  isolation (another family gets 404 on every route).

## 7. Measured live results (2026-11-21, `docs/quality/evidence/autonomy_live.json`)

Setup: a scratch individual, not the owner's instance; SearXNG on 127.0.0.1:8888; vLLM (Qwen3-30B-A3B-Instruct
FP8) on 127.0.0.1:8811. Every step went through the user-facing CLI.

1. Off by default: status showed `enabled: false`, and `nsf web autonomy idle` returned `off`.
2. The owner recorded a hypothesis with a 30-day prediction at p = 0.5: `nsf knowledge hypothesize "numpy 2.5.5 was
   released"`.
3. The ranking picked it with priority 0.723: gain 0.278 bits x (1 + 0.8 + 0.8) / cost 1.0. The signals were
   hypothesis 1 and prediction uncertainty 1.
4. `nsf web autonomy enable` recorded `owner:cli`.
5. `nsf web work --once` (17.2 s wall) queued and ran one idle investigation, a `fact_verification` task. It
   **completed**:
   - usage: 6 searches, 8 pages, 16.9 s active, $0.00;
   - SearXNG: 2 requests, 14 upstream engine requests;
   - providers that answered: SearXNG (docker hub, duckduckgo, mwmbl), PyPI, npm;
   - sources included pypi.org/project/numpy and numpy.org release notes;
   - 15 subjects evaluated: 1 `supported` (basis `registry_record`), 12 `insufficient`, 2 `temporally_unresolved`;
   - answer written by the model (`model:qwen3-30b-a3b-instruct-2507-fp8`) under the citation guard.
6. The autonomous budget moved from 20/60/1800 s to 14/52/1786 s remaining. The user budget was untouched.
7. Notification: one pending question, `web:autonomy:<task>`, saying "1 newly verified statement(s)". No other
   notification.
8. After the run, the new research opportunities from that run were ranked and refused. One reason was "known only
   from autonomous research and unrelated to an active goal". The other was the 24-hour cooldown on the numpy
   subject.
9. With the autonomous search limit set to 6, the amount already used:
   - `nsf web autonomy idle` returned `refused: daily autonomous research budget exhausted on 2026-11-21: searches 6
     of 6`;
   - an autonomous submit raised `BudgetRefused`;
   - a user (`cli`) submit was queued normally.

Earlier live trials, kept to explain two choices:

- With mode `gap_resolution`, a 30-request budget, a hypothesis ran out of requests after 3 SearXNG calls. Each
  SearXNG call is charged one request per queried engine (7 engines, 21 requests), so the task ended `partial`.
  Hypotheses now use `fact_verification`.
- With an ambiguous news-like statement (a telescope launch), SearXNG routed the queries to news results. The task
  completed with only `insufficient` and `temporally_unresolved` subjects, and no notification was sent, as
  designed.

## 8. Limits stated plainly

- In the live run the hypothesis's prediction did not settle. The registry cluster's key (`numpy pypi releas
  version`) was not similar enough to the hypothesis key (`numpy releas`) under key matching alone. Fixed in 0.7.0:
  the autonomous task now carries the hypothesis id (task context `hypothesis`), and `evolution.observe` settles on
  that link, with lexical key matching as the fallback
  (`test_a_hypothesis_settles_on_its_autonomous_tasks_link_when_the_keys_differ`). The live run has not been
  repeated since the fix.
- Intent detected packages only in lower case (`numpy`, not `NumPy`), so "NumPy 2.5.5 was released" did not reach
  PyPI. Fixed in 0.7.0: package detection is case-insensitive (`tests/webcortex/test_intent.py`).
- Active time can overshoot the day's limit by one network step. Spend is a price model, because no paid provider
  is configured, so $1 has never been reached live; it is reached only in tests with a set price.
- Concurrency is enforced across processes that share the individual's SQLite file. One executing host is assumed
  (ADR-031).
- Multi-node is the minimal RD-11 scope. Nodes are Pocket-paired devices with a capability; there is no worker
  federation, remote attestation or result signing. The Pocket page offers Continue and Evidence review; session
  hand-off is API only for now.
- Not verified: days-long unattended operation (RD-54). Notifications reach a phone only while Pocket is open
  (RD-22).
