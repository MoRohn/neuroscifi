"""The OMNIMIND benchmark (acceptance/omni, lane Z): the framework's comparison and outcomes, the freeze of the task
definitions, every family's truths against an independent reading (tests/acceptance/omni_oracle.py), the `made`
judge against a reference maker and its deliberately flawed variants (omni_reference_maker.py), the process
isolation of hostile tasks (probe subjects), and the two subjects' honesty (today's answers; "not built")."""
import hashlib
import json
import re
from pathlib import Path
import tempfile

import pytest

from neuroscifi.acceptance import omni
from neuroscifi.acceptance.omni import Answer, Task, cases, made, readers, same, score_task, subject

import omni_oracle as oracle
import omni_reference_maker as reference

# The frozen identity of every family's tasks per split, and of the case tables, as committed with the baseline.
# A change here means the tasks changed: every earlier number stops being comparable. Do not update these to make a
# run pass; add new tasks in a new file instead.
CASES_SHA256 = "da1f0fb3cbd6d6b464485db7f2898bbc3b80a3ac821e1c22c6d9077b279ae26c"
# The made-judge's readers. Pinned first by fix lane F2 when they were made stricter (review finding M1: a chart a
# person cannot see was read as carried); the file at 505b55b, never pinned, had SHA-256 6c6c28a2e91b1cbb...c239.
# A change here changes what "right" means for made and transform: re-pin only with the reason written in
# docs/quality/OMNIMIND_BENCHMARK.md, before any look that it affects.
READERS_SHA256 = "df21e65899c3830514d2ac05322f3b2ace5738aedb02a17561baf0c985596327"
FROZEN = {
    "development": {
        "intake": {"n": 337, "sha256": "54afb377ea0e01f6bda0153949bb867f32c48a7265eb333ba10948720d67b1aa"},
        "honest": {"n": 55, "sha256": "d34412d16c276237eff32346c59c811c06bdb3b68cc3d8141d8105a93aa7bbd9"},
        "unknown": {"n": 51, "sha256": "289e924e5d4534c255fa4d688a9f535e915c82a38054d7ebcda41d0d3620d6d7"},
        "compound": {"n": 36, "sha256": "d62e94fc4d355bb0503d081cd8c93047a2a0fa3ff18863387112cc64760a3af1"},
        "hostile": {"n": 23, "sha256": "25adf483319ab3fd15ff6adc9f06fb8ebf6daa9def96a2b5a504ea64334abc09"},
        "made": {"n": 14, "sha256": "d7d00304c4d3b2dc9dfce5a9a389e08b752de2ef1d5ab142c7e6d798f53dcddf"},
        "transform": {"n": 8, "sha256": "aa79bb0a676c3be36b7832bda16463414bd8c97485a2ae4ea20081075a32b8f4"},
    },
    "held_out": {
        "intake": {"n": 339, "sha256": "16e00749adc16c4923f0715a22f18e04228001ef443b2a416d2fe8ad1f268929"},
        "honest": {"n": 55, "sha256": "dea2244011afef86c6eb0b4e30baa9a0b380abd02b273a9700d29679486ec0a8"},
        "unknown": {"n": 49, "sha256": "472ea985b5eca67a430f009d66c05504f5c959ad9ab865e1cf5792e3537c9bb9"},
        "compound": {"n": 40, "sha256": "fc48a744a7febd4fa40d4e699b79281074dde4903d574980f404019462bf3d3e"},
        "hostile": {"n": 23, "sha256": "3dd144a27f44f8421bad5211d82f9bb8dfc2d5b86202c079a55abb48d140f6a2"},
        "made": {"n": 14, "sha256": "f3fdfccfcf9ec7b07bd7c719a1156bd57130adfca2548e362b6039ac9d9b8b61"},
        "transform": {"n": 8, "sha256": "6d415d1e2ae4bbcde21d4c35ccad2015782962f739a4d881e07fd611c9642686"},
    },
}


# ---- comparison and outcomes ----------------------------------------------------------------------------------------

