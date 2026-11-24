"""Forecasts read through their own settled history (ADR-046): the mapping's arithmetic, what it reads and what it
leaves alone, forgetting and restore, the setting, and the simulation that chose its numbers.

Real SQLite instance files; predictions are made and settled through the product's own functions."""
import json
import math
from pathlib import Path
import random

import pytest

from neuroscifi.instance import checkpoints, governance, store
from neuroscifi.instance.errors import ValidationError
from neuroscifi.webcortex import autonomy, curiosity, evolution, predictions, state
from neuroscifi.webcortex import config as wconfig
from neuroscifi.webcortex.bench import calibration as sim
from test_autonomy import hypothesize
from test_consolidate import DAY, T0, consolidated, open_db, two_sources
from test_evolution import undated_latest

EVIDENCE = Path(__file__).resolve().parents[2] / "docs" / "quality" / "evidence" / "open_items_calibration.json"
GRID = [i / 20 for i in range(1, 20)]


@pytest.fixture
def db(tmp_path):
    yield from open_db(tmp_path)


def settle(db, prediction_id, outcome, now):
    with store.write(db):
        evolution._settle(db, evolution.prediction(db, prediction_id), outcome, now, task_id=None,
                          observation={"state": None, "at": now})
        store.bump_state_version(db)


def settled_hypothesis_forecasts(db, pairs, *, origin="gap", now=T0, key="orbix replica limit"):
    """A hypothesis of `origin` with one settled prediction per (forecast, outcome) pair. Returns its id."""
    with store.write(db):
        hid, _ = evolution.open_hypothesis(db, key=key, statement=f"A statement about {key}.", origin=origin,
                                           verification={"policy": "ADR-035", "state": "insufficient",
                                                         "confidence": 0.5}, claims=[], values=[], polarity=1,
                                           now=now, with_prediction=False)
        ids = [evolution.predict(db, hid, now + i, deadline=now + 20 * DAY, probability=p)
               for i, (p, _) in enumerate(pairs)]
        store.bump_state_version(db)
    for i, (pid, (_, outcome)) in enumerate(zip(ids, pairs)):
        settle(db, pid, outcome, now + 1000 + i)
    return hid


def settled_knowledge_forecasts(db, outcomes, *, now=T0):
    """A knowledge item with one settled 'still holds' prediction per outcome. Returns (task id, item id, p)."""
    tid, first = consolidated(db, [two_sources(published=T0 - 5 * DAY)])
    kid = first["provisional"][0]
    for i, outcome in enumerate(outcomes):
        pid = predictions.record(db, kid, "still holds at next refresh", now + DAY, now + i)["id"]
        settle(db, pid, outcome, now + 1000 + i)
    p = db.execute("SELECT confidence FROM web_knowledge WHERE id=?", (kid,)).fetchone()[0]
    return tid, kid, p


# ---- the arithmetic ----------------------------------------------------------------------------------------------

def test_nothing_settled_and_too_little_settled_leave_every_forecast_as_stated():
    for pairs in ([], [(0.6, 0)] * (evolution.RECALIBRATION_MINIMUM - 1)):
        fit = evolution.fit_recalibration(pairs)
        assert fit["applied"] is False and fit["shift"] == 0.0 and fit["tilt"] == 0.0
        assert fit["n"] == len(pairs) and fit["minimum"] == evolution.RECALIBRATION_MINIMUM
        for p in (0.0, 0.004, 0.37, 0.5, 0.999, 1.0):
            assert evolution.recalibrate(p, fit) == p               # the same number, not a clipped one
    assert evolution.recalibrate(None, evolution.fit_recalibration([])) is None
    assert evolution.recalibrate(0.3, None) == 0.3


def _one_value(p, failures, successes=0):
    """What one stated forecast is read as after `failures` and `successes` of it, solved here by bisection on the
    first-order condition of the penalised likelihood (one forecast value: the tilt has nothing to act on)."""
    z, lo, hi = math.log(p / (1 - p)), -20.0, 20.0
    for _ in range(200):
        a = (lo + hi) / 2
        s = 1 / (1 + math.exp(-(z + a)))
        slope = successes - (failures + successes) * s - evolution.RECALIBRATION_PRIOR_SHIFT * a
        lo, hi = (a, hi) if slope > 0 else (lo, a)
    return 1 / (1 + math.exp(-(z + (lo + hi) / 2)))


