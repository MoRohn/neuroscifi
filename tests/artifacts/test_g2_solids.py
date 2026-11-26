"""Lane G2: solids and surfaces (make/solids.py) and the independent mesh check (check/meshes.py).

Wrong meshes are made by editing bytes or by building files here by hand; the checker shares no code with the
generator."""
import math
import struct

import numpy as np
import pytest

from neuroscifi.artifacts import check, make
from neuroscifi.artifacts.check import _g2 as _checks  # noqa: F401
from neuroscifi.artifacts.check import meshes
from neuroscifi.artifacts.make import _g2  # noqa: F401
from neuroscifi.artifacts.make import solids
from neuroscifi.artifacts.spec import Spec
from neuroscifi.artifacts.types import Malformed


def _spec(form, data, options=None):
    return {"spec": form, "version": 1, "title": "A solid", "description": "Made for a test.", "data": data,
            "options": options or {}, "sources": ["request"], "made_by": {"generator": "", "version": 0}}


CASES = {
    "box": ("solid_box", {"size": [2, 3, 4], "center": [1, 1, 1]}),
    "plate_with_holes": ("solid_extrude", {"outline": [[0, 0], [10, 0], [10, 6], [0, 6]], "height": 2,
                                           "holes": [[[2, 2], [4, 2], [4, 4], [2, 4]], [[6, 1], [8, 1], [8, 5], [6, 5]]]}),
    "outline_with_straight_points": ("solid_extrude", {"outline": [[0, 0], [2, 0], [4, 0], [4, 2], [4, 4], [2, 4],
                                                                   [0, 4], [0, 2]], "height": 1,
                                                       "holes": [[[1, 1], [1, 2], [2, 2], [2, 1]]]}),
    "vase": ("solid_revolve", {"profile": [[0, 0], [2, 0], [2, 1], [1, 2], [1, 4], [0, 4]]}),
    "ring": ("solid_revolve", {"profile": [[1, 0], [2, 1], [1, 2]]}),
    "surface": ("surface_grid", {"values": [[0, 1, 2], [1, 2, 3], [0, 0.5, 1], [2, 2, 2]], "x": [0, 3], "y": [0, 4]}),
    "closed_surface": ("surface_grid", {"values": [[0, 1, 2], [1, 2, 3], [0, 0.5, 1], [2, 2, 2]], "x": [0, 3],
                                        "y": [0, 4], "base_z": -1}),
    "points": ("solid_points", {"points": [[0, 0, 0], [1, 2, 3], [4, 1, 0]]}),
}


def _failed(verdict):
    return {f.rule for f in verdict.technical if not f.passed}


@pytest.mark.parametrize("fmt", ["stl", "obj"])
@pytest.mark.parametrize("case", sorted(CASES))
def test_every_form_is_made_and_passes_its_check(case, fmt):
    form, data = CASES[case]
    made = make.make(_spec(form, data, {"format": fmt, "unit": "cm"}))
    assert made.media_type == ("model/stl" if fmt == "stl" else "model/obj")
    verdict = check.check(made.bytes, made.media_type, made.spec)
    assert verdict.ok, [(f.rule, f.detail) for f in verdict.technical if not f.passed]
    assert {f.rule for f in verdict.technical} >= {"watertight", "consistent_orientation", "bounds", "unit",
                                                   "triangle_count"}
    assert made.bytes == make.make(_spec(form, data, {"format": fmt, "unit": "cm"})).bytes


def test_analytic_volumes_are_the_ones_checked():
    e = meshes.expectations(Spec.from_dict(_spec(*CASES["box"])))
    assert e["volume"] == 24.0
    e = meshes.expectations(Spec.from_dict(_spec(*CASES["plate_with_holes"])))
    assert e["volume"] == (60 - 4 - 8) * 2
    cyl = Spec.from_dict(_spec("solid_revolve", {"profile": [[0, 0], [1, 0], [1, 1], [0, 1]]}, {"segments": 256}))
    e = meshes.expectations(cyl)
    assert e["volume"] == pytest.approx(0.5 * 256 * math.sin(2 * math.pi / 256))
    assert e["smooth_volume"] == pytest.approx(math.pi)
    made = make.make(cyl.to_dict())
    finding = next(f for f in check.check(made.bytes, made.media_type, made.spec).technical if f.rule == "volume")
    assert finding.passed and "the smooth solid would hold 3.14159" in finding.detail


