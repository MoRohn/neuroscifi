"""Detection of lane B2's formats (images, sound, video containers, meshes, maps, arrays) for `detect.detect`.

`detect_b2(head, size)` answers `(format, basis, reasons, container)` or None, from signatures (magic numbers at
fixed offsets, with a sanity check of the fields next to them) and from structure (the first element of an XML
document, the keys of a JSON object, the keywords of a text mesh or WKT geometry, the size rule of binary STL,
the first member name of a zip file). The file name and declared type are never used here (they stay hints in
`detect`). `install()` adds the formats, extensions and this function to `detect`'s tables; it is idempotent.
"""
from __future__ import annotations

import re
import struct

# format -> (family, media type)
FORMATS_B2: dict[str, tuple[str, str]] = {
    "gif": ("image", "image/gif"),
    "bmp": ("image", "image/bmp"),
    "ico": ("image", "image/vnd.microsoft.icon"),
    "tiff": ("image", "image/tiff"),
    "pbm": ("image", "image/x-portable-bitmap"),
    "pgm": ("image", "image/x-portable-graymap"),
    "ppm": ("image", "image/x-portable-pixmap"),
    "pam": ("image", "image/x-portable-arbitrarymap"),
    "farbfeld": ("image", "image/x-farbfeld"),
    "qoi": ("image", "image/qoi"),
    "jpeg": ("image", "image/jpeg"),
    "webp": ("image", "image/webp"),
    "heic": ("image", "image/heic"),
    "avif": ("image", "image/avif"),
    "psd": ("image", "image/vnd.adobe.photoshop"),
    "dicom": ("image", "application/dicom"),
    "fits": ("image", "image/fits"),
    "svg": ("drawing", "image/svg+xml"),
    "wav": ("audio", "audio/wav"),
    "aiff": ("audio", "audio/aiff"),
    "au": ("audio", "audio/basic"),
    "midi": ("audio", "audio/midi"),
    "mp3": ("audio", "audio/mpeg"),
    "aac": ("audio", "audio/aac"),
    "flac": ("audio", "audio/flac"),
    "ogg": ("audio", "audio/ogg"),
    "m4a": ("audio", "audio/mp4"),
    "mp4": ("video", "video/mp4"),
    "mov": ("video", "video/quicktime"),
    "mkv": ("video", "video/x-matroska"),
    "webm": ("video", "video/webm"),
    "avi": ("video", "video/x-msvideo"),
    "obj": ("mesh", "model/obj"),
    "stl": ("mesh", "model/stl"),
    "ply": ("mesh", "model/x-ply"),
    "off": ("mesh", "model/x-off"),
    "gltf": ("mesh", "model/gltf+json"),
    "glb": ("mesh", "model/gltf-binary"),
    "geojson": ("geo", "application/geo+json"),
    "gpx": ("geo", "application/gpx+xml"),
    "kml": ("geo", "application/vnd.google-earth.kml+xml"),
    "kmz": ("geo", "application/vnd.google-earth.kmz"),
    "wkt": ("geo", "text/x-wkt"),
    "npy": ("array", "application/x-npy"),
    "npz": ("array", "application/x-npz"),
}

EXTENSIONS_B2: dict[str, str] = {
    ".gif": "gif", ".bmp": "bmp", ".dib": "bmp", ".ico": "ico", ".cur": "ico", ".tif": "tiff", ".tiff": "tiff",
    ".pbm": "pbm", ".pgm": "pgm", ".ppm": "ppm", ".pnm": "ppm", ".pam": "pam", ".ff": "farbfeld", ".qoi": "qoi",
    ".jpg": "jpeg", ".jpeg": "jpeg", ".jpe": "jpeg", ".webp": "webp", ".heic": "heic", ".heif": "heic",
    ".avif": "avif", ".psd": "psd", ".dcm": "dicom", ".fits": "fits", ".fit": "fits", ".fts": "fits",
    ".svg": "svg", ".wav": "wav", ".aif": "aiff", ".aiff": "aiff", ".aifc": "aiff", ".au": "au", ".snd": "au",
    ".mid": "midi", ".midi": "midi", ".mp3": "mp3", ".aac": "aac", ".flac": "flac", ".ogg": "ogg", ".oga": "ogg",
    ".opus": "ogg", ".m4a": "m4a", ".mp4": "mp4", ".m4v": "mp4", ".mov": "mov", ".mkv": "mkv", ".webm": "webm",
    ".avi": "avi", ".obj": "obj", ".stl": "stl", ".ply": "ply", ".off": "off", ".gltf": "gltf", ".glb": "glb",
    ".geojson": "geojson", ".gpx": "gpx", ".kml": "kml", ".kmz": "kmz", ".wkt": "wkt", ".npy": "npy",
    ".npz": "npz",
}

