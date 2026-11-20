import importlib.util
from pathlib import Path
from http.client import HTTPSConnection
import json
import shutil
import ssl
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
            server=bridge.ThreadingHTTPServer(("127.0.0.1",0),bridge.Handler)
            self.addCleanup(server.server_close)
            ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert,key)
            server.socket=ctx.wrap_socket(server.socket,server_side=True)
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
