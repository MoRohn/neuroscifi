"""Detect, inspect, identify (DESIGN §3, ADR-072): `detect(head, name=, declared=, size=) -> Detected`.

Order:
1. Signature: magic numbers at fixed offsets, each confirmed by whatever else the prefix shows (a RIFF form type,
   an ISO BMFF brand, an EBML doctype, a ZIP member name, a TIFF byte order, a tar header checksum, ...).
2. Structure, on a bounded prefix decoded as text (UTF-8/16/32 by BOM and validity): JSON, NDJSON, GeoJSON,
   Jupyter notebook, XML and its root element (svg, html, kml, gpx, rss, atom, plist), HTML, CSV/TSV, Markdown (only
   with real markers), source code by shebang, OBJ and text STL by their line grammar.
3. The declared media type and the file name's extension: hints only. A hint that contradicts the signature or the
   structure loses and `reasons` says so. A hint may only make a generic answer more specific when it is consistent
   with it (text named `.yaml`, a ZIP named `.docx` whose members were not seen), and then `basis` is "declared".

Unknown stays unknown. `detect` sees at most HEAD_MAX bytes and never decompresses or follows anything. It never
raises: every parse of the prefix is bounded and guarded.
"""
from __future__ import annotations

import codecs
import csv
import io
import json
import re
import struct

from .types import Detected

HEAD_MAX = 64 << 10
MIN_STRUCTURE_PREFIX = 32   # structure tests need at least this much of an input known to be longer

