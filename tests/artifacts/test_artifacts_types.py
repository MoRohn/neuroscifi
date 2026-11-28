"""The representation types of OMNIMIND (DESIGN §2): invariants, describe() bounded and JSON-able with no invented
values, shape() for each class, text() bounded."""
import json

import numpy as np
import pytest

from neuroscifi.artifacts import types as T

BIG = "x" * 100_000


def _json_ok(d):
    raw = json.dumps(d, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    assert len(raw) <= T.DESCRIBE_MAX_BYTES
    return raw


def samples():
    num = T.Column("height", "number", "m", np.array([1.5, np.nan, 2.25]), 1)
    txt = T.Column("city", "text", None, ("Oslo", "Lima", "Oslo"), 0)
    tim = T.Column("when", "time", None, np.array([0.0, 60.0, 120.0]), 0)
    raster = T.Raster(np.array([[0, 255], [10, 20]], dtype=np.uint8), "gray", 8)
    mesh = T.Mesh(np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float64),
                  np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], dtype=np.int64), "mm")
    return {
        "table": T.Table((num, txt, tim), 3, "Cities"),
        "signal": T.Signal(np.zeros((16000, 2), dtype=np.float32), 16000.0, ("left", "right")),
        "raster": raster,
        "frames": T.Frames((raster, raster), (0.1, 0.2)),
        "drawing": T.Drawing(100.0, 50.0, (T.Shape("text", ((1, 2),), "Label", {}), T.Shape("line", ((0, 0), (5, 5)), None, {}))),
        "mesh": mesh,
        "features": T.Features((T.Feature("Point", np.array([[10.0, 59.9]]), {"name": "Oslo"}),), "EPSG:4326"),
        "tree": T.Tree(T.Node("root", None, {}, (T.Node("a", "alpha"), T.Node("b", 2)))),
        "graph": T.Graph((T.GNode("f", "function", "main"), T.GNode("g", "function", "helper")), (T.Edge("f", "g", "calls"),)),
        "document": T.Document((T.Block("heading", 1, "Intro"), T.Block("paragraph", 0, "Some words."),
                                T.Block("code", 0, "x = 1")), "Doc"),
        "bundle": T.Bundle((T.Member("a.txt", 3, T.Detected("text", "text", "text/plain", "structure", "zip", ()),
                                     None, ""),)),
        "opaque": T.Opaque(10, ("size: 10 bytes",), (), ("an adapter",)),
    }


EXPECTED_KINDS = {"table": {"records", "series", "categories"}, "signal": {"sound", "series"}, "raster": {"image"},
                  "frames": {"image", "sequence"}, "drawing": {"image"}, "mesh": {"solid"}, "features": {"map"},
                  "tree": {"hierarchy"}, "graph": {"network"}, "document": {"prose", "code", "hierarchy"},
                  "bundle": {"hierarchy"}, "opaque": set()}


@pytest.mark.parametrize("name", sorted(EXPECTED_KINDS))
def test_every_representation_has_kind_describe_shape_text(name):
    rep = samples()[name]
    assert isinstance(rep, T.REPRESENTATIONS)
    assert rep.kind == rep.describe()["kind"]
    _json_ok(rep.describe())
    shape = rep.shape()
    assert isinstance(shape, T.ContentShape)
    assert set(shape.kinds) == EXPECTED_KINDS[name]
    assert isinstance(rep.text(), str)


