"""The internal-state record: one typed, versioned snapshot of the individual's own operating condition, assembled
from measurements that already exist (Project Cognitive Genesis, lane I; ADR-051).

It is a measurement record, not a feeling: every field says what it measured, where it came from (`source`), when
the reading was taken (`at`) and how old it is (`age_seconds`). Nothing here is inferred by a model.

  snapshot(db, db_path, now)   computed on request (`nsf internal-state`): memory and pressure level, the background
                               gate, audio, background model slots and in-use locks (read from /proc/locks, never
                               by taking a lock), the idle policy's unloaded services, write regulation and backlog,
                               the research queue, the worker's heartbeat, model calls from the token ledger (and the
                               endpoint's health with gateway=True), knowledge staleness, prediction errors, internal
                               disagreement (open contradictions, open change suspicions), the owner's quiet switch,
                               the persisted history's summary and the predictive regulation's forecast
  record(db_path, now)         one compact sample appended to <db>.resources/internal-state.jsonl (the audio beacon's
                               folder and file rules, resources/shared.py: no link followed, bounded reads, the
                               folder's file mode, so the idle account can read it). The research worker writes it
                               from its heartbeat thread every RECORD_EVERY s (scheduler.work); nothing else writes
                               it. HISTORY_KEPT samples are kept (6 hours at the default pace)
  history(db_path)             the persisted samples, oldest first (what resources.forecast reads)

The history holds numbers only (memory, counts, flags, mean latency): no text, no content, no identifiers. Like
idle-events.jsonl and the token ledger it is operational telemetry: not in checkpoints, not something `forget` needs
to reach, and deleting the file loses only the statistics. The owner turns recording off with
`nsf config --no-internal-state-history` (InstanceConfig.internal_state_history)."""
from __future__ import annotations

from contextlib import closing
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import time

SCHEMA = "nsf.internal_state/1"
HISTORY_FILE = "internal-state.jsonl"
LOCK_FILE = "internal-state.lock"
HISTORY_KEPT = 720            # samples kept (6 hours at RECORD_EVERY)
RECORD_EVERY = 30.0           # seconds between the worker's samples
READ_LIMIT = 1 << 20          # bytes of the history read at most (its newest part)
LATENCY_WINDOW = 300.0        # model calls of the last this many seconds make the latency reading
ERROR_WINDOW = 30 * 86400.0   # prediction errors of the last this many seconds count


def folder(db_path) -> Path | None:
    return Path(f"{db_path}.resources") if db_path else None


def _field(value, source, at, now) -> dict:
    return {"value": value, "source": source, "at": at,
            "age_seconds": None if at is None else round(max(0.0, now - at), 3)}


# ---- locks held, read from /proc/locks (no lock is taken) ----------------------------------------------------------

def _proc_locks() -> list[tuple[str, str]]:
    """[(mode, "major:minor:inode")] of the FLOCK locks on this host (READ = shared, WRITE = exclusive)."""
    try:
        with open("/proc/locks", encoding="ascii", errors="replace") as fh:
            lines = fh.read(1 << 20).splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        parts = line.split()
        if "->" in parts:                       # a waiter, not a holder
            continue
        if len(parts) >= 6 and parts[1] == "FLOCK":
            out.append((parts[3], parts[5]))
    return out


def _locks(paths) -> dict | None:
    """{path name: (shared holders, exclusive holders)} for the files that exist; None when /proc/locks is not
    readable (not Linux)."""
    if not os.path.exists("/proc/locks"):
        return None
    held = _proc_locks()
    out = {}
    for path in paths:
        try:
            st = os.lstat(path)
        except OSError:
            continue
        ident = f"{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}:{st.st_ino}"
        modes = [mode for mode, key in held if key == ident]
        out[Path(path).name] = (modes.count("READ"), modes.count("WRITE"))
    return out


def _slots_in_use(db_path):
    where = folder(db_path)
    if where is None or not where.is_dir():
        return 0
    locks = _locks(sorted(where.glob("model-slot-*.lock")))
    return None if locks is None else sum(1 for _, exclusive in locks.values() if exclusive)


