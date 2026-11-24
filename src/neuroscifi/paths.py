"""This account's home folder, without ever raising.

`Path.home()` raises when HOME is unset and the uid has no account entry: a unit with `DynamicUser=`, a container
with a bare uid, a user namespace. Modules that only derive a default file name from the home folder must still
import there (the file then simply does not exist and their own defaults apply).
"""
from __future__ import annotations

import os
from pathlib import Path

NOWHERE = Path("/nonexistent")


def home(environ=None) -> Path:
    """HOME, else the account's entry, else a folder that does not exist."""
    value = (os.environ if environ is None else environ).get("HOME")
    if value:
        return Path(value)
    try:
        import pwd
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, OSError):
        return NOWHERE


__all__ = ["NOWHERE", "home"]