def _stl_triangles(data):
    count = struct.unpack_from("<I", data, 80)[0]
    return np.frombuffer(data, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]), count=count,
                         offset=84).copy()


def _stl_from(header, rec):
    return header + struct.pack("<I", len(rec)) + rec.tobytes()


def test_an_inverted_face_is_caught():
    made = make.make(_spec(*CASES["box"]))
    rec = _stl_triangles(made.bytes)
    rec["v"][3] = rec["v"][3][::-1].copy()
    verdict = check.check(_stl_from(made.bytes[:80], rec), "model/stl", made.spec)
    assert {"consistent_orientation", "normals_agree"} <= _failed(verdict)


def test_a_missing_face_is_caught():
    made = make.make(_spec(*CASES["vase"]))
    rec = _stl_triangles(made.bytes)
    verdict = check.check(_stl_from(made.bytes[:80], np.delete(rec, 7)), "model/stl", made.spec)
    assert {"watertight", "triangle_count"} <= _failed(verdict)


def test_a_scaled_copy_is_caught():
    made = make.make(_spec(*CASES["plate_with_holes"]))
    rec = _stl_triangles(made.bytes)
    rec["v"] *= np.float32(1.1)
    verdict = check.check(_stl_from(made.bytes[:80], rec), "model/stl", made.spec)
    assert {"bounds", "volume"} <= _failed(verdict)
    assert {"watertight", "consistent_orientation"}.isdisjoint(_failed(verdict))


def test_a_wrong_unit_and_wrong_surface_values_are_caught():
    made = make.make(_spec(*CASES["surface"], {"format": "obj", "unit": "m"}))
    other = Spec.from_dict(_spec(*CASES["surface"], {"format": "obj", "unit": "mm"}))
    assert "unit" in _failed(check.check(made.bytes, "model/obj", other))
    form, data = CASES["surface"]
    shifted = {**data, "values": [[0, 1, 2], [1, 2, 3.5], [0, 0.5, 1], [2, 2, 2]]}
    shifted_spec = Spec.from_dict(_spec(form, shifted, {"format": "obj", "unit": "m"}))
    verdict = check.check(made.bytes, "model/obj", shifted_spec)
    rec = next(f for f in verdict.technical if f.rule == "values_recovered")
    assert not rec.passed and rec.measure == pytest.approx(11 / 12)


def test_a_moved_marker_is_caught():
    made = make.make(_spec(*CASES["points"]))
    form, data = CASES["points"]
    moved = Spec.from_dict(_spec(form, {"points": [[0, 0, 0], [1, 2, 3.5], [4, 1, 0]]}, {"marker": 0.04}))
    assert "values_recovered" in _failed(check.check(made.bytes, "model/stl", moved))


