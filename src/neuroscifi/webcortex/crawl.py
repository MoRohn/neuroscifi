"""Bounded, polite crawling and feed discovery (ARCHITECTURE §4.1).

Contract
- `crawl` is a breadth-first walk from `seeds` through `SafeFetcher.fetch(purpose="crawl")`, so the URL/address
  policy, robots.txt (incl. Crawl-delay), throttling, Retry-After and the task budget apply to every request.
- Caps: depth <= min(max_depth, budget.max_depth); pages <= min(max_pages, budget.max_pages - usage.pages). One page
  is charged to `usage.pages` per document acquired (failed fetches cost requests, not pages).
- `same_site=True` keeps the walk on the seeds' registrable domains. URLs are de-duplicated by `canonical_url`
  (before fetching and again after redirects). `rel=nofollow` links and `<meta name=robots content=nofollow>`
  pages are not followed; obvious binary links (images, archives, media) are skipped without a request.
- Sitemaps (`sitemaps=True`): `Sitemap:` lines from robots.txt, else `/sitemap.xml`; sitemap indexes are followed
  at most MAX_SITEMAP_FETCHES times per crawl. Sitemap entries are depth-1 discoveries.
- Results come back in visit order. Failures are never silent: each is appended (FetchError.to_json(), or a
  budget_exhausted record) to the optional `failures` list, and a budget exhaustion ends the crawl.
- Links and feeds found in pages are untrusted data; they only ever reach `fetcher.fetch`.
"""
from __future__ import annotations

from collections import deque
from urllib.parse import urlsplit, urlunsplit

from scrapling.parser import Selector

from .extract import _absolute, canonical_url, parse_sitemap, registrable_domain
from .types import BudgetExceeded, FetchError

MAX_SITEMAP_FETCHES = 4
MAX_SITEMAP_ENTRIES = 500
FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/rdf+xml", "application/feed+json")
_SKIP_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".tif", ".tiff", ".mp3",
                  ".mp4", ".m4a", ".wav", ".ogg", ".webm", ".avi", ".mov", ".mkv", ".zip", ".gz", ".tgz", ".bz2",
                  ".xz", ".7z", ".rar", ".tar", ".exe", ".msi", ".dmg", ".iso", ".apk", ".deb", ".rpm", ".woff",
                  ".woff2", ".ttf", ".otf", ".css", ".js", ".wasm", ".bin")
HTML_TYPES = ("text/html", "application/xhtml+xml")


def discover_feeds(html: bytes, url) -> list[str]:
    """Feed URLs advertised with <link rel="alternate" type="application/rss+xml|atom+xml|..."> (absolute)."""
    sel = Selector(bytes(html).decode("utf-8", "replace") or "<html></html>", url=url, huge_tree=False)
    out = []
    for link in sel.css("link[rel][href]"):
        rels = (link.attrib.get("rel") or "").lower().split()
        kind = (link.attrib.get("type") or "").split(";")[0].strip().lower()
        if "alternate" in rels and kind in FEED_TYPES:
            target = _absolute(url, link.attrib.get("href"))
            if target and target not in out:
                out.append(target)
    return out


def _page_links(body: bytes, url: str) -> list[str]:
    sel = Selector(bytes(body).decode("utf-8", "replace") or "<html></html>", url=url, huge_tree=False)
    for meta in sel.css("meta[name][content]"):
        if (meta.attrib.get("name") or "").lower() == "robots" and \
                "nofollow" in (meta.attrib.get("content") or "").lower():
            return []
    base = url
    for el in sel.css("base[href]")[:1]:
        base = _absolute(url, el.attrib.get("href")) or url
    out = []
    for a in sel.css("a[href]"):
        if "nofollow" in (a.attrib.get("rel") or "").lower().split():
            continue
        target = _absolute(base, a.attrib.get("href"))
        if target and not urlsplit(target).path.lower().endswith(_SKIP_SUFFIXES):
            out.append(target)
    return out


def _record(failures, error):
    if failures is None:
        return
    if isinstance(error, FetchError):
        failures.append(error.to_json())
    else:
        failures.append({"code": "budget_exhausted", "url": None, "detail": str(error), "retry_after": None,
                         "status": None})


def _sitemap_urls(fetcher, seed: str, *, budget, usage, failures) -> list[str]:
    parts = urlsplit(seed)
    origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    try:
        pending = list(fetcher.sitemaps(seed, budget=budget, usage=usage)) or [origin + "/sitemap.xml"]
    except FetchError as error:
        _record(failures, error)
        return []
    found, fetched = [], 0
    while pending and fetched < MAX_SITEMAP_FETCHES and len(found) < MAX_SITEMAP_ENTRIES:
        sitemap = pending.pop(0)
        fetched += 1
        try:
            result = fetcher.fetch(sitemap, purpose="crawl", budget=budget, usage=usage)
            entries = parse_sitemap(result.body, result.final_url)
        except FetchError as error:
            if error.status != 404:
                _record(failures, error)
            continue
        for entry in entries:
            (pending if entry.get("sitemap") else found).append(entry["url"])
    return found[:MAX_SITEMAP_ENTRIES]


def crawl(fetcher, seeds, *, budget, usage, max_pages, max_depth, same_site=True, sitemaps=True,
          failures: list | None = None) -> list:
    """Breadth-first bounded crawl; returns FetchResults in visit order (see module docstring)."""
    page_cap = max(0, min(int(max_pages), budget.max_pages - usage.pages))
    depth_cap = max(0, min(int(max_depth), budget.max_depth))
    seeds = [s for s in dict.fromkeys(seeds) if _absolute(s, s)]
    sites = {registrable_domain(urlsplit(s).hostname or "") for s in seeds}
    queue: deque = deque()
    seen: set = set()

    def enqueue(url, depth):
        key = canonical_url(url)
        if key in seen:
            return
        if same_site and registrable_domain(urlsplit(url).hostname or "") not in sites:
            return
        seen.add(key)
        queue.append((url, depth))

    for seed in seeds:
        enqueue(seed, 0)
    results: list = []
    try:
        if sitemaps and depth_cap >= 1 and page_cap > len(seeds):
            for origin in dict.fromkeys(urlunsplit((urlsplit(s).scheme, urlsplit(s).netloc, "/", "", ""))
                                        for s in seeds):
                for url in _sitemap_urls(fetcher, origin, budget=budget, usage=usage, failures=failures):
                    enqueue(url, 1)
        fetched_keys: set = set()
        while queue and len(results) < page_cap:
            url, depth = queue.popleft()
            try:
                result = fetcher.fetch(url, purpose="crawl", budget=budget, usage=usage)
            except FetchError as error:
                _record(failures, error)
                continue
            final_key = canonical_url(result.final_url)
            if final_key in fetched_keys:
                continue                      # redirected onto a page already collected
            fetched_keys.add(final_key)
            seen.add(final_key)
            usage.charge(budget, pages=1)
            results.append(result)
            if depth < depth_cap and result.content_type in HTML_TYPES:
                for link in _page_links(result.body, result.final_url):
                    enqueue(link, depth + 1)
    except BudgetExceeded as error:
        _record(failures, error)
    return results
