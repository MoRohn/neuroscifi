"""ProviderRegistry: routing, parallel fan-out, circuit breakers, fallback, RRF fusion, dedupe, budget, state."""
import json
import threading

import pytest

from neuroscifi.instance.errors import ValidationError
from neuroscifi.webcortex.search import (Breaker, ProviderError, ProviderRegistry, RRF_K, SearchProvider,
                                         canonical_url, fuse, parse_iso_date, strip_html)
from neuroscifi.webcortex.types import Budget, FetchError, Query, QueryPlan, SearchResult, Usage


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def R(url, provider="p", rank=1, **kw):
    return SearchResult(url=url, title=kw.pop("title", url), snippet=kw.pop("snippet", ""), provider=provider,
                        rank=rank, **kw)


class Fake:
    """Scripted provider: `behaviour` is a list of results, an exception, or a callable(query) -> either."""

    def __init__(self, name, kinds=("web",), behaviour=(), *, general_web=False, accepts=None):
        self.name, self.kinds, self.behaviour, self.general_web = name, frozenset(kinds), behaviour, general_web
        self.calls = []
        if accepts is not None:
            self.accepts = accepts

    def search(self, query, limit, *, budget, usage):
        self.calls.append((query.text, query.kind, limit))
        b = self.behaviour(query) if callable(self.behaviour) else self.behaviour
        if isinstance(b, BaseException):
            raise b
        return [r if isinstance(r, SearchResult) else R(r, self.name, i + 1) for i, r in enumerate(b)][:limit]


def plan(*texts, kind="web"):
    return QueryPlan(queries=tuple(Query(t, kind) if isinstance(t, str) else t for t in texts))


def run(registry, p, budget=None, usage=None, **kw):
    usage = usage if usage is not None else Usage()
    return registry.search(p, budget=budget or Budget(), usage=usage, **kw), usage


def state_of(registry, name):
    return next(s for s in registry.status() if s["name"] == name)


# ---- helpers -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("HTTPS://WWW.Example.COM:443/a/b/?utm_source=x&b=2&a=1#frag", "https://example.com/a/b?a=1&b=2"),
    ("http://example.com:80/", "http://example.com"),
    ("http://example.com:8080/x/", "http://example.com:8080/x"),
    ("https://example.com/p?fbclid=1&gclid=2&q=3", "https://example.com/p?q=3"),
    ("https://user:pw@example.com/p", "https://example.com/p"),
    ("https://Example.com/Case/Path", "https://example.com/Case/Path"),
])
def test_canonical_url(raw, expected):
    assert canonical_url(raw) == expected


def test_strip_html_and_dates():
    assert strip_html('<b>Dendritic</b> &amp; <span class="x">spikes</span>&nbsp;now') == "Dendritic & spikes now"
    assert strip_html("&lt;script&gt;alert(1)&lt;/script&gt; text") == "alert(1) text"
    assert strip_html("if a < b and c > d") == "if a < b and c > d"
    assert strip_html("x<!-- hidden -->y") == "x y"
    assert parse_iso_date("2026-11-20T00:00:00Z") == 1795132800.0
    assert parse_iso_date("2026-11-20T02:00:00+02:00") == 1795132800.0
    assert parse_iso_date("2026-11-20") == 1795132800.0
    assert parse_iso_date("2026-13-40") is None and parse_iso_date("yesterday") is None and parse_iso_date(None) is None


def test_fake_providers_satisfy_protocol():
    assert isinstance(Fake("a"), SearchProvider)


# ---- fusion and dedupe -------------------------------------------------------------------------------------

def test_rrf_scores_and_order():
    lists = [(0, "a", None, [R("https://x.org/1", "a"), R("https://x.org/2", "a")]),
             (0, "b", None, [R("https://x.org/2", "b"), R("https://x.org/3", "b")])]
    fused = fuse(lists)
    assert [r.url for r in fused] == ["https://x.org/2", "https://x.org/1", "https://x.org/3"]
    assert fused[0].score == pytest.approx(1 / (RRF_K + 2) + 1 / (RRF_K + 1))
    assert fused[1].score == pytest.approx(1 / (RRF_K + 1))
    assert fused[2].score == pytest.approx(1 / (RRF_K + 2))
    assert fused[0].metadata["found_by"] == ["a", "b"]


