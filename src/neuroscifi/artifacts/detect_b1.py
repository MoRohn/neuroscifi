"""Lane B1's detection for the formats its adapters read, written while lane K's full `detect` table is not yet
merged (DESIGN §3: "Detect ... K, extended by B1/B2"). The main session folds this into `detect.detect` or drops it
where K's table already answers the same names.

`refine(detected, head, name=, declared=, size=)` takes K's answer and, only when that answer is generic (unknown,
text, json, zip, a compressor), looks closer with the same discipline: signature first, then structure on the
bounded prefix, then the file name as a hint that a structure must confirm (or, alone, gives basis "declared").
It never raises. `detect(head, ...)` is K's `detect` followed by `refine`.

Format names (the adapters' `formats` use these; K's brief uses the same words): csv, tsv, ndjson, xlsx, ods,
markdown, html, docx, odt, pptx, odp, epub, rtf, pdf, xml, plist, toml, ini, yaml, python, javascript, c, shell,
ipynb, pyproject, requirements, package-json, zip, jar, tar, tar.gz, tar.bz2, tar.xz, tar.zst, 7z, rar, sqlite.
"""
from __future__ import annotations

import bz2
import configparser
import csv
import io
import json
import lzma
import re
import tomllib
import zlib

from .detect import detect as _kernel_detect
from .types import Detected

