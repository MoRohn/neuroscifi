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
Every forget also removes, in its transaction, the imagined records (ADR-049) derived from what it removed
(imagined.purge_orphans; effects["imagined_removed"], absent while zero).
"""
import hashlib
import json
import re
import secrets

from . import imagined, store
from .errors import Gone, NotFound, ValidationError
from .schema import has_consolidated

REASONS = ("forgotten", "expired", "epoch_forgotten", "partner_forgotten")
MAX_ROWID = 2 ** 63 - 1       # SQLite's largest integer: a larger id cannot be bound, let alone exist
# Forgets whose deletion_log row names a scope by its digest label and which delete that scope's changepoints in
# every epoch; checkpoints._replay deletes them in an older copy too (replay_scope_forgets).
SCOPE_KINDS = ("camera", "partner")
FORGOTTEN_CAUSE = "evidence:#forgotten"
_KIND = {"camera": "camera", "word": "partner"}  # evidence channel -> label kind
_LABEL = r"(?:camera|partner):#[0-9a-f]{16}"
_MAP_KIND = {"sensors": "camera", "partners": "partner"}  # compute() maps keyed by root


def root_digest(instance_id, root):
    """Stable, instance-salted pseudonym of a root (a bare hash of a name would be guessable)."""
    return hashlib.sha256(f"{instance_id}:{root}".encode()).hexdigest()[:16]


def _dump(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _scrubber(roots, *, scoped=False):
    """roots: {root: (kind, digest)}. Returns text -> text replacing each root by its label.

    `camera:<root>` / `partner:<root>` keep their own prefix; a bare root takes the kind it was forgotten as.
    Matches need a non-word boundary on both sides so a short root does not rewrite unrelated words, and
    existing labels are left untouched.

    scoped: only the root's forgotten kind goes, because the other channel still holds the name: `<kind>:<root>`
    (and, for a partner, the executive's "partner <root>"); a bare root is left, since it may name the other one.
    """
    alternatives = "|".join(re.escape(r) for r in sorted(roots, key=len, reverse=True))
    if scoped:
        pattern = re.compile(rf"{_LABEL}|(?<![\w-])(camera:|partner:|partner )({alternatives})(?![\w-])")

        def label(match):
            if match.group(2) is None:
                return match.group(0)
            kind, digest = roots[match.group(2)]
            prefix = match.group(1)
            if prefix[:-1] != kind:
                return match.group(0)
            return f"{kind}:#{digest}" if prefix.endswith(":") else f"partner {kind}:#{digest}"

        return lambda text: pattern.sub(label, text)
    pattern = re.compile(rf"{_LABEL}|(?<![\w-])(?:(camera|partner):)?({alternatives})(?![\w-])")

    def label(match):
        if match.group(2) is None:
            return match.group(0)
        kind, digest = roots[match.group(2)]
        return f"{match.group(1) or kind}:#{digest}"

    return lambda text: pattern.sub(label, text)


def _scrub_value(value, roots, sub, kind=None, *, scoped=False):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if kind is not None and key in roots and (not scoped or roots[key][0] == kind):
                new_key = f"{kind}:#{roots[key][1]}"
            else:
                new_key = sub(key)
            out[new_key] = _scrub_value(item, roots, sub, _MAP_KIND.get(key), scoped=scoped)
        return out
    if isinstance(value, list):
        return [_scrub_value(item, roots, sub, scoped=scoped) for item in value]
    if isinstance(value, str):
        prefix, _, rest = value.partition(":")
        if prefix in ("camera", "partner") and rest in roots and (not scoped or roots[rest][0] == prefix):
            return f"{prefix}:#{roots[rest][1]}"
        if value in roots and not scoped:
            return f"{roots[value][0]}:#{roots[value][1]}"
        return sub(value)
    return value


def _scrub_text(text, roots, sub, *, as_json=False, scoped=False):
    if text is None:
        return None
    if as_json:
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            return _dump(_scrub_value(parsed, roots, sub, scoped=scoped))
    return _scrub_value(text, roots, sub, scoped=scoped)


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


def _drop_in_process_copies():
    """Drop the statement text this process keeps outside the file: knowledge.query's lexical index and the
    semantic vectors (review F6). Called by every forget that removes or rewrites knowledge text, so nothing
    forgotten stays in memory; another process drops its copies at its own next query. A cache cannot fail a
    forget."""
    try:
        from ..webcortex import knowledge, semantic
        knowledge.clear_index()
        semantic.clear()
    except Exception:
        pass


def scrub_roots(db, roots, *, scoped=False):
    """Replace forgotten roots in belief_revisions, pending, decisions and the Web Cortex text columns (task
    objectives, reports and checkpoints, events, claims, knowledge); delete their changepoints and links.

    Caller owns the transaction. roots: {root: (kind, digest)} with kind 'camera' or 'partner'. Returns the
    number of rows rewritten or deleted. Works on any migrated connection to an instance file (also a
    restore's staged copy). A root has no evidence left in any channel, so both its camera:<root> and
    partner:<root> changepoints go, and so does every epoch link with a segment of that root.

    scoped=True: the root has no evidence left in its kind's channel, but the other channel still holds the same
    name (a camera forgotten while a partner of that name is held, or the reverse). Only that kind goes: its
    `<kind>:<root>` scope everywhere, its key in the summaries' `sensors` or `partners` map, its changepoints and
    the epoch links with a segment of it, and, in a question or decision about that very scope, the bare name too
    (the executive writes a partner's name bare there). A bare name anywhere else may be the other one, and stays.
    """
    rewritten, names = 0, sorted(roots)
    kinds = ("camera", "partner")
    links = db.execute("SELECT epoch, segments FROM epoch_links").fetchall() if db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='epoch_links'").fetchone() else []
    for epoch, segments in links:
        if any(f"{prefix}:{root}" in json.loads(segments) for root in roots
               for prefix in ((roots[root][0],) if scoped else kinds)):
            rewritten += db.execute("DELETE FROM epoch_links WHERE epoch=?", (epoch,)).rowcount
    for start in range(0, len(names), 32):  # bounded SQL expression size; labels survive a second pass
        chunk = {root: roots[root] for root in names[start:start + 32]}
        scopes = [f"{prefix}:{root}" for root in chunk for prefix in ((chunk[root][0],) if scoped else kinds)]
        rewritten += db.execute(f"DELETE FROM changepoints WHERE scope IN ({','.join('?' * len(scopes))})",
                                scopes).rowcount
        sub = _scrubber(chunk, scoped=scoped)
        own = {f"{chunk[root][0]}:{root}": root for root in chunk} if scoped else {}
        # Summaries are written with ensure_ascii, so a non-ASCII root is stored in its escaped form.
        needles = sorted({form for root in chunk for form in (root, json.dumps(root)[1:-1])})
        for table, columns, json_columns in _SCRUB_TABLES:
            match = " OR ".join(f"instr({c},?)>0" for c in columns for _ in needles)
            params = [n for _ in columns for n in needles]
            for row in db.execute(f"SELECT rowid,{','.join(columns)} FROM {table} WHERE {match}", params).fetchall():
                old = list(row[1:])
                new = old
                if own and columns[0] == "scope" and old[0] in own:
                    # A question or decision about the forgotten scope itself: every mention of the name is it.
                    root = own[old[0]]
                    one = {root: chunk[root]}
                    whole = _scrubber(one)
                    new = [_scrub_text(text, one, whole, as_json=column in json_columns)
                           for column, text in zip(columns, new)]
                new = [_scrub_text(text, chunk, sub, as_json=column in json_columns, scoped=scoped)
                       for column, text in zip(columns, new)]
                if new != old:
                    db.execute(f"UPDATE {table} SET {','.join(c + '=?' for c in columns)} WHERE rowid=?",
                               (*new, row[0]))
                    rewritten += 1
    if rewritten:
        _drop_in_process_copies()      # a knowledge statement may have held the forgotten name
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


def root_held(db, root, channel=None):
    """Whether any evidence of the root is left, as a raw event or as a consolidated reading (ADR-044); with
    `channel`, in that channel only."""
    where, params = ("root=?", (root,)) if channel is None else ("root=? AND channel=?", (root, channel))
    if db.execute(f"SELECT 1 FROM evidence WHERE {where} LIMIT 1", params).fetchone() is not None:
        return True
    return has_consolidated(db) and db.execute(f"SELECT 1 FROM evidence_consolidated WHERE {where} LIMIT 1",
                                               params).fetchone() is not None


def held_roots(db):
    """Every camera and word root the individual holds evidence of: the journal's, then those held only as
    consolidated readings (ADR-044). Private names: the Web Cortex never sends one out (orchestrator.private_terms)."""
    roots = [r for (r,) in db.execute("SELECT DISTINCT root FROM evidence WHERE channel IN ('camera','word')")]
    if has_consolidated(db):
        # One index seek per root, not a pass over every reading (the table only grows, and this runs for every web
        # query): the next root after the last one found, then whether it has a camera or word reading.
        roots += [r for (r,) in db.execute(
            "WITH RECURSIVE r(root) AS (SELECT min(root) FROM evidence_consolidated UNION ALL "
            "SELECT (SELECT min(c.root) FROM evidence_consolidated c WHERE c.root>r.root) FROM r "
            "WHERE r.root IS NOT NULL) SELECT root FROM r WHERE root IS NOT NULL AND EXISTS (SELECT 1 FROM "
            "evidence_consolidated c WHERE c.root=r.root "
            "AND c.channel IN ('camera','word'))")]
    return list(dict.fromkeys(roots))


def drop_consolidated(db, where, params, now, reason):
    """Delete the consolidated readings matching `where` (a predicate on evidence_consolidated) because the owner
    forgot them. Caller owns the transaction. Returns the deleted rows as (seq, None, epoch, channel, root).

    The tombstone that recorded the event's expiry (same seq and epoch) now says it was forgotten: a restore
    consults it to decide whether a copy may keep the reading (checkpoints._replay), and an 'expired' reason
    means "kept as a consolidated reading".
    """
    if reason not in REASONS or reason == "expired":
        raise ValidationError("a consolidated reading is only ever removed by a forget")
    if not has_consolidated(db):
        return []
    rows = db.execute(f"SELECT seq, NULL, epoch, channel, root FROM evidence_consolidated WHERE {where} ORDER BY seq",
                      params).fetchall()
    db.executemany("DELETE FROM evidence_consolidated WHERE seq=?", [(r[0],) for r in rows])
    db.executemany("UPDATE tombstones SET reason=?, deleted_at=? WHERE seq=? AND epoch=? AND reason='expired'",
                   [(reason, now, r[0], r[2]) for r in rows])
    return rows


def naming_forms(roots, *, scoped=False):
    """What an imagined record that names these forgotten roots holds (imagined.remove_naming): the root, or for a
    scoped forget (scrub_roots scoped) only `<kind>:<root>`, since the bare name may be the other kind's."""
    return [f"{roots[root][0]}:{root}" if scoped else root for root in sorted(roots)]


def _forget_roots(db, rows, now):
    """Scrub each camera/word root of the removed rows that has no evidence left, raw or consolidated; and, for a
    root the other channel still holds, the kind that has none left (scrub_roots scoped, recorded in
    forgotten_scopes so a restore does the same). Caller owns the txn. Returns (roots forgotten, scopes forgotten,
    rows rewritten, the forms an imagined record naming them holds: naming_forms)."""
    instance_id = store.birth(db)["instance_id"]
    roots, scopes = {}, {"camera": {}, "partner": {}}   # a root may lose both kinds and still be a reference root
    held = {}       # each root is looked up once, however many of its rows went (a sweep removes thousands)
    for _, _, _, channel, root in rows:
        if channel not in _KIND:
            continue
        if root not in held:
            held[root] = root_held(db, root)
            if not held[root]:
                roots[root] = (_KIND[channel], root_digest(instance_id, root))
        if held[root] and (channel, root) not in held:
            held[(channel, root)] = root_held(db, root, channel)
            if not held[(channel, root)]:
                scopes[_KIND[channel]][root] = (_KIND[channel], root_digest(instance_id, root))
    db.executemany("INSERT OR IGNORE INTO forgotten_roots(digest,kind,forgotten_at) VALUES(?,?,?)",
                   [(digest, kind, now) for kind, digest in roots.values()])
    db.executemany("INSERT OR IGNORE INTO forgotten_scopes(digest,kind,forgotten_at) VALUES(?,?,?)",
                   [(digest, kind, now) for part in scopes.values() for kind, digest in part.values()])
    scrubbed = scrub_roots(db, roots) + sum(scrub_roots(db, part, scoped=True) for part in scopes.values() if part)
    forms = naming_forms(roots) + [f for part in scopes.values() for f in naming_forms(part, scoped=True)]
    return len(roots), sum(map(len, scopes.values())), scrubbed, forms


def drop_epoch_rows(db, epoch, now=None):
    """Delete the epoch's derived rows: belief_revisions, decisions, pending, changepoints, every epoch link from
    or to it, its graph context and its streams, its consolidated readings (ADR-044; their tombstones then say
    'epoch_forgotten'), and clear the owner's free-text note. The epochs row itself stays: tombstones and
    deletion_log name it, and evidence rows reference it. Caller owns the transaction. Returns {table: rows deleted}
    (epoch_note: 1 when a note was cleared; evidence_consolidated only when any was deleted).

    Shared by forget_epoch and checkpoints._replay, so a restored copy loses exactly what the live forget removed.
    """
    derived = {table: db.execute(f"DELETE FROM {table} WHERE epoch=?", (epoch,)).rowcount
               for table in ("belief_revisions", "decisions", "pending", "changepoints")}
    if has_consolidated(db):
        stamp = "" if now is None else ", deleted_at=?"
        db.execute(f"UPDATE tombstones SET reason='epoch_forgotten'{stamp} WHERE epoch=? AND reason='expired' AND seq IN "
                   "(SELECT seq FROM evidence_consolidated WHERE epoch=?)",
                   (epoch, epoch) if now is None else (now, epoch, epoch))
        folded = db.execute("DELETE FROM evidence_consolidated WHERE epoch=?", (epoch,)).rowcount
        if folded:
            derived["evidence_consolidated"] = folded
    derived["epoch_links"] = db.execute("DELETE FROM epoch_links WHERE epoch=? OR source_epoch=?",
                                        (epoch, epoch)).rowcount
    for table in ("epoch_contexts", "epoch_streams"):
        derived[table] = db.execute(f"DELETE FROM {table} WHERE epoch=?", (epoch,)).rowcount
    derived["epoch_note"] = db.execute("UPDATE epochs SET note=NULL WHERE name=? AND note IS NOT NULL",
                                       (epoch,)).rowcount
    return derived


def _remove(db, where, params, now, *, reason, cause, kind, target, drop_epoch=None, consolidated=None,
            consolidate=False, scopes=()):
    """Caller owns the transaction. Returns the effects dict, or None when nothing matched.

    drop_epoch: also delete that epoch's derived rows (drop_epoch_rows; forget_epoch).
    scopes: also delete the confirmed and rejected changes of these scopes in every epoch (forget_camera: a camera
    without evidence must not stay in the beliefs as an empty segment).
    consolidated: (where, params) on evidence_consolidated: a forget also deletes those consolidated readings.
    consolidate: the sweep's expiry with InstanceConfig.consolidation on: each matched event with consent 'learn'
    leaves a consolidated reading (ADR-044), every copy of a sample its own: which copy counts is decided when
    beliefs are read (the earliest), so a forgotten first copy gives way to the next. Every matched event is deleted
    and tombstoned either way. Under a limit (InstanceConfig.max_consolidated_rows) only as many readings are added
    as it leaves room for, the oldest events first by their place in the journal; the others expire to nothing, and
    effects["not_consolidated"] counts them. No reading already kept is ever removed for the limit.
    """
    rows = db.execute(f"SELECT seq,id,epoch,channel,root FROM evidence WHERE {where} ORDER BY seq", params).fetchall()
    folded_rows = drop_consolidated(db, *consolidated, now, reason) if consolidated is not None else []
    sources = sorted({r[2] for r in rows + folded_rows} | ({drop_epoch} if drop_epoch is not None else set()))
    linked = [e for (e,) in db.execute(f"SELECT epoch FROM epoch_links WHERE source_epoch IN "
                                       f"({','.join('?' * len(sources))}) ORDER BY epoch", sources)
              if e != drop_epoch and e not in sources]
    derived = drop_epoch_rows(db, drop_epoch, now) if drop_epoch is not None else {}
    if scopes:
        derived = derived | {"changepoints": derived.get("changepoints", 0) + db.execute(
            f"DELETE FROM changepoints WHERE scope IN ({','.join('?' * len(scopes))})", tuple(scopes)).rowcount}
    if not rows and not folded_rows and not any(derived.values()):
        return None
    kept, turned_away = set(), 0
    if consolidate and rows and has_consolidated(db) and db.execute(
            f"SELECT 1 FROM evidence WHERE ({where}) AND consent='learn' LIMIT 1", params).fetchone():
        # (only a sweep that meets a learning event counts the readings for the limit)
        # A seq is issued once (AUTOINCREMENT; a restore raises the counter past the live file's,
        # checkpoints._replay), so no reading can hold it already; OR IGNORE only keeps a file that broke that rule
        # sweepable.
        room = store.consolidation_room(db)             # None: no limit
        if room is None or room >= len(rows):
            limit, limit_params = "", ()
        else:                                           # the limit may bite: the oldest events first, by seq
            limit, limit_params = " ORDER BY seq LIMIT ?", (room,)
            turned_away = db.execute(f"SELECT count(*) FROM evidence WHERE ({where}) AND consent='learn'",
                                     params).fetchone()[0]
        db.execute(
            "INSERT OR IGNORE INTO evidence_consolidated(seq,epoch,channel,root,value,sample_id,consolidated_at) "
            f"SELECT seq, epoch, channel, root, bit<>action, sample_id, ? FROM evidence WHERE ({where}) "
            f"AND consent='learn'{limit}", (now, *params, *limit_params))
        kept = {r[0] for r in rows} & {seq for (seq,) in db.execute(
            "SELECT seq FROM evidence_consolidated WHERE seq BETWEEN ? AND ?", (rows[0][0], rows[-1][0]))}
        turned_away = max(0, turned_away - len(kept))
    db.executemany("DELETE FROM evidence WHERE seq=?", [(r[0],) for r in rows])
    db.executemany("INSERT OR IGNORE INTO tombstones(id,seq,epoch,deleted_at,reason) VALUES(?,?,?,?,?)",
                   [(i, seq, epoch, now, reason) for seq, i, epoch, _, _ in rows])
    stale = []
    if rows:
        first = min(r[0] for r in rows)
        stale = [r[0] for r in db.execute(
            "SELECT id FROM checkpoints WHERE status='valid' AND max_evidence_seq>=? ORDER BY id", (first,))]
        db.executemany("UPDATE checkpoints SET status='stale' WHERE id=?", [(i,) for i in stale])
    if any(derived.values()) or folded_rows:
        # A derived row carries no evidence seq (and may outlive its expired evidence), so any checkpoint may hold it.
        # The same goes for a consolidated reading: a checkpoint's max_evidence_seq says nothing about it.
        stale = _stale_all(db, stale)
    rows = sorted(rows + folded_rows)
    epochs = sorted({r[2] for r in rows})
    version = store.bump_state_version(db)
    from .beliefs import compute  # lazy: governance must not depend on inference at import time
    for epoch in epochs + linked:
        if epoch == drop_epoch:
            continue
        count = sum(1 for r in rows if r[2] == epoch)
        # Always record a revision: the post-deletion belief state is part of the semantic history (MEM-03/08).
        summary = compute(db, epoch) | {"removed": count, "cause": cause}
        if kept:
            summary["consolidated_now"] = sum(1 for r in rows if r[2] == epoch and r[0] in kept)
        if turned_away:
            summary["consolidation_limit_reached"] = True   # some of this sweep's learning events left no reading
        db.execute("INSERT INTO belief_revisions(epoch,state_version,created_at,cause,summary) VALUES(?,?,?,?,?)",
                   (epoch, version, now, cause, _dump(summary)))
    causes = _forget_causes(db, [r[1] for r in rows if r[1] is not None])
    forgotten, scoped, scrubbed, forms = _forget_roots(db, rows, now)
    effects = {"deleted": len(rows) - len(folded_rows), "epochs": epochs, "checkpoints_stale": stale,
               "state_version": version, "roots_forgotten": forgotten, "derived_rows_scrubbed": scrubbed + causes}
    if scoped:
        effects["scopes_forgotten"] = scoped    # a camera or partner gone while the other channel keeps its name
    if folded_rows:
        effects["consolidated_deleted"] = len(folded_rows)
    if kept:
        effects["consolidated"] = len(kept)
    if turned_away:
        effects["not_consolidated"] = turned_away       # learning events the limit on readings left nothing of
    if linked:
        effects["linked_epochs_revised"] = linked
    if drop_epoch is not None or any(derived.values()):
        effects["derived_deleted"] = derived
    logged = db.execute("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects) VALUES(?,?,?,?)",
                        (kind, target, now, json.dumps(effects, sort_keys=True))).lastrowid
    # after the log row: an epoch's row outlives its forget, so that row is what says the epoch is gone
    if _imagined_cascade(db, effects, now, forms).get("imagined_removed"):
        db.execute("UPDATE deletion_log SET effects=? WHERE id=?", (json.dumps(effects, sort_keys=True), logged))
    return effects


def _artifacts():
    """The artifact store (OMNIMIND, ADR-071), imported when a forget of a root needs it: it removes every artifact
    whose label, index, text layer or bytes name the root (artifacts/store.py scan_root, forget_found, finish)."""
    from ..artifacts import store as artifact_store
    return artifact_store


def _imagined_cascade(db, effects, now, forms=()):
    """Remove the imagined records (ADR-049) derived from anything this forget removed and then, a record being
    never rewritten, those whose text holds a forgotten root (`forms`: naming_forms), as stored or inside a base64,
    zlib or gzip value (imagined.remove_naming, which states its bounds; archives written since the oldest of those
    become stale too). effects["imagined_removed"] counts both (absent while zero). Caller owns the transaction."""
    removed = imagined.purge_orphans(db, now)
    named, stale = imagined.remove_naming(db, forms, now)
    if named:
        removed += named
        effects["checkpoints_stale"] = sorted({*effects.get("checkpoints_stale", ()), *stale})
    if removed:
        effects["imagined_removed"] = removed
    return effects


def _stale_all(db, stale=()):
    """Mark every valid checkpoint stale. Caller owns the transaction. Returns the ids now stale (sorted)."""
    more = [r[0] for r in db.execute("SELECT id FROM checkpoints WHERE status='valid' ORDER BY id")]
    db.executemany("UPDATE checkpoints SET status='stale' WHERE id=?", [(i,) for i in more])
    return sorted({*stale, *more})


REBUILD_HINT = ("{n} checkpoint archive(s) still hold the forgotten data and are now marked stale: rebuild them "
                "with `nsf checkpoint rebuild` (console: Checkpoints, Rebuild stale archives)")


def _result(db, effects):
    """The forget result. When checkpoint archives became stale, `next_steps` says how to rebuild them (a stale
    archive keeps the forgotten payload until it is rebuilt or pruned)."""
    if effects:
        effects = effects | {"wal_checkpoint": store.truncate_wal(db)}
    out = {"status": "forgotten" if effects else "absent", "effects": effects or {}}
    stale = (effects or {}).get("checkpoints_stale") or []
    if stale:
        out["next_steps"] = [REBUILD_HINT.format(n=len(stale))]
    return out


def forget_evidence(db, event_id, now, *, reason="forgotten"):
    if reason not in REASONS:
        raise ValidationError(f"reason must be one of {', '.join(REASONS)}")
    if not isinstance(event_id, str) or not event_id:
        raise ValidationError("event id must be a non-empty string")
    # The event id is caller-chosen and may carry meaning, so the revision cause does not repeat it.
    with store.write(db):
        # An event whose lifetime ended may live on as a consolidated reading (ADR-044). It has no id any more: its
        # tombstone, which keeps the id with the seq, finds it, so the owner can still forget exactly that event.
        # The tombstone is looked up first (by its key), then the one reading (by its key): nothing is scanned,
        # whether the id is a journal event's, a consolidated one's or unknown.
        expired = None if reason == "expired" else db.execute(
            "SELECT seq, epoch FROM tombstones WHERE id=? AND reason='expired'", (event_id,)).fetchone()
        effects = _remove(db, "id=?", (event_id,), now, reason=reason, cause=FORGOTTEN_CAUSE, kind="evidence",
                          target=event_id, consolidated=("seq=? AND epoch=?", tuple(expired)) if expired else None)
    return _result(db, effects)


def forget_epoch(db, epoch, now):
    """Forget every evidence row of the epoch, its belief revisions, decisions, questions and changepoints, every
    epoch link from or to it, its graph-context association, its population streams and the owner's note."""
    with store.write(db):
        if db.execute("SELECT 1 FROM epochs WHERE name=?", (epoch,)).fetchone() is None:
            raise NotFound(f"Unknown epoch '{epoch}'")
        effects = _remove(db, "epoch=?", (epoch,), now, reason="epoch_forgotten",
                          cause=f"forget:epoch:{epoch}", kind="epoch", target=epoch, drop_epoch=epoch,
                          consolidated=("epoch=?", (epoch,)))
    return _result(db, effects)


def forget_partner(db, root, now):
    if not isinstance(root, str) or not root:
        raise ValidationError("partner root must be a non-empty string")
    found = _artifacts().scan_root(db, root)        # the artifacts naming it, found outside the transaction
    with store.write(db):
        # The root is the partner's payload, so the log keeps only its instance-salted label.
        label = "partner:#" + root_digest(store.birth(db)["instance_id"], root)
        effects = _remove(db, "channel='word' AND root=?", (root,), now, reason="partner_forgotten",
                          cause=f"forget:{label}", kind="partner", target=label,
                          consolidated=("channel='word' AND root=?", (root,)), scopes=(f"partner:{root}",))
        effects = _artifacts().forget_found(db, found, now, effects, label)
    return _artifacts().finish(db, found, _result(db, effects))


def replay_scope_forgets(copy, forgets, version):
    """In a restore's staged copy: delete the changepoints of every camera or partner the live file forgot after the
    copy was written. Caller owns the transaction. Returns the number of changepoints deleted.

    forgets: [(kind, label, state version of the forget, requested_at, effects JSON)] from the live deletion_log,
    kind in SCOPE_KINDS and label '<kind>:#<digest>'. The log holds no name, so the copy's own changepoint scopes are
    matched by their instance-salted digest. Only a copy older than the forget (state version) is touched: in a newer
    one a scope of that name is a later camera or partner. The copy takes the record over, dated after its own state
    version as a replayed epoch forget is, so an older archive restored FROM the copy loses the changepoints too.
    Without this, forget_camera (or forget_partner) of a root that a partner (or camera) of the same name still
    holds left the forgotten scope in a restored copy as an empty segment: listed as a sensor, counted by the
    executive, and a new camera of that name landed in the old segment (second-pass review S2).
    """
    pending = [f for f in forgets if version < f[2]]
    if not pending:
        return 0
    instance_id = store.birth(copy)["instance_id"]
    scopes = {}
    for (scope,) in copy.execute("SELECT DISTINCT scope FROM changepoints").fetchall():
        kind, _, root = scope.partition(":")
        if kind in SCOPE_KINDS and root:
            scopes[f"{kind}:#{root_digest(instance_id, root)}"] = scope
    removed = 0
    for kind, label, _, requested_at, effects_json in pending:
        if label in scopes:
            removed += copy.execute("DELETE FROM changepoints WHERE scope=?", (scopes[label],)).rowcount
        if not copy.execute("SELECT 1 FROM deletion_log WHERE target_kind=? AND target_id=? AND requested_at=?",
                            (kind, label, requested_at)).fetchone():
            carried = {k: v for k, v in json.loads(effects_json).items() if k != "checkpoints_stale"} \
                | {"state_version": version + 1, "replayed": True}
            copy.execute("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects) VALUES(?,?,?,?)",
                         (kind, label, requested_at, json.dumps(carried, sort_keys=True)))
            copy.execute("UPDATE checkpoints SET status='stale' WHERE status='valid'")
    return removed


def forget_camera(db, root, now):
    """Forget everything a camera root taught, in every epoch: its events in the journal, its consolidated readings
    (ADR-044) and the changes confirmed or rejected for it. The mirror of forget_partner for the camera channel: a
    camera root is a private name too, and since consolidation it no longer leaves on its own after the lifetime.
    The root is scrubbed from the history when no evidence of that name is left in any channel; the log keeps only
    its instance-salted label. Tombstones say 'forgotten'.
    """
    if not isinstance(root, str) or not root:
        raise ValidationError("camera root must be a non-empty string")
    found = _artifacts().scan_root(db, root)        # the artifacts naming it, found outside the transaction
    with store.write(db):
        label = "camera:#" + root_digest(store.birth(db)["instance_id"], root)
        effects = _remove(db, "channel='camera' AND root=?", (root,), now, reason="forgotten",
                          cause=f"forget:{label}", kind="camera", target=label,
                          consolidated=("channel='camera' AND root=?", (root,)), scopes=(f"camera:{root}",))
        effects = _artifacts().forget_found(db, found, now, effects, label)
    return _artifacts().finish(db, found, _result(db, effects))


def forget_consolidated(db, seq, now):
    """Forget one consolidated reading by its journal position, the id `nsf memory consolidated` and the console
    show for it (the event's own id is known only to its tombstone, which no listing shows). Everything else is as
    for forget_evidence on an expired event: the tombstone then says 'forgotten', a belief revision and a
    deletion_log row are written, every checkpoint is stale, and a restore removes the reading from any copy.
    A seq that names no consolidated reading (an event still in the journal is forgotten by its id) is 'absent'.
    """
    if isinstance(seq, bool) or not isinstance(seq, int) or not 1 <= seq <= MAX_ROWID:
        raise ValidationError(f"the reading's seq must be an integer from 1 to {MAX_ROWID}")
    with store.write(db):
        effects = _remove(db, "0", (), now, reason="forgotten", cause=FORGOTTEN_CAUSE, kind="consolidated",
                          target=str(seq), consolidated=("seq=?", (seq,)))
    return _result(db, effects)


def forget_dataset(db, dataset_id, now):
    """Drop a dataset blob, its note and its digest, delete the graph effects measured on it, invalidate graphs
    trained on it and erase what they learned (weights, the digest of the weights, metrics, the scores of their
    decisions, effects measured on them; ADR-048), and mark every existing checkpoint stale.

    A dataset forgotten before, by an earlier version that kept what graphs learned from it, has that finished now
    ("completed"; the owner already named it, and a restore of any archive finishes it in its copy too). Nothing
    left to do: "absent"."""
    from .graphs import erase_learned_from, invalidate_dependents  # W2-B; imported lazily to keep modules decoupled
    if isinstance(dataset_id, bool) or not isinstance(dataset_id, int) or not -MAX_ROWID - 1 <= dataset_id <= MAX_ROWID:
        raise ValidationError("the dataset id must be an integer SQLite can hold")
    failure = None
    with store.write(db):
        row = db.execute("SELECT deleted_at FROM datasets WHERE id=?", (dataset_id,)).fetchone()
        if row is None:
            raise NotFound(f"Unknown dataset {dataset_id}")
        if row[0] is not None:
            effects = _finish_dataset_forget(db, dataset_id, now, by="forget_dataset")
            status = "completed"
        else:
            # the note is the owner's free text about the data (as an epoch's note, forget_epoch): it goes with the
            # blob. The digest goes too: with it, a guessed dataset could be checked against the file (ADR-048).
            db.execute("UPDATE datasets SET blob=NULL, note=NULL, sha256='', deleted_at=? WHERE id=?",
                       (now, dataset_id))
            effects_deleted = db.execute("DELETE FROM graph_effects WHERE dataset_id=?", (dataset_id,)).rowcount
            cascade = invalidate_dependents(db, dataset_id, now)
            erased = erase_learned_from(db, dataset_id, now)
            stale = _stale_checkpoints(db)
            version = store.bump_state_version(db)
            effects = {"dataset": dataset_id, "invalidated": cascade.get("invalidated", []),
                       "reactivated": cascade.get("reactivated"), "checkpoints_stale": stale,
                       "graph_effects_deleted": effects_deleted + erased["effects_deleted"],
                       "graphs_erased": erased["erased"], "decisions_redacted": erased["decisions_redacted"],
                       "state_version": version}
            _imagined_cascade(db, effects, now)
            status, failure = "forgotten", cascade.get("failure")
            db.execute("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects,failure) "
                       "VALUES('dataset',?,?,?,?)",
                       (str(dataset_id), now, json.dumps(effects, sort_keys=True), failure))
    if effects is None:
        return {"status": "absent", "effects": {}}
    effects["wal_checkpoint"] = store.truncate_wal(db)
    return {"status": status, "effects": effects, "failure": failure}


def _stale_checkpoints(db):
    stale = [r[0] for r in db.execute("SELECT id FROM checkpoints WHERE status='valid' ORDER BY id")]
    db.executemany("UPDATE checkpoints SET status='stale' WHERE id=?", [(i,) for i in stale])
    return stale


# Deleted datasets of which something learned is still held: the digest, effects measured on them, or a revision
# trained or validated on them that is not fully erased (a forget by a version before ADR-048, or by schema 15's
# first build, which kept the weights' digest).
_UNFINISHED = (
    "SELECT d.id FROM datasets d WHERE d.deleted_at IS NOT NULL AND (d.sha256!='' "
    "OR EXISTS(SELECT 1 FROM graph_effects e WHERE e.dataset_id=d.id) "
    "OR EXISTS(SELECT 1 FROM graph_lineage l JOIN graph_revisions r ON r.id=l.revision_id WHERE l.dataset_id=d.id "
    "AND l.role IN ('train','validation') AND r.status='invalidated' AND (r.blob!=X'' OR r.sha256!='erased'))) "
    "ORDER BY d.id")


def _finish_dataset_forget(db, dataset_id, now, *, by):
    """For a dataset already deleted: remove its digest and the effects measured on it, erase what invalidated
    revisions learned from it (graphs.erase_learned_from), mark checkpoints stale when something was removed
    (archives taken since the first forget hold it), and log it. Caller owns the transaction. None when nothing
    was left to remove."""
    from .graphs import erase_learned_from
    digest = db.execute("UPDATE datasets SET sha256='' WHERE id=? AND sha256!=''", (dataset_id,)).rowcount
    effects_deleted = db.execute("DELETE FROM graph_effects WHERE dataset_id=?", (dataset_id,)).rowcount
    erased = erase_learned_from(db, dataset_id, now)
    if not (digest or effects_deleted or erased["erased"] or erased["effects_deleted"]):
        return None
    stale = _stale_checkpoints(db)
    version = store.bump_state_version(db)
    effects = {"dataset": dataset_id, "completed": True, "by": by, "digest_removed": bool(digest),
               "graph_effects_deleted": effects_deleted + erased["effects_deleted"],
               "graphs_erased": erased["erased"], "decisions_redacted": erased["decisions_redacted"],
               "checkpoints_stale": stale, "state_version": version}
    _imagined_cascade(db, effects, now)
    db.execute("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects) VALUES('dataset',?,?,?)",
               (str(dataset_id), now, json.dumps(effects, sort_keys=True)))
    return effects


def finish_earlier_forgets(db, now):
    """At an open (store.open_existing): finish, once, the forgets of datasets an earlier version left unfinished
    (ADR-048), so the live file holds what every restored copy holds. Read first without the write lock; done only
    when this connection can write, and skipped for this open when the file is busy (store.optional_write). Returns
    the effects per dataset, or None when there was nothing to do or it could not be done now."""
    if not db.execute(_UNFINISHED).fetchone() or not store.can_write(db):
        return None

    def run(db):
        done = [effects for (dataset_id,) in db.execute(_UNFINISHED).fetchall()
                if (effects := _finish_dataset_forget(db, dataset_id, now, by="upgrade")) is not None]
        return done or None
    done = store.optional_write(db, run)
    if done:
        store.truncate_wal(db)
    return done


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
    """Expire unpromoted evidence and epoch streams whose lifetime ended, and imagined records (ADR-049) past
    theirs ("imagined_expired", absent while zero).

    With InstanceConfig.consolidation on (the default, ADR-044) an expiring learning event leaves a consolidated
    reading: its seq, epoch, channel, root, corrected reading and sample id, which beliefs keep counting. Its id,
    actor, action and bit, provenance and timestamps go, as do memory-only events and later copies of a sample;
    "consolidated" in the result counts the readings kept. With it off an expired event leaves nothing.

    The file keeps at most InstanceConfig.max_consolidated_rows readings (0 = no limit). A sweep that would pass the
    limit consolidates the oldest of its learning events, by their place in the journal, until the limit is reached;
    the rest expire to nothing, as with consolidation off, and "not_consolidated" counts them. "warnings" then says
    what happened and what the owner can do; from 80 % of the limit it says how close the limit is. The limit never
    removes a reading that is kept.
    """
    with store.write(db):
        effects = _remove(db, "expires_at IS NOT NULL AND expires_at<=? AND retention!='promoted'", (now,), now,
                          reason="expired", cause="expired", kind="sweep", target=f"{now:.0f}",
                          consolidate=store.config(db).consolidation)
        streams = db.execute("DELETE FROM epoch_streams WHERE expires_at IS NOT NULL AND expires_at<=?",
                             (now,)).rowcount
        expired_imagined = imagined.expire(db, now)
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
    if effects and effects.get("consolidated"):
        out["consolidated"] = effects["consolidated"]       # absent while zero
    if effects and effects.get("not_consolidated"):
        out["not_consolidated"] = effects["not_consolidated"]   # absent while zero
    if expired_imagined:
        out["imagined_expired"] = expired_imagined          # absent while zero
    problems = [p for p in store.setting_problems(db) if "consolidat" in p or "settings" in p]
    if effects and (effects.get("consolidated") or effects.get("not_consolidated")):
        # Only a sweep that met learning events looks at the limit: the usual sweep, which expires nothing, costs
        # no count of the readings.
        problems += store.consolidation_warnings(db)
    if problems:
        out["warnings"] = problems                          # absent for sound settings below the warning share
    if effects or streams or expired_imagined:
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
WEB_FORGOTTEN_KINDS = ("task", "knowledge", "source_url", "source_domain")   # + VOICE_KIND (migration 11)
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


def _has_evolution(db):
    """Migration 8 (knowledge evolution, ADR-038) is present."""
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='web_hypotheses'").fetchone() is not None


def _drop_node_links(db, kinds, ids):
    """Delete the typed knowledge-graph edges touching nodes (kind in `kinds`, id in `ids`). Caller owns the txn."""
    for part in _chunks(sorted({str(i) for i in ids})):
        marks, kmarks = _in(part), _in(kinds)
        db.execute(f"DELETE FROM web_claim_links WHERE (src_kind IN ({kmarks}) AND src IN ({marks})) OR "
                   f"(dst_kind IN ({kmarks}) AND dst IN ({marks}))", (*kinds, *part, *kinds, *part))


def _drop_predictions(db, where, params):
    """Delete predictions (and their prediction/error graph nodes). Caller owns the txn."""
    ids = _ids(db, f"SELECT id FROM web_predictions WHERE {where}", params)
    if ids:
        _drop_node_links(db, ("prediction", "error"), ids)
        for part in _chunks(ids):
            db.execute(f"DELETE FROM web_predictions WHERE id IN ({_in(part)})", part)
    return ids


def _drop_hypotheses(db, ids):
    """Delete hypotheses with their predictions and graph edges; revisions of them lose the parent link (their own
    statement rests on other evidence). Caller owns the txn. Returns the ids."""
    ids = sorted(set(ids))
    for part in _chunks(ids):
        marks = _in(part)
        _drop_predictions(db, f"hypothesis_id IN ({marks})", part)
        db.execute(f"UPDATE web_hypotheses SET parent_id=NULL WHERE parent_id IN ({marks})", part)
        db.execute(f"DELETE FROM web_hypotheses WHERE id IN ({marks})", part)
        db.execute(f"DELETE FROM web_events WHERE json_extract(detail,'$.hypothesis_id') IN ({marks})", part)
    if ids:
        _drop_node_links(db, ("hypothesis",), ids)
    return ids


def _scrub_observations(db, task_id):
    """Predictions settled or tracked by a forgotten task keep their outcome but lose what the task observed."""
    for pid, detail in db.execute("SELECT id, detail FROM web_predictions WHERE task_id=? OR detail LIKE ?",
                                  (task_id, f"%{task_id}%")).fetchall():
        value = json.loads(detail or "{}")
        obs = value.get("observation") or {}
        changed = False
        if obs.get("task_id") == task_id or value.get("task_id") == task_id:
            for k in ("observation", "error_analysis", "task_id"):
                changed |= value.pop(k, None) is not None
            value["observation"] = {"forgotten": True}
        kept = [o for o in value.get("observations") or [] if o.get("task_id") != task_id]
        if kept != (value.get("observations") or []):
            value["observations"], changed = kept, True
        if changed:
            db.execute("UPDATE web_predictions SET detail=? WHERE id=?", (_dump(value), pid))


def _drop_counter_support(db, ids):
    """A hypothesis opened against a knowledge item counts that item's supporting claims as evidence against it
    (evolution._describe). When the item is forgotten its support is no longer evidence against anything: remove those
    claims from the counter-evidence of the hypotheses opened against it (review1 CM2). Caller owns the txn."""
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='web_hypothesis_details'").fetchone() is None:
        return
    for part in _chunks(ids):
        marks = _in(part)
        support = {}
        for kid, supporting in db.execute(f"SELECT id, supporting FROM web_knowledge WHERE id IN ({marks})", part):
            support[kid] = set(json.loads(supporting or "[]"))
        for hid, kid, counter in db.execute(
                "SELECT h.id, h.knowledge_id, d.counter_evidence FROM web_hypotheses h JOIN web_hypothesis_details d "
                f"ON d.hypothesis_id=h.id WHERE h.knowledge_id IN ({marks})", part).fetchall():
            held = json.loads(counter or "[]")
            kept = [c for c in held if c not in support.get(kid, set())]
            if kept != held:
                db.execute("UPDATE web_hypothesis_details SET counter_evidence=? WHERE hypothesis_id=?",
                           (_dump(kept), hid))


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
    if _has_evolution(db):
        _drop_node_links(db, ("knowledge", "interval"), ids)
    _drop_counter_support(db, ids)
    for part in _chunks(ids):
        marks = _in(part)
        db.execute(f"DELETE FROM web_knowledge WHERE id IN ({marks})", part)
        if _has_evolution(db):
            _drop_predictions(db, f"knowledge_id IN ({marks})", part)
            db.execute(f"UPDATE web_hypotheses SET knowledge_id=NULL WHERE knowledge_id IN ({marks})", part)
        db.execute(f"DELETE FROM web_predictions WHERE knowledge_id IN ({marks})", part)
        db.execute(f"DELETE FROM web_feedback WHERE knowledge_id IN ({marks})", part)
        db.execute(f"DELETE FROM web_mentions WHERE knowledge_id IN ({marks})", part)
        db.execute(f"DELETE FROM web_events WHERE json_extract(detail,'$.knowledge_id') IN ({marks})", part)
    closed = _forget_pending(db, [f"web:knowledge:{k}" for k in ids], [], now)
    _forget_replaced_statements(db, ids)
    _drop_in_process_copies()
    return {"knowledge_deleted": ids, "versions_restored": sorted(restored), "versions_relinked": sorted(relinked),
            "pending_closed": closed}


def _forget_replaced_statements(db, ids):
    """ADR-061 (security review M1): the supersession rule's question, recorded under the NEW version's scope, quotes
    the statement it replaced. When that replaced version is forgotten, the question loses its text (the record
    keeps only the replaced id, a number). Caller owns the txn."""
    for part in _chunks(sorted(set(ids))):
        for (kid,) in db.execute("SELECT id FROM web_knowledge WHERE json_extract(verification, "
                                 f"'$.supersession.replaced') IN ({_in(part)})", part).fetchall():
            db.execute("UPDATE pending SET question=? WHERE scope=? AND resolution LIKE 'answered by the supersession "
                       "rule%'", (_WEB_QUESTION, f"web:knowledge:{kid}"))


def _scrub_rule_records(db):
    """ADR-061 (security review M1): a supersession record written before the fix lane kept the replaced row's
    sources and supporting claims; every web forget (live or replayed) cuts such a record down to the columns the
    replacement changed (consolidate.RESTORE_COLUMNS), so a forgotten source is neither kept there nor written back by
    a revert. Caller owns the txn. Returns the records changed."""
    from ..webcortex.consolidate import RESTORE_COLUMNS
    changed = 0
    for kid, raw in db.execute("SELECT id, verification FROM web_knowledge WHERE json_extract(verification, "
                               "'$.supersession.previous') IS NOT NULL").fetchall():
        value = json.loads(raw)
        previous = value["supersession"]["previous"]
        if isinstance(previous, dict) and set(previous) - set(RESTORE_COLUMNS):
            value["supersession"]["previous"] = {k: v for k, v in previous.items() if k in RESTORE_COLUMNS}
            db.execute("UPDATE web_knowledge SET verification=? WHERE id=?", (_dump(value), kid))
            changed += 1
    return changed


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
            if _has_evolution(db):   # entity co-mention edges cite the claim; hypotheses state a claim's text
                db.execute(f"DELETE FROM web_claim_links WHERE json_extract(detail,'$.claim') IN ({marks})", part)
                hypotheses = _ids(db, f"SELECT DISTINCT h.id FROM web_hypotheses h, json_each(h.claims) j "
                                      f"WHERE j.value IN ({marks})", part)
                _drop_hypotheses(db, hypotheses)
    effects = _drop_knowledge(db, doomed, now)
    effects["knowledge_stale"] = sorted(set(weakened) - set(doomed))
    effects["claims_removed"] = len(gone)
    db.execute("DELETE FROM web_entities WHERE id NOT IN (SELECT entity_id FROM web_mentions)")
    return effects


def _premise_cascade(db, effects, now):
    """Lane K (ADR-055): premise rows and re-check marks naming what this forget removed go; what rested on it is
    marked "to re-check" (webcortex.premises.purge); reflection lines naming it go (reflection.purge).
    effects["premises"] only when something changed."""
    from ..webcortex import premises, reflection     # lazy, as the other webcortex imports here
    found = premises.purge(db, now)
    found["reflection_lines_removed"] = reflection.purge(db)
    if any(found.values()):
        effects["premises"] = found
    return effects


def _web_finish(db, kind, target, effects, now):
    """Stale checkpoints, state version and deletion_log. Caller owns the txn. `target` is persisted: it must
    not carry forgotten payload (a URL or domain goes in as its digest label)."""
    _scrub_rule_records(db)
    _premise_cascade(db, effects, now)
    _imagined_cascade(db, effects, now)
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


def _claim_texts(db, claim_ids):
    """The texts of the claims (read before they are deleted: _forget_quoted)."""
    texts = set()
    for part in _chunks(sorted(claim_ids)):
        texts.update(t for (t,) in db.execute(f"SELECT text FROM web_claims WHERE id IN ({_in(part)})", part) if t)
    return texts


def _forget_quoted(db, texts):
    """review3b N6: a contradiction question (the ask path under the held item's scope, and the supersession rule's
    answered one) quotes the sentence of the claim that contradicted, «...». When that claim is forgotten its quote is
    replaced by «#forgotten» (the question keeps its state: the contradiction may still stand on other sources), so
    the forgotten sentence stays nowhere. A quote of the same sentence from a source that is kept is replaced too
    (the safe side); the held item's own statement ("known «...»") is the item's and stays while the item does.
    Caller owns the txn; a restore replays it with the forget. Returns the questions changed."""
    from ..webcortex import consolidate, knowledge
    quotes = set()
    for t in texts:
        for variant in (t, consolidate.safe_text(t)):
            if variant and variant != consolidate.WITHHELD:
                quotes.add(f"«{knowledge.quote(variant)}»")
    changed = []
    if not quotes:
        return changed
    for pid, question in db.execute("SELECT id, question FROM pending WHERE substr(scope,1,4)='web:' AND "
                                    "instr(question,'«')>0").fetchall():
        known = re.match(r"(.*?known «[^»]*»)(.*)", question, re.DOTALL)
        head, rest = (known.group(1), known.group(2)) if known else ("", question)
        redacted = rest
        for q in quotes:
            redacted = redacted.replace(q, "«" + _WEB_QUESTION + "»")
        if redacted != rest:
            db.execute("UPDATE pending SET question=? WHERE id=?", (head + redacted, pid))
            changed.append(pid)
    return changed


def _sourcing_plan(db, claim_ids):
    """{digit token: (earliest time a claim stating it was read, the subject names of those claims)} of the specific
    values of the claims (security review M3's mark). A claim that names no subject marks nothing (review3b N9: the
    mark needs a shared subject name, as echo recognition's own source check does)."""
    from ..webcortex import consolidate
    plan = {}
    for part in _chunks(sorted(claim_ids)):
        for values, at, text in db.execute(
                f"SELECT claim_values, retrieved_at, text FROM web_claims WHERE id IN ({_in(part)})", part).fetchall():
            try:
                listed = json.loads(values or "[]")
            except ValueError:
                continue
            names = consolidate._name_set(text or "")
            if not names or not isinstance(at, (int, float)):
                continue
            for v in listed if isinstance(listed, list) else ():
                if isinstance(v, str) and consolidate._specific(v):
                    for token in consolidate._DIGITS.findall(v):
                        first, known = plan.get(token, (at, set()))
                        plan[token] = (min(first, at), known | names)
    return plan


def _task_versions(db):
    """{rowid: (status, updated_at)} of every task, read from the covering index web_tasks_status (no result is
    read): a task whose result was written since an earlier reading shows here as changed."""
    return {rowid: (status, at) for rowid, status, at in db.execute("SELECT rowid, status, updated_at FROM web_tasks")}


def _answer_rows(db, tokens, rowids=None):
    """rowids of tasks with a model-written result one of whose digit runs is a token: ONE pass over the results
    (or over `rowids`), whatever the number of tokens (review3b N2: one LIKE scan per 100 tokens before)."""
    from ..webcortex import consolidate
    found = set()
    if rowids is None:
        rows = db.execute("SELECT rowid, result FROM web_tasks WHERE result LIKE '%model:%'")
    else:
        rows = (r for part in _chunks(sorted(rowids)) for r in db.execute(
            f"SELECT rowid, result FROM web_tasks WHERE rowid IN ({_in(part)}) AND result LIKE '%model:%'", part))
    for rowid, raw in rows:
        if isinstance(raw, str) and not tokens.isdisjoint(consolidate._DIGITS.findall(raw)):
            found.add(rowid)
    return found


def prepare_sourced_marks(db, claim_ids):
    """The read-only half of _mark_sourced_answers, run BEFORE the forget's write transaction (review3b N2: the scan
    of every task result held the write lock, 6.9 s for one page at 10,000 tasks, so other writers met their busy
    timeout): the tokens, the candidate answers and every task's version at the time of the scan."""
    plan = _sourcing_plan(db, claim_ids)
    if not plan:
        return {"tokens": frozenset(), "rows": frozenset(), "seen": {}}
    seen = _task_versions(db)
    return {"tokens": frozenset(plan), "rows": frozenset(_answer_rows(db, set(plan))), "seen": seen}


def _mark_sourced_answers(db, claim_ids, prepared=None):
    """ADR-061 (security review M3): before claims are forgotten, every stored model-written answer sentence that
    states a specific value of one of them (a digit run equal to the value's), names a subject the claim named, and
    was written at or after that claim was read is marked as having had a source (reasoning["sourced"]: indexes of
    its text's sentences; answer[i]["sourced"]: true). The mark names no forgotten content. Without it, echo
    recognition would take the answer for the individual's own unsourced text once its source is gone, and later
    honest pages stating the value would be discarded as its echoes for ever.
    Caller owns the txn (the live forget and every checkpoint replay). With `prepared` (prepare_sourced_marks, made
    before the transaction for these claims) only its candidate answers and the tasks changed since it was made are
    read here; without it (or when the claims now state a token it did not plan for) one pass over every result.
    Returns the answers marked."""
    from ..webcortex import consolidate, text as T
    plan = _sourcing_plan(db, claim_ids)
    if not plan:
        return 0
    tokens = set(plan)
    if prepared is not None and tokens <= prepared["tokens"]:
        now_seen = _task_versions(db)
        changed = {r for r, v in now_seen.items() if prepared["seen"].get(r) != v}
        rowids = (set(prepared["rows"]) & set(now_seen)) | _answer_rows(db, tokens, changed)
    else:
        rowids = _answer_rows(db, tokens)
    marked = set()

    def hit(sentence, stated):
        runs = set(consolidate._DIGITS.findall(sentence))
        return any(t in runs for t in stated) and bool(consolidate._name_set(sentence) &
                                                        set().union(*(plan[t][1] for t in stated if t in runs)))
    for part in _chunks(sorted(rowids)):
        for rowid, at, raw in db.execute(f"SELECT rowid, coalesce(finished_at, updated_at, created_at), result FROM "
                                         f"web_tasks WHERE rowid IN ({_in(part)})", part).fetchall():
            try:
                result = json.loads(raw)
            except (TypeError, ValueError):
                continue
            reasoning = result.get("reasoning") if isinstance(result, dict) else None
            if not isinstance(reasoning, dict) or not str(reasoning.get("generated_by") or "").startswith("model:"):
                continue
            stated = [t for t in tokens if plan[t][0] <= (at or 0)]
            if not stated:
                continue
            changed = False
            if isinstance(reasoning.get("text"), str):
                hits = {i for i, s in enumerate(T.sentences(reasoning["text"])) if hit(s, stated)}
                if hits - set(reasoning.get("sourced") or ()):
                    reasoning["sourced"] = sorted(hits | set(reasoning.get("sourced") or ()))
                    changed = True
            for a in reasoning.get("answer") or ():
                if isinstance(a, dict) and isinstance(a.get("text"), str) and not a.get("sourced") and \
                        hit(a["text"], stated):
                    a["sourced"] = True
                    changed = True
            if changed:
                db.execute("UPDATE web_tasks SET result=? WHERE rowid=?", (_dump(result), rowid))
                marked.add(rowid)
    return len(marked)


def _forget_research_core(db, task_id, now, marks=None):
    """Remove one task and what rested only on it. Caller owns the txn. Returns (effects, cache) or None. `marks`:
    prepare_sourced_marks for its claims, made before the transaction (review3b N2)."""
    if db.execute("SELECT 1 FROM web_tasks WHERE id=?", (task_id,)).fetchone() is None:
        return None
    claim_ids = _ids(db, "SELECT DISTINCT id FROM web_claims WHERE task_id=?", (task_id,))
    _mark_sourced_answers(db, claim_ids, marks)
    quoted = _claim_texts(db, claim_ids)
    sources = db.execute("SELECT url,final_url,canonical_url,content_sha256 FROM web_sources WHERE task_id=?",
                         (task_id,)).fetchall()
    deleted = {table: db.execute(f"DELETE FROM {table} WHERE task_id=?", (task_id,)).rowcount
               for table in ("web_events", "web_sources", "web_claims", "web_claim_links", "web_mentions",
                             "web_feedback")}
    if _has_evolution(db):
        _drop_hypotheses(db, _ids(db, "SELECT id FROM web_hypotheses WHERE task_id=?", (task_id,)))
        _drop_node_links(db, ("observation",), [task_id])
        _scrub_observations(db, task_id)
    db.execute("UPDATE web_predictions SET task_id=NULL WHERE task_id=?", (task_id,))
    db.execute("UPDATE web_tasks SET parent_id=NULL WHERE parent_id=?", (task_id,))
    db.execute("UPDATE web_monitors SET last_task_id=NULL WHERE last_task_id=?", (task_id,))
    db.execute("UPDATE web_knowledge SET task_id=NULL WHERE task_id=?", (task_id,))
    _unlink_ledger(db, task_id)
    _scrub_voice_context(db, task_id)
    db.execute("DELETE FROM web_tasks WHERE id=?", (task_id,))
    _web_tombstones(db, "task", [task_id], now)
    effects = _web_cascade(db, claim_ids, now)
    effects["pending_closed"] = sorted(set(effects["pending_closed"]) | set(_forget_task_questions(db, task_id, now)))
    effects["questions_redacted"] = _forget_quoted(db, quoted)
    effects["deleted"] = deleted
    urls, shas = _unreferenced(db, {u for r in sources for u in r[:3] if u}, {r[3] for r in sources if r[3]})
    return effects, {"urls": urls, "sha256": shas}


def _unlink_ledger(db, task_id):
    """R6 D11: the task's daily-budget row (autonomy_ledger) keeps its numbers, so forgetting never refunds a budget,
    but no longer names the task: its key becomes a random 'forgotten:' id and its open and settle times the start of
    its UTC day (the day is all a budget reads). Runs in the live forget and in every checkpoint replay."""
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='autonomy_ledger'").fetchone() is None:
        return
    db.execute("UPDATE autonomy_ledger SET task_id=?, opened_at=CAST(strftime('%s', day) AS REAL), "
               "settled_at=CASE WHEN settled_at IS NULL THEN NULL ELSE CAST(strftime('%s', day) AS REAL) END "
               "WHERE task_id=?", ("forgotten:" + secrets.token_hex(12), task_id))


_VOICE_TASK_KEYS = {"last_task_id": ("last_task_id", "last_objective"), "follow_task_id": ("follow_task_id",
                                                                                          "follow_by")}


def _scrub_voice_context(db, task_id):
    """Every voice session's context that names the task (the last research asked, the one whose answer a session
    awaits: voice/hub.py) or quotes its question loses those keys, so a forgotten question is not carried into the
    next session (review1 S3). Runs inside the forget's transaction and in every checkpoint replay
    (_forget_research_core); the task row is still there to read its question from."""
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='voice_sessions'").fetchone() is None:
        return
    row = db.execute("SELECT objective FROM web_tasks WHERE id=?", (task_id,)).fetchone()
    objective = row[0] if row else None
    rows = db.execute("SELECT id, context FROM voice_sessions WHERE json_valid(context) AND "
                      "(json_extract(context,'$.last_task_id')=? OR json_extract(context,'$.follow_task_id')=? OR "
                      "json_extract(context,'$.last_objective')=?)", (task_id, task_id, objective)).fetchall()
    for sid, text in rows:
        context = json.loads(text)
        doomed = {k for key, keys in _VOICE_TASK_KEYS.items() if context.get(key) == task_id for k in keys}
        if objective is not None and context.get("last_objective") == objective:
            doomed |= set(_VOICE_TASK_KEYS["last_task_id"])
        context = {k: v for k, v in context.items() if k not in doomed}
        db.execute("UPDATE voice_sessions SET context=? WHERE id=?", (json.dumps(context, sort_keys=True), sid))


def _forget_task_questions(db, task_id, now):
    """The task's own questions: its proposal, contested subjects, review aggregate, monitor findings and autonomy
    finding (its text quotes the objective). Their scope named the task, so it becomes 'web:forgotten' (R6 D11;
    deletion_log alone names a forgotten task)."""
    scopes = [f"web:research:{task_id}", f"web:review:{task_id}", f"web:autonomy:{task_id}"]
    scopes += [s for (s,) in db.execute("SELECT DISTINCT scope FROM pending WHERE scope LIKE 'web:finding:%' "
                                        "AND substr(scope, -?) = ?", (len(task_id) + 1, ":" + task_id))]
    closed = _forget_pending(db, scopes, [f"web:contested:{task_id}:"], now)
    for part in _chunks(closed):
        db.execute(f"UPDATE pending SET scope='web:forgotten' WHERE id IN ({_in(part)})", part)
    return closed


def forget_research(db, task_id, now):
    """Forget a research task: its row, events, sources, claims, links, mentions, feedback, questions and the
    knowledge that rested only on it. The objective is kept nowhere (deletion_log names the task id only). Its
    autonomy_ledger row keeps the day's usage numbers under a random id (_unlink_ledger).
    effects["cache"] = {"urls", "sha256"}: the task's public-cache keys no other task references, for the caller
    to drop from the public cache (returned, never persisted)."""
    if not isinstance(task_id, str) or not task_id:
        raise ValidationError("task id must be a non-empty string")
    marks = prepare_sourced_marks(db, _ids(db, "SELECT DISTINCT id FROM web_claims WHERE task_id=?", (task_id,)))
    with store.write(db):
        out = _forget_research_core(db, task_id, now, marks)
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


_OPEN_TASK = ("queued", "discovering", "reading", "evaluating", "synthesizing")


def _refresh_tasks(db, chain):
    """Unfinished refresh tasks of the knowledge rows in `chain`: origin 'refresh-owner:<id>' (the owner's) or
    'refresh:<id>' (a due prediction), and the owner refreshes recorded before those origins existed (origin cli or
    console, fact_verification of the item's statement, parent = the item's task). Their objective is the
    forgotten statement."""
    items = []
    for part in _chunks(sorted(chain)):
        items += db.execute(f"SELECT id, statement, task_id FROM web_knowledge WHERE id IN ({_in(part)})",
                            part).fetchall()
    origins = {f"{prefix}{kid}" for kid, _, _ in items for prefix in ("refresh:", "refresh-owner:")}
    legacy = {(statement, parent) for _, statement, parent in items}
    found = []
    for task_id, origin, mode, objective, parent in db.execute(
            f"SELECT id, origin, mode, objective, parent_id FROM web_tasks WHERE status IN ({_in(_OPEN_TASK)}) "
            "ORDER BY rowid", _OPEN_TASK).fetchall():
        if origin in origins or (origin in ("cli", "console") and mode == "fact_verification"
                                 and (objective, parent) in legacy):
            found.append(task_id)
    return found


def _forget_knowledge_core(db, knowledge_id, now, marks=None):
    """Caller owns the txn. Returns effects or None when absent. Unfinished refresh tasks of the chain (their
    objective is the forgotten statement) are forgotten with it (a worker running one stops at its next fenced
    write); effects["refresh_tasks_forgotten"] lists them and effects["cache"] their unshared public-cache keys."""
    chain = _knowledge_chain(db, knowledge_id)
    if not chain:
        return None
    refreshes, cache = [], {"urls": [], "sha256": []}
    for task_id in _refresh_tasks(db, chain):
        out = _forget_research_core(db, task_id, now, marks)
        if out is not None:
            refreshes.append(task_id)
            cache = {k: sorted(set(cache[k]) | set(out[1].get(k) or [])) for k in cache}
    chain = [kid for kid in chain if db.execute("SELECT 1 FROM web_knowledge WHERE id=?", (kid,)).fetchone()]
    effects = _drop_knowledge(db, chain, now)
    db.execute("DELETE FROM web_entities WHERE id NOT IN (SELECT entity_id FROM web_mentions)")
    effects["refresh_tasks_forgotten"] = refreshes
    if refreshes:
        effects["cache"] = cache
    return effects


def forget_knowledge(db, knowledge_id, now):
    """Forget a knowledge item and every version in its supersession chain (claims stay with their tasks), and the
    queued or running refresh tasks of that chain (C-08: the statement is their objective)."""
    if isinstance(knowledge_id, bool) or not isinstance(knowledge_id, int):
        raise ValidationError("knowledge id must be an integer")
    marks = prepare_sourced_marks(db, [c for task_id in _refresh_tasks(db, _knowledge_chain(db, knowledge_id))
                                       for c in _ids(db, "SELECT DISTINCT id FROM web_claims WHERE task_id=?",
                                                     (task_id,))])
    with store.write(db):
        effects = _forget_knowledge_core(db, knowledge_id, now, marks)
        if effects is None:
            return {"status": "absent", "effects": {}}
        cache = effects.pop("cache", None)
        _web_finish(db, "knowledge", str(knowledge_id), effects, now)
    if cache is not None:
        effects["cache"] = cache
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


def _source_selection(db, target):
    """What forgetting one source URL or every source of a domain reaches (read only)."""
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
    return canonical, domain, rows, source_ids, claim_where, params, urls, needles, sorted({r[0] for r in claim_rows})


def _forget_source_core(db, target, now, marks=None):
    """Forget one source URL or every source of a domain. Caller owns the txn. Returns (effects, cache, label) or
    None when nothing matched. `marks`: prepare_sourced_marks for its claims, made before the transaction (N2)."""
    canonical, domain, rows, source_ids, claim_where, params, urls, needles, claim_ids = _source_selection(db, target)
    if not rows and not claim_ids:
        return None
    _mark_sourced_answers(db, claim_ids, marks)
    quoted = _claim_texts(db, claim_ids)
    db.execute(f"DELETE FROM web_claims WHERE {claim_where}", params)
    for part in _chunks(source_ids):
        db.execute(f"DELETE FROM web_sources WHERE id IN ({_in(part)})", part)
    effects = _web_cascade(db, claim_ids, now)
    effects["questions_redacted"] = _forget_quoted(db, quoted)
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
    marks = prepare_sourced_marks(db, _source_selection(db, url_or_domain)[-1])     # read only, before the lock
    with store.write(db):
        out = _forget_source_core(db, url_or_domain, now, marks)
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
    if any(out.values()):
        _scrub_rule_records(db)
        _premise_cascade(db, {}, now)       # lane K: premises and re-check marks of what the replay removed
    return out


# ---- Voice (migration 9, ADR-037; tombstone kind: migration 11) -------------------------------------------------
# Forgetting a voice session deletes its row, its transcript segments (corrections included) and any retained raw
# audio. Like a web forget it records a payload-free tombstone in web_forgotten (kind 'voice_session'), so
# checkpoints.restore drops the session again from an older archive (replay_voice_forgets). The digest is the one
# software before migration 11 wrote under kind 'task' for the subject "voice:<session id>" (voice_digest): a salted
# hash cannot be recomputed in SQL, so migration 11 relabels those rows without rehashing, and replay also accepts a
# 'task' row with that digest (research task ids are UUIDs, so the namespaces cannot collide). deletion_log keeps
# only the digest label, never the session id or any transcript text. Raw audio swept on expiry leaves no tombstone:
# restore drops every voice_audio row the live instance no longer holds (checkpoints.restore), as it does for epoch
# streams.

VOICE_KIND = "voice_session"
_VOICE_LEGACY_KIND = "task"


def voice_digest(instance_id, session_id):
    """Digest of a forgotten voice session (frozen to the pre-migration-11 form, see above)."""
    return web_digest(instance_id, _VOICE_LEGACY_KIND, f"voice:{session_id}")


def _delete_voice_session(db, session_id):
    """Delete a voice session, its segments and their audio explicitly (a restored copy may not enforce foreign
    keys). Caller owns the txn. Returns (segments, audio rows) deleted."""
    audio = db.execute("DELETE FROM voice_audio WHERE segment_id IN "
                       "(SELECT id FROM voice_segments WHERE session_id=?)", (session_id,)).rowcount
    segments = db.execute("DELETE FROM voice_segments WHERE session_id=?", (session_id,)).rowcount
    db.execute("DELETE FROM voice_sessions WHERE id=?", (session_id,))
    return segments, audio


_VOICE_RESEARCH_INTENTS = ("research", "continue_research")


def _voice_research_tasks(db, session_id):
    """Research tasks this voice session created from its utterances: the tasks its research / continue replies
    name, created while the session ran (a reply that points at an older task did not create it)."""
    started = db.execute("SELECT started_at FROM voice_sessions WHERE id=?", (session_id,)).fetchone()
    if started is None:
        return []
    found = []
    for task_id, detail in db.execute("SELECT task_id, detail FROM voice_segments WHERE session_id=? AND "
                                      "task_id IS NOT NULL ORDER BY seq", (session_id,)).fetchall():
        try:
            intent = json.loads(detail or "{}").get("intent")
        except (ValueError, AttributeError):
            intent = None
        if intent in _VOICE_RESEARCH_INTENTS and task_id not in found and db.execute(
                "SELECT 1 FROM web_tasks WHERE id=? AND created_at>=?", (task_id, started[0])).fetchone():
            found.append(task_id)
    return found


def voice_marks(db, session_id):
    """prepare_sourced_marks for the research tasks a voice session created (read only; before the transaction)."""
    return prepare_sourced_marks(db, [c for task_id in _voice_research_tasks(db, session_id) for c in _ids(
        db, "SELECT DISTINCT id FROM web_claims WHERE task_id=?", (task_id,))])


def forget_voice_session(db, session_id, now, marks=None):
    """Delete one voice session with its transcript and retained audio, and the research tasks it created from
    what was said (their objective is the spoken sentence; forgotten like forget_research, a running one stops at
    its next fenced write: UAT T5-C02), record its tombstone, stale the checkpoints and log the digest label.
    Caller owns the txn (voice/transcripts.forget_session). Returns the effects."""
    research = _voice_research_tasks(db, session_id)
    segments, audio = _delete_voice_session(db, session_id)
    forgotten = [task_id for task_id in research if _forget_research_core(db, task_id, now, marks) is not None]
    digest = voice_digest(store.birth(db)["instance_id"], session_id)
    db.execute("INSERT OR IGNORE INTO web_forgotten(kind,digest,forgotten_at) VALUES(?,?,?)", (VOICE_KIND, digest, now))
    return _web_finish(db, "voice_session", "voice_session:#" + digest,
                       {"segments": segments, "audio": audio, "research_tasks_forgotten": forgotten}, now)


def replay_voice_forgets(db, forgotten, now):
    """Re-apply voice tombstones ({(kind, digest)}, as replay_web_forgets) to a restored copy: kind 'voice_session',
    or a pre-migration-11 'task' row with a voice digest. Caller owns the txn. Returns the number of sessions
    removed again."""
    if not forgotten:
        return 0
    instance_id = store.birth(db)["instance_id"]
    removed = 0
    for (session_id,) in db.execute("SELECT id FROM voice_sessions").fetchall():
        digest = voice_digest(instance_id, session_id)
        if (VOICE_KIND, digest) in forgotten or (_VOICE_LEGACY_KIND, digest) in forgotten:
            _delete_voice_session(db, session_id)
            removed += 1
    return removed
