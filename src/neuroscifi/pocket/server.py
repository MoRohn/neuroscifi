"""NeuroSciFi mobile bridge. Run with --help for pairing and local administration."""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import ssl
import threading
import time
from collections import Counter
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID

WEB = Path(__file__).resolve().parent / "web"
VOCAB = {"sound": ("soft", "moderate", "strong"), "light": ("dim", "midrange", "bright"),
         "dynamics": ("steady","variable","wide"), "activity": ("focus", "walk", "rest", "other")}
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"), "/mobile.js": ("mobile.js", "text/javascript"),
          "/style.css": ("style.css", "text/css"),
          "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
          "/icon.svg": ("icon.svg", "image/svg+xml")}
for asset in ("field-model.mjs","field-ui.mjs","sw.js"):
    ASSETS["/"+asset]=(asset,"text/javascript")
FIELD = {
    "goal":(("work","read","unwind","move"),("owner",)), "sound":(VOCAB["sound"],("mic",)),
    "dynamics":(VOCAB["dynamics"],("mic",)), "visible":(("brief","several","extended"),("pocket",)), "layout":(("low","medium","high"),("image",)),
    "switches":(("few","some","many"),("android","shortcut")), "wakes":(("few","some","many"),("android",)),
    "screenTime":(("under5","5to20","20plus"),("android",)), "appTime":(("under5","5to20","20plus"),("android",)),
    "power":(("saving","standard"),("android","ios")), "thermal":(("normal","elevated","high"),("android","ios"))
}
DAY = 86400


def fingerprint(token):
    return hashlib.sha256(token.encode()).hexdigest()


def event_id(value):
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError()
    parsed = UUID(value)
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError()
    return value


def validate(data):
    if set(data) != {"id", "channel", "value", "learn"} or type(data["learn"]) is not bool:
        raise ValueError()
    if not isinstance(data["channel"], str) or data["value"] not in VOCAB.get(data["channel"], ()):
        raise ValueError()
    return event_id(data["id"]), data["channel"], data["value"], int(data["learn"])


def connect(path):
    db = sqlite3.connect(path, timeout=5)
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA secure_delete=ON")
    return db


