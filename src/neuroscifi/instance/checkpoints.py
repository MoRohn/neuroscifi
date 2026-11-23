"""Verified checkpoint archives of the instance database.

`create` takes a consistent online backup (the writer may stay open) and packs it as an NSFA archive; retained
voice audio past its TTL is deleted first, so it never enters a new archive.
A deletion that commits while the archive is being written makes the new checkpoint 'stale' at once.
`restore` never publishes a partial file and re-applies current tombstones (with the revision causes naming them),
forgotten epochs (their derived rows, governance.drop_epoch_rows, in copies written before the forget), forgotten
datasets (with their graph cascade), forgotten roots and web tombstones (forgotten research tasks, knowledge versions and sources, with
the same cascade as the live forget: governance.replay_web_forgets; forgotten voice sessions with their
transcripts and audio: governance.replay_voice_forgets), research-node revocations and retired node keys, and the
nonces of signed node requests still inside their replay window, and drops epoch streams and
retained voice audio the instance no longer holds, so nothing forgotten (or revoked) comes back.
`prune` keeps the newest valid archives and deletes the rest.
`rewrite` (run after a voice-session or web forget: `rewrite_stale`) rewrites a stale archive in place without the
forgotten rows, with the same replay as `restore`, verified and published crash-safe (a journal and an atomic rename;
`_recover` settles a killed rewrite), so the forgotten data leaves older checkpoint files at once instead of waiting
for `rebuild` or `prune`. Archives made stale by kinds the replay cannot fully redo still wait for `rebuild`.
Every conversion works in a private `.nsf-partial-*` folder next to its output, removed when it ends; one left by a
killed process (a full plaintext copy of the instance) is removed by the next conversion, prune or console start
(`sweep`, archive.sweep_partials), so a forgotten record cannot survive in it (UAT T5-03).

Archives live in `default_dir(db_path)` = `<db parent>/<db stem>.checkpoints`, one folder per instance.
Older instances used `<db parent>/checkpoints`; those archives stay registered by absolute path, so
verify, restore, rebuild and prune keep working on them.
"""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3

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
    # Retained raw audio past its TTL is not archived, even when the voice hub's sweep has not run yet
    # (secure_delete overwrites the freed pages). Archives written earlier are immutable (hash-registered): audio
    # in them expires only with the archive itself (prune / rebuild), and a restore drops it again.
    if db.execute("SELECT 1 FROM voice_audio WHERE expires_at<=? LIMIT 1", (now,)).fetchone():  # no idle write
        with store.write(db):
            db.execute("DELETE FROM voice_audio WHERE expires_at<=?", (now,))
        store.truncate_wal(db)
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
    row = _recover(db, _row(db, checkpoint_id))
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
    with archive.partial_dir(path.parent) as directory:
        out = directory / "decoded.sqlite"
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
    row = _recover(db, _row(db, checkpoint_id))
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
    forgets = _forgets(db, now) if apply_tombstones else None
    old_umask = os.umask(0o077)
    try:
        with archive.partial_dir(dest.parent) as directory:
            staged = directory / "restored.sqlite"
            try:
                archive.convert("unpack", path, staged)
            except ValueError as error:
                raise Conflict(f"Checkpoint {checkpoint_id} cannot be decoded: {error}") from None
            with closing(sqlite3.connect(staged, isolation_level=None)) as copy:
                counts = _replay(copy, forgets, now)
            staged.chmod(0o600)
            try:
                os.link(staged, dest)
            except FileExistsError:
                raise Conflict(f"{dest} already exists; restore never overwrites") from None
    finally:
        os.umask(old_umask)
    return {"checkpoint": checkpoint_id, "path": str(dest), **counts}


