"""Verified checkpoint archives of the instance database.

`create` takes a consistent online backup (the writer may stay open) and packs it as an NSFA archive.
A deletion that commits while the archive is being written makes the new checkpoint 'stale' at once.
`restore` never publishes a partial file and re-applies current tombstones, forgotten datasets (with their
graph cascade), forgotten roots and web tombstones (forgotten research tasks, knowledge versions and sources, with
the same cascade as the live forget: governance.replay_web_forgets), and drops epoch streams the instance no
longer holds, so nothing forgotten comes back.
`prune` keeps the newest valid archives and deletes the rest.

Archives live in `default_dir(db_path)` = `<db parent>/<db stem>.checkpoints`, one folder per instance.
Older instances used `<db parent>/checkpoints`; those archives stay registered by absolute path, so
verify, restore, rebuild and prune keep working on them.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

from .. import archive
from . import governance, store
from .errors import Conflict, Gone, NotFound, ValidationError, WritesPaused
from .schema import migrate

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


def default_dir(db_path):
    """Per-instance archive folder: `<db parent>/<db stem>.checkpoints`."""
    path = Path(db_path).resolve()
    return path.parent / f"{path.stem}.checkpoints"


# Deletions of payload (not the checkpoint bookkeeping that rebuild and prune log).
_LAST_DELETION = "SELECT COALESCE(MAX(id),0) FROM deletion_log WHERE target_kind!='checkpoint'"


def create(db, db_path, out_dir, now, *, policy="cold"):
    """Archive the instance. WritesPaused unless out_dir has room for a full copy plus the free-space reserve.

    If a deletion commits while the archive is written, the checkpoint is registered 'stale' (it may hold the
    deleted payload) and the result carries a "warning".
    """
    if policy not in POLICIES:
        raise ValidationError(f"policy must be one of {', '.join(POLICIES)}")
    out_dir = Path(out_dir)
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)
    # The snapshot and the archive can each approach the database size.
    needed = Path(db_path).stat().st_size + store.config(db).min_free_bytes
    free = shutil.disk_usage(out_dir).free
    if free < needed:
        raise WritesPaused(f"{free} free bytes in {out_dir} is below the {needed} bytes a checkpoint needs "
                           "(database size plus the free-space reserve)")
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    version = store.state_version(db)
    path = out_dir / f"checkpoint-{stamp}-{version}.nsfa"
    last_deletion = db.execute(_LAST_DELETION).fetchone()[0]
    old_umask = os.umask(0o077)
    try:
        archive.convert("checkpoint", Path(db_path), path, methods=POLICIES[policy])
    except FileExistsError:
        raise Conflict(f"{path} already exists; checkpoints are never overwritten") from None
    finally:
        os.umask(old_umask)
    path.chmod(0o600)
    digest, size = _sha256(path), path.stat().st_size
    with store.write(db):
        # Read the bounds after the backup: a concurrent write can only raise them, which errs towards 'stale'.
        max_seq = db.execute("SELECT COALESCE(MAX(seq),0) FROM evidence").fetchone()[0]
        raced = db.execute(_LAST_DELETION).fetchone()[0] > last_deletion
        cursor = db.execute(
            "INSERT INTO checkpoints(path,policy,sha256,bytes,state_version,max_evidence_seq,created_at,status) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (str(path), policy, digest, size, version, max_seq, now, "stale" if raced else "valid"))
        store.bump_state_version(db)
    row = _row(db, cursor.lastrowid)
    if raced:
        row["warning"] = ("a deletion committed while this archive was written, so it may still hold the deleted "
                          "data; it is registered as stale and the next rebuild replaces it")
    return row


def verify(db, checkpoint_id, now):
    row = _row(db, checkpoint_id)
    if row["status"] == "deleted":
        raise Gone(f"Checkpoint {checkpoint_id} was deleted")
    path = Path(row["path"])
    if not path.is_file():
        with store.write(db):
            db.execute("UPDATE checkpoints SET status='missing' WHERE id=?", (checkpoint_id,))
            store.bump_state_version(db)
        return {**_row(db, checkpoint_id), "ok": False}
    if _sha256(path) != row["sha256"]:
        raise Conflict(f"Checkpoint {checkpoint_id} fails verification: archive hash differs from the registry")
    # Decode next to the archive (same protected folder), never into the shared system temp directory.
    with tempfile.TemporaryDirectory(dir=path.parent) as directory:
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
        store.bump_state_version(db)
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
    roots = db.execute("SELECT digest,kind,forgotten_at FROM forgotten_roots").fetchall() if apply_tombstones else []
    web = db.execute("SELECT kind,digest,forgotten_at FROM web_forgotten").fetchall() if apply_tombstones else []
    # Epoch streams leave no tombstone: a stream the live instance no longer holds was forgotten or expired.
    live_streams = set(db.execute("SELECT id,sha256 FROM epoch_streams").fetchall()) if apply_tombstones else None
    removed = blobs = scrubbed = streams = 0
    web_removed = {"tasks": 0, "knowledge": 0, "sources": 0}
    invalidated = []
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
                migrate(copy)  # an older archive gains forgotten_roots before the scrub below
                copy.execute("BEGIN IMMEDIATE")
                candidates = _root_candidates(copy) if roots else {}
                for tomb in tombstones:
                    removed += copy.execute("DELETE FROM evidence WHERE id=?", (tomb[0],)).rowcount
                    copy.execute("INSERT OR IGNORE INTO tombstones(id,seq,epoch,deleted_at,reason) VALUES(?,?,?,?,?)", tomb)
                if forgotten:
                    from . import graphs  # imported lazily, as governance does, to keep modules decoupled
                for dataset_id in forgotten:
                    blobs += copy.execute("UPDATE datasets SET blob=NULL, deleted_at=? "
                                          "WHERE id=? AND deleted_at IS NULL", (now, dataset_id)).rowcount
                    invalidated += graphs.invalidate_dependents(copy, dataset_id, now)["invalidated"]
                if roots:
                    copy.executemany("INSERT OR IGNORE INTO forgotten_roots(digest,kind,forgotten_at) VALUES(?,?,?)",
                                     roots)
                    scrubbed = _scrub_copy(copy, candidates, {digest for digest, _, _ in roots})
                if live_streams is not None:
                    for stream in copy.execute("SELECT id,sha256 FROM epoch_streams").fetchall():
                        if stream not in live_streams:
                            streams += copy.execute("DELETE FROM epoch_streams WHERE id=?", (stream[0],)).rowcount
                if web:
                    copy.executemany("INSERT OR IGNORE INTO web_forgotten(kind,digest,forgotten_at) VALUES(?,?,?)",
                                     web)
                    web_removed = governance.replay_web_forgets(copy, {(k, d) for k, d, _ in web}, now)
                copy.execute("COMMIT")
                copy.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # no frame of a removed row stays behind
                copy.execute("PRAGMA journal_mode=DELETE")
            staged.chmod(0o600)
            try:
                os.link(staged, dest)
            except FileExistsError:
                raise Conflict(f"{dest} already exists; restore never overwrites") from None
    finally:
        os.umask(old_umask)
    return {"checkpoint": checkpoint_id, "path": str(dest), "tombstones_applied": len(tombstones),
            "evidence_removed": removed, "datasets_blanked": blobs, "graphs_invalidated": invalidated,
            "derived_rows_scrubbed": scrubbed, "streams_removed": streams, "web_forgotten_applied": len(web),
            "web_removed": web_removed}


def _root_candidates(copy):
    """Camera/word roots named in the copy (evidence rows and belief summaries), before tombstones apply."""
    found = {("camera" if channel == "camera" else "partner", root) for channel, root in copy.execute(
        "SELECT DISTINCT channel, root FROM evidence WHERE channel IN ('camera','word')")}
    for (summary,) in copy.execute("SELECT summary FROM belief_revisions"):
        try:
            parsed = json.loads(summary)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            for key, kind in (("sensors", "camera"), ("partners", "partner")):
                if isinstance(parsed.get(key), dict):
                    found.update((kind, root) for root in parsed[key])
    return found


def _scrub_copy(copy, candidates, digests):
    """Scrub, in a restore's staged copy, every candidate root that the live instance has forgotten."""
    instance_id = store.birth(copy)["instance_id"]
    roots = {}
    for kind, root in sorted(candidates):
        digest = governance.root_digest(instance_id, root)
        if digest in digests and root not in roots and \
                copy.execute("SELECT 1 FROM evidence WHERE root=? LIMIT 1", (root,)).fetchone() is None:
            roots[root] = (kind, digest)
    return governance.scrub_roots(copy, roots)


