"""The explorable model's little language, in Python (lane G2, DESIGN §6). The same language is implemented in the
fixed runtime `artifacts/web/explorable.mjs`; tests prove the two agree on thousands of generated expressions.

An expression is text that is PARSED into a tree and the tree is evaluated: nothing is ever handed to `eval`, and a
name can only be a declared parameter, quantity, state variable, `t`, `dt`, or the constants `pi` and `e`.

Grammar (one comparison at most, at the top):
    expr    := sum [ ("<" | "<=" | ">" | ">=" | "==" | "!=" | "≤" | "≥" | "≠") sum ]
    sum     := product { ("+" | "-" | "−") product }
    product := unary { ("*" | "/" | "×" | "÷" | "·") unary }
    unary   := ("-" | "−" | "+") unary | power
    power   := primary [ "^" unary ]                 (right-associative; -2^2 is -(2^2))
    primary := number | name | name "(" expr { "," expr } ")" | "(" expr ")"
Functions: sin cos tan exp ln log10 sqrt abs (one argument), min max (2 to 8), clamp(x, lo, hi), if(c, a, b).
A comparison is 1 when true and 0 when false; `if` takes `a` when c is neither 0 nor undefined (NaN), else `b`.
Arithmetic is IEEE double precision with the conventions of ECMAScript: 1/0 is infinity, 0/0 and sqrt(-1) are
undefined (NaN), ln(0) is minus infinity, 1^infinity is undefined, min/max of an undefined value is undefined.

Trees are nested lists that serialise to the same JSON in both languages:
    ["num", x] | ["var", name] | ["neg", a] | ["bin", op, a, b] | ["cmp", op, a, b] | ["call", fn, [args]]
with op one of + - * / ^ and comparison ops < <= > >= == !=.

The model semantics (shared by the runtime, the generator's static fallback and the checker) are here too:
`evaluate_model(data, values)`.
"""
from __future__ import annotations

import math
import re

MAX_SOURCE = 500
MAX_NODES = 256
MAX_DEPTH = 32
MAX_NAME = 32
FUNCTIONS = {"sin": (1, 1), "cos": (1, 1), "tan": (1, 1), "exp": (1, 1), "ln": (1, 1), "log10": (1, 1),
             "sqrt": (1, 1), "abs": (1, 1), "min": (2, 8), "max": (2, 8), "clamp": (3, 3), "if": (3, 3)}
