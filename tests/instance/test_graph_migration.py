"""The graph-rule migration (ADR-047): an individual made by older software adopts best-learner-v1 and an active graph
worse than no graph is archived, once, at the next open; recorded, reversible, skippable, idempotent, and never
attempted on a file the process cannot write."""
import json
import os
import shutil
import stat

import numpy as np
import pytest

from neuroscifi import cli
from neuroscifi.graph_store import decode, encode
from neuroscifi.instance import checkpoints, datasets, graph_migration, graphs, rules, store

T0 = 1_800_000_000.0
SEED = 813240


def _nsf(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, json.loads(captured.out) if code == 0 else captured.err


def _older_individual(path):
    """A file as software before the migration left it: born under gate-v1 alone, no migration record, with
    - context 'good': learned and promoted under gate-v1, better than no graph (recorded by learn);
    - context 'bad': an active graph promoted before check (f) existed (no beats_no_graph in its metrics) whose
      predictions are the negation of a good fit, so it is worse than no graph on its own validation data;
    - context 'founding': a founding graph, which has no validation data and cannot be judged."""
    db = store.create(path, T0, birth_rules=(store.GATE_V1,))
    ids = datasets.simulate(db, "good", T0, seed=SEED)
    good = graphs.learn(db, "good", T0 + 1, train=ids["train"], validation=ids["validation"], holder="t")
    assert graphs.promote(db, good, T0 + 2)["promoted"]
    bad_ids = datasets.simulate(db, "bad", T0 + 3, seed=SEED + 1)
    fitted = graphs.learn(db, "bad", T0 + 4, train=bad_ids["train"], validation=bad_ids["validation"], holder="t")
    shown = graphs.show(db, fitted)
    blob = encode(-np.asarray(decode(db.execute("SELECT blob FROM graph_revisions WHERE id=?",
                                                (fitted,)).fetchone()[0])), shown["degree"], shown["bits"])
    with store.write(db):
        db.execute("UPDATE graph_revisions SET status='rejected', decision='{\"action\":\"reject\"}' WHERE id=?",
                   (fitted,))
        bad = graphs._insert_revision(db, context="bad", parent_id=None, status="active", blob=blob,
                                      rule_version="gate-v1", origin="learner",
                                      metrics={"train_dataset": bad_ids["train"],
                                               "validation_dataset": bad_ids["validation"]}, now=T0 + 5)
        db.execute("UPDATE graph_revisions SET decision=?, decided_at=? WHERE id=?",
                   (json.dumps({"action": "promote", "promoted": True}), T0 + 5, bad))
        db.executemany("INSERT INTO graph_lineage(revision_id,dataset_id,role) VALUES(?,?,?)",
                       [(bad, bad_ids["train"], "train"), (bad, bad_ids["validation"], "validation")])
    founding = graphs.register_founding(db, encode(np.eye(4) * 0.5, 1, 8), T0 + 6)
    with store.write(db):
        db.execute("DELETE FROM meta WHERE key=?", (graph_migration.MARKER,))
    db.close()
    return {"good": good, "bad": bad, "founding": founding}


@pytest.fixture
def older(tmp_path):
    path = tmp_path / "older.sqlite"
    return path, _older_individual(path)


def _statuses(db):
    return dict(db.execute("SELECT id, status FROM graph_revisions"))


def test_open_migrates_once_keeps_a_good_graph_and_archives_a_bad_one(older):
    path, revs = older
    db = store.open_existing(path)
    try:
        record = graph_migration.status(db)
        assert record["status"] == "applied"
        assert record["rule"] == {"from": "gate-v1", "to": "best-learner-v1"}
        assert [r["version"] for r in rules.listing(db)] == ["gate-v1", "best-learner-v1"]
        assert rules.listing(db)[-1]["created_by"] == "migration"
        assert rules.latest(db) == "best-learner-v1"
        statuses = _statuses(db)
        # (a) the good graph stays active, judged from what learn recorded
        assert statuses[revs["good"]] == "active"
        assert [e["revision"] for e in record["keep"]] == [revs["good"]]
        assert record["keep"][0]["check"]["computed"] is False
        # (b) the bad graph is archived, with a decision that says why; snapshot and metrics kept
        assert statuses[revs["bad"]] == "archived"
        assert [e["revision"] for e in record["demote"]] == [revs["bad"]]
        check = record["demote"][0]["check"]
        assert check["computed"] is True and check["validation_mse"] > check["no_graph_validation_mse"]
        shown = graphs.show(db, revs["bad"])
        assert shown["decision"]["action"] == graphs.DEMOTED and shown["decision"]["by"] == "migration"
        assert shown["decision"]["prior"] == {"action": "promote", "promoted": True}
        assert shown["rule_version"] == "gate-v1" and "validation_dataset" in shown["metrics"]
        # a founding graph cannot be judged and stays active
        assert statuses[revs["founding"]] == "active"
        assert [e["revision"] for e in record["not_judged"]] == [revs["founding"]]
        assert store.inventory(db)["graph_rule_migration"]["status"] == "applied"
        version = store.state_version(db)
    finally:
        db.close()
    # (e) idempotent: the next open changes nothing
    db = store.open_existing(path)
    try:
        assert store.state_version(db) == version
        assert [r["version"] for r in rules.listing(db)] == ["gate-v1", "best-learner-v1"]
        assert graph_migration.apply(db, T0 + 100) is None
    finally:
        db.close()


def test_a_new_graph_is_learned_under_best_learner_after_the_migration(older):
    path, _ = older
    db = store.open_existing(path)
    try:
        ids = datasets.simulate(db, "next", T0 + 10, seed=SEED + 2)
        rev = graphs.learn(db, "next", T0 + 11, train=ids["train"], validation=ids["validation"], holder="t")
        assert graphs.show(db, rev)["rule_version"] == "best-learner-v1"
    finally:
        db.close()


def test_the_deletion_cascade_never_revives_a_demoted_graph(older):
    path, revs = older
    db = store.open_existing(path)
    try:
        ids = datasets.simulate(db, "bad", T0 + 20, seed=SEED + 3)
        rev = graphs.learn(db, "bad", T0 + 21, train=ids["train"], validation=ids["validation"], holder="t")
        assert graphs.promote(db, rev, T0 + 22)["promoted"]
        with store.write(db):
            db.execute("UPDATE datasets SET deleted_at=? WHERE id=?", (T0 + 23, ids["train"]))
            out = graphs.invalidate_dependents(db, ids["train"], T0 + 23)
        assert out["reactivated"] is None and "no eligible snapshot" in out["failure"]
        assert _statuses(db)[revs["bad"]] == "archived"
    finally:
        db.close()


def test_undo_restores_gate_v1_reactivates_and_turns_the_migration_off(older, capsys):
    path, revs = older
    store.open_existing(path).close()
    code, err = _nsf(capsys, "graph", "migration", "undo", "--db", str(path))
    assert code == 2 and "--owner-approved" in err
    code, out = _nsf(capsys, "graph", "migration", "undo", "--owner-approved", "--db", str(path))
    assert code == 0 and out["status"] == "reversed"
    assert {"rule": "gate-v1-restored", "same_as": "gate-v1"} in out["undone"]
    assert {"revision": revs["bad"], "context": "bad", "reactivated": True} in out["undone"]
    assert out["not_undone"] == []
    db = store.open_existing(path)
    try:
        assert rules.latest(db) == "gate-v1-restored"
        assert {k: v for k, v in rules.get(db, "gate-v1-restored").items() if k != "version"} == \
               {k: v for k, v in store.GATE_V1.items() if k != "version"}
        assert _statuses(db)[revs["bad"]] == "active"
        assert graphs.show(db, revs["bad"])["decision"]["action"] == "undo_demote"
        assert store.config(db).graph_rule_migration is False
        logged = db.execute("SELECT effects FROM deletion_log WHERE target_kind='setting' "
                            "AND target_id='graph_rule_migration'").fetchall()
        assert len(logged) == 1 and json.loads(logged[0][0])["to"] is False
        # never runs again on this individual
        assert graph_migration.apply(db, T0 + 200) is None
        assert graph_migration.status(db)["status"] == "reversed"
    finally:
        db.close()
    code, err = _nsf(capsys, "graph", "migration", "undo", "--owner-approved", "--db", str(path))
    assert code != 0 and "Nothing to undo" in err


def test_undo_leaves_a_context_whose_owner_promoted_since(older):
    path, revs = older
    db = store.open_existing(path)
    try:
        ids = datasets.simulate(db, "bad", T0 + 30, seed=SEED + 4)
        rev = graphs.learn(db, "bad", T0 + 31, train=ids["train"], validation=ids["validation"], holder="t")
        assert graphs.promote(db, rev, T0 + 32)["promoted"]
        with store.write(db):
            rules.register(db, dict(store.GATE_V1, version="owners-own"), T0 + 33, created_by="owner")
        out = graph_migration.undo(db, T0 + 34)
        assert out["undone"] == []
        whys = [entry["why"] for entry in out["not_undone"]]
        assert any("owners-own" in why for why in whys)
        assert any(f"revision {rev} is active" in why for why in whys)
        assert _statuses(db)[revs["bad"]] == "archived" and _statuses(db)[rev] == "active"
        assert rules.latest(db) == "owners-own"
    finally:
        db.close()


def test_opting_out_before_the_next_open_leaves_the_file_as_it_is(older, capsys):
    path, revs = older
    code, shown = _nsf(capsys, "graph", "migration", "status", "--db", str(path))
    assert code == 0 and shown["status"] == "pending"
    assert shown["plan"]["rule"] == {"from": "gate-v1", "to": "best-learner-v1"}
    assert [e["revision"] for e in shown["plan"]["demote"]] == [revs["bad"]]
    code, out = _nsf(capsys, "config", "--no-graph-rule-migration", "--db", str(path))
    assert code == 0 and out["changed"] == ["graph_rule_migration"]
    code, _ = _nsf(capsys, "graph", "list", "--db", str(path))
    assert code == 0
    db = store.open_existing(path)
    try:
        assert rules.latest(db) == "gate-v1" and _statuses(db)[revs["bad"]] == "active"
        assert graph_migration.status(db)["status"] == "opted_out"
        assert db.execute("SELECT 1 FROM meta WHERE key=?", (graph_migration.MARKER,)).fetchone() is None
    finally:
        db.close()


def test_a_damaged_setting_reads_as_off(older):
    path, revs = older
    db = store.connect(path)
    try:
        raw = json.loads(db.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0])
        raw["graph_rule_migration"] = "yes"
        db.execute("UPDATE meta SET value=? WHERE key='config'", (json.dumps(raw),))
    finally:
        db.close()
    db = store.open_existing(path)
    try:
        assert rules.latest(db) == "gate-v1" and _statuses(db)[revs["bad"]] == "active"
        assert any("graph_rule_migration" in p for p in store.setting_problems(db))
    finally:
        db.close()


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes through file permissions")
def test_a_read_only_open_writes_nothing_and_migrates_at_the_next_writable_open(older, tmp_path):
    path, revs = older
    folder = tmp_path / "ro"
    folder.mkdir()
    copy = folder / "older.sqlite"
    shutil.copy2(path, copy)
    before = copy.read_bytes()
    copy.chmod(stat.S_IRUSR)
    try:
        db = store.open_existing(copy)
        try:
            assert graph_migration.status(db)["status"] == "pending"
            assert rules.latest(db) == "gate-v1"
        finally:
            db.close()
        assert copy.read_bytes() == before
    finally:
        # SQLite gives the -wal and -shm it creates the database file's permissions, so they are made writable too
        for name in (copy, *(folder / (copy.name + s) for s in ("-wal", "-shm"))):
            if name.exists():
                name.chmod(stat.S_IRUSR | stat.S_IWUSR)
    db = store.connect(copy)             # a connection that may only read (as the idle account's are)
    try:
        db.execute("PRAGMA query_only=ON")
        assert graph_migration.apply(db, T0 + 50) is None
    finally:
        db.close()
    assert copy.read_bytes() == before
    db = store.open_existing(copy)
    try:
        assert graph_migration.status(db)["status"] == "applied"
        assert _statuses(db)[revs["bad"]] == "archived"
    finally:
        db.close()


