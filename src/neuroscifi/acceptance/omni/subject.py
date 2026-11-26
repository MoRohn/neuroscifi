"""The thin drivers: the only module of the benchmark that touches product code, and only lazily.

- `TodaySubject` ("today"): what exists on `main` before OMNIMIND. Documents reach the system only as web
  responses, so a file is handed to `webcortex.extract.extract` as the response a server would send with the
  task's declared media type, then through `safety.quarantine` as the orchestrator does (a type the fetcher does
  not accept, `webcortex.net.CONTENT_TYPES`, is "cannot", as its unsupported_type would be). Arrays reach it
  through the two commands that call `np.load(allow_pickle=False)` (`nsf graphfile pack` for .npy, `nsf epoch
  stream` for an .npz holding exactly `states` and `targets`). Every other question is answered "cannot": no
  detection, no file intake, nothing that makes an artifact.
- `ArtifactsSubject` ("artifacts"): written against DESIGN.md §2/§3/§6 (`artifacts.adapters.decode`,
  `artifacts.make.make`, `artifacts.check`), importing them lazily; while the package is absent every answer is
  "cannot" with the reason "not built".

A driver reads a MEASUREMENT (MEASURES) off whatever representation the subject returns. The measurement's
definition lives here, not in the product: e.g. `image.mean_gray` is the driver's own computation over the
returned pixels. Exceptions: a named failure (the product's ArtifactError, today's FetchError or ValidationError)
becomes "refused" (or "cannot" when its reason is that a capability is missing); any other exception propagates
and the harness records a crash.

Where DESIGN.md leaves a representation open (an array has no class of its own; how a JSON value maps to a Node;
whether an XLSX is a Bundle of sheets or one Table), the driver accepts every reading that DESIGN allows and says
"cannot" for anything else. The spec a `make` task's request becomes is built in `_spec` and is the one place a
later lane adjusts to the field names lanes G1/G2 settle on: the request, the truth and the readers stay frozen.
"""
from __future__ import annotations

import fnmatch
import importlib
import io
import json
import re
import zipfile

import numpy as np

from . import Answer, Task

MEASURES = {
    "detect.family": "family named by detection (DESIGN §2 FAMILIES)",
    "detect.format": "format named by detection (short lower-case name)",
    "table.n_rows": "data rows", "table.n_cols": "columns", "table.columns": "column names in order",
    "table.column_mean:<name>": "mean of a numeric column, missing cells ignored",
    "table.cell:<r>,<c>": "data row r, column c (0-based); a number, a string, epoch seconds for times, None",
    "table.n_missing:<name>": "missing cells of a column",
    "table.sheets": "names of the tables of a workbook, in order",
    "doc.title": "the document's title", "doc.headings": "heading texts in order",
    "doc.table_cell:<t>,<r>,<c>": "table t, data row r, column c", "doc.figures": "figure captions in order",
    "doc.figure_members": "names of the files the figures show, in order", "doc.contains:<phrase>": "phrase in text",
    "doc.n_pages": "pages", "doc.safe_text": "the text a model may see (refused when it carries instructions)",
    "text.chars": "characters of the decoded text",
    "image.size": "[width, height]", "image.pixel:<x>,<y>": "channel values at x, y (palette expanded)",
    "image.mean_gray": "mean luminance in [0, 1] (Rec. 709 weights for colour; alpha ignored)",
    "image.n_frames": "frames", "image.frame_delays": "seconds per frame",
    "drawing.count:<kind>": "shapes of a kind", "drawing.texts": "text shapes in order", "drawing.size": "[w, h]",
    "audio.duration_s": "seconds", "audio.rate_hz": "samples per second", "audio.channels": "channels",
    "audio.dominant_hz": "strongest frequency of the mixed signal", "audio.n_onsets": "sounds after silence",
    "video.duration_s": "seconds", "video.size": "[width, height]", "video.frame_mean_gray:<i>": "frame content",
    "mesh.n_faces": "triangles", "mesh.volume": "signed volume", "mesh.watertight": "closed and oriented",
    "mesh.bounds": "[[min x, y, z], [max x, y, z]]", "mesh.area": "surface area",
    "geo.bbox": "[min x, min y, max x, max y]", "geo.n_features": "features", "geo.n_points": "positions",
    "tree.value:<path>": "value at a/b/0/c (XML: name[i], @attr)", "tree.keys:<path>": "child names",
    "code.functions": "function names", "code.calls:<f>": "package functions f calls", "code.imports": "modules",
    "notebook.code_cells": "code cells", "notebook.code:<i>": "source of code cell i",
    "array.shape": "shape", "array.dtype": "numpy dtype name", "array.sum": "sum", "array.value:<i,j>": "element",
    "bundle.members": "member file paths, sorted", "bundle.n_members": "member files",
}


