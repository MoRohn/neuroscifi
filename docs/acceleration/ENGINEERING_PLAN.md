# Engineering plan: what was changed, what was decided, and what is left

Status: 2026-11-24. Phases 1 and 2 on `nvidia-acceleration-review` (based on `9e4570a`, 0.7.0), merged to `main` as
`793a21f`; phase 3 on `open-items-round`, based on `793a21f`. Finding numbers refer to
[ARCHITECTURE_AUDIT.md](ARCHITECTURE_AUDIT.md) §4; numbers to [PERFORMANCE.md](PERFORMANCE.md).

This page covers three phases. Phase 1 ended with six decisions left to the owner (§4 as it then stood). The owner
delegated them the same day ("analyze the decisions and proceed"); phase 2 decided each on a measurement, built it,
and put it through independent reviews. Rows added in phase 2 say so. Phase 3 (2026-11-24) took the open items
that phase 2 left ("analyze the open items … and proceed"): §5 says what was built, what the reviews found and what
was left open on purpose; §6 is the list after it.

Order of priority applied: impact × confidence × feasibility ÷ (complexity + risk + maintenance), under the
directive's hierarchy: correctness and identity first, then reasoning quality, memory, reliability and safety,
then latency. Two consequences run through this page: a change that could alter what the individual remembers or
contacts was either made stricter-only or given a switch that restores the earlier behaviour, and no default was
changed for speed alone when it removes something the owner currently sees.

## 1. Done, on by default

Each row: the smallest change that fixes the finding, how it was accepted, how to undo it.

