import json
import sqlite3

import numpy as np
import pytest

from neuroscifi import cli
from neuroscifi.graph_store import decode, encode
from neuroscifi.instance import datasets, graphs, rules, store
from neuroscifi.instance.errors import Conflict, Gone, LeaseHeld, NotFound, ValidationError
from neuroscifi.lab import graph_bench

T0 = 1_800_000_000.0
SEED = 813240


def _learn(db, context, now, *, seed=SEED, world="a", steps=1536, holder="tester"):
    ids = datasets.simulate(db, context, now, seed=seed, world=world, steps=steps)
    rev = graphs.learn(db, context, now + 1, train=ids["train"], validation=ids["validation"], holder=holder)
    return rev, ids


def _status(db, rev):
    return db.execute("SELECT status FROM graph_revisions WHERE id=?", (rev,)).fetchone()[0]


def _add_rule(db, now, **changes):
    definition = dict(store.GATE_V1, **changes)
    with store.write(db):
        rules.register(db, definition, now, created_by="owner")
    return definition["version"]


# ---- 2. end to end ------------------------------------------------------------------------------------

def test_simulate_learn_evaluate_promote(instance):
    db, _ = instance
    replay = store.counter(db, "replay_updates")
    rev, ids = _learn(db, "desk", T0)
    shown = graphs.show(db, rev)
    assert shown["status"] == "candidate" and shown["origin"] == "learner" and shown["rule_version"] == "gate-v1"
    assert {(row["dataset_id"], row["role"]) for row in shown["lineage"]} == {(ids["train"], "train"),
                                                                             (ids["validation"], "validation")}
    metrics = shown["metrics"]
    pack = metrics["pack"]
    assert pack["validation_mse"] <= 1.02 * pack["dense_validation_mse"]
    assert shown["byte_size"] <= store.GATE_V1["byte_budget"] and shown["byte_size"] <= 400
    assert (shown["bits"], shown["degree"], shown["byte_size"]) == (4, 3, 247)
    assert metrics["sparse"]["edges"] == 72 and metrics["sparse"]["swaps"] > 0
    assert metrics["sparse"]["support_agreement_with_snapshot"] > 0.9
    # The packed snapshot keeps the generator's edges.
    a, _ = graph_bench.world(np.random.default_rng(SEED))
    support = np.array(shown["support"])
    assert (support & (a != 0)).sum() / support.sum() > 0.9
    assert np.allclose(np.array(shown["matrix"]), np.round(decode(
        db.execute("SELECT blob FROM graph_revisions WHERE id=?", (rev,)).fetchone()[0]), 6), atol=1e-6)

    evaluation = graphs.evaluate(db, rev, T0 + 2)
    assert [row["dataset_id"] for row in evaluation["heldout"]] == [ids["heldout"]]
    assert evaluation["heldout_mse"] < 0.006
    assert graphs.show(db, rev)["metrics"]["heldout_mse"] == evaluation["heldout_mse"]

    result = graphs.promote(db, rev, T0 + 3)
    decision = result["decision"]
    assert result["promoted"] and decision["passed"] == list(graphs.CHECKS) and decision["failed"] == []
    assert decision["incumbent"] is None and decision["rule_version"] == "gate-v1"
    assert decision["checks"]["byte_budget"] == {"passed": True, "byte_size": 247, "byte_budget": 4096}
    assert _status(db, rev) == "active"
    stored = json.loads(db.execute("SELECT decision FROM graph_revisions WHERE id=?", (rev,)).fetchone()[0])
    assert stored == decision
    assert store.counter(db, "replay_updates") == replay + 3   # learn, evaluate, promote
    with pytest.raises(Conflict):
        graphs.promote(db, rev, T0 + 4)
    assert db.execute("SELECT count(*) FROM graph_leases").fetchone()[0] == 0


# ---- 3. the gate --------------------------------------------------------------------------------------

