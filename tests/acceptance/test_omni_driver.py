"""The `artifacts` driver (acceptance/omni/subject.py) against a stand-in package written here from DESIGN.md §2/§3/§6
(frozen dataclasses with the stated fields; `adapters.decode` built on the test oracle; `make.make` on the reference
maker). It proves that a package which decodes correctly is scored right through the driver's reading of the
representations, that a named failure becomes "refused" or "cannot", and that `Opaque` gives "cannot" for content
but still answers identification. The stand-in lives only in sys.modules for the duration of each test."""
from dataclasses import dataclass, field
import io
import json
import re
import sys
import types
import zipfile

import numpy as np
import pytest

from neuroscifi.acceptance import omni
from neuroscifi.acceptance.omni import Answer, made, readers, subject, transform

import omni_oracle as oracle
import omni_reference_maker as reference


def _stand_in():
    T = types.ModuleType("neuroscifi.artifacts.types")

    @dataclass(frozen=True)
    class Detected:
        family: str
        format: str
        media_type: str = "application/octet-stream"
        basis: str = "signature"
        container: object = None
        reasons: tuple = ()

    @dataclass(frozen=True)
    class Limits:
        max_bytes: int = 64 << 20

    class ArtifactError(ValueError):
        def __init__(self, reason, text=""):
            super().__init__(text or reason)
            self.reason = reason

    @dataclass(frozen=True)
    class Column:
        name: str
        kind: str
        unit: object
        values: object
        n_missing: int

    @dataclass(frozen=True)
    class Table:
        columns: tuple
        n_rows: int
        title: object = None

    @dataclass(frozen=True)
    class Raster:
        pixels: np.ndarray
        mode: str
        bits: int

    @dataclass(frozen=True)
    class Signal:
        samples: np.ndarray
        rate_hz: float
        channel_names: tuple = ()

    @dataclass(frozen=True)
    class Mesh:
        vertices: np.ndarray
        faces: np.ndarray
        unit: object = None

    @dataclass(frozen=True)
    class Member:
        path: str
        size: int
        detected: object
        inner: object
        note: str = ""

    @dataclass(frozen=True)
    class Bundle:
        members: tuple

    @dataclass(frozen=True)
    class Block:
        kind: str
        level: int
        text: str
        ref: object = None

    @dataclass(frozen=True)
    class Document:
        blocks: tuple
        title: object = None

        def text(self):
            return " ".join(b.text for b in self.blocks)

    @dataclass(frozen=True)
    class Node:
        name: str
        value: object
        attrs: dict = field(default_factory=dict)
        children: tuple = ()

    @dataclass(frozen=True)
    class Tree:
        root: object

    @dataclass(frozen=True)
    class Opaque:
        size: int
        findings: tuple = ()
        candidates: tuple = ()
        needs: tuple = ("an adapter nobody has written",)

    @dataclass(frozen=True)
    class Decoded:
        detected: object
        representation: object
        notes: tuple = ()
        partial: bool = False

    for k, v in list(locals().items()):
        if isinstance(v, type):
            setattr(T, k, v)

    def table_of(t):
        cols = []
        for name, values in zip(t["names"], t["cols"]):
            numeric = all(v is None or isinstance(v, float) for v in values)
            vals = np.array([np.nan if v is None else v for v in values]) if numeric else tuple(values)
            cols.append(Column(name, "number" if numeric else "text", None, vals, sum(v is None for v in values)))
        return Table(tuple(cols), len(t["cols"][0]))

    def tree_of(name, value):
        if isinstance(value, dict):
            return Node(name, None, {}, tuple(tree_of(k, v) for k, v in value.items()))
        if isinstance(value, list):
            return Node(name, None, {}, tuple(tree_of(str(i), v) for i, v in enumerate(value)))
        return Node(name, value)

    def decode(data, *, name=None, declared=None, limits=None):
        ext = (name or "").rsplit(".", 1)[-1]
        if ext == "csv":
            return Decoded(Detected("table", "csv"), table_of(oracle.csv_table(data)))
        if ext == "png":
            r = readers.read_png(data)
            px = r.frames[0]
            mode = {1: "gray", 3: "rgb", 4: "rgba"}.get(px.shape[2], "gray")
            return Decoded(Detected("image", "png"), Raster(px[:, :, 0] if px.shape[2] == 1 else px, mode,
                                                            8 if r.color_type == 3 else r.bits))
        if ext == "wav":
            s = oracle.wav_sound(data)
            full = float(np.abs(s["x"]).max()) or 1.0
            scale = 32767.0 if full <= 32767 else 8388607.0
            return Decoded(Detected("audio", "wav"), Signal((s["x"] / scale).astype(np.float32), float(s["rate"])))
        if ext in ("obj", "stl"):
            corners = oracle.mesh_read(name, data)["corners"].reshape(-1, 3)
            return Decoded(Detected("mesh", ext), Mesh(corners, np.arange(len(corners)).reshape(-1, 3)))
        if ext == "json":
            return Decoded(Detected("tree", "json"), Tree(tree_of("", json.loads(data))))
        if ext == "md":
            d = oracle.markdown_doc(data)
            levels = [len(m.group(1)) for m in re.finditer(r"^(#+)\s", data.decode(), re.M)]
            blocks = [Block("heading", lv, h) for lv, h in zip(levels, d["headings"])]
            blocks.append(Block("paragraph", 0, d["text"]))
            return Decoded(Detected("document", "markdown"), Document(tuple(blocks)))
        if ext == "zip":
            z = zipfile.ZipFile(io.BytesIO(data))
            members = []
            for info in z.infolist():
                if info.filename.startswith("../"):
                    raise ArtifactError("refused", "a member path leaves the archive")
                inner = decode(z.read(info), name=info.filename)
                members.append(Member(info.filename, info.file_size, inner.detected, inner.representation))
            return Decoded(Detected("archive", "zip"), Bundle(tuple(members)))
        if ext == "xlsx":
            raise ArtifactError("missing_capability", "no xlsx adapter here")
        return Decoded(Detected("unknown", "unknown"), Opaque(len(data)))

    A = types.ModuleType("neuroscifi.artifacts.adapters")
    A.decode = decode
    M = types.ModuleType("neuroscifi.artifacts.make")
    reverse = {v: k for k, v in subject.FORM_NAMES.items()}

    @dataclass(frozen=True)
    class Made:
        bytes: bytes
        media_type: str

    def make(spec, limits):
        return Made(*reference.make(_request_of(spec, reverse)))
    M.make = make
    P = types.ModuleType("neuroscifi.artifacts")
    return {"neuroscifi.artifacts": P, "neuroscifi.artifacts.types": T, "neuroscifi.artifacts.adapters": A,
            "neuroscifi.artifacts.make": M}


