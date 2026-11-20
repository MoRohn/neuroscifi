import json
import os
import sqlite3
import threading
import time

import pytest

from neuroscifi.cli import main
from neuroscifi.instance import checkpoints, governance, store
from neuroscifi.instance.errors import Conflict
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
    with pytest.raises(Conflict):
        checkpoints.create(db, path, tmp_path / "cps", T0)
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
    assert code == 0 and out["status"] == "valid" and (tmp_path / "checkpoints").is_dir()
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
    code, out, _ = run_cli(capsys, "forget", "partner", "r-secret", "--db", db_path)
    assert code == 0 and out["effects"]["deleted"] == 1
    code, out, _ = run_cli(capsys, "forget", "epoch", "e2", "--db", db_path)
    assert code == 0 and out["status"] in ("forgotten", "absent")
    code, out, _ = run_cli(capsys, "memory", "deletion", "--db", db_path)
    assert code == 0 and len(out["items"]) == 4
