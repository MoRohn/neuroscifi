"""FROZEN task definitions of the cognitive benchmark: the development split and the held-out split of every family.

Written and committed before any mechanism was built or tuned (Project Cognitive Genesis, lane Z, start commit
07a8728). The rules, for every lane:

- Tune on DEVELOPMENT only. HELD_OUT is for reporting a result once a setting is chosen; it is never used to choose.
- Never edit this file to make a result look better. tests/acceptance/test_cognition_bench.py pins its SHA-256
  digest; a change shows up there and in review, and every earlier number stops being comparable.
- The two splits use disjoint seeds and different world parameters (other slopes, periods, subject mixes, causal
  structures and staged states), so a setting that only fits the development worlds shows a gap on held-out.
- The task family no lane may tune on at all is in withheld.py, not here.

Every world here is synthetic and fictional. Each entry is plain data read by the family module that names it;
the generators are deterministic for a given spec and seed.
"""

# ---- 1. anticipation: runtime traces with known futures ------------------------------------------------------------
# One trace = TICKS ticks of TICK_SECONDS (anticipation.py). Components (all optional):
#   memory:  base_gib (foreground use on top of the resident models), noise_gib, leaks [{start, slope, until,
#            release}], steps [{at, size, for}], shift {at, to_gib, noise_gib}
#   audio:   period, duration, phase, jitter (voice sessions; jitter in ticks, drawn per session from the seed)
#   latency: base_ms, noise_ms, drift [{start, slope_ms, until}], steps [{at, size_ms, for}]
#   backlog: period, size, duration, phase (bursts of queued owner tasks)
#   demand:  autonomous work available (0, 1 or 2 tasks) as a constant or [{from, value}]
ANTICIPATION = {
    "development": {
        "seeds": (11, 12, 13),
        "traces": (
            {"name": "steady (nothing should be done)",
             "memory": {"base_gib": 66.0, "noise_gib": 0.8}, "latency": {"base_ms": 900.0, "noise_ms": 60.0},
             "demand": 2},
            {"name": "slow memory leak, then release",
             "memory": {"base_gib": 70.0, "noise_gib": 0.5,
                        "leaks": [{"start": 60, "slope": 0.55, "until": 150, "release": True}]},
             "latency": {"base_ms": 900.0, "noise_ms": 60.0}, "demand": 2},
            {"name": "fast memory leak",
             "memory": {"base_gib": 72.0, "noise_gib": 0.5,
                        "leaks": [{"start": 80, "slope": 2.2, "until": 100, "release": True}]},
             "latency": {"base_ms": 900.0, "noise_ms": 60.0}, "demand": 2},
            {"name": "sudden spike (cannot be foreseen)",
             "memory": {"base_gib": 74.0, "noise_gib": 0.5, "steps": [{"at": 120, "size": 30.0, "for": 10}]},
             "latency": {"base_ms": 900.0, "noise_ms": 60.0}, "demand": 2},
            {"name": "regime shift to a tighter level",
             "memory": {"base_gib": 64.0, "noise_gib": 0.5, "shift": {"at": 100, "to_gib": 79.0, "noise_gib": 2.0}},
             "latency": {"base_ms": 900.0, "noise_ms": 60.0}, "demand": 2},
            {"name": "periodic voice sessions",
             "memory": {"base_gib": 66.0, "noise_gib": 0.5}, "audio": {"period": 60, "duration": 10, "phase": 25,
                                                                         "jitter": 1},
             "latency": {"base_ms": 900.0, "noise_ms": 60.0}, "demand": 2},
            {"name": "latency drift",
             "memory": {"base_gib": 66.0, "noise_gib": 0.5},
             "latency": {"base_ms": 900.0, "noise_ms": 60.0, "drift": [{"start": 90, "slope_ms": 30.0, "until": 150}]},
             "demand": 2},
            {"name": "periodic owner backlog bursts",
             "memory": {"base_gib": 66.0, "noise_gib": 0.5}, "latency": {"base_ms": 900.0, "noise_ms": 60.0},
             "backlog": {"period": 48, "size": 3, "duration": 5, "phase": 20}, "demand": 2},
        ),
    },
    "held_out": {
        "seeds": (1011, 1012, 1013),
        "traces": (
            {"name": "steady at a higher level",
             "memory": {"base_gib": 72.0, "noise_gib": 1.2}, "latency": {"base_ms": 1100.0, "noise_ms": 90.0},
             "demand": 2},
            {"name": "medium leak with a later release",
             "memory": {"base_gib": 68.0, "noise_gib": 0.7,
                        "leaks": [{"start": 50, "slope": 1.1, "until": 120, "release": True}]},
             "latency": {"base_ms": 1000.0, "noise_ms": 70.0}, "demand": 2},
            {"name": "two leaks of different speed",
             "memory": {"base_gib": 70.0, "noise_gib": 0.6,
                        "leaks": [{"start": 30, "slope": 0.7, "until": 80, "release": True},
                                  {"start": 150, "slope": 1.8, "until": 175, "release": True}]},
             "latency": {"base_ms": 1000.0, "noise_ms": 70.0}, "demand": 2},
            {"name": "spike on top of an elevated level",
             "memory": {"base_gib": 80.0, "noise_gib": 0.6, "steps": [{"at": 90, "size": 22.0, "for": 15}]},
             "latency": {"base_ms": 1000.0, "noise_ms": 70.0}, "demand": 2},
            {"name": "regime shift with a noisier tail",
             "memory": {"base_gib": 62.0, "noise_gib": 0.6, "shift": {"at": 70, "to_gib": 81.0, "noise_gib": 3.0}},
             "latency": {"base_ms": 1000.0, "noise_ms": 70.0}, "demand": 2},
            {"name": "voice sessions with a shorter period and jitter",
             "memory": {"base_gib": 68.0, "noise_gib": 0.6}, "audio": {"period": 37, "duration": 7, "phase": 11,
                                                                         "jitter": 3},
             "latency": {"base_ms": 1000.0, "noise_ms": 70.0}, "demand": 2},
            {"name": "latency step then drift",
             "memory": {"base_gib": 68.0, "noise_gib": 0.6},
             "latency": {"base_ms": 1000.0, "noise_ms": 70.0, "steps": [{"at": 60, "size_ms": 500.0, "for": 40}],
                         "drift": [{"start": 140, "slope_ms": 45.0, "until": 180}]},
             "demand": 2},
            {"name": "backlog bursts with voice sessions",
             "memory": {"base_gib": 68.0, "noise_gib": 0.6}, "latency": {"base_ms": 1000.0, "noise_ms": 70.0},
             "audio": {"period": 70, "duration": 9, "phase": 40, "jitter": 2},
             "backlog": {"period": 33, "size": 2, "duration": 6, "phase": 7}, "demand": 2},
        ),
    },
}

