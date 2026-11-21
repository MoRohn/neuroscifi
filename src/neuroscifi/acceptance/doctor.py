"""Environment doctor: runtime versions against the pins and, with a database, its on-disk safety.

Every check returns {"name", "status": "ok"|"warn"|"fail", "detail"}. The doctor opens the database
read-only (SQLite may still create its -wal/-shm sidecars, with the database file's permissions) and never
claims encryption without evidence from the kernel.
"""
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys

from ..instance.schema import SCHEMA_VERSION

FALLBACK_PINS = {"numpy": "2.3.5", "zstandard": "0.25.0"}
MIN_PYTHON = (3, 12)
MIN_SQLITE = (3, 24, 0)   # UPSERT (ON CONFLICT ... DO UPDATE) is used by the store
NETWORK_FILESYSTEMS = {"nfs", "nfs4", "cifs", "smb3", "smbfs", "9p", "ceph", "glusterfs", "fuse.sshfs", "afs"}


def _check(name, status, detail):
    return {"name": name, "status": status, "detail": detail}


def _pins():
    """Exact '==' pins of the runtime dependencies, from the installed metadata or the known pins."""
    try:
        requirements = metadata.requires("neuroscifi") or []
    except metadata.PackageNotFoundError:
        return dict(FALLBACK_PINS), "built-in pins (package metadata not found)"
    pins = {}
    for requirement in requirements:
        if ";" in requirement or "==" not in requirement:
            continue
        name, version = requirement.split("==", 1)
        pins[name.strip().lower()] = version.strip()
    return (pins or dict(FALLBACK_PINS)), "package metadata"


def runtime_checks():
    checks = []
    version = sys.version_info
    checks.append(_check("python", "ok" if version[:2] >= MIN_PYTHON else "fail",
                         f"Python {version.major}.{version.minor}.{version.micro}; requires >= "
                         f"{'.'.join(map(str, MIN_PYTHON))}"))
    pins, source = _pins()
    for name in ("numpy", "zstandard"):
        pinned = pins.get(name)
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            checks.append(_check(name, "fail", f"{name} is not installed (pinned {pinned}, from {source})"))
            continue
        status = "ok" if pinned is None or installed == pinned else "warn"
        checks.append(_check(name, status, f"installed {installed}; pinned {pinned} (from {source})"))
    sqlite_version = tuple(int(x) for x in sqlite3.sqlite_version.split("."))
    checks.append(_check("sqlite", "ok" if sqlite_version >= MIN_SQLITE else "fail",
                         f"SQLite {sqlite3.sqlite_version}; requires >= {'.'.join(map(str, MIN_SQLITE))}"))
    try:
        memory = sqlite3.connect(":memory:")
        try:
            valid = memory.execute("SELECT json_valid(?)", (json.dumps({"ok": True}),)).fetchone()[0]
        finally:
            memory.close()
        checks.append(_check("json", "ok" if valid == 1 else "fail",
                             "SQLite JSON functions available" if valid == 1 else "json_valid returned false"))
    except sqlite3.Error as error:
        checks.append(_check("json", "fail", f"SQLite JSON functions unavailable: {error}"))
    return checks


def _mode_check(path):
    files = [path] + [Path(str(path) + suffix) for suffix in ("-wal", "-shm") if Path(str(path) + suffix).exists()]
    worst, details = "ok", []
    for item in files:
        mode = stat.S_IMODE(item.stat().st_mode)
        details.append(f"{item.name} {mode:04o}")
        if mode & 0o007:
            worst = "fail"
        elif mode & 0o070 and worst == "ok":
            worst = "warn"
    advice = {"ok": "owner-only", "warn": "group can access; use chmod 600",
              "fail": "other users can access the individual; use chmod 600"}[worst]
    return _check("file_mode", worst, f"{', '.join(details)}: {advice}")


def _parent_check(path):
    parent = path.resolve().parent
    mode = stat.S_IMODE(parent.stat().st_mode)
    if mode & 0o004:
        return _check("parent_directory", "warn", f"{parent} is {mode:04o}: world-readable directory listing")
    return _check("parent_directory", "ok", f"{parent} is {mode:04o}")


def _mount(path):
    """The /proc/self/mountinfo entry with the longest mount point containing `path`, or None."""
    try:
        lines = Path("/proc/self/mountinfo").read_text().splitlines()
    except OSError:
        return None
    target = str(path.resolve())
    best = None
    for line in lines:
        left, _, right = line.partition(" - ")
        fields, tail = left.split(), right.split()
        if len(fields) < 5 or len(tail) < 2:
            continue
        point = fields[4].replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")
        inside = target == point or target.startswith(point.rstrip("/") + "/")
        if inside and (best is None or len(point) >= len(best["mount_point"])):
            best = {"mount_point": point, "device": fields[2], "fstype": tail[0], "source": tail[1]}
    return best


def _crypt_uuid(device, seen=None):
    """The dm uuid prefix 'CRYPT-...' of the device or of a device below it (LVM on LUKS), or None."""
    seen = seen or set()
    if device in seen:
        return None
    seen.add(device)
    base = Path("/sys/dev/block") / device
    try:
        uuid = (base / "dm" / "uuid").read_text().strip()
    except OSError:
        uuid = ""
    if uuid.startswith("CRYPT-"):
        return uuid
    try:
        slaves = list((base / "slaves").iterdir())
    except OSError:
        return None
    for slave in slaves:
        try:
            below = (slave / "dev").read_text().strip()
        except OSError:
            continue
        found = _crypt_uuid(below, seen)
        if found:
            return found
    return None


