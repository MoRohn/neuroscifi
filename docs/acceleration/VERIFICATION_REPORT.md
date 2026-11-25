# Final system verification report

Status: 2026-11-24, on the reference DGX Spark. Phases 1 and 2: branch `nvidia-acceleration-review` (based on
`9e4570a`, 0.7.0), merged to `main` as `793a21f`. Phase 3: branch `open-items-round`, based on `793a21f`, merged
to `main` as `c565c20`. Phase 4: branch `remaining-items-round`, based on `c565c20`.
An item is marked done only with a test, a measurement or an inspection named beside it. Where the environment
could not check something it is marked **unverified**, not passed.

## 1. What was done

- **Discovery and baseline.** The code was audited by subsystem and the running host measured before any change
  ([ARCHITECTURE_AUDIT.md](ARCHITECTURE_AUDIT.md)); the regression gate, an inference benchmark, an end-to-end
  pipeline benchmark and a memory-retrieval benchmark were recorded at `9e4570a`
  (`docs/quality/evidence/accel_baseline_*.json`). Three reusable benchmark scripts were added for this.
- **NVIDIA stack.** NIM, Triton, Dynamo, TensorRT-LLM and adjacent software were checked against official
  documentation and this hardware ([NVIDIA_ASSESSMENT.md](NVIDIA_ASSESSMENT.md)). Adopted: two scheduling flags of
  the installed vLLM, and GPU-served embedding and reranking models behind the existing gateway. Not adopted, with
  reasons: an LLM NIM, Triton, Dynamo, TensorRT-LLM, speech and safety NIMs, RAPIDS, Guardrails. Three engine
  options were rejected on measurement.
- **Fixes and improvements.** In phase 1, fourteen changes on by default and three off until the owner turns them
  on; phase 2 moved one of the three (read-ahead) to on, added eleven rows on by default and three that stay off
  ([ENGINEERING_PLAN.md](ENGINEERING_PLAN.md) §1 and §2), each with tests and a rollback.
- **Measurement after.** [PERFORMANCE.md](PERFORMANCE.md), with regressions and costs in its §6.
- **Regression.** [REGRESSION_REPORT.md](REGRESSION_REPORT.md).
- **Independent review.** A code review and a security review of the whole change set by reviewers that did not
  write it: §4.
- **Phase 2: the owner's decisions.** Phase 1 ended with six decisions for the owner
  ([ENGINEERING_PLAN.md](ENGINEERING_PLAN.md) §4 as it then stood). The owner delegated them the same day. Each
  was decided on a measurement and built by its own lane: the self-critique's scope (A), read-ahead by default
  (B), the memory models as a managed service (C), consolidation of expiring evidence (D), the ask budget (E),
  the sparse graph and what is promoted (F). Building them turned up twelve further defects, most of them older
  than the branch ([ARCHITECTURE_AUDIT.md](ARCHITECTURE_AUDIT.md) §4, "Found in phase 2"). Four independent
  reviews read the result in up to three passes; one of their findings was a regression of the sentence guard by
  a fix made during the review itself, which was withdrawn (§4). The one defect of the last pass on web reading
  was fixed and the fix confirmed by the same reviewer (§4).
- **Phase 3: the open items.** The owner then asked for the items phase 2 left open to be analysed and acted on.
  Seven lanes, each ruled on first: the sentence guard's older gaps (closed one change at a time, held by a
  differential against `307b907`); read-ahead's traffic bound and the owner's stop for every request kind;
  contradictions in other words (proposed by the memory models, decided by the value comparison, asked, never
  merged) with a kept knowledge index and stale web questions; the calibration loop where it changes no belief
  (ADR-046); a limit on consolidated readings; the installed two-account deployment, verified in a user namespace;
  one server for both memory models (4.26 GiB against 9.33). Three independent reviews read the result; one
  critical finding (a path to root through the installer, on the branch only) and the others were fixed
  ([ENGINEERING_PLAN.md](ENGINEERING_PLAN.md) §5). Eight items were left open on purpose, with the reason (§5.3
  there).