def _services_in_use(db_path):
    where = folder(db_path)
    if where is None or not where.is_dir():
        return {}
    locks = _locks(sorted(where.glob("idle-inuse-*.lock")))
    if locks is None:
        return None
    return {name.removeprefix("idle-inuse-").removesuffix(".lock"): shared for name, (shared, _) in locks.items()}


# ---- the parts of the instance database ------------------------------------------------------------------------------

def _has(db, table) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _queue(db) -> dict | None:
    """Research tasks queued and running, split into the owner's and background ones (background.ORIGIN_SQL)."""
    if not _has(db, "web_tasks"):
        return None
    from .background import ORIGIN_SQL
    rows = db.execute(f"SELECT status, {ORIGIN_SQL}, count(*) FROM web_tasks WHERE status IN ('queued','discovering',"
                      "'reading','evaluating','synthesizing') AND coalesce(stage,'')!='proposed' GROUP BY 1, 2"
                      ).fetchall()
    out = {"owner_queued": 0, "background_queued": 0, "owner_running": 0, "background_running": 0}
    for status, background, n in rows:
        out[f"{'background' if background else 'owner'}_{'queued' if status == 'queued' else 'running'}"] += n
    return out


def _model_calls(db_path, now) -> tuple[dict | None, float | None]:
    """The token ledger's last LATENCY_WINDOW seconds (read only; a missing ledger is not created)."""
    if not db_path:
        return None, None
    from .inference.ledger import ledger_path
    path = ledger_path(db_path)
    if not path.exists():
        return None, None
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)) as led:
            row = led.execute("SELECT count(*), coalesce(sum(status='ok'),0), avg(latency_ms), avg(first_token_ms), "
                              "max(at), coalesce(sum(priority IS NOT NULL AND priority>0),0) FROM calls WHERE at>=?",
                              (now - LATENCY_WINDOW,)).fetchone()
    except sqlite3.Error:
        return None, None
    calls, ok, latency, ttft, newest, background = row
    return ({"window_seconds": LATENCY_WINDOW, "calls": calls, "ok": ok, "background_calls": background,
             "mean_latency_ms": None if latency is None else round(latency, 1),
             "mean_first_token_ms": None if ttft is None else round(ttft, 1)}, newest)


def _knowledge(db, now) -> tuple[dict | None, dict | None]:
    """(staleness, disagreement) of the live web knowledge."""
    if not _has(db, "web_knowledge"):
        return None, None
    from .webcortex import knowledge, state
    cfg = state.get_config(db)
    live = knowledge.rows(db, f"status IN ({','.join('?' * len(state.LIVE_KNOWLEDGE))})", state.LIVE_KNOWLEDGE)
    stale = sum(1 for item in live[:2000] if knowledge.staleness(item, now, cfg, db)[0])
    contradicted = sum(1 for item in live if item["contradicting"])
    contested = db.execute("SELECT count(*) FROM pending WHERE resolved_at IS NULL AND scope LIKE 'web:contested:%'"
                           ).fetchone()[0]
    return ({"live_items": len(live), "stale_items": stale, "checked": min(len(live), 2000)},
            {"contradicted_knowledge": contradicted, "open_contested_questions": contested})


def _prediction_errors(db, now) -> dict:
    out = {"window_days": ERROR_WINDOW / 86400}
    if _has(db, "web_predictions"):
        rows = dict(db.execute("SELECT outcome, count(*) FROM web_predictions WHERE checked_at>=? AND outcome IS NOT "
                               "NULL GROUP BY outcome", (now - ERROR_WINDOW,)).fetchall())
        out["web_settled"] = sum(rows.values())
        out["web_errors"] = rows.get("error", 0)
    # the core's change detector: questions it raised that are still open (a suspected change of a calibration)
    out["open_change_questions"] = db.execute("SELECT count(*) FROM pending WHERE resolved_at IS NULL AND "
                                              "kind='calibrate'").fetchone()[0]
    return out