class Cannot(Exception):
    """The representation does not carry what the measurement needs."""


def split_question(question: str) -> tuple[list[str], str, str]:
    parts = question.split("::")
    name, _, arg = parts[-1].partition(":")
    return parts[:-1], name, arg


def _only_file(task: Task) -> tuple[str, bytes, str | None]:
    if len(task.files) != 1:
        raise Cannot("a measurement task has one file")
    name, data = next(iter(task.files.items()))
    return name, data, task.declared.get(name)


def _norm(text: str) -> str:
    return " ".join(str(text).split())


# ---- measurement over DESIGN §2 representations (duck-typed by class name; no import of the product) ---------------

def _kind(rep) -> str:
    return type(rep).__name__


def _members(rep) -> list:
    return list(getattr(rep, "members", ()) or ())


def _descend_single(rep):
    """A Bundle of one decoded member (a .gz, a .zst) stands for that member."""
    if _kind(rep) == "Bundle":
        members = _members(rep)
        if len(members) == 1 and members[0].inner is not None:
            return members[0].inner
    return rep


def _member(rep, name: str):
    if _kind(rep) == "Bundle":
        for m in _members(rep):
            path = str(m.path).lstrip("./")
            if path == name or path.endswith("/" + name) or path.rsplit("/", 1)[-1] == name and "/" not in name:
                if m.inner is None:
                    raise Cannot(f"member {name} not decoded: {m.note}")
                return m.inner, m.detected
        raise Cannot(f"no member {name}")
    if _kind(rep) == "Table" and getattr(rep, "title", None) == name:
        return rep, None
    if _kind(rep) == "Document":
        for block in rep.blocks:
            if block.kind == "table" and getattr(block.ref, "title", None) == name:
                return block.ref, None
    raise Cannot(f"{_kind(rep)} has no member {name}")


def _value_list(column) -> list:
    values = column.values
    if isinstance(values, np.ndarray):
        return [None if (isinstance(v, float) and np.isnan(v)) else float(v) for v in values.tolist()]
    return [None if v is None else str(v) for v in values]


def _tables(rep) -> list:
    rep = _descend_single(rep)
    if _kind(rep) == "Table":
        return [rep]
    if _kind(rep) == "Bundle":
        return [m.inner for m in _members(rep) if m.inner is not None and _kind(m.inner) == "Table"]
    if _kind(rep) == "Document":
        return [b.ref for b in rep.blocks if b.kind == "table" and b.ref is not None]
    if _kind(rep) == "Tree":
        return [_records(rep)]
    raise Cannot(f"no table in a {_kind(rep)}")


class _Records:
    """A Tree whose root holds records (objects of scalars) read as a table: what a JSON array of objects is."""

    def __init__(self, columns, rows):
        self.col_names, self.rows = columns, rows
        self.n_rows = len(rows)


def _records(tree):
    rows = []
    for child in tree.root.children:
        if not child.children:
            raise Cannot("a tree that is not a list of records")
        rows.append({c.name: c.value for c in child.children})
    names: list[str] = []
    for row in rows:
        names += [k for k in row if k not in names]
    return _Records(names, rows)


