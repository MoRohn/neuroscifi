"""Detect, inspect, identify (DESIGN §3, ADR-072): `detect(head, name=, declared=, size=) -> Detected`.

Order: signature (magic numbers at fixed offsets), then structure (text encodings, JSON, XML, CSV, ... within a
bounded prefix), then the declared media type and the file name's extension, which are hints only. A hint that
contradicts a signature or a structure loses, and `reasons` says so. Unknown stays unknown. `detect` sees at most
HEAD_MAX bytes and never looks inside containers (adapters do). It never raises on any input.
"""
from __future__ import annotations

import json

from .types import Detected

HEAD_MAX = 64 << 10

# format -> (family, media type). The single table of names this module may answer with.
FORMATS: dict[str, tuple[str, str]] = {
    "png": ("image", "image/png"),
    "gzip": ("archive", "application/gzip"),
    "zstd": ("archive", "application/zstd"),
    "bzip2": ("archive", "application/x-bzip2"),
    "xz": ("archive", "application/x-xz"),
    "pickle": ("unknown", "application/x-python-pickle"),
    "json": ("tree", "application/json"),
    "text": ("text", "text/plain"),
}

EXTENSIONS: dict[str, str] = {".png": "png", ".gz": "gzip", ".zst": "zstd", ".bz2": "bzip2", ".xz": "xz",
                              ".pkl": "pickle", ".pickle": "pickle", ".json": "json", ".txt": "text"}
DECLARED: dict[str, str] = {media: fmt for fmt, (_, media) in FORMATS.items()}

# Further detectors added by adapter lanes (B1/B2): `fn(head, size) -> (format, basis, reasons, container) | None`,
# tried in order before the signature table; the first answer wins. A detector that raises is skipped.
DETECTORS: list = []

# (offset, magic, format, what was seen)
SIGNATURES: list[tuple[int, bytes, str, str]] = [
    (0, b"\x89PNG\r\n\x1a\n", "png", "the first 8 bytes are the PNG signature"),
    (0, b"\x1f\x8b", "gzip", "the first 2 bytes are the gzip magic number"),
    (0, b"\x28\xb5\x2f\xfd", "zstd", "the first 4 bytes are the Zstandard frame magic number"),
    (0, b"BZh", "bzip2", "the first 3 bytes are the bzip2 magic 'BZh'"),
    (0, b"\xfd7zXZ\x00", "xz", "the first 6 bytes are the xz magic number"),
]


def _make(fmt: str, basis: str, reasons: list[str], container: str | None = None) -> Detected:
    family, media = FORMATS.get(fmt, ("unknown", "application/octet-stream"))
    return Detected(family, fmt, media, basis, container, tuple(reasons))


def _hints(name: str | None, declared: str | None) -> list[tuple[str, str]]:
    """(format, reason) for each hint that names a known format."""
    out = []
    if declared:
        media = declared.split(";")[0].strip().lower()
        if media in DECLARED:
            out.append((DECLARED[media], f"the declared type is {media}"))
    if name:
        lower = name.lower()
        for ext in sorted(EXTENSIONS, key=len, reverse=True):
            if lower.endswith(ext):
                out.append((EXTENSIONS[ext], f"the file name ends in {ext}"))
                break
    return out


def _by_signature(head: bytes, size: int | None) -> tuple[str, list[str], str | None] | None:
    for offset, magic, fmt, why in SIGNATURES:
        if head[offset:offset + len(magic)] == magic:
            return fmt, [why], None
    return None


def _by_structure(head: bytes) -> tuple[str, list[str]] | None:
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        return None
    stripped = text.strip()
    if stripped[:1] in ("{", "[") and len(head) < HEAD_MAX:
        try:
            json.loads(stripped)
            return "json", ["the bytes are UTF-8 text that parses as JSON"]
        except ValueError:
            pass
    if text and all(c.isprintable() or c in "\t\r\n" for c in text):
        return "text", ["the bytes are valid UTF-8 with no control characters"]
    return None


def detect(head: bytes, *, name: str | None = None, declared: str | None = None,
           size: int | None = None) -> Detected:
    try:
        head = bytes(head[:HEAD_MAX])
    except Exception:
        return Detected("unknown", "unknown", "application/octet-stream", "none", None, ("input is not bytes",))
    hints = _hints(name, declared)
    found, basis = None, "signature"
    for detector in DETECTORS:
        try:
            hit = detector(head, size)
        except Exception:
            hit = None
        if hit:
            found, basis = (hit[0], list(hit[2]), hit[3]), hit[1]
            break
    if found is None:
        found = _by_signature(head, size)
    if found is None:
        structure = _by_structure(head)
        found = (structure[0], structure[1], None) if structure else None
        basis = "structure"
    if found is not None:
        fmt, reasons, container = found
        for hinted, why in hints:
            if hinted != fmt:
                reasons.append(f"{why}, which names {hinted}: the {basis} wins")
        return _make(fmt, basis, reasons, container)
    if hints:
        fmt, why = hints[0]
        return _make(fmt, "declared", [why + "; nothing in the bytes confirms it"])
    return Detected("unknown", "unknown", "application/octet-stream", "none", None,
                    ("no signature or structure was recognised",))


__all__ = ["DECLARED", "DETECTORS", "EXTENSIONS", "FORMATS", "HEAD_MAX", "SIGNATURES", "detect"]
