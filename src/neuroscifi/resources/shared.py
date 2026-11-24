"""The files of `<db>.resources/`, which one account or two may use (RESOURCE_SCHEDULING.md §3d).

One account (`nsf start`, a developer's checkout): the folder is created 0700 by whoever needs it first and every
file in it is 0600, as before.

Two accounts (deploy/install.sh --idle): the console, the worker and Pocket run as the service account, the idle
and wake units as the operations account that may start and stop the model services. Both read and write this
folder: the audio beacon and the use markers are the service account's, the policy's state and events the
operations account's, and each takes the other's locks. The installer makes the folder setgid and group-writable
(2770, group: the operations group). That is the one signal this module reads: in such a folder a file this
process creates is made group-readable and group-writable (0660), set on the open descriptor because a unit's
`UMask=0077` would otherwise remove the group's bits; the folder's setgid bit gives it the folder's group.

What this module never does: change the mode or the owner of a folder, or of a file it did not create in the same
call. A folder that is not setgid and group-writable keeps private files, whatever its group is.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat

PRIVATE_FILE = 0o600
SHARED_FILE = 0o660
PRIVATE_FOLDER = 0o700


def ensure_folder(folder) -> Path:
    """Create the folder private (0700) if it is missing; an existing one is left exactly as it is."""
    folder = Path(folder)
    folder.mkdir(mode=PRIVATE_FOLDER, exist_ok=True)
    return folder


def is_shared(folder) -> bool:
    """Whether the folder is one two accounts share: setgid and writable by its group (the installer's 2770)."""
    try:
        mode = os.stat(folder).st_mode
    except OSError:
        return False
    return bool(mode & stat.S_ISGID) and bool(mode & stat.S_IWGRP)


def file_mode(folder) -> int:
    return SHARED_FILE if is_shared(folder) else PRIVATE_FILE


def open_file(path, flags) -> int:
    """os.open for a file of the folder: an existing file is opened as it is (its mode is never touched); a missing
    one is created with the folder's file mode, set on the descriptor so that a umask cannot narrow it."""
    path = Path(path)
    flags &= ~(os.O_CREAT | os.O_EXCL)
    mode = file_mode(path.parent)
    for _ in range(3):
        try:
            fd = os.open(path, flags | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        except FileExistsError:
            try:
                return os.open(path, flags | os.O_NOFOLLOW)
            except FileNotFoundError:
                continue                     # removed in between: create it
        else:
            if mode != PRIVATE_FILE:
                try:
                    os.fchmod(fd, mode)
                except OSError:
                    pass
            return fd
    return os.open(path, flags | os.O_CREAT | os.O_NOFOLLOW, mode)


def write_text(path, text) -> None:
    """Replace the file's content atomically (a temporary file of this process beside it, then a rename)."""
    path = Path(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        os.unlink(tmp)                       # a stale one of a process that had this pid
    except OSError:
        pass
    fd = open_file(tmp, os.O_WRONLY | os.O_TRUNC)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def touch(path, when=None) -> None:
    """Create the file if missing and set its times to `when` (None: now). Setting a chosen time needs the file's
    owner; a file the other account created can only be set to "now", which is what a use marker means."""
    os.close(open_file(path, os.O_WRONLY))
    try:
        os.utime(path, None if when is None else (when, when))
    except PermissionError:
        os.utime(path, None)


__all__ = ["PRIVATE_FILE", "PRIVATE_FOLDER", "SHARED_FILE", "ensure_folder", "file_mode", "is_shared", "open_file",
           "touch", "write_text"]