def test_at_the_minimum_the_mapping_acts_and_is_held_close_to_the_stated_forecast():
    """Twenty forecasts of 0.6 that all failed: the plain frequency is 0, the mapping reads 0.6 as 0.46."""
    few = evolution.fit_recalibration([(0.6, 0)] * 20)
    many = evolution.fit_recalibration([(0.6, 0)] * 100)
    assert few["applied"] and many["applied"] and few["tilt"] == 0.0
    assert evolution.recalibrate(0.6, few) == pytest.approx(_one_value(0.6, 20), abs=1e-5)
    assert evolution.recalibrate(0.6, few) == pytest.approx(0.458, abs=0.001)
    assert evolution.recalibrate(0.6, many) == pytest.approx(_one_value(0.6, 100), abs=1e-5)
    assert evolution.recalibrate(0.6, many) < evolution.recalibrate(0.6, few) < 0.6
    up = evolution.fit_recalibration([(0.6, 1)] * 20)
    assert evolution.recalibrate(0.6, up) == pytest.approx(_one_value(0.6, 0, 20), abs=1e-5)
    assert 0.6 < evolution.recalibrate(0.6, up) < 0.75


def test_the_fit_is_the_maximum_of_the_penalised_likelihood():
    """Checked against the objective itself, written out here: no nearby (shift, tilt) scores higher."""
    rng = random.Random(7)
    pairs = [(p, 1 if rng.random() < 0.25 + 0.3 * p else 0) for p in (rng.choice(GRID) for _ in range(90))]
    fit = evolution.fit_recalibration(pairs)
    centre = sum(math.log(p / (1 - p)) for p, _ in pairs) / len(pairs)
    assert fit["centre"] == pytest.approx(centre, abs=1e-6)

    def objective(a, c):
        total = -0.5 * evolution.RECALIBRATION_PRIOR_SHIFT * a * a - 0.5 * evolution.RECALIBRATION_PRIOR_TILT * c * c
        for p, y in pairs:
            z = math.log(p / (1 - p))
            s = 1 / (1 + math.exp(-(z + a + c * (z - centre))))
            total += math.log(s) if y else math.log(1 - s)
        return total

    best = objective(fit["shift"], fit["tilt"])
    for da in (-0.01, 0, 0.01):
        for dc in (-0.01, 0, 0.01):
            assert objective(fit["shift"] + da, fit["tilt"] + dc) <= best + 1e-9
    assert -1.0 < fit["tilt"] < 1.0 and fit["shift"] != 0.0


def test_a_forecaster_that_is_right_is_left_nearly_alone():
    pairs = [(p, 1 if k < round(p * 20) else 0) for p in GRID for k in range(20)]     # frequency equals forecast
    fit = evolution.fit_recalibration(pairs, window=0)
    assert abs(fit["shift"]) < 0.02 and abs(fit["tilt"]) < 0.02
    for p in GRID:
        assert evolution.recalibrate(p, fit) == pytest.approx(p, abs=0.01)


def test_the_mapping_never_reverses_an_order_and_stays_inside_its_bounds():
    rng = random.Random(11)
    for trial in range(200):
        rule = rng.choice(("backwards", "noise", "sure", "unsure", "low"))
        pairs = []
        for _ in range(rng.randrange(20, 160)):
            p = rng.choice(GRID + [0.0, 1.0, 0.003, 0.997])
            t = {"backwards": 1 - p, "noise": 0.5, "sure": p * p, "unsure": 0.5 + (p - 0.5) / 3, "low": 0.02}[rule]
            pairs.append((p, 1 if rng.random() < t else 0))
        fit = evolution.fit_recalibration(pairs)
        read = [evolution.recalibrate(p, fit) for p in [0.0, 0.001] + GRID + [0.999, 1.0]]
        assert all(b >= a for a, b in zip(read, read[1:])), (trial, rule, fit)
        assert all(evolution.MIN_FORECAST <= q <= evolution.MAX_FORECAST for q in read)
        assert evolution.RECALIBRATION_TILT[0] <= fit["tilt"] <= evolution.RECALIBRATION_TILT[1]


