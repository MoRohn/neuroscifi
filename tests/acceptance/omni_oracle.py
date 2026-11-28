"""A test-only oracle for the OMNIMIND benchmark: it re-reads every generated file with the standard library (csv,
json, zipfile, tarfile, gzip, wave, html.parser, xml.etree, ast), numpy, and pypdf when present, and answers the
benchmark's measurements from what it parsed. Agreement between the oracle and a task's stated truth is the
evidence that the generator wrote what it says it wrote. It shares no code with the generators (enc.py, the family
modules); for PNG it uses the benchmark's reader (readers.py), which is itself tested against hand-made bytes.
"""
from __future__ import annotations

import ast
import csv
import gzip
from html.parser import HTMLParser
import io
import json
import re
import struct
import tarfile
import wave
import xml.etree.ElementTree as ET
import zipfile

import numpy as np

from neuroscifi.acceptance.omni import readers


class NotOracle(Exception):
    """The oracle does not read this measurement (the test says which ones it skips)."""


def norm(s: str) -> str:
    return " ".join(str(s).split())


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# ---- tables ---------------------------------------------------------------------------------------------------------

def _number(s: str, decimal_comma: bool):
    t = s.replace(",", ".") if decimal_comma else s
    try:
        return float(t)
    except ValueError:
        return None


def csv_table(data: bytes) -> dict:
    text = data.decode("utf-8-sig")
    first = text.split("\n", 1)[0]
    delim = "\t" if "\t" in first else ";" if ";" in first else ","
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delim))
    header, body = rows[0], [r for r in rows[1:] if r]
    cols = []
    for c in range(len(header)):
        raw = [r[c] for r in body]
        nums = [_number(v, delim == ";") for v in raw if v != ""]
        numeric = all(v is not None for v in nums) and nums
        cols.append([(None if v == "" else _number(v, delim == ";")) if numeric else (v if v != "" else None)
                     for v in raw])
    return {"kind": "table", "names": header, "cols": cols}


def records_table(rows: list[dict]) -> dict:
    names = list(rows[0])
    return {"kind": "table", "names": names, "cols": [[r.get(n) for r in rows] for n in names]}


def xlsx_tables(data: bytes) -> dict:
    z = zipfile.ZipFile(io.BytesIO(data))
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    strings = ["".join(si.itertext()) for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(ns + "si")]
    sheets = [s.get("name") for s in ET.fromstring(z.read("xl/workbook.xml")).iter(ns + "sheet")]
    tables = {}
    for i, sheet in enumerate(sheets):
        root = ET.fromstring(z.read(f"xl/worksheets/sheet{i + 1}.xml"))
        grid = []
        for row in root.iter(ns + "row"):
            values = []
            for c in row.iter(ns + "c"):
                v = c.find(ns + "v").text
                if c.get("t") == "s":
                    values.append(strings[int(v)])
                elif c.get("s") == "1":
                    values.append((float(v) - 25569) * 86400.0)
                else:
                    values.append(float(v))
            grid.append(values)
        tables[sheet] = {"kind": "table", "names": grid[0], "cols": [list(c) for c in zip(*grid[1:])]}
    return {"kind": "workbook", "sheets": sheets, "tables": tables}


def table_measure(t: dict, name: str, arg: str):
    names, cols = t["names"], t["cols"]
    if name == "table.n_rows":
        return len(cols[0])
    if name == "table.n_cols":
        return len(names)
    if name == "table.columns":
        return list(names)
    if name == "table.column_mean":
        vals = [v for v in cols[names.index(arg)] if v is not None]
        return sum(vals) / len(vals)
    if name == "table.n_missing":
        return sum(v is None for v in cols[names.index(arg)])
    if name == "table.cell":
        r, c = (int(v) for v in arg.split(","))
        return cols[c][r]
    raise NotOracle(name)


# ---- documents --------------------------------------------------------------------------------------------------------

class _HTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.title, self.headings, self.cells, self.captions, self.text = [], "", [], [], [], []
        self.row = None

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)
        if tag == "tr":
            self.row = []
        if tag in ("td", "th"):
            self.row.append("")

    def handle_endtag(self, tag):
        if tag == "tr":
            self.cells.append(self.row)
        while self.stack and self.stack.pop() != tag:
            pass

    def handle_data(self, data):
        top = self.stack[-1] if self.stack else ""
        if top == "title":
            self.title += data
        elif re.fullmatch(r"h[1-6]", top):
            self.headings.append(norm(data))
        elif top in ("td", "th"):
            self.row[-1] += data
        elif top == "figcaption":
            self.captions.append(norm(data))
        if top != "title":
            self.text.append(data)


