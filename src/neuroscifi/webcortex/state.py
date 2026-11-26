"""Web Cortex persistence inside the individual (migration 6, ADR-029). Owner of every `web_*` table.

Configuration lives in `meta` under config.META_KEY. Helpers documented "caller owns the transaction" must run
inside `store.write(db)`; the others open their own. Everything returned is JSON-serialisable.
"""
from __future__ import annotations

from dataclasses import fields
import json
import math
from urllib.parse import urlsplit

from ..instance import store
from ..instance.errors import Conflict, NotFound, ValidationError, WritesPaused
from . import config as wconfig
from .types import Budget, Claim, ClaimCluster, FRESHNESS_CLASSES, MODES

EVENT_KINDS = ("submitted", "started", "stage", "query", "search", "fetch", "extract", "render", "crawl", "claim",
               "error", "decision", "finding", "synthesized", "consolidated", "prediction", "prediction_error",
               "feedback", "completed", "partial", "failed", "cancelled", "resumed", "cancel_requested")
KNOWLEDGE_STATES = ("provisional", "accepted", "stale", "archived", "rejected", "superseded")
LIVE_KNOWLEDGE = ("provisional", "accepted", "stale")   # rows that still describe the world as known
MONITOR_STATES = ("proposed", "active", "paused", "stopped")
PREDICTION_OUTCOMES = ("confirmed", "error", "inconclusive")
FEEDBACK_EFFECTS = ("strengthened", "weakened", "unchanged", "reversed", "new")
CLAIM_LINK_KINDS = ("supports", "contradicts", "supersedes", "duplicates")     # claim -> claim (migration 6)
# Knowledge-graph edges between typed nodes (migration 8, ADR-038; docs/knowledge/KNOWLEDGE_EVOLUTION.md §3).
LINK_KINDS = CLAIM_LINK_KINDS + ("derived_from", "evidence_for", "valid_during", "related_to", "predicts", "observed",
                                 "error_of", "revised_to")
NODE_KINDS = ("claim", "source", "knowledge", "interval", "entity", "hypothesis", "prediction", "observation", "error")
DISABLED_MESSAGE = "Web access is disabled for this individual; enable with `nsf web config --enable`"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def dumps(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=_default)


def _default(value):
    if hasattr(value, "to_json"):
        return value.to_json()
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    raise TypeError(f"not JSON-serialisable: {type(value).__name__}")


