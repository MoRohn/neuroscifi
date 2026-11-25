"""Forgetting a dataset erases what graphs learned from it (ADR-048, schema 15; ENGINEERING_PLAN §6 item 13).

Before: `forget_dataset` invalidated the revisions trained or validated on the dataset, but their weights (an
image of the data they were fitted to) and metrics stayed, because the snapshot's blob was immutable by trigger;
the dataset's digest stayed too. Now an invalidated revision of that lineage loses its blob and metrics, the graph
effects measured on it go, the dataset's digest goes, and a restore does the same. The trigger allows exactly that
update of an invalidated revision and refuses every other, and an erased revision can never be made active again.
"""
import json
import sqlite3

import pytest

from neuroscifi.instance import checkpoints, datasets, governance, graphs, store
from neuroscifi.instance.errors import Gone

from conftest import T0


def learned(db):
    ids = datasets.simulate(db, "ctx", T0, seed=1)
    rev = graphs.learn(db, "ctx", T0, train=ids["train"], validation=ids["validation"], holder="t")
    graphs.lesion(db, rev, ids["heldout"], T0 + 1)          # effects measured ON the revision, on other data
    return ids, rev


def blob(db, rev):
    return bytes(db.execute("SELECT blob FROM graph_revisions WHERE id=?", (rev,)).fetchone()[0])


def test_forgetting_the_training_data_erases_the_weights_metrics_and_effects_of_its_graphs(instance, tmp_path):
    db, path = instance
    ids, rev = learned(db)
    sha = db.execute("SELECT sha256 FROM graph_revisions WHERE id=?", (rev,)).fetchone()[0]
    assert blob(db, rev) and graphs.show(db, rev)["metrics"]
    assert db.execute("SELECT count(*) FROM graph_effects WHERE revision_id=?", (rev,)).fetchone()[0] > 0
    archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 2)
    out = governance.forget_dataset(db, ids["train"], T0 + 3)
    effects = out["effects"]
    assert effects["invalidated"] == [rev] and effects["graphs_erased"] == [rev]
    assert blob(db, rev) == b""
    row = db.execute("SELECT status, sha256, metrics FROM graph_revisions WHERE id=?", (rev,)).fetchone()
    # the digest of the weights goes too: with it a guessed dataset could be refitted and checked (review L3)
    assert row[0] == "invalidated" and sha != graphs.ERASED_SHA256 and row[1] == graphs.ERASED_SHA256
    assert row[2] == '{"erased": {"at": %r, "dataset": %d}}' % (T0 + 3, ids["train"])
    assert db.execute("SELECT count(*) FROM graph_effects WHERE revision_id=?", (rev,)).fetchone()[0] == 0
    assert db.execute("SELECT sha256 FROM datasets WHERE id=?", (ids["train"],)).fetchone()[0] == ""
    # the other datasets keep their digests; the revision stays listed with its lineage and decisions
    assert db.execute("SELECT count(*) FROM datasets WHERE sha256!=''").fetchone()[0] == 2
    shown = graphs.show(db, rev)
    assert shown["matrix"] == [] and "erased" in shown and shown["lineage"]
    with pytest.raises(Gone):
        graphs.export(db, rev, tmp_path / "out.nsg")
    with pytest.raises(Gone):
        graphs.lesion(db, rev, ids["heldout"], T0 + 4)
    # a restore of an archive made before the forget ends the same way
    checkpoints.restore(db, archive["id"], tmp_path / "r.sqlite", T0 + 5)
    with sqlite3.connect(tmp_path / "r.sqlite") as copy:
        assert bytes(copy.execute("SELECT blob FROM graph_revisions WHERE id=?", (rev,)).fetchone()[0]) == b""
        assert copy.execute("SELECT count(*) FROM graph_effects WHERE revision_id=?", (rev,)).fetchone()[0] == 0
        assert copy.execute("SELECT sha256 FROM datasets WHERE id=?", (ids["train"],)).fetchone()[0] == ""
    copy = store.open_existing(tmp_path / "r.sqlite")
    try:
        assert graphs.show(copy, rev)["matrix"] == []
    finally:
        copy.close()


def test_a_revision_invalidated_earlier_is_erased_by_the_forget_of_its_other_dataset(instance):
    db, _ = instance
    ids, rev = learned(db)
    governance.forget_dataset(db, ids["validation"], T0 + 3)
    assert blob(db, rev) == b""
    out = governance.forget_dataset(db, ids["train"], T0 + 4)         # already invalidated and erased: nothing new
    assert out["effects"]["graphs_erased"] == [] and out["effects"]["invalidated"] == []


