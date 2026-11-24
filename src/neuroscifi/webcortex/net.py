"""SafeFetcher: the only path from an individual to the public web (ARCHITECTURE §3, §4.1; ADR-024).

Security properties (each one has a test in tests/webcortex/test_net.py)
- Address policy: every resolved address must be globally routable. Loopback, RFC 1918, link-local (incl. the
  169.254.169.254 / fd00:ec2::254 metadata endpoints), CGNAT, multicast, reserved, unspecified, documentation and
  benchmark ranges, IPv6 ULA and site-local are refused, and so are IPv6 forms that embed a refused IPv4 address
  (IPv4-mapped, IPv4-compatible, 6to4, Teredo, NAT64); the local-use NAT64 prefix 64:ff9b:1::/48 is refused
  outright. If ANY answer for a host is refused the host is refused.
- URL policy: http/https only, allow-listed ports, no userinfo, no non-canonical numeric hosts (0x7f000001,
  2130706433, 127.1; checked again after IDNA mapping, so fullwidth digits and ideographic dots cannot spell one),
  IDNA-encodable hosts only, deny-listed domains and their subdomains, at most 2048 chars.
- DNS pinning: a host is resolved once per fetch (shared by its robots.txt request and redirect hops back to it);
  the socket connects to a validated address and is never re-resolved, so DNS rebinding cannot swap the target.
  TLS verifies the certificate against the original hostname; the Host header carries the original hostname.
- Redirects are followed manually (at most policy.max_redirects); every hop is re-checked (URL policy, resolution,
  address policy, robots), https -> http downgrades are refused, 303 (and 301/302 after POST) become GET, and
  credentials headers (Authorization, Cookie, a provider API key header) are dropped when the origin changes.
- Limits: total wall-clock timeout per fetch, enforced by a per-exchange watchdog that shuts the socket down at
  the deadline (a server trickling bytes in the status line, headers or body cannot outlive it), streamed body
  cap (policy.max_bytes; policy.max_api_bytes for purpose='api'; pages/crawls are truncated and flagged,
  api/feed/subresource responses raise too_large),
  gzip/deflate decoded under the same cap (decompression bombs stop at the cap), content-type allow-list checked
  before the body is read.
- Politeness: robots.txt for purposes page/feed/crawl/subresource, evaluated by RobotsRules (RFC 9309: group
  selection by product token, longest-match Allow/Disallow with Allow winning ties, `*` and `$`, percent-encoding
  normalisation, 500 KiB parse cap), scoped to scheme + host + port. 4xx => allow, 5xx/unreachable => disallow
  that origin for this process; parsed files are cached 24 h per origin in memory and the PublicCache; timeouts and
  429 are not remembered. Per-host throttle: the gap between two requests to a host is the largest of
  min_domain_interval, the host's host_intervals entry (defaults: export.arxiv.org 3 s, eutils.ncbi.nlm.nih.gov
  0.34 s) and Crawl-delay; purpose='subresource' uses subresource_interval (0.1 s) and host_intervals only. The
  fetch deadline starts after the throttle wait; a wait longer than policy.timeout is not slept:
  FetchError('rate_limited', retry_after=wait). Retry-After backoff on 429/503 (requests inside a backoff fail
  without touching the network) is kept in memory and in the PublicCache, so a new fetcher sharing the cache
  honours it.
- Purposes: page, feed, crawl (robots, cached), api (no robots, never cached, own size cap, POST allowed), robots,
  subresource (browser sub-requests only: robots and all limits apply, script/style types allowed in addition,
  never cached, never truncated, never extracted as documents).
- Conditional GET: a 304 to a cached validator returns the cached body with retrieval_mode='live' (the origin just
  confirmed it is current) and the header REVALIDATED_HEADER ('x-nsf-revalidated': '304').
- Budget: one request is charged before every network call (robots and redirect hops included); body bytes are
  charged after reading, and reading stops before the byte budget could be exceeded (BudgetExceeded propagates).
  Identical concurrent GETs are coalesced: a follower is charged its own request(s) and the body length on its own
  budget, and when the leader fails with anything but a FetchError (its budget, or the stop its gate raised) the
  follower fetches for itself.
- Read-ahead (speculate): a page may be fetched on its own thread before the caller asks for it, under every rule
  above (address, robots, throttle, backoff, size, time). Its charges are recorded, not applied, and its public-cache
  and robots.txt writes are held back; the fetch() that claims it applies them in order only when a fetch made at
  that moment would have sent the same requests and seen the same answers (same policy, backoff, robots entries
  and cached validators; the budget stopping it at the same point), else it fetches again. An unused one (abandon)
  is never charged, cached or remembered; what it sent counts in Usage.prefetch_unused_*. Throttle slots and
  Retry-After backoff it caused apply at once. The fetches ahead of one task share that task's budget: a request
  is sent, and a body byte read, only while what the task was charged, what its earlier fetches ahead sent unused
  and what its fetches ahead in flight hold (SafeFetcher._ahead) leave room in max_requests / max_bytes. At the
  byte limit a fetch ahead reads one byte more, as the reader's own read does, to learn that the body goes on.
  A fetch ahead is started only when that room covers a typical fetch (TYPICAL_FETCH: robots.txt and the page),
  which it then holds from the start. One that runs out of room at a later request (a redirect chain) stops before
  that request; its claim replays what it sent and goes on from that hop on the task's own budget (nothing is
  fetched twice; a stop inside a robots.txt walk that has already sent a request is the exception: that fetch is
  not continued, and its claim fetches as usual). The unused part is what a fetch ahead sent before it became
  unusable (the policy changed, a back-off, cut by the reader reaching its site, a page the reader passed by).
  It has an allowance of its own (speculate(allowance=...), Budget.unused_allowance: a tenth of the budget, never
  less than a full window needs). Requests: what the task's fetches ahead have sent unused, plus what those not
  yet claimed or abandoned have sent or hold, stays within it at every moment: a fetch ahead is not started
  without room for a typical fetch there, and one under way stops before a request that would pass it and is
  continued by its claim, exactly like one that ran out of the budget's room. A request charged and not yet known
  to have gone out when its fetch is abandoned stays held until its exchange ends, so it is in the account all
  along, as held or as unused. Bytes: no fetch ahead is started once the bytes received ahead and not used have
  reached the allowance's byte part; those in flight then still finish, each within the document limit and the
  task's max_bytes. So: charged <= max_requests, sent ahead unused <= the allowance's requests, and everything a
  task sends is at most max_requests plus its unused allowance; with the allowance spent nothing is fetched ahead
  and the task reads one page at a time. (A caller that gives no allowance keeps the older bound: unused <=
  max_requests, at most twice max_requests in all.)
- Stops (gated, speculate(wanted=...)): before every network step of a fetch the fetcher asks whether the fetch
  is still wanted: before each name lookup, before each connection is opened (after the politeness wait) and once
  more when the connection is open, immediately before the request is written on it (robots.txt, the page, every
  redirect hop). The reader's own fetches ask the gate of the calling thread (gated()), a read-ahead fetch asks the
  one it was started with, and a read-ahead fetch also stops once it is abandoned or the fetcher's policy was
  replaced. So no lookup starts and no request is sent after the stop its gate reports; a connection being opened
  when the stop lands may complete its handshake, and it is closed with nothing sent on it and no request counted.
  A request already on the wire is cut (the connection is shut down): at once when a read-ahead fetch is
  abandoned, and within STOP_POLL seconds of the stop otherwise, because while an exchange is on the wire its
  watchdog asks too (a read-ahead fetch's own gate; for the reader's fetch the `watch` given to gated(), which
  must be callable from another thread). What had arrived of a cut read-ahead request counts as unused. A claim
  waiting for its read-ahead fetch asks the caller's gate every STOP_POLL seconds as well. A request charged and
  then never written (the stop came during the handshake) is not counted, on either path.
  A read-ahead fetch fails closed: a gate it cannot read is a stop. The reader's `watch` does not: a watch that
  cannot be read is no answer (the reader's own gate still fails closed before every step), and a cut its gate
  does not confirm (the stop was withdrawn within the poll) is FetchError('interrupted'), which is nobody's
  verdict: no robots.txt entry, back-off or cache entry is recorded from it, and a caller sharing the fetch
  fetches for itself.
- One exchange per host for fetches ahead: a host is busy while any exchange with it is in progress in this
  fetcher (one process). A read-ahead fetch that reaches a busy host (a redirect onto the site the reader is on,
  or onto another fetch ahead's) stops there, before the request; its claim goes on from that hop at the reader's
  turn (nothing is fetched twice). When the reader's own fetch reaches a host a read-ahead fetch is talking to,
  that fetch is cut and not used. The reader's own fetches are not held back by one another (as before).
- Numeric headers (Retry-After, Content-Length) accept ASCII digits only.
- Privacy: bodies and full query strings are never logged; purpose='api' responses are never cached (API URLs
  carry search queries, ADR-023).

Everything that touches the outside world is injectable: `resolver(host, port) -> [ip]`,
`connector(ip, port, timeout) -> socket`, `clock()` and `sleep(seconds)` (tests use fixtureweb and a fake clock).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
import email.utils
import fnmatch
import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from urllib.parse import SplitResult, quote, urljoin, urlsplit, urlunsplit
import weakref
import zlib

from .config import USER_AGENT
from .types import Budget, BudgetExceeded, FetchError, FetchResult

CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "application/json", "application/*+json",
                 "application/xml", "text/xml", "application/atom+xml", "application/rss+xml", "application/pdf")
SUBRESOURCE_TYPES = ("application/javascript", "text/javascript", "application/ecmascript", "text/ecmascript",
                     "application/x-javascript", "text/css")
PURPOSES = ("page", "api", "feed", "crawl", "robots", "subresource")
ROBOTS_PURPOSES = ("page", "feed", "crawl", "subresource")
CACHED_PURPOSES = ("page", "feed", "crawl")
REVALIDATED_HEADER = "x-nsf-revalidated"   # set to "304" on a result whose cached body the origin just confirmed
HOST_INTERVALS = {"export.arxiv.org": 3.0,           # arXiv API terms: at most one request every 3 seconds
                  "eutils.ncbi.nlm.nih.gov": 0.34}   # NCBI E-utilities: at most 3 requests per second without a key
REDIRECT_STATUSES = (301, 302, 303, 307, 308)
MAX_URL_LENGTH = 2048
ROBOTS_TTL = 24 * 3600.0
ROBOTS_MAX_BYTES = 512 * 1024          # fetch cap for robots.txt bodies
ROBOTS_PARSE_BYTES = 500 * 1024        # RFC 9309 §2.5: parsers must accept at least 500 KiB; the rest is ignored
MAX_ROBOTS_PATTERN = 8 * MAX_URL_LENGTH  # longer allow/disallow values cannot be paths and are ignored
MAX_SITEMAPS = 100
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
_CREDENTIAL_HEADERS = {"authorization", "cookie", "x-subscription-token"}   # the last: Brave Search API key
_DIGITS = re.compile(r"[0-9]+")         # ASCII only: str.isdigit() accepts '²' and other Unicode digits
_CHARGE_LOCK = threading.Lock()
# a read-ahead fetch reads its body unbounded by the task's byte budget (its own page cap still applies); the budget
# is applied when the reader claims it (SafeFetcher._commit), exactly where a fetch made then would have stopped
_UNBOUNDED = Budget(max_requests=1 << 62, max_bytes=1 << 62)
STOP_POLL = 0.25      # seconds between two looks at the gate while a request is on the wire or a claim waits
TYPICAL_FETCH = 2     # requests of an ordinary page fetch (robots.txt and the page): what a fetch ahead holds at start


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
    # host (or parent domain) -> minimum seconds between requests; see SafeFetcher._interval for how it combines
    host_intervals: dict = field(default_factory=lambda: dict(HOST_INTERVALS), hash=False)
    max_api_bytes: int = 16_000_000    # body cap for purpose='api' (large JSON metadata); over it -> too_large
    subresource_interval: float = 0.1  # same-host gap for purpose='subresource' (browser sub-requests)


# ---- address and URL policy -----------------------------------------------------------------------------------

_V4_EXTRA = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "100.64.0.0/10", "192.0.0.0/24", "192.0.2.0/24", "198.18.0.0/15", "198.51.100.0/24",
    "203.0.113.0/24", "240.0.0.0/4", "255.255.255.255/32")]
_V6_EXTRA = [ipaddress.ip_network(n) for n in (
    "fc00::/7", "fec0::/10", "fe80::/10", "ff00::/8", "2001:db8::/32", "3fff::/20", "100::/64", "::/128", "::1/128",
    "fd00:ec2::254/128")]
_NAT64 = (ipaddress.ip_network("64:ff9b::/96"),)
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")   # RFC 8215 local-use: translator unknown, refused outright
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
    if addr in _NAT64_LOCAL:
        return "local-use NAT64 address"
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


def _numeric_host(host: str, url: str):
    """The IP address a host spells (IPv6 literal or canonical dotted IPv4), None for a name; non-canonical numeric
    IPv4 spellings (0x7f000001, 2130706433, 127.1, 0177.0.0.1) are refused."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if _NUMERIC_LABEL.match(host.split(".")[-1]):
        try:
            dotted = socket.inet_ntoa(socket.inet_aton(host))
        except (OSError, ValueError):
            raise _refuse(url, "invalid numeric host") from None
        if dotted != host:
            raise _refuse(url, "non-canonical numeric IPv4 host")
        return ipaddress.ip_address(dotted)
    return None


