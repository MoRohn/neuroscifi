"""Forget propagates to checkpoint files: a voice-session forget rewrites every archive it made stale, in place and
crash-safe, without the session (checkpoints.rewrite). The rows are searched for in the decoded archives, restore and
verify keep working, a kill at any step leaves the old archive whole or the new one whole, and tombstones still
replay. Archives made stale by kinds a rewrite cannot fully redo keep waiting for `nsf checkpoint rebuild`."""
import io
import json
import os
import random
import signal
import sqlite3
import subprocess
import sys
import time

import pytest

from neuroscifi import archive
from neuroscifi.cli import main
from neuroscifi.instance import checkpoints, governance, store
from neuroscifi.instance.errors import Conflict
from neuroscifi.voice import transcripts as tx
from _seed import T0, add

SECRET = "my bank PIN is 4417-zq"


def decoded(path) -> bytes:
    """The database bytes an archive holds."""
    out = io.BytesIO()
    with open(path, "rb") as source:
        archive.unpack(source, out)
    return out.getvalue()


def sha(path):
    return checkpoints._sha256(path)


def leftovers(folder):
    return sorted(p.name for p in folder.iterdir() if not p.name.endswith(".nsfa"))


def two_checkpoints(db, path, folder):
    """A voice session that says SECRET (with retained audio), a session that stays, and two checkpoints of both."""
    gone = tx.start_session(db, "owner", "console:x", "voice_text", T0)
    seg = tx.upsert(db, gone, "u1", speaker="user", source="asr", status="final", text=SECRET, now=T0 + 1)
    tx.retain_audio(db, seg["id"], b"\x01\x02" * 50, 16000, T0 + 1, 24)
    kept = tx.start_session(db, "owner", "console:x", "voice_text", T0 + 2)
    tx.upsert(db, kept, "u1", speaker="user", source="asr", status="final", text="keep me", now=T0 + 3)
    first = checkpoints.create(db, path, folder, T0 + 4)
    tx.upsert(db, gone, "u2", speaker="user", source="asr", status="final", text=SECRET + " again", now=T0 + 5)
    second = checkpoints.create(db, path, folder, T0 + 6)
    for cp in (first, second):
        assert SECRET.encode() in decoded(cp["path"]) and gone.encode() in decoded(cp["path"])
    return gone, kept, first, second


