"""The cognitive benchmark (acceptance/cognition): scoring functions against hand-computed cases, determinism per
seed, the freeze of the task definitions, the withheld-family guard and its ledger, the offline guard, and the
registration interface. The full baseline run (about a minute: the offline corpus and the grounding scenario) runs
only with NSF_BENCH_COGNITION=1, like the other opt-in suites; its numbers are docs/quality/evidence/
genesis_baseline.json."""
import hashlib
import json
import math
import os
from pathlib import Path
import socket

import pytest

from neuroscifi.acceptance import cognition
from neuroscifi.acceptance.cognition import (adversarial, anticipation, counterfactual, infovalue, scoring, selfmodel,
                                             splits, withheld)

# The digests of the task definitions as frozen in their own commit, before any mechanism or tuning. A change here
# means the held-out or withheld tasks changed: every earlier number stops being comparable. Do not update these to
# make a run pass; add new tasks in a new file instead.
SPLITS_SHA256 = "7f21365473e2fedd36011d626be46453d67433691b67b9b126244f1d644a1c39"
WITHHELD_SHA256 = "be646257370e45c6ac50b45b51f9dcf75b2ec556c4f91f2933618082251964e2"


# ---- scoring, against hand-computed values ---------------------------------------------------------------------------

def test_information_scores_by_hand():
    h = lambda p: -(p * math.log2(p) + (1 - p) * math.log2(1 - p))  # noqa: E731
    assert scoring.entropy(0.5) == 1.0 and scoring.entropy(1.0) == 0.0
    assert scoring.report_gain(0.5, 0.8) == pytest.approx(1 - h(0.8))          # 0.278072
    assert scoring.report_gain(0.3, 0.5) == 0.0                                 # a coin-flip report teaches nothing
    assert scoring.report_posterior(0.5, 0.9, 1) == pytest.approx(0.9)
    assert scoring.report_posterior(0.5, 0.9, 0) == pytest.approx(0.1)
    assert scoring.readings_gain(0.5, 0.1, 1) == pytest.approx(1 - h(0.1))      # one reading = one report
    assert scoring.readings_gain(0.5, 0.1, 0) == 0.0 and scoring.readings_gain(1.0, 0.1, 3) == 0.0
    assert scoring.readings_gain(0.5, 0.1, 3) > scoring.readings_gain(0.5, 0.1, 1)
    assert scoring.log_score_gain(0.5, 0.9, 1) == pytest.approx(math.log2(0.9 / 0.5))
    assert scoring.log_score_gain(0.5, 0.9, 0) == pytest.approx(math.log2(0.1 / 0.5))   # confident and wrong: < 0


def test_breach_report_by_hand():
    assert scoring.episodes([False, True, True, False, True]) == [(1, 3), (4, 5)]
    reference = [False] * 4 + [True, True] + [False] * 4          # one breach coming at tick 4
    regulated = [False] * 10                                       # the regulator avoided it
    holds = [False, True] + [False] * 6 + [True, False]            # held at 1 (3 ticks ahead) and at 8 (nothing coming)
    r = scoring.breach_report(reference, regulated, holds, horizon=3)
    assert (r["reference_episodes"], r["avoided"], r["avoided_share"]) == (1, 1, 1.0)
    assert r["anticipated"] == 1 and r["mean_lead_ticks"] == 3
    assert (r["hold_ticks"], r["false_alarm_ticks"], r["false_alarm_share"]) == (2, 1, 0.5)
    late = scoring.breach_report(reference, reference, [False] * 10, horizon=3)
    assert late["avoided"] == 0 and late["mean_lead_ticks"] is None and late["false_alarm_share"] is None


def test_brier_murphy_and_ece_by_hand():
    f, o = [0.8, 0.8, 0.2, 0.2], [1, 0, 0, 0]
    assert scoring.brier([0.8, 0.3], [1, 0]) == pytest.approx(0.065)
    m = scoring.murphy(f, o)
    assert m["brier"] == pytest.approx(0.19) and m["reliability"] == pytest.approx(0.065)
    assert m["resolution"] == pytest.approx(0.0625) and m["uncertainty"] == pytest.approx(0.1875)
    assert m["brier"] == pytest.approx(m["reliability"] - m["resolution"] + m["uncertainty"])
    assert scoring.ece(f, o) == pytest.approx(0.25)                             # 0.5 * 0.3 + 0.5 * 0.2
    assert scoring.ece(f, o, bins=2, mass=True) == pytest.approx(0.25)
    assert scoring.ece([1.0], [1]) == 0.0                                       # a forecast of 1 is in the last bin
    assert scoring.brier([], []) is None and scoring.murphy([], []) is None


