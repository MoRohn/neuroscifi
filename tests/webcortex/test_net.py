"""SafeFetcher: SSRF policy, DNS pinning, redirects, limits, robots, backoff, conditional GET, budget, coalescing.

All tests run against FixtureWeb (no real network) except the one `live` smoke test (NSF_LIVE=1).
"""
import gzip
import threading

import pytest

from neuroscifi.webcortex import net
from neuroscifi.webcortex.cache import PublicCache
from neuroscifi.webcortex.fixtureweb import SPECIAL
from neuroscifi.webcortex.net import NetPolicy, SafeFetcher, check_address, check_url
from neuroscifi.webcortex.types import Budget, BudgetExceeded, FetchError, Usage


class FakeClock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


class Counting:
    """Wraps a resolver/connector and counts calls per first argument."""

    def __init__(self, fn):
        self.fn, self.calls = fn, []

    def __call__(self, *args):
        self.calls.append(args)
        return self.fn(*args)


def make(web, tmp_path, *, cache=True, **policy):
    clock = FakeClock()
    resolver, connector = Counting(web.resolver), Counting(web.connector)
    fetcher = SafeFetcher(NetPolicy(**policy), resolver=resolver, connector=connector,
                          cache=PublicCache(tmp_path / "c.sqlite") if cache else None, clock=clock, sleep=clock.sleep)
    return fetcher, clock, resolver, connector


def code_of(fn):
    with pytest.raises(FetchError) as info:
        fn()
    return info.value


# ---- address and URL policy ------------------------------------------------------------------------------------

@pytest.mark.parametrize("ip", [
    "127.0.0.1", "127.8.9.10", "10.1.2.3", "172.16.0.1", "192.168.0.1", "169.254.169.254", "100.64.0.1",
    "0.0.0.0", "224.0.0.1", "240.0.0.1", "255.255.255.255", "192.0.2.1", "198.51.100.7", "203.0.113.9", "198.18.0.1",
    "::1", "::", "fe80::1", "fd00::1", "fc00::1", "fec0::1", "ff02::1", "2001:db8::1", "::ffff:127.0.0.1",
    "::ffff:10.0.0.1", "::127.0.0.1", "2002:7f00:1::1", "2002:a9fe:a9fe::1", "2001:0:4136:e378:8000:63bf:80ff:fffe",
    "64:ff9b::7f00:1", "64:ff9b::a9fe:a9fe", "fd00:ec2::254", "not-an-ip"])
def test_forbidden_addresses(ip):
    assert check_address(ip) is not None


@pytest.mark.parametrize("ip", ["45.79.0.2", "8.8.8.8", "93.184.215.14", "2606:4700::1111", "::ffff:8.8.8.8",
                                "64:ff9b::808:808"])
def test_public_addresses(ip):
    assert check_address(ip) is None


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://[::1]/", "http://169.254.169.254/latest/meta-data/", "http://0x7f000001/",
    "http://2130706433/", "http://127.1/", "http://0177.0.0.1/", "http://[::ffff:127.0.0.1]/", "http://10.0.0.1/",
    "file:///etc/passwd", "gopher://news.example/", "ftp://news.example/", "http://news.example:22/",
    "http://user:pass@news.example/", "http://news.example@127.0.0.1/", "http://localhost/", "http://a.localhost/",
    "http://bad host/", "http://xn--/", "javascript:alert(1)", "http://" + "a" * 2050 + ".example/",
    "http://exa­​mple .com/"])
def test_check_url_refuses(url):
    assert code_of(lambda: check_url(url, NetPolicy())).code == "blocked_policy"


def test_check_url_normalises_and_denies_domains():
    parts = check_url("HTTP://Bücher.Example:8080/p?q=1#frag", NetPolicy())
    assert parts.scheme == "http" and parts.hostname == "xn--bcher-kva.example" and parts.port == 8080
    assert parts.fragment == "" and parts.query == "q=1"
    policy = NetPolicy(deny_domains=("tracker.example",))
    for url in ("http://tracker.example/", "http://cdn.tracker.example/x"):
        assert code_of(lambda: check_url(url, policy)).code == "blocked_policy"
    check_url("http://nottracker.example/", policy)


@pytest.mark.parametrize("host", sorted(SPECIAL))
def test_special_hosts_refused_without_connecting(web, tmp_path, host):
    fetcher, _clock, _resolver, connector = make(web, tmp_path)
    for purpose in ("page", "api"):
        error = code_of(lambda: fetcher.fetch(f"http://{host}/", purpose=purpose))
        assert error.code == "blocked_policy"
    assert connector.calls == []
    assert web.log == []


def test_literal_ip_urls_never_connect(web, tmp_path):
    fetcher, _clock, resolver, connector = make(web, tmp_path)
    for url in ("http://127.0.0.1/", "http://[::1]/", "http://169.254.169.254/latest/meta-data/",
                "http://0x7f000001/", "http://2130706433/"):
        assert code_of(lambda: fetcher.fetch(url)).code == "blocked_policy"
    assert connector.calls == [] and resolver.calls == []


def test_redirects_to_internal_targets_refused(web, tmp_path):
    web.add_redirect("pub.example", "/to-internal", "http://internal.test/admin")
    web.add_redirect("pub.example", "/to-loopback", "http://127.0.0.1/")
    web.add_redirect("pub.example", "/to-metadata", "http://169.254.169.254/latest/meta-data/", status=307)
    web.add_redirect("pub.example", "/to-file", "file:///etc/passwd")
    fetcher, _clock, _resolver, connector = make(web, tmp_path)
    for path in ("/to-internal", "/to-loopback", "/to-metadata", "/to-file"):
        assert code_of(lambda: fetcher.fetch(f"http://pub.example{path}", purpose="api")).code == "blocked_policy"
    assert web.requests_to("internal.test") == [] and web.requests_to("127.0.0.1") == []
    assert {c[0] for c in connector.calls} == {web.hosts["pub.example"]}


