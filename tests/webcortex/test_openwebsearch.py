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
    import json, os, sys
    from http.server import BaseHTTPRequestHandler, HTTPServer
    args = sys.argv[1:]
    host, port = args[args.index("--host") + 1], int(args[args.index("--port") + 1])
    print("env:", " ".join(sorted(os.environ)), flush=True)
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            data = {"daemon": "running", "version": "2.2.0", "configSummary": {"defaultSearchEngine": "bing"}}
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
    started = d.start(timeout=10, allow_browser_user_agent=True)
    try:
        assert started["started"] is True and started["running"] is True and started["owned"] is True
        assert started["version"] == "2.2.0" and started["honest_user_agent"] is False
        assert d.pid_path.read_text().split()[0] == str(started["pid"])
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
        d.start(timeout=10, allow_browser_user_agent=True)


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


# ---- review remediation (V1): honest User-Agent, child environment, redirects, pid reuse, port squatting -------

import shutil  # noqa: E402
import subprocess  # noqa: E402

from neuroscifi.webcortex import config as wc_config  # noqa: E402
from neuroscifi.webcortex.providers import openwebsearch as ows  # noqa: E402

PATCH_SCRIPT = ows.DEFAULT_PACKAGE_DIR / "apply-honest-ua.mjs"
CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{v}.0.0.0 Safari/537.36"
MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
ENGINE_FILES = {   # the forged strings of open-webSearch 2.2.0, as the patch script expects them
    "build/engines/duckduckgo/searchDuckDuckGo.js":
        f'const h = {{"User-Agent": "{CHROME.format(v=112)}"}};\nconst a = {{\'User-Agent\': \'Apifox/1.0.0 '
        f'(https://apifox.com)\'}};\n',
    "build/engines/startpage/startpage.js": f"const h = {{'User-Agent': '{CHROME.format(v=136)}'}};\n",
    "build/engines/brave/brave.js": f'const h = {{"User-Agent": "{CHROME.format(v=112)}"}};\n',
    "build/engines/bing/bing.js": f"const B = '{MAC}';\nconst h = {{'User-Agent': '{CHROME.format(v=133)}'}};\n",
    "build/engines/sogou/sogou.js": f"const h = {{'User-Agent': '{CHROME.format(v=136)}'}};\n",
}


def fake_engines(package, version="2.2.0"):
    root = package / "node_modules" / "open-websearch"
    (root / "package.json").write_text(json.dumps({"name": "open-websearch", "version": version}))
    for rel, text in ENGINE_FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def run_patch(package):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    return subprocess.run([node, str(PATCH_SCRIPT), str(package)], capture_output=True, text=True, timeout=60)


def test_patch_script_user_agent_is_the_web_cortex_user_agent():
    assert f'export const USER_AGENT = "{wc_config.USER_AGENT}";' in PATCH_SCRIPT.read_text()
    package_json = json.loads((ows.DEFAULT_PACKAGE_DIR / "package.json").read_text())
    assert package_json["scripts"]["patch-ua"] == "node apply-honest-ua.mjs"


def test_patch_script_replaces_forged_user_agents_and_is_verified(fake_package):
    root = fake_engines(fake_package)
    assert ows.honest_user_agent(fake_package) is False
    first = run_patch(fake_package)
    assert first.returncode == 0, first.stderr
    for rel in ENGINE_FILES:
        text = (root / rel).read_text()
        assert "Mozilla/5.0" not in text and "Apifox" not in text and wc_config.USER_AGENT in text
    assert ows.honest_user_agent(fake_package) is True
    marker = (root / ".neuroscifi-honest-ua.json").read_bytes()
    second = run_patch(fake_package)                                  # idempotent
    assert second.returncode == 0 and "already patched" in second.stdout
    assert (root / ".neuroscifi-honest-ua.json").read_bytes() == marker
    bing = root / "build/engines/bing/bing.js"
    bing.write_text(bing.read_text() + f"// {MAC}\n")                 # tampered after patching
    assert ows.honest_user_agent(fake_package) is False


def test_patch_script_refuses_other_versions_and_moved_strings(fake_package, tmp_path):
    fake_engines(fake_package, version="2.3.0")
    out = run_patch(fake_package)
    assert out.returncode == 1 and "2.2.0" in out.stderr
    root = fake_engines(fake_package)
    (root / "build/engines/brave/brave.js").write_text("const h = {};\n")
    out = run_patch(fake_package)
    assert out.returncode == 1 and "brave.js" in out.stderr
    assert not (root / ".neuroscifi-honest-ua.json").exists()


