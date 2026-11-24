"""No request starts after a stop, and what read-ahead sends stays inside the allowances.

A stop is the owner's: the task cancelled, web access turned off, a domain denied. Every fetch asks before each of
its network steps (a name lookup, robots.txt, the page, a redirect hop): the reader's own fetches through
SafeFetcher.gated, a fetch made ahead of its turn through the gate it was started with. These tests count requests
where they arrive (FixtureWeb.log) and name lookups where they are made (the resolver), and issue each stop while
every fetch is provably waiting for an answer, so "after the stop" is an ordering, not a timing.

Also here: a fetch ahead that is abandoned is cut on the wire and counted exactly; it never sends a request its
task's request budget has no room for; tasks under a daily allowance read one page at a time and the allowances
count unused read-ahead traffic; the default window and how saved settings load."""
import json
import threading
import time

import pytest

from neuroscifi.instance import store
from neuroscifi.webcortex import config as wconfig, orchestrator, scheduler, services as svc_mod, state
from neuroscifi.webcortex.cache import PublicCache
from neuroscifi.webcortex.fixtureweb import FixtureWeb
from neuroscifi.webcortex.net import NetPolicy, SafeFetcher
from neuroscifi.webcortex.providers.corpus import CorpusProvider
from neuroscifi.webcortex.types import Budget, BudgetExceeded, FetchError, Usage

T0 = 1_795_200_000.0            # 2026-11-21
QUESTION = "Which boards does the Zephyr compiler version 4.2.0 support?"
ROBOTS = "User-agent: *\nAllow: /\n"
HOSTS = ("one.example", "two.example", "three.example", "four.example", "five.example", "six.example")


class Held:
    """A FixtureWeb route whose answer waits until release() (the request is logged on arrival, before the wait)."""

    def __init__(self, body, release, content_type="text/plain"):
        self.status, self.body, self.headers, self.hits = 200, body.encode(), {"Content-Type": content_type}, 0
        self.release = release

    @property
    def delay(self):
        self.release.wait(10)
        return 0.0


class Stop(Exception):
    """What a test gate raises."""


def fetcher_for(web, tmp_path, *, resolver=None, connector=None, **policy):
    tmp_path.mkdir(parents=True, exist_ok=True)
    return SafeFetcher(NetPolicy(min_domain_interval=0.0, **policy), cache=PublicCache(tmp_path / "c.sqlite"),
                       resolver=resolver or web.resolver, connector=connector or web.connector, clock=lambda: T0,
                       sleep=lambda s: None)


def redirecting(web):
    """http://a.example/p redirects to http://b.example/q; both sites have a robots.txt. A full fetch takes four
    requests (a's robots.txt, /p, b's robots.txt, /q) and two name lookups (a, b)."""
    web.add("a.example", "/robots.txt", ROBOTS, content_type="text/plain")
    web.add_redirect("a.example", "/p", "http://b.example/q")
    web.add("b.example", "/robots.txt", ROBOTS, content_type="text/plain")
    web.add("b.example", "/q", "<p>there</p>")
    lookups = []

    def resolver(host, port):
        lookups.append(host)
        return web.resolver(host, port)
    return resolver, lookups


# The network steps of that fetch, in order, as the gate is asked: before a hop's name lookup, before each
# connection is opened (after the politeness wait) and once more when it is open, before the request is written on
# it. Stopping at step k must leave exactly the requests and lookups before it (a connection opened at the step
# before is closed with nothing sent).
STEPS = [("http://a.example/p", 0, 0),             # 1 before a.example's lookup
         ("http://a.example/robots.txt", 0, 1),    # 2 before robots.txt (the lookup is shared with the page)
         ("http://a.example/robots.txt", 0, 1),    # 3 before connecting for robots.txt
         ("http://a.example/robots.txt", 0, 1),    # 4 connected: before the robots.txt request is written
         ("http://a.example/p", 1, 1),             # 5 before connecting for the page
         ("http://a.example/p", 1, 1),             # 6 connected: before the page request is written
         ("http://b.example/q", 2, 1),             # 7 before b.example's lookup (the redirect hop)
         ("http://b.example/robots.txt", 2, 2),    # 8
         ("http://b.example/robots.txt", 2, 2),    # 9
         ("http://b.example/robots.txt", 2, 2),    # 10 connected: before b's robots.txt request is written
         ("http://b.example/q", 3, 2),             # 11
         ("http://b.example/q", 3, 2)]             # 12 connected: before the final request is written


def settled(web, seconds=3.0, quiet=0.3):
    """The fixture log once no request has arrived for `quiet` seconds."""
    deadline, last, since = time.monotonic() + seconds, -1, time.monotonic()
    while time.monotonic() < deadline:
        if len(web.log) != last:
            last, since = len(web.log), time.monotonic()
        elif time.monotonic() - since >= quiet:
            break
        time.sleep(0.02)
    return list(web.log)


# ---- the fetcher: the gate is asked before every network step ---------------------------------------------------

def test_the_gate_is_asked_before_every_lookup_and_request_with_the_url_about_to_be_contacted(web, tmp_path):
    resolver, _ = redirecting(web)
    fetcher, asked = fetcher_for(web, tmp_path, resolver=resolver), []
    with fetcher.gated(asked.append):
        result = fetcher.fetch("http://a.example/p", budget=Budget(), usage=Usage())
    assert result.body == b"<p>there</p>" and asked == [url for url, _, _ in STEPS]
    fetcher.fetch("http://b.example/q", budget=Budget(), usage=Usage())      # outside gated(): nobody is asked
    assert len(asked) == len(STEPS)


@pytest.mark.parametrize("step", range(1, len(STEPS) + 1))
def test_a_readers_fetch_stopped_by_its_gate_sends_nothing_from_that_step_on(web, tmp_path, step):
    resolver, lookups = redirecting(web)
    fetcher, usage, asked = fetcher_for(web, tmp_path, resolver=resolver), Usage(), []

    def gate(url):
        asked.append(url)
        if len(asked) == step:
            raise Stop(url)

    with pytest.raises(Stop), fetcher.gated(gate):
        fetcher.fetch("http://a.example/p", budget=Budget(), usage=usage)
    url, requests, names = STEPS[step - 1]
    assert asked[-1] == url and len(asked) == step
    assert len(settled(web, quiet=0.1)) == requests and len(lookups) == names
    assert usage.requests == requests            # charged exactly what was sent: the gate is asked before the charge
    with fetcher._lock:
        assert not fetcher._inflight             # the stopped fetch left nothing behind
    assert fetcher.fetch("http://a.example/p", budget=Budget(), usage=Usage()).body == b"<p>there</p>"


@pytest.mark.parametrize("step", range(1, len(STEPS) + 1))
def test_a_fetch_ahead_stopped_by_its_gate_sends_nothing_from_that_step_on(web, tmp_path, step):
    resolver, lookups = redirecting(web)
    fetcher, usage, asked = fetcher_for(web, tmp_path, resolver=resolver), Usage(), []

    def wanted(url):
        asked.append(url)
        return "stopped by the test" if len(asked) >= step else None

    assert fetcher.speculate("http://a.example/p", usage=usage, wanted=wanted)
    spec = next(iter(fetcher._speculations.values()))
    assert spec.finished.wait(5)
    url, requests, names = STEPS[step - 1]
    assert asked == [u for u, _, _ in STEPS[:step]] and spec.stopped == "stopped by the test"
    assert len(settled(web, quiet=0.1)) == requests and len(lookups) == names
    # the reader's own fetch then does the work itself, and the stopped one counts as unused, to the request
    result = fetcher.fetch("http://a.example/p", budget=Budget(), usage=usage)
    assert result.body == b"<p>there</p>"
    assert usage.prefetch_unused_requests == requests and usage.requests == 4
    assert len(web.log) == usage.requests + usage.prefetch_unused_requests


def test_a_gate_that_fails_stops_the_fetch_ahead(web, tmp_path):
    resolver, lookups = redirecting(web)
    fetcher, usage = fetcher_for(web, tmp_path, resolver=resolver), Usage()

    def wanted(url):
        raise RuntimeError("the individual's file cannot be read")

    fetcher.speculate("http://a.example/p", usage=usage, wanted=wanted)
    spec = next(iter(fetcher._speculations.values()))
    assert spec.finished.wait(5) and "gate failed" in spec.stopped
    assert settled(web, quiet=0.1) == [] and lookups == []


