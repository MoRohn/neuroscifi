"""Pocket Web Push (ADR-040): VAPID, the empty request a push service receives, the endpoint allow-list (SSRF),
404/410 cleanup, 429 backoff, the per-device rate limit, family isolation and the erase cascades.

A local fake push service stands in for FCM and friends through Push(test_origins=...), which nothing in the CLI
sets; every allow-list test runs without it."""
import base64
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import socket
import threading
import time

import pytest

pytest.importorskip("cryptography")
from cryptography.exceptions import InvalidSignature  # noqa: E402
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402

from neuroscifi.pocket import push, server as bridge  # noqa: E402
from neuroscifi.webcortex import knowledge, state  # noqa: E402
from test_pocket_research import QUESTION, Spark, submit  # noqa: E402

GOOD = ["https://fcm.googleapis.com/fcm/send/dXJsOmV4YW1wbGU:APA91bHPRgkF",
        "https://fcm.googleapis.com/wp/cXdlcnR5dWlvcA",
        "https://jmt17.google.com/fcm/send/dXJsOmV4YW1wbGU:APA91bH",
        "https://updates.push.services.mozilla.com/wpush/v2/gAAAAABkZXhhbXBsZQ",
        "https://web.push.apple.com/QGuQyavXutnMH2-example",
        "https://wns2-par02p.notify.windows.com/w/?token=BQYAAABexample%2bslash",
        "https://FCM.googleapis.com:443/fcm/send/upper-case-host-and-default-port"]
BAD = ["http://fcm.googleapis.com/fcm/send/x",                  # not https
       "https://evil.example/fcm/send/x",
       "https://fcm.googleapis.com.evil.example/x",               # allowed name as a prefix
       "https://evil.example/https://fcm.googleapis.com/x",
       "https://fcm.googleapis.com@evil.example/x",               # userinfo trick
       "https://user:pw@fcm.googleapis.com/x",
       "https://fcm.googleapis.com:8443/x", "https://fcm.googleapis.com:0443/x",
       "https://fcm.googleapis.com./x",                           # trailing dot
       "https://127.0.0.1/x", "https://[::1]/x", "https://localhost/x", "https://10.0.0.1/x",
       "https://169.254.169.254/latest/meta-data/", "https://2130706433/x", "https://0x7f000001/x",
       "https://push.apple.com/x", "https://evilpush.apple.com/x", "https://notify.windows.com.evil.example/x",
       "https://jmt18.google.com/x", "https://www.google.com/x", "https://x.jmt17.google.com/x",
       "https://fcm.googleapis.com\\@evil.example/x", "https://fcm.googleapis.com/x#fragment",
       "https://fcm.googleapis.com/x y", "https://fcm.googleаpis.com/x",   # Cyrillic a
       "https://fcm.googleapis.com", "//fcm.googleapis.com/x", "fcm.googleapis.com/x", "file:///etc/passwd",
       "https://fcm.googleapis.com/" + "a" * 1000, "", None, 42, ["https://fcm.googleapis.com/x"]]


class FakePushService:
    """A local push service: records every request; answers from `replies` ((status, headers) each), else 201."""

    def __init__(self):
        self.requests, self.replies = [], []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.requests.append({"path": self.path, "headers": self.headers, "body": body})
                status, headers = outer.replies.pop(0) if outer.replies else (201, {})
                self.send_response(status)
                for name, value in {**headers, "Content-Length": "0"}.items():
                    self.send_header(name, value)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"

    def endpoint(self, name):
        return f"{self.origin}/push/{name}"

    def paths(self):
        return [r["path"] for r in self.requests]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake():
    f = FakePushService()
    yield f
    f.close()


@pytest.fixture
def spark(tmp_path, fake):
    s = Spark(tmp_path)
    s.server.push = push.Push(s.server, push.load_key(tmp_path / "keys" / "vapid.pem"), test_origins={fake.origin})
    yield s
    s.close()


@pytest.fixture
def devices(spark):
    """Phone A paired by invitation, tablet B linked from A (same family), unrelated C paired separately."""
    status, a = spark.req("pair", {"code": "invite"})
    assert status == 201
    status, link = spark.req("link", {}, a["key"])
    assert status == 201
    status, b = spark.req("pair", {"code": link["code"]})
    assert status == 201
    return a["key"], b["key"], spark.server.store.pair()


def subscribe(spark, token, endpoint):
    return spark.req("push", {"action": "subscribe", "endpoint": endpoint}, token)


def finish(spark, ident, at, status="completed"):
    with spark.owner() as db, db:
        state.update_task(db, ident, at, status=status, finished_at=at)