def check_url(url: str, policy: NetPolicy) -> SplitResult:
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
    literal = _numeric_host(host, url)
    ascii_host = None
    if literal is None:
        try:
            ascii_host = host.encode("idna").decode("ascii").lower() if not host.isascii() else host.lower()
        except UnicodeError:
            raise _refuse(url, "host is not IDNA-encodable") from None
        # IDNA maps fullwidth digits and ideographic dots to ASCII: "１２７。０。０。１" is 127.0.0.1
        literal = _numeric_host(ascii_host, url)
    if literal is not None:
        if isinstance(literal, ipaddress.IPv6Address) and literal.scope_id:
            raise _refuse(url, "scoped IPv6 literal")
        reason = check_address(str(literal))
        if reason:
            raise _refuse(url, reason)
        netloc_host = f"[{literal}]" if literal.version == 6 else str(literal)
    else:
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
    rules: "RobotsRules"
    fetched_at: float
    delay: float = 0.0
    sitemaps: tuple = ()
    reason: str = ""

    def allowed(self, url: str) -> bool:
        return self.rules.allowed(url)


class _Flight:
    def __init__(self):
        self.event = threading.Event()
        self.result = None
        self.error = None


class _Abandoned(BudgetExceeded):
    """Raised inside a read-ahead fetch that is no longer wanted (abandoned, the policy replaced, its gate said
    stop), at its next network step, so it sends no further request."""

    def __init__(self, why="abandoned"):
        super().__init__("read-ahead", why, "unused")


class _OverBudget(BudgetExceeded):
    """Raised inside a read-ahead fetch at a point it may not pass on its own. Before a request, with nothing of
    that request sent: the task's budget has no room for it (`kind` 'requests'), or the site is busy with another
    exchange (`kind` 'busy'). Or while a body is read: the task's byte budget has no room (`kind` 'bytes': the read
    stopped one byte past the room, and a 'cut' event says what had arrived). Room is counted for the task as a
    whole: charged, sent unused before, and held by its fetches ahead in flight.

    Stopped before a request, the fetch is continued by its claim: what it sent is replayed on the task's Usage and
    the walk goes on from that hop as the reader's own fetch, on the task's own budget (_fits, _commit). Stopped in
    a body, it is used only when a read made at that moment would stop at the same place, else fetched again.
    Never raised out of a robots.txt walk that has sent a request: _fetch_robots turns that into _Abandoned (the
    walk's own progress is not carried over, and going on from the page's hop would ask for the file twice)."""

    def __init__(self, kind="requests"):
        super().__init__("read-ahead", kind, "budget")
        self.kind = kind


