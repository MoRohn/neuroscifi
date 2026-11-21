"""Research nodes and research-session ownership (RD-11 minimal multi-node scope; ADR-039 draft; ADR-031 kept).

A *node* is a Pocket-paired device the owner enrolled explicitly with the 'node' capability (`nsf web node enrol
LABEL --pocket-db PATH`); it is identified by instance-salted digests of its device and family fingerprints, never
by its credential. Enrolment does not make it an executor: the Spark that runs `nsf web work` stays the one
executing node (ADR-031). A node can submit research, continue it, take or hand off ownership of a research
session, and read results (the family's task list is the sync).

A research *session* is the chain of tasks that continue one another (the root task id names it). It has at most
one owner at a time - the primary key of autonomy_sessions - with a lease of OWN_SECONDS renewed by the owner's
actions. Another node gets ownership only
  - when nobody owns the session or the owner's lease expired (take), or
  - by an explicit hand-off: the owner offers the session to a named node of its family (handoff) and that node
    accepts within OFFER_SECONDS (accept); the transfer is one conditional UPDATE, so two devices never both own it.
While a session is owned, cancelling or continuing its tasks from any other device of the family is refused
(Conflict); reading stays open to the whole family (ADR-028).
"""
from __future__ import annotations

from ..instance import store
from ..instance.errors import Conflict, NotFound, ValidationError
from . import state

OWN_SECONDS = 900.0
OFFER_SECONDS = 300.0
CAPABILITY = "node"
MAX_CHAIN = 50


def device_digest(db, fingerprint: str) -> str:
    """Instance-salted pseudonym of a Pocket device fingerprint (like state.family_digest)."""
    from ..instance.governance import root_digest
    if not isinstance(fingerprint, str) or not fingerprint:
        raise ValidationError("device fingerprint must be a non-empty string")
    return root_digest(store.birth(db)["instance_id"], "device:" + fingerprint)


def enrol(db, device_fp, family_fp, label, now, *, by="owner") -> dict:
    """The owner grants a paired device the node capability (re-enrolling a revoked one restores it)."""
    if not isinstance(label, str) or not label.strip() or len(label) > 64:
        raise ValidationError("label must be a non-blank string of at most 64 characters")
    device, family = device_digest(db, device_fp), state.family_digest(db, family_fp)
    with store.write(db):
        db.execute("INSERT INTO autonomy_nodes(device,family,label,capabilities,enrolled_by,enrolled_at) "
                   "VALUES(?,?,?,?,?,?) ON CONFLICT(device) DO UPDATE SET family=excluded.family, "
                   "label=excluded.label, capabilities=excluded.capabilities, enrolled_by=excluded.enrolled_by, "
                   "enrolled_at=excluded.enrolled_at, revoked_at=NULL",
                   (device, family, label.strip(), state.dumps([CAPABILITY]), str(by)[:64], now))
        state.log_event(db, None, "decision", {"node": "enrolled", "label": label.strip(), "by": str(by)[:64]}, now)
        store.bump_state_version(db)
    return node(db, device)


def revoke(db, label, now) -> int:
    """Revoke every live node with this label; their sessions are released. Returns how many."""
    with store.write(db):
        devices = [d for (d,) in db.execute("SELECT device FROM autonomy_nodes WHERE label=? AND revoked_at IS NULL",
                                            (label,))]
        for device in devices:
            db.execute("UPDATE autonomy_nodes SET revoked_at=? WHERE device=?", (now, device))
            db.execute("DELETE FROM autonomy_sessions WHERE owner=?", (device,))
            db.execute("UPDATE autonomy_sessions SET offered_to=NULL, offer_expires_at=NULL WHERE offered_to=?",
                       (device,))
        if devices:
            state.log_event(db, None, "decision", {"node": "revoked", "label": label, "count": len(devices)}, now)
            store.bump_state_version(db)
    return len(devices)


def node(db, device) -> dict | None:
    """The live node with this device digest, else None."""
    row = db.execute("SELECT device, family, label, capabilities, enrolled_by, enrolled_at FROM autonomy_nodes "
                     "WHERE device=? AND revoked_at IS NULL", (device,)).fetchone()
    if row is None:
        return None
    out = dict(zip(("device", "family", "label", "capabilities", "enrolled_by", "enrolled_at"), row))
    out["capabilities"] = state.loads(out["capabilities"], [])
    return out if CAPABILITY in out["capabilities"] else None


