"""SVG read into a `Drawing`: shapes with their geometry resolved through nested transforms, text, titles. No
rendering, no style sheet applied, nothing loaded.

The XML parser refuses any DOCTYPE or entity declaration. Coordinates are in the drawing's user units (the viewBox
when there is one; the drawing's width and height are then the viewBox's). Shapes: rect (4 corners), circle and
ellipse (centre and the 4 extreme points), line, polyline, polygon, path (move/line/curve/arc commands; Bézier
curves and arcs flattened to 8 points per segment), text and tspan (anchor point and words), image (corners; an
embedded `data:` image is not decoded), `use` of an element of the same file (copied with its offset, at most
USE_DEPTH levels, at most
USE_COPIES copies in all). Each shape keeps a few presentation attributes (id, class, fill, stroke,
stroke-width, opacity, font-size, text-anchor), cut to 120 characters.

Refused content, recorded in the notes and never kept: script, foreignObject, iframe, embedded HTML, event
attributes (on...), references to anything outside the file (an image, a `use` or a link's address); a style
sheet is noted and not applied. Text, titles and descriptions are the artifact's own words (untrusted).
"""
from __future__ import annotations

import math
import re

import numpy as np

from ..safe_b2 import Deadline, count, local, parse_xml, screen_words
from ..types import Decoded, Drawing, Malformed, Refused, Shape
from . import Adapter

KEEP = ("id", "class", "fill", "stroke", "stroke-width", "opacity", "font-size", "text-anchor")
HIDDEN = {"defs", "symbol", "clipPath", "mask", "pattern", "marker", "linearGradient", "radialGradient", "filter",
          "metadata", "style", "script", "foreignObject", "iframe", "title", "desc"}