class _Speculation:
    """A page fetch made ahead of its turn (SafeFetcher.speculate), on its own thread.

    It stands in for the task's Usage during the fetch: every charge is recorded in order and none is applied, and
    the fetch's writes to shared state (the public-cache body, robots.txt entries) are held back as events. What it
    saw of shared state (the cached validators of its URL, the robots entries it used, host backoff) is kept so the
    claim (SafeFetcher._claim) can tell whether a fetch made at that moment would have sent the same requests;
    only then are the recorded charges applied to the task's Usage, in order, and the held writes made. Hosts it
    contacted keep their throttle slots and any Retry-After backoff at once: politeness never waits for a claim.

    `wanted(url)` is the task's gate, asked before every network step (ask): a reason to stop, or None. `budget` is
    the task's budget: a request the task could not be charged for now is not sent (_OverBudget). `allowance` is
    the task's unused allowance (Budget.unused_allowance; with a budget only): a request that would take what the
    task's fetches ahead have sent unused, or have sent and hold in flight, past its request part is not sent
    either (_OverBudget, the same stop)."""
    bytes = 0       # _read_body bounds a read by budget.max_bytes - usage.bytes: unbounded here (_UNBOUNDED)

    def __init__(self, url, usage, fetcher, *, budget=None, wanted=None, purpose="page", allowance=None):
        self.url, self.usage, self.fetcher, self.policy = url, usage, fetcher, fetcher.policy
        self.budget, self.wanted, self.purpose = budget, wanted, purpose
        self.allowance = allowance if budget is not None else None
        self.reserved = 0                 # requests held from the start (TYPICAL_FETCH), used before any more is taken
        self.recorded = 0                 # requests recorded so far
        self.hop = None                   # the redirect hop under way (_request): where a claim goes on from
        self.lock = threading.Lock()
        self.finished = threading.Event()
        self.abandoned = False
        self.halted = False               # stopped for good without being abandoned (halt): sends nothing more
        self.stopped = None               # why the fetch ended itself early (its gate, the policy), else None
        self.wire = None                  # the _Watchdog of the exchange on the wire, if any (abandon cuts it)
        self.pending = False              # the request recorded last is not on the wire yet (sent() clears it)
        self.held = [0, 0]                # requests and bytes this fetch holds in fetcher._ahead (release())
        # in order: ("request",) | ("bytes", wire, identity-encoded) | ("robots", key, entry, (text, at) | None)
        # | ("cache", FetchResult) | ("cut", wire, identity-encoded, declared length | None, size limit): the body
        # read stopped at the task's byte budget (last event of the fetch)
        self.events: list[tuple] = []
        self.robots: dict = {}            # robots key -> entry this fetch fetched (held back)
        self.robots_text: dict = {}       # robots key -> (text, fetched_at) for the public cache (held back)
        self.robots_seen: dict = {}       # robots key -> committed entry this fetch used
        self.backoff_seen: dict = {}      # host -> backoff time the check saw (None: none)
        self.backoff_set: dict = {}       # host -> backoff time this fetch's own 429/503 set
        self.validators = None            # the cached response a conditional GET revalidated (_validators)
        self.result = self.error = None

    def hold(self, event):
        with self.lock:
            self.events.append(event)

    def ask(self, url):
        """Before a network step (a name lookup, a request): _Abandoned unless the fetch is still wanted. Not
        wanted: abandoned by the reader, the fetcher's policy replaced (the run applied a new deny list: nothing
        goes out under the old one), or the task's gate gives a reason (cancelled, web access off, the deny list
        changed). The gate is read now, outside the lock; a gate that fails counts as a stop."""
        why = self.unwanted(url)
        if why:
            raise _Abandoned(why)

    def unwanted(self, url) -> str | None:
        """Why the fetch must not go on (kept in `stopped`), else None. Callable from any thread: the watchdog of
        the exchange on the wire asks it too."""
        with self.lock:
            why = "abandoned" if self.abandoned else self.stopped if self.halted else \
                "policy changed" if self.fetcher.policy is not self.policy else None
        if why is None and self.wanted is not None:
            try:
                why = self.wanted(url)
            except Exception as error:        # an unreadable gate must not let a request out
                why = f"gate failed ({type(error).__name__})"
        if why:
            with self.lock:
                self.stopped = self.stopped or why
        return why

    def halt(self, why) -> None:
        """Stop the fetch for good without abandoning it: it sends nothing more and its request on the wire is cut.
        It stays for its claim, which will not use it (its error is not a FetchError), and what it sent is counted
        when the reader abandons it. Used when the reader's own fetch reaches the host it is talking to."""
        with self.lock:
            self.halted = True
            self.stopped = self.stopped or why
            wire = self.wire
        if wire is not None:
            wire.abort()

    def charge(self, _budget, *, requests=0, bytes=0, identity=True, **_others):
        """Record a charge in order. Once abandoned, or once the fetcher's policy changed, refuse it so no further
        request is sent; bytes that already arrived then count as unused. A request the task's request budget has
        no room for is refused as _OverBudget and not sent. Room is the task's, not this fetch's: what the task was
        charged, what its fetches ahead sent unused, and what all its fetches ahead in flight hold. The same for
        the task's unused allowance, which the last two alone must stay within."""
        with self.lock:
            if self.abandoned:
                if bytes:
                    self.usage.charge_unused(bytes=bytes)
                raise _Abandoned()
            if requests and (self.halted or self.fetcher.policy is not self.policy):
                raise _Abandoned(self.stopped or "policy changed")
            if requests and self.budget is not None:
                with self.fetcher._lock:
                    held = self.fetcher._ahead.setdefault(id(self.usage), [0, 0])
                    # what it held from the start is used first; only requests beyond that take new room
                    more = max(0, self.recorded + requests - self.reserved) - max(0, self.recorded - self.reserved)
                    ahead = self.usage.prefetch_unused_requests + held[0] + more    # sent unused, or sent and held
                    if more and (self.usage.requests + ahead > self.budget.max_requests
                                 or (self.allowance is not None and ahead > self.allowance[0])):
                        raise _OverBudget("requests")
                    held[0] += more
                    self.held[0] += more
            self.events.extend([("request",)] * requests)
            if requests:
                self.recorded += requests
                self.pending = True
            if bytes:
                self.events.append(("bytes", bytes, identity))

    def take(self, wanted: int) -> int:
        """Before a body read of up to `wanted` bytes: how many of them the task's byte budget has room for (0: none),
        now held for this fetch. Room is the task's: max_bytes less what it was charged, what its fetches ahead
        received unused and what all its fetches ahead in flight hold. Without a budget: `wanted`."""
        with self.lock:
            if self.abandoned or self.budget is None:     # abandoned: its connection is shut, the read ends itself
                return wanted
            with self.fetcher._lock:
                held = self.fetcher._ahead.setdefault(id(self.usage), [0, 0])
                room = self.budget.max_bytes - self.usage.bytes - self.usage.prefetch_unused_bytes - held[1]
                granted = max(0, min(wanted, room))
                held[1] += granted
                self.held[1] += granted
            return granted

    def give_back(self, count: int) -> None:
        """Bytes taken for a read that did not arrive (a short read, the end of the body)."""
        if count <= 0 or self.budget is None:
            return
        with self.fetcher._lock:
            count = min(count, self.held[1])
            held = self.fetcher._ahead.get(id(self.usage))
            if held is not None:
                held[1] -= count
            self.held[1] -= count

    def cut(self, wire, identity, declared, limit):
        """The body read stopped at the task's byte budget: `wire` bytes of it had arrived (one more than there was
        room for). Recorded for the claim (_fits) and for the count of unused bytes; then _OverBudget('bytes')."""
        with self.lock:
            if self.abandoned:
                self.usage.charge_unused(bytes=wire)
                raise _Abandoned()
            self.events.append(("cut", wire, identity, declared, limit))
        raise _OverBudget("bytes")

    def release(self, keep=0) -> None:
        """Give up what this fetch holds of the task's budget in fetcher._ahead: when it is claimed (its charges are
        on the task's Usage by then) or abandoned (what it sent counts as unused by then). Harmless twice.
        `keep`: requests that stay held (abandon(): a request recorded and not yet known to have gone out is neither
        counted as unused nor given back until its exchange ends, so the task's room never looks larger than it
        is in between)."""
        with self.fetcher._lock:
            held = self.fetcher._ahead.get(id(self.usage))
            give = max(0, self.held[0] - keep)
            if held is not None:
                held[0] -= give
                held[1] -= self.held[1]
                if held[0] <= 0 and held[1] <= 0:
                    del self.fetcher._ahead[id(self.usage)]
            self.held = [self.held[0] - give, 0]

    def unsend(self) -> None:
        """The request recorded last was never written on a connection (the fetch was stopped while the connection
        was being opened, or cut before the send). It is not a request: taken out of the record and of what this
        fetch holds of the task's budget."""
        with self.lock:
            if not self.pending:
                return
            self.pending = False
            for i in range(len(self.events) - 1, -1, -1):
                if self.events[i][0] == "request":
                    del self.events[i]
                    break
            self.recorded -= 1
            with self.fetcher._lock:
                if self.recorded >= self.reserved and self.held[0] > 0:     # it had taken room beyond its start
                    self.held[0] -= 1
                    held = self.fetcher._ahead.get(id(self.usage))
                    if held is not None:
                        held[0] -= 1

    def settle(self) -> None:
        """The fetch has ended: what it held from the start and did not use goes back to the task's room."""
        with self.lock, self.fetcher._lock:
            spare = min(self.reserved - self.recorded, self.held[0])
            if spare > 0:
                self.held[0] -= spare
                held = self.fetcher._ahead.get(id(self.usage))
                if held is not None:
                    held[0] -= spare
            self.reserved = min(self.reserved, self.recorded)

    def arrived(self, wire) -> None:
        """Bytes of a body whose read was cut short (the connection was shut down under it): they arrived, so they
        count, as unused at once when the fetch is abandoned, else in the record for when it is."""
        with self.lock:
            if not self.abandoned:
                self.events.append(("bytes", wire, False))
                return
        self.usage.charge_unused(bytes=wire)

    def on_wire(self, watchdog):
        """The exchange about to use the wire (None: it ended). False when the fetch was abandoned meanwhile: the
        caller must not send. When the exchange of an abandoned fetch ends, the request abandon() had left held is
        settled: sent() has counted it as unused if it went out, and its room goes back now either way."""
        with self.lock:
            self.wire = watchdog
            ended = watchdog is None
            if ended:
                self.pending = False
            wanted = not (self.abandoned or self.halted)
            settle = ended and self.abandoned
        if settle:
            self.release()
        return wanted

    def sent(self):
        """The request recorded last went out. Abandoned in the meantime (abandon() had left it out, not knowing
        whether the cut came first): it reached the site, so it counts as unused now."""
        with self.lock:
            late = self.abandoned and self.pending
            self.pending = False
        if late:
            self.usage.charge_unused(requests=1)

    def abandon(self, events=None):
        """Drop the fetch: what it sent and received (or only `events`, the part a claim did not use) counts as
        unused prefetch on the task's Usage, a fetch still running sends nothing more, and its request on the wire
        is cut (the connection is shut down). A request recorded but not sent yet is left out; sent() counts it if
        it still goes out before the cut, and until its exchange ends (on_wire(None)) it stays held in the task's
        account, so that request is in the account at every moment, as held or as unused."""
        with self.lock:
            if self.abandoned:
                return
            self.abandoned = True
            spent = list(self.events if events is None else events)
            wire, unsent = self.wire, 1 if self.pending and events is None else 0
        if wire is not None:
            wire.abort()
        self.usage.charge_unused(requests=sum(1 for e in spent if e[0] == "request") - unsent,
                                 bytes=sum(e[1] for e in spent if e[0] in ("bytes", "cut")))
        self.release(keep=unsent)        # after the count: the task's room never looks larger than it is


