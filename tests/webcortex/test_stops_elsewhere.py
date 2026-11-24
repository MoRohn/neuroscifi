"""The owner's stop for the requests a task does not send from its own thread or as page fetches.

Page and crawl fetches ask the run's gate before every request (test_read_ahead_stops.py). Searches run on a
pool's threads, a render's sub-requests on the render thread, and a record read through a provider's API was a
fetch outside the gate: those noticed a cancel, web access turned off or a denied host only at the run's next
check (once per search round; the web configuration re-read at most every 2 s). Now each of them asks before every
request: the search's gate (ProviderRegistry.search(wanted=, scope=)), the render's (BrowserRenderer.render(gate=))
and the run's own gate around the record read. Requests are counted where they arrive, and each stop is issued
while the request before it is provably waiting, so "after the stop" is an ordering, not a timing."""
import threading
import time

import pytest

from neuroscifi.instance import store
from neuroscifi.webcortex import orchestrator, search as search_mod, services as svc_mod, state
from neuroscifi.webcortex.browser import BrowserRenderer
from neuroscifi.webcortex.fixtureweb import FixtureWeb
from neuroscifi.webcortex.providers import searxng as sx
from neuroscifi.webcortex.providers.corpus import CorpusProvider
from neuroscifi.webcortex.providers.feeds import FeedProvider
from neuroscifi.webcortex.providers.openwebsearch import OpenWebSearchProvider
from neuroscifi.webcortex.search import ProviderError, ProviderRegistry
from neuroscifi.webcortex.types import (Budget, FetchError, Query, QueryPlan, SearchResult, Stopped, Usage,
                                        raising_gate, stop_verdict)

from test_openwebsearch import FakeDaemon
from test_read_ahead_stops import QUESTION, ROBOTS, T0, Held, fetcher_for, page, settled
from test_searxng import RESULTS, FakeSearXNG


def result(n, provider):
    return SearchResult(url=f"https://example.org/{provider}/{n}", title=f"hit {n}", snippet="s", provider=provider)


class Counting:
    """A provider that answers from memory and counts its calls; `during` runs inside each call."""
    general_web, kinds = True, frozenset({"web"})

    def __init__(self, name, during=None):
        self.name, self.calls, self.during = name, [], during

    def search(self, query, limit, *, budget=None, usage=None):
        self.calls.append(query.text)
        if self.during is not None:
            self.during(query)
        return [result(len(self.calls), self.name)]


def plan(*texts):
    return QueryPlan(queries=tuple(Query(t, "web") for t in texts))


# ---- the gate itself --------------------------------------------------------------------------------------------

def test_a_gate_answers_a_stop_a_denial_or_nothing_and_a_gate_that_fails_is_a_stop():
    assert stop_verdict(None) is None and raising_gate(None) is None
    assert stop_verdict(lambda url: None) is None and stop_verdict(lambda url: "") is None
    stop = stop_verdict(lambda url: "the task was cancelled", "https://a.example/")
    assert isinstance(stop, Stopped) and (stop.why, stop.denied) == ("the task was cancelled", False)
    denied = Stopped("the site was denied", denied=True)
    assert stop_verdict(lambda url: denied) is denied

    def broken(url):
        raise OSError("database is locked")
    failed = stop_verdict(broken)
    assert failed.why == "gate failed (OSError)" and not failed.denied
    with pytest.raises(Stopped):
        raising_gate(broken)(None)
    assert raising_gate(lambda url: None)(None) is None
    assert not isinstance(Stopped("x"), FetchError)              # it says nothing about a site
    # neither a stop nor a request cut on a stop that was then withdrawn says anything about a provider's health
    assert {"stopped", "interrupted"} <= search_mod.NON_HEALTH_CODES
    assert search_mod._error_code(Stopped("web access was turned off")) == ("stopped", "web access was turned off")


# ---- the registry -----------------------------------------------------------------------------------------------

def test_a_search_without_a_gate_is_what_it_was():
    a = Counting("a")
    outcome = ProviderRegistry([a]).search(plan("one", "two"), budget=Budget(), usage=Usage())
    assert a.calls == ["one", "two"] and len(outcome.results) == 2 and outcome.failures == []


