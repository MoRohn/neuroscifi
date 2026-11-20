"""Simulated acceptance scenarios at reduced sizes: each must pass and show the relationships it claims."""
import argparse
import json

import pytest

from neuroscifi.acceptance import scenarios
from neuroscifi.acceptance.cli import register


def _common(result, name):
    assert result["name"] == name and result["simulated"] is True
    assert set(result) >= {"name", "simulated", "pass", "metrics", "thresholds", "notes", "checks"}
    failed = [k for k, v in result["checks"].items() if not v]
    assert result["pass"] is True, failed
    json.dumps(result, allow_nan=False)


def test_birth_publishes_record_and_zero_episode_baseline():
    result = scenarios.birth()
    _common(result, "birth")
    m = result["metrics"]
    assert m["personal_episodes"] == 0 and m["initial_graph"]["context"] == "founding"
    assert m["baseline"]["mechanism_p_inverted"] == 0.5 and m["baseline"]["decision"] == "observe"
    assert m["baseline"]["founding_graph_probe_mse"] < m["baseline"]["zero_predictor_probe_mse"]


@pytest.fixture(scope="module")
def grounding():
    return scenarios.grounding(lifetimes=40)


def test_grounding_meets_brief_thresholds(grounding):
    _common(grounding, "grounding")
    m, t = grounding["metrics"], grounding["thresholds"]
    assert m["lifetimes"] == 40 and m["changed_lifetimes"] == 20
    assert all(m["changed_factor_counts"][f] > 0 for f in ("mechanism", "camera", "partner"))
    cw = m["confident_wrong"]
    assert cw["pooled"] > 0 and cw["detecting"] < cw["pooled"]
    assert cw["relative_reduction"] >= t["confident_wrong_relative_reduction_min"]
    assert m["unchanged_coverage_detecting"] >= t["unchanged_coverage_min"]
    assert m["unchanged_task_loss_points"] <= t["unchanged_task_loss_points_max"]
    assert m["detection"]["identified_or_asked_rate"] >= t["identified_or_asked_rate_min"]
    # Detection must improve calibration on the changed factor, and Brier and coverage are always reported.
    assert m["brier"]["changed_factor"]["detecting"] < m["brier"]["changed_factor"]["pooled"]
    for scores in (m["changed_factor_final_answers"], m["unchanged_all_factors"]):
        for policy in ("pooled", "detecting"):
            assert 0 <= scores[policy]["coverage"] <= 1 and scores[policy]["brier"] is not None


def test_grounding_is_deterministic_per_seed():
    first = scenarios.grounding(seed=7, lifetimes=4)
    again = scenarios.grounding(seed=7, lifetimes=4)
    assert first["metrics"] == again["metrics"]


def test_lifelong_correction_resolves_both_partners_across_a_day():
    result = scenarios.lifelong_correction()
    _common(result, "lifelong_correction")
    m = result["metrics"]
    for mode in ("in_session", "day_later_exact", "day_later_semantic"):
        assert m[mode]["partner-A"]["meaning_correct"] and m[mode]["partner-B"]["meaning_correct"]
    assert m["executive"]["meaning_asks"] >= 1 and m["executive"]["other_asks"] == 0
    assert set(m["executive"]["asks_by_scope"]) == {"partner:partner-B"}
    assert m["day_later_decision"] == "act"
    assert scenarios.lifelong_correction()["metrics"] == m


def test_graph_development_cross_test_lesion_and_baselines():
    result = scenarios.graph_development()
    _common(result, "graph_development")
    forks = result["metrics"]["forks"]
    for name, fork in forks.items():
        h = fork["heldout_mse"]
        assert fork["cross_over_own_ratio"] > 1
        assert h["promoted_graph"] < h["fixed_random_sparse_lstsq_baseline"] < h["zero_predictor"]
        assert h["permuted_control"] > h["promoted_graph"]
        assert fork["lesion"]["true_edge_mean_delta_mse"] > fork["lesion"]["weak_edge_mean_delta_mse"]
        assert fork["lesion"]["true_edges"] == fork["lesion"]["weak_edges"] == 72


def test_growth_retention_gate_recall_and_unknown_context():
    result = scenarios.growth_retention()
    _common(result, "growth_retention")
    m = result["metrics"]
    assert m["rejection"]["failed"] == ["retention"]
    assert m["recall"]["context_accuracy"] >= 0.9 and m["recall"]["selected_mse"] < m["recall"]["latest_only_mse"]
    assert m["unknown_context"]["verdict"] == "unknown_context" and m["unknown_context"]["pending_id"] is not None
    assert m["unknown_context"]["graph_revisions_after"] == m["unknown_context"]["graph_revisions_before"]
    assert m["lookup_disabled"]["selected_mse"] == m["recall"]["selected_mse"]


def test_device_ownership_restart_checkpoint_and_pressure():
    result = scenarios.device_ownership(events=60)
    _common(result, "device_ownership")
    loop = result["metrics"]["loop"]
    assert loop["label"].endswith("not Spark") and loop["latency_ms"]["n"] == 60
    assert 0 < loop["latency_ms"]["p50"] <= loop["latency_ms"]["p95"] <= loop["latency_ms"]["p99"]
    assert result["metrics"]["storage_pressure"]["decision"] == "defer"


def _cli(argv):
    parser = argparse.ArgumentParser(prog="nsf")
    register(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(argv)
    return args.handler(args)


def test_cli_acceptance_writes_report_and_exit_code(tmp_path, capsys):
    out = tmp_path / "results" / "acceptance.json"
    assert _cli(["acceptance", "birth", "--out", str(out)]) == 0
    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(out.read_text())
    assert printed["pass"] is True and printed["simulated"] is True
    assert list(saved["scenarios"]) == ["birth"] and saved["scenarios"]["birth"]["pass"] is True
    assert _cli(["acceptance", "nonsense"]) == 2