def listing(db) -> list[dict]:
    return [{"label": label, "family": family[:8], "enrolled_at": at, "revoked_at": revoked}
            for label, family, at, revoked in db.execute(
                "SELECT label, family, enrolled_at, revoked_at FROM autonomy_nodes ORDER BY enrolled_at")]


def session_of(db, task_id) -> str:
    """The root task id of the chain of continuations `task_id` belongs to."""
    current = str(task_id)
    for _ in range(MAX_CHAIN):
        row = db.execute("SELECT parent_id FROM web_tasks WHERE id=?", (current,)).fetchone()
        if row is None:
            raise NotFound(f"No research task {task_id}")
        if not row[0] or db.execute("SELECT 1 FROM web_tasks WHERE id=?", (row[0],)).fetchone() is None:
            return current
        current = row[0]
    return current


def _row(db, session, now) -> dict | None:
    row = db.execute("SELECT session, family, owner, acquired_at, expires_at, offered_to, offer_expires_at FROM "
                     "autonomy_sessions WHERE session=?", (session,)).fetchone()
    if row is None:
        return None
    out = dict(zip(("session", "family", "owner", "acquired_at", "expires_at", "offered_to", "offer_expires_at"), row))
    if out["offer_expires_at"] is not None and out["offer_expires_at"] <= now:
        out["offered_to"] = out["offer_expires_at"] = None
    out["live"] = out["expires_at"] > now
    return out


def _label(db, device):
    row = db.execute("SELECT label FROM autonomy_nodes WHERE device=?", (device,)).fetchone()
    return row[0] if row else None


def view(db, session, device, now) -> dict:
    """Ownership as one device sees it (labels of its own family's nodes only)."""
    row = _row(db, session, now)
    if row is None or not row["live"]:
        return {"session": session, "owner": None, "you": False, "offered_to": None, "offered_to_you": False}
    return {"session": session, "owner": _label(db, row["owner"]), "you": row["owner"] == device,
            "expires_in": round(row["expires_at"] - now, 1), "offered_to": _label(db, row["offered_to"])
            if row["offered_to"] else None, "offered_to_you": row["offered_to"] == device}


def may_drive(db, task_id, device, now) -> bool:
    """Whether `device` may cancel or continue the task: its session is unowned, expired, or owned by it."""
    row = _row(db, session_of(db, task_id), now)
    return row is None or not row["live"] or row["owner"] == device


def _require_node(db, device, family):
    found = node(db, device)
    if found is None or found["family"] != family:
        raise PermissionError("not an enrolled research node of this family")
    return found


def take(db, task_id, device, family, now) -> dict:
    """Own the task's session when nobody owns it (or the owner's lease expired); the owner renews. Conflict while
    another node owns it: ask that node to hand it off."""
    _require_node(db, device, family)
    session = session_of(db, task_id)
    with store.write(db):
        row = _row(db, session, now)
        if row is not None and row["live"] and row["owner"] != device:
            raise Conflict(f"session owned by {_label(db, row['owner']) or 'another device'}; ask it to hand off")
        db.execute("INSERT INTO autonomy_sessions(session,family,owner,acquired_at,expires_at) VALUES(?,?,?,?,?) "
                   "ON CONFLICT(session) DO UPDATE SET owner=excluded.owner, family=excluded.family, "
                   "acquired_at=CASE WHEN autonomy_sessions.owner=excluded.owner THEN autonomy_sessions.acquired_at "
                   "ELSE excluded.acquired_at END, expires_at=excluded.expires_at, "
                   "offered_to=CASE WHEN autonomy_sessions.owner=excluded.owner THEN autonomy_sessions.offered_to "
                   "ELSE NULL END, offer_expires_at=CASE WHEN autonomy_sessions.owner=excluded.owner THEN "
                   "autonomy_sessions.offer_expires_at ELSE NULL END",
                   (session, family, device, now, now + OWN_SECONDS))
        state.log_event(db, session, "decision", {"session": "taken", "by": _label(db, device)}, now)
    return view(db, session, device, now)


