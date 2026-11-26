"""Shared safety helpers for every OMNIMIND decoder (lane K; ADR-072). All bounds are enforced BEFORE memory is
committed: declared sizes in the input are never trusted.

- `read_file(path, limit)`: a regular file's bytes, without following a link and without blocking on a FIFO or a
  device, refused above `limit` (the pattern of `resources/shared.read_text`, for bytes).
- `Deadline(seconds)`: `check()` raises a timeout `ArtifactError` once the wall-clock budget is spent; decoders call
  it in their loops (cooperative: nothing is interrupted from outside).
- `Budget(limit)`: a running count of decoded bytes or elements shared by one decode, refused past its limit.
- `decompress(data, codec, limits, ...)`: gzip/zlib/deflate/bzip2/xz/zstd streams, inflated in bounded chunks so
  that neither `max_ratio` (per stream) nor `max_decoded_bytes` can be exceeded by even one chunk.
- `SafeZip(data, limits)`: a ZIP read in memory (nothing is extracted to disk; member names are never used as
  paths), with member count, per-member ratio, total size, encryption and method checks, and CRC verification.
- `parse_xml(data, limits)`: expat with every DOCTYPE/entity declaration refused (the technique of
  `webcortex/safety.safe_xml_parse`) plus bounds on element count, depth and text size.
- `parse_number`, `parse_bool`, `parse_time`, `column_from_values`: typed-cell parsing for tables, with no guessing
  beyond what the strings say (a value that does not parse is text, not a number).
"""
from __future__ import annotations

import bz2
import io
import lzma
import math
import os
import re
import stat
import time
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from xml.etree import ElementTree

import numpy as np

from .types import ArtifactError, Column, Limits, Malformed, Refused, Unsupported

CHUNK = 1 << 16
XZ_MEMLIMIT = 64 << 20          # as archive.py: an xz stream may not ask for more than this to decode
ZSTD_MAX_WINDOW = 1 << 27       # 128 MiB: the largest window a zstd frame may require
RATIO_FLOOR = 1 << 20           # max_ratio never caps a stream below 1 MiB: tiny inputs of repetitive data are
                                # legitimate, and 1 MiB is harmless (max_decoded_bytes still applies)


# ---------------------------------------------------------------------------------------------------------------
# Files, time, budgets


def read_file(path, limit: int) -> bytes:
    """The bytes of a regular file, at most `limit` (Refused above it). Never follows a symbolic link, never blocks
    on a FIFO or device. Failures are named errors with a plain reason."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        raise Refused("there is no such file", rule="not_found") from None
    except PermissionError:
        raise Refused("the file cannot be read with this account's permissions", rule="permission") from None
    except OSError as error:
        if getattr(error, "errno", None) == 40:   # ELOOP: a symbolic link with O_NOFOLLOW
            raise Refused("the path is a symbolic link; give the file itself", rule="symlink") from None
        raise Refused(f"the file cannot be opened ({type(error).__name__})", rule="open") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise Refused("only regular files are read (not a directory, device, pipe or socket)", rule="not_regular")
        if info.st_size > limit:
            raise Refused(f"the file holds {info.st_size} bytes, more than the limit of {limit}", rule="max_bytes",
                          reason="too_large")
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, min(CHUNK, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:   # the file grew while being read
                raise Refused(f"the file holds more than the limit of {limit} bytes", rule="max_bytes",
                              reason="too_large")
        return b"".join(chunks)
    finally:
        os.close(fd)


class Deadline:
    """A wall-clock budget, checked cooperatively by the decoder's own loops."""

    def __init__(self, seconds: float, *, clock=time.monotonic):
        self._clock = clock
        self.seconds = float(seconds)
        self.started = clock()

    def elapsed(self) -> float:
        return self._clock() - self.started

    def remaining(self) -> float:
        return self.seconds - self.elapsed()

    def check(self, where: str = "decoding") -> None:
        if self.elapsed() > self.seconds:
            raise ArtifactError(f"{where} took longer than the limit of {self.seconds:g} s", reason="timeout")


class Budget:
    """A running count (bytes or elements) that may not pass `limit`; `rule` names the limit in the refusal."""

    def __init__(self, limit: int, rule: str):
        self.limit, self.rule, self.used = int(limit), rule, 0

    def take(self, n: int) -> None:
        if self.used + n > self.limit:
            raise Refused(f"more than {self.limit} would be decoded ({self.rule})", rule=self.rule,
                          reason="too_large")
        self.used += n

    def left(self) -> int:
        return max(0, self.limit - self.used)


# ---------------------------------------------------------------------------------------------------------------
# Streams


