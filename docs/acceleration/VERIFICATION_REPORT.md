# Final system verification report

Status: 2026-11-23, branch `nvidia-acceleration-review` (based on `9e4570a`, 0.7.0), on the reference DGX Spark.
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

## 2. Definition of done

| Item | Status | Evidence |
|---|---|---|
| The existing implementation has been inspected | Done | Three subsystem audits with file-level findings; [ARCHITECTURE_AUDIT.md](ARCHITECTURE_AUDIT.md) |
| Functionality inventoried and baselined | Done, with one gap | Inventory in the audit; `accel_baseline_*.json`. Gap: the browser suite was not run at baseline; the one browser test that failed afterwards was run on the baseline commit (it passes there) and bisected |
| NVIDIA hardware and software compatibility verified | Done for what was adopted; documented for the rest | Hardware measured on the host. Adopted software runs here. For software not adopted, compatibility is from NVIDIA's documentation and anonymous registry manifests, not from running it: **unverified in execution**, and NVIDIA validates none of it on this operating system |
| Each candidate evaluated against real alternatives | Done | Adoption matrix; measured comparisons for the engine flags, speculative decoding, lexical against embedding against reranker, reranker against the answer model as judge. Phase 2: every sentence judged against the narrowed scope against no critique; read-ahead windows 1 to 4; counts against readings for consolidation; `gate-v1` against the sparse graph as a candidate against the better-learner rule; settings and other servers for the memory models' footprint (plan §3) |
| High-value, feasible integrations implemented | Done | Scheduling flags; memory models (`deploy/memory`, `semantic.py`), since phase 2 as a managed service (`nsf setup`, units, idle release). A lighter server for them is a proposal only: nothing was pulled or run |
| Confirmed critical gaps remediated | Done for what was found in the audit; **not done** for gaps the phase-2 reviews found in older code | Plan §1 and §2. The three capability gaps phase 1 left to the owner (consolidation of expiring evidence, the ask budget, the sparse graph) were decided and built (plan §4). Open: the sentence guard's older gaps and one read-ahead defect from the last review pass (§5 items 1 and 2); contradictions in other words; the calibration loop (plan §5) |
| Cognitive performance re-evaluated | Done | Reasoning 15 of 16 and tools 8 of 8 before and after; 46 model answers of 51 before and after in phase 1, 44 to 46 across the runs of phase 2 with the answer call unchanged; the critique's marks measured, then counted exactly (42 of 46 on restatements) and reduced to 4 on the sentences it still reads; the guard's behaviour compared with `307b907` input by input (1,476) |
| Context and memory correctness validated | Done | Long-context recall 3 of 3; memory benchmark by class with held-out subjects (re-run in phase 2: unchanged); the memory-answer check 86 of 96 and 1 of 288; provenance and timestamps untouched (the same rows, reordered). Phase 2: beliefs equal before and after the evidence lifetime, against a twin that never expires, over 80 randomised lives; forgetting exact and restores clean through a second generation (two independent fuzzes) |
| Hardware utilisation measured and optimised where practical | Done | GPU 96 % at one stream; queue, prefill, decode, cache, power and temperature now reported; scheduling flags. No further headroom was found to recover: the limit is single-stream decode. Phase 2: the memory models' 4.6 GiB each measured by component and not reducible by settings; releasable when idle (7.9 to 9.4 GiB returned, measured) |
| Multimodal and distributed capabilities remain operational | Partly verifiable | Voice, Pocket and Presence pass their suites and the browser suite with synthetic media. **Unverified**: a real microphone, a real handset (as in 0.7.0) |
| Existing features pass regression testing | Done | Final code (`5583823`; later commits change only reports, evidence files and the clean-up at the end of two Web Cortex tests, §5 item 14, after which `tests/webcortex` gave 1807 passed, 9 skipped): `bash scripts/check.sh` 3996 passed, 18 skipped, Node checks and 8 of 8 reproduction pass, all checks passed in 659 s (baseline 3337 passed, phase 1 3483 passed, the same 18 skipped); browser suite 198 passed, 1 skipped, exit 0; acceptance exit 0, every scenario passes; offline benchmark exit 0 with every compared metric identical at read-ahead 1, 4 and the default. The same three suites had passed with the same counts but 3989 Python tests on the tree before the last read-ahead fix. Phase 1's runs are in [REGRESSION_REPORT.md](REGRESSION_REPORT.md) §1 |
| Edge cases and recovery scenarios pass | Done for those listed | [REGRESSION_REPORT.md](REGRESSION_REPORT.md) §4. **Unverified**: GPU memory exhaustion, a failed image upgrade and its rollback, days of autonomy, and the installed two-account deployment (§5 item 13) |
| New and existing services have observability | Done | `nsf resources`, `GET /api/resources`, the ledger, `nsf llm status`, `nsf web memory`, the `retrieval` and `memory_check` fields. Since phase 2 the memory models are services of the idle policy (`nsf resources`: timeouts, state, last use, events) and `nsf web memory` shows `asleep` or `refused`; a task's usage reports unused read-ahead; a report's `critique` says how many sentences were judged and why a call was skipped; `nsf config`, `nsf regulation` and `nsf memory consolidated` show the consolidation setting and readings; a changed instance setting leaves an audit row |
| Improvements supported by reproducible evidence | Done | Scripts and evidence files; repeat runs for the engine flags; a dev and held-out split for memory. Phase 2: `accel_decisions_*.json`, `accel_consolidation_cost.json`; `scripts/bench_consolidation.py`, `scripts/bench_graph_learner.py`, `scripts/guard_differential.py`; repeat runs for the critique scope. Its times were taken under load and are marked indicative ([PERFORMANCE.md](PERFORMANCE.md) §7) |
| No material unapproved regressions remain | Done, with the costs listed | [PERFORMANCE.md](PERFORMANCE.md) §6. Phase 2 changed four defaults under the owner's delegation, each with its switch; their costs are listed there and in §5. |
| All significant changes have rollback procedures | Done | Plan §1 and §2, last column; plan §4, the last line of each decision; the runbook. Three changes have no switch because they only refuse or only make a stated property true (the truncating attribution, the stops, the gate's no-graph check) |
| Documentation matches the delivered implementation | Done | The architecture, resource, memory, contract and runbook pages were updated from the code by each lane; ADR-044 and ADR-045; `MIGRATIONS.md` and `ENVIRONMENT.md` corrected; this directory |

## 3. What is running on the host now

| Service | State after this review |
|---|---|
| `neuroscifi-vllm` | Running the same model and image with `--scheduling-policy priority --max-num-batched-tokens 4096 --enable-prompt-tokens-details`, as phase 1 left it. Not restarted or reconfigured in phase 2. Roll back with the two lines of plan §1 row 8 |
| `neuroscifi-memory-embed`, `neuroscifi-memory-rerank` | Started, measured and **stopped and removed** again in both phases: no individual on this host is configured to use them. `deploy/memory/run.sh start && deploy/memory/run.sh wait` brings them back (18 to 31 s) |
| `neuroscifi-voice-asr`, `neuroscifi-voice-tts`, `neuroscifi-searxng` | Not touched |
| The soak run (`nsf-soak`) from an earlier session | Not touched; it runs a frozen copy of the 0.7.0 code and has no model configured |
| The host's other tenants' containers | Not touched |
| systemd | **Nothing installed.** The new units (two memory units, the wake path and service) and `deploy/install.sh --memory / --idle` were exercised as a dry run and as transient user units that ran for seconds and are gone |
| Images | None pulled in phase 2. Three anonymous registry manifest reads (for the lighter-server proposal); nothing of the project or the owner was sent |
| Model weights added | `~/.local/share/neuroscifi/models/qwen3-embedding-0.6b`, `qwen3-reranker-0.6b` (1.2 GB each, Apache-2.0, pinned revisions), from phase 1 |
| Keys | `~/.config/neuroscifi/memory/api_key` created by `deploy/memory/run.sh` (0600), from phase 1; unchanged |
| Git | Everything was committed on the branch `nvidia-acceleration-review`. On the owner's instruction (2026-11-23) the branch is pushed and merged to `main` with a merge commit, so the commit hashes cited on these pages stay valid, once the run of §2 has passed; that happens after this page is committed |

No owner instance exists on this host, so no individual's settings were changed; the measurements used temporary
individuals. The new defaults apply to an individual when its services next start. For an individual created by
0.7.0 that means, on upgrade: consolidation on, the ask window, read-ahead at 4 and the narrowed critique;
`docs/operations/MIGRATIONS.md` section 2 says what to run first to keep the earlier meaning of expiry.

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

## 5. Outstanding issues and residual risks

Correctness first; the full list with reasons is [ENGINEERING_PLAN.md](ENGINEERING_PLAN.md) §5.

1. **The sentence guard has gaps that are older than this branch** and were found by its reviews: a one-version
   inversion of an exception passes; a sentence the model shortens so that an exception is gone passes; words are
   compared without case; a clean sentence passes for a claim holding a direction-override character; one faithful
   word-for-word sentence is rejected. They are recorded in the differential reference and unchanged. The one
   attempt to fix one of them regressed the guard, which is why none was changed here.
2. **Read-ahead was reviewed four times and each of the first three passes found something** (§4). The last
   pass approved it; the mechanism is new, concurrent and on by default, and `nsf web config --set read_ahead=1`
   is the exact path of 0.7.0 with the stricter stops. One residue of the last fix: for a site whose robots.txt
   redirects to another site's file while that site is being read, robots.txt is sent twice (charged once, the
   other request counted as unused).
