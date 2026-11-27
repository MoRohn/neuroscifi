"""Request helpers of the owner console, shared by `console/server.py` and `console/artifacts.py` (moved here by
OMNIMIND fix lane F4, code review: artifacts.py imported these private helpers from server.py, which imports
artifacts.py: a two-way dependency on underscore names). Behaviour and messages unchanged."""
from __future__ import annotations

from ..instance.errors import ValidationError


def fields(body, required=(), optional=()):
    """Reject unknown or missing keys so a typo never silently falls back to a default."""
    unknown = sorted(set(body) - set(required) - set(optional))
    if unknown:
        raise ValidationError("unknown field(s): " + ", ".join(unknown))
    missing = [key for key in required if key not in body]
    if missing:
        raise ValidationError("missing field(s): " + ", ".join(missing))
    return body


def integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{name} must be an integer")
    return value


def query(query, allowed):
    unknown = sorted(set(query) - set(allowed))
    if unknown:
        raise ValidationError("unknown query parameter(s): " + ", ".join(unknown))
    return {key: values[-1] for key, values in query.items()}


def number(text, name, kind=float):
    if text is None:
        return None
    try:
        return kind(text)
    except ValueError:
        raise ValidationError(f"{name} must be a number") from None


__all__ = ["fields", "integer", "number", "query"]