def _bounded_loop(step, cap: int, deadline: Deadline | None, why_cap: str) -> bytes:
    """Collect chunks from `step(max_len)` (None when finished) without ever holding more than `cap` + 1 bytes."""
    out, total = [], 0
    while True:
        if deadline is not None:
            deadline.check()
        chunk = step(min(CHUNK, cap + 1 - total))
        if chunk is None:
            return b"".join(out)
        out.append(chunk)
        total += len(chunk)
        if total > cap:
            raise Refused(f"the stream decompresses to more than {cap} bytes ({why_cap})", rule=why_cap,
                          reason="too_large")


def _stream_cap(data: bytes, limits: Limits, budget: Budget | None) -> tuple[int, str]:
    by_ratio = max(len(data) * limits.max_ratio, RATIO_FLOOR)
    by_total = budget.left() if budget is not None else limits.max_decoded_bytes
    return (by_ratio, "max_ratio") if by_ratio < by_total else (by_total, "max_decoded_bytes")


def decompress(data: bytes, codec: str, limits: Limits, *, deadline: Deadline | None = None,
               budget: Budget | None = None, max_members: int | None = None) -> tuple[bytes, list[str]]:
    """Inflate one stream (`codec`: "gzip", "zlib", "deflate", "bzip2", "xz", "zstd"). Concatenated members of the
    same codec are read in turn, at most `max_members` (default limits.max_members). Returns (bytes, notes)."""
    data = bytes(data)
    cap, why = _stream_cap(data, limits, budget)
    max_members = limits.max_members if max_members is None else max_members
    notes: list[str] = []
    try:
        if codec == "zstd":
            out = _zstd_frames(data, cap, why, deadline, max_members, notes)
        else:
            out = _inflate_members(data, codec, cap, why, deadline, max_members, notes)
    except ArtifactError:
        raise
    except (zlib.error, OSError, EOFError, lzma.LZMAError, ValueError) as error:
        text = str(error).lower()
        if "memory" in text and "limit" in text:
            raise Refused("the xz stream needs more memory to decode than allowed", rule="memlimit") from None
        raise Malformed(f"the {codec} stream is corrupt or truncated ({type(error).__name__})") from None
    except Exception as error:   # zstandard.ZstdError and anything else a codec raises
        raise Malformed(f"the {codec} stream is corrupt or truncated ({type(error).__name__})") from None
    if budget is not None:
        budget.take(len(out))
    return out, notes


ZSTD_MAX_PER_INPUT_BYTE = 32 << 10   # an RLE block: 4 input bytes -> one 128 KiB block
ZSTD_BLOCK = 128 << 10


