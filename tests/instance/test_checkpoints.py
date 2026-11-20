import json
import os
import sqlite3
import threading
import time

import pytest

from neuroscifi.cli import main
from neuroscifi.instance import checkpoints, governance, store
from neuroscifi.instance.errors import Conflict, ValidationError
from _seed import T0, add, pending


def evidence_rows(path):
    with sqlite3.connect(path) as c:
        return c.execute("SELECT * FROM evidence ORDER BY seq").fetchall()


def test_create_registers_and_protects_file(instance, tmp_path):
    db, path = instance
    add(db, "a")
    cp = checkpoints.create(db, path, tmp_path / "cps", T0)
    f = tmp_path / "cps" / f"checkpoint-20270115T080000Z-{cp['state_version']}.nsfa"
    assert f.is_file() and cp["path"] == str(f)
    assert oct(f.stat().st_mode & 0o777) == "0o600" and oct(f.parent.stat().st_mode & 0o777) == "0o700"
    assert cp["status"] == "valid" and cp["max_evidence_seq"] == 1 and cp["bytes"] == f.stat().st_size
    # create bumps state_version, so a clash needs a file already at the next name; it is never overwritten
    clash = tmp_path / "cps" / f"checkpoint-20270115T080000Z-{store.state_version(db)}.nsfa"
    clash.write_bytes(b"precious")
    with pytest.raises(Conflict):
        checkpoints.create(db, path, tmp_path / "cps", T0)
    assert clash.read_bytes() == b"precious"
    assert checkpoints.verify(db, cp["id"], T0 + 1)["verified_at"] == T0 + 1
    assert [c["id"] for c in checkpoints.listing(db)] == [cp["id"]]


def test_consistent_backup_with_writer_open(instance, tmp_path):
    db, path = instance
    add(db, "committed")
    writer = store.connect(path)
    writer.execute("BEGIN IMMEDIATE")
    add(writer, "uncommitted")
    result = {}
    def worker():
        own = store.connect(path)
        try:
            result["cp"] = checkpoints.create(own, path, tmp_path / "cps", T0)
        finally:
            own.close()

    thread = threading.Thread(target=worker)
    thread.start()
    deadline = time.time() + 20
    while not list((tmp_path / "cps").glob("*.nsfa")) and time.time() < deadline:
        time.sleep(0.02)
    assert list((tmp_path / "cps").glob("*.nsfa")), "backup should finish while the writer is open"
    writer.execute("COMMIT")
    writer.close()
    thread.join(30)
    cp = result["cp"]
    assert checkpoints.verify(db, cp["id"], T0 + 1)["ok"]
    out = tmp_path / "restored.sqlite"
    checkpoints.restore(db, cp["id"], out, T0 + 2)
    assert [r[1] for r in evidence_rows(out)] == ["committed"]


def test_restore_identical_and_refuses_existing(instance, tmp_path):
    db, path = instance
    for i in range(20):
        add(db, f"e{i}", root=f"r{i}")
    cp = checkpoints.create(db, path, tmp_path / "cps", T0, policy="fast")
    out = tmp_path / "new.sqlite"
    res = checkpoints.restore(db, cp["id"], out, T0 + 1)
    assert res["evidence_removed"] == 0 and oct(out.stat().st_mode & 0o777) == "0o600"
    assert evidence_rows(out) == evidence_rows(path)
    assert not (tmp_path / "new.sqlite-wal").exists()
    existing = tmp_path / "keep.sqlite"
    existing.write_bytes(b"precious")
    with pytest.raises(Conflict):
        checkpoints.restore(db, cp["id"], existing, T0)
    assert existing.read_bytes() == b"precious"
    assert not [p for p in tmp_path.iterdir() if p.is_dir() and p.name != "cps"]


