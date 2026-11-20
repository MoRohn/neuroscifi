"""Forgetting, expiry and promotion with a dependency cascade.

Every destructive operation runs in one `write()` transaction: the evidence rows are deleted, only a
payload-free tombstone remains, checkpoints that may contain the rows are marked stale, a belief revision
records that conclusions changed, and the effects are written to `deletion_log`. Open questions are not
closed here: the executive closes questions whose premise disappeared on its next recorded decision.

Derived memory is scrubbed in the same transaction. A camera or word root with no evidence row left is
replaced in belief_revisions, pending and decisions by `camera:#<digest>` or `partner:#<digest>`
(digest = sha256(instance_id ":" root)[:16], recorded in forgotten_roots), its changepoints rows are
deleted (a change of a root with no evidence is meaningless), epoch_links naming it in a segment are deleted,
and revision causes naming a removed event id become `evidence:#forgotten`. Forgetting an epoch also deletes
that epoch's changepoints, every link from or to it, its graph-context association and its population streams
(a deleted stream marks every checkpoint stale; sweep also expires streams). An epoch that continues an epoch losing evidence pools
fewer events on its next read; it gets a belief revision too. After the commit the WAL is checkpointed and truncated so no
old frame keeps the payload; a reader that blocks this is reported as effects["wal_checkpoint"] = "busy".
"""
import hashlib
import json
import re

from . import store
from .errors import Gone, NotFound, ValidationError

REASONS = ("forgotten", "expired", "epoch_forgotten", "partner_forgotten")
FORGOTTEN_CAUSE = "evidence:#forgotten"
_KIND = {"camera": "camera", "word": "partner"}  # evidence channel -> label kind
_LABEL = r"(?:camera|partner):#[0-9a-f]{16}"
_MAP_KIND = {"sensors": "camera", "partners": "partner"}  # compute() maps keyed by root


def root_digest(instance_id, root):
    """Stable, instance-salted pseudonym of a root (a bare hash of a name would be guessable)."""
    return hashlib.sha256(f"{instance_id}:{root}".encode()).hexdigest()[:16]


