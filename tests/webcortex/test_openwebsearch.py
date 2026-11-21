"""open-webSearch provider against a fake loopback daemon emulating the 2.2.0 envelope; Daemon lifecycle."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sys
import textwrap
import threading
import time

import pytest

from neuroscifi.instance.errors import ValidationError
from neuroscifi.webcortex.providers.openwebsearch import (Daemon, NotLoopback, OpenWebSearchProvider,
                                                          loopback_base)
from neuroscifi.webcortex.search import ProviderError, ProviderRegistry
from neuroscifi.webcortex.types import Budget, Query, QueryPlan, Usage

DDG = [
    {"title": '<b>Dendritic</b> computation &amp; learning', "url": "https://example.org/dendrites",
     "description": "How <b>dendrites</b> compute in &lt;5 ms &amp; x &gt; y.", "source": "example.org", "engine": "duckduckgo"},
    {"title": '<span class="result__type">PDF</span> Active <b>dendrites</b> review',
     "url": "//duckduckgo.com/l/?uddg=https%3A%2F%2Fpapers.example.net%2Factive.pdf&rut=abc",
     "description": "A review.", "source": "papers.example.net", "engine": "duckduckgo"},
    {"title": "Third", "url": "https://example.com/3", "description": "three", "engine": "duckduckgo",
     "publishedDate": "2026-11-18T10:00:00Z"},
    {"title": "bad scheme", "url": "javascript:alert(1)", "description": "", "engine": "duckduckgo"},
]


class FakeDaemon:
    """Emulates POST /search, GET /health, GET /status of open-webSearch 2.2.0."""

    def __init__(self, engines):
        self.engines = engines          # engine -> list of results | ("fail", code, message) | ("http", status)
        self.requests = []
        daemon = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, payload):
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.path == "/health":
                    self._send(200, {"status": "ok", "data": {"daemon": "running"}, "error": None, "hint": None})
                elif self.path == "/status":
                    self._send(200, {"status": "ok", "error": None, "hint": None, "data": {
                        "daemon": "running", "version": "2.2.0", "capabilities": ["search", "fetch-web"],
                        "configSummary": {"defaultSearchEngine": "bing", "allowedSearchEngines": [],
                                          "searchMode": "request", "effectiveSearchMode": "request",
                                          "playwrightAvailable": False}}})
                else:
                    self._send(404, {"status": "error", "data": None,
                                     "error": {"code": "not_found", "message": "nope", "retryable": False}})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                daemon.requests.append({"path": self.path, "body": body})
                if self.path != "/search":
                    return self._send(404, {"status": "error", "data": None, "error": {"code": "not_found"}})
                engine = body["engines"][0]
                spec = daemon.engines.get(engine, [])
                if isinstance(spec, tuple) and spec[0] == "http":
                    return self._send(spec[1], {"status": "error", "data": None, "hint": "x", "error": {
                        "code": "invalid_request", "message": "limit must be an integer", "retryable": False}})
                failures, results = [], []
                if isinstance(spec, tuple):
                    failures.append({"engine": engine, "code": spec[1], "message": spec[2]})
                else:
                    results = spec[: body["limit"]]
                self._send(200, {"status": "ok", "error": None, "hint": None, "data": {
                    "query": body["query"], "engines": [engine], "totalResults": len(results), "results": results,
                    "partialFailures": failures}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def engines_called(self):
        return [r["body"]["engines"][0] for r in self.requests]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def daemon():
    d = FakeDaemon({})
    yield d
    d.close()


class Clock:
    def __init__(self):
        self.t = 5_000_000.0

    def __call__(self):
        return self.t


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---- URL policy ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("url", ["https://127.0.0.1:3210", "http://example.com:3210", "http://10.0.0.5:3210",
                                 "http://127.0.0.1:3210/search", "http://user@127.0.0.1:3210",
                                 "http://0.0.0.0:3210", "http://127.0.0.2:3210", "http://localhost.evil.com",
                                 "ftp://localhost", "http://127.0.0.1:3210/?x=1", "http://[::ffff:127.0.0.1]:1"])
def test_non_loopback_urls_refused(url):
    with pytest.raises(ValueError):
        OpenWebSearchProvider(url, ["duckduckgo"])
    with pytest.raises(ValidationError):
        loopback_base(url)


def test_loopback_urls_accepted():
    assert loopback_base("http://127.0.0.1:3210/") == "http://127.0.0.1:3210"
    assert loopback_base("http://LOCALHOST:8") == "http://localhost:8"
    assert loopback_base("http://[::1]:3210") == "http://[::1]:3210"
    assert issubclass(NotLoopback, ValueError)


# ---- search ----------------------------------------------------------------------------------------------------

def test_engine_fallback_html_stripping_and_attempts(daemon):
    daemon.engines = {"brave": ("fail", "engine_error", "Request failed with status code 429"),
                      "startpage": [], "duckduckgo": DDG, "bing": [{"title": "b", "url": "https://b.org/"}]}
    p = OpenWebSearchProvider(daemon.url, ["brave", "startpage", "duckduckgo", "bing"])
    results = p.search(Query("dendritic computation"), 3)
    assert daemon.engines_called() == ["brave", "startpage", "duckduckgo"]     # stopped once 3 gathered
    assert [r.url for r in results] == ["https://example.org/dendrites", "https://papers.example.net/active.pdf",
                                        "https://example.com/3"]
    assert results[0].title == "Dendritic computation & learning"
    assert results[0].snippet == "How dendrites compute in <5 ms & x > y."
    assert results[1].title == "Active dendrites review" and results[1].metadata["filetype"] == "PDF"
    assert results[2].published_at == 1795132800.0 - 2 * 86400 + 10 * 3600
    assert [r.rank for r in results] == [1, 2, 3] and {r.engine for r in results} == {"duckduckgo"}
    attempts = p.last_attempts
    assert [(a["engine"], a["ok"], a["n"]) for a in attempts] == [("brave", False, 0), ("startpage", True, 0),
                                                                  ("duckduckgo", True, 3)]
    assert attempts[0]["error"] == "engine_error: Request failed with status code 429"
    snap = p.engine_snapshot()
    assert snap["brave"]["failures"] == 1 and snap["startpage"]["failures"] == 1   # empty while ddg answered
    assert snap["duckduckgo"]["successes"] == 1 and snap["bing"]["successes"] == 0


def test_request_body_is_bounded_and_site_applied(daemon):
    daemon.engines = {"duckduckgo": DDG}
    p = OpenWebSearchProvider(daemon.url, ["duckduckgo"])
    results = p.search(Query("x" * 2000, site="arxiv.org"), 50)
    assert len(results) == 3 and all(r.url.startswith("https://") for r in results)   # javascript: dropped
    body = daemon.requests[0]["body"]
    assert len(body["query"]) == 512 and body["limit"] == 20
    assert body["engines"] == ["duckduckgo"] and body["searchMode"] == "request"
    p.search(Query("dendrites", site="arxiv.org"), 5)
    assert daemon.requests[1]["body"]["query"] == "dendrites site:arxiv.org"


def test_all_empty_is_neutral(daemon):
    daemon.engines = {"duckduckgo": [], "brave": []}
    p = OpenWebSearchProvider(daemon.url, ["duckduckgo", "brave"])
    assert p.search(Query("zzqx obscure"), 5) == []
    snap = p.engine_snapshot()
    assert snap["duckduckgo"]["failures"] == 0 and snap["brave"]["failures"] == 0


def test_engine_breaker_opens_and_is_skipped(daemon):
    clock = Clock()
    daemon.engines = {"brave": ("fail", "engine_error", "429"), "duckduckgo": DDG[:1]}
    p = OpenWebSearchProvider(daemon.url, ["brave", "duckduckgo"], clock=clock, failure_threshold=2, cooldown=60)
    for _ in range(2):
        p.search(Query("q"), 5)
    assert p.engine_snapshot()["brave"]["state"] == "open"
    daemon.requests.clear()
    p.search(Query("q"), 5)
    assert daemon.engines_called() == ["duckduckgo"]
    assert p.last_attempts[0] == {"engine": "brave", "ok": False, "n": 0, "error": "circuit_open", "latency": 0.0}
    clock.t += 60
    daemon.engines["brave"] = DDG[:1]
    daemon.requests.clear()
    p.search(Query("q"), 1)
    assert daemon.engines_called() == ["brave"] and p.engine_snapshot()["brave"]["state"] == "closed"


def test_every_engine_failing_raises_with_details(daemon):
    daemon.engines = {"brave": ("fail", "engine_error", "429"), "bing": ("fail", "engine_error",
                                                                          "regional redirect limit")}
    p = OpenWebSearchProvider(daemon.url, ["brave", "bing"])
    with pytest.raises(ProviderError) as err:
        p.search(Query("q"), 5)
    assert err.value.code == "engines_failed" and "regional redirect limit" in err.value.detail
    assert len(p.last_attempts) == 2


def test_error_envelope_is_an_engine_failure(daemon):
    daemon.engines = {"brave": ("http", 400), "duckduckgo": DDG[:1]}
    p = OpenWebSearchProvider(daemon.url, ["brave", "duckduckgo"])
    assert len(p.search(Query("q"), 5)) == 1
    assert p.last_attempts[0]["error"] == "invalid_request: limit must be an integer"


def test_daemon_unreachable():
    p = OpenWebSearchProvider(f"http://127.0.0.1:{free_port()}", ["duckduckgo", "brave"], timeout=2)
    with pytest.raises(ProviderError) as err:
        p.search(Query("q"), 5)
    assert err.value.code == "daemon_unreachable"
    assert p.engine_snapshot()["duckduckgo"]["failures"] == 0     # the daemon is down, not the engine
    health = p.health()
    assert health["reachable"] is False and health["error"]


def test_budget_charges_requests(daemon):
    daemon.engines = {"brave": ("fail", "engine_error", "429"), "duckduckgo": DDG[:1]}
    p = OpenWebSearchProvider(daemon.url, ["brave", "duckduckgo"])
    usage = Usage()
    p.search(Query("q"), 5, budget=Budget(), usage=usage)
    assert usage.requests == 2
    with pytest.raises(ProviderError) as err:
        p.search(Query("q"), 5, budget=Budget(max_requests=2), usage=usage)
    assert err.value.code == "budget_exhausted" and daemon.engines_called() == ["brave", "duckduckgo"]


def test_health(daemon):
    h = OpenWebSearchProvider(daemon.url, ["duckduckgo"]).health()
    assert h["reachable"] is True and h["version"] == "2.2.0" and h["playwright"] is False
    assert h["engines_default"] == "bing" and h["search_mode"] == "request"


def test_registry_sees_engine_attempts_and_status(daemon):
    daemon.engines = {"brave": ("fail", "engine_error", "429"), "startpage": [], "duckduckgo": DDG}
    p = OpenWebSearchProvider(daemon.url, ["brave", "startpage", "duckduckgo"])
    reg = ProviderRegistry([p])
    out = reg.search(QueryPlan(queries=(Query("dendritic computation"),)), budget=Budget(), usage=Usage())
    assert len(out.results) == 3
    engines = [(a["engine"], a["ok"]) for a in out.attempts]
    assert engines == [("brave", False), ("startpage", True), ("duckduckgo", True), (None, True)]
    assert {f["engine"] for f in out.failures} == {"brave", "startpage"}
    names = {s["name"]: s for s in reg.status()}
    assert names["openwebsearch:brave"]["last_error"].startswith("engine_error")
    assert names["openwebsearch"]["successes"] == 1


# ---- Daemon lifecycle ------------------------------------------------------------------------------------------

FAKE_ENTRY = textwrap.dedent("""
    import json, sys
    from http.server import BaseHTTPRequestHandler, HTTPServer
    args = sys.argv[1:]
    host, port = args[args.index("--host") + 1], int(args[args.index("--port") + 1])
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            data = {"daemon": "running", "version": "fake", "configSummary": {"defaultSearchEngine": "bing"}}
            raw = json.dumps({"status": "ok", "data": data, "error": None}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(raw))); self.end_headers()
            self.wfile.write(raw)
    print("fake daemon on", port, flush=True)
    HTTPServer((host, port), H).serve_forever()
