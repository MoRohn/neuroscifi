"""Video containers read for their structure only: ISO base media (MP4, MOV, 3GP), Matroska/WebM (EBML), AVI.

NO FRAME IS DECODED: no video decoder exists on this host. What is read: the container's box/element tree, the
duration, and per track its kind, codec, dimensions, frame rate, sample count, audio channels and rate, language
and name, as the headers state them. The representation is a `Tree`: the root holds a `tracks` node (one child per
track, its facts as attributes) and a `structure` node (the container's boxes or elements, with offsets and
sizes). `tracks_table(tree)` turns the tracks into a `Table`. Track names, titles and tags are the artifact's own
words (untrusted). Frames of uncompressed or MJPEG video in AVI are not decoded either.

Bounds: at most MAX_NODES boxes/elements, nesting at most MAX_DEPTH, `max_members` tracks; every size is checked
against the bytes present; nothing is read past the end of the input.
"""
from __future__ import annotations

import struct

import numpy as np

from .. import repr_b2 as R
from ..safe import Deadline, injection_notes
from ..safe_b2 import Reader, members
from ..types import Column, Decoded, HeaderOnly, Limits, Malformed, Node, Table, Tree
from . import Adapter

MAX_NODES = 20_000
MAX_DEPTH = 16
NO_FRAMES = "no frame is decoded: no video decoder exists on this host"

# ------------------------------------------------------------------------------------------------- ISO base media

ISO_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"udta", b"dinf", b"mvex", b"moof", b"traf",
                  b"iprp", b"ipco", b"meta", b"ilst", b"sinf", b"schi", b"mfra", b"tref", b"gmhd"}
FULL_BOX_CONTAINERS = {b"meta"}


def iso_boxes(data: bytes, start: int, end: int, depth: int, state: dict):
    """(type, offset, payload_start, end, children) for each box in data[start:end], recursing into containers."""
    pos = start
    out = []
    while pos + 8 <= end:
        state["n"] += 1
        if state["n"] > MAX_NODES:
            raise Malformed("the container holds more boxes than are read here")
        size, kind = struct.unpack(">I4s", data[pos:pos + 8])
        head = 8
        if size == 1:
            if pos + 16 > end:
                raise Malformed("a box's 64-bit size is cut short")
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
            head = 16
        elif size == 0:
            size = end - pos
        if size < head or pos + size > end:
            state["cut"] = True
            size = end - pos
            if size < head:
                break
        payload = pos + head
        children = []
        if kind in ISO_CONTAINERS and depth < MAX_DEPTH:
            inner = payload + (4 if kind in FULL_BOX_CONTAINERS else 0)
            if kind == b"meta" and data[payload + 4:payload + 8] == b"hdlr":      # QuickTime meta: no version field
                inner = payload
            children = iso_boxes(data, inner, pos + size, depth + 1, state)
        out.append((kind, pos, payload, pos + size, children))
        pos += size
    return out


def _find(boxes, path):
    """All boxes reached by the path of types (e.g. [b'mdia', b'mdhd'])."""
    found = [b for b in boxes if b[0] == path[0]]
    if len(path) == 1:
        return found
    return [x for b in found for x in _find(b[4], path[1:])]


def _first(boxes, path):
    found = _find(boxes, path)
    return found[0] if found else None


def _full(data, box):
    """(version, payload after version/flags) of a full box."""
    return data[box[2]], box[2] + 4


def _mvhd(data, box):
    v, p = _full(data, box)
    if v == 1:
        _, _, scale, dur = struct.unpack(">QQIQ", data[p:p + 28])
    else:
        _, _, scale, dur = struct.unpack(">IIII", data[p:p + 16])
    return scale, dur


