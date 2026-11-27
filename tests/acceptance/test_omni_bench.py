"""The OMNIMIND benchmark (acceptance/omni, lane Z): the framework's comparison and outcomes, the freeze of the task
definitions, every family's truths against an independent reading (tests/acceptance/omni_oracle.py), the `made`
judge against a reference maker and its deliberately flawed variants (omni_reference_maker.py), the process
isolation of hostile tasks (probe subjects), and the two subjects' honesty (today's answers; "not built")."""
import hashlib
import json
from pathlib import Path
import tempfile

import pytest

from neuroscifi.acceptance import omni
from neuroscifi.acceptance.omni import Answer, Task, cases, made, same, score_task, subject

import omni_oracle as oracle
import omni_reference_maker as reference

# The frozen identity of every family's tasks per split, and of the case tables, as committed with the baseline.
# A change here means the tasks changed: every earlier number stops being comparable. Do not update these to make a
# run pass; add new tasks in a new file instead.
CASES_SHA256 = "da1f0fb3cbd6d6b464485db7f2898bbc3b80a3ac821e1c22c6d9077b279ae26c"
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

def test_artifacts_subject_says_not_built_while_the_package_is_absent(monkeypatch):
    """The package exists now, so its absence is simulated (None in sys.modules makes the import fail), instead of a
    skip that could never run again (code review L8; fix lane F1, ruling I)."""
    import sys
    for name in [m for m in sys.modules if m == "neuroscifi.artifacts" or m.startswith("neuroscifi.artifacts.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "neuroscifi.artifacts", None)
    s = subject.ArtifactsSubject()
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
