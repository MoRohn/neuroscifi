"""ZIP and TAR to `Bundle` (lane B1); 7z and RAR identified only. A TAR inside gzip, bzip2, xz or zstd reaches the
TAR adapter through lane K's compressed-stream adapter, which inflates the stream and decodes the inner bytes.

Nothing is ever extracted to disk and no member name is ever joined to a filesystem path. Each member is listed
with its name (as stored, data only), its size, and a `Detected`; regular members are read into memory and decoded
recursively through `adapters.decode` with `nested()` limits (one level less of `max_depth`, the remaining
`max_decoded_bytes`, the remaining seconds). Per-member outcomes are in `Member.note`:

- refused as members to decode (listed, never read): names that are absolute, hold `..` or NUL; symbolic and hard
  links (the link target is recorded as text); device and FIFO entries; sparse TAR members (their holes would be
  materialised); encrypted ZIP members (`Refused` per member, not for the archive);
- refused while reading: a member that would pass the remaining `max_decoded_bytes` or `max_ratio`;
- not decoded: members nested deeper than `max_depth`;
- an `ArtifactError` from the member's own adapter is recorded in its note; the archive goes on.

More than `max_members` entries: the first `max_members` are listed and the result is partial. Each decoded member
is charged twice its size against `max_decoded_bytes` (its bytes, and its representation as an estimate). When an
archive holds Python files, one extra member `(python package)` carries their combined `Graph` (calls resolved
across files, see `code.py`). 7z and RAR: `Unsupported`, with the format version read from the signature block.
"""
from __future__ import annotations

import io
import posixpath
import tarfile
from dataclasses import replace

from ..safe import Deadline
from ..safe_b1 import DecodeBudget, ZipReader, unsafe_member_name
from ..types import ArtifactError, Decoded, Detected, Malformed, Member, Refused, Unsupported, Bundle
from . import Adapter, nested

NOTE_MAX = 300


def _cut(text: str, n: int = NOTE_MAX) -> str:
    return text if len(text) <= n else text[:n] + "…"


def _listed(name: str, container: str, why: str) -> Detected:
    from .. import detect as detect_mod
    try:
        found = detect_mod.detect(b"", name=posixpath.basename(name) or None)
        return Detected(found.family, found.format, found.media_type, found.basis, container, (why,))
    except Exception:
        return Detected("unknown", "unknown", "application/octet-stream", "none", container, (why,))


def _directory(container: str) -> Detected:
    return Detected("unknown", "directory", "inode/directory", "structure", container, ("the entry is a directory",))


