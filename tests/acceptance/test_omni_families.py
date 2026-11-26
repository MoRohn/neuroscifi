"""The OMNIMIND benchmark's honest, unknown, compound and transform families against independent readings
(lane Z). Header facts of the hand-built JPEG, WebP, TIFF, MP3, FLAC, Ogg, MP4, WebM, Parquet, HDF5 and compound
files are re-read here by parsers written in this file; disguised files are unwrapped with the standard library (and
zstandard) and handed to the oracle; compound members are reached through zipfile/tarfile; transform tasks are
re-derived from their inputs and made by the reference maker, which the judge must call right, and flawed, which
it must catch."""
import gzip
import io
import json
import re
import struct
import zipfile

import numpy as np
import pytest

from neuroscifi.acceptance import omni
from neuroscifi.acceptance.omni import honest, transform, unknown
from neuroscifi.acceptance.omni.cases import UNKNOWN

import omni_oracle as oracle
import omni_reference_maker as reference

# ---- honest: header parsers written here -------------------------------------------------------------------------


def _segments(data):
    pos, out = 2, []
    while pos < len(data):
        marker = struct.unpack(">H", data[pos:pos + 2])[0]
        length = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        out.append((marker, data[pos + 4:pos + 2 + length]))
        pos += 2 + length
        if marker == 0xFFDA:
            out.append(("scan", data[pos:data.rindex(b"\xff\xd9")]))
            break
    return out


