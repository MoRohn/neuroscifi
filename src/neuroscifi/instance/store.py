"""Open, create and describe one persistent individual (one SQLite file).

Contract shared by every instance module:
- `connect(path)` returns a connection with foreign keys, WAL, secure_delete and a busy timeout.
- Writers wrap mutations in `with write(db):` (BEGIN IMMEDIATE ... COMMIT) and call
  `bump_state_version(db)` exactly once per committed logical change.
- Time is always passed in explicitly (`now`, seconds since the epoch) so tests control the clock.
- Configuration and the birth record are written once at creation and read through `config(db)`
  and `birth(db)`.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
import glob
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
import uuid

from .. import __version__
from .errors import NotFound, ValidationError, WritesPaused
from .schema import SCHEMA_VERSION, migrate

THERMAL_ZONES = "/sys/class/thermal/thermal_zone*/temp"  # millidegrees Celsius, one file per zone
DEFAULT_DB_ENV = "NSF_DB"
DEFAULT_DB = "neuroscifi.sqlite"
GATE_V1 = {
    "version": "gate-v1",
    "validation_tolerance": 0.02,
    "pack_candidates": {"degrees": [2, 3, 4, 6, 12], "bits": [4, 8]},
    "min_heldout_gain": 0.0,
    "retention_tolerance": 0.10,
    "byte_budget": 4096,
    "unknown_context_ratio": 3.0,
    "recall_history": 12,
}
PRIORS = {
    "state_prior": "uniform over mechanism, per-camera-root alignment and per-partner convention bits",
    "camera_reading_error": 0.10,
    "word_reading_error": 0.10,
    "reference_root_flip_probability": 0.15,
    "reference_reading_error": 0.10,
}


@dataclass(frozen=True)
class InstanceConfig:
    evidence_ttl_seconds: int = 7 * 86400   # short default lifetime for unpromoted evidence
    max_evidence_rows: int = 1_000_000      # regulation: pause new writes beyond this
    min_free_bytes: int = 64 * 1024 * 1024  # regulation: pause new writes below this free space
    ask_budget_per_epoch: int = 3           # executive interruption budget
    max_thermal_celsius: float | None = None  # regulation: pause new writes above this temperature (None = off)

    @classmethod
    def from_json(cls, text):
        raw = json.loads(text)
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})


def default_db_path():
    return Path(os.environ.get(DEFAULT_DB_ENV, DEFAULT_DB))


def add_db_argument(parser):
    parser.add_argument("--db", type=Path, default=None,
                        help=f"instance database (default ${DEFAULT_DB_ENV} or ./{DEFAULT_DB})")


def resolve_db(args):
    return args.db if args.db is not None else default_db_path()


def connect(path):
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA journal_size_limit=0")  # a reset WAL is truncated, so old frames do not linger on disk
    db.execute("PRAGMA secure_delete=ON")
    db.execute("PRAGMA recursive_triggers=ON")  # REPLACE's implicit delete fires delete triggers
    db.execute("PRAGMA busy_timeout=10000")
    return db


def truncate_wal(db, *, attempts=5, pause=0.05):
    """Copy the WAL into the database and truncate it, so removed payloads leave no frames behind.

    Call after a committed destructive operation. Returns "ok", or "busy" when a reader kept the
    checkpoint from completing after `attempts` tries (the next successful checkpoint finishes the job).
    """
    db.execute("PRAGMA busy_timeout=100")
    try:
        for attempt in range(attempts):
            if attempt:
                time.sleep(pause)
            try:
                busy = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
            except sqlite3.OperationalError:
                busy = 1
            if not busy:
                return "ok"
        return "busy"
    finally:
        db.execute("PRAGMA busy_timeout=10000")


@contextmanager
def write(db):
    """Serialize a mutation. Nested use is not supported."""
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
    except BaseException:
        db.execute("ROLLBACK")
        raise
    try:
        db.execute("COMMIT")
    except BaseException:
        # A failed COMMIT (deferred constraint, I/O error, busy) can leave the transaction open.
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise


def bump(db, name, amount=1):
    db.execute("INSERT INTO counters(name,value) VALUES(?,?) "
               "ON CONFLICT(name) DO UPDATE SET value=value+excluded.value", (name, amount))


def counter(db, name):
    row = db.execute("SELECT value FROM counters WHERE name=?", (name,)).fetchone()
    return row[0] if row else 0


def bump_state_version(db):
    bump(db, "state_version")
    return counter(db, "state_version")


def state_version(db):
    return counter(db, "state_version")


def _meta(db, key):
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if row is None:
        raise NotFound(f"Instance metadata '{key}' is missing; run `nsf init` first")
    return row[0]


def config(db):
    return InstanceConfig.from_json(_meta(db, "config"))


def birth(db):
    return json.loads(_meta(db, "birth"))


def create(path, now, *, action_semantics=None, config=None, founding_graph=None, note=None):
    """Create a new individual. Refuses an existing file so an individual is never overwritten."""
    path = Path(path)
    if path.exists():
        raise ValidationError(f"{path} already exists; each file is one individual")
    config = config or InstanceConfig()
    semantics = action_semantics or {
        "0": "commanded direction A, as declared by the owner",
        "1": "commanded direction B, as declared by the owner",
    }
    if set(semantics) != {"0", "1"} or not all(isinstance(v, str) and v.strip() for v in semantics.values()):
        raise ValidationError("Action semantics must describe actions '0' and '1'")
    old_umask = os.umask(0o077)
    try:
        db = connect(path)
    finally:
        os.umask(old_umask)
    try:
        migrate(db)
        record = {
            "instance_id": str(uuid.uuid4()),
            "created_at": now,
            "software_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "priors": PRIORS,
            "action_semantics": semantics,
            "encoders": [
                {"name": "manual-bit-encoding", "channels": ["camera", "word", "reference"],
                 "externally_trained": False,
                 "provenance": "owner-declared binary encoding of commanded and observed directions"},
            ],
            "initial_graph": None,
            "note": note,
        }
        with write(db):
            db.execute("INSERT INTO meta VALUES('config',?)", (json.dumps(asdict(config), sort_keys=True),))
            db.execute("INSERT INTO meta VALUES('birth',?)", (json.dumps(record, sort_keys=True),))
            from . import rules  # registered here so every individual starts with an immutable exam
            rules.register(db, GATE_V1, now, created_by="birth")
            bump_state_version(db)
        if founding_graph is not None:
            from . import graphs
            revision = graphs.register_founding(db, founding_graph, now)
            record["initial_graph"] = {"revision_id": revision, "context": "founding"}
            with write(db):
                db.execute("UPDATE meta SET value=? WHERE key='birth'", (json.dumps(record, sort_keys=True),))
    except BaseException:
        db.close()
        path.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
        raise
    return db


def open_existing(path):
    path = Path(path)
    if not path.is_file():
        raise NotFound(f"No instance at {path}; create one with `nsf init --db {path}`")
    db = connect(path)
    migrate(db)
    birth(db)  # fail early on a non-instance database
    return db


def _hottest_zone():
    """Highest readable thermal zone temperature in Celsius, or None when no zone is readable."""
    readings = []
    for name in glob.glob(THERMAL_ZONES):
        try:
            readings.append(int(Path(name).read_text().strip()) / 1000)
        except (OSError, ValueError):
            continue
    return max(readings) if readings else None


def write_allowed(db, path=None):
    """Regulation: return (allowed, reason). Reads stay available while writes are paused.

    Pauses on journal size, on free space below the reserve and, when config.max_thermal_celsius is set, on any
    thermal zone above that temperature. The free-space check uses `path`, else the connection's own file (PRAGMA
    database_list), so every caller is covered whether or not it knows the path; an in-memory database has no
    free-space check.
    """
    cfg = config(db)
    if path is None:
        path = database_path(db)
    rows = db.execute("SELECT count(*) FROM evidence").fetchone()[0]
    if rows >= cfg.max_evidence_rows:
        return False, f"evidence journal holds {rows} rows (limit {cfg.max_evidence_rows})"
    if path is not None:
        free = shutil.disk_usage(Path(path).resolve().parent).free
        if free < cfg.min_free_bytes:
            return False, f"{free} free bytes is below the {cfg.min_free_bytes}-byte reserve"
    if cfg.max_thermal_celsius is not None:
        hottest = _hottest_zone()
        if hottest is not None and hottest > cfg.max_thermal_celsius:
            return False, (f"hardware temperature {hottest:.1f} C exceeds the {cfg.max_thermal_celsius} C limit; "
                           "writes are paused while the device is throttled")
    return True, None


def require_write_allowed(db, path=None):
    allowed, reason = write_allowed(db, path)
    if not allowed:
        raise WritesPaused(reason)


def database_path(db):
    """The file behind the connection's main database (PRAGMA database_list), or None for an in-memory one."""
    for _, name, file in db.execute("PRAGMA database_list").fetchall():
        if name == "main":
            return file or None
    return None