def test_dns_rebinding_pins_the_validated_address(web, tmp_path):
    web.add("rebind.test", "/", "<p>public answer</p>")
    public = web.hosts["rebind.test"]
    web.add_rebinding("rebind.test", [public, "127.0.0.1"])
    fetcher, _clock, resolver, connector = make(web, tmp_path)
    result = fetcher.fetch("http://rebind.test/")          # robots.txt + page share one resolution
    assert result.remote_ip == public and b"public answer" in result.body
    assert [c[0] for c in resolver.calls] == ["rebind.test"]
    assert all(c[0] == public for c in connector.calls) and len(connector.calls) == 2
    # the next lookup returns the rebound loopback answer: refused before any connection
    assert code_of(lambda: fetcher.fetch("http://rebind.test/", conditional=False)).code == "blocked_policy"
    assert len(connector.calls) == 2


def test_mixed_answers_refused(web, tmp_path):
    web.add("mixed.example", "/", "x")
    fetcher, *_ = make(web, tmp_path)
    fetcher._resolver = lambda host, port: [web.hosts["mixed.example"], "10.0.0.7"]
    assert code_of(lambda: fetcher.fetch("http://mixed.example/")).code == "blocked_policy"


def test_unknown_host_is_dns_failure(web, tmp_path):
    fetcher, *_ = make(web, tmp_path)
    assert code_of(lambda: fetcher.fetch("http://nowhere.example/")).code == "dns_failure"


def test_redirect_loop_and_limit(web, tmp_path):
    web.add_redirect("loop.example", "/a", "/b")
    web.add_redirect("loop.example", "/b", "/a")
    fetcher, *_ = make(web, tmp_path, max_redirects=3)
    assert code_of(lambda: fetcher.fetch("http://loop.example/a", purpose="api")).code == "too_many_redirects"
    assert len(web.requests_to("loop.example")) == 4


def test_redirect_followed_and_303_becomes_get(web, tmp_path):
    web.add_redirect("r.example", "/old", "http://r2.example/new", status=301)
    web.add("r2.example", "/new", "<p>new</p>")
    web.add_redirect("r.example", "/post", "/done", status=303)
    web.add("r.example", "/done", '{"ok": true}', content_type="application/json")
    fetcher, *_ = make(web, tmp_path)
    result = fetcher.fetch("http://r.example/old")
    assert result.final_url == "http://r2.example/new" and result.redirects == ["http://r2.example/new"]
    result = fetcher.fetch("http://r.example/post", method="POST", body=b"q=1", purpose="api",
                           headers={"Content-Type": "application/x-www-form-urlencoded", "Authorization": "x"})
    assert result.body == b'{"ok": true}'
    assert [r["method"] for r in web.requests_to("r.example")][-2:] == ["POST", "GET"]


def test_https_to_http_downgrade_refused(web, tmp_path, monkeypatch):
    """The fixture speaks plain HTTP only, so the TLS hop is simulated at the single-exchange seam."""
    fetcher, *_ = make(web, tmp_path)
    web.add("secure.example", "/", "x")
    real = SafeFetcher._exchange

    def fake_exchange(self, parts, *args, **kwargs):
        if parts.scheme == "https":
            return net._Response(302, {"location": "http://secure.example/plain"})
        return real(self, parts, *args, **kwargs)

    monkeypatch.setattr(SafeFetcher, "_exchange", fake_exchange)
    error = code_of(lambda: fetcher.fetch("https://secure.example/", purpose="api"))
    assert error.code == "blocked_policy" and "downgrade" in error.detail
    assert web.requests_to("secure.example") == []


def test_method_and_purpose_restrictions(web, tmp_path):
    fetcher, *_ = make(web, tmp_path)
    assert code_of(lambda: fetcher.fetch("http://a.example/", method="DELETE")).code == "blocked_policy"
    assert code_of(lambda: fetcher.fetch("http://a.example/", method="POST")).code == "blocked_policy"
    assert code_of(lambda: fetcher.fetch("http://a.example/", purpose="exfiltrate")).code == "blocked_policy"


# ---- limits ----------------------------------------------------------------------------------------------------

def test_size_limit_truncates_pages_and_rejects_api(web, tmp_path):
    web.add("big.example", "/page", b"<p>" + b"a" * 5000 + b"</p>")
    web.add("big.example", "/api", b"[" + b"1," * 5000 + b"1]", content_type="application/json")
    fetcher, *_ = make(web, tmp_path, max_bytes=1000, max_api_bytes=1000)
    result = fetcher.fetch("http://big.example/page")
    assert result.truncated and len(result.body) == 1000
    assert code_of(lambda: fetcher.fetch("http://big.example/api", purpose="api")).code == "too_large"


def test_gzip_bomb_is_capped(web, tmp_path):
    bomb = gzip.compress(b"\0" * 20_000_000, compresslevel=9)
    assert len(bomb) < 100_000
    web.add("bomb.example", "/p", bomb, headers={"Content-Encoding": "gzip"})
    web.add("bomb.example", "/a", bomb, content_type="application/json", headers={"Content-Encoding": "gzip"})
    fetcher, *_ = make(web, tmp_path, max_bytes=100_000)
    result = fetcher.fetch("http://bomb.example/p")
    assert result.truncated and len(result.body) == 100_000
    assert code_of(lambda: fetcher.fetch("http://bomb.example/a", purpose="api")).code == "too_large"


