"""Public web cache: downloaded bodies, HTTP validators and robots.txt files (ARCHITECTURE §6, ADR-023).

Contract
- One SQLite file per individual, `<db stem>.webcache.sqlite`, separate from the individual's own file and never part
  of a checkpoint. Created with mode 0600 (umask 077 while creating), WAL journal, `secure_delete` on so forgotten
  bodies are overwritten rather than left in free pages.
- Tables: `documents(sha256, body, content_type, bytes, first_seen)` (content-addressed: one row per distinct body),
  `urls(url, final_url, status, sha256, etag, last_modified, fetched_at)`, `robots(host, text, fetched_at)`,
  `backoff(host, until)` (Retry-After backoffs shared by every SafeFetcher on this cache), `meta(key, value)`
  (`last_prune`).
- Privacy: rows are keyed by URL and content hash only. No task objective, search query or claim is ever written
  here; the fetcher does not cache purpose='api' responses because API URLs embed search queries.
- `prune` removes rows older than `max_age_seconds` and expired backoffs, then the least recently fetched documents
  until the stored bodies total at most `max_bytes`. `maybe_prune(now, ..., every=3600)` runs `prune` only when the
  last prune recorded in `meta` is at least `every` seconds old (or in the future), so callers may invoke it on every
  run; it returns the prune report or None. `forget_url` / `forget_domain` delete matching rows and every document
  body no remaining `urls` row references; `forget_domain` also drops the domain's robots and backoff rows.
- Rendered pages (`put_rendered`): the DOM a BrowserRenderer produced is stored under its content hash with a URL
  row `<url>#nsf-rendered` (never a fetchable URL, so a later fetch of the page is never served the rendered body);
  a resume re-reads it by hash instead of rendering again. Retention and forget_url/forget_domain treat it like any
  other row.
- Disposable (ADR-023): a corrupt file ("file is not a database", "database disk image is malformed", on open or on
  any later use) is renamed `<name>.corrupt-<unix time>` (with its -wal/-shm) and a fresh empty cache is created in
  its place; the operation is retried once on the fresh cache. Each quarantine is appended to `events`
  ({"quarantined_as", "error"}) for the caller to record (services.Services.record_cache_warnings). Busy/locked
  errors are not corruption and propagate.
- Thread-safe: one connection guarded by a lock. Times are passed in (`fetched_at`, `now`); nothing reads a clock
  except the quarantine name.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
import threading
import time
from urllib.parse import urlsplit

RENDERED_SUFFIX = "#nsf-rendered"

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    sha256 TEXT PRIMARY KEY, body BLOB NOT NULL, content_type TEXT NOT NULL DEFAULT '',
    bytes INTEGER NOT NULL, first_seen REAL NOT NULL);
CREATE TABLE IF NOT EXISTS urls (
    url TEXT PRIMARY KEY, final_url TEXT NOT NULL, status INTEGER NOT NULL, sha256 TEXT NOT NULL,
    etag TEXT, last_modified TEXT, fetched_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS urls_sha ON urls(sha256);
CREATE INDEX IF NOT EXISTS urls_fetched ON urls(fetched_at);
CREATE TABLE IF NOT EXISTS robots (host TEXT PRIMARY KEY, text TEXT NOT NULL, fetched_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS backoff (host TEXT PRIMARY KEY, until REAL NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").rstrip(".")
    except ValueError:
        return ""


def _in_domain(host: str, domain: str) -> bool:
    return bool(host) and (host == domain or host.endswith("." + domain))


def is_corrupt(error) -> bool:
    """SQLITE_CORRUPT / SQLITE_NOTADB (raised as plain sqlite3.DatabaseError), never busy/locked/constraint errors."""
    if not isinstance(error, sqlite3.DatabaseError):
        return False
    text = str(error).lower()
    return type(error) is sqlite3.DatabaseError or "malformed" in text or "not a database" in text


class PublicCache:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events: list[dict] = []
        self._lock = threading.Lock()
        try:
            self._open()
        except sqlite3.DatabaseError as error:
            if not is_corrupt(error):
                raise
            self._quarantine(error)
            self._open()

    def _open(self):
        old = os.umask(0o077)
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            os.close(fd)
            os.chmod(self.path, 0o600)
            self._db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False, timeout=30)
            try:
                self._db.execute("PRAGMA journal_mode=WAL")
                self._db.execute("PRAGMA synchronous=NORMAL")
                self._db.execute("PRAGMA secure_delete=ON")
                self._db.executescript(SCHEMA)
                self._db.execute("SELECT count(*) FROM urls").fetchone()
            except BaseException:
                self._db.close()
                raise
        finally:
            os.umask(old)

    def _quarantine(self, error):
        """Rename the corrupt file (and its -wal/-shm) aside; the caller reopens a fresh cache."""
        stamp = int(time.time())
        target = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
        n = 1
        while target.exists():
            target = self.path.with_name(f"{self.path.name}.corrupt-{stamp}-{n}")
            n += 1
        for suffix in ("", "-wal", "-shm"):
            source = Path(str(self.path) + suffix)
            if source.exists():
                os.replace(source, Path(str(target) + suffix))
        self.events.append({"quarantined_as": str(target), "error": str(error)[:200]})

    def _call(self, fn):
        """Run fn(connection) under the lock; a corrupt cache is quarantined, rebuilt and fn retried once."""
        with self._lock:
            try:
                return fn(self._db)
            except sqlite3.DatabaseError as error:
                if not is_corrupt(error):
                    raise
                try:
                    self._db.close()
                except sqlite3.Error:
                    pass
                self._quarantine(error)
                self._open()
                return fn(self._db)

    def close(self):
        with self._lock:
            self._db.close()

    # ---- documents and URLs ---------------------------------------------------------------------------------
    def lookup(self, url: str) -> dict | None:
        row = self._call(lambda db: db.execute(
            "SELECT u.final_url, u.status, d.content_type, u.sha256, u.etag, u.last_modified, u.fetched_at "
            "FROM urls u JOIN documents d ON d.sha256 = u.sha256 WHERE u.url = ?", (url,)).fetchone())
        if row is None:
            return None
        keys = ("final_url", "status", "content_type", "sha256", "etag", "last_modified", "fetched_at")
        return dict(zip(keys, row))

    def _put(self, result, urls, etag, modified) -> str:
        body = bytes(result.body)
        sha = hashlib.sha256(body).hexdigest()

        def put(db):
            with db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT OR IGNORE INTO documents(sha256, body, content_type, bytes, first_seen) "
                           "VALUES (?,?,?,?,?)", (sha, body, result.content_type or "", len(body), result.retrieved_at))
                for url in urls:
                    db.execute("INSERT OR REPLACE INTO urls(url, final_url, status, sha256, etag, last_modified, "
                               "fetched_at) VALUES (?,?,?,?,?,?,?)",
                               (url, result.final_url, int(result.status), sha, etag, modified, result.retrieved_at))
        self._call(put)
        return sha

    def put(self, result) -> str:
        """Store a FetchResult body (content-addressed) and its URL rows; returns the body's sha256."""
        headers = result.headers or {}
        return self._put(result, list(dict.fromkeys((result.url, result.final_url))), headers.get("etag"),
                         headers.get("last-modified"))

    def put_rendered(self, result) -> str:
        """Store a rendered page (retrieval_mode 'rendered') under its content hash, with the URL row
        `<url>#nsf-rendered` (no validators); returns the body's sha256 (body() reads it back)."""
        return self._put(result, [str(result.url).split("#", 1)[0] + RENDERED_SUFFIX], None, None)

    def body(self, sha256: str) -> bytes | None:
        row = self._call(lambda db: db.execute("SELECT body FROM documents WHERE sha256 = ?", (sha256,)).fetchone())
        return bytes(row[0]) if row else None

    # ---- robots.txt -----------------------------------------------------------------------------------------
    def robots(self, host: str):
        row = self._call(lambda db: db.execute("SELECT text, fetched_at FROM robots WHERE host = ?",
                                               (host.lower(),)).fetchone())
        return (row[0], row[1]) if row else None

    def put_robots(self, host: str, text: str, fetched_at: float):
        self._call(lambda db: db.execute("INSERT OR REPLACE INTO robots(host, text, fetched_at) VALUES (?,?,?)",
                                         (host.lower(), text, fetched_at)))

    # ---- Retry-After backoff ----------------------------------------------------------------------------------
    def backoff(self, host: str) -> float | None:
        """Time until which requests to host must not be sent, or None."""
        row = self._call(lambda db: db.execute("SELECT until FROM backoff WHERE host = ?",
                                               (host.lower(),)).fetchone())
        return row[0] if row else None

    def put_backoff(self, host: str, until: float):
        """Record a backoff; an earlier `until` never shortens one already stored."""
        self._call(lambda db: db.execute("INSERT INTO backoff(host, until) VALUES (?, ?) ON CONFLICT(host) DO "
                                         "UPDATE SET until = MAX(until, excluded.until)", (host.lower(), float(until))))

    # ---- retention ------------------------------------------------------------------------------------------
    @staticmethod
    def _orphans(db) -> int:
        return db.execute("DELETE FROM documents WHERE sha256 NOT IN (SELECT sha256 FROM urls)").rowcount

    @staticmethod
    def _total_bytes(db) -> int:
        return db.execute("SELECT COALESCE(SUM(bytes), 0) FROM documents").fetchone()[0]

    def prune(self, now: float, *, max_bytes: int, max_age_seconds: float) -> dict:
        def prune(db):
            with db:
                db.execute("BEGIN IMMEDIATE")
                before = self._total_bytes(db)
                cutoff = now - max_age_seconds
                urls = db.execute("DELETE FROM urls WHERE fetched_at < ?", (cutoff,)).rowcount
                robots = db.execute("DELETE FROM robots WHERE fetched_at < ?", (cutoff,)).rowcount
                db.execute("DELETE FROM backoff WHERE until < ?", (now,))
                documents = self._orphans(db)
                total = self._total_bytes(db)
                if total > max_bytes:
                    rows = db.execute(
                        "SELECT d.sha256, d.bytes FROM documents d JOIN urls u ON u.sha256 = d.sha256 "
                        "GROUP BY d.sha256 ORDER BY MAX(u.fetched_at) ASC, d.sha256").fetchall()
                    victims = []
                    for sha, size in rows:
                        if total <= max_bytes:
                            break
                        victims.append(sha)
                        total -= size
                    for sha in victims:
                        urls += db.execute("DELETE FROM urls WHERE sha256 = ?", (sha,)).rowcount
                    documents += self._orphans(db)
                after = self._total_bytes(db)
            return {"removed_urls": urls, "removed_documents": documents, "removed_robots": robots,
                    "bytes_before": before, "bytes_after": after}
        report = self._call(prune)
        self._checkpoint()
        return report

    def maybe_prune(self, now: float, *, max_bytes: int, max_age_seconds: float, every: float = 3600.0):
        """`prune` at most once per `every` seconds (last run kept in `meta`); returns its report, or None."""
        row = self._call(lambda db: db.execute("SELECT value FROM meta WHERE key = 'last_prune'").fetchone())
        try:
            last = float(row[0]) if row else None
        except ValueError:
            last = None
        if last is not None and 0 <= now - last < every:
            return None
        report = self.prune(now, max_bytes=max_bytes, max_age_seconds=max_age_seconds)
        self._call(lambda db: db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('last_prune', ?)",
                                         (repr(float(now)),)))
        return report

    def forget_url(self, url: str) -> int:
        def forget(db):
            with db:
                db.execute("BEGIN IMMEDIATE")
                n = db.execute("DELETE FROM urls WHERE url = ? OR final_url = ? OR url = ?",
                               (url, url, str(url).split("#", 1)[0] + RENDERED_SUFFIX)).rowcount
                self._orphans(db)
            return n
        n = self._call(forget)
        self._checkpoint()
        return n

    def forget_domain(self, domain: str) -> int:
        domain = domain.lower().strip().rstrip(".")
        if not domain:
            return 0

        def forget(db):
            with db:
                db.execute("BEGIN IMMEDIATE")
                rows = db.execute("SELECT url, final_url FROM urls").fetchall()
                doomed = [url for url, final in rows
                          if _in_domain(_host_of(url), domain) or _in_domain(_host_of(final), domain)]
                n = 0
                for url in doomed:
                    n += db.execute("DELETE FROM urls WHERE url = ?", (url,)).rowcount
                hosts = [h for (h,) in db.execute("SELECT host FROM robots").fetchall() if _in_domain(h, domain)]
                for host in hosts:
                    n += db.execute("DELETE FROM robots WHERE host = ?", (host,)).rowcount
                for (host,) in db.execute("SELECT host FROM backoff").fetchall():
                    if _in_domain(host, domain):
                        db.execute("DELETE FROM backoff WHERE host = ?", (host,))
                self._orphans(db)
            return n
        n = self._call(forget)
        self._checkpoint()
        return n

    def _checkpoint(self):
        """Fold the WAL back so deleted bodies do not linger in the -wal file."""
        self._call(lambda db: db.execute("PRAGMA wal_checkpoint(TRUNCATE)"))

    def stats(self) -> dict:
        def stats(db):
            docs, size = db.execute("SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM documents").fetchone()
            urls = db.execute("SELECT COUNT(*) FROM urls").fetchone()[0]
            robots = db.execute("SELECT COUNT(*) FROM robots").fetchone()[0]
            oldest, newest = db.execute("SELECT MIN(fetched_at), MAX(fetched_at) FROM urls").fetchone()
            return docs, size, urls, robots, oldest, newest
        docs, size, urls, robots, oldest, newest = self._call(stats)
        out = {"path": str(self.path), "documents": docs, "urls": urls, "robots": robots, "bytes": size,
               "oldest_fetched_at": oldest, "newest_fetched_at": newest}
        if self.events:
            out["quarantined"] = [dict(e) for e in self.events]
        return out
