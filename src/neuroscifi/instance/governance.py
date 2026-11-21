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
    # Web Cortex text (migration 6): an owner-authorised task may name a partner (ADR-019, ADR-029).
    ("web_tasks", ("objective", "result", "checkpoint", "error"), ("result", "checkpoint")),
    ("web_events", ("detail",), ("detail",)),
    ("web_claims", ("text", "title"), ()),
    ("web_knowledge", ("statement", "key", "contradicting", "note"), ("contradicting",)),
)


def scrub_roots(db, roots):
    """Replace forgotten roots in belief_revisions, pending, decisions and the Web Cortex text columns (task
    objectives, reports and checkpoints, events, claims, knowledge); delete their changepoints and links.

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
            for row in db.execute(f"SELECT rowid,{','.join(columns)} FROM {table} WHERE {match}", params).fetchall():
                new = [_scrub_text(text, chunk, sub, as_json=column in json_columns)
                       for column, text in zip(columns, row[1:])]
                if new != list(row[1:]):
                    db.execute(f"UPDATE {table} SET {','.join(c + '=?' for c in columns)} WHERE rowid=?",
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


# ---- Web Cortex (migrations 6-7, ADR-029) ---------------------------------------------------------------------
# A forgotten research task leaves no copy of its objective, queries, events, sources or claims: the task row is
# deleted and the deletion_log names only the task id. Knowledge that rested only on removed claims is deleted
# with its questions, predictions and feedback; knowledge that keeps other support loses the removed references
# and becomes 'stale' (it needs re-verification). When a version in the middle of a supersession chain is deleted,
# its predecessor points at the nearest surviving successor; only when none survives does the predecessor become
# the latest version again, as 'stale'. Questions about removed knowledge are closed and their text rewritten.
#
# Every web forget also records payload-free tombstones in web_forgotten (kind, web_digest): the task id, each
# removed knowledge version (id and key), each removed source URL (as requested, original, final and canonical)
# or the domain. checkpoints.restore replays them on the restored copy (replay_web_forgets) so nothing forgotten
# comes back. deletion_log never holds a forgotten URL or domain (only its digest label); the public-cache keys
# the caller must drop are returned in effects["cache"] and are never persisted.

WEB_FORGOTTEN = "forgotten: the web memory it referred to was removed"
WEB_FORGOTTEN_KINDS = ("task", "knowledge", "source_url", "source_domain")
_WEB_QUESTION = "#forgotten"
_WEB_SCRUBBED = "#forgotten"


def web_digest(instance_id, kind, value):
    """Instance-salted digest of a forgotten web subject (kind in WEB_FORGOTTEN_KINDS), like root_digest."""
    return root_digest(instance_id, f"web:{kind}:{value}")


def _knowledge_subject(knowledge_id, key):
    return f"{knowledge_id}:{key}"


def _web_tombstones(db, kind, values, now):
    """Record payload-free tombstones for forgotten web subjects. Caller owns the txn. Returns the digests."""
    instance_id = store.birth(db)["instance_id"]
    digests = sorted({web_digest(instance_id, kind, v) for v in values if v not in (None, "")})
    db.executemany("INSERT OR IGNORE INTO web_forgotten(kind,digest,forgotten_at) VALUES(?,?,?)",
                   [(kind, d, now) for d in digests])
    return digests


def _ids(db, sql, params=()):
    return [r[0] for r in db.execute(sql, params)]


def _in(values):
    return ",".join("?" * len(values))


def _chunks(values, size=300):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _forget_pending(db, scopes, prefixes, now):
    """Close and rewrite web questions whose scope is in `scopes` or starts with a prefix. Caller owns the txn."""
    rows = []
    for scope in scopes:
        rows += db.execute("SELECT id,resolved_at FROM pending WHERE scope=?", (scope,)).fetchall()
    for prefix in prefixes:
        rows += db.execute("SELECT id,resolved_at FROM pending WHERE substr(scope,1,?)=?",
                           (len(prefix), prefix)).fetchall()
    for pid, resolved in rows:
        if resolved is None:
            db.execute("UPDATE pending SET question=?, resolved_at=?, resolution=? WHERE id=?",
                       (_WEB_QUESTION, now, WEB_FORGOTTEN, pid))
        else:
            db.execute("UPDATE pending SET question=?, resolution=? WHERE id=?", (_WEB_QUESTION, WEB_FORGOTTEN, pid))
    return sorted({pid for pid, _ in rows})


def _drop_knowledge(db, ids, now):
    """Delete knowledge rows and everything derived from them, recording a tombstone per version. Caller owns the
    txn. Returns effects."""
    ids = sorted(set(ids))
    if not ids:
        return {"knowledge_deleted": [], "versions_restored": [], "versions_relinked": [], "pending_closed": []}
    doomed = set(ids)
    rows = []
    for part in _chunks(ids):
        rows += db.execute(f"SELECT id,key,superseded_by FROM web_knowledge WHERE id IN ({_in(part)})", part).fetchall()
    successor = {kid: sup for kid, _, sup in rows}
    restored, relinked = [], []
    for kid in ids:
        nearest, seen = successor.get(kid), set()
        while nearest is not None and nearest in doomed and nearest not in seen:
            seen.add(nearest)
            nearest = successor.get(nearest)
        if nearest is not None and (nearest in doomed or db.execute(
                "SELECT 1 FROM web_knowledge WHERE id=?", (nearest,)).fetchone() is None):
            nearest = None
        for (older,) in db.execute("SELECT id FROM web_knowledge WHERE superseded_by=?", (kid,)).fetchall():
            if older in doomed:
                continue
            if nearest is not None:   # the chain closes over the gap: the older version stays superseded
                db.execute("UPDATE web_knowledge SET superseded_by=?, updated_at=? WHERE id=?", (nearest, now, older))
                relinked.append(older)
                continue
            db.execute("UPDATE web_knowledge SET superseded_by=NULL, valid_until=NULL, status=CASE WHEN "
                       "status='superseded' THEN 'stale' ELSE status END, note=?, updated_at=? WHERE id=?",
                       ("its successor version was forgotten; needs re-verification", now, older))
            restored.append(older)
    _web_tombstones(db, "knowledge", [_knowledge_subject(kid, key) for kid, key, _ in rows], now)
    for part in _chunks(ids):
        marks = _in(part)
        db.execute(f"DELETE FROM web_knowledge WHERE id IN ({marks})", part)
        db.execute(f"DELETE FROM web_predictions WHERE knowledge_id IN ({marks})", part)
        db.execute(f"DELETE FROM web_feedback WHERE knowledge_id IN ({marks})", part)
        db.execute(f"DELETE FROM web_mentions WHERE knowledge_id IN ({marks})", part)
        db.execute(f"DELETE FROM web_events WHERE json_extract(detail,'$.knowledge_id') IN ({marks})", part)
    closed = _forget_pending(db, [f"web:knowledge:{k}" for k in ids], [], now)
    return {"knowledge_deleted": ids, "versions_restored": sorted(restored), "versions_relinked": sorted(relinked),
            "pending_closed": closed}


def _web_cascade(db, removed_claims, now):
    """Re-check knowledge after claims were removed. Caller owns the txn. Returns effects."""
    removed = set(removed_claims)
    surviving = lambda cid: db.execute("SELECT 1 FROM web_claims WHERE id=? LIMIT 1",  # noqa: E731
                                       (cid,)).fetchone() is not None
    gone = {cid for cid in removed if not surviving(cid)}
    doomed, weakened = [], []
    if gone:
        for kid, supporting, contradicting, status in db.execute(
                "SELECT id,supporting,contradicting,status FROM web_knowledge").fetchall():
            support = json.loads(supporting or "[]")
            against = json.loads(contradicting or "[]")
            kept = [c for c in support if c not in gone]
            kept_against = [c for c in against if (c.get("claim") if isinstance(c, dict) else c) not in gone]
            if support and not kept:
                doomed.append(kid)
            elif kept != support or kept_against != against:
                new_status = "stale" if status in ("provisional", "accepted") and kept != support else status
                db.execute("UPDATE web_knowledge SET supporting=?, contradicting=?, status=?, note=?, updated_at=? "
                           "WHERE id=?", (_dump(kept), _dump(kept_against), new_status,
                                          f"lost {len(support) - len(kept)} supporting claim(s) to a forget; "
                                          "needs re-verification" if kept != support else None, now, kid))
                weakened.append(kid)
        for part in _chunks(sorted(gone)):
            marks = _in(part)
            db.execute(f"DELETE FROM web_claim_links WHERE src IN ({marks}) OR dst IN ({marks})", (*part, *part))
            db.execute(f"DELETE FROM web_mentions WHERE claim_id IN ({marks})", part)
    effects = _drop_knowledge(db, doomed, now)
    effects["knowledge_stale"] = sorted(set(weakened) - set(doomed))
    effects["claims_removed"] = len(gone)
    db.execute("DELETE FROM web_entities WHERE id NOT IN (SELECT entity_id FROM web_mentions)")
    return effects


def _web_finish(db, kind, target, effects, now):
    """Stale checkpoints, state version and deletion_log. Caller owns the txn. `target` is persisted: it must
    not carry forgotten payload (a URL or domain goes in as its digest label)."""
    effects["checkpoints_stale"] = _stale_all(db)
    effects["state_version"] = store.bump_state_version(db)
    db.execute("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects) VALUES(?,?,?,?)",
               (kind, target, now, json.dumps(effects, sort_keys=True)))
    return effects


def _unreferenced(db, urls, shas):
    """Of a removed task's public-cache keys, those no remaining web_sources row still references."""
    still = set()
    for part in _chunks(sorted(urls)):
        marks = _in(part)
        for row in db.execute(f"SELECT url,final_url,canonical_url FROM web_sources WHERE canonical_url IN ({marks}) "
                              f"OR url IN ({marks}) OR final_url IN ({marks})", (*part, *part, *part)):
            still.update(u for u in row if u)
    kept_shas = set()
    for part in _chunks(sorted(shas)):
        kept_shas.update(_ids(db, f"SELECT content_sha256 FROM web_sources WHERE content_sha256 IN ({_in(part)})",
                              part))
    return sorted(set(urls) - still), sorted(set(shas) - kept_shas)


