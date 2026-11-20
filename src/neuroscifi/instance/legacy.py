"""Import a prototype `legacy_live.py` database into an individual.

The prototype file is opened read-only. Epochs and observations are copied as observed, learnable,
promoted evidence (promoted so imported history does not silently expire). Re-importing is
idempotent: identical rows are skipped, forgotten ids stay forgotten, and an id bound to a different
observation is a Conflict.
"""
from pathlib import Path
import sqlite3

from . import beliefs, evidence, store
from .errors import Conflict, NotFound, ValidationError


def _read(legacy_path):
    path = Path(legacy_path)
    if not path.is_file():
        raise NotFound(f"No prototype database at {path}")
    try:
        source = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error as error:
        raise ValidationError(f"Cannot open {path} read-only: {error}") from error
    try:
        tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"epochs", "observations"} <= tables:
            raise ValidationError(f"{path} is not a prototype database (needs tables epochs and observations)")
        epochs = [r[0] for r in source.execute("SELECT name FROM epochs ORDER BY name")]
        observations = source.execute(
            "SELECT id,epoch,channel,action,bit,source_root FROM observations ORDER BY id").fetchall()
    except sqlite3.Error as error:
        raise ValidationError(f"Cannot read {path}: {error}") from error
    finally:
        source.close()
    return epochs, observations


def import_legacy(db, legacy_path, now):
    """Copy a prototype database's epochs and observations. Returns counts of newly created rows.

    Extra keys: already_present (identical rows skipped) and forgotten_skipped (tombstoned ids).
    """
    epochs, observations = _read(legacy_path)
    records = []
    for event_id, epoch, channel, action, bit, root in observations:
        try:
            records.append(evidence.normalize({"id": event_id, "epoch": epoch, "channel": channel,
                                               "action": action, "bit": bit, "root": root}))
        except ValidationError as error:
            raise ValidationError(f"prototype observation {event_id!r}: {error}") from error
    for name in epochs:
        if not isinstance(name, str) or not name.strip() or len(name) > 64:
            raise ValidationError(f"prototype epoch name {name!r} must be a non-blank string of at most 64 characters")
    created_epochs, created, present, forgotten, touched = 0, {}, 0, 0, set()
    with store.write(db):
        store.require_write_allowed(db)
        for name in epochs:
            created_epochs += db.execute("INSERT OR IGNORE INTO epochs(name,created_at,note) VALUES(?,?,?)",
                                         (name, now, "imported from a prototype database")).rowcount
        for record in records:
            if db.execute("SELECT 1 FROM tombstones WHERE id=?", (record["id"],)).fetchone():
                forgotten += 1
                continue
            row = db.execute(f"SELECT {','.join(evidence.PAYLOAD)} FROM evidence WHERE id=?",
                             (record["id"],)).fetchone()
            if row is not None:
                if tuple(row) != tuple(record[key] for key in evidence.PAYLOAD):
                    raise Conflict(f"Evidence id {record['id']!r} is already bound to a different observation")
                present += 1
                continue
            beliefs.require_epoch(db, record["epoch"])
            db.execute(f"INSERT INTO evidence(id,{','.join(evidence.PAYLOAD)},recorded_at,retention,expires_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'promoted',NULL)",
                       (record["id"], *(record[key] for key in evidence.PAYLOAD), now))
            created[record["channel"]] = created.get(record["channel"], 0) + 1
            touched.add(record["epoch"])
        total = sum(created.values())
        if total:
            store.bump(db, "evidence_written", total)
            for channel, count in created.items():
                store.bump(db, f"encoder_exposure:{channel}", count)
        if total or created_epochs:
            store.bump_state_version(db)
        for epoch in sorted(touched):
            beliefs.record_revision(db, epoch, now, "legacy_import")
    return {"epochs": created_epochs, "evidence": total, "already_present": present, "forgotten_skipped": forgotten}
