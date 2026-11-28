"""Lane G2: the expression language's two implementations agree (artifacts/expr.py and the runtime
artifacts/web/explorable.mjs run under node): the same strings are accepted or refused, accepted ones give the same
tree, and the same values within 1e-12 relative (the two languages' sin, exp, log and pow may differ in the last
bit), with the same undefined values, infinities and signs of zero. Skipped, with the reason, where node is absent.
"""
import json
import math
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from neuroscifi.artifacts import expr

RUNTIME = Path(__file__).resolve().parents[2] / "src" / "neuroscifi" / "artifacts" / "web" / "explorable.mjs"
NODE = shutil.which("node")
N_EXPRESSIONS = 3000

DRIVER = r"""
import { parse, evaluate } from %s;
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (c) => { input += c; });
process.stdin.on('end', () => {
  const out = [];
  const show = (v) => (Object.is(v, -0) ? '-0' : String(v));
  for (const c of JSON.parse(input)) {
    try {
      const tree = parse(c.src);
      const values = c.envs.map((e) => { try { return show(evaluate(tree, new Map(Object.entries(e)))); } catch (err) { return 'error'; } });
      out.push({ ok: true, tree, values });
    } catch (err) {
      out.push({ ok: false, error: err.constructor.name });
    }
  }
  process.stdout.write(JSON.stringify(out));
});
"""

NUMBERS = ["0", "1", "2", "3", "0.5", "2.5", "10", "100", ".25", "1e3", "1e-3", "3.14159", "1e300", "1e-300", "7.", "42"]
NAMES = ["a", "b", "c", "x", "pi", "e"]
OPS = ["+", "-", "*", "/", "^", "−", "×", "÷", "·"]
CMPS = ["<", "<=", ">", ">=", "==", "!=", "≤", "≥", "≠"]
ONE_ARG = ["sin", "cos", "tan", "exp", "ln", "log10", "sqrt", "abs"]


def _gen(rng: random.Random, depth: int) -> str:
    if depth <= 0 or rng.random() < 0.25:
        return rng.choice(NUMBERS) if rng.random() < 0.5 else rng.choice(NAMES)
    kind = rng.random()
    sp = " " if rng.random() < 0.5 else ""
    if kind < 0.40:
        return f"{_gen(rng, depth - 1)}{sp}{rng.choice(OPS)}{sp}{_gen(rng, depth - 1)}"
    if kind < 0.50:
        return f"({_gen(rng, depth - 1)})"
    if kind < 0.58:
        return f"-{_gen(rng, depth - 1)}"
    if kind < 0.75:
        return f"{rng.choice(ONE_ARG)}({_gen(rng, depth - 1)})"
    if kind < 0.85:
        fn = rng.choice(["min", "max"])
        return f"{fn}(" + f",{sp}".join(_gen(rng, depth - 1) for _ in range(rng.randint(2, 4))) + ")"
    if kind < 0.90:
        return f"clamp({_gen(rng, depth - 1)}, {_gen(rng, depth - 1)}, {_gen(rng, depth - 1)})"
    cond = f"{_gen(rng, depth - 1)}{sp}{rng.choice(CMPS)}{sp}{_gen(rng, depth - 1)}"
    return f"if({cond}, {_gen(rng, depth - 1)}, {_gen(rng, depth - 1)})"


def _mutate(rng: random.Random, src: str) -> str:
    """A nearby string that is often not in the language (both sides must refuse the same ones)."""
    i = rng.randrange(len(src) + 1)
    choice = rng.random()
    if choice < 0.4 and src:
        return src[:i] + src[i + 1:]
    if choice < 0.8:
        return src[:i] + rng.choice(list("()+-*/^,<>=!.e1 a;_") + ["sin(", "**", "<<"]) + src[i:]
    return src[:i] + src[i:][::-1]


def _cases():
    rng = random.Random(20261126)
    cases = []
    for k in range(N_EXPRESSIONS):
        src = _gen(rng, rng.randint(1, 5))
        if k % 5 == 4:
            src = _mutate(rng, src)
        envs = [{n: rng.choice([0.0, -0.0, 1.0, -2.5, 3.0, 0.1, 1e-200, -1e200, 7.25, 0.5]) for n in "abcx"}
                for _ in range(2)]
        cases.append({"src": src, "envs": envs})
    return cases


def _floats(tree):
    if isinstance(tree, list):
        return [_floats(t) for t in tree]
    if isinstance(tree, (int, float)) and not isinstance(tree, bool):
        return float(tree)
    return tree


def _same(py: float, js: str) -> bool:
    j = float({"NaN": "nan", "Infinity": "inf", "-Infinity": "-inf", "-0": "-0.0"}.get(js, js))
    if math.isnan(py) or math.isnan(j):
        return math.isnan(py) and math.isnan(j)
    if math.isinf(py) or math.isinf(j) or py == 0 or j == 0:
        return py == j and math.copysign(1, py) == math.copysign(1, j)
    return abs(py - j) <= 1e-12 * max(abs(py), abs(j))


@pytest.mark.skipif(NODE is None, reason="node is not installed here, so the runtime's evaluator cannot be run")
def test_python_and_the_runtime_agree_on_generated_expressions():
    cases = _cases()
    driver = DRIVER % json.dumps(RUNTIME.as_uri())
    done = subprocess.run([NODE, "--input-type=module", "-e", driver], input=json.dumps(cases), capture_output=True,
                          text=True, timeout=120, check=True)
    js = json.loads(done.stdout)
    assert len(js) == len(cases)
    accepted = refused = values = 0
    disagreements = []
    for case, j in zip(cases, js, strict=True):
        try:
            tree = expr.parse(case["src"])
        except expr.ExprError:
            if j["ok"]:
                disagreements.append(("python refused, runtime accepted", case["src"]))
            refused += 1
            continue
        if not j["ok"]:
            disagreements.append(("python accepted, runtime refused", case["src"]))
            continue
        accepted += 1
        if _floats(tree) != _floats(j["tree"]):
            disagreements.append(("different trees", case["src"]))
            continue
        for env, got in zip(case["envs"], j["values"], strict=True):
            values += 1
            try:
                mine = expr.evaluate(tree, env)
            except expr.ExprError:          # a name with no value: both sides must say so
                if got != "error":
                    disagreements.append(("python found no value, runtime did", case["src"]))
                continue
            if got == "error" or not _same(mine, got):
                disagreements.append(("different values", case["src"], env, mine, got))
    assert not disagreements, disagreements[:5]
    assert accepted > 2000 and refused > 300 and values == 2 * accepted
