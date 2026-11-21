"""NeuroSciFi mobile bridge. Run with --help for pairing and local administration."""
import argparse
import errno
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import sqlite3
import ssl
import stat
import sys
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
for asset in ("field-model.mjs","field-ui.mjs","research-ui.mjs","sw.js"):
    ASSETS["/"+asset]=(asset,"text/javascript")
from ..presence import POCKET as PRESENCE_FILES, assets as presence_assets  # noqa: E402  shared presence + theme
ASSETS.update(presence_assets(PRESENCE_FILES))
from ..voice import assets as voice_assets  # noqa: E402  Voice Cortex client modules
ASSETS.update(voice_assets())
FIELD = {
    "goal":(("work","read","unwind","move"),("owner",)), "sound":(VOCAB["sound"],("mic",)),
    "dynamics":(VOCAB["dynamics"],("mic",)), "visible":(("brief","several","extended"),("pocket",)), "layout":(("low","medium","high"),("image",)),
    "switches":(("few","some","many"),("android","shortcut")), "wakes":(("few","some","many"),("android",)),
    "screenTime":(("under5","5to20","20plus"),("android",)), "appTime":(("under5","5to20","20plus"),("android",)),
    "power":(("saving","standard"),("android","ios")), "thermal":(("normal","elevated","high"),("android","ios"))
}
DAY = 86400
MAX_CONNECTIONS = 32
PER_IP_CONNECTIONS = 8   # one address cannot hold more than a quarter of the slots
HEAD_DEADLINE = 10       # seconds from accept to a complete request (TLS handshake, head and body included)
BODY_IDLE = 2.0          # a body that stops arriving before its Content-Length is incomplete (400)
FAMILY_DEVICES = 6       # devices in one pairing family (the invited device and those linked from it)
BODY_LIMIT = 512
# A 300-character question in a non-Latin script needs up to 3 bytes a character; research_submit alone may send
# 1 KiB (README: deviation from the 512-byte cap of every other route).
RESEARCH_BODY_LIMIT = 1024
INVITATION_SECONDS = 300
INVITATION_FORMAT = re.compile(r"[A-Za-z0-9_-]{32}\n?")
ROUTES = {"pair","save","forget","state","unpair","episode","episodes","forget_episode","link","mute","unmute"}
# Web Cortex research (ADR-028): exist only with --instance-db and the individual's pocket_research switch on.
RESEARCH = {"research_submit","research_list","research_get","research_cancel",
            "research_evidence","research_continue","research_session"}   # 0.7.0: pocket/research_routes.py
# An omitted mode lets the Spark assess one from the question, as the console does.
RESEARCH_MODES = ("quick_lookup","deep_research","fact_verification","comparative","scientific","technical")
MAX_OBJECTIVE = 300
VERDICTS = ("supported", "disputed", "contested", "insufficient")
SUBMISSIONS_PER_HOUR = 10   # per pairing family, kept in the Pocket database so a restart does not reset it
_RATE_LOCK = threading.Lock()