def test_forgetting_a_voice_session_rewrites_both_checkpoints_without_it(instance, tmp_path, capsys):
    db, path = instance
    folder = tmp_path / "cps"
    gone, kept, first, second = two_checkpoints(db, path, folder)
    assert main(["forget", "voice", gone, "--db", str(path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [r["id"] for r in out["checkpoints_rewritten"]] == [first["id"], second["id"]]
    assert out["checkpoints_stale"] == [] and "next_steps" not in out and "checkpoints_not_rewritten" not in out
    for cp in (first, second):
        row = checkpoints._row(db, cp["id"])
        raw = decoded(row["path"])
        assert SECRET.encode() not in raw and gone.encode() not in raw and b"keep me" in raw
        assert row["status"] == "valid" and row["sha256"] != cp["sha256"] and row["sha256"] == sha(row["path"])
        assert row["bytes"] == os.path.getsize(row["path"]) and row["path"] == cp["path"]
        assert checkpoints.verify(db, cp["id"], T0 + 20)["ok"] is True
        # The file itself no longer holds the session: a restore WITHOUT the tombstone replay shows the same.
        dest = tmp_path / f"raw-{cp['id']}.sqlite"
        checkpoints.restore(db, cp["id"], dest, T0 + 21, apply_tombstones=False)
        with sqlite3.connect(dest) as copy:
            assert [r[0] for r in copy.execute("SELECT id FROM voice_sessions")] == [kept]
            assert copy.execute("SELECT count(*) FROM voice_audio").fetchone()[0] == 0
            assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert SECRET.encode() not in dest.read_bytes()
        restored = checkpoints.restore(db, cp["id"], tmp_path / f"r-{cp['id']}.sqlite", T0 + 22)
        assert restored["voice_sessions_removed"] == 0             # already gone from the file
    assert leftovers(folder) == []                                  # no journal, no working folder
    log = db.execute("SELECT target_kind, target_id, effects FROM deletion_log ORDER BY id").fetchall()
    assert [k for k, _, _ in log] == ["voice_session", "checkpoint", "checkpoint"]
    assert all(json.loads(e)["status"] == "rewritten" for k, _, e in log if k == "checkpoint")
    assert gone not in str(log) and SECRET not in str(log)


def test_nsf_voice_forget_reports_the_rewritten_checkpoints_too(instance, tmp_path, capsys):
    db, path = instance
    gone, _, first, second = two_checkpoints(db, path, tmp_path / "cps")
    assert main(["voice", "forget", gone, "--db", str(path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [r["id"] for r in out["checkpoints_rewritten"]] == [first["id"], second["id"]]


def test_tombstones_still_behave_and_other_kinds_wait_for_rebuild(instance, tmp_path, capsys):
    db, path = instance
    folder = tmp_path / "cps"
    add(db, "ev-1", root="partner-x")
    gone, kept, first, second = two_checkpoints(db, path, folder)
    tx.forget_session(db, gone, now=T0 + 10)
    rewritten = checkpoints._row(db, second["id"])
    # The rewritten archive carries the voice tombstone, so a later restore stays consistent.
    with sqlite3.connect(":memory:") as copy:
        copy.deserialize(decoded(rewritten["path"]))
        assert copy.execute("SELECT kind FROM web_forgotten").fetchall() == [("voice_session",)]
    # An evidence forget makes the archives stale again; its derived rows are not replayable, so no rewrite.
    assert main(["forget", "evidence", "ev-1", "--db", str(path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["checkpoints_rewritten"] == [] and out["checkpoints_stale"] == [first["id"], second["id"]]
    assert all("evidence" in r["reason"] for r in out["checkpoints_not_rewritten"])
    assert out["next_steps"] == [governance.REBUILD_HINT.format(n=2)]
    assert checkpoints._row(db, second["id"])["sha256"] == rewritten["sha256"]     # left as it was
    restored = checkpoints.restore(db, second["id"], tmp_path / "r.sqlite", T0 + 30)
    assert restored["evidence_removed"] == 1 and restored["tombstones_applied"] == 1
    with sqlite3.connect(tmp_path / "r.sqlite") as copy:
        assert copy.execute("SELECT count(*) FROM evidence WHERE id='ev-1'").fetchone()[0] == 0
        assert [r[0] for r in copy.execute("SELECT id FROM voice_sessions")] == [kept]
    with pytest.raises(Conflict, match="evidence"):
        checkpoints.rewrite(db, second["id"], T0 + 31)
    # rebuild still replaces them, as before
    rebuilt = checkpoints.rebuild_stale(db, path, folder, T0 + 32)
    assert sorted(rebuilt["deleted"]) == [first["id"], second["id"]]


def test_an_archive_made_stale_by_a_race_is_not_rewritten(instance, tmp_path):
    db, path = instance
    _, _, first, _ = two_checkpoints(db, path, tmp_path / "cps")
    with store.write(db):
        db.execute("UPDATE checkpoints SET status='stale' WHERE id=?", (first["id"],))
    assert "raced" in checkpoints.rewritable(db, checkpoints._row(db, first["id"]))
    out = checkpoints.rewrite_stale(db, T0 + 10)
    assert out["rewritten"] == [] and out["not_rewritten"][0]["id"] == first["id"]


def test_a_rewrite_refuses_an_archive_whose_file_no_longer_matches(instance, tmp_path):
    db, path = instance
    gone, _, first, _ = two_checkpoints(db, path, tmp_path / "cps")
    tx.forget_session(db, gone, now=T0 + 10, rewrite=False)
    with open(first["path"], "r+b") as handle:
        handle.seek(100)
        handle.write(b"\x00\xff")
    damaged = sha(first["path"])
    with pytest.raises(Conflict, match="archive hash differs"):
        checkpoints.rewrite(db, first["id"], T0 + 11)
    assert sha(first["path"]) == damaged and checkpoints._row(db, first["id"])["status"] == "stale"
    assert leftovers(tmp_path / "cps") == []


# ---- kill -9 during a rewrite --------------------------------------------------------------------------------------
KILL_AT = """
import os, pathlib, signal, sys
from neuroscifi import archive
from neuroscifi.instance import checkpoints, store
db_path, cid, point = sys.argv[1], int(sys.argv[2]), sys.argv[3]
target = checkpoints._row(store.connect(db_path), cid)["path"]
die = lambda: os.kill(os.getpid(), signal.SIGKILL)
real_replace, real_unlink, real_encode = os.replace, pathlib.Path.unlink, archive.encode
calls = []
def encode(data, method):                      # mid-pack: part of the new archive is written
    calls.append(1)
    if point == "pack" and len(calls) == 6:
        die()
    return real_encode(data, method)
def replace(src, dst, *a, **k):
    if str(dst) == target and point == "journal":
        die()                                  # the journal is on disk, the old archive still in place
    real_replace(src, dst, *a, **k)
    if str(dst) == target and point == "replaced":
        die()                                  # the new archive is in place, the registry not yet committed
def unlink(self, *a, **k):
    if self.name.endswith(".rewrite") and point == "committed":
        die()                                  # committed, the journal not yet removed
    return real_unlink(self, *a, **k)
archive.encode, os.replace, pathlib.Path.unlink = encode, replace, unlink
db = store.connect(db_path)
import time
print("ready", flush=True)                     # the parent times its kill from here
started = time.monotonic()
checkpoints.rewrite(db, cid, 1800000100.0)
print("finished", time.monotonic() - started, flush=True)
"""


def run_rewrite(path, cid, point, *, kill_after=None):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    proc = subprocess.Popen([sys.executable, "-c", KILL_AT, str(path), str(cid), point], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    first = proc.stdout.readline().decode()
    if kill_after is not None and first.startswith("ready"):
        time.sleep(kill_after)
        proc.send_signal(signal.SIGKILL)
    out, err = proc.communicate(timeout=300)
    return proc.returncode, first + out.decode(), err.decode()


def whole(db, path, cp, folder):
    """After the next sweep: the registry matches the file, it verifies, and it is either the old archive (still
    stale, still holding the session) or the complete new one (valid, without it). Returns "old" or "new"."""
    checkpoints.sweep(db, path)
    row = checkpoints._row(db, cp["id"])
    assert row["sha256"] == sha(row["path"]) and row["bytes"] == os.path.getsize(row["path"])
    assert checkpoints.verify(db, cp["id"], T0 + 50)["ok"] is True
    holds = SECRET.encode() in decoded(row["path"])
    assert (row["status"], holds) in (("stale", True), ("valid", False)), (row["status"], holds)
    assert leftovers(folder) == []
    return "old" if holds else "new"


@pytest.mark.parametrize("point,expected", [("pack", "old"), ("journal", "old"), ("replaced", "new"),
                                            ("committed", "new")])
def test_a_rewrite_killed_at_each_step_leaves_one_whole_archive(instance, tmp_path, point, expected):
    db, path = instance
    folder = tmp_path / "cps"
    gone, _, first, _ = two_checkpoints(db, path, folder)
    tx.forget_session(db, gone, now=T0 + 10, rewrite=False)
    code, out, err = run_rewrite(path, first["id"], point)
    assert code == -signal.SIGKILL, err[-600:]
    assert whole(db, path, first, folder) == expected
    if expected == "old":                                       # the next forget (or `nsf checkpoint rewrite`) redoes it
        assert checkpoints.rewrite_stale(db, T0 + 60)["rewritten"][0]["id"] == first["id"]
        assert whole(db, path, first, folder) == "new"


def test_kill_9_at_random_moments_never_leaves_a_half_archive(instance, tmp_path):
    db, path = instance
    folder = tmp_path / "cps"
    filler = os.urandom(1 << 20).hex()                      # ~16 MB of incompressible transcript text
    sid = tx.start_session(db, "owner", "console:x", "voice_text", T0 - 10)
    for i in range(8):
        tx.upsert(db, sid, f"f{i}", speaker="user", source="asr", status="final", text=filler[i * 1999:][:1999],
                  now=T0 - 9)
    with store.write(db):
        db.execute("CREATE TABLE filler(b BLOB)")
        db.executemany("INSERT INTO filler VALUES(?)", [(os.urandom(1 << 20),) for _ in range(16)])
    gone, _, first, _ = two_checkpoints(db, path, folder)
    tx.forget_session(db, gone, now=T0 + 10, rewrite=False)
    code, out, _ = run_rewrite(path, first["id"], "none")      # an untouched run, for its duration
    assert code == 0 and "finished" in out
    took = float(out.split()[-1])
    assert whole(db, path, first, folder) == "new"
    rng = random.Random(7)
    killed = 0
    for _ in range(8):                                         # the second archive, killed at random moments
        row = checkpoints._row(db, 2)
        if row["status"] != "stale":
            break
        code, _, _ = run_rewrite(path, 2, "none", kill_after=rng.uniform(0.0, took * 1.1))
        killed += code == -signal.SIGKILL
        whole(db, path, row, folder)
    assert killed >= 1


def test_nsf_checkpoint_rewrite_retries_and_an_unfinished_journal_is_swept(instance, tmp_path, capsys):
    db, path = instance
    folder = tmp_path / "cps"
    gone, _, first, second = two_checkpoints(db, path, folder)
    tx.forget_session(db, gone, now=T0 + 10, rewrite=False)      # e.g. a rewrite that did not run
    unfinished = folder / (os.path.basename(first["path"]) + ".rewrite.tmp")
    unfinished.write_text("{}")                                 # killed while writing its journal
    checkpoints.sweep(db, path)
    assert leftovers(folder) == []
    assert main(["checkpoint", "rewrite", "--id", str(second["id"]), "--db", str(path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [r["id"] for r in out["rewritten"]] == [second["id"]] and out["not_rewritten"] == []
    assert [r["status"] for r in checkpoints.listing(db)] == ["stale", "valid"]
    assert main(["checkpoint", "rewrite", "--db", str(path)]) == 0
    assert [r["id"] for r in json.loads(capsys.readouterr().out)["rewritten"]] == [first["id"]]