def loads(text, default=None):
    if text is None:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def finite(value, default=None):
    """A finite float or `default` (JSON returns never carry NaN/Infinity)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return default
    return float(value)


def has_tables(db) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='web_tasks'").fetchone() is not None


# ---- configuration -----------------------------------------------------------------------------------------

def get_config(db) -> wconfig.WebConfig:
    row = db.execute("SELECT value FROM meta WHERE key=?", (wconfig.META_KEY,)).fetchone()
    return wconfig.WebConfig.from_json(row[0] if row else None)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _str_list(name, value, *, max_items=200, max_len=256):
    if not isinstance(value, list) or len(value) > max_items or \
            not all(isinstance(v, str) and 0 < len(v) <= max_len for v in value):
        raise ValidationError(f"{name} must be a list of at most {max_items} non-empty strings "
                              f"(each at most {max_len} characters)")
    return list(value)


def _validate(name, value):
    """Return the validated value for one WebConfig field, or raise ValidationError."""
    if name in ("enabled", "autonomous", "pocket_research", "browser_rendering", "news_rendering", "memory_check",
                "search_browser_user_agent", "calibrated_forecasts", "curiosity_learning_progress",
                "salience_record", "hypothesis_fields", "hypothesis_abandon", "premise_recheck", "idle_recheck",
                "reflection", "reflection_model_summary", "answer_monitoring", "selective_answering",
                "verification_escalation", "adaptive_depth", "pocket_recent", "salience_attention",
                "arbitration_record"):
        if not isinstance(value, bool):
            raise ValidationError(f"{name} must be true or false")
        return value
    if name == "tick_admission":
        from .autonomy import ADMISSION_RULES
        if value not in ADMISSION_RULES:
            raise ValidationError(f"tick_admission must be one of {', '.join(ADMISSION_RULES)}")
        return value
    if name == "strategy_selection":
        if value not in ("gate", "off"):
            raise ValidationError("strategy_selection must be 'gate' (follows the improvement ledger) or 'off'")
        return value
    if name in ("recheck_per_tick", "recheck_daily"):
        if not _is_int(value) or not 0 <= value <= 100:
            raise ValidationError(f"{name} must be an integer between 0 and 100")
        return value
    if name == "recheck_order":         # ADR-060: stored in its canonical order
        from .recheck import DEFAULT_ORDER, order_parts
        try:
            parts = order_parts(value)
        except ValueError as exc:
            raise ValidationError(str(exc)) from None
        return "+".join(parts) if parts else DEFAULT_ORDER
    if name == "reflection_interval_days":
        if not _is_num(value) or not 1 <= value <= 365:
            raise ValidationError("reflection_interval_days must be a number of days between 1 and 365")
        return float(value)
    if name == "openwebsearch_url":
        parts = urlsplit(value) if isinstance(value, str) else None
        if parts is None or parts.scheme != "http" or (parts.hostname or "") not in LOOPBACK_HOSTS:
            raise ValidationError("openwebsearch_url must be a plain-HTTP loopback URL, e.g. http://127.0.0.1:3210")
        try:
            parts.port
        except ValueError:
            raise ValidationError("openwebsearch_url has an invalid port") from None
        return value
    if name == "searxng_url":
        if value == "":
            return value
        from .providers.openwebsearch import NotLoopback, loopback_base
        try:
            return loopback_base(value, "SearXNG") if isinstance(value, str) else loopback_base("", "SearXNG")
        except NotLoopback as exc:
            raise ValidationError(f"searxng_url must be empty or a plain-HTTP loopback origin, e.g. "
                                  f"http://127.0.0.1:8888 ({exc})") from None
    if name == "searxng_engines":
        categories = ("general", "news", "it", "science")
        if not isinstance(value, dict) or set(value) - set(categories):
            raise ValidationError(f"searxng_engines keys must be among {', '.join(categories)}")
        return {k: _str_list(f"searxng_engines.{k}", v, max_items=20, max_len=64) for k, v in value.items()}
    if name == "daily_provider_queries":
        keyed = tuple(wconfig.DEFAULT_PROVIDER_DAILY_QUERIES)
        if not isinstance(value, dict) or set(value) - set(keyed):
            raise ValidationError(f"daily_provider_queries keys must be among {', '.join(keyed)}")
        if not all(_is_int(v) and 0 <= v <= 100_000 for v in value.values()):
            raise ValidationError("daily_provider_queries values must be integers from 0 to 100000 (0 = off)")
        return dict(get_default(name), **value)
    if name in ("engines", "providers", "deny_domains"):
        return [v.lower() for v in _str_list(name, value)] if name == "deny_domains" else _str_list(name, value)
    if name == "private_terms":
        return _str_list(name, value, max_items=500, max_len=512)
    if name == "budget_ceiling":
        if not isinstance(value, dict):
            raise ValidationError("budget_ceiling must be an object")
        known = {f.name for f in fields(Budget)}
        unknown = sorted(set(value) - known)
        if unknown:
            raise ValidationError(f"unknown budget field(s): {', '.join(unknown)}")
        for key, item in value.items():
            if not _is_num(item) or item < 0 or (key != "max_seconds" and not _is_int(item)):
                raise ValidationError(f"budget_ceiling.{key} must be a non-negative "
                                      f"{'number' if key == 'max_seconds' else 'integer'}")
        return dict(get_default("budget_ceiling"), **value)
    if name in ("daily_autonomous", "daily_mcp"):
        if not isinstance(value, dict) or set(value) - {"tasks", "requests", "bytes"}:
            raise ValidationError(f"{name} accepts only tasks, requests and bytes")
        if not all(_is_int(v) and v >= 0 for v in value.values()):
            raise ValidationError(f"{name} values must be non-negative integers")
        return dict(get_default(name), **value)
    if name in ("max_open_web_questions", "max_web_questions_per_task"):
        if not _is_int(value) or not 1 <= value <= 1000:
            raise ValidationError(f"{name} must be an integer between 1 and 1000")
        return value
    if name == "web_question_stale_days":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
                or not 0 <= value <= 3650:
            raise ValidationError("web_question_stale_days must be a number of days between 0 and 3650 (0: an open "
                                  "question never becomes stale)")
        return float(value)
    if name == "daily_renders":
        if not _is_int(value) or not 0 <= value <= 1000:
            raise ValidationError("daily_renders must be an integer between 0 and 1000")
        return value
    if name == "mcp_research_per_hour":
        if not _is_int(value) or not 0 <= value <= 1000:
            raise ValidationError("mcp_research_per_hour must be an integer between 0 and 1000")
        return value
    if name == "read_ahead":
        if not _is_int(value) or not 1 <= value <= 16:
            raise ValidationError("read_ahead must be an integer between 1 (one page at a time) and 16")
        return value
    if name == "min_domain_interval":
        if not _is_num(value) or not 0 <= value <= 3600:
            raise ValidationError("min_domain_interval must be between 0 and 3600 seconds")
        return float(value)
    if name in ("max_document_bytes", "cache_max_bytes"):
        if not _is_int(value) or value < 1024:
            raise ValidationError(f"{name} must be an integer of at least 1024")
        return value
    if name == "cache_max_age_days":
        if not _is_num(value) or value <= 0:
            raise ValidationError("cache_max_age_days must be a positive number")
        return float(value)
    if name == "auto_provisional_min_sources":
        if not _is_int(value) or not 1 <= value <= 50:
            raise ValidationError("auto_provisional_min_sources must be an integer between 1 and 50")
        return value
    if name == "auto_provisional_min_confidence":
        if not _is_num(value) or not 0 <= value <= 1:
            raise ValidationError("auto_provisional_min_confidence must be between 0 and 1")
        return float(value)
    if name == "freshness_ttl":
        if not isinstance(value, dict) or set(value) - set(FRESHNESS_CLASSES):
            raise ValidationError(f"freshness_ttl keys must be among {', '.join(FRESHNESS_CLASSES)}")
        if not all(_is_num(v) and v > 0 for v in value.values()):
            raise ValidationError("freshness_ttl values must be positive numbers of seconds")
        return {k: float(v) for k, v in value.items()}
    raise ValidationError(f"unknown configuration key '{name}'")


def get_default(name):
    return getattr(wconfig.WebConfig(), name)


def set_config(db, changes: dict, now) -> wconfig.WebConfig:
    """Validate and apply `changes` (field -> value). Unknown keys -> ValidationError. One state_version bump."""
    if not isinstance(changes, dict) or not changes:
        raise ValidationError("configuration changes must be a non-empty object")
    known = {f.name for f in fields(wconfig.WebConfig)}
    unknown = sorted(set(changes) - known)
    if unknown:
        raise ValidationError(f"unknown configuration key(s): {', '.join(unknown)}")
    validated = {name: _validate(name, value) for name, value in changes.items()}
    with store.write(db):
        current = get_config(db).to_json()
        current.update(validated)
        db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (wconfig.META_KEY, dumps(current)))
        store.bump_state_version(db)
    return get_config(db)


# Renders of the current UTC day, across tasks and processes (ADR-041): {"day": "YYYY-MM-DD", "renders": n}. A meta
# row, not a count of render events, so forgetting a task never gives its renders back.
RENDERS_KEY = "webcortex.renders"


def render_day(db, now) -> dict:
    from datetime import datetime, timezone
    day = datetime.fromtimestamp(float(now), tz=timezone.utc).strftime("%Y-%m-%d")
    row = db.execute("SELECT value FROM meta WHERE key=?", (RENDERS_KEY,)).fetchone()
    held = loads(row[0], {}) if row else {}
    return {"day": day, "renders": int(held.get("renders") or 0) if held.get("day") == day else 0}


def take_render(db, now, cap) -> tuple[bool, int]:
    """Count one render against the day's cap: (True, renders now) or (False, renders so far) when the cap is
    reached. One short write transaction, so concurrent workers cannot both take the last render."""
    with store.write(db):
        today = render_day(db, now)
        if today["renders"] >= int(cap):
            return False, today["renders"]
        today["renders"] += 1
        db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (RENDERS_KEY, dumps(today)))
    return True, today["renders"]


# ---- feeds ---------------------------------------------------------------------------------------------------
# The owner's RSS/Atom feeds live under their own meta key (not in WebConfig): providers.build adds a FeedProvider
# when any exist, and monitors read feed items before web results.

FEEDS_KEY = "webcortex.feeds"
MAX_FEEDS = 50
MAX_FEED_URL = 2048


def get_feeds(db) -> list[str]:
    row = db.execute("SELECT value FROM meta WHERE key=?", (FEEDS_KEY,)).fetchone()
    feeds = loads(row[0], []) if row else []
    return [f for f in feeds if isinstance(f, str)] if isinstance(feeds, list) else []


def _feed_url(url) -> str:
    """A validated feed URL: http(s), a host, no credentials, and an address the fetcher may reach (net.check_url:
    no loopback, private, link-local or reserved local host). A host name that resolves to a private address is
    still refused at fetch time."""
    if not isinstance(url, str) or not 0 < len(url.strip()) <= MAX_FEED_URL:
        raise ValidationError(f"a feed is an http(s) URL of at most {MAX_FEED_URL} characters")
    url = url.strip()
    parts = urlsplit(url)
    try:
        parts.port
    except ValueError:
        raise ValidationError(f"feed URL has an invalid port: {url}") from None
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ValidationError(f"a feed must be an http(s) URL with a host and no credentials: {url}")
    from . import net
    from .types import FetchError
    try:   # the fetcher's address policy, checked now rather than on every task: no loopback/private/local hosts
        net.check_url(url, net.NetPolicy())
    except FetchError as exc:
        raise ValidationError(f"feed URL refused by the network policy ({exc.detail}): {url}") from None
    return url


def set_feeds(db, feeds, now) -> list[str]:
    """Replace the feed list (validated http(s) URLs, duplicates dropped, at most MAX_FEEDS). One state bump."""
    if not isinstance(feeds, list):
        raise ValidationError("feeds must be a list of URLs")
    clean = list(dict.fromkeys(_feed_url(f) for f in feeds))
    if len(clean) > MAX_FEEDS:
        raise ValidationError(f"at most {MAX_FEEDS} feeds")
    with store.write(db):
        db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (FEEDS_KEY, dumps(clean)))
        store.bump_state_version(db)
    return get_feeds(db)


def add_feed(db, url, now) -> list[str]:
    return set_feeds(db, [*get_feeds(db), _feed_url(url)], now)


def remove_feed(db, url, now) -> list[str]:
    feeds = get_feeds(db)
    target = url.strip() if isinstance(url, str) else url
    if target not in feeds:
        raise NotFound(f"No feed {url}")
    return set_feeds(db, [f for f in feeds if f != target], now)


def require_enabled(db) -> wconfig.WebConfig:
    cfg = get_config(db)
    if not cfg.enabled:
        raise Conflict(DISABLED_MESSAGE)
    return cfg


def write_allowed(db, path=None):
    """Instance regulation (store.write_allowed) for a Web Cortex write: (allowed, reason). The free-space check
    uses `path`, else the connection's own file (PRAGMA database_list)."""
    return store.write_allowed(db, path if path is not None else store.database_path(db))


