"""Front-door adjustment in the linear causal engine (ADR-059, Wave 3 lane C): a closed form worked by hand, a decline
with its reason for every way the criterion fails, the switch that restores the old declines, and property tests
against the structural equations (a wrong answer is counted, never tolerated)."""
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys

import numpy as np
import pytest

import neuroscifi
from neuroscifi import causal
from neuroscifi.acceptance.cognition import adversarial, counterfactual, frontdoor_splits, imagination, splits

ROOT = Path(__file__).resolve().parents[1]


def worked():
    """U ~ N(0, 1); X = 0.5 + U + e; M = 1 + 2 X + e; Y = -1 + 3 M + U + e (all noises N(0, 1)); U hidden.
    Var X 2, Cov(M, X) 4, Var M 9, Cov(Y, X) 3*4 + 1 = 13, Cov(Y, M) 3*9 + 2 = 29, Var Y 81 + 1 + 12 + 1 = 95; means
    X 0.5, M 2, Y 5. E[Y | do(X = x)] = -1 + 3 (1 + 2 x) = 2 + 6 x."""
    cov = {"X": {"X": 2, "M": 4, "Y": 13}, "M": {"X": 4, "M": 9, "Y": 29}, "Y": {"X": 13, "M": 29, "Y": 95}}
    return {"graph": {"X": [], "M": ["X"], "Y": ["M"]}, "hidden_common_cause": ["X", "Y"],
            "observational_mean": {"X": 0.5, "M": 2.0, "Y": 5.0}, "observational_covariance": cov,
            "do": {"X": 1.5}, "ask": "Y"}


def test_front_door_matches_the_hand_worked_closed_form_and_says_so():
    out = causal.answer(worked())
    # b(M ~ X) = 4 / 2 = 2; b(Y ~ M | X) = 3 (solve [[9, 4], [4, 2]] b = [29, 13]): 5 + 3 * 2 * (1.5 - 0.5) = 11
    assert out["value"] == pytest.approx(2 + 6 * 1.5) and out["sd"] is None
    assert out["identified"] and out["identified_by"] == "front_door" and out["mediators"] == ["M"]
    assert out["label"] == "imagined, not evidence" and out["kind"] == "simulation"
    assert any("front-door" in a for a in out["assumptions"]) and "point value" in out["uncertainty"]
    # the confounded regression slope 13 / 2 would have said 5 + 6.5 = 11.5: the front door is not that
    assert abs(out["value"] - 11.5) > 0.4
    assert causal.answer(worked() | {"means": worked()["observational_mean"]})["value"] == pytest.approx(11.0)


def test_the_switch_off_declines_as_before_and_says_why():
    out = causal.answer(worked(), front_door=False)
    assert out["value"] is None and not out["identified"] and out["identified_by"] is None
    assert "back-door" in out["why"] and "switched off" in out["why"]


def test_other_answers_say_which_criterion_identified_them():
    model = {"order": ["X", "Y"], "edges": {"Y": {"X": 2.0}}, "intercept": {}, "noise_sd": 1.0}
    assert causal.answer({"model": model, "do": {"X": 1.0}, "ask": "Y"})["identified_by"] == "declared_model"
    back = causal.answer({"graph": {"X0": [], "X1": ["X0"]}, "hidden_common_cause": [],
                          "observational_covariance": {"X0": {"X0": 2, "X1": 4}, "X1": {"X0": 4, "X1": 9}},
                          "means": {"X0": 0.0, "X1": 1.0}, "do": {"X0": 1.5}, "ask": "X1"})
    assert back["identified_by"] == "back_door" and back["value"] == pytest.approx(4.0)


def _with(**change):
    q = json.loads(json.dumps(worked()))
    for k, v in change.items():
        q[k] = v
    return q


