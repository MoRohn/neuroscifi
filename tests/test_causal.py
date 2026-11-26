"""The linear-Gaussian causal engine (ADR-052): Pearl's three rungs against closed forms worked by hand, and a decline
for what back-door adjustment cannot identify."""
import math

import pytest

from neuroscifi import causal


def chain(b=2.0):
    return {"order": ["X", "Y"], "edges": {"Y": {"X": b}}, "intercept": {"X": 0.5, "Y": 1.0}, "noise_sd": 1.0}


def test_rung_one_conditions_on_what_is_seen():
    out = causal.answer({"model": chain(), "given": {"X": 2.0}, "ask": "Y"})
    assert out["rung"] == "observational" and out["value"] == pytest.approx(5.0) and out["sd"] == pytest.approx(1.0)
    # Seeing the effect tells about the cause: E[X | Y = y] = 0.5 + 2 (y - 2) / 5.
    back = causal.answer({"model": chain(), "given": {"Y": 4.0}, "ask": "X"})
    assert back["value"] == pytest.approx(0.5 + 2 * (4.0 - 2.0) / 5) and back["sd"] == pytest.approx(math.sqrt(1 / 5))


def test_rung_two_cuts_the_causes_own_causes():
    fork = {"order": ["Z", "X", "Y"], "edges": {"X": {"Z": 1.0}, "Y": {"Z": 3.0}}, "intercept": {},
            "noise_sd": 1.0}
    seen = causal.answer({"model": fork, "given": {"X": 2.0}, "ask": "Y"})
    done = causal.answer({"model": fork, "do": {"X": 2.0}, "ask": "Y"})
    assert seen["value"] == pytest.approx(3.0) and done["value"] == pytest.approx(0.0)   # X does not cause Y
    assert done["sd"] == pytest.approx(math.sqrt(10.0)) and done["identified"]


def test_rung_three_abducts_then_acts_then_predicts():
    observed = {"X": 1.0, "Y": 4.5}                   # so this unit's noise of Y is 4.5 - 1 - 2 = 1.5
    out = causal.answer({"model": chain(), "observed": observed, "had": {"X": 3.0}, "ask": "Y"})
    assert out["rung"] == "counterfactual" and out["value"] == pytest.approx(1.0 + 6.0 + 1.5)
    assert out["sd"] < 1e-6
    # Only Y seen: E[noise_Y | y] = (y - 2) / 5 and its variance 4 / 5 (b = 2, the noise of X absorbs the rest).
    partial = causal.answer({"model": chain(), "observed": {"Y": 4.5}, "had": {"X": 3.0}, "ask": "Y"})
    assert partial["value"] == pytest.approx(7.0 + 2.5 / 5) and partial["sd"] == pytest.approx(math.sqrt(4 / 5))


def test_a_hidden_common_cause_of_the_cause_is_declined_not_guessed():
    out = causal.answer({"graph": {"X0": [], "X1": ["X0"]}, "hidden_common_cause": ["X0", "X1"],
                         "observational_covariance": {"X0": {"X0": 1, "X1": 1}, "X1": {"X0": 1, "X1": 2}},
                         "do": {"X0": 1.0}, "ask": "X1"})
    assert out["value"] is None and not out["identified"] and "back-door" in out["why"]
    unconfounded = causal.answer({"graph": {"X0": [], "X1": ["X0"]}, "hidden_common_cause": [],
                                  "observational_covariance": {"X0": {"X0": 2, "X1": 4}, "X1": {"X0": 4, "X1": 9}},
                                  "means": {"X0": 0.0, "X1": 1.0}, "do": {"X0": 1.5}, "ask": "X1"})
    assert unconfounded["value"] == pytest.approx(1.0 + 2.0 * 1.5)


def test_a_malformed_graph_question_is_refused_not_answered():
    """review1 CL10: "X0" as hidden_common_cause was the set {'X', '0'} (the confounded cause read as unconfounded,
    a confident wrong number); a node missing from the covariance raised KeyError."""
    base = {"graph": {"X0": [], "X1": ["X0"]}, "observational_covariance": {"X0": {"X0": 1, "X1": 1},
                                                                           "X1": {"X0": 1, "X1": 2}},
            "means": {"X0": 0.0, "X1": 0.0}, "do": {"X0": 1.0}, "ask": "X1"}
    with pytest.raises(ValueError, match="list of node names"):
        causal.answer(base | {"hidden_common_cause": "X0"})
    with pytest.raises(ValueError, match="not a node"):
        causal.answer(base | {"hidden_common_cause": ["W"]})
    with pytest.raises(ValueError, match="covariance and the means"):
        causal.answer(base | {"observational_covariance": {"X0": {"X0": 1}}})
    with pytest.raises(ValueError, match="not a node"):
        causal.answer(base | {"ask": "Z"})


def test_malformed_models_are_refused():
    for model, match in (({"order": ["X", "Y"], "edges": {"X": {"Y": 1.0}}}, "causal order"),
                         ({"order": ["X"], "noise_sd": 0}, "noise_sd"), ({"order": []}, "order")):
        with pytest.raises(ValueError, match=match):
            causal.answer({"model": model, "do": {"X": 1}, "ask": "X"})
    with pytest.raises(ValueError, match="not a node"):
        causal.answer({"model": chain(), "do": {"W": 1}, "ask": "Y"})