| # | Finding | Change | Accepted by | Rollback |
|---|---|---|---|---|
| 1 | C1 restore resurrects a forgotten epoch | A restore replays epoch forgets recorded in the deletion log, on copies older than the forget; the forget-event path no longer restores revision causes naming a forgotten event; a forget that removes derived rows marks archives stale even with no live evidence | Tests that failed first, for every derived table (`tests/instance/test_checkpoints.py`, `test_erasure.py`) | Revert the commit; no schema change |
| 2 | C2 the note survives `forget_epoch` | The note is cleared, also in a restored copy (the row stays: evidence and tombstones refer to the name) | Test | Revert |
| 3 | C4 `graph_effects` survive `forget_dataset`; the dataset's free-text note survived too (found while fixing C2) | Both deleted in the cascade and in a restore | Tests that failed first | Revert |
| 4 | C5 concurrent migration | Write lock first, then re-read the version, rollback on error | Deterministic two-connection test | Revert |
| 5 | C8, C9 | Closed-form lesion (equal to 1e-15 on a test graph); an oversized dataset is refused with a clear message | Tests | Revert |
| 6 | W4 an abstaining model reported as rejected | Truthful reason and `abstained: true`; the status value is unchanged | Test; 2 to 4 abstentions labelled on the benchmark | Revert |
| 7 | W1b memory answers the wrong property | Before memory answers without the web, the answer model confirms the chosen statements (`memory_gate.py`). It can only send a question to the web. Nothing is asked or recorded when no model is configured | 86 of 96 right, 1 of 288 wrong accepted (one call per statement); orchestrator tests with the reproduced wrong answer | `nsf web config --no-memory-check` |
| 8 | R4 the owner waits behind background prefill | vLLM priority scheduling and a 4096-token step; the gateway sends `priority` for autonomous tasks when `background_priority` is set (`nsf setup` sets 10 for a vLLM endpoint) | Two runs each: first token under load −35 %, no single-stream change | `NSF_VLLM_SCHEDULING=fcfs`, `NSF_VLLM_BATCHED_TOKENS=` in `~/.config/neuroscifi/inference/vllm.env`, restart; `nsf llm config --set background_priority=0` |
| 9 | R5a a queued question waits for a poll | The worker wakes when another connection commits a queue change; the 5 s poll stays as the fallback | 2.73 s → 0.05 s mean | Revert |
| 10 | W6 queue order | Owner-asked tasks are claimed first, oldest first within a class; an autonomous task queued over 10 minutes competes by age again | Tests | Revert |
| 11 | R2 Pocket voice invisible to the resource policy | Pocket publishes the audio beacon; console and Pocket merge their entries in one file; the audio check covers every session | Tests | Revert |
| 12 | R6, R7 voice ordering and a lost chunk | A reply generation per turn: an older reply is shown, not spoken. The hub accepts a repeat of the last chunk once; the client retries once | Tests; the Node checks | Revert |
| 13 | R1 reload ignores the deadline; no memory check | A reload waits no longer than the caller may and continues in the background; a reload that would leave less than the critical threshold free is refused and recorded; `run.sh wait` fails at once on a crash loop | Tests that failed first | Revert |
| 14 | Missing telemetry | `nsf resources` and `GET /api/resources`: GPU temperature, power, throttling, load, the model server's queue, prefill, decode, first-token and prefix-cache state (lifetime and since the last reading). The ledger: provider, priority, cached tokens; an index. `nsf llm status`: cache share and calls by purpose | Tests; read against the live server | Revert; the ledger's added columns are ignored by older code |
| 16 | W5 pages read one at a time (phase 2, decision B) | Read-ahead is the default (`read_ahead` 4): while one page is read, up to three further pages, one per site, are fetched ahead, and read, charged and reported in the original order. The pages fetched ahead of a task share its request and byte budget; a page fetched ahead never opens a second connection to a site an exchange is in progress with. Curiosity, refresh and MCP tasks read one page at a time, and the daily allowances count unused traffic | Offline benchmark identical at 1, at 4 and at the default; 600 random small webs under tight budgets, and after the last fix 1,800 more with redirecting robots.txt files: 0 differences (the one case the third review pass found was fixed and confirmed by a fourth); task p50 with 300 ms per response 2.05 → 0.96 s on a quiet host | `nsf web config --set read_ahead=1` |
| 18 | W9, W10 a stop took effect late (found in phase 2; on the one-at-a-time path of 0.7.0 as well) | Every page fetch, the reader's own included, asks before each name lookup, each connection and each request whether the task was cancelled, web access turned off or the site denied, and again on the open connection before the request is written. Requests on the wire are cut within about 0.25 s. Crawl robots.txt and sitemap reads are inside the same gate | Requests reaching sites after a cancel / web access off / a deny: 1 / 11 / 11 at one page at a time and 4 / 8 / 8 at four before, 0 in all six after; a cancelled run ends about 0.25 s after the cancel (before: when its slowest request ended, up to the 15 s timeout); the stop tests 30 of 30 runs | None: the old timing contradicted the stated property |
| 19 | W3 the critique marks the code's own hedges (decision A) | The critique reads only sentences that are not one cited claim word for word with the attribution the code appended (`reasoning.restates`, a character-for-character comparison), and makes no call when none is left; `reasoning.critique` records `judged` and `restatements` when a sentence was left out | The stored 0.7.0-scope run re-read: 79 of 90 kept sentences are such restatements and carried 42 of the 46 marks. Live, 51 tasks: 9 to 11 critique calls instead of 44, 4 marks instead of 42, none on a restatement, 0 sentences removed in every run; task mean 4.13 → 2.59 to 2.80 s | `nsf llm config --critique-restatements` |
| 20 | W11 a shown sentence could lose the words after its own attribution (found in phase 2; in 0.7.0) | A sentence whose trailing "according to <source> …" would take further words with it is refused with a plain reason instead of shown cut. The answer path and the guard are otherwise the code of `307b907` | A committed differential over 1,476 inputs recorded from `307b907`'s own code: 1,459 identical, 17 refused (all from the review corpora), 0 different; 0 of 248 benchmark sentences lost; the 827 guard tests of `307b907` pass unmodified | None: it only refuses |
| 21 | C3 learned state expires (decision D) | Schema 14, ADR-044: when a learning event's lifetime ends, the sweep keeps a consolidated reading (journal position, epoch, channel, root, corrected reading, sample id) for every expiring copy with consent to learn; beliefs, change detection and epoch matching read raw events and readings as one set, the earliest copy of a sample counting once. Forgetting stays exact: by event id (through the tombstone), by journal position, by partner, by camera, by epoch | Posteriors equal (`==`) before and after expiry; 80 randomised lives against a twin that never expires: 0 differences; 38 safeguards broken one at a time, 38 caught; restore fuzz through a second generation: nothing forgotten returns | `nsf config --no-consolidation` (an expired event then leaves nothing, as in 0.7.0; readings already kept stay until forgotten) |
| 22 | C7 the ask budget never refills; C12 instance settings could not be changed after `nsf init` (decision E) | The budget counts the questions of an epoch asked in the last `ask_budget_window_seconds` (7 days). New command `nsf config` (ask budget, window, consolidation); a changed setting leaves an audit row | Tests at the window's edge; settings saved by 0.7.0 read the default | `nsf config --ask-window-days 0` (the old rule) |
| 23 | C11 a restored copy reused evidence positions; a restore reverted the owner's settings (found in phase 2) | A restored copy continues the live file's evidence sequence and takes the live file's consolidation, ask budget and window (`settings_carried`); a value that does not validate is not carried and is reported (`settings_warnings`); a saved `consolidation` that is not true or false reads as off | Restore tests through a second generation; the security review's script: the copy of an opted-out individual consolidates nothing | Revert; `apply_tombstones=False` stays a plain copy with the archive's settings |
| 24 | C6 the sparse learner's graph was discarded; C10 the dense learner fails beyond about 100 populations (decision F; C10 found in phase 2) | The edge exchange runs for all receivers at once, bit for bit the lab's loop. The promotion gate has a sixth check no rule can waive: a snapshot must beat a graph with no edges. New individuals are born with `best-learner-v1` beside `gate-v1`: the sparse learner's graph is a candidate and the tolerance is measured against the better learner (ADR-045) | Fit 2.4 times faster at 24 populations, 1.7 at 256; 43,000 random cases identical to the loop; at 160 to 256 populations a working graph (validation error 0.0043) is promoted where `gate-v1` produced one worse than no graph; reproduction 8 of 8; acceptance 7 of 7 | Existing individuals are unchanged. A new one goes back with `nsf graph rules add --builtin gate-v1 --as gate-v1-again --owner-approved` |
| 25 | C13 a scoring rule with `"bits": [4.0]` validated and then crashed packing (found in phase 2; older) | Bit widths and degrees must be lists of integers | Tests that failed first | Revert |
| 26 | R11, R12 the reload path (found in phase 2) | A systemd unit the idle policy stopped is started again as that unit (before: `run.sh start`, a second, unsupervised container beside it). A wait does not call a container gone before it could exist (60 s grace). A running container counts as loaded only once it answers, and a start that never becomes ready is stopped. The retry limit and the single start hold across processes | Live with a transient user unit: the old wait died after 130 ms with "container is gone", the fixed one reported ready after 35 s; tests that failed first | Revert |
| 27 | R13 a model server's key and prompts went to whatever held its loopback port while the container was down (found in phase 2) | Before every request to a loopback port of this deployment's own services, the gateway reads who owns the socket that would accept it (`/proc/net/tcp`); a process of another account gets nothing and the call fails as `unavailable`. `deploy/memory/run.sh` sends its warm-up request only when Docker reports the container publishing the port | Tests with kernel-shaped tables and real sockets; live: a stand-in on the freed port received no request with the key. Limits in §5.3 and §6 item 2 | `NSF_LISTENER_CHECK=0` |
| 28 | W1a, setup (decision C) | `nsf setup` has a `memory` step: when the two memory models answer and no memory setting was made by hand, it switches semantic retrieval on for the individual; without their key file it contacts nothing | Live on a temporary individual: done, then "already done" on the second run; tests | `nsf setup --no-memory`; `nsf web memory config --disable` |

### Found by the independent reviews of phase 1 and fixed

Two reviewers that did not write the changes read all of them, twice (the second pass checked the fixes of the
first). Each row has a test that fails on the code before the fix.

