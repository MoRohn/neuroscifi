"""Brave Search API: the primary general discovery provider when the owner has configured a key (ADR-034
amendment 1). Without a key it is not built and discovery is exactly as before.

Official documentation (read 2026-11-21):
  Web search   https://api-dashboard.search.brave.com/api-reference/web/search/get
  News search  https://api-dashboard.search.brave.com/api-reference/news/news_search/get
  Rate limits  https://api-dashboard.search.brave.com/documentation/guides/rate-limiting
  Terms        https://api-dashboard.search.brave.com/terms-of-service  (Search API Terms of Use, updated 1 Sep 2026)
  Plans        https://brave.com/search/api/

Requests: `GET https://api.search.brave.com/res/v1/web/search` (kind 'web') or `.../res/v1/news/search` (kind
'news'), header `X-Subscription-Token: <key>`, through SafeFetcher purpose='api' (address policy, honest
User-Agent, budget charge, never cached on disk, credential header dropped on a cross-origin redirect). Parameters
sent: q (web at most 600 characters and 75 words, news 400 and 50), count = the registry's per-query limit (at most
20), safesearch=off (as SearXNG), text_decorations=false, spellcheck=false (the planned query is sent as planned),
result_filter=web on the web endpoint, and freshness pd/pw/pm/py from the plan's recency window
(intent.searxng_route). `site:` is a documented query operator and is appended like SearXNG does.

Responses: `web.results[]` (web) or `results[]` (news), each with title, url, description, age (human-readable,
"2 days ago"), page_age ("The page's date, based on its published or last modified date"), page_fetched,
meta_url, language and, for news, breaking. Normalisation as SearXNG: provider 'brave', engine 'web' or 'news'
(also `metadata.engines`), title/snippet stripped of HTML, `published_at` only from a parseable ISO-8601
`page_age` (never inferred from `age`, which is kept as text), `metadata.date_basis` = 'page_age (published or
last modified)', `metadata.retrieved_at`, `metadata.canonical_url`, `metadata.transient` = True. Results are
untrusted data and discovery hints only: the page itself is read from its origin.

Rate limits: every response carries X-RateLimit-Limit / -Policy / -Remaining / -Reset as comma lists, one entry per
window (e.g. remaining "1, 1000", policy "1;w=1, 15000;w=2592000"); the limit is a 1-second sliding window and an
excess request gets HTTP 429; only successful requests are billed. SafeFetcher spaces requests to the API host by
min_domain_interval (1 s by default, the lowest documented per-second limit), and a 429 sets its host backoff
(Retry-After when sent, else 60 s). A window longer than a second with nothing remaining (the monthly quota) blocks
the provider until its X-RateLimit-Reset. Either is `rate_limited` with `retry_after`: health-neutral, and the
registry's fallback (SearXNG) answers instead.

Daily cap: `daily_provider_queries['brave']` (WebConfig, default DEFAULT_DAILY_CAP) queries per UTC day, counted per
key in keys.Ledger before a request is sent and returned only when Brave cannot have billed it (Brave bills only
successful requests: `_unbilled`), so every origin (owner, Pocket, MCP, monitors and autonomy) draws from one cap. 0 switches the provider
off without removing the key. Reaching it is `daily_cap` (health-neutral).

Terms: §3(b)(i) forbids storing, caching or creating a database of Search Results "other than transient storage
required for operation"; §3(b)(xiii) forbids using them to train, evaluate or benchmark AI models; §4(a) the key
must be kept secure. Hence: an in-memory cache of CACHE_TTL seconds only, `metadata.transient` on every result (the
orchestrator drops such results from a task's checkpoint when the task ends), and the benchmark never uses Brave.
"""
from __future__ import annotations

import json
import threading
import time
from urllib.parse import urlencode

from ..config import DEFAULT_PROVIDER_DAILY_QUERIES
from ..search import REGISTRY_QUERY, ProviderError, _canonical, parse_iso_date, strip_html
from ..types import BudgetExceeded, FetchError, Query, SearchResult
from . import keys
from .openwebsearch import _clean_url

BASE = "https://api.search.brave.com/res/v1"
PATHS = {"web": "/web/search", "news": "/news/search"}
ENDPOINTS = {engine: BASE + path for engine, path in PATHS.items()}
QUERY_LIMITS = {"web": (600, 75), "news": (400, 50)}      # characters, words (documented maxima)
FRESHNESS = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}
DEFAULT_DAILY_CAP = DEFAULT_PROVIDER_DAILY_QUERIES["brave"]
MAX_LIMIT = 20
CACHE_TTL = 120.0
CACHE_MAX = 64
DATE_BASIS = "page_age (published or last modified)"
_STATUS_CODES = {401: "key_rejected", 403: "forbidden", 404: "http_error", 422: "bad_request"}
# Observed 2026-11-22: an unknown key is answered 422, not 401 (SafeFetcher does not return error bodies).
_STATUS_HINTS = {401: "the key was refused", 403: "the plan does not include this endpoint",
                 422: "an invalid key or request; Brave answers an unknown key with 422"}