def _forgets(db, now):
    """Everything the live instance has forgotten (or revoked), read once for a replay into an archive copy."""
    from ..webcortex.signing import FRESH_SECONDS
    return {
        "tombstones": db.execute("SELECT id,seq,epoch,deleted_at,reason FROM tombstones").fetchall(),
        "datasets": [r[0] for r in db.execute("SELECT id FROM datasets WHERE deleted_at IS NOT NULL")],
        "roots": db.execute("SELECT digest,kind,forgotten_at FROM forgotten_roots").fetchall(),
        # Forgotten epochs with the state version their forget committed at: an epoch's derived rows carry no
        # tombstone (and its evidence may have expired first), so deletion_log is the record that it was forgotten.
        "epochs": [(name, json.loads(effects).get("state_version", float("inf"))) for name, effects in db.execute(
            "SELECT target_id, effects FROM deletion_log WHERE target_kind='epoch' ORDER BY id")],
        "web": db.execute("SELECT kind,digest,forgotten_at FROM web_forgotten").fetchall(),
        # Epoch streams leave no tombstone: a stream the live instance no longer holds was forgotten or expired.
        "streams": set(db.execute("SELECT id,sha256 FROM epoch_streams").fetchall()),
        # Retained raw audio likewise: a row the live instance no longer holds was forgotten, swept or expired.
        "audio": set(db.execute("SELECT segment_id,created_at FROM voice_audio").fetchall()),
        # Node revocations are authorisation withdrawn: like tombstones they are replayed, so a restore never brings
        # a revoked research node back as active (UAT T5-C03).
        "revoked": db.execute("SELECT device,revoked_at FROM autonomy_nodes WHERE revoked_at IS NOT NULL").fetchall(),
        # Keys revoked or replaced since (a rotation leaves no revoked row): no restored row may hold one live.
        "retired": db.execute("SELECT fingerprint,retired_at FROM node_keys_retired").fetchall(),
        # Nonces of signed node requests accepted within the replay window (twice the freshness window, as
        # nodes.verify_request keeps them): the copy must refuse those requests again too.
        "nonces": db.execute("SELECT device,nonce,seen_at FROM node_nonces WHERE seen_at>=?",
                             (now - 2 * FRESH_SECONDS,)).fetchall()}