def test_a_held_out_dataset_forgotten_leaves_the_graph_it_only_scored(instance):
    db, _ = instance
    ids, rev = learned(db)
    out = governance.forget_dataset(db, ids["heldout"], T0 + 3)
    assert out["effects"]["graphs_erased"] == [] and blob(db, rev)
    assert db.execute("SELECT status FROM graph_revisions WHERE id=?", (rev,)).fetchone()[0] != "invalidated"


def test_the_trigger_still_refuses_every_other_change_of_a_snapshot(instance):
    db, _ = instance
    ids, rev = learned(db)
    for sql, params in (("UPDATE graph_revisions SET blob=? WHERE id=?", (b"", rev)),          # not invalidated
                        ("UPDATE graph_revisions SET sha256='x' WHERE id=?", (rev,)),
                        ("UPDATE graph_revisions SET context='other' WHERE id=?", (rev,))):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            with store.write(db):
                db.execute(sql, params)
    governance.forget_dataset(db, ids["train"], T0 + 3)
    for sql, params in (("UPDATE graph_revisions SET blob=? WHERE id=?", (b"\x01", rev)),       # no way back
                        ("UPDATE graph_revisions SET sha256='x', blob=? WHERE id=?", (b"", rev)),
                        ("UPDATE graph_revisions SET origin='import' WHERE id=?", (rev,)),
                        ("UPDATE graph_revisions SET rule_version='x' WHERE id=?", (rev,))):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            with store.write(db):
                db.execute(sql, params)
    for status in ("active", "archived", "candidate"):
        with pytest.raises(sqlite3.IntegrityError, match="stays invalidated"):
            with store.write(db):
                db.execute("UPDATE graph_revisions SET status=? WHERE id=?", (status, rev))


def test_migration_15_changes_no_row_of_a_version_14_file(tmp_path):
    """Additive: a version-14 file with graphs, datasets and evidence gains forgotten_scopes and the new triggers (and,
    by migration 16 after it, the copied flags); every row of every table is what it was."""
    from neuroscifi.instance import schema
    path = tmp_path / "v14.sqlite"
    db = store.create(path, T0)
    learned(db)
    db.close()
    raw = sqlite3.connect(path, isolation_level=None)
    raw.executescript(
        "BEGIN; DROP TRIGGER evidence_consolidated_copied_insert; DROP TRIGGER evidence_consolidated_copied_update; "
        "DROP TRIGGER evidence_consolidated_copied_kept; DROP INDEX evidence_consolidated_copied; "
        "ALTER TABLE evidence_consolidated DROP COLUMN copied; "
        "DROP TABLE forgotten_scopes; DROP TRIGGER graph_erased_stays_invalidated; "
        "DROP TRIGGER graph_revisions_never_deleted; "
        "DROP TRIGGER graph_identity_immutable; "
        "CREATE TRIGGER graph_identity_immutable BEFORE UPDATE OF blob, sha256, rule_version, context, origin "
        "ON graph_revisions BEGIN SELECT RAISE(ABORT,'graph snapshot identity is immutable'); END; "
        "PRAGMA user_version=14; COMMIT;")
    tables = [n for (n,) in raw.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE "
                                        "'sqlite_%' ORDER BY name")]
    columns = {t: ",".join(c[1] for c in raw.execute(f"PRAGMA table_info({t})")) for t in tables}
    before = {t: raw.execute(f"SELECT {columns[t]} FROM {t} ORDER BY rowid").fetchall() for t in tables}
    raw.close()
    db = store.open_existing(path)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION
        # the columns a version-14 file had (migration 16 adds `copied`, tested in test_consolidation_copied.py)
        assert {t: db.execute(f"SELECT {columns[t]} FROM {t} ORDER BY rowid").fetchall() for t in tables} == before
        assert db.execute("SELECT count(*) FROM forgotten_scopes").fetchone()[0] == 0
        rev = db.execute("SELECT id FROM graph_revisions").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            with store.write(db):
                db.execute("UPDATE graph_revisions SET blob=? WHERE id=?", (b"", rev))
    finally:
        db.close()


# ---- review findings of the remaining-items round (fix lane FI) -------------------------------------------------

def test_no_revision_can_be_deleted_nor_rewritten_by_insert_or_replace(instance):
    """Review L2: INSERT OR REPLACE deletes the old row implicitly, which passed both identity triggers (an erased
    revision rewritten with new weights and made active). Revisions are never deleted, so a delete trigger closes it."""
    db, _ = instance
    ids, rev = learned(db)
    governance.forget_dataset(db, ids["train"], T0 + 3)
    row = db.execute("SELECT id,context,parent_id,status,bits,degree,rows,cols,byte_size,sha256,blob,rule_version,"
                     "origin,metrics,decision,created_at,decided_at FROM graph_revisions WHERE id=?", (rev,)).fetchone()
    forged = list(row)
    forged[3], forged[9], forged[10] = "active", "h2", b"\xff"
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        with store.write(db):
            db.execute("INSERT OR REPLACE INTO graph_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", forged)
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        with store.write(db):
            db.execute("DELETE FROM graph_revisions WHERE id=?", (rev,))
    assert blob(db, rev) == b"" and db.execute("SELECT status FROM graph_revisions WHERE id=?",
                                               (rev,)).fetchone()[0] == "invalidated"