class _Walk:
    """Shared bookkeeping of one archive decode."""

    def __init__(self, limits, container: str):
        self.limits = limits
        self.container = container
        self.deadline = Deadline(limits.max_seconds)
        self.budget = DecodeBudget(limits)
        self.members: list[Member] = []
        self.notes: list[str] = []
        self.partial = False
        self.python: dict[str, bytes] = {}
        self.counts = {"decoded": 0, "listed": 0, "refused": 0, "failed": 0}

    def listed(self, name: str, size: int, why: str, *, refused: bool = False) -> None:
        self.counts["refused" if refused else "listed"] += 1
        self.members.append(Member(name, int(max(0, size)), _listed(name, self.container, why), None, _cut(why)))

    def decode_member(self, name: str, data: bytes) -> None:
        from . import decode
        self.deadline.check()
        self.budget.take_bytes(len(data))
        if name.endswith(".py"):
            self.python[name] = data
        if self.limits.max_depth - 1 < 0:
            self.counts["listed"] += 1
            self.members.append(Member(name, len(data), _listed(name, self.container, "nested deeper than allowed"),
                                       None, "not decoded: containers nest deeper than max_depth"))
            return
        inner_limits = nested(self.limits, self.budget.bytes, self.deadline.elapsed())
        try:
            result = decode(data, name=posixpath.basename(name.rstrip("/")) or None, limits=inner_limits)
        except ArtifactError as error:
            self.counts["failed" if not isinstance(error, Refused) else "refused"] += 1
            self.members.append(Member(name, len(data), _listed(name, self.container, "decoding the member failed"),
                                       None, _cut(f"{error.reason}: {error}")))
            if error.reason == "timeout" and self.deadline.remaining() < 0:
                raise
            return
        self.budget.take_bytes(min(len(data), self.budget.bytes_left))
        self.counts["decoded"] += 1
        detected = replace(result.detected, container=result.detected.container or self.container)
        note = "; ".join(result.notes)
        if result.partial:
            note = "partial: " + note
        self.members.append(Member(name, len(data), detected, result.representation, _cut(note)))

    def finish(self, detected: Detected) -> Decoded:
        if self.python:
            self._python_graph()
        c = self.counts
        self.notes.insert(0, f"{len(self.members)} member(s): {c['decoded']} decoded, {c['listed']} listed only, "
                             f"{c['refused']} refused, {c['failed']} failed")
        self.notes.append("nothing was extracted to disk; member names are data and are never followed")
        return Decoded(detected, Bundle(tuple(self.members)), tuple(self.notes), self.partial)

    def _python_graph(self) -> None:
        try:
            from .code import python_package_graph
        except ImportError:
            return
        try:
            graph, notes = python_package_graph(self.python, self.limits)
        except ArtifactError as error:
            self.notes.append(f"the combined Python graph could not be built ({error.reason})")
            return
        size = sum(len(v) for v in self.python.values())
        det = Detected("code", "python-package", "text/x-python", "structure", self.container,
                       (f"{len(self.python)} Python member(s) read together",))
        self.members.append(Member("(python package)", size, det, graph,
                                   _cut("combined graph of the archive's Python files; " + "; ".join(notes))))


def read_zip(data: bytes, detected: Detected, limits) -> Decoded:
    walk = _Walk(limits, "zip")
    reader = ZipReader(data, max_members=limits.max_members, partial_ok=True)
    if reader.truncated:
        walk.partial = True
        walk.notes.append(f"the archive declares more than {limits.max_members} members: the first "
                          f"{len(reader.entries)} are listed")
    for entry in reader.entries:
        walk.deadline.check()
        if entry.is_dir:
            walk.members.append(Member(entry.name, 0, _directory("zip"), None, "directory"))
            continue
        unsafe = unsafe_member_name(entry.name)
        if unsafe:
            walk.listed(entry.name, entry.size, f"refused as a member to decode: {unsafe}", refused=True)
            continue
        if entry.is_link:
            walk.listed(entry.name, entry.size, "refused as a member to decode: a symbolic link (never followed)",
                        refused=True)
            continue
        if entry.encrypted:
            walk.listed(entry.name, entry.size, "refused: the member is encrypted and is not decrypted", refused=True)
            continue
        if not walk.budget.fits_bytes(1):
            walk.partial = True
            walk.notes.append("stopped: the decoded-bytes limit was reached")
            break
        try:
            body = reader.read(entry, limits=limits, out_limit=walk.budget.bytes_left, deadline=walk.deadline)
        except Refused as error:
            if error.reason == "timeout":
                raise
            walk.listed(entry.name, entry.size, f"refused: {error} (rule {error.rule})", refused=True)
            continue
        except (Unsupported, Malformed) as error:
            walk.counts["failed"] += 1
            walk.members.append(Member(entry.name, entry.size, _listed(entry.name, "zip", "not read"), None,
                                       _cut(f"{error.reason}: {error}")))
            continue
        walk.decode_member(entry.name, body)
    return walk.finish(detected)


