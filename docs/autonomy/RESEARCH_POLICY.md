# Controlled autonomous research: policy

Owner decision D4 (docs/qa/RELEASE_DECISIONS.md), ADR-039 draft (`.omc/autopilot/adr/ADR-039-autonomy.md`).
Code: `src/neuroscifi/webcortex/autonomy.py` (budgets, ranking, idle investigator, notifications),
`webcortex/nodes.py` (nodes and session ownership), `pocket/research_routes.py` (Pocket routes),
migration 10 (`instance/schema.py`). Tests: `tests/webcortex/test_autonomy.py`, `tests/test_pocket_autonomy.py`,
`tests/console/test_console_autonomy.py`, `tests/webcortex/test_autonomy_ranking.py` (UAT T1-02, T1-03, T1-17, S6),
`tests/e2e/test_pocket_autonomy_e2e.py`. Live evidence:
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
- Autonomy does not feed on its own by-products. Every subject has a derivation depth: 0 for the owner's records
  and for what the owner's research found, and 1 + the depth of the investigated subject for what an autonomous task
  found. A subject is pursued only at depth 0, or at depth 1 when it shares at least a third of its words with the
  owner subject it was derived from (section 3). A word overlap with an active goal is a ranking signal, never a
  licence (UAT T1-02: a 0.03 overlap let a chain drift from predictive coding to econometric tests).
- The owner's own records are always ranked and come first: open owner hypotheses (with or without an open
  prediction), the owner's assertions that no evidence was examined for yet, and contested owner assertions. They
  are never cut by a candidate limit, and when refused they are listed first with the reason (UAT T1-03).
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
- Open hypotheses with an open prediction, from Lane D (`web_hypotheses`, `web_predictions`): every open owner
  hypothesis, then the 50 newest others. Before this lane, nothing scheduled research to settle them.
- The owner's assertions (`nsf knowledge assert`) without supporting evidence (kind `owner_assertion`).

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
  - it was derived from autonomous research more than `MAX_DERIVATION_DEPTH` (1) steps from the owner's records,
    or in a cycle of autonomous tasks;
  - it is at depth 1 and shares less than `MIN_RELEVANCE` (1/3, as a share of the smaller word set) with the owner
    subject it came from;
  - a derived statement is not a self-contained claim (`curiosity.claim_shaped`): a question, a fragment, a record
    template, or wording that leans on its source text (it, these, this ..., we, you).
- Order: owner records first, then by priority. `candidates` returns `total` (both list lengths before `limit`).
- Hypotheses are opened by consolidation only from self-contained claims (the same `claim_shaped` test), one per
  statement (an open hypothesis with the same statement under another key is reused), and at most
  `MAX_NEW_HYPOTHESES_PER_TASK` (5) new ones per task; refreshing an open one does not count (UAT T1-17: 384 open
  hypotheses after about 35 tasks, many of them "Crossref record: ..." titles).

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
| Research nodes (§6) | console Autonomy → Research nodes (`GET`/`POST /api/web/nodes`); `nsf web node list`, `nsf web node enrol NAME --fingerprint FP`, `nsf web node reject --fingerprint FP`, `nsf web node revoke NAME` |

## 5. Scheduling, recovery and notifications