3. **Read-ahead sends requests it does not use**: at most the task's request budget again, so everything a task
   sends is at most twice its budget (4 beside 350 on the benchmark). A site that answers "slow down" to a page
   fetched ahead removes the pages that lead onto it from the report. After a stop, a connection being opened may
   complete its handshake with nothing sent. Searches notice a configuration change within 2 s, not per request.
   "One connection per site" holds within one process.
4. **Consolidated readings accumulate without a cap**, and belief computation grows with an epoch's age (24 ms
   per 100,000 readings; 0.79 s at a million readings each recorded twice). A root's name and a sample id stay in
   the file until the reading is forgotten, and an individual upgraded from 0.7.0 gets that unless the owner opts
   out before the first sweep. This is retention, not rehearsal or replay.
5. **Contradictions worded differently are not detected** (W2); the knowledge loops are open (W7).
6. **Graphs.** An existing individual on `gate-v1` still promotes a snapshot at twice the sparse learner's error
   at 96 and 128 populations, and a graph that is already active is not demoted. The dense learner's failure
   beyond about 100 populations is not repaired, only no longer promoted for new individuals. No shipped data
   source has more than 24 populations.
7. **The installed, two-account deployment is unverified in everything phase 2 added to it.** Inside the hardened
   units other accounts cannot be told apart, so the listener check refuses nobody there. The idle and wake units
   need access to the instance's resources folder that the installer does not grant; until the owner grants it,
   the policy unloads nothing in such an install. A member of the wake folder's group can exhaust the wake unit's
   start limit.
