"""Wave 4 lane A (ADR-064): the division of the day's allowance in the learning-cycles harness: its worlds, its
configurations read back as named, the division exercised inside the allowance, and the pre-registered rule."""
import pytest

from neuroscifi.acceptance.cognition import cycles, cycles_reach_splits as reach

SEEDS_ELSEWHERE = {41, 42, 43, 141, 142, 143, 144, 241, 242, 243, 341, 342, 343, 344, 2601, 2602, 2603, 2701, 2702,
                   2703, 2704, 4401, 4402, 4403, 4501, 4502, 4503, 4504, 7341, 7342, 7343, 7344} | set(range(5001, 5013))


def test_the_worlds_state_their_kind_and_their_seeds_are_new():
    dev, held = reach.CYCLES["development"], reach.CYCLES["held_out"]
    assert not (set(dev["seeds"]) | set(held["seeds"])) & SEEDS_ELSEWHERE
    assert not set(dev["seeds"]) & set(held["seeds"])
    for split in (dev, held):
        assert {w["kind"] for w in split["worlds"]} == {"reach", "covered", "mixed", "shift"}
    world = reach.generate(dev["worlds"][0], dev["seeds"][0])
    thin = [s for s in world["subjects"] if s["thin"]]
    assert thin and all(world["looks"][(s["name"], s["born"])]["kind"] == "one_origin" for s in thin)
    assert reach.generate(dev["worlds"][0], dev["seeds"][0]) == world          # written whole, the same every time


def _run(config, cycles_=4):
    world = reach.CYCLES["development"]["worlds"][0]
    known = cycles.division_configurations()
    return cycles.run_world(world, reach.CYCLES["development"]["seeds"][0], known[config], cycles=cycles_,
                            generate=reach.generate)


def test_the_division_reads_back_as_named_and_stays_inside_the_allowance():
    off, on = _run("shipped+idle"), _run("division:reserve-0.5")
    assert (off["config"]["allowance_division"], on["config"]["allowance_division"]) == ("race", "reserve")
    assert on["config"]["reach_share"] == 0.5 and on["config"]["idle_investigation"] == "after the pass"
    assert off["activity"]["division_held_places"] == 0 and off["activity"]["division_reach_tasks"] == 0
    assert on["activity"]["division_held_places"] > 0 and on["activity"]["division_reach_tasks"] > 0
    for row in (off, on):           # never more autonomous looks in a cycle than that cycle's allowance
        per = row["diagnosis"]["per_cycle"]
        assert all(r["timer"] + r["idle"] <= r["allowance"] for r in per)
    # the division changed what the loop looked at, against its reference, on the same world
    assert [(c, k, n) for c, k, n in on["launches"]] != [(c, k, n) for c, k, n in off["launches"]]


def _looks_with_ids(seed, scheme):
    """A development run (rd-covered, the idle investigation first) with task ids drawn from `scheme` (an integer:
    another set of ids, in another sort order, for each value)."""
    import hashlib
    import itertools
    import uuid
    from unittest import mock
    counter = itertools.count(1)

    def make():
        return uuid.UUID(bytes=hashlib.sha256(f"{scheme}:{next(counter)}".encode()).digest()[:16], version=4)
    world = [w for w in reach.CYCLES["development"]["worlds"] if w["name"] == "rd-covered"][0]
    with mock.patch("uuid.uuid4", make):
        row = cycles.run_world(world, seed, frozenset({cycles.IDLE_FIRST}), generate=reach.generate)
    return [(lk["cycle"], lk["kind"], lk["subject"], lk["after"]) for lk in row["looks"]], row["mean"]["correct"]


@pytest.mark.parametrize("seed", (6402, 6403))
def test_the_same_history_gives_the_same_run_whatever_the_task_ids(seed):
    """Task ids are random (uuid4). Before Wave 4 lane A's fix two choices followed their order, so repeats of the
    same world and seed differed: autonomy._lineage walked a set of task ids (a different lineage, so the idle
    investigation took another candidate; seed 6402), and curiosity.rank took, among claims stored at the same
    instant, the first in the (key, task_id) index (another sentence, so another objective; seed 6403). Eight sets of
    ids must give one run."""
    first = _looks_with_ids(seed, 0)
    assert all(_looks_with_ids(seed, k) == first for k in range(1, 8))


def _result(rows):
    """A run_split-shaped result: {config: {world: (kind, [primary per seed], safety extra per seed)}}."""
    confs = {}
    for config, worlds in rows.items():
        confs[config] = {"worlds": {}}
        for w, (kind, primary, extra) in worlds.items():
            per_seed = [{cycles.PRIMARY: p, "mean.coverage": 0.5, "retention.lost": 0, "retention.regressed": 0,
                         "self_admitted": 0, "wire_admitted": 0, "one_origin_rises": 0, "admitted.wrong": x,
                         "division_changed_launch_cycles": 1, "activity.division_reach_tasks": 1,
                         "activity.division_held_places": 1} for p, x in zip(primary, extra)]
            sd = cycles.scoring.spread(primary)
            confs[config]["worlds"][w] = {"kind": kind, "per_seed": per_seed, "spread": {cycles.PRIMARY: sd}}
    return {"configurations": confs}


REF = {"r": ("reach", [0.50, 0.52, 0.48], [0, 0, 0]), "c": ("covered", [0.60, 0.61, 0.59], [0, 0, 0])}


@pytest.mark.parametrize("reach_world, covered_world, verdict", [
    (("reach", [0.60, 0.62, 0.58], [0, 0, 0]), ("covered", [0.60, 0.61, 0.59], [0, 0, 0]), "gain"),
    (("reach", [0.60, 0.62, 0.58], [0, 1, 0]), ("covered", [0.60, 0.61, 0.59], [0, 0, 0]), "unsafe"),
    (("reach", [0.60, 0.62, 0.58], [0, 0, 0]), ("covered", [0.50, 0.51, 0.49], [0, 0, 0]), "harm"),
    (("reach", [0.51, 0.53, 0.48], [0, 0, 0]), ("covered", [0.60, 0.61, 0.59], [0, 0, 0]), "revise"),
    (("reach", [0.50, 0.52, 0.48], [0, 0, 0]), ("covered", [0.60, 0.61, 0.59], [0, 0, 0]), "no evidence"),
])
def test_the_rule_decides_as_it_was_written(reach_world, covered_world, verdict):
    result = _result({cycles.DIVISION_REFERENCE: REF, "division:reserve-0.5": {"r": reach_world, "c": covered_world},
                      "division:yield": REF})
    out = cycles.division_verdicts(result)
    assert out["division:reserve-0.5"]["verdict"] == verdict
    choice = cycles.division_choice(out)
    if verdict in ("gain", "revise"):
        assert choice["chosen"] == "division:reserve-0.5"
    else:
        assert choice["chosen"] is None
