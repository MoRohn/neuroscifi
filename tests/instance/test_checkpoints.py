from contextlib import closing
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


def test_restore_drops_voice_sessions_and_audio_forgotten_after_the_checkpoint(instance, tmp_path):
    """A voice session forgotten after a checkpoint, and raw audio swept since, stay gone in a restored copy even
    from an archive that was not rewritten (rewrite=False: the replay is the guarantee); deletion_log keeps only a
    digest label (no session id, no transcript text)."""
    db, path = instance
    from neuroscifi.voice import transcripts as tx
    secret = "my bank PIN is 4417-zq"
    gone = tx.start_session(db, "owner", "console:x", "voice_text", T0)
    seg = tx.upsert(db, gone, "u1", speaker="user", source="asr", status="final", text=secret, now=T0 + 1)
    tx.retain_audio(db, seg["id"], b"\x01\x02" * 50, 16000, T0 + 1, 24)
    kept = tx.start_session(db, "owner", "console:x", "voice_text", T0 + 2)
    swept = tx.upsert(db, kept, "u1", speaker="user", source="asr", status="final", text="keep me", now=T0 + 3)
    tx.retain_audio(db, swept["id"], b"\x03" * 100, 16000, T0 + 3, 1)
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 4)
    assert tx.forget_session(db, gone, now=T0 + 5, rewrite=False)["segments"] == 1
    assert tx.sweep(db, T0 + 2 * 3600) == 1                     # the kept session's audio expired since
    out = checkpoints.restore(db, cp["id"], tmp_path / "r.sqlite", T0 + 2 * 3600 + 1)
    assert out["voice_sessions_removed"] == 1 and out["voice_audio_removed"] == 1   # the swept row
    with sqlite3.connect(tmp_path / "r.sqlite") as copy:
        assert [r[0] for r in copy.execute("SELECT id FROM voice_sessions")] == [kept]
        assert [r[0] for r in copy.execute("SELECT text FROM voice_segments")] == ["keep me"]
        assert copy.execute("SELECT count(*) FROM voice_audio").fetchone()[0] == 0
    assert secret.encode() not in (tmp_path / "r.sqlite").read_bytes()
    log = db.execute("SELECT target_kind,target_id,effects FROM deletion_log").fetchall()
    assert len(log) == 1 and log[0][0] == "voice_session" and log[0][1].startswith("voice_session:#")
    assert gone not in str(log) and secret not in str(log)
    assert db.execute("SELECT status FROM checkpoints WHERE id=?", (cp["id"],)).fetchone()[0] == "stale"
    raw = tmp_path / "raw.sqlite"                                # the guarantee is the replay, not an accident
    checkpoints.restore(db, cp["id"], raw, T0 + 2 * 3600 + 1, apply_tombstones=False)
    with sqlite3.connect(raw) as copy:
        assert copy.execute("SELECT count(*) FROM voice_sessions").fetchone()[0] == 2


def _as_schema_10(db):
    """Put a live individual back at schema 10: web_forgotten with the v10 kind CHECK (no voice kind), and without
    migration 12's node keys, enrolments and nonces or migration 13's retired keys."""
    db.executescript("BEGIN IMMEDIATE; DROP TABLE node_keys_retired; DROP TABLE node_enrolments; DROP TABLE node_nonces; "
                     + "".join(f"ALTER TABLE autonomy_nodes DROP COLUMN {c}; "
                               for c in ("alg", "public_key", "key_fingerprint", "signed")) + "COMMIT;")
    db.executescript("BEGIN IMMEDIATE; CREATE TABLE wf10(kind TEXT NOT NULL CHECK(kind IN ('task','knowledge',"
                     "'source_url','source_domain')), digest TEXT NOT NULL, forgotten_at REAL NOT NULL, "
                     "PRIMARY KEY(kind, digest)); INSERT INTO wf10 SELECT * FROM web_forgotten; "
                     "DROP TABLE web_forgotten; ALTER TABLE wf10 RENAME TO web_forgotten; "
                     "PRAGMA user_version=10; COMMIT;")


def _forget_voice_v10(db, session_id, now):
    """A voice forget as software before migration 11 recorded it: kind 'task', subject "voice:<id>"."""
    with store.write(db):
        governance._delete_voice_session(db, session_id)
        digest = governance.web_digest(store.birth(db)["instance_id"], "task", f"voice:{session_id}")
        db.execute("INSERT INTO web_forgotten(kind,digest,forgotten_at) VALUES('task',?,?)", (digest, now))
        governance._web_finish(db, "voice_session", "voice_session:#" + digest, {}, now)