# format -> (family, media type): the single table of names this module may answer with.
FORMATS: dict[str, tuple[str, str]] = {
    # images
    "png": ("image", "image/png"), "jpeg": ("image", "image/jpeg"), "gif": ("image", "image/gif"),
    "bmp": ("image", "image/bmp"), "tiff": ("image", "image/tiff"), "webp": ("image", "image/webp"),
    "heic": ("image", "image/heic"), "avif": ("image", "image/avif"), "ico": ("image", "image/vnd.microsoft.icon"),
    "psd": ("image", "image/vnd.adobe.photoshop"), "pbm": ("image", "image/x-portable-bitmap"),
    "pgm": ("image", "image/x-portable-graymap"), "ppm": ("image", "image/x-portable-pixmap"),
    "svg": ("drawing", "image/svg+xml"),
    # sound
    "wav": ("audio", "audio/wav"), "aiff": ("audio", "audio/aiff"), "flac": ("audio", "audio/flac"),
    "vorbis": ("audio", "audio/ogg"), "opus": ("audio", "audio/ogg"), "ogg": ("audio", "audio/ogg"),
    "mp3": ("audio", "audio/mpeg"), "aac": ("audio", "audio/aac"), "m4a": ("audio", "audio/mp4"),
    "midi": ("audio", "audio/midi"),
    # video
    "avi": ("video", "video/x-msvideo"), "mp4": ("video", "video/mp4"), "mov": ("video", "video/quicktime"),
    "3gp": ("video", "video/3gpp"), "mkv": ("video", "video/x-matroska"), "webm": ("video", "video/webm"),
    "ogv": ("video", "video/ogg"),
    # documents
    "pdf": ("document", "application/pdf"), "ps": ("document", "application/postscript"),
    "rtf": ("document", "application/rtf"), "doc": ("document", "application/msword"),
    "xls": ("table", "application/vnd.ms-excel"), "ppt": ("document", "application/vnd.ms-powerpoint"),
    "ole": ("archive", "application/x-ole-storage"),
    "docx": ("document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "xlsx": ("table", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "pptx": ("document", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    "odt": ("document", "application/vnd.oasis.opendocument.text"),
    "ods": ("table", "application/vnd.oasis.opendocument.spreadsheet"),
    "odp": ("document", "application/vnd.oasis.opendocument.presentation"),
    "epub": ("document", "application/epub+zip"), "html": ("document", "text/html"),
    "markdown": ("document", "text/markdown"),
    # archives and compression
    "zip": ("archive", "application/zip"), "jar": ("code", "application/java-archive"),
    "apk": ("code", "application/vnd.android.package-archive"), "npz": ("array", "application/x-npz"),
    "gzip": ("archive", "application/gzip"), "bzip2": ("archive", "application/x-bzip2"),
    "xz": ("archive", "application/x-xz"), "zstd": ("archive", "application/zstd"),
    "7z": ("archive", "application/x-7z-compressed"), "rar": ("archive", "application/vnd.rar"),
    "tar": ("archive", "application/x-tar"),
    # data
    "sqlite": ("database", "application/vnd.sqlite3"), "parquet": ("table", "application/vnd.apache.parquet"),
    "arrow": ("table", "application/vnd.apache.arrow.file"), "feather": ("table", "application/x-feather"),
    "hdf5": ("array", "application/x-hdf5"), "npy": ("array", "application/x-npy"),
    "pickle": ("unknown", "application/x-python-pickle"),
    # executables
    "elf": ("code", "application/x-elf"), "macho": ("code", "application/x-mach-binary"),
    "pe": ("code", "application/vnd.microsoft.portable-executable"), "wasm": ("code", "application/wasm"),
    "class": ("code", "application/java-vm"),
    # spatial
    "glb": ("mesh", "model/gltf-binary"), "ply": ("mesh", "model/x-ply"), "stl": ("mesh", "model/stl"),
    "obj": ("mesh", "model/obj"), "shapefile": ("geo", "application/x-esri-shape"),
    "geojson": ("geo", "application/geo+json"), "kml": ("geo", "application/vnd.google-earth.kml+xml"),
    "gpx": ("geo", "application/gpx+xml"),
    # science
    "dicom": ("image", "application/dicom"), "fits": ("array", "application/fits"),
    "nifti": ("array", "application/x-nifti"),
    # text and structure
    "text": ("text", "text/plain"), "json": ("tree", "application/json"), "ndjson": ("tree", "application/x-ndjson"),
    "xml": ("tree", "application/xml"), "rss": ("tree", "application/rss+xml"),
    "atom": ("tree", "application/atom+xml"), "plist": ("tree", "application/x-plist"),
    "csv": ("table", "text/csv"), "tsv": ("table", "text/tab-separated-values"),
    "ipynb": ("notebook", "application/x-ipynb+json"),
    # project manifests: named by the exact file name AND confirmed by the bytes (see _manifest)
    "pyproject": ("code", "application/toml"), "requirements": ("code", "text/plain"),
    "package-json": ("code", "application/json"),
    "yaml": ("tree", "application/yaml"), "toml": ("tree", "application/toml"), "ini": ("tree", "text/x-ini"),
    # source code
    "python": ("code", "text/x-python"), "javascript": ("code", "text/javascript"),
    "typescript": ("code", "text/x-typescript"), "c": ("code", "text/x-c"), "cpp": ("code", "text/x-c++"),
    "shell": ("code", "text/x-shellscript"), "perl": ("code", "text/x-perl"), "ruby": ("code", "text/x-ruby"),
    "rust": ("code", "text/x-rust"), "go": ("code", "text/x-go"), "java": ("code", "text/x-java"),
    "sql": ("code", "application/sql"), "css": ("code", "text/css"),
}

EXTENSIONS: dict[str, str] = {
    ".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".jpe": "jpeg", ".gif": "gif", ".bmp": "bmp", ".tif": "tiff",
    ".tiff": "tiff", ".webp": "webp", ".heic": "heic", ".heif": "heic", ".avif": "avif", ".ico": "ico", ".psd": "psd",
    ".pbm": "pbm", ".pgm": "pgm", ".ppm": "ppm", ".svg": "svg", ".wav": "wav", ".aif": "aiff", ".aiff": "aiff",
    ".flac": "flac", ".ogg": "ogg", ".oga": "ogg", ".opus": "opus", ".mp3": "mp3", ".aac": "aac", ".m4a": "m4a",
    ".mid": "midi", ".midi": "midi", ".avi": "avi", ".mp4": "mp4", ".m4v": "mp4", ".mov": "mov", ".3gp": "3gp",
    ".mkv": "mkv", ".webm": "webm", ".ogv": "ogv", ".pdf": "pdf", ".ps": "ps", ".eps": "ps", ".rtf": "rtf",
    ".doc": "doc", ".xls": "xls", ".ppt": "ppt", ".docx": "docx", ".xlsx": "xlsx", ".pptx": "pptx", ".odt": "odt",
    ".ods": "ods", ".odp": "odp", ".epub": "epub", ".html": "html", ".htm": "html", ".md": "markdown",
    ".markdown": "markdown", ".zip": "zip", ".jar": "jar", ".apk": "apk", ".npz": "npz", ".gz": "gzip",
    ".tgz": "gzip", ".bz2": "bzip2", ".xz": "xz", ".zst": "zstd", ".7z": "7z", ".rar": "rar", ".tar": "tar",
    ".sqlite": "sqlite", ".sqlite3": "sqlite", ".db": "sqlite", ".parquet": "parquet", ".arrow": "arrow",
    ".feather": "feather", ".h5": "hdf5", ".hdf5": "hdf5", ".npy": "npy", ".pkl": "pickle", ".pickle": "pickle",
    ".so": "elf", ".exe": "pe", ".dll": "pe", ".dylib": "macho", ".wasm": "wasm", ".class": "class", ".glb": "glb",
    ".ply": "ply", ".stl": "stl", ".obj": "obj", ".shp": "shapefile", ".geojson": "geojson", ".kml": "kml",
    ".gpx": "gpx", ".dcm": "dicom", ".fits": "fits", ".fit": "fits", ".nii": "nifti", ".txt": "text",
    ".json": "json", ".ndjson": "ndjson", ".jsonl": "ndjson", ".xml": "xml", ".rss": "rss", ".atom": "atom",
    ".plist": "plist", ".csv": "csv", ".tsv": "tsv", ".tab": "tsv", ".ipynb": "ipynb", ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml", ".ini": "ini", ".cfg": "ini", ".conf": "ini", ".py": "python", ".js": "javascript",
    ".mjs": "javascript", ".cjs": "javascript", ".ts": "typescript", ".c": "c", ".h": "c", ".cc": "cpp",
    ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".sh": "shell", ".bash": "shell", ".zsh": "shell", ".pl": "perl",
    ".rb": "ruby", ".rs": "rust", ".go": "go", ".java": "java", ".sql": "sql", ".css": "css",
}

_ALIASES = {"text/xml": "xml", "image/jpg": "jpeg", "image/pjpeg": "jpeg", "audio/x-wav": "wav", "audio/wave": "wav",
            "audio/vnd.wave": "wav", "audio/x-aiff": "aiff", "audio/x-flac": "flac", "audio/mp3": "mp3",
            "audio/x-midi": "midi", "audio/opus": "opus", "application/x-gzip": "gzip", "application/x-zip-compressed": "zip",
            "application/x-tar": "tar", "application/x-sqlite3": "sqlite", "application/x-pdf": "pdf",
            "text/x-markdown": "markdown", "application/x-yaml": "yaml", "text/yaml": "yaml",
            "application/javascript": "javascript", "application/x-sh": "shell", "application/x-python": "python",
            "image/x-icon": "ico", "application/x-pickle": "pickle", "application/x-msdownload": "pe",
            "application/ld+json": "json", "application/xhtml+xml": "html", "application/x-ndjson": "ndjson",
            "application/jsonl": "ndjson", "text/x-python-script": "python", "model/x-stl": "stl"}
DECLARED: dict[str, str] = {**{media: fmt for fmt, (_, media) in FORMATS.items()
                               if media not in ("audio/ogg", "text/plain")}, "audio/ogg": "ogg", "text/plain": "text",
                            **_ALIASES}

# Generic answers a consistent hint may refine (basis then becomes "declared").
_REFINES = {
    "text": {"yaml", "toml", "ini", "markdown", "csv", "tsv", "python", "javascript", "typescript", "c", "cpp",
             "shell", "perl", "ruby", "rust", "go", "java", "sql", "css", "obj", "ndjson"},
    "zip": {"docx", "xlsx", "pptx", "odt", "ods", "odp", "epub", "jar", "apk", "npz"},
    "ole": {"doc", "xls", "ppt"},
    "xml": {"svg", "kml", "gpx", "rss", "atom", "plist"},
    "json": {"geojson", "ipynb"},
}


def _u16(b: bytes, off: int, big: bool = False) -> int:
    return struct.unpack_from(">H" if big else "<H", b, off)[0]


def _u32(b: bytes, off: int, big: bool = False) -> int:
    return struct.unpack_from(">I" if big else "<I", b, off)[0]


# ---------------------------------------------------------------------------------------------------------------
# Signatures. Each test takes (head, size) and returns (format, reasons, container) or None.


def _riff(h, size):
    if h[:4] != b"RIFF" or len(h) < 12:
        return None
    form = h[8:12]
    fmt = {b"WAVE": "wav", b"AVI ": "avi", b"WEBP": "webp"}.get(form)
    if fmt is None:
        return None
    return fmt, [f"RIFF container with form type {form.decode('latin-1').strip()!r}"], "riff"


def _iff(h, size):
    if h[:4] == b"FORM" and len(h) >= 12 and h[8:12] in (b"AIFF", b"AIFC"):
        return "aiff", [f"IFF FORM container of type {h[8:12].decode()!r}"], "iff"
    return None


_HEIF = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1"}
_AVIF = {b"avif", b"avis"}
_MP4 = {b"isom", b"iso2", b"iso3", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1", b"dash", b"M4V ", b"M4VP",
        b"f4v ", b"mmp4", b"MSNV", b"NDAS"}


def _isobmff(h, size):
    if len(h) < 12 or h[4:8] != b"ftyp":
        return None
    box = _u32(h, 0, True)
    if box < 16 or box > 4096:
        return None
    major = h[8:12]
    brands = [major] + [h[i:i + 4] for i in range(16, min(box, len(h)) - 3, 4)]
    label = major.decode("latin-1")
    if any(b in _AVIF for b in brands):
        fmt = "avif"
    elif major in _HEIF or (major == b"mif1" and any(b in _HEIF for b in brands)):
        fmt = "heic"
    elif major == b"qt  ":
        fmt = "mov"
    elif major in (b"M4A ", b"M4B ", b"M4P "):
        fmt = "m4a"
    elif major[:3] in (b"3gp", b"3g2"):
        fmt = "3gp"
    elif major in _MP4:
        fmt = "mp4"
    else:
        return None
    return fmt, [f"ISO base media file with 'ftyp' box, major brand {label!r}"], "isobmff"


def _vint(h, i):
    """EBML variable-length integer at i: (value, length) or None."""
    if i >= len(h) or h[i] == 0:
        return None
    first = h[i]
    length = 8 - first.bit_length() + 1
    if i + length > len(h):
        return None
    value = first & ((1 << (8 - length)) - 1)
    for b in h[i + 1:i + length]:
        value = (value << 8) | b
    return value, length


def _ebml(h, size):
    if h[:4] != b"\x1a\x45\xdf\xa3":
        return None
    i = h.find(b"\x42\x82", 4, 64)
    if i < 0:
        return None
    v = _vint(h, i + 2)
    if v is None or v[0] > 32:
        return None
    doctype = h[i + 2 + v[1]:i + 2 + v[1] + v[0]].rstrip(b"\x00")
    fmt = {b"webm": "webm", b"matroska": "mkv"}.get(doctype)
    if fmt is None:
        return None
    return fmt, [f"EBML header with DocType {doctype.decode()!r}"], "ebml"


def _ogg(h, size):
    if h[:4] != b"OggS" or len(h) < 28 or h[4] != 0:
        return None
    start = 27 + h[26]
    payload = h[start:start + 8]
    if payload.startswith(b"\x01vorbis"):
        return "vorbis", ["Ogg page whose first packet is a Vorbis identification header"], "ogg"
    if payload.startswith(b"OpusHead"):
        return "opus", ["Ogg page whose first packet is an OpusHead"], "ogg"
    if payload.startswith(b"\x80theora"):
        return "ogv", ["Ogg page whose first packet is a Theora identification header"], "ogg"
    if len(payload) < 8:
        return None
    return "ogg", ["Ogg page signature 'OggS' (first packet's codec not recognised)"], "ogg"


_MP3_BITRATES = {  # (version_is_mpeg1, layer) -> kbit/s by index
    (True, 3): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (False, 3): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
_MP3_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def _mp3_frame(h, i):
    """Length of the MPEG audio Layer III frame starting at i, or None."""
    if i + 4 > len(h) or h[i] != 0xFF or (h[i + 1] & 0xE0) != 0xE0:
        return None
    version = (h[i + 1] >> 3) & 3
    layer = (h[i + 1] >> 1) & 3
    bitrate_idx, rate_idx, pad = h[i + 2] >> 4, (h[i + 2] >> 2) & 3, (h[i + 2] >> 1) & 1
    if version == 1 or layer != 1 or bitrate_idx in (0, 15) or rate_idx == 3:
        return None
    mpeg1 = version == 3
    bitrate = _MP3_BITRATES[(mpeg1, 3)][bitrate_idx] * 1000
    rate = _MP3_RATES[version][rate_idx]
    return (144 if mpeg1 else 72) * bitrate // rate + pad


def _adts_frame(h, i):
    if i + 7 > len(h) or h[i] != 0xFF or (h[i + 1] & 0xF6) != 0xF0 or ((h[i + 2] >> 2) & 0xF) > 12:
        return None
    length = ((h[i + 3] & 3) << 11) | (h[i + 4] << 3) | (h[i + 5] >> 5)
    return length if length >= 7 else None


def _mpeg_audio(h, size):
    if h[:3] == b"ID3" and len(h) >= 10 and h[3] in (2, 3, 4) and all(b < 0x80 for b in h[6:10]):
        return "mp3", ["ID3v2 tag header (a tag that precedes MPEG audio)"], None
    for name, frame in (("mp3", _mp3_frame), ("aac", _adts_frame)):
        first = frame(h, 0)
        if first and frame(h, first) and frame(h, first + frame(h, first)):
            what = "MPEG audio Layer III" if name == "mp3" else "AAC ADTS"
            return name, [f"three consecutive {what} frame headers at the computed frame lengths"], None
    return None


def _ole(h, size):
    if h[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return None
    for stream, fmt in (("WordDocument", "doc"), ("Workbook", "xls"), ("Book", "xls"),
                        ("PowerPoint Document", "ppt")):
        if stream.encode("utf-16-le") + b"\x00\x00" in h:
            return fmt, ["OLE compound file signature", f"a directory entry named {stream!r} in the prefix"], "ole"
    return "ole", ["OLE compound file signature (no known stream name in the prefix)"], None


def _zip_members(h) -> tuple[list[str], bytes | None]:
    """Member names from the local file headers visible in the prefix, and the stored 'mimetype' content."""
    names, mimetype, i = [], None, 0
    while len(names) < 256 and i + 30 <= len(h) and h[i:i + 4] == b"PK\x03\x04":
        flags, method = _u16(h, i + 6), _u16(h, i + 8)
        csize, nlen, xlen = _u32(h, i + 18), _u16(h, i + 26), _u16(h, i + 28)
        name = h[i + 30:i + 30 + nlen].decode("utf-8", "replace")
        names.append(name)
        data_at = i + 30 + nlen + xlen
        if name == "mimetype" and method == 0:
            mimetype = h[data_at:data_at + min(csize, 128)]
        if flags & 0x08:
            break   # sizes follow the data: the next header cannot be found without decompressing
        i = data_at + csize
    if len(names) < 256:   # also any other local headers that happen to lie in the prefix
        for m in re.finditer(rb"PK\x03\x04", h[i:]):
            j = i + m.start()
            if j + 30 <= len(h):
                nlen = _u16(h, j + 26)
                names.append(h[j + 30:j + 30 + nlen].decode("utf-8", "replace"))
            if len(names) >= 256:
                break
    return names, mimetype


_ODF = {b"application/vnd.oasis.opendocument.text": "odt", b"application/vnd.oasis.opendocument.spreadsheet": "ods",
        b"application/vnd.oasis.opendocument.presentation": "odp", b"application/epub+zip": "epub"}


def _zip(h, size):
    if h[:4] not in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return None
    if h[:4] == b"PK\x03\x04" and len(h) >= 30 and _u16(h, 8) not in (0, 1, 6, 8, 9, 12, 14, 93, 95, 98, 99):
        return None
    names, mimetype = _zip_members(h)
    if mimetype is not None:
        for prefix, fmt in _ODF.items():
            if mimetype.startswith(prefix):
                return fmt, ["ZIP local file header", f"a stored 'mimetype' member reading {prefix.decode()!r}"], "zip"
    rules = (("docx", lambda n: n.startswith("word/")), ("xlsx", lambda n: n.startswith("xl/")),
             ("pptx", lambda n: n.startswith("ppt/")),
             ("apk", lambda n: n in ("AndroidManifest.xml", "classes.dex")),
             ("jar", lambda n: n == "META-INF/MANIFEST.MF" or n.endswith(".class")))
    for fmt, rule in rules:
        hit = next((n for n in names if rule(n)), None)
        if hit is not None:
            return fmt, ["ZIP local file header", f"a member named {hit[:80]!r} in the prefix"], "zip"
    if names and all(n.endswith(".npy") for n in names):
        return "npz", ["ZIP local file header", "every member seen is a .npy array"], "zip"
    return "zip", ["ZIP signature" + (f"; {len(names)} member name(s) seen, none specific" if names else "")], None


def _tar(h, size):
    if len(h) < 512 or h[257:262] != b"ustar":
        return None
    try:
        stored = int(h[148:156].split(b"\x00")[0].strip() or b"-1", 8)
    except ValueError:
        return None
    computed = sum(h[:148]) + 8 * 32 + sum(h[156:512])
    if stored != computed:
        return None
    return "tar", ["'ustar' magic at offset 257 and a valid header checksum"], None


def _bmp(h, size):
    if h[:2] != b"BM" or len(h) < 26:
        return None
    if _u32(h, 14) not in (12, 40, 52, 56, 64, 108, 124) or _u32(h, 10) < 26:
        return None
    return "bmp", ["'BM' signature and a known DIB header size at offset 14"], None


def _ico(h, size):
    if h[:4] != b"\x00\x00\x01\x00" or len(h) < 22:
        return None
    count = _u16(h, 4)
    if not 1 <= count <= 256 or h[9] != 0 or _u16(h, 10) > 1 or _u32(h, 18) < 6 + 16 * count:
        return None
    return "ico", [f"ICO header with {count} image entr{'y' if count == 1 else 'ies'}"], None


def _netpbm(h, size):
    m = re.match(rb"P([1-6])[ \t\r\n]+(?:#[^\n]*\n[ \t\r\n]*)*\d+[ \t\r\n]+(?:#[^\n]*\n[ \t\r\n]*)*\d+", h[:512])
    if m is None:
        return None
    fmt = {b"1": "pbm", b"4": "pbm", b"2": "pgm", b"5": "pgm", b"3": "ppm", b"6": "ppm"}[m.group(1)]
    return fmt, [f"NetPBM magic 'P{m.group(1).decode()}' followed by a width and a height"], None


def _bzip2(h, size):
    if h[:3] != b"BZh" or len(h) < 4 or not 0x31 <= h[3] <= 0x39:
        return None
    if len(h) >= 10 and h[4:10] not in (b"\x31\x41\x59\x26\x53\x59", b"\x17\x72\x45\x38\x50\x90"):
        return None
    return "bzip2", ["'BZh' magic with a block size digit" + (" and a block magic" if len(h) >= 10 else "")], None


def _class_or_fat(h, size):
    if h[:4] != b"\xca\xfe\xba\xbe" or len(h) < 8:
        return None
    n_arch = _u32(h, 4, True)
    if 1 <= n_arch <= 30:
        return "macho", [f"Mach-O universal header with {n_arch} architecture(s)"], None
    major = _u16(h, 6, True)
    if 45 <= major <= 80:
        return "class", [f"Java class file magic with class-file version {major}"], None
    return None


def _pe(h, size):
    if h[:2] != b"MZ" or len(h) < 64:
        return None
    at = _u32(h, 0x3C)
    if at + 4 <= len(h) and h[at:at + 4] == b"PE\x00\x00":
        return "pe", ["'MZ' header whose offset at 0x3C points to a 'PE' signature"], None
    return None


def _pickle(h, size):
    if len(h) >= 3 and h[0] == 0x80 and 2 <= h[1] <= 5 and h[2] in b"\x95}](cKXqN)\x8c\x88\x89J\x8a\x80U.\x94":
        return "pickle", [f"Python pickle protocol {h[1]} opcode sequence"], None
    if re.match(rb"\((?:dp|lp)[01]\n|c(?:__builtin__|copy_reg|builtins|numpy[\w.]*)\n", h[:64]):
        return "pickle", ["Python pickle protocol 0 opcode sequence"], None
    return None


def _nifti(h, size):
    if len(h) >= 348 and h[344:348] in (b"n+1\x00", b"ni1\x00") and 348 in (_u32(h, 0), _u32(h, 0, True)):
        return "nifti", ["NIfTI-1 header size 348 and magic at offset 344"], None
    if len(h) >= 12 and h[4:8] in (b"n+2\x00", b"ni2\x00") and 540 in (_u32(h, 0), _u32(h, 0, True)):
        return "nifti", ["NIfTI-2 header size 540 and magic at offset 4"], None
    return None


def _stl_binary(h, size):
    if size is None or len(h) < 84 or size < 84:
        return None
    n = _u32(h, 80)
    if 84 + 50 * n != size or n == 0:
        return None
    return "stl", [f"84-byte header and triangle count {n} that match the size exactly (84 + 50 x {n})"], None


def _shapefile(h, size):
    if len(h) >= 36 and h[:4] == b"\x00\x00\x27\x0a" and _u32(h, 28) == 1000:
        return "shapefile", ["shapefile file code 9994 and version 1000"], None
    return None


def _ply(h, size):
    if re.match(rb"ply\r?\nformat (ascii|binary_little_endian|binary_big_endian) 1\.0[ \t]*\r?\n", h[:64]):
        return "ply", ["'ply' magic line followed by a format line"], None
    return None


def _glb(h, size):
    if h[:4] == b"glTF" and len(h) >= 12 and _u32(h, 4) == 2:
        return "glb", ["'glTF' magic with container version 2"], None
    return None


def _npy(h, size):
    if h[:6] == b"\x93NUMPY" and len(h) >= 8 and 1 <= h[6] <= 3:
        return "npy", [f"NumPy array file magic, format version {h[6]}.{h[7]}"], None
    return None


def _tiff(h, size):
    for magic, order in ((b"II*\x00", "little-endian"), (b"MM\x00*", "big-endian"),
                         (b"II+\x00", "little-endian BigTIFF"), (b"MM\x00+", "big-endian BigTIFF")):
        if h[:4] == magic:
            return "tiff", [f"TIFF header, {order} byte order"], None
    return None


def _fixed(offset: int, magic: bytes, fmt: str, why: str, container: str | None = None, *, min_len: int = 0):
    def test(h, size):
        if len(h) >= max(min_len, offset + len(magic)) and h[offset:offset + len(magic)] == magic:
            return fmt, [why], container
        return None
    test.__name__ = f"_sig_{fmt}"
    return test


def _psd(h, size):
    if h[:4] == b"8BPS" and len(h) >= 6 and _u16(h, 4, True) in (1, 2):
        return "psd", ["'8BPS' signature with version 1 or 2"], None
    return None


def _midi(h, size):
    if h[:8] == b"MThd\x00\x00\x00\x06":
        return "midi", ["'MThd' chunk of length 6"], None
    return None


def _wasm(h, size):
    if h[:8] == b"\x00asm\x01\x00\x00\x00":
        return "wasm", ["WebAssembly magic and version 1"], None
    return None


_PDF_OBJECT = re.compile(rb"(?:^|[\r\n\s])\d{1,10}\s+\d{1,5}\s+obj\b")


def _pdf(h, size):
    """'%PDF-n.n' within the first 1024 bytes AND what every PDF has after it (lane FX, item 1): an indirect object
    ('1 0 obj') in the prefix; or, at offset 0, the header on a line of its own not followed by prose (a cut head
    of a PDF). A header followed by words is words ('%PDF-1.7 was mentioned'); one buried in other bytes is a
    coincidence, not a PDF."""
    at = h.find(b"%PDF-", 0, 1024)
    if at < 0 or not re.match(rb"%PDF-\d\.\d", h[at:at + 8]):
        return None
    if _PDF_OBJECT.search(h, at + 8):
        return "pdf", ["'%PDF-' header" + (f" at offset {at}" if at else "") + " and an indirect object after it"], None
    if at == 0 and re.match(rb"%PDF-\d\.\d[ \t]*(?:\r?\n|\r|$)", h[:16]) and not re.match(
            rb"%PDF-\d\.\d[ \t]*\r?\n[ \t]*[A-Za-z]{2,}[ \t]+[A-Za-z]{2,}", h[:64]):
        return "pdf", ["'%PDF-' header on a line of its own at the start (no object in the prefix)"], None
    return None


def _fits(h, size):
    if re.match(rb"SIMPLE  = {20}T", h[:30]):
        return "fits", ["FITS primary header card 'SIMPLE = T'"], None
    return None


def _gzip(h, size):
    if h[:3] == b"\x1f\x8b\x08" and (len(h) < 4 or h[3] < 0x20):
        return "gzip", ["gzip magic number with the deflate method byte"], None
    return None


SIGNATURES = [
    _fixed(0, b"\x89PNG\r\n\x1a\n", "png", "the first 8 bytes are the PNG signature"),
    _fixed(0, b"\xff\xd8\xff", "jpeg", "the first 3 bytes are a JPEG start-of-image marker"),
    _fixed(0, b"GIF87a", "gif", "'GIF87a' signature"), _fixed(0, b"GIF89a", "gif", "'GIF89a' signature"),
    _tiff, _riff, _iff, _isobmff, _ebml, _ogg, _psd, _midi, _wasm, _pdf, _fits, _gzip, _bzip2, _ole, _zip, _tar,
    _fixed(0, b"fLaC", "flac", "'fLaC' stream marker"),
    _fixed(0, b"%!PS", "ps", "'%!PS' PostScript header"),
    _fixed(0, b"\xc5\xd0\xd3\xc6", "ps", "DOS EPS binary header"),
    _fixed(0, b"{\\rtf", "rtf", "'{\\rtf' header"),
    _fixed(0, b"\xfd7zXZ\x00", "xz", "the first 6 bytes are the xz magic number"),
    _fixed(0, b"\x28\xb5\x2f\xfd", "zstd", "the first 4 bytes are the Zstandard frame magic number"),
    _fixed(0, b"7z\xbc\xaf\x27\x1c", "7z", "7z signature"),
    _fixed(0, b"Rar!\x1a\x07\x00", "rar", "RAR 4 signature"), _fixed(0, b"Rar!\x1a\x07\x01\x00", "rar", "RAR 5 signature"),
    _fixed(0, b"SQLite format 3\x00", "sqlite", "'SQLite format 3' header"),
    _fixed(0, b"bplist00", "plist", "'bplist00' binary property list header"),
    _fixed(0, b"PAR1", "parquet", "'PAR1' magic at the start", min_len=8),
    _fixed(0, b"ARROW1\x00\x00", "arrow", "'ARROW1' file magic"),
    _fixed(0, b"FEA1", "feather", "Feather version 1 magic", min_len=8),
    _fixed(0, b"\x89HDF\r\n\x1a\n", "hdf5", "HDF5 superblock signature"),
    _npy, _pickle,
    _fixed(0, b"\x7fELF", "elf", "ELF magic", min_len=16),
    _fixed(0, b"\xfe\xed\xfa\xce", "macho", "Mach-O 32-bit big-endian magic"),
    _fixed(0, b"\xfe\xed\xfa\xcf", "macho", "Mach-O 64-bit big-endian magic"),
    _fixed(0, b"\xce\xfa\xed\xfe", "macho", "Mach-O 32-bit little-endian magic"),
    _fixed(0, b"\xcf\xfa\xed\xfe", "macho", "Mach-O 64-bit little-endian magic"),
    _class_or_fat, _pe, _glb, _ply, _stl_binary,
    _fixed(128, b"DICM", "dicom", "'DICM' at offset 128 after the DICOM preamble"),
    _nifti, _shapefile, _bmp, _ico, _netpbm, _mpeg_audio,
]


# ---------------------------------------------------------------------------------------------------------------
# Structure


_BOMS = ((b"\x00\x00\xfe\xff", "utf-32-be"), (b"\xff\xfe\x00\x00", "utf-32-le"), (b"\xef\xbb\xbf", "utf-8"),
         (b"\xfe\xff", "utf-16-be"), (b"\xff\xfe", "utf-16-le"))
_BAD_CONTROL = re.compile(r"[\x00-\x08\x0e-\x1a\x1c-\x1f\x7f-\x9f]")


def _as_text(h: bytes, complete: bool) -> tuple[str, str] | None:
    """(text, how) when the prefix is text in a Unicode encoding, else None."""
    for bom, enc in _BOMS:
        if h.startswith(bom):
            body, how = h[len(bom):], f"{enc.upper()} byte-order mark"
            break
    else:
        body, enc, how = h, "utf-8", "valid UTF-8"
        if len(h) >= 4 and b"\x00" in h:
            odd, even = h[1::2], h[0::2]
            if odd.count(0) >= 0.9 * len(odd) and even.count(0) < 0.1 * len(even):
                enc, how = "utf-16-le", "UTF-16LE without a byte-order mark (every second byte is zero)"
            elif even.count(0) >= 0.9 * len(even) and odd.count(0) < 0.1 * len(odd):
                enc, how = "utf-16-be", "UTF-16BE without a byte-order mark (every first byte is zero)"
            else:
                return None
    unit = 4 if enc.startswith("utf-32") else 2 if enc.startswith("utf-16") else 1
    if not complete:
        body = body[:len(body) - len(body) % unit]
    try:
        text = codecs.getincrementaldecoder(enc)("strict").decode(body, final=complete)
    except (UnicodeDecodeError, ValueError):
        return None
    if not text.strip() or _BAD_CONTROL.search(text):
        return None
    return text, how


def _json_prefix(text: str, complete: bool) -> str | None:
    """'complete' when the text parses as JSON, 'prefix' when it is a plausible truncated JSON document."""
    try:
        json.loads(text)
        return "complete"
    except RecursionError:
        return "prefix" if not complete else None
    except ValueError as error:
        if complete:
            return None
        msg = getattr(error, "msg", "")
        body = text.rstrip()
        if len(body) >= 8 and (msg.startswith("Unterminated string") or
                               (msg.startswith("Expecting") and error.pos >= len(body))):
            return "prefix"
        return None


def _ndjson(lines: list[str], complete: bool) -> bool:
    lines = [ln for ln in lines if ln.strip()]
    if not complete and lines:
        lines = lines[:-1]
    if len(lines) < 2:
        return False
    for line in lines[:200]:
        try:
            if not isinstance(json.loads(line), (dict, list)):
                return False
        except (ValueError, RecursionError):
            return False
    return True


_XML_PROLOG = re.compile(r"\s*(?:<\?xml[^>]*\?>|<!--.*?-->|<\?[^>]*\?>|<!DOCTYPE[^>\[]*(?:\[.*?\])?\s*>|\s)*",
                         re.S | re.I)
_HTML_TAGS = ("html", "head", "body", "div", "p", "title", "meta", "table", "span", "a", "script", "style", "link",
              "h1", "h2", "h3", "ul", "ol", "section", "article", "header", "nav")


_HTML_EVIDENCE = re.compile(r"<(?:head|body|div|p|br|span|table|tr|td|script|style|meta|link|title|h[1-6]|ul|ol|li|"
                            r"img|section|article|nav|header|footer)[\s/>]", re.I)


def _xml_like(text: str, complete: bool = True) -> tuple[str, list[str]] | None:
    stripped = text.lstrip()
    if not stripped.startswith("<"):
        return None
    if re.match(r"<!doctype\s+html", stripped, re.I):
        return "html", ["an HTML doctype"]
    m = _XML_PROLOG.match(stripped)
    rest = stripped[m.end():] if m else stripped
    tag = re.match(r"<([A-Za-z_][\w.:-]*)[\s/>]", rest)
    if tag is None:
        return None
    root = tag.group(1)
    local = root.split(":")[-1].lower()
    declared = stripped.startswith("<?xml")
    reasons = [("an XML declaration and " if declared else "") + f"the root element <{root[:40]}>"]
    if "<!DOCTYPE" in stripped[:m.end() if m else 0].upper() and local != "html":
        reasons.append("it declares a DOCTYPE (a decoder will refuse it)")
    if local in ("html", "head", "body"):
        return "html", reasons
    if not declared and local in _HTML_TAGS and _HTML_EVIDENCE.search(rest[:HEAD_MAX]):
        return "html", reasons + ["it uses tags that only HTML defines"]
    if local == "svg":
        return "svg", reasons
    if local == "feed" and "http://www.w3.org/2005/Atom" in rest[:2048]:
        return "atom", reasons
    fmt = {"kml": "kml", "gpx": "gpx", "rss": "rss", "plist": "plist", "rdf": "rss"}.get(local)
    if fmt:
        return fmt, reasons
    if not declared and not re.search(r"</\s*" + re.escape(root) + r"\s*>|/>", rest[:HEAD_MAX]):
        # a lone '<word' with no closing tag is not enough. A prefix cut from a longer document cannot hold the root's
        # end tag: there, an element nested in the root and closed is the evidence.
        if complete or not re.search(r"<([A-Za-z_][\w.:-]*)[^<>]*>[^<]*</\1\s*>", rest[tag.end():HEAD_MAX]):
            return None
        reasons.append("a prefix of a longer document: the root is not closed in it, a nested element is")
    return "xml", reasons


def _delimited(lines: list[str], complete: bool) -> tuple[str, list[str]] | None:
    lines = [ln for ln in lines if ln.strip()]
    if not complete and len(lines) > 2:
        lines = lines[:-1]
    if len(lines) < 2:
        return None
    sample = "\n".join(lines[:200])
    for delim, fmt in (("\t", "tsv"), (",", "csv"), (";", "csv"), ("|", "csv")):
        if delim not in lines[0]:
            continue
        try:
            rows = list(csv.reader(io.StringIO(sample), delimiter=delim))
        except csv.Error:
            continue
        widths = {len(r) for r in rows if r}
        if len(widths) == 1 and min(widths) >= 2:
            n = widths.pop()
            name = {"\t": "tab", ",": "comma", ";": "semicolon", "|": "pipe"}[delim]
            return fmt, [f"{len(rows)} lines with the same {n} {name}-separated fields"]
    return None


_MD_MARKERS = (("an ATX heading", re.compile(r"^#{1,6} \S", re.M)), ("a fenced code block", re.compile(r"^```", re.M)),
               # lane FX4 (review L3): [ \t]* never crosses a line, so a file of blank lines is not quadratic
               ("a Markdown table rule", re.compile(r"^\|?[ \t]*:?-{3,}:?[ \t]*(\|[ \t]*:?-{3,}:?[ \t]*)+\|?[ \t]*$",
                                                    re.M)),
               ("a Markdown link", re.compile(r"\[[^\]\n\[]{1,200}\]\([^)\s]{1,500}\)")),
               ("a task list item", re.compile(r"^[ \t]*[-*] \[[ xX]\] ", re.M)),
               ("bold text", re.compile(r"\*\*[^*\n]{1,200}\*\*")), ("a bullet list", re.compile(r"^[-*+] \S.*\n[-*+] \S", re.M)))


_ATX_BLOCK = re.compile(r"(?:\A|\n[ \t]*\n)#{1,6} \S[^\n]*(?=\n[ \t]*\n|\n?\Z)")


def _markdown(text: str, headings_alone: bool = False) -> tuple[str, list[str]] | None:
    seen = [what for what, pattern in _MD_MARKERS if pattern.search(text)]
    if len(seen) >= 2:
        return "markdown", ["Markdown markers: " + ", ".join(seen)]
    # Lane FX3 (class U-C): a document whose only marker is its headings. Two or more ATX headings, each a block of
    # its own (a blank line or the file's edge on both sides), are Markdown, unless a hint names another text
    # format whose comments also begin with '#' (YAML, TOML, INI, code): `headings_alone` is False then.
    if headings_alone and len(_ATX_BLOCK.findall(text.replace("\r\n", "\n"))) >= 2:
        return "markdown", ["Markdown markers: two or more ATX headings, each a block of its own"]
    return None


_PY_START = re.compile(r"^(?:def|class|import|from|async\s+def)\s", re.M)


def _python(text: str) -> tuple[str, list[str]] | None:
    """Lane FX3 (class U-B): text that parses as a Python module holding at least one function, class or import
    is Python source, whatever it is named. The text is parsed by `ast.parse` (syntax only); nothing is compiled,
    imported or run."""
    if not _PY_START.search(text):
        return None
    import ast
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    kinds = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)
    if not any(isinstance(node, kinds) for node in tree.body):
        return None
    return "python", ["it parses as a Python module with a function, a class or an import (syntax only, never run)"]


_SHEBANG = (("python", "python"), ("node", "javascript"), ("deno", "javascript"), ("bash", "shell"),
            ("zsh", "shell"), ("dash", "shell"), ("ksh", "shell"), ("sh", "shell"), ("perl", "perl"), ("ruby", "ruby"))


def _shebang(text: str) -> tuple[str, list[str]] | None:
    m = re.match(r"#!\s*(\S+)(?:[ \t]+(\S+))?", text)
    if m is None:
        return None
    prog = m.group(1).rsplit("/", 1)[-1]
    if prog == "env" and m.group(2):
        prog = m.group(2).rsplit("/", 1)[-1]
    for start, fmt in _SHEBANG:
        if re.fullmatch(re.escape(start) + r"[\d.]*", prog):
            return fmt, [f"a '#!' line naming {prog[:20]!r}"]
    return None


_OBJ_LINE = re.compile(r"^(?:v(?: +-?[\d.eE+-]+){3,4}|vt(?: +-?[\d.eE+-]+){1,3}|vn(?: +-?[\d.eE+-]+){3}|"
                       r"f(?: +-?\d+(?:/-?\d*){0,2}){3,}|[og] .*|s .*|l(?: +\d+)+|mtllib .*|usemtl .*|#.*)\s*$")


def _obj(lines: list[str], complete: bool) -> tuple[str, list[str]] | None:
    body = [ln for ln in lines if ln.strip()]
    if not complete and body:
        body = body[:-1]
    vertices = sum(1 for ln in body if ln.startswith("v "))
    if vertices >= 3 and all(_OBJ_LINE.match(ln.strip()) for ln in body):
        return "obj", [f"Wavefront OBJ line grammar on every line ({vertices} vertex lines)"]
    return None


_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_STL_FACET = re.compile(rf"^\s*facet\s+normal\s+{_NUM}\s+{_NUM}\s+{_NUM}\s*$", re.M)
_STL_VERTEX = re.compile(rf"^\s*vertex\s+{_NUM}\s+{_NUM}\s+{_NUM}\s*$", re.M)


def _structure(h: bytes, complete: bool, headings_alone: bool = False,
               markdown_named: bool = False) -> tuple[str, list[str]] | None:
    decoded = _as_text(h, complete)
    if decoded is None:
        return None
    text, how = decoded
    head = [f"the bytes are text ({how})"]
    stripped = text.lstrip()
    lines = text[:HEAD_MAX].splitlines()
    if stripped[:1] in "{[":
        if _ndjson(lines, complete):
            return "ndjson", head + ["two or more lines, each a JSON object or array"]
        kind = _json_prefix(stripped, complete)
        if kind is not None:
            reasons = head + (["it parses as JSON"] if kind == "complete" else
                              ["it is consistent with the beginning of a JSON document (the prefix ends inside it)"])
            first = stripped[:4096]
            if re.search(r'"nbformat"\s*:\s*\d', stripped) and re.search(r'"cells"\s*:', stripped):
                return "ipynb", reasons + ["it has 'nbformat' and 'cells' keys"]
            if stripped.startswith("{") and re.search(r'"type"\s*:\s*"(?:FeatureCollection|Feature)"', first) \
                    and re.search(r'"(?:features|geometry)"\s*:', first):
                return "geojson", reasons + ["a GeoJSON 'type' member"]
            return "json", reasons
    if stripped.startswith("<"):
        xml = _xml_like(text, complete)
        if xml is not None:
            return xml[0], head + xml[1]
    if stripped.startswith("#!"):
        code = _shebang(stripped)
        if code is not None:
            return code[0], head + code[1]
    if complete and not markdown_named:
        code = _python(text)
        if code is not None:
            return code[0], head + code[1]
    if re.match(r"solid[ \t\r\n]", stripped) and _STL_FACET.search(text) and _STL_VERTEX.search(text):
        return "stl", head + ["'solid' followed by 'facet normal' lines (text STL)"]
    for test in (_obj, _delimited):
        found = test(lines, complete)
        if found is not None:
            return found[0], head + found[1]
    md = _markdown(text, headings_alone)
    if md is not None:
        return md[0], head + md[1]
    return "text", head


# ---------------------------------------------------------------------------------------------------------------
# Hints and the answer


def _hints(name: str | None, declared: str | None) -> list[tuple[str, str]]:
    """(format, reason) for each hint that names a known format: the declared type first, then the extension."""
    out = []
    if isinstance(declared, str) and declared:
        media = declared.split(";")[0].strip().lower()
        if media in DECLARED:
            out.append((DECLARED[media], f"the declared type is {media[:80]}"))
    if isinstance(name, str) and name:
        lower = name.lower().rsplit("/", 1)[-1]
        dot = lower.rfind(".")
        if dot > 0 and lower[dot:] in EXTENSIONS:
            out.append((EXTENSIONS[lower[dot:]], f"the file name ends in {lower[dot:]}"))
    return out


def _make(fmt: str, basis: str, reasons: list[str], container: str | None = None) -> Detected:
    family, media = FORMATS.get(fmt, ("unknown", "application/octet-stream"))
    return Detected(family, fmt, media, basis, container, tuple(reasons))


UNKNOWN_REASON = "no signature or structure was recognised"

# Formats whose files are text by definition (lane FX, item 1). Only these may be named on a hint (a declared type
# or an extension) when nothing in the bytes is recognised, and then only when the bytes read as text in a
# single-byte encoding (an XML file in ISO-8859-1, a CSV in Windows-1252); the format's reader must then confirm
# the hint by decoding (`adapters.decode`). Every other format has a signature or a structure test here, so bytes
# that show neither CONTRADICT a hint naming it: a name or a declared type alone never identifies a file.
TEXT_FORMATS = frozenset({"text", "json", "ndjson", "xml", "rss", "atom", "plist", "csv", "tsv", "ipynb", "yaml",
                          "toml", "ini", "pyproject", "requirements", "package-json", "python", "javascript",
                          "typescript", "c", "cpp", "shell", "perl", "ruby", "rust", "go", "java", "sql", "css",
                          "html", "markdown", "svg", "kml", "gpx", "geojson", "obj"})
_NOT_TEXT = re.compile(rb"[\x00-\x08\x0e-\x1a\x1c-\x1f\x7f]")
HINT_ONLY = "nothing in the bytes confirms it"
# Formats refused on their name alone: what they hold is never read, so naming them costs nothing but a refusal.
REFUSED_ON_HINT = frozenset({"pickle"})


def _single_byte_text(head: bytes) -> bool:
    """Whether bytes read as text in some single-byte encoding: no NUL and no control character other than tab,
    line breaks, form feed and escape."""
    return bool(head.strip()) and _NOT_TEXT.search(head) is None


def _unknown(reasons) -> Detected:
    return Detected("unknown", "unknown", "application/octet-stream", "none", None, tuple(reasons))


# Formats whose files may be wholly text although a signature names them (lane FX3, class U-A): their signature is
# not doubted when every byte is text. Every other signature belongs to a binary layout (sizes, counts, block
# terminators, a PNG's 0x1A byte) that no file made only of text can hold.
TEXT_SIGNATURES = frozenset({"pdf", "ps", "rtf", "ply", "fits", "pbm", "pgm", "ppm", "pnm", "pam", "pickle"})
PROSE_PREFIX = 4096     # a prefix of a longer file this long, all text, is enough to doubt a binary signature


def _binary_signature_in_text(fmt: str, head: bytes, size: int | None, headings_alone: bool,
                              markdown_named: bool = False):
    """What the structure test finds when a BINARY format's signature begins bytes that are all text (a note that
    starts "RIFF....WAVE is how a sound file begins", a table whose first header cell is "GIF89a"): the signature is
    then part of the words, not a file of that format. None when the signature stands."""
    if fmt in TEXT_SIGNATURES or fmt in TEXT_FORMATS:
        return None
    complete = (size is not None and len(head) >= size) or (size is None and len(head) < HEAD_MAX)
    if not complete and len(head) < PROSE_PREFIX:
        return None
    if _as_text(head, complete) is None:
        return None
    found = _structure(head, complete, headings_alone, markdown_named)
    if found is None:
        return None
    return found[0], list(found[1]) + [f"it begins with the bytes of a {fmt} signature, but every byte is text, "
                                       f"which no {fmt} file is: the signature is part of the words"], None, "structure"


def _identify(head: bytes, size: int | None, headings_alone: bool = False,
              markdown_named: bool = False) -> tuple[str, list[str], str | None, str] | None:
    for test in SIGNATURES:
        try:
            found = test(head, size)
        except (struct.error, IndexError, ValueError):
            found = None
        if found is not None:
            # a detector added by a lane (B2's structure test for glTF, OFF, WKT, bare GeoJSON geometries) may say
            # that its answer rests on structure, not on a signature: a fourth element names the basis
            basis = found[3] if len(found) > 3 and found[3] in ("signature", "structure") else "signature"
            if basis == "signature":
                prose = _binary_signature_in_text(found[0], head, size, headings_alone, markdown_named)
                if prose is not None:
                    return prose
            return found[0], list(found[1]), found[2], basis
    complete = (size is not None and len(head) >= size) or (size is None and len(head) < HEAD_MAX)
    if size is not None and not complete and len(head) < MIN_STRUCTURE_PREFIX:
        return None   # a few bytes of a longer input say nothing about its structure
    found = _structure(head, complete, headings_alone, markdown_named)
    if found is not None:
        return found[0], list(found[1]), None, "structure"
    return None


_REQUIREMENT = re.compile(r"(?:[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9_,.\s-]*\])?\s*(?:(?:[<>=!~]=?|===)\s*[^\s;#]+"
                          r"\s*(?:,\s*(?:[<>=!~]=?|===)\s*[^\s;#]+\s*)*)?(?:@\s*\S+)?\s*(?:;[^#]*)?|-[-\w]+(?:[ =]\S+)?)"
                          r"\s*(?:#.*)?")


def _manifest(name, fmt: str, head: bytes, size: int | None) -> tuple[str, str, list[str]] | None:
    """A project manifest: the exact file name names it, and the bytes must agree (format, basis, reasons)."""
    if not isinstance(name, str):
        return None
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    if base == "package.json" and fmt == "json" and head.lstrip(b"\xef\xbb\xbf \t\r\n")[:1] == b"{":
        return "package-json", "structure", ["the file is named package.json and holds a JSON object"]
    if fmt != "text":
        return None
    complete = (size is not None and len(head) >= size) or (size is None and len(head) < HEAD_MAX)
    decoded = _as_text(head, complete)
    if decoded is None:
        return None
    text = decoded[0]
    if base == "pyproject.toml":
        if not complete:
            return "pyproject", "declared", ["the file is named pyproject.toml; only a prefix was seen, so TOML "
                                             "was not checked"]
        try:
            import tomllib
            tomllib.loads(text)
        except Exception:
            return None   # named pyproject.toml but not TOML: the text answer (and the hint) stand
        return "pyproject", "structure", ["the file is named pyproject.toml and parses as TOML"]
    if re.fullmatch(r"requirements(?:[-_.][\w.-]+)?\.(?:txt|in)", base):
        lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        if not complete and lines:
            lines = lines[:-1]
        if lines and all(_REQUIREMENT.fullmatch(ln) for ln in lines[:2000]):
            return "requirements", "structure", [f"the file is named {base[:60]} and every line is a requirement "
                                                 "specifier, an option or a comment"]
        return None
    return None


def detect(head: bytes, *, name: str | None = None, declared: str | None = None,
           size: int | None = None) -> Detected:
    try:
        head = bytes(head[:HEAD_MAX])
    except Exception:
        return _unknown(["the input is not bytes"])
    if size is not None and (not isinstance(size, int) or size < 0):
        size = None
    hints = _hints(name, declared)
    if not head:
        text_hint = next(((f, w) for f, w in hints if f in TEXT_FORMATS), None)
        if text_hint:
            return _make(text_hint[0], "declared", [text_hint[1] + "; the input is empty, so " + HINT_ONLY])
        return _unknown(["the input is empty"] + [w + f", which names {f}: an empty input is not one" for f, w in hints])
    # Markdown by its headings alone only when no hint names another text format whose comments begin with '#'
    headings_alone = not any(f in TEXT_FORMATS and f not in ("text", "markdown") for f, _ in hints)
    # lane FX4 (review L2): a file named or declared Markdown is not taken for Python because it parses as Python
    markdown_named = any(f == "markdown" for f, _ in hints)
    try:
        found = _identify(head, size, headings_alone, markdown_named)
    except (RecursionError, MemoryError):
        found = None
    if found is None:
        text = _single_byte_text(head)
        for fmt, why in hints:
            if fmt in TEXT_FORMATS and text or fmt in REFUSED_ON_HINT:
                return _make(fmt, "declared", [UNKNOWN_REASON, why + "; " + HINT_ONLY])
        return _unknown([UNKNOWN_REASON] + [why + f", which names {fmt}: the bytes show no {fmt} signature or "
                                            "structure, and a name or a declared type alone never identifies a file"
                                            for fmt, why in hints])
    fmt, reasons, container, basis = found
    complete = (size is not None and len(head) >= size) or (size is None and len(head) < HEAD_MAX)
    manifest = _manifest(name, fmt, head, size)
    if manifest is not None:
        return _make(manifest[0], manifest[1], reasons + manifest[2])
    for hinted, why in hints:
        if hinted == fmt:
            reasons.append(why + ", which agrees")
        elif hinted in _REFINES.get(fmt, ()) and complete and fmt in ("zip", "ole"):
            # the whole file was seen: a member or stream specific to the hinted format would have been found
            reasons.append(why + f", but the whole file was examined and nothing in it is specific to {hinted}")
        elif hinted in _REFINES.get(fmt, ()):
            reasons.append(why + f", which is consistent with {fmt} but not confirmed by the bytes")
            container = "zip" if fmt == "zip" else "ole" if fmt == "ole" else container
            return _make(hinted, "declared", reasons, container)
        else:
            reasons.append(why + f", which names {hinted}: the {basis} wins")
    return _make(fmt, basis, reasons, container)


__all__ = ["DECLARED", "EXTENSIONS", "FORMATS", "HEAD_MAX", "HINT_ONLY", "SIGNATURES", "TEXT_FORMATS", "detect"]