def test_shapes_carry_the_measured_counts():
    s = samples()
    assert s["table"].shape().n_items == 3 and s["table"].shape().n_series == 1 and s["table"].shape().has_time
    sig = s["signal"].shape()
    assert sig.n_items == 16000 and sig.n_series == 2 and sig.duration_s == 1.0 and sig.has_time
    assert s["raster"].shape().has_space == 2 and s["raster"].shape().n_items == 4
    assert s["frames"].shape().duration_s == pytest.approx(0.3)
    assert s["mesh"].shape().has_space == 3
    assert s["tree"].shape().n_items == 3
    assert s["graph"].shape().n_items == 2
    assert s["document"].shape().text_chars == len(s["document"].text())
    low_rate = T.Signal(np.zeros((10, 1), dtype=np.float32), 1.0, ("x",))
    assert low_rate.shape().kinds == ("series",)
    open_mesh = T.Mesh(np.zeros((3, 3)), np.array([[0, 1, 2]], dtype=np.int64))
    assert open_mesh.shape().kinds == ("surface",)


def test_describe_quotes_only_values_that_are_there():
    d = samples()["table"].describe()
    by_name = {c["name"]: c for c in d["columns"]}
    assert by_name["height"]["min"] == 1.5 and by_name["height"]["max"] == 2.25   # NaN is not a value
    assert by_name["height"]["n_missing"] == 1
    assert set(by_name["city"]["sample"]) == {"Oslo", "Lima"}
    m = samples()["mesh"].describe()
    assert m["bounds_min"] == [0.0, 0.0, 0.0] and m["bounds_max"] == [1.0, 1.0, 1.0] and m["closed"] is True
    r = samples()["raster"].describe()
    assert r["channel_min"] == [0.0] and r["channel_max"] == [255.0] and (r["width"], r["height"]) == (2, 2)


def test_describe_stays_bounded_whatever_the_content():
    cols = tuple(T.Column(BIG + str(i), "text", BIG, tuple(BIG + str(j) for j in range(50)), 0) for i in range(200))
    _json_ok(T.Table(cols, 50, BIG).describe())
    nodes = tuple(T.Node(BIG + str(i), BIG) for i in range(500))
    _json_ok(T.Tree(T.Node(BIG, None, {}, nodes)).describe())
    blocks = tuple(T.Block("heading", 1, BIG) for _ in range(500))
    _json_ok(T.Document(blocks, BIG).describe())
    _json_ok(T.Opaque(1, tuple(BIG for _ in range(500)), (BIG,), (BIG,)).describe())
    shapes = tuple(T.Shape(BIG + str(i), (), BIG, {}) for i in range(500))
    _json_ok(T.Drawing(1.0, 1.0, shapes).describe())
    feats = tuple(T.Feature("Point", np.zeros((1, 2)), {BIG + str(i): 1 for i in range(50)}) for _ in range(50))
    _json_ok(T.Features(feats, BIG).describe())
    _json_ok(T.bounded({"kind": "x", "deep": {str(i): [BIG] * 100 for i in range(100)}}))


def test_describe_is_json_even_for_nan_and_infinity():
    col = T.Column("v", "number", None, np.array([np.nan, np.inf, -np.inf]), 1)
    raw = _json_ok(T.Table((col,), 3, None).describe())
    assert b"NaN" not in raw and b"Infinity" not in raw


def test_text_is_bounded():
    doc = T.Document(tuple(T.Block("paragraph", 0, BIG) for _ in range(30)), None)
    assert len(doc.text()) <= T.TEXT_MAX_CHARS


def test_deep_tree_does_not_recurse():
    node = T.Node("leaf")
    for _ in range(5000):
        node = T.Node("n", None, {}, (node,))
    tree = T.Tree(node)
    assert tree.describe()["depth"] == 5000
    assert tree.shape().n_items == 5001


