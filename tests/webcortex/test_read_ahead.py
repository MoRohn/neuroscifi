"""Read-ahead (config.read_ahead): the reader fetches the next candidates ahead of their turn, one per host, and the
task still reads, charges, records and reports exactly what one-at-a-time reading does.

Two levels: SafeFetcher.speculate / claim / abandon on their own (charges replayed in order, held-back cache and
robots writes, the budget stopping a claim where a fetch would stop), and whole research runs on FixtureWeb compared
between read_ahead 1 and read_ahead 4 (consumed order, usage, sources, events, cancellation, budget exhaustion,
crash and resume, the per-host interval)."""
import random
import threading
import time

import pytest

from neuroscifi.instance import store
from neuroscifi.webcortex import orchestrator, services as svc_mod, state
from neuroscifi.webcortex.cache import PublicCache
from neuroscifi.webcortex.fixtureweb import FixtureWeb
from neuroscifi.webcortex.net import NetPolicy, SafeFetcher
from neuroscifi.webcortex.providers.corpus import CorpusProvider
from neuroscifi.webcortex.types import Budget, BudgetExceeded, Usage

T0 = 1_795_200_000.0            # 2026-11-21
QUESTION = "Which boards does the Zephyr compiler version 4.2.0 support?"
UNUSED = ("prefetch_unused_requests", "prefetch_unused_bytes")


def page(title, paragraphs):
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (f"<html><head><title>{title}</title><meta property='article:published_time' "
            f"content='2026-11-18T10:00:00Z'></head><body><article><h1>{title}</h1>{body}</article></body></html>")


# eight candidates on six hosts (two hosts carry two each); one page is missing (a failed fetch costs a request, not
# a page slot) and one is disallowed by its site's robots.txt
PAGES = [
    ("one.example", "/zephyr-boards", "Zephyr compiler 4.2.0 boards", 200),
    ("two.example", "/zephyr-42", "Zephyr 4.2.0 supported boards list", 200),
    ("one.example", "/zephyr-more", "More Zephyr compiler 4.2.0 boards", 200),
    ("three.example", "/gone", "Zephyr compiler 4.2.0 board support (removed)", 404),
    ("four.example", "/private/zephyr", "Zephyr compiler 4.2.0 boards, members only", 200),
    ("five.example", "/zephyr-review", "Zephyr compiler 4.2.0 review on three boards", 200),
    ("six.example", "/zephyr-notes", "Zephyr compiler 4.2.0 release notes and boards", 200),
    ("five.example", "/zephyr-faq", "Zephyr compiler 4.2.0 boards FAQ", 200),
]


def fixture_web(delay=0.0):
    web = FixtureWeb()
    for n, (host, path, title, status) in enumerate(PAGES):
        paragraphs = [f"The Zephyr compiler version 4.2.0 supports {10 + n} boards according to {host}.",
                      f"Source {n} lists the nRF52 and STM32 boards for version 4.2.0 of the Zephyr compiler."]
        if status == 200:
            web.add(host, path, page(title, paragraphs), delay=delay)
    web.add("four.example", "/robots.txt", "User-agent: *\nDisallow: /private/\n", content_type="text/plain")
    return web.start()


def corpus():
    return CorpusProvider([{"url": f"http://{host}{path}", "title": title,
                            "snippet": f"{title}: the Zephyr compiler version 4.2.0 and the boards it supports"}
                           for host, path, title, _ in PAGES])


class Spy:
    """Delegates to the run's SafeFetcher and counts read-ahead fetches started; `before(n)` runs before the n-th
    page fetch the reader makes (1-based), `after(n)` once it returned."""

    def __init__(self, inner, before=None, after=None):
        self.inner, self.before, self.after, self.started, self.fetches = inner, before, after, [], 0

    def fetch(self, url, **kwargs):
        if kwargs.get("purpose", "page") != "page":
            return self.inner.fetch(url, **kwargs)
        self.fetches += 1
        if self.before:
            self.before(self.fetches)
        result = self.inner.fetch(url, **kwargs)
        if self.after:
            self.after(self.fetches)
        return result

    def speculate(self, url, **kwargs):
        started = self.inner.speculate(url, **kwargs)
        if started:
            self.started.append(url)
        return started

    def __getattr__(self, name):
        return getattr(self.inner, name)