| Finding | Fix |
|---|---|
| **A second-generation restore resurrected a forgotten epoch.** The first fix of C1 replayed the forget into a restored copy but left no record there, so an older archive restored *from that copy* brought the revisions, decisions, question, links and the owner's note back (reproduced by both reviewers) | The copy carries the forget in its deletion log, dated in its own history, and marks the archives it lists stale; a later epoch of the same name created in the copy is kept |
| **The telemetry scrape could hand the model server's key to another host**: urllib resent the bearer key to the target of a redirect and to an environment proxy (both reproduced) | The scrape follows no redirect and uses no proxy; the idle policy's scrape uses no proxy either |
| **A memory model was sent the answer model's key** when configured without a key file (reproduced) | Only the answer model falls back to the default key file; `deploy/memory` documents `--key-file` |
| **One statement could sway the memory check for the others** (all statements shared one prompt), and the statements were not marked private | One call per statement; the message is marked private, so a model off this host needs the private-memory authorisation. Cost: 86 instead of 94 of 96 right facts accepted |
| A memory answer could lose the facts it was approved on when a second lookup ranked differently; the check also ran for arithmetic | Approved items are loaded by id; arithmetic is not checked |
| Twelve aged background refreshes could run ahead of the owner as a block | At most one aged autonomous task per ten minutes passes the owner's |
| A repeated audio chunk with different bytes was acknowledged and dropped; a text-only session could report "user speaking" and hold audio priority | A repeat must be the same bytes; "user speaking" needs the microphone lease |
| One memory measurement skewed by another tenant (a "100 GiB" model) would refuse every later reload | A measured footprint above 1.5 times the documented one is not believed |
| A slow embedding call held every memory lookup of the process; a late reader could re-add a forgotten statement's vector; vectors were shared across endpoints of one model name; the ledger's upgrade ran on every call and swallowed "database is locked" | The model is called outside the lock, a reader of older memory stores nothing, the store is keyed by endpoint, forgetting a statement empties this process's vectors, the ledger upgrades once per process and fails loudly |
| `deploy/memory/run.sh wait` did not fail on an exited container and an unknown service name exited 0 | Both fixed |

### Found by the independent reviews of phase 2 and fixed

Four reviewers that wrote none of the changes read phase 2 (the core, the web-reading path, the runtime with the
critique and the graphs, and security and privacy across all of it), reproduced what they suspected, and read the
fixes again; the web-reading path was read a third time. Each row has a test that fails on the code before the fix.

| Finding | Fix |
|---|---|
| **Forgetting a sample's first copy after expiry also lost what its later copies taught** (a count of 3 where forgetting before expiry, or no expiry, gives 4). The first version kept one reading per sample | Every expiring learning copy leaves a reading; which one counts is decided when beliefs are read. Cost: a reading takes 96 bytes instead of 77, and `compute()` at 100,000 readings 24 ms instead of 12 |
| **A checkpoint restore turned consolidation back on** for an owner who had turned it off | The live file's settings travel into the copy (row 23) |
| **One consolidated reading could not be forgotten in practice, and a camera not at all** short of its epoch (the listing showed a position, the forget needed the event id) | `nsf forget consolidated SEQ`, `nsf forget camera NAME`, the same in the console; a restore replays both, and a forgotten camera or partner leaves no empty entry in a restored copy |
| Lookups by root scanned every reading (a sweep took 5.3 s at a million readings); the copy rule ran one query per copied sample (`compute()` 5.4 s at a million readings each recorded twice) | An index by root (0.08 s); the rule in passes over one index (0.79 s) |
| A restore failed on a damaged settings row; the "evidence through #N" number of a question fell at a sweep; an oversized position gave a server error | The restore carries nothing from an unreadable row and says so; the horizon is monotone; a validation error |
| **A request could be sent after a stop that landed while its connection was being opened** | The gate is asked again on the open connection; nothing is written and nothing counted |
| **A page fetched ahead that redirected onto the site being read opened a second connection there** | A site is busy while an exchange with it is in progress; the fetch ahead stops and its claim continues from that hop |
| **The pages fetched ahead of a task each spent the same remaining budget** (12 requests under a budget of 4; 21 under 10; 2.5 MB of unused bodies) | One shared account per task; a fetch ahead starts only with room for robots.txt and the page. 4 under 4; unused requests at most the budget, since phase 3 at most an allowance of a tenth of it (§5.1) |
| A "slow down" answer to a page fetched ahead skipped every other page of the *earlier* site and blamed its crawl delay | Only pages that lead onto the site that asked are left out, and the report names that site and reason. This is the one stated exception to "identical to one page at a time" |
| A cut the gate did not confirm (one failed read by the watch) was stored as "robots.txt disallows everything" for 24 hours in a fetcher shared by later tasks | A watch that cannot be read cuts nothing; an unconfirmed cut ends as a new error `interrupted` that leaves no verdict, back-off or cache entry |
| A stop during the reader's TLS handshake left one request charged that was never sent | Refunded on every path where nothing was written |
| **The restatement test took factorial time** when citation markup attached nine claims to a sentence (68.7 s), outside the task's time budget | No search over orderings; in the final rule a restatement is exactly one cited claim |
| **An attempted fix of the answer path regressed the guard**: keeping the words after an attribution and exempting a word-for-word clause let five sentences that invert an exception through, and let a hostile claim's "according to …" count as support (found by the last security pass) | Withdrawn. The answer path is the code of `307b907` again with the one refusal of row 20, held there by the differential test |
| A reload of a stopped unit failed its own wait; a failed reload left the container running and the next one counted it as loaded | Row 26 |
| The first listener-owner check refused legitimate endpoints (a listener on another address, a server under its own account) and trusted the overflow uid | Only the socket that would accept the connection counts, only this deployment's own service ports are checked, uid 65534 is neither trusted nor refused; one parse of the tables serves a second of checks |
| Wake requests could not work across two accounts (every file private to its creator), and an entry the wake unit could not remove ran it into its start limit | The asking process needs no access to the policy's folder: a refused connection to a service port leaves the request. Regular files only; anything else is removed or set aside (1 start where there were 29) |

## 2. Done, off until the owner turns it on