def test_gate_a_rejects_without_independent_heldout(instance):
    db, _ = instance
    ids = datasets.simulate(db, "desk", T0, seed=SEED)
    train = datasets.load(db, ids["train"])
    val = datasets.load(db, ids["validation"])
    t = datasets.add(db, "bare", "train", *train, "simulated", T0)
    v = datasets.add(db, "bare", "validation", *val, "simulated", T0)
    # Re-labelling the training episodes as held-out does not count as independent data.
    copy = datasets.add(db, "bare", "heldout", *train, "simulated", T0)
    rev = graphs.learn(db, "bare", T0 + 1, train=t, validation=v, holder="tester")
    result = graphs.promote(db, rev, T0 + 2)
    checks = result["decision"]["checks"]
    assert not result["promoted"] and result["decision"]["failed"][0] == "independent_heldout"
    assert checks["independent_heldout"]["passed"] is False
    assert checks["independent_heldout"]["excluded"][0]["dataset_id"] == copy
    assert checks["heldout_gain"]["passed"] is None and checks["retention"]["passed"] is None
    assert _status(db, rev) == "rejected"
    assert graphs.evaluate(db, rev, T0 + 3)["heldout_mse"] is None


def test_gate_b_keeps_incumbent_when_candidate_is_no_better(instance):
    db, _ = instance
    first, ids = _learn(db, "desk", T0)
    assert graphs.promote(db, first, T0 + 2)["promoted"]
    # Learning again from the same episodes gives the same graph: no held-out gain.
    again = graphs.learn(db, "desk", T0 + 3, train=ids["train"], validation=ids["validation"], holder="tester")
    result = graphs.promote(db, again, T0 + 4)
    gain = result["decision"]["checks"]["heldout_gain"]
    assert not result["promoted"] and result["decision"]["failed"] == ["heldout_gain"]
    assert gain["candidate_mse"] == gain["incumbent_mse"] and gain["threshold"] == gain["incumbent_mse"]
    assert result["decision"]["incumbent"] == first
    assert _status(db, first) == "active" and _status(db, again) == "rejected"


def test_gate_c_rewired_world_fails_retention_and_new_context_promotes(instance):
    db, _ = instance
    first, ids_a = _learn(db, "desk", T0)
    assert graphs.promote(db, first, T0 + 2)["promoted"]
    rewired, ids_b = _learn(db, "desk", T0 + 10, world="b")
    result = graphs.promote(db, rewired, T0 + 12)
    decision = result["decision"]
    gain, retention = decision["checks"]["heldout_gain"], decision["checks"]["retention"]
    assert decision["heldout"] == {"current": [ids_b["heldout"]], "older": [ids_a["heldout"]]}
    assert gain["passed"] and gain["candidate_mse"] < 0.5 * gain["incumbent_mse"]
    (old,) = retention["datasets"]
    assert old["dataset_id"] == ids_a["heldout"] and old["candidate_mse"] > 2 * old["incumbent_mse"]
    assert not retention["passed"] and decision["failed"] == ["retention"]
    assert decision["recommendation"] == graphs.NEW_CONTEXT_ADVICE
    assert not result["promoted"] and _status(db, first) == "active" and _status(db, rewired) == "rejected"
    # Registered as its own context, the rewired world gets its own graph and the old one stays active.
    remounted, _ = _learn(db, "desk-remounted", T0 + 20, world="b")
    assert graphs.promote(db, remounted, T0 + 22)["promoted"]
    active = {r["context"]: r["id"] for r in graphs.listing(db) if r["status"] == "active"}
    assert active == {"desk": first, "desk-remounted": remounted}


def test_gate_d_owner_rule_budget_and_rule_version_is_frozen_at_learn(instance):
    db, _ = instance
    before, _ = _learn(db, "desk", T0)
    _add_rule(db, T0 + 5, version="tight-budget", byte_budget=128)
    assert rules.latest(db) == "tight-budget"
    after = graphs.learn(db, "desk", T0 + 6, train=1, validation=2, holder="tester")
    assert graphs.show(db, before)["rule_version"] == "gate-v1"
    assert graphs.show(db, after)["rule_version"] == "tight-budget"
    result = graphs.promote(db, after, T0 + 7)
    budget = result["decision"]["checks"]["byte_budget"]
    assert not result["promoted"] and result["decision"]["failed"] == ["byte_budget"]
    assert budget == {"passed": False, "byte_size": 247, "byte_budget": 128}
    assert result["decision"]["rule_version"] == "tight-budget"
    # The earlier candidate is still judged by the rule it was created under.
    early = graphs.promote(db, before, T0 + 8)
    assert early["promoted"] and early["decision"]["thresholds"]["byte_budget"] == 4096


