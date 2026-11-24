import numpy as np
import pytest

from neuroscifi.graph_store import decode, encode
from neuroscifi.instance import learner, store
from neuroscifi.instance.errors import ValidationError
from neuroscifi.lab import graph_bench


def _simulate_era_one(seed):
    """Run lab.graph_bench.simulate and capture the era-1 (epoch 0) world, dense and sparse matrices."""
    captured = {}

    def observe(era, world, dense, growing):
        if era == 0:
            captured.update(world=world, dense=dense, sparse=growing)

    graph_bench.simulate(seed, observe_epoch=observe)
    return captured


def _replay_era_one_inputs(seed):
    """Replay simulate's RNG calls in order to recover its era-1 inputs.

    simulate draws, from one Generator: world(rng) -> (a, b), then layout(rng) -> the frozen initial
    support (shared by its 'fixed' and 'growing' learners), then stream(a, STEPS, rng) -> (x, y).
    Repeating those calls on a fresh Generator with the same seed yields bit-identical inputs.
    """
    rng = np.random.default_rng(seed)
    a, _ = graph_bench.world(rng)
    initial = graph_bench.layout(rng)
    x, y = graph_bench.stream(a, graph_bench.STEPS, rng)
    return a, initial, x, y


@pytest.mark.parametrize("seed", [813240, 238194])
def test_learner_matches_graph_bench_simulate(seed):
    captured = _simulate_era_one(seed)
    a, initial, x, y = _replay_era_one_inputs(seed)
    np.testing.assert_array_equal(captured["world"], a)  # the replay reproduced simulate's stream
    assert graph_bench.N == 24 and graph_bench.K == 3
    # The dense learner does not depend on the initial support, so public fit() matches for any seed.
    fitted = learner.fit(x, y, k=3, seed=0)
    assert np.max(np.abs(fitted["dense"] - captured["dense"])) <= 1e-12
    # The sparse learner does: start it from simulate's frozen layout (fit() draws its own from `seed`).
    sparse = learner._learn(x, y, initial.copy())
    assert np.max(np.abs(sparse["dense"] - captured["dense"])) <= 1e-12
    assert np.max(np.abs(sparse["sparse"] - captured["sparse"])) <= 1e-12
    assert sparse["swaps"] > 0
    assert (sparse["support"].sum(axis=1) == 3).all()
    # It recovers the generator's support after the swaps.
    assert (sparse["support"] & (a != 0)).sum() / sparse["support"].sum() > 0.9


def test_fit_draws_initial_support_like_graph_bench_layout():
    np.testing.assert_array_equal(learner._layout(24, 3, np.random.default_rng(5)),
                                  graph_bench.layout(np.random.default_rng(5)))


def test_fit_generalises_to_other_population_counts():
    rng = np.random.default_rng(3)
    n = 7
    truth = np.zeros((n, n))
    for j in range(n):
        truth[j, (j + 1) % n] = 0.4
        truth[j, (j + 3) % n] = -0.3
    states = rng.normal(size=(800, n))
    targets = states @ truth.T + rng.normal(0, 0.05, size=(800, n))
    out = learner.fit(states, targets, k=2, seed=1)
    assert out["dense"].shape == out["sparse"].shape == (n, n)
    assert not np.diag(out["dense"]).any() and not np.diag(out["support"]).any()
    assert (out["support"].sum(axis=1) == 2).all()
    np.testing.assert_array_equal(out["support"], truth != 0)
    assert np.max(np.abs(out["dense"] - truth)) < 0.05
    # Degenerate sizes are handled: one population has no off-diagonal edge to learn.
    single = learner.fit(states[:, :1], targets[:, :1], k=3)
    assert single["support"].sum() == 0 and single["swaps"] == 0
    with pytest.raises(ValidationError):
        learner.fit(states, targets[:, :3])


def test_pack_selects_smallest_snapshot_within_tolerance():
    rng = np.random.default_rng(813240)
    a, _ = graph_bench.world(rng)
    x, y = graph_bench.stream(a, graph_bench.STEPS, np.random.default_rng(1))
    val = graph_bench.stream(a, 256, np.random.default_rng(2))
    dense = learner.fit(x, y)["dense"]
    rule = store.GATE_V1
    packed = learner.pack(dense, val, rule)
    assert packed["validation_mse"] <= 1.02 * packed["dense_validation_mse"]
    assert packed["byte_size"] == len(packed["blob"])
    eligible = [c for c in packed["candidates"] if c["eligible"]]
    assert packed["byte_size"] == min(c["byte_size"] for c in eligible)
    assert len(packed["candidates"]) == 1 + 5 * 2
    assert (packed["bits"], packed["degree"], packed["byte_size"]) == (4, 3, 247)
    np.testing.assert_allclose(learner.mse(decode(packed["blob"]), *val), packed["validation_mse"])
    # A zero tolerance can only keep candidates no worse than FP32, which always qualifies.
    strict = learner.pack(dense, val, dict(rule, validation_tolerance=0.0, pack_candidates={"degrees": [2],
                                                                                            "bits": [4]}))
    assert strict["bits"] == 32 and strict["degree"] == 24


