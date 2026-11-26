"""An independent reader of SVG pictures: a bounded parser, geometry, and the reading contract (DESIGN §6).

Written from the SVG format and the reading contract only: it never imports the generators (`artifacts/make`) or
reads their constants (a test enforces the import rule). It recovers values the way a person reads a chart: each
axis's scale is fitted from its tick positions and tick labels (linear, log10, ISO 8601 time, or categories by
position), and every mark's geometry is mapped back through those scales. The `<metadata>` table is never read.

Tolerance (stated): a recovered value matches a source value when it lies within 0.5 % of its axis's tick span
(log axes: of the span in decades; heatmap values: 1 % of the ramp's span).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from xml.parsers import expat

from ..types import Malformed, Refused

SVG_NS = "http://www.w3.org/2000/svg"
MAX_BYTES = 16 << 20
MAX_NODES = 250_000
MAX_DEPTH = 64
TOLERANCE = 0.005
HEAT_TOLERANCE = 0.01


# --- parser -----------------------------------------------------------------------------------------------------

class Elem:
    __slots__ = ("tag", "attrs", "children", "text", "parent", "ns")

    def __init__(self, tag, attrs, parent, ns):
        self.tag, self.attrs, self.parent, self.ns = tag, attrs, parent, ns
        self.children: list[Elem] = []
        self.text = ""

    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def has(self, *names) -> bool:
        return set(names) <= self.classes()

    def iter(self):
        stack = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))

    def find(self, tag=None, *classes):
        return [n for n in self.iter() if (tag is None or n.tag == tag) and n.has(*classes)]

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def label(self) -> str:
        """Direct text of a <text> element (not its <title> child)."""
        return self.text.strip()

    def full_label(self) -> str:
        """The label a reader gets: the <title> child's text when the shown text was cut, else the shown text."""
        for c in self.children:
            if c.tag == "title" and c.text.strip():
                return c.text.strip()
        return self.label()


@dataclass
class Parsed:
    root: Elem
    n_nodes: int
    comments: int
    other_namespaces: tuple[str, ...]


def parse(data: bytes, *, max_bytes: int = MAX_BYTES, max_nodes: int = MAX_NODES, max_depth: int = MAX_DEPTH) -> Parsed:
    """Parse untrusted SVG bytes. Refuses DOCTYPE, entity declarations, external entities, processing instructions;
    bounds bytes, nodes and depth while parsing. Raises Refused or Malformed with a plain reason."""
    if len(data) > max_bytes:
        raise Refused(f"the picture is {len(data)} bytes, over the {max_bytes} a reader accepts", rule="max_bytes",
                      reason="too_large")
    if not data.strip():
        raise Malformed("the picture is empty")
    parser = expat.ParserCreate(namespace_separator=" ")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    state = {"root": None, "cur": None, "n": 0, "depth": 0, "comments": 0}
    other_ns: set[str] = set()

    class _Stop(Exception):
        def __init__(self, error):
            self.error = error

    def refuse(rule, words):
        raise _Stop(Refused(words, rule=rule))

    def split(name):
        if " " in name:
            ns, local = name.split(" ", 1)
            return ns, local
        return "", name

    def start(name, attrs):
        state["n"] += 1
        state["depth"] += 1
        if state["n"] > max_nodes:
            refuse("max_elements", f"the picture has more than {max_nodes} elements")
        if state["depth"] > max_depth:
            refuse("max_depth", f"the picture nests deeper than {max_depth}")
        ns, local = split(name)
        if ns != SVG_NS:
            other_ns.add(ns or "(none)")
        clean = {}
        for k, v in attrs.items():
            ans, alocal = split(k)
            if ans:
                other_ns.add(ans)
                clean["{" + ans + "}" + alocal] = v
            else:
                clean[alocal] = v
        node = Elem(local if ns == SVG_NS else "{" + ns + "}" + local, clean, state["cur"], ns)
        if state["cur"] is None:
            if state["root"] is not None:
                refuse("structure", "more than one root element")
            state["root"] = node
        else:
            state["cur"].children.append(node)
        state["cur"] = node

    def end(name):
        state["depth"] -= 1
        state["cur"] = state["cur"].parent

    def chars(data_):
        if state["cur"] is not None:
            state["cur"].text += data_

    def comment(_data):
        state["comments"] += 1

    def no_decl(*_a):
        refuse("xml_dtd", "a DOCTYPE or entity declaration is not allowed in a made picture")

    def no_pi(target, _data):
        refuse("processing_instruction", "a processing instruction is not allowed in a made picture")

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    parser.CommentHandler = comment
    parser.StartDoctypeDeclHandler = no_decl
    parser.EntityDeclHandler = no_decl
    parser.UnparsedEntityDeclHandler = no_decl
    parser.ExternalEntityRefHandler = lambda *a: no_decl()
    parser.ProcessingInstructionHandler = no_pi
    parser.buffer_text = True
    try:
        parser.Parse(bytes(data), True)
    except _Stop as stop:
        raise stop.error from None
    except expat.ExpatError as error:
        raise Malformed(f"the picture is not well-formed XML ({expat.ErrorString(error.code)})") from None
    if state["root"] is None:
        raise Malformed("the picture has no root element")
    return Parsed(state["root"], state["n"], state["comments"], tuple(sorted(other_ns)))


# --- geometry ---------------------------------------------------------------------------------------------------