_ISO_IMAGE = {b"heic": "heic", b"heix": "heic", b"heim": "heic", b"heis": "heic", b"hevc": "heic",
              b"hevx": "heic", b"avif": "avif", b"avis": "avif"}
_ISO_AUDIO = {b"M4A ", b"M4B ", b"M4P ", b"F4A "}
_GEOJSON_TYPES = ("FeatureCollection", "Feature", "Point", "MultiPoint", "LineString", "MultiLineString", "Polygon",
                  "MultiPolygon", "GeometryCollection")
_WKT = re.compile(rb"\s*(SRID=\d+;\s*)?(POINT|LINESTRING|POLYGON|MULTIPOINT|MULTILINESTRING|MULTIPOLYGON|"
                  rb"GEOMETRYCOLLECTION|TRIANGLE|TIN|POLYHEDRALSURFACE)\s*(Z|M|ZM)?\s*(\(|EMPTY)", re.I)
_OBJ_KEYS = {b"v", b"vt", b"vn", b"vp", b"f", b"l", b"p", b"o", b"g", b"s", b"mtllib", b"usemtl", b"#", b"cstype",
             b"deg", b"curv", b"surf", b"parm", b"end", b"mg", b"lod"}
_XML_PROLOG = re.compile(rb"\A(\xef\xbb\xbf)?\s*(<\?.*?\?>\s*|<!--.*?-->\s*|<!DOCTYPE[^\[>]*(?:\[.*?\])?\s*>\s*)*<([A-Za-z_][\w.:-]*)",
                         re.S)


def _hit(fmt, why, basis="signature", container=None):
    return fmt, basis, [why], container


def _riff(head):
    form = head[8:12]
    return {b"WAVE": "wav", b"AVI ": "avi", b"WEBP": "webp"}.get(form), form


def _iso(head):
    if head[4:8] != b"ftyp":
        if head[4:8] in (b"moov", b"mdat", b"wide", b"pnot") and len(head) >= 8:
            size = struct.unpack(">I", head[:4])[0]
            if size in (0, 1) or size >= 8:
                return _hit("mov", f"the file starts with a QuickTime '{head[4:8].decode()}' box", "structure",
                            "isobmff")
        return None
    size = struct.unpack(">I", head[:4])[0]
    brands = [head[8:12]] + [head[i:i + 4] for i in range(16, min(size, len(head), 256), 4)]
    major = head[8:12]
    for brand in brands:
        if brand in _ISO_IMAGE:
            fmt = _ISO_IMAGE[brand]
            return _hit(fmt, f"an ISO base media 'ftyp' box names the brand '{brand.decode('latin-1')}'",
                        container="isobmff")
    if brands and major in (b"mif1", b"msf1"):
        return _hit("heic", "an ISO base media 'ftyp' box names the image brand 'mif1'", container="isobmff")
    if major in _ISO_AUDIO:
        return _hit("m4a", f"an ISO base media 'ftyp' box names the audio brand '{major.decode('latin-1')}'",
                    container="isobmff")
    if major == b"qt  ":
        return _hit("mov", "an ISO base media 'ftyp' box names the QuickTime brand 'qt'", container="isobmff")
    return _hit("mp4", f"an ISO base media 'ftyp' box names the brand '{major.decode('latin-1', 'replace')}'",
                container="isobmff")


def _ebml(head):
    at = head.find(b"\x42\x82", 4, 64)
    if at > 0 and at + 2 < len(head) and head[at + 2]:
        n = 9 - head[at + 2].bit_length()
        size = int.from_bytes(head[at + 2:at + 2 + n], "big") & ((1 << (7 * n)) - 1)
        if size <= 32:
            doctype = head[at + 2 + n:at + 2 + n + size].rstrip(b"\x00")
            if doctype == b"webm":
                return _hit("webm", "an EBML header names the document type 'webm'", container="ebml")
            if doctype == b"matroska":
                return _hit("mkv", "an EBML header names the document type 'matroska'", container="ebml")
    return _hit("mkv", "the first 4 bytes are the EBML magic number (Matroska family)", container="ebml")


def _mpeg_audio(head):
    """An MP3 frame header at offset 0 (layer III, valid bitrate and rate), or an ADTS AAC header."""
    if len(head) < 4 or head[0] != 0xFF or head[1] & 0xE0 != 0xE0:
        return None
    layer = (head[1] >> 1) & 3
    if head[1] & 0xF6 == 0xF0:      # 12 sync bits, layer 00: ADTS
        if (head[2] >> 2) & 0xF < 13:
            return _hit("aac", "the first bytes are an ADTS (AAC) frame header")
        return None
    version = (head[1] >> 3) & 3
    bitrate = head[2] >> 4
    rate = (head[2] >> 2) & 3
    if layer == 1 and version != 1 and bitrate not in (0, 15) and rate != 3:
        return _hit("mp3", "the first bytes are an MPEG audio layer III frame header")
    return None