def test_dedupe_by_canonical_url_keeps_structured_metadata():
    web = R("https://www.arxiv.org/abs/2411.01234/?utm_source=feed", "openwebsearch", engine="duckduckgo",
            title="Dendritic spikes", snippet="short")
    structured = R("https://arxiv.org/abs/2411.01234", "arxiv", kind="academic", published_at=1.0,
                   metadata={"arxiv_id": "2411.01234", "doi": "10.1/x"}, snippet="")
    fused = fuse([(0, "openwebsearch", "duckduckgo", [web]), (1, "arxiv", None, [structured])])
    assert len(fused) == 1
    r = fused[0]
    assert r.provider == "arxiv" and r.kind == "academic" and r.published_at == 1.0
    assert r.metadata["arxiv_id"] == "2411.01234" and r.metadata["doi"] == "10.1/x"
    assert r.snippet == "short"            # filled from the duplicate
    assert r.metadata["found_by"] == ["openwebsearch:duckduckgo", "arxiv"]
    assert r.score == pytest.approx(2 / (RRF_K + 1))


def test_fusion_across_queries_rewards_repeated_hits():
    a = Fake("a", behaviour=lambda q: ["https://s.org/common", f"https://s.org/{q.text}"])
    reg = ProviderRegistry([a])
    out, _ = run(reg, plan("one", "two"))
    assert out.results[0].url == "https://s.org/common"
    assert out.results[0].score == pytest.approx(2 / (RRF_K + 1))
    assert {r.url for r in out.results} == {"https://s.org/common", "https://s.org/one", "https://s.org/two"}


# ---- routing, fallback, parallelism ------------------------------------------------------------------------

def test_kind_routing_and_web_fallback_when_no_kind_provider():
    web = Fake("web", ("web",), ["https://w.org/1"])
    acad = Fake("acad", ("academic",), ["https://a.org/1"])
    reg = ProviderRegistry([web, acad])
    out, _ = run(reg, plan(Query("q", "academic")))
    assert acad.calls and not web.calls
    assert [r.url for r in out.results] == ["https://a.org/1"]
    out, _ = run(reg, plan(Query("q", "encyclopedia")))      # nobody serves it -> web providers
    assert web.calls and [r.url for r in out.results] == ["https://w.org/1"]


def test_fallback_to_web_when_specialists_fail():
    web = Fake("web", ("web",), ["https://w.org/1"])
    acad = Fake("acad", ("academic",), ProviderError("acad", "HTTP 503", code="http_error"))
    reg = ProviderRegistry([web, acad])
    out, _ = run(reg, plan(Query("dendrites", "academic")))
    assert [r.url for r in out.results] == ["https://w.org/1"]
    assert any(f["provider"] == "acad" and "http_error" in f["error"] for f in out.failures)
    assert [a["provider"] for a in out.attempts] == ["acad", "web"]


def test_one_provider_raising_does_not_stop_the_others():
    good = Fake("good", behaviour=["https://g.org/1", "https://g.org/2"])
    bad = Fake("bad", behaviour=RuntimeError("boom"))
    fetchy = Fake("fetchy", behaviour=FetchError("timeout", "https://x", "slow"))
    reg = ProviderRegistry([good, bad, fetchy])
    out, _ = run(reg, plan("q"))
    assert [r.url for r in out.results] == ["https://g.org/1", "https://g.org/2"]
    errors = {f["provider"]: f["error"] for f in out.failures}
    assert errors["bad"].startswith("provider_error: RuntimeError: boom")
    assert errors["fetchy"].startswith("timeout")
    assert state_of(reg, "bad")["failures"] == 1 and state_of(reg, "good")["successes"] == 1


def test_accepts_filters_providers():
    pypi = Fake("pypi", ("technical",), ["https://pypi.org/project/numpy/"],
                accepts=lambda q: q.text.startswith("pypi:"))
    web = Fake("web", ("web",), ["https://w.org/1"])
    reg = ProviderRegistry([pypi, web])
    out, _ = run(reg, plan(Query("pypi:numpy", "technical")))
    assert pypi.calls and not web.calls
    out, _ = run(reg, plan(Query("numpy release notes", "technical")))
    assert len(pypi.calls) == 1 and web.calls    # not accepted -> routed to web


