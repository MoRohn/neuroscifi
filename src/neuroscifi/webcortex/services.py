"""The acquisition and discovery services one research run uses (ARCHITECTURE §4.4, §7).

`build(db, db_path)` wires them from the individual's WebConfig:
- the public cache `<db stem>.webcache.sqlite` next to the instance file (ADR-023);
- a SafeFetcher with the owner's deny list, per-host interval and document size limit;
- a ProviderRegistry over `providers.build(config, fetcher, feeds=state.get_feeds(db))` (or the given providers;
  a FeedProvider joins when the owner listed feeds) whose health state is
  loaded from procedural memory (web_strategies) and saved back by `persist()` after a run;
- a BrowserRenderer only when `browser_rendering` or `news_rendering` is on (ADR-041: the run decides per page
  whether the switches allow a render);
- `fixture` (a FixtureWeb) injects its resolver/connector so tests and the offline bench never reach the network.

The public cache is disposable (ADR-023): a corrupt cache file is quarantined (renamed `<name>.corrupt-<time>`) and
rebuilt empty by PublicCache itself; each quarantine is recorded as a warning event (kind 'error', stage
'public_cache', no task) by build() and persist(), so research never stops over it.

Lease holders (`holder_id`) are "<hostname digest>:<pid>:<thread>:<process start time>". `holder_alive` tells
whether a holder on THIS host is still running (the pid exists with the same start time, so a reused pid is not
mistaken for the crashed worker); a holder on another host, or in the older 3-part form without a start time on
a live pid, is unknown (None).
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


def host_digest() -> str:
    return hashlib.sha256(socket.gethostname().encode()).hexdigest()[:12]


def process_start(pid) -> str | None:
    """Start time of process `pid` (clock ticks since boot, /proc/<pid>/stat field 22), or None when unknown."""
    try:
        with open(f"/proc/{int(pid)}/stat", "rb") as handle:
            data = handle.read().decode("ascii", "replace")
        return data[data.rindex(")") + 2:].split()[19]
    except (OSError, ValueError, IndexError):
        return None


def holder_id() -> str:
    """Lease holder name of this process and thread: hostname digest, pid, thread, process start time."""
    return f"{host_digest()}:{os.getpid()}:{threading.get_ident()}:{process_start(os.getpid()) or 0}"


def _pid_exists(pid) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def holder_alive(holder) -> bool | None:
    """Whether the worker named by a lease holder is still running: True / False for a holder on this host (pid
    and, when recorded, process start time), None when it cannot be told (another host, no holder, foreign
    form)."""
    parts = str(holder or "").split(":")
    if len(parts) < 3 or parts[0] != host_digest():
        return None
    try:
        pid = int(parts[1])
    except ValueError:
        return None
    if pid <= 0 or not _pid_exists(pid):
        return False
    recorded = parts[3] if len(parts) > 3 else ""
    if recorded and recorded != "0":
        current = process_start(pid)
        if current is not None and current != recorded:
            return False            # the pid was reused by another process
    return True


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
        """Save the registry's provider health (procedural memory) and record public-cache quarantines."""
        now = self.clock() if now is None else now
        if self.registry is not None and hasattr(self.registry, "dump_state"):
            state.save_strategies(db, self.registry.dump_state(), now)
        self.record_cache_warnings(db, now)

    def record_cache_warnings(self, db, now=None):
        """One warning event (kind 'error', stage 'public_cache', no task) per public-cache quarantine not yet
        recorded. Returns how many were recorded."""
        pending = list(getattr(self.cache, "events", None) or [])
        if not pending:
            return 0
        from ..instance import store
        now = self.clock() if now is None else now
        with store.write(db):
            for event in pending:
                state.log_event(db, None, "error", {"stage": "public_cache", "warning": "the public cache was "
                                                    "corrupt; it was quarantined and rebuilt empty (pages are "
                                                    "fetched again)", "quarantined_as": event.get("quarantined_as"),
                                                    "error": event.get("error")}, now)
        del self.cache.events[:len(pending)]
        return len(pending)

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
    from ..resources import idle   # an idle-unloaded SearXNG is loaded again on the first search (RD-46)
    providers = [idle.on_demand(p, "searxng", db_path) if getattr(p, "name", None) == "searxng" else p
                 for p in providers]
    registry = ProviderRegistry(list(providers), clock=clock)
    registry.load_state(state.load_strategies(db))
    renderer = None
    if cfg.browser_rendering or cfg.news_rendering:
        from .browser import BrowserRenderer
        renderer = BrowserRenderer(fetcher)
    services = Services(fetcher=fetcher, cache=cache, registry=registry, renderer=renderer, clock=clock, config=cfg,
                        db_path=str(db_path))
    services.record_cache_warnings(db)
    return services
