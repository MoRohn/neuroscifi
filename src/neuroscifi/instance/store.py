"""Open, create and describe one persistent individual (one SQLite file).

Contract shared by every instance module:
- `connect(path)` returns a connection with foreign keys, WAL, secure_delete and a busy timeout.
- Writers wrap mutations in `with write(db):` (BEGIN IMMEDIATE ... COMMIT) and call
  `bump_state_version(db)` exactly once per committed logical change.
- Time is always passed in explicitly (`now`, seconds since the epoch) so tests control the clock.
- Configuration and the birth record are written at creation and read through `config(db)` and `birth(db)`.
  The birth record never changes; `set_config(db, changes)` changes the few settings the owner may change later.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
import glob
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import time
import uuid

from .. import __version__
from .errors import InstanceError, NotFound, ValidationError, WritesPaused
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
# gate-v1 with the two optional parts of rules.py: the graph the edge-exchange learner fitted is a candidate beside the
# cuts of the dense fit, and the tolerance is measured against the better of the two learners. Same thresholds, same
# byte budget. Where the dense learner is the better one it chooses what gate-v1 chooses; where it is not (many
# populations) it packs the sparse learner's graph instead of a cut of a fit that failed.
BEST_LEARNER_V1 = {
    **GATE_V1,
    "version": "best-learner-v1",
    "pack_candidates": {"degrees": list(GATE_V1["pack_candidates"]["degrees"]),
                        "bits": list(GATE_V1["pack_candidates"]["bits"]), "learned_sparse": True},
    "pack_reference": "best_learner",
}
# What a new individual is born with, in this order: candidates are learned under the last one (rules.latest), and
# gate-v1 stays on record as the exam every individual born before best-learner-v1 started with. An individual born
# earlier keeps exactly the rules it has; its owner adopts a built-in rule with
# `nsf graph rules add --builtin best-learner-v1 --owner-approved` (cli_graphs.py).
BIRTH_RULES = (GATE_V1, BEST_LEARNER_V1)
BUILTIN_RULES = {rule["version"]: rule for rule in BIRTH_RULES}
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
    # The budget's rolling window: questions asked in an epoch longer ago than this no longer count, so a long-lived
    # epoch can ask again. 0 = the whole epoch (the rule before the window existed: the budget never refills).
    ask_budget_window_seconds: float = 7 * 86400
    # Consolidation (ADR-044): when a learning event's lifetime ends, what it taught (its place in the journal, its
    # channel and root, the corrected reading, its sample id) stays as a consolidated reading, so beliefs outlive
    # their raw events. False = an expired event leaves nothing, as before schema 14; readings consolidated earlier
    # keep counting until they are forgotten.
    consolidation: bool = True
    # The most consolidated readings the file keeps (ADR-044; every reading counts, a later copy of a sample too).
    # At the limit the sweep stops adding readings: expiring learning events leave nothing, as before schema 14, and
    # the sweep, `nsf regulation`, `nsf birth` and the console say so; from CONSOLIDATION_WARN_SHARE of it they warn.
    # It never deletes a reading: lowering it below what is kept only stops further consolidation. 0 = no limit.
    max_consolidated_rows: int = 1_000_000
    max_thermal_celsius: float | None = None  # regulation: pause new writes above this temperature (None = off)

    @classmethod
    def from_json(cls, text):
        raw = json.loads(text)
        if not isinstance(raw, dict):
            raise ValueError("the settings are not a JSON object")
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in raw.items() if k in known}
        # Never on by truthiness: a saved value that is not a JSON boolean (a hand-edited "no", a 1) reads as off,
        # the reading that cannot keep data an owner opted out of. setting_problems() reports it.
        if not isinstance(values.get("consolidation", True), bool):
            values["consolidation"] = False
        # A limit that is not a whole number from 0 up (a hand-edited "none", a -1, true) is not a limit: the default
        # stands, never "no limit" by accident. setting_problems() reports it.
        if "max_consolidated_rows" in values and not _row_limit(values["max_consolidated_rows"]):
            del values["max_consolidated_rows"]
        return cls(**values)


MAX_ROW_LIMIT = 10**12
CONSOLIDATION_WARN_SHARE = 0.8      # of max_consolidated_rows: from here the owner is warned


def _row_limit(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_ROW_LIMIT


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


# Settings the owner may change after birth (`nsf config`), each with its check. The others are fixed at `nsf init`.
_SETTABLE = {
    "ask_budget_per_epoch": lambda v: isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 1000,
    "ask_budget_window_seconds": lambda v: (isinstance(v, (int, float)) and not isinstance(v, bool)
                                            and math.isfinite(v) and 0 <= v <= 36500 * 86400),
    "consolidation": lambda v: isinstance(v, bool),
    "max_consolidated_rows": _row_limit,
}


SETTING_KIND = "setting"      # deletion_log.target_kind of a changed setting (target_id: the setting's name)


def valid_setting(name, value):
    """Whether `value` is one that set_config would accept for the owner-settable setting `name`."""
    return name in _SETTABLE and bool(_SETTABLE[name](value))


def setting_problems(db):
    """Owner-settable settings whose saved value set_config would refuse (a hand-edited or damaged settings row), as
    sentences; [] for a sound row. Such a value is never used as it stands: consolidation reads as off, the ask
    budget's window as its default (executive._ask_window)."""
    try:
        raw = json.loads(_meta(db, "config"))
    except ValueError:
        return ["the saved settings cannot be read as JSON"]
    if not isinstance(raw, dict):
        return ["the saved settings are not a JSON object"]
    notes = {"consolidation": "; consolidation is treated as off until it is set with `nsf config --consolidation` or "
                              "`--no-consolidation`",
             "max_consolidated_rows": f"; the default limit of {InstanceConfig.max_consolidated_rows} consolidated "
                                      "readings applies until it is set with `nsf config --max-consolidated-rows N`"}
    return [f"the saved {name} ({raw[name]!r}) is not a valid value" + notes.get(name, "")
            for name in sorted(_SETTABLE) if name in raw and not _SETTABLE[name](raw[name])]