def test_no_query_is_charged_or_sent_after_the_stop():
    """The owner stops the task while the first query is with its providers: the second and third are not charged
    and no provider is called for them; the outcome says so and nothing is raised."""
    stopped = threading.Event()
    a = Counting("a", during=lambda q: stopped.set())
    b = Counting("b")
    usage, sent = Usage(), []
    outcome = ProviderRegistry([a, b]).search(
        plan("one", "two", "three"), budget=Budget(), usage=usage, sent=lambda: sent.append(usage.queries),
        wanted=lambda url: "the task was cancelled" if stopped.is_set() else None)
    assert a.calls == ["one"] and usage.queries == 1 and sent == [1]
    assert [f["error"] for f in outcome.failures if f["provider"] is None] == ["stopped: the task was cancelled"]
    assert [f["query"] for f in outcome.failures if f["provider"] is None] == ["two"]
    assert len(b.calls) <= 1                                     # called for the first query, or stopped before it


def test_a_provider_is_not_called_once_the_task_is_stopped_and_its_health_is_untouched():
    a = Counting("a")
    registry = ProviderRegistry([a], failure_threshold=1)
    for _ in range(3):
        outcome = registry.search(plan("one"), budget=Budget(), usage=Usage(),
                                  wanted=lambda url: "web access was turned off")
        assert outcome.results == [] and a.calls == []
    status = {row["name"]: row for row in registry.status()}["a"]
    assert status["state"] == "closed" and not status.get("consecutive_failures")
    assert registry.search(plan("one"), budget=Budget(), usage=Usage()).results      # and it still answers


def test_a_stop_that_lands_inside_a_providers_call_is_that_calls_outcome_not_its_failure():
    """The gate says stop only on the provider's thread (the stop lands after the query was dispatched): the call
    ends 'stopped', which moves no breaker, and the next query is not sent."""
    main = threading.get_ident()
    a = Counting("a")
    registry = ProviderRegistry([a], failure_threshold=1)
    seen = []

    def wanted(url):
        seen.append(threading.get_ident() == main)
        return None if threading.get_ident() == main and len(seen) == 1 else "the task was cancelled"

    outcome = registry.search(plan("one", "two"), budget=Budget(), usage=Usage(), wanted=wanted)
    assert a.calls == [] and outcome.results == []
    mine = [f for f in outcome.failures if f["provider"] == "a"]
    assert [f["error"] for f in mine] == ["stopped: the task was cancelled"]
    assert {row["name"]: row for row in registry.status()}["a"]["state"] == "closed"
    assert seen[0] is True and False in seen                    # asked here, and on the provider's thread


def test_every_fetch_a_provider_makes_asks_the_gate_before_each_request(web, tmp_path):
    """A provider that makes two API requests through SafeFetcher; the task is stopped while the first is on its
    way back. The second is never sent, the first stays charged, and the scope's watch is the registry's caller's
    to give (the orchestrator passes SafeFetcher.gated)."""
    release, stop = threading.Event(), threading.Event()
    web.routes[("api.example", "/one")] = Held('{"n": 1}', release, "application/json")
    web.add("api.example", "/two", '{"n": 2}', content_type="application/json")
    fetcher = fetcher_for(web, tmp_path)

    class TwoCalls:
        name, general_web, kinds = "two", True, frozenset({"web"})

        def search(self, query, limit, *, budget=None, usage=None):
            for path in ("/one", "/two"):
                fetcher.fetch(f"http://api.example{path}", purpose="api", budget=budget, usage=usage)
            return [result(1, "two")]

    usage, out = Usage(), {}

    def run():
        out["outcome"] = ProviderRegistry([TwoCalls()]).search(
            plan("one"), budget=Budget(), usage=usage,
            wanted=lambda url: "the task was cancelled" if stop.is_set() else None,
            scope=lambda ask: fetcher.gated(ask))
    worker = threading.Thread(target=run)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not web.requests_to("api.example", "/one"):
            time.sleep(0.01)
        stop.set()
    finally:
        release.set()
    worker.join(10)
    assert not worker.is_alive()
    assert [r["path"] for r in settled(web, quiet=0.2)] == ["/one"]
    assert usage.requests == 1                                   # the one that was sent, and only that one
    assert [f["error"] for f in out["outcome"].failures if f["provider"] == "two"] == \
        ["stopped: the task was cancelled"]


# ---- the providers that talk to a daemon on this host ------------------------------------------------------------