class Crash(BaseException):
    """The worker process dying (nothing in the run catches a BaseException)."""


def individual(tmp_path, name, window, *, interval=0.0):
    path = tmp_path / name / "individual.sqlite"
    path.parent.mkdir()
    db = store.create(path, T0)
    state.set_config(db, {"enabled": True, "min_domain_interval": interval, "read_ahead": window}, T0)
    return db, path


def services_for(db, path, web, *, clock=None, sleep=time.sleep, before=None, after=None):
    services = svc_mod.build(db, path, fixture=web, providers=[corpus()], clock=clock or (lambda: T0), sleep=sleep)
    services.fetcher = Spy(services.fetcher, before, after)
    return services


def settled(web, seconds=3.0):
    """The fixture log once no request has arrived for a moment (an abandoned read-ahead fetch may still be
    finishing a response it had already asked for)."""
    deadline, last = time.monotonic() + seconds, -1
    while time.monotonic() < deadline and len(web.log) != last:
        last = len(web.log)
        time.sleep(0.1)
    return list(web.log)


def outcome(db, done):
    """Everything a task produced that read-ahead must not change."""
    report = done["result"] or {}
    task = state.task(db, done["id"])
    return {"status": done["status"], "error": done["error"],
            "usage": {k: v for k, v in done["usage"].items() if k not in UNUSED + ("seconds",)},
            "sources": [(s["url"], s["retrieval_mode"], s.get("cited")) for s in report.get("sources") or []],
            "answer": [a.get("text") for a in report.get("answer") or []],
            "limitations": report.get("limitations") or [],
            "documents": [(d["url"], d["sha256"]) for d in (task["checkpoint"] or {}).get("documents", [])],
            "events": [(e["kind"], e.get("stage"), e.get("detail")) for e in state.events(db, done["id"])]}


def research(tmp_path, name, window, *, budget=None, before=None, delay=0.0):
    web = fixture_web(delay)
    db, path = individual(tmp_path, name, window)
    try:
        task = orchestrator.submit(db, QUESTION, T0, mode="fact_verification", budget=budget)
        services = services_for(db, path, web, before=before)
        try:
            done = orchestrator.run(db, task["id"], services)
        finally:
            services.close()
        log = settled(web)
        cache = PublicCache(svc_mod.cache_path(path))
        try:
            cached = {f"http://{host}{p}" for host, p, _, _ in PAGES if cache.lookup(f"http://{host}{p}")}
        finally:
            cache.close()
        return {"outcome": outcome(db, done), "usage": done["usage"], "started": services.fetcher.started,
                "log": log, "cached": cached, "done": done}
    finally:
        db.close()
        web.stop()


# ---- whole runs: read_ahead 4 against read_ahead 1 ----------------------------------------------------------------

def test_read_ahead_reads_charges_and_records_exactly_what_one_at_a_time_reading_does(tmp_path):
    one = research(tmp_path, "one", 1, budget={"max_pages": 4}, delay=0.05)
    four = research(tmp_path, "four", 4, budget={"max_pages": 4}, delay=0.05)
    assert one["started"] == [] and len(four["started"]) >= 2          # read-ahead really ran at 4, never at 1
    assert four["outcome"] == one["outcome"]
    assert one["outcome"]["usage"]["pages"] == 4 and one["outcome"]["documents"]
    # what the task did not use: never cached, and every request the fixture saw is charged or counted as unused
    assert four["cached"] == one["cached"]
    assert len(four["log"]) == four["usage"]["requests"] + four["usage"].get("prefetch_unused_requests", 0)
    assert len(one["log"]) == one["usage"]["requests"] and not set(UNUSED) & set(one["usage"])