REFUSED = {"script", "foreignObject", "iframe", "embed", "object", "audio", "video", "handler", "listener"}
UNITS = {"": 1.0, "px": 1.0, "pt": 4 / 3, "pc": 16.0, "mm": 96 / 25.4, "cm": 96 / 2.54, "in": 96.0}
USE_DEPTH = 8
USE_COPIES = 100_000         # instantiations of `use` per drawing
CURVE_STEPS = 8
_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_CMD = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])|([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")
XLINK = "{http://www.w3.org/1999/xlink}href"


def numbers(text: str | None) -> list[float]:
    return [float(x) for x in _NUM.findall(text or "")]


def length(text: str | None) -> float | None:
    if not text:
        return None
    m = re.fullmatch(r"\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([a-z%]*)\s*", text)
    if not m or m.group(2) not in UNITS:
        return None
    v = float(m.group(1)) * UNITS[m.group(2)]
    return v if math.isfinite(v) else None


def parse_transform(text: str | None) -> np.ndarray:
    """The 3x3 matrix of an SVG transform list (matrix, translate, scale, rotate, skewX, skewY)."""
    m = np.eye(3)
    for name, args in re.findall(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)", text or ""):
        a = numbers(args)
        t = np.eye(3)
        if name == "matrix" and len(a) == 6:
            t = np.array([[a[0], a[2], a[4]], [a[1], a[3], a[5]], [0, 0, 1]])
        elif name == "translate" and a:
            t[0, 2], t[1, 2] = a[0], a[1] if len(a) > 1 else 0.0
        elif name == "scale" and a:
            t[0, 0], t[1, 1] = a[0], a[1] if len(a) > 1 else a[0]
        elif name == "rotate" and a:
            r = math.radians(a[0])
            rot = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
            if len(a) == 3:
                pre, post = np.eye(3), np.eye(3)
                pre[:2, 2], post[:2, 2] = (a[1], a[2]), (-a[1], -a[2])
                rot = pre @ rot @ post
            t = rot
        elif name == "skewX" and a:
            t[0, 1] = math.tan(math.radians(a[0]))
        elif name == "skewY" and a:
            t[1, 0] = math.tan(math.radians(a[0]))
        m = m @ t
    return m


def _apply(m: np.ndarray, pts) -> tuple[tuple[float, float], ...]:
    if not pts:
        return ()
    a = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    out = a @ m[:2, :2].T + m[:2, 2]
    return tuple((float(x), float(y)) for x, y in out)


def _bezier(p0, ctrl, p1, steps=CURVE_STEPS):
    pts = [p0, *ctrl, p1]
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        if len(pts) == 3:
            x = (1 - t) ** 2 * pts[0][0] + 2 * (1 - t) * t * pts[1][0] + t * t * pts[2][0]
            y = (1 - t) ** 2 * pts[0][1] + 2 * (1 - t) * t * pts[1][1] + t * t * pts[2][1]
        else:
            x = ((1 - t) ** 3 * pts[0][0] + 3 * (1 - t) ** 2 * t * pts[1][0] + 3 * (1 - t) * t * t * pts[2][0]
                 + t ** 3 * pts[3][0])
            y = ((1 - t) ** 3 * pts[0][1] + 3 * (1 - t) ** 2 * t * pts[1][1] + 3 * (1 - t) * t * t * pts[2][1]
                 + t ** 3 * pts[3][1])
        out.append((x, y))
    return out


def _arc(p0, rx, ry, phi, large, sweep, p1, steps=CURVE_STEPS):
    """SVG elliptical arc (endpoint parameterisation, SVG 1.1 F.6.5) flattened to `steps` points."""
    if p0 == p1:
        return []
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return [p1]
    c, s = math.cos(math.radians(phi)), math.sin(math.radians(phi))
    dx, dy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1, y1 = c * dx + s * dy, -s * dx + c * dy
    lam = x1 * x1 / (rx * rx) + y1 * y1 / (ry * ry)
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    den = rx * rx * y1 * y1 + ry * ry * x1 * x1
    k = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        k = -k
    cx1, cy1 = k * rx * y1 / ry, -k * ry * x1 / rx
    cx = c * cx1 - s * cy1 + (p0[0] + p1[0]) / 2
    cy = s * cx1 + c * cy1 + (p0[1] + p1[1]) / 2

    def angle(ux, uy, vx, vy):
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
    t1 = angle(1, 0, (x1 - cx1) / rx, (y1 - cy1) / ry)
    dt = angle((x1 - cx1) / rx, (y1 - cy1) / ry, (-x1 - cx1) / rx, (-y1 - cy1) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    out = []
    for i in range(1, steps + 1):
        t = t1 + dt * i / steps
        out.append((cx + rx * math.cos(t) * c - ry * math.sin(t) * s, cy + rx * math.cos(t) * s + ry * math.sin(t) * c))
    out[-1] = p1
    return out


def path_points(d: str, limits) -> tuple[list[tuple[float, float]], int]:
    """Vertices of a path's data (curves and arcs flattened), and the number of subpaths."""
    tokens = _CMD.findall(d or "")
    pts: list[tuple[float, float]] = []
    cur = start = (0.0, 0.0)
    last_ctrl = None
    cmd = None
    subpaths = 0
    i = 0

    def nums(n):
        nonlocal i
        vals = []
        while len(vals) < n:
            if i >= len(tokens) or tokens[i][0]:
                raise Malformed("an SVG path command lacks its numbers")
            vals.append(float(tokens[i][1]))
            i += 1
        return vals
    while i < len(tokens):
        if tokens[i][0]:
            cmd = tokens[i][0]
            i += 1
        elif cmd is None:
            raise Malformed("an SVG path does not start with a command")
        rel = cmd.islower()
        c = cmd.upper()
        ox, oy = cur if rel else (0.0, 0.0)
        if c == "Z":
            cur = start
            pts.append(cur)
            last_ctrl = None
            continue
        if c == "M":
            x, y = nums(2)
            cur = start = (ox + x, oy + y)
            pts.append(cur)
            subpaths += 1
            cmd = "l" if rel else "L"
        elif c == "L":
            x, y = nums(2)
            cur = (ox + x, oy + y)
            pts.append(cur)
        elif c == "H":
            (x,) = nums(1)
            cur = (ox + x if rel else x, cur[1])
            pts.append(cur)
        elif c == "V":
            (y,) = nums(1)
            cur = (cur[0], oy + y if rel else y)
            pts.append(cur)
        elif c in "CS":
            if c == "C":
                a = nums(6)
                c1, c2, end = (ox + a[0], oy + a[1]), (ox + a[2], oy + a[3]), (ox + a[4], oy + a[5])
            else:
                a = nums(4)
                c1 = (2 * cur[0] - last_ctrl[0], 2 * cur[1] - last_ctrl[1]) if last_ctrl else cur
                c2, end = (ox + a[0], oy + a[1]), (ox + a[2], oy + a[3])
            pts += _bezier(cur, [c1, c2], end)
            last_ctrl, cur = c2, end
            count(len(pts), limits, "SVG path points")
            continue
        elif c in "QT":
            if c == "Q":
                a = nums(4)
                q, end = (ox + a[0], oy + a[1]), (ox + a[2], oy + a[3])
            else:
                a = nums(2)
                q = (2 * cur[0] - last_ctrl[0], 2 * cur[1] - last_ctrl[1]) if last_ctrl else cur
                end = (ox + a[0], oy + a[1])
            pts += _bezier(cur, [q], end)
            last_ctrl, cur = q, end
            continue
        elif c == "A":
            rx, ry, phi, large, sweep, x, y = nums(7)
            end = (ox + x, oy + y)
            pts += _arc(cur, rx, ry, phi, bool(large), bool(sweep), end)
            cur = end
        last_ctrl = None
        count(len(pts), limits, "SVG path points")
    return pts, subpaths


def _external(href: str | None) -> bool:
    return bool(href) and not href.startswith("#") and not href.startswith("data:")


def decode_svg(data: bytes, detected, limits) -> Decoded:
    deadline = Deadline(limits)
    root = parse_xml(data, limits, "the SVG document")
    if local(root.tag) != "svg":
        raise Malformed("the root element is not svg")
    by_id = {el.get("id"): el for el in root.iter() if el.get("id")}
    shapes: list[Shape] = []
    refused: dict[str, int] = {}
    notes: list[str] = []
    n_points = [0]
    visits = [0]
    uses = [0]

    def refuse(what):
        refused[what] = refused.get(what, 0) + 1

    def keep(el) -> dict[str, str]:
        return {k: str(el.get(k))[:120] for k in KEEP if el.get(k) is not None}

    def add(kind, pts, el, m, text=None, extra=None):
        p = _apply(m, pts)
        n_points[0] += len(p)
        count(n_points[0], limits, "SVG points")
        count(len(shapes) + 1, limits, "SVG shapes")
        attrs = keep(el)
        if extra:
            attrs.update(extra)
        shapes.append(Shape(kind, p, text, attrs))

    def num(el, name, default=0.0):
        v = length(el.get(name))
        return default if v is None else v

    def walk(el, m, depth, use_depth):
        visits[0] += 1
        if visits[0] % 1024 == 0:
            deadline.check("reading SVG elements")
            count(visits[0], limits, "SVG element visits (including use copies)")
        tag = local(el.tag)
        for name in el.attrib:
            if local(name).lower().startswith("on"):
                refuse("event attribute")
        if tag in REFUSED:
            refuse(tag)
            return
        if tag == "style":
            notes.append("a style sheet is present; it is not applied")
            return
        if tag in ("title", "desc"):
            text = "".join(el.itertext()).strip()
            if text:
                shapes.append(Shape(tag, (), text, {}))
            return
        if tag in HIDDEN:
            return
        m = m @ parse_transform(el.get("transform"))
        href = el.get("href") or el.get(XLINK)
        if tag == "rect":
            x, y, w, h = num(el, "x"), num(el, "y"), num(el, "width"), num(el, "height")
            add("rect", [(x, y), (x + w, y), (x + w, y + h), (x, y + h)], el, m)
        elif tag in ("circle", "ellipse"):
            cx, cy = num(el, "cx"), num(el, "cy")
            rx = num(el, "r") if tag == "circle" else num(el, "rx")
            ry = num(el, "r") if tag == "circle" else num(el, "ry")
            add(tag, [(cx, cy), (cx + rx, cy), (cx, cy + ry), (cx - rx, cy), (cx, cy - ry)], el, m)
        elif tag == "line":
            add("line", [(num(el, "x1"), num(el, "y1")), (num(el, "x2"), num(el, "y2"))], el, m)
        elif tag in ("polyline", "polygon"):
            v = numbers(el.get("points"))
            add(tag, list(zip(v[0::2], v[1::2])), el, m)
        elif tag == "path":
            pts, sub = path_points(el.get("d", ""), limits)
            add("path", pts, el, m, extra={"subpaths": str(sub)})
        elif tag in ("text", "tspan", "textPath"):
            direct = (el.text or "").strip()
            x = numbers(el.get("x"))[:1] or [0.0]
            y = numbers(el.get("y"))[:1] or [0.0]
            if direct:
                add("text", [(x[0], y[0])], el, m, text=direct)
            for child in el:
                walk(child, m, depth + 1, use_depth)
                if child.tail and child.tail.strip():
                    add("text", [(x[0], y[0])], el, m, text=child.tail.strip())
            return
        elif tag == "image":
            x, y, w, h = num(el, "x"), num(el, "y"), num(el, "width"), num(el, "height")
            extra = {}
            if href and href.startswith("data:"):
                extra["source"] = "embedded image (not decoded)"
            elif href:
                refuse("external image reference")
                extra["source"] = "external reference (refused, not loaded)"
            add("image", [(x, y), (x + w, y), (x + w, y + h), (x, y + h)], el, m, extra=extra)
            return
        elif tag == "use":
            if _external(href):
                refuse("external use reference")
                return
            target = by_id.get((href or "")[1:])
            if target is None or use_depth >= USE_DEPTH:
                if target is not None:
                    notes.append(f"a use chain deeper than {USE_DEPTH} was cut")
                return
            uses[0] += 1
            if uses[0] > USE_COPIES:
                raise Refused(f"the drawing instantiates more than {USE_COPIES} use copies", rule="svg_use",
                              reason="too_large")
            t = np.eye(3)
            t[0, 2], t[1, 2] = num(el, "x"), num(el, "y")
            inner = target if local(target.tag) != "symbol" else None
            if inner is None:
                for child in target:
                    walk(child, m @ t, depth + 1, use_depth + 1)
            else:
                walk(inner, m @ t, depth + 1, use_depth + 1)
            return
        elif tag == "a":
            if _external(href):
                refuse("link address (not followed)")
        if depth > 256:
            return
        for child in el:
            walk(child, m, depth + 1, use_depth)

    vb = numbers(root.get("viewBox"))
    width, height = length(root.get("width")), length(root.get("height"))
    if len(vb) == 4 and vb[2] >= 0 and vb[3] >= 0:
        width, height = vb[2], vb[3]
        base = np.eye(3)
        notes.append("coordinates are in the drawing's user units (its viewBox)")
    else:
        base = np.eye(3)
        if width is None or height is None:
            notes.append("the drawing states no usable width and height; 0 is used")
    for child in root:
        walk(child, base, 1, 0)
    for name in root.attrib:
        if local(name).lower().startswith("on"):
            refuse("event attribute")
    if refused:
        notes.append("refused and not kept: " + ", ".join(f"{n} x {k}" for k, n in sorted(refused.items())))
    drawing = Drawing(float(width or 0.0), float(height or 0.0), tuple(shapes))
    notes.extend(screen_words(drawing.text()))
    return Decoded(detected, drawing, tuple(dict.fromkeys(notes)), False)


class SVGAdapter(Adapter):
    name = "b2-svg"
    formats = ("svg",)
    families = ("drawing",)

    def decode(self, data, detected, limits):
        return decode_svg(data, detected, limits)


ADAPTERS = (SVGAdapter(),)

__all__ = ["ADAPTERS", "SVGAdapter", "decode_svg", "length", "parse_transform", "path_points"]
