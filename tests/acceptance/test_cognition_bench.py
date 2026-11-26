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
    rows = [{"answerable": True, "truth": 1.0, "value": 1.5, "sd": 0.5, "scale": 2.0},     # 0.25 off: outside 0.1
            {"answerable": True, "truth": 0.0, "value": None, "sd": None, "scale": 1.0},   # declined
            {"answerable": True, "truth": 2.0, "value": 0.0, "sd": None, "scale": 1.0},    # a point, 2.0 off
            {"answerable": True, "truth": 1.0, "value": 1.05, "sd": 0.0, "scale": 1.0},    # within tolerance
            {"answerable": False, "truth": None, "value": None, "sd": None, "scale": 1.0},
            {"answerable": True, "truth": 0.5, "value": None, "sd": None, "scale": 1.0, "identified_by": "front_door"},
            {"answerable": True, "truth": 0.5, "value": 0.52, "sd": 0.1, "scale": 1.0, "identified_by": "front_door"}]
    s = scoring.answer_scores(rows)
    assert s["answerable"] == 4 and s["answerable_with_front_door"] == 6 and s["tolerance"] == 0.1
    # every answerable row counts, the front door's too; a decline is not within tolerance: 1.05 and 0.52 of 6
    assert s["answered_within_tolerance"] == pytest.approx(2 / 6)
    assert s["answered_within_tolerance_without_front_door"] == pytest.approx(1 / 4)    # the secondary figure
    assert s["strict_tolerance"] == 0.05 and s["answered_within_strict_tolerance"] == pytest.approx(2 / 6)  # 0.05 is in
    assert scoring.answer_scores(rows[:3] + [{**rows[3], "value": 1.06}])["answered_within_strict_tolerance"] == 0.0
    assert s["max_relative_error"] == pytest.approx(2.0)
    z = 1.959963984540054
    by_hand = [2 * z * 0.5 / 2.0, 2 / 0.05 * 2.0, 2 / 0.05 * 0.05]             # width / scale; 2/alpha x the miss
    assert s["interval_score"] == pytest.approx(sum(by_hand) / 3) and s["interval_score_answered"] == 3
    assert s["front_door"] == 2 and s["front_door_declined"] == 1 and s["front_door_within_tolerance"] == 0.5
    assert s["unanswerable"] == 1
    # the old scores, renamed: any answer counts, and declining the confounded questions is not a correctness score
    assert s["answered_share"] == pytest.approx(3 / 4)
    assert s["mean_relative_error"] == pytest.approx((0.25 + 2.0 + 0.05) / 3)
    assert s["interval_coverage"] == pytest.approx(1 / 3)                       # sd 0 covers only an exact answer
    assert s["declined_confounded_share"] == pytest.approx(2 / 3)
    assert scoring.interval_score(0.0, 1e6, 1.0) > 3e6                          # a uselessly wide interval pays


def test_a_trivial_answerer_scores_badly_on_the_headline():
    """Wave 1 review B3: answering 0 with sd 1e6 (and declining only the confounded questions) scored coverage 1.0,
    interval coverage 1.0 and abstention 1.0. On the headline and the interval score it is nowhere near the exact
    answers, while the old scores still cannot tell it apart."""

    spec = {**splits.COUNTERFACTUAL["development"], "seeds": (31,)}
    zero = counterfactual.run(spec, counterfactual.ZeroAnswerer)["summary"]
    assert zero["answered_share"]["mean"] == 1.0 and zero["interval_coverage"]["mean"] == 1.0
    assert zero["declined_confounded_share"]["mean"] == 1.0                        # the old scores are fooled
    assert zero["answered_within_tolerance"]["mean"] < 0.25
    assert zero["interval_score"]["mean"] > 1e5


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


def test_withheld_family_is_refused_without_final(tmp_path, monkeypatch):
    monkeypatch.setattr(cognition, "LEDGER", tmp_path / "ledger.jsonl")
    with pytest.raises(cognition.Refused, match="final"):
        cognition.run(["withheld"])
    assert not (tmp_path / "ledger.jsonl").exists()