""")


@pytest.fixture
def fake_package(tmp_path):
    entry = tmp_path / "pkg" / "node_modules" / "open-websearch" / "build" / "index.js"
    entry.parent.mkdir(parents=True)
    entry.write_text(FAKE_ENTRY)
    return tmp_path / "pkg"


def test_daemon_refuses_non_loopback_host(tmp_path):
    for host in ("0.0.0.0", "192.168.1.4", "example.com", "::"):
        with pytest.raises(ValueError):
            Daemon(host=host, package_dir=tmp_path)


def test_daemon_missing_install(tmp_path):
    with pytest.raises(ValidationError, match="not installed"):
        Daemon(package_dir=tmp_path, port=free_port(), state_dir=tmp_path / "s").start()


def test_daemon_start_status_stop(fake_package, tmp_path):
    d = Daemon(node=sys.executable, package_dir=fake_package, port=free_port(), state_dir=tmp_path / "state")
    started = d.start(timeout=10)
    try:
        assert started["started"] is True and started["running"] is True and started["owned"] is True
        assert started["version"] == "fake"
        assert d.pid_path.read_text() == str(started["pid"])
        assert d.start()["started"] is False                       # already running: not started twice
        other = Daemon(node=sys.executable, package_dir=fake_package, port=d.port, state_dir=tmp_path / "state")
        assert other.status()["pid"] == started["pid"] and other.status()["owned"] is False
        stopped = other.stop()                                     # pid file + cmdline check, no pattern kill
        assert stopped == {"stopped": True, "pid": started["pid"]}
        deadline = time.monotonic() + 5
        while d.status()["running"] and time.monotonic() < deadline:
            time.sleep(0.1)
        assert d.status()["running"] is False and not d.pid_path.exists()
        assert "fake daemon on" in d.log_path.read_text()
    finally:
        d.stop()


def test_daemon_stop_never_kills_unrelated_pid(fake_package, tmp_path):
    d = Daemon(node=sys.executable, package_dir=fake_package, port=free_port(), state_dir=tmp_path)
    d.pid_path.write_text(str(os.getpid()))                        # stale pid file pointing at this test process
    assert d.stop()["stopped"] is False


def test_daemon_start_failure_reports_log(tmp_path):
    entry = tmp_path / "pkg" / "node_modules" / "open-websearch" / "build" / "index.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("import sys\nprint('boom: missing module', file=sys.stderr)\nsys.exit(3)\n")
    d = Daemon(node=sys.executable, package_dir=tmp_path / "pkg", port=free_port(), state_dir=tmp_path / "s")
    with pytest.raises(ProviderError, match="boom: missing module"):
        d.start(timeout=10)


# ---- live --------------------------------------------------------------------------------------------------------

@pytest.mark.live
def test_live_daemon_search(tmp_path):
    d = Daemon(port=free_port(), state_dir=tmp_path)
    if not d.entry.is_file():
        pytest.fail(f"open-webSearch not installed at {d.entry}")
    d.start(timeout=15)
    try:
        p = OpenWebSearchProvider(d.base_url, ["duckduckgo", "brave", "startpage", "bing"], timeout=25)
        health = p.health()
        assert health["reachable"] and health["version"]
        reg = ProviderRegistry([p])
        out = reg.search(QueryPlan(queries=(Query("dendritic computation"),)), budget=Budget(), usage=Usage())
        engine_attempts = [a for a in out.attempts if a["engine"]]
        print("\nLIVE openwebsearch attempts:", json.dumps(engine_attempts, indent=1))
        print("LIVE results:", [(r.engine, r.url) for r in out.results[:5]])
        assert len(out.results) >= 1                      # some engine answered
        assert engine_attempts                             # per-engine outcomes are reported, never raised
        assert all({"engine", "ok", "n", "error", "latency"} <= set(a) for a in engine_attempts)
        # fragile engines first: their failures must surface as attempts while duckduckgo still answers
        p2 = OpenWebSearchProvider(d.base_url, ["brave", "startpage", "bing", "duckduckgo"], timeout=25)
        out2 = ProviderRegistry([p2]).search(QueryPlan(queries=(Query("dendritic computation"),)),
                                             budget=Budget(), usage=Usage())
        engine_attempts = [a for a in out2.attempts if a["engine"]]
        print("LIVE fragile-first attempts:", [(a["engine"], a["ok"], a["n"], (a["error"] or "")[:80])
                                               for a in engine_attempts])
        assert len(engine_attempts) >= 2 and len(out2.results) >= 1
        assert {a["engine"] for a in out2.failures if a["engine"]}   # at least one engine failure, reported
    finally:
        d.stop()
        assert not Path(d.pid_path).exists()