def test_same_by_hand():
    assert same(3, 3.0) and same(3.0, "3.0") and not same(3.0, "three")
    assert same(1.0, 1.009, {"rel": 0.01}) and not same(1.0, 1.02, {"rel": 0.01})
    assert same(10.0, 10.4, {"abs": 0.5}) and not same(10.0, 10.6, {"abs": 0.5})
    assert same(True, True) and not same(True, 1) and not same(False, None)
    assert same("a  b\nc", "a b c") and not same("ab", "a b")
    assert same([1, "x"], [1.0, "x"]) and not same([1, 2], [2, 1]) and same([1, 2], [2, 1], {"set": True})
    assert same(["png", "apng"], "apng", {"one_of": True}) and not same(["png"], "jpeg", {"one_of": True})
    assert same(None, None) and not same(None, 0)
    assert same({"a": [1, 2]}, {"a": [1.0, 2.0]}) and not same({"a": 1}, {"a": 1, "b": 2})


def _task(kind, expect=1, tol=None):
    return Task("t", "intake", {"f": b""}, {}, "table.n_rows", expect, tol, kind)


@pytest.mark.parametrize("kind,answer,outcome", [
    ("measure", Answer(1, "answered"), "right"), ("measure", Answer(2, "answered"), "wrong"),
    ("measure", Answer(None, "cannot"), "abstained"), ("measure", Answer(None, "refused"), "abstained"),
    ("cannot", Answer(None, "cannot"), "right"), ("cannot", Answer(None, "refused"), "right"),
    ("cannot", Answer(1, "answered"), "wrong"),
    ("cannot_or_value", Answer(None, "cannot"), "right"), ("cannot_or_value", Answer(1, "answered"), "right"),
    ("cannot_or_value", Answer(5, "answered"), "wrong"),
    ("refuse", Answer(None, "refused"), "right"), ("refuse", Answer(None, "cannot"), "abstained"),
    ("refuse", Answer(1, "answered"), "wrong")])
def test_outcomes(kind, answer, outcome):
    assert score_task(_task(kind), answer).outcome == outcome


def test_answer_status_is_checked():
    with pytest.raises(ValueError):
        Answer(1, "maybe")


def test_crash_and_hang_are_results():
    class Crash:
        name = "crash"

        def answer(self, task):
            raise KeyError("x")

    class Slow:
        name = "slow"

        def answer(self, task):
            while True:
                pass

    rows = omni.run_tasks("intake", "development", Crash(), [_task("measure")])
    assert rows[0][1].outcome == "failed" and rows[0][1].detail["failure"] == "crash"
    rows = omni.run_tasks("intake", "development", Slow(), [_task("measure")], seconds=0.3)
    assert rows[0][1].outcome == "failed" and rows[0][1].detail["failure"] == "hang"


def test_summary_counts_and_fabrication_is_separate():
    tasks = [_task("measure"), _task("measure"), _task("cannot")]
    rows = [(tasks[0], omni.Result("right")), (tasks[1], omni.Result("wrong")), (tasks[2], omni.Result("right"))]

    class S:
        name = "s"
    out = omni.summarise("intake", "development", S(), rows, 0.0)
    assert (out["n"], out["right"], out["wrong"], out["fabrication"], out["score"]) == (3, 2, 1, 1, 0.6667)


# ---- the freeze -----------------------------------------------------------------------------------------------------

def test_splits_are_disjoint_and_seeded_from_their_own_ranges():
    for name in omni.FAMILIES:
        table = getattr(cases, name.upper())
        dev, held = table["development"], table["held_out"]
        assert all(c["seed"] in omni.DEV_SEEDS for c in dev), name
        assert all(c["seed"] in omni.HELD_OUT_SEEDS for c in held), name
        assert not {c["name"] for c in dev} & {c["name"] for c in held}, name
    assert not set(omni.DEV_SEEDS) & set(omni.WITHHELD_SEEDS) and not set(omni.HELD_OUT_SEEDS) & set(
        omni.WITHHELD_SEEDS)


def test_tasks_are_deterministic():
    for name in ("intake", "made", "honest", "unknown"):
        fam = omni.family(name)
        a = [t.digest() for t in fam.tasks("development")]
        b = [t.digest() for t in fam.tasks("development")]
        assert a == b, name


