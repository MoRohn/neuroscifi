"""Web Push for Pocket (ADR-040): an empty push to the devices that opted in, so they hear about finished research
while Pocket is closed.

The Spark sends no payload, so nothing about a question reaches the push service; it learns the device's push
endpoint and when the Spark sends. The service worker shows one generic notification ("Your Spark finished a research
task") because it holds no pairing credential (POC-07) and so cannot ask the Spark what finished; Pocket loads the
details when it is opened.

`push` route (bearer, family scope, exists only with research on and the bridge started with --push-key):
  {action: "status"}                 the Spark's VAPID public key and whether this device is subscribed
  {action: "subscribe", endpoint}    store the endpoint the browser's push service returned (allow-listed hosts only)
  {action: "unsubscribe"}            delete this device's subscription
  {action: "test"}                   queue one empty push to this device (same rate limit as every push)

Unpairing, revoking or erasing a device deletes its subscription (ON DELETE CASCADE from `devices`).
"""
from __future__ import annotations

import base64
from contextlib import closing
from email.utils import parsedate_to_datetime
import http.client
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
import stat
import sys
import threading
import time
from urllib.parse import urlsplit

from .server import connect, fingerprint, research_db

# Push services a browser may return an endpoint from: FCM (Chrome, Android, Opera, Samsung; jmt17.google.com is the
# FCM host that Chromium builds without Google's official keys return, seen live from Chromium 153 on Linux), Mozilla
# autopush (Firefox), Apple web push (Safari, iOS home-screen apps) and Windows WNS (Edge). An entry with a leading
# dot matches its subdomains only. Nothing else is ever contacted, whatever a subscription says (SSRF).
SERVICES = ("fcm.googleapis.com", "jmt17.google.com", "updates.push.services.mozilla.com", ".push.apple.com",
            ".notify.windows.com")
SUBJECT = "mailto:pocket@neuroscifi.invalid"   # VAPID contact the push service sees; --push-contact replaces it
TTL = 43200            # seconds the push service keeps an undelivered push (a phone that is off for the night)
JWT_LIFETIME = 43200   # RFC 8292: at most 24 hours
INTERVAL = 60          # at most one push per device per minute; finishes meanwhile collapse into the next one
POLL = 5               # seconds between checks for finished research
RECENT = 6 * 3600      # finishes older than this (a bridge that was down) are not announced
TIMEOUT = 10           # per socket operation (connect, send, receive)
# Live, FCM answered 410 to a registration 3 and 8 s old and 201 at 19 s: during this grace after an endpoint is
# stored, 404/410 is retried every 15 s instead of deleting the subscription.
GRACE = 120
MAX_ENDPOINT = 1000
MAX_BACKOFF = 86400
TERMINAL = ("completed", "partial", "failed", "cancelled")
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


# ---- the Spark's VAPID key ----------------------------------------------------------------------------------------

def load_key(path):
    """The VAPID private key (EC P-256) at `path`, generated there on first use as a new 0600 file. An existing file
    must be a regular 0600 file of this user, not a symlink, holding a P-256 key; anything else raises."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    path = Path(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        key = ec.generate_private_key(ec.SECP256R1())
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
            os.fsync(fd)
        finally:
            os.close(fd)
        return key
    except OSError as error:   # ELOOP: a symlink
        raise PermissionError(f"{path}: not a regular file ({error.strerror})") from None
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise PermissionError(f"{path}: the push key must be a regular 0600 file owned by this user")
        data = os.read(fd, 8192)
    finally:
        os.close(fd)
    key = serialization.load_pem_private_key(data, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise ValueError(f"{path}: not an EC P-256 private key")
    return key


def public_key(key) -> str:
    """The applicationServerKey a browser subscribes with: the uncompressed P-256 point, base64url."""
    from cryptography.hazmat.primitives import serialization
    return b64u(key.public_key().public_bytes(serialization.Encoding.X962,
                                              serialization.PublicFormat.UncompressedPoint))


def vapid_jwt(key, audience, subject, now, lifetime=JWT_LIFETIME) -> str:
    """RFC 8292 token: ES256 over {"typ":"JWT","alg":"ES256"}.{"aud","exp","sub"}, signature as raw r || s."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    part = lambda obj: b64u(json.dumps(obj, separators=(",", ":")).encode())  # noqa: E731
    signing = part({"typ": "JWT", "alg": "ES256"}) + "." + part({"aud": audience, "exp": int(now) + lifetime,
                                                                 "sub": subject})
    r, s = decode_dss_signature(key.sign(signing.encode("ascii"), ec.ECDSA(hashes.SHA256())))
    return signing + "." + b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


# ---- endpoint policy (SSRF) ---------------------------------------------------------------------------------------

