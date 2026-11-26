"""Triangle meshes: Wavefront OBJ, STL (ASCII and binary), PLY (ASCII, binary little- and big-endian), OFF.

Polygons are triangulated as a fan from their first vertex. OBJ: negative (relative) indices resolved, `v/vt/vn`
references read for the vertex only, groups, objects and material names kept as the artifact's own words, material
libraries recorded and never opened, lines and points counted and skipped. STL: binary when the size is exactly
84 + 50 x the triangle count, else ASCII; vertices with exactly equal coordinates are merged so that the surface's
connectivity can be measured. PLY: `vertex` x/y/z and `face` vertex index lists; other elements and properties are
skipped (counted); comments are words. OFF: plain, colour and normal variants (extra numbers per line ignored).
None of these formats carries a unit: `unit` is None.

Bounds: vertex and face counts are checked against `max_elements` before arrays are built, array bytes against
`max_decoded_bytes`; the wall clock is checked as lines are read.
"""
from __future__ import annotations

import io
import re
from array import array

import numpy as np

from .. import repr_b2 as R
from ..safe import Deadline, injection_notes
from ..safe_b2 import Reader, count, need
from ..types import Decoded, Detected, Limits, Malformed, Unsupported
from . import Adapter

NO_UNIT = "the format carries no unit"


def fan(polys: list[list[int]]) -> list[tuple[int, int, int]]:
    out = []
    for p in polys:
        for i in range(1, len(p) - 1):
            out.append((p[0], p[i], p[i + 1]))
    return out


def _mesh(vertices, faces, words, notes, detected, limits) -> Decoded:
    count(len(vertices), limits, "vertices")
    count(len(faces), limits, "triangles")
    need(len(vertices) * 24 + len(faces) * 24, limits, "the mesh arrays")
    v = np.asarray(vertices, dtype=np.float64).reshape(-1, 3)
    f = np.asarray(faces, dtype=np.int64).reshape(-1, 3)
    if v.size and not np.isfinite(v).all():
        raise Malformed("a vertex coordinate is not a finite number")
    if f.size and (f.min() < 0 or f.max() >= len(v)):
        raise Malformed("a face refers to a vertex that does not exist")
    text = "\n".join(dict.fromkeys(w for w in words if w))
    notes = list(notes) + [NO_UNIT] + list(injection_notes(text))
    return Decoded(detected, R.Mesh(v, f, None, words=text), tuple(notes), False)