def jpeg_read(data):
    segs = dict((m, b) for m, b in _segments(data) if m != "scan")
    sof = segs.get(0xFFC0) or segs.get(0xFFC2)
    h, w = struct.unpack(">HH", sof[1:5])
    out = {"size": [w, h]}
    if 0xFFC0 not in segs:
        return out
    tables = {}
    for m, b in _segments(data):
        if m == 0xFFC4:
            counts, values, code, k, table = b[1:17], b[17:], 0, 0, {}
            for length in range(1, 17):
                for _ in range(counts[length - 1]):
                    table[(length, code)] = values[k]
                    code, k = code + 1, k + 1
                code <<= 1
            tables[b[0]] = table
    scan = dict((m, b) for m, b in _segments(data))["scan"].replace(b"\xff\x00", b"\xff")
    bits = "".join(format(byte, "08b") for byte in scan)
    pos, prev, values = 0, 0, []

    def symbol(table):
        nonlocal pos
        code = 0
        for length in range(1, 17):
            code = (code << 1) | int(bits[pos])
            pos += 1
            if (length, code) in table:
                return table[(length, code)]
        raise AssertionError("no code")
    for _ in range((h // 8) * (w // 8)):
        size = symbol(tables[0x00])
        diff = 0
        if size:
            raw = int(bits[pos:pos + size], 2)
            pos += size
            diff = raw if raw >= 1 << (size - 1) else raw - (1 << size) + 1
        prev += diff
        assert symbol(tables[0x10]) == 0                          # end of block: no AC coefficient
        values.append(prev / 8 + 128)
    blocks = np.array(values).reshape(h // 8, w // 8)
    out["pixels"] = np.kron(blocks, np.ones((8, 8)))
    return out


def webp_size(data):
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP" and data[12:16] == b"VP8X"
    w = int.from_bytes(data[24:27], "little") + 1
    h = int.from_bytes(data[27:30], "little") + 1
    vp8 = data.index(b"VP8 ") + 8
    assert data[vp8 + 3:vp8 + 6] == b"\x9d\x01\x2a"
    assert struct.unpack("<HH", data[vp8 + 6:vp8 + 10]) == (w, h)
    return [w, h]


def tiff_size(data):
    assert data[:4] == b"II*\x00"
    ifd = struct.unpack("<I", data[4:8])[0]
    tags = {}
    for i in range(struct.unpack("<H", data[ifd:ifd + 2])[0]):
        tag, typ, _, value = struct.unpack("<HHII", data[ifd + 2 + 12 * i:ifd + 14 + 12 * i])
        tags[tag] = value & 0xFFFF if typ == 3 else value
    assert tags[259] == 7
    return [tags[256], tags[257]]


def mp3_facts(data):
    size = sum((b & 0x7F) << (7 * (3 - i)) for i, b in enumerate(data[6:10]))
    pos, frames = 10 + size, 0
    while pos + 4 <= len(data):
        h = int.from_bytes(data[pos:pos + 4], "big")
        assert h >> 21 == 0x7FF
        rate = (44100, 48000, 32000)[(h >> 10) & 3]
        kbps = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)[(h >> 12) & 0xF]
        pos += 144 * kbps * 1000 // rate + ((h >> 9) & 1)
        frames += 1
    return {"rate": rate, "channels": 1 if (h >> 6) & 3 == 3 else 2, "duration": frames * 1152 / rate}


def flac_facts(data):
    assert data[:4] == b"fLaC"
    v = int.from_bytes(data[18:26], "big")
    rate, ch, total = v >> 44, ((v >> 41) & 7) + 1, v & ((1 << 36) - 1)
    return {"rate": rate, "channels": ch, "duration": total / rate}


def _ogg_crc_bitwise(data):
    crc = 0
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
    return crc


def ogg_facts(data):
    pages, pos = [], 0
    while pos < len(data):
        assert data[pos:pos + 4] == b"OggS"
        n = data[pos + 26]
        segs = data[pos + 27:pos + 27 + n]
        end = pos + 27 + n + sum(segs)
        page = bytearray(data[pos:end])
        crc = struct.unpack("<I", page[22:26])[0]
        page[22:26] = b"\x00\x00\x00\x00"
        assert _ogg_crc_bitwise(bytes(page)) == crc
        pages.append((struct.unpack("<q", data[pos + 6:pos + 14])[0], data[pos + 27 + n:end]))
        pos = end
    ident = pages[0][1]
    assert ident[:7] == b"\x01vorbis"
    ch, rate = ident[11], struct.unpack("<I", ident[12:16])[0]
    return {"rate": rate, "channels": ch, "duration": pages[-1][0] / rate}


def mp4_facts(data):
    def boxes(b):
        pos = 0
        while pos < len(b):
            size = struct.unpack(">I", b[pos:pos + 4])[0]
            yield b[pos + 4:pos + 8], b[pos + 8:pos + size]
            pos += size
    top = dict(boxes(data))
    moov = dict(boxes(top[b"moov"]))
    timescale, duration = struct.unpack(">II", moov[b"mvhd"][12:20])
    tkhd = dict(boxes(moov[b"trak"]))[b"tkhd"]
    w, h = struct.unpack(">II", tkhd[-8:])
    return {"size": [w >> 16, h >> 16], "duration": duration / timescale}


def _ebml_walk(b):
    pos = 0
    while pos < len(b):
        first = b[pos]
        n = 8 - first.bit_length() + 1
        eid = int.from_bytes(b[pos:pos + n], "big")
        pos += n
        first = b[pos]
        m = 8 - first.bit_length() + 1
        size = int.from_bytes(b[pos:pos + m], "big") & ((1 << (7 * m)) - 1)
        pos += m
        yield eid, b[pos:pos + size]
        pos += size


def webm_facts(data):
    top = dict(_ebml_walk(data))
    assert dict(_ebml_walk(top[0x1A45DFA3]))[0x4282] == b"webm"
    seg = dict(_ebml_walk(top[0x18538067]))
    info = dict(_ebml_walk(seg[0x1549A966]))
    scale = int.from_bytes(info[0x2AD7B1], "big")
    duration = struct.unpack(">d", info[0x4489])[0] * scale / 1e9
    video = dict(_ebml_walk(dict(_ebml_walk(dict(_ebml_walk(seg[0x1654AE6B]))[0xAE]))[0xE0]))
    return {"size": [int.from_bytes(video[0xB0], "big"), int.from_bytes(video[0xBA], "big")], "duration": duration}


class _Thrift:
    """Thrift compact protocol, enough to read a struct generically."""

    def __init__(self, b):
        self.b, self.pos = b, 0

    def varint(self):
        out = shift = 0
        while True:
            byte = self.b[self.pos]
            self.pos += 1
            out |= (byte & 0x7F) << shift
            shift += 7
            if not byte & 0x80:
                return out

    def zz(self):
        v = self.varint()
        return (v >> 1) ^ -(v & 1)

    def value(self, t):
        if t in (1, 2):
            return t == 1
        if t in (5, 6):
            return self.zz()
        if t == 8:
            n = self.varint()
            self.pos += n
            return self.b[self.pos - n:self.pos]
        if t == 9:
            head = self.b[self.pos]
            self.pos += 1
            n, et = head >> 4, head & 0xF
            if n == 15:
                n = self.varint()
            return [self.value(et) for _ in range(n)]
        if t == 12:
            return self.struct()
        raise AssertionError(t)

    def struct(self):
        out, fid = {}, 0
        while True:
            head = self.b[self.pos]
            self.pos += 1
            if head == 0:
                return out
            fid += head >> 4
            out[fid] = self.value(head & 0xF)


def parquet_facts(data):
    assert data[:4] == b"PAR1" and data[-4:] == b"PAR1"
    n = struct.unpack("<I", data[-8:-4])[0]
    meta = _Thrift(data[-8 - n:-8]).struct()
    return {"rows": meta[3], "columns": [e[4].decode() for e in meta[2][1:]]}


def cfb_names(data):
    assert data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    first_dir = struct.unpack("<I", data[48:52])[0]
    sector = data[512 * (first_dir + 1):512 * (first_dir + 2)]
    names = []
    for i in range(4):
        e = sector[128 * i:128 * (i + 1)]
        n = struct.unpack("<H", e[64:66])[0]
        if n:
            names.append(e[:n - 2].decode("utf-16-le"))
    fat_sector = struct.unpack("<I", data[76:80])[0]
    assert struct.unpack("<I", data[512 * (fat_sector + 1):512 * (fat_sector + 1) + 4])[0] == 0xFFFFFFFD
    return names


def honest_reading(name, data, question):
    measure, _, arg = question.partition(":")
    if name.endswith((".jpg", ".jpeg")):
        r = jpeg_read(data)
        if measure == "image.size":
            return r["size"]
        if measure == "image.pixel":
            x, y = (int(v) for v in arg.split(","))
            return [int(r["pixels"][y, x])]
        if measure == "image.mean_gray":
            return float(r["pixels"].mean() / 255)
    if name.endswith(".webp") and measure == "image.size":
        return webp_size(data)
    if name.endswith((".tif", ".tiff")) and measure == "image.size":
        return tiff_size(data)
    for ext, fn in ((".mp3", mp3_facts), (".flac", flac_facts), ((".ogg", ".oga"), ogg_facts)):
        if name.endswith(ext):
            f = fn(data)
            return {"audio.rate_hz": f["rate"], "audio.channels": f["channels"],
                    "audio.duration_s": f["duration"]}[measure]
    if name.endswith((".mp4", ".m4v", ".webm")):
        f = (webm_facts if name.endswith(".webm") else mp4_facts)(data)
        return {"video.size": f["size"], "video.duration_s": f["duration"]}[measure]
    if name.endswith(".parquet"):
        f = parquet_facts(data)
        return {"table.n_rows": f["rows"], "table.columns": f["columns"]}[measure]
    raise oracle.NotOracle(question)


def test_honest_header_facts_agree_with_parsers_written_here():
    checked = 0
    for split in omni.SPLITS:
        for t in honest.tasks(split):
            if t.kind not in ("measure", "cannot_or_value") or t.question.startswith("detect."):
                continue
            (name, data), = t.files.items()
            assert omni.same(t.expect, honest_reading(name, data, t.question), t.tolerance), t.id
            checked += 1
    assert checked >= 40


def test_honest_containers_are_well_formed():
    for split in omni.SPLITS:
        for t in honest.tasks(split):
            (name, data), = t.files.items()
            if name.endswith((".doc", ".xls")):
                names = cfb_names(data)
                assert names[0] == "Root Entry" and ("WordDocument" in names or "Workbook" in names)
            if name.endswith((".h5", ".hdf5")):
                assert data[:8] == b"\x89HDF\r\n\x1a\n"


def test_every_honest_content_question_is_an_abstention_or_the_real_value():
    for split in omni.SPLITS:
        for t in honest.tasks(split):
            if not t.question.startswith(("detect.", "image.size", "audio.rate", "audio.channels", "audio.duration",
                                          "video.size", "video.duration", "table.n_rows", "table.columns")):
                assert t.kind in ("cannot", "cannot_or_value"), t.id
                if t.kind == "cannot_or_value":
                    assert t.files and next(iter(t.files)).endswith((".jpg", ".jpeg")), t.id


# ---- unknown: disguised files unwrapped and read ---------------------------------------------------------------------

_TRUE_NAME = {"csv": "x.csv", "csv_utf16": "x.csv", "json_bom": "x.json", "png": "x.png", "csv_gzip": "x.csv",
              "csv_zstd": "x.csv", "csv_gzip_zstd": "x.csv", "zip_in_gzip": "x.zip", "png_ancillary": "x.png",
              "wav_list": "x.wav", "pgm_comments": "x.pgm", "stl_solid_header": "x.stl", "bmp_top_down": "x.bmp",
              "ndjson_txt": "x.ndjson", "geojson_as_json": "x.geojson", "xml_latin1": "x.xml"}


def _unwrap(make, data):
    if make == "csv_utf16":
        return data[2:].decode("utf-16-le").encode()
    if make in ("csv_gzip", "zip_in_gzip"):
        return gzip.decompress(data)
    zstandard = pytest.importorskip("zstandard")
    if make == "csv_zstd":
        return zstandard.ZstdDecompressor().decompress(data)
    if make == "csv_gzip_zstd":
        return zstandard.ZstdDecompressor().decompress(gzip.decompress(data))
    return data


def test_disguised_files_hold_what_their_tasks_say():
    checked = 0
    for split in omni.SPLITS:
        cases = {c["name"]: c for c in UNKNOWN[split]}
        for t in unknown.tasks(split):
            case = cases[t.id.split("/")[2]]
            if case["kind"] != "disguised" or t.question.startswith("detect."):
                continue
            (_, data), = t.files.items()
            value = oracle.answer(_TRUE_NAME[case["make"]], _unwrap(case["make"], data), t.question)
            assert omni.same(t.expect, value, t.tolerance), (t.id, t.expect, value)
            checked += 1
    assert checked >= 30


def test_truly_unknown_files_are_not_a_known_container():
    for split in omni.SPLITS:
        cases = {c["name"]: c for c in UNKNOWN[split]}
        for t in unknown.tasks(split):
            case = cases[t.id.split("/")[2]]
            if case["kind"] != "unknown":
                continue
            (_, data), = t.files.items()
            if case["make"] not in ("head_of_png", "head_of_zip"):
                assert not data.startswith((b"\x89PNG", b"PK\x03\x04", b"\x1f\x8b", b"%PDF", b"GIF8", b"RIFF"))
                assert not zipfile.is_zipfile(io.BytesIO(data))
            if t.question == "detect.format" and case["make"] not in ("head_of_png", "head_of_zip"):
                assert t.expect == "unknown"


# ---- compound: members reached with the standard library -----------------------------------------------------------


def _compound_reading(name, data, question):
    parts = question.split("::")
    if name.endswith(".docx") and len(parts) == 2:
        member = zipfile.ZipFile(io.BytesIO(data)).read(parts[0])
        return oracle.answer(parts[0], member, parts[1])
    if parts[-1] == "doc.figure_members":
        md = zipfile.ZipFile(io.BytesIO(data)).read(parts[0]).decode()
        return re.findall(r"!\[.*?\]\((.*?)\)", md)
    return oracle.answer(name, data, question)


def test_compound_truths_agree_with_an_independent_reading():
    checked = 0
    for split in omni.SPLITS:
        for t in omni.family("compound").tasks(split):
            (name, data), = t.files.items()
            try:
                value = _compound_reading(name, data, t.question)
            except oracle.NotOracle:
                continue
            assert omni.same(t.expect, value, t.tolerance), (t.id, t.expect, value)
            checked += 1
    assert checked >= 70


# ---- transform: re-derived from the inputs, made by the reference maker ----------------------------------------------


def _headings_with_levels(name, data):
    if name.endswith(".md"):
        return [(len(m.group(1)), m.group(2).strip()) for m in re.finditer(r"^(#+)\s+(.*)$", data.decode(), re.M)]
    if name.endswith(".html"):
        return [(int(m.group(1)), m.group(2)) for m in re.finditer(r"<h([1-6])>(.*?)</h\1>", data.decode())]
    xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode()
    out = []
    for para in re.findall(r"<w:p>(.*?)</w:p>", xml):
        style = re.search(r'w:pStyle w:val="Heading(\d)"', para)
        if style:
            out.append((int(style.group(1)), "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", para))))
    return out


