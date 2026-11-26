"""Voice sessions, transcripts, retained audio and capture ownership in the instance database (schema v9).

Transcript model (docs/voice/STREAMING_AND_TRANSCRIPTS.md):
- one segment per utterance (user) or reply (neuroscifi), ordered by `seq` within a session, timestamped
  (started_at / ended_at of the speech, created_at / updated_at of the row);
- user speech: source 'asr', authority 'approximate', status 'provisional' while the utterance is open, then
  'final'; typed text: source 'typed', authority 'user_typed';
- NeuroSciFi replies: source 'model' (passed the evidence guard) or 'template', authority 'authoritative'; status
  'interrupted' when barge-in stopped playback (detail.spoken_until = sentences actually played);
- corrections keep the original ASR text and add corrected_text (only on approximate segments);
- raw audio exists only when retention is on, in voice_audio with expires_at (sweep() deletes it).
Capture ownership: one row per individual ('owner' scope); a lease of LEASE_SECONDS renewed by the capturing device.
"""
from __future__ import annotations

import json
import secrets
import time

from ..instance import governance, store
from ..instance.errors import Conflict, NotFound, ValidationError

LEASE_SECONDS = 20.0
SCOPE = "owner"
MAX_TEXT = 20000


def _row(cursor, row):
    return dict(zip([c[0] for c in cursor.description], row))


def _rows(db, sql, params=()):
    cur = db.execute(sql, params)
    return [_row(cur, r) for r in cur.fetchall()]


# ---- sessions --------------------------------------------------------------------------------------------------

def start_session(db, principal, device, mode, now) -> str:
    sid = secrets.token_hex(12)
    with store.write(db):
        db.execute("INSERT INTO voice_sessions(id,principal,device,mode,status,context,started_at,updated_at) "
                   "VALUES(?,?,?,?,'active','{}',?,?)", (sid, principal, device, mode, now, now))
    return sid


def session(db, sid) -> dict:
    rows = _rows(db, "SELECT * FROM voice_sessions WHERE id=?", (sid,))
    if not rows:
        raise NotFound("voice session not found")
    out = rows[0]
    out["context"] = json.loads(out["context"] or "{}")
    return out


def update_session(db, sid, now, *, mode=None, status=None, context=None):
    with store.write(db):
        row = db.execute("SELECT context FROM voice_sessions WHERE id=?", (sid,)).fetchone()
        if row is None:
            raise NotFound("voice session not found")
        ctx = json.loads(row[0] or "{}")
        if context:
            ctx.update(context)
        db.execute("UPDATE voice_sessions SET mode=coalesce(?,mode), status=coalesce(?,status), context=?, "
                   "ended_at=CASE WHEN ?='ended' THEN ? ELSE ended_at END, updated_at=? WHERE id=?",
                   (mode, status, json.dumps(ctx, sort_keys=True), status, now, now, sid))


def end_orphans(db, device_prefix, now) -> int:
    """Mark ended every session still 'active' on a device of this server (`console:` or `pocket:`): a server
    starting holds no session, so those belonged to a process that died (UAT T5-08). Returns how many."""
    if not db.execute("SELECT 1 FROM voice_sessions WHERE status='active' AND substr(device,1,?)=? LIMIT 1",
                      (len(device_prefix), device_prefix)).fetchone():
        return 0     # no write at all on the usual start
    with store.write(db):
        return db.execute("UPDATE voice_sessions SET status='ended', ended_at=?, updated_at=? WHERE "
                          "status='active' AND substr(device,1,?)=?",
                          (now, now, len(device_prefix), device_prefix)).rowcount


def last_context(db, principal) -> dict:
    """Context of the principal's most recent session (survives session end and process restarts)."""
    row = db.execute("SELECT context FROM voice_sessions WHERE principal=? ORDER BY updated_at DESC, rowid DESC "
                     "LIMIT 1", (principal,)).fetchone()
    return json.loads(row[0]) if row else {}