def test_read_ahead_fetches_one_page_per_host_and_never_shortens_a_sites_interval(tmp_path):
    web = fixture_web(delay=0.02)
    db, path = individual(tmp_path, "interval", 4, interval=0.3)
    try:
        task = orchestrator.submit(db, QUESTION, T0, mode="fact_verification", budget={"max_pages": 6})
        services = services_for(db, path, web, clock=time.time)
        try:
            done = orchestrator.run(db, task["id"], services)
        finally:
            services.close()
        assert done["status"] in ("completed", "partial"), done["error"]
        assert len(services.fetcher.started) >= 2
        log = settled(web)
    finally:
        db.close()
        web.stop()
    by_host = {}
    for request in log:
        by_host.setdefault(request["host"], []).append(request["at"])
    gaps = [b - a for times in by_host.values() for a, b in zip(times, times[1:])]
    assert gaps and min(gaps) >= 0.3 - 0.02      # the fixture stamps arrival; 20 ms of scheduling slack
    assert len({r["host"] for r in log}) >= 4      # several sites really were read at once


def cancel(connection, task_id):
    orchestrator.cancel(connection, task_id, T0 + 1)


def web_off(connection, _task_id):
    state.set_config(connection, {"enabled": False}, T0 + 1)


def deny(connection, _task_id):
    state.set_config(connection, {"deny_domains": ["two.example", "five.example", "six.example"]}, T0 + 1)


@pytest.mark.parametrize("interrupt", [cancel, web_off, deny], ids=["cancel", "web_off", "deny_mid_run"])
def test_an_interruption_with_read_ahead_in_flight_ends_the_run_exactly_as_one_at_a_time(tmp_path, monkeypatch,
                                                                                          interrupt):
    """Once the first page is read, another connection cancels the task, turns web access off or denies hosts (the
    configuration is re-read before every step here). The run ends exactly as one-at-a-time reading ends it; the
    pages fetched ahead reached their sites, so they count as unused, and are never charged, cached or recorded."""
    monkeypatch.setattr(orchestrator, "CONFIG_RECHECK", 0.0)
    runs = {}
    for window in (1, 4):
        web = fixture_web(delay=0.05)
        db, path = individual(tmp_path, f"w{window}", window)
        try:
            task = orchestrator.submit(db, QUESTION, T0, mode="fact_verification")

            def after_first(n, task_id=task["id"], path=path):
                if n == 1:
                    other = store.connect(path)
                    try:
                        interrupt(other, task_id)
                    finally:
                        other.close()
            services = services_for(db, path, web, after=after_first)
            with store.write(db):    # the run holds the lease, so cancel() requests instead of cancelling outright
                state.update_task(db, task["id"], T0, lease_holder=services.holder, lease_expires_at=T0 + 600)
            try:
                done = orchestrator.run(db, task["id"], services)
            finally:
                services.close()
            log = settled(web)
            cache = PublicCache(svc_mod.cache_path(path))
            try:
                cached = [u for u in services.fetcher.started if cache.lookup(u)]
            finally:
                cache.close()
            runs[window] = {"outcome": outcome(db, done), "usage": done["usage"], "log": log, "cached": cached,
                            "started": services.fetcher.started}
        finally:
            db.close()
            web.stop()
    one, four = runs[1], runs[4]
    assert one["outcome"]["status"] in ("cancelled", "partial")
    assert four["outcome"] == one["outcome"]
    assert four["started"] and four["usage"].get("prefetch_unused_requests", 0) >= 1
    assert len(one["log"]) == one["usage"]["requests"]
    assert len(four["log"]) == four["usage"]["requests"] + four["usage"]["prefetch_unused_requests"]
    assert four["cached"] == []


@pytest.mark.parametrize("budget", [{"max_requests": 5}, {"max_bytes": 1500}], ids=["requests", "bytes"])
def test_budget_exhaustion_with_read_ahead_in_flight_ends_exactly_as_one_at_a_time(tmp_path, budget):
    one = research(tmp_path, "one", 1, budget=budget)
    four = research(tmp_path, "four", 4, budget=budget)
    assert four["outcome"] == one["outcome"]
    assert any("budget" in x for x in one["outcome"]["limitations"])
    assert four["cached"] == one["cached"]
    assert len(four["log"]) == four["usage"]["requests"] + four["usage"].get("prefetch_unused_requests", 0)


EDGES = [{"max_requests": n} for n in (1, 2, 3, 4, 6, 7, 9, 12)] + \
        [{"max_bytes": n} for n in (200, 450, 700, 950, 1300, 2000, 2700)]