def subs(spark):
    with closing(bridge.connect(spark.server.store.path)) as db:
        return {endpoint.rsplit("/", 1)[-1]: (pending, not_before)
                for endpoint, pending, not_before in db.execute("SELECT endpoint, pending, not_before FROM push_subs")}


def b64d(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def verify(token, public):
    """Check an ES256 JWT with the raw uncompressed P-256 public key; returns (header, claims)."""
    head, claims, signature = token.split(".")
    raw = b64d(signature)
    assert len(raw) == 64
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64d(public))
    key.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
               f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    return json.loads(b64d(head)), json.loads(b64d(claims))


def test_vapid_jwt_structure_and_signature(tmp_path):
    key = push.load_key(tmp_path / "vapid.pem")
    public = push.public_key(key)
    assert len(b64d(public)) == 65 and b64d(public)[0] == 4, "uncompressed P-256 point (applicationServerKey)"
    token = push.vapid_jwt(key, "https://fcm.googleapis.com", "mailto:owner@example.org", 1_800_000_000)
    assert re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token), "base64url without padding"
    header, claims = verify(token, public)
    assert header == {"typ": "JWT", "alg": "ES256"}
    assert claims == {"aud": "https://fcm.googleapis.com", "exp": 1_800_000_000 + 43200, "sub": "mailto:owner@example.org"}
    assert claims["exp"] - 1_800_000_000 <= 86400, "RFC 8292: at most 24 hours"
    head, body, signature = token.split(".")
    forged = push.b64u(json.dumps({**claims, "aud": "https://evil.example"}).encode())
    with pytest.raises(InvalidSignature):
        verify(f"{head}.{forged}.{signature}", public)
    other = push.public_key(ec.generate_private_key(ec.SECP256R1()))
    with pytest.raises(InvalidSignature):
        verify(token, other)


def test_the_key_is_created_once_as_a_private_file_and_refused_when_loose(tmp_path):
    path = tmp_path / "config" / "pocket" / "vapid.pem"
    first = push.public_key(push.load_key(path))
    assert oct(os.stat(path).st_mode & 0o777) == "0o600" and oct(os.stat(path.parent).st_mode & 0o777) == "0o700"
    assert push.public_key(push.load_key(path)) == first, "generated once, then reused"
    os.chmod(path, 0o644)
    with pytest.raises(PermissionError):
        push.load_key(path)
    os.chmod(path, 0o600)
    link = tmp_path / "link.pem"
    link.symlink_to(path)
    with pytest.raises(PermissionError):
        push.load_key(link)
    junk = tmp_path / "junk.pem"
    junk.write_bytes(b"not a key")
    os.chmod(junk, 0o600)
    with pytest.raises(ValueError):
        push.load_key(junk)


def test_the_cli_needs_an_instance_for_push(tmp_path, capsys):
    with pytest.raises(SystemExit):
        bridge.main(["--db", str(tmp_path / "p.sqlite"), "--push-key", str(tmp_path / "k.pem")])
    assert "--push-key needs --instance-db" in capsys.readouterr().err
    assert not (tmp_path / "k.pem").exists()


def test_an_empty_push_with_ttl_urgency_and_vapid_authorization(spark, fake, devices, monkeypatch):
    a, _, _ = devices
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")   # a proxy would swallow the push: none is used
    assert spark.req("push", {"action": "status"}, a) == (200, {"key": spark.server.push.public, "subscribed": False})
    assert subscribe(spark, a, fake.endpoint("a") + "?token=abc") == (201, {"subscribed": True})
    assert subscribe(spark, a, fake.endpoint("a") + "?token=abc") == (200, {"subscribed": True})
    assert spark.req("push", {"action": "status"}, a)[1]["subscribed"] is True
    assert spark.req("push", {"action": "test"}, a) == (202, {"after": 0})
    now = time.time()
    assert spark.server.push.step(now) == [("127.0.0.1", 201)]
    [request] = fake.requests
    assert request["path"] == "/push/a?token=abc", "the endpoint exactly as the browser's push service returned it"
    assert request["body"] == b""
    headers = request["headers"]
    assert headers["TTL"] == "43200" and headers["Urgency"] == "normal" and headers["Content-Length"] == "0"
    for absent in ("Content-Encoding", "Content-Type", "Encryption", "Crypto-Key", "Topic"):
        assert absent not in headers, absent
    match = re.fullmatch(r"vapid t=([A-Za-z0-9_.-]+), k=([A-Za-z0-9_-]+)", headers["Authorization"])
    assert match and match[2] == spark.server.push.public
    _, claims = verify(match[1], match[2])
    assert claims["aud"] == fake.origin and 0 < claims["exp"] - now <= 86400 and claims["sub"].startswith("mailto:")
    assert QUESTION not in json.dumps({k: v for k, v in headers.items()}), "nothing about a question is sent"
    assert subs(spark) == {"a?token=abc": (0, None)}


