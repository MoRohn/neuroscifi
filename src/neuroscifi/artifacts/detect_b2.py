"""Detection of the lane-B2 formats that lane K's `detect` does not know: Sun/NeXT AU, farbfeld, QOI, PAM (P7),
KMZ (a zip whose first member is a .kml), glTF JSON, OFF, WKT/EWKT; and a bare GeoJSON geometry (K's table knows
GeoJSON only as a Feature or FeatureCollection).

`install()` adds them to `detect`'s tables (FORMATS, EXTENSIONS, DECLARED) and to its public SIGNATURES list:
`b2_signatures` (fixed magic numbers, and the zip's first member name) is put first, because KMZ must be told from a
plain zip before K's zip test answers; `b2_structure` (glTF's `asset.version`, OFF's header and counts, a WKT
geometry keyword) is appended last, so it runs after every signature and before K's general text/JSON structure.
Because K's `detect` labels every SIGNATURES answer with basis "signature", the reasons of a `b2_structure` answer
say that it rests on structure. Idempotent. The file name and declared type are never used here.
"""
from __future__ import annotations

import re
import struct

# format -> (family, media type): only formats lane K's table lacks
FORMATS_B2: dict[str, tuple[str, str]] = {
    "au": ("audio", "audio/basic"),
    "farbfeld": ("image", "image/x-farbfeld"),
    "qoi": ("image", "image/qoi"),
    "pam": ("image", "image/x-portable-arbitrarymap"),
    "kmz": ("geo", "application/vnd.google-earth.kmz"),
    "gltf": ("mesh", "model/gltf+json"),
    "off": ("mesh", "model/x-off"),
    "wkt": ("geo", "text/x-wkt"),
}

EXTENSIONS_B2: dict[str, str] = {".au": "au", ".snd": "au", ".ff": "farbfeld", ".qoi": "qoi", ".pam": "pam",
                                 ".kmz": "kmz", ".gltf": "gltf", ".off": "off", ".wkt": "wkt"}

_WKT = re.compile(rb"\s*(SRID=\d+;\s*)?(POINT|LINESTRING|POLYGON|MULTIPOINT|MULTILINESTRING|MULTIPOLYGON|"
                  rb"GEOMETRYCOLLECTION|TRIANGLE|TIN|POLYHEDRALSURFACE)\s*(Z|M|ZM)?\s*(\(|EMPTY)", re.I)
_OFF = re.compile(rb"\s*(?:ST)?C?N?4?n?OFF(?:[ \t]+BINARY)?(?:\s|#[^\n]*\n)+\d+\s+\d+(?:\s+\d+)?\s")
_GLTF = re.compile(r'"asset"\s*:\s*\{[^{}]*"version"\s*:')
_GEOMETRY = re.compile(r'"type"\s*:\s*"(Point|MultiPoint|LineString|MultiLineString|Polygon|MultiPolygon|'
                       r'GeometryCollection)"')


def _zip_first_name(head: bytes):
    if head[:4] != b"PK\x03\x04" or len(head) < 30:
        return None
    nlen = struct.unpack("<H", head[26:28])[0]
    return head[30:30 + nlen].decode("utf-8", "replace").lower()


def b2_signatures(head: bytes, size=None):
    """(format, reasons, container) for the B2-only formats with a fixed signature, or None."""
    h = head
    if h[:4] == b".snd" and len(h) >= 24:
        return "au", ["the first 4 bytes are the Sun/NeXT audio magic '.snd'"], None
    if h[:8] == b"farbfeld":
        return "farbfeld", ["the first 8 bytes are the farbfeld magic"], None
    if h[:4] == b"qoif" and len(h) >= 14:
        return "qoi", ["the first 4 bytes are the QOI magic 'qoif'"], None
    if h[:3] == b"P7\n" and b"WIDTH" in h[:512] and b"ENDHDR" in h[:4096]:
        return "pam", ["the first line is the NetPBM 'P7' (PAM) magic and the header holds WIDTH and ENDHDR"], None
    first = _zip_first_name(h)
    if first is not None and first.endswith(".kml"):
        return "kmz", ["a zip file whose first member is a .kml document"], "zip"
    return None


def _text(head: bytes):
    try:
        return head.decode("utf-8")
    except UnicodeDecodeError as error:
        if error.start >= len(head) - 4 and error.start > 0:     # a character cut at the end of the prefix
            return head[:error.start].decode("utf-8", "ignore")
        return None


def b2_structure(head: bytes, size=None):
    """(format, reasons, container, "structure") for glTF JSON, OFF and WKT text, or None (the basis is structure)."""
    text = _text(head[:65536])
    if text is None:
        return None
    s = text.lstrip("\ufeff \t\r\n")
    if s.startswith("{") and _GLTF.search(s):
        return "gltf", ["structure: a JSON object with an 'asset' member holding a 'version' (glTF)"], None, "structure"
    if s.startswith("{") and (m := _GEOMETRY.search(s[:4096])) and re.search(r'"(coordinates|geometries)"\s*:',
                                                                            s[:65536]):
        return "geojson", [f"structure: a JSON object whose 'type' is the GeoJSON geometry '{m.group(1)}'"], None, "structure"
    raw = s.encode("utf-8")
    if _OFF.match(raw[:1024]):
        return "off", ["structure: the first line is 'OFF' followed by the vertex and face counts"], None, "structure"
    if _WKT.match(raw[:256]):
        return "wkt", ["structure: text that starts with a WKT geometry keyword and '(' or EMPTY"], None, "structure"
    return None


def uninstall() -> None:
    """Remove B2's detectors from `detect.SIGNATURES` (the table entries stay; they name nothing on their own)."""
    from . import detect
    for fn in (b2_signatures, b2_structure):
        while fn in detect.SIGNATURES:
            detect.SIGNATURES.remove(fn)


def install() -> None:
    """Add B2's formats, extensions and detectors to `detect`'s tables (idempotent)."""
    from . import detect
    for fmt, entry in FORMATS_B2.items():
        detect.FORMATS.setdefault(fmt, entry)
        detect.DECLARED.setdefault(entry[1], fmt)
    for ext, fmt in EXTENSIONS_B2.items():
        detect.EXTENSIONS.setdefault(ext, fmt)
    if b2_signatures not in detect.SIGNATURES:
        detect.SIGNATURES.insert(0, b2_signatures)
    if b2_structure not in detect.SIGNATURES:
        detect.SIGNATURES.append(b2_structure)


__all__ = ["EXTENSIONS_B2", "FORMATS_B2", "b2_signatures", "b2_structure", "install", "uninstall"]