def html_doc(data: bytes) -> dict:
    p = _HTML()
    p.feed(data.decode("utf-8"))
    return {"kind": "doc", "title": norm(p.title), "headings": p.headings, "tables": [p.cells[1:]],
            "figures": p.captions, "text": norm(" ".join(p.text))}


def markdown_doc(data: bytes) -> dict:
    text = data.decode("utf-8")
    lines = text.splitlines()
    heads = [norm(m.group(2)) for m in (re.match(r"(#{1,6})\s+(.*)", ln) for ln in lines) if m]
    rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines if ln.startswith("|")]
    return {"kind": "doc", "headings": heads, "tables": [rows[2:]], "figures": re.findall(r"!\[(.*?)\]", text),
            "text": norm(text)}


def docx_doc(data: bytes) -> dict:
    z = zipfile.ZipFile(io.BytesIO(data))
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    body = ET.fromstring(z.read("word/document.xml")).find(w + "body")
    heads, tables, text, captions = [], [], [], []
    for el in body:
        if el.tag == w + "p":
            t = "".join(x.text or "" for x in el.iter(w + "t"))
            style = el.find(f"{w}pPr/{w}pStyle")
            if style is not None and style.get(w + "val", "").startswith("Heading"):
                heads.append(norm(t))
            if style is not None and style.get(w + "val", "") == "Caption":
                captions.append(norm(t))
            text.append(t)
        elif el.tag == w + "tbl":
            rows = [["".join(x.text or "" for x in tc.iter(w + "t")) for tc in tr.iter(w + "tc")]
                    for tr in el.iter(w + "tr")]
            tables.append(rows[1:])
    title = "".join(ET.fromstring(z.read("docProps/core.xml")).itertext())
    return {"kind": "doc", "title": norm(title), "headings": heads, "tables": tables, "text": norm(" ".join(text)),
            "figures": captions}


def odt_doc(data: bytes) -> dict:
    z = zipfile.ZipFile(io.BytesIO(data))
    root = ET.fromstring(z.read("content.xml"))
    t_ns, tb = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}", "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
    heads = [norm("".join(h.itertext())) for h in root.iter(t_ns + "h")]
    tables = []
    for table in root.iter(tb + "table"):
        rows = [[norm("".join(c.itertext())) for c in r.iter(tb + "table-cell")] for r in table.iter(tb + "table-row")]
        tables.append(rows[1:])
    title = "".join(ET.fromstring(z.read("meta.xml")).itertext())
    return {"kind": "doc", "title": norm(title), "headings": heads, "tables": tables,
            "text": norm(" ".join(root.itertext()))}


def epub_doc(data: bytes) -> dict:
    z = zipfile.ZipFile(io.BytesIO(data))
    assert z.namelist()[0] == "mimetype" and z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
    container = ET.fromstring(z.read("META-INF/container.xml"))
    opf_path = next(el.get("full-path") for el in container.iter() if local(el.tag) == "rootfile")
    opf = ET.fromstring(z.read(opf_path))
    base = opf_path.rsplit("/", 1)[0] + "/"
    title = next("".join(el.itertext()) for el in opf.iter() if local(el.tag) == "title")
    items = {el.get("id"): el.get("href") for el in opf.iter() if local(el.tag) == "item"}
    heads, text = [], []
    for ref in (el.get("idref") for el in opf.iter() if local(el.tag) == "itemref"):
        root = ET.fromstring(z.read(base + items[ref]))
        body = next(el for el in root.iter() if local(el.tag) == "body")
        heads += [norm("".join(el.itertext())) for el in body.iter() if re.fullmatch(r"h[1-6]", local(el.tag))]
        text.append(" ".join(body.itertext()))
    return {"kind": "doc", "title": norm(title), "headings": heads, "text": norm(" ".join(text))}


def pdf_doc(data: bytes) -> dict:
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(data))
    return {"kind": "doc", "title": reader.metadata.title, "n_pages": len(reader.pages),
            "text": norm(" ".join(p.extract_text() for p in reader.pages))}