def require_writable(db, path=None):
    """WritesPaused unless regulation allows new writes (web research, consolidation, monitors, proposals)."""
    allowed, reason = write_allowed(db, path)
    if not allowed:
        raise WritesPaused(f"web memory writes are paused: {reason}")


def family_digest(db, fingerprint: str) -> str:
    """Instance-salted pseudonym of a Pocket pairing family fingerprint (like governance.root_digest)."""
    from ..instance.governance import root_digest
    if not isinstance(fingerprint, str) or not fingerprint:
        raise ValidationError("family fingerprint must be a non-empty string")
    return root_digest(store.birth(db)["instance_id"], "family:" + fingerprint)


# ---- tasks and events ----------------------------------------------------------------------------------------

TASK_COLUMNS = ("id", "objective", "mode", "status", "stage", "origin", "family", "parent_id", "budget", "usage",
                "checkpoint", "result", "error", "executed_on", "created_at", "updated_at", "started_at",
                "finished_at", "cancel_requested", "lease_holder", "lease_expires_at")
_TASK_JSON = ("budget", "usage", "checkpoint", "result", "executed_on")


def task(db, task_id, *, family=None) -> dict:
    """The task row as a dict (JSON columns decoded). NotFound when absent or owned by another family."""
    row = db.execute(f"SELECT {','.join(TASK_COLUMNS)} FROM web_tasks WHERE id=?", (str(task_id),)).fetchone()
    if row is None or (family is not None and row[TASK_COLUMNS.index("family")] != family):
        raise NotFound(f"No research task {task_id}")
    out = dict(zip(TASK_COLUMNS, row))
    for key in _TASK_JSON:
        out[key] = loads(out[key], {} if key in ("budget", "usage") else None)
    out["cancel_requested"] = bool(out["cancel_requested"])
    return out


