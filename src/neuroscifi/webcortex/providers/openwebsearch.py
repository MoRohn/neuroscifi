"""open-webSearch 2.2.0 through its loopback REST daemon (ADR-025).

Only `GET /health`, `GET /status` and `POST /search` are called; never the daemon's `/fetch-*` tools (no DNS
pinning) and never `/mcp` (binds 0.0.0.0 without auth). The base URL must be plain HTTP to a loopback host: this is
the one trusted local integration, so it bypasses SafeFetcher (which refuses loopback by design) and ignores proxy
environment variables. The daemon itself contacts the search engines.

Engines fail independently and the daemon answers HTTP 200 with `partialFailures` (live 2026-11-20: duckduckgo
answered, brave 429, startpage 0 results silently, bing "regional redirect limit"). So each search tries the
configured engines one at a time, in order, skipping engines whose own circuit breaker is open, and stops once
`limit` results are gathered. Per-engine attempts are exposed as `last_attempts` (thread-local) for the registry.
An engine that answers nothing counts as a failure only when a later engine answered the same query with results;
when every engine is empty the query is simply obscure (health-neutral). Titles and snippets arrive as HTML and are
stripped to text. Results are untrusted data.

Honest User-Agent: upstream open-webSearch sends forged desktop-Chrome (and Apifox) User-Agents. The pinned build is
patched by `deploy/open-websearch/apply-honest-ua.mjs` (`npm run patch-ua`), which replaces them with
config.USER_AGENT and writes UA_MARKER with the sha256 of each patched file. `honest_user_agent(package_dir)`
checks that marker against the files on disk; `Daemon.status()` and `OpenWebSearchProvider.health()` report it as
`honest_user_agent`, and `Daemon.start()` refuses an unpatched build unless `allow_browser_user_agent=True` (an
explicit owner decision). Engines may answer an honest User-Agent differently or refuse it.

Daemon hygiene: the child gets a minimal environment (PATH, HOME, NODE_*, OPEN_WEBSEARCH_*, plus explicit `env`);
loopback HTTP never follows redirects; a port is trusted only when /status reports EXPECTED_VERSION and the
listening socket belongs to the process NeuroSciFi started (pid + /proc start time recorded in the pid file, so a
recycled pid is never signalled).
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
import time
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit
import urllib.request

from ...instance.errors import ValidationError
from ..config import USER_AGENT
from ..types import BudgetExceeded, Query, SearchResult
from ..search import Breaker, ProviderError, charge, parse_iso_date, strip_html

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
MAX_QUERY_CHARS = 512
MAX_LIMIT = 20
MAX_RESPONSE_BYTES = 4_000_000
DEFAULT_PACKAGE_DIR = Path(__file__).resolve().parents[4] / "deploy" / "open-websearch"
ENTRY = Path("node_modules") / "open-websearch" / "build" / "index.js"
EXPECTED_VERSION = "2.2.0"
UA_MARKER = Path("node_modules") / "open-websearch" / ".neuroscifi-honest-ua.json"
UA_TARGETS = ("build/engines/duckduckgo/searchDuckDuckGo.js", "build/engines/startpage/startpage.js",
              "build/engines/brave/brave.js", "build/engines/bing/bing.js")
_ENV_KEEP = ("PATH", "HOME")
_ENV_PREFIXES = ("NODE_", "OPEN_WEBSEARCH_")


def honest_user_agent(package_dir: Path | str = DEFAULT_PACKAGE_DIR) -> bool:
    """True when the install at package_dir carries apply-honest-ua.mjs's marker for EXPECTED_VERSION and
    config.USER_AGENT, the marker covers the four search engines, every recorded file still has its recorded
    sha256 and none of the four contains a browser User-Agent."""
    marker = Path(package_dir) / UA_MARKER
    root = marker.parent
    try:
        data = json.loads(marker.read_text())
        files = data["files"]
        if data.get("version") != EXPECTED_VERSION or data.get("user_agent") != USER_AGENT:
            return False
        if not isinstance(files, dict) or not all(t in files for t in UA_TARGETS):
            return False
        for rel, digest in files.items():
            raw = (root / rel).read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                return False
            if rel in UA_TARGETS and b"Mozilla/5.0" in raw:
                return False
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
    return True


class NotLoopback(ValidationError, ValueError):
    """The open-webSearch integration only talks to a daemon on this machine."""


def loopback_base(base_url: str, what: str = "open-webSearch") -> str:
    """Validate and normalise a local service URL: http, loopback host, no path, user info, query or fragment."""
    try:
        parts = urlsplit(str(base_url).strip())
        port = parts.port
    except ValueError as exc:
        raise NotLoopback(f"invalid {what} URL {base_url!r}: {exc}") from None
    host = (parts.hostname or "").lower()
    if parts.scheme != "http" or host not in LOOPBACK_HOSTS:
        raise NotLoopback(f"{what} URL must be http://127.0.0.1, http://localhost or http://[::1]; "
                          f"got {base_url!r}")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise NotLoopback(f"{what} URL must be a bare origin; got {base_url!r}")
    shown = f"[{host}]" if ":" in host else host
    return f"http://{shown}:{port}" if port else f"http://{shown}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None              # a loopback daemon has no reason to redirect: the 3xx is returned as an error


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)   # never via a shell proxy


def bounded_request(method: str, url: str, data: bytes | None, headers: dict, timeout: float, *,
                    max_bytes: int = MAX_RESPONSE_BYTES) -> tuple[int, dict, bytes]:
    """One HTTP exchange with a loopback daemon -> (status, lower-cased headers, body), never via a proxy and never
    following a redirect (a 3xx is returned as is). `timeout` bounds the WHOLE exchange - connect, status line,
    headers and every read of the body - not each read: a daemon dripping one byte at a time cannot hold a search
    past it (UAT T5-M05). Raises socket.timeout at the deadline, URLError when the connection fails, ValueError
    for a body over max_bytes."""
    parts = urlsplit(url)
    deadline = time.monotonic() + timeout
    conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    fired = []

    def watchdog():      # status line and headers dripped byte by byte: shut the socket at the deadline
        fired.append(True)
        try:
            conn.sock.shutdown(socket.SHUT_RDWR)
        except (OSError, AttributeError):
            pass
    timer = threading.Timer(timeout, watchdog)
    timer.daemon = True
    timer.start()
    try:
        conn.request(method, (parts.path or "/") + (f"?{parts.query}" if parts.query else ""), body=data,
                     headers=headers)
        sock = conn.sock          # getresponse drops conn.sock when the server closes; the response still reads it
        resp = conn.getresponse()
        raw = bytearray()
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise socket.timeout("the answer did not arrive within the deadline")
            try:
                sock.settimeout(left)
            except OSError:
                pass     # read to the end and closed: read1 answers b""
            chunk = resp.read1(65536)
            if not chunk:
                break
            raw += chunk
            if len(raw) > max_bytes:
                raise ValueError("response too large")
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, bytes(raw)
    except (socket.timeout, TimeoutError):
        raise
    except (OSError, http.client.HTTPException) as exc:
        if fired or time.monotonic() >= deadline:
            raise socket.timeout("the answer did not arrive within the deadline") from None
        raise URLError(exc) from None
    finally:
        timer.cancel()
        conn.close()


def _http_json(method: str, url: str, body: dict | None, timeout: float) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    status, _, raw = bounded_request(method, url, data, {"Content-Type": "application/json",
                                                         "Accept": "application/json"}, timeout)
    payload = json.loads(raw.decode("utf-8", "replace"))
    if not isinstance(payload, dict):
        raise ValueError("response is not a JSON object")
    return status, payload


def _unwrap_redirect(url: str) -> str:
    """DuckDuckGo HTML results may link through //duckduckgo.com/l/?uddg=<target>."""
    if url.startswith("//"):
        url = "https:" + url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if (parts.hostname or "").endswith("duckduckgo.com") and parts.path.startswith("/l/"):
        target = parse_qs(parts.query).get("uddg")
        if target:
            return target[0]
    return url


