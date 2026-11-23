# Cognitive system review and NVIDIA acceleration (November 2026)

A review of the running 0.7.0 system on its DGX Spark: what it does, where it falls short, which NVIDIA inference
software helps it, and the changes made as a result. Branch `nvidia-acceleration-review`, based on `9e4570a`.

**The short version.** The answer model's engine stays: one stream already saturates the GPU, the server's queue is
empty, and NIM, Triton, Dynamo and TensorRT-LLM either wrap the same engine, need a second GPU, or would mean a
different model. Two flags of the installed engine make the owner's requests wait less behind background work.
The larger gains are not in inference speed. A queued question no longer waits for a 5-second poll. Memory finds a
fact asked in other words (27 % → 98 %) with two small GPU-served models that are off until configured. Memory no
longer answers a question about the wrong property. A restored checkpoint no longer brings back what was forgotten.
The reasoning model, its precision and the sentence guard are unchanged, and no claim of better reasoning is made.

| Document | Deliverable |
|---|---|
| [ARCHITECTURE_AUDIT.md](ARCHITECTURE_AUDIT.md) | What runs, a capability inventory with status, the dependency map, every deficiency found |
| [NVIDIA_ASSESSMENT.md](NVIDIA_ASSESSMENT.md) | The machine, the adoption matrix, why each technology was integrated, deferred or rejected, sources |
| [COGNITIVE_GAP_ANALYSIS.md](COGNITIVE_GAP_ANALYSIS.md) | Reasoning, memory, learning, perception, autonomy and distribution: what is real, what is missing |
| [ENGINEERING_PLAN.md](ENGINEERING_PLAN.md) | Each change with its acceptance and rollback; what was rejected on measurement; the owner's open decisions; next priorities |
| [PERFORMANCE.md](PERFORMANCE.md) | Before and after, with regressions and costs |
| [REGRESSION_REPORT.md](REGRESSION_REPORT.md) | Capability-by-capability preservation, behaviour changed on purpose, edge cases |
| [VERIFICATION_REPORT.md](VERIFICATION_REPORT.md) | The definition of done, item by item, with evidence or "unverified" |

Updated in place rather than duplicated here: the architecture of inference and its adaptive policy
([COGNITIVE_INFERENCE.md](../architecture/COGNITIVE_INFERENCE.md)), resource scheduling and telemetry
([RESOURCE_SCHEDULING.md](../architecture/RESOURCE_SCHEDULING.md)), memory
([MEMORY_INTEGRATION.md](../web-cortex/MEMORY_INTEGRATION.md)), contracts
([API_CONTRACTS.md](../architecture/API_CONTRACTS.md)) and operations ([RUNBOOK.md](../operations/RUNBOOK.md)).

Evidence: `docs/quality/evidence/accel_*.json`. Reproduce with `scripts/bench_inference.py`,
`scripts/bench_pipeline.py` and `scripts/bench_memory.py`; each script's docstring gives the command.