def test_a_caller_waiting_on_a_fetch_its_leader_was_stopped_in_fetches_for_itself(web, tmp_path):
    web.add("a.example", "/p", "<p>hello</p>")
    fetcher = fetcher_for(web, tmp_path)
    at_gate, go, out = threading.Event(), threading.Event(), {}

    def gate(_url):
        at_gate.set()
        go.wait(5)
        raise Stop()

    def leader():
        try:
            with fetcher.gated(gate):
                fetcher.fetch("http://a.example/p", budget=Budget(), usage=Usage())
        except Stop:
            out["leader"] = "stopped"

    def follower():
        out["follower"] = fetcher.fetch("http://a.example/p", budget=Budget(), usage=Usage()).body

    first = threading.Thread(target=leader)
    first.start()
    assert at_gate.wait(5)
    second = threading.Thread(target=follower)
    second.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not fetcher.stats()["totals"].get("coalesced"):
        time.sleep(0.01)
    go.set()
    first.join(5)
    second.join(5)
    assert out == {"leader": "stopped", "follower": b"<p>hello</p>"}       # the leader's stop is not the follower's


# ---- the fetcher: an abandoned fetch ahead is cut, and counted to the request ----------------------------------

def test_abandoning_a_fetch_ahead_cuts_its_request_on_the_wire(web, tmp_path):
    release = threading.Event()
    web.add("slow.example", "/robots.txt", ROBOTS, content_type="text/plain")
    web.routes[("slow.example", "/p")] = Held("<p>late</p>", release, "text/html")
    fetcher, usage = fetcher_for(web, tmp_path), Usage()
    try:
        fetcher.speculate("http://slow.example/p", usage=usage)
        spec = next(iter(fetcher._speculations.values()))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(web.log) < 2:      # the page request has arrived and is held
            time.sleep(0.01)
        assert [r["path"] for r in web.log] == ["/robots.txt", "/p"]
        started = time.monotonic()
        fetcher.abandon("http://slow.example/p", usage=usage)
        assert spec.finished.wait(2) and time.monotonic() - started < 2      # it did not wait for the answer
        assert spec.result is None and isinstance(spec.error, BudgetExceeded)
        assert usage.prefetch_unused_requests == 2 == len(web.log) and usage.requests == 0
    finally:
        release.set()


def test_a_fetch_ahead_abandoned_before_its_request_went_out_sends_and_counts_nothing(web, tmp_path):
    """Abandoned between the charge and the send (here: while the connection is being opened). The connection is
    shut down as soon as it exists, nothing is sent on it, and the request that was never sent is not counted."""
    web.add("a.example", "/robots.txt", ROBOTS, content_type="text/plain")
    web.add("a.example", "/p", "<p>hello</p>")
    connecting, proceed = threading.Event(), threading.Event()

    def connector(ip, port, timeout):
        connecting.set()
        proceed.wait(5)
        return web.connector(ip, port, timeout)

    fetcher, usage = fetcher_for(web, tmp_path, connector=connector), Usage()
    fetcher.speculate("http://a.example/p", usage=usage)
    spec = next(iter(fetcher._speculations.values()))
    assert connecting.wait(5)
    fetcher.abandon("http://a.example/p", usage=usage)
    proceed.set()
    assert spec.finished.wait(5)
    assert settled(web, quiet=0.2) == []
    assert (usage.prefetch_unused_requests, usage.prefetch_unused_bytes, usage.requests) == (0, 0, 0)


def test_a_claim_that_gives_up_waiting_cuts_the_fetch_ahead_before_it_fetches_again(web, tmp_path, monkeypatch):
    """The fetch ahead is still waiting for its page when the reader's patience ends. It is cut first, and its
    thread has ended before the reader's own request reaches the site: one talker per host."""
    release = threading.Event()
    web.add("slow.example", "/robots.txt", ROBOTS, content_type="text/plain")
    web.routes[("slow.example", "/p")] = Held("<p>late</p>", release, "text/html")
    fetcher, budget, usage = fetcher_for(web, tmp_path), Budget(), Usage()
    monkeypatch.setattr(fetcher, "_claim_wait", lambda: 0.2)
    fetcher.speculate("http://slow.example/p", usage=usage)
    spec = next(iter(fetcher._speculations.values()))
    seen = {}

    def watch():      # the reader's own requests are the third (robots.txt again) and fourth (the page, held)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and len(web.log) < 3:
            time.sleep(0.005)
        seen["ahead_had_ended"] = spec.finished.is_set()
        release.set()

    watcher = threading.Thread(target=watch)
    watcher.start()
    try:
        result = fetcher.fetch("http://slow.example/p", budget=budget, usage=usage)
    finally:
        release.set()
        watcher.join(5)
    assert result.body == b"<p>late</p>" and seen == {"ahead_had_ended": True}
    assert [r["path"] for r in web.log] == ["/robots.txt", "/p", "/robots.txt", "/p"]
    assert (usage.requests, usage.prefetch_unused_requests) == (2, 2)


# ---- the fetcher: a fetch ahead stays inside the task's request budget --------------------------------------------

def test_a_fetch_ahead_sends_no_request_the_tasks_request_budget_has_no_room_for(web, tmp_path):
    """Three requests are needed (robots.txt, /p, the redirect target); the budget has two. One page at a time
    sends two and is refused the third. A fetch ahead that knows the budget sends the same two, and the claim ends
    exactly as the plain fetch does, with nothing unused."""
    web.add("a.example", "/robots.txt", ROBOTS, content_type="text/plain")
    web.add_redirect("a.example", "/p", "http://a.example/q")
    web.add("a.example", "/q", "<p>there</p>")
    budget = Budget(max_requests=2)

    def stopped(fetcher, usage):
        with pytest.raises(BudgetExceeded) as info:
            fetcher.fetch("http://a.example/p", budget=budget, usage=usage)
        return str(info.value), usage.requests, usage.bytes

    plain = stopped(fetcher_for(web, tmp_path / "plain"), Usage())
    assert len(web.log) == 2
    ahead, usage = fetcher_for(web, tmp_path / "ahead"), Usage()
    ahead.speculate("http://a.example/p", usage=usage, budget=budget)
    spec = next(iter(ahead._speculations.values()))
    assert spec.finished.wait(5)
    assert len(settled(web, quiet=0.1)) == 4                       # two more, not three
    assert stopped(ahead, usage) == plain
    assert usage.prefetch_unused_requests == 0 and len(web.log) == 4
    assert ahead.cache.robots("http~80~.a.example")                # what a plain fetch keeps, the claim kept


def test_a_fetch_ahead_is_not_started_without_room_for_a_typical_fetch(web, tmp_path):
    """The reader used the budget's last requests: nothing is started ahead (a fetch ahead needs room for
    robots.txt and the page, and holds it from the start), and the reader's own fetch is refused as before."""
    web.add("a.example", "/p", "<p>hello</p>")
    budget, usage = Budget(max_requests=3), Usage()
    fetcher = fetcher_for(web, tmp_path)
    usage.charge(budget, requests=2)                          # one request left: not a whole fetch
    assert fetcher.speculate("http://a.example/p", usage=usage, budget=budget) is False
    assert not fetcher._speculations and fetcher._ahead == {} and settled(web, quiet=0.1) == []
    usage.charge(budget, requests=1)
    with pytest.raises(BudgetExceeded):
        fetcher.fetch("http://a.example/p", budget=budget, usage=usage)
    assert usage.requests == 3 and usage.prefetch_unused_requests == 0 and web.log == []


def test_a_fetch_ahead_gives_back_what_it_held_and_did_not_use(web, tmp_path):
    """Room for two requests is held from the start; a fetch that needed one (robots.txt was known) gives the other
    back when it ends, so the next fetch ahead can start."""
    web.add("a.example", "/robots.txt", ROBOTS, content_type="text/plain")
    web.add("a.example", "/p", "<p>one</p>")
    web.add("b.example", "/p", "<p>two</p>")
    fetcher, budget, usage = fetcher_for(web, tmp_path), Budget(max_requests=4), Usage()
    fetcher.robots_allowed("http://a.example/p")              # a.example's robots.txt is known: one request needed
    before = len(web.log)
    assert fetcher.speculate("http://a.example/p", usage=usage, budget=budget)
    finish_all(fetcher)
    assert fetcher._ahead[id(usage)][0] == 1 and len(web.log) == before + 1
    assert fetcher.speculate("http://b.example/p", usage=usage, budget=budget)      # 1 held + 2 fit in 4
    finish_all(fetcher)
    assert fetcher._ahead[id(usage)][0] == 3
    for host in ("a.example", "b.example"):
        fetcher.abandon(f"http://{host}/p", usage=usage)
    assert usage.prefetch_unused_requests == 3 and fetcher._ahead == {}


# ---- the fetcher: the fetches ahead of one task share that task's budget ----------------------------------------

def finish_all(fetcher):
    for spec in list(fetcher._speculations.values()):
        assert spec.finished.wait(5)


