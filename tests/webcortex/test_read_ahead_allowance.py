"""What read-ahead may waste: the unused allowance of a task (Budget.unused_allowance).

A task's charged requests stay within max_requests. What its pages fetched ahead send and the task then does not
use is counted apart (Usage.prefetch_unused_requests) and used to be bounded only by max_requests again, so a run
whose fetches ahead all became unusable could send twice its budget. Now that part has its own allowance, a tenth
of the budget and never less than a full window needs; the fetcher holds every fetch ahead to it request by
request, and with it spent the task reads one page at a time, which is the same reading."""
import threading

import pytest

from neuroscifi.instance import store
from neuroscifi.webcortex import orchestrator, services as svc_mod, state
from neuroscifi.webcortex.fixtureweb import FixtureWeb
from neuroscifi.webcortex.providers.corpus import CorpusProvider
from neuroscifi.webcortex.types import UNUSED_BYTES_FLOOR, Budget, Usage

from test_read_ahead_stops import (QUESTION, ROBOTS, T0, Held, big_pages, fetcher_for, finish_all, page,
                                   redirect_sites, settled, slow_connector)

ROOMY = 10**9


def test_the_allowance_is_a_tenth_of_the_budget_and_never_less_than_a_full_window_needs():
    assert Budget().unused_allowance(4) == (8, 2_000_000)                     # 40 requests, 20 MB
    assert Budget(max_requests=200, max_bytes=60_000_000).unused_allowance(6) == (20, 6_000_000)
    assert Budget(max_requests=12, max_bytes=1_000_000).unused_allowance(4) == (8, UNUSED_BYTES_FLOOR)
    assert Budget(max_requests=400).unused_allowance(1)[0] == 40
    assert Budget(max_requests=0, max_bytes=0).unused_allowance(0) == (2, UNUSED_BYTES_FLOOR)
    # a full window holds a robots.txt and a page for each page ahead, and one of them may be wasted
    for window in range(2, 9):
        assert Budget(max_requests=10).unused_allowance(window)[0] >= 2 * (window - 1) + 2


# ---- the fetcher: requests are held to the allowance exactly ----------------------------------------------------

def test_fetches_ahead_send_and_hold_no_more_than_the_allowance_and_the_rest_is_the_readers(web, tmp_path):
    """Six sites, four requests each (robots.txt and a chain of three), a budget that would allow them all, an
    allowance of eight requests. Four fetches ahead start (each holds two) and each stops before its third request;
    their claims go on from there on the task's own budget, so every page is read, nothing is requested twice and
    nothing counts as unused."""
    hosts = [f"{c}.example" for c in "abcdef"]
    redirect_sites(web, hosts)
    fetcher, budget, usage = fetcher_for(web, tmp_path), Budget(max_requests=100), Usage()
    started = [fetcher.speculate(f"http://{host}/p", usage=usage, budget=budget, allowance=(8, ROOMY))
               for host in hosts]
    assert started == [True, True, True, True, False, False]
    finish_all(fetcher)
    assert len(settled(web, quiet=0.2)) == 8 and fetcher._ahead[id(usage)][0] == 8
    for host in hosts:
        assert fetcher.fetch(f"http://{host}/p", budget=budget, usage=usage).body == b"<p>page</p>"
    paths = [(r["host"], r["path"]) for r in web.log]
    assert len(paths) == len(set(paths)) == 24                   # every request once
    assert (usage.requests, usage.prefetch_unused_requests) == (24, 0) and fetcher._ahead == {}