Matrix = tuple[float, float, float, float, float, float]   # a b c d e f: x' = a x + c y + e, y' = b x + d y + f
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_TRANSFORM = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")
_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def mul(m: Matrix, n: Matrix) -> Matrix:
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = n
    return (a * a2 + c * b2, b * a2 + d * b2, a * c2 + c * d2, b * c2 + d * d2, a * e2 + c * f2 + e, b * e2 + d * f2 + f)


def parse_transform(text: str | None) -> Matrix:
    m = IDENTITY
    if not text:
        return m
    for name, args in _TRANSFORM.findall(text):
        v = [float(x) for x in _NUMBER.findall(args)]
        if name == "matrix" and len(v) == 6:
            t = tuple(v)
        elif name == "translate" and v:
            t = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0)
        elif name == "scale" and v:
            t = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == "rotate" and v:
            r = math.radians(v[0])
            cos, sin = math.cos(r), math.sin(r)
            t = (cos, sin, -sin, cos, 0, 0)
            if len(v) == 3:
                t = mul(mul((1, 0, 0, 1, v[1], v[2]), t), (1, 0, 0, 1, -v[1], -v[2]))
        elif name == "skewX" and v:
            t = (1, 0, math.tan(math.radians(v[0])), 1, 0, 0)
        elif name == "skewY" and v:
            t = (1, math.tan(math.radians(v[0])), 0, 1, 0, 0)
        else:
            continue
        m = mul(m, t)
    return m


def ctm(node: Elem, *, own: bool = True) -> Matrix:
    """The matrix from `node`'s user space to the root's (its ancestors' transforms, and its own if `own`)."""
    chain = list(node.ancestors())[::-1] + ([node] if own else [])
    m = IDENTITY
    for n in chain:
        m = mul(m, parse_transform(n.attrs.get("transform")))
    return m


def apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


def fnum(value, default: float = 0.0) -> float:
    if value is None:
        return default
    found = _NUMBER.match(str(value).strip())
    return float(found.group(0)) if found else default


def points_of(text: str | None) -> list[tuple[float, float]]:
    v = [float(x) for x in _NUMBER.findall(text or "")]
    return list(zip(v[0::2], v[1::2]))


def canvas(root: Elem) -> tuple[float, float, float, float]:
    vb = root.attrs.get("viewBox")
    if vb:
        v = [float(x) for x in _NUMBER.findall(vb)]
        if len(v) == 4:
            return v[0], v[1], v[0] + v[2], v[1] + v[3]
    return 0.0, 0.0, fnum(root.attrs.get("width"), 300), fnum(root.attrs.get("height"), 150)


def mark_geometry(node: Elem):
    """('point', x, y) for circles, ('box', x0, y0, x1, y1) for rects, ('line', [(x, y)...]) for polylines; root space."""
    m = ctm(node)
    if node.tag == "circle":
        return ("point",) + apply(m, fnum(node.attrs.get("cx")), fnum(node.attrs.get("cy")))
    if node.tag == "rect":
        x, y = fnum(node.attrs.get("x")), fnum(node.attrs.get("y"))
        w, h = fnum(node.attrs.get("width")), fnum(node.attrs.get("height"))
        corners = [apply(m, a, b) for a, b in ((x, y), (x + w, y), (x, y + h), (x + w, y + h))]
        xs, ys = [c[0] for c in corners], [c[1] for c in corners]
        return ("box", min(xs), min(ys), max(xs), max(ys))
    if node.tag in ("polyline", "polygon"):
        return ("line", [apply(m, a, b) for a, b in points_of(node.attrs.get("points"))])
    if node.tag == "line":
        return ("line", [apply(m, fnum(node.attrs.get("x1")), fnum(node.attrs.get("y1"))),
                         apply(m, fnum(node.attrs.get("x2")), fnum(node.attrs.get("y2")))])
    return None


# --- axes -------------------------------------------------------------------------------------------------------

_DECIMAL = re.compile(r"^-?\d+(?:\.\d+)?$")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?Z?)?$")


def iso_seconds(text: str) -> float | None:
    m = _ISO.match(text)
    if not m:
        return None
    y, mo, d, h, mi, s, frac = m.groups()
    try:
        dt = datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(s or 0), tzinfo=timezone.utc)
    except ValueError:
        return None
    delta = dt - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return delta.days * 86400.0 + delta.seconds + (float("0." + frac) if frac else 0.0)


@dataclass
class Tick:
    x: float
    y: float
    label: str
    full: str
    node: Elem


@dataclass
class Axis:
    """A fitted axis. kind: 'linear' | 'log' | 'time' | 'band'. For continuous kinds value = a + b * pixel (in log10
    space for 'log'); for 'band', `categories` maps each full label to its pixel position."""

    name: str
    kind: str
    ticks: list[Tick]
    a: float = 0.0
    b: float = 0.0
    residual: float = 0.0           # largest misfit of a tick, as a share of the tick span
    span: float = 0.0               # tick span in value units (decades for log)
    categories: dict[str, float] = field(default_factory=dict)
    domain: tuple[float, float] | None = None   # pixel extent of the domain line, along the axis
    cross: float | None = None                  # the domain line's position across the axis (y of an x axis)

    def value(self, pixel: float) -> float:
        v = self.a + self.b * pixel
        return 10.0 ** v if self.kind == "log" else v

    def pixel(self, value: float) -> float:
        v = math.log10(value) if self.kind == "log" else value
        return (v - self.a) / self.b if self.b else float("nan")

    def tol(self) -> float:
        return TOLERANCE * self.span

    def close(self, recovered: float, source: float, tol: float | None = None) -> bool:
        tol = self.tol() if tol is None else tol
        if self.kind == "log":
            if recovered <= 0 or source <= 0:
                return False
            return abs(math.log10(recovered) - math.log10(source)) <= tol
        return abs(recovered - source) <= tol

    def category_at(self, pixel: float) -> str | None:
        if not self.categories:
            return None
        return min(self.categories, key=lambda c: abs(self.categories[c] - pixel))


