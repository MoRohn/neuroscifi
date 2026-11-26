"""Readiness of real outcomes (Wave 3 lane H; ADR-058): counts of the individual's own settled outcomes against each
learner's minimum, read from the learner's own constants; read only."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from neuroscifi import readiness
from neuroscifi.instance import store
from neuroscifi.resources import shared
from neuroscifi.webcortex import evolution, monitoring, premises, scenarios, state

T0 = 1_800_000_000.0
DAY = 86400.0
NOW = T0 + 100 * DAY


@pytest.fixture
def individual(tmp_path):
    path = tmp_path / "individual.sqlite"
    with closing(store.create(path, T0)) as db:
        state.set_config(db, {"enabled": True}, T0)
    return path


def _task(db, tid, status="completed", mode="fact_verification", at=T0):
    db.execute("INSERT INTO web_tasks(id, objective, mode, status, origin, created_at, updated_at, finished_at) "
               "VALUES(?, 'staged question', ?, ?, 'console', ?, ?, ?)", (tid, mode, status, at, at, at))


def _knowledge(db, kid, task_id=None):
    db.execute("INSERT INTO web_knowledge(id, key, version, statement, status, confidence, uncertainty, "
               "freshness_class, first_verified_at, last_verified_at, task_id, created_at, updated_at) "
               "VALUES(?, ?, 1, 'staged statement', 'accepted', 0.8, 0.2, 'stable', ?, ?, ?, ?, ?)",
               (kid, f"k{kid}", T0, T0, task_id, T0, T0))


def _hypothesis(db, hid, status="open", origin="gap", resolved_at=None):
    db.execute("INSERT INTO web_hypotheses(id, key, statement, origin, status, created_at, updated_at, resolved_at) "
               "VALUES(?, ?, 'staged hypothesis', ?, ?, ?, ?, ?)", (hid, f"h{hid}", origin, status, T0, T0,
                                                                     resolved_at))


def _prediction(db, *, knowledge_id=None, hypothesis_id=None, outcome=None, checked_at=None, probability=0.7,
                forgotten=False):
    detail = json.dumps({"observation": {"forgotten": True}}) if forgotten else "{}"
    db.execute("INSERT INTO web_predictions(knowledge_id, hypothesis_id, expectation, probability, check_after, "
               "created_at, checked_at, outcome, detail) VALUES(?, ?, 'staged', ?, ?, ?, ?, ?, ?)",
               (knowledge_id, hypothesis_id, probability, T0, T0, checked_at, outcome, detail))


def _recheck(db, status, outcome):
    db.execute("INSERT INTO web_rechecks(subject_kind, subject_id, reason, status, outcome, created_at, updated_at) "
               "VALUES('knowledge', 1, 'idle', ?, ?, ?, ?)", (status, outcome, T0, T0))


def _scenario(db, i, settled):
    db.execute("INSERT INTO imagined(id, kind, payload, assumptions, uncertainty, mechanism, mechanism_version, "
               "question, created_at, expires_at, outcome, observed_at) VALUES(?, 'simulation', '{}', '[]', "
               "'staged', ?, '1', 'staged', ?, ?, ?, ?)",
               (f"imagined:{i:032x}", scenarios.MECHANISM, T0, T0 + 365 * DAY,
                '{"futures": 0}' if settled else None, T0 + DAY if settled else None))


def stage(path):
    with closing(store.connect(path)) as db, store.write(db):
        _task(db, "t1")
        _task(db, "t2")
        _task(db, "t3", status="failed")
        _task(db, "t4", status="partial")
        _knowledge(db, 1, "t1")
        _knowledge(db, 2, "t2")
        _knowledge(db, 3, "t3")
        _knowledge(db, 4, "t4")
        _knowledge(db, 5, None)
        _hypothesis(db, 1, "supported", resolved_at=NOW - 5 * DAY)
        _hypothesis(db, 2, "refuted", resolved_at=NOW - 200 * DAY)
        _hypothesis(db, 3, "abandoned")
        _hypothesis(db, 4, "open", origin="owner")
        # knowledge kind: t1 has two decisive verdicts (one task), t2 an inconclusive one, t3 failed (not an answer)
        _prediction(db, knowledge_id=1, outcome="confirmed", checked_at=NOW - 10 * DAY)
        _prediction(db, knowledge_id=1, outcome="error", checked_at=NOW - 2 * DAY)
        _prediction(db, knowledge_id=2, outcome="inconclusive", checked_at=NOW - 3 * DAY)
        _prediction(db, knowledge_id=3, outcome="confirmed", checked_at=NOW - 3 * DAY)
        _prediction(db, knowledge_id=4, outcome="error", checked_at=NOW - 40 * DAY, probability=None)
        _prediction(db, knowledge_id=5, outcome="confirmed", checked_at=NOW - 1 * DAY, forgotten=True)
        _prediction(db, knowledge_id=5)                                          # open
        # hypothesis and owner kinds
        _prediction(db, hypothesis_id=1, outcome="confirmed", checked_at=NOW - 200 * DAY)
        _prediction(db, hypothesis_id=4, outcome="error", checked_at=NOW - 20 * DAY)
        for status, outcome in (("done", "superseded by new evidence"), ("done", "prediction error"),
                                ("done", "confirmed by new evidence"), ("done", "no evidence about it"),
                                ("done", "closed: the item is superseded"), ("dropped", "dropped"),
                                ("marked", None), ("queued", None)):
            _recheck(db, status, outcome)
        _scenario(db, 1, True)
        _scenario(db, 2, False)


def test_an_individual_without_outcomes_is_far_from_every_minimum(individual):
    with shared.read_database(individual) as db:
        out = readiness.report(db, NOW)
    for kind in evolution.RECALIBRATION_KINDS:
        learner = out["learners"][f"forecast_recalibration.{kind}"]
        assert learner["have"] == 0 and learner["missing"] == evolution.RECALIBRATION_MINIMUM
        assert learner["earliest"] == "unknown" and learner["rate_per_day"] == 0
    calib = out["learners"]["answer_calibration"]
    assert calib["minimum"] == monitoring.MIN_ITEMS and calib["minimum_blocks"] == monitoring.MIN_BLOCKS
    assert calib["earliest"] == "unknown"
    assert all(not m["applied"] for m in calib["shipped_maps"].values())     # Wave 2's finding, read from the file
    assert out["predictions"]["open"] == 0 and out["answers"]["with_a_later_verdict"] == 0


def test_the_counts_on_a_staged_individual(individual):
    stage(individual)
    with shared.read_database(individual) as db:
        out = readiness.report(db, NOW)
    k = out["predictions"]["by_kind"]
    assert k["knowledge"]["settled"] == 5 and k["knowledge"]["confirmed"] == 2 and k["knowledge"]["error"] == 2
    assert k["knowledge"]["inconclusive"] == 1
    assert k["hypothesis"]["settled"] == 1 and k["owner"]["settled"] == 1
    assert out["predictions"]["open"] == 1 and out["predictions"]["settled_but_forgotten"] == 1
    month = readiness._month(NOW - 10 * DAY)
    assert k["knowledge"]["by_month"][month]["confirmed"] >= 1
    # answers: t1 and t4 are finished with a decisive verdict on their knowledge; t2's is inconclusive; t3 failed
    assert out["answers"] == {"finished": 3, "with_a_later_verdict": 2, "verdict": out["answers"]["verdict"],
                              "by_month_of_first_verdict": out["answers"]["by_month_of_first_verdict"]}
    assert out["hypotheses"]["settled"] == 2 and out["hypotheses"]["by_status"]["abandoned"] == 1
    assert out["rechecks"] == {"found_a_change": 2, "found_no_change": 1, "undecided": 1,
                               "closed_by_another_path": 1, "dropped": 1, "open": 2}
    assert out["scenarios"] == {"records": 2, "settled": 1}
    # recalibration counts decisive settlements with a forecast, not forgotten: knowledge 3 (t1 twice, t3), not t4
    learner = out["learners"]["forecast_recalibration.knowledge"]
    assert learner["have"] == 3 and learner["missing"] == evolution.RECALIBRATION_MINIMUM - 3
    owner = out["learners"]["forecast_recalibration.owner"]
    assert owner["have"] == 1
    assert out["learners"]["scenario_records"]["have"] == 1
    assert out["learners"]["strategy_selection"]["window"]
    assert out["learners"]["hypothesis_settlement"]["have"] == 2


def test_the_earliest_date_follows_the_observed_rate_and_the_code_minimum(individual, monkeypatch):
    with closing(store.connect(individual)) as db, store.write(db):
        _knowledge(db, 1)
        for i in range(10):                         # ten in the last ten days: one a day
            _prediction(db, knowledge_id=1, outcome="confirmed", checked_at=NOW - 10 * DAY + i * DAY)
    with shared.read_database(individual) as db:
        out = readiness.report(db, NOW)
        learner = out["learners"]["forecast_recalibration.knowledge"]
        assert learner["rate_window_days"] == 10.0 and learner["rate_per_day"] == 1.0
        assert learner["earliest"] == readiness._date(NOW + (evolution.RECALIBRATION_MINIMUM - 10) * DAY)
        # the minimum is the code's constant, read at run time (never restated)
        monkeypatch.setattr(evolution, "RECALIBRATION_MINIMUM", 8)
        assert readiness.report(db, NOW)["learners"]["forecast_recalibration.knowledge"]["earliest"] == "met"
        monkeypatch.setattr(monitoring, "MIN_ITEMS", 1)
        assert readiness.report(db, NOW)["learners"]["answer_calibration"]["minimum"] == 1


def test_the_report_stores_nothing(individual, capsys):
    from neuroscifi import cli
    stage(individual)
    before = hashlib.sha256(Path(individual).read_bytes()).hexdigest()
    assert not os.path.exists(f"{individual}-wal")
    assert cli.main(["readiness", "--db", str(individual)]) in (0, None)
    out = json.loads(capsys.readouterr().out)
    assert out["schema"] == readiness.SCHEMA
    # the file is byte for byte what it was; a read-only open of a WAL database may leave its log, empty
    assert hashlib.sha256(Path(individual).read_bytes()).hexdigest() == before
    assert not os.path.exists(f"{individual}-wal") or os.path.getsize(f"{individual}-wal") == 0
    # and on a connection that refuses every write, the whole report is computed
    with closing(sqlite3.connect(f"file:{individual}?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        assert readiness.report(db, NOW)["answers"]["with_a_later_verdict"] == 2


def test_the_strategy_counts_equal_strategies_outcomes(individual):
    """The bounded count gives what the learner's own function gives (drift guard for the restated reward)."""
    stage(individual)
    with closing(store.connect(individual)) as db, store.write(db):
        _task(db, "t5", mode="quick_lookup")
        _task(db, "t6", mode="quick_lookup", status="partial")
        _knowledge(db, 6, "t5")
        db.execute("UPDATE web_knowledge SET verification='{\"state\":\"supported\"}' WHERE id=6")
        db.execute("UPDATE web_predictions SET task_id='t4' WHERE knowledge_id=4")
        db.execute("UPDATE web_predictions SET task_id='t2' WHERE knowledge_id=2")      # inconclusive: no reward
    with shared.read_database(individual) as db:
        from neuroscifi.webcortex import strategies
        want = {m: {"tasks": o["tasks"], "rewarded": o["rewarded"]} for m, o in strategies.outcomes(db).items()
                if o["tasks"]}
        got = readiness.report(db, NOW)["learners"]["strategy_selection"]["by_mode"]
    assert got == want and got["quick_lookup"] == {"tasks": 2, "rewarded": 1}
    assert got["fact_verification"]["rewarded"] == 1