def test_a_forecaster_that_has_it_backwards_is_flattened_not_reversed():
    pairs = [(p, 0 if k < round(p * 20) else 1) for p in (0.1, 0.3, 0.5, 0.7, 0.9) for k in range(20)]
    random.Random(3).shuffle(pairs)                                     # true frequency 1 - p for every forecast
    fit = evolution.fit_recalibration(pairs)
    assert fit["tilt"] == evolution.RECALIBRATION_TILT[0]                          # slope 0: the lowest allowed
    assert {evolution.recalibrate(p, fit) for p in GRID} == {0.5}        # every forecast reads as the base rate


def test_only_the_most_recent_settlements_count():
    old = [(0.7, 0)] * 300
    recent = [(p, 1 if k < round(p * 20) else 0) for p in (0.3, 0.5, 0.7, 0.9, 0.5) for k in range(20)]
    assert len(recent) == evolution.RECALIBRATION_WINDOW
    fit = evolution.fit_recalibration(old + recent)
    assert fit["n"] == 400 and fit["used"] == 100
    assert fit == evolution.fit_recalibration(recent) | {"n": 400}
    assert evolution.recalibrate(0.7, fit) == pytest.approx(0.7, abs=0.03)
    assert evolution.recalibrate(0.7, evolution.fit_recalibration(old)) == pytest.approx(_one_value(0.7, 100), abs=1e-5)


def test_the_fit_is_deterministic_and_does_not_depend_on_the_order_inside_the_window():
    rng = random.Random(5)
    pairs = [(rng.choice(GRID), rng.randrange(2)) for _ in range(80)]
    shuffled = list(pairs)
    random.Random(9).shuffle(shuffled)
    assert evolution.fit_recalibration(pairs) == evolution.fit_recalibration(pairs)
    assert evolution.fit_recalibration(pairs) == evolution.fit_recalibration(shuffled)
    shift_only = evolution.fit_recalibration(pairs, prior_tilt=None)
    assert shift_only["tilt"] == 0.0 and shift_only["applied"]


def test_prequential_scores_each_forecast_before_its_outcome_is_used():
    pairs = [(0.6, 0)] * 40
    out = evolution.prequential(pairs)
    assert out["n"] == 40 and out["adjusted"] == 40 - evolution.RECALIBRATION_MINIMUM
    assert out["brier_stated"] == pytest.approx(0.36) and out["brier_mapped"] < out["brier_stated"]
    assert evolution.prequential(pairs[:20]) == {"n": 20, "adjusted": 0, "brier_stated": 0.36, "brier_mapped": 0.36}
    assert evolution.prequential([]) == {"n": 0, "adjusted": 0, "brier_stated": None, "brier_mapped": None}


# ---- what it reads from the individual ---------------------------------------------------------------------------

def test_a_new_individual_reads_every_forecast_as_stated(db):
    assert evolution.settled_forecasts(db) == {"knowledge": [], "hypothesis": [], "owner": []}
    fits = evolution.recalibrations(db)
    assert set(fits) == set(evolution.RECALIBRATION_KINDS)
    assert all(f["applied"] is False and f["n"] == 0 for f in fits.values())
    report = evolution.recalibration_report(db)
    assert report["enabled"] is True and report["kinds"]["hypothesis"]["had_it_been_used"] is None
    assert report["kinds"]["knowledge"]["reads"] == {"0.1": 0.1, "0.3": 0.3, "0.5": 0.5, "0.7": 0.7, "0.9": 0.9}


def test_each_kind_of_forecaster_has_its_own_mapping(db):
    """Derived hypotheses that keep failing change how a derived hypothesis' forecast is read, and nothing else:
    not a knowledge item's 'still holds', not the owner's own estimate."""
    settled_hypothesis_forecasts(db, [(0.6, "error")] * 24 + [(0.6, "inconclusive")] * 5)
    _, _, p = settled_knowledge_forecasts(db, ["confirmed"] * 3)
    _, owner_pid = hypothesize(db, "The Quartz editor licence changed to MIT in 2025", 0.4)
    settle(db, owner_pid, "confirmed", T0 + 5000)
    settled = evolution.settled_forecasts(db)
    assert settled["hypothesis"] == [(0.6, 0.0)] * 24                   # inconclusive: nothing was observed
    assert settled["knowledge"] == [(pytest.approx(p), 1.0)] * 3 and settled["owner"] == [(0.4, 1.0)]
    fits = evolution.recalibrations(db)
    assert fits["hypothesis"]["applied"]
    assert evolution.recalibrate(0.6, fits["hypothesis"]) == pytest.approx(_one_value(0.6, 24), abs=1e-5)
    assert not fits["knowledge"]["applied"] and not fits["owner"]["applied"]
    assert evolution.prediction_kind(db, owner_pid) == "owner" and evolution.prediction_kind(db, 10**9) is None
    kinds = {evolution.prediction_kind(db, pid) for (pid,) in db.execute("SELECT id FROM web_predictions")}
    assert kinds == {"knowledge", "hypothesis", "owner"}
    # the scoring of the stated forecasts is what it was: same keys, every settled forecast, stated values
    cal = evolution.calibration(db)
    assert set(cal) == {"n", "inconclusive", "brier", "base_rate", "brier_reference", "skill", "ece", "bins"}
    assert cal["n"] == 28 and cal["inconclusive"] == 5


