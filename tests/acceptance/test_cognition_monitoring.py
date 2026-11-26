"""ADR-054: the monitoring mechanism registered for the selective family. The plugin's registration is checked by
default; the corpus runs (about 30 s) only with NSF_BENCH_COGNITION=1: the shipped calibration is exactly what
fitting on the development split gives, and the mechanism beats the baseline on held-out calibration with
discrimination not worse (the numbers: docs/quality/evidence/genesis_monitoring.json)."""
import os

import pytest

from neuroscifi.acceptance import cognition
from neuroscifi.acceptance.cognition import monitoring as P
from neuroscifi.webcortex import monitoring as M

slow = pytest.mark.skipif(os.environ.get("NSF_BENCH_COGNITION") != "1", reason="set NSF_BENCH_COGNITION=1")


def test_the_mechanism_is_registered_with_every_component_ablatable():
    mech = next(m for m in cognition.mechanisms() if m.family == "selective" and m.name == "monitoring")
    assert set(mech.components) == {*M.COMPONENTS, "calibration", "stratify"}
    with pytest.raises(cognition.Refused):
        cognition._maker(mech, frozenset({"no_such_component"}))


def test_the_shipped_calibration_names_only_development_items():
    shipped = M.calibration()
    dev = set(cognition.splits.SELECTIVE["development"]["questions"])
    held = set(cognition.splits.SELECTIVE["held_out"]["questions"])
    named = {q.strip(" ()") for q in shipped["fitted_on"].split("(", 1)[1].split(",")}
    assert named <= dev and not named & held
    assert shipped["weights"] == M.WEIGHTS                  # fitted under the weights the product uses


@slow
def test_the_shipped_calibration_is_the_development_fit():
    fitted = P.fit_tables()
    shipped = M.calibration()
    assert fitted["strata"] == shipped["strata"]
    assert fitted["threshold"]["confidence"] == pytest.approx(shipped["threshold"]["confidence"])


def test_no_shipped_map_is_applied_on_so_little_development_data():
    """Wave 1 review A6: every shipped map was fitted on 6 to 15 items in 1 to 3 blocks: none is applied, and the
    file says so; claims never use a map (the unfitted score)."""
    shipped = M.calibration()
    for name, table in shipped["strata"].items():
        assert M.applied(table) is False and shipped["applied"][name] is False, name
        assert sum(table["n"]) < M.MIN_ITEMS or len(table["x"]) < M.MIN_BLOCKS
    assert shipped["minimum"] == {"items": M.MIN_ITEMS, "blocks": M.MIN_BLOCKS}


@slow
def test_held_out_claim_brier_is_lower_than_the_baseline_and_ranking_not_worse():
    """The supported claim (Wave 1 review B5): on 10 held-out claims, adding the verification state to the posterior
    lowers Brier; the unfitted score is what ships. ECE with 10 bins at n=10 backs no claim and is not asserted."""
    base = cognition.run(["selective"], split="held_out")["families"]["selective"]["result"]
    mon = cognition.run(["selective"], split="held_out", mechanism="monitoring")["families"]["selective"]["result"]
    assert mon["claim_probability"]["items"] == base["claim_probability"]["items"] == 10
    assert mon["claim_probability"]["murphy"]["brier"] < base["claim_probability"]["murphy"]["brier"]
    assert mon["claim_probability"]["auroc"] >= base["claim_probability"]["auroc"]
    assert mon["decision"]["wrong_withheld"] == 0 == mon["decision"]["wrong_items"]     # nothing to gain on held-out


@slow
def test_the_unfitted_claim_score_beats_the_fitted_map_on_development():
    """Why claims ship unfitted, decided on development alone (held-out agrees: the evidence file)."""
    from neuroscifi.acceptance.cognition import scoring
    table = P.fit_tables()["strata"]["claim"]
    rows = [r for r in P.rows("development") if r["category"] == "verification" and r["gold"] in ("supported",
                                                                                                    "disputed")]
    s = [P._scores(r["record"], frozenset())[0] for r in rows]
    y = [int(r["gold"] == "supported") for r in rows]
    assert scoring.brier([M.calibrate(v, None) for v in s], y) < scoring.brier([M.calibrate(v, table) for v in s], y)
