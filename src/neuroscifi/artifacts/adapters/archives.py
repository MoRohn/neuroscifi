"""ZIP and TAR to `Bundle` (lane B1); 7z and RAR identified only. A TAR inside gzip, bzip2, xz or zstd reaches the
TAR adapter through lane K's compressed-stream adapter, which inflates the stream and decodes the inner bytes.

Nothing is ever extracted to disk and no member name is ever joined to a filesystem path. Each member is listed
with its name (as stored, data only), its size, and a `Detected`; regular members are read into memory and decoded
recursively through `adapters.decode` with `nested()` limits (one level less of `max_depth`, the remaining
`max_decoded_bytes`, the remaining seconds). Per-member outcomes are in `Member.note`:

- the WHOLE archive is refused (`Refused(rule="unsafe_member_name")`, nothing inside decoded) when any member's
  name is absolute or holds `..` or NUL, or a member is a symbolic or hard link, a device, FIFO or other special
  entry: such a member is an attack on whoever extracts the archive, and the main session's ruling (integration,
  ADR-072) is that its siblings are not read either. The refusal names the first offending member (JSON-escaped,
  as data) and carries the listing of member names and sizes as `findings` (inspection, never content);
- the WHOLE archive is refused too (lane FX, item 2: the table `outcome.PART_ONLY_RULES` / `refuses_whole`) when
  any member is refused for any other reason than the three that concern that member only: a bomb-like member (the
  ratio rule, or a small member that would need more than the budget), a member that inflates to another size than
  the directory states or that the archive's bytes cut short (a lying size), a damaged stored member, a member
  nested deeper than `max_depth`, a member whose own reader refuses it (an entity, a DTD, an external reference,
  words addressed to an AI agent, ...). Its honest siblings are not answered; the listing is kept as findings;
- refused as members only (listed, never read; the archive goes on): sparse TAR members (their holes would be
  materialised); encrypted ZIP members (not an attack on the reader); a member that would pass what is left of
  `max_decoded_bytes` (a large archive);
- a `Malformed` or `Unsupported` from a member's own adapter is recorded in its note; the archive goes on.

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
from ..outcome import refuses_whole
from ..safe_b1 import DecodeBudget, ZipReader, unsafe_member_name
from ..types import ArtifactError, Decoded, Detected, Malformed, Member, Refused, Unsupported, Bundle
from . import Adapter, nested

NOTE_MAX = 300


def _cut(text: str, n: int = NOTE_MAX) -> str:
    return text if len(text) <= n else text[:n] + "…"


LISTING_MAX = 256          # member names and sizes carried by a whole-archive refusal


def _quoted(name: str) -> str:
    """A member name as data: JSON-escaped (control and non-ASCII characters escaped), cut to 120 characters."""
    import json
    return json.dumps(_cut(str(name), 120), ensure_ascii=True)


def refuse_unsafe(container: str, offending: list[tuple[str, str]], listing: list[tuple[str, int]]):
    """Raise the whole-archive refusal: `offending` is (name, why) of each unsafe member, `listing` (name, size) of
    every member seen. Nothing inside has been decoded."""
    name, why = offending[0]
    more = f" (and {len(offending) - 1} more unsafe member(s))" if len(offending) > 1 else ""
    # the member's name is the file's own text: it goes into the findings (the listing), never into the message
    error = Refused(f"the {container} archive is refused as a whole: a member {why}{more}; nothing inside was "
                    "decoded", rule="unsafe_member_name")
    error.findings = tuple(f"member {_quoted(n)}: {int(max(0, size))} bytes" for n, size in listing[:LISTING_MAX]) \
        + ((f"{len(listing) - LISTING_MAX} more member(s) not listed",) if len(listing) > LISTING_MAX else ())
    raise error


def refuse_whole(container: str, why: str, rule: str, listing: list[tuple[str, int]]):
    """Raise the whole-file refusal for a hostile part met inside (lane FX, item 2; `outcome.refuses_whole`): `why`
    says what was met (never a member's name: names are the file's own text and go into the findings only)."""
    error = Refused(f"the {container} file is refused as a whole: {why}; nothing inside is answered", rule=rule)
    error.findings = tuple(f"member {_quoted(n)}: {int(max(0, size))} bytes" for n, size in listing[:LISTING_MAX]) \
        + ((f"{len(listing) - LISTING_MAX} more member(s) not listed",) if len(listing) > LISTING_MAX else ())
    raise error


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

    def refuse(self, name: str, size: int, rule: str, why: str, *, remaining=()) -> None:
        """A member refused by `rule`: the whole archive when the outcome table says so, else this member only."""
        if refuses_whole(rule):
            listing = [(m.path, m.size) for m in self.members] + [(name, size)] + list(remaining)
            refuse_whole(self.container.upper(), f"a member was refused ({rule})", rule, listing)
        self.listed(name, size, why, refused=True)

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
            # the chain's own answer to this depth is Refused(rule="max_depth"): the member is refused, by the same rule
            self.refuse(name, len(data), "max_depth", "refused: containers nest deeper than max_depth (rule max_depth)")
            return
        inner_limits = nested(self.limits, self.budget.bytes, self.deadline.elapsed())
        try:
            result = decode(data, name=posixpath.basename(name.rstrip("/")) or None, limits=inner_limits)
        except ArtifactError as error:
            rule = getattr(error, "rule", "")
            if rule == "max_decoded_bytes" and len(data) * self.limits.max_ratio < inner_limits.max_decoded_bytes:
                rule = "bomb_like"      # a small member that would need more than the budget: a bomb, not a big file
            if isinstance(error, Refused) and error.reason != "timeout" and refuses_whole(rule):
                self.refuse(name, len(data), rule, "")
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
        walk.notes.append(f"stopped after the first {len(reader.entries)} of the {reader.declared} members the "
                          f"archive declares (the limit is {limits.max_members}): the other "
                          f"{reader.declared - len(reader.entries)} are not read")
    offending = []
    for entry in reader.entries:
        unsafe = unsafe_member_name(entry.name)
        if unsafe:
            offending.append((entry.name, f"has an unsafe name ({unsafe})"))
        elif entry.is_link:
            offending.append((entry.name, "is a symbolic link"))
    if offending:
        refuse_unsafe("ZIP", offending, [(e.name, e.size) for e in reader.entries])
    for entry in reader.entries:
        walk.deadline.check()
        if entry.is_dir:
            walk.members.append(Member(entry.name, 0, _directory("zip"), None, "directory"))
            continue
        if entry.encrypted:
            walk.refuse(entry.name, entry.size, "encrypted", "refused: the member is encrypted and is not decrypted")
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
            walk.refuse(entry.name, entry.size, error.rule, f"refused: {error} (rule {error.rule})",
                        remaining=[(e.name, e.size) for e in reader.entries[reader.entries.index(entry) + 1:]])
            continue
        except Malformed:
            # the archive's own stored bytes are damaged or lie (a CRC that fails, a member past the end): the
            # archive is refused as a whole, never answered from its other members
            refuse_whole("ZIP", "a member's stored bytes are damaged or do not match the directory",
                         "damaged_member", [(e.name, e.size) for e in reader.entries])
        except Unsupported as error:
            walk.counts["failed"] += 1
            walk.members.append(Member(entry.name, entry.size, _listed(entry.name, "zip", "not read"), None,
                                       _cut(f"{error.reason}: {error}")))
            continue
        walk.decode_member(entry.name, body)
    return walk.finish(detected)


def _tar_check(data: bytes, limits) -> None:
    """A first pass over the TAR headers only (no member is read): refuse the whole archive when a member is unsafe."""
    tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:")
    deadline = Deadline(limits.max_seconds)
    offending, listing = [], []
    while len(listing) < limits.max_members:
        deadline.check()
        try:
            info = tf.next()
        except tarfile.TarError:
            break                                   # the decoding pass reports the damage
        if info is None:
            break
        tf.members = []
        listing.append((info.name, info.size))
        unsafe = unsafe_member_name(info.name)
        if unsafe:
            offending.append((info.name, f"has an unsafe name ({unsafe})"))
        elif info.issym() or info.islnk():
            kind = "symbolic" if info.issym() else "hard"
            offending.append((info.name, f"is a {kind} link to {_quoted(info.linkname)}"))
        elif not (info.isdir() or info.isreg()):
            offending.append((info.name, "is a device, FIFO or other special entry"))
    if offending:
        refuse_unsafe("TAR", offending, listing)


def read_tar(data: bytes, detected: Detected, limits) -> Decoded:
    walk = _Walk(limits, "tar")
    try:
        _tar_check(data, limits)
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
            walk.refuse(name, info.size, "sparse_member", "refused: a sparse member (its holes would be materialised)")
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
            # the header states more bytes than the archive holds: a lying size refuses the whole archive
            walk.refuse(name, info.size, "lying_size", "")
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