def test_a_schema_10_checkpoint_restored_onto_11_replays_old_and_new_voice_tombstones(instance, tmp_path):
    """Voice tombstones written before migration 11 (kind 'task') and after it (kind 'voice_session') both drop
    their sessions from a schema-10 archive restored by schema-11 software; the archive's own tombstone is
    relabelled by the restore's migration."""
    db, path = instance
    from neuroscifi.instance import schema
    from neuroscifi.voice import transcripts as tx
    _as_schema_10(db)
    ids = [tx.start_session(db, "owner", "console:x", "voice_text", T0 + i) for i in range(4)]
    for i, sid in enumerate(ids):
        tx.upsert(db, sid, "u1", speaker="user", source="asr", status="final", text=f"said {i}", now=T0 + 10 + i)
    _forget_voice_v10(db, ids[0], T0 + 20)                       # before the checkpoint
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 21)
    _forget_voice_v10(db, ids[1], T0 + 22)                       # after it, still on schema 10
    assert schema.migrate(db) == schema.SCHEMA_VERSION >= 11     # the live instance upgrades
    assert sorted(k for (k,) in db.execute("SELECT kind FROM web_forgotten")) == ["voice_session"] * 2
    tx.forget_session(db, ids[2], now=T0 + 23)                   # after the upgrade
    out = checkpoints.restore(db, cp["id"], tmp_path / "r.sqlite", T0 + 30)
    assert out["voice_sessions_removed"] == 2
    with sqlite3.connect(tmp_path / "r.sqlite") as copy:
        assert copy.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION
        assert [r[0] for r in copy.execute("SELECT id FROM voice_sessions")] == [ids[3]]
        assert [r[0] for r in copy.execute("SELECT text FROM voice_segments")] == ["said 3"]
        assert sorted(k for (k,) in copy.execute("SELECT kind FROM web_forgotten")) == ["voice_session"] * 3
    # A live tombstone that kept the legacy kind (no deletion_log row to relabel it by) is replayed as well.
    with store.write(db):
        db.execute("DELETE FROM deletion_log WHERE target_kind='voice_session'")
        db.execute("UPDATE web_forgotten SET kind='task' WHERE kind='voice_session'")
    out = checkpoints.restore(db, cp["id"], tmp_path / "r2.sqlite", T0 + 31)
    assert out["voice_sessions_removed"] == 2


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


def test_checkpoint_excludes_retained_audio_past_its_ttl(instance, tmp_path):
    """Raw audio whose TTL has passed is never written into a new archive (L7), even when no sweep ran yet."""
    db, path = instance
    from neuroscifi.voice import transcripts as tx
    sid = tx.start_session(db, "owner", "console:x", "voice_text", T0)
    old = tx.upsert(db, sid, "u1", speaker="user", source="asr", status="final", text="old", now=T0 + 1)
    tx.retain_audio(db, old["id"], b"EXPIRED-PCM-" * 40, 16000, T0 + 1, 1)          # expires at T0 + 1 h
    new = tx.upsert(db, sid, "u2", speaker="user", source="asr", status="final", text="new", now=T0 + 2 * 3600)
    tx.retain_audio(db, new["id"], b"LIVE-PCM-" * 40, 16000, T0 + 2 * 3600, 24)
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 2 * 3600 + 5)
    out = tmp_path / "r.sqlite"
    checkpoints.restore(db, cp["id"], out, T0 + 2 * 3600 + 6, apply_tombstones=False)
    with sqlite3.connect(out) as copy:
        assert [r[0] for r in copy.execute("SELECT segment_id FROM voice_audio")] == [new["id"]]
    assert b"EXPIRED-PCM-" not in out.read_bytes() and b"LIVE-PCM-" in out.read_bytes()


KILL_DURING_PACK = """
import os, signal, sys
from pathlib import Path
from neuroscifi import archive
def killed(*args, **kwargs):
    os.kill(os.getpid(), signal.SIGKILL)       # kill -9 while the plaintext snapshot exists
archive.pack = killed
archive.convert("checkpoint", Path(sys.argv[1]), Path(sys.argv[2]))
"""