def _fit(px, vals):
    n = len(px)
    mx, mv = sum(px) / n, sum(vals) / n
    sxx = sum((p - mx) ** 2 for p in px)
    if sxx == 0:
        return None
    b = sum((p - mx) * (v - mv) for p, v in zip(px, vals)) / sxx
    a = mv - b * mx
    span = (max(vals) - min(vals)) or 1.0
    res = max(abs(a + b * p - v) for p, v in zip(px, vals)) / span
    return a, b, res, span


def read_axis(group: Elem, which: str, *, band: bool = False) -> Axis:
    """Fit an axis group (`<g class="axis x|y|z">`) from its ticks. `which` picks the pixel coordinate. With `band`
    the labels are categories even when they look like numbers ("08" as an hour of the day)."""
    ticks = []
    for t in group.find("g", "tick"):
        x, y = apply(ctm(t), 0, 0)
        texts = [n for n in t.iter() if n.tag == "text"]
        label = texts[0].label() if texts else ""
        full = texts[0].full_label() if texts else ""
        ticks.append(Tick(x, y, label, full, t))
    axis = Axis(which, "band", ticks)
    for line in group.find("line", "domain"):
        g = mark_geometry(line)
        coords = [p[0] if which == "x" else p[1] for p in g[1]]
        axis.domain = (min(coords), max(coords))
        across = [p[1] if which == "x" else p[0] for p in g[1]]
        axis.cross = sum(across) / len(across)
    pix = [t.x if which == "x" else t.y for t in ticks]
    labels = [t.full for t in ticks]
    if not band and len(ticks) >= 2 and all(_DECIMAL.match(s) for s in labels):
        vals = [float(s) for s in labels]
        lin = _fit(pix, vals)
        best = ("linear", lin) if lin else None
        if all(v > 0 for v in vals):
            lg = _fit(pix, [math.log10(v) for v in vals])
            if lg and (best is None or lg[2] < best[1][2]):
                best = ("log", lg)
        if best:
            axis.kind, (axis.a, axis.b, axis.residual, axis.span) = best[0], best[1]
            return axis
    if not band and len(ticks) >= 2 and all(iso_seconds(s) is not None for s in labels):
        fit = _fit(pix, [iso_seconds(s) for s in labels])
        if fit:
            axis.kind = "time"
            axis.a, axis.b, axis.residual, axis.span = fit
            return axis
    axis.kind = "band"
    axis.categories = {t.full: (t.x if which == "x" else t.y) for t in ticks}
    return axis


def axes_of(scope: Elem, bands: tuple[str, ...] = ()) -> dict[str, Axis]:
    """The axes of a picture or panel; axes named in `bands` are read as categories (the form says so)."""
    out = {}
    for g in scope.iter():
        if g.tag == "g" and "axis" in g.classes():
            for which in ("x", "y", "z"):
                if which in g.classes() and which not in out:
                    out[which] = read_axis(g, which if which != "z" else "y", band=which in bands)
    return out


# --- marks ------------------------------------------------------------------------------------------------------

@dataclass
class SeriesMarks:
    name: str
    node: Elem
    lines: list[list[tuple[float, float]]] = field(default_factory=list)
    points: list[tuple[float, float]] = field(default_factory=list)
    boxes: list[tuple[float, float, float, float, Elem]] = field(default_factory=list)


def plot_marks(plot: Elem) -> list[SeriesMarks]:
    out = []
    for g in plot.iter():
        if g.tag != "g" or "series" not in g.classes():
            continue
        s = SeriesMarks(g.attrs.get("data-name", ""), g)
        for n in g.iter():
            if n is g:
                continue
            if n.tag == "polyline" and "series" in n.classes():
                s.lines.append(mark_geometry(n)[1])
            elif n.tag == "circle" and "mark" in n.classes():
                _, x, y = mark_geometry(n)
                s.points.append((x, y))
            elif n.tag == "rect" and "mark" in n.classes():
                _, x0, y0, x1, y1 = mark_geometry(n)
                s.boxes.append((x0, y0, x1, y1, n))
        out.append(s)
    return out


# --- carried information: recover values and compare with the source -------------------------------------------

def _finding(rule, passed, detail, measure=None):
    from . import Finding
    return Finding(rule, passed, detail, measure)


def _report(found: int, total: int, stray: int, what: str, extra=()) -> list:
    share = found / total if total else 1.0
    out = [_finding("values_recovered", found == total,
                    f"{found} of {total} {what} recovered from the picture within the stated tolerance", share),
           _finding("marks_match_source", stray == 0,
                    "every mark maps to a source value" if stray == 0 else
                    f"{stray} mark(s) map to no source value", float(stray))]
    return out + list(extra)