@pytest.mark.parametrize("endpoint", GOOD)
def test_known_push_services_are_accepted(spark, devices, endpoint):
    a, _, _ = devices
    push.check_endpoint(endpoint)
    assert subscribe(spark, a, endpoint)[0] == 201


def test_ssrf_endpoints_are_refused_and_never_stored(spark, fake, devices):
    a, _, _ = devices
    for endpoint in BAD + [fake.endpoint("x").replace("127.0.0.1", "localhost")]:
        with pytest.raises(ValueError):
            push.check_endpoint(endpoint)
        too_big = len(json.dumps({"action": "subscribe", "endpoint": endpoint})) > bridge.RESEARCH_BODY_LIMIT
        assert subscribe(spark, a, endpoint) == (400, {} if too_big else {"error": "push_service"}), endpoint
    with pytest.raises(ValueError):
        push.check_endpoint(fake.endpoint("x")), "the local fake is allowed only through the test override"
    assert subs(spark) == {}


@pytest.mark.parametrize("addresses", [["10.0.0.5"], ["127.0.0.1"], ["::1"], ["169.254.169.254"],
                                       ["142.250.1.1", "192.168.1.1"], ["::ffff:127.0.0.1"], []])
def test_an_allowed_name_that_resolves_to_a_private_address_is_never_contacted(tmp_path, addresses):
    connects = []
    sender = push.Push(None, push.load_key(tmp_path / "k.pem"), resolver=lambda host, port: addresses)
    real = socket.create_connection
    try:
        socket.create_connection = lambda *a, **k: connects.append(a) or real(*a, **k)
        with pytest.raises(ValueError):
            sender.request("https://fcm.googleapis.com/fcm/send/x", time.time())
    finally:
        socket.create_connection = real
    assert connects == []


def test_redirects_are_not_followed(spark, fake, devices):
    a, _, _ = devices
    elsewhere = FakePushService()
    try:
        fake.replies.append((307, {"Location": elsewhere.endpoint("stolen")}))
        subscribe(spark, a, fake.endpoint("a"))
        spark.req("push", {"action": "test"}, a)
        assert spark.server.push.step() == [("127.0.0.1", 307)]
        assert elsewhere.requests == [] and len(fake.requests) == 1
        assert subs(spark) == {"a": (0, None)}, "the push is dropped, the subscription kept"
    finally:
        elsewhere.close()


@pytest.mark.parametrize("gone", [404, 410])
def test_a_gone_subscription_is_deleted(spark, fake, devices, gone):
    a, _, c = devices
    subscribe(spark, a, fake.endpoint("a"))
    subscribe(spark, c, fake.endpoint("c"))
    fake.replies += [(gone, {}), (gone, {})]
    spark.req("push", {"action": "test"}, a)
    now = time.time()
    assert spark.server.push.step(now) == [("127.0.0.1", gone)]
    assert subs(spark) == {"a": (1, now + 15), "c": (0, None)}, "a fresh registration: FCM answers 410 at first (live)"
    assert spark.server.push.step(now + push.GRACE) == [("127.0.0.1", gone)]
    assert subs(spark) == {"c": (0, None)}
    assert spark.req("push", {"action": "status"}, a)[1]["subscribed"] is False


def test_429_and_retry_after_are_honoured(spark, fake, devices):
    a, _, _ = devices
    subscribe(spark, a, fake.endpoint("a"))
    sender, now = spark.server.push, time.time()
    fake.replies += [(429, {"Retry-After": "120"}), (429, {"Retry-After": time.strftime(
        "%a, %d %b %Y %H:%M:%S GMT", time.gmtime(now + 121 + 300))}), (503, {})]
    assert spark.req("push", {"action": "test"}, a) == (202, {"after": 0})
    assert sender.step(now) == [("127.0.0.1", 429)]
    assert subs(spark) == {"a": (1, now + 120)}, "still pending, after Retry-After"
    assert spark.req("push", {"action": "test"}, a)[1]["after"] in (119, 120)
    assert sender.step(now + 60) == [] and len(fake.requests) == 1
    assert sender.step(now + 121) == [("127.0.0.1", 429)]
    assert abs(subs(spark)["a"][1] - (now + 121 + 300)) < 2, "an HTTP-date Retry-After"
    assert sender.step(now + 121 + 301) == [("127.0.0.1", 503)]
    assert subs(spark)["a"] == (1, now + 121 + 301 + 120), "5xx without Retry-After: two minutes"
    assert sender.step(now + 121 + 301 + 121) == [("127.0.0.1", 201)]
    assert subs(spark)["a"] == (0, None) and len(fake.requests) == 4