def test_corruption_is_detected_and_leaves_no_temp_files(instance, tmp_path):
    db, path = instance
    add(db, "a")
    cp = checkpoints.create(db, path, tmp_path / "cps", T0)
    f = tmp_path / "cps" / os.listdir(tmp_path / "cps")[0]
    data = bytearray(f.read_bytes())
    data[len(data) // 2] ^= 0xFF
    f.write_bytes(bytes(data))
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    old = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = str(tmp)
    import tempfile
    tempfile.tempdir = None
    try:
        with pytest.raises(Conflict, match="fails verification"):
            checkpoints.verify(db, cp["id"], T0)
        assert list(tmp.iterdir()) == []
        # corruption that keeps the registry hash consistent is still caught by the full decode
        db.execute("UPDATE checkpoints SET sha256=? WHERE id=?", (checkpoints._sha256(f), cp["id"]))
        with pytest.raises(Conflict, match="fails verification"):
            checkpoints.verify(db, cp["id"], T0)
        assert list(tmp.iterdir()) == []
        dest = tmp_path / "out.sqlite"
        with pytest.raises(Conflict):
            checkpoints.restore(db, cp["id"], dest, T0)
        assert not dest.exists() and [p.name for p in tmp_path.iterdir()].count("out.sqlite") == 0
    finally:
        tempfile.tempdir = None
        if old is None:
            del os.environ["TMPDIR"]
        else:
            os.environ["TMPDIR"] = old


def test_missing_file_verify(instance, tmp_path):
    db, path = instance
    cp = checkpoints.create(db, path, tmp_path / "cps", T0)
    os.unlink(cp["path"])
    out = checkpoints.verify(db, cp["id"], T0)
    assert out["status"] == "missing" and out["ok"] is False


def test_forget_marks_only_checkpoints_containing_the_event(instance, tmp_path):
    db, path = instance
    add(db, "base")
    before = checkpoints.create(db, path, tmp_path / "cps", T0)
    s = add(db, "target")
    after = checkpoints.create(db, path, tmp_path / "cps", T0 + 1)
    assert before["max_evidence_seq"] < s <= after["max_evidence_seq"]
    out = governance.forget_evidence(db, "target", T0 + 2)
    assert out["effects"]["checkpoints_stale"] == [after["id"]]
    status = dict(db.execute("SELECT id,status FROM checkpoints"))
    assert status == {before["id"]: "valid", after["id"]: "stale"}


def test_no_resurrection_after_restore(instance, tmp_path):
    db, path = instance
    add(db, "keep")
    add(db, "forgotten", root="zz-secret-root")
    cp = checkpoints.create(db, path, tmp_path / "cps", T0)
    governance.forget_evidence(db, "forgotten", T0 + 1)
    out = tmp_path / "r.sqlite"
    res = checkpoints.restore(db, cp["id"], out, T0 + 2)
    assert res["evidence_removed"] == 1
    assert [r[1] for r in evidence_rows(out)] == ["keep"]
    with sqlite3.connect(out) as c:
        assert c.execute("SELECT id,reason FROM tombstones").fetchall() == [("forgotten", "forgotten")]
    # opting out shows the guarantee is the tombstone pass, not an accident
    raw = tmp_path / "raw.sqlite"
    checkpoints.restore(db, cp["id"], raw, T0 + 2, apply_tombstones=False)
    assert [r[1] for r in evidence_rows(raw)] == ["keep", "forgotten"]


def test_rebuild_stale_replaces_and_records_missing(instance, tmp_path):
    db, path = instance
    add(db, "a")
    add(db, "b")
    c1 = checkpoints.create(db, path, tmp_path / "cps", T0)
    c2 = checkpoints.create(db, path, tmp_path / "cps", T0 + 1)
    governance.forget_evidence(db, "a", T0 + 2)
    os.unlink(c2["path"])
    out = checkpoints.rebuild_stale(db, path, tmp_path / "cps", T0 + 3)
    assert out["deleted"] == [c1["id"]] and out["missing"] == [c2["id"]]
    assert not os.path.exists(c1["path"])
    status = dict(db.execute("SELECT id,status FROM checkpoints"))
    assert status[c1["id"]] == "deleted" and status[c2["id"]] == "missing"
    assert status[out["new_checkpoint"]["id"]] == "valid"
    assert checkpoints.verify(db, out["new_checkpoint"]["id"], T0 + 4)["ok"]
    failures = db.execute("SELECT target_id,failure FROM deletion_log WHERE target_kind='checkpoint' ORDER BY id").fetchall()
    assert failures[0] == (str(c1["id"]), None) and "already gone" in failures[1][1]
    restored = tmp_path / "r.sqlite"
    checkpoints.restore(db, out["new_checkpoint"]["id"], restored, T0 + 5)
    assert [r[1] for r in evidence_rows(restored)] == ["b"]


def run_cli(capsys, *argv):
    code = main(list(argv))
    cap = capsys.readouterr()
    return code, (json.loads(cap.out) if cap.out.strip() else None), cap.err


def test_cli_end_to_end(tmp_path, capsys):
    db_path = str(tmp_path / "i.sqlite")
    code, out, _ = run_cli(capsys, "init", "--db", db_path)
    assert code == 0
    db = store.connect(db_path)
    add(db, "e1", root="r-secret", recorded_at=time.time(), expires_at=time.time() + 3600)
    add(db, "e2", epoch_name="e2", recorded_at=time.time(), expires_at=time.time() + 3600)
    add(db, "e3", epoch_name="e2", expires_at=time.time() + 3600)
    pending(db)
    db.close()

    code, out, _ = run_cli(capsys, "memory", "evidence", "--db", db_path, "--channel", "word", "--limit", "1")
    assert code == 0 and len(out["items"]) == 1 and out["items"][0]["kind"] == "evidence"
    code, out, err = run_cli(capsys, "memory", "evidence", "--db", db_path, "--channel", "nope")
    assert code == 2 and "channel" in err

    code, out, _ = run_cli(capsys, "checkpoint", "create", "--db", db_path)
    assert code == 0 and out["status"] == "valid" and (tmp_path / "i.checkpoints").is_dir()
    assert not (tmp_path / "checkpoints").exists()  # per-instance folder, not shared with siblings
    cid = out["id"]
    code, out, _ = run_cli(capsys, "checkpoint", "list", "--db", db_path)
    assert code == 0 and [c["id"] for c in out] == [cid]
    code, out, _ = run_cli(capsys, "checkpoint", "verify", str(cid), "--db", db_path)
    assert code == 0 and out["ok"] is True

    code, out, _ = run_cli(capsys, "promote", "e1", "--db", db_path)
    assert code == 0 and out["status"] == "promoted"
    # e2 reaches its expiry after the checkpoint was taken (commands sweep due rows before running).
    with sqlite3.connect(db_path) as raw:
        raw.execute("UPDATE evidence SET expires_at=? WHERE id='e2'", (time.time() - 1,))
    code, out, _ = run_cli(capsys, "sweep", "--db", db_path)
    assert code == 0 and out["expired"] == 1 and out["checkpoints_stale"] == [cid]
    code, out, _ = run_cli(capsys, "forget", "evidence", "e3", "--db", db_path)
    assert code == 0 and out["status"] == "forgotten" and out["effects"]["checkpoints_stale"] == []
    code, out, _ = run_cli(capsys, "forget", "evidence", "e3", "--db", db_path)
    assert out["status"] == "absent"
    code, _, err = run_cli(capsys, "promote", "e3", "--db", db_path)
    assert code == 2 and "forgotten" in err

    dest = str(tmp_path / "restored.sqlite")
    code, out, _ = run_cli(capsys, "checkpoint", "restore", str(cid), dest, "--db", db_path)
    assert code == 0 and out["evidence_removed"] == 2
    code, _, err = run_cli(capsys, "checkpoint", "restore", str(cid), dest, "--db", db_path)
    assert code == 2 and "already exists" in err

    code, out, _ = run_cli(capsys, "checkpoint", "rebuild", "--db", db_path)
    assert code == 0 and out["deleted"] == [cid]
    rebuilt = out["new_checkpoint"]["id"]
    code, out, _ = run_cli(capsys, "checkpoint", "prune", "--keep", "1", "--db", db_path)
    assert code == 0 and out["deleted"] == [] and out["kept"] == [rebuilt]
    code, out, _ = run_cli(capsys, "forget", "partner", "r-secret", "--db", db_path)
    assert code == 0 and out["effects"]["deleted"] == 1
    code, out, _ = run_cli(capsys, "forget", "epoch", "e2", "--db", db_path)
    # e2 has no evidence left, but its belief revisions (from the sweep and the forget) are deleted with it
    assert code == 0 and out["status"] == "forgotten" and out["effects"]["deleted"] == 0
    assert out["effects"]["derived_deleted"]["belief_revisions"] == 2
    code, out, _ = run_cli(capsys, "memory", "deletion", "--db", db_path)
    assert code == 0 and len(out["items"]) == 5


def test_deletion_during_pack_registers_the_archive_stale(instance, tmp_path, monkeypatch):
    db, path = instance
    add(db, "keep")
    add(db, "secret", root="zz-race-root")
    real = checkpoints.archive.convert

    def convert_then_forget(*args, **kwargs):
        real(*args, **kwargs)  # the archive now holds 'secret'
        governance.forget_evidence(db, "secret", T0 + 1)  # commits before the checkpoint is registered

    monkeypatch.setattr(checkpoints.archive, "convert", convert_then_forget)
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 2)
    assert cp["status"] == "stale" and "deletion committed" in cp["warning"]
    assert checkpoints.listing(db)[0]["status"] == "stale"
    monkeypatch.setattr(checkpoints.archive, "convert", real)
    rebuilt = checkpoints.rebuild_stale(db, path, tmp_path / "cps", T0 + 3)
    assert rebuilt["deleted"] == [cp["id"]] and rebuilt["new_checkpoint"]["status"] == "valid"


