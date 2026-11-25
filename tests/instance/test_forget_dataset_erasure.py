"""Forgetting a dataset erases what graphs learned from it (ADR-048, schema 15; ENGINEERING_PLAN §6 item 13).

Before: `forget_dataset` invalidated the revisions trained or validated on the dataset, but their weights (an
image of the data they were fitted to) and metrics stayed, because the snapshot's blob was immutable by trigger;
the dataset's digest stayed too. Now an invalidated revision of that lineage loses its blob and metrics, the graph
effects measured on it go, the dataset's digest goes, and a restore does the same. The trigger allows exactly that
update of an invalidated revision and refuses every other, and an erased revision can never be made active again.
"""
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
    assert row[0] == "invalidated" and row[1] == sha
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
