"""Meshes (lane B2): OBJ, STL, PLY, OFF, glTF/GLB recover a reference cube and tetrahedron written by hand in each
format; hostile variants are named errors; measure_mesh on shapes with known area, volume and topology."""
import base64
import json
import struct

import numpy as np
import pytest

from b2_fixtures import b2_registered  # noqa: F401

from neuroscifi.artifacts import adapters, measure_mesh, outcome
from neuroscifi.artifacts import types as T
from neuroscifi.artifacts.adapters import _b2, gltf, meshes


# unit cube, outward-facing quads (counter-clockwise seen from outside)
CUBE_V = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
CUBE_Q = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (0, 4, 7, 3)]
TET_V = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]
TET_F = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]


def _tris_of_quads(quads):
    return [(q[0], q[i], q[i + 1]) for q in quads for i in (1, 2)]


def _check_cube(mesh):
    assert isinstance(mesh, T.Mesh) and len(mesh.faces) == 12
    assert measure_mesh.edge_use(mesh)["watertight"]
    assert abs(measure_mesh.surface_area(mesh) - 6.0) < 1e-9
    assert abs(measure_mesh.signed_volume(mesh) - 1.0) < 1e-9
    assert measure_mesh.consistently_oriented(mesh)


def test_obj_quads_negative_indices_groups_and_libraries():
    lines = ["# a cube", "mtllib cube.mtl", "o Cube", "g side"]
    lines += [f"v {x} {y} {z}" for x, y, z in CUBE_V]
    lines += ["vn 0 0 1", "vt 0 0", "usemtl grey"]
    lines += ["f " + " ".join(f"{i - 8}/1/1" for i in q) for q in CUBE_Q]       # negative (relative) indices
    lines += ["l 1 2"]
    out = adapters.decode("\n".join(lines).encode(), name="x.obj")
    assert out.detected.format == "obj" and out.detected.basis == "structure"
    _check_cube(out.representation)
    words = out.representation.text()
    assert "Cube" in words and "side" in words and "grey" in words
    assert any(outcome.OUTSIDE in n for n in out.notes) and any("fans" in n for n in out.notes)
    assert out.representation.unit is None