def regulation(db, path=None):
    """Write regulation and interruption load. open_questions counts every open question; open_web_questions
    counts the Web Cortex ones (scope 'web:...'), which have their own budget (WebConfig.max_open_web_questions)
    and never use an epoch's ask budget."""
    allowed, reason = write_allowed(db, path)
    return {
        "writes_allowed": allowed, "reason": reason,
        "evidence_rows": db.execute("SELECT count(*) FROM evidence").fetchone()[0],
        "open_questions": db.execute("SELECT count(*) FROM pending WHERE resolved_at IS NULL").fetchone()[0],
        "open_web_questions": db.execute("SELECT count(*) FROM pending WHERE resolved_at IS NULL "
                                         "AND scope LIKE 'web:%'").fetchone()[0],
        "write_volume": counter(db, "evidence_written"),
        "state_version": state_version(db),
        "config": asdict(config(db)),
    }


def inventory(db):
    """Birth record plus the live memory inventory and development counters."""
    count = lambda sql: db.execute(sql).fetchone()[0]  # noqa: E731
    out = {
        "birth": birth(db),
        "state_version": state_version(db),
        "memory": {
            "epochs": count("SELECT count(*) FROM epochs"),
            "evidence": count("SELECT count(*) FROM evidence"),
            "promoted_evidence": count("SELECT count(*) FROM evidence WHERE retention='promoted'"),
            "tombstones": count("SELECT count(*) FROM tombstones"),
            "belief_revisions": count("SELECT count(*) FROM belief_revisions"),
            "open_questions": count("SELECT count(*) FROM pending WHERE resolved_at IS NULL"),
            "datasets": count("SELECT count(*) FROM datasets WHERE deleted_at IS NULL"),
            "graph_revisions": count("SELECT count(*) FROM graph_revisions"),
            "active_graphs": count("SELECT count(*) FROM graph_revisions WHERE status='active'"),
            "checkpoints": count("SELECT count(*) FROM checkpoints WHERE status IN ('valid','stale')"),
        },
        "development": {name: value for name, value in db.execute(
            "SELECT name,value FROM counters WHERE name!='state_version' ORDER BY name")},
    }
    from ..webcortex import state as web_state  # lazy: the Web Cortex tables exist from migration 6
    web = web_state.counts(db)
    if web:
        out["web"] = web
    return out