class Store:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = path, clock
        with closing(connect(path)) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS devices(key TEXT PRIMARY KEY, label TEXT UNIQUE NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                  device TEXT REFERENCES devices(key) ON DELETE CASCADE, id TEXT, ch TEXT, val TEXT,
                  learn INTEGER, until INTEGER, PRIMARY KEY(device,id));
                CREATE TABLE IF NOT EXISTS members(device TEXT PRIMARY KEY REFERENCES devices(key) ON DELETE CASCADE,
                  family TEXT NOT NULL);
                INSERT OR IGNORE INTO members SELECT key,key FROM devices;
                CREATE TABLE IF NOT EXISTS episodes(device TEXT REFERENCES devices(key) ON DELETE CASCADE,
                  id TEXT, body TEXT, until INTEGER,PRIMARY KEY(device,id));
                CREATE TABLE IF NOT EXISTS retired_episodes(device TEXT REFERENCES devices(key) ON DELETE CASCADE,
                  id TEXT, PRIMARY KEY(device,id));
                CREATE TABLE IF NOT EXISTS family_retired(family TEXT,id TEXT,PRIMARY KEY(family,id));
                CREATE TRIGGER IF NOT EXISTS erase_field_origin BEFORE DELETE ON devices BEGIN
                  INSERT OR IGNORE INTO family_retired SELECT m.family,e.id FROM members m JOIN episodes e
                    ON e.device=m.device WHERE m.device=OLD.key;
                  INSERT OR IGNORE INTO family_retired SELECT m.family,r.id FROM members m JOIN retired_episodes r
                    ON r.device=m.device WHERE m.device=OLD.key;
                  DELETE FROM family_retired WHERE family IN
                    (SELECT family FROM members WHERE device=OLD.key) AND NOT EXISTS
                    (SELECT 1 FROM members m WHERE m.family=family_retired.family AND m.device!=OLD.key);
                END;
                CREATE TABLE IF NOT EXISTS retired(
                  device TEXT REFERENCES devices(key) ON DELETE CASCADE,id TEXT,PRIMARY KEY(device,id));
                CREATE TABLE IF NOT EXISTS muted(
                  device TEXT REFERENCES devices(key) ON DELETE CASCADE,ch TEXT,PRIMARY KEY(device,ch));
            """)

    def pair(self, sponsor=None):
        key = secrets.token_urlsafe(32)
        with closing(connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            family=fingerprint(key)
            if sponsor is not None:
                row=db.execute("SELECT family FROM members WHERE device=?",(sponsor,)).fetchone()
                if not row: raise PermissionError()
                family=row[0]
            db.execute("INSERT INTO devices VALUES (?,?)", (fingerprint(key), secrets.token_hex(6)))
            db.execute("INSERT INTO members VALUES(?,?)",(fingerprint(key),family))
        return key

    @staticmethod
    def expire(db, now):
        db.execute("INSERT OR IGNORE INTO retired SELECT device,id FROM events WHERE until<=?", (now,))
        db.execute("DELETE FROM events WHERE until<=?", (now,))
        db.execute("INSERT OR IGNORE INTO retired_episodes SELECT device,id FROM episodes WHERE until<=?", (now,))
        db.execute("DELETE FROM episodes WHERE until<=?",(now,))

    def sweep(self):
        with closing(connect(self.path)) as db, db:
            self.expire(db, int(self.clock()))

    def action(self, token, operation, data):
        key, now = fingerprint(token), int(self.clock())
        with closing(connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM devices WHERE key=?", (key,)).fetchone():
                raise PermissionError()
            self.expire(db, now)
            if operation in {"episodes","episode","forget_episode","authorize_link"}:
                family=db.execute("SELECT family FROM members WHERE device=?",(key,)).fetchone()[0]
            if operation=="authorize_link" and not data:
                return 200, {}
            if operation == "episodes" and not data:
                cursor=db.execute("SELECT body FROM episodes WHERE device IN (SELECT device FROM members WHERE family=?) ORDER BY rowid DESC LIMIT 256",(family,))
                return 200, {"episodes":[json.loads(r[0]) for r in cursor]}
            if operation == "forget_episode" and set(data)=={"id"}:
                ident=event_id(data["id"])
                row=db.execute("SELECT device FROM episodes WHERE id=? AND device IN (SELECT device FROM members WHERE family=?)",(ident,family)).fetchone()
                if not row: return 200, {}
                args=(row[0],ident)
                db.execute("INSERT OR IGNORE INTO retired_episodes SELECT device,id FROM episodes WHERE device=? AND id=?",args)
                db.execute("DELETE FROM episodes WHERE device=? AND id=?",args)
                return 200, {}
            if operation == "episode":
                if set(data)!={"id","features","outcome","learn"} or type(data["learn"]) is not bool:
                    raise ValueError()
                if data["outcome"] not in ("continued","switched","paused"):
                    raise ValueError()
                f=data["features"]
                if not isinstance(f,dict) or not f:
                    raise ValueError()
                for name,pair in f.items():
                    if name not in FIELD or not isinstance(pair,list) or len(pair)!=2:
                        raise ValueError()
                    values,sources=FIELD[name]
                    if pair[0] not in values or pair[1] not in sources:
                        raise ValueError()
                args=(key,event_id(data["id"]))
                if db.execute("SELECT 1 FROM retired_episodes WHERE device IN (SELECT device FROM members WHERE family=?) AND id=? UNION ALL SELECT 1 FROM family_retired WHERE family=? AND id=?",(family,args[1],family,args[1])).fetchone():
                    return 410, {}
                body=json.dumps(data,sort_keys=True,separators=(",",":"))
                row=db.execute("SELECT body FROM episodes WHERE device IN (SELECT device FROM members WHERE family=?) AND id=?",(family,args[1])).fetchone()
                if row: return (200 if row[0]==body else 409), {}
                if db.execute("SELECT count(*) FROM episodes WHERE device=?",(key,)).fetchone()[0]>=64:
                    return 429, {}
                db.execute("INSERT INTO episodes VALUES(?,?,?,?)",(*args,body,now+DAY))
                return 201, {}
            if operation == "save":
                ident, channel, value, learn = validate(data)
                args = (key, ident)
                if db.execute("SELECT 1 FROM retired WHERE device=? AND id=?", args).fetchone():
                    return 410, {}
                row = db.execute("SELECT ch,val,learn FROM events WHERE device=? AND id=?", args).fetchone()
                is_muted = db.execute("SELECT 1 FROM muted WHERE device=? AND ch=?", (key, channel)).fetchone()
                if row is not None:
                    # Muting turned learning off after storage, so a retry of the original request still matches.
                    return (200 if row == (channel, value, learn) or (is_muted and learn and row[:2] == (channel, value)) else 409), {}
                if learn and is_muted:
                    return 423, {}
                if db.execute("SELECT count(*) FROM events WHERE device=?", (key,)).fetchone()[0] >= 128:
                    return 429, {}
                db.execute("INSERT INTO events VALUES (?,?,?,?,?,?)", (key, ident, channel, value, learn, now + DAY))
                return 201, {}
            if operation == "forget" and set(data) == {"id"}:
                args = (key, event_id(data["id"]))
                if db.execute("SELECT 1 FROM events WHERE device=? AND id=?", args).fetchone():
                    db.execute("INSERT OR IGNORE INTO retired VALUES (?,?)", args)
                    db.execute("DELETE FROM events WHERE device=? AND id=?", args)
                return 200, {}
            if operation in {"mute", "unmute"} and set(data) == {"channel"}:
                channel = data["channel"]
                if not isinstance(channel, str) or channel not in VOCAB:
                    raise ValueError()
                if operation == "unmute":
                    db.execute("DELETE FROM muted WHERE device=? AND ch=?", (key, channel))
                    return 200, {}
                db.execute("INSERT OR IGNORE INTO muted VALUES (?,?)", (key, channel))
                return 200, {"unlearned": db.execute("UPDATE events SET learn=0 WHERE device=? AND ch=? AND learn=1", (key, channel)).rowcount}
            if operation == "unpair" and not data:
                db.execute("DELETE FROM devices WHERE key=?", (key,))
                return 200, {}
            if operation == "state" and not data:
                rows = db.execute("SELECT id,ch,val,learn FROM events WHERE device=? ORDER BY rowid DESC", (key,)).fetchall()
                records = [dict(id=i, channel=c, value=v, learn=bool(l)) for i,c,v,l in rows]
                counts = Counter((c,v) for _,c,v,l in rows if l)
                # Symmetric unit prior. Each retained, learning-approved event contributes one vote.
                prediction = {}
                for channel, values in VOCAB.items():
                    n = sum(counts[channel, v] for v in values)
                    if n:
                        prediction[channel] = {v: (counts[channel,v]+1)/(n+len(values)) for v in values}
                muted = [c for (c,) in db.execute("SELECT ch FROM muted WHERE device=? ORDER BY ch", (key,))]
                return 200, {"records": records, "prediction": prediction, "muted": muted}
            raise ValueError()


class Handler(BaseHTTPRequestHandler):
    timeout = 8
    def log_message(self, *args):
        pass  # No observation, address, URL, request body, or token in access logs.

    def reply(self, code, body=b"", mime="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body, allow_nan=False).encode()
        self.send_response(code)
        for name, value in {
            "Content-Type": mime, "Content-Length": str(len(body)), "Cache-Control": "no-store",
            "Referrer-Policy":"no-referrer","X-Content-Type-Options":"nosniff",
            "Permissions-Policy": "camera=(self), microphone=(self), geolocation=()",
            "Content-Security-Policy": "default-src 'none'; worker-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; manifest-src 'self'; img-src 'self'; media-src 'self' blob:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
        }.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        asset = ASSETS.get(self.path)
        if not asset:
            return self.reply(404)
        name, mime = asset
        self.reply(200, (WEB/name).read_bytes(), mime)

    def do_POST(self):
        route = self.path.removeprefix("/api/")
        if self.path != "/api/" + route or route not in {"pair","save","forget","state","unpair","episode","episodes","forget_episode","link","mute","unmute"}:
            return self.reply(404)
        origin = self.headers.get("Origin")
        scheme = "https://" if isinstance(self.connection, ssl.SSLSocket) else "http://"
        if origin and origin != scheme + self.headers.get("Host", ""):
            return self.reply(403)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if self.headers.get_content_type() != "application/json" or not 2 <= size <= 512:
                return self.reply(400)
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                return self.reply(400)
            if route == "pair":
                with self.server.pair_lock:
                    code = data.get("code")
                    if set(data)!={"code"} or not isinstance(code,str) or not code.isascii():
                        return self.reply(403)
                    now=time.monotonic()
                    if self.server.invitation and now<self.server.invitation_deadline and hmac.compare_digest(self.server.invitation,code):
                        key=self.server.store.pair()
                        self.server.invitation=None
                    else:
                        link=getattr(self.server,"handoffs",{}).pop(fingerprint(code),None)
                        if not link or now>=link[1]: return self.reply(403)
                        key=self.server.store.pair(sponsor=link[0])
                return self.reply(201, {"key":key})
            auth = self.headers.get("Authorization","")
            if not auth.startswith("Bearer "):
                return self.reply(403)
            if route=="link":
                if data: raise ValueError()
                self.server.store.action(auth[7:],"authorize_link",{})
                with self.server.pair_lock:
                    now=time.monotonic(); sponsor=fingerprint(auth[7:])
                    handoffs={k:v for k,v in getattr(self.server,"handoffs",{}).items() if now<v[1] and v[0]!=sponsor}
                    code=secrets.token_urlsafe(24)
                    handoffs[fingerprint(code)]=(sponsor,now+300)
                    self.server.handoffs=handoffs
                return self.reply(201,{"code":code})
            status, payload = self.server.store.action(auth[7:], route, data)
            self.reply(status, payload)
        except PermissionError:
            self.reply(403)
        except (ValueError, TypeError):
            self.reply(400)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default="mobile-private.sqlite")
    p.add_argument("--bind", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--cert")
    p.add_argument("--key")
    p.add_argument("--list-pairings", action="store_true")
    p.add_argument("--erase-pairing", metavar="LABEL")
    a = p.parse_args(argv)
    os.umask(0o077)
    store = Store(a.db)
    store.sweep()
    if a.list_pairings or a.erase_pairing:
        with closing(connect(a.db)) as db, db:
            if a.erase_pairing:
                n = db.execute("DELETE FROM devices WHERE label=?", (a.erase_pairing,)).rowcount
                print(f"Erased pairings: {n}")
            else:
                for row in db.execute("SELECT label FROM devices ORDER BY rowid"):
                    print(row[0])
        return
    if bool(a.cert) != bool(a.key) or (a.bind not in {"127.0.0.1","localhost"} and not a.cert):
        p.error("LAN access requires both --cert and --key for HTTPS.")
    server = ThreadingHTTPServer((a.bind,a.port), Handler)
    if a.cert:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(a.cert,a.key)
        server.socket = ctx.wrap_socket(server.socket,server_side=True)
    server.store, server.pair_lock = store, threading.Lock()
    server.invitation = secrets.token_urlsafe(24)
    server.invitation_deadline = time.monotonic() + 300
    print(f"One-use pairing code (5 minutes): {server.invitation}", flush=True)
    stop = threading.Event()
    def upkeep():
        while not stop.wait(60):
            store.sweep()
    threading.Thread(target=upkeep, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