def _table_measure(t, name: str, arg: str):
    if isinstance(t, _Records):
        cols = t.col_names

        def column(c):
            return [r.get(c) for r in t.rows]
        if name == "table.n_rows":
            return t.n_rows
        if name == "table.n_cols":
            return len(cols)
        if name == "table.columns":
            return cols
        if name == "table.column_mean:" or name == "table.column_mean":
            vals = [float(v) for v in column(arg) if v is not None]
            return sum(vals) / len(vals)
        if name == "table.cell":
            r, c = (int(v) for v in arg.split(","))
            return t.rows[r].get(cols[c])
        if name == "table.n_missing":
            return sum(v is None for v in column(arg))
        raise Cannot(name)
    cols = list(t.columns)
    if name == "table.n_rows":
        return int(t.n_rows)
    if name == "table.n_cols":
        return len(cols)
    if name == "table.columns":
        return [c.name for c in cols]
    if name in ("table.column_mean", "table.n_missing"):
        col = next((c for c in cols if c.name == arg), None)
        if col is None:
            raise Cannot(f"no column {arg}")
        if name == "table.n_missing":
            return int(col.n_missing)
        vals = [v for v in _value_list(col) if v is not None]
        if not vals or not all(isinstance(v, float) for v in vals):
            raise Cannot(f"column {arg} is not numeric")
        return sum(vals) / len(vals)
    if name == "table.cell":
        r, c = (int(v) for v in arg.split(","))
        return _value_list(cols[c])[r]
    raise Cannot(name)


def _blocks(rep, kind: str) -> list:
    rep = _descend_single(rep)
    if _kind(rep) != "Document":
        raise Cannot(f"a {_kind(rep)} is not a document")
    return [b for b in rep.blocks if b.kind == kind]


def _caption(block) -> str:
    if block.text:
        return block.text
    ref = block.ref
    if isinstance(ref, str):
        return ref
    if isinstance(ref, dict):
        return str(ref.get("caption") or ref.get("alt") or "")
    if isinstance(ref, (tuple, list)) and ref:
        return str(ref[0])
    return ""


def _figure_member(block) -> str | None:
    ref = block.ref
    if isinstance(ref, dict):
        return ref.get("member") or ref.get("src")
    if isinstance(ref, (tuple, list)) and len(ref) > 1:
        return str(ref[1])
    return None


def _raster(rep):
    rep = _descend_single(rep)
    if _kind(rep) == "Frames":
        return rep.frames[0], rep
    if _kind(rep) == "Raster":
        return rep, None
    raise Cannot(f"a {_kind(rep)} has no pixels")


def mean_gray(pixels: np.ndarray, mode: str, bits: int) -> float:
    top = float((1 << bits) - 1)
    p = pixels.astype(np.float64)
    if mode == "gray" or p.ndim == 2:
        g = p if p.ndim == 2 else p[:, :, 0]
    else:
        g = 0.2126 * p[:, :, 0] + 0.7152 * p[:, :, 1] + 0.0722 * p[:, :, 2]
    return float(g.mean() / top)


def _signal(rep):
    from .readers import Sound
    rep = _descend_single(rep)
    if _kind(rep) != "Signal":
        raise Cannot(f"a {_kind(rep)} is not a sound")
    samples = np.asarray(rep.samples, dtype=np.float64)
    if samples.ndim == 1:
        samples = samples[:, None]
    return Sound(samples, int(round(rep.rate_hz)), 16), rep


def _mesh(rep):
    from .readers import MeshReading
    rep = _descend_single(rep)
    if _kind(rep) != "Mesh":
        raise Cannot(f"a {_kind(rep)} is not a mesh")
    corners = np.asarray(rep.vertices, dtype=np.float64)[np.asarray(rep.faces, dtype=np.int64)].reshape(-1, 3)
    unique, inverse = np.unique(corners, axis=0, return_inverse=True)
    return MeshReading(unique, inverse.reshape(-1, 3).astype(np.int64))