def check_endpoint(endpoint, test_origins=()):
    """The parsed endpoint when it is an https URL on port 443 of an allow-listed push service, else ValueError.
    `test_origins` (exact "scheme://host:port" strings) exists for tests only; nothing in the CLI sets it."""
    if not isinstance(endpoint, str) or not 0 < len(endpoint) <= MAX_ENDPOINT:
        raise ValueError("endpoint must be a URL of at most %d characters" % MAX_ENDPOINT)
    if any(not 0x21 <= ord(c) <= 0x7E for c in endpoint) or "\\" in endpoint or "#" in endpoint:
        raise ValueError("endpoint has a character a push service URL never has")
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        raise ValueError("malformed endpoint") from None
    if f"{parts.scheme}://{parts.netloc}" in test_origins:
        return parts
    host = parts.netloc.lower()
    if parts.scheme != "https" or "@" in parts.netloc or port not in (None, 443) or not parts.path.startswith("/"):
        raise ValueError("endpoint must be https on port 443 without credentials")
    host = host.removesuffix(":443")
    if host != parts.hostname or not all(_LABEL.match(label) for label in host.split(".")):
        raise ValueError("endpoint host is not a plain DNS name")
    if not any(host.endswith(s) and len(host) > len(s) if s.startswith(".") else host == s for s in SERVICES):
        raise ValueError("endpoint host is not a known push service")
    return parts


def _resolve(host, port):
    out = []
    for *_, sockaddr in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        if sockaddr[0] not in out:
            out.append(sockaddr[0])
    return out


class _Pinned(http.client.HTTPSConnection):
    """HTTPS to one already-checked address, with SNI and certificate verification for the push service's name.
    http.client never reads proxy settings and never follows a redirect."""

    def __init__(self, host, address, timeout):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def retry_after(value, now):
    """Seconds to wait from a Retry-After header (delta seconds or HTTP date), bounded, else None."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        seconds = int(value)
    else:
        try:
            seconds = parsedate_to_datetime(value).timestamp() - now
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
    return min(max(seconds, 1), MAX_BACKOFF)


# ---- dispatcher ----------------------------------------------------------------------------------------------------

class Push:
    """Watches the individual for finished research and owner findings, and sends empty pushes to subscribed devices.

    collect() marks subscriptions `pending` (once per finish, per family); flush() sends what the per-device rate
    limit and any Retry-After allow. Network I/O happens only here, never in a request handler."""

    def __init__(self, server, key, *, subject=SUBJECT, interval=INTERVAL, poll=POLL, timeout=TIMEOUT,
                 resolver=_resolve, test_origins=()):
        self.server, self.key, self.subject = server, key, subject
        self.public = public_key(key)
        self.interval, self.poll, self.timeout, self.resolver = interval, poll, timeout, resolver
        self.test_origins = frozenset(test_origins)
        self.wake = threading.Event()
        self.lock = threading.Lock()

    # -- the request a push service receives
    def request(self, endpoint, now):
        """(status, Retry-After seconds or None) of one empty push. ValueError when the endpoint is refused."""
        parts = check_endpoint(endpoint, self.test_origins)
        origin = f"{parts.scheme}://{parts.netloc}"
        headers = {"TTL": str(TTL), "Urgency": "normal", "Content-Length": "0",
                   "Authorization": f"vapid t={vapid_jwt(self.key, origin, self.subject, now)}, k={self.public}"}
        if origin in self.test_origins:
            conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=self.timeout)
        else:
            from ..webcortex.net import check_address
            addresses = self.resolver(parts.hostname, 443)
            if not addresses or any(check_address(a) for a in addresses):
                raise ValueError("push service host resolves to a non-public address")
            conn = _Pinned(parts.hostname, addresses[0], self.timeout)
        try:
            conn.request("POST", parts.path + ("?" + parts.query if parts.query else ""), body=b"", headers=headers)
            response = conn.getresponse()
            return response.status, retry_after(response.getheader("Retry-After"), now)
        finally:
            conn.close()

    # -- what changed since the last pass
    def collect(self, now):
        """Mark the subscriptions that have news: a research task of their family finished, or an autonomy finding
        reached the owner. Returns the number of new events."""
        path = self.server.store.path
        with closing(connect(path)) as db:
            families = [r[0] for r in db.execute(
                "SELECT DISTINCT m.family FROM push_subs s JOIN members m ON m.device=s.device")]
        if not families:
            return 0
        individual = research_db(self.server)
        if individual is None:
            return 0
        from ..webcortex import state
        news, since = [], now - RECENT
        marks = ",".join("?" * len(TERMINAL))
        with closing(individual):
            for family in families:
                digest = state.family_digest(individual, family)
                for ident, at in individual.execute(
                        f"SELECT id, coalesce(finished_at, updated_at) FROM web_tasks WHERE family=? AND status IN "
                        f"({marks}) AND coalesce(finished_at, updated_at)>?", (digest, *TERMINAL, since)):
                    news.append((family, f"task:{ident}:{at!r}", at))
            for ident, at in individual.execute("SELECT id, created_at FROM pending WHERE scope LIKE "
                                                "'web:autonomy:%' AND created_at>?", (since,)):
                news.append(("", f"finding:{ident}", at))   # the owner's: every subscribed device
        fresh = 0
        with closing(connect(path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            for family, item, at in news:
                if not db.execute("INSERT OR IGNORE INTO push_seen VALUES(?,?,?)", (family, item, now)).rowcount:
                    continue
                fresh += 1
                if family:
                    db.execute("UPDATE push_subs SET pending=1 WHERE created<=? AND device IN "
                               "(SELECT device FROM members WHERE family=?)", (at, family))
                else:
                    db.execute("UPDATE push_subs SET pending=1 WHERE created<=?", (at,))
        return fresh

    def due(self, last, not_before, now):
        """Seconds until this subscription may be sent to (0 when it may now)."""
        return max(0.0, (last + self.interval - now) if last is not None else 0.0,
                   (not_before - now) if not_before is not None else 0.0)

    def flush(self, now):
        """Send one empty push to every pending subscription the rate limit allows. Returns [(host, status)]."""
        path = self.server.store.path
        with closing(connect(path)) as db:
            rows = db.execute("SELECT device, endpoint, last, not_before, stored FROM push_subs WHERE pending=1").fetchall()
        sent = []
        for device, endpoint, last, not_before, stored in rows:
            if self.due(last, not_before, now):
                continue
            host = urlsplit(endpoint).hostname
            try:
                status, wait = self.request(endpoint, now)
            except ValueError:
                status, wait = None, None   # refused by policy: never retried
            except (OSError, http.client.HTTPException):
                status, wait = 0, 60        # unreachable or timed out: try again in a minute
            sent.append((host, status))
            with closing(connect(path)) as db, db:
                args = (device, endpoint)
                if status in (200, 201, 202, 204):
                    db.execute("UPDATE push_subs SET pending=0, last=?, not_before=NULL WHERE device=? AND endpoint=?",
                               (now, *args))
                elif status in (404, 410) and now - stored >= GRACE:   # the browser or the service dropped it
                    db.execute("DELETE FROM push_subs WHERE device=? AND endpoint=?", args)
                elif status is not None and (status in (0, 404, 410, 429) or status >= 500):   # honour Retry-After
                    backoff = wait or {0: 60, 404: 15, 410: 15, 429: 600}.get(status, 120)
                    db.execute("UPDATE push_subs SET not_before=? WHERE device=? AND endpoint=?", (now + backoff, *args))
                else:   # refused (policy, 3xx, other 4xx): drop this push, keep the subscription
                    db.execute("UPDATE push_subs SET pending=0, last=? WHERE device=? AND endpoint=?", (now, *args))
            if status not in (200, 201, 202, 204):
                # the service's host and the status only: never the endpoint path, which identifies the device
                outcome = {None: "refused by policy", 0: "no answer"}.get(status, f"answered {status}")
                print(f"nsf-pocket: push to {host}: {outcome}", file=sys.stderr, flush=True)
        return sent

    def step(self, now=None):
        with self.lock:
            now = time.time() if now is None else now
            self.collect(now)
            return self.flush(now)

    def run(self, stop):
        """The bridge's dispatcher thread: a pass every `poll` seconds, or at once after a test push."""
        while not stop.is_set():
            try:
                self.step()
            except Exception:
                print("nsf-pocket: push pass failed", file=sys.stderr, flush=True)
            self.wake.wait(self.poll)
            self.wake.clear()


