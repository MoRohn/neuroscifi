"""Forecasts from an active graph (ADR-052): several steps ahead against the world's known answer, the stated horizon
limit, refusal when errors have no bound, and pushes answered only where the recorded inputs identify them."""
from contextlib import closing

import numpy as np
import pytest

from neuroscifi.instance import datasets, forecast, graphs, imagine, imagined, recall, store
from neuroscifi.lab import graph_bench

T0 = 1_800_000_000.0


def _learned(db, context, ids):
    revision = graphs.learn(db, context, T0 + 2, train=ids["train"], validation=ids["validation"], holder="test")
    graphs.promote(db, revision, T0 + 3)
    return revision


@pytest.fixture
def world(tmp_path):
    db = store.create(tmp_path / "f.sqlite", T0)
    with closing(db):
        ids = datasets.simulate(db, "w", T0 + 1, seed=7)
        _learned(db, "w", ids)
        a = graph_bench.world(np.random.default_rng(7))[0]
        yield db, ids, a


def _own(db, context, states, targets):
    """A context learned from trajectories the test wrote (train, validation, heldout)."""
    ids = {}
    for split, (s, t) in zip(datasets.SPLITS, (( states, targets), (states[:256], targets[:256]),
                                               (states[256:768], targets[256:768]))):
        ids[split] = datasets.add(db, context, split, s, t, "simulated", T0)
    _learned(db, context, ids)
    return ids


def test_rollouts_match_the_worlds_known_answer_within_their_stated_spread(world):
    db, ids, a = world
    step = 0.2 * np.eye(a.shape[0]) + a
    states, targets = datasets.load(db, ids["heldout"])
    for row, k in ((3, 2), (40, 3), (90, 5)):
        x = states[row]
        truth = a @ np.linalg.matrix_power(step, k - 1) @ x
        out = forecast.forecast(db, "w", x.tolist(), k)
        error = np.abs(np.array(out["mean"]) - truth)
        assert np.all(error <= 2 * np.array(out["sd"])), k
        assert np.mean(error) / np.mean(targets.std(axis=0)) < 0.1
        assert out["label"] == imagined.NOT_EVIDENCE and out["kind"] == "simulation"
    spreads = [forecast.forecast(db, "w", states[3].tolist(), k, population=0)["sd"] for k in (1, 2, 5)]
    assert spreads == sorted(spreads)                 # uncertainty grows with the horizon
    fitted = forecast.model(db, "w")
    assert fitted["c"] == pytest.approx(np.full(a.shape[0], 0.2), abs=0.15)
    assert fitted["g"] == pytest.approx(np.ones(a.shape[0]), abs=0.15)


def test_horizon_one_is_the_graphs_own_next_step_prediction(world):
    db, ids, _ = world
    states, _ = datasets.load(db, ids["heldout"])
    revision = graphs._active(db, "w")
    matrix = graphs._matrix(db, revision).astype(np.float64)
    out = forecast.forecast(db, "w", states[10].tolist(), 1)
    assert out["mean"] == pytest.approx((states[10] @ matrix.T).tolist(), abs=1e-12)
    assert recall.run(db, ids["heldout"], T0 + 9, record=False)["verdict"] == "known:w"


def test_a_push_is_answered_where_the_recorded_inputs_identify_it(world):
    db, ids, a = world
    step = 0.2 * np.eye(a.shape[0]) + a
    x = datasets.load(db, ids["heldout"])[0][5]
    out = forecast.forecast(db, "w", x.tolist(), 3, population=4, action={"population": 2, "push": 0.6})
    push = np.zeros(a.shape[0])
    push[2] = 0.6
    truth = (a @ np.linalg.matrix_power(step, 2) @ x + a @ step @ push)[4]
    assert out["action_support"] == {"supported": True, "reasons": []}
    assert abs(out["mean"] - truth) <= 2 * out["sd"]
    plain = forecast.forecast(db, "w", x.tolist(), 3, population=4)["mean"]
    assert out["mean"] - plain == pytest.approx((a @ step @ push)[4], abs=0.02)