FORMATS: dict[str, tuple[str, str]] = {
    "csv": ("table", "text/csv"),
    "tsv": ("table", "text/tab-separated-values"),
    "ndjson": ("table", "application/x-ndjson"),
    "xlsx": ("table", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "ods": ("table", "application/vnd.oasis.opendocument.spreadsheet"),
    "markdown": ("document", "text/markdown"),
    "html": ("document", "text/html"),
    "docx": ("document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "odt": ("document", "application/vnd.oasis.opendocument.text"),
    "pptx": ("document", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    "odp": ("document", "application/vnd.oasis.opendocument.presentation"),
    "epub": ("document", "application/epub+zip"),
    "rtf": ("document", "application/rtf"),
    "pdf": ("document", "application/pdf"),
    "xml": ("tree", "application/xml"),
    "plist": ("tree", "application/x-plist"),
    "toml": ("tree", "application/toml"),
    "ini": ("tree", "text/plain"),
    "yaml": ("tree", "application/yaml"),
    "python": ("code", "text/x-python"),
    "javascript": ("code", "text/javascript"),
    "c": ("code", "text/x-c"),
    "shell": ("code", "text/x-shellscript"),
    "ipynb": ("notebook", "application/x-ipynb+json"),
    "pyproject": ("code", "application/toml"),
    "requirements": ("code", "text/plain"),
    "package-json": ("code", "application/json"),
    "zip": ("archive", "application/zip"),
    "jar": ("archive", "application/java-archive"),
    "tar": ("archive", "application/x-tar"),
    "tar.gz": ("archive", "application/x-tar"),
    "tar.bz2": ("archive", "application/x-tar"),
    "tar.xz": ("archive", "application/x-tar"),
    "tar.zst": ("archive", "application/x-tar"),
    "7z": ("archive", "application/x-7z-compressed"),
    "rar": ("archive", "application/vnd.rar"),
    "sqlite": ("database", "application/vnd.sqlite3"),
}

SIGNATURES = [
    (0, b"%PDF-", "pdf", "the first 5 bytes are '%PDF-'"),
    (0, b"{\\rtf", "rtf", "the first 5 bytes are '{\\rtf'"),
    (0, b"SQLite format 3\x00", "sqlite", "the first 16 bytes are the SQLite header string"),
    (0, b"7z\xbc\xaf\x27\x1c", "7z", "the first 6 bytes are the 7z signature"),
    (0, b"Rar!\x1a\x07", "rar", "the first 6 bytes are the RAR signature"),
    (0, b"bplist00", "plist", "the first 8 bytes are 'bplist00' (a binary property list)"),
    (257, b"ustar", "tar", "the bytes at offset 257 are the ustar magic"),
]

EXTENSIONS = {".csv": "csv", ".tsv": "tsv", ".tab": "tsv", ".ndjson": "ndjson", ".jsonl": "ndjson",
              ".md": "markdown", ".markdown": "markdown", ".html": "html", ".htm": "html", ".xhtml": "html",
              ".xml": "xml", ".plist": "plist", ".toml": "toml", ".ini": "ini", ".cfg": "ini", ".yaml": "yaml",
              ".yml": "yaml", ".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
              ".c": "c", ".h": "c", ".sh": "shell", ".bash": "shell", ".ipynb": "ipynb", ".rtf": "rtf"}
FILENAMES = {"pyproject.toml": "pyproject", "requirements.txt": "requirements", "package.json": "package-json"}
_ZIP_KINDS = {"application/vnd.oasis.opendocument.text": "odt",
              "application/vnd.oasis.opendocument.spreadsheet": "ods",
              "application/vnd.oasis.opendocument.presentation": "odp", "application/epub+zip": "epub"}
_HTML_ROOTS = {"head", "body", "div", "span", "table", "section", "article", "ul", "ol", "h1", "h2", "h3", "h4", "h5",
               "h6", "form", "nav"}              # an HTML fragment's usual first element (unless named .xml)
_GENERIC = {"unknown", "text", "json", "zip", "gzip", "bzip2", "xz", "zstd"}
_SHEBANG = re.compile(rb"^#![^\n]*\b(python[0-9.]*|node|bash|sh|zsh|dash|ksh)\b")
_MD_MARKERS = [(re.compile(r"(?m)^#{1,6} \S"), "an ATX heading"), (re.compile(r"(?m)^(```|~~~)"), "a fenced code block"),
               (re.compile(r"(?m)^\s{0,3}([-*+]|\d+\.) \S"), "a list item"),
               (re.compile(r"\[[^\[\]\n]{1,200}+\]\([^)\s\[\]]{1,500}+\)"), "a link"),
               (re.compile(r"(?m)^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$"), "a table separator row")]


def _make(fmt: str, basis: str, reasons: list[str], container: str | None = None) -> Detected:
    family, media = FORMATS[fmt]
    return Detected(family, fmt, media, basis, container, tuple(reasons))


def _base(name: str | None) -> str:
    return (name or "").replace("\\", "/").rsplit("/", 1)[-1].lower()


def _hint(name: str | None) -> str | None:
    base = _base(name)
    if base in FILENAMES:
        return FILENAMES[base]
    for ext in sorted(EXTENSIONS, key=len, reverse=True):
        if base.endswith(ext):
            return EXTENSIONS[ext]
    return None


def _zip_kind(head: bytes) -> tuple[str, str]:
    """Look at the local headers visible in the prefix: OOXML by part names, ODF/EPUB by the stored mimetype."""
    names, pos = [], 0
    while len(names) < 64:
        pos = head.find(b"PK\x03\x04", pos)
        if pos < 0 or pos + 30 > len(head):
            break
        nlen = int.from_bytes(head[pos + 26:pos + 28], "little")
        xlen = int.from_bytes(head[pos + 28:pos + 30], "little")
        member = head[pos + 30:pos + 30 + nlen].decode("utf-8", "replace")
        names.append(member)
        if member == "mimetype" and head[pos + 8:pos + 10] == b"\x00\x00":
            size = int.from_bytes(head[pos + 18:pos + 22], "little")
            body = head[pos + 30 + nlen + xlen:pos + 30 + nlen + xlen + min(size, 128)].decode("ascii", "replace")
            if body.strip() in _ZIP_KINDS:
                return _ZIP_KINDS[body.strip()], f"the stored 'mimetype' member says {body.strip()}"
        pos += 30
    for prefix, fmt in (("word/", "docx"), ("xl/", "xlsx"), ("ppt/", "pptx")):
        if any(n.startswith(prefix) for n in names) and any(n == "[Content_Types].xml" for n in names):
            return fmt, f"the archive holds [Content_Types].xml and {prefix} parts"
    if any(n == "META-INF/MANIFEST.MF" for n in names):
        return "jar", "the archive holds META-INF/MANIFEST.MF"
    return "zip", "ZIP local header signature"


def _peek_tar(head: bytes, method: str) -> bool:
    try:
        if method == "gzip":
            out = zlib.decompressobj(31).decompress(head, 1024)
        elif method == "bzip2":
            out = bz2.BZ2Decompressor().decompress(head, 1024)
        elif method == "xz":
            out = lzma.LZMADecompressor().decompress(head, 1024)
        else:
            import zstandard
            out = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(head)).read(1024)
    except Exception:
        return False
    return out[257:262] == b"ustar"


def _delimited(text: str) -> str | None:
    lines = [ln for ln in text.splitlines()[:32] if ln.strip()]
    if len(lines) < 2:
        return None
    for d, fmt in (("\t", "tsv"), (",", "csv"), (";", "csv"), ("|", "csv")):
        try:
            counts = [len(r) for r in csv.reader(io.StringIO("\n".join(lines)), delimiter=d, strict=False)]
        except csv.Error:
            continue
        body = counts[:-1] if len(counts) > 2 else counts      # the last line may be cut or short
        if body and min(body) >= 2 and len(set(body)) == 1:
            return fmt
    return None


def _ndjson(text: str) -> bool:
    lines = [ln for ln in text.splitlines()[:32] if ln.strip()]
    if len(lines) < 2:
        return False
    complete = lines[:-1] if len(lines) > 2 else lines
    try:
        return all(isinstance(json.loads(ln), dict) for ln in complete)
    except ValueError:
        return False


def _by_text(head: bytes, hint: str | None) -> tuple[str, str] | None:
    try:
        text = head.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        if error.start >= len(head) - 3:
            text = head[:error.start].decode("utf-8")      # a multi-byte character cut by the end of the prefix
        else:
            text = head.decode("latin-1")                  # 8-bit text: accepted only when almost all printable
            printable = sum(1 for ch in text if ch.isprintable() or ch in "\t\r\n")
            if not text or printable / len(text) < 0.95:
                return None
    stripped = text.lstrip()
    low = stripped[:2048].lower()
    if low.startswith("<!doctype html") or re.match(r"<html[\s>]", low) or (
            "<html" in low[:1024] and low.startswith(("<!--", "<?xml"))):
        return "html", "the text starts with an HTML doctype or <html> element"
    if stripped.startswith("<?xml") or re.match(r"<[A-Za-z_][\w.:-]*[\s>/]", stripped):
        root = re.search(r"<(?!\?|!)([A-Za-z_][\w.:-]*)", stripped)
        if root and root.group(1).lower() == "plist":
            return "plist", "XML whose root element is <plist>"
        if root and root.group(1).lower() == "html":
            return "html", "XML whose root element is <html>"
        if root and root.group(1).lower() == "svg":
            return None                                   # lane B2's drawing
        if root and root.group(1).lower() in _HTML_ROOTS and not stripped.startswith("<?xml") and hint != "xml":
            return "html", f"the text starts with an HTML <{root.group(1).lower()}> element"
        return "xml", "the text starts with an XML declaration or element"
    if stripped.startswith("{"):
        try:
            value = json.loads(stripped)
            if isinstance(value, dict) and "cells" in value and "nbformat" in value:
                return "ipynb", "JSON with 'cells' and 'nbformat' keys (a Jupyter notebook)"
            if hint == "package-json" and isinstance(value, dict):
                return "package-json", "JSON object in a file named package.json"
        except ValueError:
            pass
        if _ndjson(stripped):
            return "ndjson", "every complete line is a JSON object"
        if '"nbformat"' in stripped[:65536] and '"cells"' in stripped[:65536]:
            return "ipynb", "JSON text holding 'cells' and 'nbformat' keys (a Jupyter notebook)"
    m = _SHEBANG.match(head)
    if m:
        word = m.group(1).decode()
        fmt = "python" if word.startswith("python") else "javascript" if word == "node" else "shell"
        return fmt, f"the first line is a #! line naming {word}"
    if hint in ("pyproject", "toml"):
        try:
            tomllib.loads(text)
            return hint, "the text parses as TOML"
        except (tomllib.TOMLDecodeError, ValueError, RecursionError):
            pass
    if hint == "ini":
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read_string(text)
            if parser.sections():
                return "ini", "the text parses as INI with at least one [section]"
        except configparser.Error:
            pass
    if hint in ("python", "javascript", "c", "shell", "yaml", "requirements", "rtf"):
        return None
    if hint != "markdown":
        fmt = _delimited(text)
        if fmt:
            return fmt, ("every line splits into the same number (>= 2) of fields by "
                         + ("TAB" if fmt == "tsv" else "one delimiter"))
    seen = [why for rx, why in _MD_MARKERS if rx.search(text)]
    if len(seen) >= 2 or (seen and hint == "markdown"):
        return "markdown", "Markdown markers: " + ", ".join(seen)
    return None


def refine(detected: Detected, head: bytes, *, name: str | None = None, declared: str | None = None,
           size: int | None = None) -> Detected:
    try:
        return _refine(detected, bytes(head[:64 << 10]), name, declared, size)
    except Exception:
        return detected


def _refine(detected: Detected, head: bytes, name, declared, size) -> Detected:
    if detected.format not in _GENERIC and detected.format != "unknown":
        return detected
    hint = _hint(name)
    for offset, magic, fmt, why in SIGNATURES:
        if head[offset:offset + len(magic)] == magic:
            return _make(fmt, "signature", [why])
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
        fmt, why = _zip_kind(head)
        return _make(fmt, "signature", ["the first 4 bytes are a ZIP signature", why], "zip" if fmt != "zip" else None)
    if detected.format in ("gzip", "bzip2", "xz", "zstd"):
        if _peek_tar(head, detected.format):
            fmt = {"gzip": "tar.gz", "bzip2": "tar.bz2", "xz": "tar.xz", "zstd": "tar.zst"}[detected.format]
            return _make(fmt, "signature", list(detected.reasons) + ["the decompressed start has the ustar magic "
                                                                       "at offset 257"], detected.format)
        return detected
    if detected.format in ("text", "json", "unknown"):
        found = _by_text(head, hint)
        if found:
            fmt, why = found
            reasons = [why]
            if hint and hint != fmt:
                reasons.append(f"the file name suggests {hint}: the structure wins")
            return _make(fmt, "structure", reasons)
        if detected.format == "json":
            return detected
        if hint and hint in FORMATS and detected.format == "text":
            return _make(hint, "declared", [f"the file name suggests {hint}; the text does not contradict it"])
    return detected


def detect(head: bytes, *, name: str | None = None, declared: str | None = None, size: int | None = None) -> Detected:
    """K's `detect` followed by B1's `refine`."""
    head = bytes(head[:64 << 10])
    try:
        found = _kernel_detect(head, name=name, declared=declared, size=size)
    except Exception as error:        # K's detect promises never to raise; this wrapper keeps that promise too
        found = Detected("unknown", "unknown", "application/octet-stream", "none", None,
                         (f"the kernel's detection failed ({type(error).__name__})",))
    return refine(found, head, name=name, declared=declared, size=size)


__all__ = ["EXTENSIONS", "FILENAMES", "FORMATS", "SIGNATURES", "detect", "refine"]