def test_a_held_lease_postpones_the_migration(older):
    path, _ = older
    db = store.connect(path)
    try:
        db.execute("INSERT INTO graph_leases(context,holder,expires_at) VALUES('good','job',?)", (4e9,))
    finally:
        db.close()
    db = store.open_existing(path)
    try:
        assert graph_migration.status(db)["status"] == "pending" and rules.latest(db) == "gate-v1"
        db.execute("DELETE FROM graph_leases")
    finally:
        db.close()
    db = store.open_existing(path)
    try:
        assert graph_migration.status(db)["status"] == "applied"
    finally:
        db.close()


def test_a_new_individual_is_never_migrated(tmp_path):
    db = store.create(tmp_path / "new.sqlite", T0, birth_rules=(store.GATE_V1,))
    db.close()
    db = store.open_existing(tmp_path / "new.sqlite")
    try:
        assert graph_migration.status(db)["status"] == "settled_at_birth"
        assert rules.latest(db) == "gate-v1"                # an explicit choice at birth is kept
        assert "graph_rule_migration" not in store.inventory(db)
    finally:
        db.close()


def test_an_owner_who_already_chose_a_rule_keeps_it(tmp_path):
    path = tmp_path / "chose.sqlite"
    db = store.create(path, T0, birth_rules=(store.GATE_V1, store.BEST_LEARNER_V1,
                                             dict(store.GATE_V1, version="gate-v1-again")))
    with store.write(db):
        db.execute("DELETE FROM meta WHERE key=?", (graph_migration.MARKER,))
    db.close()
    db = store.open_existing(path)
    try:
        record = graph_migration.status(db)
        assert record["status"] == "applied" and record["rule"] is None and "gate-v1-again" in record["rule_note"]
        assert rules.latest(db) == "gate-v1-again"
    finally:
        db.close()


