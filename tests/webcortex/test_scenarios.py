"""Scenario records for predictions (ADR-052): three futures with stated probabilities that sum to 1, written only to
the imagined store and linked to the prediction; filled once with the observed outcome and the comparison when the
prediction settles; the per-mechanism prediction error; off by default, held by quiet, covered by forgetting."""
import json
import math

import pytest

from neuroscifi.instance import governance, imagined, store
from neuroscifi.webcortex import evolution, scenarios, scheduler
from test_consolidate import T0, consolidated, open_db, two_sources
from test_evolution import undated_latest


@pytest.fixture
def db(tmp_path):
    yield from open_db(tmp_path)


def _open_prediction(db):
    tid, out = consolidated(db, [undated_latest()])
    hid = out["held"][0]["hypothesis_id"]
    pid = db.execute("SELECT id FROM web_predictions WHERE hypothesis_id=?", (hid,)).fetchone()[0]
    return tid, hid, pid


def test_three_futures_whose_probabilities_sum_to_one_with_their_assumptions(db):
    _, hid, pid = _open_prediction(db)
    f = scenarios.futures(db, pid)
    probs = [f["futures"][k]["p"] for k in scenarios.FUTURES]
    assert sum(probs) == pytest.approx(1.0, abs=1e-12) and all(0 < p < 1 for p in probs)
    assert f["decisive"] == {"decisive": 0, "settled": 0, "rate": 0.5}       # Laplace with no history
    assert f["futures"]["confirmed"]["p"] == pytest.approx(0.5 * f["q"])
    assert any("does not depend on whether the statement holds" in a for a in f["assumptions"])
    assert ("prediction", pid) in f["derived_from"] and ("hypothesis", hid) in f["derived_from"]


def test_off_by_default_then_stored_only_in_the_imagined_store_and_linked_to_its_prediction(db):
    _, _, pid = _open_prediction(db)
    assert scenarios.tick(db, T0 + 10) is None
    with pytest.raises(Exception, match="imagine_scenarios is off"):
        scenarios.imagine(db, pid, T0 + 10)
    store.set_config(db, {"imagine_scenarios": True}, T0)
    before = {t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
              for t in ("web_predictions", "web_hypotheses", "web_knowledge", "web_claims", "evidence")}
    assert scenarios.tick(db, T0 + 10) == {"settled": 0, "imagined": 1}
    record_id = scenarios._existing(db, pid)
    shown = imagined.show(db, record_id)
    assert shown["kind"] == "simulation" and shown["label"] == imagined.NOT_EVIDENCE
    assert ["prediction", str(pid)] in shown["derived_from"]
    assert scenarios.imagine(db, pid, T0 + 11) == record_id                   # one record per prediction
    after = {t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in before}
    assert after == before


def test_settlement_fills_the_outcome_once_and_the_error_is_reported_per_mechanism(db):
    store.set_config(db, {"imagine_scenarios": True}, T0)
    _, _, pid = _open_prediction(db)
    scenarios.tick(db, T0 + 10)
    record_id = scenarios._existing(db, pid)
    payload = imagined.show(db, record_id)["payload"]
    consolidated(db, [two_sources(hosts=("c.example", "d.example"))], T0 + 20)     # confirms the prediction
    assert evolution.prediction(db, pid)["outcome"] == "confirmed"
    assert scenarios.tick(db, T0 + 30)["settled"] == 1
    shown = imagined.show(db, record_id)
    outcome = shown["outcome"]
    assert outcome["evidence"] == [["prediction", str(pid)]]
    # No text in an outcome (imagined.observe): the slot holds which future happened, the comparison is computed.
    assert outcome["observed"] == {"futures": scenarios.FUTURES.index("confirmed")}
    assert scenarios.observed_future(shown) == "confirmed"
    c = scenarios.compare(payload, scenarios.observed_future(shown))
    p = payload["futures"]
    assert c["brier_3way"] == pytest.approx((p["confirmed"]["p"] - 1) ** 2 + p["error"]["p"] ** 2
                                            + p["inconclusive"]["p"] ** 2)
    assert c["log_loss"] == pytest.approx(-math.log(p["confirmed"]["p"]))
    assert c["forecast_brier"] == pytest.approx((payload["forecast"]["used"] - 1) ** 2)
    assert scenarios.tick(db, T0 + 40)["settled"] == 0                               # once
    report = scenarios.errors(db)["mechanisms"]["scenarios.template v1"]
    assert report["n"] == 1 and report["decisive_observed"] == 1.0
    assert report["brier_3way"] == pytest.approx(c["brier_3way"])
    # The settled prediction now informs the next template: 1 decisive of 1 settled.
    assert scenarios.decisive_rate(db, "hypothesis") == {"decisive": 1, "settled": 1, "rate": 2 / 3}


def test_quiet_holds_the_timer_and_forgetting_the_task_takes_the_record(db):
    store.set_config(db, {"imagine_scenarios": True}, T0)
    tid, _, pid = _open_prediction(db)
    store.set_config(db, {"quiet": True}, T0)
    assert "held" in scenarios.tick(db, T0 + 10)
    store.set_config(db, {"quiet": False}, T0)
    scenarios.tick(db, T0 + 10)
    assert scenarios._existing(db, pid)
    effects = governance.forget_research(db, tid, T0 + 20)
    assert effects["effects"]["imagined_removed"] == 1
    assert scenarios._existing(db, pid) is None


def test_the_timer_reports_scenarios_only_when_they_are_on(db):
    from neuroscifi.webcortex import state
    state.set_config(db, {"autonomous": False}, T0)
    report = scheduler.tick(db, T0 + 10, services=None)
    assert "scenarios" not in report
    store.set_config(db, {"imagine_scenarios": True}, T0)
    _open_prediction(db)
    report = scheduler.tick(db, T0 + 20, services=None)
    assert report["scenarios"] == {"settled": 0, "imagined": 1}
    assert json.dumps(report)          # the report stays JSON


def test_two_concurrent_writers_make_one_record_per_prediction(db, monkeypatch):
    """review1 CL7: the 'already imagined?' check ran outside the write; two ticks could both write."""
    store.set_config(db, {"imagine_scenarios": True}, T0)
    _, _, pid = _open_prediction(db)
    other = store.connect(db.execute("PRAGMA database_list").fetchone()[2])
    real = scenarios.futures
    raced = []

    def futures(conn, prediction_id):
        if not raced:                       # the other process writes its record while this one computes
            raced.append(True)
            raced.append(scenarios.imagine(other, prediction_id, T0 + 10))
        return real(conn, prediction_id)
    monkeypatch.setattr(scenarios, "futures", futures)
    try:
        assert scenarios.imagine(db, pid, T0 + 10) == raced[1]
    finally:
        other.close()
    assert db.execute("SELECT count(*) FROM imagined").fetchone()[0] == 1


def test_at_the_store_limit_the_timer_makes_nothing_and_evicts_nothing(db):
    """review1 CL8: each pass evicted the oldest records (an owner's kept answer among them) to make room."""
    store.set_config(db, {"imagine_scenarios": True, "max_imagined_rows": 1}, T0)
    kept = imagined.record(db, **imagined.example(1), now=T0)
    _open_prediction(db)
    assert scenarios.tick(db, T0 + 10) == {"settled": 0, "imagined": 0}
    assert [i["id"] for i in imagined.listing(db, T0 + 11)["items"]] == [kept]