def _track(data, trak, notes) -> dict:
    t: dict = {}
    tkhd = _first(trak[4], [b"tkhd"])
    if tkhd:
        v, p = _full(data, tkhd)
        r = Reader(data, ">", p, "a tkhd box")
        if v == 1:
            r.skip(16)
            t["track_id"] = r.u32()
            r.skip(12)
        else:
            r.skip(8)
            t["track_id"] = r.u32()
            r.skip(8)
        r.skip(8 + 4 + 4 + 36)
        w, h = r.unpack("II")
        if w or h:
            t["width"], t["height"] = w / 65536, h / 65536
    mdhd = _first(trak[4], [b"mdia", b"mdhd"])
    if mdhd:
        scale, dur = _mvhd(data, mdhd)
        v, p = _full(data, mdhd)
        lang = struct.unpack(">H", data[p + (28 if v == 1 else 16):p + (30 if v == 1 else 18)])[0]
        t["timescale"] = scale
        if scale:
            t["duration_s"] = dur / scale
        code = "".join(chr(((lang >> s) & 31) + 0x60) for s in (10, 5, 0))
        if code.isalpha() and code != "und":
            t["language"] = code
    hdlr = _first(trak[4], [b"mdia", b"hdlr"])
    if hdlr:
        handler = data[hdlr[2] + 8:hdlr[2] + 12]
        t["kind"] = {b"vide": "video", b"soun": "audio", b"text": "text", b"sbtl": "subtitle", b"subt": "subtitle",
                     b"meta": "metadata", b"hint": "hint", b"tmcd": "timecode"}.get(handler,
                                                                                  handler.decode("latin-1"))
        name = data[hdlr[2] + 24:hdlr[3]].split(b"\x00", 1)[0]
        if name[:1] and name[0] == len(name) - 1:
            name = name[1:]                                    # QuickTime Pascal string
        name = name.decode("utf-8", "replace").strip()
        if name:
            t["name"] = name
    stsd = _first(trak[4], [b"mdia", b"minf", b"stbl", b"stsd"])
    if stsd and stsd[3] - stsd[2] >= 16:
        p = stsd[2] + 8
        size, codec = struct.unpack(">I4s", data[p:p + 8])
        t["codec"] = codec.decode("latin-1")
        e = p + 8 + 8         # reserved(6) + data reference index(2)
        if t.get("kind") == "video" and e + 28 <= len(data):
            w, h = struct.unpack(">HH", data[e + 16:e + 20])
            t["coded_width"], t["coded_height"] = w, h
        elif t.get("kind") == "audio" and e + 20 <= len(data):
            ver = struct.unpack(">H", data[e:e + 2])[0]
            ch, bits = struct.unpack(">HH", data[e + 8:e + 12])
            rate = struct.unpack(">I", data[e + 16:e + 20])[0] / 65536
            t["channels"], t["sample_bits"], t["sample_rate_hz"] = ch, bits, rate
            if ver == 2:
                notes.append("a QuickTime version-2 sound description: channel and rate fields may be placeholders")
    stsz = _first(trak[4], [b"mdia", b"minf", b"stbl", b"stsz"])
    if stsz:
        t["sample_count"] = struct.unpack(">I", data[stsz[2] + 8:stsz[2] + 12])[0]
    else:
        stts = _first(trak[4], [b"mdia", b"minf", b"stbl", b"stts"])
        if stts:
            n = struct.unpack(">I", data[stts[2] + 4:stts[2] + 8])[0]
            total, p = 0, stts[2] + 8
            for _ in range(min(n, 100_000)):
                if p + 8 > stts[3]:
                    break
                total += struct.unpack(">I", data[p:p + 4])[0]
                p += 8
            t["sample_count"] = total
    if t.get("kind") == "video" and t.get("sample_count") and t.get("duration_s"):
        t["frame_rate"] = round(t["sample_count"] / t["duration_s"], 3)
    return t