def test_gzip_and_deflate_decoded(web, tmp_path):
    import zlib
    web.add("enc.example", "/g", gzip.compress(b"<p>gz</p>"), headers={"Content-Encoding": "gzip"})
    web.add("enc.example", "/d", zlib.compress(b"<p>zlib</p>"), headers={"Content-Encoding": "deflate"})
    raw = zlib.compressobj(wbits=-15)
    web.add("enc.example", "/r", raw.compress(b"<p>raw</p>") + raw.flush(), headers={"Content-Encoding": "deflate"})
    web.add("enc.example", "/x", b"??", headers={"Content-Encoding": "br"})
    fetcher, *_ = make(web, tmp_path)
    assert fetcher.fetch("http://enc.example/g").body == b"<p>gz</p>"
    assert fetcher.fetch("http://enc.example/d").body == b"<p>zlib</p>"
    assert fetcher.fetch("http://enc.example/r").body == b"<p>raw</p>"
    assert code_of(lambda: fetcher.fetch("http://enc.example/x")).code == "decode_error"
    assert web.requests_to("enc.example", "/g")[0]["headers"]["accept-encoding"] == "gzip, deflate"


@pytest.mark.parametrize("content_type", ["application/octet-stream", "image/png", "application/x-msdownload", None])
def test_disallowed_content_type(web, tmp_path, content_type):
    web.add("type.example", "/f", b"\x89PNG....", content_type=content_type)
    fetcher, *_ = make(web, tmp_path)
    assert code_of(lambda: fetcher.fetch("http://type.example/f")).code == "unsupported_type"


def test_structured_json_types_allowed(web, tmp_path):
    web.add("type.example", "/j", b"{}", content_type="application/vnd.api+json")
    fetcher, *_ = make(web, tmp_path)
    assert fetcher.fetch("http://type.example/j", purpose="api").content_type == "application/vnd.api+json"


def test_slow_route_times_out(web, tmp_path):
    web.add("slow.example", "/", "late", delay=1.5)
    fetcher, *_ = make(web, tmp_path, timeout=0.4)
    assert code_of(lambda: fetcher.fetch("http://slow.example/", purpose="api")).code == "timeout"


def test_http_errors_carry_status(web, tmp_path):
    web.add("err.example", "/gone", "gone", status=410)
    fetcher, *_ = make(web, tmp_path)
    error = code_of(lambda: fetcher.fetch("http://err.example/gone"))
    assert error.code == "http_error" and error.status == 410
    assert fetcher.stats()["hosts"]["err.example"]["errors"] == 1


# ---- robots.txt ------------------------------------------------------------------------------------------------

def test_robots_disallow_blocks_without_requesting(web, tmp_path):
    web.add("polite.example", "/robots.txt", "User-agent: *\nDisallow: /private\n", content_type="text/plain")
    web.add("polite.example", "/private/x", "secret")
    web.add("polite.example", "/public", "ok")
    fetcher, *_ = make(web, tmp_path)
    assert code_of(lambda: fetcher.fetch("http://polite.example/private/x")).code == "blocked_robots"
    assert web.requests_to("polite.example", "/private/x") == []
    assert fetcher.fetch("http://polite.example/public").body == b"ok"
    assert fetcher.robots_allowed("http://polite.example/public") is True
    assert fetcher.robots_allowed("http://polite.example/private/y") is False
    assert len(web.requests_to("polite.example", "/robots.txt")) == 1


def test_robots_specific_agent_rules(web, tmp_path):
    web.add("ua.example", "/robots.txt", "User-agent: NeuroSciFi-WebCortex\nDisallow: /\n\nUser-agent: *\nAllow: /\n",
            content_type="text/plain")
    fetcher, *_ = make(web, tmp_path)
    assert code_of(lambda: fetcher.fetch("http://ua.example/x")).code == "blocked_robots"


def test_robots_404_allows_and_500_disallows(web, tmp_path):
    web.add("open.example", "/p", "fine")
    web.add("broken.example", "/robots.txt", "oops", status=500, content_type="text/plain")
    web.add("broken.example", "/p", "never")
    fetcher, *_ = make(web, tmp_path)
    assert fetcher.fetch("http://open.example/p").body == b"fine"
    assert code_of(lambda: fetcher.fetch("http://broken.example/p")).code == "blocked_robots"
    assert code_of(lambda: fetcher.fetch("http://broken.example/p")).code == "blocked_robots"
    assert web.requests_to("broken.example", "/p") == []
    assert len(web.requests_to("broken.example", "/robots.txt")) == 1      # remembered for this process


def test_api_purpose_skips_robots(web, tmp_path):
    web.add("api.example", "/robots.txt", "User-agent: *\nDisallow: /\n", content_type="text/plain")
    web.add("api.example", "/v1", '{"a": 1}', content_type="application/json")
    fetcher, *_ = make(web, tmp_path)
    assert fetcher.fetch("http://api.example/v1", purpose="api").body == b'{"a": 1}'
    assert web.requests_to("api.example", "/robots.txt") == []
    assert code_of(lambda: fetcher.fetch("http://api.example/v1", purpose="feed")).code == "blocked_robots"