@pytest.mark.parametrize("question, reason", [
    (_with(graph={"X": [], "M": ["X"], "Y": ["M", "X"]}), "bypasses every mediator"),
    (_with(hidden_common_cause=["X", "M", "Y"]), "unblocked back-door path to the mediator"),
    (_with(hidden_common_cause=["X", "M"]), "unblocked back-door path to the mediator"),
    (_with(graph={"X": [], "M": ["X", "W"], "Y": ["M", "W"], "W": []}), "not blocked by X"),
    (_with(graph={"X": ["A"], "M": ["X", "A"], "Y": ["M"], "A": []}), "unblocked back-door path to the mediator"),
    (_with(observational_covariance={"X": {"X": 2, "Y": 13}, "Y": {"X": 13, "Y": 95}},
           observational_mean={"X": 0.5, "Y": 5.0}), "not observed"),
    (_with(graph={"X": [], "M": ["X"], "Y": []}), "no directed path"),
    (_with(graph={"X": ["Y"], "M": ["X"], "Y": ["M"]}), "cycle"),
    (_with(ask="X", hidden_common_cause=["X"]), "same node"),
    (_with(observational_covariance={"X": {"X": 2, "M": 4, "Y": 13}, "M": {"X": 4, "M": 8, "Y": 26},
                                     "Y": {"X": 13, "M": 26, "Y": 95}}), "not positive definite"),
    (_with(graph={f"N{i}": [] for i in range(70)} | {"X": [], "M": ["X"], "Y": ["M"]}), "more than 64"),
])
def test_where_the_criterion_fails_the_question_is_declined_with_the_reason(question, reason):
    out = causal.answer(question)
    assert out["value"] is None and not out["identified"] and reason in out["why"], out["why"]
    assert "front-door" in out["why"]


def test_malformed_front_door_questions_are_refused_not_answered():
    with pytest.raises(ValueError, match="differ"):
        causal.answer(_with(means={"X": 0.0, "M": 2.0, "Y": 5.0}))
    with pytest.raises(ValueError, match="symmetric"):
        causal.answer(_with(observational_covariance={"X": {"X": 2, "M": 4, "Y": 13}, "M": {"X": 3, "M": 9, "Y": 29},
                                                      "Y": {"X": 13, "M": 29, "Y": 95}}))
    with pytest.raises(ValueError, match="covariance and the means"):
        causal.answer(_with(observational_covariance={"X": {"X": 2, "M": 4}, "M": {"X": 4, "M": 9, "Y": 29},
                                                      "Y": {"X": 13, "M": 29, "Y": 95}}))
    with pytest.raises(ValueError, match="covariance and the means"):
        causal.answer(_with(observational_mean={"X": 0.5, "M": 2.0}))


def test_d_separation_on_the_textbook_shapes():
    chain = {"A": [], "B": ["A"], "C": ["B"]}
    collider = {"A": [], "C": [], "B": ["A", "C"], "D": ["B"]}
    assert not causal._d_separated(chain, {"A"}, {"C"}, set()) and causal._d_separated(chain, {"A"}, {"C"}, {"B"})
    assert causal._d_separated(collider, {"A"}, {"C"}, set())
    assert not causal._d_separated(collider, {"A"}, {"C"}, {"B"})
    assert not causal._d_separated(collider, {"A"}, {"C"}, {"D"})       # a descendant of the collider opens it


# ---- property tests against the structural equations ----------------------------------------------------------------

def test_the_property_worlds_are_frozen():
    """The worlds file was committed alone, before the mechanism (commit fb46a23); its digest is pinned here."""
    text = (ROOT / "src/neuroscifi/acceptance/cognition/frontdoor_splits.py").read_bytes()
    assert hashlib.sha256(text).hexdigest() == FROZEN_DIGEST
    dev, held = (set(frontdoor_splits.FRONT_DOOR[s]["seeds"]) for s in ("development", "held_out"))
    assert not dev & held and not (dev | held) & set(adversarial.FRONT_DOOR_SEEDS)
    used = {s for family in (splits.ANTICIPATION, splits.INFO_VALUE, splits.COUNTERFACTUAL, splits.SELF_MODEL)
            for split in family.values() for s in split.get("seeds", ())}
    assert not (dev | held) & used


