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