def _forget_research_core(db, task_id, now):
    """Remove one task and what rested only on it. Caller owns the txn. Returns (effects, cache) or None."""
    if db.execute("SELECT 1 FROM web_tasks WHERE id=?", (task_id,)).fetchone() is None:
        return None
    claim_ids = _ids(db, "SELECT DISTINCT id FROM web_claims WHERE task_id=?", (task_id,))
    sources = db.execute("SELECT url,final_url,canonical_url,content_sha256 FROM web_sources WHERE task_id=?",
                         (task_id,)).fetchall()
    deleted = {table: db.execute(f"DELETE FROM {table} WHERE task_id=?", (task_id,)).rowcount
               for table in ("web_events", "web_sources", "web_claims", "web_claim_links", "web_mentions",
                             "web_feedback")}
    db.execute("UPDATE web_predictions SET task_id=NULL WHERE task_id=?", (task_id,))
    db.execute("UPDATE web_tasks SET parent_id=NULL WHERE parent_id=?", (task_id,))
    db.execute("UPDATE web_monitors SET last_task_id=NULL WHERE last_task_id=?", (task_id,))
    db.execute("UPDATE web_knowledge SET task_id=NULL WHERE task_id=?", (task_id,))
    db.execute("DELETE FROM web_tasks WHERE id=?", (task_id,))
    _web_tombstones(db, "task", [task_id], now)
    effects = _web_cascade(db, claim_ids, now)
    effects["pending_closed"] = sorted(set(effects["pending_closed"]) | set(_forget_task_questions(db, task_id, now)))
    effects["deleted"] = deleted
    urls, shas = _unreferenced(db, {u for r in sources for u in r[:3] if u}, {r[3] for r in sources if r[3]})
    return effects, {"urls": urls, "sha256": shas}


