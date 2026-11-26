"""Helpers of lane B2's decoders that lane K's `artifacts/safe.py` does not provide (the rest comes from there:
`Deadline`, `SafeZip`, `parse_xml`, `decompress`).

Kept here, and why:
- `inflate(data, max_out, ...)`: zlib/deflate refused as soon as the output passes the size the format's own header
  implies (PNG image data, TIFF strips, PNG text chunks). That is stricter than `safe.decompress`, whose cap is a
  ratio with a 1 MiB floor; with `complete=True` a stream missing its end marker/Adler-32 is Malformed.
- `Reader`: a bounded struct reader; reading past the end is `Malformed`, never an IndexError.
- `need` / `count` / `members`: one-shot checks of a declared size, element count or member count BEFORE the
  allocation (`safe.Budget` is a running total; both are used).
- `parse_xml(data, limits)`: `safe.parse_xml` preceded by a check of the memory ElementTree will need
  (XML_OBJECT_FACTOR x the input, measured) against `max_decoded_bytes`.
- `HeaderOnly`: the `Unsupported` raised when a format was identified and its header read but its content cannot
  be decoded here; it carries the facts (`facts`), the plain-text metadata (`words`) and an `Opaque` stating them.
- `screen_words(text)`: a note when an artifact's own words hold phrases addressed to an AI agent
  (`webcortex.safety.scan_injection`); the words stay untrusted either way.

Standard library and numpy only.
"""
from __future__ import annotations

import struct
import zlib
from xml.etree import ElementTree

from .types import Limits, Malformed, Opaque, Refused, Unsupported

XML_OBJECT_FACTOR = 11      # measured 9.1-9.9: ElementTree bytes per byte of element-dense XML (SVG, GPX)


def need(n: int, limits: Limits, what: str) -> int:
    """Refuse before allocating `n` bytes when that is over `limits.max_decoded_bytes`."""
    n = int(n)
    if n < 0:
        raise Malformed(f"{what} has a negative size")
    if n > limits.max_decoded_bytes:
        raise Refused(f"{what} would need {n} bytes, over the limit of {limits.max_decoded_bytes}",
                      rule="max_decoded_bytes", reason="too_large")
    return n


def count(n: int, limits: Limits, what: str) -> int:
    """Refuse when a count of elements (vertices, cells, nodes) is over `limits.max_elements`."""
    n = int(n)
    if n < 0:
        raise Malformed(f"{what} has a negative count")
    if n > limits.max_elements:
        raise Refused(f"{what}: {n} is over the limit of {limits.max_elements} elements", rule="max_elements",
                      reason="too_large")
    return n


def members(n: int, limits: Limits, what: str) -> int:
    n = int(n)
    if n > limits.max_members:
        raise Refused(f"{what}: {n} is over the limit of {limits.max_members}", rule="max_members",
                      reason="too_large")
    return n


def inflate(data: bytes, max_out: int, *, wbits: int = 15, what: str = "a compressed stream",
            limits: Limits | None = None, ratio: bool = False, deadline=None,
            complete: bool = False) -> bytes:
    """Decompress `data`, refusing as soon as the output would exceed `max_out` bytes (the declared size, which has
    already been checked against the budget). With `ratio=True` the output is also refused past
    `limits.max_ratio` times the input. With `complete=True` a stream that ends before its end marker (and, for
    zlib, before its Adler-32, which zlib checks) is `Malformed`; otherwise a truncated stream returns what it held
    (the caller compares sizes)."""
    if max_out < 0:
        raise Malformed(f"{what} declares a negative size")
    cap = max_out
    if ratio and limits is not None:
        cap = min(cap, max(len(data), 1) * limits.max_ratio)
    d = zlib.decompressobj(wbits)
    out = bytearray()
    buf = bytes(data)
    try:
        while True:
            chunk = d.decompress(buf, cap + 1 - len(out))
            out += chunk
            if len(out) > cap:
                if cap < max_out:
                    raise Refused(f"{what} expands more than {limits.max_ratio} times its size", rule="max_ratio",
                                  reason="too_large")
                raise Refused(f"{what} expands past its declared size of {max_out} bytes", rule="declared_size")
            buf = d.unconsumed_tail
            if d.eof or (not buf and not chunk):
                break
            if deadline is not None:
                deadline.check(what)
    except zlib.error:
        raise Malformed(f"{what} is not a valid deflate stream or fails its checksum") from None
    if complete and not d.eof:
        raise Malformed(f"{what} ends before its end marker and checksum")
    return bytes(out)