class LostLease(Exception):
    """The worker's lease on a task was taken over (it expired and another worker claimed the task): the run must
    stop without writing its outcome or consolidating."""


def update_task(db, task_id, now, *, holder=None, **changes):
    """Caller owns the transaction. JSON columns are encoded; updated_at is set. With `holder` the update is
    fenced: it applies only while that worker still holds the lease, else LostLease."""
    sets, params = ["updated_at=?"], [now]
    for key, value in changes.items():
        if key not in TASK_COLUMNS or key in ("id", "created_at"):
            raise ValidationError(f"task column {key} cannot be updated")
        sets.append(f"{key}=?")
        params.append(dumps(value) if key in _TASK_JSON and value is not None else value)
    if holder is None:
        db.execute(f"UPDATE web_tasks SET {','.join(sets)} WHERE id=?", (*params, task_id))
        return
    if not db.execute(f"UPDATE web_tasks SET {','.join(sets)} WHERE id=? AND lease_holder=?",
                      (*params, task_id, holder)).rowcount:
        raise LostLease(f"research task {task_id}: the lease is held by another worker")


def require_lease(db, task_id, holder):
    """LostLease unless `holder` still holds the task's lease (a fence before consolidating)."""
    row = db.execute("SELECT lease_holder FROM web_tasks WHERE id=?", (task_id,)).fetchone()
    if row is None or row[0] != holder:
        raise LostLease(f"research task {task_id}: the lease is held by another worker")