def test_unused_requests_keep_counting_until_nothing_is_fetched_ahead(web, tmp_path):
    """One wasted fetch leaves room for three pages ahead, three for two, and with the allowance spent nothing is
    started ahead any more; the reader's own fetches are untouched."""
    hosts = [f"h{n}.example" for n in range(12)]
    for host in hosts:
        web.add(host, "/robots.txt", ROBOTS, content_type="text/plain")
        web.add(host, "/p", "<p>page</p>")
    fetcher, budget, usage = fetcher_for(web, tmp_path), Budget(max_requests=100), Usage()
    todo = iter(hosts)

    def ahead(count):
        started = [h for h in (next(todo) for _ in range(count))
                   if fetcher.speculate(f"http://{h}/p", usage=usage, budget=budget, allowance=(8, ROOMY))]
        finish_all(fetcher)
        return started

    def waste(started):
        for host in started:
            fetcher.abandon(f"http://{host}/p", usage=usage)

    first = ahead(1)
    waste(first)
    assert usage.prefetch_unused_requests == 2
    second = ahead(4)
    assert len(second) == 3                                      # 2 unused + 3 x 2 held = 8
    waste(second[:1])
    for host in second[1:]:
        fetcher.fetch(f"http://{host}/p", budget=budget, usage=usage)     # used: charged, not unused
    assert (usage.prefetch_unused_requests, usage.requests) == (4, 4)
    third = ahead(3)
    assert len(third) == 2                                       # 4 unused + 2 x 2 held = 8
    waste(third)
    assert usage.prefetch_unused_requests == 8
    before = len(settled(web, quiet=0.1))
    assert ahead(2) == [] and len(settled(web, quiet=0.1)) == before and fetcher._ahead == {}
    assert fetcher.fetch("http://h11.example/p", budget=budget, usage=usage).status == 200
    assert usage.prefetch_unused_requests == 8


def test_no_page_is_fetched_ahead_once_the_unused_bytes_reach_the_allowance(web, tmp_path):
    hosts = ["a.example", "b.example", "c.example"]
    big_pages(web, hosts, size=4000)
    fetcher, budget, usage = fetcher_for(web, tmp_path), Budget(max_requests=100), Usage()
    assert fetcher.speculate("http://a.example/p", usage=usage, budget=budget, allowance=(100, 5000))
    finish_all(fetcher)
    fetcher.abandon("http://a.example/p", usage=usage)
    assert 4000 <= usage.prefetch_unused_bytes < 5000
    assert fetcher.speculate("http://b.example/p", usage=usage, budget=budget, allowance=(100, 5000))
    finish_all(fetcher)
    fetcher.abandon("http://b.example/p", usage=usage)
    assert usage.prefetch_unused_bytes >= 5000
    before = len(web.log)
    assert fetcher.speculate("http://c.example/p", usage=usage, budget=budget, allowance=(100, 5000)) is False
    assert len(settled(web, quiet=0.1)) == before and fetcher._ahead == {}


def test_an_allowance_without_a_budget_is_no_account(web, tmp_path):
    """The account lives with the task's budget; a caller that gives none gets a fetch ahead as before."""
    web.add("a.example", "/p", "<p>hello</p>")
    fetcher, usage = fetcher_for(web, tmp_path), Usage()
    assert fetcher.speculate("http://a.example/p", usage=usage, allowance=(0, 0))
    finish_all(fetcher)
    assert fetcher._ahead == {} and fetcher.fetch("http://a.example/p", usage=usage).body == b"<p>hello</p>"


def test_a_request_charged_and_not_yet_sent_stays_in_the_account_when_its_fetch_is_abandoned(web, tmp_path):
    """abandon() cannot know whether a request recorded just before will still go out, so it neither counts it as
    unused nor gives its room back until the exchange has ended. Before, the room was given back at once, and a
    fetch ahead started in that moment could take it while the first request was still on its way out."""
    for host in ("a.example", "b.example"):
        web.add(host, "/robots.txt", ROBOTS, content_type="text/plain")
        web.add(host, "/p", "<p>hi</p>")
    connecting, proceed = threading.Event(), threading.Event()
    fetcher, budget, usage = fetcher_for(web, tmp_path), Budget(max_requests=2), Usage()
    fetcher.robots_allowed("http://a.example/p")                  # known: the fetch ahead goes straight to the page
    before = len(web.log)
    fetcher._connector = slow_connector(web, connecting, proceed)
    assert fetcher.speculate("http://a.example/p", usage=usage, budget=budget)
    spec = next(iter(fetcher._speculations.values()))
    assert connecting.wait(5)                                     # the page request is charged; its connection opens
    fetcher.abandon("http://a.example/p", usage=usage)
    assert usage.prefetch_unused_requests == 0 and fetcher._ahead[id(usage)][0] == 1
    assert fetcher.speculate("http://b.example/p", usage=usage, budget=budget) is False     # 1 held + 2 > 2
    proceed.set()
    assert spec.finished.wait(5)
    assert settled(web, quiet=0.2)[before:] == []                 # it never went out
    assert usage.prefetch_unused_requests == 0 and fetcher._ahead == {}
    fetcher._connector = web.connector
    assert fetcher.speculate("http://b.example/p", usage=usage, budget=budget)             # the room is back
    finish_all(fetcher)
    fetcher.abandon("http://b.example/p", usage=usage)
    assert usage.prefetch_unused_requests == 2 and fetcher._ahead == {}