def _clean_url(url) -> str | None:
    if not isinstance(url, str):
        return None
    url = _unwrap_redirect(url.strip())
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname or len(url) > 2048:
        return None
    return url


_TYPE_SPAN = re.compile(r'<span[^>]*class="[^"]*result__type[^"]*"[^>]*>(.*?)</span>', re.I | re.S)


class OpenWebSearchProvider:
    name = "openwebsearch"
    kinds = frozenset({"web", "news"})
    general_web = True

    def __init__(self, base_url: str, engines, *, timeout: float = 20.0, clock=time.time,
                 failure_threshold: int = 3, cooldown: float = 300.0,
                 package_dir: Path | str = DEFAULT_PACKAGE_DIR):
        self.base_url = loopback_base(base_url)
        self.package_dir = Path(package_dir)
        self.engines = [str(e).strip().lower() for e in engines if str(e).strip()]
        if not self.engines:
            raise ValidationError("open-webSearch needs at least one engine")
        self.timeout = float(timeout)
        self._clock = clock
        self._failure_threshold, self._cooldown = failure_threshold, cooldown
        self._lock = threading.Lock()
        self._breakers = {e: Breaker(failure_threshold=failure_threshold, cooldown=cooldown) for e in self.engines}
        self._local = threading.local()

    # -- engine health ------------------------------------------------------------------------------------------
    @property
    def last_attempts(self) -> list[dict]:
        return list(getattr(self._local, "attempts", []))

    def _breaker(self, engine: str) -> Breaker:
        if engine not in self._breakers:
            self._breakers[engine] = Breaker(failure_threshold=self._failure_threshold, cooldown=self._cooldown)
        return self._breakers[engine]

    def record_engine(self, engine: str, ok: bool, latency: float, error: str | None = None) -> None:
        with self._lock:
            if ok:
                self._breaker(engine).success(latency, self._clock())
            else:
                self._breaker(engine).failure(error, latency, self._clock())

    def engine_snapshot(self) -> dict:
        now = self._clock()
        with self._lock:
            return {e: b.snapshot(now) for e, b in self._breakers.items()}

    def dump_engines(self) -> dict:
        with self._lock:
            return {e: b.to_json() for e, b in self._breakers.items()}

    def load_engine_state(self, engine: str, data: dict) -> None:
        with self._lock:
            self._breakers[engine] = Breaker(failure_threshold=self._failure_threshold,
                                             cooldown=self._cooldown).load(data)

    # -- search -------------------------------------------------------------------------------------------------
    def _query_text(self, query: Query) -> str:
        text = " ".join(str(query.text).split())
        if query.site:
            text = f"{text} site:{query.site}"
        return text[:MAX_QUERY_CHARS]

    def _parse(self, items, engine: str, start_rank: int, since) -> list[SearchResult]:
        out = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            url = _clean_url(item.get("url"))
            if url is None:
                continue
            raw_title = str(item.get("title") or "")
            metadata = {}
            m = _TYPE_SPAN.search(raw_title) or _TYPE_SPAN.search(str(item.get("description") or ""))
            if m:
                metadata["filetype"] = strip_html(m.group(1))[:16]
            title = strip_html(_TYPE_SPAN.sub(" ", raw_title))[:500]
            snippet = strip_html(_TYPE_SPAN.sub(" ", str(item.get("description") or "")))[:1000]
            published = None
            for key in ("publishedDate", "published", "date"):
                published = parse_iso_date(item.get(key))
                if published is not None:
                    break
            if since is not None and published is not None and published < since:
                continue
            if item.get("source"):
                metadata["source"] = strip_html(item.get("source"))[:200]
            out.append(SearchResult(url=url, title=title or url, snippet=snippet, provider=self.name,
                                    engine=str(item.get("engine") or engine)[:40], rank=start_rank + len(out),
                                    kind="web", published_at=published, metadata=metadata))
        return out

    def _call_engine(self, engine: str, text: str, limit: int) -> tuple[list, str | None, str | None]:
        """-> (items, failure_code, failure_message). Raises ProviderError when the daemon itself is unusable."""
        body = {"query": text, "limit": limit, "engines": [engine], "searchMode": "request"}
        try:
            status, payload = _http_json("POST", self.base_url + "/search", body, self.timeout)
        except (URLError, ConnectionError, socket.timeout, TimeoutError) as exc:
            raise ProviderError(self.name, f"daemon unreachable at {self.base_url}: {exc}",
                                code="daemon_unreachable") from None
        except (ValueError, UnicodeDecodeError) as exc:
            return [], "bad_response", f"daemon answered unparseable JSON: {exc}"
        if payload.get("status") != "ok" or not isinstance(payload.get("data"), dict):
            err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            return [], str(err.get("code") or f"http_{status}")[:60], str(err.get("message") or "")[:300]
        data = payload["data"]
        failures = [f for f in data.get("partialFailures") or [] if isinstance(f, dict)]
        mine = [f for f in failures if str(f.get("engine", "")).lower() == engine] or failures
        items = data.get("results") if isinstance(data.get("results"), list) else []
        if mine and not items:
            f = mine[0]
            return [], str(f.get("code") or "engine_error")[:60], str(f.get("message") or "")[:300]
        return items, None, None

    def search(self, query: Query, limit: int, *, budget=None, usage=None) -> list[SearchResult]:
        limit = max(1, min(int(limit), MAX_LIMIT))
        text = self._query_text(query)
        attempts: list[dict] = []
        self._local.attempts = attempts
        if not text:
            return []
        results: list[SearchResult] = []
        seen: set[str] = set()
        pending_empty: list[tuple[str, float]] = []
        tried = 0
        for engine in self.engines:
            if len(results) >= limit:
                break
            with self._lock:
                allowed = self._breaker(engine).allow(self._clock())
            if not allowed:
                attempts.append({"engine": engine, "ok": False, "n": 0, "error": "circuit_open", "latency": 0.0})
                continue
            try:
                charge(budget, usage, requests=1)   # the daemon makes one upstream request per engine call
            except BudgetExceeded as exc:
                with self._lock:
                    self._breaker(engine).neutral()
                attempts.append({"engine": engine, "ok": False, "n": 0, "error": f"budget_exhausted: {exc}",
                                 "latency": 0.0})
                if not results:
                    raise ProviderError(self.name, str(exc), retryable=False, code="budget_exhausted") from None
                break
            tried += 1
            started = time.perf_counter()
            try:
                items, code, message = self._call_engine(engine, text, limit)
            except ProviderError:
                with self._lock:
                    self._breaker(engine).neutral()
                attempts.append({"engine": engine, "ok": False, "n": 0, "error": "daemon_unreachable",
                                 "latency": round(time.perf_counter() - started, 4)})
                raise
            latency = round(time.perf_counter() - started, 4)
            if code:
                error = f"{code}: {message}" if message else code
                with self._lock:
                    self._breaker(engine).failure(error, latency, self._clock())
                attempts.append({"engine": engine, "ok": False, "n": 0, "error": error, "latency": latency})
                continue
            parsed = []
            for r in self._parse(items, engine, len(results) + 1, query.since):
                if r.url not in seen:
                    seen.add(r.url)
                    parsed.append(r)
            if not parsed:
                pending_empty.append((engine, latency))
                attempts.append({"engine": engine, "ok": True, "n": 0, "error": "empty", "latency": latency})
                continue
            with self._lock:
                self._breaker(engine).success(latency, self._clock())
            attempts.append({"engine": engine, "ok": True, "n": len(parsed), "error": None, "latency": latency})
            results.extend(parsed)
        with self._lock:   # settle empty answers: failure only when another engine found results
            for engine, latency in pending_empty:
                if results:
                    self._breaker(engine).failure("empty: 0 results while another engine answered", latency,
                                                  self._clock())
                else:
                    self._breaker(engine).neutral()
        if not results and tried and all(not a["ok"] for a in attempts if a["error"] != "circuit_open"):
            errors = "; ".join(f"{a['engine']}: {a['error']}" for a in attempts)
            raise ProviderError(self.name, f"every engine failed ({errors})", code="engines_failed")
        if not results and not tried:
            raise ProviderError(self.name, "every engine circuit is open", code="circuit_open")
        return results[:limit]

    # -- daemon health ------------------------------------------------------------------------------------------
    def health(self) -> dict:
        out = {"base_url": self.base_url, "reachable": False, "version": None, "playwright": None,
               "engines_default": None, "engines_allowed": None, "search_mode": None, "error": None,
               "engines": self.engine_snapshot(), "honest_user_agent": honest_user_agent(self.package_dir)}
        try:
            _, h = _http_json("GET", self.base_url + "/health", None, min(self.timeout, 5.0))
            out["reachable"] = h.get("status") == "ok"
            _, s = _http_json("GET", self.base_url + "/status", None, min(self.timeout, 5.0))
        except (URLError, ConnectionError, OSError, ValueError) as exc:
            out["error"] = f"{type(exc).__name__}: {exc}"[:300]
            return out
        data = s.get("data") if isinstance(s.get("data"), dict) else {}
        summary = data.get("configSummary") if isinstance(data.get("configSummary"), dict) else {}
        out.update({"version": data.get("version"), "playwright": summary.get("playwrightAvailable"),
                    "engines_default": summary.get("defaultSearchEngine"),
                    "engines_allowed": summary.get("allowedSearchEngines"),
                    "search_mode": summary.get("effectiveSearchMode") or summary.get("searchMode"),
                    "capabilities": data.get("capabilities")})
        return out