# ---- 2. information value: staged worlds with a known expected information gain per candidate ----------------------
# core: an individual whose mechanism is pinned by reference readings; partners and cameras with known true bits; a
#       "noise" partner whose words are coin flips (the noisy TV: its readings carry no information about anything).
#       One investigation = `per_step` readings of the subject the policy names; `budget` investigations.
# web:  knowledge subjects staged through consolidate.run; one investigation = one research outcome fed back through
#       consolidate.run. kinds: stale (stale but important: the stored version was superseded in the world), noise
#       (sources disagree at random for ever), fresh (verified recently: nothing to learn), gap (one source so far),
#       contested (two sides; the world settles it). prior / reliability define the known information gain.
INFO_VALUE = {
    "development": {
        "seeds": (21, 22, 23),
        "core": (
            {"name": "one noisy partner among two honest ones", "reference_roots": 3, "reference_readings": 4,
             "cameras": ({"root": "cam-1", "truth": 0, "initial": 2},),
             "partners": ({"root": "p-a", "truth": 0, "initial": 2}, {"root": "p-b", "truth": 1, "initial": 4},
                          {"root": "p-noise", "truth": 0, "initial": 4, "noise": True}),
             "budget": 8, "per_step": 3},
            {"name": "a camera seen once, a partner seen often", "reference_roots": 3, "reference_readings": 4,
             "cameras": ({"root": "cam-1", "truth": 1, "initial": 1},),
             "partners": ({"root": "p-a", "truth": 1, "initial": 6}, {"root": "p-noise", "truth": 1, "initial": 6,
                                                                        "noise": True}),
             "budget": 6, "per_step": 3},
        ),
        "web": (
            {"name": "stale important, noisy, fresh, gap", "budget": 6, "step_hours": 30.0,
             "subjects": ({"id": "quarrel", "kind": "stale", "prior": 0.7, "reliability": 0.95, "age_days": 120.0},
                          {"id": "tinsel", "kind": "noise"},
                          {"id": "marrow", "kind": "fresh"},
                          {"id": "lumen", "kind": "gap", "prior": 0.6, "reliability": 0.9})},
            {"name": "contested settles, noisy persists", "budget": 6, "step_hours": 30.0,
             "subjects": ({"id": "pivot", "kind": "contested", "prior": 0.5, "reliability": 0.9},
                          {"id": "drizzle", "kind": "noise"},
                          {"id": "ember", "kind": "stale", "prior": 0.6, "reliability": 0.95, "age_days": 90.0})},
        ),
    },
    "held_out": {
        "seeds": (1021, 1022, 1023),
        "core": (
            {"name": "two noisy partners and an under-observed honest one", "reference_roots": 3,
             "reference_readings": 4, "cameras": ({"root": "cam-1", "truth": 0, "initial": 3},),
             "partners": ({"root": "p-c", "truth": 1, "initial": 1}, {"root": "p-noise-1", "truth": 0, "initial": 5,
                                                                        "noise": True},
                          {"root": "p-noise-2", "truth": 1, "initial": 3, "noise": True}),
             "budget": 8, "per_step": 3},
            {"name": "two cameras, one partner, one noisy", "reference_roots": 4, "reference_readings": 3,
             "cameras": ({"root": "cam-1", "truth": 1, "initial": 2}, {"root": "cam-2", "truth": 0, "initial": 1}),
             "partners": ({"root": "p-d", "truth": 0, "initial": 3}, {"root": "p-noise", "truth": 0, "initial": 4,
                                                                        "noise": True}),
             "budget": 7, "per_step": 2},
        ),
        "web": (
            {"name": "two noisy subjects beside a stale one", "budget": 6, "step_hours": 26.0,
             "subjects": ({"id": "saffron", "kind": "stale", "prior": 0.8, "reliability": 0.95, "age_days": 150.0},
                          {"id": "gossamer", "kind": "noise"}, {"id": "brindle", "kind": "noise"},
                          {"id": "cobalt", "kind": "fresh"})},
            {"name": "gap and contested beside noise", "budget": 5, "step_hours": 36.0,
             "subjects": ({"id": "thimble", "kind": "gap", "prior": 0.7, "reliability": 0.85},
                          {"id": "quill", "kind": "contested", "prior": 0.5, "reliability": 0.9},
                          {"id": "nettle", "kind": "noise"})},
        ),
    },
}