| # | Finding | Change | Why off | Turn on |
|---|---|---|---|---|
| 15 | W1a lexical memory retrieval | Semantic retrieval: an embedding model's candidates, a reranker's order and floor (`semantic.py`; `deploy/memory/`) | It needs two services and 8.7 GiB of memory, and makes a lookup 107 to 111 ms. Paraphrase recall@1 0.27 → 0.98. Since phase 2 it switches itself on at setup once the services answer (row 28) | `deploy/memory/run.sh start && deploy/memory/run.sh wait`, then `nsf setup --db PATH` (or by hand: `nsf web memory config --enable …` as in the runbook) |
| 17 | W3 the critique's cost for spoken answers | `critique_voice`: the critique pass can be skipped for spoken answers | It removes marks from a stored report. Since row 19 a spoken answer pays for a critique only when it holds a sentence of the model's own (9 to 11 of 51 tasks) | `nsf llm config --no-critique-voice` |
| 29 | The memory models were unsupervised and outside the idle policy (decision C) | Units `neuroscifi-memory-embed.service` and `-rerank.service` (`deploy/install.sh --memory`; since phase 3 `neuroscifi-memory.service` for the single server, the default); services `embed` and `rerank` of the idle policy. A lookup that finds a model unloaded is answered from the lexical ranking in 2 to 4 ms with `retrieval.waking`, and the models are back about 20 s later | Units are the owner's to install; an idle timeout releases memory at the price of the first lookups after a pause being lexical | `sudo bash deploy/install.sh --memory --memory-user USER`; `nsf resources idle --set embed=15m --set rerank=15m` |
| 30 | R10 an unloaded service could not be reloaded on demand from the hardened console and worker units (found in phase 2) | Wake requests: with `NSF_WAKE_DIR` set, the unprivileged process leaves an empty `<service>.wake` file and `neuroscifi-wake.path` / `.service`, running as the account that owns the services, performs the load under the policy's own rules | Needs the units installed. Since phase 3 the installer arranges the access two accounts need (§5.1). **Unverified as installed system units** | `sudo bash deploy/install.sh --idle [--idle-user USER]` |
| 31 | C6, C10 for individuals created before phase 2 | The built-in rule `best-learner-v1` can be adopted with one command | An existing individual's rule is part of its history and is not changed for it | `nsf graph rules add --builtin best-learner-v1 --owner-approved` |

## 3. Rejected on measurement

Tried, measured, not adopted. Recorded so that they are not tried again without a new reason.

| Idea | Result | Evidence |
|---|---|---|
| `--performance-mode interactivity` | No change in decode or loaded latency; 25 s slower to start | `accel_vllm_flags.json` |
| A step of 8192 tokens | No better than 4096; doubles the longest step a new request waits behind | same |
| Speculative decoding by prompt lookup | Restating decode +20 %, free-form decode halved, research task mean 4.4 → 5.6 s | `accel_rejected_speculative_*.json` |
| Fusing lexical and semantic ranks | Paraphrase recall@1 0.48 against 0.94 for the semantic order alone | `accel_after_runtime.json` |
| A reranker as the judge of "memory answers this" | Accepts 11 to 60 of 288 wrong-property facts and both reproduced wrong answers | same |
| Requiring the model to write at least one sentence (suggested by the audit for W4) | Not built: the empty answers are correct abstentions on questions the evidence does not answer | `accel_baseline_pipeline.json`, tasks T04, T08, F25 |
| NIM, Triton, Dynamo, TensorRT-LLM for the answer model | [NVIDIA_ASSESSMENT.md](NVIDIA_ASSESSMENT.md) | — |
| Counts per epoch, channel and segment as the durable form of expired evidence (the audit's sketch for C3; phase 2) | Not built. A count cannot be cut at a change the owner confirms later, cannot tell which sample it already holds (a later copy would count again), and cannot give back one event | ADR-044, alternatives |
| One consolidated reading per sample (the first copy only) | Built first and replaced: forgetting the first copy after expiry lost the later ones (n = 3 against 4) | `accel_consolidation_cost.json`, `first_version_only_first_copy_kept` |
| Keeping the words after the model's attribution in the composed sentence, and letting a claim's clause pass the guard when copied word for word | Withdrawn after review: five sentences inverting an exception and two hostile-attribution sentences were accepted that `307b907` rejects; the comparison with that commit showed 87 differences | `tests/inference/fixtures/guard_reference.json`, `scripts/guard_differential.py` |
| Counting several claims joined by "and" or ";" as a restatement | Dropped: a joined fragment can say what neither claim says alone. A restatement is one claim; 2 of 958 stored sentences change class | `accel_decisions_critique_scope.json`, `one_claim_rule` |
| The sparse learner's graph as one more candidate under `gate-v1`'s reference | Chosen in 64 % of lab worlds at the same 247 bytes for a held-out error 0.3 % lower, and in no other configuration: the reference is the dense fit, and a cut of a bad fit is always within tolerance of it. The rule that was adopted measures against the better learner | `accel_decisions_graph_learner_select.json` |
| In-place buffers and support-only updates in the graph learner | Bit-identical, no gain worth its guard (0.0209 s against 0.0193 s at 256 populations) | Lane measurement |
| Handing out the shared read-ahead allowance in candidate order | Moves the waste to the next fetch. Instead a fetch ahead reserves a typical fetch at its start and one that stops before a request is continued by its claim: 34 → 22 requests in the reviewer's worst case under a budget of 20 | `accel_decisions_read_ahead.json`, `second_pass` |
| Charging unused read-ahead to the task's own budget | Not done: it changes where a task ends, so reports would differ from one page at a time | — |
| A smaller key-value cache, `MALLOC_ARENA_MAX=2`, a shorter maximum length for the memory models | vLLM refuses to start under 0.22 GiB at a 2,048-token input; 2.64 → 2.69 GiB; at most 0.13 GiB per service for rejecting long statements. The footprint stays 4.6 GiB per service | `accel_decisions_memory_service.json` |
| A reload thread inside the process that looked something up | A one-shot command exits before the thread has started anything; the reload is a process of its own | same, `live_check` |
| A listener-owner check on every loopback endpoint | It refused a server under its own account and a port held on another address; scoped to the deployment's own service ports | `tests/test_listener_check.py` |
| `TriggerLimit*` or `PathChanged=` for the wake path unit | The first puts the path unit into a failure mode that stops the watch; the second does not re-check when the service ends. `PathExistsGlob=` is kept and entries that are not requests are set aside | The unit's header; systemd.path(5) of systemd 262 |

## 4. Decisions, as made

Phase 1 left six decisions with the owner. The owner delegated them on 2026-11-23; each was decided on a
measurement, and several of the rulings below were changed by what the independent reviews found. Every one has
a switch or a command that restores the earlier behaviour, named at its end.

**A. The self-critique pass: narrowed, not turned off.** The pass could remove nothing the guard had accepted, and
on the benchmark 42 of its 46 marks sat on sentences that are one cited claim word for word with the attribution
the code appended (79 of the 90 kept sentences; phase 1's count of 35 used a looser test that compared content
words). Such a sentence holds nothing of the model's own for a second reading to judge, and the hedge is the
code's. The critique now reads only the other sentences and makes no call when there is none. On the final code
and a quiet host: 10 calls instead of 45 on the 51 tasks, 4 or 5 marks instead of 49, none on a restatement,
mean task time 2.65 and 2.70 s instead of 4.16 s (2.41 s with the pass off). Two rulings followed from the
reviews. A restatement is exactly
one claim, compared character for character: joined claims, a changed capital and text with invisible characters
are always read. And the answer path itself was put back to what it was: an attempt to keep the words that follow
an attribution, made while fixing a review finding, let sentences that invert an exception through the guard and
was withdrawn; what remains is one refusal that can only remove a sentence (§1 row 20). Cost: the critic no
longer reads the restated sentences as context for the gaps it names; a faithful sentence that ends "… according
to a report by Reuters" is refused, where 0.7.0 showed it with the code's attribution. Back:
`nsf llm config --critique-restatements`.

**B. Read-ahead: on, at 4, after its two objections were removed.** It had been left off because fetches already
under way went on after a cancel or a denial, and because unused requests escaped the daily caps. Measuring the
first objection showed that the one-at-a-time path of 0.7.0 was worse than believed: the web settings were re-read
at most every 2 s, so a short run read every page after web access had been turned off (11 requests). Now no
request is sent after a stop on either path (0 in six cases): the gate is asked before every name lookup,
connection and request and again on the open connection, and requests on the wire are cut. What can still reach
a site after a stop is a connection being opened, with nothing sent on it. Tasks under a daily allowance read one
page at a time, and the allowances count unused traffic. Median task time with 300 ms per response went from
2.05 s to 0.96 s with every outcome identical. Stated exceptions and costs: a site that answers "slow down" to a
page fetched ahead is left alone at once, so pages that lead onto that site are missing where one-at-a-time
reading would have read them, and the report says so; a task's charged requests stay within its budget, but what
it sends ahead and does not use comes on top, up to the budget again (22 requests reached sites under a budget of
20 in the reviewer's worst case without owner action, 32 with a deny mid-run; 4 unused beside 350 used on the
benchmark). The one defect found by the last review pass was fixed and the fix confirmed (`989a384`). Since
phase 3 the unused part has an allowance of its own, a tenth of the budget (§5.1). Back:
`nsf web config --set read_ahead=1`.

**C. The memory models: yes, as a managed service.** Retrieval by meaning (paraphrase recall@1 0.27 → 0.98) is
worth its memory, but not as two hand-started containers. `nsf setup` finds the two models and switches them on,
contacts nothing without their key file and never changes a memory setting the owner made by hand;
`deploy/install.sh --memory` installs two supervised units; with an idle timeout set by the owner the policy
releases their memory when nobody uses them, and a lookup that finds them unloaded is answered from the lexical
ranking in 2 to 4 ms while they start again (about 20 s). The footprint itself, 4.6 GiB per service for 1.1 GiB
of weights, could not be lowered with settings of the pinned image; phase 3 replaced the two servers by one
process for both models (4.26 GiB in all, §5.1). Building this exposed four older defects of
the reload path, all fixed (§1 rows 26 and 27, §2 row 30). The NVIDIA embedding NIM remains the owner's licence
decision; any `/v1/embeddings` endpoint can be configured in place of the local model, and the idle policy and
the listener check leave such an endpoint alone. Nothing was installed on this host and the containers were
removed after measuring. Back: `nsf web memory config --disable`; `nsf resources idle --set embed=off --set
rerank=off`.

**D. Consolidation of expiring evidence (C3): built, on by default.** When a learning event's lifetime ends, what
it taught stays as a consolidated reading: its position in the journal, its epoch, channel and root, the
corrected reading and its sample id. The event's id, actor, action and bit, provenance and timestamps go as
before. Beliefs read events and readings as one set, so a posterior is the same number before and after its
events expire, with confirmed changes, samples and linked epochs. The audit's sketch, counts per segment, was not
built because counts cannot stay exact (§3). Forgetting stays exact and gained two handles (a reading by its
position, a camera by its name); a restore neither loses the learning nor returns a forgotten reading, through a
second generation, and it takes the live file's settings with it. This is retention of what was learned; it is
not rehearsal or replay, which stay deferred. Costs: about 96 bytes per reading (since phase 3 under a limit of a
million by default, §5.1); belief computation now grows
with an epoch's age (24 ms per 100,000 readings with a third of them sampled; 0.79 s at a million
readings that were each recorded twice); and a privacy change: a root's name and a sample id now stay in the file
until the reading is forgotten, where 0.7.0 scrubbed a root when its last event expired. An individual upgraded
from 0.7.0 gets consolidation on; `docs/operations/MIGRATIONS.md` section 2 says how to opt out before anything
is swept. Back: `nsf config --no-consolidation`.

