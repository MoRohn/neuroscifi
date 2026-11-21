"""SearXNG provider (ADR-034) against a fake loopback SearXNG: normalisation, engine health, breakers, fallback to
open-webSearch, intent routing, cache, budget, health verdict, config wiring and the container/patch helpers.
Offline: nothing here contacts a real SearXNG or the network."""
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from neuroscifi.instance.errors import ValidationError
from neuroscifi.webcortex import providers, state
from neuroscifi.webcortex import cli as wcli
from neuroscifi.webcortex.config import WebConfig
from neuroscifi.webcortex.intent import searxng_route
from neuroscifi.webcortex.providers import searxng as sx
from neuroscifi.webcortex.providers.openwebsearch import NotLoopback
from neuroscifi.webcortex.search import ProviderError, ProviderRegistry
from neuroscifi.webcortex.types import Budget, Query, QueryPlan, SearchResult, Usage

DAY = 86400.0
T0 = 1_800_000_000.0
CONFIG = {"version": "2026.11.21+c6ee3e9fd", "engines": [
    {"name": "mwmbl", "categories": ["general"], "enabled": True},
    {"name": "duckduckgo", "categories": ["general", "web"], "enabled": True},
    {"name": "bing news", "categories": ["news"], "enabled": True},
    {"name": "hackernews", "categories": ["news", "it"], "enabled": True},
    {"name": "stackoverflow", "categories": ["it"], "enabled": True},
    {"name": "europepmc", "categories": ["science"], "enabled": True},
    {"name": "arxiv", "categories": ["science"], "enabled": False}]}

RESULTS = [
    {"url": "https://example.org/a", "title": "<b>Predictive</b> coding &amp; cortex", "content": "A <i>review</i>",
     "engine": "mwmbl", "engines": ["mwmbl", "duckduckgo"], "score": 2.5, "category": "general",
     "publishedDate": "2026-11-18T10:00:00"},
    {"url": "https://example.org/b", "title": "No date", "content": "undated", "engine": "mwmbl",
     "engines": ["mwmbl"], "score": 1.0, "category": "general"},
    {"url": "https://example.org/c", "title": "Null date", "content": "", "engine": "mwmbl", "engines": ["mwmbl"],
     "publishedDate": None, "category": "general"},
    {"url": "https://example.org/d", "title": "Garbage date", "content": "", "engine": "mwmbl",
     "engines": ["mwmbl"], "publishedDate": "last Tuesday", "category": "general"},
    {"url": "javascript:alert(1)", "title": "bad", "content": "", "engine": "mwmbl", "engines": ["mwmbl"]},
    {"url": "https://example.org/a", "title": "duplicate", "content": "", "engine": "mwmbl", "engines": ["mwmbl"]},
]


class FakeSearXNG:
    """GET /search?format=json, /config and /healthz like SearXNG 2026.11. `reply` is a dict (the JSON body), or
    ("http", status[, headers]) for an HTTP error."""

    def __init__(self, reply=None):
        self.reply = reply if reply is not None else {"results": [], "unresponsive_engines": []}
        self.requests = []
        self.config = CONFIG
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, body, headers=None, ctype="application/json"):
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                parts = urlsplit(self.path)
                if parts.path == "/healthz":
                    return self._send(200, b"OK", ctype="text/plain")
                if parts.path == "/config":
                    return self._send(200, fake.config)
                if parts.path != "/search":
                    return self._send(404, {"error": "nope"})
                fake.requests.append({k: v[0] for k, v in parse_qs(parts.query).items()})
                reply = fake.reply(fake.requests[-1]) if callable(fake.reply) else fake.reply
                if isinstance(reply, tuple) and reply[0] == "drip":     # one byte every reply[1] seconds
                    raw = json.dumps({"results": [RESULTS[1]]}).encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    for b in raw:
                        try:
                            self.wfile.write(bytes([b]))
                            self.wfile.flush()
                        except OSError:
                            return
                        time.sleep(reply[1])
                    return
                if isinstance(reply, tuple):
                    return self._send(reply[1], {"error": "x"}, reply[2] if len(reply) > 2 else None)
                return self._send(200, {"query": fake.requests[-1].get("q"), "number_of_results": 0,
                                        "answers": [], "suggestions": [], **reply})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake():
    f = FakeSearXNG()
    yield f
    f.close()


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def provider(fake, clock=None, **kw):
    return sx.SearXNGProvider(fake.url, clock=clock or Clock(), sleep=lambda s: None, **kw)