def test_daemon_refuses_unpatched_build_unless_explicitly_allowed(fake_package, tmp_path):
    d = Daemon(node=sys.executable, package_dir=fake_package, port=free_port(), state_dir=tmp_path / "s")
    try:
        with pytest.raises(ValidationError, match="patch-ua"):
            d.start(timeout=10)
        assert not d.pid_path.exists()
        fake_engines(fake_package)
        assert run_patch(fake_package).returncode == 0
        started = d.start(timeout=10)                                 # patched: no override needed
        assert started["started"] is True and started["honest_user_agent"] is True
    finally:
        d.stop()


def test_daemon_child_environment_is_minimal(fake_package, tmp_path, monkeypatch):
    monkeypatch.setenv("NSF_TEST_SECRET_TOKEN", "do-not-leak")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "do-not-leak")
    monkeypatch.setenv("NODE_OPTIONS", "--max-old-space-size=256")
    monkeypatch.setenv("OPEN_WEBSEARCH_DAEMON_TIMEOUT_MS", "1000")
    d = Daemon(node=sys.executable, package_dir=fake_package, port=free_port(), state_dir=tmp_path / "s")
    d.start(timeout=10, env={"USE_PROXY": "false"}, allow_browser_user_agent=True)
    try:
        names = set(d.log_path.read_text().split("env:", 1)[1].splitlines()[0].split())
    finally:
        d.stop()
    assert "NSF_TEST_SECRET_TOKEN" not in names and "AWS_SECRET_ACCESS_KEY" not in names
    assert {"PATH", "NODE_OPTIONS", "OPEN_WEBSEARCH_DAEMON_TIMEOUT_MS", "USE_PROXY", "ENABLE_CORS",
            "OPEN_WEBSEARCH_QUIET_STARTUP"} <= names


def test_loopback_client_never_follows_redirects():
    target = FakeDaemon({})
    hits = []

    class Redirect(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits.append(self.path)
            raw = b"{}"
            self.send_response(302)
            self.send_header("Location", target.url + "/health")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        status, payload = ows._http_json("GET", f"http://127.0.0.1:{server.server_address[1]}/health", None, 2.0)
        assert status == 302 and payload == {} and hits == ["/health"]
        assert target.requests == []
    finally:
        server.shutdown()
        server.server_close()
        target.close()


def test_recycled_pid_is_never_trusted_or_signalled(fake_package, tmp_path):
    d = Daemon(node=sys.executable, package_dir=fake_package, port=free_port(), state_dir=tmp_path / "s")
    started = d.start(timeout=10, allow_browser_user_agent=True)
    try:
        pid, start_time = d.pid_path.read_text().split()
        assert pid == str(started["pid"]) and start_time == ows._starttime(started["pid"])
        d.pid_path.write_text(f"{pid} 1")                             # same pid number, different process start
        other = Daemon(node=sys.executable, package_dir=fake_package, port=d.port, state_dir=tmp_path / "s")
        assert other.status()["pid"] is None and other.status()["running"] is False
        assert other.stop()["stopped"] is False and ows._alive(started["pid"])
    finally:
        d.stop()


def test_port_squatter_is_not_trusted(daemon, tmp_path, fake_package):
    port = int(daemon.url.rsplit(":", 1)[1])                          # answers /status as open-webSearch 2.2.0
    d = Daemon(node=sys.executable, package_dir=fake_package, port=port, state_dir=tmp_path / "s")
    status = d.status()
    assert status["running"] is False and status["port_answering"] is True and status["pid"] is None
    with pytest.raises(ProviderError) as info:
        d.start(timeout=5, allow_browser_user_agent=True)
    assert info.value.code == "port_in_use"
    d.pid_path.parent.mkdir(parents=True, exist_ok=True)
    d.pid_path.write_text(f"{os.getpid()} {ows._starttime(os.getpid())}")   # a recorded pid that is not listening
    assert d.status()["running"] is False


def test_provider_health_reports_honest_user_agent(daemon, fake_package):
    p = OpenWebSearchProvider(daemon.url, ["duckduckgo"], package_dir=fake_package)
    assert p.health()["honest_user_agent"] is False
    fake_engines(fake_package)
    assert run_patch(fake_package).returncode == 0
    assert p.health()["honest_user_agent"] is True
