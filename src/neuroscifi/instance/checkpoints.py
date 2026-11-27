"""Verified checkpoint archives of the instance database.

`create` takes a consistent online backup (the writer may stay open) and packs it as an NSFA archive; retained
voice audio past its TTL is deleted first, so it never enters a new archive.
A deletion that commits while the archive is being written makes the new checkpoint 'stale' at once.
`restore` never publishes a partial file and re-applies current tombstones (with the revision causes naming them;
an event the live file has consolidated since, ADR-044, becomes the same consolidated reading in the copy, and a
consolidated reading the owner forgot since is removed from it), forgotten epochs (their derived rows,
governance.drop_epoch_rows, in copies written before the forget), forgotten
datasets (with the graph effects measured on them and their graph cascade), forgotten roots and web tombstones (forgotten research tasks, knowledge versions and sources, with
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
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3

from .. import archive
from . import governance, imagined, store
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


# Deletions of payload (not the checkpoint bookkeeping that rebuild and prune log, nor a web setting's change:
# webcortex.state.SETTING_KIND, which deletes nothing).
_LAST_DELETION = "SELECT COALESCE(MAX(id),0) FROM deletion_log WHERE target_kind NOT IN ('checkpoint','web_setting')"


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
    # The snapshot and the archive can each approach the database size. A damaged reserve is not read as a looser
    # one (review4 M1): the checkpoint is refused, as on main, until the owner repairs it.
    if "min_free_bytes" in store.fixed_damage(db):
        raise WritesPaused(store.fixed_damage_reason(["min_free_bytes"]))
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
    old_umask = os.umask(0o077)
    try:
        with archive.partial_dir(dest.parent) as directory:
            staged = directory / "restored.sqlite"
            try:
                archive.convert("unpack", path, staged)
            except ValueError as error:
                raise Conflict(f"Checkpoint {checkpoint_id} cannot be decoded: {error}") from None
            with closing(sqlite3.connect(staged, isolation_level=None)) as copy:
                if apply_tombstones:
                    with _snapshot(db, now, _journal_seqs(copy)) as forgets:
                        counts = _replay(copy, forgets, now)
                else:
                    counts = _replay(copy, None, now)
                counts |= _artifact_store().after_restore(db, copy, dest, now)   # {} without artifacts
            staged.chmod(0o600)
            try:
                os.link(staged, dest)
            except FileExistsError:
                raise Conflict(f"{dest} already exists; restore never overwrites") from None
    finally:
        os.umask(old_umask)
    return {"checkpoint": checkpoint_id, "path": str(dest), **counts}


# Settings of the live file that a restore or rewrite writes into the copy: every setting the owner may change with
# `nsf config`. They are the owner's current choices, not part of what an archive remembers: an archive taken before
# `--no-consolidation` must not turn consolidation back on in the copy (security review M1), nor one taken after it
# turn it off against the owner's later choice; the same goes, with less at stake, for the ask budget and its
# window. The settings fixed at `nsf init` (lifetime, journal limit, free-space reserve, temperature limit) cannot
# differ between an individual and its own archives, so they stay as the archive has them.
CARRIED_SETTINGS = ("consolidation", "max_consolidated_rows", "ask_budget_per_epoch", "ask_budget_window_seconds",
                    "graph_rule_migration", "max_imagined_rows", "imagined_lifetime_seconds", "quiet",
                    "internal_state_history", "predictive_regulation", "executive_rule", "decision_information",
                    *store.IMAGINE_SWITCHES, "imagine_max_seconds", "imagine_max_bytes",
                    "broadcast", "broadcast_threshold", *store.ARTIFACT_SETTINGS)


READING_BATCH = 500     # seqs per statement when the live file's readings are read for an archive's events


def _journal_seqs(copy):
    """The seqs of the events an archive's decoded copy holds in its journal, ascending: the only events of the copy
    that can become consolidated readings in a replay. 8 bytes each, at most the journal's row limit of them."""
    from array import array
    return array("q", (seq for (seq,) in copy.execute("SELECT seq FROM evidence ORDER BY seq")))