def last_session(db, principal) -> dict | None:
    """The principal's most recent session (the one last_context reads): {"id", "status", "ended_at", "context"}."""
    row = db.execute("SELECT id, status, ended_at, context FROM voice_sessions WHERE principal=? ORDER BY updated_at "
                     "DESC, rowid DESC LIMIT 1", (principal,)).fetchone()
    return None if row is None else {"id": row[0], "status": row[1], "ended_at": row[2],
                                     "context": json.loads(row[3] or "{}")}


def clear_follow(db, principal, task_id) -> int:
    """Take the awaited answer of `task_id` off every session of the principal, in one write transaction; returns
    how many sessions awaited it. The follower that gets a count above 0 is the one that delivers it: two processes
    following the same task cannot both (review1 A9, A10), and an answer delivered or given up on is never picked
    up again by a later session (S9)."""
    with store.write(db):
        rows = db.execute("SELECT id, context FROM voice_sessions WHERE principal=? AND json_valid(context) AND "
                          "json_extract(context,'$.follow_task_id')=?", (principal, task_id)).fetchall()
        for sid, text in rows:
            ctx = json.loads(text)
            ctx.pop("follow_task_id", None)
            ctx.pop("follow_by", None)
            # updated_at stays: it orders the sessions last_context reads
            db.execute("UPDATE voice_sessions SET context=? WHERE id=?", (json.dumps(ctx, sort_keys=True), sid))
    return len(rows)


def active_elsewhere(db, principal, ids) -> bool:
    """Whether the principal has an active session other than `ids` (one another process holds)."""
    ids = list(ids)
    return db.execute("SELECT 1 FROM voice_sessions WHERE principal=? AND status='active'"
                      + (f" AND id NOT IN ({','.join('?' * len(ids))})" if ids else "") + " LIMIT 1",
                      (principal, *ids)).fetchone() is not None


def sessions(db, principal=None, limit=20) -> list[dict]:
    where, params = ("WHERE principal=?", (principal,)) if principal else ("", ())
    out = _rows(db, f"SELECT s.*, (SELECT count(*) FROM voice_segments g WHERE g.session_id=s.id) AS segments "
                    f"FROM voice_sessions s {where} ORDER BY started_at DESC LIMIT ?", (*params, int(limit)))
    for s in out:
        s["context"] = json.loads(s["context"] or "{}")
    return out


# ---- segments ---------------------------------------------------------------------------------------------------

AUTHORITY = {"asr": "approximate", "typed": "user_typed", "model": "authoritative", "template": "authoritative"}


def upsert(db, sid, utterance, *, speaker, source, status, text, now, started_at=None, ended_at=None, task_id=None,
           detail=None) -> dict:
    """Insert or update one segment (utterance id unique per session). seq is assigned on first insert, so the
    order is the order utterances began."""
    with store.write(db):
        seg_id = _upsert(db, sid, utterance, speaker=speaker, source=source, status=status, text=text, now=now,
                         started_at=started_at, ended_at=ended_at, task_id=task_id, detail=detail)
    return segment(db, seg_id)


def finalize(db, sid, utterance, *, text, now, started_at, detail, pcm=None, rate=16000, ttl_hours=None) -> dict:
    """The final segment of a spoken utterance and, when retention is on (ttl_hours), its raw audio: one write
    transaction (the voice hub batches them off the audio request path)."""
    with store.write(db):
        seg_id = _upsert(db, sid, utterance, speaker="user", source="asr", status="final", text=text, now=now,
                         started_at=started_at, ended_at=now, detail=detail)
        if pcm is not None and ttl_hours:
            db.execute("INSERT OR REPLACE INTO voice_audio(segment_id,pcm,rate,created_at,expires_at) "
                       "VALUES(?,?,?,?,?)", (int(seg_id), pcm, int(rate), now, now + 3600.0 * ttl_hours))
    return segment(db, seg_id)