# ---- the snapshot ----------------------------------------------------------------------------------------------------

def snapshot(db, db_path, now=None, *, gateway=False, history_limit=0) -> dict:
    """The record (see the module doc). gateway=True also asks the model endpoint for its health (a network call to
    the owner-configured endpoint, as `nsf llm status` makes); history_limit > 0 includes that many recent samples."""
    from . import background
    from .instance import store
    from .resources import forecast, idle, monitor, policy
    from .webcortex import scheduler
    now = time.time() if now is None else now
    fields = {}
    s = monitor.sample()
    fields["memory"] = _field({"available_bytes": (s["memory"] or {}).get("available_bytes"),
                               "total_bytes": (s["memory"] or {}).get("total_bytes"), "level": s["level"],
                               "reasons": s["reasons"]}, "resources.monitor.sample (/proc/meminfo, PSI)", s["at"], now)
    gate = policy.background_gate(db_path, sample=s)
    fields["audio_active"] = _field(gate["audio"], "resources.policy.audio_active (voice hub, audio beacon)", now, now)
    fields["background_gate"] = _field({"allowed": gate["allowed"], "reason": gate["reason"], "why": gate["why"]},
                                       "resources.policy.background_gate", now, now)
    fields["background_model_slots"] = _field({"cap": policy.model_slots(db_path), "in_use": _slots_in_use(db_path)},
                                              "resources.policy.model_slots; /proc/locks on model-slot-*.lock", now,
                                              now)
    fields["model_services_in_use"] = _field(_services_in_use(db_path), "/proc/locks on idle-inuse-*.lock", now, now)
    if db_path:
        services = idle.Idle(db_path).read().get("services") or {}
        fields["idle_unloaded"] = _field(sorted(k for k, v in services.items() if (v or {}).get("state") ==
                                                "unloaded"), "resources.idle (idle.json)", now, now)
    reg = store.regulation(db, db_path)
    fields["writes"] = _field({k: reg[k] for k in ("writes_allowed", "reason", "evidence_rows",
                                                   "consolidated_readings", "open_questions", "open_web_questions",
                                                   "state_version")}, "instance.store.regulation", now, now)
    fields["research_queue"] = _field(_queue(db), "web_tasks (instance database)", now, now)
    if db_path:
        worker = scheduler.worker_status(db_path, now)
        fields["worker"] = _field({"running": worker["running"]}, "webcortex.scheduler.worker_status (<db>.worker)",
                                  worker["beat_at"], now)
    calls, newest = _model_calls(db_path, now)
    fields["model_calls"] = _field(calls, "inference.ledger (<db>.inference.sqlite)", newest, now)
    if gateway:
        from .inference.gateway import Gateway
        health = Gateway.from_db(db, db_path).health()
        fields["model_endpoint"] = _field({k: health.get(k) for k in ("state", "reason", "model", "latency_ms")},
                                          "inference.gateway.Gateway.health", time.time(), now)
    staleness, disagreement = _knowledge(db, now)
    fields["knowledge_staleness"] = _field(staleness, "webcortex.knowledge.staleness", now, now)
    fields["prediction_errors"] = _field(_prediction_errors(db, now), "web_predictions; pending (kind calibrate)",
                                         now, now)
    fields["internal_disagreement"] = _field(disagreement, "web_knowledge.contradicting; pending web:contested",
                                             now, now)
    fields["quiet"] = _field(background.quiet(db), "background.quiet (the owner's switch)", now, now)
    samples = history(db_path)
    cfg = store.config(db)
    out = {"schema": SCHEMA, "version": 1, "at": now, "fields": fields,
           "settings": {"internal_state_history": cfg.internal_state_history,
                        "predictive_regulation": cfg.predictive_regulation},
           "history": {"file": str(folder(db_path) / HISTORY_FILE) if db_path else None, "samples": len(samples),
                       "oldest_at": samples[0]["at"] if samples else None,
                       "newest_at": samples[-1]["at"] if samples else None, "kept": HISTORY_KEPT,
                       "every_seconds": RECORD_EVERY},
           "forecast": forecast.assess(samples, now=now)}
    if history_limit:
        out["history"]["recent"] = samples[-int(history_limit):]
    return out


