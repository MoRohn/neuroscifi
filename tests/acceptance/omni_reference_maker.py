"""A test-only reference MAKER for the OMNIMIND benchmark's `made` and `transform` families: it turns the
benchmark's requests into artifacts that follow DESIGN.md §6's reading contract (SVG charts with axes, ticks and
classed marks; 16-bit PCM WAV; binary STL; OBJ; APNG; CSV). It is written independently of readers.py (no shared
code) and exists only to prove that the judge says "right" for correct artifacts, and catches each deliberate
defect (`flaw`): a 10 % scale error, a wrong pitch, an inverted face, an unasked hue, a script, a random id, a
wrong delay, a changed cell.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import json
import struct
import zlib

import numpy as np

from neuroscifi.acceptance.omni import Answer


def _fmt(v: float) -> str:
    return repr(round(float(v), 9))


class Scale:
    def __init__(self, lo, hi, p0, p1, flaw_factor=1.0):
        if hi == lo:
            lo, hi = lo - 1, hi + 1
        self.lo, self.hi, self.p0, self.p1, self.k = lo, hi, p0, p1, flaw_factor

    def __call__(self, v):                 # where a mark goes (a flawed maker misplaces it)
        return self.p0 + (v * self.k - self.lo) / (self.hi - self.lo) * (self.p1 - self.p0)

    def tick(self, v):                     # where a tick goes (always right)
        return self.p0 + (v - self.lo) / (self.hi - self.lo) * (self.p1 - self.p0)


def _padded(values, zero=False):
    lo, hi = min(values), max(values)
    if zero:
        lo, hi = min(lo, 0.0), max(hi, 0.0)
    pad = 0.05 * ((hi - lo) or 1.0)
    return lo - pad, hi + pad


def _axis(cls, ticks) -> str:
    return f'<g class="axis {cls}">' + "".join(
        f'<g class="tick" transform="translate({x},{y})"><line x2="0" y2="4" stroke="currentColor"/>'
        f"<text>{label}</text></g>" for x, y, label in ticks) + "</g>"


def _svg(body: str, flaw: str | None) -> bytes:
    extra = '<script>alert(1)</script>' if flaw == "script" else ""
    rid = f'<desc>{np.random.default_rng().integers(1 << 30)}</desc>' if flaw == "random_id" else ""
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="400" viewBox="0 0 640 400">'
            f'<style>.mark {{ fill: #555; }} .warn {{ fill: #c0392b; }}</style>{extra}{rid}{body}</svg>').encode()


def chart(request: dict, flaw: str | None = None) -> bytes:
    form = request["form"]
    k = 1.1 if flaw == "skew" else 1.0
    marks, xt, yt = [], [], []
    if form in ("line", "scatter"):
        if form == "line":
            xs = [x for s in request["series"] for x in s["x"]]
            ys = [y for s in request["series"] for y in s["y"]]
        else:
            xs, ys = [p[0] for p in request["points"]], [p[1] for p in request["points"]]
        sx, sy = Scale(*_padded(xs), 60, 600), Scale(*_padded(ys), 340, 40, k)
        xt = [(sx.tick(v), 350, _fmt(v)) for v in np.linspace(sx.lo, sx.hi, 5)]
        yt = [(50, sy.tick(v), _fmt(v)) for v in np.linspace(sy.lo, sy.hi, 5)]
        if form == "line":
            for s in request["series"]:
                pts = " ".join(f"{sx(x):.6f},{sy(y):.6f}" for x, y in zip(s["x"], s["y"]))
                marks.append(f'<g class="series" data-name="{s["name"]}"><polyline class="series" points="{pts}" '
                             'fill="none" stroke="currentColor"/></g>')
        else:
            hue = ' fill="#1f77b4"' if flaw == "hue" else ""
            marks.append('<g class="series" data-name="points">' + "".join(
                f'<circle class="mark" cx="{sx(x):.6f}" cy="{sy(y):.6f}" r="3"{hue}/>' for x, y in request["points"])
                + "</g>")
    elif form == "bar":
        cats, vals = request["categories"], request["values"]
        sy = Scale(*_padded(vals, zero=True), 340, 40, k)
        band = 540 / len(cats)
        xt = [(60 + band * (i + 0.5), 350, c) for i, c in enumerate(cats)]
        yt = [(50, sy.tick(v), _fmt(v)) for v in np.linspace(sy.lo, sy.hi, 5)]
        warn = set(request.get("warn", ()))
        bars = []
        for i, v in enumerate(vals):
            top, base = sy(v), sy.tick(0.0)
            cls = "mark warn" if i in warn or (flaw == "hue" and i == 0) else "mark"
            fill = ' fill="#c0392b"' if cls == "mark warn" else ""
            bars.append(f'<rect class="{cls}" x="{60 + band * i + 4:.6f}" y="{min(top, base):.6f}" '
                        f'width="{band - 8:.6f}" height="{abs(base - top):.6f}"{fill}/>')
        marks.append('<g class="series" data-name="values">' + "".join(bars) + "</g>")
    elif form == "histogram":
        edges = request["bin_edges"]
        counts = [sum(1 for v in request["values"] if edges[i] <= v < edges[i + 1]) for i in range(len(edges) - 1)]
        sx, sy = Scale(edges[0], edges[-1], 60, 600), Scale(0, max(counts) * 1.1, 340, 40, k)
        xt = [(sx.tick(e), 350, _fmt(e)) for e in edges]
        yt = [(50, sy.tick(v), _fmt(v)) for v in np.linspace(0, max(counts) * 1.1, 5)]
        marks.append('<g class="series" data-name="counts">' + "".join(
            f'<rect class="mark" x="{sx(edges[i]):.6f}" y="{sy(c):.6f}" width="{sx(edges[i + 1]) - sx(edges[i]):.6f}"'
            f' height="{sy.tick(0) - sy(c):.6f}"/>' for i, c in enumerate(counts)) + "</g>")
    elif form == "timeline":
        times = [datetime.fromisoformat(e["date"]).replace(tzinfo=timezone.utc).timestamp() for e in request["events"]]
        lo, hi = min(times) - 86400 * 3, max(times) + 86400 * 3
        sx = Scale(lo, hi, 60, 600, k)
        days = np.linspace(lo, hi, 4)
        xt = [(sx.tick(d), 350, datetime.fromtimestamp(d, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
              for d in days]
        yt = [(50, 300, "0"), (50, 100, "1")]
        marks.append('<g class="series" data-name="events">' + "".join(
            f'<circle class="mark" cx="{sx(t):.6f}" cy="200" r="4"/><text x="{sx(t):.6f}" y="190">{e["label"]}</text>'
            for t, e in zip(times, request["events"])) + "</g>")
    else:
        raise ValueError(form)
    body = (_axis("x", xt) + _axis("y", yt) + '<g class="plot">' + "".join(marks) + "</g>"
            + f'<metadata>{json.dumps(request)[:2000].replace("<", "")}</metadata>')
    return _svg(body, flaw)


def diagram(request: dict, flaw: str | None = None) -> bytes:
    nodes = request["nodes"]
    pos = {n: (320 + 150 * np.cos(2 * np.pi * i / len(nodes)), 200 + 150 * np.sin(2 * np.pi * i / len(nodes)))
           for i, n in enumerate(nodes)}
    edges = request["edges"] if flaw != "skew" else [[e[0], nodes[0]] for e in request["edges"]]
    body = "".join(f'<line class="edge" x1="{pos[a][0]:.4f}" y1="{pos[a][1]:.4f}" x2="{pos[b][0]:.4f}" '
                   f'y2="{pos[b][1]:.4f}" stroke="currentColor"/>' for a, b in edges)
    body += "".join(f'<g class="node" transform="translate({x:.4f},{y:.4f})"><circle r="12" fill="#eee" '
                    f'stroke="currentColor"/><text>{n}</text></g>' for n, (x, y) in pos.items())
    return _svg(body, flaw)


def wav(samples: np.ndarray, rate: int) -> bytes:
    data = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def sound(request: dict, flaw: str | None = None) -> bytes:
    rate = 22050
    bend = 1.03 if flaw == "pitch" else 1.0
    if request["form"] == "tone":
        t = np.arange(int(rate * request["duration_s"])) / rate
        return wav(0.5 * np.sin(2 * np.pi * request["frequency_hz"] * bend * t), rate)
    lo, hi = request["low_hz"], request["high_hz"]
    a, b = request["domain"]
    n = int(rate * request["tone_s"])
    t = np.arange(n) / rate
    fade = np.minimum(1, np.minimum(np.arange(n), np.arange(n)[::-1]) / 100)
    parts = [0.5 * fade * np.sin(2 * np.pi * (lo + (v - a) / (b - a) * (hi - lo)) * bend * t)
             for v in request["values"]]
    return wav(np.concatenate(parts), rate)


def stl(vertices, faces, flaw=None) -> bytes:
    faces = [list(f) for f in faces]
    if flaw == "invert":
        faces[0] = faces[0][::-1]
    out = b"reference".ljust(80, b"\x00") + struct.pack("<I", len(faces))
    for f in faces:
        a, b, c = (np.array(vertices[i], float) for i in f)
        n = np.cross(b - a, c - a)
        n = n / np.linalg.norm(n)
        out += struct.pack("<12fH", *n, *a, *b, *c, 0)
    return out


def surface_obj(request: dict, flaw=None) -> bytes:
    xs, ys, z = request["x"], request["y"], request["z"]
    lines, idx = [], {}
    for j, y in enumerate(ys):
        for i, x in enumerate(xs):
            idx[i, j] = len(idx) + 1
            zz = z[j][i] * (1.1 if flaw == "skew" else 1.0)
            lines.append(f"v {x!r} {y!r} {zz!r}")
    for j in range(len(ys) - 1):
        for i in range(len(xs) - 1):
            a, b, c, d = idx[i, j], idx[i + 1, j], idx[i + 1, j + 1], idx[i, j + 1]
            lines += [f"f {a} {b} {c}", f"f {a} {c} {d}"]
    return ("\n".join(lines) + "\n").encode()


def _chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def apng(request: dict, flaw=None) -> bytes:
    frames = [np.array(f, np.uint8) for f in request["frames"]]
    h, w = frames[0].shape
    color = 2 if flaw == "hue" else 0
    out = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, color, 0, 0, 0))
    out += _chunk(b"acTL", struct.pack(">II", len(frames), 0))
    seq = 0
    for i, (f, d) in enumerate(zip(frames, request["delays_s"])):
        if flaw == "delay" and i == 1:
            d = d + 0.05
        out += _chunk(b"fcTL", struct.pack(">IIIIIHHBB", seq, w, h, 0, 0, int(round(d * 1000)), 1000, 0, 0))
        seq += 1
        if color == 2:
            px = np.stack([f, f, np.clip(f.astype(int) + 9, 0, 255).astype(np.uint8)], axis=2)
        else:
            px = f
        raw = zlib.compress(b"".join(b"\x00" + row.tobytes() for row in px))
        if i == 0:
            out += _chunk(b"IDAT", raw)
        else:
            out += _chunk(b"fdAT", struct.pack(">I", seq) + raw)
            seq += 1
    return out + _chunk(b"IEND", b"")


def data_file(request: dict, flaw=None) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(request["columns"])
    for k, row in enumerate(request["rows"]):
        row = list(row)
        if flaw == "cell" and k == 0:
            row[1] = row[1] + 1
        w.writerow([repr(v) if isinstance(v, float) else v for v in row])
    return buf.getvalue().encode()


def make(request: dict, flaw: str | None = None) -> tuple[bytes, str]:
    form = request["form"]
    if form in ("line", "bar", "scatter", "histogram", "timeline"):
        return chart(request, flaw), "image/svg+xml"
    if form == "diagram":
        return diagram(request, flaw), "image/svg+xml"
    if form in ("tone", "sonify"):
        return sound(request, flaw), "audio/wav"
    if form == "mesh":
        return stl(request["vertices"], request["faces"], flaw), "model/stl"
    if form == "surface":
        return surface_obj(request, flaw), "model/obj"
    if form == "animation":
        return apng(request, flaw), "image/apng"
    if form == "data_file":
        return data_file(request, flaw), "text/csv"
    raise ValueError(form)


class ReferenceMaker:
    """A subject that makes every request correctly (or with one deliberate flaw)."""

    def __init__(self, flaw: str | None = None):
        self.flaw = flaw
        self.name = f"reference-maker{':' + flaw if flaw else ''}"

    def answer(self, task) -> Answer:
        if task.kind != "make":
            return Answer(None, "cannot", "the reference maker only makes")
        data, media = make(json.loads(task.files["request.json"]), self.flaw)
        return Answer({"bytes": data, "media_type": media}, "answered")
