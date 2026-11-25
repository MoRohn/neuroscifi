"""The files of `<db>.resources/`, which one account or two may use (RESOURCE_SCHEDULING.md §3d).

One account (`nsf start`, a developer's checkout): the folder is created 0700 by whoever needs it first and every
file in it is 0600, as before.

Two accounts (deploy/install.sh --idle): the console, the worker and Pocket run as the service account, the idle
and wake units as the operations account that may start and stop the model services. Both read and write this
folder: the audio beacon and the use markers are the service account's, the policy's state and events the
operations account's, and each takes the other's locks. The installer makes the folder setgid and group-writable
(2770, group: the operations group). That is the one signal this module reads: in such a folder a file this
process creates is made group-readable and group-writable (0660), set on the open descriptor because a unit's
`UMask=0077` would otherwise remove the group's bits; the folder's setgid bit gives it the folder's group. A file
only the service account writes (the in-flight files, the internal-state history: `group_write=False`) is made
0640 there instead: the other account reads it and cannot rewrite what the trusted account wrote (review1b R2).

What this module never does: change the mode or the owner of a folder, or of a file it did not create in the same
call. A folder that is not setgid and group-writable keeps private files, whatever its group is.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import stat

PRIVATE_FILE = 0o600
SHARED_FILE = 0o660
SHARED_READ_ONLY_FILE = 0o640       # a file of a shared folder the other account reads and never writes
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


def file_mode(folder, *, group_write=True) -> int:
    if not is_shared(folder):
        return PRIVATE_FILE
    return SHARED_FILE if group_write else SHARED_READ_ONLY_FILE


class NotAFile(OSError):
    """What is at a path of the folder is not a plain file (a link, a FIFO, a device, a folder)."""


def _regular(fd, path) -> int:
    """`fd` if it is a plain file; else it is closed and NotAFile raised. In a folder two accounts write, either can
    put a FIFO (an open or a read that never returns), a link to /dev/zero (a read that never ends) or a device
    where a file is expected (review L4)."""
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise NotAFile(f"{path} is not a plain file")
    return fd


def open_file(path, flags, *, group_write=True) -> int:
    """os.open for a file of the folder: an existing file is opened as it is (its mode is never touched); a missing
    one is created with the folder's file mode, set on the descriptor so that a umask cannot narrow it. A link is
    never followed, and anything but a plain file is refused (NotAFile) without blocking on it. group_write=False:
    a file only this account writes (0640 in a shared folder; 0600 elsewhere as always)."""
    path = Path(path)
    flags = (flags & ~(os.O_CREAT | os.O_EXCL)) | os.O_NONBLOCK | os.O_NOFOLLOW
    mode = file_mode(path.parent, group_write=group_write)
    for _ in range(3):
        try:
            fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, mode)
        except FileExistsError:
            try:
                return _regular(os.open(path, flags), path)
            except FileNotFoundError:
                continue                     # removed in between: create it
        else:
            if mode != PRIVATE_FILE:
                try:
                    os.fchmod(fd, mode)
                except OSError:
                    pass
            return fd
    return _regular(os.open(path, flags | os.O_CREAT, mode), path)


READ_LIMIT = 4 << 20                 # bytes read from one file of the folder at most


class NotOwned(OSError):
    """The file belongs to an account other than the ones allowed to write it (owners=)."""


def writers(db_path) -> set[int]:
    """The accounts whose files of `<db>.resources/` this process trusts: its own, and the instance database's owner
    (the service account of a two-account install). A file the other account of the folder created is not theirs."""
    out = {os.getuid(), os.geteuid()}
    try:
        out.add(os.stat(db_path).st_uid)
    except (OSError, TypeError, ValueError):
        pass
    return out


def read_text(path, *, limit=READ_LIMIT, tail=False, owners=None) -> str:
    """The file's text, read without following a link and without blocking (NotAFile for anything but a plain
    file). At most `limit` bytes: a larger file is refused (ValueError), or with `tail` its last `limit` bytes are
    read, from the first whole line in them. owners: the uids allowed to own the file (writers()); a file of any
    other account is refused (NotOwned)."""
    fd = _regular(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), path)
    if owners is not None and os.fstat(fd).st_uid not in owners:
        os.close(fd)
        raise NotOwned(f"{path} belongs to another account")
    try:
        size = os.fstat(fd).st_size
        if size > limit:
            if not tail:
                raise ValueError(f"{path} holds {size} bytes, more than the {limit} read from such a file")
            os.lseek(fd, size - limit, os.SEEK_SET)
        chunks, left = [], limit
        while left > 0:
            chunk = os.read(fd, min(left, 1 << 16))
            if not chunk:
                break
            chunks.append(chunk)
            left -= len(chunk)
        data = b"".join(chunks)
        if tail and size > limit:
            data = data.partition(b"\n")[2]
        return data.decode("utf-8", "replace")
    finally:
        os.close(fd)


def _no_constant(name):
    raise ValueError(f"{name} is not a number")


def _finite(text):
    value = float(text)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"{text} is not a finite number")
    return value


INT_DIGITS = 18                      # the longest integer a file of the folder may hold (a time, a count, bytes)


def _bounded_int(text):
    if len(text.lstrip("-")) > INT_DIGITS:
        raise ValueError(f"{text[:20]}... is too large a number")
    return int(text)


def loads(text):
    """json.loads for text another account wrote: anything that is not JSON raises ValueError, including text nested
    deeply enough to exhaust the parser's recursion (a state file of 200,000 '[' raised RecursionError, which the
    callers' ValueError handlers did not catch: review L4c), a number that is not finite (NaN, Infinity, 1e400:
    Python's json accepts them, and one NaN in the internal-state history made every reader raise: review1 S2), and
    an integer of more than INT_DIGITS digits (a 1 followed by 400 zeros is a Python int that no float can hold:
    every reader comparing it with a time raised OverflowError: review1b R1)."""
    import json
    try:
        return json.loads(text, parse_constant=_no_constant, parse_float=_finite, parse_int=_bounded_int)
    except RecursionError:
        raise ValueError("nested too deeply to be read") from None


def read_json(path, **kwargs):
    """read_text, then loads: no link, no FIFO, bounded, and malformed or too deep is ValueError."""
    return loads(read_text(path, **kwargs))


def write_text(path, text, *, group_write=True) -> None:
    """Replace the file's content atomically (a temporary file of this process beside it, then a rename);
    group_write as for open_file."""
    path = Path(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        os.unlink(tmp)                       # a stale one of a process that had this pid
    except OSError:
        pass
    fd = open_file(tmp, os.O_WRONLY | os.O_TRUNC, group_write=group_write)
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
    fd = open_file(path, os.O_WRONLY)
    try:                                 # through the descriptor: a link swapped in after the open is not followed
        try:
            os.utime(fd, None if when is None else (when, when))
        except PermissionError:
            os.utime(fd, None)
    finally:
        os.close(fd)


@contextmanager
def read_database(path, *, timeout=5.0):
    """An open, read-only connection to an instance database another account owns and writes (the service
    account's, read by the idle account in a two-account install: the idle pass, and SearXNG's start and stop).

    The database and its write-ahead log's two files are the writer's, and SQLite opens all three: a link, a FIFO
    (an open that never returns) or a device under any of the three names is refused with NotAFile before anything
    is opened (review L5, L4b); a missing database raises FileNotFoundError. Nothing of the writer's choosing runs
    here (review L5): no function a view or a trigger names is trusted (`trusted_schema=OFF`), and nothing is
    written (`mode=ro`, `query_only=ON`). A reader that may not write beside the database cannot create the
    write-ahead log's files. While a process has the database open they exist and the ordinary read-only open
    works; when they do not exist nobody has it open, so the file is complete as it stands and is read as it stands
    (immutable: no lock file is needed). What remains is a swap after the look and before SQLite's open, which needs
    a race; its worst is a read that waits for `timeout`. sqlite3.Error when the database cannot be read."""
    path = Path(path)
    for name in (str(path), f"{path}-wal", f"{path}-shm"):
        try:
            kind = os.lstat(name).st_mode
        except FileNotFoundError:
            if name == str(path):
                raise
            continue
        if not stat.S_ISREG(kind):
            raise NotAFile(f"{os.path.basename(name)} is not a plain file")
    if not os.access(path, os.R_OK):     # SQLite may reuse a descriptor this process holds: ask the kernel first
        raise PermissionError(f"{path.name} is not readable by this account")
    plain = f"file:{path}?mode=ro"
    attempts = [plain]
    if not os.path.exists(f"{path}-wal") and not os.access(path.parent, os.W_OK):
        attempts.append(f"{plain}&immutable=1")
    for n, uri in enumerate(attempts):
        db = sqlite3.connect(uri, uri=True, timeout=timeout)
        try:
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA query_only=ON")
            db.execute("SELECT count(*) FROM sqlite_schema").fetchone()      # opens the log: fails here if it must
        except sqlite3.OperationalError:
            db.close()
            if n + 1 == len(attempts):
                raise
            continue
        try:
            yield db
        finally:
            db.close()
        return


def table(db, name) -> bool:
    """Whether `name` is a table of `db` (a view or a trigger of the same name is not read in its place)."""
    row = db.execute("SELECT type FROM sqlite_schema WHERE name=?", (name,)).fetchone()
    return row is not None and row[0] == "table"


__all__ = ["NotAFile", "NotOwned", "PRIVATE_FILE", "PRIVATE_FOLDER", "READ_LIMIT", "SHARED_FILE",
           "SHARED_READ_ONLY_FILE", "ensure_folder", "file_mode",
           "is_shared", "loads", "open_file", "read_database", "read_json", "read_text", "table", "touch",
           "write_text", "writers"]