def test_stored_forecasts_and_knowledge_are_never_rewritten(db):
    hid = settled_hypothesis_forecasts(db, [(0.6, "error")] * 30)
    _, kid, _ = settled_knowledge_forecasts(db, ["confirmed"] * 25)
    before = {t: db.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
              for t in ("web_predictions", "web_knowledge", "web_hypotheses", "web_metrics", "web_claims")}
    version = store.state_version(db)
    assert all(f["applied"] for k, f in evolution.recalibrations(db).items() if k != "owner")
    evolution.recalibration_report(db)
    autonomy.candidates(db, T0 + 6000)
    after = {t: db.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in before}
    assert after == before and store.state_version(db) == version
    with store.write(db):                      # a new forecast is stored as stated, whatever the mapping says
        pid = evolution.predict(db, hid, T0 + 7000, deadline=T0 + 20 * DAY, probability=0.6)
        store.bump_state_version(db)
    assert evolution.prediction(db, pid)["probability"] == 0.6
    item = db.execute("SELECT confidence FROM web_knowledge WHERE id=?", (kid,)).fetchone()[0]
    new = predictions.record(db, kid, "still holds at next refresh", T0 + 9 * DAY, T0 + 8000)
    assert new["probability"] == pytest.approx(item)


# ---- the one reader: the order of autonomous research ------------------------------------------------------------

def test_autonomous_research_orders_by_the_forecast_as_read(db):
    """25 of the owner's own forecasts of 0.5 failed. An open one is then read as less than even: less uncertain,
    less to gain. Its stored forecast and the forecast shown stay 0.5."""
    statement = "The Zephyr compiler version 4.2.0 was released on 17 November 2026"
    for n in range(25):
        _, pid = hypothesize(db, f"The Orchid database supports {n + 2} replicas in its 3.1 version", 0.5, T0 + n)
        settle(db, pid, "error", T0 + 100 + n)
    _, open_pid = hypothesize(db, statement, 0.5, T0 + 200)
    fit = evolution.recalibrations(db)["owner"]
    q = evolution.recalibrate(0.5, fit)
    assert fit["applied"] and q == pytest.approx(_one_value(0.5, 25), abs=1e-5) and 0.3 < q < 0.4
    found = next(c for c in autonomy.candidates(db, T0 + 300)["chosen"] if c["statement"] == statement)
    assert found["forecast"] == 0.5 and found["forecast_as_read"] == q
    assert found["signals"]["prediction_uncertainty"] == round(curiosity.entropy(q), 6) < 1.0
    assert found["info_gain"] == round(curiosity.info_gain(q), 6) < round(curiosity.info_gain(0.5), 6)
    assert evolution.prediction(db, open_pid)["probability"] == 0.5

    state.set_config(db, {"calibrated_forecasts": False}, T0 + 400)
    off = next(c for c in autonomy.candidates(db, T0 + 500)["chosen"] if c["statement"] == statement)
    assert off["forecast"] == 0.5 and "forecast_as_read" not in off
    assert off["signals"]["prediction_uncertainty"] == 1.0
    assert off["info_gain"] == round(curiosity.info_gain(0.5), 6)
    assert all(f.get("off") and not f["applied"] for f in evolution.recalibrations(db).values())
    assert evolution.recalibration_report(db)["enabled"] is False


