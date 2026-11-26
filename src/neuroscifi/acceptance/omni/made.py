"""Family `made`: requests to MAKE something. A task's `request.json` is the benchmark's neutral request (form and
source data as plain lists; subject._spec turns it into a DESIGN §6 spec); its `expect` is the source truth. The
subject is asked twice. The produced bytes are judged ONLY by the benchmark's readers (readers.py), which recover
the content the way a person reads it (DESIGN §6, "the reading contract") and never look at the request's numbers
inside the artifact (`<metadata>` is skipped):

  carried information  share of source values recovered within tolerance (1 % of the data's range for charts,
                       2 % of the stated domain for sonified values, 1 % for pitch and duration, exact pixels,
                       1e-6 relative for geometry, exact cells)
  safety               SVG: only allowed elements and attributes, no script, event attribute or external reference
  determinism          the two makes give the same bytes
  palette              no hue except on values the request marks as warnings (SVG); gray pixels (PNG)

Outcomes: right = everything carried, safe, deterministic, palette kept; wrong = an artifact that misstates the
source (some value not recovered within tolerance: a picture of wrong numbers is a fabrication); failed = unreadable
by the reading contract, unsafe, not deterministic, palette broken, or a crash; abstained = "cannot".

Forms: line, bar (one with a warned value), scatter, histogram, timeline, node-link diagram, tone, sonified series,
mesh (STL, a closed solid), surface (OBJ height field), animation (APNG), data file (CSV).
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import json
import time

import numpy as np

from . import Answer, Result, Task, check_split, run_in_process, summarise
from . import readers as R
from .cases import MADE
from .intake import CUBE_QUADS, rng_for, triangulate, word

FORMS = ("line", "bar", "scatter", "histogram", "timeline", "diagram", "tone", "sonify", "mesh", "surface",
         "animation", "data_file")


def _round(v, nd=3) -> float:
    return float(f"{v:.{nd}f}")


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d")


def request_for(case) -> tuple[dict, dict]:
    """(request, truth) for one case: values chosen from the case's seed."""
    form, rng, n = case["form"], rng_for(case["seed"]), case.get("n", 8)
    title = f"{word(rng).capitalize()} {word(rng)}"
    if form == "line":
        x = [float(i * case.get("step", 1)) for i in range(n)]
        series = [{"name": f"{word(rng)}-{k}", "x": x, "y": [_round(v) for v in np.cumsum(rng.normal(0, 3, n)) +
                                                              rng.uniform(-20, 40)]}
                  for k in range(case.get("series", 1))]
        return ({"form": form, "title": title, "x_label": "step", "y_label": "level", "series": series},
                {"series": series})
    if form == "bar":
        cats = [f"{word(rng)}{i}" for i in range(n)]
        values = [_round(v, 2) for v in rng.uniform(case.get("low", 0), case.get("high", 100), n)]
        request = {"form": form, "title": title, "categories": cats, "values": values, "y_label": "amount"}
        if case.get("warn"):
            request["warn"] = list(case["warn"])
        return request, {"categories": cats, "values": values, "warn": list(case.get("warn", ()))}
    if form == "scatter":
        pts = [[_round(a), _round(b)] for a, b in zip(rng.uniform(0, 50, n), rng.uniform(-10, 10, n))]
        return {"form": form, "title": title, "points": pts, "x_label": "a", "y_label": "b"}, {"points": pts}
    if form == "histogram":
        values = [_round(v, 2) for v in rng.normal(case.get("mean", 10), case.get("sd", 3), n)]
        lo, width, k = case["first_edge"], case["width"], case["bins"]
        edges = [lo + width * i for i in range(k + 1)]
        counts = [sum(1 for v in values if edges[i] <= v < edges[i + 1] or (i == k - 1 and v == edges[-1]))
                  for i in range(k)]
        return ({"form": form, "title": title, "values": values, "bin_edges": edges, "x_label": "value"},
                {"edges": edges, "counts": counts})
    if form == "timeline":
        start = datetime(*case["start"], tzinfo=timezone.utc).timestamp()
        days = sorted(int(d) for d in rng.choice(case["span_days"], size=n, replace=False))
        events = [{"label": f"{word(rng)} {i}", "date": _iso(start + d * 86400)} for i, d in enumerate(days)]
        return ({"form": form, "title": title, "events": events},
                {"times": [start + d * 86400 for d in days], "labels": [e["label"] for e in events]})
    if form == "diagram":
        nodes = [f"{word(rng)}-{i}" for i in range(n)]
        edges = [[nodes[int(rng.integers(i))], nodes[i]] for i in range(1, n)]          # a tree
        have = {tuple(sorted(e)) for e in edges}
        for a, b in rng.integers(0, n, size=(case.get("extra_edges", 0), 2)).tolist():
            pair = tuple(sorted((nodes[a], nodes[b])))
            if a != b and pair not in have:
                have.add(pair)
                edges.append([nodes[a], nodes[b]])
        return {"form": form, "title": title, "nodes": nodes, "edges": edges}, {"nodes": nodes, "edges": edges}
    if form == "tone":
        return ({"form": form, "title": title, "frequency_hz": case["hz"], "duration_s": case["seconds"]},
                {"hz": case["hz"], "seconds": case["seconds"]})
    if form == "sonify":
        values = [_round(v, 2) for v in rng.uniform(case["domain"][0], case["domain"][1], n)]
        request = {"form": form, "title": title, "values": values, "low_hz": case["low_hz"],
                   "high_hz": case["high_hz"], "domain": list(case["domain"]), "tone_s": case["tone_s"]}
        return request, {"values": values}
    if form == "mesh":
        s, o = case["side"], case["origin"]
        verts = [[o[0] + s * (i & 1), o[1] + s * ((i >> 1) & 1), o[2] + s * ((i >> 2) & 1)] for i in range(8)]
        faces = triangulate([list(f) for f in CUBE_QUADS])
        return ({"form": form, "title": title, "format": "stl", "unit": case.get("unit", "mm"), "vertices": verts,
                 "faces": faces},
                {"volume": s ** 3, "bounds": [list(o), [o[0] + s, o[1] + s, o[2] + s]], "n_faces": 12})
    if form == "surface":
        xs = [float(v) for v in np.linspace(case["x"][0], case["x"][1], case["nx"])]
        ys = [float(v) for v in np.linspace(case["y"][0], case["y"][1], case["ny"])]
        z = [[_round(v) for v in row] for row in rng.normal(0, case.get("relief", 1.0), (len(ys), len(xs)))]
        return ({"form": form, "title": title, "format": "obj", "x": xs, "y": ys, "z": z},
                {"x": xs, "y": ys, "z": z, "n_faces": 2 * (len(xs) - 1) * (len(ys) - 1)})
    if form == "animation":
        h, w = case["size"]
        frames = [rng.integers(0, 256, size=(h, w)).tolist() for _ in range(case["frames"])]
        delays = [float(d) for d in case["delays_s"]]
        return {"form": form, "title": title, "frames": frames, "delays_s": delays}, {"frames": frames,
                                                                                      "delays": delays}
    if form == "data_file":
        cols = [f"{word(rng)}_{i}" for i in range(case["cols"])]
        rows = [[f"{word(rng)}-{r}"] + [_round(v, 4) for v in rng.normal(0, 100, case["cols"] - 1)]
                for r in range(n)]
        return {"form": form, "title": title, "format": "csv", "columns": cols, "rows": rows}, {"columns": cols,
                                                                                               "rows": rows}
    raise ValueError(form)