def redirect_sites(web, hosts):
    """Each site: robots.txt, and /p -> /q -> /r (four requests for one page)."""
    for host in hosts:
        web.add(host, "/robots.txt", ROBOTS, content_type="text/plain")
        web.add_redirect(host, "/p", f"http://{host}/q")
        web.add_redirect(host, "/q", f"http://{host}/r")
        web.add(host, "/r", "<p>page</p>")


def test_fetches_ahead_together_send_no_more_requests_than_the_task_has_room_for(web, tmp_path):
    """Security review L1: three fetches ahead of one task, four requests each, a request budget of four. Each used
    to check the budget against its own requests only, and twelve requests reached the sites."""
    hosts = ["a.example", "b.example", "c.example"]
    redirect_sites(web, hosts)
    fetcher, budget, usage = fetcher_for(web, tmp_path), Budget(max_requests=4), Usage()
    started = [fetcher.speculate(f"http://{host}/p", usage=usage, budget=budget) for host in hosts]
    assert started == [True, True, False]       # each holds a typical fetch from the start; the third has no room
    finish_all(fetcher)
    assert len(settled(web, quiet=0.2)) == 4 and usage.requests == 0
    assert fetcher._ahead[id(usage)][0] == 4                      # all of the task's room is held
    for host in hosts:
        fetcher.abandon(f"http://{host}/p", usage=usage)
    assert usage.prefetch_unused_requests == 4 == len(web.log) and fetcher._ahead == {}
    # what was sent unused keeps counting: a further fetch ahead of this task sends nothing
    web.add("d.example", "/p", "<p>more</p>")
    fetcher.speculate("http://d.example/p", usage=usage, budget=budget)
    finish_all(fetcher)
    fetcher.abandon("http://d.example/p", usage=usage)
    assert len(settled(web, quiet=0.2)) == 4 and usage.prefetch_unused_requests == 4 and fetcher._ahead == {}


def test_fetches_ahead_count_what_the_task_was_charged(web, tmp_path):
    hosts = ["a.example", "b.example", "c.example"]
    redirect_sites(web, hosts)
    fetcher, budget, usage = fetcher_for(web, tmp_path), Budget(max_requests=7), Usage()
    usage.charge(budget, requests=5)                              # the reader used five; two are left for all
    for host in hosts:
        fetcher.speculate(f"http://{host}/p", usage=usage, budget=budget)
    finish_all(fetcher)
    assert len(settled(web, quiet=0.2)) == 2


def test_a_fetch_ahead_that_ran_out_of_shared_room_is_continued_not_fetched_again(web, tmp_path):
    """The room a fetch ahead sees also counts what other fetches ahead hold, so in a redirect chain it can stop
    where a fetch made at its turn would go on. Its claim replays what it sent and goes on from that hop on the
    task's own budget: the reader gets what one page at a time gets, and no request is made twice. (Before, the
    fetch ahead was thrown away and fetched again: two more requests in this case.)"""
    redirect_sites(web, ["a.example", "b.example"])
    budget = Budget(max_requests=4)
    plain_fetcher, plain = fetcher_for(web, tmp_path / "plain"), Usage()
    expected = plain_fetcher.fetch("http://b.example/p", budget=budget, usage=plain)
    assert plain.requests == 4 and len(web.log) == 4
    fetcher, usage = fetcher_for(web, tmp_path / "ahead"), Usage()
    fetcher.speculate("http://a.example/p", usage=usage, budget=budget)
    fetcher.speculate("http://b.example/p", usage=usage, budget=budget)
    finish_all(fetcher)
    assert len(settled(web, quiet=0.2)) - 4 == 4                  # robots.txt and /p each: the task's four
    fetcher.abandon("http://a.example/p", usage=usage)            # the reader passed it by
    result = fetcher.fetch("http://b.example/p", budget=budget, usage=usage)
    assert (result.body, result.final_url, result.redirects) == (expected.body, expected.final_url,
                                                                 expected.redirects)
    assert usage.requests == plain.requests == 4 and usage.bytes == plain.bytes
    assert usage.prefetch_unused_requests == 2 and fetcher._ahead == {}           # a.example's two, and no more
    paths = [r["path"] for r in web.log[4:] if r["host"] == "b.example"]
    assert paths == ["/robots.txt", "/p", "/q", "/r"]             # each of b.example's four exactly once
    assert fetcher.cache.lookup("http://b.example/p") and fetcher.cache.robots("http~80~.b.example")


def test_a_fetch_ahead_that_ran_out_of_room_before_a_hops_robots_txt_is_continued_at_that_hop(web, tmp_path):
    """The redirect leads to another site, and the room ran out before that site's robots.txt. The claim goes on at
    the hop (the page's walk), fetching that robots.txt first, and returns the page, not the robots.txt."""
    web.add("a.example", "/robots.txt", ROBOTS, content_type="text/plain")
    web.add_redirect("a.example", "/p", "http://b.example/q")
    web.add("b.example", "/robots.txt", ROBOTS, content_type="text/plain")
    web.add("b.example", "/q", "<p>there</p>")
    web.add("c.example", "/robots.txt", ROBOTS, content_type="text/plain")
    web.add("c.example", "/p", "<p>other</p>")
    budget = Budget(max_requests=4)
    plain_fetcher, plain = fetcher_for(web, tmp_path / "plain"), Usage()
    expected = plain_fetcher.fetch("http://a.example/p", budget=budget, usage=plain)
    before = len(web.log)
    fetcher, usage = fetcher_for(web, tmp_path / "ahead"), Usage()
    fetcher.speculate("http://c.example/p", usage=usage, budget=budget)
    fetcher.speculate("http://a.example/p", usage=usage, budget=budget)
    finish_all(fetcher)
    assert len(settled(web, quiet=0.2)) - before == 4             # two each; b.example was not asked anything
    assert web.requests_to("b.example")[2:] == []
    fetcher.abandon("http://c.example/p", usage=usage)
    result = fetcher.fetch("http://a.example/p", budget=budget, usage=usage)
    assert (result.body, result.final_url, result.content_type, result.redirects) == \
        (expected.body, expected.final_url, expected.content_type, expected.redirects)
    assert result.body == b"<p>there</p>" and (usage.requests, usage.bytes) == (plain.requests, plain.bytes)
    assert [r["path"] for r in web.log[before:] if r["host"] == "b.example"] == ["/robots.txt", "/q"]
    assert usage.prefetch_unused_requests == 2 and fetcher._ahead == {}


def test_a_continued_fetch_ahead_is_refused_where_one_page_at_a_time_is(web, tmp_path):
    """Continued on the task's own budget: when that has no room either, the refusal is the plain fetch's."""
    redirect_sites(web, ["b.example"])
    budget = Budget(max_requests=3)

    def stopped(fetcher, usage):
        with pytest.raises(BudgetExceeded) as info:
            fetcher.fetch("http://b.example/p", budget=budget, usage=usage)
        return str(info.value), usage.requests, usage.bytes

    plain = stopped(fetcher_for(web, tmp_path / "plain"), Usage())
    assert len(web.log) == 3
    ahead, usage = fetcher_for(web, tmp_path / "ahead"), Usage()
    ahead.speculate("http://b.example/p", usage=usage, budget=budget)
    finish_all(ahead)
    assert stopped(ahead, usage) == plain
    assert len(settled(web, quiet=0.1)) == 6 and usage.prefetch_unused_requests == 0 and ahead._ahead == {}


def big_pages(web, hosts, size=4000):
    for host in hosts:
        web.add(host, "/robots.txt", ROBOTS, content_type="text/plain")
        web.add(host, "/p", "<p>" + "x" * (size - 7) + "</p>")


def test_fetches_ahead_together_read_no_more_bytes_than_the_task_has_room_for(web, tmp_path):
    """Three fetches ahead of one task, a 4000-byte page each, a byte budget of 1000. Each used to read its whole
    page (bounded by the document size limit only). Now they share the task's room; at the limit each reads one
    byte more, as the reader's own read does, to learn that the body goes on."""
    hosts = ["a.example", "b.example", "c.example"]
    big_pages(web, hosts)
    fetcher, budget, usage = fetcher_for(web, tmp_path), Budget(max_bytes=1000), Usage()
    for host in hosts:
        fetcher.speculate(f"http://{host}/p", usage=usage, budget=budget)
    finish_all(fetcher)
    held = {key: list(value) for key, value in getattr(fetcher, "_ahead", {}).items()}
    for host in hosts:
        fetcher.abandon(f"http://{host}/p", usage=usage)
    assert 3 * len(ROBOTS) < usage.prefetch_unused_bytes <= 1000 + len(hosts)
    assert usage.bytes == 0 and held[id(usage)][1] <= 1000 and fetcher._ahead == {}