def test_the_digest_marker_is_allowed_only_with_the_erasure_of_an_invalidated_revision(instance):
    db, _ = instance
    ids, rev = learned(db)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with store.write(db):
            db.execute("UPDATE graph_revisions SET sha256=? WHERE id=?", (graphs.ERASED_SHA256, rev))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):        # invalidated, weights kept: refused too
        with store.write(db):
            graphs.invalidate_dependents(db, ids["train"], T0 + 2)
            db.execute("UPDATE graph_revisions SET sha256=? WHERE id=?", (graphs.ERASED_SHA256, rev))


def _numbers(node, keys=None):
    """Every value under a score key in a decision tree that is not None."""
    keys = graphs.SCORE_KEYS if keys is None else keys
    found = []
    if isinstance(node, list):
        for item in node:
            found += _numbers(item, keys)
    elif isinstance(node, dict):
        for key, value in node.items():
            if key in keys and value is not None:
                found.append((key, value))
            else:
                found += _numbers(value, keys)
    return found


def test_an_erased_revision_keeps_no_score_that_could_confirm_a_guessed_dataset(instance, tmp_path):
    """Review L3: the decisions of an erased revision and the incumbent's side of other revisions' decisions held
    errors computed with its weights; refitting a guessed dataset and comparing them confirmed the guess."""
    db, path = instance
    first_ids = datasets.simulate(db, "desk", T0, seed=1)
    first = graphs.learn(db, "desk", T0 + 1, train=first_ids["train"], validation=first_ids["validation"],
                         holder="t")
    assert graphs.promote(db, first, T0 + 2)["promoted"]
    second_ids = datasets.simulate(db, "desk", T0 + 10, seed=1, world="b")
    second = graphs.learn(db, "desk", T0 + 11, train=second_ids["train"], validation=second_ids["validation"],
                          holder="t")
    decision = graphs.promote(db, second, T0 + 12)["decision"]
    assert decision["incumbent"] == first and _numbers(decision, graphs.INCUMBENT_KEYS)
    own_before = _numbers(graphs.show(db, first)["decision"])
    assert own_before                                            # its promotion decision quotes its errors
    archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 13)
    out = governance.forget_dataset(db, first_ids["train"], T0 + 20)
    assert out["effects"]["graphs_erased"] == [first] and out["effects"]["decisions_redacted"] == [second]
    assert _numbers(graphs.show(db, first)["decision"]) == []
    kept = graphs.show(db, second)["decision"]
    assert _numbers(kept, graphs.INCUMBENT_KEYS) == []
    assert kept["checks"]["heldout_gain"]["candidate_mse"] is not None      # the other revision's own scores stay
    assert kept["checks"]["heldout_gain"]["passed"] == decision["checks"]["heldout_gain"]["passed"]
    # a restore of an archive taken before the forget ends the same way
    checkpoints.restore(db, archive["id"], tmp_path / "r.sqlite", T0 + 21)
    copy = store.open_existing(tmp_path / "r.sqlite")
    try:
        for revision in (first, second):
            assert _timeless(graphs.show(copy, revision)["decision"]) == _timeless(graphs.show(db, revision)["decision"])
        assert graphs.show(copy, first)["sha256"] == graphs.ERASED_SHA256
    finally:
        copy.close()


def _forgotten_the_old_way(db, dataset_id, now):
    """What forget_dataset did before ADR-048: the blob and note go, the revisions trained on the dataset are
    invalidated, their weights, the dataset's digest and the effects measured on the revisions stay."""
    with store.write(db):
        db.execute("UPDATE datasets SET blob=NULL, note=NULL, deleted_at=? WHERE id=?", (now, dataset_id))
        db.execute("DELETE FROM graph_effects WHERE dataset_id=?", (dataset_id,))
        graphs.invalidate_dependents(db, dataset_id, now)
        db.execute("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects) VALUES('dataset',?,?,'{}')",
                   (str(dataset_id), now))


def _timeless(node):
    """A JSON tree without its times: a restore records the time of its own replay, the live forget its own."""
    if isinstance(node, str) and node[:1] in "{[":
        return _timeless(json.loads(node))
    if isinstance(node, list):
        return [_timeless(item) for item in node]
    if isinstance(node, dict):
        return {k: _timeless(v) for k, v in node.items() if k not in ("at", "decided_at")}
    return node