def reference_transform(task, flaw=None):
    request = json.loads(task.files["request.json"])
    (name, data), = [(n, d) for n, d in task.files.items() if n != "request.json"]
    kind = request["transform"]
    if kind in ("csv_to_chart", "table_to_sound"):
        rows = json.loads(data) if name.endswith(".json") else None
        if rows is None:
            text = data.decode()
            delim = "\t" if name.endswith(".tsv") else ","
            import csv
            parsed = list(csv.DictReader(io.StringIO(text, newline=""), delimiter=delim))
            rows = [{k: (float(v) if re.fullmatch(r"-?\d+(\.\d+)?", v) else v) for k, v in r.items()} for r in parsed]
        if kind == "csv_to_chart":
            xs, ys = [r[request["x"]] for r in rows], [r[request["y"]] for r in rows]
            made = ({"form": "line", "series": [{"name": "y", "x": xs, "y": ys}]} if request["form"] == "line"
                    else {"form": "scatter", "points": [[a, b] for a, b in zip(xs, ys)]})
        else:
            values = [r[request["column"]] for r in rows]
            if flaw == "order":
                values = values[::-1]
            made = {"form": "sonify", "values": values, "low_hz": request["low_hz"], "high_hz": request["high_hz"],
                    "domain": [min(values), max(values)], "tone_s": request["tone_s"]}
    elif kind == "mesh_to_stl":
        m = oracle.mesh_read(name, data)["corners"]
        verts = [list(p) for tri in m for p in tri]
        made = {"form": "mesh", "vertices": verts, "faces": [[3 * i, 3 * i + 1, 3 * i + 2] for i in range(len(m))]}
        if flaw == "drop":
            made["faces"] = made["faces"][:-1]
    elif kind == "doc_to_outline":
        nodes, edges, stack = [], [], []
        for level, text in _headings_with_levels(name, data):
            nodes.append(text)
            while stack and stack[-1][0] >= level:
                stack.pop()
            if stack:
                edges.append([stack[-1][1], text])
            stack.append((level, text))
        if flaw == "flat":
            edges = [[nodes[0], n] for n in nodes[1:]]
        made = {"form": "diagram", "nodes": nodes, "edges": edges}
    else:
        files = oracle.read(name, data)["files"]
        rows = [[p, len(b) + (1 if flaw == "size" else 0), "x"] for p, b in sorted(files.items())]
        made = {"form": "data_file", "columns": ["path", "size", "format"], "rows": rows}
    payload, media = reference.make(made, flaw if flaw in ("skew", "script") else None)
    if kind == "doc_to_outline" and flaw != "skew":
        payload = _tree_layout(made)
    return omni.Answer({"bytes": payload, "media_type": media}, "answered")


