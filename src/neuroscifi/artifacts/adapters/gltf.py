"""glTF 2.0 (JSON) and GLB (binary glTF): triangle geometry resolved through accessors and buffer views.

Only embedded buffers are read: the GLB binary chunk and `data:` URIs (base64). Any other URI (a file or a web
address) is recorded and never opened or fetched; geometry that needs it is skipped and the notes say so. Every
mesh instance reached from the default scene (or every mesh once, with no transform, when there is no scene) is
placed in world space through its node's matrix or translation/rotation/scale, and all instances are merged into
one `Mesh`. Triangle lists, strips and fans are read; points and lines are counted and skipped; sparse accessors
and morph targets are not applied. Names of nodes, meshes and materials are the artifact's own words.
`scene_graph(data)` returns the scene structure as a `Graph` (scenes, nodes, meshes; child and instance edges).
"""
from __future__ import annotations

import base64
import binascii
import json

import numpy as np

from .. import repr_b2 as R
from ..safe import Deadline, injection_notes
from ..safe_b2 import Reader, count, need
from ..types import Decoded, Detected, Edge, GNode, Graph, Limits, Malformed
from . import Adapter

COMPONENT = {5120: "i1", 5121: "u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
MAX_NODES_VISITED = 100_000


def split_glb(data: bytes, limits=None) -> tuple[dict, bytes | None]:
    r = Reader(data, "<", 0, "the GLB file")
    magic, version, length = r.unpack("4sII")
    if magic != b"glTF":
        raise Malformed("the GLB magic is missing")
    if version != 2:
        raise Malformed(f"GLB version {version} is not glTF 2.0")
    if length > len(data):
        raise Malformed("the GLB header declares more bytes than the file holds")
    gltf, binary = None, None
    while r.pos + 8 <= length:
        clen, ctype = r.unpack("II")
        body = r.take(clen)
        if ctype == 0x4E4F534A and gltf is None:
            gltf = _json(body, limits)
        elif ctype == 0x004E4942 and binary is None:
            binary = body
    if gltf is None:
        raise Malformed("the GLB file has no JSON chunk")
    return gltf, binary


def _json(raw: bytes, limits=None) -> dict:
    if limits is not None:
        need(len(raw) * 3, limits, "the glTF JSON parsed into objects")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Malformed("the glTF JSON is not valid UTF-8 JSON") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("asset"), dict):
        raise Malformed("the glTF JSON has no 'asset' object")
    return doc


def _list(doc, key):
    value = doc.get(key, [])
    return value if isinstance(value, list) else []


class _Resolver:
    def __init__(self, doc, binary, limits, notes):
        self.doc, self.binary, self.limits, self.notes = doc, binary, limits, notes
        self.buffers: dict[int, bytes | None] = {}
        self.external: list[str] = []

    def buffer(self, i: int) -> bytes | None:
        if i in self.buffers:
            return self.buffers[i]
        bufs = _list(self.doc, "buffers")
        if not 0 <= i < len(bufs) or not isinstance(bufs[i], dict):
            raise Malformed(f"buffer {i} does not exist")
        b = bufs[i]
        length = b.get("byteLength")
        if not isinstance(length, int) or length < 0:
            raise Malformed(f"buffer {i} has no valid byteLength")
        need(length, self.limits, f"glTF buffer {i}")
        uri = b.get("uri")
        out = None
        if uri is None:
            if i == 0 and self.binary is not None:
                out = self.binary
            else:
                raise Malformed(f"buffer {i} has no URI and no binary chunk")
        elif isinstance(uri, str) and uri.startswith("data:"):
            head, _, payload = uri.partition(",")
            if ";base64" not in head:
                raise Malformed(f"buffer {i} is a data URI that is not base64")
            if len(payload) > (length * 4) // 3 + 8:
                raise Malformed(f"buffer {i}'s data URI is longer than its byteLength implies")
            try:
                out = base64.b64decode(payload, validate=True)
            except (binascii.Error, ValueError):
                raise Malformed(f"buffer {i}'s data URI is not valid base64") from None
        else:
            self.external.append(f"buffer {i}")
        if out is not None and len(out) < length:
            raise Malformed(f"buffer {i} holds fewer bytes than its byteLength")
        self.buffers[i] = out
        return out

    def accessor(self, i: int) -> np.ndarray | None:
        accs = _list(self.doc, "accessors")
        if not isinstance(i, int) or not 0 <= i < len(accs) or not isinstance(accs[i], dict):
            raise Malformed(f"accessor {i} does not exist")
        a = accs[i]
        ctype, kind, n = a.get("componentType"), a.get("type"), a.get("count")
        if ctype not in COMPONENT or kind not in WIDTH or not isinstance(n, int) or n < 0:
            raise Malformed(f"accessor {i} has an invalid componentType, type or count")
        width = WIDTH[kind]
        count(n * width, self.limits, f"accessor {i} components")
        if "sparse" in a:
            self.notes.append(f"accessor {i} is sparse; its sparse substitutions are not applied")
        if "bufferView" not in a:
            return np.zeros((n, width))
        views = _list(self.doc, "bufferViews")
        v = a["bufferView"]
        if not isinstance(v, int) or not 0 <= v < len(views) or not isinstance(views[v], dict):
            raise Malformed(f"accessor {i} refers to a buffer view that does not exist")
        view = views[v]
        buf = self.buffer(view.get("buffer", -1))
        if buf is None:
            return None
        vo, vl = view.get("byteOffset", 0), view.get("byteLength")
        ao = a.get("byteOffset", 0)
        if not all(isinstance(x, int) and x >= 0 for x in (vo, vl, ao)) or vo + vl > len(buf):
            raise Malformed(f"buffer view {v} lies outside its buffer")
        dtype = np.dtype(COMPONENT[ctype])
        elem = dtype.itemsize * width
        stride = view.get("byteStride") or elem
        if not isinstance(stride, int) or stride < elem:
            raise Malformed(f"buffer view {v} has a stride shorter than its elements")
        if n and ao + stride * (n - 1) + elem > vl:
            raise Malformed(f"accessor {i} reads past the end of buffer view {v}")
        need(n * width * 8, self.limits, f"accessor {i}")
        raw = np.frombuffer(buf, np.uint8, vl, vo)[ao:]
        if n == 0:
            return np.zeros((0, width))
        rows = np.lib.stride_tricks.as_strided(raw, shape=(n, elem), strides=(stride, 1))
        with np.errstate(invalid="ignore", over="ignore"):
            return np.ascontiguousarray(rows).view(dtype).reshape(n, width).astype(np.float64)