def test_checkpoint_bookkeeping_is_not_a_deletion_race(instance, tmp_path):
    db, path = instance
    add(db, "a")
    c1 = checkpoints.create(db, path, tmp_path / "cps", T0)
    checkpoints.prune(db, T0 + 1, keep=1)
    c2 = checkpoints.create(db, path, tmp_path / "cps", T0 + 2)
    assert c1["status"] == c2["status"] == "valid" and "warning" not in c2


def test_restore_invalidates_graphs_built_on_forgotten_datasets(instance, tmp_path):
    db, path = instance
    from neuroscifi.instance import datasets, graphs
    ids = datasets.simulate(db, "desk", T0, seed=3, steps=256)
    rev = graphs.learn(db, "desk", T0 + 1, train=ids["train"], validation=ids["validation"], holder="t")
    assert graphs.promote(db, rev, T0 + 2)["promoted"]
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 3)
    governance.forget_dataset(db, ids["train"], T0 + 4)
    out = checkpoints.restore(db, cp["id"], tmp_path / "r.sqlite", T0 + 5)
    with sqlite3.connect(tmp_path / "r.sqlite") as copy:
        assert copy.execute("SELECT status FROM graph_revisions WHERE id=?", (rev,)).fetchone()[0] == "invalidated"
        assert copy.execute("SELECT count(*) FROM graph_revisions WHERE status='active'").fetchone()[0] == 0
        assert copy.execute("SELECT blob FROM datasets WHERE id=?", (ids["train"],)).fetchone()[0] is None
    assert out["datasets_blanked"] == 1 and out["graphs_invalidated"] == [rev]


