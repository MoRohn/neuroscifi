"""Simulated acceptance scenarios at reduced sizes: each must pass and show the relationships it claims."""
import argparse
import json

import numpy as np
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
    assert cw["pooled"] > 0 and cw["detecting"] < cw["pooled"] and cw["manual_new_epoch"] < cw["pooled"]
    assert cw["relative_reduction"] >= t["confident_wrong_relative_reduction_min"]
    assert m["unchanged_coverage_detecting"] >= t["unchanged_coverage_min"]
    assert m["unchanged_task_loss_points"] <= t["unchanged_task_loss_points_max"]
    assert m["detection"]["identified_or_asked_rate"] >= t["identified_or_asked_rate_min"]
    # Detection must improve calibration on the changed factor, and Brier and coverage are always reported.
    assert m["brier"]["changed_factor"]["detecting"] < m["brier"]["changed_factor"]["pooled"]
    for scores in (m["changed_factor_final_answers"], m["unchanged_all_factors"]):
        for policy in ("pooled", "detecting", "manual_new_epoch"):
            assert 0 <= scores[policy]["coverage"] <= 1 and scores[policy]["brier"] is not None


def test_grounding_product_flow_stays_in_one_epoch_and_keeps_the_partner(grounding):
    m = grounding["metrics"]
    # The product flow answers the executive's question and never starts a new epoch; the manual arm does.
    assert m["detection"]["mean_epochs_per_lifetime"] == 1
    assert m["detection_manual_new_epoch"]["mean_epochs_per_lifetime"] > 1
    owner = m["owner_questions_per_lifetime"]["detecting"]
    assert owner["confirmed_total"] >= 1 and owner["all"]["total"] == owner["confirmed_total"] + owner["rejected_total"]
    assert 0 < owner["all"]["mean"] <= 3   # at most the ask budget of the single epoch
    # Relearning only the camera keeps the partner's meaning; a new epoch forgets it.
    kept = m["partner_meaning_preserved_after_camera_change"]
    assert kept["lifetimes"] > 0 and kept["detecting"] > kept["manual_new_epoch"]


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


def test_device_ownership_restart_checkpoint_concurrency_and_pressure():
    result = scenarios.device_ownership(events=60)
    _common(result, "device_ownership")
    m = result["metrics"]
    loop, loaded = m["loop"], m["loop_under_consolidation"]
    host = loop["host"]
    if host["machine"] == "aarch64" and host["gpu"] and "GB10" in host["gpu"]:
        assert loop["label"] == "host = this DGX Spark (aarch64, GB10)"
    else:
        assert loop["label"].endswith("not a DGX Spark")
    for timed in (loop, loaded):
        assert timed["latency_ms"]["n"] == 60
        assert 0 < timed["latency_ms"]["p50"] <= timed["latency_ms"]["p95"] <= timed["latency_ms"]["p99"]
    assert loaded["failed_observations"] == 0 and loaded["failures"] == {"busy": 0, "writes_paused": 0}
    background = loaded["background"]
    assert background["operations"]["learn"] >= 1 and background["operations"]["checkpoint"] >= 1
    assert background["operations_overlapping_loop"] >= 1 and set(background["errors"]) == {"busy", "writes_paused"}
    # Checkpoint operations advance the live state_version; restore equality ignores exactly that key.
    versions = m["checkpoint"]["state_version"]
    assert versions["live"] > versions["restored"]
    assert m["storage_pressure"]["decision"] == "defer"


def test_restore_comparison_ignores_only_state_version():
    state = {"state_version": 3, "mechanism": {"p_inverted": .1}, "epoch": "bench"}
    assert scenarios._without_state_version(state) == {"mechanism": {"p_inverted": .1}, "epoch": "bench"}
    assert scenarios._without_state_version(state) != scenarios._without_state_version(
        dict(state, mechanism={"p_inverted": .2}))


@pytest.fixture(scope="module")
def first_test():
    return scenarios.first_physical_test(lifetimes=12)


