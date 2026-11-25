"""The imagined store: quarantined content the system produced itself (ADR-049, schema 17; provenance.QUARANTINED).

A record holds one simulation, speculation or inference: a structured payload, its explicit assumptions, a statement of
its uncertainty, what produced it (mechanism and version, and the model id when a model wrote text), the records it was
derived from (by kind and id), the question or goal it served, when it was made and when it expires, and an outcome
slot for what was later observed (imagine -> observe -> compare). It is never evidence:

  - It lives only in the tables `imagined`, `imagined_refs` and `imagined_forgotten`. No knowledge, claim,
    verification, calibration, belief or evidence path reads them (tests/webcortex/test_imagined_quarantine.py checks
    the code), and the database refuses an imagined id in any reference column of those tables (schema._NO_IMAGINED).
  - A record is never rewritten: its kind, payload and lineage are fixed by trigger, so it cannot be relabelled as
    anything else; only the outcome slot is filled, once.
  - Retrieval (knowledge.query, semantic memory, memory.retrieve) reads other tables and never returns one; the
    answer model's evidence bundle never holds one (reasoning.bundle drops quarantined content, check_sentence never
    resolves a citation to it). A caller that wants to show imagined text to a model puts it in section(), a separate
    part labelled NOT_EVIDENCE whose items carry no citable id.
  - Bounded: at most InstanceConfig.max_imagined_rows records (the oldest is evicted first; 0 = nothing may be
    stored), each for at most InstanceConfig.imagined_lifetime_seconds (governance.sweep expires them).
  - Forgetting covers it: a record whose source is forgotten (a task, a knowledge item, a claim, a source, an event, an
    epoch, a camera or partner root, a dataset, a graph, another imagined record) is removed in the same transaction
    (purge_orphans, called by every forget), and every removal leaves a payload-free tombstone that a restore replays.
  - A checkpoint is a copy of the whole file, so archives and restores carry the store with its kinds; a restore then
    removes what the live file removed since (replay).

Nothing writes to the store by default: no mechanism that generates imagined content exists yet. example() is a tiny
deterministic generator for tests and measurements; it writes nothing itself.
"""
import hashlib
import json
import secrets

from .. import provenance
from . import store
from .errors import NotFound, ValidationError

KINDS = (provenance.SIMULATION, provenance.SPECULATION, provenance.INFERENCE)
NOT_EVIDENCE = provenance.NOT_EVIDENCE
REF_KINDS = ("task", "knowledge", "claim", "source", "hypothesis", "prediction", "evidence", "consolidated", "epoch",
             "camera", "partner", "dataset", "graph", "imagined")
# Evidence a later observation may name (observe): never another imagined record.
OBSERVATION_KINDS = tuple(k for k in REF_KINDS if k != "imagined")
MAX_REFS = 64
DELETION_KIND = "imagined"       # deletion_log.target_kind of an owner's purge

# For each kind of source, "this reference no longer resolves" as SQL over the imagined_refs row r and its record i.
# An epoch row outlives its forget, so the deletion log decides: an epoch forgotten after the record was made.
_GONE = {
    "task": "NOT EXISTS (SELECT 1 FROM web_tasks t WHERE t.id=r.ref_id)",
    "knowledge": "NOT EXISTS (SELECT 1 FROM web_knowledge k WHERE k.id=CAST(r.ref_id AS INTEGER))",
    "claim": "NOT EXISTS (SELECT 1 FROM web_claims c WHERE c.id=r.ref_id)",
    "source": "NOT EXISTS (SELECT 1 FROM web_sources s WHERE s.id=CAST(r.ref_id AS INTEGER))",
    "hypothesis": "NOT EXISTS (SELECT 1 FROM web_hypotheses h WHERE h.id=CAST(r.ref_id AS INTEGER))",
    "prediction": "NOT EXISTS (SELECT 1 FROM web_predictions p WHERE p.id=CAST(r.ref_id AS INTEGER))",
    "evidence": "NOT EXISTS (SELECT 1 FROM evidence e WHERE e.id=r.ref_id)",
    "consolidated": "NOT EXISTS (SELECT 1 FROM evidence_consolidated c WHERE c.seq=CAST(r.ref_id AS INTEGER))",
    "epoch": "(NOT EXISTS (SELECT 1 FROM epochs e WHERE e.name=r.ref_id) OR EXISTS (SELECT 1 FROM deletion_log d "
             "WHERE d.target_kind='epoch' AND d.target_id=r.ref_id AND d.requested_at>=i.created_at))",
    "camera": "(NOT EXISTS (SELECT 1 FROM evidence e WHERE e.channel='camera' AND e.root=r.ref_id) AND NOT EXISTS "
              "(SELECT 1 FROM evidence_consolidated c WHERE c.channel='camera' AND c.root=r.ref_id))",
    "partner": "(NOT EXISTS (SELECT 1 FROM evidence e WHERE e.channel='word' AND e.root=r.ref_id) AND NOT EXISTS "
               "(SELECT 1 FROM evidence_consolidated c WHERE c.channel='word' AND c.root=r.ref_id))",
    "dataset": "NOT EXISTS (SELECT 1 FROM datasets d WHERE d.id=CAST(r.ref_id AS INTEGER) AND d.deleted_at IS NULL)",
    "graph": "NOT EXISTS (SELECT 1 FROM graph_revisions g WHERE g.id=CAST(r.ref_id AS INTEGER) AND g.blob!=X'')",
    "imagined": "NOT EXISTS (SELECT 1 FROM imagined j WHERE j.id=r.ref_id)",
}
COLUMNS = ("id", "kind", "payload", "assumptions", "uncertainty", "mechanism", "mechanism_version", "model",
           "question", "created_at", "expires_at", "outcome", "observed_at")


