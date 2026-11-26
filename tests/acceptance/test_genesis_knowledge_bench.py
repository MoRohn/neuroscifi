"""Lane K's benchmark (acceptance/cognition/knowledge.py): frozen worlds, determinism, the registrations and the
adversarial cases it adds."""
import hashlib
from pathlib import Path

from neuroscifi.acceptance import cognition
from neuroscifi.acceptance.cognition import adversarial, knowledge, knowledge_splits

ROOT = Path(__file__).resolve().parents[2]
TINY = {"name": "tiny", "items": 4, "accepted_share": 0.5, "release_share": 0.5,
        "volatility": {"release": 0.3, "stable": 0.1}, "age_days": (2.0, 20.0), "steps": 3, "step_hours": 24.0,
        "budget": 1, "arrivals": 1, "crowd": 2, "reliability": 0.9, "dependents": {"share": 0.5, "max": 2}}


def test_the_worlds_are_frozen():
    """Committed before any mechanism of the lane (8b8a9c0); a change here would let a setting be tuned on held-out."""
    digest = hashlib.sha256((ROOT / "src/neuroscifi/acceptance/cognition/knowledge_splits.py").read_bytes()).hexdigest()
    assert digest == "0cd8512c2d16418336be3ecde7a2c626d00a8457bc341bf5cb0216e8ebd12815"
    assert not set(knowledge_splits.RECHECK["development"]["seeds"]) & set(knowledge_splits.RECHECK["held_out"]["seeds"])


def test_a_recheck_world_is_deterministic_and_never_writes_a_belief():
    for policy in ("baseline", "recheck"):
        one, two = (knowledge.recheck_world(TINY, 5, policy) for _ in range(2))
        one.pop("wall_seconds"), two.pop("wall_seconds")
        assert one == two and one["belief_writes"] == 0 and one["lost"] == 0


def test_the_strategy_world_scores_external_outcomes_only():
    spec = {"name": "tiny", "steps": 30, "budget": 1, "candidates": 3,
            "success": {"fact_verification": 0.6, "quick_lookup": 0.1, "inflator": 0.0}}
    mech = knowledge.strategy_world(spec, 1, "thompson")
    inc = knowledge.strategy_world(spec, 1, "incumbent")
    assert mech == knowledge.strategy_world(spec, 1, "thompson")
    assert mech["inflator_share"] <= inc["inflator_share"]


def test_the_decoupled_draw_keys_each_outcome_by_step_and_subject_and_leaves_the_recorded_draw_alone(tmp_path):
    """Lane Y (H3): under the decoupled draw an outcome about a subject in a step is the same whatever was drawn
    before it (common random numbers across policies); the recorded (default) draw is unchanged."""
    from neuroscifi.instance import store
    dbs = [store.create(tmp_path / f"k{i}.sqlite", knowledge.T0, note="test") for i in range(2)]
    try:
        world = knowledge._World(TINY, 5, dbs[0], "baseline", decoupled=True)
        world.step = 2
        first = world.outcome_rng("item000").random()
        world.rng.random(), world.outcome_rng("item001").random()
        assert world.outcome_rng("item000").random() == first
        world.step = 3
        assert world.outcome_rng("item000").random() != first
        recorded = knowledge._World(TINY, 5, dbs[1], "baseline")   # the default: the one stream, as recorded
        assert recorded.outcome_rng("item000") is recorded.rng
    finally:
        for db in dbs:
            db.close()


def test_the_decoupled_strategy_draw_offers_both_policies_the_same_candidates():
    """Lane Y (H3): under the recorded draw the offers shift once the two policies choose differently (one stream
    draws the offers and the outcomes); decoupled, both policies are offered the same candidates at every step."""
    from neuroscifi.acceptance.cognition import knowledge_splits as ks
    spec = next(w for w in ks.STRATEGY["development"]["worlds"] if w["name"] == "dev-close")
    offers = {}
    for decoupled in (False, True):
        for policy in ("incumbent", "thompson"):
            offers[decoupled, policy] = []
            knowledge.strategy_world(spec, 22, policy, decoupled=decoupled, offers=offers[decoupled, policy])
    assert offers[False, "incumbent"] != offers[False, "thompson"]
    assert offers[True, "incumbent"] == offers[True, "thompson"]


def test_lane_k_registers_for_retention_adversarial_and_information_value():
    cognition.load_plugins()
    names = {(m.family, m.name) for m in cognition.mechanisms()}
    assert {("retention", "knowledge_evolution"), ("adversarial", "knowledge_evolution"),
            ("info_value", "knowledge_recheck")} <= names


def test_the_lane_k_adversarial_cases_pass_with_and_without_the_mechanisms():
    cases = (adversarial.no_self_confirmation_without_evidence, adversarial.reflection_summary_is_never_evidence,
             adversarial.recheck_and_reflection_stay_bounded, adversarial.self_reported_success_earns_no_reward)
    for case in cases:
        assert case()["pass"] is True, case.__name__
    with knowledge.KnowledgeEvolution().activate():
        for case in cases:
            assert case()["pass"] is True, case.__name__