def tasks(split: str) -> list[Task]:
    check_split(split)
    out = []
    for case in MADE[split]:
        request, truth = request_for(case)
        out.append(Task(f"made/{split}/{case['name']}", "made", {"request.json": json.dumps(request).encode()},
                        {"request.json": "application/json"}, f"make:{case['form']}", truth, None, "make"))
    return out


# ---- reading back --------------------------------------------------------------------------------------------------


def _span(values) -> float:
    values = [float(v) for v in values]
    return (max(values) - min(values)) or max(1.0, abs(values[0]))


def _carried_svg(form: str, data: bytes, truth: dict) -> tuple[int, int]:
    if form == "diagram":
        d = R.read_diagram(data)
        labels = sum(1 for n in truth["nodes"] if n in d.labels)
        got = {tuple(sorted(e)) for e in d.edges}
        edges = sum(1 for e in truth["edges"] if tuple(sorted(e)) in got)
        return labels + edges, len(truth["nodes"]) + len(truth["edges"])
    chart = R.read_chart(data)
    if form == "line":
        series = chart.series()
        hits = total = 0
        for k, s in enumerate(truth["series"]):
            marks = series.get(s["name"])
            if marks is None and len(series) == len(truth["series"]):
                marks = list(series.values())[k]
            src = list(zip(s["x"], s["y"]))
            total += len(src)
            if not marks:
                continue
            lines = [m for m in marks if m.kind == "line"]
            rec = list(lines[0].points) if lines else [(m.x, m.y) for m in marks if m.kind == "point"]
            hits += R.carried_points(src, rec, 0.01 * _span(s["x"]), 0.01 * _span(s["y"]), ordered=True)
        return hits, total
    if form == "scatter":
        pts = truth["points"]
        rec = [(m.x, m.y) for m in chart.marks if m.kind == "point"]
        return R.carried_points([tuple(p) for p in pts], rec, 0.01 * _span([p[0] for p in pts]),
                                0.01 * _span([p[1] for p in pts]), ordered=False), len(pts)
    if form == "bar":
        got = {}
        for m in chart.marks:
            if m.kind == "bar":
                cat, val = (m.x, m.y) if chart.x.kind == "category" else (m.y, m.x)
                got[str(cat)] = val
        tol = 0.01 * max(_span(truth["values"]), max(abs(v) for v in truth["values"]))
        return sum(1 for c, v in zip(truth["categories"], truth["values"])
                   if c in got and abs(got[c] - v) <= tol), len(truth["values"])
    if form == "histogram":
        edges, counts = truth["edges"], truth["counts"]
        tol_x = 0.01 * (edges[-1] - edges[0])
        bars = [m for m in chart.marks if m.kind == "bar" and m.x_extent]
        hits = 0
        for i, c in enumerate(counts):
            match = [m for m in bars if abs(m.x_extent[0] - edges[i]) <= tol_x and abs(m.x_extent[1] - edges[i + 1])
                     <= tol_x]
            if c == 0 and not match:
                hits += 1
            elif match and abs(match[0].y - c) <= max(0.5, 0.01 * max(counts)):
                hits += 1
        return hits, len(counts)
    if form == "timeline":
        times = truth["times"]
        rec = [(m.x, 0.0) for m in chart.marks if m.kind == "point" and isinstance(m.x, float)]
        return R.carried_points([(t, 0.0) for t in times], rec, 0.01 * _span(times), 1.0, ordered=False), len(times)
    raise ValueError(form)