def test_providers_run_in_parallel_per_query():
    barrier = threading.Barrier(3, timeout=5)

    def meet(q):
        barrier.wait()        # deadlocks (BrokenBarrierError) unless all three run concurrently
        return [f"https://p.org/{threading.current_thread().name}"]

    provs = [Fake(n, behaviour=meet) for n in ("a", "b", "c")]
    out, _ = run(ProviderRegistry(provs), plan("q"), budget=Budget(max_concurrency=3))
    assert not out.failures and all(a["ok"] for a in out.attempts)


def test_max_concurrency_one_serialises():
    active, peak, lock = [0], [0], threading.Lock()

    def slow(q):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        threading.Event().wait(0.02)
        with lock:
            active[0] -= 1
        return ["https://p.org/x"]

    run(ProviderRegistry([Fake(n, behaviour=slow) for n in "abcd"]), plan("q"), budget=Budget(max_concurrency=1))
    assert peak[0] == 1


# ---- budget --------------------------------------------------------------------------------------------------

def test_budget_charges_one_query_each_and_caps():
    a = Fake("a", behaviour=["https://a.org/1"])
    out, usage = run(ProviderRegistry([a]), plan("q1", "q2", "q3", "q4", "q5"), budget=Budget(max_queries=3))
    assert usage.queries == 3 and len(a.calls) == 3
    assert any("2 planned queries not sent" in f["error"] for f in out.failures)


def test_budget_exhausted_by_earlier_usage_stops_before_sending():
    a = Fake("a", behaviour=["https://a.org/1"])
    out, usage = run(ProviderRegistry([a]), plan("q1", "q2"), budget=Budget(max_queries=3), usage=Usage(queries=2))
    assert usage.queries == 3 and len(a.calls) == 1
    assert any(f["error"].startswith("budget_exhausted") for f in out.failures)


def test_non_health_errors_do_not_move_breaker():
    clock = Clock()
    a = Fake("a", behaviour=ProviderError("a", "spent", code="budget_exhausted", retryable=False))
    reg = ProviderRegistry([a], clock=clock, failure_threshold=1)
    for _ in range(3):
        run(reg, plan("q"))
    s = state_of(reg, "a")
    assert s["state"] == "closed" and s["failures"] == 0 and len(a.calls) == 3


# ---- circuit breaker -----------------------------------------------------------------------------------------

def test_breaker_transitions_with_fake_clock():
    clock = Clock()
    outcomes = [RuntimeError("down")] * 3 + [["https://a.org/ok"]] + [RuntimeError("again")]
    a = Fake("a", behaviour=lambda q: outcomes.pop(0))
    reg = ProviderRegistry([a], clock=clock, failure_threshold=3, cooldown=300)
    for _ in range(3):
        run(reg, plan("q"))
    assert state_of(reg, "a")["state"] == "open" and len(a.calls) == 3
    out, _ = run(reg, plan("q"))                       # open: skipped, recorded
    assert len(a.calls) == 3
    assert out.attempts == [{"provider": "a", "engine": None, "query": "q", "ok": False, "n": 0,
                             "error": "circuit_open", "latency": 0.0}]
    clock.t += 299
    run(reg, plan("q"))
    assert len(a.calls) == 3
    clock.t += 1
    assert state_of(reg, "a")["state"] == "half_open"
    out, _ = run(reg, plan("q"))                       # trial succeeds -> closed
    assert len(a.calls) == 4 and out.results and state_of(reg, "a")["state"] == "closed"
    assert state_of(reg, "a")["consecutive_failures"] == 0


def test_half_open_failure_reopens_and_allows_one_trial():
    clock = Clock()
    b = Breaker(failure_threshold=2, cooldown=10)
    b.failure("x", 0.1, clock())
    assert b.allow(clock())
    b.failure("x", 0.1, clock())
    assert b.state == "open" and not b.allow(clock())
    clock.t += 10
    assert b.allow(clock()) and b.state == "half_open"
    assert not b.allow(clock())                         # only one trial in flight
    b.failure("still down", 0.2, clock())
    assert b.state == "open" and b.opened_at == clock.t and b.last_error == "still down"
    clock.t += 10
    assert b.allow(clock())
    b.neutral()                                         # a health-neutral trial frees the slot
    assert b.allow(clock())
    b.success(0.1, clock())
    assert b.state == "closed"