@pytest.mark.parametrize("build", [
    lambda: T.Detected("pictures", "png", "image/png", "signature", None, ()),
    lambda: T.Detected("image", "png", "image/png", "guess", None, ()),
    lambda: T.Column("a", "number", None, np.array([1, 2]), 0),             # int64 is not "number"
    lambda: T.Column("a", "colour", None, (), 0),
    lambda: T.Column("a", "text", None, ("x", 1), 0),
    lambda: T.Column("a", "number", None, np.array([1.0]), 2),
    lambda: T.Table((T.Column("a", "text", None, ("x",), 0),), 2),
    lambda: T.Signal(np.zeros((4,), dtype=np.float32), 10.0, ("a",)),
    lambda: T.Signal(np.full((4, 1), 1.5, dtype=np.float32), 10.0, ("a",)),
    lambda: T.Signal(np.zeros((4, 1), dtype=np.float64), 10.0, ("a",)),
    lambda: T.Signal(np.zeros((4, 2), dtype=np.float32), 10.0, ("a",)),
    lambda: T.Signal(np.zeros((4, 1), dtype=np.float32), 0.0, ("a",)),
    lambda: T.Raster(np.zeros((2, 2, 3), dtype=np.uint8), "gray", 8),
    lambda: T.Raster(np.zeros((2, 2), dtype=np.int32), "gray", 8),
    lambda: T.Raster(np.zeros((2, 2), dtype=np.uint8), "gray", 16),
    lambda: T.Raster(np.zeros((2, 2, 3), dtype=np.uint8), "cmyk", 8),
    lambda: T.Frames((), (0.1,)),
    lambda: T.Mesh(np.zeros((3, 3)), np.array([[0, 1, 3]], dtype=np.int64)),
    lambda: T.Mesh(np.zeros((3, 2)), np.zeros((0, 3), dtype=np.int64)),
    lambda: T.Mesh(np.full((3, 3), np.nan), np.zeros((0, 3), dtype=np.int64)),
    lambda: T.Feature("Point", np.zeros((1, 4)), {}),
    lambda: T.Graph((T.GNode("a", "k", "a"),), (T.Edge("a", "b", "x"),)),
    lambda: T.Graph((T.GNode("a", "k", "a"), T.GNode("a", "k", "b")), ()),
    lambda: T.Block("sidebar", 0, ""),
    lambda: T.Block("table", 0, "", "not a table"),
    lambda: T.ContentShape(("vibes",), 0, 0, False, 0, False, 0.0, 0),
    lambda: T.ContentShape((), 0, 0, False, 1, False, 0.0, 0),
    lambda: T.Decoded(T.Detected("text", "text", "text/plain", "structure", None, ()), "a string", (), False),
])
def test_invariants_are_enforced_as_named_errors(build):
    with pytest.raises(T.Malformed):
        build()


def test_errors_carry_stable_reasons():
    assert issubclass(T.ArtifactError, ValueError)
    assert T.Unsupported("x", needs=("pypdf",)).reason == "unsupported"
    assert T.Unsupported("x", needs=("pypdf",)).needs == ("pypdf",)
    assert T.Malformed("x").reason == "malformed"
    refused = T.Refused("x", rule="max_bytes", reason="too_large")
    assert (refused.reason, refused.rule) == ("too_large", "max_bytes")
    assert T.ArtifactError("x", reason="timeout").reason == "timeout"
    with pytest.raises(ValueError):
        T.ArtifactError("x", reason="oops")


def test_limits_defaults_are_the_design_values():
    lim = T.Limits()
    assert (lim.max_bytes, lim.max_decoded_bytes, lim.max_members, lim.max_depth, lim.max_ratio,
            lim.max_elements, lim.max_seconds) == (64 << 20, 256 << 20, 4096, 4, 200, 5_000_000, 20.0)
    with pytest.raises(AttributeError):
        lim.max_bytes = 1   # frozen


def test_types_import_with_stdlib_and_numpy_only():
    import subprocess
    import sys
    code = ("import sys; import neuroscifi.artifacts.types; "
            "bad = sorted(m for m in sys.modules if m.split('.')[0] not in sys.stdlib_module_names "
            "and m.split('.')[0] not in ('numpy', 'neuroscifi', '__main__')); "
            "own = sorted(m for m in sys.modules if m.startswith('neuroscifi.') and not m.startswith('neuroscifi.artifacts')); "
            "print(bad, own)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "[] []"