def _iso_words(data, boxes) -> list[str]:
    words = []
    ilst = _find(boxes, [b"moov", b"udta", b"meta", b"ilst"]) + _find(boxes, [b"moov", b"meta", b"ilst"])
    for lst in ilst:
        for p in iso_boxes(data, lst[2], lst[3], MAX_DEPTH, {"n": 0}):
            d = _first(iso_boxes(data, p[2], p[3], MAX_DEPTH, {"n": 0}), [b"data"])
            if d and d[3] - d[2] > 8 and struct.unpack(">I", data[d[2]:d[2] + 4])[0] & 0xFFFFFF == 1:
                key = p[0].decode("latin-1").replace("\xa9", "")
                words.append(f"{key}: {data[d[2] + 8:d[3]].decode('utf-8', 'replace')}")
    return words


def read_iso(data: bytes, limits: Limits) -> dict:
    """Facts of an ISO base media file: brands, duration, tracks, words, the box list."""
    deadline = Deadline(limits.max_seconds)
    state = {"n": 0, "cut": False}
    boxes = iso_boxes(data, 0, len(data), 0, state)
    deadline.check("reading ISO boxes")
    if not boxes:
        raise Malformed("no ISO base media box was found")
    notes = []
    facts: dict = {}
    ftyp = _first(boxes, [b"ftyp"])
    if ftyp:
        facts["major_brand"] = data[ftyp[2]:ftyp[2] + 4].decode("latin-1")
        facts["compatible_brands"] = [data[i:i + 4].decode("latin-1") for i in range(ftyp[2] + 8, ftyp[3], 4)][:16]
    traks = _find(boxes, [b"moov", b"trak"])
    members(len(traks), limits, "tracks")
    try:
        mvhd = _first(boxes, [b"moov", b"mvhd"])
        if mvhd:
            scale, dur = _mvhd(data, mvhd)
            if scale:
                facts["duration_s"] = dur / scale
        tracks = [_track(data, t, notes) for t in traks]
    except (struct.error, IndexError):
        raise Malformed("a movie or track header box is cut short") from None
    if _first(boxes, [b"moof"]):
        notes.append("fragmented file (moof boxes): sample counts cover the initial movie box only")
    if not _first(boxes, [b"moov"]):
        notes.append("no movie box (moov) was found: tracks are unknown")
    if state["cut"]:
        notes.append("a box declares more bytes than the file holds (the file may be cut short)")
    return {"boxes": boxes, "facts": facts, "tracks": tracks, "words": _iso_words(data, boxes), "notes": notes}


def _iso_tree(data, boxes) -> list[Node]:
    out = []
    for kind, pos, _, end, children in boxes:
        out.append(Node(kind.decode("latin-1"), None, {"offset": str(pos), "size": str(end - pos)},
                        tuple(_iso_tree(data, children))))
    return out


# --------------------------------------------------------------------------------------------------- EBML / Matroska

EBML_MASTER = {0x18538067: "Segment", 0x1549A966: "Info", 0x1654AE6B: "Tracks", 0xAE: "TrackEntry", 0xE0: "Video",
               0xE1: "Audio", 0x1254C367: "Tags", 0x7373: "Tag", 0x67C8: "SimpleTag", 0x1A45DFA3: "EBML",
               0x1043A770: "Chapters", 0x45B9: "EditionEntry", 0xB6: "ChapterAtom", 0x80: "ChapterDisplay",
               0x1941A469: "Attachments", 0x61A7: "AttachedFile", 0x63C0: "Targets", 0x6D80: "ContentEncodings"}
EBML_NAMES = {0x1F43B675: "Cluster", 0x1C53BB6B: "Cues", 0x114D9B74: "SeekHead", 0xEC: "Void", 0x4282: "DocType",
              0x2AD7B1: "TimecodeScale", 0x4489: "Duration", 0x7BA9: "Title", 0x4D80: "MuxingApp",
              0x5741: "WritingApp", 0xD7: "TrackNumber", 0x83: "TrackType", 0x86: "CodecID", 0x536E: "Name",
              0x22B59C: "Language", 0x23E383: "DefaultDuration", 0xB0: "PixelWidth", 0xBA: "PixelHeight",
              0xB5: "SamplingFrequency", 0x9F: "Channels", 0x6264: "BitDepth", 0x45A3: "TagName",
              0x4487: "TagString", 0x85: "ChapString", 0x466E: "FileName", 0x4660: "FileMimeType"}