class FamilyFull(Exception):
    """The pairing family already has FAMILY_DEVICES devices."""


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
                  learn INTEGER, until INTEGER, asked INTEGER, PRIMARY KEY(device,id));
                CREATE TABLE IF NOT EXISTS members(device TEXT PRIMARY KEY REFERENCES devices(key) ON DELETE CASCADE,
                  family TEXT NOT NULL, sponsor TEXT, created INTEGER);
                INSERT OR IGNORE INTO members(device,family) SELECT key,key FROM devices;
                CREATE TABLE IF NOT EXISTS submissions(family TEXT NOT NULL, at REAL NOT NULL);
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
            if "asked" not in [r[1] for r in db.execute("PRAGMA table_info(events)")]:
                db.execute("ALTER TABLE events ADD COLUMN asked INTEGER")  # learn value originally requested
            columns = [r[1] for r in db.execute("PRAGMA table_info(members)")]
            for column in ("sponsor TEXT", "created INTEGER"):  # who linked a device and when (list-pairings)
                if column.split()[0] not in columns:
                    db.execute("ALTER TABLE members ADD COLUMN " + column)

    def pair(self, sponsor=None):
        key = secrets.token_urlsafe(32)
        with closing(connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            family, label = fingerprint(key), None
            if sponsor is not None:
                row=db.execute("SELECT m.family,d.label FROM members m JOIN devices d ON d.key=m.device "
                               "WHERE m.device=?",(sponsor,)).fetchone()
                if not row: raise PermissionError()
                family, label = row
                if db.execute("SELECT count(*) FROM members WHERE family=?",(family,)).fetchone()[0] >= FAMILY_DEVICES:
                    raise FamilyFull()
            db.execute("INSERT INTO devices VALUES (?,?)", (fingerprint(key), secrets.token_hex(6)))
            db.execute("INSERT INTO members VALUES(?,?,?,?)",(fingerprint(key),family,label,int(self.clock())))
        return key

    def family_full(self, token):
        """True when the credential's family cannot take another linked device (PermissionError when unknown)."""
        family = self.family(token)
        with closing(connect(self.path)) as db:
            return db.execute("SELECT count(*) FROM members WHERE family=?", (family,)).fetchone()[0] >= FAMILY_DEVICES

    def admit(self, family, submit):
        """Run `submit` unless the family already queued SUBMISSIONS_PER_HOUR tasks in the last hour."""
        with _RATE_LOCK:
            now = self.clock()
            with closing(connect(self.path)) as db:
                recent = db.execute("SELECT count(*) FROM submissions WHERE family=? AND at>?",
                                    (family, now - 3600)).fetchone()[0]
            if recent >= SUBMISSIONS_PER_HOUR:
                return None
            result = submit()
            with closing(connect(self.path)) as db, db:
                db.execute("INSERT INTO submissions VALUES(?,?)", (family, now))
            return result

    def pairings(self):
        """(label, family tag, sponsor label or None, created epoch or None) per device, oldest first."""
        with closing(connect(self.path)) as db:
            rows = db.execute("SELECT d.label,m.family,m.sponsor,m.created FROM devices d "
                              "LEFT JOIN members m ON m.device=d.key ORDER BY d.rowid").fetchall()
        return [(label, family_tag(family), sponsor, created) for label, family, sponsor, created in rows]

    def erase(self, label, family=False):
        """Erase one pairing by label, or with `family` every device of its family. Returns the number erased."""
        with closing(connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if not family:
                return db.execute("DELETE FROM devices WHERE label=?", (label,)).rowcount
            return db.execute("DELETE FROM devices WHERE key IN (SELECT device FROM members WHERE family IN "
                              "(SELECT m.family FROM members m JOIN devices d ON d.key=m.device WHERE d.label=?))",
                              (label,)).rowcount

    def family(self, token):
        """The pairing family fingerprint of a live credential (PermissionError when unknown or revoked)."""
        with closing(connect(self.path)) as db:
            row = db.execute("SELECT family FROM members WHERE device=?", (fingerprint(token),)).fetchone()
        if not row:
            raise PermissionError()
        return row[0]

    @staticmethod
    def expire(db, now):
        db.execute("INSERT OR IGNORE INTO retired SELECT device,id FROM events WHERE until<=?", (now,))
        db.execute("DELETE FROM events WHERE until<=?", (now,))
        db.execute("INSERT OR IGNORE INTO retired_episodes SELECT device,id FROM episodes WHERE until<=?", (now,))
        db.execute("DELETE FROM episodes WHERE until<=?",(now,))
        db.execute("DELETE FROM submissions WHERE at<=? OR family NOT IN (SELECT family FROM members)", (now - 3600,))

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
                row = db.execute("SELECT ch,val,learn,asked FROM events WHERE device=? AND id=?", args).fetchone()
                is_muted = db.execute("SELECT 1 FROM muted WHERE device=? AND ch=?", (key, channel)).fetchone()
                if row is not None:
                    # Muting turned learning off after storage, so a retry of the original request still matches,
                    # also after unmuting. Rows stored before `asked` existed fall back to the muted-now rule.
                    same = row[:2] == (channel, value) and (row[2] == learn or row[3] == learn or
                                                            (row[3] is None and is_muted and learn))
                    return (200 if same else 409), {}
                if learn and is_muted:
                    return 423, {}
                if db.execute("SELECT count(*) FROM events WHERE device=?", (key,)).fetchone()[0] >= 128:
                    return 429, {}
                db.execute("INSERT INTO events(device,id,ch,val,learn,until,asked) VALUES (?,?,?,?,?,?,?)",
                           (key, ident, channel, value, learn, now + DAY, learn))
                return 201, {}
            if operation == "forget" and set(data) == {"id"}:
                args = (key, event_id(data["id"]))
                # Retire even an id not stored yet, so a delayed save of a forgotten event gets 410.
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
            if operation == "unpair" and (not data or (set(data) == {"family"} and data["family"] is True)):
                if data:  # revoke the whole family: this device and every device linked with it
                    db.execute("DELETE FROM devices WHERE key IN (SELECT device FROM members WHERE family="
                               "(SELECT family FROM members WHERE device=?))", (key,))
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


def research_db(server):
    """A connection to the individual when Pocket research is on, else None (its routes then answer 404)."""
    path = getattr(server, "instance_db", None)
    if not path or not Path(path).is_file():
        return None
    from ..instance import store as individual
    from ..webcortex import state
    db = individual.connect(path)
    try:
        cfg = state.get_config(db)
        if cfg.enabled and cfg.pocket_research:
            return db
    except BaseException:
        db.close()
        raise
    db.close()
    return None


def research_on(server):
    db = research_db(server)
    if db is None:
        return False
    db.close()
    return True


def _text(value, limit=1000):
    return value[:limit] if isinstance(value, str) else ""


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _confidence(value):
    """A probability rounded for display, else None (NaN or infinity would also break the JSON reply)."""
    return round(float(value), 3) if _number(value) is not None and 0 <= value <= 1 else None


def _citations(value, n=12):
    return [c for c in value[:n] if type(c) is int] if isinstance(value, list) else []


def _count(value):
    """A small non-negative integer for display, else None (never trusts report JSON blindly)."""
    return value if type(value) is int and 0 <= value <= 1000 else None


def phone_report(report):
    """The report trimmed for a phone: answer, verdict, contradictions, uncertainties, limitations, freshness,
    sources."""
    if not isinstance(report, dict):
        return None
    def side(s):
        return {"text": _text(s.get("text")), "citation": _number(s.get("citation"))} if isinstance(s, dict) else None
    def items(name, n):
        value = report.get(name)
        return [x for x in value[:n] if isinstance(x, dict)] if isinstance(value, list) else []
    def texts(name, n, limit=500):
        value = report.get(name)
        return [_text(x, limit) for x in value[:n] if isinstance(x, str)] if isinstance(value, list) else []
    fresh = report.get("freshness") if isinstance(report.get("freshness"), dict) else {}
    verdict = report.get("verdict") if isinstance(report.get("verdict"), dict) else {}
    return {
        "live": bool(report.get("live")),
        "answer": [{"text": _text(a.get("text")), "type": "quote" if a.get("type") == "quote" else "template",
                    "citations": _citations(a.get("citations")),
                    "status": _text(a.get("status"), 40), "confidence": _confidence(a.get("confidence")),
                    "copies": _count(a.get("copies")), "independent_sources": _count(a.get("independent_sources"))}
                   for a in items("answer", 20)],
        "verdict": {"label": verdict["label"], "confidence": _confidence(verdict.get("confidence")),
                    "statement": _text(verdict.get("statement"), 300), "citations": _citations(verdict.get("citations"))}
                   if verdict.get("label") in VERDICTS else None,
        "contradictions": [{"reason": _text(c.get("reason"), 300), "assessment": _text(c.get("assessment")),
                            "a": side(c.get("a")), "b": side(c.get("b"))} for c in items("contradictions", 10)],
        "uncertainties": texts("uncertainties", 20),
        "limitations": texts("limitations", 20),
        "freshness": {"class": _text(fresh.get("class"), 40), "satisfied": bool(fresh.get("satisfied")),
                      "newest_source_at": _number(fresh.get("newest_source_at"))},
        "sources": [{"n": s.get("n") if type(s.get("n")) is int else None, "title": _text(s.get("title"), 300),
                     "url": _text(s.get("url"), 2000), "domain": _text(s.get("domain"), 255),
                     "published_at": _number(s.get("published_at")), "retrieved_at": _number(s.get("retrieved_at")),
                     "retrieval_mode": "cache" if s.get("retrieval_mode") == "cache" else "live"}
                    for s in items("sources", 40)],
    }


def task_summary(task):
    return {"id": task["id"], "objective": task["objective"], "status": task["status"], "stage": task["stage"],
            "mode": task["mode"], "created": task["created_at"], "updated": task["updated_at"]}


def task_detail(task):
    usage = task.get("usage") if isinstance(task.get("usage"), dict) else {}
    return task_summary(task) | {"stages_done": task.get("stages_done", []),
                                 "cancel_requested": bool(task.get("cancel_requested")),
                                 "error": _text(task.get("error"), 200) or None,
                                 # incremental progress (0.7.0): what the Spark has spent on it so far
                                 "spent": {"searches": _count(usage.get("queries")), "pages": _count(usage.get("pages"))},
                                 "continued_from": task.get("parent_id") if isinstance(task.get("parent_id"), str) else None,
                                 "report": phone_report(task.get("result"))}


def family_tag(family):
    """A short, stable name for a pairing family in list-pairings (not the credential fingerprint itself)."""
    return hashlib.sha256(("pocket-family:" + family).encode()).hexdigest()[:8] if family else "-"


def research(server, token, route, data):
    """Family-scoped research actions (ADR-028). Pocket queues and reads; the Spark's worker runs the task."""
    from ..instance.errors import Conflict, InstanceError, NotFound, ValidationError, WritesPaused
    from ..webcortex import autonomy, nodes, orchestrator, state
    from . import research_routes
    family = server.store.family(token)
    db = research_db(server)
    if db is None:
        return 404, {}
    with closing(db):
        digest = state.family_digest(db, family)  # instance-salted; never the credential or its fingerprint
        try:
            if route == "research_submit":
                objective, mode = data.get("objective"), data.get("mode")
                if not {"objective"} <= set(data) <= {"objective", "mode"}:
                    raise ValueError()
                if "mode" in data and mode not in RESEARCH_MODES:
                    raise ValueError()
                if not isinstance(objective, str) or not objective.strip() or len(objective) > MAX_OBJECTIVE:
                    raise ValueError()
                cfg = state.get_config(db)
                if orchestrator.screen(db, objective, cfg):
                    return 400, {"error": "private_data"}
                try:
                    task = server.store.admit(family, lambda: orchestrator.submit(
                        db, objective, time.time(), mode=mode, origin="pocket", family=digest))
                except autonomy.BudgetRefused:
                    return 429, {"error": "daily_budget"}   # the owner's daily research budget is spent (D4)
                return (429, {}) if task is None else (201, {"id": task["id"]})
            if route == "research_list":
                if data:
                    raise ValueError()
                return 200, {"tasks": [task_summary(t) for t in orchestrator.listing(db, family=digest, limit=50)]}
            if route in research_routes.ROUTES or route == "research_cancel":   # 0.7.0: evidence, continue, sessions
                return research_routes.handle(server, db, digest, nodes.device_digest(db, fingerprint(token)), route,
                                              data, token_family=family)
            if set(data) != {"id"}:
                raise ValueError()
            ident = event_id(data["id"])
            return 200, task_detail(orchestrator.get(db, ident, family=digest))
        except (NotFound, Conflict):
            return 404, {}  # another family's task, a forgotten one, or web access switched off meanwhile
        except ValidationError:
            return 400, {"error": "invalid"}
        except WritesPaused:
            return 507, {"error": "writes_paused"}  # instance regulation (disk pressure); reads keep working
        except InstanceError as error:
            return error.http_status, {}


class Handler(BaseHTTPRequestHandler):
    timeout = 8
    replied = False

    def log_message(self, *args):
        pass  # No observation, address, URL, request body, or token in access logs.

    def version_string(self):
        return "NeuroSciFi-Pocket"  # no Python or library version

    def send_error(self, code, message=None, explain=None):
        """Malformed requests (400, 414, 431, 501) get an empty reply with the same security headers."""
        self.close_connection = True
        if getattr(self, "request_version", "") in ("", "HTTP/0.9"):
            self.request_version = "HTTP/1.0"  # a status line and headers even for an unparsable request line
        if not self.replied:
            self.reply(code)

    def read_body(self, size):
        """Exactly `size` body bytes, or ValueError (-> 400) when the client sends fewer than its Content-Length:
        a gap of BODY_IDLE seconds ends the wait (UAT T5-08: it waited out the socket timeout and answered 500)."""
        try:
            self.connection.settimeout(BODY_IDLE)
            data = self.rfile.read(size)
        except OSError:
            raise ValueError("incomplete request body") from None
        finally:
            try:
                self.connection.settimeout(self.timeout)
            except OSError:
                pass
        if len(data) != size:
            raise ValueError("incomplete request body")
        return data

    def reply(self, code, body=b"", mime="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body, allow_nan=False).encode()
        self.replied = True
        getattr(self.server, "replying", lambda: None)()  # the request is complete: its deadline no longer applies
        self.send_response(code)
        for name, value in {
            "Content-Type": mime, "Content-Length": str(len(body)), "Cache-Control": "no-store",
            "Referrer-Policy":"no-referrer","X-Content-Type-Options":"nosniff",
            "Permissions-Policy": "camera=(self), microphone=(self), geolocation=()",
            "Content-Security-Policy": "default-src 'none'; worker-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; manifest-src 'self'; img-src 'self'; media-src 'self' blob:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
            **({"Strict-Transport-Security": "max-age=31536000"} if isinstance(self.connection, ssl.SSLSocket) else {}),
        }.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def host_allowed(self):
        names = getattr(self.server, "allowed_hosts", None)
        if not names:
            return True
        host = self.headers.get("Host", "").lower()
        return host in names or host in {f"{n}:{self.server.server_port}" for n in names}

    def do_GET(self):
        if not self.host_allowed():
            return self.reply(421)
        # The page alone accepts a query: the share target (manifest.webmanifest) opens /?share_url=…; the page
        # reads it locally and nothing here looks at it.
        asset = ASSETS.get("/" if self.path.startswith("/?") else self.path)
        if not asset:
            return self.reply(404)
        name, mime = asset
        try:
            body = (WEB/name).read_bytes()
        except OSError:   # a listed asset missing on disk (incomplete install) is a 404, not a dropped connection
            return self.reply(404)
        self.reply(200, body, mime)

    def do_POST(self):
        if not self.host_allowed():
            return self.reply(421)
        route = self.path.removeprefix("/api/")
        from ..voice.http import POCKET_ROUTES as VOICE
        if self.path != "/api/" + route or route not in ROUTES | RESEARCH | VOICE | {"presence"}:
            return self.reply(404)
        origin = self.headers.get("Origin")
        scheme = "https://" if isinstance(self.connection, ssl.SSLSocket) else "http://"
        if origin and origin != scheme + self.headers.get("Host", ""):
            return self.reply(403)
        if route in VOICE:   # Voice Cortex: own auth and size rules (voice/http.py: JSON <= 512 B, audio <= 64 KiB)
            try:
                from ..voice.http import pocket as voice_pocket
                return self.reply(*voice_pocket(self, route))
            except sqlite3.Error:
                return self.reply(503, {})
            except Exception:
                if self.replied:
                    raise
                print("nsf-pocket: voice request failed", file=sys.stderr, flush=True)
                return self.reply(500, {})
        try:
            if route in RESEARCH:
                # Authenticate before consulting the feature state, so a caller without a live credential gets the
                # same empty 403 whether research is on or off. A paired device then sees 404 while it is off.
                auth = self.headers.get("Authorization", "")
                if not auth.startswith("Bearer "):
                    return self.reply(403)
                self.server.store.family(auth[7:])
                if not research_on(self.server):
                    return self.reply(404)  # invisible while off, like an unknown route
            size = int(self.headers.get("Content-Length", "0"))
            limit = RESEARCH_BODY_LIMIT if route == "research_submit" else BODY_LIMIT
            if self.headers.get_content_type() != "application/json" or not 2 <= size <= limit:
                return self.reply(400)
            data = json.loads(self.read_body(size))
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
                        remove_invitation_file(self.server)
                    else:
                        link=getattr(self.server,"handoffs",{}).pop(fingerprint(code),None)
                        if not link or now>=link[1]: return self.reply(403)
                        try:
                            key=self.server.store.pair(sponsor=link[0])
                        except FamilyFull:
                            return self.reply(409, {"error": "family_full"})
                return self.reply(201, {"key":key})
            auth = self.headers.get("Authorization","")
            if not auth.startswith("Bearer "):
                return self.reply(403)
            if route=="link":
                if data: raise ValueError()
                self.server.store.action(auth[7:],"authorize_link",{})
                if self.server.store.family_full(auth[7:]):
                    return self.reply(409, {"error": "family_full"})
                with self.server.pair_lock:
                    now=time.monotonic(); sponsor=fingerprint(auth[7:])
                    handoffs={k:v for k,v in getattr(self.server,"handoffs",{}).items() if now<v[1] and v[0]!=sponsor}
                    code=secrets.token_urlsafe(24)
                    handoffs[fingerprint(code)]=(sponsor,now+300)
                    self.server.handoffs=handoffs
                return self.reply(201,{"code":code})
            if route in RESEARCH:
                return self.reply(*research(self.server, auth[7:], route, data))
            if route == "presence":  # family-scoped presence state (presence/routes.py)
                from ..presence.routes import pocket_state
                return self.reply(*pocket_state(self.server, auth[7:], data))
            status, payload = self.server.store.action(auth[7:], route, data)
            if route == "state" and status == 200 and research_on(self.server):
                payload["research"] = True  # lets the page show its Research panel without probing a 404
            self.reply(status, payload)
        except PermissionError:
            self.reply(403)
        except (ValueError, TypeError):
            self.reply(400)
        except sqlite3.Error:
            self.reply(503, {})
        except Exception:
            if self.replied:
                raise
            print("nsf-pocket: request failed", file=sys.stderr, flush=True)  # no body, token or traceback
            self.reply(500, {})


class _Connection:
    __slots__ = ("sock", "ip", "accepted", "phase", "evicted")

    def __init__(self, sock, ip):
        self.sock, self.ip, self.accepted, self.phase, self.evicted = sock, ip, time.monotonic(), "handshake", False


def _abort(sock):
    """Wake a thread blocked on this socket: the raw shutdown, also beneath an SSLSocket, makes its read return EOF."""
    try:
        socket.socket.shutdown(sock, socket.SHUT_RDWR)
    except OSError:
        pass


class PocketServer(ThreadingHTTPServer):
    """Threaded server whose TLS handshake runs in the worker thread, with connection limits.

    Wrapping the listening socket would handshake inside accept(), so one silent TCP connection froze every client.
    Slow clients cannot lock phones out: each connection must deliver its whole request (handshake, head, body)
    within HEAD_DEADLINE seconds of accept; one address holds at most PER_IP_CONNECTIONS; and when a cap is reached,
    a new connection evicts the oldest one still waiting for its request (a pre-handshake one first), from its own
    address when that address is at its cap.
    """
    def __init__(self, address, handler=Handler, tls=None, limit=MAX_CONNECTIONS, per_ip=PER_IP_CONNECTIONS,
                 deadline=HEAD_DEADLINE):
        super().__init__(address, handler)
        self.tls, self.limit, self.per_ip, self.deadline = tls, limit, per_ip, deadline
        self.live, self.live_lock, self.current = set(), threading.Lock(), threading.local()

    def process_request(self, request, client_address):
        conn = _Connection(request, client_address[0])
        with self.live_lock:
            active = [c for c in self.live if not c.evicted]
            same = [c for c in active if c.ip == conn.ip]
            # Over a cap, the newcomer replaces the oldest connection still waiting for its request: from its own
            # address when that address is at its cap, else from anyone. If every candidate is replying, drop it.
            pool = same if len(same) >= self.per_ip else active if len(active) >= self.limit else None
            admitted = True
            if pool is not None:
                waiting = sorted((c for c in pool if c.phase != "reply"),
                                 key=lambda c: (c.phase != "handshake", c.accepted))
                admitted = bool(waiting)
                if waiting:
                    waiting[0].evicted = True
                    _abort(waiting[0].sock)
            if admitted:
                self.live.add(conn)
        if not admitted:
            self.shutdown_request(request)  # over a cap: drop at once
            return
        try:
            threading.Thread(target=self.process_request_thread, args=(request, client_address, conn),
                             daemon=True).start()
        except BaseException:
            with self.live_lock:
                self.live.discard(conn)
            raise

    def process_request_thread(self, request, client_address, conn):
        self.current.conn = conn
        try:
            try:
                request.settimeout(Handler.timeout)
                if self.tls:
                    request = self.tls.wrap_socket(request, server_side=True, do_handshake_on_connect=False)
                    with self.live_lock:
                        conn.sock = request
                    if conn.evicted:
                        raise OSError()
                    request.do_handshake()
                with self.live_lock:
                    if conn.evicted:
                        raise OSError()
                    conn.phase = "head"
            except (OSError, ValueError):
                self.shutdown_request(request)
                return
            try:
                self.finish_request(request, client_address)
            except Exception:
                if not conn.evicted:
                    self.handle_error(request, client_address)
            finally:
                self.shutdown_request(request)
        finally:
            with self.live_lock:
                self.live.discard(conn)

    def replying(self):
        """Called by the handler once the request is complete: from now on only the per-send timeout applies."""
        conn = getattr(self.current, "conn", None)
        if conn is not None:
            with self.live_lock:
                conn.phase = "reply"

    def service_actions(self):
        """Every poll of serve_forever: cut connections that have not delivered their request in time."""
        now = time.monotonic()
        with self.live_lock:
            for conn in self.live:
                if conn.phase != "reply" and not conn.evicted and now - conn.accepted > self.deadline:
                    conn.evicted = True
                    _abort(conn.sock)

    def handle_error(self, request, client_address):
        print("nsf-pocket: connection error", file=sys.stderr, flush=True)  # no address, no traceback


def stale_invitation(path):
    """True for a leftover of an earlier bridge: a regular 0600 file we own, not a symlink, holding one code, that
    no running bridge holds locked or whose code has expired (older than INVITATION_SECONDS)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600):
            return False
        if not INVITATION_FORMAT.fullmatch(os.read(fd, 64).decode("ascii", "replace")):
            return False
        if time.time() - info.st_mtime >= INVITATION_SECONDS:
            return True
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False  # a running bridge holds it: its code may still be valid
        return True
    finally:
        os.close(fd)


def write_invitation_file(path, code):
    """Write the code to `path` with mode 0600 and return the open descriptor, which holds a lock for as long as the
    bridge runs. An existing file is replaced (temporary file, then rename) only when it is a stale invitation of an
    earlier bridge; anything else, including a symlink, raises FileExistsError."""
    path = Path(path)
    stale = os.path.lexists(path)
    if stale and not stale_invitation(path):
        raise FileExistsError(errno.EEXIST, "exists and is not a stale invitation", str(path))
    target = path.with_name(f".{path.name}.{os.getpid()}.tmp") if stale else path
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.write(fd, (code + "\n").encode())
        if stale:
            os.replace(target, path)  # rename never follows a symlink at `path`
    except BaseException:
        os.close(fd)
        if stale:
            try:
                os.unlink(target)
            except FileNotFoundError:
                pass
        raise
    return fd


def remove_invitation_file(server):
    path = getattr(server, "invitation_file", None)
    if path:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default="mobile-private.sqlite")
    p.add_argument("--bind", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--cert")
    p.add_argument("--key")
    p.add_argument("--invitation-file", metavar="PATH",
                   help="write the one-use code to this 0600 file instead of stdout; removed when used, expired or at "
                        "shutdown; only a stale invitation left by an earlier bridge is replaced")
    p.add_argument("--allowed-host", action="append", metavar="NAME",
                   help="accept only this Host (name or name:port); repeatable; others get 421")
    p.add_argument("--instance-db", metavar="PATH",
                   help="the individual's database; with its pocket_research switch on, paired devices may queue "
                        "and read research tasks of their own family")
    p.add_argument("--list-pairings", action="store_true")
    p.add_argument("--erase-pairing", metavar="LABEL")
    p.add_argument("--family", action="store_true",
                   help="with --erase-pairing: erase every device of that label's pairing family")
    a = p.parse_args(argv)
    if a.family and not a.erase_pairing:
        p.error("--family needs --erase-pairing LABEL")
    os.umask(0o077)
    store = Store(a.db)
    store.sweep()
    if a.erase_pairing:
        print(f"Erased pairings: {store.erase(a.erase_pairing, family=a.family)}")
        return
    if a.list_pairings:
        for label, family, sponsor, created in store.pairings():
            when = time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(created)) if created else "unknown"
            print(f"{label}  family={family}  sponsor={sponsor or 'invitation'}  created={when}")
        return
    if bool(a.cert) != bool(a.key) or (a.bind not in {"127.0.0.1","localhost"} and not a.cert):
        p.error("LAN access requires both --cert and --key for HTTPS.")
    if a.instance_db:
        from ..instance import store as individual
        from ..instance.errors import InstanceError
        try:
            db = individual.open_existing(a.instance_db)  # migrates; research tasks live in the individual
            try:
                from ..voice import transcripts
                transcripts.end_orphans(db, "pocket:", time.time())   # voice sessions of a bridge that died (T5-08)
            finally:
                db.close()
        except (InstanceError, sqlite3.Error) as error:
            p.error(f"cannot open instance {a.instance_db}: {error}")
    ctx = None
    if a.cert:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(a.cert,a.key)
    try:
        server = PocketServer((a.bind,a.port), Handler, tls=ctx)
    except OSError as error:
        print(f"nsf: cannot listen on {a.bind}:{a.port}: {error.strerror or error}", file=sys.stderr)
        raise SystemExit(2)
    server.allowed_hosts = {h.lower() for h in a.allowed_host or ()}
    server.store, server.pair_lock = store, threading.Lock()
    server.instance_db = a.instance_db
    server.invitation = secrets.token_urlsafe(24)
    server.invitation_deadline = time.monotonic() + INVITATION_SECONDS
    server.invitation_file = a.invitation_file
    held = None
    if a.invitation_file:
        try:
            signal.signal(signal.SIGTERM, signal.default_int_handler)  # service stop runs the cleanup below
        except ValueError:
            pass  # not the main thread
        try:
            held = write_invitation_file(a.invitation_file, server.invitation)
        except OSError as error:
            server.server_close()
            p.error(f"cannot create invitation file {a.invitation_file} (only a stale invitation of an earlier "
                    f"bridge is replaced; other files are never overwritten): {error.strerror or error}")
        announcement = f"Invitation written to {a.invitation_file}"
        expiry = threading.Timer(INVITATION_SECONDS, lambda: remove_invitation_file(server))
        expiry.daemon = True
        expiry.start()
    else:
        announcement = f"One-use pairing code (5 minutes): {server.invitation}"
    stop = threading.Event()
    def upkeep():
        while not stop.wait(60):
            store.sweep()
    threading.Thread(target=upkeep, daemon=True).start()
    try:
        print(announcement, flush=True)  # inside the try: a stop signal right after this line still cleans up
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        remove_invitation_file(server)
        if held is not None:
            os.close(held)


if __name__ == "__main__":
    main()