def _request_of(spec, reverse):
    """The benchmark request a spec was built from: the inverse of the driver's field-name mapping
    (subject._data_and_options), so the reference maker can make it."""
    form = reverse[spec["spec"]]
    data, options = dict(spec["data"]), dict(spec["options"])
    if form == "sonify":
        data |= {"low_hz": options["low_hz"], "high_hz": options["high_hz"], "tone_s": options["tone_s"],
                 "domain": options["range"]}
    elif form == "surface":
        nx, ny = len(data["values"][0]), len(data["values"])
        data = {"x": [float(v) for v in np.linspace(*data["x"], nx)], "y": [float(v) for v in np.linspace(*data["y"], ny)],
                "z": data["values"], "format": options.get("format", "stl")}
    elif form == "animation":
        data = {"frames": data["frames"], "delays_s": [d / 1000 for d in data["delays_ms"]]}
    elif form in ("line", "scatter", "bar"):
        labels = {"x_label": data["x"]["label"], "y_label": data["y"]["label"]}
        if form == "line":
            data = {"series": [{"name": s["name"], "x": [p[0] for p in s["points"]], "y": [p[1] for p in s["points"]]}
                               for s in data["series"]]} | labels
        elif form == "scatter":
            data = {"points": data["series"][0]["points"]} | labels
        else:
            data = {"categories": data["categories"], "values": data["series"][0]["values"],
                    "warn": data["series"][0].get("warning", [])} | labels
    elif form == "histogram":
        import math
        w, values = options["bin_width"], data["values"]
        data = {"values": values, "bin_edges": [k * w for k in range(math.floor(min(values) / w),
                                                                     math.ceil(max(values) / w) + 1)]}
    elif form == "timeline":
        from datetime import datetime, timezone
        data = {"events": [{"label": e["label"], "date": datetime.fromtimestamp(e["at"], tz=timezone.utc)
                            .strftime("%Y-%m-%d")} for e in data["events"]]}
    elif form == "diagram":
        label = {n["id"]: n["label"] for n in data["nodes"]}
        data = {"nodes": [n["label"] for n in data["nodes"]], "edges": [[label[e["from"]], label[e["to"]]]
                                                                         for e in data["edges"]]}
    elif form == "data_file":
        cols = data["columns"]
        data = {"format": "csv", "columns": [c["name"] for c in cols],
                "rows": [list(r) for r in zip(*[c["values"] for c in cols])]}
    return dict(data, form=form)


