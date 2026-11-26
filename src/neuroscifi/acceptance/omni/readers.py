"""The benchmark's own readers of MADE artifacts: they recover the content of produced bytes the way a person reads
them (DESIGN.md §6, "the reading contract"), so that "carried information" is measured, never echoed.

- SVG charts: the axes' scales are fitted from tick positions and tick labels (linear or log10 numbers, ISO 8601
  dates, or category labels by position), and every mark's geometry is mapped back through them. `<metadata>` is
  never read.
- WAV: RIFF parsed by hand; a sonified series is cut into its equal-length tones and each tone's pitch is the
  interpolated FFT peak, mapped back to a value between the two frequencies the request states.
- Meshes (binary and ASCII STL, OBJ, PLY): signed volume (divergence theorem), area, bounds, edge-manifold
  watertightness with consistent orientation, and facet normals against the winding.
- PNG and APNG: chunks with CRCs, all five filters, Adam7, bit depths 1-16, palettes and tRNS, the animation
  control chunks and frame compositing; pixel values come out as numbers.
- Safety and palette checks for SVG: allowed elements and attributes only, no external reference, colours gray
  except where the request marks a value as a warning.

Independence: nothing here imports product code or the benchmark's encoders (`enc.py`); `zlib` is used only to
inflate (decompression is a fixed function) and for CRCs. Every reader is tested against hand-made right and wrong
reference artifacts (tests/acceptance/test_omni_readers.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
import re
import struct
import xml.etree.ElementTree as ET
import zlib

import numpy as np


class ReadError(ValueError):
    """The bytes could not be read as the artifact they claim to be (the reason says what was wrong)."""


# ---- SVG: parsing, transforms ---------------------------------------------------------------------------------------

_SVG_NS = "{http://www.w3.org/2000/svg}"
_XLINK = "{http://www.w3.org/1999/xlink}href"
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def parse_svg(data: bytes) -> ET.Element:
    head = data[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in data.lower():
        raise ReadError("SVG carries a DTD or an entity declaration")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise ReadError(f"not well-formed XML: {error}") from None
    if _local(root.tag) != "svg":
        raise ReadError(f"root element is <{_local(root.tag)}>, not <svg>")
    return root


def _matrix(transform: str | None) -> np.ndarray:
    m = np.eye(3)
    if not transform:
        return m
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", transform):
        v = [float(a) for a in re.findall(_NUM, args)]
        if name == "translate":
            t = np.array([[1, 0, v[0]], [0, 1, v[1] if len(v) > 1 else 0.0], [0, 0, 1]])
        elif name == "scale":
            sx = v[0]
            sy = v[1] if len(v) > 1 else sx
            t = np.array([[sx, 0, 0], [0, sy, 0], [0, 0, 1]])
        elif name == "matrix":
            a, b, c, d, e, f = v
            t = np.array([[a, c, e], [b, d, f], [0, 0, 1]])
        elif name == "rotate":
            r = math.radians(v[0])
            cx, cy = (v[1], v[2]) if len(v) == 3 else (0.0, 0.0)
            rot = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
            t = np.array([[1, 0, cx], [0, 1, cy], [0, 0, 1]]) @ rot @ np.array([[1, 0, -cx], [0, 1, -cy], [0, 0, 1]])
        else:
            raise ReadError(f"unknown transform {name}")
        m = m @ t
    return m


@dataclass
class _El:
    el: ET.Element
    tag: str
    classes: tuple[str, ...]
    ctm: np.ndarray                  # the element's own coordinate system (its transform included)
    ancestors: tuple["_El", ...]

    def point(self, x: float, y: float) -> tuple[float, float]:
        p = self.ctm @ np.array([x, y, 1.0])
        return float(p[0]), float(p[1])

    def attr(self, name: str, default: float = 0.0) -> float:
        value = self.el.get(name)
        if value is None:
            return default
        found = re.match(_NUM, value.strip())
        if not found:
            raise ReadError(f"attribute {name}={value!r} is not a number")
        return float(found.group(0))


def _walk(root: ET.Element) -> list[_El]:
    out: list[_El] = []

    def visit(el, ctm, ancestors):
        tag = _local(el.tag)
        if tag == "metadata":
            return                         # never read to judge the picture
        own = ctm @ _matrix(el.get("transform"))
        node = _El(el, tag, tuple((el.get("class") or "").split()), own, ancestors)
        out.append(node)
        for child in el:
            visit(child, own, ancestors + (node,))

    visit(root, np.eye(3), ())
    return out


def _text(el: ET.Element) -> str:
    return " ".join("".join(el.itertext()).split())


# ---- SVG charts: scales and marks -------------------------------------------------------------------------------------

def _parse_tick_value(label: str):
    s = label.strip().replace("−", "-")
    if re.fullmatch(_NUM, s):
        return "number", float(s)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?", s):
        text = s.replace(" ", "T").replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return "time", dt.timestamp()
    return "category", s


@dataclass
class Scale:
    kind: str                         # "linear" | "log" | "time" | "category"
    a: float = 0.0                    # value = a + b * pixel (log: log10(value) = a + b * pixel)
    b: float = 0.0
    categories: tuple[tuple[str, float], ...] = ()
    lo: float = 0.0                   # the tick domain (values)
    hi: float = 0.0

    def value(self, pixel: float):
        if self.kind == "category":
            return min(self.categories, key=lambda c: abs(c[1] - pixel))[0]
        v = self.a + self.b * pixel
        return 10 ** v if self.kind == "log" else v

    @property
    def span(self) -> float:
        return abs(self.hi - self.lo)


def fit_scale(ticks: list[tuple[float, str]]) -> Scale:
    """A scale from (pixel, label) ticks: at least two numeric (or date) ticks with distinct pixels, or categories."""
    if not ticks:
        raise ReadError("an axis without ticks")
    parsed = [(p, *_parse_tick_value(label)) for p, label in ticks]
    kinds = {k for _, k, _ in parsed}
    if kinds == {"category"} or len(kinds) > 1:
        return Scale("category", categories=tuple((str(v) if k != "category" else v, p) for p, k, v in parsed))
    if len(parsed) < 2:
        raise ReadError("a numeric axis needs at least two ticks")
    px = np.array([p for p, _, _ in parsed])
    vals = np.array([v for _, _, v in parsed])
    if np.ptp(px) == 0:
        raise ReadError("ticks at one position")
    kind = parsed[0][1]
    lo, hi = float(vals.min()), float(vals.max())

    def fit(y):
        b, a = np.polyfit(px, y, 1)
        resid = float(np.max(np.abs(a + b * px - y))) / max(float(np.ptp(y)), 1e-300)
        return float(a), float(b), resid

    a, b, resid = fit(vals)
    best = Scale("time" if kind == "time" else "linear", a, b, lo=lo, hi=hi)
    if kind == "number" and lo > 0 and len(parsed) >= 3:
        la, lb, lresid = fit(np.log10(vals))
        if lresid < resid:
            best, resid = Scale("log", la, lb, lo=lo, hi=hi), lresid
    if resid > 1e-3:
        raise ReadError(f"tick labels are not on a linear or log scale (residual {resid:.3g})")
    return best


@dataclass
class Mark:
    series: str
    kind: str                         # "point" | "bar" | "line"
    x: object = None                  # value (or category) of a point, the category of a bar
    y: object = None
    x_extent: tuple | None = None     # a bar's two x values (histograms)
    y_extent: tuple | None = None
    points: tuple = ()                # a line's (x, y) values


@dataclass
class ChartReading:
    x: Scale | None
    y: Scale | None
    marks: list[Mark] = field(default_factory=list)

    def series(self) -> dict[str, list[Mark]]:
        out: dict[str, list[Mark]] = {}
        for m in self.marks:
            out.setdefault(m.series, []).append(m)
        return out


def _axis_ticks(nodes: list[_El], axis: str) -> list[tuple[float, str]]:
    groups = [n for n in nodes if n.tag == "g" and "axis" in n.classes and axis in n.classes]
    if len(groups) != 1:
        raise ReadError(f"expected one <g class=\"axis {axis}\">, found {len(groups)}")
    group = groups[0]
    ticks = []
    for n in nodes:
        if n.tag == "g" and "tick" in n.classes and group in n.ancestors:
            x, y = n.point(0.0, 0.0)
            label = _text(n.el)
            if label:
                ticks.append((x if axis == "x" else y, label))
    return ticks


def _series_name(node: _El) -> str:
    for anc in reversed(node.ancestors):
        if anc.tag == "g" and "series" in anc.classes:
            return anc.el.get("data-name", "")
    return node.el.get("data-name", "") if "series" in node.classes else ""


def _points_attr(value: str) -> list[tuple[float, float]]:
    nums = [float(v) for v in re.findall(_NUM, value or "")]
    if len(nums) % 2:
        raise ReadError("odd number of coordinates in points")
    return list(zip(nums[0::2], nums[1::2]))


def read_chart(data: bytes) -> ChartReading:
    """Recover the marks of an SVG chart through its axes (the reading contract of DESIGN.md §6)."""
    nodes = _walk(parse_svg(data))
    plots = [n for n in nodes if n.tag == "g" and "plot" in n.classes]
    if len(plots) != 1:
        raise ReadError(f"expected one <g class=\"plot\">, found {len(plots)}")
    plot = plots[0]
    xs, ys = fit_scale(_axis_ticks(nodes, "x")), fit_scale(_axis_ticks(nodes, "y"))
    reading = ChartReading(xs, ys)
    for n in nodes:
        if plot not in n.ancestors:
            continue
        if n.tag == "circle" and "mark" in n.classes:
            px, py = n.point(n.attr("cx"), n.attr("cy"))
            reading.marks.append(Mark(_series_name(n), "point", xs.value(px), ys.value(py)))
        elif n.tag == "rect" and "mark" in n.classes:
            x0, y0 = n.attr("x"), n.attr("y")
            p0 = n.point(x0, y0)
            p1 = n.point(x0 + n.attr("width"), y0 + n.attr("height"))
            reading.marks.append(_bar(_series_name(n), p0, p1, xs, ys))
        elif n.tag == "polyline" and "series" in n.classes:
            pts = tuple((xs.value(px), ys.value(py)) for px, py in (n.point(x, y) for x, y in
                                                                    _points_attr(n.el.get("points", ""))))
            reading.marks.append(Mark(_series_name(n), "line", points=pts))
    return reading


def _bar_value(v0: float, v1: float, scale: Scale) -> float:
    base = 0.0 if scale.lo <= 0.0 <= scale.hi or scale.kind != "linear" else scale.lo
    if scale.kind == "log":
        base = scale.lo
    return v0 if abs(v0 - base) >= abs(v1 - base) else v1


def _bar(series: str, p0, p1, xs: Scale, ys: Scale) -> Mark:
    cx, cy = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
    if xs.kind == "category" and ys.kind != "category":
        v0, v1 = ys.value(p0[1]), ys.value(p1[1])
        return Mark(series, "bar", xs.value(cx), _bar_value(v0, v1, ys), y_extent=(min(v0, v1), max(v0, v1)))
    if ys.kind == "category" and xs.kind != "category":
        v0, v1 = xs.value(p0[0]), xs.value(p1[0])
        return Mark(series, "bar", _bar_value(v0, v1, xs), ys.value(cy), x_extent=(min(v0, v1), max(v0, v1)))
    xv = sorted((xs.value(p0[0]), xs.value(p1[0])))
    v0, v1 = ys.value(p0[1]), ys.value(p1[1])
    return Mark(series, "bar", (xv[0] + xv[1]) / 2, _bar_value(v0, v1, ys), x_extent=tuple(xv),
                y_extent=(min(v0, v1), max(v0, v1)))


# ---- carried information: recovered values against the source ------------------------------------------------------

def carried_points(source: list[tuple[float, float]], recovered: list[tuple[float, float]], tol_x: float,
                   tol_y: float, *, ordered: bool) -> int:
    """How many source points are recovered within tolerance. Ordered (a line): the recovered point nearest in x.
    Unordered (a scatter): a greedy one-to-one matching by scaled distance."""
    if ordered:
        hits = 0
        for sx, sy in source:
            if not recovered:
                break
            rx, ry = min(recovered, key=lambda p: abs(p[0] - sx))
            hits += abs(rx - sx) <= tol_x and abs(ry - sy) <= tol_y
        return hits
    left = list(recovered)
    hits = 0
    for sx, sy in source:
        if not left:
            break
        k = min(range(len(left)), key=lambda i: (abs(left[i][0] - sx) / max(tol_x, 1e-300)) ** 2
                + (abs(left[i][1] - sy) / max(tol_y, 1e-300)) ** 2)
        rx, ry = left[k]
        if abs(rx - sx) <= tol_x and abs(ry - sy) <= tol_y:
            hits += 1
            left.pop(k)
    return hits


# ---- SVG diagrams (node-link) ---------------------------------------------------------------------------------------

def _shape_center(node: _El, nodes: list[_El]) -> tuple[float, float]:
    for n in nodes:
        if node in n.ancestors or n is node:
            if n.tag in ("circle", "ellipse"):
                return n.point(n.attr("cx"), n.attr("cy"))
            if n.tag == "rect":
                return n.point(n.attr("x") + n.attr("width") / 2, n.attr("y") + n.attr("height") / 2)
    return node.point(0.0, 0.0)


def _path_ends(d: str) -> list[tuple[float, float]]:
    """End points of a simple path: absolute M/L/H/V/C/Q commands only (what a diagram's edge uses)."""
    tokens = re.findall(r"[MLHVCQZmlhvcqz]|" + _NUM, d or "")
    pts, cmd, i, cur = [], None, 0, (0.0, 0.0)
    while i < len(tokens):
        t = tokens[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in "Zz":
                continue
            continue
        if cmd in ("M", "L"):
            cur = (float(tokens[i]), float(tokens[i + 1]))
            i += 2
        elif cmd == "H":
            cur = (float(tokens[i]), cur[1])
            i += 1
        elif cmd == "V":
            cur = (cur[0], float(tokens[i]))
            i += 1
        elif cmd == "C":
            cur = (float(tokens[i + 4]), float(tokens[i + 5]))
            i += 6
        elif cmd == "Q":
            cur = (float(tokens[i + 2]), float(tokens[i + 3]))
            i += 4
        else:
            raise ReadError(f"path command {cmd!r} is outside what an edge reader reads")
        pts.append(cur)
    return pts


@dataclass
class DiagramReading:
    labels: list[str]
    edges: list[tuple[str, str]]
    positions: list[tuple[float, float]] = field(default_factory=list)


def read_diagram(data: bytes) -> DiagramReading:
    """Nodes are `<g class="node">` with their label as text and their position at the first circle, ellipse or
    rect inside (else the group's origin); edges are elements of class "edge" (line, polyline or path), each end
    attached to the nearest node. (DESIGN.md §6 fixes the contract for charts only; this is the benchmark's reading
    of a diagram, stated in docs/quality/OMNIMIND_BENCHMARK.md.)"""
    nodes = _walk(parse_svg(data))
    graph_nodes = [n for n in nodes if n.tag == "g" and "node" in n.classes]
    if not graph_nodes:
        raise ReadError("no <g class=\"node\">")
    centers = [(_text(n.el), _shape_center(n, nodes)) for n in graph_nodes]
    edges = []
    for n in nodes:
        if "edge" not in n.classes or n.tag not in ("line", "polyline", "path"):
            continue
        if n.tag == "line":
            ends = [n.point(n.attr("x1"), n.attr("y1")), n.point(n.attr("x2"), n.attr("y2"))]
        elif n.tag == "polyline":
            ends = [n.point(x, y) for x, y in _points_attr(n.el.get("points", ""))]
        else:
            ends = [n.point(x, y) for x, y in _path_ends(n.el.get("d", ""))]
        if len(ends) < 2:
            continue
        a, b = ends[0], ends[-1]
        near = [min(centers, key=lambda c: (c[1][0] - p[0]) ** 2 + (c[1][1] - p[1]) ** 2)[0] for p in (a, b)]
        edges.append((near[0], near[1]))
    return DiagramReading([label for label, _ in centers], edges, [p for _, p in centers])


# ---- SVG safety and palette -----------------------------------------------------------------------------------------

SVG_ALLOWED = frozenset({
    "svg", "g", "defs", "title", "desc", "metadata", "style", "path", "rect", "circle", "ellipse", "line", "polyline",
    "polygon", "text", "tspan", "clipPath", "mask", "pattern", "marker", "linearGradient", "radialGradient", "stop",
    "use", "symbol", "animate", "animateTransform", "animateMotion", "set"})


def svg_safety(data: bytes) -> list[str]:
    """Findings that make an SVG unsafe to show (empty when safe): a DTD, a script, an event attribute, a
    foreignObject or any element outside the allowed set, an external reference (href or url() not starting with
    '#'), javascript:, @import."""
    try:
        root = parse_svg(data)
    except ReadError as error:
        return [str(error)]
    findings = []
    for el in root.iter():
        tag = _local(el.tag)
        if tag not in SVG_ALLOWED:
            findings.append(f"element <{tag}> is not allowed")
        for name, value in el.attrib.items():
            local = _local(name)
            low = value.strip().lower()
            if local.lower().startswith("on"):
                findings.append(f"event attribute {local} on <{tag}>")
            if local == "href" and not low.startswith("#"):
                findings.append(f"external reference {value[:60]!r} on <{tag}>")
            if "javascript:" in low:
                findings.append(f"javascript: in {local}")
            for ref in re.findall(r"url\(\s*['\"]?([^)'\"]*)", low):
                if not ref.startswith("#"):
                    findings.append(f"external url({ref[:40]}) in {local}")
            if tag in ("animate", "set") and local == "attributeName" and low in ("href", "xlink:href"):
                findings.append("an animation that changes a reference")
        if tag == "style":
            css = (el.text or "").lower()
            if "@import" in css:
                findings.append("@import in a style sheet")
            for ref in re.findall(r"url\(\s*['\"]?([^)'\"]*)", css):
                if not ref.startswith("#"):
                    findings.append(f"external url({ref[:40]}) in a style sheet")
    return findings


_NAMED_GRAY = {"black", "white", "gray", "grey", "silver", "dimgray", "dimgrey", "darkgray", "darkgrey", "lightgray",
               "lightgrey", "gainsboro", "whitesmoke"}
_NEUTRAL = {"none", "currentcolor", "transparent", "inherit", "initial", "unset", "context-fill", "context-stroke"}
_COLOR_PROPS = ("fill", "stroke", "stop-color", "color", "flood-color", "lighting-color")


def color_is_gray(value: str) -> bool | None:
    """True for a gray (r = g = b) or neutral colour, False for a hue, None when it is not a colour this reads
    (a CSS variable: judged where it is defined)."""
    v = value.strip().lower()
    if not v or v in _NEUTRAL or v in _NAMED_GRAY or v.startswith("url(#"):
        return True
    if v.startswith("var("):
        return None
    m = re.fullmatch(r"#([0-9a-f]{3,8})", v)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            return h[0] == h[1] == h[2]
        if len(h) in (6, 8):
            return h[0:2] == h[2:4] == h[4:6]
        return False
    m = re.fullmatch(r"rgba?\(([^)]*)\)", v)
    if m:
        parts = [p.strip() for p in re.split(r"[,\s/]+", m.group(1)) if p.strip()]
        rgb = parts[:3]
        return len(rgb) == 3 and rgb[0] == rgb[1] == rgb[2]
    m = re.fullmatch(r"hsla?\(([^)]*)\)", v)
    if m:
        parts = [p.strip() for p in re.split(r"[,\s/]+", m.group(1)) if p.strip()]
        return len(parts) >= 2 and float(parts[1].rstrip("%")) == 0.0
    return False                               # a named hue ("red", "teal", ...)


def _style_colors(style: str) -> list[str]:
    out = []
    for decl in (style or "").split(";"):
        if ":" in decl:
            prop, value = decl.split(":", 1)
            if prop.strip().lower() in _COLOR_PROPS or prop.strip().startswith("--"):
                out.append(value.strip())
    return out


def svg_palette(data: bytes, *, warn_allowed: int = 0) -> list[str]:
    """Hue findings (empty when the palette rule holds). Hue is allowed only on elements inside or carrying a class
    "warn" or "danger", at most on `warn_allowed` marks, and in style rules whose selector names warn or danger."""
    root = parse_svg(data)
    nodes = _walk(root)
    findings, hued_marks = [], 0
    for n in nodes:
        flagged = any(c in ("warn", "danger") for a in (*n.ancestors, n) for c in a.classes)
        colors = [n.el.get(p) for p in _COLOR_PROPS if n.el.get(p)] + _style_colors(n.el.get("style", ""))
        hue = [c for c in colors if color_is_gray(c) is False]
        if hue:
            if not flagged:
                findings.append(f"hue {hue[0]!r} on <{n.tag}> not marked as a warning")
            elif "mark" in n.classes or "series" in n.classes:
                hued_marks += 1
        if n.tag == "style":
            for selector, body in re.findall(r"([^{}]+)\{([^}]*)\}", n.el.text or ""):
                for c in _style_colors(body):
                    if color_is_gray(c) is False and not re.search(r"warn|danger", selector):
                        findings.append(f"hue {c!r} in style rule {selector.strip()[:40]!r}")
    if hued_marks > warn_allowed:
        findings.append(f"{hued_marks} hued marks, {warn_allowed} values marked as warnings")
    return findings


# ---- WAV ----------------------------------------------------------------------------------------------------------

@dataclass
class Sound:
    samples: np.ndarray               # float64 (n, channels) in [-1, 1]
    rate: int
    bits: int

    @property
    def duration_s(self) -> float:
        return self.samples.shape[0] / self.rate


def read_wav(data: bytes) -> Sound:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ReadError("not a RIFF/WAVE file")
    pos, fmt, payload = 12, None, None
    while pos + 8 <= len(data):
        kind, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if len(body) < size:
            raise ReadError(f"chunk {kind!r} declares {size} bytes, {len(body)} present")
        if kind == b"fmt ":
            fmt = body
        elif kind == b"data":
            payload = body
        pos += 8 + size + (size & 1)
    if fmt is None or payload is None:
        raise ReadError("no fmt or no data chunk")
    tag, channels, rate, _, block, bits = struct.unpack("<HHIIHH", fmt[:16])
    if tag == 0xFFFE and len(fmt) >= 26:
        tag = struct.unpack("<H", fmt[24:26])[0]
    if tag != 1 or bits not in (8, 16, 24, 32) or channels < 1:
        raise ReadError(f"not integer PCM (format {tag}, {bits} bits)")
    width = bits // 8
    n = len(payload) // (width * channels)
    raw = payload[:n * width * channels]
    if bits == 8:
        ints = np.frombuffer(raw, np.uint8).astype(np.int64) - 128
    elif bits == 16:
        ints = np.frombuffer(raw, "<i2").astype(np.int64)
    elif bits == 24:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int64)
        ints = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        ints = np.where(ints >= 1 << 23, ints - (1 << 24), ints)
    else:
        ints = np.frombuffer(raw, "<i4").astype(np.int64)
    full = (1 << (bits - 1)) - 1
    return Sound((ints / full).reshape(n, channels), rate, bits)


def dominant_hz(x: np.ndarray, rate: int) -> float:
    """The strongest frequency of a (mono) stretch: Hann window, FFT, parabolic interpolation of the log peak."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 2:
        x = x.mean(axis=1)
    if len(x) < 16:
        raise ReadError("too few samples for a pitch")
    spec = np.abs(np.fft.rfft((x - x.mean()) * np.hanning(len(x))))
    k = int(np.argmax(spec[1:])) + 1
    if 1 <= k < len(spec) - 1 and spec[k - 1] > 0 and spec[k + 1] > 0 and spec[k] > 0:
        a, b, c = np.log(spec[k - 1]), np.log(spec[k]), np.log(spec[k + 1])
        denom = a - 2 * b + c
        delta = 0.5 * (a - c) / denom if denom else 0.0
    else:
        delta = 0.0
    return (k + delta) * rate / len(x)


def _trim(x: np.ndarray) -> np.ndarray:
    mono = np.abs(x.mean(axis=1) if x.ndim == 2 else x)
    peak = float(mono.max()) if len(mono) else 0.0
    if peak == 0:
        raise ReadError("silence")
    loud = np.nonzero(mono > 0.01 * peak)[0]
    return x[loud[0]:loud[-1] + 1]


def tone_pitches(sound: Sound, n: int) -> list[float]:
    """The pitch of each of `n` equal-length tones (leading and trailing silence trimmed; the central 60% of each
    tone analysed)."""
    x = _trim(sound.samples)
    seg = len(x) / n
    out = []
    for i in range(n):
        a, b = int(i * seg + 0.2 * seg), int(i * seg + 0.8 * seg)
        out.append(dominant_hz(x[a:b], sound.rate))
    return out


def onsets(sound: Sound, frame_s: float = 0.01) -> int:
    """Sounds that start after silence: 10 ms frames, a rise above 10% of the loudest frame's RMS after at least
    30 ms below 2% of it."""
    mono = sound.samples.mean(axis=1)
    size = max(1, int(sound.rate * frame_s))
    n = len(mono) // size
    if n == 0:
        return 0
    rms = np.sqrt((mono[:n * size].reshape(n, size) ** 2).mean(axis=1))
    top = float(rms.max())
    if top == 0:
        return 0
    count, quiet = 0, 3
    for r in rms:
        if r < 0.02 * top:
            quiet += 1
        elif r > 0.1 * top:
            if quiet >= 3:
                count += 1
            quiet = 0
    return count


# ---- meshes ------------------------------------------------------------------------------------------------------------

@dataclass
class MeshReading:
    vertices: np.ndarray              # (n, 3) float64, welded by exact coordinates
    triangles: np.ndarray             # (m, 3) int64
    stored_normals: np.ndarray | None = None    # STL facet normals as written

    @property
    def volume(self) -> float:
        v = self.vertices[self.triangles]
        return float(np.einsum("ij,ij->i", v[:, 0], np.cross(v[:, 1], v[:, 2])).sum() / 6.0)

    @property
    def area(self) -> float:
        v = self.vertices[self.triangles]
        return float(np.linalg.norm(np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0]), axis=1).sum() / 2.0)

    @property
    def bounds(self) -> tuple[list[float], list[float]]:
        used = self.vertices[np.unique(self.triangles)]
        return used.min(axis=0).tolist(), used.max(axis=0).tolist()

    def edge_report(self) -> tuple[bool, bool]:
        """(watertight, consistently oriented): every undirected edge shared by exactly two triangles, and every
        directed edge used once (neighbours traverse a shared edge in opposite directions)."""
        directed: dict[tuple[int, int], int] = {}
        for a, b, c in self.triangles.tolist():
            for e in ((a, b), (b, c), (c, a)):
                directed[e] = directed.get(e, 0) + 1
        undirected: dict[tuple[int, int], int] = {}
        for (a, b), k in directed.items():
            key = (a, b) if a < b else (b, a)
            undirected[key] = undirected.get(key, 0) + k
        watertight = all(k == 2 for k in undirected.values())
        oriented = all(k == 1 for k in directed.values())
        return watertight, oriented

    def normals_agree(self) -> bool | None:
        """Whether every stored STL normal points the way the winding does (None for formats without normals)."""
        if self.stored_normals is None:
            return None
        v = self.vertices[self.triangles]
        computed = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
        return bool(np.all(np.einsum("ij,ij->i", computed, self.stored_normals) > 0))


def _weld(corners: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    unique, inverse = np.unique(corners, axis=0, return_inverse=True)
    return unique, inverse.reshape(-1, 3).astype(np.int64)


def read_mesh(data: bytes) -> MeshReading:
    if len(data) >= 84:
        count = struct.unpack("<I", data[80:84])[0]
        if len(data) == 84 + 50 * count:
            rec = np.frombuffer(data[84:], dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]))
            verts, tris = _weld(rec["v"].reshape(-1, 3).astype(np.float64))
            return MeshReading(verts, tris, rec["n"].astype(np.float64))
    text = data.decode("ascii", errors="strict") if data.isascii() else None
    if text is None:
        raise ReadError("binary data that is not a binary STL")
    if text.lstrip().startswith("solid"):
        normals = [[float(v) for v in m] for m in re.findall(rf"facet\s+normal\s+({_NUM})\s+({_NUM})\s+({_NUM})", text)]
        corners = [[float(v) for v in m] for m in re.findall(rf"vertex\s+({_NUM})\s+({_NUM})\s+({_NUM})", text)]
        if not corners or len(corners) % 3 or len(normals) * 3 != len(corners):
            raise ReadError("ASCII STL with an incomplete facet")
        verts, tris = _weld(np.array(corners))
        return MeshReading(verts, tris, np.array(normals))
    if text.startswith("ply"):
        return _read_ply_ascii(text)
    verts, faces = [], []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "v":
            verts.append([float(p) for p in parts[1:4]])
        elif parts[0] == "f":
            idx = []
            for p in parts[1:]:
                i = int(p.split("/")[0])
                idx.append(i - 1 if i > 0 else len(verts) + i)
            faces += [[idx[0], idx[k], idx[k + 1]] for k in range(1, len(idx) - 1)]
    if not verts or not faces:
        raise ReadError("no vertices or no faces")
    return MeshReading(np.array(verts, dtype=np.float64), np.array(faces, dtype=np.int64))


def _read_ply_ascii(text: str) -> MeshReading:
    head, _, body = text.partition("end_header")
    if "format ascii" not in head:
        raise ReadError("only ASCII PLY is read here")
    nv = int(re.search(r"element vertex (\d+)", head).group(1))
    lines = [ln.split() for ln in body.strip().splitlines()]
    verts = np.array([[float(v) for v in ln[:3]] for ln in lines[:nv]])
    faces = []
    for ln in lines[nv:]:
        idx = [int(v) for v in ln[1:1 + int(ln[0])]]
        faces += [[idx[0], idx[k], idx[k + 1]] for k in range(1, len(idx) - 1)]
    return MeshReading(verts, np.array(faces, dtype=np.int64))


# ---- PNG and APNG --------------------------------------------------------------------------------------------------------

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


@dataclass
class PNGReading:
    width: int
    height: int
    bits: int
    color_type: int
    frames: list[np.ndarray]          # composited full canvases; pixels (h, w, c) with palette expanded
    delays_s: list[float]
    plays: int
    animated: bool


def _unfilter(raw: bytes, width: int, height: int, channels: int, bits: int) -> np.ndarray:
    bpp = max(1, channels * bits // 8)
    stride = (width * channels * bits + 7) // 8
    if len(raw) < height * (stride + 1):
        raise ReadError("image data shorter than the declared size")
    out = bytearray(height * stride)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        ftype = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += stride + 1
        if ftype > 4:
            raise ReadError(f"filter type {ftype}")
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            if ftype == 1:
                line[x] = (line[x] + a) & 0xFF
            elif ftype == 2:
                line[x] = (line[x] + b) & 0xFF
            elif ftype == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 0xFF
            elif ftype == 4:
                c = prev[x - bpp] if x >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
        out[y * stride:(y + 1) * stride] = line
        prev = line
    rows = np.frombuffer(bytes(out), np.uint8).reshape(height, stride)
    if bits == 16:
        samples = rows.view(">u2").astype(np.uint16)
    elif bits == 8:
        samples = rows
    else:
        per = 8 // bits
        shifts = np.array([8 - bits * (k + 1) for k in range(per)], dtype=np.uint8)
        samples = ((rows[:, :, None] >> shifts) & ((1 << bits) - 1)).reshape(height, -1)
    return samples[:, :width * channels].reshape(height, width, channels)


def _decode_image(stream: bytes, width: int, height: int, color_type: int, bits: int, interlace: int,
                  limit: int) -> np.ndarray:
    channels = _CHANNELS[color_type]
    try:
        d = zlib.decompressobj()
        raw = d.decompress(stream, limit)
    except zlib.error as error:
        raise ReadError(f"bad zlib stream: {error}") from None
    if not interlace:
        return _unfilter(raw, width, height, channels, bits)
    out = np.zeros((height, width, channels), dtype=np.uint16 if bits == 16 else np.uint8)
    pos = 0
    for x0, y0, dx, dy in _ADAM7:
        w, h = (width - x0 + dx - 1) // dx, (height - y0 + dy - 1) // dy
        if w <= 0 or h <= 0:
            continue
        stride = (w * channels * bits + 7) // 8
        size = h * (stride + 1)
        out[y0::dy, x0::dx] = _unfilter(raw[pos:pos + size], w, h, channels, bits)
        pos += size
    return out


def read_png(data: bytes, *, max_pixels: int = 1 << 24) -> PNGReading:
    if data[:8] != _PNG_SIG:
        raise ReadError("no PNG signature")
    pos, chunks = 8, []
    while pos + 12 <= len(data):
        size = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + size]
        crc = data[pos + 8 + size:pos + 12 + size]
        if len(body) < size or len(crc) < 4:
            raise ReadError(f"chunk {kind!r} truncated")
        if struct.unpack(">I", crc)[0] != zlib.crc32(kind + body) & 0xFFFFFFFF:
            raise ReadError(f"chunk {kind!r} fails its CRC")
        chunks.append((kind, body))
        pos += 12 + size
        if kind == b"IEND":
            break
    if not chunks or chunks[0][0] != b"IHDR":
        raise ReadError("IHDR is not first")
    width, height, bits, color_type, _, _, interlace = struct.unpack(">IIBBBBB", chunks[0][1])
    if width * height > max_pixels:
        raise ReadError(f"{width} x {height} is beyond the reader's bound")
    palette = trns = None
    actl, frames_meta, idat, current = None, [], bytearray(), None
    for kind, body in chunks[1:]:
        if kind == b"PLTE":
            palette = np.frombuffer(body, np.uint8).reshape(-1, 3)
        elif kind == b"tRNS":
            trns = body
        elif kind == b"acTL":
            actl = struct.unpack(">II", body)
        elif kind == b"fcTL":
            seq, w, h, xo, yo, dn, dd, dispose, blend = struct.unpack(">IIIIIHHBB", body)
            current = {"w": w, "h": h, "x": xo, "y": yo, "delay": dn / (dd or 100), "dispose": dispose,
                       "blend": blend, "data": bytearray(), "default": False}
            frames_meta.append(current)
        elif kind == b"IDAT":
            idat += body
            if current is not None and frames_meta and frames_meta[-1] is current and not current["data"]:
                current["default"] = True
        elif kind == b"fdAT":
            if current is None:
                raise ReadError("fdAT before fcTL")
            current["data"] += body[4:]
    limit = (width * _CHANNELS[color_type] * bits // 8 + 2) * height * 2 + 1024

    def expand(px: np.ndarray) -> np.ndarray:
        if color_type != 3:
            return px
        if palette is None:
            raise ReadError("palette image without PLTE")
        idx = px[:, :, 0].astype(np.int64)
        if idx.max(initial=0) >= len(palette):
            raise ReadError("palette index out of range")
        rgb = palette[idx]
        if trns is None:
            return rgb
        alpha = np.full(len(palette), 255, np.uint8)
        alpha[:len(trns)] = np.frombuffer(trns, np.uint8)[:len(palette)]
        return np.concatenate([rgb, alpha[idx][:, :, None]], axis=2)

    still = expand(_decode_image(bytes(idat), width, height, color_type, bits, interlace, limit))
    if actl is None:
        return PNGReading(width, height, bits, color_type, [still], [0.0], 0, False)
    canvas = np.zeros_like(still)
    out_frames, delays = [], []
    for meta in frames_meta:
        if meta["default"]:
            px = still
        else:
            px = expand(_decode_image(bytes(meta["data"]), meta["w"], meta["h"], color_type, bits, 0, limit))
        before = canvas.copy()
        region = canvas[meta["y"]:meta["y"] + meta["h"], meta["x"]:meta["x"] + meta["w"]]
        if meta["blend"] == 1 and px.shape[2] in (2, 4):
            alpha = px[:, :, -1:].astype(np.float64) / 255.0
            region[:] = np.rint(px * alpha + region * (1 - alpha)).astype(region.dtype)
        else:
            region[:] = px
        out_frames.append(canvas.copy())
        delays.append(meta["delay"])
        if meta["dispose"] == 1:
            region[:] = 0
        elif meta["dispose"] == 2:
            canvas = before
    if len(out_frames) != actl[0]:
        raise ReadError(f"acTL declares {actl[0]} frames, {len(out_frames)} present")
    return PNGReading(width, height, bits, color_type, out_frames, delays, actl[1], True)


def png_is_gray(reading: PNGReading) -> bool:
    for frame in reading.frames:
        if frame.shape[2] >= 3 and not (np.array_equal(frame[:, :, 0], frame[:, :, 1])
                                        and np.array_equal(frame[:, :, 1], frame[:, :, 2])):
            return False
    return True