def _forget_task_questions(db, task_id, now):
    """The task's own questions: its proposal, contested subjects, review aggregate and monitor findings."""
    scopes = [f"web:research:{task_id}", f"web:review:{task_id}"]
    scopes += [s for (s,) in db.execute("SELECT DISTINCT scope FROM pending WHERE scope LIKE 'web:finding:%' "
                                        "AND substr(scope, -?) = ?", (len(task_id) + 1, ":" + task_id))]
    return _forget_pending(db, scopes, [f"web:contested:{task_id}:"], now)


def forget_research(db, task_id, now):
    """Forget a research task: its row, events, sources, claims, links, mentions, feedback, questions and the
    knowledge that rested only on it. The objective is kept nowhere (deletion_log names the task id only).
    effects["cache"] = {"urls", "sha256"}: the task's public-cache keys no other task references, for the caller
    to drop from the public cache (returned, never persisted)."""
    if not isinstance(task_id, str) or not task_id:
        raise ValidationError("task id must be a non-empty string")
    with store.write(db):
        out = _forget_research_core(db, task_id, now)
        if out is None:
            return {"status": "absent", "effects": {}}
        effects, cache = out
        _web_finish(db, "research", task_id, effects, now)
    effects["cache"] = cache
    return _result(db, effects)


def _knowledge_chain(db, knowledge_id):
    chain, frontier = set(), [knowledge_id]
    while frontier:
        kid = frontier.pop()
        if kid in chain:
            continue
        row = db.execute("SELECT superseded_by FROM web_knowledge WHERE id=?", (kid,)).fetchone()
        if row is None:
            continue
        chain.add(kid)
        if row[0] is not None:
            frontier.append(row[0])
        frontier += _ids(db, "SELECT id FROM web_knowledge WHERE superseded_by=?", (kid,))
    return sorted(chain)