def consolidation_room(db, cfg=None):
    """How many more consolidated readings the file may take: None without a limit (max_consolidated_rows 0), else
    the limit less what is kept, never below 0. One count over the smallest index of the table."""
    cfg = config(db) if cfg is None else cfg
    if not cfg.max_consolidated_rows:
        return None
    held = db.execute("SELECT count(*) FROM evidence_consolidated").fetchone()[0]
    return max(0, cfg.max_consolidated_rows - held)


_CAP_REMEDY = ("Raise the limit with `nsf config --max-consolidated-rows N` (0 = no limit), or forget an epoch, a "
               "partner or a camera that is no longer needed (`nsf forget epoch|partner|camera NAME`).")


def consolidation_warnings(db, held=None, cfg=None):
    """What the owner must be told about the limit on consolidated readings, as sentences; [] below
    CONSOLIDATION_WARN_SHARE of it, without a limit, and while consolidation is off (nothing is added then)."""
    cfg = config(db) if cfg is None else cfg
    limit = cfg.max_consolidated_rows
    if not limit or not cfg.consolidation:
        return []
    if held is None:
        held = db.execute("SELECT count(*) FROM evidence_consolidated").fetchone()[0]
    if held >= limit:
        return [f"The limit on consolidated readings is reached ({held} of {limit}): a learning event that reaches "
                "the end of its lifetime now leaves nothing, as before consolidation existed, so beliefs stop "
                f"outliving new evidence. Nothing already kept is removed. {_CAP_REMEDY}"]
    if held >= CONSOLIDATION_WARN_SHARE * limit:
        return [f"Consolidated readings are at {100 * held // limit} % of their limit ({held} of {limit}). At the "
                f"limit a learning event that reaches the end of its lifetime leaves nothing. {_CAP_REMEDY}"]
    return []