def test_task_definitions_are_frozen():
    assert hashlib.sha256(Path(cases.__file__).read_bytes()).hexdigest() == CASES_SHA256
    assert hashlib.sha256(Path(readers.__file__).read_bytes()).hexdigest() == READERS_SHA256
    for split in omni.SPLITS:
        assert omni.digests(split) == FROZEN[split], split


def test_nothing_in_the_benchmark_imports_the_product_except_the_driver():
    import ast
    root = Path(omni.__file__).parent
    for path in root.glob("*.py"):
        if path.name == "subject.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert node.level <= 1, (path.name, node.module)            # nothing above acceptance/omni
                assert not (node.module or "").startswith("neuroscifi"), (path.name, node.module)
            elif isinstance(node, ast.Import):
                assert not any(a.name.startswith("neuroscifi") for a in node.names), path.name


DRIVER_FILES = ("src/neuroscifi/acceptance/omni/subject.py", "scripts/omni_forms_driver.py")
_ID_LIKE = re.compile(r"(intake|honest|unknown|compound|hostile|made|transform|forms|cross|learn)/"
                      r"(development|held_out|withheld)\b")


def _case_names() -> set[str]:
    """Every case name and id in the frozen development and held-out tables (the withheld files are never read)."""
    from neuroscifi.acceptance.omni import cross_cases, forms_cases, learn_people
    found: set[str] = set()

    def walk(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k in ("name", "id") and isinstance(v, str) and len(v) >= 4:
                    found.add(v)
                walk(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v)

    for module in (cases, forms_cases, cross_cases, learn_people):
        for name, value in vars(module).items():
            if not name.startswith("_"):
                walk(value)
    return found


def driver_findings(source: str, names: set[str]) -> list[str]:
    """What a benchmark driver may not do (review finding B4): the drivers may import the product, but they only
    TRANSLATE its answers. They may not import `make` internals or a private product name, call scan_injection,
    decode with limits of their own, choose an outcome by matching an error's words, turn a partial decode into an
    outcome themselves, or name a task, a case or a split's task path."""
    import ast
    out = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = node.module or "" if isinstance(node, ast.ImportFrom) else ""
            for alias in node.names:
                full = f"{module}.{alias.name}" if module else alias.name
                if "artifacts.make." in full or (module.endswith("artifacts.make") and alias.name.startswith("_")):
                    out.append(f"imports a make internal: {full}")
                if "artifacts" in module and alias.name.startswith("_"):
                    out.append(f"imports a private product name: {full}")
                if alias.name == "scan_injection":
                    out.append("imports scan_injection")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if v.startswith("neuroscifi.artifacts.make.") or v.startswith("neuroscifi.artifacts._"):
                out.append(f"loads a product internal: {v}")
            if _ID_LIKE.search(v) or v in names:
                out.append(f"names a task or case: {v[:60]!r}")
        elif isinstance(node, (ast.Name, ast.Attribute)):
            if (node.id if isinstance(node, ast.Name) else node.attr) == "scan_injection":
                out.append("calls scan_injection")
        if isinstance(node, ast.Call):
            func = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if func == "Limits" and (node.args or node.keywords):
                out.append("builds Limits of its own")
            if func == "decode" and any(k.arg == "limits" for k in node.keywords):
                out.append("decodes with limits of its own")
        if isinstance(node, ast.Compare):
            sides = [node.left, *node.comparators]
            words = any(isinstance(s, ast.Constant) and isinstance(s.value, str) or isinstance(s, (ast.Tuple, ast.List,
                        ast.Set)) and s.elts and all(isinstance(e, ast.Constant) for e in s.elts) for s in sides)
            reason = any(isinstance(s, ast.Name) and s.id in ("reason", "message")
                         or isinstance(s, ast.Attribute) and s.attr in ("reason", "message", "args")
                         or isinstance(s, ast.Call) and getattr(s.func, "id", "") == "str" for s in sides)
            if words and reason:
                out.append(f"chooses by an error's words (line {node.lineno})")
        if isinstance(node, ast.If) and any(isinstance(n, ast.Attribute) and n.attr == "partial"
                                            for n in ast.walk(node.test)):
            if any(isinstance(n, ast.Constant) and n.value in ("refused", "cannot") for b in node.body
                   for n in ast.walk(b)):
                out.append(f"turns a partial decode into an outcome (line {node.lineno})")
    return out


def test_driver_rules_catch_each_way_of_deciding():
    names = {"line-one-series"}
    bad = {
        "from neuroscifi.artifacts.make import _g1": "make internal",
        "import neuroscifi.artifacts.make.svg": "make internal",
        "m = importlib.import_module('neuroscifi.artifacts.make._g2')": "product internal",
        "from neuroscifi.webcortex.safety import scan_injection": "scan_injection",
        "x = safety.scan_injection(text)": "scan_injection",
        "d = adapters.decode(b, limits=types.Limits(max_members=9))": "limits of its own",
        "status = 'cannot' if reason in ('unsupported',) else 'refused'": "error's words",
        "if 'bomb' in str(error): pass": "error's words",
        "if decoded.partial:\n    return Answer(None, 'refused', 'partial')": "partial decode",
        "if task.id == 'made/development/line-one-series': pass": "names a task",
        "if case == 'line-one-series': pass": "names a task",
    }
    for source, expected in bad.items():
        assert any(expected in f for f in driver_findings(source, names)), source
    fine = ("from neuroscifi.artifacts import adapters, make, outcome\n"
            "a = outcome.answer(adapters.decode(b, name=n, declared=d))\n"
            "made = make.make(spec, types.Limits())\nif split != 'development': raise SystemExit(1)")
    assert driver_findings(fine, names) == []


@pytest.mark.xfail(strict=True, reason="waits for lane F1's driver: today's subject.py calls scan_injection, maps "
                                       "error reasons and partial decodes to outcomes itself (review B4); the main "
                                       "session removes this mark at merge")
def test_the_drivers_only_translate():
    names = _case_names()
    root = Path(omni.__file__).resolve().parents[4]
    findings = {rel: driver_findings((root / rel).read_text(encoding="utf-8"), names) for rel in DRIVER_FILES}
    assert findings == {rel: [] for rel in DRIVER_FILES}


# ---- reserved seed ranges (review finding B3) -----------------------------------------------------------------------

# Calls whose argument at this position (or keyword `seed`) is a seed; `P` is cross_cases.P(pid, kind, seed, ...).
_SEED_ARG = {"rng_for": 0, "default_rng": 0, "Random": 0, "RandomState": 0, "seed": 0, "P": 2}
# The case tables themselves own their seeds; the withheld machinery is never read here.
_TABLES = {"cases.py", "forms_cases.py", "cross_cases.py", "learn_people.py"}


def _int_values(node, known: dict) -> frozenset[int] | None:
    import ast
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return frozenset([node.value])
    if isinstance(node, ast.Name):
        return known.get(node.id)
    if isinstance(node, (ast.Tuple, ast.List)) and all(isinstance(e, ast.Constant) and type(e.value) is int
                                                         for e in node.elts):
        return frozenset(e.value for e in node.elts)
    if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "range" and node.args
            and all(isinstance(a, ast.Constant) and type(a.value) is int for a in node.args)):
        r = range(*[a.value for a in node.args])
        return frozenset(r) if 0 < len(r) <= 100_000 else None
    return None