def doc_measure(d: dict, name: str, arg: str):
    if name == "doc.headings":
        return d["headings"]
    if name == "doc.title":
        return d["title"]
    if name == "doc.contains":
        return norm(arg) in d["text"]
    if name == "doc.figures":
        return d["figures"]
    if name == "doc.n_pages":
        return d["n_pages"]
    if name == "doc.table_cell":
        t, r, c = (int(v) for v in arg.split(","))
        cell = norm(d["tables"][t][r][c])
        try:
            return float(cell)
        except ValueError:
            return cell
    raise NotOracle(name)


# ---- images -----------------------------------------------------------------------------------------------------------

def pnm_image(data: bytes) -> dict:
    tokens, pos = [], 0
    while len(tokens) < 4:
        m = re.compile(rb"\s*(#[^\n]*\n\s*)*(\S+)").match(data, pos)
        tokens.append(m.group(2).decode())
        pos = m.end()
    magic, w, h, top = tokens[0], int(tokens[1]), int(tokens[2]), int(tokens[3])
    c = 3 if magic in ("P3", "P6") else 1
    if magic in ("P2", "P3"):
        values = np.array([int(v) for v in data[pos:].split()])
    else:
        body = data[pos + 1:]
        values = np.frombuffer(body, ">u2" if top > 255 else np.uint8)
    arr = values[:w * h * c].reshape(h, w, c)
    return {"kind": "image", "pixels": arr, "top": top}


def bmp_image(data: bytes) -> dict:
    offset = struct.unpack("<I", data[10:14])[0]
    w, h, _, bits = struct.unpack("<iiHH", data[18:30])
    top_down = h < 0
    h = abs(h)
    bpp = bits // 8
    stride = (w * bpp + 3) // 4 * 4
    rows = [np.frombuffer(data[offset + y * stride: offset + y * stride + w * bpp], np.uint8).reshape(w, bpp)
            for y in range(h)]
    if not top_down:
        rows.reverse()
    arr = np.stack(rows)
    rgb = arr[:, :, [2, 1, 0] + ([3] if bpp == 4 else [])]
    return {"kind": "image", "pixels": rgb, "top": 255, "bmp32": bpp == 4}


def _lzw_decode(data: bytes, min_size: int, count: int) -> list[int]:
    clear, end = 1 << min_size, (1 << min_size) + 1
    size, bitpos, out = min_size + 1, 0, []
    table = [[i] for i in range(clear)] + [None, None]
    prev = None
    bits = int.from_bytes(data, "little")
    while len(out) < count:
        code = (bits >> bitpos) & ((1 << size) - 1)
        bitpos += size
        if code == clear:
            table = [[i] for i in range(clear)] + [None, None]
            size, prev = min_size + 1, None
            continue
        if code == end:
            break
        if code < len(table) and table[code] is not None:
            entry = table[code]
            if prev is not None:
                table.append(prev + [entry[0]])
        else:
            entry = prev + [prev[0]]
            table.append(entry)
        out += entry
        prev = entry
        if len(table) == (1 << size) and size < 12:
            size += 1
    return out


def gif_image(data: bytes) -> dict:
    assert data[:6] == b"GIF89a"
    w, h, packed = struct.unpack("<HHB", data[6:11])
    n_colors = 2 << (packed & 7)
    palette = np.frombuffer(data[13:13 + 3 * n_colors], np.uint8).reshape(-1, 3)
    pos, delays, frames = 13 + 3 * n_colors, [], []
    while data[pos] != 0x3B:
        if data[pos] == 0x21:
            label = data[pos + 1]
            pos += 2
            if label == 0xF9:
                delays.append(struct.unpack("<H", data[pos + 2:pos + 4])[0] / 100)
            while data[pos]:
                pos += data[pos] + 1
            pos += 1
        else:
            fw, fh = struct.unpack("<HH", data[pos + 5:pos + 9])
            min_size = data[pos + 10]
            pos += 11
            payload = bytearray()
            while data[pos]:
                payload += data[pos + 1:pos + 1 + data[pos]]
                pos += data[pos] + 1
            pos += 1
            frames.append(np.array(_lzw_decode(bytes(payload), min_size, fw * fh)).reshape(fh, fw))
    return {"kind": "image", "pixels": palette[frames[0]], "top": 255, "n_frames": len(frames), "delays": delays}


def png_image(data: bytes) -> dict:
    r = readers.read_png(data)
    frame = r.frames[0]
    return {"kind": "image", "pixels": frame, "top": (1 << r.bits) - 1 if r.color_type != 3 else 255}


