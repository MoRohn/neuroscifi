"""ADR-064 (Wave 4 lane A): the division of the day's autonomous task count between refreshing what is held and
reaching subjects that hold nothing (WebConfig.allowance_division, reach_share). Off ("race") by default."""
import json

import pytest

from neuroscifi.instance import store
from neuroscifi.instance.errors import ValidationError
from neuroscifi.webcortex import autonomy, scheduler, state
from test_autonomy import STATEMENT, T0, curious, hypothesize
from test_orchestrator import open_individual

CAP = {"tasks": 4, "requests": 400, "bytes": 80_000_000}


@pytest.fixture
def individual(tmp_path):
    yield from open_individual(tmp_path)


def divided(db, rule="reserve", share=0.5, cap=CAP):
    state.set_config(db, {"allowance_division": rule, "reach_share": share, "daily_autonomous": cap,
                          "autonomous": True}, T0)
    autonomy.enable(db, T0)


def test_race_is_the_default_and_reads_nothing(individual, monkeypatch):
    db, _ = individual
    cfg = state.get_config(db)
    assert (cfg.allowance_division, cfg.reach_share) == ("race", 0.25)

    def boom(*a, **k):
        raise AssertionError("read under race")
    for name in ("_reach_tasks", "_reach_open", "_yield_share", "candidates"):
        monkeypatch.setattr(autonomy, name, boom)
    assert autonomy.division(db, T0) == {"rule": "race", "held": 0}
    monkeypatch.setattr(autonomy, "division", boom)
    report = {"skipped": []}
    assert scheduler._division(db, T0, cfg, report) == 0 and report == {"skipped": []}


def test_a_reserve_holds_places_for_a_reaching_candidate_and_refreshing_may_not_take_them(individual):
    db, _ = individual
    hypothesize(db, STATEMENT, 0.5)                    # an open owner hypothesis: a candidate that is no knowledge item
    divided(db)
    out = autonomy.division(db, T0)
    assert out | {"share": 0.5} == out and (out["reserve"], out["reached"], out["reach_open"], out["held"]) == \
        (2, 0, True, 2)
    cfg = state.get_config(db)
    assert scheduler._over_cap(db, T0, cfg, held=2) is None
    curious(db, "Orchid database replicas")
    curious(db, "Maple cache eviction")
    assert scheduler._over_cap(db, T0, cfg) is None                 # the cap itself is not reached (2 of 4)
    assert autonomy.HELD_FOR_REACHING in scheduler._over_cap(db, T0, cfg, held=2)
    # the idle investigator may take a held place for reaching, and records which path it took
    queued = autonomy.idle(db, T0, worker="a")
    assert queued["action"] == "queued"
    decision = queued["candidate"]["division"]
    assert decision["path"] == "reach" and decision["held"] == 2 and decision["refresh_room"] == 0
    assert autonomy._reach_tasks(db, T0) == 1


def test_the_reserve_is_released_when_nothing_reaching_is_admitted_or_autonomy_is_not_enabled(individual):
    db, _ = individual
    divided(db)
    out = autonomy.division(db, T0)
    assert (out["reserve"], out["reach_open"], out["held"]) == (2, False, 0)      # no reaching candidate: released
    hypothesize(db, STATEMENT, 0.5)
    assert autonomy.division(db, T0)["held"] == 2
    autonomy.disable(db, T0)                            # only the idle investigator reaches: without it, released
    assert autonomy.division(db, T0)["held"] == 0


def test_a_failing_division_holds_nothing_and_is_reported(individual, monkeypatch, caplog):
    db, _ = individual
    hypothesize(db, STATEMENT, 0.5)
    divided(db)

    def boom(*a, **k):
        raise RuntimeError("Zephyr secret")
    monkeypatch.setattr(autonomy, "division", boom)
    report = {"skipped": []}
    assert scheduler._division(db, T0, state.get_config(db), report) == 0
    assert report["skipped"] == [{"what": "allowance division", "reason": "failed: RuntimeError"}]
    assert "Zephyr" not in caplog.text and "RuntimeError" in caplog.text


def _finished(db, now, *, reach, wrote):
    task = curious(db, f"Subject {now} {reach} {wrote}", now=now)
    with store.write(db):
        db.execute("UPDATE web_tasks SET status='completed', finished_at=?, result=? WHERE id=?",
                   (now, json.dumps({"consolidation": {"provisional": [1] if wrote else [], "superseded": []}}),
                    task["id"]))
        state.log_event(db, task["id"], "decision", {"autonomy": {"division": {"path": "reach" if reach else
                                                                                "refresh"}}}, now)


def test_the_yield_share_reads_what_each_path_wrote_to_knowledge_within_its_bounds(individual):
    db, _ = individual
    state.set_config(db, {"daily_autonomous": CAP | {"tasks": 1000}}, T0)
    share, inputs = autonomy._yield_share(db, T0)
    assert share == 0.5 and inputs["reach"] == {"finished": 0, "wrote_knowledge": 0, "yield": 0.5}
    for k in range(4):
        _finished(db, T0 - 3600 * (k + 1), reach=True, wrote=True)
        _finished(db, T0 - 3600 * (k + 1) - 60, reach=False, wrote=False)
    share, inputs = autonomy._yield_share(db, T0)
    assert inputs["reach"]["wrote_knowledge"] == 4 and inputs["refresh"]["finished"] == 4
    assert share == pytest.approx((5 / 6) / (5 / 6 + 1 / 6))
    for k in range(40):             # refreshing writes nothing: (1/46) against (5/6), bounded above
        _finished(db, T0 - 60 * k - 7, reach=False, wrote=False)
    assert autonomy._yield_share(db, T0)[0] == autonomy.YIELD_BOUNDS[1]
    assert autonomy._yield_share(db, T0 + 8 * 86400.0)[0] == 0.5         # outside the window: the prior


@pytest.mark.parametrize("name, value", [("allowance_division", "always"), ("allowance_division", True),
                                         ("reach_share", 0), ("reach_share", 1), ("reach_share", "half"),
                                         ("reach_share", float("nan")), ("reach_share", True)])
def test_the_settings_refuse_what_they_cannot_mean(individual, name, value):
    db, _ = individual
    with pytest.raises(ValidationError):
        state.set_config(db, {name: value}, T0)


def test_a_damaged_saved_division_reads_as_race(individual):
    db, _ = individual
    from neuroscifi.webcortex import config as wconfig
    with store.write(db):
        db.execute("UPDATE meta SET value=json_set(value,'$.allowance_division','always','$.reach_share',7) "
                   "WHERE key=?", (wconfig.META_KEY,))
    cfg = state.get_config(db)
    assert (cfg.allowance_division, cfg.reach_share) == ("race", 0.25)
    assert len(state.setting_problems(db)) == 2