def _killed_checkpoint(path, out_dir):
    import subprocess
    import sys
    out_dir.mkdir(mode=0o700, exist_ok=True)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    proc = subprocess.run([sys.executable, "-c", KILL_DURING_PACK, str(path), str(out_dir / "x.nsfa")], env=env,
                          capture_output=True, timeout=60)
    assert proc.returncode == -9, proc.stderr.decode()[-400:]
    left = [p for p in out_dir.iterdir() if p.is_dir()]
    assert left and any((p / "snapshot.sqlite").exists() for p in left)   # what the kill leaves behind
    return left


def test_a_checkpoint_killed_mid_pack_leaves_no_copy_behind_after_the_next_checkpoint(instance, tmp_path):
    """T5-03: tmp*/snapshot.sqlite (a full copy) stayed in <db>.checkpoints for good after kill -9, so data
    forgotten afterwards survived there."""
    db, path = instance
    add(db, "secret-evidence")
    out = tmp_path / "cps"
    _killed_checkpoint(path, out)
    cp = checkpoints.create(db, path, out, T0)
    assert sorted(p.name for p in out.iterdir()) == [os.path.basename(cp["path"])]


def test_prune_and_console_start_remove_what_a_killed_checkpoint_left(instance, tmp_path):
    db, path = instance
    add(db, "secret-evidence")
    out = checkpoints.default_dir(path)
    _killed_checkpoint(path, out)
    assert checkpoints.prune(db, T0, keep=1, db_path=path)["deleted"] == []
    assert list(out.iterdir()) == []
    _killed_checkpoint(path, out)
    from neuroscifi.console.server import ConsoleServer
    server = ConsoleServer(path, 0)
    server.server_close()
    assert list(out.iterdir()) == []


def test_an_abandoned_rc1_working_folder_is_removed_but_a_live_one_is_kept(instance, tmp_path):
    from neuroscifi import archive
    db, path = instance
    cp = checkpoints.create(db, path, tmp_path / "cps", T0)
    legacy = tmp_path / "cps" / "tmpab12cd"
    legacy.mkdir()
    (legacy / "snapshot.sqlite").write_bytes(b"copy")
    os.utime(legacy, (time.time() - 2 * archive.LEGACY_AGE,) * 2)
    unrelated = tmp_path / "cps" / "tmpkeep"
    unrelated.mkdir()
    (unrelated / "notes.txt").write_text("not ours")
    os.utime(unrelated, (time.time() - 2 * archive.LEGACY_AGE,) * 2)
    with archive.partial_dir(tmp_path / "cps") as live:
        assert checkpoints.prune(db, T0, keep=1)["kept"] == [cp["id"]]
        assert live.is_dir() and not legacy.exists() and unrelated.is_dir()
    assert not live.exists()


def test_restore_keeps_a_node_revoked_after_the_checkpoint_revoked(instance, tmp_path):
    """T5-C03: a research node revoked after the checkpoint came back active (and owning its sessions)."""
    from neuroscifi.webcortex import nodes
    db, path = instance
    nodes.enrol(db, "device-fp-1", "family-fp", "phone", T0)
    nodes.enrol(db, "device-fp-2", "family-fp", "tablet", T0)
    device = nodes.device_digest(db, "device-fp-1")
    with store.write(db):
        db.execute("INSERT INTO autonomy_sessions(session,family,owner,acquired_at,expires_at) VALUES('s1',?,?,?,?)",
                   ("family", device, T0, T0 + 900))
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 1)
    assert nodes.revoke(db, "phone", T0 + 2) == 1
    out = checkpoints.restore(db, cp["id"], tmp_path / "r.sqlite", T0 + 3)
    assert out["node_revocations_applied"] == 1
    copy = store.open_existing(tmp_path / "r.sqlite")
    try:
        assert nodes.node(copy, device) is None
        assert [n["label"] for n in nodes.listing(copy) if n["revoked_at"] is None] == ["tablet"]
        assert copy.execute("SELECT count(*) FROM autonomy_sessions WHERE owner=?", (device,)).fetchone()[0] == 0
    finally:
        copy.close()


def _node_key():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from nodekeys import NodeKey
    return NodeKey()