def _local_matrix(node: dict) -> np.ndarray:
    if "matrix" in node:
        m = node["matrix"]
        if not (isinstance(m, list) and len(m) == 16 and all(isinstance(x, (int, float)) for x in m)):
            raise Malformed("a node's matrix is not 16 numbers")
        return np.array(m, np.float64).reshape(4, 4).T      # glTF matrices are column-major
    t = node.get("translation", [0, 0, 0])
    q = node.get("rotation", [0, 0, 0, 1])
    s = node.get("scale", [1, 1, 1])
    try:
        t, (x, y, z, w), s = np.array(t, float), [float(v) for v in q], np.array(s, float)
    except (TypeError, ValueError):
        raise Malformed("a node's translation, rotation or scale is not numeric") from None
    if t.shape != (3,) or s.shape != (3,):
        raise Malformed("a node's translation or scale is not three numbers")
    rot = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                    [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                    [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    out = np.eye(4)
    out[:3, :3] = rot * s[None, :]
    out[:3, 3] = t
    return out


def _instances(doc: dict, notes: list) -> list[tuple[int, np.ndarray]]:
    """(mesh index, world matrix) for every mesh reached from the default scene's roots."""
    nodes = _list(doc, "nodes")
    scenes = _list(doc, "scenes")
    if not scenes:
        notes.append("no scene: every mesh placed once without a transform")
        return [(i, np.eye(4)) for i in range(len(_list(doc, "meshes")))]
    s = doc.get("scene", 0)
    if not isinstance(s, int) or not 0 <= s < len(scenes):
        raise Malformed("the default scene does not exist")
    stack = [(r, np.eye(4), 0) for r in reversed(scenes[s].get("nodes", []) if isinstance(scenes[s], dict) else [])]
    out, visited = [], 0
    while stack:
        i, parent, depth = stack.pop()
        visited += 1
        if visited > MAX_NODES_VISITED or depth > 64:
            raise Malformed("the node hierarchy is cyclic or too deep")
        if not isinstance(i, int) or not 0 <= i < len(nodes) or not isinstance(nodes[i], dict):
            raise Malformed(f"node {i} does not exist")
        world = parent @ _local_matrix(nodes[i])
        if "mesh" in nodes[i]:
            out.append((nodes[i]["mesh"], world))
        for c in reversed(nodes[i].get("children", [])):
            stack.append((c, world, depth + 1))
    return out


def _triangles(mode: int, idx: np.ndarray) -> np.ndarray:
    if mode == 4:
        return idx[: len(idx) // 3 * 3].reshape(-1, 3)
    if mode == 5:
        k = np.arange(len(idx) - 2)
        tri = np.stack([idx[k], idx[k + 1], idx[k + 2]], axis=1)
        tri[1::2, [0, 1]] = tri[1::2, [1, 0]]
        return tri
    k = np.arange(1, len(idx) - 1)
    return np.stack([np.full(len(k), idx[0]), idx[k], idx[k + 1]], axis=1)


def decode_gltf_doc(doc: dict, binary: bytes | None, detected: Detected, limits: Limits) -> Decoded:
    deadline = Deadline(limits.max_seconds)
    notes: list[str] = []
    version = doc["asset"].get("version")
    if not isinstance(version, str) or not version.startswith("2"):
        raise Malformed("only glTF 2.0 is read here")
    res = _Resolver(doc, binary, limits, notes)
    meshes = _list(doc, "meshes")
    verts, faces, base = [], [], 0
    skipped_modes = 0
    for mesh_index, world in _instances(doc, notes):
        deadline.check("resolving glTF meshes")
        if not isinstance(mesh_index, int) or not 0 <= mesh_index < len(meshes) or not isinstance(
                meshes[mesh_index], dict):
            raise Malformed(f"mesh {mesh_index} does not exist")
        for prim in meshes[mesh_index].get("primitives", []):
            if not isinstance(prim, dict):
                raise Malformed("a glTF primitive is not an object")
            mode = prim.get("mode", 4)
            if mode not in (4, 5, 6):
                skipped_modes += 1
                continue
            pos_i = (prim.get("attributes") or {}).get("POSITION")
            if pos_i is None:
                continue
            pos = res.accessor(pos_i)
            if pos is None:
                continue
            if pos.shape[1] != 3:
                raise Malformed("a POSITION accessor is not VEC3")
            if "indices" in prim:
                idx = res.accessor(prim["indices"])
                if idx is None:
                    continue
                idx = idx[:, 0].astype(np.int64)
            else:
                idx = np.arange(len(pos), dtype=np.int64)
            if len(idx) and (idx.min() < 0 or idx.max() >= len(pos)):
                raise Malformed("a glTF index refers to a vertex that does not exist")
            if prim.get("targets"):
                notes.append("morph targets are not applied")
            world_pos = pos @ world[:3, :3].T + world[:3, 3]
            verts.append(world_pos)
            faces.append(_triangles(mode, idx) + base if len(idx) >= 3 else np.zeros((0, 3), np.int64))
            base += len(pos)
            count(base, limits, "glTF vertices")
    if res.external:
        notes.append(f"{len(res.external)} buffer(s) refer to external files; never opened, their geometry skipped")
    if skipped_modes:
        notes.append(f"{skipped_modes} primitive(s) of points or lines skipped")
    if doc.get("extensionsRequired"):
        notes.append("required extensions are not interpreted: " + ", ".join(map(str, doc["extensionsRequired"]))[:200])
    v = np.concatenate(verts) if verts else np.zeros((0, 3))
    f = np.concatenate(faces).astype(np.int64) if faces else np.zeros((0, 3), np.int64)
    count(len(f), limits, "glTF triangles")
    if len(v) and not np.isfinite(v).all():
        raise Malformed("a glTF position is not a finite number")
    words = [str(x.get("name")) for key in ("nodes", "meshes", "materials", "scenes") for x in _list(doc, key)
             if isinstance(x, dict) and isinstance(x.get("name"), str)]
    text = "\n".join(dict.fromkeys(words))
    graph = scene_graph_of(doc)
    notes.append(f"scene graph: {len(graph.nodes)} node(s), {len(graph.edges)} edge(s) (gltf.scene_graph)")
    notes.append("glTF positions are in metres by the specification; the unit is stated as 'm'")
    notes.extend(injection_notes(text))
    return Decoded(detected, R.Mesh(v, f, "m", words=text), tuple(notes), False)


def scene_graph_of(doc: dict) -> Graph:
    nodes, edges = [], []
    for kind, key in (("scene", "scenes"), ("node", "nodes"), ("mesh", "meshes")):
        for i, x in enumerate(_list(doc, key)[:100_000]):
            name = x.get("name") if isinstance(x, dict) and isinstance(x.get("name"), str) else f"{kind} {i}"
            nodes.append(GNode(f"{kind}:{i}", kind, name, {}))
    ids = {n.id for n in nodes}
    for i, s in enumerate(_list(doc, "scenes")):
        for r in (s.get("nodes", []) if isinstance(s, dict) else []):
            if f"node:{r}" in ids:
                edges.append(Edge(f"scene:{i}", f"node:{r}", "root"))
    for i, n in enumerate(_list(doc, "nodes")):
        if not isinstance(n, dict):
            continue
        for c in n.get("children", []) if isinstance(n.get("children"), list) else []:
            if f"node:{c}" in ids:
                edges.append(Edge(f"node:{i}", f"node:{c}", "child"))
        if f"mesh:{n.get('mesh')}" in ids:
            edges.append(Edge(f"node:{i}", f"mesh:{n.get('mesh')}", "instance"))
    return Graph(tuple(nodes), tuple(edges))


def scene_graph(data: bytes) -> Graph:
    """The scene structure of a glTF or GLB file as a Graph."""
    doc = split_glb(data)[0] if data[:4] == b"glTF" else _json(data)
    return scene_graph_of(doc)


class GLTFAdapter(Adapter):
    name = "b2-gltf"
    formats = ("gltf", "glb")
    families = ("mesh",)

    def decode(self, data, detected, limits):
        if data[:4] == b"glTF":
            doc, binary = split_glb(data, limits)
        else:
            doc, binary = _json(data, limits), None
        return decode_gltf_doc(doc, binary, detected, limits)


ADAPTERS = (GLTFAdapter(),)

__all__ = ["ADAPTERS", "GLTFAdapter", "scene_graph", "scene_graph_of", "split_glb"]