@pytest.mark.parametrize("budget", EDGES, ids=[f"{k}={v}" for edge in EDGES for k, v in edge.items()])
def test_at_every_budget_edge_read_ahead_ends_as_one_at_a_time_and_stays_inside_the_budget(tmp_path, budget):
    """The fetches ahead of a task share its budget (they send a request, and read a body byte, only while what the
    task was charged, sent unused and holds ahead leaves room). Wherever the budget ends the task, it reads, charges
    and reports what one-at-a-time reading does, and what was sent ahead unused never exceeds the budget itself."""
    one = research(tmp_path, "one", 1, budget=budget)
    four = research(tmp_path, "four", 4, budget=budget)
    assert four["outcome"] == one["outcome"]
    assert four["cached"] == one["cached"]
    assert len(four["log"]) == four["usage"]["requests"] + four["usage"].get("prefetch_unused_requests", 0)
    assert len(one["log"]) == one["usage"]["requests"]
    if "max_requests" in budget:
        assert four["usage"].get("prefetch_unused_requests", 0) <= budget["max_requests"]
    else:       # at the byte limit each fetch ahead reads one byte more, as the reader's own read does
        assert four["usage"].get("prefetch_unused_bytes", 0) <= budget["max_bytes"] + len(four["started"])


def random_web(rng):
    """A small random web and a tight budget: two to five sites, three to six candidates, each a chain of zero to
    three redirects within and across sites ending in a page of some size that answers after 0 to 0.1 s; sites
    without a robots.txt, and sites whose robots.txt redirects, to a file on the same site or on another one (as
    example.com/robots.txt does to www.example.com/robots.txt). Returns (routes to register, candidate URLs,
    budget)."""
    hosts = [f"h{i}.example" for i in range(rng.randint(2, 5))]
    no_robots = {h for h in hosts if rng.random() < 0.2}
    robots_elsewhere = {h: rng.choice(hosts) for h in hosts if h not in no_robots and rng.random() < 0.3}
    routes, candidates = [], []
    for host in hosts:
        if host in robots_elsewhere:
            target = robots_elsewhere[host]
            routes.append(("redirect", host, "/robots.txt", f"http://{target}/robots-{host}.txt"))
            routes.append(("robots", target, f"/robots-{host}.txt"))
        elif host not in no_robots:
            routes.append(("robots", host, "/robots.txt"))
    for n in range(rng.randint(3, 6)):
        host, path = rng.choice(hosts), f"/c{n}"
        candidates.append(f"http://{host}{path}")
        for k in range(rng.randint(0, 3)):
            following = rng.choice(hosts) if rng.random() < 0.5 else host
            routes.append(("redirect", host, path, f"http://{following}/c{n}_{k}"))
            host, path = following, f"/c{n}_{k}"
        routes.append(("page", host, path, n, rng.choice([2, 20, 200]), rng.choice([0, 0, 0.03, 0.1])))
    budget = {"max_requests": rng.randint(3, 14), "max_bytes": rng.choice([4000, 20000, 60000, 20_000_000])}
    return hosts, routes, candidates, budget


def run_random_web(tmp_path, name, window, hosts, routes, candidates, budget):
    web = FixtureWeb()
    for host in hosts:
        web._ip_for(host)
    for route in routes:
        if route[0] == "redirect":
            web.add_redirect(route[1], route[2], route[3])
        elif route[0] == "robots":
            web.add(route[1], route[2], "User-agent: *\nAllow: /\n", content_type="text/plain")
        else:
            _, host, path, n, lines, delay = route
            web.add(host, path, page(f"Zephyr compiler 4.2.0 boards ({host} {n})", [
                f"The Zephyr compiler version 4.2.0 supports {10 + n} boards according to {host}."] + [
                f"Board list line {k} of {host} for the Zephyr compiler version 4.2.0." for k in range(lines)]),
                delay=delay)
    web.start()
    db, path = individual(tmp_path, name, window)
    try:
        task = orchestrator.submit(db, QUESTION, T0, mode="fact_verification", budget=budget)
        provider = CorpusProvider([{"url": url, "title": f"Zephyr compiler 4.2.0 boards ({n})",
                                    "snippet": "the Zephyr compiler version 4.2.0 and the boards it supports"}
                                   for n, url in enumerate(candidates)])
        services = svc_mod.build(db, path, fixture=web, providers=[provider], clock=lambda: T0)
        try:
            done = orchestrator.run(db, task["id"], services)
        finally:
            services.close()
        log = settled(web)
        return {"outcome": outcome(db, done), "usage": done["usage"], "requests_reaching_sites": len(log)}
    finally:
        db.close()
        web.stop()


