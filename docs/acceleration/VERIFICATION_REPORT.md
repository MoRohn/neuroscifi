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
- **Fixes and improvements.** Fourteen changes on by default and three off until the owner turns them on
  ([ENGINEERING_PLAN.md](ENGINEERING_PLAN.md) §1 and §2), each with tests and a rollback.
- **Measurement after.** [PERFORMANCE.md](PERFORMANCE.md), with regressions and costs in its §6.
- **Regression.** [REGRESSION_REPORT.md](REGRESSION_REPORT.md).
- **Independent review.** A code review and a security review of the whole change set by reviewers that did not
  write it: §4.

## 2. Definition of done

| Item | Status | Evidence |
|---|---|---|
| The existing implementation has been inspected | Done | Three subsystem audits with file-level findings; [ARCHITECTURE_AUDIT.md](ARCHITECTURE_AUDIT.md) |
| Functionality inventoried and baselined | Done, with one gap | Inventory in the audit; `accel_baseline_*.json`. Gap: the browser suite was not run at baseline; the one browser test that failed afterwards was run on the baseline commit (it passes there) and bisected |
| NVIDIA hardware and software compatibility verified | Done for what was adopted; documented for the rest | Hardware measured on the host. Adopted software runs here. For software not adopted, compatibility is from NVIDIA's documentation and anonymous registry manifests, not from running it: **unverified in execution**, and NVIDIA validates none of it on this operating system |
| Each candidate evaluated against real alternatives | Done | Adoption matrix; measured comparisons for the engine flags, speculative decoding, lexical against embedding against reranker, reranker against the answer model as judge |
| High-value, feasible integrations implemented | Done | Scheduling flags; memory models (`deploy/memory`, `semantic.py`) |
| Confirmed critical gaps remediated | Done for correctness and safety; three capability gaps left to the owner | Plan §1. Open by decision: consolidation of expiring evidence, the ask budget, the sparse graph (plan §4). Open as next work: contradictions in other words, the calibration loop (plan §5) |
| Cognitive performance re-evaluated | Done | Reasoning 15 of 16 and tools 8 of 8 before and after; 46 model answers of 51 before and after; the critique's marks measured |
| Context and memory correctness validated | Done | Long-context recall 3 of 3; memory benchmark by class with held-out subjects; the memory-answer check 86 of 96 and 1 of 288; provenance and timestamps untouched (the same rows, reordered) |
| Hardware utilisation measured and optimised where practical | Done | GPU 96 % at one stream; queue, prefill, decode, cache, power and temperature now reported; scheduling flags. No further headroom was found to recover: the limit is single-stream decode |
| Multimodal and distributed capabilities remain operational | Partly verifiable | Voice, Pocket and Presence pass their suites and the browser suite with synthetic media. **Unverified**: a real microphone, a real handset (as in 0.7.0) |
| Existing features pass regression testing | Done | `bash scripts/check.sh` on the final tree: 3483 passed, 18 skipped, Node checks and 8 of 8 reproduction pass (baseline 3337 passed, 18 skipped); browser suite: 194 passed, 1 skipped, 1 failed on the final tree: `test_add_a_phone_from_the_console_to_a_paired_phone` saw Chromium's `net::ERR_NETWORK_CHANGED` when another tenant's container brought up a network interface on this shared host (system log, same second). Its file was rerun three times on the same tree: 3 of 3 pass each time. The full run before the last review fixes: 195 passed, 1 skipped. A second full run on the same tree afterwards: 195 passed, 1 skipped, exit 0; acceptance exit 0; offline benchmark metrics identical |
| Edge cases and recovery scenarios pass | Done for those listed | [REGRESSION_REPORT.md](REGRESSION_REPORT.md) §4. **Unverified**: GPU memory exhaustion, a failed image upgrade and its rollback, days of autonomy |
| New and existing services have observability | Done | `nsf resources`, `GET /api/resources`, the ledger, `nsf llm status`, `nsf web memory`, the `retrieval` and `memory_check` fields. The memory containers are counted by the monitor; they are not managed by the idle policy |
| Improvements supported by reproducible evidence | Done | Scripts and evidence files; repeat runs for the engine flags; a dev and held-out split for memory |
| No material unapproved regressions remain | Done, with the costs listed | [PERFORMANCE.md](PERFORMANCE.md) §6. The costs of the optional features are the owner's to accept by turning them on |
| All significant changes have rollback procedures | Done | Plan §1, last column; the runbook |
| Documentation matches the delivered implementation | Done | The architecture, resource, memory, contract and runbook pages were updated from the code; this directory |

## 3. What is running on the host now