**E. The ask budget (C7): a rolling window.** The budget counts the questions asked in an epoch in the last 7
days, answered or not. The owner's attention recovers with time; counting only open or answered questions would
let ignored questions be asked again without bound, and a window bounds interruptions either way. An epoch
shorter than the window behaves exactly as before. Instance settings could not be changed after `nsf init` at
all, so the ruling needed a new command, `nsf config`. Back: `nsf config --ask-window-days 0`.

**F. The sparse graph (C6): measured, then decided differently from both options offered.** Phase 1 offered
"promote the sparse graph or make the pass optional". The measurement corrected the premise (the exchange loop
was 20 to 66 % of a fit, not four times it; it now runs for all receivers at once with the lab loop's result bit
for bit) and found something worse: the dense learner stops fitting beyond about 100 populations, while
`datasets.add` accepts 1024. Under `gate-v1` a snapshot no better than a graph with no edges was promoted as the
first graph of a context from 160 populations (about 14,000 times worse at 256). Two rulings: the gate refuses
such a snapshot under any rule, and new individuals are born with `best-learner-v1`, which packs whichever
learner fits better. At 24 populations, the only size a shipped data source produces, nothing measurable changes
(the same 247 bytes; the sparse learner's weights in 64 % of worlds; held-out error 0.3 % lower); from 96
populations the promoted error halves. Existing individuals keep their rule and adopt the new one with one
command (§2 row 31). The byte budget stays 4096, so above 512 populations nothing is promoted, as before.
Recorded acceptance evidence was not regenerated: `growth_retention` would differ in nine leaves, listed in
`accel_decisions_graph_birth_rule.json`, and the first physical test stays on `gate-v1` as preregistered.

## 5. Phase 3: the open items

On 2026-11-24 the owner asked for the open items of the earlier list of next priorities to be analysed and acted
on. Each was ruled on before it was built (the ruling is in the second column), built by its own lane, merged, read
by three independent reviews in parallel (security across all of it; correctness of the web, knowledge, guard and
calibration work; correctness of the runtime, deployment and instance work), fixed, and read again where a fix
was security-critical. Branch `open-items-round`, based on `793a21f` (the merge of phases 1 and 2).

### 5.1 Built

| Open item | Ruling | What was built | Evidence | Back |
|---|---|---|---|---|
| Gaps of the sentence guard older than the branch (W12) | Corpus first; each change its own commit, stricter than `307b907` everywhere except one enumerated loosening, proven by the differential | A claim's exception or condition must stand in the sentence whole (`RESTRICTION_LOST`: except, unless, only if, provided that, assuming, subject to, given that and their variants); a word shared with the cited claims keeps their case (`CASE_CHANGED`); extraction removes characters nobody sees and drops the line, or the whole HTML paragraph, that holds a direction control unless the text is right-to-left, and the guard refuses a claim that still holds one (`UNSEEN_IN_CLAIM`); a passive clause that drops or swaps its agent is refused (`PASSIVE_SWAP`); a capitalised status word ("Retracted") is not read as part of a source's name; reasons are listed in one order. The one loosening: a sentence that is exactly one cited claim with the code's attribution is no longer rejected by the anchor rule alone (not for a claim that opens with a bare pronoun). The refusal of §1 row 20 now spares an attribution that only names its source ("… according to a report by Reuters") | 306 adversarial sentences in the gap corpus, each labelled before any change. Over 1,782 inputs recorded from `307b907`'s own code: 1,602 identical, 163 stricter, 17 loosened (all of the proven class, checked by a frozen copy of its rule in the script), 0 different. None of the 1,137 attack-suite inputs and none of the 248 benchmark sentences moved at any commit; a live run refused nothing by these rules (`open_items_guard.json`) | None: refusals only, apart from the proven class |
| Read-ahead's bounds | Cap what read-ahead may waste; once the cap is spent the task reads one page at a time, which cannot change an outcome. Bring every other request kind under the owner's stop | Unused read-ahead has an allowance of its own: a tenth of the request budget, never less than two requests per page of the window (8 at the defaults), held exactly across the task's fetches ahead. Searches, records read through a provider's API and a render's sub-requests ask the owner's stop before each request. A politeness slot nobody used is given back; a request cut while being written counts if any byte of it left; a paid search stopped before sending is refunded; denying one feed's site stops only that feed | Everything a task sends is at most its budget plus the allowance: 48 at the default budget of 40 (80 before). The reviewers' worst cases: 32 → 25 under a budget of 20, 24 → 17 to 19 under 12. 1,800 random webs with redirecting robots.txt files: 0 differences from one page at a time. Requests after a stop, for every kind: 0. Benchmark p50 0.98 s at 4, unchanged (`open_items_reading.json`) | `nsf web config --set read_ahead=1` |
| Contradictions in other words (W2) | The memory models only propose a pair; the existing value comparison decides; nothing is decided on similarity alone; a benchmark with a held-out half first | With semantic memory on and an answer model configured: the embedding model proposes candidates, the reranker must agree in both directions, the answer model confirms in one private call per pair (numbers masked), and the value comparison says whether the two agree or contradict. A contradiction is recorded and put to the owner as a question; it never supersedes knowledge or lowers its confidence. A same-value pair is reported (`merged: false`) and not merged: the statement is stored as its own item, as in 0.7.0 (a ruling of the integration, below). A value the owner rejected before is not asked about again | 132 probes over 32 fictional facts (`contradiction_paraphrase.json`). Words only: 0 of 86 found. Shipped: development 33 of 43 right, held out 31 of 43 right and 0 false pairs of 23, in two runs. Without the answer model's confirmation the held-out half had 3 false pairs; the confirmation was added after seeing that, so the held-out figure is of a design revised once (`open_items_knowledge.json`) | `nsf web memory config --no-contradictions` |
| `knowledge.query` rebuilt its index on every call (W8) | Keep the index; invalidate exactly | The lexical index is kept per individual file for the process and used only while the live statements equal the ones it was built from (one indexed read per query); at most 4 individuals and 120,000 statements per process. Every forget that removes or rewrites knowledge drops it, and the semantic vectors | Repeated query 200 → 31 ms at 20,000 statements, 1,136 → 193 ms at 100,000; results identical to the uncached path over random sequences of writes, forgets and queries on two connections | None needed: results are identical |
| Web questions never expired | A question left open for 30 days stays open and answerable but stops counting toward the cap | `web_question_stale_days` (default 30) | Tests at the 30-day edge; the per-task cap unchanged | `nsf web config --set web_question_stale_days=0` |
| The calibration loop (W7) | Close it only where no belief changes; the identity at a cold start; a held-out gain shown first | A forecast is read through how the settled forecasts of its kind fared (ADR-046): the identity below 20 settlements, held to the stated forecast by a prior, monotone, within [0.01, 0.99], computed on read. It **ranks** the autonomous investigations the stated forecasts admit and never admits or refuses one (an independent review found the first version deciding admission; fixed). The thresholds of provisional knowledge and the research-policy weights are not connected | Simulated forecasters, settings chosen on development streams only. Held out: mean squared distance to the true probability 0.0436 → 0.0165, Brier 0.2273 → 0.1999, no stream worse by more than 0.0011. Offline benchmark identical with it on and made inert (`open_items_calibration.json`) | `nsf web config --set calibrated_forecasts=false` |
| Consolidation's growth | A cap with an early, plain warning; no compaction into counts and no belief cache | `max_consolidated_rows` (default 1,000,000): at the limit a sweep keeps the oldest of its expiring events and the rest expire to nothing, as in 0.7.0; nothing already kept is removed. Warnings from 80 % in the sweep, `nsf regulation`, `nsf birth`, `nsf config` and the console. `nsf regulation` and the console's overview name the largest epoch and time its belief computation, only when asked (the console header, which reloads after every teaching action, does not). A restore reads only the live readings of its archive's own events | Restore peak at a million readings 549 → 232 MiB; the count for the limit 3.6 ms at a million; 54 safeguards broken one at a time, 54 caught (`open_items_consolidation.json`) | `nsf config --max-consolidated-rows 0` |
| The reload path and the installed two-account deployment | Make the two-account install work as installed and verify it with two real uids | An ownership model (one shared group; the state folder 0710, the database 0640, `<db>.resources/` 2770, the request folder 3770) arranged by `deploy/install.sh --idle`, every step inside the service account's folder run as that account; a polkit rule lets the idle account start and stop exactly the model services' units. The wake unit lingers to serve requests, so its starts are bounded by the clock. TTS and SearXNG count as loaded only once they answer. No home folder needed at import. `deploy/vllm/run.sh status` sends the key on standard input and only to its own container. A reload and the wait for the in-use lock come off the call's own time. The idle account opens shared files without following links and the database read-only with its schema untrusted. The voice hub's close is final and the Pocket control socket's thread ends; the test suite fails a session that leaves a thread of the package running | `scripts/two_account_check.py`, run by the suite in a user namespace with real uids and the real installer: 46 of 46 checks, 5 of them the root-escalation attack, which fail on the code before the fix. Wake unit under a request every 2 s: before, start limit hit with 30 of 35 requests unserved; after, 0 unserved. The polkit rule's own JavaScript evaluated over every action, unit, verb and caller (`open_items_deployment.json`) | `install.sh` without `--idle`; per item, revert |
| A lighter server for the memory models | One process for both models inside the image already pinned; the default only if the benchmark is equal and latency no worse | `deploy/memory/server.py`: one container on both existing ports, the GPU taken in first-come turns of one forward pass by either model, strict request parsing, at most 32 connections (a 503 beyond, delivered whole), a Docker network of its own. Selected by `NSF_MEMORY_SERVER` (default `single`); `deploy/install.sh --memory` installs its unit (`neuroscifi-memory.service`) or the two vLLM units. The idle policy treats one container under two names as one: an unload waits for calls under either name, and the more conservative of the two timeouts governs | Benchmark every class equal over two runs (paraphrase 0.979, unnamed 0.917); lookup p50 93 to 104 ms, p95 122 to 164 ms; ready 7 to 11 s against 54 to 69 s; 4.26 GiB against 9.33 GiB for the vLLM pair, measured side by side (4.47 GiB on the final server); an interactive embedding behind the largest accepted call 8.0 s → 390 ms (`open_items_memory_server.json`) | `NSF_MEMORY_SERVER=vllm` in `~/.config/neuroscifi/memory/memory.env`, then `deploy/memory/run.sh restart` |

**Rulings made while integrating the lanes**, not in a lane's brief: a same-value pair found by meaning is
reported, not merged, because the held-out half had been used once to revise the design and a wrong pair of two
properties that share a value would count its sources as independent support; the largest epoch's belief cost is
measured only when asked; `install.sh --memory` installs the unit of the configured memory server and the polkit
rule names all three memory units; two test-only defects were fixed (the Pocket signing test's 121 s margin, which
the timestamp's truncation could close; the hung-render test, which counted the resource tracker of the `spawn`
start method as surviving Chromium).

### 5.2 Found by the independent reviews of phase 3 and fixed

Each row has a test that fails on the code before the fix.

| Finding | Fix |
|---|---|
| **Critical: `deploy/install.sh --idle-access` gave the service account a path to root.** It ran `chgrp` and `chmod` as root on the database, its `-wal`/`-shm` and `.resources` inside a folder the service account owns, following links; planted links to `/etc/shadow` and `/etc/systemd/system` made them group-readable and group-writable (found by two reviews independently and reproduced with the real installer). Never released: it existed only on the round's branch | Every step inside the service account's folder runs as that account, so the kernel refuses what that account could not do itself; root acts only on root's own paths. The attack is part of the two-account check |
| A plain re-run of the installer undid the two-account arrangement | A re-run keeps it and says so |
| The voice hub's transcript writer could outlive `close()` (a write after close; a race between the swap and the stop) | A write after close runs inline; the swap and the stop are one step under the hub's lock |
| With the single memory server, the owner's unload of one name stopped the shared container under a call of the other; a timeout set for one name governed both | Every name's lock is taken; the more conservative timeout governs |
| The wait for the in-use lock was not bounded by the call's deadline (1 s asked, 4 s taken) | Bounded; a reload is reported only when one happened |
| The wake unit's bound held only if the command reached its linger; a malformed state file skipped it | The linger holds on every exit; a malformed file is read as empty and reported |
| The idle account read files the service account controls through links (a FIFO could hang it, `/dev/zero` exhaust it) and opened its database with the schema trusted | No links, regular files only, size-capped; the database read-only with `trusted_schema=OFF` and `query_only=ON` |
| A large rerank batch held an interactive embedding past its 2 s budget (8.0 s) | Turns of one forward pass (390 ms) |
| The memory server's parsing was loose (a duplicate or signed `Content-Length`), it started a thread per connection before the key check, and it logged raw paths | Strict parsing, a connection cap decided before any thread, a log of known routes only |
| The unused read-ahead allowance could be passed through a race when a fetch ahead was abandoned | The count and the release are one step under the fetch's lock |
| A direction override between an ordinary `<` and `>` was taken for a tag and survived ("72 mg" in the claim, "27 mg" on the page); other direction marks reordered digits but were only stripped | A tag only where HTML starts one; every direction control drops its line or paragraph unless the text is right-to-left |
| The restriction rule missed common condition words; a capitalised status word passed as part of a source's name; a passive clause could drop its agent (accepted by `307b907` too) | Added; refused; refused |
| The differential proved the loosening with the code under test | A frozen copy of the rule in the script |
| The calibration mapping decided whether a hypothesis was researched, not only its rank | Admission from the stated forecast only |
| A forgotten statement stayed in the kept index in memory; a paid search stopped before sending still counted against its daily cap | Every forget clears the index and the vectors; refunded |
| Two flaky tests in the gate (a stale-signature margin; a wake test without its fixture's pin) and a test-fixture thread leak in the browser suite | Fixed in the tests and the fixture |

### 5.3 Left open on purpose

| Item | Why |
|---|---|
| A TensorRT-LLM comparison | A multi-hour experiment that stops the answer model and changes its quantisation; a research prototype, not a fix ([NVIDIA_ASSESSMENT.md](NVIDIA_ASSESSMENT.md) §5) |
| Voice continuity across a restart (R8) and an offline outbox in Pocket | Need a real handset and microphone to be validated |
| Precision of the self-critique's remaining marks | Needs human labels; the marks are shown and never acted on |
| The dense learner's divergence above about 100 populations; demotion of a graph already active | Changes what is promoted for existing individuals, and no data source exceeds 24 populations |
| Digest labels of forgotten cameras and partners checkable offline; names and ids in tombstones | Needs a key kept outside the file, and the restore's replay depends on the tombstones |
| Identifying another account from inside `PrivateUsers=yes` units | Every other account reads as one overflow uid there; the listener check refuses nobody inside them |
| One connection per site across processes | Coordination between processes for a single-user system; stated as a limit |
| A belief cache, or a maintained list of copied samples | A second source of truth every forget, sweep, confirmed change and restore must invalidate; not needed below about a million readings, where the cap keeps a file by default |

## 6. Next engineering priorities

In the order of the directive's hierarchy: correctness first. Each item says why it is not done.

1. **Gaps of the sentence guard still open**, each pinned as "open" in the gap corpus so that a fix shows up in
   the differential: an exception written as a negated phrase ("for all users not on Windows"); a bound of time
   ("until May 2027": left out of the restriction rule on purpose, because its loss generalises over time and
   inverts nothing); a group named as an advisory's author ("according to the advisory from Windows Users");
   a pronoun of the sentence's own beside a capitalised claim word; versions or roles swapped between two clauses
   of one active sentence; an "according to the cited sources" set between commas, and the attribution worded from
   the sources counted before inline citations (both would change accepted text); a restriction after character
   600 of a claim (production cuts claims at 420). Right-to-left text: a line that uses a direction mark or
   isolate is read but never cited in an answer.
2. **Unverified in the installed deployment**: systemd running the units as system units with `PrivateUsers=yes`;
   polkitd acting on the rule (its JavaScript is evaluated in a test, not by polkitd); a real root and a real
   second account beyond the user-namespace check; the idle timer; a real reload through the idle policy against
   the single memory server. In a two-account install SearXNG's own commands write the instance database, which the
   idle account may only read, so SearXNG needs an account with both; the installer does not arrange that.
3. **Read-ahead** stays the first place to look if a research run behaves differently from 0.7.0, and
   `read_ahead=1` is the comparison. Stated residues: for a site whose robots.txt redirects to another site's file
   while that site is being read, robots.txt is sent twice (charged once); a continued fetch waits one extra
   politeness interval; one connection per site holds within one process; a query a metasearch daemon on this
   computer already holds and a dependency check's registry lookups are outside the owner's stop; unused traffic of
   fetches in flight when the process exits is not recorded (charged traffic since the last checkpoint is lost the
   same way).
4. **Contradictions in other words**: values in other units (11 of 16 missed on the benchmark) and aliases (6 of 6)
   go undetected; a first word that nothing writes inside a sentence is refused as a name; the held-out figure is
   of a design revised once, so a fresh set of subjects is the clean test before same-value pairs are merged. The
   pairing needs the answer model. A subject contained in another's name ("Paris, Texas") is stopped only by the
   answer model's confirmation.
5. **Graphs.** An existing individual on `gate-v1` still promotes, at 96 and 128 populations, a snapshot at twice
   the sparse learner's error; only adopting `best-learner-v1` changes that. A graph already active and failing the
   new check is not demoted. The dense learner's failure is not repaired. The other half of C6 is unchanged: no
   real data source and no consumer of a promoted graph outside the core.
6. **Consolidation.** Belief computation stays linear in an epoch's readings (bounded by the limit): an epoch whose
   readings all carry sample ids pays about 0.3 s per million readings on every read even when nothing was recorded
   twice. A restore still reads every tombstone of the live file into memory. The prototype's `legacy` status is not
   bit-exact once readings are consolidated (tested to 1e-12). A camera forgotten while a partner of the same name
   is held leaves the name in revision, question and decision text. Two files that both live on after a restore
   issue positions independently, and a restore from a divergent lineage loses learning rather than keeping it.
   Tombstones and the deletion log hold epoch names and event ids in clear, and the digest labels of forgotten
   cameras and partners can be checked offline against guessed names.
7. **The memory server**: against the vLLM pair the nearest statement is the same for 130 of 130 questions and the
   reranker's best for 129, but the ten nearest candidates are the same set for 104 of 130 (vLLM against itself:
   120), a drift below what the benchmark can see. Its containers can still open outbound connections (Docker
   cannot publish a port from a network without egress); the server opens none. A client that keeps 32 connections
   with prompt heads fills the cap until each head deadline (10 s) passes.
8. **Calibration**: the evidence is simulated, because no individual has settled predictions yet; for about 50
   forecasts after a forecaster's rule reverses, the reading is worse than the stated forecast (0.095 against
   0.043); settlements are treated as independent. A prediction merely settled by a task that is later forgotten
   keeps its outcome and still counts.