def _tree_node(rep, path: str):
    rep = _descend_single(rep)
    if _kind(rep) != "Tree":
        raise Cannot(f"a {_kind(rep)} is not a tree")
    node = rep.root
    for seg in [s for s in path.split("/") if s]:
        if seg.startswith("@"):
            return ("attr", node.attrs.get(seg[1:]))
        m = re.fullmatch(r"(.+?)\[(\d+)\]", seg)
        if m:
            named = [c for c in node.children if c.name == m.group(1)]
            index = int(m.group(2))
            if index >= len(named):
                raise Cannot(f"no {seg}")
            node = named[index]
            continue
        named = [c for c in node.children if c.name == seg]
        if named:
            node = named[0]
        elif seg.isdigit() and int(seg) < len(node.children):
            node = node.children[int(seg)]
        else:
            raise Cannot(f"no {seg}")
    return ("node", node)


def _graphs(rep) -> list:
    rep = _descend_single(rep)
    if _kind(rep) == "Graph":
        return [rep]
    if _kind(rep) == "Bundle":
        out = []
        for m in _members(rep):
            if m.inner is not None:
                try:
                    out += _graphs(m.inner)
                except Cannot:
                    pass
        if out:
            return out
    raise Cannot(f"no code graph in a {_kind(rep)}")


def _bare(label: str) -> str:
    return re.split(r"[.:/]", str(label))[-1]


def _array(rep):
    rep = _descend_single(rep)
    if isinstance(rep, np.ndarray):
        return rep
    for attr in ("array", "values", "data"):
        value = getattr(rep, attr, None)
        if isinstance(value, np.ndarray):
            return value
    raise Cannot(f"a {_kind(rep)} is not an array")