def set_config(db, changes, now=None):
    """Change owner-settable instance settings. Returns {"config": the settings now, "changed": [names]}.

    ValidationError for an unknown or fixed setting or a value out of range; nothing is written then. A real change
    bumps the state version and leaves a row in the deletion log (kind 'setting', the name, {"from", "to"}), beside
    the forgets and sweeps whose meaning it changes: the log then shows from when to when expired events left no
    consolidated reading. Settings saved by older software keep every field they have; a field they lack reads as
    its default (InstanceConfig.from_json).
    """
    if not isinstance(changes, dict):
        raise ValidationError("settings must be a JSON object")
    for name, value in changes.items():
        check = _SETTABLE.get(name)
        if check is None:
            raise ValidationError(f"{name!r} cannot be changed (changeable: {', '.join(sorted(_SETTABLE))})")
        if not check(value):
            raise ValidationError(f"{name}: value {value!r} is out of range")
    with write(db):
        raw = json.loads(_meta(db, "config"))
        current = asdict(InstanceConfig.from_json(json.dumps(raw)))
        # (against what is saved, type included: setting a damaged "no" to false is a change and repairs the row)
        current = current | {name: raw[name] for name in changes if name in raw}
        changed = sorted(name for name, value in changes.items()
                         if current[name] != value or isinstance(current[name], bool) != isinstance(value, bool))
        if changed:
            raw.update({name: changes[name] for name in changed})
            db.execute("UPDATE meta SET value=? WHERE key='config'", (json.dumps(raw, sort_keys=True),))
            version = bump_state_version(db)
            db.executemany("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects) VALUES(?,?,?,?)", [
                (SETTING_KIND, name, time.time() if now is None else now, json.dumps(
                    {"from": current[name], "to": changes[name], "state_version": version}, sort_keys=True))
                for name in changed])
    return {"config": asdict(config(db)), "changed": changed}


def birth(db):
    return json.loads(_meta(db, "birth"))


def create(path, now, *, action_semantics=None, config=None, founding_graph=None, note=None, birth_rules=None):
    """Create a new individual. Refuses an existing file so an individual is never overwritten.

    birth_rules: the scoring rules it starts with, registered in order (default BIRTH_RULES); graphs are learned
    under the last. Given explicitly to reproduce an individual born under gate-v1 alone: `birth_rules=(GATE_V1,)`.
    """
    path = Path(path)
    if path.exists():
        raise ValidationError(f"{path} already exists; each file is one individual")
    birth_rules = BIRTH_RULES if birth_rules is None else tuple(birth_rules)
    if not birth_rules:
        raise ValidationError("An individual needs at least one scoring rule at birth")
    from . import rules  # registered at birth so every individual starts with an immutable exam
    for rule in birth_rules:
        rules.validate(rule)
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
            for rule in birth_rules:
                rules.register(db, rule, now, created_by="birth")
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
    cfg = config(db)
    held = db.execute("SELECT count(*) FROM evidence_consolidated").fetchone()[0]
    out = {
        "writes_allowed": allowed, "reason": reason,
        "evidence_rows": db.execute("SELECT count(*) FROM evidence").fetchone()[0],
        "consolidated_readings": held,
        "open_questions": db.execute("SELECT count(*) FROM pending WHERE resolved_at IS NULL").fetchone()[0],
        "open_web_questions": db.execute("SELECT count(*) FROM pending WHERE resolved_at IS NULL "
                                         "AND scope LIKE 'web:%'").fetchone()[0],
        "write_volume": counter(db, "evidence_written"),
        "state_version": state_version(db),
        "config": asdict(cfg),
    }
    if held:
        out["largest_epoch"] = _largest_epoch(db)       # absent while nothing is consolidated
    warnings = consolidation_warnings(db, held, cfg)
    if warnings:
        out["warnings"] = warnings                      # absent while there is nothing to say
    return out


def _largest_epoch(db):
    """The epoch with the most consolidated readings and what its beliefs cost to compute now: {"epoch",
    "consolidated_readings", "compute_ms"}. Belief computation is linear in an epoch's readings (ADR-044), and every
    decision and belief read of that epoch pays it, so the owner sees the cost here before it is felt. One grouped
    pass over the epoch index and one compute(); compute_ms is None when the epoch's beliefs cannot be computed."""
    epoch, readings = db.execute("SELECT epoch, count(*) FROM evidence_consolidated GROUP BY epoch "
                                 "ORDER BY 2 DESC, 1 LIMIT 1").fetchone()
    from .beliefs import compute                        # lazy: beliefs imports this module
    started = time.perf_counter()
    try:
        compute(db, epoch)
        elapsed = round((time.perf_counter() - started) * 1000, 1)
    except InstanceError:
        elapsed = None
    return {"epoch": epoch, "consolidated_readings": readings, "compute_ms": elapsed}


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
            "consolidated_readings": count("SELECT count(*) FROM evidence_consolidated"),
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
    warnings = consolidation_warnings(db, out["memory"]["consolidated_readings"])
    if warnings:
        out["warnings"] = warnings      # absent while there is nothing to say (the limit on consolidated readings)
    return out