def _fits(spec: _Speculation, budget, usage) -> bool:
    """Whether replaying the speculation's charges on `usage` reproduces exactly what a fetch made now would do: it
    either fits, or stops at a request or at an uncompressed body exactly where _read_body would. A compressed
    body near the byte limit does not qualify (where its read stops depends on the chunk sizes the limit sets).

    A fetch ahead that stopped itself at the task's budget (_OverBudget) may have stopped earlier than a fetch
    made at its turn would, because its room also counted what other fetches ahead held or had sent unused.
    Stopped before a request (nothing of that request was sent; also when it stopped at a busy site): it qualifies,
    and the claim goes on from that hop on the task's own budget (_commit), which refuses the request if a fetch
    made now would be refused it.
    Stopped while a body was read: it qualifies only when a read made now stops at the same place, that is:
    uncompressed, the size limit not ending the read first, and the body known to be longer than what the task
    has left now (by its declared length, or by what had already arrived); else the reader fetches for itself."""
    over = isinstance(spec.error, _OverBudget)
    resumable = over and spec.error.kind in ("requests", "busy") and spec.hop is not None
    if budget is None or usage is None:
        return not over or resumable
    requests, used = usage.requests, usage.bytes
    for event in spec.events:
        if event[0] == "request":
            if requests + 1 > budget.max_requests:
                return True
            requests += 1
        elif event[0] == "bytes":
            wire, identity = event[1], event[2]
            left = budget.max_bytes - used
            if identity and wire > left:
                return True
            if not identity and wire + CHUNK > left:
                return False
            used += wire
        elif event[0] == "cut":
            _, wire, identity, declared, limit = event
            left = budget.max_bytes - used
            return bool(identity and left <= limit and max(wire, declared or 0) > left)
    return not over or resumable                # stopped before a request: the claim goes on from there


class _Watchdog:
    """Shuts a connection down at the fetch deadline. Socket timeouts bound each read, not the exchange: a server
    that trickles one byte per gap shorter than the timeout would otherwise keep a fetch alive indefinitely. The
    watchdog holds a duplicate of the connection's descriptor (shutdown acts on the connection, so it also stops a
    TLS socket wrapped around the original), and `fired` tells the reader the EOF/error it sees is the deadline.
    abort() shuts it down at once for a fetch nobody wants any more (`aborted`); called before the connection
    exists, the connection is shut down as soon as it is armed, before anything is sent on it. With `poll` (a
    callable safe on another thread; truthy: stop) the watchdog asks every STOP_POLL seconds while the exchange is
    on the wire and aborts on a stop. A poll that raises is no answer and the watch goes on: this is the reader's
    `watch`, whose own gate fails closed before every step, and a read error must not cut a healthy request (a
    read-ahead fetch's poll never raises: _Speculation.unwanted turns a gate it cannot read into a stop)."""

    def __init__(self, deadline: float, poll=None):
        self.deadline = deadline
        self.poll = poll
        self.fired = False
        self.aborted = False
        self._lock = threading.Lock()
        self._dup = None
        self._timer = None
        self._done = False
        self._wake = threading.Event()

    def arm(self, sock) -> None:
        try:
            dup = sock.dup()
        except (AttributeError, OSError):     # an injected connector without real sockets: socket timeouts only
            return
        with self._lock:
            if self._done:
                dup.close()
                return
            self._dup = dup
            if self.aborted:
                self._shutdown()
                return
            if self.poll is None:
                self._timer = threading.Timer(max(0.0, self.deadline - time.monotonic()), self._fire)
            else:
                self._timer = threading.Thread(target=self._watch, name="nsf-fetch-watch")
            self._timer.daemon = True
            self._timer.start()

    def _watch(self) -> None:
        while True:
            remaining = self.deadline - time.monotonic()
            if self._wake.wait(max(0.0, min(STOP_POLL, remaining))):
                return                                    # disarmed
            if time.monotonic() >= self.deadline:
                self._fire()
                return
            try:
                stop = self.poll()
            except Exception:                             # no answer this time (see the class doc)
                stop = None
            if stop:
                self.abort()
                return

    def _shutdown(self) -> None:
        try:
            self._dup.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _fire(self) -> None:
        with self._lock:
            if self._done or self._dup is None:
                return
            self.fired = True
            self._shutdown()

    def abort(self) -> None:
        with self._lock:
            self.aborted = True
            if not self._done and self._dup is not None:
                self._shutdown()

    def disarm(self) -> None:
        with self._lock:
            self._done = True
            self._wake.set()
            if self._timer is not None and self.poll is None:
                self._timer.cancel()
            if self._dup is not None:
                self._dup.close()
                self._dup = None


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
    if _DIGITS.fullmatch(value):
        return min(float(value), MAX_RETRY_AFTER)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    return min(max(0.0, when.timestamp() - now), MAX_RETRY_AFTER)


# ---- robots.txt (RFC 9309) ------------------------------------------------------------------------------------

_UNRESERVED = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
_EOL = re.compile(r"\r\n|\r|\n")
_AGENT_TOKEN = re.compile(r"[A-Za-z_-]+")
_ROBOTS_KEYS = {"user-agent": "user-agent", "useragent": "user-agent", "user agent": "user-agent",
                "allow": "allow", "disallow": "disallow", "crawl-delay": "crawl-delay", "sitemap": "sitemap"}


def _robots_path(value: str, *, uri: bool = False) -> str:
    """RFC 9309 §2.2.2 normalisation, applied alike to rule patterns and request paths: octets outside printable
    ASCII are percent-encoded (UTF-8), escapes of unreserved characters are decoded, other escapes are upper-cased.
    In a request path a literal `*` or `$` becomes %2A / %24, so only a pattern's own `*` and final `$` are special
    (a `$` elsewhere in a pattern is literal)."""
    text = "".join(chr(b) if 0x20 < b < 0x7F else f"%{b:02X}" for b in value.encode("utf-8", "replace"))
    text = _ESCAPE.sub(lambda m: chr(int(m.group(1), 16)) if int(m.group(1), 16) in _UNRESERVED
                       else "%" + m.group(1).upper(), text)
    if uri:
        return text.replace("*", "%2A").replace("$", "%24")
    anchored = text.endswith("$")
    return (text[:-1] if anchored else text).replace("$", "%24") + ("$" if anchored else "")


def _robots_match(pattern: str, path: str) -> bool:
    """`*` matches any run of characters and a final `$` anchors the end; otherwise a prefix match. Leftmost
    matching of the `*`-separated segments is exact for this pattern language and never backtracks."""
    anchored = pattern.endswith("$")
    if anchored:
        pattern = pattern[:-1]
    segments = pattern.split("*")
    if not path.startswith(segments[0]):
        return False
    pos = len(segments[0])
    if len(segments) == 1:
        return not anchored or pos == len(path)
    for segment in segments[1:-1]:
        found = path.find(segment, pos)
        if found < 0:
            return False
        pos = found + len(segment)
    last = segments[-1]
    if anchored:
        return path.endswith(last) and len(path) - len(last) >= pos
    return path.find(last, pos) >= 0


def _product_token(user_agent: str) -> str:
    match = _AGENT_TOKEN.match(str(user_agent).strip())
    return match.group(0).lower() if match else ""


