"""Typed failures shared by the instance modules, the CLI and the console API.

Each error maps to one HTTP status in the console and to exit code 2 in the CLI.
"""


class InstanceError(Exception):
    http_status = 400


class ValidationError(InstanceError):
    """Malformed or unsupported input."""
    http_status = 400


class NotFound(InstanceError):
    http_status = 404


class Conflict(InstanceError):
    """An id is already bound to a different payload, or a state transition is not allowed."""
    http_status = 409


class Gone(InstanceError):
    """The id was forgotten or expired; it cannot be resurrected by a retry."""
    http_status = 410


class LeaseHeld(InstanceError):
    """Another writer owns the mutable graph for this context."""
    http_status = 423


class WritesPaused(InstanceError):
    """Regulation paused new writes; reads and approved skills remain available."""
    http_status = 507


def failure_kind(exc: BaseException) -> str:
    """A failure described without its message, for a log line (Wave 2 reconciliation, review2b N5): the exception's
    type, with SQLite's error name for a database error (SQLITE_BUSY, SQLITE_IOERR_WRITE, ...) and the errno name for
    an OS error (ENOSPC, ...). A message may name a subject and the process log is out of forget's reach, so the
    message itself is never included."""
    import errno
    import re
    kind = type(exc).__name__
    try:                    # it is called inside handlers that must not fail themselves (review2c D3): an exception
        name = getattr(exc, "sqlite_errorname", None)       # whose attribute raises is described by its type alone
        if isinstance(name, str) and re.fullmatch(r"SQLITE_[A-Z0-9_]{1,40}", name):
            return f"{kind} {name}"
        code = getattr(exc, "errno", None) if isinstance(exc, OSError) else None
        if isinstance(code, int) and not isinstance(code, bool) and code in errno.errorcode:
            return f"{kind} {errno.errorcode[code]}"
    except Exception:       # noqa: BLE001 - the type name is always available
        pass
    return kind