def test_no_text_of_any_record_is_shown(individual):
    stage(individual)
    with shared.read_database(individual) as db:
        text = json.dumps(readiness.report(db, NOW))
    assert "staged" not in text


def test_the_recheck_outcomes_it_sorts_are_the_ones_the_code_writes():
    source = Path(premises.__file__).read_text()
    for outcome in readiness.CHANGE + readiness.NO_CHANGE + readiness.UNDECIDED:
        head = outcome.split(" ")[0] if outcome.startswith("prediction ") else outcome
        assert head in source, outcome
    assert "closed: " in source
    assert set(readiness.SETTLED_HYPOTHESES) <= set(evolution.HYPOTHESIS_STATES)


def test_the_self_description_carries_it_in_the_owner_scope_only(individual):
    from neuroscifi import self_description as sd
    stage(individual)
    with closing(store.connect(individual)) as db:
        owner = sd.build(db, individual, NOW)
        agent = sd.build(db, individual, NOW, scope="agent")
        family = sd.build(db, individual, NOW, scope="family", family="fam-a")
    value = sd.value(owner, "uncertain.outcomes_readiness")
    assert value["forecast_recalibration.knowledge"] == {"minimum": evolution.RECALIBRATION_MINIMUM, "have": 3,
                                                         "missing": evolution.RECALIBRATION_MINIMUM - 3,
                                                         "earliest": value["forecast_recalibration.knowledge"][
                                                             "earliest"]}
    assert "outcomes_readiness" not in agent.get("uncertain", {})
    assert "outcomes_readiness" not in json.dumps(family)


def test_an_individual_without_web_records(tmp_path):
    path = tmp_path / "plain.sqlite"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE meta(key TEXT, value TEXT)")
    with shared.read_database(path) as db:
        out = readiness.report(db, NOW)
    assert "no Web Cortex records" in out["note"]


def test_a_missing_individual_is_named(tmp_path, capsys):
    from neuroscifi import cli
    assert cli.main(["readiness", "--db", str(tmp_path / "missing.sqlite")]) == 2
    assert "No instance" in capsys.readouterr().err
    assert not os.path.exists(tmp_path / "missing.sqlite")
