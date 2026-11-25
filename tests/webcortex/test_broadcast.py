"""Selective broadcast (broadcast.py, ADR-053): one function decides which findings reach the other subsystems.
Off by default and then exactly as before; log adds a salience decision and changes nothing else; on broadcasts only
at or above the threshold and within the hourly limit, keeps the rest local with the reason, and records which open
question Presence's ranking puts first."""
import json

import pytest

from neuroscifi import broadcast
from neuroscifi.instance import store
from neuroscifi.presence import aggregator
from neuroscifi.webcortex import autonomy, orchestrator, scheduler, state
from test_orchestrator import T0, open_individual  # noqa: F401


@pytest.fixture
def individual(tmp_path):
    yield from open_individual(tmp_path)


def finished(db, settled, now=T0, objective="Zephyr compiler release blog"):
    """A finished autonomous task whose checkpoint settled these predictions ([(id, outcome)])."""
    task = orchestrator.submit(db, objective, now, mode="fact_verification", origin="curiosity")
    orchestrator.cancel(db, task["id"], now + 1)
    with store.write(db):
        db.execute("UPDATE web_tasks SET checkpoint=? WHERE id=?", (state.dumps({"predictions_settled": [
            {"prediction_id": pid, "hypothesis_id": None, "outcome": outcome} for pid, outcome in settled]}),
            task["id"]))
    return task


def decisions(db, task_id, key):
    return [e["detail"][key] for e in state.events(db, task_id) if e["kind"] == "decision" and key in e["detail"]]


def questions(db):
    return db.execute("SELECT count(*) FROM pending WHERE scope LIKE 'web:autonomy:%' AND resolved_at IS NULL"
                      ).fetchone()[0]


def test_off_by_default_is_exactly_as_before(individual):
    db, _ = individual
    assert store.config(db).broadcast == "off" and broadcast.mode(db) == "off"
    task = finished(db, [(7, "confirmed")])
    found = autonomy.after_task(db, task["id"], T0 + 5)
    assert found["pending_id"] is not None and questions(db) == 1
    assert decisions(db, task["id"], "broadcast") == []                       # nothing scored, nothing logged
    assert decisions(db, task["id"], "autonomy")[-1] == "notified"


def test_log_delivers_as_before_and_records_what_on_would_do(individual):
    db, _ = individual
    store.set_config(db, {"broadcast": "log"}, T0)
    task = finished(db, [(7, "confirmed")])            # one confirmed prediction: salience 1-exp(-0.25) = 0.221
    found = autonomy.after_task(db, task["id"], T0 + 5)
    assert found["pending_id"] is not None and questions(db) == 1
    [entry] = decisions(db, task["id"], "broadcast")
    assert entry["mode"] == "log" and entry["outcome"] == "delivered" and entry["would"] == "local"
    assert entry["salience"] == pytest.approx(0.2212, abs=1e-4) and "below threshold" in entry["reason"]
    assert entry["contention"]["competitors"] == 1 and entry["contention"]["this_one_won"] is True


def test_on_keeps_a_low_salience_finding_local_and_says_why(individual):
    db, _ = individual
    store.set_config(db, {"broadcast": "on"}, T0)
    task = finished(db, [(7, "confirmed")])
    found = autonomy.after_task(db, task["id"], T0 + 5)
    assert found["pending_id"] is None and questions(db) == 0
    [entry] = decisions(db, task["id"], "broadcast")
    assert entry["outcome"] == "local" and entry["channels"] == ["web_events"]
    assert decisions(db, task["id"], "autonomy")[-1].startswith("finding kept local (salience 0.221 below")


def test_on_broadcasts_a_salient_finding_and_names_the_winner_of_contention(individual):
    db, _ = individual
    store.set_config(db, {"broadcast": "on"}, T0)
    first = finished(db, [(1, "error"), (2, "error")])           # two wrong predictions: 1-exp(-1.2) = 0.699
    autonomy.after_task(db, first["id"], T0 + 5)
    second = finished(db, [(3, "error"), (4, "confirmed")], now=T0 + 10, objective="Zephyr boards news")
    found = autonomy.after_task(db, second["id"], T0 + 20)
    assert found["pending_id"] is not None and questions(db) == 2
    entry = decisions(db, second["id"], "broadcast")[0]
    assert entry["outcome"] == "broadcast" and entry["channels"] == ["pending", "web_events", "state_version"]
    assert entry["contention"]["competitors"] == 2 and entry["contention"]["winner"] == f"web:autonomy:{second['id']}"
    assert "Presence priority" in entry["contention"]["rule"]
    # what Presence shows first is the same question (one arbiter)
    rows = [{"rank": aggregator.RANK["user_critical"], "since": at, "scope": sc} for sc, at in db.execute(
        "SELECT scope, created_at FROM pending WHERE resolved_at IS NULL")]
    assert aggregator.arbitrate(rows)[0]["scope"] == entry["contention"]["winner"]


