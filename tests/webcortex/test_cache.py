"""PublicCache: file mode, content addressing, validators, robots, pruning and forgetting."""
import hashlib
import os
import sqlite3
import stat

from neuroscifi.webcortex.cache import PublicCache
from neuroscifi.webcortex.types import FetchResult


def result(url, body, *, at=1000.0, final=None, etag=None, modified=None, content_type="text/html"):
    headers = {}
    if etag:
        headers["etag"] = etag
    if modified:
        headers["last-modified"] = modified
    return FetchResult(url=url, final_url=final or url, status=200, headers=headers, content_type=content_type,
                       body=body, retrieved_at=at)


def test_file_is_private_and_wal(tmp_path):
    cache = PublicCache(tmp_path / "sub" / "x.webcache.sqlite")
    cache.put(result("http://a.example/", b"body"))
    assert stat.S_IMODE(os.stat(cache.path).st_mode) == 0o600
    for suffix in ("-wal", "-shm"):
        side = str(cache.path) + suffix
        if os.path.exists(side):
            assert stat.S_IMODE(os.stat(side).st_mode) & 0o077 == 0
    mode = sqlite3.connect(cache.path).execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
    tables = {r[0] for r in sqlite3.connect(cache.path).execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"documents", "urls", "robots", "backoff", "meta"}


def test_put_lookup_body_content_addressed(tmp_path):
    cache = PublicCache(tmp_path / "c.sqlite")
    sha = cache.put(result("http://a.example/x", b"same", final="http://a.example/y", etag='"e"',
                           modified="Tue, 03 Nov 2026 10:00:00 GMT"))
    assert sha == hashlib.sha256(b"same").hexdigest()
    cache.put(result("http://b.example/z", b"same"))
    row = cache.lookup("http://a.example/x")
    assert row == {"final_url": "http://a.example/y", "status": 200, "content_type": "text/html", "sha256": sha,
                   "etag": '"e"', "last_modified": "Tue, 03 Nov 2026 10:00:00 GMT", "fetched_at": 1000.0}
    assert cache.lookup("http://a.example/y")["sha256"] == sha        # final URL indexed too
    assert cache.body(sha) == b"same" and cache.body("0" * 64) is None
    stats = cache.stats()
    assert stats["documents"] == 1 and stats["urls"] == 3 and stats["bytes"] == 4
    assert cache.lookup("http://missing.example/") is None


def test_robots_roundtrip(tmp_path):
    cache = PublicCache(tmp_path / "c.sqlite")
    assert cache.robots("a.example") is None
    cache.put_robots("A.example", "User-agent: *\n", 50.0)
    assert cache.robots("a.example") == ("User-agent: *\n", 50.0)


def test_prune_by_age_and_size_lru(tmp_path):
    cache = PublicCache(tmp_path / "c.sqlite")
    cache.put(result("http://a.example/old", b"o" * 100, at=0.0))
    cache.put(result("http://a.example/mid", b"m" * 100, at=500.0))
    cache.put(result("http://a.example/new", b"n" * 100, at=900.0))
    cache.put_robots("a.example", "", 0.0)
    report = cache.prune(1000.0, max_bytes=10_000, max_age_seconds=600)
    assert report["removed_urls"] == 1 and report["removed_documents"] == 1 and report["removed_robots"] == 1
    assert cache.lookup("http://a.example/old") is None and cache.lookup("http://a.example/mid")
    report = cache.prune(1000.0, max_bytes=150, max_age_seconds=10_000)
    assert report["bytes_after"] == 100 and cache.lookup("http://a.example/mid") is None
    assert cache.lookup("http://a.example/new") is not None


def test_forget_url_and_domain(tmp_path):
    cache = PublicCache(tmp_path / "c.sqlite")
    sha = cache.put(result("http://a.example/1", b"one"))
    cache.put(result("http://sub.a.example/2", b"two"))
    cache.put(result("http://b.example/3", b"three"))
    cache.put(result("http://nota.example/4", b"four"))
    cache.put_robots("sub.a.example", "", 1.0)
    assert cache.forget_url("http://a.example/1") == 1
    assert cache.lookup("http://a.example/1") is None and cache.body(sha) is None
    assert cache.forget_domain("a.example") == 2      # one URL row + one robots row
    assert cache.lookup("http://sub.a.example/2") is None and cache.robots("sub.a.example") is None
    assert cache.lookup("http://nota.example/4") is not None and cache.lookup("http://b.example/3") is not None
    assert cache.forget_domain("") == 0
    raw = (tmp_path / "c.sqlite").read_bytes()
    assert b"one" not in raw.replace(b"none", b"")  # secure_delete + checkpoint: forgotten body not left behind


def test_maybe_prune_runs_at_most_once_per_interval(tmp_path):
    cache = PublicCache(tmp_path / "c.sqlite")
    limits = {"max_bytes": 10_000, "max_age_seconds": 600}
    cache.put(result("http://a.example/old", b"o" * 100, at=0.0))
    first = cache.maybe_prune(10_000.0, **limits)
    assert first["removed_urls"] == 1 and cache.lookup("http://a.example/old") is None
    cache.put(result("http://a.example/old2", b"p" * 100, at=0.0))
    assert cache.maybe_prune(13_599.0, **limits) is None             # inside `every` (3600 s): nothing runs
    assert cache.lookup("http://a.example/old2") is not None
    again = PublicCache(tmp_path / "c.sqlite")                       # the last prune time lives in the file
    assert again.maybe_prune(13_599.0, **limits) is None
    assert again.maybe_prune(13_600.0, **limits)["removed_urls"] == 1
    assert again.maybe_prune(5_000.0, **limits) is not None          # a clock that went backwards still prunes
    assert again.maybe_prune(5_001.0, every=0, **limits) is not None


def test_backoff_rows_keep_the_longest_and_expire(tmp_path):
    cache = PublicCache(tmp_path / "c.sqlite")
    assert cache.backoff("busy.example") is None
    cache.put_backoff("Busy.example", 200.0)
    cache.put_backoff("busy.example", 150.0)                         # never shortened
    assert cache.backoff("busy.example") == 200.0
    cache.put_backoff("sub.slow.example", 500.0)
    cache.prune(300.0, max_bytes=10**9, max_age_seconds=10**9)
    assert cache.backoff("busy.example") is None and cache.backoff("sub.slow.example") == 500.0
    cache.forget_domain("slow.example")
    assert cache.backoff("sub.slow.example") is None


def test_forget_deletes_only_orphaned_bodies(tmp_path):
    cache = PublicCache(tmp_path / "c.sqlite")
    shared = cache.put(result("http://a.example/1", b"shared body"))
    cache.put(result("http://b.example/2", b"shared body"))
    moved = cache.put(result("http://c.example/old", b"moved body", final="http://c.example/new"))
    assert cache.forget_url("http://a.example/1") == 1
    assert cache.body(shared) == b"shared body"                      # b.example still references it
    cache.forget_domain("b.example")
    assert cache.body(shared) is None
    cache.forget_url("http://c.example/old")
    assert cache.body(moved) == b"moved body"                        # the final URL's row still references it
    cache.forget_url("http://c.example/new")
    assert cache.body(moved) is None and cache.stats()["documents"] == 0