@dataclass(frozen=True)
class RobotsRules:
    """The robots.txt rules that apply to one product token (RFC 9309).

    - Groups: one or more `user-agent` lines followed by rule lines; a `user-agent` line after a rule starts a new
      group. The groups whose user-agent token is the product token (case-insensitive; a token that is a whole
      `-`-separated prefix of it, such as `NeuroSciFi` for `NeuroSciFi-WebCortex`, also matches) are used, the
      longest matching token winning and equal tokens being combined; otherwise the combined `*` groups; otherwise
      no rule applies.
    - Rules: the matching rule with the longest (normalised) pattern decides, `allow` winning ties; `*` is a
      wildcard, a final `$` anchors the end; patterns and the URL's path?query are compared after percent-encoding
      normalisation. `/robots.txt` itself is always allowed. Empty `allow`/`disallow` values are no rule.
    - `crawl-delay` (non-standard) is taken from the chosen groups (largest value); `sitemap` lines are global.
    - Only the first ROBOTS_PARSE_BYTES are parsed (cut at the last full line); malformed lines are ignored.
    """
    rules: tuple = ()           # ((allow: bool, normalised pattern), ...)
    delay: float = 0.0
    sitemaps: tuple = ()
    disallow_all: bool = False

    @classmethod
    def parse(cls, text: str, user_agent: str = USER_AGENT) -> "RobotsRules":
        data = str(text).encode("utf-8", "replace")
        if len(data) > ROBOTS_PARSE_BYTES:
            data = data[:ROBOTS_PARSE_BYTES]
            data = data[:max(data.rfind(b"\n"), data.rfind(b"\r"), 0)]
        product = _product_token(user_agent)
        groups: list[dict] = []
        current, in_agents, sitemaps = None, False, []
        for line in _EOL.split(data.decode("utf-8", "ignore").lstrip("﻿")):
            line = line.split("#", 1)[0].strip()
            key, sep, value = line.partition(":")
            key, value = _ROBOTS_KEYS.get(key.strip().lower()), value.strip()
            if not sep or key is None:
                continue
            if key == "sitemap":
                if value and value not in sitemaps and len(sitemaps) < MAX_SITEMAPS:
                    sitemaps.append(value)
                continue
            if key == "user-agent":
                if not in_agents:
                    current = {"agents": [], "rules": [], "delay": 0.0}
                    groups.append(current)
                    in_agents = True
                current["agents"].append("*" if value.startswith("*") else _product_token(value))
                continue
            in_agents = False
            if current is None:
                continue                     # a rule before any user-agent line belongs to no group
            if key == "crawl-delay":
                try:
                    delay = float(value)
                except ValueError:
                    continue
                if delay == delay and delay >= 0:          # not NaN
                    current["delay"] = max(current["delay"], delay)
            elif value and len(value) <= MAX_ROBOTS_PATTERN:
                current["rules"].append((key == "allow", _robots_path(value if value[0] in "/*" else "/" + value)))

        def score(agent):
            if agent == "*":
                return 0
            if agent and (agent == product or product.startswith(agent + "-")):
                return len(agent)
            return -1

        best = max((score(a) for g in groups for a in g["agents"]), default=-1)
        chosen = [g for g in groups if best >= 0 and any(score(a) == best for a in g["agents"])]
        return cls(tuple(rule for g in chosen for rule in g["rules"]),
                   max((g["delay"] for g in chosen), default=0.0), tuple(sitemaps))

    def allowed(self, url: str) -> bool:
        if self.disallow_all:
            return False
        if not self.rules:
            return True
        parts = urlsplit(url)
        if (parts.path or "/") == "/robots.txt":
            return True
        target = _robots_path((parts.path or "/") + ("?" + parts.query if parts.query else ""), uri=True)
        best, verdict = -1, True
        for allow, pattern in self.rules:
            if (len(pattern) > best or (len(pattern) == best and allow)) and _robots_match(pattern, target):
                best, verdict = len(pattern), allow
        return verdict


def _robots_from_text(text: str, fetched_at: float, reason: str = "", user_agent: str = USER_AGENT) -> _Robots:
    rules = RobotsRules.parse(text, user_agent)
    return _Robots(rules, fetched_at, min(max(rules.delay, 0.0), MAX_CRAWL_DELAY), rules.sitemaps, reason)


def _robots_key(parts: SplitResult) -> str:
    """Cache key of a robots.txt: RFC 9309 §2.3 scopes the file to scheme, host and port. The host stays the
    suffix after a '.' so PublicCache.forget_domain still finds the row; '~' cannot occur in a host name."""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return f"{parts.scheme}~{port}~.{parts.hostname}"


def _robots_disallow_all(fetched_at: float, reason: str) -> _Robots:
    return _Robots(RobotsRules(disallow_all=True), fetched_at, 0.0, (), reason)