def seed_intervals(source: str) -> list[tuple[int, int, int]]:
    """(line, lowest, highest) of every seed expression whose values can be worked out from the source: integer
    arithmetic over loop variables bound by `for x in range(..)`, a literal tuple or a name bound so, and parameters
    whose default is an integer or a tuple of integers. The values are enumerated exactly (a seed that is reserved
    only between two values it never takes is not reported). A seed that depends on anything else (a case's own
    seed) is not reported."""
    import ast
    import itertools
    tree = ast.parse(source)
    known: dict[str, frozenset[int]] = {}
    for _ in range(2):                                   # defaults first, then loops over them
        for node in ast.walk(tree):
            if isinstance(node, (ast.For, ast.comprehension)) and isinstance(node.target, ast.Name):
                values = _int_values(node.iter, known)
                if values:
                    known[node.target.id] = values
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args.args[len(node.args.args) - len(node.args.defaults):]
                for arg, default in zip(args, node.args.defaults):
                    values = _int_values(default, {})
                    if values:
                        known[arg.arg] = values
            elif (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                  and isinstance(node.value, ast.Constant) and type(node.value.value) is int):
                known[node.targets[0].id] = frozenset([node.value.value])

    def values(node):
        if isinstance(node, (ast.Constant, ast.Name)):
            return _int_values(node, known)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            inner = values(node.operand)
            return frozenset(-v for v in inner) if inner else None
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
            a, b = values(node.left), values(node.right)
            if not a or not b or len(a) * len(b) > 1_000_000:
                return None
            op = {ast.Add: int.__add__, ast.Sub: int.__sub__, ast.Mult: int.__mul__}[type(node.op)]
            return frozenset(op(x, y) for x, y in itertools.product(a, b))
        return None

    out = []
    for node in ast.walk(tree):
        exprs = []
        if isinstance(node, ast.Call):
            func = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if func in _SEED_ARG and len(node.args) > _SEED_ARG[func]:
                exprs.append(node.args[_SEED_ARG[func]])
            exprs += [k.value for k in node.keywords if k.arg == "seed"]
        elif isinstance(node, ast.Dict):
            exprs += [v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant) and k.value == "seed"]
        for expr in exprs:
            got = values(expr)
            if got:
                for name, r in (("HELD_OUT_SEEDS", omni.HELD_OUT_SEEDS), ("WITHHELD_SEEDS", omni.WITHHELD_SEEDS)):
                    inside = sorted(v for v in got if v in r)
                    if inside:
                        out.append((expr.lineno, inside[0], inside[-1], name))
                if not any(v in omni.HELD_OUT_SEEDS or v in omni.WITHHELD_SEEDS for v in got):
                    out.append((expr.lineno, min(got), max(got), None))
    return out