def test_gate_e_rule_version_and_blob_are_immutable(instance):
    db, _ = instance
    rev, _ = _learn(db, "desk", T0)
    _add_rule(db, T0 + 5, version="lenient", min_heldout_gain=0.0, retention_tolerance=1.0)
    for sql, value in [("UPDATE graph_revisions SET rule_version=? WHERE id=?", "lenient"),
                       ("UPDATE graph_revisions SET blob=? WHERE id=?", b"NSG1"),
                       ("UPDATE graph_revisions SET sha256=? WHERE id=?", "0" * 64)]:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute(sql, (value, rev))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("UPDATE scoring_rules SET definition='{}' WHERE version='gate-v1'")
    assert graphs.show(db, rev)["rule_version"] == "gate-v1"


def _delete_dataset(db, dataset_id, now):
    db.execute("UPDATE datasets SET blob=NULL, deleted_at=? WHERE id=?", (now, dataset_id))


def test_learn_refuses_a_dataset_deleted_while_it_was_fitting(instance, monkeypatch):
    db, path = instance
    ids = datasets.simulate(db, "desk", T0, seed=SEED)
    other = store.connect(path)
    real_fit = graphs.learner.fit

    def fit_while_deleted(*args, **kwargs):
        with store.write(other):       # the owner forgets the training set mid-fit (no lease needed)
            _delete_dataset(other, ids["train"], T0 + 1)
            graphs.invalidate_dependents(other, ids["train"], T0 + 1)
        return real_fit(*args, **kwargs)

    monkeypatch.setattr(graphs.learner, "fit", fit_while_deleted)
    try:
        with pytest.raises(Gone, match=f"Dataset {ids['train']} was deleted"):
            graphs.learn(db, "desk", T0, train=ids["train"], validation=ids["validation"], holder="alice")
        assert db.execute("SELECT count(*) FROM graph_revisions").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM graph_lineage").fetchone()[0] == 0
    finally:
        other.close()


def test_gate_rejects_a_candidate_whose_lineage_was_deleted(instance):
    db, _ = instance
    rev, ids = _learn(db, "desk", T0)
    with store.write(db):   # a deletion that bypassed the cascade (older software, or a race before this fix)
        _delete_dataset(db, ids["validation"], T0 + 2)
    result = graphs.promote(db, rev, T0 + 3)
    check = result["decision"]["checks"]["lineage_intact"]
    assert not result["promoted"] and result["decision"]["failed"] == ["lineage_intact"]
    assert check == {"passed": False, "datasets": [ids["train"], ids["validation"]], "deleted": [ids["validation"]]}
    assert _status(db, rev) == "rejected"


def test_promote_scores_outside_the_write_lock_and_rechecks_the_incumbent(instance, monkeypatch):
    db, path = instance
    weak, weak_ids, strong, strong_ids = _two_generations(db)
    candidate, _ = _learn(db, "desk", T0 + 30, seed=SEED + 1)
    other = store.connect(path)
    other.execute("PRAGMA busy_timeout=0")
    real_scores = graphs._scores
    seen = []

    def scores_while_others_write(*args, **kwargs):
        if not seen:
            seen.append(True)
            with store.write(other):     # would raise 'database is locked' if promote held the write lock
                _delete_dataset(other, strong_ids["train"], T0 + 31)
                graphs.invalidate_dependents(other, strong_ids["train"], T0 + 31)   # the incumbent changes
        return real_scores(*args, **kwargs)

    monkeypatch.setattr(graphs, "_scores", scores_while_others_write)
    try:
        with pytest.raises(Conflict, match="active graph of desk changed"):
            graphs.promote(db, candidate, T0 + 32)
        assert _status(db, candidate) == "candidate" and _status(db, weak) == "active"
        assert db.execute("SELECT count(*) FROM graph_leases").fetchone()[0] == 0
        monkeypatch.setattr(graphs, "_scores", real_scores)
        assert graphs.promote(db, candidate, T0 + 33)["decision"]["incumbent"] == weak   # a retry re-scores
    finally:
        other.close()


