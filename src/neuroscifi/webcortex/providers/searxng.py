"""SearXNG through its loopback JSON API: the primary general discovery provider (ADR-034).

Only `GET /search?format=json`, `GET /config` and `GET /healthz` are called. The base URL must be plain HTTP to a
loopback host (openwebsearch.loopback_base): SearXNG is a trusted local integration, so like the open-webSearch
daemon it bypasses SafeFetcher (which refuses loopback by design), ignores proxy variables and never follows
redirects. The loopback exemption covers exactly the configured `searxng_url`; SafeFetcher's SSRF policy is
unchanged. SearXNG forwards the query text to third-party engines, so queries reach this provider only after the
orchestrator's privacy screen, like every other provider.

One SearXNG call queries the engines of one category (intent.searxng_route): news -> news, software -> it,
scientific -> science, otherwise general. SearXNG answers HTTP 200 even when engines fail; it lists them in
`unresponsive_engines` ([name, reason]). Each engine therefore has its own Breaker (reasons classified as
captcha / forbidden (403) / rate_limited (429) / timeout / error) and its own counters (`engine_stats`). An engine
that answered nothing is health-neutral (metasearch engines cover different ground); only an engine SearXNG reports
unresponsive counts as failed. Engines whose circuit is open are left out of the request (`engines=`); when every
engine of the category is open the call raises `circuit_open`. When every queried engine failed, `engines_failed`.

Budget: one SearXNG call costs one `usage.requests` per upstream engine it queries (the true upstream load).
Results are cached in memory for CACHE_TTL seconds per exact request (a cache hit costs nothing and is marked
`metadata.cache`). The HTTP timeout adapts to observed latency (MIN_TIMEOUT..MAX_TIMEOUT); a 5xx or a reset
connection is retried once; a 429 is reported as `rate_limited` with its Retry-After, never retried.

Normalisation (SearchResult): title and snippet (`content`) stripped of HTML; `engine` = the engine that ranked
it first, `metadata.engines` = every engine that returned it; `published_at` only when SearXNG sends a parseable
`publishedDate` (never inferred); `metadata.retrieved_at` = when NeuroSciFi received it; `metadata.canonical_url`;
`metadata.searxng_score`; `kind` 'academic' for the science category, else 'web'. Results are untrusted data.

`health()` says "healthy" only when SearXNG answers AND some search returned real results within HEALTHY_WINDOW;
an instance that only answers /healthz is "degraded" (see `verdict`).

`Container` runs the pinned image (IMAGE, by digest) with docker: loopback-only port, read-only root filesystem,
no capabilities, no-new-privileges, unprivileged user, memory/CPU/pid limits, a healthcheck, bounded JSON logs and
`--restart unless-stopped`. Its secret is generated once into the config directory (0600, outside git). The image
impersonates a Chrome browser by default (curl_cffi TLS fingerprint and headers, see `honest_client`); `Container`
mounts a patched copy of that one file and an honest `useragents.json`, verified by sha256, so engines receive the
SearXNG User-Agent and no browser fingerprint (ADR-032's rule, ADR-034).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
import urllib.request

from ...instance.errors import ValidationError
from ..search import REGISTRY_QUERY, Breaker, ProviderError, _canonical, charge, parse_iso_date, strip_html
from ..types import BudgetExceeded, Query, SearchResult
from .openwebsearch import _OPENER, NotLoopback, _clean_url, _is_loopback_host, _port_open, loopback_base

DEFAULT_PORT = 8888
DEFAULT_URL = f"http://127.0.0.1:{DEFAULT_PORT}"
IMAGE_DIGEST = "sha256:fc1d81d7e9d04d3c8b3e23c7162feddf5c3d6aecd32e67a56eaabcb8d8747f79"
IMAGE = f"searxng/searxng@{IMAGE_DIGEST}"
IMAGE_VERSION = "2026.11.21-c6ee3e9fd"
VERSION_TAG = "2026.11.21+c6ee3e9fd"      # what SearXNG reports (/config, its own User-Agent)
CONTAINER_NAME = "neuroscifi-searxng"
CONTAINER_USER = "977:977"          # the image's 'searxng' user
CATEGORIES = ("general", "news", "it", "science")
TIME_RANGES = ("day", "week", "month", "year")
MAX_QUERY_CHARS = 512
MAX_LIMIT = 20
MAX_RESPONSE_BYTES = 4_000_000
CACHE_TTL = 120.0
CACHE_MAX = 128
HEALTHY_WINDOW = 6 * 3600.0         # "healthy" needs a non-empty real answer at most this old
CONFIG_TTL = 300.0
MIN_TIMEOUT, DEFAULT_TIMEOUT, MAX_TIMEOUT = 6.0, 12.0, 20.0
DEFAULT_TEMPLATE = Path(__file__).resolve().parents[4] / "deploy" / "searxng" / "settings.yml"
CLIENT_PATH = "/usr/local/searxng/searx/network/client.py"
CLIENT_SHA256 = "ff720490c36bf378d5ea9132c050f236b5e83075e59fb251422e72ef234220e4"   # pristine, IMAGE_VERSION
USERAGENTS_PATH = "/usr/local/searxng/searx/data/useragents.json"
PYCACHE_PATH = "/usr/local/searxng/searx/network/__pycache__"
HONEST_SUFFIX = "NeuroSciFi-WebCortex (self-hosted research agent; loopback-only instance)"
HONEST_UA = f"SearXNG/{VERSION_TAG} {HONEST_SUFFIX}"
PATCH_MARKER = "neuroscifi-honest-ua.json"
_REASONS = (("captcha", "captcha"), ("too many requests", "rate_limited"), ("429", "rate_limited"),
            ("access denied", "forbidden"), ("403", "forbidden"), ("timeout", "timeout"))
STAT_KEYS = ("calls", "success", "empty", "error", "captcha", "forbidden", "rate_limited", "timeout", "results")


def classify(reason) -> str:
    """SearXNG unresponsive-engine reason -> captcha | rate_limited | forbidden | timeout | error."""
    low = str(reason or "").lower()
    return next((kind for needle, kind in _REASONS if needle in low), "error")


def verdict(reachable: bool, last_ok_at: float | None, now: float, *, engines_open: bool = False) -> str:
    """'healthy' = answers AND returned real results within HEALTHY_WINDOW (and not every engine is open);
    'degraded' = answers but no recent non-empty result; 'down' = does not answer. HTTP 200 alone is not health."""
    if not reachable:
        return "down"
    if last_ok_at is None or now - float(last_ok_at) > HEALTHY_WINDOW or engines_open:
        return "degraded"
    return "healthy"


def _http(url: str, timeout: float) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            status, headers, raw = resp.status, dict(resp.headers.items()), resp.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        status, headers, raw = exc.code, dict((exc.headers or {}).items()), exc.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("response too large")
    return status, {k.lower(): v for k, v in headers.items()}, raw


def _retry_after(headers: dict) -> float | None:
    try:
        return max(0.0, float(headers.get("retry-after")))
    except (TypeError, ValueError):
        return None


class SearXNGProvider:
    name = "searxng"
    kinds = frozenset({"web", "news", "academic", "technical"})
    general_web = True

    def __init__(self, base_url: str = DEFAULT_URL, *, engines: dict | None = None, timeout: float | None = None,
                 clock=time.time, failure_threshold: int = 3, cooldown: float = 300.0, sleep=time.sleep):
        self.base_url = loopback_base(base_url, "SearXNG")
        # optional per-category engine lists (config searxng_engines); default: the category's enabled engines
        self.engines = {str(k): [str(e) for e in v] for k, v in (engines or {}).items() if v}
        self.fixed_timeout = float(timeout) if timeout else None
        self._clock, self._sleep = clock, sleep
        self._failure_threshold, self._cooldown = failure_threshold, cooldown
        self._lock = threading.Lock()
        self._breakers: dict[str, Breaker] = {}
        self._stats: dict[str, dict] = {}
        self._latencies: deque = deque(maxlen=50)
        self._cache: dict[tuple, tuple[float, dict, float]] = {}
        self._config: tuple[float, dict] | None = None
        self.last_nonempty_at: float | None = None
        self._local = threading.local()

    # -- engine health ------------------------------------------------------------------------------------------
    @property
    def last_attempts(self) -> list[dict]:
        return list(getattr(self._local, "attempts", []))

    def _breaker(self, engine: str) -> Breaker:
        if engine not in self._breakers:
            self._breakers[engine] = Breaker(failure_threshold=self._failure_threshold, cooldown=self._cooldown)
        return self._breakers[engine]

    def _stat(self, engine: str) -> dict:
        return self._stats.setdefault(engine, {k: 0 for k in STAT_KEYS})

    def record_engine(self, engine: str, ok: bool, latency: float, error: str | None = None) -> None:
        with self._lock:
            if ok:
                self._breaker(engine).success(latency, self._clock())
            else:
                self._breaker(engine).failure(error, latency, self._clock())

    def engine_snapshot(self) -> dict:
        now = self._clock()
        with self._lock:
            return {e: {**b.snapshot(now), "stats": dict(self._stat(e))} for e, b in self._breakers.items()}

    def engine_stats(self) -> dict:
        with self._lock:
            return {e: dict(s) for e, s in self._stats.items()}

    def dump_engines(self) -> dict:
        with self._lock:
            return {e: b.to_json() for e, b in self._breakers.items()}

    def load_engine_state(self, engine: str, data: dict) -> None:
        with self._lock:
            self._breakers[engine] = Breaker(failure_threshold=self._failure_threshold,
                                             cooldown=self._cooldown).load(data)

    # -- request shaping ----------------------------------------------------------------------------------------
    def accepts(self, query: Query) -> bool:
        return not REGISTRY_QUERY.match(str(query.text))   # "pypi:numpy" belongs to the registries

    def timeout(self) -> float:
        """Adaptive: 2 x p90 of recent call latency + 2 s, within MIN_TIMEOUT..MAX_TIMEOUT (DEFAULT_TIMEOUT until
        three calls were seen)."""
        if self.fixed_timeout:
            return self.fixed_timeout
        with self._lock:
            lat = sorted(self._latencies)
        if len(lat) < 3:
            return DEFAULT_TIMEOUT
        p90 = lat[min(len(lat) - 1, int(0.9 * len(lat)))]
        return round(min(MAX_TIMEOUT, max(MIN_TIMEOUT, 2.0 * p90 + 2.0)), 2)

    def category_engines(self, categories: str) -> list[str]:
        """Engines SearXNG would query for `categories` (config override, else /config's enabled engines)."""
        wanted = [c for c in categories.split(",") if c]
        out: list[str] = []
        for cat in wanted:
            for engine in self.engines.get(cat) or self._enabled(cat):
                if engine not in out:
                    out.append(engine)
        return out

    def _enabled(self, category: str) -> list[str]:
        cfg = self.config()
        return [str(e.get("name")) for e in cfg.get("engines") or []
                if isinstance(e, dict) and e.get("enabled") and category in (e.get("categories") or [])]

    def config(self) -> dict:
        """SearXNG's /config (engines, categories, version), cached CONFIG_TTL seconds; {} when unavailable."""
        now = self._clock()
        with self._lock:
            if self._config and now - self._config[0] < CONFIG_TTL:
                return self._config[1]
        try:
            status, _, raw = _http(self.base_url + "/config", min(self.timeout(), 5.0))
            data = json.loads(raw.decode("utf-8", "replace")) if status == 200 else {}
        except (URLError, ConnectionError, OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        if data:
            with self._lock:
                self._config = (now, data)
        return data

    def params(self, query: Query, route: dict, engines: list[str] | None) -> dict:
        text = " ".join(str(query.text).split())
        if query.site:
            text = f"{text} site:{query.site}"
        out = {"q": text[:MAX_QUERY_CHARS], "format": "json", "categories": route["categories"], "pageno": 1,
               "safesearch": 0}
        if query.language and str(query.language).replace("-", "").isalnum() and len(str(query.language)) <= 12:
            out["language"] = str(query.language)
        if route.get("time_range") in TIME_RANGES:
            out["time_range"] = route["time_range"]
        if engines:
            out["engines"] = ",".join(engines)
        return out

    # -- search -------------------------------------------------------------------------------------------------
    def _get(self, params: dict) -> tuple[dict, float]:
        """One /search call with one retry on 5xx or a reset connection. -> (payload, latency)."""
        url = self.base_url + "/search?" + urlencode(params)
        timeout = self.timeout()
        last = None
        for attempt in range(2):
            started = time.perf_counter()
            try:
                status, headers, raw = _http(url, timeout)
            except (socket.timeout, TimeoutError) as exc:
                raise ProviderError(self.name, f"no answer within {timeout}s: {exc}", code="timeout") from None
            except URLError as exc:
                if isinstance(getattr(exc, "reason", None), (socket.timeout, TimeoutError)):
                    raise ProviderError(self.name, f"no answer within {timeout}s", code="timeout") from None
                if isinstance(getattr(exc, "reason", None), ConnectionRefusedError):
                    raise ProviderError(self.name, f"SearXNG unreachable at {self.base_url}: {exc.reason}",
                                        code="searxng_unreachable") from None
                last = f"{exc.reason}"
            except ConnectionError as exc:
                last = f"{type(exc).__name__}: {exc}"
            except ValueError as exc:
                raise ProviderError(self.name, str(exc), code="bad_response") from None
            else:
                latency = round(time.perf_counter() - started, 4)
                if status == 429:
                    raise ProviderError(self.name, "SearXNG answered 429", code="rate_limited",
                                        retry_after=_retry_after(headers) or 60.0)
                if status >= 500:
                    last = f"HTTP {status}"
                elif status != 200:
                    raise ProviderError(self.name, f"HTTP {status} (is the json format enabled?)",
                                        code="http_error", retryable=False)
                else:
                    try:
                        payload = json.loads(raw.decode("utf-8", "replace"))
                    except ValueError as exc:
                        raise ProviderError(self.name, f"unparseable JSON: {exc}", code="bad_response") from None
                    if not isinstance(payload, dict):
                        raise ProviderError(self.name, "response is not a JSON object", code="bad_response")
                    with self._lock:
                        self._latencies.append(latency)
                    return payload, latency
            if attempt == 0:
                self._sleep(0.5)
        raise ProviderError(self.name, f"SearXNG failed twice: {last}", code="searxng_error")

    def _parse(self, payload: dict, category: str, since, retrieved_at: float, cached: bool) -> list[SearchResult]:
        out, seen = [], set()
        kind = "academic" if category == "science" else "web"
        for item in payload.get("results") if isinstance(payload.get("results"), list) else []:
            if not isinstance(item, dict):
                continue
            url = _clean_url(item.get("url"))
            if url is None or url in seen:
                continue
            published = parse_iso_date(item.get("publishedDate"))   # absent or unparseable -> None, never guessed
            if since is not None and published is not None and published < since:
                continue
            seen.add(url)
            engines = [str(e)[:40] for e in item.get("engines") or [] if e] or [str(item.get("engine") or "")[:40]]
            metadata = {"engines": engines, "category": str(item.get("category") or category)[:40],
                        "retrieved_at": retrieved_at, "canonical_url": _canonical(url)}
            score = item.get("score")
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                metadata["searxng_score"] = round(float(score), 4)
            if cached:
                metadata["cache"] = True
            out.append(SearchResult(url=url, title=strip_html(item.get("title"))[:500] or url,
                                    snippet=strip_html(item.get("content"))[:1000], provider=self.name,
                                    engine=str(item.get("engine") or engines[0])[:40], rank=len(out) + 1,
                                    kind=kind, published_at=published, metadata=metadata))
        return out

    def search(self, query: Query, limit: int, *, budget=None, usage=None) -> list[SearchResult]:
        from ..intent import searxng_route
        limit = max(1, min(int(limit), MAX_LIMIT))
        attempts: list[dict] = []
        self._local.attempts = attempts
        if not str(query.text).strip():
            return []
        now = self._clock()
        route = searxng_route(query, now=now)
        category = route["categories"].split(",")[0]
        known = self.category_engines(route["categories"])
        with self._lock:
            allowed = [e for e in known if self._breaker(e).allow(now)]
        for engine in known:
            if engine not in allowed:
                attempts.append({"engine": engine, "ok": False, "n": 0, "error": "circuit_open", "latency": 0.0})
        if known and not allowed:
            raise ProviderError(self.name, f"every {route['categories']} engine circuit is open", code="circuit_open")
        narrowed = allowed if (len(allowed) < len(known) or self.engines) else None
        params = self.params(query, route, narrowed)
        key = tuple(sorted(params.items()))
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] > CACHE_TTL:
                self._cache.pop(key, None)
                hit = None
        if hit:
            with self._lock:
                for engine in allowed:
                    self._breaker(engine).neutral()
            results = self._parse(hit[1], category, query.since, hit[2], True)
            attempts.append({"engine": None, "ok": True, "n": len(results), "error": None, "latency": 0.0,
                             "cache": True})
            return results[:limit]
        try:
            charge(budget, usage, requests=max(1, len(allowed)))   # one upstream request per queried engine
        except BudgetExceeded as exc:
            with self._lock:
                for engine in allowed:
                    self._breaker(engine).neutral()
            raise ProviderError(self.name, str(exc), retryable=False, code="budget_exhausted") from None
        try:
            payload, latency = self._get(params)
        except ProviderError:
            with self._lock:
                for engine in allowed:
                    self._breaker(engine).neutral()
            raise
        retrieved_at = self._clock()
        results = self._parse(payload, category, query.since, retrieved_at, False)
        failed = {}
        for entry in payload.get("unresponsive_engines") or []:
            if isinstance(entry, (list, tuple)) and entry:
                failed[str(entry[0])] = str(entry[1] if len(entry) > 1 else "error")[:200]
        found: dict[str, int] = {}
        for r in results:
            for engine in r.metadata.get("engines") or []:
                found[engine] = found.get(engine, 0) + 1
        queried = list(dict.fromkeys([*allowed, *failed, *found]))
        with self._lock:
            for engine in queried:
                stat = self._stat(engine)
                stat["calls"] += 1
                breaker = self._breaker(engine)
                if engine in failed:
                    kind = classify(failed[engine])
                    stat[kind] += 1
                    breaker.failure(f"{kind}: {failed[engine]}", latency, retrieved_at)
                    attempts.append({"engine": engine, "ok": False, "n": 0, "error": f"{kind}: {failed[engine]}",
                                     "latency": latency})
                elif found.get(engine):
                    stat["success"] += 1
                    stat["results"] += found[engine]
                    breaker.success(latency, retrieved_at)
                    attempts.append({"engine": engine, "ok": True, "n": found[engine], "error": None,
                                     "latency": latency})
                else:
                    stat["empty"] += 1
                    breaker.neutral(latency)
                    attempts.append({"engine": engine, "ok": True, "n": 0, "error": "empty", "latency": latency})
            if results:
                self.last_nonempty_at = retrieved_at
                self._cache[key] = (now, payload, retrieved_at)
                while len(self._cache) > CACHE_MAX:
                    self._cache.pop(next(iter(self._cache)))
        if not results and queried and all(e in failed for e in queried):
            raise ProviderError(self.name, "every engine failed (" +
                                "; ".join(f"{e}: {failed[e]}" for e in queried) + ")", code="engines_failed")
        return results[:limit]

    # -- health -------------------------------------------------------------------------------------------------
    def health(self) -> dict:
        now = self._clock()
        out = {"base_url": self.base_url, "reachable": False, "version": None, "error": None,
               "engines_enabled": {}, "engines": self.engine_snapshot(), "last_nonempty_at": self.last_nonempty_at,
               "timeout": self.timeout(), "expected_version": IMAGE_VERSION}
        try:
            status, _, raw = _http(self.base_url + "/healthz", 5.0)
            out["reachable"] = status == 200
            if status != 200:
                out["error"] = f"/healthz answered HTTP {status}"
        except (URLError, ConnectionError, OSError, ValueError) as exc:
            out["error"] = f"{type(exc).__name__}: {exc}"[:300]
        if out["reachable"]:
            cfg = self.config()
            out["version"] = cfg.get("version")
            for cat in CATEGORIES:
                out["engines_enabled"][cat] = self.engines.get(cat) or self._enabled(cat)
        snaps = out["engines"]
        engines_open = bool(snaps) and all(s["state"] == "open" for s in snaps.values())
        out["verdict"] = verdict(out["reachable"], self.last_nonempty_at, now, engines_open=engines_open)
        return out


# ---- the container -------------------------------------------------------------------------------------------

def honest_client(text: str) -> str:
    """Patch SearXNG's network/client.py (IMAGE_VERSION) so no request impersonates a browser: curl_cffi
    impersonation (Chrome TLS fingerprint + Chrome headers and User-Agent, the image default, or Firefox where an
    engine asks) is switched off, and every client sends SearXNG's own User-Agent unless an engine sets one (which
    `useragents.json` makes honest too). Raises ValidationError when the file is not the expected one."""
    if hashlib.sha256(text.encode()).hexdigest() != CLIENT_SHA256:
        raise ValidationError(f"SearXNG {CLIENT_PATH} is not the pinned {IMAGE_VERSION} file; refusing to patch")
    old_flag = '    use_impersonate = impersonate not in ("", NO_IMPERSONATE)\n'
    old_if = '    if use_impersonate:\n'
    if text.count(old_flag) != 1 or text.count(old_if) != 1:
        raise ValidationError("SearXNG client.py patch anchors not found")
    text = text.replace(old_flag, "    use_impersonate = False  # NeuroSciFi ADR-034: never impersonate a browser\n")
    return text.replace(old_if, '    from searx.utils import searxng_useragent  # NeuroSciFi ADR-034: honest UA\n'
                                '    kwargs["headers"] = {"User-Agent": searxng_useragent()}\n' + old_if)


def honest_useragents() -> str:
    """searx/data/useragents.json whose 'random browser' User-Agent is SearXNG's honest one (no placeholders)."""
    return json.dumps({"os": ["NeuroSciFi"], "ua": HONEST_UA, "versions": [IMAGE_VERSION]}, indent=2) + "\n"


# Run inside the container by Container.probe_headers (stdin to the image's Python): SearXNG's network layer
# against a loopback listener, no external traffic.
_PROBE = r"""
import http.server, json, threading
seen = {}
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        seen[self.path.strip("/")] = dict(self.headers); self.send_response(200); self.end_headers()
        self.wfile.write(b"ok")
    def log_message(self, *a):
        pass
srv = http.server.HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d/" % srv.server_address[1]
import searx.network as N
import searx.network.network as NN
from searx.utils import gen_useragent, searxng_useragent
N.initialize()
for net in NN.NETWORKS.values():
    net.enable_http = True
N.get(base + "default", timeout=5)
N.get(base + "chrome", timeout=5, impersonate="chrome")
N.get(base + "firefox", timeout=5, impersonate="firefox")
N.get(base + "gen_useragent", timeout=5, headers={"User-Agent": gen_useragent()})
print(json.dumps({"searxng_useragent": searxng_useragent(), "gen_useragent": gen_useragent(), "requests": seen}))
"""


def default_config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "neuroscifi" / "searxng"


def _write(path: Path, text: str, mode: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as fh:
        os.fchmod(fh.fileno(), mode)
        fh.write(text)


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


@dataclass
class Container:
    """The pinned SearXNG image under docker, published on 127.0.0.1:`port` only.

    `start()` prepares the config directory (secret, settings, honest-UA patch; see module docstring), then
    `docker run`s the container unless NeuroSciFi's container already runs, and waits for /healthz. `stop()` stops
    and removes only the container named CONTAINER_NAME carrying the label neuroscifi.component=searxng.
    """
    port: int = DEFAULT_PORT
    host: str = "127.0.0.1"
    config_dir: Path | str | None = None
    template: Path | str = DEFAULT_TEMPLATE
    docker: str = "docker"
    memory: str = "768m"
    cpus: str = "1.0"
    pids: int = 256
    runner: object = field(default=subprocess.run, repr=False)

    def __post_init__(self):
        if not _is_loopback_host(str(self.host)):
            raise NotLoopback(f"SearXNG must be published on a loopback address, not {self.host!r}")
        if not (0 < int(self.port) < 65536):
            raise ValidationError(f"invalid port {self.port!r}")
        self.config_dir = Path(self.config_dir) if self.config_dir else default_config_dir()
        # an installed wheel has no deploy/ tree next to it: NSF_SEARXNG_SETTINGS names the template there
        self.template = Path(os.environ.get("NSF_SEARXNG_SETTINGS") or self.template)

    @property
    def base_url(self) -> str:
        host = f"[{self.host}]" if ":" in str(self.host) else self.host
        return f"http://{host}:{int(self.port)}"

    def _path(self, name: str) -> Path:
        return Path(self.config_dir) / name

    def _docker(self, *args, timeout: float = 120.0, check: bool = False, input: str | None = None):
        try:
            return self.runner([self.docker, *args], capture_output=True, text=True, timeout=timeout, check=check,
                               input=input)
        except (OSError, subprocess.SubprocessError) as exc:   # docker missing, hung or refused
            return subprocess.CompletedProcess([self.docker, *args], 127, "", f"{type(exc).__name__}: {exc}")

    def prepare(self) -> dict:
        """Create the config directory (0700), the secret (once, 0600), settings.yml and the honest-UA files
        (0644: the container user must read them through the read-only bind mounts)."""
        cfg = Path(self.config_dir)
        cfg.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(cfg, 0o700)
        secret = self._path("secret.env")
        created = False
        if not secret.exists():
            _write(secret, f"SEARXNG_SECRET={secrets.token_hex(32)}\n", 0o600)
            created = True
        os.chmod(secret, 0o600)
        if not Path(self.template).is_file():
            raise ValidationError(f"SearXNG settings template {self.template} not found (set NSF_SEARXNG_SETTINGS "
                                  "to deploy/searxng/settings.yml)")
        _write(self._path("settings.yml"), Path(self.template).read_text(), 0o644)
        _write(self._path("useragents.json"), honest_useragents(), 0o644)
        pristine = self._docker("run", "--rm", "--network", "none", "--entrypoint", "cat", IMAGE, CLIENT_PATH)
        if getattr(pristine, "returncode", 1) != 0:
            raise ValidationError(f"cannot read {CLIENT_PATH} from {IMAGE}: {getattr(pristine, 'stderr', '')}"[:400])
        _write(self._path("client.py"), honest_client(pristine.stdout), 0o644)
        marker = {"image": IMAGE, "version": IMAGE_VERSION, "user_agent": HONEST_UA,
                  "files": {name: _sha(self._path(name)) for name in ("client.py", "useragents.json", "settings.yml")}}
        _write(self._path(PATCH_MARKER), json.dumps(marker, indent=2) + "\n", 0o644)
        return {"config_dir": str(cfg), "secret_created": created, "honest_user_agent": self.honest()}

    def honest(self) -> bool:
        """The patch marker matches the files on disk and the patched client disables impersonation."""
        try:
            marker = json.loads(self._path(PATCH_MARKER).read_text())
            client = self._path("client.py").read_text()
        except (OSError, ValueError):
            return False
        return (marker.get("version") == IMAGE_VERSION and marker.get("user_agent") == HONEST_UA
                and all(_sha(self._path(n)) == d for n, d in (marker.get("files") or {}).items())
                and "use_impersonate = False" in client and HONEST_UA in self._path("useragents.json").read_text())

    def probe_headers(self) -> dict:
        """Run SearXNG's own HTTP client inside the running container against a throwaway loopback listener and
        report the headers it sends: by default, when an engine asks for Chrome or Firefox impersonation, and with
        an engine's gen_useragent() header. `honest` is True only when no request carried a browser User-Agent or
        Client-Hint/navigation headers."""
        done = self._docker("exec", "-i", CONTAINER_NAME, "/usr/local/searxng/.venv/bin/python", "-",
                            timeout=60, input=_PROBE)
        try:
            data = json.loads(getattr(done, "stdout", "") or "")
        except ValueError:
            return {"honest": False, "error": (getattr(done, "stderr", "") or "probe failed")[-500:]}
        browserish = []
        for name, headers in (data.get("requests") or {}).items():
            low = {str(k).lower(): str(v) for k, v in headers.items()}
            if "mozilla/" in low.get("user-agent", "").lower() or "sec-ch-ua" in low or "sec-fetch-mode" in low:
                browserish.append(name)
        return {**data, "browser_like": browserish,
                "honest": not browserish and bool(data.get("requests")) and all(
                    HONEST_SUFFIX in str({k.lower(): v for k, v in h.items()}.get("user-agent", ""))
                    for h in data["requests"].values())}

    def run_args(self) -> list[str]:
        cfg = Path(self.config_dir)
        return ["run", "--detach", "--name", CONTAINER_NAME, "--label", "neuroscifi.component=searxng",
                "--restart", "unless-stopped",
                "--publish", f"{self.host}:{int(self.port)}:8080",
                "--user", CONTAINER_USER, "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--memory", self.memory, "--memory-swap", self.memory, "--cpus", self.cpus,
                "--pids-limit", str(int(self.pids)),
                "--tmpfs", "/tmp:rw,size=64m,mode=1777",
                "--tmpfs", "/var/cache/searxng:rw,size=64m,uid=977,gid=977,mode=0755",
                "--tmpfs", "/etc/searxng:rw,size=1m,uid=977,gid=977,mode=0755",
                # the image's .pyc files are hash-based and unchecked: without an empty __pycache__ Python would
                # run the original client.py and ignore the mounted patch
                "--tmpfs", f"{PYCACHE_PATH}:rw,size=4m,uid=977,gid=977,mode=0755",
                "--volume", f"{cfg / 'settings.yml'}:/etc/searxng/settings.yml:ro",
                "--volume", f"{cfg / 'client.py'}:{CLIENT_PATH}:ro",
                "--volume", f"{cfg / 'useragents.json'}:{USERAGENTS_PATH}:ro",
                "--env-file", str(cfg / "secret.env"),
                "--env", "FORCE_OWNERSHIP=false",
                "--health-cmd", "wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1",
                "--health-interval", "30s", "--health-timeout", "5s", "--health-retries", "3",
                "--health-start-period", "20s",
                "--log-driver", "json-file", "--log-opt", "max-size=10m", "--log-opt", "max-file=3",
                IMAGE]

    def inspect(self) -> dict | None:
        done = self._docker("inspect", CONTAINER_NAME, timeout=30)
        if getattr(done, "returncode", 1) != 0:
            return None
        try:
            data = json.loads(done.stdout)
        except ValueError:
            return None
        item = data[0] if isinstance(data, list) and data else None
        labels = ((item or {}).get("Config") or {}).get("Labels") or {}
        return item if labels.get("neuroscifi.component") == "searxng" else None

    def _answers(self) -> bool:
        try:
            return _http(self.base_url + "/healthz", 2.0)[0] == 200
        except (URLError, ConnectionError, OSError, ValueError):
            return False

    def start(self, *, timeout: float = 60.0) -> dict:
        info = self.inspect()
        if info and (info.get("State") or {}).get("Running"):
            if (info.get("Config") or {}).get("Image") == IMAGE:
                return {**self.status(), "started": False, "reason": "already running"}
            self.stop()
        elif info:
            self._docker("rm", CONTAINER_NAME, timeout=60)
        if _port_open(str(self.host), int(self.port)):
            raise ProviderError("searxng", f"port {int(self.port)} on {self.host} is in use by another process; "
                                "choose another port (searxng_url)", code="port_in_use")
        prepared = self.prepare()
        if not prepared["honest_user_agent"]:
            raise ValidationError("the honest-User-Agent patch for SearXNG could not be verified; not starting")
        done = self._docker(*self.run_args(), timeout=120)
        if getattr(done, "returncode", 1) != 0:
            raise ProviderError("searxng", f"docker run failed: {getattr(done, 'stderr', '')}"[:500],
                                code="container_failed")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._answers():
                probe = self.probe_headers()
                if not probe.get("honest"):
                    self.stop()
                    raise ProviderError("searxng", "SearXNG still sends browser-like requests after the honest-UA "
                                        f"patch ({probe.get('browser_like') or probe.get('error')}); stopped it",
                                        code="not_honest")
                return {**self.status(), "started": True, "prepared": prepared,
                        "header_probe": {"honest": True, "user_agent": probe.get("searxng_useragent")}}
            info = self.inspect()
            if info and not (info.get("State") or {}).get("Running"):
                logs = self._docker("logs", "--tail", "30", CONTAINER_NAME, timeout=30)
                raise ProviderError("searxng", f"container exited: {getattr(logs, 'stdout', '')}"
                                    f"{getattr(logs, 'stderr', '')}"[-1500:], code="container_failed")
            time.sleep(0.5)
        raise ProviderError("searxng", f"SearXNG did not answer /healthz within {timeout}s", code="container_failed")

    def stop(self) -> dict:
        if self.inspect() is None:
            return {"stopped": False, "reason": f"no container {CONTAINER_NAME} started by NeuroSciFi"}
        self._docker("stop", "--time", "15", CONTAINER_NAME, timeout=60)
        self._docker("rm", CONTAINER_NAME, timeout=60)
        return {"stopped": True, "container": CONTAINER_NAME}

    def status(self) -> dict:
        info = self.inspect() or {}
        state = info.get("State") or {}
        ports = ((info.get("NetworkSettings") or {}).get("Ports") or {}).get("8080/tcp") or []
        return {"container": CONTAINER_NAME, "exists": bool(info), "running": bool(state.get("Running")),
                "health": (state.get("Health") or {}).get("Status"), "started_at": state.get("StartedAt"),
                "image": (info.get("Config") or {}).get("Image"), "expected_image": IMAGE,
                "published": [f"{p.get('HostIp')}:{p.get('HostPort')}" for p in ports if isinstance(p, dict)],
                "base_url": self.base_url, "answers": self._answers(), "config_dir": str(self.config_dir),
                "secret_present": self._path("secret.env").exists(), "honest_user_agent": self.honest()}


__all__ = ["SearXNGProvider", "Container", "classify", "verdict", "honest_client", "honest_useragents",
           "DEFAULT_URL", "IMAGE", "IMAGE_VERSION", "HONEST_UA", "HEALTHY_WINDOW"]