def test_a_fetch_ahead_cut_at_the_byte_budget_is_claimed_exactly_like_a_fetch(web, tmp_path):
    big_pages(web, ["a.example"])
    budget = Budget(max_bytes=1000)

    def stopped(fetcher, usage):
        with pytest.raises(BudgetExceeded) as info:
            fetcher.fetch("http://a.example/p", budget=budget, usage=usage)
        return str(info.value), usage.requests, usage.bytes

    plain = stopped(fetcher_for(web, tmp_path / "plain"), Usage())
    assert len(web.log) == 2
    ahead, usage = fetcher_for(web, tmp_path / "ahead"), Usage()
    ahead.speculate("http://a.example/p", usage=usage, budget=budget)
    finish_all(ahead)
    assert stopped(ahead, usage) == plain
    assert len(settled(web, quiet=0.1)) == 4                       # the claim used it: nothing was fetched again
    # what arrived of the page beyond what the task was charged: the room that was left, and the one byte past it
    assert usage.prefetch_unused_bytes == 1000 - len(ROBOTS) + 1 and ahead._ahead == {}


def test_a_fetch_ahead_cut_early_at_the_byte_budget_is_fetched_again_when_the_page_fits(web, tmp_path):
    """Two fetches ahead, 3000 bytes each, a byte budget of 5000. The second is cut at the room the first leaves.
    The reader passes the first by and reaches the second: one page at a time it fits, so it is fetched again."""
    big_pages(web, ["a.example", "b.example"], size=3000)
    budget = Budget(max_bytes=5000)
    plain_fetcher, plain = fetcher_for(web, tmp_path / "plain"), Usage()
    expected = plain_fetcher.fetch("http://b.example/p", budget=budget, usage=plain)
    fetcher, usage = fetcher_for(web, tmp_path / "ahead"), Usage()
    fetcher.speculate("http://a.example/p", usage=usage, budget=budget)
    finish_all(fetcher)
    fetcher.speculate("http://b.example/p", usage=usage, budget=budget)
    finish_all(fetcher)
    assert fetcher._ahead[id(usage)][1] <= 5000
    fetcher.abandon("http://a.example/p", usage=usage)
    result = fetcher.fetch("http://b.example/p", budget=budget, usage=usage)
    assert result.body == expected.body and (usage.requests, usage.bytes) == (plain.requests, plain.bytes)
    assert fetcher._ahead == {}


# ---- whole runs: the stop lands while every fetch is waiting for an answer --------------------------------------

def page(title, paragraphs):
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (f"<html><head><title>{title}</title><meta property='article:published_time' "
            f"content='2026-11-18T10:00:00Z'></head><body><article><h1>{title}</h1>{body}</article></body></html>")


def held_web(release):
    """Six sites, one candidate each; every robots.txt answer waits for `release`."""
    web = FixtureWeb()
    for n, host in enumerate(HOSTS):
        web.add(host, "/zephyr", page(f"Zephyr compiler 4.2.0 boards ({host})", [
            f"The Zephyr compiler version 4.2.0 supports {10 + n} boards according to {host}.",
            f"Source {n} lists the nRF52 and STM32 boards for version 4.2.0 of the Zephyr compiler."]))
        web.routes[(host, "/robots.txt")] = Held(ROBOTS, release)
    return web.start()


def corpus():
    return CorpusProvider([{"url": f"http://{host}/zephyr", "title": f"Zephyr compiler 4.2.0 boards ({host})",
                            "snippet": "the Zephyr compiler version 4.2.0 and the boards it supports"}
                           for host in HOSTS])


def cancel(connection, task_id, _web):
    orchestrator.cancel(connection, task_id, T0 + 1)


def web_off(connection, _task_id, _web):
    state.set_config(connection, {"enabled": False}, T0 + 1)


def deny_all(connection, _task_id, _web):
    state.set_config(connection, {"deny_domains": list(HOSTS)}, T0 + 1)


def run_until_held(tmp_path, window, in_flight, interrupt, held=None):
    """Run the question with every robots.txt held; once `in_flight` requests wait at their sites (the reader's and
    the fetches ahead: nothing else can be sent), apply `interrupt` on another connection, note what has arrived,
    release the answers and let the run end. Returns what arrived by the stop, everything that ever arrived, and
    the finished task. `held` (a dict): the answers stay held for up to five more seconds after the stop, and
    held['ended'] says whether the run ended in that time, with held['seconds'] how long after the stop."""
    release = threading.Event()
    web = held_web(release)
    path = tmp_path / f"w{window}" / "individual.sqlite"
    path.parent.mkdir()
    db = store.create(path, T0)
    state.set_config(db, {"enabled": True, "min_domain_interval": 0.0, "read_ahead": window}, T0)
    task = orchestrator.submit(db, QUESTION, T0, mode="fact_verification")
    holder, out = svc_mod.holder_id(), {}
    with store.write(db):    # the run holds the lease, so cancel() requests instead of cancelling outright
        state.update_task(db, task["id"], T0, lease_holder=holder, lease_expires_at=T0 + 600)
    db.close()

    def run():
        connection = store.open_existing(path)
        services = svc_mod.build(connection, path, fixture=web, providers=[corpus()], clock=lambda: T0)
        services.holder = holder
        try:
            out["done"] = orchestrator.run(connection, task["id"], services)
        except BaseException as error:          # shown by the assertion below
            out["error"] = error
        finally:
            services.close()
            connection.close()

    worker = threading.Thread(target=run)
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and len(web.log) < in_flight:
            time.sleep(0.01)
        time.sleep(0.2)                         # nothing else is on its way: every fetch waits for its robots.txt
        assert len(web.log) == in_flight, [(r["host"], r["path"]) for r in web.log]
        other = store.connect(path)
        try:
            interrupt(other, task["id"], web)
        finally:
            other.close()
        at_stop = list(web.log)
        if held is not None:
            stopped_at = time.monotonic()
            worker.join(5)
            held.update(ended=not worker.is_alive(), seconds=time.monotonic() - stopped_at)
    finally:
        release.set()
    worker.join(30)
    assert not worker.is_alive() and "error" not in out, out.get("error")
    try:
        return at_stop, settled(web), out["done"]
    finally:
        web.stop()


@pytest.mark.parametrize("interrupt", [cancel, web_off, deny_all], ids=["cancel", "web_off", "deny"])
@pytest.mark.parametrize("window", [1, 4])
def test_no_request_starts_after_a_stop(tmp_path, window, interrupt):
    """`window` fetches are waiting for their site's robots.txt (the reader's and window - 1 ahead) when the owner
    stops the task. Their answers then arrive, and none of them sends its page request: what reached the sites by
    the stop is everything that ever reaches them. (Before, the reader's fetch went on to request its page, and so
    did each fetch ahead until the reader's next check.)"""
    at_stop, final, done = run_until_held(tmp_path, window, window, interrupt)
    assert {r["path"] for r in at_stop} == {"/robots.txt"} and len({r["host"] for r in at_stop}) == window
    assert [(r["host"], r["path"]) for r in final] == [(r["host"], r["path"]) for r in at_stop]
    if interrupt is deny_all:       # every candidate is denied now: nothing could be read
        assert done["status"] == "failed" and "blocked_policy" in done["error"], done["error"]
    else:
        assert done["status"] == ("cancelled" if interrupt is cancel else "partial"), done["error"]
    usage = done["usage"]
    assert usage["pages"] == 0
    # every request that reached a site is charged to the task or counted as unused read-ahead, and nothing else is
    assert usage["requests"] + usage.get("prefetch_unused_requests", 0) == len(final) == window
    assert usage["requests"] == 1 and usage.get("prefetch_unused_requests", 0) == window - 1


def test_a_site_denied_mid_run_gets_nothing_more_and_the_others_are_still_read(tmp_path):
    """The three sites being fetched ahead are denied while their robots.txt answers are awaited. They get nothing
    after the denial; the reader's own site and the two not yet started are read as usual, under the new list."""
    denied = []

    def deny_those_ahead(connection, _task_id, web):
        # the candidates are read in the order given: the reader is on the first site, the next three are ahead
        # (which request arrived first says nothing: the four are sent at the same moment)
        assert {r["host"] for r in web.log} == set(HOSTS[:4])
        denied.extend(HOSTS[1:4])
        state.set_config(connection, {"deny_domains": list(denied)}, T0 + 1)

    at_stop, final, done = run_until_held(tmp_path, 4, 4, deny_those_ahead)
    assert len(denied) == 3
    after = final[len(at_stop):]
    assert [r for r in after if r["host"] in denied] == []                 # nothing more to a denied site
    assert {r["host"] for r in after} == set(HOSTS) - set(denied)          # the allowed sites were read
    assert done["status"] == "partial" and done["usage"]["pages"] == 3, done["error"]
    assert done["usage"]["requests"] + done["usage"]["prefetch_unused_requests"] == len(final)
    assert done["usage"]["prefetch_unused_requests"] == 3