def log_event(db, task_id, kind, detail, now, *, stage=None) -> int:
    """Append one episodic event. Caller owns the transaction."""
    if kind not in EVENT_KINDS:
        raise ValidationError(f"event kind must be one of {', '.join(EVENT_KINDS)}")
    return db.execute("INSERT INTO web_events(task_id,kind,stage,detail,created_at) VALUES(?,?,?,?,?)",
                      (task_id, kind, stage, dumps(detail or {}), now)).lastrowid


def events(db, task_id) -> list[dict]:
    return [{"id": i, "kind": k, "stage": s, "detail": loads(d, {}), "at": t} for i, k, s, d, t in db.execute(
        "SELECT id,kind,stage,detail,created_at FROM web_events WHERE task_id=? ORDER BY id", (task_id,))]


# ---- sources, claims, links ----------------------------------------------------------------------------------

SOURCE_COLUMNS = ("id", "task_id", "url", "final_url", "canonical_url", "domain", "title", "kind", "published_at",
                  "modified_at", "retrieved_at", "retrieval_mode", "content_sha256", "provider", "flags", "metadata",
                  "created_at")


def save_source(db, task_id, doc, now, *, provider=None) -> int:
    """Record a document (types.Document or its JSON) the task read. Caller owns the transaction.

    One row per (task, canonical url): a re-read (resume from cache) updates retrieval mode and time.
    """
    d = doc.to_json() if hasattr(doc, "to_json") else dict(doc)
    metadata = {k: v for k, v in (d.get("metadata") or {}).items() if k != "abstract"}
    values = (task_id, d["url"], d.get("final_url"), d["canonical_url"], d["domain"], d.get("title") or "",
              d.get("kind") or "page", finite(d.get("published_at")), finite(d.get("modified_at")),
              d["retrieved_at"], d.get("retrieval_mode") or "live", d.get("content_sha256") or None, provider,
              dumps(d.get("flags") or {}), dumps(metadata), now)
    db.execute(f"INSERT INTO web_sources({','.join(SOURCE_COLUMNS[1:])}) VALUES({','.join('?' * 16)}) "
               "ON CONFLICT(task_id, canonical_url) DO UPDATE SET retrieval_mode=excluded.retrieval_mode, "
               "retrieved_at=excluded.retrieved_at, content_sha256=excluded.content_sha256, flags=excluded.flags",
               values)
    return db.execute("SELECT id FROM web_sources WHERE task_id=? AND canonical_url=?",
                      (task_id, d["canonical_url"])).fetchone()[0]