# ---- the route -----------------------------------------------------------------------------------------------------

def route(server, token, data):
    """(status, payload) of the `push` route for a live credential (PermissionError otherwise)."""
    push = getattr(server, "push", None)
    if push is None:
        return 404, {}
    device, action = fingerprint(token), data.get("action")
    with closing(connect(server.store.path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM devices WHERE key=?", (device,)).fetchone():
            raise PermissionError()
        row = db.execute("SELECT endpoint, last, not_before FROM push_subs WHERE device=?", (device,)).fetchone()
        if action == "status" and set(data) == {"action"}:
            return 200, {"key": push.public, "subscribed": row is not None}
        if action == "subscribe" and set(data) == {"action", "endpoint"}:
            endpoint = data["endpoint"]
            try:
                check_endpoint(endpoint, push.test_origins)
            except ValueError:
                return 400, {"error": "push_service"}
            if row and row[0] == endpoint:
                return 200, {"subscribed": True}
            # One browser holds one subscription: the device that registers it last receives its pushes.
            db.execute("DELETE FROM push_subs WHERE endpoint=? AND device!=?", (endpoint, device))
            now = time.time()
            if row:   # the browser's push service rotated the endpoint
                db.execute("UPDATE push_subs SET endpoint=?, stored=?, not_before=NULL WHERE device=?",
                           (endpoint, now, device))
            else:
                db.execute("INSERT INTO push_subs(device, endpoint, created, stored) VALUES(?,?,?,?)",
                           (device, endpoint, now, now))
            return 201, {"subscribed": True}
        if action == "unsubscribe" and set(data) == {"action"}:
            db.execute("DELETE FROM push_subs WHERE device=?", (device,))
            return 200, {"subscribed": False}
        if not (action == "test" and set(data) == {"action"}):
            raise ValueError()
        if row is None:
            return 409, {"error": "not_subscribed"}
        db.execute("UPDATE push_subs SET pending=1 WHERE device=?", (device,))
        after = math.ceil(push.due(row[1], row[2], time.time()))
    push.wake.set()   # after the commit, so the dispatcher sees the pending push
    return 202, {"after": after}
