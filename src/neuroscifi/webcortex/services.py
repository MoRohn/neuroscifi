"""The acquisition and discovery services one research run uses (ARCHITECTURE §4.4, §7).

`build(db, db_path)` wires them from the individual's WebConfig:
- the public cache `<db stem>.webcache.sqlite` next to the instance file (ADR-023);
- a SafeFetcher with the owner's deny list, per-host interval and document size limit;
- a ProviderRegistry over `providers.build(config, fetcher, feeds=state.get_feeds(db))` (or the given providers;
  a FeedProvider joins when the owner listed feeds) whose health state is
  loaded from procedural memory (web_strategies) and saved back by `persist()` after a run;
- a BrowserRenderer only when `browser_rendering` is on;
- `fixture` (a FixtureWeb) injects its resolver/connector so tests and the offline bench never reach the network.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import socket
import threading
import time

from . import state


def holder_id() -> str:
    """Lease holder name of this process and thread (the host appears only as a digest)."""
    host = hashlib.sha256(socket.gethostname().encode()).hexdigest()[:12]
    return f"{host}:{os.getpid()}:{threading.get_ident()}"


@dataclass
class Services:
    fetcher: object
    cache: object
    registry: object
    renderer: object
    clock: object
    config: object
    holder: str = field(default_factory=holder_id)
    db_path: str | None = None

    def persist(self, db, now=None):
        """Save the registry's provider health (procedural memory)."""
        if self.registry is not None and hasattr(self.registry, "dump_state"):
            state.save_strategies(db, self.registry.dump_state(), self.clock() if now is None else now)

    def prune_cache(self, now=None):
        """Public-cache retention (ADR-023): drop entries older than config.cache_max_age_days, then the oldest
        bodies until config.cache_max_bytes holds. PublicCache.maybe_prune runs it at most hourly; returns its
        result, or None when it did not run (or the cache has no retention API)."""
        prune = getattr(self.cache, "maybe_prune", None)
        if prune is None:
            return None
        cfg = self.config
        return prune(self.clock() if now is None else now, max_bytes=int(cfg.cache_max_bytes),
                     max_age_seconds=float(cfg.cache_max_age_days) * 86400.0)

    def close(self):
        if self.cache is not None and hasattr(self.cache, "close"):
            self.cache.close()


def cache_path(db_path) -> Path:
    path = Path(db_path).resolve()
    return path.with_name(f"{path.stem}.webcache.sqlite")


def build(db, db_path, *, fixture=None, providers=None, clock=time.time, sleep=time.sleep) -> Services:
    from . import net
    from .cache import PublicCache
    from .search import ProviderRegistry
    cfg = state.get_config(db)
    cache = PublicCache(cache_path(db_path))
    policy = net.NetPolicy(deny_domains=tuple(cfg.deny_domains), min_domain_interval=cfg.min_domain_interval,
                           max_bytes=cfg.max_document_bytes)
    inject = {"resolver": fixture.resolver, "connector": fixture.connector} if fixture is not None else {}
    fetcher = net.SafeFetcher(policy, cache=cache, clock=clock, sleep=sleep, **inject)
    if providers is None:
        from . import providers as provider_modules
        providers = provider_modules.build(cfg, fetcher, feeds=state.get_feeds(db), clock=clock)
    registry = ProviderRegistry(list(providers), clock=clock)
    registry.load_state(state.load_strategies(db))
    renderer = None
    if cfg.browser_rendering:
        from .browser import BrowserRenderer
        renderer = BrowserRenderer(fetcher)
    return Services(fetcher=fetcher, cache=cache, registry=registry, renderer=renderer, clock=clock, config=cfg,
                    db_path=str(db_path))