def _zstd_frames(data, cap, why, deadline, max_members, notes) -> bytes:
    """zstd frames in turn. Input is fed in pieces small enough that even the worst case (RLE blocks, 32 KiB of
    output per input byte, plus one block begun earlier) cannot pass `cap` by more than one block; the frame's end
    must be reached, so a truncated frame is Malformed (the zstandard stream reader would return nothing silently)."""
    import zstandard
    out, total, rest, frames = [], 0, data, 0
    while rest:
        frames += 1
        if frames > max_members:
            raise Refused(f"more than {max_members} concatenated zstd frames", rule="max_members")
        d = zstandard.ZstdDecompressor(max_window_size=ZSTD_MAX_WINDOW).decompressobj()
        pos = 0
        while not d.eof:
            if deadline is not None:
                deadline.check()
            if pos >= len(rest):
                raise Malformed("the zstd stream ends before its frame does (truncated)")
            room = cap + 1 - total
            n = max(1, min(CHUNK, (room - ZSTD_BLOCK) // ZSTD_MAX_PER_INPUT_BYTE))
            chunk = d.decompress(rest[pos:pos + n])
            pos += n
            out.append(chunk)
            total += len(chunk)
            if total > cap:
                raise Refused(f"the stream decompresses to more than {cap} bytes ({why})", rule=why,
                              reason="too_large")
        rest = d.unused_data + rest[pos:]
        if rest and rest[:4] != b"\x28\xb5\x2f\xfd" and not (0x50 <= rest[0] <= 0x5F and rest[1:4] == b"\x2a\x4d\x18"):
            notes.append(f"{len(rest)} bytes after the last zstd frame were not decoded")
            break
    if frames > 1:
        notes.append(f"{frames} concatenated zstd frames were decoded in order")
    return b"".join(out)


def _new_decompressor(codec: str):
    if codec == "gzip":
        return zlib.decompressobj(wbits=31)
    if codec == "zlib":
        return zlib.decompressobj(wbits=15)
    if codec == "deflate":
        return zlib.decompressobj(wbits=-15)
    if codec == "bzip2":
        return bz2.BZ2Decompressor()
    if codec == "xz":
        return lzma.LZMADecompressor(format=lzma.FORMAT_AUTO, memlimit=XZ_MEMLIMIT)
    raise Unsupported(f"no decompressor for {codec!r}", needs=(f"a {codec} decoder",))


def _inflate_members(data, codec, cap, why, deadline, max_members, notes) -> bytes:
    out, total, rest, members = [], 0, data, 0
    while rest:
        members += 1
        if members > max_members:
            raise Refused(f"more than {max_members} concatenated {codec} members", rule="max_members")
        d = _new_decompressor(codec)
        is_zlib = codec in ("gzip", "zlib", "deflate")
        pending = rest
        while True:
            if deadline is not None:
                deadline.check()
            room = cap + 1 - total
            if is_zlib:
                chunk = d.decompress(pending, min(CHUNK, room))
                pending = d.unconsumed_tail
            else:
                chunk = d.decompress(pending, min(CHUNK, room))
                pending = b""
            out.append(chunk)
            total += len(chunk)
            if total > cap:
                raise Refused(f"the stream decompresses to more than {cap} bytes ({why})", rule=why,
                              reason="too_large")
            if d.eof:
                break
            if not chunk and (is_zlib and not pending or not is_zlib and d.needs_input):
                raise Malformed(f"the {codec} stream ends before its end marker (truncated)")
        rest = d.unused_data
        if rest and codec in ("zlib", "deflate"):
            notes.append(f"{len(rest)} bytes after the end of the stream were not decoded")
            break
        if rest and codec == "gzip" and rest[:2] != b"\x1f\x8b":
            notes.append(f"{len(rest)} bytes after the last gzip member were not decoded")
            break
        if rest and codec == "bzip2" and rest[:3] != b"BZh":
            notes.append(f"{len(rest)} bytes after the last bzip2 stream were not decoded")
            break
        if rest and codec == "xz" and rest.strip(b"\x00") == b"":
            break   # stream padding
    if members > 1:
        notes.append(f"{members} concatenated {codec} members were decoded in order")
    return b"".join(out)


# ---------------------------------------------------------------------------------------------------------------
# ZIP


@dataclass(frozen=True)
class ZipEntry:
    name: str                   # as stored; never used as a path
    size: int                   # declared uncompressed size (a claim, checked while reading)
    compressed_size: int
    method: int
    encrypted: bool
    is_dir: bool


def zip_declared_entries(data: bytes) -> int | None:
    """The member count the end-of-central-directory record claims (Zip64 included), read before any directory is
    parsed so that a directory of millions of entries is refused without building them. None if no record."""
    tail_from = max(0, len(data) - (1 << 16) - 22)
    at = data.rfind(b"PK\x05\x06", tail_from)
    if at < 0 or at + 22 > len(data):
        return None
    count = int.from_bytes(data[at + 10:at + 12], "little")
    loc = at - 20
    if count == 0xFFFF and loc >= 0 and data[loc:loc + 4] == b"PK\x06\x07":
        rec = int.from_bytes(data[loc + 8:loc + 16], "little")
        if 0 <= rec <= len(data) - 56 and data[rec:rec + 4] == b"PK\x06\x06":
            count = int.from_bytes(data[rec + 32:rec + 40], "little")
    return count


_ZIP_METHODS = {zipfile.ZIP_STORED: "stored", zipfile.ZIP_DEFLATED: "deflate", zipfile.ZIP_BZIP2: "bzip2",
                zipfile.ZIP_LZMA: "lzma"}


class SafeZip:
    """A ZIP archive read from memory. `entries` lists members (bounded by max_members); `read(name)` inflates one
    member in bounded chunks against its declared size, the per-stream ratio and the shared decoded budget, and
    verifies its CRC. Nothing touches the disk."""

    def __init__(self, data: bytes, limits: Limits, *, deadline: Deadline | None = None,
                 budget: Budget | None = None):
        self.limits = limits
        self.deadline = deadline or Deadline(limits.max_seconds)
        self.budget = budget or Budget(limits.max_decoded_bytes, "max_decoded_bytes")
        declared = zip_declared_entries(data)
        if declared is not None and declared > limits.max_members:
            raise Refused(f"the archive declares {declared} members, more than {limits.max_members}",
                          rule="max_members")
        try:
            self._zip = zipfile.ZipFile(io.BytesIO(bytes(data)))
            infos = self._zip.infolist()
        except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, EOFError, OSError) as error:
            raise Malformed(f"the ZIP directory cannot be read ({type(error).__name__})") from None
        if len(infos) > limits.max_members:
            raise Refused(f"the archive lists {len(infos)} members, more than {limits.max_members}",
                          rule="max_members")
        self._infos = {}
        entries = []
        for info in infos:
            entry = ZipEntry(info.filename, int(info.file_size), int(info.compress_size), int(info.compress_type),
                             bool(info.flag_bits & 0x1), info.is_dir())
            entries.append(entry)
            self._infos.setdefault(info.filename, info)
        self.entries: tuple[ZipEntry, ...] = tuple(entries)

    def names(self) -> list[str]:
        return [e.name for e in self.entries]

    def read(self, name: str, *, max_bytes: int | None = None) -> bytes:
        info = self._infos.get(name)
        if info is None:
            raise Malformed("the archive has no member of that name")
        if info.flag_bits & 0x1:
            raise Unsupported("the member is encrypted", needs=("the password, which is never asked for",))
        if info.compress_type not in _ZIP_METHODS:
            raise Unsupported(f"the member uses ZIP compression method {info.compress_type}",
                              needs=(f"a decoder for ZIP method {info.compress_type}",))
        cap = min(max(info.compress_size * self.limits.max_ratio, RATIO_FLOOR) if info.compress_type else info.file_size,
                  self.budget.left(), max_bytes if max_bytes is not None else self.budget.left())
        if info.file_size > cap:
            raise Refused(f"a member declares {info.file_size} bytes, more than may be decoded here ({cap})",
                          rule="max_ratio" if info.compress_type else "max_decoded_bytes", reason="too_large")
        try:
            with self._zip.open(info) as handle:
                out = _bounded_loop(lambda n: handle.read(n) or None, min(cap, info.file_size), self.deadline,
                                    "declared_size")
        except ArtifactError:
            raise
        except zipfile.BadZipFile as error:
            raise Malformed(f"a ZIP member is corrupt ({'CRC mismatch' if 'CRC' in str(error) else 'bad data'})") \
                from None
        except (zlib.error, EOFError, OSError, ValueError, NotImplementedError, lzma.LZMAError) as error:
            raise Malformed(f"a ZIP member cannot be inflated ({type(error).__name__})") from None
        if len(out) != info.file_size:
            raise Malformed("a ZIP member is shorter than its declared size")
        self.budget.take(len(out))
        return out


# ---------------------------------------------------------------------------------------------------------------
# XML


def _refuse(*_args):
    raise Refused("XML with a DOCTYPE or entity declaration is refused", rule="xml_dtd")


def parse_xml(data: bytes, limits: Limits, *, deadline: Deadline | None = None) -> ElementTree.Element:
    """ElementTree root of an untrusted XML document. Any DOCTYPE or entity declaration, external entity or
    undefined entity is refused before expansion (as `webcortex/safety.safe_xml_parse`); element count is bounded
    by max_elements, depth by 256, character data by max_decoded_bytes. Namespaced tags are '{uri}local'."""
    import xml.parsers.expat as expat
    data = bytes(data)
    if not data.strip():
        raise Malformed("the XML document is empty")
    deadline = deadline or Deadline(limits.max_seconds)
    builder = ElementTree.TreeBuilder()
    parser = expat.ParserCreate(namespace_separator="}")
    state = {"elements": 0, "depth": 0, "chars": 0}
    max_depth = 256

    def name(raw):
        return "{" + raw if "}" in raw else raw

    def start(tag, attrs):
        state["elements"] += 1
        state["depth"] += 1
        if state["elements"] > limits.max_elements:
            raise Refused(f"the XML holds more than {limits.max_elements} elements", rule="max_elements")
        if state["depth"] > max_depth:
            raise Refused(f"the XML nests deeper than {max_depth} elements", rule="xml_depth")
        if state["elements"] % 4096 == 0:
            deadline.check("parsing XML")
        builder.start(name(tag), {name(k): v for k, v in attrs.items()})

    def end(tag):
        state["depth"] -= 1
        builder.end(name(tag))

    def chars(text):
        state["chars"] += len(text)
        if state["chars"] > limits.max_decoded_bytes:
            raise Refused("the XML holds more character data than may be decoded", rule="max_decoded_bytes")
        builder.data(text)

    parser.StartDoctypeDeclHandler = _refuse
    parser.EntityDeclHandler = _refuse
    parser.UnparsedEntityDeclHandler = _refuse
    parser.ExternalEntityRefHandler = _refuse
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.buffer_text = True
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    try:
        parser.Parse(data, True)
        return builder.close()
    except ArtifactError:
        raise
    except expat.ExpatError as error:
        raise Malformed(f"the XML is malformed ({expat.ErrorString(error.code)})") from None
    except RecursionError:
        raise Refused("the XML nests too deeply", rule="xml_depth") from None


# ---------------------------------------------------------------------------------------------------------------
# Cells: numbers, booleans, times


MISSING = frozenset({"", "na", "n/a", "nan", "null", "none", "nil", "-", "--", "?", "#n/a"})
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_GROUPED = re.compile(r"[+-]?\d{1,3}(?:([,_   ])\d{3})(?:\1\d{3})*(?:\.\d+)?")
_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,9}))?)?"
                  r"(Z|[+-]\d{2}:?\d{2})?)?")