def _dump(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _scrubber(roots):
    """roots: {root: (kind, digest)}. Returns text -> text replacing each root by its label.

    `camera:<root>` / `partner:<root>` keep their own prefix; a bare root takes the kind it was forgotten as.
    Matches need a non-word boundary on both sides so a short root does not rewrite unrelated words, and
    existing labels are left untouched.
    """
    alternatives = "|".join(re.escape(r) for r in sorted(roots, key=len, reverse=True))
    pattern = re.compile(rf"{_LABEL}|(?<![\w-])(?:(camera|partner):)?({alternatives})(?![\w-])")

    def label(match):
        if match.group(2) is None:
            return match.group(0)
        kind, digest = roots[match.group(2)]
        return f"{match.group(1) or kind}:#{digest}"

    return lambda text: pattern.sub(label, text)


def _scrub_value(value, roots, sub, kind=None):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if kind is not None and key in roots:
                new_key = f"{kind}:#{roots[key][1]}"
            else:
                new_key = sub(key)
            out[new_key] = _scrub_value(item, roots, sub, _MAP_KIND.get(key))
        return out
    if isinstance(value, list):
        return [_scrub_value(item, roots, sub) for item in value]
    if isinstance(value, str):
        prefix, _, rest = value.partition(":")
        if prefix in ("camera", "partner") and rest in roots:
            return f"{prefix}:#{roots[rest][1]}"
        if value in roots:
            return f"{roots[value][0]}:#{roots[value][1]}"
        return sub(value)
    return value


def _scrub_text(text, roots, sub, *, as_json=False):
    if text is None:
        return None
    if as_json:
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            return _dump(_scrub_value(parsed, roots, sub))
    return _scrub_value(text, roots, sub)


_SCRUB_TABLES = (  # table, scrubbed columns, columns stored as JSON
    ("belief_revisions", ("summary", "cause"), ("summary",)),
    ("pending", ("scope", "question", "resolution"), ()),
    ("decisions", ("scope", "reason"), ()),
)


def scrub_roots(db, roots):
    """Replace forgotten roots in belief_revisions, pending and decisions; delete their changepoints and links.

    Caller owns the transaction. roots: {root: (kind, digest)} with kind 'camera' or 'partner'. Returns the
    number of rows rewritten or deleted. Works on any migrated connection to an instance file (also a
    restore's staged copy). A root has no evidence left in any channel, so both its camera:<root> and
    partner:<root> changepoints go, and so does every epoch link with a segment of that root.
    """
    rewritten, names = 0, sorted(roots)
    links = db.execute("SELECT epoch, segments FROM epoch_links").fetchall() if db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='epoch_links'").fetchone() else []
    for epoch, segments in links:
        if any(f"{prefix}:{root}" in json.loads(segments) for root in roots for prefix in ("camera", "partner")):
            rewritten += db.execute("DELETE FROM epoch_links WHERE epoch=?", (epoch,)).rowcount
    for start in range(0, len(names), 32):  # bounded SQL expression size; labels survive a second pass
        chunk = {root: roots[root] for root in names[start:start + 32]}
        scopes = [f"{prefix}:{root}" for root in chunk for prefix in ("camera", "partner")]
        rewritten += db.execute(f"DELETE FROM changepoints WHERE scope IN ({','.join('?' * len(scopes))})",
                                scopes).rowcount
        sub = _scrubber(chunk)
        # Summaries are written with ensure_ascii, so a non-ASCII root is stored in its escaped form.
        needles = sorted({form for root in chunk for form in (root, json.dumps(root)[1:-1])})
        for table, columns, json_columns in _SCRUB_TABLES:
            match = " OR ".join(f"instr({c},?)>0" for c in columns for _ in needles)
            params = [n for _ in columns for n in needles]
            for row in db.execute(f"SELECT id,{','.join(columns)} FROM {table} WHERE {match}", params).fetchall():
                new = [_scrub_text(text, chunk, sub, as_json=column in json_columns)
                       for column, text in zip(columns, row[1:])]
                if new != list(row[1:]):
                    db.execute(f"UPDATE {table} SET {','.join(c + '=?' for c in columns)} WHERE id=?",
                               (*new, row[0]))
                    rewritten += 1
    return rewritten


def _forget_causes(db, event_ids):
    """Rewrite belief_revisions causes (and the summary's copy) that name a removed event id."""
    names = sorted({f"{prefix}:{i}" for i in event_ids for prefix in ("evidence", "forget")})
    rewritten = 0
    for start in range(0, len(names), 400):
        part = names[start:start + 400]
        rows = db.execute(f"SELECT id,cause,summary FROM belief_revisions WHERE cause IN ({','.join('?' * len(part))})",
                          part).fetchall()
        for rid, cause, summary in rows:
            try:
                parsed = json.loads(summary)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict) and parsed.get("cause") == cause:
                summary = _dump(parsed | {"cause": FORGOTTEN_CAUSE})
            db.execute("UPDATE belief_revisions SET cause=?, summary=? WHERE id=?", (FORGOTTEN_CAUSE, summary, rid))
            rewritten += 1
    return rewritten


def _forget_roots(db, rows, now):
    """Scrub each camera/word root of the removed rows that has no evidence row left. Caller owns the txn."""
    instance_id = store.birth(db)["instance_id"]
    roots = {}
    for _, _, _, channel, root in rows:
        if channel in _KIND and root not in roots and \
                db.execute("SELECT 1 FROM evidence WHERE root=? LIMIT 1", (root,)).fetchone() is None:
            roots[root] = (_KIND[channel], root_digest(instance_id, root))
    db.executemany("INSERT OR IGNORE INTO forgotten_roots(digest,kind,forgotten_at) VALUES(?,?,?)",
                   [(digest, kind, now) for kind, digest in roots.values()])
    return len(roots), scrub_roots(db, roots)