# ---- 3. counterfactual and prospective validity ---------------------------------------------------------------------
# scm:    linear Gaussian structural causal models with exact answers. structure: chain | fork | collider | mediator
#         | confounded (a hidden common cause: the effect of X on Y from observation alone is NOT identifiable).
#         Queries: observational, interventional (do), counterfactual (abduction-action-prediction), and
#         not_identifiable where the right answer is "cannot be answered from what is given".
# graph:  the individual learns and promotes a graph on a lab.graph_bench world (the product path), then answers
#         "what follows" k steps ahead, with and without an action (an external push on one population).
# belief: what-if on the belief model: the posterior of a partner had one of its readings been the other bit.
COUNTERFACTUAL = {
    "development": {
        "seeds": (31, 32, 33),
        "scm": ({"structure": "chain", "nodes": 3, "queries": 8}, {"structure": "fork", "nodes": 3, "queries": 8},
                {"structure": "confounded", "nodes": 3, "queries": 4}),
        "graph": ({"world_seed": 7, "horizons": (1, 3), "queries": 6, "action_queries": 3},),
        "belief": ({"partner_words": 6, "queries": 3},),
    },
    "held_out": {
        "seeds": (1031, 1032, 1033),
        "scm": ({"structure": "collider", "nodes": 3, "queries": 8}, {"structure": "mediator", "nodes": 4,
                                                                       "queries": 8},
                {"structure": "confounded", "nodes": 4, "queries": 4}),
        "graph": ({"world_seed": 19, "horizons": (1, 2, 5), "queries": 6, "action_queries": 3},),
        "belief": ({"partner_words": 9, "queries": 3},),
    },
}

