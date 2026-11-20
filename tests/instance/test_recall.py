import numpy as np
import pytest

from neuroscifi.graph_store import decode, encode
from neuroscifi.instance import datasets, graphs, recall, store
from neuroscifi.instance.errors import NotFound
from neuroscifi.lab import graph_bench

T0 = 1_800_000_000.0
SEED = 840091


def _promote(db, context, world, now):
    ids = datasets.simulate(db, context, now, seed=SEED, world=world)
    rev = graphs.learn(db, context, now + 1, train=ids["train"], validation=ids["validation"], holder="tester")
    assert graphs.promote(db, rev, now + 2)["promoted"]
    return rev


@pytest.fixture
def two_contexts(instance):
    """'desk' learned from world a, 'remounted' from its rewired twin b (the lab's 64-pair setup)."""
    db, path = instance
    desk = _promote(db, "desk", "a", T0)
    remounted = _promote(db, "remounted", "b", T0 + 10)
    return db, {"desk": desk, "remounted": remounted}


def _blob(db, rev):
    return db.execute("SELECT blob FROM graph_revisions WHERE id=?", (rev,)).fetchone()[0]


def test_recall_follows_alternating_contexts(two_contexts):
    db, revs = two_contexts
    a, b = graph_bench.world(np.random.default_rng(SEED))
    rng = np.random.default_rng(SEED + 700000)
    parts, truth = [], []
    # As in lab.context_recall: start in the most recent world and switch three times.
    for context, matrix in (("remounted", b), ("desk", a), ("remounted", b), ("desk", a)):
        parts.append(graph_bench.stream(matrix, 128, rng))
        truth += [context] * 128
    states = np.vstack([p[0] for p in parts])
    targets = np.vstack([p[1] for p in parts])
    probe = datasets.add(db, "probe", "heldout", states, targets, "simulated", T0 + 20)
    replay = store.counter(db, "replay_updates")
    revisions = store.counter(db, "state_version"), len(graphs.listing(db))

    out = recall.run(db, probe, T0 + 21)

    chosen = [run["context"] for run in out["choices"] for _ in range(run["from"], run["to"])]
    accuracy = float(np.mean([c == t for c, t in zip(chosen, truth)]))
    matrices = {name: decode(_blob(db, rev)) for name, rev in revs.items()}
    oracle = float(np.mean([np.mean((states[t] @ matrices[truth[t]].T - targets[t]) ** 2)
                            for t in range(len(truth))]))
    assert out["steps"] == len(chosen) == 512
    assert out["selected_mse"] < 0.6 * out["latest_only_mse"]
    assert out["selected_mse"] < 1.15 * oracle
    assert accuracy > 0.9
    assert out["unknown_fraction"] < 0.05 and out["verdict"].startswith("known:")
    assert len(out["choices"]) >= 4 and out["pending_id"] is None
    assert store.counter(db, "replay_updates") == replay + 1
    assert len(graphs.listing(db)) == revisions[1]
    # Restricting the competitors is honoured.
    only = recall.run(db, probe, T0 + 22, contexts=["desk"], record=False)
    assert {run["context"] for run in only["choices"]} == {"desk"}
    with pytest.raises(NotFound):
        recall.run(db, probe, T0 + 22, contexts=["nowhere"], record=False)


def test_unrelated_world_is_unknown_and_asks_without_creating_a_graph(two_contexts):
    db, revs = two_contexts
    unrelated, _ = graph_bench.world(np.random.default_rng(SEED + 1))
    states, targets = graph_bench.stream(unrelated, 512, np.random.default_rng(5))
    probe = datasets.add(db, "probe", "heldout", states, targets, "simulated", T0 + 20)
    before = db.execute("SELECT count(*) FROM graph_revisions").fetchone()[0]

    dry = recall.run(db, probe, T0 + 21, record=False)
    assert dry["verdict"] == "unknown_context" and dry["pending_id"] is None
    assert db.execute("SELECT count(*) FROM pending").fetchone()[0] == 0

    out = recall.run(db, probe, T0 + 22)
    assert out["verdict"] == "unknown_context" and out["unknown_fraction"] > 0.9
    row = db.execute("SELECT kind, scope, context, question, resolved_at FROM pending WHERE id=?",
                     (out["pending_id"],)).fetchone()
    assert row[:3] == ("new_context", "context:unknown", "probe") and row[4] is None
    assert f"Dataset {probe}" in row[3] and "new context" in row[3]
    # The same evidence does not open a second question, and no graph is ever created by recall.
    assert recall.run(db, probe, T0 + 23)["pending_id"] == out["pending_id"]
    assert db.execute("SELECT count(*) FROM pending").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM graph_revisions").fetchone()[0] == before


def test_a_founding_graph_without_validation_cannot_vouch_for_an_unrelated_world(tmp_path):
    a, _ = graph_bench.world(np.random.default_rng(SEED))
    db = store.create(tmp_path / "born.sqlite", T0, founding_graph=encode(a, 3, 4))
    try:
        unrelated, _ = graph_bench.world(np.random.default_rng(SEED + 1))
        states, targets = graph_bench.stream(unrelated, 512, np.random.default_rng(5))
        probe = datasets.add(db, "probe", "heldout", states, targets, "simulated", T0 + 1)
        out = recall.run(db, probe, T0 + 2)
        assert out["competitors"] == [{"context": "founding", "revision_id": 1, "validation_mse": None}]
        assert out["verdict"] == "unknown_context" and out["unknown_fraction"] == 1.0
        assert out["pending_id"] is not None
    finally:
        db.close()
