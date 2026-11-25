"""WITHHELD TASK FAMILY. NO LANE MAY TUNE ON THIS FILE, READ ITS SCORES WHILE DEVELOPING, OR CHANGE IT.

It measures transfer: the same interfaces as the families in splits.py (a regulator, an investigation chooser, an
answerer, a self-description) on worlds of kinds that appear in neither the development nor the held-out split:
traces whose leaks accelerate and whose voice sessions follow the owner's backlog, an investigation world with more
noise than signal, causal structures with longer paths and actions on hidden routes, and staged states of other
shapes. A mechanism that gains on development and held-out but not here has learned the benchmark, not the task
(directive §10 "Generalization: performance on genuinely novel and withheld tasks"; §12 "Benchmark overfitting").

The harness (scripts/bench_cognition.py) refuses to score this family unless `--final` is passed, and every --final
run is written into its output ("final_runs") and appended to the ledger docs/quality/evidence/genesis_final_uses.jsonl,
so a withheld number that was looked at more than once shows that it was. Written with splits.py, before any tuning;
tests/acceptance/test_cognition_bench.py pins its SHA-256 digest.
"""

WITHHELD = {
    "seeds": (9001, 9002, 9003),
    "anticipation": (
        {"name": "accelerating leak (slope doubles twice)",
         "memory": {"base_gib": 70.0, "noise_gib": 0.6,
                    "leaks": [{"start": 70, "slope": 0.4, "until": 100, "release": False},
                              {"start": 100, "slope": 0.8, "until": 120, "release": False},
                              {"start": 120, "slope": 1.6, "until": 135, "release": True}]},
         "latency": {"base_ms": 950.0, "noise_ms": 80.0}, "demand": 2},
        {"name": "voice sessions right after each backlog burst",
         "memory": {"base_gib": 70.0, "noise_gib": 0.6}, "latency": {"base_ms": 950.0, "noise_ms": 80.0},
         "backlog": {"period": 55, "size": 2, "duration": 4, "phase": 15},
         "audio": {"period": 55, "duration": 8, "phase": 21, "jitter": 1}, "demand": 2},
        {"name": "latency drift during a leak, background demand that varies",
         "memory": {"base_gib": 69.0, "noise_gib": 0.7,
                    "leaks": [{"start": 110, "slope": 0.9, "until": 160, "release": True}]},
         "latency": {"base_ms": 950.0, "noise_ms": 80.0, "drift": [{"start": 120, "slope_ms": 35.0, "until": 165}]},
         "demand": [{"from": 0, "value": 1}, {"from": 100, "value": 2}, {"from": 200, "value": 0}]},
    ),
    "info_value": {
        "core": (
            {"name": "three noisy partners, one honest partner seen once", "reference_roots": 3,
             "reference_readings": 4, "cameras": ({"root": "cam-1", "truth": 1, "initial": 2},),
             "partners": ({"root": "p-e", "truth": 0, "initial": 1},
                          {"root": "p-noise-1", "truth": 1, "initial": 2, "noise": True},
                          {"root": "p-noise-2", "truth": 0, "initial": 3, "noise": True},
                          {"root": "p-noise-3", "truth": 1, "initial": 4, "noise": True}),
             "budget": 8, "per_step": 3},
        ),
        "web": (
            {"name": "three noisy subjects, one stale, one gap", "budget": 7, "step_hours": 28.0,
             "subjects": ({"id": "garnet", "kind": "noise"}, {"id": "haddock", "kind": "noise"},
                          {"id": "isobar", "kind": "noise"},
                          {"id": "juniper", "kind": "stale", "prior": 0.75, "reliability": 0.95, "age_days": 200.0},
                          {"id": "kestrel", "kind": "gap", "prior": 0.65, "reliability": 0.9})},
        ),
    },
    "counterfactual": {
        "scm": ({"structure": "chain", "nodes": 5, "queries": 8}, {"structure": "confounded", "nodes": 5,
                                                                    "queries": 4}),
        "graph": ({"world_seed": 41, "horizons": (1, 4), "queries": 6, "action_queries": 4},),
        "belief": ({"partner_words": 5, "queries": 3},),
    },
    "self_model": (
        {"name": "everything at once", "running_tasks": 2, "queued_tasks": 4, "owner_questions": 4,
         "autonomy_enabled": True, "autonomous_searches_used": 20, "memory_level": "elevated", "writes_paused": True,
         "audio": True, "unidentifiable": True, "vllm_unloaded": True, "last_decision": "defer"},
    ),
}