def test_seed_intervals_are_worked_out_from_the_source():
    src = ("def sweep(seeds=(1, 2, 3)):\n    for seed in seeds:\n        for k in range(30):\n"
           "            P('s', 'kind', 9000 + 100 * seed + k, 'q', 'csv')\n"
           "rng = np.random.default_rng(4100)\nr = random.Random(seed=7)\nx = {'seed': 3599}\n"
           "y = rng_for(case['seed'])\n")
    got = sorted(seed_intervals(src))
    assert got == [(4, 9100, 9329, "WITHHELD_SEEDS"), (5, 4100, 4100, None), (6, 7, 7, None), (7, 3599, 3599, None)]
    spaced = "for n in (100, 1000, 10000, 50000):\n    r = random.Random(n)\n"     # never inside: not reported
    assert seed_intervals(spaced) == [(2, 100, 50000, None)]


def test_no_script_or_test_draws_a_seed_from_a_reserved_range():
    """The held-out and withheld seed ranges belong to their case tables (development seeds are for tuning). Lane R's
    sweep used 9100-9329 before fix lane F2 moved it to LANE_R_SWEEP_SEEDS. Scope: OMNIMIND's own scripts and tests
    (a seed elsewhere, such as another programme's test of its own, never reaches these generators)."""
    root = Path(omni.__file__).resolve().parents[4]
    files = [*(root / "scripts").glob("*omni*.py"), *(root / "tests" / "acceptance").glob("*omni*.py"),
             *(root / "tests" / "artifacts").rglob("*.py")]
    files += [p for p in Path(omni.__file__).parent.glob("*.py") if p.name not in _TABLES]
    found, checked = [], 0
    for path in files:
        if "withheld" in path.name or path.name == "final.py":          # the withheld machinery is never read here
            continue
        for line, lo, hi, reserved in seed_intervals(path.read_text(encoding="utf-8")):
            checked += 1
            if reserved:
                found.append(f"{path.relative_to(root)}:{line} seeds {lo}-{hi} in {reserved}")
    assert checked > 10 and found == []
    lane_r = seed_intervals((root / "scripts" / "omnimind_r_evidence.py").read_text(encoding="utf-8"))
    sweep = [(lo, hi) for _, lo, hi, _ in lane_r if lo >= 1000]
    assert len(sweep) == 6 and all(omni.LANE_R_SWEEP_SEEDS.start <= lo and hi < omni.LANE_R_SWEEP_SEEDS.stop
                                   for lo, hi in sweep)


