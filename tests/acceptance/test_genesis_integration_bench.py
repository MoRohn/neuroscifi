"""Wave 2 lane L's benchmark (acceptance/cognition/integrated.py): frozen worlds, determinism, the configurations and
the invariants every configuration keeps."""
import hashlib
from pathlib import Path

from neuroscifi.acceptance import cognition
from neuroscifi.acceptance.cognition import integrated, integrated_splits, knowledge_splits, splits

ROOT = Path(__file__).resolve().parents[2]
TINY = {"name": "tiny", "items": 4, "accepted_share": 0.5, "release_share": 0.5,
        "volatility": {"release": 0.3, "stable": 0.1}, "age_days": (2.0, 20.0), "steps": 4, "step_hours": 24.0,
        "budget": 1, "idle": 1, "arrivals": 1, "crowd": 2, "reliability": 0.9, "dependents": {"share": 0.5, "max": 2}}


def test_the_worlds_are_frozen_and_their_seeds_are_new():
    """Committed alone before the runner and any link of the lane (a625b96); a change would let a link be tuned on
    held-out."""
    digest = hashlib.sha256((ROOT / "src/neuroscifi/acceptance/cognition/integrated_splits.py").read_bytes()
                            ).hexdigest()
    assert digest == "1338f5702c94d0088d3b4d55e859cb98cb933ddb4cb47ff5c3ca00ec81d6b370"
    dev, held = (set(integrated_splits.INTEGRATED[s]["seeds"]) for s in ("development", "held_out"))
    assert not dev & held
    used = set(cognition.WIDE_SEEDS)
    for family in vars(splits).values():
        if isinstance(family, dict):
            for part in family.values():
                if isinstance(part, dict):
                    used |= set(part.get("seeds") or ())
    for family in (knowledge_splits.RECHECK, knowledge_splits.STRATEGY):
        for part in family.values():
            used |= set(part["seeds"])
    assert not (dev | held) & used


def test_every_configuration_is_deterministic_and_keeps_the_invariants():
    for name in ("baseline", "integrated", "-salience_attention"):
        links, defaults = integrated.CONFIGS[name]
        one, two = (integrated.run_world(TINY, 5, links, defaults=defaults) for _ in range(2))
        for row in (one, two):          # task ids are random; the probes' verdicts are compared
            row.pop("wall_seconds")
            row["probes"] = [(p["probe"], p["correct"]) for p in row["probes"]]
        assert one == two, name
        assert one["belief_writes"] == 0 and one["conflicts"] == 0 and one["lost"] == 0, name
        assert one["investigations"] > 0, name


def test_the_configurations_are_the_baseline_each_link_alone_and_each_link_ablated():
    configs = integrated.configurations()
    assert configs["baseline"] == ((), False) and configs["defaults"] == ((), True)
    assert configs["integrated"] == (tuple(integrated.LINKS), False)
    for link in integrated.LINKS:
        assert configs[f"alone:{link}"] == ((link,), False)
        assert link not in configs[f"-{link}"][0] and len(configs[f"-{link}"][0]) == len(integrated.LINKS) - 1
    web, inst = integrated.config(())
    assert web["salience_attention"] is False and web["arbitration_record"] is False and web["idle_recheck"] is False
    assert inst["imagine_scenarios"] is False


def test_the_self_description_probes_are_labelled_plumbing_and_answered_once_the_record_has_the_cycle():
    row = integrated.run_world(TINY, 5, ("recheck", "marks", "arbitration"))
    assert row["self_probes"] == 4 and row["self_probes_correct"] == 4
    assert all(p["plumbing_check"] for p in row["probes"])


def test_the_integrated_mechanism_is_registered_for_retention_and_adversarial():
    for family in ("retention", "adversarial"):
        mech = next(m for m in cognition.mechanisms() if (m.family, m.name) == (family, "integrated"))
        assert set(mech.components) == set(integrated.LINKS)