_TRUE, _FALSE = frozenset({"true", "yes"}), frozenset({"false", "no"})


def is_missing(text) -> bool:
    return text is None or (isinstance(text, str) and text.strip().lower() in MISSING)


def parse_number(text: str) -> float | None:
    """A decimal or scientific number, with optional sign and thousands grouping by comma, underscore or space
    (groups of exactly three digits). None for anything else, including NaN and infinity words."""
    s = text.strip()
    if len(s) > 64:
        return None
    if _NUMBER.fullmatch(s):
        value = float(s)
    elif _GROUPED.fullmatch(s):
        value = float(re.sub(r"[,_   ]", "", s))
    else:
        return None
    return value if math.isfinite(value) else None


def parse_bool(text: str) -> bool | None:
    s = text.strip().lower()
    return True if s in _TRUE else False if s in _FALSE else None


def parse_time(text: str) -> float | None:
    """ISO 8601 date or date-time -> seconds since the epoch, UTC. A time without an offset is read as UTC (callers
    say so in their notes). None for anything else; no other date layout is guessed (03/04/05 is ambiguous)."""
    m = _ISO.fullmatch(text.strip())
    if m is None:
        return None
    year, month, day, hh, mm, ss, frac, zone = m.groups()
    try:
        dt = datetime(int(year), int(month), int(day), int(hh or 0), int(mm or 0), int(ss or 0), tzinfo=timezone.utc)
    except ValueError:
        return None
    seconds = dt.timestamp() + (float("0." + frac) if frac else 0.0)
    if zone and zone != "Z":
        sign = 1 if zone[0] == "+" else -1
        digits = zone[1:].replace(":", "")
        seconds -= sign * (int(digits[:2]) * 3600 + int(digits[2:]) * 60)
    return seconds