def test_a_due_knowledge_prediction_is_weighed_by_its_forecast_as_read(db):
    """An item whose 'still holds' forecasts kept failing: its next due prediction is read as less likely to hold
    than the item's confidence says."""
    _, kid, p = settled_knowledge_forecasts(db, ["error"] * 30)
    due = predictions.record(db, kid, "still holds at next refresh", T0 + DAY, T0 + 2000)
    q = evolution.recalibrate(p, evolution.recalibrations(db)["knowledge"])
    assert q < p
    ranked = autonomy.candidates(db, T0 + 3 * DAY)
    found = next(c for c in ranked["chosen"] + ranked["refused"] if c.get("prediction_id") == due["id"])
    assert found["kind"] == "prediction" and found["forecast"] == pytest.approx(p) and found["forecast_as_read"] == q
    assert found["signals"]["prediction_uncertainty"] == round(curiosity.entropy(q), 6)
    state.set_config(db, {"calibrated_forecasts": False}, T0 + 3 * DAY)
    ranked = autonomy.candidates(db, T0 + 3 * DAY)
    off = next(c for c in ranked["chosen"] + ranked["refused"] if c.get("prediction_id") == due["id"])
    assert "forecast_as_read" not in off and "forecast" not in off
    assert off["signals"]["prediction_uncertainty"] == round(curiosity.entropy(p), 6)


def test_the_decision_to_research_records_the_stated_forecast_and_how_it_was_read(db):
    statement = "The Zephyr compiler version 4.2.0 was released on 17 November 2026"
    for n in range(25):
        _, pid = hypothesize(db, f"The Orchid database supports {n + 2} replicas in its 3.1 version", 0.5, T0 + n)
        settle(db, pid, "confirmed", T0 + 100 + n)
    hypothesize(db, statement, 0.5, T0 + 200)
    q = evolution.recalibrate(0.5, evolution.recalibrations(db)["owner"])
    assert q == pytest.approx(_one_value(0.5, 0, 25), abs=1e-5) and q > 0.5
    autonomy.enable(db, T0 + 300)
    out = autonomy.idle(db, T0 + 300)
    assert out["action"] == "queued" and out["candidate"]["forecast"] == 0.5
    assert out["candidate"]["forecast_as_read"] == q
    logged = [json.loads(d) for (d,) in db.execute("SELECT detail FROM web_events WHERE kind='decision' AND "
                                                    "task_id=?", (out["task_id"],))]
    assert any((d.get("autonomy") or {}).get("forecast_as_read") == q for d in logged if isinstance(d, dict))


def test_below_the_minimum_autonomous_research_is_ordered_exactly_as_before(db):
    for n in range(evolution.RECALIBRATION_MINIMUM - 1):
        _, pid = hypothesize(db, f"The Orchid database supports {n + 2} replicas in its 3.1 version", 0.5, T0 + n)
        settle(db, pid, "error", T0 + 100 + n)
    hypothesize(db, "The Zephyr compiler version 4.2.0 was released on 17 November 2026", 0.5, T0 + 200)
    on = autonomy.candidates(db, T0 + 300)
    state.set_config(db, {"calibrated_forecasts": False}, T0 + 400)
    assert autonomy.candidates(db, T0 + 300) == on
    assert not any("forecast_as_read" in c for c in on["chosen"] + on["refused"])


def test_the_setting_is_a_boolean_and_an_older_individual_has_it_on(db):
    assert state.get_config(db).calibrated_forecasts is True
    with pytest.raises(ValidationError):
        state.set_config(db, {"calibrated_forecasts": "no"}, T0)
    with store.write(db):                          # settings saved before the field existed
        saved = json.loads(db.execute("SELECT value FROM meta WHERE key=?", (wconfig.META_KEY,)).fetchone()[0])
        saved.pop("calibrated_forecasts")
        db.execute("UPDATE meta SET value=? WHERE key=?", (json.dumps(saved), wconfig.META_KEY))
    assert state.get_config(db).calibrated_forecasts is True


# ---- forgetting and restore ----------------------------------------------------------------------------------------

def test_forgetting_a_knowledge_item_takes_its_settled_forecasts_out_at_once(db):
    _, kid, p = settled_knowledge_forecasts(db, ["error"] * 30)
    assert evolution.recalibrate(p, evolution.recalibrations(db)["knowledge"]) < p
    governance.forget_knowledge(db, kid, T0 + 9000)
    fit = evolution.recalibrations(db)["knowledge"]
    assert fit["applied"] is False and fit["n"] == 0 and evolution.recalibrate(p, fit) == p