- Idle investigations: when a worker (`nsf web work`, or the console's embedded worker) finds nothing queued, it
  asks `autonomy.idle`, at most once per `idle_interval` (300 s) per worker (process and thread). One worker runs one
  task at a time, so two concurrent investigations need two workers, for example the console's `--web-worker` and
  `nsf web work`. In RC1 the interval was instance-wide and the second worker only waited (UAT S6), so
  idle investigations never ran two at once (`test_two_workers_start_two_autonomous_investigations_at_once`). If autonomy is enabled, the budget has room,
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
- Notifications use the existing pending-question mechanism, `web:autonomy:<task>`. They are opened even when the
  owner's web question budget (`max_open_web_questions`) is full: the autonomous budget already bounds them, and a
  finding dropped at the cap was logged "notified" with no question (R1-01). The decision log says "notified" only
  with the pending id; otherwise "finding not delivered". A notification is sent only when the task produced at least one of these:
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
| `node_enrol {action[, ...]}` | any device of the family | `status`; `challenge`; `attest {challenge, alg, public_key, signature}` (below) |

Multi-node, minimal scope (RD-11 assumption, ADR-031 kept; signing and attestation ADR-040 draft):

- A node is a paired Pocket device that the owner enrolled with the `node` capability after an attested
  enrolment. It is stored as instance-salted digests of its device and family fingerprints, never the credential,
  with its public key, algorithm and fingerprint. Nodes submit, continue, read and own sessions. They never execute:
  the Spark that runs `nsf web work` is the only executing node. There is no worker federation.
- **Node key.** Pocket generates the node's key pair with WebCrypto: Ed25519 when the browser supports it, else
  ECDSA P-256 (SHA-256). The private key is non-extractable and kept in IndexedDB, so it survives reloads. The
  pairing credential stays in tab memory (POC-07).
- **Attested enrolment.**
  1. The device asks for a challenge (`node_enrol challenge`). The Spark binds it to the pairing family, the node
     id and an expiry of 5 minutes.
  2. The device signs the challenge and sends its public key (`attest`). The Spark verifies the signature and keeps
     the key pending for at most 1 hour.
  3. The device shows its key fingerprint (8 groups of 4 hex digits). The console (Autonomy → Research nodes) lists
     the pending key with its fingerprint and algorithm.
  4. The owner compares the two fingerprints and enrols the device under a name, or rejects it. Nothing is a node
     before that.
- **Signed requests.** A node's state-changing requests (`research_submit`, `research_continue`, `research_cancel`,
  `research_session` other than `status`) carry `X-NSF-Timestamp`, `X-NSF-Nonce` and `X-NSF-Signature` over the
  canonical request: method, path, SHA-256 of the body, timestamp and nonce. The Spark answers `401` with a code
  unless the signature verifies with the recorded key and algorithm and the timestamp is within ±120 s of the
  Spark's clock. The nonce must not have been used before by that device; nonces are kept 240 s. The codes are
  `signature_invalid`, `stale_request`, `replayed_request`, `wrong_family`, `malformed`, `node_revoked` and
  `unknown_node`. An unsigned change from a node enrolled with a key is `signature_required`, never accepted
  silently.
- **Signed results.** The Spark's own Ed25519 key (`INSTANCE.spark-key`, mode 0600, outside git; `--spark-key`)
  signs every research reply, errors included: status, path, the request's nonce and the body. A device pins the
  Spark key and fingerprint from the `pair` reply. Pocket shows nothing as from your Spark unless the signature
  verifies under the pinned key. A tampered reply or one signed by any other key is refused. A pairing made before
  this change has no pin, and Pocket asks the user to pair again.
- **Revocation and rotation.** Revoking a node deletes its key, nonces and pending enrolment, so its later
  signatures fail (`node_revoked`). Rotating a key means enrolling again.
- **Back-compatibility.** Nodes enrolled before signing existed have no key. They must re-enrol: session actions
  answer `409 reenrol`, and Pocket and the console say so.
- A session is a chain of continued tasks, named by its root task id. A session has one owner at a time; the
  primary key of `autonomy_sessions` guarantees it. Ownership is a 15-minute lease renewed by the owner's actions.
- Another node can get ownership in one of two ways:
  - `take`: only when the session is unowned or the lease has expired;
  - explicit hand-off: the owner offers the session to a named node of the same family, and that node accepts
    within 5 minutes. The transfer is one conditional `UPDATE`.
- While a session is owned, no other device of the family can cancel or continue it (`409 owned`). Reading stays
  open to the family.
