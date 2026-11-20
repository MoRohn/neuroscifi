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
    # the caller-chosen event id is not repeated in derived memory
    assert rev[0] == "evidence:#forgotten" and json.loads(rev[1])["cause"] == "evidence:#forgotten"
    assert rev[2] == before + 1
    assert governance.forget_evidence(db, "ev1", T0 + 6)["status"] == "absent"
    assert governance.forget_evidence(db, "never-existed", T0 + 6)["status"] == "absent"
    assert store.state_version(db) == before + 1
    assert db.execute("SELECT count(*) FROM deletion_log").fetchone()[0] == 1


def test_forget_leaves_open_questions_to_the_executive(instance):
    """A deletion does not close questions wholesale; executive.decide closes those whose premise is gone."""
    db, _ = instance
    add(db, "a", epoch_name="e1")
    add(db, "b", epoch_name="e2")
    pending(db, "e1")
    pending(db, "e2")
    out = governance.forget_evidence(db, "a", T0 + 1)
    assert "pending_superseded" not in out["effects"]
    assert db.execute("SELECT epoch, resolved_at, resolution FROM pending ORDER BY epoch").fetchall() == [
        ("e1", None, None), ("e2", None, None)]


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


def test_forget_dataset_checks_existence_inside_its_transaction(instance, monkeypatch):
    db, path = instance
    from contextlib import contextmanager
    from neuroscifi.instance import datasets
    ids = datasets.simulate(db, "ctx", T0, seed=1, steps=64)
    other = store.connect(path)
    real, raced = store.write, []

    @contextmanager
    def racing_write(conn):
        if not raced:  # another process forgets the same dataset just before our transaction starts
            raced.append(None)
            raced[0] = governance.forget_dataset(other, ids["train"], T0 + 1)
        with real(conn) as inner:
            yield inner

    monkeypatch.setattr(store, "write", racing_write)
    try:
        out = governance.forget_dataset(db, ids["train"], T0 + 2)
    finally:
        other.close()
    assert raced[0]["status"] == "forgotten" and out == {"status": "absent", "effects": {}}
    assert db.execute("SELECT count(*) FROM deletion_log WHERE target_kind='dataset'").fetchone()[0] == 1
    assert db.execute("SELECT deleted_at FROM datasets WHERE id=?", (ids["train"],)).fetchone()[0] == T0 + 1


def test_forget_dataset_truncates_the_wal(instance):
    db, path = instance
    from neuroscifi.instance import datasets
    ids = datasets.simulate(db, "ctx", T0, seed=1, steps=64)
    out = governance.forget_dataset(db, ids["train"], T0 + 1)
    assert path.with_name(path.name + "-wal").stat().st_size == 0
    assert out["effects"]["wal_checkpoint"] == "ok"


def test_write_rolls_back_when_commit_fails(instance):
    import sqlite3
    db, _ = instance
    with pytest.raises(sqlite3.IntegrityError):
        with store.write(db):
            db.execute("PRAGMA defer_foreign_keys=ON")  # the violation is only detected at COMMIT
            db.execute("INSERT INTO graph_lineage(revision_id,dataset_id,role) VALUES(999,999,'train')")
    assert not db.in_transaction
    assert db.execute("SELECT count(*) FROM graph_lineage").fetchone()[0] == 0
    with store.write(db):
        store.bump_state_version(db)  # the connection is usable again


def test_scoring_rules_cannot_be_replaced_and_latest_is_last_registered(instance):
    import sqlite3
    from neuroscifi.instance import rules
    db, _ = instance
    original = db.execute("SELECT definition,sha256 FROM scoring_rules WHERE version='gate-v1'").fetchone()
    for verb in ("INSERT OR REPLACE", "REPLACE", "INSERT OR IGNORE", "INSERT"):
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            db.execute(f"{verb} INTO scoring_rules VALUES('gate-v1','{{}}','forged',0,'learner')")
    assert db.execute("SELECT definition,sha256 FROM scoring_rules WHERE version='gate-v1'").fetchone() == original
    with store.write(db):  # registered later, with an earlier clock reading
        rules.register(db, store.GATE_V1 | {"version": "gate-v2"}, T0 - 100, created_by="owner")
    assert rules.latest(db) == "gate-v2"


def test_thermal_limit_pauses_writes(tmp_path, monkeypatch):
    from neuroscifi.instance.errors import WritesPaused
    zone = tmp_path / "thermal_zone0"
    zone.mkdir()
    monkeypatch.setattr(store, "THERMAL_ZONES", str(tmp_path / "thermal_zone*" / "temp"))
    db = store.create(tmp_path / "i.sqlite", T0, config=store.InstanceConfig(max_thermal_celsius=80.0))
    try:
        (zone / "temp").write_text("85500\n")
        with pytest.raises(WritesPaused, match="85.5 C exceeds the 80.0 C limit"):
            store.require_write_allowed(db)
        assert store.regulation(db)["writes_allowed"] is False
        (zone / "temp").write_text("61000\n")
        store.require_write_allowed(db)
    finally:
        db.close()
    plain = store.create(tmp_path / "j.sqlite", T0)  # default: no thermal check, even when hot
    try:
        (zone / "temp").write_text("99000\n")
        assert store.config(plain).max_thermal_celsius is None
        store.require_write_allowed(plain)
    finally:
        plain.close()


def test_bulk_writes_check_free_space_when_given_the_db_path(tmp_path):
    import numpy as np
    from neuroscifi import legacy_live
    from neuroscifi.instance import datasets, legacy
    from neuroscifi.instance.errors import WritesPaused
    path = tmp_path / "i.sqlite"
    db = store.create(path, T0, config=store.InstanceConfig(min_free_bytes=2 ** 62))
    proto = tmp_path / "proto.sqlite"
    source = legacy_live.open_store(proto)
    with source:
        source.execute("INSERT INTO epochs VALUES ('desk')")
    legacy_live.add_event(source, "desk", "p1", "camera", 0, 1, "cam-a")
    source.close()
    try:
        states = np.zeros((8, 2))
        with pytest.raises(WritesPaused, match="reserve"):
            datasets.add(db, "c", "train", states, states, "observed", T0, path=path)
        with pytest.raises(WritesPaused, match="reserve"):
            datasets.simulate(db, "c", T0, seed=1, steps=64, path=path)
        with pytest.raises(WritesPaused, match="reserve"):
            legacy.import_legacy(db, proto, T0, path=path)
        assert db.execute("SELECT count(*) FROM datasets").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0
        assert datasets.add(db, "c", "train", states, states, "observed", T0) >= 1  # without a path: unchanged
    finally:
        db.close()