def _forget_knowledge_core(db, knowledge_id, now):
    """Caller owns the txn. Returns effects or None when absent."""
    chain = _knowledge_chain(db, knowledge_id)
    if not chain:
        return None
    effects = _drop_knowledge(db, chain, now)
    db.execute("DELETE FROM web_entities WHERE id NOT IN (SELECT entity_id FROM web_mentions)")
    return effects


def forget_knowledge(db, knowledge_id, now):
    """Forget a knowledge item and every version in its supersession chain (claims stay with their tasks)."""
    if isinstance(knowledge_id, bool) or not isinstance(knowledge_id, int):
        raise ValidationError("knowledge id must be an integer")
    with store.write(db):
        effects = _forget_knowledge_core(db, knowledge_id, now)
        if effects is None:
            return {"status": "absent", "effects": {}}
        _web_finish(db, "knowledge", str(knowledge_id), effects, now)
    return _result(db, effects)


def _source_target(url_or_domain):
    """(canonical url or None, domain or None, the target as given)."""
    if not isinstance(url_or_domain, str) or not url_or_domain.strip():
        raise ValidationError("source must be a URL or a domain")
    target = url_or_domain.strip()
    if "://" not in target:
        return None, target.lower().strip("."), target
    try:
        from ..webcortex.extract import canonical_url  # lazy: the canonical form the orchestrator stores
        canonical = canonical_url(target)
    except ImportError:
        canonical = target
    except Exception:   # an unparsable URL is matched as given
        canonical = target
    return canonical, None, target


def _mentions(text, needles):
    return isinstance(text, str) and any(n in text for n in needles)


def _prune_json(value, needles, *, root=False):
    """Drop every record (a dict with a string value naming a needle, or such a string in a list) and every map
    entry keyed by a needle. At the root, a string naming a needle is replaced instead."""
    if isinstance(value, dict):
        if not root and any(_mentions(v, needles) for v in value.values()):
            return _GONE
        out = {}
        for key, item in value.items():
            if _mentions(key, needles):
                continue
            if root and _mentions(item, needles):
                out[key] = _WEB_SCRUBBED
                continue
            pruned = _prune_json(item, needles)
            if pruned is not _GONE:
                out[key] = pruned
        return out
    if isinstance(value, list):
        return [p for p in (_prune_json(v, needles) for v in value) if p is not _GONE]
    if _mentions(value, needles):
        return _WEB_SCRUBBED if root else _GONE
    return value