def test_robots_cached_in_memory_and_public_cache(web, tmp_path):
    web.add("c.example", "/robots.txt", "User-agent: *\nAllow: /\n", content_type="text/plain")
    web.add("c.example", "/1", "one")
    web.add("c.example", "/2", "two")
    fetcher, clock, *_ = make(web, tmp_path)
    fetcher.fetch("http://c.example/1")
    fetcher.fetch("http://c.example/2")
    assert len(web.requests_to("c.example", "/robots.txt")) == 1
    # a new fetcher on the same cache file reuses the stored robots.txt (24 h)
    other = SafeFetcher(NetPolicy(), resolver=web.resolver, connector=web.connector, cache=fetcher.cache,
                        clock=clock, sleep=clock.sleep)
    other.fetch("http://c.example/1", conditional=False)
    assert len(web.requests_to("c.example", "/robots.txt")) == 1
    clock.t += 25 * 3600
    other.fetch("http://c.example/2", conditional=False)
    assert len(web.requests_to("c.example", "/robots.txt")) == 2


def test_crawl_delay_and_domain_interval(web, tmp_path):
    web.add("slowbot.example", "/robots.txt", "User-agent: *\nCrawl-delay: 7\n", content_type="text/plain")
    for path in ("/1", "/2", "/3"):
        web.add("slowbot.example", path, "x")
    web.add("fast.example", "/1", "x")
    web.add("fast.example", "/2", "x")
    fetcher, clock, *_ = make(web, tmp_path, min_domain_interval=2.0)
    fetcher.fetch("http://slowbot.example/1")
    fetcher.fetch("http://slowbot.example/2")
    fetcher.fetch("http://slowbot.example/3")
    assert clock.sleeps == [7.0, 7.0, 7.0]
    clock.sleeps.clear()
    fetcher.fetch("http://fast.example/1")
    fetcher.fetch("http://fast.example/2")
    assert clock.sleeps == [2.0, 2.0]          # robots -> /1 -> /2


# ---- robots.txt matcher (RFC 9309) -------------------------------------------------------------------------------

RFC_SIMPLE = """User-Agent: *
Disallow: *.gif$
Disallow: /example/
Allow: /publications/

User-Agent: foobot
Disallow:/
Allow:/example/page.html
Allow:/example/allowed.gif

User-Agent: barbot
User-Agent: bazbot
Disallow: /example/page.html

User-Agent: quxbot

EOF
"""


@pytest.mark.parametrize("agent,path,allowed", [
    ("foobot", "/example/page.html", True), ("foobot", "/example/allowed.gif", True),
    ("foobot", "/example/disallowed.gif", False), ("foobot", "/publications/", False), ("foobot", "/", False),
    ("barbot", "/example/page.html", False), ("barbot", "/example/disallowed.gif", True),
    ("bazbot", "/example/page.html", False), ("bazbot", "/publications/x", True),
    ("quxbot", "/example/page.html", True), ("quxbot", "/x.gif", True),
    ("otherbot", "/x.gif", False), ("otherbot", "/a/b/x.gif", False), ("otherbot", "/x.gif?size=2", True),
    ("otherbot", "/example/x", False), ("otherbot", "/publications/x", True),
    ("otherbot", "/publications/a.gif", True),      # Allow /publications/ is longer than *.gif$
    ("otherbot", "/other", True)])
def test_robots_rfc9309_simple_example(agent, path, allowed):
    """RFC 9309 §5.1, evaluated for each product token."""
    rules = net.RobotsRules.parse(RFC_SIMPLE, f"{agent}/1.0")
    assert rules.allowed("http://www.example.com" + path) is allowed


def test_robots_rfc9309_longest_match_and_ties():
    rules = net.RobotsRules.parse("User-Agent: foobot\nAllow: /example/page/\n"
                                  "Disallow: /example/page/disallowed.gif\n", "foobot")       # RFC 9309 §5.2
    assert rules.allowed("http://e.example/example/page/disallowed.gif") is False
    assert rules.allowed("http://e.example/example/page/other.gif") is True
    for text in ("User-agent: *\nAllow: /frob\nDisallow: /frob\n", "User-agent: *\nDisallow: /frob\nAllow: /frob\n"):
        assert net.RobotsRules.parse(text).allowed("http://e.example/frob/x") is True      # allow wins a tie


def test_robots_rfc9309_groups_combine_and_select():
    rules = net.RobotsRules.parse("user-agent: ExampleBot\ndisallow: /foo\ndisallow: /bar\n\n"
                                  "user-agent: ExampleBot\ndisallow: /baz\n", "ExampleBot/2")  # RFC 9309 §2.2.1
    assert [rules.allowed(f"http://e.example/{p}") for p in ("foo", "bar", "baz", "qux")] == [False] * 3 + [True]
    # no matching group and no `*` group: no rule applies
    assert net.RobotsRules.parse("User-agent: otherbot\nDisallow: /\n").allowed("http://e.example/x") is True
    # our product token, case-insensitively; a whole `-` prefix of it also matches; the most specific token wins
    ours = "User-agent: neuroscifi-webcortex/0.6\nDisallow: /\n"
    assert net.RobotsRules.parse(ours).allowed("http://e.example/x") is False
    assert net.RobotsRules.parse("User-agent: NeuroSciFi\nDisallow: /\n").allowed("http://e.example/x") is False
    assert net.RobotsRules.parse("User-agent: Neuro\nDisallow: /\n").allowed("http://e.example/x") is True
    specific = ("User-agent: *\nDisallow: /\n\nUser-agent: NeuroSciFi\nDisallow: /\n\n"
                "User-agent: NeuroSciFi-WebCortex\nAllow: /\n")
    assert net.RobotsRules.parse(specific).allowed("http://e.example/x") is True