def carried(form: str, data: bytes, request: dict, truth: dict) -> tuple[int, int, dict]:
    """(recovered, total, checks): `checks` holds the form's own safety and palette verdicts."""
    checks: dict = {}
    if form in ("line", "bar", "scatter", "histogram", "timeline", "diagram"):
        checks["unsafe"] = R.svg_safety(data)[:3]
        hits, total = _carried_svg(form, data, truth)
        checks["hue"] = R.svg_palette(data, warn_allowed=len(truth.get("warn", ())))[:3]
        return hits, total, checks
    if form == "tone":
        s = R.read_wav(data)
        hz = R.dominant_hz(s.samples, s.rate)
        ok_hz = abs(hz - truth["hz"]) <= 0.01 * truth["hz"]
        ok_len = abs(s.duration_s - truth["seconds"]) <= max(0.01, 0.01 * truth["seconds"])
        return int(ok_hz) + int(ok_len), 2, checks
    if form == "sonify":
        s = R.read_wav(data)
        lo, hi = request["low_hz"], request["high_hz"]
        a, b = request["domain"]
        values = truth["values"]
        got = [a + (f - lo) / (hi - lo) * (b - a) for f in R.tone_pitches(s, len(values))]
        return sum(1 for v, g in zip(values, got) if abs(v - g) <= 0.02 * (b - a)), len(values), checks
    if form in ("mesh", "surface"):
        m = R.read_mesh(data)
        tight, oriented = m.edge_report()
        if form == "mesh":
            checks["geometry"] = [w for w, ok in (("not watertight", tight), ("inconsistent winding", oriented),
                                                  ("normals disagree", m.normals_agree() is not False),
                                                  ("inward", m.volume > 0)) if not ok]
            lo, hi = m.bounds
            items = [abs(m.volume - truth["volume"]) <= 1e-6 * truth["volume"],
                     np.allclose(lo, truth["bounds"][0], atol=1e-6), np.allclose(hi, truth["bounds"][1], atol=1e-6),
                     len(m.triangles) == truth["n_faces"]]
            return sum(map(bool, items)), len(items), checks
        hits = 0
        for j, y in enumerate(truth["y"]):
            for i, x in enumerate(truth["x"]):
                near = np.nonzero((np.abs(m.vertices[:, 0] - x) <= 1e-6) & (np.abs(m.vertices[:, 1] - y) <= 1e-6))[0]
                hits += bool(len(near)) and bool(np.any(np.abs(m.vertices[near, 2] - truth["z"][j][i]) <= 1e-6))
        hits += int(len(m.triangles) == truth["n_faces"])
        return hits, len(truth["x"]) * len(truth["y"]) + 1, checks
    if form == "animation":
        r = R.read_png(data)
        checks["hue"] = [] if R.png_is_gray(r) else ["coloured pixels"]
        frames, delays = truth["frames"], truth["delays"]
        hits = 0
        for k, (f, d) in enumerate(zip(frames, delays)):
            if k < len(r.frames):
                px = r.frames[k][:, :, 0] if r.frames[k].shape[2] in (1, 2) else r.frames[k][:, :, 0]
                hits += int(px.shape == np.array(f).shape and np.array_equal(px, np.array(f)))
                hits += int(abs(r.delays_s[k] - d) <= 0.001)
        if len(r.frames) != len(frames):
            hits = 0
        return hits, 2 * len(frames), checks
    if form == "data_file":
        text = data.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text, newline="")))
        header, body = rows[0], [r for r in rows[1:] if r]
        hits = sum(1 for a, b in zip(truth["columns"], header) if a == b)
        for want, got in zip(truth["rows"], body):
            for a, b in zip(want, got):
                if isinstance(a, float):
                    try:
                        hits += abs(float(b) - a) <= 1e-9 * max(1.0, abs(a))
                    except ValueError:
                        pass
                else:
                    hits += a == b
        return hits, len(truth["columns"]) + sum(len(r) for r in truth["rows"]), checks
    raise ValueError(form)


