"""Frozen worlds of Wave 2 lane L (the integration loop, directive §8; ADR-056), written and committed BEFORE any
integration link of the lane and before the runner that reads them.

No setting of the lane may be chosen on HELD_OUT: anything chosen is chosen on DEVELOPMENT and reported on HELD_OUT.
The withheld family (withheld.py) is not used, read or imported here; splits.py, withheld.py and knowledge_splits.py
are unchanged. Seeds are disjoint from splits.py (11-13, 21-23, 31-33, 41-42 and 10xx), the wide seeds (5001-5012) and
knowledge_splits.py (11-13, 21-23, 101-104, 201-204); every random stream is also keyed by the world's name.

INTEGRATED worlds (scripts/bench_wave2_integration.py; family "integrated"): one staged Web Cortex individual on which
the whole background loop runs through the real modules, step after step:

  the timer pass       webcortex.scheduler.tick(queue=True, max_tasks=`budget`): scenario records, the knowledge
                       re-check, the due predictions' refreshes and the timer's curiosity, in the tick's own order
  the idle path        `idle` calls of webcortex.autonomy.idle (the worker's idle investigation) per step
  the outcomes         every task those queued is answered by a research outcome of reliability `reliability` (the
                       world's current truth of the subject, else the version the individual already holds; noise
                       subjects: two sources that disagree at random, for ever) fed through the real consolidation
                       (webcortex.consolidate.run) under that task, so predictions settle through the real paths
                       (predictions.check_due at the next pass, evolution.observe for a hypothesis's prediction)

`items` knowledge items verified `age_days` ago (accepted with probability `accepted_share`, else provisional;
"release" class with probability `release_share`, else "stable"), each with the "still holds" prediction the
orchestrator records. Every step (`step_hours`) the world's truth of an item changes with probability
volatility[class]; `arrivals` new items arrive (the owner's research); `crowd` contested subjects that are not
knowledge stay in memory (the noisy subjects of the idle path); a share `dependents.share` of items carry 1 ..
`dependents.max` hypotheses resting on them.

Scores per world and seed (computed by the runner, from the world's truth, never from anything the system imagined or
predicted): correctness (share of item subjects whose live knowledge states the current truth, mean over steps and at
the end), stale facts caught per investigation, the share of investigations spent on noise subjects, investigations,
conflicts (two tasks for one subject in one timer pass), belief writes outside consolidation (must be 0), the scenario
records' three-way Brier and log loss against the stated reference of equal futures (when scenario records are on),
self-description probes against the world's bookkeeping, and the cost (wall seconds, peak resident set, tasks).
"""
from __future__ import annotations

INTEGRATED = {
    "development": {
        "seeds": (61, 62, 63),
        "worlds": (
            {"name": "dev-mixed", "items": 14, "accepted_share": 0.4, "release_share": 0.6,
             "volatility": {"release": 0.14, "stable": 0.04}, "age_days": (2.0, 35.0), "steps": 12,
             "step_hours": 24.0, "budget": 2, "idle": 1, "arrivals": 1, "crowd": 4, "reliability": 0.92,
             "dependents": {"share": 0.3, "max": 2}},
            {"name": "dev-noisy", "items": 10, "accepted_share": 0.5, "release_share": 0.5,
             "volatility": {"release": 0.12, "stable": 0.05}, "age_days": (1.0, 20.0), "steps": 12,
             "step_hours": 24.0, "budget": 1, "idle": 1, "arrivals": 1, "crowd": 8, "reliability": 0.9,
             "dependents": {"share": 0.2, "max": 3}},
        ),
    },
    "held_out": {
        "seeds": (601, 602, 603, 604),
        "worlds": (
            {"name": "ho-volatile", "items": 16, "accepted_share": 0.35, "release_share": 0.7,
             "volatility": {"release": 0.18, "stable": 0.06}, "age_days": (1.0, 40.0), "steps": 14,
             "step_hours": 24.0, "budget": 2, "idle": 1, "arrivals": 2, "crowd": 6, "reliability": 0.93,
             "dependents": {"share": 0.35, "max": 3}},
            {"name": "ho-crowded", "items": 12, "accepted_share": 0.45, "release_share": 0.45,
             "volatility": {"release": 0.10, "stable": 0.04}, "age_days": (3.0, 45.0), "steps": 14,
             "step_hours": 36.0, "budget": 1, "idle": 1, "arrivals": 1, "crowd": 10, "reliability": 0.9,
             "dependents": {"share": 0.25, "max": 2}},
        ),
    },
}