_GONE = object()


def _scrub_web_mentions(db, needles):
    """Remove a forgotten source's URL/domain from episodic events (deleted), task reports and checkpoints and
    monitor baselines (records naming it dropped). Caller owns the txn. Returns rows changed."""
    forms = sorted({form for n in needles if n for form in (n, json.dumps(n)[1:-1])})
    if not forms:
        return 0
    changed = 0
    match = lambda column: " OR ".join(f"instr({column},?)>0" for _ in forms)  # noqa: E731
    changed += db.execute(f"DELETE FROM web_events WHERE {match('detail')}", forms).rowcount
    for table, key, columns in (("web_tasks", "rowid", ("result", "checkpoint")),
                                ("web_monitors", "id", ("baseline",))):
        where = " OR ".join(f"({match(c)})" for c in columns)
        for row in db.execute(f"SELECT {key},{','.join(columns)} FROM {table} WHERE {where}",
                              forms * len(columns)).fetchall():
            new = []
            for text in row[1:]:
                try:
                    parsed = json.loads(text) if text is not None else None
                except ValueError:
                    parsed = None
                new.append(text if parsed is None else _dump(_prune_json(parsed, forms, root=True)))
            db.execute(f"UPDATE {table} SET {','.join(c + '=?' for c in columns)} WHERE {key}=?", (*new, row[0]))
            changed += 1
    return changed


def _forget_source_core(db, target, now):
    """Forget one source URL or every source of a domain. Caller owns the txn. Returns (effects, cache, label) or
    None when nothing matched."""
    canonical, domain, given = _source_target(target)
    if domain is None:
        rows = db.execute("SELECT id,url,final_url,canonical_url,content_sha256 FROM web_sources WHERE "
                          "canonical_url=? OR url=? OR final_url=? OR url=? OR final_url=?",
                          (canonical, given, given, canonical, canonical)).fetchall()
        urls = sorted({canonical, given} | {u for r in rows for u in r[1:4] if u})
        source_ids = [r[0] for r in rows]
        clauses, params = [], []
        for part in _chunks(urls):
            clauses.append(f"canonical_url IN ({_in(part)}) OR source_url IN ({_in(part)})")
            params += [*part, *part]
        for part in _chunks(source_ids):
            clauses.append(f"source_id IN ({_in(part)})")
            params += part
        claim_where = " OR ".join(f"({c})" for c in clauses)
        claim_rows = db.execute(f"SELECT DISTINCT id, source_url, canonical_url FROM web_claims WHERE {claim_where}",
                                params).fetchall()
        urls = sorted(set(urls) | {u for r in claim_rows for u in r[1:] if u})
        needles = urls
    else:
        like = "%." + domain
        rows = db.execute("SELECT id,url,final_url,canonical_url,content_sha256 FROM web_sources WHERE "
                          "domain=? OR domain LIKE ?", (domain, like)).fetchall()
        source_ids = [r[0] for r in rows]
        clauses, params = ["domain=? OR domain LIKE ?"], [domain, like]
        for part in _chunks(source_ids):
            clauses.append(f"source_id IN ({_in(part)})")
            params += part
        claim_where = " OR ".join(f"({c})" for c in clauses)
        claim_rows = db.execute(f"SELECT DISTINCT id, source_url, canonical_url FROM web_claims WHERE {claim_where}",
                                params).fetchall()
        urls = sorted({u for r in rows for u in r[1:4] if u} | {u for r in claim_rows for u in r[1:] if u})
        needles = [domain]
    claim_ids = sorted({r[0] for r in claim_rows})
    if not rows and not claim_ids:
        return None
    db.execute(f"DELETE FROM web_claims WHERE {claim_where}", params)
    for part in _chunks(source_ids):
        db.execute(f"DELETE FROM web_sources WHERE id IN ({_in(part)})", part)
    effects = _web_cascade(db, claim_ids, now)
    dropped = 0
    for kid, sources in db.execute("SELECT id,sources FROM web_knowledge").fetchall():
        listed = json.loads(sources or "[]")
        kept = [s for s in listed if not (s.get("url") in urls or (domain and (
            s.get("domain") == domain or str(s.get("domain", "")).endswith("." + domain))))]
        if kept != listed:
            db.execute("UPDATE web_knowledge SET sources=? WHERE id=?", (_dump(kept), kid))
            dropped += 1
    scrubbed = _scrub_web_mentions(db, needles)
    if domain is None:
        digests = _web_tombstones(db, "source_url", urls, now)
        label = "source_url:#" + web_digest(store.birth(db)["instance_id"], "source_url", canonical)
    else:
        digests = _web_tombstones(db, "source_domain", [domain], now)
        label = "source_domain:#" + digests[0]
    effects.update({"sources_deleted": len(rows), "knowledge_sources_dropped": dropped,
                    "derived_rows_scrubbed": scrubbed})
    cache = {"urls": urls if domain is None else sorted({u for r in rows for u in r[1:4] if u}), "domain": domain,
             "sha256": sorted({r[4] for r in rows if r[4]})}
    return effects, cache, label