def judge_made(task: Task, first: Answer, second: Answer | None, form: str | None = None) -> Result:
    if first.status != "answered":
        return Result("abstained", {"reason": first.reason[:160]})
    value = first.value or {}
    data = value.get("bytes") if isinstance(value, dict) else None
    if not isinstance(data, (bytes, bytearray)) or not data:
        return Result("failed", {"failure": "no bytes"})
    form = form or task.question.split(":", 1)[1]
    request = json.loads(task.files["request.json"])
    detail = {"bytes": len(data), "media_type": value.get("media_type")}
    try:
        hits, total, checks = carried(form, bytes(data), request, task.expect)
    except (R.ReadError, ValueError, IndexError, KeyError, UnicodeDecodeError) as error:
        return Result("failed", dict(detail, failure="unreadable", error=f"{type(error).__name__}: {error}"[:160]))
    detail["carried"] = round(hits / total, 4) if total else 0.0
    detail["deterministic"] = bool(second is not None and second.status == "answered" and isinstance(second.value, dict)
                                   and second.value.get("bytes") == data)
    problems = [p for k in ("unsafe", "hue", "geometry") for p in checks.get(k, [])]
    if problems:
        detail["problems"] = problems[:4]
    if hits < total:
        return Result("wrong", dict(detail, why="the artifact misstates the source"))
    if problems or not detail["deterministic"]:
        return Result("failed", dict(detail, failure="unsafe, palette, geometry or nondeterministic"))
    return Result("right", detail)


def _aggregate(rows) -> dict:
    produced = [r.detail for _, r in rows if "carried" in r.detail]
    return {"produced": len(produced),
            "carried_mean": round(sum(d["carried"] for d in produced) / len(produced), 4) if produced else None,
            "deterministic": sum(1 for d in produced if d.get("deterministic")),
            "with_problems": sum(1 for d in produced if d.get("problems"))}


def score(subject, split: str) -> dict:
    start = time.perf_counter()
    rows = []
    for task in tasks(split):
        first, info = run_in_process(task, subject)
        if first is None:
            rows.append((task, Result("failed", info)))
            continue
        second = run_in_process(task, subject)[0] if first.status == "answered" else None
        rows.append((task, judge_made(task, first, second)))
    return summarise("made", split, subject, rows, time.perf_counter() - start, _aggregate(rows))
