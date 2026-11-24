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
          "/icon.svg": ("icon.svg", "image/svg+xml"),
          # home-screen icons, rendered from the brain logo by scripts/brand/build_app_icons.py
          "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"), "/icon-192.png": ("icon-192.png", "image/png"),
          "/icon-512.png": ("icon-512.png", "image/png"),
          "/icon-maskable-512.png": ("icon-maskable-512.png", "image/png")}
for asset in ("field-model.mjs","field-ui.mjs","research-ui.mjs","node-key.mjs","sw.js"):
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
            "research_evidence","research_continue","research_session",   # 0.7.0: pocket/research_routes.py
            "node_enrol"}                                                  # attested node enrolment (ADR-042 draft)
PUSH = "push"   # Web Push opt-in (ADR-040, pocket/push.py): exists only with research on and --push-key
NONCE = re.compile(r"[A-Za-z0-9_-]{16,64}")   # X-NSF-Nonce: binds the Spark's signed reply to this request
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
                -- Web Push (ADR-040): one endpoint per opted-in device, erased with the device (`created`: opted in,
                -- `stored`: this endpoint registered); push_seen holds which finishes were announced (family '' = the
                -- owner's autonomy findings), ids only, for two days.
                CREATE TABLE IF NOT EXISTS push_subs(device TEXT PRIMARY KEY REFERENCES devices(key) ON DELETE CASCADE,
                  endpoint TEXT UNIQUE NOT NULL, created REAL NOT NULL, stored REAL NOT NULL,
                  pending INTEGER NOT NULL DEFAULT 0, last REAL, not_before REAL);
                CREATE TABLE IF NOT EXISTS push_seen(family TEXT NOT NULL, item TEXT NOT NULL, at REAL NOT NULL,
                  PRIMARY KEY(family,item));
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

    def pairings(self, instance=None):
        """(label, family tag, sponsor label or None, created epoch or None) per device, oldest first. `instance` is
        the open individual's database the family tag is salted with (family_tag); without it the tag is '-'."""
        with closing(connect(self.path)) as db:
            rows = db.execute("SELECT d.label,m.family,m.sponsor,m.created FROM devices d "
                              "LEFT JOIN members m ON m.device=d.key ORDER BY d.rowid").fetchall()
        return [(label, family_tag(family, instance), sponsor, created) for label, family, sponsor, created in rows]

    def erase(self, label, family=False):
        """Erase one pairing by label, or with `family` every device of its family. Returns the number erased."""
        with closing(connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if not family:
                return db.execute("DELETE FROM devices WHERE label=?", (label,)).rowcount
            return db.execute("DELETE FROM devices WHERE key IN (SELECT device FROM members WHERE family IN "
                              "(SELECT m.family FROM members m JOIN devices d ON d.key=m.device WHERE d.label=?))",
                              (label,)).rowcount

    def label(self, token):
        """The pairing's label (list-pairings), shown to the owner beside a pending node enrolment."""
        with closing(connect(self.path)) as db:
            row = db.execute("SELECT label FROM devices WHERE key=?", (fingerprint(token),)).fetchone()
        return row[0] if row else ""

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
        db.execute("DELETE FROM push_seen WHERE at<=? OR (family!='' AND family NOT IN (SELECT family FROM members))",
                   (now - 2 * DAY,))

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


def spark_key(server):
    """The Spark's Ed25519 signing key (webcortex/signing.py): --spark-key, else `<instance>.spark-key`; created once
    with mode 0600. None while the bridge has no individual (no research, nothing to sign or pin)."""
    from ..webcortex import signing
    path = getattr(server, "spark_key_path", None)
    if not path and getattr(server, "instance_db", None):
        path = str(signing.key_path(server.instance_db))
    if not path:
        return None
    cached = getattr(server, "_spark_key", None)
    if cached is None or cached[0] != path:
        cached = server._spark_key = (path, signing.SparkKey.load_or_create(path))
    return cached[1]


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


CLAIM_STATES = ("corroborated", "supported", "single_source", "contested", "insufficient", "disputed")


def _reasoning(value):
    """The model-written answer as the console shows it (R6 D8): who wrote it, its guarded sentences with their
    citations and claim states, and the rules notes; None when the report has none."""
    if not isinstance(value, dict):
        return None
    def note(name):
        x = value.get(name)
        return _text(x.get("text"), 600) or None if isinstance(x, dict) else None
    by = _text(value.get("generated_by"), 160)
    model = by.startswith("model:")
    rows = value.get("answer") if model and isinstance(value.get("answer"), list) else []
    answer = [{"text": _text(s.get("text")), "citations": _citations(s.get("citations")),
               "states": sorted({x for x in (s.get("states") or {}).values() if x in CLAIM_STATES})
               if isinstance(s.get("states"), dict) else [],
               "flagged": isinstance(s.get("flag"), dict)}
              for s in rows[:12] if isinstance(s, dict) and isinstance(s.get("text"), str) and s["text"]]
    return {"generated_by": by if model else "template", "answer": answer, "note": note("note"),
            "unanswered": note("unanswered")}


def phone_report(report):
    """The report trimmed for a phone: the model-written answer (reasoning), answer, verdict, contradictions,
    uncertainties, limitations, freshness, sources."""
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
        "reasoning": _reasoning(report.get("reasoning")),
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


def family_tag(family, instance):
    """The one name of a pairing family the owner sees (R6 D4): the first 8 hex digits of the instance-salted family
    digest (webcortex.state.family_digest), as the console, `nsf web node list` and the start of a task's `family` in
    `nsf web tasks` show it; never the credential fingerprint. `instance` is the open individual's database; without
    one no surface shows a family, so the tag is '-'."""
    if not family or instance is None:
        return "-"
    from ..webcortex import state
    return state.family_digest(instance, family)[:8]


def research(server, token, route, data, raw=b"", headers=None):
    """Family-scoped research actions (ADR-028). Pocket queues and reads; the Spark's worker runs the task.
    `raw` and `headers` are the request's body bytes and headers, for a node's signature (ADR-042 draft)."""
    from ..instance.errors import Conflict, InstanceError, NotFound, ValidationError, WritesPaused
    from ..webcortex import autonomy, nodes, orchestrator, state
    from . import research_routes
    family = server.store.family(token)
    db = research_db(server)
    if db is None:
        return 404, {}
    with closing(db):
        digest = state.family_digest(db, family)  # instance-salted; never the credential or its fingerprint
        device = nodes.device_digest(db, fingerprint(token))
        try:
            refused = research_routes.gate(db, digest, device, route, data, raw, headers or {}, time.time())
            if refused:
                return refused
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
                from ..webcortex.scheduler import worker_status   # R6 setup (h): say when no worker runs
                return 200, {"tasks": [task_summary(t) for t in orchestrator.listing(db, family=digest, limit=50)],
                             "worker_running": worker_status(server.instance_db)["running"]}
            if route in research_routes.ROUTES or route == "research_cancel":   # 0.7.0: evidence, continue, sessions
                return research_routes.handle(server, db, digest, device, route, data, token_family=family,
                                              pocket_label=server.store.label(token))
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

    def reply(self, code, body=b"", mime="application/json", nonce=None):
        """`nonce` (a research reply): sign status, path, the request's nonce and the body with the Spark's key."""
        if not isinstance(body, bytes):
            body = json.dumps(body, allow_nan=False).encode()
        extra, spark = {}, spark_key(self.server) if nonce is not None else None
        if spark is not None:
            from ..webcortex.signing import b64, canonical_response
            extra["X-NSF-Spark-Signature"] = b64(spark.sign(canonical_response(code, self.path, nonce, body)))
        self.replied = True
        getattr(self.server, "replying", lambda: None)()  # the request is complete: its deadline no longer applies
        self.send_response(code)
        for name, value in {
            "Content-Type": mime, "Content-Length": str(len(body)), "Cache-Control": "no-store",
            "Referrer-Policy":"no-referrer","X-Content-Type-Options":"nosniff",
            "Permissions-Policy": "camera=(self), microphone=(self), geolocation=()",
            "Content-Security-Policy": "default-src 'none'; worker-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; manifest-src 'self'; img-src 'self'; media-src 'self' blob:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
            **({"Strict-Transport-Security": "max-age=31536000"} if isinstance(self.connection, ssl.SSLSocket) else {}),
            **extra,
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
        if self.path != "/api/" + route or route not in ROUTES | RESEARCH | VOICE | {"presence", PUSH}:
            return self.reply(404)
        # Every reply of a research or push route, refusals and errors included, is signed over this nonce
        # (ADR-042 section 4); field, presence and voice replies are not (signed is None).
        nonce = self.headers.get("X-NSF-Nonce", "")
        signed = (nonce if NONCE.fullmatch(nonce) else "") if route in RESEARCH or route == PUSH else None
        origin = self.headers.get("Origin")
        scheme = "https://" if isinstance(self.connection, ssl.SSLSocket) else "http://"
        if origin and origin != scheme + self.headers.get("Host", ""):
            return self.reply(403, nonce=signed)
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
            if route in RESEARCH or route == PUSH:
                # Authenticate before consulting the feature state, so a caller without a live credential gets the
                # same empty 403 whether research is on or off. A paired device then sees 404 while it is off.
                auth = self.headers.get("Authorization", "")
                if not auth.startswith("Bearer "):
                    return self.reply(403, nonce=signed)
                self.server.store.family(auth[7:])
                if not research_on(self.server):
                    return self.reply(404, nonce=signed)  # invisible while off, like an unknown route
            size = int(self.headers.get("Content-Length", "0"))
            # a push endpoint URL can take several hundred bytes (WNS), so `push` shares research_submit's 1 KiB
            limit = RESEARCH_BODY_LIMIT if route in ("research_submit", PUSH) else BODY_LIMIT
            if self.headers.get_content_type() != "application/json" or not 2 <= size <= limit:
                return self.reply(400, nonce=signed)
            raw = self.read_body(size)
            data = json.loads(raw)
            if not isinstance(data, dict):
                return self.reply(400, nonce=signed)
            if route == "pair":
                with self.server.pair_lock:
                    code = data.get("code")
                    # "via": "invitation" (ADR-043): a code that arrived in a link (a QR code's #pair=) may only be
                    # the Spark's own invitation, never a linked device's handoff code, so a link cannot put this
                    # device into someone else's family
                    via = data.get("via")
                    if set(data)-{"via"}!={"code"} or via not in (None,"invitation") or not isinstance(code,str) or not code.isascii():
                        return self.reply(403)
                    now=time.monotonic()
                    if self.server.invitation and now<self.server.invitation_deadline and hmac.compare_digest(self.server.invitation,code):
                        key=self.server.store.pair()
                        used=getattr(self.server,"invitations_used",None)
                        if used is not None and getattr(self.server,"invitation_id",None):
                            used[self.server.invitation_id]=time.time()   # before the code is cleared: no "expired" gap
                            while len(used)>32: used.pop(next(iter(used)))
                        self.server.invitation=None
                        remove_invitation_file(self.server)
                    elif via=="invitation":
                        return self.reply(403)
                    else:
                        link=getattr(self.server,"handoffs",{}).pop(fingerprint(code),None)
                        if not link or now>=link[1]: return self.reply(403)
                        try:
                            key=self.server.store.pair(sponsor=link[0])
                        except FamilyFull:
                            return self.reply(409, {"error": "family_full"})
                spark = spark_key(self.server)   # pinned by the device: it verifies every research reply with it
                return self.reply(201, {"key":key, **({"spark": spark.pin()} if spark else {})})
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
            if route == PUSH:   # Pocket's call() verifies push replies like research replies (ADR-040, ADR-042)
                from . import push
                return self.reply(*push.route(self.server, auth[7:], data), nonce=signed)
            if route in RESEARCH:
                return self.reply(*research(self.server, auth[7:], route, data, raw, self.headers), nonce=signed)
            if route == "presence":  # family-scoped presence state (presence/routes.py)
                from ..presence.routes import pocket_state
                return self.reply(*pocket_state(self.server, auth[7:], data))
            status, payload = self.server.store.action(auth[7:], route, data)
            if route == "state" and status == 200 and research_on(self.server):
                payload["research"] = True  # lets the page show its Research panel without probing a 404
                if getattr(self.server, "push", None) is not None:
                    payload["push"] = True  # and its push switch (ADR-040)
            self.reply(status, payload)
        except PermissionError:
            self.reply(403, nonce=signed)
        except (ValueError, TypeError):
            self.reply(400, nonce=signed)
        except sqlite3.Error:
            self.reply(503, {}, nonce=signed)
        except Exception:
            if self.replied:
                raise
            print("nsf-pocket: request failed", file=sys.stderr, flush=True)  # no body, token or traceback
            self.reply(500, {}, nonce=signed)


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

    def server_close(self):
        beacon = getattr(self, "audio_beacon", None)   # started with the voice hub (voice.http.hub_for)
        if beacon is not None:
            beacon.close()                               # an open session's audio state is withdrawn at once
        super().server_close()
        # Last (review F2): a request still being answered may write its transcript; the hub, once closed, makes
        # such a write in that request's own thread and starts no writer again.
        hub = getattr(self, "voice_hub", None)
        if hub is not None:
            hub.close()                                  # its transcript writer and task followers end here

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
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return            # the device went away (tab closed, network switched): not an error worth reporting
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


# `nsf pocket invite` (R6 setup (b)): the owner asks the RUNNING bridge for a new one-use terminal pairing code, so
# another family pairs without a restart. The request goes over a Unix socket only this user can use: a 0700
# folder of its own ($NSF_POCKET_CONTROL_DIR, which the systemd unit sets to /run/neuroscifi; else
# $XDG_RUNTIME_DIR/neuroscifi; else /tmp/neuroscifi-<uid>), the socket 0600, and the peer's uid
# (SO_PEERCRED) must be ours. The new code replaces any earlier terminal code, is one-use and lasts
# INVITATION_SECONDS; it goes back to the asking process only (never to stdout, logs or the invitation file).

def control_path(db) -> Path:
    """The control socket of the bridge serving Pocket database `db` (short: AF_UNIX paths are limited)."""
    base = os.environ.get("XDG_RUNTIME_DIR")
    folder = Path(os.environ["NSF_POCKET_CONTROL_DIR"]) if os.environ.get("NSF_POCKET_CONTROL_DIR") else \
        Path(base) / "neuroscifi" if base and Path(base).is_dir() else Path(f"/tmp/neuroscifi-{os.getuid()}")
    digest = hashlib.sha256(os.path.realpath(db).encode()).hexdigest()[:16]
    return folder / f"pocket-{digest}.sock"


def _private_folder(folder: Path):
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = os.lstat(folder)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise OSError(errno.EPERM, "not a private folder of this user (0700)", str(folder))


def new_invitation(server, open_setup=False) -> str:
    """Set a new one-use code on `server` (valid INVITATION_SECONDS, replacing any earlier one) with an id the owner's
    tools can ask about (`status`). `open_setup`: the owner asked for codes, so the phone setup page offers the CA for
    setup_page.SETUP_WINDOW; start-up and the console's automatic renewals do not reopen it."""
    from .setup_page import SETUP_WINDOW
    earlier = getattr(server, "invitation_id", None)
    if earlier:
        replaced = server.__dict__.setdefault("invitations_replaced", {})
        replaced[earlier] = True
        while len(replaced) > 32:
            replaced.pop(next(iter(replaced)))
    server.invitation = secrets.token_urlsafe(24)
    server.invitation_deadline = time.monotonic() + INVITATION_SECONDS
    server.invitation_id = secrets.token_hex(8)
    if open_setup:
        server.setup_until = time.monotonic() + SETUP_WINDOW
    return server.invitation


def invitation_state(server, ident) -> str:
    """'used', 'pending', 'expired', 'replaced' (a newer code was issued) or 'unknown' for an invitation id handed out
    by the control socket. Call under server.pair_lock (the pair route records 'used' under it)."""
    if ident in getattr(server, "invitations_used", {}):
        return "used"
    if ident == getattr(server, "invitation_id", None):
        return "pending" if server.invitation and time.monotonic() < server.invitation_deadline else "expired"
    return "replaced" if ident in getattr(server, "invitations_replaced", {}) else "unknown"


def public_host(server) -> str:
    """The address phones on the LAN use in a QR code: the bound address when it is a LAN address, else the first
    LAN IP address on the certificate, else this host's name. IP addresses, not .local names: Android resolves
    .local unreliably, and browsers never apply HSTS to an IP address (the setup page is plain HTTP)."""
    import ipaddress
    bind = server.server_address[0]

    def lan(text):
        try:
            ip = ipaddress.ip_address(text.strip("[]"))
        except ValueError:
            return False
        return not (ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_link_local)
    if lan(bind):
        return f"[{bind}]" if ":" in bind else bind
    names = getattr(server, "certificate_names", []) or []
    ip = next((n for n in names if lan(n) and ":" not in n), None) or next((n for n in names if lan(n)), None)
    if ip:
        return f"[{ip}]" if ":" in ip else ip
    return next((n for n in names if n not in ("localhost",) and not lan(n) and "." in n), socket.gethostname())


def invitation_reply(server, code) -> dict:
    """What the owner's tools need for the two QR codes: the pairing URL (code in the fragment, which browsers never
    send) and the setup page URL; the CA fingerprint for an optional check."""
    host = public_host(server)
    setup = getattr(server, "setup_httpd", None)
    return {"code": code, "expires_in": INVITATION_SECONDS, "id": server.invitation_id,
            "pair_url": f"https://{host}:{server.server_port}/#pair={code}",
            "app_url": f"https://{host}:{server.server_port}/",
            "setup_url": f"http://{host}:{setup.server_port}/" if setup is not None else None,
            "setup_seconds": int(max(0.0, getattr(server, "setup_until", 0.0) - time.monotonic())),
            "ca_sha256": getattr(server, "ca_sha256", None)}


def issue_invitation(server, open_setup=True) -> str:
    """A new one-use terminal pairing code for `server`, valid INVITATION_SECONDS; it replaces any earlier one."""
    with server.pair_lock:
        new_invitation(server, open_setup)
        remove_invitation_file(server)       # the file's earlier code is no longer valid
        return server.invitation


def open_control(server, db):
    """Listen on control_path(db) for `nsf pocket invite`; None (with a note on stderr) when it cannot."""
    path = control_path(db)
    try:
        _private_folder(path.parent)
        if path.exists() or path.is_symlink():
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.connect(str(path))
                raise OSError(errno.EADDRINUSE, "another bridge serves this database", str(path))
            except (ConnectionRefusedError, FileNotFoundError):
                os.unlink(path)               # left by a bridge that died
            finally:
                probe.close()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            sock.bind(str(path))
        finally:
            os.umask(old)
        os.chmod(path, 0o600)
        sock.listen(4)
    except OSError as error:
        print(f"nsf-pocket: `nsf pocket invite` is unavailable for this bridge: {error.strerror or error}",
              file=sys.stderr, flush=True)
        return None

    def serve():
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return                        # closed at shutdown
            with conn:
                try:
                    conn.settimeout(5)
                    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                    if int.from_bytes(creds[4:8], sys.byteorder) != os.getuid():
                        continue              # only the bridge's own user
                    request = conn.recv(64).strip()
                    if request in (b"invite", b"renew"):   # renew: a new code, the setup window left as it is
                        code = issue_invitation(server, open_setup=request == b"invite")
                        conn.sendall(json.dumps(invitation_reply(server, code)).encode() + b"\n")
                    elif re.fullmatch(rb"status [0-9a-f]{16}", request):
                        with server.pair_lock:
                            state = invitation_state(server, request[7:].decode())
                        conn.sendall(json.dumps({"state": state}).encode() + b"\n")
                    else:
                        conn.sendall(b'{"error": "unknown request"}\n')
                except OSError:
                    pass
    threading.Thread(target=serve, daemon=True).start()
    return sock, path


def close_control(control):
    if control is not None:
        sock, path = control
        try:
            sock.shutdown(socket.SHUT_RDWR)   # wakes the thread waiting in accept(); close() alone leaves it there
        except OSError:
            pass
        sock.close()
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def _control(db, request: bytes, timeout=5.0) -> dict:
    path = control_path(db)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(path))
        sock.sendall(request + b"\n")
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    try:
        reply = json.loads(data or b"{}")
    except ValueError:
        reply = None
    if not isinstance(reply, dict) or "error" in reply:
        raise OSError(errno.EPROTO, "the bridge refused the request", str(path))
    return reply


def request_invitation(db, timeout=5.0, renew=False) -> dict:
    """Ask the running bridge for `db` for a new code: {"code", "expires_in", "id", "pair_url", "app_url",
    "setup_url" (None without a setup page), "setup_seconds", "ca_sha256"}; OSError when none answers. `renew`: an
    automatic renewal, which does not reopen the phone setup page's certificate window."""
    reply = _control(db, b"renew" if renew else b"invite", timeout)
    if not isinstance(reply.get("code"), str):
        raise OSError(errno.EPROTO, "the bridge refused the request", str(control_path(db)))
    return reply


def invitation_status(db, ident: str, timeout=5.0) -> str:
    """'used', 'pending', 'expired', 'replaced' or 'unknown' for an id from request_invitation; OSError when no
    bridge answers."""
    if not re.fullmatch(r"[0-9a-f]{16}", ident or ""):
        return "unknown"
    state = _control(db, b"status " + ident.encode(), timeout).get("state")
    return state if state in ("used", "pending", "expired", "replaced", "unknown") else "unknown"


def certificate_names(cert_path) -> list[str]:
    """The DNS names and IP addresses in a PEM certificate's subjectAltName (the first certificate of a chain)."""
    try:
        info = ssl._ssl._test_decode_cert(str(cert_path))
    except (OSError, ssl.SSLError, AttributeError):
        return []
    return [value for kind, value in info.get("subjectAltName", ()) if kind in ("DNS", "IP Address")]


def listen_lines(bind, port, cert) -> list[str]:
    """What the bridge prints at start (R6 setup (a)): its listen URL and, with a certificate, the URL for each name
    on it (what a phone may type; `nsf pocket tls init` put them there)."""
    scheme = "https" if cert else "http"
    host = f"[{bind}]" if ":" in bind else bind
    lines = [f"Listening on {scheme}://{host}:{port}"]
    if cert:
        names = [n for n in certificate_names(cert) if n not in ("localhost", "127.0.0.1", "::1")
                 and not n.startswith("::1")]
        urls = [f"https://{'[' + n + ']' if ':' in n else n}:{port}" for n in names]
        if urls:
            lines.append("Open on your phone or tablet (names on the certificate): " + ", ".join(urls))
    return lines


def main(argv=None, *, started=None):
    """`started(server)` is called once the bridge listens: whoever runs it in a thread (a test, an embedding
    process) ends it with `server.shutdown()`, after which everything below is closed as on a service stop."""
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
    p.add_argument("--push-key", metavar="PATH",
                   help="offer Web Push to paired devices (ADR-040): the Spark's VAPID private key, created as a new "
                        "0600 file on first start; keep it outside git. Needs --instance-db and the crypto extra")
    p.add_argument("--setup-port", type=int, metavar="PORT",
                   help="also serve the phone setup page (ADR-043) on this plain-HTTP port: the CA certificate and "
                        "step-by-step instructions, offered for 15 minutes after each new invitation; never the app, "
                        "the API or a pairing code. Needs --ca-file")
    p.add_argument("--ca-file", metavar="PATH",
                   help="the CA certificate (DER or PEM, never a key) the setup page offers to phones")
    p.add_argument("--push-contact", metavar="URI", default=None,
                   help="the VAPID contact (mailto: or https:) the push service sees; default "
                        "mailto:pocket@neuroscifi.invalid")
    p.add_argument("--spark-key", metavar="PATH",
                   help="the Spark's Ed25519 signing key (created once, mode 0600; default: INSTANCE_DB.spark-key); "
                        "devices pin its fingerprint at pairing and check every research reply against it")
    p.add_argument("--list-pairings", action="store_true",
                   help="print paired devices; with --instance-db each family tag matches the console's")
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
        instance = None
        if a.instance_db:
            from ..instance import store as individual
            from ..instance.errors import InstanceError
            try:
                instance = individual.open_existing(a.instance_db)
            except (InstanceError, sqlite3.Error) as error:
                p.error(f"cannot open instance {a.instance_db}: {error}")
        else:
            print("nsf: family tags need the individual's database (--instance-db PATH or NSF_DB); they are the tags "
                  "the console and `nsf web node list` show", file=sys.stderr)
        try:
            for label, family, sponsor, created in store.pairings(instance):
                when = time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(created)) if created else "unknown"
                print(f"{label}  family={family}  sponsor={sponsor or 'invitation'}  created={when}")
        finally:
            if instance is not None:
                instance.close()
        return
    if bool(a.cert) != bool(a.key) or (a.bind not in {"127.0.0.1","localhost"} and not a.cert):
        p.error("LAN access requires both --cert and --key for HTTPS.")
    vapid = None
    if a.push_key or a.push_contact:
        if not a.push_key or not a.instance_db:
            p.error("--push-key needs --instance-db (pushes announce finished research); --push-contact needs --push-key")
        if a.push_contact and not re.fullmatch(r"(mailto:|https://)[!-~]{3,200}", a.push_contact):
            p.error("--push-contact must be a mailto: or https:// URI")
        try:
            from . import push
            vapid = push.load_key(a.push_key)
        except ImportError:
            p.error("Web Push needs the crypto extra: pip install 'neuroscifi[crypto]'")
        except (OSError, ValueError) as error:
            p.error(f"cannot use push key {a.push_key}: {error}")
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
    holder = argparse.Namespace(instance_db=a.instance_db, spark_key_path=a.spark_key)
    if a.instance_db or a.spark_key:
        try:
            spark = spark_key(holder)
        except ImportError:
            p.error("signed research replies need the cryptography package: pip install 'neuroscifi[crypto]'")
        except (OSError, ValueError) as error:
            p.error(f"cannot use the Spark signing key: {error}")
        print(f"Spark key fingerprint: {spark.fingerprint}", flush=True)
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
    server.push = None
    if vapid is not None:
        server.push = push.Push(server, vapid, **({"subject": a.push_contact} if a.push_contact else {}))
    server.spark_key_path, server._spark_key = holder.spark_key_path, getattr(holder, "_spark_key", None)
    server.certificate_names = certificate_names(a.cert) if a.cert else []
    server.invitations_used = {}
    server.setup_httpd = None
    if a.ca_file:
        from . import setup_page
        try:
            server.ca_der, server.ca_sha256 = setup_page.load_ca(a.ca_file)
        except (OSError, ValueError) as error:
            server.server_close()
            p.error(f"cannot use CA certificate {a.ca_file}: {getattr(error, 'strerror', None) or error}")
    if a.setup_port is not None:
        from . import setup_page
        if not a.ca_file or not a.cert:
            server.server_close()
            p.error("--setup-port needs --cert and --ca-file (the page offers that CA to phones)")
        try:
            server.setup_httpd = setup_page.start(server, a.bind, a.setup_port)
        except OSError as error:
            server.server_close()
            print(f"nsf: cannot listen on {a.bind}:{a.setup_port}: {error.strerror or error}", file=sys.stderr)
            raise SystemExit(2)
    new_invitation(server)
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
    control = open_control(server, a.db)
    if control is not None:
        try:
            signal.signal(signal.SIGTERM, signal.default_int_handler)  # a service stop removes the control socket
        except ValueError:
            pass  # not the main thread
    stop = threading.Event()
    def upkeep():
        while not stop.wait(60):
            store.sweep()
    threading.Thread(target=upkeep, daemon=True).start()
    if server.push is not None:
        threading.Thread(target=server.push.run, args=(stop,), daemon=True).start()
    try:
        print(announcement, flush=True)  # inside the try: a stop signal right after this line still cleans up
        for line in listen_lines(a.bind, server.server_port, a.cert):
            print(line, flush=True)
        if server.setup_httpd is not None:
            print(f"Phone setup page (plain HTTP, the CA certificate only): http://{public_host(server)}:"
                  f"{server.setup_httpd.server_port}/", flush=True)
        if control is not None:
            print(f"Pair another family later without a restart: nsf pocket invite --db {a.db}", flush=True)
            print(f"Add a phone with QR codes: nsf add-phone --pocket-db {a.db}  (or the console's Add a phone)",
                  flush=True)
        if started is not None:
            started(server)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        if server.push is not None:
            server.push.wake.set()
        server.server_close()
        if server.setup_httpd is not None:
            server.setup_httpd.shutdown()
            server.setup_httpd.server_close()
        close_control(control)
        remove_invitation_file(server)
        if held is not None:
            os.close(held)


if __name__ == "__main__":
    main()