# ---- daily allowances ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("origin, capped", [("curiosity", True), ("refresh:7", True), ("mcp", True),
                                            ("cli", False), ("console", False), ("voice", False),
                                            ("monitor:3", False)])
def test_tasks_under_a_daily_allowance_read_one_page_at_a_time(tmp_path, origin, capped):
    assert orchestrator.daily_capped(origin) is capped
    assert capped == (scheduler._counted(origin, False) or scheduler._counted(origin, True))


def test_an_autonomous_task_fetches_nothing_ahead_at_any_window(tmp_path):
    release = threading.Event()
    release.set()
    web = held_web(release)
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    try:
        state.set_config(db, {"enabled": True, "min_domain_interval": 0.0, "read_ahead": 4}, T0)
        started = []
        for origin in ("curiosity", "cli"):
            task = orchestrator.submit(db, QUESTION + f" ({origin})", T0, mode="fact_verification", origin=origin)
            services = svc_mod.build(db, path, fixture=web, providers=[corpus()], clock=lambda: T0)
            inner, calls = services.fetcher.speculate, []

            def speculate(url, _inner=inner, _calls=calls, **kwargs):
                _calls.append(url)
                return _inner(url, **kwargs)

            services.fetcher.speculate = speculate
            try:
                done = orchestrator.run(db, task["id"], services)
            finally:
                services.close()
            assert done["status"] in ("completed", "partial"), done["error"]
            started.append(len(calls))
            if origin == "curiosity":
                assert not {"prefetch_unused_requests", "prefetch_unused_bytes"} & set(done["usage"])
                assert done["usage"]["requests"] == len(settled(web, quiet=0.1))
        assert started[0] == 0 and started[1] >= 2
    finally:
        db.close()
        web.stop()


def autonomous_task(db, usage, *, origin="curiosity", status="completed"):
    n = db.execute("SELECT count(*) FROM web_tasks").fetchone()[0]
    task = orchestrator.submit(db, f"What changed in Zephyr compiler release 4.{n}?", T0, origin=origin)
    with store.write(db):
        state.update_task(db, task["id"], T0 + 1, status=status, usage=usage)
    return task["id"]


def test_the_daily_allowances_count_unused_read_ahead_traffic(tmp_path):
    db = store.create(tmp_path / "individual.sqlite", T0)
    try:
        state.set_config(db, {"enabled": True, "daily_autonomous": {"tasks": 12, "requests": 400, "bytes": 1000}},
                         T0)
        cfg = state.get_config(db)
        autonomous_task(db, {"requests": 390, "bytes": 600, "prefetch_unused_requests": 9,
                             "prefetch_unused_bytes": 399})
        assert scheduler.autonomous_usage(db, T0 + 2) == {"tasks": 1, "requests": 399, "bytes": 999}
        assert scheduler.autonomous_remaining(db, T0 + 2, cfg) == {"requests": 1, "bytes": 1}
        assert scheduler._over_cap(db, T0 + 2, cfg) is None                 # one request and one byte are left
        autonomous_task(db, {"requests": 0, "bytes": 0, "prefetch_unused_requests": 1})
        assert scheduler.autonomous_usage(db, T0 + 2)["requests"] == 400
        assert scheduler.autonomous_remaining(db, T0 + 2, cfg)["requests"] == 0
        assert scheduler._over_cap(db, T0 + 2, cfg) == "daily autonomous cap reached: requests 400 of 400"
        with pytest.raises(Exception, match="daily autonomous cap reached: requests"):
            orchestrator.submit(db, "What is new in the Zephyr compiler?", T0 + 2, origin="curiosity")
    finally:
        db.close()


def test_the_mcp_allowance_counts_unused_read_ahead_traffic_too(tmp_path):
    db = store.create(tmp_path / "individual.sqlite", T0)
    try:
        state.set_config(db, {"enabled": True, "daily_mcp": {"tasks": 5, "requests": 50, "bytes": 1000}}, T0)
        cfg = state.get_config(db)
        autonomous_task(db, {"requests": 40, "bytes": 0, "prefetch_unused_requests": 10}, origin="mcp")
        assert scheduler.autonomous_usage(db, T0 + 2, mcp=True)["requests"] == 50
        assert scheduler.autonomous_remaining(db, T0 + 2, cfg, mcp=True)["requests"] == 0
    finally:
        db.close()


# ---- the default, and saved settings ------------------------------------------------------------------------------

def test_the_default_window_is_four_and_saved_settings_keep_what_they_say(tmp_path):
    assert wconfig.WebConfig().read_ahead == 4
    db = store.create(tmp_path / "individual.sqlite", T0)
    try:
        assert state.get_config(db).read_ahead == 4                         # nothing saved: the default
        # settings saved by 0.7.0 have no read_ahead: the default applies to them
        saved = state.get_config(db).to_json()
        saved.pop("read_ahead")
        with store.write(db):
            db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (wconfig.META_KEY, json.dumps(saved)))
        assert state.get_config(db).read_ahead == 4
        # the rollback is one setting, and it stays what the owner set through later changes of other settings
        assert state.set_config(db, {"read_ahead": 1}, T0).read_ahead == 1
        assert state.set_config(db, {"min_domain_interval": 2.0}, T0).read_ahead == 1
    finally:
        db.close()


def test_a_run_without_a_file_to_read_stops_from_reads_one_page_at_a_time(tmp_path):
    """The gate of the fetches ahead reads the owner's stops from the individual's file at request time. Services
    that name no file cannot give it one, so nothing is fetched ahead."""
    release = threading.Event()
    release.set()
    web = held_web(release)
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    try:
        state.set_config(db, {"enabled": True, "min_domain_interval": 0.0}, T0)
        task = orchestrator.submit(db, QUESTION, T0, mode="fact_verification")
        services = svc_mod.build(db, path, fixture=web, providers=[corpus()], clock=lambda: T0)
        services.db_path = None
        calls = []
        services.fetcher.speculate = lambda url, **kwargs: calls.append(url) or False
        try:
            done = orchestrator.run(db, task["id"], services)
        finally:
            services.close()
        assert done["status"] in ("completed", "partial") and calls == []
    finally:
        db.close()
        web.stop()


# ---- the gate of the fetches ahead reads the owner's stops at the moment it is asked ----------------------------

class RunLike:
    """What _Lookout needs of a run: the task, the lease holder, the deny list the reader last applied."""

    def __init__(self, path, task_id, holder, deny_list=()):
        self.path, self.id, self.holder, self.deny_list = str(path), task_id, holder, tuple(deny_list)

    def services_db_path(self):
        return self.path


def test_the_lookout_answers_from_what_is_committed_when_it_is_asked(tmp_path):
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    try:
        state.set_config(db, {"enabled": True}, T0)
        task = orchestrator.submit(db, QUESTION, T0, mode="fact_verification")
        holder = svc_mod.holder_id()          # this process, reading: cancel() requests and does not act itself
        with store.write(db):
            state.update_task(db, task["id"], T0, status="reading", stage="reading", lease_holder=holder,
                              lease_expires_at=T0 + 600)
        run = RunLike(path, task["id"], holder)
        lookout = orchestrator._Lookout(run)

        def ask(url="http://one.example/zephyr"):
            out = {}
            thread = threading.Thread(target=lambda: out.update(answer=lookout(url)))
            thread.start()                    # asked from another thread, as a fetch ahead asks
            thread.join(5)
            return out["answer"]

        assert ask() is None
        # a denial is read from the file as it is now, for the host asked about (and its subdomains), whatever
        # list the reader has applied so far
        state.set_config(db, {"deny_domains": ["two.example"]}, T0 + 1)
        assert ask() is None and ask("http://two.example/zephyr") == "the site was denied"
        assert ask("http://www.two.example/x") == "the site was denied" and ask("http://nottwo.example/") is None
        state.set_config(db, {"deny_domains": []}, T0 + 1)
        assert ask("http://two.example/zephyr") is None
        state.set_config(db, {"enabled": False}, T0 + 2)
        assert ask() == "web access was turned off"
        state.set_config(db, {"enabled": True}, T0 + 3)
        assert ask() is None
        orchestrator.cancel(db, task["id"], T0 + 4)       # the lease is live, so this requests the cancel
        assert state.task(db, task["id"])["cancel_requested"] and ask() == "the task was cancelled"
        with store.write(db):
            state.update_task(db, task["id"], T0 + 5, lease_holder="another worker")
        assert ask() == "the task was forgotten or taken over"
        with pytest.raises(Exception):        # read only: it can never change the individual
            lookout.db.execute("UPDATE web_tasks SET cancel_requested=0")
        lookout.close()
        assert ask() == "the reader moved on"
        lookout.close()                       # closing twice is harmless
    finally:
        db.close()