# ---- 4. self-description probes: a staged individual and runtime state with ground truth ----------------------------
SELF_MODEL = {
    "development": {
        "seeds": (41, 42),
        "states": (
            {"name": "busy afternoon", "running_tasks": 1, "queued_tasks": 2, "owner_questions": 2,
             "autonomy_enabled": True, "autonomous_searches_used": 7, "memory_level": "elevated",
             "writes_paused": False, "audio": False, "unidentifiable": True, "vllm_unloaded": False,
             "last_decision": "test_reference"},
            {"name": "quiet night", "running_tasks": 0, "queued_tasks": 0, "owner_questions": 0,
             "autonomy_enabled": False, "autonomous_searches_used": 0, "memory_level": "ok", "writes_paused": False,
             "audio": False, "unidentifiable": False, "vllm_unloaded": True, "last_decision": "act"},
        ),
    },
    "held_out": {
        "seeds": (1041, 1042),
        "states": (
            {"name": "under pressure while talking", "running_tasks": 2, "queued_tasks": 1, "owner_questions": 1,
             "autonomy_enabled": True, "autonomous_searches_used": 19, "memory_level": "critical",
             "writes_paused": False, "audio": True, "unidentifiable": True, "vllm_unloaded": False,
             "last_decision": "ask"},
            {"name": "storage full", "running_tasks": 0, "queued_tasks": 3, "owner_questions": 3,
             "autonomy_enabled": True, "autonomous_searches_used": 2, "memory_level": "ok", "writes_paused": True,
             "audio": False, "unidentifiable": False, "vllm_unloaded": False, "last_decision": "defer"},
        ),
    },
}

# ---- 5. selective answering on the offline corpus's gold labels (webcortex/bench/corpus.py) --------------------------
# Odd-numbered items develop, even-numbered items are held out (the corpus is fixed; the split is by item).
SELECTIVE = {
    "development": {"questions": ("F01", "F03", "F05", "F07", "F09", "F11", "F13", "F15", "F17", "F19", "F21",
                                  "F23", "F25", "F27", "F29", "A01", "A03", "A05", "A07", "A09", "A11")},
    "held_out": {"questions": ("F02", "F04", "F06", "F08", "F10", "F12", "F14", "F16", "F18", "F20", "F22",
                               "F24", "F26", "F28", "A02", "A04", "A06", "A08", "A10", "A12")},
}

# ---- 6. retention: the frozen suite (no split: it is re-run as is) ----------------------------------------------------
# Acceptance scenarios at the reduced sizes tests/acceptance uses, and the offline benchmark's key metrics. A metric
# is retained when it is at least as good as the baseline value recorded in docs/quality/evidence/genesis_baseline.json
# (higher_is_better) or no worse than `tolerance` from it.
RETENTION = {
    "scenarios": ({"name": "birth", "kwargs": {}}, {"name": "grounding", "kwargs": {"lifetimes": 40}},
                  {"name": "lifelong_correction", "kwargs": {}}, {"name": "graph_development", "kwargs": {}}),
    "offline_metrics": (
        {"path": "verification.states.accuracy", "higher_is_better": True, "tolerance": 0.0},
        {"path": "authoritative.answer_correct_top.rate", "higher_is_better": True, "tolerance": 0.0},
        {"path": "authoritative.retrieval_success.rate", "higher_is_better": True, "tolerance": 0.0},
        {"path": "contradictions.detector.all.f1", "higher_is_better": True, "tolerance": 0.0},
        {"path": "citations.per_cited_source.precision", "higher_is_better": True, "tolerance": 0.0},
        {"path": "budgets.ok", "higher_is_better": True, "tolerance": 0.0},
        {"path": "time_sensitive.freshness_compliance.rate", "higher_is_better": True, "tolerance": 0.0},
    ),
}