def sources(db, task_id) -> list[dict]:
    out = []
    for row in db.execute(f"SELECT {','.join(SOURCE_COLUMNS)} FROM web_sources WHERE task_id=? ORDER BY id",
                          (task_id,)):
        item = dict(zip(SOURCE_COLUMNS, row))
        item["flags"], item["metadata"] = loads(item["flags"], {}), loads(item["metadata"], {})
        out.append(item)
    return out


CLAIM_COLUMNS = ("id", "task_id", "source_id", "text", "key", "kind", "polarity", "claim_values", "source_url",
                 "canonical_url", "domain", "title", "published_at", "event_at", "retrieved_at", "verified_at",
                 "relevance", "doc_sha256", "sentence_index", "cluster_status", "confidence", "flags", "created_at")


def claim_from_row(row: dict) -> Claim:
    return Claim(id=row["id"], text=row["text"], source_url=row["source_url"], domain=row["domain"],
                 title=row["title"] or "", retrieved_at=row["retrieved_at"], key=row["key"], kind=row["kind"],
                 polarity=row["polarity"], values=_as_read(_listed(row["claim_values"]), row["text"]),
                 published_at=row["published_at"],
                 event_at=row["event_at"], relevance=row["relevance"] or 0.0, doc_sha256=row["doc_sha256"] or "",
                 sentence_index=row["sentence_index"] or 0,
                 flags=row["flags"] if isinstance(row["flags"], dict) else loads(row["flags"], {}))


def _listed(value):
    return list(value) if isinstance(value, list) else loads(value, [])


def _as_read(values, text):
    """A stored claim's values as this version reads its text (consolidate._read_values, as for a knowledge item):
    a claim saved when "2,900 residents" read as the year 2900, or "120 km/h" as "120 km", is compared as what its
    text says now. A claim's values are those of its own text (claims.extract), so nothing else is replaced."""
    from .consolidate import _read_values
    return _read_values({"claim_values": values, "statement": text})