def _replay(copy, forgets, now):
    """Migrate an archive's decoded copy and apply `forgets` (None: nothing) in one transaction, the WAL truncated
    and the journal back to DELETE. Shared by restore and rewrite, so both remove exactly the same rows."""
    tombstones = forgets["tombstones"] if forgets else []
    forgotten = forgets["datasets"] if forgets else []
    roots = forgets["roots"] if forgets else []
    epochs = forgets["epochs"] if forgets else []
    web = forgets["web"] if forgets else []
    live_streams = forgets["streams"] if forgets else None
    live_audio = forgets["audio"] if forgets else None
    revoked = forgets["revoked"] if forgets else []
    retired = forgets["retired"] if forgets else []
    nonces = forgets["nonces"] if forgets else []
    removed = blobs = scrubbed = streams = voice = audio = nodes_revoked = epoch_rows = 0
    web_removed = {"tasks": 0, "knowledge": 0, "sources": 0}
    invalidated = []
    copy.execute("PRAGMA secure_delete=ON")
    migrate(copy)  # an older archive gains forgotten_roots before the scrub below
    copy.execute("BEGIN IMMEDIATE")
    candidates = _root_candidates(copy) if roots else {}
    for tomb in tombstones:
        removed += copy.execute("DELETE FROM evidence WHERE id=?", (tomb[0],)).rowcount
        copy.execute("INSERT OR IGNORE INTO tombstones(id,seq,epoch,deleted_at,reason) VALUES(?,?,?,?,?)", tomb)
    # Revision causes naming a removed event id become evidence:#forgotten, as in the live forget.
    gone = {tomb[0] for tomb in tombstones}
    causes = [cause.partition(":")[2] for (cause,) in copy.execute(
        "SELECT DISTINCT cause FROM belief_revisions WHERE cause LIKE 'evidence:%' OR cause LIKE 'forget:%'")]
    scrubbed += governance._forget_causes(copy, [i for i in causes if i in gone])
    if forgotten:
        from . import graphs  # imported lazily, as governance does, to keep modules decoupled
    for dataset_id in forgotten:
        blobs += copy.execute("UPDATE datasets SET blob=NULL, deleted_at=? "
                              "WHERE id=? AND deleted_at IS NULL", (now, dataset_id)).rowcount
        invalidated += graphs.invalidate_dependents(copy, dataset_id, now)["invalidated"]
    if roots:
        copy.executemany("INSERT OR IGNORE INTO forgotten_roots(digest,kind,forgotten_at) VALUES(?,?,?)", roots)
        scrubbed += _scrub_copy(copy, candidates, {digest for digest, _, _ in roots})
    if live_streams is not None:
        for stream in copy.execute("SELECT id,sha256 FROM epoch_streams").fetchall():
            if stream not in live_streams:
                streams += copy.execute("DELETE FROM epoch_streams WHERE id=?", (stream[0],)).rowcount
    # An epoch forgotten after this copy was written (its state version is older than the forget's) loses its
    # derived rows as in the live forget; a copy written after the forget may hold the same name used again.
    version = store.state_version(copy)
    for epoch, forgotten_at_version in epochs:
        if version < forgotten_at_version:
            epoch_rows += sum(governance.drop_epoch_rows(copy, epoch).values())
    if web:
        copy.executemany("INSERT OR IGNORE INTO web_forgotten(kind,digest,forgotten_at) VALUES(?,?,?)", web)
        web_removed = governance.replay_web_forgets(copy, {(k, d) for k, d, _ in web}, now)
        voice = governance.replay_voice_forgets(copy, {(k, d) for k, d, _ in web}, now)
    # a signed request accepted after the archive was written stays a replay in the copy (security review A-5)
    copy.executemany("INSERT OR IGNORE INTO node_nonces(device,nonce,seen_at) VALUES(?,?,?)", nonces)
    for device, at in revoked:
        # revocation deletes the node's key (ADR-042 draft): a restore or rewrite never brings it back
        hit = copy.execute("UPDATE autonomy_nodes SET revoked_at=?, alg=NULL, public_key=NULL, key_fingerprint=NULL "
                           "WHERE device=? AND (revoked_at IS NULL OR revoked_at<?)", (at, device, at)).rowcount
        copy.execute("DELETE FROM node_enrolments WHERE device=?", (device,))
        nodes_revoked += hit
        copy.execute("DELETE FROM autonomy_sessions WHERE owner=?", (device,))
        copy.execute("UPDATE autonomy_sessions SET offered_to=NULL, offer_expires_at=NULL "
                     "WHERE offered_to=?", (device,))
    # A key the live instance retired (revoked, or replaced by a later enrolment of the same row, which leaves no
    # revoked row) is revoked wherever the copy still holds it, so neither a revocation nor a rotation is undone.
    copy.executemany("INSERT OR IGNORE INTO node_keys_retired(fingerprint,retired_at) VALUES(?,?)", retired)
    for device, at in copy.execute("SELECT n.device, r.retired_at FROM autonomy_nodes n JOIN node_keys_retired r ON "
                                   "r.fingerprint=n.key_fingerprint WHERE n.revoked_at IS NULL").fetchall():
        nodes_revoked += copy.execute("UPDATE autonomy_nodes SET revoked_at=?, alg=NULL, public_key=NULL, "
                                      "key_fingerprint=NULL WHERE device=?", (at, device)).rowcount
        copy.execute("DELETE FROM node_enrolments WHERE device=?", (device,))
        copy.execute("DELETE FROM node_nonces WHERE device=?", (device,))
        copy.execute("DELETE FROM autonomy_sessions WHERE owner=?", (device,))
        copy.execute("UPDATE autonomy_sessions SET offered_to=NULL, offer_expires_at=NULL "
                     "WHERE offered_to=?", (device,))
    copy.execute("DELETE FROM node_enrolments WHERE key_fingerprint IN (SELECT fingerprint FROM node_keys_retired)")
    if live_audio is not None:
        for row in copy.execute("SELECT segment_id,created_at FROM voice_audio").fetchall():
            if row not in live_audio:
                audio += copy.execute("DELETE FROM voice_audio WHERE segment_id=?", (row[0],)).rowcount
    copy.execute("COMMIT")
    copy.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # no frame of a removed row stays behind
    copy.execute("PRAGMA journal_mode=DELETE")
    return {"tombstones_applied": len(tombstones), "evidence_removed": removed, "epoch_rows_removed": epoch_rows,
            "datasets_blanked": blobs,
            "graphs_invalidated": invalidated, "derived_rows_scrubbed": scrubbed, "streams_removed": streams,
            "web_forgotten_applied": len(web), "web_removed": web_removed, "voice_sessions_removed": voice,
            "voice_audio_removed": audio, "node_revocations_applied": nodes_revoked}