CONSTANTS = {"pi": math.pi, "e": math.e}
RESERVED = set(FUNCTIONS) | set(CONSTANTS) | {"t", "dt"}
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(r"(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_ALIASES = {"−": "-", "×": "*", "÷": "/", "·": "*", "≤": "<=", "≥": ">=", "≠": "!="}
_TWO = ("<=", ">=", "==", "!=")
_ONE = "+-*/^()<>,"
NAN = float("nan")
INF = float("inf")


class ExprError(ValueError):
    """An expression that is not in the language (a plain reason, with the position where it went wrong)."""


def tokenize(src: str) -> list[tuple[str, object, int]]:
    if not isinstance(src, str):
        raise ExprError("an expression is text")
    if len(src) > MAX_SOURCE:
        raise ExprError(f"an expression is longer than {MAX_SOURCE} characters")
    out = []
    i = 0
    while i < len(src):
        ch = src[i]
        if ch in " \t":
            i += 1
        elif ch in _ALIASES:
            out.append(("op", _ALIASES[ch], i))
            i += 1
        elif src[i:i + 2] in _TWO:
            out.append(("op", src[i:i + 2], i))
            i += 2
        elif ch in _ONE:
            out.append(("op", ch, i))
            i += 1
        elif ch.isascii() and (ch.isdigit() or ch == "."):
            m = _NUMBER.match(src, i)
            if not m:
                raise ExprError(f"a malformed number at position {i + 1}")
            value = float(m.group())
            if math.isinf(value):
                raise ExprError(f"the number at position {i + 1} is too large")
            out.append(("num", value, i))
            i = m.end()
        elif (m := NAME.match(src, i)) is not None:
            if len(m.group()) > MAX_NAME:
                raise ExprError(f"a name at position {i + 1} is longer than {MAX_NAME} characters")
            out.append(("name", m.group(), i))
            i = m.end()
        else:
            raise ExprError(f"unexpected character at position {i + 1}")
    out.append(("end", None, len(src)))
    return out


_COMPARE = ("<", "<=", ">", ">=", "==", "!=")


class _Parser:
    """Recursive descent. `depth` counts nesting: brackets, function calls, signs and exponents; more than MAX_DEPTH
    is refused. Every node of the tree counts toward MAX_NODES (brackets and a leading + make no node)."""

    def __init__(self, src: str, names):
        self.tokens = tokenize(src)
        self.at = 0
        self.names = names
        self.nodes = 0

    def peek(self):
        return self.tokens[self.at]

    def take(self):
        tok = self.tokens[self.at]
        self.at += 1
        return tok

    def is_op(self, *values):
        tok = self.tokens[self.at]
        return tok[0] == "op" and tok[1] in values

    def node(self, value):
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise ExprError(f"an expression has more than {MAX_NODES} parts")
        return value

    def nest(self, depth):
        if depth > MAX_DEPTH:
            raise ExprError(f"an expression is nested deeper than {MAX_DEPTH}")
        return depth

    def expr(self, depth):
        left = self.sum(depth)
        if self.is_op(*_COMPARE):
            op = self.take()[1]
            right = self.sum(depth)
            if self.is_op(*_COMPARE):
                raise ExprError(f"only one comparison is allowed (position {self.peek()[2] + 1})")
            return self.node(["cmp", op, left, right])
        return left

    def sum(self, depth):
        left = self.product(depth)
        while self.is_op("+", "-"):
            op = self.take()[1]
            left = self.node(["bin", op, left, self.product(depth)])
        return left

    def product(self, depth):
        left = self.unary(depth)
        while self.is_op("*", "/"):
            op = self.take()[1]
            left = self.node(["bin", op, left, self.unary(depth)])
        return left

    def unary(self, depth):
        if self.is_op("-", "+"):
            op = self.take()[1]
            inner = self.unary(self.nest(depth + 1))
            return self.node(["neg", inner]) if op == "-" else inner
        return self.power(depth)

    def power(self, depth):
        base = self.primary(depth)
        if self.is_op("^"):
            self.take()
            return self.node(["bin", "^", base, self.unary(self.nest(depth + 1))])
        return base

    def primary(self, depth):
        kind, value, pos = self.take()
        if kind == "num":
            return self.node(["num", value])
        if kind == "name":
            if self.is_op("("):
                if value not in FUNCTIONS:
                    raise ExprError(f"{value!r} at position {pos + 1} is not a function of the language")
                self.take()
                inner = self.nest(depth + 1)
                args = [self.expr(inner)]
                while self.is_op(","):
                    self.take()
                    args.append(self.expr(inner))
                if not self.is_op(")"):
                    raise ExprError(f"expected ')' at position {self.peek()[2] + 1}")
                self.take()
                low, high = FUNCTIONS[value]
                if not low <= len(args) <= high:
                    raise ExprError(f"{value} takes {low if low == high else f'{low} to {high}'} argument(s)")
                return self.node(["call", value, args])
            if value in FUNCTIONS:
                raise ExprError(f"the function {value} at position {pos + 1} needs its arguments in brackets")
            if value not in CONSTANTS and self.names is not None and value not in self.names:
                raise ExprError(f"unknown name {value!r} at position {pos + 1}")
            return self.node(["var", value])
        if kind == "op" and value == "(":
            inner = self.expr(self.nest(depth + 1))
            if not self.is_op(")"):
                raise ExprError(f"expected ')' at position {self.peek()[2] + 1}")
            self.take()
            return inner
        if kind == "end":
            raise ExprError("the expression ends too soon")
        raise ExprError(f"unexpected {value!r} at position {pos + 1}")


def parse(src: str, names=None) -> list:
    """The tree of `src`; with `names`, every name must be one of them (or a constant)."""
    p = _Parser(src, set(names) if names is not None else None)
    if p.peek()[0] == "end":
        raise ExprError("an empty expression")
    tree = p.expr(0)
    tok = p.peek()
    if tok[0] != "end":
        raise ExprError(f"unexpected {tok[1]!r} at position {tok[2] + 1}")
    return tree


def names_in(tree) -> set[str]:
    out = set()
    stack = [tree]
    while stack:
        node = stack.pop()
        kind = node[0]
        if kind == "var":
            out.add(node[1])
        elif kind == "neg":
            stack.append(node[1])
        elif kind in ("bin", "cmp"):
            stack += [node[2], node[3]]
        elif kind == "call":
            stack += node[2]
    return out - set(CONSTANTS)


# ---------------------------------------------------------------------------------------------------------------
# Arithmetic with ECMAScript's conventions


def _odd_integer(x: float) -> bool:
    return math.isfinite(x) and x == math.floor(x) and math.fmod(x, 2.0) != 0


def js_pow(a: float, b: float) -> float:
    if math.isnan(b):
        return NAN
    if b == 0:
        return 1.0
    if math.isnan(a):
        return NAN
    if abs(a) == 1 and math.isinf(b):
        return NAN
    try:
        return math.pow(a, b)
    except ValueError:
        if a == 0:          # 0 to a negative power
            return math.copysign(INF, a) if _odd_integer(b) else INF
        return NAN          # a negative base to a fractional power
    except OverflowError:
        return -INF if a < 0 and _odd_integer(b) else INF


def _div(a: float, b: float) -> float:
    if b == 0:
        if a == 0 or math.isnan(a):
            return NAN
        return math.copysign(INF, a) * math.copysign(1.0, b)
    return a / b


def _unary(fn, x: float) -> float:
    if math.isnan(x):
        return NAN
    try:
        if fn == "sin":
            return math.sin(x)
        if fn == "cos":
            return math.cos(x)
        if fn == "tan":
            return math.tan(x)
        if fn == "exp":
            return math.exp(x)
        if fn == "ln":
            return -INF if x == 0 else math.log(x)
        if fn == "log10":
            return -INF if x == 0 else math.log10(x)
        if fn == "sqrt":
            return math.sqrt(x)
        if fn == "abs":
            return abs(x)
    except OverflowError:
        return INF
    except ValueError:
        return NAN
    raise ExprError(f"unknown function {fn!r}")


def _min(values) -> float:
    out = INF
    for v in values:
        if math.isnan(v):
            return NAN
        if v < out or (v == 0 and out == 0 and math.copysign(1.0, v) < 0):
            out = v
    return out


def _max(values) -> float:
    out = -INF
    for v in values:
        if math.isnan(v):
            return NAN
        if v > out or (v == 0 and out == 0 and math.copysign(1.0, v) > 0):
            out = v
    return out


_CMP = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
        "==": lambda a, b: a == b, "!=": lambda a, b: a != b}


