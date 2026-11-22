"""Voice routes for the owner console and the Pocket bridge (one implementation, two adapters).

Console (loopback, session token, Host/Origin checks already passed): `/api/voice/<op>`; GET for status, events,
speech and transcripts (query string), POST JSON bodies of at most 4 KiB, and POST /api/voice/audio with a raw
`audio/l16; rate=16000` body of at most 64 KiB (the only route that accepts more than JSON; every other console
limit is unchanged).
Pocket (paired credential over HTTPS on the LAN): POST `/api/voice_<op>`; JSON bodies of at most 512 bytes (1 KiB for
voice_text / voice_correct, like research_submit), voice_audio raw as above. A paired device acts as a Pocket
principal only while the individual's pocket_research switch is on (default off; otherwise Pocket voice is invisible:
voice_status answers {"voice": false} and every other route 404): its own family's tasks and transcripts, no owner
settings, no knowledge-base reads and no sensitive owner commands.
Audio parameters travel in headers (X-NSF-Session, X-NSF-Utterance, X-NSF-Seq, X-NSF-Final) because Pocket routes
match the path exactly.
"""
from __future__ import annotations

import json
import threading
import time
from urllib.parse import parse_qs

from ..instance.errors import InstanceError, ValidationError
from .bridge import Principal
from .hub import MAX_CHUNK, VoiceHub

MAX_JSON = 4096
POCKET_JSON = 512
POCKET_TEXT = 1024
POCKET_WAIT = 7.0          # Pocket connections must reply within its per-connection deadline
AUDIO_TYPE = "audio/l16"
_HUBS: dict[int, VoiceHub] = {}
_HUBS_LOCK = threading.Lock()

CONSOLE_GET = {"status", "events", "speech", "transcripts", "selftest_speech"}
CONSOLE_POST = {"session", "consent", "mode", "settings", "capture", "audio", "text", "confirm", "interrupt",
                "client", "correct", "forget", "selftest", "selftest_audio"}
POCKET_OPS = {"status", "session", "consent", "mode", "settings", "capture", "audio", "text", "confirm",
              "interrupt", "client", "events", "speech", "transcripts", "correct", "forget", "selftest",
              "selftest_speech", "selftest_audio"}
AUDIO_OPS = ("audio", "selftest_audio")
AUDIO_HEADERS = ("X-NSF-Session", "X-NSF-Utterance", "X-NSF-Seq", "X-NSF-Final", "X-NSF-Selftest", "X-NSF-Index")
POCKET_ROUTES = {"voice_" + op for op in POCKET_OPS}


def hub_for(server, db_path, clock=time.time) -> VoiceHub:
    with _HUBS_LOCK:
        hub = getattr(server, "voice_hub", None)
        if hub is None:
            hub = VoiceHub(db_path, clock=clock)
            server.voice_hub = hub
        return hub


def _fields(body, required=(), optional=()):
    unknown = sorted(set(body) - set(required) - set(optional))
    if unknown:
        raise ValidationError("unknown field(s): " + ", ".join(unknown))
    missing = [k for k in required if k not in body]
    if missing:
        raise ValidationError("missing field(s): " + ", ".join(missing))
    return body


MAX_INT = 2 ** 53 - 1        # SQLite and JavaScript both hold it exactly; 2**63 overflowed SQLite (T5-05)
STRING_FIELDS = ("session", "session_id", "id", "turn", "kind", "action", "mode", "device", "label", "text", "q",
                 "run", "step", "surface")