@pytest.mark.parametrize("rule,path,matches", [
    # RFC 9309 §2.2.2 percent-encoding table
    ("/foo/bar?baz=quz", "/foo/bar?baz=quz", True),
    ("/foo/bar?baz=https://foo.bar", "/foo/bar?baz=https://foo.bar", True),
    ("/foo/bar/ツ", "/foo/bar/%E3%83%84", True),
    ("/foo/bar/ツ", "/foo/bar/ツ", True),
    ("/foo/bar/%E3%83%84", "/foo/bar/ツ", True),
    ("/foo/bar/%e3%83%84", "/foo/bar/%E3%83%84", True),
    ("/foo/bar/%62%61%7A", "/foo/bar/baz", True),
    ("/foo/bar/baz", "/foo/bar/%62%61%7a", True),
    # RFC 9309 §2.2.3 special characters
    ("/this/*/exactly", "/this/is/exactly", True),
    ("/this/*/exactly", "/this/exactly", False),
    ("/this/path/exactly$", "/this/path/exactly", True),
    ("/this/path/exactly$", "/this/path/exactly/more", False),
    ("/path/file-with-a-%2A.html", "/path/file-with-a-*.html", True),
    ("/path/file-with-a-%2A.html", "/path/file-with-a-x.html", False),
    ("/path/foo-%24", "/path/foo-$", True),
    # wildcards and anchors
    ("/*.pdf$", "/x.pdf", True), ("/*.pdf$", "/a/b/c.pdf", True), ("/*.pdf$", "/x.pdf?dl=1", False),
    ("/*.pdf$", "/x.pdfx", False), ("/*.pdf", "/x.pdfx", True), ("/*?", "/search?q=1", True), ("/*?", "/search", False),
    ("/private*/", "/private-stuff/x", True), ("/private*/", "/private", False), ("/a**b", "/a/x/b", True),
    ("*.php", "/index.php", True), ("/x$y", "/x$y", True), ("/x$y", "/xy", False), ("fish", "/fish/x", True),
    ("/$", "/", True), ("/$", "/x", False), ("/*/*/*/*/*/*/*/*/*/*/*/*/*/*/z$", "/" + "a/" * 400 + "y", False)])
def test_robots_pattern_matching(rule, path, matches):
    rules = net.RobotsRules.parse(f"User-agent: *\nDisallow: {rule}\n")
    assert rules.allowed("http://e.example" + path) is (not matches)


def test_robots_parsing_tolerates_malformed_input():
    text = ("﻿Disallow: /before-any-group\r\nUser-agent: *\rDisallow /no-colon\nNonsense: /x\n"
            "Disallow: /kept # trailing comment\n# Disallow: /commented\nDisallow:\nAllow:\nCrawl-delay: soon\n"
            "Crawl-delay: 2.5\n\x00garbage\n")
    rules = net.RobotsRules.parse(text)
    for path, expected in (("/before-any-group", True), ("/no-colon", True), ("/x", True), ("/kept", False),
                           ("/commented", True), ("/robots.txt", True)):
        assert rules.allowed("http://e.example" + path) is expected, path
    assert rules.delay == 2.5
    assert net.RobotsRules.parse("").allowed("http://e.example/x") is True
    assert net.RobotsRules.parse("User-agent: *\nDisallow: /\n").allowed("http://e.example/robots.txt") is True


