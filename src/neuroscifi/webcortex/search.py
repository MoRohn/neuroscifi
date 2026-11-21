"""DISCOVERY: provider protocol, per-provider/engine circuit breakers, parallel fan-out, fusion and dedupe.

`ProviderRegistry.search(plan, budget=, usage=)` sends each planned `Query` (at most `budget.max_queries`, one
`usage.queries` charge each) to every provider whose `kinds` serve `query.kind` (or to the 'web' providers when
none does), in parallel (`budget.max_concurrency` threads), skipping providers whose circuit is open. Result lists
are fused with reciprocal rank fusion (k=60) across providers, engines and queries, and duplicates are merged by
canonical URL. Every attempt — success, failure, empty answer or skipped circuit — is returned, never swallowed.

Circuit breaker (per provider, and per engine inside open-webSearch): `failure_threshold` consecutive failures open
the circuit for `cooldown` seconds; after that one trial call is allowed (half_open); success closes it, failure
re-opens it.

Empty-answer rule (defined here; ARCHITECTURE §4.2 leaves it open): an empty result list is a *soft* failure.
(Only a general provider's empty answer is listed in `failures`; every empty answer stays in `attempts`.)
It counts as a health failure only for a provider that declares `general_web = True` (a general search engine:
open-webSearch), only for a general query (kind 'web' or 'news'), and only when some other provider answered the
same query with results — the "engine answered 200 with nothing" failure mode. When nobody found anything the query is simply obscure and the
empty answer is health-neutral. For specialist providers (Wikipedia, arXiv, PyPI, ...) an empty answer means
"nothing matches" (a registry's 404 for a name it does not know: a soft miss) and counts as a success; it is not a
failure. A specialist query nobody answered falls back to the general web providers, except a registry lookup
(REGISTRY_QUERY): "npm:numpy" is not a web search. Provider errors whose code is not about the provider's health
(budget exhausted, network disabled, blocked by policy) are recorded as failed attempts but never move a breaker.
Neither do failures caused by our own politeness: a `rate_limited` error that carries a `retry_after` (a host
backoff still in force after a 429/503, a Crawl-delay slot beyond the timeout, a 429 with Retry-After) is us
waiting as asked, not the provider failing; counting it would hold the circuit open long after the backoff ends.

Providers may also expose:
  `accepts(query) -> bool`   the provider does not handle this query form (e.g. PyPI and a sentence); not called
  `general_web: bool`        see the empty-answer rule
  `last_attempts`            per-engine attempt dicts of the calling thread's most recent search (open-webSearch)
  `engine_snapshot() / load_engine_state(engine, data) / record_engine(engine, ok, latency, error)`
  `fallback_for: str`        name of a primary provider (open-webSearch -> "searxng", ADR-034): when the primary
                             is a candidate for the same query and its circuit is not open, this provider waits and
                             runs only if the primary failed or found nothing
"""
from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import html
import re
import statistics
import threading
import time
from typing import Protocol, runtime_checkable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..instance.errors import ValidationError
from .types import BudgetExceeded, FetchError, Query, QueryPlan, SearchOutcome, SearchResult

RRF_K = 60
HEALTH_WINDOW = 6 * 3600.0   # "healthy" needs a successful call at most this old (T1-11)
GENERAL_KINDS = ("web", "news")
# Error codes that say nothing about a provider's health: never move a breaker.
NON_HEALTH_CODES = frozenset({"budget_exhausted", "disabled", "blocked_policy", "blocked_robots", "circuit_open",
                              "not_applicable"})
# Registry lookups ("pypi:numpy", "osv:PyPI:x@1.0") are never re-sent to general web search when nobody answers.
REGISTRY_QUERY = re.compile(r"^(?:pypi|npm|osv|github):\S", re.I)
_USAGE_LOCK = threading.Lock()   # Usage.charge is not atomic; discovery charges go through this lock


class ProviderError(Exception):
    """A provider could not answer. `retryable` False means retrying the same request is pointless."""

    def __init__(self, provider: str, detail: str, *, retryable: bool = True, code: str = "provider_error",
                 retry_after: float | None = None):
        super().__init__(f"{provider}: {code}: {detail}")
        self.provider, self.detail, self.retryable, self.code = provider, detail, retryable, code
        self.retry_after = retry_after

    def to_json(self) -> dict:
        return {"provider": self.provider, "code": self.code, "detail": self.detail, "retryable": self.retryable}