def column_from_values(name: str, values, *, unit: str | None = None) -> Column:
    """A typed Column from raw cells (str, int, float, bool or None). The kind is the narrowest that every
    non-missing cell satisfies: integer, number, boolean, time (ISO 8601 only), else text. Nothing is guessed:
    a column with one cell that does not parse stays text."""
    values = list(values)
    present = [v for v in values if not is_missing(v)]
    n_missing = len(values) - len(present)

    def as_float(v):
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return float(v) if math.isfinite(float(v)) else None
        return parse_number(v) if isinstance(v, str) else None

    if present and all(isinstance(v, bool) or (isinstance(v, str) and parse_bool(v) is not None) for v in present):
        arr = np.array([np.nan if is_missing(v) else float(v if isinstance(v, bool) else parse_bool(v))
                        for v in values], dtype=np.float64)
        return Column(name, "boolean", unit, arr, n_missing)
    floats = [as_float(v) for v in present]
    if present and all(f is not None for f in floats):
        integral = all(not isinstance(v, float) and float(f).is_integer() and abs(f) < 2 ** 53
                       and not (isinstance(v, str) and re.search(r"[.eE]", v)) for v, f in zip(present, floats))
        arr = np.array([np.nan if is_missing(v) else as_float(v) for v in values], dtype=np.float64)
        if integral and n_missing == 0:
            return Column(name, "integer", unit, arr.astype(np.int64), 0)
        return Column(name, "integer" if integral else "number", unit, arr, n_missing)
    if present and all(isinstance(v, str) and parse_time(v) is not None for v in present):
        arr = np.array([np.nan if is_missing(v) else parse_time(v) for v in values], dtype=np.float64)
        return Column(name, "time", unit, arr, n_missing)
    texts = tuple("" if is_missing(v) else (v if isinstance(v, str) else repr(v) if not isinstance(v, (int, float))
                                             else str(v)) for v in values)
    return Column(name, "text", unit, texts, n_missing)


__all__ = ["Budget", "Deadline", "MISSING", "SafeZip", "ZipEntry", "column_from_values", "decompress",
           "is_missing", "parse_bool", "parse_number", "parse_time", "parse_xml", "read_file", "zip_declared_entries"]