# ---- 4/5. rollback and invalidation -------------------------------------------------------------------

def _two_generations(db):
    """A short-trained revision, then a better long-trained one of the same world that replaces it."""
    weak, weak_ids = _learn(db, "desk", T0, steps=256)
    assert graphs.promote(db, weak, T0 + 2)["promoted"]
    strong, strong_ids = _learn(db, "desk", T0 + 10, steps=1536)
    result = graphs.promote(db, strong, T0 + 12)
    assert result["promoted"], result["decision"]["failed"]
    assert _status(db, weak) == "archived"
    return weak, weak_ids, strong, strong_ids


def test_rollback_reactivates_archived_but_not_invalidated(instance):
    db, _ = instance
    weak, weak_ids, strong, strong_ids = _two_generations(db)
    out = graphs.rollback(db, "desk", weak, T0 + 20)
    assert out == {"context": "desk", "active": weak, "archived": strong}
    assert _status(db, weak) == "active" and _status(db, strong) == "archived"
    assert graphs.show(db, weak)["decision"]["action"] == "rollback"
    with pytest.raises(Conflict):
        graphs.rollback(db, "desk", weak, T0 + 21)          # already active
    with pytest.raises(ValidationError):
        graphs.rollback(db, "elsewhere", strong, T0 + 21)
    with store.write(db):
        db.execute("UPDATE datasets SET blob=NULL, deleted_at=? WHERE id=?", (T0 + 22, strong_ids["train"]))
        graphs.invalidate_dependents(db, strong_ids["train"], T0 + 22)
    assert _status(db, strong) == "invalidated"
    with pytest.raises(Conflict, match="invalidated"):
        graphs.rollback(db, "desk", strong, T0 + 23)
    assert _status(db, weak) == "active"


def test_invalidate_dependents_reactivates_newest_eligible_then_reports_failure(instance):
    db, _ = instance
    weak, weak_ids, strong, strong_ids = _two_generations(db)
    version = store.state_version(db)
    with store.write(db):
        db.execute("UPDATE datasets SET blob=NULL, deleted_at=? WHERE id=?", (T0 + 20, strong_ids["train"]))
        out = graphs.invalidate_dependents(db, strong_ids["train"], T0 + 20)
    assert out == {"invalidated": [strong], "reactivated": weak, "failure": None}
    assert store.state_version(db) == version   # the caller owns the transaction and the version bump
    assert _status(db, strong) == "invalidated" and _status(db, weak) == "active"
    assert graphs.show(db, strong)["decision"]["action"] == "invalidate"
    with store.write(db):
        db.execute("UPDATE datasets SET blob=NULL, deleted_at=? WHERE id=?", (T0 + 30, weak_ids["validation"]))
        out = graphs.invalidate_dependents(db, weak_ids["validation"], T0 + 30)
    assert out == {"invalidated": [weak], "reactivated": None, "failure": "no eligible snapshot for context desk"}
    assert not [r for r in graphs.listing(db, "desk") if r["status"] == "active"]
    with store.write(db):
        again = graphs.invalidate_dependents(db, weak_ids["validation"], T0 + 31)
    assert again == {"invalidated": [], "reactivated": None, "failure": None}


