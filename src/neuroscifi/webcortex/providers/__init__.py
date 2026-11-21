"""Search providers (DISCOVERY). `build(config, fetcher, feeds=)` instantiates the providers enabled in
`config.providers`, plus a FeedProvider over the owner's feeds (state.get_feeds, `nsf web feeds`) when any exist.

open-webSearch and SearXNG talk to their loopback services directly; every other provider goes through `fetcher`
(net.SafeFetcher, purpose='api'). Unknown names in the config are ignored here and reported by `unknown()`.

SearXNG (ADR-034) is built only when `config.searxng_url` is set: at its position in `config.providers` when named
there, else first (the primary general provider). open-webSearch then gets `fallback_for = "searxng"`: the registry
runs it for a query only when SearXNG's circuit is open, or SearXNG failed or found nothing (search.py). Without
`searxng_url` nothing changes.
"""
from __future__ import annotations

import time

from .feeds import FeedProvider
from .openwebsearch import OpenWebSearchProvider
from .searxng import SearXNGProvider
from .structured import (ArxivProvider, CrossrefProvider, GitHubReleasesProvider, NpmProvider, OpenAlexProvider,
                         OSVProvider, PubMedProvider, PyPIProvider, WikipediaProvider)

STRUCTURED = {"wikipedia": WikipediaProvider, "arxiv": ArxivProvider, "openalex": OpenAlexProvider,
              "crossref": CrossrefProvider, "pubmed": PubMedProvider, "pypi": PyPIProvider, "npm": NpmProvider,
              "osv": OSVProvider, "github": GitHubReleasesProvider}
KNOWN = ("searxng", "openwebsearch", "feeds", *STRUCTURED)


def unknown(config) -> list[str]:
    return [n for n in config.providers if n not in KNOWN]


def build(config, fetcher, *, feeds=None, clock=time.time) -> list:
    """Providers enabled in `config.providers`, in that order (duplicates dropped), then the FeedProvider when
    `feeds` (default: `config.feeds` if the configuration carries one) lists any feed URL. 'feeds' in
    config.providers places it at that position instead."""
    feeds = list(feeds if feeds is not None else getattr(config, "feeds", None) or [])
    searxng_url = getattr(config, "searxng_url", "") or ""
    names = list(config.providers)
    if searxng_url and "searxng" not in names:
        names.insert(0, "searxng")
    out, seen = [], set()
    for name in [*names, "feeds"]:
        if name in seen or name not in KNOWN:
            continue
        seen.add(name)
        if name == "searxng":
            if searxng_url:
                out.append(SearXNGProvider(searxng_url, engines=getattr(config, "searxng_engines", None) or {},
                                           clock=clock))
        elif name == "openwebsearch":
            provider = OpenWebSearchProvider(config.openwebsearch_url, config.engines)
            if searxng_url:
                provider.fallback_for = "searxng"
            out.append(provider)
        elif name == "feeds":
            if feeds:
                out.append(FeedProvider(feeds, fetcher=fetcher, clock=clock))
        else:
            out.append(STRUCTURED[name](fetcher))
    return out


__all__ = ["build", "unknown", "KNOWN", "STRUCTURED", "FeedProvider", "SearXNGProvider"]