# ---- rewrite in place after a forget ------------------------------------------------------------------------------
# Forget kinds whose replay into an archive copy (_replay, the same as a restore's) is the live forget's whole
# cascade: governance.replay_voice_forgets and replay_web_forgets. An archive made stale only by these is rewritten
# without the forgotten rows and is valid again. Other kinds (evidence, epoch, partner, dataset, expiry) leave derived
# rows the replay does not reach, and an archive made stale while it was written has no recorded cause: those stay
# stale until `rebuild` replaces them.
REWRITABLE_KINDS = frozenset({"voice_session", "research", "knowledge", "source"})
JOURNAL_SUFFIX = ".rewrite"


class _Hasher:
    """A write-only sink that hashes what archive.unpack decodes (the rewritten archive is decoded once to check it)."""

    def __init__(self):
        self.digest, self.size = hashlib.sha256(), 0

    def write(self, data):
        self.digest.update(data)
        self.size += len(data)


def _fsync_dir(folder):
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def _folder_lock(folder):
    """An exclusive flock on the archive folder itself (no lock file): one rewrite at a time per folder, and no prune
    or rebuild deletes an archive while it is rewritten."""
    import fcntl
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _journal(path):
    return Path(f"{path}{JOURNAL_SUFFIX}")


def _causes(db, checkpoint_id):
    """The deletion_log kinds that made this archive stale (the rows naming it in effects.checkpoints_stale)."""
    kinds = set()
    for kind, effects in db.execute("SELECT target_kind, effects FROM deletion_log WHERE target_kind!='checkpoint' "
                                    "AND effects LIKE '%checkpoints_stale%'"):
        try:
            stale = json.loads(effects).get("checkpoints_stale") or []
        except (ValueError, AttributeError):
            continue
        if checkpoint_id in stale:
            kinds.add(kind)
    return kinds


def rewritable(db, row) -> str | None:
    """None when this stale archive can be rewritten in place, else why it needs `rebuild` instead."""
    if row["status"] != "stale":
        return f"checkpoint {row['id']} is {row['status']}, not stale"
    kinds = _causes(db, row["id"])
    if not kinds:
        return "no recorded forget made it stale (a deletion raced its creation); rebuild replaces it"
    other = sorted(kinds - REWRITABLE_KINDS)
    if other:
        return f"made stale by a forget of kind {', '.join(other)}, which a rewrite cannot fully replay; rebuild " \
               "replaces it"
    return None


