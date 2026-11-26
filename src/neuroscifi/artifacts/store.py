"""The artifact store (OMNIMIND DESIGN §4, ADR-071): bytes, index, links, audit, forget, restore.

Where things live
- The BYTES live outside the individual's file, in the sidecar `<stem>.artifacts.sqlite` next to it (the web cache's
  pattern, webcortex/cache.py): content-addressed by sha256, stored in rows of at most CHUNK bytes so that a read or an
  export streams with bounded memory, mode 0600 whatever the umask, WAL, `secure_delete` on, and a corrupt file is
  renamed aside (`<name>.corrupt-<time>`) and replaced by an empty one, whose missing bytes the index then shows. The
  sidecar is never part of a checkpoint. It also holds each artifact's TEXT LAYER (its own words as its decoder
  returned them, at most TEXT_MAX_BYTES of UTF-8) and a made artifact's SPECIFICATION (its editable source).
- The INDEX lives in the individual's file (schema 20): `artifacts` (one row per artifact: ids, the hash, the size,
  what was detected, origin, maker, the owner's label, a bounded JSON index holding describe() of the representation,
  the scope, the injection flag, the state), `artifact_links`, `artifact_events` (append-only), `artifact_forgotten`
  (payload-free tombstones) and `artifact_gaps` (how often a format ended undecoded).
- Identical bytes are stored once; the index rows that name a hash are its reference count. Bytes are removed when no
  row names their hash any more. The hash is verified on every read.

Rules
- Explicit owner commands only. Nothing here runs unasked, so the owner's quiet switch has nothing to hold
  (background.py); no artifact is reachable from a family or agent scope: every reader takes `viewer` and refuses
  anything but "owner".
- Intake (`add`): a regular file only (no FIFO, no device, no directory); a symbolic link is followed only when the
  caller says the owner named that path (`follow_symlinks=True`: `nsf artifact add` does, because the owner typed it;
  the default is to refuse, so a path found by a program is never followed). The file is read once, bounded by
  `artifact_max_bytes` before anything is allocated beyond it; over a cap is a named refusal, never a partial write.
  The write regulator's free-space reserve applies (instance store.require_write_allowed) and the free space must
  also hold the file. Decoding runs under `Limits` derived from the settings. A decoder that fails or knows nothing of
  the format still leaves the artifact stored with what was established (the owner gave it); a SAFETY refusal
  (`Refused`: pickle, nesting, resources) stores nothing.
- The text layer passes `webcortex.safety.scan_injection`; a flagged artifact is stored and marked `flagged`, and
  `text_for_model` refuses its text: the gate a later wave that shows artifact text to a model must go through.
- Made artifacts (`add_made`) have origin 'made', a maker (generator@version), their spec as the editable source and
  a `derived_from` link to every source artifact. They are never evidence (provenance.MADE, ADR-071) and every
  listing labels them provenance.MADE_NOT_EVIDENCE.
- Forget (`forget_artifact`): the artifact and every artifact made from it or contained in it, transitively: index
  rows, links, events, text layers, specifications, and the bytes no remaining row names; a tombstone each; one
  deletion_log row (kind 'artifact', target '#<digest>'); the state version; checkpoints written since the oldest of
  them become stale (the CLI then rewrites them in place: the kind is in checkpoints.REWRITABLE_KINDS).
- Forget of a root (governance.forget_partner / forget_camera): `scan_root` runs OUTSIDE the write transaction and
  finds every artifact whose label, index, text layer, specification or bytes name the root (raw bytes as UTF-8,
  UTF-16LE and UTF-16BE, base64 of it, and the same inside every gzip, zip (deflate), zlib (PNG text chunks, PDF
  streams), bzip2, xz and Zstandard stream found in them, nested up to Limits.max_depth, within Limits'
  decompressed-size budget). `forget_found` runs INSIDE the forget's transaction: it scans what was added since,
  then removes. What the scan cannot find: text drawn as pixels (no OCR exists here), words split by a format's own
  structure (a PDF text operator per letter), encodings it does not undo (UTF-32, quoted-printable, base64 broken
  across lines, encrypted members, LZW), anything past the decompressed-size budget (counted in the result).
- Restore (checkpoints.restore): `replay` removes from the archive's copy every artifact the live file has forgotten
  since; `after_restore` gives the restored file its own sidecar with the bytes, text layers and specifications its
  remaining index rows name (copied from the live sidecar), marks the rows whose bytes are gone 'missing', and sweeps
  anything no row names. `reconcile` does the same check for the live store (with a grace period for an add in
  flight).
"""
from __future__ import annotations

import base64
import bz2
import hashlib
import json
import lzma
import os
import re
import secrets
import shutil
import sqlite3
import stat
import struct
import threading
import time
import zlib
from dataclasses import dataclass, field, replace
from pathlib import Path

from .. import provenance
from ..instance.errors import NotFound
from . import adapters
from .detect import detect
from .types import ArtifactError, Limits, Malformed, Refused, bounded

CHUNK = 1 << 20                  # bytes per sidecar row
TEXT_MAX_BYTES = 256 << 10       # the text layer, UTF-8
INDEX_MAX_BYTES = 32 << 10       # the JSON index in the individual's file
LABEL_MAX = 255
ID_PREFIX = "artifact-"
ORIGINS = ("given", "fetched", "made")
VIEWERS = ("owner", "family", "agent")
RELATIONS = ("derived_from", "contains", "describes", "same_subject", "same_time", "consistent_with", "contradicts")
DST_KINDS = ("artifact",)        # links to other records come with cross-modal binding (a later wave)
DELETION_KIND = "artifact"       # deletion_log.target_kind of an artifact forget
SIDECAR_SUFFIX = ".artifacts.sqlite"
SWEEP_GRACE = 3600.0             # reconcile leaves sidecar rows younger than this alone (an add may be in flight)
GIVEN_LABEL = "given by the owner"
MADE_LABEL = provenance.MADE_NOT_EVIDENCE
_ID = re.compile(r"artifact-[0-9a-f]{32}")
_HOW = re.compile(r"[a-z0-9_.:=,;/+-]{1,200}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f‎‏‪-‮⁦-⁩]")
_WORD = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
EXPAND_BUFFER = 64 << 20         # the most of one decompressed stream kept to look inside it again

SIDECAR_SCHEMA = """
CREATE TABLE IF NOT EXISTS blobs(sha256 TEXT PRIMARY KEY, size INTEGER NOT NULL, chunks INTEGER NOT NULL,
  stored_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS chunks(sha256 TEXT NOT NULL, n INTEGER NOT NULL, data BLOB NOT NULL, PRIMARY KEY(sha256, n));
CREATE TABLE IF NOT EXISTS layers(artifact_id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('text','spec')),
  body TEXT NOT NULL, stored_at REAL NOT NULL, PRIMARY KEY(artifact_id, kind));
"""


# ---------------------------------------------------------------------------------------------------------------
# Errors and records


class StoreRefused(Refused):
    """The store said no (a cap, a scope, a file that is not a regular file, a flagged text). `rule` names it."""


class NotHeld(ArtifactError, NotFound):
    """No artifact of that id is held (never held, or forgotten), or its bytes are not in the store."""

    reason = "refused"


@dataclass(frozen=True)
class ArtifactRecord:
    id: str
    sha256: str
    size: int
    family: str
    format: str
    media_type: str
    origin: str
    maker: str | None
    label: str
    scope: str
    flagged: bool
    created_at: float
    state: str
    index: dict = field(default_factory=dict)

    @property
    def provenance(self) -> str:
        return MADE_LABEL if self.origin == "made" else GIVEN_LABEL

    def to_dict(self) -> dict:
        return {"id": self.id, "sha256": self.sha256, "size": self.size, "family": self.family,
                "format": self.format, "media_type": self.media_type, "origin": self.origin, "maker": self.maker,
                "label": self.label, "scope": self.scope, "flagged": self.flagged, "created_at": self.created_at,
                "state": self.state, "provenance": self.provenance, "index": self.index}


_COLUMNS = ("id", "sha256", "size", "family", "format", "media_type", "origin", "maker", "label", "scope", "flagged",
            "created_at", "state", "index_json")


def _record(row) -> ArtifactRecord:
    values = dict(zip(_COLUMNS, row))
    index = json.loads(values.pop("index_json"))
    values["flagged"] = bool(values["flagged"])
    return ArtifactRecord(**values, index=index)