# ---- normalisation -------------------------------------------------------------------------------------------

def test_results_are_normalised_and_dates_are_never_invented(fake):
    fake.reply = {"results": RESULTS, "unresponsive_engines": []}
    p = provider(fake)
    out = p.search(Query("predictive coding", "web"), 10)
    assert [r.url for r in out] == ["https://example.org/a", "https://example.org/b", "https://example.org/c",
                                    "https://example.org/d"]          # bad scheme and duplicate dropped
    a, b, c, d = out
    assert a.title == "Predictive coding & cortex" and a.snippet == "A review" and a.provider == "searxng"
    assert a.engine == "mwmbl" and a.metadata["engines"] == ["mwmbl", "duckduckgo"] and a.rank == 1
    assert a.metadata["category"] == "general" and a.metadata["searxng_score"] == 2.5 and a.kind == "web"
    assert a.metadata["retrieved_at"] == T0 and a.metadata["canonical_url"] == "https://example.org/a"
    assert a.published_at == datetime(2026, 11, 18, 10, tzinfo=timezone.utc).timestamp()
    assert b.published_at is None and c.published_at is None and d.published_at is None
    assert all("published" not in k for r in out for k in r.metadata)  # no stand-in date anywhere


def test_since_drops_old_dated_results_but_keeps_undated(fake):
    fake.reply = {"results": RESULTS[:2], "unresponsive_engines": []}
    out = provider(fake).search(Query("x", "web", since=T0), 10)       # 2026-11-18 is before T0 (2027)
    assert [r.url for r in out] == ["https://example.org/b"]


def test_science_results_are_academic_kind(fake):
    fake.reply = {"results": [{"url": "https://europepmc.org/article/MED/1", "title": "t", "content": "c",
                               "engine": "europepmc", "engines": ["europepmc"], "category": "science"}]}
    (r,) = provider(fake).search(Query("predictive coding", "academic"), 5)
    assert r.kind == "academic" and fake.requests[-1]["categories"] == "science"


# ---- request shaping and routing -------------------------------------------------------------------------------

def test_request_parameters(fake):
    p = provider(fake)
    p.search(Query("ai   news", "news", since=T0 - 7 * DAY, site="example.com", language="en"), 5)
    req = fake.requests[-1]
    assert req == {"q": "ai news site:example.com", "format": "json", "categories": "news", "pageno": "1",
                   "safesearch": "0", "language": "en", "time_range": "week"}
    p.search(Query("x" * 900, "technical", since=T0 - 2 * DAY), 5)
    req = fake.requests[-1]
    assert len(req["q"]) == sx.MAX_QUERY_CHARS and req["categories"] == "it" and "time_range" not in req
    assert p.accepts(Query("pypi:numpy", "technical")) is False and p.accepts(Query("numpy", "web"))


@pytest.mark.parametrize("kind,text,purpose,since,expected", [
    ("news", "ai", "", T0 - DAY, ("news", "day")),
    ("news", "ai", "", T0 - 7 * DAY, ("news", "week")),
    ("news", "ai", "", T0 - 30 * DAY, ("news", "month")),
    ("news", "ai", "", None, ("news", None)),
    ("web", "ai inference", "", T0 - 400 * DAY, ("general", None)),
    ("web", "vllm release notes", "expansion: release notes", None, ("general,it", None)),
    ("web", "numpy package", "", T0 - 3 * DAY, ("general,it", "week")),
    ("technical", "vllm", "", T0 - DAY, ("it", None)),
    ("academic", "predictive coding", "", T0 - DAY, ("science", None)),
    ("encyclopedia", "cortex", "", None, ("general", None)),
])
def test_intent_to_searxng_category(kind, text, purpose, since, expected):
    route = searxng_route(Query(text, kind, since=since, purpose=purpose), now=T0)
    assert (route["categories"], route["time_range"]) == expected and route["why"]