def test_invalidate_skips_archived_revisions_with_deleted_lineage(instance):
    db, _ = instance
    weak, weak_ids, strong, strong_ids = _two_generations(db)
    with store.write(db):
        # Delete both training sets in one transaction, the newer one last.
        for dataset in (weak_ids["train"], strong_ids["train"]):
            db.execute("UPDATE datasets SET blob=NULL, deleted_at=? WHERE id=?", (T0 + 20, dataset))
        first = graphs.invalidate_dependents(db, weak_ids["train"], T0 + 20)
        second = graphs.invalidate_dependents(db, strong_ids["train"], T0 + 20)
    assert first == {"invalidated": [weak], "reactivated": None, "failure": None}
    assert second == {"invalidated": [strong], "reactivated": None,
                      "failure": "no eligible snapshot for context desk"}


def test_cascade_does_not_revive_a_graph_the_owner_rolled_back_from(instance):
    db, _ = instance
    weak, weak_ids, strong, strong_ids = _two_generations(db)
    graphs.rollback(db, "desk", weak, T0 + 20)        # the owner prefers the older graph
    assert graphs.show(db, strong)["decision"]["action"] == "replaced_by_rollback"
    with store.write(db):
        _delete_dataset(db, weak_ids["train"], T0 + 21)
        out = graphs.invalidate_dependents(db, weak_ids["train"], T0 + 21)
    assert out == {"invalidated": [weak], "reactivated": None, "failure": "no eligible snapshot for context desk"}
    assert _status(db, strong) == "archived"
    # The owner can still choose it explicitly.
    assert graphs.rollback(db, "desk", strong, T0 + 22)["active"] == strong


def test_cascade_picks_the_newest_eligible_snapshot_past_a_rolled_back_one(instance):
    db, _ = instance
    weak, weak_ids, strong, strong_ids = _two_generations(db)
    third, third_ids = _learn(db, "desk", T0 + 30, seed=SEED, steps=1536 * 2)
    assert graphs.promote(db, third, T0 + 32)["promoted"]
    graphs.rollback(db, "desk", strong, T0 + 40)      # third is replaced by the owner's rollback
    with store.write(db):
        _delete_dataset(db, strong_ids["train"], T0 + 41)
        out = graphs.invalidate_dependents(db, strong_ids["train"], T0 + 41)
    assert out == {"invalidated": [strong], "reactivated": weak, "failure": None}
    assert _status(db, third) == "archived" and _status(db, weak) == "active"


# ---- 7. lease -----------------------------------------------------------------------------------------

def test_lease_single_writer(instance):
    db, path = instance
    ids = datasets.simulate(db, "desk", T0, seed=SEED)
    other = store.connect(path)
    try:
        with graphs.lease(db, "desk", "alice", T0, ttl=60):
            with pytest.raises(LeaseHeld):
                with graphs.lease(other, "desk", "bob", T0 + 1):
                    pass
            with pytest.raises(LeaseHeld):
                graphs.learn(other, "desk", T0 + 1, train=ids["train"], validation=ids["validation"], holder="bob")
            with graphs.lease(other, "elsewhere", "bob", T0 + 1):   # other contexts are independent
                pass
            rev = graphs.learn(db, "desk", T0 + 2, train=ids["train"], validation=ids["validation"], holder="alice")
            with pytest.raises(LeaseHeld):
                graphs.promote(other, rev, T0 + 3)
            assert db.execute("SELECT holder FROM graph_leases WHERE context='desk'").fetchone() == ("alice",)
            # learn renewed alice's lease to T0 + 2 + 600; once that expires, bob can take it over.
            with pytest.raises(LeaseHeld):
                with graphs.lease(other, "desk", "bob", T0 + 601):
                    pass
            with graphs.lease(other, "desk", "bob", T0 + 603):
                assert other.execute("SELECT holder FROM graph_leases").fetchone() == ("bob",)
        assert db.execute("SELECT count(*) FROM graph_leases").fetchone()[0] == 0
        assert _status(db, rev) == "candidate"
        assert graphs.promote(other, rev, T0 + 700)["promoted"]
    finally:
        other.close()


