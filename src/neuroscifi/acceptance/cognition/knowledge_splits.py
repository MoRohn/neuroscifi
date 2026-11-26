"""Frozen worlds of lane K (recursive knowledge evolution), written and committed BEFORE any mechanism code of the lane.

No setting of the lane may be chosen on HELD_OUT: settings are chosen on DEVELOPMENT and reported on HELD_OUT once.
The withheld family (withheld.py) stays the main session's; nothing here is tuned on it.

RECHECK worlds (scripts/bench_genesis_knowledge.py, family "retention under change"): a staged individual holds
`items` knowledge items verified `age_days` ago (accepted with probability `accepted_share`, else provisional; release
class with probability `release_share`, else stable), each with its "still holds" prediction as the orchestrator records
it. Every step (`step_hours`) the world's truth of an item changes with probability volatility[class]; `arrivals` new
items are verified by the owner's research (new knowledge arrives); `crowd` contested subjects that are not knowledge
stay in memory (the crowding lane S measured). `budget` investigations per step re-verify items; one investigation is a
research outcome of the given reliability fed through the real consolidation. A share `dependents.share` of items carry
1..dependents.max hypotheses that rest on them (alternatives recorded against the item). Scores: share of live items
that state the world's current truth (unweighted, by owner importance), old items (present at step 0) and new
items separately, stale facts caught per investigation, items lost.

STRATEGY worlds (strategy selection by Thompson sampling, family "learning to learn"): each step `candidates`
research opportunities are offered, each with a research mode; a mode's investigations produce verified knowledge with
probability success[mode] (external outcome, known only to the world). `budget` investigations per step. The
"inflator" mode records many completed runs and self-reported success without producing anything (reward hacking).
The bench runs the inflator as the real mode "continuous". Scores: verified items gained per investigation; share
of investigations on the inflator.
"""
from __future__ import annotations

RECHECK = {
    "development": {
        "seeds": (11, 12, 13),
        "worlds": (
            {"name": "dev-steady", "items": 18, "accepted_share": 0.35, "release_share": 0.5,
             "volatility": {"release": 0.10, "stable": 0.03}, "age_days": (3.0, 40.0), "steps": 14,
             "step_hours": 24.0, "budget": 1, "arrivals": 1, "crowd": 6, "reliability": 0.95,
             "dependents": {"share": 0.25, "max": 3}},
            {"name": "dev-volatile", "items": 14, "accepted_share": 0.5, "release_share": 0.7,
             "volatility": {"release": 0.18, "stable": 0.05}, "age_days": (1.0, 25.0), "steps": 12,
             "step_hours": 24.0, "budget": 2, "arrivals": 2, "crowd": 10, "reliability": 0.9,
             "dependents": {"share": 0.35, "max": 2}},
        ),
    },
    "held_out": {
        "seeds": (101, 102, 103, 104),
        "worlds": (
            {"name": "ho-sparse-budget", "items": 22, "accepted_share": 0.3, "release_share": 0.6,
             "volatility": {"release": 0.12, "stable": 0.04}, "age_days": (2.0, 50.0), "steps": 16,
             "step_hours": 24.0, "budget": 1, "arrivals": 1, "crowd": 12, "reliability": 0.93,
             "dependents": {"share": 0.3, "max": 3}},
            {"name": "ho-busy", "items": 16, "accepted_share": 0.45, "release_share": 0.4,
             "volatility": {"release": 0.15, "stable": 0.06}, "age_days": (1.0, 30.0), "steps": 12,
             "step_hours": 36.0, "budget": 2, "arrivals": 3, "crowd": 8, "reliability": 0.9,
             "dependents": {"share": 0.2, "max": 4}},
        ),
    },
}

STRATEGY = {
    "development": {
        "seeds": (21, 22, 23),
        "worlds": (
            {"name": "dev-two-good", "steps": 40, "budget": 1, "candidates": 4,
             "success": {"fact_verification": 0.55, "gap_resolution": 0.30, "quick_lookup": 0.15, "inflator": 0.0}},
            {"name": "dev-close", "steps": 40, "budget": 1, "candidates": 3,
             "success": {"fact_verification": 0.40, "gap_resolution": 0.50, "quick_lookup": 0.35, "inflator": 0.0}},
        ),
    },
    "held_out": {
        "seeds": (201, 202, 203, 204),
        "worlds": (
            {"name": "ho-quick-wins", "steps": 50, "budget": 1, "candidates": 4,
             "success": {"fact_verification": 0.25, "gap_resolution": 0.20, "quick_lookup": 0.60, "inflator": 0.0}},
            {"name": "ho-flat", "steps": 50, "budget": 1, "candidates": 3,
             "success": {"fact_verification": 0.35, "gap_resolution": 0.35, "quick_lookup": 0.30, "inflator": 0.0}},
        ),
    },
}