@contextmanager
def _snapshot(db, now, wanted=None):
    """Everything the live instance has forgotten (or revoked) and the owner's settings, read in one snapshot for a
    replay into an archive copy: a forget committing meanwhile is either wholly in it or not at all (a tombstone
    saying 'forgotten' with the reading still listed as held, or the reverse, could keep a forgotten reading).

    The snapshot (a read transaction on the live file; writers carry on, WAL) stays open until the block ends, because
    the tombstones are not read here: _Tombstones reads them in batches while the replay applies them, so what is held
    in memory does not grow with every event the individual ever lost (about 230 MiB per million when they were read
    whole). Run the replay inside the block.

    wanted: the journal seqs of the copy the replay is for (_journal_seqs). The live file's consolidated readings are
    then read for those seqs only, in the same snapshot: a replay asks for a reading only where the copy still holds
    the event, so the result is the same, and what is held in memory is bounded by the archive's journal (one
    lifetime of events, at most its row limit) instead of by every reading the individual ever kept. None reads
    them all.

    While the snapshot is open, a forget committed by another connection cannot truncate the WAL (its
    truncate_wal says "busy"): the frames of the rows it removed stay in the WAL until a checkpoint completes. So
    when the file changed meanwhile (PRAGMA data_version), the WAL is truncated as soon as the snapshot ends."""
    if db.in_transaction:
        yield _read_forgets(db, now, wanted)
        return
    before = db.execute("PRAGMA data_version").fetchone()[0]
    db.execute("BEGIN")             # deferred: the first read takes the snapshot (WAL)
    try:
        yield _read_forgets(db, now, wanted)
    finally:
        db.execute("COMMIT")
        if db.execute("PRAGMA data_version").fetchone()[0] != before:
            store.truncate_wal(db)


def _forgets(db, now, wanted=None):
    """_snapshot's content with the tombstones read whole into a list: for callers that keep it after the snapshot
    ends (tests and the measurement script); a replay runs inside _snapshot instead."""
    with _snapshot(db, now, wanted) as forgets:
        return forgets | {"tombstones": list(forgets["tombstones"])}


TOMBSTONE_BATCH = 2000  # tombstones per fetch while a replay applies them


class _Tombstones:
    """The live file's tombstones (id, seq, epoch, deleted_at, reason), read in the caller's snapshot TOMBSTONE_BATCH
    rows at a time each time they are iterated. Valid only inside _snapshot."""

    def __init__(self, db):
        self.db = db

    def __iter__(self):
        cursor = self.db.execute("SELECT id,seq,epoch,deleted_at,reason FROM tombstones")
        try:
            while batch := cursor.fetchmany(TOMBSTONE_BATCH):
                yield from batch
        finally:
            cursor.close()

    def among(self, ids):
        """The ids in `ids` that have a tombstone (by the table's key, READING_BATCH per statement)."""
        ids, found = list(ids), set()
        for start in range(0, len(ids), READING_BATCH):
            chunk = ids[start:start + READING_BATCH]
            found.update(row[0] for row in self.db.execute(
                f"SELECT id FROM tombstones WHERE id IN ({','.join('?' * len(chunk))})", chunk))
        return found


def _tombstoned(tombstones, ids):
    """The ids in `ids` that have a tombstone in `tombstones` (a _Tombstones or a list of rows)."""
    if isinstance(tombstones, _Tombstones):
        return tombstones.among(ids)
    return {tomb[0] for tomb in tombstones} & set(ids)


def _held_readings(db, wanted):
    """{seq: row} of the live file's consolidated readings: all of them (wanted None), or those whose seq is in
    `wanted`, READING_BATCH seqs per statement by the table's key."""
    columns = "seq,epoch,channel,root,value,sample_id,consolidated_at"
    if wanted is None:
        return {row[0]: row for row in db.execute(f"SELECT {columns} FROM evidence_consolidated")}
    held = {}
    for start in range(0, len(wanted), READING_BATCH):
        chunk = list(wanted[start:start + READING_BATCH])
        held.update((row[0], row) for row in db.execute(
            f"SELECT {columns} FROM evidence_consolidated WHERE seq IN ({','.join('?' * len(chunk))})", chunk))
    return held