- **Phase 4: the remaining items.** The owner then asked for the list phase 3 left to be analysed and acted on, and
  answered four questions: graphs migrate automatically, the TensorRT-LLM comparison prepared in the best way, real
  systemd left unverified, voice stop as it is. Ten lanes: the guard's remaining gaps (stricter only, the
  differential against `307b907` at 0 different); contradictions in other units and stated aliases, tested on a fresh
  held-out set; forgetting of one kind of a name, of a forgotten task's settlements and of a forgotten dataset's
  graphs (schema 15, ADR-048); belief computation without a pass over every reading and a restore that streams its
  tombstones (schema 16); the graph-rule migration for existing individuals (ADR-047); the memory models without a
  network; SearXNG in the two-account install and an unused voice session; read-ahead's robots.txt continuation and
  two flaky tests; an offline outbox in Pocket; the TensorRT-LLM window scripted, with no downtime taken. Three
  independent reviews, a first fix round, an independent re-review of the fixes and a second fix round followed
  ([ENGINEERING_PLAN.md](ENGINEERING_PLAN.md) §7; what remains is its §6).

## 2. Definition of done

| Item | Status | Evidence |
|---|---|---|
| The existing implementation has been inspected | Done | Three subsystem audits with file-level findings; [ARCHITECTURE_AUDIT.md](ARCHITECTURE_AUDIT.md) |
| Functionality inventoried and baselined | Done, with one gap | Inventory in the audit; `accel_baseline_*.json`. Gap: the browser suite was not run at baseline; the one browser test that failed afterwards was run on the baseline commit (it passes there) and bisected |
| NVIDIA hardware and software compatibility verified | Done for what was adopted; documented for the rest | Hardware measured on the host. Adopted software runs here. For software not adopted, compatibility is from NVIDIA's documentation and anonymous registry manifests, not from running it: **unverified in execution**, and NVIDIA validates none of it on this operating system |
| Each candidate evaluated against real alternatives | Done | Adoption matrix; measured comparisons for the engine flags, speculative decoding, lexical against embedding against reranker, reranker against the answer model as judge. Phase 2: every sentence judged against the narrowed scope against no critique; read-ahead windows 1 to 4; counts against readings for consolidation; `gate-v1` against the sparse graph as a candidate against the better-learner rule; settings and other servers for the memory models' footprint (plan §3) |
| High-value, feasible integrations implemented | Done | Scheduling flags; memory models (`deploy/memory`, `semantic.py`), since phase 2 as a managed service (`nsf setup`, units, idle release), since phase 3 served by one process for both models inside the image already pinned (no image pulled) |
| Confirmed critical gaps remediated | Done, with the guard's remaining gaps open | Plan §1, §2 and §5. The three capability gaps phase 1 left to the owner were decided and built (plan §4). Phase 3 closed the guard gaps the phase-2 reviews recorded, contradictions in other words (asked, not decided), the calibration loop where it changes no belief, and the reload path of an installed two-account deployment. Open: further guard gaps found while building the corpus (§5 item 1); what was left open on purpose (plan §5.3) |
| Cognitive performance re-evaluated | Done | Reasoning 15 of 16 and tools 8 of 8 before and after; 46 model answers of 51 before and after in phase 1, 44 to 46 across the runs of phase 2 with the answer call unchanged; the critique's marks measured, then counted exactly (42 of 46 on restatements) and reduced to 4 on the sentences it still reads; the guard's behaviour compared with `307b907` input by input (1,476; in phase 3 1,782, with 306 adversarial sentences labelled before each change) |
| Context and memory correctness validated | Done | Long-context recall 3 of 3; memory benchmark by class with held-out subjects (re-run in phase 2 and against the single memory server in phase 3: unchanged); contradictions in other words 31 of 43 found and 0 of 23 false pairs on held-out subjects (of a design revised once); the kept knowledge index identical to a fresh one over random sequences on two connections; the memory-answer check 86 of 96 and 1 of 288; provenance and timestamps untouched (the same rows, reordered). Phase 2: beliefs equal before and after the evidence lifetime, against a twin that never expires, over 80 randomised lives; forgetting exact and restores clean through a second generation (two independent fuzzes) |
| Hardware utilisation measured and optimised where practical | Done | GPU 96 % at one stream; queue, prefill, decode, cache, power and temperature now reported; scheduling flags. No further headroom was found to recover: the limit is single-stream decode. Phase 2: the memory models' 4.6 GiB each measured by component and not reducible by settings; releasable when idle (7.9 to 9.4 GiB returned, measured). Phase 3: one server for both, 4.26 GiB against 9.33 GiB measured side by side, ready in 7 to 11 s against 54 to 69 s; an interactive embedding behind the largest accepted call 8.0 s → 390 ms |
| Multimodal and distributed capabilities remain operational | Partly verifiable | Voice, Pocket and Presence pass their suites and the browser suite with synthetic media. **Unverified**: a real microphone, a real handset (as in 0.7.0) |
| Existing features pass regression testing | Done | Phase 4, final code: <<FINAL-RUN>>. Phase 3, final code (`1ece09b`; later commits change only these reports): `bash scripts/check.sh` 4584 passed, 18 skipped, Node checks and 8 of 8 reproduction pass, all checks passed in 747 s (baseline 3337, phase 1 3483, phase 2 3996; the same 18 skipped); browser suite 198 passed, 1 skipped, 1 failed on a 30 s wait for the phone's enrol button, then three further full runs on the same tree 199 passed, 1 skipped each; acceptance exit 0; offline benchmark exit 0 with every metric equal to phase 2's; guard differential 1,808 inputs, 0 different; two-account check 49 of 49. Earlier phases' runs are in [REGRESSION_REPORT.md](REGRESSION_REPORT.md) §1 |
| Edge cases and recovery scenarios pass | Done for those listed | [REGRESSION_REPORT.md](REGRESSION_REPORT.md) §4. **Unverified**: GPU memory exhaustion, a failed image upgrade and its rollback, days of autonomy. The two-account deployment is verified in a user namespace with two real uids and the real installer (46 checks, the root-escalation attack among them), not under systemd and polkitd (§5 item 3) |
| New and existing services have observability | Done | `nsf resources`, `GET /api/resources`, the ledger, `nsf llm status`, `nsf web memory`, the `retrieval` and `memory_check` fields. Since phase 2 the memory models are services of the idle policy (`nsf resources`: timeouts, state, last use, events) and `nsf web memory` shows `asleep` or `refused`; a task's usage reports unused read-ahead; a report's `critique` says how many sentences were judged and why a call was skipped; `nsf config`, `nsf regulation` and `nsf memory consolidated` show the consolidation setting and readings; a changed instance setting leaves an audit row. Phase 3: the consolidation limit's warnings and the largest epoch's belief cost (`nsf regulation`, the console's overview), `nsf web status` questions with the stale ones, `nsf knowledge calibration` with the mapping per kind and how it would have fared, `nsf resources` with a shared memory container's names and effective timeout, the idle state's problems, `merged` and `held` on a pair found by meaning |
| Improvements supported by reproducible evidence | Done | Scripts and evidence files; repeat runs for the engine flags; a dev and held-out split for memory. Phase 2: `accel_decisions_*.json`, `accel_consolidation_cost.json`; `scripts/bench_consolidation.py`, `scripts/bench_graph_learner.py`, `scripts/guard_differential.py`; repeat runs for the critique scope. Its times were taken under load and are marked indicative ([PERFORMANCE.md](PERFORMANCE.md) §7). Phase 3: `open_items_*.json`; `scripts/bench_knowledge.py`, `scripts/bench_calibration.py`, `scripts/two_account_check.py`; the guard's gap corpus with every label fixed before the change ([PERFORMANCE.md](PERFORMANCE.md) §8) |
| No material unapproved regressions remain | Done, with the costs listed | [PERFORMANCE.md](PERFORMANCE.md) §6. Phase 2 changed four defaults under the owner's delegation, each with its switch; phase 3 added the limit on consolidated readings, contradictions in other words, the calibration mapping, stale web questions and the single memory server, each with its switch, and the guard's refusals, which have none. Their costs are listed there and in §5 |
| All significant changes have rollback procedures | Done | Plan §1 and §2, last column; plan §4, the last line of each decision; the runbook. Three changes have no switch because they only refuse or only make a stated property true (the truncating attribution, the stops, the gate's no-graph check) |
| Documentation matches the delivered implementation | Done | The architecture, resource, memory, contract and runbook pages were updated from the code by each lane; ADR-044, ADR-045 and ADR-046; `MIGRATIONS.md` and `ENVIRONMENT.md` corrected; this directory |

## 3. What is running on the host now

| Service | State after this review |
|---|---|
| `neuroscifi-vllm` | Running the same model and image with `--scheduling-policy priority --max-num-batched-tokens 4096 --enable-prompt-tokens-details`, as phase 1 left it. Not restarted or reconfigured in phases 2 and 3 (called, for the benchmarks). Roll back with the two lines of plan §1 row 8 |
| `neuroscifi-memory-embed`, `neuroscifi-memory-rerank` | Started, measured and **stopped and removed** again in every phase: no individual on this host is configured to use them |
| The single memory server (`neuroscifi-memory`, and the test containers `neuroscifi-memlite-*` and their network) | Started for measurement in phase 3 and **stopped and removed**. `deploy/memory/run.sh start && deploy/memory/run.sh wait` brings the default server up (7 to 11 s) |
| `neuroscifi-voice-asr`, `neuroscifi-voice-tts` | Not touched |
| `neuroscifi-searxng` | Not touched, apart from one probe in phase 3: a lane ran one `wget` inside the container while checking the memory server's network, outside its allowed scope, and killed that process when it hung. SearXNG stayed healthy with 0 restarts; no file or setting changed |
| The soak run (`nsf-soak`) from an earlier session | Not touched; it runs a frozen copy of the 0.7.0 code and has no model configured |
| The host's other tenants' containers | Not touched |
| systemd | **Nothing installed.** The new units (the memory units, the wake path and service) and `deploy/install.sh --memory / --idle` were exercised as a dry run, as transient user units that ran for seconds and are gone, and inside a user namespace with a private `/etc` |
| Images | None pulled in phases 2 and 3. Three anonymous registry manifest reads (for the lighter-server proposal); nothing of the project or the owner was sent. Phase 4: TensorRT-LLM 1.3.0 and 1.4.0rc4 pulled from NGC without a login; 1.3.0 removed again, 1.4.0rc4 (53 GB) kept for the owner's window |
| `neuroscifi-vllm` in phase 4 | Never stopped or changed; the TensorRT-LLM preparation and its dry runs only read its configuration, and it was healthy at the end |
| Test containers of phase 4 | The lanes' memory servers, forwarders, small-model proxies and their networks (own names) were removed |
| Model weights added | `~/.local/share/neuroscifi/models/qwen3-embedding-0.6b`, `qwen3-reranker-0.6b` (1.2 GB each, Apache-2.0, pinned revisions), from phase 1 |
| Keys | `~/.config/neuroscifi/memory/api_key` created by `deploy/memory/run.sh` (0600), from phase 1; unchanged |
| Git | Phases 1 and 2: committed on `nvidia-acceleration-review`, pushed and merged to `main` as `793a21f` with a merge commit on the owner's instruction (2026-11-23), so the hashes cited stay valid; the branch was deleted. Phase 3: committed on `open-items-round`, merged to `main` as `c565c20` on the owner's instruction. Phase 4: committed on `remaining-items-round`; pushed only on the owner's instruction |

No owner instance exists on this host, so no individual's settings were changed; the measurements used temporary
individuals. The new defaults apply to an individual when its services next start. For an individual created by
0.7.0 that means, on upgrade: consolidation on, the ask window, read-ahead at 4 and the narrowed critique;
`docs/operations/MIGRATIONS.md` section 2 says what to run first to keep the earlier meaning of expiry. Phase 3
adds, on upgrade: the guard's new refusals; a limit of a million consolidated readings; web questions stale after 30
days; the calibration mapping (the identity until 20 forecasts of a kind have settled); contradictions in other
words once semantic memory and an answer model are configured; and `NSF_MEMORY_SERVER=single` for whoever starts the
memory models next. Phase 4 adds, on upgrade: schema 15 and 16 at the first open; at that open, once, the graph-rule
migration (an individual still on `gate-v1` gets `best-learner-v1`; an active graph worse than no graph is archived;
`nsf config --no-graph-rule-migration` before, `nsf graph migration undo --owner-approved` after) and the completion
of datasets forgotten by an earlier version; the guard's further refusals; contradictions found across units;
`NSF_MEMORY_EGRESS=none` for whoever starts the memory models next; an unused voice session stops holding off idle
unloading after 30 minutes.

## 4. Independent review

**Phase 1.** A code reviewer and a security reviewer, neither of which wrote the changes, read the whole change
set (`9e4570a..307b907`), reproduced what they suspected, and reviewed again after the first fixes. One serious
defect was found by both (a restore made from a restored copy brought a forgotten epoch back), two key-handling
defects and eleven smaller findings in the first pass, and two further defects with plausible triggers and several
minor ones in the second; every finding with a concrete trigger was fixed with a test that fails on the earlier
code ([ENGINEERING_PLAN.md](ENGINEERING_PLAN.md) §1, "Found by the independent reviews of phase 1 and
fixed"). The fixes made after its second pass were not reviewed a third time.

**Phase 2** (`307b907..HEAD`, about 2,200 added source lines at the first pass) was read by four reviewers that
wrote none of it, each with its own brief and none given the authors' conclusions: the core (consolidation, the
ask budget, restores); the web-reading path (read-ahead and stops); the runtime, the critique and the graphs; and
security and privacy across all of it.

- **First pass.** No critical finding. High: the restatement test took factorial time on a sentence carrying nine
  claims; a reload of a stopped unit failed its own wait; a request could be sent after a stop that landed while
  its connection was opening. Medium, among others: forgetting a sample's first copy after expiry lost its later
  copies; a restore turned consolidation back on; one consolidated reading could not be forgotten in practice; a
  page fetched ahead could open a second connection to a site; the pages fetched ahead of a task each spent the
  same budget; a claim that lost its qualifier counted as a restatement.
- **Second pass**, on the fixes. Core: the six first-pass findings confirmed fixed (70 random lives against a
  never-expiring twin, 15 restore fuzz seeds through a second generation: nothing forgotten returned), one
  slowdown introduced by a fix and four small points, all then fixed. Web reading: the first-pass findings
  confirmed fixed and equivalence not broken in 400 random webs; three narrow defects (the 429 exception skipped
  too much; an unconfirmed cut poisoned robots.txt for a day; a charge without a request) and the true traffic
  bound, all then fixed or stated. Runtime: the listener check that had just been added, including its extension
  to the answer model made while the lanes were integrated, refused legitimate endpoints and was redesigned; wake
  requests could not work across two accounts and were redesigned.
- **Final security pass.** The earlier security fixes hold. It found that a fix made in the second round had
  **regressed the sentence guard**: five sentences that invert an exception, and two that borrow a hostile
  claim's "according to …", were accepted where `307b907` rejects them. The change was withdrawn: the answer path
  was rebuilt from `307b907`, and a committed differential over 1,476 inputs recorded from that commit's own code
  shows 1,459 identical results, 17 that differ only by a sentence now refused, and none accepted that was
  rejected before. The reviewer confirmed this independently on its own five corpora and by replaying the
  reference through its own extraction of `307b907`.
- **Third pass on web reading**, limited to the mechanisms the second-round fixes introduced. The 429 fix, the
  `interrupted` error, the refund and the traffic numbers hold. It found one defect: a fetch ahead stopped inside
  a redirected robots.txt walk was continued from its page hop, so that robots.txt was requested and charged
  twice and, at the edge of the request budget, the task read fewer pages than one at a time (reproduced; the
  reviewer's fuzz found it independently, 9 times). It also asked for "at most twice" instead of "below twice" in
  the statement of the traffic bound.
- **Fourth pass on web reading (confirmation).** The fix (`989a384`: such a stop is no longer continued; the
  fetch is made again and what was sent counts as unused) was read and re-attacked by the same reviewer: it sits
  in the one place that knows how far the robots.txt walk got, can only make a stop non-resumable, and leaves
  the charged usage equal to one-at-a-time reading. The reproduction, the replayed fuzz scenarios and 600 further
  random webs on the seeds that had found the differences show none. Verdict: approve.
- **Found sound by the reviewers:** beliefs equal before and after expiry in every case the fuzzes produced;
  forgets erase at the byte level (the file, the WAL, free pages) and do not return through a restore; older
  software refuses a schema-14 file; every console route needs the token and the Host and Origin checks, and
  Pocket has no new route; the gate before every request fails closed; SSRF and rebinding protections apply on
  the fetch-ahead path; accounting exact in 2,500 and again 1,300 timing trials with no thread, busy mark or
  budget hold left behind; the daily caps hold; the vectorised edge exchange bit-identical in 43,000 random
  cases and packing under old rules byte-identical in 400; the reload's argument list fixed and the key never on
  a command line; no secret or personal data in anything added.

What the reviews did not cover: the core's second-round fixes (the copy rule as passes over one index) and the
runtime redesign (the scoped listener check, the wake redesign, the installer's `--idle`) were read by the
security reviewer's final pass but not by a further correctness pass; the last change to the critique's scope
(a restatement is one claim) was made after the guard check and only narrows what is skipped. These rest on
their tests and the final regression run.

**Phase 3** (`793a21f..open-items-round`) was read by three reviewers in parallel that wrote none of it, each with
its own brief and none given the authors' conclusions: security and privacy across all of it; correctness of the
web reading, knowledge, guard and calibration work; correctness of the runtime, deployment and instance work.

- **First pass.** One **critical** finding, made by two of the reviewers independently and reproduced with the real
  installer in a user namespace: `deploy/install.sh --idle-access` changed group and mode, as root and following
  links, of files inside a folder the service account owns, so that account could have pointed them at
  `/etc/shadow` or `/etc/systemd/system`. It existed only on the round's branch. Fixed by construction: every step
  inside that folder now runs as the service account, so the kernel refuses what that account could not do itself;
  the attack is part of `scripts/two_account_check.py` (46 of 46 checks; 5 of them fail on the code before the fix).
  High: the voice hub's transcript writer could outlive `close()`; with the single memory server, unloading one name
  stopped the container under a call of the other. Medium: a timeout of one memory name governed both; the in-use
  lock wait ignored the call's deadline; the wake unit's bound did not hold on an exception; a re-run of the
  installer undid the arrangement; a large rerank held an interactive embedding for 8 s; a race could pass the
  unused read-ahead allowance; a direction override between a plain `<` and `>`, and other direction marks, could
  leave a claim whose digits read differently on the page; the calibration mapping decided admission, not only
  rank. Low: the memory server's loose parsing and thread per connection; the idle account reading through links;
  missing condition words; status words in a source's name; a passive role swap (older than the branch); the
  loosening proven with the code under test; a forgotten statement kept in the in-memory index; a paid search not
  refunded. Every one was fixed with a test that fails on the earlier code
  ([ENGINEERING_PLAN.md](ENGINEERING_PLAN.md) §5.2).
- **Found sound:** the guard's recorded reference is byte for byte what `307b907` does (re-recorded independently;
  the one difference seen in a later re-recording is the order of two verbs in one reason, which `307b907` itself
  varies between processes and the comparison ignores); the consolidation limit over 360 randomised sweeps (nothing
  kept is ever removed, oldest first); a restore reading only its archive's readings gives the same result as the
  full read; read-ahead equivalence over 350 random webs and the stop gate on every request kind; the kept knowledge
  index scores bit for bit like a fresh one and never returns a forgotten statement; a pair found by meaning never
  merges or weakens anything; the calibration mapping's mathematics over 3,000 random fits; the memory server's
  numerics against the model cards and its constant-time key check; no secret or personal data in anything added.
- **Fixes after the first pass.** The full non-browser suite and the browser suite were run on the merged round
  before the fixes; their two flaky tests and a thread left by a browser-test fixture were fixed. A narrow security
  re-review of the critical and security-relevant fixes, with the first pass's exploits and new variants, rated the
  remaining risk **low**: the installer finding fixed (planted links for the database, `-wal`, `-shm` and
  `.resources`, a link inside `--db`, 40 runs of the `find` race and a hard link, with the real installer as root
  and two real accounts: no target changed); the idle account's reads fixed for every reported vector; the polkit
  rule's JavaScript answers yes for exactly the 14 intended combinations of 95,744 (7 units × start and stop × the
  idle account); direction controls caught in 36 HTML variants checked against what headless Chromium shows; the
  frozen proof independent; the memory server's parsing strict. It found five new low items and one older medium,
  all fixed in a last round with tests that fail on the earlier code: a FIFO planted as the database's `-wal` or
  `-shm` hung the idle pass, and deeply nested JSON in a state file crashed it (both now reported, nothing opened);
  32 keep-alive health checks without the key held every memory-server slot (now closed after an unkeyed answer,
  with slots kept for keyed calls); a fake tag inside `<title>` or `<xmp>` hid an override; "Both…", "Such…", "The
  latter…" counted as exact restatements; and, older than the branch, a claim restated with its roles reversed in
  the passive ("Globex was acquired by Acme Corp." from "Globex acquired Acme Corp.") was accepted. The installer's
  service-account steps now also run detached from root's terminal. These last fixes were not reviewed a further
  time; they rest on their tests and the final run of §2.

**Phase 4** (`c565c20..remaining-items-round`) was read the same way, by three reviewers in parallel that wrote none
of it: security and privacy across all of it; correctness of the web, knowledge and guard work; correctness of the
instance, runtime and deployment work.

- **First pass.** No critical finding. One **high**: knowledge stored before the change could be strengthened by a
  statement that contradicts it, because stored values had been read by the earlier parser ("120 km/h" as 120 km)
  and the new units put the two in different comparison groups. Medium: the TensorRT window left the answer model's
  port free for another local account to take and receive the key; a test of migration 15 was broken by the schema
  renumbering, so the upgrade from 14 was not checked; datasets forgotten by the previous release kept their graph
  weights in the live file while a restore erased them; a pending graph migration made every open wait for the
  write lock; an extra numbered name hid a contradiction; converted values had to match exactly; a role read as an
  alias; `JOINED_APART` refused an ordinary fusion and was bypassed by putting the name first; the group-word rule
  refused common publishers. Low: `INSERT OR REPLACE` past the graph triggers; an erased revision's weight hash
  confirming a guessed dataset; WAL cleanup held back by a restore; unlimited scans under Pocket's lock; the
  SearXNG port; the forwarder's documentation; four Pocket retry and graph-migration edge cases; three guard
  over-refusals; unit spellings; masking; tie order. All fixed with tests that fail on the earlier code.
- **Re-review of the fixes.** An independent reviewer re-ran every earlier reproduction (most fixed) and re-recorded
  the guard's reference from `307b907` (0 different). It found that the fixes had added problems: a forgotten
  question queued again by a later Pocket retry (a privacy regression of a first fix); the TensorRT script restarting
  its callers while something else held the port, and taking the units' own processes for processes started by
  hand; rates dropped, so different rates corroborated each other; a shared year hiding incomparable values; real
  restrictions dropped by the guard's over-refusal fixes; files from the first build of migration 15 failing to
  open; and smaller items. All were fixed in a second round, each with a test that fails on the earlier code
  ([ENGINEERING_PLAN.md](ENGINEERING_PLAN.md) §7.3).
- **Found sound:** every path that can write a consolidated reading keeps the copied-flag rule (multi-row updates,
  `INSERT OR REPLACE`, upserts, changes of seq), and beliefs are bit for bit those of the earlier code; the streamed
  tombstones inside one snapshot; an undo can never bring back a forgotten graph; Pocket's exactly-once within and
  across processes; the conversion factors exact by definition and SI and IEC never equated; the read-ahead
  continuation charges and gates every hop; change detection off changes nothing; no key in any file the TensorRT
  script writes; no secret or personal data in anything added.
- **Not reviewed again:** the second round's fixes rest on their tests, the lanes' full suites and the final run of §2.

## 5. Outstanding issues and residual risks

Correctness first; the full list with reasons is [ENGINEERING_PLAN.md](ENGINEERING_PLAN.md) §6, as it stands after
phase 4.

1. **The sentence guard is lexical.** Open: a name swapped for a name in a fused sentence; by ruling, a bound of
   time, two attribution wordings and citing right-to-left lines with direction marks. What a hostile page can do is
   unchanged in kind from 0.7.0: have its own weak claim shown word for word with the code's hedge, or have a
   faithful sentence refused. Phase 4's rules refuse a few faithful wordings on purpose ("by Stack Overflow
   Developers", "by People"), and none of the benchmark's sentences.
2. **Right-to-left text loses citations.** A line of Hebrew or Arabic that uses a direction mark or isolate is read
   but never cited; a line or paragraph with a direction override is not read at all.
3. **The answer model's key goes to whatever holds its port** while the model is stopped (an idle unload, a
   TensorRT window): the product's inference clients do not check the listener. The TensorRT window keeps its callers
   stopped until the model is back on its own port. Older than this review.
4. **The installed, two-account deployment is verified in a user namespace, not as installed**, by the owner's
   choice: systemd with `PrivateUsers=yes` (and the SearXNG drop-in), polkitd, a real root and second account, the
   idle timer and a real reload of the memory server are **unverified**.
5. **Read-ahead is concurrent and on by default.** What a task sends is at most its budget plus a tenth of it;
   `nsf web config --set read_ahead=1` is the path of 0.7.0 with the stricter stops.
6. **Comparing values.** A number from 1,000 to 2,999 written with a comma is read as a year (a fix changes stored
   values: the owner's ruling); numbers as words and "pounds" are not read; one false pair remains on the fresh
   held-out set, from before phase 4. Pairs found by meaning are asked, never merged.
7. **Consolidated readings** grow up to a limit (a million by default), with warnings from 80 %. Tombstones and the
   deletion log hold epoch names and event ids in clear, and the labels of forgotten cameras and partners can be
   checked offline against guessed names, because their salt is in the same file.
8. **The memory server's forwarder** carries the key and the memory texts and can reach outside; the model server
   cannot. The vLLM pair still can.
9. **Semantic retrieval's floor, the reranker's instruction and the contradiction thresholds were chosen on small
   fictional English sets.** The floor should be revisited on the owner's real memory.
10. **Decisions resting on a model's judgement**: the memory-answer check (it can only withhold), the contradiction
    pairing's confirmation, the self-critique's marks (shown, never acted on; no precision measured).
11. **Calibration's evidence is simulated**, and after a forecaster's rule reverses about 50 forecasts read worse
    than stated; the change detection that would help failed its held-out gate and ships off.
12. **The TensorRT-LLM comparison is prepared, not run**: everything about the 30B model under TensorRT-LLM is
    **unverified** until the owner's window.
13. **Unverified, because the environment could not check it:** GPU memory exhaustion; a failed image upgrade and its
    rollback; days of autonomy; a real microphone, a real handset (the Pocket outbox included), Safari and Firefox,
    the live internet. Nothing here was validated by NVIDIA for this operating system.

## 6. Recommended next priorities

In order: the owner's ruling on numbers written with a comma; an ownership check in the inference clients before the
key is sent; a run of the installed two-account deployment under real systemd and polkitd if the owner wants it; the
TensorRT-LLM window when the owner can spare the answer model for about 45 minutes; the guard's name-for-name swaps,
with their own corpus and the differential as the gate; the memory server's floor and drift measured on the owner's
own memory; settled predictions of a real individual to check the calibration mapping; the Pocket outbox on a real
handset.