def _upsert(db, sid, utterance, *, speaker, source, status, text, now, started_at=None, ended_at=None, task_id=None,
            detail=None) -> int:
    if speaker not in ("user", "neuroscifi") or source not in AUTHORITY:
        raise ValidationError("bad speaker or source")
    text = str(text or "")[:MAX_TEXT]
    row = db.execute("SELECT id, detail FROM voice_segments WHERE session_id=? AND utterance=?",
                     (sid, utterance)).fetchone()
    if row is None:
        seq = db.execute("SELECT coalesce(max(seq),0)+1 FROM voice_segments WHERE session_id=?",
                         (sid,)).fetchone()[0]
        cur = db.execute(
            "INSERT INTO voice_segments(session_id,utterance,seq,speaker,source,status,authority,text,detail,"
            "task_id,started_at,ended_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, utterance, seq, speaker, source, status, AUTHORITY[source], text,
             json.dumps(detail or {}, sort_keys=True), task_id, started_at or now, ended_at, now, now))
        seg_id = cur.lastrowid
    else:
        seg_id = row[0]
        merged = {**json.loads(row[1] or "{}"), **(detail or {})}
        db.execute("UPDATE voice_segments SET source=?, status=?, authority=?, text=?, detail=?, "
                   "task_id=coalesce(?,task_id), ended_at=coalesce(?,ended_at), updated_at=? WHERE id=?",
                   (source, status, AUTHORITY[source], text, json.dumps(merged, sort_keys=True), task_id,
                    ended_at, now, seg_id))
    return seg_id


def segment(db, seg_id) -> dict:
    rows = _rows(db, "SELECT * FROM voice_segments WHERE id=?", (int(seg_id),))
    if not rows:
        raise NotFound("transcript segment not found")
    return _public(rows[0])


def _public(r) -> dict:
    r["detail"] = json.loads(r["detail"] or "{}")
    r["display_text"] = r["corrected_text"] or r["text"]
    r["label"] = {"approximate": "Approximate (speech recognition)", "user_typed": "Typed",
                  "authoritative": "NeuroSciFi"}[r["authority"]]
    return r


def mark_interrupted(db, sid, utterance, spoken_until, now):
    with store.write(db):
        row = db.execute("SELECT id, detail FROM voice_segments WHERE session_id=? AND utterance=?",
                         (sid, utterance)).fetchone()
        if row is None:
            return None
        detail = {**json.loads(row[1] or "{}"), "spoken_until": int(spoken_until), "interrupted_at": now}
        db.execute("UPDATE voice_segments SET status='interrupted', detail=?, updated_at=? WHERE id=?",
                   (json.dumps(detail, sort_keys=True), now, row[0]))
    return segment(db, row[0])


def correct(db, seg_id, text, now, *, principal=None) -> dict:
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise ValidationError("correction must be 1..2000 characters")
    with store.write(db):
        row = db.execute("SELECT g.authority, s.principal FROM voice_segments g JOIN voice_sessions s "
                         "ON s.id=g.session_id WHERE g.id=?", (int(seg_id),)).fetchone()
        if row is None or (principal is not None and row[1] != principal):
            raise NotFound("transcript segment not found")
        if row[0] != "approximate":
            raise Conflict("only speech-recognition text can be corrected; NeuroSciFi and typed text are kept as is")
        db.execute("UPDATE voice_segments SET corrected_text=?, corrected_at=?, updated_at=? WHERE id=?",
                   (" ".join(text.split()), now, now, int(seg_id)))
    return segment(db, seg_id)


def listing(db, *, session_id=None, principal=None, limit=200) -> list[dict]:
    where, params = [], []
    if session_id:
        where.append("g.session_id=?")
        params.append(session_id)
    if principal:
        where.append("s.principal=?")
        params.append(principal)
    sql = ("SELECT g.* FROM voice_segments g JOIN voice_sessions s ON s.id=g.session_id" +
           (" WHERE " + " AND ".join(where) if where else "") +
           " ORDER BY s.started_at DESC, g.session_id, g.seq LIMIT ?")
    return [_public(r) for r in _rows(db, sql, (*params, max(1, min(1000, int(limit)))))]