@runtime_checkable
class SearchProvider(Protocol):
    name: str
    kinds: frozenset

    def search(self, query: Query, limit: int, *, budget, usage) -> list[SearchResult]:
        ...


def charge(budget, usage, **amounts) -> None:
    """Thread-safe `usage.charge`; a no-op without a budget/usage pair. Raises BudgetExceeded."""
    if budget is None or usage is None:
        return
    with _USAGE_LOCK:
        usage.charge(budget, **amounts)


# ---- small shared helpers ------------------------------------------------------------------------------------

_TAG = re.compile(r"</?[A-Za-z!][^<>]*>")   # tag-shaped only: "a < b > c" survives
_WS = re.compile(r"\s+")


def strip_html(text) -> str:
    """Untrusted HTML fragment -> plain text: tags dropped, entities unescaped, whitespace collapsed."""
    if not text:
        return ""
    text = _TAG.sub(" ", str(text))
    text = html.unescape(text)
    text = _TAG.sub(" ", text)          # entity-encoded tags (&lt;b&gt;) become tags after unescaping
    return _WS.sub(" ", text).strip()


_ISO = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?\s*"
                  r"(Z|[+-]\d{2}:?\d{2})?\s*$")


def parse_iso_date(value) -> float | None:
    """ISO-8601 date or datetime (UTC when no offset) -> Unix seconds; None when absent or malformed."""
    if not value or not isinstance(value, str):
        return None
    m = _ISO.match(value)
    if not m:
        return None
    y, mo, d, hh, mm, ss, tz = m.groups()
    try:
        dt = datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), int(ss or 0), tzinfo=timezone.utc)
    except ValueError:
        return None
    ts = dt.timestamp()
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        digits = tz[1:].replace(":", "")
        ts -= sign * (int(digits[:2]) * 3600 + int(digits[2:]) * 60)
    return ts