def evaluate(tree, env) -> float:
    """The value of a parsed tree; `env` maps names to numbers. Iterative depth is bounded by MAX_DEPTH."""
    kind = tree[0]
    if kind == "num":
        return float(tree[1])
    if kind == "var":
        name = tree[1]
        if name in env:
            return float(env[name])
        if name in CONSTANTS:
            return CONSTANTS[name]
        raise ExprError(f"no value for {name!r}")
    if kind == "neg":
        return -evaluate(tree[1], env)
    if kind == "bin":
        op, a, b = tree[1], evaluate(tree[2], env), evaluate(tree[3], env)
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            return _div(a, b)
        return js_pow(a, b)
    if kind == "cmp":
        return 1.0 if _CMP[tree[1]](evaluate(tree[2], env), evaluate(tree[3], env)) else 0.0
    fn, args = tree[1], tree[2]
    if fn == "if":
        c = evaluate(args[0], env)
        return evaluate(args[1] if (c != 0 and not math.isnan(c)) else args[2], env)
    values = [evaluate(a, env) for a in args]
    if fn == "min":
        return _min(values)
    if fn == "max":
        return _max(values)
    if fn == "clamp":
        return _min([_max([values[0], values[1]]), values[2]])
    return _unary(fn, values[0])


# ---------------------------------------------------------------------------------------------------------------
# The model