FROZEN_DIGEST = "4e7e27933eff6f33f59d263784893f10eb5ace41d87ea96664ec95892f9322fa"


@pytest.mark.parametrize("split", ["development", "held_out"])
def test_exact_moments_every_answer_world_exact_and_no_false_answer(split):
    spec = frontdoor_splits.FRONT_DOOR[split]
    false_answers, exact = 0, 0
    for kind in spec["kinds"]:
        for seed in spec["seeds"]:
            world = frontdoor_splits.generate(kind, seed, split)
            out = causal.answer(world["query"])
            if world["expect"] == "decline":
                false_answers += out["value"] is not None
                continue
            assert out["identified_by"] == "front_door"
            error = abs(out["value"] - world["truth"]) / world["scale"]
            false_answers += error > frontdoor_splits.TOLERANCE
            exact += error <= frontdoor_splits.EXACT
    assert false_answers == 0
    assert exact == len(frontdoor_splits.ANSWER_KINDS) * len(spec["seeds"])


def test_sampled_moments_never_turn_a_trap_into_an_answer():
    """The criterion is a property of the declared graph: estimated moments cannot make a trap answerable."""
    for kind in frontdoor_splits.DECLINE_KINDS:
        for seed in frontdoor_splits.FRONT_DOOR["development"]["seeds"][:5]:
            world = frontdoor_splits.generate(kind, seed)
            assert causal.answer(frontdoor_splits.sampled(world, 500))["value"] is None


def _random_world(rng):
    """A random linear-Gaussian DAG over 3 to 7 observed nodes with a hidden U acting on the cause X, the outcome Y
    and, at random, on other nodes; some nodes other than X and Y are left out of the data. Edges are drawn with
    probability 0.3 (0.15 for X -> Y), and a path X -> M -> Y through a node between them is added in 4 of 5 graphs,
    so that many graphs reach the criterion's later conditions."""
    n = rng.randint(3, 7)
    names = [f"V{i}" for i in range(n)]
    x_at = rng.randrange(0, n - 2)
    y_at = rng.randrange(x_at + 2, n)
    x, y = names[x_at], names[y_at]
    parents = {v: [names[j] for j in range(i) if rng.random() < (0.15 if (names[j], v) == (x, y) else 0.3)]
               for i, v in enumerate(names)}
    if rng.random() < 0.8:
        m = names[rng.randrange(x_at + 1, y_at)]
        parents[m] = sorted(set(parents[m]) | {x})
        parents[y] = sorted(set(parents[y]) | {m})
    for v in names:
        if v in (x, y) or rng.random() < 0.2:
            parents[v].append("U")
    scm = {"order": ["U", *names], "edges": {v: {p: rng.choice((-1, 1)) * rng.uniform(0.5, 1.5) for p in ps}
                                              for v, ps in {"U": [], **parents}.items()},
           "intercept": {v: rng.uniform(-1, 1) for v in ["U", *names]}, "noise_sd": 1.0}
    data = [v for v in names if v in (x, y) or rng.random() > 0.15]
    mean, load, idx = frontdoor_splits._moments(scm)
    cov = load @ load.T
    value = round(rng.uniform(-2, 2), 3)
    query = {"graph": {v: [p for p in ps if p != "U"] for v, ps in parents.items()},
             "hidden_common_cause": [v for v in names if "U" in parents[v]],
             "observational_mean": {v: float(mean[idx[v]]) for v in data},
             "observational_covariance": {a: {b: float(cov[idx[a], idx[b]]) for b in data} for a in data},
             "do": {x: value}, "ask": y}
    truth = float(frontdoor_splits._moments(scm, {x: value})[0][idx[y]])
    return query, truth, float(np.sqrt(cov[idx[y], idx[y]]))