def _remove(db, where, params, now, *, reason, cause, kind, target, drop_epoch=None):
    """Caller owns the transaction. Returns the effects dict, or None when nothing matched.

    drop_epoch: also delete that epoch's belief_revisions, decisions, pending and changepoints rows
    (forget_epoch).
    """
    rows = db.execute(f"SELECT seq,id,epoch,channel,root FROM evidence WHERE {where} ORDER BY seq", params).fetchall()
    sources = sorted({r[2] for r in rows} | ({drop_epoch} if drop_epoch is not None else set()))
    linked = [e for (e,) in db.execute(f"SELECT epoch FROM epoch_links WHERE source_epoch IN "
                                       f"({','.join('?' * len(sources))}) ORDER BY epoch", sources)
              if e != drop_epoch and e not in sources]
    derived = {}
    if drop_epoch is not None:
        derived = {table: db.execute(f"DELETE FROM {table} WHERE epoch=?", (drop_epoch,)).rowcount
                   for table in ("belief_revisions", "decisions", "pending", "changepoints")}
        derived["epoch_links"] = db.execute("DELETE FROM epoch_links WHERE epoch=? OR source_epoch=?",
                                            (drop_epoch, drop_epoch)).rowcount
        for table in ("epoch_contexts", "epoch_streams"):
            derived[table] = db.execute(f"DELETE FROM {table} WHERE epoch=?", (drop_epoch,)).rowcount
    if not rows and not any(derived.values()):
        return None
    db.executemany("DELETE FROM evidence WHERE seq=?", [(r[0],) for r in rows])
    db.executemany("INSERT OR IGNORE INTO tombstones(id,seq,epoch,deleted_at,reason) VALUES(?,?,?,?,?)",
                   [(i, seq, epoch, now, reason) for seq, i, epoch, _, _ in rows])
    stale = []
    if rows:
        first = min(r[0] for r in rows)
        stale = [r[0] for r in db.execute(
            "SELECT id FROM checkpoints WHERE status='valid' AND max_evidence_seq>=? ORDER BY id", (first,))]
        db.executemany("UPDATE checkpoints SET status='stale' WHERE id=?", [(i,) for i in stale])
    if derived.get("epoch_streams"):
        stale = _stale_all(db, stale)  # a stream carries no evidence seq, so any checkpoint may hold it
    epochs = sorted({r[2] for r in rows})
    version = store.bump_state_version(db)
    from .beliefs import compute  # lazy: governance must not depend on inference at import time
    for epoch in epochs + linked:
        if epoch == drop_epoch:
            continue
        count = sum(1 for r in rows if r[2] == epoch)
        # Always record a revision: the post-deletion belief state is part of the semantic history (MEM-03/08).
        summary = compute(db, epoch) | {"removed": count, "cause": cause}
        db.execute("INSERT INTO belief_revisions(epoch,state_version,created_at,cause,summary) VALUES(?,?,?,?,?)",
                   (epoch, version, now, cause, _dump(summary)))
    causes = _forget_causes(db, [r[1] for r in rows])
    forgotten, scrubbed = _forget_roots(db, rows, now)
    effects = {"deleted": len(rows), "epochs": epochs, "checkpoints_stale": stale, "state_version": version,
               "roots_forgotten": forgotten, "derived_rows_scrubbed": scrubbed + causes}
    if linked:
        effects["linked_epochs_revised"] = linked
    if drop_epoch is not None:
        effects["derived_deleted"] = derived
    db.execute("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects) VALUES(?,?,?,?)",
               (kind, target, now, json.dumps(effects, sort_keys=True)))
    return effects