# ---- the persisted history -------------------------------------------------------------------------------------------

def sample(db, db_path, now=None) -> dict:
    """One compact history sample: numbers and flags only."""
    from .resources import monitor, policy
    now = time.time() if now is None else now
    s = monitor.sample()
    mem = s["memory"] or {}
    queue = _queue(db) or {}
    calls, _ = _model_calls(db_path, now)
    from .instance import store
    from .instance.errors import InstanceError
    try:
        allowed = store.write_allowed(db, db_path)[0]
    except (InstanceError, ValueError):      # a damaged settings row: the sample still records the host
        allowed = None
    return {"v": 1, "at": round(now, 3), "available_bytes": mem.get("available_bytes"),
            "total_bytes": mem.get("total_bytes"), "level": s["level"], "audio": policy.audio_active(db_path),
            "background_running": queue.get("background_running", 0), "owner_queued": queue.get("owner_queued", 0),
            "background_queued": queue.get("background_queued", 0), "writes_allowed": allowed,
            "latency_ms": (calls or {}).get("mean_latency_ms")}


def enabled(db) -> bool:
    from .instance import store
    from .instance.errors import NotFound
    try:
        return bool(store.config(db).internal_state_history)
    except NotFound:
        return False


def append(db_path, item: dict) -> None:
    """Append one sample under the history's lock; trim to HISTORY_KEPT when it holds twice that."""
    from .resources import shared
    where = shared.ensure_folder(folder(db_path))
    path = where / HISTORY_FILE
    lock = shared.open_file(where / LOCK_FILE, os.O_RDWR)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        fd = shared.open_file(path, os.O_WRONLY | os.O_APPEND)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(item, sort_keys=True) + "\n")
        try:
            lines = shared.read_text(path, limit=READ_LIMIT, tail=True).splitlines()
        except (OSError, ValueError):
            lines = []
        if len(lines) > 2 * HISTORY_KEPT:
            shared.write_text(path, "\n".join(lines[-HISTORY_KEPT:]) + "\n")
    finally:
        os.close(lock)


def record(db_path, now=None) -> dict | None:
    """Take one sample with a read-only connection and append it, when the owner's setting allows (the research
    worker's heartbeat calls this every RECORD_EVERY s). Returns the sample, or None when nothing was recorded."""
    if not db_path:
        return None
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)) as db:
        db.execute("PRAGMA query_only=ON")
        if not enabled(db):
            return None
        item = sample(db, db_path, now)
    append(db_path, item)
    return item


def record_quietly(db_path, now=None) -> None:
    """record(), never raising: a status reading must not stop the worker that takes it."""
    try:
        record(db_path, now)
    except Exception:          # noqa: BLE001 - telemetry only
        pass


def history(db_path, limit=HISTORY_KEPT) -> list[dict]:
    """The persisted samples, oldest first (at most `limit`, the newest ones); [] without a file."""
    from .resources import shared
    where = folder(db_path)
    if where is None:
        return []
    try:
        lines = shared.read_text(where / HISTORY_FILE, limit=READ_LIMIT, tail=True).splitlines()
    except (OSError, ValueError):
        return []
    out = []
    for line in lines[-limit:] if limit else lines:
        try:
            item = shared.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and isinstance(item.get("at"), (int, float)):
            out.append(item)
    out.sort(key=lambda item: item["at"])
    return out


def cli(args):
    """`nsf internal-state [--gateway] [--history N]`: the record, computed now (JSON)."""
    from .instance import store
    path = store.resolve_db(args)
    db = store.open_existing(path, graph_migration=False)
    try:
        return snapshot(db, path, time.time(), gateway=args.gateway, history_limit=args.history)
    finally:
        db.close()


__all__ = ["HISTORY_FILE", "HISTORY_KEPT", "RECORD_EVERY", "SCHEMA", "append", "history", "record",
           "record_quietly", "sample", "snapshot"]