# ---- the edge exchange for all receivers at once (learner._exchange) against the lab's loop ------------------------

def _learn_as_the_lab_wrote_it(states, targets, use):
    """learner._learn before the exchange was vectorised (lab.graph_bench.simulate for one stream), verbatim."""
    n = states.shape[1]
    dense = np.zeros((n, n))
    growing = dense.copy()
    score = dense.copy()
    swaps = 0
    for left in range(0, len(states), learner.WINDOW):
        xx = states[left:left + learner.WINDOW]
        yy = targets[left:left + learner.WINDOW]
        denom = (xx * xx).sum(axis=0) + learner.RIDGE
        for mat, mask in ((dense, None), (growing, use)):
            residual = yy - xx @ mat.T
            grad = (residual.T @ xx) / denom
            np.fill_diagonal(grad, 0)
            mat += learner.LEARNING_RATE * grad if mask is None else learner.LEARNING_RATE * grad * mask
            if mat is growing:
                score = learner.SCORE_KEEP * score + learner.SCORE_NEW * grad
        if (left // learner.WINDOW + 1) % learner.SWAP_EVERY == 0:
            for j in range(n):
                inside = np.where(use[j])[0]
                outside = np.where((~use[j]) & (np.arange(n) != j))[0]
                if not len(inside) or not len(outside):
                    continue
                drop = inside[np.argmin(abs(growing[j, inside]))]
                add = outside[np.argmax(abs(score[j, outside]))]
                if abs(score[j, add]) > learner.SWAP_RATIO * abs(growing[j, drop]) + learner.SWAP_MARGIN:
                    growing[j, drop] = 0
                    use[j, drop] = False
                    use[j, add] = True
                    growing[j, add] = 0
                    swaps += 1
    return {"dense": dense, "sparse": growing, "support": use, "swaps": swaps}


def _bitwise(a, b):
    """Every bit of both matrices (so -0.0 is not 0.0 and a NaN equals only the same NaN), support and swaps."""
    assert a["dense"].tobytes() == b["dense"].tobytes()
    assert a["sparse"].tobytes() == b["sparse"].tobytes()
    assert a["support"].dtype == b["support"].dtype == bool
    np.testing.assert_array_equal(a["support"], b["support"])
    assert a["swaps"] == b["swaps"] and isinstance(b["swaps"], int)


def _world_stream(n, true_k, steps, seed):
    rng = np.random.default_rng(seed)
    a = np.zeros((n, n))
    for j in range(n):
        if n > 1:
            idx = rng.choice(np.delete(np.arange(n), j), min(true_k, n - 1), replace=False)
            a[j, idx] = rng.choice([-1., 1.], len(idx)) * rng.uniform(.17, .25, len(idx))
    prev = np.zeros(n)
    states = np.empty((steps, n))
    targets = np.empty_like(states)
    for t in range(steps):
        residual = a @ prev + rng.normal(0, .065, n)
        states[t], targets[t] = prev, residual
        prev = .2 * prev + residual + rng.normal(0, .6, n) * (rng.random(n) < .22)
    return states, targets


@pytest.mark.parametrize("n,steps", [(2, 512), (3, 512), (24, 1536), (256, 768), (1024, 256)])
def test_learn_is_the_lab_loop_bit_for_bit(n, steps):
    swaps = 0
    for seed in ((0, 1, 2) if n <= 24 else (0,)):
        states, targets = _world_stream(n, 3, steps, seed)
        for k in (sorted({0, 1, min(3, n - 1), n - 1}) if n <= 24 else (3, n - 1) if n == 256 else (3,)):
            initial = learner._layout(n, k, np.random.default_rng(seed + 7))
            lab = _learn_as_the_lab_wrote_it(states, targets, initial.copy())
            _bitwise(lab, learner._learn(states, targets, initial.copy()))
            _bitwise(lab, learner._learn(states, targets, initial.copy(), exchange=learner._exchange_reference))
            assert (lab["support"].sum(axis=1) == k).all()             # an exchange keeps k inputs per receiver
            _bitwise(lab, learner.fit(states, targets, k=k, seed=seed + 7))   # fit() draws that layout from its seed
            swaps += lab["swaps"]
    assert swaps > 0 or n <= 3     # edges were exchanged, or this proves nothing (the smallest sizes exchange none)


@pytest.mark.parametrize("seed", [813240, 238194])
def test_learn_matches_graph_bench_simulate_exactly(seed):
    """The requirements matrix says 'max diff 0.0' (GRA-02); the older test above allows 1e-12."""
    captured = _simulate_era_one(seed)
    _, initial, x, y = _replay_era_one_inputs(seed)
    sparse = learner._learn(x, y, initial.copy())
    assert sparse["dense"].tobytes() == captured["dense"].tobytes()
    assert sparse["sparse"].tobytes() == captured["sparse"].tobytes()


def test_learn_on_streams_with_ties_and_zeros():
    rng = np.random.default_rng(11)
    n, steps = 12, 640
    states = rng.integers(-1, 2, size=(steps, n)).astype(float)       # values -1, 0, 1: equal scores are common
    states[:, 3] = states[:, 2]                                       # two identical inputs: tied scores every window
    states[:, 5] = 0.0                                                # an input that never varies: score exactly 0
    targets = np.roll(states, 1, axis=1) * .25
    targets[:, 7] = 0.0                                               # a receiver with nothing to learn
    swaps = 0
    for k in (1, 3, n - 2, n - 1):
        initial = learner._layout(n, k, np.random.default_rng(k))
        lab = _learn_as_the_lab_wrote_it(states, targets, initial.copy())
        _bitwise(lab, learner._learn(states, targets, initial.copy()))
        swaps += lab["swaps"]
    assert swaps > 0
    zeros = np.zeros((96, n))                                         # nothing to learn at all: no exchange, no NaN
    out = learner._learn(zeros, zeros, learner._layout(n, 3, np.random.default_rng(0)))
    _bitwise(_learn_as_the_lab_wrote_it(zeros, zeros, learner._layout(n, 3, np.random.default_rng(0))), out)
    assert out["swaps"] == 0 and not out["sparse"].any()


def _exchange_both(growing, score, use):
    a = (growing.copy(), score.copy(), use.copy())
    b = (growing.copy(), score.copy(), use.copy())
    swaps_a = learner._exchange_reference(*a)
    swaps_b = learner._exchange(*b)
    assert swaps_a == swaps_b
    assert a[0].tobytes() == b[0].tobytes()                      # growing, every bit
    assert a[1].tobytes() == b[1].tobytes() == score.tobytes()   # the score is read, never written
    np.testing.assert_array_equal(a[2], b[2])
    return swaps_b, b[0], b[2]


def test_exchange_breaks_ties_at_the_lowest_index_like_the_loop():
    use = np.array([[False, True, True, False, False],
                    [True, False, False, True, False],
                    [True, True, False, False, False],
                    [False, False, True, False, True],
                    [True, False, False, True, False]])
    growing = np.where(use, .1, 0.0)                       # every weight equal: the weakest is the lowest index
    score = np.full((5, 5), -.5)                           # every candidate equal: the best is the lowest index
    swaps, new_growing, new_use = _exchange_both(growing, score, use)
    assert swaps == 5
    np.testing.assert_array_equal(new_use[0], [False, False, True, True, False])   # dropped 1, added 3 (0 is self)
    np.testing.assert_array_equal(new_use[1], [False, False, True, True, False])   # dropped 0, added 2
    np.testing.assert_array_equal(new_use[4], [False, True, False, True, False])   # dropped 0, added 1
    assert new_growing[0, 1] == 0 and new_growing[0, 3] == 0 and new_growing[0, 2] == .1
    # exactly at the threshold nothing moves (strictly greater), a hair above it does
    edge = learner.SWAP_RATIO * .1 + learner.SWAP_MARGIN
    assert _exchange_both(growing, np.full((5, 5), edge), use)[0] == 0
    assert _exchange_both(growing, np.full((5, 5), np.nextafter(edge, 1)), use)[0] == 5


def test_exchange_agrees_with_the_loop_on_awkward_values():
    rng = np.random.default_rng(5)
    values = np.array([0.0, -0.0, .5, -.5, 1.0, -1.0, 1e-300, np.inf, -np.inf, np.nan])
    exchanged = 0
    for case in range(400):
        n = int(rng.integers(1, 10))
        k = int(rng.integers(0, n))                          # 0 .. n - 1 inputs per receiver
        use = learner._layout(n, k, rng)
        pool = values[:7] if case % 2 == 0 else values       # finite values, then with inf and NaN
        growing = np.where(use, rng.choice(pool, size=(n, n)), 0.0)
        exchanged += _exchange_both(growing, rng.choice(pool, size=(n, n)), use)[0]
    assert exchanged > 100
    # supports the vectorised path does not take: uneven rows, a receiver listed as its own input, an empty row
    for case in range(200):
        n = int(rng.integers(2, 9))
        use = rng.random((n, n)) < .4
        if case % 2:
            np.fill_diagonal(use, False)
        growing = np.where(use, rng.choice(values[:7], size=(n, n)), 0.0)
        _exchange_both(growing, rng.choice(values, size=(n, n)), use)
    assert _exchange_both(np.zeros((1, 1)), np.ones((1, 1)), np.zeros((1, 1), bool))[0] == 0


# ---- pack: gate-v1 unchanged, and the optional parts of a rule (learned_sparse, pack_reference) --------------------

def _pack_before(dense, validation, rule):
    """learner.pack as it was before a rule could name the learned sparse graph (commit 307b907), verbatim."""
    states, targets = validation
    dense = np.asarray(dense).astype("f4")
    baseline = learner.mse(dense, states, targets)
    cols = dense.shape[1]
    tolerance = float(rule["validation_tolerance"])
    options = [(cols, 32)] + [(degree, bits) for degree in rule["pack_candidates"]["degrees"]
                              for bits in rule["pack_candidates"]["bits"] if degree <= cols]
    candidates = []
    for degree, bits in options:
        blob = encode(dense, degree, bits)
        loss = learner.mse(decode(blob), states, targets)
        candidates.append({"bits": bits, "degree": degree, "byte_size": len(blob), "validation_mse": loss,
                           "eligible": loss <= (1 + tolerance) * baseline, "blob": blob})
    chosen = min((c for c in candidates if c["eligible"]), key=lambda c: c["byte_size"])
    return {"blob": chosen["blob"], "bits": chosen["bits"], "degree": chosen["degree"],
            "byte_size": chosen["byte_size"], "validation_mse": chosen["validation_mse"],
            "dense_validation_mse": baseline,
            "candidates": [{key: c[key] for key in ("bits", "degree", "byte_size", "validation_mse", "eligible")}
                           for c in candidates]}


SPARSE_TOO = dict(store.GATE_V1, version="sparse-too",
                  pack_candidates=dict(store.GATE_V1["pack_candidates"], learned_sparse=True))
BEST_LEARNER = dict(SPARSE_TOO, version="best-learner", pack_reference="best_learner")


def _fitted(n, true_k, seed, steps=1536):
    states, targets = _world_stream(n, true_k, steps + 256, seed)
    return learner.fit(states[:steps], targets[:steps], seed=seed), (states[steps:], targets[steps:])


def test_pack_under_a_rule_without_the_new_parts_is_what_it_was():
    rules = [store.GATE_V1, dict(store.GATE_V1, pack_candidates={"degrees": [6], "bits": [8]}),
             dict(store.GATE_V1, validation_tolerance=0.0), dict(store.GATE_V1, pack_reference="dense"),
             # (3, 8 bit) and (4, 4 bit) are files of one size: the first in the rule's order is taken, as before
             dict(store.GATE_V1, validation_tolerance=1.0, pack_candidates={"degrees": [3, 4], "bits": [8, 4]})]
    for n, true_k, seed in ((24, 3, 0), (24, 3, 1), (24, 6, 2), (24, 2, 3), (7, 2, 4), (96, 3, 5), (1, 0, 6)):
        fitted, val = _fitted(n, true_k, seed)
        for rule in rules:
            before = _pack_before(fitted["dense"], val, rule)
            assert learner.pack(fitted["dense"], val, rule) == before                       # no sparse matrix given
            assert learner.pack(fitted["dense"], val, rule, sparse=fitted["sparse"]) == before   # given and ignored


def test_pack_with_the_learned_sparse_graph_as_a_candidate():
    fitted, val = _fitted(24, 3, 0)
    before = _pack_before(fitted["dense"], val, store.GATE_V1)
    out = learner.pack(fitted["dense"], val, SPARSE_TOO, sparse=fitted["sparse"])
    assert [(c["learner"], c["degree"], c["bits"]) for c in out["candidates"]] == \
        [("dense", 24, 32)] + [("dense", d, b) for d in (2, 3, 4, 6, 12) for b in (4, 8)] + \
        [("sparse", 24, 32), ("sparse", 3, 4), ("sparse", 3, 8)]
    # the dense candidates are scored exactly as before, against the same reference
    assert [{k: v for k, v in c.items() if k != "learner"} for c in out["candidates"][:11]] == before["candidates"]
    assert out["reference"] == "dense" and out["reference_validation_mse"] == out["dense_validation_mse"]
    # two files of 247 bytes are eligible, one per learner: the lower validation error is taken
    same_size = [c for c in out["candidates"] if c["eligible"] and c["byte_size"] == 247]
    assert sorted(c["learner"] for c in same_size) == ["dense", "sparse"] and out["byte_size"] == 247
    assert out["validation_mse"] == min(c["validation_mse"] for c in same_size)
    assert out["learner"] == min(same_size, key=lambda c: c["validation_mse"])["learner"]
    assert learner.mse(decode(out["blob"]), *val) == out["validation_mse"]
    with pytest.raises(ValidationError, match="none was given"):
        learner.pack(fitted["dense"], val, SPARSE_TOO)
    with pytest.raises(ValidationError, match="different shapes"):
        learner.pack(fitted["dense"], val, SPARSE_TOO, sparse=fitted["sparse"][:, :5])
    # a sparse matrix with no edge at all (k = 0, or one population) is still a file
    empty = learner.pack(fitted["dense"], val, SPARSE_TOO, sparse=np.zeros((24, 24)))
    assert [c["degree"] for c in empty["candidates"] if c["learner"] == "sparse"] == [24, 1, 1]
    single, single_val = _fitted(1, 0, 6)
    assert learner.pack(single["dense"], single_val, BEST_LEARNER, sparse=single["sparse"])["byte_size"] > 0


def test_pack_measures_the_tolerance_against_the_better_learner():
    # 96 populations: the sparse learner reaches the noise floor, the dense one about twice its error
    fitted, val = _fitted(96, 3, 5)
    gate = _pack_before(fitted["dense"], val, store.GATE_V1)
    out = learner.pack(fitted["dense"], val, BEST_LEARNER, sparse=fitted["sparse"])
    sparse_fp32 = learner.mse(fitted["sparse"].astype("f4"), *val)
    assert out["reference"] == "best_learner" and out["reference_validation_mse"] == sparse_fp32
    assert sparse_fp32 < .6 * out["dense_validation_mse"]
    assert (out["learner"], out["degree"], out["bits"]) == ("sparse", 3, 4)
    assert out["validation_mse"] < .6 * gate["validation_mse"]            # against the cut gate-v1 takes
    assert out["validation_mse"] <= 1.02 * sparse_fp32
    by = {(c["learner"], c["degree"], c["bits"]): c for c in out["candidates"]}
    assert by[("sparse", 96, 32)]["eligible"] and by[("sparse", 96, 32)]["validation_mse"] == sparse_fp32
    assert not by[("dense", 96, 32)]["eligible"]
    # the edge of the tolerance: a hair above the 4-bit file's loss it is in, a hair below it is out
    loss = by[("sparse", 3, 4)]["validation_mse"] / sparse_fp32 - 1
    assert 0 < loss < .02
    inside = learner.pack(fitted["dense"], val, dict(BEST_LEARNER, validation_tolerance=loss * (1 + 1e-9)),
                          sparse=fitted["sparse"])
    outside = learner.pack(fitted["dense"], val, dict(BEST_LEARNER, validation_tolerance=loss * (1 - 1e-9)),
                           sparse=fitted["sparse"])
    assert (inside["learner"], inside["bits"]) == ("sparse", 4)
    assert (outside["learner"], outside["bits"]) == ("sparse", 8)         # the next smallest file that is within
    # at zero tolerance only a file at the reference qualifies: the better learner's FP32 graph, so there is a choice
    strict = learner.pack(fitted["dense"], val, dict(BEST_LEARNER, validation_tolerance=0.0), sparse=fitted["sparse"])
    assert strict["validation_mse"] <= sparse_fp32 and strict["learner"] == "sparse"
    # where the dense learner is the better one, the reference and the choice are gate-v1's
    six, six_val = _fitted(24, 6, 2)
    dense_wins = learner.pack(six["dense"], six_val, BEST_LEARNER, sparse=six["sparse"])
    before = _pack_before(six["dense"], six_val, store.GATE_V1)
    assert dense_wins["learner"] == "dense" and dense_wins["blob"] == before["blob"]
    assert dense_wins["reference_validation_mse"] == before["dense_validation_mse"]