EBML_STRINGS = {0x4282, 0x7BA9, 0x4D80, 0x5741, 0x86, 0x536E, 0x22B59C, 0x45A3, 0x4487, 0x85, 0x466E, 0x4660}
EBML_UINTS = {0x2AD7B1, 0xD7, 0x83, 0x23E383, 0xB0, 0xBA, 0x9F, 0x6264}
EBML_FLOATS = {0x4489, 0xB5}


def _vint(data: bytes, pos: int, keep_marker: bool) -> tuple[int, int, bool]:
    if pos >= len(data):
        raise Malformed("an EBML number is cut short")
    first = data[pos]
    if first == 0:
        raise Malformed("an EBML number is longer than 8 bytes")
    n = 9 - first.bit_length()
    if pos + n > len(data):
        raise Malformed("an EBML number is cut short")
    value = int.from_bytes(data[pos:pos + n], "big")
    unknown = False
    if not keep_marker:
        value &= (1 << (7 * n)) - 1
        unknown = value == (1 << (7 * n)) - 1
    return value, pos + n, unknown


def ebml_elements(data: bytes, start: int, end: int, depth: int, state: dict) -> list[dict]:
    out = []
    pos = start
    while pos < end:
        state["n"] += 1
        if state["n"] > MAX_NODES:
            raise Malformed("the container holds more elements than are read here")
        eid, p, _ = _vint(data, pos, True)
        size, p, unknown = _vint(data, p, False)
        if unknown:
            size = end - p
            state["unknown_size"] = True
        if p + size > end:
            state["cut"] = True
            size = end - p
        el = {"id": eid, "pos": pos, "data": p, "end": p + size, "children": []}
        if eid == 0x1F43B675:                     # Cluster: media data, counted not walked
            state["clusters"] = state.get("clusters", 0) + 1
            if unknown:
                out.append(el)
                break
        elif eid in EBML_MASTER and depth < MAX_DEPTH:
            el["children"] = ebml_elements(data, p, p + size, depth + 1, state)
        elif eid in EBML_STRINGS:
            el["value"] = data[p:p + size].split(b"\x00", 1)[0].decode("utf-8", "replace")
        elif eid in EBML_UINTS and size <= 8:
            el["value"] = int.from_bytes(data[p:p + size], "big")
        elif eid in EBML_FLOATS and size in (4, 8):
            el["value"] = struct.unpack(">f" if size == 4 else ">d", data[p:p + size])[0]
        out.append(el)
        pos = p + size
    return out


def _ebml_get(els, eid):
    for e in els:
        if e["id"] == eid:
            return e
    return None


def _ebml_all(els, eid):
    out = []
    for e in els:
        if e["id"] == eid:
            out.append(e)
        out.extend(_ebml_all(e["children"], eid))
    return out