def _numbers(value) -> list[float]:
    out = []
    for part in str(value or "").split(","):
        part = part.strip().split(";")[0].strip()
        try:
            out.append(float(part))
        except ValueError:
            return []
    return out


def _windows(value) -> list[float]:
    out = []
    for part in str(value or "").split(","):
        found = [p.strip()[2:] for p in part.split(";") if p.strip().startswith("w=")]
        try:
            out.append(float(found[0]) if found else 0.0)
        except ValueError:
            out.append(0.0)
    return out


def rate_limit(headers: dict) -> dict:
    """X-RateLimit-* response headers -> {'windows': [{window, limit, remaining, reset}], 'exhausted': the longest
    window (> 1 s) with nothing remaining, or None}. Missing or malformed headers -> no windows."""
    low = {str(k).lower(): v for k, v in (headers or {}).items()}
    limits = _numbers(low.get("x-ratelimit-limit"))
    remaining = _numbers(low.get("x-ratelimit-remaining"))
    resets = _numbers(low.get("x-ratelimit-reset"))
    policy = _windows(low.get("x-ratelimit-policy"))
    windows = []
    for i, left in enumerate(remaining):
        windows.append({"window": policy[i] if i < len(policy) else None,
                        "limit": limits[i] if i < len(limits) else None, "remaining": left,
                        "reset": resets[i] if i < len(resets) else None})
    exhausted = [w for w in windows if w["remaining"] <= 0 and (w["reset"] or 0) > 1.0]
    return {"windows": windows, "exhausted": max(exhausted, key=lambda w: w["reset"]) if exhausted else None}


def _unbilled(exc) -> bool:
    """Can Brave have counted this request? Only successful requests are billed: an error status, or a refusal
    before anything was sent, was not; a failure after a 2xx status line (body too large or cut off, the byte
    budget) or an ambiguous timeout may have been, and keeps its count (erring towards the cap)."""
    if isinstance(exc, BudgetExceeded):
        return exc.resource != "bytes"            # the request charge comes before sending; bytes while reading
    if exc.status is not None:
        return not 200 <= exc.status < 300
    return exc.code in ("blocked_policy", "dns_failure", "connect_failed", "tls_error", "rate_limited")