# ---- truths against an independent reading --------------------------------------------------------------------------

def _oracle_check(family_name):
    checked, bad = 0, []
    for split in omni.SPLITS:
        for t in omni.family(family_name).tasks(split):
            if t.kind not in ("measure", "cannot_or_value") or len(t.files) != 1:
                continue
            (name, data), = t.files.items()
            try:
                value = oracle.answer(name, data, t.question)
            except oracle.NotOracle:
                continue
            checked += 1
            if not same(t.expect, value, t.tolerance):
                bad.append((t.id, t.expect, value))
    return checked, bad


def test_intake_truths_agree_with_an_independent_reading():
    checked, bad = _oracle_check("intake")
    assert checked >= 480 and bad == []


# ---- made: the judge against a reference maker and its flaws -----------------------------------------------------------

def test_reference_maker_is_right_everywhere():
    for split in omni.SPLITS:
        out = made.score(reference.ReferenceMaker(), split)
        assert out["right"] == out["n"] and out["carried_mean"] == 1.0, out["failures"]


@pytest.mark.parametrize("flaw,outcome,forms", [
    ("skew", "wrong", ("line", "bar", "scatter", "histogram", "timeline", "diagram", "surface")),
    ("pitch", "wrong", ("tone", "sonify")), ("delay", "wrong", ("animation",)), ("cell", "wrong", ("data_file",)),
    ("invert", "failed", ("mesh",)), ("hue", "failed", ("bar", "scatter", "animation")),
    ("script", "failed", ("line", "bar", "scatter", "histogram", "timeline", "diagram")),
    ("random_id", "failed", ("line", "bar", "diagram"))])
def test_each_flaw_is_caught(flaw, outcome, forms):
    out = made.score(reference.ReferenceMaker(flaw), "development")
    by_form = {}
    for task in made.tasks("development"):
        by_form.setdefault(task.question.split(":")[1], []).append(out["tasks"][task.id])
    for form in forms:
        assert outcome in by_form[form], (flaw, form, by_form[form])


SVG_FORMS = ("line", "bar", "scatter", "histogram", "timeline", "diagram")


def _hide(data: bytes, how: str) -> bytes:
    """The reference maker's picture with its plot (a chart) or all of it (a diagram) made invisible to a person."""
    s = data.decode()
    attrs = {"display none": ' style="display:none"', "visibility hidden": ' visibility="hidden"',
             "opacity 0": ' opacity="0"', "zero size": ' transform="scale(0)"',
             "off the canvas": ' transform="translate(5000,0)"', "clipped away": ' clip-path="url(#hide)"'}
    clip = '<clipPath id="hide"><rect width="1" height="1"/></clipPath>'
    if how == "background colour":
        return s.replace("</svg>", "<style>.plot *, .node *, .edge { fill: #fff; stroke: #fff }</style></svg>").encode()
    if how == "covered":
        return s.replace("</svg>", '<rect x="-10" y="-10" width="2000" height="2000" fill="#fff"/></svg>').encode()
    if 'class="plot"' in s:
        return s.replace('<g class="plot"', clip + '<g class="plot"' + attrs[how], 1).encode()
    head, _, body = s.partition("</style>")
    return (head + "</style>" + clip + f"<g{attrs[how]}>" + body.replace("</svg>", "</g></svg>")).encode()


class _Hiding(reference.ReferenceMaker):
    def __init__(self, how: str):
        super().__init__()
        self.how = how

    def answer(self, task):
        a = super().answer(task)
        if a.status == "answered" and a.value["media_type"] == "image/svg+xml":
            a = omni.Answer(dict(a.value, bytes=_hide(a.value["bytes"], self.how)), "answered")
        return a