def test_a_final_run_has_no_ledger_but_the_repositorys():
    """review0 L7: run(["withheld"], final=True, ledger=<elsewhere>) scored the withheld family without a line in the
    repository's ledger. The public API names no ledger; the rest of the protocol is procedural (the document)."""
    import inspect
    assert "ledger" not in inspect.signature(cognition.run).parameters
    with pytest.raises(TypeError):
        cognition.run(["withheld"], final=True, ledger=Path("elsewhere.jsonl"))
    assert cognition.LEDGER == cognition.REPO / "docs" / "quality" / "evidence" / "genesis_final_uses.jsonl"


def test_every_final_run_is_recorded(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(cognition, "LEDGER", ledger)      # a test's ledger: the repository's stays untouched
    monkeypatch.setattr(withheld, "WITHHELD", _small_withheld())
    first = cognition.run(["withheld"], final=True, seeds=(9001,), note="first")
    again = cognition.run(["withheld"], final=True, seeds=(9001,), note="again")
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


def _front_door(q):
    """E[Y | do(X = x)] from what a confounded query gives, by the front-door adjustment through X's observed child M:
    slope = b(M ~ X) * b(Y ~ M | X), level from the observational means (Pearl 1995; linear Gaussian)."""
    x, y = q["hidden_common_cause"]
    m = next(v for v, parents in q["graph"].items() if x in parents)
    c, mu = q["observational_covariance"], q["observational_mean"]
    b_mx = c[m][x] / c[x][x]
    b_ym_x = (c[y][m] * c[x][x] - c[y][x] * c[m][x]) / (c[m][m] * c[x][x] - c[m][x] ** 2)
    return mu[y] + b_mx * b_ym_x * (q["do"][x] - mu[x])


def test_counterfactual_truths_are_exact():
    """An exact solver for the given model answers every answerable SCM query with no error; the confounded queries
    are answered exactly by the front-door adjustment from what they give (so a decline there is an abstention, not
    a correct answer); today's system answers only the one-step graph queries."""

    class Exact:
        def answer(self, q):
            if "hidden_common_cause" in q:
                return {"value": _front_door(q), "sd": 0.0}
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

    for split in ("development", "held_out"):         # 3 and 4 nodes: a mediator on each
        spec = {"seeds": (31, 32), "scm": splits.COUNTERFACTUAL[split]["scm"], "graph": (), "belief": ()}
        out = counterfactual.run(spec, Exact)
        for kind in out["by_query_kind"]:
            if kind != "scm:confounded":
                assert out["by_query_kind"][kind]["answered_share"] == 1.0
                assert out["by_query_kind"][kind]["mean_relative_error"] < 1e-6
        front = out["by_query_kind"]["scm:confounded"]
        assert front["front_door"] == 4 * 2 and front["front_door_declined"] == 0
        assert front["front_door_within_tolerance"] == 1.0
        assert out["summary"]["answered_within_tolerance"]["mean"] == 1.0
        assert out["summary_by_group"]["front_door"]["front_door_within_tolerance"]["mean"] == 1.0
    today = counterfactual.run({"seeds": (31,), "scm": splits.COUNTERFACTUAL["development"]["scm"], "belief": (),
                                "graph": splits.COUNTERFACTUAL["development"]["graph"]}, counterfactual.TodayAnswerer)
    assert today["by_query_kind"]["graph:horizon 1"]["answered_share"] == 1.0
    assert today["by_query_kind"]["graph:horizon 3"]["answered_share"] == 0.0
    assert today["by_query_kind"]["graph:action"]["answered_share"] == 0.0
    assert today["summary_by_group"]["front_door"]["front_door_declined"]["mean"] == 4    # an honest abstention
    assert set(today["summary_by_group"]) == set(counterfactual.GROUPS)


def test_declining_an_answerable_question_cannot_score_one_on_the_headline():
    """Re-review of the Wave 1 fixes, benchmark issue 1: the headline dropped the front-door questions (answerable)
    from its denominator, so a system that declined all of them scored 1.0. An answerer exact on every question
    except those, which it declines, must stay below 1.0 on the headline (and on the front-door group); the old
    figure survives only under its own name."""

    class ExactButNoFrontDoor:
        def answer(self, q):
            if "hidden_common_cause" in q:
                return {"value": None, "sd": None}
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

    spec = {"seeds": (31,), "scm": splits.COUNTERFACTUAL["held_out"]["scm"], "graph": (), "belief": ()}
    out = counterfactual.run(spec, ExactButNoFrontDoor)
    seed = out["per_seed"][0]
    assert seed["front_door"] == 4 and seed["front_door_declined"] == 4
    assert out["summary"]["answered_within_tolerance"]["mean"] < 1.0             # declines are not within tolerance
    assert seed["answered_within_tolerance"] == pytest.approx(seed["answerable"] / seed["answerable_with_front_door"])
    assert seed["answered_within_tolerance_without_front_door"] == 1.0           # the old, secondary figure
    assert out["summary_by_group"]["front_door"]["answered_within_tolerance"]["mean"] == 0.0
    assert out["summary_by_group"]["handed_equations"]["answered_within_tolerance"]["mean"] == 1.0
    assert "not a claim" in out["summary_is"]


def test_two_nodes_with_a_hidden_cause_stay_unanswerable():
    rows = counterfactual.scm_queries({"structure": "confounded", "nodes": 2, "queries": 2}, 1)
    assert all(not r["answerable"] and r["truth"] is None and "identified_by" not in r for r in rows)


def test_self_description_staging_matches_its_design():
    out = selfmodel.run({"seeds": (41,), "states": splits.SELF_MODEL["development"]["states"]},
                        selfmodel.TodayDescriber)
    assert out["staging_problems"] == []
    assert out["per_probe"]["uncertain_about"]["answered"] == 0.0          # no surface says what is uncertain
    assert out["per_probe"]["memory_level"]["correct"] == 1.0


def test_adversarial_cases_today():
    """Today's behaviour, measured: every staged case runs and passes. The baseline's one failure,
    `simulated_reading_moves_no_belief`, was a mislabelled expectation (a reading of a simulated test environment is
    an observation, ADR-014); ADR-052 §6 corrected it to `imagined_reading_moves_no_belief`: a what-if never reaches
    the live belief, the journal or the state version."""
    out = adversarial.run()
    scored = [c for c in out["cases"] if c["pass"] is not None]
    assert len(scored) == len(adversarial.CASES) and all(isinstance(c["pass"], bool) for c in scored)
    assert out["failed"] == [], out["failed"]
    case = next(c for c in out["cases"] if c["id"] == "imagined_reading_moves_no_belief")
    assert "0.500 -> 0.500" in case["observed"] and "move it to 0.99" in case["observed"], case["observed"]
    # reported by origin, never added up into an improvement (Wave 1 review)
    origin = out["by_origin"]
    assert (origin["original"]["passed"], origin["original"]["scored"]) == (13, 13)
    assert "imagined_reading_moves_no_belief" in origin["original"]["redefined"]
    assert (origin["added_wave1"]["scored"], origin["restored"]["scored"]) == (4, 1)
    assert sum(len(ids) for ids in adversarial.ORIGIN.values()) == len(adversarial.CASES)


def test_the_restored_case_fails_when_a_system_made_reading_gets_into_the_journal(monkeypatch):
    """The restored case is not vacuous: a journal that lets the system-made reading in (here: laundered into a plain
    observation before the checks) fails it. (The database's own CHECK and triggers refuse it too.)"""
    from neuroscifi.instance import evidence
    assert adversarial.system_made_content_never_enters_the_journal()["pass"] is True
    real, n = evidence.observe, iter(range(100))

    def leaky(db, record, now):
        return real(db, dict(record, id=f"leak-{next(n)}", root="p", sample_id=None, provenance="observed"), now)
    monkeypatch.setattr(evidence, "observe", leaky)
    out = adversarial.system_made_content_never_enters_the_journal()
    assert out["pass"] is False and "CHANGED" in out["observed"]


def test_self_description_is_scored_under_both_answer_keys():
    """Wave 1 review B4: the original key (uncertain_about None when everything is settled) beside lane W's, and the
    two probes whose truth comes from the code the describer reads marked as plumbing checks."""

    class SaysNothing(selfmodel.TodayDescriber):
        def describe(self, probe, surfaces):
            return selfmodel.NOTHING if probe == "uncertain_about" else super().describe(probe, surfaces)

    spec = {"seeds": (41,), "states": splits.SELF_MODEL["development"]["states"][1:]}      # "quiet night": settled
    out = selfmodel.run(spec, SaysNothing)
    assert out["summary"]["accuracy"]["mean"] == pytest.approx(out["summary_original_key"]["accuracy"]["mean"]
                                                               + 1 / len(selfmodel.PROBES))
    assert out["plumbing_checks"] == ["authorised_model_tools", "last_decision"]
    assert out["per_probe"]["last_decision"]["plumbing_check"] is True and "printed" in out["disclosure"]
    assert out["summary_without_plumbing"]["correct"]["mean"] <= out["summary_without_plumbing"]["probes"]["mean"]


def test_wide_seeds_are_disjoint_and_paired():
    every = set(withheld.WITHHELD["seeds"])
    for fam in (splits.ANTICIPATION, splits.INFO_VALUE, splits.COUNTERFACTUAL, splits.SELF_MODEL):
        every |= set(fam["development"]["seeds"]) | set(fam["held_out"]["seeds"])
    assert len(cognition.WIDE_SEEDS) == 12 and not set(cognition.WIDE_SEEDS) & every
    out = cognition.wide_seeds("counterfactual", "development", "baseline", reference="baseline", seeds=(5001, 5002))
    row = out["metrics"]["summary.answered_within_tolerance.mean"]
    assert row["paired_difference"]["mean"] == 0.0 and row["seeds_equal"] == 2 and out["supplementary"]
    with pytest.raises(ValueError):
        cognition.wide_seeds("selective", "development", "baseline")


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
        assert cognition.compare({"development": ablated, "held_out": {}}, out) == diff   # the baseline file's shape
    finally:
        cognition._REGISTRY.pop(("anticipation", "test-holder"), None)


def test_a_mechanism_switched_on_never_reads_a_corpus_run_cached_without_it(monkeypatch):
    """review0 M3: the offline corpus run was cached per process by `quick` alone, so retention (and selective) under
    a mechanism's activate() read the baseline's cached run: "retained" by construction."""
    from contextlib import contextmanager
    from neuroscifi.acceptance.cognition import selective
    switched = []

    class Agent:
        @contextmanager
        def activate(self):
            switched.append("on")
            yield
            switched.append("off")
    monkeypatch.setattr(selective, "_CACHE", {False: "a run made without the mechanism"})
    with cognition._activation(lambda: Agent()):
        assert selective._CACHE == {} and switched == ["on"]
        selective._CACHE[False] = "a run made with the mechanism"
    assert selective._CACHE == {} and switched == ["on", "off"]     # nor does a later family read the mechanism's
    selective._CACHE[False] = "a baseline run"
    with cognition._activation(lambda: object()):                     # no mechanism to switch on: the cache stands
        assert selective._CACHE == {False: "a baseline run"}
    # review0b N9: the baseline of retention and adversarial switches nothing on either: it reads the cached run, as
    # the recorded baseline costs did (clearing it re-ran the corpus: 29.5 s instead of 14.6 s)
    for family in ("retention", "adversarial"):
        with cognition._activation(cognition._maker(cognition._REGISTRY[(family, "baseline")], frozenset())):
            assert selective._CACHE == {False: "a baseline run"}
    assert selective._CACHE == {False: "a baseline run"}


def test_retention_compares_only_with_a_baseline_of_the_same_corpus():
    """review0 M3: a --quick run was compared with the recorded full-corpus baseline."""
    from neuroscifi.acceptance.cognition import retention
    recorded = {"families": {"retention": {"result": {"quick_offline_run": False, "offline_metrics": {
        "a.b": {"value": 0.5}}}}}}
    assert retention.recorded(recorded, quick=False) == {"a.b": 0.5}
    assert retention.recorded(recorded, quick=True) is None
    assert retention.recorded({"development": recorded}, quick=False) == {"a.b": 0.5}


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
