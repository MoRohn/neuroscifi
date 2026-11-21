"""A local HTTP server that impersonates public hosts, for deterministic tests and the offline benchmark.

Wire it into the real acquisition stack through SafeFetcher's injection points:

    web = FixtureWeb(); web.add("news.example", "/a", "<html>...</html>"); web.start()
    fetcher = SafeFetcher(policy, resolver=web.resolver, connector=web.connector)

`resolver(host, port)` maps each registered host to a fake *public* address, so the real address policy runs.
Special hosts resolve to forbidden addresses to exercise the SSRF defences (see SPECIAL). `connector(ip, port,
timeout)` refuses anything that is not one of its fake public addresses and otherwise connects to the local
server, which routes on the Host header. Nothing here can reach the real network.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading
import time
from urllib.parse import urlsplit

SPECIAL = {   # hostname -> address the policy must refuse
    "internal.test": "127.0.0.1",
    "loopback6.test": "::1",
    "metadata.test": "169.254.169.254",
    "private10.test": "10.0.0.5",
    "private192.test": "192.168.1.10",
    "cgnat.test": "100.64.0.1",
    "mapped.test": "::ffff:127.0.0.1",
    "unspecified.test": "0.0.0.0",
    "ula.test": "fd00::1",
}


@dataclass
class Route:
    status: int = 200
    body: bytes = b""
    headers: dict = field(default_factory=dict)
    delay: float = 0.0
    hits: int = 0


class FixtureWeb:
    def __init__(self):
        self.routes: dict[tuple[str, str], Route] = {}
        self.hosts: dict[str, str] = {}          # host -> fake public ip
        self.rebind: dict[str, list[str]] = {}   # host -> successive answers (DNS-rebinding tests)
        self.log: list[dict] = []
        self.lock = threading.Lock()
        self.server = None
        self.port = None

    # ---- registration -------------------------------------------------------------------------------------
    def _ip_for(self, host):
        host = host.lower()
        if host not in self.hosts:
            n = len(self.hosts) + 1
            self.hosts[host] = f"45.79.{n // 250}.{n % 250 + 1}"
        return self.hosts[host]

    def add(self, host, path, body, *, status=200, content_type="text/html; charset=utf-8", headers=None,
            delay=0.0):
        self._ip_for(host)
        if isinstance(body, str):
            body = body.encode()
        hdrs = {"Content-Type": content_type} if content_type else {}
        hdrs.update(headers or {})
        self.routes[(host.lower(), path)] = Route(status, body, hdrs, delay)
        return self

    def add_redirect(self, host, path, location, status=302):
        return self.add(host, path, b"", status=status, content_type=None, headers={"Location": location})

    def add_rebinding(self, host, answers):
        """Each resolver call for `host` returns the next answer (the last one repeats)."""
        self.rebind[host.lower()] = list(answers)

    def url(self, host, path="/"):
        return f"http://{host}{path}"

    # ---- injection points for SafeFetcher -----------------------------------------------------------------
    def resolver(self, host, port):
        host = host.lower().rstrip(".")
        if host in self.rebind:
            answers = self.rebind[host]
            answer = answers.pop(0) if len(answers) > 1 else answers[0]
            return [answer]
        if host in SPECIAL:
            return [SPECIAL[host]]
        if host in self.hosts:
            return [self.hosts[host]]
        raise socket.gaierror(socket.EAI_NONAME, f"fixture web has no host {host}")

    def connector(self, ip, port, timeout):
        fake = set(self.hosts.values()) | {a for answers in self.rebind.values() for a in answers}
        if ip not in fake or ip in SPECIAL.values():
            raise ConnectionRefusedError(f"fixture connector refuses {ip}")
        return socket.create_connection(("127.0.0.1", self.port), timeout=timeout)

    # ---- server -------------------------------------------------------------------------------------------
    def start(self):
        web = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _serve(self, head=False):
                host = (self.headers.get("Host") or "").split(":")[0].lower()
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                with web.lock:
                    web.log.append({"host": host, "method": self.command, "path": self.path,
                                    "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body,
                                    "at": time.time()})
                route = web.routes.get((host, self.path)) or web.routes.get((host, urlsplit(self.path).path))
                if route is None:
                    self.send_response(404)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", "9")
                    self.end_headers()
                    if not head:
                        self.wfile.write(b"not found")
                    return
                route.hits += 1
                if route.delay:
                    time.sleep(route.delay)
                etag = route.headers.get("ETag")
                if etag and self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(route.status)
                for name, value in route.headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(route.body)))
                self.end_headers()
                if not head:
                    self.wfile.write(route.body)

            def do_GET(self):
                self._serve()

            def do_POST(self):
                self._serve()

            def do_HEAD(self):
                self._serve(head=True)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def requests_to(self, host, path=None):
        with self.lock:
            return [r for r in self.log if r["host"] == host and (path is None or r["path"] == path)]