def read_ebml(data: bytes, limits: Limits) -> dict:
    state = {"n": 0}
    els = ebml_elements(data, 0, len(data), 0, state)
    notes, facts, tracks, words = [], {}, [], []
    header = _ebml_get(els, 0x1A45DFA3)
    if header is None:
        raise Malformed("the EBML header is missing")
    doctype = _ebml_get(header["children"], 0x4282)
    facts["doc_type"] = doctype["value"] if doctype else None
    seg = _ebml_get(els, 0x18538067)
    if seg is None:
        raise Malformed("the Matroska segment is missing")
    info = _ebml_get(seg["children"], 0x1549A966)
    if info:
        scale = (_ebml_get(info["children"], 0x2AD7B1) or {}).get("value", 1_000_000)
        dur = (_ebml_get(info["children"], 0x4489) or {}).get("value")
        if dur is not None and np.isfinite(dur):
            facts["duration_s"] = dur * scale / 1e9
        for eid, key in ((0x7BA9, "title"), (0x4D80, "muxing_app"), (0x5741, "writing_app")):
            v = (_ebml_get(info["children"], eid) or {}).get("value")
            if v:
                facts[key] = v
                if eid == 0x7BA9:
                    words.append(f"title: {v}")
    for entry in _ebml_all(seg["children"], 0xAE):
        c = entry["children"]

        def val(eid, default=None, among=c):
            return (_ebml_get(among, eid) or {}).get("value", default)
        kind = {1: "video", 2: "audio", 3: "complex", 16: "logo", 17: "subtitle", 18: "buttons",
                32: "control", 33: "metadata"}.get(val(0x83), "unknown")
        t = {"track_id": val(0xD7), "kind": kind, "codec": val(0x86), "language": val(0x22B59C), "name": val(0x536E)}
        dd = val(0x23E383)
        if dd:
            t["frame_rate"] = round(1e9 / dd, 3)
        video = _ebml_get(c, 0xE0)
        if video:
            t["width"], t["height"] = val(0xB0, among=video["children"]), val(0xBA, among=video["children"])
        audio = _ebml_get(c, 0xE1)
        if audio:
            t["sample_rate_hz"] = val(0xB5, among=audio["children"])
            t["channels"] = val(0x9F, 1, among=audio["children"])
            t["sample_bits"] = val(0x6264, among=audio["children"])
        if t["name"]:
            words.append(f"track name: {t['name']}")
        tracks.append({k: v for k, v in t.items() if v is not None})
    members(len(tracks), limits, "tracks")
    for tag in _ebml_all(seg["children"], 0x67C8):
        name = (_ebml_get(tag["children"], 0x45A3) or {}).get("value")
        text = (_ebml_get(tag["children"], 0x4487) or {}).get("value")
        if name and text:
            words.append(f"{name}: {text}")
    if state.get("clusters"):
        facts["clusters_seen"] = state["clusters"]
    if state.get("unknown_size"):
        notes.append("an element of unknown size (live stream): reading stopped at the first cluster")
    if state.get("cut"):
        notes.append("an element declares more bytes than the file holds (the file may be cut short)")
    if _ebml_all(seg["children"], 0x61A7):
        notes.append("attached files are present; not read")
    return {"elements": els, "facts": facts, "tracks": tracks, "words": words, "notes": notes}


def _ebml_tree(els) -> list[Node]:
    out = []
    for e in els:
        name = EBML_MASTER.get(e["id"]) or EBML_NAMES.get(e["id"]) or f"0x{e['id']:X}"
        attrs = {"offset": str(e["pos"]), "size": str(e["end"] - e["pos"])}
        value = e.get("value")
        if isinstance(value, str):
            value = value[:200]
        out.append(Node(name, value, attrs, tuple(_ebml_tree(e["children"]))))
    return out


# --------------------------------------------------------------------------------------------------------------- AVI


def _riff_chunks(data, start, end, depth, state):
    out = []
    pos = start
    while pos + 8 <= end:
        state["n"] += 1
        if state["n"] > MAX_NODES:
            raise Malformed("the AVI file holds more chunks than are read here")
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body_end = pos + 8 + size
        if body_end > end:
            state["cut"] = True
            body_end = end
        ch = {"id": cid, "pos": pos, "data": pos + 8, "end": body_end, "list": None, "children": []}
        if cid in (b"LIST", b"RIFF") and body_end - pos >= 12:
            ch["list"] = data[pos + 8:pos + 12]
            if ch["list"] != b"movi" and depth < MAX_DEPTH:
                ch["children"] = _riff_chunks(data, pos + 12, body_end, depth + 1, state)
            else:
                state["movi"] = True
        out.append(ch)
        pos = body_end + (size & 1)
    return out


