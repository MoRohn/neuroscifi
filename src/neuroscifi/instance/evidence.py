"""The evidence journal: calibration epochs and idempotent, validated observations.

Each observation is one corrected binary reading (bit XOR action) from a camera root, a word partner
or a reference root. Ingest is idempotent on the event id: the same payload twice is a duplicate,
a different payload is a Conflict, and a forgotten or expired id is Gone forever.
"""
import hashlib
import math

from . import beliefs, datasets, store
from .errors import Conflict, Gone, ValidationError

CHANNELS = ("camera", "word", "reference")
OPTIONAL = {"sample_id": None, "actor": "owner", "consent": "learn", "provenance": "observed", "acquired_at": None}
REQUIRED = ("id", "epoch", "channel", "action", "bit", "root")
# Columns that define an event's identity for the duplicate/Conflict check.
PAYLOAD = ("epoch", "channel", "action", "bit", "root", "sample_id", "actor", "consent", "provenance", "acquired_at")
_TEXT_LIMITS = {"id": 128, "epoch": 64, "root": 128, "sample_id": 128, "actor": 128}
_CHOICES = {"channel": CHANNELS, "consent": ("learn", "memory_only"), "provenance": ("observed", "simulated")}


def _text(name, value, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValidationError(f"{name} must be a non-blank string of at most {limit} characters")
    return value


def normalize(obs):
    """Validate an observation dict strictly and return it with defaults filled in."""
    if not isinstance(obs, dict):
        raise ValidationError("observation must be a JSON object")
    unknown = sorted(set(obs) - set(REQUIRED) - set(OPTIONAL))
    if unknown:
        raise ValidationError("unknown observation field(s): " + ", ".join(map(str, unknown)))
    missing = [key for key in REQUIRED if key not in obs]
    if missing:
        raise ValidationError("missing observation field(s): " + ", ".join(missing))
    record = {**OPTIONAL, **obs}
    for name, limit in _TEXT_LIMITS.items():
        if record[name] is not None or name in REQUIRED or name == "actor":
            _text(name, record[name], limit)
    for name, choices in _CHOICES.items():
        if record[name] not in choices:
            raise ValidationError(f"{name} must be one of {', '.join(choices)}")
    for name in ("action", "bit"):
        if isinstance(record[name], bool) or record[name] not in (0, 1) or not isinstance(record[name], int):
            raise ValidationError(f"{name} must be the integer 0 or 1")
    acquired = record["acquired_at"]
    if acquired is not None:
        if isinstance(acquired, bool) or not isinstance(acquired, (int, float)) or not math.isfinite(acquired):
            raise ValidationError("acquired_at must be a finite number of Unix seconds")
        record["acquired_at"] = float(acquired)
    return record


def start_epoch(db, name, now, note=None, context=None):
    """Create a calibration epoch. Idempotent: an existing name returns created=False.

    context: the graph context of the epoch's panel (epoch_contexts); given for an existing epoch it replaces the
    association. With a context the result also carries "context". WritesPaused while instance regulation pauses
    writes (free space of the instance's own file included).
    """
    _text("epoch name", name, 64)
    if note is not None and (not isinstance(note, str) or len(note) > 1024):
        raise ValidationError("note must be a string of at most 1024 characters")
    if context is not None:
        datasets._check_context(context)
    store.require_write_allowed(db)
    with store.write(db):
        created = db.execute("INSERT OR IGNORE INTO epochs(name,created_at,note) VALUES(?,?,?)",
                             (name, now, note)).rowcount == 1
        changed = context is not None and _associate(db, name, context, now)
        if created or changed:
            store.bump_state_version(db)
    return {"epoch": name, "created": created} | ({"context": context} if context is not None else {})


def _associate(db, epoch, context, now):
    """Upsert the epoch's graph context. Caller owns the transaction. Returns True when it changed."""
    if beliefs.epoch_context(db, epoch) == context:
        return False
    db.execute("INSERT INTO epoch_contexts(epoch,context,created_at) VALUES(?,?,?) "
               "ON CONFLICT(epoch) DO UPDATE SET context=excluded.context, created_at=excluded.created_at",
               (epoch, context, now))
    return True


def set_epoch_context(db, epoch, context, now):
    """Declare (or replace) the graph context of an existing epoch's panel. Returns {"epoch", "context", "changed"}.

    Errors: NotFound (unknown epoch), ValidationError (context not a nonblank name of at most 64 characters).
    """
    datasets._check_context(context)
    with store.write(db):
        beliefs.require_epoch(db, epoch)
        changed = _associate(db, epoch, context, now)
        if changed:
            store.bump_state_version(db)
    return {"epoch": epoch, "context": context, "changed": changed}


def attach_stream(db, epoch, states, targets, now, *, provenance="observed", path=None):
    """Attach a short population stream (states[T,N], targets[T,N]) observed in an epoch.

    Validated like datasets.add (numeric, finite, 2-D, same shape, 8..200000 steps, 1..1024 populations) and stored
    as an npz blob read back with allow_pickle=False. Regulation applies (WritesPaused); it expires after the
    evidence TTL and is forgotten with its epoch. It is evidence about which panel this is (beliefs.match_epochs
    scores the most recent one) and is never used for training.
    Returns {"id", "epoch", "rows", "cols", "sha256", "provenance", "created_at", "expires_at"}.
    """
    if provenance not in datasets.PROVENANCES:
        raise ValidationError(f"provenance must be one of {', '.join(datasets.PROVENANCES)}")
    states, targets = datasets._check_arrays(states, targets)
    blob = datasets._encode(states, targets)
    digest = hashlib.sha256(blob).hexdigest()
    rows, cols = states.shape
    with store.write(db):
        beliefs.require_epoch(db, epoch)
        store.require_write_allowed(db, path)
        expires = now + store.config(db).evidence_ttl_seconds
        stream_id = db.execute(
            "INSERT INTO epoch_streams(epoch,blob,rows,cols,sha256,provenance,created_at,expires_at) "
            "VALUES(?,?,?,?,?,?,?,?)", (epoch, blob, rows, cols, digest, provenance, now, expires)).lastrowid
        store.bump_state_version(db)
    return {"id": stream_id, "epoch": epoch, "rows": rows, "cols": cols, "sha256": digest, "provenance": provenance,
            "created_at": now, "expires_at": expires}


def observe(db, obs, now, *, path=None):
    """Journal one observation and record a belief revision if the MAP beliefs changed.

    Errors: ValidationError (bad or unknown fields), NotFound (unknown epoch), Gone (tombstoned id),
    Conflict (id bound to another payload), WritesPaused (regulation, checked before insert).
    A duplicate bumps repeat_views only; it never extends expiry or changes the state version.
    """
    record = normalize(obs)
    with store.write(db):
        beliefs.ensure_tables(db)
        beliefs.require_epoch(db, record["epoch"])
        if db.execute("SELECT 1 FROM tombstones WHERE id=?", (record["id"],)).fetchone():
            raise Gone(f"Evidence {record['id']!r} was forgotten or expired and cannot be recorded again")
        row = db.execute(f"SELECT seq,{','.join(PAYLOAD)} FROM evidence WHERE id=?", (record["id"],)).fetchone()
        if row is not None:
            if tuple(row[1:]) != tuple(record[key] for key in PAYLOAD):
                raise Conflict(f"Evidence id {record['id']!r} is already bound to a different observation")
            store.bump(db, "repeat_views")
            return {"status": "duplicate", "seq": row[0], "state_version": store.state_version(db),
                    "belief_revision": None}
        store.require_write_allowed(db, path)
        ttl = store.config(db).evidence_ttl_seconds
        seq = db.execute(
            f"INSERT INTO evidence(id,{','.join(PAYLOAD)},recorded_at,retention,expires_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'standard',?)",
            (record["id"], *(record[key] for key in PAYLOAD), now, now + ttl)).lastrowid
        store.bump(db, "evidence_written")
        store.bump(db, f"encoder_exposure:{record['channel']}")
        version = store.bump_state_version(db)
        revision = beliefs.record_revision(db, record["epoch"], now, f"evidence:{record['id']}")
    return {"status": "created", "seq": seq, "state_version": version, "belief_revision": revision}


def list_evidence(db, epoch, *, channel=None, root=None, limit=500):
    """The latest `limit` events of an epoch (optionally one channel and/or root), oldest first."""
    beliefs.require_epoch(db, epoch)
    if channel is not None and channel not in CHANNELS:
        raise ValidationError(f"channel must be one of {', '.join(CHANNELS)}")
    if root is not None:
        _text("root", root, 128)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10_000:
        raise ValidationError("limit must be an integer between 1 and 10000")
    where, params = ["epoch=?"], [epoch]
    for column, value in (("channel", channel), ("root", root)):
        if value is not None:
            where.append(f"{column}=?")
            params.append(value)
    rows = db.execute(f"SELECT {','.join(beliefs.EVENT_COLUMNS)} FROM evidence WHERE {' AND '.join(where)} "
                      "ORDER BY seq DESC LIMIT ?", (*params, limit)).fetchall()
    return [beliefs.event_dict(row) for row in reversed(rows)]