def test_a_push_is_declined_without_inputs_or_with_feedback(tmp_path):
    db = store.create(tmp_path / "g.sqlite", T0)
    rng = np.random.default_rng(3)
    a = graph_bench.world(rng)[0]
    n, steps = a.shape[0], 1536
    with closing(db):
        # No input: the target IS the next state (x' = A x + noise); nothing moves a population on its own.
        x = np.zeros((steps + 1, n))
        x[0] = rng.normal(0, 1, n)
        for t in range(steps):
            x[t + 1] = 2.5 * a @ x[t] + rng.normal(0, 0.3, n)
        _own(db, "closed", x[:-1], x[1:])
        # Feedback: the input to each population follows the state of another one.
        s = np.zeros((steps, n))
        y = np.zeros((steps, n))
        prev = np.zeros(n)
        for t in range(steps):
            y[t] = a @ prev + rng.normal(0, 0.065, n)
            s[t] = prev
            prev = 0.2 * prev + y[t] + 0.3 * np.roll(prev, 1) + rng.normal(0, 0.05, n)
        _own(db, "feedback", s, y)
        for context, reason in (("closed", "no input that moves"), ("feedback", "follow the past")):
            with pytest.raises(imagine.Refused, match=reason):
                forecast.forecast(db, context, [0.1] * n, 3, action={"population": 1, "push": 0.5})
            forecast.forecast(db, context, [0.1] * n, 2)        # a forecast without a push is still answered


def test_the_stated_horizon_limit_and_unbounded_errors_are_refused(world, tmp_path):
    db, _, a = world
    with pytest.raises(imagine.Refused, match="from 1 to 12"):
        forecast.forecast(db, "w", [0.0] * a.shape[0], forecast.MAX_HORIZON + 1)
    grow = store.create(tmp_path / "h.sqlite", T0)
    rng = np.random.default_rng(4)
    with closing(grow):
        n, steps = 3, 900
        x = np.zeros((steps + 1, n))
        x[0] = 0.01
        for t in range(steps):
            x[t + 1] = 1.008 * x[t] + rng.normal(0, 0.01, n)     # a carry-over that grows
        _own(grow, "grow", x[:-1], np.zeros((steps, n)) + rng.normal(0, 0.001, (steps, n)))
        with pytest.raises(imagine.Refused, match="no bound"):
            forecast.forecast(grow, "grow", [0.1] * n, 2)
        assert forecast.forecast(grow, "grow", [0.1] * n, 1)["horizon"] == 1


def test_a_trajectory_too_short_to_learn_a_carry_over_is_refused(tmp_path):
    """review1 CL11: the carry-over (2 numbers per population and an n x n covariance) was fitted from any rows."""
    db = store.create(tmp_path / "s.sqlite", T0)
    rng = np.random.default_rng(5)
    n = 4
    with closing(db):
        ids = {}
        for split in datasets.SPLITS:
            s = rng.normal(0, 1, (2 * n, n))
            ids[split] = datasets.add(db, "short", split, s, s @ (0.3 * np.eye(n)) + rng.normal(0, 0.1, (2 * n, n)),
                                      "simulated", T0)
        revision = graphs.learn(db, "short", T0 + 2, train=ids["train"], validation=ids["validation"], holder="t")
        with store.write(db):      # the gate would not promote so little data; the forecast must not rely on that
            db.execute("UPDATE graph_revisions SET status='active' WHERE id=?", (revision,))
        with pytest.raises(imagine.Refused, match="too short"):
            forecast.forecast(db, "short", [0.1] * n, 2)


def test_forecasts_are_read_only_and_behind_their_switch(world):
    db, ids, a = world
    version = store.state_version(db)
    forecast.forecast(db, "w", [0.0] * a.shape[0], 4, action={"population": 0, "push": 1.0})
    assert store.state_version(db) == version
    store.set_config(db, {"imagine_forecast": False}, T0)
    with pytest.raises(imagine.Refused, match="imagine_forecast is off"):
        forecast.forecast(db, "w", [0.0] * a.shape[0], 2)
    store.set_config(db, {"imagine_forecast": True}, T0)
    with pytest.raises(Exception, match="No active graph"):
        forecast.forecast(db, "nowhere", [0.0], 2)
    with pytest.raises(Exception, match="finite numbers"):
        forecast.forecast(db, "w", [0.0, 1.0], 2)