def _stale_all(db, stale=()):
    """Mark every valid checkpoint stale. Caller owns the transaction. Returns the ids now stale (sorted)."""
    more = [r[0] for r in db.execute("SELECT id FROM checkpoints WHERE status='valid' ORDER BY id")]
    db.executemany("UPDATE checkpoints SET status='stale' WHERE id=?", [(i,) for i in more])
    return sorted({*stale, *more})


def _result(db, effects):
    if effects:
        effects = effects | {"wal_checkpoint": store.truncate_wal(db)}
    return {"status": "forgotten" if effects else "absent", "effects": effects or {}}


def forget_evidence(db, event_id, now, *, reason="forgotten"):
    if reason not in REASONS:
        raise ValidationError(f"reason must be one of {', '.join(REASONS)}")
    if not isinstance(event_id, str) or not event_id:
        raise ValidationError("event id must be a non-empty string")
    # The event id is caller-chosen and may carry meaning, so the revision cause does not repeat it.
    with store.write(db):
        effects = _remove(db, "id=?", (event_id,), now, reason=reason,
                          cause=FORGOTTEN_CAUSE, kind="evidence", target=event_id)
    return _result(db, effects)


def forget_epoch(db, epoch, now):
    """Forget every evidence row of the epoch, its belief revisions, decisions, questions and changepoints, every
    epoch link from or to it, its graph-context association and its population streams."""
    with store.write(db):
        if db.execute("SELECT 1 FROM epochs WHERE name=?", (epoch,)).fetchone() is None:
            raise NotFound(f"Unknown epoch '{epoch}'")
        effects = _remove(db, "epoch=?", (epoch,), now, reason="epoch_forgotten",
                          cause=f"forget:epoch:{epoch}", kind="epoch", target=epoch, drop_epoch=epoch)
    return _result(db, effects)


def forget_partner(db, root, now):
    if not isinstance(root, str) or not root:
        raise ValidationError("partner root must be a non-empty string")
    with store.write(db):
        # The root is the partner's payload, so the log keeps only its instance-salted label.
        label = "partner:#" + root_digest(store.birth(db)["instance_id"], root)
        effects = _remove(db, "channel='word' AND root=?", (root,), now, reason="partner_forgotten",
                          cause=f"forget:{label}", kind="partner", target=label)
    return _result(db, effects)


def forget_dataset(db, dataset_id, now):
    """Drop a dataset blob, invalidate graphs trained on it and mark every existing checkpoint stale."""
    from .graphs import invalidate_dependents  # W2-B; imported lazily to keep modules decoupled
    with store.write(db):
        row = db.execute("SELECT deleted_at FROM datasets WHERE id=?", (dataset_id,)).fetchone()
        if row is None:
            raise NotFound(f"Unknown dataset {dataset_id}")
        if row[0] is not None:
            return {"status": "absent", "effects": {}}
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
    effects["wal_checkpoint"] = store.truncate_wal(db)
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
    """Expire unpromoted evidence and epoch streams whose lifetime ended."""
    with store.write(db):
        effects = _remove(db, "expires_at IS NOT NULL AND expires_at<=? AND retention!='promoted'", (now,), now,
                          reason="expired", cause="expired", kind="sweep", target=f"{now:.0f}")
        streams = db.execute("DELETE FROM epoch_streams WHERE expires_at IS NOT NULL AND expires_at<=?",
                             (now,)).rowcount
        stale = effects["checkpoints_stale"] if effects else []
        if streams:
            stale = _stale_all(db, stale)
            version = store.bump_state_version(db)
            db.execute("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects) VALUES(?,?,?,?)",
                       ("sweep_streams", f"{now:.0f}", now,
                        json.dumps({"streams_expired": streams, "checkpoints_stale": stale, "state_version": version},
                                   sort_keys=True)))
    out = {"expired": effects["deleted"] if effects else 0,
           "epochs": effects["epochs"] if effects else [],
           "checkpoints_stale": stale, "streams_expired": streams}
    if effects or streams:
        out["wal_checkpoint"] = store.truncate_wal(db)
    return out