def test_lost_lease_blocks_commit(instance, monkeypatch):
    db, path = instance
    ids = datasets.simulate(db, "desk", T0, seed=SEED)
    real_fit = graphs.learner.fit
    bob = store.connect(path)
    held = []

    def slow_fit(*args, **kwargs):
        # While alice is still fitting, her lease expires and bob takes the context over and keeps it.
        with pytest.raises(LeaseHeld):
            with graphs.lease(bob, "desk", "bob", T0 + 599):
                pass
        held.append(graphs.lease(bob, "desk", "bob", T0 + 601))
        held[0].__enter__()
        return real_fit(*args, **kwargs)

    monkeypatch.setattr(graphs.learner, "fit", slow_fit)
    try:
        with pytest.raises(LeaseHeld, match="lost"):
            graphs.learn(db, "desk", T0, train=ids["train"], validation=ids["validation"], holder="alice")
        assert db.execute("SELECT count(*) FROM graph_revisions").fetchone()[0] == 0
        assert db.execute("SELECT holder FROM graph_leases").fetchone() == ("bob",)  # alice left bob's lease alone
        held[0].__exit__(None, None, None)
        assert db.execute("SELECT count(*) FROM graph_leases").fetchone()[0] == 0
    finally:
        bob.close()


# ---- 8. lesion ----------------------------------------------------------------------------------------

def test_lesion_true_edges_matter_more_than_weak_ones(instance):
    db, _ = instance
    _add_rule(db, T0, version="wide", pack_candidates={"degrees": [6], "bits": [8]})
    rev, ids = _learn(db, "desk", T0 + 1)
    assert graphs.show(db, rev)["degree"] == 6           # 3 true + 3 weak inputs per receiver
    out = graphs.lesion(db, rev, ids["heldout"], T0 + 3)
    a, _ = graph_bench.world(np.random.default_rng(SEED))
    true = [e["delta_mse"] for e in out["edges"] if a[e["dst"], e["src"]] != 0]
    weak = [e["delta_mse"] for e in out["edges"] if a[e["dst"], e["src"]] == 0]
    assert len(true) == 72 and len(weak) == 72
    assert min(true) > max(weak)
    assert np.mean(true) > 5 * max(abs(w) for w in weak)
    assert out["permuted_control_mse"] > 2 * out["base_mse"]
    rows = db.execute("SELECT kind, count(*) FROM graph_effects WHERE revision_id=? GROUP BY kind", (rev,)).fetchall()
    assert dict(rows) == {"lesion": 144, "lesion_control": 1}
    # Uncertainty: every true edge's effect is many time-block standard errors away from zero.
    assert out["task"] == "desk" and out["se_block_steps"] == 32 and out["se_blocks"] == 16
    true_edges = [e for e in out["edges"] if a[e["dst"], e["src"]] != 0]
    assert all(e["se"] > 0 and e["delta_mse"] > 3 * e["se"] for e in true_edges)
    detail = json.loads(db.execute("SELECT detail FROM graph_effects WHERE kind='lesion' AND src=? AND dst=?",
                                   (true_edges[0]["src"], true_edges[0]["dst"])).fetchone()[0])
    assert detail["task"] == "desk" and detail["se"] == true_edges[0]["se"] and detail["block"] == 32


def test_lesion_uses_the_closed_form_error_change_and_equals_the_full_recomputation(instance, monkeypatch):
    """Zeroing one edge changes only its receiver's residual, so the change in MSE is the mean of the per-step
    change: lesion no longer recomputes the full MSE per edge (base and control only), and every delta_mse equals
    the full recomputation."""
    from neuroscifi.instance import learner
    db, _ = instance
    rev, ids = _learn(db, "desk", T0, steps=256)
    calls = []
    full_mse = learner.mse
    monkeypatch.setattr(learner, "mse", lambda *a: calls.append(1) or full_mse(*a))
    out = graphs.lesion(db, rev, ids["heldout"], T0 + 3)
    assert len(calls) == 2 and len(out["edges"]) > 10
    matrix = graphs._matrix(db, rev).astype(np.float64)
    states, targets = datasets.load(db, ids["heldout"])
    base = full_mse(matrix, states, targets)
    for edge in out["edges"]:
        lesioned = matrix.copy()
        lesioned[edge["dst"], edge["src"]] = 0
        assert edge["delta_mse"] == pytest.approx(full_mse(lesioned, states, targets) - base, rel=1e-9, abs=1e-15)


