"""Owner console HTTP server: static UI plus a JSON API over one instance file (API_CONTRACTS.md §6).

Security controls (SYSTEM_ARCHITECTURE.md §5):
- binds 127.0.0.1 only; there is no option to choose another address;
- a per-start session token, sent by the page as `Authorization: Bearer`, compared in constant time;
- Host must name the loopback address and port (DNS-rebinding defence), and any Origin must match it;
- POST bodies are JSON objects of at most 4 KiB; GET carries no body;
- strict CSP, nosniff, no-referrer, no-store; no access logs.
Every request opens its own connection, expires due evidence first, and closes it afterwards.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import re
import secrets
import time
from urllib.parse import parse_qs, unquote, urlsplit

from ..instance import (beliefs, checkpoints, datasets, evidence, executive, governance, graphs, memory,
                        recall, store)
from ..instance.errors import InstanceError, ValidationError

BIND = "127.0.0.1"
DEFAULT_PORT = 8766
MAX_BODY = 4096
WEB = Path(__file__).resolve().parent / "web"
ICON = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" '
        b'fill="#184e42"/><circle cx="16" cy="16" r="7" fill="none" stroke="#f4f7f5" stroke-width="3"/></svg>')
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/console.css": ("console.css", "text/css; charset=utf-8"),
          "/console.js": ("console.js", "text/javascript; charset=utf-8")}
HEADERS = {
    "Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
    "Cross-Origin-Resource-Policy": "same-origin", "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                                "img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"),
}
FORGET = {"evidence": governance.forget_evidence, "epoch": governance.forget_epoch,
          "partner": governance.forget_partner, "dataset": governance.forget_dataset}


# ---- request helpers -------------------------------------------------------------------------------

def _fields(body, required=(), optional=()):
    """Reject unknown or missing keys so a typo never silently falls back to a default."""
    unknown = sorted(set(body) - set(required) - set(optional))
    if unknown:
        raise ValidationError("unknown field(s): " + ", ".join(unknown))
    missing = [key for key in required if key not in body]
    if missing:
        raise ValidationError("missing field(s): " + ", ".join(missing))
    return body


def _int(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{name} must be an integer")
    return value


def _query(query, allowed):
    unknown = sorted(set(query) - set(allowed))
    if unknown:
        raise ValidationError("unknown query parameter(s): " + ", ".join(unknown))
    return {key: values[-1] for key, values in query.items()}


def _number(text, name, kind=float):
    if text is None:
        return None
    try:
        return kind(text)
    except ValueError:
        raise ValidationError(f"{name} must be a number") from None


# ---- route handlers: (ctx, params, query, body) -> JSON value -----------------------------------------

def _inventory(c, p, q, b):
    return {**store.inventory(c.db), "regulation": store.regulation(c.db, c.path)}


def _epochs(c, p, q, b):
    rows = c.db.execute(
        "SELECT e.name, e.created_at, e.note, count(v.seq), coalesce(sum(v.consent='learn'),0), "
        "coalesce(sum(v.consent='memory_only'),0), coalesce(sum(v.retention='promoted'),0), "
        "(SELECT count(*) FROM tombstones t WHERE t.epoch=e.name) "
        "FROM epochs e LEFT JOIN evidence v ON v.epoch=e.name GROUP BY e.name ORDER BY e.created_at, e.name")
    keys = ("name", "created_at", "note", "evidence", "learning", "memory_only", "promoted", "forgotten")
    return [dict(zip(keys, row)) for row in rows]


def _start_epoch(c, p, q, b):
    _fields(b, ("name",), ("note",))
    return evidence.start_epoch(c.db, b["name"], c.now, b.get("note"))


def _explain(c, p, q, b):
    return beliefs.explain(c.db, p[0])


def _decision(c, p, q, b):
    return executive.decide(c.db, p[0], c.now, record=False, path=c.path)


def _decide(c, p, q, b):
    _fields(b)
    return executive.decide(c.db, p[0], c.now, record=True, path=c.path)


def _evidence(c, p, q, b):
    q = _query(q, ("channel", "root", "limit"))
    return evidence.list_evidence(c.db, p[0], channel=q.get("channel") or None, root=q.get("root") or None,
                                  limit=_number(q.get("limit", "500"), "limit", int))


def _observe(c, p, q, b):
    return evidence.observe(c.db, b, c.now, path=c.path)


def _pending(c, p, q, b):
    q = _query(q, ("epoch", "all"))
    return executive.pending(c.db, epoch=q.get("epoch") or None, include_resolved=q.get("all") in ("1", "true"))


def _resolve(c, p, q, b):
    _fields(b, ("resolution",))
    return executive.resolve(c.db, int(p[0]), b["resolution"], c.now)


def _forget(c, p, q, b):
    _fields(b, ("kind", "target"))
    if b["kind"] not in FORGET:
        raise ValidationError("kind must be one of " + ", ".join(FORGET))
    target = _int(b["target"], "target") if b["kind"] == "dataset" else b["target"]
    if b["kind"] != "dataset" and not isinstance(target, str):
        raise ValidationError("target must be a string")
    return FORGET[b["kind"]](c.db, target, c.now)


def _promote_evidence(c, p, q, b):
    _fields(b, ("id",))
    if not isinstance(b["id"], str):
        raise ValidationError("id must be a string")
    return governance.promote(c.db, b["id"], c.now)


def _graphs(c, p, q, b):
    q = _query(q, ("context",))
    return graphs.listing(c.db, q.get("context") or None)


def _graph(c, p, q, b):
    return graphs.show(c.db, int(p[0]))


def _graph_learn(c, p, q, b):
    _fields(b, ("context", "train", "validation"))
    revision = graphs.learn(c.db, b["context"], c.now, train=_int(b["train"], "train"),
                            validation=_int(b["validation"], "validation"), holder="console")
    return {"revision_id": revision}


def _graph_evaluate(c, p, q, b):
    _fields(b)
    return graphs.evaluate(c.db, int(p[0]), c.now)


def _graph_promote(c, p, q, b):
    _fields(b)
    return graphs.promote(c.db, int(p[0]), c.now)


def _graph_reject(c, p, q, b):
    _fields(b, ("reason",))
    return graphs.reject(c.db, int(p[0]), c.now, b["reason"])


def _datasets(c, p, q, b):
    q = _query(q, ("context",))
    return datasets.listing(c.db, q.get("context") or None)


def _simulate(c, p, q, b):
    _fields(b, ("context", "seed"), ("world",))
    return datasets.simulate(c.db, b["context"], c.now, seed=b["seed"], world=b.get("world", "a"))


def _recall(c, p, q, b):
    _fields(b, ("dataset_id",), ("contexts",))
    contexts = b.get("contexts")
    if contexts is not None and (not isinstance(contexts, list) or not all(isinstance(x, str) for x in contexts)):
        raise ValidationError("contexts must be a list of context names")
    return recall.run(c.db, _int(b["dataset_id"], "dataset_id"), c.now, contexts=contexts or None, record=True)


def _checkpoints(c, p, q, b):
    return checkpoints.listing(c.db)


def _checkpoint_create(c, p, q, b):
    _fields(b, (), ("policy",))
    return checkpoints.create(c.db, c.path, c.path.parent / "checkpoints", c.now, policy=b.get("policy", "cold"))


def _checkpoint_verify(c, p, q, b):
    _fields(b)
    return checkpoints.verify(c.db, int(p[0]), c.now)


def _memory(c, p, q, b):
    q = _query(q, ("kind", "id", "epoch", "context", "channel", "root", "since", "until", "limit"))
    if "kind" not in q:
        raise ValidationError("kind is required (one of " + ", ".join(memory.KINDS) + ")")
    filters = {k: q.get(k) or None for k in ("id", "epoch", "context", "channel", "root")}
    return memory.retrieve(c.db, q["kind"], **filters, since=_number(q.get("since"), "since"),
                           until=_number(q.get("until"), "until"), limit=_number(q.get("limit", "100"), "limit", int))


SEGMENT = r"([^/]+)"
ROUTES = [(method, re.compile(f"^/api/{pattern}$"), handler) for method, pattern, handler in (
    ("GET", "inventory", _inventory),
    ("GET", "epochs", _epochs),
    ("POST", "epochs", _start_epoch),
    ("GET", f"epochs/{SEGMENT}/beliefs", _explain),
    ("GET", f"epochs/{SEGMENT}/explain", _explain),
    ("GET", f"epochs/{SEGMENT}/decision", _decision),
    ("POST", f"epochs/{SEGMENT}/decide", _decide),
    ("GET", f"epochs/{SEGMENT}/evidence", _evidence),
    ("POST", "observe", _observe),
    ("GET", "pending", _pending),
    ("POST", r"pending/(\d+)/resolve", _resolve),
    ("POST", "forget", _forget),
    ("POST", "promote", _promote_evidence),
    ("GET", "graphs", _graphs),
    ("POST", "graphs/learn", _graph_learn),
    ("GET", r"graphs/(\d+)", _graph),
    ("POST", r"graphs/(\d+)/evaluate", _graph_evaluate),
    ("POST", r"graphs/(\d+)/promote", _graph_promote),
    ("POST", r"graphs/(\d+)/reject", _graph_reject),
    ("GET", "datasets", _datasets),
    ("POST", "datasets/simulate", _simulate),
    ("POST", "recall", _recall),
    ("GET", "checkpoints", _checkpoints),
    ("POST", "checkpoints", _checkpoint_create),
    ("POST", r"checkpoints/(\d+)/verify", _checkpoint_verify),
    ("GET", "memory", _memory),
)]


class _Context:
    def __init__(self, db, path, now):
        self.db, self.path, self.now = db, path, now


class Handler(BaseHTTPRequestHandler):
    timeout = 30

    def version_string(self):
        return "nsf-console"

    def log_message(self, *args):
        pass  # No URL, token, body or address in logs.

    def reply(self, code, body=b"", mime="application/json", extra=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, allow_nan=False).encode()
        self.send_response(code)
        for name, value in {"Content-Type": mime, "Content-Length": str(len(body)), **HEADERS,
                            **(extra or {})}.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def fail(self, code, message):
        self.reply(code, {"error": message})

    def _trusted_origin(self):
        """Host must name this loopback server; a present Origin must be exactly http://<Host>."""
        port = self.server.server_port
        host = self.headers.get("Host", "")
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin == "http://" + host

    def _authorized(self):
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return False
        return hmac.compare_digest(auth[7:].encode(), self.server.token.encode())

    def handle_method(self):
        if not self._trusted_origin():
            return self.fail(403, "forbidden host or origin")
        url = urlsplit(self.path)
        if not url.path.startswith("/api/"):
            asset = ASSETS.get(url.path)
            if self.command not in ("GET", "HEAD"):
                return self.fail(405 if asset or url.path == "/icon.svg" else 404, "method not allowed")
            if url.path == "/icon.svg":
                return self.reply(200, ICON, "image/svg+xml")
            if not asset:
                return self.fail(404, "not found")
            return self.reply(200, (WEB / asset[0]).read_bytes(), asset[1])
        if not self._authorized():
            return self.fail(403, "missing or invalid session token; open the URL printed by `nsf console`")
        matches = [(method, m, handler) for method, pattern, handler in ROUTES if (m := pattern.match(url.path))]
        if not matches:
            return self.fail(404, "unknown route")
        chosen = next(((m, h) for method, m, h in matches if method == self.command), None)
        if chosen is None:
            allow = ", ".join(sorted({method for method, _, _ in matches}))
            return self.reply(405, {"error": "method not allowed"}, extra={"Allow": allow})
        match, handler = chosen
        body = self._body()
        if body is None:
            return
        params = [unquote(g) for g in match.groups()]
        self._dispatch(handler, params, parse_qs(url.query, keep_blank_values=False), body)

    def _body(self):
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.fail(400, "invalid Content-Length")
            return None
        if self.command == "GET":
            if size or self.headers.get("Transfer-Encoding"):
                self.fail(400, "GET requests carry no body")
                return None
            return {}
        if self.headers.get("Transfer-Encoding"):
            self.fail(400, "chunked bodies are not accepted")
            return None
        if size > MAX_BODY:
            self.close_connection = True
            self.fail(413, f"body exceeds {MAX_BODY} bytes")
            return None
        if self.headers.get_content_type() != "application/json":
            self.fail(400, "Content-Type must be application/json")
            return None
        try:
            body = json.loads(self.rfile.read(size)) if size else None
        except (ValueError, UnicodeDecodeError):
            body = None
        if not isinstance(body, dict):
            self.fail(400, "body must be a JSON object")
            return None
        return body

    def _dispatch(self, handler, params, query, body):
        db = None
        try:
            db = store.open_existing(self.server.db_path)
            now = self.server.clock()
            governance.sweep(db, now)
            result = handler(_Context(db, self.server.db_path, now), params, query, body)
            self.reply(200, result)
        except InstanceError as error:
            self.fail(error.http_status, str(error))
        except Exception:
            self.fail(500, "internal error")
        finally:
            if db is not None:
                db.close()

    def do_GET(self):
        self.handle_method()

    def do_HEAD(self):
        self.handle_method()

    def do_POST(self):
        self.handle_method()

    do_PUT = do_DELETE = do_PATCH = do_POST


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, db_path, port=DEFAULT_PORT, *, token=None, clock=time.time):
        self.db_path = Path(db_path).resolve()
        store.open_existing(self.db_path).close()  # refuse to start without an instance
        self.token = token or secrets.token_urlsafe(32)
        self.clock = clock
        super().__init__((BIND, port), Handler)

    @property
    def url(self):
        return f"http://{BIND}:{self.server_port}/#token={self.token}"

