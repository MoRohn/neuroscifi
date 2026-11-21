"""Bounded crawl: depth/page caps, same-site, robots, sitemap discovery, de-duplication; feed discovery."""
from neuroscifi.webcortex.cache import PublicCache
from neuroscifi.webcortex.crawl import crawl, discover_feeds
from neuroscifi.webcortex.net import NetPolicy, SafeFetcher
from neuroscifi.webcortex.types import Budget, Usage


class FakeClock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def make(web, tmp_path):
    clock = FakeClock()
    return SafeFetcher(NetPolicy(), resolver=web.resolver, connector=web.connector,
                       cache=PublicCache(tmp_path / "c.sqlite"), clock=clock, sleep=clock.sleep)


def page(*links, text="page"):
    anchors = "".join(f'<a href="{href}">{href}</a> ' for href in links)
    return f"<html><body><p>{text}</p>{anchors}</body></html>"


def site(web):
    web.add("site.example", "/", page("/a", "/b", "/a#dup", "/a?utm_source=x", "http://other.example/x",
                                      "/img.png", "mailto:x@site.example"))
    web.add("site.example", "/a", page("/a1", "/"))
    web.add("site.example", "/b", page("/b1"))
    web.add("site.example", "/a1", page("/a2"))
    web.add("site.example", "/b1", page())
    web.add("site.example", "/a2", page())
    web.add("other.example", "/x", page())


def paths(results):
    return [r.final_url.split("site.example", 1)[-1] for r in results]


def test_depth_cap_and_dedupe(web, tmp_path):
    site(web)
    fetcher = make(web, tmp_path)
    budget, usage = Budget(max_pages=20, max_depth=3, max_requests=100), Usage()
    results = crawl(fetcher, ["http://site.example/"], budget=budget, usage=usage, max_pages=20, max_depth=1,
                    sitemaps=False)
    assert paths(results) == ["/", "/a", "/b"]
    assert usage.pages == 3
    assert len(web.requests_to("site.example", "/a")) == 1 and web.requests_to("other.example") == []
    assert web.requests_to("site.example", "/img.png") == []
    results = crawl(make(web, tmp_path), ["http://site.example/"], budget=budget, usage=Usage(), max_pages=20,
                    max_depth=2, sitemaps=False)
    assert paths(results) == ["/", "/a", "/b", "/a1", "/b1"]


def test_page_cap_from_args_and_budget(web, tmp_path):
    site(web)
    results = crawl(make(web, tmp_path), ["http://site.example/"], budget=Budget(max_pages=10, max_depth=3),
                    usage=Usage(), max_pages=2, max_depth=3, sitemaps=False)
    assert len(results) == 2
    usage = Usage(pages=8)
    results = crawl(make(web, tmp_path), ["http://site.example/"], budget=Budget(max_pages=10, max_depth=3),
                    usage=usage, max_pages=50, max_depth=3, sitemaps=False)
    assert len(results) == 2 and usage.pages == 10
    results = crawl(make(web, tmp_path), ["http://site.example/"], budget=Budget(max_pages=10, max_depth=0),
                    usage=Usage(), max_pages=10, max_depth=5, sitemaps=False)
    assert paths(results) == ["/"]


def test_cross_site_when_allowed(web, tmp_path):
    site(web)
    results = crawl(make(web, tmp_path), ["http://site.example/"], budget=Budget(max_pages=10, max_depth=1),
                    usage=Usage(), max_pages=10, max_depth=1, same_site=False, sitemaps=False)
    assert "http://other.example/x" in [r.final_url for r in results]


def test_robots_respected_and_failures_recorded(web, tmp_path):
    site(web)
    web.add("site.example", "/robots.txt", "User-agent: *\nDisallow: /b\n", content_type="text/plain")
    failures = []
    results = crawl(make(web, tmp_path), ["http://site.example/"], budget=Budget(max_pages=10, max_depth=2),
                    usage=Usage(), max_pages=10, max_depth=2, sitemaps=False, failures=failures)
    assert "/b" not in paths(results) and "/b1" not in paths(results)
    assert web.requests_to("site.example", "/b") == []
    assert any(f["code"] == "blocked_robots" for f in failures)


