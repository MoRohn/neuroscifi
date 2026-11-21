"""Search providers (DISCOVERY). `build(config, fetcher)` instantiates the providers enabled in `config.providers`.

open-webSearch talks to its loopback daemon directly; every other provider goes through `fetcher`
(net.SafeFetcher, purpose='api'). Unknown names in the config are ignored here and reported by `unknown()`.
"""
from __future__ import annotations

from .openwebsearch import OpenWebSearchProvider
from .structured import (ArxivProvider, CrossrefProvider, GitHubReleasesProvider, NpmProvider, OpenAlexProvider,
                         OSVProvider, PubMedProvider, PyPIProvider, WikipediaProvider)

STRUCTURED = {"wikipedia": WikipediaProvider, "arxiv": ArxivProvider, "openalex": OpenAlexProvider,
              "crossref": CrossrefProvider, "pubmed": PubMedProvider, "pypi": PyPIProvider, "npm": NpmProvider,
              "osv": OSVProvider, "github": GitHubReleasesProvider}
KNOWN = ("openwebsearch", *STRUCTURED)


def unknown(config) -> list[str]:
    return [n for n in config.providers if n not in KNOWN]


def build(config, fetcher) -> list:
    """Providers enabled in `config.providers`, in that order (duplicates dropped)."""
    out, seen = [], set()
    for name in config.providers:
        if name in seen or name not in KNOWN:
            continue
        seen.add(name)
        if name == "openwebsearch":
            out.append(OpenWebSearchProvider(config.openwebsearch_url, config.engines))
        else:
            out.append(STRUCTURED[name](fetcher))
    return out


__all__ = ["build", "unknown", "KNOWN", "STRUCTURED"]
