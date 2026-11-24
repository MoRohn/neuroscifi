import hashlib

import numpy as np
import pytest

from neuroscifi.instance import datasets, store
from neuroscifi.instance.errors import Conflict, Gone, NotFound, ValidationError
from neuroscifi.lab import graph_bench

T0 = 1_800_000_000.0


def _arrays(rows=16, cols=4, seed=0):
    rng = np.random.default_rng(seed)
    return rng.normal(size=(rows, cols)), rng.normal(size=(rows, cols))


def test_add_and_load_round_trip_exactly(instance):
    db, _ = instance
    states, targets = _arrays()
    before = store.state_version(db)
    dataset_id = datasets.add(db, "desk", "train", states, targets, "observed", T0, note="bench")
    assert store.state_version(db) == before + 1
    got_states, got_targets = datasets.load(db, dataset_id)
    assert got_states.dtype == np.float64
    np.testing.assert_array_equal(got_states, states)
    np.testing.assert_array_equal(got_targets, targets)
    (row,) = datasets.listing(db, "desk")
    blob = db.execute("SELECT blob FROM datasets WHERE id=?", (dataset_id,)).fetchone()[0]
    assert row["rows"] == 16 and row["cols"] == 4 and row["split"] == "train" and row["bytes"] == len(blob)
    assert row["sha256"] == hashlib.sha256(blob).hexdigest()
    assert datasets.listing(db, "elsewhere") == []


@pytest.mark.parametrize("states, targets", [
    (np.zeros((16, 4)), np.zeros((16, 3))),            # shape mismatch
    (np.zeros(16), np.zeros(16)),                      # not 2-D
    (np.zeros((7, 4)), np.zeros((7, 4))),              # too few steps
    (np.zeros((16, 1025)), np.zeros((16, 1025))),      # too many populations
    (np.full((16, 4), np.nan), np.zeros((16, 4))),     # not finite
    (np.full((16, 4), np.inf), np.zeros((16, 4))),
])
def test_add_rejects_malformed_arrays(instance, states, targets):
    db, _ = instance
    with pytest.raises(ValidationError):
        datasets.add(db, "desk", "train", states, targets, "observed", T0)
    assert datasets.listing(db) == []


def test_a_shape_whose_stored_blob_would_exceed_the_sqlite_length_limit_is_refused_before_it_is_encoded():
    """Two float64 arrays of rows x cols take 16 * rows * cols bytes; SQLite refuses a blob over 1e9 bytes with a raw
    DataError, and the row and column bounds alone allow 3.3 GB. Zero-stride views stand in for the arrays, so the
    test allocates no data (the oversized shape is refused before the finite check and the encoding)."""
    limit, cols = 1_000_000_000, datasets.MAX_COLS          # SQLITE_MAX_LENGTH, the default build limit
    assert 16 * datasets.MAX_ROWS * cols > limit
    too_big = limit // (16 * cols) + 1                       # the arrays alone already exceed the limit
    view = np.broadcast_to(np.float64(0.0), (too_big, cols))
    with pytest.raises(ValidationError, match="too large"):
        datasets._check_arrays(view, view)
    fits = (limit - 4096) // (16 * cols)                     # room for the npz framing (well under 4 kB)
    view = np.broadcast_to(np.float64(0.0), (fits, cols))    # the finite check reads it: 64 MB, no blob
    assert datasets._check_arrays(view, view)[0].shape == (fits, cols)
    blob = datasets._encode(np.zeros((16, cols)), np.zeros((16, cols)))
    assert len(blob) - 16 * 16 * cols < 4096


def test_add_rejects_bad_labels(instance):
    db, _ = instance
    states, targets = _arrays()
    for context, split, provenance in [("desk", "test", "observed"), ("desk", "train", "imagined"),
                                       ("", "train", "observed"), ("x" * 65, "train", "observed")]:
        with pytest.raises(ValidationError):
            datasets.add(db, context, split, states, targets, provenance, T0)


def test_load_missing_deleted_and_tampered(instance):
    db, _ = instance
    with pytest.raises(NotFound):
        datasets.load(db, 99)
    states, targets = _arrays()
    first = datasets.add(db, "desk", "train", states, targets, "observed", T0)
    second = datasets.add(db, "desk", "heldout", states, targets, "observed", T0)
    with store.write(db):
        db.execute("UPDATE datasets SET blob=NULL, deleted_at=? WHERE id=?", (T0 + 1, first))
        db.execute("UPDATE datasets SET sha256=? WHERE id=?", ("0" * 64, second))
    with pytest.raises(Gone):
        datasets.load(db, first)
    with pytest.raises(Conflict):
        datasets.load(db, second)


def test_load_validates_stored_arrays(instance):
    db, _ = instance
    states, targets = _arrays()
    dataset_id = datasets.add(db, "desk", "train", states, targets, "observed", T0)
    bad = datasets._encode(np.full((16, 4), np.nan), targets)
    with store.write(db):
        db.execute("UPDATE datasets SET blob=?, sha256=? WHERE id=?",
                   (bad, hashlib.sha256(bad).hexdigest(), dataset_id))
    with pytest.raises(ValidationError):
        datasets.load(db, dataset_id)


def test_simulate_uses_documented_seed_derivation(instance):
    db, _ = instance
    ids = datasets.simulate(db, "desk", T0, seed=7, steps=320)
    assert set(ids) == {"train", "validation", "heldout"}
    a, b = graph_bench.world(np.random.default_rng(7))
    expected = {"train": (0, 320), "validation": (1, 256), "heldout": (2, 512)}
    for split, (index, length) in expected.items():
        states, targets = datasets.load(db, ids[split])
        want = graph_bench.stream(a, length, np.random.default_rng([7, 0, index]))
        np.testing.assert_array_equal(states, want[0])
        np.testing.assert_array_equal(targets, want[1])
        assert states.shape == (length, graph_bench.N)
    # The world is linear: targets are explained by the generating matrix up to the 0.065 noise.
    states, targets = datasets.load(db, ids["heldout"])
    assert abs(graph_bench.mse(a, (states, targets)) - 0.065 ** 2) < 0.001
    # 'b' is the rewired version of the same world, on its own streams.
    ids_b = datasets.simulate(db, "desk", T0, seed=7, world="b", steps=320)
    states_b, targets_b = datasets.load(db, ids_b["heldout"])
    assert abs(graph_bench.mse(b, (states_b, targets_b)) - 0.065 ** 2) < 0.001
    rows = {r["id"]: r for r in datasets.listing(db)}
    assert len({r["sha256"] for r in rows.values()}) == 6
    assert all(r["provenance"] == "simulated" for r in rows.values())
    # Deterministic for a given seed.
    again = datasets.simulate(db, "other", T0, seed=7, steps=320)
    rows = {r["id"]: r for r in datasets.listing(db)}
    assert all(rows[again[split]]["sha256"] == rows[ids[split]]["sha256"] for split in ids)


def test_simulate_rejects_bad_arguments(instance):
    db, _ = instance
    for kwargs in [{"seed": -1}, {"seed": 1, "world": "c"}, {"seed": 1, "steps": 4}, {"seed": 1.5}]:
        with pytest.raises(ValidationError):
            datasets.simulate(db, "desk", T0, **kwargs)
    assert datasets.listing(db) == []