class BraveProvider:
    """`BASE` (class attribute) is the API origin; tests point it at FixtureWeb over plain HTTP."""
    name = "brave"
    kinds = frozenset({"web", "news"})
    general_web = True
    BASE = BASE

    def __init__(self, fetcher, *, daily_cap: int = DEFAULT_DAILY_CAP, clock=time.time):
        self.fetcher = fetcher
        self.daily_cap = max(0, int(daily_cap))
        self._clock = clock
        self.ledger = keys.Ledger(self.name, clock=clock)
        self._lock = threading.Lock()
        self._cache: dict[tuple, tuple[float, dict, float]] = {}

    def accepts(self, query: Query) -> bool:
        return not REGISTRY_QUERY.match(str(query.text))   # "pypi:numpy" belongs to the registries

    def params(self, query: Query, engine: str, limit: int) -> dict:
        from ..intent import searxng_route
        chars, words = QUERY_LIMITS[engine]
        text = " ".join(str(query.text).split()[:words - (1 if query.site else 0)])
        if query.site:
            text = f"{text} site:{query.site}"
        out = {"q": text[:chars], "count": max(1, min(int(limit), MAX_LIMIT)), "safesearch": "off",
               "spellcheck": "false"}
        if engine == "web":
            out["text_decorations"] = "false"
            out["result_filter"] = "web"
        freshness = FRESHNESS.get(searxng_route(query, now=self._clock()).get("time_range"))
        if freshness:
            out["freshness"] = freshness
        return out

    def _fail(self, code: str, detail: str, *, retry_after: float | None = None, retryable: bool = True):
        return ProviderError(self.name, detail[:300], code=code, retryable=retryable, retry_after=retry_after)

    def search(self, query: Query, limit: int, *, budget=None, usage=None) -> list[SearchResult]:
        engine = "news" if query.kind == "news" else "web"
        limit = max(1, min(int(limit), MAX_LIMIT))
        if not str(query.text).strip():
            return []
        params = self.params(query, engine, limit)
        cache_key = (engine, tuple(sorted(params.items())))
        now = self._clock()
        with self._lock:
            hit = self._cache.get(cache_key)
            if hit and now - hit[0] > CACHE_TTL:
                self._cache.pop(cache_key, None)
                hit = None
        if hit:
            return self.parse(hit[1], engine, query.since, hit[2], cached=True)[:limit]
        if self.daily_cap <= 0:
            raise self._fail("daily_cap", "the daily cap is 0 (provider switched off)", retryable=False)
        limited = self.ledger.limited()
        if limited:
            raise self._fail("rate_limited", f"{limited['reason']}; blocked until reset",
                             retry_after=max(0.0, limited["until"] - now))
        try:
            key = keys.read_key(self.name)
        except keys.KeyProblem as exc:
            raise self._fail(exc.code, exc.detail, retryable=False) from None
        try:
            self.ledger.reserve(self.daily_cap)
        except keys.CapReached as exc:
            raise self._fail("daily_cap", str(exc), retryable=False) from None
        url = f"{self.BASE}{PATHS[engine]}?{urlencode(params)}"
        try:
            result = self.fetcher.fetch(url, headers={"X-Subscription-Token": key, "Accept": "application/json"},
                                        purpose="api", budget=budget, usage=usage)
        except (FetchError, BudgetExceeded) as exc:
            if _unbilled(exc):
                self.ledger.refund()
            raise self._fetch_error(exc, key) from None
        if result.status != 200:
            if not 200 <= result.status < 300:
                self.ledger.refund()
            error = self._status_error(result, key)
            self.ledger.note(ok=False, error=f"{error.code}: {error.detail}")
            raise error
        try:
            payload = json.loads(result.body.decode("utf-8"))
            problem = None if isinstance(payload, dict) else "response is not a JSON object"
        except (ValueError, UnicodeDecodeError) as exc:
            problem = f"unparseable JSON: {exc}"
        if problem:
            self.ledger.note(ok=False, error=f"bad_response: {problem}")
            raise self._fail("bad_response", problem, retryable=False)
        limits = rate_limit(result.headers)
        exhausted = limits["exhausted"]
        self.ledger.note(ok=True, remaining={str(int(w["window"] or 0)): w["remaining"] for w in limits["windows"]},
                         limited_for=exhausted["reset"] if exhausted else None,
                         reason=f"quota window of {int(exhausted['window'] or 0)} s used up" if exhausted else None)
        retrieved_at = self._clock()
        results = self.parse(payload, engine, query.since, retrieved_at)
        if results:
            with self._lock:
                self._cache[cache_key] = (now, payload, retrieved_at)
                while len(self._cache) > CACHE_MAX:
                    self._cache.pop(next(iter(self._cache)))
        return results[:limit]

    def _fetch_error(self, exc, key) -> ProviderError:
        """A SafeFetcher failure as a ProviderError (key scrubbed). Only Brave's own 429 enters the shared ledger as
        a rate limit; SafeFetcher's spacing or a backoff already in force is ours and stays per process."""
        if isinstance(exc, BudgetExceeded):
            return self._fail("budget_exhausted", str(exc), retryable=False)
        detail = keys.scrub(exc.detail or exc, key)
        if exc.status in _STATUS_HINTS:
            detail = f"{detail} ({_STATUS_HINTS[exc.status]})"
        if exc.code == "rate_limited":
            wait = exc.retry_after if exc.retry_after is not None else 60.0
            if exc.status == 429:
                self.ledger.note(ok=False, error=f"rate_limited: {detail}", limited_for=wait,
                                 reason="Brave answered 429")
            return self._fail("rate_limited", detail, retry_after=wait)
        code = _STATUS_CODES.get(exc.status, exc.code) if exc.code == "http_error" else exc.code
        self.ledger.note(ok=False, error=f"{code}: {detail}")
        return self._fail(code, detail, retryable=code in ("timeout", "connect_failed", "dns_failure", "http_error",
                                                           "tls_error"))

    def _status_error(self, result, key) -> ProviderError:
        status = int(result.status)
        detail = ""
        try:
            error = json.loads(result.body.decode("utf-8")).get("error") or {}
            detail = f"{error.get('code') or ''}: {error.get('detail') or ''}".strip(": ")
        except (ValueError, UnicodeDecodeError, AttributeError):
            pass
        code = _STATUS_CODES.get(status, "http_error")
        detail = detail or _STATUS_HINTS.get(status, "")
        return self._fail(code, keys.scrub(f"HTTP {status}" + (f" ({detail})" if detail else ""), key)[:300],
                          retryable=status >= 500)

    def parse(self, payload: dict, engine: str, since, retrieved_at: float, *, cached: bool = False):
        section = payload.get("web") if engine == "web" else payload
        items = section.get("results") if isinstance(section, dict) else None
        out, seen = [], set()
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            url = _clean_url(item.get("url"))
            if url is None or url in seen:
                continue
            published = parse_iso_date(item.get("page_age"))      # absent or unparseable -> None, never guessed
            if since is not None and published is not None and published < since:
                continue
            seen.add(url)
            metadata = {"engines": [engine], "category": engine, "retrieved_at": retrieved_at,
                        "canonical_url": _canonical(url), "transient": True}
            if published is not None:
                metadata["date_basis"] = DATE_BASIS
            for field_name, limit in (("age", 60), ("page_fetched", 40), ("language", 16)):
                value = item.get(field_name)
                if isinstance(value, str) and value.strip():
                    metadata[field_name] = value.strip()[:limit]
            host = (item.get("meta_url") or {}).get("hostname") if isinstance(item.get("meta_url"), dict) else None
            if isinstance(host, str) and host:
                metadata["source"] = host[:253]
            if isinstance(item.get("breaking"), bool):
                metadata["breaking"] = item["breaking"]
            if cached:
                metadata["cache"] = True
            out.append(SearchResult(url=url, title=strip_html(item.get("title"))[:500] or url,
                                    snippet=strip_html(item.get("description"))[:1000], provider=self.name,
                                    engine=engine, rank=len(out) + 1, kind="web", published_at=published,
                                    metadata=metadata))
        return out