def merge_exact(tri_vertices: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(3m, 3) corner coordinates -> unique vertices and (m, 3) faces, merging exactly equal coordinates."""
    if not len(tri_vertices):
        return np.zeros((0, 3)), np.zeros((0, 3), np.int64)
    rows = np.ascontiguousarray(tri_vertices)
    keys = rows.view(np.dtype((np.void, rows.dtype.itemsize * 3))).ravel()     # one fixed-size key per corner
    _, first, inverse = np.unique(keys, return_index=True, return_inverse=True)
    return rows[first].astype(np.float64), inverse.reshape(-1, 3).astype(np.int64)


# ---------------------------------------------------------------------------------------------------------------- OBJ


def decode_obj(data: bytes, detected: Detected, limits: Limits) -> Decoded:
    deadline = Deadline(limits.max_seconds)
    verts = array("d")              # x, y, z of each vertex, flat
    tris = array("q")               # fan-triangulated corners, flat
    n_verts = 0
    big_polys = False
    words: list[str] = []
    notes: list[str] = []
    skipped = {"l": 0, "p": 0, "vt": 0, "vn": 0, "curves": 0}
    libs = 0
    for lineno, raw in enumerate(io.BytesIO(data)):          # one line at a time, never a list of all lines
        if lineno % 20000 == 0:
            deadline.check("reading OBJ lines")
            need(len(verts) * 8 + len(tris) * 8 + len(words) * 64, limits, "the OBJ arrays")
        line = (raw.split(b"#", 1)[0] if b"#" in raw else raw).strip()
        if not line:
            continue
        parts = line.split()
        key = parts[0]
        if key == b"v":
            if len(parts) < 4:
                raise Malformed(f"OBJ line {lineno + 1}: a vertex needs three coordinates")
            try:
                verts.extend((float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError:
                raise Malformed(f"OBJ line {lineno + 1}: a vertex coordinate is not a number") from None
            n_verts += 1
            if n_verts > limits.max_elements:
                count(n_verts, limits, "OBJ vertices")
        elif key == b"f":
            if len(parts) < 4:
                raise Malformed(f"OBJ line {lineno + 1}: a face needs at least three vertices")
            try:
                refs = [int(x) for x in parts[1:]] if b"/" not in line else \
                    [int(x.split(b"/", 1)[0]) for x in parts[1:]]
            except ValueError:
                raise Malformed(f"OBJ line {lineno + 1}: a face index is not an integer") from None
            poly = []
            for i in refs:
                if i == 0:
                    raise Malformed(f"OBJ line {lineno + 1}: face index 0 does not exist (indices start at 1)")
                i = i - 1 if i > 0 else n_verts + i
                if not 0 <= i < n_verts:
                    raise Malformed(f"OBJ line {lineno + 1}: a face refers to a vertex not yet defined")
                poly.append(i)
            if len(poly) > 3:
                big_polys = True
            for k in range(1, len(poly) - 1):
                tris.extend((poly[0], poly[k], poly[k + 1]))
            if len(tris) > 3 * limits.max_elements:
                count(len(tris) // 3, limits, "OBJ triangles")
        elif key in (b"g", b"o", b"usemtl"):
            words.append(raw.split(None, 1)[1].decode("utf-8", "replace").strip() if len(parts) > 1 else "")
        elif key == b"mtllib":
            libs += 1
        elif key.decode("latin-1") in skipped:
            skipped[key.decode("latin-1")] += 1
        elif key in (b"curv", b"curv2", b"surf", b"cstype"):
            skipped["curves"] += 1
    groups = sum(1 for _ in words)
    if libs:
        notes.append(f"{libs} material librar{'y' if libs == 1 else 'ies'} named; never opened")
    if skipped["vt"] or skipped["vn"]:
        notes.append(f"texture coordinates ({skipped['vt']}) and normals ({skipped['vn']}) are not kept")
    if skipped["l"] or skipped["p"]:
        notes.append(f"{skipped['l']} line and {skipped['p']} point element(s) skipped (not triangles)")
    if skipped["curves"]:
        notes.append(f"{skipped['curves']} free-form curve/surface statement(s) not evaluated")
    if groups:
        notes.append(f"{groups} group/object/material name(s) kept as words")
    if big_polys:
        notes.append("polygons with more than three vertices were triangulated as fans")
    return _mesh(np.frombuffer(verts, np.float64).reshape(-1, 3), np.frombuffer(tris, np.int64).reshape(-1, 3),
                 words, notes, detected, limits)


# ---------------------------------------------------------------------------------------------------------------- STL

STL_DTYPE = np.dtype([("normal", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])


def decode_stl(data: bytes, detected: Detected, limits: Limits) -> Decoded:
    if len(data) >= 84:
        n = int.from_bytes(data[80:84], "little")
        if 84 + 50 * n == len(data):
            return _stl_binary(data, n, detected, limits)
    head = data[:5].lower()
    if head == b"solid":
        return _stl_ascii(data, detected, limits)
    if len(data) >= 84:
        n = int.from_bytes(data[80:84], "little")
        if 84 + 50 * n > len(data):
            raise Malformed(f"binary STL declares {n} triangles but holds {(len(data) - 84) // 50}")
        return _stl_binary(data, n, detected, limits, trailing=len(data) - 84 - 50 * n)
    raise Malformed("the STL file is too short")


def _stl_binary(data, n, detected, limits, trailing=0):
    count(n, limits, "STL triangles")
    need(n * 50 + n * 9 * 8 * 2, limits, "the STL triangles")
    rec = np.frombuffer(data, STL_DTYPE, count=n, offset=84)
    with np.errstate(invalid="ignore"):
        corners = np.ascontiguousarray(rec["v"].reshape(-1, 3)) + np.float32(0.0)     # -0.0 becomes 0.0
    if not np.isfinite(corners).all():
        raise Malformed("an STL vertex coordinate is not a finite number")
    v, f = merge_exact(corners)
    header = data[:80].split(b"\x00", 1)[0].decode("latin-1", "replace").strip()
    notes = ["binary STL (size rule: 84 + 50 x triangles)", "vertices with exactly equal coordinates were merged"]
    if trailing:
        notes.append(f"{trailing} byte(s) after the last triangle were ignored")
    words = [header] if header and all(c.isprintable() for c in header) else []
    return _mesh(v, f, words, notes, detected, limits)


_FLOAT = rb"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_VERTEX = re.compile(rb"vertex\s+(" + _FLOAT + rb")\s+(" + _FLOAT + rb")\s+(" + _FLOAT + rb")")


def _stl_ascii(data, detected, limits):
    deadline = Deadline(limits.max_seconds)
    n_vertex = data.count(b"vertex")
    count(n_vertex, limits, "STL vertices")
    need(len(data) * STL_OBJECT_FACTOR, limits, "the ASCII STL parsed into numbers")
    found = _VERTEX.findall(data)
    deadline.check("reading ASCII STL")
    if len(found) != n_vertex:
        raise Malformed("an ASCII STL 'vertex' line does not hold three numbers")
    if len(found) % 3:
        raise Malformed("ASCII STL vertices are not a multiple of three")
    corners = (np.array(found, dtype=np.float64).reshape(-1, 3) if found else np.zeros((0, 3))) + 0.0
    names = [m.decode("utf-8", "replace").strip() for m in re.findall(rb"^\s*solid[ \t]*([^\r\n]*)", data, re.M)]
    v, f = merge_exact(corners)
    return _mesh(v, f, names, ["ASCII STL", "vertices with exactly equal coordinates were merged"], detected, limits)


# ---------------------------------------------------------------------------------------------------------------- PLY

PLY_TYPES = {"char": "i1", "int8": "i1", "uchar": "u1", "uint8": "u1", "short": "i2", "int16": "i2",
             "ushort": "u2", "uint16": "u2", "int": "i4", "int32": "i4", "uint": "u4", "uint32": "u4",
             "float": "f4", "float32": "f4", "double": "f8", "float64": "f8"}
STRUCT_CODES = {"i1": "b", "u1": "B", "i2": "h", "u2": "H", "i4": "i", "u4": "I", "f4": "f", "f8": "d"}


def _ply_header(data: bytes, limits: Limits):
    end = data.find(b"end_header", 0, 1 << 20)
    if end < 0:
        raise Malformed("the PLY header has no end_header line")
    nl = data.find(b"\n", end)
    body_at = nl + 1 if nl >= 0 else len(data)
    fmt, elements, words = None, [], []
    for raw in data[:end].decode("latin-1").splitlines()[1:]:
        parts = raw.split()
        if not parts:
            continue
        if parts[0] == "format":
            fmt = parts[1] if len(parts) > 1 else None
        elif parts[0] in ("comment", "obj_info"):
            words.append(raw.split(None, 1)[1] if len(parts) > 1 else "")
        elif parts[0] == "element":
            if len(parts) != 3 or not parts[2].isdigit():
                raise Malformed("a PLY element line is malformed")
            elements.append({"name": parts[1], "n": count(int(parts[2]), limits, f"PLY {parts[1]} elements"),
                             "props": []})
        elif parts[0] == "property":
            if not elements or len(parts) < 3:
                raise Malformed("a PLY property comes before any element or is incomplete")
            if parts[1] == "list":
                if len(parts) != 5 or parts[2] not in PLY_TYPES or parts[3] not in PLY_TYPES:
                    raise Malformed("a PLY list property is malformed")
                elements[-1]["props"].append((parts[4], PLY_TYPES[parts[2]], PLY_TYPES[parts[3]]))
            else:
                if len(parts) != 3 or parts[1] not in PLY_TYPES:
                    raise Malformed("a PLY property is malformed")
                elements[-1]["props"].append((parts[2], PLY_TYPES[parts[1]], None))
    if fmt not in ("ascii", "binary_little_endian", "binary_big_endian"):
        raise Malformed("the PLY header names no known format")
    return fmt, elements, words, body_at


def _ply_binary(data, at, elements, endian, limits, deadline):
    r = Reader(data, endian, at, "the PLY body")
    out = {}
    for el in elements:
        props = el["props"]
        if not props:
            out[el["name"]] = {}
            continue
        if all(p[2] is None for p in props):
            dt = np.dtype([(f"p{i}", endian + p[1]) for i, p in enumerate(props)])
            need(el["n"] * dt.itemsize * 2, limits, f"the PLY {el['name']} elements")
            arr = np.frombuffer(r.take(el["n"] * dt.itemsize), dt)
            out[el["name"]] = {p[0]: arr[f"p{i}"] for i, p in enumerate(props)}
            continue
        # elements with list properties: fast path when every row is the same (one list of a fixed length)
        if len(props) == 1 and el["n"]:
            name, ctype, itype = props[0]
            csize, isize = np.dtype(ctype).itemsize, np.dtype(itype).itemsize
            k = int(np.frombuffer(data, endian + ctype, 1, r.pos)[0]) if r.remaining >= csize else 0
            row = csize + k * isize
            if k >= 3 and r.remaining >= row * el["n"]:
                dt = np.dtype([("c", endian + ctype), ("i", endian + itype, (k,))])
                arr = np.frombuffer(data, dt, el["n"], r.pos)
                if (arr["c"] == k).all():
                    r.skip(row * el["n"])
                    out[el["name"]] = {name: [list(map(int, x)) for x in arr["i"]] if k != 3 else arr["i"]}
                    continue
        rows = {p[0]: [] for p in props}
        for j in range(el["n"]):
            if j % 20000 == 0:
                deadline.check("reading PLY elements")
            for name, ctype, itype in props:
                if itype is None:
                    rows[name].append(r.unpack(STRUCT_CODES[ctype])[0])
                else:
                    k = int(np.frombuffer(r.take(np.dtype(ctype).itemsize), endian + ctype)[0])
                    vals = np.frombuffer(r.take(k * np.dtype(itype).itemsize), endian + itype)
                    rows[name].append([int(x) for x in vals])
        out[el["name"]] = rows
    return out


PLY_OBJECT_FACTOR = 5       # measured 4.0: Python lists of numbers per byte of ASCII PLY
OFF_OBJECT_FACTOR = 1       # lines are read one at a time into preallocated arrays
STL_OBJECT_FACTOR = 6       # measured 5.4: matched vertex tuples per byte of ASCII STL


def _ply_ascii(data, at, elements, limits, deadline):
    need((len(data) - at) * PLY_OBJECT_FACTOR, limits, "the PLY text parsed into numbers")
    lines = iter(io.BytesIO(memoryview(data)[at:]))
    out = {}
    for el in elements:
        rows = {p[0]: [] for p in el["props"]}
        for j in range(el["n"]):
            if j % 20000 == 0:
                deadline.check("reading PLY elements")
            try:
                toks = next(lines).split()
                while not toks:
                    toks = next(lines).split()
            except StopIteration:
                raise Malformed(f"the PLY body ends before {el['n']} {el['name']} elements") from None
            t = 0
            try:
                for name, ctype, itype in el["props"]:
                    if itype is None:
                        rows[name].append(float(toks[t]))
                        t += 1
                    else:
                        k = int(toks[t])
                        rows[name].append([int(x) for x in toks[t + 1:t + 1 + k]])
                        if len(rows[name][-1]) != k:
                            raise Malformed("a PLY list is shorter than its count")
                        t += 1 + k
            except (ValueError, IndexError):
                raise Malformed(f"a PLY {el['name']} line is malformed") from None
        out[el["name"]] = rows
    return out


def decode_ply(data: bytes, detected: Detected, limits: Limits) -> Decoded:
    deadline = Deadline(limits.max_seconds)
    fmt, elements, words, at = _ply_header(data, limits)
    if fmt == "ascii":
        body = _ply_ascii(data, at, elements, limits, deadline)
    else:
        body = _ply_binary(data, at, elements, "<" if fmt == "binary_little_endian" else ">", limits, deadline)
    vertex = body.get("vertex")
    if vertex is None or not all(k in vertex for k in ("x", "y", "z")):
        raise Malformed("the PLY file has no vertex element with x, y and z")
    v = np.column_stack([np.asarray(vertex[k], np.float64) for k in ("x", "y", "z")]) if len(vertex["x"]) \
        else np.zeros((0, 3))
    faces = body.get("face", {})
    key = "vertex_indices" if "vertex_indices" in faces else ("vertex_index" if "vertex_index" in faces else None)
    notes = [f"PLY {fmt.replace('_', ' ')}"]
    if key is None:
        tris = np.zeros((0, 3), np.int64)
        notes.append("no face element: a point cloud (vertices only)")
    else:
        lists = faces[key]
        if isinstance(lists, np.ndarray):
            tris = lists.astype(np.int64)
        else:
            if any(len(p) < 3 for p in lists):
                raise Malformed("a PLY face has fewer than three vertices")
            tris = fan(lists)
    other = [e["name"] for e in elements if e["name"] not in ("vertex", "face")]
    if other:
        notes.append("elements not kept: " + ", ".join(other[:8]))
    extra = [p[0] for e in elements if e["name"] == "vertex" for p in e["props"] if p[0] not in ("x", "y", "z")]
    if extra:
        notes.append("vertex properties not kept: " + ", ".join(extra[:8]))
    return _mesh(v, tris, words, notes, detected, limits)


# ---------------------------------------------------------------------------------------------------------------- OFF


def decode_off(data: bytes, detected: Detected, limits: Limits) -> Decoded:
    deadline = Deadline(limits.max_seconds)
    need(len(data) * OFF_OBJECT_FACTOR, limits, "the OFF text split into lines")
    lines = (ln.split(b"#", 1)[0].split() for ln in io.BytesIO(data))
    lines = (ln for ln in lines if ln)
    head = next(lines, None)
    if not head or not head[0].endswith(b"OFF"):
        raise Malformed("the OFF header is missing")
    if b"BINARY" in head:
        raise Unsupported("binary OFF is not read here", needs=("a binary OFF reader",))
    rest = head[1:] or next(lines, None)
    try:
        nv, nf = int(rest[0]), int(rest[1])
    except (TypeError, ValueError, IndexError):
        raise Malformed("the OFF counts are missing or not integers") from None
    count(nv, limits, "OFF vertices")
    count(nf, limits, "OFF faces")
    need(nv * 24, limits, "the OFF vertices")
    verts = np.empty((nv, 3))
    tris = array("q")
    try:
        for i in range(nv):
            ln = next(lines)
            verts[i] = (float(ln[0]), float(ln[1]), float(ln[2]))
            if i % 65536 == 0:
                deadline.check("reading OFF")
        for j in range(nf):
            ln = next(lines)
            k = int(ln[0])
            poly = [int(x) for x in ln[1:1 + k]]
            if len(poly) != k or k < 3:
                raise Malformed("an OFF face is shorter than its count or has fewer than three vertices")
            for t in range(1, k - 1):
                tris.extend((poly[0], poly[t], poly[t + 1]))
            if j % 65536 == 0:
                deadline.check("reading OFF")
                need(nv * 24 + len(tris) * 8, limits, "the OFF arrays")
    except StopIteration:
        raise Malformed("the OFF file ends before its declared vertices and faces") from None
    except (ValueError, IndexError):
        raise Malformed("an OFF line is malformed") from None
    notes = [f"OFF ({head[0].decode('latin-1')})"]
    if head[0] != b"OFF":
        notes.append("colours, normals or texture coordinates in the file are not kept")
    return _mesh(verts, np.frombuffer(tris, np.int64).reshape(-1, 3), [], notes, detected, limits)


class _MeshAdapter(Adapter):
    families = ("mesh",)
    _fn = None

    def decode(self, data, detected, limits):
        return type(self)._fn(data, detected, limits)


class OBJAdapter(_MeshAdapter):
    name, formats, _fn = "b2-obj", ("obj",), staticmethod(decode_obj)


class STLAdapter(_MeshAdapter):
    name, formats, _fn = "b2-stl", ("stl",), staticmethod(decode_stl)


class PLYAdapter(_MeshAdapter):
    name, formats, _fn = "b2-ply", ("ply",), staticmethod(decode_ply)


class OFFAdapter(_MeshAdapter):
    name, formats, _fn = "b2-off", ("off",), staticmethod(decode_off)


ADAPTERS = (OBJAdapter(), STLAdapter(), PLYAdapter(), OFFAdapter())

__all__ = ["ADAPTERS", "OBJAdapter", "OFFAdapter", "PLYAdapter", "STLAdapter", "decode_obj", "decode_off",
           "decode_ply", "decode_stl", "fan", "merge_exact"]