def _attested(db, device, family, key, now):
    from neuroscifi.webcortex import nodes
    nodes.attest(db, device, family, key.attestation(nodes.challenge(db, device, family, "pocket", now)), now + 1)
    return nodes.approve(db, key.fingerprint, "phone", now + 2)


def _old_key_verifies(copy, device, family, key):
    """Whether a request signed with `key` is accepted by the restored copy for that device."""
    from neuroscifi.webcortex import nodes
    from neuroscifi.webcortex.signing import SignatureError
    now = time.time()
    h = key.headers("research_cancel", b'{"id":"x"}', timestamp=now)
    try:
        nodes.verify_request(copy, device, family, "POST", "/api/research_cancel", b'{"id":"x"}', h["X-NSF-Timestamp"],
                             h["X-NSF-Nonce"], h["X-NSF-Signature"], now)
        return True
    except SignatureError as refused:
        return refused.code


@pytest.mark.parametrize("revoked_first", [True, False])
def test_restore_never_brings_back_a_retired_node_key(instance, tmp_path, revoked_first):
    """Security review A-2: the owner revokes a node's key K1 (or the node simply re-enrols with K2), then restores a
    checkpoint from before that. The restored copy held K1 as live (0 revocations applied: the device's live row is
    not revoked any more). Revoking and replacing a key now retires its fingerprint, and the replay revokes any
    restored row holding a retired key."""
    from neuroscifi.webcortex import nodes
    db, path = instance
    device, fam = nodes.device_digest(db, "dev-1"), nodes.state.family_digest(db, "fam-1")
    k1, k2 = _node_key(), _node_key()
    _attested(db, device, fam, k1, T0)
    with store.write(db):
        db.execute("INSERT INTO autonomy_sessions(session,family,owner,acquired_at,expires_at) VALUES('s1',?,?,?,?)",
                   (fam, device, T0, T0 + 900))
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 10)
    if revoked_first:
        assert nodes.revoke(db, "phone", T0 + 20) == 1
    _attested(db, device, fam, k2, T0 + 30)
    assert nodes.node(db, device)["key_fingerprint"] == k2.fingerprint
    assert [r[0] for r in db.execute("SELECT fingerprint FROM node_keys_retired")] == [k1.fingerprint]
    out = checkpoints.restore(db, cp["id"], tmp_path / "r.sqlite", T0 + 40)
    assert out["node_revocations_applied"] == 1
    copy = store.open_existing(tmp_path / "r.sqlite")
    try:
        row = nodes._node_row(copy, device)
        assert row["key_fingerprint"] is None and row["revoked_at"] == (T0 + 20 if revoked_first else T0 + 32)
        assert _old_key_verifies(copy, device, fam, k1) == "node_revoked"
        assert copy.execute("SELECT count(*) FROM autonomy_sessions").fetchone()[0] == 0
        assert [r[0] for r in copy.execute("SELECT fingerprint FROM node_keys_retired")] == [k1.fingerprint]
    finally:
        copy.close()


def test_a_key_approved_again_after_revocation_is_live_again_and_restores_live(instance, tmp_path):
    """The same browser keeps its key: when the owner revokes it and later approves it again, it is no longer
    retired, so a restore does not revoke it."""
    from neuroscifi.webcortex import nodes
    db, path = instance
    device, fam = nodes.device_digest(db, "dev-1"), nodes.state.family_digest(db, "fam-1")
    key = _node_key()
    _attested(db, device, fam, key, T0)
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 10)
    nodes.revoke(db, "phone", T0 + 20)
    _attested(db, device, fam, key, T0 + 30)
    assert db.execute("SELECT count(*) FROM node_keys_retired").fetchone()[0] == 0
    checkpoints.restore(db, cp["id"], tmp_path / "r.sqlite", T0 + 40)
    copy = store.open_existing(tmp_path / "r.sqlite")
    try:
        assert nodes.node(copy, device)["key_fingerprint"] == key.fingerprint
        assert _old_key_verifies(copy, device, fam, key) is True
    finally:
        copy.close()