- Revoking a node releases its sessions and deletes its key.
- Tests cover:
  - a race of 8 concurrent `take` calls (exactly one owner);
  - hand-off and accept, revocation, and family isolation (another family gets 404 on every route);
  - canonicalisation, both algorithms, replay, stale timestamps, wrong family, revoked nodes, tampered bodies,
    unsigned requests from signed nodes and re-enrolment (`tests/webcortex/test_signing.py`,
    `tests/test_pocket_signing.py`);
  - Spark-signed replies and pinning in JavaScript (`tests/js/signing_check.mjs`);
  - enrol, claim and hand-off in Chromium with Ed25519 and with the P-256 fallback, and a tampered reply
    (`tests/e2e/test_pocket_signing_e2e.py`).

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

RC2 re-run after the UAT T1 fixes (`autonomy_live.json`, section `rc2`): a fresh scratch individual with two user
research tasks first (predictive coding; "Is numpy 2.5.5 stable?"), then the owner's hypothesis "scipy 1.18.1 was
released" (p = 0.5), 60 searches / 150 pages, `idle_interval` 0, five `nsf web work --once --max-tasks 1` runs:

- Before autonomy started, the owner's hypothesis ranked 1st (in RC1 it ranked 15th, then disappeared). It was
  investigated first, and it is now `supported`.
- The other four picks stayed on the user's subjects: a NumPy 2.5.5 patch-release disagreement (depth 0), a NumPy
  1.18.1 announcement (depth 1, overlap 0.67), and the StringDType ABI change (depth 0, then depth 1, overlap 0.86).
  No pick was more than one step from the owner's records.
- After the runs, 38 candidates were refused with stated reasons:
  - 12 were sentences that depend on their source text;
  - 11 were two steps from the owner's records;
  - 9 were off the owner subject they came from;
  - 4 were cooling down;
  - 2 were not statements.
- Each task opened 5 hypotheses (the cap). None came from a record title.
- Residual: one depth-1 pick was a mailing-list greeting ("Hi ... on behalf of the NumPy team ...") about NumPy
  1.18.1. It passed the relevance test through the shared words "1.18.1" and "released". It stayed on topic, but it
  was low value.

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
- Days-long unattended operation (RD-54) is verified in a 7-day accelerated simulation and a wall-clock soak that was
  started on 2026-11-22 and is still running (section 9). A multi-day wall-clock result is owner-side until that soak
  has run for days. Notifications reach a phone only while Pocket is open (RD-22).

## 9. Soak results

### 9.1 Accelerated: 7 simulated days (`scripts/soak_sim.py`, 2026-11-22)

**What ran.** The production scheduler, worker loop (`scheduler.work` with its idle investigator), `scheduler.tick`, orchestrator, D4 admission and settlement, and consolidation, on real SQLite.

**What was simulated:**

- the clock: injected, advanced by the worker's poll sleep and by 2 s for each search;
- the web: FixtureWeb with 12 topics × 3 dated pages, and a CorpusProvider as the search engine;
- the owner: 8 hypotheses with predictions, 2 monitors every 6 h, and 3 questions a day.

The budgets were the defaults: autonomous 20 searches, 60 pages and 1800 s per UTC day; user 400 and 1200.

**Injected faults:**

- a provider outage (day 2, 06:00–12:00 UTC: every search fails);
- a partial outage (day 5, 00:00–03:00: one search in two fails);
- a page outage (day 4, 09:00–15:00: two of three hosts answer 503);
- a worker restart every day at 12:00 UTC;
- a crash in the middle of a task (day 3, 12:15 UTC, after its reading stage).

