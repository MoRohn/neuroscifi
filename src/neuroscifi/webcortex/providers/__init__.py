"""Search providers (DISCOVERY). `build(config, fetcher, feeds=)` instantiates the providers enabled in
`config.providers`, plus a FeedProvider over the owner's feeds (state.get_feeds, `nsf web feeds`) when any exist.

open-webSearch talks to its loopback daemon directly; every other provider goes through `fetcher`
(net.SafeFetcher, purpose='api'). Unknown names in the config are ignored here and reported by `unknown()`.
"""
from __future__ import annotations

import time

from .feeds import FeedProvider
from .openwebsearch import OpenWebSearchProvider
from .structured import (ArxivProvider, CrossrefProvider, GitHubReleasesProvider, NpmProvider, OpenAlexProvider,
                         OSVProvider, PubMedProvider, PyPIProvider, WikipediaProvider)

STRUCTURED = {"wikipedia": WikipediaProvider, "arxiv": ArxivProvider, "openalex": OpenAlexProvider,
              "crossref": CrossrefProvider, "pubmed": PubMedProvider, "pypi": PyPIProvider, "npm": NpmProvider,
              "osv": OSVProvider, "github": GitHubReleasesProvider}
KNOWN = ("openwebsearch", "feeds", *STRUCTURED)


def unknown(config) -> list[str]:
    return [n for n in config.providers if n not in KNOWN]


def build(config, fetcher, *, feeds=None, clock=time.time) -> list:
    """Providers enabled in `config.providers`, in that order (duplicates dropped), then the FeedProvider when
    `feeds` (default: `config.feeds` if the configuration carries one) lists any feed URL. 'feeds' in
    config.providers places it at that position instead."""
    feeds = list(feeds if feeds is not None else getattr(config, "feeds", None) or [])
    out, seen = [], set()
    for name in [*config.providers, "feeds"]:
        if name in seen or name not in KNOWN:
            continue
        seen.add(name)
        if name == "openwebsearch":
            out.append(OpenWebSearchProvider(config.openwebsearch_url, config.engines))
        elif name == "feeds":
            if feeds:
                out.append(FeedProvider(feeds, fetcher=fetcher, clock=clock))
        else:
            out.append(STRUCTURED[name](fetcher))
    return out


__all__ = ["build", "unknown", "KNOWN", "STRUCTURED", "FeedProvider"]