def test_restore_carries_over_recent_node_nonces(instance, tmp_path):
    """Security review A-5: a signed request accepted after the checkpoint was accepted again by the restored copy
    within its freshness window, because the nonce store rolled back with the restore."""
    from neuroscifi.webcortex import nodes
    from neuroscifi.webcortex.signing import SignatureError
    db, path = instance
    now = time.time()
    device, fam = nodes.device_digest(db, "dev-1"), nodes.state.family_digest(db, "fam-1")
    key = _node_key()
    _attested(db, device, fam, key, now)
    cp = checkpoints.create(db, path, tmp_path / "cps", now)
    h = key.headers("research_cancel", b'{"id":"x"}', timestamp=now)
    request = ("POST", "/api/research_cancel", b'{"id":"x"}', h["X-NSF-Timestamp"], h["X-NSF-Nonce"],
               h["X-NSF-Signature"])
    nodes.verify_request(db, device, fam, *request, now + 1)
    with store.write(db):          # a nonce older than the replay window is not carried over
        db.execute("INSERT INTO node_nonces(device,nonce,seen_at) VALUES(?,?,?)", (device, "o" * 16, now - 241))
    out = checkpoints.restore(db, cp["id"], tmp_path / "r.sqlite", now + 3)
    copy = store.open_existing(tmp_path / "r.sqlite")
    try:
        with pytest.raises(SignatureError) as refused:
            nodes.verify_request(copy, device, fam, *request, now + 4)
        assert refused.value.code == "replayed_request"
        assert [r[0] for r in copy.execute("SELECT nonce FROM node_nonces")] == [h["X-NSF-Nonce"]]
    finally:
        copy.close()
    assert out["node_revocations_applied"] == 0


EPOCH_DERIVED = ("evidence", "belief_revisions", "decisions", "pending", "changepoints", "epoch_contexts",
                 "epoch_streams")
NOTHING_LEFT = {t: 0 for t in (*EPOCH_DERIVED, "epoch_links")}


def _teach_epoch_with_every_derived_row(db, epoch, start, *, note=None):
    """An epoch holding a row in every table forget_epoch clears: evidence, revisions, three recorded decisions
    (with the question they open), a changepoint, its graph context, a stream, and a later epoch linked from it."""
    import numpy as np
    from neuroscifi.instance import evidence, executive
    evidence.start_epoch(db, epoch, start, note=note, context="panel")
    for i in range(4):
        evidence.observe(db, {"id": f"{epoch}-{start:.0f}-c{i}", "epoch": epoch, "channel": "camera",
                              "action": i % 2, "bit": i % 2, "root": "cam-room"}, start + i)
    for i, bit in enumerate((0, 1, 0, 1)):
        evidence.observe(db, {"id": f"{epoch}-{start:.0f}-w{i}", "epoch": epoch, "channel": "word", "action": 0,
                              "bit": bit, "root": "Bob-room"}, start + 10 + i)
    for i in range(3):
        executive.decide(db, epoch, start + 20 + i, record=True)
    rng = np.random.default_rng(5)
    evidence.attach_stream(db, epoch, rng.normal(size=(8, 2)), rng.normal(size=(8, 2)), start + 30)
    evidence.start_epoch(db, f"{epoch}-next", start + 31)
    with store.write(db):
        db.execute("INSERT INTO changepoints(epoch,scope,since_seq,pending_id,created_at,confirmed) "
                   "VALUES(?,'camera:cam-room',1,NULL,?,1)", (epoch, start + 32))
        db.execute("INSERT OR REPLACE INTO epoch_links(epoch,source_epoch,segments,pending_id,created_at) "
                   "VALUES(?,?,?,NULL,?)", (f"{epoch}-next", epoch, json.dumps({"mechanism": "mechanism@0"}),
                                            start + 33))


def _epoch_rows(path, epoch):
    """Rows naming `epoch` in each table forget_epoch clears, plus the epoch's note."""
    with closing(sqlite3.connect(path)) as copy:
        rows = {t: copy.execute(f"SELECT count(*) FROM {t} WHERE epoch=?", (epoch,)).fetchone()[0]
                for t in EPOCH_DERIVED}
        rows["epoch_links"] = copy.execute("SELECT count(*) FROM epoch_links WHERE epoch=? OR source_epoch=?",
                                           (epoch, epoch)).fetchone()[0]
    return rows