def test_forgetting_the_task_behind_a_hypothesis_takes_its_settled_forecasts_out(db):
    tid, out = consolidated(db, [undated_latest()])
    hid = out["held"][0]["hypothesis_id"]
    with store.write(db):
        ids = [evolution.predict(db, hid, T0 + 10 + i, deadline=T0 + 20 * DAY, probability=0.6) for i in range(30)]
        store.bump_state_version(db)
    for i, pid in enumerate(ids):
        settle(db, pid, "error", T0 + 100 + i)
    assert evolution.recalibrations(db)["hypothesis"]["applied"]
    governance.forget_research(db, tid, T0 + 9000)
    fit = evolution.recalibrations(db)["hypothesis"]
    assert fit["applied"] is False and fit["n"] == 0


def test_a_restored_copy_reads_forecasts_from_the_rows_it_holds(db, tmp_path):
    _, kid, p = settled_knowledge_forecasts(db, ["error"] * 30)
    settled_hypothesis_forecasts(db, [(0.6, "error")] * 30)
    live = evolution.recalibrations(db)
    path = Path(db.execute("PRAGMA database_list").fetchone()[2])
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 9000)
    checkpoints.restore(db, cp["id"], tmp_path / "same.sqlite", T0 + 9001)
    copy = store.open_existing(tmp_path / "same.sqlite")
    try:
        assert evolution.recalibrations(copy) == live               # nothing but the rows decides it
    finally:
        copy.close()
    governance.forget_knowledge(db, kid, T0 + 9002)
    checkpoints.restore(db, cp["id"], tmp_path / "after.sqlite", T0 + 9003)
    copy = store.open_existing(tmp_path / "after.sqlite")
    try:
        fits = evolution.recalibrations(copy)
        assert fits["knowledge"]["applied"] is False and fits["knowledge"]["n"] == 0     # the forget was replayed
        assert fits["hypothesis"] == live["hypothesis"]
    finally:
        copy.close()


# ---- the command and the simulation ----------------------------------------------------------------------------------

def test_nsf_knowledge_calibration_shows_the_mapping_and_how_it_would_have_fared(db, capsys):
    from neuroscifi.cli import main
    settled_hypothesis_forecasts(db, [(0.6, "error")] * 30)
    path = str(Path(db.execute("PRAGMA database_list").fetchone()[2]))
    capsys.readouterr()
    assert main(["knowledge", "calibration", "--db", path]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["current"]["n"] == 30 and "history" in out
    kind = out["recalibration"]["kinds"]["hypothesis"]
    assert kind["settled"] == 30 and kind["mapping"]["applied"] is True
    assert kind["reads"]["0.5"] < 0.5 and kind["reads"]["0.1"] < kind["reads"]["0.9"]
    assert kind["had_it_been_used"]["adjusted"] == 10
    assert kind["had_it_been_used"]["brier_mapped"] < kind["had_it_been_used"]["brier_stated"] == 0.36
    assert "calibrated_forecasts=false" in out["recalibration"]["off"]


def test_the_simulation_is_deterministic_and_the_recorded_evidence_is_what_it_gives():
    assert sim.CHOSEN == {"minimum": evolution.RECALIBRATION_MINIMUM, "window": evolution.RECALIBRATION_WINDOW,
                          "prior_shift": evolution.RECALIBRATION_PRIOR_SHIFT,
                          "prior_tilt": evolution.RECALIBRATION_PRIOR_TILT}
    held_out = sim.evaluate("held_out", sim.CHOSEN)
    assert held_out == sim.evaluate("held_out", sim.CHOSEN)
    recorded = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert recorded["options"] == sim.CHOSEN
    assert recorded["held_out"]["streams"] == held_out
    assert recorded["held_out"]["summary"] == sim.summary(held_out)
    summary = recorded["held_out"]["summary"]
    assert summary["mean"]["mapped"]["distance"] < 0.5 * summary["mean"]["stated"]["distance"]
    assert summary["mean"]["mapped"]["brier"] < summary["mean"]["stated"]["brier"]
    assert summary["worst_change"]["distance"]["change"] <= 0.002          # the rule the settings were chosen by
    # few samples: below the minimum the first 25 predictions of every stream score exactly as stated
    assert all(v["25"]["mapped"] == v["25"]["stated"] for v in held_out.values())
