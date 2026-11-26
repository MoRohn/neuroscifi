"""Family `transform`: several steps, judged at the end by the benchmark's readers against the INPUT's truth.

  csv_to_chart     a table file -> a line or scatter chart of two named columns -> values recovered through the axes
  table_to_sound   a table file -> a sonified column -> the ORDER of the values recovered from the pitches
  mesh_to_stl      an OBJ or PLY mesh -> binary or ASCII STL -> reloaded: same volume, same faces, closed, outward
  doc_to_outline   a document -> a node-link outline of its headings -> every heading recovered, every
                   parent-child link recovered, and the headings in document order (depth-first, siblings by
                   position)
  archive_index    an archive of mixed files -> an index (CSV: path, size, ...) -> every member accounted for with
                   its size

The task's files are the input and `request.json` (what to make). Outcomes as in `made`: right, wrong (the result
misstates the input), failed (unreadable, unsafe, nondeterministic), abstained.
"""
from __future__ import annotations

import csv
import io
import json
import time

import numpy as np

from . import Answer, Result, Task, check_split, enc, run_in_process, summarise
from . import readers as R
from .cases import TRANSFORM
from .intake import (CUBE_QUADS, TETRA_FACES, csv_bytes, doc_content, docx_bytes, html_bytes, markdown_bytes, rng_for,
                     sentence, table_values, triangulate)


def _distinct_table(rng, case):
    t = table_values(rng, {"columns": case["columns"], "rows": case["rows"]})
    t["cols"][2] = [int(v) for v in rng.permutation(np.arange(10, 10 + 7 * case["rows"], 7))]    # distinct, unordered
    t["cols"][1] = [float(f"{v:.2f}") for v in np.cumsum(rng.uniform(0.5, 3.0, case["rows"]))]  # increasing x
    return t


def _outline(sections) -> tuple[list[str], list[list[str]]]:
    heads, edges, stack = [], [], []
    for level, heading, _ in sections:
        heads.append(heading)
        while stack and stack[-1][0] >= level:
            stack.pop()
        if stack:
            edges.append([stack[-1][1], heading])
        stack.append((level, heading))
    return heads, edges


def build(case) -> tuple[dict, dict, dict, dict]:
    """(files, declared, request, truth)."""
    kind, rng = case["transform"], rng_for(case["seed"])
    if kind in ("csv_to_chart", "table_to_sound"):
        t = _distinct_table(rng, case)
        x_name, y_name = t["names"][1], t["names"][2]
        fmt = case.get("input", "csv")
        if fmt == "json":
            data = enc.jdump([dict(zip(t["names"], row)) for row in zip(*t["cols"])])
            name, declared = "table.json", "application/json"
        else:
            data = csv_bytes(t, delim="\t" if fmt == "tsv" else ",")
            name, declared = f"table.{fmt}", "text/csv" if fmt == "csv" else "text/tab-separated-values"
        if kind == "csv_to_chart":
            request = {"transform": kind, "form": case["form"], "x": x_name, "y": y_name}
            truth = {"x": t["cols"][1], "y": t["cols"][2]}
        else:
            request = {"transform": kind, "column": y_name, "low_hz": case["low_hz"], "high_hz": case["high_hz"],
                       "tone_s": case["tone_s"]}
            truth = {"values": t["cols"][2]}
        return {name: data}, {name: declared}, request, truth
    if kind == "mesh_to_stl":
        o = case["origin"]
        if case["shape"] == "cube":
            s = case["side"]
            verts = [[o[0] + s * (i & 1), o[1] + s * ((i >> 1) & 1), o[2] + s * ((i >> 2) & 1)] for i in range(8)]
            faces, volume = [list(f) for f in CUBE_QUADS], s ** 3
        else:
            a, b, c = case["legs"]
            verts = [list(o), [o[0] + a, o[1], o[2]], [o[0], o[1] + b, o[2]], [o[0], o[1], o[2] + c]]
            faces, volume = [list(f) for f in TETRA_FACES], a * b * c / 6
        if case["input"] == "obj":
            data, name, declared = enc.obj(verts, faces), "part.obj", "model/obj"
        else:
            data, name, declared = enc.ply(verts, faces), "part.ply", "model/ply"
        return ({name: data}, {name: declared}, {"transform": kind, "format": case["format"]},
                {"volume": volume, "n_faces": len(triangulate(faces))})
    if kind == "doc_to_outline":
        d = doc_content(rng, {"sections": case["sections"], "rows": 2, "figures": 0})
        sections = [(lv, h, p) for lv, h, p in d["sections"]]
        d["sections"] = sections
        fmt = case["input"]
        data = {"md": markdown_bytes, "docx": docx_bytes, "html": html_bytes}[fmt](d)
        name = {"md": "notes.md", "docx": "notes.docx", "html": "notes.html"}[fmt]
        declared = {"md": "text/markdown", "html": "text/html",
                    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}[fmt]
        heads, edges = _outline(sections)
        return {name: data}, {name: declared}, {"transform": kind}, {"headings": heads, "edges": edges}
    if kind == "archive_index":
        files = {}
        for i in range(case["members"]):
            ext = ("csv", "txt", "json", "png")[i % 4]
            path = f"{('docs', 'data', 'img')[i % 3]}/item{i}.{ext}"
            if ext == "png":
                files[path] = enc.png(rng.integers(0, 256, size=(3, 4)).astype(np.uint8), mode="gray")
            elif ext == "json":
                files[path] = enc.jdump({"i": i})
            else:
                files[path] = (sentence(rng) * (1 + i)).encode()
        if case["input"] == "zip":
            data, name, declared = enc.zip_files(files), "mixed.zip", "application/zip"
        else:
            data, name, declared = enc.tar_archive(files), "mixed.tar", "application/x-tar"
        return ({name: data}, {name: declared}, {"transform": kind, "format": "csv"},
                {"members": {p: len(b) for p, b in sorted(files.items())}})
    raise ValueError(kind)