# ---- review round: a stop that lands while a connection is being opened (F1) -------------------------------------

def slow_connector(web, connecting, proceed):
    """A connection that takes a while to open (in the world: up to the fetch timeout)."""
    def connector(ip, port, timeout):
        connecting.set()
        proceed.wait(5)
        return web.connector(ip, port, timeout)
    return connector


def test_the_readers_request_is_not_sent_when_the_stop_lands_while_its_connection_opens(web, tmp_path):
    """The gate was asked before connecting only: a stop during the handshake let the request out afterwards. It is
    asked again on the open connection; the connection is closed with nothing written and no request is charged."""
    web.add("a.example", "/robots.txt", ROBOTS, content_type="text/plain")
    web.add("a.example", "/p", "<p>hi</p>")
    fetcher = fetcher_for(web, tmp_path)
    fetcher.robots_allowed("http://a.example/p")              # the site's robots.txt is known
    before = len(web.log)
    connecting, proceed, stop = threading.Event(), threading.Event(), threading.Event()
    fetcher._connector = slow_connector(web, connecting, proceed)
    usage, asked_after, out = Usage(), [], {}

    def gate(url):
        if stop.is_set():
            asked_after.append(url)
            raise Stop(url)

    def reader():
        try:
            with fetcher.gated(gate):
                out["result"] = fetcher.fetch("http://a.example/p", budget=Budget(), usage=usage)
        except Stop:
            out["stopped"] = True

    thread = threading.Thread(target=reader)
    thread.start()
    assert connecting.wait(5)
    stop.set()                                                # the owner stops now; the connection is still opening
    proceed.set()
    thread.join(5)
    assert out == {"stopped": True} and asked_after == ["http://a.example/p"]
    assert settled(web, quiet=0.2)[before:] == []
    assert usage.requests == 0 and fetcher.stats()["hosts"]["a.example"]["requests"] == 1     # robots.txt only


def test_a_fetch_aheads_request_is_not_sent_when_the_stop_lands_while_its_connection_opens(web, tmp_path):
    web.add("a.example", "/robots.txt", ROBOTS, content_type="text/plain")
    web.add("a.example", "/p", "<p>hi</p>")
    connecting, proceed, stop = threading.Event(), threading.Event(), threading.Event()
    fetcher, budget, usage = fetcher_for(web, tmp_path, connector=slow_connector(web, connecting, proceed)), \
        Budget(), Usage()
    fetcher.speculate("http://a.example/p", usage=usage, budget=budget,
                      wanted=lambda url: "cancelled" if stop.is_set() else None)
    spec = next(iter(fetcher._speculations.values()))
    assert connecting.wait(5)                                 # the connection for robots.txt is being opened
    stop.set()
    proceed.set()
    assert spec.finished.wait(5) and spec.stopped == "cancelled"
    assert settled(web, quiet=0.2) == []
    fetcher.abandon("http://a.example/p", usage=usage)
    assert (usage.prefetch_unused_requests, usage.requests) == (0, 0) and fetcher._ahead == {}


# ---- review round: one exchange per host for fetches ahead (F2) -------------------------------------------------