class gate:
    """Set the search gate of the calling thread, as ProviderRegistry._call does on a provider's thread."""

    def __init__(self, ask):
        self.ask = ask

    def __enter__(self):
        search_mod._GATE.ask = self.ask

    def __exit__(self, *_exc):
        search_mod._GATE.ask = None


def stop_now(url=None):
    raise Stopped("the task was cancelled")


def test_searxng_is_handed_no_query_after_the_stop_and_nothing_is_charged():
    fake = FakeSearXNG()
    try:
        fake.reply = {"results": RESULTS}
        provider = sx.SearXNGProvider(fake.url, sleep=lambda s: None)
        usage = Usage()
        with gate(stop_now), pytest.raises(Stopped):
            provider.search(Query("dendritic computation"), 5, budget=Budget(), usage=usage)
        assert fake.requests == [] and usage.requests == 0
        assert provider.search(Query("dendritic computation"), 5, budget=Budget(), usage=usage)   # outside a gate
        assert len(fake.requests) == 1 and usage.requests >= 1
    finally:
        fake.close()


def test_searxng_does_not_retry_after_the_stop_and_the_first_request_stays_charged():
    fake = FakeSearXNG()
    try:
        fake.reply = ("status", 502)
        slept = []
        provider = sx.SearXNGProvider(fake.url, sleep=slept.append)
        usage, asked = Usage(), []

        def ask(url=None):
            asked.append(len(fake.requests))
            if fake.requests:
                raise Stopped("web access was turned off")
        with gate(ask), pytest.raises(Stopped):
            provider.search(Query("dendritic computation"), 5, budget=Budget(), usage=usage)
        assert len(fake.requests) == 1 and slept == [0.5] and asked == [0, 1]
        assert usage.requests >= 1                               # it was sent: it counts
    finally:
        fake.close()


def test_the_openwebsearch_daemon_is_called_for_no_further_engine_after_the_stop():
    daemon = FakeDaemon({"brave": [], "duckduckgo": [{"title": "t", "url": "https://d.example/"}]})
    try:
        provider = OpenWebSearchProvider(daemon.url, ["brave", "duckduckgo"])
        usage = Usage()

        def ask(url=None):
            if daemon.requests:
                raise Stopped("the task was cancelled")
        with gate(ask), pytest.raises(Stopped):
            provider.search(Query("dendritic computation"), 3, budget=Budget(), usage=usage)
        assert daemon.engines_called() == ["brave"] and usage.requests == 1
    finally:
        daemon.close()


def test_a_stop_is_not_recorded_as_an_unparseable_feed(web, tmp_path):
    web.add("feeds.example", "/robots.txt", ROBOTS, content_type="text/plain")
    web.add("feeds.example", "/a.xml", "<rss><channel></channel></rss>", content_type="application/rss+xml")
    fetcher = fetcher_for(web, tmp_path)
    provider = FeedProvider(["http://feeds.example/a.xml", "http://feeds.example/b.xml"], fetcher=fetcher)
    with fetcher.gated(stop_now), pytest.raises(Stopped):
        provider.search(Query("zephyr compiler"), 5, budget=Budget(), usage=Usage())
    assert web.log == []


# ---- whole runs: a search in flight when the owner stops the task ----------------------------------------------

class ApiSearch:
    """Finds its hits with two API requests through the run's fetcher (set once the services exist)."""
    name, general_web, kinds = "apisearch", True, frozenset({"web", "news", "technical", "academic"})

    def __init__(self, urls):
        self.fetcher, self.urls = None, urls

    def search(self, query, limit, *, budget=None, usage=None):
        try:
            for path in ("/find", "/more"):
                self.fetcher.fetch(f"http://api.example{path}", purpose="api", budget=budget, usage=usage)
        except FetchError as error:
            raise ProviderError(self.name, str(error), code=error.code) from None
        return [SearchResult(url=url, title="Zephyr compiler 4.2.0 boards",
                             snippet="the Zephyr compiler version 4.2.0 and the boards it supports",
                             provider=self.name) for url in self.urls]


def cancel(connection, task_id):
    orchestrator.cancel(connection, task_id, T0 + 1)


def web_off(connection, _task_id):
    state.set_config(connection, {"enabled": False}, T0 + 1)


def deny_api(connection, _task_id):
    state.set_config(connection, {"deny_domains": ["api.example"]}, T0 + 1)