MAX_STEPS = 5000
MAX_SWEEP = 401


def model_problems(data: dict) -> list[str]:
    """Every reason a model's data cannot be run: names, expressions, ranges and views."""
    problems: list[str] = []
    known: list[str] = []

    def claim(name, what):
        if not isinstance(name, str) or not NAME.fullmatch(name) or len(name) > MAX_NAME:
            problems.append(f"{what} name {name!r} is not a plain name")
        elif name in RESERVED:
            problems.append(f"{what} name {name!r} is reserved")
        elif name in known:
            problems.append(f"the name {name!r} is used twice")
        else:
            known.append(name)

    params = data.get("parameters", [])
    for p in params:
        claim(p.get("name"), "a parameter")
        lo, hi, step, value = p.get("min"), p.get("max"), p.get("step"), p.get("value")
        if not (isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and lo < hi):
            problems.append(f"parameter {p.get('name')!r}: min must be below max")
        elif not (isinstance(step, (int, float)) and 0 < step <= hi - lo):
            problems.append(f"parameter {p.get('name')!r}: the step must be positive and fit the range")
        elif not (isinstance(value, (int, float)) and lo <= value <= hi):
            problems.append(f"parameter {p.get('name')!r}: the starting value must lie in the range")
    dyn = data.get("dynamics")
    state = [s.get("name") for s in dyn.get("state", [])] if dyn else []
    param_names = list(known)
    statics: list[str] = []
    dynamic: set[str] = set()
    for q in data.get("quantities", []):
        name = q.get("name")
        scope = known + (state + ["t", "dt"] if dyn else [])
        try:
            tree = parse(q.get("expr", ""), scope)
            if names_in(tree) & (set(state) | {"t", "dt"} | dynamic):
                dynamic.add(name)
            else:
                statics.append(name)
        except ExprError as error:
            problems.append(f"quantity {name!r}: {error}")
        claim(name, "a quantity")
    if dyn:
        for s in dyn.get("state", []):
            claim(s.get("name"), "a state variable")
        for s in dyn.get("state", []):
            try:
                parse(s.get("initial", ""), param_names)
            except ExprError as error:
                problems.append(f"state {s.get('name')!r} starting value: {error}")
            try:
                parse(s.get("update", ""), known + ["t", "dt"])
            except ExprError as error:
                problems.append(f"state {s.get('name')!r} update: {error}")
        steps, dt = dyn.get("steps"), dyn.get("dt")
        if not (isinstance(steps, int) and 1 <= steps <= MAX_STEPS):
            problems.append(f"dynamics: steps must be a whole number from 1 to {MAX_STEPS}")
        if not (isinstance(dt, (int, float)) and dt > 0 and math.isfinite(dt)):
            problems.append("dynamics: dt must be a positive number")
    for k, view in enumerate(data.get("views", [])):
        kind = view.get("kind")
        if kind not in ("plot", "table", "bars"):
            problems.append(f"view {k + 1}: unknown kind")
            continue
        items = view.get("y", []) if kind == "plot" else view.get("items", [])
        if not items or (kind == "plot" and not view.get("x")):
            problems.append(f"view {k + 1}: a plot needs x and y; a table or bars need items")
        for name in items:
            if name not in known:
                problems.append(f"view {k + 1}: unknown name {name!r}")
        if kind == "plot":
            x = view.get("x")
            if x == "time":
                if not dyn:
                    problems.append(f"view {k + 1}: a plot over time needs dynamics")
            elif x in param_names:
                bad = [n for n in items if n in dynamic or n in state]
                if bad:
                    problems.append(f"view {k + 1}: a sweep can plot only quantities that do not change over time")
                p = next(p for p in params if p["name"] == x)
                lo, hi = view.get("from", p.get("min")), view.get("to", p.get("max"))
                if not (isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and lo < hi):
                    problems.append(f"view {k + 1}: the sweep's range must rise")
                points = view.get("points", 101)
                if not (isinstance(points, int) and 2 <= points <= MAX_SWEEP):
                    problems.append(f"view {k + 1}: a sweep has 2 to {MAX_SWEEP} points")
            else:
                problems.append(f"view {k + 1}: x must be 'time' or a parameter's name")
    return problems