def test_a_restore_carries_the_owners_choice_after_undo(older, tmp_path):
    """An archive taken before the migration has no record of it; restored after the owner undid it, the copy carries
    the owner's setting (off) and is not migrated at its first open."""
    path, revs = older
    db = store.open_existing(path, graph_migration=False)
    try:
        archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 40)
    finally:
        db.close()
    db = store.open_existing(path)
    try:
        assert graph_migration.status(db)["status"] == "applied"
        graph_migration.undo(db, T0 + 41)
        report = checkpoints.restore(db, archive["id"], tmp_path / "restored.sqlite", T0 + 42)
        assert report["settings_carried"]["graph_rule_migration"] == {"from": True, "to": False}
    finally:
        db.close()
    copy = store.open_existing(tmp_path / "restored.sqlite")
    try:
        assert graph_migration.status(copy)["status"] == "opted_out"
        assert rules.latest(copy) == "gate-v1" and _statuses(copy)[revs["bad"]] == "active"
    finally:
        copy.close()


def test_a_restore_of_an_older_archive_is_migrated_like_the_live_file(older, tmp_path):
    path, revs = older
    db = store.open_existing(path, graph_migration=False)
    try:
        archive = checkpoints.create(db, path, tmp_path / "cps", T0 + 40)
    finally:
        db.close()
    db = store.open_existing(path)
    try:
        checkpoints.restore(db, archive["id"], tmp_path / "restored.sqlite", T0 + 42)
    finally:
        db.close()
    copy = store.open_existing(tmp_path / "restored.sqlite")
    try:
        assert graph_migration.status(copy)["status"] == "applied"
        assert rules.latest(copy) == "best-learner-v1" and _statuses(copy)[revs["bad"]] == "archived"
    finally:
        copy.close()
