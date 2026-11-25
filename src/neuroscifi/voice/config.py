"""Voice settings of one individual, kept in the instance `meta` table under key 'voice'.

Defaults: hybrid path on loopback (ASR 127.0.0.1:18431, TTS 127.0.0.1:18432), mode A (voice + text), raw audio NOT
retained, PersonaPlex off. A speech endpoint must be loopback: audio and transcripts never leave the host through a
speech engine. The shared API key is read from a 0600 file (key_file, else NSF_VOICE_KEY_FILE, else
~/.config/neuroscifi/voice/api_key, written by deploy/voice/run.sh).
"""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import stat
from urllib.parse import urlsplit

from .. import paths
from ..instance import store
from ..instance.errors import ValidationError

MODES = ("voice_text", "voice_in_text_out", "text_only")
# Kokoro-82M v1.0 American/British English voices served by deploy/voice/tts_server.py (same allow-list there).
VOICES = ("af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "am_adam", "am_michael", "am_onyx",
          "bf_emma", "bf_isabella", "bm_george", "bm_lewis")
# PersonaPlex's bundled voice embeddings; a request never names a file (upstream path traversal, audit §2.1).
PERSONAPLEX_VOICES = tuple(f"{p}{i}" for p, n in (("NATF", 4), ("NATM", 4), ("VARF", 5), ("VARM", 5))
                           for i in range(n))
MAX_AUDIO_TTL_HOURS = 168
DEFAULTS = {
    "mode": "voice_text", "voice": "af_heart", "speed": 1.0, "retain_audio": False, "audio_ttl_hours": 24,
    "transcript_visible": True, "asr_url": "http://127.0.0.1:18431", "asr_model": "whisper-large-v3-turbo",
    "tts_url": "http://127.0.0.1:18432", "key_file": None, "personaplex": False,
    "personaplex_url": "http://127.0.0.1:18998", "personaplex_voice": "NATF2", "consent": {},
    # R8 (ADR-053): a spoken question's research answer is still delivered after the console or Pocket restarts: the
    # session keeps which task it follows, and the principal's next session picks the follow up again. On by
    # default (it fixes a recorded finding; nothing new is stored beyond one task id in the session context).
    "resume_follow": True,
    # ADR-053, review1 A15: "what are you working on?" and the other status questions that started research before
    # the voice command "what are you doing" existed keep starting research unless the owner switches this on (then
    # they get the short self-description). Off by default: an existing phrase keeps what it did.
    "status_questions": False,
}
OWNER_ONLY = {"retain_audio", "audio_ttl_hours", "asr_url", "asr_model", "tts_url", "key_file", "personaplex",
              "personaplex_url", "personaplex_voice", "resume_follow", "status_questions"}
DEFAULT_KEY_FILE = paths.home() / ".config" / "neuroscifi" / "voice" / "api_key"     # never raises (paths.home)