def _xml_root(head):
    m = _XML_PROLOG.match(head[:4096])
    if not m:
        return None
    root = m.group(3).split(b":")[-1].lower()
    return {b"svg": "svg", b"gpx": "gpx", b"kml": "kml"}.get(root)


def _text(head):
    try:
        return head.decode("utf-8")
    except UnicodeDecodeError as error:
        if error.start >= len(head) - 4 and error.start > 0:     # a character cut at the end of the prefix
            return head[:error.start].decode("utf-8", "ignore")
        return None


def _json_kind(text):
    s = text.lstrip("﻿ \t\r\n")
    if not s.startswith("{"):
        return None
    if re.search(r'"asset"\s*:\s*\{[^{}]*"version"\s*:', s[:65536]):
        return _hit("gltf", "a JSON object with an 'asset' member holding a 'version' (glTF)", "structure")
    m = re.search(r'"type"\s*:\s*"([A-Za-z]+)"', s[:65536])
    if m and m.group(1) in _GEOJSON_TYPES:
        return _hit("geojson", f"a JSON object whose 'type' is '{m.group(1)}' (GeoJSON)", "structure")
    return None


def _obj(lines):
    keys = []
    for line in lines[:200]:
        line = line.strip()
        if not line:
            continue
        key = line.split(None, 1)[0]
        if key.startswith(b"#"):
            key = b"#"
        keys.append(key)
    if len(keys) < 2 or not all(k in _OBJ_KEYS for k in keys):
        return False
    return b"v" in keys


def _stl_binary(head, size):
    if size is None or size < 84 or len(head) < 84:
        return False
    n = struct.unpack("<I", head[80:84])[0]
    return 84 + 50 * n == size


def _netpbm(head):
    m = re.match(rb"P([1-7])\s", head[:3])
    if not m:
        return None
    kind = int(m.group(1))
    if kind == 7:
        return _hit("pam", "the first bytes are the NetPBM 'P7' (PAM) magic") if b"WIDTH" in head[:512] else None
    sep = rb"(?:\s|#[^\n]*\n)+"
    fields = 2 if kind in (1, 4) else 3
    if not re.match(rb"P[1-6]" + (sep + rb"\d+") * fields + rb"\s", head[:512]):
        return None
    fmt = {1: "pbm", 4: "pbm", 2: "pgm", 5: "pgm", 3: "ppm", 6: "ppm"}[kind]
    return _hit(fmt, f"the first bytes are the NetPBM 'P{kind}' magic followed by a size")


def _zip_first_name(head):
    if head[:4] != b"PK\x03\x04" or len(head) < 30:
        return None
    nlen = struct.unpack("<H", head[26:28])[0]
    return head[30:30 + nlen].decode("utf-8", "replace").lower()