def test_robots_crawl_delay_sitemaps_and_parse_cap():
    text = ("Sitemap: https://e.example/a.xml\nUser-agent: otherbot\nCrawl-delay: 30\n\n"
            "User-agent: *\nCrawl-delay: 4\nDisallow: /early\nSitemap: https://e.example/b.xml\n")
    rules = net.RobotsRules.parse(text)
    assert rules.delay == 4.0 and rules.sitemaps == ("https://e.example/a.xml", "https://e.example/b.xml")
    padding = "# " + "x" * 98 + "\n"
    big = "User-agent: *\nDisallow: /early\n" + padding * (net.ROBOTS_PARSE_BYTES // len(padding)) + \
        "Disallow: /late\n"
    assert len(big.encode()) > net.ROBOTS_PARSE_BYTES
    rules = net.RobotsRules.parse(big)
    assert rules.allowed("http://e.example/early") is False and rules.allowed("http://e.example/late") is True
    assert net._robots_from_text("User-agent: *\nCrawl-delay: 99999\n", 0.0).delay == net.MAX_CRAWL_DELAY


def test_robots_wildcards_enforced_by_fetcher(web, tmp_path):
    """R5: urllib.robotparser ignored `*` and `$`, so `Disallow: /*.pdf$` did not block /x.pdf."""
    web.add("wild.example", "/robots.txt", "User-agent: *\nDisallow: /*.pdf$\nDisallow: /*?sort=\n",
            content_type="text/plain")
    web.add("wild.example", "/papers/x.pdf", b"%PDF-1.4", content_type="application/pdf")
    web.add("wild.example", "/list", "list")
    fetcher, *_ = make(web, tmp_path)
    assert code_of(lambda: fetcher.fetch("http://wild.example/papers/x.pdf")).code == "blocked_robots"
    assert code_of(lambda: fetcher.fetch("http://wild.example/list?sort=asc")).code == "blocked_robots"
    assert web.requests_to("wild.example", "/papers/x.pdf") == []
    assert fetcher.fetch("http://wild.example/list").body == b"list"
    assert fetcher.robots_allowed("http://wild.example/papers/x.pdf?download=1") is True


# ---- per-host intervals (R10) ----------------------------------------------------------------------------------

def test_host_intervals_with_fake_clock(web, tmp_path):
    assert NetPolicy().host_intervals == {"export.arxiv.org": 3.0, "eutils.ncbi.nlm.nih.gov": 0.34}
    for host in ("export.arxiv.org", "eutils.ncbi.nlm.nih.gov", "plain.example", "api.slow.example"):
        web.add(host, "/q", "{}", content_type="application/json")
    fetcher, clock, *_ = make(web, tmp_path, min_domain_interval=0.0)
    for _ in range(3):
        fetcher.fetch("http://export.arxiv.org/q", purpose="api")
    assert clock.sleeps == [3.0, 3.0]                      # at most one request every 3 s
    clock.sleeps.clear()
    for _ in range(3):
        fetcher.fetch("http://eutils.ncbi.nlm.nih.gov/q", purpose="api")
    assert clock.sleeps == pytest.approx([0.34, 0.34])
    clock.sleeps.clear()
    fetcher.fetch("http://plain.example/q", purpose="api")
    fetcher.fetch("http://plain.example/q", purpose="api")
    assert clock.sleeps == []
    # the largest of min_domain_interval, host_intervals and Crawl-delay applies; entries cover subdomains
    fetcher, clock, *_ = make(web, tmp_path, min_domain_interval=5.0, host_intervals={"slow.example": 2.0})
    fetcher.fetch("http://api.slow.example/q", purpose="api")
    fetcher.fetch("http://api.slow.example/q", purpose="api")
    assert clock.sleeps == [5.0]
    fetcher, clock, *_ = make(web, tmp_path, min_domain_interval=1.0, host_intervals={"slow.example": 2.0})
    fetcher.fetch("http://api.slow.example/q", purpose="api")
    fetcher.fetch("http://api.slow.example/q", purpose="api")
    assert clock.sleeps == [2.0]


# ---- API size cap (R15) ----------------------------------------------------------------------------------------

def test_api_responses_have_their_own_size_cap(web, tmp_path):
    """R15: a 6.2 MB PyPI JSON document must not fail the 5 MB page cap."""
    big = b'{"releases": "' + b"r" * 6_200_000 + b'"}'
    web.add("pypi.example", "/pypi/ruff/json", big, content_type="application/json")
    web.add("pypi.example", "/page", b"<p>" + b"p" * 6_200_000 + b"</p>")
    fetcher, *_ = make(web, tmp_path)
    budget, usage = Budget(max_bytes=100_000_000), Usage()
    assert fetcher.policy.max_bytes == 5_000_000 and fetcher.policy.max_api_bytes == 16_000_000
    result = fetcher.fetch("http://pypi.example/pypi/ruff/json", purpose="api", budget=budget, usage=usage)
    assert result.body == big and not result.truncated
    assert usage.bytes == len(big)                          # budget bytes still charged
    page = fetcher.fetch("http://pypi.example/page", conditional=False)
    assert page.truncated and len(page.body) == 5_000_000   # page cap unchanged
    fetcher, *_ = make(web, tmp_path, max_api_bytes=1_000_000)
    assert code_of(lambda: fetcher.fetch("http://pypi.example/pypi/ruff/json", purpose="api")).code == "too_large"


# ---- sub-resources for the renderer (F4, Lane A throttle) ------------------------------------------------------

def test_subresource_purpose_admits_scripts_and_styles(web, tmp_path):
    web.add("cdn.example", "/jquery.js", "window.x = 1;", content_type="application/javascript")
    web.add("cdn.example", "/legacy.js", "var y;", content_type="text/javascript; charset=utf-8")
    web.add("cdn.example", "/site.css", "p {}", content_type="text/css")
    web.add("cdn.example", "/data.json", "{}", content_type="application/json", headers={"ETag": '"d"'})
    web.add("cdn.example", "/logo.png", b"\x89PNG", content_type="image/png")
    web.add("cdn.example", "/logo.svg", b"<svg/>", content_type="image/svg+xml")
    web.add("cdn.example", "/huge.js", "z" * 5000, content_type="application/javascript")
    fetcher, *_ = make(web, tmp_path)
    assert code_of(lambda: fetcher.fetch("http://cdn.example/jquery.js")).code == "unsupported_type"
    for path in ("/jquery.js", "/legacy.js", "/site.css", "/data.json"):
        result = fetcher.fetch(f"http://cdn.example{path}", purpose="subresource")
        assert result.status == 200 and result.retrieval_mode == "live"
        assert fetcher.cache.lookup(f"http://cdn.example{path}") is None          # never cached
    assert fetcher.cache.stats()["urls"] == 0
    for path in ("/logo.png", "/logo.svg"):
        assert code_of(lambda: fetcher.fetch(f"http://cdn.example{path}", purpose="subresource")).code == \
            "unsupported_type"
    assert code_of(lambda: fetcher.fetch("http://internal.test/evil.js", purpose="subresource")).code == \
        "blocked_policy"
    assert code_of(lambda: fetcher.fetch("http://cdn.example/x.js", method="POST", purpose="subresource")).code == \
        "blocked_policy"
    small, *_ = make(web, tmp_path, max_bytes=1000, cache=False)
    assert code_of(lambda: small.fetch("http://cdn.example/huge.js", purpose="subresource")).code == "too_large"


def test_subresource_obeys_robots_and_uses_short_interval(web, tmp_path):
    web.add("render.example", "/robots.txt", "User-agent: *\nCrawl-delay: 9\nDisallow: /secret/\n",
            content_type="text/plain")
    web.add("render.example", "/", "<p>page</p>")
    for path in ("/a.js", "/b.js", "/c.css", "/secret/x.js"):
        web.add("render.example", path, "1", content_type="text/css")
    web.add("export.arxiv.org", "/widget.js", "1", content_type="text/javascript")
    fetcher, clock, *_ = make(web, tmp_path, min_domain_interval=1.0)
    fetcher.fetch("http://render.example/")
    clock.sleeps.clear()
    for path in ("/a.js", "/b.js", "/c.css"):
        fetcher.fetch(f"http://render.example{path}", purpose="subresource")
    assert clock.sleeps == pytest.approx([0.1, 0.1, 0.1])  # not 1 s each, and not the 9 s Crawl-delay
    assert code_of(lambda: fetcher.fetch("http://render.example/secret/x.js", purpose="subresource")).code == \
        "blocked_robots"
    clock.sleeps.clear()
    fetcher.fetch("http://render.example/", conditional=False)
    assert clock.sleeps == pytest.approx([9.0])           # pages still wait the full interval
    clock.sleeps.clear()
    fetcher.fetch("http://export.arxiv.org/widget.js", purpose="subresource")
    fetcher.fetch("http://export.arxiv.org/widget.js", purpose="subresource")
    assert clock.sleeps == [3.0, 3.0]                       # robots.txt, widget, widget: host_intervals bind


# ---- backoff shared through the cache (8) ----------------------------------------------------------------------

def test_retry_after_shared_by_fetchers_on_one_cache(web, tmp_path):
    web.add("busy.example", "/", "slow down", status=429, headers={"Retry-After": "30"})
    first, clock, *_ = make(web, tmp_path)
    assert code_of(lambda: first.fetch("http://busy.example/", purpose="api")).code == "rate_limited"
    hits = len(web.log)
    clock.t += 5
    second = SafeFetcher(NetPolicy(), resolver=web.resolver, connector=web.connector, cache=first.cache,
                         clock=clock, sleep=clock.sleep)
    error = code_of(lambda: second.fetch("http://busy.example/other", purpose="api"))
    assert error.code == "rate_limited" and error.retry_after == pytest.approx(25)
    assert len(web.log) == hits                             # the new fetcher did not touch the network
    clock.t += 26
    web.add("busy.example", "/", "ok", content_type="text/plain")
    third = SafeFetcher(NetPolicy(), resolver=web.resolver, connector=web.connector, cache=first.cache,
                        clock=clock, sleep=clock.sleep)
    assert third.fetch("http://busy.example/", purpose="api").body == b"ok"


# ---- Retry-After -----------------------------------------------------------------------------------------------

def test_retry_after_seconds_sets_backoff(web, tmp_path):
    web.add("busy.example", "/", "slow down", status=429, headers={"Retry-After": "30"})
    fetcher, clock, *_ = make(web, tmp_path)
    error = code_of(lambda: fetcher.fetch("http://busy.example/", purpose="api"))
    assert error.code == "rate_limited" and error.retry_after == 30
    hits = len(web.log)
    clock.t += 10
    error = code_of(lambda: fetcher.fetch("http://busy.example/other", purpose="api"))
    assert error.code == "rate_limited" and error.retry_after == pytest.approx(20)
    assert len(web.log) == hits
    assert fetcher.stats()["hosts"]["busy.example"]["backoffs"] == 1
    clock.t += 21
    web.add("busy.example", "/", "ok", content_type="text/plain")
    assert fetcher.fetch("http://busy.example/", purpose="api").body == b"ok"


def test_retry_after_http_date_on_503(web, tmp_path):
    import email.utils
    fetcher, clock, *_ = make(web, tmp_path)
    web.add("down.example", "/", "maintenance", status=503,
            headers={"Retry-After": email.utils.formatdate(clock.t + 120, usegmt=True)})
    error = code_of(lambda: fetcher.fetch("http://down.example/", purpose="api"))
    assert error.code == "rate_limited" and error.retry_after == pytest.approx(120, abs=1)
    web.add("down2.example", "/", "maintenance", status=503)
    assert code_of(lambda: fetcher.fetch("http://down2.example/", purpose="api")).code == "http_error"


# ---- conditional GET and cache ---------------------------------------------------------------------------------

def test_conditional_get_returns_cached_body(web, tmp_path):
    web.add("etag.example", "/doc", "<p>version 1</p>", headers={"ETag": '"v1"'})
    fetcher, *_ = make(web, tmp_path)
    first = fetcher.fetch("http://etag.example/doc")
    assert first.retrieval_mode == "live" and first.body == b"<p>version 1</p>"
    second = fetcher.fetch("http://etag.example/doc")
    request = web.requests_to("etag.example", "/doc")[-1]
    assert request["headers"]["if-none-match"] == '"v1"'
    # the origin confirmed the stored body just now: a live observation, marked as revalidated (R11-304)
    assert second.retrieval_mode == "live" and second.headers[net.REVALIDATED_HEADER] == "304"
    assert second.body == first.body and second.content_type == "text/html"
    assert net.REVALIDATED_HEADER not in first.headers
    assert fetcher.stats()["hosts"]["etag.example"]["cache_hits"] == 1
    third = fetcher.fetch("http://etag.example/doc", conditional=False)
    assert third.retrieval_mode == "live" and net.REVALIDATED_HEADER not in third.headers
    assert "if-none-match" not in web.requests_to("etag.example", "/doc")[-1]["headers"]


def test_api_responses_are_not_cached(web, tmp_path):
    web.add("q.example", "/search", '{"r": []}', content_type="application/json", headers={"ETag": '"x"'})
    fetcher, *_ = make(web, tmp_path)
    fetcher.fetch("http://q.example/search?q=private+words", purpose="api")
    assert fetcher.cache.lookup("http://q.example/search?q=private+words") is None
    assert fetcher.cache.stats()["urls"] == 0


# ---- budget ----------------------------------------------------------------------------------------------------

def test_budget_counts_robots_redirects_and_bytes(web, tmp_path):
    web.add_redirect("b.example", "/start", "/end")
    web.add("b.example", "/end", "x" * 100)
    fetcher, *_ = make(web, tmp_path)
    budget, usage = Budget(max_requests=10), Usage()
    fetcher.fetch("http://b.example/start", budget=budget, usage=usage)
    assert usage.requests == 3 and usage.bytes == 100          # robots.txt + redirect + final


def test_budget_exhausted_before_exceeding(web, tmp_path):
    web.add_redirect("b.example", "/start", "/end")
    web.add("b.example", "/end", "x")
    fetcher, *_ = make(web, tmp_path)
    budget, usage = Budget(max_requests=2), Usage()
    with pytest.raises(BudgetExceeded) as info:
        fetcher.fetch("http://b.example/start", budget=budget, usage=usage)
    assert info.value.resource == "requests" and usage.requests == 2
    assert web.requests_to("b.example", "/end") == []


def test_byte_budget_never_exceeded(web, tmp_path):
    web.add("b.example", "/big", "y" * 5000)
    fetcher, *_ = make(web, tmp_path)
    budget, usage = Budget(max_bytes=1000), Usage()
    with pytest.raises(BudgetExceeded) as info:
        fetcher.fetch("http://b.example/big", purpose="api", budget=budget, usage=usage)
    assert info.value.resource == "bytes" and usage.bytes <= 1000


# ---- coalescing and async --------------------------------------------------------------------------------------

def test_identical_concurrent_gets_are_coalesced(web, tmp_path):
    web.add("co.example", "/slow", "shared", delay=0.5)
    fetcher, *_ = make(web, tmp_path, cache=False)
    results, errors = [], []

    def run():
        try:
            results.append(fetcher.fetch("http://co.example/slow", purpose="api"))
        except Exception as error:      # surfaced by the assertion below
            errors.append(error)

    threads = [threading.Thread(target=run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and [r.body for r in results] == [b"shared"] * 3
    assert web.routes[("co.example", "/slow")].hits == 1
    assert fetcher.stats()["hosts"]["co.example"]["coalesced"] == 2


def test_afetch_runs_in_threads(web, tmp_path):
    import asyncio
    web.add("as.example", "/1", "one", content_type="text/plain")
    web.add("as.example", "/2", "two", content_type="text/plain")
    fetcher, *_ = make(web, tmp_path, cache=False, min_domain_interval=0.0)

    async def main():
        return await asyncio.gather(fetcher.afetch("http://as.example/1", purpose="api", budget=Budget(),
                                                   usage=Usage()),
                                    fetcher.afetch("http://as.example/2", purpose="api"))

    one, two = asyncio.run(main())
    assert (one.body, two.body) == (b"one", b"two")


def test_stats_shape(web, tmp_path):
    web.add("s.example", "/", "abc", content_type="text/plain")
    fetcher, *_ = make(web, tmp_path)
    fetcher.fetch("http://s.example/", purpose="api")
    stats = fetcher.stats()
    host = stats["hosts"]["s.example"]
    assert host["requests"] == 1 and host["bytes"] == 3 and host["errors"] == 0
    assert stats["totals"]["requests"] == 1 and stats["inflight"] == 0


def test_set_cookie_not_kept_and_host_header_original(web, tmp_path):
    web.add("h.example", "/", "x", content_type="text/plain", headers={"Set-Cookie": "id=1"})
    fetcher, *_ = make(web, tmp_path)
    result = fetcher.fetch("http://h.example/", purpose="api")
    assert "set-cookie" not in result.headers
    request = web.requests_to("h.example")[0]
    assert request["headers"]["host"] == "h.example"
    assert request["headers"]["user-agent"].startswith("NeuroSciFi-WebCortex/")


def test_connection_close_response_read_under_deadline(web, tmp_path):
    """Servers that answer `Connection: close` make http.client drop its socket early; the body is still read."""
    web.add("close.example", "/", "x" * 200_000, content_type="text/plain", headers={"Connection": "close"})
    fetcher, *_ = make(web, tmp_path)
    assert len(fetcher.fetch("http://close.example/", purpose="api").body) == 200_000

# ---- live smoke ------------------------------------------------------------------------------------------------

@pytest.mark.live
def test_live_tls_smoke():
    from neuroscifi.webcortex.extract import extract, parse_feed
    fetcher = SafeFetcher(NetPolicy())
    page = fetcher.fetch("https://example.com/")
    assert page.status == 200 and page.remote_ip and check_address(page.remote_ip) is None
    assert "Example Domain" in extract(page).title
    api = fetcher.fetch("https://export.arxiv.org/api/query?search_query=all:dendritic&max_results=1", purpose="api")
    assert api.content_type in ("application/atom+xml", "application/xml", "text/xml")
    items = parse_feed(api.body, api.final_url)
    assert len(items) == 1 and items[0]["title"]