def search(db, query, *, principal=None, limit=50) -> list[dict]:
    """Case-insensitive substring search over text and corrected text, newest first."""
    if not isinstance(query, str) or not query.strip() or len(query) > 200:
        raise ValidationError("query must be 1..200 characters")
    like = "%" + query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    sql = ("SELECT g.* FROM voice_segments g JOIN voice_sessions s ON s.id=g.session_id WHERE "
           "(g.text LIKE ? ESCAPE '\\' OR coalesce(g.corrected_text,'') LIKE ? ESCAPE '\\')" +
           (" AND s.principal=?" if principal else "") + " ORDER BY g.created_at DESC LIMIT ?")
    params = (like, like, *((principal,) if principal else ()), max(1, min(200, int(limit))))
    return [_public(r) for r in _rows(db, sql, params)]


def forget_session(db, sid, *, principal=None, now=None, rewrite=True) -> dict:
    """Delete a voice session, its transcript and any retained audio, record its payload-free tombstone (a restored
    checkpoint drops it again; governance.forget_voice_session), then truncate the WAL. With `rewrite` the checkpoint
    archives the forget made stale are rewritten in place without the session (checkpoints.after_forget; the result
    names them). `rewrite=False` leaves that to the caller (the voice hub runs it in the background)."""
    now = time.time() if now is None else now
    with store.write(db):
        row = db.execute("SELECT principal FROM voice_sessions WHERE id=?", (sid,)).fetchone()
        if row is None or (principal is not None and row[0] != principal):
            raise NotFound("voice session not found")
        effects = governance.forget_voice_session(db, sid, now)
    store.truncate_wal(db)
    out = {"forgotten": sid, "segments": effects["segments"],
           "research_tasks_forgotten": effects["research_tasks_forgotten"]}
    stale = effects["checkpoints_stale"]
    if stale and rewrite:
        from ..instance import checkpoints
        out |= checkpoints.after_forget(db, now)
        stale = out["checkpoints_stale"]
    elif stale:
        out["checkpoints_stale"] = stale
    if stale:
        out["next_steps"] = [governance.REBUILD_HINT.format(n=len(stale))]
    return out


# ---- retained audio (off by default) ---------------------------------------------------------------------------

def retain_audio(db, seg_id, pcm: bytes, rate, now, ttl_hours) -> None:
    with store.write(db):
        db.execute("INSERT OR REPLACE INTO voice_audio(segment_id,pcm,rate,created_at,expires_at) VALUES(?,?,?,?,?)",
                   (int(seg_id), pcm, int(rate), now, now + 3600.0 * ttl_hours))


def sweep(db, now) -> int:
    """Delete retained audio past its TTL (and every row when retention was switched off). Returns rows deleted."""
    from . import config as vconfig
    cfg = vconfig.get(db)
    with store.write(db):
        if cfg["retain_audio"]:
            n = db.execute("DELETE FROM voice_audio WHERE expires_at<=?", (now,)).rowcount
        else:
            n = db.execute("DELETE FROM voice_audio").rowcount
        db.execute("DELETE FROM voice_capture WHERE expires_at<=?", (now,))
    if n:
        store.truncate_wal(db)
    return n


def audio_count(db) -> int:
    return db.execute("SELECT count(*) FROM voice_audio").fetchone()[0]


# ---- capture ownership --------------------------------------------------------------------------------------------

def holder(db, now) -> dict | None:
    rows = _rows(db, "SELECT * FROM voice_capture WHERE scope=? AND expires_at>?", (SCOPE, now))
    return rows[0] if rows else None