8. **The memory models cost 4.6 GiB each** on a shared host with about 31 GiB available; no setting lowers it.
   With an idle timeout (off by default) they are released when unused.
9. **Semantic retrieval's floor and the reranker's instruction were chosen on 130 queries over 48 fictional
   facts.** Held-out subjects agree, but it is one small English set. The floor should be revisited on the owner's
   real memory; `nsf web memory config --set floor=…` changes it and `retrieval` in every result shows what was
   dropped.
10. **Two decisions rest on a model's judgement.** The memory-answer check can only withhold a memory answer and
    errs towards web research (10 of 96 right facts refused; 1 wrong-property fact in 288 accepted, which in use
    must also pass the lexical test). The self-critique's 4 remaining marks are unconfirmed judgements, shown and
    never acted on.
11. **Tombstones and the deletion log hold epoch names and event ids in clear.** Consolidation now depends on the
    tombstones' map from id to position; the labels of forgotten cameras and partners can be checked offline
    against guessed names, because their salt is in the same file.
12. **A call that triggers an idle reload** can take up to twice its timeout; where the policy's state cannot be
    read, it fails once as `unavailable` and a later call succeeds.
13. **Unverified, because the environment could not check it:** the systemd units as installed system units and
    `deploy/install.sh` run as root; the idle timer; a listener or a request folder of a real second account; a
    container that never answers; the wait's grace for the answer-model and speech scripts beyond unit tests;
    GPU memory exhaustion; a failed image upgrade and its rollback; days of autonomy; a real microphone, a real
    handset, the live internet; the 1024-population learner timings on a quiet host. Nothing here was validated
    by NVIDIA for this operating system.
14. **Two older tests are sensitive to a slow runner.** Of the two CI runs of the final commit, the pull-request
    run passed all three jobs and the push run failed
    `test_two_concurrent_autonomous_investigations_at_most_across_processes` (a forked child never reported) and `test_hung_render_kills_driver_and_chromium` (Chromium outlived the
    render by more than 3 s), then hung on the processes they left until the job's limit. The branch touches
    neither test's code path, the forking test process holds 34 leftover threads on `9e4570a` and 35 here, and
    neither failed in 12 local runs on one busy CPU on either commit. Both tests now kill what outlives them, so
    a failure ends the job instead of hanging it; the sensitivity itself (a fork from a multi-threaded test
    process, a 3 s allowance for a browser to die) is not removed.

## 6. Recommended next priorities

In order: the guard gaps of §5 item 1, each with its own adversarial corpus and the differential test as the
gate; paraphrased
contradictions with the embedding model as a candidate source; a cap and a maintained list of copied samples for
consolidated readings; verification of the installed two-account deployment (the units, the installer, the wake
folder); the calibration loop; a lighter server for the memory models; a TensorRT-LLM comparison with this
review's harness if a faster decode is wanted more than the current model's exact quantisation.
