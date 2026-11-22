"""API keys of keyed search providers (ADR-034 amendment 1): outside git, owner-only, never shown or logged.

A key lives in `<key_dir>/<provider>.key`, mode 0600, in a 0700 directory. key_dir is NSF_SEARCH_KEY_DIR, else
$XDG_CONFIG_HOME/neuroscifi/search, else ~/.config/neuroscifi/search (the SearXNG secret's convention). `nsf web
provider set-key NAME --from-file PATH | --stdin` writes it (never from argv: argv is visible in ps and the shell
history). `read_key` refuses a key file that is a symlink or not a regular file, is not owned by this user, or
that group or others can read or write. The key text goes to the one caller that puts it in a request header; it is
never stored on a provider object, in the configuration, in the instance, in an event, error or evidence file, and
status output says only "configured".

`Ledger` is the per-key usage record `<key_dir>/<provider>.usage.json` (0600, flock-serialised): queries sent on the
UTC day (the daily cap), the provider's own rate-limit state, the last success and the last error (scrubbed of the
key). It belongs to the key, not to one individual, so every instance and process on this host that uses the key
counts against the same day.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import stat
import threading
import time

KEYED = ("brave",)
MAX_KEY_CHARS = 256
UNREADABLE_QUERIES = 1_000_000          # a damaged ledger counts as a used-up day, never as an empty one
_LOCK = threading.Lock()


class KeyProblem(Exception):
    """The key is missing or unusable. `code`: 'key_missing' | 'key_insecure' | 'key_invalid'."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


def key_dir() -> Path:
    if os.environ.get("NSF_SEARCH_KEY_DIR"):
        return Path(os.environ["NSF_SEARCH_KEY_DIR"])
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "neuroscifi" / "search"


def _known(name: str) -> str:
    if name not in KEYED:
        raise KeyProblem("key_invalid", f"no keyed provider named {name!r} (known: {', '.join(KEYED)})")
    return name


def key_path(name: str) -> Path:
    return key_dir() / f"{_known(name)}.key"


def normalise_key(text) -> str:
    """The key as sent: surrounding whitespace (a trailing newline) removed; printable ASCII without spaces."""
    key = str(text or "").strip()
    if not key:
        raise KeyProblem("key_invalid", "the key is empty")
    if len(key) > MAX_KEY_CHARS or not all(33 <= ord(c) < 127 for c in key):
        raise KeyProblem("key_invalid", f"a key is printable ASCII without spaces, at most {MAX_KEY_CHARS} "
                                        "characters")
    return key


def _check(path: Path) -> os.stat_result:
    """lstat of a usable key file, else KeyProblem (missing, or insecure: what is wrong and how to fix it). The
    directory must be ours and closed to writes by group and others (nobody else can swap the file)."""
    try:
        info = os.lstat(path)
        folder = os.stat(path.parent)
    except (FileNotFoundError, NotADirectoryError):
        raise KeyProblem("key_missing", f"no key file {path}") from None
    except OSError as exc:
        raise KeyProblem("key_insecure", f"cannot inspect {path}: {exc.strerror or type(exc).__name__}") from None
    if folder.st_uid != os.getuid() or stat.S_IMODE(folder.st_mode) & 0o022:
        raise KeyProblem("key_insecure", f"{path.parent} must be owned by this user and not writable by group or "
                                         f"others (chmod 700 {path.parent})")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise KeyProblem("key_insecure", f"{path} is not a regular file (a symlink is refused)")
    if info.st_uid != os.getuid():
        raise KeyProblem("key_insecure", f"{path} is not owned by this user")
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o077:
        raise KeyProblem("key_insecure", f"{path} has mode {mode:04o}; group and others must have no access "
                                         f"(chmod 600 {path})")
    return info


def key_state(name: str) -> dict:
    """{'key': 'configured' | 'missing' | 'insecure', 'key_file': path, 'problem': text or None}. Never the key.
    Never raises for a key that is merely absent or unreadable (no home directory included): search must not stop
    for a provider the owner does not use."""
    try:
        path = key_path(name)
    except RuntimeError:                     # no home directory to resolve ~/.config from
        return {"key": "missing", "key_file": None, "problem": None}
    try:
        _check(path)
    except KeyProblem as exc:
        return {"key": "missing" if exc.code == "key_missing" else "insecure", "key_file": str(path),
                "problem": None if exc.code == "key_missing" else exc.detail}
    return {"key": "configured", "key_file": str(path), "problem": None}


def read_key(name: str) -> str:
    """The key, read now (no copy is kept). KeyProblem when missing, insecure or malformed."""
    path = key_path(name)
    checked = _check(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)   # a swapped-in FIFO cannot block us
    except OSError as exc:
        raise KeyProblem("key_insecure", f"cannot open {path}: {exc.strerror or type(exc).__name__}") from None
    with os.fdopen(fd, "r", encoding="ascii", errors="replace") as fh:
        info = os.fstat(fh.fileno())
        if (info.st_dev, info.st_ino) != (checked.st_dev, checked.st_ino) or not stat.S_ISREG(info.st_mode) \
                or stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid != os.getuid():
            raise KeyProblem("key_insecure", f"{path} changed while it was opened")
        text = fh.read(MAX_KEY_CHARS + 64)
    return normalise_key(text)