def read_tar(data: bytes, detected: Detected, limits) -> Decoded:
    walk = _Walk(limits, "tar")
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:")
    except tarfile.TarError:
        raise Malformed("the bytes are not a readable TAR archive") from None
    count = 0
    while True:
        walk.deadline.check()
        try:
            info = tf.next()
        except tarfile.TarError:
            walk.partial = True
            walk.notes.append(f"a damaged header stopped the listing after {count} member(s)")
            break
        if info is None:
            break
        count += 1
        if count > limits.max_members:
            walk.partial = True
            walk.notes.append(f"stopped after {limits.max_members} members")
            break
        tf.members = []                         # tarfile keeps every header it reads; the walk keeps its own list
        name = info.name
        if info.isdir():
            walk.members.append(Member(name, 0, _directory("tar"), None, "directory"))
            continue
        unsafe = unsafe_member_name(name)
        if unsafe:
            walk.listed(name, info.size, f"refused as a member to decode: {unsafe}", refused=True)
            continue
        if info.issym() or info.islnk():
            kind = "symbolic" if info.issym() else "hard"
            walk.listed(name, info.size, f"refused as a member to decode: a {kind} link to "
                                         f"{_cut(info.linkname, 120)!r} (never followed)", refused=True)
            continue
        if info.issparse():
            walk.listed(name, info.size, "refused: a sparse member (its holes would be materialised)", refused=True)
            continue
        if not info.isreg():
            walk.listed(name, 0, "refused: a device, FIFO or other special entry", refused=True)
            continue
        if not walk.budget.fits_bytes(min(info.size, 1)):
            walk.partial = True
            walk.notes.append("stopped: the decoded-bytes limit was reached")
            break
        if info.size > walk.budget.bytes_left:
            walk.listed(name, info.size, f"refused: the member's {info.size} bytes pass the remaining decoded-bytes "
                                         "limit", refused=True)
            continue
        try:
            handle = tf.extractfile(info)
            body = handle.read(info.size) if handle is not None else b""
        except tarfile.TarError:
            body = b""
        if len(body) != info.size:
            walk.counts["failed"] += 1
            walk.members.append(Member(name, len(body), _listed(name, "tar", "truncated"), None,
                                       "malformed: the member is cut short by the end of the archive"))
            continue
        walk.decode_member(name, body)
    return walk.finish(detected)


def _unread(data: bytes, detected: Detected):
    if detected.format == "7z":
        version = f"{data[6]}.{data[7]}" if len(data) >= 8 else "unknown"
        error = Unsupported(f"7z archive (format version {version}) identified; nothing here reads 7z",
                            needs=("a 7z reader (for example py7zr), not installed",))
        error.facts = {"format": "7z", "version": version, "size": len(data)}
    else:
        version = "5" if data[6:8] == b"\x01\x00" else "1.5-4" if data[6:7] == b"\x00" else "unknown"
        error = Unsupported(f"RAR archive (format {version}) identified; nothing here reads RAR",
                            needs=("a RAR reader (for example rarfile with unrar), not installed",))
        error.facts = {"format": "rar", "version": version, "size": len(data)}
    raise error


class ZipAdapter(Adapter):
    name = "b1-zip"
    formats = ("zip", "jar", "apk")
    families = ("archive",)

    def decode(self, data, detected, limits):
        return read_zip(data, detected, limits)


class TarAdapter(Adapter):
    name = "b1-tar"
    formats = ("tar",)
    families = ("archive",)

    def decode(self, data, detected, limits):
        return read_tar(data, detected, limits)


class UnreadArchiveAdapter(Adapter):
    name = "b1-7z-rar"
    reads = "identity"
    formats = ("7z", "rar")
    families = ("archive",)

    def available(self):
        return True, "identifies 7z and RAR and says what is missing (no reader installed)"

    def decode(self, data, detected, limits):
        _unread(data, detected)


ADAPTERS = (ZipAdapter, TarAdapter, UnreadArchiveAdapter)

__all__ = ["ADAPTERS", "TarAdapter", "UnreadArchiveAdapter", "ZipAdapter", "read_tar", "read_zip"]