def test_no_fan_out_to_every_engine(fake):
    p = provider(fake)
    p.search(Query("ai", "news"), 5)
    assert "engines" not in fake.requests[-1]          # SearXNG queries only the news category's engines
    assert p.category_engines("news") == ["bing news", "hackernews"]
    assert p.category_engines("science") == ["europepmc"]                       # disabled arxiv is not counted


# ---- engine health, breakers, failures -------------------------------------------------------------------------

def test_unresponsive_engines_feed_engine_health(fake):
    fake.reply = {"results": [RESULTS[1]], "unresponsive_engines": [["duckduckgo", "CAPTCHA"]]}
    p = provider(fake)
    p.search(Query("x", "web"), 5)
    by_engine = {a["engine"]: a for a in p.last_attempts}
    assert by_engine["duckduckgo"]["ok"] is False and by_engine["duckduckgo"]["error"].startswith("captcha")
    assert by_engine["mwmbl"] == {"engine": "mwmbl", "ok": True, "n": 1, "error": None,
                                  "latency": by_engine["mwmbl"]["latency"]}
    stats = p.engine_stats()
    assert stats["duckduckgo"]["captcha"] == 1 and stats["mwmbl"]["success"] == 1 and stats["mwmbl"]["results"] == 1
    for reason, kind in (("too many requests", "rate_limited"), ("access denied", "forbidden"),
                         ("timeout", "timeout"), ("Suspended: CAPTCHA", "captcha"), ("parsing error", "error"),
                         ("HTTP error 403", "forbidden"), ("HTTP 429", "rate_limited")):
        assert sx.classify(reason) == kind


def test_engine_breaker_opens_and_engine_is_left_out(fake):
    fake.reply = {"results": [RESULTS[1]], "unresponsive_engines": [["duckduckgo", "CAPTCHA"]]}
    clock = Clock()
    p = provider(fake, clock)
    for i in range(3):
        p.search(Query(f"q{i}", "web"), 5)
    assert p.engine_snapshot()["duckduckgo"]["state"] == "open"
    p.search(Query("q-next", "web"), 5)
    assert fake.requests[-1]["engines"] == "mwmbl"                     # the open engine is not asked
    assert {"engine": "duckduckgo", "ok": False, "n": 0, "error": "circuit_open", "latency": 0.0} in p.last_attempts


def test_every_engine_open_is_circuit_open(fake):
    fake.reply = {"results": [], "unresponsive_engines": [["bing news", "timeout"], ["hackernews", "timeout"]]}
    p = provider(fake)
    for i in range(3):
        with pytest.raises(ProviderError) as exc:
            p.search(Query(f"q{i}", "news"), 5)
        assert exc.value.code == "engines_failed"
    with pytest.raises(ProviderError) as exc:
        p.search(Query("again", "news"), 5)
    assert exc.value.code == "circuit_open"


def test_empty_engine_is_neutral(fake):
    fake.reply = {"results": [], "unresponsive_engines": []}
    p = provider(fake)
    assert p.search(Query("obscure", "web"), 5) == []
    assert all(s["state"] == "closed" and s["failures"] == 0 for s in p.engine_snapshot().values())
    assert p.engine_stats()["mwmbl"]["empty"] == 1


def test_http_failures(fake):
    fake.reply = ("http", 429, {"Retry-After": "30"})
    with pytest.raises(ProviderError) as exc:
        provider(fake).search(Query("x", "web"), 5)
    assert exc.value.code == "rate_limited" and exc.value.retry_after == 30.0
    fake.reply = ("http", 403)
    with pytest.raises(ProviderError) as exc:
        provider(fake).search(Query("x", "web"), 5)
    assert exc.value.code == "http_error"
    calls = []

    def flaky(req):
        calls.append(req)
        return ("http", 502) if len(calls) == 1 else {"results": [RESULTS[1]]}
    fake.reply = flaky
    assert len(provider(fake).search(Query("x", "web"), 5)) == 1 and len(calls) == 2   # one bounded retry
    fake.reply = ("http", 500)
    with pytest.raises(ProviderError) as exc:
        provider(fake).search(Query("y", "web"), 5)
    assert exc.value.code == "searxng_error"