def date_string(ts: float) -> str:
    """Unix seconds -> YYYY-MM-DD (UTC)."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


_TRACKING = re.compile(r"^(utm_.*|fbclid|gclid)$", re.I)


def canonical_url(url: str) -> str:
    """Deterministic dedupe key: lower-case scheme and host, no 'www.', no default port, no user info, no fragment,
    no tracking parameters (utm_*, fbclid, gclid), sorted parameters, no trailing slash."""
    try:
        parts = urlsplit(str(url).strip())
        port = parts.port
    except ValueError:
        return str(url).strip()
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if ":" in host:
        host = f"[{host}]"
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    params = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING.match(k))
    path = parts.path.rstrip("/")
    return urlunsplit((scheme, host, path, urlencode(params), ""))


_canonical_impl = None


def _canonical(url: str) -> str:
    """extract.canonical_url when the extraction module is installed (one definition across the pipeline)."""
    global _canonical_impl
    if _canonical_impl is None:
        try:
            from .extract import canonical_url as impl   # optional; heavy parser imports stay lazy
        except Exception:
            impl = canonical_url
        _canonical_impl = impl
    try:
        return _canonical_impl(url)
    except Exception:
        return canonical_url(url)


# ---- circuit breaker -----------------------------------------------------------------------------------------

class Breaker:
    """Health of one provider or engine. Not locked: the owner holds its own lock around every call."""

    def __init__(self, *, failure_threshold: int = 3, cooldown: float = 300.0):
        self.failure_threshold, self.cooldown = max(1, int(failure_threshold)), float(cooldown)
        self.state = "closed"
        self.consecutive_failures = 0
        self.successes = 0
        self.failures = 0
        self.last_error: str | None = None
        self.last_ok_at: float | None = None
        self.last_failure_at: float | None = None
        self.opened_at: float | None = None
        self.trial_in_flight = False
        self.latencies: deque = deque(maxlen=100)

    def allow(self, now: float) -> bool:
        """May a call start now? Moves open -> half_open after the cooldown and reserves the single trial."""
        if self.state == "open":
            if self.opened_at is not None and now - self.opened_at >= self.cooldown:
                self.state = "half_open"
                self.trial_in_flight = False
            else:
                return False
        if self.state == "half_open":
            if self.trial_in_flight:
                return False
            self.trial_in_flight = True
        return True

    def success(self, latency: float, now: float) -> None:
        self.successes += 1
        self.consecutive_failures = 0
        self.last_ok_at = now
        self.latencies.append(float(latency))
        self.state, self.opened_at, self.trial_in_flight = "closed", None, False

    def failure(self, error: str | None, latency: float, now: float) -> None:
        self.failures += 1
        self.consecutive_failures += 1
        self.last_error = (error or "failure")[:300]
        self.last_failure_at = now
        self.latencies.append(float(latency))
        if self.state == "half_open" or self.consecutive_failures >= self.failure_threshold:
            self.state, self.opened_at = "open", now
        self.trial_in_flight = False

    def neutral(self, latency: float | None = None) -> None:
        """A call that says nothing about health (or never ran): releases a half-open trial reservation."""
        if latency is not None:
            self.latencies.append(float(latency))
        self.trial_in_flight = False

    def view_state(self, now: float) -> str:
        if self.state == "open" and self.opened_at is not None and now - self.opened_at >= self.cooldown:
            return "half_open"
        return self.state

    def p50(self) -> float | None:
        return round(statistics.median(self.latencies), 4) if self.latencies else None

    def health(self, now: float) -> str:
        """What the owner is shown (T1-11; the circuit `state` alone said "closed" = healthy while SearXNG was down,
        because the circuit opens only after `failure_threshold` consecutive failures):
        'unused' (never called), 'down' (circuit open, or cooling down before a trial), 'failing' (the most recent
        call failed), 'unchecked' (the last success is older than HEALTH_WINDOW), else 'healthy'."""
        state = self.view_state(now)
        if not (self.successes or self.failures):
            return "unused"
        if state in ("open", "half_open"):
            return "down"
        if self.last_failure_at is not None and (self.last_ok_at is None or self.last_failure_at >= self.last_ok_at):
            return "failing"
        if self.last_ok_at is None or now - self.last_ok_at > HEALTH_WINDOW:
            return "unchecked"
        return "healthy"

    def snapshot(self, now: float) -> dict:
        return {"state": self.view_state(now), "health": self.health(now),
                "since_ok": None if self.last_ok_at is None else round(max(0.0, now - self.last_ok_at), 1),
                "successes": self.successes, "failures": self.failures,
                "consecutive_failures": self.consecutive_failures, "last_error": self.last_error,
                "last_ok_at": self.last_ok_at, "last_failure_at": self.last_failure_at, "opened_at": self.opened_at,
                "p50_latency": self.p50(), "calls": len(self.latencies)}

    def to_json(self) -> dict:
        return {"state": self.state, "successes": self.successes, "failures": self.failures,
                "consecutive_failures": self.consecutive_failures, "last_error": self.last_error,
                "last_ok_at": self.last_ok_at, "last_failure_at": self.last_failure_at, "opened_at": self.opened_at,
                "latencies": [round(x, 4) for x in self.latencies]}

    def load(self, data: dict) -> "Breaker":
        state = data.get("state")
        self.state = state if state in ("closed", "open", "half_open") else "closed"
        for name in ("successes", "failures", "consecutive_failures"):
            value = data.get(name)
            setattr(self, name, max(0, int(value)) if isinstance(value, (int, float)) else 0)
        for name in ("last_ok_at", "last_failure_at", "opened_at"):
            value = data.get(name)
            setattr(self, name, float(value) if isinstance(value, (int, float)) else None)
        self.last_error = str(data["last_error"])[:300] if data.get("last_error") else None
        if self.state == "open" and self.opened_at is None:
            self.state = "closed"
        self.trial_in_flight = False
        self.latencies = deque((float(x) for x in data.get("latencies") or () if isinstance(x, (int, float))),
                               maxlen=100)
        return self


# ---- registry ------------------------------------------------------------------------------------------------

def _error_code(exc: BaseException) -> tuple[str, str]:
    if isinstance(exc, ProviderError):
        return exc.code, exc.detail
    if isinstance(exc, BudgetExceeded):
        return "budget_exhausted", str(exc)
    if isinstance(exc, FetchError):
        return exc.code, exc.detail or str(exc)
    return "provider_error", f"{type(exc).__name__}: {exc}"


def politeness_failure(exc: BaseException) -> bool:
    """Did the call fail only because we are honouring a rate limit (backoff in force, Retry-After, Crawl-delay)?"""
    return (isinstance(exc, (ProviderError, FetchError)) and exc.code == "rate_limited"
            and getattr(exc, "retry_after", None) is not None)


class ProviderRegistry:
    """Fan-out over search providers with health tracking. Thread-safe."""

    def __init__(self, providers: list, *, clock=time.time, failure_threshold: int = 3, cooldown: float = 300.0):
        names = [p.name for p in providers]
        if len(set(names)) != len(names):
            raise ValidationError(f"duplicate provider names: {names}")
        self.providers = list(providers)
        self._by_name = {p.name: p for p in self.providers}
        self._clock = clock
        self.failure_threshold, self.cooldown = failure_threshold, cooldown
        self._lock = threading.RLock()
        self._health = {p.name: Breaker(failure_threshold=failure_threshold, cooldown=cooldown) for p in providers}
        self._orphans: dict[str, dict] = {}   # persisted state of providers/engines not configured now

    # -- health -------------------------------------------------------------------------------------------------
    def record(self, name: str, ok: bool, latency: float, error: str | None = None) -> None:
        """Record one call outcome. 'provider:engine' names go to that provider's engine breakers."""
        now = self._clock()
        if ":" in name:
            prov, engine = name.split(":", 1)
            provider = self._by_name.get(prov)
            if provider is not None and hasattr(provider, "record_engine"):
                provider.record_engine(engine, ok, latency, error)
                return
        with self._lock:
            breaker = self._health.get(name)
            if breaker is None:
                breaker = Breaker(failure_threshold=self.failure_threshold, cooldown=self.cooldown)
                if name in self._orphans:
                    breaker.load(self._orphans.pop(name))
                self._health[name] = breaker
            if ok:
                breaker.success(latency, now)
            else:
                breaker.failure(error, latency, now)

    def _allow(self, name: str) -> bool:
        with self._lock:
            return self._health[name].allow(self._clock())

    def _settle(self, name: str, verdict: str, latency: float, error: str | None) -> None:
        now = self._clock()
        with self._lock:
            breaker = self._health[name]
            if verdict == "ok":
                breaker.success(latency, now)
            elif verdict == "fail":
                breaker.failure(error, latency, now)
            else:
                breaker.neutral(latency)

    def status(self) -> list[dict]:
        now = self._clock()
        out = []
        with self._lock:
            for name, breaker in self._health.items():
                out.append({"name": name, "provider": name, "engine": None, **breaker.snapshot(now)})
                provider = self._by_name.get(name)
                if provider is not None and hasattr(provider, "engine_snapshot"):
                    for engine, snap in provider.engine_snapshot().items():
                        out.append({"name": f"{name}:{engine}", "provider": name, "engine": engine, **snap})
            for name, data in sorted(self._orphans.items()):
                snap = Breaker(failure_threshold=self.failure_threshold, cooldown=self.cooldown).load(data)
                prov, _, engine = name.partition(":")
                out.append({"name": name, "provider": prov, "engine": engine or None, "configured": False,
                            **snap.snapshot(now)})
        return out

    def dump_state(self) -> dict:
        with self._lock:
            health = {name: breaker.to_json() for name, breaker in self._health.items()}
            for name, provider in self._by_name.items():
                if hasattr(provider, "dump_engines"):
                    for engine, data in provider.dump_engines().items():
                        health[f"{name}:{engine}"] = data
            for name, data in self._orphans.items():
                health.setdefault(name, data)
        return {"version": 1, "health": health}

    def load_state(self, state: dict) -> None:
        health = (state or {}).get("health") or {}
        with self._lock:
            for name, data in health.items():
                if not isinstance(data, dict):
                    continue
                if name in self._health:
                    self._health[name] = Breaker(failure_threshold=self.failure_threshold,
                                                 cooldown=self.cooldown).load(data)
                    continue
                prov, _, engine = name.partition(":")
                provider = self._by_name.get(prov)
                if engine and provider is not None and hasattr(provider, "load_engine_state"):
                    provider.load_engine_state(engine, data)
                else:
                    self._orphans[name] = dict(data)

    # -- search -------------------------------------------------------------------------------------------------
    def _candidates(self, query: Query, exclude=()) -> list:
        def serves(p, kind):
            if kind not in p.kinds or p.name in exclude:
                return False
            accepts = getattr(p, "accepts", None)
            return accepts is None or bool(accepts(query))
        chosen = [p for p in self.providers if serves(p, query.kind)]
        if not chosen and query.kind != "web":
            chosen = [p for p in self.providers if serves(p, "web")]
        return chosen

    def _deferred(self, providers) -> list:
        """Fallback providers (`fallback_for`) whose primary is among `providers` with a circuit that is not open."""
        names = {p.name for p in providers}
        now = self._clock()
        out = []
        with self._lock:
            for p in providers:
                primary = getattr(p, "fallback_for", None)
                if primary in names and primary in self._health and self._health[primary].view_state(now) != "open":
                    out.append(p)
        return out

    def _call(self, provider, query: Query, limit: int, budget, usage) -> dict:
        started = time.perf_counter()
        out = {"provider": provider.name, "results": [], "error": None, "code": None, "engine_attempts": [],
               "polite": False}
        try:
            results = provider.search(query, limit, budget=budget, usage=usage)
            out["results"] = [r for r in (results or []) if isinstance(r, SearchResult)][:limit]
        except Exception as exc:   # every failure is recorded on the outcome, never raised past the registry
            out["code"], out["error"] = _error_code(exc)
            out["polite"] = politeness_failure(exc)
        out["latency"] = round(time.perf_counter() - started, 4)
        out["engine_attempts"] = list(getattr(provider, "last_attempts", None) or [])
        return out

    def _round(self, pool, providers, query, limit, budget, usage, attempts, failures, lists, qi) -> list[dict]:
        runnable = []
        for provider in providers:
            if self._allow(provider.name):
                runnable.append(provider)
            else:
                entry = {"provider": provider.name, "engine": None, "query": query.text, "ok": False, "n": 0,
                         "error": "circuit_open", "latency": 0.0}
                attempts.append(entry)
                failures.append(entry)
        calls = [pool.submit(self._call, p, query, limit, budget, usage) for p in runnable]
        done = [f.result() for f in calls]
        anyone_found = any(d["results"] for d in done)
        for provider, d in zip(runnable, done):
            general = getattr(provider, "general_web", False)
            for ea in d["engine_attempts"]:
                entry = {"provider": provider.name, "engine": ea.get("engine"), "query": query.text,
                         "ok": bool(ea.get("ok")), "n": int(ea.get("n") or 0), "error": ea.get("error"),
                         "latency": float(ea.get("latency") or 0.0)}
                attempts.append(entry)
                if not entry["ok"] or (entry["n"] == 0 and general):
                    failures.append(entry)
            n = len(d["results"])
            error = f"{d['code']}: {d['error']}" if d["code"] else (None if n else "empty")
            entry = {"provider": provider.name, "engine": None, "query": query.text, "ok": d["code"] is None,
                     "n": n, "error": error, "latency": d["latency"]}
            attempts.append(entry)
            if d["code"] is not None:
                failures.append(entry)
                verdict = "neutral" if d["code"] in NON_HEALTH_CODES or d["polite"] else "fail"
            elif n:
                verdict = "ok"
            else:
                if general:            # a specialist's empty answer ("no such package": 404) is a soft miss
                    failures.append(entry)
                soft = general and query.kind in GENERAL_KINDS and anyone_found
                verdict = "fail" if soft else ("neutral" if general else "ok")
            self._settle(provider.name, verdict, d["latency"], error)
            if d["results"]:
                by_engine: dict = {}
                for r in d["results"]:
                    by_engine.setdefault(r.engine, []).append(r)
                for engine, group in by_engine.items():
                    lists.append((qi, provider.name, engine, group))
        return done

    def search(self, plan: QueryPlan, *, budget, usage, per_query: int = 8, sent=None) -> SearchOutcome:
        """`sent`, when given, is called after each query is charged and before it is dispatched (the orchestrator
        persists the usage there, so a worker killed mid-search leaves every search it sent counted: T5-07)."""
        per_query = max(1, min(int(per_query), 20))
        attempts: list[dict] = []
        failures: list[dict] = []
        lists: list[tuple] = []
        queries = list(plan.queries)
        cap = max(0, int(budget.max_queries))
        if len(queries) > cap:
            failures.append({"provider": None, "engine": None, "query": None, "ok": False, "n": 0,
                             "error": f"budget_exhausted: {len(queries) - cap} planned queries not sent "
                                      f"(max_queries={cap})", "latency": 0.0})
            queries = queries[:cap]
        started = self._clock()
        with ThreadPoolExecutor(max_workers=max(1, int(budget.max_concurrency)),
                                thread_name_prefix="webcortex-search") as pool:
            for qi, query in enumerate(queries):
                if self._clock() - started > budget.max_seconds:
                    failures.append({"provider": None, "engine": None, "query": query.text, "ok": False, "n": 0,
                                     "error": "budget_exhausted: seconds", "latency": 0.0})
                    break
                try:
                    charge(budget, usage, queries=1)
                except BudgetExceeded as exc:
                    failures.append({"provider": None, "engine": None, "query": query.text, "ok": False, "n": 0,
                                     "error": f"budget_exhausted: {exc}", "latency": 0.0})
                    break
                if sent is not None:
                    sent()
                providers = self._candidates(query)
                if not providers:
                    failures.append({"provider": None, "engine": None, "query": query.text, "ok": False, "n": 0,
                                     "error": f"no provider serves kind {query.kind!r}", "latency": 0.0})
                    continue
                deferred = self._deferred(providers)
                done = self._round(pool, [p for p in providers if p not in deferred], query, per_query, budget,
                                   usage, attempts, failures, lists, qi)
                if deferred:
                    primaries = {p.fallback_for for p in deferred}
                    if not any(d["results"] for d in done if d["provider"] in primaries):
                        done += self._round(pool, deferred, query, per_query, budget, usage, attempts, failures,
                                            lists, qi)
                if query.kind != "web" and not any(d["results"] for d in done) and not REGISTRY_QUERY.match(query.text):
                    # fallback: specialist providers produced nothing usable -> general web providers
                    fallback = [p for p in self._candidates(Query(query.text, "web", query.since, query.site,
                                                                  query.language, query.purpose),
                                                            exclude={p.name for p in providers})]
                    if fallback:
                        self._round(pool, fallback, query, per_query, budget, usage, attempts, failures, lists, qi)
        return SearchOutcome(results=fuse(lists), attempts=attempts, failures=failures)


