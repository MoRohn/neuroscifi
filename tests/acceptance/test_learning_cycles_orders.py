"""Wave 3 lane R (ADR-060): the rate worlds (cycles_rate_splits.py, committed alone before everything else), the
diagnosis of the loop's shortfall (cycles.decompose), the re-check order configurations and their verdict rule."""
import hashlib
from pathlib import Path

from neuroscifi.acceptance.cognition import cycles, cycles_rate_splits as rate, cycles_splits

ROOT = Path(__file__).resolve().parents[2]
DEV = rate.CYCLES["development"]["worlds"][0]


def test_the_rate_worlds_are_frozen_with_seeds_disjoint_from_every_other_split():
    """Committed alone first (3f75ed8), before the harness that scores them and before the order mechanism."""
    digest = hashlib.sha256((ROOT / "src/neuroscifi/acceptance/cognition/cycles_rate_splits.py").read_bytes()).hexdigest()
    assert digest == "59b9a1a5d54be215f27be2b2b4405bd6c137f1d1c524bf54446e0246e76ff5c8"
    dev, held = (set(rate.CYCLES[s]["seeds"]) for s in ("development", "held_out"))
    lane_f = {s for split in cycles_splits.CYCLES.values() for s in split["seeds"]}
    assert not dev & held and not (dev | held) & lane_f and not (dev | held) & {7341, 7342, 7343, 7344}
    kinds = {w["name"]: (len(w["rates"]), w.get("shift_cycle")) for s in rate.CYCLES.values() for w in s["worlds"]}
    assert kinds["ho-rate-uniform"] == (1, None) and kinds["ho-rate-mixed"][0] == 3 and kinds["ho-rate-shift"][1] == 8


def test_the_world_changes_and_the_pages_draw_from_separate_streams():
    world = rate.generate(DEV, 2601)
    assert world == rate.generate(DEV, 2601)
    # other page settings: other pages, the same truth for every subject
    other = rate.generate(DEV | {"lag": 0.6, "wire": 0.3, "one_origin": 0.4, "outlets_per_look": 2}, 2601)
    assert [s["truth"] for s in other["subjects"]] == [s["truth"] for s in world["subjects"]]
    assert other["looks"] != world["looks"]
    # the shift world draws every class again at its shift cycle, and only there
    shift = rate.CYCLES["development"]["worlds"][2]
    w = rate.generate(shift, 2601)
    moved = {c for s in w["subjects"] for c in range(1, shift["cycles"] + 1)
             if c in s["rate_class"] and c - 1 in s["rate_class"] and s["rate_class"][c] != s["rate_class"][c - 1]}
    assert moved == {shift["shift_cycle"]}
    majors = {p["text"].split(" is the latest")[0].rsplit(" ", 1)[1].split(".")[0]
              for look in world["looks"].values() for p in look["pages"]}
    assert majors <= {"1", "7", "9"}


def test_the_decomposition_sums_to_the_gap_and_counts_every_look():
    row = cycles.run_world(DEV, 2601, frozenset(), cycles=5, generate=rate.generate)
    spec = DEV | {"cycles": 5}
    parts = cycles.decompose(row, spec, 2601, rate.generate)
    refs = cycles.references(spec, 2601, rate.generate)["series"]
    measured = sum(s["correct"] for s in row["series"][1:]) / 5
    for ref in ("round_robin", "oracle"):
        gap = parts[ref]["gap"]
        assert abs(gap - sum(parts[ref][k] for k in cycles.GAP_PARTS)) < 1e-9
        assert abs(gap - (sum(refs[ref][1:]) / 5 - measured)) < 1e-9          # the gap in PRIMARY's own units
    looks = parts["looks"]
    assert looks["total"] == len(row["looks"]) == sum(looks[k] for k in cycles.LOOK_PARTS)
    assert looks["allowance"] == sum(row["allowance"]) and looks["unused"] == looks["allowance"] - looks["total"]
    assert looks["total"] == sum(v for k, v in row["tasks"].items() if k != "owner")


def test_an_order_is_read_back_inside_the_run_changes_what_is_looked_at_and_has_its_verdict():
    configs = {k: v for k, v in cycles.order_configurations().items()
               if k in ("baseline", "recheck:staleness", "recheck:rotation", cycles.COMBINATION,
                        *cycles.ABLATIONS.values(), "recheck:rotation/all")}
    result = cycles.run_split("rate:development", configs=configs, cycles=4, seeds=(2601,))
    world = result["configurations"]["baseline"]["worlds"][DEV["name"]]
    combo = result["configurations"][cycles.COMBINATION]["worlds"][DEV["name"]]
    assert world["config_read_back"]["recheck_order"] == "staleness" and world["config_read_back"]["idle_recheck"] is False
    assert combo["config_read_back"]["recheck_order"] == "rate+seen+rotation"
    assert combo["config_read_back"]["idle_recheck"] is True and combo["config_read_back"]["recheck_per_tick"] == 100
    row = combo["per_seed"][0]
    assert row["activity.recheck_order_picks"] > 0 and row["activity.recheck_order_picks"] == row["activity.recheck_tasks"]
    assert result["configurations"]["recheck:staleness"]["worlds"][DEV["name"]]["per_seed"][0][
        "activity.recheck_order_picks"] == 0
    assert row["order_changed_launch_cycles"] is None             # lane K's order at that share was not run
    rotation = result["configurations"]["recheck:rotation"]["worlds"][DEV["name"]]["per_seed"][0]
    assert rotation["order_changed_launch_cycles"] is not None    # compared with recheck:staleness, same share
    assert "decomposition" in combo and set(combo["decomposition"]) >= {"round_robin", "oracle", "looks"}
    verdicts = cycles.order_verdicts(result)
    assert set(verdicts) == set(configs) - {"baseline"}
    assert verdicts[cycles.COMBINATION]["checks"]["d_safety"] in (True, False)
    assert verdicts["recheck:rotation"]["per_world"][DEV["name"]]["vs_blind_rotation"] == 0.0