# ---- whole runs -------------------------------------------------------------------------------------------------

def zephyr(host, n):
    return page(f"Zephyr compiler 4.2.0 boards ({host})", [
        f"The Zephyr compiler version 4.2.0 supports {10 + n} boards according to {host}.",
        f"Source {n} lists the nRF52 and STM32 boards for version 4.2.0 of the Zephyr compiler."])


class Arriving(Held):
    """A held route that also says when its request has arrived."""

    def __init__(self, body, release, arrived):
        super().__init__(body, release, "text/html")
        self.arrived = arrived

    @property
    def delay(self):
        self.arrived.set()
        self.release.wait(20)
        return 0.0


def chains(release, arrived, hosts, hops):
    """The reader's first page (hosts[0]) is held; every other candidate is a chain of `hops` redirects on its own
    site, so a fetch ahead of it can send up to hops + 2 requests."""
    web = FixtureWeb()
    for n, host in enumerate(hosts):
        web.add(host, "/robots.txt", ROBOTS, content_type="text/plain")
        if n == 0:
            web.routes[(host, "/r0")] = Arriving(zephyr(host, 0), release, arrived)
            continue
        for k in range(hops):
            web.add_redirect(host, f"/r{k}", f"http://{host}/r{k + 1}")
        web.add(host, f"/r{hops}", zephyr(host, n))
    return web.start()


def run_chains(tmp_path, window, max_requests, *, deny, hosts=4, hops=5):
    """One task over chains(); while the reader waits for its first page, optionally the owner denies an unrelated
    domain (the fetcher's policy is replaced, so every fetch ahead in flight is thrown away and fetched again).
    Returns (requests the fetches ahead had sent by then, everything that reached the sites, the finished task,
    its sources)."""
    names = [f"h{n}.example" for n in range(hosts)]
    release, arrived = threading.Event(), threading.Event()
    web = chains(release, arrived, names, hops)
    path = tmp_path / f"w{window}" / "individual.sqlite"
    path.parent.mkdir(parents=True)
    db = store.create(path, T0)
    state.set_config(db, {"enabled": True, "min_domain_interval": 0.0, "read_ahead": window}, T0)
    task = orchestrator.submit(db, QUESTION, T0, mode="fact_verification", budget={"max_requests": max_requests})
    holder, out = svc_mod.holder_id(), {}
    with store.write(db):
        state.update_task(db, task["id"], T0, lease_holder=holder, lease_expires_at=T0 + 600)
    db.close()
    provider = CorpusProvider([{"url": f"http://{host}/r0", "title": f"Zephyr compiler 4.2.0 boards ({n})",
                                "snippet": "the Zephyr compiler version 4.2.0 and the boards it supports"}
                               for n, host in enumerate(names)])

    def run():
        connection = store.open_existing(path)
        services = svc_mod.build(connection, path, fixture=web, providers=[provider], clock=lambda: T0)
        services.holder = holder
        try:
            out["done"] = orchestrator.run(connection, task["id"], services)
            out["sources"] = sorted(s["url"] for s in state.sources(connection, task["id"]))
        except BaseException as error:
            out["error"] = error
        finally:
            services.close()
            connection.close()

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert arrived.wait(10)
        ahead = len(settled(web, quiet=0.4)) - 2                  # all but the reader's robots.txt and page
        if deny:
            other = store.connect(path)
            try:
                state.set_config(other, {"deny_domains": ["unrelated.example"]}, T0 + 1)
            finally:
                other.close()
    finally:
        release.set()
    worker.join(60)
    assert not worker.is_alive() and "error" not in out, out.get("error")
    try:
        return ahead, settled(web), out["done"], out["sources"]
    finally:
        web.stop()