def image_measure(d: dict, name: str, arg: str):
    px = d["pixels"]
    if name == "image.size":
        return [px.shape[1], px.shape[0]]
    if name == "image.pixel":
        x, y = (int(v) for v in arg.split(","))
        value = [int(v) for v in np.atleast_1d(px[y, x])]
        return value
    if name == "image.mean_gray":
        a = px.astype(np.float64)
        g = a[:, :, 0] if a.shape[2] < 3 else 0.2126 * a[:, :, 0] + 0.7152 * a[:, :, 1] + 0.0722 * a[:, :, 2]
        return float(g.mean() / d["top"])
    if name == "image.n_frames":
        return d["n_frames"]
    if name == "image.frame_delays":
        return d["delays"]
    raise NotOracle(name)


# ---- sound, meshes, geography, trees, code, arrays ---------------------------------------------------------------------

def wav_sound(data: bytes) -> dict:
    with wave.open(io.BytesIO(data)) as w:
        rate, ch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if width == 1:
        x = np.frombuffer(raw, np.uint8).astype(float) - 128
    elif width == 2:
        x = np.frombuffer(raw, "<i2").astype(float)
    else:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int64)
        x = (b[:, 0] | b[:, 1] << 8 | b[:, 2] << 16).astype(float)
        x[x >= 1 << 23] -= 1 << 24
    return {"kind": "sound", "x": x.reshape(n, ch), "rate": rate}


def sound_measure(d: dict, name: str, arg: str):
    x, rate = d["x"], d["rate"]
    mono = x.mean(axis=1)
    if name == "audio.duration_s":
        return len(mono) / rate
    if name == "audio.rate_hz":
        return rate
    if name == "audio.channels":
        return x.shape[1]
    if name == "audio.dominant_hz":
        spec = np.abs(np.fft.rfft(mono))
        return float(np.argmax(spec[1:]) + 1) * rate / len(mono)
    if name == "audio.n_onsets":
        loud = np.abs(mono) > 0.05 * np.abs(mono).max()
        idx = np.nonzero(loud)[0]
        return 1 + int(np.sum(np.diff(idx) > 0.05 * rate))
    raise NotOracle(name)


def mesh_read(name: str, data: bytes) -> dict:
    if name.endswith(".stl") and len(data) >= 84 and len(data) == 84 + 50 * struct.unpack("<I", data[80:84])[0]:
        n = struct.unpack("<I", data[80:84])[0]
        tri = [struct.unpack("<12f", data[84 + 50 * i:84 + 50 * i + 48])[3:] for i in range(n)]
        corners = np.array(tri, dtype=float).reshape(-1, 3)
    elif name.endswith(".stl"):
        corners = np.array([[float(v) for v in ln.split()[1:]] for ln in data.decode().splitlines()
                            if ln.strip().startswith("vertex")])
    elif name.endswith(".obj"):
        verts = [[float(v) for v in ln.split()[1:]] for ln in data.decode().splitlines() if ln.startswith("v ")]
        faces = [[int(v) - 1 for v in ln.split()[1:]] for ln in data.decode().splitlines() if ln.startswith("f ")]
        corners = np.array([verts[i] for f in faces for k in range(1, len(f) - 1) for i in (f[0], f[k], f[k + 1])])
    else:
        head, _, body = data.partition(b"end_header\n")
        nv = int(re.search(rb"element vertex (\d+)", head).group(1))
        nf = int(re.search(rb"element face (\d+)", head).group(1))
        if b"binary_little_endian" in head:
            verts = np.frombuffer(body[:12 * nv], "<f4").reshape(nv, 3).astype(float)
            pos, faces = 12 * nv, []
            for _ in range(nf):
                k = body[pos]
                faces.append(list(struct.unpack(f"<{k}i", body[pos + 1:pos + 1 + 4 * k])))
                pos += 1 + 4 * k
        else:
            lines = body.decode().split("\n")
            verts = np.array([[float(v) for v in ln.split()] for ln in lines[:nv]])
            faces = [[int(v) for v in ln.split()[1:]] for ln in lines[nv:nv + nf]]
        corners = np.array([verts[i] for f in faces for k in range(1, len(f) - 1) for i in (f[0], f[k], f[k + 1])])
    return {"kind": "mesh", "corners": corners.reshape(-1, 3, 3)}