def test_restore_scrubs_roots_forgotten_after_the_checkpoint(instance, tmp_path):
    db, path = instance
    from neuroscifi.instance import evidence
    root = "Alice-restore-zq"
    evidence.start_epoch(db, "e", T0)
    for i, bit in enumerate((0, 1, 1)):
        evidence.observe(db, {"id": f"w{i}", "epoch": "e", "channel": "word", "action": 0, "bit": bit,
                              "root": root}, T0 + i)
    evidence.observe(db, {"id": "c0", "epoch": "e", "channel": "camera", "action": 0, "bit": 0,
                          "root": "cam-a"}, T0 + 5)
    db.execute("INSERT INTO decisions(epoch,state_version,decision,scope,reason,created_at) VALUES(?,?,?,?,?,?)",
               ("e", 1, "ask", f"partner:{root}", f"partner {root} is still ambiguous; ask", T0 + 6))
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 7)
    governance.forget_partner(db, root, T0 + 8)
    out = checkpoints.restore(db, cp["id"], tmp_path / "r.sqlite", T0 + 9)
    assert out["evidence_removed"] == 3
    with sqlite3.connect(tmp_path / "r.sqlite") as copy:
        text = "\n".join(str(v) for (t,) in copy.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                         for row in copy.execute(f"SELECT * FROM {t}") for v in row if not isinstance(v, bytes))
        assert root not in text
        live_roots = db.execute("SELECT digest,kind,forgotten_at FROM forgotten_roots").fetchall()
        assert copy.execute("SELECT digest,kind,forgotten_at FROM forgotten_roots").fetchall() == live_roots
        assert copy.execute("SELECT scope FROM decisions").fetchone()[0] == f"partner:#{live_roots[0][0]}"
    assert root.encode() not in (tmp_path / "r.sqlite").read_bytes()
    assert out["derived_rows_scrubbed"] > 0