@pytest.mark.parametrize("seed", [3, 21, 117, 404])
def test_random_webs_under_tight_budgets_end_the_same_at_one_page_at_a_time_and_read_ahead(tmp_path, seed):
    """Redirect chains, shared sites, robots.txt files that redirect (also onto another site), slow pages, and a
    budget that ends the task somewhere in the middle: whatever happens ahead (a fetch stopped at the shared
    budget, at a busy site, inside a robots.txt walk, cut, continued, thrown away), the task ends exactly as one
    page at a time ends it (status, charged usage, sources, answer, limitations, documents, events), every request
    that reached a site is charged or counted unused, and unused read-ahead stays within the request budget and
    within the task's unused allowance."""
    rng = random.Random(seed)
    for n in range(8):
        hosts, routes, candidates, budget = random_web(rng)
        one = run_random_web(tmp_path, f"s{seed}n{n}w1", 1, hosts, routes, candidates, budget)
        four = run_random_web(tmp_path, f"s{seed}n{n}w4", 4, hosts, routes, candidates, budget)
        where = (seed, n, routes, candidates, budget)
        assert four["outcome"] == one["outcome"], where
        assert one["requests_reaching_sites"] == one["usage"]["requests"], where
        unused = four["usage"].get("prefetch_unused_requests", 0)
        assert four["requests_reaching_sites"] == four["usage"]["requests"] + unused, where
        assert unused <= budget["max_requests"], where
        # and within the task's unused allowance: everything it sends is at most max_requests plus that
        allowance = Budget(**budget).unused_allowance(4)[0]
        assert unused <= allowance and four["requests_reaching_sites"] <= budget["max_requests"] + allowance, where


def test_crash_with_read_ahead_in_flight_then_resume_uses_only_checkpointed_pages(tmp_path):
    def crash_before_second(n):
        if n == 2:
            raise Crash()

    results = {}
    for window in (1, 4):
        web = fixture_web(delay=0.05)
        db, path = individual(tmp_path, f"crash{window}", window)
        try:
            task = orchestrator.submit(db, QUESTION, T0, mode="fact_verification")
            services = services_for(db, path, web, before=crash_before_second)
            with pytest.raises(Crash):
                orchestrator.run(db, task["id"], services)
            services.close()
            settled(web)
            mid = state.task(db, task["id"])
            assert mid["status"] == "reading" and len(mid["checkpoint"]["documents"]) == 1
            later = services_for(db, path, web, clock=lambda: T0 + 60)
            try:
                done = orchestrator.resume(db, task["id"], later)
            finally:
                later.close()
            assert done["status"] in ("completed", "partial"), done["error"]
            assert "resumed" in [e["kind"] for e in state.events(db, task["id"])]
            results[window] = outcome(db, done)
        finally:
            db.close()
            web.stop()
    assert results[4] == results[1]


# ---- the fetcher: speculate, claim, abandon -------------------------------------------------------------------

def fetcher_for(web, tmp_path, **policy):
    return SafeFetcher(NetPolicy(min_domain_interval=0.0, **policy), cache=PublicCache(tmp_path / "c.sqlite"),
                       resolver=web.resolver, connector=web.connector, clock=lambda: T0, sleep=lambda s: None)


def wait_finished(fetcher):
    for spec in list(fetcher._speculations.values()):
        assert spec.finished.wait(5)