@pytest.fixture
def artifacts(monkeypatch):
    for name, module in _stand_in().items():
        monkeypatch.setitem(sys.modules, name, module)
    return subject.ArtifactsSubject()


READ = (".csv", ".png", ".wav", ".obj", ".stl", ".json", ".md", ".zip")


def test_a_correct_package_is_right_through_the_driver(artifacts):
    checked = 0
    for split in omni.SPLITS:
        for t in omni.family("intake").tasks(split):
            (name, _), = t.files.items()
            if not name.endswith(READ) or t.question.startswith("detect.") or "::" in t.question and \
                    not name.endswith(".zip"):
                continue
            if name.endswith(".json") and not t.question.startswith("tree."):
                continue
            if name.endswith(".md") and not t.question.startswith(("doc.headings", "doc.contains")):
                continue
            if name.endswith(".zip") and t.question.startswith("code."):
                continue
            a = artifacts.answer(t)
            assert a.status == "answered", (t.id, a.reason)
            assert omni.score_task(t, a).outcome == "right", (t.id, t.expect, a.value)
            checked += 1
    assert checked >= 80


def test_named_failures_and_opaque(artifacts):
    t = next(t for t in omni.family("intake").tasks("development") if t.id.endswith("xlsx-two-sheets/table.sheets"))
    assert artifacts.answer(t).status == "cannot"                    # missing_capability
    unknown = [t for t in omni.family("unknown").tasks("development") if "random-4k" in t.id]
    by_q = {t.question: artifacts.answer(t) for t in unknown}
    assert by_q["detect.format"] == Answer("unknown", "answered")
    assert by_q["table.n_rows"].status == "cannot"
    slip = next(t for t in omni.family("hostile").tasks("development") if t.id.endswith("zip-member-dotdot"))
    assert artifacts.answer(slip).status == "refused"


def test_made_and_transform_through_the_driver(artifacts):
    out = made.score(artifacts, "development")
    # forms no product form makes (a solid from given vertices and faces) are "cannot" before anything is made
    unmade = [t.id for t in made.tasks("development") if subject.FORM_NAMES[t.question.split(":")[1]] is None]
    assert out["right"] == out["n"] - len(unmade) and out["abstained"] == len(unmade), out["failures"]
    for t in transform.tasks("development"):
        kind = t.question.split(":")[1]
        name = next(n for n in t.files if n != "request.json")
        if kind == "mesh_to_stl":
            assert artifacts.answer(t).status == "cannot"
            continue
        if kind == "doc_to_outline" and not name.endswith(".md"):
            continue
        a = artifacts.answer(t)
        result = transform.judge(t, a, artifacts.answer(t))
        if kind == "doc_to_outline":            # the reference maker's circular layout does not keep reading order
            assert result.detail.get("carried", 0) >= 0.8, (t.id, result.detail)
            continue
        assert result.outcome == "right", (t.id, result.detail)