def test_functional_map_proposes_routes_missing_from_the_active_graph(tmp_path):
    a, _ = graph_bench.world(np.random.default_rng(SEED))
    pruned = a.copy()
    missing = [(dst, int(np.flatnonzero(a[dst])[0])) for dst in range(0, 24, 3)]   # drop 8 true edges
    for dst, src in missing:
        pruned[dst, src] = 0
    db = store.create(tmp_path / "map.sqlite", T0, founding_graph=encode(pruned, 3, 32))
    try:
        ids = datasets.simulate(db, "founding", T0, seed=SEED)
        out = graphs.functional_map(db, ids["train"], T0 + 1, top=20)
        assert out["context"] == "founding" and out["revision_id"] == 1 and out["pairs"] == 24 * 23
        proposed = {(r["dst"], r["src"]) for r in out["routes"]}
        assert all((r["dst"], r["src"]) not in {(d, s) for d, s in zip(*np.nonzero(pruned))} for r in out["routes"])
        assert len(proposed & set(missing)) >= 6
        assert {r["lag"] for r in out["routes"] if (r["dst"], r["src"]) in missing} == {1}
        rows = db.execute("SELECT count(*), min(detail) FROM graph_effects WHERE kind='coactivation'").fetchone()
        assert rows[0] == 24 * 23 and json.loads(rows[1])["context"] == "founding"
        assert "common stimulus" in graphs.functional_map.__doc__
        other = datasets.simulate(db, "elsewhere", T0, seed=SEED)
        with pytest.raises(NotFound, match="no active graph"):
            graphs.functional_map(db, other["train"], T0 + 2)
        with pytest.raises(ValidationError):
            graphs.functional_map(db, ids["train"], T0 + 2, lags=(0,))
    finally:
        db.close()


# ---- 9. founding graph --------------------------------------------------------------------------------

def test_founding_graph_is_registered_at_birth(tmp_path):
    a, _ = graph_bench.world(np.random.default_rng(SEED))
    path = tmp_path / "born.sqlite"
    db = store.create(path, T0, founding_graph=encode(a, 3, 4))
    try:
        birth = store.birth(db)
        assert birth["initial_graph"] == {"revision_id": 1, "context": "founding"}
        (rev,) = graphs.listing(db)
        assert (rev["status"], rev["context"], rev["origin"]) == ("active", "founding", "founding")
        assert (rev["bits"], rev["degree"], rev["rows"], rev["cols"], rev["byte_size"]) == (4, 3, 24, 24, 247)
        assert np.array(graphs.show(db, 1)["support"]).sum() == 72
        with pytest.raises(Conflict):
            graphs.register_founding(db, encode(a, 3, 4), T0)
    finally:
        db.close()
    bad = tmp_path / "bad.sqlite"
    with pytest.raises(ValidationError):
        store.create(bad, T0, founding_graph=b"not a graph")
    assert not bad.exists()


# ---- 10. CLI ------------------------------------------------------------------------------------------

