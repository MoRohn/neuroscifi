from pathlib import Path
from http.client import HTTPConnection, HTTPSConnection
import contextlib
import io
import json
import shutil
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from uuid import uuid4
from test_mobile import bridge


@unittest.skipUnless(shutil.which("openssl"), "openssl required for temporary TLS fixture")
class MobileTLS(unittest.TestCase):
    def test_tls_and_pairing(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            cert,key=root/"cert.pem",root/"key.pem"
            subprocess.run(["openssl","req","-x509","-newkey","rsa:2048","-nodes","-days","1","-keyout",str(key),
                            "-out",str(cert),"-subj","/CN=localhost","-addext","subjectAltName=DNS:localhost"],
                           check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert,key)
            server=bridge.PocketServer(("127.0.0.1",0),bridge.Handler,tls=ctx)
            self.addCleanup(server.server_close)
            server.store=bridge.Store(root/"db")
            server.invitation="test";server.pair_lock=threading.Lock()
            server.invitation_deadline=time.monotonic()+30
            threading.Thread(target=server.serve_forever,daemon=True).start()
            try:
                ctx=ssl.create_default_context(cafile=str(cert))
                def req(route,data,token="",origin=None):
                    c=HTTPSConnection("localhost",server.server_port,context=ctx,timeout=3)
                    headers={"Content-Type":"application/json","Authorization":"Bearer "+token}
                    if origin: headers["Origin"]=origin
                    c.request("POST","/api/"+route,json.dumps(data),headers)
                    resp=c.getresponse()
                    status=resp.status;body=resp.read()
                    c.close()
                    return status,json.loads(body) if body else {}
                self.assertEqual(req("pair",{"code":"wrong"})[0],403)
                self.assertEqual(req("pair",{"code":"test"},origin="https://other.invalid")[0],403)
                status,body=req("pair",{"code":"test"})
                token=body["key"]
                self.assertEqual(status,201)
                self.assertEqual(req("pair",{"code":"test"})[0],403)
                event=dict(id=str(uuid4()),channel="light",value="dim",learn=True)
                self.assertEqual(req("save",event)[0],403)
                self.assertEqual(req("save",dict(event,raw="image"),token)[0],400)
                self.assertEqual(req("save",event,token)[0],201)
                self.assertEqual(req("state",{},token)[1]["prediction"]["light"]["dim"],0.5)
                self.assertEqual(req("forget",{"id":event["id"]},token)[0],200)
                self.assertEqual(req("save",event,token)[0],410)
                status, link = req("link",{},token)
                self.assertEqual(status,201)
                status,tablet=req("pair",link)
                self.assertEqual(status,201)
                self.assertEqual(req("pair",link)[0],403)
                ep=dict(id=str(uuid4()),features={"goal":["read","owner"]},outcome="continued",learn=True)
                self.assertEqual(req("episode",ep,token)[0],201)
                self.assertEqual(req("episodes",{},tablet["key"])[1]["episodes"],[ep])
                self.assertEqual(req("forget_episode",{"id":ep["id"]},tablet["key"])[0],200)
                self.assertEqual(req("episode",ep,token)[0],410)
                self.assertEqual(req("unpair",{},token)[0],200)
                self.assertEqual(req("state",{},token)[0],403)
            finally:
                server.shutdown()


def tls_fixture(test, root):
    cert, key = root / "cert.pem", root / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-keyout", str(key),
                    "-out", str(cert), "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    server = bridge.PocketServer(("127.0.0.1", 0), bridge.Handler, tls=ctx)
    server.store = bridge.Store(root / "db")
    server.invitation, server.pair_lock = "test", threading.Lock()
    server.invitation_deadline = time.monotonic() + 30
    threading.Thread(target=server.serve_forever, daemon=True).start()
    test.addCleanup(server.server_close)
    test.addCleanup(server.shutdown)
    return server, ssl.create_default_context(cafile=str(cert))


@unittest.skipUnless(shutil.which("openssl"), "openssl required for temporary TLS fixture")
class PocketTLSRobustness(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.server, self.ctx = tls_fixture(self, Path(tmp.name))

    def get(self, timeout=3):
        c = HTTPSConnection("localhost", self.server.server_port, context=self.ctx, timeout=timeout)
        c.request("GET", "/")
        resp = c.getresponse()
        resp.read()
        headers = dict(resp.getheaders())
        c.close()
        return resp.status, headers

    def test_idle_tcp_connection_does_not_block_other_clients(self):
        idle = socket.create_connection(("127.0.0.1", self.server.server_port))
        self.addCleanup(idle.close)
        started = time.monotonic()
        status, headers = self.get()
        self.assertEqual(status, 200)
        self.assertLess(time.monotonic() - started, 3)

    def test_hsts_on_tls_only(self):
        self.assertEqual(self.get()[1]["Strict-Transport-Security"], "max-age=31536000")

    def test_full_server_evicts_the_oldest_waiting_connection(self):
        self.server.limit = 1
        idle = socket.create_connection(("127.0.0.1", self.server.server_port))
        self.addCleanup(idle.close)
        time.sleep(0.3)
        started = time.monotonic()
        self.assertEqual(self.get()[0], 200)  # the newcomer is served
        idle.settimeout(3)
        self.assertEqual(idle.recv(1), b"")  # the silent pre-handshake connection was shed
        self.assertLess(time.monotonic() - started, 3)

    def test_slow_clients_do_not_lock_out_phones(self):
        """UAT S-01: 32 slow clients (silent before the handshake, or dripping a header after it) from four
        addresses; a phone on another address keeps getting answers, and the slow ones are cut at the deadline."""
        self.server.deadline = 2
        port, slow = self.server.server_port, []
        for n in range(32):
            s = socket.create_connection(("127.0.0.1", port), source_address=(f"127.0.0.{2 + n % 4}", 0))
            self.addCleanup(s.close)
            if n % 2:
                s = self.ctx.wrap_socket(s, server_hostname="localhost")
                s.sendall(b"POST /api/state HTTP/1.1\r\n")
            slow.append(s)
        time.sleep(0.3)
        results = []
        for _ in range(6):
            started = time.monotonic()
            results.append((self.get()[0], time.monotonic() - started < 2))
            for s in slow:
                try:
                    s.sendall(b"X-Slow: 1\r\n" if isinstance(s, ssl.SSLSocket) else b"")
                except OSError:
                    pass
            time.sleep(0.4)
        self.assertEqual(results, [(200, True)] * 6)
        time.sleep(2.5)
        cut = 0
        for s in slow:
            s.settimeout(3)
            try:
                cut += s.recv(1) == b""
            except (OSError, ssl.SSLError):
                cut += 1
        self.assertEqual(cut, 32, "every slow client is cut at the request deadline")


class PocketPlainHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        server = bridge.PocketServer(("127.0.0.1", 0), bridge.Handler)
        server.store = bridge.Store(Path(self.tmp.name) / "db")
        server.invitation, server.pair_lock = "test", threading.Lock()
        server.invitation_deadline = time.monotonic() + 30
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.server = server

    def fetch(self, method, path, data=None, token=""):
        c = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        c.request(method, path, json.dumps(data) if data is not None else None, headers)
        resp = c.getresponse()
        body = resp.read()
        headers = dict(resp.getheaders())
        c.close()
        return resp.status, body, headers

    def test_manifest_icon_and_csp(self):
        status, body, headers = self.fetch("GET", "/manifest.webmanifest")
        self.assertEqual((status, headers["Content-Type"]), (200, "application/manifest+json"))
        manifest = json.loads(body)
        self.assertEqual([s["url"] for s in manifest["shortcuts"]], ["/#workspace", "/#mark", "/#sensor"])
        self.assertTrue(all(i["src"].startswith("/") for i in manifest["icons"]))
        status, icon, headers = self.fetch("GET", "/icon.svg")
        self.assertEqual((status, headers["Content-Type"]), (200, "image/svg+xml"))
        self.assertNotIn(b"http", icon.replace(b"http://www.w3.org/2000/svg", b""))
        csp = headers["Content-Security-Policy"]
        for directive in ("default-src 'none'", "manifest-src 'self'", "img-src 'self'", "connect-src 'self'"):
            self.assertIn(directive, csp)
        self.assertNotIn("unsafe", csp)
        html = self.fetch("GET", "/")[1]
        self.assertIn(b'id="mark"', html)
        self.assertIn(b'rel="manifest"', html)

    def test_mute_routes(self):
        self.assertEqual(self.fetch("POST", "/api/mute", {"channel": "sound"})[0], 403)
        token = json.loads(self.fetch("POST", "/api/pair", {"code": "test"})[1])["key"]
        self.assertEqual(self.fetch("POST", "/api/mute", {"channel": "photo"}, token)[0], 400)
        self.assertEqual(self.fetch("POST", "/api/mute", {"channel": "sound", "x": 1}, token)[0], 400)
        event = dict(id=str(uuid4()), channel="sound", value="soft", learn=True)
        self.assertEqual(self.fetch("POST", "/api/save", event, token)[0], 201)
        status, body, _ = self.fetch("POST", "/api/mute", {"channel": "sound"}, token)
        self.assertEqual((status, json.loads(body)), (200, {"unlearned": 1}))
        self.assertEqual(self.fetch("POST", "/api/save", event, token)[0], 200)
        self.assertEqual(self.fetch("POST", "/api/save", dict(event, id=str(uuid4())), token)[0], 423)
        state = json.loads(self.fetch("POST", "/api/state", {}, token)[1])
        self.assertEqual((state["muted"], state["prediction"]), (["sound"], {}))
        self.assertEqual(self.fetch("POST", "/api/unmute", {"channel": "sound"}, token)[0], 200)
        self.assertEqual(self.fetch("POST", "/api/save", dict(event, id=str(uuid4())), token)[0], 201)

    def test_no_hsts_on_plain_http(self):
        self.assertNotIn("Strict-Transport-Security", self.fetch("GET", "/")[2])

    def test_allowed_host_gives_421(self):
        self.server.allowed_hosts = {"pocket.lan"}
        port = self.server.server_port
        for host, expected in (("pocket.lan", 200), (f"pocket.lan:{port}", 200), ("POCKET.lan", 200),
                               ("evil.example", 421), (f"pocket.lan:{port + 1}", 421), ("", 421)):
            with self.subTest(host=host):
                c = HTTPConnection("127.0.0.1", port, timeout=3)
                c.putrequest("GET", "/", skip_host=True)
                if host:
                    c.putheader("Host", host)
                c.endheaders()
                self.assertEqual(c.getresponse().status, expected)
                c.close()
        c = HTTPConnection("127.0.0.1", port, timeout=3)
        c.request("POST", "/api/pair", "{}", {"Content-Type": "application/json", "Host": "evil.example"})
        self.assertEqual(c.getresponse().status, 421)
        c.close()

    def test_sqlite_error_is_503(self):
        self.server.store.path = self.tmp.name  # a directory: sqlite cannot open it
        token = "x"
        status, body, _ = self.fetch("POST", "/api/state", {}, token)
        self.assertEqual((status, json.loads(body)), (503, {}))

    def test_reset_mid_request_leaks_no_address_or_traceback(self):
        """A device that went away (RST mid-request, a closed tab) prints nothing (R6); another handler error prints
        one fixed line without the address or a traceback."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            s = socket.create_connection(("127.0.0.1", self.server.server_port))
            s.sendall(b"POST /api/save HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{")
            s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            s.close()  # RST while the server is blocked reading the body
            time.sleep(0.5)
            self.assertEqual(err.getvalue(), "")
            try:
                raise ValueError("a real handler bug")
            except ValueError:
                self.server.handle_error(None, ("127.0.0.1", 1))
        text = err.getvalue()
        self.assertEqual(text, "nsf-pocket: connection error\n")
        self.assertNotIn("127.0.0.1", text)
        self.assertNotIn("Traceback", text)

    def test_a_body_shorter_than_its_content_length_is_400_at_once(self):
        """T5-08: the server waited out its socket timeout for the missing bytes, then answered 500 (after 8 s)."""
        s = socket.create_connection(("127.0.0.1", self.server.server_port))
        self.addCleanup(s.close)
        started = time.monotonic()
        s.sendall(b"POST /api/state HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                  b"Authorization: Bearer x\r\nContent-Length: 400\r\n\r\n{}")
        s.settimeout(6)
        head = s.recv(200)
        self.assertTrue(head.startswith(b"HTTP/1.0 400") or head.startswith(b"HTTP/1.1 400"), head)
        self.assertLess(time.monotonic() - started, bridge.BODY_IDLE + 1.5)

    def test_full_server_evicts_a_waiting_connection_not_a_reply(self):
        self.server.limit = 1
        idle = socket.create_connection(("127.0.0.1", self.server.server_port))
        self.addCleanup(idle.close)
        time.sleep(0.3)
        self.assertEqual(self.fetch("GET", "/icon.svg")[0], 200)
        idle.settimeout(3)
        self.assertEqual(idle.recv(1), b"")

    def test_per_address_connection_cap(self):
        self.server.per_ip = 2
        port = self.server.server_port
        held = [socket.create_connection(("127.0.0.1", port), source_address=("127.0.0.7", 0)) for _ in range(2)]
        for s in held:
            self.addCleanup(s.close)
        time.sleep(0.3)
        other = socket.create_connection(("127.0.0.1", port))  # another address, also idle
        self.addCleanup(other.close)
        c = HTTPConnection("127.0.0.1", port, timeout=3, source_address=("127.0.0.7", 0))
        c.request("GET", "/icon.svg")
        self.assertEqual(c.getresponse().status, 200)  # a third connection from .7 is served...
        c.close()
        held[0].settimeout(3)
        self.assertEqual(held[0].recv(1), b"")  # ...by replacing that address's oldest waiting connection
        for s in (held[1], other):
            s.settimeout(0.3)
            with self.assertRaises(TimeoutError):  # still open: neither the other .7 nor another address is shed
                s.recv(1)

    def test_whole_request_deadline_not_per_read(self):
        """A client dripping one header line every 0.3 s never hits the 8 s per-read timeout; the request deadline
        (counted from accept) still cuts it."""
        self.server.deadline = 1
        s = socket.create_connection(("127.0.0.1", self.server.server_port))
        self.addCleanup(s.close)
        s.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n")
        started, closed = time.monotonic(), False
        while time.monotonic() - started < 4:
            time.sleep(0.3)
            try:
                s.sendall(b"X-Drip: 1\r\n")
            except OSError:
                closed = True
                break
            s.settimeout(0.01)
            try:
                if s.recv(1) == b"":
                    closed = True
                    break
            except TimeoutError:
                pass
        self.assertTrue(closed)
        self.assertLess(time.monotonic() - started, 3)

    def test_server_header_and_error_pages(self):
        """UAT S-12: no Python version in Server, and malformed requests get the security headers too."""
        self.assertEqual(self.fetch("GET", "/")[2]["Server"], "NeuroSciFi-Pocket")
        port = self.server.server_port
        for raw, code in ((b"BROKEN\r\n\r\n", 400), (b"GET /" + b"a" * 70000 + b" HTTP/1.1\r\n\r\n", 414),
                          (b"GET / HTTP/1.1\r\n" + b"X-A: b\r\n" * 120 + b"\r\n", 431),
                          (b"PUT / HTTP/1.1\r\nHost: x\r\n\r\n", 501)):
            with self.subTest(code=code):
                s = socket.create_connection(("127.0.0.1", port))
                s.settimeout(3)
                s.sendall(raw)
                head = b""
                while b"\r\n\r\n" not in head:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    head += chunk
                s.close()
                text = head.decode("latin-1")
                self.assertTrue(text.startswith(f"HTTP/1.0 {code}") or text.startswith(f"HTTP/1.1 {code}"), text[:80])
                self.assertIn("Content-Security-Policy: default-src 'none'", text)
                self.assertIn("X-Content-Type-Options: nosniff", text)
                self.assertNotIn("Python", text)
                self.assertNotIn("<html", text.lower())
