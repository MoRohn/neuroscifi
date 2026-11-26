"""Hand-assembled samples whose true format is known by construction (the detection test corpus, lane K).

Every sample is built here from the format's published layout with struct and literal bytes (and, for the
compressed containers, the standard library's own writers), never with `neuroscifi.artifacts`. Each entry:
(label, bytes, kwargs for detect, expected format, expected basis)."""
import bz2
import gzip
import io
import json
import lzma
import struct
import tarfile
import zipfile

import zstandard


def _zip(members, *, stored_first=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if stored_first:
            z.writestr(zipfile.ZipInfo(stored_first[0]), stored_first[1], compress_type=zipfile.ZIP_STORED)
        for name, data in members:
            z.writestr(name, data)
    return buf.getvalue()


def _tar():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as t:
        info = tarfile.TarInfo("hello.txt")
        info.size = 5
        info.mtime = 0
        t.addfile(info, io.BytesIO(b"hello"))
    return buf.getvalue()


def _ole(stream_name):
    header = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504
    entry = stream_name.encode("utf-16-le") + b"\x00\x00"
    entry = entry + b"\x00" * (64 - len(entry)) + struct.pack("<H", len(stream_name) * 2 + 2) + b"\x02" + b"\x00" * 61
    return header + entry


def _ogg(packet):
    return b"OggS\x00\x02" + b"\x00" * 20 + b"\x01" + bytes([len(packet)]) + packet


def _ftyp(major, compat=()):
    body = major + b"\x00\x00\x02\x00" + b"".join(compat)
    return struct.pack(">I", 8 + len(body)) + b"ftyp" + body + b"\x00\x00\x00\x08free"


def _mp3_frames(n=4):
    # MPEG-1 Layer III, 128 kbit/s, 44100 Hz, no padding: 144 * 128000 / 44100 = 417 bytes per frame
    frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
    return frame * n


def _adts(n=4):
    length = 20
    hdr = bytes([0xFF, 0xF1, 0x50, 0x80 | (length >> 11), (length >> 3) & 0xFF, ((length & 7) << 5) | 0x1F, 0xFC])
    return (hdr + b"\x00" * (length - 7)) * n


def _bmp():
    w, h = 2, 2
    pixels = b"\x00\x00\xff\xff\xff\xff\x00\x00" * 2
    dib = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, len(pixels), 2835, 2835, 0, 0)
    return b"BM" + struct.pack("<IHHI", 14 + 40 + len(pixels), 0, 0, 54) + dib + pixels


def _ico():
    entry = struct.pack("<BBBBHHII", 16, 16, 0, 0, 1, 32, 40, 22)
    return b"\x00\x00\x01\x00\x01\x00" + entry + b"\x00" * 40


def _ebml(doctype):
    dt = doctype.encode()
    body = b"\x42\x86\x81\x01" + b"\x42\x82" + bytes([0x80 | len(dt)]) + dt
    return b"\x1a\x45\xdf\xa3" + bytes([0x80 | len(body)]) + body + b"\x18\x53\x80\x67"


def _nifti1():
    hdr = bytearray(352)
    struct.pack_into("<i", hdr, 0, 348)
    hdr[344:348] = b"n+1\x00"
    return bytes(hdr)


def _stl_binary(n=2):
    header = b"binary stl made by hand".ljust(80, b"\x00")
    tri = struct.pack("<12fH", *([0.0] * 12), 0)
    return header + struct.pack("<I", n) + tri * n


def _pe():
    dos = bytearray(128)
    dos[:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 0x40)
    dos[0x40:0x44] = b"PE\x00\x00"
    return bytes(dos)


def _shp():
    hdr = bytearray(100)
    struct.pack_into(">i", hdr, 0, 9994)
    struct.pack_into(">i", hdr, 24, 50)
    struct.pack_into("<ii", hdr, 28, 1000, 1)
    return bytes(hdr)


def _sqlite():
    return b"SQLite format 3\x00" + struct.pack(">HBB", 4096, 1, 1) + b"\x00" * 80


def _npy():
    header = "{'descr': '<f8', 'fortran_order': False, 'shape': (2,), }"
    header = header + " " * (64 - 10 - len(header) - 1) + "\n"
    return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header.encode() + struct.pack("<2d", 1.0, 2.0)


def _fits():
    cards = ["SIMPLE  =                    T", "BITPIX  =                    8", "NAXIS   =                    0", "END"]
    return "".join(c.ljust(80) for c in cards).ljust(2880).encode()


def _dicom():
    return b"\x00" * 128 + b"DICM" + b"\x02\x00\x00\x00UL\x04\x00\x00\x00\x00\x00"