# ---------------------------------------------------------------------------------------------------------------
# The sidecar


def is_corrupt(error) -> bool:
    if not isinstance(error, sqlite3.DatabaseError):
        return False
    text = str(error).lower()
    return type(error) is sqlite3.DatabaseError or "malformed" in text or "not a database" in text


class Sidecar:
    """The byte store beside one individual. One connection, guarded by a lock; a corrupt file is quarantined."""

    def __init__(self, path):
        self.path = Path(path)
        self.events: list[dict] = []
        self._lock = threading.Lock()
        try:
            self._open()
        except sqlite3.DatabaseError as error:
            if not is_corrupt(error):
                raise
            self._quarantine(error)
            self._open()

    def _open(self):
        old = os.umask(0o077)
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            os.close(fd)
            os.chmod(self.path, 0o600)
            self.db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False, timeout=30)
            try:
                self.db.execute("PRAGMA journal_mode=WAL")
                self.db.execute("PRAGMA journal_size_limit=0")
                self.db.execute("PRAGMA synchronous=NORMAL")
                self.db.execute("PRAGMA secure_delete=ON")
                self.db.executescript(SIDECAR_SCHEMA)
                self.db.execute("SELECT count(*) FROM blobs").fetchone()
            except BaseException:
                self.db.close()
                raise
        finally:
            os.umask(old)

    def _quarantine(self, error):
        stamp = int(time.time())
        target = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
        n = 1
        while target.exists():
            target = self.path.with_name(f"{self.path.name}.corrupt-{stamp}-{n}")
            n += 1
        for suffix in ("", "-wal", "-shm"):
            source = Path(str(self.path) + suffix)
            if source.exists():
                os.replace(source, Path(str(target) + suffix))
        self.events.append({"quarantined_as": str(target), "error": type(error).__name__})

    def call(self, fn):
        """fn(connection) under the lock; a corrupt file is quarantined, rebuilt empty and fn retried once."""
        with self._lock:
            try:
                return fn(self.db)
            except sqlite3.DatabaseError as error:
                if not is_corrupt(error):
                    raise
                try:
                    self.db.close()
                except sqlite3.Error:
                    pass
                self._quarantine(error)
                self._open()
                return fn(self.db)

    def close(self):
        with self._lock:
            self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- bytes ----------------------------------------------------------------------------------------------
    def put(self, data: bytes, sha: str, now: float, *, cap: int, layers=()) -> bool:
        """Store `data` under `sha` unless held, with the artifact's layers [(artifact_id, kind, body)]. Refuses
        (StoreRefused, nothing written) when the store would then hold more than `cap` bytes. Returns True when the
        bytes were new."""
        def put(db):
            db.execute("BEGIN IMMEDIATE")
            try:
                new = db.execute("SELECT 1 FROM blobs WHERE sha256=?", (sha,)).fetchone() is None
                if new:
                    held = db.execute("SELECT coalesce(sum(size),0) FROM blobs").fetchone()[0]
                    if held + len(data) > cap:
                        raise StoreRefused(f"the artifact store holds {held} bytes; {len(data)} more would pass its "
                                           f"limit of {cap} (nsf config --artifact-store-max-mib)",
                                           rule="artifact_store_max_bytes", reason="too_large")
                    view = memoryview(data)
                    n = 0
                    for n, start in enumerate(range(0, len(data), CHUNK)):
                        db.execute("INSERT INTO chunks(sha256,n,data) VALUES(?,?,?)", (sha, n, view[start:start + CHUNK]))
                    db.execute("INSERT INTO blobs(sha256,size,chunks,stored_at) VALUES(?,?,?,?)",
                               (sha, len(data), (len(data) + CHUNK - 1) // CHUNK, now))
                db.executemany("INSERT OR REPLACE INTO layers(artifact_id,kind,body,stored_at) VALUES(?,?,?,?)",
                               [(i, k, b, now) for i, k, b in layers])
                db.execute("COMMIT")
                return new
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        return self.call(put)

    def has(self, sha: str) -> bool:
        return self.call(lambda db: db.execute("SELECT 1 FROM blobs WHERE sha256=?", (sha,)).fetchone()) is not None

    def chunks(self, sha: str):
        """The stored chunks of `sha` in order, one row at a time (bounded memory). Raises NotHeld when absent."""
        row = self.call(lambda db: db.execute("SELECT size, chunks FROM blobs WHERE sha256=?", (sha,)).fetchone())
        if row is None:
            raise NotHeld("the bytes of this artifact are not in the store (forgotten, or the store was replaced)")
        size, count = row
        for n in range(count):
            part = self.call(lambda db, n=n: db.execute("SELECT data FROM chunks WHERE sha256=? AND n=?",
                                                        (sha, n)).fetchone())
            if part is None:
                raise Malformed("the stored bytes of this artifact are incomplete")
            yield bytes(part[0])

    def read(self, sha: str, size: int) -> bytes:
        """All the bytes of `sha`, verified against the hash (Malformed on a mismatch)."""
        digest, out = hashlib.sha256(), bytearray()
        for part in self.chunks(sha):
            if len(out) + len(part) > size:
                raise Malformed("the stored bytes of this artifact are longer than recorded")
            digest.update(part)
            out += part
        if len(out) != size or digest.hexdigest() != sha:
            raise Malformed("the stored bytes of this artifact no longer match their hash")
        return bytes(out)

    def layer(self, artifact_id: str, kind: str) -> str | None:
        row = self.call(lambda db: db.execute("SELECT body FROM layers WHERE artifact_id=? AND kind=?",
                                              (artifact_id, kind)).fetchone())
        return row[0] if row else None

    def layers_of(self, artifact_id: str) -> list[str]:
        return [b for (b,) in self.call(lambda db: db.execute("SELECT body FROM layers WHERE artifact_id=?",
                                                              (artifact_id,)).fetchall())]

    def drop(self, shas=(), ids=()) -> dict:
        """Delete the bytes of `shas` and the layers of `ids`, then fold the WAL back so nothing lingers there."""
        def drop(db):
            db.execute("BEGIN IMMEDIATE")
            try:
                blobs = 0
                for sha in shas:
                    db.execute("DELETE FROM chunks WHERE sha256=?", (sha,))
                    blobs += db.execute("DELETE FROM blobs WHERE sha256=?", (sha,)).rowcount
                layers = sum(db.execute("DELETE FROM layers WHERE artifact_id=?", (i,)).rowcount for i in ids)
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
            return {"blobs": blobs, "layers": layers}
        out = self.call(drop)
        self.checkpoint()
        return out

    def checkpoint(self) -> str:
        busy = self.call(lambda db: db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0])
        return "busy" if busy else "ok"

    def inventory(self) -> tuple[dict, dict]:
        """({sha: stored_at}, {(artifact_id, kind): stored_at})."""
        blobs = dict(self.call(lambda db: db.execute("SELECT sha256, stored_at FROM blobs").fetchall()))
        layers = {(i, k): t for i, k, t in self.call(lambda db: db.execute(
            "SELECT artifact_id, kind, stored_at FROM layers").fetchall())}
        return blobs, layers

    def total(self) -> int:
        return self.call(lambda db: db.execute("SELECT coalesce(sum(size),0) FROM blobs").fetchone()[0])


def sidecar_path(db) -> Path | None:
    """`<stem>.artifacts.sqlite` next to the individual's file; None for an in-memory database."""
    from ..instance import store
    path = store.database_path(db)
    if not path:
        return None
    path = Path(path)
    return path.with_name(path.stem + SIDECAR_SUFFIX)


def _sidecar(db, *, create=True) -> Sidecar | None:
    path = sidecar_path(db)
    if path is None:
        if create:
            raise StoreRefused("an individual held only in memory has no artifact store", rule="no_file",
                               reason="missing_capability")
        return None
    if not create and not path.exists():
        return None
    return Sidecar(path)


# ---------------------------------------------------------------------------------------------------------------
# Small helpers


def _now(now):
    return time.time() if now is None else float(now)


def present(db) -> bool:
    """Whether this file has the artifact index (schema 20)."""
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='artifacts'").fetchone() is not None


def digest(artifact_id: str, instance_id: str) -> str:
    """The payload-free tombstone of a forgotten artifact (instance-salted, as governance.root_digest)."""
    return hashlib.sha256(f"{instance_id}:{artifact_id}".encode()).hexdigest()[:16]


def _instance_id(db) -> str:
    from ..instance import store
    return store.birth(db)["instance_id"]


def _viewer(viewer):
    if viewer != "owner":
        raise StoreRefused("artifacts are the owner's: no family or agent scope may read them", rule="scope")


def _label(name) -> str:
    if not isinstance(name, str) or not name.strip():
        raise Malformed("an artifact needs a name (its label)")
    label = _CONTROL.sub(" ", name).strip()
    return (label[:LABEL_MAX - 1] + "…") if len(label) > LABEL_MAX else label


def _text_layer(text: str) -> tuple[str, bool]:
    raw = (text or "").encode("utf-8", "replace")
    if len(raw) <= TEXT_MAX_BYTES:
        return raw.decode("utf-8"), False
    return raw[:TEXT_MAX_BYTES].decode("utf-8", "ignore"), True


def limits(cfg) -> Limits:
    """The decode limits the owner's settings give (DESIGN §2 defaults, input size from artifact_max_bytes)."""
    return replace(Limits(), max_bytes=int(cfg.artifact_max_bytes))


def apply_adapter_setting(approved) -> list[str]:
    """Load the external adapters the owner approved (InstanceConfig.artifact_adapters) and unload any loaded one the
    owner no longer approves, so a change of the setting acts on the next call. Returns the names loaded now."""
    approved = {str(n) for n in approved}
    loaded = adapters.load_external(approved)
    for name, state in list(adapters._EXTERNAL.items()):      # lane K's registry has no public unload yet
        if state.get("state") == "loaded" and name not in approved:
            adapters.unregister(state.get("adapter", ""))
            adapters._EXTERNAL[name] = {"state": "found", "reason": "found, not approved"}
    return loaded


def _decode(data: bytes, *, name, declared, lim: Limits):
    """(decoded or None, error or None, [(format, outcome)] gaps) for these bytes. A safety refusal propagates."""
    gaps, me = [], threading.get_ident()

    def hook(detected, outcome):
        if threading.get_ident() == me:
            gaps.append((detected.format, outcome))
    remove = adapters.on_gap(hook)
    try:
        try:
            return adapters.decode(data, name=name, declared=declared, limits=lim), None, gaps
        except Refused:
            raise
        except ArtifactError as error:
            return None, {"reason": error.reason, "message": str(error)[:300],
                          "needs": list(getattr(error, "needs", ()))[:16]}, gaps
    finally:
        remove()


def _index(detected, decoded, error, *, text_chars, text_truncated, injection, extra=None) -> tuple[dict, str]:
    """The bounded JSON index of an artifact (<= INDEX_MAX_BYTES) and its compact serialisation."""
    out = {"detected": {"family": detected.family, "format": detected.format, "media_type": detected.media_type,
                        "basis": detected.basis, "container": detected.container,
                        "reasons": list(detected.reasons)[:16]},
           "text_chars": text_chars, "text_truncated": text_truncated, "injection": injection}
    if decoded is not None:
        try:
            description = decoded.representation.describe()
        except Exception as error:      # a representation that cannot describe itself is recorded as such
            description = {"kind": decoded.representation.kind, "error": type(error).__name__}
        out |= {"representation": description, "notes": list(decoded.notes)[:32], "partial": bool(decoded.partial)}
    if error is not None:
        out["error"] = error
    if extra:
        out |= extra
    out = bounded(out)
    text = json.dumps(out, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(text.encode("utf-8")) > INDEX_MAX_BYTES:
        out = {"detected": out.get("detected"), "truncated": True}
        text = json.dumps(bounded(out), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return out, text


def _injection(text: str) -> dict:
    if not text:
        return {"flagged": False, "patterns": []}
    from ..webcortex.safety import scan_injection
    patterns = sorted({f["pattern"] for f in scan_injection(text)})
    return {"flagged": bool(patterns), "patterns": patterns[:16]}


def _read_file(path, cap: int, *, follow_symlinks: bool) -> bytes:
    """The bytes of a regular file, read once and bounded by `cap` (StoreRefused over it, or for anything that is
    not a regular file). Checks are made on the opened descriptor, so a file swapped after a check is not read."""
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | (0 if follow_symlinks else os.O_NOFOLLOW)
    try:
        fd = os.open(path, flags)
    except OSError as error:
        if not follow_symlinks and os.path.islink(path):
            raise StoreRefused("a symbolic link is followed only when you name it yourself", rule="symlink") from None
        raise StoreRefused(f"the file cannot be opened ({type(error).__name__})", rule="unreadable") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise StoreRefused("only a regular file is taken (not a directory, a pipe or a device)",
                               rule="not_a_regular_file")
        if info.st_size > cap:
            raise StoreRefused(f"the file has {info.st_size} bytes, over the limit of {cap} "
                               "(nsf config --artifact-max-mib)", rule="artifact_max_bytes", reason="too_large")
        out = bytearray()
        while len(out) <= cap:
            part = os.read(fd, min(CHUNK, cap + 1 - len(out)))
            if not part:
                break
            out += part
        if len(out) > cap:
            raise StoreRefused(f"the file grew past the limit of {cap} bytes while it was read",
                               rule="artifact_max_bytes", reason="too_large")
        return bytes(out)
    finally:
        os.close(fd)


def _room(db, path_hint, size: int):
    """The instance's write regulator, and room for `size` more bytes above the free-space reserve."""
    from ..instance import store
    from ..instance.errors import WritesPaused
    store.require_write_allowed(db)
    if path_hint is not None:
        free = shutil.disk_usage(Path(path_hint).resolve().parent).free
        reserve = store.config(db).min_free_bytes
        if free - size < reserve:
            raise WritesPaused(f"storing {size} bytes would leave {free - size} free bytes, below the "
                               f"{reserve}-byte reserve")


def _event(db, artifact_id, action, now, *, sha=None, size=None, kind=None):
    db.execute("INSERT INTO artifact_events(artifact_id,action,sha256,size,kind,at) VALUES(?,?,?,?,?,?)",
               (artifact_id, action, sha, size, kind, now))


def _count_gaps(db, gaps, now):
    for fmt, outcome in gaps:
        db.execute("INSERT INTO artifact_gaps(format,outcome,count,first_at,last_at) VALUES(?,?,1,?,?) "
                   "ON CONFLICT(format,outcome) DO UPDATE SET count=count+1, last_at=excluded.last_at",
                   (str(fmt)[:64] or "unknown", outcome, now, now))


# ---------------------------------------------------------------------------------------------------------------
# Intake


def _store(db, data: bytes, *, label, origin, maker, scope, name_hint, declared, now, extra_index=None,
           extra_layers=(), links=(), action="added") -> ArtifactRecord:
    from ..instance import store
    cfg = store.config(db)
    if len(data) > cfg.artifact_max_bytes:
        raise StoreRefused(f"{len(data)} bytes is over the limit of {cfg.artifact_max_bytes} per artifact "
                           "(nsf config --artifact-max-mib)", rule="artifact_max_bytes", reason="too_large")
    path = sidecar_path(db)
    if path is None:
        raise StoreRefused("an individual held only in memory has no artifact store", rule="no_file",
                           reason="missing_capability")
    _room(db, path, len(data))
    apply_adapter_setting(cfg.artifact_adapters)
    decoded, error, gaps = _decode(data, name=name_hint, declared=declared, lim=limits(cfg))
    detected = decoded.detected if decoded is not None else detect(data[:64 << 10], name=name_hint,
                                                                   declared=declared, size=len(data))
    text = ""
    if decoded is not None:
        try:
            text = decoded.representation.text() or ""
        except Exception:
            text = ""
    text, truncated = _text_layer(text)
    injection = _injection(text)
    index, index_text = _index(detected, decoded, error, text_chars=len(text), text_truncated=truncated,
                               injection=injection, extra=extra_index)
    sha = hashlib.sha256(data).hexdigest()
    artifact_id = ID_PREFIX + secrets.token_hex(16)
    layers = ([(artifact_id, "text", text)] if text else []) + [(artifact_id, k, b) for k, b in extra_layers]
    with Sidecar(path) as side:
        side.put(data, sha, now, cap=cfg.artifact_store_max_bytes, layers=layers)
        try:
            with store.write(db):
                for _, dst in links:
                    if db.execute("SELECT 1 FROM artifacts WHERE id=?", (dst,)).fetchone() is None:
                        raise NotHeld("a source artifact is not held (forgotten meanwhile?)")
                db.execute(f"INSERT INTO artifacts({','.join(_COLUMNS)}) VALUES({','.join('?' * len(_COLUMNS))})",
                           (artifact_id, sha, len(data), detected.family, detected.format[:64],
                            detected.media_type[:128], origin, maker, label, scope, int(injection["flagged"]), now,
                            "stored", index_text))
                _event(db, artifact_id, action, now, sha=sha, size=len(data), kind=detected.format[:64])
                for relation, dst in links:
                    db.execute("INSERT OR IGNORE INTO artifact_links(src,relation,dst_kind,dst_id,how,confidence,"
                               "created_at) VALUES(?,?,?,?,?,?,?)",
                               (artifact_id, relation, "artifact", dst, "spec.sources", 1.0, now))
                _count_gaps(db, gaps, now)
                store.bump_state_version(db)
        except BaseException:
            # nothing names the bytes or layers now unless another artifact holds the same bytes
            held = db.execute("SELECT 1 FROM artifacts WHERE sha256=?", (sha,)).fetchone() is not None
            side.drop(shas=() if held else (sha,), ids=(artifact_id,))
            raise
    return get(db, artifact_id)


def add(db, source, *, name=None, origin="given", scope="owner", follow_symlinks=False, now=None) -> ArtifactRecord:
    """Keep a file the owner hands over. `source` is the bytes themselves (then `name` is required) or a path (then
    `name` defaults to its base name). Returns the stored record. Refusals are StoreRefused/Refused (nothing stored);
    a file nothing here can decode is stored with what was established."""
    now = _now(now)
    if origin != "given":
        raise StoreRefused("only 'given' artifacts are taken here ('made' ones through add_made; 'fetched' is "
                           "reserved for a later wave)", rule="origin")
    if scope != "owner":
        raise StoreRefused("artifacts are the owner's: no family or agent scope may add one", rule="scope")
    from ..instance import store
    cfg = store.config(db)
    if isinstance(source, (bytes, bytearray, memoryview)):
        if len(source) > cfg.artifact_max_bytes:
            raise StoreRefused(f"{len(source)} bytes is over the limit of {cfg.artifact_max_bytes} per artifact",
                               rule="artifact_max_bytes", reason="too_large")
        data, hint = bytes(source), name
    elif isinstance(source, (str, os.PathLike)):
        data = _read_file(os.fspath(source), cfg.artifact_max_bytes, follow_symlinks=follow_symlinks)
        hint = name if name is not None else Path(os.fspath(source)).name
    else:
        raise Malformed("give the bytes or a path")
    label = _label(hint)
    return _store(db, data, label=label, origin="given", maker=None, scope=scope, name_hint=label, declared=None,
                  now=now)


def add_made(db, made, spec, sources=(), *, now=None) -> ArtifactRecord:
    """Keep what the system made (artifacts.make.Made) with its specification as the editable source and a
    `derived_from` link to every source artifact. Origin 'made': never evidence (ADR-071)."""
    from .spec import Spec, canonical_json
    now = _now(now)
    if isinstance(spec, dict):
        spec = Spec.from_dict(spec)
    if not isinstance(spec, Spec):
        raise Malformed("a made artifact needs its specification (a Spec)")
    spec = Spec.from_dict(spec.to_dict())                     # validated again: nothing unchecked is kept
    data = getattr(made, "bytes", None)
    if not isinstance(data, bytes):
        raise Malformed("a made artifact needs its bytes")
    generator = str(spec.made_by.get("generator") or "")
    if not generator:
        raise Malformed("a made artifact's specification must name the generator that made it")
    sources = list(dict.fromkeys(str(s) for s in sources))
    for source in sources:
        if not _ID.fullmatch(source):
            raise Malformed("a source is an artifact id")
        if db.execute("SELECT 1 FROM artifacts WHERE id=?", (source,)).fetchone() is None:
            raise NotHeld("a source artifact is not held")
    named = [s for s in spec.sources if s != "request"]
    if set(named) - set(sources):
        raise Malformed("the specification names a source that is not among the sources given")
    text = canonical_json(spec.to_dict())
    maker = f"{generator}@{int(spec.made_by.get('version', 0))}"[:80]
    extra = {"spec": {"form": spec.spec, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                      "bytes": len(text.encode("utf-8"))},
             "made_notes": [str(n)[:200] for n in tuple(getattr(made, "notes", ()))[:16]]}
    return _store(db, data, label=_label(spec.title or spec.spec), origin="made", maker=maker, scope="owner",
                  name_hint=None, declared=str(getattr(made, "media_type", "") or "") or None, now=now,
                  extra_index=extra, extra_layers=[("spec", text)], links=[("derived_from", s) for s in sources],
                  action="made")


# ---------------------------------------------------------------------------------------------------------------
# Links


def link(db, src, relation, dst_kind, dst_id, how, confidence, *, now=None) -> dict:
    """Record a link from artifact `src` to `dst_id` of `dst_kind` ('artifact' in this wave). `how` states the basis
    in a short machine-readable form (lower-case letters, digits and `_.:=,;/+-`), never text from an artifact."""
    from ..instance import store
    now = _now(now)
    if relation not in RELATIONS:
        raise Malformed(f"relation must be one of {', '.join(RELATIONS)}")
    if dst_kind not in DST_KINDS:
        raise Malformed("in this wave an artifact links only to another artifact (links to other records come "
                        "with cross-modal binding)")
    if not isinstance(how, str) or not _HOW.fullmatch(how):
        raise Malformed("`how` is a short machine-readable statement (lower-case letters, digits and _.:=,;/+-)")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise Malformed("confidence is a number from 0 to 1")
    for value in (src, dst_id):
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise Malformed("both ends of a link are artifact ids")
    if src == dst_id:
        raise Malformed("an artifact does not link to itself")
    with store.write(db):
        for value in (src, dst_id):
            if db.execute("SELECT 1 FROM artifacts WHERE id=?", (value,)).fetchone() is None:
                raise NotHeld("no artifact of that id is held")
        added = db.execute("INSERT OR IGNORE INTO artifact_links(src,relation,dst_kind,dst_id,how,confidence,"
                           "created_at) VALUES(?,?,?,?,?,?,?)",
                           (src, relation, dst_kind, dst_id, how, float(confidence), now)).rowcount
        if added:
            _event(db, src, "linked", now, kind=relation)
            store.bump_state_version(db)
    return {"status": "linked" if added else "exists", "src": src, "relation": relation, "dst_kind": dst_kind,
            "dst_id": dst_id}


# ---------------------------------------------------------------------------------------------------------------
# Reading


def get(db, artifact_id, *, viewer="owner") -> ArtifactRecord:
    _viewer(viewer)
    row = db.execute(f"SELECT {','.join(_COLUMNS)} FROM artifacts WHERE id=?", (str(artifact_id),)).fetchone()
    if row is None:
        raise NotHeld("no artifact of that id is held")
    return _record(row)


def listing(db, *, viewer="owner", origin=None, limit=100) -> dict:
    """The artifacts held, newest first; each says what it is: given by the owner, or made by the system and not
    evidence."""
    _viewer(viewer)
    if origin is not None and origin not in ORIGINS:
        raise Malformed(f"origin must be one of {', '.join(ORIGINS)}")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10_000:
        raise Malformed("limit is a whole number from 1 to 10000")
    where, params = ("WHERE origin=?", (origin,)) if origin else ("", ())
    rows = db.execute(f"SELECT {','.join(_COLUMNS)} FROM artifacts {where} ORDER BY seq DESC LIMIT ?",
                      (*params, limit)).fetchall()
    counts = dict(db.execute("SELECT origin, count(*) FROM artifacts GROUP BY origin").fetchall())
    items = []
    for row in rows:
        record = _record(row)
        items.append({k: v for k, v in record.to_dict().items() if k != "index"}
                     | {"kind": (record.index.get("representation") or {}).get("kind", "unread")})
    return {"made_label": MADE_LABEL, "counts": {o: counts.get(o, 0) for o in ORIGINS}, "items": items}


def show(db, artifact_id, *, viewer="owner") -> dict:
    record = get(db, artifact_id, viewer=viewer)
    out = record.to_dict()
    out["links"] = [dict(zip(("src", "relation", "dst_kind", "dst_id", "how", "confidence"), r)) for r in db.execute(
        "SELECT src, relation, dst_kind, dst_id, how, confidence FROM artifact_links WHERE src=? OR "
        "(dst_kind='artifact' AND dst_id=?) ORDER BY created_at, src, relation", (record.id, record.id))]
    out["events"] = [dict(zip(("action", "sha256", "size", "kind", "at"), r)) for r in db.execute(
        "SELECT action, sha256, size, kind, at FROM artifact_events WHERE artifact_id=? ORDER BY id DESC LIMIT 50",
        (record.id,))]
    return out


def read_bytes(db, artifact_id, *, viewer="owner", now=None) -> bytes:
    """The artifact's bytes, verified against their hash. A mismatch marks the row 'corrupt' and raises Malformed."""
    record = get(db, artifact_id, viewer=viewer)
    side = _sidecar(db, create=False)
    if side is None:
        _mark(db, record, "missing", _now(now))
        raise NotHeld("the bytes of this artifact are not in the store")
    try:
        with side:
            return side.read(record.sha256, record.size)
    except Malformed:
        _mark(db, record, "corrupt", _now(now))
        raise
    except NotHeld:
        _mark(db, record, "missing", _now(now))
        raise


def _mark(db, record, state, now):
    from ..instance import store
    if record.state == state:
        return
    with store.write(db):
        db.execute("UPDATE artifacts SET state=? WHERE id=?", (state, record.id))
        _event(db, record.id, f"marked_{state}", now, sha=record.sha256)
        store.bump_state_version(db)


def text_for_model(db, artifact_id, *, viewer="owner") -> str:
    """The artifact's text layer, for a later wave that shows artifact text to a model: refused for a flagged
    artifact (its text held instruction-like content). Untrusted in every case: the caller still wraps it in the
    untrusted-data envelope."""
    record = get(db, artifact_id, viewer=viewer)
    if record.flagged:
        raise StoreRefused("this artifact's text was flagged as instructions aimed at an AI agent; it is never "
                           "offered to a model", rule="flagged")
    side = _sidecar(db, create=False)
    if side is None:
        return ""
    with side:
        return side.layer(record.id, "text") or ""


def spec_of(db, artifact_id, *, viewer="owner") -> dict | None:
    """A made artifact's specification (its editable source), or None for a given one."""
    record = get(db, artifact_id, viewer=viewer)
    side = _sidecar(db, create=False)
    if side is None or record.origin != "made":
        return None
    with side:
        text = side.layer(record.id, "spec")
    return json.loads(text) if text else None


def export(db, artifact_id, path, *, viewer="owner", now=None) -> dict:
    """Write the artifact's bytes to a NEW file at `path` (never overwrites), mode 0600, streamed in chunks and
    published only once the hash matched."""
    from ..instance import store
    now = _now(now)
    record = get(db, artifact_id, viewer=viewer)
    target = Path(path)
    if target.exists() or target.is_symlink():
        raise StoreRefused("export never overwrites: the target exists", rule="exists")
    if not target.parent.is_dir():
        raise StoreRefused("the target's folder does not exist", rule="no_folder")
    side = _sidecar(db, create=False)
    if side is None:
        raise NotHeld("the bytes of this artifact are not in the store")
    partial = target.with_name(f".nsf-partial-{secrets.token_hex(8)}")
    old = os.umask(0o077)
    try:
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    finally:
        os.umask(old)
    try:
        digest, size = hashlib.sha256(), 0
        with side, os.fdopen(fd, "wb") as out:
            for part in side.chunks(record.sha256):
                digest.update(part)
                size += len(part)
                if size > record.size:
                    break
                out.write(part)
            out.flush()
            os.fsync(out.fileno())
        if size != record.size or digest.hexdigest() != record.sha256:
            _mark(db, record, "corrupt", now)
            raise Malformed("the stored bytes of this artifact no longer match their hash; nothing was exported")
        os.chmod(partial, 0o600)
        try:
            os.link(partial, target)
        except FileExistsError:
            raise StoreRefused("export never overwrites: the target exists", rule="exists") from None
    finally:
        partial.unlink(missing_ok=True)
    with store.write(db):
        _event(db, record.id, "exported", now, sha=record.sha256, size=record.size)
    return {"status": "exported", "id": record.id, "bytes": record.size, "sha256": record.sha256,
            "path": str(target)}


def gaps(db) -> list[dict]:
    return [dict(zip(("format", "outcome", "count", "first_at", "last_at"), r)) for r in db.execute(
        "SELECT format, outcome, count, first_at, last_at FROM artifact_gaps ORDER BY count DESC, format, outcome")]


def adapter_catalog(db) -> dict:
    """adapters.catalog() after the owner's approvals are applied, with the approved names."""
    from ..instance import store
    approved = store.config(db).artifact_adapters
    apply_adapter_setting(approved)
    return {"approved": list(approved), "adapters": adapters.catalog()}


# ---------------------------------------------------------------------------------------------------------------
# Facts: measurements computed from the decoded representation, each with how it was computed


def _fact(name, value, how):
    return {"name": name, "value": value, "how": how}


def _numeric(values):
    import numpy as np
    arr = np.asarray(values, dtype=np.float64)
    finite = arr[np.isfinite(arr)]
    if not finite.size:
        return {"count": 0}
    return {"count": int(finite.size), "min": float(finite.min()), "max": float(finite.max()),
            "mean": float(finite.mean()), "std": float(finite.std())}


def facts_of(representation) -> list[dict]:
    """Generic measurements of one representation (types.py): counts, sizes, ranges, means of numeric columns,
    duration, bounds. No interpretation."""
    import numpy as np
    r, kind, out = representation, representation.kind, []
    if kind == "table":
        out.append(_fact("rows", r.n_rows, "number of rows the decoder read"))
        out.append(_fact("columns", len(r.columns), "number of columns"))
        for c in r.columns[:64]:
            entry = {"column": c.name[:120], "kind": c.kind, "missing": int(c.n_missing)}
            if c.kind in ("number", "integer", "time"):
                entry |= _numeric(c.values)
            elif c.kind == "text":
                entry["distinct"] = len(set(c.values))
            elif c.kind == "boolean":
                entry["true"] = int(np.nansum(np.asarray(c.values, dtype=np.float64) == 1))
            out.append(_fact("column", entry, "min, max, mean and population standard deviation over the finite "
                                              "values; distinct values for text; count of true for booleans"))
    elif kind == "signal":
        n, ch = r.samples.shape
        out += [_fact("samples", int(n), "samples per channel"), _fact("channels", int(ch), "channel count"),
                _fact("rate_hz", float(r.rate_hz), "declared sample rate"),
                _fact("duration_s", float(n / r.rate_hz), "samples divided by the sample rate")]
        if n:
            data = r.samples.astype(np.float64)
            out.append(_fact("peak", float(np.abs(data).max()), "largest absolute sample value (full scale is 1)"))
            out.append(_fact("rms", [float(v) for v in np.sqrt((data ** 2).mean(axis=0))[:32]],
                             "root mean square per channel"))
    elif kind == "raster":
        w, h = r.size
        out += [_fact("width", w, "pixels"), _fact("height", h, "pixels"), _fact("mode", r.mode, "decoded mode"),
                _fact("bits", int(r.bits), "bits per sample")]
        if r.pixels.size:
            flat = r.pixels.reshape(h * w, -1).astype(np.float64)
            out.append(_fact("channel_stats", [{"min": float(flat[:, i].min()), "max": float(flat[:, i].max()),
                                                "mean": float(flat[:, i].mean())} for i in range(flat.shape[1])],
                             "per channel over every pixel"))
    elif kind == "frames":
        out += [_fact("frames", len(r.frames), "frame count"),
                _fact("duration_s", float(sum(r.delays_s)), "sum of the frame delays")]
    elif kind == "drawing":
        out += [_fact("width", float(r.width), "declared"), _fact("height", float(r.height), "declared"),
                _fact("shapes", len(r.shapes), "shape count")]
    elif kind == "mesh":
        out += [_fact("vertices", len(r.vertices), "vertex count"), _fact("faces", len(r.faces), "triangle count"),
                _fact("closed", r.closed(), "every edge shared by exactly two triangles")]
        if len(r.vertices):
            out.append(_fact("bounds", {"min": [float(v) for v in r.vertices.min(axis=0)],
                                        "max": [float(v) for v in r.vertices.max(axis=0)]},
                             "smallest and largest coordinate per axis"))
    elif kind == "features":
        out.append(_fact("features", len(r.features), "feature count"))
        coords = [f.coordinates[:, :2] for f in r.features if len(f.coordinates)]
        if coords:
            allc = np.concatenate(coords)
            out.append(_fact("bounds", [float(allc[:, 0].min()), float(allc[:, 1].min()), float(allc[:, 0].max()),
                                        float(allc[:, 1].max())], "smallest and largest x and y of every coordinate"))
    elif kind in ("tree", "graph", "document", "bundle", "opaque"):
        description = r.describe()
        for key in ("n_nodes", "depth", "n_edges", "n_blocks", "text_chars", "n_members", "total_size", "size"):
            if key in description:
                out.append(_fact(key, description[key], "as the decoder's description counts it"))
        if kind == "document":
            words = len(r.text().split())
            out.append(_fact("words", words, "white-space separated words of the text the decoder recovered"))
        if kind == "opaque":
            out.append(_fact("findings", list(r.findings)[:16], "measured on the bytes; nothing was decoded"))
    return out


def facts(db, artifact_id, *, viewer="owner") -> dict:
    """What can be measured from the artifact's decoded representation (decoded again from the verified bytes under
    the current adapters and limits)."""
    from ..instance import store
    record = get(db, artifact_id, viewer=viewer)
    data = read_bytes(db, artifact_id, viewer=viewer)
    cfg = store.config(db)
    apply_adapter_setting(cfg.artifact_adapters)
    decoded, error, _ = _decode(data, name=record.label if record.origin == "given" else None,
                                declared=record.media_type if record.origin == "made" else None, lim=limits(cfg))
    out = {"id": record.id, "origin": record.origin, "provenance": record.provenance, "size": record.size,
           "format": record.format}
    if decoded is None:
        return out | {"decoded": False, "error": error,
                      "facts": [_fact("size", record.size, "bytes stored")]}
    return out | {"decoded": True, "kind": decoded.representation.kind, "partial": decoded.partial,
                  "notes": list(decoded.notes), "facts": facts_of(decoded.representation)}


# ---------------------------------------------------------------------------------------------------------------
# Forget


def _closure(db, ids) -> tuple[set, set]:
    """(ids, cascaded): `ids` plus every artifact made from one of them (derived_from) or contained in one of them
    (contains), transitively."""
    found, frontier = set(ids), list(ids)
    while frontier:
        part, frontier = frontier[:300], frontier[300:]
        marks = ",".join("?" * len(part))
        more = {r for (r,) in db.execute(
            f"SELECT src FROM artifact_links WHERE relation='derived_from' AND dst_kind='artifact' AND dst_id IN "
            f"({marks}) UNION SELECT dst_id FROM artifact_links WHERE relation='contains' AND dst_kind='artifact' "
            f"AND src IN ({marks})", part + part)} - found
        found |= more
        frontier += sorted(more)
    return found, found - set(ids)


def _remove(db, ids, reasons, now, instance_id) -> tuple[list, list, float | None]:
    """Delete these artifacts' rows, links and events and leave a tombstone each. Caller owns the transaction.
    `reasons`: {id: reason}. Returns (ids removed, their hashes, earliest created_at)."""
    ids = sorted(ids)
    removed, shas, earliest = [], [], None
    for start in range(0, len(ids), 300):
        part = ids[start:start + 300]
        marks = ",".join("?" * len(part))
        rows = db.execute(f"SELECT id, sha256, created_at FROM artifacts WHERE id IN ({marks})", part).fetchall()
        db.execute(f"DELETE FROM artifact_links WHERE src IN ({marks}) OR (dst_kind='artifact' AND dst_id IN "
                   f"({marks}))", part + part)
        db.execute(f"DELETE FROM artifacts WHERE id IN ({marks})", part)
        db.execute(f"DELETE FROM artifact_events WHERE artifact_id IN ({marks})", part)
        db.executemany("INSERT OR IGNORE INTO artifact_forgotten(digest,reason,forgotten_at) VALUES(?,?,?)",
                       [(digest(i, instance_id), reasons.get(i, "forgotten"), now) for i, _, _ in rows])
        removed += [r[0] for r in rows]
        shas += [r[1] for r in rows]
        if rows:
            first = min(r[2] for r in rows)
            earliest = first if earliest is None else min(earliest, first)
    return removed, shas, earliest


def _stale_checkpoints(db, since) -> list:
    """Valid checkpoints written at or after `since` (they may hold a removed row) become stale."""
    if since is None:
        return []
    stale = [i for (i,) in db.execute("SELECT id FROM checkpoints WHERE status='valid' AND created_at>=? ORDER BY id",
                                      (since,))]
    db.executemany("UPDATE checkpoints SET status='stale' WHERE id=?", [(i,) for i in stale])
    return stale


def _unreferenced(db, shas) -> list:
    return sorted(s for s in set(shas)
                  if db.execute("SELECT 1 FROM artifacts WHERE sha256=?", (s,)).fetchone() is None)


def _sweep_after(db, removed, shas) -> dict:
    """After a forget committed: the bytes no row names any more and the removed artifacts' layers leave the sidecar
    (secure_delete; the sidecar's WAL folded back)."""
    if not removed:
        return {}
    side = _sidecar(db, create=False)
    if side is None:
        return {}
    with side:
        dropped = side.drop(shas=_unreferenced(db, shas), ids=removed)
    return {"artifact_bytes_removed": dropped["blobs"]}


def forget_artifact(db, artifact_id, now=None) -> dict:
    """Forget one artifact and everything made from it or contained in it (see the module doc)."""
    from ..instance import governance, store
    now = _now(now)
    artifact_id = str(artifact_id)
    if not _ID.fullmatch(artifact_id):
        raise Malformed("an artifact id is 'artifact-' and 32 hexadecimal digits")
    instance_id = _instance_id(db)
    with store.write(db):
        if db.execute("SELECT 1 FROM artifacts WHERE id=?", (artifact_id,)).fetchone() is None:
            effects = None
        else:
            ids, cascaded = _closure(db, [artifact_id])
            reasons = {i: "source_forgotten" for i in cascaded} | {artifact_id: "forgotten"}
            removed, shas, earliest = _remove(db, ids, reasons, now, instance_id)
            effects = {"artifacts_removed": len(removed), "made_or_contained_removed": len(cascaded),
                       "checkpoints_stale": _stale_checkpoints(db, earliest),
                       "state_version": store.bump_state_version(db)}
            db.execute("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects) VALUES(?,?,?,?)",
                       (DELETION_KIND, "#" + digest(artifact_id, instance_id), now,
                        json.dumps(effects, sort_keys=True)))
    if effects is None:
        return {"status": "absent", "effects": {}}
    effects |= _sweep_after(db, removed, shas)
    return governance._result(db, effects)


# ---- forget of a root (governance.forget_partner / forget_camera) ------------------------------------------------


@dataclass
class RootScan:
    """What scan_root found, for forget_found to remove inside the forget's transaction."""

    root: str
    mark: int                       # the highest artifact seq the scan covered
    hits: list = field(default_factory=list)
    scanned: int = 0
    budget_reached: int = 0         # artifacts whose compressed streams were not all searched (budget)
    bytes_missing: int = 0          # artifacts searched without their bytes (label, index, layers only)
    removed: list = field(default_factory=list)
    shas: list = field(default_factory=list)
    cache: dict = field(default_factory=dict, repr=False)     # sha -> whether its bytes name the root


def _text_matcher(root):
    return re.compile(rf"(?<![\w-]){re.escape(root)}(?![\w-])")


def _strings(value, depth=0):
    if depth > 64:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _strings(v, depth + 1)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v, depth + 1)


def _b64_cores(needle: bytes) -> list[bytes]:
    """The base64 characters fully determined by `needle` at each of the three alignments (standard and URL-safe
    alphabets); empty when the needle is too short for a core of at least six characters."""
    out = []
    for k in range(3):
        encoded = base64.b64encode(b"\0" * k + needle)
        start, end = -(-8 * k // 6), (8 * (k + len(needle))) // 6
        core = encoded[start:end]
        if len(core) >= 6:
            out += [core, core.replace(b"+", b"-").replace(b"/", b"_")]
    return list(dict.fromkeys(out))


class _Needles:
    """The byte forms of one root, with the boundary rule of governance._scrubber (no word character or '-' on
    either side, ASCII; the base64 forms have no boundary)."""

    def __init__(self, root: str):
        self.forms = [(root.encode(enc), unit, enc) for enc, unit in
                      (("utf-8", 1), ("utf-16-le", 2), ("utf-16-be", 2))]
        self.b64 = _b64_cores(root.encode("utf-8"))
        self.longest = max([len(f) for f, _, _ in self.forms] + [len(c) for c in self.b64])

    @staticmethod
    def _word(buf, at, unit, enc):
        if at < 0 or at + unit > len(buf):
            return False
        if unit == 1:
            return buf[at] in _WORD
        ch = bytes(buf[at:at + 2]).decode(enc, "replace")
        return len(ch) == 1 and ord(ch) < 128 and ord(ch) in _WORD

    def found(self, buf) -> bool:
        for needle, unit, enc in self.forms:
            start = 0
            while (at := buf.find(needle, start)) != -1:
                if not self._word(buf, at - unit, unit, enc) and not self._word(buf, at + len(needle), unit, enc):
                    return True
                start = at + 1
        return any(buf.find(core) != -1 for core in self.b64)


def _inflate(decompressor, view, limit):
    out, pos, tail = bytearray(), 0, b""
    while len(out) < limit:
        if tail:
            data = tail
        elif pos < len(view):
            data, pos = view[pos:pos + CHUNK], pos + CHUNK
        else:
            break
        out += decompressor.decompress(data, limit - len(out))
        tail = decompressor.unconsumed_tail
        if decompressor.eof:
            return bytes(out), False
    return bytes(out), len(out) >= limit


def _unpack(decompressor, view, limit):
    out, pos = bytearray(), 0
    while len(out) < limit and not decompressor.eof:
        if decompressor.needs_input:
            if pos >= len(view):
                break
            data, pos = bytes(view[pos:pos + CHUNK]), pos + CHUNK
        else:
            data = b""
        out += decompressor.decompress(data, limit - len(out))
    return bytes(out), len(out) >= limit and not decompressor.eof


def _expand(kind, view, limit):
    """(decompressed bytes, cut short) of one stream starting at view[0]; (b"", False) when it is not one."""
    try:
        if kind == "gzip":
            return _inflate(zlib.decompressobj(31), view, limit)
        if kind == "deflate":
            return _inflate(zlib.decompressobj(-15), view, limit)
        if kind == "zlib":
            return _inflate(zlib.decompressobj(15), view, limit)
        if kind == "bzip2":
            return _unpack(bz2.BZ2Decompressor(), view, limit)
        if kind == "xz":
            return _unpack(lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=256 << 20), view, limit)
        if kind == "zstd":
            import zstandard
            reader = zstandard.ZstdDecompressor(max_window_size=1 << 27).stream_reader(view, read_across_frames=False)
            out = reader.read(limit)
            return out, len(out) >= limit and reader.read(1) != b""
    except Exception:       # hostile or accidental: not a stream of this kind
        return b"", False
    return b"", False


def _candidates(buf, max_members):
    """(offset of the compressed data, kind) for every stream a signature or a container header announces."""
    found = []

    def every(magic, kind, shift=0, check=None):
        start = 0
        while len(found) < max_members and (at := buf.find(magic, start)) != -1:
            if check is None or check(at):
                found.append((at + shift, kind))
            start = at + 1

    every(b"\x1f\x8b\x08", "gzip")
    every(b"\x28\xb5\x2f\xfd", "zstd")
    every(b"\xfd7zXZ\x00", "xz")
    every(b"BZh", "bzip2", check=lambda at: buf[at + 3:at + 4] in b"123456789" and buf[at + 4:at + 10] == b"1AY&SY")
    start = 0
    while len(found) < max_members and (at := buf.find(b"PK\x03\x04", start)) != -1:   # zip local file headers
        start = at + 1
        if at + 30 <= len(buf):
            method, = struct.unpack_from("<H", buf, at + 8)
            name_len, extra_len = struct.unpack_from("<HH", buf, at + 26)
            if method == 8:
                found.append((at + 30 + name_len + extra_len, "deflate"))
    if bytes(buf[:8]) == b"\x89PNG\r\n\x1a\n":                                         # PNG compressed text chunks
        at = 8
        while at + 12 <= len(buf) and len(found) < max_members:
            length, ctype = struct.unpack_from(">I4s", buf, at)
            body = at + 8
            if body + length > len(buf):
                break
            if ctype == b"zTXt":
                cut = buf.find(b"\0", body, body + length)
                if cut != -1:
                    found.append((cut + 2, "zlib"))
            elif ctype == b"iTXt":
                cut = buf.find(b"\0", body, body + length)
                if cut != -1 and buf[cut + 1:cut + 2] == b"\x01":
                    lang = buf.find(b"\0", cut + 3, body + length)
                    key = buf.find(b"\0", lang + 1, body + length) if lang != -1 else -1
                    if key != -1:
                        found.append((key + 1, "zlib"))
            at = body + length + 4
    start = 0
    while len(found) < max_members and (at := buf.find(b"stream", start)) != -1:      # PDF FlateDecode streams
        start = at + 1
        for eol in (b"\r\n", b"\n"):
            if bytes(buf[at + 6:at + 6 + len(eol)]) == eol:
                found.append((at + 6 + len(eol), "zlib"))
                break
    return found


def _search(buf, needles, budget, depth, max_depth, max_members) -> tuple[bool, bool]:
    """(found, budget reached): the needles in `buf` and in every compressed stream inside it, recursively."""
    if needles.found(buf):
        return True, False
    if depth >= max_depth:
        return False, False
    view, reached = memoryview(buf), False
    for offset, kind in _candidates(buf, max_members):
        if budget[0] <= 0:
            return False, True
        out, cut = _expand(kind, view[offset:], min(budget[0], EXPAND_BUFFER))
        budget[0] -= len(out)
        reached = reached or cut
        if out:
            hit, more = _search(out, needles, budget, depth + 1, max_depth, max_members)
            if hit:
                return True, False
            reached = reached or more
    return False, reached


def _artifact_names(db, side, row, matcher, needles, lim, cache, scan) -> bool:
    """Whether one artifact (row: seq, id, sha256, size, label, index_json) names the root anywhere."""
    _, artifact_id, sha, size, label, index_json = row
    if matcher.search(label):
        return True
    try:
        index = json.loads(index_json)
    except ValueError:
        index = index_json
    if any(matcher.search(s) for s in _strings(index)):
        return True
    if side is not None and any(matcher.search(body) for body in side.layers_of(artifact_id)):
        return True
    if sha not in cache:
        data = None
        if side is not None:
            try:
                data = side.read(sha, size)
            except ArtifactError:
                data = None
        if data is None:
            scan.bytes_missing += 1
            cache[sha] = False
        else:
            hit, reached = _search(data, needles, [lim.max_decoded_bytes], 0, lim.max_depth, lim.max_members)
            scan.budget_reached += int(reached)
            cache[sha] = hit
    return cache[sha]


def _scan(db, root, rows, scan, cache):
    from ..instance import store
    try:
        lim = limits(store.config(db))
    except Exception:
        lim = Limits()
    matcher, needles = _text_matcher(root), _Needles(root)
    side = _sidecar(db, create=False)
    try:
        for row in rows:
            scan.scanned += 1
            if _artifact_names(db, side, row, matcher, needles, lim, cache, scan):
                scan.hits.append(row[1])
    finally:
        if side is not None:
            side.close()


def scan_root(db, root) -> RootScan:
    """Find, OUTSIDE any write transaction, every artifact that names `root` (see the module doc)."""
    scan = RootScan(root=str(root), mark=0)
    if not present(db) or not root:
        return scan
    scan.mark = db.execute("SELECT coalesce(max(seq),0) FROM artifacts").fetchone()[0]
    rows = db.execute("SELECT seq, id, sha256, size, label, index_json FROM artifacts WHERE seq<=? ORDER BY seq",
                      (scan.mark,))
    _scan(db, scan.root, rows, scan, scan.cache)
    return scan


def forget_found(db, scan, now, effects, label) -> dict | None:
    """INSIDE the root forget's transaction: scan the artifacts added since `scan_root`, then remove every hit and
    what was made from or contained in it, with tombstones and one deletion_log row (kind 'artifact', target the
    root's label). Returns the forget's effects with "artifacts_removed" (absent while zero), or a new effects dict
    when the forget removed nothing else."""
    from ..instance import store
    if scan is None or not present(db):
        return effects
    later = db.execute("SELECT seq, id, sha256, size, label, index_json FROM artifacts WHERE seq>? ORDER BY seq",
                       (scan.mark,)).fetchall()
    if later:
        _scan(db, scan.root, later, scan, scan.cache)
    hits = [h for h in dict.fromkeys(scan.hits)
            if db.execute("SELECT 1 FROM artifacts WHERE id=?", (h,)).fetchone() is not None]
    if not hits:
        return effects
    ids, cascaded = _closure(db, hits)
    reasons = {i: "source_forgotten" for i in cascaded} | {h: "root_forgotten" for h in hits}
    removed, shas, earliest = _remove(db, ids, reasons, now, _instance_id(db))
    stale = _stale_checkpoints(db, earliest)
    scan.removed, scan.shas = removed, shas
    if effects is None:
        effects = {"state_version": store.bump_state_version(db), "checkpoints_stale": stale}
    else:
        effects["checkpoints_stale"] = sorted(set(effects.get("checkpoints_stale") or []) | set(stale))
    effects["artifacts_removed"] = len(removed)
    record = {"artifacts_removed": len(removed), "made_or_contained_removed": len(cascaded),
              "scanned": scan.scanned, "budget_reached": scan.budget_reached, "bytes_missing": scan.bytes_missing,
              "checkpoints_stale": stale, "state_version": effects["state_version"]}
    db.execute("INSERT INTO deletion_log(target_kind,target_id,requested_at,effects) VALUES(?,?,?,?)",
               (DELETION_KIND, label, now, json.dumps(record, sort_keys=True)))
    return effects


def finish(db, scan, result) -> dict:
    """After the root forget committed: the removed artifacts' bytes and layers leave the sidecar."""
    if scan is None or not scan.removed:
        return result
    swept = _sweep_after(db, scan.removed, scan.shas)
    if swept:
        result = result | {"effects": result.get("effects", {}) | swept}
    return result


# ---------------------------------------------------------------------------------------------------------------
# Restore and reconciliation


def read_forgotten(db) -> list:
    """The live file's artifact tombstones, for checkpoints._read_forgets."""
    if not present(db):
        return []
    return db.execute("SELECT digest, reason, forgotten_at FROM artifact_forgotten").fetchall()


def replay(copy, forgotten, now) -> int:
    """In a restore's or rewrite's copy: remove every artifact the live file forgot since the archive was written,
    and what was made from or contained in it; keep the tombstones. Caller owns the transaction. Returns how many."""
    if not present(copy):
        return 0
    copy.executemany("INSERT OR IGNORE INTO artifact_forgotten(digest,reason,forgotten_at) VALUES(?,?,?)", forgotten)
    if not forgotten:
        return 0
    gone, instance_id = {d for d, _, _ in forgotten}, _instance_id(copy)
    doomed = [i for (i,) in copy.execute("SELECT id FROM artifacts") if digest(i, instance_id) in gone]
    if not doomed:
        return 0
    ids, cascaded = _closure(copy, doomed)
    removed, _, _ = _remove(copy, ids, {i: "source_forgotten" for i in cascaded}, now, instance_id)
    return len(removed)


def _reconcile(db, side, now, grace) -> dict:
    """Mark index rows whose bytes are gone 'missing' (and back to 'stored' when they are there again), and sweep
    sidecar rows no index row names (older than `grace` seconds)."""
    from ..instance import store
    rows = db.execute("SELECT id, sha256, state FROM artifacts").fetchall()
    blobs, layers = side.inventory()
    ids, shas = {r[0] for r in rows}, {r[1] for r in rows}
    missing = [(i, s) for i, s, state in rows if s not in blobs and state != "missing"]
    back = [(i, s) for i, s, state in rows if s in blobs and state == "missing"]
    if missing or back:
        with store.write(db):
            for i, s in missing:
                db.execute("UPDATE artifacts SET state='missing' WHERE id=?", (i,))
                _event(db, i, "marked_missing", now, sha=s)
            for i, _ in back:
                db.execute("UPDATE artifacts SET state='stored' WHERE id=?", (i,))
            store.bump_state_version(db)
    orphan_blobs = [s for s, t in blobs.items() if s not in shas and t <= now - grace]
    orphan_layers = sorted({i for (i, _), t in layers.items() if i not in ids and t <= now - grace})
    swept = side.drop(shas=orphan_blobs, ids=orphan_layers) if orphan_blobs or orphan_layers else \
        {"blobs": 0, "layers": 0}
    return {"marked_missing": len(missing), "found_again": len(back), "bytes_swept": swept["blobs"],
            "layers_swept": swept["layers"]}


def reconcile(db, now=None, *, grace=SWEEP_GRACE) -> dict:
    """The live store's check: see _reconcile. An individual without artifacts and without a sidecar is left as it
    is (no file is created)."""
    now = _now(now)
    if not present(db):
        return {}
    side = _sidecar(db, create=False)
    if side is None:
        if db.execute("SELECT 1 FROM artifacts WHERE state!='missing' LIMIT 1").fetchone() is None:
            return {}
        side = _sidecar(db)
    with side:
        return _reconcile(db, side, now, grace)


def after_restore(live_db, copy, dest, now) -> dict:
    """Give a restored copy (connection `copy`, to be published at `dest`) its own sidecar: the bytes, text layers and
    specifications its remaining index rows name, copied from the live sidecar; rows whose bytes are not there are
    marked 'missing'; anything no row names is swept. {} (and no file) when the copy holds no artifact."""
    if not present(copy) or copy.execute("SELECT 1 FROM artifacts LIMIT 1").fetchone() is None:
        return {}
    dest = Path(dest)
    target = dest.with_name(dest.stem + SIDECAR_SUFFIX)
    live = sidecar_path(live_db)
    rows = copy.execute("SELECT id, sha256 FROM artifacts").fetchall()
    copied = 0
    with Sidecar(target) as side:
        if live is not None and live.exists() and live.resolve() != target.resolve():
            def fill(db):
                db.execute("ATTACH DATABASE ? AS live", (str(live),))
                try:
                    db.execute("BEGIN IMMEDIATE")
                    try:
                        db.execute("CREATE TEMP TABLE IF NOT EXISTS want_sha(sha256 TEXT PRIMARY KEY)")
                        db.execute("CREATE TEMP TABLE IF NOT EXISTS want_id(artifact_id TEXT PRIMARY KEY)")
                        db.execute("DELETE FROM want_sha")
                        db.execute("DELETE FROM want_id")
                        db.executemany("INSERT OR IGNORE INTO want_sha VALUES(?)", [(s,) for _, s in rows])
                        db.executemany("INSERT OR IGNORE INTO want_id VALUES(?)", [(i,) for i, _ in rows])
                        n = db.execute("INSERT OR IGNORE INTO main.blobs SELECT b.* FROM live.blobs b JOIN want_sha w "
                                       "ON w.sha256=b.sha256 WHERE b.sha256 NOT IN (SELECT sha256 FROM main.blobs)"
                                       ).rowcount
                        db.execute("INSERT OR IGNORE INTO main.chunks SELECT c.* FROM live.chunks c JOIN want_sha w "
                                   "ON w.sha256=c.sha256")
                        db.execute("INSERT OR IGNORE INTO main.layers SELECT l.* FROM live.layers l JOIN want_id w "
                                   "ON w.artifact_id=l.artifact_id")
                        db.execute("COMMIT")
                    except BaseException:
                        if db.in_transaction:
                            db.execute("ROLLBACK")
                        raise
                finally:
                    db.execute("DETACH DATABASE live")
                return n
            copied = side.call(fill)
        report = _reconcile(copy, side, now, grace=-1.0)
        side.checkpoint()
    return {"artifact_bytes_copied": copied, "artifacts_missing": report["marked_missing"]}


__all__ = ["ArtifactRecord", "CHUNK", "DELETION_KIND", "GIVEN_LABEL", "INDEX_MAX_BYTES", "MADE_LABEL", "NotHeld",
           "RELATIONS", "RootScan", "Sidecar", "StoreRefused", "TEXT_MAX_BYTES", "add", "add_made",
           "adapter_catalog", "after_restore", "apply_adapter_setting", "digest", "export", "facts", "facts_of",
           "finish", "forget_artifact", "forget_found", "gaps", "get", "limits", "link", "listing", "present",
           "read_bytes", "read_forgotten", "reconcile", "replay", "scan_root", "show", "sidecar_path", "spec_of",
           "text_for_model"]