def _axes_ok(axes: dict, need=("x", "y")) -> list:
    out = []
    for which in need:
        axis = axes.get(which)
        if axis is None:
            out.append(_finding("axes_readable", False, f"no {which} axis with ticks"))
            continue
        if axis.kind != "band" and axis.residual > 0.001:
            out.append(_finding("axis_ticks_consistent", False,
                                f"the {which} axis's tick labels do not fit one scale (misfit "
                                f"{axis.residual:.1%} of the span)", axis.residual))
    if not out:
        out.append(_finding("axis_ticks_consistent", True, "every axis's ticks fit one scale", 0.0))
    return out


def _source_points(series: dict, xk: str, yk: str, agg: dict | None):
    pts = [(p[0], p[1]) for p in series.get("points", []) if p[0] is not None and p[1] is not None]
    if agg:
        pts = _aggregate(pts, agg.get("rule"), int(agg.get("bins", 2)))
    return [(x, y) for x, y in pts if not (xk == "log" and x <= 0) and not (yk == "log" and y <= 0)]


def _aggregate(pts, rule, bins):
    """The aggregation rule as the chart's description states it (written here from that statement)."""
    if not pts:
        return []
    lo, hi = min(p[0] for p in pts), max(p[0] for p in pts)
    groups: dict[int, list] = {}
    for x, y in pts:
        k = bins - 1 if hi == lo else min(bins - 1, int((x - lo) / (hi - lo) * bins))
        groups.setdefault(k, []).append((x, y))
    out = []
    for k in sorted(groups):
        g = groups[k]
        mx = sum(p[0] for p in g) / len(g)
        if rule == "min_max":
            out += [(mx, min(p[1] for p in g)), (mx, max(p[1] for p in g))]
        else:
            out.append((mx, sum(p[1] for p in g) / len(g)))
    return out


def _match(source, recovered, ax: Axis, ay: Axis):
    """Greedy nearest matching of value pairs within each axis's tolerance; (matched, stray)."""
    import bisect
    def key(v, axis):
        return math.log10(v) if axis.kind == "log" and v > 0 else v
    rec = sorted((key(x, ax), key(y, ay), i) for i, (x, y) in enumerate(recovered))
    xs = [r[0] for r in rec]
    used = [False] * len(rec)
    tx, ty = ax.tol(), ay.tol()
    matched = 0
    for x, y in source:
        kx, ky = key(x, ax), key(y, ay)
        lo, hi = bisect.bisect_left(xs, kx - tx), bisect.bisect_right(xs, kx + tx)
        best, best_d = None, None
        for j in range(lo, hi):
            if used[j] or abs(rec[j][1] - ky) > ty:
                continue
            d = abs(rec[j][0] - kx) / (tx or 1) + abs(rec[j][1] - ky) / (ty or 1)
            if best is None or d < best_d:
                best, best_d = j, d
        if best is not None:
            used[best] = True
            matched += 1
    return matched, used.count(False)


def carried_xy(scope: Elem, data: dict, options: dict, *, line: bool, plots_in: Elem | None = None) -> list:
    axes = axes_of(scope)
    out = _axes_ok(axes)
    if "x" not in axes or "y" not in axes or axes["x"].kind == "band" or axes["y"].kind == "band":
        return out + [_finding("values_recovered", False, "the axes cannot be fitted, so no value can be read", 0.0)]
    ax, ay = axes["x"], axes["y"]
    plots = [n for n in (plots_in or scope).iter() if n.tag == "g" and "plot" in n.classes()]
    recovered = {s.name: s for p in plots for s in plot_marks(p)}
    xk, yk = data["x"].get("scale", "linear"), data["y"].get("scale", "linear")
    found = total = stray = 0
    missing = []
    for s in data["series"]:
        src = _source_points(s, xk, yk, options.get("aggregate"))
        total += len(src)
        r = recovered.pop(s["name"], None)
        if r is None:
            missing.append(s["name"])
            continue
        pix = [p for ln in r.lines for p in ln] if line else r.points
        vals = [(ax.value(px), ay.value(py)) for px, py in pix]
        m, st = _match(src, vals, ax, ay)
        found += m
        stray += st
    for r in recovered.values():          # series groups with no source series
        stray += len(r.points) + sum(len(ln) for ln in r.lines)
    extra = [_finding("series_present", not missing, "every source series is drawn under its name" if not missing
                      else f"{len(missing)} series not found by name")]
    return out + _report(found, total, stray, "points", extra)