| | Result |
|---|---|
| Wall time | 71 s for 7 days (2026-11-23 06:00 to 2026-11-30 06:00 UTC) |
| Tasks | 116: 39 autonomous (curiosity), 56 monitor, 21 owner; 111 completed, 1 partial, 3 failed (the full provider outage), 1 queued at the end |
| Autonomous use per UTC day | 20 searches every day (the cap), 25–28 pages, 40 s; never above a limit |
| Midnight reset | autonomous research resumed on every one of the 7 following UTC days |
| User budget | 18–25 searches and 54–73 pages a day, separate from the autonomous one |
| Ticks | 96 on every full day (every 15 min, no drift); idle decisions never closer than `idle_interval` |
| Ledger | every row on the UTC day its task started; no open row of an ended task |
| Crash recovery | the crashed task was resumed by the next worker after its lease expired; 9 worker starts |
| Provider failures | 16 searches failed during the outages; the 3 tasks that found nothing failed with a stated reason |
| Leases | no running task with an expired lease at the end |
| DB size (instance + cache + WAL), end of day | 2.9, 6.0, 4.9, 6.9, 6.8, 6.6, 10.3, 8.1 MB: grows with the knowledge it holds and falls back at WAL checkpoints |
| Python heap growth per day | 0.2–1.2 MB, last day −0.6 MB; RSS 95 → 104 MB |
| Decision log | 4980 events; 1640 "refused" idle decisions (about 234 a day) |

All six checks passed: budget, reset, drift, growth, leases and decisions. The gate runs 3 simulated days (`tests/webcortex/test_soak_sim.py`).

**Observation for the owner.** Once the day's budget is spent, the idle investigator records one "refused" decision every `idle_interval` (300 s) until midnight UTC, about 234 rows a day, roughly 85 000 a year. That growth is bounded and linear, but it is noise in the decision log. Raising `idle_interval`, or logging the refusal once per day and reason, would remove it.

### 9.2 Wall-clock: the `nsf-soak` session (started 2026-11-22 01:23 UTC, still running)

**Setup.** The scratch instance lives in `lane_deploy_live/soak`. It has web on, autonomy enabled by the owner, SearXNG at 127.0.0.1:8888, 5 owner hypotheses and one 6-hour monitor. The code is a snapshot of 94ccb09 in `soak/code`, so later edits do not change the soak. The budgets are the defaults (20 searches, 60 pages).

**Units** (transient user units, all under the `neuroscifi-webcortex` and `neuroscifi-webcortex-tick` sandboxes through `scripts/hardened_run.py`):

- `nsf-soak.service`: `nsf web work`;
- `nsf-soak-tick.timer`: `nsf web tick --queue` every 15 min;
- `nsf-soak-sample.timer`: `scripts/soak_report.py --append` every 10 min, to `soak/samples.jsonl`.

**Report:**

```sh
L=…/scratchpad/v07/lane_deploy_live
python3 $L/soak/soak_report.py --db $L/soak/instance.sqlite --unit nsf-soak --samples $L/soak/samples.jsonl
```

**Stop and remove:**

```sh
systemctl --user stop nsf-soak-tick.timer nsf-soak-sample.timer nsf-soak.service
systemctl --user reset-failed 'nsf-soak*' 2>/dev/null
```

The units are transient, so nothing is left in `~/.config/systemd`. They do not survive a reboot or a logout without lingering, which was not enabled.

**First hour** (report at 01:43 UTC):

- The worker had been active with 0 restarts.
- Autonomous: 4 curiosity tasks (3 completed, 1 partial) used 17 of 20 searches and 28 of 60 pages, with 15 SearXNG requests and 96 upstream engine requests.
- User: the monitor ran once (2 searches, 2 pages).
- No running task, no open ledger row, no expired lease.
- 167 claims from 30 sources. Files grew from 5.1 to 7.7 MB. Worker RSS 138 → 140 MB.
- All four checks ok.

By 01:47 UTC the day's autonomous budget was spent: exactly 20 of 20 searches (36 of 60 pages) over 5 curiosity tasks, none beyond the cap, with no task running and no open ledger row. The worker then waits for the 00:00 UTC reset.

The report at hand-off is in the lane's final message. Days-long wall-clock results are owner-side: read the report after a few UTC midnights. Expect `autonomous_used.searches` ≤ 20 on every day, a new day's row after each 00:00 UTC, `restarts` 0, and flat RSS.