def test_an_unanswering_push_service_times_out_and_is_retried(spark, devices, tmp_path):
    a, _, _ = devices
    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen(4)   # accepts the connection (backlog) and never answers
    origin = f"http://127.0.0.1:{silent.getsockname()[1]}"
    try:
        spark.server.push = push.Push(spark.server, push.load_key(tmp_path / "keys" / "vapid.pem"), timeout=0.5,
                                      test_origins={origin})
        subscribe(spark, a, origin + "/push/a")
        spark.req("push", {"action": "test"}, a)
        started, now = time.monotonic(), time.time()
        assert spark.server.push.step(now) == [("127.0.0.1", 0)]
        assert time.monotonic() - started < 5
        assert subs(spark) == {"a": (1, now + 60)}
    finally:
        silent.close()


def test_family_isolation_and_one_push_per_finish(spark, fake, devices):
    a, b, c = devices
    for token, name in ((a, "a"), (b, "b"), (c, "c")):
        assert subscribe(spark, token, fake.endpoint(name))[0] == 201
    sender, now = spark.server.push, time.time() + 1
    mine = submit(spark, a)[1]["id"]
    sender.step(now)
    assert fake.requests == [], "nothing finished yet"
    finish(spark, mine, now)
    sender.step(now + 1)
    assert sorted(fake.paths()) == ["/push/a", "/push/b"], "family A's finish reaches A's devices, never C"
    sender.step(now + 120)
    assert len(fake.requests) == 2, "each finish is announced once"
    theirs = submit(spark, c, "How fast is the Zephyr 4.2 compiler?")[1]["id"]
    finish(spark, theirs, now + 130)
    sender.step(now + 131)
    assert fake.paths()[2:] == ["/push/c"]


def test_a_finish_before_subscribing_is_not_announced(spark, fake, devices):
    a, _, _ = devices
    earlier = submit(spark, a)[1]["id"]
    finish(spark, earlier, time.time() - 5)
    subscribe(spark, a, fake.endpoint("a"))
    spark.server.push.step()
    assert fake.requests == []


def test_the_rate_limit_collapses_finishes_into_one_push_a_minute(spark, fake, devices):
    a, _, _ = devices
    subscribe(spark, a, fake.endpoint("a"))
    sender, now = spark.server.push, time.time() + 1
    ids = [submit(spark, a, f"{QUESTION} ({n})")[1]["id"] for n in range(3)]
    finish(spark, ids[0], now)
    finish(spark, ids[1], now)
    assert sender.step(now) == [("127.0.0.1", 201)]
    finish(spark, ids[2], now + 10)
    assert sender.step(now + 10) == [] and subs(spark)["a"][0] == 1, "held, not dropped"
    assert 0 < spark.req("push", {"action": "test"}, a)[1]["after"] <= 61, "the page says when it leaves"
    assert sender.step(now + 61) == [("127.0.0.1", 201)]
    assert len(fake.requests) == 2 and subs(spark)["a"][0] == 0


def test_an_owner_finding_reaches_every_subscribed_device(spark, fake, devices):
    a, b, c = devices
    for token, name in ((a, "a"), (c, "c")):
        subscribe(spark, token, fake.endpoint(name))
    now = time.time() + 1
    with spark.owner() as db, db:
        knowledge.open_question(db, "web:autonomy:task-1", "1 newly verified statement(s)", now)
        knowledge.open_question(db, "web:other:task-2", "unrelated question", now)
    spark.server.push.step(now)
    assert sorted(fake.paths()) == ["/push/a", "/push/c"]
    spark.server.push.step(now + 120)
    assert len(fake.requests) == 2


def test_unsubscribe_unpair_revoke_and_erase_delete_the_subscription(spark, fake, devices):
    a, b, c = devices
    d = spark.server.store.pair()
    for token, name in ((a, "a"), (b, "b"), (c, "c"), (d, "d")):
        subscribe(spark, token, fake.endpoint(name))
    assert set(subs(spark)) == {"a", "b", "c", "d"}
    assert spark.req("push", {"action": "unsubscribe"}, d) == (200, {"subscribed": False})
    assert set(subs(spark)) == {"a", "b", "c"}
    assert spark.req("unpair", {}, b)[0] == 200                     # Forget this pairing
    assert set(subs(spark)) == {"a", "c"}
    subscribe(spark, a, fake.endpoint("a2"))
    assert spark.req("unpair", {"family": True}, a)[0] == 200       # Forget all linked devices
    assert set(subs(spark)) == {"c"}
    with closing(bridge.connect(spark.server.store.path)) as db:
        label = db.execute("SELECT label FROM devices WHERE key=?", (bridge.fingerprint(c),)).fetchone()[0]
    assert spark.server.store.erase(label) == 1                     # nsf pocket erase-pairing LABEL (on the Spark)
    assert subs(spark) == {}
    assert spark.req("push", {"action": "status"}, c)[0] == 403, "the credential is gone with its subscription"