def loopback_url(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    if parts.hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(parts.hostname).is_loopback
    except ValueError:
        return False


def get(db) -> dict:
    row = db.execute("SELECT value FROM meta WHERE key='voice'").fetchone()
    stored = json.loads(row[0]) if row else {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def _validate(name, value):
    if name == "mode" and value not in MODES:
        raise ValidationError("mode must be one of " + ", ".join(MODES))
    if name == "voice" and value not in VOICES:
        raise ValidationError("voice must be one of " + ", ".join(VOICES))
    if name == "personaplex_voice" and value not in PERSONAPLEX_VOICES:
        raise ValidationError("personaplex_voice must be one of " + ", ".join(PERSONAPLEX_VOICES))
    if name == "speed" and (isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.5 <= value <= 2.0):
        raise ValidationError("speed must be between 0.5 and 2.0")
    if name in ("retain_audio", "transcript_visible", "personaplex", "resume_follow", "status_questions") \
            and not isinstance(value, bool):
        raise ValidationError(f"{name} must be true or false")
    if name == "audio_ttl_hours" and (isinstance(value, bool) or not isinstance(value, int)
                                      or not 1 <= value <= MAX_AUDIO_TTL_HOURS):
        raise ValidationError(f"audio_ttl_hours must be an integer from 1 to {MAX_AUDIO_TTL_HOURS}")
    if name in ("asr_url", "tts_url", "personaplex_url") and (not isinstance(value, str) or not loopback_url(value)):
        raise ValidationError(f"{name} must be an http(s) URL on a loopback address (speech stays on this host)")
    if name in ("asr_model",) and (not isinstance(value, str) or not 0 < len(value) <= 128):
        raise ValidationError("asr_model must be a non-empty string")
    if name == "key_file" and value is not None and (not isinstance(value, str) or not value):
        raise ValidationError("key_file must be a path or null")
    return value


def update(db, changes: dict, *, owner=True) -> dict:
    unknown = sorted(set(changes) - (set(DEFAULTS) - {"consent"}))
    if unknown:
        raise ValidationError("unknown voice setting(s): " + ", ".join(unknown))
    if not owner and set(changes) & OWNER_ONLY:
        raise ValidationError("only the owner console may change " + ", ".join(sorted(set(changes) & OWNER_ONLY)))
    with store.write(db):
        current = get(db)
        current.update({k: _validate(k, v) for k, v in changes.items()})
        db.execute("INSERT INTO meta(key,value) VALUES('voice',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (json.dumps(current, sort_keys=True),))
    return current


# A Pocket principal's own preferences (meta key 'voice_pref:<principal>'); they never rewrite the owner's settings.
PREF_KEYS = ("mode", "voice", "speed", "transcript_visible")


def effective(db, principal: str | None = None, owner: bool = True) -> dict:
    """The settings as one principal sees them: the owner's, overlaid with a non-owner's own preferences."""
    cfg = get(db)
    if owner or not principal:
        return cfg
    row = db.execute("SELECT value FROM meta WHERE key=?", ("voice_pref:" + principal,)).fetchone()
    prefs = json.loads(row[0]) if row else {}
    return {**cfg, **{k: v for k, v in prefs.items() if k in PREF_KEYS}}


def update_pref(db, principal: str, changes: dict) -> dict:
    """Record a non-owner's preferences (mode, voice, speed, transcript visibility) for that principal only."""
    unknown = sorted(set(changes) - (set(DEFAULTS) - {"consent"}))
    if unknown:
        raise ValidationError("unknown voice setting(s): " + ", ".join(unknown))
    if set(changes) - set(PREF_KEYS):
        raise ValidationError("only the owner console may change " + ", ".join(sorted(set(changes) - set(PREF_KEYS))))
    key = "voice_pref:" + principal
    with store.write(db):
        row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        prefs = json.loads(row[0]) if row else {}
        prefs.update({k: _validate(k, v) for k, v in changes.items()})
        db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, json.dumps(prefs, sort_keys=True)))
    return effective(db, principal, owner=False)


def record_consent(db, device: str, now: float, accept: bool) -> dict:
    with store.write(db):
        current = get(db)
        consent = dict(current["consent"])
        if accept:
            consent[device] = now
        else:
            consent.pop(device, None)
        current["consent"] = dict(list(consent.items())[-64:])   # bounded: at most 64 devices remembered
        db.execute("INSERT INTO meta(key,value) VALUES('voice',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (json.dumps(current, sort_keys=True),))
    return current


def public(cfg: dict) -> dict:
    """Settings safe to show: no key path contents, consent as device count only."""
    out = {k: v for k, v in cfg.items() if k not in ("consent", "key_file")}
    out["key_file_set"] = bool(cfg.get("key_file"))
    return out


def read_key(cfg: dict) -> str | None:
    """The speech API key, or None. A key file readable by group/others is refused (as the inference key is)."""
    path = Path(cfg.get("key_file") or os.environ.get("NSF_VOICE_KEY_FILE") or DEFAULT_KEY_FILE).expanduser()
    try:
        info = path.stat()
    except OSError:
        return None
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ValidationError(f"voice key file {path} must not be readable by group or others (chmod 600)")
    try:
        return path.read_text().strip() or None
    except OSError:
        return None
