"""Inference settings of one individual, stored as JSON in the instance `meta` table (key 'inference').

Default: not configured (no endpoint), so nothing contacts a model until the owner runs `nsf llm config --url ...`.
The API key is never stored in the instance: `key_file` names a file (default DEFAULT_KEY_FILE, written by
deploy/vllm/run.sh with mode 0600). Local-first: an endpoint must resolve to loopback or private addresses; any
other endpoint needs the owner's explicit `remote_allowed`, must use https, and then every outbound message passes
webcortex.safety.privacy_screen and messages marked private are refused unless `remote_private_memory` is also set.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import ipaddress
import json
import math
import os
from pathlib import Path
import socket
from urllib.parse import urlsplit

from ..instance import store
from ..instance.errors import ValidationError

META_KEY = "inference"
DEFAULT_DIR = Path(os.environ.get("NSF_INFERENCE_DIR") or Path.home() / ".config" / "neuroscifi" / "inference")
DEFAULT_KEY_FILE = DEFAULT_DIR / "api_key"
THINKING = ("default", "on", "off")


@dataclass
class InferenceConfig:
    base_url: str = ""                 # e.g. http://127.0.0.1:8811/v1; "" = not configured
    model: str = ""                    # served model name; "" = the first model the endpoint lists
    key_file: str = ""                 # API key file; "" = DEFAULT_KEY_FILE when it exists, else no key
    reasoning: bool = True             # research answers are written by the model when it is ready
    self_critique: bool = True         # one bounded critique pass that may only remove sentences
    tools: bool = False                # `nsf llm ask` may offer the allow-listed read-only tools
    remote_allowed: bool = False       # owner authorisation for an endpoint that is not loopback/private
    remote_private_memory: bool = False   # ... and for sending private memory to it
    connect_timeout: float = 3.0       # seconds to open the connection
    read_timeout: float = 180.0        # seconds for one whole call (streaming included)
    max_retries: int = 2               # retries after the first attempt (connection errors, 429, 502-504)
    backoff: float = 0.5               # first retry delay; doubles per retry; Retry-After honoured up to 10 s
    max_context_tokens: int = 24000    # prompt + output estimate per call (the server's max-model-len is larger)
    max_output_tokens: int = 1200
    temperature: float = 0.2
    thinking: str = "default"          # Qwen3-style chat_template_kwargs.enable_thinking: default / on / off

    @classmethod
    def from_json(cls, text: str | None) -> "InferenceConfig":
        raw = json.loads(text) if text else {}
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def to_json(self) -> dict:
        return asdict(self)

    @property
    def configured(self) -> bool:
        return bool(self.base_url)


_BOOLS = {"reasoning", "self_critique", "tools", "remote_allowed", "remote_private_memory"}
_RANGES = {"connect_timeout": (0.1, 60.0), "read_timeout": (1.0, 3600.0), "backoff": (0.0, 30.0),
           "temperature": (0.0, 2.0)}
_INTS = {"max_retries": (0, 5), "max_context_tokens": (512, 1_000_000), "max_output_tokens": (16, 32768)}


def _validate(name, value):
    if name in _BOOLS:
        if not isinstance(value, bool):
            raise ValidationError(f"{name} must be true or false")
        return value
    if name in _RANGES:
        lo, hi = _RANGES[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
                or not lo <= value <= hi:
            raise ValidationError(f"{name} must be a number between {lo:g} and {hi:g}")
        return float(value)
    if name in _INTS:
        lo, hi = _INTS[name]
        if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
            raise ValidationError(f"{name} must be an integer between {lo} and {hi}")
        return value
    if name == "thinking":
        if value not in THINKING:
            raise ValidationError("thinking must be one of " + ", ".join(THINKING))
        return value
    if not isinstance(value, str) or len(value) > 1024:
        raise ValidationError(f"{name} must be a string of at most 1024 characters")
    if name == "base_url" and value:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.query or parts.fragment \
                or parts.username or parts.password:
            raise ValidationError("base_url must be http(s)://host[:port]/path without credentials, query or "
                                  "fragment, e.g. http://127.0.0.1:8811/v1")
        return value.rstrip("/")
    if name == "key_file" and value and not os.path.isabs(value):
        raise ValidationError("key_file must be an absolute path")
    return value


def get_config(db) -> InferenceConfig:
    row = db.execute("SELECT value FROM meta WHERE key=?", (META_KEY,)).fetchone()
    return InferenceConfig.from_json(row[0] if row else None)


def set_config(db, changes: dict, now=None) -> InferenceConfig:
    """Validate and apply `changes` (field -> value); unknown keys are refused. One state_version bump."""
    if not isinstance(changes, dict) or not changes:
        raise ValidationError("configuration changes must be a non-empty object")
    known = {f.name for f in fields(InferenceConfig)}
    unknown = sorted(set(changes) - known)
    if unknown:
        raise ValidationError(f"unknown inference setting(s): {', '.join(unknown)}")
    validated = {name: _validate(name, value) for name, value in changes.items()}
    with store.write(db):
        current = get_config(db).to_json()
        current.update(validated)
        db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (META_KEY, json.dumps(current, sort_keys=True)))
        store.bump_state_version(db)
    return get_config(db)


def public(cfg: InferenceConfig) -> dict:
    """The configuration as shown to the owner (no key, only whether a key file is present)."""
    out = cfg.to_json()
    path = key_path(cfg)
    out["key_file_effective"] = str(path) if path else None
    out["key_present"] = bool(path and path.is_file())
    return out


def key_path(cfg: InferenceConfig) -> Path | None:
    if cfg.key_file:
        return Path(cfg.key_file)
    env = os.environ.get("NSF_INFERENCE_KEY_FILE")
    if env:
        return Path(env)
    return DEFAULT_KEY_FILE if DEFAULT_KEY_FILE.is_file() else None


def read_key(cfg: InferenceConfig) -> str | None:
    """The API key from the key file, or None. A key file readable by everyone is refused (it would leak)."""
    path = key_path(cfg)
    if path is None or not path.is_file():
        return None
    if path.stat().st_mode & 0o004:
        raise ValidationError(f"{path} is readable by every user; chmod 600 (or 640 for a service group)")
    key = path.read_text(encoding="utf-8").strip()
    return key or None


def locality(url: str, *, resolver=socket.getaddrinfo) -> tuple[str, str]:
    """('loopback' | 'private' | 'remote', detail) for an endpoint URL. A host name counts as local only when
    every address it resolves to is loopback (or private, which is any non-global address: RFC 1918, ULA,
    link-local, CGNAT/tailnet). An unresolvable name is 'remote' (it cannot be shown to be local)."""
    host = urlsplit(url).hostname or ""
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = resolver(host, None, 0, socket.SOCK_STREAM)
        except (OSError, UnicodeError):
            return "remote", f"{host} does not resolve"
        addrs = []
        for info in infos:
            try:
                addrs.append(ipaddress.ip_address(info[4][0].split("%")[0]))
            except ValueError:
                return "remote", f"{host} resolves to an unparsable address"
    if not addrs:
        return "remote", f"{host} has no address"
    if all(a.is_loopback for a in addrs):
        return "loopback", host
    if all(a.is_loopback or not a.is_global for a in addrs):
        return "private", host
    return "remote", host


__all__ = ["DEFAULT_KEY_FILE", "InferenceConfig", "META_KEY", "get_config", "key_path", "locality", "public",
           "read_key", "set_config"]