def measure(rep, detected, name: str, arg: str):
    """Read one measurement off a DESIGN §2 representation; raises Cannot when it does not carry it."""
    if name == "detect.family":
        return detected.family
    if name == "detect.format":
        return detected.format
    if _kind(rep) == "Opaque":
        raise Cannot("nothing decoded the content: " + "; ".join(getattr(rep, "needs", ()) or ("no adapter",)))
    if name.startswith("table."):
        if name == "table.sheets":
            return [getattr(t, "title", None) for t in _tables(rep)]
        tables = _tables(rep)
        if not tables:
            raise Cannot("no table")
        return _table_measure(tables[0], name, arg)
    if name == "text.chars":
        return len(rep.text())
    if name == "doc.safe_text":
        from ...webcortex.safety import scan_injection        # the existing gate text passes before a model
        text = rep.text()
        if scan_injection(text):
            return _Refuse("instructions aimed at an AI agent in the text")
        return len(text)
    if name.startswith("doc."):
        rep1 = _descend_single(rep)
        if name == "doc.title":
            title = getattr(rep1, "title", None)
            if not title:
                raise Cannot("no title")
            return title
        if name == "doc.headings":
            return [_norm(b.text) for b in _blocks(rep1, "heading")]
        if name == "doc.figures":
            return [_norm(_caption(b)) for b in _blocks(rep1, "figure")]
        if name == "doc.figure_members":
            return [_figure_member(b) for b in _blocks(rep1, "figure")]
        if name == "doc.contains":
            return _norm(arg) in _norm(rep1.text())
        if name == "doc.n_pages":
            return len(_blocks(rep1, "page_break")) + 1
        if name == "doc.table_cell":
            t, r, c = (int(v) for v in arg.split(","))
            tables = [b.ref for b in _blocks(rep1, "table")]
            if t >= len(tables) or tables[t] is None:
                raise Cannot("no such table")
            return _table_measure(tables[t], "table.cell", f"{r},{c}")
    if name.startswith("image."):
        raster, frames = _raster(rep)
        px = np.asarray(raster.pixels)
        if name == "image.size":
            return [int(px.shape[1]), int(px.shape[0])]
        if name == "image.pixel":
            x, y = (int(v) for v in arg.split(","))
            value = px[y, x]
            return [int(v) for v in np.atleast_1d(value)]
        if name == "image.mean_gray":
            return mean_gray(px, raster.mode, int(raster.bits))
        if name == "image.n_frames":
            return len(frames.frames) if frames is not None else 1
        if name == "image.frame_delays":
            return [float(d) for d in frames.delays_s] if frames is not None else [0.0]
    if name.startswith("drawing."):
        rep1 = _descend_single(rep)
        if _kind(rep1) != "Drawing":
            raise Cannot(f"a {_kind(rep1)} is not a drawing")
        if name == "drawing.count":
            return sum(1 for s in rep1.shapes if s.kind == arg)
        if name == "drawing.texts":
            return [_norm(s.text) for s in rep1.shapes if s.text]
        if name == "drawing.size":
            return [float(rep1.width), float(rep1.height)]
    if name.startswith("audio."):
        from .readers import dominant_hz, onsets
        sound, sig = _signal(rep)
        if name == "audio.duration_s":
            return sound.samples.shape[0] / float(sig.rate_hz)
        if name == "audio.rate_hz":
            return float(sig.rate_hz)
        if name == "audio.channels":
            return int(sound.samples.shape[1])
        if name == "audio.dominant_hz":
            return dominant_hz(sound.samples, sound.rate)
        if name == "audio.n_onsets":
            return onsets(sound)
    if name.startswith("mesh."):
        mesh = _mesh(rep)
        if name == "mesh.n_faces":
            return int(mesh.triangles.shape[0])
        if name == "mesh.volume":
            return mesh.volume
        if name == "mesh.area":
            return mesh.area
        if name == "mesh.bounds":
            return list(mesh.bounds)
        if name == "mesh.watertight":
            tight, oriented = mesh.edge_report()
            return bool(tight and oriented)
    if name.startswith("geo."):
        rep1 = _descend_single(rep)
        if _kind(rep1) != "Features":
            raise Cannot(f"a {_kind(rep1)} is not geographic")
        coords = [np.asarray(f.coordinates, dtype=np.float64).reshape(-1, np.asarray(f.coordinates).shape[-1])
                  for f in rep1.features]
        if name == "geo.n_features":
            return len(rep1.features)
        if name == "geo.n_points":
            return int(sum(c.shape[0] for c in coords))
        if name == "geo.bbox":
            allc = np.concatenate(coords)
            return [float(allc[:, 0].min()), float(allc[:, 1].min()), float(allc[:, 0].max()),
                    float(allc[:, 1].max())]
    if name == "tree.value":
        what, node = _tree_node(rep, arg)
        return node if what == "attr" else node.value
    if name == "tree.keys":
        _, node = _tree_node(rep, arg)
        return [c.name for c in node.children]
    if name.startswith("code."):
        graphs = _graphs(rep)
        nodes = [n for g in graphs for n in g.nodes]
        edges = [e for g in graphs for e in g.edges]
        functions = {n.id: _bare(n.label) for n in nodes if n.kind in ("function", "method")}
        if name == "code.functions":
            return sorted(set(functions.values()))
        if name == "code.imports":
            return sorted({_bare(n.label) for n in nodes if n.kind == "module" and any(
                e.kind == "imports" and e.dst == n.id for e in edges)})
        if name == "code.calls":
            names = set(functions.values())
            ids = [i for i, label in functions.items() if label == arg]
            labels = {n.id: _bare(n.label) for n in nodes}
            return sorted({labels.get(e.dst, _bare(e.dst)) for e in edges if e.kind == "calls" and e.src in ids}
                          & names)
    if name.startswith("notebook."):
        cells = _blocks(rep, "code")
        if name == "notebook.code_cells":
            return len(cells)
        if name == "notebook.code":
            return cells[int(arg)].text
    if name.startswith("array."):
        return _array_measure(_array(rep), name, arg)
    if name.startswith("bundle."):
        if _kind(rep) != "Bundle":
            raise Cannot(f"a {_kind(rep)} is not a bundle")
        paths = sorted(str(m.path) for m in _members(rep))
        return paths if name == "bundle.members" else len(paths)
    raise Cannot(f"{name} is not read off a {_kind(rep)}")


def _array_measure(a: np.ndarray, name: str, arg: str):
    if name == "array.shape":
        return [int(s) for s in a.shape]
    if name == "array.dtype":
        return a.dtype.name
    if name == "array.sum":
        return float(np.sum(a, dtype=np.float64))
    if name == "array.value":
        idx = tuple(int(v) for v in arg.split(","))
        return float(a[idx])
    raise Cannot(name)