def test_verify_decodes_next_to_the_archive(instance, tmp_path, monkeypatch):
    db, path = instance
    add(db, "a")
    cp = checkpoints.create(db, path, tmp_path / "cps", T0)
    seen = []
    real = checkpoints.archive.unpack

    def spy(source, dest):
        seen.append(os.path.dirname(os.path.dirname(dest.name)))
        return real(source, dest)

    monkeypatch.setattr(checkpoints.archive, "unpack", spy)
    assert checkpoints.verify(db, cp["id"], T0 + 1)["ok"]
    assert seen == [str(tmp_path / "cps")]
    assert sorted(p.name for p in (tmp_path / "cps").iterdir()) == [os.path.basename(cp["path"])]


def test_create_tightens_an_existing_folder(instance, tmp_path):
    db, path = instance
    folder = tmp_path / "shared"
    folder.mkdir(mode=0o755)
    os.chmod(folder, 0o755)
    checkpoints.create(db, path, folder, T0)
    assert oct(folder.stat().st_mode & 0o777) == "0o700"


def test_create_pauses_without_room_for_a_full_copy(tmp_path):
    from neuroscifi.instance.errors import WritesPaused
    path = tmp_path / "i.sqlite"
    db = store.create(path, T0, config=store.InstanceConfig(min_free_bytes=2 ** 62))
    try:
        before = store.state_version(db)
        with pytest.raises(WritesPaused, match="checkpoint needs"):
            checkpoints.create(db, path, tmp_path / "cps", T0)
        assert list((tmp_path / "cps").iterdir()) == [] and checkpoints.listing(db) == []
        assert store.state_version(db) == before
    finally:
        db.close()


def test_prune_keeps_the_newest_valid_archives(instance, tmp_path):
    db, path = instance
    add(db, "a")
    made = [checkpoints.create(db, path, tmp_path / "cps", T0 + i) for i in range(4)]
    os.unlink(made[0]["path"])
    out = checkpoints.prune(db, T0 + 10, keep=2)
    assert out == {"kept": [made[3]["id"], made[2]["id"]], "deleted": [made[1]["id"]], "missing": [made[0]["id"]]}
    status = {c["id"]: c["status"] for c in checkpoints.listing(db)}
    assert [status[c["id"]] for c in made] == ["missing", "deleted", "valid", "valid"]
    assert not os.path.exists(made[1]["path"]) and os.path.exists(made[2]["path"])
    log = db.execute("SELECT target_id,failure FROM deletion_log WHERE target_kind='checkpoint' ORDER BY id").fetchall()
    assert dict(log) == {str(made[1]["id"]): None, str(made[0]["id"]): log[1][1]} and "already gone" in log[1][1]
    assert checkpoints.prune(db, T0 + 11, keep=2)["deleted"] == []
    with pytest.raises(ValidationError):
        checkpoints.prune(db, T0, keep=0)
    rebuilt = checkpoints.rebuild_stale(db, path, tmp_path / "cps", T0 + 12, keep=1)
    assert rebuilt["pruned"]["kept"] == [rebuilt["new_checkpoint"]["id"]]
    assert sorted(rebuilt["pruned"]["deleted"]) == [made[2]["id"], made[3]["id"]]


def test_each_checkpoint_mutation_bumps_state_version_once(instance, tmp_path):
    db, path = instance
    add(db, "a")
    version = store.state_version(db)

    def bumped():
        nonlocal version
        now, version = store.state_version(db), store.state_version(db)
        return now

    start = version
    cp = checkpoints.create(db, path, tmp_path / "cps", T0)
    assert bumped() == start + 1
    checkpoints.verify(db, cp["id"], T0 + 1)
    assert bumped() == start + 2
    other = checkpoints.create(db, path, tmp_path / "cps", T0 + 2)
    os.unlink(other["path"])
    assert bumped() == start + 3
    checkpoints.verify(db, other["id"], T0 + 3)  # marks it missing
    assert bumped() == start + 4
    checkpoints.create(db, path, tmp_path / "cps", T0 + 4)
    assert bumped() == start + 5
    checkpoints.prune(db, T0 + 5, keep=1)  # deletes cp in one transaction
    assert bumped() == start + 6


def test_default_dir_is_per_instance(tmp_path):
    assert checkpoints.default_dir(tmp_path / "alice.sqlite") == tmp_path.resolve() / "alice.checkpoints"
    assert checkpoints.default_dir(tmp_path / "bob.sqlite") != checkpoints.default_dir(tmp_path / "alice.sqlite")