def read_avi(data: bytes, limits: Limits) -> dict:
    state = {"n": 0}
    top = _riff_chunks(data, 0, len(data), 0, state)
    if not top or top[0]["list"] != b"AVI ":
        raise Malformed("the RIFF form type is not AVI")
    root = top[0]["children"]
    facts, tracks, words, notes = {}, [], [], []
    hdrl = next((c for c in root if c["list"] == b"hdrl"), None)
    if hdrl is None:
        raise Malformed("the AVI header list is missing")
    avih = next((c for c in hdrl["children"] if c["id"] == b"avih"), None)
    if avih and avih["end"] - avih["data"] >= 40:
        usec, _, _, _, frames, _, streams, _, w, h = struct.unpack("<10I", data[avih["data"]:avih["data"] + 40])
        facts.update({"frames": frames, "streams": streams, "width": w, "height": h})
        if usec:
            facts["frame_rate"] = round(1e6 / usec, 3)
            facts["duration_s"] = frames * usec / 1e6
    for strl in (c for c in hdrl["children"] if c["list"] == b"strl"):
        t = {"track_id": len(tracks) + 1}
        for c in strl["children"]:
            body = data[c["data"]:c["end"]]
            if c["id"] == b"strh" and len(body) >= 36:
                kind, handler = body[:4], body[4:8]
                scale, rate, _, length = struct.unpack("<4I", body[20:36])
                t["kind"] = {b"vids": "video", b"auds": "audio", b"txts": "text", b"mids": "midi"}.get(
                    kind, kind.decode("latin-1"))
                if kind == b"vids":
                    t["codec"] = handler.decode("latin-1").strip("\x00 ") or None
                    if scale:
                        t["frame_rate"] = round(rate / scale, 3)
                    t["sample_count"] = length
            elif c["id"] == b"strf":
                if t.get("kind") == "video" and len(body) >= 20:
                    w, h, _, bits, comp = struct.unpack("<iiHHI", body[4:20])
                    t["width"], t["height"] = w, abs(h)
                    t["codec"] = struct.pack("<I", comp).decode("latin-1").strip("\x00 ") if comp > 3 else (
                        "uncompressed" if comp == 0 else f"BMP compression {comp}")
                elif t.get("kind") == "audio" and len(body) >= 16:
                    tag, ch, rate, _, _, bits = struct.unpack("<HHIIHH", body[:16])
                    t["codec"] = f"wave format {tag:#06x}"
                    t["channels"], t["sample_rate_hz"], t["sample_bits"] = ch, rate, bits
            elif c["id"] == b"strn":
                t["name"] = body.split(b"\x00", 1)[0].decode("latin-1")
                words.append(f"stream name: {t['name']}")
        tracks.append({k: v for k, v in t.items() if v is not None})
    members(len(tracks), limits, "tracks")
    for info in (c for c in root if c["list"] == b"INFO"):
        for c in info["children"]:
            v = data[c["data"]:c["end"]].split(b"\x00", 1)[0].decode("latin-1").strip()
            if v:
                words.append(f"{c['id'].decode('latin-1')}: {v}")
    if any(c["list"] == b"AVIX" for c in top[1:]):
        notes.append("OpenDML extension (AVIX) present: frame counts may cover the first part only")
    if state.get("cut"):
        notes.append("a chunk declares more bytes than the file holds (the file may be cut short)")
    notes.append("frames of uncompressed or MJPEG video in AVI are not decoded either")
    return {"chunks": top, "facts": facts, "tracks": tracks, "words": words, "notes": notes}


def _avi_tree(chunks) -> list[Node]:
    out = []
    for c in chunks:
        name = c["id"].decode("latin-1") + (f" {c['list'].decode('latin-1')}" if c["list"] else "")
        out.append(Node(name, None, {"offset": str(c["pos"]), "size": str(c["end"] - c["pos"])},
                        tuple(_avi_tree(c["children"]))))
    return out


# ----------------------------------------------------------------------------------------------------- representation

TRACK_COLUMNS = (("track_id", "integer"), ("kind", "text"), ("codec", "text"), ("duration_s", "number"),
                 ("width", "number"), ("height", "number"), ("frame_rate", "number"), ("sample_count", "integer"),
                 ("channels", "integer"), ("sample_rate_hz", "number"), ("language", "text"), ("name", "text"))


def _fmt(v) -> str:
    return f"{v:.6g}" if isinstance(v, float) else str(v)