9. **The knowledge index and the semantic vectors are per process**: another long-running process keeps a forgotten
   statement's text until its own next query or exit; a repeated query still reads every live statement (172 ms at
   100,000).
10. **The critique's remaining marks** are a model's unconfirmed judgement on the 10 to 11 sentences it still reads
    (4 or 5 marks a run); no precision is measured.
11. **A TensorRT-LLM comparison** on this model with the harness of this review
    ([NVIDIA_ASSESSMENT.md](NVIDIA_ASSESSMENT.md) §5).
12. **Voice continuity across a restart (R8)** and an **offline outbox in Pocket**.
13. **Left open from the reviews of phase 1**, each low in impact and stated so it is not rediscovered:
    - *Voice.* Pressing stop on a reply also silences a reply to a question asked before that reply began to speak
      (it is still shown). Whether stop should let the newer reply through is the owner's call. A final chunk
      retried after the hub failed while closing the utterance is acknowledged as a duplicate.
    - *Resource policy.* An open Pocket voice session counts as a session for as long as the phone keeps it open,
      so a paired device can keep idle unloading from happening. Idle unloading is off by default.
    - *Forgetting, older than this review.* A graph revision invalidated by `forget_dataset` keeps its weights and
      metrics (the blob is immutable by trigger), and the dataset's digest survives.
    - *Two older tests are sensitive to a slow runner*: the cross-process race test (now spawned, 50 of 50 runs) and
      the hung-render test's 3 s allowance for a browser to die. Both kill what outlives them, so a failure ends a
      CI job instead of hanging it.

Done since phase 2 listed them: the guard gaps listed then; read-ahead's traffic bound and the stop for every
request kind; contradictions in other words (proposed and asked, not merged); the kept knowledge index; stale web
questions; the calibration loop where it changes no belief; the cap on consolidated readings and a restore's
memory; the two-account install, the wake unit's start limit, readiness for TTS and SearXNG, the home folder at
import, the key on `run.sh status`'s command line, a reload's time; one server for both memory models.