@pytest.mark.parametrize("interrupt, status", [(cancel, "cancelled"), (web_off, "partial"), (deny_api, None)],
                         ids=["cancel", "web_off", "deny"])
def test_a_search_sends_no_request_after_the_stop(tmp_path, interrupt, status):
    """The provider's first API request is waiting for its answer when the owner cancels the task, turns web access
    off, or denies the API's host. The answer then arrives and the provider's second request is never sent (before:
    it was, and so was every further query of the round). A denied API host is that provider's failure and the run
    goes on without it."""
    release = threading.Event()
    web = FixtureWeb()
    web.routes[("api.example", "/find")] = Held('{"hits": 1}', release, "application/json")
    web._ip_for("api.example")
    web.add("api.example", "/more", '{"hits": 2}', content_type="application/json")
    web.add("one.example", "/robots.txt", ROBOTS, content_type="text/plain")
    web.add("one.example", "/zephyr", page("Zephyr compiler 4.2.0 boards", [
        "The Zephyr compiler version 4.2.0 supports 12 boards according to one.example."]))
    web.start()
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    state.set_config(db, {"enabled": True, "min_domain_interval": 0.0}, T0)
    task = orchestrator.submit(db, QUESTION, T0, mode="fact_verification")
    holder, out = svc_mod.holder_id(), {}
    with store.write(db):
        state.update_task(db, task["id"], T0, lease_holder=holder, lease_expires_at=T0 + 600)
    db.close()
    provider = ApiSearch(["http://one.example/zephyr"])

    def run():
        connection = store.open_existing(path)
        services = svc_mod.build(connection, path, fixture=web, providers=[provider], clock=lambda: T0)
        services.holder, provider.fetcher = holder, services.fetcher
        try:
            out["done"] = orchestrator.run(connection, task["id"], services)
        except BaseException as error:
            out["error"] = error
        finally:
            services.close()
            connection.close()

    worker = threading.Thread(target=run)
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not web.requests_to("api.example", "/find"):
            time.sleep(0.01)
        assert web.requests_to("api.example", "/find")
        other = store.connect(path)
        try:
            interrupt(other, task["id"])
        finally:
            other.close()
        at_stop = len(web.log)
    finally:
        release.set()
    worker.join(30)
    try:
        assert not worker.is_alive() and "error" not in out, out.get("error")
        log = settled(web)
        assert web.requests_to("api.example", "/more") == []
        done = out["done"]
        if status is not None:
            assert done["status"] == status, done["error"]
            assert len(log) == at_stop == 1 and done["usage"]["requests"] == 1
        else:                                                    # the API host alone is denied: nothing more to it
            assert [r["host"] for r in log].count("api.example") == 1
    finally:
        web.stop()


# ---- a record read through a provider's API ---------------------------------------------------------------------

class Wikipedia:
    """A 'wikipedia' provider whose article() reads the lead and then the whole text, as two API requests."""
    name, general_web, kinds = "wikipedia", False, frozenset({"web", "news", "technical", "academic"})

    def __init__(self):
        self.fetcher = None

    def search(self, query, limit, *, budget=None, usage=None):
        return [SearchResult(url="https://en.wikipedia.org/wiki/Zephyr_compiler", title="Zephyr compiler",
                             snippet="the Zephyr compiler version 4.2.0 and the boards it supports",
                             provider=self.name, metadata={"snippet_only": True})]

    def article(self, title, *, budget=None, usage=None, lang="en"):
        for path in ("/lead", "/text"):
            self.fetcher.fetch(f"http://wiki.example{path}", purpose="api", budget=budget, usage=usage)
        return {"extract": "The Zephyr compiler version 4.2.0 supports 12 boards.", "title": title,
                "url": "https://en.wikipedia.org/wiki/Zephyr_compiler"}