def _int(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or not -MAX_INT <= value <= MAX_INT:
        raise ValidationError(f"{name} must be an integer of at most {MAX_INT} in size")
    return value


def run(hub: VoiceHub, principal: Principal, op: str, b: dict, *, audio=None, headers=None, wait_cap=None):
    """One operation. Returns (status, payload, mime); payload is bytes for audio. Identifier and text fields
    must be strings (an object as `session` was a 500 for Pocket, T5-05)."""
    for key in STRING_FIELDS:
        if b.get(key) is not None and not isinstance(b[key], str):
            raise ValidationError(f"{key} must be a string")
    if op == "status":
        _fields(b, (), ("session", "probe"))
        return 200, hub.status(principal, b.get("session"), probe=bool(b.get("probe"))), None
    if op == "session":
        _fields(b, ("action",), ("device", "mode", "session", "label"))
        if b["action"] == "start":
            device = b.get("device")
            if not isinstance(device, str) or not 4 <= len(device) <= 64 or not device.replace("-", "").replace(
                    "_", "").isalnum():
                raise ValidationError("device must be a client id of 4..64 letters, digits, - or _")
            device = principal.device_fixed or principal.device_prefix + device
            return 200, hub.start(principal, device, principal.label, b.get("mode")), None
        if b["action"] == "stop":
            return 200, hub.stop(b.get("session"), principal), None
        raise ValidationError("action must be start or stop")
    if op == "consent":
        _fields(b, ("session", "accept"))
        if not isinstance(b["accept"], bool):
            raise ValidationError("accept must be true or false")
        return 200, hub.consent(b["session"], principal, b["accept"]), None
    if op == "mode":
        _fields(b, ("session", "mode"))
        return 200, hub.set_mode(b["session"], principal, b["mode"]), None
    if op == "settings":
        return 200, hub.settings(principal, b), None
    if op == "capture":
        _fields(b, ("session", "action"), ("confirm",))
        return 200, hub.capture(b["session"], principal, b["action"], handoff_confirm=b.get("confirm")), None
    if op == "audio":
        h = headers or {}
        try:
            seq = int(h.get("X-NSF-Seq", ""))
        except ValueError:
            raise ValidationError("X-NSF-Seq must be an integer") from None
        final = h.get("X-NSF-Final") == "1"
        return 200, hub.audio(h.get("X-NSF-Session"), principal, h.get("X-NSF-Utterance"), seq, final, audio), None
    if op == "selftest":
        _fields(b, ("action",), ("run", "step", "data", "surface"))
        return 200, hub.selftest(principal, b["action"], b), None
    if op == "selftest_audio":
        h = headers or {}
        try:
            seq, index = int(h.get("X-NSF-Seq", "")), int(h.get("X-NSF-Index", ""))
        except ValueError:
            raise ValidationError("X-NSF-Seq and X-NSF-Index must be integers") from None
        return 200, hub.selftest_audio(principal, h.get("X-NSF-Selftest"), index, seq, h.get("X-NSF-Final") == "1",
                                       audio), None
    if op == "selftest_speech":
        _fields(b, ("run", "n"))
        return 200, hub.selftest_speech(principal, b["run"], _int(b["n"], "n")), "audio/wav"
    if op == "text":
        _fields(b, ("session", "text"))
        return 200, hub.text(b["session"], principal, b["text"]), None
    if op == "confirm":
        _fields(b, ("session", "id", "accept"))
        if not isinstance(b["accept"], bool):
            raise ValidationError("accept must be true or false")
        return 200, hub.confirm(b["session"], principal, b["id"], b["accept"]), None
    if op == "interrupt":
        _fields(b, ("session", "kind"), ("turn", "sentence", "detect_ms", "stop_ms"))
        return 200, hub.interrupt(b["session"], principal, b.get("turn"), b.get("sentence", 0), b["kind"],
                                  detect_ms=b.get("detect_ms"), stop_ms=b.get("stop_ms")), None
    if op == "client":
        _fields(b, ("session",), ("mic_capturing", "speaking", "user_speaking", "output_muted"))
        return 200, hub.client_status(b.pop("session"), principal, **b), None
    if op == "events":
        _fields(b, ("session", "since"), ("wait",))
        wait = b.get("wait", 15)
        if isinstance(wait, bool) or not isinstance(wait, (int, float)) or wait < 0:
            raise ValidationError("wait must be a non-negative number")
        if wait_cap is not None:
            wait = min(wait, wait_cap)
        return 200, hub.events(b["session"], principal, _int(b["since"], "since"), wait=wait), None
    if op == "speech":
        _fields(b, ("session", "turn", "n"))
        return 200, hub.speech(b["session"], principal, b["turn"], _int(b["n"], "n")), "audio/wav"
    if op == "transcripts":
        _fields(b, (), ("session", "q", "limit"))
        return 200, hub.transcripts(principal, session_id=b.get("session"), query=b.get("q"),
                                    limit=_int(b.get("limit", 200), "limit")), None
    if op == "correct":
        _fields(b, ("segment", "text"))
        return 200, hub.correct(principal, _int(b["segment"], "segment"), b["text"]), None
    if op == "forget":
        _fields(b, ("session_id", "confirm"))
        if b["confirm"] is not True:
            raise ValidationError("forgetting a conversation needs confirm: true")
        return 200, hub.forget(principal, b["session_id"]), None
    raise ValidationError("unknown voice operation")


def _owner():
    return Principal(name="owner", owner=True, research=True, origin="voice", label="Owner console",
                     device_prefix="console:")


# ---- console adapter -------------------------------------------------------------------------------------------

def console(handler, url):
    """Called by the console Handler for /api/voice/* after the token, Host and Origin checks."""
    op = url.path[len("/api/voice/"):]
    method = handler.command
    if op not in CONSOLE_GET | CONSOLE_POST:
        return handler.fail(404, "unknown route")
    want = "GET" if op in CONSOLE_GET else "POST"
    if method != want and not (method == "HEAD" and want == "GET"):
        return handler.reply(405, {"error": "method not allowed"}, extra={"Allow": want})
    try:
        size = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        return handler.fail(400, "invalid Content-Length")
    if size < 0 or handler.headers.get("Transfer-Encoding"):
        return handler.fail(400, "invalid body framing")
    headers, audio, body = {}, None, {}
    if want == "GET":
        if size:
            return handler.fail(400, "GET requests carry no body")
        body = {k: v[-1] for k, v in parse_qs(url.query).items()}
        for key in ("since", "n", "limit"):
            if key in body:
                try:
                    body[key] = int(body[key])
                except ValueError:
                    return handler.fail(400, f"{key} must be an integer")
        if "wait" in body:
            try:
                body["wait"] = float(body["wait"])
            except ValueError:
                return handler.fail(400, "wait must be a number")
        if "probe" in body:
            body["probe"] = body["probe"] in ("1", "true")
    elif op in AUDIO_OPS:
        if size > MAX_CHUNK:
            handler.close_connection = True
            return handler.fail(413, f"audio chunk exceeds {MAX_CHUNK} bytes")
        if handler.headers.get_content_type() != AUDIO_TYPE or "rate=16000" not in \
                handler.headers.get("Content-Type", "").replace(" ", "").lower():
            return handler.fail(400, "Content-Type must be audio/L16; rate=16000")
        audio = handler.rfile.read(size)
        headers = {k: handler.headers.get(k) for k in AUDIO_HEADERS}
    else:
        if size > MAX_JSON:
            handler.close_connection = True
            return handler.fail(413, f"body exceeds {MAX_JSON} bytes")
        if handler.headers.get_content_type() != "application/json":
            return handler.fail(400, "Content-Type must be application/json")
        try:
            body = json.loads(handler.rfile.read(size)) if size else None
        except (ValueError, UnicodeDecodeError):
            body = None
        if not isinstance(body, dict):
            return handler.fail(400, "body must be a JSON object")
    server = handler.server
    hub = hub_for(server, server.db_path, server.clock)
    try:
        status, payload, mime = run(hub, _owner(), op, body, audio=audio, headers=headers)
    except InstanceError as error:
        return handler.fail(error.http_status, str(error))
    except Exception:
        return handler.fail(500, "internal error")
    if mime:
        return handler.reply(status, payload, mime)
    return handler.reply(status, payload)


# ---- Pocket adapter -------------------------------------------------------------------------------------------------

def pocket_principal(server, token):
    """The paired device's principal (PermissionError when the credential is unknown or revoked)."""
    from ..pocket import server as pocket
    from ..webcortex import nodes, state as wstate
    from ..instance import store as individual
    family = server.store.family(token)
    db = individual.connect(server.instance_db)
    try:
        digest = wstate.family_digest(db, family)
        # instance-salted pseudonym of this one device (never the credential or its fingerprint)
        device = wstate.family_digest(db, "voice-device:" + pocket.fingerprint(token))[:16]
        # the device as the research routes know it: voice changes research under their rules (voice/bridge.py)
        node_device = nodes.device_digest(db, pocket.fingerprint(token))
    finally:
        db.close()
    tag = digest[:12]        # what the instance stores: the salted family pseudonym, never the Pocket fingerprint
    p = Principal(name=f"pocket:{tag}", owner=False, research=pocket.research_on(server), origin="pocket-voice",
                  family=digest, admit=lambda submit: server.store.admit(family, submit),
                  label=f"Pocket device ({tag})", device_prefix=f"pocket:{tag}:",
                  device_fixed=f"pocket:{tag}:{device}", node_device=node_device)
    return p


def pocket(handler, route):
    """Called by the Pocket Handler for /api/voice_* after the Host and Origin checks. Returns (status, body[, mime])."""
    server = handler.server
    path = getattr(server, "instance_db", None)
    from pathlib import Path
    auth = handler.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return 403, {}
    try:
        principal = pocket_principal(server, auth[7:]) if path and Path(path).is_file() else None
        if principal is None or not principal.research:
            server.store.family(auth[7:])
    except PermissionError:
        return 403, {}
    if principal is None or not principal.research:
        # Without an individual, or while pocket_research is off (the default), Pocket has no voice: voice reaches
        # research, transcripts and the owner's microphone, so it is gated by the same switch as Pocket research.
        # A paired device learns that this bridge offers no voice (no 404 to log).
        return (200, {"voice": False}) if route == "voice_status" else (404, {})
    op = route[len("voice_"):]
    try:
        size = int(handler.headers.get("Content-Length", "0"))
    except ValueError:
        return 400, {}
    headers, audio, body = {}, None, {}
    if op in AUDIO_OPS:
        if not 0 <= size <= MAX_CHUNK or handler.headers.get_content_type() != AUDIO_TYPE:
            return 400, {}
        try:
            audio = handler.read_body(size)
        except ValueError:
            return 400, {}
        headers = {k: handler.headers.get(k) for k in AUDIO_HEADERS}
    else:
        limit = POCKET_TEXT if op in ("text", "correct", "selftest") else POCKET_JSON
        if handler.headers.get_content_type() != "application/json" or not 2 <= size <= limit:
            return 400, {}
        try:
            body = json.loads(handler.read_body(size))
        except (ValueError, UnicodeDecodeError):
            return 400, {}
        if not isinstance(body, dict):
            return 400, {}
    replying = getattr(server, "replying", None)
    if op in ("events", "speech", "audio", "text", "selftest_audio", "selftest_speech") and replying:
        replying()      # the request is complete; waiting for events or synthesis is not a slow client
    hub = hub_for(server, path)
    try:
        status, payload, mime = run(hub, principal, op, body, audio=audio, headers=headers, wait_cap=POCKET_WAIT)
    except InstanceError as error:
        return error.http_status, {"error": str(error)[:300]}
    if mime:
        return status, payload, mime
    return status, payload