def test_restore_never_brings_back_any_row_of_an_epoch_forgotten_after_the_checkpoint(instance, tmp_path):
    """MEM-08: a restore of an archive written before forget_epoch holds none of the forgotten epoch's evidence,
    belief revisions, decisions, questions, changepoints, links, graph context, streams or note."""
    db, path = instance
    _teach_epoch_with_every_derived_row(db, "room", T0, note="the owner's private note zq")
    before = _epoch_rows(path, "room")
    assert all(before[t] for t in (*EPOCH_DERIVED, "epoch_links")), before  # precondition: every table is hit
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 40)
    out = governance.forget_epoch(db, "room", T0 + 41)
    assert cp["id"] in out["effects"]["checkpoints_stale"]
    assert _epoch_rows(path, "room") == NOTHING_LEFT
    checkpoints.restore(db, cp["id"], tmp_path / "r.sqlite", T0 + 42)
    assert _epoch_rows(tmp_path / "r.sqlite", "room") == NOTHING_LEFT
    with closing(sqlite3.connect(tmp_path / "r.sqlite")) as copy:   # the linked epoch itself is kept
        assert copy.execute("SELECT count(*) FROM epochs WHERE name='room-next'").fetchone()[0] == 1


def test_restore_keeps_rows_of_an_epoch_name_reused_after_it_was_forgotten(instance, tmp_path):
    """The epoch forget is replayed only into archives written before it: an archive of the epoch name used again
    afterwards keeps the new rows."""
    db, path = instance
    _teach_epoch_with_every_derived_row(db, "room", T0)
    governance.forget_epoch(db, "room", T0 + 41)
    _teach_epoch_with_every_derived_row(db, "room", T0 + 100)
    again = _epoch_rows(path, "room")
    assert again["decisions"] == 3
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 140)
    checkpoints.restore(db, cp["id"], tmp_path / "r.sqlite", T0 + 141)
    assert _epoch_rows(tmp_path / "r.sqlite", "room") == again


def test_restore_rewrites_revision_causes_naming_an_event_forgotten_after_the_checkpoint(instance, tmp_path):
    """The live forget turns a revision cause `evidence:<id>` into evidence:#forgotten; a restore does the same."""
    from neuroscifi.instance import evidence
    db, path = instance
    evidence.start_epoch(db, "e", T0)
    for i, (event, channel) in enumerate((("secret-event-zq", "reference"), ("kept-zq", "camera"))):
        evidence.observe(db, {"id": event, "epoch": "e", "channel": channel, "action": 0, "bit": 1,
                              "root": "std"}, T0 + i)
    assert db.execute("SELECT count(*) FROM belief_revisions WHERE cause='evidence:secret-event-zq'").fetchone()[0]
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 2)
    governance.forget_evidence(db, "secret-event-zq", T0 + 3)
    checkpoints.restore(db, cp["id"], tmp_path / "r.sqlite", T0 + 4)
    with closing(sqlite3.connect(tmp_path / "r.sqlite")) as copy:
        restored = copy.execute("SELECT id, cause, summary FROM belief_revisions ORDER BY id").fetchall()
        assert len(restored) == 2
        assert restored == [db.execute("SELECT id, cause, summary FROM belief_revisions WHERE id=?", (r[0],))
                            .fetchone() for r in restored]
        assert copy.execute("SELECT count(*) FROM belief_revisions WHERE cause='evidence:secret-event-zq' "
                            "OR summary LIKE '%evidence:secret-event-zq%'").fetchone()[0] == 0


def test_forget_epoch_whose_evidence_all_expired_marks_archives_holding_its_derived_rows_stale(instance, tmp_path):
    """Swept evidence leaves the epoch's revisions, decisions and questions behind; an archive written after the
    sweep holds them, so forgetting the epoch must mark it stale even though no evidence row is removed."""
    db, path = instance
    _teach_epoch_with_every_derived_row(db, "room", T0)
    ttl = store.config(db).evidence_ttl_seconds
    assert governance.sweep(db, T0 + 40 + ttl)["expired"] == 8
    assert db.execute("SELECT count(*) FROM decisions WHERE epoch='room'").fetchone()[0] == 3
    cp = checkpoints.create(db, path, tmp_path / "cps", T0 + 41 + ttl)
    out = governance.forget_epoch(db, "room", T0 + 42 + ttl)
    assert out["effects"]["deleted"] == 0 and out["effects"]["checkpoints_stale"] == [cp["id"]]
    assert checkpoints.listing(db)[0]["status"] == "stale"
    checkpoints.restore(db, cp["id"], tmp_path / "r.sqlite", T0 + 43 + ttl)
    assert _epoch_rows(tmp_path / "r.sqlite", "room") == NOTHING_LEFT
