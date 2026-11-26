"""Wave 3 lane R (ADR-060): the re-check's order (WebConfig.recheck_order). The default is lane K's order, unchanged;
another order only re-orders the same pool (the same items, the same admission inputs), from the individual's own
records, and each pick's decision event says why it was taken."""
import math

import pytest

from neuroscifi.instance.errors import ValidationError
from neuroscifi.webcortex import autonomy, recheck, state
from test_consolidate import DAY, T0, consolidated, open_db, two_sources
from test_knowledge_evolution import _two_items

NEWER = "NumPy 2.4.0 is the latest release of the NumPy library."
TTL = 7.0       # the release class's TTL in days (temporal.FRESHNESS)


@pytest.fixture
def db(tmp_path):
    yield from open_db(tmp_path)


def ranked(db, order, now=T0):
    state.set_config(db, {"recheck_order": order}, now)
    return {c["knowledge_id"]: c for c in recheck.rank(db, now)}


def test_the_setting_is_off_by_default_and_refuses_what_it_does_not_know(db):
    assert state.get_config(db).recheck_order == "staleness"
    state.set_config(db, {"recheck_order": "rotation+seen+rate"}, T0)
    assert state.get_config(db).recheck_order == "rate+seen+rotation"          # stored in its canonical order
    for bad in ("", "age", "rate+rate", "staleness+rate", 3, None):
        with pytest.raises(ValidationError):
            state.set_config(db, {"recheck_order": bad}, T0)


def test_another_order_offers_the_same_items_with_the_same_admission_inputs(db):
    a, b = _two_items(db)
    default = ranked(db, "staleness")
    assert all("order" not in c for c in default.values())            # the default carries nothing new
    for order in ("rotation", "rate", "rate+seen", "rate+seen+rotation", "seen+rotation", "rate+rotation"):
        mine = ranked(db, order)
        assert set(mine) == set(default) == {a, b}
        for kid, c in mine.items():         # what autonomy.admission reads is unchanged: it only orders
            for k in ("info_gain", "p_current", "signals", "staleness", "importance", "kind", "key", "mode"):
                assert c[k] == default[kid][k], (order, k)
            assert c["order"]["rule"] == order


def test_rate_reads_the_subjects_own_verified_changes_with_a_prior(db):
    a, b = _two_items(db)
    _, newer = consolidated(db, [two_sources(text=NEWER, published=T0 - 41 * DAY, hosts=("c.example", "d.example"))],
                            T0 - 40 * DAY)
    assert newer["superseded"] and newer["superseded"][0]["old"] == a       # one verified change of a's subject
    a2 = newer["superseded"][0]["new"]
    mine = ranked(db, "rate")
    changed, quiet = mine[a2]["order"], mine[b]["order"]
    assert changed["changes_seen"] == 1 and quiet["changes_seen"] == 0
    # exposure since the subject was first verified (60 days = 60/7 TTLs); prior: one change per TTL over one TTL
    assert changed["rate_per_ttl"] == pytest.approx(2 / (60 / TTL + 1), abs=1e-5)
    assert quiet["rate_per_ttl"] == pytest.approx(1 / (60 / TTL + 1), abs=1e-5)
    assert changed["p_changed"] == pytest.approx(1 - math.exp(-changed["rate_per_ttl"] * 40 / TTL), abs=1e-5)
    assert mine[b]["value"] == pytest.approx(0.7 * quiet["p_changed"], abs=1e-5)       # importance x chance x 1


def test_seen_makes_a_marked_item_certain_and_rotation_puts_the_longest_waiting_first(db):
    from neuroscifi.instance import store
    from neuroscifi.webcortex import premises
    a, b = _two_items(db)
    with store.write(db):
        premises.mark(db, "knowledge", b, "prediction_failed", {}, T0)
    mine = ranked(db, "rate+seen")
    assert mine[b]["order"]["p_changed"] == 1.0 and mine[b]["order"]["newer_seen"] is True
    assert mine[a]["order"]["p_changed"] < 1.0
    # rotation: a refresh task of `a` yesterday is a look; b has waited 60 days and goes first
    with store.write(db):
        db.execute("INSERT INTO web_tasks(id, objective, mode, status, origin, created_at, updated_at) "
                   "VALUES('look-a', 'NumPy', 'fact_verification', 'completed', ?, ?, ?)",
                   (f"refresh:{a}", T0 - DAY, T0 - DAY))
    mine = ranked(db, "rotation")
    assert mine[a]["order"]["ttls_waited"] == pytest.approx(1 / TTL, abs=1e-5)
    assert mine[b]["order"]["overdue"] is True and mine[a]["order"]["overdue"] is False
    assert recheck.rank(db, T0)[0]["knowledge_id"] == b


def test_each_pick_records_why_and_the_default_pick_records_what_it_did(db):
    a, b = _two_items(db)
    autonomy.enable(db, T0)
    state.set_config(db, {"idle_recheck": True, "autonomous": True, "recheck_order": "rate+seen+rotation"}, T0)
    out = recheck.run(db, T0)
    task_id = out["submitted"][0]["task_id"]
    decision = [e["detail"] for e in state.events(db, task_id) if e["kind"] == "decision"][0]["autonomy"]
    why = decision["recheck"]["order"]
    assert why["rule"] == "rate+seen+rotation" and why["overdue"] is True and why["ttls_waited"] > 1
    assert set(why) >= {"p_changed", "changes_seen", "rate_per_ttl", "ttls_since_verified", "newer_seen"}
    assert decision["admission"] == {"rule": "unified", "path": "recheck"}     # the same rule admitted it
    from neuroscifi import self_description
    assert self_description._to_recheck(db, T0)["value"]["order"] == "rate+seen+rotation"
    state.set_config(db, {"recheck_order": "staleness"}, T0)
    assert "order" not in self_description._to_recheck(db, T0)["value"]
    later = recheck.run(db, T0 + 2 * DAY)["submitted"][0]["task_id"]
    decision = [e["detail"] for e in state.events(db, later) if e["kind"] == "decision"][0]["autonomy"]
    assert "order" not in decision["recheck"]                                 # the default: as before