def handoff(db, task_id, device, family, to_label, now) -> dict:
    """The owner offers the session to another live node of its family (by label); it moves only on accept."""
    _require_node(db, device, family)
    session = session_of(db, task_id)
    with store.write(db):
        row = _row(db, session, now)
        if row is None or not row["live"] or row["owner"] != device:
            raise Conflict("only the session's owner can hand it off; take it first")
        target = db.execute("SELECT device FROM autonomy_nodes WHERE label=? AND family=? AND revoked_at IS NULL AND "
                            "device!=?", (to_label, family, device)).fetchall()
        if len(target) != 1:
            raise NotFound("no single other node of this family has that label")
        db.execute("UPDATE autonomy_sessions SET offered_to=?, offer_expires_at=?, expires_at=? WHERE session=?",
                   (target[0][0], now + OFFER_SECONDS, max(row["expires_at"], now + OFFER_SECONDS), session))
        state.log_event(db, session, "decision", {"session": "offered", "by": _label(db, device), "to": to_label}, now)
    return view(db, session, device, now)


def accept(db, task_id, device, family, now) -> dict:
    """The offered node takes the session over: one conditional UPDATE (owner and offer unchanged since the offer)."""
    _require_node(db, device, family)
    session = session_of(db, task_id)
    with store.write(db):
        row = _row(db, session, now)
        if row is None or row["offered_to"] != device:
            raise Conflict("this session was not offered to this node (or the offer expired)")
        moved = db.execute("UPDATE autonomy_sessions SET owner=?, acquired_at=?, expires_at=?, offered_to=NULL, "
                           "offer_expires_at=NULL WHERE session=? AND owner=? AND offered_to=? AND offer_expires_at>?",
                           (device, now, now + OWN_SECONDS, session, row["owner"], device, now)).rowcount
        if moved != 1:
            raise Conflict("the hand-off changed meanwhile; ask again")
        state.log_event(db, session, "decision", {"session": "handed off", "from": _label(db, row["owner"]),
                                                  "to": _label(db, device)}, now)
    return view(db, session, device, now)


def release(db, task_id, device, family, now) -> dict:
    _require_node(db, device, family)
    session = session_of(db, task_id)
    with store.write(db):
        n = db.execute("DELETE FROM autonomy_sessions WHERE session=? AND owner=?", (session, device)).rowcount
        if not n:
            raise Conflict("only the session's owner can release it")
        state.log_event(db, session, "decision", {"session": "released", "by": _label(db, device)}, now)
    return view(db, session, device, now)


def renew(db, task_id, device, now):
    """The owner's action renews its lease (no-op for anyone else)."""
    session = session_of(db, task_id)
    with store.write(db):
        db.execute("UPDATE autonomy_sessions SET expires_at=max(expires_at, ?) WHERE session=? AND owner=? AND "
                   "expires_at>?", (now + OWN_SECONDS, session, device, now))


# ---- CLI (`nsf web node ...`) ------------------------------------------------------------------------------------

def _pocket_device(pocket_db, label):
    """(device fingerprint, family fingerprint) of the Pocket pairing with this label (read-only)."""
    import sqlite3
    from pathlib import Path
    if not Path(pocket_db).is_file():
        raise ValidationError(f"no Pocket database at {pocket_db}")
    con = sqlite3.connect(f"file:{pocket_db}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT m.device, m.family FROM devices d JOIN members m ON m.device=d.key "
                           "WHERE d.label=?", (label,)).fetchall()
    finally:
        con.close()
    if len(rows) != 1:
        raise ValidationError(f"{len(rows)} Pocket pairings have the label {label!r}; exactly one is needed")
    return rows[0]


def cli(db, a, now):
    if a.node_action == "list":
        return {"nodes": listing(db)}
    if not a.label:
        raise ValidationError(f"{a.node_action} needs the device LABEL")
    if a.node_action == "revoke":
        return {"revoked": revoke(db, a.label, now), "nodes": listing(db)}
    if not a.pocket_db:
        raise ValidationError("enrol needs --pocket-db (the Pocket bridge's private database)")
    device_fp, family_fp = _pocket_device(a.pocket_db, a.label)
    enrol(db, device_fp, family_fp, a.label, now, by="owner:cli")
    return {"enrolled": a.label, "nodes": listing(db)}