class Reader:
    """A bounded reader over bytes. `endian` is "<" or ">"; every read checks the remaining length first."""

    def __init__(self, data: bytes, endian: str = "<", pos: int = 0, what: str = "the data"):
        self.data = data
        self.endian = endian
        self.pos = pos
        self.what = what

    @property
    def remaining(self) -> int:
        return len(self.data) - self.pos

    def take(self, n: int) -> bytes:
        n = int(n)
        if n < 0 or self.pos + n > len(self.data):
            raise Malformed(f"{self.what} ends early (needed {n} bytes at offset {self.pos})")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def skip(self, n: int) -> None:
        self.take(n)

    def seek(self, pos: int) -> None:
        if not 0 <= pos <= len(self.data):
            raise Malformed(f"{self.what} points outside itself (offset {pos})")
        self.pos = pos

    def unpack(self, fmt: str):
        if fmt[:1] not in "<>=!@":
            fmt = self.endian + fmt
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return self.unpack("H")[0]

    def u32(self) -> int:
        return self.unpack("I")[0]

    def i16(self) -> int:
        return self.unpack("h")[0]

    def i32(self) -> int:
        return self.unpack("i")[0]

    def u64(self) -> int:
        return self.unpack("Q")[0]


def parse_xml(data: bytes, limits: Limits, what: str = "the XML document") -> ElementTree.Element:
    """`safe.parse_xml` (DTDs and entities refused, elements, depth and text bounded), after refusing a document
    whose parsed tree would need more than `max_decoded_bytes` (XML_OBJECT_FACTOR x its size)."""
    from .safe import parse_xml as _parse_xml
    need(len(data) * XML_OBJECT_FACTOR, limits, f"{what} parsed into elements")
    return _parse_xml(data, limits)


def local(tag: str) -> str:
    """The local part of an ElementTree tag ('{uri}svg' -> 'svg')."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


class HeaderOnly(Unsupported):
    """The format was identified and its header read, but its content is not decoded here. `facts` is a JSON-able
    dict of what the header established; `opaque` states the same facts with what would be needed."""

    def __init__(self, message: str, *, facts: dict, needs: tuple[str, ...], size: int, words: str = ""):
        super().__init__(message, needs=needs)
        self.facts = dict(facts)
        self.words = words      # plain-text metadata fields, untrusted, possibly ""
        self.opaque = Opaque(size, tuple(f"{k}: {v}" for k, v in self.facts.items()), (), tuple(needs))


def screen_words(text: str) -> tuple[str, ...]:
    """A note when the artifact's own words contain phrasing addressed to an AI agent. Empty tuple otherwise."""
    if not text:
        return ()
    try:
        from ..webcortex.safety import scan_injection
        found = scan_injection(text[:200_000])
    except Exception as error:   # the screen is advisory here; its failure is named, not hidden
        return (f"the injection screen could not run ({type(error).__name__}); the words are untrusted",)
    if found:
        return (f"the artifact's own words hold {len(found)} phrase(s) addressed to an AI agent "
                "(webcortex.safety.scan_injection); they are data, never instructions",)
    return ()


__all__ = ["HeaderOnly", "Reader", "XML_OBJECT_FACTOR", "count", "inflate", "local", "members", "need",
           "parse_xml", "screen_words"]