def test_first_physical_test_reports_declared_criteria_and_baselines(first_test):
    result = first_test
    assert result["name"] == "first_physical_test" and result["simulated"] is True
    assert result["pass"] == all(result["checks"].values())
    json.dumps(result, allow_nan=False)
    m, t = result["metrics"], result["thresholds"]
    assert (t["attribution_min"], t["earlier_setup_recognition_min"], t["retention_min"]) == (0.80, 0.80, 0.90)
    checks = result["checks"]
    assert checks["attribution_at_least_80_percent"] == (m["attribution_rate"] >= 0.80)
    assert checks["earlier_setup_recognised_at_least_80_percent"] == (m["earlier_setup_recognition_rate"] >= 0.80)
    assert checks["older_skill_retention_at_least_90_percent"] == (m["older_skill_retention"] >= 0.90)
    # Relationships this scenario demonstrates with the default seed.
    assert m["attribution_rate"] >= 0.80 and m["older_skill_retention"] >= 0.90
    finals = m["final_answers_end_of_panel_a"]
    assert finals["product"]["confident_wrong"] < finals["fixed_wiring"]["confident_wrong"]
    assert checks["fewer_confident_wrong_final_answers_than_fixed_wiring"]
    assert finals["recurrent_replay"] is None and finals["episodic_lookup"]["answers"] == 36
    assert m["restart_panel_beliefs_identical"] == 1.0
    assert m["retrieval_archive_lookup_disabled"]["product_last_revision_summary_accuracy"] >= 0.90
    for name in ("product", "fixed_wiring", "episodic_lookup", "recurrent_replay"):
        prediction = m["next_camera_reading_prediction"][name]
        assert prediction["panel_a"]["n"] == 12 * 40 and prediction["return_to_earlier_setup"]["n"] == 12 * 4
        assert 0 <= prediction["panel_a"]["brier"] <= 1
        assert m["compute_cpu_seconds_per_lifetime"][name]["mean"] >= 0
    interventions = m["interventions"]
    assert interventions["owner_answers_per_lifetime"]["total"] >= 12   # every remount is confirmed by the owner
    assert interventions["reference_readings_per_lifetime"] == 10
    assert interventions["question_kinds"].get("change:yes", 0) >= 12 * m["attribution_rate"]
    # Recognition scores panel-A's earlier segments against a new setup; without references it is bounded by 2/3.
    bound = m["recognition_information_bound"]
    assert bound["best_case_posterior_in_designed_return"] < bound["limit_posterior_without_references"] < .75
    assert m["return_match"]["panel_a_posterior_max"] <= bound["best_case_posterior_in_designed_return"] + 1e-12
    rows = m["recognition_sensitivity_report_only"]
    assert [(r["return_steps"], r["reference_readings"]) for r in rows] == \
        [(4, 0), (4, 1), (6, 0), (6, 1), (8, 0), (8, 2), (12, 0), (12, 3)]
    assert all(r["recognition_rate"] == 0 for r in rows if not r["reference_readings"])
    continued = m["return_after_continue"]
    assert continued["lifetimes_continued"] == round(12 * m["earlier_setup_recognition_rate"])
    counterfactual = m["return_after_continue_counterfactual_report_only"]
    after = counterfactual["next_camera_reading_steps_2_to_4"]
    assert after["continued"]["n"] == after["not_continued_same_readings"]["n"] == 12 * 3
    assert after["continued"]["accuracy"] > after["not_continued_same_readings"]["accuracy"]
    beliefs_after = counterfactual["return_beliefs_correct_for_original_setup"]
    assert beliefs_after["continued"] > beliefs_after["not_continued"]


def test_first_physical_test_is_deterministic_per_seed():
    def strip(result):
        return {k: v for k, v in result["metrics"].items() if k != "compute_cpu_seconds_per_lifetime"}
    assert strip(scenarios.first_physical_test(seed=11, lifetimes=3)) == \
        strip(scenarios.first_physical_test(seed=11, lifetimes=3))


def test_recurrent_baseline_learns_a_fixed_mapping():
    model = scenarios._RecurrentReplay(np.random.default_rng(0))
    rng = np.random.default_rng(1)
    hits = []
    for i in range(300):
        action = int(rng.integers(2))
        p = model.step({"channel": "camera", "root": "camera-1", "action": action, "bit": action ^ 1})
        hits.append((p > .5) == bool(action ^ 1))
    assert np.mean(hits[-100:]) > .95 and len(model.buffer) == scenarios.RECURRENT_DESIGN["replay_capacity"]


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