def mesh_measure(d: dict, name: str, arg: str):
    t = d["corners"]
    if name == "mesh.n_faces":
        return len(t)
    if name == "mesh.volume":
        return float(sum(np.dot(a, np.cross(b, c)) for a, b, c in t) / 6)
    if name == "mesh.area":
        return float(sum(np.linalg.norm(np.cross(b - a, c - a)) for a, b, c in t) / 2)
    if name == "mesh.bounds":
        flat = t.reshape(-1, 3)
        return [flat.min(axis=0).tolist(), flat.max(axis=0).tolist()]
    if name == "mesh.watertight":
        directed = {}
        for tri in t:
            keys = [tuple(p) for p in tri]
            for a, b in ((0, 1), (1, 2), (2, 0)):
                directed[(keys[a], keys[b])] = directed.get((keys[a], keys[b]), 0) + 1
        return all(directed.get((b, a), 0) == 1 and k == 1 for (a, b), k in directed.items())
    raise NotOracle(name)


def geo_read(name: str, data: bytes) -> dict:
    if name.endswith(".gpx"):
        root = ET.fromstring(data)
        pts = [(float(el.get("lon")), float(el.get("lat"))) for el in root.iter() if local(el.tag) in ("wpt", "trkpt")]
        return {"kind": "geo", "points": pts, "n_features": None}
    doc = json.loads(data)
    pts = []

    def walk(c):
        if isinstance(c[0], (int, float)):
            pts.append(tuple(c))
        else:
            for x in c:
                walk(x)
    for f in doc["features"]:
        walk(f["geometry"]["coordinates"])
    return {"kind": "geo", "points": pts, "n_features": len(doc["features"])}


def geo_measure(d, name, arg):
    xs, ys = [p[0] for p in d["points"]], [p[1] for p in d["points"]]
    if name == "geo.bbox":
        return [min(xs), min(ys), max(xs), max(ys)]
    if name == "geo.n_points":
        return len(xs)
    if name == "geo.n_features":
        return d["n_features"]
    raise NotOracle(name)


def tree_measure(name_file: str, data: bytes, name: str, arg: str):
    if name_file.endswith(".xml"):
        node = ET.fromstring(data)
        for seg in [s for s in arg.split("/") if s]:
            if seg.startswith("@"):
                return node.get(seg[1:])
            m = re.fullmatch(r"(.+?)\[(\d+)\]", seg)
            node = node.findall(m.group(1))[int(m.group(2))] if m else node.find(seg)
        return node.text
    node = json.loads(data)
    for seg in [s for s in arg.split("/") if s]:
        node = node[int(seg)] if isinstance(node, list) else node[seg]
    return list(node) if name == "tree.keys" else node


def code_read(files: dict[str, str]) -> dict:
    functions, calls, imports = set(), {}, set()
    for src in files.values():
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                functions.add(node.name)
                calls[node.name] = {c.func.id if isinstance(c.func, ast.Name) else c.func.attr
                                    for c in ast.walk(node) if isinstance(c, ast.Call)}
            elif isinstance(node, ast.Import):
                imports |= {a.name.rsplit(".", 1)[-1] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module.rsplit(".", 1)[-1])
    return {"kind": "code", "functions": sorted(functions),
            "calls": {f: sorted(c & functions) for f, c in calls.items()}, "imports": sorted(imports)}


def code_measure(d, name, arg):
    if name == "code.functions":
        return d["functions"]
    if name == "code.imports":
        return d["imports"]
    if name == "code.calls":
        return d["calls"][arg]
    raise NotOracle(name)


def array_measure(a: np.ndarray, name: str, arg: str):
    if name == "array.shape":
        return list(a.shape)
    if name == "array.dtype":
        return a.dtype.name
    if name == "array.sum":
        return float(a.astype(float).sum())
    if name == "array.value":
        return float(a[tuple(int(v) for v in arg.split(","))])
    raise NotOracle(name)


# ---- dispatch ---------------------------------------------------------------------------------------------------------