def _live_settings(db):
    """({name: value} to carry into a copy, [warnings]). Restore is the recovery tool, so a damaged settings row never
    stops it: a row that cannot be read carries nothing; a value store.set_config would refuse is not carried as it
    stands. For consolidation the value carried is then the one the live file acts on, off (InstanceConfig.from_json:
    never on by truthiness), so the copy cannot keep what the live file would not; a bad ask budget or window is
    left as the archive has it."""
    try:
        effective = asdict(store.config(db))
        raw = json.loads(store._meta(db, "config"))
    except (ValueError, TypeError, NotFound):
        return {}, ["the live instance's settings could not be read: no setting was carried into the copy, which "
                    "keeps the archive's (check `nsf config` on both)"]
    carried, warnings = {}, []
    for name in CARRIED_SETTINGS:
        value = raw.get(name, effective[name])
        if store.valid_setting(name, value):
            carried[name] = value
        elif name in ("consolidation", "graph_rule_migration", "quiet", "internal_state_history",
                      "predictive_regulation", "decision_information", *store.IMAGINE_SWITCHES):
            carried[name] = effective[name]
            acts = "on" if effective[name] else "off"
            warnings.append(f"the live instance's saved {name} ({value!r}) is not true or false; it acts as "
                            f"{acts} there, and {acts} was carried into the copy")
        else:
            warnings.append(f"the live instance's saved {name} ({value!r}) is not a valid value and was not carried; "
                            "the copy keeps the archive's")
    return carried, warnings


def _acting_rule(value):
    """The timer's admission rule a saved tick_admission acts as (scheduler.tick: only "legacy" is the old rule)."""
    return "legacy" if value == "legacy" else "unified"


def _live_rule(db):
    """The live file's acting tick_admission, carried into a copy like CARRIED_SETTINGS; None when unreadable."""
    from ..webcortex.config import META_KEY
    row = db.execute("SELECT value FROM meta WHERE key=?", (META_KEY,)).fetchone()
    try:
        saved = json.loads(row[0]) if row else {}
    except ValueError:
        return None
    return _acting_rule(saved.get("tick_admission")) if isinstance(saved, dict) else None


_LEDGER_ROW = ("capability", "level", "change", "evidence", "generalisation", "regressions", "cost", "reproducibility",
               "decision", "decided_by", "created_at")


def _live_reverts(db):
    """The improvement ledger's owner reverts in force in the live file: for each capability whose latest row is the
    owner's 'revert', that row (_LEDGER_ROW). A restore carries them like quiet (review1 CL3): an archive taken before
    the revert must not switch strategy selection back on."""
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='improvement_ledger'").fetchone() is None:
        return []
    return db.execute(f"SELECT {','.join('l.' + c for c in _LEDGER_ROW)} FROM improvement_ledger l WHERE l.id IN "
                      "(SELECT max(id) FROM improvement_ledger GROUP BY capability) AND l.decision='revert' AND "
                      "l.decided_by='owner' ORDER BY l.id").fetchall()


def _artifact_store():
    from ..artifacts import store as artifact_store      # lazy, as governance imports it (ADR-071)
    return artifact_store


def _learn():
    from ..artifacts import learn                        # lazy, as the artifact store (ADR-078)
    return learn