def carried_bar(scope: Elem, data: dict, options: dict, plots_in: Elem | None = None) -> list:
    axes = axes_of(scope, bands=("x",))
    out = _axes_ok(axes)
    ax, ay = axes.get("x"), axes.get("y")
    if ax is None or ay is None or ax.kind != "band" or ay.kind not in ("linear",):
        return out + [_finding("values_recovered", False, "the axes cannot be read as categories and values", 0.0)]
    stacked = options.get("mode") == "stacked"
    plots = [n for n in (plots_in or scope).iter() if n.tag == "g" and "plot" in n.classes()]
    recovered = {s.name: s for p in plots for s in plot_marks(p)}
    cats = data["categories"]
    labelled = [c for c in cats if c in ax.categories]
    out.append(_finding("categories_labelled", len(labelled) == len(cats),
                        f"{len(labelled)} of {len(cats)} categories carry their label on the axis"))
    pos = sorted(ax.categories.values())
    spacing = (pos[1] - pos[0]) if len(pos) > 1 else float("inf")
    tol = ay.tol()
    lo_tick = min(float(t.full) for t in ay.ticks)
    hi_tick = max(float(t.full) for t in ay.ticks)
    truncated = lo_tick > 0 + tol or hi_tick < 0 - tol
    found = total = stray = 0
    not_from_zero = 0
    for s in data["series"]:
        src = {c: v for c, v in zip(cats, s["values"]) if v is not None}
        total += len(src)
        r = recovered.pop(s["name"], None)
        if r is None:
            continue
        got: dict[str, float] = {}
        for x0, y0, x1, y1, _n in r.boxes:
            centre = (x0 + x1) / 2
            cat = ax.category_at(centre)
            if cat is None or abs(ax.categories[cat] - centre) > spacing / 2:
                stray += 1
                continue
            vt, vb = ay.value(y0), ay.value(y1)
            if stacked:
                v = vt - vb
            elif abs(vb) <= tol:
                v = vt
            elif abs(vt) <= tol:
                v = vb
            else:
                not_from_zero += 1
                v = vt
            if cat in got:
                stray += 1
                continue
            got[cat] = v
        for c, v in src.items():
            if c in got and abs(got.pop(c) - v) <= tol:
                found += 1
        stray += len(got)
    for r in recovered.values():
        stray += len(r.boxes)
    from_zero_spec = options.get("y_from_zero", True)
    extra = []
    if from_zero_spec:
        extra.append(_finding("bars_from_zero", not truncated and not_from_zero == 0,
                              "every bar starts at zero" if not truncated and not not_from_zero else
                              "the value axis is truncated: bars do not start at zero", float(not_from_zero)))
    style = [_finding("axis_truncated", False, "the value axis does not start at zero, as the specification "
                      "asks: bar lengths are not proportional to the values")] if truncated and not from_zero_spec else []
    return out + _report(found, total, stray, "bar values", extra), style


def carried_hist(scope: Elem, data: dict, options: dict) -> list:
    axes = axes_of(scope)
    out = _axes_ok(axes)
    ax, ay = axes.get("x"), axes.get("y")
    if ax is None or ay is None or ax.kind != "linear" or ay.kind != "linear":
        return out + [_finding("values_recovered", False, "the axes cannot be fitted", 0.0)]
    values = sorted(float(v) for v in data["values"])
    plots = [n for n in scope.iter() if n.tag == "g" and "plot" in n.classes()]
    bins = sorted((ax.value(b[0]), ax.value(b[2]), ay.value(b[1]) - ay.value(b[3]))
                  for p in plots for s in plot_marks(p) for b in s.boxes)
    eps = ax.tol() * 0.02
    # Adjacent bins share one edge: the next bin's left edge (x + width of one rect and x of the next differ by
    # rounding, and a value on that edge must fall in exactly one bin).
    bins = [(a, bins[i + 1][0] if i + 1 < len(bins) and abs(bins[i + 1][0] - b) <= ax.tol() else b, c)
            for i, (a, b, c) in enumerate(bins)]
    import bisect
    found = stray = 0
    counted = 0
    last_edge = max((b[1] for b in bins), default=None)
    for a, b, c in bins:
        lo = bisect.bisect_left(values, a - eps)
        hi = bisect.bisect_right(values, b + eps) if last_edge is not None and abs(b - last_edge) <= eps * 50 \
            else bisect.bisect_left(values, b - eps)
        truth = hi - lo
        if abs(c - truth) <= max(0.5, ay.tol()):
            found += 1
            counted += truth
        else:
            stray += 1
    extra = [_finding("all_values_binned", counted == len(values),
                      f"the bins hold {counted} of {len(values)} values", counted / max(1, len(values)))]
    return out + _report(found, len(bins), stray, "bin counts", extra)


def _gray(fill: str | None):
    m = re.fullmatch(r"#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})", (fill or "").strip())
    if not m:
        return None
    r, g, b = (int(x, 16) for x in m.groups())
    return (r + g + b) / 3 if max(r, g, b) - min(r, g, b) <= 3 else None