def test_hand_built_ascii_stl_and_obj_are_read():
    # a unit tetrahedron with outward winding, written by hand
    corners = {"o": (0, 0, 0), "x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}
    faces = [("o", "y", "x"), ("o", "x", "z"), ("o", "z", "y"), ("x", "y", "z")]
    lines = ["solid hand"]
    for f in faces:
        lines += ["facet normal 0 0 0", "outer loop"] + [f"vertex {' '.join(map(str, corners[c]))}" for c in f] + [
            "endloop", "endfacet"]
    lines.append("endsolid hand")
    verdict = check.check("\n".join(lines).encode(), "model/stl")
    assert verdict.ok, _failed(verdict)
    vol = next(f for f in verdict.technical if f.rule == "outward").measure
    assert vol == pytest.approx(1 / 6)
    obj = "# unit: mm\nv 0 0 0\nv 1 0 0\nv 0 1 0\nv 0 0 1\nf 1 3 2\nf 1 2 4\nf 1 4 3\nf 2/7 3/8 4/9\n"
    v, f, comments = meshes.read_obj(obj.encode())
    assert len(v) == 4 and len(f) == 4 and comments == ["unit: mm"]
    assert check.check(obj.encode(), "model/obj").ok
    inside_out = obj.replace("f 1 3 2\nf 1 2 4\nf 1 4 3\nf 2/7 3/8 4/9", "f 1 2 3\nf 1 4 2\nf 1 3 4\nf 2 4 3")
    assert "outward" in _failed(check.check(inside_out.encode(), "model/obj"))


@pytest.mark.parametrize("bad", [b"", b"solid x\n", b"\x00" * 84 + b"\x01", b"\x00" * 80 + b"\xff\xff\xff\x7f",
                                 b"v 0 0 0\nf 1 2 3\n", b"v nan 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", b"f 1 2 3\n"])
def test_hostile_mesh_bytes_are_named_findings(bad):
    for media in ("model/stl", "model/obj"):
        verdict = check.check(bad, media)
        assert not verdict.ok


def test_truncated_stl_is_caught():
    made = make.make(_spec(*CASES["box"]))
    verdict = check.check(made.bytes[:-10], "model/stl", made.spec)
    assert "mesh_readable" in _failed(verdict)


@pytest.mark.parametrize("form,data,needle", [
    ("solid_extrude", {"outline": [[0, 0], [4, 4], [4, 0], [0, 5]], "height": 1}, "crosses"),
    ("solid_extrude", {"outline": [[0, 0], [4, 0], [4, 4], [0, 4]], "height": 1,
                       "holes": [[[5, 5], [6, 5], [6, 6]]]}, "outside"),
    ("solid_extrude", {"outline": [[0, 0], [4, 0], [4, 4], [0, 4]], "height": 1,
                       "holes": [[[1, 1], [3, 1], [3, 3]], [[2, 0.5], [3.5, 2], [2, 2]]]}, "crosses"),
    ("solid_extrude", {"outline": [[0, 0], [1, 0], [2, 0]], "height": 1}, "no area"),
    ("solid_revolve", {"profile": [[0, 0], [-1, 1], [0, 2]]}, "negative radius"),
    ("solid_revolve", {"profile": [[1, 0], [0, 1], [1, 2]]}, "ends of a profile"),
    ("solid_revolve", {"profile": [[1, 0], [2, 2], [2, 0], [1, 2]]}, "crosses"),
    ("surface_grid", {"values": [[0, 1], [1, 2]], "x": [0, 1], "y": [0, 1], "base_z": 0.5}, "below"),
    ("surface_grid", {"values": [[0, 1], [1, 2, 3]], "x": [0, 1], "y": [0, 1]}, "same length"),
    ("surface_grid", {"values": [[0, 1], [1, 2]], "x": [1, 0], "y": [0, 1]}, "rise"),
])
def test_impossible_geometry_is_refused_not_fixed(form, data, needle):
    with pytest.raises(Malformed, match=needle):
        make.make(_spec(form, data))


def test_triangulation_covers_random_outlines_exactly():
    rng = np.random.default_rng(7)
    for trial in range(40):
        n = int(rng.integers(6, 80))
        ang = (np.arange(n) + rng.uniform(0, 0.9, n)) * (2 * np.pi / n)
        r = rng.uniform(4, 10, n)
        outline = np.column_stack([r * np.cos(ang), r * np.sin(ang)]).round(3).tolist()
        holes = [[[-1, -1], [-1, 1], [1, 1], [1, -1]]] if trial % 2 else []
        made = make.make(_spec("solid_extrude", {"outline": outline, "holes": holes, "height": 1.5}))
        assert check.check(made.bytes, made.media_type, made.spec).ok, trial


def test_large_outline_stays_within_a_second_or_two():
    import time
    ang = np.linspace(0, 2 * np.pi, 996, endpoint=False)
    r = 6 + 3 * np.sin(7 * ang)
    outline = np.column_stack([r * np.cos(ang), r * np.sin(ang)]).tolist()
    start = time.perf_counter()
    made = make.make(_spec("solid_extrude", {"outline": outline, "holes": [[[-1, -1], [-1, 1], [1, 1], [1, -1]]],
                                             "height": 1}))
    assert time.perf_counter() - start < 5.0
    assert check.check(made.bytes, made.media_type, made.spec).ok


def test_a_point_on_a_straight_edge_never_ends_in_a_flat_triangle():
    # the square with a point in the middle of its bottom edge: clipping the far corners first would leave the three
    # points of that edge as the last, flat, triangle
    pts = np.array([[0, 0], [0.5, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float64)
    tris = solids.triangulate(pts, [0, 1, 2, 3, 4], [])
    assert len(tris) == 3
    for a, b, c in tris:
        (ax, ay), (bx, by), (cx, cy) = pts[a], pts[b], pts[c]
        assert abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax)) > 1e-9