class _Refuse:
    def __init__(self, reason):
        self.reason = reason


# ---- the subject of the future: the artifacts package -------------------------------------------------------------

class ArtifactsSubject:
    name = "artifacts"

    def __init__(self):
        self._mods = None
        self._missing = None
        self._cache: dict = {}

    def _modules(self):
        if self._mods is None and self._missing is None:
            try:
                self._mods = {n: importlib.import_module(f"neuroscifi.artifacts.{n}")
                              for n in ("adapters", "types")}
            except ImportError as error:
                self._missing = f"not built: {error.name or error}"
        return self._mods

    def available(self) -> tuple[bool, str]:
        return (self._modules() is not None, self._missing or "")

    def _status(self, error) -> Answer:
        reason = getattr(error, "reason", "") or ""
        status = "cannot" if reason in ("unsupported", "missing_capability") else "refused"
        return Answer(None, status, f"{type(error).__name__}: {reason}: {str(error)[:120]}")

    def _decode(self, name, data, declared):
        key = (name, declared, len(data), hash(data))
        if key not in self._cache:
            if len(self._cache) > 64:
                self._cache.clear()
            adapters = self._mods["adapters"]
            self._cache[key] = adapters.decode(data, name=name, declared=declared)
        return self._cache[key]

    def answer(self, task: Task) -> Answer:
        if self._modules() is None:
            return Answer(None, "cannot", self._missing)
        error_type = self._mods["types"].ArtifactError
        try:
            if task.kind == "make":
                return self._make(json.loads(task.files["request.json"]))
            if task.kind == "transform":
                return self._transform(task)
            name, data, declared = _only_file(task)
            decoded = self._decode(name, data, declared)
            members, measure_name, arg = split_question(task.question)
            rep, detected = decoded.representation, decoded.detected
            for member in members:
                try:
                    rep, det = _member(rep, member)
                except Cannot:
                    if _descend_single(rep) is rep:
                        raise
                    rep, det = _member(_descend_single(rep), member)       # through a .gz around an archive
                detected = det or detected
            value = measure(rep, detected, measure_name, arg)
        except error_type as error:
            return self._status(error)
        except Cannot as why:
            return Answer(None, "cannot", str(why))
        if isinstance(value, _Refuse):
            return Answer(None, "refused", value.reason)
        return Answer(_plain(value), "answered")

    # make and transform: the request becomes a DESIGN §6 spec
    def _make(self, request: dict) -> Answer:
        try:
            make = importlib.import_module("neuroscifi.artifacts.make")
        except ImportError as error:
            return Answer(None, "cannot", f"not built: {error.name or error}")
        made = make.make(_spec(request), self._mods["types"].Limits())
        return Answer({"bytes": bytes(made.bytes if hasattr(made, "bytes") else made[0]),
                       "media_type": getattr(made, "media_type", "")}, "answered")

    def _transform(self, task: Task) -> Answer:
        """The steps a transform task needs, chained HERE from DESIGN §3 and §6 calls (decode, then make): the
        production pipeline is a later wave; when it exists, this should call it instead."""
        request = json.loads(task.files["request.json"])
        (name, data), = [(n, d) for n, d in task.files.items() if n != "request.json"]
        rep = _descend_single(self._decode(name, data, task.declared.get(name)).representation)
        kind = request["transform"]
        if kind in ("csv_to_chart", "table_to_sound"):
            table = _tables(rep)[0]

            def column(col):
                if isinstance(table, _Records):
                    return [table.rows[i].get(col) for i in range(table.n_rows)]
                match = [c for c in table.columns if c.name == col]
                if not match:
                    raise Cannot(f"no column {col}")
                return _value_list(match[0])
            if kind == "csv_to_chart":
                xs, ys = column(request["x"]), column(request["y"])
                made = ({"form": "line", "series": [{"name": request["y"], "x": xs, "y": ys}]}
                        if request["form"] == "line" else {"form": "scatter", "points": [list(p) for p in zip(xs, ys)]})
            else:
                values = column(request["column"])
                made = {"form": "sonify", "values": values, "low_hz": request["low_hz"], "high_hz": request["high_hz"],
                        "domain": [min(values), max(values)], "tone_s": request["tone_s"]}
        elif kind == "mesh_to_stl":
            if _kind(rep) != "Mesh":
                raise Cannot(f"a {_kind(rep)} is not a mesh")
            made = {"form": "mesh", "format": request["format"], "vertices": np.asarray(rep.vertices).tolist(),
                    "faces": np.asarray(rep.faces).tolist()}
        elif kind == "doc_to_outline":
            nodes, edges, stack = [], [], []
            for block in _blocks(rep, "heading"):
                text = _norm(block.text)
                nodes.append(text)
                while stack and stack[-1][0] >= block.level:
                    stack.pop()
                if stack:
                    edges.append([stack[-1][1], text])
                stack.append((block.level, text))
            made = {"form": "diagram", "nodes": nodes, "edges": edges}
        elif kind == "archive_index":
            if _kind(rep) != "Bundle":
                raise Cannot(f"a {_kind(rep)} is not an archive")
            made = {"form": "data_file", "format": "csv", "columns": ["path", "size", "format"],
                    "rows": [[str(m.path), int(m.size), m.detected.format] for m in _members(rep)]}
        else:
            raise Cannot(f"unknown transform {kind}")
        return self._make(made)