def carried_heat(scope: Elem, data: dict, options: dict) -> list:
    axes = axes_of(scope, bands=("x", "y"))
    ax, ay = axes.get("x"), axes.get("y")
    zgroup = next((g for g in scope.iter() if g.tag == "g" and g.has("axis", "z")), None)
    if ax is None or ay is None or zgroup is None:
        return [_finding("values_recovered", False, "the heatmap's axes or ramp cannot be read", 0.0)]
    pairs = []
    for t in zgroup.find("g", "tick"):
        sw = next((n for n in t.iter() if n.tag == "rect"), None)
        tx = next((n for n in t.iter() if n.tag == "text"), None)
        g = _gray(sw.attrs.get("fill") if sw is not None else None)
        if g is not None and tx is not None and _DECIMAL.match(tx.label()):
            pairs.append((g, float(tx.label())))
    fit = _fit([p[0] for p in pairs], [p[1] for p in pairs]) if len(pairs) >= 2 else None
    if fit is None:
        return [_finding("values_recovered", False, "the ramp has fewer than two readable swatches", 0.0)]
    a, b, res, span = fit
    out = [_finding("axis_ticks_consistent", res <= 0.02, f"the ramp's swatches fit one scale (misfit {res:.1%})", res)]
    tol = HEAT_TOLERANCE * span
    xc, yc = data["x"]["categories"], data["y"]["categories"]
    plots = [n for n in scope.iter() if n.tag == "g" and "plot" in n.classes()]
    got: dict[tuple[str, str], float] = {}
    stray = 0
    cells = []
    for p in plots:
        for n in p.iter():
            if n.tag == "rect" and "mark" in n.classes():
                _, x0, y0, x1, y1 = mark_geometry(n)
                g = _gray(n.attrs.get("fill"))
                cx, cy = ax.category_at((x0 + x1) / 2), ay.category_at((y0 + y1) / 2)
                if g is None or cx is None or cy is None or (cx, cy) in got:
                    stray += 1
                    continue
                got[(cx, cy)] = a + b * g
                cells.append((x0, y0, x1, y1, cx, cy))
    found = total = 0
    printed_bad = printed = 0
    truth = {}
    for r, row in enumerate(data["values"]):
        for c, v in enumerate(row):
            if v is None:
                continue
            total += 1
            truth[(xc[c], yc[r])] = v
            k = (xc[c], yc[r])
            if k in got and abs(got.pop(k) - v) <= tol:
                found += 1
    stray += len(got)
    for p in plots:
        for n in p.iter():
            if n.tag == "text" and _DECIMAL.match(n.label()):
                x, y = apply(ctm(n), fnum(n.attrs.get("x")), fnum(n.attrs.get("y")))
                for x0, y0, x1, y1, cx, cy in cells:
                    if x0 <= x <= x1 and y0 <= y <= y1:
                        printed += 1
                        text = n.label()
                        unit = 10.0 ** -(len(text.split(".")[1]) if "." in text else 0)
                        v = truth.get((cx, cy))
                        if v is None or abs(float(text) - v) > max(unit / 2, abs(v) * 5e-4) + 1e-12:
                            printed_bad += 1
                        break
    extra = [_finding("printed_values_match", printed_bad == 0, f"{printed - printed_bad} of {printed} printed "
                      "values match their cells", float(printed_bad))] if printed else []
    return out + _report(found, total, stray, "cell values", extra)


def carried_timeline(root: Elem, data: dict, options: dict) -> list:
    axes = axes_of(root, bands=("y",))
    out = _axes_ok(axes, need=("x",))
    ax, ay = axes.get("x"), axes.get("y")
    if ax is None or ax.kind == "band" or ay is None:
        return out + [_finding("values_recovered", False, "the time axis or the lanes cannot be read", 0.0)]
    lane_y = ay.categories
    ordered = sorted(lane_y.values())
    plots = [n for n in root.iter() if n.tag == "g" and "plot" in n.classes()]
    groups = {s.name: s for p in plots for s in plot_marks(p)}
    tol = ax.tol()
    found = total = stray = misplaced = 0
    labels_missing = 0
    for lane in dict.fromkeys([e["lane"] for e in data.get("events", [])] + [s["lane"] for s in data.get("spans", [])]
                              + list(data.get("lanes") or [])):
        evs = sorted(e["at"] for e in data.get("events", []) if e["lane"] == lane)
        sps = sorted((s["start"], s["end"]) for s in data.get("spans", []) if s["lane"] == lane)
        total += len(evs) + len(sps)
        g = groups.pop(lane, None)
        if g is None or lane not in lane_y:
            continue
        y = lane_y[lane]
        below = [v for v in ordered if v > y + 0.5]
        next_y = below[0] if below else float("inf")
        got_e = sorted(ax.value(px) for px, py in g.points)
        misplaced += sum(1 for px, py in g.points if abs(py - y) > 1.5)
        got_s = sorted((ax.value(b[0]), ax.value(b[2])) for b in g.boxes)
        misplaced += sum(1 for b in g.boxes if not (y < (b[1] + b[3]) / 2 < next_y))
        used = [False] * len(got_e)
        for t in evs:
            j = next((j for j, v in enumerate(got_e) if not used[j] and abs(v - t) <= tol), None)
            if j is not None:
                used[j] = True
                found += 1
        stray += used.count(False)
        used = [False] * len(got_s)
        for a, b in sps:
            j = next((j for j, (u, v) in enumerate(got_s)
                      if not used[j] and abs(u - a) <= tol and abs(v - b) <= tol), None)
            if j is not None:
                used[j] = True
                found += 1
        stray += used.count(False)
        texts = set()
        for n in g.node.iter():
            if n.tag == "text":
                texts.add(n.full_label())
            elif n.tag == "circle":
                texts.update(c.text.strip() for c in n.children if c.tag == "title")
        want = [e["label"] for e in data.get("events", []) if e["lane"] == lane] + \
               [s["label"] for s in data.get("spans", []) if s["lane"] == lane]
        labels_missing += sum(1 for w in want if w not in texts)
    for g in groups.values():
        stray += len(g.points) + len(g.boxes)
    extra = [_finding("marks_in_their_lanes", misplaced == 0, "every mark sits in its lane" if not misplaced else
                      f"{misplaced} mark(s) sit outside their lane", float(misplaced)),
             _finding("labels_present", labels_missing == 0, "every event and span carries its label" if not
                      labels_missing else f"{labels_missing} label(s) missing", float(labels_missing))]
    return out + _report(found, total, stray, "event times and span ends", extra)