| Service | State after this review |
|---|---|
| `neuroscifi-vllm` | Running the same model and image with `--scheduling-policy priority --max-num-batched-tokens 4096 --enable-prompt-tokens-details`. Roll back with the two lines of plan §1 row 8 |
| `neuroscifi-memory-embed`, `neuroscifi-memory-rerank` | Started, measured and then **stopped and removed** at the end of this review: no individual on this host is configured to use them, and they take 8.7 GiB. `deploy/memory/run.sh start` brings them back (about 20 s) |
| `neuroscifi-voice-asr`, `neuroscifi-voice-tts`, `neuroscifi-searxng` | Not touched |
| The soak run (`nsf-soak`) from an earlier session | Not touched; it runs a frozen copy of the 0.7.0 code and has no model configured |
| The host's other tenants' containers | Not touched |
| Model weights added | `~/.local/share/neuroscifi/models/qwen3-embedding-0.6b`, `qwen3-reranker-0.6b` (1.2 GB each, Apache-2.0, pinned revisions) |
| Keys | `~/.config/neuroscifi/memory/api_key` created by `deploy/memory/run.sh` (0600) |

No owner instance exists on this host, so no individual's settings were changed. The new defaults apply to an
individual when its services next start; the optional features need the commands in the plan.

## 4. Independent review

A code reviewer and a security reviewer, neither of which wrote the changes, read the whole change set
(`9e4570a..HEAD`, source, deploy scripts and tests), reproduced what they suspected, and reviewed again after the
first fixes.

- **First pass:** one serious defect, reproduced by both: a restore made from a restored copy brought a forgotten
  epoch back. Two key-handling defects: the telemetry scrape forwarded the model server's key through a redirect
  or a proxy, and a memory model was sent the answer model's key by default. Eleven smaller findings.
- **Second pass:** both earlier fixes confirmed correct ("fix (a): correct and complete"; sequences of forgets,
  restores and reused epoch names walked by hand). The proxy path of the scrape was still open and was closed. Two
  further defects with plausible triggers (a memory answer losing its approved facts; one skewed measurement
  blocking model reloads) and several minor ones.
- **Outcome:** every finding with a concrete trigger is fixed with a test that fails on the earlier code
  ([ENGINEERING_PLAN.md](ENGINEERING_PLAN.md) §1, "Found by the independent reviews and fixed"). Those left open are
  listed in the plan's §5, item 10, with the reason. The fixes made after the second pass were not reviewed a third
  time; they are covered by their tests and the final regression run.
- **Found sound by the reviewers:** read-ahead at 1 is exactly the old code path, and above 1 charges and cache
  writes are applied only on a claim; the migration fix leaves no open transaction; the worker wake-up cannot lose
  a task and does not spin; memory texts are marked private and pinned to their provider's own address; the memory
  check has no channel to send anything out; priority cannot be set by untrusted input; the new telemetry is on the
  loopback console only; the memory deploy script is hardened as the others are.

One of the findings changed a measured number: judging each statement in its own call made the memory-answer check
stricter (86 of 96 right facts accepted instead of 94), which this page and [PERFORMANCE.md](PERFORMANCE.md) now
state.

## 5. Outstanding issues and residual risks

1. **Learning does not persist** beyond the evidence lifetime (C3). This is the main distance between the
   architecture and the running system, and it is unchanged.
2. **Contradictions worded differently are not detected** (W2); the knowledge loops are open (W7).
3. **The self-critique pass** costs 42 % of a task for marks that are mostly false alarms; it is on until the owner
   decides (plan §4 A).
4. **Semantic retrieval's floor and the reranker's instruction were chosen on 130 queries over 48 fictional
   facts.** Held-out subjects agree, but it is one small English set. The floor should be revisited on the owner's
   real memory; `nsf web memory config --set floor=…` changes it and `retrieval` in every result shows what was
   dropped.
5. **The memory-answer check is a model's judgement.** It can only withhold a memory answer, and errs towards web
   research (10 of 96 right facts refused). One wrong-property fact in 288 was accepted by the model; in use it
   would also have to pass the lexical test.
6. **Read-ahead above 1** lets already-sent requests reach sites after a cancel or a denial, and its unused
   requests are outside the daily caps (plan §4 B). It is off.
7. **The memory models cost 8.7 GiB** on a shared host with about 31 GiB available, and are outside the idle
   policy.
8. **A call that triggers an idle reload** can take up to twice its timeout.
9. **Nothing here was validated by NVIDIA for this operating system**, and nothing was tested on a handset, with a
   real microphone or against the live internet.
10. **Tombstones and the deletion log hold epoch names and event ids in clear**, and the restore's replay of epoch
    forgets depends on the deletion log being kept.

## 6. Recommended next priorities

In order: decide A, B and C of the plan; consolidation of expiring evidence (D); paraphrased contradictions with
the embedding model as a candidate source; the calibration loop; idle management and a lighter server for the
memory models; a TensorRT-LLM comparison with this review's harness if a faster decode is wanted more than the
current model's exact quantisation.