def acquire(db, device, label, principal, session_id, now, *, handoff=False) -> dict:
    """Take the microphone lease. Refused (granted False, holder named) while another device holds a live lease,
    unless `handoff` (the caller obtained explicit confirmation). The same device renews."""
    with store.write(db):
        row = db.execute("SELECT device, label, expires_at FROM voice_capture WHERE scope=?", (SCOPE,)).fetchone()
        if row and row[0] != device and row[2] > now and not handoff:
            return {"granted": False, "holder": {"device": row[0], "label": row[1],
                                                  "expires_in": round(row[2] - now, 1)}}
        db.execute("INSERT INTO voice_capture(scope,device,label,principal,session_id,acquired_at,expires_at) "
                   "VALUES(?,?,?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET device=excluded.device, "
                   "label=excluded.label, principal=excluded.principal, session_id=excluded.session_id, "
                   "acquired_at=CASE WHEN voice_capture.device=excluded.device THEN voice_capture.acquired_at "
                   "ELSE excluded.acquired_at END, expires_at=excluded.expires_at",
                   (SCOPE, device, label, principal, session_id, now, now + LEASE_SECONDS))
        previous = {"device": row[0], "label": row[1]} if row and row[0] != device and row[2] > now else None
    return {"granted": True, "expires_in": LEASE_SECONDS, "handed_off_from": previous}


def renew(db, device, now) -> bool:
    with store.write(db):
        n = db.execute("UPDATE voice_capture SET expires_at=? WHERE scope=? AND device=? AND expires_at>?",
                       (now + LEASE_SECONDS, SCOPE, device, now)).rowcount
    return bool(n)


def release(db, device) -> bool:
    with store.write(db):
        return bool(db.execute("DELETE FROM voice_capture WHERE scope=? AND device=?", (SCOPE, device)).rowcount)


def holds(db, device, now) -> bool:
    return db.execute("SELECT 1 FROM voice_capture WHERE scope=? AND device=? AND expires_at>?",
                      (SCOPE, device, now)).fetchone() is not None


# ---- hand-off requests across principals ----------------------------------------------------------------------------
# A device that may not take the microphone on its own confirmation (a Pocket device while another principal holds the
# lease) files one request; only the HOLDER's session can grant or refuse it. Kept in `meta` (key HANDOFF_KEY) because
# the console and Pocket processes share only the database. One request at a time; a newer one replaces it.

HANDOFF_KEY = "voice_handoff"
HANDOFF_SECONDS = 60.0


def request_handoff(db, *, device, label, principal, session_id, holder_device, now) -> dict:
    req = {"id": secrets.token_hex(8), "to_device": device, "to_label": label, "to_principal": principal,
           "to_session": session_id, "from_device": holder_device, "expires_at": now + HANDOFF_SECONDS}
    with store.write(db):
        db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (HANDOFF_KEY, json.dumps(req, sort_keys=True)))
    return req


def handoff_request(db, now, *, holder_device=None) -> dict | None:
    """The live request (for `holder_device` only, when given), else None."""
    row = db.execute("SELECT value FROM meta WHERE key=?", (HANDOFF_KEY,)).fetchone()
    req = json.loads(row[0]) if row else None
    if not req or req.get("expires_at", 0) <= now:
        return None
    if holder_device is not None and req.get("from_device") != holder_device:
        return None
    return req


def answer_handoff(db, req_id, holder_device, now, accept: bool) -> dict | None:
    """The holder grants (the lease moves to the requester) or refuses a request. None when there is no such live
    request for this holder, or it no longer holds the microphone."""
    with store.write(db):
        row = db.execute("SELECT value FROM meta WHERE key=?", (HANDOFF_KEY,)).fetchone()
        req = json.loads(row[0]) if row else None
        if not req or req.get("id") != req_id or req.get("from_device") != holder_device or req["expires_at"] <= now:
            return None
        db.execute("DELETE FROM meta WHERE key=?", (HANDOFF_KEY,))
        if not db.execute("SELECT 1 FROM voice_capture WHERE scope=? AND device=? AND expires_at>?",
                          (SCOPE, holder_device, now)).fetchone():
            return None
        if accept:
            db.execute("UPDATE voice_capture SET device=?, label=?, principal=?, session_id=?, acquired_at=?, "
                       "expires_at=? WHERE scope=?", (req["to_device"], req["to_label"], req["to_principal"],
                                                     req["to_session"], now, now + LEASE_SECONDS, SCOPE))
    return req