def _shape_box(node: Elem):
    for c in node.children:
        g = mark_geometry(c)
        if g is None:
            continue
        if g[0] == "box":
            return g[1:]
        if g[0] == "point":
            r = fnum(c.attrs.get("r"))
            return g[1] - r, g[2] - r, g[1] + r, g[2] + r
        if g[0] == "line" and g[1]:
            xs, ys = [p[0] for p in g[1]], [p[1] for p in g[1]]
            return min(xs), min(ys), max(xs), max(ys)
    return None


def read_diagram(root: Elem):
    """Nodes [(label, box)], edges [(src index, dst index, kind, label, has_arrow)], strays (edge ends on no node)."""
    nodes = []
    for g in root.iter():
        if g.tag == "g" and "node" in g.classes():
            box = _shape_box(g)
            text = next((n for n in g.iter() if n.tag == "text"), None)
            if box is not None:
                nodes.append((text.full_label() if text is not None else "", box))
    kinds = {}
    for key in root.iter():
        if key.tag == "g" and key.has("key", "edge-kind"):
            line = next((n for n in key.iter() if n.tag in ("line", "polyline")), None)
            if line is not None:
                kinds[line.attrs.get("stroke-dasharray", "").strip()] = key.attrs.get("data-name", "")

    def nearest(pt):
        best, best_d = None, None
        for i, (_l, (x0, y0, x1, y1)) in enumerate(nodes):
            dx = max(x0 - pt[0], 0, pt[0] - x1)
            dy = max(y0 - pt[1], 0, pt[1] - y1)
            d = math.hypot(dx, dy)
            if best is None or d < best_d:
                best, best_d = i, d
        return best if best_d is not None and best_d <= 6 else None

    edges, strays = [], 0
    for g in root.iter():
        if g.tag != "g" or "edge" not in g.classes():
            continue
        line = next((n for n in g.iter() if n.tag == "polyline" and "edge" in n.classes()), None)
        if line is None:
            continue
        pts = mark_geometry(line)[1]
        if len(pts) < 2:
            strays += 1
            continue
        a, b = nearest(pts[0]), nearest(pts[-1])
        if a is None or b is None:
            strays += 1
            continue
        label = next((n.full_label() for n in g.iter() if n.tag == "text" and "edge-label" in n.classes()), None)
        arrow = any(n.tag == "path" and "arrow" in n.classes() for n in g.iter())
        kind = kinds.get(line.attrs.get("stroke-dasharray", "").strip(), "") if kinds else ""
        edges.append((a, b, kind, label, arrow))
    return nodes, edges, strays


def carried_diagram(root: Elem, form: str, data: dict) -> list:
    from collections import Counter
    nodes, edges, strays = read_diagram(root)
    labels = [n[0] for n in nodes]
    if form == "graph.tree":
        src_nodes = data["nodes"]
        by_id = {n["id"]: n["label"] for n in src_nodes}
        src_edges = [(by_id[n["parent"]], n["label"], "", None) for n in src_nodes if n.get("parent") is not None]
        directed = True
        use_kind = use_label = False
    elif form == "flow.steps":
        src_nodes = data["steps"]
        by_id = {n["id"]: n["label"] for n in src_nodes}
        links = data.get("links")
        if links is None:
            links = [{"from": a["id"], "to": b["id"]} for a, b in zip(src_nodes, src_nodes[1:])]
        src_edges = [(by_id[k["from"]], by_id[k["to"]], "", k.get("label")) for k in links]
        directed, use_kind, use_label = True, False, True
    else:
        src_nodes = data["nodes"]
        by_id = {n["id"]: n["label"] for n in src_nodes}
        src_edges = [(by_id[e["from"]], by_id[e["to"]], e.get("kind", ""), e.get("label")) for e in data["edges"]]
        directed = bool(data.get("directed", True))
        use_kind = any(e.get("kind") for e in data["edges"])
        use_label = any(e.get("label") for e in data["edges"])

    def key(a, b, kind, label):
        pair = (a, b) if directed else tuple(sorted((a, b)))
        return pair + ((kind or "",) if use_kind else ()) + ((label or "",) if use_label else ())
    want_nodes = Counter(n["label"] for n in src_nodes)
    got_nodes = Counter(labels)
    want_edges = Counter(key(*e) for e in src_edges)
    got_edges = Counter(key(labels[a], labels[b], k, lab) for a, b, k, lab, _arrow in edges)
    n_found = sum((want_nodes & got_nodes).values())
    e_found = sum((want_edges & got_edges).values())
    stray = sum((got_nodes - want_nodes).values()) + sum((got_edges - want_edges).values()) + strays
    overlaps = 0
    for i in range(len(nodes)):
        a = nodes[i][1]
        for j in range(i + 1, len(nodes)):
            b = nodes[j][1]
            if a[0] < b[2] - 1 and b[0] < a[2] - 1 and a[1] < b[3] - 1 and b[1] < a[3] - 1:
                overlaps += 1
    # A tree reads top to bottom (its lines run from parent down to child) and needs no arrowheads.
    missing_arrows = sum(1 for e in edges if not e[4]) if directed and form != "graph.tree" else 0
    extra = [_finding("nodes_recovered", n_found == sum(want_nodes.values()),
                      f"{n_found} of {sum(want_nodes.values())} nodes found by label"),
             _finding("edges_recovered", e_found == sum(want_edges.values()),
                      f"{e_found} of {sum(want_edges.values())} edges found from their line ends"),
             _finding("nodes_apart", overlaps == 0, "no two nodes overlap" if not overlaps else
                      f"{overlaps} pair(s) of nodes overlap", float(overlaps)),
             _finding("arrows_present", missing_arrows == 0, "every directed edge has its arrowhead" if not
                      missing_arrows else f"{missing_arrows} directed edge(s) without an arrowhead")]
    total = sum(want_nodes.values()) + sum(want_edges.values())
    return _report(n_found + e_found, total, stray, "nodes and edges", extra)