def _read_forgets(db, now, wanted=None):
    from ..webcortex.signing import FRESH_SECONDS
    settings, warnings = _live_settings(db)
    return {
        "settings": settings, "settings_warnings": warnings, "tick_admission": _live_rule(db),
        "reverts": _live_reverts(db),
        # Cameras and partners forgotten by name (governance.SCOPE_KINDS): their changepoints carry no tombstone, so
        # the deletion log is the record, as for epochs below.
        "scopes": [(kind, label, json.loads(effects).get("state_version", float("inf")), requested_at, effects)
                   for kind, label, requested_at, effects in db.execute(
                       "SELECT target_kind, target_id, requested_at, effects FROM deletion_log WHERE target_kind IN "
                       f"({','.join('?' * len(governance.SCOPE_KINDS))}) ORDER BY id", governance.SCOPE_KINDS)],
        # Read while the replay applies them, in this snapshot (_snapshot).
        "tombstones": _Tombstones(db),
        # Consolidated readings the live file holds (ADR-044), by seq: an expired event of the copy becomes one only
        # when the live file kept it, with the live row's content. For a replay: only those of the copy's own events.
        "consolidated": _held_readings(db, wanted),
        # The highest evidence seq the live file ever issued or recorded. A consolidated reading is identified by its
        # seq alone, so the restored copy must never issue one of them again for another event.
        "max_seq": max(row[0] or 0 for row in (
            db.execute("SELECT seq FROM sqlite_sequence WHERE name='evidence'").fetchone() or (0,),
            db.execute("SELECT max(seq) FROM evidence").fetchone(),
            db.execute("SELECT max(seq) FROM evidence_consolidated").fetchone(),
            db.execute("SELECT max(seq) FROM tombstones").fetchone())),
        "datasets": [r[0] for r in db.execute("SELECT id FROM datasets WHERE deleted_at IS NOT NULL")],
        "roots": db.execute("SELECT digest,kind,forgotten_at FROM forgotten_roots").fetchall(),
        # One kind of a name the other channel still holds (governance.scrub_roots scoped; schema 15).
        "forgotten_scopes": db.execute("SELECT digest,kind,forgotten_at FROM forgotten_scopes").fetchall(),
        # Forgotten epochs with the state version their forget committed at: an epoch's derived rows carry no
        # tombstone (and its evidence may have expired first), so deletion_log is the record that it was forgotten.
        "epochs": [(name, json.loads(effects).get("state_version", float("inf")), requested_at, effects)
                   for name, requested_at, effects in db.execute(
                       "SELECT target_id, requested_at, effects FROM deletion_log WHERE target_kind='epoch' "
                       "ORDER BY id")],
        "web": db.execute("SELECT kind,digest,forgotten_at FROM web_forgotten").fetchall(),
        # The owner's removal of every own-text fingerprint (`nsf web config --forget-own-text`, ADR-067): no tombstone,
        # so the deletion log is the record, as for epochs above (webcortex.emitted.replay).
        "own_text": [(json.loads(effects).get("state_version", float("inf")), requested_at, effects)
                     for requested_at, effects in db.execute(
                         "SELECT requested_at, effects FROM deletion_log WHERE target_kind='own_text' ORDER BY id")],
        # Imagined records the live file removed (purged, forgotten with a source, expired, evicted; ADR-049).
        "imagined": db.execute("SELECT digest,reason,forgotten_at FROM imagined_forgotten").fetchall()
        if imagined.present(db) else [],
        # Artifacts the live file forgot (ADR-071): their index rows go from the copy too (artifacts/store.py).
        "artifacts": _artifact_store().read_forgotten(db),
        # Artifacts a root forget HELD because its scan could not finish, and the owner's releases (fix lane F4): no
        # tombstone, so the deletion log is the record (artifacts/store.replay_holds).
        "artifact_holds": _artifact_store().read_holds(db),
        # The owner's resets of the form outcomes (`nsf artifact feedback --reset`, ADR-078): no tombstone, so the
        # deletion log is the record, as for own text above (artifacts/learn.replay).
        "form_outcomes": _learn().read_resets(db),
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
    live_consolidated = forgets.get("consolidated", {}) if forgets else {}
    consolidated = unconsolidated = 0
    carried, settings_warnings = {}, list(forgets.get("settings_warnings", ())) if forgets else []
    scope_changes = 0
    forgotten = forgets["datasets"] if forgets else []
    roots = forgets["roots"] if forgets else []
    kind_scopes = forgets.get("forgotten_scopes", []) if forgets else []
    epochs = forgets["epochs"] if forgets else []
    web = forgets["web"] if forgets else []
    live_streams = forgets["streams"] if forgets else None
    live_audio = forgets["audio"] if forgets else None
    revoked = forgets["revoked"] if forgets else []
    retired = forgets["retired"] if forgets else []
    nonces = forgets["nonces"] if forgets else []
    removed = blobs = scrubbed = streams = voice = audio = nodes_revoked = epoch_rows = effects = 0
    named = []          # forgotten roots as an imagined record would name them (governance.naming_forms)
    web_removed = {"tasks": 0, "knowledge": 0, "sources": 0}
    invalidated = []
    copy.execute("PRAGMA secure_delete=ON")
    migrate(copy)  # an older archive gains forgotten_roots before the scrub below
    # Wave 4 lane D (review3c F6): the answer marks' scan of every task result, made before the write transaction
    web_marks = governance.prepare_replay_marks(copy, {(k, d) for k, d, _ in web}) if web else None
    copy.execute("BEGIN IMMEDIATE")
    if forgets and forgets.get("settings"):
        # First of all, so that nothing done to the copy from here on runs under a setting the owner has changed.
        try:
            saved = json.loads(copy.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0])
            before = asdict(store.InstanceConfig.from_json(json.dumps(saved)))
        except (ValueError, TypeError):
            saved, before = None, {}
            settings_warnings.append("the archive's settings could not be read: no setting was written into the copy")
        # (compared with what the archive saved, type included: a damaged value there is replaced by the live one)
        before = before | {name: saved[name] for name in forgets["settings"] if saved and name in saved}
        carried = {name: {"from": before[name], "to": value} for name, value in forgets["settings"].items()
                   if saved is not None and (before[name] != value
                                             or isinstance(before[name], bool) != isinstance(value, bool))}
        if carried:
            saved.update({name: change["to"] for name, change in carried.items()})
            copy.execute("UPDATE meta SET value=? WHERE key='config'", (json.dumps(saved, sort_keys=True),))
            for name, change in sorted(carried.items()):
                copy.execute("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects) VALUES(?,?,?,?)",
                             (store.SETTING_KIND, name, now, json.dumps(change | {"carried_by_restore": True},
                                                                        sort_keys=True)))
    if forgets and forgets.get("tick_admission"):
        # The timer's admission rule (ADR-050) is the owner's current choice too: an archive taken under "legacy"
        # never brings the looser rule back, nor one taken under "unified" undoes a later choice. The other web
        # settings stay as the archive has them.
        from ..webcortex.config import META_KEY
        row = copy.execute("SELECT value FROM meta WHERE key=?", (META_KEY,)).fetchone()
        try:
            web_saved = json.loads(row[0]) if row else {}
        except ValueError:
            web_saved = None
            settings_warnings.append("the archive's web settings could not be read: tick_admission was not written "
                                     "into the copy")
        rule = forgets["tick_admission"]
        if isinstance(web_saved, dict) and _acting_rule(web_saved.get("tick_admission")) != rule:
            change = {"from": _acting_rule(web_saved.get("tick_admission")), "to": rule}
            copy.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (META_KEY, json.dumps(web_saved | {"tick_admission": rule}, sort_keys=True)))
            copy.execute("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects) VALUES(?,?,?,?)",
                         (store.SETTING_KIND, "tick_admission", now,
                          json.dumps(change | {"carried_by_restore": True}, sort_keys=True)))
            carried["tick_admission"] = change
    for row in (forgets.get("reverts") or []) if forgets else []:
        # The owner's revert of a strategy or rule (improvement ledger, append-only) is carried like a setting: the
        # copy gets the same row appended unless its own latest row for that capability is already that revert.
        capability, created_at = row[0], row[-1]
        latest = copy.execute("SELECT decision, decided_by, created_at FROM improvement_ledger WHERE capability=? "
                              "ORDER BY id DESC LIMIT 1", (capability,)).fetchone()
        if latest is not None and latest[:2] == ("revert", "owner") and latest[2] >= created_at:
            continue
        copy.execute(f"INSERT INTO improvement_ledger({','.join(_LEDGER_ROW)}) VALUES({','.join('?' * len(row))})",
                     row)
        carried[f"improvement_ledger:{capability}"] = {"from": latest[0] if latest else None, "to": "revert"}
    candidates = _root_candidates(copy) if roots or kind_scopes else {}
    applied = 0
    for tomb in tombstones:
        applied += 1
        event_id, seq, epoch, _, reason = tomb
        # The live file's consolidated reading of this very event (same id, seq and epoch): the copy keeps what the
        # event taught exactly as the live file does, and loses the rest of it like any expired event.
        kept = live_consolidated.get(seq) if reason == "expired" else None
        if kept is not None and kept[1] == epoch and copy.execute(
                "SELECT 1 FROM evidence WHERE id=? AND seq=? AND epoch=?", (event_id, seq, epoch)).fetchone():
            consolidated += copy.execute(
                "INSERT OR IGNORE INTO evidence_consolidated(seq,epoch,channel,root,value,sample_id,consolidated_at) "
                "VALUES(?,?,?,?,?,?,?)", kept).rowcount
        removed += copy.execute("DELETE FROM evidence WHERE id=?", (event_id,)).rowcount
        if reason != "expired":
            # A consolidated reading the owner forgot since (one event, its partner or its epoch): the live forget
            # changed the tombstone's reason from 'expired', which is how a copy that still holds the reading knows.
            unconsolidated += copy.execute("DELETE FROM evidence_consolidated WHERE seq=? AND epoch=?",
                                           (seq, epoch)).rowcount
        # The copy remembers the stronger reason too: an older archive restored FROM it must lose the reading.
        copy.execute("INSERT INTO tombstones(id,seq,epoch,deleted_at,reason) VALUES(?,?,?,?,?) "
                     "ON CONFLICT(id) DO UPDATE SET reason=excluded.reason, deleted_at=excluded.deleted_at "
                     "WHERE tombstones.reason='expired' AND excluded.reason!='expired'", tomb)
    if forgets:
        # Never issue an evidence seq the live file already used (see _read_forgets "max_seq").
        high = max(forgets.get("max_seq", 0), copy.execute("SELECT coalesce(max(seq),0) FROM evidence").fetchone()[0])
        if copy.execute("UPDATE sqlite_sequence SET seq=max(seq, ?) WHERE name='evidence'", (high,)).rowcount == 0 \
                and high:
            copy.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('evidence',?)", (high,))
    # Revision causes naming a removed event id become evidence:#forgotten, as in the live forget.
    causes = [cause.partition(":")[2] for (cause,) in copy.execute(
        "SELECT DISTINCT cause FROM belief_revisions WHERE cause LIKE 'evidence:%' OR cause LIKE 'forget:%'")]
    gone = _tombstoned(tombstones, causes)
    scrubbed += governance._forget_causes(copy, [i for i in causes if i in gone])
    if forgotten:
        from . import graphs  # imported lazily, as governance does, to keep modules decoupled
    for dataset_id in forgotten:
        blobs += copy.execute("UPDATE datasets SET blob=NULL, note=NULL, deleted_at=? "
                              "WHERE id=? AND deleted_at IS NULL", (now, dataset_id)).rowcount
        copy.execute("UPDATE datasets SET sha256='' WHERE id=?", (dataset_id,))
        effects += copy.execute("DELETE FROM graph_effects WHERE dataset_id=?", (dataset_id,)).rowcount
        invalidated += graphs.invalidate_dependents(copy, dataset_id, now)["invalidated"]
        effects += graphs.erase_learned_from(copy, dataset_id, now)["effects_deleted"]
    if roots:
        copy.executemany("INSERT OR IGNORE INTO forgotten_roots(digest,kind,forgotten_at) VALUES(?,?,?)", roots)
        scrubbed += _scrub_copy(copy, candidates, {digest for digest, _, _ in roots}, named)
    if live_streams is not None:
        for stream in copy.execute("SELECT id,sha256 FROM epoch_streams").fetchall():
            if stream not in live_streams:
                streams += copy.execute("DELETE FROM epoch_streams WHERE id=?", (stream[0],)).rowcount
    # An epoch forgotten after this copy was written (its state version is older than the forget's) loses its
    # derived rows as in the live forget; a copy written after the forget may hold the same name used again.
    version = store.state_version(copy)
    scope_changes = governance.replay_scope_forgets(copy, forgets.get("scopes", ()) if forgets else (), version)
    if kind_scopes:     # after the changepoints of a camera or partner forgotten by name (counted there)
        copy.executemany("INSERT OR IGNORE INTO forgotten_scopes(digest,kind,forgotten_at) VALUES(?,?,?)",
                         kind_scopes)
        scrubbed += _scrub_copy_scopes(copy, candidates, {(digest, kind) for digest, kind, _ in kind_scopes},
                                        named)
    for epoch, forgotten_at_version, requested_at, effects_json in epochs:
        if version < forgotten_at_version:
            epoch_rows += sum(governance.drop_epoch_rows(copy, epoch).values())
            # The copy must remember the forget, as it keeps the tombstones above: it can become the individual, and
            # an older archive restored FROM it must lose the epoch too (review: a second restore brought the
            # revisions, decisions, questions and the note back, because the copy's deletion log held no record).
            # In the copy's own history the forget happens now, after its current state version, so an epoch of the
            # same name created in the copy later is newer than the record and is kept.
            if not copy.execute("SELECT 1 FROM deletion_log WHERE target_kind='epoch' AND target_id=? AND "
                                "requested_at=?", (epoch, requested_at)).fetchone():
                carried = {k: v for k, v in json.loads(effects_json).items() if k != "checkpoints_stale"} \
                    | {"state_version": version + 1, "replayed": True}     # the archive ids were the live file's
                copy.execute("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects) "
                             "VALUES('epoch',?,?,?)", (epoch, requested_at, json.dumps(carried, sort_keys=True)))
                # as the live forget does: the archives the copy lists were written before the forget
                copy.execute("UPDATE checkpoints SET status='stale' WHERE status='valid'")
    own_text = forgets.get("own_text") if forgets else None
    if own_text:
        from ..webcortex import emitted
        own_text_removed = emitted.replay(copy, own_text, version)
    if web:
        copy.executemany("INSERT OR IGNORE INTO web_forgotten(kind,digest,forgotten_at) VALUES(?,?,?)", web)
        web_removed = governance.replay_web_forgets(copy, {(k, d) for k, d, _ in web}, now, web_marks)
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
    # Last, after every other replay: imagined records the live file removed since, those past their lifetime, and
    # those derived from anything the replay above removed (ADR-049).
    imagined_removed = imagined.replay(copy, forgets.get("imagined", []) if forgets else [], now)
    # Then, as the live forget did, those whose text holds a root forgotten above (governance._imagined_cascade):
    # what the live tombstones already removed is gone, so this finds only what they do not name.
    imagined_removed += imagined.remove_naming(copy, named, now)[0]
    artifacts_removed = _artifact_store().replay(copy, forgets.get("artifacts", []) if forgets else [], now)
    holds_carried = _artifact_store().replay_holds(copy, forgets.get("artifact_holds") if forgets else None)
    # (an artifact removed above took its form outcomes with it, by schema 21's trigger)
    outcomes_removed = _learn().replay(copy, forgets.get("form_outcomes") if forgets else None, version)
    copy.execute("COMMIT")
    copy.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # no frame of a removed row stays behind
    copy.execute("PRAGMA journal_mode=DELETE")
    return {"tombstones_applied": applied, "evidence_removed": removed,
            "evidence_consolidated": consolidated, "consolidated_removed": unconsolidated,
            "settings_carried": carried, "settings_warnings": settings_warnings,
            "scope_changes_removed": scope_changes,
            "epoch_rows_removed": epoch_rows,
            "datasets_blanked": blobs, "graph_effects_removed": effects,
            "graphs_invalidated": invalidated, "derived_rows_scrubbed": scrubbed, "streams_removed": streams,
            "web_forgotten_applied": len(web), "web_removed": web_removed, "voice_sessions_removed": voice,
            "voice_audio_removed": audio, "node_revocations_applied": nodes_revoked,
            "imagined_removed": imagined_removed,
            **({"artifacts_removed": artifacts_removed} if artifacts_removed else {}),
            **({"artifact_holds_carried": holds_carried} if holds_carried else {}),
            **({"form_outcomes_removed": outcomes_removed} if outcomes_removed else {})} | (
                {"own_text_removed": own_text_removed} if own_text else {})