def test_a_record_read_through_a_providers_api_sends_no_request_after_the_stop(tmp_path):
    release = threading.Event()
    web = FixtureWeb()
    web.routes[("wiki.example", "/lead")] = Held('{"lead": 1}', release, "application/json")
    web._ip_for("wiki.example")
    web.add("wiki.example", "/text", '{"text": 1}', content_type="application/json")
    web.start()
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    state.set_config(db, {"enabled": True, "min_domain_interval": 0.0}, T0)
    task = orchestrator.submit(db, QUESTION, T0, mode="fact_verification")
    holder, out = svc_mod.holder_id(), {}
    with store.write(db):
        state.update_task(db, task["id"], T0, lease_holder=holder, lease_expires_at=T0 + 600)
    db.close()
    provider = Wikipedia()

    def run():
        connection = store.open_existing(path)
        services = svc_mod.build(connection, path, fixture=web, providers=[provider], clock=lambda: T0)
        services.holder, provider.fetcher = holder, services.fetcher
        try:
            out["done"] = orchestrator.run(connection, task["id"], services)
        except BaseException as error:
            out["error"] = error
        finally:
            services.close()
            connection.close()

    worker = threading.Thread(target=run)
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not web.requests_to("wiki.example", "/lead"):
            time.sleep(0.01)
        assert web.requests_to("wiki.example", "/lead")
        other = store.connect(path)
        try:
            orchestrator.cancel(other, task["id"], T0 + 1)
        finally:
            other.close()
    finally:
        release.set()
    worker.join(30)
    try:
        assert not worker.is_alive() and "error" not in out, out.get("error")
        assert [r["path"] for r in settled(web)] == ["/lead"]
        assert out["done"]["status"] == "cancelled" and out["done"]["usage"]["requests"] == 1
    finally:
        web.stop()


# ---- a render's sub-requests ------------------------------------------------------------------------------------

SCRIPTED = """<!doctype html><html><head><title>Scripted</title></head><body><div id="out"></div>
<script src="/first.js"></script>
<script>
fetch('/second.json').then(r => r.text()).catch(() => 'refused').then(t => {
  document.getElementById('out').textContent = 'second: ' + t;
});
fetch('http://other.example/third.json').catch(() => {});
</script></body></html>"""


def scripted_web(release=None):
    web = FixtureWeb()
    web.add("app.example", "/robots.txt", ROBOTS, content_type="text/plain")
    web.add("app.example", "/", SCRIPTED)
    if release is None:
        web.add("app.example", "/first.js", "window.first = 1;", content_type="application/javascript")
    else:
        web.routes[("app.example", "/first.js")] = Held("window.first = 1;", release, "application/javascript")
    web.add("app.example", "/second.json", '{"n": 2}', content_type="application/json")
    web.add("other.example", "/robots.txt", ROBOTS, content_type="text/plain")
    web.add("other.example", "/third.json", '{"n": 3}', content_type="application/json")
    return web.start()


def renderer_for(web, tmp_path):
    fetcher = fetcher_for(web, tmp_path)
    renderer = BrowserRenderer(fetcher, timeout=25.0)
    ok, reason = renderer.available()
    if not ok:
        pytest.skip(f"browser rendering unavailable: {reason}")
    return fetcher, renderer


def test_a_renders_sub_requests_ask_the_tasks_gate_and_none_is_sent_after_the_stop(tmp_path):
    """The page's first script is waiting for its answer when the task is stopped. The script then arrives and
    runs, and the two requests it leads to are refused without a request; the render ends with what it has, and
    last_events says 'stopped'."""
    release, stop = threading.Event(), threading.Event()
    web = scripted_web(release)
    try:
        fetcher, renderer = renderer_for(web, tmp_path)
        scope = lambda ask: fetcher.gated(ask)                                   # noqa: E731
        wanted = lambda url: "the task was cancelled" if stop.is_set() else None   # noqa: E731
        out = {}

        def run():
            try:
                out["result"] = renderer.render("http://app.example/", budget=Budget(max_browser_sessions=1),
                                                usage=Usage(), gate=(wanted, scope))
            except BaseException as error:
                out["error"] = error
        worker = threading.Thread(target=run)
        worker.start()
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not web.requests_to("app.example", "/first.js"):
                time.sleep(0.01)
            assert web.requests_to("app.example", "/first.js")
            stop.set()
        finally:
            release.set()
        worker.join(40)
        assert not worker.is_alive() and "error" not in out, out.get("error")
        paths = [(r["host"], r["path"]) for r in settled(web)]
        assert ("app.example", "/second.json") not in paths and not [p for p in paths if p[0] == "other.example"]
        codes = {e["url"]: e.get("code") or e["outcome"] for e in renderer.last_events}
        assert codes["http://app.example/second.json"] == "stopped"
        assert codes["http://other.example/third.json"] == "stopped"
    finally:
        release.set()
        web.stop()