def test_risk_coverage_and_auroc_by_hand():
    rc = scoring.risk_coverage([0.9, 0.8, 0.8, 0.1], [True, False, True, False])
    assert [(p["coverage"], round(p["risk"], 6)) for p in rc["points"]] == [(0.25, 0.0), (0.75, 0.333333),
                                                                            (1.0, 0.5)]
    assert rc["aurc"] == pytest.approx((0 + 1 / 3 + 1 / 3 + 0.5) / 4)            # ties take the group's risk
    assert rc["coverage_at_risk_0.05"] == 0.25 and rc["full_coverage_risk"] == 0.5
    assert scoring.auroc([0.9, 0.8, 0.8, 0.1], [1, 0, 1, 0]) == pytest.approx(3.5 / 4)
    assert scoring.auroc([0.5, 0.5], [1, 1]) is None


def test_answer_scores_by_hand():
    rows = [{"answerable": True, "truth": 1.0, "value": 1.5, "sd": 0.5, "scale": 2.0},
            {"answerable": True, "truth": 0.0, "value": None, "sd": None, "scale": 1.0},
            {"answerable": True, "truth": 2.0, "value": 0.0, "sd": None, "scale": 1.0},
            {"answerable": False, "truth": None, "value": None, "sd": None, "scale": 1.0},
            {"answerable": False, "truth": None, "value": 3.0, "sd": 1.0, "scale": 1.0}]
    s = scoring.answer_scores(rows)
    assert s["coverage"] == pytest.approx(2 / 3)
    assert s["mean_relative_error"] == pytest.approx((0.25 + 2.0) / 2)
    assert s["interval_coverage"] == 0.5 and s["abstention_correct"] == 0.5


def test_spread_and_rounding():
    s = scoring.spread([1.0, 3.0, None])
    assert (s["mean"], s["sd"], s["n"]) == (2.0, pytest.approx(math.sqrt(2)), 2)
    assert scoring.spread([None])["mean"] is None
    assert scoring.rounded({"a": [1.23456789, float("nan")]}, 3) == {"a": [1.235, None]}


# ---- the frozen task definitions -------------------------------------------------------------------------------------

def test_task_definitions_are_frozen():
    for module, pinned in ((splits, SPLITS_SHA256), (withheld, WITHHELD_SHA256)):
        assert hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() == pinned, module.__name__


def test_development_held_out_and_withheld_seeds_are_disjoint():
    families = (splits.ANTICIPATION, splits.INFO_VALUE, splits.COUNTERFACTUAL, splits.SELF_MODEL)
    for fam in families:
        dev, held = set(fam["development"]["seeds"]), set(fam["held_out"]["seeds"])
        assert dev and held and not dev & held and not (dev | held) & set(withheld.WITHHELD["seeds"])
    dev, held = set(splits.SELECTIVE["development"]["questions"]), set(splits.SELECTIVE["held_out"]["questions"])
    assert dev and held and not dev & held
    names = lambda split: {t["name"] for t in splits.ANTICIPATION[split]["traces"]}  # noqa: E731
    assert not names("development") & names("held_out")


def test_withheld_family_is_refused_without_final(tmp_path):
    with pytest.raises(cognition.Refused, match="final"):
        cognition.run(["withheld"], ledger=tmp_path / "ledger.jsonl")
    assert not (tmp_path / "ledger.jsonl").exists()