def carried_animation(root: Elem, data: dict, options: dict) -> list:
    """Each frame's plot read through the shared axes and compared with that frame's data; the static picture (no
    animation, or reduced motion) must be the last frame alone."""
    frames = [g for g in root.iter() if g.tag == "g" and "frame" in g.classes()]
    src = data.get("frames", [])
    form = data.get("form")
    found = total = stray = 0
    other = []
    for g, f in zip(frames, src):
        if form == "chart.bar":
            sub, _style = carried_bar(root, f["data"], options, plots_in=g)
            what = sum(1 for s in f["data"]["series"] for v in s["values"] if v is not None)
        else:
            sub = carried_xy(root, f["data"], options, line=form == "chart.line", plots_in=g)
            what = sum(len(_source_points(s, f["data"]["x"].get("scale", "linear"), f["data"]["y"].get(
                "scale", "linear"), None)) for s in f["data"]["series"])
        total += what
        for fd in sub:
            if fd.rule == "values_recovered":
                found += round((fd.measure or 0) * what)
            elif fd.rule == "marks_match_source":
                stray += int(fd.measure or 0)
            elif not fd.passed:
                other.append(fd)
    for f in src[len(frames):]:
        total += 1
    hidden = [g.attrs.get("opacity", "").strip() in ("0", "0.0") for g in frames]
    static_ok = bool(frames) and all(hidden[:-1]) and not hidden[-1]
    css = "".join(n.text for n in root.iter() if n.tag == "style")
    flat = re.sub(r"\s+", "", css).lower()
    reduced = bool(re.search(r"@media\(prefers-reduced-motion:reduce\)\{[^{}]*\.frame\{animation:none", flat))
    extra = [_finding("frames_present", len(frames) == len(src), f"{len(frames)} of {len(src)} frames drawn"),
             _finding("static_last_frame", static_ok, "without animation the last frame alone is shown"
                      if static_ok else "the still picture is not the last frame alone"),
             _finding("reduced_motion_honoured", reduced, "reduced motion turns the animation off" if reduced else
                      "no reduced-motion rule turns the animation off")]
    seen = set()
    for fd in other:
        if fd.rule not in seen:
            seen.add(fd.rule)
            extra.append(fd)
    return _report(found, total, stray, "values across all frames", extra)


def carried(root: Elem, form: str, data: dict, options: dict) -> tuple[list, list]:
    """(technical, style) findings of carried information for a chart form (empty when it is not a chart)."""
    if form in ("chart.line", "chart.scatter"):
        return carried_xy(root, data, options, line=form == "chart.line"), []
    if form == "chart.bar":
        return carried_bar(root, data, options)
    if form == "chart.histogram":
        return carried_hist(root, data, options), []
    if form == "chart.heatmap":
        return carried_heat(root, data, options), []
    if form == "chart.small_multiples":
        panels = [n for n in root.iter() if n.tag == "g" and "panel" in n.classes()]
        by_name = {p.attrs.get("data-name", ""): p for p in panels}
        found = total = stray = 0
        bad_axes = []
        missing = 0
        for s in data["series"]:
            p = by_name.pop(s["name"], None)
            n = len(_source_points(s, data["x"].get("scale", "linear"), data["y"].get("scale", "linear"), None))
            total += n
            if p is None:
                missing += 1
                continue
            sub = carried_xy(p, {**data, "series": [s]}, {}, line=True)
            for f in sub:
                if f.rule == "values_recovered":
                    found += round((f.measure or 0) * n)
                elif f.rule == "marks_match_source":
                    stray += int(f.measure or 0)
                elif f.rule in ("axis_ticks_consistent", "axes_readable") and not f.passed:
                    bad_axes.append(f.detail)
        extra = [_finding("series_present", missing == 0 and not by_name,
                          "one panel per source series" if missing == 0 and not by_name else
                          f"{missing} series without a panel, {len(by_name)} panel(s) without a series"),
                 _finding("axis_ticks_consistent", not bad_axes, "every panel's ticks fit one scale" if not bad_axes
                          else bad_axes[0])]
        return _report(found, total, stray, "points", extra), []
    if form == "timeline":
        return carried_timeline(root, data, options), []
    if form == "animation.svg":
        return carried_animation(root, data, options), []
    if form in ("graph.nodelink", "graph.tree", "flow.steps"):
        return carried_diagram(root, form, data), []
    return [], []


__all__ = ["Axis", "Elem", "HEAT_TOLERANCE", "Parsed", "SeriesMarks", "TOLERANCE", "apply", "axes_of", "canvas",
           "ctm", "fnum", "iso_seconds", "mark_geometry", "parse", "parse_transform", "plot_marks", "points_of",
           "read_axis", "carried"]