def _tree_layout(made):
    """A top-down tree layout: depth by row, siblings left to right in document order."""
    nodes, edges = made["nodes"], made["edges"]
    parent = {b: a for a, b in edges}
    depth = {}
    for n in nodes:
        depth[n] = depth[parent[n]] + 1 if n in parent else 0
    xs, ys = {n: 40 + 60 * i for i, n in enumerate(nodes)}, {n: 40 + 80 * depth[n] for n in nodes}
    body = "".join(f'<line class="edge" x1="{xs[a]}" y1="{ys[a]}" x2="{xs[b]}" y2="{ys[b]}" stroke="currentColor"/>'
                   for a, b in edges)
    body += "".join(f'<g class="node" transform="translate({xs[n]},{ys[n]})"><circle r="10" fill="#eee"/>'
                    f"<text>{n}</text></g>" for n in nodes)
    return f'<svg xmlns="http://www.w3.org/2000/svg">{body}</svg>'.encode()


def test_reference_transforms_are_right_everywhere():
    for split in omni.SPLITS:
        for t in transform.tasks(split):
            a = reference_transform(t)
            result = transform.judge(t, a, reference_transform(t))
            assert result.outcome == "right", (t.id, result.detail)


@pytest.mark.parametrize("flaw,kinds", [("skew", ("csv_to_chart",)), ("order", ("table_to_sound",)),
                                        ("drop", ("mesh_to_stl",)), ("flat", ("doc_to_outline",)),
                                        ("size", ("archive_index",))])
def test_transform_flaws_are_caught(flaw, kinds):
    for t in transform.tasks("development"):
        kind = t.question.split(":")[1]
        if kind in kinds:
            result = transform.judge(t, reference_transform(t, flaw), reference_transform(t, flaw))
            assert result.outcome in ("wrong", "failed"), (t.id, flaw, result.outcome)
