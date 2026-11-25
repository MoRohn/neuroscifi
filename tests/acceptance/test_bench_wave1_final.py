"""scripts/bench_wave1.py final: Wave 1's one look at the withheld family. The family itself is never run here."""
import importlib.util
from pathlib import Path

import pytest

from neuroscifi.acceptance import cognition


@pytest.fixture
def bench(monkeypatch):
    spec = importlib.util.spec_from_file_location("bench_wave1", Path(__file__).resolve().parents[2] / "scripts" /
                                                  "bench_wave1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cognition.load_plugins()               # first: a module imported once registers once
    saved = dict(cognition._REGISTRY)
    yield module
    cognition._REGISTRY.clear()
    cognition._REGISTRY.update(saved)


def test_one_name_reaches_each_lanes_mechanism_without_falling_back_to_the_baseline(bench):
    bench.register_wave1()
    assert set(bench.WAVE1_FINAL) == {"anticipation", "info_value", "counterfactual", "self_model"}
    for family, (name, _) in bench.WAVE1_FINAL.items():
        mech, fell_back = cognition._resolve(family, "wave1")
        assert not fell_back and mech.factory is cognition._REGISTRY[(family, name)].factory
        assert mech.components == cognition._REGISTRY[(family, name)].components


def test_the_look_is_refused_on_a_dirty_tree_before_the_ledger_is_written(bench, monkeypatch, tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(cognition, "LEDGER", ledger)      # a test's ledger: the repository's stays untouched
    monkeypatch.setattr(cognition, "environment", lambda: {"git_commit": "0" * 40, "git_dirty": True})
    with pytest.raises(SystemExit, match="clean"):
        bench.final("a test")
    monkeypatch.setattr(cognition, "environment", lambda: {"git_commit": "0" * 40, "git_dirty": False})
    with pytest.raises(SystemExit, match="note"):
        bench.final("")
    assert not ledger.exists()