def claims(db, task_id) -> list[dict]:
    out = []
    for row in db.execute(f"SELECT {','.join(CLAIM_COLUMNS)} FROM web_claims WHERE task_id=? ORDER BY rowid",
                          (task_id,)):
        item = dict(zip(CLAIM_COLUMNS, row))
        item["claim_values"], item["flags"] = loads(item["claim_values"], []), loads(item["flags"], {})
        item["claim_values"] = _as_read(item["claim_values"], item["text"])
        out.append(item)
    return out


def links(db, task_id) -> list[dict]:
    """The task's claim -> claim links (supports, contradicts, supersedes, duplicates). Typed knowledge-graph edges
    (evolution.graph) are not listed here."""
    return [{"id": i, "src": s, "dst": d, "kind": k, "detail": loads(x, {}), "at": t} for i, s, d, k, x, t in
            db.execute("SELECT id,src,dst,kind,detail,created_at FROM web_claim_links WHERE task_id=? AND "
                       "src_kind='claim' AND dst_kind='claim' ORDER BY id", (task_id,))]


def add_link(db, task_id, src, dst, kind, detail, now, *, src_kind="claim", dst_kind="claim") -> int:
    """Caller owns the transaction."""
    if kind not in LINK_KINDS:
        raise ValidationError(f"link kind must be one of {', '.join(LINK_KINDS)}")
    if src_kind not in NODE_KINDS or dst_kind not in NODE_KINDS:
        raise ValidationError(f"node kind must be one of {', '.join(NODE_KINDS)}")
    return db.execute("INSERT INTO web_claim_links(task_id,src_kind,src,dst_kind,dst,kind,detail,created_at) "
                      "VALUES(?,?,?,?,?,?,?,?)", (task_id, src_kind, str(src), dst_kind, str(dst), kind,
                                                  dumps(detail or {}), now)).lastrowid


def cluster_json(cluster: ClaimCluster) -> dict:
    """Checkpoint form of a scored cluster: claim objects replaced by their ids."""
    out = cluster.to_json()
    out["claims"] = [c.id if hasattr(c, "id") else c["id"] for c in cluster.claims]
    for key in ("confidence", "uncertainty"):
        out[key] = finite(out.get(key), 0.0)
    return out


def save_clusters(db, task_id, clusters, now, *, source_ids=None) -> list[dict]:
    """Persist scored clusters of a task: their claims, 'supports' and 'contradicts' links, and the cluster list
    in the task checkpoint (key "clusters"). Caller owns the transaction. Returns the cluster JSON list.

    source_ids: {canonical or source url: web_sources.id}. A claim's canonical url is its source_url (claims are
    extracted from documents keyed by canonical url).
    """
    source_ids = source_ids or {}
    out = []
    for cluster in clusters:
        for c in cluster.claims:
            db.execute(f"INSERT OR REPLACE INTO web_claims({','.join(CLAIM_COLUMNS)}) "
                       f"VALUES({','.join('?' * len(CLAIM_COLUMNS))})",
                       (c.id, task_id, source_ids.get(c.source_url), c.text, c.key or cluster.key, c.kind,
                        -1 if c.polarity < 0 else 1, dumps(list(c.values or [])), c.source_url, c.source_url,
                        c.domain, c.title, finite(c.published_at), finite(c.event_at), c.retrieved_at, now,
                        finite(c.relevance, 0.0), c.doc_sha256 or None, c.sentence_index, cluster.status,
                        finite(cluster.confidence, 0.0), dumps(c.flags or {}), now))
        head = cluster.supporting[0] if cluster.supporting else None
        for cid in cluster.supporting[1:]:
            add_link(db, task_id, cid, head, "supports", {"key": cluster.key}, now)
        for item in cluster.contradicting:
            add_link(db, task_id, item["a"], item["b"], "contradicts", {"reason": item.get("reason", ""),
                                                                        "key": cluster.key}, now)
        out.append(cluster_json(cluster))
    row = db.execute("SELECT checkpoint FROM web_tasks WHERE id=?", (task_id,)).fetchone()
    if row is None:
        raise NotFound(f"No research task {task_id}")
    checkpoint = loads(row[0], None) or {}
    checkpoint["clusters"] = out
    db.execute("UPDATE web_tasks SET checkpoint=?, updated_at=? WHERE id=?", (dumps(checkpoint), now, task_id))
    return out