def _plain(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


# The benchmark's request forms -> the form names of DESIGN §5. Requests carry data as plain lists; the spec's
# "data" holds them under the same names. Adjust HERE (only) when lanes G1/G2 fix their schemas.
FORM_NAMES = {"line": "line_chart", "bar": "bar_chart", "scatter": "scatter_chart", "histogram": "histogram",
              "timeline": "timeline", "diagram": "node_link", "tone": "sound", "sonify": "sonification",
              "mesh": "solid", "surface": "surface", "animation": "animation", "data_file": "data_file"}


def _spec(request: dict) -> dict:
    data = {k: v for k, v in request.items() if k not in ("form", "title", "description", "options")}
    return {"spec": FORM_NAMES.get(request["form"], request["form"]), "version": 1,
            "title": request.get("title", ""), "description": request.get("description", ""),
            "data": data, "options": request.get("options", {}), "sources": ["request"],
            "made_by": {"generator": "", "version": 0}}


# ---- the subject of today: what exists on main ---------------------------------------------------------------------

class TodaySubject:
    name = "today"

    def __init__(self):
        self._cache: dict = {}

    def available(self) -> tuple[bool, str]:
        return True, ""

    def answer(self, task: Task) -> Answer:
        if task.kind in ("make", "transform"):
            return Answer(None, "cannot", "nothing on main makes an artifact from a request")
        try:
            name, data, declared = _only_file(task)
        except Cannot as why:
            return Answer(None, "cannot", str(why))
        members, measure_name, arg = split_question(task.question)
        if measure_name.startswith("array.") and name.endswith((".npy", ".npz")) and len(members) <= 1:
            return self._array(name, data, members, measure_name, arg)
        if members:
            return Answer(None, "cannot", "no archive or member reading on main")
        if measure_name.startswith("doc."):
            from ...webcortex.net import CONTENT_TYPES     # what the fetcher accepts (anything else: unsupported_type)
            if any(fnmatch.fnmatchcase(declared or "", pattern) for pattern in CONTENT_TYPES):
                return self._document(name, data, declared, measure_name, arg)
            return Answer(None, "cannot", f"the fetcher refuses {declared or 'no content type'} (unsupported_type)")
        return Answer(None, "cannot", f"no intake path on main for {measure_name}")

    def _document(self, name, data, declared, measure_name, arg) -> Answer:
        from ...webcortex import extract as wx, safety
        from ...webcortex.types import FetchError, FetchResult
        key = (name, declared, hash(data))
        if key not in self._cache:
            if len(self._cache) > 64:
                self._cache.clear()
            url = f"https://files.invalid/{name}"
            result = FetchResult(url=url, final_url=url, status=200, headers={}, content_type=declared or "",
                                 body=data, retrieved_at=0.0)
            try:
                self._cache[key] = safety.quarantine(wx.extract(result))   # as the orchestrator does after extract
            except FetchError as error:
                self._cache[key] = error
        doc = self._cache[key]
        if isinstance(doc, Exception):
            return Answer(None, "refused", f"{type(doc).__name__}: {str(doc)[:120]}")
        if measure_name == "doc.safe_text":
            if doc.flags.get("injection"):
                return Answer(None, "refused", "injection findings in the extracted document")
            return Answer(len(doc.text), "answered")
        if measure_name == "doc.title":
            return Answer(doc.title, "answered") if doc.title and doc.kind in ("page", "pdf") else \
                Answer(None, "cannot", "no title field for this kind")
        if measure_name == "doc.contains":
            return Answer(_norm(arg) in _norm(doc.text), "answered")
        if measure_name == "doc.n_pages" and doc.kind == "pdf":
            return Answer(int(doc.metadata.get("pages", 0)), "answered")
        if measure_name == "doc.headings" and doc.markdown:
            heads = [_norm(line.lstrip("#")) for line in doc.markdown.splitlines() if re.match(r"#{1,6}\s", line)]
            return Answer(heads, "answered")
        return Answer(None, "cannot", f"{measure_name} is not read off an extracted web document")

    def _array(self, name, data, members, measure_name, arg) -> Answer:
        try:
            loaded = np.load(io.BytesIO(data), allow_pickle=False)
        except (ValueError, EOFError, OSError) as error:
            if name.endswith(".npz"):           # nsf epoch stream names this failure (ValidationError)
                return Answer(None, "refused", f"ValidationError: {type(error).__name__}")
            raise                               # nsf graphfile pack does not catch it: a traceback
        if isinstance(loaded, np.ndarray):
            if members:
                return Answer(None, "cannot", "an .npy has no members")
            return Answer(_plain(_array_measure(loaded, measure_name, arg)), "answered")
        with loaded:
            if set(loaded.files) != {"states", "targets"}:
                return Answer(None, "refused", "ValidationError: must hold exactly the arrays states and targets")
            if not members or members[0] not in ("states", "targets"):
                return Answer(None, "cannot", "an .npz is read as states and targets only")
            try:
                array = loaded[members[0]]
            except (ValueError, zipfile.BadZipFile) as error:
                return Answer(None, "refused", f"ValidationError: {type(error).__name__}")
        return Answer(_plain(_array_measure(array, measure_name, arg)), "answered")


SUBJECTS = {"today": TodaySubject, "artifacts": ArtifactsSubject}


class ProbeSubject:
    """The harness's own self-test (`probe:<behaviour>`), never a result: refuse, answer, hang, crash, allocate past
    the memory bound, or write outside its working directory, so tests can prove each failure is caught."""

    def __init__(self, behaviour: str):
        self.name = f"probe:{behaviour}"
        self.behaviour = behaviour

    def answer(self, task: Task) -> Answer:
        b = self.behaviour
        if b == "refuse":
            return Answer(None, "refused", "probe")
        if b == "answer":
            return Answer(1, "answered")
        if b == "hang":
            import time
            while True:
                time.sleep(0.05)
        if b == "crash":
            raise KeyError("probe")
        if b == "allocate":
            block = np.ones(512 << 20, dtype=np.uint8)
            return Answer(int(block[0]), "answered")
        if b == "escape":
            with open("../../omnimind-probe-escape.txt", "w") as out:
                out.write("x")
            return Answer(None, "refused", "probe")
        raise ValueError(b)


def get(name: str):
    if name.startswith("probe:"):
        return ProbeSubject(name.split(":", 1)[1])
    if name not in SUBJECTS:
        raise ValueError(f"unknown subject {name!r} (one of {', '.join(SUBJECTS)})")
    return SUBJECTS[name]()