class _Gated:
    """SafeFetcher.gated: sets the calling thread's gate (and watch), and puts the previous ones back on exit."""

    def __init__(self, local, ask, watch=None):
        self.local, self.ask, self.watch = local, ask, watch

    def __enter__(self):
        self.previous = (getattr(self.local, "ask", None), getattr(self.local, "watch", None))
        self.local.ask, self.local.watch = self.ask, self.watch
        return self

    def __exit__(self, *_exc):
        self.local.ask, self.local.watch = self.previous
        return False


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
        self._speculations: dict = {}      # (url, purpose, id(usage)) -> _Speculation not yet claimed or abandoned
        self._gate = threading.local()     # .ask: the calling thread's gate while inside gated()
        self._busy: dict = {}              # host -> [exchanges in progress, the fetches ahead among them]
        # id(usage) -> [requests, bytes] held by that task's fetches ahead not yet claimed or abandoned: the part
        # of the task's budget they have used or reserved (_Speculation.charge / take / release)
        self._ahead: dict = {}

    # ---- public API -----------------------------------------------------------------------------------------
    def fetch(self, url, *, method="GET", body=None, headers=None, purpose="page", conditional=True, budget=None,
              usage=None) -> FetchResult:
        method = str(method).upper()
        if purpose not in PURPOSES:
            raise FetchError("blocked_policy", _redact(str(url)), f"unknown purpose {purpose!r}")
        if method not in ("GET", "HEAD", "POST") or (method == "POST" and purpose != "api"):
            raise FetchError("blocked_policy", _redact(str(url)), f"method {method} not allowed for {purpose}")
        if self._speculations and method == "GET" and body is None and not headers and conditional \
                and usage is not None:
            spec = self._claim(url, purpose, budget, usage)
            if spec is not None:
                return self._commit(spec, budget, usage)
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
            if flight.error is not None and (not isinstance(flight.error, FetchError)
                                             or flight.error.code == "interrupted"):
                # the leader's budget, or the stop its gate raised, is not this caller's: fetch on this caller's own
                return self._fetch(url, method, body, headers, purpose, conditional, budget, usage)
            if flight.error is not None:
                raise flight.error
            result = flight.result
            # a follower is charged what its own request would have cost (request + redirect hops, body bytes)
            _charge(budget, usage, requests=1 + len(result.redirects), bytes=len(result.body))
            return replace(result, headers=dict(result.headers), redirects=list(result.redirects))
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

    def gated(self, ask, watch=None):
        """Context manager: while inside it, every fetch this thread makes calls `ask(url)` before each of its
        network steps (a name lookup, opening a connection, writing the request on it: robots.txt, the page, every
        redirect hop) with the URL about to be contacted. `ask` raises to stop the fetch there; whatever it raises
        leaves fetch() unchanged, except that a FetchError raised while robots.txt is being fetched is taken for
        that file's failure, so a gate should raise its own exception type (orchestrator._Run.still_wanted). A
        connection opened for a request that is then not sent is closed and the request is not charged. A caller
        waiting on the same fetch made by another thread then fetches for itself.

        `watch(url)` (optional; called from another thread; a reason or None) is asked every STOP_POLL seconds
        while a request of this thread is on the wire: on a reason the request is cut and `ask` is asked on the
        calling thread, which raises the stop."""
        return _Gated(self._gate, ask, watch)

    def _ask(self, usage, url) -> None:
        """Before a network step of a fetch: is the fetch still wanted? (module doc, Stops)."""
        if isinstance(usage, _Speculation):
            usage.ask(url)
            return
        ask = getattr(self._gate, "ask", None)
        if ask is not None:
            ask(url)

    def speculate(self, url, *, purpose="page", usage=None, budget=None, wanted=None, allowance=None) -> bool:
        """Start fetching `url` now, on its own thread, for a fetch(url, purpose=purpose, usage=usage) the caller
        expects to make soon (the reader's read-ahead, orchestrator._Run.prefetch). Nothing of it is charged,
        cached or remembered until that fetch claims it, and it is used only when a fetch made at that moment would
        have sent the same requests and seen the same answers; otherwise the claim fetches again and this one
        counts as unused. abandon() drops it. Every policy, robots, throttle, backoff and size rule applies to it as
        to any fetch. `wanted(url)`: the task's gate, called on the fetch's thread before each of its network steps;
        a reason (or an exception) stops the fetch there. `budget`: the task's budget, shared by all its fetches
        ahead; the fetch is started only when it has room for a typical fetch (TYPICAL_FETCH requests), which it
        holds from then on. `allowance` (with a budget): the task's unused allowance, (requests, bytes)
        (Budget.unused_allowance): the same room must be left in its request part by what the task's fetches ahead
        have sent unused and what those in flight have sent or hold, and nothing is started once the bytes received
        ahead and not used have reached its byte part. Returns whether a fetch was started (not for purposes that
        are never cached, not twice, not without that room)."""
        if purpose not in CACHED_PURPOSES or usage is None:
            return False
        key = (url, purpose, id(usage))
        spec = _Speculation(url, usage, self, budget=budget, wanted=wanted, purpose=purpose, allowance=allowance)
        allowance = spec.allowance
        with self._lock:
            if key in self._speculations:
                return False
            if budget is not None:
                held = self._ahead.setdefault(id(usage), [0, 0])
                ahead = usage.prefetch_unused_requests + held[0] + TYPICAL_FETCH
                if usage.requests + ahead > budget.max_requests or (allowance is not None and (
                        ahead > allowance[0] or usage.prefetch_unused_bytes >= allowance[1])):
                    if held[0] <= 0 and held[1] <= 0:
                        del self._ahead[id(usage)]
                    return False
                held[0] += TYPICAL_FETCH
                spec.held[0] = spec.reserved = TYPICAL_FETCH
            self._speculations[key] = spec

        def run():
            try:
                spec.result = self._fetch(url, "GET", None, None, purpose, True, _UNBOUNDED, spec)
            except Exception as error:     # kept for the claim, which raises it on the reader's thread
                spec.error = error
            finally:
                spec.settle()
                spec.finished.set()
        threading.Thread(target=run, name="nsf-read-ahead", daemon=True).start()
        return True

    def abandon(self, url, *, purpose="page", usage=None) -> None:
        """Drop a read-ahead fetch the caller will not claim; what it sent counts as unused (Usage.charge_unused).
        Nothing when it was already claimed or never started."""
        with self._lock:
            spec = self._speculations.pop((url, purpose, id(usage)), None)
        if spec is not None:
            spec.abandon()

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

    # ---- read-ahead (speculate) ------------------------------------------------------------------------------
    def _claim(self, url, purpose, budget, usage):
        """The finished read-ahead fetch of (url, purpose) for `usage` when a fetch made now would do exactly what it
        did (_unchanged, _fits), else None after abandoning it (the caller then fetches as usual)."""
        with self._lock:
            spec = self._speculations.pop((url, purpose, id(usage)), None)
        if spec is None:
            return None
        if not self._await(spec, url) or not self._unchanged(spec) or not _fits(spec, budget, usage):
            # abandon() cuts a request still on the wire, so the fetch the caller makes next is the only one talking
            # to that host; the short wait lets the cut fetch's thread finish first (it sends nothing either way)
            spec.abandon()
            spec.finished.wait(1.0)
            return None
        return spec

    def _await(self, spec, url) -> bool:
        """Wait for a read-ahead fetch being claimed (at most _claim_wait()); False when it did not finish. Every
        STOP_POLL seconds the caller's gate is asked: on a stop the fetch is abandoned (its request on the wire is
        cut) and the stop propagates."""
        end = time.monotonic() + self._claim_wait()
        while not spec.finished.wait(STOP_POLL):
            if time.monotonic() >= end:
                return False
            try:
                self._ask(None, url)
            except BaseException:
                spec.abandon()
                spec.finished.wait(1.0)
                raise
        return True

    def _claim_wait(self) -> float:
        """The longest a claim waits for its read-ahead fetch: robots.txt and page, their waits, every hop."""
        return self.policy.timeout * 4 * (self.policy.max_redirects + 1) + 5

    def _unchanged(self, spec) -> bool:
        """Whether a fetch of spec.url made now would see what the read-ahead fetch saw: the same policy, the same
        host backoff (or the one its own 429/503 set), the same committed robots entries and still none for the
        origins whose robots.txt it fetched, the same cached validators. An outcome decided locally by when it was
        asked (a throttle slot beyond the timeout, a backoff in force) or an internal error never qualifies."""
        if spec.policy is not self.policy:
            return False
        error = spec.error
        if error is not None and not isinstance(error, (FetchError, _OverBudget)):
            return False
        if isinstance(error, FetchError) and error.code == "rate_limited" and error.status is None:
            return False
        for host, seen in spec.backoff_seen.items():
            if self._backoff_until(host) not in (seen, spec.backoff_set.get(host)):
                return False
        now = self._clock()
        for key, entry in spec.robots_seen.items():
            with self._lock:
                current = self._robots.get(key)
            if current is not entry or now - entry.fetched_at >= ROBOTS_TTL:
                return False
        for key in spec.robots:
            with self._lock:
                current = self._robots.get(key)
            if current is not None and now - current.fetched_at < ROBOTS_TTL:
                return False
            stored = self.cache.robots(key) if self.cache is not None else None
            if stored is not None and now - stored[1] < ROBOTS_TTL:
                return False
        if self.cache is not None and spec.validators != self._validators(spec.url):
            return False
        return True

    def _commit(self, spec, budget, usage) -> FetchResult:
        """Apply a claimed read-ahead fetch as if it were made now: its charges on `usage` in order (raising
        BudgetExceeded exactly where a fetch made now would have stopped, the rest counted as unused), the held
        robots entries and cache body written in order; then its result is returned or its FetchError raised.
        What it held of the task's budget (fetcher._ahead) is released once its charges are on `usage`."""
        events = spec.events
        try:
            for i, event in enumerate(events):
                try:
                    if event[0] == "request":
                        _charge(budget, usage, requests=1)
                    elif event[0] == "bytes":
                        if budget is not None and event[2] and usage.bytes + event[1] > budget.max_bytes:
                            # _read_body reads one byte past what is left and stops there, before charging
                            raise BudgetExceeded("bytes", budget.max_bytes + 1, budget.max_bytes)
                        _charge(budget, usage, bytes=event[1])
                    elif event[0] == "cut":
                        # the fetch ahead stopped reading at the byte budget, and a read made now stops there too
                        # (_fits): the error _read_body raises one byte past what is left
                        raise BudgetExceeded("bytes", budget.max_bytes + 1, budget.max_bytes)
                    elif event[0] == "robots":
                        _, key, entry, text = event
                        with self._lock:
                            self._robots[key] = entry
                        if text is not None and self.cache is not None:
                            self.cache.put_robots(key, *text)
                    elif self.cache is not None:
                        self.cache.put(event[1])
                except BudgetExceeded:
                    spec.abandon(events[i:])
                    raise
            if isinstance(spec.error, _OverBudget):
                # the fetch stopped before a request, for lack of room in the task's shared account or at a busy
                # site (nothing of that request was sent). What it did is on the task's Usage now; the fetch goes
                # on from that hop as the reader's own, on the task's budget, which refuses the request if a fetch
                # made now would be refused it. Nothing is requested twice.
                spec.release()
                return self._fetch(spec.url, "GET", None, None, spec.purpose, True, budget, usage, resume=spec.hop)
            if spec.error is not None:
                raise spec.error
            return spec.result
        finally:
            spec.release()

    def _validators(self, url):
        """The cached response of `url` a conditional GET revalidates (an ETag or Last-Modified and its body still
        stored), else None."""
        cached = self.cache.lookup(url)
        if cached and (cached["etag"] or cached["last_modified"]) and self.cache.body(cached["sha256"]):
            return cached
        return None

    def _store(self, result, usage):
        """Keep a fetched body in the public cache; a read-ahead fetch holds it until claimed (never if unused)."""
        if isinstance(usage, _Speculation):
            usage.hold(("cache", result))
        else:
            self.cache.put(result)

    def _fetch(self, url, method, body, headers, purpose, conditional, budget, usage, resume=None) -> FetchResult:
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
            if conditional and self.cache is not None and method == "GET" and purpose in CACHED_PURPOSES:
                cached = self._validators(url)
                if cached:
                    if cached["etag"]:
                        request_headers["If-None-Match"] = cached["etag"]
                    if cached["last_modified"]:
                        request_headers["If-Modified-Since"] = cached["last_modified"]
                if isinstance(usage, _Speculation):
                    usage.validators = cached
            response, final, redirects = self._request(parts, method, body, request_headers, purpose, budget, usage,
                                                       {}, deadline, resume)
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
                # The origin was contacted just now and confirmed the stored body is current: that is a live
                # observation (retrieval_mode 'live'); REVALIDATED_HEADER marks that the body came from the cache.
                result_headers[REVALIDATED_HEADER] = "304"
                result = FetchResult(url=url, final_url=final.geturl(), status=cached["status"],
                                     headers=result_headers, content_type=cached["content_type"], body=stored,
                                     retrieved_at=now, retrieval_mode="live", redirects=redirects, elapsed=elapsed,
                                     remote_ip=response.remote_ip)
                self._store(result, usage)
                self._count(final.hostname, "cache_hits")
                return result
            if not 200 <= response.status < 300:
                raise FetchError("http_error", _redact(final.geturl()), f"HTTP {response.status}",
                                 status=response.status)
            result = FetchResult(url=url, final_url=final.geturl(), status=response.status, headers=response.headers,
                                 content_type=_media_type(response.headers.get("content-type")), body=response.body,
                                 retrieved_at=now, retrieval_mode="live", redirects=redirects,
                                 truncated=response.truncated, elapsed=elapsed, remote_ip=response.remote_ip)
            if (self.cache is not None and method == "GET" and purpose in CACHED_PURPOSES
                    and not response.truncated):
                self._store(result, usage)
            return result
        except FetchError:
            self._count(host, "errors")
            raise

    def _request(self, parts, method, body, headers, purpose, budget, usage, resolved, deadline, resume=None):
        """Follow redirects manually; returns (final _Response, final SplitResult, [redirect URLs]). `resume`: the
        hop a read-ahead fetch of `parts` had reached when it ran out of room (_Speculation.hop): the walk goes on
        from there, with the redirects it had followed."""
        redirects = []
        current = parts
        origin = (parts.scheme, parts.hostname, parts.port)
        if resume is not None:
            current, method, body, headers, redirects = resume[0], resume[1], resume[2], dict(resume[3]), \
                list(resume[4])
        while True:
            if isinstance(usage, _Speculation) and purpose != "robots":       # the page's walk, not a robots.txt's
                usage.hop = (current, method, body, dict(headers), list(redirects))
            host = current.hostname
            self._ask(usage, current.geturl())                # still wanted? before this hop's name lookup
            self._check_backoff(host, current.geturl(), usage)
            addresses = self._resolve(current, resolved)      # policy refusal precedes any robots request
            crawl_delay = 0.0
            if purpose in ROBOTS_PURPOSES:
                entry = self._robots_entry(current, budget, usage, resolved, deadline)
                if not entry.allowed(current.geturl()):
                    self._count(host, "robots_blocked")
                    raise FetchError("blocked_robots", _redact(current.geturl()), entry.reason or "disallowed")
                crawl_delay = entry.delay
            # the fetch deadline starts after the politeness wait, not before it
            deadline += self._throttle(host, self._interval(host, purpose, crawl_delay), current.geturl())
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

    def _interval(self, host, purpose, crawl_delay=0.0) -> float:
        """Minimum seconds between two requests to `host`: the largest of the applicable values. Every purpose but
        'subresource': min_domain_interval, the host's host_intervals entry and robots.txt Crawl-delay (for the
        robots purposes). 'subresource': subresource_interval and the host_intervals entry; Crawl-delay is not
        applied to a render's sub-requests (one page view), while robots allow/disallow still is."""
        host_interval = 0.0
        matched = -1
        for domain, seconds in (self.policy.host_intervals or {}).items():
            domain = str(domain).lower().strip(".")
            if domain and len(domain) > matched and (host == domain or host.endswith("." + domain)):
                matched, host_interval = len(domain), float(seconds)
        if purpose == "subresource":
            return max(self.policy.subresource_interval, host_interval)
        return max(self.policy.min_domain_interval, host_interval, crawl_delay)

    def _backoff_until(self, host, now=None):
        """The backoff time the check compares with `now` for host (None: none): this fetcher's, else the one
        another fetcher or process sharing the cache set."""
        now = self._clock() if now is None else now
        with self._lock:
            until = self._backoff.get(host)
        if self.cache is not None and (until is None or now >= until):
            until = self.cache.backoff(host)          # set by another fetcher/process sharing the cache
            if until is not None and now < until:
                with self._lock:
                    self._backoff[host] = max(until, self._backoff.get(host, 0.0))
        return until

    def _check_backoff(self, host, url, usage=None):
        now = self._clock()
        until = self._backoff_until(host, now)
        if isinstance(usage, _Speculation):
            usage.backoff_seen.setdefault(host, until)
        if until is not None and now < until:
            raise FetchError("rate_limited", _redact(url), "host backoff in force", retry_after=until - now)

    def _set_backoff(self, host, seconds):
        """Back the host off for `seconds`; returns the backoff time now in force for it."""
        until = self._clock() + seconds
        with self._lock:
            self._backoff[host] = in_force = max(until, self._backoff.get(host, 0.0))
        if self.cache is not None:
            self.cache.put_backoff(host, until)
        self._count(host, "backoffs")
        return in_force

    def _throttle(self, host, interval, url="") -> float:
        """Reserve the next request slot for host: at least `interval` seconds after the previous one; sleeps until
        then and returns the seconds waited. A slot further away than policy.timeout is not reserved and nothing
        sleeps: FetchError('rate_limited', retry_after=wait) (a Crawl-delay at or above the timeout must not
        turn every first page fetch into a charged timeout)."""
        with self._lock:
            now = self._clock()
            last = self._last_request.get(host)
            slot = now if last is None else max(now, last + interval)
            wait = slot - now
            if wait > self.policy.timeout:
                raise FetchError("rate_limited", _redact(url or host),
                                 f"next request slot for {host} in {wait:.1f}s exceeds the "
                                 f"{self.policy.timeout:g}s fetch timeout", retry_after=wait)
            self._last_request[host] = slot
        if wait > 0:
            self._sleep(wait)
        return max(0.0, wait)

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
        host = parts.hostname
        self._ask(usage, url)                 # still wanted? after the politeness wait, before connecting
        spec = usage if isinstance(usage, _Speculation) else None
        self._enter(host, spec)               # one exchange per host for fetches ahead (module doc)
        try:
            return self._exchange_on(parts, method, body, headers, addresses, purpose, budget, usage, deadline, spec)
        finally:
            self._leave(host, spec)

    def _enter(self, host, spec) -> None:
        """An exchange with `host` begins. A read-ahead fetch does not begin one while any other is in progress
        with that host: it stops there, before the request (_OverBudget 'busy'), and its claim goes on from that
        hop at the reader's turn. The reader's own fetch begins regardless (as before), and cuts the read-ahead
        fetches talking to that host (their request was sent: they are not used)."""
        with self._lock:
            busy = self._busy.setdefault(host, [0, set()])
            if spec is not None and busy[0]:
                yielding = None
            else:
                busy[0] += 1
                yielding = [] if spec is not None else list(busy[1])
                if spec is not None:
                    busy[1].add(spec)
        if yielding is None:
            with spec.lock:
                spec.stopped = spec.stopped or "host busy"
            raise _OverBudget("busy")
        for other in yielding:
            other.halt("host busy")

    def _leave(self, host, spec) -> None:
        with self._lock:
            busy = self._busy.get(host)
            if busy is None or (spec is not None and spec not in busy[1]):
                return                         # a read-ahead fetch that never entered (host busy)
            busy[0] -= 1
            busy[1].discard(spec)
            if busy[0] <= 0:
                del self._busy[host]

    def _unsent(self, usage, host, charged=True) -> None:
        """The request charged for this exchange was never written on a connection: it is not a request.
        `charged`: whether the reader's Usage was charged for it at all (not without a budget)."""
        if isinstance(usage, _Speculation):
            usage.unsend()
        elif charged:
            refund = getattr(usage, "refund", None)
            if refund is not None:
                refund(requests=1)
        self._count(host, "requests", -1)

    def _cut(self, usage, url):
        """The exchange was cut on purpose (its watchdog was aborted): raise what ends the fetch. A read-ahead fetch
        ends unused. The reader's own fetch was cut because its `watch` saw a stop: its gate raises that stop here.
        Should the gate not confirm it (the stop was withdrawn within the poll), the fetch ends 'interrupted': the
        page was not read, and that says nothing about the site (no robots.txt verdict, no back-off, no cache)."""
        if isinstance(usage, _Speculation):
            raise _Abandoned(usage.stopped or "abandoned")
        self._ask(usage, url)
        raise FetchError("interrupted", _redact(url), "cut: a stop was seen while the request was on the wire, and "
                                                      "was withdrawn")

    def _exchange_on(self, parts, method, body, headers, addresses, purpose, budget, usage, deadline, spec):
        url = parts.geturl()
        host, port = parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)
        _charge(budget, usage, requests=1)
        self._count(host, "requests")
        chosen = []
        if spec is not None:
            poll = (lambda: spec.unwanted(url)) if spec.wanted is not None else None
        else:
            watch = getattr(self._gate, "watch", None)
            poll = (lambda: watch(url)) if watch is not None else None
        watchdog = _Watchdog(deadline, poll)
        if spec is not None and not spec.on_wire(watchdog):     # abandon() from now on cuts this exchange
            self._unsent(spec, host)                            # stopped since the charge: nothing is sent
            spec.on_wire(None)
            raise _Abandoned(spec.stopped or "abandoned")

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
                watchdog.arm(sock)
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
        written = refused = False             # the request went out on the connection; the gate said stop first
        try:
            conn.connect()                    # the handshake is not the request: the gate is asked again after it
            try:
                self._ask(usage, url)         # a stop that landed while the connection was being opened
            except BaseException:
                refused = True
                raise
            conn.request(method, target, body=body, headers=headers)
            written = True
            if spec is not None:
                spec.sent()
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
                in_force = self._set_backoff(host, wait)
                if isinstance(usage, _Speculation):
                    usage.backoff_set[host] = in_force
                raise FetchError("rate_limited", _redact(url), f"HTTP {status}", retry_after=wait, status=status)
            if 200 <= status < 300 and method != "HEAD":
                if purpose != "robots":
                    media = _media_type(response_headers.get("content-type"))
                    allowed = self.policy.content_types + (SUBRESOURCE_TYPES if purpose == "subresource" else ())
                    if not any(fnmatch.fnmatchcase(media, pattern) for pattern in allowed):
                        raise FetchError("unsupported_type", _redact(url), media or "no content type", status=status)
                self._read_body(raw, conn, response, purpose, budget, usage, deadline, url)
            if watchdog.aborted:                  # cut on purpose: whatever arrived is not a response
                self._cut(usage, url)
            if watchdog.fired:                    # the body "ended" because the watchdog shut the socket
                raise TimeoutError
            return response
        except FetchError:
            if watchdog.aborted:
                self._cut(usage, url)
            raise
        except BudgetExceeded:
            raise
        except (OSError, http.client.HTTPException) as error:
            if watchdog.aborted:
                self._cut(usage, url)
            if watchdog.fired or isinstance(error, (TimeoutError, socket.timeout)):
                raise FetchError("timeout", _redact(url), f"no response within {self.policy.timeout:g}s") from None
            if isinstance(error, (ssl.SSLError, ssl.CertificateError)):
                raise FetchError("tls_error", _redact(url), type(error).__name__) from None
            if isinstance(error, http.client.HTTPException):
                raise FetchError("http_error", _redact(url),
                                 f"malformed response ({type(error).__name__})") from None
            detail = type(error).__name__ + (f" ({error.strerror})" if error.strerror else "")
            raise FetchError("connect_failed", _redact(url), detail) from None
        finally:
            # stopped before the request was written (the gate's answer on the open connection, or a cut during
            # the handshake): nothing was sent, so it is not a request and is not counted, on either path. A
            # connection that failed on its own stays charged, as before
            if not written and (refused or watchdog.aborted):
                self._unsent(usage, host, charged=budget is not None and usage is not None)
            if spec is not None:
                spec.on_wire(None)
            watchdog.disarm()
            conn.release()

    def _read_body(self, raw, conn, response, purpose, budget, usage, deadline, url):
        limit = {"robots": ROBOTS_MAX_BYTES, "api": self.policy.max_api_bytes}.get(purpose, self.policy.max_bytes)
        may_truncate = purpose in ("page", "crawl", "robots")
        budget_left = None
        if budget is not None and usage is not None:
            budget_left = budget.max_bytes - usage.bytes
        # a fetch ahead that knows its task's budget takes the room for every read from the task's shared account
        ahead = usage if isinstance(usage, _Speculation) and usage.budget is not None else None
        encoding = (response.headers.get("content-encoding") or "identity").strip().lower()
        if encoding in ("gzip", "x-gzip"):
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif encoding == "deflate":
            decoder = None            # decided on the first chunk: zlib-wrapped or raw deflate
        elif encoding == "identity":
            decoder = False
        else:
            raise FetchError("decode_error", _redact(url), f"unsupported content-encoding {encoding[:40]}")
        declared = response.headers.get("content-length", "").strip()
        if not may_truncate and _DIGITS.fullmatch(declared) and int(declared) > limit and encoding == "identity":
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
            granted = amount
            if ahead is not None:
                granted = ahead.take(amount)
                amount = max(granted, 1)      # no room left: one byte tells whether the body goes on (as below)
            try:
                chunk = raw.read(amount)
            except (OSError, http.client.HTTPException):
                if isinstance(usage, _Speculation) and wire:
                    usage.arrived(wire)                        # cut mid-body: what had arrived still arrived
                raise
            if ahead is not None:
                ahead.give_back(granted - min(granted, len(chunk)))
                if len(chunk) > granted:      # the body is longer than the task's byte budget has room for
                    ahead.cut(wire + len(chunk), decoder is False,
                              int(declared) if _DIGITS.fullmatch(declared) else None, limit)
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
        if isinstance(usage, _Speculation):     # where a read stops at the byte limit depends on the encoding (_fits)
            usage.charge(budget, bytes=wire, identity=decoder is False)
        else:
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
        """The robots.txt entry of parts' origin: remembered, from the public cache, or fetched. A read-ahead fetch
        (usage a _Speculation) records the entries it used and holds back the one it fetched (_commit writes it)."""
        key = _robots_key(parts)
        spec = usage if isinstance(usage, _Speculation) else None
        if spec is not None and key in spec.robots:       # fetched earlier in this read-ahead fetch (a redirect hop)
            return spec.robots[key]
        now = self._clock()
        with self._lock:
            entry = self._robots.get(key)
            host_lock = self._robots_locks.setdefault(key, threading.Lock())
        if entry is not None and (now - entry.fetched_at < ROBOTS_TTL):
            if spec is not None:
                spec.robots_seen.setdefault(key, entry)
            return entry
        with host_lock:
            now = self._clock()
            with self._lock:
                entry = self._robots.get(key)
            if entry is not None and (now - entry.fetched_at < ROBOTS_TTL):
                if spec is not None:
                    spec.robots_seen.setdefault(key, entry)
                return entry
            stored = self.cache.robots(key) if self.cache is not None else None
            if stored is not None and now - stored[1] < ROBOTS_TTL:
                entry = _robots_from_text(stored[0], stored[1], user_agent=self.policy.user_agent)
            else:
                entry = self._fetch_robots(parts, budget, usage, resolved, deadline)
                if spec is not None:
                    spec.robots[key] = entry
                    spec.hold(("robots", key, entry, spec.robots_text.pop(key, None)))
                    return entry
            with self._lock:
                self._robots[key] = entry
            if spec is not None:
                spec.robots_seen.setdefault(key, entry)
            return entry

    def _fetch_robots(self, parts, budget, usage, resolved, deadline) -> _Robots:
        key = _robots_key(parts)
        robots = urlsplit(urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", "")))
        headers = {"User-Agent": self.policy.user_agent, "Accept": "text/plain,*/*;q=0.5",
                   "Accept-Encoding": "gzip, deflate", "Connection": "close"}
        ahead = usage if isinstance(usage, _Speculation) else None
        sent_before = ahead.recorded if ahead is not None else 0
        try:
            response, _final, _redirects = self._request(robots, "GET", None, headers, "robots", budget, usage,
                                                         resolved, deadline)
        except (BudgetExceeded, FetchError) as error:
            if isinstance(error, _OverBudget) and error.kind != "bytes" and ahead.recorded > sent_before:
                # a read-ahead fetch stopped before a request inside this robots.txt walk, after the walk had sent
                # one (the file redirects). A claim goes on from the page's hop, where this file would have to be
                # asked for again from its first request, so this stop is not one to go on from: the fetch is not
                # used, what it sent counts as unused, and its claim fetches as usual
                with ahead.lock:
                    ahead.stopped = ahead.stopped or "stopped inside a robots.txt walk"
                raise _Abandoned("stopped inside a robots.txt walk") from None
            if isinstance(error, BudgetExceeded) or error.code in ("rate_limited", "timeout", "interrupted"):
                raise
            return _robots_disallow_all(self._clock(), f"robots.txt unavailable ({error.code})")
        now = self._clock()
        if 200 <= response.status < 300:
            text = response.body.decode("utf-8", "replace")
            self._store_robots(key, text, now, usage)
            return _robots_from_text(text, now, user_agent=self.policy.user_agent)
        if 400 <= response.status < 500:
            self._store_robots(key, "", now, usage)
            return _robots_from_text("", now, user_agent=self.policy.user_agent)
        return _robots_disallow_all(now, f"robots.txt answered HTTP {response.status}")

    def _store_robots(self, key, text, at, usage):
        """Keep a fetched robots.txt in the public cache; a read-ahead fetch holds it until claimed."""
        if isinstance(usage, _Speculation):
            usage.robots_text[key] = (text, at)
        elif self.cache is not None:
            self.cache.put_robots(key, text, at)