def _recover(db, row):
    """Finish or roll back a rewrite that a killed process left half done (its journal next to the archive). The
    archive file is always whole (it is replaced by a rename), so the journal only says which hash the registry must
    carry: the new one once the rename happened, else the old one stays. Taken under the folder lock, so a rewrite
    still running in another process is never settled half-way. Returns the current row."""
    journal = _journal(row["path"])
    pending = Path(f"{journal}.tmp")
    if not journal.exists() and not pending.exists():
        return row
    path = Path(row["path"])
    with _folder_lock(path.parent):
        pending.unlink(missing_ok=True)          # a journal never finished: no rename can have followed it
        row = _row(db, row["id"])
        if not journal.exists():
            return row
        try:
            info = json.loads(journal.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        actual = _sha256(path) if path.is_file() else None
        if actual is not None and actual == info.get("new_sha256") and row["sha256"] == info.get("old_sha256"):
            with store.write(db):
                _commit_rewrite(db, row["id"], info, info.get("at", 0.0), recovered=True)
        elif actual not in (info.get("old_sha256"), info.get("new_sha256")):
            return row     # neither: not ours to settle; verify reports the mismatch
        journal.unlink(missing_ok=True)
        _fsync_dir(path.parent)
    return _row(db, row["id"])


def _commit_rewrite(db, checkpoint_id, info, now, *, recovered=False):
    """Register the rewritten archive (caller owns the transaction)."""
    db.execute("UPDATE checkpoints SET sha256=?, bytes=?, status='valid', verified_at=? WHERE id=? AND sha256=?",
               (info["new_sha256"], info["bytes"], now, checkpoint_id, info["old_sha256"]))
    effects = {"status": "rewritten", "old_sha256": info["old_sha256"], "new_sha256": info["new_sha256"],
               "bytes": info["bytes"], "removed": info.get("removed", {}), "recovered": recovered}
    db.execute("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects) VALUES('checkpoint',?,?,?)",
               (str(checkpoint_id), now, json.dumps(effects, sort_keys=True)))
    store.bump_state_version(db)


def rewrite(db, checkpoint_id, now):
    """Rewrite one stale archive without what the instance has forgotten since it was written, in place.

    The archive is decoded next to itself (archive.partial_dir: removed however this ends, swept if killed), the same
    replay as a restore is applied, the copy is vacuumed into a new file there (no freed page keeps a removed row)
    and checked (integrity_check), packed with the archive's own policy, decoded again and compared (sha256), then
    published crash-safe: a journal with the old and new hashes is written and fsynced, the new archive replaces the
    old one by rename, the registry takes the new hash, size and status 'valid' in one transaction, and the journal is
    removed. A kill at any point leaves the old archive whole or the new one whole; _recover settles the registry.
    Returns {"id", "path", "old_sha256", "new_sha256", "old_bytes", "bytes", "removed", "seconds"}."""
    import time
    started = time.monotonic()
    row = _recover(db, _row(db, checkpoint_id))
    why = rewritable(db, row)
    if why:
        raise Conflict(why)
    path = Path(row["path"])
    if not path.is_file():
        raise NotFound(f"Checkpoint file {path} is missing")
    forgets = _forgets(db, now)
    old_umask = os.umask(0o077)
    try:
        with _folder_lock(path.parent), archive.partial_dir(path.parent) as directory:
            if _sha256(path) != row["sha256"]:
                raise Conflict(f"Checkpoint {checkpoint_id} fails verification: archive hash differs from the "
                               "registry")
            staged = directory / "rewritten.sqlite"
            with path.open("rb") as source, staged.open("wb") as dest:
                try:
                    archive.unpack(source, dest)
                except ValueError as error:
                    raise Conflict(f"Checkpoint {checkpoint_id} cannot be decoded: {error}") from None
            vacuumed = directory / "vacuumed.sqlite"
            with closing(sqlite3.connect(staged, isolation_level=None)) as copy:
                removed = _replay(copy, forgets, now)
                # VACUUM INTO writes the compacted copy here, not into a temporary database in the system temp
                # directory; no freed page of a removed row is carried over.
                copy.execute("PRAGMA temp_store=MEMORY")
                copy.execute("VACUUM INTO ?", (str(vacuumed),))
            staged.unlink()
            with closing(sqlite3.connect(vacuumed)) as copy:
                check = copy.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise Conflict(f"Checkpoint {checkpoint_id} rewrite failed its database check: {check}")
            raw = _sha256(vacuumed)
            packed = directory / "rewritten.nsfa"
            fd = os.open(packed, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with vacuumed.open("rb") as source, os.fdopen(fd, "wb") as out:
                archive.pack(source, out, POLICIES.get(row["policy"], archive.COLD))
                out.flush()
                os.fsync(out.fileno())
            decoded = _Hasher()
            with packed.open("rb") as source:
                archive.unpack(source, decoded)
            if decoded.digest.hexdigest() != raw:
                raise Conflict(f"Checkpoint {checkpoint_id} rewrite failed verification: the new archive does not "
                               "decode to the rewritten copy")
            info = {"id": checkpoint_id, "old_sha256": row["sha256"], "new_sha256": _sha256(packed),
                    "bytes": packed.stat().st_size, "at": now, "removed": removed}
            with store.write(db):    # no other writer may change the registry row between the check and the commit
                current = db.execute("SELECT sha256, status FROM checkpoints WHERE id=?", (checkpoint_id,)).fetchone()
                if tuple(current) != (row["sha256"], "stale") or not path.is_file():
                    raise Conflict(f"Checkpoint {checkpoint_id} changed while it was rewritten; nothing replaced")
                journal = _journal(path)
                tmp = Path(f"{journal}.tmp")
                tmp.write_text(json.dumps(info, sort_keys=True), encoding="utf-8")
                with tmp.open("rb") as handle:
                    os.fsync(handle.fileno())
                os.replace(tmp, journal)
                _fsync_dir(path.parent)
                os.replace(packed, path)       # atomic: the old archive or the new one, never a mix
                _fsync_dir(path.parent)
                _commit_rewrite(db, checkpoint_id, info, now)
            journal.unlink()
            _fsync_dir(path.parent)
    finally:
        os.umask(old_umask)
    return {"id": checkpoint_id, "path": str(path), "old_sha256": row["sha256"], "new_sha256": info["new_sha256"],
            "old_bytes": row["bytes"], "bytes": info["bytes"], "removed": removed,
            "seconds": round(time.monotonic() - started, 2)}


def rewrite_stale(db, now, ids=None) -> dict:
    """Rewrite every stale archive that can be rewritten (or those in `ids`). Returns {"rewritten": [rewrite()
    results], "not_rewritten": [{"id", "reason"}]}; one archive's failure does not stop the others, and a failed
    one stays stale (its old file untouched)."""
    out = {"rewritten": [], "not_rewritten": []}
    for row in listing(db):
        if row["status"] != "stale" or (ids is not None and row["id"] not in ids):
            continue
        try:
            out["rewritten"].append(rewrite(db, row["id"], now))
        except (Conflict, NotFound, OSError, ValueError, sqlite3.DatabaseError) as error:
            out["not_rewritten"].append({"id": row["id"], "reason": str(error)})
    return out


def after_forget(db, now) -> dict:
    """Rewrite the stale archives a forget left (rewrite_stale) and say what happened, for the forget's result:
    {"checkpoints_rewritten": [{id, path, old_bytes, bytes, seconds}], "checkpoints_not_rewritten": [{id, reason}]
    (only when some were not), "checkpoints_stale": the ids still stale}."""
    done = rewrite_stale(db, now)
    out = {"checkpoints_rewritten": [{k: r[k] for k in ("id", "path", "old_bytes", "bytes", "seconds")}
                                     for r in done["rewritten"]]}
    if done["not_rewritten"]:
        out["checkpoints_not_rewritten"] = done["not_rewritten"]
    out["checkpoints_stale"] = [r["id"] for r in listing(db) if r["status"] == "stale"]
    return out


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
            if path.parent.is_dir():
                with _folder_lock(path.parent):    # not while a rewrite replaces it
                    path.unlink()
            else:
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


def sweep(db, db_path=None) -> int:
    """Remove working directories a killed checkpoint, restore or rewrite left in the archive folders (each holds a
    full copy of the instance; archive.sweep_partials) and settle any rewrite a kill interrupted (_recover). Run by
    every prune and at console start (every conversion also sweeps its folder's working directories; verify,
    restore and rewrite settle their own archive first)."""
    for row in listing(db):
        if row["status"] not in ("deleted", "missing"):
            _recover(db, row)
    folders = {Path(r["path"]).parent for r in listing(db)}
    if db_path is not None:
        folders.add(default_dir(db_path))
    return sum(archive.sweep_partials(folder) for folder in sorted(folders) if folder.is_dir())


def prune(db, now, keep=7, db_path=None):
    """Delete valid archives beyond the newest `keep`; mark them 'deleted' (or 'missing') and log each one.
    Abandoned working directories in the archive folders are removed too (sweep)."""
    _check_keep(keep)
    sweep(db, db_path)
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