def test_a_dripping_searxng_cannot_hold_a_search_past_its_timeout(fake):
    """T5-M05: one byte a second kept every read alive; web search and health --probe hung past 120 s."""
    fake.reply = ("drip", 0.2)
    started = time.monotonic()
    with pytest.raises(ProviderError) as exc:
        provider(fake, timeout=1.0).search(Query("x", "web"), 5)
    assert exc.value.code == "timeout" and time.monotonic() - started < 2.5


def test_odd_result_shapes_never_raise_or_open_phantom_breakers(fake):
    """T5-M06: engines 7, a dict title, unresponsive [[None, None], [], [5]] -> TypeError (provider_error) and
    breakers named 'None', '5' and ''."""
    fake.reply = {"results": [
        {"url": "https://w1.example/a", "title": {"x": 1}, "content": 5, "engines": 7, "score": "NaN",
         "publishedDate": "not a date"},
        {"url": "https://w2.example/b", "title": None, "content": None, "engines": "google", "engine": 9,
         "publishedDate": 1e300, "score": 1e308},
        {"url": "https://w3.example/c", "title": "t", "content": "c", "engines": ["mwmbl", None, 4, ""],
         "publishedDate": "2026-13-45T99:99:99"},
        "string-item", None, 5], "unresponsive_engines": [[None, None], [], [5], ["", "x"], "duckduckgo"]}
    p = provider(fake)
    results = p.search(Query("x", "web"), 5)
    assert [r.url for r in results] == ["https://w1.example/a", "https://w2.example/b", "https://w3.example/c"]
    assert results[2].metadata["engines"] == ["mwmbl"] and results[0].published_at is None
    assert set(p.engine_snapshot()) <= {"mwmbl", "duckduckgo"}
    for reply in ({"results": {"a": 1}, "unresponsive_engines": "duckduckgo"}, {"results": [], "unresponsive_engines": 5}):
        fake.reply = reply
        assert p.search(Query("y" + str(len(fake.requests)), "web"), 5) == []
    assert set(p.engine_snapshot()) <= {"mwmbl", "duckduckgo"}


def test_odd_config_shapes_are_ignored(fake):
    fake.config = {"engines": [{"name": "x", "enabled": True, "categories": 5}, "str", None,
                               {"name": 7, "enabled": True, "categories": ["general"]},
                               {"name": "ok", "enabled": "yes", "categories": ["general"]}], "version": {"v": 1}}
    p = provider(fake)
    assert p.category_engines("general") == []
    assert p.health()["reachable"] is True
    fake.reply = {"results": [RESULTS[1]]}
    assert len(p.search(Query("x", "web"), 5)) == 1


def test_unreachable():
    p = sx.SearXNGProvider("http://127.0.0.1:9", clock=Clock(), sleep=lambda s: None)
    with pytest.raises(ProviderError) as exc:
        p.search(Query("x", "web"), 5)
    assert exc.value.code == "searxng_unreachable"
    assert p.health()["verdict"] == "down"


@pytest.mark.parametrize("url", ["http://example.com:8888", "https://127.0.0.1:8888", "http://10.0.0.2:8888",
                                 "http://127.0.0.1:8888/search", "http://user@127.0.0.1:8888"])
def test_only_a_loopback_endpoint_is_allowed(url):
    with pytest.raises(NotLoopback):
        sx.SearXNGProvider(url)


# ---- cache, budget, adaptive timeout --------------------------------------------------------------------------

def test_short_ttl_cache_and_budget(fake):
    fake.reply = {"results": RESULTS[:2]}
    clock = Clock()
    p = provider(fake, clock)
    budget, usage = Budget(max_requests=10), Usage()
    first = p.search(Query("x", "web"), 5, budget=budget, usage=usage)
    assert usage.requests == 2                                         # general = mwmbl + duckduckgo
    again = p.search(Query("x", "web"), 5, budget=budget, usage=usage)
    assert len(fake.requests) == 1 and usage.requests == 2 and [r.url for r in again] == [r.url for r in first]
    assert again[0].metadata["cache"] is True and again[0].metadata["retrieved_at"] == T0
    clock.t += sx.CACHE_TTL + 1
    p.search(Query("x", "web"), 5, budget=budget, usage=usage)
    assert len(fake.requests) == 2
    with pytest.raises(ProviderError) as exc:
        p.search(Query("z", "web"), 5, budget=Budget(max_requests=1), usage=Usage())
    assert exc.value.code == "budget_exhausted"