@pytest.mark.parametrize("carried", [0.9, 0.1, None])
def test_a_finding_takes_the_salience_record_its_candidate_carried(individual, carried):
    """Wave 1 seam: a task queued with lane I's salience record (ADR-051, salience.py) is offered with that score and
    its explanation; one without a record keeps broadcast.score's local function (0.221 for one confirmed)."""
    db, _ = individual
    store.set_config(db, {"broadcast": "on"}, T0)
    task = finished(db, [(7, "confirmed")])
    if carried is not None:
        record = {"score": carried, "signals": {"novelty": 1.0}, "weights": {"novelty": 0.15},
                  "explanation": f"salience {carried:.3f} = test"}
        with store.write(db):
            state.log_event(db, task["id"], "decision", {"autonomy": {"kind": "stale", "key": "zephyr",
                                                                      "salience": record}}, T0)
    found = autonomy.after_task(db, task["id"], T0 + 5)
    [entry] = decisions(db, task["id"], "broadcast")
    if carried is None:
        assert entry["salience"] == pytest.approx(0.2212, abs=1e-4) and entry["outcome"] == "local"
        assert "source" not in (entry["inputs"] or {})
        return
    assert entry["salience"] == carried and entry["inputs"]["source"] == autonomy.SALIENCE_SOURCE
    assert entry["inputs"]["explanation"] == record["explanation"]
    assert entry["outcome"] == ("broadcast" if carried >= 0.5 else "local")
    assert (found["pending_id"] is not None) == (carried >= 0.5)


def test_the_hourly_limit_keeps_the_rest_local(individual, monkeypatch):
    db, _ = individual
    store.set_config(db, {"broadcast": "on", "broadcast_threshold": 0.0}, T0)
    monkeypatch.setattr(broadcast, "MAX_PER_HOUR", 2)
    outcomes = []
    for i in range(3):
        task = finished(db, [(i, "confirmed")], now=T0 + i, objective=f"Zephyr release {i}")
        autonomy.after_task(db, task["id"], T0 + 10 + i)
        outcomes.append(decisions(db, task["id"], "broadcast")[0])
    assert [o["outcome"] for o in outcomes] == ["broadcast", "broadcast", "local"]
    assert outcomes[2]["reason"].startswith("rate limit: 2 broadcasts")
    assert [x["kind"] for x in broadcast.log(db)] == ["autonomy.finding"] * 3


def test_a_monitor_finding_goes_through_the_same_function(individual, monkeypatch):
    db, _ = individual
    store.set_config(db, {"broadcast": "log"}, T0)
    mon = scheduler.monitor_add(db, "Zephyr boards", T0, interval=3600, authorized_by="owner")
    task = orchestrator.submit(db, "Zephyr boards", T0, mode="continuous", origin=f"monitor:{mon['id']}")
    with store.write(db):
        state.update_task(db, task["id"], T0, status="completed")
        db.execute("UPDATE web_monitors SET baseline=? WHERE id=?",
                   (json.dumps({"claims": {"k": {"values": ["1"], "polarity": 1}}}), mon["id"]))
    real = scheduler.compare
    monkeypatch.setattr(scheduler, "compare", lambda *a: real(*a) | {
        "meaningful": True, "changed_values": [{"key": "k", "before": ["1"], "after": ["2"]}]})
    changes = scheduler.after_monitor_task(db, task["id"], T0 + 1)
    assert changes["pending_id"] is not None
    [entry] = decisions(db, task["id"], "broadcast")
    assert entry["kind"] == "monitor.finding" and entry["salience"] == pytest.approx(0.3935, abs=1e-4)


def test_salience_is_a_plain_function_of_the_counts_and_unknown_kinds_are_refused():
    low, _ = broadcast.score("autonomy.finding", {"new_verified": 1})
    high, inputs = broadcast.score("autonomy.finding", {"new_verified": 1, "resolved_contradictions": 2})
    assert 0 < low < high < 1 and inputs["counts"] == {"new_verified": 1, "resolved_contradictions": 2}
    capped, _ = broadcast.score("autonomy.finding", {"new_verified": 50})
    assert capped == broadcast.score("autonomy.finding", {"new_verified": broadcast.CAP})[0]
    with pytest.raises(KeyError):
        broadcast.score("internal.state", {})


def test_settings_are_checked_and_a_damaged_value_reads_as_off(individual):
    db, _ = individual
    from neuroscifi.instance.errors import ValidationError
    for bad in ({"broadcast": "loud"}, {"broadcast_threshold": 1.5}, {"broadcast_threshold": True}):
        with pytest.raises(ValidationError):
            store.set_config(db, bad, T0)
    with store.write(db):
        raw = json.loads(db.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0])
        raw["broadcast"] = "always"
        db.execute("UPDATE meta SET value=? WHERE key='config'", (json.dumps(raw),))
    assert store.config(db).broadcast == "off" and broadcast.mode(db) == "off"
    assert any("broadcast" in p for p in store.setting_problems(db))