def test_forgotten_families_leave_no_push_records(spark, fake, devices):
    a, _, c = devices
    subscribe(spark, a, fake.endpoint("a"))
    task = submit(spark, a)[1]["id"]
    finish(spark, task, time.time() + 1)
    spark.server.push.step(time.time() + 2)
    with closing(bridge.connect(spark.server.store.path)) as db:
        assert db.execute("SELECT count(*) FROM push_seen").fetchone()[0] == 1
    spark.req("unpair", {"family": True}, a)
    spark.server.store.sweep()
    with closing(bridge.connect(spark.server.store.path)) as db:
        assert db.execute("SELECT count(*) FROM push_seen").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM push_subs").fetchone()[0] == 0


def test_the_push_route_is_gated_like_research(spark, fake, devices):
    a, _, c = devices
    assert spark.req("push", {"action": "status"})[0] == 403
    assert spark.req("push", {"action": "status"}, "not-a-credential")[0] == 403
    assert spark.req("push", {"action": "status"}, a, origin="http://evil.example")[0] == 403
    for body in ({}, {"action": "status", "extra": 1}, {"action": "subscribe"}, {"action": "nope"},
                 {"action": "test", "endpoint": fake.endpoint("a")}):
        assert spark.req("push", body, a)[0] == 400, body
    assert spark.req("push", {"action": "test"}, a) == (409, {"error": "not_subscribed"})
    wns = "https://wns2-par02p.notify.windows.com/w/?token=" + "B" * 560   # several hundred bytes, under 1 KiB
    assert subscribe(spark, a, wns)[0] == 201
    assert spark.req("push", raw=json.dumps({"action": "subscribe", "endpoint": "https://fcm.googleapis.com/"
                                             + "a" * 1100}), token=a)[0] == 400
    assert spark.req("state", {}, a)[1]["push"] is True, "the page learns it without probing a 404"
    spark.configure(pocket_research=False)
    assert spark.req("push", {"action": "status"}, a)[0] == 404, "invisible while research is off"
    assert "push" not in spark.req("state", {}, a)[1]
    assert spark.req("push", {"action": "status"})[0] == 403
    spark.configure(pocket_research=True)
    spark.server.push = None
    assert spark.req("push", {"action": "status"}, a)[0] == 404, "the bridge was started without --push-key"
    assert "push" not in spark.req("state", {}, a)[1]


def test_one_browser_subscription_belongs_to_the_device_that_registered_it_last(spark, fake, devices):
    a, _, c = devices
    subscribe(spark, a, fake.endpoint("shared"))
    assert subscribe(spark, c, fake.endpoint("shared"))[0] == 201
    assert spark.req("push", {"action": "status"}, a)[1]["subscribed"] is False
    assert spark.req("push", {"action": "status"}, c)[1]["subscribed"] is True
    assert subscribe(spark, c, fake.endpoint("rotated"))[0] == 201, "a rotated endpoint replaces the old one"
    assert set(subs(spark)) == {"rotated"}


def test_push_replies_are_spark_signed_like_research_replies(spark, fake, devices):
    """Security review B-0: Pocket's call() refuses an unsigned success, so an unsigned push reply kept the switch
    hidden. Every push reply carries X-NSF-Spark-Signature over status, path, the request's nonce and the body."""
    from neuroscifi.webcortex import signing
    a, _, _ = devices
    pin = signing.SparkKey.load_or_create(signing.key_path(spark.instance)).pin()

    def signed():
        status, path, nonce, body, headers = spark.last
        sig = headers.get("X-NSF-Spark-Signature")
        return sig is not None and signing.verify(pin["alg"], signing.unb64(pin["key"]), signing.unb64(sig),
                                                  signing.canonical_response(status, path, nonce, body))
    for body, status in (({"action": "status"}, 200), ({"action": "test"}, 409),
                         ({"action": "subscribe", "endpoint": fake.endpoint("a")}, 201), ({"action": "test"}, 202),
                         ({"action": "unsubscribe"}, 200)):
        assert spark.req("push", body, a)[0] == status, body
        assert signed(), body