def test_adaptive_timeout(fake):
    p = provider(fake)
    assert p.timeout() == sx.DEFAULT_TIMEOUT
    p._latencies.extend([0.2, 0.3, 0.4])
    assert p.timeout() == sx.MIN_TIMEOUT
    p._latencies.extend([30.0] * 10)
    assert p.timeout() == sx.MAX_TIMEOUT


# ---- health verdict ------------------------------------------------------------------------------------------

def test_healthy_needs_recent_real_results(fake):
    clock = Clock()
    p = provider(fake, clock)
    h = p.health()
    assert h["reachable"] and h["version"] == CONFIG["version"] and h["verdict"] == "degraded"
    assert h["engines_enabled"]["news"] == ["bing news", "hackernews"]
    p.search(Query("nothing", "web"), 5)                               # HTTP 200, zero results
    assert p.health()["verdict"] == "degraded"
    fake.reply = {"results": RESULTS[:1]}
    p.search(Query("something", "web"), 5)
    assert p.health()["verdict"] == "healthy"
    clock.t += sx.HEALTHY_WINDOW + 1
    assert p.health()["verdict"] == "degraded"
    assert sx.verdict(False, T0, T0) == "down" and sx.verdict(True, T0, T0, engines_open=True) == "degraded"


# ---- registry: primary SearXNG, open-webSearch fallback ---------------------------------------------------------

class FakeOWS:
    name, kinds, general_web = "openwebsearch", frozenset({"web", "news"}), True

    def __init__(self):
        self.calls = 0
        self.fallback_for = "searxng"

    def search(self, query, limit, *, budget=None, usage=None):
        self.calls += 1
        return [SearchResult(url="https://fallback.example/1", title="f", snippet="", provider=self.name,
                             engine="bing")]


def plan(*queries):
    return QueryPlan(queries=tuple(queries))


def test_open_websearch_runs_only_when_searxng_is_down_or_empty(fake):
    fake.reply = {"results": RESULTS[:2]}
    ows = FakeOWS()
    reg = ProviderRegistry([provider(fake), ows], clock=Clock())
    out = reg.search(plan(Query("x", "web")), budget=Budget(), usage=Usage())
    assert ows.calls == 0 and {r.provider for r in out.results} == {"searxng"}
    assert any(a["provider"] == "searxng" and a["engine"] == "mwmbl" for a in out.attempts)

    fake.reply = {"results": []}
    out = reg.search(plan(Query("nothing here", "web")), budget=Budget(), usage=Usage())
    assert ows.calls == 1 and [r.url for r in out.results] == ["https://fallback.example/1"]

    down = sx.SearXNGProvider("http://127.0.0.1:9", clock=Clock(), sleep=lambda s: None)
    ows2 = FakeOWS()
    reg = ProviderRegistry([down, ows2], clock=Clock())
    out = reg.search(plan(Query("x", "web")), budget=Budget(), usage=Usage())
    assert ows2.calls == 1 and out.results and any(f.get("provider") == "searxng" for f in out.failures)


def test_open_circuit_runs_the_fallback_in_the_same_round(fake):
    clock = Clock()
    ows = FakeOWS()
    down = sx.SearXNGProvider("http://127.0.0.1:9", clock=clock, sleep=lambda s: None)
    reg = ProviderRegistry([down, ows], clock=clock)
    for i in range(3):
        reg.search(plan(Query(f"x{i}", "web")), budget=Budget(), usage=Usage())
    assert reg.status()[0]["state"] == "open"
    calls = ows.calls
    out = reg.search(plan(Query("y", "web")), budget=Budget(), usage=Usage())
    assert ows.calls == calls + 1 and any(a["error"] == "circuit_open" for a in out.attempts)