def fuse(lists: list[tuple]) -> list[SearchResult]:
    """Reciprocal rank fusion (k=60) over ranked lists, merging duplicates by canonical URL.

    `lists` holds (query_index, provider, engine, [SearchResult in rank order]). The representative of a merged
    group is the copy with the richest structured metadata (ties: best single-list rank, then first seen); missing
    fields are filled from the others, metadata is merged (representative wins) and `metadata['found_by']` lists
    every provider[:engine] that returned it. Rank and popularity order results; they are never evidence of truth.
    """
    groups: dict[str, dict] = {}
    order = 0
    for _qi, provider, engine, results in lists:
        for position, result in enumerate(results, start=1):
            key = _canonical(result.url)
            contribution = 1.0 / (RRF_K + position)
            group = groups.get(key)
            if group is None:
                group = groups[key] = {"score": 0.0, "items": [], "found_by": [], "first": order}
                order += 1
            group["score"] += contribution
            group["items"].append((len(result.metadata or {}), -position, -len(group["items"]), result))
            tag = f"{provider}:{engine}" if engine else provider
            if tag not in group["found_by"]:
                group["found_by"].append(tag)
    fused = []
    for key, group in groups.items():
        items = sorted(group["items"], key=lambda t: (t[0], t[1], t[2]), reverse=True)
        rep = items[0][3]
        others = [t[3] for t in items[1:]]
        metadata = {}
        for other in reversed(others):
            metadata.update(other.metadata or {})
        metadata.update(rep.metadata or {})
        metadata["found_by"] = list(group["found_by"])
        title = rep.title or next((o.title for o in others if o.title), "")
        snippet = rep.snippet or max((o.snippet for o in others), key=len, default="")
        published = rep.published_at if rep.published_at is not None else next(
            (o.published_at for o in others if o.published_at is not None), None)
        fused.append((round(group["score"], 8), group["first"],
                      SearchResult(url=rep.url, title=title, snippet=snippet, provider=rep.provider,
                                   engine=rep.engine, rank=rep.rank, kind=rep.kind, published_at=published,
                                   metadata=metadata, score=round(group["score"], 8))))
    fused.sort(key=lambda t: (-t[0], t[1]))
    return [r for _, _, r in fused]


__all__ = ["SearchProvider", "ProviderError", "ProviderRegistry", "Breaker", "canonical_url", "fuse", "charge",
           "strip_html", "parse_iso_date", "date_string", "RRF_K", "NON_HEALTH_CODES", "REGISTRY_QUERY"]