def _class():
    return b"\xca\xfe\xba\xbe\x00\x00\x00\x34" + b"\x00\x10"


def _macho_fat():
    return b"\xca\xfe\xba\xbe\x00\x00\x00\x02" + b"\x00" * 40


def _gif():
    return b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"


def _png():
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    return sig + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\x00\x00\x00\x00"


def _wav():
    data = struct.pack("<4h", 0, 1000, -1000, 0)
    fmt = struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16)
    return (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
            + b"data" + struct.pack("<I", len(data)) + data)


def _midi():
    return b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60MTrk\x00\x00\x00\x04\x00\xff\x2f\x00"


def _pickle(protocol):
    # hand-assembled: PROTO n, EMPTY_DICT, STOP (the opcodes of pickle.dumps({}, protocol=2))
    return b"\x80" + bytes([protocol]) + b"}" + b"."


CSV = b"name,height,city\nAda,1.70,London\nAlan,1.80,Wilmslow\nGrace,1.60,New York\n"
TSV = b"a\tb\tc\n1\t2\t3\n4\t5\t6\n"
JSON = json.dumps({"rows": [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]}).encode()
NDJSON = b'{"a": 1}\n{"a": 2}\n{"a": 3}\n'
GEOJSON = json.dumps({"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [10.7, 59.9]}, "properties": {"n": "Oslo"}}]}).encode()
IPYNB = json.dumps({"cells": [{"cell_type": "code", "source": ["1+1"]}], "metadata": {}, "nbformat": 4,
                    "nbformat_minor": 5}).encode()
SVG = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="5" height="5"/></svg>'
HTML = b"<!DOCTYPE html>\n<html><head><title>t</title></head><body><p>Hi</p></body></html>"
HTML_BARE = b"<html><body><p>Hi</p></body></html>"
XML = b'<?xml version="1.0" encoding="UTF-8"?>\n<catalog><book id="1">A</book></catalog>'
KML = b'<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Placemark/></kml>'
GPX = b'<?xml version="1.0"?><gpx version="1.1" creator="hand"><wpt lat="1" lon="2"/></gpx>'
RSS = b'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title></channel></rss>'
ATOM = b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>x</title></feed>'
PLIST = b'<?xml version="1.0"?><plist version="1.0"><dict/></plist>'
MARKDOWN = b"# Title\n\nSome text with a [link](https://example.org).\n\n```\ncode\n```\n"
PYTHON = b"#!/usr/bin/env python3\nprint('hi')\n"
SHELL = b"#!/bin/sh\necho hi\n"
NODE = b"#!/usr/bin/env node\nconsole.log(1)\n"
OBJ = b"# cube\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
STL_TEXT = b"solid t\n  facet normal 0 0 1\n    outer loop\n      vertex 0 0 0\n    endloop\n  endfacet\nendsolid t\n"
PLY = b"ply\nformat ascii 1.0\nelement vertex 0\nend_header\n"
REQUIREMENTS = (b"numpy==2.3.5\nzstandard>=0.25 ; python_version > '3.10'\n# pinned\n-r base.txt\n"
                b"requests[socks]~=2.31\nwheelpkg @ https://example.org/w.whl\n")
TEXT = "Plain words, with no structure at all.\nA second line; nothing more.\n".encode()


def corpus():
    """[(label, bytes, detect kwargs, expected format, expected basis)]"""
    s, st, d = "signature", "structure", "declared"
    zipped = _zip([("a.txt", b"hello")])
    return [
        ("png", _png(), {}, "png", s),
        ("jpeg", b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00", {}, "jpeg", s),
        ("gif", _gif(), {}, "gif", s),
        ("bmp", _bmp(), {}, "bmp", s),
        ("tiff-le", b"II*\x00\x08\x00\x00\x00\x00\x00", {}, "tiff", s),
        ("tiff-be", b"MM\x00*\x00\x00\x00\x08\x00\x00", {}, "tiff", s),
        ("webp", b"RIFF\x1a\x00\x00\x00WEBPVP8L\x0d\x00\x00\x00\x2f\x00\x00\x00\x10\x07\x10\x11\x11\x88\x88\xfe\x07\x00", {}, "webp", s),
        ("heic", _ftyp(b"heic", (b"mif1", b"heic")), {}, "heic", s),
        ("heif-mif1", _ftyp(b"mif1", (b"mif1", b"heic")), {}, "heic", s),
        ("avif", _ftyp(b"avif", (b"mif1", b"avif")), {}, "avif", s),
        ("ico", _ico(), {}, "ico", s),
        ("psd", b"8BPS\x00\x01" + b"\x00" * 20, {}, "psd", s),
        ("pbm-p1", b"P1\n2 2\n0 1\n1 0\n", {}, "pbm", s),
        ("pgm-p5", b"P5\n# made by hand\n2 1\n255\n\x00\xff", {}, "pgm", s),
        ("ppm-p6", b"P6 1 1 255\n\xff\x00\x00", {}, "ppm", s),
        ("ppm-p3", b"P3\n1 1\n255\n255 0 0\n", {}, "ppm", s),
        ("pbm-p4", b"P4\n8 1\n\xaa", {}, "pbm", s),
        ("pgm-p2", b"P2\n1 1\n255\n7\n", {}, "pgm", s),
        ("svg", SVG, {}, "svg", st),
        ("wav", _wav(), {}, "wav", s),
        ("avi", b"RIFF\x00\x01\x00\x00AVI LIST", {}, "avi", s),
        ("aiff", b"FORM\x00\x00\x00\x2eAIFFCOMM", {}, "aiff", s),
        ("aifc", b"FORM\x00\x00\x00\x2eAIFCFVER", {}, "aiff", s),
        ("flac", b"fLaC\x00\x00\x00\x22" + b"\x00" * 34, {}, "flac", s),
        ("ogg-vorbis", _ogg(b"\x01vorbis\x00\x00\x00\x00\x01"), {}, "vorbis", s),
        ("ogg-opus", _ogg(b"OpusHead\x01\x01"), {}, "opus", s),
        ("ogg-theora", _ogg(b"\x80theora\x03\x02"), {}, "ogv", s),
        ("mp3-id3", b"ID3\x04\x00\x00\x00\x00\x00\x0a" + b"\x00" * 10 + _mp3_frames(1), {}, "mp3", s),
        ("mp3-frames", _mp3_frames(4), {}, "mp3", s),
        ("aac-adts", _adts(4), {}, "aac", s),
        ("mp4", _ftyp(b"isom", (b"isom", b"mp41")), {}, "mp4", s),
        ("mov", _ftyp(b"qt  ", (b"qt  ",)), {}, "mov", s),
        ("m4a", _ftyp(b"M4A ", (b"M4A ", b"mp42")), {}, "m4a", s),
        ("mkv", _ebml("matroska"), {}, "mkv", s),
        ("webm", _ebml("webm"), {}, "webm", s),
        ("midi", _midi(), {}, "midi", s),
        ("pdf", b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n", {}, "pdf", s),
        ("ps", b"%!PS-Adobe-3.0\n%%Title: t\n", {}, "ps", s),
        ("rtf", b"{\\rtf1\\ansi Hello}", {}, "rtf", s),
        ("ole-doc", _ole("WordDocument"), {}, "doc", s),
        ("ole-xls", _ole("Workbook"), {}, "xls", s),
        ("ole-ppt", _ole("PowerPoint Document"), {}, "ppt", s),
        ("ole-generic", _ole("Contents"), {}, "ole", s),
        ("docx", _zip([("[Content_Types].xml", b"<Types/>"), ("word/document.xml", b"<w:document/>")]), {}, "docx", s),
        ("xlsx", _zip([("[Content_Types].xml", b"<Types/>"), ("xl/workbook.xml", b"<workbook/>")]), {}, "xlsx", s),
        ("pptx", _zip([("[Content_Types].xml", b"<Types/>"), ("ppt/presentation.xml", b"<p/>")]), {}, "pptx", s),
        ("odt", _zip([("content.xml", b"<x/>")], stored_first=("mimetype", b"application/vnd.oasis.opendocument.text")), {}, "odt", s),
        ("ods", _zip([("content.xml", b"<x/>")], stored_first=("mimetype", b"application/vnd.oasis.opendocument.spreadsheet")), {}, "ods", s),
        ("odp", _zip([("content.xml", b"<x/>")], stored_first=("mimetype", b"application/vnd.oasis.opendocument.presentation")), {}, "odp", s),
        ("epub", _zip([("META-INF/container.xml", b"<c/>")], stored_first=("mimetype", b"application/epub+zip")), {}, "epub", s),
        ("jar", _zip([("META-INF/MANIFEST.MF", b"Manifest-Version: 1.0\n"), ("A.class", b"\xca\xfe\xba\xbe")]), {}, "jar", s),
        ("apk", _zip([("AndroidManifest.xml", b"\x03\x00"), ("classes.dex", b"dex\n035\x00")]), {}, "apk", s),
        ("npz", _zip([("a.npy", _npy()), ("b.npy", _npy())]), {}, "npz", s),
        ("zip", zipped, {}, "zip", s),
        ("zip-named-docx", zipped, {"name": "report.docx"}, "docx", d),
        ("gzip", gzip.compress(b"hello", mtime=0), {}, "gzip", s),
        ("bzip2", bz2.compress(b"hello"), {}, "bzip2", s),
        ("xz", lzma.compress(b"hello"), {}, "xz", s),
        ("zstd", zstandard.ZstdCompressor().compress(b"hello"), {}, "zstd", s),
        ("7z", b"7z\xbc\xaf\x27\x1c\x00\x04" + b"\x00" * 24, {}, "7z", s),
        ("rar4", b"Rar!\x1a\x07\x00\xcf\x90\x73\x00", {}, "rar", s),
        ("rar5", b"Rar!\x1a\x07\x01\x00\x33\x92", {}, "rar", s),
        ("tar", _tar(), {}, "tar", s),
        ("sqlite", _sqlite(), {}, "sqlite", s),
        ("parquet", b"PAR1\x15\x04\x15\x10\x15\x14", {}, "parquet", s),
        ("arrow", b"ARROW1\x00\x00\xff\xff\xff\xff", {}, "arrow", s),
        ("feather-v1", b"FEA1\x00\x00\x00\x00", {}, "feather", s),
        ("hdf5", b"\x89HDF\r\n\x1a\n\x00\x00\x00\x00", {}, "hdf5", s),
        ("npy", _npy(), {}, "npy", s),
        ("pickle-2", _pickle(2), {}, "pickle", s),
        ("pickle-5", b"\x80\x05\x95\x05\x00\x00\x00\x00\x00\x00\x00}\x94.", {}, "pickle", s),
        ("pickle-0", b"(dp0\nS'a'\np1\nI1\ns.", {}, "pickle", s),
        ("elf", b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8 + b"\x03\x00\xb7\x00", {}, "elf", s),
        ("macho-64", b"\xcf\xfa\xed\xfe\x0c\x00\x00\x01", {}, "macho", s),
        ("macho-fat", _macho_fat(), {}, "macho", s),
        ("pe", _pe(), {}, "pe", s),
        ("wasm", b"\x00asm\x01\x00\x00\x00", {}, "wasm", s),
        ("class", _class(), {}, "class", s),
        ("glb", b"glTF\x02\x00\x00\x00\x0c\x00\x00\x00", {}, "glb", s),
        ("ply", PLY, {}, "ply", s),
        ("stl-binary", _stl_binary(2), {"size": 84 + 100}, "stl", s),
        ("stl-text", STL_TEXT, {}, "stl", st),
        ("obj", OBJ, {}, "obj", st),
        ("dicom", _dicom(), {}, "dicom", s),
        ("fits", _fits(), {}, "fits", s),
        ("nifti", _nifti1(), {}, "nifti", s),
        ("shapefile", _shp(), {}, "shapefile", s),
        ("text-utf8", TEXT, {}, "text", st),
        ("text-utf8-bom", b"\xef\xbb\xbf" + TEXT, {}, "text", st),
        ("text-utf16le-bom", b"\xff\xfe" + TEXT.decode().encode("utf-16-le"), {}, "text", st),
        ("text-utf16be-bom", b"\xfe\xff" + TEXT.decode().encode("utf-16-be"), {}, "text", st),
        ("text-utf16le-nobom", TEXT.decode().encode("utf-16-le"), {}, "text", st),
        ("text-utf32le-bom", b"\xff\xfe\x00\x00" + TEXT.decode().encode("utf-32-le"), {}, "text", st),
        ("text-utf32be-bom", b"\x00\x00\xfe\xff" + TEXT.decode().encode("utf-32-be"), {}, "text", st),
        ("text-unicode", "Ærlig talt: café, naïve, 東京.\n".encode(), {}, "text", st),
        ("json", JSON, {}, "json", st),
        ("json-utf16", b"\xff\xfe" + JSON.decode().encode("utf-16-le"), {}, "json", st),
        ("ndjson", NDJSON, {}, "ndjson", st),
        ("geojson", GEOJSON, {}, "geojson", st),
        ("ipynb", IPYNB, {}, "ipynb", st),
        ("xml", XML, {}, "xml", st),
        ("html", HTML, {}, "html", st),
        ("html-bare", HTML_BARE, {}, "html", st),
        ("kml", KML, {}, "kml", st),
        ("gpx", GPX, {}, "gpx", st),
        ("rss", RSS, {}, "rss", st),
        ("atom", ATOM, {}, "atom", st),
        ("plist", PLIST, {}, "plist", st),
        ("csv", CSV, {}, "csv", st),
        ("tsv", TSV, {}, "tsv", st),
        ("csv-semicolon", b"a;b\n1;2\n3;4\n", {}, "csv", st),
        ("markdown", MARKDOWN, {}, "markdown", st),
        ("python-shebang", PYTHON, {}, "python", st),
        ("shell-shebang", SHELL, {}, "shell", st),
        ("node-shebang", NODE, {}, "javascript", st),
        ("python-by-name", b"import os\nprint(os.sep)\n", {"name": "tool.py"}, "python", d),
        ("c-by-name", b"int main(void) { return 0; }\n", {"name": "main.c"}, "c", d),
        ("yaml-by-name", b"key: value\nlist:\n  - a\n", {"name": "conf.yaml"}, "yaml", d),
        ("toml-by-name", b'[tool]\nname = "x"\n', {"name": "config.toml"}, "toml", d),
        ("pyproject", b'[project]\nname = "x"\ndependencies = ["numpy==2.3.5"]\n', {"name": "pyproject.toml"},
         "pyproject", st),
        ("requirements", REQUIREMENTS, {"name": "requirements.txt"}, "requirements", st),
        ("requirements-dev", b"pytest==9.2.0\n", {"name": "requirements-dev.txt"}, "requirements", st),
        ("package-json", b'{"name": "x", "version": "1.0.0", "dependencies": {"a": "^1.0.0"}}',
         {"name": "package.json"}, "package-json", st),
        ("ini-by-name", b"[section]\nkey=value\n", {"name": "setup.cfg"}, "ini", d),
        ("yaml-declared", b"key: value\n", {"declared": "application/yaml"}, "yaml", d),
    ]


# Adversarial and conflicting cases: (label, bytes, kwargs, expected format, expected basis)
def conflicts():
    s, st = "signature", "structure"
    return [
        ("png-named-csv", _png(), {"name": "data.csv", "declared": "text/plain"}, "png", s),
        ("csv-named-png", CSV, {"name": "image.png", "declared": "image/png"}, "csv", st),
        ("text-declared-pdf", TEXT, {"declared": "application/pdf"}, "text", st),
        ("pickle-named-json", _pickle(2), {"name": "data.json"}, "pickle", s),
        ("json-named-pkl", JSON, {"name": "data.pkl"}, "json", st),
        ("zip-named-png", _zip([("a.txt", b"x")]), {"name": "a.png"}, "zip", s),
        ("stl-size-mismatch", _stl_binary(2), {"size": 84 + 100 + 1}, "unknown", "none"),
        ("bm-text", b"BMW cars are fast, and this line is just text.\n", {}, "text", st),
        ("riff-unknown-form", b"RIFF\x10\x00\x00\x00ABCD" + b"\x00" * 8, {}, "unknown", "none"),
        ("ftyp-unknown-brand", _ftyp(b"zzzz"), {}, "unknown", "none"),
        ("ebml-unknown-doctype", _ebml("other"), {}, "unknown", "none"),
        ("mz-no-pe", b"MZ" + b"\x00" * 62, {}, "unknown", "none"),
        ("lone-frame-sync", b"\xff\xfb\x90\x00" + b"\x00" * 50, {}, "unknown", "none"),
        ("tar-bad-checksum", _tar()[:148] + b"0000000\x00" + _tar()[156:], {}, "unknown", "none"),
        ("cafebabe-ambiguous", b"\xca\xfe\xba\xbe\x00\x00\x00\x28", {}, "unknown", "none"),
        ("empty", b"", {}, "unknown", "none"),
        ("latin1-text", "café crème brûlée".encode("latin-1"), {}, "unknown", "none"),
        ("lt-in-prose", b"if a < b then this is prose, not markup\n", {}, "text", st),
        ("one-line-commas", b"Hello, world\n", {}, "text", st),
        ("json-scalar", b"42\n", {}, "text", st),
        ("broken-json", b'{"a": 1,, "b"}', {}, "text", st),
        ("pyproject-not-toml", b"[project\nname=", {"name": "pyproject.toml"}, "toml", "declared"),
        ("requirements-prose", b"This is prose, not a list of requirements.\n", {"name": "requirements.txt"}, "text", st),
        ("package-json-array", b"[1, 2, 3]", {"name": "package.json"}, "json", st),
        ("package-json-elsewhere", b'{"name": "x"}', {"name": "notpackage.json"}, "json", st),
        ("bare-tags-named-xml", b"<a>" * 1000 + b"</a>" * 1000, {"name": "deep.xml"}, "xml", st),
        ("bare-tags", b"<b>bold</b>", {}, "xml", st),
        ("bare-html-tags", b"<div><p>Hi</p></div>", {}, "html", st),
    ]