def _unlink(rows, now, extra):
    """Delete archive files. Returns (deleted ids, missing ids, deletion_log rows, error or None)."""
    deleted, missing, log = [], [], []
    for row in rows:
        path = Path(row["path"])
        try:
            path.unlink()
            status, failure = "deleted", None
        except FileNotFoundError:
            status, failure = "missing", f"checkpoint file {path} was already gone"
        except OSError as error:
            return deleted, missing, log, Conflict(f"Could not delete checkpoint {row['id']}: {error}")
        (missing if failure else deleted).append(row["id"])
        log.append((row["id"], status, json.dumps({"status": status, **extra}), failure))
    return deleted, missing, log, None


def _record(db, now, log):
    """Record unlinked archives in one transaction (one state_version bump)."""
    if not log:
        return
    with store.write(db):
        db.executemany("UPDATE checkpoints SET status=? WHERE id=?", [(status, i) for i, status, _, _ in log])
        db.executemany("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects,failure) "
                       "VALUES('checkpoint',?,?,?,?)", [(str(i), now, effects, failure) for i, _, effects, failure in log])
        store.bump_state_version(db)


def _check_keep(keep):
    if isinstance(keep, bool) or not isinstance(keep, int) or keep < 1:
        raise ValidationError("keep must be a positive integer")
    return keep


def prune(db, now, keep=7):
    """Delete valid archives beyond the newest `keep`; mark them 'deleted' (or 'missing') and log each one."""
    _check_keep(keep)
    valid = [r for r in listing(db) if r["status"] == "valid"]
    valid.sort(key=lambda r: (r["created_at"], r["id"]), reverse=True)
    deleted, missing, log, error = _unlink(valid[keep:], now, {"pruned": True, "keep": keep})
    _record(db, now, log)
    if error:
        raise error
    return {"kept": [r["id"] for r in valid[:keep]], "deleted": deleted, "missing": missing}


def rebuild_stale(db, db_path, out_dir, now, *, policy="cold", keep=7):
    """Create one fresh checkpoint, delete the stale archive files, then prune valid ones beyond `keep`."""
    _check_keep(keep)
    stale = [r for r in listing(db) if r["status"] == "stale"]
    fresh = create(db, db_path, out_dir, now, policy=policy)
    deleted, missing, log, error = _unlink(stale, now, {"replaced_by": fresh["id"]})
    _record(db, now, log)
    if error:
        raise error
    return {"new_checkpoint": fresh, "deleted": deleted, "missing": missing, "pruned": prune(db, now, keep)}
