"""Research nodes and research-session ownership (RD-11 minimal multi-node scope; ADR-039 draft; ADR-031 kept), with
signed node identities and attested enrolment (ADR-040 draft; signing.py).

A *node* is a Pocket-paired device the owner enrolled with the 'node' capability. Enrolment is attested:
  1. the paired device asks for a challenge (Pocket `node_enrol` challenge); the Spark issues one bound to the
     device's pairing family, its node id and an expiry (CHALLENGE_SECONDS);
  2. the device signs it with its own non-extractable key (Ed25519, or ECDSA P-256 where the browser lacks Ed25519)
     and sends the public key (attest); the Spark verifies the signature and keeps the key as pending;
  3. the owner compares the fingerprint the console shows with the one the device shows and approves it
     (console, or `nsf web node enrol NAME --fingerprint FP`), naming the node. Only then is it a node.
A node is identified by instance-salted digests of its device and family fingerprints, never by its credential.
Every state-changing request of a signed node carries a signature over the canonical request (verify_request): a
fresh timestamp (FRESH_SECONDS), a nonce never seen before, the node's recorded algorithm and key. Revoking a node
deletes its key; rotating a key is re-enrolment. Nodes enrolled before signing existed (signed=0) must re-enrol.

Enrolment does not make a node an executor: the Spark that runs `nsf web work` stays the one executing node
(ADR-031). A node can submit research, continue it, take or hand off ownership of a research session, and read
results (the family's task list is the sync).

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

import secrets

from ..instance import store
from ..instance.errors import Conflict, NotFound, ValidationError
from . import signing, state
from .signing import SignatureError

OWN_SECONDS = 900.0
OFFER_SECONDS = 300.0
CHALLENGE_SECONDS = 300.0     # a challenge must be signed within 5 minutes
PENDING_SECONDS = 3600.0      # an attested key waits at most an hour for the owner's approval
CAPABILITY = "node"
MAX_CHAIN = 50


def device_digest(db, fingerprint: str) -> str:
    """Instance-salted pseudonym of a Pocket device fingerprint (like state.family_digest)."""
    from ..instance.governance import root_digest
    if not isinstance(fingerprint, str) or not fingerprint:
        raise ValidationError("device fingerprint must be a non-empty string")
    return root_digest(store.birth(db)["instance_id"], "device:" + fingerprint)


def node_id(device) -> str:
    """The public name of a node in its challenge and status: a prefix of the salted device digest."""
    return "n-" + device[:16]


def family_tag(family) -> str:
    """The family a challenge is bound to: a prefix of the salted family digest (never the fingerprint)."""
    return "f-" + family[:16]


def _label_ok(label):
    if not isinstance(label, str) or not label.strip() or len(label) > 64:
        raise ValidationError("label must be a non-blank string of at most 64 characters")
    return label.strip()


def enrol(db, device_fp, family_fp, label, now, *, by="owner", key=None) -> dict:
    """Record the node capability for a paired device. `key` is (alg, raw public key) once an attestation was
    approved (approve); without it the row is an unsigned node of the kind enrolled before signing existed, which
    node() refuses until it re-enrols (kept for those rows and their tests)."""
    return _record(db, device_digest(db, device_fp), state.family_digest(db, family_fp), label, now, by=by, key=key)


def _record(db, device, family, label, now, *, by, key):
    label = _label_ok(label)
    alg, raw = key if key else (None, None)
    fp = signing.fingerprint(alg, raw) if key else None
    with store.write(db):
        db.execute("INSERT INTO autonomy_nodes(device,family,label,capabilities,enrolled_by,enrolled_at,alg,public_key,"
                   "key_fingerprint,signed) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(device) DO UPDATE SET "
                   "family=excluded.family, label=excluded.label, capabilities=excluded.capabilities, "
                   "enrolled_by=excluded.enrolled_by, enrolled_at=excluded.enrolled_at, revoked_at=NULL, "
                   "alg=excluded.alg, public_key=excluded.public_key, key_fingerprint=excluded.key_fingerprint, "
                   "signed=excluded.signed",
                   (device, family, label, state.dumps([CAPABILITY]), str(by)[:64], now, alg,
                    signing.b64(raw) if key else None, fp, 1 if key else 0))
        db.execute("DELETE FROM node_enrolments WHERE device=?", (device,))
        state.log_event(db, None, "decision", {"node": "enrolled", "label": label, "by": str(by)[:64],
                                               **({"alg": alg, "fingerprint": fp} if key else {})}, now)
        store.bump_state_version(db)
    return node(db, device) or {"device": device, "label": label, "signed": False}


def challenge(db, device, family, pocket_label, now) -> dict:
    """A fresh enrolment challenge for this paired device (replaces any pending one of it)."""
    nonce = signing.b64(secrets.token_bytes(32))
    expires = int(now + CHALLENGE_SECONDS)
    with store.write(db):
        db.execute("INSERT INTO node_enrolments(device,family,pocket_label,challenge,issued_at,expires_at) "
                   "VALUES(?,?,?,?,?,?) ON CONFLICT(device) DO UPDATE SET family=excluded.family, "
                   "pocket_label=excluded.pocket_label, challenge=excluded.challenge, issued_at=excluded.issued_at, "
                   "expires_at=excluded.expires_at, alg=NULL, public_key=NULL, key_fingerprint=NULL, "
                   "attested_at=NULL", (device, family, str(pocket_label)[:64], nonce, now, expires))
    return {"challenge": nonce, "family": family_tag(family), "node": node_id(device), "expires": expires}


def attest(db, device, family, data, now) -> dict:
    """The device proves possession of its key by signing the challenge the Spark issued to it. The key is then
    pending until the owner approves its fingerprint."""
    row = db.execute("SELECT family, challenge, expires_at, attested_at FROM node_enrolments WHERE device=?",
                     (device,)).fetchone()
    if row is None or row[3] is not None or row[1] != data.get("challenge"):
        raise SignatureError("no_challenge", "no open challenge for this device; ask for a new one")
    if row[0] != family:
        raise SignatureError("wrong_family", "the challenge was issued to another pairing family")
    if row[2] <= now:
        raise SignatureError("challenge_expired", "the challenge expired; ask for a new one")
    alg = data.get("alg")
    try:
        raw, sig = signing.unb64(data.get("public_key")), signing.unb64(data.get("signature"))
        signing.public_key(alg, raw)
    except ValueError:
        raise SignatureError("bad_key", "unsupported algorithm or malformed key") from None
    message = signing.canonical_challenge(row[1], family_tag(row[0]), node_id(device), int(row[2]))
    if not signing.verify(alg, raw, sig, message):
        raise SignatureError("signature_invalid", "the challenge signature does not verify")
    fp = signing.fingerprint(alg, raw)
    with store.write(db):
        done = db.execute("UPDATE node_enrolments SET alg=?, public_key=?, key_fingerprint=?, attested_at=?, "
                          "expires_at=? WHERE device=? AND challenge=? AND attested_at IS NULL",
                          (alg, signing.b64(raw), fp, now, now + PENDING_SECONDS, device, row[1])).rowcount
        if done != 1:
            raise SignatureError("no_challenge", "the challenge was used meanwhile")
        state.log_event(db, None, "decision", {"node": "attested", "alg": alg, "fingerprint": fp}, now)
        store.bump_state_version(db)
    return status(db, device, family, now)


def pending(db, now) -> list[dict]:
    """Attested keys waiting for the owner, with what the console shows to compare."""
    return [{"pocket_label": label, "family": family[:8], "alg": alg, "fingerprint": fp, "attested_at": at,
             "expires_at": exp}
            for label, family, alg, fp, at, exp in db.execute(
                "SELECT pocket_label, family, alg, key_fingerprint, attested_at, expires_at FROM node_enrolments "
                "WHERE attested_at IS NOT NULL AND expires_at>? ORDER BY attested_at", (now,))]


def approve(db, fingerprint, label, now, *, by="owner") -> dict:
    """The owner enrols the device whose attested key has this fingerprint, under `label` (its node name)."""
    label = _label_ok(label)
    row = db.execute("SELECT device, family, alg, public_key FROM node_enrolments WHERE key_fingerprint=? AND "
                     "attested_at IS NOT NULL AND expires_at>?", (str(fingerprint).strip().upper(), now)).fetchall()
    if len(row) != 1:
        raise NotFound("no pending enrolment has that fingerprint (it may have expired: ask the device again)")
    device, family, alg, public = row[0]
    return _record(db, device, family, label, now, by=by, key=(alg, signing.unb64(public)))


def reject(db, fingerprint, now) -> int:
    with store.write(db):
        n = db.execute("DELETE FROM node_enrolments WHERE key_fingerprint=?",
                       (str(fingerprint).strip().upper(),)).rowcount
        if n:
            state.log_event(db, None, "decision", {"node": "enrolment rejected"}, now)
            store.bump_state_version(db)
    return n


def revoke(db, label, now) -> int:
    """Revoke every live node with this label: its key is deleted (later signatures fail) and its sessions are
    released. Returns how many."""
    with store.write(db):
        devices = [d for (d,) in db.execute("SELECT device FROM autonomy_nodes WHERE label=? AND revoked_at IS NULL",
                                            (label,))]
        for device in devices:
            db.execute("UPDATE autonomy_nodes SET revoked_at=?, alg=NULL, public_key=NULL, key_fingerprint=NULL "
                       "WHERE device=?", (now, device))
            db.execute("DELETE FROM node_enrolments WHERE device=?", (device,))
            db.execute("DELETE FROM node_nonces WHERE device=?", (device,))
            db.execute("DELETE FROM autonomy_sessions WHERE owner=?", (device,))
            db.execute("UPDATE autonomy_sessions SET offered_to=NULL, offer_expires_at=NULL WHERE offered_to=?",
                       (device,))
        if devices:
            state.log_event(db, None, "decision", {"node": "revoked", "label": label, "count": len(devices)}, now)
            store.bump_state_version(db)
    return len(devices)


_NODE = ("device", "family", "label", "capabilities", "enrolled_by", "enrolled_at", "alg", "public_key",
         "key_fingerprint", "signed", "revoked_at")


def _node_row(db, device) -> dict | None:
    row = db.execute("SELECT " + ", ".join(_NODE) + " FROM autonomy_nodes WHERE device=?", (device,)).fetchone()
    if row is None:
        return None
    out = dict(zip(_NODE, row))
    out["capabilities"] = state.loads(out["capabilities"], [])
    out["signed"] = bool(out["signed"])
    return out


def node(db, device) -> dict | None:
    """The live, signed node with this device digest, else None (an unsigned node must re-enrol)."""
    out = _node_row(db, device)
    if out is None or out["revoked_at"] is not None or CAPABILITY not in out["capabilities"]:
        return None
    return out if out["signed"] and out["public_key"] else None


def status(db, device, family, now) -> dict:
    """What a paired device learns about itself: none, challenged, attested (waiting for the owner), enrolled,
    reenrol (enrolled before signing) or revoked; with its node id and, once it proved a key, the fingerprint."""
    out = {"node": node_id(device), "state": "none"}
    row = _node_row(db, device)
    if row is not None and row["family"] == family:
        if row["revoked_at"] is not None:
            out["state"] = "revoked"
        elif not row["signed"] or not row["public_key"]:
            out["state"] = "reenrol"
        else:
            out.update(state="enrolled", label=row["label"], alg=row["alg"], fingerprint=row["key_fingerprint"])
    pend = db.execute("SELECT alg, key_fingerprint, attested_at, expires_at FROM node_enrolments WHERE device=? AND "
                      "family=? AND expires_at>?", (device, family, now)).fetchone()
    if pend is not None:
        out["pending"] = "attested" if pend[2] is not None else "challenged"
        if pend[2] is not None:
            out["pending_alg"], out["pending_fingerprint"] = pend[0], pend[1]
    return out


def requires_signature(db, device) -> bool:
    """A live node enrolled with a key: its state-changing requests are accepted only when signed."""
    return node(db, device) is not None


def verify_request(db, device, family, method, path, body: bytes, timestamp, nonce, signature, now) -> dict:
    """Check a node's signed request and record its nonce; returns the node. SignatureError(code) otherwise:
    unknown_node, node_revoked, reenrol, wrong_family, malformed, stale_request, replayed_request,
    signature_invalid."""
    row = _node_row(db, device)
    if row is None:
        raise SignatureError("unknown_node", "this device has no enrolled key")
    if row["revoked_at"] is not None:
        raise SignatureError("node_revoked", "this node was revoked and its key deleted")
    if not row["signed"] or not row["public_key"]:
        raise SignatureError("reenrol", "this node was enrolled before signing and must re-enrol")
    if row["family"] != family:
        raise SignatureError("wrong_family", "the node belongs to another pairing family")
    try:
        ts = int(timestamp)
        if str(ts) != timestamp or not isinstance(nonce, str) or not signing.NONCE.fullmatch(nonce):
            raise ValueError()
        sig = signing.unb64(signature)
    except (TypeError, ValueError):
        raise SignatureError("malformed", "timestamp, nonce or signature is malformed") from None
    if abs(now - ts) > signing.FRESH_SECONDS:
        raise SignatureError("stale_request", "the request's timestamp is more than 120 s from the Spark's clock")
    message = signing.canonical_request(method, path, body, ts, nonce)
    if not signing.verify(row["alg"], signing.unb64(row["public_key"]), sig, message):
        raise SignatureError("signature_invalid", "the signature does not verify with the node's key")
    with store.write(db):
        db.execute("DELETE FROM node_nonces WHERE seen_at<?", (now - 2 * signing.FRESH_SECONDS,))
        if db.execute("INSERT OR IGNORE INTO node_nonces(device,nonce,seen_at) VALUES(?,?,?)",
                      (device, nonce, now)).rowcount != 1:
            raise SignatureError("replayed_request", "this nonce was already used")
    return row


def listing(db, now=None) -> list[dict]:
    """Nodes with their state, algorithm and fingerprint (enrolled, reenrol for unsigned rows, revoked)."""
    out = []
    for label, family, at, revoked, alg, fp, signed in db.execute(
            "SELECT label, family, enrolled_at, revoked_at, alg, key_fingerprint, signed FROM autonomy_nodes "
            "ORDER BY enrolled_at"):
        kind = "revoked" if revoked is not None else "enrolled" if signed and fp else "reenrol"
        out.append({"label": label, "family": family[:8], "enrolled_at": at, "revoked_at": revoked, "state": kind,
                    "alg": alg, "fingerprint": fp})
    return out


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
        legacy = _node_row(db, device)
        if found is None and legacy is not None and legacy["family"] == family and legacy["revoked_at"] is None:
            raise PermissionError("reenrol")      # enrolled before signing: it must prove a key first
        raise PermissionError("not_a_node")
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
                            "signed=1 AND public_key IS NOT NULL AND device!=?", (to_label, family, device)).fetchall()
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

def cli(db, a, now):
    if a.node_action == "list":
        if a.label or a.fingerprint:
            raise ValidationError("list takes no LABEL or --fingerprint")
        return {"nodes": listing(db), "pending": pending(db, now)}
    if a.node_action in ("enrol", "reject") and not a.fingerprint:
        raise ValidationError(f"{a.node_action} needs --fingerprint: the one the device shows, as `nsf web node list` "
                              "lists it under pending")
    if a.node_action == "reject":
        if not reject(db, a.fingerprint, now):
            raise NotFound("no pending enrolment has that fingerprint (nsf web node list)")
        return {"rejected": a.fingerprint, "pending": pending(db, now)}
    if not a.label:
        raise ValidationError(f"{a.node_action} needs the node LABEL")
    if a.node_action == "revoke":
        revoked = revoke(db, a.label, now)
        if not revoked:
            raise NotFound(f"no enrolled node is labelled {a.label!r} (nsf web node list)")
        return {"revoked": revoked, "nodes": listing(db)}
    approve(db, a.fingerprint, a.label, now, by="owner:cli")
    return {"enrolled": a.label, "nodes": listing(db)}
