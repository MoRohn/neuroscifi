"""The phone setup page (ADR-043): plain HTTP on the LAN, so a phone that does not trust the Spark's private CA yet can
reach it from a QR code. It serves a few static files, the CA certificate (public data, never a key) and a small
JSON description, and nothing else: no Pocket app, no API, no pairing code. The certificate is offered only for
SETUP_WINDOW after the owner asks for codes (Add a phone, `nsf add-phone`; not at bridge start-up and not on the
console's automatic renewals); outside that window the page says how to open it again. The pairing code itself never
travels over this port: it is in the second QR code, an https:// URL whose fragment the browser never sends to any
server. Plain HTTP is unauthenticated, so this page shows no fingerprint (an impostor would show its own): the check
is the phone's own certificate details against the fingerprint on the owner's console (ADR-043, security analysis).
The listener is a PocketServer: the bridge's connection caps and request deadline apply here too.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import threading
import time
from http.server import BaseHTTPRequestHandler

WEB = Path(__file__).resolve().parent / "web"
PRESENCE_WEB = Path(__file__).resolve().parents[1] / "presence" / "web"
SETUP_WINDOW = 15 * 60          # seconds the certificate is offered after each new invitation
FILES = {"/": (WEB / "setup.html", "text/html; charset=utf-8"), "/setup.css": (WEB / "setup.css", "text/css"),
         "/setup.mjs": (WEB / "setup.mjs", "text/javascript"), "/ui.css": (PRESENCE_WEB / "ui.css", "text/css"),
         "/theme-boot.js": (PRESENCE_WEB / "theme-boot.js", "text/javascript"),
         "/icon.svg": (WEB / "icon.svg", "image/svg+xml")}
CA_PATH = "/neuroscifi-ca.crt"
# a Host header that may appear in this page's CSP: a DNS name, an IPv4 address or a bracketed IPv6 address
_HOSTNAME = re.compile(r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                       r"|\[[0-9A-Fa-f:.]{2,45}\]")


def split_host(host: str) -> tuple[str, str]:
    """('name', 'port') of a Host header value ('' when absent); ('', '') when it is not a plain host."""
    m = re.fullmatch(r"(\[[^\]]*\]|[^:\[\]]*)(?::(\d{1,5}))?", host or "")
    if not m or not _HOSTNAME.fullmatch(m.group(1)):
        return "", ""
    return m.group(1), m.group(2) or ""


def fingerprint_groups(sha256_hex: str | None) -> str | None:
    """The CA's SHA-256 fingerprint as the console shows it next to the codes: sixteen groups of four hex digits,
    the way the phones' certificate details group them closely enough to compare by eye."""
    if not sha256_hex:
        return None
    h = sha256_hex.replace(":", "").upper()
    return " ".join(h[i:i + 4] for i in range(0, len(h), 4))


class SetupHandler(BaseHTTPRequestHandler):
    timeout = 10
    server_version = "nsf-pocket-setup"
    sys_version = ""

    def log_message(self, *args):
        pass  # no addresses or paths in logs

    def send_error(self, code, message=None, explain=None):
        """http.server's own errors (unsupported method, bad request line, 414) with this page's headers and no
        echoed request text."""
        self.close_connection = True
        self.reply(code)

    def reply(self, code, body=b"", mime="text/plain; charset=utf-8", hostname=None, extra=None):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        bridge = self.server.bridge
        connect = "'self'" + (f" https://{hostname}:{bridge.server_port}" if hostname else "")
        self.send_response(code)
        for name, value in {
            "Content-Type": mime, "Content-Length": str(len(body)), "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
                                       f"connect-src {connect}; base-uri 'none'; frame-ancestors 'none'; "
                                       "form-action 'none'",
            **(extra or {}),
        }.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        bridge = self.server.bridge
        hostname, port = split_host(self.headers.get("Host", ""))
        allowed = {split_host(name)[0] for name in getattr(bridge, "allowed_hosts", None) or ()}
        if not hostname or (port and int(port) != self.server.server_port) or \
                (allowed and hostname.lower() not in allowed):
            return self.reply(421)
        path = self.path.split("?", 1)[0]
        if path in FILES:
            file, mime = FILES[path]
            try:
                return self.reply(200, file.read_bytes(), mime, hostname)
            except OSError:
                return self.reply(404)
        is_open = setup_open(bridge)
        if path == "/setup.json":
            return self.reply(200, {"open": is_open, "https_port": bridge.server_port,
                                    "certificate": bool(getattr(bridge, "ca_der", None))},
                              "application/json", hostname)
        if path == CA_PATH and is_open and getattr(bridge, "ca_der", None):
            return self.reply(200, bridge.ca_der, "application/x-x509-ca-cert", hostname,
                              {"Content-Disposition": 'attachment; filename="neuroscifi-ca.crt"'})
        return self.reply(404)

    do_HEAD = do_GET


def setup_open(bridge) -> bool:
    return time.monotonic() < getattr(bridge, "setup_until", 0.0)


def load_ca(path) -> tuple[bytes, str]:
    """(DER bytes, SHA-256 hex) of a CA certificate file in DER or PEM."""
    import base64
    import hashlib
    data = Path(path).read_bytes()
    if b"-----BEGIN CERTIFICATE-----" in data:
        block = data.split(b"-----BEGIN CERTIFICATE-----", 1)[1].split(b"-----END CERTIFICATE-----", 1)[0]
        data = base64.b64decode(b"".join(block.split()))
    if not data.startswith(b"\x30"):
        raise ValueError("not a DER or PEM certificate")
    return data, hashlib.sha256(data).hexdigest().upper()


def start(bridge, bind: str, port: int):
    """Serve the setup page for `bridge` on (bind, port) in a daemon thread; returns the server (close it at exit).
    A PocketServer without TLS: per-address and total connection caps, and a deadline for each request."""
    from .server import PocketServer
    httpd = PocketServer((bind, port), SetupHandler, tls=None)
    httpd.daemon_threads = True
    httpd.bridge = bridge
    threading.Thread(target=httpd.serve_forever, name="nsf-pocket-setup", daemon=True).start()
    return httpd
