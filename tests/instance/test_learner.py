import numpy as np
import pytest

from neuroscifi.graph_store import decode
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