def _storage_checks(path):
    mount = _mount(path)
    if mount is None:
        return [_check("filesystem", "warn", "unknown: /proc/self/mountinfo is unavailable"),
                _check("encryption", "warn", "unknown: no mount information")]
    network = mount["fstype"] in NETWORK_FILESYSTEMS
    checks = [_check("filesystem", "warn" if network else "ok",
                     f"{mount['fstype']} at {mount['mount_point']} (source {mount['source']}, device "
                     f"{mount['device']})" + ("; SQLite WAL is unsafe on network filesystems" if network else ""))]
    device = mount["device"]
    if device.startswith("0:") and mount["source"].startswith("/dev/"):
        try:
            rdev = os.stat(mount["source"]).st_rdev
            device = f"{os.major(rdev)}:{os.minor(rdev)}"
        except OSError:
            pass
    uuid = None if device.startswith("0:") else _crypt_uuid(device)
    if uuid:
        kind = uuid.split("-")[1] if uuid.count("-") >= 1 else "CRYPT"
        checks.append(_check("encryption", "ok", f"backing device {device} is dm-crypt ({kind}) per "
                                                 f"/sys/dev/block/{device}/dm/uuid or a device below it"))
    else:
        checks.append(_check("encryption", "warn",
                             f"unknown: no dm-crypt evidence for device {device} ({mount['fstype']}); "
                             "encryption at rest is not confirmed"))
    return checks


def database_checks(path):
    path = Path(path)
    if not path.is_file():
        return [_check("database", "fail", f"no instance file at {path}")]
    checks = [_mode_check(path), _parent_check(path)]
    min_free = None
    try:
        db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            journal = db.execute("PRAGMA journal_mode").fetchone()[0]
            user_version = db.execute("PRAGMA user_version").fetchone()[0]
            integrity = db.execute("PRAGMA quick_check").fetchone()[0]
            config = db.execute("SELECT value FROM meta WHERE key='config'").fetchone()
            birth = db.execute("SELECT value FROM meta WHERE key='birth'").fetchone()
        finally:
            db.close()
    except sqlite3.Error as error:
        checks.append(_check("database", "fail", f"cannot read {path} as an instance: {error}"))
        return checks
    checks.append(_check("instance", "ok" if birth else "fail",
                         "birth record present" if birth else "no birth record: not a NeuroSciFi instance"))
    checks.append(_check("journal_mode", "ok" if journal == "wal" else "warn",
                         f"journal_mode={journal}" + ("" if journal == "wal" else "; opening with nsf sets WAL")))
    if user_version == SCHEMA_VERSION:
        checks.append(_check("schema_version", "ok", f"user_version {user_version} matches this software"))
    elif user_version < SCHEMA_VERSION:
        checks.append(_check("schema_version", "warn", f"user_version {user_version} < {SCHEMA_VERSION}; it will be "
                                                       "migrated forward when opened"))
    else:
        checks.append(_check("schema_version", "fail", f"user_version {user_version} > {SCHEMA_VERSION}: written by "
                                                       "newer software"))
    checks.append(_check("integrity", "ok" if integrity == "ok" else "fail", f"quick_check: {integrity}"))
    if config:
        try:
            min_free = int(json.loads(config[0])["min_free_bytes"])
        except (ValueError, KeyError, TypeError):
            min_free = None
    free = shutil.disk_usage(path.resolve().parent).free
    if min_free is None:
        checks.append(_check("free_space", "warn", f"{free} bytes free; no min_free_bytes in the instance config"))
    else:
        status = "fail" if free < min_free else "warn" if free < 2 * min_free else "ok"
        checks.append(_check("free_space", status, f"{free} bytes free; reserve {min_free} bytes"
                                                   + ("; writes are paused" if status == "fail" else "")))
    checks.extend(_storage_checks(path))
    checks.append(_cache_check(path))
    return checks


def _cache_check(path):
    """The public web cache next to the instance (<stem>.webcache.sqlite, ADR-023): absent is fine; a file that is
    not a readable SQLite database is reported (warn: the cache is disposable and is quarantined and rebuilt on
    its next use, but until then every page is fetched again)."""
    cache = path.resolve().with_name(f"{path.resolve().stem}.webcache.sqlite")
    if not cache.exists():
        return _check("public_cache", "ok", f"no public cache file ({cache.name}); it is created on first use")
    try:
        db = sqlite3.connect(f"{cache.as_uri()}?mode=ro", uri=True)
        try:
            integrity = db.execute("PRAGMA quick_check").fetchone()[0]
            db.execute("SELECT count(*) FROM urls").fetchone()
        finally:
            db.close()
    except sqlite3.Error as error:
        integrity = str(error)
    if integrity == "ok":
        return _check("public_cache", "ok", f"{cache.name}: quick_check ok")
    return _check("public_cache", "warn", f"{cache.name} is corrupt ({integrity[:120]}); it is disposable: the next "
                                          "research run renames it to .corrupt-<time> and starts an empty cache")


def run(db=None):
    checks = runtime_checks()
    if db is not None:
        checks.extend(database_checks(db))
    return {"checks": checks}


def failed(report):
    return any(check["status"] == "fail" for check in report["checks"])