@pytest.mark.parametrize("max_requests", [20, 12])
def test_a_run_whose_fetches_ahead_all_become_unusable_sends_at_most_its_budget_and_its_allowance(tmp_path,
                                                                                                   max_requests):
    """The reviewers' worst case: the fetches ahead send what they may, then a changed deny list makes every one of
    them unusable and the reader fetches the pages itself on its whole budget. That reached 32 requests under a
    budget of 20, and exactly twice the budget in a constructed case. With the allowance (8 here) the fetches ahead
    stop at 8 requests, and what the task reads, charges and reports is what one page at a time gives."""
    allowance = Budget(max_requests=max_requests).unused_allowance(4)[0]
    assert allowance == 8
    _, one_log, one, one_sources = run_chains(tmp_path / "one", 1, max_requests, deny=True)
    ahead, log, done, sources = run_chains(tmp_path / "four", 4, max_requests, deny=True)
    usage = done["usage"]
    assert ahead == allowance                                     # all the room they get, and no more
    assert usage.get("prefetch_unused_requests", 0) <= allowance
    assert len(log) == usage["requests"] + usage.get("prefetch_unused_requests", 0) <= max_requests + allowance
    assert usage["requests"] <= max_requests

    def outcome(task, urls):
        charged = {k: v for k, v in task["usage"].items() if k not in ("seconds", "prefetch_unused_requests",
                                                                       "prefetch_unused_bytes")}
        return task["status"], charged, urls, (task["result"] or {}).get("limitations")
    assert outcome(done, sources) == outcome(one, one_sources)
    assert len(one_log) == one["usage"]["requests"]


def test_without_a_stop_the_fetches_ahead_are_continued_and_nothing_is_wasted(tmp_path):
    """The same web with nothing going wrong: the fetches ahead stop at the allowance, their claims go on from
    there, and the run sends exactly what one page at a time sends."""
    _, one_log, one, one_sources = run_chains(tmp_path / "one", 1, 40, deny=False)
    ahead, log, done, sources = run_chains(tmp_path / "four", 4, 40, deny=False)
    assert ahead == 8 and done["usage"].get("prefetch_unused_requests", 0) == 0
    assert len(log) == len(one_log) == done["usage"]["requests"] == one["usage"]["requests"]
    assert sources == one_sources and done["status"] == one["status"] == "completed"


def test_with_the_allowance_spent_a_run_reads_one_page_at_a_time(tmp_path):
    """The task's unused requests use the allowance up while its first page is being arranged: the fetcher starts
    nothing ahead, and from the next page on read() does not even ask."""
    web = FixtureWeb()
    hosts = [f"h{n}.example" for n in range(6)]
    for n, host in enumerate(hosts):
        web.add(host, "/robots.txt", ROBOTS, content_type="text/plain")
        web.add(host, "/zephyr", zephyr(host, n))
    web.start()
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    try:
        state.set_config(db, {"enabled": True, "min_domain_interval": 0.0, "read_ahead": 4}, T0)
        task = orchestrator.submit(db, QUESTION, T0, mode="fact_verification")
        provider = CorpusProvider([{"url": f"http://{host}/zephyr", "title": f"Zephyr compiler 4.2.0 boards ({n})",
                                    "snippet": "the Zephyr compiler version 4.2.0 and the boards it supports"}
                                   for n, host in enumerate(hosts)])
        services = svc_mod.build(db, path, fixture=web, providers=[provider], clock=lambda: T0)
        inner, asked = services.fetcher.speculate, []

        def speculate(url, **kwargs):
            if not asked:
                kwargs["usage"].charge_unused(requests=7)        # 7 of the 8 are gone: no room for a page ahead
            started = inner(url, **kwargs)
            asked.append((url, started, kwargs["usage"].pages, kwargs["allowance"]))
            return started

        services.fetcher.speculate = speculate
        try:
            done = orchestrator.run(db, task["id"], services)
        finally:
            services.close()
        assert done["status"] == "completed" and done["usage"]["pages"] >= 3
        assert asked and all(allowance == (8, 2_000_000) for *_, allowance in asked)
        assert not any(started for _, started, _, _ in asked)     # the fetcher: nothing starts
        assert {pages for _, _, pages, _ in asked} == {0}         # read(): never asked again after the first page
        assert done["usage"]["prefetch_unused_requests"] == 7
        assert len(web.log) == done["usage"]["requests"]          # one page at a time: nothing but what was used
    finally:
        db.close()
        web.stop()
