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

    def test_connection_cap_drops_excess(self):
        self.server.slots = threading.BoundedSemaphore(1)
        idle = socket.create_connection(("127.0.0.1", self.server.server_port))
        self.addCleanup(idle.close)
        time.sleep(0.3)
        extra = socket.create_connection(("127.0.0.1", self.server.server_port))
        self.addCleanup(extra.close)
        extra.settimeout(3)
        self.assertEqual(extra.recv(1), b"")  # closed immediately by the server


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
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            s = socket.create_connection(("127.0.0.1", self.server.server_port))
            s.sendall(b"POST /api/save HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{")
            s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            s.close()  # RST while the server is blocked reading the body
            deadline = time.monotonic() + 3
            while "connection error" not in err.getvalue() and time.monotonic() < deadline:
                time.sleep(0.05)
        text = err.getvalue()
        self.assertIn("nsf-pocket: connection error", text)
        self.assertNotIn("127.0.0.1", text)
        self.assertNotIn("Traceback", text)

    def test_excess_plain_connections_are_dropped(self):
        self.server.slots = threading.BoundedSemaphore(1)
        idle = socket.create_connection(("127.0.0.1", self.server.server_port))
        self.addCleanup(idle.close)
        time.sleep(0.3)
        extra = socket.create_connection(("127.0.0.1", self.server.server_port))
        self.addCleanup(extra.close)
        extra.settimeout(3)
        self.assertEqual(extra.recv(1), b"")
