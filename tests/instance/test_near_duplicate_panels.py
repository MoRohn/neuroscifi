"""Near-duplicate panels must not be offered as the earlier setup (post-hoc probe kept as a regression test).

Found after D-1 (docs/quality/PREREGISTRATION_D1.md): the brief's rewired world (one of three inputs per
population changed, p.8) with identical calibration bits and root names is the hardest negative control.
The old graph beats the zero predictor there but not its own calibrated error, so the Gaussian likelihood
rejects it. Uses tmp_path; 4 seeds per variant.
"""
import pytest
from pathlib import Path
import numpy as np
from neuroscifi.lab import graph_bench as gb
from neuroscifi.instance import store, datasets, graphs, evidence, beliefs

def run(world_seed, variant, tmp):
    rng = np.random.default_rng(world_seed)
    a, b = gb.world(rng)
    if variant == "fresh":
        target, _ = gb.world(np.random.default_rng(world_seed + 999_999))
    elif variant == "rewired":
        target = b
    else:
        target = a
    t0 = 1_800_000_000.0
    db = store.create(Path(tmp) / f"{variant}-{world_seed}.sqlite", t0)
    r = np.random.default_rng([world_seed, 1])
    tr = gb.stream(a, gb.STEPS, r); va = gb.stream(a, 256, r); ho = gb.stream(a, 512, r)
    ids = [datasets.add(db, "panel-A", s, x, y, "simulated", t0) for s, (x, y) in
           (("train", tr), ("validation", va), ("heldout", ho))]
    rev = graphs.learn(db, "panel-A", t0 + 1, train=ids[0], validation=ids[1], holder="probe")
    assert graphs.promote(db, rev, t0 + 2)["promoted"]
    evidence.start_epoch(db, "panel-A", t0, context="panel-A")
    n = 0
    def obs(ep, ch, act, bit, root):
        nonlocal n; n += 1
        evidence.observe(db, {"id": f"e{n}", "epoch": ep, "channel": ch, "action": act, "bit": bit,
                              "root": root, "provenance": "simulated"}, t0 + n)
    for k in range(8):
        obs("panel-A", "camera", k % 2, k % 2, "cam"); obs("panel-A", "word", k % 2, k % 2, "alice")
    for j in range(3):
        obs("panel-A", "reference", 0, 0, f"std{j}")
    evidence.start_epoch(db, "ret", t0 + 100)
    obs("ret", "camera", 0, 0, "cam"); obs("ret", "word", 1, 1, "alice")   # identical bits: worst case
    x, y = gb.stream(target, 12, np.random.default_rng([world_seed, 13]))
    evidence.attach_stream(db, "ret", x, y, t0 + 200, provenance="simulated")
    m = beliefs.match_epochs(db, "ret")
    top = m["ranking"][0] if m["ranking"] else {}
    db.close()
    return m["best"] is not None, top.get("posterior", 0.0), top.get("dynamics_log_likelihood", 0) - m["new_setup_dynamics_log_likelihood"]



@pytest.mark.parametrize("variant,offered", [("same", True), ("rewired", False), ("fresh", False)])
def test_only_the_same_panel_is_offered(tmp_path, variant, offered):
    for seed in range(840100, 840104):
        got, posterior, margin = run(seed, variant, tmp_path)
        assert got is offered, (variant, seed, posterior, margin)
        assert (margin > 0) is offered
