import json

import pytest

from neuroscifi.instance import governance, store
from neuroscifi.instance.errors import Gone, NotFound
from _seed import T0, add, pending

SECRET = "zq-unique-root-77"


def all_text(db):
    out = []
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        for row in db.execute(f"SELECT * FROM {name}"):
            out.extend(str(v) for v in row)
    return "\n".join(out)


def test_forget_leaves_only_a_tombstone(instance):
    db, _ = instance
    add(db, "ev1", root=SECRET)
    add(db, "ev2", root="other")
    pending(db)
    before = store.state_version(db)
    result = governance.forget_evidence(db, "ev1", T0 + 5)
    assert result["status"] == "forgotten"
    assert db.execute("SELECT count(*) FROM evidence WHERE id='ev1'").fetchone()[0] == 0
    tomb = db.execute("SELECT id,epoch,deleted_at,reason FROM tombstones").fetchall()
    assert tomb == [("ev1", "e1", T0 + 5, "forgotten")]
    assert SECRET not in all_text(db)
    assert store.state_version(db) == before + 1
    log = db.execute("SELECT target_kind,target_id,effects,failure FROM deletion_log").fetchall()
    assert log[0][:2] == ("evidence", "ev1") and json.loads(log[0][2])["epochs"] == ["e1"] and log[0][3] is None
    rev = db.execute("SELECT cause,summary,state_version FROM belief_revisions").fetchone()
    assert rev[0] == "forget:ev1" and json.loads(rev[1])["cause"] == "forget:ev1" and rev[2] == before + 1
    assert governance.forget_evidence(db, "ev1", T0 + 6)["status"] == "absent"
    assert governance.forget_evidence(db, "never-existed", T0 + 6)["status"] == "absent"
    assert store.state_version(db) == before + 1
    assert db.execute("SELECT count(*) FROM deletion_log").fetchone()[0] == 1


def test_forget_supersedes_pending_of_that_epoch_only(instance):
    db, _ = instance
    add(db, "a", epoch_name="e1")
    add(db, "b", epoch_name="e2")
    pending(db, "e1")
    pending(db, "e2")
    governance.forget_evidence(db, "a", T0 + 1)
    rows = dict(db.execute("SELECT epoch, resolution FROM pending"))
    assert rows == {"e1": "superseded by deletion", "e2": None}
    assert db.execute("SELECT resolved_at FROM pending WHERE epoch='e1'").fetchone()[0] == T0 + 1


def test_forget_epoch_and_partner_hit_exactly_the_intended_rows(instance):
    db, _ = instance
    add(db, "w1", epoch_name="e1", channel="word", root=SECRET)
    add(db, "w2", epoch_name="e2", channel="word", root=SECRET)
    add(db, "c1", epoch_name="e1", channel="camera", root=SECRET)
    add(db, "w3", epoch_name="e1", channel="word", root="other")
    out = governance.forget_partner(db, SECRET, T0 + 1)
    assert out["effects"]["deleted"] == 2
    assert {r[0] for r in db.execute("SELECT id FROM evidence")} == {"c1", "w3"}
    assert dict(db.execute("SELECT id,reason FROM tombstones")) == {"w1": "partner_forgotten", "w2": "partner_forgotten"}
    assert SECRET not in "".join(str(r) for r in db.execute("SELECT * FROM deletion_log"))
    assert governance.forget_partner(db, SECRET, T0 + 2)["status"] == "absent"

    out = governance.forget_epoch(db, "e1", T0 + 3)
    assert out["effects"]["deleted"] == 2
    assert db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0
    assert dict(db.execute("SELECT id,reason FROM tombstones WHERE id IN ('c1','w3')")) == {
        "c1": "epoch_forgotten", "w3": "epoch_forgotten"}
    with pytest.raises(NotFound):
        governance.forget_epoch(db, "nope", T0)


def test_sweep_expires_only_due_unpromoted_rows(instance):
    db, _ = instance
    add(db, "old", expires_at=T0 + 10)
    add(db, "due-later", expires_at=T0 + 1000)
    add(db, "kept", expires_at=T0 + 10)
    assert governance.promote(db, "kept", T0)["status"] == "promoted"
    assert db.execute("SELECT retention,expires_at FROM evidence WHERE id='kept'").fetchone() == ("promoted", None)
    assert governance.promote(db, "kept", T0)["status"] == "unchanged"
    out = governance.sweep(db, T0 + 100)
    assert out["expired"] == 1
    assert {r[0] for r in db.execute("SELECT id FROM evidence")} == {"due-later", "kept"}
    assert db.execute("SELECT id,reason FROM tombstones").fetchall() == [("old", "expired")]
    assert db.execute("SELECT cause FROM belief_revisions").fetchone()[0] == "expired"
    before = store.state_version(db)
    assert governance.sweep(db, T0 + 100)["expired"] == 0
    assert store.state_version(db) == before
    # far future: promoted rows still never expire
    governance.sweep(db, T0 + 10**7)
    assert [r[0] for r in db.execute("SELECT id FROM evidence")] == ["kept"]


def test_promote_gone_notfound_and_expired(instance):
    db, _ = instance
    add(db, "x")
    add(db, "late", expires_at=T0 + 1)
    governance.forget_evidence(db, "x", T0)
    with pytest.raises(Gone):
        governance.promote(db, "x", T0)
    with pytest.raises(NotFound):
        governance.promote(db, "zzz", T0)
    with pytest.raises(Gone):
        governance.promote(db, "late", T0 + 5)
    governance.sweep(db, T0 + 5)
    with pytest.raises(Gone):
        governance.promote(db, "late", T0 + 5)


def test_forget_dataset_cascade_with_real_graphs(instance):
    db, _ = instance
    from neuroscifi.instance import checkpoints, datasets, graphs  # W2-B modules
    ids = datasets.simulate(db, "ctx", T0, seed=1)
    stale = checkpoints.create(db, instance[1], instance[1].parent / "cp", T0)
    rev = graphs.learn(db, "ctx", T0, train=ids["train"], validation=ids["validation"], holder="t")
    out = governance.forget_dataset(db, ids["train"], T0 + 1)
    assert out["status"] == "forgotten"
    assert rev in out["effects"]["invalidated"]
    assert db.execute("SELECT status FROM graph_revisions WHERE id=?", (rev,)).fetchone()[0] == "invalidated"
    row = db.execute("SELECT blob,deleted_at FROM datasets WHERE id=?", (ids["train"],)).fetchone()
    assert row == (None, T0 + 1)
    assert db.execute("SELECT status FROM checkpoints WHERE id=?", (stale["id"],)).fetchone()[0] == "stale"
    assert db.execute("SELECT target_kind FROM deletion_log").fetchone()[0] == "dataset"
    assert governance.forget_dataset(db, ids["train"], T0 + 2)["status"] == "absent"
    with pytest.raises(NotFound):
        governance.forget_dataset(db, 9999, T0)
