"""Lane I's mechanisms in the cognitive benchmark (ADR-051): registered through PLUGINS, run the product code, and
their ablations remove the gain. Development split only (held-out is for the recorded evidence)."""
import json
from pathlib import Path

from neuroscifi.acceptance import cognition
from neuroscifi.acceptance.cognition import instinct

BASELINE = json.loads(Path(cognition.BASELINE).read_text())


def test_lane_i_registers_through_plugins():
    cognition.load_plugins()
    names = {(m.family, m.name): m for m in cognition.mechanisms()}
    assert names[("anticipation", "instinct")].components == ("trend", "footprint", "voice")
    assert names[("info_value", "instinct")].components == ("information_rule", "learning_progress")
    assert ("info_value", "instinct-idle") in names and ("retention", "instinct") in names


def test_predictive_regulation_avoids_memory_breaches_the_reactive_gate_does_not():
    on = cognition.run(["anticipation"], mechanism="instinct")["families"]["anticipation"]["result"]["summary"]
    off = cognition.run(["anticipation"], mechanism="instinct", ablate=("footprint",))
    off = off["families"]["anticipation"]["result"]["summary"]
    base = BASELINE["development"]["families"]["anticipation"]["result"]["summary"]
    assert base["memory.avoided_share"]["mean"] == 0.0
    assert on["memory.avoided_share"]["mean"] > 0.5 and off["memory.avoided_share"]["mean"] == 0.0
    assert on["breach_ticks_all_channels"]["mean"] < base["breach_ticks_all_channels"]["mean"]


def test_learning_progress_spends_less_on_noise_and_its_ablation_is_the_baseline():
    """review1 CM1: after the rework (refreshes count as progress, the stronger of it and futility, recovery with a
    half-life) the noise share still falls on development, but so does the stale share (0.194 -> 0.139), and the
    held-out wider seeds lose bits per budget on the timer path: it ships off (WebConfig)."""
    out = cognition.run(["info_value"], mechanism="instinct")["families"]["info_value"]["result"]["web_mean"]
    base = BASELINE["development"]["families"]["info_value"]["result"]["web_mean"]
    assert out["noise_share"] < base["noise_share"]
    ablated = cognition.run(["info_value"], mechanism="instinct", ablate=("learning_progress",))
    assert ablated["families"]["info_value"]["result"]["web_mean"]["noise_share"] == base["noise_share"]


def test_switching_on_for_a_family_restores_the_product_afterwards():
    from neuroscifi.instance import executive
    from neuroscifi.resources import policy
    from neuroscifi.webcortex import curiosity
    before = (executive.rule_version, curiosity.learning_progress_on, policy.predictive_on)
    with instinct.switched_on():
        assert executive.rule_version(None) == "information-v1" and curiosity.learning_progress_on(None) is True
    assert (executive.rule_version, curiosity.learning_progress_on, policy.predictive_on) == before


def test_the_chooser_stages_each_world_at_its_first_step_whatever_the_objects_id(tmp_path):
    """review1c: staging was remembered by id(db), and CPython reuses the id of a closed database, so a later world
    could run unstaged; only activate() hid that, and the withheld family runs without it."""
    from contextlib import closing
    from neuroscifi.acceptance.cognition.instinct import InstinctChooser
    from neuroscifi.instance import store
    chooser = InstinctChooser()
    first = tmp_path / "a.sqlite"
    with closing(store.create(first, 1.0)) as db:
        chooser._stage({"world": "core", "db": db, "now": 2.0, "history": []})
        assert store.config(db).executive_rule == "information-v1"
        reused = id(db)
    with closing(store.create(tmp_path / "b.sqlite", 1.0)) as db:
        chooser.staged.add(id(db))                # as if this object had the first one's id
        chooser.staged.add(reused)
        chooser._stage({"world": "core", "db": db, "now": 2.0, "history": []})
        assert store.config(db).executive_rule == "information-v1"
        store.set_config(db, {"executive_rule": "rules-v1"}, 3.0)
        chooser._stage({"world": "core", "db": db, "now": 4.0, "history": [{"step": 0}]})     # later steps: left alone
        assert store.config(db).executive_rule == "rules-v1"
