"""Lane G2: the explorable model's little language in Python (artifacts/expr.py) and the model's semantics.

Expected values are worked out by hand or by an independent loop written here."""
import math

import pytest

from neuroscifi.artifacts import expr

ENV = {"a": 2.0, "b": 3.0, "x": -1.5}


@pytest.mark.parametrize("src,want", [
    ("1 + 2 * 3", 7.0), ("(1 + 2) * 3", 9.0), ("-2^2", -4.0), ("2^-1", 0.5), ("2^3^2", 512.0),
    ("a * b - x", 7.5), ("a ≤ b", 1.0), ("a >= b", 0.0), ("a == 2", 1.0), ("a != 2", 0.0), ("3 × 4 ÷ 2 − 1", 5.0),
    ("if(a < b, 10, 20)", 10.0), ("if(0, 1, 2)", 2.0), ("if(0/0, 1, 2)", 2.0), ("min(3, a, b)", 2.0),
    ("max(1, x)", 1.0), ("clamp(x, 0, 1)", 0.0), ("clamp(5, 0, 1)", 1.0), ("abs(x)", 1.5), ("sqrt(16)", 4.0),
    ("ln(e)", 1.0), ("log10(1000)", 3.0), ("exp(0)", 1.0), ("sin(0)", 0.0), ("cos(0)", 1.0), ("2 * pi", 2 * math.pi),
    (".5e1", 5.0), ("1.", 1.0), ("+a", 2.0), ("--a", 2.0),
])
def test_values(src, want):
    assert expr.evaluate(expr.parse(src), ENV) == pytest.approx(want, rel=1e-15, abs=1e-15)


@pytest.mark.parametrize("src,kind", [
    ("1/0", math.inf), ("-1/0", -math.inf), ("0/0", "nan"), ("sqrt(-1)", "nan"), ("ln(0)", -math.inf),
    ("ln(-1)", "nan"), ("log10(0)", -math.inf), ("exp(1000)", math.inf), ("1^(1/0)", "nan"), ("(-8)^(1/3)", "nan"),
    ("0^-1", math.inf), ("(-0)^-1", -math.inf), ("(-0)^-2", math.inf), ("10^400", math.inf), ("(-10)^401", -math.inf),
    ("(0/0)^0", 1.0), ("min(1, 0/0)", "nan"), ("1/(-0)", -math.inf), ("1/min(0, -0)", -math.inf),
    ("1/max(-0, 0)", math.inf), ("sin(1/0)", "nan"), ("2^(0/0)", "nan"),
])
def test_ecmascript_conventions(src, kind):
    got = expr.evaluate(expr.parse(src), {})
    if kind == "nan":
        assert math.isnan(got)
    else:
        assert got == kind


def test_trees_are_the_shared_json_shape():
    assert expr.parse("a + 2*b") == ["bin", "+", ["var", "a"], ["bin", "*", ["num", 2.0], ["var", "b"]]]
    assert expr.parse("if(a<b, -a, 1)") == ["call", "if", [["cmp", "<", ["var", "a"], ["var", "b"]],
                                                           ["neg", ["var", "a"]], ["num", 1.0]]]
    assert expr.names_in(expr.parse("a + pi * sin(b) + e")) == {"a", "b"}


@pytest.mark.parametrize("src,needle", [
    ("", "empty"), ("1 +", "ends too soon"), ("(1", "expected '.' at"), ("1)", "unexpected"), ("foo(1)", "not a function"),
    ("sin 1", "needs its arguments"), ("a < b < c", "only one comparison"), ("1 = 2", "unexpected character"),
    ("sin(1, 2)", "takes 1"), ("min(1)", "takes 2 to 8"), ("q", "unknown name"), ("a; b", "unexpected character"),
    ("x" * 33, "longer than 32"), ("(" * 33 + "1" + ")" * 33, "nested deeper"), ("-" * 40 + "1", "nested deeper"),
    ("1" + "+1" * 300, "longer than 500"), ("+".join(["a"] * 140), "more than 256 parts"),
    ("__import__('os')", "unexpected"), ("a.b", "malformed number"), ("1\n+1", "unexpected character"), ("٣", "unexpected"),
    ("2 ^", "ends too soon"), ("1e", "unexpected"), ("1e400", "too large"),
])
def test_refusals_are_named(src, needle):
    with pytest.raises(expr.ExprError, match=needle):
        expr.parse(src, ["a", "b", "c"])