@pytest.mark.parametrize("how", ("display none", "visibility hidden", "opacity 0", "zero size", "off the canvas",
                                 "clipped away", "background colour", "covered"))
def test_a_made_picture_a_person_cannot_see_is_not_right(how):
    """Review M1: the judge reads what a person would see; the same artifacts, visible, are right (test above)."""
    out = made.score(_Hiding(how), "development")
    for task in made.tasks("development"):
        if task.question.split(":")[1] in SVG_FORMS:
            assert out["tasks"][task.id] in ("wrong", "failed"), (how, task.id, out["tasks"][task.id])


def test_a_maker_that_answers_nothing_useful():
    class Empty:
        name = "empty"

        def answer(self, task):
            return Answer({"bytes": b"<svg/>", "media_type": "image/svg+xml"}, "answered")
    out = made.score(Empty(), "development")
    assert out["right"] == 0 and out["failed"] + out["wrong"] == out["n"]


# ---- hostile: isolation ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("behaviour,outcome,failure", [
    ("refuse", "right", None), ("answer", "wrong", None), ("crash", "failed", "crash"),
    ("allocate", "failed", "memory_bound"), ("escape", "failed", "escape"), ("hang", "failed", "hang")])
def test_isolation_catches_every_failure(behaviour, outcome, failure):
    from neuroscifi.acceptance.omni import hostile, isolate
    task = hostile.tasks("development")[0]
    with tempfile.TemporaryDirectory() as root:
        report = isolate.run_isolated(task, f"probe:{behaviour}", Path(root), seconds=6,
                                      escape_paths=["../../omnimind-probe-escape.txt"])
        result = hostile.judge(task, report)
        assert result.outcome == outcome
        assert result.detail.get("failure") == failure
        assert not (Path(root) / "omnimind-probe-escape.txt").exists()


def test_hostile_files_are_small_on_disk_and_large_when_expanded():
    from neuroscifi.acceptance.omni import hostile
    by = {t.id.split("/")[-1]: t for t in hostile.tasks("development")}
    bomb = by["zip-bomb-1gib"].files["bomb.zip"]
    assert len(bomb) < 2 << 20
    import io
    import zipfile
    info = zipfile.ZipFile(io.BytesIO(bomb)).getinfo("data.csv")
    assert info.file_size == 1 << 30
    with zipfile.ZipFile(io.BytesIO(bomb)).open("data.csv") as f:
        assert f.read(1 << 20) == b"0" * (1 << 20)


# ---- the subjects -------------------------------------------------------------------------------------------------

def test_artifacts_subject_says_not_built_while_the_package_is_absent():
    s = subject.ArtifactsSubject()
    try:
        import neuroscifi.artifacts  # noqa: F401
        pytest.skip("the artifacts package exists now")
    except ImportError:
        pass
    a = s.answer(omni.family("intake").tasks("development")[0])
    assert a.status == "cannot" and a.reason.startswith("not built")


def test_today_answers_only_what_main_can_and_never_fabricates_on_intake():
    out = omni.family("intake").score(subject.get("today"), "development")
    assert out["wrong"] == 0 and out["failed"] == 0 and 0 < out["right"] < out["n"]
    answered = {k for k, v in out["tasks"].items() if v == "right"}
    assert all("/doc." in k or "array." in k for k in answered)


def test_measures_vocabulary_covers_every_question():
    names = {m.split(":")[0] for m in subject.MEASURES}
    for name in ("intake", "honest", "unknown", "compound", "hostile"):
        for split in omni.SPLITS:
            for t in omni.family(name).tasks(split):
                _, measure, _ = subject.split_question(t.question)
                assert measure in names, (t.id, measure)


def test_baseline_file_is_the_today_subject_on_both_splits():
    path = Path(omni.__file__).resolve().parents[4] / "docs" / "quality" / "evidence" / "omnimind_baseline.json"
    if not path.exists():
        pytest.skip("baseline not recorded yet")
    data = json.loads(path.read_text())
    assert data["subject"] == "today" and set(data["splits"]) == set(omni.SPLITS)
    for split in omni.SPLITS:
        assert set(data["splits"][split]["families"]) == set(omni.FAMILIES)
