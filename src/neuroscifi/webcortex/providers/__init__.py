"""Search providers (DISCOVERY). `build(config, fetcher, feeds=)` instantiates the providers enabled in
`config.providers`, plus a FeedProvider over the owner's feeds (state.get_feeds, `nsf web feeds`) when any exist.

open-webSearch and SearXNG talk to their loopback services directly; every other provider goes through `fetcher`
(net.SafeFetcher, purpose='api'). Unknown names in the config are ignored here and reported by `unknown()`.

SearXNG (ADR-034) is built only when `config.searxng_url` is set: at its position in `config.providers` when named
there, else first (the primary general provider). open-webSearch then gets `fallback_for = "searxng"`: the registry
runs it for a query only when SearXNG's circuit is open, or SearXNG failed or found nothing (search.py). Without
`searxng_url` nothing changes.

Brave (ADR-034 amendment 1) is built only when its key file exists (providers/keys.py; `nsf web provider set-key
brave`) and `daily_provider_queries['brave']` is not 0: at its position in `config.providers` when named there,
else first. SearXNG then gets `fallback_for = "brave"` (and open-webSearch stays SearXNG's fallback, or becomes
Brave's when SearXNG is not configured), so a general or news query goes to Brave, then to SearXNG when Brave is
rate-limited, capped, failing or finds nothing, then to open-webSearch. Without a key nothing changes.
"""
from __future__ import annotations

import time

from . import keys
from .brave import DEFAULT_DAILY_CAP, BraveProvider
from .feeds import FeedProvider
from .openwebsearch import OpenWebSearchProvider
from .searxng import SearXNGProvider
from .structured import (ArxivProvider, CrossrefProvider, GitHubReleasesProvider, NpmProvider, OpenAlexProvider,
                         OSVProvider, PubMedProvider, PyPIProvider, WikipediaProvider)

STRUCTURED = {"wikipedia": WikipediaProvider, "arxiv": ArxivProvider, "openalex": OpenAlexProvider,
              "crossref": CrossrefProvider, "pubmed": PubMedProvider, "pypi": PyPIProvider, "npm": NpmProvider,
              "osv": OSVProvider, "github": GitHubReleasesProvider}
KNOWN = ("brave", "searxng", "openwebsearch", "feeds", *STRUCTURED)


def unknown(config) -> list[str]:
    return [n for n in config.providers if n not in KNOWN]


def daily_cap(config, name: str) -> int:
    """The configured daily query cap of a keyed provider (WebConfig.daily_provider_queries), else its default."""
    caps = getattr(config, "daily_provider_queries", None) or {}
    value = caps.get(name, DEFAULT_DAILY_CAP) if isinstance(caps, dict) else DEFAULT_DAILY_CAP
    return max(0, int(value)) if isinstance(value, (int, float)) and not isinstance(value, bool) else DEFAULT_DAILY_CAP


def build(config, fetcher, *, feeds=None, clock=time.time) -> list:
    """Providers enabled in `config.providers`, in that order (duplicates dropped), then the FeedProvider when
    `feeds` (default: `config.feeds` if the configuration carries one) lists any feed URL. 'feeds' in
    config.providers places it at that position instead."""
    feeds = list(feeds if feeds is not None else getattr(config, "feeds", None) or [])
    searxng_url = getattr(config, "searxng_url", "") or ""
    brave_cap = daily_cap(config, "brave")
    brave = brave_cap > 0 and keys.key_state("brave")["key"] != "missing"
    names = list(config.providers)
    if searxng_url and "searxng" not in names:
        names.insert(0, "searxng")
    if brave and "brave" not in names:
        names.insert(0, "brave")
    out, seen = [], set()
    for name in [*names, "feeds"]:
        if name in seen or name not in KNOWN:
            continue
        seen.add(name)
        if name == "brave":
            if brave:
                out.append(BraveProvider(fetcher, daily_cap=brave_cap, clock=clock))
        elif name == "searxng":
            if searxng_url:
                provider = SearXNGProvider(searxng_url, engines=getattr(config, "searxng_engines", None) or {},
                                           clock=clock)
                if brave:
                    provider.fallback_for = "brave"
                out.append(provider)
        elif name == "openwebsearch":
            provider = OpenWebSearchProvider(config.openwebsearch_url, config.engines)
            if searxng_url:
                provider.fallback_for = "searxng"
            elif brave:
                provider.fallback_for = "brave"
            out.append(provider)
        elif name == "feeds":
            if feeds:
                out.append(FeedProvider(feeds, fetcher=fetcher, clock=clock))
        else:
            out.append(STRUCTURED[name](fetcher))
    return out


__all__ = ["build", "unknown", "daily_cap", "KNOWN", "STRUCTURED", "FeedProvider", "SearXNGProvider",
           "BraveProvider"]