def test_every_final_run_is_recorded(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(withheld, "WITHHELD", _small_withheld())
    first = cognition.run(["withheld"], final=True, ledger=ledger, seeds=(9001,), note="first")
    again = cognition.run(["withheld"], final=True, ledger=ledger, seeds=(9001,), note="again")
    assert [r["note"] for r in first["final_runs"]] == ["first"]
    assert [r["note"] for r in again["final_runs"]] == ["first", "again"]
    assert len(ledger.read_text().splitlines()) == 2
    result = again["families"]["withheld"]["result"]
    assert set(result) == {"anticipation", "info_value", "counterfactual", "self_model"}
    assert again["families"]["withheld"]["mechanisms"]["anticipation"]["used"] == "baseline"


def _small_withheld():
    w = withheld.WITHHELD
    return {"seeds": w["seeds"], "anticipation": w["anticipation"][:1],
            "info_value": {"core": w["info_value"]["core"], "web": ()},
            "counterfactual": {"scm": w["counterfactual"]["scm"][:1], "graph": (), "belief": ()},
            "self_model": w["self_model"]}


# ---- determinism per seed --------------------------------------------------------------------------------------------

def test_anticipation_is_deterministic_per_seed_and_seeds_differ():
    spec = splits.ANTICIPATION["development"]["traces"][1]
    a = anticipation.score_trace(spec, 11, anticipation.ReactiveRegulator())
    b = anticipation.score_trace(spec, 11, anticipation.ReactiveRegulator())
    c = anticipation.exogenous(spec, 12)
    assert a == b and anticipation.exogenous(spec, 11) != c


def test_info_value_counterfactual_and_self_model_are_deterministic_per_seed():
    core = splits.INFO_VALUE["development"]["core"][0]
    web = splits.INFO_VALUE["development"]["web"][0]
    for fn, spec, chooser in ((infovalue.core_world, core, infovalue.ExecutiveChooser),
                              (infovalue.web_world, web, infovalue.CuriosityChooser)):
        assert fn(spec, 21, chooser()) == fn(spec, 21, chooser())
    spec = {"seeds": (31,), **{k: splits.COUNTERFACTUAL["development"][k] for k in ("scm", "graph", "belief")}}
    first = counterfactual.run(spec, counterfactual.TodayAnswerer)
    assert first == counterfactual.run(spec, counterfactual.TodayAnswerer)
    one = {"seeds": (41,), "states": splits.SELF_MODEL["development"]["states"]}
    assert selfmodel.run(one, selfmodel.TodayDescriber) == selfmodel.run(one, selfmodel.TodayDescriber)


# ---- the families, on today's system ---------------------------------------------------------------------------------

def test_the_existing_gate_is_part_of_the_plant():
    """A regulator can only add holds: one that never holds scores exactly like today's gate."""
    spec = splits.ANTICIPATION["development"]["traces"][5]                      # periodic voice sessions
    open_run = anticipation.simulate(spec, 11, anticipation.OpenRegulator())
    gate_run = anticipation.simulate(spec, 11, anticipation.ReactiveRegulator())
    assert open_run["flags"] == gate_run["flags"] and open_run["work"] == gate_run["work"]
    assert not any(open_run["holds"]) and any(gate_run["holds"])                 # only the recorded holds differ
    ungated = anticipation.simulate(spec, 11, None, gate=False)
    assert sum(ungated["flags"]["audio"]) > sum(gate_run["flags"]["audio"])     # the gate itself does something

    class Always:
        def decide(self, history):
            return {"background": "hold"}
    held = anticipation.score_trace(spec, 11, Always())
    assert held["background_work"] == 0 and held["audio"]["ticks"] == 0


def test_anticipation_thresholds_come_from_the_code():
    from neuroscifi.resources import monitor
    limits = anticipation.CONSTANTS["code_thresholds_at_128_gib"]
    assert limits == monitor.thresholds(128 * 1024 ** 3)
    assert anticipation.BREACH_FLOOR_GIB * 1024 ** 3 < limits["critical_below_bytes"]


def test_information_value_oracle_bounds_and_noise():
    spec = splits.INFO_VALUE["development"]["core"][0]
    oracle = infovalue.core_world(spec, 21, "oracle")
    assert oracle["noise_share"] == 0.0 and oracle["known_gain_bits"] > 0
    world = infovalue.web_world(splits.INFO_VALUE["development"]["web"][0], 21, "oracle")
    assert world["noise_share"] == 0.0 and world["realised_bits"] > 0


def test_counterfactual_truths_are_exact():
    """An exact solver for the given model answers every answerable SCM query with no error; the confounded queries
    are not answerable; today's system answers only the one-step graph queries."""

    class Exact:
        def answer(self, q):
            if q.get("rung") in ("observational", "interventional", "counterfactual"):
                scm = {"order": q["model"]["order"], "edges": q["model"]["edges"],
                       "intercept": q["model"]["intercept"], "hidden": []}
                x, y = q["model"]["cause"], q["model"]["outcome"]
                if q["rung"] == "interventional":
                    return {"value": counterfactual._linear(scm, q["do"])[y][0], "sd": 0.0}
                if q["rung"] == "counterfactual":
                    obs = q["observed"]
                    noise = {v: obs[v] - scm["intercept"][v] - sum(c * obs[p] for p, c in scm["edges"][v].items())
                             for v in scm["order"]}
                    return {"value": counterfactual._solve(scm, noise, q["had"])[y], "sd": 0.0}
                lin = counterfactual._linear(scm)
                slope = counterfactual._cov(lin[y], lin[x]) / counterfactual._cov(lin[x], lin[x])
                return {"value": lin[y][0] + slope * (q["given"][x] - lin[x][0]), "sd": 0.0}
            return {"value": None, "sd": None}

    spec = {"seeds": (31, 32), "scm": splits.COUNTERFACTUAL["development"]["scm"], "graph": (), "belief": ()}
    out = counterfactual.run(spec, Exact)
    for kind in ("scm:observational", "scm:interventional", "scm:counterfactual"):
        assert out["by_query_kind"][kind]["coverage"] == 1.0
        assert out["by_query_kind"][kind]["mean_relative_error"] < 1e-6
    assert out["by_query_kind"]["scm:not_identifiable"]["answerable"] == 0
    assert out["by_query_kind"]["scm:not_identifiable"]["abstention_correct"] == 1.0
    today = counterfactual.run({"seeds": (31,), "scm": (), "belief": (),
                                "graph": splits.COUNTERFACTUAL["development"]["graph"]}, counterfactual.TodayAnswerer)
    assert today["by_query_kind"]["graph:horizon 1"]["coverage"] == 1.0
    assert today["by_query_kind"]["graph:horizon 3"]["coverage"] == 0.0
    assert today["by_query_kind"]["graph:action"]["coverage"] == 0.0


def test_self_description_staging_matches_its_design():
    out = selfmodel.run({"seeds": (41,), "states": splits.SELF_MODEL["development"]["states"]},
                        selfmodel.TodayDescriber)
    assert out["staging_problems"] == []
    assert out["per_probe"]["uncertain_about"]["answered"] == 0.0          # no surface says what is uncertain
    assert out["per_probe"]["memory_level"]["correct"] == 1.0


def test_adversarial_cases_today():
    """Today's behaviour, measured: every staged case runs; the one known gap is that a reading labelled
    provenance='simulated' moves a belief like an observation (ADR-014 labels it, nothing keeps it apart)."""
    out = adversarial.run()
    scored = [c for c in out["cases"] if c["pass"] is not None]
    assert len(scored) == len(adversarial.CASES) and all(isinstance(c["pass"], bool) for c in scored)
    assert out["failed"] == ["simulated_reading_moves_no_belief"], out["failed"]


# ---- the harness -----------------------------------------------------------------------------------------------------

def test_offline_guard_refuses_and_counts_non_loopback_connections():
    from neuroscifi.inference import gateway
    cost = {"model_calls": 0, "refused_connections": 0}
    original, chat = socket.socket.connect, gateway.Gateway.chat
    with cognition._guarded(cost):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            with pytest.raises(OSError, match="offline"):
                s.connect(("192.0.2.1", 9))
    assert cost["refused_connections"] == 1 and cost["wall_seconds"] is not None and cost["peak_rss_bytes"] > 0
    assert socket.socket.connect is original and gateway.Gateway.chat is chat       # everything restored


def test_registration_ablation_and_fallback(tmp_path):
    seen = []

    def make(ablate):
        class Holder:
            def decide(self, history):
                seen.append(ablate)
                return {"background": "hold" if "never" not in ablate else "allow"}
        return Holder

    cognition.register("anticipation", "test-holder", make, components=("never",))
    try:
        out = cognition.run(["anticipation", "counterfactual"], mechanism="test-holder", seeds=(11,))
        assert out["families"]["anticipation"]["mechanism"] == {"used": "test-holder", "fell_back_to_baseline": False,
                                                                "needs_model": False, "reference": False}
        assert out["families"]["counterfactual"]["mechanism"]["fell_back_to_baseline"] is True
        assert out["families"]["anticipation"]["result"]["summary"]["work_given_up_share"]["mean"] == 1.0
        ablated = cognition.run(["anticipation"], mechanism="test-holder", ablate=("never",), seeds=(11,))
        assert ablated["ablate"] == ["never"] and frozenset({"never"}) in seen
        with pytest.raises(cognition.Refused, match="no component"):
            cognition.run(["anticipation"], mechanism="test-holder", ablate=("typo",), seeds=(11,))
        for family in ("anticipation", "counterfactual"):
            assert out["families"][family]["cost"]["model_calls"] == 0
            assert out["families"][family]["cost"]["refused_connections"] == 0
        diff = cognition.compare(ablated, out)
        assert diff["anticipation"]["summary.work_given_up_share.mean"]["better"] is False
    finally:
        cognition._REGISTRY.pop(("anticipation", "test-holder"), None)


def test_command_line_lists_and_refuses(capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("bench_cognition", Path(__file__).resolve().parents[2] /
                                                  "scripts" / "bench_cognition.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert {"family": "anticipation", "name": "baseline"}.items() <= listed["mechanisms"][0].items()
    assert module.main(["run", "--family", "withheld"]) == 2
    assert "refused" in capsys.readouterr().err


@pytest.mark.skipif(os.environ.get("NSF_BENCH_COGNITION") != "1",
                    reason="the full cognitive benchmark (about a minute); set NSF_BENCH_COGNITION=1")
def test_full_development_run_is_deterministic():
    first = cognition.run(split="development")
    again = cognition.run(split="development")
    strip = lambda out: {f: v["result"] for f, v in out["families"].items()}  # noqa: E731
    assert strip(first) == strip(again)
    assert first["families"]["retention"]["result"]["all_scenarios_pass"] is True
    for family in first["families"].values():
        assert family["cost"]["model_calls"] == 0 and family["cost"]["refused_connections"] == 0