def _private_dir() -> Path:
    folder = key_dir()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    return folder


def write_key(name: str, text) -> Path:
    """Store a key: a new 0600 file renamed over `<name>.key` (atomic; the old key is replaced, never appended)."""
    key = normalise_key(text)
    path = key_path(name)
    folder = _private_dir()
    temp = folder / f".{name}.key.{os.getpid()}.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            os.fchmod(fh.fileno(), 0o600)
            fh.write(key + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return path


def remove_key(name: str) -> bool:
    path = key_path(name)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def scrub(text, key: str | None) -> str:
    """`text` with every occurrence of the key replaced (provider error bodies are never trusted not to echo it)."""
    text = str(text or "")
    return text.replace(key, "[key]") if key else text


def day_of(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")


class CapReached(Exception):
    def __init__(self, used: int, cap: int, day: str):
        super().__init__(f"daily cap of {cap} queries reached on {day} (UTC; {used} sent)")
        self.used, self.cap, self.day = used, cap, day


class Ledger:
    """The per-key daily usage and rate-limit record of one keyed provider (see the module docstring)."""

    def __init__(self, name: str, *, clock=time.time):
        self.name, self._clock = _known(name), clock

    @property
    def path(self) -> Path:
        return key_dir() / f"{self.name}.usage.json"

    @contextmanager
    def _edit(self):
        """The record under an exclusive flock (threads and processes); written back when the block succeeds."""
        _private_dir()
        with _LOCK:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                os.fchmod(fd, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX)
                raw = b""
                while chunk := os.read(fd, 65536):
                    raw += chunk
                today = day_of(self._clock())
                try:
                    data = json.loads(raw.decode("utf-8")) if raw else {}
                except ValueError:
                    data = None
                if not isinstance(data, dict):     # damaged (crash mid-write): fail closed, today's cap is used
                    data = {"day": today, "queries": UNREADABLE_QUERIES,
                            "last_error": "usage ledger was unreadable; today's cap is treated as used"}
                if data.get("day") != today:
                    data["day"], data["queries"] = today, 0
                yield data
                out = json.dumps(data, sort_keys=True).encode()
                os.lseek(fd, 0, os.SEEK_SET)
                os.ftruncate(fd, 0)
                os.write(fd, out)
                os.fsync(fd)
            finally:
                os.close(fd)

    def read(self) -> dict:
        """The record for today (queries 0 on a new day); {} fields when nothing was recorded yet."""
        if not self.path.exists():
            return {"day": day_of(self._clock()), "queries": 0}
        with self._edit() as data:
            return dict(data)

    def reserve(self, cap: int) -> int:
        """Count one query against today's cap before it is sent; CapReached (nothing counted) when none is left."""
        with self._edit() as data:
            used = int(data.get("queries") or 0)
            if used >= int(cap):
                raise CapReached(used, int(cap), data["day"])
            data["queries"] = used + 1
            return used + 1

    def refund(self) -> None:
        """Return a reserved query the provider cannot have billed (refused before sending, or answered with an
        error status). A crash before it over-counts, which only errs towards the cap."""
        with self._edit() as data:
            data["queries"] = max(0, int(data.get("queries") or 0) - 1)

    def limited(self) -> dict | None:
        """{'until', 'reason'} while a rate limit the provider announced is in force, else None."""
        data = self.read()
        until = data.get("rate_limited_until")
        if isinstance(until, (int, float)) and until > self._clock():
            return {"until": float(until), "reason": str(data.get("rate_limit_reason") or "rate limited")}
        return None

    def note(self, *, ok: bool, error: str | None = None, limited_for: float | None = None,
             reason: str | None = None, remaining: dict | None = None) -> None:
        now = self._clock()
        with self._edit() as data:
            if ok:
                data["last_ok_at"] = now
            elif error:
                data["last_error"], data["last_error_at"] = str(error)[:300], now
            until = now + max(0.0, float(limited_for)) if limited_for is not None else None
            current = data.get("rate_limited_until")
            if until is not None and not (isinstance(current, (int, float)) and current >= until):
                data["rate_limited_until"] = until        # the later block wins (a 429 never shortens a quota wait)
                data["rate_limit_reason"] = str(reason or "rate limited")[:200]
            if remaining is not None:
                data["remaining"] = remaining


__all__ = ["KEYED", "KeyProblem", "CapReached", "Ledger", "key_dir", "key_path", "key_state", "read_key",
           "write_key", "remove_key", "normalise_key", "scrub", "day_of"]