def test_random_graphs_every_answer_equals_the_interventional_truth():
    """Soundness on 6000 random graphs: whenever the engine answers, the answer is the truth from the equations (to
    floating point); a criterion check that let a non-identified graph through would answer it wrongly (generically).
    Not vacuous: over a third of the graphs is answered (2234 of 6000 when written), and the same graphs catch an
    engine whose criterion check is broken (the test below)."""
    answered, false_answers = _random_graph_run()
    assert false_answers == 0
    assert answered >= 1500


def _random_graph_run(count=6000):
    rng = random.Random("lane-c:random-graphs")
    answered = false_answers = 0
    for _ in range(count):
        query, truth, scale = _random_world(rng)
        out = causal.answer(query)
        if out["value"] is None:
            continue
        answered += 1
        false_answers += abs(out["value"] - truth) > 1e-8 * scale
    return answered, false_answers


@pytest.mark.parametrize("broken", ["no separation check", "no direct-edge check"])
def test_the_random_graphs_catch_a_broken_criterion(monkeypatch, broken):
    """The property test has power: with the d-separation conditions skipped, or with X -> Y treated as if it were
    intercepted, the same graphs produce false answers."""
    if broken == "no separation check":
        monkeypatch.setattr(causal, "_d_separated", lambda *a: True)
    else:
        real = causal._frontdoor

        def without_direct_edge(query):
            graph = {v: [p for p in ps if (p, v) != (next(iter(query["do"])), query["ask"])]
                     for v, ps in query["graph"].items()}
            return real(query | {"graph": graph})
        monkeypatch.setattr(causal, "_frontdoor", without_direct_edge)
    assert _random_graph_run(2000)[1] > 0


# ---- the benchmark: front door on, off, and every other answer unchanged ----------------------------------------------

def test_counterfactual_front_door_answered_and_every_other_answer_leaf_identical():
    for split in ("development", "held_out"):
        spec = {"seeds": splits.COUNTERFACTUAL[split]["seeds"][:1], "scm": splits.COUNTERFACTUAL[split]["scm"],
                "graph": (), "belief": ()}
        on = counterfactual.run(spec, lambda: imagination.ImaginationAnswerer())
        off = counterfactual.run(spec, lambda: imagination.ImaginationAnswerer(frozenset({"front_door"})))
        assert on["summary_by_group"]["front_door"]["front_door_within_tolerance"]["mean"] == 1.0
        assert on["summary_by_group"]["front_door"]["answered_within_strict_tolerance"]["mean"] == 1.0
        assert off["summary_by_group"]["front_door"]["front_door_declined"]["mean"] == 4
        for g in ("handed_equations", "belief_what_if", "learned_graph"):
            assert on["summary_by_group"][g] == off["summary_by_group"][g]
        kinds = {k for k in on["by_query_kind"] if k != "scm:confounded"}
        assert {k: on["by_query_kind"][k] for k in kinds} == {k: off["by_query_kind"][k] for k in kinds}


def test_the_new_adversarial_cases_pass_with_the_front_door_on():
    for case in adversarial.CASES_WAVE3_C:
        result = case()
        assert result["pass"] is True, result["observed"]
        assert result["observed"].startswith("5 of 5 declined")


# ---- the owner's command ------------------------------------------------------------------------------------------

def _scm(path, tmp_path, *flags):
    src = Path(neuroscifi.__file__).resolve().parents[1]
    env = {"PYTHONPATH": str(src), "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    return subprocess.run([sys.executable, "-m", "neuroscifi.cli", "imagine", "scm", str(path), *flags],
                          capture_output=True, text=True, timeout=60, env=env)


def test_nsf_imagine_scm_answers_by_the_front_door_unless_told_not_to(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(json.dumps(worked()))
    on = _scm(path, tmp_path)
    assert on.returncode == 0, on.stderr
    answer = json.loads(on.stdout)
    assert answer["identified_by"] == "front_door" and answer["value"] == pytest.approx(11.0)
    off = _scm(path, tmp_path, "--no-front-door")
    assert off.returncode == 0, off.stderr
    assert json.loads(off.stdout)["value"] is None
