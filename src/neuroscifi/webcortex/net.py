"""SafeFetcher: the only path from an individual to the public web (ARCHITECTURE §3, §4.1; ADR-024).

Security properties (each one has a test in tests/webcortex/test_net.py)
- Address policy: every resolved address must be globally routable. Loopback, RFC 1918, link-local (incl. the
  169.254.169.254 / fd00:ec2::254 metadata endpoints), CGNAT, multicast, reserved, unspecified, documentation and
  benchmark ranges, IPv6 ULA and site-local are refused, and so are IPv6 forms that embed a refused IPv4 address
  (IPv4-mapped, IPv4-compatible, 6to4, Teredo, NAT64). If ANY answer for a host is refused the host is refused.
- URL policy: http/https only, allow-listed ports, no userinfo, no non-canonical numeric hosts (0x7f000001,
  2130706433, 127.1), IDNA-encodable hosts only, deny-listed domains and their subdomains, at most 2048 chars.
- DNS pinning: a host is resolved once per fetch (shared by its robots.txt request and redirect hops back to it);
  the socket connects to a validated address and is never re-resolved, so DNS rebinding cannot swap the target.
  TLS verifies the certificate against the original hostname; the Host header carries the original hostname.
- Redirects are followed manually (at most policy.max_redirects); every hop is re-checked (URL policy, resolution,
  address policy, robots), https -> http downgrades are refused, 303 (and 301/302 after POST) become GET, and
  credentials headers are dropped when the origin changes.
- Limits: total wall-clock timeout per fetch, streamed body cap (pages/crawls are truncated and flagged; api/feed
  responses raise too_large), gzip/deflate decoded under the same cap (decompression bombs stop at the cap),
  content-type allow-list checked before the body is read.
- Politeness: robots.txt for purposes page/feed/crawl (4xx => allow, 5xx/unreachable => disallow this host for
  this process, cached 24 h in memory and the PublicCache), Crawl-delay as the per-host minimum interval,
  per-host throttle, Retry-After backoff on 429/503 (requests inside a backoff fail without touching the network).
- Budget: one request is charged before every network call (robots and redirect hops included); body bytes are
  charged after reading, and reading stops before the byte budget could be exceeded (BudgetExceeded propagates).
- Privacy: bodies and full query strings are never logged; purpose='api' responses are never cached (API URLs
  carry search queries, ADR-023).

Everything that touches the outside world is injectable: `resolver(host, port) -> [ip]`,
`connector(ip, port, timeout) -> socket`, `clock()` and `sleep(seconds)` (tests use fixtureweb and a fake clock).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import email.utils
import fnmatch
import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
import urllib.robotparser
import weakref
import zlib

from .config import USER_AGENT
from .types import BudgetExceeded, FetchError, FetchResult

CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "application/json", "application/*+json",
                 "application/xml", "text/xml", "application/atom+xml", "application/rss+xml", "application/pdf")
PURPOSES = ("page", "api", "feed", "crawl", "robots")
ROBOTS_PURPOSES = ("page", "feed", "crawl")
REDIRECT_STATUSES = (301, 302, 303, 307, 308)
MAX_URL_LENGTH = 2048
ROBOTS_TTL = 24 * 3600.0
ROBOTS_MAX_BYTES = 512 * 1024          # RFC 9309 asks parsers to accept at least 500 KiB
MAX_CRAWL_DELAY = 60.0                 # a hostile robots.txt cannot stall a task for hours
MAX_RETRY_AFTER = 6 * 3600.0
DEFAULT_BACKOFF = 60.0                 # 429 without a usable Retry-After
CHUNK = 64 * 1024
_RESERVED_SUFFIXES = ("localhost", "localdomain", "local", "internal", "home.arpa")
_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?)(\.[a-z0-9_](?:[a-z0-9_-]{0,61}"
                      r"[a-z0-9_])?)*\.?$")
_NUMERIC_LABEL = re.compile(r"^(0x[0-9a-f]*|[0-9]+)$")
_FORBIDDEN_HEADERS = {"host", "accept-encoding", "connection", "content-length", "transfer-encoding", "user-agent",
                      "te", "upgrade", "proxy-authorization", "if-none-match", "if-modified-since"}
_CREDENTIAL_HEADERS = {"authorization", "cookie"}
_CHARGE_LOCK = threading.Lock()


@dataclass(frozen=True)
class NetPolicy:
    allowed_schemes: tuple = ("http", "https")
    allowed_ports: tuple = (80, 443, 8080, 8443)
    max_redirects: int = 5
    max_bytes: int = 5_000_000
    timeout: float = 15.0
    user_agent: str = USER_AGENT
    content_types: tuple = CONTENT_TYPES
    deny_domains: tuple = ()
    min_domain_interval: float = 1.0
    max_concurrency: int = 4           # afetch semaphore (clamped further by budget.max_concurrency)


# ---- address and URL policy -----------------------------------------------------------------------------------

_V4_EXTRA = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "100.64.0.0/10", "192.0.0.0/24", "192.0.2.0/24", "198.18.0.0/15", "198.51.100.0/24",
    "203.0.113.0/24", "240.0.0.0/4", "255.255.255.255/32")]
_V6_EXTRA = [ipaddress.ip_network(n) for n in (
    "fc00::/7", "fec0::/10", "fe80::/10", "ff00::/8", "2001:db8::/32", "3fff::/20", "100::/64", "::/128", "::1/128",
    "fd00:ec2::254/128")]
_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))
_COMPAT = ipaddress.ip_network("::/96")


def _check_v4(addr: ipaddress.IPv4Address) -> str | None:
    if addr.is_unspecified:
        return "unspecified address"
    if addr.is_loopback:
        return "loopback address"
    if addr.is_link_local:
        return "link-local address (incl. cloud metadata)"
    if addr.is_multicast:
        return "multicast address"
    if addr in ipaddress.ip_network("100.64.0.0/10"):
        return "carrier-grade NAT address"
    if addr.is_private:
        return "private address"
    if addr.is_reserved:
        return "reserved address"
    if any(addr in net for net in _V4_EXTRA):
        return "special-purpose address"
    if not addr.is_global:
        return "not globally routable"
    return None


def check_address(ip: str) -> str | None:
    """Reason the address must not be contacted, or None when it is a public unicast address."""
    try:
        addr = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return "not an IP address"
    if addr.version == 4:
        return _check_v4(addr)
    embedded = None
    if addr.ipv4_mapped is not None:
        embedded = ("IPv4-mapped", addr.ipv4_mapped)
    elif addr.sixtofour is not None:
        embedded = ("6to4", addr.sixtofour)
    elif addr.teredo is not None:
        server, client = addr.teredo
        reason = _check_v4(client) or _check_v4(server)
        if reason:
            return f"Teredo address embedding a {reason}"
    elif any(addr in net for net in _NAT64):
        embedded = ("NAT64", ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF))
    elif addr in _COMPAT and int(addr) > 1:
        embedded = ("IPv4-compatible", ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF))
    if embedded is not None:
        reason = _check_v4(embedded[1])
        if reason:
            return f"{embedded[0]} address embedding a {reason}"
        if embedded[0] in ("IPv4-mapped", "NAT64"):
            return None             # the embedded public IPv4 address is what is actually contacted
    if addr == ipaddress.IPv6Address("fd00:ec2::254"):
        return "cloud metadata address"
    if addr.is_unspecified:
        return "unspecified address"
    if addr.is_loopback:
        return "loopback address"
    if addr.is_link_local:
        return "link-local address"
    if addr.is_site_local:
        return "site-local address"
    if addr.is_multicast:
        return "multicast address"
    if addr in ipaddress.ip_network("fc00::/7"):
        return "unique-local address"
    if addr.is_private or addr.is_reserved or any(addr in net for net in _V6_EXTRA):
        return "special-purpose address"
    if not addr.is_global:
        return "not globally routable"
    return None


def _refuse(url, detail):
    return FetchError("blocked_policy", _redact(url), detail)


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _redact(url: str) -> str:
    """URL without its query string, for error messages and stats (queries never reach logs)."""
    try:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")) + ("?…" if parts.query else "")
    except ValueError:
        return url[:80]


def check_url(url: str, policy: NetPolicy) -> "urllib.parse.SplitResult":
    """Validate a URL against the policy; returns a normalised SplitResult or raises FetchError('blocked_policy')."""
    if not isinstance(url, str) or not url:
        raise FetchError("blocked_policy", str(url)[:80], "empty URL")
    if len(url) > MAX_URL_LENGTH:
        raise _refuse(url, f"URL longer than {MAX_URL_LENGTH} characters")
    if any(ord(c) < 0x21 or ord(c) == 0x7F for c in url):
        raise _refuse(url, "URL contains whitespace or control characters")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as error:
        raise _refuse(url, f"malformed URL ({error})") from None
    scheme = parts.scheme.lower()
    if scheme not in policy.allowed_schemes:
        raise _refuse(url, f"scheme {scheme or '(none)'!r} not allowed")
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise _refuse(url, "credentials in URL")
    if "\\" in parts.netloc:
        raise _refuse(url, "backslash in host")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise _refuse(url, "no host")
    port = port if port is not None else (443 if scheme == "https" else 80)
    if port not in policy.allowed_ports:
        raise _refuse(url, f"port {port} not allowed")
    literal = None
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        if _NUMERIC_LABEL.match(host.split(".")[-1]):
            try:
                dotted = socket.inet_ntoa(socket.inet_aton(host))
            except OSError:
                raise _refuse(url, "invalid numeric host") from None
            if dotted != host:
                raise _refuse(url, "non-canonical numeric IPv4 host")
            literal = ipaddress.ip_address(dotted)
    if literal is not None:
        if isinstance(literal, ipaddress.IPv6Address) and literal.scope_id:
            raise _refuse(url, "scoped IPv6 literal")
        reason = check_address(str(literal))
        if reason:
            raise _refuse(url, reason)
        netloc_host = f"[{literal}]" if literal.version == 6 else str(literal)
    else:
        try:
            ascii_host = host.encode("idna").decode("ascii").lower() if not host.isascii() else host.lower()
        except UnicodeError:
            raise _refuse(url, "host is not IDNA-encodable") from None
        if not _HOST_RE.match(ascii_host):
            raise _refuse(url, "invalid host name")
        if any(ascii_host == s or ascii_host.endswith("." + s) for s in _RESERVED_SUFFIXES):
            raise _refuse(url, "reserved local host name")
        for domain in policy.deny_domains:
            domain = str(domain).lower().strip(".")
            if domain and (ascii_host == domain or ascii_host.endswith("." + domain)):
                raise _refuse(url, "domain is on the deny list")
        host = netloc_host = ascii_host
    netloc = netloc_host if parts.port is None else f"{netloc_host}:{port}"
    return urlsplit(urlunsplit((scheme, netloc, parts.path or "/", parts.query, "")))


# ---- low-level connection ---------------------------------------------------------------------------------------

def _default_resolver(host, port):
    out = []
    for _family, _type, _proto, _name, sockaddr in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        if sockaddr[0] not in out:
            out.append(sockaddr[0])
    return out


def _default_connector(ip, port, timeout):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect((ip, port))
    except BaseException:
        sock.close()
        raise
    return sock


class _Holding:
    """Keeps the pinned socket open (http.client closes it early on `Connection: close`) so the body reader can
    keep shrinking its timeout to the fetch deadline; `release()` closes for real."""
    held = True

    def close(self):
        if not self.held:
            super().close()

    def release(self):
        self.held = False
        self.close()


class _PinnedHTTP(_Holding, http.client.HTTPConnection):
    """HTTPConnection whose connect() uses a socket already opened to a validated address."""

    def __init__(self, host, port, *, opener, timeout):
        super().__init__(host, port, timeout=timeout)
        self._opener = opener
        self.pinned = None

    def connect(self):
        self.sock = self.pinned = self._opener()


class _PinnedHTTPS(_Holding, http.client.HTTPSConnection):
    def __init__(self, host, port, *, opener, timeout, context):
        super().__init__(host, port, timeout=timeout, context=context)
        self._opener = opener
        self.pinned = None

    def connect(self):
        raw = self._opener()
        try:
            self.sock = self.pinned = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


@dataclass
class _Response:
    status: int
    headers: dict
    body: bytes = b""
    truncated: bool = False
    remote_ip: str | None = None
    wire: int = 0


@dataclass
class _Robots:
    parser: urllib.robotparser.RobotFileParser
    fetched_at: float
    delay: float = 0.0
    sitemaps: tuple = ()
    reason: str = ""

    def allowed(self, url: str) -> bool:
        return self.parser.can_fetch(USER_AGENT, url)


class _Flight:
    def __init__(self):
        self.event = threading.Event()
        self.result = None
        self.error = None


def _media_type(value: str | None) -> str:
    return (value or "").split(";")[0].strip().lower()


def _charge(budget, usage, **amounts):
    if budget is not None and usage is not None:
        with _CHARGE_LOCK:
            usage.charge(budget, **amounts)


def _parse_retry_after(value: str | None, now: float) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return min(float(value), MAX_RETRY_AFTER)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    return min(max(0.0, when.timestamp() - now), MAX_RETRY_AFTER)


def _robots_from_text(text: str, fetched_at: float, reason: str = "") -> _Robots:
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(text.splitlines())
    delay = parser.crawl_delay(USER_AGENT)
    try:
        delay = min(max(float(delay or 0.0), 0.0), MAX_CRAWL_DELAY)
    except (TypeError, ValueError):
        delay = 0.0
    return _Robots(parser, fetched_at, delay, tuple(parser.site_maps() or ()), reason)


def _robots_disallow_all(fetched_at: float, reason: str) -> _Robots:
    parser = urllib.robotparser.RobotFileParser()
    parser.disallow_all = True
    return _Robots(parser, fetched_at, 0.0, (), reason)


class SafeFetcher:
    def __init__(self, policy: NetPolicy | None = None, *, cache=None, resolver=None, connector=None,
                 clock=time.time, sleep=time.sleep):
        self.policy = policy or NetPolicy()
        self.cache = cache
        self._resolver = resolver or _default_resolver
        self._connector = connector or _default_connector
        self._clock, self._sleep = clock, sleep
        self._ssl = ssl.create_default_context()
        self._ssl.minimum_version = ssl.TLSVersion.TLSv1_2
        self._lock = threading.Lock()
        self._inflight: dict = {}
        self._last_request: dict[str, float] = {}
        self._backoff: dict[str, float] = {}
        self._robots: dict[str, _Robots] = {}
        self._robots_locks: dict[str, threading.Lock] = {}
        self._stats: dict[str, dict] = {}
        self._semaphores = weakref.WeakKeyDictionary()

    # ---- public API -----------------------------------------------------------------------------------------
    def fetch(self, url, *, method="GET", body=None, headers=None, purpose="page", conditional=True, budget=None,
              usage=None) -> FetchResult:
        method = str(method).upper()
        if purpose not in PURPOSES:
            raise FetchError("blocked_policy", _redact(str(url)), f"unknown purpose {purpose!r}")
        if method not in ("GET", "HEAD", "POST") or (method == "POST" and purpose != "api"):
            raise FetchError("blocked_policy", _redact(str(url)), f"method {method} not allowed for {purpose}")
        if method != "GET" or body is not None:
            return self._fetch(url, method, body, headers, purpose, conditional, budget, usage)
        key = (url, purpose, bool(conditional), tuple(sorted((str(k).lower(), str(v))
                                                             for k, v in (headers or {}).items())))
        with self._lock:
            flight = self._inflight.get(key)
            leader = flight is None
            if leader:
                flight = self._inflight[key] = _Flight()
        if not leader:
            self._count(urlsplit(url).hostname or "", "coalesced")
            if not flight.event.wait(self.policy.timeout * 3 + 5):
                raise FetchError("timeout", _redact(url), "coalesced request did not finish")
            if flight.error is not None:
                raise flight.error
            return replace(flight.result, headers=dict(flight.result.headers), redirects=list(flight.result.redirects))
        try:
            flight.result = self._fetch(url, method, body, headers, purpose, conditional, budget, usage)
            return flight.result
        except BaseException as error:
            flight.error = error
            raise
        finally:
            with self._lock:
                self._inflight.pop(key, None)
            flight.event.set()

    async def afetch(self, url, **kwargs) -> FetchResult:
        budget = kwargs.get("budget")
        limit = self.policy.max_concurrency
        if budget is not None:
            limit = min(limit, budget.max_concurrency)
        semaphore = self._semaphore(max(1, int(limit)))
        async with semaphore:
            return await asyncio.to_thread(self.fetch, url, **kwargs)

    def robots_allowed(self, url, *, budget=None, usage=None) -> bool:
        parts = check_url(url, self.policy)
        entry = self._robots_entry(parts, budget, usage, {}, time.monotonic() + self.policy.timeout)
        return entry.allowed(parts.geturl())

    def sitemaps(self, url, *, budget=None, usage=None) -> list[str]:
        """`Sitemap:` URLs announced by the robots.txt of url's host (used by crawl.py)."""
        parts = check_url(url, self.policy)
        return list(self._robots_entry(parts, budget, usage, {}, time.monotonic() + self.policy.timeout).sitemaps)

    def stats(self) -> dict:
        now = self._clock()
        with self._lock:
            hosts = {}
            for host, counters in self._stats.items():
                row = dict(counters)
                until = self._backoff.get(host)
                row["backoff_remaining"] = max(0.0, until - now) if until else 0.0
                hosts[host] = row
            inflight = len(self._inflight)
        totals = {}
        for row in hosts.values():
            for name, value in row.items():
                if name != "backoff_remaining":
                    totals[name] = totals.get(name, 0) + value
        return {"hosts": hosts, "totals": totals, "inflight": inflight}

    # ---- internals ------------------------------------------------------------------------------------------
    def _semaphore(self, limit: int) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        with self._lock:
            per_loop = self._semaphores.setdefault(loop, {})
            if limit not in per_loop:
                per_loop[limit] = asyncio.Semaphore(limit)
            return per_loop[limit]

    def _count(self, host, name, amount=1):
        with self._lock:
            row = self._stats.setdefault(host, {"requests": 0, "errors": 0, "bytes": 0, "backoffs": 0,
                                                "robots_blocked": 0, "coalesced": 0, "cache_hits": 0})
            row[name] += amount

    def _fetch(self, url, method, body, headers, purpose, conditional, budget, usage) -> FetchResult:
        started = time.monotonic()
        deadline = started + self.policy.timeout
        host = _host(url)
        try:
            parts = check_url(url, self.policy)
            request_headers = {"User-Agent": self.policy.user_agent,
                               "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;"
                                         "q=0.9,application/pdf;q=0.8,text/plain;q=0.7,*/*;q=0.1",
                               "Accept-Encoding": "gzip, deflate", "Connection": "close"}
            for name, value in (headers or {}).items():
                if str(name).lower() not in _FORBIDDEN_HEADERS:
                    request_headers[str(name)] = str(value)
            cached = None
            if conditional and self.cache is not None and method == "GET" and purpose in ROBOTS_PURPOSES:
                cached = self.cache.lookup(url)
                if cached and (cached["etag"] or cached["last_modified"]) and self.cache.body(cached["sha256"]):
                    if cached["etag"]:
                        request_headers["If-None-Match"] = cached["etag"]
                    if cached["last_modified"]:
                        request_headers["If-Modified-Since"] = cached["last_modified"]
                else:
                    cached = None
            response, final, redirects = self._request(parts, method, body, request_headers, purpose, budget, usage,
                                                       {}, deadline)
            now = self._clock()
            elapsed = time.monotonic() - started
            if response.status == 304 and cached is not None:
                stored = self.cache.body(cached["sha256"])
                if stored is None:
                    raise FetchError("http_error", _redact(url), "304 for a body no longer cached", status=304)
                result_headers = dict(response.headers)
                result_headers["content-type"] = cached["content_type"]
                for name, key in (("etag", "etag"), ("last-modified", "last_modified")):
                    if cached[key] and name not in result_headers:
                        result_headers[name] = cached[key]
                result = FetchResult(url=url, final_url=final.geturl(), status=cached["status"],
                                     headers=result_headers, content_type=cached["content_type"], body=stored,
                                     retrieved_at=now, retrieval_mode="cache", redirects=redirects, elapsed=elapsed,
                                     remote_ip=response.remote_ip)
                self.cache.put(result)
                self._count(final.hostname, "cache_hits")
                return result
            if not 200 <= response.status < 300:
                raise FetchError("http_error", _redact(final.geturl()), f"HTTP {response.status}",
                                 status=response.status)
            result = FetchResult(url=url, final_url=final.geturl(), status=response.status, headers=response.headers,
                                 content_type=_media_type(response.headers.get("content-type")), body=response.body,
                                 retrieved_at=now, retrieval_mode="live", redirects=redirects,
                                 truncated=response.truncated, elapsed=elapsed, remote_ip=response.remote_ip)
            if (self.cache is not None and method == "GET" and purpose in ROBOTS_PURPOSES
                    and not response.truncated):
                self.cache.put(result)
            return result
        except FetchError:
            self._count(host, "errors")
            raise

    def _request(self, parts, method, body, headers, purpose, budget, usage, resolved, deadline):
        """Follow redirects manually; returns (final _Response, final SplitResult, [redirect URLs])."""
        redirects = []
        current = parts
        origin = (parts.scheme, parts.hostname, parts.port)
        while True:
            host = current.hostname
            self._check_backoff(host, current.geturl())
            addresses = self._resolve(current, resolved)      # policy refusal precedes any robots request
            interval = self.policy.min_domain_interval
            if purpose in ROBOTS_PURPOSES:
                entry = self._robots_entry(current, budget, usage, resolved, deadline)
                if not entry.allowed(current.geturl()):
                    self._count(host, "robots_blocked")
                    raise FetchError("blocked_robots", _redact(current.geturl()), entry.reason or "disallowed")
                interval = max(interval, entry.delay)
            self._throttle(host, interval)
            response = self._exchange(current, method, body, headers, addresses, purpose, budget, usage, deadline)
            if response.status not in REDIRECT_STATUSES:
                return response, current, redirects
            location = response.headers.get("location")
            if not location:
                raise FetchError("http_error", _redact(current.geturl()), "redirect without Location",
                                 status=response.status)
            if len(redirects) >= self.policy.max_redirects:
                raise FetchError("too_many_redirects", _redact(parts.geturl()),
                                 f"more than {self.policy.max_redirects} redirects")
            target = urljoin(current.geturl(), location.strip()).split("#", 1)[0]
            following = check_url(target, self.policy)
            if current.scheme == "https" and following.scheme == "http":
                raise _refuse(target, "https to http downgrade")
            if response.status == 303 or (response.status in (301, 302) and method == "POST"):
                method = "GET" if method != "HEAD" else "HEAD"
                body = None
                headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
            if (following.scheme, following.hostname, following.port) != origin:
                headers = {k: v for k, v in headers.items() if k.lower() not in _CREDENTIAL_HEADERS}
            redirects.append(following.geturl())
            current = following

    def _check_backoff(self, host, url):
        now = self._clock()
        with self._lock:
            until = self._backoff.get(host)
        if until is not None and now < until:
            raise FetchError("rate_limited", _redact(url), "host backoff in force", retry_after=until - now)

    def _set_backoff(self, host, seconds):
        until = self._clock() + seconds
        with self._lock:
            self._backoff[host] = max(until, self._backoff.get(host, 0.0))
        self._count(host, "backoffs")

    def _throttle(self, host, interval):
        """Reserve the next request slot for host: at least `interval` seconds after the previous one."""
        with self._lock:
            now = self._clock()
            last = self._last_request.get(host)
            slot = now if last is None else max(now, last + interval)
            self._last_request[host] = slot
        wait = slot - now
        if wait > 0:
            self._sleep(wait)

    def _resolve(self, parts, resolved):
        host, port = parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)
        key = (host, port)
        if key in resolved:
            return resolved[key]
        try:
            ipaddress.ip_address(host)
            answers = [host]                     # literal, already validated by check_url
        except ValueError:
            try:
                answers = [str(a) for a in self._resolver(host, port)]
            except (OSError, UnicodeError) as error:
                raise FetchError("dns_failure", _redact(parts.geturl()), type(error).__name__) from None
        if not answers:
            raise FetchError("dns_failure", _redact(parts.geturl()), "no addresses")
        for answer in answers:
            reason = check_address(answer)
            if reason:
                raise _refuse(parts.geturl(), f"{host} resolves to a forbidden address: {reason}")
        resolved[key] = answers
        return answers

    def _exchange(self, parts, method, body, headers, addresses, purpose, budget, usage, deadline) -> _Response:
        """One HTTP exchange over a pinned connection. Reads the body only for 2xx responses."""
        url = parts.geturl()
        host, port = parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)
        _charge(budget, usage, requests=1)
        self._count(host, "requests")
        chosen = []

        def opener():
            last = None
            for ip in addresses:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("deadline passed before connecting")
                try:
                    sock = self._connector(ip, port, min(remaining, self.policy.timeout))
                except (TimeoutError, socket.timeout):
                    raise
                except OSError as error:
                    last = error
                    continue
                sock.settimeout(max(0.01, deadline - time.monotonic()))
                chosen.append(ip)
                return sock
            raise ConnectionError(f"could not connect to any validated address ({type(last).__name__})")

        remaining = max(0.01, deadline - time.monotonic())
        if parts.scheme == "https":
            conn = _PinnedHTTPS(host, port, opener=opener, timeout=remaining, context=self._ssl)
        else:
            conn = _PinnedHTTP(host, port, opener=opener, timeout=remaining)
        target = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~") + (
            "?" + quote(parts.query, safe="=&%+:@/?!$'()*,;-._~") if parts.query else "")
        try:
            conn.request(method, target, body=body, headers=headers)
            raw = conn.getresponse()
            status = raw.status
            response_headers: dict = {}
            for name, value in raw.getheaders():
                name = name.lower()
                if name == "set-cookie":
                    continue
                response_headers[name] = f"{response_headers[name]}, {value}" if name in response_headers else value
            response = _Response(status, response_headers, remote_ip=chosen[0] if chosen else None)
            if status == 429 or (status == 503 and response_headers.get("retry-after")):
                wait = _parse_retry_after(response_headers.get("retry-after"), self._clock())
                if wait is None:
                    if status == 503:
                        return response
                    wait = DEFAULT_BACKOFF
                self._set_backoff(host, wait)
                raise FetchError("rate_limited", _redact(url), f"HTTP {status}", retry_after=wait, status=status)
            if 200 <= status < 300 and method != "HEAD":
                if purpose != "robots":
                    media = _media_type(response_headers.get("content-type"))
                    if not any(fnmatch.fnmatchcase(media, pattern) for pattern in self.policy.content_types):
                        raise FetchError("unsupported_type", _redact(url), media or "no content type", status=status)
                self._read_body(raw, conn, response, purpose, budget, usage, deadline, url)
            return response
        except FetchError:
            raise
        except BudgetExceeded:
            raise
        except (TimeoutError, socket.timeout):
            raise FetchError("timeout", _redact(url), f"no response within {self.policy.timeout:g}s") from None
        except (ssl.SSLError, ssl.CertificateError) as error:
            raise FetchError("tls_error", _redact(url), type(error).__name__) from None
        except http.client.HTTPException as error:
            raise FetchError("http_error", _redact(url), f"malformed response ({type(error).__name__})") from None
        except OSError as error:
            detail = type(error).__name__ + (f" ({error.strerror})" if error.strerror else "")
            raise FetchError("connect_failed", _redact(url), detail) from None
        finally:
            conn.release()

    def _read_body(self, raw, conn, response, purpose, budget, usage, deadline, url):
        limit = ROBOTS_MAX_BYTES if purpose == "robots" else self.policy.max_bytes
        may_truncate = purpose in ("page", "crawl", "robots")
        budget_left = None
        if budget is not None and usage is not None:
            budget_left = budget.max_bytes - usage.bytes
        encoding = (response.headers.get("content-encoding") or "identity").strip().lower()
        if encoding in ("gzip", "x-gzip"):
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif encoding == "deflate":
            decoder = None            # decided on the first chunk: zlib-wrapped or raw deflate
        elif encoding == "identity":
            decoder = False
        else:
            raise FetchError("decode_error", _redact(url), f"unsupported content-encoding {encoding[:40]}")
        declared = response.headers.get("content-length", "")
        if not may_truncate and declared.isdigit() and int(declared) > limit and encoding == "identity":
            raise FetchError("too_large", _redact(url), f"declared body of {declared} bytes exceeds {limit}")
        out = bytearray()
        wire = 0
        over = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            if conn.pinned is not None:
                conn.pinned.settimeout(remaining)
            amount = CHUNK
            if decoder is False:
                amount = min(amount, limit - len(out) + 1)
            if budget_left is not None:
                amount = min(amount, budget_left - wire + 1)
            if amount <= 0:
                amount = 1
            chunk = raw.read(amount)
            if not chunk:
                break
            wire += len(chunk)
            if budget_left is not None and wire > budget_left:
                raise BudgetExceeded("bytes", usage.bytes + wire, budget.max_bytes)
            if decoder is None:
                decoder = zlib.decompressobj(zlib.MAX_WBITS if (chunk[0] & 0x0F) == 8 and
                                             int.from_bytes(chunk[:2], "big") % 31 == 0 else -zlib.MAX_WBITS)
            try:
                data = chunk if decoder is False else decoder.decompress(chunk, limit - len(out) + 1)
            except zlib.error:
                raise FetchError("decode_error", _redact(url), "corrupt compressed body") from None
            out += data
            if len(out) > limit or wire > limit or (decoder is not False and decoder.unconsumed_tail):
                over = True       # decoded cap (decompression bomb) or wire cap (endless empty deflate blocks)
                break
        if decoder not in (None, False) and not over:
            try:
                out += decoder.flush()
            except zlib.error:
                raise FetchError("decode_error", _redact(url), "corrupt compressed body") from None
            over = len(out) > limit
        _charge(budget, usage, bytes=wire)
        self._count(_host(url), "bytes", wire)
        if over:
            if not may_truncate:
                raise FetchError("too_large", _redact(url), f"body exceeds {limit} bytes")
            del out[limit:]
            response.truncated = True
        response.body = bytes(out)
        response.wire = wire

    def _robots_entry(self, parts, budget, usage, resolved, deadline) -> _Robots:
        host = parts.hostname
        now = self._clock()
        with self._lock:
            entry = self._robots.get(host)
            host_lock = self._robots_locks.setdefault(host, threading.Lock())
        if entry is not None and (now - entry.fetched_at < ROBOTS_TTL):
            return entry
        with host_lock:
            now = self._clock()
            with self._lock:
                entry = self._robots.get(host)
            if entry is not None and (now - entry.fetched_at < ROBOTS_TTL):
                return entry
            stored = self.cache.robots(host) if self.cache is not None else None
            if stored is not None and now - stored[1] < ROBOTS_TTL:
                entry = _robots_from_text(stored[0], stored[1])
            else:
                entry = self._fetch_robots(parts, budget, usage, resolved, deadline)
            with self._lock:
                self._robots[host] = entry
            return entry

    def _fetch_robots(self, parts, budget, usage, resolved, deadline) -> _Robots:
        host = parts.hostname
        robots = urlsplit(urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", "")))
        headers = {"User-Agent": self.policy.user_agent, "Accept": "text/plain,*/*;q=0.5",
                   "Accept-Encoding": "gzip, deflate", "Connection": "close"}
        try:
            response, _final, _redirects = self._request(robots, "GET", None, headers, "robots", budget, usage,
                                                         resolved, deadline)
        except (BudgetExceeded, FetchError) as error:
            if isinstance(error, BudgetExceeded) or error.code in ("rate_limited", "timeout"):
                raise
            return _robots_disallow_all(self._clock(), f"robots.txt unavailable ({error.code})")
        now = self._clock()
        if 200 <= response.status < 300:
            text = response.body.decode("utf-8", "replace")
            if self.cache is not None:
                self.cache.put_robots(host, text, now)
            return _robots_from_text(text, now)
        if 400 <= response.status < 500:
            if self.cache is not None:
                self.cache.put_robots(host, "", now)
            return _robots_from_text("", now)
        return _robots_disallow_all(now, f"robots.txt answered HTTP {response.status}")