def tasks(split: str) -> list[Task]:
    check_split(split)
    out = []
    for case in TRANSFORM[split]:
        files, declared, request, truth = build(case)
        files = dict(files, **{"request.json": json.dumps(request).encode()})
        declared = dict(declared, **{"request.json": "application/json"})
        out.append(Task(f"transform/{split}/{case['name']}", "transform", files, declared,
                        f"transform:{case['transform']}", truth, None, "transform"))
    return out


# ---- judging -------------------------------------------------------------------------------------------------------


def _dfs_order(reading: R.DiagramReading) -> list[str]:
    """Headings in reading order: depth-first from the node no edge points to, siblings by position across the
    direction the tree grows."""
    labels = reading.labels
    pos = dict(zip(labels, reading.positions))
    children: dict[str, list[str]] = {}
    has_parent = set()
    for a, b in reading.edges:
        children.setdefault(a, []).append(b)
        has_parent.add(b)
    roots = [n for n in labels if n not in has_parent]
    growth_down = np.mean([pos[b][1] - pos[a][1] for a, b in reading.edges]) if reading.edges else 1.0
    key = (lambda n: (pos[n][0], pos[n][1])) if abs(growth_down) >= 1e-9 and growth_down > 0 else \
        (lambda n: (pos[n][1], pos[n][0]))
    order, seen = [], set()

    def visit(n):
        if n in seen:
            return
        seen.add(n)
        order.append(n)
        for c in sorted(children.get(n, []), key=key):
            visit(c)
    for r in sorted(roots, key=key):
        visit(r)
    return order


