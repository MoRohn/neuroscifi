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


def test_every_configuration_meets_the_same_world_under_the_decoupled_draw(monkeypatch):
    """Lane Y (H3): with one stream for the world and the outcomes, a configuration that investigates other subjects
    shifts every later truth change (development world dev-mixed, seed 61, diverges at step 8); decoupled, the
    truth trajectory is the same under every configuration, so paired differences are paired on the world."""
    from neuroscifi.acceptance.cognition import knowledge
    spec = next(w for w in integrated_splits.INTEGRATED["development"]["worlds"] if w["name"] == "dev-mixed")
    trajectories, real = [], knowledge._World.step_world

    def step_world(self):
        real(self)
        trajectories[-1].append(dict(self.truth))
    monkeypatch.setattr(knowledge._World, "step_world", step_world)
    seen = {}
    for decoupled in (False, True):
        for name in ("baseline", "integrated"):
            trajectories.append([])
            links, defaults = integrated.CONFIGS[name]
            integrated.run_world(spec, 61, links, defaults=defaults, decoupled=decoupled)
            seen[decoupled, name] = trajectories[-1]
    assert seen[False, "baseline"] != seen[False, "integrated"]
    assert seen[True, "baseline"] == seen[True, "integrated"]


def test_the_configuration_is_applied_before_the_staging(tmp_path):
    """Lane Y (L1): the baseline (premise_recheck and hypothesis_fields off) stages no premise or hypothesis-detail
    rows; before, the staging ran at the shipped defaults and wrote them in every configuration."""
    from neuroscifi.instance import store
    db = store.create(tmp_path / "l.sqlite", integrated.T0, note="test")
    try:
        integrated.World(TINY, 5, db, *integrated.config(()))
        assert db.execute("SELECT count(*) FROM web_hypotheses").fetchone()[0] > 0
        assert db.execute("SELECT count(*) FROM web_premises").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM web_hypothesis_details").fetchone()[0] == 0
    finally:
        db.close()


def test_the_integrated_mechanism_forces_only_its_links_and_keeps_what_each_case_staged():
    """Lane Y (H2): the retention and adversarial families' cases stage their own settings; the "integrated"
    mechanism forces the links' settings on top of them and nothing else (before, the whole configuration was
    forced, switching every Wave 1 setting off inside each case)."""
    import json
    from neuroscifi.instance.store import InstanceConfig
    from neuroscifi.webcortex.config import WebConfig
    staged_web = {"reflection": True, "answer_monitoring": True, "selective_answering": True, "idle_recheck": False}
    staged_inst = {"predictive_regulation": True, "broadcast": "log"}
    with integrated.switched_on():
        web = WebConfig.from_json(json.dumps(staged_web))
        inst = InstanceConfig.from_json(json.dumps(staged_inst))
    assert web.reflection is True and web.answer_monitoring is True and web.selective_answering is True
    assert inst.predictive_regulation is True and inst.broadcast == "log"
    assert web.idle_recheck is True and web.salience_attention is True and web.arbitration_record is True
    assert inst.imagine_scenarios is True
    web_forced, inst_forced = integrated.forced(tuple(integrated.LINKS))
    assert "reflection" not in web_forced and "predictive_regulation" not in inst_forced


def test_each_run_counts_what_the_links_did():
    """Lane Y (M1): a link that acted but changed nothing investigated is "not exercised", which needs the counts."""
    row = integrated.run_world(TINY, 5, ("salience_attention", "learning_progress"))
    for key in integrated.ACTIVITY + ("marks_rows", "premise_rows"):
        assert isinstance(row[key], int), key
    assert row["admissions"] > 0 and len(row["launches"]) == len(row["held"]) == TINY["steps"]


def test_the_integrated_mechanism_is_registered_for_retention_and_adversarial():
    for family in ("retention", "adversarial"):
        mech = next(m for m in cognition.mechanisms() if (m.family, m.name) == (family, "integrated"))
        assert set(mech.components) == set(integrated.LINKS)
