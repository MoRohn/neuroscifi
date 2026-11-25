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


@slow
def test_held_out_calibration_improves_and_ranking_is_not_worse():
    base = cognition.run(["selective"], split="held_out")["families"]["selective"]["result"]
    mon = cognition.run(["selective"], split="held_out", mechanism="monitoring")["families"]["selective"]["result"]
    assert mon["claim_probability"]["ece_10_bins"] < base["claim_probability"]["ece_10_bins"]
    assert mon["claim_probability"]["murphy"]["brier"] < base["claim_probability"]["murphy"]["brier"]
    assert mon["claim_probability"]["auroc"] >= base["claim_probability"]["auroc"]
    assert mon["selective"]["aurc"] <= base["selective"]["aurc"]