def carried(kind: str, data: bytes, request: dict, truth: dict) -> tuple[int, int, dict]:
    checks: dict = {}
    if kind == "csv_to_chart":
        checks["unsafe"] = R.svg_safety(data)[:3]
        checks["hue"] = R.svg_palette(data)[:3]
        chart = R.read_chart(data)
        lines = [m for m in chart.marks if m.kind == "line"]
        rec = list(lines[0].points) if lines and request["form"] == "line" else \
            [(m.x, m.y) for m in chart.marks if m.kind == "point"]
        src = list(zip(truth["x"], truth["y"]))
        span = lambda v: (max(v) - min(v)) or 1.0          # noqa: E731
        hits = R.carried_points(src, rec, 0.01 * span(truth["x"]), 0.01 * span(truth["y"]),
                                ordered=request["form"] == "line")
        return hits, len(src), checks
    if kind == "table_to_sound":
        values = truth["values"]
        pitches = R.tone_pitches(R.read_wav(data), len(values))
        same_order = list(np.argsort(pitches, kind="stable")) == list(np.argsort(values, kind="stable"))
        return int(same_order), 1, checks
    if kind == "mesh_to_stl":
        m = R.read_mesh(data)
        tight, oriented = m.edge_report()
        checks["geometry"] = [w for w, ok in (("not watertight", tight), ("inconsistent winding", oriented),
                                              ("normals disagree", m.normals_agree() is not False)) if not ok]
        if request["format"] == "stl" and not (data[:5] == b"solid" or len(data) >= 84):
            checks["geometry"].append("not STL")
        items = [abs(m.volume - truth["volume"]) <= 1e-6 * truth["volume"], len(m.triangles) == truth["n_faces"]]
        return sum(items), 2, checks
    if kind == "doc_to_outline":
        checks["unsafe"] = R.svg_safety(data)[:3]
        checks["hue"] = R.svg_palette(data)[:3]
        d = R.read_diagram(data)
        heads, edges = truth["headings"], truth["edges"]
        got = {tuple(e) for e in d.edges}
        hits = sum(1 for h in heads if h in d.labels) + sum(1 for e in edges if tuple(e) in got)
        hits += int(_dfs_order(d) == heads)
        return hits, len(heads) + len(edges) + 1, checks
    if kind == "archive_index":
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline="")))
        index = {}
        for row in rows:
            path = row.get("path") or row.get("name") or row.get("member")
            size = row.get("size") or row.get("bytes")
            if path is not None and size is not None:
                index[path.lstrip("./")] = size
        hits = 0
        for path, size in truth["members"].items():
            try:
                hits += int(float(index.get(path, "nan")) == size)
            except ValueError:
                pass
        return hits, len(truth["members"]), checks
    raise ValueError(kind)


def judge(task: Task, first: Answer, second: Answer | None) -> Result:
    if first.status != "answered":
        return Result("abstained", {"reason": first.reason[:160]})
    value = first.value if isinstance(first.value, dict) else {}
    data = value.get("bytes")
    if not isinstance(data, (bytes, bytearray)) or not data:
        return Result("failed", {"failure": "no bytes"})
    kind = task.question.split(":", 1)[1]
    request = json.loads(task.files["request.json"])
    detail = {"bytes": len(data), "media_type": value.get("media_type")}
    try:
        hits, total, checks = carried(kind, bytes(data), request, task.expect)
    except (R.ReadError, ValueError, IndexError, KeyError, UnicodeDecodeError) as error:
        return Result("failed", dict(detail, failure="unreadable", error=f"{type(error).__name__}: {error}"[:160]))
    detail["carried"] = round(hits / total, 4)
    detail["deterministic"] = bool(second is not None and isinstance(second.value, dict)
                                   and second.value.get("bytes") == data)
    problems = [p for k in ("unsafe", "hue", "geometry") for p in checks.get(k, [])]
    if problems:
        detail["problems"] = problems[:4]
    if hits < total:
        return Result("wrong", dict(detail, why="the result misstates the input"))
    if problems or not detail["deterministic"]:
        return Result("failed", dict(detail, failure="unsafe, palette, geometry or nondeterministic"))
    return Result("right", detail)


def score(subject, split: str) -> dict:
    start = time.perf_counter()
    rows = []
    for task in tasks(split):
        first, info = run_in_process(task, subject)
        if first is None:
            rows.append((task, Result("failed", info)))
            continue
        second = run_in_process(task, subject)[0] if first.status == "answered" else None
        rows.append((task, judge(task, first, second)))
    produced = [r.detail for _, r in rows if "carried" in r.detail]
    return summarise("transform", split, subject, rows, time.perf_counter() - start,
                     {"produced": len(produced),
                      "carried_mean": round(sum(d["carried"] for d in produced) / len(produced), 4)
                      if produced else None})