# ---- rewrite in place after a forget ------------------------------------------------------------------------------
# Forget kinds whose replay into an archive copy (_replay, the same as a restore's) is the live forget's whole
# cascade: governance.replay_voice_forgets and replay_web_forgets. An archive made stale only by these is rewritten
# without the forgotten rows and is valid again. Other kinds (evidence, epoch, partner, dataset, expiry) leave derived
# rows the replay does not reach, and an archive made stale while it was written has no recorded cause: those stay
# stale until `rebuild` replaces them.
REWRITABLE_KINDS = frozenset({"voice_session", "research", "knowledge", "source", imagined.DELETION_KIND,
                              "artifact",        # artifacts/store.py DELETION_KIND: replay() is its whole cascade
                              "own_text",        # webcortex.emitted.DELETION_KIND: its replay is the whole removal
                              "form_outcomes"})  # artifacts/learn.DELETION_KIND: its replay is the whole removal
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
                with _snapshot(db, now, _journal_seqs(copy)) as forgets:
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
        "SELECT DISTINCT channel, root FROM evidence WHERE channel IN ('camera','word') UNION "
        "SELECT DISTINCT channel, root FROM evidence_consolidated WHERE channel IN ('camera','word')")}
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


def _scrub_copy(copy, candidates, digests, named):
    """Scrub, in a restore's staged copy, every candidate root that the live instance has forgotten; their
    governance.naming_forms are added to `named` (the imagined records holding them go after imagined.replay)."""
    instance_id = store.birth(copy)["instance_id"]
    roots = {}
    for kind, root in sorted(candidates):
        digest = governance.root_digest(instance_id, root)
        if digest in digests and root not in roots and not governance.root_held(copy, root):
            roots[root] = (kind, digest)
    named.extend(governance.naming_forms(roots))
    return governance.scrub_roots(copy, roots)


def _scrub_copy_scopes(copy, candidates, forgotten, named):
    """In a restore's staged copy: scrub the kind of every candidate name that the live instance forgot as that kind
    while the other channel kept the name (forgotten: {(digest, kind)}), unless the copy still holds evidence of that
    kind (a newer camera or partner of the same name). A name with no evidence left at all in the copy is scrubbed
    only as that kind too: its other kind is still held by the live instance."""
    instance_id = store.birth(copy)["instance_id"]
    parts = {"camera": {}, "partner": {}}
    for kind, root in sorted(candidates):
        digest = governance.root_digest(instance_id, root)
        channel = "camera" if kind == "camera" else "word"
        if (digest, kind) in forgotten and not governance.root_held(copy, root, channel):
            parts[kind][root] = (kind, digest)
    named.extend(f for part in parts.values() for f in governance.naming_forms(part, scoped=True))
    return sum(governance.scrub_roots(copy, part, scoped=True) for part in parts.values() if part)


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