def test_empty_answer_rule():
    clock = Clock()
    general = Fake("general", behaviour=[], general_web=True)
    specialist = Fake("special", ("web",), behaviour=lambda q: ["https://s.org/1"] if q.text == "found" else [])
    reg = ProviderRegistry([general, specialist], clock=clock)
    run(reg, plan("found"))          # general empty while another answered -> soft failure
    assert state_of(reg, "general")["failures"] == 1
    run(reg, plan("nothing"))        # nobody found anything -> neutral for general, success for specialist
    assert state_of(reg, "general")["failures"] == 1 and state_of(reg, "general")["successes"] == 0
    assert state_of(reg, "special")["successes"] == 2 and state_of(reg, "special")["failures"] == 0
    out, _ = run(reg, plan(Query("found", "academic")))   # non-general query: general empty is not a failure
    assert state_of(reg, "general")["failures"] == 1
    assert any(f["provider"] == "general" and f["error"] == "empty" for f in out.failures)


def test_status_dump_load_round_trip_with_engines():
    from neuroscifi.webcortex.providers.openwebsearch import OpenWebSearchProvider
    clock = Clock()
    ows = OpenWebSearchProvider("http://127.0.0.1:9", ["duckduckgo", "brave"], clock=clock, failure_threshold=2)
    a = Fake("a", behaviour=["https://a.org/1"])
    reg = ProviderRegistry([ows, a], clock=clock, failure_threshold=2)
    reg.record("a", True, 0.5)
    reg.record("a", True, 1.5)
    reg.record("a", False, 2.5, "timeout")
    reg.record("openwebsearch:brave", False, 0.3, "http_429: rate limited")
    reg.record("openwebsearch:brave", False, 0.3, "http_429: rate limited")
    reg.record("openwebsearch:duckduckgo", True, 0.8)
    reg.record("retired", False, 1.0, "gone")           # unknown provider name is kept, not lost
    names = [s["name"] for s in reg.status()]
    assert names[:3] == ["openwebsearch", "openwebsearch:duckduckgo", "openwebsearch:brave"]
    assert state_of(reg, "openwebsearch:brave")["state"] == "open"
    assert state_of(reg, "a")["p50_latency"] == 1.5 and state_of(reg, "a")["last_error"] == "timeout"
    dumped = json.loads(json.dumps(reg.dump_state()))
    assert set(dumped["health"]) == {"openwebsearch", "openwebsearch:duckduckgo", "openwebsearch:brave", "a",
                                     "retired"}

    ows2 = OpenWebSearchProvider("http://127.0.0.1:9", ["duckduckgo", "brave"], clock=clock, failure_threshold=2)
    reg2 = ProviderRegistry([ows2, Fake("a")], clock=clock, failure_threshold=2)
    reg2.load_state(dumped)
    assert reg2.dump_state() == dumped
    assert state_of(reg2, "openwebsearch:brave")["state"] == "open"
    assert ows2.engine_snapshot()["brave"]["state"] == "open"
    clock.t += 300
    assert state_of(reg2, "openwebsearch:brave")["state"] == "half_open"

    reg3 = ProviderRegistry([Fake("a")], clock=clock)    # openwebsearch not configured: kept as orphan
    reg3.load_state(dumped)
    assert reg3.dump_state()["health"]["openwebsearch:brave"] == dumped["health"]["openwebsearch:brave"]
    orphan = state_of(reg3, "openwebsearch:brave")
    assert orphan["configured"] is False and orphan["engine"] == "brave"


def test_load_state_ignores_garbage():
    reg = ProviderRegistry([Fake("a")])
    reg.load_state({"health": {"a": {"state": "weird", "failures": "x"}, "b": "nope"}})


def test_concurrent_records_are_counted_exactly():
    reg = ProviderRegistry([Fake("a")], failure_threshold=10_000)

    def work():
        for i in range(500):
            reg.record("a", i % 2 == 0, 0.01, "e")

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    s = state_of(reg, "a")
    assert s["successes"] == 2000 and s["failures"] == 2000


def test_duplicate_provider_names_refused():
    with pytest.raises(ValidationError):
        ProviderRegistry([Fake("a"), Fake("a")])


def test_outcome_is_json_serialisable():
    reg = ProviderRegistry([Fake("a", behaviour=["https://a.org/1"]), Fake("b", behaviour=RuntimeError("x"))])
    out, _ = run(reg, plan("q"))
    # SearchOutcome.to_json() leaves nested SearchResult objects (shared JsonMixin); serialise them explicitly
    json.dumps({**out.to_json(), "results": [r.to_json() for r in out.results]})
