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
- Thread-safe: one connection guarded by a lock. Times are passed in (`fetched_at`, `now`); nothing reads a clock.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
import threading
from urllib.parse import urlsplit

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


class PublicCache:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        old = os.umask(0o077)
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            os.close(fd)
            os.chmod(self.path, 0o600)
            self._db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False, timeout=30)
            self._db.execute("PRAGMA journal_mode=WAL")
        finally:
            os.umask(old)
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA secure_delete=ON")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def close(self):
        with self._lock:
            self._db.close()

    # ---- documents and URLs ---------------------------------------------------------------------------------
    def lookup(self, url: str) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT u.final_url, u.status, d.content_type, u.sha256, u.etag, u.last_modified, u.fetched_at "
                "FROM urls u JOIN documents d ON d.sha256 = u.sha256 WHERE u.url = ?", (url,)).fetchone()
        if row is None:
            return None
        keys = ("final_url", "status", "content_type", "sha256", "etag", "last_modified", "fetched_at")
        return dict(zip(keys, row))

    def put(self, result) -> str:
        """Store a FetchResult body (content-addressed) and its URL rows; returns the body's sha256."""
        body = bytes(result.body)
        sha = hashlib.sha256(body).hexdigest()
        headers = result.headers or {}
        etag, modified = headers.get("etag"), headers.get("last-modified")
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._db.execute(
                "INSERT OR IGNORE INTO documents(sha256, body, content_type, bytes, first_seen) VALUES (?,?,?,?,?)",
                (sha, body, result.content_type or "", len(body), result.retrieved_at))
            for url in dict.fromkeys((result.url, result.final_url)):
                self._db.execute(
                    "INSERT OR REPLACE INTO urls(url, final_url, status, sha256, etag, last_modified, fetched_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (url, result.final_url, int(result.status), sha, etag, modified, result.retrieved_at))
        return sha

    def body(self, sha256: str) -> bytes | None:
        with self._lock:
            row = self._db.execute("SELECT body FROM documents WHERE sha256 = ?", (sha256,)).fetchone()
        return bytes(row[0]) if row else None

    # ---- robots.txt -----------------------------------------------------------------------------------------
    def robots(self, host: str):
        with self._lock:
            row = self._db.execute("SELECT text, fetched_at FROM robots WHERE host = ?",
                                   (host.lower(),)).fetchone()
        return (row[0], row[1]) if row else None

    def put_robots(self, host: str, text: str, fetched_at: float):
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO robots(host, text, fetched_at) VALUES (?,?,?)",
                             (host.lower(), text, fetched_at))

    # ---- Retry-After backoff ----------------------------------------------------------------------------------
    def backoff(self, host: str) -> float | None:
        """Time until which requests to host must not be sent, or None."""
        with self._lock:
            row = self._db.execute("SELECT until FROM backoff WHERE host = ?", (host.lower(),)).fetchone()
        return row[0] if row else None

    def put_backoff(self, host: str, until: float):
        """Record a backoff; an earlier `until` never shortens one already stored."""
        with self._lock:
            self._db.execute("INSERT INTO backoff(host, until) VALUES (?, ?) ON CONFLICT(host) DO UPDATE SET "
                             "until = MAX(until, excluded.until)", (host.lower(), float(until)))

    # ---- retention ------------------------------------------------------------------------------------------
    def _orphans(self) -> int:
        return self._db.execute(
            "DELETE FROM documents WHERE sha256 NOT IN (SELECT sha256 FROM urls)").rowcount

    def _total_bytes(self) -> int:
        return self._db.execute("SELECT COALESCE(SUM(bytes), 0) FROM documents").fetchone()[0]

    def prune(self, now: float, *, max_bytes: int, max_age_seconds: float) -> dict:
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            before = self._total_bytes()
            cutoff = now - max_age_seconds
            urls = self._db.execute("DELETE FROM urls WHERE fetched_at < ?", (cutoff,)).rowcount
            robots = self._db.execute("DELETE FROM robots WHERE fetched_at < ?", (cutoff,)).rowcount
            self._db.execute("DELETE FROM backoff WHERE until < ?", (now,))
            documents = self._orphans()
            total = self._total_bytes()
            if total > max_bytes:
                rows = self._db.execute(
                    "SELECT d.sha256, d.bytes FROM documents d JOIN urls u ON u.sha256 = d.sha256 "
                    "GROUP BY d.sha256 ORDER BY MAX(u.fetched_at) ASC, d.sha256").fetchall()
                victims = []
                for sha, size in rows:
                    if total <= max_bytes:
                        break
                    victims.append(sha)
                    total -= size
                for sha in victims:
                    urls += self._db.execute("DELETE FROM urls WHERE sha256 = ?", (sha,)).rowcount
                documents += self._orphans()
            after = self._total_bytes()
        self._checkpoint()
        return {"removed_urls": urls, "removed_documents": documents, "removed_robots": robots,
                "bytes_before": before, "bytes_after": after}

    def maybe_prune(self, now: float, *, max_bytes: int, max_age_seconds: float, every: float = 3600.0):
        """`prune` at most once per `every` seconds (last run kept in `meta`); returns its report, or None."""
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = 'last_prune'").fetchone()
        try:
            last = float(row[0]) if row else None
        except ValueError:
            last = None
        if last is not None and 0 <= now - last < every:
            return None
        report = self.prune(now, max_bytes=max_bytes, max_age_seconds=max_age_seconds)
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('last_prune', ?)", (repr(float(now)),))
        return report

    def forget_url(self, url: str) -> int:
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            n = self._db.execute("DELETE FROM urls WHERE url = ? OR final_url = ?", (url, url)).rowcount
            self._orphans()
        self._checkpoint()
        return n

    def forget_domain(self, domain: str) -> int:
        domain = domain.lower().strip().rstrip(".")
        if not domain:
            return 0
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            rows = self._db.execute("SELECT url, final_url FROM urls").fetchall()
            doomed = [url for url, final in rows
                      if _in_domain(_host_of(url), domain) or _in_domain(_host_of(final), domain)]
            n = 0
            for url in doomed:
                n += self._db.execute("DELETE FROM urls WHERE url = ?", (url,)).rowcount
            hosts = [h for (h,) in self._db.execute("SELECT host FROM robots").fetchall() if _in_domain(h, domain)]
            for host in hosts:
                n += self._db.execute("DELETE FROM robots WHERE host = ?", (host,)).rowcount
            for (host,) in self._db.execute("SELECT host FROM backoff").fetchall():
                if _in_domain(host, domain):
                    self._db.execute("DELETE FROM backoff WHERE host = ?", (host,))
            self._orphans()
        self._checkpoint()
        return n

    def _checkpoint(self):
        """Fold the WAL back so deleted bodies do not linger in the -wal file."""
        with self._lock:
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def stats(self) -> dict:
        with self._lock:
            docs, size = self._db.execute("SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM documents").fetchone()
            urls = self._db.execute("SELECT COUNT(*) FROM urls").fetchone()[0]
            robots = self._db.execute("SELECT COUNT(*) FROM robots").fetchone()[0]
            oldest, newest = self._db.execute("SELECT MIN(fetched_at), MAX(fetched_at) FROM urls").fetchone()
        return {"path": str(self.path), "documents": docs, "urls": urls, "robots": robots, "bytes": size,
                "oldest_fetched_at": oldest, "newest_fetched_at": newest}