def test_a_claimed_read_ahead_fetch_is_charged_and_cached_only_when_claimed(web, tmp_path):
    web.add("a.example", "/robots.txt", "User-agent: *\nAllow: /\n", content_type="text/plain")
    web.add("a.example", "/p", "<p>hello</p>", headers={"ETag": '"v1"'})
    fetcher, budget, usage = fetcher_for(web, tmp_path), Budget(), Usage()
    assert fetcher.speculate("http://a.example/p", usage=usage)
    wait_finished(fetcher)
    assert len(web.log) == 2 and usage.requests == 0 and usage.bytes == 0      # sent, not yet charged
    assert fetcher.cache.lookup("http://a.example/p") is None and fetcher.cache.robots("http~80~.a.example") is None
    result = fetcher.fetch("http://a.example/p", budget=budget, usage=usage)
    assert result.body == b"<p>hello</p>" and len(web.log) == 2                # the claim sent nothing more
    assert (usage.requests, usage.bytes) == (2, len(result.body) + len("User-agent: *\nAllow: /\n"))
    assert fetcher.cache.lookup("http://a.example/p") and fetcher.cache.robots("http~80~.a.example")
    assert not set(UNUSED) & set(usage.to_json())


def test_an_abandoned_read_ahead_fetch_counts_as_unused_and_leaves_no_cache_or_robots_entry(web, tmp_path):
    web.add("a.example", "/robots.txt", "User-agent: *\nAllow: /\n", content_type="text/plain")
    web.add("a.example", "/p", "<p>hello</p>")
    fetcher, usage = fetcher_for(web, tmp_path), Usage()
    fetcher.speculate("http://a.example/p", usage=usage)
    wait_finished(fetcher)
    fetcher.abandon("http://a.example/p", usage=usage)
    assert (usage.requests, usage.bytes) == (0, 0)
    assert usage.prefetch_unused_requests == 2
    assert usage.prefetch_unused_bytes == len("User-agent: *\nAllow: /\n") + len("<p>hello</p>")
    assert fetcher.cache.lookup("http://a.example/p") is None and fetcher.cache.robots("http~80~.a.example") is None
    with fetcher._lock:
        assert not fetcher._robots and not fetcher._speculations
    # the next fetch is a plain one: it asks robots.txt again, exactly as if nothing had been fetched ahead
    fetcher.fetch("http://a.example/p", budget=Budget(), usage=usage)
    assert usage.requests == 2 and len(web.log) == 4


def test_an_abandoned_running_read_ahead_fetch_sends_nothing_more(web, tmp_path):
    web.add("slow.example", "/robots.txt", "User-agent: *\nAllow: /\n", content_type="text/plain", delay=0.3)
    web.add("slow.example", "/p", "<p>late</p>")
    fetcher, usage = fetcher_for(web, tmp_path), Usage()
    fetcher.speculate("http://slow.example/p", usage=usage)
    time.sleep(0.1)                         # robots.txt is on its way
    fetcher.abandon("http://slow.example/p", usage=usage)
    time.sleep(0.6)
    assert [r["path"] for r in web.log] == ["/robots.txt"]      # the page request was never sent
    assert usage.prefetch_unused_requests == 1 and usage.requests == 0


def test_a_read_ahead_fetch_is_fetched_again_when_its_robots_entry_was_committed_meanwhile(web, tmp_path):
    """Sequentially the earlier fetch would have fetched robots.txt and the later one used it: the read-ahead fetch
    that fetched it too is not used (its robots request counts as unused) and the claim fetches as usual."""
    web.add("a.example", "/robots.txt", "User-agent: *\nAllow: /\n", content_type="text/plain")
    web.add("a.example", "/first", "<p>first</p>")
    web.add("a.example", "/second", "<p>second</p>")
    fetcher, budget, usage = fetcher_for(web, tmp_path), Budget(), Usage()
    fetcher.speculate("http://a.example/second", usage=usage)
    wait_finished(fetcher)
    fetcher.fetch("http://a.example/first", budget=budget, usage=usage)      # commits a.example's robots entry
    fetcher.fetch("http://a.example/second", budget=budget, usage=usage)
    assert usage.requests == 3                                               # robots, first, second
    assert (usage.prefetch_unused_requests, len(web.log)) == (2, 5)


def test_a_read_ahead_claim_stops_at_the_request_budget_exactly_like_a_fetch(web, tmp_path):
    web.add("a.example", "/p", "<p>hello</p>")
    budget = Budget(max_requests=1)

    def stopped(fetcher, usage):
        with pytest.raises(BudgetExceeded) as info:
            fetcher.fetch("http://a.example/p", budget=budget, usage=usage)
        return str(info.value), usage.requests, usage.bytes

    plain_usage = Usage()
    plain = stopped(fetcher_for(web, tmp_path / "plain"), plain_usage)      # robots.txt takes the one request
    ahead_fetcher, ahead_usage = fetcher_for(web, tmp_path / "ahead"), Usage()
    ahead_fetcher.speculate("http://a.example/p", usage=ahead_usage)
    wait_finished(ahead_fetcher)
    assert stopped(ahead_fetcher, ahead_usage) == plain
    assert ahead_usage.prefetch_unused_requests == 1                        # the page request it sent anyway
    assert ahead_fetcher.cache.lookup("http://a.example/p") is None