def build_tree(fmt: str, facts: dict, tracks: list[dict], structure: list[Node]) -> Tree:
    track_nodes = tuple(Node("track", None, {k: _fmt(v)[:200] for k, v in t.items()}, ()) for t in tracks)
    attrs = {k: _fmt(v)[:200] for k, v in facts.items() if not isinstance(v, list)}
    return Tree(Node(fmt, None, attrs, (Node("tracks", None, {"count": str(len(tracks))}, track_nodes),
                                        Node("structure", None, {}, tuple(structure)))))


def tracks_table(tree: Tree) -> Table:
    """The tracks node of a container tree as a Table (missing numbers NaN, missing text "")."""
    tracks_node = next((c for c in tree.root.children if c.name == "tracks"), None)
    rows = [dict(t.attrs) for t in (tracks_node.children if tracks_node else ())]
    cols = []
    for name, kind in TRACK_COLUMNS:
        raw = [r.get(name) for r in rows]
        if kind == "text":
            cols.append(Column(name, "text", None, tuple(v or "" for v in raw), sum(v is None for v in raw)))
            continue
        vals = []
        for v in raw:
            try:
                vals.append(float(v) if v is not None else np.nan)
            except ValueError:
                vals.append(np.nan)
        arr = np.array(vals, np.float64)
        unit = {"duration_s": "s", "frame_rate": "1/s", "sample_rate_hz": "Hz"}.get(name)
        cols.append(Column(name, "number" if kind == "number" else "integer", unit, arr, int(np.isnan(arr).sum())))
    return Table(tuple(cols), len(rows), "tracks")


def _decoded(detected, fmt, info, structure) -> Decoded:
    text = "\n".join(info["words"])
    notes = [NO_FRAMES] + info["notes"] + list(injection_notes(text))
    if text:
        notes.append(f"{len(info['words'])} title/name/tag field(s) read as the artifact's own words: " +
                     "they are listed in the tree only by count")
    tree = build_tree(fmt, info["facts"], info["tracks"], structure)
    return Decoded(detected, R.Tree(tree.root, words=text), tuple(notes), False)


def audio_only_iso(info: dict) -> bool:
    kinds = {t.get("kind") for t in info["tracks"]}
    return "audio" in kinds and "video" not in kinds


class ISOAdapter(Adapter):
    name = "b2-isobmff"
    reads = "structure"
    formats = ("mp4", "mov", "3gp")
    families = ("video",)

    def decode(self, data, detected, limits):
        info = read_iso(data, limits)
        return _decoded(detected, detected.format, info, _iso_tree(data, info["boxes"]))


class MatroskaAdapter(Adapter):
    name = "b2-matroska"
    reads = "structure"
    formats = ("mkv", "webm")
    families = ("video",)

    def decode(self, data, detected, limits):
        info = read_ebml(data, limits)
        if info["tracks"] and all(t.get("kind") == "audio" for t in info["tracks"]):
            t = info["tracks"][0]
            raise HeaderOnly(f"audio-only {detected.format} ({t.get('codec')}): samples are not decoded here",
                             facts={"format": detected.format, **info["facts"], **t},
                             needs=(f"a {t.get('codec')} decoder",), size=len(data), words="\n".join(info["words"]))
        return _decoded(detected, detected.format, info, _ebml_tree(info["elements"]))


class AVIAdapter(Adapter):
    name = "b2-avi"
    reads = "structure"
    formats = ("avi",)
    families = ("video",)

    def decode(self, data, detected, limits):
        info = read_avi(data, limits)
        return _decoded(detected, "avi", info, _avi_tree(info["chunks"]))


ADAPTERS = (ISOAdapter(), MatroskaAdapter(), AVIAdapter())

__all__ = ["ADAPTERS", "AVIAdapter", "ISOAdapter", "MatroskaAdapter", "NO_FRAMES", "audio_only_iso", "build_tree",
           "ebml_elements", "iso_boxes", "read_avi", "read_ebml", "read_iso", "tracks_table"]