def arrived(web, host, path, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not web.requests_to(host, path):
        time.sleep(0.01)
    assert web.requests_to(host, path), [(r["host"], r["path"]) for r in web.log]


def test_a_fetch_ahead_opens_no_second_connection_to_the_site_the_reader_is_on(web, tmp_path):
    """A page fetched ahead on b.example redirects to a.example while the reader's request to a.example is still
    being answered. The read-ahead window counts candidate hosts, and cannot know a redirect's target: the fetch
    ahead used to open a second connection to a.example. It stops at the busy site, before the request, and at its
    turn the claim goes on from that hop: nothing it had fetched is fetched again."""
    release = threading.Event()
    web.add("a.example", "/robots.txt", ROBOTS, content_type="text/plain")
    web.routes[("a.example", "/slow")] = Held("<p>slow</p>", release, "text/html")
    web.add("a.example", "/other", "<p>other</p>")
    web.add("b.example", "/robots.txt", ROBOTS, content_type="text/plain")
    web.add_redirect("b.example", "/p", "http://a.example/other")
    fetcher, budget, usage, out = fetcher_for(web, tmp_path), Budget(), Usage(), {}
    reader = threading.Thread(target=lambda: out.update(
        slow=fetcher.fetch("http://a.example/slow", budget=budget, usage=usage)))
    reader.start()
    try:
        arrived(web, "a.example", "/slow")
        fetcher.speculate("http://b.example/p", usage=usage, budget=budget)
        spec = next(iter(fetcher._speculations.values()))
        assert spec.finished.wait(5) and spec.stopped == "host busy"
        assert web.requests_to("a.example", "/other") == [] and web.requests_to("b.example", "/p")
    finally:
        release.set()
    reader.join(5)
    assert out["slow"].body == b"<p>slow</p>"
    result = fetcher.fetch("http://b.example/p", budget=budget, usage=usage)        # at its turn: continued
    assert result.body == b"<p>other</p>" and result.redirects == ["http://a.example/other"]
    assert fetcher._busy == {} and fetcher._ahead == {}
    assert usage.prefetch_unused_requests == 0 and usage.requests == 5              # nothing wasted, nothing twice
    assert [(r["host"], r["path"]) for r in web.log if r["host"] == "b.example"] == \
        [("b.example", "/robots.txt"), ("b.example", "/p")]
    assert len(web.log) == usage.requests + usage.prefetch_unused_requests


def test_the_readers_fetch_cuts_the_fetch_ahead_that_is_talking_to_the_site_it_reaches(web, tmp_path):
    """The other direction: the reader's page redirects onto a site where a fetch ahead is waiting for its answer.
    The reader is not held back (as before); the fetch ahead is cut, so there is one connection to the site, and it
    is not used."""
    release = threading.Event()
    web.add("b.example", "/robots.txt", ROBOTS, content_type="text/plain")
    web.routes[("b.example", "/slow")] = Held("<p>slow</p>", release, "text/html")
    web.add("b.example", "/target", "<p>target</p>")
    web.add("a.example", "/robots.txt", ROBOTS, content_type="text/plain")
    web.add_redirect("a.example", "/p", "http://b.example/target")
    fetcher, budget, usage = fetcher_for(web, tmp_path), Budget(), Usage()
    try:
        fetcher.speculate("http://b.example/slow", usage=usage, budget=budget)
        spec = next(iter(fetcher._speculations.values()))
        arrived(web, "b.example", "/slow")
        result = fetcher.fetch("http://a.example/p", budget=budget, usage=usage)
        assert result.body == b"<p>target</p>"
        assert spec.finished.wait(2) and spec.stopped == "host busy" and spec.result is None
    finally:
        release.set()
    fetcher.abandon("http://b.example/slow", usage=usage)
    assert usage.prefetch_unused_requests == 2 and fetcher._busy == {} and fetcher._ahead == {}
    assert len(settled(web, quiet=0.2)) == usage.requests + usage.prefetch_unused_requests


# ---- review round: requests on the wire are cut on a stop, and a waiting claim asks too (F7) -------------------

def held_page(web, release):
    web.add("slow.example", "/robots.txt", ROBOTS, content_type="text/plain")
    web.routes[("slow.example", "/p")] = Held("<p>late</p>", release, "text/html")


def test_the_readers_request_on_the_wire_is_cut_when_its_watch_sees_the_stop(web, tmp_path):
    """Before, the reader's own request ran to its end (up to the fetch timeout) before a cancel took effect."""
    release, stop = threading.Event(), threading.Event()
    held_page(web, release)
    fetcher, usage, out = fetcher_for(web, tmp_path), Usage(), {}

    def gate(url):
        if stop.is_set():
            raise Stop(url)

    def reader():
        try:
            with fetcher.gated(gate, lambda url: "stopped" if stop.is_set() else None):
                fetcher.fetch("http://slow.example/p", budget=Budget(), usage=usage)
        except Stop:
            out["stopped"] = time.monotonic()

    thread = threading.Thread(target=reader)
    thread.start()
    try:
        arrived(web, "slow.example", "/p")
        stopped_at = time.monotonic()
        stop.set()
        thread.join(3)                                        # the answer is still held: only the cut ends it
        assert "stopped" in out and out["stopped"] - stopped_at < 2
    finally:
        release.set()
    assert usage.requests == 2 == len(web.log)                # both were sent, so both are charged
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and any(t.name == "nsf-fetch-watch" for t in threading.enumerate()):
        time.sleep(0.05)
    assert not any(t.name == "nsf-fetch-watch" for t in threading.enumerate())      # the watch ends with its request


def test_a_request_cut_for_a_stop_that_was_withdrawn_is_interrupted_and_says_nothing_about_the_site(web, tmp_path):
    """The watch saw a stop, the request was cut, and the gate does not confirm it (the owner undid it within the
    poll). Here that happens while robots.txt is on the wire. It used to end as 'connect_failed', which a robots.txt
    fetch turns into "disallow everything" for 24 hours, in a fetcher the worker keeps across tasks."""
    release, seen = threading.Event(), []
    web.routes[("slow.example", "/robots.txt")] = Held(ROBOTS, release)
    web.add("slow.example", "/p", "<p>late</p>")
    fetcher, usage = fetcher_for(web, tmp_path), Usage()
    try:
        with pytest.raises(FetchError) as info, \
                fetcher.gated(lambda url: None, lambda url: seen.append(url) or (len(seen) == 1 and "stop")):
            fetcher.fetch("http://slow.example/p", budget=Budget(), usage=usage)
    finally:
        release.set()
    assert info.value.code == "interrupted" and "cut" in info.value.detail and seen
    with fetcher._lock:
        assert fetcher._robots == {} and fetcher._backoff == {}           # no verdict on the site, no back-off
    assert fetcher.cache.robots("http~80~.slow.example") is None and fetcher.cache.lookup("http://slow.example/p") is None
    # the site is read as usual afterwards
    assert fetcher.fetch("http://slow.example/p", budget=Budget(), usage=usage).body == b"<p>late</p>"


def test_a_watch_that_cannot_be_read_does_not_cut_a_healthy_request(web, tmp_path):
    """Any error from the watch used to count as a stop and cut the reader's request. The reader's own gate fails
    closed before every step; a watch that fails is no answer, and the request goes on."""
    web.add("a.example", "/robots.txt", ROBOTS, content_type="text/plain", delay=0.4)
    web.add("a.example", "/p", "<p>hi</p>", delay=0.4)
    fetcher, calls = fetcher_for(web, tmp_path), []

    def watch(url):
        calls.append(url)
        raise OSError("database is locked")

    with fetcher.gated(lambda url: None, watch):
        result = fetcher.fetch("http://a.example/p", budget=Budget(), usage=Usage())
    assert result.body == b"<p>hi</p>" and calls                # asked, failed, and nothing was cut
    with fetcher._lock:
        assert not fetcher._robots["http~80~.a.example"].rules.disallow_all


def test_a_stop_during_the_readers_tls_handshake_charges_no_request(web, tmp_path):
    """The watch asks while the connection is being opened too. A stop during a slow TLS handshake cut it, nothing
    was sent, and the request charged before connecting stayed charged."""
    import socket as socket_module
    listener = socket_module.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    held, stop, out = [], threading.Event(), {}

    def serve():                                              # accepts, and never answers the handshake
        try:
            while True:
                held.append(listener.accept()[0])
        except OSError:
            pass

    threading.Thread(target=serve, daemon=True).start()
    web._ip_for("tls.example")
    port = listener.getsockname()[1]
    fetcher = fetcher_for(web, tmp_path, connector=lambda ip, _port, timeout: socket_module.create_connection(
        ("127.0.0.1", port), timeout=timeout))
    usage = Usage()

    def gate(url):
        if stop.is_set():
            raise Stop(url)

    def reader():
        try:
            with fetcher.gated(gate, lambda url: "stopped" if stop.is_set() else None):
                fetcher.fetch("https://tls.example/data", purpose="api", budget=Budget(), usage=usage)
        except Stop:
            out["stopped"] = True
        except Exception as error:
            out["error"] = error

    thread = threading.Thread(target=reader)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not held:      # connected: the handshake is under way
            time.sleep(0.01)
        assert held
        stop.set()
        thread.join(5)
        assert out == {"stopped": True}
        assert usage.requests == 0                            # nothing was written, so nothing is charged
        assert fetcher.stats()["hosts"]["tls.example"]["requests"] == 0
    finally:
        listener.close()
        for connection in held:
            connection.close()


def test_a_watch_is_not_asked_for_fetches_outside_the_gate_and_a_plain_fetch_is_unchanged(web, tmp_path):
    web.add("a.example", "/p", "<p>hi</p>", delay=0.6)
    fetcher, seen = fetcher_for(web, tmp_path), []
    with fetcher.gated(lambda url: None, lambda url: seen.append(url)):
        assert fetcher.fetch("http://a.example/p", budget=Budget(), usage=Usage()).body == b"<p>hi</p>"
    asked = len(seen)
    assert asked >= 1                                         # asked while the page was on its way (0.6 s)
    assert fetcher.fetch("http://a.example/p", budget=Budget(), usage=Usage(), conditional=False).body == b"<p>hi</p>"
    assert len(seen) == asked


def test_a_claim_waiting_for_its_fetch_ahead_asks_the_gate_and_cuts_the_fetch_on_a_stop(web, tmp_path):
    release, stop = threading.Event(), threading.Event()
    held_page(web, release)
    fetcher, usage, out = fetcher_for(web, tmp_path), Usage(), {}
    fetcher.speculate("http://slow.example/p", usage=usage)   # no gate of its own: only the claim can stop it
    spec = next(iter(fetcher._speculations.values()))

    def gate(url):
        if stop.is_set():
            raise Stop(url)

    def reader():
        try:
            with fetcher.gated(gate):
                fetcher.fetch("http://slow.example/p", budget=Budget(), usage=usage)
        except Stop:
            out["stopped"] = time.monotonic()

    try:
        arrived(web, "slow.example", "/p")
        thread = threading.Thread(target=reader)
        thread.start()
        time.sleep(0.3)                                       # the claim is waiting for the held answer
        stopped_at = time.monotonic()
        stop.set()
        thread.join(3)
        assert "stopped" in out and out["stopped"] - stopped_at < 2
        assert spec.finished.wait(2) and spec.abandoned
    finally:
        release.set()
    assert (usage.requests, usage.prefetch_unused_requests) == (0, 2) and len(settled(web, quiet=0.2)) == 2


@pytest.mark.parametrize("window", [1, 4])
def test_a_cancel_ends_the_run_while_its_requests_are_still_unanswered(tmp_path, window):
    """Every fetch waits for an answer that does not come. Before, the run ended when the reader's own request did
    (up to the fetch timeout, 15 s by default); now the requests on the wire are cut and it ends at once."""
    held = {}
    at_stop, final, done = run_until_held(tmp_path, window, window, cancel, held)
    assert held["ended"] and held["seconds"] < 3, held
    assert done["status"] == "cancelled" and len(final) == len(at_stop) == window
    assert done["usage"]["requests"] + done["usage"].get("prefetch_unused_requests", 0) == window


# ---- review round: the crawler's own robots.txt and sitemap reads are gated (F4) --------------------------------

def sitemap(urls):
    return ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(f"<url><loc>{u}</loc></url>" for u in urls) + "</urlset>")


def crawl_task(tmp_path, web, interrupt):
    """A crawl of docs.example whose robots.txt lists four sitemaps; the first one's answer is held. `interrupt` is
    applied on another connection once that request has arrived. Returns (paths by the stop, paths after it, task)."""
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    state.set_config(db, {"enabled": True, "min_domain_interval": 0.0}, T0)
    task = orchestrator.submit(db, "Investigate these sources: http://docs.example/", T0, mode="deep_research",
                               context={"crawl": {"seeds": ["http://docs.example/"], "max_pages": 4, "max_depth": 1}})
    holder, out = svc_mod.holder_id(), {}
    with store.write(db):
        state.update_task(db, task["id"], T0, lease_holder=holder, lease_expires_at=T0 + 600)
    db.close()

    def run():
        connection = store.open_existing(path)
        services = svc_mod.build(connection, path, fixture=web, providers=[], clock=lambda: T0)
        services.holder = holder
        try:
            out["done"] = orchestrator.run(connection, task["id"], services)
        except BaseException as error:
            out["error"] = error
        finally:
            services.close()
            connection.close()

    worker = threading.Thread(target=run)
    worker.start()
    arrived(web, "docs.example", "/s0.xml")
    other = store.connect(path)
    try:
        interrupt(other, task["id"], web)
    finally:
        other.close()
    at_stop = len(web.log)
    return worker, out, at_stop


