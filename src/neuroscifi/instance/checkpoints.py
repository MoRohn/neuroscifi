"""Verified checkpoint archives of the instance database.

`create` takes a consistent online backup (the writer may stay open) and packs it as an NSFA archive.
`restore` never publishes a partial file and re-applies current tombstones so forgotten ids stay forgotten.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from .. import archive
from . import store
from .errors import Conflict, Gone, NotFound, ValidationError

POLICIES = {"fast": archive.FAST, "cold": archive.COLD, "experimental": archive.TRIAL}
COLUMNS = ("id", "path", "policy", "sha256", "bytes", "state_version", "max_evidence_seq",
           "created_at", "verified_at", "status")


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _row(db, checkpoint_id):
    row = db.execute(f"SELECT {','.join(COLUMNS)} FROM checkpoints WHERE id=?", (checkpoint_id,)).fetchone()
    if row is None:
        raise NotFound(f"Unknown checkpoint {checkpoint_id}")
    return dict(zip(COLUMNS, row))


def listing(db):
    return [dict(zip(COLUMNS, r)) for r in db.execute(f"SELECT {','.join(COLUMNS)} FROM checkpoints ORDER BY id")]


def create(db, db_path, out_dir, now, *, policy="cold"):
    if policy not in POLICIES:
        raise ValidationError(f"policy must be one of {', '.join(POLICIES)}")
    out_dir = Path(out_dir)
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    version = store.state_version(db)
    path = out_dir / f"checkpoint-{stamp}-{version}.nsfa"
    old_umask = os.umask(0o077)
    try:
        archive.convert("checkpoint", Path(db_path), path, methods=POLICIES[policy])
    except FileExistsError:
        raise Conflict(f"{path} already exists; checkpoints are never overwritten") from None
    finally:
        os.umask(old_umask)
    path.chmod(0o600)
    # Read the bounds after the backup: a concurrent write can only raise them, which errs towards 'stale'.
    max_seq = db.execute("SELECT COALESCE(MAX(seq),0) FROM evidence").fetchone()[0]
    with store.write(db):
        cursor = db.execute(
            "INSERT INTO checkpoints(path,policy,sha256,bytes,state_version,max_evidence_seq,created_at,status) "
            "VALUES(?,?,?,?,?,?,?,'valid')",
            (str(path), policy, _sha256(path), path.stat().st_size, version, max_seq, now))
    return _row(db, cursor.lastrowid)


def verify(db, checkpoint_id, now):
    row = _row(db, checkpoint_id)
    if row["status"] == "deleted":
        raise Gone(f"Checkpoint {checkpoint_id} was deleted")
    path = Path(row["path"])
    if not path.is_file():
        with store.write(db):
            db.execute("UPDATE checkpoints SET status='missing' WHERE id=?", (checkpoint_id,))
        return {**_row(db, checkpoint_id), "ok": False}
    if _sha256(path) != row["sha256"]:
        raise Conflict(f"Checkpoint {checkpoint_id} fails verification: archive hash differs from the registry")
    with tempfile.TemporaryDirectory() as directory:
        out = Path(directory) / "decoded.sqlite"
        try:
            with path.open("rb") as source, out.open("wb") as dest:
                archive.unpack(source, dest)
            with closing(sqlite3.connect(out)) as copy:
                check = copy.execute("PRAGMA integrity_check").fetchone()[0]
        except (ValueError, OSError, sqlite3.DatabaseError) as error:
            raise Conflict(f"Checkpoint {checkpoint_id} fails verification: {error}") from None
        if check != "ok":
            raise Conflict(f"Checkpoint {checkpoint_id} fails verification: database check said {check}")
    with store.write(db):
        db.execute("UPDATE checkpoints SET verified_at=? WHERE id=?", (now, checkpoint_id))
    return {**_row(db, checkpoint_id), "ok": True}


def restore(db, checkpoint_id, dest, now, *, apply_tombstones=True):
    row = _row(db, checkpoint_id)
    if row["status"] in ("deleted", "missing"):
        raise Gone(f"Checkpoint {checkpoint_id} is {row['status']}")
    path = Path(row["path"])
    if not path.is_file():
        raise NotFound(f"Checkpoint file {path} is missing")
    dest = Path(dest)
    if dest.exists():
        raise Conflict(f"{dest} already exists; restore never overwrites")
    if not dest.parent.is_dir():
        raise NotFound(f"Destination directory {dest.parent} does not exist")
    if _sha256(path) != row["sha256"]:
        raise Conflict(f"Checkpoint {checkpoint_id} fails verification: archive hash differs from the registry")
    tombstones = db.execute("SELECT id,seq,epoch,deleted_at,reason FROM tombstones").fetchall() if apply_tombstones else []
    forgotten = [r[0] for r in db.execute("SELECT id FROM datasets WHERE deleted_at IS NOT NULL")] if apply_tombstones else []
    removed = blobs = 0
    old_umask = os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(dir=dest.parent) as directory:
            staged = Path(directory) / "restored.sqlite"
            try:
                archive.convert("unpack", path, staged)
            except ValueError as error:
                raise Conflict(f"Checkpoint {checkpoint_id} cannot be decoded: {error}") from None
            with closing(sqlite3.connect(staged, isolation_level=None)) as copy:
                copy.execute("PRAGMA secure_delete=ON")
                copy.execute("BEGIN IMMEDIATE")
                for tomb in tombstones:
                    removed += copy.execute("DELETE FROM evidence WHERE id=?", (tomb[0],)).rowcount
                    copy.execute("INSERT OR IGNORE INTO tombstones(id,seq,epoch,deleted_at,reason) VALUES(?,?,?,?,?)", tomb)
                for dataset_id in forgotten:
                    blobs += copy.execute("UPDATE datasets SET blob=NULL, deleted_at=? "
                                          "WHERE id=? AND deleted_at IS NULL", (now, dataset_id)).rowcount
                copy.execute("COMMIT")
                copy.execute("PRAGMA journal_mode=DELETE")
            staged.chmod(0o600)
            try:
                os.link(staged, dest)
            except FileExistsError:
                raise Conflict(f"{dest} already exists; restore never overwrites") from None
    finally:
        os.umask(old_umask)
    return {"checkpoint": checkpoint_id, "path": str(dest), "tombstones_applied": len(tombstones),
            "evidence_removed": removed, "datasets_blanked": blobs}


def rebuild_stale(db, db_path, out_dir, now, *, policy="cold"):
    """Replace stale checkpoints by one fresh checkpoint, then delete the stale archive files."""
    stale = [r for r in listing(db) if r["status"] == "stale"]
    fresh = create(db, db_path, out_dir, now, policy=policy)
    deleted, missing = [], []
    for row in stale:
        path = Path(row["path"])
        try:
            path.unlink()
            status, failure = "deleted", None
        except FileNotFoundError:
            status, failure = "missing", f"stale checkpoint file {path} was already gone"
        except OSError as error:
            raise Conflict(f"Could not delete stale checkpoint {row['id']}: {error}") from None
        with store.write(db):
            db.execute("UPDATE checkpoints SET status=? WHERE id=?", (status, row["id"]))
            db.execute("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects,failure) "
                       "VALUES('checkpoint',?,?,?,?)",
                       (str(row["id"]), now, json.dumps({"status": status, "replaced_by": fresh["id"]}), failure))
        (missing if failure else deleted).append(row["id"])
    return {"new_checkpoint": fresh, "deleted": deleted, "missing": missing}
