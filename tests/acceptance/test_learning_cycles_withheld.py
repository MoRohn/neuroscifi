"""Wave 3 step 0: the guard of the withheld learning-cycles worlds (acceptance/cognition/cycles_withheld.py). No test
here loads, generates or scores those worlds: the loader (cycles._withheld) is replaced wherever a path could reach
it, and the file is only hashed."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from neuroscifi.acceptance import cognition
from neuroscifi.acceptance.cognition import cycles, cycles_splits, integrated_splits, knowledge_splits, splits
from neuroscifi.webcortex import config as wconfig

ROOT = Path(__file__).resolve().parents[2]
DEV = cycles_splits.CYCLES["development"]["worlds"][0]
# The withheld worlds' seeds as committed (26409bc); the pinned digest below freezes them with the rest of the file.
WITHHELD_CYCLE_SEEDS = (7341, 7342, 7343, 7344)
# The cognitive benchmark's withheld family's seeds as its documents state them (that file is not imported here).
WITHHELD_FAMILY_SEEDS = (9001, 9002, 9003)


def test_the_withheld_worlds_are_frozen():
    """Committed alone (26409bc) before this guard and before any Wave 3 mechanism; a change is a new file, new seeds
    and a new look, never an edit."""
    digest = hashlib.sha256((ROOT / "src/neuroscifi/acceptance/cognition/cycles_withheld.py").read_bytes()).hexdigest()
    assert digest == "0a4831aa07e30cd31a2f4e3270e59ef20338ed2ebb229b22c77f1448628ebc0d"


def test_the_withheld_seeds_are_disjoint_from_every_split_and_benchmark_seed():
    every = set(cognition.WIDE_SEEDS) | set(WITHHELD_FAMILY_SEEDS)
    for module in (splits, knowledge_splits, integrated_splits):
        for name in dir(module):
            value = getattr(module, name)
            if isinstance(value, dict):
                every |= {s for part in value.values() if isinstance(part, dict) for s in part.get("seeds", ())}
    every |= {s for part in cycles_splits.CYCLES.values() for s in part["seeds"]}
    assert {41, 141, 101, 601, 1011} <= every          # the collection above reached each kind of split
    assert len(WITHHELD_CYCLE_SEEDS) >= 4 and not set(WITHHELD_CYCLE_SEEDS) & every


@pytest.fixture
def sealed(monkeypatch, tmp_path):
    """The worlds' loader fails if reached; the ledger is a test's own."""
    def loader():
        raise AssertionError("the withheld worlds were loaded")
    monkeypatch.setattr(cycles, "_withheld", loader)
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(cognition, "LEDGER", ledger)
    return ledger


@pytest.fixture
def bench():
    spec = importlib.util.spec_from_file_location("bench_learning_cycles", ROOT / "scripts" /
                                                  "bench_learning_cycles.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_development_paths_refuse_the_withheld_split(sealed, bench):
    with pytest.raises(cognition.Refused, match="only by cycles.final"):
        cycles.run_split(cycles.WITHHELD, cycles=1)
    with pytest.raises(cognition.Refused, match="only by cycles.final"):
        cycles.run_references(cycles.WITHHELD)
    with pytest.raises(SystemExit):                     # not a choice of `run --split`
        bench.main(["run", "--split", "withheld", "--cycles", "1"])
    assert not sealed.exists()


def test_final_is_refused_without_a_note_and_on_a_dirty_or_uncommitted_tree_before_the_ledger(sealed, monkeypatch):
    monkeypatch.setattr(cognition, "environment", lambda: {"git_commit": "0" * 40, "git_dirty": True})
    with pytest.raises(cognition.Refused, match="clean"):
        cycles.final("a test")
    monkeypatch.setattr(cognition, "environment", lambda: {"git_commit": None, "git_dirty": None})
    with pytest.raises(cognition.Refused, match="committed"):
        cycles.final("a test")
    monkeypatch.setattr(cognition, "environment", lambda: {"git_commit": "0" * 40, "git_dirty": False})
    with pytest.raises(cognition.Refused, match="note"):
        cycles.final("")
    assert not sealed.exists()


def test_the_script_refuses_an_output_in_the_tree_another_source_and_a_dirty_tree(sealed, bench, monkeypatch,
                                                                                 tmp_path):
    out = tmp_path / "out.json"
    inside = ROOT / "docs" / "quality" / "evidence" / "a-test-output.json"
    with pytest.raises(SystemExit, match="outside this checkout"):
        bench.main(["final", "--note", "a test", "--out", str(inside)])
    monkeypatch.setattr(cognition, "REPO", tmp_path)
    with pytest.raises(SystemExit, match="own source"):
        bench.main(["final", "--note", "a test", "--out", str(out)])
    monkeypatch.setattr(cognition, "REPO", ROOT)
    monkeypatch.setattr(cognition, "environment", lambda: {"git_commit": "0" * 40, "git_dirty": True})
    with pytest.raises(SystemExit, match="clean"):
        bench.main(["final", "--note", "a test", "--out", str(out)])
    with pytest.raises(SystemExit):                     # --note is required
        bench.main(["final", "--out", str(out)])
    assert not sealed.exists() and not out.exists() and not inside.exists()


def test_a_final_run_records_its_line_before_it_loads_or_scores_anything(monkeypatch, tmp_path):
    """The look counts even when it crashes: the line is in the ledger when the worlds are first reached. A stand-in
    for the loader is used; the withheld worlds are not."""
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(cognition, "LEDGER", ledger)
    monkeypatch.setattr(cognition, "environment", lambda: {"git_commit": "1" * 40, "git_dirty": False})
    seen = []

    def loader():
        seen.append([json.loads(line) for line in ledger.read_text().splitlines()])
        raise RuntimeError("stand-in: nothing is scored")
    monkeypatch.setattr(cycles, "_withheld", loader)
    with pytest.raises(RuntimeError, match="stand-in"):
        cycles.final("a test look")
    [[entry]] = seen
    assert entry["commit"] == "1" * 40 and entry["dirty"] is False and entry["note"] == "a test look"
    assert entry["mechanism"] == "baseline" and entry["ablate"] == []
    assert entry["families"] == [cycles.FINAL_FAMILY] and "withheld" not in entry["families"]
    assert entry["configurations"] == sorted(cycles.FINAL_CONFIGS)


def test_shipped_forces_only_what_the_run_needs_and_each_run_reads_the_settings_back():
    assert set(cycles._settings(frozenset({cycles.SHIPPED}), 2)) == {"enabled", "autonomous", "daily_autonomous"}
    defaults = wconfig.WebConfig()
    shipped = cycles.run_world(DEV, 41, frozenset({cycles.SHIPPED}), cycles=1)["config"]
    baseline = cycles.run_world(DEV, 41, frozenset(), cycles=1)["config"]
    for key in cycles.READ_BACK:
        if key not in ("enabled", "autonomous"):
            assert shipped[key] == getattr(defaults, key), key
    assert {k: baseline[k] for k in cycles.WAVE1_OFF_WEB} == cycles.WAVE1_OFF_WEB
    assert shipped["autonomous"] is baseline["autonomous"] is True
    assert shipped["hypothesis_fields"] is True and baseline["hypothesis_fields"] is False


def test_a_budget_schedule_sets_each_cycles_allowance_in_the_run_and_in_the_references():
    spec = dict(DEV, budget_schedule=(2, 1, 3, 2), cycles=3)
    assert cycles.run_world(spec, 41, frozenset(), cycles=3)["allowance"] == [1, 3, 2]
    assert cycles.run_world(DEV, 41, frozenset(), cycles=2)["allowance"] == [DEV["budget"]] * 2
    none = dict(DEV, budget_schedule=(0,) * (DEV["cycles"] + 1))
    series = cycles.references(none, 41)["series"]
    assert series["oracle"] == series["round_robin_perfect"] == series["snapshot"]     # nothing refreshed
    assert cycles.references(DEV, 41) == cycles.references(dict(DEV, budget_schedule=None), 41)


def test_a_world_digest_names_the_world_a_run_met():
    one, two = cycles_splits.generate(DEV, 41), cycles_splits.generate(DEV, 42)
    assert cycles.world_digest(one) == cycles.world_digest(cycles_splits.generate(DEV, 41))
    assert cycles.world_digest(one) != cycles.world_digest(two)