def _nsf(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, json.loads(captured.out) if code == 0 else captured.err


def test_cli_graph_lifecycle(tmp_path, capsys):
    db = str(tmp_path / "cli.sqlite")
    assert _nsf(capsys, "init", "--db", db)[0] == 0
    code, ids = _nsf(capsys, "graph", "simulate", "desk", "--seed", str(SEED), "--db", db)
    assert code == 0 and set(ids) == {"train", "validation", "heldout"}
    code, learned = _nsf(capsys, "graph", "learn", "desk", "--train", str(ids["train"]),
                         "--validation", str(ids["validation"]), "--db", db)
    rev = learned["revision_id"]
    code, evaluation = _nsf(capsys, "graph", "evaluate", str(rev), "--db", db)
    assert code == 0 and evaluation["heldout_mse"] < 0.006
    code, promoted = _nsf(capsys, "graph", "promote", str(rev), "--db", db)
    assert code == 0 and promoted["promoted"]
    code, listed = _nsf(capsys, "graph", "list", "--context", "desk", "--db", db)
    assert [(r["id"], r["status"]) for r in listed] == [(rev, "active")]
    code, shown = _nsf(capsys, "graph", "show", str(rev), "--db", db)
    assert len(shown["matrix"]) == 24 and len(shown["support"][0]) == 24
    code, recalled = _nsf(capsys, "graph", "recall", str(ids["heldout"]), "--context", "desk", "--db", db)
    assert code == 0 and recalled["verdict"] == "known:desk" and recalled["steps"] == 512
    code, data = _nsf(capsys, "graph", "datasets", "--db", db)
    assert code == 0 and len(data) == 3
    out = tmp_path / "desk.nsg"
    code, exported = _nsf(capsys, "graph", "export", str(rev), str(out), "--db", db)
    assert code == 0 and exported["bytes"] == 247 and decode(out.read_bytes()).shape == (24, 24)
    assert out.stat().st_mode & 0o777 == 0o600
    code, err = _nsf(capsys, "graph", "export", str(rev), str(out), "--db", db)
    assert code == 2 and "overwrite" in err
    link = tmp_path / "link.nsg"
    link.symlink_to(tmp_path / "elsewhere.nsg")            # a dangling symlink is not followed
    code, err = _nsf(capsys, "graph", "export", str(rev), str(link), "--db", db)
    assert code == 2 and not (tmp_path / "elsewhere.nsg").exists()
    code, mapped = _nsf(capsys, "graph", "coactivation", str(ids["heldout"]), "--top", "5", "--db", db)
    assert code == 0 and len(mapped["routes"]) == 5 and mapped["context"] == "desk"
    # Owner rules: the flag is mandatory.
    rule = tmp_path / "rule.json"
    rule.write_text(json.dumps(dict(store.GATE_V1, version="gate-v2", byte_budget=2048)))
    code, err = _nsf(capsys, "graph", "rules", "add", str(rule), "--db", db)
    assert code == 2 and "--owner-approved" in err
    code, versions = _nsf(capsys, "graph", "rules", "list", "--db", db)
    assert [r["version"] for r in versions] == ["gate-v1"]
    code, added = _nsf(capsys, "graph", "rules", "add", str(rule), "--owner-approved", "--db", db)
    assert code == 0 and added["latest"] == "gate-v2"
    code, err = _nsf(capsys, "graph", "reject", str(rev), "--reason", "late", "--db", db)
    assert code == 2 and "only candidates" in err


def test_every_write_path_honours_the_free_space_reserve_without_a_path(instance):
    """S-03: datasets.simulate, graphs.learn and evidence.start_epoch refuse while free space is below the reserve
    even when the caller (the console) passes no path: regulation reads the instance's own file."""
    from neuroscifi.instance import evidence
    from neuroscifi.instance.errors import WritesPaused
    db, _ = instance
    ids = datasets.simulate(db, "desk", T0, seed=SEED, steps=512)
    with store.write(db):
        cfg = json.loads(db.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0])
        db.execute("UPDATE meta SET value=? WHERE key='config'", (json.dumps(cfg | {"min_free_bytes": 2 ** 62}),))
    assert store.regulation(db)["writes_allowed"] is False
    with pytest.raises(WritesPaused, match="reserve"):
        datasets.simulate(db, "desk", T0 + 1, seed=SEED + 1, steps=512)
    with pytest.raises(WritesPaused, match="reserve"):
        graphs.learn(db, "desk", T0 + 2, train=ids["train"], validation=ids["validation"], holder="console:x")
    with pytest.raises(WritesPaused, match="reserve"):
        evidence.start_epoch(db, "desk-2", T0 + 3)
    assert db.execute("SELECT count(*) FROM graph_revisions WHERE origin='learner'").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM epochs WHERE name='desk-2'").fetchone()[0] == 0