def present(db):
    """Whether this file has the imagined store (schema 17)."""
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='imagined'").fetchone() is not None


def digest(db, record_id, instance_id=None):
    """The payload-free tombstone of a removed record (instance-salted, as governance.root_digest)."""
    salt = store.birth(db)["instance_id"] if instance_id is None else instance_id
    return hashlib.sha256(f"{salt}:{record_id}".encode()).hexdigest()[:16]


def _text(name, value, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValidationError(f"{name} must be a non-empty string of at most {limit} characters")
    return value


def _ref(item, kinds=REF_KINDS):
    if not isinstance(item, (list, tuple)) or len(item) != 2:
        raise ValidationError("each reference is a pair (kind, id)")
    kind, ref_id = item
    if kind not in kinds:
        raise ValidationError(f"reference kind must be one of {', '.join(kinds)}")
    if isinstance(ref_id, bool) or not isinstance(ref_id, (str, int)) or not 1 <= len(str(ref_id)) <= 128:
        raise ValidationError("reference id must be a string or an integer")
    return kind, str(ref_id)


def _unresolved(db, refs, created_at):
    """The references of `refs` that name nothing this file holds (as purge_orphans would judge them)."""
    missing = []
    for kind, ref_id in refs:
        row = db.execute(f"SELECT {_GONE[kind]} FROM (SELECT ? AS ref_id) r, (SELECT ? AS created_at) i",
                         (ref_id, created_at)).fetchone()
        if row[0]:
            missing.append((kind, ref_id))
    return missing


def _stale_checkpoints(db, since):
    """Mark valid checkpoints written at or after `since` (they may hold a removed record) stale. Caller owns the
    transaction."""
    stale = [i for (i,) in db.execute("SELECT id FROM checkpoints WHERE status='valid' AND created_at>=? ORDER BY id",
                                      (since,))]
    db.executemany("UPDATE checkpoints SET status='stale' WHERE id=?", [(i,) for i in stale])
    return stale


def _remove(db, where, params, reason, now):
    """Delete the records matching `where`, their references, and leave a tombstone each. Caller owns the txn.
    Returns (ids removed, earliest created_at or None)."""
    rows = db.execute(f"SELECT id, created_at FROM imagined WHERE {where}", params).fetchall()
    if not rows:
        return [], None
    ids, salt = [r[0] for r in rows], store.birth(db)["instance_id"]
    db.executemany("DELETE FROM imagined_refs WHERE record_id=?", [(i,) for i in ids])   # a copy may lack FKs
    db.executemany("DELETE FROM imagined WHERE id=?", [(i,) for i in ids])
    db.executemany("INSERT OR IGNORE INTO imagined_forgotten(digest,reason,forgotten_at) VALUES(?,?,?)",
                   [(digest(db, i, salt), reason, now) for i in ids])
    return sorted(ids), min(r[1] for r in rows)


def _log(db, target, removed, stale, now):
    """The deletion_log row of a removal that is not part of a forget (purge, expiry, eviction). Caller owns the txn.
    Returns the effects."""
    effects = {"imagined_removed": removed, "checkpoints_stale": stale, "state_version": store.bump_state_version(db)}
    db.execute("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects) VALUES(?,?,?,?)",
               (DELETION_KIND, target, now, json.dumps(effects, sort_keys=True)))
    return effects


_ORPHANS = ("id IN (SELECT r.record_id FROM imagined_refs r JOIN imagined i ON i.id=r.record_id WHERE " +
            " OR ".join(f"(r.ref_kind='{kind}' AND {test})" for kind, test in _GONE.items()) + ")")


def purge_orphans(db, now):
    """Remove every record one of whose sources is gone (forgotten, expired, erased), and the records derived from
    those, to a fixed point. Caller owns the transaction: every forget calls it in its own, a restore in its replay.
    Checkpoints are not touched here: a record is made after what it derives from, so every archive that holds it
    holds that source too, and the forget of the source already made the archive stale. Returns the number removed
    (0, after one indexed read, for an empty store or a file without one)."""
    if not present(db) or db.execute("SELECT 1 FROM imagined_refs LIMIT 1").fetchone() is None:
        return 0
    removed = 0
    while True:
        ids, _ = _remove(db, _ORPHANS, (), "forgotten", now)
        if not ids:
            return removed
        removed += len(ids)


def expire(db, now):
    """Remove records whose lifetime ended, and what derives from them; checkpoints written since the oldest of them
    become stale and one deletion_log row (kind 'imagined', target 'expired') says so. Caller owns the transaction
    (governance.sweep). Returns how many were removed."""
    if not present(db):
        return 0
    ids, earliest = _remove(db, "expires_at<=?", (now,), "expired", now)
    if not ids:
        return 0
    removed = len(ids) + purge_orphans(db, now)
    _log(db, "expired", removed, _stale_checkpoints(db, earliest), now)
    return removed


def record(db, kind, *, payload, assumptions, uncertainty, mechanism, mechanism_version, question, now,
           derived_from=(), model=None, lifetime=None):
    """Store one imagined record and return its id ('imagined:' + 32 hex). Every reference in `derived_from`
    ((kind, id) pairs, kind in REF_KINDS) must name a record this file holds. `lifetime` (seconds) may only shorten
    InstanceConfig.imagined_lifetime_seconds. At max_imagined_rows the oldest records are evicted first; with 0 the
    store takes nothing (ValidationError). Writes are refused while regulation pauses them."""
    if kind not in KINDS:
        raise ValidationError(f"an imagined record is one of {', '.join(KINDS)}; evidence and predictions have their "
                              "own tables")
    try:
        payload_text = json.dumps(payload, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        raise ValidationError("payload must be JSON") from None
    if len(payload_text) > 65536:
        raise ValidationError("payload must be at most 64 KiB of JSON")
    if not isinstance(assumptions, (list, tuple)) or not all(isinstance(a, str) and a.strip() for a in assumptions):
        raise ValidationError("assumptions must be a list of statements (an empty list says there are none)")
    _text("uncertainty", uncertainty, 2000)
    _text("mechanism", mechanism, 64)
    _text("mechanism_version", mechanism_version, 64)
    _text("question", question, 2000)
    if model is not None:
        _text("model", model, 200)
    if not isinstance(derived_from, (list, tuple)) or len(derived_from) > MAX_REFS:
        raise ValidationError(f"derived_from must be a list of at most {MAX_REFS} (kind, id) pairs")
    refs = list(dict.fromkeys(_ref(r) for r in derived_from))
    cfg = store.config(db)
    if cfg.max_imagined_rows == 0:
        raise ValidationError("the imagined store takes nothing (max_imagined_rows is 0: `nsf config "
                              "--max-imagined-rows N` sets a limit)")
    life = cfg.imagined_lifetime_seconds
    if lifetime is not None:
        if isinstance(lifetime, bool) or not isinstance(lifetime, (int, float)) or not 0 < lifetime:
            raise ValidationError("lifetime must be a positive number of seconds")
        life = min(life, lifetime)
    store.require_write_allowed(db)
    record_id = "imagined:" + secrets.token_hex(16)
    with store.write(db):
        missing = _unresolved(db, refs, now)
        if missing:
            raise ValidationError(f"derived_from names records this instance does not hold: {missing}")
        held = db.execute("SELECT count(*) FROM imagined").fetchone()[0]
        evicting = held >= cfg.max_imagined_rows
        if evicting:                                 # _log bumps the state version for this transaction
            evicted, earliest = _remove(db, "id IN (SELECT id FROM imagined ORDER BY created_at, id LIMIT ?)",
                                        (held - cfg.max_imagined_rows + 1,), "evicted", now)
            removed = len(evicted) + purge_orphans(db, now)
            _log(db, "evicted", removed, _stale_checkpoints(db, earliest), now)
            missing = _unresolved(db, refs, now)     # an evicted record may have been one of this one's sources
            if missing:
                raise ValidationError(f"derived_from names records the limit just evicted: {missing}")
        db.execute(f"INSERT INTO imagined({','.join(COLUMNS[:11])}) VALUES({','.join('?' * 11)})",
                   (record_id, kind, payload_text, json.dumps(list(assumptions)), uncertainty, mechanism,
                    mechanism_version, model, question, now, now + life))
        db.executemany("INSERT INTO imagined_refs(record_id,ref_kind,ref_id) VALUES(?,?,?)",
                       [(record_id, k, i) for k, i in refs])
        if not evicting:
            store.bump_state_version(db)
    return record_id


def observe(db, record_id, *, observation, now, evidence=()):
    """Fill a record's outcome slot with what was later observed, once: {"observed": observation, "evidence": [[kind,
    id], ...]}. The evidence named must be held by this file and is never another imagined record. Comparing the two
    is the caller's (a mechanism's) business; this store keeps both side by side and never turns either into the
    other."""
    refs = list(dict.fromkeys(_ref(r, OBSERVATION_KINDS) for r in evidence))
    try:
        outcome = json.dumps({"observed": observation, "evidence": [list(r) for r in refs]}, sort_keys=True,
                             allow_nan=False)
    except (TypeError, ValueError):
        raise ValidationError("observation must be JSON") from None
    with store.write(db):
        row = db.execute("SELECT outcome FROM imagined WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise NotFound(f"Unknown imagined record {record_id}")
        missing = _unresolved(db, refs, now)
        if missing:
            raise ValidationError(f"evidence names records this instance does not hold: {missing}")
        db.execute("UPDATE imagined SET outcome=?, observed_at=? WHERE id=?", (outcome, now, record_id))
        store.bump_state_version(db)
    return show(db, record_id)


def _refs(db, record_id):
    return [[k, i] for k, i in db.execute("SELECT ref_kind, ref_id FROM imagined_refs WHERE record_id=? "
                                          "ORDER BY ref_kind, ref_id", (record_id,))]


def _item(db, row, *, full):
    item = dict(zip(COLUMNS, row))
    out = {"id": item["id"], "kind": item["kind"], "label": NOT_EVIDENCE, "mechanism": item["mechanism"],
           "mechanism_version": item["mechanism_version"], "model": item["model"], "question": item["question"],
           "created_at": item["created_at"], "expires_at": item["expires_at"],
           "observed": item["observed_at"] is not None}
    if full:
        out |= {"payload": json.loads(item["payload"]), "assumptions": json.loads(item["assumptions"]),
                "uncertainty": item["uncertainty"], "derived_from": _refs(db, item["id"]),
                "outcome": None if item["outcome"] is None else json.loads(item["outcome"]),
                "observed_at": item["observed_at"]}
    return out


def listing(db, now, *, kind=None, limit=100):
    """Records still within their lifetime, newest first, each with its kind and NOT_EVIDENCE. Read-only."""
    if kind is not None and kind not in KINDS:
        raise ValidationError(f"kind must be one of {', '.join(KINDS)}")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValidationError("limit must be an integer from 1 to 1000")
    if not present(db):
        return {"label": NOT_EVIDENCE, "counts": {k: 0 for k in KINDS}, "items": []}
    where, params = "expires_at>?", [now]
    if kind is not None:
        where, params = where + " AND kind=?", params + [kind]
    rows = db.execute(f"SELECT {','.join(COLUMNS)} FROM imagined WHERE {where} ORDER BY created_at DESC, id LIMIT ?",
                      (*params, limit)).fetchall()
    counts = dict(db.execute("SELECT kind, count(*) FROM imagined WHERE expires_at>? GROUP BY kind", (now,)))
    return {"label": NOT_EVIDENCE, "counts": {k: counts.get(k, 0) for k in KINDS},
            "items": [_item(db, r, full=False) for r in rows]}


def show(db, record_id):
    """One record in full (payload, assumptions, uncertainty, lineage, outcome), labelled NOT_EVIDENCE."""
    row = db.execute(f"SELECT {','.join(COLUMNS)} FROM imagined WHERE id=?", (record_id,)).fetchone() \
        if present(db) else None
    if row is None:
        raise NotFound(f"Unknown imagined record {record_id}")
    return _item(db, row, full=True)


def purge(db, now, *, record_id=None, kind=None):
    """The owner removes one record (record_id), every record of a kind, or all of them (neither given): rows and
    lineage deleted, tombstones kept (a restore removes them from older copies), checkpoints that may hold them made
    stale, one deletion_log row (kind 'imagined', target the id, the kind or 'all'). Records derived from a removed
    one go too."""
    if record_id is not None and kind is not None:
        raise ValidationError("purge one record or one kind, not both")
    if kind is not None and kind not in KINDS:
        raise ValidationError(f"kind must be one of {', '.join(KINDS)}")
    where, params, target = "1", (), "all"
    if record_id is not None:
        where, params, target = "id=?", (str(record_id),), str(record_id)
    elif kind is not None:
        where, params, target = "kind=?", (kind,), kind
    with store.write(db):
        if not present(db):
            return {"status": "absent", "effects": {}}
        ids, earliest = _remove(db, where, params, "purged", now)
        if not ids:
            return {"status": "absent", "effects": {}}
        effects = _log(db, target, len(ids) + purge_orphans(db, now), _stale_checkpoints(db, earliest), now)
    return {"status": "purged", "effects": effects | {"wal_checkpoint": store.truncate_wal(db)}}


def replay(copy, forgotten, now):
    """In a restore's or rewrite's copy: remove every record the live file removed since the archive was written
    (`forgotten`: the live imagined_forgotten rows), keep their tombstones, expire what is past its lifetime, and drop
    what derives from anything the copy no longer holds. Caller owns the transaction. Returns how many."""
    if not present(copy):
        return 0
    copy.executemany("INSERT OR IGNORE INTO imagined_forgotten(digest,reason,forgotten_at) VALUES(?,?,?)", forgotten)
    gone, salt = {d for d, _, _ in forgotten}, store.birth(copy)["instance_id"]
    doomed = [i for (i,) in copy.execute("SELECT id FROM imagined") if digest(copy, i, salt) in gone]
    removed = 0
    for part in range(0, len(doomed), 300):
        chunk = doomed[part:part + 300]
        marks = ",".join("?" * len(chunk))
        copy.execute(f"DELETE FROM imagined_refs WHERE record_id IN ({marks})", chunk)
        removed += copy.execute(f"DELETE FROM imagined WHERE id IN ({marks})", chunk).rowcount
    return removed + expire(copy, now) + purge_orphans(copy, now)


def section(records):
    """Imagined records as a separate, labelled part of what a model may be shown: {"label": NOT_EVIDENCE, "items":
    [{"kind", "payload", "assumptions", "uncertainty"}]}. The items carry no id: an answer cannot cite them, and the
    guard resolves citations only against the evidence claims and sources (reasoning.check_sentence)."""
    return {"label": NOT_EVIDENCE,
            "items": [{"kind": r["kind"], "payload": r.get("payload"), "assumptions": r.get("assumptions") or [],
                       "uncertainty": r.get("uncertainty")} for r in records]}


def example(seed, *, question="what if the next reading differs?", derived_from=()):
    """A tiny deterministic generator for tests and measurements: the arguments of one record() call (kind, payload,
    assumptions, uncertainty, mechanism, version, question, derived_from) from an integer seed. Writes nothing."""
    kind = KINDS[seed % len(KINDS)]
    value = (seed * 7919) % 1000
    return {"kind": kind, "payload": {"scenario": f"test scenario {seed}", "value": value},
            "assumptions": [f"assumption {seed}: the test world holds still"],
            "uncertainty": f"made up for a test (seed {seed}); no evidence behind it",
            "mechanism": "test-generator", "mechanism_version": "1", "question": question,
            "derived_from": list(derived_from)}
