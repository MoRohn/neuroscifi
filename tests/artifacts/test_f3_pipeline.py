"""Fix lane F3, item 3 (ruling D, the open parts): a source read in part is used only when the owner agrees, and what
is made says which part; large sources are made from a stated, bounded reduction; a planned form that cannot be built
from the content falls through; the SIMULATION record follows the owner's imagination settings (test_produce.py)."""
from __future__ import annotations

import io
import struct
import zipfile

import numpy as np

import test_produce as base
from neuroscifi.artifacts import produce as P
from neuroscifi.artifacts import produce_builders as B
from neuroscifi.artifacts.types import Limits, Mesh, Raster


def _rows(n: int) -> bytes:
    return ("t,value\n" + "".join(f"{i},{(i * 37) % 101}\n" for i in range(n))).encode()


def test_a_partly_read_source_is_used_only_with_the_owners_agreement_and_says_which_part():
    data, small = _rows(20_000), Limits(max_decoded_bytes=1 << 20)
    refused = P.produce(None, "show the value over t", [P.Given("rows.csv", data)], limits=small)
    assert refused.status != "made"
    why = " ".join(str(n.get("why", "")) for n in refused.record["sources"])
    assert "only part of it could be read" in why and "--partial" in why and "stopped after" in why
    made = P.produce(None, "show the value over t", [P.Given("rows.csv", data)], limits=small, partial_ok=True)
    assert made.status == "made", made.record.get("candidates")
    assert "Only part of rows.csv was read: what was read holds" in made.spec.description
    assert "stopped after" in made.spec.description


def test_a_long_series_is_drawn_from_its_extremes_by_a_stated_rule(monkeypatch):
    monkeypatch.setattr(B, "LINE_WINDOWS", 50)
    values = [(i * 7919) % 1000 for i in range(5000)]
    values[1234], values[4321] = 5000, -5000
    data = ("t,value\n" + "".join(f"{i},{v}\n" for i, v in enumerate(values))).encode()
    made = P.produce(None, "show the value over t", [P.Given("long.csv", data)], form="line_chart")
    assert made.status == "made"
    points = made.spec.data["series"][0]["points"]
    assert len(points) <= 100 and [1234, 5000] in points and [4321, -5000] in points
    assert "smallest and the largest value in each of 50 equal windows" in made.spec.description


def test_a_large_image_is_measured_from_a_stated_downsample(monkeypatch):
    monkeypatch.setattr(P, "MAX_RASTER_PIXELS", 10_000)
    rep, rule = P.reduce_for_making(Raster(np.zeros((300, 400), np.uint8), "gray", 8))
    assert rep.pixels.shape == (75, 100) and "one pixel in every 4 across and down" in rule
    made = P.produce(None, "show me the picture", [P.Given("big.png", base.png_gray(np.tile(
        np.arange(400, dtype=np.uint8), (300, 1))))])
    assert made.status == "made", made.record.get("candidates")
    assert "big.png was measured and drawn from a downsample of its 400 x 300 pixels" in made.spec.description


def _stl(triangles: np.ndarray) -> bytes:
    rec = np.zeros(len(triangles), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["v"] = triangles
    return b"binary".ljust(80, b" ") + struct.pack("<I", len(triangles)) + rec.tobytes()


def test_a_large_mesh_is_shown_decimated_by_a_stated_rule(monkeypatch):
    monkeypatch.setattr(B, "MAX_SOLID_VIEW_FACES", 300)
    monkeypatch.setattr(B, "MAX_SOLID_VIEW_VERTICES", 600)
    rep, rule = P.reduce_for_making(Mesh(np.arange(9000, dtype=np.float64).reshape(-1, 3),
                                         np.arange(3000, dtype=np.int64).reshape(-1, 3)))
    assert len(rep.faces) <= 300 and len(rep.vertices) <= 600 and rep.faces.max() < len(rep.vertices)
    assert "one triangle in every 5 of its 1000 is kept" in rule
    tris = np.random.default_rng(4).normal(0, 5, (2000, 3, 3)).astype(np.float32)
    made = P.produce(None, "show me the shape", [P.Given("many.stl", _stl(tris))])
    assert made.status == "made", made.record.get("candidates")
    assert "many.stl was decimated to be shown" in made.spec.description


def test_many_files_in_one_folder_are_a_tree_with_a_stated_cut():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i in range(200):
            z.writestr(f"day{i:03d}.txt", f"note {i}\n")
    made = P.produce(None, "what is in these files", [P.Given("notes.zip", buf.getvalue())], form="tree_diagram")
    assert made.status == "made", made.record.get("candidates")
    labels = [n["label"] for n in made.spec.data["nodes"]]
    assert f"... and {200 - B.MAX_SIBLINGS} more" in labels
    assert f"At most {B.MAX_SIBLINGS} folders and members are shown under one node" in made.spec.description


def test_a_planned_form_that_cannot_be_built_from_the_content_falls_through(monkeypatch):
    first = P.produce(None, "show me the temperature over time", [P.Given("temps.csv", base.temps_csv())])
    planned = first.record["plan"]["chosen"][0]

    def cannot(*_a, **_k):
        raise B.CannotBuild("not from this content")
    monkeypatch.setitem(B.BUILDERS, planned, cannot)
    second = P.produce(None, "show me the temperature over time", [P.Given("temps.csv", base.temps_csv())])
    assert second.status == "made", second.record.get("candidates")
    fell = [c for c in second.record["candidates"] if c.get("fallback")]
    assert fell and "could not be built from this content" in fell[0]["fallback"]


def test_the_partial_switch_is_on_the_make_command():
    from neuroscifi.cli import build_parser
    args = build_parser().parse_args(["artifact", "make", "x.csv", "-r", "show it", "--partial"])
    assert args.partial is True
    assert build_parser().parse_args(["artifact", "make", "x.csv", "-r", "show it"]).partial is False