@pytest.mark.parametrize("interrupt", [cancel, web_off], ids=["cancel", "web_off"])
def test_a_crawl_reads_no_further_sitemap_after_a_stop(tmp_path, interrupt):
    """The crawler fetches robots.txt and the sitemaps itself, not through the run's page fetch, so they were not
    gated: after a cancel the three remaining sitemaps were still requested."""
    release = threading.Event()
    web = FixtureWeb()
    robots = ROBOTS + "".join(f"Sitemap: http://docs.example/s{i}.xml\n" for i in range(4))
    web.add("docs.example", "/robots.txt", robots, content_type="text/plain")
    web.routes[("docs.example", "/s0.xml")] = Held(sitemap(["http://docs.example/a"]), release, "application/xml")
    for i in range(1, 4):
        web.add("docs.example", f"/s{i}.xml", sitemap([f"http://docs.example/x{i}"]), content_type="application/xml")
    web.add("docs.example", "/", page("Zephyr compiler 4.2.0 boards", ["The Zephyr compiler supports 12 boards."]))
    web.add("docs.example", "/a", page("Zephyr compiler 4.2.0 boards, more", ["It supports 14 boards."]))
    web.start()
    try:
        try:
            worker, out, at_stop = crawl_task(tmp_path, web, interrupt)
        finally:
            release.set()
        worker.join(30)
        assert not worker.is_alive() and "error" not in out, out.get("error")
        log = settled(web)
        assert [r["path"] for r in log[:at_stop]] == ["/robots.txt", "/s0.xml"]
        assert [r["path"] for r in log[at_stop:]] == []
        assert out["done"]["status"] in ("cancelled", "partial", "failed")
    finally:
        web.stop()


# ---- review round: a new deny list is never visible beside the old policy (F6) -----------------------------------

def test_the_fetchers_policy_is_replaced_before_the_run_records_the_new_deny_list(tmp_path):
    """A fetch ahead that asked between the two used to see the new list recorded and its own (old) policy still in
    force, and went on. The policy goes first; and the lookout no longer consults the reader's copy at all."""
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    try:
        state.set_config(db, {"enabled": True}, T0)
        seen = []

        class Fetcher:
            def __init__(self):
                self._policy = NetPolicy()

            policy = property(lambda self: self._policy)

            @policy.setter
            def policy(self, value):
                seen.append((tuple(run.deny_list), tuple(value.deny_domains)))
                self._policy = value

        class Run:
            recheck_config = orchestrator._Run.recheck_config
            flush_deferred = orchestrator._Run.flush_deferred

            def event(self, *args):
                seen.append(("event", args[1]))

        run = Run()
        run.db, run.deny_list, run.config_read, run.deferred = db, (), 0.0, []
        run.services = type("S", (), {"fetcher": Fetcher()})()
        state.set_config(db, {"deny_domains": ["x.example"]}, T0 + 1)
        run.recheck_config(force=True, defer=True)
        assert seen == [((), ("x.example",))]                 # the old list was still the recorded one
        assert run.deny_list == ("x.example",) and run.deferred     # the event waits until after the fetch
        run.flush_deferred()
        assert seen[-1] == ("event", {"deny_domains_updated": 1}) and run.deferred == []
    finally:
        db.close()


# ---- review round: politeness wins over identical outcomes (F3) --------------------------------------------------

def zephyr(host, n):
    return page(f"Zephyr compiler 4.2.0 boards ({host})", [
        f"The Zephyr compiler version 4.2.0 supports {10 + n} boards according to {host}.",
        f"Source {n} lists the nRF52 and STM32 boards for version 4.2.0 of the Zephyr compiler."])


@pytest.mark.parametrize("window", [1, 4])
def test_a_back_off_received_by_a_fetch_ahead_applies_to_the_site_at_once(tmp_path, window):
    """The one stated exception to "read-ahead reads what one page at a time reads". The first candidate redirects,
    slowly, to two.example; the second candidate is on two.example and is answered 429 with Retry-After. One page at
    a time, the first is read before the 429 exists. Read ahead, the 429 arrives first, the site is left alone from
    that moment, and the first candidate's hop onto it is not made. The site asked: it is not asked again, and the
    report says why the source is missing. Only that is missing: the first candidate's own site did not ask for
    anything, and a later page on it is read (it used to be skipped, and blamed on a crawl delay it does not
    have)."""
    web = FixtureWeb()
    for host in ("one.example", "two.example", "three.example"):
        web.add(host, "/robots.txt", ROBOTS, content_type="text/plain")
    web.add("one.example", "/zephyr", b"", status=302, content_type=None,
            headers={"Location": "http://two.example/article"}, delay=0.5)
    web.add("one.example", "/second", zephyr("one.example", 4))        # another page on the first site
    web.add("two.example", "/article", zephyr("two.example", 1))
    web.add("two.example", "/zephyr", "slow down", status=429, content_type="text/plain",
            headers={"Retry-After": "60"})
    web.add("three.example", "/zephyr", zephyr("three.example", 3))
    web.start()
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    try:
        state.set_config(db, {"enabled": True, "min_domain_interval": 0.0, "read_ahead": window}, T0)
        task = orchestrator.submit(db, QUESTION, T0, mode="fact_verification")
        provider = CorpusProvider([{"url": url, "title": f"Zephyr compiler 4.2.0 boards ({n})",
                                    "snippet": "the Zephyr compiler version 4.2.0 and the boards it supports"}
                                   for n, url in enumerate(("http://one.example/zephyr", "http://two.example/zephyr",
                                                            "http://three.example/zephyr",
                                                            "http://one.example/second"))])
        services = svc_mod.build(db, path, fixture=web, providers=[provider], clock=time.time)
        try:
            done = orchestrator.run(db, task["id"], services)
        finally:
            services.close()
        sources = sorted(s["url"] for s in state.sources(db, done["id"]))
        limitations = (done["result"] or {}).get("limitations") or []
        asked = [text for text in limitations if "two.example" in text and "asked for fewer requests" in text]
        assert done["status"] in ("completed", "partial"), done["error"]
        assert asked, limitations                              # at both windows the 429 itself is reported
        # the first site never asked for anything: nothing blames it, and its other page is read at both windows
        assert not [text for text in limitations if "one.example" in text or "crawl delay" in text], limitations
        to_two = [r["path"] for r in settled(web) if r["host"] == "two.example"]
        if window == 1:
            assert sources == ["http://one.example/second", "http://one.example/zephyr",
                               "http://three.example/zephyr"]
            assert to_two == ["/robots.txt", "/article", "/zephyr"]
        else:   # missing: exactly the page that leads onto the site that asked for fewer requests
            assert sources == ["http://one.example/second", "http://three.example/zephyr"]
            assert to_two == ["/robots.txt", "/zephyr"]        # nothing after the site asked to slow down
    finally:
        db.close()
        web.stop()


# ---- second pass: every page read still shows as its own progress event -----------------------------------------

def test_each_source_read_is_its_own_progress_event_at_the_default_read_ahead(tmp_path):
    """Progress views (the console's and the phone's stage lists, Presence) follow a task through its recorded
    events. With pages fetched ahead the reader takes several pages in quick succession; each must still be
    recorded as its own 'fetch' event, in reading order, during the reading stage."""
    release = threading.Event()
    release.set()
    web = held_web(release)
    path = tmp_path / "individual.sqlite"
    db = store.create(path, T0)
    try:
        state.set_config(db, {"enabled": True, "min_domain_interval": 0.0}, T0)       # read_ahead: the default
        assert state.get_config(db).read_ahead == 4
        task = orchestrator.submit(db, QUESTION, T0, mode="fact_verification")
        services = svc_mod.build(db, path, fixture=web, providers=[corpus()], clock=lambda: T0)
        inner, ahead = services.fetcher.speculate, []
        services.fetcher.speculate = lambda url, **kwargs: inner(url, **kwargs) and not ahead.append(url)
        try:
            done = orchestrator.run(db, task["id"], services)
        finally:
            services.close()
        assert done["status"] in ("completed", "partial"), done["error"]
        assert len(ahead) >= 2                                  # pages really were fetched ahead
        events = state.events(db, task["id"])
        fetched = [e for e in events if e["kind"] == "fetch"]
        read = [d["url"] for d in state.task(db, task["id"])["checkpoint"]["documents"]]
        assert [e["detail"]["url"] for e in fetched] == read and len(read) == done["usage"]["pages"] >= 4
        assert all(e["stage"] == "reading" for e in fetched)
        assert len({e["detail"]["source_id"] for e in fetched}) == len(fetched)
        order = [e["kind"] for e in events]
        assert order.index("fetch") > [i for i, e in enumerate(events) if e["kind"] == "stage"
                                       and e["detail"].get("stage") == "reading"][0]
    finally:
        db.close()
        web.stop()
