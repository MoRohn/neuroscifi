"""Wave 3 lane V (ADR-062): the worker's idle investigation (autonomy.idle) in the learning-cycles harness, inside the
same daily allowance as the timer, and its verdict rule."""
from collections import Counter
import functools
import importlib.util
import json
from pathlib import Path

from neuroscifi.acceptance.cognition import cycles, cycles_rate_splits as rate, cycles_splits

DEV = cycles_splits.CYCLES["development"]["worlds"][0]
CONFIGS = cycles.idle_configurations()


def _looks_per_cycle(row):
    return Counter(lk["cycle"] for lk in row["looks"])


def test_the_timer_and_the_idle_investigation_together_never_pass_the_days_allowance():
    for name in ("idle", "idle-first"):
        row = cycles.run_world(DEV, 41, CONFIGS[name], cycles=5)
        per_cycle = _looks_per_cycle(row)
        assert all(per_cycle[c] <= cycles.budget_at(DEV, c) for c in range(1, 6)), (name, per_cycle)
        assert row["allowance"] == [cycles.budget_at(DEV, c) for c in range(1, 6)]   # what the references are given
        assert row["activity"]["idle_tasks"] == row["tasks"]["idle"] == sum(1 for lk in row["looks"]
                                                                              if lk["kind"] == "idle")
    first = cycles.run_world(DEV, 41, CONFIGS["idle-first"], cycles=5)
    assert first["tasks"]["idle"] > 0 and first["activity"]["idle_stopped_by_allowance"] > 0


def test_the_idle_investigation_reaches_subjects_that_hold_nothing_through_the_one_admission_rule():
    row = cycles.run_world(DEV, 41, CONFIGS["idle-first"], cycles=5)
    idle = [lk for lk in row["looks"] if lk["kind"] == "idle"]
    assert idle and all(lk["candidate"] for lk in idle)
    assert row["activity"]["idle_uncovered_looks"] == sum(1 for lk in idle if lk["before"] is None) > 0
    assert row["config"]["autonomy_enabled"] is True and row["config"]["idle_investigation"] == "before the pass"
    assert row["config"]["autonomy.cooldown"] == 86400.0 and row["config"]["autonomy.min_gain"] == 0.05


def test_without_the_idle_marker_nothing_changes_and_without_the_owners_enable_it_does_nothing():
    base = cycles.run_world(DEV, 41, CONFIGS["baseline"], cycles=4)
    assert base["tasks"]["idle"] == 0 and base["activity"]["idle_decisions"] == 0
    assert base["config"]["idle_investigation"] == "not run"
    off = cycles.run_world(DEV, 41, frozenset({cycles.NO_LOOP, cycles.IDLE}), cycles=4)
    assert off["activity"]["idle_actions"] == {"off": 4} and off["tasks"]["idle"] == 0


def test_the_idle_verdict_pairs_each_configuration_with_the_same_one_without_it():
    configs = {k: CONFIGS[k] for k in ("baseline", "idle", "idle-first")}
    result = cycles.run_split("rate:development", configs=configs, cycles=3, seeds=(2601,))
    verdicts = cycles.idle_verdicts(result)
    assert set(verdicts) == {"idle", "idle-first"} and verdicts["idle"]["against"] == "baseline"
    world = rate.CYCLES["development"]["worlds"][0]["name"]
    entry = verdicts["idle-first"]["per_world"][world]
    rows = result["configurations"]
    assert entry["paired"] == [rows["idle-first"]["worlds"][world]["per_seed"][0][cycles.PRIMARY]
                               - rows["baseline"]["worlds"][world]["per_seed"][0][cycles.PRIMARY]]
    assert set(entry["safety"]) == set(cycles.SAFETY) and verdicts["idle-first"]["verdict"] in (
        "gain", "revise", "no evidence", "not exercised")


def test_the_one_command_rerun_writes_runs_references_verdicts_and_safety_sums(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("bench_learning_cycles", root / "scripts" / "bench_learning_cycles.py")
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    monkeypatch.setattr(cycles, "run_split", functools.partial(cycles.run_split, cycles=2, seeds=(41,)))
    out = tmp_path / "idle.json"
    assert bench.main(["idle", "--split", "development", "--out", str(out)]) == 0
    value = json.loads(out.read_text())
    assert set(value["runs"]) == set(value["verdicts"]) == set(value["safety_all_runs"]) == {"development"}
    assert set(value["runs"]["development"]["configurations"]) == set(CONFIGS)
    assert "references" in value["runs"]["development"] and value["safety_all_runs"]["development"]["runs"] == 12