def test_sitemap_discovery_via_robots_and_index(web, tmp_path):
    web.add("map.example", "/robots.txt", "User-agent: *\nAllow: /\nSitemap: http://map.example/index.xml\n",
            content_type="text/plain")
    web.add("map.example", "/index.xml", """<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <sitemap><loc>http://map.example/s1.xml</loc></sitemap></sitemapindex>""", content_type="application/xml")
    web.add("map.example", "/s1.xml", """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url><loc>http://map.example/deep/1</loc></url><url><loc>http://elsewhere.example/2</loc></url>
        </urlset>""", content_type="application/xml")
    web.add("map.example", "/", page())
    web.add("map.example", "/deep/1", page(text="from sitemap"))
    results = crawl(make(web, tmp_path), ["http://map.example/"], budget=Budget(max_pages=10, max_depth=1),
                    usage=Usage(), max_pages=10, max_depth=1)
    assert [r.final_url for r in results] == ["http://map.example/", "http://map.example/deep/1"]


def test_sitemap_default_location(web, tmp_path):
    web.add("plain.example", "/", page())
    web.add("plain.example", "/sitemap.xml", """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url><loc>http://plain.example/hidden-page</loc></url></urlset>""", content_type="text/xml")
    web.add("plain.example", "/hidden-page", page())
    failures = []
    results = crawl(make(web, tmp_path), ["http://plain.example/"], budget=Budget(max_pages=5, max_depth=1),
                    usage=Usage(), max_pages=5, max_depth=1, failures=failures)
    assert [r.final_url for r in results] == ["http://plain.example/", "http://plain.example/hidden-page"]
    assert failures == []


def test_redirect_onto_seen_page_not_duplicated(web, tmp_path):
    web.add("rd.example", "/", page("/alias", "/real"))
    web.add_redirect("rd.example", "/alias", "/real")
    web.add("rd.example", "/real", page())
    results = crawl(make(web, tmp_path), ["http://rd.example/"], budget=Budget(max_pages=10, max_depth=1),
                    usage=Usage(), max_pages=10, max_depth=1, sitemaps=False)
    assert [r.final_url for r in results] == ["http://rd.example/", "http://rd.example/real"]


def test_budget_exhaustion_ends_crawl(web, tmp_path):
    site(web)
    failures = []
    usage = Usage()
    results = crawl(make(web, tmp_path), ["http://site.example/"], budget=Budget(max_pages=10, max_depth=3,
                                                                                 max_requests=3),
                    usage=usage, max_pages=10, max_depth=3, sitemaps=False, failures=failures)
    assert len(results) == 2 and usage.requests == 3          # robots + two pages
    assert failures[-1]["code"] == "budget_exhausted"


def test_nofollow_respected(web, tmp_path):
    web.add("nf.example", "/", '<html><body><a rel="nofollow" href="/x">x</a><a href="/y">y</a></body></html>')
    web.add("nf.example", "/y", '<html><head><meta name="robots" content="noindex, nofollow"></head>'
                                '<body><a href="/z">z</a></body></html>')
    web.add("nf.example", "/x", page())
    web.add("nf.example", "/z", page())
    results = crawl(make(web, tmp_path), ["http://nf.example/"], budget=Budget(max_pages=10, max_depth=3),
                    usage=Usage(), max_pages=10, max_depth=3, sitemaps=False)
    assert [r.final_url for r in results] == ["http://nf.example/", "http://nf.example/y"]


def test_discover_feeds():
    html = b"""<html><head>
      <link rel="alternate" type="application/rss+xml" href="/feed.xml">
      <link rel="alternate" type="application/atom+xml; charset=utf-8" href="https://blog.example/atom">
      <link rel="alternate" type="text/html" hreflang="de" href="/de">
      <link rel="stylesheet" type="application/rss+xml" href="/not-a-feed">
      <link rel="alternate" type="application/rss+xml" href="javascript:alert(1)">
    </head><body></body></html>"""
    assert discover_feeds(html, "http://blog.example/post") == ["http://blog.example/feed.xml",
                                                                "https://blog.example/atom"]