def forget_source(db, url_or_domain, now):
    """Forget one source URL (as requested, original, final or canonical) or every source of a registrable domain,
    with the claims taken from it and the knowledge that rested only on them; reports, checkpoints, events and
    monitor baselines lose the records naming it. deletion_log keeps only a digest label. effects["cache"] names
    the public-cache keys ({"urls": [...], "domain": d|None, "sha256": [...]}) the caller drops from the public
    cache; it is returned, never persisted."""
    _source_target(url_or_domain)
    with store.write(db):
        out = _forget_source_core(db, url_or_domain, now)
        if out is None:
            return {"status": "absent", "effects": {}}
        effects, cache, label = out
        _web_finish(db, "source", label, effects, now)
    effects["cache"] = cache
    return _result(db, effects)


def _suffixes(domain):
    parts = (domain or "").lower().strip(".").split(".")
    return [".".join(parts[i:]) for i in range(len(parts)) if parts[i]]


def replay_web_forgets(db, forgotten, now):
    """Re-apply web tombstones to a restored copy: every task, knowledge version and source (URL or domain) whose
    digest is in `forgotten` ({(kind, digest)}) is forgotten again with the same cascade as the live forget.
    Caller owns the transaction. Returns counts."""
    out = {"tasks": 0, "knowledge": 0, "sources": 0}
    if not forgotten:
        return out
    instance_id = store.birth(db)["instance_id"]

    def wanted(kind, value):
        return (kind, web_digest(instance_id, kind, value)) in forgotten

    for (task_id,) in db.execute("SELECT id FROM web_tasks ORDER BY rowid").fetchall():
        if wanted("task", task_id) and _forget_research_core(db, task_id, now) is not None:
            out["tasks"] += 1
    doomed = [kid for kid, key in db.execute("SELECT id, key FROM web_knowledge").fetchall()
              if wanted("knowledge", _knowledge_subject(kid, key))]
    if doomed:
        _drop_knowledge(db, doomed, now)
        db.execute("DELETE FROM web_entities WHERE id NOT IN (SELECT entity_id FROM web_mentions)")
        out["knowledge"] = len(doomed)
    urls = {u for row in db.execute("SELECT url, final_url, canonical_url FROM web_sources") for u in row if u}
    urls |= {u for row in db.execute("SELECT source_url, canonical_url FROM web_claims") for u in row if u}
    for url in sorted(urls):
        if wanted("source_url", url) and _forget_source_core(db, url, now) is not None:
            out["sources"] += 1
    domains = {d for (d,) in db.execute("SELECT domain FROM web_sources UNION SELECT domain FROM web_claims") if d}
    for domain in sorted({s for d in domains for s in _suffixes(d)}):
        if wanted("source_domain", domain) and _forget_source_core(db, domain, now) is not None:
            out["sources"] += 1
    return out