def _graph_state(db):
    return ([(i, status, sha, size, _timeless(metrics), _timeless(decision or "null")) for i, status, sha, size,
             metrics, decision in db.execute("SELECT id, status, sha256, length(blob), metrics, decision "
                                             "FROM graph_revisions ORDER BY id")],
            db.execute("SELECT id, sha256, deleted_at FROM datasets ORDER BY id").fetchall(),
            db.execute("SELECT count(*) FROM graph_effects").fetchone()[0])


def test_forgetting_again_a_dataset_an_earlier_version_forgot_finishes_the_forget(instance):
    """Review CM2: a dataset forgotten by the previous release kept the weights learned from it in the live file, and
    forget_dataset answered "absent" so the owner could not finish it, while every restore erased them."""
    db, _ = instance
    ids, rev = learned(db)
    _forgotten_the_old_way(db, ids["train"], T0 + 2)
    assert blob(db, rev) and db.execute("SELECT sha256 FROM datasets WHERE id=?", (ids["train"],)).fetchone()[0]
    out = governance.forget_dataset(db, ids["train"], T0 + 3)
    assert out["status"] == "completed" and out["effects"]["graphs_erased"] == [rev]
    assert out["effects"]["digest_removed"] and out["effects"]["by"] == "forget_dataset"
    assert blob(db, rev) == b"" and graphs.show(db, rev)["sha256"] == graphs.ERASED_SHA256
    assert db.execute("SELECT sha256 FROM datasets WHERE id=?", (ids["train"],)).fetchone()[0] == ""
    assert db.execute("SELECT count(*) FROM graph_effects WHERE revision_id=?", (rev,)).fetchone()[0] == 0
    logged = json.loads(db.execute("SELECT effects FROM deletion_log WHERE target_kind='dataset' "
                                   "ORDER BY id DESC LIMIT 1").fetchone()[0])
    assert logged["completed"] and logged["graphs_erased"] == [rev]
    assert governance.forget_dataset(db, ids["train"], T0 + 4) == {"status": "absent", "effects": {}}


def test_the_first_open_finishes_what_an_earlier_version_left_and_matches_a_restore(tmp_path):
    """Review CM2: the live file and its restored copies agree without the owner repeating anything; nothing not
    learned from the forgotten dataset changes."""
    path = tmp_path / "i.sqlite"
    db = store.create(path, T0)
    ids, rev = learned(db)
    other_ids = datasets.simulate(db, "other", T0, seed=2)
    other = graphs.learn(db, "other", T0 + 1, train=other_ids["train"], validation=other_ids["validation"],
                         holder="t")
    archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 1)
    _forgotten_the_old_way(db, ids["train"], T0 + 2)
    untouched = db.execute("SELECT * FROM graph_revisions WHERE id=?", (other,)).fetchone()
    db.close()
    db = store.open_existing(path)
    try:
        assert blob(db, rev) == b"" and graphs.show(db, rev)["sha256"] == graphs.ERASED_SHA256
        assert db.execute("SELECT * FROM graph_revisions WHERE id=?", (other,)).fetchone() == untouched
        logged = json.loads(db.execute("SELECT effects FROM deletion_log WHERE target_kind='dataset' "
                                       "ORDER BY id DESC LIMIT 1").fetchone()[0])
        assert logged["by"] == "upgrade" and logged["graphs_erased"] == [rev]
        assert db.execute("SELECT status FROM checkpoints WHERE id=?", (archive["id"],)).fetchone()[0] == "stale"
        checkpoints.restore(db, archive["id"], tmp_path / "r.sqlite", T0 + 3)
        live = _graph_state(db)
    finally:
        db.close()
    copy = store.open_existing(tmp_path / "r.sqlite")
    try:
        restored = _graph_state(copy)
    finally:
        copy.close()
    assert restored[0] == live[0] and restored[2] == live[2]
    assert [(i, sha) for i, sha, _ in restored[1]] == [(i, sha) for i, sha, _ in live[1]]
    db = store.open_existing(path)             # done once: a second open finds nothing left
    try:
        assert governance.finish_earlier_forgets(db, T0 + 5) is None
    finally:
        db.close()


def test_the_completion_waits_for_a_writable_open(tmp_path):
    path = tmp_path / "i.sqlite"
    db = store.create(path, T0)
    ids, rev = learned(db)
    _forgotten_the_old_way(db, ids["train"], T0 + 2)
    db.close()
    path.chmod(0o444)
    try:
        db = store.open_existing(path)
        try:
            assert blob(db, rev)                          # not done, and the open did not fail
        finally:
            db.close()
    finally:
        # (a read-only open leaves its -wal/-shm read-only too, an older SQLite matter: give them back)
        for name in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            if name.exists():
                name.chmod(0o600)
    db = store.open_existing(path)
    try:
        assert blob(db, rev) == b""
    finally:
        db.close()