def detect_b2(head: bytes, size: int | None = None):
    """(format, basis, reasons, container) for a B2 format, or None."""
    h = head
    if h[:6] in (b"GIF87a", b"GIF89a"):
        return _hit("gif", f"the first 6 bytes are the GIF signature '{h[:6].decode()}'")
    if h[:4] in (b"RIFF", b"RIFX") and len(h) >= 12:
        fmt, form = _riff(h)
        if fmt:
            return _hit(fmt, f"a RIFF file whose form type is '{form.decode('latin-1').strip()}'", container="riff")
        return None
    if h[:4] == b"FORM" and h[8:12] in (b"AIFF", b"AIFC"):
        return _hit("aiff", f"an IFF file whose form type is '{h[8:12].decode()}'", container="iff")
    if h[:4] == b".snd" and len(h) >= 24:
        return _hit("au", "the first 4 bytes are the Sun/NeXT audio magic '.snd'")
    if h[:4] == b"MThd":
        return _hit("midi", "the first 4 bytes are the Standard MIDI File chunk 'MThd'")
    if h[:4] == b"fLaC":
        return _hit("flac", "the first 4 bytes are the FLAC magic 'fLaC'")
    if h[:4] == b"OggS":
        return _hit("ogg", "the first 4 bytes are the Ogg page capture pattern 'OggS'", container="ogg")
    if h[:3] == b"ID3":
        return _hit("mp3", "the file starts with an ID3v2 tag (as MP3 files do)")
    if h[:4] == b"\x1a\x45\xdf\xa3":
        return _ebml(h)
    if len(h) >= 12 and (h[4:8] == b"ftyp" or h[4:8] in (b"moov", b"mdat", b"wide", b"pnot")):
        hit = _iso(h)
        if hit:
            return hit
    if h[:3] == b"\xff\xd8\xff":
        return _hit("jpeg", "the first 3 bytes are the JPEG start-of-image marker")
    if h[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
        return _hit("tiff", "the first 4 bytes are a TIFF byte-order mark and magic number")
    if h[:2] == b"BM" and len(h) >= 18 and struct.unpack("<I", h[14:18])[0] in (12, 16, 40, 52, 56, 64, 108, 124):
        return _hit("bmp", "the first 2 bytes are 'BM' and a known BMP header size follows")
    if h[:4] in (b"\x00\x00\x01\x00", b"\x00\x00\x02\x00") and len(h) >= 22:
        n = struct.unpack("<H", h[4:6])[0]
        if 0 < n <= 256 and h[9] == 0 and struct.unpack("<I", h[18:22])[0] >= 6 + 16 * n:
            return _hit("ico", "an icon directory (reserved 0, type 1 or 2) with a plausible first entry")
    if h[:8] == b"farbfeld":
        return _hit("farbfeld", "the first 8 bytes are the farbfeld magic")
    if h[:4] == b"qoif":
        return _hit("qoi", "the first 4 bytes are the QOI magic 'qoif'")
    if h[:4] == b"8BPS":
        return _hit("psd", "the first 4 bytes are the Photoshop magic '8BPS'")
    if h[128:132] == b"DICM":
        return _hit("dicom", "the bytes at offset 128 are the DICOM prefix 'DICM'")
    if h[:9] == b"SIMPLE  =":
        return _hit("fits", "the file starts with the FITS card 'SIMPLE  ='")
    if h[:6] == b"\x93NUMPY":
        return _hit("npy", "the first 6 bytes are the NumPy array magic")
    if h[:4] == b"glTF":
        return _hit("glb", "the first 4 bytes are the binary glTF magic 'glTF'")
    if h[:4] in (b"ply\n", b"ply\r"):
        return _hit("ply", "the first line is 'ply'")
    first_zip = _zip_first_name(h)
    if first_zip is not None:
        if first_zip.endswith(".npy"):
            return _hit("npz", "a zip file whose first member is a .npy array", "structure", "zip")
        if first_zip.endswith(".kml"):
            return _hit("kmz", "a zip file whose first member is a .kml document", "structure", "zip")
        return None
    hit = _netpbm(h)
    if hit:
        return hit
    if _stl_binary(h, size):
        return _hit("stl", "the size is exactly 84 + 50 x the triangle count at offset 80 (binary STL)", "structure")
    hit = _mpeg_audio(h)
    if hit:
        return hit
    text = _text(h)
    if text is None:
        return None
    raw = text.encode("utf-8")
    root = _xml_root(raw)
    if root:
        return _hit(root, f"an XML document whose root element is '{root}'", "structure")
    hit = _json_kind(text)
    if hit:
        return hit
    stripped = raw.lstrip()
    if re.match(rb"solid\b[^\n]*\n\s*(facet\s+normal\s|endsolid\b)", stripped[:1024]):
        return _hit("stl", "text whose first line is 'solid ...' and whose next is 'facet normal' (ASCII STL)",
                    "structure")
    if re.match(rb"\s*(?:ST)?C?N?4?n?OFF(?:[ \t]+BINARY)?(?:\s|#[^\n]*\n)+\d+\s+\d+(?:\s+\d+)?\s", raw[:1024]):
        return _hit("off", "the first line is 'OFF' (Object File Format)", "structure")
    if _WKT.match(raw[:256]):
        return _hit("wkt", "text that starts with a WKT geometry keyword and '('", "structure")
    lines = raw.splitlines()
    if len(raw) == len(head):
        complete = lines
    else:
        complete = lines[:-1]
    if _obj(complete):
        return _hit("obj", "every line starts with a Wavefront OBJ keyword and vertices ('v') are present",
                    "structure")
    return None


def install() -> None:
    """Add B2's formats, extensions and detector to `detect`'s tables (idempotent)."""
    from . import detect
    for fmt, entry in FORMATS_B2.items():
        detect.FORMATS.setdefault(fmt, entry)
        detect.DECLARED.setdefault(entry[1], fmt)
    for ext, fmt in EXTENSIONS_B2.items():
        detect.EXTENSIONS.setdefault(ext, fmt)
    detectors = getattr(detect, "DETECTORS", None)
    if detectors is not None and detect_b2 not in detectors:
        detectors.append(detect_b2)


__all__ = ["EXTENSIONS_B2", "FORMATS_B2", "detect_b2", "install"]