def test_a_render_sub_request_to_a_host_denied_now_is_refused_and_the_others_are_served(tmp_path):
    web = scripted_web()
    try:
        fetcher, renderer = renderer_for(web, tmp_path)

        def wanted(url):
            return Stopped("the site was denied", denied=True) if url and "other.example" in url else None
        result = renderer.render("http://app.example/", budget=Budget(max_browser_sessions=1), usage=Usage(),
                                 gate=(wanted, lambda ask: fetcher.gated(ask)))
        assert 'second: {"n": 2}' in result.body.decode()
        codes = {e["url"]: e.get("code") or e["outcome"] for e in renderer.last_events}
        assert codes["http://other.example/third.json"] == "blocked_policy"
        assert codes["http://app.example/second.json"] == "served"
        assert web.requests_to("other.example") == []
    finally:
        web.stop()


def test_a_render_without_a_gate_is_what_it_was(tmp_path):
    web = scripted_web()
    try:
        _, renderer = renderer_for(web, tmp_path)
        result = renderer.render("http://app.example/", budget=Budget(max_browser_sessions=1), usage=Usage())
        assert 'second: {"n": 2}' in result.body.decode()
        assert web.requests_to("other.example", "/third.json")
    finally:
        web.stop()


def test_the_runs_gate_for_other_threads_reads_the_owners_stops_and_says_which(tmp_path):
    """_Run.elsewhere(): the verdicts the searches and renders ask for, read from the file at that moment."""
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    try:
        state.set_config(db, {"enabled": True}, T0)
        task = orchestrator.submit(db, QUESTION, T0, mode="fact_verification")

        class RunLike:
            id, holder = task["id"], None

            def services_db_path(self):
                return str(path)
        lookout = orchestrator._Lookout(RunLike())
        try:
            assert lookout.verdict() is None and lookout.verdict("https://a.example/x") is None
            state.set_config(db, {"deny_domains": ["a.example"]}, T0 + 1)
            denied = lookout.verdict("https://www.a.example/x")
            assert isinstance(denied, Stopped) and denied.denied and denied.why == orchestrator.SITE_DENIED
            assert lookout.verdict() is None and lookout.verdict("https://b.example/") is None
            state.set_config(db, {"enabled": False}, T0 + 2)
            off = lookout.verdict("https://b.example/")
            assert off.why == "web access was turned off" and not off.denied
        finally:
            lookout.close()
        assert lookout.verdict().why == "the reader moved on"
    finally:
        db.close()


def test_corpus_provider_runs_are_unchanged_by_the_gate(tmp_path):
    """A plain run through the gated search path: same sources and usage as the registry gives without a gate."""
    web = FixtureWeb()
    web.add("one.example", "/robots.txt", ROBOTS, content_type="text/plain")
    web.add("one.example", "/zephyr", page("Zephyr compiler 4.2.0 boards", [
        "The Zephyr compiler version 4.2.0 supports 12 boards according to one.example."]))
    web.start()
    try:
        runs = []
        for gated in (True, False):
            path = tmp_path / f"g{int(gated)}" / "individual.sqlite"
            path.parent.mkdir()
            db = store.create(path, T0)
            try:
                state.set_config(db, {"enabled": True, "min_domain_interval": 0.0}, T0)
                task = orchestrator.submit(db, QUESTION, T0, mode="fact_verification")
                provider = CorpusProvider([{"url": "http://one.example/zephyr", "title": "Zephyr compiler 4.2.0 boards",
                                            "snippet": "the Zephyr compiler version 4.2.0 and the boards it supports"}])
                services = svc_mod.build(db, path, fixture=web, providers=[provider], clock=lambda: T0)
                if not gated:                # a registry that takes no gate: called as before
                    inner = services.registry.search
                    services.registry.search = lambda plan, *, budget, usage, sent=None: inner(
                        plan, budget=budget, usage=usage, sent=sent)
                try:
                    done = orchestrator.run(db, task["id"], services)
                finally:
                    services.close()
                usage = {k: v for k, v in done["usage"].items() if k != "seconds"}
                runs.append((done["status"], usage, sorted(s["url"] for s in state.sources(db, done["id"]))))
            finally:
                db.close()
        assert runs[0] == runs[1] and runs[0][0] == "completed"
    finally:
        web.stop()