def test_a_read_ahead_claim_stops_at_the_byte_budget_exactly_like_a_fetch(web, tmp_path):
    body = "<p>" + "x" * 4000 + "</p>"
    web.add("a.example", "/p", body)
    budget = Budget(max_bytes=1000)

    def stopped(fetcher, usage):
        with pytest.raises(BudgetExceeded) as info:
            fetcher.fetch("http://a.example/p", budget=budget, usage=usage)
        return str(info.value), usage.requests, usage.bytes

    plain = stopped(fetcher_for(web, tmp_path / "plain"), Usage())
    ahead_fetcher, ahead_usage = fetcher_for(web, tmp_path / "ahead"), Usage()
    ahead_fetcher.speculate("http://a.example/p", usage=ahead_usage)
    wait_finished(ahead_fetcher)
    assert stopped(ahead_fetcher, ahead_usage) == plain
    assert ahead_usage.prefetch_unused_bytes == len(body)


def test_read_ahead_setting_is_validated_and_one_turns_it_off(tmp_path):
    db, _ = individual(tmp_path, "config", 1)
    try:
        assert state.get_config(db).read_ahead == 1
        for bad in (0, 17, 2.5, "4"):
            with pytest.raises(Exception, match="read_ahead"):
                state.set_config(db, {"read_ahead": bad}, T0)
        assert state.set_config(db, {"read_ahead": 3}, T0).read_ahead == 3
    finally:
        db.close()


def test_read_ahead_threads_do_not_outlive_an_abandon(web, tmp_path):
    web.add("a.example", "/p", "<p>hello</p>", delay=0.2)
    fetcher, usage = fetcher_for(web, tmp_path), Usage()
    fetcher.speculate("http://a.example/p", usage=usage)
    fetcher.abandon("http://a.example/p", usage=usage)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and any(t.name == "nsf-read-ahead" for t in threading.enumerate()):
        time.sleep(0.05)
    assert not any(t.name == "nsf-read-ahead" for t in threading.enumerate())


def test_a_fetch_ahead_abandoned_between_two_of_its_reads_reads_one_byte_more_not_the_rest_of_the_body(web, tmp_path,
                                                                                                     monkeypatch):
    """Wave 3 fix lane (the reconciliation lane's race): the reader's abandon lands between two body reads of the
    fetch's own thread, made deterministic here by abandoning from inside its first take(). The rest of the page is
    already in the response's buffer, so shutting the connection does not stop a read: before the fix the next take()
    granted a whole chunk and the fetch read and counted all of it, past the task's byte budget."""
    from neuroscifi.webcortex import net
    body = "<p>" + "x" * 4000 + "</p>"
    web.add("a.example", "/robots.txt", "User-agent: *\nAllow: /\n", content_type="text/plain")
    web.add("a.example", "/p", body)
    fetcher, usage, budget = fetcher_for(web, tmp_path), Usage(), Budget(max_bytes=300)
    real, takes = net._Speculation.take, []

    def take(self, wanted):
        page = sum(1 for e in self.events if e[0] == "request") >= 2     # the page's body (robots.txt read before)
        if page and len(takes) == 1:
            self.abandon()              # the reader's abandon, after the body's first read and before the next
        granted = real(self, wanted)
        if page:
            takes.append(granted)
        return granted
    monkeypatch.setattr(net._Speculation, "take", take)
    assert fetcher.speculate("http://a.example/p", usage=usage, budget=budget)
    wait_finished(fetcher)
    assert len(takes) >= 2 and takes[1] == 0                       # nothing granted once abandoned
    assert usage.prefetch_unused_bytes <= budget.max_bytes + 1     # the one byte that tells the body goes on
    assert usage.bytes == 0 and usage.requests == 0