def read(name: str, data: bytes):
    """A parsed form of one file, chosen by its name (the oracle knows what it was handed)."""
    if name.endswith(".csv.gz"):
        return csv_table(gzip.decompress(data))
    if name.endswith((".csv", ".tsv")):
        return csv_table(data)
    if name.endswith(".ndjson"):
        return records_table([json.loads(ln) for ln in data.decode().splitlines() if ln])
    if name.endswith(".xlsx"):
        return xlsx_tables(data)
    if name.endswith(".md"):
        return markdown_doc(data)
    if name.endswith(".html"):
        return html_doc(data)
    if name.endswith(".docx"):
        return docx_doc(data)
    if name.endswith(".odt"):
        return odt_doc(data)
    if name.endswith(".epub"):
        return epub_doc(data)
    if name.endswith(".pdf"):
        return pdf_doc(data)
    if name.endswith(".png"):
        return png_image(data)
    if name.endswith((".pgm", ".ppm")):
        return pnm_image(data)
    if name.endswith(".bmp"):
        return bmp_image(data)
    if name.endswith(".gif"):
        return gif_image(data)
    if name.endswith(".svg"):
        return {"kind": "svg", "root": ET.fromstring(data)}
    if name.endswith(".wav"):
        return wav_sound(data)
    if name.endswith((".obj", ".stl", ".ply")):
        return mesh_read(name, data)
    if name.endswith((".geojson", ".gpx")):
        return geo_read(name, data)
    if name.endswith(".ipynb"):
        return {"kind": "notebook", "nb": json.loads(data)}
    if name.endswith(".npy"):
        return {"kind": "array", "a": np.load(io.BytesIO(data), allow_pickle=False)}
    if name.endswith(".npz"):
        with np.load(io.BytesIO(data), allow_pickle=False) as z:
            return {"kind": "arrays", "arrays": {k: z[k] for k in z.files}}
    if name.endswith(".py"):
        return code_read({name: data.decode()})
    if name.endswith(".zip"):
        z = zipfile.ZipFile(io.BytesIO(data))
        files = {n: z.read(n) for n in z.namelist()}
        if all(n.endswith(".py") for n in files):
            return code_read({k: v.decode() for k, v in files.items()})
        return {"kind": "bundle", "files": files}
    if name.endswith((".tar", ".tar.gz")):
        raw = gzip.decompress(data) if name.endswith(".gz") else data
        with tarfile.open(fileobj=io.BytesIO(raw)) as t:
            return {"kind": "bundle", "files": {m.name: t.extractfile(m).read() for m in t.getmembers() if m.isfile()}}
    if name.endswith((".json", ".xml")):
        parsed = json.loads(data) if name.endswith(".json") else None
        if isinstance(parsed, list):
            return records_table(parsed)
        return {"kind": "tree", "name": name, "data": data}
    raise NotOracle(name)


def answer(name: str, data: bytes, question: str):
    parts = question.split("::")
    measure, _, arg = parts[-1].partition(":")
    rep = read(name, data)
    for member in parts[:-1]:
        if rep["kind"] == "workbook":
            rep = rep["tables"][member]
        elif rep["kind"] == "arrays":
            rep = {"kind": "array", "a": rep["arrays"][member]}
        else:
            name, data = member, rep["files"][member]
            rep = read(name, data)
    kind = rep["kind"]
    if measure.startswith("detect."):
        raise NotOracle(measure)
    if kind == "workbook" and measure == "table.sheets":
        return rep["sheets"]
    if kind == "table":
        return table_measure(rep, measure, arg)
    if kind == "doc":
        return doc_measure(rep, measure, arg)
    if kind == "image":
        return image_measure(rep, measure, arg)
    if kind == "svg":
        root = rep["root"]
        if measure == "drawing.count":
            return sum(1 for el in root.iter() if local(el.tag) == arg)
        if measure == "drawing.texts":
            return [norm(el.text) for el in root.iter() if local(el.tag) == "text"]
        if measure == "drawing.size":
            return [float(root.get("width")), float(root.get("height"))]
    if kind == "sound":
        return sound_measure(rep, measure, arg)
    if kind == "mesh":
        return mesh_measure(rep, measure, arg)
    if kind == "geo":
        return geo_measure(rep, measure, arg)
    if kind == "tree":
        return tree_measure(rep["name"], rep["data"], measure, arg)
    if kind == "code":
        return code_measure(rep, measure, arg)
    if kind == "notebook":
        cells = rep["nb"]["cells"]
        code = ["".join(c["source"]) for c in cells if c["cell_type"] == "code"]
        if measure == "notebook.code_cells":
            return len(code)
        if measure == "notebook.code":
            return code[int(arg)]
        if measure == "doc.headings":
            return [norm(re.sub(r"^#+", "", "".join(c["source"]).splitlines()[0])) for c in cells
                    if c["cell_type"] == "markdown"]
    if kind == "array":
        return array_measure(rep["a"], measure, arg)
    if kind == "bundle":
        if measure == "bundle.members":
            return sorted(rep["files"])
        if measure == "bundle.n_members":
            return len(rep["files"])
    raise NotOracle(f"{kind}: {measure}")