def test_obj_hostile_indices():
    with pytest.raises(T.Malformed):
        adapters.decode(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 4\n")
    with pytest.raises(T.Malformed):
        adapters.decode(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 0 1 2\n")
    det = T.Detected("mesh", "obj", "model/obj", "structure", None, ())
    with pytest.raises(T.Malformed):                      # 'nan' is not OBJ grammar: detection says text
        meshes.decode_obj(b"v 0 0 0\nv 1 0 nan\nv 0 1 0\nf 1 2 3\n", det, T.Limits())
    with pytest.raises(T.Refused):
        adapters.decode(b"v 0 0 0\n" * 50 + b"f 1 2 3\n", limits=T.Limits(max_elements=10))


def _stl_binary(tris, header=b"exporter 1"):
    out = header.ljust(80, b"\x00") + struct.pack("<I", len(tris))
    for tri in tris:
        out += struct.pack("<3f", 0, 0, 0) + b"".join(struct.pack("<3f", *v) for v in tri) + b"\x00\x00"
    return out


def test_stl_binary_and_ascii_with_the_size_rule():
    tris = [[CUBE_V[i] for i in t] for t in _tris_of_quads(CUBE_Q)]
    data = _stl_binary(tris, header=b"solid but binary")          # starts with 'solid' yet binary by size rule
    out = adapters.decode(data)
    assert out.detected.format == "stl"
    _check_cube(out.representation)
    assert len(out.representation.vertices) == 8                   # exactly equal corners merged
    text = "solid cube\n" + "".join(
        "facet normal 0 0 0\n outer loop\n" + "".join(f"  vertex {x:e} {y} {z}\n" for x, y, z in t)
        + " endloop\nendfacet\n" for t in tris) + "endsolid cube\n"
    out = adapters.decode(text.encode())
    assert out.detected.format == "stl"
    _check_cube(out.representation)
    assert "cube" in out.representation.text()
    with pytest.raises(T.Malformed):
        adapters.decode(_stl_binary(tris)[:-60], name="x.stl", declared="model/stl")


def test_stl_hostile_count_is_refused_or_malformed():
    data = b"\x00" * 80 + struct.pack("<I", 0xFFFFFFFF) + b"\x00" * 50
    with pytest.raises(T.ArtifactError):
        adapters.decode(data, name="x.stl")


def _ply_header(fmt, nv, nf, extra=""):
    return (f"ply\nformat {fmt} 1.0\ncomment made by hand\nelement vertex {nv}\nproperty float x\n"
            f"property float y\nproperty float z\n{extra}element face {nf}\n"
            "property list uchar int vertex_indices\nend_header\n").encode()


@pytest.mark.parametrize("fmt", ["ascii", "binary_little_endian", "binary_big_endian"])
def test_ply_all_three_encodings(fmt):
    if fmt == "ascii":
        body = "".join(f"{x} {y} {z}\n" for x, y, z in CUBE_V) + "".join(f"4 {' '.join(map(str, q))}\n" for q in CUBE_Q)
        data = _ply_header(fmt, 8, 6) + body.encode()
    else:
        e = "<" if fmt.endswith("little_endian") else ">"
        body = b"".join(struct.pack(e + "3f", *v) for v in CUBE_V)
        body += b"".join(struct.pack(e + "B4i", 4, *q) for q in CUBE_Q)
        data = _ply_header(fmt, 8, 6) + body
    out = adapters.decode(data)
    assert out.detected.format == "ply"
    _check_cube(out.representation)
    assert "made by hand" in out.representation.text()


def test_ply_binary_triangles_fast_path_and_mixed_lists():
    e = "<"
    body = b"".join(struct.pack(e + "3f", *v) for v in TET_V) + b"".join(struct.pack(e + "B3i", 3, *f) for f in TET_F)
    out = adapters.decode(_ply_header("binary_little_endian", 4, 4) + body)
    assert out.representation.faces.tolist() == [list(f) for f in TET_F]
    mixed = b"".join(struct.pack(e + "3f", *v) for v in CUBE_V)
    mixed += struct.pack(e + "B3i", 3, 0, 3, 2) + struct.pack(e + "B4i", 4, 0, 2, 1, 0)
    out = adapters.decode(_ply_header("binary_little_endian", 8, 2) + mixed)
    assert len(out.representation.faces) == 3


def test_ply_hostile():
    with pytest.raises(T.Malformed):
        adapters.decode(b"ply\nformat ascii 1.0\nelement vertex 2\nproperty float x\nend_header\n1\n2\n")
    with pytest.raises(T.Malformed):
        adapters.decode(_ply_header("binary_little_endian", 8, 6) + b"\x00" * 10)
    with pytest.raises(T.Refused):
        adapters.decode(_ply_header("ascii", 10 ** 9, 0))


def test_off_plain_and_hostile():
    body = "OFF\n# comment\n8 6 12\n" + "".join(f"{x} {y} {z}\n" for x, y, z in CUBE_V)
    body += "".join(f"4 {' '.join(map(str, q))} 255 0 0\n" for q in CUBE_Q)
    out = adapters.decode(body.encode())
    assert out.detected.format == "off"
    _check_cube(out.representation)
    with pytest.raises(T.Malformed):
        adapters.decode(b"OFF\n8 6 0\n0 0 0\n")


def _gltf(binary=False, external=False):
    pos = b"".join(struct.pack("<3f", *v) for v in TET_V)
    idx = b"".join(struct.pack("<3H", *f) for f in TET_F)
    blob = pos + idx
    doc = {"asset": {"version": "2.0"}, "scene": 0,
           "scenes": [{"nodes": [0]}],
           "nodes": [{"name": "root", "children": [1], "translation": [10, 0, 0]},
                     {"name": "tet", "mesh": 0, "scale": [2, 2, 2]}],
           "meshes": [{"name": "tetra", "primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3"},
                         {"bufferView": 1, "componentType": 5123, "count": 12, "type": "SCALAR"}],
           "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(pos)},
                           {"buffer": 0, "byteOffset": len(pos), "byteLength": len(idx)}],
           "buffers": [{"byteLength": len(blob)}]}
    if external:
        doc["buffers"][0]["uri"] = "https://example.invalid/tet.bin"
    elif not binary:
        doc["buffers"][0]["uri"] = "data:application/octet-stream;base64," + base64.b64encode(blob).decode()
    js = json.dumps(doc).encode()
    if not binary:
        return js
    js += b" " * (-len(js) % 4)
    blob += b"\x00" * (-len(blob) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(blob), 0x004E4942) + blob
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


@pytest.mark.parametrize("binary", [False, True])
def test_gltf_and_glb_with_node_transforms(binary):
    out = adapters.decode(_gltf(binary))
    assert out.detected.format == ("glb" if binary else "gltf")
    m = out.representation
    expect = np.array(TET_V, float) * 2 + [10, 0, 0]
    assert np.allclose(m.vertices, expect) and m.faces.tolist() == [list(f) for f in TET_F]
    assert m.unit == "m" and "tetra" in m.text() and "root" in m.text()
    assert abs(measure_mesh.signed_volume(m) - 8 / 6) < 1e-9
    g = gltf.scene_graph(_gltf(binary))
    assert {(e.src, e.dst, e.kind) for e in g.edges} == {("scene:0", "node:0", "root"), ("node:0", "node:1", "child"),
                                                          ("node:1", "mesh:0", "instance")}


def test_gltf_external_uri_is_never_fetched_and_hostile_accessors():
    # ADR-080 rule 2 (lane FX2): geometry in an outside buffer is never fetched and cannot be known; it used to be
    # answered as 0 triangles. The file is read as its header only: a question about its geometry is "cannot".
    with pytest.raises(T.HeaderOnly) as err:
        adapters.decode(_gltf(external=True))
    assert outcome.of_error(err.value) == outcome.CANNOT and outcome.OUTSIDE in str(err.value)
    assert err.value.facts["geometry in outside buffers"] == "buffer 0" and err.value.facts["meshes"] == 1
    assert "example" not in str(err.value) + json.dumps(err.value.facts) and "tetra" in err.value.words
    doc = json.loads(_gltf())                                       # an outside image beside embedded geometry
    doc["images"] = [{"uri": "https://example.invalid/skin.png"}]
    out = adapters.decode(json.dumps(doc).encode())
    assert len(out.representation.faces) == 4 and outcome.outside("1 image(s)") in out.notes
    doc = json.loads(_gltf())
    doc["accessors"][0]["count"] = 1000
    with pytest.raises(T.Malformed):
        adapters.decode(json.dumps(doc).encode())
    doc = json.loads(_gltf())
    doc["nodes"][1]["children"] = [0]                               # a cycle
    with pytest.raises(T.Malformed):
        adapters.decode(json.dumps(doc).encode())


# ------------------------------------------------------------------------------------------------------- measurements


def _mesh(v, f):
    return T.Mesh(np.array(v, float), np.array(f, np.int64))


def test_measurements_on_known_shapes():
    tet = _mesh(TET_V, TET_F)
    a = measure_mesh.analyse(tet)
    assert a["vertices"] == 4 and a["triangles"] == 4 and a["components"] == 1
    assert abs(a["signed_volume"] - 1 / 6) < 1e-12
    assert abs(a["surface_area"] - (1.5 + np.sqrt(3) / 2)) < 1e-12
    assert a["euler_characteristic"] == 2 and a["edge_use"]["watertight"] and a["consistently_oriented"]
    flipped = _mesh(TET_V, [TET_F[0][::-1]] + TET_F[1:])
    assert not measure_mesh.consistently_oriented(flipped)
    open_ = _mesh(TET_V, TET_F[:3])
    e = measure_mesh.edge_use(open_)
    assert not e["watertight"] and e["boundary"] == 3 and measure_mesh.analyse(open_)["signed_volume"] is None
    two = _mesh(TET_V + [(5 + x, y, z) for x, y, z in TET_V], TET_F + [tuple(i + 4 for i in f) for f in TET_F])
    assert measure_mesh.components(two) == 2 and measure_mesh.euler_characteristic(two) == 4
    fin = _mesh(TET_V + [(0, -1, 0)], TET_F + [(0, 1, 4)])         # third triangle on edge 0-1
    assert measure_mesh.edge_use(fin)["non_manifold"] == 1 and not measure_mesh.edge_use(fin)["manifold"]
    degen = _mesh(TET_V + [(2, 0, 0)], [(0, 1, 4), (0, 0, 2)])     # collinear and repeated
    assert measure_mesh.degenerate(degen) == 2


def test_shape_rule_solid_or_surface():
    s = _b2.content_shape(_mesh(TET_V, TET_F))
    assert s.kinds == ("solid",) and s.has_space == 3
    assert _b2.content_shape(_mesh(TET_V, TET_F[:3])).kinds == ("surface",)