# ---- daemon process ------------------------------------------------------------------------------------------

def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _starttime(pid: int) -> str | None:
    """Start time of a process (field 22 of /proc/<pid>/stat, clock ticks since boot): pid + start time names one
    process even after the pid number is recycled."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat[stat.rfind(")") + 2:].split()[19]
    except (OSError, IndexError):
        return None


def _listen_inodes(port: int) -> set | None:
    """Socket inodes listening on TCP `port` (Linux /proc/net); None where /proc/net is absent."""
    inodes = set()
    tables = [Path("/proc/net/tcp"), Path("/proc/net/tcp6")]
    if not any(t.exists() for t in tables):
        return None
    for table in tables:
        try:
            lines = table.read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) > 9 and fields[3] == "0A" and int(fields[1].rsplit(":", 1)[1], 16) == int(port):
                inodes.add(f"socket:[{fields[9]}]")
    return inodes


def _listens(pid: int, port: int) -> bool | None:
    """Whether process pid owns a socket listening on TCP `port` (Linux /proc); None where /proc/net is absent."""
    inodes = _listen_inodes(port)
    if inodes is None:
        return None
    try:
        fds = os.listdir(f"/proc/{pid}/fd")
    except OSError:
        return False
    for fd in fds:
        try:
            if os.readlink(f"/proc/{pid}/fd/{fd}") in inodes:
                return True
        except OSError:
            continue
    return False


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    """Whether some process holds host:port: a listening socket in /proc/net (any process, even one that never
    accepts), else a TCP connection being accepted (whatever speaks there)."""
    if _listen_inodes(port):
        return True
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _private_open(path: Path, flags: int):
    """Open a daemon state file owner-only (0600), whatever the umask or the file's earlier mode (S-10)."""
    fd = os.open(path, flags | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except OSError:
        os.close(fd)
        raise
    return fd


def _alive(pid: int) -> bool:
    """True while the process exists and is not a zombie (exited, waiting to be reaped by its parent)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat[stat.rfind(")") + 2:][:1] != "Z"


@dataclass
class Daemon:
    """Starts/stops the pinned open-webSearch daemon bound to loopback.

    `stop()` terminates only the process this object started, or the one recorded in `<state_dir>/
    openwebsearch.pid` ("<pid> <start time>") while that pid still has the recorded start time and this daemon's
    entry script on its command line. It never kills by name pattern. `start(allow_browser_user_agent=False)`
    refuses a build without the honest User-Agent patch, and refuses a port another process already holds
    (ProviderError code 'port_in_use' naming the port), whether or not that process speaks HTTP.

    Ownership is judged the same way from any process: the pid file's pid + /proc start time + entry script on the
    command line (`owned_by_pid_file`) and the process being alive. The state directory is 0700 and the pid and
    log files 0600 (S-10): the log can contain queries.
    """
    node: str = "node"
    package_dir: Path | str = DEFAULT_PACKAGE_DIR
    host: str = "127.0.0.1"
    port: int = 3210
    state_dir: Path | str | None = None

    def __post_init__(self):
        if not _is_loopback_host(str(self.host)):
            raise NotLoopback(f"open-webSearch daemon must bind a loopback address, not {self.host!r}")
        if not (0 < int(self.port) < 65536):
            raise ValidationError(f"invalid port {self.port!r}")
        self.package_dir = Path(self.package_dir)
        self.state_dir = Path(self.state_dir) if self.state_dir else self.package_dir / "state"
        self._proc: subprocess.Popen | None = None

    @property
    def base_url(self) -> str:
        host = f"[{self.host}]" if ":" in str(self.host) else self.host
        return f"http://{host}:{int(self.port)}"

    @property
    def entry(self) -> Path:
        return self.package_dir / ENTRY

    @property
    def log_path(self) -> Path:
        return Path(self.state_dir) / "openwebsearch.log"

    @property
    def pid_path(self) -> Path:
        return Path(self.state_dir) / "openwebsearch.pid"

    def _status_payload(self) -> dict | None:
        """The /status data of whatever answers on the port, or None."""
        try:
            _, payload = _http_json("GET", self.base_url + "/status", None, 1.0)
        except (URLError, ConnectionError, OSError, ValueError):
            return None
        data = payload.get("data")
        return data if payload.get("status") == "ok" and isinstance(data, dict) else None

    def _healthy(self, pid: int | None) -> bool:
        """The port answers /status as open-webSearch EXPECTED_VERSION and its listening socket belongs to `pid`
        (a process NeuroSciFi started); a port squatter answering the same JSON is not trusted."""
        if pid is None:
            return False
        data = self._status_payload()
        if data is None or data.get("version") != EXPECTED_VERSION:
            return False
        return _listens(pid, self.port) is not False

    def _owned_pid(self) -> tuple[int | None, bool]:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc.pid, True
        return self._recorded_pid(), False

    def _recorded_pid(self) -> int | None:
        try:
            recorded = self.pid_path.read_text().split()
            pid = int(recorded[0])
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        except (OSError, ValueError, IndexError):
            return None
        if len(recorded) > 1 and _starttime(pid) != recorded[1]:
            return None                       # the pid was recycled by another process
        return pid if str(self.entry).encode() in cmdline else None

    def _log_tail(self, n: int = 20) -> str:
        try:
            return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-n:])
        except OSError:
            return ""

    def start(self, *, timeout: float = 15.0, env: dict | None = None,
              allow_browser_user_agent: bool = False) -> dict:
        """Start the daemon (or report the one NeuroSciFi already runs). `allow_browser_user_agent=True` is the
        owner's explicit acceptance of an unpatched build that sends forged browser User-Agents."""
        pid, _owned = self._owned_pid()
        if self._healthy(pid):
            return {**self.status(), "started": False, "reason": "already running"}
        if pid is not None and _alive(pid) and _listens(pid, self.port):
            return {**self.status(), "started": False, "reason": "already running (not answering /status yet)"}
        if self._status_payload() is not None or _port_open(str(self.host), self.port):
            raise ProviderError("openwebsearch", f"port {int(self.port)} on {self.host} is in use by another process "
                                "(not an open-webSearch daemon NeuroSciFi started); stop that process or choose "
                                "another port", code="port_in_use")
        if not self.entry.is_file():
            raise ValidationError(f"open-webSearch is not installed: {self.entry} missing "
                                  f"(run `npm ci` in {self.package_dir})")
        if not allow_browser_user_agent and not honest_user_agent(self.package_dir):
            raise ValidationError(f"open-webSearch at {self.package_dir} sends forged browser User-Agents: run "
                                  f"`npm run patch-ua` there, or pass allow_browser_user_agent=True to accept that")
        Path(self.state_dir).mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
        child_env = {k: v for k, v in os.environ.items() if k in _ENV_KEEP or k.startswith(_ENV_PREFIXES)}
        child_env.update({"OPEN_WEBSEARCH_QUIET_STARTUP": "true", "ENABLE_CORS": "false"})
        child_env.update(env or {})
        log = os.fdopen(_private_open(self.log_path, os.O_WRONLY | os.O_APPEND), "ab")
        try:
            self._proc = subprocess.Popen(
                [self.node, str(self.entry), "serve", "--host", str(self.host), "--port", str(int(self.port))],
                cwd=str(self.package_dir), env=child_env, stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, start_new_session=True)
        finally:
            log.close()
        with os.fdopen(_private_open(self.pid_path, os.O_WRONLY | os.O_TRUNC), "w") as fh:
            fh.write(f"{self._proc.pid} {_starttime(self._proc.pid) or ''}".strip())
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise ProviderError("openwebsearch", f"daemon exited with {self._proc.returncode}: "
                                    f"{self._log_tail()}", code="daemon_failed")
            if self._healthy(self._proc.pid):
                return {**self.status(), "started": True}
            time.sleep(0.2)
        self.stop()
        raise ProviderError("openwebsearch", f"daemon not healthy after {timeout}s: {self._log_tail()}",
                            code="daemon_failed")

    def stop(self, *, timeout: float = 5.0) -> dict:
        proc = self._proc if self._proc is not None and self._proc.poll() is None else None
        pid = proc.pid if proc else self._recorded_pid()
        if pid is None:
            return {"stopped": False, "reason": "no daemon started by NeuroSciFi is running"}
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=timeout)
        else:
            os.kill(pid, 15)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and _alive(pid):
                time.sleep(0.1)
            if _alive(pid):
                os.kill(pid, 9)
        self._proc = None
        try:
            self.pid_path.unlink()
        except OSError:
            pass
        return {"stopped": True, "pid": pid}

    def status(self) -> dict:
        """`running`: a daemon NeuroSciFi started answers as EXPECTED_VERSION on its own socket; `owned`: the
        process is NeuroSciFi's, judged the same from any process — `started_here` (this object started it) or
        `owned_by_pid_file` (the pid file's pid, /proc start time and entry script all match); `alive`: that
        process exists; `port_answering`: anything answers /status on the port; `port_in_use`: anything accepts
        connections on it; `honest_user_agent`: the install carries the honest-UA patch."""
        pid, started_here = self._owned_pid()
        by_file = self._recorded_pid()
        pid = pid if pid is not None else by_file
        out = {"base_url": self.base_url, "running": self._healthy(pid), "pid": pid,
               "owned": started_here or by_file is not None, "started_here": started_here,
               "owned_by_pid_file": by_file is not None and by_file == pid, "alive": pid is not None and _alive(pid),
               "port_answering": self._status_payload() is not None,
               "port_in_use": _port_open(str(self.host), self.port), "log": str(self.log_path),
               "entry": str(self.entry), "installed": self.entry.is_file(),
               "honest_user_agent": honest_user_agent(self.package_dir)}
        if out["running"]:
            health = OpenWebSearchProvider(self.base_url, ["duckduckgo"], timeout=3.0,
                                           package_dir=self.package_dir).health()
            out.update({k: health.get(k) for k in ("version", "playwright", "engines_default", "search_mode")})
        return out