def test_registry_lookups_never_reach_searxng(fake):
    reg = ProviderRegistry([provider(fake)], clock=Clock())
    reg.search(plan(Query("pypi:numpy", "technical")), budget=Budget(), usage=Usage())
    assert fake.requests == []


def test_engine_health_is_persisted_through_the_registry(fake):
    fake.reply = {"results": [RESULTS[1]], "unresponsive_engines": [["duckduckgo", "too many requests"]]}
    reg = ProviderRegistry([provider(fake)], clock=Clock())
    reg.search(plan(Query("x", "web")), budget=Budget(), usage=Usage())
    names = {s["name"]: s for s in reg.status()}
    assert names["searxng:duckduckgo"]["failures"] == 1 and names["searxng:duckduckgo"]["stats"]["rate_limited"] == 1
    dumped = reg.dump_state()["health"]
    assert "searxng:duckduckgo" in dumped and dumped["searxng"]["successes"] == 1
    reg2 = ProviderRegistry([provider(fake)], clock=Clock())
    reg2.load_state({"health": dumped})
    assert {s["name"]: s for s in reg2.status()}["searxng:duckduckgo"]["failures"] == 1


# ---- configuration: absent = unchanged behaviour ---------------------------------------------------------------

def test_build_without_searxng_is_unchanged():
    built = providers.build(WebConfig(), fetcher=None)
    assert [p.name for p in built][:2] == ["openwebsearch", "wikipedia"]
    assert not hasattr(built[0], "fallback_for") and "searxng" not in [p.name for p in built]


def test_build_with_searxng_makes_it_primary():
    built = providers.build(WebConfig(searxng_url="http://127.0.0.1:8888",
                                      searxng_engines={"news": ["bing news"]}), fetcher=None)
    assert [p.name for p in built][:2] == ["searxng", "openwebsearch"]
    assert built[0].engines == {"news": ["bing news"]} and built[1].fallback_for == "searxng"
    named = providers.build(WebConfig(searxng_url="http://127.0.0.1:8888",
                                      providers=["wikipedia", "searxng", "openwebsearch"]), fetcher=None)
    assert [p.name for p in named] == ["wikipedia", "searxng", "openwebsearch"]


def test_config_validation(tmp_path):
    from neuroscifi.instance import store
    db = store.create(tmp_path / "i.sqlite", T0)
    try:
        assert state.get_config(db).searxng_url == ""
        cfg = state.set_config(db, {"searxng_url": "http://127.0.0.1:8888/"}, T0)
        assert cfg.searxng_url == "http://127.0.0.1:8888"
        assert state.set_config(db, {"searxng_url": ""}, T0).searxng_url == ""
        for bad in ("http://example.com:8888", "https://127.0.0.1:8888", 5):
            with pytest.raises(ValidationError):
                state.set_config(db, {"searxng_url": bad}, T0)
        assert state.set_config(db, {"searxng_engines": {"news": ["bing news"]}}, T0).searxng_engines == {
            "news": ["bing news"]}
        for bad in ({"images": ["x"]}, {"news": "bing"}, []):
            with pytest.raises(ValidationError):
                state.set_config(db, {"searxng_engines": bad}, T0)
    finally:
        db.close()


# ---- container and honest User-Agent patch ---------------------------------------------------------------------

def test_container_arguments_are_loopback_pinned_and_hardened(tmp_path):
    c = sx.Container(config_dir=tmp_path)
    args = c.run_args()
    joined = " ".join(args)
    assert args[-1] == sx.IMAGE and "@sha256:" in sx.IMAGE
    assert "127.0.0.1:8888:8080" in args and "0.0.0.0" not in joined
    for flag in ("--read-only", "--cap-drop", "no-new-privileges", "--memory", "--cpus", "--pids-limit",
                 "--restart", "--health-cmd", "--user", "json-file"):
        assert flag in joined
    assert f"{sx.PYCACHE_PATH}:" in joined and f":{sx.CLIENT_PATH}:ro" in joined and "secret.env" in joined
    with pytest.raises(NotLoopback):
        sx.Container(host="0.0.0.0", config_dir=tmp_path)


