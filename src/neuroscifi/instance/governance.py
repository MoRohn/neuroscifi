"""Forgetting, expiry and promotion with a dependency cascade.

Every destructive operation runs in one `write()` transaction: the evidence rows are deleted, only a
payload-free tombstone remains, checkpoints that may contain the rows are marked stale, a belief revision
records that conclusions changed, open questions of the affected epochs are superseded, and the effects are
written to `deletion_log`. Beliefs are computed on read, so no belief table needs rewriting.
"""
import hashlib
import json

from . import store
from .errors import Gone, NotFound, ValidationError

REASONS = ("forgotten", "expired", "epoch_forgotten", "partner_forgotten")


def _remove(db, where, params, now, *, reason, cause, kind, target):
    """Caller owns the transaction. Returns the effects dict, or None when no row matched."""
    rows = db.execute(f"SELECT seq,id,epoch FROM evidence WHERE {where} ORDER BY seq", params).fetchall()
    if not rows:
        return None
    db.executemany("DELETE FROM evidence WHERE seq=?", [(seq,) for seq, _, _ in rows])
    db.executemany("INSERT OR IGNORE INTO tombstones(id,seq,epoch,deleted_at,reason) VALUES(?,?,?,?,?)",
                   [(i, seq, epoch, now, reason) for seq, i, epoch in rows])
    first = min(seq for seq, _, _ in rows)
    stale = [r[0] for r in db.execute(
        "SELECT id FROM checkpoints WHERE status='valid' AND max_evidence_seq>=? ORDER BY id", (first,))]
    db.executemany("UPDATE checkpoints SET status='stale' WHERE id=?", [(i,) for i in stale])
    epochs = sorted({epoch for _, _, epoch in rows})
    version = store.bump_state_version(db)
    superseded = 0
    from .beliefs import compute  # lazy: governance must not depend on inference at import time
    for epoch in epochs:
        count = sum(1 for seq, _, e in rows if e == epoch)
        # Always record a revision: the post-deletion belief state is part of the semantic history (MEM-03/08).
        summary = compute(db, epoch) | {"removed": count}
        db.execute("INSERT INTO belief_revisions(epoch,state_version,created_at,cause,summary) VALUES(?,?,?,?,?)",
                   (epoch, version, now, cause, json.dumps(summary, sort_keys=True, separators=(",", ":"),
                                                           allow_nan=False)))
        superseded += db.execute(
            "UPDATE pending SET resolved_at=?, resolution='superseded by deletion' "
            "WHERE epoch=? AND resolved_at IS NULL", (now, epoch)).rowcount
    effects = {"deleted": len(rows), "epochs": epochs, "checkpoints_stale": stale,
               "pending_superseded": superseded, "state_version": version}
    db.execute("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects) VALUES(?,?,?,?)",
               (kind, target, now, json.dumps(effects, sort_keys=True)))
    return effects


def _result(effects):
    return {"status": "forgotten" if effects else "absent", "effects": effects or {}}


def forget_evidence(db, event_id, now, *, reason="forgotten"):
    if reason not in REASONS:
        raise ValidationError(f"reason must be one of {', '.join(REASONS)}")
    if not isinstance(event_id, str) or not event_id:
        raise ValidationError("event id must be a non-empty string")
    with store.write(db):
        effects = _remove(db, "id=?", (event_id,), now, reason=reason,
                          cause=f"forget:{event_id}", kind="evidence", target=event_id)
    return _result(effects)


def forget_epoch(db, epoch, now):
    if db.execute("SELECT 1 FROM epochs WHERE name=?", (epoch,)).fetchone() is None:
        raise NotFound(f"Unknown epoch '{epoch}'")
    with store.write(db):
        effects = _remove(db, "epoch=?", (epoch,), now, reason="epoch_forgotten",
                          cause=f"forget:epoch:{epoch}", kind="epoch", target=epoch)
    return _result(effects)


def forget_partner(db, root, now):
    if not isinstance(root, str) or not root:
        raise ValidationError("partner root must be a non-empty string")
    # The root is the partner's payload, so the log keeps only a digest of it.
    digest = "partner:" + hashlib.sha256(root.encode()).hexdigest()[:16]
    with store.write(db):
        effects = _remove(db, "channel='word' AND root=?", (root,), now, reason="partner_forgotten",
                          cause=f"forget:{digest}", kind="partner", target=digest)
    return _result(effects)


def forget_dataset(db, dataset_id, now):
    """Drop a dataset blob, invalidate graphs trained on it and mark every existing checkpoint stale."""
    from .graphs import invalidate_dependents  # W2-B; imported lazily to keep modules decoupled
    row = db.execute("SELECT deleted_at FROM datasets WHERE id=?", (dataset_id,)).fetchone()
    if row is None:
        raise NotFound(f"Unknown dataset {dataset_id}")
    if row[0] is not None:
        return {"status": "absent", "effects": {}}
    with store.write(db):
        db.execute("UPDATE datasets SET blob=NULL, deleted_at=? WHERE id=?", (now, dataset_id))
        cascade = invalidate_dependents(db, dataset_id, now)
        stale = [r[0] for r in db.execute("SELECT id FROM checkpoints WHERE status='valid' ORDER BY id")]
        db.executemany("UPDATE checkpoints SET status='stale' WHERE id=?", [(i,) for i in stale])
        version = store.bump_state_version(db)
        effects = {"dataset": dataset_id, "invalidated": cascade.get("invalidated", []),
                   "reactivated": cascade.get("reactivated"), "checkpoints_stale": stale,
                   "state_version": version}
        db.execute("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects,failure) "
                   "VALUES('dataset',?,?,?,?)",
                   (str(dataset_id), now, json.dumps(effects, sort_keys=True), cascade.get("failure")))
    return {"status": "forgotten", "effects": effects, "failure": cascade.get("failure")}


def promote(db, event_id, now):
    """Explicitly keep one episode beyond the short default lifetime."""
    with store.write(db):
        row = db.execute("SELECT retention,expires_at FROM evidence WHERE id=?", (event_id,)).fetchone()
        if row is None:
            if db.execute("SELECT 1 FROM tombstones WHERE id=?", (event_id,)).fetchone():
                raise Gone(f"Event '{event_id}' was forgotten or expired and cannot be promoted")
            raise NotFound(f"Unknown event '{event_id}'")
        retention, expires_at = row
        if retention == "promoted":
            return {"status": "unchanged", "id": event_id, "retention": "promoted"}
        if expires_at is not None and expires_at <= now:
            raise Gone(f"Event '{event_id}' has expired and awaits sweep; it cannot be promoted")
        db.execute("UPDATE evidence SET retention='promoted', expires_at=NULL WHERE id=?", (event_id,))
        version = store.bump_state_version(db)
    return {"status": "promoted", "id": event_id, "retention": "promoted", "state_version": version}


def sweep(db, now):
    """Expire unpromoted evidence whose lifetime ended."""
    with store.write(db):
        effects = _remove(db, "expires_at IS NOT NULL AND expires_at<=? AND retention!='promoted'", (now,), now,
                          reason="expired", cause="expired", kind="sweep", target=f"{now:.0f}")
    return {"expired": effects["deleted"] if effects else 0,
            "epochs": effects["epochs"] if effects else [],
            "checkpoints_stale": effects["checkpoints_stale"] if effects else []}