def load_clusters(db, task_id) -> list[ClaimCluster]:
    """Rebuild the task's scored clusters (with Claim objects) from its checkpoint and web_claims."""
    row = db.execute("SELECT checkpoint FROM web_tasks WHERE id=?", (task_id,)).fetchone()
    if row is None:
        raise NotFound(f"No research task {task_id}")
    stored = {c["id"]: claim_from_row(c) for c in claims(db, task_id)}
    out = []
    for item in (loads(row[0], None) or {}).get("clusters", []):
        members = [stored[cid] for cid in item.get("claims", []) if cid in stored]
        if not members:
            continue
        out.append(ClaimCluster(key=item["key"], claims=members, independent_sources=item["independent_sources"],
                                supporting=[c for c in item["supporting"] if c in stored],
                                contradicting=list(item["contradicting"]), confidence=item["confidence"],
                                uncertainty=item["uncertainty"], status=item.get("status", "single_source"),
                                statement=item.get("statement") or members[0].text))
    return out


# ---- procedural memory ---------------------------------------------------------------------------------------

def load_strategies(db, name="registry") -> dict:
    row = db.execute("SELECT state FROM web_strategies WHERE name=?", (name,)).fetchone()
    return loads(row[0], {}) if row else {}


def save_strategies(db, state: dict, now, name="registry"):
    """Persist ProviderRegistry.dump_state() (or another named strategy record). Opens its own transaction."""
    with store.write(db):
        db.execute("INSERT INTO web_strategies(name,state,updated_at) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE "
                   "SET state=excluded.state, updated_at=excluded.updated_at", (name, dumps(state or {}), now))


def record_mode_outcome(db, mode, status, usage: dict, now):
    """Mode outcome statistics (procedural memory). Caller owns the transaction."""
    if mode not in MODES:
        return
    name = f"mode:{mode}"
    row = db.execute("SELECT state FROM web_strategies WHERE name=?", (name,)).fetchone()
    stats = loads(row[0], {}) if row else {}
    stats["runs"] = stats.get("runs", 0) + 1
    stats[status] = stats.get(status, 0) + 1
    for key in ("requests", "pages", "bytes"):
        stats[f"total_{key}"] = stats.get(f"total_{key}", 0) + int((usage or {}).get(key, 0) or 0)
    db.execute("INSERT INTO web_strategies(name,state,updated_at) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE "
               "SET state=excluded.state, updated_at=excluded.updated_at", (name, dumps(stats), now))


# ---- inventory ---------------------------------------------------------------------------------------------

def counts(db) -> dict:
    """Web memory counts for store.inventory (empty dict when migration 6 is absent)."""
    if not has_tables(db):
        return {}
    count = lambda sql: db.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "tasks": count("SELECT count(*) FROM web_tasks"),
        "open_tasks": count("SELECT count(*) FROM web_tasks WHERE status NOT IN "
                            "('completed','partial','failed','cancelled')"),
        "sources": count("SELECT count(*) FROM web_sources"),
        "claims": count("SELECT count(*) FROM web_claims"),
        "knowledge": count("SELECT count(*) FROM web_knowledge"),
        "accepted_knowledge": count("SELECT count(*) FROM web_knowledge WHERE status='accepted'"),
        "provisional_knowledge": count("SELECT count(*) FROM web_knowledge WHERE status='provisional'"),
        "entities": count("SELECT count(*) FROM web_entities"),
        "monitors": count("SELECT count(*) FROM web_monitors WHERE status IN ('proposed','active','paused')"),
        "predictions_open": count("SELECT count(*) FROM web_predictions WHERE outcome IS NULL"),
        "feedback": count("SELECT count(*) FROM web_feedback"),
    }