def test_nothing_outside_the_tree_is_reachable():
    for src in ("__class__", "a.__class__", "eval(1)", "exec(1)", "open(1)", "lambda", "[1]", "{}", "'x'"):
        with pytest.raises(expr.ExprError):
            expr.evaluate(expr.parse(src, ["a"]), {"a": 1})


SPRING = {
    "parameters": [{"name": "m", "label": "Mass", "min": 0.1, "max": 5, "step": 0.1, "value": 1},
                   {"name": "k", "label": "Stiffness", "min": 1, "max": 50, "step": 1, "value": 10},
                   {"name": "c", "label": "Damping", "min": 0, "max": 5, "step": 0.1, "value": 0.4}],
    "quantities": [{"name": "omega", "expr": "sqrt(k/m)"}, {"name": "energy", "expr": "0.5*m*v^2 + 0.5*k*x^2"}],
    "dynamics": {"state": [{"name": "v", "initial": "0", "update": "v + (-(k*x) - c*v)/m*dt"},
                           {"name": "x", "initial": "0.5", "update": "x + v*dt"}], "steps": 400, "dt": 0.01},
    "views": [{"kind": "plot", "x": "time", "y": ["x"]}, {"kind": "table", "items": ["omega", "x", "energy"]},
              {"kind": "plot", "x": "k", "y": ["omega"], "points": 5}],
}


def test_model_semantics_match_an_independent_loop():
    got = expr.evaluate_model(SPRING, {"c": 1.0})
    m, k, c, dt = 1.0, 10.0, 1.0, 0.01
    x, v, xs, energies = 0.5, 0.0, [], []
    for step in range(401):
        xs.append(x)
        energies.append(0.5 * m * v ** 2 + 0.5 * k * x ** 2)
        if step == 400:
            break
        v = v + (-(k * x) - c * v) / m * dt      # the updates run in order: x sees the new v
        x = x + v * dt
    assert got["series"]["x"] == xs
    assert got["series"]["energy"] == pytest.approx(energies, rel=1e-15)
    assert got["times"][-1] == 400 * 0.01 and len(got["times"]) == 401
    assert got["values"]["x"] == xs[-1] and got["values"]["omega"] == math.sqrt(10.0)
    assert got["sweeps"][0]["x"] == [1.0, 13.25, 25.5, 37.75, 50.0]
    assert got["sweeps"][0]["omega"] == [math.sqrt(v / 1.0) for v in (1.0, 13.25, 25.5, 37.75, 50.0)]
    assert expr.model_problems(SPRING) == []


@pytest.mark.parametrize("change,needle", [
    (lambda d: d["parameters"].append({"name": "m", "label": "again", "min": 0, "max": 1, "step": 0.1, "value": 0}),
     "used twice"),
    (lambda d: d["parameters"].append({"name": "pi", "label": "pi", "min": 0, "max": 1, "step": 0.1, "value": 0}),
     "reserved"),
    (lambda d: d["parameters"][0].update(value=9), "starting value"),
    (lambda d: d["parameters"][0].update(step=0), "step"),
    (lambda d: d["parameters"][0].update(min=6), "below max"),
    (lambda d: d["quantities"].append({"name": "w", "expr": "later + 1"}), "unknown name"),
    (lambda d: d["dynamics"]["state"][0].update(initial="x"), "starting value"),
    (lambda d: d["dynamics"].update(steps=50000), "steps"),
    (lambda d: d["views"].append({"kind": "plot", "x": "k", "y": ["energy"]}), "sweep"),
    (lambda d: d["views"].append({"kind": "plot", "x": "nothing", "y": ["omega"]}), "x must be"),
    (lambda d: d["views"].append({"kind": "table", "items": ["ghost"]}), "unknown name"),
    (lambda d: d["views"].append({"kind": "plot", "x": "time"}), "a plot needs x and y"),
    (lambda d: d["views"].append({"kind": "pie", "items": ["omega"]}), "unknown kind"),
])
def test_model_problems_are_named(change, needle):
    import copy
    data = copy.deepcopy(SPRING)
    change(data)
    assert any(needle in p for p in expr.model_problems(data)), expr.model_problems(data)


def test_a_plot_over_time_needs_dynamics():
    data = {"parameters": SPRING["parameters"], "quantities": [{"name": "w", "expr": "k/m"}],
            "views": [{"kind": "plot", "x": "time", "y": ["w"]}]}
    assert any("needs dynamics" in p for p in expr.model_problems(data))