def test_container_without_docker_reports_absent(tmp_path):
    def missing(*a, **k):
        raise FileNotFoundError("docker")
    c = sx.Container(config_dir=tmp_path, runner=missing, port=9)
    st = c.status()
    assert st["exists"] is False and st["running"] is False and c.stop()["stopped"] is False


def test_prepare_writes_a_private_secret_and_a_verified_patch(tmp_path, monkeypatch):
    original = ('DEFAULT_IMPERSONATE = "chrome"\n'
                'def new_client(impersonate="chrome"):\n'
                '    use_impersonate = impersonate not in ("", NO_IMPERSONATE)\n'
                '    kwargs = {}\n'
                '    if use_impersonate:\n'
                '        kwargs["impersonate"] = impersonate\n')
    monkeypatch.setattr(sx, "CLIENT_SHA256", hashlib.sha256(original.encode()).hexdigest())

    def runner(argv, **kw):
        assert argv[:3] == ["docker", "run", "--rm"] and sx.IMAGE in argv
        return subprocess.CompletedProcess(argv, 0, original, "")
    c = sx.Container(config_dir=tmp_path / "cfg", runner=runner)
    out = c.prepare()
    assert out["secret_created"] and out["honest_user_agent"] and c.honest()
    secret = tmp_path / "cfg" / "secret.env"
    assert oct(secret.stat().st_mode & 0o777) == "0o600" and oct((tmp_path / "cfg").stat().st_mode & 0o777) == "0o700"
    first = secret.read_text()
    assert c.prepare()["secret_created"] is False and secret.read_text() == first     # generated once
    patched = (tmp_path / "cfg" / "client.py").read_text()
    assert "use_impersonate = False" in patched and "searxng_useragent()" in patched
    assert json.loads((tmp_path / "cfg" / "useragents.json").read_text())["ua"] == sx.HONEST_UA
    assert "Mozilla" not in sx.HONEST_UA
    settings = (tmp_path / "cfg" / "settings.yml").read_text()
    assert "json" in settings and "limiter: false" in settings and "public_instance: false" in settings
    (tmp_path / "cfg" / "client.py").write_text(original)                             # tampering is detected
    assert c.honest() is False
    with pytest.raises(ValidationError):
        sx.honest_client(original + "# changed upstream\n")


# ---- CLI ---------------------------------------------------------------------------------------------------------

def test_cli_status_and_health_show_searxng(capsys, tmp_path, fake, monkeypatch):
    from neuroscifi.cli import main

    def nsf(*argv):
        code = main(list(argv))
        out = capsys.readouterr()
        return code, (json.loads(out.out) if code == 0 else out.err)
    db = str(tmp_path / "individual.sqlite")
    assert nsf("init", "--db", db)[0] == 0
    monkeypatch.setattr(sx.Container, "status", lambda self: {"container": sx.CONTAINER_NAME, "running": False})
    code, out = nsf("web", "status", "--db", db)
    assert code == 0 and out["searxng"]["configured"] is False
    assert nsf("web", "config", "--set", f'searxng_url="{fake.url}"', "--db", db)[0] == 0
    code, out = nsf("web", "status", "--db", db)
    assert code == 0 and out["searxng"]["reachable"] is True and out["searxng"]["verdict"] == "degraded"
    code, out = nsf("web", "searxng", "health", "--probe", "--db", db)
    assert code != 0 and fake.requests == []                          # web access is off: nothing was sent
    assert nsf("web", "config", "--enable", "--db", db)[0] == 0
    fake.reply = {"results": RESULTS[:2], "unresponsive_engines": [["duckduckgo", "CAPTCHA"]]}
    monkeypatch.setattr(wcli, "SERVICES_OPTIONS", {})
    code, out = nsf("web", "searxng", "health", "--probe", "--db", db)
    assert code == 0 and out["probed"] and out["probes"]["general"]["results"] == 2
    assert out["searxng"]["verdict"] == "healthy"
    assert out["searxng"]["engines"]["duckduckgo"]["last_error"].startswith("captcha")
    code, out = nsf("web", "health", "--db", db)
    assert code == 0 and out["searxng"]["verdict"] == "healthy"