def status(*, daily_cap: int = DEFAULT_DAILY_CAP, stored: dict | None = None, clock=time.time) -> dict:
    """What `nsf web provider status`, `nsf web status` and the console show for Brave. No network call; never the
    key. `stored` is the registry breaker snapshot persisted by the last run (procedural memory), when any.

    state: 'unconfigured' (no key file), 'failing' (insecure key file, or the most recent call failed), 'off' (a
    usable key is stored but the daily cap is 0: Brave is switched off, not unconfigured), 'rate_limited' (Brave's own limit in force, or today's cap reached), else 'configured'."""
    now = clock()
    out = {"provider": "brave", **keys.key_state("brave"), "daily_cap": int(daily_cap),
           "endpoints": dict(ENDPOINTS), "docs": "https://api-dashboard.search.brave.com/api-reference/web/search/get"}
    stored = stored or {}
    ledger = keys.Ledger("brave", clock=clock).read() if out["key"] != "missing" else {}
    used = int(ledger.get("queries") or 0)
    out.update({"day": ledger.get("day") or keys.day_of(now), "used_today": used,
                "remaining_today": max(0, int(daily_cap) - used), "last_ok_at": ledger.get("last_ok_at"),
                "last_error": ledger.get("last_error"), "last_error_at": ledger.get("last_error_at"),
                "rate_limit_remaining": ledger.get("remaining")})
    last_ok = out["last_ok_at"]
    out["since_ok"] = None if not isinstance(last_ok, (int, float)) else round(max(0.0, now - last_ok), 1)
    until = ledger.get("rate_limited_until")
    out["rate_limited_until"] = until if isinstance(until, (int, float)) and until > now else None
    if out["key"] == "missing":
        out["state"] = "unconfigured"
    elif out["key"] == "insecure":
        out["state"], out["last_error"] = "failing", out["problem"]
    elif int(daily_cap) <= 0:
        out["state"] = "off"
        out["state_note"] = ("off (cap 0): the key is stored but daily_provider_queries.brave is 0, so no query "
                             "reaches Brave; raise the cap with nsf web config --set "
                             "'daily_provider_queries={\"brave\": 30}'")
    elif out["rate_limited_until"] or used >= int(daily_cap):
        out["state"] = "rate_limited"
        out["rate_limit_reason"] = (ledger.get("rate_limit_reason") if out["rate_limited_until"]
                                    else f"daily cap of {int(daily_cap)} queries reached (resets 00:00 UTC)")
    elif stored.get("health") in ("failing", "down") or (
            isinstance(out["last_error_at"], (int, float)) and out["last_error_at"] >= (last_ok or 0)
            and not str(out["last_error"] or "").startswith("rate_limited")):
        out["state"] = "failing"
    else:
        out["state"] = "configured"
    out["health"] = stored.get("health")
    return out


__all__ = ["BraveProvider", "status", "rate_limit", "BASE", "ENDPOINTS", "DEFAULT_DAILY_CAP"]
