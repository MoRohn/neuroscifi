# Cognitive system review and NVIDIA acceleration (November 2026)

A review of the running 0.7.0 system on its DGX Spark: what it does, where it falls short, which NVIDIA inference
software helps it, and the changes made as a result. Branch `nvidia-acceleration-review`, based on `9e4570a`.

**The short version.** The answer model's engine stays: one stream already saturates the GPU, the server's queue is
empty, and NIM, Triton and Dynamo either wrap the same engine or need a second GPU. TensorRT-LLM was first thought
to need a different quantisation; phase 4 found a pre-release that serves the same FP8 model on this GPU, and the
comparison waits for a window the owner chooses. Two flags of the installed engine make the owner's requests wait less behind background work.
The larger gains are not in inference speed. A queued question no longer waits for a 5-second poll. Memory finds a
fact asked in other words (27 % → 98 %) with two small GPU-served models that are off until configured. Memory no
longer answers a question about the wrong property. A restored checkpoint no longer brings back what was forgotten.
The reasoning model and its precision are unchanged, and no claim of better reasoning is made.

**Phase 2, the same day.** The review ended with six decisions for the owner, who delegated them; each was decided
on a measurement, built, and read by independent reviewers in up to three passes. What an expired learning event
taught now stays as a consolidated reading, so a belief no longer returns to its prior after seven days, and every
forget still removes it exactly. Research reads four pages at once by default, and no request is sent after the
owner's stop, which was not true before even for one page at a time. The self-critique reads only sentences in
which the model wrote something of its own (10 calls instead of 45 on the benchmark, 4 or 5 marks instead of 49),
the ask budget refills over seven days, the memory models became a managed service that setup switches on, and a
graph no better than no graph is no longer promoted. The sentence guard is the code of 0.7.0 with one added
refusal: an attempt to change more was caught by a review and withdrawn, and the gaps the reviews found in the
older guard head the list of what is open.

**Phase 3, the next day.** The items phase 2 left open were ruled on, built and reviewed. The sentence guard's
older gaps were closed one change at a time, each held by a differential against the 0.7.0 guard (1,782 inputs, 0
unexplained differences), and contradictions stated in other words are now found and put to the owner, never
decided. Read-ahead's waste is capped, the owner's stop covers searches and a page's sub-requests too, settled
forecasts rank autonomous research, consolidated readings have a limit, and one process serves both memory models
in half the memory. The two-account install now works as installed (verified with real accounts in a user
namespace, not under systemd), after a review found and the round fixed a path to root in its first version.

**Phase 4, the same day.** The list phase 3 left was ruled on, built and reviewed twice, with four questions answered
by the owner. The guard refuses more ways of changing a claim's meaning (still stricter only against the 0.7.0 guard:
1,937 inputs, 0 unexplained differences). Contradictions are found across units and through aliases a source states
(40 of 50 on a fresh held-out set instead of 19, false pairs 1 of 28 instead of 6), and values that cannot be
compared never strengthen knowledge. Forgetting reaches what it used to leave behind (one kind of a name, a
forgotten task's forecasts, a forgotten dataset's graph weights). Belief computation no longer passes over every
reading (465 → 118 ms at a million), a restore no longer loads every tombstone (279 → 4.2 MiB), existing
individuals adopt today's graph rules once (recorded, reversible), the memory models run without a network, Pocket
keeps a question written offline and sends it once, and the TensorRT-LLM comparison is scripted for a window the
owner chooses.

| Document | Deliverable |
|---|---|
| [ARCHITECTURE_AUDIT.md](ARCHITECTURE_AUDIT.md) | What runs, a capability inventory with status, the dependency map, every deficiency found, those of phase 2 included |
| [NVIDIA_ASSESSMENT.md](NVIDIA_ASSESSMENT.md) | The machine, the adoption matrix, why each technology was integrated, deferred or rejected, sources |
| [COGNITIVE_GAP_ANALYSIS.md](COGNITIVE_GAP_ANALYSIS.md) | Reasoning, memory, learning, perception, autonomy and distribution: what is real, what is missing |
| [ENGINEERING_PLAN.md](ENGINEERING_PLAN.md) | Each change with its acceptance and rollback; what was rejected on measurement; the six decisions as made; the open items of phase 3 (§5); next priorities (§6); the remaining items of phase 4 (§7) |
| [PERFORMANCE.md](PERFORMANCE.md) | Before and after, with regressions and costs; §7 for the decisions of phase 2, §8 for phase 3, §9 for phase 4 |
| [REGRESSION_REPORT.md](REGRESSION_REPORT.md) | Capability-by-capability preservation, behaviour changed on purpose, edge cases |
| [VERIFICATION_REPORT.md](VERIFICATION_REPORT.md) | The definition of done, item by item, with evidence or "unverified" |

Updated in place rather than duplicated here: the architecture of inference and its adaptive policy
([COGNITIVE_INFERENCE.md](../architecture/COGNITIVE_INFERENCE.md)), resource scheduling and telemetry
([RESOURCE_SCHEDULING.md](../architecture/RESOURCE_SCHEDULING.md)), memory
([MEMORY_INTEGRATION.md](../web-cortex/MEMORY_INTEGRATION.md)), contracts
([API_CONTRACTS.md](../architecture/API_CONTRACTS.md)) and operations ([RUNBOOK.md](../operations/RUNBOOK.md)).
Phase 2 also wrote two decision records, ADR-044 (consolidation) and ADR-045 (which graph is promoted), in
[ARCHITECTURAL_DECISIONS.md](../architecture/ARCHITECTURAL_DECISIONS.md), the stops and read-ahead sections of
[SEARCH_AND_ACQUISITION.md](../web-cortex/SEARCH_AND_ACQUISITION.md), and the upgrade notice of
[MIGRATIONS.md](../operations/MIGRATIONS.md) (schema 14). Phase 3 wrote ADR-046 (forecasts as read) and updated
ADR-044 (the limit on consolidated readings), the memory and resource pages (contradictions in other words, the
single memory server, the two accounts) and the runbook. Phase 4 wrote ADR-047 (the graph-rule migration) and
ADR-048 (forgetting what was left behind), amended ADR-044 (the copied flags), added schemas 15 and 16 to
MIGRATIONS.md, and updated the contracts, the runbook, the environment page and the TensorRT-LLM section of the
NVIDIA assessment (§5.1).

Evidence: `docs/quality/evidence/accel_*.json` (`accel_decisions_*.json` and `accel_consolidation_cost.json` for
phase 2), `open_items_*.json` (phase 3) and `remaining_*.json` (phase 4). Reproduce with `scripts/bench_inference.py`, `scripts/bench_pipeline.py`,
`scripts/bench_memory.py`, `scripts/bench_consolidation.py`, `scripts/bench_graph_learner.py`,
`scripts/guard_differential.py`, `scripts/bench_knowledge.py`, `scripts/bench_calibration.py` and
`scripts/two_account_check.py`; each script's docstring gives the command.