def evaluate_model(data: dict, values: dict | None = None) -> dict:
    """Run a model at the given parameter values (starting values for those not given).

    Returns {"values": {name: number} (parameters, quantities that do not change over time, and for dynamics the
    state and changing quantities at the last step), "times": [t0, t1, ...], "series": {name: [...]} (state and
    changing quantities at every step, plus constant quantities repeated), "sweeps": [{"x": [...], name: [...]}]
    one per sweep plot, in view order}.

    Dynamics: t_k = k * dt; the state starts at its `initial` expressions; at each step the changing quantities are
    computed from the state at the start of the step, then the updates run in the order listed, each seeing the new
    value of every state variable listed before it.
    """
    values = values or {}
    params = {p["name"]: float(values.get(p["name"], p["value"])) for p in data.get("parameters", [])}
    trees = {q["name"]: parse(q["expr"]) for q in data.get("quantities", [])}
    dyn = data.get("dynamics")
    state_names = [s["name"] for s in dyn["state"]] if dyn else []
    changing: set[str] = set()
    for q in data.get("quantities", []):
        if names_in(trees[q["name"]]) & (set(state_names) | {"t", "dt"} | changing):
            changing.add(q["name"])
    env = dict(params)
    for q in data.get("quantities", []):
        if q["name"] not in changing:
            env[q["name"]] = evaluate(trees[q["name"]], env)
    out_values = dict(env)
    series: dict[str, list[float]] = {}
    times: list[float] = []
    if dyn:
        dt, steps = float(dyn["dt"]), int(dyn["steps"])
        init = {s["name"]: parse(s["initial"]) for s in dyn["state"]}
        update = {s["name"]: parse(s["update"]) for s in dyn["state"]}
        state = {n: evaluate(init[n], params) for n in state_names}
        series = {n: [] for n in state_names + [q["name"] for q in data["quantities"] if q["name"] in changing]}
        for k in range(steps + 1):
            t = k * dt
            step_env = {**env, **state, "t": t, "dt": dt}
            for q in data.get("quantities", []):
                if q["name"] in changing:
                    step_env[q["name"]] = evaluate(trees[q["name"]], step_env)
            times.append(t)
            for n in series:
                series[n].append(step_env[n])
            if k == steps:
                out_values.update({n: step_env[n] for n in series})
                break
            for n in state_names:
                step_env[n] = evaluate(update[n], step_env)
                state[n] = step_env[n]
    sweeps = []
    for view in data.get("views", []):
        if view.get("kind") != "plot" or view.get("x") == "time":
            continue
        x = view["x"]
        p = next(p for p in data["parameters"] if p["name"] == x)
        lo, hi = float(view.get("from", p["min"])), float(view.get("to", p["max"]))
        n = int(view.get("points", 101))
        xs = [lo + (hi - lo) * k / (n - 1) for k in range(n)]
        sweep = {"x": xs}
        for name in view["y"]:
            sweep[name] = []
        for xv in xs:
            e = {**params, x: xv}
            for q in data.get("quantities", []):
                if q["name"] not in changing:
                    e[q["name"]] = evaluate(trees[q["name"]], e)
            for name in view["y"]:
                sweep[name].append(e[name])
        sweeps.append(sweep)
    return {"values": out_values, "times": times, "series": series, "sweeps": sweeps}


__all__ = ["CONSTANTS", "ExprError", "FUNCTIONS", "MAX_NODES", "MAX_SOURCE", "evaluate", "evaluate_model",
           "js_pow", "model_problems", "names_in", "parse", "tokenize"]
